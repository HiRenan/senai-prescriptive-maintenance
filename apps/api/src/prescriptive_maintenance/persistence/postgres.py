"""Psycopg repositories and unit of work for optional PostgreSQL integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import NoReturn, cast

from psycopg import IntegrityError
from psycopg.errors import UniqueViolation

from prescriptive_maintenance.contracts import AnalysisOutcome
from prescriptive_maintenance.persistence.migrations import PostgresConnection
from prescriptive_maintenance.persistence.models import (
    AnalysisMetadata,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
)
from prescriptive_maintenance.persistence.ports import (
    AnalysisRepository,
    DocumentRepository,
    PersistenceConflictError,
    PersistenceIntegrityError,
    UnitOfWorkStateError,
)

type PostgresConnectionFactory = Callable[[], PostgresConnection]


class PostgresDocumentRepository(DocumentRepository):
    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add(self, document: DocumentMetadata) -> None:
        existing = self.get(document.document_id)
        if existing is not None:
            if existing == document:
                return
            raise PersistenceConflictError(
                "Document identifier already represents different metadata."
            )
        try:
            self._connection.execute(
                "INSERT INTO documents (document_id, created_at) VALUES (%s, %s)",
                (document.document_id, document.created_at),
            )
            for version in document.versions:
                self._insert_version(version)
        except UniqueViolation:
            _raise_conflict()
        except IntegrityError:
            _raise_integrity()

    def add_version(self, version: DocumentVersionMetadata) -> None:
        document = self.get(version.document_id)
        if document is None:
            raise PersistenceIntegrityError(
                "A document version requires an existing document."
            )

        for stored_version in document.versions:
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

        try:
            self._insert_version(version)
        except UniqueViolation:
            _raise_conflict()
        except IntegrityError:
            _raise_integrity()

    def _insert_version(self, version: DocumentVersionMetadata) -> None:
        self._connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id,
                document_id,
                source_sha256,
                created_at
            ) VALUES (%s, %s, %s, %s)
            """,
            (
                version.document_version_id,
                version.document_id,
                version.source_sha256,
                version.created_at,
            ),
        )
        for chunk in version.chunks:
            self._connection.execute(
                """
                INSERT INTO chunk_references (
                    chunk_ref,
                    document_id,
                    document_version_id,
                    page_number
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    chunk.chunk_ref,
                    chunk.document_id,
                    chunk.document_version_id,
                    chunk.page_number,
                ),
            )

    def get(self, document_id: str) -> DocumentMetadata | None:
        document_row = self._connection.execute(
            """
            SELECT document_id, created_at
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,),
        ).fetchone()
        if document_row is None:
            return None

        version_rows = self._connection.execute(
            """
            SELECT document_version_id, document_id, source_sha256, created_at
            FROM document_versions
            WHERE document_id = %s
            ORDER BY document_version_id
            """,
            (document_id,),
        ).fetchall()
        chunk_rows = self._connection.execute(
            """
            SELECT chunk_ref, document_id, document_version_id, page_number
            FROM chunk_references
            WHERE document_id = %s
            ORDER BY document_version_id, chunk_ref
            """,
            (document_id,),
        ).fetchall()

        chunks_by_version: dict[str, list[ChunkReference]] = {}
        for row in chunk_rows:
            version_id = cast(str, row["document_version_id"])
            chunks_by_version.setdefault(version_id, []).append(
                ChunkReference(
                    chunk_ref=cast(str, row["chunk_ref"]),
                    document_id=cast(str, row["document_id"]),
                    document_version_id=version_id,
                    page_number=cast(int, row["page_number"]),
                )
            )
        versions = tuple(
            DocumentVersionMetadata(
                document_version_id=cast(str, row["document_version_id"]),
                document_id=cast(str, row["document_id"]),
                source_sha256=cast(str, row["source_sha256"]),
                created_at=cast(datetime, row["created_at"]),
                chunks=tuple(
                    chunks_by_version.get(
                        cast(str, row["document_version_id"]),
                        (),
                    )
                ),
            )
            for row in version_rows
        )
        return DocumentMetadata(
            document_id=cast(str, document_row["document_id"]),
            created_at=cast(datetime, document_row["created_at"]),
            versions=versions,
        )


