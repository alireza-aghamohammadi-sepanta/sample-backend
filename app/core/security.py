"""Security utilities: password hashing and JSON Web Tokens.

Passwords are hashed with Argon2 (salted per hash by passlib). Access tokens
are signed with the ``jwt_secret`` resolved by :mod:`app.core.config`, which is
read at call time so a rotated secret is picked up without a restart of the
module import machinery.
"""

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from app.core.config import get_settings

ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES_ENV = "ACCESS_TOKEN_EXPIRE_MINUTES"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

INVALID_CREDENTIALS_DETAIL = "Could not validate credentials"

# ``auto_error=False`` so a missing header reaches the dependency and is
# answered with the same 401 as a bad one: whether a token was sent at all is
# not something an unauthenticated caller needs to learn.
bearer_scheme = HTTPBearer(auto_error=False)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def get_password_hash(password: str) -> str:
    """Return an Argon2 hash of ``password``."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return whether ``plain_password`` matches ``hashed_password``."""
    return pwd_context.verify(plain_password, hashed_password)


def generate_password_reset_token() -> str:
    """Generate a secure random token for password reset."""
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    """Return a SHA-256 hash of a password reset token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_CREDENTIALS_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> uuid.UUID:
    """Return the id of the user the bearer token was issued to.

    Every failure mode - no header, a token this service did not sign, an
    expired token, a subject that is not a user id - is answered with the same
    401 and the same message, so nothing about the token is disclosed. The
    subject is parsed as a UUID before it is handed to callers: it ends up in
    Cloud Storage object keys, and only a real user id may go there.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _unauthorized() from None

    subject = payload.get("sub")
    if not subject:
        raise _unauthorized()

    try:
        return uuid.UUID(str(subject))
    except ValueError:
        raise _unauthorized() from None
