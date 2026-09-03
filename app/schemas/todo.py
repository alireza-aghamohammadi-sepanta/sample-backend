"""Pydantic schemas for todo items."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 1024


class TodoBase(BaseModel):
    """Base schema for todo items containing common attributes."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)


class TodoCreate(TodoBase):
    """Payload for creating a new todo item."""


class TodoUpdate(BaseModel):
    """Payload for updating an existing todo item."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    is_completed: bool | None = None


class TodoRead(TodoBase):
    """Public representation of a todo item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    is_completed: bool
    created_at: datetime
    updated_at: datetime