class PostgresAnalysisRepository(AnalysisRepository):
    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add(self, analysis: AnalysisMetadata) -> None:
        existing = self.get(analysis.analysis_id)
        if existing is not None:
            if existing == analysis:
                return
            raise PersistenceConflictError(
                "Analysis identifier already represents different metadata."
            )
        try:
            self._connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id,
                    outcome,
                    dataset_id,
                    model_id,
                    prompt_id,
                    configuration_id,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    analysis.analysis_id,
                    analysis.outcome.value,
                    analysis.dataset_id,
                    analysis.model_id,
                    analysis.prompt_id,
                    analysis.configuration_id,
                    analysis.created_at,
                ),
            )
            for reference in analysis.evidence_references:
                self._connection.execute(
                    """
                    INSERT INTO evidence_references (
                        evidence_id,
                        analysis_id,
                        document_id,
                        document_version_id,
                        chunk_ref,
                        ordinal
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reference.evidence_id,
                        analysis.analysis_id,
                        reference.document_id,
                        reference.document_version_id,
                        reference.chunk_ref,
                        reference.ordinal,
                    ),
                )
        except UniqueViolation:
            _raise_conflict()
        except IntegrityError:
            _raise_integrity()

    def get(self, analysis_id: str) -> AnalysisMetadata | None:
        analysis_row = self._connection.execute(
            """
            SELECT
                analysis_id,
                outcome,
                dataset_id,
                model_id,
                prompt_id,
                configuration_id,
                created_at
            FROM analyses
            WHERE analysis_id = %s
            """,
            (analysis_id,),
        ).fetchone()
        if analysis_row is None:
            return None

        evidence_rows = self._connection.execute(
            """
            SELECT
                evidence_id,
                document_id,
                document_version_id,
                chunk_ref,
                ordinal
            FROM evidence_references
            WHERE analysis_id = %s
            ORDER BY ordinal
            """,
            (analysis_id,),
        ).fetchall()
        references = tuple(
            EvidenceReference(
                evidence_id=cast(str, row["evidence_id"]),
                document_id=cast(str, row["document_id"]),
                document_version_id=cast(str, row["document_version_id"]),
                chunk_ref=cast(str, row["chunk_ref"]),
                ordinal=cast(int, row["ordinal"]),
            )
            for row in evidence_rows
        )
        return AnalysisMetadata(
            analysis_id=cast(str, analysis_row["analysis_id"]),
            outcome=AnalysisOutcome(cast(str, analysis_row["outcome"])),
            dataset_id=cast(str, analysis_row["dataset_id"]),
            model_id=cast(str, analysis_row["model_id"]),
            prompt_id=cast(str, analysis_row["prompt_id"]),
            configuration_id=cast(str, analysis_row["configuration_id"]),
            created_at=cast(datetime, analysis_row["created_at"]),
            evidence_references=references,
        )


def _raise_conflict() -> NoReturn:
    raise PersistenceConflictError(
        "A traceable identifier is already in use."
    ) from None


def _raise_integrity() -> NoReturn:
    raise PersistenceIntegrityError(
        "Persisted metadata violates a relational constraint."
    ) from None


class PostgresUnitOfWork:
    """Own one psycopg connection and one explicit database transaction."""

    def __init__(self, connection_factory: PostgresConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection: PostgresConnection | None = None
        self._analyses: PostgresAnalysisRepository | None = None
        self._documents: PostgresDocumentRepository | None = None
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

    def __enter__(self) -> PostgresUnitOfWork:
        if self._connection is not None:
            raise UnitOfWorkStateError("The unit of work is already active.")
        connection = self._connection_factory()
        if connection.autocommit:
            connection.close()
            raise UnitOfWorkStateError(
                "PostgreSQL units of work require autocommit to be disabled."
            )
        self._connection = connection
        self._analyses = PostgresAnalysisRepository(connection)
        self._documents = PostgresDocumentRepository(connection)
        self._completed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        connection = self._connection
        if connection is None:
            return
        try:
            if not self._completed:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None
            self._analyses = None
            self._documents = None

    def commit(self) -> None:
        connection = self._require_pending_connection()
        connection.commit()
        self._completed = True

    def rollback(self) -> None:
        connection = self._require_pending_connection()
        connection.rollback()
        self._completed = True

    def _require_pending_connection(self) -> PostgresConnection:
        if self._connection is None or self._completed:
            raise UnitOfWorkStateError("The unit of work is not pending.")
        return self._connection
