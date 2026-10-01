---
okf_version: '0.2'
generated_at_commit: 9de408116fc17e93e830f22f43ea3ffd9ad4a287
---
# Sample Backend Documentation

- [architecture](architecture.md) — ASGI application lifecycle, shared dependency injection, and layered architecture.
- [config](config.md) — environment variables and Google Cloud Secret Manager runtime configuration.
- [security](security.md) — Argon2 password hashing, JWT authentication tokens, and request authorization.
- [database](database.md) — SQLAlchemy engine lifecycle, Cloud SQL IAM connector, and session dependencies.
- [migrations](migrations.md) — Alembic database migrations and relational schema revision history.
- [auth](auth.md) — user registration, login, and password recovery workflows.
- [todos](todos.md) — task management CRUD endpoints and media asset associations.
- [assets](assets.md) — direct-to-cloud media upload pattern and asset metadata tracking.
- [storage](storage.md) — Google Cloud Storage client abstraction, V4 signed URLs, and tenant path isolation.
- [email](email.md) — transactional email dispatch service with test inspection support.
- [deployment](deployment.md) — multi-stage Docker packaging with uv and Cloud Run production readiness.
