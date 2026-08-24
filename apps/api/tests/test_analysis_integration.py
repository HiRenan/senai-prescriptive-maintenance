"""Integration tests for the explicit API v1 analysis composition."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock
from typing import Final, cast
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from prescriptive_maintenance import analysis_integration as analysis_integration_module
from prescriptive_maintenance.analysis_integration import (
    PERSISTED_GENERATION_PROMPT_ID,
    AnalysisIntegrationConfigurationError,
    AnalysisRuntimeAuthorization,
    IntegratedAnalysisService,
    PrescriptionProjectionPolicy,
    SimilarityCheckedModelPort,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    AnalysisFeatures,
    AnalysisRequest,
    AnalysisResponse,
    Diagnosis,
    OpaqueNeighbor,
    PrescriptionPriority,
)
from prescriptive_maintenance.generation import (
    GENERATION_CONTRACT_VERSION,
    FakeGenerationProvider,
    ProviderDisabledError,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalBinding,
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import RankedKnowledgeSnapshot
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.modeling import (
    InMemorySimilarityIndexAdapter,
    LoadedSimilarityIndex,
    SimilarityArtifactFile,
    SimilarityIndexCompatibility,
    SimilarityIndexManifest,
    SimilarityIndexPort,
    SimilarityIndexRecord,
    SimilarityIndexSelector,
    SimilarityNeighbor,
    SimilarityPreprocessorState,
    SimilarityQuery,
)
from prescriptive_maintenance.persistence import (
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    InMemoryStore,
    InMemoryUnitOfWork,
    PostgresConnectionFactory,
    PostgresUnitOfWork,
    upgrade,
)
from prescriptive_maintenance.persistence.migrations import (
    PostgresConnection,
    PostgresRow,
)
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPrediction,
    PortUnavailableError,
)
from prescriptive_maintenance.prescription_orchestration import (
    PrescriptionOrchestrationBinding,
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationResult,
    PrescriptionOrchestrationService,
)
from prescriptive_maintenance.services import (
    AnalysisNotFoundError,
    AnalysisUnavailableError,
)
from prescriptive_maintenance.settings import Settings
from psycopg import Connection, sql
from psycopg.rows import RowFactory, dict_row

_DATASET_ID: Final = "1" * 64
_MODEL_ID: Final = "model_synthetic_authorized_v1"
_INDEX_ID: Final = f"similarity_index_v1_{'2' * 32}"
_INDEX_CONTENT_SHA256: Final = "2" * 64
_MODEL_CONTENT_SHA256: Final = "4" * 64
_SCHEMA_ID: Final = "5" * 64
_MAPPING_VERSION: Final = "synthetic-mapping.v1"
_MAPPING_SHA256: Final = "6" * 64
_PROVIDER_ID: Final = "synthetic-provider"
_DOCUMENT_ID: Final = "doc_synthetic_manual"
_DOCUMENT_VERSION_ID: Final = "docver_synthetic_manual_v1"
_CHUNK_REF: Final = "chunk_synthetic_manual_01"
_SECOND_CHUNK_REF: Final = "chunk_synthetic_manual_02"
_NEIGHBOR_REF: Final = "neighbor_synthetic_001"
_FAULT_CODE: Final = "fault_synthetic"
_RETRIEVAL_KEY: Final = "fault-synthetic"
_NOW: Final = datetime(2032, 1, 2, 3, 4, 5, tzinfo=UTC)
_DATABASE_URL_VARIABLE: Final = "PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL"
_TEST_DATABASE_URL: Final = os.environ.get(_DATABASE_URL_VARIABLE)
_ROW_FACTORY: Final = cast(RowFactory[PostgresRow], dict_row)

_GOVERNED_POLICY = build_governed_retrieval_policy(
    policy_version="synthetic-retrieval.v1",
    minimum_score=0.5,
)
_OTHER_GOVERNED_POLICY = build_governed_retrieval_policy(
    policy_version="synthetic-retrieval.v2",
    minimum_score=0.75,
)


class _AnalysisIdSequence:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self) -> str:
        with self._lock:
            self._value += 1
            return f"ana_synthetic_integrated_{self._value}"


class _FailingResultCache(dict[str, AnalysisResponse]):
    def __setitem__(self, key: str, value: AnalysisResponse) -> None:
        del key, value
        raise RuntimeError("SYNTHETIC_CACHE_PRIVATE_DETAIL")


class _ScenarioModel:
    def __init__(
        self,
        disposition: ModelDisposition,
        *,
        neighbor_distance: float = 0.25,
        documented: bool = True,
    ) -> None:
        self._disposition = disposition
        self._neighbor_distance = neighbor_distance
        self._documented = documented
        self.calls = 0

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        del features
        self.calls += 1
        diagnosis = (
            None
            if self._disposition is ModelDisposition.OUT_OF_DISTRIBUTION
            else Diagnosis(
                code=(
                    "normal"
                    if self._disposition is ModelDisposition.NORMAL
                    else _FAULT_CODE
                ),
                summary="Diagnóstico sintético autorizado para teste.",
            )
        )
        return ModelPrediction(
            disposition=self._disposition,
            abstention_reason=(
                ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION
                if self._disposition is ModelDisposition.OUT_OF_DISTRIBUTION
                else None
            ),
            diagnosis=diagnosis,
            support_score=0.8,
            model_id=_MODEL_ID,
            neighbors=tuple(
                OpaqueNeighbor(
                    neighbor_ref=_NEIGHBOR_REF,
                    rank=1,
                    fault_code=_FAULT_CODE,
                    distance=self._neighbor_distance,
                )
                for _ in range(min(top_k, 1))
            ),
            retrieval_key=(
                _RETRIEVAL_KEY
                if self._disposition is ModelDisposition.FAULT and self._documented
                else None
            ),
        )


class _StaticSimilarityIndex(SimilarityIndexPort):
    def __init__(
        self,
        *,
        neighbor_ref: str = _NEIGHBOR_REF,
        rank: int = 1,
        fault_code: str = _FAULT_CODE,
        distance: float = 0.25000003,
    ) -> None:
        self._neighbor_ref = neighbor_ref
        self._rank = rank
        self._fault_code = fault_code
        self._distance = distance
        self.queries: list[SimilarityQuery] = []

    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]:
        self.queries.append(query)
        return (
            SimilarityNeighbor(
                opaque_id=self._neighbor_ref,
                rank=self._rank,
                fault_code=self._fault_code,
                distance=self._distance,
            ),
        )


class _StaticGovernedRetrieval:
    def __init__(
        self,
        status: GovernedRetrievalStatus,
        *,
        include_uncited_evidence: bool = False,
    ) -> None:
        self._status = status
        self._include_uncited_evidence = include_uncited_evidence
        self.calls = 0

    @property
    def runtime_binding(self) -> GovernedRetrievalBinding:
        return GovernedRetrievalBinding(
            policy_schema_version=_GOVERNED_POLICY.schema_version,
            policy_version=_GOVERNED_POLICY.policy_version,
            policy_sha256=_GOVERNED_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
        )

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        assert disposition is ModelDisposition.FAULT
        assert fault_class == _RETRIEVAL_KEY
        assert top_k >= 1
        self.calls += 1
        evidence_items = [
            RankedKnowledgeSnapshot(
                document_id=_DOCUMENT_ID,
                document_version=_DOCUMENT_VERSION_ID,
                chunk_id=_CHUNK_REF,
                page_number=2,
                section_id="section_synthetic_manual_01",
                content="Synthetic approved maintenance evidence.",
                content_sha256=(
                    "f23817e01d98e6e3eb85db764ddc9410a71ed9ca1cdd3d20c206a3403d5374ba"
                ),
                score=0.9,
            )
        ]
        if self._include_uncited_evidence:
            evidence_items.append(
                RankedKnowledgeSnapshot(
                    document_id=_DOCUMENT_ID,
                    document_version=_DOCUMENT_VERSION_ID,
                    chunk_id=_SECOND_CHUNK_REF,
                    page_number=3,
                    section_id="section_synthetic_manual_02",
                    content="Second synthetic approved maintenance evidence.",
                    content_sha256=(
                        "46d8f65b69f90b3120014ee139a0fca7c570007b829b0aa8158b9bafae6141c9"
                    ),
                    score=0.8,
                )
            )
        evidence = (
            tuple(evidence_items)
            if self._status is GovernedRetrievalStatus.EVIDENCE
            else ()
        )
        return GovernedRetrievalResult(
            status=self._status,
            fault_class=_RETRIEVAL_KEY,
            policy_schema_version=_GOVERNED_POLICY.schema_version,
            policy_version=_GOVERNED_POLICY.policy_version,
            minimum_score=_GOVERNED_POLICY.minimum_score,
            policy_sha256=_GOVERNED_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
            evidence=evidence,
        )


class _CurrentSnapshots:
    def snapshots_are_current(self, **kwargs: object) -> bool:
        assert kwargs["mapping_sha256"] == _MAPPING_SHA256
        return True


class _StaticOrchestration:
    def __init__(
        self,
        result: PrescriptionOrchestrationResult,
        *,
        runtime_binding: PrescriptionOrchestrationBinding | None = None,
    ) -> None:
        self._result = result
        self._runtime_binding = runtime_binding or PrescriptionOrchestrationBinding(
            prompt_id=PERSISTED_GENERATION_PROMPT_ID,
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=1.0,
            retrieval_policy_version=_GOVERNED_POLICY.policy_version,
            retrieval_policy_sha256=_GOVERNED_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
        )

    @property
    def runtime_binding(self) -> PrescriptionOrchestrationBinding:
        return replace(self._runtime_binding)

    def orchestrate(
        self,
        prediction: object,
        *,
        top_k: object,
    ) -> PrescriptionOrchestrationResult:
        del prediction, top_k
        return self._result


def _features() -> AnalysisFeatures:
    return AnalysisFeatures.model_validate(
        {name: float(index + 1) for index, name in enumerate(ANALYSIS_FEATURE_NAMES)}
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(features=_features(), top_k=1)


def _projection_policy(
    priorities: dict[str, PrescriptionPriority] | None = None,
) -> PrescriptionProjectionPolicy:
    return build_prescription_projection_policy(
        policy_version="synthetic-priority.v1",
        priorities={_FAULT_CODE: PrescriptionPriority.ROUTINE}
        if priorities is None
        else priorities,
    )


def _authorization(
    policy: PrescriptionProjectionPolicy,
    *,
    retrieval_policy_version: str = _GOVERNED_POLICY.policy_version,
    retrieval_policy_sha256: str = _GOVERNED_POLICY.policy_sha256,
    mapping_version: str = _MAPPING_VERSION,
    mapping_sha256: str = _MAPPING_SHA256,
    prompt_id: str = PERSISTED_GENERATION_PROMPT_ID,
    provider_id: str = _PROVIDER_ID,
    provider_timeout_seconds: float = 1.0,
) -> AnalysisRuntimeAuthorization:
    return build_analysis_runtime_authorization(
        authorization_version="synthetic-analysis.v1",
        dataset_id=_DATASET_ID,
        model_id=_MODEL_ID,
        index_id=_INDEX_ID,
        retrieval_policy_version=retrieval_policy_version,
        retrieval_policy_sha256=retrieval_policy_sha256,
        mapping_version=mapping_version,
        mapping_sha256=mapping_sha256,
        prompt_id=prompt_id,
        provider_id=provider_id,
        provider_timeout_seconds=provider_timeout_seconds,
        projection_policy=policy,
    )


def _mismatched_authorization(
    policy: PrescriptionProjectionPolicy,
    field: str,
) -> AnalysisRuntimeAuthorization:
    if field == "prompt_id":
        return _authorization(policy, prompt_id="prompt_synthetic_v2")
    if field == "provider_id":
        return _authorization(policy, provider_id="synthetic-provider-v2")
    if field == "provider_timeout_seconds":
        return _authorization(policy, provider_timeout_seconds=2.0)
    if field == "retrieval_policy_version":
        return _authorization(
            policy,
            retrieval_policy_version=_OTHER_GOVERNED_POLICY.policy_version,
        )
    if field == "retrieval_policy_sha256":
        return _authorization(
            policy,
            retrieval_policy_sha256=_OTHER_GOVERNED_POLICY.policy_sha256,
        )
    if field == "mapping_version":
        return _authorization(policy, mapping_version="synthetic-mapping.v2")
    if field == "mapping_sha256":
        return _authorization(policy, mapping_sha256="7" * 64)
    raise AssertionError("Unsupported synthetic authorization field.")


def _traceable_document() -> DocumentMetadata:
    return DocumentMetadata(
        document_id=_DOCUMENT_ID,
        created_at=_NOW,
        versions=(
            DocumentVersionMetadata(
                document_version_id=_DOCUMENT_VERSION_ID,
                document_id=_DOCUMENT_ID,
                source_sha256="7" * 64,
                created_at=_NOW,
                chunks=(
                    ChunkReference(
                        chunk_ref=_CHUNK_REF,
                        document_id=_DOCUMENT_ID,
                        document_version_id=_DOCUMENT_VERSION_ID,
                        page_number=2,
                    ),
                    ChunkReference(
                        chunk_ref=_SECOND_CHUNK_REF,
                        document_id=_DOCUMENT_ID,
                        document_version_id=_DOCUMENT_VERSION_ID,
                        page_number=3,
                    ),
                ),
            ),
        ),
    )


def _seed_document(store: InMemoryStore) -> None:
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(_traceable_document())
        transaction.commit()


def _checked_model(
    scenario: _ScenarioModel,
    authorization: AnalysisRuntimeAuthorization,
) -> SimilarityCheckedModelPort:
    return SimilarityCheckedModelPort(
        model=scenario,
        similarity=_StaticSimilarityIndex(),
        selector=SimilarityIndexSelector(
            index_id=_INDEX_ID,
            model_id=_MODEL_ID,
            compatibility=SimilarityIndexCompatibility(
                dataset_id=_DATASET_ID,
                schema_id=_SCHEMA_ID,
            ),
        ),
        authorization=authorization,
    )


def _build_service(
    *,
    disposition: ModelDisposition,
    retrieval_status: GovernedRetrievalStatus,
    provider: FakeGenerationProvider,
    store: InMemoryStore | None = None,
    projection_policy: PrescriptionProjectionPolicy | None = None,
    include_uncited_evidence: bool = False,
    orchestration_result: PrescriptionOrchestrationResult | None = None,
    orchestration_binding: PrescriptionOrchestrationBinding | None = None,
    clock: Callable[[], datetime] = lambda: _NOW,
) -> tuple[
    IntegratedAnalysisService,
    InMemoryStore,
    _ScenarioModel,
    _StaticGovernedRetrieval,
]:
    selected_store = store or InMemoryStore()
    selected_projection = projection_policy or _projection_policy()
    authorization = _authorization(selected_projection)
    scenario_model = _ScenarioModel(disposition)
    model = _checked_model(scenario_model, authorization)
    retrieval = _StaticGovernedRetrieval(
        retrieval_status,
        include_uncited_evidence=include_uncited_evidence,
    )
    orchestration = (
        PrescriptionOrchestrationService(
            retrieval=retrieval,
            provider=provider,
            snapshot_currentness=_CurrentSnapshots(),
            config=PrescriptionOrchestrationConfig(
                provider_id=_PROVIDER_ID,
                provider_timeout_seconds=1.0,
            ),
            monotonic_clock=_MonotonicSequence(),
        )
        if orchestration_result is None
        else _StaticOrchestration(
            orchestration_result,
            runtime_binding=orchestration_binding,
        )
    )
    return (
        IntegratedAnalysisService(
            model=model,
            orchestration=orchestration,
            authorization=authorization,
            projection_policy=selected_projection,
            unit_of_work_factory=lambda: InMemoryUnitOfWork(selected_store),
            clock=clock,
            analysis_id_factory=_AnalysisIdSequence(),
        ),
        selected_store,
        scenario_model,
        retrieval,
    )


class _MonotonicSequence:
    def __init__(self) -> None:
        self._value = 10.0
        self._lock = Lock()

    def __call__(self) -> float:
        with self._lock:
            self._value += 0.001
            return self._value


@pytest.mark.parametrize(
    "authorization_field",
    (
        "prompt_id",
        "provider_id",
        "provider_timeout_seconds",
        "retrieval_policy_version",
        "retrieval_policy_sha256",
        "mapping_version",
        "mapping_sha256",
    ),
)
@pytest.mark.parametrize(
    ("disposition", "documented"),
    (
        (ModelDisposition.NORMAL, True),
        (ModelDisposition.OUT_OF_DISTRIBUTION, True),
        (ModelDisposition.FAULT, False),
    ),
)
def test_complete_runtime_binding_is_checked_before_every_journey(
    authorization_field: str,
    disposition: ModelDisposition,
    documented: bool,
) -> None:
    projection = _projection_policy()
    authorization = _mismatched_authorization(projection, authorization_field)
    scenario = _ScenarioModel(disposition, documented=documented)
    retrieval = _StaticGovernedRetrieval(GovernedRetrievalStatus.EVIDENCE)
    provider = FakeGenerationProvider()
    orchestration = PrescriptionOrchestrationService(
        retrieval=retrieval,
        provider=provider,
        snapshot_currentness=_CurrentSnapshots(),
        config=PrescriptionOrchestrationConfig(
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=1.0,
        ),
        monotonic_clock=_MonotonicSequence(),
    )

    with pytest.raises(
        AnalysisIntegrationConfigurationError,
        match="not authorized",
    ):
        IntegratedAnalysisService(
            model=_checked_model(scenario, authorization),
            orchestration=orchestration,
            authorization=authorization,
            projection_policy=projection,
            unit_of_work_factory=lambda: InMemoryUnitOfWork(InMemoryStore()),
        )

    assert scenario.calls == 0
    assert retrieval.calls == 0
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("disposition", "retrieval_status", "expected_outcome", "provider_calls"),
    (
        (
            ModelDisposition.NORMAL,
            GovernedRetrievalStatus.NO_EVIDENCE,
            "normal",
            0,
        ),
        (
            ModelDisposition.FAULT,
            GovernedRetrievalStatus.EVIDENCE,
            "documented_fault",
            1,
        ),
        (
            ModelDisposition.FAULT,
            GovernedRetrievalStatus.NO_EVIDENCE,
            "undocumented_fault",
            0,
        ),
        (
            ModelDisposition.OUT_OF_DISTRIBUTION,
            GovernedRetrievalStatus.NO_EVIDENCE,
            "out_of_distribution",
            0,
        ),
    ),
)
def test_integrated_service_composes_four_non_failure_outcomes(
    disposition: ModelDisposition,
    retrieval_status: GovernedRetrievalStatus,
    expected_outcome: str,
    provider_calls: int,
) -> None:
    provider = FakeGenerationProvider()
    service, store, _, retrieval = _build_service(
        disposition=disposition,
        retrieval_status=retrieval_status,
        provider=provider,
    )
    if retrieval_status is GovernedRetrievalStatus.EVIDENCE:
        _seed_document(store)

    response = service.analyze(_request())

    assert response.root.outcome.value == expected_outcome
    assert provider.call_count == provider_calls
    assert retrieval.calls == (1 if disposition is ModelDisposition.FAULT else 0)
    assert service.get(response.root.analysis_id) == response

    with InMemoryUnitOfWork(store) as query:
        persisted = query.analyses.get(response.root.analysis_id)
    assert persisted is not None
    assert persisted.dataset_id == _DATASET_ID
    assert persisted.model_id == _MODEL_ID
    assert persisted.prompt_id == PERSISTED_GENERATION_PROMPT_ID
    assert persisted.configuration_id.startswith("config_")
    assert persisted.document_version_ids == (
        (_DOCUMENT_VERSION_ID,)
        if retrieval_status is GovernedRetrievalStatus.EVIDENCE
        else ()
    )


def test_disabled_provider_degrades_without_presenting_unused_evidence() -> None:
    provider = FakeGenerationProvider(
        error=ProviderDisabledError("private provider configuration")
    )
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=provider,
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "degraded"
    assert response.diagnosis is not None
    assert response.neighbors
    assert response.citations == ()
    assert response.prescription is None
    assert provider.call_count == 1
    assert "private provider configuration" not in response.model_dump_json()


def test_missing_priority_mapping_degrades_without_a_fallback() -> None:
    policy = _projection_policy({})
    provider = FakeGenerationProvider()
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=provider,
        projection_policy=policy,
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "degraded"
    assert response.prescription is None
    assert response.warnings[0].code == "prescription_projection_unavailable"
    assert response.citations


def test_documented_projection_exposes_only_evidence_cited_by_generation() -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(),
        include_uncited_evidence=True,
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "documented_fault"
    assert tuple(item.chunk for item in response.citations) == (_CHUNK_REF,)
    with InMemoryUnitOfWork(store) as query:
        persisted = query.analyses.get(response.analysis_id)
    assert persisted is not None
    assert tuple(item.chunk_ref for item in persisted.evidence_references) == (
        _CHUNK_REF,
        _SECOND_CHUNK_REF,
    )


def test_public_citations_union_diagnostic_and_included_prescription_evidence() -> None:
    provider = FakeGenerationProvider(
        response_text=json.dumps(
            {
                "schema_version": GENERATION_CONTRACT_VERSION,
                "diagnostic_support": {
                    "fault_code": _RETRIEVAL_KEY,
                    "status": "supported",
                    "assessment": "Synthetic supported assessment.",
                    "citations": [{"evidence_id": _CHUNK_REF}],
                },
                "prescriptions": [
                    {
                        "action": "Inspect the second synthetic source.",
                        "rationale": "The second source supports this action.",
                        "citations": [{"evidence_id": _SECOND_CHUNK_REF}],
                    }
                ],
                "warnings": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=provider,
        include_uncited_evidence=True,
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "documented_fault"
    assert tuple(item.chunk for item in response.citations) == (
        _CHUNK_REF,
        _SECOND_CHUNK_REF,
    )


def test_citation_not_present_in_trace_fails_to_degraded_without_citations() -> None:
    raw_orchestration = PrescriptionOrchestrationService(
        retrieval=_StaticGovernedRetrieval(GovernedRetrievalStatus.EVIDENCE),
        provider=FakeGenerationProvider(),
        snapshot_currentness=_CurrentSnapshots(),
        config=PrescriptionOrchestrationConfig(
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=1.0,
        ),
        monotonic_clock=_MonotonicSequence(),
    ).orchestrate(
        _ScenarioModel(ModelDisposition.FAULT).predict(_features(), top_k=1),
        top_k=1,
    )
    assert raw_orchestration.retrieval_trace is not None
    original_evidence = raw_orchestration.retrieval_trace.evidence[0]
    tampered_trace = replace(
        raw_orchestration.retrieval_trace,
        evidence=(replace(original_evidence, chunk_ref=_SECOND_CHUNK_REF),),
    )
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(),
        orchestration_result=replace(
            raw_orchestration,
            retrieval_trace=tampered_trace,
        ),
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "degraded"
    assert response.citations == ()
    assert response.prescription is None


@pytest.mark.parametrize("identity", ("policy", "mapping"))
def test_orchestration_result_identity_is_revalidated_after_construction(
    identity: str,
) -> None:
    raw_orchestration = PrescriptionOrchestrationService(
        retrieval=_StaticGovernedRetrieval(GovernedRetrievalStatus.EVIDENCE),
        provider=FakeGenerationProvider(),
        snapshot_currentness=_CurrentSnapshots(),
        config=PrescriptionOrchestrationConfig(
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=1.0,
        ),
        monotonic_clock=_MonotonicSequence(),
    ).orchestrate(
        _ScenarioModel(ModelDisposition.FAULT).predict(_features(), top_k=1),
        top_k=1,
    )
    trace = raw_orchestration.retrieval_trace
    assert trace is not None
    tampered_trace = (
        replace(trace, policy_version="synthetic-retrieval.v2")
        if identity == "policy"
        else replace(trace, mapping_version="synthetic-mapping.v2")
    )
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(),
        orchestration_result=replace(
            raw_orchestration,
            retrieval_trace=tampered_trace,
        ),
    )

    with pytest.raises(AnalysisUnavailableError, match="orchestration"):
        service.analyze(_request())

    with InMemoryUnitOfWork(store) as transaction:
        assert transaction.analyses.get("ana_synthetic_integrated_1") is None


@pytest.mark.parametrize(
    ("assessment", "actions"),
    (
        ("a" * 501, ("Synthetic safe action.",)),
        ("Synthetic supported assessment.", ("a" * 301,)),
        ("Synthetic supported assessment.", ("Synthetic safe action.",) * 6),
    ),
)
def test_generated_content_outside_public_limits_degrades_without_truncation(
    assessment: str,
    actions: tuple[str, ...],
) -> None:
    citation = {"evidence_id": _CHUNK_REF}
    provider = FakeGenerationProvider(
        response_text=json.dumps(
            {
                "schema_version": GENERATION_CONTRACT_VERSION,
                "diagnostic_support": {
                    "fault_code": _RETRIEVAL_KEY,
                    "status": "supported",
                    "assessment": assessment,
                    "citations": [citation],
                },
                "prescriptions": [
                    {
                        "action": action,
                        "rationale": "Synthetic grounded rationale.",
                        "citations": [citation],
                    }
                    for action in actions
                ],
                "warnings": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=provider,
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "degraded"
    assert response.prescription is None
    serialized = response.model_dump_json()
    assert assessment not in serialized
    assert not any(action in serialized for action in actions)


def test_invalid_provider_output_degrades_without_presenting_retrieved_evidence() -> (
    None
):
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(response_text="private invalid response"),
    )
    _seed_document(store)

    response = service.analyze(_request()).root

    assert response.outcome.value == "degraded"
    assert response.citations == ()
    assert "private invalid response" not in response.model_dump_json()


def test_missing_persisted_evidence_rolls_back_and_does_not_publish_cache() -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(),
    )

    with pytest.raises(AnalysisUnavailableError, match="persisted"):
        service.analyze(_request())
    with pytest.raises(AnalysisNotFoundError):
        service.get("ana_synthetic_integrated_1")
    with InMemoryUnitOfWork(store) as query:
        assert query.analyses.get("ana_synthetic_integrated_1") is None


def test_similarity_binding_is_exact_and_divergence_fails_closed() -> None:
    policy = _projection_policy()
    authorization = _authorization(policy)
    selector = SimilarityIndexSelector(
        index_id=_INDEX_ID,
        model_id=_MODEL_ID,
        compatibility=SimilarityIndexCompatibility(
            dataset_id=_DATASET_ID,
            schema_id=_SCHEMA_ID,
        ),
    )
    model = _ScenarioModel(ModelDisposition.FAULT)
    adapter = SimilarityCheckedModelPort(
        model=model,
        similarity=_StaticSimilarityIndex(),
        selector=selector,
        authorization=authorization,
    )

    prediction = adapter.predict(_features(), top_k=1)

    assert prediction.neighbors[0].distance == 0.25
    assert adapter.dataset_id == _DATASET_ID
    assert adapter.model_id == _MODEL_ID
    assert adapter.index_id == _INDEX_ID

    divergent = SimilarityCheckedModelPort(
        model=model,
        similarity=_StaticSimilarityIndex(neighbor_ref="neighbor_synthetic_999"),
        selector=selector,
        authorization=authorization,
    )
    with pytest.raises(PortUnavailableError, match="unavailable"):
        divergent.predict(_features(), top_k=1)

    distance_divergent = SimilarityCheckedModelPort(
        model=model,
        similarity=_StaticSimilarityIndex(distance=0.250002),
        selector=selector,
        authorization=authorization,
    )
    with pytest.raises(PortUnavailableError, match="unavailable"):
        distance_divergent.predict(_features(), top_k=1)

    wrong_selector = replace(
        selector,
        compatibility=replace(selector.compatibility, dataset_id="f" * 64),
    )
    with pytest.raises(
        AnalysisIntegrationConfigurationError,
        match="not authorized",
    ):
        SimilarityCheckedModelPort(
            model=model,
            similarity=_StaticSimilarityIndex(),
            selector=wrong_selector,
            authorization=authorization,
        )


@pytest.mark.parametrize(
    "similarity",
    (
        _StaticSimilarityIndex(neighbor_ref="neighbor_synthetic_999"),
        _StaticSimilarityIndex(rank=2),
        _StaticSimilarityIndex(fault_code="fault_other"),
        _StaticSimilarityIndex(distance=0.250002),
        _StaticSimilarityIndex(distance=float("nan")),
        _StaticSimilarityIndex(distance=float("inf")),
    ),
)
def test_similarity_ranking_corruption_fails_closed(
    similarity: _StaticSimilarityIndex,
) -> None:
    policy = _projection_policy()
    authorization = _authorization(policy)
    adapter = SimilarityCheckedModelPort(
        model=_ScenarioModel(ModelDisposition.FAULT),
        similarity=similarity,
        selector=SimilarityIndexSelector(
            index_id=_INDEX_ID,
            model_id=_MODEL_ID,
            compatibility=SimilarityIndexCompatibility(
                dataset_id=_DATASET_ID,
                schema_id=_SCHEMA_ID,
            ),
        ),
        authorization=authorization,
    )

    with pytest.raises(PortUnavailableError, match="unavailable"):
        adapter.predict(_features(), top_k=1)


def test_real_in_memory_similarity_adapter_is_used_for_authorized_synthetic_set() -> (
    None
):
    policy = _projection_policy()
    authorization = _authorization(policy)
    selector = SimilarityIndexSelector(
        index_id=_INDEX_ID,
        model_id=_MODEL_ID,
        compatibility=SimilarityIndexCompatibility(
            dataset_id=_DATASET_ID,
            schema_id=_SCHEMA_ID,
        ),
    )
    files = tuple(
        SimilarityArtifactFile(
            filename=filename,
            media_type=(
                "application/x-npy" if filename == "vectors.npy" else "application/json"
            ),
            physical_sha256=("8", "9", "a")[index] * 64,
            logical_sha256=("8", "9", "a")[index] * 64,
        )
        for index, filename in enumerate(
            ("preprocessor.json", "records.json", "vectors.npy")
        )
    )
    index = LoadedSimilarityIndex(
        manifest=SimilarityIndexManifest(
            artifact_schema_version=1,
            selector=selector,
            content_sha256=_INDEX_CONTENT_SHA256,
            source_model_id=_MODEL_ID,
            source_model_content_sha256=_MODEL_CONTENT_SHA256,
            record_count=1,
            vector_dtype=np.dtype("<f4").str,
            distance_order="distance_ascending",
            distance_tie_break="opaque_id_ascending",
            files=files,
        ),
        preprocessor=SimilarityPreprocessorState(
            mean=(0.0,) * len(ANALYSIS_FEATURE_NAMES),
            scale=(1.0,) * len(ANALYSIS_FEATURE_NAMES),
            variance=(1.0,) * len(ANALYSIS_FEATURE_NAMES),
            sample_count=1,
        ),
        records=(
            SimilarityIndexRecord(
                opaque_id=_NEIGHBOR_REF,
                fault_code=_FAULT_CODE,
            ),
        ),
        vectors=np.zeros((1, len(ANALYSIS_FEATURE_NAMES)), dtype=np.float32),
    )
    raw_model = _ScenarioModel(ModelDisposition.FAULT, neighbor_distance=0.0)
    adapter = SimilarityCheckedModelPort(
        model=raw_model,
        similarity=InMemorySimilarityIndexAdapter(index),
        selector=selector,
        authorization=authorization,
    )
    zero_features = AnalysisFeatures.model_validate(
        {name: 0.0 for name in ANALYSIS_FEATURE_NAMES}
    )

    prediction = adapter.predict(zero_features, top_k=1)

    assert prediction.neighbors == (
        OpaqueNeighbor(
            neighbor_ref=_NEIGHBOR_REF,
            rank=1,
            fault_code=_FAULT_CODE,
            distance=0.0,
        ),
    )


def test_projection_failure_is_not_misreported_as_persistence(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.NORMAL,
        retrieval_status=GovernedRetrievalStatus.NO_EVIDENCE,
        provider=FakeGenerationProvider(),
    )

    def fail_projection(**values: object) -> object:
        del values
        raise RuntimeError("SYNTHETIC_PROJECTION_PRIVATE_DETAIL")

    monkeypatch.setattr(
        analysis_integration_module,
        "_project_result",
        fail_projection,
    )
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="prescriptive_maintenance.analysis"),
        TestClient(create_app(analysis_service=service, settings=settings)) as client,
    ):
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
            headers={"X-Correlation-ID": "sen46-projection-failure"},
        )

    assert response.status_code == 503
    assert "SYNTHETIC_PROJECTION_PRIVATE_DETAIL" not in response.text
    failed = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
        and json.loads(record.message)["event"] == "analysis_failed"
    )
    assert tuple(item["stage"] for item in failed) == ("projection",)
    with InMemoryUnitOfWork(store) as transaction:
        assert transaction.analyses.get("ana_synthetic_integrated_1") is None
    with pytest.raises(AnalysisNotFoundError):
        service.get("ana_synthetic_integrated_1")


def test_metadata_failure_stops_before_persistence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.NORMAL,
        retrieval_status=GovernedRetrievalStatus.NO_EVIDENCE,
        provider=FakeGenerationProvider(),
        clock=lambda: datetime(2032, 1, 2, 3, 4, 5),
    )
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="prescriptive_maintenance.analysis"),
        TestClient(create_app(analysis_service=service, settings=settings)) as client,
    ):
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
            headers={"X-Correlation-ID": "sen46-metadata-failure"},
        )

    assert response.status_code == 503
    failed = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
        and json.loads(record.message)["event"] == "analysis_failed"
    )
    assert tuple(item["stage"] for item in failed) == ("metadata",)
    with InMemoryUnitOfWork(store) as transaction:
        assert transaction.analyses.get("ana_synthetic_integrated_1") is None


@pytest.mark.failure_matrix
def test_persistence_failure_is_classified_and_never_publishes_cache(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.FAULT,
        retrieval_status=GovernedRetrievalStatus.EVIDENCE,
        provider=FakeGenerationProvider(),
    )
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="prescriptive_maintenance.analysis"),
        TestClient(create_app(analysis_service=service, settings=settings)) as client,
    ):
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
            headers={"X-Correlation-ID": "sen46-persistence-failure"},
        )

    assert response.status_code == 503
    failed = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
        and json.loads(record.message)["event"] == "analysis_failed"
    )
    assert tuple(item["stage"] for item in failed) == ("persistence",)
    with InMemoryUnitOfWork(store) as transaction:
        assert transaction.analyses.get("ana_synthetic_integrated_1") is None
    with pytest.raises(AnalysisNotFoundError):
        service.get("ana_synthetic_integrated_1")


def test_cache_failure_after_commit_does_not_turn_success_into_503(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store, _, _ = _build_service(
        disposition=ModelDisposition.NORMAL,
        retrieval_status=GovernedRetrievalStatus.NO_EVIDENCE,
        provider=FakeGenerationProvider(),
    )
    monkeypatch.setattr(service, "_results", _FailingResultCache())
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="prescriptive_maintenance.analysis"),
        TestClient(create_app(analysis_service=service, settings=settings)) as client,
    ):
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
            headers={"X-Correlation-ID": "sen46-cache-failure"},
        )

    assert response.status_code == 200
    assert "SYNTHETIC_CACHE_PRIVATE_DETAIL" not in response.text
    analysis_id = response.json()["analysis_id"]
    with InMemoryUnitOfWork(store) as transaction:
        assert transaction.analyses.get(analysis_id) is not None
    records = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
    )
    cache_events = tuple(
        item for item in records if item["event"] == "analysis_cache_unavailable"
    )
    assert tuple(item["stage"] for item in cache_events) == ("cache",)
    completed = tuple(item for item in records if item["event"] == "analysis_completed")
    assert len(completed) == 1
    assert completed[0]["cache_published"] is False
    assert not any(item["event"] == "analysis_failed" for item in records)


def test_correlation_id_is_shared_by_allowlisted_analysis_stage_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _, _ = _build_service(
        disposition=ModelDisposition.NORMAL,
        retrieval_status=GovernedRetrievalStatus.NO_EVIDENCE,
        provider=FakeGenerationProvider(),
    )
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )

    with (
        caplog.at_level(
            logging.INFO,
            logger="prescriptive_maintenance.analysis",
        ),
        TestClient(create_app(analysis_service=service, settings=settings)) as client,
    ):
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
            headers={"X-Correlation-ID": "sen46-safe-request"},
        )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "sen46-safe-request"
    records = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
    )
    assert tuple(item["event"] for item in records) == (
        "analysis_model_completed",
        "analysis_orchestration_completed",
        "analysis_completed",
    )
    assert records[-1]["cache_published"] is True
    assert {item["correlation_id"] for item in records} == {"sen46-safe-request"}
    serialized = json.dumps(records)
    assert not any(
        forbidden in serialized
        for forbidden in ("features", "temperature_c", "system_prompt", "content")
    )


def test_correlation_context_and_cache_are_isolated_under_concurrency(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _, _ = _build_service(
        disposition=ModelDisposition.NORMAL,
        retrieval_status=GovernedRetrievalStatus.NO_EVIDENCE,
        provider=FakeGenerationProvider(),
    )
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    application = create_app(analysis_service=service, settings=settings)

    with (
        caplog.at_level(
            logging.INFO,
            logger="prescriptive_maintenance.analysis",
        ),
        TestClient(application) as client,
    ):

        def post_analysis(correlation_id: str):
            return client.post(
                "/analysis",
                json=_request().model_dump(mode="json"),
                headers={"X-Correlation-ID": correlation_id},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = tuple(
                executor.map(
                    post_analysis,
                    ("sen46-concurrent-a", "sen46-concurrent-b"),
                )
            )

    assert all(response.status_code == 200 for response in responses)
    analysis_ids = tuple(response.json()["analysis_id"] for response in responses)
    assert len(set(analysis_ids)) == 2
    records = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.analysis"
        and json.loads(record.message)["event"] == "analysis_completed"
    )
    assert {record["correlation_id"] for record in records} == {
        "sen46-concurrent-a",
        "sen46-concurrent-b",
    }


@pytest.fixture
def postgres_connection_factory() -> Iterator[PostgresConnectionFactory]:
    database_url = _TEST_DATABASE_URL
    if database_url is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")
    schema_name = f"sen46_{uuid4().hex}"
    assert re.fullmatch(r"sen46_[0-9a-f]{32}", schema_name) is not None
    admin = Connection[PostgresRow].connect(
        database_url,
        autocommit=True,
        row_factory=_ROW_FACTORY,
    )
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))

    def factory() -> PostgresConnection:
        connection = Connection[PostgresRow].connect(
            database_url,
            row_factory=_ROW_FACTORY,
        )
        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(schema_name)
            )
        )
        connection.commit()
        return connection

    connection = factory()
    upgrade(connection)
    connection.close()
    try:
        yield factory
    finally:
        admin.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
        )
        admin.close()


def test_integrated_analysis_round_trips_traceability_in_real_postgres(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    with PostgresUnitOfWork(postgres_connection_factory) as transaction:
        transaction.documents.add(_traceable_document())
        transaction.commit()

    policy = _projection_policy()
    authorization = _authorization(policy)
    raw_model = _ScenarioModel(ModelDisposition.FAULT)
    model = SimilarityCheckedModelPort(
        model=raw_model,
        similarity=_StaticSimilarityIndex(),
        selector=SimilarityIndexSelector(
            index_id=_INDEX_ID,
            model_id=_MODEL_ID,
            compatibility=SimilarityIndexCompatibility(
                dataset_id=_DATASET_ID,
                schema_id=_SCHEMA_ID,
            ),
        ),
        authorization=authorization,
    )
    provider = FakeGenerationProvider()
    retrieval = _StaticGovernedRetrieval(GovernedRetrievalStatus.EVIDENCE)
    service = IntegratedAnalysisService(
        model=model,
        orchestration=PrescriptionOrchestrationService(
            retrieval=retrieval,
            provider=provider,
            snapshot_currentness=_CurrentSnapshots(),
            config=PrescriptionOrchestrationConfig(
                provider_id=_PROVIDER_ID,
                provider_timeout_seconds=1.0,
            ),
            monotonic_clock=_MonotonicSequence(),
        ),
        authorization=authorization,
        projection_policy=policy,
        unit_of_work_factory=lambda: PostgresUnitOfWork(postgres_connection_factory),
        clock=lambda: _NOW,
        analysis_id_factory=_AnalysisIdSequence(),
    )

    response = service.analyze(_request())

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        persisted = query.analyses.get(response.root.analysis_id)
    assert persisted is not None
    assert persisted.outcome.value == "documented_fault"
    assert persisted.dataset_id == _DATASET_ID
    assert persisted.model_id == _MODEL_ID
    assert persisted.prompt_id == PERSISTED_GENERATION_PROMPT_ID
    assert tuple(
        (item.document_id, item.document_version_id, item.chunk_ref)
        for item in persisted.evidence_references
    ) == ((_DOCUMENT_ID, _DOCUMENT_VERSION_ID, _CHUNK_REF),)
