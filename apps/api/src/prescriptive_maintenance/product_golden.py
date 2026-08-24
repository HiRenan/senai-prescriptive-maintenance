"""Run the versioned synthetic product golden set through real boundaries."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Final, Literal, Self, cast

from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prescriptive_maintenance.analysis_integration import (
    PERSISTED_GENERATION_PROMPT_ID,
    AnalysisRuntimeAuthorization,
    IntegratedAnalysisService,
    SimilarityCheckedModelPort,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    API_CONTRACT_VERSION,
    AnalysisFeatures,
    AnalysisRequest,
    Citation,
    OpaqueNeighbor,
    PrescriptionPriority,
)
from prescriptive_maintenance.contracts import (
    Diagnosis as PublicDiagnosis,
)
from prescriptive_maintenance.data import CANONICAL_FEATURE_CONTRACT_VERSION
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    ProcessingStep,
)
from prescriptive_maintenance.document_registry import (
    GovernedDocumentLifecycleService,
    InMemoryDocumentRegistryRepository,
    canonical_pdf_filename,
    logical_document_id,
    persistence_document_version_id,
)
from prescriptive_maintenance.generation.contracts import (
    GENERATION_CONTRACT_VERSION,
)
from prescriptive_maintenance.generation.contracts import (
    Diagnosis as GenerationDiagnosis,
)
from prescriptive_maintenance.generation.guardrails import (
    RagGuardrailService,
    RagGuardrailStatus,
)
from prescriptive_maintenance.generation.prompt import (
    GENERATION_SYSTEM_PROMPT_VERSION,
)
from prescriptive_maintenance.generation.provider import (
    FakeGenerationProvider,
    GenerationProvider,
    ProviderExecutionError,
    ProviderRequest,
    ProviderResponse,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalBinding,
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
    RagKnowledgeRetrievalPort,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import RankedKnowledgeSnapshot
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.modeling.similarity_index import (
    SimilarityIndexCompatibility,
    SimilarityIndexPort,
    SimilarityIndexSelector,
    SimilarityNeighbor,
    SimilarityQuery,
)
from prescriptive_maintenance.persistence.memory import (
    InMemoryStore,
    InMemoryUnitOfWork,
)
from prescriptive_maintenance.persistence.models import (
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
)
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPrediction,
    PortUnavailableError,
)
from prescriptive_maintenance.prescription_orchestration import (
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationService,
)
from prescriptive_maintenance.settings import Settings

GOLDEN_REPORT_SCHEMA_VERSION: Final = 1
GOLDEN_SET_ID: Final = "product-golden-synthetic.v1"
MODEL_VERSION: Final = "product-golden-model.v1"
MODEL_ID: Final = "model_product_golden_v1"
RETRIEVAL_ADAPTER_VERSION: Final = "product-golden-approved-retrieval.v1"
MAPPING_VERSION: Final = "product-golden-mapping.v1"
MAPPING_SHA256: Final = sha256(MAPPING_VERSION.encode("ascii")).hexdigest()
PROVIDER_VERSION: Final = "fake-generation-provider.v1"
DATASET_ID: Final = sha256(b"sen48-product-golden-dataset-v1").hexdigest()
INDEX_ID: Final = (
    "similarity_index_v1_" + sha256(b"sen48-product-golden-index-v1").hexdigest()[:32]
)
FEATURE_SCHEMA_ID: Final = sha256(b"sen48-product-golden-feature-schema-v1").hexdigest()
PROVIDER_ID: Final = "product-golden-provider.v1"
PROVIDER_TIMEOUT_SECONDS: Final = 2.0
PROJECTION_VERSION: Final = "product-golden-priority.v1"
AUTHORIZATION_VERSION: Final = "product-golden-analysis.v1"
_POLICY = build_governed_retrieval_policy(
    policy_version="product-golden-retrieval.v1",
    minimum_score=0.5,
)
_FIXED_TIME: Final = datetime(2040, 1, 2, 3, 4, 5, tzinfo=UTC)
_EVIDENCE_CONTENT: Final = (
    "Synthetic approved evidence for a controlled product golden journey."
)
_DEFAULT_GOLDEN_SET: Final = (
    Path(__file__).resolve().parents[4]
    / "apps"
    / "api"
    / "tests"
    / "golden"
    / "product_journeys.v1.json"
)

ScenarioId = Literal[
    "normal",
    "documented_fault",
    "undocumented_fault",
    "out_of_distribution",
    "degraded",
]
Outcome = Literal[
    "normal",
    "documented_fault",
    "undocumented_fault",
    "out_of_distribution",
    "degraded",
]
Decision = Literal["approve", "reject"]
SafetyProbeId = Literal[
    "missing_evidence",
    "rejected_evidence",
    "invented_citation",
]
RefusalCode = Literal[
    "no_evidence",
    "stale_evidence",
    "invalid_provider_output",
]


class ProductGoldenError(RuntimeError):
    """Sanitized golden-set failure without raw inputs or local paths."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _GoldenConfiguration(_StrictModel):
    environment: Literal["offline"]
    persistence_backend: Literal["memory"]
    top_k: int = Field(ge=1, le=10)


