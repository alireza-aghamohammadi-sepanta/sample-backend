---
type: concept
title: Asset Management
summary: Two-step upload pattern, media metadata persistence, ownership checks, and asset listing.
related: ["storage.md", "todos.md", "security.md", "database.md"]
source_paths:
  - "app/api/assets.py"
  - "app/models/asset.py"
  - "app/schemas/asset.py"
---

# Asset Management

The asset subsystem manages user-uploaded media files (images and videos). Rather than proxying large binary payloads through the application server, it coordinates a direct-to-cloud two-step upload process with Google Cloud Storage via [storage](storage.md).

## Data Models

### Asset (`app/models/asset.py`)
Tracks uploaded file metadata in the `assets` table:
- `id`: UUID primary key.
- `user_id`: UUID foreign key to `users.id` with cascade deletion, indexed.
- `gcs_path`: `String(1024)`, full object key in Cloud Storage (e.g. `users/{user_id}/{media_type}/{uuid}.{ext}`).
- `public_url`: `String(2048)`, public HTTPS URL where the asset is served.
- `media_type`: `String(255)`, stored media type (`image` or `video`).
- `created_at`: UTC timestamp.
- `todos`: Many-to-many relationship linking this asset to [todos](todos.md).

## Schemas (`app/schemas/asset.py`)

- **`MediaType`**: Enum supporting `image` and `video`.
- **`SignedURLRequest`**: Request payload containing `media_type` and `file_extension` (normalized to lowercase without leading periods).
- **`SignedURLResponse`**: Returns `upload_url` (the signed PUT URL) and `gcs_path` (the target object path).
- **`AssetConfirmRequest`**: Contains `gcs_path` confirming that the client has completed the direct PUT upload.
- **`AssetResponse`**: Public serialization model exposing `id`, `user_id`, `gcs_path`, `public_url`, `media_type`, and `created_at`.

## Two-Step Upload Pattern

```
 Client                          API Server                        Cloud Storage
   |                                 |                                   |
   | 1. POST /assets/signed-url      |                                   |
   |-------------------------------->|                                   |
   |                                 | storage.build_object_path(...)    |
   |                                 | storage.generate_signed_url(...)  |
   | 2. Signed URL & GCS Path        |                                   |
   |<--------------------------------|                                   |
   |                                                                     |
   | 3. HTTP PUT (binary file payload)                                   |
   |-------------------------------------------------------------------->|
   | 4. HTTP 200 OK                                                      |
   |<--------------------------------------------------------------------|
   |                                                                     |
   | 5. POST /assets/confirm { gcs_path }                                |
   |-------------------------------->|                                   |
   |                                 | Verify key prefix matches user    |
   |                                 | Check storage.file_exists(path)   |
   |                                 | Persist Asset to database         |
   | 6. HTTP 200 (Asset metadata)    |                                   |
   |<--------------------------------|                                   |
```

### 1. Requesting a Signed URL (`POST /assets/signed-url`)
- Caller authenticates via `current_user` ([security](security.md)).
- The endpoint delegates to `storage.build_object_path()` to generate an unguessable path scoped under `users/{user_id}/{media_type}/{uuid}.{extension}`.
- Calls `storage.generate_signed_url()` to obtain a V4 signed URL with a 15-minute expiration and upload size constraint (maximum 500 MB).

### 2. Confirming an Upload (`POST /assets/confirm`)
- Once the upload to GCS completes, the client sends `POST /assets/confirm` with `gcs_path`.
- **Double-Check Validation**:
  1. **Ownership**: `storage.get_owned_media_type(payload.gcs_path, user_id)` verifies that the path matches the caller's user UUID. If mismatched, returns HTTP 403 Forbidden (`gcs_path is not an upload path of the authenticated user`).
  2. **Physical Existence**: `storage.file_exists(payload.gcs_path)` verifies that the file actually exists in the Cloud Storage bucket. If missing, returns HTTP 404 Not Found (`Uploaded file not found`).
- Upon passing both checks, a new `Asset` record is saved to the database via [database](database.md).

### 3. Listing Assets (`GET /assets`)
- Returns all assets belonging to the authenticated user ordered by `created_at ASC`.

For low-level GCS URL signing and path construction details, see [storage](storage.md).
