"""Unit tests for the Cloud Storage service and its dependency provider.

The GCS client is replaced by fakes, so the suite never authenticates and
never reaches the network. The provider is exercised against requests carrying
a hand made application state, which is what the lifespan fills in production.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from starlette.datastructures import State
from starlette.requests import Request

from app.core.config import ConfigError, get_settings
from app.schemas.asset import MediaType
from app.services import storage as storage_module
from app.services.storage import (
    CONTENT_LENGTH_RANGE_HEADER,
    MAX_UPLOAD_BYTES,
    StorageService,
    build_storage_service,
    get_storage_service,
)


ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "GCS_BUCKET_NAME",
    "GCS_SERVICE_ACCOUNT_INFO",
)

BUCKET = "sample-media"


class FakeBlob:
    """Stand-in for google.cloud.storage.blob.Blob."""

    def __init__(self, name: str, exists: bool = True):
        self.name = name
        self._exists = exists
        self.signed_url_calls: list[dict] = []
        self.exists_calls = 0

    def generate_signed_url(self, **kwargs):
        self.signed_url_calls.append(kwargs)
        return f"https://signed.example/{self.name}"

    def exists(self):
        self.exists_calls += 1
        return self._exists


class FakeBucket:
    """Stand-in for google.cloud.storage.bucket.Bucket."""

    def __init__(self, name: str, blobs: dict[str, FakeBlob] | None = None):
        self.name = name
        self.blobs = blobs if blobs is not None else {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob(name))


class FakeStorageClient:
    """Stand-in for google.cloud.storage.Client."""

    instances: list["FakeStorageClient"] = []

    def __init__(self, buckets: dict[str, FakeBucket] | None = None):
        self.buckets = buckets if buckets is not None else {}
        self.requested_buckets: list[str] = []
        FakeStorageClient.instances.append(self)

    def bucket(self, name: str) -> FakeBucket:
        self.requested_buckets.append(name)
        return self.buckets.setdefault(name, FakeBucket(name))


class FakeApp:
    """Stand-in for the ASGI application: only its state matters here."""

    def __init__(self, **state):
        self.state = State(state)


def request_with_state(**state) -> Request:
    """A request served by an application whose state holds ``state``."""
    return Request({"type": "http", "app": FakeApp(**state)})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    FakeStorageClient.instances = []
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> FakeStorageClient:
    return FakeStorageClient()


@pytest.fixture
def service(client) -> StorageService:
    return StorageService(bucket_name=BUCKET, client=client)


@pytest.fixture
def configured(monkeypatch):
    """Settings and a fake client factory the provider can be built from."""
    monkeypatch.setenv("JWT_SECRET", "jwt")
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:db")
    monkeypatch.setattr(
        storage_module, "_build_storage_client", lambda: FakeStorageClient()
    )


def test_max_upload_bytes_is_500_mb():
    assert MAX_UPLOAD_BYTES == 500 * 1024 * 1024


def test_generated_url_carries_the_500_mb_content_length_range(service, client):
    service.generate_signed_url("users/u/image/f.png", content_type="image/png")

    blob = client.buckets[BUCKET].blobs["users/u/image/f.png"]
    kwargs, = blob.signed_url_calls
    assert kwargs["headers"][CONTENT_LENGTH_RANGE_HEADER] == f"0,{MAX_UPLOAD_BYTES}"
    assert CONTENT_LENGTH_RANGE_HEADER == "x-goog-content-length-range"


def test_generated_url_is_a_v4_put_url_for_the_requested_object(service, client):
    url = service.generate_signed_url("users/u/video/f.webm", content_type="video/webm")

    assert url == "https://signed.example/users/u/video/f.webm"
    assert client.requested_buckets == [BUCKET]
    kwargs, = client.buckets[BUCKET].blobs["users/u/video/f.webm"].signed_url_calls
    assert kwargs["version"] == "v4"
    assert kwargs["method"] == "PUT"
    assert kwargs["content_type"] == "video/webm"


def test_signed_url_expiration_defaults_and_can_be_overridden(service, client):
    service.generate_signed_url("a.png")
    service.generate_signed_url("b.png", expiration=timedelta(minutes=5))

    blobs = client.buckets[BUCKET].blobs
    default_kwargs, = blobs["a.png"].signed_url_calls
    override_kwargs, = blobs["b.png"].signed_url_calls
    assert default_kwargs["expiration"] == storage_module.SIGNED_URL_EXPIRATION
    assert override_kwargs["expiration"] == timedelta(minutes=5)


def test_content_type_is_omitted_when_unknown(service, client):
    service.generate_signed_url("a.png")

    kwargs, = client.buckets[BUCKET].blobs["a.png"].signed_url_calls
    assert kwargs["content_type"] is None


def test_file_exists_reports_present_objects(service, client):
    bucket = FakeBucket(BUCKET, {"there.png": FakeBlob("there.png", exists=True)})
    client.buckets[BUCKET] = bucket

    assert service.file_exists("there.png") is True
    assert bucket.blobs["there.png"].exists_calls == 1


def test_file_exists_reports_missing_objects(service, client):
    client.buckets[BUCKET] = FakeBucket(
        BUCKET, {"gone.png": FakeBlob("gone.png", exists=False)}
    )

    assert service.file_exists("gone.png") is False


def test_build_object_path_follows_the_agreed_pattern(service):
    user_id = uuid.uuid4()

    path = service.build_object_path(user_id, MediaType.IMAGE, "png")

    prefix, remainder = path.split(f"/{MediaType.IMAGE.value}/")
    assert prefix == f"users/{user_id}"
    name, extension = remainder.rsplit(".", 1)
    assert extension == "png"
    assert uuid.UUID(name)


def test_build_object_path_is_unique_per_call(service):
    user_id = uuid.uuid4()

    first = service.build_object_path(user_id, MediaType.VIDEO, "webm")
    second = service.build_object_path(user_id, MediaType.VIDEO, "webm")

    assert first != second


def test_public_url_points_at_the_bucket(service):
    assert service.public_url("users/u/image/f.png") == (
        f"https://storage.googleapis.com/{BUCKET}/users/u/image/f.png"
    )


def test_get_owned_media_type_accepts_an_upload_key_of_the_owner(service):
    user_id = uuid.uuid4()

    for media_type in MediaType:
        path = service.build_object_path(user_id, media_type, "bin")

        assert service.get_owned_media_type(path, user_id) == media_type.value


def test_get_owned_media_type_accepts_the_owner_id_as_a_string(service):
    user_id = uuid.uuid4()
    path = service.build_object_path(user_id, MediaType.IMAGE, "png")

    assert service.get_owned_media_type(path, str(user_id)) == MediaType.IMAGE.value


def test_get_owned_media_type_refuses_another_users_key(service):
    owner = uuid.uuid4()
    path = service.build_object_path(owner, MediaType.IMAGE, "png")

    assert service.get_owned_media_type(path, uuid.uuid4()) is None


@pytest.mark.parametrize(
    "template",
    (
        "{owner}/image/name.png",  # no ``users`` prefix
        "users/{owner}/image",  # no file name
        "users/{owner}/image/",  # empty file name
        "users/{owner}//name.png",  # no media type
        "users/{owner}/image/nested/name.png",  # deeper than one file
        "users/{owner}/audio/name.mp3",  # media type the product never issues
        "users/../{owner}/image/name.png",  # traversal
        "",  # nothing at all
    ),
)
def test_get_owned_media_type_refuses_a_path_that_is_not_an_upload_key(
    service, template
):
    user_id = uuid.uuid4()

    assert service.get_owned_media_type(template.format(owner=user_id), user_id) is None


def test_the_service_uses_the_injected_bucket_and_client(client):
    service = StorageService(bucket_name=BUCKET, client=client)

    assert service.bucket_name == BUCKET
    assert service.client is client


def test_build_storage_service_takes_the_bucket_name_from_the_settings(
    monkeypatch, configured
):
    monkeypatch.setenv("GCS_BUCKET_NAME", "from-settings")

    assert build_storage_service().bucket_name == "from-settings"


def test_build_storage_service_without_a_bucket_name_is_a_config_error(configured):
    with pytest.raises(ConfigError):
        build_storage_service()


def test_get_storage_service_returns_the_service_of_the_application(service):
    request = request_with_state(storage_service=service)

    assert get_storage_service(request) is service
    # The service of the application is handed out as it is: no second client.
    assert FakeStorageClient.instances == [service.client]


def test_get_storage_service_without_a_configured_service_fails_loudly(
    monkeypatch, configured
):
    monkeypatch.setenv("GCS_BUCKET_NAME", BUCKET)
    request = request_with_state(storage_service=None)

    with pytest.raises(HTTPException) as excinfo:
        get_storage_service(request)

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == storage_module.STORAGE_UNAVAILABLE_DETAIL
    # Even perfectly configured, the provider never builds a client of its own.
    assert FakeStorageClient.instances == []


def test_get_storage_service_without_any_state_fails_loudly(configured):
    with pytest.raises(HTTPException) as excinfo:
        get_storage_service(request_with_state())

    assert excinfo.value.status_code == 500
    assert FakeStorageClient.instances == []
