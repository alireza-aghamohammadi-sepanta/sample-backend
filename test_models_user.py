"""Schema level checks for the ``User`` model.

These assertions only inspect the SQLAlchemy metadata, so the suite stays
runnable without a database service.
"""

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import Base
from app.models import User


def test_user_is_mapped_on_the_shared_base():
    assert User.__tablename__ == "users"
    assert Base.metadata.tables["users"] is User.__table__


def test_user_columns():
    columns = {column.name for column in User.__table__.columns}
    assert {"id", "email", "hashed_password"} <= columns


def test_id_is_a_uuid_primary_key_with_a_default():
    column = User.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, sa.Uuid)
    assert isinstance(column.default.arg(None), uuid.UUID)


def test_email_is_unique_indexed_and_required():
    column = User.__table__.c.email

    assert column.nullable is False
    assert column.unique is True
    assert column.index is True

    index, = User.__table__.indexes
    assert index.unique is True
    assert [c.name for c in index.columns] == ["email"]


def test_hashed_password_is_required():
    assert User.__table__.c.hashed_password.nullable is False


def test_migrations_have_a_single_head():
    config = Config(str(Path(__file__).parent / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["002"]
    assert script.get_revision("001").down_revision is None
    assert script.get_revision("002").down_revision == "001"
