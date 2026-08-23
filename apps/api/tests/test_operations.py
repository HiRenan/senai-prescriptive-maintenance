"""Adversarial tests for startup profiles, health, tracing, and safe logs."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import cast

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from prescriptive_maintenance.contracts import (
    AnalysisFeatures,
    Diagnosis,
    Prescription,
)
from prescriptive_maintenance.fakes import (
    SYNTHETIC_ANALYSIS_REQUESTS,
    SyntheticDocumentService,
    SyntheticModelPort,
    SyntheticRetrievalPort,
)
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.operations import (
    CORRELATION_ID_HEADER,
    ApplicationStartupError,
    PostgresReadinessProbe,
    ReadinessProbe,
    ReadinessService,
    current_correlation_id,
    normalize_correlation_id,
)
from prescriptive_maintenance.ports import (
    DocumentEvidence,
    GenerationPort,
    ModelPrediction,
    PortUnavailableError,
)
from prescriptive_maintenance.services import AnalysisService
from prescriptive_maintenance.settings import Settings

_DATABASE_URL = "postgresql://synthetic_user@127.0.0.1/synthetic_database"
_CORRELATION_ID_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,62}[A-Za-z0-9])?"
)


def _settings(
    environment: str,
    persistence_backend: str | None = None,
) -> Settings:
    selected_backend = persistence_backend or (
        "memory" if environment == "offline" else "postgres"
    )
    values: dict[str, object] = {
        "environment": environment,
        "persistence_backend": selected_backend,
    }
    if selected_backend == "postgres":
        values["database_url"] = _DATABASE_URL
    return Settings.model_validate(values)


class RecordingProbe(ReadinessProbe):
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    def check(self) -> None:
        self.calls += 1
        if self._error is not None:
            raise self._error


class BlockingProbe(ReadinessProbe):
    def __init__(self) -> None:
        self.calls = 0
        self.finished = Event()
        self.started = Event()
        self.release = Event()

    def check(self) -> None:
        self.calls += 1
        self.started.set()
        try:
            if not self.release.wait(timeout=1.0):
                raise RuntimeError("synthetic blocking probe expired")
        finally:
            self.finished.set()


class FailingGenerationPort(GenerationPort):
    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription:
        del diagnosis, evidence
        raise PortUnavailableError(
            "token=synthetic-secret path=C:\\synthetic-private\\manual.pdf "
            "content=raw-document"
        )


class UnavailableModelPort:
    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        del features, top_k
        raise PortUnavailableError(
            "token=synthetic-secret path=C:\\synthetic-private\\model.bin"
        )


class NeverCalledGenerationPort:
    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription:
        del diagnosis, evidence
        raise AssertionError("Generation must not be called.")


@pytest.fixture
def request_log_capture(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[pytest.LogCaptureFixture]:
    logger = logging.getLogger("prescriptive_maintenance.requests")
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


def _request_logs(
    caplog: pytest.LogCaptureFixture,
) -> tuple[dict[str, object], ...]:
    return tuple(
        cast(dict[str, object], json.loads(record.getMessage()))
        for record in caplog.records
        if record.name == "prescriptive_maintenance.requests"
    )


@pytest.mark.parametrize("environment", ("local", "aws"))
def test_connected_profiles_probe_the_same_required_database_port(
    environment: str,
) -> None:
    probe = RecordingProbe()

    with TestClient(
        create_app(settings=_settings(environment), database_probe=probe)
    ) as client:
        live = client.get("/health/live")
        assert probe.calls == 0
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert probe.calls == 1


@pytest.mark.parametrize("environment", ("local", "aws"))
def test_explicit_memory_backend_has_no_required_dependency(
    environment: str,
) -> None:
    probe = RecordingProbe(AssertionError("memory backend called PostgreSQL"))
    application = create_app(
        settings=_settings(environment, "memory"),
        database_probe=probe,
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")
        assert application.state.environment == environment
        assert application.state.persistence_backend == "memory"

    assert response.status_code == 200
    assert probe.calls == 0


def test_offline_profile_never_calls_database_or_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = RecordingProbe(AssertionError("offline touched an external dependency"))
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "offline")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_DATABASE_URL", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must_not_be_read")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must_not_be_read")
    monkeypatch.setenv("AWS_PROFILE", "must_not_be_read")

    with TestClient(create_app(database_probe=probe)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert probe.calls == 0


@pytest.mark.failure_matrix
def test_required_dependency_failure_changes_only_readiness_and_is_sanitized(
    request_log_capture: pytest.LogCaptureFixture,
) -> None:
    private_values = (
        "synthetic-secret",
        "synthetic-private",
        "manual.pdf",
        "raw-document",
    )
    probe = RecordingProbe(
        RuntimeError(
            "token=synthetic-secret path=C:\\synthetic-private\\manual.pdf "
            "content=raw-document"
        )
    )
    with TestClient(
        create_app(settings=_settings("local"), database_probe=probe)
    ) as client:
        live = client.get(
            "/health/live",
            headers={CORRELATION_ID_HEADER: "safe-live-id"},
        )
        ready = client.get(
            "/health/ready",
            headers={CORRELATION_ID_HEADER: "safe-ready-id"},
        )

    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {
        "error": {
            "code": "service_not_ready",
            "message": "O serviço não está pronto para receber tráfego.",
            "issues": [],
        }
    }
    assert ready.headers[CORRELATION_ID_HEADER] == "safe-ready-id"
    serialized_logs = json.dumps(
        _request_logs(request_log_capture),
        ensure_ascii=False,
    )
    for private_value in private_values:
        assert private_value not in ready.text
        assert private_value not in serialized_logs


@pytest.mark.failure_matrix
def test_readiness_timeout_is_bounded_and_returns_stable_503() -> None:
    probe = BlockingProbe()

    with TestClient(
        create_app(
            settings=_settings("aws"),
            database_probe=probe,
            readiness_timeout_seconds=0.02,
        )
    ) as client:
        started_at = time.monotonic()
        response = client.get("/health/ready")
        elapsed = time.monotonic() - started_at
        assert probe.started.wait(timeout=0.2)
        probe.release.set()
        assert probe.finished.wait(timeout=0.2)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"
    assert elapsed < 0.3


def test_timed_out_readiness_reuses_one_in_flight_probe() -> None:
    probe = BlockingProbe()

    with TestClient(
        create_app(
            settings=_settings("aws"),
            database_probe=probe,
            readiness_timeout_seconds=0.02,
        )
    ) as client:
        first = client.get("/health/ready")
        second = client.get("/health/ready")
        assert probe.calls == 1
        probe.release.set()
        assert probe.finished.wait(timeout=0.2)

    assert first.status_code == 503
    assert second.status_code == 503
    assert probe.calls == 1


def test_optional_generation_failure_never_changes_health() -> None:
    analysis_service = AnalysisService(
        model=SyntheticModelPort(),
        retrieval=SyntheticRetrievalPort(),
        generation=FailingGenerationPort(),
    )
    request = SYNTHETIC_ANALYSIS_REQUESTS["documented_fault"]

    with TestClient(
        create_app(settings=_settings("offline"), analysis_service=analysis_service)
    ) as client:
        analysis = client.post("/analysis", json=request.model_dump(mode="json"))
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert analysis.status_code == 200
    assert analysis.json()["outcome"] == "degraded"
    assert live.status_code == 200
    assert ready.status_code == 200


def test_invalid_startup_configuration_fails_early_with_sanitized_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "local")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv(
        "PRESCRIPTIVE_MAINTENANCE_DATABASE_URL",
        "not-a-url?token=synthetic-secret&path=C:\\synthetic-private",
    )

    with (
        pytest.raises(ApplicationStartupError) as error_info,
        TestClient(create_app()),
    ):
        pass

    assert str(error_info.value) == "Application startup configuration is invalid."
    assert error_info.value.__cause__ is None
    assert "synthetic-secret" not in repr(error_info.value)
    assert "synthetic-private" not in repr(error_info.value)


def test_hostile_settings_loader_exception_is_sanitized() -> None:
    def hostile_loader() -> Settings:
        raise RuntimeError(
            "token=synthetic-secret path=C:\\synthetic-private\\settings.env"
        )

    with (
        pytest.raises(ApplicationStartupError) as error_info,
        TestClient(create_app(settings_loader=hostile_loader)),
    ):
        pass

    assert str(error_info.value) == "Application startup configuration is invalid."
    assert error_info.value.__cause__ is None
    assert "synthetic-secret" not in repr(error_info.value)
    assert "synthetic-private" not in repr(error_info.value)


def test_settings_subclass_from_loader_fails_closed() -> None:
    class SettingsSubclass(Settings):
        pass

    noncanonical_settings = SettingsSubclass.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
        }
    )

    with (
        pytest.raises(ApplicationStartupError) as error_info,
        TestClient(create_app(settings_loader=lambda: noncanonical_settings)),
    ):
        pass

    assert str(error_info.value) == "Application startup configuration is invalid."
    assert error_info.value.__cause__ is None


def test_database_url_is_absent_from_operational_object_representations() -> None:
    private_marker = "sen62-private-operational-password"
    settings = Settings.model_validate(
        {
            "environment": "aws",
            "persistence_backend": "postgres",
            "database_url": (
                "postgresql://operations_user:"
                f"{private_marker}@127.0.0.1/operations_database"
            ),
        }
    )
    probe = PostgresReadinessProbe(
        str(settings.database_url),
        connect_timeout_seconds=1.0,
    )
    readiness = ReadinessService(
        settings,
        database_probe=None,
    )

    assert private_marker not in repr(probe)
    assert private_marker not in repr(readiness)


def test_readiness_timeout_configuration_has_a_safe_upper_bound() -> None:
    with (
        pytest.raises(ApplicationStartupError) as error_info,
        TestClient(
            create_app(
                settings=_settings("offline"),
                readiness_timeout_seconds=10.01,
            )
        ),
    ):
        pass

    assert str(error_info.value) == "Application startup configuration is invalid."


@pytest.mark.parametrize(
    "value",
    (
        "",
        " ",
        "leading space",
        "contains,comma",
        "control\x00character",
        "line\nbreak",
        "a" * 65,
        "não-ascii",
    ),
)
def test_unsafe_correlation_ids_are_rejected(value: str) -> None:
    assert normalize_correlation_id(value) is None


def test_correlation_id_subclass_is_rejected() -> None:
    class CorrelationIdSubclass(str):
        pass

    assert normalize_correlation_id(CorrelationIdSubclass("safe-looking-id")) is None


def test_missing_blank_and_multiple_correlation_ids_are_replaced() -> None:
    with TestClient(create_app(settings=_settings("offline"))) as client:
        missing = client.get("/health/live")
        blank = client.get(
            "/health/live",
            headers={CORRELATION_ID_HEADER: ""},
        )
        multiple = client.get(
            "/health/live",
            headers=[
                (CORRELATION_ID_HEADER, "first-safe-id"),
                (CORRELATION_ID_HEADER, "second-safe-id"),
            ],
        )

    correlation_ids = (
        missing.headers[CORRELATION_ID_HEADER],
        blank.headers[CORRELATION_ID_HEADER],
        multiple.headers[CORRELATION_ID_HEADER],
    )
    assert len(set(correlation_ids)) == 3
    assert all(_CORRELATION_ID_PATTERN.fullmatch(value) for value in correlation_ids)
    assert "first-safe-id" not in correlation_ids
    assert "second-safe-id" not in correlation_ids


def test_safe_correlation_id_is_propagated_to_header_and_json_log(
    request_log_capture: pytest.LogCaptureFixture,
) -> None:
    with TestClient(create_app(settings=_settings("offline"))) as client:
        response = client.get(
            "/health/live",
            headers={CORRELATION_ID_HEADER: "client-safe-id"},
        )

    assert response.headers[CORRELATION_ID_HEADER] == "client-safe-id"
    assert _request_logs(request_log_capture) == (
        {
            "correlation_id": "client-safe-id",
            "event": "http_request_completed",
            "method": "GET",
            "route": "/health/live",
            "status_code": 200,
        },
    )


def test_response_header_spoof_is_overwritten() -> None:
    application = create_app(settings=_settings("offline"))

    @application.get("/test/spoof", include_in_schema=False)
    def spoof_header() -> Response:
        return Response(headers={CORRELATION_ID_HEADER: "spoofed-id"})

    del spoof_header

    with TestClient(application) as client:
        response = client.get(
            "/test/spoof",
            headers={CORRELATION_ID_HEADER: "trusted-safe-id"},
        )

    assert response.headers.get_list(CORRELATION_ID_HEADER) == ["trusted-safe-id"]


def test_unhandled_exception_returns_sanitized_500_with_correlation_id(
    request_log_capture: pytest.LogCaptureFixture,
) -> None:
    application = create_app(settings=_settings("offline"))

    @application.get("/test/failure", include_in_schema=False)
    def fail_with_private_details() -> None:
        raise RuntimeError(
            "token=synthetic-secret path=C:\\synthetic-private\\service.log"
        )

    del fail_with_private_details

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/test/failure",
            headers={CORRELATION_ID_HEADER: "failure-safe-id"},
        )

    assert response.status_code == 500
    assert response.headers[CORRELATION_ID_HEADER] == "failure-safe-id"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "O serviço não pôde concluir a requisição.",
            "issues": [],
        }
    }
    assert "synthetic-secret" not in response.text
    assert "synthetic-private" not in response.text
    request_logs = _request_logs(request_log_capture)
    serialized_logs = json.dumps(request_logs)
    assert "synthetic-secret" not in serialized_logs
    assert "synthetic-private" not in serialized_logs
    assert len(request_logs) == 1
    assert current_correlation_id() is None


def test_correlation_context_is_isolated_across_concurrent_requests() -> None:
    application = create_app(settings=_settings("offline"))
    lock = Lock()
    observed: list[str | None] = []

    @application.get("/test/context", include_in_schema=False)
    def read_context() -> dict[str, str | None]:
        correlation_id = current_correlation_id()
        with lock:
            observed.append(correlation_id)
        time.sleep(0.01)
        return {"correlation_id": correlation_id}

    del read_context

    requested = tuple(f"parallel-request-{index}" for index in range(8))
    with (
        TestClient(application) as client,
        ThreadPoolExecutor(max_workers=len(requested)) as executor,
    ):
        futures = tuple(
            executor.submit(
                client.get,
                "/test/context",
                headers={CORRELATION_ID_HEADER: correlation_id},
            )
            for correlation_id in requested
        )
        responses = tuple(future.result(timeout=2.0) for future in futures)

    assert tuple(response.json()["correlation_id"] for response in responses) == (
        requested
    )
    assert tuple(response.headers[CORRELATION_ID_HEADER] for response in responses) == (
        requested
    )
    assert set(observed) == set(requested)
    assert current_correlation_id() is None


def test_422_409_and_503_keep_sanitized_bodies_headers_and_logs(
    request_log_capture: pytest.LogCaptureFixture,
) -> None:
    unavailable_service = AnalysisService(
        model=UnavailableModelPort(),
        retrieval=SyntheticRetrievalPort(),
        generation=NeverCalledGenerationPort(),
    )
    private_value = "payload-synthetic-secret"

    with TestClient(
        create_app(
            settings=_settings("offline"),
            analysis_service=unavailable_service,
            document_service=SyntheticDocumentService(),
        )
    ) as client:
        invalid = client.post(
            "/analysis",
            json={"unexpected": private_value},
            headers={CORRELATION_ID_HEADER: "validation-safe-id"},
        )
        conflict = client.post(
            "/documents/doc_synthetic_manual/reject",
            json={"reason": private_value},
            headers={CORRELATION_ID_HEADER: "conflict-safe-id"},
        )
        unavailable = client.post(
            "/analysis",
            json=SYNTHETIC_ANALYSIS_REQUESTS["normal"].model_dump(mode="json"),
            headers={CORRELATION_ID_HEADER: "unavailable-safe-id"},
        )

    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": {
            "code": "invalid_request",
            "message": "A requisição não atende ao contrato da API v1.",
            "issues": [{"field": "request", "code": "invalid"}],
        }
    }
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "invalid_document_transition"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "analysis_unavailable"
    assert invalid.headers[CORRELATION_ID_HEADER] == "validation-safe-id"
    assert conflict.headers[CORRELATION_ID_HEADER] == "conflict-safe-id"
    assert unavailable.headers[CORRELATION_ID_HEADER] == "unavailable-safe-id"
    assert private_value not in invalid.text
    assert private_value not in conflict.text
    assert "synthetic-secret" not in unavailable.text
    assert "synthetic-private" not in unavailable.text
    request_logs = _request_logs(request_log_capture)
    serialized_logs = json.dumps(request_logs, ensure_ascii=False)
    assert len(request_logs) == 3
    assert {record["status_code"] for record in request_logs} == {422, 409, 503}
    assert private_value not in serialized_logs
    assert "synthetic-secret" not in serialized_logs
    assert "synthetic-private" not in serialized_logs
