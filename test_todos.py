"""Integration tests for the Todo endpoints.

These scenarios test creating and listing todos via the REST API.
Database operations run against an in-memory SQLite database so the test suite
runs without external services.
"""

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db import Base
from app.db.session import get_db
from app.models import Asset, Todo, User
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
    """A registered user in the database."""
    user = User(
        id=user_id,
        email=f"{user_id}@example.test",
        hashed_password="not-a-real-hash",
    )
    db_session.add(user)
    db_session.commit()
    return user_id


@pytest.fixture
def auth_headers(registered_user) -> dict[str, str]:
    """Authorization header with a valid bearer token for registered_user."""
    token = create_access_token(registered_user)
    return {"Authorization": f"Bearer {token}"}


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
    db_session.commit()
    return other_user_id


@pytest.fixture
def other_auth_headers(other_registered_user) -> dict[str, str]:
    """Authorization header with a valid bearer token for other_registered_user."""
    token = create_access_token(other_registered_user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_asset(db_session, user_id) -> Asset:
    """An asset owned by the registered user."""
    asset = Asset(
        user_id=user_id,
        gcs_path=f"users/{user_id}/image/{uuid.uuid4()}.png",
        public_url="https://storage.googleapis.com/bucket/test.png",
        media_type="image",
    )
    db_session.add(asset)
    db_session.commit()
    return asset


@pytest.fixture
def other_user_asset(db_session, other_user_id) -> Asset:
    """An asset owned by another user."""
    asset = Asset(
        user_id=other_user_id,
        gcs_path=f"users/{other_user_id}/image/{uuid.uuid4()}.png",
        public_url="https://storage.googleapis.com/bucket/other.png",
        media_type="image",
    )
    db_session.add(asset)
    db_session.commit()
    return asset


# --- POST /todos tests ---


def test_create_todo_success(client, auth_headers, user_id, db_session):
    """Creating a todo with title and description returns 201 Created and persists it."""
    payload = {
        "title": "Buy groceries",
        "description": "Milk, eggs, and bread",
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Buy groceries"
    assert data["description"] == "Milk, eggs, and bread"
    assert data["is_completed"] is False
    assert data["user_id"] == str(user_id)
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # Verify persisted in database
    todo_id = uuid.UUID(data["id"])
    persisted = db_session.get(Todo, todo_id)
    assert persisted is not None
    assert persisted.title == "Buy groceries"
    assert persisted.description == "Milk, eggs, and bread"
    assert persisted.user_id == user_id
    assert persisted.is_completed is False


def test_create_todo_title_only(client, auth_headers, user_id, db_session):
    """Creating a todo with only a title defaults description to None and is_completed to False."""
    payload = {"title": "Call dentist"}
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Call dentist"
    assert data["description"] is None
    assert data["is_completed"] is False
    assert data["user_id"] == str(user_id)


def test_create_todo_unauthenticated(client):
    """Creating a todo without an Authorization header returns 401 Unauthorized."""
    payload = {"title": "Buy groceries"}
    response = client.post("/todos", json=payload)

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_create_todo_invalid_token(client):
    """Creating a todo with an invalid token returns 401 Unauthorized."""
    payload = {"title": "Buy groceries"}
    response = client.post(
        "/todos",
        json=payload,
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_create_todo_expired_token(client, user_id):
    """Creating a todo with an expired token returns 401 Unauthorized."""
    token = create_access_token(user_id, expires_delta=timedelta(minutes=-5))
    response = client.post(
        "/todos",
        json={"title": "Buy groceries"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_create_todo_missing_title(client, auth_headers):
    """Creating a todo without a title field returns 422 Unprocessable Entity."""
    response = client.post("/todos", json={"description": "No title provided"}, headers=auth_headers)

    assert response.status_code == 422


def test_create_todo_empty_title(client, auth_headers):
    """Creating a todo with an empty string title returns 422 Unprocessable Entity."""
    response = client.post("/todos", json={"title": ""}, headers=auth_headers)

    assert response.status_code == 422


def test_create_todo_empty_body(client, auth_headers):
    """Creating a todo with an empty JSON body returns 422 Unprocessable Entity."""
    response = client.post("/todos", json={}, headers=auth_headers)

    assert response.status_code == 422


# --- AC-1: create todo with asset_ids ---


def test_create_todo_with_asset_ids(client, auth_headers, user_id, user_asset, db_session):
    """Creating a todo with asset_ids associates user-owned assets and returns them (AC-1)."""
    payload = {
        "title": "Todo with an attachment",
        "description": "See attached image",
        "asset_ids": [str(user_asset.id)],
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Todo with an attachment"
    assert "assets" in data
    assert len(data["assets"]) == 1
    assert data["assets"][0]["id"] == str(user_asset.id)
    assert data["assets"][0]["gcs_path"] == user_asset.gcs_path
    assert data["assets"][0]["public_url"] == user_asset.public_url
    assert data["assets"][0]["media_type"] == user_asset.media_type
    assert data["assets"][0]["user_id"] == str(user_id)

    # Verify persisted relationship in database
    todo_id = uuid.UUID(data["id"])
    persisted = db_session.get(Todo, todo_id)
    assert persisted is not None
    assert len(persisted.assets) == 1
    assert persisted.assets[0].id == user_asset.id


def test_create_todo_with_multiple_asset_ids(client, auth_headers, user_id, db_session):
    """Creating a todo with multiple user-owned asset_ids associates all of them (AC-1)."""
    asset1 = Asset(
        user_id=user_id,
        gcs_path=f"users/{user_id}/image/{uuid.uuid4()}.png",
        public_url="https://storage.googleapis.com/bucket/1.png",
        media_type="image",
    )
    asset2 = Asset(
        user_id=user_id,
        gcs_path=f"users/{user_id}/video/{uuid.uuid4()}.mp4",
        public_url="https://storage.googleapis.com/bucket/2.mp4",
        media_type="video",
    )
    db_session.add_all([asset1, asset2])
    db_session.commit()

    payload = {
        "title": "Todo with multiple assets",
        "asset_ids": [str(asset1.id), str(asset2.id)],
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert len(data["assets"]) == 2
    returned_ids = {a["id"] for a in data["assets"]}
    assert returned_ids == {str(asset1.id), str(asset2.id)}


def test_create_todo_asset_ids_max_length_exceeded(client, auth_headers):
    """Creating a todo with more than 10 asset_ids returns 422 Unprocessable Entity (AC-1)."""
    payload = {
        "title": "Too many assets",
        "asset_ids": [str(uuid.uuid4()) for _ in range(11)],
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_create_todo_only_attaches_user_owned_assets(
    client, auth_headers, user_id, user_asset, other_user_asset, db_session
):
    """Creating a todo with an asset owned by another user ignores the unowned asset (AC-1)."""
    payload = {
        "title": "Todo with mixed assets",
        "asset_ids": [str(user_asset.id), str(other_user_asset.id)],
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert len(data["assets"]) == 1
    assert data["assets"][0]["id"] == str(user_asset.id)

    # Database verify
    todo_id = uuid.UUID(data["id"])
    persisted = db_session.get(Todo, todo_id)
    assert len(persisted.assets) == 1
    assert persisted.assets[0].id == user_asset.id


def test_create_todo_with_nonexistent_asset_id(client, auth_headers, user_id, db_session):
    """Creating a todo with a non-existent asset_id results in no attached assets."""
    payload = {
        "title": "Todo with missing asset",
        "asset_ids": [str(uuid.uuid4())],
    }
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["assets"] == []


def test_create_todo_without_asset_ids_defaults_to_empty(client, auth_headers):
    """Creating a todo without asset_ids returns empty assets list."""
    payload = {"title": "Todo without assets"}
    response = client.post("/todos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["assets"] == []


# --- GET /todos tests ---


def test_list_todos_empty(client, auth_headers):
    """Listing todos when user has none returns an empty list with 200 OK."""
    response = client.get("/todos", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


def test_list_todos_returns_user_todos(client, auth_headers, user_id, db_session):
    """Listing todos returns all todos created by the authenticated user."""
    todo1 = Todo(user_id=user_id, title="First todo", description="First desc")
    todo2 = Todo(user_id=user_id, title="Second todo", description=None, is_completed=True)
    db_session.add_all([todo1, todo2])
    db_session.commit()

    response = client.get("/todos", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    titles = [t["title"] for t in data]
    assert "First todo" in titles
    assert "Second todo" in titles
    for item in data:
        assert item["user_id"] == str(user_id)


def test_list_todos_scoped_to_current_user(
    client, auth_headers, other_auth_headers, user_id, other_user_id, db_session
):
    """Listing todos only returns todos belonging to current_user, not other users."""
    user_todo = Todo(user_id=user_id, title="User todo")
    other_todo = Todo(user_id=other_user_id, title="Other user todo")
    db_session.add_all([user_todo, other_todo])
    db_session.commit()

    # User 1 request
    response1 = client.get("/todos", headers=auth_headers)
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1) == 1
    assert data1[0]["title"] == "User todo"
    assert data1[0]["user_id"] == str(user_id)

    # User 2 request
    response2 = client.get("/todos", headers=other_auth_headers)
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2) == 1
    assert data2[0]["title"] == "Other user todo"
    assert data2[0]["user_id"] == str(other_user_id)


def test_list_todos_unauthenticated(client):
    """Listing todos without an Authorization header returns 401 Unauthorized."""
    response = client.get("/todos")

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_list_todos_invalid_token(client):
    """Listing todos with an invalid token returns 401 Unauthorized."""
    response = client.get("/todos", headers={"Authorization": "Bearer invalid-token"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


# --- GET /todos/{todo_id} tests ---


def test_get_todo_success(client, auth_headers, user_id, db_session):
    """Retrieving an owned todo returns 200 OK with full todo details."""
    todo = Todo(
        user_id=user_id,
        title="Dentist Appointment",
        description="Checkup at 3pm",
        is_completed=False,
    )
    db_session.add(todo)
    db_session.commit()

    response = client.get(f"/todos/{todo.id}", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(todo.id)
    assert data["title"] == "Dentist Appointment"
    assert data["description"] == "Checkup at 3pm"
    assert data["is_completed"] is False
    assert data["user_id"] == str(user_id)
    assert "created_at" in data
    assert "updated_at" in data


def test_get_todo_not_found(client, auth_headers):
    """Retrieving a non-existent todo returns 404 Not Found."""
    non_existent_id = uuid.uuid4()
    response = client.get(f"/todos/{non_existent_id}", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}


def test_get_todo_other_user(
    client, auth_headers, other_auth_headers, other_user_id, db_session
):
    """Retrieving a todo owned by another user returns 404 Not Found."""
    other_todo = Todo(
        user_id=other_user_id,
        title="Other user secret",
        description="Private notes",
    )
    db_session.add(other_todo)
    db_session.commit()

    # Accessing as user 1 should 404
    response = client.get(f"/todos/{other_todo.id}", headers=auth_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}

    # Accessing as the owner (other user) should succeed
    owner_response = client.get(f"/todos/{other_todo.id}", headers=other_auth_headers)
    assert owner_response.status_code == 200
    assert owner_response.json()["id"] == str(other_todo.id)


def test_get_todo_unauthenticated(client):
    """Retrieving a todo without authorization returns 401 Unauthorized."""
    response = client.get(f"/todos/{uuid.uuid4()}")

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_get_todo_invalid_token(client):
    """Retrieving a todo with an invalid token returns 401 Unauthorized."""
    response = client.get(
        f"/todos/{uuid.uuid4()}",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


# --- AC-2: retrieve todo with attached assets ---


def test_get_todo_with_attached_assets(
    client, auth_headers, user_id, user_asset, db_session
):
    """Retrieving a single todo returns attached assets (AC-2)."""
    todo = Todo(
        user_id=user_id,
        title="Todo with Asset",
        description="Inspect details",
        is_completed=False,
    )
    todo.assets.append(user_asset)
    db_session.add(todo)
    db_session.commit()

    response = client.get(f"/todos/{todo.id}", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(todo.id)
    assert "assets" in data
    assert len(data["assets"]) == 1
    assert data["assets"][0]["id"] == str(user_asset.id)
    assert data["assets"][0]["gcs_path"] == user_asset.gcs_path
    assert data["assets"][0]["public_url"] == user_asset.public_url
    assert data["assets"][0]["media_type"] == user_asset.media_type


def test_get_todo_without_assets_returns_empty_list(
    client, auth_headers, user_id, db_session
):
    """Retrieving a todo without attached assets returns an empty assets list (AC-2)."""
    todo = Todo(
        user_id=user_id,
        title="Todo without Asset",
        description="No assets",
    )
    db_session.add(todo)
    db_session.commit()

    response = client.get(f"/todos/{todo.id}", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["assets"] == []


def test_list_todos_includes_attached_assets(
    client, auth_headers, user_id, user_asset, db_session
):
    """Listing todos returns attached assets for each todo (AC-2)."""
    todo1 = Todo(
        user_id=user_id,
        title="Todo with asset",
        description="First",
    )
    todo1.assets.append(user_asset)

    todo2 = Todo(
        user_id=user_id,
        title="Todo without asset",
        description="Second",
    )
    db_session.add_all([todo1, todo2])
    db_session.commit()

    response = client.get("/todos", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    by_title = {t["title"]: t for t in data}
    assert len(by_title["Todo with asset"]["assets"]) == 1
    assert by_title["Todo with asset"]["assets"][0]["id"] == str(user_asset.id)
    assert by_title["Todo without asset"]["assets"] == []


# --- PATCH /todos/{todo_id} tests ---


def test_patch_todo_success(client, auth_headers, user_id, db_session):
    """Partially updating an owned todo updates specified fields and refreshes updated_at."""
    todo = Todo(
        user_id=user_id,
        title="Old Title",
        description="Old Description",
        is_completed=False,
    )
    db_session.add(todo)
    db_session.commit()
    initial_updated_at = todo.updated_at

    payload = {"title": "New Title", "is_completed": True}
    response = client.patch(f"/todos/{todo.id}", json=payload, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(todo.id)
    assert data["title"] == "New Title"
    assert data["description"] == "Old Description"
    assert data["is_completed"] is True
    assert data["user_id"] == str(user_id)

    # Verify persisted in database
    db_session.refresh(todo)
    assert todo.title == "New Title"
    assert todo.description == "Old Description"
    assert todo.is_completed is True
    assert todo.updated_at >= initial_updated_at


def test_patch_todo_description(client, auth_headers, user_id, db_session):
    """Partially updating only description changes description while keeping other fields intact."""
    todo = Todo(
        user_id=user_id,
        title="Stay Same",
        description="Before",
        is_completed=False,
    )
    db_session.add(todo)
    db_session.commit()

    payload = {"description": "After"}
    response = client.patch(f"/todos/{todo.id}", json=payload, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Stay Same"
    assert data["description"] == "After"
    assert data["is_completed"] is False

    # Also test explicitly clearing description to None
    clear_response = client.patch(
        f"/todos/{todo.id}", json={"description": None}, headers=auth_headers
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["description"] is None



def test_patch_todo_not_found(client, auth_headers):
    """Updating a non-existent todo returns 404 Not Found."""
    non_existent_id = uuid.uuid4()
    response = client.patch(
        f"/todos/{non_existent_id}",
        json={"title": "Updated"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}


def test_patch_todo_other_user(
    client, auth_headers, other_user_id, db_session
):
    """Updating a todo belonging to another user returns 404 and does not mutate it."""
    other_todo = Todo(
        user_id=other_user_id,
        title="Untouchable Title",
        description="Untouchable Desc",
        is_completed=False,
    )
    db_session.add(other_todo)
    db_session.commit()

    response = client.patch(
        f"/todos/{other_todo.id}",
        json={"title": "Hacked Title"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}

    db_session.refresh(other_todo)
    assert other_todo.title == "Untouchable Title"


def test_patch_todo_unauthenticated(client):
    """Updating a todo without authorization returns 401 Unauthorized."""
    response = client.patch(
        f"/todos/{uuid.uuid4()}",
        json={"title": "New Title"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_patch_todo_invalid_token(client):
    """Updating a todo with an invalid token returns 401 Unauthorized."""
    response = client.patch(
        f"/todos/{uuid.uuid4()}",
        json={"title": "New Title"},
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_patch_todo_invalid_payload(client, auth_headers, user_id, db_session):
    """Updating a todo with invalid field values (e.g., empty title) returns 422."""
    todo = Todo(user_id=user_id, title="Valid Title")
    db_session.add(todo)
    db_session.commit()

    response = client.patch(
        f"/todos/{todo.id}",
        json={"title": ""},
        headers=auth_headers,
    )

    assert response.status_code == 422


# --- DELETE /todos/{todo_id} tests ---


def test_delete_todo_success(client, auth_headers, user_id, db_session):
    """Deleting an owned todo returns 204 No Content and removes it from the database."""
    todo = Todo(user_id=user_id, title="To be deleted")
    db_session.add(todo)
    db_session.commit()

    response = client.delete(f"/todos/{todo.id}", headers=auth_headers)

    assert response.status_code == 204
    assert response.text == ""

    # Verify deleted from database
    persisted = db_session.get(Todo, todo.id)
    assert persisted is None


def test_delete_todo_not_found(client, auth_headers):
    """Deleting a non-existent todo returns 404 Not Found."""
    non_existent_id = uuid.uuid4()
    response = client.delete(f"/todos/{non_existent_id}", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}


def test_delete_todo_other_user(
    client, auth_headers, other_user_id, db_session
):
    """Deleting a todo belonging to another user returns 404 and does not delete it."""
    other_todo = Todo(
        user_id=other_user_id,
        title="Other user item",
    )
    db_session.add(other_todo)
    db_session.commit()

    response = client.delete(f"/todos/{other_todo.id}", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}

    # Verify still exists in DB
    persisted = db_session.get(Todo, other_todo.id)
    assert persisted is not None
    assert persisted.title == "Other user item"


def test_delete_todo_unauthenticated(client):
    """Deleting a todo without authorization returns 401 Unauthorized."""
    response = client.delete(f"/todos/{uuid.uuid4()}")

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_delete_todo_invalid_token(client):
    """Deleting a todo with an invalid token returns 401 Unauthorized."""
    response = client.delete(
        f"/todos/{uuid.uuid4()}",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}

