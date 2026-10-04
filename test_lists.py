"""Integration tests for the TodoList endpoints.

These scenarios test creating, listing, renaming, and deleting todo lists,
including duplicate name rejection, default list deletion rejection, and atomic
task reassignment upon custom list deletion.
"""

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db import Base
from app.db.session import get_db
from app.models import Asset, Todo, TodoList, User
from main import app

ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
)

SECRET = "integration-test-jwt-secret-that-is-long-enough-for-hs256"


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
def db_session():
    """A session on an in-memory SQLite database holding the whole schema."""
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
def client(db_session):
    """FastAPI TestClient with get_db overridden to use the in-memory SQLite database."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def registered_user(db_session, user_id) -> uuid.UUID:
    """A registered user in the database with a default Inbox list."""
    user = User(
        id=user_id,
        email=f"{user_id}@example.test",
        hashed_password="not-a-real-hash",
    )
    db_session.add(user)
    db_session.add(TodoList(user_id=user_id, name="Inbox", is_default=True))
    db_session.commit()
    return user_id


@pytest.fixture
def auth_headers(registered_user) -> dict[str, str]:
    """Authorization header with a valid bearer token for registered_user."""
    token = create_access_token(registered_user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def default_list(db_session, registered_user) -> TodoList:
    """Retrieve the registered user's default list."""
    stmt = sa.select(TodoList).where(
        TodoList.user_id == registered_user,
        TodoList.is_default == True,  # noqa: E712
    )
    return db_session.scalar(stmt)


@pytest.fixture
def other_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_registered_user(db_session, other_user_id) -> uuid.UUID:
    """Another registered user in the database for multi-user isolation tests."""
    user = User(
        id=other_user_id,
        email=f"{other_user_id}@example.test",
        hashed_password="not-a-real-hash",
    )
    db_session.add(user)
    db_session.add(TodoList(user_id=other_user_id, name="Inbox", is_default=True))
    db_session.commit()
    return other_user_id


@pytest.fixture
def other_auth_headers(other_registered_user) -> dict[str, str]:
    """Authorization header with a valid bearer token for other_registered_user."""
    token = create_access_token(other_registered_user)
    return {"Authorization": f"Bearer {token}"}


# --- POST /lists (Create custom list) ---


