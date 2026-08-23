"""Tests for the FastAPI application contract."""

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prescriptive_maintenance.contracts import API_CONTRACT_VERSION
from prescriptive_maintenance.main import app, create_app
from prescriptive_maintenance.settings import Settings


def test_asgi_target_is_a_fastapi_application() -> None:
    assert isinstance(app, FastAPI)


def test_create_app_returns_isolated_applications() -> None:
    first_application = create_app()
    second_application = create_app()

    assert first_application is not second_application
    assert first_application.router is not second_application.router
    assert first_application.title == "Prescriptive Maintenance API"
    assert first_application.version == API_CONTRACT_VERSION


def test_liveness_contract_with_explicit_offline_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_DATABASE_URL", raising=False)

    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
        }
    )

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"status":"ok"}'


def test_smoke_health_process_ignores_repository_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_marker = "sen62-private-dotenv-password"
    (tmp_path / ".env").write_text(
        "PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT=local\n"
        "PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND=postgres\n"
        "PRESCRIPTIVE_MAINTENANCE_DATABASE_URL="
        f"postgresql://smoke_user:{private_marker}@127.0.0.1/smoke_database\n",
        encoding="utf-8",
        newline="\n",
    )
    smoke_namespace = run_path(str(Path(__file__).parents[3] / "scripts" / "smoke.py"))
    smoke_namespace["REPOSITORY_ROOT"] = tmp_path
    check_liveness = cast(Callable[[], None], smoke_namespace["_check_liveness"])

    check_liveness()

    output = capsys.readouterr()
    assert private_marker not in output.out
    assert private_marker not in output.err
