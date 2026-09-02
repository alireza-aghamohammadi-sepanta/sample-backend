"""Integration tests for the registration endpoint.

The scenarios that touch the database run against a real PostgreSQL instance
whose URL is taken from ``TEST_DATABASE_URL`` (or ``DATABASE_URL``); the schema
is created by running the Alembic migrations, so the tests also prove that the
migration matches the model. When no URL is configured those tests are skipped,
which keeps the committed suite runnable without any service. The validation
scenarios never reach the database and therefore always run.
"""

import hashlib
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import jwt
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db.session import get_db
from app.models import User
from app.schemas.user import ForgotPasswordRequest, ResetPasswordRequest
from main import app

ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
)

SECRET = "integration-test-jwt-secret-that-is-long-enough-for-hs256"

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

requires_database = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set TEST_DATABASE_URL (or DATABASE_URL) to run against PostgreSQL",
)

# Fragments that would betray the storage layer if they ever leaked into an
# error response.
LEAKY_FRAGMENTS = (
    "psycopg",
    "pg8000",
    "sqlalchemy",
    "IntegrityError",
    "duplicate key",
    "INSERT INTO",
    "ix_users_email",
    "Traceback",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:instance")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine():
    """Engine on the local database, with the migrations applied."""
    engine = sa.create_engine(DATABASE_URL)

    config = Config(str(Path(__file__).parent / "alembic.ini"))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = DATABASE_URL
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(engine):
    """A session on an empty ``users`` table, also used by the endpoint."""
    with Session(engine) as session:
        session.execute(sa.delete(User))
        session.commit()
        yield session
        session.rollback()
        session.execute(sa.delete(User))
        session.commit()


@contextmanager
def client_using(session):
    """A client whose ``get_db`` dependency yields ``session``."""

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_session):
    with client_using(db_session) as test_client:
        yield test_client


@pytest.fixture
def offline_client():
    """Client for scenarios rejected before the endpoint touches the database."""
    with client_using(None) as test_client:
        yield test_client


def assert_no_storage_details(response):
    body = response.text
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in body, f"response leaks {fragment!r}: {body}"


# --- routing ---------------------------------------------------------------


def test_signup_route_is_registered():
    paths = app.openapi()["paths"]

    assert "/signup" in paths
    assert "post" in paths["/signup"]


def test_existing_routes_are_untouched(offline_client):
    assert offline_client.get("/").json() == {"message": "Hello World"}
    assert offline_client.get("/healthz").json() == {"status": "ok"}


# --- AC-1: successful signup ----------------------------------------------


@requires_database
def test_signup_returns_201_with_a_token(client):
    response = client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


@requires_database
def test_signup_token_is_signed_for_the_new_user(client, db_session):
    response = client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    token = response.json()["access_token"]

    payload = jwt.decode(token, SECRET, algorithms=[security.ALGORITHM])
    user = db_session.execute(sa.select(User)).scalar_one()

    assert payload["sub"] == str(user.id)
    assert uuid.UUID(payload["sub"]) == user.id
    assert payload["exp"] > payload["iat"]


@requires_database
def test_signup_persists_the_user_row(client, db_session):
    client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    user = db_session.execute(sa.select(User)).scalar_one()

    assert user.email == "alice@example.com"
    assert user.created_at is not None


@requires_database
def test_signup_normalises_the_email(client, db_session):
    response = client.post(
        "/signup",
        json={"email": "Alice@Example.COM", "password": "correct-horse-battery"},
    )

    assert response.status_code == 201
    user = db_session.execute(sa.select(User)).scalar_one()
    assert user.email == "alice@example.com"


@requires_database
def test_signup_response_never_carries_the_password(client):
    response = client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 201
    assert "correct-horse-battery" not in response.text
    assert "password" not in response.json()


# --- security: storage of the password ------------------------------------


@requires_database
def test_password_is_stored_as_an_argon2_hash(client, db_session):
    client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    user = db_session.execute(sa.select(User)).scalar_one()

    assert user.hashed_password.startswith("$argon2")
    assert user.hashed_password != "correct-horse-battery"
    assert security.verify_password("correct-horse-battery", user.hashed_password)


@requires_database
def test_plaintext_password_is_nowhere_in_the_users_table(client, db_session):
    client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    row = db_session.execute(sa.text("SELECT * FROM users")).mappings().one()

    assert "correct-horse-battery" not in " ".join(str(v) for v in row.values())


# --- AC-2: duplicate email -------------------------------------------------


@requires_database
def test_duplicate_email_returns_409(client, db_session):
    payload = {"email": "alice@example.com", "password": "correct-horse-battery"}
    assert client.post("/signup", json=payload).status_code == 201

    response = client.post("/signup", json=payload)

    assert response.status_code == 409
    assert db_session.execute(sa.select(sa.func.count()).select_from(User)).scalar_one() == 1


@requires_database
def test_duplicate_email_ignoring_case_returns_409(client, db_session):
    assert (
        client.post(
            "/signup",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        ).status_code
        == 201
    )

    response = client.post(
        "/signup",
        json={"email": "ALICE@example.com", "password": "another-password-1"},
    )

    assert response.status_code == 409
    assert db_session.execute(sa.select(sa.func.count()).select_from(User)).scalar_one() == 1


