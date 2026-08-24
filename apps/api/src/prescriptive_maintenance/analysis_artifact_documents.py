"""Governed document and persistence wiring for artifacts analysis mode."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from psycopg.conninfo import make_conninfo

from prescriptive_maintenance.analysis_artifacts import (
    MAX_JSON_ARTIFACT_BYTES,
    ChunkingBinding,
    DocumentBinding,
    EmbeddingBinding,
    content_sha256,
    decode_artifact_json,
    read_artifact_file,
    resolve_artifact_reference,
)
from prescriptive_maintenance.data.document_indexing import (
    ChunkingConfiguration,
    DocumentIndexingStatus,
    EmbeddingStatus,
    IndexedChunk,
    InMemoryChunkRepository,
    LocalHashEmbeddingProvider,
    index_extracted_document,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    ProcessingStep,
    SystemUtcClock,
)
from prescriptive_maintenance.document_lifecycle import (
    InMemoryDocumentRepository as InMemoryLifecycleDocumentRepository,
)
from prescriptive_maintenance.knowledge_retrieval import KnowledgeChunkScorer
from prescriptive_maintenance.persistence import (
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    InMemoryStore,
    InMemoryUnitOfWork,
    PostgresUnitOfWork,
    UnitOfWork,
    build_postgres_connection_factory,
)
from prescriptive_maintenance.settings import Settings


class AnalysisArtifactDocumentError(RuntimeError):
    """Sanitized failure while wiring approved document dependencies."""


@dataclass(frozen=True, slots=True)
class ArtifactDocumentRuntime:
    chunks: InMemoryChunkRepository
    lifecycle: InMemoryLifecycleDocumentRepository
    scorer: KnowledgeChunkScorer
    unit_of_work_factory: Callable[[], UnitOfWork]
    document_count: int
    chunk_count: int


def build_artifact_document_runtime(
    *,
    settings: Settings,
    root: Path,
    chunking_binding: ChunkingBinding,
    embedding_binding: EmbeddingBinding,
    document_bindings: tuple[DocumentBinding, ...],
) -> ArtifactDocumentRuntime:
    """Re-index, govern and bind persistence for approved derived documents."""

    try:
        chunking = ChunkingConfiguration(
            version=chunking_binding.version,
            max_characters=chunking_binding.max_characters,
            overlap_characters=chunking_binding.overlap_characters,
            cleanup_version=chunking_binding.cleanup_version,
            section_detection_version=chunking_binding.section_detection_version,
        )
        if (
            chunking.identity != chunking_binding.configuration_id
            or chunking.overlap_characters >= chunking.max_characters
        ):
            raise ValueError
        embedding = LocalHashEmbeddingProvider(dimension=embedding_binding.dimension)
        if (
            embedding.provider_id != embedding_binding.provider_id
            or embedding.representation_version
            != embedding_binding.representation_version
        ):
            raise ValueError
        chunks = InMemoryChunkRepository()
        documents = _load_documents(
            root=root,
            bindings=document_bindings,
            chunking=chunking,
            embedding=embedding,
            repository=chunks,
        )
        return ArtifactDocumentRuntime(
            chunks=chunks,
            lifecycle=_build_lifecycle(documents),
            scorer=_LocalEmbeddingScorer(embedding),
            unit_of_work_factory=_build_unit_of_work_factory(settings, documents),
            document_count=len(documents),
            chunk_count=len(chunks),
        )
    except AnalysisArtifactDocumentError:
        raise
    except Exception:
        raise AnalysisArtifactDocumentError(
            "The configured document artifacts are unavailable."
        ) from None


def _load_documents(
    *,
    root: Path,
    bindings: tuple[DocumentBinding, ...],
    chunking: ChunkingConfiguration,
    embedding: LocalHashEmbeddingProvider,
    repository: InMemoryChunkRepository,
) -> dict[str, tuple[tuple[int, tuple[IndexedChunk, ...]], ...]]:
    grouped: defaultdict[
        str,
        list[tuple[int, tuple[IndexedChunk, ...]]],
    ] = defaultdict(list)
    seen_versions: set[str] = set()
    seen_chunks: set[str] = set()
    for binding in bindings:
        extraction_path = resolve_artifact_reference(
            root,
            binding.extraction.path,
            directory=False,
        )
        extraction_bytes = read_artifact_file(
            extraction_path,
            maximum_bytes=MAX_JSON_ARTIFACT_BYTES,
        )
        if content_sha256(extraction_bytes) != binding.extraction.sha256:
            raise AnalysisArtifactDocumentError(
                "The configured document artifacts are unavailable."
            )
        result = index_extracted_document(
            decode_artifact_json(extraction_bytes),
            embedding_provider=embedding,
            repository=repository,
            configuration=chunking,
        )
        chunk_ids = tuple(record.chunk.chunk_id for record in result.records)
        if (
            result.status is not DocumentIndexingStatus.COMPLETED
            or result.failures
            or not result.records
            or result.document_id != binding.document_id
            or result.document_version != binding.document_version_id
            or result.records[0].chunk.provenance.source_sha256 != binding.source_sha256
            or chunk_ids != binding.chunk_ids
            or binding.document_version_id in seen_versions
            or any(chunk_id in seen_chunks for chunk_id in chunk_ids)
            or any(
                record.embedding.status is not EmbeddingStatus.EMBEDDED
                or record.embedding.provider_id != embedding.provider_id
                or record.embedding.representation_version
                != embedding.representation_version
                or record.embedding.dimension != embedding.dimension
                for record in result.records
            )
        ):
            raise AnalysisArtifactDocumentError(
                "The configured document artifacts are unavailable."
            )
        seen_versions.add(binding.document_version_id)
        seen_chunks.update(chunk_ids)
        grouped[binding.document_id].append((binding.version, result.records))

    normalized: dict[str, tuple[tuple[int, tuple[IndexedChunk, ...]], ...]] = {}
    for document_id, versions in grouped.items():
        ordered = tuple(sorted(versions, key=lambda item: item[0]))
        if tuple(number for number, _records in ordered) != tuple(
            range(1, len(ordered) + 1)
        ):
            raise AnalysisArtifactDocumentError(
                "The configured document artifacts are unavailable."
            )
        normalized[document_id] = ordered
    return normalized


def _build_lifecycle(
    documents: Mapping[str, tuple[tuple[int, tuple[IndexedChunk, ...]], ...]],
) -> InMemoryLifecycleDocumentRepository:
    repository = InMemoryLifecycleDocumentRepository()
    service = DocumentGovernanceService(repository=repository, clock=SystemUtcClock())
    for document_id in sorted(documents):
        revision = 0
        for version, records in documents[document_id]:
            source_sha256 = records[0].chunk.provenance.source_sha256
            snapshot = service.register(
                identity=document_id,
                version=version,
                sha256=source_sha256,
                actor="runtime.artifacts",
                expected_revision=revision,
            )
            snapshot = service.start_processing(
                identity=document_id,
                version=version,
                actor="runtime.artifacts",
                expected_revision=snapshot.revision,
            )
            snapshot = service.record_step_succeeded(
                identity=document_id,
                version=version,
                step=ProcessingStep.EXTRACTION,
                actor="runtime.artifacts",
                expected_revision=snapshot.revision,
            )
            snapshot = service.record_step_succeeded(
                identity=document_id,
                version=version,
                step=ProcessingStep.INDEXING,
                actor="runtime.artifacts",
                expected_revision=snapshot.revision,
            )
            snapshot = service.approve(
                identity=document_id,
                version=version,
                actor="runtime.artifacts",
                reason="Approved by the hash-pinned runtime manifest.",
                expected_revision=snapshot.revision,
            )
            revision = snapshot.revision
    return repository


def _build_unit_of_work_factory(
    settings: Settings,
    documents: Mapping[str, tuple[tuple[int, tuple[IndexedChunk, ...]], ...]],
) -> Callable[[], UnitOfWork]:
    expected_documents = _persistence_documents(documents)
    if settings.persistence_backend == "memory":
        store = InMemoryStore()
        with InMemoryUnitOfWork(store) as transaction:
            for document in expected_documents:
                transaction.documents.add(document)
            transaction.commit()

        def memory_factory() -> UnitOfWork:
            return InMemoryUnitOfWork(store)

        return memory_factory

    database_url = settings.database_url
    if database_url is None:
        raise AnalysisArtifactDocumentError(
            "The configured document artifacts are unavailable."
        )
    bounded_database_url = make_conninfo(
        str(database_url),
        connect_timeout=1,
    )
    connection_factory = build_postgres_connection_factory(bounded_database_url)

    def postgres_factory() -> UnitOfWork:
        return PostgresUnitOfWork(connection_factory)

    with postgres_factory() as transaction:
        for expected in expected_documents:
            stored = transaction.documents.get(expected.document_id)
            if stored is None or not _same_document_binding(stored, expected):
                raise AnalysisArtifactDocumentError(
                    "The configured document artifacts are unavailable."
                )
        transaction.rollback()
    return postgres_factory


def _persistence_documents(
    documents: Mapping[str, tuple[tuple[int, tuple[IndexedChunk, ...]], ...]],
) -> tuple[DocumentMetadata, ...]:
    now = datetime.now(UTC)
    return tuple(
        DocumentMetadata(
            document_id=document_id,
            created_at=now,
            versions=tuple(
                DocumentVersionMetadata(
                    document_version_id=records[0].chunk.document_version,
                    document_id=document_id,
                    source_sha256=records[0].chunk.provenance.source_sha256,
                    created_at=now + timedelta(microseconds=version),
                    chunks=tuple(
                        ChunkReference(
                            chunk_ref=record.chunk.chunk_id,
                            document_id=document_id,
                            document_version_id=record.chunk.document_version,
                            page_number=record.chunk.page_number,
                        )
                        for record in records
                    ),
                )
                for version, records in versions
            ),
        )
        for document_id, versions in sorted(documents.items())
    )


def _same_document_binding(
    stored: DocumentMetadata,
    expected: DocumentMetadata,
) -> bool:
    return stored.document_id == expected.document_id and tuple(
        (
            version.document_version_id,
            version.source_sha256,
            tuple(
                (chunk.chunk_ref, chunk.document_version_id, chunk.page_number)
                for chunk in version.chunks
            ),
        )
        for version in stored.versions
    ) == tuple(
        (
            version.document_version_id,
            version.source_sha256,
            tuple(
                (chunk.chunk_ref, chunk.document_version_id, chunk.page_number)
                for chunk in version.chunks
            ),
        )
        for version in expected.versions
    )


class _LocalEmbeddingScorer:
    """Rank approved chunks with the configured deterministic vector space."""

    def __init__(self, provider: LocalHashEmbeddingProvider) -> None:
        self._provider = provider

    def score(self, *, fault_class: str, chunk: IndexedChunk) -> float | None:
        vector = chunk.embedding.vector
        if (
            vector is None
            or chunk.embedding.provider_id != self._provider.provider_id
            or chunk.embedding.representation_version
            != self._provider.representation_version
            or chunk.embedding.dimension != self._provider.dimension
        ):
            return None
        query_content = f"fault-class:{fault_class}"
        query_chunk = replace(
            chunk.chunk,
            content=query_content,
            content_sha256=sha256(query_content.encode("utf-8")).hexdigest(),
        )
        query_embedding = self._provider.embed((query_chunk,))[0].vector
        if query_embedding is None:
            return None
        left_norm = math.sqrt(sum(value * value for value in vector))
        right_norm = math.sqrt(sum(value * value for value in query_embedding))
        if left_norm == 0.0 or right_norm == 0.0:
            return None
        cosine = sum(
            left * right for left, right in zip(vector, query_embedding, strict=True)
        ) / (left_norm * right_norm)
        return max(0.0, min(1.0, 0.5 + cosine / 2.0))
