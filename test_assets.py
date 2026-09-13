"""Integration tests for the signed URL and upload confirmation endpoints.

The storage service is replaced through ``dependency_overrides`` by one built
on a fake Cloud Storage client, so the suite never authenticates against GCP
and never reaches the network. The signed URL scenarios do not touch the
database at all: the endpoint trusts the ``sub`` claim of the token.

The confirmation scenarios do write rows, so ``get_db`` is overridden with a
session on a throwaway in-memory SQLite database created from the model
metadata. Every column of ``Asset`` uses a portable type, so the same mapping
runs there as on PostgreSQL and the tests can assert that a record is really
created without any service being installed.

A last group overrides nothing and publishes the storage service on
``app.state`` instead, which is how the lifespan wires it: those scenarios
cover the provider itself.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db import Base
from app.db.session import get_db
from app.models import Asset, User
from app.services.storage import (
    STORAGE_UNAVAILABLE_DETAIL,
    StorageService,
    get_storage_service,
)
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

CONFIRM_PATH = "/assets/confirm"

ASSETS_PATH = "/assets"


class FakeBlob:
    """Stand-in for google.cloud.storage.blob.Blob."""

    def __init__(self, name: str):
        self.name = name
        self.signed_url_calls: list[dict] = []
        # A blob object exists client side as soon as it is named; only an
        # upload makes the object itself exist in the bucket.
        self.uploaded = False

    def generate_signed_url(self, **kwargs):
        self.signed_url_calls.append(kwargs)
        return f"https://signed.example/{self.name}"

    def exists(self) -> bool:
        return self.uploaded


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

    def upload(self, gcs_path: str, bucket_name: str = BUCKET) -> None:
        """Pretend a client finished PUTting a file to ``gcs_path``."""
        self.bucket(bucket_name).blob(gcs_path).uploaded = True


class RecordingStorageService(StorageService):
    """A storage service recording the ownership checks the API asks it for.

    ``get_owned_media_type`` answers whatever the test asked for, so a scenario
    can prove the endpoint takes the verdict of the service instead of reading
    the key itself.
    """

    def __init__(self, bucket_name: str, client, media_type: str | None = "image"):
        super().__init__(bucket_name=bucket_name, client=client)
        self.media_type = media_type
        self.owned_media_type_calls: list[tuple[str, uuid.UUID]] = []

    def get_owned_media_type(self, gcs_path, user_id):
        self.owned_media_type_calls.append((gcs_path, user_id))
        return self.media_type


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:instance")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
def db_session():
    """A session on a throwaway database holding the whole model schema."""
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def registered_user(db_session, user_id) -> uuid.UUID:
    """The owner the confirmation scenarios authenticate as, as a stored row."""
    db_session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.test",
            hashed_password="not-a-real-hash",
        )
    )
    db_session.commit()
    return user_id


@pytest.fixture
def confirm_client(storage_client, db_session):
    """A client whose storage *and* database dependencies are local doubles."""
    service = StorageService(bucket_name=BUCKET, client=storage_client)

    def override_db():
        yield db_session

    app.dependency_overrides[get_storage_service] = lambda: service
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_storage_service, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def recording_client(storage_client, db_session):
    """A factory of clients whose storage service records what it is asked."""

    def build(media_type: str | None = "image"):
        service = RecordingStorageService(BUCKET, storage_client, media_type)

        def override_db():
            yield db_session

        app.dependency_overrides[get_storage_service] = lambda: service
        app.dependency_overrides[get_db] = override_db
        return TestClient(app), service

    try:
        yield build
    finally:
        app.dependency_overrides.pop(get_storage_service, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def state_client(db_session):
    """A factory of clients served by the storage service on ``app.state``.

    Nothing is overridden, so the scenarios exercise the real provider: the
    lifespan is simulated by publishing the service (or its absence) itself.
    """
    missing = object()
    previous = getattr(app.state, "storage_service", missing)

    def build(service: StorageService | None):
        app.state.storage_service = service

        def override_db():
            yield db_session

        app.dependency_overrides[get_db] = override_db
        return TestClient(app, raise_server_exceptions=False)

    try:
        yield build
    finally:
        app.dependency_overrides.pop(get_db, None)
        if previous is missing:
            del app.state.storage_service
        else:
            app.state.storage_service = previous


def stored_assets(session) -> list[Asset]:
    """Every asset row currently in ``session``'s database."""
    session.expire_all()
    return list(session.scalars(sa.select(Asset)))


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


