"""Governed document lifecycle with append-only audit and in-memory CAS."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import ClassVar, Final, Protocol
from unicodedata import category as unicode_category

from prescriptive_maintenance.contracts import DocumentStatus

_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_UNSAFE_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})
_ACTIVE_CANDIDATE_STATUSES: Final = frozenset(
    {
        DocumentStatus.RECEIVED,
        DocumentStatus.PROCESSING,
        DocumentStatus.PENDING_APPROVAL,
    }
)
_INTEGRITY_REQUIRED_STATUSES: Final = frozenset(
    {
        DocumentStatus.PENDING_APPROVAL,
        DocumentStatus.APPROVED,
        DocumentStatus.REJECTED,
        DocumentStatus.SUPERSEDED,
    }
)
_ALLOWED_TRANSITIONS: Final[dict[DocumentStatus, frozenset[DocumentStatus]]] = {
    DocumentStatus.RECEIVED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.PROCESSING: frozenset(
        {DocumentStatus.PENDING_APPROVAL, DocumentStatus.FAILED}
    ),
    DocumentStatus.PENDING_APPROVAL: frozenset(
        {DocumentStatus.APPROVED, DocumentStatus.REJECTED}
    ),
    DocumentStatus.APPROVED: frozenset({DocumentStatus.SUPERSEDED}),
    DocumentStatus.REJECTED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PROCESSING}),
    DocumentStatus.SUPERSEDED: frozenset(),
}


class DocumentLifecycleError(Exception):
    """Base class for predictable, sanitized document-domain errors."""

    code: ClassVar[str] = "document_lifecycle_error"


class InvalidDocumentInputError(DocumentLifecycleError):
    """Raised when a domain value violates the documented input contract."""

    code = "invalid_document_input"


class DocumentNotFoundError(DocumentLifecycleError):
    """Raised when a logical document identity is absent."""

    code = "document_not_found"


class DocumentVersionConflictError(DocumentLifecycleError):
    """Raised when a version number is reused or is not sequential."""

    code = "document_version_conflict"


class DocumentContentConflictError(DocumentLifecycleError):
    """Raised when the same content hash is assigned to another version."""

    code = "document_content_conflict"


class InvalidDocumentTransitionError(DocumentLifecycleError):
    """Raised when a requested state transition is not in the state matrix."""

    code = "invalid_document_transition"


class DocumentApprovalBlockedError(DocumentLifecycleError):
    """Raised when extraction or indexing integrity does not permit approval."""

    code = "document_approval_blocked"


class DocumentConcurrencyError(DocumentLifecycleError):
    """Raised when compare-and-swap observes a lost logical update."""

    code = "document_concurrency_conflict"

    def __init__(self, *, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "Document revision changed before the requested update could be saved."
        )


class DocumentAuditConflictError(DocumentLifecycleError):
    """Raised when a save would erase or rewrite existing audit history."""

    code = "document_audit_conflict"


class DocumentClockError(DocumentLifecycleError):
    """Raised when an injected clock is naive or moves backwards."""

    code = "document_clock_error"


class ProcessingStep(StrEnum):
    """Integrity gates required before documentary approval."""

    EXTRACTION = "extraction"
    INDEXING = "indexing"


class ProcessingStepStatus(StrEnum):
    """State of one independently resumable processing gate."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LifecycleAction(StrEnum):
    """Append-only audit vocabulary for lifecycle mutations."""

    REGISTERED = "registered"
    PROCESSING_STARTED = "processing_started"
    REPROCESSING_STARTED = "reprocessing_started"
    EXTRACTION_SUCCEEDED = "extraction_succeeded"
    INDEXING_SUCCEEDED = "indexing_succeeded"
    PROCESSING_FAILED = "processing_failed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


_EVENT_STATUS_TRANSITIONS: Final[
    dict[
        LifecycleAction,
        tuple[frozenset[DocumentStatus | None], DocumentStatus],
    ]
] = {
    LifecycleAction.REGISTERED: (frozenset({None}), DocumentStatus.RECEIVED),
    LifecycleAction.PROCESSING_STARTED: (
        frozenset({DocumentStatus.RECEIVED}),
        DocumentStatus.PROCESSING,
    ),
    LifecycleAction.REPROCESSING_STARTED: (
        frozenset({DocumentStatus.FAILED, DocumentStatus.REJECTED}),
        DocumentStatus.PROCESSING,
    ),
    LifecycleAction.EXTRACTION_SUCCEEDED: (
        frozenset({DocumentStatus.PROCESSING}),
        DocumentStatus.PROCESSING,
    ),
    LifecycleAction.INDEXING_SUCCEEDED: (
        frozenset({DocumentStatus.PROCESSING}),
        DocumentStatus.PENDING_APPROVAL,
    ),
    LifecycleAction.PROCESSING_FAILED: (
        frozenset({DocumentStatus.PROCESSING}),
        DocumentStatus.FAILED,
    ),
    LifecycleAction.APPROVED: (
        frozenset({DocumentStatus.PENDING_APPROVAL}),
        DocumentStatus.APPROVED,
    ),
    LifecycleAction.REJECTED: (
        frozenset({DocumentStatus.PENDING_APPROVAL}),
        DocumentStatus.REJECTED,
    ),
    LifecycleAction.SUPERSEDED: (
        frozenset({DocumentStatus.APPROVED}),
        DocumentStatus.SUPERSEDED,
    ),
}
_EVENT_STEPS: Final[dict[LifecycleAction, ProcessingStep]] = {
    LifecycleAction.EXTRACTION_SUCCEEDED: ProcessingStep.EXTRACTION,
    LifecycleAction.INDEXING_SUCCEEDED: ProcessingStep.INDEXING,
}
_REASON_REQUIRED_ACTIONS: Final = frozenset(
    {
        LifecycleAction.PROCESSING_FAILED,
        LifecycleAction.APPROVED,
        LifecycleAction.REJECTED,
        LifecycleAction.SUPERSEDED,
    }
)


