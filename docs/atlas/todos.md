---
type: concept
title: Todo Management
summary: Task CRUD operations, user-scoped queries, and media asset associations.
related: ["assets.md", "security.md", "database.md"]
source_paths:
  - "app/api/todos.py"
  - "app/models/todo.py"
  - "app/schemas/todo.py"
---

# Todo Management

The todo management subsystem provides full CRUD operations for tasks, strictly scoped to the authenticated user. It supports associating multiple media [assets](assets.md) with each task through a many-to-many relationship.

## Data Models

### Todo (`app/models/todo.py`)
Represents an individual task in the `todos` table:
- `id`: UUID primary key.
- `user_id`: UUID foreign key to `users.id` with cascade deletion, indexed.
- `title`: `String(255)`, non-nullable.
- `description`: `String(1024)`, optional.
- `is_completed`: `Boolean`, default `False`.
- `created_at`: UTC timestamp.
- `updated_at`: UTC timestamp, automatically refreshed on modification.
- `assets`: Relationship to `Asset` models via the `todo_assets` association table.

### Junction Table: `todo_assets`
A secondary table managing the many-to-many association:
- `todo_id`: UUID foreign key referencing `todos.id` (cascade delete).
- `asset_id`: UUID foreign key referencing `assets.id` (cascade delete).
- Primary key is composite: `(todo_id, asset_id)`.

## Schemas (`app/schemas/todo.py`)

- **`TodoBase`**: Core fields `title` (1–255 chars) and optional `description` (up to 1024 chars).
- **`TodoCreate`**: Extends `TodoBase` with an optional `asset_ids` list (maximum 10 assets per todo). A validator deduplicates asset IDs while preserving order.
- **`TodoUpdate`**: Partial update schema with optional `title`, `description`, `is_completed`, and `asset_ids` (maximum 10, deduplicated).
- **`TodoRead`**: Serialization model including `id`, `user_id`, `is_completed`, `created_at`, `updated_at`, and nested `assets: list[AssetResponse]`.

## Endpoints and Ownership Enforcement

All operations in `app/api/todos.py` inject `current_user` from [security](security.md), guaranteeing that tasks cannot be accessed or manipulated across user boundaries.

### Asset Validation Helper
Before attaching assets to a todo, `_validate_and_get_assets(db, asset_ids, user_id)` verifies that:
1. Every requested ID exists in the `assets` table.
2. Every requested asset belongs to `user_id`.
3. If any asset is missing or belongs to another user, an HTTP 400 Bad Request (`Invalid asset IDs`) is raised immediately.

### CRUD Endpoints

| Method | Path | Status Code | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/todos` | 201 Created | Creates a new task. If `asset_ids` are supplied, validates ownership and links them. Eagerly loads assets using `selectinload(Todo.assets)` before returning. |
| `GET` | `/todos` | 200 OK | Lists all tasks owned by the current user, ordered by `created_at ASC`, with related assets eagerly loaded. |
| `GET` | `/todos/{todo_id}` | 200 OK | Retrieves a specific task owned by the user. Returns 404 if not found or owned by someone else. |
| `PATCH` | `/todos/{todo_id}` | 200 OK | Partially updates title, description, completion status, or attached assets. Updates `updated_at` to the current UTC timestamp. |
| `DELETE` | `/todos/{todo_id}` | 204 No Content | Deletes the task. Cascade constraints automatically clean up `todo_assets` junction rows. |

## Relationship Diagram

```
+-------------------+             +-----------------------+             +-------------------+
|       User        |             |      todo_assets      |             |       Asset       |
|-------------------|             |-----------------------|             |-------------------|
| id (PK)           |<-----+      | todo_id (PK, FK)      |------>+---->| id (PK)           |
| email             |      |      | asset_id (PK, FK)     |       |     | user_id (FK)      |
| hashed_password   |      |      +-----------------------+       |     | gcs_path          |
+-------------------+      |                  ^                   |     | public_url        |
         ^                 |                  |                   |     | media_type        |
         |                 |                  v                   |     +-------------------+
         |            +----+--------------+                       |
         +------------|       Todo        |                       |
                      |-------------------|                       |
                      | id (PK)           |                       |
                      | user_id (FK)      |                       |
                      | title             |                       |
                      | description       |                       |
                      | is_completed      |                       |
                      | assets (rel)      |-----------------------+
                      +-------------------+
```

For media upload workflows and asset metadata storage, see [asset management](assets.md).
