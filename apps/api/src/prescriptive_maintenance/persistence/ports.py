"""Typed persistence ports and transaction boundary."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from prescriptive_maintenance.persistence.models import (
    AnalysisMetadata,
    DocumentMetadata,
    DocumentVersionMetadata,
)


class PersistenceError(Exception):
    """Base class for sanitized persistence failures."""


class PersistenceConflictError(PersistenceError):
    """An identifier already represents different metadata."""


class PersistenceIntegrityError(PersistenceError):
    """A referenced metadata record does not exist or does not match."""


class TransactionConflictError(PersistenceError):
    """A concurrent in-memory transaction changed the committed state."""


class UnitOfWorkStateError(PersistenceError):
    """The unit of work was used outside its valid lifecycle."""


class AnalysisRepository(Protocol):
    """Persist and recover complete analysis metadata aggregates."""

    def add(self, analysis: AnalysisMetadata) -> None: ...

    def get(self, analysis_id: str) -> AnalysisMetadata | None: ...


class DocumentRepository(Protocol):
    """Persist and recover document metadata with all known versions."""

    def add(self, document: DocumentMetadata) -> None: ...

    def add_version(self, version: DocumentVersionMetadata) -> None: ...

    def get(self, document_id: str) -> DocumentMetadata | None: ...


class UnitOfWork(Protocol):
    """One explicit transaction across both repository boundaries."""

    @property
    def analyses(self) -> AnalysisRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