class _ExpectedCalls(_StrictModel):
    model: int = Field(ge=0)
    retrieval: int = Field(ge=0)
    generation: int = Field(ge=0)
    provider: int = Field(ge=0)


class _GoldenScenario(_StrictModel):
    id: ScenarioId
    rpm: float
    expected_outcome: Outcome
    expected_calls: _ExpectedCalls

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.id != self.expected_outcome:
            raise ValueError("Golden scenario identity must match its outcome.")
        return self


class _GoldenDocument(_StrictModel):
    decision: Decision
    decision_note: str = Field(min_length=1, max_length=512)
    filename: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_synthetic_identity(self) -> Self:
        if not self.filename.endswith(".synthetic.pdf"):
            raise ValueError("Golden documents must be explicitly synthetic.")
        return self


class _GoldenSafetyProbe(_StrictModel):
    id: SafetyProbeId
    expected_provider_calls: int = Field(ge=0)
    expected_refusal: RefusalCode


class _GoldenSet(_StrictModel):
    schema_version: Literal[1]
    golden_set_id: Literal["product-golden-synthetic.v1"]
    configuration: _GoldenConfiguration
    feature_template: dict[str, float]
    scenarios: tuple[_GoldenScenario, ...]
    documents: tuple[_GoldenDocument, ...]
    safety_probes: tuple[_GoldenSafetyProbe, ...]

    @model_validator(mode="after")
    def validate_closed_set(self) -> Self:
        expected_features = set(ANALYSIS_FEATURE_NAMES).difference({"rpm"})
        if set(self.feature_template) != expected_features:
            raise ValueError("Golden feature template is incomplete.")
        if tuple(item.id for item in self.scenarios) != (
            "normal",
            "documented_fault",
            "undocumented_fault",
            "out_of_distribution",
            "degraded",
        ):
            raise ValueError("Golden scenarios must contain the five product states.")
        if tuple(item.decision for item in self.documents) != ("approve", "reject"):
            raise ValueError("Golden documents must cover approval and rejection.")
        if tuple(item.id for item in self.safety_probes) != (
            "missing_evidence",
            "rejected_evidence",
            "invented_citation",
        ):
            raise ValueError("Golden safety probes are incomplete.")
        return self


@dataclass(slots=True)
class _LayerCounter:
    attempts: int = 0
    successes: int = 0
    errors: int = 0

    def record_success(self) -> None:
        self.attempts += 1
        self.successes += 1

    def record_error(self) -> None:
        self.attempts += 1
        self.errors += 1


@dataclass(frozen=True, slots=True)
class _EvidenceFixture:
    decision: Decision
    document_id: str
    document_version: str
    citation: Citation
    snapshot: RankedKnowledgeSnapshot


class _GoldenClock:
    def __init__(self) -> None:
        self._next = _FIXED_TIME

    def now(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


class _GoldenModelPort:
    def __init__(self, scenarios: Sequence[_GoldenScenario]) -> None:
        self._scenarios = {item.rpm: item for item in scenarios}
        self.metrics = _LayerCounter()

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        try:
            scenario = self._scenarios[features.rpm]
            prediction = _prediction_for(scenario.id, top_k=top_k)
        except Exception:
            self.metrics.record_error()
            raise PortUnavailableError("Golden model is unavailable.") from None
        self.metrics.record_success()
        return prediction


class _GoldenSimilarityIndex(SimilarityIndexPort):
    def __init__(self, scenarios: Sequence[_GoldenScenario]) -> None:
        self._scenarios = {item.rpm: item for item in scenarios}

    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]:
        try:
            if query.selector.index_id != INDEX_ID or query.top_k != 1:
                raise ValueError
            scenario = self._scenarios[query.features[-1]]
            prediction = _prediction_for(scenario.id, top_k=query.top_k)
            return tuple(
                SimilarityNeighbor(
                    opaque_id=item.neighbor_ref,
                    rank=item.rank,
                    fault_code=item.fault_code,
                    distance=item.distance,
                )
                for item in prediction.neighbors
            )
        except Exception:
            raise PortUnavailableError("Golden similarity is unavailable.") from None


