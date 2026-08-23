"""Entirely synthetic tests for approved documentary knowledge retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Never, cast

import pytest
from prescriptive_maintenance.contracts import DocumentStatus
from prescriptive_maintenance.data.document_indexing import (
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingStatus,
    ExtractionProvenance,
    IndexedChunk,
    InMemoryChunkRepository,
    document_chunk_id,
    document_section_id,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    DocumentRepository,
    DocumentSnapshot,
    InMemoryDocumentRepository,
    ProcessingStep,
)
from prescriptive_maintenance.generation import (
    GENERATION_CONTRACT_VERSION,
    Diagnosis,
    ProviderRequest,
    ProviderResponse,
    RagGuardrailService,
    RagGuardrailStatus,
    RagRefusalCode,
)
from prescriptive_maintenance.generation.contracts import (
    MAX_EVIDENCE_CONTENT_CHARACTERS,
    MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedKnowledgeRetrievalService,
    GovernedRetrievalPolicy,
    GovernedRetrievalStatus,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import (
    ApprovedKnowledgeRetrievalService,
    FaultKnowledgeMappingError,
    FaultKnowledgeReferenceError,
    IndexedChunkReader,
    KnowledgeChunkScorer,
    KnowledgeRetrievalInputError,
    KnowledgeRetrievalReason,
    KnowledgeRetrievalResult,
    KnowledgeSnapshotRetrievalResult,
    RankedKnowledgeEvidence,
    RankedKnowledgeSnapshot,
    build_fault_knowledge_mapping,
    fault_knowledge_mapping_json_bytes,
    load_fault_knowledge_mapping,
    validate_fault_knowledge_mapping,
)
from prescriptive_maintenance.ports import ModelDisposition

_FAULT_CLASS = "synthetic-bearing-warning"
_EMPTY_CLASS = "synthetic-without-coverage"
_UNMAPPED_CLASS = "synthetic-unmapped"
_DOC_APPROVED = f"doc_{'a' * 64}"
_DOC_REJECTED = f"doc_{'b' * 64}"
_DOC_FAILED = f"doc_{'c' * 64}"
_DOC_PENDING = f"doc_{'d' * 64}"
_DOC_OUTSIDE_CLASS = f"doc_{'e' * 64}"
_UNKNOWN_DOC = f"doc_{'f' * 64}"
_HASH_V1 = "1" * 64
_HASH_V2 = "2" * 64
_MAPPING_VERSION = "synthetic-fault-knowledge.v1"
_POLICY_VERSION = "synthetic-governed-retrieval.v1"
_START = datetime(2038, 2, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self._next = _START

    def now(self) -> datetime:
        current = self._next
        self._next += timedelta(seconds=1)
        return current


@dataclass(slots=True)
class _RecordingScorer:
    scores: dict[str, float | None]
    calls: list[tuple[str, str, str]] = field(
        default_factory=lambda: list[tuple[str, str, str]]()
    )

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        self.calls.append((fault_class, chunk.chunk.chunk_id, chunk.chunk.content))
        return self.scores.get(chunk.chunk.chunk_id)


class MatchEveryFault(str):
    equality_calls: ClassVar[int] = 0

    def __eq__(self, _other: object) -> bool:
        type(self).equality_calls += 1
        return True

    def __hash__(self) -> int:
        return str.__hash__(self)


class LyingHash(str):
    inequality_calls: ClassVar[int] = 0

    def __ne__(self, _other: object) -> bool:
        type(self).inequality_calls += 1
        return False


class BadHashStr(str):
    hash_calls: ClassVar[int] = 0

    def __hash__(self) -> int:
        type(self).hash_calls += 1
        return id(self)


class _HostileItems(dict[str, tuple[str, ...]]):
    items_calls: ClassVar[int] = 0

    def items(self) -> Never:
        type(self).items_calls += 1
        raise RuntimeError("hostile items")


@dataclass(slots=True)
class _StaticChunkReader:
    records: object
    failure: Exception | None = None
    calls: int = 0

    def list_by_document(
        self,
        document_id: str,
        *,
        document_version: str | None = None,
    ) -> tuple[IndexedChunk, ...]:
        del document_id, document_version
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return cast(tuple[IndexedChunk, ...], self.records)


@dataclass(slots=True)
class _SwappingChunkReader:
    first: tuple[IndexedChunk, ...]
    replacement: tuple[IndexedChunk, ...]
    calls: int = 0

    def list_by_document(
        self,
        document_id: str,
        *,
        document_version: str | None = None,
    ) -> tuple[IndexedChunk, ...]:
        del document_id, document_version
        self.calls += 1
        return self.first if self.calls == 1 else self.replacement


@dataclass(slots=True)
class _StaticScorer:
    value: object = None
    failure: Exception | None = None
    calls: int = 0

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        del fault_class, chunk
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return cast(float | None, self.value)


@dataclass(slots=True)
class _StaticApprovedRetriever:
    result: object
    failure: Exception | None = None
    calls: list[tuple[str, int]] = field(
        default_factory=lambda: list[tuple[str, int]]()
    )

    def retrieve_snapshots(
        self,
        fault_class: str,
        *,
        top_k: int,
    ) -> KnowledgeSnapshotRetrievalResult:
        self.calls.append((fault_class, top_k))
        if self.failure is not None:
            raise self.failure
        return cast(KnowledgeSnapshotRetrievalResult, self.result)


@dataclass(slots=True)
class _MutatingScorer:
    calls: int = 0

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        del fault_class
        self.calls += 1
        object.__setattr__(chunk.chunk, "document_id", _DOC_REJECTED)
        return 0.75


@dataclass(slots=True)
class _ApprovingScorer:
    lifecycle: DocumentGovernanceService
    pending: DocumentSnapshot
    calls: int = 0

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        del fault_class, chunk
        self.calls += 1
        self.pending = self.lifecycle.approve(
            identity=self.pending.document.identity,
            version=2,
            actor="synthetic-racing-approver",
            reason="Synthetic concurrent approval during scoring.",
            expected_revision=self.pending.revision,
        )
        return 0.75


def _governance() -> tuple[DocumentGovernanceService, InMemoryDocumentRepository]:
    repository = InMemoryDocumentRepository()
    return (
        DocumentGovernanceService(repository=repository, clock=_Clock()),
        repository,
    )


def _register(
    service: DocumentGovernanceService,
    *,
    identity: str,
    content_hash: str = _HASH_V1,
    version: int = 1,
    expected_revision: int = 0,
) -> DocumentSnapshot:
    return service.register(
        identity=identity,
        version=version,
        sha256=content_hash,
        actor="synthetic-registrar",
        expected_revision=expected_revision,
    )


def _pending(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int = 1,
) -> DocumentSnapshot:
    processing = service.start_processing(
        identity=snapshot.document.identity,
        version=version,
        actor="synthetic-processor",
        expected_revision=snapshot.revision,
    )
    extracted = service.record_step_succeeded(
        identity=snapshot.document.identity,
        version=version,
        step=ProcessingStep.EXTRACTION,
        actor="synthetic-processor",
        expected_revision=processing.revision,
    )
    return service.record_step_succeeded(
        identity=snapshot.document.identity,
        version=version,
        step=ProcessingStep.INDEXING,
        actor="synthetic-processor",
        expected_revision=extracted.revision,
    )


def _approve(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int = 1,
) -> DocumentSnapshot:
    return service.approve(
        identity=snapshot.document.identity,
        version=version,
        actor="synthetic-approver",
        reason="Synthetic processing gates were reviewed.",
        expected_revision=snapshot.revision,
    )


def _approved_document(
    service: DocumentGovernanceService,
    *,
    identity: str,
    content_hash: str = _HASH_V1,
) -> DocumentSnapshot:
    return _approve(
        service,
        _pending(
            service,
            _register(service, identity=identity, content_hash=content_hash),
        ),
    )


def _record(
    *,
    document_id: str,
    source_hash: str,
    chunk_key: str,
    content: str,
    page_number: int = 1,
    document_extraction_status: str = "completed",
    document_failure_code: str | None = None,
    page_method: str = "native",
    page_status: str = "extracted",
    page_failure_code: str | None = None,
    embedding_status: EmbeddingStatus = EmbeddingStatus.EMBEDDED,
    provider_id: str = "synthetic-embedding",
) -> IndexedChunk:
    document_version = f"docver_{source_hash}"
    content_sha256 = sha256(content.encode("utf-8")).hexdigest()
    section_index = 1
    section_title = f"SYNTHETIC SECTION {chunk_key.upper()}"
    ordinal = page_number
    section_chunk_index = 1
    character_start = 0
    character_end = len(content)
    chunking_configuration_id = f"chunkcfg_{'9' * 64}"
    section_id = document_section_id(
        document_id=document_id,
        document_version=document_version,
        page_number=page_number,
        section_index=section_index,
        section_title=section_title,
    )
    chunk_id = document_chunk_id(
        content_sha256=content_sha256,
        document_id=document_id,
        document_version=document_version,
        page_number=page_number,
        section_id=section_id,
        section_index=section_index,
        ordinal=ordinal,
        section_chunk_index=section_chunk_index,
        character_start=character_start,
        character_end=character_end,
        chunking_configuration_id=chunking_configuration_id,
    )
    chunk = DocumentChunk(
        schema_version=1,
        chunk_id=chunk_id,
        document_id=document_id,
        document_version=document_version,
        content=content,
        content_sha256=content_sha256,
        page_number=page_number,
        section_id=section_id,
        section_index=section_index,
        section_title=section_title,
        ordinal=ordinal,
        section_chunk_index=section_chunk_index,
        character_start=character_start,
        character_end=character_end,
        chunking_configuration_id=chunking_configuration_id,
        provenance=ExtractionProvenance(
            source_name="SyntheticManual.pdf",
            source_sha256=source_hash,
            source_version=f"sha256:{source_hash}",
            source_size_bytes=2_048,
            pdf_version="1.7",
            extraction_schema_version=1,
            extractor_version=2,
            document_extraction_status=document_extraction_status,
            document_failure_code=document_failure_code,
            page_number=page_number,
            page_extraction_method=page_method,
            page_extraction_status=page_status,
            page_failure_code=page_failure_code,
            ocr_trigger_codes=(),
            quality_signals=(),
            pdfium_version="5.13.0",
            ocr_adapter_name=None,
            ocr_adapter_version=None,
        ),
    )
    if embedding_status is EmbeddingStatus.EMBEDDED:
        embedding = ChunkEmbedding(
            chunk_id=chunk_id,
            provider_id=provider_id,
            representation_version="synthetic-embedding.v1",
            dimension=2,
            status=EmbeddingStatus.EMBEDDED,
            vector=(0.6, 0.8),
            failure_code=None,
        )
    else:
        embedding = ChunkEmbedding(
            chunk_id=chunk_id,
            provider_id=provider_id,
            representation_version="synthetic-embedding.v1",
            dimension=2,
            status=EmbeddingStatus.FAILED,
            vector=None,
            failure_code="embedding.synthetic_failure",
        )
    return IndexedChunk(chunk=chunk, embedding=embedding)


def _retrieval(
    *,
    documents: DocumentRepository,
    chunks: IndexedChunkReader,
    scorer: KnowledgeChunkScorer,
    mappings: dict[str, tuple[str, ...]],
) -> ApprovedKnowledgeRetrievalService:
    mapping = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings=mappings,
    )
    return ApprovedKnowledgeRetrievalService(
        mapping=mapping,
        documents=documents,
        chunks=chunks,
        scorer=scorer,
    )


def _approved_retrieval(
    *,
    records: object,
    scorer: KnowledgeChunkScorer,
) -> tuple[ApprovedKnowledgeRetrievalService, InMemoryDocumentRepository]:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    return (
        _retrieval(
            documents=documents,
            chunks=_StaticChunkReader(records=records),
            scorer=scorer,
            mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
        ),
        documents,
    )


def _ranked_evidence(
    *,
    key: str,
    score: float,
    page_number: int,
    document_id: str = _DOC_APPROVED,
    source_hash: str = _HASH_V1,
) -> RankedKnowledgeSnapshot:
    content = f"Synthetic governed evidence {key}."
    return RankedKnowledgeSnapshot(
        document_id=document_id,
        document_version=f"docver_{source_hash}",
        chunk_id=f"chunk_{key * 64}",
        page_number=page_number,
        section_id=f"section_{key * 64}",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        score=score,
    )


def _knowledge_result(
    *,
    fault_class: str = _FAULT_CLASS,
    evidence: tuple[RankedKnowledgeSnapshot, ...] = (),
    reason: KnowledgeRetrievalReason | None = None,
) -> KnowledgeSnapshotRetrievalResult:
    return KnowledgeSnapshotRetrievalResult(
        fault_class=fault_class,
        mapping_version=_MAPPING_VERSION,
        mapping_sha256="3" * 64,
        evidence=evidence,
        reason=reason,
    )


def _supported_provider_response(request: ProviderRequest) -> ProviderResponse:
    citation = {"evidence_id": request.allowed_evidence_ids[0]}
    return ProviderResponse(
        output_text=json.dumps(
            {
                "schema_version": GENERATION_CONTRACT_VERSION,
                "diagnostic_support": {
                    "fault_code": request.diagnosis_fault_code,
                    "status": "supported",
                    "assessment": "Synthetic evidence supports the diagnosis.",
                    "citations": [citation],
                },
                "prescriptions": [
                    {
                        "action": "Inspect the synthetic asset.",
                        "rationale": "The cited synthetic source warrants review.",
                        "citations": [citation],
                    }
                ],
                "warnings": [],
            }
        )
    )


def _governed_retrieval(
    approved_retrieval: object,
    *,
    minimum_score: float = 0.75,
) -> GovernedKnowledgeRetrievalService:
    return GovernedKnowledgeRetrievalService(
        approved_retrieval=cast(_StaticApprovedRetriever, approved_retrieval),
        policy=build_governed_retrieval_policy(
            policy_version=_POLICY_VERSION,
            minimum_score=minimum_score,
        ),
    )


def test_mapping_hash_and_bytes_are_deterministic_and_load_from_explicit_path(
    tmp_path: Path,
) -> None:
    first = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings={
            "synthetic-second-class": (_DOC_REJECTED,),
            _FAULT_CLASS: (_DOC_REJECTED, _DOC_APPROVED),
        },
    )
    second = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings={
            _FAULT_CLASS: (_DOC_APPROVED, _DOC_REJECTED),
            "synthetic-second-class": (_DOC_REJECTED,),
        },
    )

    assert first == second
    assert len(first.mapping_sha256) == 64
    canonical = fault_knowledge_mapping_json_bytes(first)
    assert canonical == fault_knowledge_mapping_json_bytes(second)
    assert canonical.endswith(b"\n")
    assert b"SyntheticManual" not in canonical

    path = tmp_path / "fault-knowledge.synthetic.v1.json"
    path.write_bytes(canonical)
    assert load_fault_knowledge_mapping(path) == first


def test_changed_invalid_and_duplicate_mappings_fail_closed() -> None:
    valid = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    changed = fault_knowledge_mapping_json_bytes(valid).replace(
        _DOC_APPROVED.encode(),
        _DOC_REJECTED.encode(),
    )
    with pytest.raises(FaultKnowledgeMappingError, match="hash"):
        validate_fault_knowledge_mapping(changed)

    duplicate_documents = {
        "schema_version": 1,
        "mapping_version": _MAPPING_VERSION,
        "mapping_sha256": valid.mapping_sha256,
        "mappings": [
            {
                "fault_class": _FAULT_CLASS,
                "document_ids": [_DOC_APPROVED, _DOC_APPROVED],
            }
        ],
    }
    with pytest.raises(FaultKnowledgeMappingError, match="unique"):
        validate_fault_knowledge_mapping(
            json.dumps(duplicate_documents).encode("utf-8")
        )

    duplicate_classes = {
        "schema_version": 1,
        "mapping_version": _MAPPING_VERSION,
        "mapping_sha256": valid.mapping_sha256,
        "mappings": [
            {"fault_class": _FAULT_CLASS, "document_ids": [_DOC_APPROVED]},
            {"fault_class": _FAULT_CLASS, "document_ids": [_DOC_REJECTED]},
        ],
    }
    with pytest.raises(FaultKnowledgeMappingError, match="classes"):
        validate_fault_knowledge_mapping(json.dumps(duplicate_classes).encode())


def test_unknown_document_reference_rejects_service_construction() -> None:
    _service, documents = _governance()
    mapping = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings={_FAULT_CLASS: (_UNKNOWN_DOC,)},
    )

    with pytest.raises(FaultKnowledgeReferenceError, match="unknown"):
        ApprovedKnowledgeRetrievalService(
            mapping=mapping,
            documents=documents,
            chunks=InMemoryChunkRepository(),
            scorer=_RecordingScorer(scores={}),
        )


def test_unmapped_and_uncovered_classes_return_distinct_typed_reasons() -> None:
    lifecycle, documents = _governance()
    _register(lifecycle, identity=_DOC_PENDING)
    scorer = _RecordingScorer(scores={})
    service = _retrieval(
        documents=documents,
        chunks=InMemoryChunkRepository(),
        scorer=scorer,
        mappings={
            _EMPTY_CLASS: (),
            _FAULT_CLASS: (_DOC_PENDING,),
        },
    )

    unmapped = service.retrieve(_UNMAPPED_CLASS, top_k=3)
    explicit_empty = service.retrieve(_EMPTY_CLASS, top_k=3)
    not_approved = service.retrieve(_FAULT_CLASS, top_k=3)

    assert unmapped.evidence == ()
    assert unmapped.reason is KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED
    assert explicit_empty.evidence == ()
    assert explicit_empty.reason is KnowledgeRetrievalReason.NO_APPROVED_COVERAGE
    assert not_approved.evidence == ()
    assert not_approved.reason is KnowledgeRetrievalReason.NO_APPROVED_COVERAGE
    assert scorer.calls == []


def test_lifecycle_and_chunk_integrity_filter_before_scoring() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)

    rejected_pending = _pending(
        lifecycle,
        _register(lifecycle, identity=_DOC_REJECTED),
    )
    lifecycle.reject(
        identity=_DOC_REJECTED,
        version=1,
        actor="synthetic-reviewer",
        reason="Synthetic rejection.",
        expected_revision=rejected_pending.revision,
    )

    failed_processing = lifecycle.start_processing(
        identity=_DOC_FAILED,
        version=1,
        actor="synthetic-processor",
        expected_revision=_register(lifecycle, identity=_DOC_FAILED).revision,
    )
    lifecycle.record_step_failed(
        identity=_DOC_FAILED,
        version=1,
        step=ProcessingStep.EXTRACTION,
        code="extraction.synthetic_failure",
        reason="Synthetic extraction failure.",
        actor="synthetic-processor",
        expected_revision=failed_processing.revision,
    )

    _pending(lifecycle, _register(lifecycle, identity=_DOC_PENDING))
    _approved_document(lifecycle, identity=_DOC_OUTSIDE_CLASS)

    records = (
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="approved_valid",
            content="Synthetic approved content.",
        ),
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="approved_failed_page",
            content="Synthetic failed-page content.",
            page_number=2,
            page_method="none",
            page_status="failed",
            page_failure_code="page.synthetic_failure",
        ),
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="approved_failed_embedding",
            content="Synthetic failed-embedding content.",
            page_number=3,
            embedding_status=EmbeddingStatus.FAILED,
        ),
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="approved_partial_extraction",
            content="Synthetic partial-extraction content.",
            page_number=4,
            document_extraction_status="partial",
            document_failure_code="document.synthetic_partial",
        ),
        _record(
            document_id=_DOC_REJECTED,
            source_hash=_HASH_V1,
            chunk_key="rejected_valid",
            content="Synthetic rejected content.",
        ),
        _record(
            document_id=_DOC_FAILED,
            source_hash=_HASH_V1,
            chunk_key="failed_valid",
            content="Synthetic failed lifecycle content.",
        ),
        _record(
            document_id=_DOC_PENDING,
            source_hash=_HASH_V1,
            chunk_key="pending_valid",
            content="Synthetic pending content.",
        ),
        _record(
            document_id=_DOC_OUTSIDE_CLASS,
            source_hash=_HASH_V1,
            chunk_key="outside_valid",
            content="Synthetic outside-class content.",
        ),
    )
    chunks = InMemoryChunkRepository()
    chunks.save(records)
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.9 for record in records})
    service = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={
            _FAULT_CLASS: (
                _DOC_APPROVED,
                _DOC_REJECTED,
                _DOC_FAILED,
                _DOC_PENDING,
            )
        },
    )

    result = service.retrieve(_FAULT_CLASS, top_k=5)

    assert result.reason is None
    approved = records[0]
    assert tuple(item.chunk_id for item in result.evidence) == (
        approved.chunk.chunk_id,
    )
    assert scorer.calls == [
        (
            _FAULT_CLASS,
            approved.chunk.chunk_id,
            "Synthetic approved content.",
        )
    ]


def test_atomic_approval_replaces_old_version_without_candidate_leakage() -> None:
    lifecycle, documents = _governance()
    approved_v1 = _approved_document(lifecycle, identity=_DOC_APPROVED)
    candidate = _register(
        lifecycle,
        identity=_DOC_APPROVED,
        content_hash=_HASH_V2,
        version=2,
        expected_revision=approved_v1.revision,
    )
    pending_v2 = _pending(lifecycle, candidate, version=2)

    old = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="version_one",
        content="Synthetic approved version one.",
    )
    new = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V2,
        chunk_key="version_two",
        content="Synthetic candidate version two.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((old, new))
    scorer = _RecordingScorer(scores={old.chunk.chunk_id: 0.4, new.chunk.chunk_id: 0.8})
    service = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    before_approval = service.retrieve(_FAULT_CLASS, top_k=5)
    assert tuple(item.chunk_id for item in before_approval.evidence) == (
        old.chunk.chunk_id,
    )
    assert tuple(call[1] for call in scorer.calls) == (old.chunk.chunk_id,)

    approved_v2 = _approve(lifecycle, pending_v2, version=2)
    scorer.calls.clear()
    after_approval = service.retrieve(_FAULT_CLASS, top_k=5)

    assert approved_v2.document.version(1).status is DocumentStatus.SUPERSEDED
    assert approved_v2.document.version(2).status is DocumentStatus.APPROVED
    assert tuple(item.chunk_id for item in after_approval.evidence) == (
        new.chunk.chunk_id,
    )
    assert tuple(call[1] for call in scorer.calls) == (new.chunk.chunk_id,)


def test_concurrent_approval_during_scoring_invalidates_the_scored_revision() -> None:
    lifecycle, documents = _governance()
    approved_v1 = _approved_document(lifecycle, identity=_DOC_APPROVED)
    registered_v2 = _register(
        lifecycle,
        identity=_DOC_APPROVED,
        content_hash=_HASH_V2,
        version=2,
        expected_revision=approved_v1.revision,
    )
    pending_v2 = _pending(lifecycle, registered_v2, version=2)
    old = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="racing_version_one",
        content="Synthetic version one scored before a concurrent approval.",
    )
    replacement = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V2,
        chunk_key="racing_version_two",
        content="Synthetic version two approved while scoring is in progress.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((old, replacement))
    scorer = _ApprovingScorer(lifecycle=lifecycle, pending=pending_v2)
    service = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert scorer.calls == 1
    assert scorer.pending.document.current_version == 2
    assert scorer.pending.document.version(1).status is DocumentStatus.SUPERSEDED
    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED


def test_ranking_ties_top_k_and_repetition_are_deterministic() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    records = (
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="tie_b",
            content="Synthetic tied B.",
            page_number=2,
        ),
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="highest",
            content="Synthetic highest.",
            page_number=3,
        ),
        _record(
            document_id=_DOC_APPROVED,
            source_hash=_HASH_V1,
            chunk_key="tie_a",
            content="Synthetic tied A.",
            page_number=1,
        ),
    )
    tie_b, highest, tie_a = records
    chunks = InMemoryChunkRepository()
    chunks.save(records)
    scorer = _RecordingScorer(
        scores={
            tie_a.chunk.chunk_id: 0.7,
            tie_b.chunk.chunk_id: 0.7,
            highest.chunk.chunk_id: 0.9,
        }
    )
    service = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    first = service.retrieve(_FAULT_CLASS, top_k=2)
    second = service.retrieve(_FAULT_CLASS, top_k=2)

    assert first == second
    assert tuple(item.chunk_id for item in first.evidence) == (
        highest.chunk.chunk_id,
        tie_a.chunk.chunk_id,
    )
    assert len(first.evidence) == 2
    assert (
        first.mapping_sha256 == service.retrieve(_FAULT_CLASS, top_k=1).mapping_sha256
    )
    assert set(RankedKnowledgeEvidence.__dataclass_fields__) == {
        "document_id",
        "document_version",
        "chunk_id",
        "page_number",
        "section_id",
        "score",
    }
    assert all(item.score == item.score for item in first.evidence)


def test_retrieval_result_copies_and_validates_nested_evidence() -> None:
    original = RankedKnowledgeEvidence(
        document_id=_DOC_APPROVED,
        document_version=f"docver_{_HASH_V1}",
        chunk_id=f"chunk_{'a' * 64}",
        page_number=1,
        section_id=f"section_{'b' * 64}",
        score=0.75,
    )
    result = KnowledgeRetrievalResult(
        fault_class=_FAULT_CLASS,
        mapping_version=_MAPPING_VERSION,
        mapping_sha256="3" * 64,
        evidence=(original,),
        reason=None,
    )

    assert result.evidence[0] is not original
    object.__setattr__(original, "score", 0.1)
    assert result.evidence[0].score == 0.75

    with pytest.raises(ValueError, match="evidence"):
        KnowledgeRetrievalResult(
            fault_class=_FAULT_CLASS,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256="3" * 64,
            evidence=(cast(RankedKnowledgeEvidence, object()),),
            reason=None,
        )


def test_empty_and_invalid_scores_never_produce_partial_ranking() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="score_candidate",
        content="Synthetic scoring candidate.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((record,))

    empty = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=_RecordingScorer(scores={record.chunk.chunk_id: None}),
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    ).retrieve(_FAULT_CLASS, top_k=2)
    invalid = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=_RecordingScorer(scores={record.chunk.chunk_id: float("nan")}),
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    ).retrieve(_FAULT_CLASS, top_k=2)

    assert empty.evidence == ()
    assert empty.reason is KnowledgeRetrievalReason.EMPTY_RANKING
    assert invalid.evidence == ()
    assert invalid.reason is KnowledgeRetrievalReason.RANKING_FAILED


def test_duplicate_indexed_evidence_fails_before_scoring() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    first = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="duplicate",
        content="Synthetic duplicate identity.",
    )
    second = replace(
        first,
        embedding=replace(
            first.embedding,
            provider_id="synthetic-second-embedding",
            representation_version="synthetic-second-embedding.v1",
        ),
    )
    chunks = InMemoryChunkRepository()
    chunks.save((first, second))
    scorer = _RecordingScorer(scores={first.chunk.chunk_id: 0.5})
    service = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    result = service.retrieve(_FAULT_CLASS, top_k=2)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


@pytest.mark.parametrize("top_k", [0, 11, True])
def test_top_k_must_stay_within_the_closed_internal_budget(top_k: object) -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    service = _retrieval(
        documents=documents,
        chunks=InMemoryChunkRepository(),
        scorer=_RecordingScorer(scores={}),
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    with pytest.raises(KnowledgeRetrievalInputError, match="Top-k"):
        service.retrieve(_FAULT_CLASS, top_k=top_k)  # type: ignore[arg-type]


def test_hostile_str_subclasses_are_rejected_before_comparison_or_hashing() -> None:
    MatchEveryFault.equality_calls = 0
    LyingHash.inequality_calls = 0
    BadHashStr.hash_calls = 0

    hostile_fault = MatchEveryFault(_FAULT_CLASS)
    with pytest.raises(FaultKnowledgeMappingError, match="text"):
        build_fault_knowledge_mapping(
            mapping_version=_MAPPING_VERSION,
            mappings={hostile_fault: (_DOC_APPROVED,)},
        )
    with pytest.raises(FaultKnowledgeMappingError, match="text"):
        build_fault_knowledge_mapping(
            mapping_version=MatchEveryFault(_MAPPING_VERSION),
            mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
        )

    valid = build_fault_knowledge_mapping(
        mapping_version=_MAPPING_VERSION,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    hostile_hash = {
        "schema_version": 1,
        "mapping_version": _MAPPING_VERSION,
        "mapping_sha256": LyingHash("0" * 64),
        "mappings": [{"fault_class": _FAULT_CLASS, "document_ids": [_DOC_APPROVED]}],
    }
    with pytest.raises(FaultKnowledgeMappingError, match="text"):
        validate_fault_knowledge_mapping(hostile_hash)

    bad_first = BadHashStr(_DOC_APPROVED)
    bad_second = BadHashStr(_DOC_APPROVED)
    with pytest.raises(FaultKnowledgeMappingError, match="reference"):
        build_fault_knowledge_mapping(
            mapping_version=valid.mapping_version,
            mappings={_FAULT_CLASS: (bad_first, bad_second)},
        )

    assert MatchEveryFault.equality_calls == 0
    assert LyingHash.inequality_calls == 0
    assert BadHashStr.hash_calls == 0


def test_hostile_fault_lookup_is_rejected_without_matching_another_class() -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="hostile_fault_lookup",
        content="Synthetic exact-class content.",
    )
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.8})
    service, _documents = _approved_retrieval(records=(record,), scorer=scorer)
    MatchEveryFault.equality_calls = 0

    with pytest.raises(KnowledgeRetrievalInputError, match="canonical slug"):
        service.retrieve(MatchEveryFault(_UNMAPPED_CLASS), top_k=1)

    assert MatchEveryFault.equality_calls == 0
    assert scorer.calls == []


def test_hostile_mapping_items_is_typed_without_invoking_the_override() -> None:
    _HostileItems.items_calls = 0
    hostile = _HostileItems({_FAULT_CLASS: (_DOC_APPROVED,)})

    with pytest.raises(FaultKnowledgeMappingError, match="mappings"):
        build_fault_knowledge_mapping(
            mapping_version=_MAPPING_VERSION,
            mappings=hostile,
        )

    assert _HostileItems.items_calls == 0


@pytest.mark.parametrize("mixed_with_intact", [False, True])
def test_any_cryptographic_chunk_corruption_aborts_before_scoring(
    mixed_with_intact: bool,
) -> None:
    intact = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="integrity_intact",
        content="Synthetic intact content.",
    )
    corrupt_source = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="integrity_corrupt",
        content="Synthetic corrupt content.",
        page_number=2,
    )
    corrupt = replace(
        corrupt_source,
        chunk=replace(corrupt_source.chunk, content_sha256="0" * 64),
    )
    records = (intact, corrupt) if mixed_with_intact else (corrupt,)
    scorer = _RecordingScorer(
        scores={
            intact.chunk.chunk_id: 0.9,
            corrupt.chunk.chunk_id: 0.8,
        }
    )
    service, _documents = _approved_retrieval(records=records, scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=2)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


@pytest.mark.parametrize("identity", ["section_id", "chunk_id"])
def test_syntactically_valid_but_reassigned_index_identity_fails_closed(
    identity: str,
) -> None:
    intact = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key=f"reassigned_{identity}",
        content="Synthetic content whose deterministic identity was reassigned.",
    )
    if identity == "section_id":
        tampered = replace(
            intact,
            chunk=replace(intact.chunk, section_id=f"section_{'e' * 64}"),
        )
    else:
        reassigned_chunk_id = f"chunk_{'f' * 64}"
        tampered = replace(
            intact,
            chunk=replace(intact.chunk, chunk_id=reassigned_chunk_id),
            embedding=replace(intact.embedding, chunk_id=reassigned_chunk_id),
        )
    scorer = _RecordingScorer(scores={tampered.chunk.chunk_id: 0.9})
    service, _documents = _approved_retrieval(records=(tampered,), scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


def test_non_string_index_identity_is_an_integrity_failure_before_scoring() -> None:
    malformed = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="object_identity",
        content="Synthetic malformed identity.",
    )
    object.__setattr__(malformed.chunk, "chunk_id", object())
    scorer = _RecordingScorer(scores={})
    service, _documents = _approved_retrieval(records=(malformed,), scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


def test_malformed_repository_snapshot_is_sanitized_as_integrity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="malformed_snapshot",
        content="Synthetic snapshot boundary.",
    )
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.8})
    service, documents = _approved_retrieval(records=(record,), scorer=scorer)
    malformed = object.__new__(DocumentSnapshot)
    object.__setattr__(malformed, "revision", 1)
    object.__setattr__(malformed, "document", object())

    def malformed_get(_identity: str) -> DocumentSnapshot:
        return malformed

    monkeypatch.setattr(documents, "get", malformed_get)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


def test_truncated_lifecycle_history_cannot_authorize_approved_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="truncated_lifecycle",
        content="Synthetic evidence backed by a truncated lifecycle history.",
    )
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.8})
    service, documents = _approved_retrieval(records=(record,), scorer=scorer)
    approved = documents.get(_DOC_APPROVED)
    assert approved is not None
    truncated = DocumentSnapshot(
        document=replace(
            approved.document,
            history=(approved.document.history[0],),
        ),
        revision=approved.revision,
    )

    def truncated_get(_identity: str) -> DocumentSnapshot:
        return truncated

    monkeypatch.setattr(documents, "get", truncated_get)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == []


def test_repository_and_reader_exceptions_are_typed_without_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="boundary_exception",
        content="Synthetic boundary exception.",
    )
    repository_scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.8})
    repository_service, documents = _approved_retrieval(
        records=(record,),
        scorer=repository_scorer,
    )

    def fail_repository(_identity: str) -> DocumentSnapshot | None:
        raise TypeError("hostile repository")

    monkeypatch.setattr(documents, "get", fail_repository)
    repository_result = repository_service.retrieve(_FAULT_CLASS, top_k=1)

    lifecycle, reader_documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    reader_scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.8})
    reader_service = _retrieval(
        documents=reader_documents,
        chunks=_StaticChunkReader(
            records=(),
            failure=RuntimeError("hostile reader"),
        ),
        scorer=reader_scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    reader_result = reader_service.retrieve(_FAULT_CLASS, top_k=1)

    assert repository_result.reason is KnowledgeRetrievalReason.INDEX_UNAVAILABLE
    assert reader_result.reason is KnowledgeRetrievalReason.INDEX_UNAVAILABLE
    assert repository_scorer.calls == []
    assert reader_scorer.calls == []


def test_malformed_reader_collection_is_an_integrity_failure_without_scoring() -> None:
    scorer = _StaticScorer(value=0.8)
    service, _documents = _approved_retrieval(records=object(), scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED
    assert scorer.calls == 0


def test_scorer_mutation_is_detected_and_cannot_change_frozen_evidence() -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="scorer_mutation",
        content="Synthetic immutable evidence.",
    )
    scorer = _MutatingScorer()
    service, _documents = _approved_retrieval(records=(record,), scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.RANKING_FAILED
    assert scorer.calls == 1
    assert record.chunk.document_id == _DOC_APPROVED


@pytest.mark.parametrize(
    ("score", "failure"),
    [
        (10**10_000, None),
        (None, RuntimeError("hostile scorer")),
    ],
    ids=("overflow", "exception"),
)
def test_scorer_overflow_and_exceptions_return_ranking_failed(
    score: object,
    failure: Exception | None,
) -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="scorer_failure",
        content="Synthetic scorer failure.",
    )
    scorer = _StaticScorer(value=score, failure=failure)
    service, _documents = _approved_retrieval(records=(record,), scorer=scorer)

    result = service.retrieve(_FAULT_CLASS, top_k=1)

    assert result.evidence == ()
    assert result.reason is KnowledgeRetrievalReason.RANKING_FAILED
    assert scorer.calls == 1


def test_governed_policy_identity_is_deterministic_and_semantic() -> None:
    first = build_governed_retrieval_policy(
        policy_version=_POLICY_VERSION,
        minimum_score=0.75,
    )
    repeated = build_governed_retrieval_policy(
        policy_version=_POLICY_VERSION,
        minimum_score=0.75,
    )
    changed = build_governed_retrieval_policy(
        policy_version=_POLICY_VERSION,
        minimum_score=0.76,
    )
    positive_zero = build_governed_retrieval_policy(
        policy_version=_POLICY_VERSION,
        minimum_score=0.0,
    )
    negative_zero = build_governed_retrieval_policy(
        policy_version=_POLICY_VERSION,
        minimum_score=-0.0,
    )

    assert first == repeated
    assert first.policy_sha256 != changed.policy_sha256
    assert positive_zero == negative_zero
    assert len(first.policy_sha256) == 64
    with pytest.raises(ValueError, match="hash"):
        GovernedRetrievalPolicy(
            schema_version=first.schema_version,
            policy_version=first.policy_version,
            minimum_score=first.minimum_score,
            policy_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "disposition",
    (ModelDisposition.NORMAL, ModelDisposition.OUT_OF_DISTRIBUTION),
)
def test_non_fault_dispositions_skip_governed_search_and_ignore_stale_fault_data(
    disposition: ModelDisposition,
) -> None:
    backend = _StaticApprovedRetriever(
        result=object(),
        failure=AssertionError("approved retrieval must not be called"),
    )
    service = _governed_retrieval(backend)
    MatchEveryFault.equality_calls = 0

    result = service.retrieve(
        disposition=disposition,
        fault_class=MatchEveryFault(_FAULT_CLASS),
        top_k=0,
    )

    assert result.status is GovernedRetrievalStatus.NO_EVIDENCE
    assert result.fault_class is None
    assert result.mapping_version is None
    assert result.mapping_sha256 is None
    assert result.evidence == ()
    assert backend.calls == []
    assert MatchEveryFault.equality_calls == 0


def test_missing_and_unmapped_faults_never_reach_search_or_scorer() -> None:
    missing_backend = _StaticApprovedRetriever(
        result=object(),
        failure=AssertionError("missing fault class must not reach retrieval"),
    )
    missing = _governed_retrieval(missing_backend).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=None,
        top_k=3,
    )

    lifecycle, documents = _governance()
    del lifecycle
    reader = _StaticChunkReader(
        records=(),
        failure=AssertionError("unmapped fault must not reach indexed search"),
    )
    scorer = _StaticScorer(
        failure=AssertionError("unmapped fault must not reach scorer")
    )
    approved = _retrieval(
        documents=documents,
        chunks=reader,
        scorer=scorer,
        mappings={_FAULT_CLASS: ()},
    )
    unmapped = _governed_retrieval(approved).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_UNMAPPED_CLASS,
        top_k=3,
    )

    assert missing.status is GovernedRetrievalStatus.UNMAPPED_FAULT
    assert missing.evidence == ()
    assert missing_backend.calls == []
    assert unmapped.status is GovernedRetrievalStatus.UNMAPPED_FAULT
    assert unmapped.fault_class == _UNMAPPED_CLASS
    assert unmapped.evidence == ()
    assert reader.calls == 0
    assert scorer.calls == 0


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    (
        (
            KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED,
            GovernedRetrievalStatus.UNMAPPED_FAULT,
        ),
        (
            KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
            GovernedRetrievalStatus.NO_EVIDENCE,
        ),
        (
            KnowledgeRetrievalReason.EMPTY_RANKING,
            GovernedRetrievalStatus.NO_EVIDENCE,
        ),
        (
            KnowledgeRetrievalReason.INDEX_UNAVAILABLE,
            GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
        ),
        (
            KnowledgeRetrievalReason.INDEX_INTEGRITY_FAILED,
            GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
        ),
        (
            KnowledgeRetrievalReason.RANKING_FAILED,
            GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
        ),
    ),
)
def test_governed_retrieval_maps_empty_and_technical_states_without_ambiguity(
    reason: KnowledgeRetrievalReason,
    expected_status: GovernedRetrievalStatus,
) -> None:
    backend = _StaticApprovedRetriever(result=_knowledge_result(reason=reason))

    result = _governed_retrieval(backend).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=3,
    )

    assert result.status is expected_status
    assert result.fault_class == _FAULT_CLASS
    assert result.mapping_version == _MAPPING_VERSION
    assert result.mapping_sha256 == "3" * 64
    assert result.evidence == ()
    assert backend.calls == [(_FAULT_CLASS, 3)]


def test_governed_threshold_accepts_equality_and_preserves_metadata() -> None:
    above = _ranked_evidence(key="a", score=0.91, page_number=1)
    equal = _ranked_evidence(key="b", score=0.75, page_number=2)
    below = _ranked_evidence(key="c", score=0.749, page_number=3)
    backend_result = _knowledge_result(evidence=(above, equal, below))
    backend = _StaticApprovedRetriever(result=backend_result)
    service = _governed_retrieval(backend, minimum_score=0.75)

    result = service.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=3,
    )

    assert result.status is GovernedRetrievalStatus.EVIDENCE
    assert result.minimum_score == 0.75
    assert result.policy_version == _POLICY_VERSION
    assert len(result.policy_sha256) == 64
    assert result.mapping_version == _MAPPING_VERSION
    assert tuple(item.chunk_id for item in result.evidence) == (
        above.chunk_id,
        equal.chunk_id,
    )
    assert tuple(item.page_number for item in result.evidence) == (1, 2)
    assert all(
        item.document_id
        and item.document_version
        and item.chunk_id
        and item.section_id
        and item.content
        and item.content_sha256 == sha256(item.content.encode("utf-8")).hexdigest()
        for item in result.evidence
    )
    assert backend.calls == [(_FAULT_CLASS, 3)]

    original_content = result.evidence[0].content
    object.__setattr__(backend_result.evidence[0], "score", 0.1)
    object.__setattr__(backend_result.evidence[0], "content", "Mutated after return.")
    assert result.evidence[0].score == 0.91
    assert result.evidence[0].content == original_content


def test_all_scores_below_threshold_are_a_legitimate_no_evidence_result() -> None:
    backend = _StaticApprovedRetriever(
        result=_knowledge_result(
            evidence=(
                _ranked_evidence(key="a", score=0.74, page_number=1),
                _ranked_evidence(key="b", score=0.25, page_number=2),
            )
        )
    )

    result = _governed_retrieval(backend, minimum_score=0.75).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )

    assert result.status is GovernedRetrievalStatus.NO_EVIDENCE
    assert result.mapping_version == _MAPPING_VERSION
    assert result.evidence == ()


def test_governed_content_obeys_existing_individual_and_total_budgets() -> None:
    oversized_content = "x" * (MAX_EVIDENCE_CONTENT_CHARACTERS + 1)
    oversized = RankedKnowledgeSnapshot(
        document_id=_DOC_APPROVED,
        document_version=f"docver_{_HASH_V1}",
        chunk_id=f"chunk_{'a' * 64}",
        page_number=1,
        section_id=f"section_{'a' * 64}",
        content=oversized_content,
        content_sha256=sha256(oversized_content.encode("utf-8")).hexdigest(),
        score=1.0,
    )
    oversized_result = _governed_retrieval(
        _StaticApprovedRetriever(result=_knowledge_result(evidence=(oversized,))),
        minimum_score=0.0,
    ).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )

    snapshots: list[RankedKnowledgeSnapshot] = []
    for index, key in enumerate("abcdefg", start=1):
        content = key * MAX_EVIDENCE_CONTENT_CHARACTERS
        snapshots.append(
            RankedKnowledgeSnapshot(
                document_id=_DOC_APPROVED,
                document_version=f"docver_{_HASH_V1}",
                chunk_id=f"chunk_{key * 64}",
                page_number=index,
                section_id=f"section_{key * 64}",
                content=content,
                content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                score=1.0 - index / 100,
            )
        )
    bounded_result = _governed_retrieval(
        _StaticApprovedRetriever(result=_knowledge_result(evidence=tuple(snapshots))),
        minimum_score=0.0,
    ).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=len(snapshots),
    )

    assert oversized_result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert oversized_result.evidence == ()
    assert bounded_result.status is GovernedRetrievalStatus.EVIDENCE
    assert sum(len(item.content) for item in bounded_result.evidence) == (
        MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS
    )
    assert tuple(item.chunk_id for item in bounded_result.evidence) == tuple(
        item.chunk_id for item in snapshots[:6]
    )


def test_governed_ties_and_repeated_calls_keep_the_canonical_order() -> None:
    first_tie = _ranked_evidence(key="a", score=0.8, page_number=1)
    second_tie = _ranked_evidence(key="b", score=0.8, page_number=2)
    backend = _StaticApprovedRetriever(
        result=_knowledge_result(evidence=(first_tie, second_tie))
    )
    service = _governed_retrieval(backend, minimum_score=0.0)

    first = service.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )
    second = service.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )

    assert first == second
    assert tuple(item.chunk_id for item in first.evidence) == (
        first_tie.chunk_id,
        second_tie.chunk_id,
    )
    assert backend.calls == [(_FAULT_CLASS, 2), (_FAULT_CLASS, 2)]


@pytest.mark.parametrize("contract_failure", ("order", "duplicate"))
def test_noncanonical_backend_ranking_fails_closed(contract_failure: str) -> None:
    first = _ranked_evidence(key="a", score=0.9, page_number=1)
    second = _ranked_evidence(key="b", score=0.8, page_number=2)
    evidence = (second, first) if contract_failure == "order" else (first, first)
    backend = _StaticApprovedRetriever(result=_knowledge_result(evidence=evidence))

    result = _governed_retrieval(backend, minimum_score=0.0).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )

    assert result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert result.evidence == ()


def test_backend_exceptions_malformed_results_and_fault_mismatch_are_total() -> None:
    evidence = (_ranked_evidence(key="a", score=0.9, page_number=1),)
    backends = (
        _StaticApprovedRetriever(
            result=object(),
            failure=RuntimeError("synthetic retrieval failure"),
        ),
        _StaticApprovedRetriever(result=object()),
        _StaticApprovedRetriever(
            result=_knowledge_result(
                fault_class=_EMPTY_CLASS,
                evidence=evidence,
            )
        ),
    )

    results = tuple(
        _governed_retrieval(backend, minimum_score=0.0).retrieve(
            disposition=ModelDisposition.FAULT,
            fault_class=_FAULT_CLASS,
            top_k=1,
        )
        for backend in backends
    )

    assert all(
        result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
        for result in results
    )
    assert all(result.evidence == () for result in results)
    assert "synthetic retrieval failure" not in repr(results)


@pytest.mark.parametrize("mutation", ("score", "content"))
def test_mutated_backend_evidence_is_rejected_at_the_governed_boundary(
    mutation: str,
) -> None:
    backend_result = _knowledge_result(
        evidence=(_ranked_evidence(key="a", score=0.9, page_number=1),)
    )
    if mutation == "score":
        object.__setattr__(backend_result.evidence[0], "score", float("nan"))
    else:
        object.__setattr__(
            backend_result.evidence[0],
            "content",
            "Synthetic content changed without its hash.",
        )
    backend = _StaticApprovedRetriever(result=backend_result)

    result = _governed_retrieval(backend, minimum_score=0.0).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )

    assert result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert result.evidence == ()


@pytest.mark.parametrize("top_k", (0, 11, True))
def test_invalid_governed_top_k_fails_closed_without_calling_backend(
    top_k: object,
) -> None:
    backend = _StaticApprovedRetriever(
        result=object(),
        failure=AssertionError("invalid top-k must not reach retrieval"),
    )

    result = _governed_retrieval(backend).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=top_k,  # type: ignore[arg-type]
    )

    assert result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert result.evidence == ()
    assert backend.calls == []


def test_empty_approved_coverage_is_no_evidence_without_search_or_scoring() -> None:
    lifecycle, documents = _governance()
    del lifecycle
    reader = _StaticChunkReader(
        records=(),
        failure=AssertionError("empty coverage must not reach indexed search"),
    )
    scorer = _StaticScorer(
        failure=AssertionError("empty coverage must not reach scorer")
    )
    approved = _retrieval(
        documents=documents,
        chunks=reader,
        scorer=scorer,
        mappings={_EMPTY_CLASS: ()},
    )

    result = _governed_retrieval(approved).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_EMPTY_CLASS,
        top_k=3,
    )

    assert result.status is GovernedRetrievalStatus.NO_EVIDENCE
    assert result.evidence == ()
    assert reader.calls == 0
    assert scorer.calls == 0


def test_governed_retrieval_never_returns_a_rejected_document() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    rejected_pending = _pending(
        lifecycle,
        _register(lifecycle, identity=_DOC_REJECTED),
    )
    lifecycle.reject(
        identity=_DOC_REJECTED,
        version=1,
        actor="synthetic-reviewer",
        reason="Synthetic rejected evidence.",
        expected_revision=rejected_pending.revision,
    )
    approved_record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="governed_approved",
        content="Synthetic approved governed evidence.",
    )
    rejected_record = _record(
        document_id=_DOC_REJECTED,
        source_hash=_HASH_V1,
        chunk_key="governed_rejected",
        content="Synthetic rejected governed evidence.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((approved_record, rejected_record))
    scorer = _RecordingScorer(
        scores={
            approved_record.chunk.chunk_id: 0.8,
            rejected_record.chunk.chunk_id: 0.99,
        }
    )
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED, _DOC_REJECTED)},
    )

    result = _governed_retrieval(approved, minimum_score=0.0).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )

    assert result.status is GovernedRetrievalStatus.EVIDENCE
    assert tuple(item.document_id for item in result.evidence) == (_DOC_APPROVED,)
    assert tuple(call[1] for call in scorer.calls) == (approved_record.chunk.chunk_id,)


def test_governed_retrieval_never_returns_a_superseded_version() -> None:
    lifecycle, documents = _governance()
    approved_v1 = _approved_document(lifecycle, identity=_DOC_APPROVED)
    registered_v2 = _register(
        lifecycle,
        identity=_DOC_APPROVED,
        content_hash=_HASH_V2,
        version=2,
        expected_revision=approved_v1.revision,
    )
    pending_v2 = _pending(lifecycle, registered_v2, version=2)
    _approve(lifecycle, pending_v2, version=2)
    obsolete = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="governed_obsolete",
        content="Synthetic superseded governed evidence.",
    )
    current = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V2,
        chunk_key="governed_current",
        content="Synthetic current governed evidence.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((obsolete, current))
    scorer = _RecordingScorer(
        scores={obsolete.chunk.chunk_id: 0.99, current.chunk.chunk_id: 0.8}
    )
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    result = _governed_retrieval(approved, minimum_score=0.0).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=2,
    )

    assert result.status is GovernedRetrievalStatus.EVIDENCE
    assert tuple(item.document_version for item in result.evidence) == (
        current.chunk.document_version,
    )
    assert tuple(call[1] for call in scorer.calls) == (current.chunk.chunk_id,)


def test_content_snapshot_and_content_free_result_share_one_approved_ranking() -> None:
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="shared_ranking",
        content="Synthetic content available only to the internal RAG boundary.",
    )
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.83})
    approved, _documents = _approved_retrieval(records=(record,), scorer=scorer)

    content_free = approved.retrieve(_FAULT_CLASS, top_k=1)
    snapshot = approved.retrieve_snapshots(_FAULT_CLASS, top_k=1)

    assert content_free.reason is None
    assert snapshot.reason is None
    assert len(content_free.evidence) == len(snapshot.evidence) == 1
    assert content_free.evidence[0].chunk_id == snapshot.evidence[0].chunk_id
    assert not hasattr(content_free.evidence[0], "content")
    assert snapshot.evidence[0].content == record.chunk.content
    assert snapshot.evidence[0].content_sha256 == record.chunk.content_sha256
    assert scorer.calls == [
        (_FAULT_CLASS, record.chunk.chunk_id, record.chunk.content),
        (_FAULT_CLASS, record.chunk.chunk_id, record.chunk.content),
    ]


def test_internal_snapshot_content_is_never_exposed_by_representations() -> None:
    marker = "SENSITIVE_SYNTHETIC_MARKER"
    snapshot = RankedKnowledgeSnapshot(
        document_id=_DOC_APPROVED,
        document_version=f"docver_{_HASH_V1}",
        chunk_id=f"chunk_{'a' * 64}",
        page_number=1,
        section_id=f"section_{'a' * 64}",
        content=marker,
        content_sha256=sha256(marker.encode("utf-8")).hexdigest(),
        score=0.9,
    )
    snapshot_result = _knowledge_result(evidence=(snapshot,))
    governed = _governed_retrieval(
        _StaticApprovedRetriever(result=snapshot_result),
        minimum_score=0.0,
    ).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )
    failed = _governed_retrieval(
        _StaticApprovedRetriever(
            result=object(),
            failure=RuntimeError(marker),
        ),
        minimum_score=0.0,
    ).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )

    assert snapshot.content == marker
    assert governed.evidence[0].content == marker
    assert marker not in repr(snapshot)
    assert marker not in repr(snapshot_result)
    assert marker not in repr(governed)
    assert failed.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert failed.evidence == ()
    assert marker not in repr(failed)


def test_concurrent_content_replacement_fails_before_governed_materialization() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    original = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="content_before_race",
        content="Synthetic content scored before the concurrent replacement.",
    )
    replacement = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="content_after_race",
        content="Synthetic replacement observed during final revalidation.",
    )
    reader = _SwappingChunkReader(first=(original,), replacement=(replacement,))
    scorer = _RecordingScorer(scores={original.chunk.chunk_id: 0.9})
    approved = _retrieval(
        documents=documents,
        chunks=reader,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )

    result = _governed_retrieval(approved, minimum_score=0.0).retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )

    assert reader.calls == 2
    assert scorer.calls == [
        (_FAULT_CLASS, original.chunk.chunk_id, original.chunk.content)
    ]
    assert result.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE
    assert result.evidence == ()
    assert original.chunk.content not in repr(result)
    assert replacement.chunk.content not in repr(result)


def test_exact_snapshot_currentness_revalidates_without_rescoring_or_reranking() -> (
    None
):
    lifecycle, documents = _governance()
    approved_v1 = _approved_document(lifecycle, identity=_DOC_APPROVED)
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="currentness",
        content="Synthetic evidence revalidated around generation.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((record,))
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.9})
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    retrieved = approved.retrieve_snapshots(_FAULT_CLASS, top_k=1)

    assert retrieved.reason is None
    assert (
        approved.snapshots_are_current(
            fault_class=retrieved.fault_class,
            mapping_version=retrieved.mapping_version,
            mapping_sha256=retrieved.mapping_sha256,
            evidence=retrieved.evidence,
        )
        is True
    )
    assert scorer.calls == [(_FAULT_CLASS, record.chunk.chunk_id, record.chunk.content)]

    registered_v2 = _register(
        lifecycle,
        identity=_DOC_APPROVED,
        content_hash=_HASH_V2,
        version=2,
        expected_revision=approved_v1.revision,
    )
    pending_v2 = _pending(lifecycle, registered_v2, version=2)
    _approve(lifecycle, pending_v2, version=2)

    assert (
        approved.snapshots_are_current(
            fault_class=retrieved.fault_class,
            mapping_version=retrieved.mapping_version,
            mapping_sha256=retrieved.mapping_sha256,
            evidence=retrieved.evidence,
        )
        is False
    )
    assert scorer.calls == [(_FAULT_CLASS, record.chunk.chunk_id, record.chunk.content)]


def test_guardrail_rejects_real_lifecycle_change_during_provider_call() -> None:
    lifecycle, documents = _governance()
    approved_v1 = _approved_document(lifecycle, identity=_DOC_APPROVED)
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="provider_race",
        content="Synthetic v1 evidence current before provider execution.",
    )
    chunks = InMemoryChunkRepository()
    chunks.save((record,))
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.9})
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    governed = GovernedKnowledgeRetrievalService(
        approved_retrieval=approved,
        policy=build_governed_retrieval_policy(
            policy_version=_POLICY_VERSION,
            minimum_score=0.75,
        ),
    )
    retrieval = governed.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )

    class ApprovingV2Provider:
        call_count = 0

        def generate(self, request: ProviderRequest) -> ProviderResponse:
            self.call_count += 1
            registered_v2 = _register(
                lifecycle,
                identity=_DOC_APPROVED,
                content_hash=_HASH_V2,
                version=2,
                expected_revision=approved_v1.revision,
            )
            pending_v2 = _pending(lifecycle, registered_v2, version=2)
            _approve(lifecycle, pending_v2, version=2)
            return _supported_provider_response(request)

    provider = ApprovingV2Provider()
    result = RagGuardrailService(
        provider=provider,
        snapshot_currentness=governed,
    ).generate(
        diagnosis=Diagnosis(
            fault_code=_FAULT_CLASS,
            technical_summary="Synthetic immutable diagnostic result.",
        ),
        retrieval=retrieval,
    )

    assert retrieval.status is GovernedRetrievalStatus.EVIDENCE
    assert provider.call_count == 1
    assert result.status is RagGuardrailStatus.REFUSED
    assert result.generation is None
    assert result.refusal is not None
    assert result.refusal.code is RagRefusalCode.STALE_EVIDENCE
    assert scorer.calls == [(_FAULT_CLASS, record.chunk.chunk_id, record.chunk.content)]
    assert record.chunk.content not in repr(result)


def test_guardrail_maps_real_index_unavailability_before_provider() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="pre_currentness_unavailable",
        content="Synthetic evidence hidden by a pre-provider index failure.",
    )
    chunks = _StaticChunkReader(records=(record,))
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.9})
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    governed = _governed_retrieval(approved)
    retrieval = governed.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )
    private_marker = "SYNTHETIC_PRIVATE_PRE_INDEX_FAILURE"
    chunks.failure = RuntimeError(private_marker)

    class UnexpectedProvider:
        call_count = 0

        def generate(self, request: ProviderRequest) -> ProviderResponse:
            self.call_count += 1
            return _supported_provider_response(request)

    provider = UnexpectedProvider()
    result = RagGuardrailService(
        provider=provider,
        snapshot_currentness=governed,
    ).generate(
        diagnosis=Diagnosis(
            fault_code=_FAULT_CLASS,
            technical_summary="Synthetic immutable diagnostic result.",
        ),
        retrieval=retrieval,
    )

    assert provider.call_count == 0
    assert result.status is RagGuardrailStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code is RagRefusalCode.CURRENTNESS_UNAVAILABLE
    assert private_marker not in repr(result)
    assert record.chunk.content not in repr(result)


def test_guardrail_maps_real_index_unavailability_after_provider() -> None:
    lifecycle, documents = _governance()
    _approved_document(lifecycle, identity=_DOC_APPROVED)
    record = _record(
        document_id=_DOC_APPROVED,
        source_hash=_HASH_V1,
        chunk_key="post_currentness_unavailable",
        content="Synthetic evidence hidden by a post-provider index failure.",
    )
    chunks = _StaticChunkReader(records=(record,))
    scorer = _RecordingScorer(scores={record.chunk.chunk_id: 0.9})
    approved = _retrieval(
        documents=documents,
        chunks=chunks,
        scorer=scorer,
        mappings={_FAULT_CLASS: (_DOC_APPROVED,)},
    )
    governed = _governed_retrieval(approved)
    retrieval = governed.retrieve(
        disposition=ModelDisposition.FAULT,
        fault_class=_FAULT_CLASS,
        top_k=1,
    )
    private_marker = "SYNTHETIC_PRIVATE_POST_INDEX_FAILURE"

    class FailingIndexProvider:
        call_count = 0

        def generate(self, request: ProviderRequest) -> ProviderResponse:
            self.call_count += 1
            chunks.failure = RuntimeError(private_marker)
            return _supported_provider_response(request)

    provider = FailingIndexProvider()
    result = RagGuardrailService(
        provider=provider,
        snapshot_currentness=governed,
    ).generate(
        diagnosis=Diagnosis(
            fault_code=_FAULT_CLASS,
            technical_summary="Synthetic immutable diagnostic result.",
        ),
        retrieval=retrieval,
    )

    assert provider.call_count == 1
    assert result.status is RagGuardrailStatus.REFUSED
    assert result.generation is None
    assert result.refusal is not None
    assert result.refusal.code is RagRefusalCode.CURRENTNESS_UNAVAILABLE
    assert private_marker not in repr(result)
    assert record.chunk.content not in repr(result)