def test_create_custom_list_success(client, auth_headers, user_id, db_session):
    """Creating a custom list returns 201 Created with is_default=False."""
    response = client.post("/lists", json={"name": "Work"}, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Work"
    assert data["is_default"] is False
    assert data["user_id"] == str(user_id)
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data

    list_id = uuid.UUID(data["id"])
    persisted = db_session.get(TodoList, list_id)
    assert persisted is not None
    assert persisted.name == "Work"
    assert persisted.is_default is False
    assert persisted.user_id == user_id


def test_create_list_name_validation(client, auth_headers):
    """Creating a list with empty name or >255 chars returns 422 Unprocessable Entity."""
    res_empty = client.post("/lists", json={"name": ""}, headers=auth_headers)
    assert res_empty.status_code == 422

    res_too_long = client.post("/lists", json={"name": "a" * 256}, headers=auth_headers)
    assert res_too_long.status_code == 422

    res_max_len = client.post("/lists", json={"name": "a" * 255}, headers=auth_headers)
    assert res_max_len.status_code == 201


def test_create_list_rejects_duplicate_name_case_insensitive(client, auth_headers):
    """Creating a list with duplicate name (case-insensitive) returns 409 Conflict."""
    # Matches default list "Inbox"
    res1 = client.post("/lists", json={"name": "inbox"}, headers=auth_headers)
    assert res1.status_code == 409

    res2 = client.post("/lists", json={"name": "INBOX"}, headers=auth_headers)
    assert res2.status_code == 409

    # Create "Projects"
    res3 = client.post("/lists", json={"name": "Projects"}, headers=auth_headers)
    assert res3.status_code == 201

    # Try duplicate "projects"
    res4 = client.post("/lists", json={"name": "projects"}, headers=auth_headers)
    assert res4.status_code == 409


def test_create_list_allows_same_name_for_different_users(
    client, auth_headers, other_auth_headers
):
    """Different users can create lists with the same name."""
    res1 = client.post("/lists", json={"name": "Shared Name"}, headers=auth_headers)
    assert res1.status_code == 201

    res2 = client.post("/lists", json={"name": "Shared Name"}, headers=other_auth_headers)
    assert res2.status_code == 201


def test_create_list_unauthenticated(client):
    """Unauthenticated POST /lists returns 401."""
    response = client.post("/lists", json={"name": "Work"})
    assert response.status_code == 401


# --- GET /lists (List lists) ---


def test_list_lists_default_pinned_first_then_alphabetical(client, auth_headers):
    """GET /lists returns default list pinned first, then alphabetical by name."""
    client.post("/lists", json={"name": "Zebra"}, headers=auth_headers)
    client.post("/lists", json={"name": "Apple"}, headers=auth_headers)
    client.post("/lists", json={"name": "Mango"}, headers=auth_headers)

    response = client.get("/lists", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4

    assert data[0]["name"] == "Inbox"
    assert data[0]["is_default"] is True

    custom_names = [item["name"] for item in data[1:]]
    assert custom_names == ["Apple", "Mango", "Zebra"]
    for item in data[1:]:
        assert item["is_default"] is False


def test_list_lists_scoped_to_current_user(
    client, auth_headers, other_auth_headers
):
    """GET /lists only returns lists belonging to authenticated user."""
    client.post("/lists", json={"name": "User 1 List"}, headers=auth_headers)
    client.post("/lists", json={"name": "User 2 List"}, headers=other_auth_headers)

    res1 = client.get("/lists", headers=auth_headers)
    assert res1.status_code == 200
    names1 = [item["name"] for item in res1.json()]
    assert "User 1 List" in names1
    assert "User 2 List" not in names1

    res2 = client.get("/lists", headers=other_auth_headers)
    assert res2.status_code == 200
    names2 = [item["name"] for item in res2.json()]
    assert "User 2 List" in names2
    assert "User 1 List" not in names2


def test_list_lists_unauthenticated(client):
    """Unauthenticated GET /lists returns 401."""
    response = client.get("/lists")
    assert response.status_code == 401


# --- PATCH /lists/{id} (Rename list) ---


def test_patch_rename_custom_list(client, auth_headers, db_session):
    """Renaming a custom list preserves is_default=False."""
    create_res = client.post("/lists", json={"name": "Old Custom"}, headers=auth_headers)
    list_id = create_res.json()["id"]

    patch_res = client.patch(
        f"/lists/{list_id}", json={"name": "New Custom"}, headers=auth_headers
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["name"] == "New Custom"
    assert data["is_default"] is False

    persisted = db_session.get(TodoList, uuid.UUID(list_id))
    assert persisted.name == "New Custom"
    assert persisted.is_default is False


def test_patch_rename_default_list(client, auth_headers, default_list, db_session):
    """Renaming the default list preserves is_default=True."""
    patch_res = client.patch(
        f"/lists/{default_list.id}", json={"name": "General"}, headers=auth_headers
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["name"] == "General"
    assert data["is_default"] is True

    db_session.refresh(default_list)
    assert default_list.name == "General"
    assert default_list.is_default is True


def test_patch_rename_rejects_duplicate_name(client, auth_headers):
    """Renaming a list to an existing list name (case-insensitive) returns 409 Conflict."""
    client.post("/lists", json={"name": "List A"}, headers=auth_headers)
    res_b = client.post("/lists", json={"name": "List B"}, headers=auth_headers)
    list_b_id = res_b.json()["id"]

    patch_res = client.patch(
        f"/lists/{list_b_id}", json={"name": "list a"}, headers=auth_headers
    )
    assert patch_res.status_code == 409


def test_patch_rename_same_name_allowed(client, auth_headers):
    """Updating a list with its own existing name is allowed."""
    res = client.post("/lists", json={"name": "Keep Name"}, headers=auth_headers)
    list_id = res.json()["id"]

    patch_res = client.patch(
        f"/lists/{list_id}", json={"name": "Keep Name"}, headers=auth_headers
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Keep Name"


def test_patch_rename_not_found(client, auth_headers):
    """Renaming a non-existent list returns 404 Not Found."""
    fake_id = uuid.uuid4()
    response = client.patch(
        f"/lists/{fake_id}", json={"name": "New"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_patch_rename_other_user_list(client, auth_headers, other_auth_headers):
    """Renaming another user's list returns 404 Not Found."""
    create_res = client.post(
        "/lists", json={"name": "Other List"}, headers=other_auth_headers
    )
    other_list_id = create_res.json()["id"]

    response = client.patch(
        f"/lists/{other_list_id}", json={"name": "Stolen"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_patch_rename_validation(client, auth_headers):
    """Renaming with invalid name returns 422."""
    res = client.post("/lists", json={"name": "Valid"}, headers=auth_headers)
    list_id = res.json()["id"]

    res_empty = client.patch(
        f"/lists/{list_id}", json={"name": ""}, headers=auth_headers
    )
    assert res_empty.status_code == 422

    res_long = client.patch(
        f"/lists/{list_id}", json={"name": "x" * 256}, headers=auth_headers
    )
    assert res_long.status_code == 422


# --- DELETE /lists/{id} (Delete list) ---


def test_delete_default_list_returns_400(client, auth_headers, default_list, db_session):
    """Attempting to delete the default list returns 400 Bad Request."""
    response = client.delete(f"/lists/{default_list.id}", headers=auth_headers)
    assert response.status_code == 400

    persisted = db_session.get(TodoList, default_list.id)
    assert persisted is not None


def test_delete_custom_list_success_and_reassigns_tasks(
    client, auth_headers, user_id, default_list, db_session
):
    """Deleting a custom list reassigns tasks to default list atomically without deleting tasks or assets."""
    create_res = client.post("/lists", json={"name": "Custom"}, headers=auth_headers)
    custom_list_id = uuid.UUID(create_res.json()["id"])

    # Create an asset and attach to a task in custom list
    asset = Asset(
        user_id=user_id,
        gcs_path=f"users/{user_id}/test.png",
        public_url="https://storage.googleapis.com/test.png",
        media_type="image",
    )
    db_session.add(asset)
    db_session.flush()

    task1 = Todo(
        user_id=user_id,
        list_id=custom_list_id,
        title="Task 1 in custom",
        assets=[asset],
    )
    task2 = Todo(
        user_id=user_id,
        list_id=custom_list_id,
        title="Task 2 in custom",
    )
    db_session.add_all([task1, task2])
    db_session.commit()

    task1_id = task1.id
    task2_id = task2.id
    asset_id = asset.id

    # Delete custom list
    delete_res = client.delete(f"/lists/{custom_list_id}", headers=auth_headers)
    assert delete_res.status_code == 204

    # List is deleted
    assert db_session.get(TodoList, custom_list_id) is None

    # Tasks still exist and are reassigned to default list
    db_session.expire_all()
    t1 = db_session.get(Todo, task1_id)
    t2 = db_session.get(Todo, task2_id)
    assert t1 is not None
    assert t1.list_id == default_list.id
    assert t2 is not None
    assert t2.list_id == default_list.id

    # Asset attachment is intact
    assert len(t1.assets) == 1
    assert t1.assets[0].id == asset_id
    assert db_session.get(Asset, asset_id) is not None


def test_delete_list_not_found(client, auth_headers):
    """Deleting a non-existent list returns 404 Not Found."""
    fake_id = uuid.uuid4()
    response = client.delete(f"/lists/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


def test_delete_other_user_list(client, auth_headers, other_auth_headers):
    """Deleting another user's list returns 404 Not Found."""
    create_res = client.post(
        "/lists", json={"name": "Other List"}, headers=other_auth_headers
    )
    other_list_id = create_res.json()["id"]

    response = client.delete(f"/lists/{other_list_id}", headers=auth_headers)
    assert response.status_code == 404
