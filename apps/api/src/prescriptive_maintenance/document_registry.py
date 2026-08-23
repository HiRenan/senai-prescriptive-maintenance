"""Honest metadata registration projected through the frozen document API v1."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock
from typing import Final, Literal, NoReturn, Protocol, TypedDict, cast

from prescriptive_maintenance.contracts import (
    ApprovedDocument,
    ApproveDocumentRequest,
    Document,
    DocumentFailure,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    FailedDocument,
    PendingApprovalDocument,
    ProcessingDocument,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
    RejectedDocument,
    SupersededDocument,
)
from prescriptive_maintenance.document_lifecycle import (
    Clock,
    DocumentApprovalBlockedError,
    DocumentAuditConflictError,
    DocumentClockError,
    DocumentConcurrencyError,
    DocumentContentConflictError,
    DocumentGovernanceService,
    DocumentSnapshot,
    DocumentVersionConflictError,
    GovernedDocument,
    InvalidDocumentInputError,
    LifecycleAction,
    SystemUtcClock,
    build_document_snapshot_after_compare_and_swap,
    is_document_snapshot_audited,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentNotFoundError as DomainDocumentNotFoundError,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentRepository as GovernanceRepository,
)
from prescriptive_maintenance.document_lifecycle import (
    InvalidDocumentTransitionError as DomainInvalidDocumentTransitionError,
)
from prescriptive_maintenance.services import (
    DocumentConflictError,
    DocumentNotFoundError,
    DocumentServiceUnavailableError,
    InvalidDocumentRequestError,
    InvalidDocumentTransitionError,
)
from prescriptive_maintenance.settings import Settings

_PDF_FILENAME_PATTERN: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\.[Pp][Dd][Ff]"
)
_DOCUMENT_ID_PATTERN: Final = re.compile(r"doc_[a-z0-9_]{3,64}")
_DOCUMENT_VERSION_ID_PATTERN: Final = re.compile(r"docver_[a-z0-9_]{3,64}")
_MAX_DOCUMENT_SIZE_BYTES: Final = 25_000_000
_REGISTRATION_ACTOR: Final = "api.v1.document_registry"
_APPROVAL_WITHOUT_NOTE_REASON: Final = (
    "Aprovação técnica registrada pela API v1 sem nota do operador."
)
_FAILURE_CODE: Final = "document_processing_failed"
_FAILURE_MESSAGE: Final = "O processamento documental não foi concluído."


class DocumentRegistryError(Exception):
    """Base class for sanitized registry adapter failures."""


class DocumentRegistryUnavailableError(DocumentRegistryError):
    """The selected registry adapter could not complete an operation."""


class DocumentRegistryIntegrityError(DocumentRegistryError):
    """Persisted registration metadata does not reconstruct safely."""


@dataclass(frozen=True, slots=True)
class RegisteredDocumentVersion:
    """Transport metadata for one governed version, without document bytes."""

    number: int
    document_id: str
    document_version_id: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise DocumentRegistryIntegrityError(
                "Registered document version number is invalid."
            )
        if (
            type(self.document_id) is not str
            or _DOCUMENT_ID_PATTERN.fullmatch(self.document_id) is None
        ):
            raise DocumentRegistryIntegrityError(
                "Registered document resource identifier is invalid."
            )
        if (
            type(self.document_version_id) is not str
            or _DOCUMENT_VERSION_ID_PATTERN.fullmatch(self.document_version_id) is None
        ):
            raise DocumentRegistryIntegrityError(
                "Registered document version identifier is invalid."
            )
        if type(self.media_type) is not str or self.media_type != "application/pdf":
            raise DocumentRegistryIntegrityError(
                "Registered document media type is invalid."
            )
        if (
            type(self.size_bytes) is not int
            or not 1 <= self.size_bytes <= _MAX_DOCUMENT_SIZE_BYTES
        ):
            raise DocumentRegistryIntegrityError("Registered document size is invalid.")


@dataclass(frozen=True, slots=True)
class DocumentRegistration:
    """One logical filename and all metadata-only governed versions."""

    canonical_filename: str
    display_filename: str
    document: GovernedDocument
    versions: tuple[RegisteredDocumentVersion, ...]

    def __post_init__(self) -> None:
        canonical_filename = canonical_pdf_filename(self.canonical_filename)
        if canonical_filename != self.canonical_filename:
            raise DocumentRegistryIntegrityError(
                "Stored canonical document filename is invalid."
            )
        if canonical_pdf_filename(self.display_filename) != canonical_filename:
            raise DocumentRegistryIntegrityError(
                "Stored display filename does not match its logical identity."
            )
        if type(self.document) is not GovernedDocument:
            raise DocumentRegistryIntegrityError(
                "Stored document aggregate is not canonical."
            )
        if self.document.identity != logical_document_id(canonical_filename):
            raise DocumentRegistryIntegrityError(
                "Stored document identity does not match its canonical filename."
            )
        if type(self.versions) is not tuple or any(
            type(version) is not RegisteredDocumentVersion for version in self.versions
        ):
            raise DocumentRegistryIntegrityError(
                "Stored registration versions are not canonical."
            )
        if len(self.versions) != len(self.document.versions):
            raise DocumentRegistryIntegrityError(
                "Stored registration metadata does not cover every domain version."
            )
        for metadata, domain_version in zip(
            self.versions,
            self.document.versions,
            strict=True,
        ):
            if metadata.number != domain_version.number:
                raise DocumentRegistryIntegrityError(
                    "Stored registration version order is invalid."
                )
            if metadata.document_id != version_document_id(
                self.document.identity,
                number=domain_version.number,
                source_sha256=domain_version.sha256,
            ):
                raise DocumentRegistryIntegrityError(
                    "Stored document resource identifier is inconsistent."
                )
            if metadata.document_version_id != persistence_document_version_id(
                self.document.identity,
                number=domain_version.number,
                source_sha256=domain_version.sha256,
            ):
                raise DocumentRegistryIntegrityError(
                    "Stored document version identifier is inconsistent."
                )


@dataclass(frozen=True, slots=True)
class DocumentRegistrationSnapshot:
    """Registration metadata paired with the domain CAS revision."""

    registration: DocumentRegistration
    revision: int

    def __post_init__(self) -> None:
        if type(self.registration) is not DocumentRegistration:
            raise DocumentRegistryIntegrityError(
                "Stored document registration is not canonical."
            )
        snapshot = DocumentSnapshot(
            document=self.registration.document,
            revision=self.revision,
        )
        if not is_document_snapshot_audited(snapshot):
            raise DocumentRegistryIntegrityError(
                "Stored document registration is not fully audited."
            )

    @property
    def domain_snapshot(self) -> DocumentSnapshot:
        return DocumentSnapshot(
            document=self.registration.document,
            revision=self.revision,
        )


@dataclass(frozen=True, slots=True)
class LocatedDocumentRegistration:
    """One version resource resolved to its complete logical aggregate."""

    snapshot: DocumentRegistrationSnapshot
    version: int

    def __post_init__(self) -> None:
        if type(self.snapshot) is not DocumentRegistrationSnapshot:
            raise DocumentRegistryIntegrityError(
                "Located document registration is not canonical."
            )
        if type(self.version) is not int or not (
            1 <= self.version <= len(self.snapshot.registration.versions)
        ):
            raise DocumentRegistryIntegrityError("Located document version is invalid.")


class DocumentRegistryRepository(GovernanceRepository, Protocol):
    """CAS repository for metadata registration and lifecycle transitions."""

    def get_registration(
        self,
        identity: str,
    ) -> DocumentRegistrationSnapshot | None: ...

    def find_registration(
        self,
        document_id: str,
    ) -> LocatedDocumentRegistration | None: ...

    def list_registrations(self) -> tuple[DocumentRegistrationSnapshot, ...]: ...

    def compare_and_swap_registration(
        self,
        registration: DocumentRegistration,
        *,
        expected_revision: int,
    ) -> DocumentRegistrationSnapshot: ...


class InMemoryDocumentRegistryRepository:
    """Thread-safe metadata registry with the same CAS semantics as PostgreSQL."""

    def __init__(self) -> None:
        self._registrations: dict[str, DocumentRegistrationSnapshot] = {}
        self._document_index: dict[str, tuple[str, int]] = {}
        self._lock = RLock()

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
        with self._lock:
            current = self._registrations.get(document.identity)
            if current is None:
                raise DocumentAuditConflictError(
                    "Lifecycle transitions require registered metadata."
                )
            domain_snapshot = build_document_snapshot_after_compare_and_swap(
                document,
                current=current.domain_snapshot,
                expected_revision=expected_revision,
            )
            updated = DocumentRegistrationSnapshot(
                registration=replace(current.registration, document=document),
                revision=domain_snapshot.revision,
            )
            self._registrations[document.identity] = updated
            return domain_snapshot

    def get_registration(
        self,
        identity: str,
    ) -> DocumentRegistrationSnapshot | None:
        with self._lock:
            return self._registrations.get(identity)

    def find_registration(
        self,
        document_id: str,
    ) -> LocatedDocumentRegistration | None:
        with self._lock:
            location = self._document_index.get(document_id)
            if location is None:
                return None
            identity, version = location
            snapshot = self._registrations.get(identity)
            if snapshot is None:
                raise DocumentRegistryIntegrityError(
                    "Document resource index references missing metadata."
                )
            return LocatedDocumentRegistration(snapshot=snapshot, version=version)

    def list_registrations(self) -> tuple[DocumentRegistrationSnapshot, ...]:
        with self._lock:
            return tuple(
                self._registrations[key] for key in sorted(self._registrations)
            )

    def compare_and_swap_registration(
        self,
        registration: DocumentRegistration,
        *,
        expected_revision: int,
    ) -> DocumentRegistrationSnapshot:
        with self._lock:
            current = self._registrations.get(registration.document.identity)
            domain_snapshot = build_document_snapshot_after_compare_and_swap(
                registration.document,
                current=None if current is None else current.domain_snapshot,
                expected_revision=expected_revision,
            )
            validate_registration_metadata_update(current, registration)
            for metadata in registration.versions:
                indexed = self._document_index.get(metadata.document_id)
                expected = (registration.document.identity, metadata.number)
                if indexed is not None and indexed != expected:
                    raise DocumentRegistryIntegrityError(
                        "Document resource identifier is already in use."
                    )
            snapshot = DocumentRegistrationSnapshot(
                registration=registration,
                revision=domain_snapshot.revision,
            )
            self._registrations[registration.document.identity] = snapshot
            for metadata in registration.versions:
                self._document_index[metadata.document_id] = (
                    registration.document.identity,
                    metadata.number,
                )
            return snapshot


class GovernedDocumentLifecycleService:
    """Expose metadata-only registration through the governed domain aggregate."""

    def __init__(
        self,
        *,
        repository: DocumentRegistryRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._governance = DocumentGovernanceService(
            repository=repository,
            clock=clock,
        )

    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument:
        request = _validated_registration_request(request)
        try:
            canonical_filename = canonical_pdf_filename(request.filename)
            identity = logical_document_id(canonical_filename)
            current = self._repository.get_registration(identity)
            if current is not None:
                replay = _matching_registration(current, request)
                if replay is not None:
                    return _received_projection(current, replay)
                number = len(current.registration.versions) + 1
                document = current.registration.document.register_version(
                    version=number,
                    sha256=request.sha256,
                    actor=_REGISTRATION_ACTOR,
                    occurred_at=self._utc_now(),
                )
                registration = replace(
                    current.registration,
                    document=document,
                    versions=(
                        *current.registration.versions,
                        _version_metadata(document, number, request),
                    ),
                )
                expected_revision = current.revision
            else:
                number = 1
                document = GovernedDocument.register(
                    identity=identity,
                    version=number,
                    sha256=request.sha256,
                    actor=_REGISTRATION_ACTOR,
                    occurred_at=self._utc_now(),
                )
                registration = DocumentRegistration(
                    canonical_filename=canonical_filename,
                    display_filename=_base_text(request.filename),
                    document=document,
                    versions=(_version_metadata(document, number, request),),
                )
                expected_revision = 0
            try:
                stored = self._repository.compare_and_swap_registration(
                    registration,
                    expected_revision=expected_revision,
                )
            except DocumentConcurrencyError:
                raced = self._repository.get_registration(identity)
                if raced is not None:
                    replay = _matching_registration(raced, request)
                    if replay is not None:
                        return _received_projection(raced, replay)
                raise
            return _received_projection(stored, number)
        except DomainInvalidDocumentTransitionError:
            raise DocumentConflictError(
                "Document registration conflicts with the active candidate."
            ) from None
        except Exception as error:
            _raise_mapped_document_error(error)

    def list(self) -> DocumentListResponse:
        try:
            documents = [
                _document_projection(snapshot, version.number)
                for snapshot in self._repository.list_registrations()
                for version in snapshot.registration.versions
            ]
            documents.sort(key=lambda document: document.document_id)
            return DocumentListResponse(items=tuple(documents))
        except Exception as error:
            _raise_mapped_document_error(error)

    def get(self, document_id: str) -> DocumentResponse:
        located = self._located(document_id)
        try:
            return DocumentResponse(
                root=_document_projection(located.snapshot, located.version)
            )
        except Exception as error:
            _raise_mapped_document_error(error)

    def approve(
        self,
        document_id: str,
        request: ApproveDocumentRequest,
    ) -> ApprovedDocument:
        request = _validated_approval_request(request)
        located = self._located(document_id)
        reason = request.note or _APPROVAL_WITHOUT_NOTE_REASON
        expected_revision = _expected_revision_for_replay(
            located,
            target=DocumentStatus.APPROVED,
            action=LifecycleAction.APPROVED,
        )
        try:
            updated = self._governance.approve(
                identity=located.snapshot.registration.document.identity,
                version=located.version,
                actor=_REGISTRATION_ACTOR,
                reason=reason,
                expected_revision=expected_revision,
            )
            projected = _project_updated_snapshot(located, updated)
            if type(projected) is not ApprovedDocument:
                raise DocumentRegistryIntegrityError(
                    "Approved lifecycle result has an invalid public projection."
                )
            return projected
        except Exception as error:
            _raise_mapped_document_error(error)

    def reject(
        self,
        document_id: str,
        request: RejectDocumentRequest,
    ) -> RejectedDocument:
        request = _validated_rejection_request(request)
        located = self._located(document_id)
        expected_revision = _expected_revision_for_replay(
            located,
            target=DocumentStatus.REJECTED,
            action=LifecycleAction.REJECTED,
        )
        try:
            updated = self._governance.reject(
                identity=located.snapshot.registration.document.identity,
                version=located.version,
                actor=_REGISTRATION_ACTOR,
                reason=request.reason,
                expected_revision=expected_revision,
            )
            projected = _project_updated_snapshot(located, updated)
            if type(projected) is not RejectedDocument:
                raise DocumentRegistryIntegrityError(
                    "Rejected lifecycle result has an invalid public projection."
                )
            return projected
        except Exception as error:
            _raise_mapped_document_error(error)

    def reprocess(self, document_id: str) -> ProcessingDocument:
        located = self._located(document_id)
        expected_revision = _expected_revision_for_replay(
            located,
            target=DocumentStatus.PROCESSING,
            action=LifecycleAction.REPROCESSING_STARTED,
        )
        version = located.snapshot.registration.document.version(located.version)
        try:
            updated = self._governance.reprocess(
                identity=located.snapshot.registration.document.identity,
                version=located.version,
                sha256=version.sha256,
                actor=_REGISTRATION_ACTOR,
                expected_revision=expected_revision,
            )
            projected = _project_updated_snapshot(located, updated)
            if type(projected) is not ProcessingDocument:
                raise DocumentRegistryIntegrityError(
                    "Reprocessed lifecycle result has an invalid public projection."
                )
            return projected
        except Exception as error:
            _raise_mapped_document_error(error)

    def _located(self, document_id: str) -> LocatedDocumentRegistration:
        try:
            located = self._repository.find_registration(document_id)
        except Exception as error:
            _raise_mapped_document_error(error)
        if located is None:
            raise DocumentNotFoundError("Document was not found.")
        return located

    def _utc_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DocumentClockError("Document lifecycle clock must be timezone-aware.")
        return value.astimezone(UTC)


class RuntimeDocumentLifecycleService:
    """Lifespan-configured facade selecting the declared persistence backend."""

    def __init__(self) -> None:
        self._delegate: GovernedDocumentLifecycleService | None = None

    def configure(self, settings: Settings) -> None:
        if type(settings) is not Settings:
            raise TypeError("Document service settings must use the canonical type.")
        if settings.persistence_backend == "memory":
            repository: DocumentRegistryRepository = (
                InMemoryDocumentRegistryRepository()
            )
        else:
            database_url = settings.database_url
            if database_url is None:
                raise ValueError("PostgreSQL backend requires a database URL.")
            from prescriptive_maintenance.persistence.document_registry import (
                PostgresDocumentRegistryRepository,
                build_postgres_connection_factory,
            )

            repository = PostgresDocumentRegistryRepository(
                build_postgres_connection_factory(str(database_url))
            )
        self._delegate = GovernedDocumentLifecycleService(
            repository=repository,
            clock=SystemUtcClock(),
        )

    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument:
        return self._configured().register(request)

    def list(self) -> DocumentListResponse:
        return self._configured().list()

    def get(self, document_id: str) -> DocumentResponse:
        return self._configured().get(document_id)

    def approve(
        self,
        document_id: str,
        request: ApproveDocumentRequest,
    ) -> ApprovedDocument:
        return self._configured().approve(document_id, request)

    def reject(
        self,
        document_id: str,
        request: RejectDocumentRequest,
    ) -> RejectedDocument:
        return self._configured().reject(document_id, request)

    def reprocess(self, document_id: str) -> ProcessingDocument:
        return self._configured().reprocess(document_id)

    def _configured(self) -> GovernedDocumentLifecycleService:
        if self._delegate is None:
            raise DocumentServiceUnavailableError(
                "Document service has not completed startup configuration."
            )
        return self._delegate


def canonical_pdf_filename(value: object) -> str:
    """Return the ASCII, case-insensitive logical key for a safe PDF filename."""

    if type(value) is not str:
        raise InvalidDocumentInputError("Document filename must be text.")
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise InvalidDocumentInputError(
            "Document filename must use the safe ASCII profile."
        ) from None
    if _PDF_FILENAME_PATTERN.fullmatch(value) is None:
        raise InvalidDocumentInputError("Document filename is invalid.")
    return encoded.decode("ascii").lower()


def logical_document_id(canonical_filename: str) -> str:
    return f"doc_{_identifier_digest('logical-document', canonical_filename)}"


def version_document_id(
    logical_identity: str,
    *,
    number: int,
    source_sha256: str,
) -> str:
    digest = _identifier_digest(
        "document-resource",
        logical_identity,
        str(number),
        source_sha256,
    )
    return f"doc_{digest}"


def persistence_document_version_id(
    logical_identity: str,
    *,
    number: int,
    source_sha256: str,
) -> str:
    digest = _identifier_digest(
        "document-version",
        logical_identity,
        str(number),
        source_sha256,
    )
    return f"docver_{digest}"


def _identifier_digest(namespace: str, *parts: str) -> str:
    payload = "\x00".join((f"prescriptive-maintenance:{namespace}:v1", *parts))
    return sha256(payload.encode("ascii", errors="strict")).hexdigest()


def _base_text(value: str) -> str:
    return str.__add__("", value)


def _validated_registration_request(
    value: object,
) -> RegisterDocumentRequest:
    if type(value) is not RegisterDocumentRequest:
        raise InvalidDocumentRequestError("Document registration request is invalid.")
    try:
        return RegisterDocumentRequest.model_validate(
            {
                "filename": _base_text(value.filename),
                "media_type": _base_text(value.media_type),
                "size_bytes": value.size_bytes,
                "sha256": _base_text(value.sha256),
            }
        )
    except Exception:
        raise InvalidDocumentRequestError(
            "Document registration request is invalid."
        ) from None


def _validated_approval_request(value: object) -> ApproveDocumentRequest:
    if type(value) is not ApproveDocumentRequest:
        raise InvalidDocumentRequestError("Document approval request is invalid.")
    try:
        note = value.note
        return ApproveDocumentRequest.model_validate(
            {"note": None if note is None else _base_text(note)}
        )
    except Exception:
        raise InvalidDocumentRequestError(
            "Document approval request is invalid."
        ) from None


def _validated_rejection_request(value: object) -> RejectDocumentRequest:
    if type(value) is not RejectDocumentRequest:
        raise InvalidDocumentRequestError("Document rejection request is invalid.")
    try:
        return RejectDocumentRequest.model_validate(
            {"reason": _base_text(value.reason)}
        )
    except Exception:
        raise InvalidDocumentRequestError(
            "Document rejection request is invalid."
        ) from None


def _version_metadata(
    document: GovernedDocument,
    number: int,
    request: RegisterDocumentRequest,
) -> RegisteredDocumentVersion:
    version = document.version(number)
    return RegisteredDocumentVersion(
        number=number,
        document_id=version_document_id(
            document.identity,
            number=number,
            source_sha256=version.sha256,
        ),
        document_version_id=persistence_document_version_id(
            document.identity,
            number=number,
            source_sha256=version.sha256,
        ),
        media_type=_base_text(request.media_type),
        size_bytes=request.size_bytes,
    )


def _matching_registration(
    snapshot: DocumentRegistrationSnapshot,
    request: RegisterDocumentRequest,
) -> int | None:
    for version, metadata in zip(
        snapshot.registration.document.versions,
        snapshot.registration.versions,
        strict=True,
    ):
        if version.sha256 != request.sha256:
            continue
        if (
            metadata.media_type != request.media_type
            or metadata.size_bytes != request.size_bytes
        ):
            raise DocumentContentConflictError(
                "Document content identity already has different metadata."
            )
        return version.number
    return None


def _received_projection(
    snapshot: DocumentRegistrationSnapshot,
    version_number: int,
) -> ReceivedDocument:
    registration = snapshot.registration
    version = registration.document.version(version_number)
    metadata = registration.versions[version_number - 1]
    return ReceivedDocument(
        document_id=metadata.document_id,
        filename=registration.display_filename,
        media_type=cast(Literal["application/pdf"], metadata.media_type),
        size_bytes=metadata.size_bytes,
        sha256=version.sha256,
        created_at=version.received_at,
        updated_at=version.received_at,
        status=DocumentStatus.RECEIVED,
        decision_note=None,
        failure=None,
        superseded_by_document_id=None,
    )


class _DocumentCommon(TypedDict):
    document_id: str
    filename: str
    media_type: Literal["application/pdf"]
    size_bytes: int
    sha256: str
    created_at: datetime
    updated_at: datetime


def _document_projection(
    snapshot: DocumentRegistrationSnapshot,
    version_number: int,
) -> Document:
    registration = snapshot.registration
    version = registration.document.version(version_number)
    metadata = registration.versions[version_number - 1]
    common: _DocumentCommon = {
        "document_id": metadata.document_id,
        "filename": registration.display_filename,
        "media_type": cast(Literal["application/pdf"], metadata.media_type),
        "size_bytes": metadata.size_bytes,
        "sha256": version.sha256,
        "created_at": version.received_at,
        "updated_at": version.updated_at,
    }
    if version.status is DocumentStatus.RECEIVED:
        return ReceivedDocument(
            **common,
            status=DocumentStatus.RECEIVED,
            decision_note=None,
            failure=None,
            superseded_by_document_id=None,
        )
    if version.status is DocumentStatus.PROCESSING:
        return ProcessingDocument(
            **common,
            status=DocumentStatus.PROCESSING,
            decision_note=None,
            failure=None,
            superseded_by_document_id=None,
        )
    if version.status is DocumentStatus.PENDING_APPROVAL:
        return PendingApprovalDocument(
            **common,
            status=DocumentStatus.PENDING_APPROVAL,
            decision_note=None,
            failure=None,
            superseded_by_document_id=None,
        )
    if version.status is DocumentStatus.APPROVED:
        return ApprovedDocument(
            **common,
            status=DocumentStatus.APPROVED,
            decision_note=_decision_reason(
                registration.document,
                version_number,
                LifecycleAction.APPROVED,
            ),
            failure=None,
            superseded_by_document_id=None,
        )
    if version.status is DocumentStatus.REJECTED:
        reason = _decision_reason(
            registration.document,
            version_number,
            LifecycleAction.REJECTED,
        )
        if reason is None:
            raise DocumentRegistryIntegrityError(
                "Rejected document is missing its audited decision."
            )
        return RejectedDocument(
            **common,
            status=DocumentStatus.REJECTED,
            decision_note=reason,
            failure=None,
            superseded_by_document_id=None,
        )
    if version.status is DocumentStatus.FAILED:
        if version.failure is None:
            raise DocumentRegistryIntegrityError(
                "Failed document is missing its audited failure."
            )
        return FailedDocument(
            **common,
            status=DocumentStatus.FAILED,
            decision_note=None,
            failure=DocumentFailure(code=_FAILURE_CODE, message=_FAILURE_MESSAGE),
            superseded_by_document_id=None,
        )
    replacement = version.superseded_by_version
    if replacement is None:
        raise DocumentRegistryIntegrityError(
            "Superseded document is missing its replacement."
        )
    return SupersededDocument(
        **common,
        status=DocumentStatus.SUPERSEDED,
        decision_note=None,
        failure=None,
        superseded_by_document_id=registration.versions[replacement - 1].document_id,
    )


def _decision_reason(
    document: GovernedDocument,
    version: int,
    action: LifecycleAction,
) -> str | None:
    return next(
        (
            event.reason
            for event in reversed(document.history)
            if event.version == version and event.action is action
        ),
        None,
    )


def _expected_revision_for_replay(
    located: LocatedDocumentRegistration,
    *,
    target: DocumentStatus,
    action: LifecycleAction,
) -> int:
    document = located.snapshot.registration.document
    status = document.version(located.version).status
    latest = document.history[-1]
    if (
        status is target
        and latest.version == located.version
        and latest.action is action
        and located.snapshot.revision > 1
    ):
        return located.snapshot.revision - 1
    return located.snapshot.revision


def _project_updated_snapshot(
    located: LocatedDocumentRegistration,
    updated: DocumentSnapshot,
) -> Document:
    snapshot = DocumentRegistrationSnapshot(
        registration=replace(
            located.snapshot.registration,
            document=updated.document,
        ),
        revision=updated.revision,
    )
    return _document_projection(snapshot, located.version)


def validate_registration_metadata_update(
    current: DocumentRegistrationSnapshot | None,
    candidate: DocumentRegistration,
) -> None:
    if current is None:
        if len(candidate.versions) != 1:
            raise DocumentRegistryIntegrityError(
                "Initial registration must contain exactly one version."
            )
        return
    stored = current.registration
    if (
        stored.canonical_filename != candidate.canonical_filename
        or stored.display_filename != candidate.display_filename
        or stored.document.identity != candidate.document.identity
        or candidate.versions[: len(stored.versions)] != stored.versions
        or len(candidate.versions) != len(stored.versions) + 1
    ):
        raise DocumentRegistryIntegrityError(
            "Registration metadata must append exactly one immutable version."
        )


def _raise_mapped_document_error(error: Exception) -> NoReturn:
    if isinstance(error, DocumentNotFoundError):
        raise error
    if isinstance(error, DomainDocumentNotFoundError):
        raise DocumentNotFoundError("Document was not found.") from None
    if isinstance(
        error, (InvalidDocumentRequestError, DocumentServiceUnavailableError)
    ):
        raise error
    if isinstance(error, InvalidDocumentInputError):
        raise InvalidDocumentRequestError("Document request is invalid.") from None
    if isinstance(
        error, (DomainInvalidDocumentTransitionError, DocumentApprovalBlockedError)
    ):
        raise InvalidDocumentTransitionError(
            "Document transition is invalid for the current state."
        ) from None
    if isinstance(
        error,
        (
            DocumentConcurrencyError,
            DocumentContentConflictError,
            DocumentVersionConflictError,
        ),
    ):
        raise DocumentConflictError(
            "Document command conflicts with stored state."
        ) from None
    if isinstance(
        error,
        (
            DocumentAuditConflictError,
            DocumentClockError,
            DocumentRegistryError,
        ),
    ):
        raise DocumentServiceUnavailableError(
            "Document service could not complete the operation."
        ) from None
    raise error
