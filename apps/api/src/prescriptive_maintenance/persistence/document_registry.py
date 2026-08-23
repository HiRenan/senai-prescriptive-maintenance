"""PostgreSQL adapter for governed document registration and lifecycle CAS."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import NoReturn

from psycopg import Connection, Error
from psycopg.rows import dict_row

from prescriptive_maintenance.contracts import DocumentStatus
from prescriptive_maintenance.document_lifecycle import (
    DocumentLifecycleError,
    DocumentSnapshot,
    DocumentVersion,
    GovernedDocument,
    LifecycleAction,
    LifecycleEvent,
    ProcessingFailure,
    ProcessingIntegrity,
    ProcessingStep,
    ProcessingStepStatus,
    build_document_snapshot_after_compare_and_swap,
)
from prescriptive_maintenance.document_registry import (
    DocumentRegistration,
    DocumentRegistrationSnapshot,
    DocumentRegistryIntegrityError,
    DocumentRegistryUnavailableError,
    LocatedDocumentRegistration,
    RegisteredDocumentVersion,
    validate_registration_metadata_update,
)
from prescriptive_maintenance.persistence.migrations import (
    PostgresConnection,
    PostgresRow,
)
from prescriptive_maintenance.persistence.models import (
    DocumentMetadata,
    DocumentVersionMetadata,
)
from prescriptive_maintenance.persistence.ports import PersistenceError
from prescriptive_maintenance.persistence.postgres import (
    PostgresConnectionFactory,
    PostgresDocumentRepository,
)

_READ_ONLY_TRANSACTION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
_REGISTRY_SELECT = """
SELECT
    logical_document_id,
    canonical_filename,
    display_filename,
    revision,
    current_version
FROM document_lifecycle_registries
WHERE logical_document_id = %s
"""
_REGISTRY_SELECT_FOR_UPDATE = f"{_REGISTRY_SELECT} FOR UPDATE"
_VERSION_SELECT = """
SELECT
    lifecycle.version_number,
    lifecycle.document_id,
    lifecycle.document_version_id,
    lifecycle.media_type,
    lifecycle.size_bytes,
    lifecycle.status,
    lifecycle.extraction_status,
    lifecycle.indexing_status,
    lifecycle.updated_at,
    lifecycle.failure_step,
    lifecycle.failure_code,
    lifecycle.failure_reason,
    lifecycle.failure_actor,
    lifecycle.failure_occurred_at,
    lifecycle.superseded_by_version,
    metadata.source_sha256,
    metadata.created_at
FROM document_lifecycle_versions AS lifecycle
JOIN document_versions AS metadata
  ON metadata.document_id = lifecycle.logical_document_id
 AND metadata.document_version_id = lifecycle.document_version_id
WHERE lifecycle.logical_document_id = %s
ORDER BY lifecycle.version_number
"""
_EVENT_SELECT = """
SELECT
    sequence,
    version_number,
    action,
    source_status,
    target_status,
    occurred_at,
    actor,
    reason,
    step,
    failure_code