# --- R2 AC-2: the object must exist in the bucket ---------------------------

# Mirrors the messages the confirmation endpoint answers with; asserting them
# keeps a scenario from passing on FastAPI's own "Not Found" for a missing
# route.
NOT_FOUND_DETAIL = "Uploaded file not found"
FORBIDDEN_DETAIL = "gcs_path is not an upload path of the authenticated user"


def object_path(owner, media_type: str = "image", extension: str = "png") -> str:
    """An object key of the shape the signed URL endpoint hands out."""
    return f"users/{owner}/{media_type}/{uuid.uuid4()}.{extension}"


def test_confirm_route_is_registered():
    paths = app.openapi()["paths"]

    assert CONFIRM_PATH in paths
    assert "post" in paths[CONFIRM_PATH]


def test_confirm_upload_non_existent(confirm_client, registered_user, db_session):
    gcs_path = object_path(registered_user)

    response = confirm_client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND_DETAIL
    assert stored_assets(db_session) == []


# --- R2 AC-3: the object must belong to the caller --------------------------


def test_confirm_upload_unauthorized_path(
    confirm_client, storage_client, registered_user, db_session
):
    other_user_id = uuid.uuid4()
    gcs_path = object_path(other_user_id)
    # The victim's file is really there: only the ownership check may stop it
    # from being claimed.
    storage_client.upload(gcs_path)

    response = confirm_client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == FORBIDDEN_DETAIL
    assert stored_assets(db_session) == []


@pytest.mark.parametrize(
    "template",
    (
        "{owner}/image/name.png",  # no ``users`` prefix
        "users/{owner}/image",  # no file name
        "users/{owner}//name.png",  # no media type
        "users/{owner}/image/nested/name.png",  # deeper than one file
        "users/{owner}/audio/name.mp3",  # media type the product never issues
        "users/../{owner}/image/name.png",  # traversal
    ),
)
def test_confirm_upload_rejects_a_path_that_is_not_an_upload_key(
    confirm_client, storage_client, registered_user, db_session, template
):
    gcs_path = template.format(owner=registered_user)
    storage_client.upload(gcs_path)

    response = confirm_client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 403
    assert stored_assets(db_session) == []


def test_confirm_upload_unauthorized(confirm_client, storage_client, db_session):
    gcs_path = object_path(uuid.uuid4())
    storage_client.upload(gcs_path)

    response = confirm_client.post(CONFIRM_PATH, json={"gcs_path": gcs_path})

    assert response.status_code == 401
    assert stored_assets(db_session) == []


# --- R2 AC-1: the confirmed upload is stored --------------------------------


def test_confirm_upload_success(
    confirm_client, storage_client, registered_user, db_session
):
    gcs_path = object_path(registered_user, media_type="video", extension="webm")
    storage_client.upload(gcs_path)

    response = confirm_client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "id",
        "user_id",
        "gcs_path",
        "public_url",
        "media_type",
        "created_at",
    }
    assert body["user_id"] == str(registered_user)
    assert body["gcs_path"] == gcs_path
    assert body["public_url"] == f"https://storage.googleapis.com/{BUCKET}/{gcs_path}"
    assert body["media_type"] == "video"

    (asset,) = stored_assets(db_session)
    assert asset.id == uuid.UUID(body["id"])
    assert asset.user_id == registered_user
    assert asset.gcs_path == gcs_path
    assert asset.public_url == body["public_url"]
    assert asset.media_type == "video"
    assert asset.created_at is not None


