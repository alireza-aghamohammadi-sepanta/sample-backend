"""Pydantic schemas for todo items."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.asset import AssetResponse

MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 1024
MAX_ASSETS_PER_TODO = 10


class TodoBase(BaseModel):
    """Base schema for todo items containing common attributes."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    due_date: datetime | None = None


class TodoCreate(TodoBase):
    """Payload for creating a new todo item."""

    list_id: uuid.UUID | None = None
    due_date: datetime | None = None
    asset_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_ASSETS_PER_TODO,
    )

    @field_validator("asset_ids", mode="after")
    @classmethod
    def _deduplicate_asset_ids(
        cls, v: list[uuid.UUID] | None
    ) -> list[uuid.UUID] | None:
        if v is None:
            return None
        return list(dict.fromkeys(v))


class TodoUpdate(BaseModel):
    """Payload for updating an existing todo item."""

    list_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    due_date: datetime | None = None
    is_completed: bool | None = None
    asset_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_ASSETS_PER_TODO,
    )

    @field_validator("asset_ids", mode="after")
    @classmethod
    def _deduplicate_asset_ids(
        cls, v: list[uuid.UUID] | None
    ) -> list[uuid.UUID] | None:
        if v is None:
            return None
        return list(dict.fromkeys(v))


class TodoRead(TodoBase):
    """Public representation of a todo item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    list_id: uuid.UUID
    is_completed: bool
    due_date: datetime | None = None
    created_at: datetime
    updated_at: datetime
    assets: list[AssetResponse] = Field(default_factory=list)
