from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import get_settings
from app.core import security


ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
)

SECRET = "unit-test-jwt-secret-that-is-long-enough-for-hs256"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:instance")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _decode(token: str, secret: str = SECRET) -> dict:
    return jwt.decode(token, secret, algorithms=[security.ALGORITHM])


# --- password hashing ------------------------------------------------------


def test_get_password_hash_uses_argon2():
    hashed = security.get_password_hash("s3cret-password")

    assert hashed.startswith("$argon2")


def test_get_password_hash_is_not_the_plaintext():
    password = "s3cret-password"

    assert security.get_password_hash(password) != password


def test_get_password_hash_is_salted():
    password = "s3cret-password"

    assert security.get_password_hash(password) != security.get_password_hash(password)


def test_verify_password_accepts_the_correct_password():
    hashed = security.get_password_hash("s3cret-password")

    assert security.verify_password("s3cret-password", hashed) is True


def test_verify_password_rejects_a_wrong_password():
    hashed = security.get_password_hash("s3cret-password")

    assert security.verify_password("wrong-password", hashed) is False


# --- access tokens ---------------------------------------------------------


def test_create_access_token_payload_contains_subject_exp_and_iat():
    token = security.create_access_token("user-id-42")

    payload = _decode(token)

    assert payload["sub"] == "user-id-42"
    assert "exp" in payload
    assert "iat" in payload
    assert payload["exp"] > payload["iat"]


def test_create_access_token_uses_hs256_and_the_configured_secret():
    token = security.create_access_token("user-id-42")

    header = jwt.get_unverified_header(token)

    assert header["alg"] == "HS256"
    assert security.ALGORITHM == "HS256"
    assert _decode(token)["sub"] == "user-id-42"


def test_create_access_token_honours_the_default_expiry(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "45")

    before = datetime.now(timezone.utc)
    payload = _decode(security.create_access_token("user-id-42"))
    after = datetime.now(timezone.utc)

    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    assert before + timedelta(minutes=45) - timedelta(seconds=5) <= expires_at
    assert expires_at <= after + timedelta(minutes=45) + timedelta(seconds=5)


def test_create_access_token_honours_an_explicit_expires_delta():
    before = datetime.now(timezone.utc)
    payload = _decode(
        security.create_access_token("user-id-42", expires_delta=timedelta(minutes=5))
    )
    after = datetime.now(timezone.utc)

    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    assert before + timedelta(minutes=5) - timedelta(seconds=5) <= expires_at
    assert expires_at <= after + timedelta(minutes=5) + timedelta(seconds=5)


def test_create_access_token_accepts_a_non_string_subject():
    payload = _decode(security.create_access_token(42))

    assert payload["sub"] == "42"


def test_access_token_cannot_be_decoded_with_a_wrong_secret():
    token = security.create_access_token("user-id-42")

    with pytest.raises(jwt.InvalidSignatureError):
        _decode(token, secret="another-secret-that-is-long-enough-for-hs256")


def test_expired_access_token_is_rejected():
    token = security.create_access_token(
        "user-id-42", expires_delta=timedelta(minutes=-1)
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        _decode(token)


def test_create_access_token_reads_the_secret_at_call_time(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "rotated-secret-that-is-long-enough-for-hs256")
    get_settings.cache_clear()

    token = security.create_access_token("user-id-42")

    assert _decode(token, secret="rotated-secret-that-is-long-enough-for-hs256")["sub"] == "user-id-42"


def test_decode_access_token_returns_the_payload():
    token = security.create_access_token("user-id-42")

    payload = security.decode_access_token(token)

    assert payload["sub"] == "user-id-42"


def test_decode_access_token_rejects_a_tampered_token():
    token = security.create_access_token("user-id-42")
    tampered = token[:-2] + ("ab" if not token.endswith("ab") else "cd")

    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(tampered)