def allowed_document_transitions(
    status: DocumentStatus,
) -> frozenset[DocumentStatus]:
    """Return the closed set of direct transitions for one status."""

    return _ALLOWED_TRANSITIONS[status]


def is_document_transition_allowed(
    source: DocumentStatus,
    target: DocumentStatus,
) -> bool:
    """Report whether the direct status transition belongs to the state matrix."""

    return target in _ALLOWED_TRANSITIONS[source]


class Clock(Protocol):
    """Injectable wall-clock boundary used by lifecycle commands."""

    def now(self) -> datetime: ...


class SystemUtcClock:
    """Production clock returning timezone-aware UTC values."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProcessingIntegrity:
    """Persisted progress for resumable extraction and indexing."""

    extraction: ProcessingStepStatus = ProcessingStepStatus.PENDING
    indexing: ProcessingStepStatus = ProcessingStepStatus.PENDING

    @property
    def complete(self) -> bool:
        return (
            self.extraction is ProcessingStepStatus.SUCCEEDED
            and self.indexing is ProcessingStepStatus.SUCCEEDED
        )

    @property
    def has_failure(self) -> bool:
        return (
            self.extraction is ProcessingStepStatus.FAILED
            or self.indexing is ProcessingStepStatus.FAILED
        )

    def status_for(self, step: ProcessingStep) -> ProcessingStepStatus:
        if step is ProcessingStep.EXTRACTION:
            return self.extraction
        return self.indexing

    def with_status(
        self,
        step: ProcessingStep,
        status: ProcessingStepStatus,
    ) -> ProcessingIntegrity:
        if step is ProcessingStep.EXTRACTION:
            return replace(self, extraction=status)
        return replace(self, indexing=status)

    def resume_failed(self) -> ProcessingIntegrity:
        """Keep completed work and reset only failed work for a safe retry."""

        extraction = self.extraction
        indexing = self.indexing
        if extraction is ProcessingStepStatus.FAILED:
            extraction = ProcessingStepStatus.PENDING
        if indexing is ProcessingStepStatus.FAILED:
            indexing = ProcessingStepStatus.PENDING
        return ProcessingIntegrity(extraction=extraction, indexing=indexing)


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    """Sanitized failure retained on a failed document version."""

    step: ProcessingStep
    code: str
    reason: str
    actor: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _required_text(self.code, field="failure code", maximum=80)
        _required_text(self.reason, field="failure reason", maximum=500)
        _required_text(self.actor, field="actor", maximum=200)
        _require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """One immutable-content version and its current governed state."""

    number: int
    sha256: str
    status: DocumentStatus
    integrity: ProcessingIntegrity
    received_at: datetime
    updated_at: datetime
    failure: ProcessingFailure | None = None
    superseded_by_version: int | None = None

    def __post_init__(self) -> None:
        _validate_version_number(self.number)
        _validate_sha256(self.sha256)
        _require_utc(self.received_at)
        _require_utc(self.updated_at)
        if self.updated_at < self.received_at:
            raise InvalidDocumentInputError(
                "Document version updated_at cannot precede received_at."
            )
        if self.status is DocumentStatus.RECEIVED and self.integrity != (
            ProcessingIntegrity()
        ):
            raise InvalidDocumentInputError(
                "A received version cannot contain processing results."
            )
        if self.status is DocumentStatus.PROCESSING and self.integrity.has_failure:
            raise InvalidDocumentInputError(
                "A processing version cannot retain a failed step."
            )
        if self.status in _INTEGRITY_REQUIRED_STATUSES and not self.integrity.complete:
            raise InvalidDocumentInputError(
                "The document state requires complete extraction and indexing."
            )
        if self.status is DocumentStatus.FAILED:
            if self.failure is None or not self.integrity.has_failure:
                raise InvalidDocumentInputError(
                    "A failed version requires a matching processing failure."
                )
        elif self.failure is not None:
            raise InvalidDocumentInputError(
                "Only failed versions may retain a processing failure."
            )
        if self.status is DocumentStatus.SUPERSEDED:
            if (
                self.superseded_by_version is None
                or self.superseded_by_version <= self.number
            ):
                raise InvalidDocumentInputError(
                    "A superseded version requires a newer replacement version."
                )
        elif self.superseded_by_version is not None:
            raise InvalidDocumentInputError(
                "Only superseded versions may reference a replacement."
            )


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One ordered, append-only audit fact."""

    sequence: int
    document_identity: str
    version: int
    action: LifecycleAction
    source_status: DocumentStatus | None
    target_status: DocumentStatus
    occurred_at: datetime
    actor: str
    reason: str | None
    step: ProcessingStep | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise InvalidDocumentInputError("Audit sequence must be positive.")
        _validate_identity(self.document_identity)
        _validate_version_number(self.version)
        _require_utc(self.occurred_at)
        _required_text(self.actor, field="actor", maximum=200)
        expected_sources, expected_target = _EVENT_STATUS_TRANSITIONS[self.action]
        if (
            self.source_status not in expected_sources
            or self.target_status is not expected_target
        ):
            raise InvalidDocumentInputError(
                "Audit action does not match its status transition."
            )
        expected_step = _EVENT_STEPS.get(self.action)
        if expected_step is not None and self.step is not expected_step:
            raise InvalidDocumentInputError(
                "Audit action does not match its processing step."
            )
        if self.action is LifecycleAction.PROCESSING_FAILED:
            _validate_processing_step(self.step)
            if self.failure_code is None:
                raise InvalidDocumentInputError(
                    "A processing failure event requires a failure code."
                )
            _required_text(self.failure_code, field="failure code", maximum=80)
        elif self.failure_code is not None:
            raise InvalidDocumentInputError(
                "Only processing failure events may contain a failure code."
            )
        if (
            expected_step is None
            and self.action is not LifecycleAction.PROCESSING_FAILED
            and self.step is not None
        ):
            raise InvalidDocumentInputError(
                "Audit action cannot contain a processing step."
            )
        if self.action in _REASON_REQUIRED_ACTIONS:
            if self.reason is None:
                raise InvalidDocumentInputError(
                    "The audit action requires a non-empty reason."
                )
            _required_text(self.reason, field="reason", maximum=500)
        elif self.reason is not None:
            raise InvalidDocumentInputError("The audit action cannot contain a reason.")


