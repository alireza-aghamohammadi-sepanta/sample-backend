"""Security utilities: password hashing and JSON Web Tokens.

Passwords are hashed with Argon2 (salted per hash by passlib). Access tokens
are signed with the ``jwt_secret`` resolved by :mod:`app.core.config`, which is
read at call time so a rotated secret is picked up without a restart of the
module import machinery.
"""

import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES_ENV = "ACCESS_TOKEN_EXPIRE_MINUTES"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def get_password_hash(password: str) -> str:
    """Return an Argon2 hash of ``password``."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return whether ``plain_password`` matches ``hashed_password``."""
    return pwd_context.verify(plain_password, hashed_password)


def access_token_expire_minutes() -> int:
    """Default token lifetime in minutes, overridable via the environment."""
    raw = os.environ.get(ACCESS_TOKEN_EXPIRE_MINUTES_ENV)
    if not raw:
        return ACCESS_TOKEN_EXPIRE_MINUTES
    try:
        return int(raw)
    except ValueError:
        return ACCESS_TOKEN_EXPIRE_MINUTES


def create_access_token(subject, expires_delta: timedelta | None = None) -> str:
    """Create a signed access token for ``subject``.

    The payload holds the subject (``sub``), the issue time (``iat``) and the
    expiration time (``exp``). ``expires_delta`` overrides the default
    lifetime.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=access_token_expire_minutes())

    issued_at = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": issued_at + expires_delta,
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify ``token``, returning its payload.

    Raises a :class:`jwt.PyJWTError` subclass when the token is invalid or has
    expired.
    """
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
