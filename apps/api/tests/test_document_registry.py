"""Synthetic tests for the metadata-only document registry exposed by API v1."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import cast

import pytest
from prescriptive_maintenance.contracts import (
    ApproveDocumentRequest,
    DocumentStatus,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    ProcessingStep,
)
from prescriptive_maintenance.document_registry import (
    GovernedDocumentLifecycleService,
    InMemoryDocumentRegistryRepository,
    canonical_pdf_filename,
    logical_document_id,
)
from prescriptive_maintenance.services import (
    DocumentConflictError,
    InvalidDocumentRequestError,
    InvalidDocumentTransitionError,
)

_START = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)
_HASH_ONE = "a" * 64
_HASH_TWO = "b" * 64
_PROCESSOR = "processor.synthetic"


class ThreadSafeClock:
    """Deterministic clock usable by concurrent registry commands."""

    def __init__(self) -> None:
        self._next = _START
        self._lock = Lock()
        self.calls = 0

    def now(self) -> datetime:
        with self._lock:
            value = self._next
            self._next += timedelta(seconds=1)
            self.calls += 1
            return value


def _registry() -> tuple[
    GovernedDocumentLifecycleService,
    InMemoryDocumentRegistryRepository,
    ThreadSafeClock,
]:
    repository = InMemoryDocumentRegistryRepository()
    clock = ThreadSafeClock()
    return (
        GovernedDocumentLifecycleService(repository=repository, clock=clock),
        repository,
        clock,
    )


def _request(
    *,
    filename: str = "Manual.Synthetic.PDF",
    sha256: str = _HASH_ONE,
    size_bytes: int = 512,
) -> RegisterDocumentRequest:
    return RegisterDocumentRequest(
        filename=filename,
        media_type="application/pdf",
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _identity(filename: str = "Manual.Synthetic.PDF") -> str:
    return logical_document_id(canonical_pdf_filename(filename))


def _governance(
    repository: InMemoryDocumentRegistryRepository,
    clock: ThreadSafeClock,
) -> DocumentGovernanceService:
    return DocumentGovernanceService(repository=repository, clock=clock)


def _start_processing(
    repository: InMemoryDocumentRegistryRepository,
    clock: ThreadSafeClock,
    *,
    identity: str = _identity(),
    version: int = 1,
) -> None:
    snapshot = repository.get(identity)
    assert snapshot is not None
    _governance(repository, clock).start_processing(
        identity=identity,
        version=version,
        actor=_PROCESSOR,
        expected_revision=snapshot.revision,
    )


def _finish_integrity_gates(
    repository: InMemoryDocumentRegistryRepository,
    clock: ThreadSafeClock,
    *,
    identity: str = _identity(),
    version: int = 1,
) -> None:
    governance = _governance(repository, clock)
    for step in (ProcessingStep.EXTRACTION, ProcessingStep.INDEXING):
        snapshot = repository.get(identity)
        assert snapshot is not None
        governance.record_step_succeeded(
            identity=identity,
            version=version,
            step=step,
            actor=_PROCESSOR,
            expected_revision=snapshot.revision,
        )


def _move_to_pending_approval(
    repository: InMemoryDocumentRegistryRepository,
    clock: ThreadSafeClock,
    *,
    identity: str = _identity(),
    version: int = 1,
) -> None:
    _start_processing(repository, clock, identity=identity, version=version)
    _finish_integrity_gates(repository, clock, identity=identity, version=version)


def test_registry_starts_empty_and_registration_never_implies_approval() -> None:
    service, _, _ = _registry()

    assert service.list().items == ()

    registered = service.register(_request())

    assert registered.status is DocumentStatus.RECEIVED
    assert registered.decision_note is None
    assert registered.failure is None
    assert registered.superseded_by_document_id is None
    assert re.fullmatch(r"doc_[0-9a-f]{64}", registered.document_id)
    assert service.get(registered.document_id).root == registered


def test_exact_registration_replay_is_case_insensitive_and_preserves_first_name() -> (
    None
):
    service, repository, clock = _registry()
    initial = service.register(_request())

    replay = service.register(_request(filename="manual.synthetic.pdf"))

    assert replay == initial
    assert replay.filename == "Manual.Synthetic.PDF"
    snapshot = repository.get_registration(_identity())
    assert snapshot is not None
    assert snapshot.revision == 1
    assert len(snapshot.registration.document.history) == 1
    assert len(snapshot.registration.versions) == 1
    assert clock.calls == 1


def test_hash_replay_with_divergent_metadata_is_a_conflict() -> None:
    service, repository, _ = _registry()
    service.register(_request())

    with pytest.raises(DocumentConflictError):
        service.register(_request(filename="manual.synthetic.pdf", size_bytes=513))

    snapshot = repository.get_registration(_identity())
    assert snapshot is not None
    assert snapshot.revision == 1
    assert len(snapshot.registration.versions) == 1


def test_renamed_filename_is_a_distinct_logical_document() -> None:
    service, _, _ = _registry()

    first = service.register(_request())
    renamed = service.register(_request(filename="Renamed.Synthetic.pdf"))

    assert renamed.document_id != first.document_id
    assert len(service.list().items) == 2


def test_actions_are_idempotent_only_for_the_exact_command() -> None:
    approved_service, approved_repository, approved_clock = _registry()
    approved = approved_service.register(_request())
    _move_to_pending_approval(approved_repository, approved_clock)

    first_approval = approved_service.approve(
        approved.document_id,
        ApproveDocumentRequest(note=None),
    )
    approval_snapshot = approved_repository.get_registration(_identity())
    assert approval_snapshot is not None
    approval_history_size = len(approval_snapshot.registration.document.history)
    approval_replay = approved_service.approve(
        approved.document_id,
        ApproveDocumentRequest(note=None),
    )

    assert approval_replay == first_approval
    assert first_approval.decision_note is not None
    approval_snapshot = approved_repository.get_registration(_identity())
    assert approval_snapshot is not None
    assert len(approval_snapshot.registration.document.history) == approval_history_size
    registration_replay = approved_service.register(_request())
    assert registration_replay == approved
    assert approved_service.get(approved.document_id).root == first_approval
    approval_snapshot = approved_repository.get_registration(_identity())
    assert approval_snapshot is not None
    assert len(approval_snapshot.registration.document.history) == approval_history_size
    with pytest.raises(DocumentConflictError):
        approved_service.approve(
            approved.document_id,
            ApproveDocumentRequest(note="Outra decisão sintética."),
        )

    rejected_service, rejected_repository, rejected_clock = _registry()
    rejected = rejected_service.register(_request())
    _move_to_pending_approval(rejected_repository, rejected_clock)
    command = RejectDocumentRequest(reason="Evidência sintética insuficiente.")
    first_rejection = rejected_service.reject(rejected.document_id, command)
    rejection_snapshot = rejected_repository.get_registration(_identity())
    assert rejection_snapshot is not None
    rejection_history_size = len(rejection_snapshot.registration.document.history)

    assert rejected_service.reject(rejected.document_id, command) == first_rejection
    rejection_snapshot = rejected_repository.get_registration(_identity())
    assert rejection_snapshot is not None
    assert (
        len(rejection_snapshot.registration.document.history) == rejection_history_size
    )
    with pytest.raises(DocumentConflictError):
        rejected_service.reject(
            rejected.document_id,
            RejectDocumentRequest(reason="Decisão divergente."),
        )

    first_reprocess = rejected_service.reprocess(rejected.document_id)
    reprocess_snapshot = rejected_repository.get_registration(_identity())
    assert reprocess_snapshot is not None
    reprocess_history_size = len(reprocess_snapshot.registration.document.history)

    assert rejected_service.reprocess(rejected.document_id) == first_reprocess
    reprocess_snapshot = rejected_repository.get_registration(_identity())
    assert reprocess_snapshot is not None
    assert (
        len(reprocess_snapshot.registration.document.history) == reprocess_history_size
    )


def test_processing_started_is_not_misclassified_as_a_reprocess_replay() -> None:
    service, repository, clock = _registry()
    registered = service.register(_request())
    _start_processing(repository, clock)

    with pytest.raises(InvalidDocumentTransitionError):
        service.reprocess(registered.document_id)


def test_all_states_and_supersession_are_projected_from_the_domain() -> None:
    service, repository, clock = _registry()
    first = service.register(_request())
    assert service.get(first.document_id).root.status is DocumentStatus.RECEIVED

    _start_processing(repository, clock)
    assert service.get(first.document_id).root.status is DocumentStatus.PROCESSING

    snapshot = repository.get(_identity())
    assert snapshot is not None
    _governance(repository, clock).record_step_failed(
        identity=_identity(),
        version=1,
        step=ProcessingStep.EXTRACTION,
        code="synthetic_failure",
        reason="internal-secret-marker C:\\private\\manual.pdf",
        actor=_PROCESSOR,
        expected_revision=snapshot.revision,
    )
    failed = service.get(first.document_id).root
    assert failed.status is DocumentStatus.FAILED
    assert "internal-secret-marker" not in failed.model_dump_json()
    assert "private" not in failed.model_dump_json()

    service.reprocess(first.document_id)
    _finish_integrity_gates(repository, clock)
    assert service.get(first.document_id).root.status is DocumentStatus.PENDING_APPROVAL
    approved = service.approve(
        first.document_id,
        ApproveDocumentRequest(note="Integridade sintética comprovada."),
    )
    assert approved.status is DocumentStatus.APPROVED

    second = service.register(_request(sha256=_HASH_TWO, size_bytes=1024))
    assert second.status is DocumentStatus.RECEIVED
    _move_to_pending_approval(repository, clock, version=2)
    second_approved = service.approve(
        second.document_id,
        ApproveDocumentRequest(note="Nova versão sintética comprovada."),
    )

    superseded = service.get(first.document_id).root
    assert superseded.status is DocumentStatus.SUPERSEDED
    assert superseded.superseded_by_document_id == second_approved.document_id
    assert {item.status for item in service.list().items} == {
        DocumentStatus.SUPERSEDED,
        DocumentStatus.APPROVED,
    }
    snapshot_before_replay = repository.get_registration(_identity())
    assert snapshot_before_replay is not None
    assert service.register(_request()) == first
    assert service.get(first.document_id).root == superseded
    assert repository.get_registration(_identity()) == snapshot_before_replay


def test_transitioned_registration_replays_receipt_without_masking_current_state() -> (
    None
):
    service, repository, clock = _registry()
    receipt = service.register(_request())
    _start_processing(repository, clock)
    snapshot_before_replay = repository.get_registration(_identity())
    assert snapshot_before_replay is not None

    replay = service.register(_request(filename="manual.synthetic.pdf"))

    assert replay == receipt
    assert replay.status is DocumentStatus.RECEIVED
    assert service.get(receipt.document_id).root.status is DocumentStatus.PROCESSING
    assert repository.get_registration(_identity()) == snapshot_before_replay


def test_concurrent_exact_registration_creates_one_version_and_event() -> None:
    service, repository, _ = _registry()
    request = _request()

    def replay(_: int) -> ReceivedDocument:
        return service.register(request)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(replay, range(32)))

    assert {result.document_id for result in results} == {results[0].document_id}
    snapshot = repository.get_registration(_identity())
    assert snapshot is not None
    assert snapshot.revision == 1
    assert len(snapshot.registration.versions) == 1
    assert len(snapshot.registration.document.history) == 1


def test_concurrent_divergent_registration_cannot_create_two_candidates() -> None:
    service, repository, _ = _registry()

    def register(sha256: str) -> str:
        return service.register(_request(sha256=sha256)).document_id

    outcomes: list[str] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(register, digest) for digest in (_HASH_ONE, _HASH_TWO)
        )
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as error:
                errors.append(error)

    assert len(outcomes) == 1
    assert len(errors) == 1
    assert type(errors[0]) is DocumentConflictError
    snapshot = repository.get_registration(_identity())
    assert snapshot is not None
    assert len(snapshot.registration.versions) == 1


@pytest.mark.parametrize(
    "filename",
    ("../manual.pdf", r"folder\manual.pdf", "månual.pdf", "manual.txt"),
)
def test_direct_registry_rejects_unsafe_filename_without_retaining_it(
    filename: str,
) -> None:
    service, repository, _ = _registry()
    request = RegisterDocumentRequest.model_construct(
        filename=filename,
        media_type="application/pdf",
        size_bytes=512,
        sha256=_HASH_ONE,
    )

    with pytest.raises(InvalidDocumentRequestError):
        service.register(request)

    assert repository.list_registrations() == ()


@pytest.mark.parametrize(
    "overrides",
    (
        {"media_type": "text/plain"},
        {"size_bytes": 0},
        {"size_bytes": True},
        {"sha256": "not-a-sha256"},
    ),
)
def test_registry_revalidates_constructed_transport_models(
    overrides: dict[str, object],
) -> None:
    service, repository, _ = _registry()
    values: dict[str, object] = {
        "filename": "manual.synthetic.pdf",
        "media_type": "application/pdf",
        "size_bytes": 512,
        "sha256": _HASH_ONE,
        **overrides,
    }
    request = RegisterDocumentRequest.model_construct(
        filename=cast(str, values["filename"]),
        media_type=cast(str, values["media_type"]),
        size_bytes=cast(int, values["size_bytes"]),
        sha256=cast(str, values["sha256"]),
    )

    with pytest.raises(InvalidDocumentRequestError):
        service.register(request)

    assert repository.list_registrations() == ()