class _GoldenRetrievalPort(RagKnowledgeRetrievalPort):
    def __init__(self, approved: _EvidenceFixture) -> None:
        self._approved = approved
        self.metrics = _LayerCounter()

    @property
    def runtime_binding(self) -> GovernedRetrievalBinding:
        return GovernedRetrievalBinding(
            policy_schema_version=_POLICY.schema_version,
            policy_version=_POLICY.policy_version,
            policy_sha256=_POLICY.policy_sha256,
            mapping_version=MAPPING_VERSION,
            mapping_sha256=MAPPING_SHA256,
        )

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        try:
            if disposition is not ModelDisposition.FAULT or top_k != 1:
                raise ValueError
            if fault_class == "golden-undocumented-fault":
                result = _no_evidence_result(fault_class)
            elif fault_class in {
                "golden-documented-fault",
                "golden-degraded-fault",
            }:
                result = _evidence_result(fault_class, self._approved.snapshot)
            else:
                raise ValueError
        except Exception:
            self.metrics.record_error()
            raise PortUnavailableError("Golden retrieval is unavailable.") from None
        self.metrics.record_success()
        return result


class _ExactSnapshotCurrentness:
    def __init__(
        self,
        *,
        fault_classes: frozenset[str],
        snapshot: RankedKnowledgeSnapshot,
    ) -> None:
        self._fault_classes = fault_classes
        self._snapshot = snapshot

    def snapshots_are_current(
        self,
        *,
        fault_class: str,
        policy_schema_version: int,
        policy_version: str,
        minimum_score: float,
        policy_sha256: str,
        mapping_version: str,
        mapping_sha256: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> bool:
        return (
            fault_class in self._fault_classes
            and policy_schema_version == _POLICY.schema_version
            and policy_version == _POLICY.policy_version
            and minimum_score == _POLICY.minimum_score
            and policy_sha256 == _POLICY.policy_sha256
            and mapping_version == MAPPING_VERSION
            and mapping_sha256 == MAPPING_SHA256
            and evidence == (self._snapshot,)
        )


class _GoldenScenarioProvider(GenerationProvider):
    def __init__(self) -> None:
        self._accepted_provider = FakeGenerationProvider()
        self._failing_provider = FakeGenerationProvider(
            error=ProviderExecutionError("Synthetic provider failure."),
        )
        self.metrics = _LayerCounter()

    @property
    def provider_call_count(self) -> int:
        return self._accepted_provider.call_count + self._failing_provider.call_count

    @property
    def provider_error_count(self) -> int:
        return self._failing_provider.call_count

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        try:
            if request.diagnosis_fault_code == "golden-documented-fault":
                response = self._accepted_provider.generate(request)
            elif request.diagnosis_fault_code == "golden-degraded-fault":
                response = self._failing_provider.generate(request)
            else:
                raise ProviderExecutionError("Synthetic provider binding failed.")
        except Exception:
            self.metrics.record_error()
            raise
        self.metrics.record_success()
        return response


class _AnalysisIdSequence:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self) -> str:
        with self._lock:
            self._value += 1
            return f"ana_product_golden_{self._value:04d}"


