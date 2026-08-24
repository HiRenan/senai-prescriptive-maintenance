"""Synthetic dynamic proof of the governed document-to-citation RAG journey."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import sqrt
from typing import cast

import pytest
from prescriptive_maintenance.analysis_integration import (
    PERSISTED_GENERATION_PROMPT_ID,
    IntegratedAnalysisService,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    AnalysisFeatures,
    AnalysisRequest,
    AnalysisResponse,
    Citation,
    Diagnosis,
    DocumentStatus,
    OpaqueNeighbor,
    PrescriptionPriority,
)
from prescriptive_maintenance.data.document_indexing import (
    ChunkingConfiguration,
    DocumentChunk,
    EmbeddingStatus,
    IndexedChunk,
    InMemoryChunkRepository,
    LocalHashEmbeddingProvider,
    chunk_extracted_document,
    index_extracted_document,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    DocumentSnapshot,
    InMemoryDocumentRepository,
    ProcessingStep,
    is_document_snapshot_audited,
)
from prescriptive_maintenance.generation import FakeGenerationProvider
from prescriptive_maintenance.governed_retrieval import (
    GovernedKnowledgeRetrievalService,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import (
    ApprovedKnowledgeRetrievalService,
    build_fault_knowledge_mapping,
)
from prescriptive_maintenance.persistence import (
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    InMemoryStore,
    InMemoryUnitOfWork,
)
from prescriptive_maintenance.ports import ModelDisposition, ModelPrediction
from prescriptive_maintenance.prescription_orchestration import (
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationService,
)

_FAULT_CLASS = "dynamic-bearing-fault"
_FAULT_CODE = "dynamic_bearing_fault"
_MODEL_ID = "model_sen77_dynamic_v1"
_PROVIDER_ID = "fake-generation.v1"
_NOW = datetime(2037, 7, 7, 7, 7, 7, tzinfo=UTC)
_CHUNKING = ChunkingConfiguration(max_characters=96, overlap_characters=12)
_EMBEDDING_DIMENSION = 24


@dataclass(frozen=True, slots=True)
class _ScoringTrace:
    document_id: str
    document_version: str
    chunk_id: str
    content_sha256: str
    score: float


class _EmbeddingProjectionScorer:
    """Compute scores from real stored vectors without a chunk-to-answer lookup."""

    def __init__(self, event: AnalysisFeatures) -> None:
        self._query = _event_query_vector(event, dimension=_EMBEDDING_DIMENSION)
        self.calls: list[_ScoringTrace] = []

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        if fault_class != _FAULT_CLASS:
            return None
        vector = chunk.embedding.vector
        if vector is None:
            return None
        score = _embedding_score(vector, self._query)
        self.calls.append(
            _ScoringTrace(
                document_id=chunk.chunk.document_id,
                document_version=chunk.chunk.document_version,
                chunk_id=chunk.chunk.chunk_id,
                content_sha256=chunk.chunk.content_sha256,
                score=score,
            )
        )
        return score


class _EventModel:
    """Synthetic upstream model adapter; the RAG chain remains production code."""

    def __init__(
        self,
        *,
        expected_features: AnalysisFeatures,
        dataset_id: str,
        index_id: str,
    ) -> None:
        self._expected_features = expected_features
        self._dataset_id = dataset_id
        self._index_id = index_id
        self.calls = 0

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def model_id(self) -> str:
        return _MODEL_ID

    @property
    def index_id(self) -> str:
        return self._index_id

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        if features != self._expected_features or top_k != 1:
            raise ValueError("Synthetic SEN-77 event does not match its test binding.")
        self.calls += 1
        return ModelPrediction(
            disposition=ModelDisposition.FAULT,
            abstention_reason=None,
            diagnosis=Diagnosis(
                code=_FAULT_CODE,
                summary="Falha sintética vinculada à prova dinâmica SEN-77.",
            ),
            support_score=0.9,
            model_id=_MODEL_ID,
            neighbors=(
                OpaqueNeighbor(
                    neighbor_ref="neighbor_sen77_dynamic_001",
                    rank=1,
                    fault_code=_FAULT_CODE,
                    distance=0.0,
                ),
            ),
            retrieval_key=_FAULT_CLASS,
        )


class _LifecycleClock:
    def __init__(self) -> None:
        self._next = _NOW
        self.calls = 0

    def now(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        self.calls += 1
        return value


class _AnalysisIdSequence:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"ana_sen77_dynamic_{self._value}"


class _MonotonicSequence:
    def __init__(self) -> None:
        self._value = 30.0

    def __call__(self) -> float:
        self._value += 0.001
        return self._value


@dataclass(frozen=True, slots=True)
class _CitationLineage:
    document_id: str
    document_version: str
    chunk_id: str
    section_id: str
    page_number: int
    content_sha256: str
    character_start: int
    character_end: int


@dataclass(frozen=True, slots=True)
class _DynamicRagProof:
    event_json: str = field(repr=False)
    extraction: dict[str, object] = field(repr=False)
    chunking_chunks: tuple[DocumentChunk, ...] = field(repr=False)
    indexing_records: tuple[IndexedChunk, ...] = field(repr=False)
    replayed_indexing_records: tuple[IndexedChunk, ...] = field(repr=False)
    repository_records: tuple[IndexedChunk, ...] = field(repr=False)
    all_repository_records: tuple[IndexedChunk, ...] = field(repr=False)
    approved_snapshot: DocumentSnapshot = field(repr=False)
    pending_snapshot: DocumentSnapshot = field(repr=False)
    lifecycle_snapshots: tuple[DocumentSnapshot, ...] = field(repr=False)
    registration_snapshot: DocumentSnapshot = field(repr=False)
    registration_replay: DocumentSnapshot = field(repr=False)
    registration_clock_calls: tuple[int, int]
    index_counts: tuple[int, int]
    scorer_calls: tuple[_ScoringTrace, ...]
    selected_record: IndexedChunk = field(repr=False)
    citation_lineage: _CitationLineage
    responses: tuple[AnalysisResponse, ...] = field(repr=False)
    persisted_references: tuple[tuple[str, str, str], ...]
    provider_calls: int
    model_calls: int
    raw_markers: tuple[str, ...] = field(repr=False)
    public_log: str = field(repr=False)


def _dynamic_event_json() -> str:
    seed = sha256(b"sen-77-dynamic-event-v1").digest()
    values = {
        name: round(0.25 + seed[index] / 31.0, 6)
        for index, name in enumerate(ANALYSIS_FEATURE_NAMES)
    }
    values["temperature_c"] = 47.25
    return json.dumps(
        {"features": values, "top_k": 1},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _synthetic_extraction(
    *,
    event_json: str,
    logical_name: str,
    version: int,
) -> tuple[dict[str, object], str]:
    event_identity = sha256(event_json.encode()).hexdigest()
    marker = f"SEN77_RAW_{event_identity[:12]}_{logical_name}_{version}"
    pages = [
        (
            "# Synthetic inspection\n"
            + " ".join(f"{marker} controlled-step-{index:02d}" for index in range(1, 9))
        ),
        (
            "# Synthetic decision\n"
            + " ".join(f"{marker} bounded-action-{index:02d}" for index in range(9, 17))
        ),
    ]
    source_name = f"SEN77{logical_name.title()}Dynamic.pdf"
    source_material = json.dumps(
        {
            "event_identity": event_identity,
            "logical_name": logical_name,
            "pages": pages,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    source_hash = sha256(source_material).hexdigest()
    payload: dict[str, object] = {
        "schema_version": 1,
        "extractor_version": 2,
        "tooling": {
            "pypdfium2": "synthetic-5.13.0",
            "ocr_adapter": {"configured": False, "name": None, "version": None},
        },
        "source": {
            "name": source_name,
            "source_version": f"sha256:{source_hash}",
            "size_bytes": sum(len(page.encode()) for page in pages),
            "sha256": source_hash,
            "pdf_version": "synthetic-1.0",
        },
        "status": "completed",
        "failure_code": None,
        "page_count": len(pages),
        "pages": [
            {
                "page_number": number,
                "method": "native",
                "status": "extracted",
                "text": text,
                "native_quality": {"signals": []},
                "quality": {"signals": []},
                "ocr_trigger_codes": [],
                "failure_code": None,
            }
            for number, text in enumerate(pages, start=1)
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return cast(dict[str, object], json.loads(serialized)), marker


def _event_query_vector(
    event: AnalysisFeatures,
    *,
    dimension: int,
) -> tuple[float, ...]:
    features = tuple(getattr(event, name) for name in ANALYSIS_FEATURE_NAMES)
    raw = tuple(
        0.5 + abs(features[index % len(features)]) * (index + 1)
        for index in range(dimension)
    )
    magnitude = sqrt(sum(value * value for value in raw))
    return tuple(value / magnitude for value in raw)


def _embedding_score(
    vector: tuple[float, ...],
    query: tuple[float, ...],
) -> float:
    projection = sum(left * right for left, right in zip(vector, query, strict=True))
    return max(0.0, min(1.0, 0.5 + projection / 2.0))


def _source_hash(record: IndexedChunk) -> str:
    return record.chunk.provenance.source_sha256


def _to_pending(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int,
) -> DocumentSnapshot:
    snapshot = service.start_processing(
        identity=snapshot.document.identity,
        version=version,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )
    snapshot = service.record_step_succeeded(
        identity=snapshot.document.identity,
        version=version,
        step=ProcessingStep.EXTRACTION,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )
    return service.record_step_succeeded(
        identity=snapshot.document.identity,
        version=version,
        step=ProcessingStep.INDEXING,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )


def _register_indexed(
    service: DocumentGovernanceService,
    records: tuple[IndexedChunk, ...],
    *,
    version: int,
    expected_revision: int,
) -> DocumentSnapshot:
    record = records[0]
    return service.register(
        identity=record.chunk.document_id,
        version=version,
        sha256=_source_hash(record),
        actor="registrar.synthetic",
        expected_revision=expected_revision,
    )


def _seed_traceable_document(
    store: InMemoryStore,
    *,
    version_records: tuple[tuple[IndexedChunk, ...], ...],
) -> None:
    first = version_records[0][0]
    document = DocumentMetadata(
        document_id=first.chunk.document_id,
        created_at=_NOW,
        versions=tuple(
            DocumentVersionMetadata(
                document_version_id=records[0].chunk.document_version,
                document_id=records[0].chunk.document_id,
                source_sha256=_source_hash(records[0]),
                created_at=_NOW + timedelta(seconds=index),
                chunks=tuple(
                    ChunkReference(
                        chunk_ref=record.chunk.chunk_id,
                        document_id=record.chunk.document_id,
                        document_version_id=record.chunk.document_version,
                        page_number=record.chunk.page_number,
                    )
                    for record in records
                ),
            )
            for index, records in enumerate(version_records)
        ),
    )
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.commit()


def _build_dynamic_proof(caplog: pytest.LogCaptureFixture) -> _DynamicRagProof:
    event_json = _dynamic_event_json()
    request = AnalysisRequest.model_validate_json(event_json)
    chunk_repository = InMemoryChunkRepository()
    embedding = LocalHashEmbeddingProvider(dimension=_EMBEDDING_DIMENSION)

    target_v1_extraction, target_v1_marker = _synthetic_extraction(
        event_json=event_json,
        logical_name="approved",
        version=1,
    )
    target_v1 = index_extracted_document(
        target_v1_extraction,
        embedding_provider=embedding,
        repository=chunk_repository,
        configuration=_CHUNKING,
    )
    extraction, target_v2_marker = _synthetic_extraction(
        event_json=event_json,
        logical_name="approved",
        version=2,
    )
    chunking = chunk_extracted_document(extraction, configuration=_CHUNKING)
    target_v2 = index_extracted_document(
        extraction,
        embedding_provider=embedding,
        repository=chunk_repository,
        configuration=_CHUNKING,
    )
    count_before_replay = len(chunk_repository)
    target_v2_replay = index_extracted_document(
        extraction,
        embedding_provider=embedding,
        repository=chunk_repository,
        configuration=_CHUNKING,
    )
    count_after_replay = len(chunk_repository)

    indexed_by_state: dict[DocumentStatus, tuple[IndexedChunk, ...]] = {}
    raw_markers = [target_v1_marker, target_v2_marker]
    for status in (
        DocumentStatus.RECEIVED,
        DocumentStatus.PROCESSING,
        DocumentStatus.PENDING_APPROVAL,
        DocumentStatus.REJECTED,
        DocumentStatus.FAILED,
    ):
        state_extraction, marker = _synthetic_extraction(
            event_json=event_json,
            logical_name=status.value,
            version=1,
        )
        indexed = index_extracted_document(
            state_extraction,
            embedding_provider=embedding,
            repository=chunk_repository,
            configuration=_CHUNKING,
        )
        indexed_by_state[status] = indexed.records
        raw_markers.append(marker)

    lifecycle_repository = InMemoryDocumentRepository()
    lifecycle_clock = _LifecycleClock()
    lifecycle = DocumentGovernanceService(
        repository=lifecycle_repository,
        clock=lifecycle_clock,
    )

    registration = _register_indexed(
        lifecycle,
        target_v1.records,
        version=1,
        expected_revision=0,
    )
    calls_before_registration_replay = lifecycle_clock.calls
    registration_replay = _register_indexed(
        lifecycle,
        target_v1.records,
        version=1,
        expected_revision=0,
    )
    calls_after_registration_replay = lifecycle_clock.calls
    target_v1_approved = lifecycle.approve(
        identity=registration.document.identity,
        version=1,
        actor="approver.synthetic",
        reason="Synthetic extraction and indexing gates completed.",
        expected_revision=_to_pending(
            lifecycle,
            registration,
            version=1,
        ).revision,
    )
    target_v2_registered = _register_indexed(
        lifecycle,
        target_v2.records,
        version=2,
        expected_revision=target_v1_approved.revision,
    )
    pending_v2 = _to_pending(lifecycle, target_v2_registered, version=2)
    approved_snapshot = lifecycle.approve(
        identity=target_v2_registered.document.identity,
        version=2,
        actor="approver.synthetic",
        reason="Synthetic replacement version passed both processing gates.",
        expected_revision=pending_v2.revision,
    )

    state_snapshots: list[DocumentSnapshot] = []
    for status, records in indexed_by_state.items():
        snapshot = _register_indexed(
            lifecycle,
            records,
            version=1,
            expected_revision=0,
        )
        if status is DocumentStatus.PROCESSING:
            snapshot = lifecycle.start_processing(
                identity=snapshot.document.identity,
                version=1,
                actor="processor.synthetic",
                expected_revision=snapshot.revision,
            )
        elif status in {
            DocumentStatus.PENDING_APPROVAL,
            DocumentStatus.REJECTED,
        }:
            snapshot = _to_pending(lifecycle, snapshot, version=1)
            if status is DocumentStatus.REJECTED:
                snapshot = lifecycle.reject(
                    identity=snapshot.document.identity,
                    version=1,
                    actor="approver.synthetic",
                    reason="Synthetic evidence intentionally rejected.",
                    expected_revision=snapshot.revision,
                )
        elif status is DocumentStatus.FAILED:
            snapshot = lifecycle.start_processing(
                identity=snapshot.document.identity,
                version=1,
                actor="processor.synthetic",
                expected_revision=snapshot.revision,
            )
            snapshot = lifecycle.record_step_failed(
                identity=snapshot.document.identity,
                version=1,
                step=ProcessingStep.EXTRACTION,
                code="extraction.synthetic_control_failure",
                reason="Synthetic negative lifecycle control.",
                actor="processor.synthetic",
                expected_revision=snapshot.revision,
            )
        state_snapshots.append(snapshot)

    mapping = build_fault_knowledge_mapping(
        mapping_version="sen77-dynamic-mapping.v1",
        mappings={
            _FAULT_CLASS: tuple(
                snapshot.document.identity for snapshot in lifecycle_repository.list()
            )
        },
    )
    scorer = _EmbeddingProjectionScorer(request.features)
    approved_retrieval = ApprovedKnowledgeRetrievalService(
        mapping=mapping,
        documents=lifecycle_repository,
        chunks=chunk_repository,
        scorer=scorer,
    )
    policy = build_governed_retrieval_policy(
        policy_version="sen77-dynamic-retrieval.v1",
        minimum_score=0.0,
    )
    governed_retrieval = GovernedKnowledgeRetrievalService(
        approved_retrieval=approved_retrieval,
        policy=policy,
    )
    projection_policy = build_prescription_projection_policy(
        policy_version="sen77-dynamic-priority.v1",
        priorities={_FAULT_CODE: PrescriptionPriority.SCHEDULED},
    )
    event_digest = sha256(event_json.encode()).hexdigest()
    dataset_id = sha256(f"dataset:{event_digest}".encode()).hexdigest()
    index_id = f"similarity_index_v1_{event_digest[:32]}"
    authorization = build_analysis_runtime_authorization(
        authorization_version="sen77-dynamic-analysis.v1",
        dataset_id=dataset_id,
        model_id=_MODEL_ID,
        index_id=index_id,
        retrieval_policy_version=policy.policy_version,
        retrieval_policy_sha256=policy.policy_sha256,
        mapping_version=mapping.mapping_version,
        mapping_sha256=mapping.mapping_sha256,
        prompt_id=PERSISTED_GENERATION_PROMPT_ID,
        provider_id=_PROVIDER_ID,
        provider_timeout_seconds=1.0,
        projection_policy=projection_policy,
    )
    model = _EventModel(
        expected_features=request.features,
        dataset_id=dataset_id,
        index_id=index_id,
    )
    provider = FakeGenerationProvider()
    orchestration = PrescriptionOrchestrationService(
        retrieval=governed_retrieval,
        provider=provider,
        snapshot_currentness=governed_retrieval,
        config=PrescriptionOrchestrationConfig(
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=1.0,
        ),
        monotonic_clock=_MonotonicSequence(),
    )
    store = InMemoryStore()
    _seed_traceable_document(
        store,
        version_records=(target_v1.records, target_v2.records),
    )
    service = IntegratedAnalysisService(
        model=model,
        orchestration=orchestration,
        authorization=authorization,
        projection_policy=projection_policy,
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: _NOW,
        analysis_id_factory=_AnalysisIdSequence(),
    )

    with caplog.at_level(logging.INFO, logger="prescriptive_maintenance.analysis"):
        responses = (service.analyze(request), service.analyze(request))

    query = _event_query_vector(request.features, dimension=_EMBEDDING_DIMENSION)
    selected = min(
        target_v2.records,
        key=lambda record: (
            -_embedding_score(cast(tuple[float, ...], record.embedding.vector), query),
            record.chunk.document_id,
            record.chunk.document_version,
            record.chunk.page_number,
            record.chunk.section_id,
            record.chunk.chunk_id,
        ),
    )
    persisted: list[tuple[str, str, str]] = []
    for response in responses:
        with InMemoryUnitOfWork(store) as transaction:
            metadata = transaction.analyses.get(response.root.analysis_id)
        if metadata is None:
            raise AssertionError("Dynamic analysis metadata was not persisted.")
        persisted.extend(
            (
                item.document_id,
                item.document_version_id,
                item.chunk_ref,
            )
            for item in metadata.evidence_references
        )

    all_records = tuple(
        record
        for snapshot in lifecycle_repository.list()
        for version in snapshot.document.versions
        for record in chunk_repository.list_by_document(
            snapshot.document.identity,
            document_version=f"docver_{version.sha256}",
        )
    )
    return _DynamicRagProof(
        event_json=event_json,
        extraction=extraction,
        chunking_chunks=chunking.chunks,
        indexing_records=target_v2.records,
        replayed_indexing_records=target_v2_replay.records,
        repository_records=chunk_repository.list_by_document(
            target_v2.document_id,
            document_version=target_v2.document_version,
        ),
        all_repository_records=all_records,
        approved_snapshot=approved_snapshot,
        pending_snapshot=pending_v2,
        lifecycle_snapshots=lifecycle_repository.list(),
        registration_snapshot=registration,
        registration_replay=registration_replay,
        registration_clock_calls=(
            calls_before_registration_replay,
            calls_after_registration_replay,
        ),
        index_counts=(count_before_replay, count_after_replay),
        scorer_calls=tuple(scorer.calls),
        selected_record=selected,
        citation_lineage=_CitationLineage(
            document_id=selected.chunk.document_id,
            document_version=selected.chunk.document_version,
            chunk_id=selected.chunk.chunk_id,
            section_id=selected.chunk.section_id,
            page_number=selected.chunk.page_number,
            content_sha256=selected.chunk.content_sha256,
            character_start=selected.chunk.character_start,
            character_end=selected.chunk.character_end,
        ),
        responses=responses,
        persisted_references=tuple(persisted),
        provider_calls=provider.call_count,
        model_calls=model.calls,
        raw_markers=tuple(raw_markers),
        public_log=caplog.text,
    )


def _response_without_analysis_id(response: AnalysisResponse) -> dict[str, object]:
    payload = cast(dict[str, object], response.model_dump(mode="json"))
    return {key: value for key, value in payload.items() if key != "analysis_id"}


def _record_identity(record: IndexedChunk) -> tuple[str, str, str]:
    return (
        record.chunk.document_id,
        record.chunk.document_version,
        record.chunk.chunk_id,
    )


def _assert_dynamic_lineage(proof: _DynamicRagProof) -> None:
    request = AnalysisRequest.model_validate_json(proof.event_json)
    extraction_round_trip = cast(
        dict[str, object],
        json.loads(json.dumps(proof.extraction, sort_keys=True)),
    )
    rebuilt = chunk_extracted_document(
        extraction_round_trip,
        configuration=_CHUNKING,
    )
    assert rebuilt.chunks == proof.chunking_chunks, "chunker lineage was bypassed"

    assert tuple(record.chunk for record in proof.indexing_records) == (
        proof.chunking_chunks
    ), "indexer lineage was bypassed"
    expected_embeddings = LocalHashEmbeddingProvider(
        dimension=_EMBEDDING_DIMENSION
    ).embed(proof.chunking_chunks)
    assert tuple(record.embedding for record in proof.indexing_records) == (
        expected_embeddings
    ), "indexer lineage was bypassed"
    assert proof.indexing_records == proof.replayed_indexing_records
    assert proof.indexing_records == proof.repository_records
    assert proof.index_counts[0] == proof.index_counts[1]
    assert all(
        record.embedding.status is EmbeddingStatus.EMBEDDED
        for record in proof.indexing_records
    )

    assert is_document_snapshot_audited(proof.approved_snapshot), (
        "lifecycle approval was bypassed"
    )
    assert proof.approved_snapshot.document.current_version == 2, (
        "lifecycle approval was bypassed"
    )
    versions = proof.approved_snapshot.document.versions
    assert tuple(version.status for version in versions) == (
        DocumentStatus.SUPERSEDED,
        DocumentStatus.APPROVED,
    ), "lifecycle approval was bypassed"
    assert versions[1].sha256 == _source_hash(proof.indexing_records[0]), (
        "lifecycle approval was bypassed"
    )
    assert proof.registration_snapshot == proof.registration_replay
    assert proof.registration_clock_calls[0] == proof.registration_clock_calls[1]
    lifecycle_statuses = {
        version.status
        for snapshot in proof.lifecycle_snapshots
        for version in snapshot.document.versions
    }
    assert lifecycle_statuses == set(DocumentStatus)

    query = _event_query_vector(request.features, dimension=_EMBEDDING_DIMENSION)
    expected = min(
        proof.repository_records,
        key=lambda record: (
            -_embedding_score(cast(tuple[float, ...], record.embedding.vector), query),
            record.chunk.document_id,
            record.chunk.document_version,
            record.chunk.page_number,
            record.chunk.section_id,
            record.chunk.chunk_id,
        ),
    )
    assert expected == proof.selected_record, "approval filter was bypassed"
    eligible = tuple(
        sorted(_record_identity(record) for record in proof.indexing_records)
    )
    calls = tuple(
        (call.document_id, call.document_version, call.chunk_id)
        for call in proof.scorer_calls
    )
    assert calls == (*eligible, *eligible), "approval filter was bypassed"
    forbidden = {
        _record_identity(record)
        for record in proof.all_repository_records
        if _record_identity(record) not in set(eligible)
    }
    assert forbidden.isdisjoint(calls), "approval filter was bypassed"

    selected = proof.selected_record.chunk
    lineage = proof.citation_lineage
    assert lineage == _CitationLineage(
        document_id=selected.document_id,
        document_version=selected.document_version,
        chunk_id=selected.chunk_id,
        section_id=selected.section_id,
        page_number=selected.page_number,
        content_sha256=sha256(selected.content.encode()).hexdigest(),
        character_start=selected.character_start,
        character_end=selected.character_end,
    )
    page = cast(list[dict[str, object]], proof.extraction["pages"])[
        selected.page_number - 1
    ]
    page_text = cast(str, page["text"])
    assert page_text[selected.character_start : selected.character_end] == (
        selected.content
    )

    expected_citation = Citation(
        document_id=lineage.document_id,
        document_version=lineage.document_version,
        chunk=lineage.chunk_id,
        page_number=lineage.page_number,
    )
    assert tuple(response.root.citations for response in proof.responses) == (
        (expected_citation,),
        (expected_citation,),
    ), "approval filter was bypassed"
    expected_reference = (
        lineage.document_id,
        lineage.document_version,
        lineage.chunk_id,
    )
    assert proof.persisted_references == (expected_reference, expected_reference)
    assert _response_without_analysis_id(proof.responses[0]) == (
        _response_without_analysis_id(proof.responses[1])
    )
    assert proof.provider_calls == 2
    assert proof.model_calls == 2

    public_surface = "\n".join(
        (
            *(response.model_dump_json() for response in proof.responses),
            proof.public_log,
            repr(proof),
        )
    )
    for marker in proof.raw_markers:
        assert marker not in public_surface


def _prefabricated_record(base: IndexedChunk) -> IndexedChunk:
    content = "SEN77_PREFABRICATED_READY_ANSWER"
    chunk = replace(
        base.chunk,
        chunk_id="chunk_prefabricated_control",
        document_id="doc_prefabricated_control",
        document_version=f"docver_{'f' * 64}",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        page_number=99,
        section_id="section_prefabricated_control",
        character_start=0,
        character_end=len(content),
    )
    embedding = LocalHashEmbeddingProvider(dimension=_EMBEDDING_DIMENSION).embed(
        (chunk,)
    )[0]
    return IndexedChunk(chunk=chunk, embedding=embedding)


def _responses_with_prefabricated_citation(
    proof: _DynamicRagProof,
    record: IndexedChunk,
) -> tuple[AnalysisResponse, ...]:
    citation = Citation(
        document_id=record.chunk.document_id,
        document_version=record.chunk.document_version,
        chunk=record.chunk.chunk_id,
        page_number=record.chunk.page_number,
    )
    return tuple(
        AnalysisResponse(
            root=response.root.model_copy(update={"citations": (citation,)})
        )
        for response in proof.responses
    )


def test_new_extraction_reaches_exact_approved_chunk_citation_deterministically(
    caplog: pytest.LogCaptureFixture,
) -> None:
    proof = _build_dynamic_proof(caplog)

    _assert_dynamic_lineage(proof)


def test_prefabricated_shortcuts_fail_the_dynamic_lineage_control(
    caplog: pytest.LogCaptureFixture,
) -> None:
    proof = _build_dynamic_proof(caplog)
    prefabricated = _prefabricated_record(proof.selected_record)
    prefabricated_responses = _responses_with_prefabricated_citation(
        proof,
        prefabricated,
    )
    mutations = (
        (
            "chunker lineage was bypassed",
            replace(
                proof,
                chunking_chunks=(prefabricated.chunk,),
                indexing_records=(prefabricated,),
                replayed_indexing_records=(prefabricated,),
                repository_records=(prefabricated,),
                selected_record=prefabricated,
                responses=prefabricated_responses,
            ),
        ),
        (
            "indexer lineage was bypassed",
            replace(
                proof,
                indexing_records=(prefabricated,),
                replayed_indexing_records=(prefabricated,),
                repository_records=(prefabricated,),
                selected_record=prefabricated,
                responses=prefabricated_responses,
            ),
        ),
        (
            "lifecycle approval was bypassed",
            replace(proof, approved_snapshot=proof.pending_snapshot),
        ),
        (
            "approval filter was bypassed",
            replace(
                proof,
                selected_record=prefabricated,
                responses=prefabricated_responses,
            ),
        ),
    )

    for message, mutation in mutations:
        with pytest.raises(AssertionError, match=message):
            _assert_dynamic_lineage(mutation)
