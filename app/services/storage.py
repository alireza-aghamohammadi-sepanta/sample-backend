"""Cloud Storage access: signed upload URLs and object verification.

Clients never talk to the bucket through the API: they ask for a short lived
V4 signed URL and ``PUT`` the file straight to Cloud Storage. The signature
covers an ``x-goog-content-length-range`` header, so Cloud Storage itself
rejects anything larger than :data:`MAX_UPLOAD_BYTES`, and a client cannot lift
the limit without invalidating the signature.

The service takes its bucket and its storage client as constructor arguments,
so it can be built with doubles. One service - and one client - is built per
application by the lifespan, and :func:`get_storage_service` hands requests
that very instance, which keeps this module importable (and the test suite
runnable) without GCP credentials.
"""

import json
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, Request, status

from app.core.config import ConfigError, get_settings
from app.schemas.asset import MediaType

# 500 MB, the largest upload the product accepts.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024

# Extension header understood by Cloud Storage; its value is ``min,max`` in
# bytes. Signed as part of the URL, hence enforced server side.
CONTENT_LENGTH_RANGE_HEADER = "x-goog-content-length-range"

SIGNED_URL_VERSION = "v4"
SIGNED_URL_METHOD = "PUT"
SIGNED_URL_EXPIRATION = timedelta(minutes=15)

PUBLIC_URL_TEMPLATE = "https://storage.googleapis.com/{bucket}/{path}"

# ``users/{user_id}/{media_type}/{uuid}.{extension}``: the user prefix keeps
# objects greppable per owner, the random name makes collisions impossible.
OBJECT_PATH_TEMPLATE = "users/{user_id}/{media_type}/{name}.{extension}"
OBJECT_PATH_PREFIX = "users"
OBJECT_PATH_SEGMENTS = 4

# Answered when an application serves a request without a storage service; the
# deployment, not the caller, is what has to be fixed.
STORAGE_UNAVAILABLE_DETAIL = "Cloud Storage is not available"


def _build_storage_client() -> Any:
    """Create a Cloud Storage client.

    Imported lazily so the SDK is only required (and only authenticates) when
    Cloud Storage is actually used. ``GCS_SERVICE_ACCOUNT_INFO`` holds a
    service account key as JSON and is what signs URLs outside GCP; without it
    the ambient (attached) credentials are used.
    """
    from google.cloud import storage

    service_account_info = get_settings().gcs_service_account_info
    if service_account_info:
        try:
            info = json.loads(service_account_info)
        except ValueError as exc:
            raise ConfigError(
                "Invalid configuration: GCS_SERVICE_ACCOUNT_INFO must be a "
                "service account key in JSON format"
            ) from exc
        return storage.Client.from_service_account_info(info)

    return storage.Client()


class StorageService:
    """Signed URL issuing and object lookups for one bucket."""

    def __init__(self, bucket_name: str, client: Any):
        #: Name of the bucket every object of this service lives in.
        self.bucket_name = bucket_name
        #: Cloud Storage client every request of this service goes through.
        self.client = client

    def _blob(self, gcs_path: str) -> Any:
        return self.client.bucket(self.bucket_name).blob(gcs_path)

    def build_object_path(
        self,
        user_id: uuid.UUID | str,
        media_type: MediaType | str,
        file_extension: str,
    ) -> str:
        """Return a fresh object key for one upload of ``user_id``."""
        return OBJECT_PATH_TEMPLATE.format(
            user_id=user_id,
            media_type=MediaType(media_type).value,
            name=uuid.uuid4(),
            extension=file_extension,
        )

    def get_owned_media_type(
        self, gcs_path: str, user_id: uuid.UUID | str
    ) -> str | None:
        """Return the media type of ``gcs_path`` when ``user_id`` owns it.

        Anything that is not a key this service could have issued to this very
        user - a foreign prefix, a traversal, an unknown media type - yields
        ``None``, so a caller can refuse it before the bucket or the database
        is touched.
        """
        segments = gcs_path.split("/")
        if len(segments) != OBJECT_PATH_SEGMENTS:
            return None

        prefix, path_user_id, media_type, filename = segments
        if prefix != OBJECT_PATH_PREFIX or path_user_id != str(user_id) or not filename:
            return None

        try:
            return MediaType(media_type).value
        except ValueError:
            return None

    def generate_signed_url(
        self,
        gcs_path: str,
        content_type: str | None = None,
        expiration: timedelta = SIGNED_URL_EXPIRATION,
    ) -> str:
        """Return a signed URL to upload ``gcs_path`` with.

        The signature binds the request to ``PUT``, to ``content_type`` when
        one is given, and to an upload of at most :data:`MAX_UPLOAD_BYTES`
        bytes.
        """
        return self._blob(gcs_path).generate_signed_url(
            version=SIGNED_URL_VERSION,
            method=SIGNED_URL_METHOD,
            expiration=expiration,
            content_type=content_type,
            headers={CONTENT_LENGTH_RANGE_HEADER: f"0,{MAX_UPLOAD_BYTES}"},
        )

    def file_exists(self, gcs_path: str) -> bool:
        """Return whether ``gcs_path`` is an existing object of the bucket."""
        return bool(self._blob(gcs_path).exists())

    def public_url(self, gcs_path: str) -> str:
        """Return the public URL an uploaded object is served from."""
        return PUBLIC_URL_TEMPLATE.format(bucket=self.bucket_name, path=gcs_path)


def _bucket_name_from_settings() -> str:
    """Return the configured bucket name, or fail with a clear message."""
    bucket_name = get_settings().gcs_bucket_name
    if not bucket_name:
        raise ConfigError(
            "Missing configuration: set the GCS_BUCKET_NAME environment "
            "variable to the Cloud Storage bucket holding uploaded media"
        )
    return bucket_name


def build_storage_service() -> StorageService:
    """Return a new storage service owning a new client, from the settings."""
    return StorageService(
        bucket_name=_bucket_name_from_settings(), client=_build_storage_client()
    )


def get_storage_service(request: Request) -> StorageService:
    """FastAPI dependency returning the storage service of the application.

    The service is the one the lifespan published on ``app.state``. When there
    is none - no bucket configured, no credentials to sign with - the request
    fails with a clear server error instead of a fresh client being built,
    unnoticed, for every single call.
    """
    service = getattr(request.app.state, "storage_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=STORAGE_UNAVAILABLE_DETAIL,
        )
    return service
