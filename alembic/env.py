"""Alembic environment.

The engine is built by :func:`app.db.session.create_db_engine`, so migrations
reach the database exactly like the application does: through the Cloud SQL
Python Connector with IAM authentication, or through ``DATABASE_URL`` when that
environment variable is set.
"""

from logging.config import fileConfig

from alembic import context

# ``app.models`` is imported for its side effect: every model registers itself
# on ``Base.metadata``, which autogenerate compares against the database.
import app.models  # noqa: F401
from app.db.session import Base, create_db_engine

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to the script output instead of connecting; the URL is taken
    from the engine the application would build.
    """
    engine = create_db_engine()
    try:
        url = engine.url.render_as_string(hide_password=False)
    finally:
        engine.dispose()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode, against a live connection."""
    connectable = create_db_engine()

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
