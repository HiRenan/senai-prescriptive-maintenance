"""Tests for the FastAPI application contract."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prescriptive_maintenance.contracts import API_CONTRACT_VERSION
from prescriptive_maintenance.main import app, create_app


def test_asgi_target_is_a_fastapi_application() -> None:
    assert isinstance(app, FastAPI)


def test_create_app_returns_isolated_applications() -> None:
    first_application = create_app()
    second_application = create_app()

    assert first_application is not second_application
    assert first_application.router is not second_application.router
    assert first_application.title == "Prescriptive Maintenance API"
    assert first_application.version == API_CONTRACT_VERSION


def test_liveness_contract_without_local_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", raising=False)
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_DATABASE_URL", raising=False)

    with TestClient(create_app()) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"status":"ok"}'
