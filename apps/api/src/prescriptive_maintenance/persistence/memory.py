"""Transactional in-memory persistence adapter for the standard test suite."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
from types import TracebackType

from prescriptive_maintenance.persistence.models import (
    AnalysisMetadata,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
)
from prescriptive_maintenance.persistence.ports import (
    AnalysisRepository,
    DocumentRepository,
    PersistenceConflictError,
    PersistenceIntegrityError,
    TransactionConflictError,
    UnitOfWorkStateError,
)


@dataclass(slots=True)
class _MemoryState:
    analyses: dict[str, AnalysisMetadata] = field(
        default_factory=lambda: dict[str, AnalysisMetadata]()
    )
    documents: dict[str, DocumentMetadata] = field(
        default_factory=lambda: dict[str, DocumentMetadata]()
    )


class InMemoryStore:
    """Committed state shared by short-lived in-memory units of work."""

    def __init__(self) -> None:
        self._state = _MemoryState()
        self._generation = 0
        self._lock = Lock()

    def snapshot(self) -> tuple[_MemoryState, int]:
        with self._lock:
            return (
                _MemoryState(
                    analyses=dict(self._state.analyses),
                    documents=dict(self._state.documents),
                ),
                self._generation,
            )

    def publish(self, state: _MemoryState, *, base_generation: int) -> None:
        with self._lock:
            if self._generation != base_generation:
                raise TransactionConflictError(
                    "Committed state changed during the in-memory transaction."
                )
            self._state = _MemoryState(
                analyses=dict(state.analyses),
                documents=dict(state.documents),
            )
            self._generation += 1


class InMemoryDocumentRepository(DocumentRepository):
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    def add(self, document: DocumentMetadata) -> None:
        existing = self._state.documents.get(document.document_id)
        if existing is not None:
            if existing == document:
                return
            raise PersistenceConflictError(
                "Document identifier already represents different metadata."
            )

        version_ids = {
            version.document_version_id
            for stored in self._state.documents.values()
            for version in stored.versions
        }
        chunk_refs = {
            chunk.chunk_ref
            for stored in self._state.documents.values()
            for version in stored.versions
            for chunk in version.chunks
        }
        if any(
            version.document_version_id in version_ids for version in document.versions
        ) or any(
            chunk.chunk_ref in chunk_refs
            for version in document.versions
            for chunk in version.chunks
        ):
            raise PersistenceConflictError(
                "Document version or chunk identifier is already in use."
            )
        self._state.documents[document.document_id] = document

    def add_version(self, version: DocumentVersionMetadata) -> None:
        document = self._state.documents.get(version.document_id)
        if document is None:
            raise PersistenceIntegrityError(
                "A document version requires an existing document."
            )

        stored_versions = (
            stored_version
            for stored in self._state.documents.values()
            for stored_version in stored.versions
        )
        for stored_version in stored_versions:
            if stored_version.document_version_id != version.document_version_id:
                continue
            if stored_version == version:
                return
            raise PersistenceConflictError(
                "Document version identifier already represents different metadata."
            )

        if any(
            stored_version.source_sha256 == version.source_sha256
            for stored_version in document.versions
        ):
            raise PersistenceConflictError(
                "Document version hash already represents a different identifier."
            )

        used_chunk_refs = {
            chunk.chunk_ref
            for stored in self._state.documents.values()
            for stored_version in stored.versions
            for chunk in stored_version.chunks
        }
        if any(chunk.chunk_ref in used_chunk_refs for chunk in version.chunks):
            raise PersistenceConflictError("Chunk identifier is already in use.")

        self._state.documents[document.document_id] = replace(
            document,
            versions=(*document.versions, version),
        )

    def get(self, document_id: str) -> DocumentMetadata | None:
        return self._state.documents.get(document_id)


class InMemoryAnalysisRepository(AnalysisRepository):
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    def add(self, analysis: AnalysisMetadata) -> None:
        existing = self._state.analyses.get(analysis.analysis_id)
        if existing is not None:
            if existing == analysis:
                return
            raise PersistenceConflictError(
                "Analysis identifier already represents different metadata."
            )

        for reference in analysis.evidence_references:
            if not _reference_exists(reference, self._state.documents):
                raise PersistenceIntegrityError(
                    "Evidence must reference an existing document version and chunk."
                )
        self._state.analyses[analysis.analysis_id] = analysis

    def get(self, analysis_id: str) -> AnalysisMetadata | None:
        return self._state.analyses.get(analysis_id)


def _reference_exists(
    reference: EvidenceReference,
    documents: dict[str, DocumentMetadata],
) -> bool:
    document = documents.get(reference.document_id)
    if document is None:
        return False
    return any(
        version.document_version_id == reference.document_version_id
        and any(chunk.chunk_ref == reference.chunk_ref for chunk in version.chunks)
        for version in document.versions
    )


class InMemoryUnitOfWork:
    """Stage changes and atomically publish them only after explicit commit."""

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._store = store or InMemoryStore()
        self._state: _MemoryState | None = None
        self._base_generation: int | None = None
        self._analyses: InMemoryAnalysisRepository | None = None
        self._documents: InMemoryDocumentRepository | None = None
        self._completed = False

    @property
    def analyses(self) -> AnalysisRepository:
        if self._analyses is None or self._completed:
            raise UnitOfWorkStateError("The unit of work is not active.")
        return self._analyses

    @property
    def documents(self) -> DocumentRepository:
        if self._documents is None or self._completed:
            raise UnitOfWorkStateError("The unit of work is not active.")
        return self._documents

    def __enter__(self) -> InMemoryUnitOfWork:
        if self._state is not None:
            raise UnitOfWorkStateError("The unit of work is already active.")
        self._state, self._base_generation = self._store.snapshot()
        self._analyses = InMemoryAnalysisRepository(self._state)
        self._documents = InMemoryDocumentRepository(self._state)
        self._completed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if not self._completed:
            self.rollback()
        self._state = None
        self._base_generation = None
        self._analyses = None
        self._documents = None

    def commit(self) -> None:
        state = self._require_pending_state()
        if self._base_generation is None:
            raise UnitOfWorkStateError("The unit of work is not active.")
        self._store.publish(state, base_generation=self._base_generation)
        self._completed = True

    def rollback(self) -> None:
        self._require_pending_state()
        self._completed = True

    def _require_pending_state(self) -> _MemoryState:
        if self._state is None or self._completed:
            raise UnitOfWorkStateError("The unit of work is not pending.")
        return self._state
