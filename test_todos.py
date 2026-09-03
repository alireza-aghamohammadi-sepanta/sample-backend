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
from app.models import Todo, User
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
