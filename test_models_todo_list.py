"""Schema and migration level checks for the ``TodoList`` model.

These assertions inspect SQLAlchemy metadata, in-memory instances, and migration
application so that the test suite verifies model constraints and backfill logic.
"""

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.db import Base
from app.models.todo_list import TodoList


def test_todo_list_is_mapped_on_the_shared_base():
    assert TodoList.__tablename__ == "todo_lists"
    assert Base.metadata.tables["todo_lists"] is TodoList.__table__


def test_todo_list_columns():
    columns = {column.name for column in TodoList.__table__.columns}
    assert {
        "id",
        "user_id",
        "name",
        "is_default",
        "created_at",
        "updated_at",
    } <= columns


def test_id_is_a_uuid_primary_key_with_a_default():
    column = TodoList.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, sa.Uuid)
    assert isinstance(column.default.arg(None), uuid.UUID)


def test_user_id_references_the_users_table():
    column = TodoList.__table__.c.user_id

    assert column.nullable is False
    assert column.index is True
    assert isinstance(column.type, sa.Uuid)

    foreign_key, = column.foreign_keys
    assert foreign_key.column is Base.metadata.tables["users"].c.id
    assert foreign_key.ondelete == "CASCADE"


def test_name_is_required():
    column = TodoList.__table__.c.name

    assert column.nullable is False
    assert isinstance(column.type, sa.String)


def test_is_default_is_boolean_with_default_false():
    column = TodoList.__table__.c.is_default

    assert column.nullable is False
    assert isinstance(column.type, sa.Boolean)
    assert column.default.arg is False


def test_created_at_has_a_server_default():
    column = TodoList.__table__.c.created_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None


def test_updated_at_has_a_server_default_and_onupdate():
    column = TodoList.__table__.c.updated_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None
    assert column.onupdate is not None


def test_case_insensitive_unique_constraint():
    indexes = [
        idx for idx in TodoList.__table__.indexes
        if idx.name == "uq_todo_lists_user_id_lower_name"
    ]
    assert len(indexes) == 1
    index = indexes[0]
    assert index.unique is True


def test_todo_list_can_be_instantiated():
    user_id = uuid.uuid4()

    todo_list = TodoList(
        user_id=user_id,
        name="Personal",
        is_default=False,
    )

    assert todo_list.user_id == user_id
    assert todo_list.name == "Personal"
    assert todo_list.is_default is False


def test_migration_007_creates_todo_lists_and_backfills_existing_tasks(tmp_path, monkeypatch):
    """Verify migration 007 creates todo_lists, seeds Inbox for users, and backfills todos.list_id."""
    config = Config(str(Path(__file__).parent / "alembic.ini"))
    db_file = tmp_path / "test_migration_007.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")

    command.upgrade(config, "006")

    engine = sa.create_engine(f"sqlite+pysqlite:///{db_file}")
    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()
    todo1_id = uuid.uuid4()
    todo2_id = uuid.uuid4()

    now_str = "2026-10-04 08:00:00"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, hashed_password, created_at) VALUES "
                f"('{user1_id.hex}', 'user1@example.com', 'hash1', '{now_str}'), "
                f"('{user2_id.hex}', 'user2@example.com', 'hash2', '{now_str}')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO todos (id, user_id, title, is_completed, created_at, updated_at) VALUES "
                f"('{todo1_id.hex}', '{user1_id.hex}', 'Todo 1', 0, '{now_str}', '{now_str}'), "
                f"('{todo2_id.hex}', '{user2_id.hex}', 'Todo 2', 0, '{now_str}', '{now_str}')"
            )
        )

    # Apply migration 007
    command.upgrade(config, "007")

    with engine.begin() as conn:
        inspector = sa.inspect(conn)
        assert "todo_lists" in inspector.get_table_names()

        columns = {col["name"]: col for col in inspector.get_columns("todo_lists")}
        assert {"id", "user_id", "name", "is_default", "created_at", "updated_at"} <= set(columns.keys())
        assert columns["name"]["nullable"] is False
        assert columns["is_default"]["nullable"] is False

        todo_cols = {col["name"]: col for col in inspector.get_columns("todos")}
        assert "list_id" in todo_cols
        assert todo_cols["list_id"]["nullable"] is False

        lists = conn.execute(
            sa.text("SELECT id, user_id, name, is_default FROM todo_lists")
        ).fetchall()
        assert len(lists) == 2
        for row in lists:
            assert row[2] == "Inbox"
            assert bool(row[3]) is True

        user_list_map = {row[1]: row[0] for row in lists}

        todos = conn.execute(
            sa.text("SELECT id, user_id, list_id FROM todos")
        ).fetchall()
        assert len(todos) == 2
        for row in todos:
            expected_list_id = user_list_map[row[1]]
            assert row[2] == expected_list_id

    # Test downgrade
    command.downgrade(config, "006")
    with engine.begin() as conn:
        inspector = sa.inspect(conn)
        assert "todo_lists" not in inspector.get_table_names()
        todo_cols = {col["name"]: col for col in inspector.get_columns("todos")}
        assert "list_id" not in todo_cols
