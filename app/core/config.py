"""Application configuration.

Secrets are fetched from GCP Secret Manager when a GCP project is configured
(``GCP_PROJECT_ID``). Plain environment variables always take precedence, which
keeps local development and testing runnable without GCP credentials.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

JWT_SECRET_ENV = "JWT_SECRET"
DATABASE_INSTANCE_ENV = "DATABASE_INSTANCE"
GCP_PROJECT_ID_ENV = "GCP_PROJECT_ID"
JWT_SECRET_NAME_ENV = "JWT_SECRET_NAME"
DATABASE_INSTANCE_SECRET_NAME_ENV = "DATABASE_INSTANCE_SECRET_NAME"
GCS_BUCKET_NAME_ENV = "GCS_BUCKET_NAME"
GCS_SERVICE_ACCOUNT_INFO_ENV = "GCS_SERVICE_ACCOUNT_INFO"


class ConfigError(RuntimeError):
    """Raised when a required configuration value cannot be resolved."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings of the backend."""

    jwt_secret: str
    database_instance: str
    gcp_project_id: str | None = None
    gcs_bucket_name: str | None = None
    gcs_service_account_info: str | None = None


def _build_secret_manager_client():
    """Create a Secret Manager client.

    Imported lazily so the SDK is only required (and only authenticates) when
    secrets are actually resolved through GCP.
    """
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


def _access_secret(client, project_id: str, secret_name: str) -> str:
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def _resolve(
    env_var: str,
    secret_name_env_var: str,
    project_id: str | None,
    client_factory,
) -> str:
    value = os.environ.get(env_var)
    if value:
        return value

    if project_id:
        secret_name = os.environ.get(secret_name_env_var, env_var)
        try:
            return _access_secret(client_factory(), project_id, secret_name)
        except Exception as exc:  # noqa: BLE001 - surfaced as ConfigError
            raise ConfigError(
                f"Could not read secret '{secret_name}' from Secret Manager "
                f"project '{project_id}'"
            ) from exc

    raise ConfigError(
        f"Missing configuration: set the {env_var} environment variable or "
        f"configure {GCP_PROJECT_ID_ENV} to read it from Secret Manager"
    )


def load_settings() -> Settings:
    """Resolve the settings from the environment and/or Secret Manager."""
    project_id = os.environ.get(GCP_PROJECT_ID_ENV) or None

    # The client is created at most once per load, and only when needed.
    cached_client: list = []

    def client_factory():
        if not cached_client:
            cached_client.append(_build_secret_manager_client())
        return cached_client[0]

    jwt_secret = _resolve(
        JWT_SECRET_ENV, JWT_SECRET_NAME_ENV, project_id, client_factory
    )
    database_instance = _resolve(
        DATABASE_INSTANCE_ENV,
        DATABASE_INSTANCE_SECRET_NAME_ENV,
        project_id,
        client_factory,
    )

    # Optional: only needed by the features that talk to Cloud Storage.
    # GCS_SERVICE_ACCOUNT_INFO holds a service account key as JSON and is used
    # to sign URLs locally; on GCP the attached service account signs instead.
    gcs_bucket_name = os.environ.get(GCS_BUCKET_NAME_ENV) or None
    gcs_service_account_info = os.environ.get(GCS_SERVICE_ACCOUNT_INFO_ENV) or None

    return Settings(
        jwt_secret=jwt_secret,
        database_instance=database_instance,
        gcp_project_id=project_id,
        gcs_bucket_name=gcs_bucket_name,
        gcs_service_account_info=gcs_service_account_info,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings, loading them on first use."""
    return load_settings()
