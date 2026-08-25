"""Tests for the ASGI application: its endpoints and its lifespan.

The lifespan is exercised through ``TestClient`` used as a context manager,
with a fake GCS client, so startup never authenticates and never reaches the
network.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import DatabaseManager
from app.services import storage as storage_module
from app.services.storage import StorageService, get_storage_service
from main import app

client = TestClient(app)


ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "GCS_BUCKET_NAME",
    "GCS_SERVICE_ACCOUNT_INFO",
)

BUCKET = "sample-media"


class FakeStorageClient:
    """Stand-in for google.cloud.storage.Client."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    get_storage_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage_service.cache_clear()


@pytest.fixture
def configured(monkeypatch):
    """Settings and a fake client factory the lifespan builds storage from."""
    monkeypatch.setenv("JWT_SECRET", "jwt")
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:db")
    monkeypatch.setenv("GCS_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(
        storage_module, "_build_storage_client", lambda: FakeStorageClient()
    )


def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_publishes_the_shared_dependencies_on_the_app_state(configured):
    with TestClient(app) as started:
        assert isinstance(app.state.db_manager, DatabaseManager)
        assert isinstance(app.state.storage_service, StorageService)
        assert app.state.storage_service.bucket_name == BUCKET
        assert isinstance(app.state.storage_service.client, FakeStorageClient)
        assert started.get("/healthz").status_code == 200


def test_lifespan_releases_the_database_and_the_storage_client(configured, monkeypatch):
    shutdowns: list[DatabaseManager] = []
    monkeypatch.setattr(
        DatabaseManager, "shutdown", lambda self: shutdowns.append(self)
    )

    with TestClient(app):
        db_manager = app.state.db_manager
        storage_client = app.state.storage_service.client

    assert shutdowns == [db_manager]
    assert storage_client.closed is True


def test_app_starts_without_cloud_storage_configuration():
    with TestClient(app) as started:
        assert isinstance(app.state.db_manager, DatabaseManager)
        assert app.state.storage_service is None
        assert started.get("/healthz").status_code == 200
