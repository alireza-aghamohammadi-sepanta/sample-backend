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
from datetime import datetime, timedelta, timezone
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
from app.models import PasswordResetToken, User
from app.schemas.user import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserLogin,
)
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


# --- forgot password endpoint tests (POST /auth/forgot-password) -----------


def test_forgot_password_route_is_registered():
    paths = app.openapi()["paths"]
    assert "/auth/forgot-password" in paths
    assert "post" in paths["/auth/forgot-password"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"email": "not-an-email"}, id="malformed-email"),
        pytest.param({"email": ""}, id="empty-email"),
        pytest.param({"email": "user@"}, id="trailing-at-email"),
        pytest.param({"email": "@domain.com"}, id="missing-local-part"),
        pytest.param({}, id="missing-email"),
        pytest.param({"email": 12345}, id="numeric-email"),
    ],
)
def test_forgot_password_invalid_email_returns_422(offline_client, payload):
    response = offline_client.post("/auth/forgot-password", json=payload)
    assert response.status_code == 422
    assert_no_storage_details(response)


@requires_database
def test_forgot_password_registered_user_persists_token_and_dispatches_email(
    client, db_session
):
    from app.services.email import EmailService, get_email_service

    email_service = EmailService()
    app.dependency_overrides[get_email_service] = lambda: email_service

    try:
        # Create a registered user
        user = User(
            email="alice@example.com",
            hashed_password=security.get_password_hash("password123"),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        # Call forgot-password endpoint
        response = client.post(
            "/auth/forgot-password",
            json={"email": "alice@example.com"},
        )

        assert response.status_code == 200
        assert "message" in response.json()
        assert_no_storage_details(response)

        # Check that a token row was persisted
        tokens = list(
            db_session.execute(
                sa.select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
            ).scalars()
        )
        assert len(tokens) == 1
        token_record = tokens[0]
        assert token_record.user_id == user.id
        assert len(token_record.token_hash) == 64
        assert token_record.used_at is None

        # Check token expiration (~15 minutes in future)
        now = datetime.now(timezone.utc)
        assert token_record.expires_at > now
        remaining_seconds = (token_record.expires_at - now).total_seconds()
        assert 13 * 60 <= remaining_seconds <= 16 * 60

        # Check email dispatch
        assert len(email_service.sent_emails) == 1
        sent = email_service.sent_emails[0]
        assert sent["to_email"] == "alice@example.com"
        assert security.hash_reset_token(sent["reset_token"]) == token_record.token_hash
        assert sent["reset_token"] in sent["reset_url"]
    finally:
        app.dependency_overrides.pop(get_email_service, None)


@requires_database
def test_forgot_password_unregistered_email_returns_identical_200(client, db_session):
    from app.services.email import EmailService, get_email_service

    email_service = EmailService()
    app.dependency_overrides[get_email_service] = lambda: email_service

    try:
        # Registered user response
        user = User(
            email="registered@example.com",
            hashed_password=security.get_password_hash("password123"),
        )
        db_session.add(user)
        db_session.commit()

        registered_resp = client.post(
            "/auth/forgot-password",
            json={"email": "registered@example.com"},
        )
        assert registered_resp.status_code == 200

        # Reset email service counter
        email_service.sent_emails.clear()

        # Unregistered user response
        unregistered_resp = client.post(
            "/auth/forgot-password",
            json={"email": "unregistered@example.com"},
        )
        assert unregistered_resp.status_code == 200
        assert unregistered_resp.json() == registered_resp.json()
        assert_no_storage_details(unregistered_resp)

        # Ensure no reset token was created for unregistered email
        token_count = db_session.execute(
            sa.select(sa.func.count()).select_from(PasswordResetToken)
        ).scalar_one()
        # Exactly 1 token exists (the one from registered user)
        assert token_count == 1

        # Ensure no email was sent for unregistered user
        assert len(email_service.sent_emails) == 0
    finally:
        app.dependency_overrides.pop(get_email_service, None)


@requires_database
def test_forgot_password_normalizes_email_case(client, db_session):
    user = User(
        email="bob@example.com",
        hashed_password=security.get_password_hash("password123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/auth/forgot-password",
        json={"email": "  Bob@Example.COM  "},
    )
    assert response.status_code == 200

    token_record = db_session.execute(
        sa.select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    ).scalar_one()
    assert token_record is not None


# --- EmailService unit tests ----------------------------------------------


def test_email_service_send_password_reset_email():
    from app.services.email import EmailService

    service = EmailService()
    service.send_password_reset_email("user@example.com", "reset-token-123")

    assert len(service.sent_emails) == 1
    sent = service.sent_emails[0]
    assert sent["to_email"] == "user@example.com"
    assert sent["reset_token"] == "reset-token-123"
    assert "/reset-password?token=reset-token-123" in sent["reset_url"]
    assert sent["subject"] == "Password Reset Request"


def test_email_service_send_password_reset_email_custom_url():
    from app.services.email import EmailService

    service = EmailService()
    service.send_password_reset_email(
        "user@example.com",
        "token-456",
        reset_url="https://app.example.com/reset?token=token-456",
    )

    assert len(service.sent_emails) == 1
    sent = service.sent_emails[0]
    assert sent["reset_url"] == "https://app.example.com/reset?token=token-456"


def test_email_service_send_email():
    from app.services.email import EmailService

    service = EmailService()
    service.send_email("user@example.com", "Welcome", "Welcome to our app!")

    assert len(service.sent_emails) == 1
    sent = service.sent_emails[0]
    assert sent["to_email"] == "user@example.com"
    assert sent["subject"] == "Welcome"
    assert sent["body"] == "Welcome to our app!"


def test_get_email_service_and_build():
    from starlette.datastructures import State
    from starlette.requests import Request

    from app.services.email import (
        EmailService,
        build_email_service,
        get_email_service,
    )

    built = build_email_service()
    assert isinstance(built, EmailService)

    fallback = get_email_service()
    assert isinstance(fallback, EmailService)

    class FakeApp:
        def __init__(self, **state):
            self.state = State(state)

    custom_service = EmailService()
    req = Request({"type": "http", "app": FakeApp(email_service=custom_service)})
    assert get_email_service(req) is custom_service


# --- reset password endpoint tests (POST /auth/reset-password) -------------


def test_reset_password_route_is_registered():
    paths = app.openapi()["paths"]
    assert "/auth/reset-password" in paths
    assert "post" in paths["/auth/reset-password"]


@requires_database
def test_reset_password_successful_with_valid_token(client, db_session):
    # AC-1: valid token resets password, invalidates token, and returns bearer access token
    user = User(
        email="alice@example.com",
        hashed_password=security.get_password_hash("old-password-123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db_session.add(token_record)
    db_session.commit()

    response = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "new_password": "new-secure-password-456"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["access_token"]

    # Verify access token payload
    payload = jwt.decode(body["access_token"], SECRET, algorithms=[security.ALGORITHM])
    assert payload["sub"] == str(user.id)
    assert uuid.UUID(payload["sub"]) == user.id

    # Verify token is now marked as used
    db_session.refresh(token_record)
    assert token_record.used_at is not None
    assert token_record.used_at <= datetime.now(timezone.utc)

    # Verify user password hash was updated
    db_session.refresh(user)
    assert security.verify_password("new-secure-password-456", user.hashed_password)
    assert not security.verify_password("old-password-123", user.hashed_password)
    assert_no_storage_details(response)


@requires_database
def test_reset_password_with_password_alias_successful(client, db_session):
    user = User(
        email="bob@example.com",
        hashed_password=security.get_password_hash("old-password-123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db_session.add(token_record)
    db_session.commit()

    response = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "password": "brand-new-password-789"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body

    db_session.refresh(user)
    assert security.verify_password("brand-new-password-789", user.hashed_password)


@requires_database
def test_reset_password_expired_token_returns_400(client, db_session):
    # AC-2: expired token rejection with HTTP 400
    user = User(
        email="expired@example.com",
        hashed_password=security.get_password_hash("old-password-123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(token_record)
    db_session.commit()

    response = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "new_password": "new-secure-password"},
    )

    assert response.status_code == 400
    assert_no_storage_details(response)

    # Verify password was NOT changed and token was NOT marked used
    db_session.refresh(user)
    assert security.verify_password("old-password-123", user.hashed_password)
    db_session.refresh(token_record)
    assert token_record.used_at is None


@requires_database
def test_reset_password_invalid_or_tampered_token_returns_400(client, db_session):
    # AC-3: invalid/tampered token rejection with HTTP 400
    user = User(
        email="tamper@example.com",
        hashed_password=security.get_password_hash("old-password-123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/auth/reset-password",
        json={"token": "nonexistent-or-tampered-token", "new_password": "new-secure-password"},
    )

    assert response.status_code == 400
    assert_no_storage_details(response)

    # Verify user's password remained unchanged
    db_session.refresh(user)
    assert security.verify_password("old-password-123", user.hashed_password)


@requires_database
def test_reset_password_reused_token_returns_400(client, db_session):
    # AC-4: reused token rejection with HTTP 400
    user = User(
        email="reuse@example.com",
        hashed_password=security.get_password_hash("old-password-123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        used_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(token_record)
    db_session.commit()

    response = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "new_password": "new-secure-password"},
    )

    assert response.status_code == 400
    assert_no_storage_details(response)

    # Verify user's password remained unchanged
    db_session.refresh(user)
    assert security.verify_password("old-password-123", user.hashed_password)


@requires_database
def test_reset_password_consecutive_reset_fails_on_second_attempt(client, db_session):
    # AC-4: single-use enforcement: second attempt with same token returns HTTP 400
    user = User(
        email="singleuse@example.com",
        hashed_password=security.get_password_hash("password-version-1"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db_session.add(token_record)
    db_session.commit()

    # First attempt: succeeds
    resp1 = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "new_password": "password-version-2"},
    )
    assert resp1.status_code == 200

    # Second attempt with same token: rejected with 400
    resp2 = client.post(
        "/auth/reset-password",
        json={"token": raw_token, "new_password": "password-version-3"},
    )
    assert resp2.status_code == 400
    assert_no_storage_details(resp2)

    # Password is password-version-2, not password-version-3
    db_session.refresh(user)
    assert security.verify_password("password-version-2", user.hashed_password)
    assert not security.verify_password("password-version-3", user.hashed_password)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"token": "valid-token-str", "new_password": "short"}, id="too-short-new_password"),
        pytest.param({"token": "valid-token-str", "new_password": "a" * 129}, id="too-long-new_password"),
        pytest.param({"token": "valid-token-str", "new_password": ""}, id="empty-new_password"),
        pytest.param({"token": "valid-token-str", "password": "short"}, id="too-short-password-alias"),
        pytest.param({"token": "valid-token-str", "password": "a" * 129}, id="too-long-password-alias"),
        pytest.param({"token": "valid-token-str"}, id="missing-password"),
        pytest.param({"new_password": "valid-password-123"}, id="missing-token"),
        pytest.param({"token": "", "new_password": "valid-password-123"}, id="empty-token"),
        pytest.param({}, id="empty-payload"),
    ],
)
def test_reset_password_invalid_payload_returns_422(offline_client, payload):
    # AC-5: invalid password length outside 8-128 characters / invalid schema returns HTTP 422
    response = offline_client.post("/auth/reset-password", json=payload)
    assert response.status_code == 422
    assert_no_storage_details(response)


@requires_database
def test_reset_password_no_sensitive_data_leaked_in_logs_or_response(
    client, db_session, caplog
):
    import logging

    user = User(
        email="secrettest@example.com",
        hashed_password=security.get_password_hash("old-secret-pass"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    raw_token = security.generate_password_reset_token()
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db_session.add(token_record)
    db_session.commit()

    secret_new_password = "super-secret-new-pass-99"

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/auth/reset-password",
            json={"token": raw_token, "new_password": secret_new_password},
        )

    assert response.status_code == 200
    assert secret_new_password not in response.text
    assert raw_token not in response.text
    for record in caplog.records:
        assert secret_new_password not in record.getMessage()
        assert raw_token not in record.getMessage()


# --- User Login endpoint and schema tests ----------------------------------


class MockLoginSession:
    """Mock session for login unit tests."""

    def __init__(self, users: list[User] | None = None):
        self._users = {u.email: u for u in (users or [])}

    def scalar(self, stmt):
        params = stmt.compile().params
        for val in params.values():
            if isinstance(val, str) and val in self._users:
                return self._users[val]
        return None


def test_login_route_is_registered():
    paths = app.openapi()["paths"]
    assert "/auth/login" in paths
    assert "post" in paths["/auth/login"]


def test_user_login_schema_valid():
    schema = UserLogin(email="alice@example.com", password="my-secret-password")
    assert schema.email == "alice@example.com"
    assert schema.password == "my-secret-password"
    assert schema.normalized_email == "alice@example.com"


def test_user_login_schema_normalizes_email():
    schema = UserLogin(email="  Alice@Example.COM  ", password="my-secret-password")
    assert schema.normalized_email == "alice@example.com"


def test_user_login_schema_invalid_email():
    with pytest.raises(ValidationError):
        UserLogin(email="not-an-email", password="my-secret-password")


def test_user_login_schema_missing_password():
    with pytest.raises(ValidationError):
        UserLogin(email="alice@example.com")


def test_login_successful_with_jwt_return():
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("correct-horse-battery"),
    )
    session = MockLoginSession([user])
    with client_using(session) as test_client:
        response = test_client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body

    payload = jwt.decode(body["access_token"], SECRET, algorithms=[security.ALGORITHM])
    assert payload["sub"] == str(user.id)
    assert payload["exp"] > payload["iat"]


def test_login_alias_route_successful():
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("correct-horse-battery"),
    )
    session = MockLoginSession([user])
    with client_using(session) as test_client:
        response = test_client.post(
            "/login",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body


def test_login_case_insensitive_email_matching():
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("correct-horse-battery"),
    )
    session = MockLoginSession([user])
    with client_using(session) as test_client:
        response = test_client.post(
            "/auth/login",
            json={"email": "  ALICE@Example.COM  ", "password": "correct-horse-battery"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body


def test_login_invalid_password_returns_401():
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("correct-horse-battery"),
    )
    session = MockLoginSession([user])
    with client_using(session) as test_client:
        response = test_client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert_no_storage_details(response)


def test_login_non_existent_user_returns_401():
    session = MockLoginSession([])
    with client_using(session) as test_client:
        response = test_client.post(
            "/auth/login",
            json={"email": "nonexistent@example.com", "password": "any-password"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert_no_storage_details(response)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"email": "not-an-email", "password": "password123"}, id="malformed-email"),
        pytest.param({"email": "", "password": "password123"}, id="empty-email"),
        pytest.param({"password": "password123"}, id="missing-email"),
        pytest.param({"email": "alice@example.com"}, id="missing-password"),
        pytest.param({}, id="empty-body"),
    ],
)
def test_login_invalid_payload_returns_422(offline_client, payload):
    response = offline_client.post("/auth/login", json=payload)
    assert response.status_code == 422
    assert_no_storage_details(response)


def test_login_no_sensitive_data_leaked():
    user = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password=security.get_password_hash("super-secret-password"),
    )
    session = MockLoginSession([user])
    with client_using(session) as test_client:
        response = test_client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "super-secret-password"},
        )

    assert response.status_code == 200
    assert "super-secret-password" not in response.text
    assert_no_storage_details(response)


@requires_database
def test_db_login_successful(client, db_session):
    user = User(
        email="dbuser@example.com",
        hashed_password=security.get_password_hash("db-secret-password"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/auth/login",
        json={"email": "dbuser@example.com", "password": "db-secret-password"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body

    payload = jwt.decode(body["access_token"], SECRET, algorithms=[security.ALGORITHM])
    assert payload["sub"] == str(user.id)


@requires_database
def test_db_login_invalid_password(client, db_session):
    user = User(
        email="dbuser2@example.com",
        hashed_password=security.get_password_hash("db-secret-password"),
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "dbuser2@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}


@requires_database
def test_db_login_non_existent_user(client, db_session):
    response = client.post(
        "/auth/login",
        json={"email": "nosuchuser@example.com", "password": "password"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}


@requires_database
def test_db_login_case_insensitive_email(client, db_session):
    user = User(
        email="dbuser3@example.com",
        hashed_password=security.get_password_hash("db-secret-password"),
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "  DBUSER3@EXAMPLE.COM  ", "password": "db-secret-password"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()