FROM document_lifecycle_events
WHERE logical_document_id = %s
ORDER BY sequence
"""


def build_postgres_connection_factory(
    database_url: str,
) -> PostgresConnectionFactory:
    """Build short-lived psycopg connections without opening one eagerly."""

    if type(database_url) is not str or not database_url:
        raise ValueError("PostgreSQL document registry requires a database URL.")
    safe_database_url = str.__add__("", database_url)

    def connect() -> PostgresConnection:
        connection = Connection[PostgresRow].connect(
            safe_database_url,
            row_factory=dict_row,
        )
        connection.autocommit = False
        return connection

    return connect


class PostgresDocumentRegistryRepository:
    """Reconstruct and validate the aggregate around every transactional CAS."""

    def __init__(self, connection_factory: PostgresConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("PostgreSQL connection factory must be callable.")
        self._connection_factory = connection_factory

    def get(self, identity: str) -> DocumentSnapshot | None:
        registration = self.get_registration(identity)
        return None if registration is None else registration.domain_snapshot

    def list(self) -> tuple[DocumentSnapshot, ...]:
        return tuple(snapshot.domain_snapshot for snapshot in self.list_registrations())

    def compare_and_swap(
        self,
        document: GovernedDocument,
        *,
        expected_revision: int,
    ) -> DocumentSnapshot:
        connection = self._connect()
        try:
            with connection.transaction():
                _lock_registration(connection, document.identity)
                current = _load_registration(
                    connection,
                    document.identity,
                    for_update=True,
                )
                if current is None:
                    raise DocumentRegistryIntegrityError(
                        "Lifecycle transitions require registered metadata."
                    )
                domain_snapshot = build_document_snapshot_after_compare_and_swap(
                    document,
                    current=current.domain_snapshot,
                    expected_revision=expected_revision,
                )
                candidate = replace(current.registration, document=document)
                _write_registration(
                    connection,
                    candidate,
                    current=current,
                    revision=domain_snapshot.revision,
                )
            return domain_snapshot
        except Exception as error:
            _raise_postgres_registry_error(error)
        finally:
            connection.close()

    def get_registration(
        self,
        identity: str,
    ) -> DocumentRegistrationSnapshot | None:
        connection = self._connect()
        try:
            with connection.transaction():
                connection.execute(_READ_ONLY_TRANSACTION)
                return _load_registration(connection, identity, for_update=False)
        except Exception as error:
            _raise_postgres_registry_error(error)
        finally:
            connection.close()

    def find_registration(
        self,
        document_id: str,
    ) -> LocatedDocumentRegistration | None:
        connection = self._connect()
        try:
            with connection.transaction():
                connection.execute(_READ_ONLY_TRANSACTION)
                row = connection.execute(
                    """
                    SELECT logical_document_id, version_number
                    FROM document_lifecycle_versions
                    WHERE document_id = %s
                    """,
                    (document_id,),
                ).fetchone()
                if row is None:
                    return None
                identity = _required_text(row, "logical_document_id")
                version = _required_int(row, "version_number")
                snapshot = _load_registration(
                    connection,
                    identity,
                    for_update=False,
                )
                if snapshot is None:
                    raise DocumentRegistryIntegrityError(
                        "Document resource references missing registration metadata."
                    )
                return LocatedDocumentRegistration(
                    snapshot=snapshot,
                    version=version,
                )
        except Exception as error:
            _raise_postgres_registry_error(error)
        finally:
            connection.close()

    def list_registrations(self) -> tuple[DocumentRegistrationSnapshot, ...]:
        connection = self._connect()
        try:
            with connection.transaction():
                connection.execute(_READ_ONLY_TRANSACTION)
                rows = connection.execute(
                    """
                    SELECT logical_document_id
                    FROM document_lifecycle_registries
                    ORDER BY logical_document_id
                    """
                ).fetchall()
                registrations: list[DocumentRegistrationSnapshot] = []
                for row in rows:
                    identity = _required_text(row, "logical_document_id")
                    snapshot = _load_registration(
                        connection,
                        identity,
                        for_update=False,
                    )
                    if snapshot is None:
                        raise DocumentRegistryIntegrityError(
                            "Listed registration metadata could not be reconstructed."
                        )
                    registrations.append(snapshot)
                return tuple(registrations)
        except Exception as error:
            _raise_postgres_registry_error(error)
        finally:
            connection.close()

    def compare_and_swap_registration(
        self,
        registration: DocumentRegistration,
        *,
        expected_revision: int,
    ) -> DocumentRegistrationSnapshot:
        connection = self._connect()
        try:
            with connection.transaction():
                identity = registration.document.identity
                _lock_registration(connection, identity)
                current = _load_registration(
                    connection,
                    identity,
                    for_update=True,
                )
                domain_snapshot = build_document_snapshot_after_compare_and_swap(
                    registration.document,
                    current=None if current is None else current.domain_snapshot,
                    expected_revision=expected_revision,
                )
                validate_registration_metadata_update(current, registration)
                _write_traceable_document_metadata(
                    connection,
                    registration,
                    current=current,
                )
                _write_registration(
                    connection,
                    registration,
                    current=current,
                    revision=domain_snapshot.revision,
                )
                snapshot = DocumentRegistrationSnapshot(
                    registration=registration,
                    revision=domain_snapshot.revision,
                )
            return snapshot
        except Exception as error:
            _raise_postgres_registry_error(error)
        finally:
            connection.close()

    def _connect(self) -> PostgresConnection:
        try:
            connection = self._connection_factory()
        except Exception:
            raise DocumentRegistryUnavailableError(
                "PostgreSQL document registry is unavailable."
            ) from None
        if connection.autocommit:
            connection.close()
            raise DocumentRegistryUnavailableError(
                "PostgreSQL document registry connection is invalid."
            )
        return connection


def _load_registration(
    connection: PostgresConnection,
    identity: str,
    *,
    for_update: bool,
) -> DocumentRegistrationSnapshot | None:
    registry_row = connection.execute(
        _REGISTRY_SELECT_FOR_UPDATE if for_update else _REGISTRY_SELECT,
        (identity,),
    ).fetchone()
    if registry_row is None:
        return None
    version_rows = connection.execute(_VERSION_SELECT, (identity,)).fetchall()
    event_rows = connection.execute(_EVENT_SELECT, (identity,)).fetchall()
    try:
        versions = tuple(_domain_version(row) for row in version_rows)
        events = tuple(_lifecycle_event(row, identity=identity) for row in event_rows)
        document = GovernedDocument(
            identity=_required_text(registry_row, "logical_document_id"),
            versions=versions,
            current_version=_optional_int(registry_row, "current_version"),
            history=events,
        )
        registration = DocumentRegistration(
            canonical_filename=_required_text(registry_row, "canonical_filename"),
            display_filename=_required_text(registry_row, "display_filename"),
            document=document,
            versions=tuple(_registered_version(row) for row in version_rows),
        )
        return DocumentRegistrationSnapshot(
            registration=registration,
            revision=_required_int(registry_row, "revision"),
        )
    except DocumentRegistryIntegrityError:
        raise
    except DocumentLifecycleError:
        raise DocumentRegistryIntegrityError(
            "Persisted document registration violates lifecycle invariants."
        ) from None
    except Exception:
        raise DocumentRegistryIntegrityError(
            "Persisted document registration could not be reconstructed."
        ) from None


def _domain_version(row: PostgresRow) -> DocumentVersion:
    failure_step = _optional_text(row, "failure_step")
    if failure_step is None:
        failure = None
    else:
        failure = ProcessingFailure(
            step=ProcessingStep(failure_step),
            code=_required_text(row, "failure_code"),
            reason=_required_text(row, "failure_reason"),
            actor=_required_text(row, "failure_actor"),
            occurred_at=_required_datetime(row, "failure_occurred_at"),
        )
    return DocumentVersion(
        number=_required_int(row, "version_number"),
        sha256=_required_text(row, "source_sha256"),
        status=DocumentStatus(_required_text(row, "status")),
        integrity=ProcessingIntegrity(
            extraction=ProcessingStepStatus(_required_text(row, "extraction_status")),
            indexing=ProcessingStepStatus(_required_text(row, "indexing_status")),
        ),
        received_at=_required_datetime(row, "created_at"),
        updated_at=_required_datetime(row, "updated_at"),
        failure=failure,
        superseded_by_version=_optional_int(row, "superseded_by_version"),
    )


def _registered_version(row: PostgresRow) -> RegisteredDocumentVersion:
    return RegisteredDocumentVersion(
        number=_required_int(row, "version_number"),
        document_id=_required_text(row, "document_id"),
        document_version_id=_required_text(row, "document_version_id"),
        media_type=_required_text(row, "media_type"),
        size_bytes=_required_int(row, "size_bytes"),
    )


def _lifecycle_event(row: PostgresRow, *, identity: str) -> LifecycleEvent:
    source = _optional_text(row, "source_status")
    step = _optional_text(row, "step")
    return LifecycleEvent(
        sequence=_required_int(row, "sequence"),
        document_identity=identity,
        version=_required_int(row, "version_number"),
        action=LifecycleAction(_required_text(row, "action")),
        source_status=None if source is None else DocumentStatus(source),
        target_status=DocumentStatus(_required_text(row, "target_status")),
        occurred_at=_required_datetime(row, "occurred_at"),
        actor=_required_text(row, "actor"),
        reason=_optional_text(row, "reason"),
        step=None if step is None else ProcessingStep(step),
        failure_code=_optional_text(row, "failure_code"),
    )


def _write_traceable_document_metadata(
    connection: PostgresConnection,
    registration: DocumentRegistration,
    *,
    current: DocumentRegistrationSnapshot | None,
) -> None:
    repository = PostgresDocumentRepository(connection)
    first_new_version = 0 if current is None else len(current.registration.versions)
    if current is None:
        first = registration.document.versions[0]
        repository.add(
            DocumentMetadata(
                document_id=registration.document.identity,
                created_at=first.received_at,
                versions=(),
            )
        )
    for metadata, version in zip(
        registration.versions[first_new_version:],
        registration.document.versions[first_new_version:],
        strict=True,
    ):
        repository.add_version(
            DocumentVersionMetadata(
                document_version_id=metadata.document_version_id,
                document_id=registration.document.identity,
                source_sha256=version.sha256,
                created_at=version.received_at,
                chunks=(),
            )
        )


def _write_registration(
    connection: PostgresConnection,
    registration: DocumentRegistration,
    *,
    current: DocumentRegistrationSnapshot | None,
    revision: int,
) -> None:
    identity = registration.document.identity
    if current is None:
        connection.execute(
            """
            INSERT INTO document_lifecycle_registries (
                logical_document_id,
                canonical_filename,
                display_filename,
                revision,
                current_version
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                identity,
                registration.canonical_filename,
                registration.display_filename,
                revision,
                registration.document.current_version,
            ),
        )
        stored_version_count = 0
        stored_event_count = 0
    else:
        stored_version_count = len(current.registration.versions)
        stored_event_count = len(current.registration.document.history)

    for metadata in registration.versions[stored_version_count:]:
        version = registration.document.version(metadata.number)
        connection.execute(
            """
            INSERT INTO document_lifecycle_versions (
                logical_document_id,
                version_number,
                document_id,
                document_version_id,
                media_type,
                size_bytes,
                status,
                extraction_status,
                indexing_status,
                updated_at,
                failure_step,
                failure_code,
                failure_reason,
                failure_actor,
                failure_occurred_at,
                superseded_by_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _version_row_values(identity, metadata, version),
        )

    for metadata, version in zip(
        registration.versions,
        registration.document.versions,
        strict=True,
    ):
        values = _version_row_values(identity, metadata, version)
        connection.execute(
            """
            UPDATE document_lifecycle_versions
            SET
                media_type = %s,
                size_bytes = %s,
                status = %s,
                extraction_status = %s,
                indexing_status = %s,
                updated_at = %s,
                failure_step = %s,
                failure_code = %s,
                failure_reason = %s,
                failure_actor = %s,
                failure_occurred_at = %s,
                superseded_by_version = %s
            WHERE logical_document_id = %s AND version_number = %s
            """,
            (*values[4:], identity, version.number),
        )

    for event in registration.document.history[stored_event_count:]:
        connection.execute(
            """
            INSERT INTO document_lifecycle_events (
                logical_document_id,
                sequence,
                version_number,
                action,
                source_status,
                target_status,
                occurred_at,
                actor,
                reason,
                step,
                failure_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                identity,
                event.sequence,
                event.version,
                event.action.value,
                None if event.source_status is None else event.source_status.value,
                event.target_status.value,
                event.occurred_at,
                event.actor,
                event.reason,
                None if event.step is None else event.step.value,
                event.failure_code,
            ),
        )

    if current is not None:
        cursor = connection.execute(
            """
            UPDATE document_lifecycle_registries
            SET revision = %s, current_version = %s
            WHERE logical_document_id = %s AND revision = %s
            """,
            (
                revision,
                registration.document.current_version,
                identity,
                current.revision,
            ),
        )
        if cursor.rowcount != 1:
            raise DocumentRegistryIntegrityError(
                "PostgreSQL lifecycle revision update was not atomic."
            )


