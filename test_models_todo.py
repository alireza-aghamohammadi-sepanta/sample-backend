"""Schema level checks for the ``Todo`` model.

These assertions only inspect the SQLAlchemy metadata and in-memory instances,
so the suite stays runnable without a database service.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from app.db import Base
from app.models import Todo


def test_todo_is_mapped_on_the_shared_base():
    assert Todo.__tablename__ == "todos"
    assert Base.metadata.tables["todos"] is Todo.__table__


def test_todo_columns():
    columns = {column.name for column in Todo.__table__.columns}
    assert {
        "id",
        "user_id",
        "title",
        "description",
        "due_date",
        "is_completed",
        "created_at",
        "updated_at",
    } <= columns


def test_due_date_is_nullable():
    column = Todo.__table__.c.due_date

    assert column.nullable is True
    assert isinstance(column.type, sa.DateTime)
    assert column.type.timezone is True


def test_id_is_a_uuid_primary_key_with_a_default():
    column = Todo.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, sa.Uuid)
    assert isinstance(column.default.arg(None), uuid.UUID)


def test_user_id_references_the_users_table():
    column = Todo.__table__.c.user_id

    assert column.nullable is False
    assert column.index is True
    assert isinstance(column.type, sa.Uuid)

    foreign_key, = column.foreign_keys
    assert foreign_key.column is Base.metadata.tables["users"].c.id
    assert foreign_key.ondelete == "CASCADE"


def test_title_is_required():
    column = Todo.__table__.c.title

    assert column.nullable is False
    assert isinstance(column.type, sa.String)


def test_description_is_nullable():
    column = Todo.__table__.c.description

    assert column.nullable is True
    assert isinstance(column.type, sa.String)


def test_is_completed_is_boolean_with_default_false():
    column = Todo.__table__.c.is_completed

    assert column.nullable is False
    assert isinstance(column.type, sa.Boolean)
    assert column.default.arg is False


def test_created_at_has_a_server_default():
    column = Todo.__table__.c.created_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None


def test_updated_at_has_a_server_default_and_onupdate():
    column = Todo.__table__.c.updated_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None
    assert column.onupdate is not None


def test_todo_can_be_instantiated():
    user_id = uuid.uuid4()

    todo = Todo(
        user_id=user_id,
        title="Test task",
        description="Test details",
    )

    assert todo.user_id == user_id
    assert todo.title == "Test task"
    assert todo.description == "Test details"
    assert todo.due_date is None


def test_todo_can_be_instantiated_with_due_date():
    user_id = uuid.uuid4()
    due = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

    todo = Todo(
        user_id=user_id,
        title="Test task",
        description="Test details",
        due_date=due,
    )

    assert todo.user_id == user_id
    assert todo.title == "Test task"
    assert todo.description == "Test details"
    assert todo.due_date == due
