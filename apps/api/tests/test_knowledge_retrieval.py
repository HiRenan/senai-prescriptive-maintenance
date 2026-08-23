"""Entirely synthetic tests for approved documentary knowledge retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from prescriptive_maintenance.contracts import DocumentStatus
from prescriptive_maintenance.data.document_indexing import (
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingStatus,
    ExtractionProvenance,
    IndexedChunk,
    InMemoryChunkRepository,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    DocumentSnapshot,
    InMemoryDocumentRepository,
    ProcessingStep,
)
from prescriptive_maintenance.knowledge_retrieval import (
    ApprovedKnowledgeRetrievalService,
    FaultKnowledgeMappingError,
    FaultKnowledgeReferenceError,
    KnowledgeRetrievalInputError,
    KnowledgeRetrievalReason,
    RankedKnowledgeEvidence,
    build_fault_knowledge_mapping,
    fault_knowledge_mapping_json_bytes,
    load_fault_knowledge_mapping,
    validate_fault_knowledge_mapping,
)

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
    chunk_id = f"chunk_{chunk_key}"
    chunk = DocumentChunk(
        schema_version=1,
        chunk_id=chunk_id,
        document_id=document_id,
        document_version=f"docver_{source_hash}",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        page_number=page_number,
        section_id=f"section_{chunk_key}",
        section_index=1,
        section_title="SYNTHETIC SECTION",
        ordinal=page_number,
        section_chunk_index=1,
        character_start=0,
        character_end=len(content),
        chunking_configuration_id=f"chunkcfg_{'9' * 64}",
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
    documents: InMemoryDocumentRepository,
    chunks: InMemoryChunkRepository,
    scorer: _RecordingScorer,
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
    assert tuple(item.chunk_id for item in result.evidence) == ("chunk_approved_valid",)
    assert scorer.calls == [
        (
            _FAULT_CLASS,
            "chunk_approved_valid",
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
    chunks = InMemoryChunkRepository()
    chunks.save(records)
    scorer = _RecordingScorer(
        scores={
            "chunk_tie_a": 0.7,
            "chunk_tie_b": 0.7,
            "chunk_highest": 0.9,
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
        "chunk_highest",
        "chunk_tie_a",
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