@dataclass(frozen=True, slots=True)
class _LifecycleCommand:
    """Canonical command payload used only for exact replay recognition."""

    action: LifecycleAction
    version: int
    actor: str
    reason: str | None = None
    sha256: str | None = None
    step: ProcessingStep | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class GovernedDocument:
    """Aggregate root for all versions of one logical document."""

    identity: str
    versions: tuple[DocumentVersion, ...]
    current_version: int | None
    history: tuple[LifecycleEvent, ...]

    def __post_init__(self) -> None:
        _validate_identity(self.identity)
        if not self.versions or not self.history:
            raise InvalidDocumentInputError(
                "A governed document requires one version and one audit event."
            )
        hashes: set[str] = set()
        active_candidates = 0
        approved_versions: list[int] = []
        for expected_number, version in enumerate(self.versions, start=1):
            if version.number != expected_number:
                raise InvalidDocumentInputError(
                    "Document versions must be contiguous and ordered."
                )
            if version.sha256 in hashes:
                raise InvalidDocumentInputError(
                    "Document versions must have distinct content hashes."
                )
            hashes.add(version.sha256)
            if version.status in _ACTIVE_CANDIDATE_STATUSES:
                active_candidates += 1
                if version.number != len(self.versions):
                    raise InvalidDocumentInputError(
                        "Only the latest document version may be an active candidate."
                    )
            if version.status is DocumentStatus.APPROVED:
                approved_versions.append(version.number)
        if active_candidates > 1:
            raise InvalidDocumentInputError(
                "Only one document version may be an active candidate."
            )
        if self.current_version is None:
            if approved_versions:
                raise InvalidDocumentInputError(
                    "An approved version must be the current version."
                )
        elif approved_versions != [self.current_version]:
            raise InvalidDocumentInputError(
                "Exactly the current version may remain approved."
            )
        previous_time: datetime | None = None
        for expected_sequence, event in enumerate(self.history, start=1):
            if event.sequence != expected_sequence:
                raise InvalidDocumentInputError(
                    "Audit events must have contiguous sequence numbers."
                )
            if event.document_identity != self.identity:
                raise InvalidDocumentInputError(
                    "Audit event identity does not match the aggregate."
                )
            if event.version > len(self.versions):
                raise InvalidDocumentInputError(
                    "Audit event references an unknown document version."
                )
            if previous_time is not None and event.occurred_at < previous_time:
                raise DocumentClockError("Audit timestamps cannot move backwards.")
            previous_time = event.occurred_at

    @classmethod
    def register(
        cls,
        *,
        identity: str,
        version: int,
        sha256: str,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Create the first received version of a logical document."""

        clean_identity = _validate_identity(identity)
        if version != 1:
            raise DocumentVersionConflictError(
                "The first document version must be version 1."
            )
        clean_hash = _validate_sha256(sha256)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        instant = _require_utc(occurred_at)
        document_version = DocumentVersion(
            number=version,
            sha256=clean_hash,
            status=DocumentStatus.RECEIVED,
            integrity=ProcessingIntegrity(),
            received_at=instant,
            updated_at=instant,
        )
        event = LifecycleEvent(
            sequence=1,
            document_identity=clean_identity,
            version=version,
            action=LifecycleAction.REGISTERED,
            source_status=None,
            target_status=DocumentStatus.RECEIVED,
            occurred_at=instant,
            actor=clean_actor,
            reason=None,
        )
        return cls(
            identity=clean_identity,
            versions=(document_version,),
            current_version=None,
            history=(event,),
        )

    def version(self, number: int) -> DocumentVersion:
        """Return one version without exposing mutable repository state."""

        _validate_version_number(number)
        try:
            return self.versions[number - 1]
        except IndexError:
            raise DocumentVersionConflictError(
                "Document version does not exist."
            ) from None

    def registered_version(
        self,
        *,
        version: int,
        sha256: str,
    ) -> DocumentVersion | None:
        """Find an exact registration identity for idempotent command replay."""

        _validate_version_number(version)
        clean_hash = _validate_sha256(sha256)
        if version > len(self.versions):
            return None
        candidate = self.versions[version - 1]
        if candidate.sha256 == clean_hash:
            return candidate
        return None

    def register_version(
        self,
        *,
        version: int,
        sha256: str,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Append a new received version or return an exact existing registration."""

        _validate_version_number(version)
        clean_hash = _validate_sha256(sha256)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        if version <= len(self.versions):
            existing = self.versions[version - 1]
            if existing.sha256 == clean_hash:
                registration = next(
                    (
                        event
                        for event in self.history
                        if event.action is LifecycleAction.REGISTERED
                        and event.version == version
                    ),
                    None,
                )
                if registration is not None and registration.actor == clean_actor:
                    return self
                raise DocumentVersionConflictError(
                    "The existing registration has a different audit command."
                )
            raise DocumentVersionConflictError(
                "The requested version already identifies different content."
            )
        if any(item.sha256 == clean_hash for item in self.versions):
            raise DocumentContentConflictError(
                "The content hash is already registered under another version."
            )
        expected_version = len(self.versions) + 1
        if version != expected_version:
            raise DocumentVersionConflictError(
                "Document versions must be registered sequentially."
            )
        if self.versions[-1].status in _ACTIVE_CANDIDATE_STATUSES:
            raise InvalidDocumentTransitionError(
                "The active candidate must finish before another version is registered."
            )
        instant = self._validate_next_instant(occurred_at)
        new_version = DocumentVersion(
            number=version,
            sha256=clean_hash,
            status=DocumentStatus.RECEIVED,
            integrity=ProcessingIntegrity(),
            received_at=instant,
            updated_at=instant,
        )
        event = self._event(
            version=version,
            action=LifecycleAction.REGISTERED,
            source_status=None,
            target_status=DocumentStatus.RECEIVED,
            occurred_at=instant,
            actor=clean_actor,
            reason=None,
        )
        return replace(
            self,
            versions=(*self.versions, new_version),
            history=(*self.history, event),
        )

    def start_processing(
        self,
        *,
        version: int,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Move a newly received version into its first processing attempt."""

        clean_actor = _required_text(actor, field="actor", maximum=200)
        current = self.version(version)
        self._ensure_latest(version)
        self._ensure_transition(current.status, DocumentStatus.PROCESSING)
        instant = self._validate_next_instant(occurred_at)
        updated = replace(
            current,
            status=DocumentStatus.PROCESSING,
            updated_at=instant,
        )
        return self._replace_version_and_record(
            original=current,
            updated=updated,
            action=LifecycleAction.PROCESSING_STARTED,
            actor=clean_actor,
            reason=None,
            occurred_at=instant,
        )

    def reprocess(
        self,
        *,
        version: int,
        sha256: str,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Resume a failed version or restart both gates after rejection."""

        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_hash = _validate_sha256(sha256)
        current = self.version(version)
        if current.sha256 != clean_hash:
            raise DocumentContentConflictError(
                "The reprocessing hash does not identify the requested version."
            )
        self._ensure_latest(version)
        self._ensure_transition(current.status, DocumentStatus.PROCESSING)
        integrity = (
            current.integrity.resume_failed()
            if current.status is DocumentStatus.FAILED
            else ProcessingIntegrity()
        )
        instant = self._validate_next_instant(occurred_at)
        updated = replace(
            current,
            status=DocumentStatus.PROCESSING,
            integrity=integrity,
            updated_at=instant,
            failure=None,
        )
        return self._replace_version_and_record(
            original=current,
            updated=updated,
            action=LifecycleAction.REPROCESSING_STARTED,
            actor=clean_actor,
            reason=None,
            occurred_at=instant,
        )

    def record_step_succeeded(
        self,
        *,
        version: int,
        step: ProcessingStep,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Record one successful integrity gate and await approval when complete."""

        clean_step = _validate_processing_step(step)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        current = self.version(version)
        self._ensure_latest(version)
        if current.status is not DocumentStatus.PROCESSING:
            raise InvalidDocumentTransitionError(
                "Processing results require a version in processing."
            )
        if current.integrity.status_for(clean_step) is ProcessingStepStatus.SUCCEEDED:
            raise InvalidDocumentTransitionError(
                "A successful processing step cannot be recorded again."
            )
        self._ensure_step_order(current.integrity, clean_step)
        integrity = current.integrity.with_status(
            clean_step,
            ProcessingStepStatus.SUCCEEDED,
        )
        target = (
            DocumentStatus.PENDING_APPROVAL
            if integrity.complete
            else DocumentStatus.PROCESSING
        )
        if target is not current.status:
            self._ensure_transition(current.status, target)
        instant = self._validate_next_instant(occurred_at)
        updated = replace(
            current,
            status=target,
            integrity=integrity,
            updated_at=instant,
        )
        action = (
            LifecycleAction.EXTRACTION_SUCCEEDED
            if clean_step is ProcessingStep.EXTRACTION
            else LifecycleAction.INDEXING_SUCCEEDED
        )
        return self._replace_version_and_record(
            original=current,
            updated=updated,
            action=action,
            actor=clean_actor,
            reason=None,
            occurred_at=instant,
            step=clean_step,
        )

    def record_step_failed(
        self,
        *,
        version: int,
        step: ProcessingStep,
        code: str,
        reason: str,
        actor: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Fail one processing gate while retaining any completed gate."""

        clean_step = _validate_processing_step(step)
        clean_code = _required_text(code, field="failure code", maximum=80)
        clean_reason = _required_text(reason, field="failure reason", maximum=500)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        current = self.version(version)
        self._ensure_latest(version)
        if current.status is not DocumentStatus.PROCESSING:
            raise InvalidDocumentTransitionError(
                "Processing failures require a version in processing."
            )
        if current.integrity.status_for(clean_step) is ProcessingStepStatus.SUCCEEDED:
            raise InvalidDocumentTransitionError(
                "A successful processing step cannot regress to failed."
            )
        self._ensure_step_order(current.integrity, clean_step)
        self._ensure_transition(current.status, DocumentStatus.FAILED)
        instant = self._validate_next_instant(occurred_at)
        integrity = current.integrity.with_status(
            clean_step,
            ProcessingStepStatus.FAILED,
        )
        failure = ProcessingFailure(
            step=clean_step,
            code=clean_code,
            reason=clean_reason,
            actor=clean_actor,
            occurred_at=instant,
        )
        updated = replace(
            current,
            status=DocumentStatus.FAILED,
            integrity=integrity,
            updated_at=instant,
            failure=failure,
        )
        return self._replace_version_and_record(
            original=current,
            updated=updated,
            action=LifecycleAction.PROCESSING_FAILED,
            actor=clean_actor,
            reason=clean_reason,
            occurred_at=instant,
            step=clean_step,
            failure_code=clean_code,
        )

    def approve(
        self,
        *,
        version: int,
        actor: str,
        reason: str | None,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Approve the latest intact candidate and atomically supersede the current."""

        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_reason = _required_text(reason, field="approval reason", maximum=500)
        candidate = self.version(version)
        self._ensure_latest(version)
        if not candidate.integrity.complete:
            raise DocumentApprovalBlockedError(
                "Extraction and indexing must both succeed before approval."
            )
        if candidate.status is not DocumentStatus.PENDING_APPROVAL:
            raise InvalidDocumentTransitionError(
                "Only a pending document version can be approved."
            )
        instant = self._validate_next_instant(occurred_at)
        versions = list(self.versions)
        history = list(self.history)
        if self.current_version is not None:
            previous = self.version(self.current_version)
            self._ensure_transition(previous.status, DocumentStatus.SUPERSEDED)
            versions[previous.number - 1] = replace(
                previous,
                status=DocumentStatus.SUPERSEDED,
                updated_at=instant,
                superseded_by_version=version,
            )
            history.append(
                self._event(
                    version=previous.number,
                    action=LifecycleAction.SUPERSEDED,
                    source_status=previous.status,
                    target_status=DocumentStatus.SUPERSEDED,
                    occurred_at=instant,
                    actor=clean_actor,
                    reason=f"Superseded by version {version}.",
                    sequence=len(history) + 1,
                )
            )
        self._ensure_transition(candidate.status, DocumentStatus.APPROVED)
        versions[version - 1] = replace(
            candidate,
            status=DocumentStatus.APPROVED,
            updated_at=instant,
        )
        history.append(
            self._event(
                version=version,
                action=LifecycleAction.APPROVED,
                source_status=candidate.status,
                target_status=DocumentStatus.APPROVED,
                occurred_at=instant,
                actor=clean_actor,
                reason=clean_reason,
                sequence=len(history) + 1,
            )
        )
        return replace(
            self,
            versions=tuple(versions),
            current_version=version,
            history=tuple(history),
        )

    def reject(
        self,
        *,
        version: int,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> GovernedDocument:
        """Reject the latest pending candidate without deleting its audit trail."""

        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_reason = _required_text(reason, field="rejection reason", maximum=500)
        candidate = self.version(version)
        self._ensure_latest(version)
        self._ensure_transition(candidate.status, DocumentStatus.REJECTED)
        instant = self._validate_next_instant(occurred_at)
        updated = replace(
            candidate,
            status=DocumentStatus.REJECTED,
            updated_at=instant,
        )
        return self._replace_version_and_record(
            original=candidate,
            updated=updated,
            action=LifecycleAction.REJECTED,
            actor=clean_actor,
            reason=clean_reason,
            occurred_at=instant,
        )

    def is_eligible(self, version: int) -> bool:
        """Allow retrieval only from the intact, approved, current version."""

        candidate = self.version(version)
        return (
            candidate.status is DocumentStatus.APPROVED
            and candidate.integrity.complete
            and self.current_version == candidate.number
        )

    @property
    def eligible_version(self) -> DocumentVersion | None:
        """Return the sole eligible version, if one exists."""

        if self.current_version is None:
            return None
        candidate = self.version(self.current_version)
        return candidate if self.is_eligible(candidate.number) else None

    def _ensure_latest(self, version: int) -> None:
        if version != len(self.versions):
            raise DocumentVersionConflictError(
                "Only the latest document version can be processed or decided."
            )

    @staticmethod
    def _ensure_transition(
        source: DocumentStatus,
        target: DocumentStatus,
    ) -> None:
        if not is_document_transition_allowed(source, target):
            raise InvalidDocumentTransitionError(
                f"Transition from {source.value} to {target.value} is not allowed."
            )

    @staticmethod
    def _ensure_step_order(
        integrity: ProcessingIntegrity,
        step: ProcessingStep,
    ) -> None:
        if (
            step is ProcessingStep.INDEXING
            and integrity.extraction is not ProcessingStepStatus.SUCCEEDED
        ):
            raise InvalidDocumentTransitionError(
                "Indexing requires successful extraction."
            )

    def _replace_version_and_record(
        self,
        *,
        original: DocumentVersion,
        updated: DocumentVersion,
        action: LifecycleAction,
        actor: str,
        reason: str | None,
        occurred_at: datetime,
        step: ProcessingStep | None = None,
        failure_code: str | None = None,
    ) -> GovernedDocument:
        clean_actor = _required_text(actor, field="actor", maximum=200)
        event = self._event(
            version=updated.number,
            action=action,
            source_status=original.status,
            target_status=updated.status,
            occurred_at=occurred_at,
            actor=clean_actor,
            reason=reason,
            step=step,
            failure_code=failure_code,
        )
        versions = tuple(
            updated if item.number == updated.number else item for item in self.versions
        )
        return replace(
            self,
            versions=versions,
            history=(*self.history, event),
        )

    def _event(
        self,
        *,
        version: int,
        action: LifecycleAction,
        source_status: DocumentStatus | None,
        target_status: DocumentStatus,
        occurred_at: datetime,
        actor: str,
        reason: str | None,
        sequence: int | None = None,
        step: ProcessingStep | None = None,
        failure_code: str | None = None,
    ) -> LifecycleEvent:
        return LifecycleEvent(
            sequence=len(self.history) + 1 if sequence is None else sequence,
            document_identity=self.identity,
            version=version,
            action=action,
            source_status=source_status,
            target_status=target_status,
            occurred_at=occurred_at,
            actor=actor,
            reason=reason,
            step=step,
            failure_code=failure_code,
        )

    def _validate_next_instant(self, value: datetime) -> datetime:
        instant = _require_utc(value)
        if instant < self.history[-1].occurred_at:
            raise DocumentClockError("Lifecycle clock cannot move backwards.")
        return instant


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """Repository value paired with its compare-and-swap revision."""

    document: GovernedDocument
    revision: int

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise InvalidDocumentInputError("Stored revision must be positive.")


def is_document_snapshot_audited(value: object) -> bool:
    """Verify that one snapshot is the exact replay of its append-only history."""

    try:
        if (
            type(value) is not DocumentSnapshot
            or type(value.revision) is not int
            or value.revision < 1
            or type(value.document) is not GovernedDocument
            or type(value.document.versions) is not tuple
            or not value.document.versions
            or type(value.document.history) is not tuple
            or not value.document.history
            or any(
                type(version) is not DocumentVersion
                for version in value.document.versions
            )
            or any(
                type(event) is not LifecycleEvent for event in value.document.history
            )
        ):
            return False

        document = value.document
        first = document.history[0]
        if first.action is not LifecycleAction.REGISTERED:
            return False
        replayed = GovernedDocument.register(
            identity=document.identity,
            version=first.version,
            sha256=document.version(first.version).sha256,
            actor=first.actor,
            occurred_at=first.occurred_at,
        )
        if replayed.history != (first,):
            return False

        cursor = 1
        revision = 1
        while cursor < len(document.history):
            event = document.history[cursor]
            expected_events = (event,)
            replay_event = event
            if event.action is LifecycleAction.SUPERSEDED:
                if cursor + 1 >= len(document.history):
                    return False
                approval = document.history[cursor + 1]
                if approval.action is not LifecycleAction.APPROVED:
                    return False
                expected_events = (event, approval)
                replay_event = approval

            updated = _rebuild_audited_update(
                replayed,
                document,
                event=replay_event,
            )
            if updated.history[len(replayed.history) :] != expected_events:
                return False
            replayed = updated
            cursor += len(expected_events)
            revision += 1

        return revision == value.revision and replayed == document
    except Exception:
        return False


class DocumentRepository(Protocol):
    """Minimal persistence port required by the lifecycle service."""

    def get(self, identity: str) -> DocumentSnapshot | None: ...

    def list(self) -> tuple[DocumentSnapshot, ...]: ...

    def compare_and_swap(
        self,
        document: GovernedDocument,
        *,
        expected_revision: int,
    ) -> DocumentSnapshot: ...


class InMemoryDocumentRepository:
    """Thread-safe in-memory repository with append-only compare-and-swap."""

    def __init__(self) -> None:
        self._documents: dict[str, DocumentSnapshot] = {}
        self._lock = RLock()

    def get(self, identity: str) -> DocumentSnapshot | None:
        clean_identity = _validate_identity(identity)
        with self._lock:
            return self._documents.get(clean_identity)

    def list(self) -> tuple[DocumentSnapshot, ...]:
        with self._lock:
            return tuple(self._documents[key] for key in sorted(self._documents))

    def compare_and_swap(
        self,
        document: GovernedDocument,
        *,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_expected_revision = _validate_expected_revision(expected_revision)
        with self._lock:
            current = self._documents.get(document.identity)
            actual_revision = 0 if current is None else current.revision
            if actual_revision != clean_expected_revision:
                raise DocumentConcurrencyError(
                    expected_revision=clean_expected_revision,
                    actual_revision=actual_revision,
                )
            if current is None:
                _validate_initial_document(document)
            else:
                _validate_append_only_update(current.document, document)
            snapshot = DocumentSnapshot(
                document=document,
                revision=actual_revision + 1,
            )
            self._documents[document.identity] = snapshot
            return snapshot


class DocumentGovernanceService:
    """Apply lifecycle commands against a revisioned repository and clock."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def get(self, identity: str) -> DocumentSnapshot:
        snapshot = self._repository.get(identity)
        if snapshot is None:
            raise DocumentNotFoundError("Document was not found.")
        return snapshot

    def list(self) -> tuple[DocumentSnapshot, ...]:
        return self._repository.list()

    def register(
        self,
        *,
        identity: str,
        version: int,
        sha256: str,
        actor: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        """Register once by identity, version and hash, even after a CAS race."""

        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_hash = _validate_sha256(sha256)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.REGISTERED,
            version=clean_version,
            actor=clean_actor,
            sha256=clean_hash,
        )
        current = self._repository.get(clean_identity)
        if current is not None:
            registered = current.document.registered_version(
                version=clean_version,
                sha256=clean_hash,
            )
            if registered is not None and _matches_registration_command(
                current.document,
                command,
            ):
                return current
            if current.revision != clean_expected_revision:
                raise DocumentConcurrencyError(
                    expected_revision=clean_expected_revision,
                    actual_revision=current.revision,
                )
            updated = current.document.register_version(
                version=clean_version,
                sha256=clean_hash,
                actor=clean_actor,
                occurred_at=self._utc_now(),
            )
        else:
            if clean_expected_revision != 0:
                raise DocumentConcurrencyError(
                    expected_revision=clean_expected_revision,
                    actual_revision=0,
                )
            updated = GovernedDocument.register(
                identity=clean_identity,
                version=clean_version,
                sha256=clean_hash,
                actor=clean_actor,
                occurred_at=self._utc_now(),
            )
        try:
            return self._repository.compare_and_swap(
                updated,
                expected_revision=clean_expected_revision,
            )
        except DocumentConcurrencyError:
            raced = self._repository.get(clean_identity)
            if raced is not None and _matches_registration_command(
                raced.document, command
            ):
                return raced
            raise

    def start_processing(
        self,
        *,
        identity: str,
        version: int,
        actor: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.PROCESSING_STARTED,
            version=clean_version,
            actor=clean_actor,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.start_processing(
                version=clean_version,
                actor=clean_actor,
                occurred_at=instant,
            ),
        )

    def reprocess(
        self,
        *,
        identity: str,
        version: int,
        sha256: str,
        actor: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_hash = _validate_sha256(sha256)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.REPROCESSING_STARTED,
            version=clean_version,
            actor=clean_actor,
            sha256=clean_hash,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.reprocess(
                version=clean_version,
                sha256=clean_hash,
                actor=clean_actor,
                occurred_at=instant,
            ),
        )

    def record_step_succeeded(
        self,
        *,
        identity: str,
        version: int,
        step: ProcessingStep,
        actor: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_step = _validate_processing_step(step)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        action = (
            LifecycleAction.EXTRACTION_SUCCEEDED
            if clean_step is ProcessingStep.EXTRACTION
            else LifecycleAction.INDEXING_SUCCEEDED
        )
        command = _LifecycleCommand(
            action=action,
            version=clean_version,
            actor=clean_actor,
            step=clean_step,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.record_step_succeeded(
                version=clean_version,
                step=clean_step,
                actor=clean_actor,
                occurred_at=instant,
            ),
        )

    def record_step_failed(
        self,
        *,
        identity: str,
        version: int,
        step: ProcessingStep,
        code: str,
        reason: str,
        actor: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_step = _validate_processing_step(step)
        clean_code = _required_text(code, field="failure code", maximum=80)
        clean_reason = _required_text(reason, field="failure reason", maximum=500)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.PROCESSING_FAILED,
            version=clean_version,
            actor=clean_actor,
            reason=clean_reason,
            step=clean_step,
            failure_code=clean_code,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.record_step_failed(
                version=clean_version,
                step=clean_step,
                code=clean_code,
                reason=clean_reason,
                actor=clean_actor,
                occurred_at=instant,
            ),
        )

    def approve(
        self,
        *,
        identity: str,
        version: int,
        actor: str,
        reason: str | None,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_reason = _required_text(reason, field="approval reason", maximum=500)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.APPROVED,
            version=clean_version,
            actor=clean_actor,
            reason=clean_reason,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.approve(
                version=clean_version,
                actor=clean_actor,
                reason=clean_reason,
                occurred_at=instant,
            ),
        )

    def reject(
        self,
        *,
        identity: str,
        version: int,
        actor: str,
        reason: str,
        expected_revision: int,
    ) -> DocumentSnapshot:
        clean_identity = _validate_identity(identity)
        clean_version = _validate_version_number(version)
        clean_actor = _required_text(actor, field="actor", maximum=200)
        clean_reason = _required_text(reason, field="rejection reason", maximum=500)
        clean_expected_revision = _validate_expected_revision(expected_revision)
        command = _LifecycleCommand(
            action=LifecycleAction.REJECTED,
            version=clean_version,
            actor=clean_actor,
            reason=clean_reason,
        )
        return self._mutate(
            identity=clean_identity,
            expected_revision=clean_expected_revision,
            command=command,
            mutation=lambda document, instant: document.reject(
                version=clean_version,
                actor=clean_actor,
                reason=clean_reason,
                occurred_at=instant,
            ),
        )

    def _mutate(
        self,
        *,
        identity: str,
        expected_revision: int,
        command: _LifecycleCommand,
        mutation: Callable[[GovernedDocument, datetime], GovernedDocument],
    ) -> DocumentSnapshot:
        current = self.get(identity)
        if current.revision != expected_revision:
            if current.revision == expected_revision + 1 and _matches_latest_command(
                current.document, command
            ):
                return current
            raise DocumentConcurrencyError(
                expected_revision=expected_revision,
                actual_revision=current.revision,
            )
        updated = mutation(current.document, self._utc_now())
        try:
            return self._repository.compare_and_swap(
                updated,
                expected_revision=expected_revision,
            )
        except DocumentConcurrencyError:
            raced = self._repository.get(identity)
            if (
                raced is not None
                and raced.revision == expected_revision + 1
                and _matches_latest_command(raced.document, command)
            ):
                return raced
            raise

    def _utc_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DocumentClockError("Document lifecycle clock must be timezone-aware.")
        return value.astimezone(UTC)


def _validate_initial_document(document: GovernedDocument) -> None:
    if len(document.versions) != 1 or len(document.history) != 1:
        raise DocumentAuditConflictError(
            "An initial document save must contain only its registration."
        )
    event = document.history[0]
    if event.action is not LifecycleAction.REGISTERED:
        raise DocumentAuditConflictError(
            "An initial document save must begin with registration."
        )
    try:
        expected = GovernedDocument.register(
            identity=document.identity,
            version=event.version,
            sha256=document.version(event.version).sha256,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    except DocumentLifecycleError:
        raise DocumentAuditConflictError(
            "The initial document aggregate is not a valid registration."
        ) from None
    if expected != document:
        raise DocumentAuditConflictError(
            "The initial document aggregate is not a valid registration."
        )


def _validate_append_only_update(
    current: GovernedDocument,
    candidate: GovernedDocument,
) -> None:
    if current.identity != candidate.identity:
        raise DocumentAuditConflictError(
            "A document identity cannot change during an update."
        )
    current_history_length = len(current.history)
    if (
        len(candidate.history) <= current_history_length
        or candidate.history[:current_history_length] != current.history
    ):
        raise DocumentAuditConflictError(
            "Document audit history must be extended without rewriting prior events."
        )
    if len(candidate.versions) < len(current.versions):
        raise DocumentAuditConflictError(
            "Document versions cannot be removed from the repository."
        )
    for stored, updated in zip(current.versions, candidate.versions, strict=False):
        if (
            stored.number != updated.number
            or stored.sha256 != updated.sha256
            or stored.received_at != updated.received_at
        ):
            raise DocumentAuditConflictError(
                "Stored document version identity and content are immutable."
            )
    try:
        expected = _rebuild_audited_update(current, candidate)
    except DocumentLifecycleError:
        raise DocumentAuditConflictError(
            "The document aggregate contains an impossible lifecycle evolution."
        ) from None
    if expected != candidate:
        raise DocumentAuditConflictError(
            "The document aggregate does not match its appended audit command."
        )


def _rebuild_audited_update(
    current: GovernedDocument,
    candidate: GovernedDocument,
    *,
    event: LifecycleEvent | None = None,
) -> GovernedDocument:
    event = candidate.history[-1] if event is None else event
    if event.action is LifecycleAction.REGISTERED:
        registered = candidate.version(event.version)
        return current.register_version(
            version=event.version,
            sha256=registered.sha256,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if event.action is LifecycleAction.PROCESSING_STARTED:
        return current.start_processing(
            version=event.version,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if event.action is LifecycleAction.REPROCESSING_STARTED:
        return current.reprocess(
            version=event.version,
            sha256=current.version(event.version).sha256,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if event.action in {
        LifecycleAction.EXTRACTION_SUCCEEDED,
        LifecycleAction.INDEXING_SUCCEEDED,
    }:
        return current.record_step_succeeded(
            version=event.version,
            step=_required_event_step(event),
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if event.action is LifecycleAction.PROCESSING_FAILED:
        return current.record_step_failed(
            version=event.version,
            step=_required_event_step(event),
            code=_required_event_failure_code(event),
            reason=_required_event_reason(event),
            actor=event.actor,
            occurred_at=event.occurred_at,
        )
    if event.action is LifecycleAction.APPROVED:
        return current.approve(
            version=event.version,
            actor=event.actor,
            reason=_required_event_reason(event),
            occurred_at=event.occurred_at,
        )
    if event.action is LifecycleAction.REJECTED:
        return current.reject(
            version=event.version,
            actor=event.actor,
            reason=_required_event_reason(event),
            occurred_at=event.occurred_at,
        )
    raise DocumentAuditConflictError(
        "A supersession cannot be appended without its replacement approval."
    )


def _required_event_step(event: LifecycleEvent) -> ProcessingStep:
    if event.step is None:
        raise InvalidDocumentInputError("The audit command requires a processing step.")
    return _validate_processing_step(event.step)


def _required_event_failure_code(event: LifecycleEvent) -> str:
    if event.failure_code is None:
        raise InvalidDocumentInputError("The audit command requires a failure code.")
    return _required_text(event.failure_code, field="failure code", maximum=80)


def _required_event_reason(event: LifecycleEvent) -> str:
    if event.reason is None:
        raise InvalidDocumentInputError("The audit command requires a reason.")
    return _required_text(event.reason, field="reason", maximum=500)


def _matches_registration_command(
    document: GovernedDocument,
    command: _LifecycleCommand,
) -> bool:
    if command.action is not LifecycleAction.REGISTERED:
        return False
    try:
        version = document.version(command.version)
    except DocumentLifecycleError:
        return False
    if version.sha256 != command.sha256:
        return False
    return any(
        _event_matches_command(event, command)
        for event in document.history
        if event.action is LifecycleAction.REGISTERED
        and event.version == command.version
    )


def _matches_latest_command(
    document: GovernedDocument,
    command: _LifecycleCommand,
) -> bool:
    if not _event_matches_command(document.history[-1], command):
        return False
    if command.sha256 is None:
        return True
    try:
        return document.version(command.version).sha256 == command.sha256
    except DocumentLifecycleError:
        return False


def _event_matches_command(
    event: LifecycleEvent,
    command: _LifecycleCommand,
) -> bool:
    return (
        event.action is command.action
        and event.version == command.version
        and event.actor == command.actor
        and event.reason == command.reason
        and event.step is command.step
        and event.failure_code == command.failure_code
    )


def _validate_identity(value: str) -> str:
    return _required_text(value, field="document identity", maximum=200)


def _validate_version_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidDocumentInputError("Document version must be a positive integer.")
    return value


def _validate_expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidDocumentInputError(
            "Expected revision must be a non-negative integer."
        )
    return value


def _validate_processing_step(value: object) -> ProcessingStep:
    if not isinstance(value, ProcessingStep):
        raise InvalidDocumentInputError("Processing step is invalid.")
    return value


def _validate_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise InvalidDocumentInputError(
            "Document hash must be a lowercase hexadecimal SHA-256 value."
        )
    return value


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise InvalidDocumentInputError(f"{field.capitalize()} must be text.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise InvalidDocumentInputError(
            f"{field.capitalize()} must contain safe UTF-8 text."
        ) from None
    if any(
        unicode_category(character) in _UNSAFE_TEXT_CATEGORIES
        or _is_unicode_noncharacter(character)
        for character in value
    ):
        raise InvalidDocumentInputError(
            f"{field.capitalize()} must contain safe UTF-8 text."
        )
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise InvalidDocumentInputError(
            f"{field.capitalize()} must contain 1 to {maximum} characters."
        )
    return cleaned


def _is_unicode_noncharacter(character: str) -> bool:
    code_point = ord(character)
    return 0xFDD0 <= code_point <= 0xFDEF or code_point & 0xFFFF in {
        0xFFFE,
        0xFFFF,
    }


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DocumentClockError("Document timestamps must be timezone-aware UTC.")
    if value.utcoffset() != timedelta(0):
        raise DocumentClockError("Document timestamps must use UTC.")
    return value
