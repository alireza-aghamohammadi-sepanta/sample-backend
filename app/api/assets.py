"""Asset upload endpoints.

Uploads take two calls: the client asks for a signed URL, ``PUT``s the file
straight to Cloud Storage, then confirms the upload so its metadata is stored.

Both handlers derive ownership from the ``sub`` claim of the caller's token and
never from anything in the request body, so one user can neither write into
another user's prefix nor claim a file uploaded there. Confirmation also checks
that the object really is in the bucket, so no row is ever written for a file
that was never uploaded.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.db.session import get_db
from app.models.asset import Asset
from app.schemas.asset import (
    AssetConfirmRequest,
    AssetResponse,
    MediaType,
    SignedURLRequest,
    SignedURLResponse,
)
from app.services.storage import StorageService, get_storage_service

router = APIRouter(prefix="/assets", tags=["assets"])

# One message for every rejected key, whatever is wrong with it: whether a
# given object exists under someone else's prefix is not something a caller
# needs to learn.
FORBIDDEN_PATH_DETAIL = "gcs_path is not an upload path of the authenticated user"
FILE_NOT_FOUND_DETAIL = "Uploaded file not found"

# ``users/{user_id}/{media_type}/{name}.{extension}``, the shape
# ``StorageService.build_object_path`` hands out.
OBJECT_PATH_PREFIX = "users"
OBJECT_PATH_SEGMENTS = 4


def _owned_media_type(gcs_path: str, user_id: uuid.UUID) -> str:
    """Return the media type of ``gcs_path``, an upload key of ``user_id``.

    Anything that is not a key this service could have issued to this very
    user - a foreign prefix, a traversal, an unknown media type - is refused
    before the bucket or the database is touched.
    """
    segments = gcs_path.split("/")
    if len(segments) != OBJECT_PATH_SEGMENTS:
        raise _forbidden_path()

    prefix, path_user_id, media_type, filename = segments
    if prefix != OBJECT_PATH_PREFIX or path_user_id != str(user_id) or not filename:
        raise _forbidden_path()

    try:
        return MediaType(media_type).value
    except ValueError:
        raise _forbidden_path() from None


def _forbidden_path() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_PATH_DETAIL
    )


@router.post(
    "/signed-url", response_model=SignedURLResponse, status_code=status.HTTP_200_OK
)
def request_signed_url(
    payload: SignedURLRequest,
    user_id: uuid.UUID = Depends(current_user),
    storage: StorageService = Depends(get_storage_service),
) -> SignedURLResponse:
    """Return a short lived URL the caller can upload one file to."""
    gcs_path = storage.build_object_path(
        user_id=user_id,
        media_type=payload.media_type,
        file_extension=payload.file_extension,
    )
    return SignedURLResponse(
        upload_url=storage.generate_signed_url(gcs_path), gcs_path=gcs_path
    )


@router.post("/confirm", response_model=AssetResponse, status_code=status.HTTP_200_OK)
def confirm_upload(
    payload: AssetConfirmRequest,
    user_id: uuid.UUID = Depends(current_user),
    storage: StorageService = Depends(get_storage_service),
    db: Session = Depends(get_db),
) -> Asset:
    """Record the metadata of a file the caller finished uploading.

    The key is checked twice before anything is written: it has to be an
    upload key of the caller, and the object has to be in the bucket.
    """
    media_type = _owned_media_type(payload.gcs_path, user_id)

    if not storage.file_exists(payload.gcs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=FILE_NOT_FOUND_DETAIL
        )

    asset = Asset(
        user_id=user_id,
        gcs_path=payload.gcs_path,
        public_url=storage.public_url(payload.gcs_path),
        media_type=media_type,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset
