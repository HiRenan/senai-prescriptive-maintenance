"""Entirely synthetic tests for governed document lifecycle invariants."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from prescriptive_maintenance.contracts import DocumentStatus
from prescriptive_maintenance.document_lifecycle import (
    DocumentApprovalBlockedError,
    DocumentAuditConflictError,
    DocumentClockError,
    DocumentConcurrencyError,
    DocumentContentConflictError,
    DocumentGovernanceService,
    DocumentSnapshot,
    DocumentVersionConflictError,
    InMemoryDocumentRepository,
    InvalidDocumentInputError,
    InvalidDocumentTransitionError,
    LifecycleAction,
    LifecycleEvent,
    ProcessingStep,
    ProcessingStepStatus,
    allowed_document_transitions,
    is_document_transition_allowed,
)

_IDENTITY = "manual.synthetic"
_OTHER_IDENTITY = "guide.synthetic"
_ACTOR = "reviewer.synthetic"
_HASH_ONE = "a" * 64
_HASH_TWO = "b" * 64
_HASH_THREE = "c" * 64
_START = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)
_UNSAFE_AUDIT_TEXT = (
    "unsafe\x00text",
    "unsafe\u202etext",
    "unsafe\ud800text",
    "unsafe\ufdd0text",
)


class ControlledClock:
    """Deterministic clock that advances exactly once per real mutation."""

    def __init__(self, start: datetime = _START) -> None:
        self._next = start
        self.calls = 0

    def now(self) -> datetime:
        current = self._next
        self._next += timedelta(seconds=1)
        self.calls += 1
        return current


def _service(
    *,
    clock: ControlledClock | None = None,
) -> tuple[
    DocumentGovernanceService,
    InMemoryDocumentRepository,
    ControlledClock,
]:
    controlled_clock = ControlledClock() if clock is None else clock
    repository = InMemoryDocumentRepository()
    return (
        DocumentGovernanceService(
            repository=repository,
            clock=controlled_clock,
        ),
        repository,
        controlled_clock,
    )


def _register(
    service: DocumentGovernanceService,
    *,
    identity: str = _IDENTITY,
    version: int = 1,
    sha256: str = _HASH_ONE,
    expected_revision: int = 0,
) -> DocumentSnapshot:
    return service.register(
        identity=identity,
        version=version,
        sha256=sha256,
        actor=_ACTOR,
        expected_revision=expected_revision,
    )


def _start(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int = 1,
) -> DocumentSnapshot:
    return service.start_processing(
        identity=snapshot.document.identity,
        version=version,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )


def _succeed(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int,
    step: ProcessingStep,
) -> DocumentSnapshot:
    return service.record_step_succeeded(
        identity=snapshot.document.identity,
        version=version,
        step=step,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )


def _pending(
    service: DocumentGovernanceService,
    snapshot: DocumentSnapshot,
    *,
    version: int = 1,
) -> DocumentSnapshot:
    processing = _start(service, snapshot, version=version)
    extracted = _succeed(
        service,
        processing,
        version=version,
        step=ProcessingStep.EXTRACTION,
    )
    return _succeed(
        service,
        extracted,
        version=version,
        step=ProcessingStep.INDEXING,
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
        actor="approver.synthetic",
        reason="Synthetic approval evidence is complete.",
        expected_revision=snapshot.revision,
    )


def test_transition_matrix_is_closed_and_explicit() -> None:
    expected: dict[DocumentStatus, frozenset[DocumentStatus]] = {
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

    assert set(expected) == set(DocumentStatus)
    for source in DocumentStatus:
        assert allowed_document_transitions(source) == expected[source]
        for target in DocumentStatus:
            assert is_document_transition_allowed(source, target) is (
                target in expected[source]
            )


def test_all_allowed_transitions_are_reached_by_governed_commands() -> None:
    service, _, _ = _service()
    snapshot = _register(service)
    snapshot = _pending(service, snapshot)
    snapshot = service.reject(
        identity=_IDENTITY,
        version=1,
        actor="approver.synthetic",
        reason="Synthetic review requested a new processing pass.",
        expected_revision=snapshot.revision,
    )
    snapshot = service.reprocess(
        identity=_IDENTITY,
        version=1,
        sha256=_HASH_ONE,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )
    snapshot = _succeed(
        service,
        snapshot,
        version=1,
        step=ProcessingStep.EXTRACTION,
    )
    snapshot = _succeed(
        service,
        snapshot,
        version=1,
        step=ProcessingStep.INDEXING,
    )
    snapshot = _approve(service, snapshot)
    snapshot = _register(
        service,
        version=2,
        sha256=_HASH_TWO,
        expected_revision=snapshot.revision,
    )
    snapshot = _start(service, snapshot, version=2)
    snapshot = _succeed(
        service,
        snapshot,
        version=2,
        step=ProcessingStep.EXTRACTION,
    )
    snapshot = service.record_step_failed(
        identity=_IDENTITY,
        version=2,
        step=ProcessingStep.INDEXING,
        code="synthetic.index_unavailable",
        reason="Synthetic index write did not complete.",
        actor="indexer.synthetic",
        expected_revision=snapshot.revision,
    )
    snapshot = service.reprocess(
        identity=_IDENTITY,
        version=2,
        sha256=_HASH_TWO,
        actor="processor.synthetic",
        expected_revision=snapshot.revision,
    )
    snapshot = _succeed(
        service,
        snapshot,
        version=2,
        step=ProcessingStep.INDEXING,
    )
    snapshot = _approve(service, snapshot, version=2)

    observed = {
        (event.source_status, event.target_status)
        for event in snapshot.document.history
        if event.source_status is not None
        and event.source_status is not event.target_status
    }
    expected = {
        (source, target)
        for source in DocumentStatus
        for target in allowed_document_transitions(source)
    }
    assert observed == expected


def test_identical_registration_is_idempotent_without_new_audit_or_revision() -> None:
    service, _, clock = _service()
    first = _register(service)

    repeated = _register(service, expected_revision=0)

    assert repeated == first
    assert repeated.revision == 1
    assert len(repeated.document.versions) == 1
    assert len(repeated.document.history) == 1
    assert clock.calls == 1


def test_stale_replay_requires_exact_action_version_and_actor() -> None:
    service, repository, clock = _service()
    registered = _register(service)
    started = service.start_processing(
        identity=_IDENTITY,
        version=1,
        actor="processor.synthetic",
        expected_revision=registered.revision,
    )

    exact_replay = service.start_processing(
        identity=_IDENTITY,
        version=1,
        actor="processor.synthetic",
        expected_revision=registered.revision,
    )

    assert exact_replay == started
    assert clock.calls == 2
    for actor in ("other.processor.synthetic",):
        with pytest.raises(DocumentConcurrencyError):
            service.start_processing(
                identity=_IDENTITY,
                version=1,
                actor=actor,
                expected_revision=registered.revision,
            )
    with pytest.raises(DocumentConcurrencyError):
        service.start_processing(
            identity=_IDENTITY,
            version=2,
            actor="processor.synthetic",
            expected_revision=registered.revision,
        )
    with pytest.raises(DocumentConcurrencyError):
        service.record_step_succeeded(
            identity=_IDENTITY,
            version=1,
            step=ProcessingStep.EXTRACTION,
            actor="processor.synthetic",
            expected_revision=registered.revision,
        )
    with pytest.raises(InvalidDocumentInputError):
        service.start_processing(
            identity=_IDENTITY,
            version=1,
            actor=" ",
            expected_revision=registered.revision,
        )
    assert repository.get(_IDENTITY) == started
    assert clock.calls == 2


@pytest.mark.parametrize(
    "unsafe_text",
    _UNSAFE_AUDIT_TEXT,
    ids=("control", "format", "surrogate", "noncharacter"),
)
def test_unsafe_audit_text_is_rejected_before_idempotent_returns(
    unsafe_text: str,
) -> None:
    service, repository, clock = _service()
    registered = _register(service)

    with pytest.raises(InvalidDocumentInputError) as actor_error:
        service.register(
            identity=_IDENTITY,
            version=1,
            sha256=_HASH_ONE,
            actor=unsafe_text,
            expected_revision=0,
        )
    assert unsafe_text not in str(actor_error.value)
    processing = _start(service, registered)
    invalid_failures = (
        {"code": unsafe_text, "reason": "Synthetic failure.", "actor": _ACTOR},
        {"code": "synthetic.failure", "reason": unsafe_text, "actor": _ACTOR},
        {
            "code": "synthetic.failure",
            "reason": "Synthetic failure.",
            "actor": unsafe_text,
        },
    )
    for payload in invalid_failures:
        with pytest.raises(InvalidDocumentInputError) as failure_error:
            service.record_step_failed(
                identity=_IDENTITY,
                version=1,
                step=ProcessingStep.EXTRACTION,
                expected_revision=processing.revision,
                **payload,
            )
        assert unsafe_text not in str(failure_error.value)
    with pytest.raises(InvalidDocumentInputError) as approval_error:
        service.approve(
            identity=_IDENTITY,
            version=1,
            actor="approver.synthetic",
            reason=unsafe_text,
            expected_revision=processing.revision,
        )
    assert unsafe_text not in str(approval_error.value)
    assert repository.get(_IDENTITY) == processing
    assert clock.calls == 2


def test_registration_conflicts_do_not_replace_content_or_audit() -> None:
    service, repository, _ = _service()
    original = _register(service)

    with pytest.raises(DocumentVersionConflictError) as reused_version:
        _register(
            service,
            sha256=_HASH_TWO,
            expected_revision=original.revision,
        )
    with pytest.raises(DocumentContentConflictError) as reused_hash:
        _register(
            service,
            version=2,
            sha256=_HASH_ONE,
            expected_revision=original.revision,
        )
    with pytest.raises(DocumentVersionConflictError):
        _register(
            service,
            version=3,
            sha256=_HASH_THREE,
            expected_revision=original.revision,
        )

    assert reused_version.value.code == "document_version_conflict"
    assert reused_hash.value.code == "document_content_conflict"
    assert repository.get(_IDENTITY) == original


def test_new_hash_creates_next_version_without_displacing_current_approval() -> None:
    service, _, _ = _service()
    approved = _approve(service, _pending(service, _register(service)))

    replacement = _register(
        service,
        version=2,
        sha256=_HASH_TWO,
        expected_revision=approved.revision,
    )

    assert tuple(item.sha256 for item in replacement.document.versions) == (
        _HASH_ONE,
        _HASH_TWO,
    )
    assert replacement.document.current_version == 1
    assert replacement.document.is_eligible(1)
    assert not replacement.document.is_eligible(2)
    assert replacement.document.version(1).status is DocumentStatus.APPROVED
    assert replacement.document.version(2).status is DocumentStatus.RECEIVED


def test_partial_failure_retry_preserves_completed_work_and_is_idempotent() -> None:
    service, _, clock = _service()
    snapshot = _start(service, _register(service))
    snapshot = _succeed(
        service,
        snapshot,
        version=1,
        step=ProcessingStep.EXTRACTION,
    )
    failed = service.record_step_failed(
        identity=_IDENTITY,
        version=1,
        step=ProcessingStep.INDEXING,
        code="synthetic.index_timeout",
        reason="Synthetic index acknowledgement was not received.",
        actor="indexer.synthetic",
        expected_revision=snapshot.revision,
    )

    with pytest.raises(DocumentContentConflictError):
        service.reprocess(
            identity=_IDENTITY,
            version=1,
            sha256=_HASH_TWO,
            actor="processor.synthetic",
            expected_revision=failed.revision,
        )

    resumed = service.reprocess(
        identity=_IDENTITY,
        version=1,
        sha256=_HASH_ONE,
        actor="processor.synthetic",
        expected_revision=failed.revision,
    )
    repeated = service.reprocess(
        identity=_IDENTITY,
        version=1,
        sha256=_HASH_ONE,
        actor="processor.synthetic",
        expected_revision=failed.revision,
    )

    assert repeated == resumed
    assert resumed.document.version(1).integrity.extraction is (
        ProcessingStepStatus.SUCCEEDED
    )
    assert resumed.document.version(1).integrity.indexing is (
        ProcessingStepStatus.PENDING
    )
    assert resumed.document.version(1).failure is None
    calls_before_completion = clock.calls
    pending = _succeed(
        service,
        resumed,
        version=1,
        step=ProcessingStep.INDEXING,
    )
    repeated_result = _succeed(
        service,
        resumed,
        version=1,
        step=ProcessingStep.INDEXING,
    )
    assert repeated_result == pending
    assert clock.calls == calls_before_completion + 1


def test_failed_command_replay_requires_exact_step_code_reason_and_actor() -> None:
    service, repository, clock = _service()
    processing = _start(service, _register(service))
    failed = service.record_step_failed(
        identity=_IDENTITY,
        version=1,
        step=ProcessingStep.EXTRACTION,
        code="synthetic.extract_timeout",
        reason="Synthetic extraction acknowledgement was not received.",
        actor="extractor.synthetic",
        expected_revision=processing.revision,
    )

    exact_replay = service.record_step_failed(
        identity=_IDENTITY,
        version=1,
        step=ProcessingStep.EXTRACTION,
        code="synthetic.extract_timeout",
        reason="Synthetic extraction acknowledgement was not received.",
        actor="extractor.synthetic",
        expected_revision=processing.revision,
    )

    assert exact_replay == failed
    divergent_payloads: tuple[tuple[ProcessingStep, str, str, str], ...] = (
        (
            ProcessingStep.INDEXING,
            "synthetic.extract_timeout",
            "Synthetic extraction acknowledgement was not received.",
            "extractor.synthetic",
        ),
        (
            ProcessingStep.EXTRACTION,
            "synthetic.extract_unavailable",
            "Synthetic extraction acknowledgement was not received.",
            "extractor.synthetic",
        ),
        (
            ProcessingStep.EXTRACTION,
            "synthetic.extract_timeout",
            "Synthetic extraction returned a different failure.",
            "extractor.synthetic",
        ),
        (
            ProcessingStep.EXTRACTION,
            "synthetic.extract_timeout",
            "Synthetic extraction acknowledgement was not received.",
            "other.extractor.synthetic",
        ),
    )
    for step, code, reason, actor in divergent_payloads:
        with pytest.raises(DocumentConcurrencyError):
            service.record_step_failed(
                identity=_IDENTITY,
                version=1,
                step=step,
                code=code,
                reason=reason,
                actor=actor,
                expected_revision=processing.revision,
            )
    assert repository.get(_IDENTITY) == failed
    assert clock.calls == 3


def test_succeeded_step_cannot_regress_to_failed() -> None:
    service, repository, _ = _service()
    processing = _start(service, _register(service))
    extracted = _succeed(
        service,
        processing,
        version=1,
        step=ProcessingStep.EXTRACTION,
    )

    with pytest.raises(InvalidDocumentTransitionError) as raised:
        service.record_step_failed(
            identity=_IDENTITY,
            version=1,
            step=ProcessingStep.EXTRACTION,
            code="synthetic.extract_late_failure",
            reason="Synthetic late failure arrived after success.",
            actor="extractor.synthetic",
            expected_revision=extracted.revision,
        )

    assert raised.value.code == "invalid_document_transition"
    assert repository.get(_IDENTITY) == extracted
    assert extracted.document.version(1).integrity.extraction is (
        ProcessingStepStatus.SUCCEEDED
    )


def test_rejected_retry_restarts_both_integrity_gates() -> None:
    service, _, _ = _service()
    pending = _pending(service, _register(service))
    rejected = service.reject(
        identity=_IDENTITY,
        version=1,
        actor="approver.synthetic",
        reason="Synthetic content requires full reprocessing.",
        expected_revision=pending.revision,
    )

    resumed = service.reprocess(
        identity=_IDENTITY,
        version=1,
        sha256=_HASH_ONE,
        actor="processor.synthetic",
        expected_revision=rejected.revision,
    )

    assert resumed.document.version(1).integrity.extraction is (
        ProcessingStepStatus.PENDING
    )
    assert resumed.document.version(1).integrity.indexing is (
        ProcessingStepStatus.PENDING
    )


def test_approval_is_blocked_and_invalid_operations_preserve_state() -> None:
    service, repository, _ = _service()
    processing = _start(service, _register(service))
    extracted = _succeed(
        service,
        processing,
        version=1,
        step=ProcessingStep.EXTRACTION,
    )

    with pytest.raises(InvalidDocumentInputError) as missing_reason:
        service.approve(
            identity=_IDENTITY,
            version=1,
            actor="approver.synthetic",
            reason=None,
            expected_revision=extracted.revision,
        )
    with pytest.raises(DocumentApprovalBlockedError) as blocked:
        service.approve(
            identity=_IDENTITY,
            version=1,
            actor="approver.synthetic",
            reason="Synthetic evidence is not complete yet.",
            expected_revision=extracted.revision,
        )
    with pytest.raises(InvalidDocumentTransitionError):
        service.reject(
            identity=_IDENTITY,
            version=1,
            actor="approver.synthetic",
            reason="Synthetic invalid rejection.",
            expected_revision=extracted.revision,
        )

    assert missing_reason.value.code == "invalid_document_input"
    assert blocked.value.code == "document_approval_blocked"
    assert repository.get(_IDENTITY) == extracted
    assert extracted.document.current_version is None
    assert extracted.document.eligible_version is None


def test_indexing_cannot_run_before_extraction() -> None:
    service, repository, _ = _service()
    processing = _start(service, _register(service))

    with pytest.raises(InvalidDocumentTransitionError):
        _succeed(
            service,
            processing,
            version=1,
            step=ProcessingStep.INDEXING,
        )

    assert repository.get(_IDENTITY) == processing


def test_replacement_supersedes_atomically_and_terminal_states_are_ineligible() -> None:
    service, _, _ = _service()
    first = _approve(service, _pending(service, _register(service)))
    replacement = _register(
        service,
        version=2,
        sha256=_HASH_TWO,
        expected_revision=first.revision,
    )
    pending_replacement = _pending(service, replacement, version=2)

    approved_replacement = _approve(
        service,
        pending_replacement,
        version=2,
    )

    previous = approved_replacement.document.version(1)
    current = approved_replacement.document.version(2)
    assert previous.status is DocumentStatus.SUPERSEDED
    assert previous.superseded_by_version == 2
    assert current.status is DocumentStatus.APPROVED
    assert approved_replacement.document.current_version == 2
    assert not approved_replacement.document.is_eligible(1)
    assert approved_replacement.document.is_eligible(2)
    assert approved_replacement.revision == pending_replacement.revision + 1
    assert tuple(
        event.action for event in approved_replacement.document.history[-2:]
    ) == (LifecycleAction.SUPERSEDED, LifecycleAction.APPROVED)
    assert (
        approved_replacement.document.history[-2].occurred_at
        == approved_replacement.document.history[-1].occurred_at
    )


def test_approval_replay_requires_exact_reason_and_actor() -> None:
    service, repository, clock = _service()
    pending = _pending(service, _register(service))
    approved = service.approve(
        identity=_IDENTITY,
        version=1,
        actor="approver.synthetic",
        reason="Synthetic approval evidence is complete.",
        expected_revision=pending.revision,
    )

    replayed = service.approve(
        identity=_IDENTITY,
        version=1,
        actor="approver.synthetic",
        reason="Synthetic approval evidence is complete.",
        expected_revision=pending.revision,
    )

    assert replayed == approved
    for actor, reason in (
        ("other.approver.synthetic", "Synthetic approval evidence is complete."),
        ("approver.synthetic", "Synthetic approval payload differs."),
    ):
        with pytest.raises(DocumentConcurrencyError):
            service.approve(
                identity=_IDENTITY,
                version=1,
                actor=actor,
                reason=reason,
                expected_revision=pending.revision,
            )
    with pytest.raises(InvalidDocumentInputError):
        service.approve(
            identity=_IDENTITY,
            version=1,
            actor="approver.synthetic",
            reason=" ",
            expected_revision=pending.revision,
        )
    assert repository.get(_IDENTITY) == approved
    assert clock.calls == 5


def test_failed_and_rejected_candidates_never_displace_current_version() -> None:
    service, _, _ = _service()
    first = _approve(service, _pending(service, _register(service)))
    second = _register(
        service,
        version=2,
        sha256=_HASH_TWO,
        expected_revision=first.revision,
    )
    processing = _start(service, second, version=2)
    failed = service.record_step_failed(
        identity=_IDENTITY,
        version=2,
        step=ProcessingStep.EXTRACTION,
        code="synthetic.extract_failed",
        reason="Synthetic extraction failed.",
        actor="extractor.synthetic",
        expected_revision=processing.revision,
    )

    assert failed.document.current_version == 1
    assert failed.document.is_eligible(1)
    assert not failed.document.is_eligible(2)
    third = _register(
        service,
        version=3,
        sha256=_HASH_THREE,
        expected_revision=failed.revision,
    )
    pending_third = _pending(service, third, version=3)
    rejected = service.reject(
        identity=_IDENTITY,
        version=3,
        actor="approver.synthetic",
        reason="Synthetic review rejected the replacement.",
        expected_revision=pending_third.revision,
    )
    assert rejected.document.current_version == 1
    assert rejected.document.is_eligible(1)
    assert not rejected.document.is_eligible(2)
    assert not rejected.document.is_eligible(3)


def test_lost_compare_and_swap_does_not_overwrite_winner() -> None:
    service, repository, _ = _service()
    stored = _register(service)
    first_writer = stored.document.start_processing(
        version=1,
        actor="worker.one.synthetic",
        occurred_at=_START + timedelta(seconds=10),
    )
    second_writer = stored.document.start_processing(
        version=1,
        actor="worker.two.synthetic",
        occurred_at=_START + timedelta(seconds=11),
    )
    winner = repository.compare_and_swap(
        first_writer,
        expected_revision=stored.revision,
    )

    with pytest.raises(DocumentConcurrencyError) as lost:
        repository.compare_and_swap(
            second_writer,
            expected_revision=stored.revision,
        )

    assert lost.value.code == "document_concurrency_conflict"
    assert lost.value.expected_revision == 1
    assert lost.value.actual_revision == 2
    assert repository.get(_IDENTITY) == winner
    assert winner.document.history[-1].actor == "worker.one.synthetic"


def test_repository_rejects_history_truncation_and_rewrite() -> None:
    service, repository, _ = _service()
    stored = _start(service, _register(service))
    truncated = replace(
        stored.document,
        history=stored.document.history[:1],
    )

    with pytest.raises(DocumentAuditConflictError) as raised:
        repository.compare_and_swap(
            truncated,
            expected_revision=stored.revision,
        )

    assert raised.value.code == "document_audit_conflict"
    assert repository.get(_IDENTITY) == stored


def test_repository_rejects_fabricated_supersession() -> None:
    service, repository, _ = _service()
    approved = _approve(service, _pending(service, _register(service)))
    replacement = _register(
        service,
        version=2,
        sha256=_HASH_TWO,
        expected_revision=approved.revision,
    )
    instant = replacement.document.history[-1].occurred_at + timedelta(seconds=1)
    previous = replacement.document.version(1)
    fabricated_previous = replace(
        previous,
        status=DocumentStatus.SUPERSEDED,
        updated_at=instant,
        superseded_by_version=2,
    )
    fabricated_event = LifecycleEvent(
        sequence=len(replacement.document.history) + 1,
        document_identity=_IDENTITY,
        version=1,
        action=LifecycleAction.SUPERSEDED,
        source_status=DocumentStatus.APPROVED,
        target_status=DocumentStatus.SUPERSEDED,
        occurred_at=instant,
        actor="approver.synthetic",
        reason="Superseded by version 2.",
    )
    fabricated = replace(
        replacement.document,
        versions=(fabricated_previous, replacement.document.version(2)),
        current_version=None,
        history=(*replacement.document.history, fabricated_event),
    )

    with pytest.raises(DocumentAuditConflictError) as raised:
        repository.compare_and_swap(
            fabricated,
            expected_revision=replacement.revision,
        )

    assert raised.value.code == "document_audit_conflict"
    assert repository.get(_IDENTITY) == replacement
    assert replacement.document.version(1).status is DocumentStatus.APPROVED
    assert replacement.document.version(2).status is DocumentStatus.RECEIVED


def test_history_is_ordered_and_clock_values_are_normalized_to_utc() -> None:
    source_zone = timezone(timedelta(hours=-3))
    local_start = datetime(2035, 1, 2, 0, 4, 5, tzinfo=source_zone)
    service, _, clock = _service(clock=ControlledClock(local_start))
    snapshot = _pending(service, _register(service))

    history = snapshot.document.history
    assert tuple(event.sequence for event in history) == (1, 2, 3, 4)
    assert tuple(event.occurred_at for event in history) == tuple(
        _START + timedelta(seconds=offset) for offset in range(4)
    )
    assert all(event.occurred_at.tzinfo is UTC for event in history)
    assert clock.calls == 4


def test_naive_clock_and_required_actor_or_reason_fail_closed() -> None:
    naive = ControlledClock(datetime(2035, 1, 2, 3, 4, 5))
    service, repository, _ = _service(clock=naive)

    with pytest.raises(DocumentClockError):
        _register(service)
    assert repository.list() == ()

    valid_service, valid_repository, _ = _service()
    with pytest.raises(InvalidDocumentInputError):
        valid_service.register(
            identity=_IDENTITY,
            version=1,
            sha256=_HASH_ONE,
            actor=" ",
            expected_revision=0,
        )
    assert valid_repository.list() == ()

    pending = _pending(valid_service, _register(valid_service))
    with pytest.raises(InvalidDocumentInputError):
        valid_service.reject(
            identity=_IDENTITY,
            version=1,
            actor=_ACTOR,
            reason=" ",
            expected_revision=pending.revision,
        )
    assert valid_repository.get(_IDENTITY) == pending


def test_repository_lists_document_identities_deterministically() -> None:
    service, _, _ = _service()
    _register(service, identity=_IDENTITY)
    _register(
        service,
        identity=_OTHER_IDENTITY,
        sha256=_HASH_TWO,
    )

    assert tuple(snapshot.document.identity for snapshot in service.list()) == (
        _OTHER_IDENTITY,
        _IDENTITY,
    )
