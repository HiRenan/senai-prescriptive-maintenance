"""Short, reproducible local benchmark for the integrated analysis journey."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import version
from math import ceil, isfinite
from pathlib import Path, PurePosixPath
from threading import Lock
from time import perf_counter_ns
from types import MappingProxyType
from typing import Final, Literal, cast

from fastapi.testclient import TestClient
from httpx2 import Response

from prescriptive_maintenance.analysis_integration import (
    ANALYSIS_AUTHORIZATION_SCHEMA_VERSION,
    PERSISTED_GENERATION_PROMPT_ID,
    AnalysisRuntimeAuthorization,
    IntegratedAnalysisService,
    SimilarityCheckedModelPort,
    TraceableModelPort,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    API_CONTRACT_VERSION,
    MAX_TOP_K,
    AnalysisFeatures,
    AnalysisRequest,
    Diagnosis,
    OpaqueNeighbor,
    PrescriptionPriority,
)
from prescriptive_maintenance.data import CANONICAL_FEATURE_CONTRACT_VERSION
from prescriptive_maintenance.generation.contracts import (
    GENERATION_CONTRACT_VERSION,
    ProviderUsage,
)
from prescriptive_maintenance.generation.guardrails import SnapshotCurrentnessPort
from prescriptive_maintenance.generation.prompt import (
    GENERATION_SYSTEM_PROMPT_VERSION,
)
from prescriptive_maintenance.generation.provider import (
    FakeGenerationProvider,
    GenerationProvider,
    ProviderExecutionError,
    ProviderRequest,
    ProviderResponse,
)
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalBinding,
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
    RagKnowledgeRetrievalPort,
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import RankedKnowledgeSnapshot
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.modeling.similarity_index import (
    SIMILARITY_CONFIGURATION_VERSION,
    SIMILARITY_INDEX_DIMENSION,
    SIMILARITY_INDEX_METRIC,
    SIMILARITY_INDEX_VERSION,
    SIMILARITY_PREPROCESSOR_VERSION,
    SimilarityIndexCompatibility,
    SimilarityIndexPort,
    SimilarityIndexSelector,
    SimilarityNeighbor,
    SimilarityQuery,
)
from prescriptive_maintenance.operations import CORRELATION_ID_HEADER
from prescriptive_maintenance.persistence.memory import (
    InMemoryStore,
    InMemoryUnitOfWork,
)
from prescriptive_maintenance.persistence.models import (
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
)
from prescriptive_maintenance.ports import (
    ModelDisposition,
    ModelPort,
    ModelPrediction,
)
from prescriptive_maintenance.prescription_orchestration import (
    PrescriptionOrchestrationConfig,
    PrescriptionOrchestrationService,
)
from prescriptive_maintenance.settings import Settings

BENCHMARK_SCHEMA_VERSION: Final = 1
BENCHMARK_ID: Final = "analysis-local-synthetic.v1"
DEFAULT_WARMUP_ITERATIONS: Final = 2
DEFAULT_MEASURED_ITERATIONS: Final = 10
DEFAULT_SEED: Final = 65
DEFAULT_TOP_K: Final = 3

_DATASET_VERSION: Final = "benchmark-synthetic-dataset.v1"
_DATASET_ID: Final = sha256(b"sen65-synthetic-dataset-v1").hexdigest()
_MODEL_VERSION: Final = "benchmark-synthetic-model.v1"
_MODEL_ID: Final = "model_benchmark_synthetic_v1"
_INDEX_ID: Final = (
    "similarity_index_v1_" + sha256(b"sen65-synthetic-index-v1").hexdigest()[:32]
)
_SCHEMA_ID: Final = sha256(b"sen65-analysis-features-v1").hexdigest()
_MAPPING_VERSION: Final = "benchmark-synthetic-mapping.v1"
_MAPPING_SHA256: Final = sha256(b"sen65-synthetic-mapping-v1").hexdigest()
_PROVIDER_ID: Final = "synthetic-benchmark-provider.v1"
_PROVIDER_TIMEOUT_SECONDS: Final = 2.0
_PROJECTION_VERSION: Final = "benchmark-synthetic-priority.v1"
_AUTHORIZATION_VERSION: Final = "benchmark-synthetic-analysis.v1"
_SCHEDULE_VERSION: Final = "balanced-pairs-sha256.v1"
_WORKING_TREE_DIGEST_VERSION: Final = b"git-working-tree-content.v1"
_DOCUMENT_ID: Final = "doc_benchmark_synthetic_manual"
_DOCUMENT_VERSION_ID: Final = "docver_benchmark_synthetic_manual_v1"
_CHUNK_ID: Final = "chunk_benchmark_synthetic_manual_01"
_SECTION_ID: Final = "section_benchmark_synthetic_manual_01"
_SUCCESS_PUBLIC_FAULT: Final = "fault_synthetic_benchmark"
_FAILURE_PUBLIC_FAULT: Final = "fault_synthetic_provider_failure"
_SUCCESS_RETRIEVAL_KEY: Final = "synthetic-benchmark-fault"
_FAILURE_RETRIEVAL_KEY: Final = "synthetic-provider-failure"
_SUCCESS_RPM: Final = 6_501.0
_FAILURE_RPM: Final = 6_502.0
_SYNTHETIC_EVIDENCE: Final = (
    "Synthetic benchmark evidence for controlled maintenance validation."
)
_SYNTHETIC_TIME: Final = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)
_SIMULATED_USAGE: Final = ProviderUsage(
    input_tokens=24,
    output_tokens=12,
    total_tokens=36,
)

_RETRIEVAL_POLICY = build_governed_retrieval_policy(
    policy_version="benchmark-synthetic-retrieval.v1",
    minimum_score=0.5,
)
_BENCHMARK_LOGGER: Final = logging.getLogger("prescriptive_maintenance.benchmark")

BenchmarkScenario = Literal["documented_fault", "provider_failure"]
BenchmarkPhase = Literal["warmup", "measured"]
BenchmarkLayer = Literal["http_total", "model", "retrieval", "generation"]
BenchmarkEventStatus = Literal["success", "error"]

_SCENARIOS: Final[tuple[BenchmarkScenario, ...]] = (
    "documented_fault",
    "provider_failure",
)
_LAYERS: Final[tuple[BenchmarkLayer, ...]] = (
    "http_total",
    "model",
    "retrieval",
    "generation",
)
_EXPECTED_OUTCOMES: Final[dict[BenchmarkScenario, str]] = {
    "documented_fault": "documented_fault",
    "provider_failure": "degraded",
}


class AnalysisBenchmarkError(RuntimeError):
    """Sanitized failure raised when benchmark evidence cannot be produced."""


@dataclass(frozen=True, slots=True)
class AnalysisBenchmarkConfig:
    """Closed configuration for one short, balanced benchmark run."""

    warmup_iterations: int = DEFAULT_WARMUP_ITERATIONS
    measured_iterations: int = DEFAULT_MEASURED_ITERATIONS
    seed: int = DEFAULT_SEED
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        if (
            type(self.warmup_iterations) is not int
            or not 0 <= self.warmup_iterations <= 100
            or type(self.measured_iterations) is not int
            or not 1 <= self.measured_iterations <= 1_000
            or type(self.seed) is not int
            or not 0 <= self.seed <= 2**32 - 1
            or type(self.top_k) is not int
            or not 1 <= self.top_k <= MAX_TOP_K
        ):
            raise ValueError("Analysis benchmark configuration is invalid.")


@dataclass(frozen=True, slots=True)
class RepositoryState:
    """Public-safe Git identity without paths or diff content."""

    commit: str
    dirty: bool
    working_tree_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.commit) is not str
            or len(self.commit) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in self.commit)
            or type(self.dirty) is not bool
            or type(self.working_tree_sha256) is not str
            or len(self.working_tree_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.working_tree_sha256
            )
        ):
            raise ValueError("Repository state is invalid.")


@dataclass(frozen=True, slots=True)
class _RepositorySnapshot:
    state: RepositoryState
    uv_lock_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.state) is not RepositoryState
            or type(self.uv_lock_sha256) is not str
            or len(self.uv_lock_sha256) != 64
            or any(
                character not in "0123456789abcdef" for character in self.uv_lock_sha256
            )
        ):
            raise ValueError("Repository snapshot is invalid.")


@dataclass(frozen=True, slots=True, init=False)
class AnalysisBenchmarkReport:
    """Sanitized benchmark payload with stable JSON and Markdown renderers."""

    _payload: Mapping[str, object] = field(repr=False)
    _json: str = field(repr=False)

    def __init__(self, *, payload: Mapping[str, object]) -> None:
        canonical_payload, serialized = _validated_report_payload(payload)
        frozen_payload = _freeze_json_value(canonical_payload)
        if not isinstance(frozen_payload, Mapping):
            raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
        private_payload = cast(Mapping[str, object], frozen_payload)
        try:
            _render_markdown(private_payload)
        except AnalysisBenchmarkError:
            raise
        except Exception:
            raise AnalysisBenchmarkError(
                "Benchmark report structure is invalid."
            ) from None
        object.__setattr__(self, "_payload", private_payload)
        object.__setattr__(self, "_json", serialized)

    @property
    def payload(self) -> Mapping[str, object]:
        """Return a defensive copy without exposing the private evidence."""

        try:
            copied = cast(object, json.loads(self._json))
        except (TypeError, ValueError):
            raise AnalysisBenchmarkError(
                "Benchmark report structure is invalid."
            ) from None
        if type(copied) is not dict:
            raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
        return cast(dict[str, object], copied)

    def to_json(self) -> str:
        """Serialize with stable key ordering and finite JSON numbers only."""

        return self._json

    def to_markdown(self) -> str:
        """Render the same evidence as a compact Portuguese report."""

        return _render_markdown(self._payload)


@dataclass(frozen=True, slots=True)
class _ActiveSample:
    correlation_id: str
    phase: BenchmarkPhase
    scenario: BenchmarkScenario


@dataclass(frozen=True, slots=True)
class _LayerEvent:
    correlation_id: str
    phase: BenchmarkPhase
    scenario: BenchmarkScenario
    layer: BenchmarkLayer
    status: BenchmarkEventStatus
    duration_ms: float
    usage: ProviderUsage | None = None


class _BenchmarkRecorder:
    """Associate port timings with the single sequential HTTP sample in flight."""

    def __init__(self) -> None:
        self._active: _ActiveSample | None = None
        self._events: list[_LayerEvent] = []
        self._outcomes: dict[BenchmarkScenario, dict[str, int]] = {
            scenario: {} for scenario in _SCENARIOS
        }
        self._lock = Lock()

    @property
    def events(self) -> tuple[_LayerEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def outcomes(self) -> dict[BenchmarkScenario, dict[str, int]]:
        with self._lock:
            return {
                scenario: dict(values) for scenario, values in self._outcomes.items()
            }

    def begin(
        self,
        *,
        correlation_id: str,
        phase: BenchmarkPhase,
        scenario: BenchmarkScenario,
    ) -> None:
        with self._lock:
            if self._active is not None:
                raise AnalysisBenchmarkError("Benchmark samples cannot overlap.")
            self._active = _ActiveSample(
                correlation_id=correlation_id,
                phase=phase,
                scenario=scenario,
            )

    def end(self, *, correlation_id: str) -> None:
        with self._lock:
            if self._active is None or self._active.correlation_id != correlation_id:
                raise AnalysisBenchmarkError("Benchmark sample context is invalid.")
            self._active = None

    def record(
        self,
        *,
        layer: BenchmarkLayer,
        status: BenchmarkEventStatus,
        duration_ms: float,
        usage: ProviderUsage | None = None,
    ) -> None:
        if (
            type(duration_ms) is not float
            or not isfinite(duration_ms)
            or duration_ms < 0.0
        ):
            raise AnalysisBenchmarkError("Benchmark duration is invalid.")
        copied_usage = _copy_usage(usage)
        with self._lock:
            active = self._active
            if active is None:
                raise AnalysisBenchmarkError("Benchmark sample context is missing.")
            event = _LayerEvent(
                correlation_id=active.correlation_id,
                phase=active.phase,
                scenario=active.scenario,
                layer=layer,
                status=status,
                duration_ms=duration_ms,
                usage=copied_usage,
            )
            self._events.append(event)

    def record_outcome(self, outcome: str) -> None:
        with self._lock:
            active = self._active
            if active is None:
                raise AnalysisBenchmarkError("Benchmark sample context is missing.")
            if active.phase != "measured":
                return
            values = self._outcomes[active.scenario]
            values[outcome] = values.get(outcome, 0) + 1


class _TimedTraceableModel:
    def __init__(
        self,
        model: TraceableModelPort,
        recorder: _BenchmarkRecorder,
    ) -> None:
        self._model = model
        self._recorder = recorder

    @property
    def dataset_id(self) -> str:
        return self._model.dataset_id

    @property
    def model_id(self) -> str:
        return self._model.model_id

    @property
    def index_id(self) -> str:
        return self._model.index_id

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        _require_timed_pass_without_tracing()
        started_at = perf_counter_ns()
        status: BenchmarkEventStatus = "error"
        try:
            result = self._model.predict(features, top_k=top_k)
            status = "success"
            return result
        finally:
            _require_timed_pass_without_tracing()
            self._recorder.record(
                layer="model",
                status=status,
                duration_ms=_elapsed_milliseconds(started_at),
            )


class _TimedRetrieval:
    def __init__(
        self,
        retrieval: RagKnowledgeRetrievalPort,
        recorder: _BenchmarkRecorder,
    ) -> None:
        self._retrieval = retrieval
        self._recorder = recorder

    @property
    def runtime_binding(self) -> GovernedRetrievalBinding:
        return self._retrieval.runtime_binding

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        _require_timed_pass_without_tracing()
        started_at = perf_counter_ns()
        status: BenchmarkEventStatus = "error"
        try:
            result = self._retrieval.retrieve(
                disposition=disposition,
                fault_class=fault_class,
                top_k=top_k,
            )
            status = "success"
            return result
        finally:
            _require_timed_pass_without_tracing()
            self._recorder.record(
                layer="retrieval",
                status=status,
                duration_ms=_elapsed_milliseconds(started_at),
            )


class _TimedProvider:
    def __init__(
        self,
        provider: GenerationProvider,
        recorder: _BenchmarkRecorder,
    ) -> None:
        self._provider = provider
        self._recorder = recorder

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        _require_timed_pass_without_tracing()
        started_at = perf_counter_ns()
        status: BenchmarkEventStatus = "error"
        usage: ProviderUsage | None = None
        try:
            response = self._provider.generate(request)
            usage = response.usage
            status = "success"
            return response
        finally:
            _require_timed_pass_without_tracing()
            self._recorder.record(
                layer="generation",
                status=status,
                duration_ms=_elapsed_milliseconds(started_at),
                usage=usage,
            )


class _SyntheticScenarioModel(ModelPort):
    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        scenario = _scenario_for_rpm(features.rpm)
        public_fault, retrieval_key = _scenario_faults(scenario)
        return ModelPrediction(
            disposition=ModelDisposition.FAULT,
            abstention_reason=None,
            diagnosis=Diagnosis(
                code=public_fault,
                summary="Diagnóstico inteiramente sintético para benchmark local.",
            ),
            support_score=0.9,
            model_id=_MODEL_ID,
            neighbors=_synthetic_neighbors(scenario, top_k=top_k),
            retrieval_key=retrieval_key,
        )


class _SyntheticSimilarityIndex(SimilarityIndexPort):
    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]:
        if query.selector.index_id != _INDEX_ID or len(query.features) != len(
            ANALYSIS_FEATURE_NAMES
        ):
            raise ValueError("Synthetic similarity query is invalid.")
        scenario = _scenario_for_rpm(query.features[-1])
        return tuple(
            SimilarityNeighbor(
                opaque_id=item.neighbor_ref,
                rank=item.rank,
                fault_code=item.fault_code,
                distance=item.distance,
            )
            for item in _synthetic_neighbors(scenario, top_k=query.top_k)
        )


class _SyntheticGovernedRetrieval(RagKnowledgeRetrievalPort):
    @property
    def runtime_binding(self) -> GovernedRetrievalBinding:
        return GovernedRetrievalBinding(
            policy_schema_version=_RETRIEVAL_POLICY.schema_version,
            policy_version=_RETRIEVAL_POLICY.policy_version,
            policy_sha256=_RETRIEVAL_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
        )

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        if (
            disposition is not ModelDisposition.FAULT
            or fault_class not in {_SUCCESS_RETRIEVAL_KEY, _FAILURE_RETRIEVAL_KEY}
            or not 1 <= top_k <= MAX_TOP_K
        ):
            raise ValueError("Synthetic governed retrieval request is invalid.")
        return GovernedRetrievalResult(
            status=GovernedRetrievalStatus.EVIDENCE,
            fault_class=fault_class,
            policy_schema_version=_RETRIEVAL_POLICY.schema_version,
            policy_version=_RETRIEVAL_POLICY.policy_version,
            minimum_score=_RETRIEVAL_POLICY.minimum_score,
            policy_sha256=_RETRIEVAL_POLICY.policy_sha256,
            mapping_version=_MAPPING_VERSION,
            mapping_sha256=_MAPPING_SHA256,
            evidence=(_synthetic_snapshot(),),
        )


class _CurrentSyntheticSnapshots(SnapshotCurrentnessPort):
    def snapshots_are_current(
        self,
        *,
        fault_class: str,
        policy_schema_version: int,
        policy_version: str,
        minimum_score: float,
        policy_sha256: str,
        mapping_version: str,
        mapping_sha256: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> bool:
        return (
            fault_class in {_SUCCESS_RETRIEVAL_KEY, _FAILURE_RETRIEVAL_KEY}
            and policy_schema_version == _RETRIEVAL_POLICY.schema_version
            and policy_version == _RETRIEVAL_POLICY.policy_version
            and minimum_score == _RETRIEVAL_POLICY.minimum_score
            and policy_sha256 == _RETRIEVAL_POLICY.policy_sha256
            and mapping_version == _MAPPING_VERSION
            and mapping_sha256 == _MAPPING_SHA256
            and evidence == (_synthetic_snapshot(),)
        )


class _SyntheticScenarioProvider(GenerationProvider):
    def __init__(self) -> None:
        self._success = FakeGenerationProvider(usage=_SIMULATED_USAGE)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.diagnosis_fault_code == _FAILURE_RETRIEVAL_KEY:
            raise ProviderExecutionError("Synthetic provider failure.")
        return self._success.generate(request)


class _AnalysisIdSequence:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self) -> str:
        with self._lock:
            self._value += 1
            return f"ana_benchmark_{self._value:06d}"


def run_local_analysis_benchmark(
    config: AnalysisBenchmarkConfig,
    *,
    repository_root: Path,
) -> AnalysisBenchmarkReport:
    """Exercise the integrated POST through timed, injected synthetic ports."""

    if type(config) is not AnalysisBenchmarkConfig:
        raise ValueError("Analysis benchmark configuration is invalid.")
    if tracemalloc.is_tracing():
        raise AnalysisBenchmarkError(
            "Python allocation tracing must be inactive before the benchmark."
        )

    root = _resolve_repository_root(repository_root)
    initial_snapshot = _capture_repository_snapshot(root)
    recorder = _BenchmarkRecorder()
    timed_service, authorization = _build_integrated_service(recorder)
    timed_settings = Settings.model_validate(
        {"environment": "offline", "persistence_backend": "memory"}
    )
    timed_application = create_app(
        analysis_service=timed_service,
        settings=timed_settings,
    )
    warmup_schedule = _scenario_schedule(
        iterations=config.warmup_iterations,
        seed=config.seed ^ 0xA5A5_A5A5,
    )
    measured_schedule = _scenario_schedule(
        iterations=config.measured_iterations,
        seed=config.seed,
    )
    request_bodies: dict[BenchmarkScenario, bytes] = {
        scenario: _request_body_for(scenario, top_k=config.top_k)
        for scenario in _SCENARIOS
    }

    with TestClient(timed_application) as client:
        _run_schedule(
            client,
            recorder,
            phase="warmup",
            schedule=warmup_schedule,
            request_bodies=request_bodies,
        )
        _run_schedule(
            client,
            recorder,
            phase="measured",
            schedule=measured_schedule,
            request_bodies=request_bodies,
        )

    memory_service, memory_authorization = _build_integrated_service(None)
    if memory_authorization.authorization_sha256 != authorization.authorization_sha256:
        raise AnalysisBenchmarkError("Benchmark runtime binding is inconsistent.")
    memory_settings = Settings.model_validate(
        {"environment": "offline", "persistence_backend": "memory"}
    )
    memory_application = create_app(
        analysis_service=memory_service,
        settings=memory_settings,
    )
    with TestClient(memory_application) as client:
        peak_traced_bytes = _run_memory_schedule(
            client,
            schedule=measured_schedule,
            request_bodies=request_bodies,
        )

    payload = _build_report_payload(
        config=config,
        repository_snapshot=initial_snapshot,
        authorization=authorization,
        warmup_schedule=warmup_schedule,
        measured_schedule=measured_schedule,
        recorder=recorder,
        peak_traced_bytes=peak_traced_bytes,
    )
    _emit_layer_events(recorder.events)
    final_snapshot = _capture_repository_snapshot(root)
    _require_unchanged_repository_snapshot(
        initial=initial_snapshot,
        final=final_snapshot,
    )
    return AnalysisBenchmarkReport(payload=payload)


def discover_repository_state(repository_root: Path) -> RepositoryState:
    """Bind Git identity and working-tree content without exposing file names."""

    root = _resolve_repository_root(repository_root)
    try:
        commit = (
            _run_git_bytes(root, ("rev-parse", "HEAD"))
            .decode("ascii", errors="strict")
            .strip()
        )
    except UnicodeError:
        raise AnalysisBenchmarkError("Git state could not be read safely.") from None
    dirty, working_tree_sha256 = _capture_working_tree_digest(root)
    return RepositoryState(
        commit=commit,
        dirty=dirty,
        working_tree_sha256=working_tree_sha256,
    )


def _capture_working_tree_digest(repository_root: Path) -> tuple[bool, str]:
    status_before = _git_status_records(repository_root)
    index_before = _git_index_records(repository_root)
    entry_fingerprints = tuple(
        _working_tree_entry_fingerprint(repository_root, record)
        for record in status_before
    )
    status_middle = _git_status_records(repository_root)
    index_middle = _git_index_records(repository_root)
    if status_middle != status_before or index_middle != index_before:
        raise AnalysisBenchmarkError(
            "Git working tree changed while provenance was captured."
        )
    confirmed_fingerprints = tuple(
        _working_tree_entry_fingerprint(repository_root, record)
        for record in status_middle
    )
    status_after = _git_status_records(repository_root)
    index_after = _git_index_records(repository_root)
    if (
        status_after != status_before
        or index_after != index_before
        or confirmed_fingerprints != entry_fingerprints
    ):
        raise AnalysisBenchmarkError(
            "Git working tree changed while provenance was captured."
        )

    manifest = bytearray()
    _append_digest_component(manifest, b"version", _WORKING_TREE_DIGEST_VERSION)
    for record in status_before:
        _append_digest_component(manifest, b"status", record)
    for record in index_before:
        _append_digest_component(manifest, b"index", record)
    for fingerprint in entry_fingerprints:
        _append_digest_component(manifest, b"entry", fingerprint)
    return bool(status_before), sha256(manifest).hexdigest()


def _git_status_records(repository_root: Path) -> tuple[bytes, ...]:
    output = _run_git_bytes(
        repository_root,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
            "--no-renames",
        ),
    )
    records = _nul_records(output)
    if any(len(record) < 4 or record[2:3] != b" " for record in records):
        raise AnalysisBenchmarkError("Git state could not be read safely.")
    return tuple(sorted(records))


def _git_index_records(repository_root: Path) -> tuple[bytes, ...]:
    tagged_records = _nul_records(
        _run_git_bytes(repository_root, ("ls-files", "--stage", "-v", "-z"))
    )
    index_records: list[bytes] = []
    for record in tagged_records:
        if len(record) < 3 or record[1:2] != b" ":
            raise AnalysisBenchmarkError("Git state could not be read safely.")
        tag = record[:1]
        if tag == b"S" or tag.islower():
            raise AnalysisBenchmarkError(
                "Git index contains flags that may hide working tree changes."
            )
        index_records.append(record[2:])
    return tuple(sorted(index_records))


def _nul_records(value: bytes) -> tuple[bytes, ...]:
    if not value:
        return ()
    if not value.endswith(b"\0"):
        raise AnalysisBenchmarkError("Git state could not be read safely.")
    records = tuple(value[:-1].split(b"\0"))
    if any(not record for record in records):
        raise AnalysisBenchmarkError("Git state could not be read safely.")
    return records


def _working_tree_entry_fingerprint(
    repository_root: Path,
    status_record: bytes,
) -> bytes:
    status_code = status_record[:2]
    raw_path = status_record[3:]
    path = _safe_working_tree_path(repository_root, raw_path)
    try:
        initial_stat = path.lstat()
    except FileNotFoundError:
        if b"D" in status_code:
            return _fingerprint_record(raw_path, b"missing", b"")
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        ) from None
    except OSError:
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        ) from None

    if stat.S_ISLNK(initial_stat.st_mode):
        try:
            target = os.readlink(path)
            target_bytes = os.fsencode(target)
            final_stat = path.lstat()
        except (OSError, UnicodeError):
            raise AnalysisBenchmarkError(
                "Git working tree could not be fingerprinted safely."
            ) from None
        if _stat_identity(final_stat) != _stat_identity(initial_stat):
            raise AnalysisBenchmarkError(
                "Git working tree changed while provenance was captured."
            )
        return _fingerprint_record(raw_path, b"symlink", sha256(target_bytes).digest())

    if not stat.S_ISREG(initial_stat.st_mode):
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        )
    try:
        content_digest = sha256()
        with path.open("rb") as stream:
            opened_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_stat.st_mode) or _stat_identity(
                opened_stat
            ) != _stat_identity(initial_stat):
                raise AnalysisBenchmarkError(
                    "Git working tree changed while provenance was captured."
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_digest.update(chunk)
            closed_stat = os.fstat(stream.fileno())
        final_stat = path.lstat()
    except AnalysisBenchmarkError:
        raise
    except OSError:
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        ) from None
    if _stat_identity(closed_stat) != _stat_identity(initial_stat) or _stat_identity(
        final_stat
    ) != _stat_identity(initial_stat):
        raise AnalysisBenchmarkError(
            "Git working tree changed while provenance was captured."
        )
    return _fingerprint_record(raw_path, b"regular", content_digest.digest())


def _safe_working_tree_path(repository_root: Path, raw_path: bytes) -> Path:
    try:
        value = raw_path.decode("utf-8", errors="strict")
    except UnicodeError:
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        ) from None
    relative = PurePosixPath(value)
    if (
        not relative.parts
        or relative.is_absolute()
        or "\\" in value
        or ":" in relative.parts[0]
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        )
    candidate = repository_root.joinpath(*relative.parts)
    try:
        resolved_parent = candidate.parent.resolve(strict=False)
    except (OSError, RuntimeError):
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        ) from None
    if not resolved_parent.is_relative_to(repository_root):
        raise AnalysisBenchmarkError(
            "Git working tree could not be fingerprinted safely."
        )
    return candidate


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _fingerprint_record(raw_path: bytes, kind: bytes, digest: bytes) -> bytes:
    value = bytearray()
    _append_digest_component(value, b"path", raw_path)
    _append_digest_component(value, b"kind", kind)
    _append_digest_component(value, b"sha256", digest)
    return bytes(value)


def _append_digest_component(
    target: bytearray,
    label: bytes,
    value: bytes,
) -> None:
    target.extend(len(label).to_bytes(4, "big"))
    target.extend(label)
    target.extend(len(value).to_bytes(8, "big"))
    target.extend(value)


def _capture_repository_snapshot(repository_root: Path) -> _RepositorySnapshot:
    return _RepositorySnapshot(
        state=discover_repository_state(repository_root),
        uv_lock_sha256=_read_uv_lock_sha256(repository_root),
    )


def _require_unchanged_repository_snapshot(
    *,
    initial: _RepositorySnapshot,
    final: _RepositorySnapshot,
) -> None:
    if final != initial:
        raise AnalysisBenchmarkError(
            "Repository provenance changed during benchmark execution."
        )


def _build_integrated_service(
    recorder: _BenchmarkRecorder | None,
) -> tuple[IntegratedAnalysisService, AnalysisRuntimeAuthorization]:
    projection = build_prescription_projection_policy(
        policy_version=_PROJECTION_VERSION,
        priorities={
            _SUCCESS_PUBLIC_FAULT: PrescriptionPriority.SCHEDULED,
            _FAILURE_PUBLIC_FAULT: PrescriptionPriority.SCHEDULED,
        },
    )
    authorization = build_analysis_runtime_authorization(
        authorization_version=_AUTHORIZATION_VERSION,
        dataset_id=_DATASET_ID,
        model_id=_MODEL_ID,
        index_id=_INDEX_ID,
        retrieval_policy_version=_RETRIEVAL_POLICY.policy_version,
        retrieval_policy_sha256=_RETRIEVAL_POLICY.policy_sha256,
        mapping_version=_MAPPING_VERSION,
        mapping_sha256=_MAPPING_SHA256,
        prompt_id=PERSISTED_GENERATION_PROMPT_ID,
        provider_id=_PROVIDER_ID,
        provider_timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
        projection_policy=projection,
    )
    checked_model = SimilarityCheckedModelPort(
        model=_SyntheticScenarioModel(),
        similarity=_SyntheticSimilarityIndex(),
        selector=SimilarityIndexSelector(
            index_id=_INDEX_ID,
            model_id=_MODEL_ID,
            compatibility=SimilarityIndexCompatibility(
                dataset_id=_DATASET_ID,
                schema_id=_SCHEMA_ID,
            ),
        ),
        authorization=authorization,
    )
    retrieval: RagKnowledgeRetrievalPort = _SyntheticGovernedRetrieval()
    provider: GenerationProvider = _SyntheticScenarioProvider()
    model: TraceableModelPort = checked_model
    if recorder is not None:
        retrieval = _TimedRetrieval(retrieval, recorder)
        provider = _TimedProvider(provider, recorder)
        model = _TimedTraceableModel(model, recorder)
    orchestration = PrescriptionOrchestrationService(
        retrieval=retrieval,
        provider=provider,
        snapshot_currentness=_CurrentSyntheticSnapshots(),
        config=PrescriptionOrchestrationConfig(
            provider_id=_PROVIDER_ID,
            provider_timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
        ),
    )
    store = InMemoryStore()
    _seed_synthetic_document(store)
    service = IntegratedAnalysisService(
        model=model,
        orchestration=orchestration,
        authorization=authorization,
        projection_policy=projection,
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: _SYNTHETIC_TIME,
        analysis_id_factory=_AnalysisIdSequence(),
    )
    return service, authorization


def _seed_synthetic_document(store: InMemoryStore) -> None:
    document = DocumentMetadata(
        document_id=_DOCUMENT_ID,
        created_at=_SYNTHETIC_TIME,
        versions=(
            DocumentVersionMetadata(
                document_version_id=_DOCUMENT_VERSION_ID,
                document_id=_DOCUMENT_ID,
                source_sha256=sha256(b"sen65-synthetic-document-v1").hexdigest(),
                created_at=_SYNTHETIC_TIME,
                chunks=(
                    ChunkReference(
                        chunk_ref=_CHUNK_ID,
                        document_id=_DOCUMENT_ID,
                        document_version_id=_DOCUMENT_VERSION_ID,
                        page_number=1,
                    ),
                ),
            ),
        ),
    )
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.commit()


def _synthetic_snapshot() -> RankedKnowledgeSnapshot:
    return RankedKnowledgeSnapshot(
        document_id=_DOCUMENT_ID,
        document_version=_DOCUMENT_VERSION_ID,
        chunk_id=_CHUNK_ID,
        page_number=1,
        section_id=_SECTION_ID,
        content=_SYNTHETIC_EVIDENCE,
        content_sha256=sha256(_SYNTHETIC_EVIDENCE.encode("utf-8")).hexdigest(),
        score=0.9,
    )


def _synthetic_neighbors(
    scenario: BenchmarkScenario,
    *,
    top_k: int,
) -> tuple[OpaqueNeighbor, ...]:
    public_fault, _ = _scenario_faults(scenario)
    return tuple(
        OpaqueNeighbor(
            neighbor_ref=f"neighbor_benchmark_{scenario}_{rank:02d}",
            rank=rank,
            fault_code=public_fault,
            distance=float(rank) / 10.0,
        )
        for rank in range(1, top_k + 1)
    )


def _scenario_faults(scenario: BenchmarkScenario) -> tuple[str, str]:
    if scenario == "documented_fault":
        return _SUCCESS_PUBLIC_FAULT, _SUCCESS_RETRIEVAL_KEY
    return _FAILURE_PUBLIC_FAULT, _FAILURE_RETRIEVAL_KEY


def _scenario_for_rpm(value: float) -> BenchmarkScenario:
    if value == _SUCCESS_RPM:
        return "documented_fault"
    if value == _FAILURE_RPM:
        return "provider_failure"
    raise ValueError("Synthetic benchmark scenario is invalid.")


def _request_for(scenario: BenchmarkScenario, *, top_k: int) -> AnalysisRequest:
    rpm = _SUCCESS_RPM if scenario == "documented_fault" else _FAILURE_RPM
    values = {
        name: float(index + 1) for index, name in enumerate(ANALYSIS_FEATURE_NAMES)
    }
    values["rpm"] = rpm
    return AnalysisRequest(
        features=AnalysisFeatures.model_validate(values),
        top_k=top_k,
    )


def _request_body_for(scenario: BenchmarkScenario, *, top_k: int) -> bytes:
    try:
        return _request_for(scenario, top_k=top_k).model_dump_json().encode("utf-8")
    except Exception:
        raise AnalysisBenchmarkError(
            "Synthetic benchmark request could not be prepared safely."
        ) from None


def _scenario_schedule(
    *,
    iterations: int,
    seed: int,
) -> tuple[BenchmarkScenario, ...]:
    schedule: list[BenchmarkScenario] = []
    for iteration in range(iterations):
        identity = f"{_SCHEDULE_VERSION}:{seed}:{iteration}".encode("ascii")
        pair = (
            _SCENARIOS
            if sha256(identity).digest()[0] % 2 == 0
            else (_SCENARIOS[1], _SCENARIOS[0])
        )
        schedule.extend(pair)
    return tuple(schedule)


def _run_schedule(
    client: TestClient,
    recorder: _BenchmarkRecorder,
    *,
    phase: BenchmarkPhase,
    schedule: Sequence[BenchmarkScenario],
    request_bodies: Mapping[BenchmarkScenario, bytes],
) -> None:
    for ordinal, scenario in enumerate(schedule, start=1):
        _run_timed_sample(
            client,
            recorder,
            phase=phase,
            scenario=scenario,
            ordinal=ordinal,
            request_body=request_bodies[scenario],
        )


def _run_timed_sample(
    client: TestClient,
    recorder: _BenchmarkRecorder,
    *,
    phase: BenchmarkPhase,
    scenario: BenchmarkScenario,
    ordinal: int,
    request_body: bytes,
) -> None:
    correlation_id = f"sen65_{phase}_{scenario}_{ordinal:04d}"
    request_headers = {
        "Content-Type": "application/json",
        CORRELATION_ID_HEADER: correlation_id,
    }
    recorder.begin(
        correlation_id=correlation_id,
        phase=phase,
        scenario=scenario,
    )
    duration_ms: float | None = None
    status: BenchmarkEventStatus = "error"
    try:
        _require_timed_pass_without_tracing()
        started_at = perf_counter_ns()
        response = None
        transport_failed = False
        try:
            response = client.post(
                "/analysis",
                content=request_body,
                headers=request_headers,
            )
        except Exception:
            transport_failed = True
        finally:
            _require_timed_pass_without_tracing()
            duration_ms = _elapsed_milliseconds(started_at)
        if transport_failed or response is None:
            raise AnalysisBenchmarkError("Benchmark HTTP execution failed.")
        outcome = _validated_http_outcome(
            response,
            correlation_id=correlation_id,
            scenario=scenario,
        )
        recorder.record_outcome(outcome)
        status = "success"
    except AnalysisBenchmarkError:
        raise
    except Exception:
        raise AnalysisBenchmarkError("Benchmark HTTP execution failed.") from None
    finally:
        try:
            if duration_ms is not None:
                recorder.record(
                    layer="http_total",
                    status=status,
                    duration_ms=duration_ms,
                )
        finally:
            recorder.end(correlation_id=correlation_id)


def _run_memory_schedule(
    client: TestClient,
    *,
    schedule: Sequence[BenchmarkScenario],
    request_bodies: Mapping[BenchmarkScenario, bytes],
) -> int:
    peak_traced_bytes = 0
    for ordinal, scenario in enumerate(schedule, start=1):
        peak_traced_bytes = max(
            peak_traced_bytes,
            _run_memory_sample(
                client,
                scenario=scenario,
                ordinal=ordinal,
                request_body=request_bodies[scenario],
            ),
        )
    return peak_traced_bytes


def _run_memory_sample(
    client: TestClient,
    *,
    scenario: BenchmarkScenario,
    ordinal: int,
    request_body: bytes,
) -> int:
    correlation_id = f"sen65_memory_{scenario}_{ordinal:04d}"
    request_headers = {
        "Content-Type": "application/json",
        CORRELATION_ID_HEADER: correlation_id,
    }
    response = None
    peak_traced_bytes = 0
    trace_started = False
    transport_failed = False
    try:
        _start_memory_trace()
        trace_started = True
        try:
            response = client.post(
                "/analysis",
                content=request_body,
                headers=request_headers,
            )
        except Exception:
            transport_failed = True
        finally:
            if trace_started:
                _, peak_traced_bytes = tracemalloc.get_traced_memory()
    except AnalysisBenchmarkError:
        raise
    except Exception:
        raise AnalysisBenchmarkError("Benchmark HTTP execution failed.") from None
    finally:
        if trace_started:
            tracemalloc.stop()
    if transport_failed or response is None:
        raise AnalysisBenchmarkError("Benchmark HTTP execution failed.")
    _validated_http_outcome(
        response,
        correlation_id=correlation_id,
        scenario=scenario,
    )
    return peak_traced_bytes


def _validated_http_outcome(
    response: Response,
    *,
    correlation_id: str,
    scenario: BenchmarkScenario,
) -> str:
    try:
        body = response.json()
    except Exception:
        raise AnalysisBenchmarkError("Benchmark HTTP response is invalid.") from None
    if type(body) is not dict:
        raise AnalysisBenchmarkError("Benchmark HTTP response is invalid.")
    outcome = cast(dict[object, object], body).get("outcome")
    if (
        response.status_code != 200
        or response.headers.get(CORRELATION_ID_HEADER) != correlation_id
        or outcome != _EXPECTED_OUTCOMES[scenario]
    ):
        raise AnalysisBenchmarkError("Benchmark HTTP outcome is invalid.")
    return cast(str, outcome)


def _build_report_payload(
    *,
    config: AnalysisBenchmarkConfig,
    repository_snapshot: _RepositorySnapshot,
    authorization: AnalysisRuntimeAuthorization,
    warmup_schedule: tuple[BenchmarkScenario, ...],
    measured_schedule: tuple[BenchmarkScenario, ...],
    recorder: _BenchmarkRecorder,
    peak_traced_bytes: int,
) -> dict[str, object]:
    measured = tuple(event for event in recorder.events if event.phase == "measured")
    layer_metrics = {
        layer: _summarize_events(
            tuple(event for event in measured if event.layer == layer)
        )
        for layer in _LAYERS
    }
    scenario_metrics: dict[str, object] = {}
    outcomes = recorder.outcomes
    for scenario in _SCENARIOS:
        scenario_events = tuple(
            event for event in measured if event.scenario == scenario
        )
        scenario_metrics[scenario] = {
            "expected_outcome": _EXPECTED_OUTCOMES[scenario],
            "observed_outcomes": outcomes[scenario],
            "layers": {
                layer: _summarize_events(
                    tuple(event for event in scenario_events if event.layer == layer)
                )
                for layer in _LAYERS
            },
        }

    successful_generation = tuple(
        event
        for event in measured
        if event.layer == "generation" and event.status == "success"
    )
    failed_generation = tuple(
        event
        for event in measured
        if event.layer == "generation" and event.status == "error"
    )
    simulated_usage = tuple(
        event.usage
        for event in successful_generation
        if event.usage is not None
        and type(event.usage.input_tokens) is int
        and type(event.usage.output_tokens) is int
        and type(event.usage.total_tokens) is int
    )
    repository_state = repository_snapshot.state
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "repository": {
            "commit": repository_state.commit,
            "working_tree_dirty": repository_state.dirty,
            "working_tree_sha256": repository_state.working_tree_sha256,
            "uv_lock_sha256": repository_snapshot.uv_lock_sha256,
        },
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "dependencies": {
                "fastapi": _required_package_version("fastapi"),
                "httpx2": _required_package_version("httpx2"),
                "prescriptive-maintenance-api": _required_package_version(
                    "prescriptive-maintenance-api"
                ),
                "pydantic": _required_package_version("pydantic"),
                "starlette": _required_package_version("starlette"),
            },
        },
        "configuration": {
            "seed": config.seed,
            "warmup_iterations_per_scenario": config.warmup_iterations,
            "measured_iterations_per_scenario": config.measured_iterations,
            "memory_iterations_per_scenario": config.measured_iterations,
            "top_k": config.top_k,
            "provider_mode": "synthetic_offline",
            "scenario_schedule_version": _SCHEDULE_VERSION,
            "warmup_scenario_order_sha256": _schedule_sha256(warmup_schedule),
            "measured_scenario_order_sha256": _schedule_sha256(measured_schedule),
        },
        "bindings": {
            "api_contract_version": API_CONTRACT_VERSION,
            "dataset_id": authorization.dataset_id,
            "dataset_version": _DATASET_VERSION,
            "feature_contract_version": CANONICAL_FEATURE_CONTRACT_VERSION,
            "feature_schema_id": _SCHEMA_ID,
            "model_id": authorization.model_id,
            "model_version": _MODEL_VERSION,
            "index_id": authorization.index_id,
            "index_version": SIMILARITY_INDEX_VERSION,
            "similarity_configuration_version": SIMILARITY_CONFIGURATION_VERSION,
            "similarity_dimension": SIMILARITY_INDEX_DIMENSION,
            "similarity_metric": SIMILARITY_INDEX_METRIC,
            "similarity_preprocessor_version": SIMILARITY_PREPROCESSOR_VERSION,
            "generation_contract_version": GENERATION_CONTRACT_VERSION,
            "prompt_id": authorization.prompt_id,
            "prompt_version": GENERATION_SYSTEM_PROMPT_VERSION,
            "provider_id": authorization.provider_id,
            "provider_timeout_seconds": authorization.provider_timeout_seconds,
            "retrieval_policy_schema_version": _RETRIEVAL_POLICY.schema_version,
            "retrieval_policy_version": authorization.retrieval_policy_version,
            "retrieval_policy_sha256": authorization.retrieval_policy_sha256,
            "mapping_version": authorization.mapping_version,
            "mapping_sha256": authorization.mapping_sha256,
            "projection_policy_schema_version": 1,
            "projection_policy_version": authorization.projection_policy_version,
            "projection_policy_sha256": authorization.projection_policy_sha256,
            "authorization_schema_version": ANALYSIS_AUTHORIZATION_SCHEMA_VERSION,
            "authorization_version": authorization.authorization_version,
            "authorization_sha256": authorization.authorization_sha256,
        },
        "warmup": {
            "request_count": len(warmup_schedule),
            "included_in_distributions": False,
        },
        "metrics": {
            "primary_view": "scenarios",
            "measurement_protocol": {
                "timed_pass": "warmup_then_measured_without_tracemalloc",
                "memory_pass": "separate_fresh_service_and_application",
                "memory_schedule_matches_measured_schedule": True,
                "http_total_includes": [
                    "application_response_serialization",
                    "operational_request_log_serialization_and_handler_io",
                ],
            },
            "scenarios": scenario_metrics,
            "synthetic_scenario_mix": {
                "layers": layer_metrics,
                "population": "documented_fault_and_provider_failure",
                "percentile_population": "successful_attempts_only",
                "error_rate_population": (
                    "deliberate_provider_success_and_provider_failure_mix"
                ),
            },
            "provider_failures": {
                "count": len(failed_generation),
                "excluded_from_valid_generation_latency": True,
            },
            "memory": {
                "value_kind": "measured",
                "method": ("separate_pass_maximum_of_per_request_tracemalloc_peaks"),
                "unit": "bytes",
                "peak_traced_bytes": peak_traced_bytes,
                "scope": "individual_memory_pass_http_requests",
                "included_application_work": [
                    "application_response_serialization",
                    "operational_request_log_serialization_and_handler_io",
                ],
                "excluded_harness_work": [
                    "request_preparation",
                    "response_validation",
                    "benchmark_layer_event_serialization_and_sink_io",
                ],
            },
        },
        "ai_usage": {
            "tokens": {
                "value_kind": "simulated",
                "source": "synthetic_provider_reported_counters",
                "successful_calls": len(simulated_usage),
                "input_tokens": sum(
                    _token_count(item.input_tokens) for item in simulated_usage
                ),
                "output_tokens": sum(
                    _token_count(item.output_tokens) for item in simulated_usage
                ),
                "total_tokens": sum(
                    _token_count(item.total_tokens) for item in simulated_usage
                ),
            },
            "failed_provider_attempts": {
                "value_kind": "not_available",
                "attempts": len(failed_generation),
                "reason": "provider_error_has_no_valid_usage_envelope",
            },
            "cost": {
                "value_kind": "not_available",
                "currency": None,
                "value": None,
                "reason": "synthetic_provider_has_no_billable_price",
            },
        },
        "limits": {
            "synthetic_only": True,
            "network_calls": False,
            "paid_provider_calls": False,
            "original_materials_accessed": False,
            "memory_scope": (
                "tracemalloc tracks Python allocations only; it excludes RSS and "
                "native allocations"
            ),
            "capacity_claim": False,
        },
    }


def _summarize_events(events: tuple[_LayerEvent, ...]) -> dict[str, object]:
    valid = sorted(event.duration_ms for event in events if event.status == "success")
    errors = sum(event.status == "error" for event in events)
    attempts = len(events)
    return {
        "value_kind": "measured",
        "unit": "milliseconds",
        "timer": "time.perf_counter_ns",
        "percentile_method": "nearest_rank",
        "percentile_population": "successful_attempts_only",
        "error_rate_population": "all_attempts",
        "attempt_count": attempts,
        "valid_sample_count": len(valid),
        "error_count": errors,
        "error_rate": 0.0 if attempts == 0 else errors / attempts,
        "p50": _nearest_rank(valid, 0.50),
        "p95": _nearest_rank(valid, 0.95),
    }


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    rank = max(1, ceil(percentile * len(values)))
    return round(values[rank - 1], 6)


def _elapsed_milliseconds(started_at_ns: int) -> float:
    elapsed_ns = perf_counter_ns() - started_at_ns
    if elapsed_ns < 0:
        raise AnalysisBenchmarkError("Benchmark monotonic timer regressed.")
    return elapsed_ns / 1_000_000.0


def _require_timed_pass_without_tracing() -> None:
    if tracemalloc.is_tracing():
        raise AnalysisBenchmarkError(
            "Python allocation tracing must remain inactive during the timed pass."
        )


def _start_memory_trace() -> None:
    if tracemalloc.is_tracing():
        raise AnalysisBenchmarkError(
            "Python allocation tracing must be inactive before each sample."
        )
    try:
        tracemalloc.start()
    except Exception:
        raise AnalysisBenchmarkError(
            "Python allocation tracing could not be started safely."
        ) from None


def _schedule_sha256(schedule: Sequence[BenchmarkScenario]) -> str:
    return sha256("\n".join(schedule).encode("ascii")).hexdigest()


def _copy_usage(value: ProviderUsage | None) -> ProviderUsage | None:
    if value is None:
        return None
    return ProviderUsage(
        input_tokens=value.input_tokens,
        output_tokens=value.output_tokens,
        total_tokens=value.total_tokens,
    )


def _token_count(value: int | None) -> int:
    if type(value) is not int:
        raise AnalysisBenchmarkError("Synthetic provider usage is incomplete.")
    return value


def _required_package_version(package: str) -> str:
    try:
        return version(package)
    except Exception:
        raise AnalysisBenchmarkError(
            "Required runtime package metadata is unavailable."
        ) from None


def _resolve_repository_root(repository_root: object) -> Path:
    if not isinstance(repository_root, Path):
        raise AnalysisBenchmarkError("Repository root is unavailable.")
    try:
        root = repository_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError
    except (OSError, RuntimeError):
        raise AnalysisBenchmarkError("Repository root is unavailable.") from None
    return root


def _module_repository_root() -> Path:
    try:
        module_path = Path(__file__).resolve(strict=True)
        repository_root = module_path.parents[4]
    except (IndexError, OSError, RuntimeError):
        raise AnalysisBenchmarkError("Repository root is unavailable.") from None
    return _resolve_repository_root(repository_root)


def _read_uv_lock_sha256(repository_root: Path) -> str:
    root = _resolve_repository_root(repository_root)
    try:
        return sha256((root / "uv.lock").read_bytes()).hexdigest()
    except OSError:
        raise AnalysisBenchmarkError("Frozen dependency lock is unavailable.") from None


def _run_git_bytes(repository_root: Path, arguments: tuple[str, ...]) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise AnalysisBenchmarkError("Git is required to bind the benchmark run.")
    try:
        completed = subprocess.run(  # noqa: S603
            (str(Path(executable).resolve()), *arguments),
            cwd=repository_root,
            check=False,
            capture_output=True,
            timeout=10.0,
        )
    except Exception:
        raise AnalysisBenchmarkError("Git state could not be read safely.") from None
    if completed.returncode != 0:
        raise AnalysisBenchmarkError("Git state could not be read safely.")
    if type(completed.stdout) is not bytes:
        raise AnalysisBenchmarkError("Git state could not be read safely.")
    return completed.stdout


def _emit_layer_events(events: Sequence[_LayerEvent]) -> None:
    for event in events:
        _log_layer_event(event)


def _log_layer_event(event: _LayerEvent) -> None:
    record = {
        "benchmark_id": BENCHMARK_ID,
        "correlation_id": event.correlation_id,
        "duration_ms": round(event.duration_ms, 6),
        "event": "analysis_benchmark_layer_completed",
        "layer": event.layer,
        "phase": event.phase,
        "scenario": event.scenario,
        "status": event.status,
    }
    _BENCHMARK_LOGGER.info(
        json.dumps(
            record,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _validated_report_payload(
    payload: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    if type(payload) is not dict:
        raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
    copied = _copy_json_value(cast(object, payload))
    if type(copied) is not dict:
        raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
    canonical_payload = cast(dict[str, object], copied)
    try:
        serialized = (
            json.dumps(
                canonical_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    except (OverflowError, TypeError, ValueError):
        raise AnalysisBenchmarkError("Benchmark report structure is invalid.") from None
    return canonical_payload, serialized


def _copy_json_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
        return value
    if type(value) is list:
        return [_copy_json_value(item) for item in cast(list[object], value)]
    if type(value) is dict:
        copied: dict[str, object] = {}
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
            copied[key] = _copy_json_value(item)
        return copied
    raise AnalysisBenchmarkError("Benchmark report structure is invalid.")


def _freeze_json_value(value: object) -> object:
    if type(value) is dict:
        frozen = {
            cast(str, key): _freeze_json_value(item)
            for key, item in cast(dict[object, object], value).items()
        }
        return MappingProxyType(frozen)
    if type(value) is list:
        return tuple(_freeze_json_value(item) for item in cast(list[object], value))
    return value


def _render_markdown(payload: Mapping[str, object]) -> str:
    repository = _mapping(payload["repository"])
    configuration = _mapping(payload["configuration"])
    metrics = _mapping(payload["metrics"])
    measurement_protocol = _mapping(metrics["measurement_protocol"])
    scenarios = _mapping(metrics["scenarios"])
    scenario_mix = _mapping(metrics["synthetic_scenario_mix"])
    mixed_layers = _mapping(scenario_mix["layers"])
    ai_usage = _mapping(payload["ai_usage"])
    tokens = _mapping(ai_usage["tokens"])
    cost = _mapping(ai_usage["cost"])
    memory = _mapping(metrics["memory"])

    lines = [
        "# Benchmark local da análise prescritiva",
        "",
        f"- Commit: `{repository['commit']}`",
        f"- Árvore de trabalho suja: `{str(repository['working_tree_dirty']).lower()}`",
        f"- SHA-256 canônico da árvore: `{repository['working_tree_sha256']}`",
        f"- Seed: `{configuration['seed']}`",
        (
            "- Iterações: "
            f"`{configuration['warmup_iterations_per_scenario']}` de aquecimento e "
            f"`{configuration['measured_iterations_per_scenario']}` temporizadas, mais "
            f"`{configuration['memory_iterations_per_scenario']}` em passagem "
            "exclusiva de memória por cenário"
        ),
        f"- Top-k: `{configuration['top_k']}`",
        "",
        "## Métricas por cenário (visão principal)",
        "",
        (
            "| Cenário | Resultado esperado | Camada | Tentativas | "
            "Válidas | Erros | Taxa de erro | p50 (ms) | p95 (ms) |"
        ),
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in _SCENARIOS:
        scenario_values = _mapping(scenarios[scenario])
        scenario_layers = _mapping(scenario_values["layers"])
        for layer in _LAYERS:
            lines.append(
                _scenario_metric_row(
                    scenario,
                    str(scenario_values["expected_outcome"]),
                    layer,
                    _mapping(scenario_layers[layer]),
                )
            )

    lines.extend(
        [
            "",
            "## `synthetic_scenario_mix` (visão secundária)",
            "",
            (
                "Este agregado mistura deliberadamente `documented_fault` e "
                "`provider_failure`. Os percentis usam somente tentativas "
                "bem-sucedidas; a taxa de erro usa todas as tentativas do mix."
            ),
            "",
            (
                "| Camada | Amostras válidas | Erros | Taxa de erro | "
                "p50 (ms) | p95 (ms) |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for layer in _LAYERS:
        lines.append(_metric_row(layer, _mapping(mixed_layers[layer])))

    lines.extend(
        [
            "",
            "## Memória e uso de IA",
            "",
            (
                f"O maior pico por requisição foi de "
                f"`{memory['peak_traced_bytes']}` bytes por `tracemalloc`. "
                "A captura usa serviço e aplicação novos, em uma passagem separada "
                "dos timers. Serialização da resposta e logging operacional da "
                "aplicação, inclusive o handler, entram no pico; preparação, "
                "validação pelo harness e eventos de camada do benchmark ficam fora. "
                "A medição cobre somente alocações Python rastreadas; não representa "
                "RSS nem memória nativa."
            ),
            "",
            (
                "Os contadores de tokens são `simulated`, informados pelo provider "
                "fake: "
                f"entrada `{tokens['input_tokens']}`, saída "
                f"`{tokens['output_tokens']}` e "
                f"total `{tokens['total_tokens']}`."
            ),
            "",
            (
                f"Custo: `{cost['value_kind']}`. O provider sintético não possui preço "
                "faturável, portanto nenhum valor foi inventado."
            ),
            "",
            "## Limites",
            "",
            "O benchmark é curto, local, sequencial, inteiramente sintético e offline. "
            "O aquecimento não entra nas distribuições; falhas de provider são "
            "contadas, "
            "mas seu tempo não entra na latência válida de geração. Os números não "
            "representam capacidade, SLO, modelo real, Bedrock ou decisão industrial.",
            (
                "Todos os percentis e contadores de uso vêm da passagem temporizada "
                "sem `tracemalloc`. O `http_total` inclui serialização da resposta e "
                "logging operacional da aplicação: "
                f"`{measurement_protocol['timed_pass']}`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _metric_row(layer: BenchmarkLayer, summary: Mapping[str, object]) -> str:
    return (
        f"| {layer} | {summary['valid_sample_count']} | {summary['error_count']} | "
        f"{_format_number(summary['error_rate'])} | "
        f"{_format_number(summary['p50'])} | {_format_number(summary['p95'])} |"
    )


def _scenario_metric_row(
    scenario: BenchmarkScenario,
    expected_outcome: str,
    layer: BenchmarkLayer,
    summary: Mapping[str, object],
) -> str:
    return (
        f"| {scenario} | {expected_outcome} | {layer} | "
        f"{summary['attempt_count']} | {summary['valid_sample_count']} | "
        f"{summary['error_count']} | {_format_number(summary['error_rate'])} | "
        f"{_format_number(summary['p50'])} | {_format_number(summary['p95'])} |"
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AnalysisBenchmarkError("Benchmark report structure is invalid.")
    return cast(Mapping[str, object], value)


def _format_number(value: object) -> str:
    if value is None:
        return "n/a"
    if type(value) is int:
        return str(value)
    if type(value) is float and isfinite(value):
        return f"{value:.6f}"
    raise AnalysisBenchmarkError("Benchmark report number is invalid.")


def _configure_event_logging() -> None:
    if _BENCHMARK_LOGGER.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _BENCHMARK_LOGGER.addHandler(handler)
    _BENCHMARK_LOGGER.setLevel(logging.INFO)
    _BENCHMARK_LOGGER.propagate = False


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa um benchmark local, sintético e offline do POST /analysis."
        )
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_ITERATIONS,
        help="iterações de aquecimento por cenário (padrão: 2)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_MEASURED_ITERATIONS,
        help="iterações medidas por cenário (padrão: 10)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="seed da ordem balanceada dos cenários (padrão: 65)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="quantidade de vizinhos sintéticos (padrão: 3)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="formato sanitizado escrito em stdout (padrão: json)",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    options = parser.parse_args(arguments)
    try:
        config = AnalysisBenchmarkConfig(
            warmup_iterations=options.warmup,
            measured_iterations=options.iterations,
            seed=options.seed,
            top_k=options.top_k,
        )
        repository_root = _module_repository_root()
        _configure_event_logging()
        report = run_local_analysis_benchmark(
            config,
            repository_root=repository_root,
        )
    except (AnalysisBenchmarkError, ValueError) as error:
        parser.error(str(error))
    output = report.to_json() if options.format == "json" else report.to_markdown()
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
