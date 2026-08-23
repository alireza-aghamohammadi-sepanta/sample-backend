import os

import pytest
from sqlalchemy import text

from app.core.config import ConfigError, get_settings
from app.db import session as db_session


ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "DATABASE_INSTANCE",
    "DATABASE_URL",
    "DB_USER",
    "DB_NAME",
)

# Tests run against a real local engine. SQLite keeps the suite runnable
# without any service; point TEST_DATABASE_URL at a local PostgreSQL to
# exercise the same code against Postgres.
LOCAL_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")


class FakeConnection:
    """Stand-in for a DBAPI connection returned by the connector."""


class FakeConnector:
    """Stand-in for google.cloud.sql.connector.Connector."""

    instances: list["FakeConnector"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[tuple[tuple, dict]] = []
        self.closed = False
        FakeConnector.instances.append(self)

    def connect(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeConnection()

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    FakeConnector.instances = []
    db_session.reset_engine()
    get_settings.cache_clear()
    yield
    db_session.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def fake_connector(monkeypatch):
    from google.cloud.sql import connector as connector_module

    monkeypatch.setattr(connector_module, "Connector", FakeConnector)
    return FakeConnector


def _set_cloud_sql_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "jwt")
    monkeypatch.setenv("DATABASE_INSTANCE", "sample-project:europe-west1:sample-db")
    monkeypatch.setenv("DB_USER", "backend@sample-project.iam")
    monkeypatch.setenv("DB_NAME", "sample")


def test_connector_creator_uses_iam_auth(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)

    connection = db_session.connect_with_connector()

    assert isinstance(connection, FakeConnection)
    assert len(fake_connector.instances) == 1
    (args, kwargs), = fake_connector.instances[0].calls
    assert args == ("sample-project:europe-west1:sample-db", "pg8000")
    assert kwargs == {
        "user": "backend@sample-project.iam",
        "db": "sample",
        "enable_iam_auth": True,
    }


def test_connector_is_created_once(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)

    db_session.connect_with_connector()
    db_session.connect_with_connector()

    assert len(fake_connector.instances) == 1
    assert len(fake_connector.instances[0].calls) == 2


def test_database_name_defaults_to_postgres(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)
    monkeypatch.delenv("DB_NAME")

    db_session.connect_with_connector()

    (_args, kwargs), = fake_connector.instances[0].calls
    assert kwargs["db"] == "postgres"


def test_missing_db_user_raises_config_error(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)
    monkeypatch.delenv("DB_USER")

    with pytest.raises(ConfigError):
        db_session.connect_with_connector()


def test_engine_uses_pg8000_driver_and_connector(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)

    engine = db_session.get_engine()

    assert engine.url.drivername == "postgresql+pg8000"
    assert engine.url.host is None
    # The connector is only contacted when a connection is actually requested.
    assert fake_connector.instances == []


def test_database_url_overrides_the_connector(monkeypatch, fake_connector):
    _set_cloud_sql_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+pg8000://app:app@127.0.0.1:5432/db")

    engine = db_session.get_engine()

    assert engine.url.host == "127.0.0.1"
    assert engine.url.database == "db"
    assert fake_connector.instances == []


def test_engine_is_cached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", LOCAL_DATABASE_URL)

    assert db_session.get_engine() is db_session.get_engine()


def test_get_db_yields_a_working_session_and_closes_it(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", LOCAL_DATABASE_URL)

    generator = db_session.get_db()
    session = next(generator)

    assert session.execute(text("SELECT 1")).scalar_one() == 1
    assert session.in_transaction()

    with pytest.raises(StopIteration):
        next(generator)

    assert not session.in_transaction()


def test_get_db_closes_the_session_on_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", LOCAL_DATABASE_URL)

    generator = db_session.get_db()
    session = next(generator)
    session.execute(text("SELECT 1"))

    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("boom"))

    assert not session.in_transaction()
