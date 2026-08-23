"""Request and response schemas of the asset upload endpoints."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


# The object key is ``users/{user_id}/{media_type}/{uuid}.{extension}``, so the
# media type doubles as a path segment: keep it to the two kinds of media the
# product accepts.
class MediaType(StrEnum):
    """Kind of media a client asks to upload."""

    IMAGE = "image"
    VIDEO = "video"


# Long enough for ``jpeg``/``webm`` and every extension in between, restricted
# to characters that are safe inside a Cloud Storage object name.
MAX_FILE_EXTENSION_LENGTH = 10
FILE_EXTENSION_PATTERN = r"^[a-z0-9]+$"

# Mirrors the ``gcs_path`` column of the ``assets`` table.
MAX_GCS_PATH_LENGTH = 1024


class SignedURLRequest(BaseModel):
    """Payload asking for a signed URL to upload one file with."""

    media_type: MediaType
    file_extension: str = Field(
        min_length=1,
        max_length=MAX_FILE_EXTENSION_LENGTH,
        pattern=FILE_EXTENSION_PATTERN,
    )

    @field_validator("file_extension", mode="before")
    @classmethod
    def _normalize_file_extension(cls, value: object) -> object:
        """Accept ``.PNG`` as well as ``png``; store the bare lowercase form."""
        if isinstance(value, str):
            return value.strip().lstrip(".").lower()
        return value


class SignedURLResponse(BaseModel):
    """The URL to PUT the file to, plus the object key it will land on."""

    upload_url: str
    gcs_path: str


class AssetConfirmRequest(BaseModel):
    """Payload confirming that an upload finished at ``gcs_path``."""

    gcs_path: str = Field(min_length=1, max_length=MAX_GCS_PATH_LENGTH)


class AssetResponse(BaseModel):
    """Public view of a stored asset, built straight from an ``Asset`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    gcs_path: str
    public_url: str
    # A plain string, not ``MediaType``: rows written by earlier or later
    # versions must never make a read fail.
    media_type: str
    created_at: datetime
