"""Authentication endpoints.

Only registration lives here for now. The handler never logs nor echoes the
plaintext password, and every storage failure is translated into a plain
message so no SQL or driver detail reaches the client.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate

router = APIRouter(tags=["auth"])

EMAIL_TAKEN_DETAIL = "Email already registered"


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
