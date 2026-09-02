"""ASGI application: routers, health checks and process wide lifecycle.

The dependencies shared by the whole API - the database manager and the Cloud
Storage service - are built once by the lifespan handler and published on
``app.state``, so the application owns them instead of module level globals,
and shutdown can release them.

Startup never contacts GCP: the database manager connects lazily, and a storage
service that cannot be built (no bucket configured, no credentials available)
is simply absent, which keeps the app startable locally and in tests.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import assets, auth
from app.db.session import DatabaseManager
from app.services.email import build_email_service
from app.services.storage import StorageService, build_storage_service


def _build_storage_service() -> StorageService | None:
    """Return the shared storage service, or ``None`` when unavailable.

    Cloud Storage is optional: the endpoints that need it fail on their own
    terms, while the rest of the API - and the health checks Cloud Run polls -
    must keep serving.
    """
    try:
        return build_storage_service()
    except Exception:  # noqa: BLE001 - storage stays optional at startup
        return None


def _close_storage_service(service: StorageService | None) -> None:
    """Close the client of ``service``, if there is one and it can be closed."""
    if service is None:
        return

    close = getattr(service.client, "close", None)
    if close is not None:
        close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the shared dependencies on startup, release them on shutdown."""
    app.state.db_manager = DatabaseManager()
    app.state.storage_service = _build_storage_service()
    app.state.email_service = build_email_service()
    try:
        yield
    finally:
        app.state.db_manager.shutdown()
        _close_storage_service(app.state.storage_service)


app = FastAPI(lifespan=lifespan)

app.include_router(auth.router)
app.include_router(assets.router)

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
