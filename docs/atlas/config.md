---
type: concept
title: Configuration and Secrets Management
summary: Application configuration loading from local environment variables with automatic fallback to GCP Secret Manager.
related: ["architecture.md", "database.md", "security.md", "storage.md"]
source_paths: ["app/core/config.py"]
---

# Configuration and Secrets Management

Configuration settings for the backend are defined and resolved in `app/core/config.py`. The system supports hybrid local and cloud execution: local environment variables take precedence, with automatic fallback to Google Cloud Secret Manager when running in GCP.

## Settings Model

The runtime settings are encapsulated in the immutable `@dataclass(frozen=True) Settings` class:

```python
@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    database_instance: str
    gcp_project_id: str | None = None
    gcs_bucket_name: str | None = None
    gcs_service_account_info: str | None = None
```

### Configuration Parameters

| Parameter | Environment Variable | Secret Manager Env Variable | Purpose |
| :--- | :--- | :--- | :--- |
| `jwt_secret` | `JWT_SECRET` | `JWT_SECRET_NAME` | Secret key used for signing and decoding HS256 tokens in [security](security.md). |
| `database_instance` | `DATABASE_INSTANCE` | `DATABASE_INSTANCE_SECRET_NAME` | Cloud SQL instance connection name (`project:region:instance`) used by [database](database.md). |
| `gcp_project_id` | `GCP_PROJECT_ID` | N/A | GCP project identifier. If set, triggers fallback to Secret Manager for unresolved secrets. |
| `gcs_bucket_name` | `GCS_BUCKET_NAME` | N/A | Target Cloud Storage bucket name for uploaded media handled by [storage](storage.md). |
| `gcs_service_account_info` | `GCS_SERVICE_ACCOUNT_INFO` | N/A | Optional raw JSON credentials for authenticating and signing GCS URLs outside GCP. |

## Resolution Strategy

Settings are resolved using `_resolve()`:
1. **Direct Environment Variables**: Checks `os.environ[env_var]`. If present and non-empty, it is returned immediately.
2. **GCP Secret Manager Fallback**: If the environment variable is absent and `GCP_PROJECT_ID` is set:
   - Secret Manager client is initialized lazily using `google.cloud.secretmanager.SecretManagerServiceClient()`.
   - The secret name is determined by `os.environ.get(secret_name_env_var, env_var)`.
   - The secret version is requested at `projects/{project_id}/secrets/{secret_name}/versions/latest`.
   - If Secret Manager cannot be accessed, a `ConfigError` is raised.
3. **Missing Configuration**: If neither an environment variable nor a GCP project is configured to look up the secret, a `ConfigError` is raised with a descriptive error message.

```
       +-------------------------------+
       |       os.environ[ENV_VAR]      |
       +-------------------------------+
                   |
           Found? / \ Not Found
                /     \
               v       v
         +-------+   +-------------------+
         | Return|   | GCP_PROJECT_ID    |
         | Value |   | configured?       |
         +-------+   +-------------------+
                         |
                 Yes /       \ No
                    /         \
                   v           v
       +------------------+  +-----------------+
       | Fetch from GCP   |  | Raise           |
       | Secret Manager   |  | ConfigError     |
       +------------------+  +-----------------+
```

## Caching

Settings resolution is cached process-wide using `@lru_cache(maxsize=1)`:

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
```

This prevents repeated calls to environment parsing and Secret Manager network roundtrips during request processing. In testing scenarios, callers can clear the cache via `get_settings.cache_clear()` to reload updated configuration values.
