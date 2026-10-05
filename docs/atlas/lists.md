---
type: concept
title: Todo Lists Management
summary: User-scoped todo lists, default Inbox provisioning, and task reassignment on deletion.
related: ["todos.md", "auth.md", "security.md", "database.md"]
source_paths:
  - "app/api/lists.py"
  - "app/models/todo_list.py"
  - "app/schemas/todo_list.py"
---

# Todo Lists Management

The todo lists subsystem allows users to organize their [todos](todos.md) into distinct lists. Every user is automatically provisioned with a default "Inbox" list upon registration in [authentication](auth.md), and users can create, rename, and delete custom lists.

## Data Models

### TodoList (`app/models/todo_list.py`)
Represents a task list in the `todo_lists` table:
- `id`: UUID primary key, generated via `uuid.uuid4`.
- `user_id`: UUID foreign key referencing `users.id` with cascade deletion, indexed (`ix_todo_lists_user_id`).
- `name`: `String(255)`, non-nullable list display name.
- `is_default`: `Boolean`, default `False`. Identifies the system-created fallback list.
- `created_at`: UTC timestamp, server default `now()`.
- `updated_at`: UTC timestamp, automatically refreshed on modification.

### Uniqueness Constraint
List names are unique per user on a case-insensitive basis:
- Enforced at the database level by the unique index `uq_todo_lists_user_id_lower_name` on `(user_id, lower(name))`.
- Enforced at the application level during creation and rename operations.

## Schemas (`app/schemas/todo_list.py`)

- **`TodoListBase`**: Base schema containing `name` (1–255 characters).
- **`TodoListCreate`**: Inherits `TodoListBase` for creating a new list.
- **`TodoListUpdate`**: Inherits `TodoListBase` for renaming an existing list.
- **`TodoListRead`**: Serialization model including `id`, `user_id`, `name`, `is_default`, `created_at`, and `updated_at`.

## Endpoints and Ownership Enforcement

All endpoints in `app/api/lists.py` authenticate callers using the `current_user` dependency from [security](security.md) and query records scoped strictly to the authenticated `user_id`.

### Endpoints Summary

| Method | Path | Status Code | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/lists` | 201 Created | Creates a custom list (`is_default=False`). Checks for case-insensitive duplicate names and returns 409 Conflict if matched. |
| `GET` | `/lists` | 200 OK | Lists all lists owned by the caller. Pins the default list first, followed by custom lists in alphabetical order (`lower(name) ASC`). |
| `PATCH` | `/lists/{id}` | 200 OK | Renames a list (custom or default). Returns 404 Not Found if missing or unowned, and 409 Conflict if the new name duplicates an existing list case-insensitively. |
| `DELETE` | `/lists/{id}` | 204 No Content | Deletes a custom list. Atomically reassigns all contained tasks to the user's default list. Returns 400 Bad Request if attempting to delete the default list. |

### Lifecycle and Safety Rules

1. **Default Inbox Provisioning**: During user registration (`POST /signup`), a default list named "Inbox" with `is_default=True` is automatically inserted alongside the user record.
2. **Default List Deletion Guard**: Calling `DELETE /lists/{id}` on a list with `is_default=True` raises an HTTP 400 Bad Request (`Cannot delete default list`).
3. **Atomic Task Reassignment**: When a custom list is deleted, all tasks with `Todo.list_id == id` belonging to that user are reassigned via `UPDATE todos SET list_id = :default_list_id` in the same transaction before deleting the list row, ensuring tasks are never orphaned.