def test_signed_url_then_confirm_stores_the_upload(
    confirm_client, storage_client, registered_user, db_session
):
    headers = auth_headers(registered_user)
    gcs_path = confirm_client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=headers,
    ).json()["gcs_path"]
    storage_client.upload(gcs_path)

    response = confirm_client.post(
        CONFIRM_PATH, json={"gcs_path": gcs_path}, headers=headers
    )

    assert response.status_code == 200
    (asset,) = stored_assets(db_session)
    assert (asset.gcs_path, asset.media_type) == (gcs_path, "image")


def test_confirm_upload_requires_a_gcs_path(confirm_client, registered_user):
    response = confirm_client.post(
        CONFIRM_PATH, json={}, headers=auth_headers(registered_user)
    )

    assert response.status_code == 422


# --- the API layer knows no object key format -------------------------------


def test_confirm_upload_asks_the_storage_service_who_owns_the_path(
    recording_client, storage_client, registered_user
):
    client, service = recording_client()
    gcs_path = object_path(registered_user)
    storage_client.upload(gcs_path)

    response = client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 200
    assert service.owned_media_type_calls == [(gcs_path, registered_user)]


def test_confirm_upload_stores_the_media_type_the_service_reports(
    recording_client, storage_client, registered_user, db_session
):
    # A key of a shape the endpoint could not classify on its own: only the
    # service decides what it is, and the endpoint stores that answer.
    client, _service = recording_client(media_type="video")
    gcs_path = "some/other/layout.bin"
    storage_client.upload(gcs_path)

    response = client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 200
    (asset,) = stored_assets(db_session)
    assert (asset.gcs_path, asset.media_type) == (gcs_path, "video")


