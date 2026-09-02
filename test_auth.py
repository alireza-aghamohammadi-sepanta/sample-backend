"""Integration tests for the registration endpoint.

The scenarios that touch the database run against a real PostgreSQL instance
whose URL is taken from ``TEST_DATABASE_URL`` (or ``DATABASE_URL``); the schema
is created by running the Alembic migrations, so the tests also prove that the
migration matches the model. When no URL is configured those tests are skipped,
which keeps the committed suite runnable without any service. The validation
scenarios never reach the database and therefore always run.
"""

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.config import get_settings
from app.db import Base
from app.db.session import get_db
from app.models import User
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


def test_login_route_is_registered():
    paths = app.openapi()["paths"]

    assert "/login" in paths
    assert "post" in paths["/login"]


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


# --- login endpoints -------------------------------------------------------


@pytest.fixture
def sqlite_session():
    """A session on a throwaway database holding the whole model schema."""
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def auth_client(sqlite_session):
    with client_using(sqlite_session) as test_client:
        yield test_client


@pytest.fixture
def registered_user(sqlite_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("correct-horse-battery"),
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    return user


# --- AC-1: successful login ------------------------------------------------


def test_login_returns_200_with_a_token(auth_client, registered_user):
    response = auth_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_token_is_signed_for_the_user(auth_client, registered_user):
    response = auth_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    token = response.json()["access_token"]

    payload = jwt.decode(token, SECRET, algorithms=[security.ALGORITHM])
    assert payload["sub"] == str(registered_user.id)
    assert uuid.UUID(payload["sub"]) == registered_user.id
    assert payload["exp"] > payload["iat"]


def test_login_normalises_the_email(auth_client, registered_user):
    response = auth_client.post(
        "/login",
        json={"email": "  Alice@Example.COM  ", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    payload = jwt.decode(token, SECRET, algorithms=[security.ALGORITHM])
    assert payload["sub"] == str(registered_user.id)


def test_login_response_never_carries_the_password(auth_client, registered_user):
    response = auth_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    assert "correct-horse-battery" not in response.text
    assert "password" not in response.json()


# --- AC-2: incorrect password rejection ------------------------------------


def test_login_with_incorrect_password_returns_401(auth_client, registered_user):
    response = auth_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "wrong-password-123"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert_no_storage_details(response)


# --- AC-3: non-existent email rejection -----------------------------------


def test_login_with_non_existent_email_returns_401(auth_client, sqlite_session):
    response = auth_client.post(
        "/login",
        json={"email": "nonexistent@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert_no_storage_details(response)


def test_login_error_detail_is_identical_for_wrong_password_and_missing_user(
    auth_client, registered_user
):
    wrong_password_response = auth_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "wrong-password-123"},
    )
    missing_user_response = auth_client.post(
        "/login",
        json={"email": "nonexistent@example.com", "password": "correct-horse-battery"},
    )

    assert wrong_password_response.status_code == 401
    assert missing_user_response.status_code == 401
    assert wrong_password_response.json() == missing_user_response.json() == {"detail": "Invalid email or password"}


# --- AC-4: malformed payload rejection -------------------------------------


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
def test_login_invalid_payloads_return_422(offline_client, payload):
    response = offline_client.post("/login", json=payload)

    assert response.status_code == 422
    assert_no_storage_details(response)


# --- PostgreSQL integration tests for login -------------------------------


@requires_database
def test_postgres_login_successful(client, db_session):
    # First sign up the user through the real endpoint
    signup_res = client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert signup_res.status_code == 201

    login_res = client.post(
        "/login",
        json={"email": "Alice@example.com", "password": "correct-horse-battery"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    payload = jwt.decode(token, SECRET, algorithms=[security.ALGORITHM])
    user = db_session.execute(sa.select(User)).scalar_one()
    assert payload["sub"] == str(user.id)


@requires_database
def test_postgres_login_wrong_password(client, db_session):
    signup_res = client.post(
        "/signup",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert signup_res.status_code == 201

    login_res = client.post(
        "/login",
        json={"email": "alice@example.com", "password": "wrong-password-123"},
    )
    assert login_res.status_code == 401
    assert login_res.json()["detail"] == "Invalid email or password"


@requires_database
def test_postgres_login_non_existent_user(client, db_session):
    login_res = client.post(
        "/login",
        json={"email": "nobody@example.com", "password": "correct-horse-battery"},
    )
    assert login_res.status_code == 401
    assert login_res.json()["detail"] == "Invalid email or password"
