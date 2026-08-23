"""Pure orchestration for governed, evidence-grounded prescriptions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from threading import BoundedSemaphore, Event, Thread, local
from time import monotonic
from typing import Final, Protocol, cast

from prescriptive_maintenance.contracts import (
    MAX_TOP_K,
    OpaqueNeighbor,
)
from prescriptive_maintenance.contracts import (
    Diagnosis as PublicDiagnosis,
)
from prescriptive_maintenance.generation.contracts import (
    Diagnosis as GenerationDiagnosis,
)
from prescriptive_maintenance.generation.contracts import (
    ProviderUsage,
)
from prescriptive_maintenance.generation.guardrails import (
    GuardedGenerationResult,
    RagGuardrailService,
    RagGuardrailStatus,
    RagRefusalCode,
    SnapshotCurrentnessPort,
)
from prescriptive_maintenance.generation.prompt import (
    GENERATION_SYSTEM_PROMPT_VERSION,
)
from prescriptive_maintenance.generation.provider import (
    GenerationProvider,
    ProviderDisabledError,
    ProviderExecutionError,
    ProviderInvalidResponseError,
    ProviderRequest,
    ProviderResponse,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
    RagKnowledgeRetrievalPort,
)
from prescriptive_maintenance.knowledge_retrieval import canonical_fault_class
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPrediction,
)

MAX_PROVIDER_TIMEOUT_SECONDS: Final = 120.0

_MODEL_ID_PATTERN: Final = re.compile(r"model_[a-z0-9_.-]{3,64}")
_PROVIDER_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class PrescriptionOrchestrationStatus(StrEnum):
    """Closed outcomes of the internal prescription use case."""

    GENERATED = "generated"
    SKIPPED = "skipped"
    REFUSED = "refused"
    DEGRADED = "degraded"


class PrescriptionOrchestrationReason(StrEnum):
    """Safe reason codes that never carry dependency details."""

    NORMAL = "normal"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    UNDOCUMENTED_FAULT = "undocumented_fault"
    NO_EVIDENCE = "no_evidence"
    UNMAPPED_FAULT = "unmapped_fault"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    INVALID_PREDICTION = "invalid_prediction"
    INVALID_REQUEST = "invalid_request"
    GUARDRAIL_REFUSAL = "guardrail_refusal"
    CURRENTNESS_UNAVAILABLE = "currentness_unavailable"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_BUSY = "provider_busy"
    TIMING_UNAVAILABLE = "timing_unavailable"


_NOTICE_TEXT: Final[dict[PrescriptionOrchestrationReason, tuple[str, str]]] = {
    PrescriptionOrchestrationReason.NORMAL: (
        "Prescription generation is not applicable to a normal diagnosis.",
        "Continue routine monitoring.",
    ),
    PrescriptionOrchestrationReason.OUT_OF_DISTRIBUTION: (
        "The model abstained from producing a supported diagnosis.",
        "Review the input and obtain a qualified diagnostic assessment.",
    ),
    PrescriptionOrchestrationReason.UNDOCUMENTED_FAULT: (
        "The diagnosed fault has no documentary retrieval key.",
        "Review approved documentation coverage before prescribing an action.",
    ),
    PrescriptionOrchestrationReason.NO_EVIDENCE: (
        "No eligible documentary evidence is available.",
        "Review approved documentation coverage before prescribing an action.",
    ),
    PrescriptionOrchestrationReason.UNMAPPED_FAULT: (
        "The diagnosed fault has no governed documentary mapping.",
        "Review the approved fault-to-document mapping.",
    ),
    PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE: (
        "Governed documentary retrieval is unavailable.",
        "Retry after the retrieval boundary is healthy.",
    ),
    PrescriptionOrchestrationReason.INVALID_PREDICTION: (
        "The model prediction is invalid for prescription orchestration.",
        "Run the diagnostic step again with a validated model result.",
    ),
    PrescriptionOrchestrationReason.INVALID_REQUEST: (
        "The prescription orchestration request is invalid.",
        "Retry with a supported evidence limit.",
    ),
    PrescriptionOrchestrationReason.GUARDRAIL_REFUSAL: (
        "The grounded generation boundary refused the result.",
        "Follow the structured guardrail refusal before retrying.",
    ),
    PrescriptionOrchestrationReason.CURRENTNESS_UNAVAILABLE: (
        "Documentary currentness could not be verified.",
        "Retry after lifecycle and index validation are available.",
    ),
    PrescriptionOrchestrationReason.PROVIDER_DISABLED: (
        "The generation provider is disabled.",
        "Enable an approved provider configuration before retrying.",
    ),
    PrescriptionOrchestrationReason.PROVIDER_ERROR: (
        "The generation provider is unavailable.",
        "Retry after the provider boundary is healthy.",
    ),
    PrescriptionOrchestrationReason.PROVIDER_TIMEOUT: (
        "The generation provider exceeded the configured time limit.",
        "Retry only after the prior provider call has finished.",
    ),
    PrescriptionOrchestrationReason.PROVIDER_BUSY: (
        "A prior generation call is still in progress.",
        "Wait for the bounded provider slot to become available.",
    ),
    PrescriptionOrchestrationReason.TIMING_UNAVAILABLE: (
        "Generation timing could not be measured safely.",
        "Review the monotonic clock before retrying.",
    ),
}


@dataclass(frozen=True, slots=True)
class PrescriptionOrchestrationConfig:
    """Explicit provider identity and caller-facing timeout policy."""

    provider_id: str
    provider_timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not str
            or _PROVIDER_ID_PATTERN.fullmatch(self.provider_id) is None
        ):
            raise ValueError("Prescription provider identifier is invalid.")
        if (
            type(self.provider_timeout_seconds) is not float
            or not isfinite(self.provider_timeout_seconds)
            or not 0.0 < self.provider_timeout_seconds <= MAX_PROVIDER_TIMEOUT_SECONDS
        ):
            raise ValueError("Prescription provider timeout is invalid.")


@dataclass(frozen=True, slots=True)
class PrescriptionOrchestrationNotice:
    """Allowlisted explanation for a skipped, refused, or degraded result."""

    code: PrescriptionOrchestrationReason
    message: str
    next_action: str

    def __post_init__(self) -> None:
        if type(self.code) is not PrescriptionOrchestrationReason:
            raise ValueError("Prescription orchestration reason is invalid.")
        expected = _NOTICE_TEXT[self.code]
        if (
            type(self.message) is not str
            or self.message != expected[0]
            or type(self.next_action) is not str
            or self.next_action != expected[1]
        ):
            raise ValueError("Prescription orchestration notice is invalid.")


@dataclass(frozen=True, slots=True)
class PrescriptionRunMetadata:
    """Allowlisted audit metadata for one provider attempt."""

    prompt_id: str
    provider_id: str
    latency_ms: float
    usage: ProviderUsage | None

    def __post_init__(self) -> None:
        if (
            type(self.prompt_id) is not str
            or self.prompt_id != GENERATION_SYSTEM_PROMPT_VERSION
        ):
            raise ValueError("Prescription prompt identifier is invalid.")
        if (
            type(self.provider_id) is not str
            or _PROVIDER_ID_PATTERN.fullmatch(self.provider_id) is None
        ):
            raise ValueError("Prescription provider identifier is invalid.")
        if (
            type(self.latency_ms) is not float
            or not isfinite(self.latency_ms)
            or self.latency_ms < 0.0
        ):
            raise ValueError("Prescription latency is invalid.")
        object.__setattr__(
            self,
            "latency_ms",
            0.0 if self.latency_ms == 0.0 else self.latency_ms,
        )
        object.__setattr__(self, "usage", _copy_usage(self.usage))


@dataclass(frozen=True, slots=True)
class PrescriptionOrchestrationResult:
    """Content-free orchestration result preserving validated model context."""

    status: PrescriptionOrchestrationStatus
    disposition: ModelDisposition | None
    diagnosis: PublicDiagnosis | None = field(repr=False)
    support_score: float | None
    model_id: str | None
    neighbors: tuple[OpaqueNeighbor, ...]
    guardrail: GuardedGenerationResult | None = field(repr=False)
    metadata: PrescriptionRunMetadata | None
    notice: PrescriptionOrchestrationNotice | None

    def __post_init__(self) -> None:
        if type(self.status) is not PrescriptionOrchestrationStatus:
            raise ValueError("Prescription orchestration status is invalid.")
        if (
            self.disposition is not None
            and type(self.disposition) is not ModelDisposition
        ):
            raise ValueError("Prescription orchestration disposition is invalid.")

        diagnosis = _copy_public_diagnosis(self.diagnosis)
        if self.diagnosis is not None and diagnosis is None:
            raise ValueError("Prescription orchestration diagnosis is invalid.")
        object.__setattr__(self, "diagnosis", diagnosis)

        if self.support_score is not None and not _is_support_score(self.support_score):
            raise ValueError("Prescription orchestration support is invalid.")
        if self.model_id is not None and not _is_model_id(self.model_id):
            raise ValueError("Prescription orchestration model identifier is invalid.")

        neighbors = _copy_neighbors(self.neighbors)
        if neighbors is None:
            raise ValueError("Prescription orchestration neighbors are invalid.")
        object.__setattr__(self, "neighbors", neighbors)

        has_context = self.disposition is not None
        if has_context != (
            self.support_score is not None and self.model_id is not None
        ):
            raise ValueError("Prescription orchestration model context is incomplete.")
        if not has_context and (diagnosis is not None or neighbors):
            raise ValueError(
                "Invalid orchestration cannot carry partial model context."
            )
        if (self.disposition is ModelDisposition.OUT_OF_DISTRIBUTION) != (
            has_context and diagnosis is None
        ):
            raise ValueError("Prescription orchestration diagnosis shape is invalid.")

        guardrail = self.guardrail
        if guardrail is not None and type(guardrail) is not GuardedGenerationResult:
            raise ValueError("Prescription orchestration guardrail result is invalid.")
        metadata = self.metadata
        if metadata is not None and type(metadata) is not PrescriptionRunMetadata:
            raise ValueError("Prescription orchestration metadata is invalid.")
        if metadata is not None:
            metadata = PrescriptionRunMetadata(
                prompt_id=metadata.prompt_id,
                provider_id=metadata.provider_id,
                latency_ms=metadata.latency_ms,
                usage=metadata.usage,
            )
            object.__setattr__(self, "metadata", metadata)
        notice = self.notice
        if notice is not None and type(notice) is not PrescriptionOrchestrationNotice:
            raise ValueError("Prescription orchestration notice is invalid.")
        if notice is not None:
            notice = PrescriptionOrchestrationNotice(
                code=notice.code,
                message=notice.message,
                next_action=notice.next_action,
            )
            object.__setattr__(self, "notice", notice)
        if metadata is not None and guardrail is None:
            raise ValueError("Prescription metadata requires one guardrail result.")
        if guardrail is not None and not _guardrail_matches_public_diagnosis(
            guardrail,
            diagnosis,
        ):
            raise ValueError("Prescription guardrail diagnosis is inconsistent.")

        if self.status is PrescriptionOrchestrationStatus.GENERATED:
            if (
                self.disposition is not ModelDisposition.FAULT
                or guardrail is None
                or guardrail.status is not RagGuardrailStatus.ACCEPTED
                or metadata is None
                or notice is not None
            ):
                raise ValueError("Generated prescription result is incomplete.")
            return
        if notice is None:
            raise ValueError("Non-generated prescription result requires a notice.")
        if self.status is PrescriptionOrchestrationStatus.SKIPPED:
            _validate_skipped_result(self.disposition, notice.code, guardrail, metadata)
            return
        if self.status is PrescriptionOrchestrationStatus.REFUSED:
            _validate_refused_result(
                self.disposition,
                notice.code,
                guardrail,
                metadata,
            )
            return
        _validate_degraded_result(
            self.disposition,
            notice.code,
            guardrail,
            metadata,
        )


class _ProviderAttemptOutcome(StrEnum):
    NOT_CALLED = "not_called"
    COMPLETED = "completed"
    DISABLED = "disabled"
    FAILED = "failed"
    INVALID_RESPONSE = "invalid_response"
    TIMED_OUT = "timed_out"
    BUSY = "busy"


class _ProviderFailure(StrEnum):
    DISABLED = "disabled"
    FAILED = "failed"
    INVALID_RESPONSE = "invalid_response"


@dataclass(slots=True)
class _ProviderCallState:
    done: Event = field(default_factory=Event, repr=False)
    response: ProviderResponse | None = field(default=None, repr=False)
    failure: _ProviderFailure | None = None


class _BoundedGenerationProvider:
    """Bound one synchronous provider call without queuing or retrying it."""

    def __init__(
        self,
        *,
        provider: GenerationProvider,
        timeout_seconds: float,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._capacity = BoundedSemaphore(value=1)
        self._attempt = local()

    def reset_attempt(self) -> None:
        self._attempt.outcome = _ProviderAttemptOutcome.NOT_CALLED
        self._attempt.usage = None

    def attempt_outcome(self) -> _ProviderAttemptOutcome:
        outcome = getattr(
            self._attempt,
            "outcome",
            _ProviderAttemptOutcome.NOT_CALLED,
        )
        return (
            outcome
            if type(outcome) is _ProviderAttemptOutcome
            else _ProviderAttemptOutcome.NOT_CALLED
        )

    def attempt_usage(self) -> ProviderUsage | None:
        return _copy_usage(getattr(self._attempt, "usage", None))

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        try:
            acquired = self._capacity.acquire(blocking=False)
        except Exception:
            self._attempt.outcome = _ProviderAttemptOutcome.FAILED
            raise ProviderExecutionError("Generation provider failed.") from None
        if not acquired:
            self._attempt.outcome = _ProviderAttemptOutcome.BUSY
            raise ProviderExecutionError("Generation provider is busy.")

        try:
            state = _ProviderCallState()
            worker = Thread(
                target=self._run_provider,
                args=(request, state),
                name="bounded-generation-provider",
                daemon=True,
            )
            worker.start()
        except Exception:
            self._capacity.release()
            self._attempt.outcome = _ProviderAttemptOutcome.FAILED
            raise ProviderExecutionError("Generation provider failed.") from None
        except BaseException:
            self._capacity.release()
            raise

        try:
            finished = state.done.wait(self._timeout_seconds)
        except Exception:
            self._attempt.outcome = _ProviderAttemptOutcome.FAILED
            raise ProviderExecutionError("Generation provider failed.") from None
        if not finished:
            self._attempt.outcome = _ProviderAttemptOutcome.TIMED_OUT
            raise ProviderExecutionError("Generation provider timed out.")

        if state.failure is _ProviderFailure.DISABLED:
            self._attempt.outcome = _ProviderAttemptOutcome.DISABLED
            raise ProviderDisabledError("Generation provider is disabled.")
        if state.failure is _ProviderFailure.INVALID_RESPONSE:
            self._attempt.outcome = _ProviderAttemptOutcome.INVALID_RESPONSE
            raise ProviderInvalidResponseError(
                "Generation provider returned invalid output."
            )
        if state.failure is not None or state.response is None:
            self._attempt.outcome = _ProviderAttemptOutcome.FAILED
            raise ProviderExecutionError("Generation provider failed.")

        self._attempt.outcome = _ProviderAttemptOutcome.COMPLETED
        self._attempt.usage = _copy_usage(state.response.usage)
        return state.response

    def _run_provider(
        self,
        request: ProviderRequest,
        state: _ProviderCallState,
    ) -> None:
        try:
            response = cast(object, self._provider.generate(request))
            if type(response) is not ProviderResponse:
                state.failure = _ProviderFailure.INVALID_RESPONSE
                return
            state.response = ProviderResponse(
                output_text=response.output_text,
                usage=_copy_usage(response.usage),
            )
        except ProviderDisabledError:
            state.failure = _ProviderFailure.DISABLED
        except ProviderInvalidResponseError:
            state.failure = _ProviderFailure.INVALID_RESPONSE
        except BaseException:
            state.failure = _ProviderFailure.FAILED
        finally:
            self._capacity.release()
            state.done.set()


class MonotonicClock(Protocol):
    """Injected monotonic clock used only for allowlisted latency."""

    def __call__(self) -> float: ...


@dataclass(frozen=True, slots=True)
class _PredictionSnapshot:
    disposition: ModelDisposition
    diagnosis: PublicDiagnosis | None = field(repr=False)
    support_score: float
    model_id: str
    neighbors: tuple[OpaqueNeighbor, ...]
    retrieval_key: str | None


class PrescriptionOrchestrationService:
    """Compose governed retrieval and RAG guardrails without persistence."""

    def __init__(
        self,
        *,
        retrieval: RagKnowledgeRetrievalPort,
        provider: GenerationProvider,
        snapshot_currentness: SnapshotCurrentnessPort,
        config: PrescriptionOrchestrationConfig,
        monotonic_clock: MonotonicClock = monotonic,
    ) -> None:
        if type(config) is not PrescriptionOrchestrationConfig:
            raise ValueError("Prescription orchestration configuration is invalid.")
        try:
            clock = cast(Callable[[], object], monotonic_clock)
            if not callable(clock):
                raise TypeError
        except Exception:
            raise ValueError("Prescription monotonic clock is invalid.") from None

        self._retrieval = retrieval
        self._config = PrescriptionOrchestrationConfig(
            provider_id=config.provider_id,
            provider_timeout_seconds=config.provider_timeout_seconds,
        )
        self._clock = clock
        self._bounded_provider = _BoundedGenerationProvider(
            provider=provider,
            timeout_seconds=self._config.provider_timeout_seconds,
        )
        self._guardrails = RagGuardrailService(
            provider=self._bounded_provider,
            snapshot_currentness=snapshot_currentness,
        )

    def orchestrate(
        self,
        prediction: object,
        *,
        top_k: object,
    ) -> PrescriptionOrchestrationResult:
        """Return a total, typed decision with at most one provider attempt."""

        clean_prediction = _copy_prediction(prediction)
        if clean_prediction is None:
            return _result_without_context(
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.INVALID_PREDICTION,
            )
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.INVALID_REQUEST,
            )
        if clean_prediction.disposition is ModelDisposition.NORMAL:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.SKIPPED,
                PrescriptionOrchestrationReason.NORMAL,
            )
        if clean_prediction.disposition is ModelDisposition.OUT_OF_DISTRIBUTION:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.SKIPPED,
                PrescriptionOrchestrationReason.OUT_OF_DISTRIBUTION,
            )

        retrieval_key = clean_prediction.retrieval_key
        if retrieval_key is None:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.SKIPPED,
                PrescriptionOrchestrationReason.UNDOCUMENTED_FAULT,
            )
        generation_diagnosis = _generation_diagnosis(clean_prediction)
        if generation_diagnosis is None:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.INVALID_PREDICTION,
            )

        retrieval = self._retrieve(
            fault_class=retrieval_key,
            top_k=top_k,
        )
        if retrieval is None:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE,
            )
        if retrieval.status is GovernedRetrievalStatus.NO_EVIDENCE:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.SKIPPED,
                PrescriptionOrchestrationReason.NO_EVIDENCE,
            )
        if retrieval.status is GovernedRetrievalStatus.UNMAPPED_FAULT:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.SKIPPED,
                PrescriptionOrchestrationReason.UNMAPPED_FAULT,
            )
        if retrieval.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE,
            )

        started_at = _clock_value(self._clock)
        if started_at is None:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.TIMING_UNAVAILABLE,
            )

        self._bounded_provider.reset_attempt()
        guarded = self._guardrails.generate(
            diagnosis=generation_diagnosis,
            retrieval=retrieval,
        )
        outcome = self._bounded_provider.attempt_outcome()
        finished_at = _clock_value(self._clock)
        latency_ms = _latency_ms(started_at, finished_at)
        if latency_ms is None:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.TIMING_UNAVAILABLE,
            )

        metadata = (
            None
            if outcome
            in {_ProviderAttemptOutcome.NOT_CALLED, _ProviderAttemptOutcome.BUSY}
            else PrescriptionRunMetadata(
                prompt_id=GENERATION_SYSTEM_PROMPT_VERSION,
                provider_id=self._config.provider_id,
                latency_ms=latency_ms,
                usage=self._bounded_provider.attempt_usage(),
            )
        )

        if outcome is _ProviderAttemptOutcome.TIMED_OUT:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.PROVIDER_TIMEOUT,
                guardrail=guarded,
                metadata=metadata,
            )
        if outcome is _ProviderAttemptOutcome.BUSY:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.PROVIDER_BUSY,
                guardrail=guarded,
            )
        if outcome is _ProviderAttemptOutcome.DISABLED:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.PROVIDER_DISABLED,
                guardrail=guarded,
                metadata=metadata,
            )
        if outcome is _ProviderAttemptOutcome.FAILED:
            return _result(
                clean_prediction,
                PrescriptionOrchestrationStatus.DEGRADED,
                PrescriptionOrchestrationReason.PROVIDER_ERROR,
                guardrail=guarded,
                metadata=metadata,
            )
        if guarded.status is RagGuardrailStatus.ACCEPTED:
            return _generated_result(
                clean_prediction,
                guardrail=guarded,
                metadata=metadata,
            )

        refusal_code = guarded.refusal.code if guarded.refusal is not None else None
        if refusal_code is RagRefusalCode.CURRENTNESS_UNAVAILABLE:
            status = PrescriptionOrchestrationStatus.DEGRADED
            reason = PrescriptionOrchestrationReason.CURRENTNESS_UNAVAILABLE
        else:
            status = PrescriptionOrchestrationStatus.REFUSED
            reason = PrescriptionOrchestrationReason.GUARDRAIL_REFUSAL
        return _result(
            clean_prediction,
            status,
            reason,
            guardrail=guarded,
            metadata=metadata,
        )

    def _retrieve(
        self,
        *,
        fault_class: str,
        top_k: int,
    ) -> GovernedRetrievalResult | None:
        try:
            method = self._retrieval.retrieve
            if not callable(method):
                return None
            raw = cast(
                object,
                method(
                    disposition=ModelDisposition.FAULT,
                    fault_class=fault_class,
                    top_k=top_k,
                ),
            )
            return _copy_retrieval(raw)
        except Exception:
            return None


def _copy_prediction(value: object) -> _PredictionSnapshot | None:
    try:
        if type(value) is not ModelPrediction:
            return None
        disposition = value.disposition
        abstention_reason = value.abstention_reason
        diagnosis = _copy_public_diagnosis(value.diagnosis)
        neighbors = _copy_neighbors(value.neighbors)
        if (
            type(disposition) is not ModelDisposition
            or not _is_support_score(value.support_score)
            or not _is_model_id(value.model_id)
            or neighbors is None
            or (
                abstention_reason is not None
                and type(abstention_reason) is not ModelAbstentionReason
            )
        ):
            return None

        retrieval_key = value.retrieval_key
        if retrieval_key is not None:
            retrieval_key = canonical_fault_class(retrieval_key)

        if disposition is ModelDisposition.OUT_OF_DISTRIBUTION:
            if (
                diagnosis is not None
                or retrieval_key is not None
                or abstention_reason is None
            ):
                return None
        elif disposition is ModelDisposition.NORMAL:
            if (
                diagnosis is None
                or retrieval_key is not None
                or abstention_reason is not None
            ):
                return None
        elif disposition is ModelDisposition.FAULT:
            if diagnosis is None or abstention_reason is not None:
                return None
        else:
            return None

        return _PredictionSnapshot(
            disposition=disposition,
            diagnosis=diagnosis,
            support_score=value.support_score,
            model_id=value.model_id,
            neighbors=neighbors,
            retrieval_key=retrieval_key,
        )
    except Exception:
        return None


def _copy_public_diagnosis(value: object) -> PublicDiagnosis | None:
    try:
        if type(value) is not PublicDiagnosis:
            return None
        return PublicDiagnosis(code=value.code, summary=value.summary)
    except Exception:
        return None


def _copy_neighbors(value: object) -> tuple[OpaqueNeighbor, ...] | None:
    try:
        if type(value) is not tuple:
            return None
        items = cast(tuple[object, ...], value)
        if len(items) > MAX_TOP_K:
            return None
        copied: list[OpaqueNeighbor] = []
        for item in items:
            if type(item) is not OpaqueNeighbor:
                return None
            copied.append(
                OpaqueNeighbor(
                    neighbor_ref=item.neighbor_ref,
                    rank=item.rank,
                    fault_code=item.fault_code,
                    distance=item.distance,
                )
            )
        result = tuple(copied)
        if tuple(item.rank for item in result) != tuple(range(1, len(result) + 1)):
            return None
        references = tuple(item.neighbor_ref for item in result)
        if len(references) != len(set(references)):
            return None
        return result
    except Exception:
        return None


def _copy_retrieval(value: object) -> GovernedRetrievalResult | None:
    try:
        if type(value) is not GovernedRetrievalResult:
            return None
        return GovernedRetrievalResult(
            status=value.status,
            fault_class=value.fault_class,
            policy_schema_version=value.policy_schema_version,
            policy_version=value.policy_version,
            minimum_score=value.minimum_score,
            policy_sha256=value.policy_sha256,
            mapping_version=value.mapping_version,
            mapping_sha256=value.mapping_sha256,
            evidence=value.evidence,
        )
    except Exception:
        return None


def _generation_diagnosis(
    prediction: _PredictionSnapshot,
) -> GenerationDiagnosis | None:
    try:
        if prediction.diagnosis is None or prediction.retrieval_key is None:
            return None
        return GenerationDiagnosis(
            fault_code=prediction.retrieval_key,
            technical_summary=prediction.diagnosis.summary,
        )
    except Exception:
        return None


def _copy_usage(value: object) -> ProviderUsage | None:
    try:
        if type(value) is not ProviderUsage:
            return None
        return ProviderUsage(
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            total_tokens=value.total_tokens,
        )
    except Exception:
        return None


def _clock_value(clock: Callable[[], object]) -> float | None:
    try:
        value = clock()
    except Exception:
        return None
    return value if type(value) is float and isfinite(value) else None


def _latency_ms(started_at: float, finished_at: float | None) -> float | None:
    if finished_at is None or finished_at < started_at:
        return None
    elapsed = finished_at - started_at
    if not isfinite(elapsed):
        return None
    latency = elapsed * 1_000.0
    if not isfinite(latency):
        return None
    return 0.0 if latency == 0.0 else latency


def _validate_skipped_result(
    disposition: ModelDisposition | None,
    reason: PrescriptionOrchestrationReason,
    guardrail: GuardedGenerationResult | None,
    metadata: PrescriptionRunMetadata | None,
) -> None:
    expected_dispositions = {
        PrescriptionOrchestrationReason.NORMAL: ModelDisposition.NORMAL,
        PrescriptionOrchestrationReason.OUT_OF_DISTRIBUTION: (
            ModelDisposition.OUT_OF_DISTRIBUTION
        ),
        PrescriptionOrchestrationReason.UNDOCUMENTED_FAULT: ModelDisposition.FAULT,
        PrescriptionOrchestrationReason.NO_EVIDENCE: ModelDisposition.FAULT,
        PrescriptionOrchestrationReason.UNMAPPED_FAULT: ModelDisposition.FAULT,
    }
    if (
        guardrail is not None
        or metadata is not None
        or expected_dispositions.get(reason) is not disposition
    ):
        raise ValueError("Skipped prescription result has an invalid state.")


def _validate_refused_result(
    disposition: ModelDisposition | None,
    reason: PrescriptionOrchestrationReason,
    guardrail: GuardedGenerationResult | None,
    metadata: PrescriptionRunMetadata | None,
) -> None:
    refusal_code = _guardrail_refusal_code(guardrail)
    degraded_codes = {
        RagRefusalCode.CURRENTNESS_UNAVAILABLE,
        RagRefusalCode.RETRIEVAL_UNAVAILABLE,
        RagRefusalCode.PROVIDER_DISABLED,
        RagRefusalCode.PROVIDER_ERROR,
    }
    post_response_codes = {
        RagRefusalCode.INVALID_PROVIDER_OUTPUT,
        RagRefusalCode.INSUFFICIENT_EVIDENCE,
    }
    if refusal_code in post_response_codes:
        metadata_shape_is_valid = metadata is not None
    elif refusal_code is RagRefusalCode.STALE_EVIDENCE:
        metadata_shape_is_valid = True
    else:
        metadata_shape_is_valid = metadata is None
    if (
        disposition is not ModelDisposition.FAULT
        or reason is not PrescriptionOrchestrationReason.GUARDRAIL_REFUSAL
        or refusal_code is None
        or refusal_code in degraded_codes
        or not metadata_shape_is_valid
    ):
        raise ValueError("Refused prescription result has an invalid state.")


def _validate_degraded_result(
    disposition: ModelDisposition | None,
    reason: PrescriptionOrchestrationReason,
    guardrail: GuardedGenerationResult | None,
    metadata: PrescriptionRunMetadata | None,
) -> None:
    if reason is PrescriptionOrchestrationReason.INVALID_PREDICTION:
        valid = disposition is None and guardrail is None and metadata is None
    elif reason is PrescriptionOrchestrationReason.INVALID_REQUEST:
        valid = disposition is not None and guardrail is None and metadata is None
    elif reason in {
        PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE,
        PrescriptionOrchestrationReason.TIMING_UNAVAILABLE,
    }:
        valid = (
            disposition is ModelDisposition.FAULT
            and guardrail is None
            and metadata is None
        )
    elif reason is PrescriptionOrchestrationReason.CURRENTNESS_UNAVAILABLE:
        valid = (
            disposition is ModelDisposition.FAULT
            and _guardrail_refusal_code(guardrail)
            is RagRefusalCode.CURRENTNESS_UNAVAILABLE
        )
    elif reason is PrescriptionOrchestrationReason.PROVIDER_DISABLED:
        valid = (
            disposition is ModelDisposition.FAULT
            and _guardrail_refusal_code(guardrail) is RagRefusalCode.PROVIDER_DISABLED
            and metadata is not None
        )
    elif reason in {
        PrescriptionOrchestrationReason.PROVIDER_ERROR,
        PrescriptionOrchestrationReason.PROVIDER_TIMEOUT,
    }:
        valid = (
            disposition is ModelDisposition.FAULT
            and _guardrail_refusal_code(guardrail) is RagRefusalCode.PROVIDER_ERROR
            and metadata is not None
        )
    elif reason is PrescriptionOrchestrationReason.PROVIDER_BUSY:
        valid = (
            disposition is ModelDisposition.FAULT
            and _guardrail_refusal_code(guardrail) is RagRefusalCode.PROVIDER_ERROR
            and metadata is None
        )
    else:
        valid = False
    if not valid:
        raise ValueError("Degraded prescription result has an invalid state.")


def _guardrail_refusal_code(
    guardrail: GuardedGenerationResult | None,
) -> RagRefusalCode | None:
    try:
        if (
            guardrail is None
            or type(guardrail) is not GuardedGenerationResult
            or guardrail.status is not RagGuardrailStatus.REFUSED
            or guardrail.diagnosis is None
            or guardrail.generation is not None
            or guardrail.refusal is None
            or type(guardrail.refusal.code) is not RagRefusalCode
        ):
            return None
        return guardrail.refusal.code
    except Exception:
        return None


def _guardrail_matches_public_diagnosis(
    guardrail: GuardedGenerationResult,
    diagnosis: PublicDiagnosis | None,
) -> bool:
    try:
        guarded_diagnosis = guardrail.diagnosis
        return (
            diagnosis is not None
            and type(guarded_diagnosis) is GenerationDiagnosis
            and canonical_fault_class(guarded_diagnosis.fault_code)
            == guarded_diagnosis.fault_code
            and guarded_diagnosis.technical_summary == diagnosis.summary
        )
    except Exception:
        return False


def _is_support_score(value: object) -> bool:
    return type(value) is float and isfinite(value) and 0.0 <= value <= 1.0


def _is_model_id(value: object) -> bool:
    return type(value) is str and _MODEL_ID_PATTERN.fullmatch(value) is not None


def _notice(reason: PrescriptionOrchestrationReason) -> PrescriptionOrchestrationNotice:
    message, next_action = _NOTICE_TEXT[reason]
    return PrescriptionOrchestrationNotice(
        code=reason,
        message=message,
        next_action=next_action,
    )


def _result_without_context(
    status: PrescriptionOrchestrationStatus,
    reason: PrescriptionOrchestrationReason,
) -> PrescriptionOrchestrationResult:
    return PrescriptionOrchestrationResult(
        status=status,
        disposition=None,
        diagnosis=None,
        support_score=None,
        model_id=None,
        neighbors=(),
        guardrail=None,
        metadata=None,
        notice=_notice(reason),
    )


def _result(
    prediction: _PredictionSnapshot,
    status: PrescriptionOrchestrationStatus,
    reason: PrescriptionOrchestrationReason,
    *,
    guardrail: GuardedGenerationResult | None = None,
    metadata: PrescriptionRunMetadata | None = None,
) -> PrescriptionOrchestrationResult:
    return PrescriptionOrchestrationResult(
        status=status,
        disposition=prediction.disposition,
        diagnosis=prediction.diagnosis,
        support_score=prediction.support_score,
        model_id=prediction.model_id,
        neighbors=prediction.neighbors,
        guardrail=guardrail,
        metadata=metadata,
        notice=_notice(reason),
    )


def _generated_result(
    prediction: _PredictionSnapshot,
    *,
    guardrail: GuardedGenerationResult,
    metadata: PrescriptionRunMetadata | None,
) -> PrescriptionOrchestrationResult:
    if metadata is None:
        return _result(
            prediction,
            PrescriptionOrchestrationStatus.DEGRADED,
            PrescriptionOrchestrationReason.TIMING_UNAVAILABLE,
        )
    return PrescriptionOrchestrationResult(
        status=PrescriptionOrchestrationStatus.GENERATED,
        disposition=prediction.disposition,
        diagnosis=prediction.diagnosis,
        support_score=prediction.support_score,
        model_id=prediction.model_id,
        neighbors=prediction.neighbors,
        guardrail=guardrail,
        metadata=metadata,
        notice=None,
    )
