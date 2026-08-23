"""Integration tests for the signed URL endpoint.

The storage service is replaced through ``dependency_overrides`` by one built
on a fake Cloud Storage client, so the suite never authenticates against GCP
and never reaches the network. Nothing here touches the database either: the
endpoint trusts the ``sub`` claim of the token, so every scenario runs without
a service.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token
from app.services.storage import StorageService, get_storage_service
from main import app

ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "GCS_BUCKET_NAME",
    "GCS_SERVICE_ACCOUNT_INFO",
)

SECRET = "integration-test-jwt-secret-that-is-long-enough-for-hs256"

BUCKET = "sample-media"

SIGNED_URL_PATH = "/assets/signed-url"


class FakeBlob:
    """Stand-in for google.cloud.storage.blob.Blob."""

    def __init__(self, name: str):
        self.name = name
        self.signed_url_calls: list[dict] = []

    def generate_signed_url(self, **kwargs):
        self.signed_url_calls.append(kwargs)
        return f"https://signed.example/{self.name}"


class FakeBucket:
    """Stand-in for google.cloud.storage.bucket.Bucket."""

    def __init__(self, name: str):
        self.name = name
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob(name))


class FakeStorageClient:
    """Stand-in for google.cloud.storage.Client."""

    def __init__(self):
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:instance")
    get_settings.cache_clear()
    get_storage_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage_service.cache_clear()


@pytest.fixture
def storage_client() -> FakeStorageClient:
    return FakeStorageClient()


@pytest.fixture
def client(storage_client):
    """A client whose storage service signs URLs with the fake GCS client."""
    service = StorageService(bucket_name=BUCKET, client=storage_client)
    app.dependency_overrides[get_storage_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_storage_service, None)


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


def auth_headers(subject) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=subject)}"}


def parse_object_path(gcs_path: str) -> tuple[str, str, str, str]:
    """Split ``users/{user_id}/{media_type}/{uuid}.{ext}`` into its parts."""
    prefix, path_user_id, media_type, filename = gcs_path.split("/")
    assert prefix == "users"
    name, _, extension = filename.rpartition(".")
    return path_user_id, media_type, name, extension


# --- routing ---------------------------------------------------------------


def test_signed_url_route_is_registered():
    paths = app.openapi()["paths"]

    assert SIGNED_URL_PATH in paths
    assert "post" in paths[SIGNED_URL_PATH]


def test_existing_routes_are_untouched(client):
    assert client.get("/").json() == {"message": "Hello World"}
    assert client.get("/healthz").json() == {"status": "ok"}


# --- AC-2: authentication --------------------------------------------------


def test_request_signed_url_unauthorized(client):
    response = client.post(
        SIGNED_URL_PATH, json={"media_type": "image", "file_extension": "png"}
    )

    assert response.status_code == 401


def test_request_signed_url_rejects_a_token_signed_with_another_secret(
    client, monkeypatch, user_id
):
    monkeypatch.setenv("JWT_SECRET", "a-different-secret-that-is-long-enough-too")
    get_settings.cache_clear()
    headers = auth_headers(user_id)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()

    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=headers,
    )

    assert response.status_code == 401


def test_request_signed_url_rejects_a_malformed_token(client):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401


def test_request_signed_url_rejects_an_expired_token(client, user_id):
    token = create_access_token(subject=user_id, expires_delta=timedelta(minutes=-5))

    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_request_signed_url_rejects_a_subject_that_is_not_a_user_id(client):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=auth_headers("../../someone-else"),
    )

    assert response.status_code == 401


def test_unauthorized_response_never_reaches_storage(client, storage_client):
    client.post(SIGNED_URL_PATH, json={"media_type": "image", "file_extension": "png"})

    assert storage_client.buckets == {}


# --- AC-1 / AC-3: successful signed URL ------------------------------------


def test_request_signed_url_success(client, user_id):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"upload_url", "gcs_path"}

    path_user_id, media_type, name, extension = parse_object_path(body["gcs_path"])
    assert path_user_id == str(user_id)
    assert media_type == "image"
    assert uuid.UUID(name)
    assert extension == "png"

    assert body["upload_url"] == f"https://signed.example/{body['gcs_path']}"


def test_request_signed_url_supports_video(client, user_id):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "video", "file_extension": "webm"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    path_user_id, media_type, _, extension = parse_object_path(
        response.json()["gcs_path"]
    )
    assert (path_user_id, media_type, extension) == (str(user_id), "video", "webm")


def test_request_signed_url_normalises_the_extension(client, user_id):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": ".JPEG"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert response.json()["gcs_path"].endswith(".jpeg")


def test_each_request_gets_a_fresh_object_name(client, user_id):
    payload = {"media_type": "image", "file_extension": "png"}
    headers = auth_headers(user_id)

    first = client.post(SIGNED_URL_PATH, json=payload, headers=headers).json()
    second = client.post(SIGNED_URL_PATH, json=payload, headers=headers).json()

    assert first["gcs_path"] != second["gcs_path"]


def test_object_path_ignores_a_client_supplied_user_id(client, user_id):
    other_user_id = uuid.uuid4()

    response = client.post(
        SIGNED_URL_PATH,
        json={
            "media_type": "image",
            "file_extension": "png",
            "user_id": str(other_user_id),
        },
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert str(other_user_id) not in response.json()["gcs_path"]
    assert response.json()["gcs_path"].startswith(f"users/{user_id}/")


def test_signed_url_is_a_v4_put_url_with_the_upload_size_limit(
    client, storage_client, user_id
):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=auth_headers(user_id),
    )

    gcs_path = response.json()["gcs_path"]
    blob = storage_client.buckets[BUCKET].blobs[gcs_path]
    (kwargs,) = blob.signed_url_calls
    assert kwargs["version"] == "v4"
    assert kwargs["method"] == "PUT"
    assert kwargs["headers"]["x-goog-content-length-range"].endswith(
        f",{500 * 1024 * 1024}"
    )


# --- validation ------------------------------------------------------------


def test_unknown_media_type_is_rejected(client, user_id):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "audio", "file_extension": "mp3"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 422


def test_missing_file_extension_is_rejected(client, user_id):
    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 422


def test_validation_is_only_reached_with_a_token(client):
    response = client.post(SIGNED_URL_PATH, json={"media_type": "audio"})

    assert response.status_code == 401
