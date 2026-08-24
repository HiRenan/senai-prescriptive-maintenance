"""Synthetic adversarial tests for prescription orchestration."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from hashlib import sha256
from threading import Barrier, Event, Lock
from typing import cast

import pandas as pd
import pytest
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    AnalysisFeatures,
    Diagnosis,
    OpaqueNeighbor,
)
from prescriptive_maintenance.generation import (
    GENERATION_CONTRACT_VERSION,
    GENERATION_SYSTEM_PROMPT_VERSION,
    FakeGenerationProvider,
    ProviderDisabledError,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    RagRefusalCode,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalBinding,
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import RankedKnowledgeSnapshot
from prescriptive_maintenance.modeling.knn import (
    KnnModelPortAdapter,
    fit_knn_model,
)
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPrediction,
)
from prescriptive_maintenance.prescription_orchestration import (
    MAX_PROVIDER_TIMEOUT_SECONDS,
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationReason,
    PrescriptionOrchestrationResult,
    PrescriptionOrchestrationService,
    PrescriptionOrchestrationStatus,
)

_PUBLIC_FAULT_CODE = "synthetic_fault"
_FAULT_CLASS = "synthetic-bearing-warning"
_DOC_ID = f"doc_{'a' * 64}"
_DOC_VERSION = f"docver_{'b' * 64}"
_CHUNK_ID = f"chunk_{'c' * 64}"
_SECTION_ID = f"section_{'d' * 64}"
_MAPPING_VERSION = "synthetic-fault-knowledge.v1"
_MAPPING_SHA256 = "e" * 64
_POLICY = build_governed_retrieval_policy(
    policy_version="synthetic-rag-policy.v1",
    minimum_score=0.75,
)


def _prediction(
    *,
    disposition: ModelDisposition = ModelDisposition.FAULT,
    retrieval_key: str | None = _FAULT_CLASS,
) -> ModelPrediction:
    is_ood = disposition is ModelDisposition.OUT_OF_DISTRIBUTION
    diagnosis = (
        None
        if is_ood
        else Diagnosis(
            code=_PUBLIC_FAULT_CODE,
            summary="Synthetic immutable diagnostic summary.",
        )
    )
    return ModelPrediction(
        disposition=disposition,
        abstention_reason=(
            ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION if is_ood else None
        ),
        diagnosis=diagnosis,
        support_score=0.83,
        model_id="model_synthetic_v1",
        neighbors=(
            OpaqueNeighbor(
                neighbor_ref="neighbor_synthetic_01",
                rank=1,
                fault_code=_PUBLIC_FAULT_CODE,
                distance=0.25,
            ),
        ),
        retrieval_key=(
            None
            if disposition
            in {
                ModelDisposition.NORMAL,
                ModelDisposition.OUT_OF_DISTRIBUTION,
            }
            else retrieval_key
        ),
    )


def _knn_operating_prediction(operating_label: str) -> ModelPrediction:
    rows: list[dict[str, object]] = []
    for first_value, label in (
        (0.0, operating_label),
        (10.0, "synthetic-problem"),
    ):
        row: dict[str, object] = {
            name: float(first_value if position == 0 else position + 1)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
        row["y"] = label
        rows.append(row)
    training = pd.DataFrame(rows, columns=(*ANALYSIS_FEATURE_NAMES, "y"))
    training.loc[:, list(ANALYSIS_FEATURE_NAMES)] = training.loc[
        :, list(ANALYSIS_FEATURE_NAMES)
    ].astype("float64")
    training["y"] = training["y"].astype("string")
    model = fit_knn_model(
        training,
        dataset_id="a" * 64,
        training_partition_sha256="b" * 64,
        default_top_k=1,
        minimum_class_count=1,
    )
    features = AnalysisFeatures.model_validate(
        {
            name: float(0.0 if position == 0 else position + 1)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
    )
    return KnnModelPortAdapter(model).predict(features, top_k=1)


def _snapshot(
    *,
    content: str = "Synthetic approved evidence for a controlled test.",
) -> RankedKnowledgeSnapshot:
    return RankedKnowledgeSnapshot(
        document_id=_DOC_ID,
        document_version=_DOC_VERSION,
        chunk_id=_CHUNK_ID,
        page_number=2,
        section_id=_SECTION_ID,
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        score=0.91,
    )


def _retrieval_result(
    status: GovernedRetrievalStatus = GovernedRetrievalStatus.EVIDENCE,
    *,
    content: str = "Synthetic approved evidence for a controlled test.",
) -> GovernedRetrievalResult:
    return GovernedRetrievalResult(
        status=status,
        fault_class=_FAULT_CLASS,
        policy_schema_version=_POLICY.schema_version,
        policy_version=_POLICY.policy_version,
        minimum_score=_POLICY.minimum_score,
        policy_sha256=_POLICY.policy_sha256,
        mapping_version=_MAPPING_VERSION,
        mapping_sha256=_MAPPING_SHA256,
        evidence=(_snapshot(content=content),)
        if status is GovernedRetrievalStatus.EVIDENCE
        else (),
    )


def _valid_output_text(
    *,
    narrative: str = "Synthetic evidence supports controlled inspection.",
) -> str:
    citation = {"evidence_id": _CHUNK_ID}
    return json.dumps(
        {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": _FAULT_CLASS,
                "status": "supported",
                "assessment": narrative,
                "citations": [citation],
            },
            "prescriptions": [
                {
                    "action": narrative,
                    "rationale": narrative,
                    "citations": [citation],
                }
            ],
            "warnings": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _insufficient_output_text() -> str:
    return json.dumps(
        {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": _FAULT_CLASS,
                "status": "insufficient_evidence",
                "assessment": None,
                "citations": [],
            },
            "prescriptions": [],
            "warnings": [
                {
                    "code": "evidence_gap",
                    "message": "Synthetic evidence is insufficient.",
                    "citations": [],
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(slots=True)
class _Retrieval:
    result: object = field(default_factory=_retrieval_result)
    failure: BaseException | None = None
    calls: int = 0
    binding: GovernedRetrievalBinding = field(
        default_factory=lambda: GovernedRetrievalBinding(
            policy_schema_version=_POLICY.schema_version,
            policy_version=_POLICY.policy_version,
            policy_sha256=_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
        )
    )

    @property
    def runtime_binding(self) -> GovernedRetrievalBinding:
        return replace(self.binding)

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        assert disposition is ModelDisposition.FAULT
        assert fault_class == _FAULT_CLASS
        assert 1 <= top_k <= 10
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return cast(GovernedRetrievalResult, self.result)


@dataclass(slots=True)
class _Currentness:
    states: list[object] = field(default_factory=lambda: list[object]())
    calls: int = 0

    def snapshots_are_current(self, **kwargs: object) -> bool | None:
        assert kwargs["fault_class"] == _FAULT_CLASS
        assert kwargs["evidence"]
        self.calls += 1
        if not self.states:
            return True
        state = self.states.pop(0)
        if isinstance(state, BaseException):
            raise state
        return cast(bool | None, state)


class _RecordingProvider:
    def __init__(
        self,
        *,
        output_text: str | None = None,
        usage: object = None,
        failure: BaseException | None = None,
        response: object | None = None,
    ) -> None:
        self._output_text = output_text or _valid_output_text()
        self._usage = (
            ProviderUsage(input_tokens=10, output_tokens=6, total_tokens=16)
            if usage is None
            else usage
        )
        self._failure = failure
        self._response = response
        self._lock = Lock()
        self.requests: list[ProviderRequest] = []

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        with self._lock:
            self.requests.append(request)
        if self._failure is not None:
            raise self._failure
        if self._response is not None:
            return cast(ProviderResponse, self._response)
        return ProviderResponse(
            output_text=self._output_text,
            usage=cast(ProviderUsage | None, self._usage),
        )


class _BlockingProvider(_RecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.finished = Event()

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        with self._lock:
            self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=2.0)
        self.finished.set()
        return ProviderResponse(output_text=self._output_text)


class _Clock:
    def __init__(self, values: list[object]) -> None:
        self._values = values
        self._lock = Lock()

    def __call__(self) -> float:
        with self._lock:
            value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return cast(float, value)


def _service(
    *,
    retrieval: object | None = None,
    provider: object | None = None,
    currentness: object | None = None,
    timeout: float = 0.5,
    clock: object | None = None,
) -> PrescriptionOrchestrationService:
    return PrescriptionOrchestrationService(
        retrieval=cast(_Retrieval, retrieval or _Retrieval()),
        provider=cast(_RecordingProvider, provider or _RecordingProvider()),
        snapshot_currentness=cast(
            _Currentness,
            currentness or _Currentness(),
        ),
        config=PrescriptionOrchestrationConfig(
            provider_id="fake-generation.v1",
            provider_timeout_seconds=timeout,
        ),
        monotonic_clock=cast(_Clock, clock or _Clock([1.0, 1.025])),
    )


def test_runtime_binding_includes_effective_retrieval_dependency() -> None:
    retrieval = _Retrieval()

    binding = _service(retrieval=retrieval).runtime_binding

    assert binding.prompt_id == f"prompt_{GENERATION_SYSTEM_PROMPT_VERSION}"
    assert binding.provider_id == "fake-generation.v1"
    assert binding.provider_timeout_seconds == 0.5
    assert binding.retrieval_policy_version == retrieval.binding.policy_version
    assert binding.retrieval_policy_sha256 == retrieval.binding.policy_sha256
    assert binding.mapping_version == retrieval.binding.mapping_version
    assert binding.mapping_sha256 == retrieval.binding.mapping_sha256


def test_retrieval_result_identity_mismatch_blocks_provider() -> None:
    provider = _RecordingProvider()
    different_policy = build_governed_retrieval_policy(
        policy_version="synthetic-rag-policy.v2",
        minimum_score=0.8,
    )
    retrieval = _Retrieval(
        binding=GovernedRetrievalBinding(
            policy_schema_version=different_policy.schema_version,
            policy_version=different_policy.policy_version,
            policy_sha256=different_policy.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
        )
    )

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        _prediction(),
        top_k=2,
    )

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE
    assert provider.call_count == 0


def test_documented_fault_generates_once_with_allowlisted_metadata() -> None:
    retrieval = _Retrieval()
    provider = _RecordingProvider()
    currentness = _Currentness()

    result = _service(
        retrieval=retrieval,
        provider=provider,
        currentness=currentness,
        clock=_Clock([20.0, 20.125]),
    ).orchestrate(_prediction(), top_k=3)

    assert result.status is PrescriptionOrchestrationStatus.GENERATED
    assert result.notice is None
    assert result.disposition is ModelDisposition.FAULT
    assert result.diagnosis == _prediction().diagnosis
    assert result.neighbors == _prediction().neighbors
    assert result.guardrail is not None
    assert result.guardrail.generation is not None
    assert result.guardrail.generation.diagnosis.fault_code == _FAULT_CLASS
    assert result.guardrail.generation.diagnostic_support is not None
    assert result.guardrail.generation.diagnostic_support.citations[0].evidence_id == (
        _CHUNK_ID
    )
    assert result.metadata is not None
    assert result.metadata.prompt_id == GENERATION_SYSTEM_PROMPT_VERSION
    assert result.metadata.provider_id == "fake-generation.v1"
    assert result.metadata.latency_ms == 125.0
    assert result.metadata.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=6,
        total_tokens=16,
    )
    assert retrieval.calls == 1
    assert provider.call_count == 1
    assert currentness.calls == 2
    assert provider.requests[0].prompt_version == GENERATION_SYSTEM_PROMPT_VERSION


def test_existing_offline_fake_composes_without_network_or_credentials() -> None:
    provider = FakeGenerationProvider()

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.GENERATED
    assert result.metadata is not None
    assert result.metadata.usage == ProviderUsage(
        input_tokens=21,
        output_tokens=13,
        total_tokens=34,
    )
    assert provider.call_count == 1


@pytest.mark.parametrize(
    ("prediction", "reason"),
    (
        (
            _prediction(disposition=ModelDisposition.NORMAL),
            PrescriptionOrchestrationReason.NORMAL,
        ),
        (
            _prediction(disposition=ModelDisposition.OUT_OF_DISTRIBUTION),
            PrescriptionOrchestrationReason.OUT_OF_DISTRIBUTION,
        ),
        (
            _prediction(retrieval_key=None),
            PrescriptionOrchestrationReason.UNDOCUMENTED_FAULT,
        ),
    ),
)
def test_non_documented_model_outcomes_skip_retrieval_and_provider(
    prediction: ModelPrediction,
    reason: PrescriptionOrchestrationReason,
) -> None:
    retrieval = _Retrieval()
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        prediction,
        top_k=2,
    )

    assert result.status is PrescriptionOrchestrationStatus.SKIPPED
    assert result.notice is not None
    assert result.notice.code is reason
    assert result.diagnosis == prediction.diagnosis
    assert result.neighbors == prediction.neighbors
    assert result.guardrail is None
    assert result.metadata is None
    assert retrieval.calls == 0
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("operating_label", "canonical_state"),
    (
        ("NÓRMAL", "normal"),
        ("BÁSELÍNE", "baseline"),
        ("TÉSTE", "teste"),
        ("ACELERÁNDO", "acelerando"),
        ("MÓTOR-DESLIGÁDO", "motor_desligado"),
    ),
)
def test_knn_operating_state_never_calls_retrieval_or_provider(
    operating_label: str,
    canonical_state: str,
) -> None:
    prediction = _knn_operating_prediction(operating_label)
    retrieval = _Retrieval()
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        prediction,
        top_k=1,
    )

    assert prediction.disposition is ModelDisposition.NORMAL
    assert prediction.diagnosis is not None
    assert prediction.diagnosis.code == f"operating_state_{canonical_state}"
    assert prediction.retrieval_key is None
    assert prediction.neighbors
    assert all(
        neighbor.fault_code == f"operating_state_{canonical_state}"
        for neighbor in prediction.neighbors
    )
    assert result.status is PrescriptionOrchestrationStatus.SKIPPED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.NORMAL
    assert result.diagnosis == prediction.diagnosis
    assert result.guardrail is None
    assert result.metadata is None
    assert retrieval.calls == 0
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("retrieval_status", "status", "reason"),
    (
        (
            GovernedRetrievalStatus.NO_EVIDENCE,
            PrescriptionOrchestrationStatus.SKIPPED,
            PrescriptionOrchestrationReason.NO_EVIDENCE,
        ),
        (
            GovernedRetrievalStatus.UNMAPPED_FAULT,
            PrescriptionOrchestrationStatus.SKIPPED,
            PrescriptionOrchestrationReason.UNMAPPED_FAULT,
        ),
        (
            GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
            PrescriptionOrchestrationStatus.DEGRADED,
            PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE,
        ),
    ),
)
def test_empty_governed_states_never_call_provider(
    retrieval_status: GovernedRetrievalStatus,
    status: PrescriptionOrchestrationStatus,
    reason: PrescriptionOrchestrationReason,
) -> None:
    retrieval = _Retrieval(result=_retrieval_result(retrieval_status))
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        _prediction(),
        top_k=2,
    )

    assert result.status is status
    assert result.notice is not None
    assert result.notice.code is reason
    assert result.diagnosis == _prediction().diagnosis
    assert result.neighbors == _prediction().neighbors
    assert provider.call_count == 0


@pytest.mark.parametrize("top_k", (True, 0, 11, 1.0, "2"))
def test_invalid_top_k_fails_before_retrieval_or_provider(top_k: object) -> None:
    retrieval = _Retrieval()
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        _prediction(),
        top_k=top_k,
    )

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.INVALID_REQUEST
    assert result.neighbors == _prediction().neighbors
    assert retrieval.calls == 0
    assert provider.call_count == 0


@pytest.mark.parametrize("attack", ("exception", "mapping", "subclass"))
def test_hostile_retrieval_is_total_and_sanitized(attack: str) -> None:
    marker = "SYNTHETIC_PRIVATE_RETRIEVAL_PATH_C:\\secret"
    retrieval = _Retrieval()
    if attack == "exception":
        retrieval.failure = RuntimeError(marker)
    elif attack == "mapping":
        retrieval.result = {"content": marker}
    else:
        base = _retrieval_result()

        class RetrievalSubclass(GovernedRetrievalResult):
            pass

        retrieval.result = RetrievalSubclass(
            status=base.status,
            fault_class=base.fault_class,
            policy_schema_version=base.policy_schema_version,
            policy_version=base.policy_version,
            minimum_score=base.minimum_score,
            policy_sha256=base.policy_sha256,
            mapping_version=base.mapping_version,
            mapping_sha256=base.mapping_sha256,
            evidence=base.evidence,
        )
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        _prediction(),
        top_k=2,
    )

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is (PrescriptionOrchestrationReason.RETRIEVAL_UNAVAILABLE)
    assert result.diagnosis == _prediction().diagnosis
    assert result.neighbors == _prediction().neighbors
    assert provider.call_count == 0
    assert marker not in repr(result)


@pytest.mark.parametrize(
    "attack",
    (
        "mapping",
        "subclass",
        "nonfinite_support",
        "diagnosis_subclass",
        "neighbor_subclass",
        "duplicate_neighbor",
        "contradictory_abstention",
    ),
)
def test_hostile_model_predictions_fail_closed(attack: str) -> None:
    prediction: object = _prediction()
    if attack == "mapping":
        prediction = {"disposition": "fault"}
    elif attack == "subclass":

        class PredictionSubclass(ModelPrediction):
            pass

        base = _prediction()
        prediction = PredictionSubclass(
            disposition=base.disposition,
            abstention_reason=base.abstention_reason,
            diagnosis=base.diagnosis,
            support_score=base.support_score,
            model_id=base.model_id,
            neighbors=base.neighbors,
            retrieval_key=base.retrieval_key,
        )
    elif attack == "nonfinite_support":
        object.__setattr__(prediction, "support_score", float("nan"))
    elif attack == "diagnosis_subclass":

        class DiagnosisSubclass(Diagnosis):
            pass

        object.__setattr__(
            prediction,
            "diagnosis",
            DiagnosisSubclass(code=_PUBLIC_FAULT_CODE, summary="Synthetic subclass."),
        )
    elif attack == "neighbor_subclass":

        class NeighborSubclass(OpaqueNeighbor):
            pass

        object.__setattr__(
            prediction,
            "neighbors",
            (
                NeighborSubclass(
                    neighbor_ref="neighbor_synthetic_01",
                    rank=1,
                    fault_code=_PUBLIC_FAULT_CODE,
                    distance=0.25,
                ),
            ),
        )
    elif attack == "duplicate_neighbor":
        neighbor = _prediction().neighbors[0]
        object.__setattr__(prediction, "neighbors", (neighbor, neighbor))
    else:
        object.__setattr__(
            prediction,
            "abstention_reason",
            ModelAbstentionReason.INCONCLUSIVE_VOTE,
        )
    retrieval = _Retrieval()
    provider = _RecordingProvider()

    result = _service(retrieval=retrieval, provider=provider).orchestrate(
        prediction,
        top_k=2,
    )

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.INVALID_PREDICTION
    assert result.disposition is None
    assert result.diagnosis is None
    assert result.neighbors == ()
    assert retrieval.calls == 0
    assert provider.call_count == 0


def test_invalid_or_uncited_provider_output_is_a_guardrail_refusal() -> None:
    output = json.loads(_valid_output_text())
    output["prescriptions"][0]["citations"] = []
    provider = _RecordingProvider(output_text=json.dumps(output))

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.REFUSED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.GUARDRAIL_REFUSAL
    assert result.guardrail is not None
    assert result.guardrail.refusal is not None
    assert result.guardrail.refusal.code is RagRefusalCode.INVALID_PROVIDER_OUTPUT
    assert result.diagnosis == _prediction().diagnosis
    assert result.neighbors == _prediction().neighbors
    assert result.metadata is not None
    assert result.metadata.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=6,
        total_tokens=16,
    )
    assert provider.call_count == 1


def test_insufficient_output_keeps_usage_but_not_provider_narrative() -> None:
    provider = _RecordingProvider(output_text=_insufficient_output_text())

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.REFUSED
    assert result.guardrail is not None
    assert result.guardrail.refusal is not None
    assert result.guardrail.refusal.code is RagRefusalCode.INSUFFICIENT_EVIDENCE
    assert result.metadata is not None
    assert result.metadata.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=6,
        total_tokens=16,
    )
    assert "Synthetic evidence is insufficient." not in repr(result)
    assert provider.call_count == 1


@pytest.mark.failure_matrix
def test_provider_exception_degrades_without_leaking_or_retrying() -> None:
    marker = "SYNTHETIC_TOKEN_PATH_C:\\private\\credential"
    provider = _RecordingProvider(failure=RuntimeError(marker))

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.PROVIDER_ERROR
    assert result.guardrail is not None
    assert result.guardrail.refusal is not None
    assert result.guardrail.refusal.code is RagRefusalCode.PROVIDER_ERROR
    assert result.diagnosis == _prediction().diagnosis
    assert result.neighbors == _prediction().neighbors
    assert result.metadata is not None
    assert result.metadata.usage is None
    assert provider.call_count == 1
    assert marker not in repr(result)


def test_provider_base_exception_is_contained_only_inside_daemon_worker() -> None:
    marker = "SYNTHETIC_WORKER_SYSTEM_EXIT_PRIVATE_DETAIL"
    provider = _RecordingProvider(failure=SystemExit(marker))

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.PROVIDER_ERROR
    assert result.metadata is not None
    assert result.metadata.usage is None
    assert provider.call_count == 1
    assert marker not in repr(result)


def test_provider_disabled_is_a_typed_degraded_result() -> None:
    provider = _RecordingProvider(
        failure=ProviderDisabledError("SYNTHETIC_DISABLED_PRIVATE_DETAIL")
    )

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.PROVIDER_DISABLED
    assert result.metadata is not None
    assert provider.call_count == 1
    assert "SYNTHETIC_DISABLED_PRIVATE_DETAIL" not in repr(result)


@pytest.mark.parametrize("carrier", ("mapping", "response_subclass"))
def test_invalid_provider_envelopes_are_refused_and_sanitized(carrier: str) -> None:
    marker = "SYNTHETIC_PRIVATE_PROVIDER_MAPPING"
    response: object = {"private": marker}
    if carrier == "response_subclass":

        class ResponseSubclass(ProviderResponse):
            pass

        response = ResponseSubclass(output_text=marker)
    provider = _RecordingProvider(response=response)

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.REFUSED
    assert result.guardrail is not None
    assert result.guardrail.refusal is not None
    assert result.guardrail.refusal.code is RagRefusalCode.INVALID_PROVIDER_OUTPUT
    assert provider.call_count == 1
    assert marker not in repr(result)


def test_malformed_usage_is_discarded_without_losing_grounded_output() -> None:
    class UsageSubclass(ProviderUsage):
        pass

    provider = _RecordingProvider(
        usage=UsageSubclass(input_tokens=1, output_tokens=1, total_tokens=2)
    )

    result = _service(provider=provider).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.GENERATED
    assert result.metadata is not None
    assert result.metadata.usage is None
    assert provider.call_count == 1


def test_pre_and_post_currentness_failures_preserve_model_context() -> None:
    pre_provider = _RecordingProvider()
    pre = _service(
        provider=pre_provider,
        currentness=_Currentness(states=[None]),
    ).orchestrate(_prediction(), top_k=2)

    post_provider = _RecordingProvider()
    post = _service(
        provider=post_provider,
        currentness=_Currentness(states=[True, False]),
    ).orchestrate(_prediction(), top_k=2)

    assert pre.status is PrescriptionOrchestrationStatus.DEGRADED
    assert pre.notice is not None
    assert pre.notice.code is (PrescriptionOrchestrationReason.CURRENTNESS_UNAVAILABLE)
    assert pre_provider.call_count == 0
    assert post.status is PrescriptionOrchestrationStatus.REFUSED
    assert post.guardrail is not None
    assert post.guardrail.refusal is not None
    assert post.guardrail.refusal.code is RagRefusalCode.STALE_EVIDENCE
    assert post_provider.call_count == 1
    for result in (pre, post):
        assert result.diagnosis == _prediction().diagnosis
        assert result.neighbors == _prediction().neighbors


@pytest.mark.parametrize(
    "value",
    (
        True,
        0,
        0.0,
        -1.0,
        float("nan"),
        float("inf"),
        MAX_PROVIDER_TIMEOUT_SECONDS + 0.001,
    ),
)
def test_timeout_policy_rejects_unsafe_values(value: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        PrescriptionOrchestrationConfig(
            provider_id="fake-generation.v1",
            provider_timeout_seconds=value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "provider_id",
    (True, "", " provider", "provider/path", "x" * 129),
)
def test_provider_identity_rejects_non_allowlisted_values(provider_id: object) -> None:
    with pytest.raises(ValueError, match="identifier"):
        PrescriptionOrchestrationConfig(
            provider_id=provider_id,  # type: ignore[arg-type]
            provider_timeout_seconds=1.0,
        )


@pytest.mark.parametrize(
    "start",
    (
        True,
        1,
        float("nan"),
        float("inf"),
        RuntimeError("SYNTHETIC_CLOCK_PRIVATE_DETAIL"),
    ),
)
def test_invalid_start_clock_skips_provider_and_is_sanitized(start: object) -> None:
    provider = _RecordingProvider()

    result = _service(provider=provider, clock=_Clock([start])).orchestrate(
        _prediction(),
        top_k=2,
    )

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.TIMING_UNAVAILABLE
    assert result.metadata is None
    assert provider.call_count == 0
    assert "SYNTHETIC_CLOCK_PRIVATE_DETAIL" not in repr(result)


@pytest.mark.parametrize(
    "failure",
    (KeyboardInterrupt(), SystemExit("SYNTHETIC_CALLER_SYSTEM_EXIT")),
)
def test_clock_caller_base_exceptions_propagate_without_provider(
    failure: BaseException,
) -> None:
    provider = _RecordingProvider()

    with pytest.raises(type(failure)):
        _service(provider=provider, clock=_Clock([failure])).orchestrate(
            _prediction(),
            top_k=2,
        )

    assert provider.call_count == 0


@pytest.mark.parametrize(
    "finish",
    (
        True,
        2,
        float("nan"),
        float("inf"),
        0.5,
        RuntimeError("SYNTHETIC_CLOCK_PRIVATE_DETAIL"),
    ),
)
def test_invalid_or_non_monotonic_finish_discards_generation(
    finish: object,
) -> None:
    marker = "SYNTHETIC_VALIDATED_PROVIDER_NARRATIVE"
    provider = _RecordingProvider(output_text=_valid_output_text(narrative=marker))

    result = _service(
        provider=provider,
        clock=_Clock([1.0, finish]),
    ).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.TIMING_UNAVAILABLE
    assert result.guardrail is None
    assert result.metadata is None
    assert provider.call_count == 1
    assert marker not in repr(result)
    assert "SYNTHETIC_CLOCK_PRIVATE_DETAIL" not in repr(result)


@pytest.mark.parametrize(
    ("started_at", "finished_at"),
    ((-1e308, 1e308), (0.0, 1e306)),
)
def test_finite_clock_values_with_nonfinite_latency_fail_closed(
    started_at: float,
    finished_at: float,
) -> None:
    provider = _RecordingProvider()

    result = _service(
        provider=provider,
        clock=_Clock([started_at, finished_at]),
    ).orchestrate(_prediction(), top_k=2)

    assert result.status is PrescriptionOrchestrationStatus.DEGRADED
    assert result.notice is not None
    assert result.notice.code is PrescriptionOrchestrationReason.TIMING_UNAVAILABLE
    assert result.guardrail is None
    assert result.metadata is None
    assert provider.call_count == 1


@pytest.mark.failure_matrix
def test_timeout_busy_late_completion_and_slot_release_are_bounded() -> None:
    provider = _BlockingProvider()
    service = _service(
        provider=provider,
        timeout=0.02,
        clock=_Clock([1.0, 1.02, 2.0, 2.001, 3.0, 3.01]),
    )

    started = time.monotonic()
    timed_out = service.orchestrate(_prediction(), top_k=2)
    elapsed = time.monotonic() - started
    assert provider.started.is_set()
    assert elapsed < 0.5
    assert timed_out.status is PrescriptionOrchestrationStatus.DEGRADED
    assert timed_out.notice is not None
    assert timed_out.notice.code is PrescriptionOrchestrationReason.PROVIDER_TIMEOUT
    assert timed_out.metadata is not None
    assert timed_out.metadata.usage is None
    assert provider.call_count == 1

    busy = service.orchestrate(_prediction(), top_k=2)
    assert busy.status is PrescriptionOrchestrationStatus.DEGRADED
    assert busy.notice is not None
    assert busy.notice.code is PrescriptionOrchestrationReason.PROVIDER_BUSY
    assert provider.call_count == 1

    frozen_timeout_result = repr(timed_out)
    provider.release.set()
    assert provider.finished.wait(timeout=0.5)
    deadline = time.monotonic() + 0.5
    while True:
        recovered = service.orchestrate(_prediction(), top_k=2)
        if (
            recovered.notice is None
            or recovered.notice.code
            is not PrescriptionOrchestrationReason.PROVIDER_BUSY
        ):
            break
        assert time.monotonic() < deadline
        time.sleep(0.001)

    assert recovered.status is PrescriptionOrchestrationStatus.GENERATED
    assert provider.call_count == 2
    assert repr(timed_out) == frozen_timeout_result
    assert timed_out.metadata is not None
    assert timed_out.metadata.usage is None


def test_concurrent_race_starts_one_provider_thread_without_queue_or_retry() -> None:
    provider = _BlockingProvider()
    service = PrescriptionOrchestrationService(
        retrieval=_Retrieval(),
        provider=provider,
        snapshot_currentness=_Currentness(),
        config=PrescriptionOrchestrationConfig(
            provider_id="fake-generation.v1",
            provider_timeout_seconds=0.04,
        ),
    )
    barrier = Barrier(2)

    def run() -> PrescriptionOrchestrationResult:
        barrier.wait(timeout=1.0)
        return service.orchestrate(_prediction(), top_k=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(run), executor.submit(run))
        results = tuple(future.result(timeout=1.0) for future in futures)

    reasons = {result.notice.code for result in results if result.notice is not None}
    assert reasons == {
        PrescriptionOrchestrationReason.PROVIDER_TIMEOUT,
        PrescriptionOrchestrationReason.PROVIDER_BUSY,
    }
    assert provider.call_count == 1
    provider.release.set()
    assert provider.finished.wait(timeout=0.5)


def test_document_prompt_output_and_diagnostic_text_are_absent_from_repr() -> None:
    document_marker = "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT"
    output_marker = "SYNTHETIC_VALIDATED_PROVIDER_NARRATIVE"
    prediction = _prediction()
    object.__setattr__(
        prediction,
        "diagnosis",
        Diagnosis(code=_PUBLIC_FAULT_CODE, summary="SYNTHETIC_DIAGNOSTIC_DETAIL"),
    )
    provider = _RecordingProvider(
        output_text=_valid_output_text(narrative=output_marker)
    )

    result = _service(
        retrieval=_Retrieval(result=_retrieval_result(content=document_marker)),
        provider=provider,
    ).orchestrate(prediction, top_k=2)

    representation = repr(result)
    assert result.status is PrescriptionOrchestrationStatus.GENERATED
    assert document_marker not in representation
    assert output_marker not in representation
    assert "SYNTHETIC_DIAGNOSTIC_DETAIL" not in representation
    assert provider.requests[0].system_prompt not in representation
    assert provider.requests[0].input_json not in representation


def test_repetition_is_deterministic_and_has_one_attempt_per_call() -> None:
    first_provider = _RecordingProvider()
    second_provider = _RecordingProvider()
    first = _service(
        provider=first_provider,
        clock=_Clock([5.0, 5.025]),
    ).orchestrate(_prediction(), top_k=2)
    second = _service(
        provider=second_provider,
        clock=_Clock([5.0, 5.025]),
    ).orchestrate(_prediction(), top_k=2)

    assert first == second
    assert first_provider.call_count == second_provider.call_count == 1
    assert first_provider.requests == second_provider.requests


def test_result_contract_rejects_unreachable_status_combinations() -> None:
    generated = _service().orchestrate(_prediction(), top_k=2)
    normal = _service().orchestrate(
        _prediction(disposition=ModelDisposition.NORMAL),
        top_k=2,
    )
    provider_error = _service(
        provider=_RecordingProvider(failure=RuntimeError("synthetic"))
    ).orchestrate(_prediction(), top_k=2)
    refused = _service(provider=_RecordingProvider(output_text="{}")).orchestrate(
        _prediction(), top_k=2
    )
    pre_provider_refused = _service(
        retrieval=_Retrieval(
            result=_retrieval_result(content="Synthetic unsafe control.\x00")
        )
    ).orchestrate(_prediction(), top_k=2)

    assert generated.guardrail is not None
    assert generated.metadata is not None
    assert normal.notice is not None
    assert provider_error.guardrail is not None
    assert provider_error.metadata is not None
    assert provider_error.notice is not None
    assert refused.guardrail is not None
    assert refused.notice is not None
    assert pre_provider_refused.guardrail is not None
    assert pre_provider_refused.metadata is None
    assert pre_provider_refused.notice is not None

    mismatched_diagnosis = Diagnosis(
        code=_PUBLIC_FAULT_CODE,
        summary="Different synthetic public diagnosis.",
    )

    invalid_states = (
        lambda: replace(generated, disposition=ModelDisposition.NORMAL),
        lambda: replace(generated, diagnosis=mismatched_diagnosis),
        lambda: replace(provider_error, guardrail=None),
        lambda: replace(normal, notice=provider_error.notice),
        lambda: replace(refused, notice=provider_error.notice),
        lambda: replace(refused, metadata=None),
        lambda: replace(
            pre_provider_refused,
            metadata=generated.metadata,
        ),
        lambda: replace(provider_error, metadata=None),
    )
    for build_invalid_state in invalid_states:
        with pytest.raises(
            ValueError,
            match=r"result|metadata|state|incomplete|inconsistent",
        ):
            build_invalid_state()
