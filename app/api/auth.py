"""Authentication endpoints.

Registration and password reset request handlers. These handlers never log nor echo
plaintext credentials or tokens unnecessarily, and every storage failure is translated
into a plain message so no SQL or driver detail reaches the client.
"""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    generate_password_reset_token,
    get_password_hash,
    hash_reset_token,
    verify_password,
)
from app.db.session import get_db
from app.models.password_reset_token import PasswordResetToken
from app.models.todo_list import TodoList
from app.models.user import User
from app.schemas.user import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    Token,
    UserCreate,
    UserLogin,
)
from app.services.email import EmailService, get_email_service

router = APIRouter(tags=["auth"])

EMAIL_TAKEN_DETAIL = "Email already registered"
FORGOT_PASSWORD_GENERIC_MESSAGE = (
    "If the email is registered, a password reset link has been sent."
)
INVALID_CREDENTIALS_DETAIL = "Invalid credentials"
INVALID_RESET_TOKEN_DETAIL = "Invalid or expired reset token"
RESET_TOKEN_EXPIRE_MINUTES = 15


@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
def signup(payload: UserCreate, db: Session = Depends(get_db)) -> Token:
    """Register an account and return an access token for it."""
    email = payload.normalized_email

    existing = db.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN_DETAIL
        )

    user = User(
        email=email,
        hashed_password=get_password_hash(payload.password),
    )
    db.add(user)
    try:
        if hasattr(db, "flush"):
            db.flush()
        default_list = TodoList(
            user_id=user.id,
            name="Inbox",
            is_default=True,
        )
        db.add(default_list)
        db.commit()
    except IntegrityError:
        # Another request registered the same address between the lookup and
        # the insert; the unique index is the authority. The driver message is
        # deliberately dropped.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN_DETAIL
        ) from None

    db.refresh(user)
    return Token(access_token=create_access_token(subject=str(user.id)))


@router.post("/auth/login", response_model=Token, status_code=status.HTTP_200_OK)
@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK, include_in_schema=False)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> Token:
    """Authenticate a user and return an access token."""
    email = payload.normalized_email
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
        )

    return Token(access_token=create_access_token(subject=str(user.id)))


@router.post("/auth/forgot-password", status_code=status.HTTP_200_OK)
@router.post("/forgot-password", status_code=status.HTTP_200_OK, include_in_schema=False)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
) -> dict[str, str]:
    """Request a password reset link.

    Always returns HTTP 200 with a generic message to prevent user enumeration.
    When the email exists in the database, generates a 15-minute reset token,
    records its SHA-256 hash, and dispatches the reset email.
    """
    email = payload.normalized_email
    user = db.scalar(select(User).where(User.email == email))

    if user is not None:
        raw_token = generate_password_reset_token()
        token_hash = hash_reset_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=RESET_TOKEN_EXPIRE_MINUTES
        )

        reset_token_record = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(reset_token_record)
        db.commit()

        email_service.send_password_reset_email(
            to_email=user.email,
            reset_token=raw_token,
        )

    return {"message": FORGOT_PASSWORD_GENERIC_MESSAGE}


@router.post("/auth/reset-password", response_model=Token, status_code=status.HTTP_200_OK)
@router.post("/reset-password", response_model=Token, status_code=status.HTTP_200_OK, include_in_schema=False)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> Token:
    """Reset user password using a valid, unexpired, single-use token.

    Verifies the SHA-256 hash of the token against stored tokens, updates the
    user's password with Argon2, marks the token as used, and returns an access token.
    """
    token_hash = hash_reset_token(payload.token)
    reset_token_record = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )

    if reset_token_record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RESET_TOKEN_DETAIL,
        )

    now = datetime.now(timezone.utc)
    expires_at = reset_token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now or reset_token_record.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RESET_TOKEN_DETAIL,
        )

    user = db.scalar(select(User).where(User.id == reset_token_record.user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RESET_TOKEN_DETAIL,
        )

    user.hashed_password = get_password_hash(payload.new_password)
    reset_token_record.used_at = now
    db.commit()
    db.refresh(user)

    return Token(access_token=create_access_token(subject=str(user.id)))