def _version_row_values(
    identity: str,
    metadata: RegisteredDocumentVersion,
    version: DocumentVersion,
) -> tuple[object, ...]:
    failure = version.failure
    return (
        identity,
        version.number,
        metadata.document_id,
        metadata.document_version_id,
        metadata.media_type,
        metadata.size_bytes,
        version.status.value,
        version.integrity.extraction.value,
        version.integrity.indexing.value,
        version.updated_at,
        None if failure is None else failure.step.value,
        None if failure is None else failure.code,
        None if failure is None else failure.reason,
        None if failure is None else failure.actor,
        None if failure is None else failure.occurred_at,
        version.superseded_by_version,
    )


def _lock_registration(connection: PostgresConnection, identity: str) -> None:
    lock_id = int.from_bytes(
        sha256(f"document-registry:{identity}".encode("ascii")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))


def _required_text(row: PostgresRow, key: str) -> str:
    value = row.get(key)
    if type(value) is not str:
        raise DocumentRegistryIntegrityError(
            "Persisted document text field is invalid."
        )
    return value


def _optional_text(row: PostgresRow, key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is not str:
        raise DocumentRegistryIntegrityError(
            "Persisted optional document text field is invalid."
        )
    return value


def _required_int(row: PostgresRow, key: str) -> int:
    value = row.get(key)
    if type(value) is not int:
        raise DocumentRegistryIntegrityError(
            "Persisted document integer field is invalid."
        )
    return value


def _optional_int(row: PostgresRow, key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise DocumentRegistryIntegrityError(
            "Persisted optional document integer field is invalid."
        )
    return value


def _required_datetime(row: PostgresRow, key: str) -> datetime:
    value = row.get(key)
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise DocumentRegistryIntegrityError("Persisted document timestamp is invalid.")
    return value.astimezone(UTC)


def _raise_postgres_registry_error(error: Exception) -> NoReturn:
    if isinstance(
        error,
        (DocumentLifecycleError, DocumentRegistryIntegrityError),
    ):
        raise error
    if isinstance(error, PersistenceError):
        raise DocumentRegistryIntegrityError(
            "Traceable document metadata conflicts with lifecycle persistence."
        ) from None
    if isinstance(error, Error):
        raise DocumentRegistryUnavailableError(
            "PostgreSQL document registry is unavailable."
        ) from None
    raise DocumentRegistryUnavailableError(
        "PostgreSQL document registry could not complete the operation."
    ) from None
