---
type: concept
title: Todo Management
summary: Task CRUD operations, user-scoped queries, list assignment, due dates, and media asset associations.
related: ["assets.md", "lists.md", "security.md", "database.md"]
source_paths:
  - "app/api/todos.py"
  - "app/models/todo.py"
  - "app/schemas/todo.py"
---

# Todo Management

The todo management subsystem provides full CRUD operations for tasks, strictly scoped to the authenticated user. Tasks are organized within user-scoped [todo lists](lists.md), optionally track a `due_date`, and support associating multiple media [assets](assets.md) through a many-to-many relationship.

## Data Models

### Todo (`app/models/todo.py`)
Represents an individual task in the `todos` table:
- `id`: UUID primary key.
- `user_id`: UUID foreign key to `users.id` with cascade deletion, indexed.
- `list_id`: UUID foreign key to `todo_lists.id` with cascade deletion, indexed.
- `title`: `String(255)`, non-nullable.
- `description`: `String(1024)`, optional.
- `is_completed`: `Boolean`, default `False`.
- `due_date`: UTC timestamp (`DateTime(timezone=True)`), optional.
- `created_at`: UTC timestamp.
- `updated_at`: UTC timestamp, automatically refreshed on modification.
- `assets`: Relationship to `Asset` models via the `todo_assets` association table.

### Junction Table: `todo_assets`
A secondary table managing the many-to-many association:
- `todo_id`: UUID foreign key referencing `todos.id` (cascade delete).
- `asset_id`: UUID foreign key referencing `assets.id` (cascade delete).
- Primary key is composite: `(todo_id, asset_id)`.

## Schemas (`app/schemas/todo.py`)

- **`TodoBase`**: Core fields `title` (1–255 chars), optional `description` (up to 1024 chars), and optional `due_date`.
- **`TodoCreate`**: Extends `TodoBase` with optional `list_id` and an optional `asset_ids` list (maximum 10 assets per todo). A validator deduplicates asset IDs while preserving order. If `list_id` is omitted, the task is assigned to the user's default list.
- **`TodoUpdate`**: Partial update schema with optional `list_id` (non-nullable), `title`, `description`, `due_date`, `is_completed`, and `asset_ids` (maximum 10, deduplicated).
- **`TodoRead`**: Serialization model including `id`, `user_id`, `list_id`, `title`, `description`, `is_completed`, `due_date`, `created_at`, `updated_at`, and nested `assets: list[AssetResponse]`.

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
| `POST` | `/todos` | 201 Created | Creates a new task. If `list_id` is specified, verifies it exists and belongs to the user (404 if not); otherwise assigns to the default list. Validates asset IDs if supplied. Eagerly loads assets using `selectinload(Todo.assets)`. |
| `GET` | `/todos` | 200 OK | Lists tasks owned by the current user, optionally filtered by `list_id` query parameter. Sorted by status and due date: uncompleted with due date first (`due_date ASC`), uncompleted without due date, completed last, with `created_at ASC` as tiebreaker. Eagerly loads assets. |
| `GET` | `/todos/{todo_id}` | 200 OK | Retrieves a specific task owned by the user. Returns 404 if not found or owned by someone else. |
| `PATCH` | `/todos/{todo_id}` | 200 OK | Partially updates title, description, due date, completion status, list assignment (`list_id`), or attached assets. Rejects `null` list IDs with 400 and unowned list IDs with 404. Updates `updated_at` to the current UTC timestamp. |
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
     ^          ^          |                  |                   |     | media_type        |
     |          |          |                  v                   |     +-------------------+
     |   +------+----------+----+        +----+--------------+    |
     |   |       TodoList       |        |       Todo        |    |
     |   |----------------------|        |-------------------|    |
     |   | id (PK)              |<-------| list_id (FK)      |    |
     +---| user_id (FK)         |        | id (PK)           |    |
         | name                 |        | user_id (FK)      |----+
         | is_default           |        | title             |
         +----------------------+        | description       |
                                         | is_completed      |
                                         | due_date          |
                                         | assets (rel)      |----+
                                         +-------------------+
```

For list organization rules and deletion safeguards, see [todo lists](lists.md). For media upload workflows and asset metadata storage, see [asset management](assets.md).
