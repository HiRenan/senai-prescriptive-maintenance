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
    Diagnosis,
    DocumentResponse,
    DocumentStatus,
    OpaqueNeighbor,
    Prescription,
    ReceivedDocument,
    RegisterDocumentRequest,
)
from prescriptive_maintenance.fakes import (
    SYNTHETIC_ANALYSIS_REQUESTS,
    SyntheticDocumentService,
    SyntheticGenerationPort,
    SyntheticModelPort,
)
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.ports import (
    DocumentEvidence,
    ModelPrediction,
    PortUnavailableError,
)
from prescriptive_maintenance.services import AnalysisService

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


def _request_payload(outcome: str) -> dict[str, Any]:
    return SYNTHETIC_ANALYSIS_REQUESTS[outcome].model_dump(mode="json")


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
    assert response.json()["error"]["issues"][0]["field"] == (
        "features.internal_sensor"
    )


@pytest.mark.parametrize(
    "case",
    ("nan", "infinity", "boolean", "numeric_string", "missing"),
)
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
        "title",
        "locator",
    }
    assert citation["document_version"] == "docver_synthetic_manual_v1"
    assert citation["chunk"] == "chunk_synthetic_manual_01"
    forbidden = ("text", "content", "source", "path", "embedding", "measurement")
    assert not any(token in key for token in forbidden for key in citation)


def test_document_lifecycle_is_complete_and_registration_is_never_approved() -> None:
    with TestClient(create_app()) as client:
        listed = client.get("/documents")
        registered = client.post(
            "/documents",
            json={
                "filename": "new.synthetic.pdf",
                "media_type": "application/pdf",
                "size_bytes": 512,
                "sha256": "c" * 64,
            },
        )

    assert listed.status_code == 200
    statuses = {item["status"] for item in listed.json()["items"]}
    assert statuses == {
        "received",
        "processing",
        "pending_approval",
        "approved",
        "rejected",
        "failed",
        "superseded",
    }
    assert registered.status_code == 201
    assert registered.json()["status"] == "received"
    assert registered.json()["decision_note"] is None


def test_document_actions_enforce_state_transitions() -> None:
    with TestClient(create_app()) as client:
        approved = client.post(
            "/documents/doc_synthetic_pending/approve",
            json={"note": "Aprovação sintética."},
        )
        invalid = client.post(
            "/documents/doc_synthetic_manual/reprocess",
        )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    DocumentResponse.model_validate_json(approved.content)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_document_transition"


def test_document_reject_reprocess_and_not_found_contracts() -> None:
    with TestClient(create_app()) as client:
        rejected = client.post(
            "/documents/doc_synthetic_pending/reject",
            json={"reason": "Motivo sintético."},
        )
        reprocessed = client.post(
            "/documents/doc_synthetic_failed/reprocess",
        )
        missing = client.get("/documents/doc_synthetic_missing")

    assert rejected.json()["status"] == "rejected"
    assert reprocessed.json()["status"] == "processing"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_not_found"


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
