"""Database engine, session factory and FastAPI dependency.

By default connections are opened through the Cloud SQL Python Connector with
IAM database authentication, so no static database password is ever needed.
Setting ``DATABASE_URL`` bypasses the connector and builds a plain SQLAlchemy
engine from that URL, which keeps local development and tests runnable without
GCP credentials.

:class:`DatabaseManager` owns the engine, the connector and the session factory
of one application, so their lifecycle is explicit and several managers (a real
one and a test one, say) can coexist. The module level entrypoints delegate to a
default manager, which keeps importing this module free of configuration loading
and of connection attempts.
"""

import os
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import ConfigError, get_settings

DATABASE_URL_ENV = "DATABASE_URL"
DB_USER_ENV = "DB_USER"
DB_NAME_ENV = "DB_NAME"

DEFAULT_DB_NAME = "postgres"

# URL without host: pg8000 is only used as the DBAPI, the actual socket is
# provided by the Cloud SQL connector through the engine ``creator``.
CLOUD_SQL_URL = "postgresql+pg8000://"


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models."""


class DatabaseManager:
    """Lifecycle owner of one engine, one connector and one session factory.

    Nothing is built by the constructor: the connector is created on the first
    connection, the engine on the first :meth:`get_engine`, and
    :meth:`shutdown` releases both.
    """

    def __init__(self) -> None:
        self._connector: Any = None
        self._engine: Engine | None = None
        #: Bound lazily by :meth:`get_engine`, never at construction time.
        self.session_factory = sessionmaker(autocommit=False, autoflush=False)

    def get_connector(self) -> Any:
        """Return this manager's Cloud SQL connector, created on first use."""
        if self._connector is None:
            # Imported lazily so the connector (and its credentials lookup) is
            # only required when Cloud SQL is actually used.
            from google.cloud.sql import connector as connector_module

            self._connector = connector_module.Connector()

        return self._connector

    def connect_with_connector(self) -> Any:
        """Open a new pg8000 connection to Cloud SQL using IAM authentication."""
        settings = get_settings()

        db_user = os.environ.get(DB_USER_ENV)
        if not db_user:
            raise ConfigError(
                f"Missing configuration: set the {DB_USER_ENV} environment variable "
                "to the IAM database user used to reach Cloud SQL"
            )

        return self.get_connector().connect(
            settings.database_instance,
            "pg8000",
            user=db_user,
            db=os.environ.get(DB_NAME_ENV, DEFAULT_DB_NAME),
            enable_iam_auth=True,
        )

    def create_engine(self) -> Engine:
        """Build a new SQLAlchemy engine for the current environment."""
        database_url = os.environ.get(DATABASE_URL_ENV)
        if database_url:
            return create_engine(database_url, pool_pre_ping=True)

        return create_engine(
            CLOUD_SQL_URL,
            creator=self.connect_with_connector,
            pool_pre_ping=True,
        )

    def get_engine(self) -> Engine:
        """Return the cached engine, creating and binding it on first use."""
        if self._engine is None:
            self._engine = self.create_engine()
            self.session_factory.configure(bind=self._engine)

        return self._engine

    def create_session(self) -> Session:
        """Return a new session bound to this manager's engine."""
        return self.session_factory(bind=self.get_engine())

    def shutdown(self) -> None:
        """Dispose of the engine and close the connector.

        Safe to call more than once; a later use rebuilds both.
        """
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

        if self._connector is not None:
            self._connector.close()
            self._connector = None


# Default manager behind the module level entrypoints below.
_manager = DatabaseManager()

SessionLocal = _manager.session_factory


def get_connector() -> Any:
    """Return the process wide Cloud SQL connector, creating it on first use."""
    return _manager.get_connector()


def connect_with_connector() -> Any:
    """Open a new pg8000 connection to Cloud SQL using IAM authentication."""
    return _manager.connect_with_connector()


def create_db_engine() -> Engine:
    """Build the SQLAlchemy engine for the current environment."""
    return _manager.create_engine()


def get_engine() -> Engine:
    """Return the cached engine, creating and binding it on first use."""
    return _manager.get_engine()


def reset_engine() -> None:
    """Dispose of the cached engine and connector.

    Mainly useful for tests and for reloading configuration at runtime.
    """
    _manager.shutdown()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = _manager.create_session()
    try:
        yield session
    finally:
        session.close()
