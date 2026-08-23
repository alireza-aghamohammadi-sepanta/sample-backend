"""Request and response schemas of the user facing endpoints."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Argon2 accepts any length; a floor of 8 characters keeps trivially guessable
# passwords out without imposing a policy the frontend cannot explain.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class UserCreate(BaseModel):
    """Payload of a registration request."""

    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @property
    def normalized_email(self) -> str:
        """The address as it is stored: lowercased, without surrounding space."""
        return self.email.strip().lower()


class UserRead(BaseModel):
    """Public view of an account: never carries the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr


class Token(BaseModel):
    """A signed access token, as returned by signup and login."""

    access_token: str
    token_type: str = "bearer"
