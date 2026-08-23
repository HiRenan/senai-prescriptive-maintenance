"""Functional tests for the frozen API v1 contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_COUNT,
    ANALYSIS_FEATURE_NAMES,
    MAX_TOP_K,
    AnalysisFeatures,
    AnalysisRequest,
    AnalysisResponse,
    ApprovedDocument,
    ApproveDocumentRequest,
    Citation,
    Diagnosis,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    ErrorResponse,
    OpaqueNeighbor,
    Prescription,
    ProcessingDocument,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
    RejectedDocument,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    ProcessingStep,
)
from prescriptive_maintenance.document_registry import (
    GovernedDocumentLifecycleService,
    InMemoryDocumentRegistryRepository,
    canonical_pdf_filename,
    logical_document_id,
)
from prescriptive_maintenance.fakes import (
    SYNTHETIC_ANALYSIS_REQUESTS,
    SyntheticDocumentService,
    SyntheticGenerationPort,
    SyntheticModelPort,
    SyntheticRetrievalPort,
    build_synthetic_analysis_service,
)
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.ports import (
    DocumentEvidence,
    ModelPrediction,
    PortContractError,
    PortUnavailableError,
)
from prescriptive_maintenance.services import (
    AnalysisService,
    DocumentConflictError,
    DocumentNotFoundError,
    DocumentServiceUnavailableError,
    InvalidDocumentRequestError,
    InvalidDocumentTransitionError,
)

EXPECTED_FEATURES = (
    "z_rms_velocity_mm_s",
    "temperature_c",
    "x_rms_velocity_mm_s",
    "z_peak_acceleration_g",
    "x_peak_acceleration_g",
    "z_peak_vel_comp_freq_hz",
    "x_peak_vel_comp_freq_hz",
    "z_rms_acceleration_g",
    "x_rms_acceleration_g",
    "z_kurtosis",
    "x_kurtosis",
    "z_crest_factor",
    "x_crest_factor",
    "z_peak_velocity_mm_s",
    "x_peak_velocity_mm_s",
    "z_high_freq_rms_accel_g",
    "x_high_freq_rms_accel_g",
    "rpm",
)
RESULT_FIELDS = {
    "analysis_id",
    "outcome",
    "diagnosis",
    "support",
    "abstention",
    "model_id",
    "neighbors",
    "prescription",
    "citations",
    "warnings",
}


@pytest.fixture(autouse=True)
def configure_offline_startup_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "offline")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_DATABASE_URL", raising=False)


def _request_payload(outcome: str) -> dict[str, Any]:
    return SYNTHETIC_ANALYSIS_REQUESTS[outcome].model_dump(mode="json")


def _analysis_response_payload(outcome: str) -> dict[str, Any]:
    return (
        build_synthetic_analysis_service()
        .analyze(SYNTHETIC_ANALYSIS_REQUESTS[outcome])
        .model_dump(mode="json")
    )


def _echo_analysis_response(payload: AnalysisResponse) -> AnalysisResponse:
    return payload


def test_analysis_feature_contract_is_the_single_ordered_source() -> None:
    assert ANALYSIS_FEATURE_COUNT == 18
    assert ANALYSIS_FEATURE_NAMES == EXPECTED_FEATURES
    assert tuple(AnalysisFeatures.model_fields) == EXPECTED_FEATURES


@pytest.mark.parametrize("outcome", tuple(SYNTHETIC_ANALYSIS_REQUESTS))
def test_post_analysis_exposes_all_five_synthetic_outcomes(outcome: str) -> None:
    with TestClient(create_app()) as client:
        response = client.post("/analysis", json=_request_payload(outcome))

    assert response.status_code == 200
    payload = cast(dict[str, Any], response.json())
    assert payload["outcome"] == outcome
    assert set(payload) == RESULT_FIELDS
    AnalysisResponse.model_validate_json(response.content)


def test_analysis_outcome_invariants_and_top_k_are_enforced() -> None:
    with TestClient(create_app()) as client:
        normal = cast(
            dict[str, Any],
            client.post("/analysis", json=_request_payload("normal")).json(),
        )
        documented_request = _request_payload("documented_fault")
        documented_request["top_k"] = 1
        documented = cast(
            dict[str, Any],
            client.post("/analysis", json=documented_request).json(),
        )
        undocumented = cast(
            dict[str, Any],
            client.post(
                "/analysis", json=_request_payload("undocumented_fault")
            ).json(),
        )
        out_of_distribution = cast(
            dict[str, Any],
            client.post(
                "/analysis", json=_request_payload("out_of_distribution")
            ).json(),
        )
        degraded = cast(
            dict[str, Any],
            client.post("/analysis", json=_request_payload("degraded")).json(),
        )

    assert normal["diagnosis"] is not None
    assert "support_score" not in normal["diagnosis"]
    assert normal["neighbors"]
    assert normal["abstention"] is None
    assert normal["prescription"] is None
    assert normal["citations"] == []
    assert len(documented["neighbors"]) == 1
    assert len(documented["citations"]) == 1
    assert documented["abstention"] is None
    assert undocumented["support"]["level"] == "sufficient"
    assert undocumented["abstention"]["reason"] == "undocumented_fault"
    assert undocumented["neighbors"]
    assert undocumented["prescription"] is None
    assert out_of_distribution["diagnosis"] is None
    assert out_of_distribution["abstention"]["reason"] == "out_of_distribution"
    assert out_of_distribution["neighbors"]
    assert all(
        neighbor["distance"] > 1 for neighbor in out_of_distribution["neighbors"]
    )
    assert degraded["diagnosis"] is not None
    assert degraded["support"] == {
        "level": "sufficient",
        "support_score": 0.72,
    }
    assert degraded["abstention"]["reason"] == "dependency_unavailable"
    assert degraded["neighbors"]
    assert degraded["citations"]
    assert degraded["prescription"] is None
    assert degraded["warnings"]


@pytest.mark.parametrize(
    ("outcome", "crossed_reason"),
    (
        ("undocumented_fault", "out_of_distribution"),
        ("undocumented_fault", "dependency_unavailable"),
        ("out_of_distribution", "undocumented_fault"),
        ("out_of_distribution", "dependency_unavailable"),
        ("degraded", "undocumented_fault"),
        ("degraded", "out_of_distribution"),
    ),
)
def test_abstention_variants_reject_every_crossed_reason_in_models_and_http(
    outcome: str,
    crossed_reason: str,
) -> None:
    payload = _analysis_response_payload(outcome)
    cast(dict[str, Any], payload["abstention"])["reason"] = crossed_reason

    with pytest.raises(ValueError):
        AnalysisResponse.model_validate(payload)

    application = create_app()
    application.add_api_route(
        "/test/analysis-contract",
        _echo_analysis_response,
        methods=["POST"],
        response_model=AnalysisResponse,
    )
    with TestClient(application) as client:
        response = client.post("/test/analysis-contract", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


class NeverCalledPorts:
    def __init__(self, *, unavailable_model: bool = False) -> None:
        self.calls = 0
        self.unavailable_model = unavailable_model

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        del features, top_k
        self.calls += 1
        if self.unavailable_model:
            raise PortUnavailableError("synthetic model outage")
        raise AssertionError("Invalid requests must not reach the model port.")

    def retrieve(self, retrieval_key: str, *, top_k: int) -> DocumentEvidence:
        del retrieval_key, top_k
        self.calls += 1
        raise AssertionError("Retrieval must not be called.")

    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription:
        del diagnosis, evidence
        self.calls += 1
        raise AssertionError("Generation must not be called.")


@pytest.mark.parametrize("top_k", (0, MAX_TOP_K + 1, 1.2, True, "5"))
@pytest.mark.failure_matrix
def test_invalid_top_k_returns_sanitized_422_before_internal_ports(
    top_k: object,
) -> None:
    ports = NeverCalledPorts()
    service = AnalysisService(model=ports, retrieval=ports, generation=ports)
    payload = _request_payload("normal")
    payload["top_k"] = top_k

    with TestClient(create_app(analysis_service=service)) as client:
        response = client.post("/analysis", json=payload)

    assert response.status_code == 422
    assert ports.calls == 0
    assert response.json()["error"]["code"] == "invalid_request"
    assert "input" not in response.text


def test_extra_feature_returns_422_before_internal_ports() -> None:
    ports = NeverCalledPorts()
    service = AnalysisService(model=ports, retrieval=ports, generation=ports)
    payload = _request_payload("normal")
    cast(dict[str, Any], payload["features"])["internal_sensor"] = 123.0

    with TestClient(create_app(analysis_service=service)) as client:
        response = client.post("/analysis", json=payload)

    assert response.status_code == 422
    assert ports.calls == 0
    assert response.json()["error"]["issues"] == [
        {"field": "request", "code": "invalid"}
    ]


@pytest.mark.parametrize(
    "case",
    ("nan", "infinity", "boolean", "numeric_string", "missing"),
)
@pytest.mark.failure_matrix
def test_invalid_feature_values_return_422_before_internal_ports(case: str) -> None:
    ports = NeverCalledPorts()
    service = AnalysisService(model=ports, retrieval=ports, generation=ports)
    payload = _request_payload("normal")
    features = cast(dict[str, Any], payload["features"])
    if case == "nan":
        features["rpm"] = float("nan")
    elif case == "infinity":
        features["rpm"] = float("inf")
    elif case == "boolean":
        features["rpm"] = True
    elif case == "numeric_string":
        features["rpm"] = "1000.0"
    else:
        del features["rpm"]

    with TestClient(create_app(analysis_service=service)) as client:
        if case in {"nan", "infinity"}:
            response = client.post(
                "/analysis",
                content=json.dumps(payload, allow_nan=True),
                headers={"content-type": "application/json"},
            )
        else:
            response = client.post("/analysis", json=payload)

    assert response.status_code == 422
    assert ports.calls == 0
    assert response.json()["error"]["code"] == "invalid_request"


def test_model_unavailability_returns_503_without_internal_details() -> None:
    ports = NeverCalledPorts(unavailable_model=True)
    service = AnalysisService(model=ports, retrieval=ports, generation=ports)

    with TestClient(create_app(analysis_service=service)) as client:
        response = client.post("/analysis", json=_request_payload("normal"))

    assert response.status_code == 503
    assert ports.calls == 1
    assert response.json() == {
        "error": {
            "code": "analysis_unavailable",
            "message": "A análise está temporariamente indisponível.",
            "issues": [],
        }
    }
    assert "synthetic model outage" not in response.text


class UnavailableRetrievalPort:
    def retrieve(self, retrieval_key: str, *, top_k: int) -> DocumentEvidence:
        del retrieval_key, top_k
        raise PortUnavailableError("synthetic retrieval outage")


class UnsafeCitation(Citation):
    local_path: str


class UnsafeCitationRetrievalPort:
    def retrieve(self, retrieval_key: str, *, top_k: int) -> DocumentEvidence:
        del retrieval_key, top_k
        unsafe_citation = UnsafeCitation(
            document_id="doc_synthetic_manual",
            document_version="docver_synthetic_manual_v1",
            chunk="chunk_synthetic_manual_01",
            page_number=1,
            local_path=r"C:\synthetic-private\manual.pdf",
        )
        return DocumentEvidence(
            support_score=0.88,
            citations=(unsafe_citation,),
        )


class TrackingGenerationPort(SyntheticGenerationPort):
    def __init__(self) -> None:
        self.calls = 0
        self.received_evidence: DocumentEvidence | None = None

    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription:
        self.calls += 1
        self.received_evidence = evidence
        return super().generate(diagnosis, evidence)


def test_retrieval_unavailability_preserves_model_support_and_neighbors() -> None:
    service = AnalysisService(
        model=SyntheticModelPort(),
        retrieval=UnavailableRetrievalPort(),
        generation=SyntheticGenerationPort(),
    )

    with TestClient(create_app(analysis_service=service)) as client:
        response = client.post(
            "/analysis",
            json=_request_payload("documented_fault"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "degraded"
    assert payload["support"] == {"level": "sufficient", "support_score": 0.92}
    assert payload["neighbors"]
    assert payload["citations"] == []
    assert payload["prescription"] is None
    assert "synthetic retrieval outage" not in response.text


def test_retrieval_boundary_rejects_subclass_and_preserves_valid_citations() -> None:
    retrieval = UnsafeCitationRetrievalPort()
    generation = TrackingGenerationPort()

    with pytest.raises(
        PortContractError,
        match="Retrieval evidence violates the internal contract",
    ) as raised:
        retrieval.retrieve("documented", top_k=1)
    assert str(raised.value) == "Retrieval evidence violates the internal contract."
    assert "synthetic-private" not in str(raised.value)
    assert "manual.pdf" not in str(raised.value)

    service = AnalysisService(
        model=SyntheticModelPort(),
        retrieval=retrieval,
        generation=generation,
    )

    with TestClient(create_app(analysis_service=service)) as client:
        response = client.post(
            "/analysis",
            json=_request_payload("documented_fault"),
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "degraded"
    assert response.json()["citations"] == []
    assert generation.calls == 0
    assert generation.received_evidence is None
    assert "local_path" not in response.text
    assert "synthetic-private" not in response.text
    assert "manual.pdf" not in response.text

    valid_generation = TrackingGenerationPort()
    valid_service = AnalysisService(
        model=SyntheticModelPort(),
        retrieval=SyntheticRetrievalPort(),
        generation=valid_generation,
    )

    with TestClient(create_app(analysis_service=valid_service)) as client:
        valid_response = client.post(
            "/analysis",
            json=_request_payload("documented_fault"),
        )

    assert valid_response.status_code == 200
    assert valid_generation.calls == 1
    assert valid_generation.received_evidence is not None
    citations = valid_generation.received_evidence.citations
    assert type(citations) is tuple
    assert all(type(citation) is Citation for citation in citations)
    assert tuple(citation.chunk for citation in citations) == (
        "chunk_synthetic_manual_01",
        "chunk_synthetic_manual_02",
        "chunk_synthetic_manual_03",
    )
    assert tuple(item["chunk"] for item in valid_response.json()["citations"]) == (
        "chunk_synthetic_manual_01",
        "chunk_synthetic_manual_02",
        "chunk_synthetic_manual_03",
    )


def test_analysis_query_returns_created_result_and_404_for_unknown_id() -> None:
    with TestClient(create_app()) as client:
        created = client.post(
            "/analysis", json=_request_payload("documented_fault")
        ).json()
        queried = client.get(f"/analysis/{created['analysis_id']}")
        missing = client.get("/analysis/ana_synthetic_missing")

    assert queried.status_code == 200
    assert queried.json() == created
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "analysis_not_found"


def test_public_neighbors_are_opaque_and_have_no_internal_data() -> None:
    with TestClient(create_app()) as client:
        payload = cast(
            dict[str, Any],
            client.post("/analysis", json=_request_payload("documented_fault")).json(),
        )

    neighbors = cast(list[dict[str, Any]], payload["neighbors"])
    neighbor = neighbors[0]
    assert set(neighbor) == {"neighbor_ref", "rank", "fault_code", "distance"}
    assert neighbor["fault_code"] == "synthetic_documented_fault"
    assert all(item["distance"] >= 0 for item in neighbors)
    assert any(item["distance"] > 1 for item in neighbors)
    forbidden = (
        "feature",
        "vector",
        "embedding",
        "row",
        "path",
        "chunk",
        "timestamp",
        "measurement",
    )
    assert not any(
        token in response_key for token in forbidden for response_key in neighbor
    )


@pytest.mark.parametrize("distance", (-0.01, float("nan"), float("inf")))
def test_neighbor_distance_rejects_negative_and_non_finite_values(
    distance: float,
) -> None:
    with pytest.raises(ValueError):
        OpaqueNeighbor(
            neighbor_ref="neighbor_synthetic_invalid",
            rank=1,
            fault_code="synthetic_fault",
            distance=distance,
        )


def test_model_neighbors_and_document_evidence_are_separate_internal_contracts() -> (
    None
):
    assert set(ModelPrediction.__dataclass_fields__) >= {"neighbors", "support_score"}
    assert set(DocumentEvidence.__dataclass_fields__) == {"support_score", "citations"}


def test_public_citations_are_auditable_without_raw_document_content() -> None:
    with TestClient(create_app()) as client:
        payload = client.post(
            "/analysis",
            json=_request_payload("documented_fault"),
        ).json()

    citation = payload["citations"][0]
    assert set(citation) == {
        "document_id",
        "document_version",
        "chunk",
        "page_number",
    }
    assert citation["document_version"] == "docver_synthetic_manual_v1"
    assert citation["chunk"] == "chunk_synthetic_manual_01"
    assert citation["page_number"] == 1
    forbidden = (
        "title",
        "locator",
        "text",
        "content",
        "source",
        "path",
        "embedding",
        "measurement",
    )
    assert not any(token in key for token in forbidden for key in citation)


def test_citation_page_number_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Citation(
            document_id="doc_synthetic_manual",
            document_version="docver_synthetic_manual_v1",
            chunk="chunk_synthetic_manual_01",
            page_number=0,
        )


def test_document_lifecycle_is_complete_and_registration_is_never_approved() -> None:
    with TestClient(create_app()) as client:
        initially_listed = client.get("/documents")
        registered = client.post(
            "/documents",
            json={
                "filename": "new.synthetic.pdf",
                "media_type": "application/pdf",
                "size_bytes": 512,
                "sha256": "c" * 64,
            },
        )
        listed = client.get("/documents")
        fetched = client.get(f"/documents/{registered.json()['document_id']}")

    assert initially_listed.status_code == 200
    assert initially_listed.json() == {"items": []}
    assert registered.status_code == 201
    assert registered.json()["status"] == "received"
    assert registered.json()["decision_note"] is None
    assert listed.json()["items"] == [registered.json()]
    assert fetched.json() == registered.json()


class ApiDocumentClock:
    def __init__(self) -> None:
        self._next = datetime(2035, 1, 2, tzinfo=UTC)

    def now(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


def _pending_document_service() -> tuple[GovernedDocumentLifecycleService, str]:
    repository = InMemoryDocumentRegistryRepository()
    clock = ApiDocumentClock()
    service = GovernedDocumentLifecycleService(repository=repository, clock=clock)
    request = RegisterDocumentRequest(
        filename="pending.synthetic.pdf",
        media_type="application/pdf",
        size_bytes=512,
        sha256="c" * 64,
    )
    document_id = service.register(request).document_id
    identity = logical_document_id(canonical_pdf_filename(request.filename))
    governance = DocumentGovernanceService(repository=repository, clock=clock)
    snapshot = repository.get(identity)
    assert snapshot is not None
    snapshot = governance.start_processing(
        identity=identity,
        version=1,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )
    for step in (ProcessingStep.EXTRACTION, ProcessingStep.INDEXING):
        snapshot = governance.record_step_succeeded(
            identity=identity,
            version=1,
            step=step,
            actor="processor.synthetic",
            expected_revision=snapshot.revision,
        )
    return service, document_id


def test_document_actions_enforce_state_transitions() -> None:
    documents, document_id = _pending_document_service()
    with TestClient(create_app(document_service=documents)) as client:
        approved = client.post(
            f"/documents/{document_id}/approve",
            json={"note": "Aprovação sintética."},
        )
        invalid = client.post(
            f"/documents/{document_id}/reprocess",
        )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    DocumentResponse.model_validate_json(approved.content)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_document_transition"


def test_document_reject_reprocess_and_not_found_contracts() -> None:
    documents, document_id = _pending_document_service()
    with TestClient(create_app(document_service=documents)) as client:
        rejected = client.post(
            f"/documents/{document_id}/reject",
            json={"reason": "Motivo sintético."},
        )
        reprocessed = client.post(
            f"/documents/{document_id}/reprocess",
        )
        missing = client.get("/documents/doc_synthetic_missing")

    assert rejected.json()["status"] == "rejected"
    assert reprocessed.json()["status"] == "processing"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_not_found"


def test_document_conflict_and_domain_validation_errors_are_sanitized() -> None:
    registration_payload = {
        "filename": "conflict.synthetic.pdf",
        "media_type": "application/pdf",
        "size_bytes": 512,
        "sha256": "e" * 64,
    }
    with TestClient(create_app()) as client:
        assert client.post("/documents", json=registration_payload).status_code == 201
        conflict = client.post(
            "/documents",
            json={**registration_payload, "size_bytes": 513},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"] == {
        "code": "document_conflict",
        "message": "O comando documental conflita com o estado armazenado.",
        "issues": [],
    }
    assert "conflict.synthetic.pdf" not in conflict.text
    assert "e" * 64 not in conflict.text

    documents, document_id = _pending_document_service()
    current_before_replay = documents.get(document_id)
    with TestClient(create_app(document_service=documents)) as client:
        transitioned_replay = client.post(
            "/documents",
            json={
                "filename": "PENDING.SYNTHETIC.PDF",
                "media_type": "application/pdf",
                "size_bytes": 512,
                "sha256": "c" * 64,
            },
        )
        current_after_replay = client.get(f"/documents/{document_id}")

    assert transitioned_replay.status_code == 201
    assert transitioned_replay.json()["document_id"] == document_id
    assert transitioned_replay.json()["status"] == "received"
    assert current_after_replay.status_code == 200
    assert current_after_replay.json()["status"] == "pending_approval"
    assert documents.get(document_id) == current_before_replay

    private_marker = "private-note\x00C:\\private\\manual.pdf"
    with TestClient(create_app(document_service=documents)) as client:
        invalid = client.post(
            f"/documents/{document_id}/approve",
            json={"note": private_marker},
        )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert "private-note" not in invalid.text
    assert "private" not in invalid.text


class UnavailableDocumentService(SyntheticDocumentService):
    def list(self) -> DocumentListResponse:
        raise DocumentServiceUnavailableError(
            "token=private-document-token path=C:\\private\\manual.pdf"
        )


def test_document_repository_failure_returns_sanitized_503() -> None:
    with TestClient(
        create_app(document_service=UnavailableDocumentService())
    ) as client:
        response = client.get("/documents")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "document_service_unavailable",
        "message": "O ciclo documental está temporariamente indisponível.",
        "issues": [],
    }
    assert "private-document-token" not in response.text
    assert "private" not in response.text


class FailingDocumentService(SyntheticDocumentService):
    def __init__(self, *, method: str, error: Exception) -> None:
        super().__init__()
        self._method = method
        self._error = error

    def _raise_for(self, method: str) -> None:
        if method == self._method:
            raise self._error

    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument:
        self._raise_for("register")
        return super().register(request)

    def list(self) -> DocumentListResponse:
        self._raise_for("list")
        return super().list()

    def get(self, document_id: str) -> DocumentResponse:
        self._raise_for("get")
        return super().get(document_id)

    def approve(
        self,
        document_id: str,
        request: ApproveDocumentRequest,
    ) -> ApprovedDocument:
        self._raise_for("approve")
        return super().approve(document_id, request)

    def reject(
        self,
        document_id: str,
        request: RejectDocumentRequest,
    ) -> RejectedDocument:
        self._raise_for("reject")
        return super().reject(document_id, request)

    def reprocess(self, document_id: str) -> ProcessingDocument:
        self._raise_for("reprocess")
        return super().reprocess(document_id)


_DOCUMENT_OPERATION_REQUESTS: dict[
    str,
    tuple[str, str, str, str, dict[str, object] | None],
] = {
    "register": (
        "registerDocument",
        "POST",
        "/documents",
        "/documents",
        {
            "filename": "runtime-error.synthetic.pdf",
            "media_type": "application/pdf",
            "size_bytes": 512,
            "sha256": "c" * 64,
        },
    ),
    "list": ("listDocuments", "GET", "/documents", "/documents", None),
    "get": (
        "getDocument",
        "GET",
        "/documents/{document_id}",
        "/documents/doc_synthetic_received",
        None,
    ),
    "approve": (
        "approveDocument",
        "POST",
        "/documents/{document_id}/approve",
        "/documents/doc_synthetic_pending/approve",
        {"note": "Aprovação sintética."},
    ),
    "reject": (
        "rejectDocument",
        "POST",
        "/documents/{document_id}/reject",
        "/documents/doc_synthetic_pending/reject",
        {"reason": "Motivo sintético."},
    ),
    "reprocess": (
        "reprocessDocument",
        "POST",
        "/documents/{document_id}/reprocess",
        "/documents/doc_synthetic_rejected/reprocess",
        None,
    ),
}

_DOCUMENT_CAUGHT_ERROR_CASES: tuple[
    tuple[str, type[Exception], int],
    ...,
] = (
    ("register", InvalidDocumentRequestError, 422),
    ("register", DocumentConflictError, 409),
    ("register", DocumentServiceUnavailableError, 503),
    ("list", DocumentServiceUnavailableError, 503),
    ("get", DocumentNotFoundError, 404),
    ("get", DocumentServiceUnavailableError, 503),
    ("approve", DocumentNotFoundError, 404),
    ("approve", InvalidDocumentRequestError, 422),
    ("approve", DocumentConflictError, 409),
    ("approve", InvalidDocumentTransitionError, 409),
    ("approve", DocumentServiceUnavailableError, 503),
    ("reject", DocumentNotFoundError, 404),
    ("reject", InvalidDocumentRequestError, 422),
    ("reject", DocumentConflictError, 409),
    ("reject", InvalidDocumentTransitionError, 409),
    ("reject", DocumentServiceUnavailableError, 503),
    ("reprocess", DocumentNotFoundError, 404),
    ("reprocess", DocumentConflictError, 409),
    ("reprocess", InvalidDocumentTransitionError, 409),
    ("reprocess", DocumentServiceUnavailableError, 503),
)


@pytest.mark.parametrize(
    ("service_method", "error_type", "expected_status"),
    _DOCUMENT_CAUGHT_ERROR_CASES,
)
def test_caught_document_errors_match_declared_openapi_responses(
    service_method: str,
    error_type: type[Exception],
    expected_status: int,
) -> None:
    operation_id, http_method, path_template, request_path, payload = (
        _DOCUMENT_OPERATION_REQUESTS[service_method]
    )
    application = create_app(
        document_service=FailingDocumentService(
            method=service_method,
            error=error_type("private runtime detail"),
        )
    )

    with TestClient(application) as client:
        if payload is None:
            response = client.request(http_method, request_path)
        else:
            response = client.request(http_method, request_path, json=payload)

    assert response.status_code == expected_status
    ErrorResponse.model_validate_json(response.content)
    operation = application.openapi()["paths"][path_template][http_method.lower()]
    assert operation["operationId"] == operation_id
    assert operation["responses"][str(expected_status)]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorResponse"}


class TrackingDocumentService(SyntheticDocumentService):
    def __init__(self) -> None:
        super().__init__()
        self.register_calls = 0

    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument:
        self.register_calls += 1
        return super().register(request)


@pytest.mark.parametrize(
    ("filename", "media_type"),
    (
        ("../manual.pdf", "application/pdf"),
        (r"folder\manual.pdf", "application/pdf"),
        ("månual.pdf", "application/pdf"),
        ("manual.txt", "application/pdf"),
        ("manual.pdf", "text/plain"),
    ),
)
def test_unsafe_document_registration_is_rejected_before_service(
    filename: str,
    media_type: str,
) -> None:
    documents = TrackingDocumentService()

    with TestClient(create_app(document_service=documents)) as client:
        response = client.post(
            "/documents",
            json={
                "filename": filename,
                "media_type": media_type,
                "size_bytes": 512,
                "sha256": "c" * 64,
            },
        )

    assert response.status_code == 422
    assert documents.register_calls == 0


def test_document_timestamps_must_be_zoned_and_monotonic() -> None:
    common: dict[str, Any] = {
        "document_id": "doc_synthetic_timeline",
        "filename": "timeline.synthetic.pdf",
        "media_type": "application/pdf",
        "size_bytes": 512,
        "sha256": "d" * 64,
        "status": DocumentStatus.RECEIVED,
        "decision_note": None,
        "failure": None,
        "superseded_by_document_id": None,
    }
    aware = datetime(2030, 1, 2, tzinfo=UTC)

    with pytest.raises(ValueError, match="timezone-aware"):
        ReceivedDocument(
            **common,
            created_at=datetime(2030, 1, 2),
            updated_at=datetime(2030, 1, 2),
        )
    with pytest.raises(ValueError, match="cannot precede"):
        ReceivedDocument(
            **common,
            created_at=aware,
            updated_at=aware - timedelta(seconds=1),
        )


def test_analysis_request_model_rejects_non_finite_features() -> None:
    payload = _request_payload("normal")
    cast(dict[str, Any], payload["features"])["rpm"] = float("nan")

    with pytest.raises(ValueError):
        AnalysisRequest.model_validate(payload)
