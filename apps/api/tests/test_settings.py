"""Tests for explicit and validated application settings."""

from pathlib import Path

import pytest
from prescriptive_maintenance.settings import Settings
from pydantic import PostgresDsn, ValidationError

ENV_EXAMPLE = Path(__file__).parents[3] / ".env.example"
ENVIRONMENT_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT"
DATABASE_URL_VARIABLE = "PRESCRIPTIVE_MAINTENANCE_DATABASE_URL"


def _load_settings(env_file: Path | None) -> Settings:
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]


@pytest.fixture(autouse=True)
def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.delenv(DATABASE_URL_VARIABLE, raising=False)


def test_settings_load_complete_env_example() -> None:
    settings = _load_settings(ENV_EXAMPLE)
    database_hosts = settings.database_url.hosts()

    assert settings.environment == "local"
    assert isinstance(settings.database_url, PostgresDsn)
    assert settings.database_url.scheme == "postgresql"
    assert settings.database_url.path == "/prescriptive_maintenance"
    assert database_hosts[0]["host"] == "127.0.0.1"
    assert database_hosts[0]["port"] == 5432


def test_process_environment_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "test")
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE,
        "postgresql://override_user@127.0.0.1:55432/override_database",
    )

    settings = _load_settings(ENV_EXAMPLE)
    database_hosts = settings.database_url.hosts()

    assert settings.environment == "test"
    assert settings.database_url.path == "/override_database"
    assert database_hosts[0]["port"] == 55432


def test_invalid_environment_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "staging")
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE,
        "postgresql://settings_user@127.0.0.1/settings_database",
    )

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert [error["loc"] for error in error_info.value.errors(include_url=False)] == [
        ("environment",)
    ]
    assert error_info.value.errors(include_url=False)[0]["type"] == "literal_error"


def test_missing_environment_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE,
        "postgresql://settings_user@127.0.0.1/settings_database",
    )

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert [error["loc"] for error in error_info.value.errors(include_url=False)] == [
        ("environment",)
    ]
    assert error_info.value.errors(include_url=False)[0]["type"] == "missing"


def test_missing_database_url_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "local")

    with pytest.raises(ValidationError) as error_info:
        _load_settings(None)

    assert [error["loc"] for error in error_info.value.errors(include_url=False)] == [
        ("database_url",)
    ]
    assert error_info.value.errors(include_url=False)[0]["type"] == "missing"
