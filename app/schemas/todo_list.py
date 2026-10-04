"""Pydantic schemas for todo lists."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_LIST_NAME_LENGTH = 255


class TodoListBase(BaseModel):
    """Base schema for todo lists."""

    name: str = Field(min_length=1, max_length=MAX_LIST_NAME_LENGTH)


class TodoListCreate(TodoListBase):
    """Payload for creating a custom todo list."""


class TodoListUpdate(TodoListBase):
    """Payload for updating (renaming) a todo list."""


class TodoListRead(TodoListBase):
    """Public representation of a todo list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    is_default: bool
    created_at: datetime
    updated_at: datetime
