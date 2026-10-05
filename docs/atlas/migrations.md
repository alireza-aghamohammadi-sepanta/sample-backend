---
type: concept
title: Database Schema and Migrations
summary: Alembic migration setup, offline and online execution modes, and database schema revision history.
related: ["database.md", "auth.md", "todos.md", "assets.md", "lists.md"]
source_paths:
  - "alembic.ini"
  - "alembic/env.py"
  - "migrations/versions/001_initial_user_table.py"
  - "migrations/versions/002_add_password_reset_tokens.py"
  - "migrations/versions/003_add_todos_table.py"
  - "migrations/versions/004_add_assets_table.py"
  - "migrations/versions/005_add_todo_assets_table.py"
  - "migrations/versions/006_add_due_date_to_todos.py"
  - "migrations/versions/007_add_todo_lists_table.py"
---

# Database Schema and Migrations

Database schema versioning is managed with [Alembic](https://alembic.sqlalchemy.org/). Alembic is configured to run against either live Cloud SQL instances or local databases using the same connection mechanisms as the application.

## Migration Architecture

The Alembic configuration is defined across:
- `alembic.ini`: Root configuration pointing to `script_location = %(here)s/alembic` and `version_locations = %(here)s/migrations/versions`.
- `alembic/env.py`: Environment script integrating with `app.db.session.create_db_engine`.

### Online vs. Offline Execution
In `alembic/env.py`:
- **`run_migrations_online()`**: Opens a live connection via `app.db.session.create_db_engine()`. This supports both Cloud SQL IAM authentication and local `DATABASE_URL` strings (see [database](database.md)).
- **`run_migrations_offline()`**: Renders DDL scripts directly to standard output or SQL files without connecting to a database engine, obtaining URL metadata from `engine.url.render_as_string()`.

All models from `app.models` are imported in `alembic/env.py` to register metadata with `Base.metadata`, enabling Alembic autogenerate to detect schema discrepancies.

## Schema Version History

The database schema has evolved through sequential revisions:

```
  001 (initial user table)
       │
       ▼
  002 (add password reset tokens)
       │
       ▼
  003 (add todos table)
       │
       ▼
  004 (add assets table)
       │
       ▼
  005 (add todo_assets table)
       │
       ▼
  006 (add due date to todos)
       │
       ▼
  007 (add todo lists table)
```

### Revision Details

#### 1. `001_initial_user_table.py` (`001`)
- **Table**: `users`
- **Columns**:
  - `id`: `sa.Uuid()`, Primary Key.
  - `email`: `sa.String(320)`, non-nullable, unique index `ix_users_email`.
  - `hashed_password`: `sa.String(255)`, non-nullable.
  - `created_at`: `sa.DateTime(timezone=True)`, server default `now()`.
- Supports the core account system in [authentication](auth.md).

#### 2. `002_add_password_reset_tokens.py` (`002`)
- **Table**: `password_reset_tokens`
- **Columns**:
  - `id`: `sa.Uuid()`, Primary Key.
  - `user_id`: `sa.Uuid()`, Foreign Key referencing `users.id` with `ondelete="CASCADE"`, indexed `ix_password_reset_tokens_user_id`.
  - `token_hash`: `sa.String(255)`, non-nullable, indexed `ix_password_reset_tokens_token_hash`.
  - `expires_at`: `sa.DateTime(timezone=True)`, non-nullable.
  - `created_at`: `sa.DateTime(timezone=True)`, server default `now()`.
  - `used_at`: `sa.DateTime(timezone=True)`, nullable.
- Stores hashed single-use tokens for password recovery in [authentication](auth.md).

#### 3. `003_add_todos_table.py` (`003`)
- **Table**: `todos`
- **Columns**:
  - `id`: `sa.Uuid()`, Primary Key.
  - `user_id`: `sa.Uuid()`, Foreign Key referencing `users.id` with `ondelete="CASCADE"`, indexed `ix_todos_user_id`.
  - `title`: `sa.String(255)`, non-nullable.
  - `description`: `sa.String(1024)`, nullable.
  - `is_completed`: `sa.Boolean()`, server default `false`, non-nullable.
  - `created_at`: `sa.DateTime(timezone=True)`, server default `now()`.
  - `updated_at`: `sa.DateTime(timezone=True)`, server default `now()`.
- Implements the task entity for [todos](todos.md).

#### 4. `004_add_assets_table.py` (`004`)
- **Table**: `assets`
- **Columns**:
  - `id`: `sa.Uuid()`, Primary Key.
  - `user_id`: `sa.Uuid()`, Foreign Key referencing `users.id` with `ondelete="CASCADE"`, indexed `ix_assets_user_id`.
  - `gcs_path`: `sa.String(1024)`, non-nullable.
  - `public_url`: `sa.String(2048)`, non-nullable.
  - `media_type`: `sa.String(255)`, non-nullable.
  - `created_at`: `sa.DateTime(timezone=True)`, server default `now()`.
- Tracks uploaded media metadata in [assets](assets.md).

#### 5. `005_add_todo_assets_table.py` (`005`)
- **Table**: `todo_assets`
- **Columns**:
  - `todo_id`: `sa.Uuid()`, Foreign Key referencing `todos.id` with `ondelete="CASCADE"`, part of composite Primary Key.
  - `asset_id`: `sa.Uuid()`, Foreign Key referencing `assets.id` with `ondelete="CASCADE"`, part of composite Primary Key.
- Serves as the many-to-many junction table associating media assets with todo items.

#### 6. `006_add_due_date_to_todos.py` (`006`)
- **Table Alteration**: `todos`
- **Columns Added**:
  - `due_date`: `sa.DateTime(timezone=True)`, nullable.
- Adds scheduling and deadline tracking support to tasks in [todos](todos.md).

#### 7. `007_add_todo_lists_table.py` (`007`)
- **Table**: `todo_lists`
- **Columns**:
  - `id`: `sa.Uuid()`, Primary Key.
  - `user_id`: `sa.Uuid()`, Foreign Key referencing `users.id` with `ondelete="CASCADE"`, indexed `ix_todo_lists_user_id`.
  - `name`: `sa.String(255)`, non-nullable.
  - `is_default`: `sa.Boolean()`, server default `false`, non-nullable.
  - `created_at`: `sa.DateTime(timezone=True)`, server default `now()`.
  - `updated_at`: `sa.DateTime(timezone=True)`, server default `now()`.
- **Constraint / Index**:
  - Expression index `uq_todo_lists_user_id_lower_name` enforcing unique lowercase list names per user.
- **Table Alteration**: `todos`
  - Adds `list_id`: `sa.Uuid()`, Foreign Key referencing `todo_lists.id` with `ondelete="CASCADE"`, indexed `ix_todos_list_id`, non-nullable.
- **Data Backfill**:
  - Provisions a default "Inbox" list (`is_default=True`) for all existing users.
  - Backfills existing `todos.list_id` to each user's default inbox list before marking the column non-nullable.
- Implements list grouping in [todo lists](lists.md).
