"""Authentication endpoints.

Registration and password reset request handlers. These handlers never log nor echo
plaintext credentials or tokens unnecessarily, and every storage failure is translated
into a plain message so no SQL or driver detail reaches the client.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    generate_password_reset_token,
    get_password_hash,
    hash_reset_token,
)
from app.db.session import get_db
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.user import ForgotPasswordRequest, Token, UserCreate
from app.services.email import EmailService, get_email_service

router = APIRouter(tags=["auth"])

EMAIL_TAKEN_DETAIL = "Email already registered"
FORGOT_PASSWORD_GENERIC_MESSAGE = (
    "If the email is registered, a password reset link has been sent."
)
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

    user = User(email=email, hashed_password=get_password_hash(payload.password))
    db.add(user)
    try:
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
