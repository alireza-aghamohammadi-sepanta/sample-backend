---
type: concept
title: Deployment and Containerization
summary: Multi-stage Docker packaging with Astral uv, runtime dependencies, and Cloud Run production readiness.
related: ["architecture.md", "config.md", "database.md"]
source_paths:
  - "Dockerfile"
  - "pyproject.toml"
---

# Deployment and Containerization

The backend is packaged as an OCI-compliant container image optimized for deployment on Google Cloud Run or Kubernetes. Packaging is managed via a multi-stage Docker build utilizing [uv](https://docs.astral.sh/uv/) for fast and deterministic dependency resolution.

## Multi-Stage Dockerfile

The `Dockerfile` consists of two stages: a builder stage utilizing `uv` and a lightweight runtime stage based on `python:3.13-slim`.

```dockerfile
# Stage 1: Build virtual environment
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Cache dependency layer
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime image
FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY main.py /app/

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["fastapi", "run", "main.py", "--port", "8000", "--host", "0.0.0.0"]
```

### Build Optimizations
- **Bytecode Compilation (`UV_COMPILE_BYTECODE=1`)**: Precompiles Python bytecode (`.pyc`), speeding up container startup times on Cloud Run cold starts.
- **Cache Mounts (`--mount=type=cache`)**: Caches wheel downloads across image builds.
- **Layer Separation**: Installs external dependencies first using `uv.lock` and `pyproject.toml` before copying application code, ensuring Docker cache hits when source files change.
- **Minimal Final Image**: The compiler, git, and build tooling from the builder stage are omitted from the final image, minimizing image size and attack surface.

## Runtime Dependencies

Production dependencies defined in `pyproject.toml` include:
- `fastapi[standard]>=0.141.1`: ASGI web framework and server tooling.
- `sqlalchemy>=2.0.52`: Database ORM and query builder (see [database](database.md)).
- `alembic>=1.19.1`: Database migrations runner (see [migrations](migrations.md)).
- `cloud-sql-python-connector[pg8000]>=1.22.0`: IAM database connectivity to Cloud SQL.
- `google-cloud-secret-manager>=2.30.0`: Dynamic secret retrieval (see [configuration](config.md)).
- `google-cloud-storage>=3.13.1`: Cloud Storage client for asset handling (see [storage](storage.md)).
- `passlib[argon2]>=1.7.4`: Password hashing with Argon2.
- `pyjwt>=2.13.0`: JWT token generation and verification (see [security](security.md)).

## Production Execution on Cloud Run

The container entrypoint executes:
```bash
fastapi run main.py --port 8000 --host 0.0.0.0
```
- **Non-blocking startup**: As described in [architecture](architecture.md), external cloud services (Cloud SQL and GCS) do not block application startup.
- **Readiness Probes**: Cloud Run polls `/healthz`, which answers with HTTP 200 immediately upon server boot without waiting for database handshake or cloud credentials.
- **IAM Authentication**: When deployed in Google Cloud with an attached Service Account, Cloud SQL IAM database authentication and Secret Manager access work out of the box without requiring static keys or passwords.