def run_product_golden(
    golden_set_path: Path = _DEFAULT_GOLDEN_SET,
) -> dict[str, object]:
    """Execute the complete synthetic golden set and return a sanitized report."""

    golden, golden_sha256 = _load_golden_set(golden_set_path)
    settings = Settings.model_validate(
        {
            "environment": golden.configuration.environment,
            "persistence_backend": golden.configuration.persistence_backend,
            "analysis_mode": "synthetic_demo",
        }
    )
    document_results, approved, rejected = _run_document_journeys(golden, settings)
    analysis_results, layer_metrics, authorization = _run_analysis_journeys(
        golden,
        settings,
        approved,
    )
    safety_results = _run_safety_probes(golden, approved, rejected)
    return {
        "analysis_journeys": analysis_results,
        "bindings": {
            "api_contract_version": API_CONTRACT_VERSION,
            "authorization_sha256": authorization.authorization_sha256,
            "authorization_version": authorization.authorization_version,
            "dataset_id": authorization.dataset_id,
            "feature_contract_version": CANONICAL_FEATURE_CONTRACT_VERSION,
            "feature_schema_id": FEATURE_SCHEMA_ID,
            "generation_contract_version": GENERATION_CONTRACT_VERSION,
            "index_id": authorization.index_id,
            "mapping_sha256": MAPPING_SHA256,
            "mapping_version": MAPPING_VERSION,
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "prompt_version": GENERATION_SYSTEM_PROMPT_VERSION,
            "provider_version": PROVIDER_VERSION,
            "projection_policy_sha256": authorization.projection_policy_sha256,
            "projection_policy_version": authorization.projection_policy_version,
            "retrieval_adapter_version": RETRIEVAL_ADAPTER_VERSION,
            "retrieval_policy_schema_version": _POLICY.schema_version,
            "retrieval_policy_sha256": _POLICY.policy_sha256,
            "retrieval_policy_version": _POLICY.policy_version,
        },
        "configuration": {
            "environment": golden.configuration.environment,
            "persistence_backend": golden.configuration.persistence_backend,
            "provider_mode": "synthetic_offline",
            "top_k": golden.configuration.top_k,
        },
        "document_journeys": document_results,
        "golden_set": {
            "id": golden.golden_set_id,
            "sha256": golden_sha256,
        },
        "limits": {
            "aws_dependencies": False,
            "network_calls": False,
            "original_materials_accessed": False,
            "paid_provider_calls": False,
            "raw_content_in_report": False,
        },
        "metrics": {
            "measurement": "deterministic_call_and_error_counts",
            **layer_metrics,
        },
        "safety_probes": safety_results,
        "schema_version": GOLDEN_REPORT_SCHEMA_VERSION,
    }


