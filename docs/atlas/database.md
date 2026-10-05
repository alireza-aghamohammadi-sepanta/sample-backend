---
type: concept
title: Database Management and Sessions
summary: SQLAlchemy engine lifecycle, lazy Cloud SQL IAM connection pooling, and FastAPI session dependency injection.
related: ["migrations.md", "architecture.md", "config.md", "auth.md", "todos.md", "lists.md"]
source_paths: ["app/db/session.py"]
---

# Database Management and Sessions

Database connectivity, connection lifecycle, and SQLAlchemy session management are defined in `app/db/session.py`. The system supports both direct database connections (for local development and CI testing) and IAM-authenticated connections via the Cloud SQL Python Connector (for Google Cloud deployments).

## Declarative Base

All ORM entities inherit from the SQLAlchemy 2.0 declarative base:

```python
class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models."""
```

Models registered under `Base` include `User`, `PasswordResetToken`, `Todo`, `TodoList`, and `Asset`.

## `DatabaseManager` Architecture

Rather than relying on global mutable database engines, the backend uses `DatabaseManager` to encapsulate:
- The SQLAlchemy `Engine` instance.
- The Cloud SQL `Connector` instance.
- The sessionmaker factory (`session_factory`).

```python
class DatabaseManager:
    def __init__(self) -> None:
        self._connector: Any = None
        self._engine: Engine | None = None
        self.session_factory = sessionmaker(autocommit=False, autoflush=False)
```

### Lazy Initialization
Neither the engine nor the connector is built at instantiation time:
- The engine is constructed on the first call to `get_engine()`.
- The Cloud SQL connector is initialized on the first connection attempt via `get_connector()`.
- Lazy initialization allows importing database models and modules without requiring database network access or configuration validation upfront.

### Connection Modes

The engine is built using `DatabaseManager.create_engine()`:

1. **Direct Connection (`DATABASE_URL`)**:
   - If the `DATABASE_URL` environment variable is defined, SQLAlchemy constructs an engine directly using `create_engine(database_url, pool_pre_ping=True)`.
   - Used for unit/integration testing (e.g. SQLite or local PostgreSQL) without GCP credentials.

2. **Cloud SQL IAM Connector**:
   - If `DATABASE_URL` is not set, the manager connects to Cloud SQL via `CLOUD_SQL_URL = "postgresql+pg8000://"`.
   - The connection factory is delegated to `connect_with_connector`:
     ```python
     self.get_connector().connect(
         settings.database_instance,
         "pg8000",
         user=db_user,
         db=os.environ.get(DB_NAME_ENV, "postgres"),
         enable_iam_auth=True,
     )
     ```
   - Cloud SQL IAM database authentication eliminates the need for hardcoded or static database passwords. The IAM database username is provided via `DB_USER`.

### Shutdown and Resource Cleanup
Calling `shutdown()` disposes of the connection pool via `self._engine.dispose()` and closes the Cloud SQL connector via `self._connector.close()`. This method is invoked by the FastAPI lifespan handler during process termination (see [architecture](architecture.md)).

## Dependency Injection: `get_db`

FastAPI endpoints consume database sessions via the `get_db` generator dependency:

```python
def manager_for_request(request: Request) -> DatabaseManager:
    manager = getattr(request.app.state, "db_manager", None)
    return _manager if manager is None else manager

def get_db(request: Request) -> Iterator[Session]:
    session = manager_for_request(request).create_session()
    try:
        yield session
    finally:
        session.close()
```

- If the application was initialized with a lifespan context, the session is created from `request.app.state.db_manager`.
- If invoked outside a standard lifespan (such as an unmanaged `TestClient(app)`), it gracefully falls back to the default process-wide `_manager`.
- The `finally` block guarantees that sessions are closed after the HTTP response is completed, preventing connection pool leaks.

For information on how schema changes and database migrations are managed, see [database migrations](migrations.md).
