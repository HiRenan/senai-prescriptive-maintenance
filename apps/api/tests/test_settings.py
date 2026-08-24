"""Tests for explicit and validated application startup profiles."""

from pathlib import Path

import pytest
from prescriptive_maintenance.settings import Settings
from pydantic import PostgresDsn, ValidationError

ENV_EXAMPLE = Path(__file__).parents[3] / ".env.example"
ENVIRONMENT_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT"
PERSISTENCE_BACKEND_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND"
DATABASE_URL_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_DATABASE_URL"
ANALYSIS_MODE_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE"


def _load_settings(env_file: Path | None) -> Settings:
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]


@pytest.fixture(autouse=True)
def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.delenv(PERSISTENCE_BACKEND_VARIABLE, raising=False)
    monkeypatch.delenv(DATABASE_URL_VARIABLE, raising=False)
    monkeypatch.setenv(ANALYSIS_MODE_VARIABLE, "synthetic_demo")


def test_settings_load_complete_local_env_example() -> None:
    settings = _load_settings(ENV_EXAMPLE)
    database_url = settings.database_url

    assert settings.environment == "local"
    assert settings.persistence_backend == "postgres"
    assert settings.analysis_mode == "synthetic_demo"
    assert isinstance(database_url, PostgresDsn)
    assert database_url.scheme == "postgresql"
    assert database_url.path == "/prescriptive_maintenance"
    assert database_url.hosts()[0]["host"] == "127.0.0.1"
    assert database_url.hosts()[0]["port"] == 5432


def test_database_url_is_excluded_from_settings_representation() -> None:
    private_marker = "sen62-private-password"
    settings = Settings.model_validate(
        {
            "environment": "local",
            "persistence_backend": "postgres",
            "analysis_mode": "synthetic_demo",
            "database_url": (
                "postgresql://settings_user:"
                f"{private_marker}@127.0.0.1/settings_database"
            ),
        }
    )

    assert private_marker not in repr(settings)
    assert "database_url" not in repr(settings)


def test_artifact_references_are_excluded_from_settings_representation(
    tmp_path: Path,
) -> None:
    private_marker = "sen79-private-artifact-directory"
    manifest = tmp_path / private_marker / "runtime.json"
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "artifacts",
            "analysis_artifacts_manifest": manifest,
            "analysis_artifacts_manifest_sha256": "0" * 64,
        }
    )

    representation = repr(settings)
    assert private_marker not in representation
    assert "analysis_artifacts_manifest" not in representation
    assert "analysis_artifacts_manifest_sha256" not in representation


def test_invalid_database_url_is_hidden_from_validation_error_text() -> None:
    private_marker = "sen62-private-invalid-url"

    with pytest.raises(ValidationError) as error_info:
        Settings.model_validate(
            {
                "environment": "local",
                "persistence_backend": "postgres",
                "analysis_mode": "synthetic_demo",
                "database_url": f"not-a-url?token={private_marker}",
            }
        )

    assert private_marker not in str(error_info.value)


def test_process_environment_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "aws")
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE,
        "postgresql://override_user@127.0.0.1:55432/override_database",
    )

    settings = _load_settings(ENV_EXAMPLE)
    database_url = settings.database_url

    assert settings.environment == "aws"
    assert settings.persistence_backend == "postgres"
    assert database_url is not None
    assert database_url.path == "/override_database"
    assert database_url.hosts()[0]["port"] == 55432


def test_offline_profile_requires_no_external_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "offline")
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, "memory")

    settings = _load_settings(None)

    assert settings.environment == "offline"
    assert settings.persistence_backend == "memory"
    assert settings.database_url is None


@pytest.mark.parametrize("environment", ("local", "aws"))
def test_connected_profiles_can_select_memory_explicitly(environment: str) -> None:
    settings = Settings.model_validate(
        {
            "environment": environment,
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )

    assert settings.environment == environment
    assert settings.persistence_backend == "memory"
    assert settings.database_url is None


@pytest.mark.parametrize("environment", ("local", "aws"))
def test_connected_profiles_require_database_url(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, environment)
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, "postgres")

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert [error["loc"] for error in error_info.value.errors(include_url=False)] == [
        ("database_url",)
    ]


def test_offline_profile_rejects_stale_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "offline")
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, "memory")
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE,
        "postgresql://offline_user@127.0.0.1/offline_database",
    )

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("database_url",)


@pytest.mark.parametrize("environment", ("local", "aws"))
def test_memory_backend_rejects_database_url(environment: str) -> None:
    with pytest.raises(ValidationError) as error_info:
        Settings.model_validate(
            {
                "environment": environment,
                "persistence_backend": "memory",
                "analysis_mode": "synthetic_demo",
                "database_url": "postgresql://memory_user@127.0.0.1/memory_database",
            }
        )

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("database_url",)


@pytest.mark.parametrize(
    "database_url",
    (None, "postgresql://offline_user@127.0.0.1/offline_database"),
)
def test_offline_profile_rejects_postgres_backend(
    database_url: str | None,
) -> None:
    values: dict[str, object] = {
        "environment": "offline",
        "persistence_backend": "postgres",
        "analysis_mode": "synthetic_demo",
    }
    if database_url is not None:
        values["database_url"] = database_url

    with pytest.raises(ValidationError) as error_info:
        Settings.model_validate(values)

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("database_url",)


@pytest.mark.parametrize("environment", ("test", "production", "AWS", "", " "))
def test_invalid_or_legacy_profile_fails_validation(
    environment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, environment)
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, "memory")

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("environment",)


@pytest.mark.parametrize("persistence_backend", ("sqlite", "POSTGRES", "", " "))
def test_invalid_persistence_backend_fails_validation(
    persistence_backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "local")
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, persistence_backend)

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert error_info.value.errors(include_url=False)[0]["loc"] == (
        "persistence_backend",
    )


def test_missing_environment_fails_validation() -> None:
    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("environment",)
    assert error_info.value.errors(include_url=False)[0]["type"] == "missing"


def test_missing_persistence_backend_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "local")

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert error_info.value.errors(include_url=False)[0]["loc"] == (
        "persistence_backend",
    )
    assert error_info.value.errors(include_url=False)[0]["type"] == "missing"


def test_undeclared_alias_and_extra_field_are_rejected() -> None:
    with pytest.raises(ValidationError) as error_info:
        Settings.model_validate(
            {
                "environment": "offline",
                "persistence_backend": "memory",
                "analysis_mode": "synthetic_demo",
                "profile": "aws",
            }
        )

    assert error_info.value.errors(include_url=False)[0]["loc"] == ("profile",)
    assert error_info.value.errors(include_url=False)[0]["type"] == "extra_forbidden"


def test_extra_dotenv_field_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT=offline\n"
        "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND=memory\n"
        "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE=synthetic_demo\n"
        "PRESCRIPTIVE_MAINTENANCE_UNDECLARED=unsafe\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValidationError) as error_info:
        _load_settings(env_file)

    assert error_info.value.errors(include_url=False)[0]["type"] == "extra_forbidden"


def test_offline_profile_ignores_hostile_aws_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "offline")
    monkeypatch.setenv(PERSISTENCE_BACKEND_VARIABLE, "memory")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must_not_be_read")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must_not_be_read")
    monkeypatch.setenv("AWS_PROFILE", "must_not_be_read")
    monkeypatch.setenv("AWS_REGION", "must_not_be_read")

    settings = _load_settings(None)

    assert settings.environment == "offline"
    assert settings.persistence_backend == "memory"
    assert settings.database_url is None
