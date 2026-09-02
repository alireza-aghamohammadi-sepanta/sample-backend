"""Schema level checks for the ``PasswordResetToken`` model.

These assertions only inspect the SQLAlchemy metadata and in-memory instances,
so the suite stays runnable without a database service.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from app.db import Base
from app.models import PasswordResetToken


def test_password_reset_token_is_mapped_on_the_shared_base():
    assert PasswordResetToken.__tablename__ == "password_reset_tokens"
    assert Base.metadata.tables["password_reset_tokens"] is PasswordResetToken.__table__


def test_password_reset_token_columns():
    columns = {column.name for column in PasswordResetToken.__table__.columns}
    assert {"id", "user_id", "token_hash", "expires_at", "created_at", "used_at"} <= columns


def test_id_is_a_uuid_primary_key_with_a_default():
    column = PasswordResetToken.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, sa.Uuid)
    assert isinstance(column.default.arg(None), uuid.UUID)


def test_user_id_references_the_users_table():
    column = PasswordResetToken.__table__.c.user_id

    assert column.nullable is False
    assert column.index is True
    assert isinstance(column.type, sa.Uuid)

    foreign_key, = column.foreign_keys
    assert foreign_key.column is Base.metadata.tables["users"].c.id


def test_token_hash_is_indexed_and_required():
    column = PasswordResetToken.__table__.c.token_hash

    assert column.nullable is False
    assert column.index is True
    assert isinstance(column.type, sa.String)


def test_expires_at_is_required():
    column = PasswordResetToken.__table__.c.expires_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)


def test_created_at_has_a_server_default():
    column = PasswordResetToken.__table__.c.created_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None


def test_used_at_is_nullable():
    column = PasswordResetToken.__table__.c.used_at

    assert column.nullable is True
    assert isinstance(column.type, sa.DateTime)


def test_password_reset_token_can_be_instantiated():
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    token_obj = PasswordResetToken(
        user_id=user_id,
        token_hash="a" * 64,
        expires_at=now,
    )

    assert token_obj.user_id == user_id
    assert token_obj.token_hash == "a" * 64
    assert token_obj.expires_at == now
    assert token_obj.used_at is None
