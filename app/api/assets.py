"""Asset upload endpoints.

Only the signed URL handler lives here for now. The object key is built from
the ``sub`` claim of the caller's token and never from anything in the request
body, so one user cannot write into another user's prefix.
"""

import uuid

from fastapi import APIRouter, Depends, status

from app.core.security import current_user
from app.schemas.asset import SignedURLRequest, SignedURLResponse
from app.services.storage import StorageService, get_storage_service

router = APIRouter(prefix="/assets", tags=["assets"])


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