def render_product_golden_report(report: dict[str, object]) -> str:
    """Serialize the report with byte-stable ordering and finite JSON values."""

    try:
        return (
            json.dumps(
                report,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    except (TypeError, ValueError):
        raise ProductGoldenError("Golden report structure is invalid.") from None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the canonical golden set and write only its sanitized report."""

    parser = argparse.ArgumentParser(
        description="Executa o golden set sintético ponta a ponta.",
    )
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=_DEFAULT_GOLDEN_SET,
        help="Fixture JSON sintética e versionada.",
    )
    arguments = parser.parse_args(argv)
    try:
        report = run_product_golden(arguments.golden_set)
        serialized = render_product_golden_report(report)
    except ProductGoldenError as error:
        print(f"golden-e2e: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(serialized)
    return 0


def _load_golden_set(path: Path) -> tuple[_GoldenSet, str]:
    try:
        raw = path.read_bytes()
        if not raw or raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
            raise ValueError
        golden = _GoldenSet.model_validate_json(raw)
        if golden.golden_set_id != GOLDEN_SET_ID:
            raise ValueError
        return golden, sha256(raw).hexdigest()
    except Exception:
        raise ProductGoldenError("Golden set is invalid.") from None


def _run_document_journeys(
    golden: _GoldenSet,
    settings: Settings,
) -> tuple[list[dict[str, object]], _EvidenceFixture, _EvidenceFixture]:
    repository = InMemoryDocumentRegistryRepository()
    clock = _GoldenClock()
    documents = GovernedDocumentLifecycleService(repository=repository, clock=clock)
    governance = DocumentGovernanceService(repository=repository, clock=clock)
    application = create_app(document_service=documents, settings=settings)
    results: list[dict[str, object]] = []
    evidence: dict[Decision, _EvidenceFixture] = {}

    with TestClient(application) as client:
        for specification in golden.documents:
            registered = client.post(
                "/documents",
                json={
                    "filename": specification.filename,
                    "media_type": "application/pdf",
                    "sha256": specification.sha256,
                    "size_bytes": specification.size_bytes,
                },
                headers={
                    "X-Correlation-ID": (
                        f"sen48-document-{specification.decision}-register"
                    )
                },
            )
            _require(registered.status_code == 201, "Document registration failed.")
            registered_payload = _json_object(registered)
            document_id = _required_text(registered_payload, "document_id")
            _require(
                registered_payload.get("status") == "received",
                "Document registration did not remain unapproved.",
            )

            identity = logical_document_id(
                canonical_pdf_filename(specification.filename)
            )
            snapshot = repository.get(identity)
            _require(snapshot is not None, "Registered document is unavailable.")
            if snapshot is None:
                raise AssertionError("Unreachable document state.")
            snapshot = governance.start_processing(
                identity=identity,
                version=1,
                actor="processor.product-golden",
                expected_revision=snapshot.revision,
            )
            for step in (ProcessingStep.EXTRACTION, ProcessingStep.INDEXING):
                snapshot = governance.record_step_succeeded(
                    identity=identity,
                    version=1,
                    step=step,
                    actor="processor.product-golden",
                    expected_revision=snapshot.revision,
                )

            if specification.decision == "approve":
                decided = client.post(
                    f"/documents/{document_id}/approve",
                    json={"note": specification.decision_note},
                    headers={"X-Correlation-ID": "sen48-document-approve-decision"},
                )
                expected_status = "approved"
            else:
                decided = client.post(
                    f"/documents/{document_id}/reject",
                    json={"reason": specification.decision_note},
                    headers={"X-Correlation-ID": "sen48-document-reject-decision"},
                )
                expected_status = "rejected"
            _require(decided.status_code == 200, "Document decision failed.")
            decided_payload = _json_object(decided)
            _require(
                decided_payload.get("status") == expected_status,
                "Document decision state is invalid.",
            )
            fetched = client.get(
                f"/documents/{document_id}",
                headers={
                    "X-Correlation-ID": (
                        f"sen48-document-{specification.decision}-query"
                    )
                },
            )
            _require(fetched.status_code == 200, "Document query failed.")
            _require(
                _json_object(fetched).get("status") == expected_status,
                "Document query returned a divergent state.",
            )

            version_id = persistence_document_version_id(
                identity,
                number=1,
                source_sha256=specification.sha256,
            )
            chunk_id = f"chunk_golden_{specification.decision}_01"
            section_id = f"section_golden_{specification.decision}_01"
            content = (
                _EVIDENCE_CONTENT
                if specification.decision == "approve"
                else "Synthetic rejected evidence that must never reach generation."
            )
            citation = Citation(
                document_id=document_id,
                document_version=version_id,
                chunk=chunk_id,
                page_number=1,
            )
            evidence[specification.decision] = _EvidenceFixture(
                decision=specification.decision,
                document_id=document_id,
                document_version=version_id,
                citation=citation,
                snapshot=RankedKnowledgeSnapshot(
                    document_id=document_id,
                    document_version=version_id,
                    chunk_id=chunk_id,
                    page_number=1,
                    section_id=section_id,
                    content=content,
                    content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                    score=0.9,
                ),
            )
            results.append(
                {
                    "decision": specification.decision,
                    "document_id": document_id,
                    "document_version": version_id,
                    "registered_status": "received",
                    "result_status": expected_status,
                }
            )

    approved = evidence.get("approve")
    rejected = evidence.get("reject")
    _require(
        approved is not None and rejected is not None,
        "Document evidence fixtures are incomplete.",
    )
    if approved is None or rejected is None:
        raise AssertionError("Unreachable evidence state.")
    return results, approved, rejected


def _run_analysis_journeys(
    golden: _GoldenSet,
    settings: Settings,
    approved: _EvidenceFixture,
) -> tuple[
    list[dict[str, object]],
    dict[str, object],
    AnalysisRuntimeAuthorization,
]:
    service, model, retrieval, provider, authorization = _build_analysis_service(
        golden,
        approved,
    )
    application = create_app(analysis_service=service, settings=settings)
    results: list[dict[str, object]] = []

    with TestClient(application) as client:
        for scenario in golden.scenarios:
            before = _call_snapshot(model, retrieval, provider)
            request = _request_for(golden, scenario)
            correlation_id = f"sen48-analysis-{scenario.id}"
            response = client.post(
                "/analysis",
                json=request.model_dump(mode="json"),
                headers={"X-Correlation-ID": correlation_id},
            )
            _require(response.status_code == 200, "Golden analysis request failed.")
            _require(
                response.headers.get("X-Correlation-ID") == correlation_id,
                "Golden analysis correlation is invalid.",
            )
            payload = _json_object(response)
            observed_outcome = _required_text(payload, "outcome")
            _require(
                observed_outcome == scenario.expected_outcome,
                "Golden analysis outcome diverged.",
            )
            analysis_id = _required_text(payload, "analysis_id")
            queried = client.get(
                f"/analysis/{analysis_id}",
                headers={"X-Correlation-ID": f"{correlation_id}-query"},
            )
            _require(
                queried.status_code == 200 and _json_object(queried) == payload,
                "Golden analysis query did not round-trip.",
            )
            after = _call_snapshot(model, retrieval, provider)
            calls = {
                key: after[key] - before[key]
                for key in ("model", "retrieval", "generation", "provider")
            }
            _require(
                calls == scenario.expected_calls.model_dump(mode="python"),
                "Golden layer call count diverged.",
            )
            raw_citations = payload.get("citations")
            _require(type(raw_citations) is list, "Golden citations are invalid.")
            citations = cast(list[object], raw_citations)
            approved_citation_match = citations in (
                [],
                [approved.citation.model_dump(mode="json")],
            )
            _require(
                approved_citation_match,
                "Analysis citation does not match approved evidence.",
            )
            if scenario.id == "documented_fault":
                _require(
                    citations == [approved.citation.model_dump(mode="json")],
                    "Documented analysis lacks its approved citation.",
                )
            results.append(
                {
                    "approved_citation_match": approved_citation_match,
                    "id": scenario.id,
                    "layer_calls": calls,
                    "observed_outcome": observed_outcome,
                    "round_trip": True,
                }
            )

    _require(
        model.metrics.attempts == len(golden.scenarios),
        "Golden model aggregate is invalid.",
    )
    layer_metrics: dict[str, object] = {
        "generation": {
            "attempts": provider.metrics.attempts,
            "errors": provider.metrics.errors,
            "provider_attempts": provider.provider_call_count,
            "provider_errors": provider.provider_error_count,
            "successes": provider.metrics.successes,
            "version": GENERATION_SYSTEM_PROMPT_VERSION,
        },
        "model": {
            "attempts": model.metrics.attempts,
            "errors": model.metrics.errors,
            "successes": model.metrics.successes,
            "version": MODEL_VERSION,
        },
        "retrieval": {
            "attempts": retrieval.metrics.attempts,
            "errors": retrieval.metrics.errors,
            "policy_sha256": _POLICY.policy_sha256,
            "successes": retrieval.metrics.successes,
            "version": RETRIEVAL_ADAPTER_VERSION,
        },
    }
    return results, layer_metrics, authorization


def _build_analysis_service(
    golden: _GoldenSet,
    approved: _EvidenceFixture,
) -> tuple[
    IntegratedAnalysisService,
    _GoldenModelPort,
    _GoldenRetrievalPort,
    _GoldenScenarioProvider,
    AnalysisRuntimeAuthorization,
]:
    projection = build_prescription_projection_policy(
        policy_version=PROJECTION_VERSION,
        priorities={
            "golden_documented_fault": PrescriptionPriority.SCHEDULED,
            "golden_degraded_fault": PrescriptionPriority.SCHEDULED,
        },
    )
    authorization = build_analysis_runtime_authorization(
        authorization_version=AUTHORIZATION_VERSION,
        dataset_id=DATASET_ID,
        model_id=MODEL_ID,
        index_id=INDEX_ID,
        retrieval_policy_version=_POLICY.policy_version,
        retrieval_policy_sha256=_POLICY.policy_sha256,
        mapping_version=MAPPING_VERSION,
        mapping_sha256=MAPPING_SHA256,
        prompt_id=PERSISTED_GENERATION_PROMPT_ID,
        provider_id=PROVIDER_ID,
        provider_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
        projection_policy=projection,
    )
    raw_model = _GoldenModelPort(golden.scenarios)
    checked_model = SimilarityCheckedModelPort(
        model=raw_model,
        similarity=_GoldenSimilarityIndex(golden.scenarios),
        selector=SimilarityIndexSelector(
            index_id=INDEX_ID,
            model_id=MODEL_ID,
            compatibility=SimilarityIndexCompatibility(
                dataset_id=DATASET_ID,
                schema_id=FEATURE_SCHEMA_ID,
            ),
        ),
        authorization=authorization,
    )
    retrieval = _GoldenRetrievalPort(approved)
    provider = _GoldenScenarioProvider()
    orchestration = PrescriptionOrchestrationService(
        retrieval=retrieval,
        provider=provider,
        snapshot_currentness=_ExactSnapshotCurrentness(
            fault_classes=frozenset(
                {"golden-documented-fault", "golden-degraded-fault"}
            ),
            snapshot=approved.snapshot,
        ),
        config=PrescriptionOrchestrationConfig(
            provider_id=PROVIDER_ID,
            provider_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
        ),
    )
    store = InMemoryStore()
    _seed_approved_evidence(store, approved)
    service = IntegratedAnalysisService(
        model=checked_model,
        orchestration=orchestration,
        authorization=authorization,
        projection_policy=projection,
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: _FIXED_TIME,
        analysis_id_factory=_AnalysisIdSequence(),
    )
    return service, raw_model, retrieval, provider, authorization


def _seed_approved_evidence(
    store: InMemoryStore,
    approved: _EvidenceFixture,
) -> None:
    document = DocumentMetadata(
        document_id=approved.document_id,
        created_at=_FIXED_TIME,
        versions=(
            DocumentVersionMetadata(
                document_version_id=approved.document_version,
                document_id=approved.document_id,
                source_sha256=sha256(b"sen48-approved-synthetic-document").hexdigest(),
                created_at=_FIXED_TIME,
                chunks=(
                    ChunkReference(
                        chunk_ref=approved.citation.chunk,
                        document_id=approved.document_id,
                        document_version_id=approved.document_version,
                        page_number=approved.citation.page_number,
                    ),
                ),
            ),
        ),
    )
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.commit()


def _run_safety_probes(
    golden: _GoldenSet,
    approved: _EvidenceFixture,
    rejected: _EvidenceFixture,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for probe in golden.safety_probes:
        provider = _provider_for_probe(probe.id)
        diagnosis, retrieval, currentness = _probe_inputs(
            probe.id,
            approved,
            rejected,
        )
        guarded = RagGuardrailService(
            provider=provider,
            snapshot_currentness=currentness,
        ).generate(diagnosis=diagnosis, retrieval=retrieval)
        _require(
            guarded.status is RagGuardrailStatus.REFUSED
            and guarded.refusal is not None,
            "Unsafe golden evidence was not refused.",
        )
        if guarded.refusal is None:
            raise AssertionError("Unreachable guardrail state.")
        refusal = guarded.refusal.code.value
        _require(
            refusal == probe.expected_refusal,
            "Golden safety refusal diverged.",
        )
        _require(
            provider.call_count == probe.expected_provider_calls,
            "Golden safety provider call count diverged.",
        )
        results.append(
            {
                "id": probe.id,
                "provider_calls": provider.call_count,
                "refusal": refusal,
                "status": guarded.status.value,
            }
        )
    return results


def _prediction_for(scenario: ScenarioId, *, top_k: int) -> ModelPrediction:
    if top_k != 1:
        raise ValueError("Golden top-k is invalid.")
    if scenario == "out_of_distribution":
        return ModelPrediction(
            disposition=ModelDisposition.OUT_OF_DISTRIBUTION,
            abstention_reason=ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION,
            diagnosis=None,
            support_score=0.05,
            model_id=MODEL_ID,
            neighbors=(_neighbor("golden_reference_fault", distance=1.5),),
            retrieval_key=None,
        )
    if scenario == "normal":
        disposition = ModelDisposition.NORMAL
        code = "golden_normal"
        summary = "Condição inteiramente sintética dentro da faixa esperada."
        retrieval_key = None
        support_score = 0.98
    elif scenario == "documented_fault":
        disposition = ModelDisposition.FAULT
        code = "golden_documented_fault"
        summary = "Falha sintética ligada a evidência documental aprovada."
        retrieval_key = "golden-documented-fault"
        support_score = 0.92
    elif scenario == "undocumented_fault":
        disposition = ModelDisposition.FAULT
        code = "golden_undocumented_fault"
        summary = "Falha sintética sem evidência documental elegível."
        retrieval_key = "golden-undocumented-fault"
        support_score = 0.79
    else:
        disposition = ModelDisposition.FAULT
        code = "golden_degraded_fault"
        summary = "Falha sintética com provider indisponível."
        retrieval_key = "golden-degraded-fault"
        support_score = 0.72
    return ModelPrediction(
        disposition=disposition,
        abstention_reason=None,
        diagnosis=PublicDiagnosis(code=code, summary=summary),
        support_score=support_score,
        model_id=MODEL_ID,
        neighbors=(_neighbor(code, distance=0.2),),
        retrieval_key=retrieval_key,
    )


def _neighbor(fault_code: str, *, distance: float) -> OpaqueNeighbor:
    indexed_fault_code = f"fault_{fault_code}"
    return OpaqueNeighbor(
        neighbor_ref=f"neighbor_{fault_code}",
        rank=1,
        fault_code=indexed_fault_code,
        distance=distance,
    )


def _evidence_result(
    fault_class: str,
    snapshot: RankedKnowledgeSnapshot,
) -> GovernedRetrievalResult:
    return GovernedRetrievalResult(
        status=GovernedRetrievalStatus.EVIDENCE,
        fault_class=fault_class,
        policy_schema_version=_POLICY.schema_version,
        policy_version=_POLICY.policy_version,
        minimum_score=_POLICY.minimum_score,
        policy_sha256=_POLICY.policy_sha256,
        mapping_version=MAPPING_VERSION,
        mapping_sha256=MAPPING_SHA256,
        evidence=(snapshot,),
    )


def _no_evidence_result(fault_class: str) -> GovernedRetrievalResult:
    return GovernedRetrievalResult(
        status=GovernedRetrievalStatus.NO_EVIDENCE,
        fault_class=fault_class,
        policy_schema_version=_POLICY.schema_version,
        policy_version=_POLICY.policy_version,
        minimum_score=_POLICY.minimum_score,
        policy_sha256=_POLICY.policy_sha256,
        mapping_version=MAPPING_VERSION,
        mapping_sha256=MAPPING_SHA256,
        evidence=(),
    )


def _provider_for_probe(probe: SafetyProbeId) -> FakeGenerationProvider:
    if probe != "invented_citation":
        return FakeGenerationProvider()
    fault_class = "golden-invented-citation"
    invented = {"evidence_id": "chunk_golden_invented_01"}
    return FakeGenerationProvider(
        response_text=json.dumps(
            {
                "diagnostic_support": {
                    "assessment": "Synthetic assessment with an invented citation.",
                    "citations": [invented],
                    "fault_code": fault_class,
                    "status": "supported",
                },
                "prescriptions": [
                    {
                        "action": "Synthetic action that must be refused.",
                        "citations": [invented],
                        "rationale": "The citation is intentionally not supplied.",
                    }
                ],
                "schema_version": GENERATION_CONTRACT_VERSION,
                "warnings": [],
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _probe_inputs(
    probe: SafetyProbeId,
    approved: _EvidenceFixture,
    rejected: _EvidenceFixture,
) -> tuple[
    GenerationDiagnosis,
    GovernedRetrievalResult,
    _ExactSnapshotCurrentness,
]:
    if probe == "missing_evidence":
        fault_class = "golden-missing-evidence"
        return (
            GenerationDiagnosis(
                fault_code=fault_class,
                technical_summary="Synthetic diagnosis without documentary evidence.",
            ),
            _no_evidence_result(fault_class),
            _ExactSnapshotCurrentness(
                fault_classes=frozenset({fault_class}),
                snapshot=approved.snapshot,
            ),
        )
    if probe == "rejected_evidence":
        fault_class = "golden-rejected-evidence"
        return (
            GenerationDiagnosis(
                fault_code=fault_class,
                technical_summary="Synthetic diagnosis with rejected evidence.",
            ),
            _evidence_result(fault_class, rejected.snapshot),
            _ExactSnapshotCurrentness(
                fault_classes=frozenset({fault_class}),
                snapshot=approved.snapshot,
            ),
        )
    fault_class = "golden-invented-citation"
    return (
        GenerationDiagnosis(
            fault_code=fault_class,
            technical_summary="Synthetic diagnosis for citation validation.",
        ),
        _evidence_result(fault_class, approved.snapshot),
        _ExactSnapshotCurrentness(
            fault_classes=frozenset({fault_class}),
            snapshot=approved.snapshot,
        ),
    )


def _request_for(golden: _GoldenSet, scenario: _GoldenScenario) -> AnalysisRequest:
    values = dict(golden.feature_template)
    values["rpm"] = scenario.rpm
    try:
        return AnalysisRequest.model_validate(
            {
                "features": values,
                "top_k": golden.configuration.top_k,
            }
        )
    except Exception:
        raise ProductGoldenError("Golden analysis request is invalid.") from None


def _call_snapshot(
    model: _GoldenModelPort,
    retrieval: _GoldenRetrievalPort,
    provider: _GoldenScenarioProvider,
) -> dict[str, int]:
    return {
        "generation": provider.metrics.attempts,
        "model": model.metrics.attempts,
        "provider": provider.provider_call_count,
        "retrieval": retrieval.metrics.attempts,
    }


def _json_object(response: Response) -> dict[str, object]:
    try:
        value = cast(object, response.json())
    except Exception:
        raise ProductGoldenError("Golden HTTP response is invalid.") from None
    if type(value) is not dict:
        raise ProductGoldenError("Golden HTTP response is invalid.")
    return cast(dict[str, object], value)


def _required_text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if type(value) is not str:
        raise ProductGoldenError("Golden HTTP response is incomplete.")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProductGoldenError(message)


if __name__ == "__main__":
    raise SystemExit(main())
