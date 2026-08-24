"""Fail-closed composition root for the configured analysis runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from prescriptive_maintenance.analysis_artifact_documents import (
    build_artifact_document_runtime,
)
from prescriptive_maintenance.analysis_artifacts import (
    MAX_JSON_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    content_sha256,
    load_artifacts_manifest,
    read_artifact_file,
    resolve_artifact_reference,
    semantic_sha256,
    verify_artifact_sha256,
)
from prescriptive_maintenance.analysis_integration import (
    PERSISTED_GENERATION_PROMPT_ID,
    IntegratedAnalysisService,
    SimilarityCheckedModelPort,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.contracts import (
    AnalysisRequest,
    AnalysisResponse,
    PrescriptionPriority,
)
from prescriptive_maintenance.fakes import build_synthetic_analysis_service
from prescriptive_maintenance.generation import (
    GENERATION_SYSTEM_PROMPT,
    FakeGenerationProvider,
    GenerationProvider,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedKnowledgeRetrievalService,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import (
    ApprovedKnowledgeRetrievalService,
    validate_fault_knowledge_mapping,
)
from prescriptive_maintenance.modeling.knn import KnnModelPortAdapter, load_knn_model
from prescriptive_maintenance.modeling.similarity_index import (
    InMemorySimilarityIndexAdapter,
    SimilarityIndexCompatibility,
    load_similarity_index,
)
from prescriptive_maintenance.operating_states import operating_state_policy_payload
from prescriptive_maintenance.persistence import UnitOfWork
from prescriptive_maintenance.prescription_orchestration import (
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationService,
)
from prescriptive_maintenance.services import (
    AnalysisLifecycleService,
    AnalysisUnavailableError,
)
from prescriptive_maintenance.settings import AnalysisMode, Settings


class AnalysisRuntimeCompositionError(RuntimeError):
    """Sanitized failure raised when an approved runtime cannot be composed."""


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeSummary:
    """Allowlisted aggregate metadata suitable for readiness and smoke output."""

    mode: AnalysisMode
    model_sample_count: int
    index_record_count: int
    approved_document_count: int
    indexed_chunk_count: int
    mapped_fault_count: int


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeComposition:
    """A composed service plus non-public handles used for verification."""

    mode: AnalysisMode
    service: AnalysisLifecycleService
    summary: AnalysisRuntimeSummary
    unit_of_work_factory: Callable[[], UnitOfWork] | None = None
    generation_provider: GenerationProvider | None = None


class ConfiguredAnalysisService:
    """Stable router dependency configured once the explicit mode is known."""

    def __init__(self) -> None:
        self._mode: AnalysisMode | None = None
        self._service: AnalysisLifecycleService | None = None
        self._lock = RLock()

    @property
    def mode(self) -> AnalysisMode | None:
        with self._lock:
            return self._mode

    @property
    def available(self) -> bool:
        with self._lock:
            return self._service is not None

    def select(self, mode: AnalysisMode) -> None:
        with self._lock:
            self._mode = mode
            self._service = None

    def configure(self, service: AnalysisLifecycleService) -> None:
        with self._lock:
            self._service = service

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        return self._selected_service().analyze(request)

    def get(self, analysis_id: str) -> AnalysisResponse:
        return self._selected_service().get(analysis_id)

    def _selected_service(self) -> AnalysisLifecycleService:
        with self._lock:
            service = self._service
        if service is None:
            raise AnalysisUnavailableError(
                "The configured analysis runtime is unavailable."
            )
        return service


def compose_analysis_runtime(settings: Settings) -> AnalysisRuntimeComposition:
    """Compose exactly the selected runtime mode without discovery or fallback."""

    if type(settings) is not Settings:
        raise AnalysisRuntimeCompositionError("Analysis runtime settings are invalid.")
    if settings.analysis_mode == "synthetic_demo":
        return AnalysisRuntimeComposition(
            mode="synthetic_demo",
            service=build_synthetic_analysis_service(),
            summary=AnalysisRuntimeSummary(
                mode="synthetic_demo",
                model_sample_count=0,
                index_record_count=0,
                approved_document_count=0,
                indexed_chunk_count=0,
                mapped_fault_count=0,
            ),
        )
    try:
        return _compose_artifacts_runtime(settings)
    except AnalysisRuntimeCompositionError:
        raise
    except Exception:
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        ) from None


def _compose_artifacts_runtime(settings: Settings) -> AnalysisRuntimeComposition:
    loaded = load_artifacts_manifest(settings)
    manifest = loaded.manifest
    root = loaded.root

    model_directory = resolve_artifact_reference(
        root,
        manifest.model.artifact.path,
        directory=True,
    )
    verify_artifact_sha256(
        model_directory / "manifest.json",
        manifest.model.artifact.manifest_sha256,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    model = load_knn_model(
        model_directory,
        expected_model_id=manifest.model.model_id,
    )
    if (
        model.dataset_id != manifest.model.dataset_id
        or model.content_sha256 != manifest.model.content_sha256
        or model.training_partition_sha256 != manifest.model.training_partition_sha256
        or semantic_sha256(operating_state_policy_payload())
        != manifest.operating_state_policy_sha256
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    expected_compatibility = SimilarityIndexCompatibility(
        dataset_id=manifest.model.dataset_id,
        schema_id=manifest.index.schema_id,
    )
    index_directory = resolve_artifact_reference(
        root,
        manifest.index.artifact.path,
        directory=True,
    )
    verify_artifact_sha256(
        index_directory / "manifest.json",
        manifest.index.artifact.manifest_sha256,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    index = load_similarity_index(
        index_directory,
        expected=expected_compatibility,
        expected_index_id=manifest.index.index_id,
    )
    index_manifest = index.manifest
    if (
        index_manifest.content_sha256 != manifest.index.content_sha256
        or index_manifest.source_model_id != manifest.index.source_model_id
        or index_manifest.source_model_content_sha256
        != manifest.index.source_model_content_sha256
        or index_manifest.record_count != manifest.index.record_count
        or index_manifest.source_model_id != model.model_id
        or index_manifest.source_model_content_sha256 != model.content_sha256
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    mapping_path = resolve_artifact_reference(
        root,
        manifest.mapping.artifact.path,
        directory=False,
    )
    mapping_bytes = read_artifact_file(
        mapping_path,
        maximum_bytes=MAX_JSON_ARTIFACT_BYTES,
    )
    if content_sha256(mapping_bytes) != manifest.mapping.artifact.sha256:
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )
    mapping = validate_fault_knowledge_mapping(mapping_bytes)
    if (
        mapping.mapping_version != manifest.mapping.mapping_version
        or mapping.mapping_sha256 != manifest.mapping.mapping_sha256
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    approved_document_ids = {document.document_id for document in manifest.documents}
    mapped_document_ids = {
        document_id for entry in mapping.mappings for document_id in entry.document_ids
    }
    mapped_fault_classes = {entry.fault_class for entry in mapping.mappings}
    model_fault_codes = {
        label.target_slug: label.fault_code
        for label in model.labels
        if label.operating_state is None
    }
    if (
        not mapped_document_ids.issubset(approved_document_ids)
        or not mapped_fault_classes.issubset(model_fault_codes)
        or any(
            model_fault_codes[fault_class] not in manifest.projection_policy.priorities
            for fault_class in mapped_fault_classes
        )
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    document_runtime = build_artifact_document_runtime(
        settings=settings,
        root=root,
        chunking_binding=manifest.chunking,
        embedding_binding=manifest.embedding,
        document_bindings=manifest.documents,
    )

    retrieval_policy = build_governed_retrieval_policy(
        policy_version=manifest.retrieval_policy.policy_version,
        minimum_score=manifest.retrieval_policy.minimum_score,
    )
    projection_policy = build_prescription_projection_policy(
        policy_version=manifest.projection_policy.policy_version,
        priorities={
            fault_code: PrescriptionPriority(priority)
            for fault_code, priority in manifest.projection_policy.priorities.items()
        },
    )
    if (
        retrieval_policy.policy_sha256 != manifest.retrieval_policy.policy_sha256
        or projection_policy.policy_sha256 != manifest.projection_policy.policy_sha256
        or manifest.prompt_id != PERSISTED_GENERATION_PROMPT_ID
        or manifest.prompt_sha256
        != content_sha256(
            GENERATION_SYSTEM_PROMPT.text.encode("utf-8", errors="strict")
        )
        or manifest.provider.provider_id != "fake-generation.v1"
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    approved = ApprovedKnowledgeRetrievalService(
        mapping=mapping,
        documents=document_runtime.lifecycle,
        chunks=document_runtime.chunks,
        scorer=document_runtime.scorer,
    )
    governed = GovernedKnowledgeRetrievalService(
        approved_retrieval=approved,
        policy=retrieval_policy,
    )
    provider = FakeGenerationProvider()
    orchestration = PrescriptionOrchestrationService(
        retrieval=governed,
        provider=provider,
        snapshot_currentness=governed,
        config=PrescriptionOrchestrationConfig(
            provider_id=manifest.provider.provider_id,
            provider_timeout_seconds=manifest.provider.timeout_seconds,
        ),
    )
    authorization = build_analysis_runtime_authorization(
        authorization_version=manifest.authorization_version,
        dataset_id=model.dataset_id,
        model_id=model.model_id,
        index_id=index.selector.index_id,
        retrieval_policy_version=retrieval_policy.policy_version,
        retrieval_policy_sha256=retrieval_policy.policy_sha256,
        mapping_version=mapping.mapping_version,
        mapping_sha256=mapping.mapping_sha256,
        prompt_id=manifest.prompt_id,
        provider_id=manifest.provider.provider_id,
        provider_timeout_seconds=manifest.provider.timeout_seconds,
        projection_policy=projection_policy,
    )
    if (
        authorization.authorization_sha256 != manifest.authorization_sha256
        or authorization.configuration_id != manifest.configuration_id
    ):
        raise AnalysisRuntimeCompositionError(
            "The configured artifacts runtime is unavailable."
        )

    checked_model = SimilarityCheckedModelPort(
        model=KnnModelPortAdapter(model),
        similarity=InMemorySimilarityIndexAdapter(index),
        selector=index.selector,
        authorization=authorization,
    )
    service = IntegratedAnalysisService(
        model=checked_model,
        orchestration=orchestration,
        authorization=authorization,
        projection_policy=projection_policy,
        unit_of_work_factory=document_runtime.unit_of_work_factory,
    )
    return AnalysisRuntimeComposition(
        mode="artifacts",
        service=service,
        summary=AnalysisRuntimeSummary(
            mode="artifacts",
            model_sample_count=model.sample_count,
            index_record_count=index.manifest.record_count,
            approved_document_count=document_runtime.document_count,
            indexed_chunk_count=document_runtime.chunk_count,
            mapped_fault_count=len(mapping.mappings),
        ),
        unit_of_work_factory=document_runtime.unit_of_work_factory,
        generation_provider=provider,
    )
