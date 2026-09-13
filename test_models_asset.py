"""Schema level checks for the ``Asset`` model.

These assertions only inspect the SQLAlchemy metadata and in-memory instances,
so the suite stays runnable without a database service.
"""

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.db import Base
from app.models import Asset


def test_asset_is_mapped_on_the_shared_base():
    assert Asset.__tablename__ == "assets"
    assert Base.metadata.tables["assets"] is Asset.__table__


def test_asset_columns():
    columns = {column.name for column in Asset.__table__.columns}
    assert {"id", "user_id", "gcs_path", "public_url", "media_type"} <= columns


def test_id_is_a_uuid_primary_key_with_a_default():
    column = Asset.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, sa.Uuid)
    assert isinstance(column.default.arg(None), uuid.UUID)


def test_user_id_references_the_users_table():
    column = Asset.__table__.c.user_id

    assert column.nullable is False
    assert column.index is True
    assert isinstance(column.type, sa.Uuid)

    foreign_key, = column.foreign_keys
    assert foreign_key.column is Base.metadata.tables["users"].c.id


def test_media_columns_are_required():
    for name in ("gcs_path", "public_url", "media_type"):
        assert Asset.__table__.c[name].nullable is False


def test_created_at_has_a_server_default():
    column = Asset.__table__.c.created_at

    assert column.nullable is False
    assert isinstance(column.type, sa.DateTime)
    assert column.server_default is not None


def test_asset_can_be_instantiated_with_its_metadata():
    user_id = uuid.uuid4()

    asset = Asset(
        user_id=user_id,
        gcs_path="uploads/cat.png",
        public_url="https://storage.googleapis.com/bucket/uploads/cat.png",
        media_type="image/png",
    )

    assert asset.user_id == user_id
    assert asset.gcs_path == "uploads/cat.png"
    assert asset.public_url == "https://storage.googleapis.com/bucket/uploads/cat.png"
    assert asset.media_type == "image/png"


def test_migration_004_applies_cleanly(tmp_path, monkeypatch):
    """Verify that migration revision 004 applies cleanly to the test database schema without errors."""
    config = Config(str(Path(__file__).parent / "alembic.ini"))
    db_file = tmp_path / "test_migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")

    command.upgrade(config, "004")

    engine = sa.create_engine(f"sqlite+pysqlite:///{db_file}")
    try:
        inspector = sa.inspect(engine)
        assert "assets" in inspector.get_table_names()
        columns = {col["name"]: col for col in inspector.get_columns("assets")}
        assert {"id", "user_id", "gcs_path", "public_url", "media_type", "created_at"} <= set(columns.keys())
        assert columns["gcs_path"]["nullable"] is False
        assert columns["public_url"]["nullable"] is False
        assert columns["media_type"]["nullable"] is False
        assert columns["created_at"]["nullable"] is False

        indexes = inspector.get_indexes("assets")
        user_id_indexes = [idx for idx in indexes if idx["column_names"] == ["user_id"]]
        assert len(user_id_indexes) == 1

        command.downgrade(config, "003")
        inspector = sa.inspect(engine)
        assert "assets" not in inspector.get_table_names()
    finally:
        engine.dispose()
