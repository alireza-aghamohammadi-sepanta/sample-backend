"""Database engine, session factory and FastAPI dependency.

By default connections are opened through the Cloud SQL Python Connector with
IAM database authentication, so no static database password is ever needed.
Setting ``DATABASE_URL`` bypasses the connector and builds a plain SQLAlchemy
engine from that URL, which keeps local development and tests runnable without
GCP credentials.
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

_connector: Any = None
_engine: Engine | None = None

# Bound lazily by :func:`get_engine`, so importing this module never triggers
# configuration loading or a connection attempt.
SessionLocal = sessionmaker(autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models."""


def get_connector() -> Any:
    """Return the process wide Cloud SQL connector, creating it on first use."""
    global _connector

    if _connector is None:
        # Imported lazily so the connector (and its credentials lookup) is only
        # required when Cloud SQL is actually used.
        from google.cloud.sql import connector as connector_module

        _connector = connector_module.Connector()

    return _connector


def connect_with_connector() -> Any:
    """Open a new pg8000 connection to Cloud SQL using IAM authentication."""
    settings = get_settings()

    db_user = os.environ.get(DB_USER_ENV)
    if not db_user:
        raise ConfigError(
            f"Missing configuration: set the {DB_USER_ENV} environment variable "
            "to the IAM database user used to reach Cloud SQL"
        )

    return get_connector().connect(
        settings.database_instance,
        "pg8000",
        user=db_user,
        db=os.environ.get(DB_NAME_ENV, DEFAULT_DB_NAME),
        enable_iam_auth=True,
    )


def create_db_engine() -> Engine:
    """Build the SQLAlchemy engine for the current environment."""
    database_url = os.environ.get(DATABASE_URL_ENV)
    if database_url:
        return create_engine(database_url, pool_pre_ping=True)

    return create_engine(
        CLOUD_SQL_URL,
        creator=connect_with_connector,
        pool_pre_ping=True,
    )


def get_engine() -> Engine:
    """Return the cached engine, creating and binding it on first use."""
    global _engine

    if _engine is None:
        _engine = create_db_engine()
        SessionLocal.configure(bind=_engine)

    return _engine


def reset_engine() -> None:
    """Dispose of the cached engine and connector.

    Mainly useful for tests and for reloading configuration at runtime.
    """
    global _connector, _engine

    if _engine is not None:
        _engine.dispose()
        _engine = None

    if _connector is not None:
        _connector.close()
        _connector = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = SessionLocal(bind=get_engine())
    try:
        yield session
    finally:
        session.close()
