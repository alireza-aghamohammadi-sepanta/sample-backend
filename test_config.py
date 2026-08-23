import pytest

from app.core import config
from app.core.config import ConfigError, Settings, get_settings


ENV_VARS = (
    "GCP_PROJECT_ID",
    "JWT_SECRET",
    "JWT_SECRET_NAME",
    "DATABASE_INSTANCE",
    "DATABASE_INSTANCE_SECRET_NAME",
)


class FakeSecretPayload:
    def __init__(self, data: bytes):
        self.data = data


class FakeAccessResponse:
    def __init__(self, data: bytes):
        self.payload = FakeSecretPayload(data)


class FakeSecretManagerClient:
    """Stand-in for google.cloud.secretmanager.SecretManagerServiceClient."""

    def __init__(self, secrets: dict[str, str]):
        self.secrets = secrets
        self.requested: list[str] = []

    def access_secret_version(self, request):
        name = request["name"]
        self.requested.append(name)
        try:
            value = self.secrets[name]
        except KeyError:  # pragma: no cover - mirrors NotFound from the SDK
            raise LookupError(name)
        return FakeAccessResponse(value.encode("utf-8"))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_from_environment_without_gcp_project(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "local-jwt-secret")
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:local")

    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.jwt_secret == "local-jwt-secret"
    assert settings.database_instance == "project:region:local"
    assert settings.gcp_project_id is None


def test_settings_fetched_from_secret_manager(monkeypatch):
    client = FakeSecretManagerClient(
        {
            "projects/sample-project/secrets/JWT_SECRET/versions/latest": "sm-jwt-secret",
            "projects/sample-project/secrets/DATABASE_INSTANCE/versions/latest": (
                "sample-project:europe-west1:sample-db"
            ),
        }
    )
    monkeypatch.setattr(config, "_build_secret_manager_client", lambda: client)
    monkeypatch.setenv("GCP_PROJECT_ID", "sample-project")

    settings = get_settings()

    assert settings.jwt_secret == "sm-jwt-secret"
    assert settings.database_instance == "sample-project:europe-west1:sample-db"
    assert settings.gcp_project_id == "sample-project"
    assert client.requested == [
        "projects/sample-project/secrets/JWT_SECRET/versions/latest",
        "projects/sample-project/secrets/DATABASE_INSTANCE/versions/latest",
    ]


def test_secret_names_are_configurable(monkeypatch):
    client = FakeSecretManagerClient(
        {
            "projects/p/secrets/backend-jwt/versions/latest": "s1",
            "projects/p/secrets/backend-db/versions/latest": "s2",
        }
    )
    monkeypatch.setattr(config, "_build_secret_manager_client", lambda: client)
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("JWT_SECRET_NAME", "backend-jwt")
    monkeypatch.setenv("DATABASE_INSTANCE_SECRET_NAME", "backend-db")

    settings = get_settings()

    assert (settings.jwt_secret, settings.database_instance) == ("s1", "s2")


def test_environment_overrides_secret_manager(monkeypatch):
    client = FakeSecretManagerClient(
        {
            "projects/p/secrets/DATABASE_INSTANCE/versions/latest": "p:region:db",
        }
    )
    monkeypatch.setattr(config, "_build_secret_manager_client", lambda: client)
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("JWT_SECRET", "env-wins")

    settings = get_settings()

    assert settings.jwt_secret == "env-wins"
    assert settings.database_instance == "p:region:db"
    assert client.requested == [
        "projects/p/secrets/DATABASE_INSTANCE/versions/latest",
    ]


def test_missing_configuration_raises_config_error():
    with pytest.raises(ConfigError) as excinfo:
        get_settings()

    assert "JWT_SECRET" in str(excinfo.value)


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "first")
    monkeypatch.setenv("DATABASE_INSTANCE", "project:region:first")

    first = get_settings()
    monkeypatch.setenv("JWT_SECRET", "second")

    assert get_settings() is first

    get_settings.cache_clear()
    assert get_settings().jwt_secret == "second"