@requires_database
def test_conflict_response_hides_storage_details(client):
    payload = {"email": "alice@example.com", "password": "correct-horse-battery"}
    client.post("/signup", json=payload)

    response = client.post("/signup", json=payload)

    assert_no_storage_details(response)
    assert response.json()["detail"] == "Email already registered"


class LosingRaceSession:
    """Session whose insert loses the race against a concurrent signup.

    The lookup finds no user, but the unique constraint rejects the insert,
    exactly like two simultaneous signups for the same address.
    """

    def __init__(self):
        self.rolled_back = False

    def scalar(self, *args, **kwargs):
        return None

    def add(self, instance):
        self.added = instance

    def commit(self):
        raise IntegrityError(
            "INSERT INTO users (id, email, hashed_password) VALUES (...)",
            {},
            Exception(
                'duplicate key value violates unique constraint "ix_users_email"'
            ),
        )

    def refresh(self, instance):  # pragma: no cover - never reached
        raise AssertionError("refresh after a failed commit")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_unique_constraint_race_is_reported_as_409():
    """A row inserted between the lookup and the insert still yields a 409."""
    session = LosingRaceSession()
    with client_using(session) as test_client:
        response = test_client.post(
            "/signup",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"
    assert session.rolled_back is True
    assert_no_storage_details(response)


# --- AC-3: invalid input ---------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"email": "not-an-email", "password": "correct-horse-battery"},
            id="malformed-email",
        ),
        pytest.param(
            {"email": "", "password": "correct-horse-battery"}, id="empty-email"
        ),
        pytest.param({"password": "correct-horse-battery"}, id="missing-email"),
        pytest.param({"email": "alice@example.com"}, id="missing-password"),
        pytest.param({}, id="empty-body"),
        pytest.param(
            {"email": "alice@example.com", "password": "short"}, id="short-password"
        ),
    ],
)
def test_invalid_payloads_return_422(offline_client, payload):
    response = offline_client.post("/signup", json=payload)

    assert response.status_code == 422
    assert_no_storage_details(response)


# --- password reset security utilities ------------------------------------


def test_generate_password_reset_token_returns_non_empty_string():
    token = security.generate_password_reset_token()
    assert isinstance(token, str)
    assert len(token) >= 32


def test_generate_password_reset_token_is_random():
    t1 = security.generate_password_reset_token()
    t2 = security.generate_password_reset_token()
    assert t1 != t2


def test_hash_reset_token_returns_sha256_hex_digest():
    token = "test-reset-token-value"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert security.hash_reset_token(token) == expected
    assert len(security.hash_reset_token(token)) == 64


def test_hash_reset_token_is_deterministic():
    token = "deterministic-token"
    assert security.hash_reset_token(token) == security.hash_reset_token(token)


def test_hash_reset_token_differs_for_different_inputs():
    assert security.hash_reset_token("token-a") != security.hash_reset_token("token-b")


# --- forgot / reset password schema constraints ---------------------------


def test_forgot_password_request_valid_email():
    req = ForgotPasswordRequest(email="user@example.com")
    assert req.email == "user@example.com"
    assert req.normalized_email == "user@example.com"


def test_forgot_password_request_normalizes_email():
    req = ForgotPasswordRequest(email="  User.Name@Example.COM  ")
    assert req.normalized_email == "user.name@example.com"


@pytest.mark.parametrize("invalid_email", ["not-an-email", "", "user@", "@domain.com"])
def test_forgot_password_request_invalid_email(invalid_email):
    with pytest.raises(ValidationError):
        ForgotPasswordRequest(email=invalid_email)


def test_forgot_password_request_missing_email():
    with pytest.raises(ValidationError):
        ForgotPasswordRequest.model_validate({})


def test_reset_password_request_valid():
    req = ResetPasswordRequest(token="token-abc", new_password="validpassword123")
    assert req.token == "token-abc"
    assert req.new_password == "validpassword123"
    assert req.password == "validpassword123"


def test_reset_password_request_password_alias():
    req = ResetPasswordRequest(token="token-abc", password="validpassword123")
    assert req.token == "token-abc"
    assert req.new_password == "validpassword123"
    assert req.password == "validpassword123"


def test_reset_password_request_password_length_bounds():
    req_min = ResetPasswordRequest(token="tok", new_password="a" * 8)
    assert len(req_min.new_password) == 8

    req_max = ResetPasswordRequest(token="tok", new_password="a" * 128)
    assert len(req_max.new_password) == 128


def test_reset_password_request_rejects_short_password():
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="tok", new_password="short")


def test_reset_password_request_rejects_long_password():
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="tok", new_password="a" * 129)


def test_reset_password_request_missing_token():
    with pytest.raises(ValidationError):
        ResetPasswordRequest.model_validate({"new_password": "validpassword123"})


def test_reset_password_request_empty_token():
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="", new_password="validpassword123")

