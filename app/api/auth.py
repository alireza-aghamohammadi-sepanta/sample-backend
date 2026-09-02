"""Authentication endpoints.

Registration and login endpoints live here. Handlers never log nor echo the
plaintext password, and every storage failure is translated into a plain
message so no SQL or driver detail reaches the client.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate

router = APIRouter(tags=["auth"])

EMAIL_TAKEN_DETAIL = "Email already registered"
INVALID_CREDENTIALS_DETAIL = "Invalid email or password"


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


@router.post("/login", response_model=Token)
def login(payload: UserCreate, db: Session = Depends(get_db)) -> Token:
    """Authenticate with email and password and return an access token."""
    email = payload.normalized_email

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
        )

    return Token(access_token=create_access_token(user.id), token_type="bearer")