def test_confirm_upload_refuses_a_path_the_service_disowns(
    recording_client, storage_client, registered_user, db_session
):
    # A perfectly shaped key of the caller: the endpoint still refuses it,
    # because ownership is the service's verdict, not a string comparison.
    client, service = recording_client(media_type=None)
    gcs_path = object_path(registered_user)
    storage_client.upload(gcs_path)

    response = client.post(
        CONFIRM_PATH,
        json={"gcs_path": gcs_path},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == FORBIDDEN_DETAIL
    assert service.owned_media_type_calls == [(gcs_path, registered_user)]
    assert stored_assets(db_session) == []


# --- the endpoints are served by the application state ----------------------


def test_endpoints_use_the_storage_service_of_the_application_state(
    state_client, storage_client, registered_user
):
    client = state_client(StorageService(bucket_name=BUCKET, client=storage_client))

    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 200
    gcs_path = response.json()["gcs_path"]
    assert gcs_path in storage_client.buckets[BUCKET].blobs


def test_endpoints_fail_loudly_without_a_storage_service(
    state_client, registered_user, db_session
):
    client = state_client(None)

    response = client.post(
        SIGNED_URL_PATH,
        json={"media_type": "image", "file_extension": "png"},
        headers=auth_headers(registered_user),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == STORAGE_UNAVAILABLE_DETAIL
    assert stored_assets(db_session) == []


# --- GET /assets endpoint ---------------------------------------------------


def test_list_assets_route_is_registered():
    paths = app.openapi()["paths"]
    assert ASSETS_PATH in paths
    assert "get" in paths[ASSETS_PATH]


def test_list_assets_unauthenticated(confirm_client):
    """GET /assets without Authorization header returns 401 Unauthorized."""
    response = confirm_client.get(ASSETS_PATH)
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_list_assets_invalid_token(confirm_client):
    """GET /assets with an invalid token returns 401 Unauthorized."""
    response = confirm_client.get(
        ASSETS_PATH, headers={"Authorization": "Bearer not-a-valid-jwt-token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_list_assets_expired_token(confirm_client, registered_user):
    """GET /assets with an expired token returns 401 Unauthorized."""
    token = create_access_token(
        registered_user, expires_delta=timedelta(minutes=-5)
    )
    response = confirm_client.get(
        ASSETS_PATH, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_list_assets_empty(confirm_client, registered_user):
    """Listing assets when user has none returns an empty list with 200 OK."""
    response = confirm_client.get(
        ASSETS_PATH, headers=auth_headers(registered_user)
    )
    assert response.status_code == 200
    assert response.json() == []


def test_list_assets_returns_assets_ordered_by_created_at(
    confirm_client, registered_user, db_session
):
    """Listing assets returns assets belonging to user ordered by created_at ascending."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    asset_middle = Asset(
        id=uuid.uuid4(),
        user_id=registered_user,
        gcs_path=f"users/{registered_user}/image/2.png",
        public_url=f"https://storage.googleapis.com/sample-media/users/{registered_user}/image/2.png",
        media_type="image",
        created_at=base_time + timedelta(hours=1),
    )
    asset_earliest = Asset(
        id=uuid.uuid4(),
        user_id=registered_user,
        gcs_path=f"users/{registered_user}/image/1.png",
        public_url=f"https://storage.googleapis.com/sample-media/users/{registered_user}/image/1.png",
        media_type="image",
        created_at=base_time,
    )
    asset_latest = Asset(
        id=uuid.uuid4(),
        user_id=registered_user,
        gcs_path=f"users/{registered_user}/video/3.mp4",
        public_url=f"https://storage.googleapis.com/sample-media/users/{registered_user}/video/3.mp4",
        media_type="video",
        created_at=base_time + timedelta(hours=2),
    )

    # Insert out of order
    db_session.add_all([asset_middle, asset_earliest, asset_latest])
    db_session.commit()

    response = confirm_client.get(
        ASSETS_PATH, headers=auth_headers(registered_user)
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3

    # Check ordering is by created_at ascending
    assert data[0]["id"] == str(asset_earliest.id)
    assert data[1]["id"] == str(asset_middle.id)
    assert data[2]["id"] == str(asset_latest.id)

    # Verify response schema fields
    assert data[0]["user_id"] == str(registered_user)
    assert data[0]["gcs_path"] == f"users/{registered_user}/image/1.png"
    assert data[0]["public_url"] == f"https://storage.googleapis.com/sample-media/users/{registered_user}/image/1.png"
    assert data[0]["media_type"] == "image"
    assert "created_at" in data[0]


def test_list_assets_multi_tenant_isolation(
    confirm_client, registered_user, db_session
):
    """Users cannot see each other's assets."""
    other_user_id = uuid.uuid4()
    db_session.add(
        User(
            id=other_user_id,
            email=f"{other_user_id}@example.test",
            hashed_password="not-a-real-hash",
        )
    )
    asset_user1 = Asset(
        id=uuid.uuid4(),
        user_id=registered_user,
        gcs_path=f"users/{registered_user}/image/u1.png",
        public_url=f"https://storage.googleapis.com/sample-media/users/{registered_user}/image/u1.png",
        media_type="image",
    )
    asset_user2 = Asset(
        id=uuid.uuid4(),
        user_id=other_user_id,
        gcs_path=f"users/{other_user_id}/image/u2.png",
        public_url=f"https://storage.googleapis.com/sample-media/users/{other_user_id}/image/u2.png",
        media_type="image",
    )
    db_session.add_all([asset_user1, asset_user2])
    db_session.commit()

    # User 1 should only see user 1's asset
    response1 = confirm_client.get(
        ASSETS_PATH, headers=auth_headers(registered_user)
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1) == 1
    assert data1[0]["id"] == str(asset_user1.id)
    assert data1[0]["user_id"] == str(registered_user)

    # User 2 should only see user 2's asset
    response2 = confirm_client.get(
        ASSETS_PATH, headers=auth_headers(other_user_id)
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2) == 1
    assert data2[0]["id"] == str(asset_user2.id)
    assert data2[0]["user_id"] == str(other_user_id)


def test_list_assets_trailing_slash(confirm_client, registered_user):
    """GET /assets/ with trailing slash works properly."""
    response = confirm_client.get(
        f"{ASSETS_PATH}/", headers=auth_headers(registered_user)
    )
    assert response.status_code == 200
    assert response.json() == []
