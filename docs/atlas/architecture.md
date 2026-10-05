---
type: concept
title: Application Architecture & Lifecycle
summary: FastAPI application setup, lifespan management, and shared dependency injection via app state.
related: ["config.md", "database.md", "storage.md", "email.md", "auth.md", "lists.md", "todos.md", "assets.md", "deployment.md"]
source_paths: ["main.py"]
---

# Application Architecture & Lifecycle

The application is a modern asynchronous REST API built with [FastAPI](https://fastapi.tiangolo.com/) and Python 3.13. It implements a layered architecture separating routing, domain models, schemas, and shared infrastructure services.

## Application Entrypoint and Lifespan

The central entrypoint is defined in `main.py`. The application uses FastAPI's `lifespan` async context manager to manage process-wide dependencies rather than relying on global variables.

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.db_manager = DatabaseManager()
    app.state.storage_service = _build_storage_service()
    app.state.email_service = build_email_service()
    try:
        yield
    finally:
        app.state.db_manager.shutdown()
        _close_storage_service(app.state.storage_service)
```

### Shared Dependencies on `app.state`

1. **`db_manager`** (`DatabaseManager`):
   - Created on startup via [database](database.md) connection management.
   - Connects lazily to PostgreSQL (either directly or via the Cloud SQL Python Connector with IAM auth).
   - Properly disposed during shutdown via `app.state.db_manager.shutdown()`.

2. **`storage_service`** (`StorageService | None`):
   - Created via `_build_storage_service()`, which delegates to [storage](storage.md).
   - Designed to be completely optional at boot: if GCS bucket configuration or credentials are not present, `_build_storage_service()` catches the exception and sets `app.state.storage_service = None`.
   - This ensures the application can still boot locally and in test environments, and Cloud Run health checks can succeed even if storage dependencies fail.
   - On shutdown, `_close_storage_service()` closes any underlying HTTP client sessions if supported.

3. **`email_service`** (`EmailService`):
   - Instantiated via `build_email_service()` from the [email service](email.md).
   - Handled across requests for transactional communications such as password reset links.

## Router Registration

The application mounts four feature routers under the top-level FastAPI instance:
- `app.include_router(auth.router)`: [Authentication](auth.md) endpoints for user registration, login, and password resets.
- `app.include_router(assets.router)`: [Asset management](assets.md) endpoints for signed URL generation and upload confirmation.
- `app.include_router(lists.router)`: [Todo list management](lists.md) endpoints for creating, listing, renaming, and deleting todo lists.
- `app.include_router(todos.router)`: [Todo management](todos.md) endpoints for creating, listing, updating, and deleting todo items.

## Health Checks and Base Endpoints

`main.py` provides two unauthenticated top-level routes:
- `GET /`: Returns `{"message": "Hello World"}`.
- `GET /healthz`: Returns `{"status": "ok"}` for container orchestration and Cloud Run liveness/readiness probes. Because startup does not eagerly connect to GCP services, health check probes respond immediately upon application launch.

## Layered Design

```
+-------------------------------------------------------------+
|                        HTTP Clients                         |
+-------------------------------------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                      FastAPI Routers                        |
| (app/api/auth.py, app/api/assets.py, lists.py, todos.py)   |
+-------------------------------------------------------------+
          |                            |                |
          v                            v                v
+-------------------+        +-------------------+  +-------------------+
|  Pydantic Schemas |        | Security / Auth   |  | Infrastructure    |
|   (app/schemas/)  |        | (app/core/        |  | Services          |
|                   |        |   security.py)    |  | (Storage, Email)  |
+-------------------+        +-------------------+  +-------------------+
                                       |                      |
                                       v                      |
                             +-------------------+            |
                             | SQLAlchemy Models |            |
                             |   (app/models/)   |            |
                             +-------------------+            |
                                       |                      |
                                       v                      v
                             +-------------------+  +-------------------+
                             | DatabaseManager   |  | Google Cloud      |
                             | (app/db/          |  | Storage (GCS)     |
                             |   session.py)     |  |                   |
                             +-------------------+  +-------------------+
```

For configuration management and credentials resolution across these layers, see [configuration](config.md). For details on container packaging and deployment execution, see [deployment](deployment.md).
