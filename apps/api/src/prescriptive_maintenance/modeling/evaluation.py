"""Frozen, no-tuning evaluation protocol for the exact temporal k-NN engine."""

from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import os
import platform
import re
import stat
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import fsum
from pathlib import Path
from typing import Final, NoReturn, Protocol, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn import (  # pyright: ignore[reportMissingTypeStubs]
    __version__ as sklearn_version,
)
from sklearn.metrics import (  # pyright: ignore[reportMissingTypeStubs]
    pairwise_distances_chunked,  # pyright: ignore[reportUnknownVariableType]
)

from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_COUNT,
    ANALYSIS_FEATURE_NAMES,
)
from prescriptive_maintenance.modeling.knn import (
    KNN_METRIC,
    KNN_SUPPORT_HEURISTIC,
    InMemoryKnnModel,
    KnnAbstentionPolicy,
    KnnError,
    KnnEvaluationSnapshot,
    load_knn_model,
)
from prescriptive_maintenance.modeling.similarity_index import (
    LoadedSimilarityIndex,
    SimilarityIndexCompatibility,
    SimilarityIndexError,
    load_similarity_index,
)
from prescriptive_maintenance.operating_states import (
    operating_state_policy_payload,
    resolve_operating_state,
)
from prescriptive_maintenance.ports import ModelAbstentionReason

MODEL_EVALUATION_SCHEMA_VERSION: Final = 3
MODEL_EVALUATION_PROTOCOL_VERSION: Final = "temporal-knn-open-set-exact.v3"
MODEL_EVALUATION_TOP_K: Final = 5
MODEL_EVALUATION_WORKING_MEMORY_MIB: Final = 64
MODEL_EVALUATION_PARITY_SAMPLE_COUNT: Final = 16
MODEL_EVALUATION_LATENCY_WARMUP_COUNT: Final = 5
MODEL_EVALUATION_LATENCY_SAMPLE_COUNT: Final = 64
MODEL_EVALUATION_HOLDOUT_PARTITION: Final = "test"

_PLAN_ID_PREFIX: Final = "evaluation_plan_v3_"
_HOLDOUT_FILENAME: Final = "test.parquet"
_MANIFEST_MAXIMUM_BYTES: Final = 2 * 1024 * 1024
_HOLDOUT_MAXIMUM_BYTES: Final = 128 * 1024 * 1024
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID_PATTERN: Final = re.compile(r"^model_[a-z0-9_.-]{3,64}$")
_INDEX_ID_PATTERN: Final = re.compile(r"^similarity_index_[a-z0-9_.-]{3,64}$")
_EVALUATED_ENGINE: Final = "in_memory_knn_exact"
_INDEX_EVALUATION_ROLE: Final = "identity_and_compatibility_binding_only"
_METRIC_NAMES: Final[tuple[str, ...]] = (
    "candidate_operational_objective_accuracy",
    "candidate_always_problem_baseline_accuracy",
    "candidate_operational_objective_balanced_accuracy",
    "candidate_operational_recall",
    "candidate_problem_recall",
    "selective_operational_objective_accuracy",
    "selective_always_problem_baseline_accuracy",
    "selective_operational_objective_balanced_accuracy",
    "selective_operational_recall",
    "selective_problem_recall",
    "coverage",
    "abstention_rate",
    "candidate_top1_accuracy",
    "neighbor_hit_at_1",
    "neighbor_hit_at_k",
    "neighbor_precision_at_k",
    "neighbor_recall_at_k",
    "neighbor_incremental_hit_at_positions_2_to_k",
    "neighbor_mrr_at_k",
    "majority_class_accuracy",
    "selective_candidate_accuracy",
    "open_set_known_coverage",
    "open_set_known_selective_accuracy",
    "open_set_unknown_false_acceptance_rate",
    "open_set_unknown_rejection_rate",
)
_ALWAYS_PROBLEM_BASELINE_TYPE: Final = "constant_class_accuracy"
_ALWAYS_PROBLEM_BASELINE_STRATEGY: Final = "always_problem"
_ALWAYS_PROBLEM_BASELINE_FORMULA: Final = "actual_problem_count / scope_row_count"

type FloatMatrix = NDArray[np.float64]
type FloatVector = NDArray[np.float64]
type IndexMatrix = NDArray[np.int64]
type BoolVector = NDArray[np.bool_]


class ModelEvaluationError(Exception):
    """Raised when the frozen evaluation cannot be reproduced safely."""


@dataclass(frozen=True, slots=True)
class _PartitionBinding:
    filename: str
    row_count: int
    column_count: int
    physical_sha256: str


@dataclass(frozen=True, slots=True)
class _DatasetBinding:
    dataset_id: str
    schema_id: str
    manifest_sha256: str
    train: _PartitionBinding
    validation: _PartitionBinding
    test: _PartitionBinding


@dataclass(frozen=True, slots=True)
class FrozenEvaluationPlan:
    """Immutable method and artifact identities fixed before holdout access."""

    plan_id: str
    dataset_id: str
    dataset_schema_id: str
    dataset_manifest_sha256: str
    model_id: str
    model_content_sha256: str
    index_id: str
    index_content_sha256: str
    holdout_partition_sha256: str
    holdout_row_count: int
    calibration_partition: str
    calibration_partition_sha256: str
    policy: KnnAbstentionPolicy

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": MODEL_EVALUATION_SCHEMA_VERSION,
            "protocol_version": MODEL_EVALUATION_PROTOCOL_VERSION,
            "plan_id": self.plan_id,
            "dataset_id": self.dataset_id,
            "dataset_schema_id": self.dataset_schema_id,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "model_id": self.model_id,
            "model_content_sha256": self.model_content_sha256,
            "index_id": self.index_id,
            "index_content_sha256": self.index_content_sha256,
            "holdout": {
                "partition": MODEL_EVALUATION_HOLDOUT_PARTITION,
                "physical_sha256": self.holdout_partition_sha256,
                "row_count": self.holdout_row_count,
            },
            "method": {
                "evaluated_engine": _EVALUATED_ENGINE,
                "operational_index_role": _INDEX_EVALUATION_ROLE,
                "metric": KNN_METRIC,
                "top_k": MODEL_EVALUATION_TOP_K,
                "search": "exact_full_distance_batched",
                "distance_order": "ascending",
                "distance_tie_break": "neighbor_ref_ascending",
                "candidate_selection": (
                    "vote_count_descending_then_distance_sum_ascending_"
                    "then_target_slug_ascending"
                ),
                "working_memory_mib": MODEL_EVALUATION_WORKING_MEMORY_MIB,
                "metric_scopes": {
                    "primary": (
                        "candidate_all_rows_and_selective_accepted_"
                        "operational_vs_problem"
                    ),
                    "secondary_exact_label_diagnostics": [
                        "all_rows",
                        "known_train_classes",
                        "unknown_train_classes",
                    ],
                },
                "metrics": list(_METRIC_NAMES),
                "baseline_definitions": _baseline_definitions_payload(),
                "operating_state_policy": operating_state_policy_payload(),
            },
            "abstention_policy": {
                "version": self.policy.version,
                "calibration_partition": self.calibration_partition,
                "calibration_partition_sha256": (self.calibration_partition_sha256),
                "calibration_sample_count": self.policy.calibration_sample_count,
                "distance_quantile": self.policy.distance_quantile,
                "distance_threshold": self.policy.distance_threshold,
                "vote_margin_quantile": self.policy.vote_margin_quantile,
                "vote_margin_threshold": self.policy.vote_margin_threshold,
                "minimum_class_count": self.policy.minimum_class_count,
                "support_heuristic": KNN_SUPPORT_HEURISTIC,
                "support_interpretation": "heuristic_not_probability",
            },
            "parity_audit": {
                "sampling": "evenly_spaced_holdout_order",
                "sample_count": MODEL_EVALUATION_PARITY_SAMPLE_COUNT,
            },
            "latency_benchmark": {
                "unit": "single_model_query",
                "cache_state": "warm_after_full_holdout_evaluation",
                "sampling": "evenly_spaced_holdout_order",
                "warmup_count": MODEL_EVALUATION_LATENCY_WARMUP_COUNT,
                "sample_count": MODEL_EVALUATION_LATENCY_SAMPLE_COUNT,
                "percentiles": [0.5, 0.95],
            },
            "memory_benchmark": {
                "primary_measure": "process_lifetime_peak_rss_or_working_set",
                "complementary_measure": "tracemalloc_peak_delta_during_batch",
                "scope": (
                    "process high-water mark observed immediately before and "
                    "after batched evaluation"
                ),
                "limitation": (
                    "the process peak includes prior allocations; tracemalloc "
                    "does not include every native NumPy or BLAS allocation"
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class FrozenEvaluationContext:
    """Validated objects bound to a plan that does not require holdout bytes."""

    plan: FrozenEvaluationPlan
    model: InMemoryKnnModel
    index: LoadedSimilarityIndex
    dataset: _DatasetBinding
    materialized_plan: bytes
    materialized_plan_sha256: str


@dataclass(frozen=True, slots=True)
class ScopeMetrics:
    """Counts and rates with explicit denominators for one row scope."""

    row_count: int
    candidate_correct_count: int
    neighbor_hit_at_1_count: int
    neighbor_hit_at_k_count: int
    reciprocal_rank_sum: float
    majority_correct_count: int
    accepted_count: int
    accepted_candidate_correct_count: int
    abstention_counts: tuple[tuple[str, int], ...]
    hit_counts_by_k: tuple[int, ...]
    relevant_retrieved_counts_by_k: tuple[int, ...]
    retrieved_counts_by_k: tuple[int, ...]
    first_hit_counts_by_position: tuple[int, ...]
    relevant_available_count: int
    absent_relevance_row_count: int

    def to_payload(self) -> dict[str, object]:
        abstained_count = self.row_count - self.accepted_count
        return {
            "row_count": self.row_count,
            "candidate_top1": _rate_payload(
                self.candidate_correct_count,
                self.row_count,
            ),
            "neighbor_hit_at_1": _rate_payload(
                self.neighbor_hit_at_1_count,
                self.row_count,
            ),
            "neighbor_hit_at_k": _rate_payload(
                self.neighbor_hit_at_k_count,
                self.row_count,
            ),
            "neighbor_mrr_at_k": {
                "reciprocal_rank_sum": round(self.reciprocal_rank_sum, 12),
                "denominator": self.row_count,
                "value": (
                    None
                    if self.row_count == 0
                    else _ratio(self.reciprocal_rank_sum, self.row_count)
                ),
            },
            "neighbor_ranking": _neighbor_ranking_payload(self),
            "majority_class_baseline": _rate_payload(
                self.majority_correct_count,
                self.row_count,
            ),
            "coverage": _rate_payload(self.accepted_count, self.row_count),
            "abstention": {
                **_rate_payload(abstained_count, self.row_count),
                "reasons": {reason: count for reason, count in self.abstention_counts},
            },
            "selective_candidate_accuracy": _rate_payload(
                self.accepted_candidate_correct_count,
                self.accepted_count,
            ),
        }


@dataclass(frozen=True, slots=True)
class AlwaysProblemBaselineAccuracy:
    """Accuracy of the constant problem candidate in one explicit scope."""

    actual_problem_count: int
    scope_row_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "metric_type": _ALWAYS_PROBLEM_BASELINE_TYPE,
            "strategy": _ALWAYS_PROBLEM_BASELINE_STRATEGY,
            "formula": _ALWAYS_PROBLEM_BASELINE_FORMULA,
            **_rate_payload(self.actual_problem_count, self.scope_row_count),
        }


@dataclass(frozen=True, slots=True)
class OperationalObjectiveMetrics:
    """Candidate and selective operating-state versus problem metrics."""

    candidate_operational_as_operational_count: int
    candidate_operational_as_problem_count: int
    candidate_problem_as_operational_count: int
    candidate_problem_as_problem_count: int
    selective_operational_as_operational_count: int
    selective_operational_as_problem_count: int
    selective_problem_as_operational_count: int
    selective_problem_as_problem_count: int
    abstention_counts: tuple[tuple[str, int], ...]

    def to_payload(self) -> dict[str, object]:
        candidate = _operational_candidate_scope_payload(
            prefix="candidate",
            baseline_metric_name="candidate_always_problem_baseline_accuracy",
            operational_as_operational=(
                self.candidate_operational_as_operational_count
            ),
            operational_as_problem=self.candidate_operational_as_problem_count,
            problem_as_operational=self.candidate_problem_as_operational_count,
            problem_as_problem=self.candidate_problem_as_problem_count,
        )
        selective = _operational_candidate_scope_payload(
            prefix="selective_candidate",
            baseline_metric_name="selective_always_problem_baseline_accuracy",
            operational_as_operational=(
                self.selective_operational_as_operational_count
            ),
            operational_as_problem=self.selective_operational_as_problem_count,
            problem_as_operational=self.selective_problem_as_operational_count,
            problem_as_problem=self.selective_problem_as_problem_count,
        )
        row_count = cast(int, candidate["row_count"])
        accepted_count = cast(int, selective["row_count"])
        abstained_count = row_count - accepted_count
        return {
            "candidate_all_rows": candidate,
            "selective_accepted": selective,
            "coverage": _rate_payload(accepted_count, row_count),
            "abstained": {
                "count": abstained_count,
                "rate": _rate_payload(abstained_count, row_count),
                "reasons": {reason: count for reason, count in self.abstention_counts},
            },
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Sanitized aggregate report; no labels, rows, features, or paths."""

    plan: FrozenEvaluationPlan
    materialized_plan_sha256: str
    hardware: Mapping[str, object]
    operational_objective: OperationalObjectiveMetrics
    all_rows: ScopeMetrics
    known_train_classes: ScopeMetrics
    unknown_train_classes: ScopeMetrics
    boundary_repair_row_count: int
    parity_audit_count: int
    evaluation_seconds: float
    latency_milliseconds: tuple[float, ...]
    process_peak_before: _ProcessPeakMemory
    process_peak_after: _ProcessPeakMemory
    peak_traced_allocation_bytes: int

    @property
    def unknown_class_row_count(self) -> int:
        """Preserve the previous aggregate accessor for report consumers."""

        return self.unknown_train_classes.row_count

    def to_payload(self) -> dict[str, object]:
        return {
            "report_schema_version": MODEL_EVALUATION_SCHEMA_VERSION,
            "materialized_plan_sha256": self.materialized_plan_sha256,
            "plan": self.plan.to_payload(),
            "hardware": dict(self.hardware),
            "holdout_summary": {
                "all_row_count": self.all_rows.row_count,
                "known_train_class_row_count": self.known_train_classes.row_count,
                "unknown_train_class_row_count": self.unknown_train_classes.row_count,
            },
            "metrics": {
                "definitions": _metric_definitions(),
                "primary_operational_objective": (
                    self.operational_objective.to_payload()
                ),
                "secondary_exact_label_diagnostics": {
                    "all_rows": self.all_rows.to_payload(),
                    "known_train_classes": self.known_train_classes.to_payload(),
                    "unknown_train_classes": self.unknown_train_classes.to_payload(),
                },
                "open_set": _open_set_payload(
                    self.known_train_classes,
                    self.unknown_train_classes,
                ),
            },
            "execution": {
                "full_distance_rows": self.all_rows.row_count,
                "boundary_repair_row_count": self.boundary_repair_row_count,
                "parity_audit_count": self.parity_audit_count,
                "evaluation_seconds": round(self.evaluation_seconds, 6),
                "latency": {
                    "cache_state": "warm_after_full_holdout_evaluation",
                    "warmup_count": MODEL_EVALUATION_LATENCY_WARMUP_COUNT,
                    "sample_count": len(self.latency_milliseconds),
                    "p50_milliseconds": _percentile(
                        self.latency_milliseconds,
                        0.5,
                    ),
                    "p95_milliseconds": _percentile(
                        self.latency_milliseconds,
                        0.95,
                    ),
                },
                "memory": {
                    "process_peak": _process_peak_payload(
                        self.process_peak_before,
                        self.process_peak_after,
                    ),
                    "traced_allocations_complement": {
                        "peak_delta_bytes": self.peak_traced_allocation_bytes,
                        "peak_delta_mib": round(
                            self.peak_traced_allocation_bytes / (1024 * 1024),
                            6,
                        ),
                        "measure": "tracemalloc_peak_delta_during_batch",
                        "limitation": (
                            "Não substitui RSS/working set e pode omitir buffers "
                            "nativos de NumPy/BLAS."
                        ),
                    },
                },
            },
            "limitations": [
                "O suporte é heurístico e não representa probabilidade.",
                "O protocolo não ajusta hiperparâmetros ou limiares no teste.",
                (
                    "O objetivo operacional versus problema é pós-hoc no mesmo "
                    "holdout já observado e não constitui validação independente."
                ),
                (
                    "A correspondência exata de labels permanece apenas como "
                    "diagnóstico secundário."
                ),
                "Classes ausentes do treino não podem ser previstas pelo candidato.",
                (
                    "Precisão e recall do ranking usam linhas de treino; "
                    "não representam ocorrências independentes."
                ),
                "A busca é exata e O(N); o lote reduz overhead sem mudar o ranking.",
                (
                    "O índice operacional é vinculado por identidade e "
                    "compatibilidade; a avaliação mede o k-NN exato em memória."
                ),
                "As métricas não aprovam o modelo para uso operacional.",
            ],
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_payload(),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        )


@dataclass(frozen=True, slots=True)
class _RowEvaluation:
    truth: str
    candidate: str
    neighbor_targets: tuple[str, ...]
    abstention_reason: ModelAbstentionReason | None


@dataclass(frozen=True, slots=True)
class _ProcessPeakMemory:
    value_bytes: int | None
    source: str


class _WindowsGetCurrentProcess(Protocol):
    restype: object

    def __call__(self) -> int: ...


class _WindowsGetProcessMemoryInfo(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, handle: int, counters: object, size: int) -> int: ...


class _WindowsProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def freeze_evaluation_plan(
    *,
    dataset_manifest_path: Path,
    model_artifact_directory: Path,
    index_artifact_directory: Path,
) -> FrozenEvaluationContext:
    """Validate identities and freeze every method choice without test bytes."""

    dataset = _load_dataset_binding(dataset_manifest_path)
    try:
        model = load_knn_model(model_artifact_directory)
        index = load_similarity_index(
            index_artifact_directory,
            expected=SimilarityIndexCompatibility(
                dataset_id=dataset.dataset_id,
                schema_id=dataset.schema_id,
            ),
        )
    except (KnnError, SimilarityIndexError, OSError, ValueError) as error:
        raise ModelEvaluationError("Model artifacts are incompatible.") from error
    if (
        model.dataset_id != dataset.dataset_id
        or model.training_partition_sha256 != dataset.train.physical_sha256
        or model.default_top_k != MODEL_EVALUATION_TOP_K
        or index.manifest.source_model_id != model.model_id
        or index.manifest.source_model_content_sha256 != model.content_sha256
        or index.manifest.record_count != model.sample_count
    ):
        raise ModelEvaluationError("Evaluation artifact identities are inconsistent.")
    policy = model.abstention_policy
    calibration = (
        dataset.validation
        if policy.calibration_partition == "validation"
        else dataset.train
        if policy.calibration_partition == "train_leave_one_out"
        else None
    )
    if (
        calibration is None
        or policy.calibration_partition_sha256 != calibration.physical_sha256
        or policy.calibration_partition_sha256 == dataset.test.physical_sha256
    ):
        raise ModelEvaluationError("Abstention policy depends on the holdout.")

    payload = _plan_identity_payload(
        dataset=dataset,
        model=model,
        index=index,
        policy=policy,
    )
    plan_id = (
        f"{_PLAN_ID_PREFIX}{sha256(_canonical_json_bytes(payload)).hexdigest()[:32]}"
    )
    plan = FrozenEvaluationPlan(
        plan_id=plan_id,
        dataset_id=dataset.dataset_id,
        dataset_schema_id=dataset.schema_id,
        dataset_manifest_sha256=dataset.manifest_sha256,
        model_id=model.model_id,
        model_content_sha256=model.content_sha256,
        index_id=index.selector.index_id,
        index_content_sha256=index.manifest.content_sha256,
        holdout_partition_sha256=dataset.test.physical_sha256,
        holdout_row_count=dataset.test.row_count,
        calibration_partition=policy.calibration_partition,
        calibration_partition_sha256=policy.calibration_partition_sha256,
        policy=policy,
    )
    if plan.plan_id != (
        f"{_PLAN_ID_PREFIX}"
        f"{sha256(_canonical_json_bytes(_plan_identity_payload_from_plan(plan))).hexdigest()[:32]}"
    ):
        raise ModelEvaluationError("Frozen evaluation plan identity is invalid.")
    materialized_plan = _canonical_json_bytes(plan.to_payload())
    materialized_plan_sha256 = sha256(materialized_plan).hexdigest()
    return FrozenEvaluationContext(
        plan=plan,
        model=model,
        index=index,
        dataset=dataset,
        materialized_plan=materialized_plan,
        materialized_plan_sha256=materialized_plan_sha256,
    )


def evaluate_frozen_holdout(
    context: FrozenEvaluationContext,
    *,
    holdout_path: Path,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> EvaluationReport:
    """Open the holdout once, after freezing, and compute sanitized aggregates."""

    _verify_materialized_plan(context)
    holdout = _load_holdout_once(
        holdout_path,
        expected=context.dataset.test,
    )
    snapshot = context.model.evaluation_snapshot()
    transformed = _transform_holdout(
        holdout,
        context.model,
    )

    process_peak_before = _process_peak_memory()
    tracemalloc.start()
    baseline_bytes, _baseline_peak = tracemalloc.get_traced_memory()
    started_ns = clock_ns()
    try:
        positions, distances, repair_count = _exact_ranked_neighbors(
            transformed,
            snapshot=snapshot,
            top_k=MODEL_EVALUATION_TOP_K,
        )
        outcomes = _evaluate_rows(
            holdout,
            model=context.model,
            snapshot=snapshot,
            positions=positions,
            distances=distances,
        )
        parity_count = _audit_batch_parity(
            holdout,
            model=context.model,
            outcomes=outcomes,
        )
        finished_ns = clock_ns()
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    process_peak_after = _process_peak_memory()

    training_target_counts = {
        label.target_slug: snapshot.class_counts[position]
        for position, label in enumerate(snapshot.labels)
    }
    training_targets = frozenset(training_target_counts)
    majority_target = min(
        range(len(snapshot.labels)),
        key=lambda index: (
            -snapshot.class_counts[index],
            snapshot.labels[index].target_slug,
        ),
    )
    majority_label = snapshot.labels[majority_target].target_slug
    all_mask = np.ones(len(outcomes), dtype=np.bool_)
    known_mask = np.asarray(
        [item.truth in training_targets for item in outcomes],
        dtype=np.bool_,
    )
    unknown_mask = np.logical_not(known_mask)
    all_metrics = _scope_metrics(
        outcomes,
        all_mask,
        majority_label,
        training_target_counts,
    )
    known_metrics = _scope_metrics(
        outcomes,
        known_mask,
        majority_label,
        training_target_counts,
    )
    unknown_metrics = _scope_metrics(
        outcomes,
        unknown_mask,
        majority_label,
        training_target_counts,
    )
    operational_metrics = _operational_objective_metrics(outcomes)
    latency = _benchmark_latency(
        holdout,
        model=context.model,
        clock_ns=clock_ns,
    )
    return EvaluationReport(
        plan=context.plan,
        materialized_plan_sha256=context.materialized_plan_sha256,
        hardware=_hardware_profile(),
        operational_objective=operational_metrics,
        all_rows=all_metrics,
        known_train_classes=known_metrics,
        unknown_train_classes=unknown_metrics,
        boundary_repair_row_count=repair_count,
        parity_audit_count=parity_count,
        evaluation_seconds=max(0.0, (finished_ns - started_ns) / 1_000_000_000),
        latency_milliseconds=latency,
        process_peak_before=process_peak_before,
        process_peak_after=process_peak_after,
        peak_traced_allocation_bytes=max(0, peak_bytes - baseline_bytes),
    )


def run_evaluation(
    *,
    dataset_manifest_path: Path,
    holdout_path: Path,
    model_artifact_directory: Path,
    index_artifact_directory: Path,
) -> EvaluationReport:
    """Freeze first and only then delegate the one holdout read."""

    context = freeze_evaluation_plan(
        dataset_manifest_path=dataset_manifest_path,
        model_artifact_directory=model_artifact_directory,
        index_artifact_directory=index_artifact_directory,
    )
    return evaluate_frozen_holdout(context, holdout_path=holdout_path)


def save_evaluation_report(report: EvaluationReport, output_path: Path) -> None:
    """Persist one sanitized report without overwriting prior evidence."""

    payload = report.to_json().encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except OSError:
        raise ModelEvaluationError(
            "Evaluation report destination is unavailable."
        ) from None
    try:
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
    except OSError:
        with suppress(OSError):
            os.close(descriptor)
        raise ModelEvaluationError("Evaluation report could not be written.") from None
    failure = False
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                failure = True
                break
            offset += written
        if not failure:
            os.fsync(descriptor)
    except OSError:
        failure = True
    try:
        os.close(descriptor)
    except OSError:
        failure = True
        with suppress(OSError):
            os.close(descriptor)
    if failure:
        _remove_owned_report(output_path, identity=identity)
        raise ModelEvaluationError("Evaluation report could not be written.")


def _remove_owned_report(path: Path, *, identity: tuple[int, int]) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise ModelEvaluationError("Evaluation report cleanup is unsafe.")
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        raise ModelEvaluationError("Evaluation report cleanup failed.") from None


def _exact_ranked_neighbors(
    transformed: FloatMatrix,
    *,
    snapshot: KnnEvaluationSnapshot,
    top_k: int,
) -> tuple[IndexMatrix, FloatMatrix, int]:
    row_count = len(transformed)
    positions = np.empty((row_count, top_k), dtype=np.int64)
    distances = np.empty((row_count, top_k), dtype=np.float64)
    next_row = 0
    boundary_repair_count = 0
    chunks = cast(
        Iterable[FloatMatrix],
        pairwise_distances_chunked(
            transformed,
            snapshot.training_vectors,
            metric=KNN_METRIC,
            n_jobs=1,
            working_memory=MODEL_EVALUATION_WORKING_MEMORY_MIB,
        ),
    )
    for raw_chunk in chunks:
        chunk = np.asarray(raw_chunk, dtype=np.float64, order="C")
        if (
            chunk.ndim != 2
            or chunk.shape[1] != len(snapshot.training_vectors)
            or not np.isfinite(chunk).all()
            or np.any(chunk < 0.0)
        ):
            raise ModelEvaluationError("Exact distance batch is invalid.")
        for relative_row, row_distances in enumerate(chunk):
            boundary = float(np.partition(row_distances, top_k - 1)[top_k - 1])
            guard = max(
                1e-12,
                abs(boundary) * np.finfo(np.float64).eps * ANALYSIS_FEATURE_COUNT * 64,
            )
            candidates = np.flatnonzero(row_distances <= boundary + guard)
            if len(candidates) < top_k:
                raise ModelEvaluationError("Exact ranking boundary is invalid.")
            if len(candidates) > top_k:
                boundary_repair_count += 1
            query = transformed[next_row + relative_row]
            canonical_distances = cast(
                FloatVector,
                np.linalg.norm(
                    snapshot.training_vectors[candidates] - query,
                    axis=1,
                ),
            )
            if not np.isfinite(canonical_distances).all():
                raise ModelEvaluationError("Canonical distance repair failed.")
            order = np.lexsort(
                (snapshot.neighbor_refs[candidates], canonical_distances)
            )
            selected = np.asarray(candidates[order[:top_k]], dtype=np.int64)
            selected_distances = np.asarray(
                canonical_distances[order[:top_k]],
                dtype=np.float64,
            )
            positions[next_row + relative_row] = selected
            distances[next_row + relative_row] = selected_distances
        next_row += len(chunk)
    if (
        next_row != row_count
        or not np.isfinite(distances).all()
        or np.any(distances < 0.0)
    ):
        raise ModelEvaluationError("Exact ranking did not cover the holdout.")
    return positions, distances, boundary_repair_count


def _evaluate_rows(
    holdout: pd.DataFrame,
    *,
    model: InMemoryKnnModel,
    snapshot: KnnEvaluationSnapshot,
    positions: IndexMatrix,
    distances: FloatMatrix,
) -> tuple[_RowEvaluation, ...]:
    truths = tuple(cast(str, value) for value in holdout["y"])
    rows: list[_RowEvaluation] = []
    for row_index, truth in enumerate(truths):
        row_positions = tuple(int(value) for value in positions[row_index])
        row_distances = tuple(float(value) for value in distances[row_index])
        candidate = model.candidate_from_ranked_neighbors(
            row_positions,
            row_distances,
        )
        neighbor_targets = tuple(
            snapshot.labels[int(snapshot.target_indices[position])].target_slug
            for position in row_positions
        )
        rows.append(
            _RowEvaluation(
                truth=truth,
                candidate=candidate.target_slug,
                neighbor_targets=neighbor_targets,
                abstention_reason=candidate.abstention_reason,
            )
        )
    return tuple(rows)


def _audit_batch_parity(
    holdout: pd.DataFrame,
    *,
    model: InMemoryKnnModel,
    outcomes: tuple[_RowEvaluation, ...],
) -> int:
    indices = _evenly_spaced_indices(
        len(holdout),
        MODEL_EVALUATION_PARITY_SAMPLE_COUNT,
    )
    for row_index in indices:
        features = _row_features(holdout, row_index)
        direct = model.predict_candidate(
            features,
            top_k=MODEL_EVALUATION_TOP_K,
        )
        outcome = outcomes[row_index]
        if (
            direct.target_slug != outcome.candidate
            or direct.abstention_reason != outcome.abstention_reason
            or tuple(item.target_slug for item in direct.neighbors)
            != outcome.neighbor_targets
        ):
            raise ModelEvaluationError(
                "Batched exact ranking diverges from the model contract."
            )
    return len(indices)


def _scope_metrics(
    outcomes: tuple[_RowEvaluation, ...],
    mask: BoolVector,
    majority_label: str,
    relevant_counts: Mapping[str, int],
) -> ScopeMetrics:
    selected = tuple(item for item, keep in zip(outcomes, mask, strict=True) if keep)
    candidate_correct = sum(item.candidate == item.truth for item in selected)
    hit_counts = [0] * MODEL_EVALUATION_TOP_K
    relevant_retrieved_counts = [0] * MODEL_EVALUATION_TOP_K
    retrieved_counts = [0] * MODEL_EVALUATION_TOP_K
    first_hit_counts = [0] * MODEL_EVALUATION_TOP_K
    reciprocal_ranks: list[float] = []
    for item in selected:
        matches = tuple(target == item.truth for target in item.neighbor_targets)
        first_hit = next(
            (position for position, match in enumerate(matches) if match),
            None,
        )
        reciprocal_ranks.append(0.0 if first_hit is None else 1.0 / (first_hit + 1))
        if first_hit is not None and first_hit < MODEL_EVALUATION_TOP_K:
            first_hit_counts[first_hit] += 1
        for position in range(MODEL_EVALUATION_TOP_K):
            prefix = matches[: position + 1]
            relevant_count = sum(prefix)
            retrieved_counts[position] += len(prefix)
            relevant_retrieved_counts[position] += relevant_count
            hit_counts[position] += relevant_count > 0
    accepted = tuple(item for item in selected if item.abstention_reason is None)
    reasons = tuple(
        (
            reason.value,
            sum(item.abstention_reason is reason for item in selected),
        )
        for reason in ModelAbstentionReason
    )
    return ScopeMetrics(
        row_count=len(selected),
        candidate_correct_count=candidate_correct,
        neighbor_hit_at_1_count=hit_counts[0],
        neighbor_hit_at_k_count=hit_counts[-1],
        reciprocal_rank_sum=fsum(reciprocal_ranks),
        majority_correct_count=sum(item.truth == majority_label for item in selected),
        accepted_count=len(accepted),
        accepted_candidate_correct_count=sum(
            item.candidate == item.truth for item in accepted
        ),
        abstention_counts=reasons,
        hit_counts_by_k=tuple(hit_counts),
        relevant_retrieved_counts_by_k=tuple(relevant_retrieved_counts),
        retrieved_counts_by_k=tuple(retrieved_counts),
        first_hit_counts_by_position=tuple(first_hit_counts),
        relevant_available_count=sum(
            relevant_counts.get(item.truth, 0) for item in selected
        ),
        absent_relevance_row_count=sum(
            item.truth not in relevant_counts for item in selected
        ),
    )


def _operational_objective_metrics(
    outcomes: tuple[_RowEvaluation, ...],
) -> OperationalObjectiveMetrics:
    candidate_counts = [0, 0, 0, 0]
    selective_counts = [0, 0, 0, 0]
    for item in outcomes:
        truth_is_operational = resolve_operating_state(item.truth) is not None
        candidate_is_operational = resolve_operating_state(item.candidate) is not None
        if truth_is_operational and candidate_is_operational:
            position = 0
        elif truth_is_operational:
            position = 1
        elif candidate_is_operational:
            position = 2
        else:
            position = 3
        candidate_counts[position] += 1
        if item.abstention_reason is None:
            selective_counts[position] += 1
    reasons = tuple(
        (
            reason.value,
            sum(item.abstention_reason is reason for item in outcomes),
        )
        for reason in ModelAbstentionReason
    )
    return OperationalObjectiveMetrics(
        candidate_operational_as_operational_count=candidate_counts[0],
        candidate_operational_as_problem_count=candidate_counts[1],
        candidate_problem_as_operational_count=candidate_counts[2],
        candidate_problem_as_problem_count=candidate_counts[3],
        selective_operational_as_operational_count=selective_counts[0],
        selective_operational_as_problem_count=selective_counts[1],
        selective_problem_as_operational_count=selective_counts[2],
        selective_problem_as_problem_count=selective_counts[3],
        abstention_counts=reasons,
    )


def _benchmark_latency(
    holdout: pd.DataFrame,
    *,
    model: InMemoryKnnModel,
    clock_ns: Callable[[], int],
) -> tuple[float, ...]:
    sample_indices = _evenly_spaced_indices(
        len(holdout),
        MODEL_EVALUATION_LATENCY_SAMPLE_COUNT,
    )
    if not sample_indices:
        raise ModelEvaluationError("Latency benchmark has no holdout samples.")
    for warmup in range(MODEL_EVALUATION_LATENCY_WARMUP_COUNT):
        model.predict_candidate(
            _row_features(holdout, sample_indices[warmup % len(sample_indices)]),
            top_k=MODEL_EVALUATION_TOP_K,
        )
    observations: list[float] = []
    for row_index in sample_indices:
        features = _row_features(holdout, row_index)
        started_ns = clock_ns()
        model.predict_candidate(features, top_k=MODEL_EVALUATION_TOP_K)
        finished_ns = clock_ns()
        observations.append(max(0.0, (finished_ns - started_ns) / 1_000_000))
    return tuple(observations)


def _transform_holdout(
    holdout: pd.DataFrame,
    model: InMemoryKnnModel,
) -> FloatMatrix:
    try:
        raw = np.asarray(
            holdout.loc[:, list(ANALYSIS_FEATURE_NAMES)].to_numpy(copy=True),
            dtype=np.float64,
            order="C",
        )
    except (TypeError, ValueError):
        raise ModelEvaluationError("Holdout features are invalid.") from None
    state = model.preprocessor_state
    mean = np.asarray(state.mean, dtype=np.float64)
    scale = np.asarray(state.scale, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        transformed = np.asarray((raw - mean) / scale, dtype=np.float64, order="C")
    if (
        transformed.shape != (len(holdout), ANALYSIS_FEATURE_COUNT)
        or not np.isfinite(transformed).all()
    ):
        raise ModelEvaluationError("Holdout preprocessing failed.")
    return transformed


def _load_holdout_once(path: Path, *, expected: _PartitionBinding) -> pd.DataFrame:
    raw = _read_bounded_regular_file(path, _HOLDOUT_MAXIMUM_BYTES)
    if sha256(raw).hexdigest() != expected.physical_sha256:
        raise ModelEvaluationError("Holdout identity does not match the frozen plan.")
    try:
        frame = pd.read_parquet(BytesIO(raw))
    except (OSError, TypeError, ValueError):
        raise ModelEvaluationError("Holdout Parquet is invalid.") from None
    expected_columns = (*ANALYSIS_FEATURE_NAMES, "y")
    if (
        tuple(frame.columns) != expected_columns
        or len(frame) != expected.row_count
        or len(frame.columns) != expected.column_count
        or frame.empty
        or any(
            str(frame[name].dtype).lower() != "float64"
            for name in ANALYSIS_FEATURE_NAMES
        )
        or str(frame["y"].dtype).lower() != "string"
        or frame["y"].isna().any()
        or any(not isinstance(value, str) or not value for value in frame["y"])
    ):
        raise ModelEvaluationError("Holdout contract is incompatible.")
    return frame


def _load_dataset_binding(path: Path) -> _DatasetBinding:
    raw = _read_bounded_regular_file(path, _MANIFEST_MAXIMUM_BYTES)
    manifest = _decode_json(raw)
    dataset_id = _required_sha256(manifest.get("dataset_id"), "dataset_id")
    components = _required_mapping(manifest.get("components"), "components")
    schema_id = _required_sha256(
        components.get("dataset_schema_id"),
        "dataset schema",
    )
    gates = _required_mapping(manifest.get("gates"), "gates")
    if not gates or any(
        type(value) is not bool or not value for value in gates.values()
    ):
        raise ModelEvaluationError("Canonical dataset gates are not all satisfied.")
    artifacts = _required_sequence(manifest.get("artifacts"), "artifacts")
    partitions = {
        name: _partition_binding(artifacts, f"{name}.parquet")
        for name in ("train", "validation", "test")
    }
    partition_summaries = _required_sequence(
        manifest.get("partitions"),
        "partitions",
    )
    summary_counts: dict[str, int] = {}
    for value in partition_summaries:
        item = _required_mapping(value, "partition")
        name = _required_text(item.get("name"), "partition name")
        count = _required_integer(item.get("row_count"), "partition row_count")
        if name in summary_counts:
            raise ModelEvaluationError("Canonical partition registry is invalid.")
        summary_counts[name] = count
    if any(
        summary_counts.get(name) != binding.row_count
        for name, binding in partitions.items()
    ):
        raise ModelEvaluationError("Canonical partition counts are inconsistent.")
    return _DatasetBinding(
        dataset_id=dataset_id,
        schema_id=schema_id,
        manifest_sha256=sha256(raw).hexdigest(),
        train=partitions["train"],
        validation=partitions["validation"],
        test=partitions["test"],
    )


def _partition_binding(
    artifacts: Sequence[object],
    filename: str,
) -> _PartitionBinding:
    matches: list[Mapping[str, object]] = []
    for value in artifacts:
        item = _required_mapping(value, "artifact")
        if item.get("filename") == filename:
            matches.append(item)
    if len(matches) != 1:
        raise ModelEvaluationError("Canonical partition artifact is unavailable.")
    item = matches[0]
    return _PartitionBinding(
        filename=filename,
        row_count=_required_integer(item.get("row_count"), "artifact row_count"),
        column_count=_required_integer(
            item.get("column_count"),
            "artifact column_count",
        ),
        physical_sha256=_required_sha256(
            item.get("physical_sha256"),
            "artifact physical identity",
        ),
    )


def _plan_identity_payload(
    *,
    dataset: _DatasetBinding,
    model: InMemoryKnnModel,
    index: LoadedSimilarityIndex,
    policy: KnnAbstentionPolicy,
) -> dict[str, object]:
    return _plan_identity_fields(
        dataset_id=dataset.dataset_id,
        dataset_schema_id=dataset.schema_id,
        dataset_manifest_sha256=dataset.manifest_sha256,
        model_id=model.model_id,
        model_content_sha256=model.content_sha256,
        index_id=index.selector.index_id,
        index_content_sha256=index.manifest.content_sha256,
        holdout_partition_sha256=dataset.test.physical_sha256,
        holdout_row_count=dataset.test.row_count,
        calibration_partition=policy.calibration_partition,
        calibration_partition_sha256=policy.calibration_partition_sha256,
        policy=policy,
    )


def _plan_identity_payload_from_plan(
    plan: FrozenEvaluationPlan,
) -> dict[str, object]:
    return _plan_identity_fields(
        dataset_id=plan.dataset_id,
        dataset_schema_id=plan.dataset_schema_id,
        dataset_manifest_sha256=plan.dataset_manifest_sha256,
        model_id=plan.model_id,
        model_content_sha256=plan.model_content_sha256,
        index_id=plan.index_id,
        index_content_sha256=plan.index_content_sha256,
        holdout_partition_sha256=plan.holdout_partition_sha256,
        holdout_row_count=plan.holdout_row_count,
        calibration_partition=plan.calibration_partition,
        calibration_partition_sha256=plan.calibration_partition_sha256,
        policy=plan.policy,
    )


def _plan_identity_fields(
    *,
    dataset_id: str,
    dataset_schema_id: str,
    dataset_manifest_sha256: str,
    model_id: str,
    model_content_sha256: str,
    index_id: str,
    index_content_sha256: str,
    holdout_partition_sha256: str,
    holdout_row_count: int,
    calibration_partition: str,
    calibration_partition_sha256: str,
    policy: KnnAbstentionPolicy,
) -> dict[str, object]:
    return {
        "schema_version": MODEL_EVALUATION_SCHEMA_VERSION,
        "protocol_version": MODEL_EVALUATION_PROTOCOL_VERSION,
        "dataset_id": dataset_id,
        "dataset_schema_id": dataset_schema_id,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "model_id": model_id,
        "model_content_sha256": model_content_sha256,
        "index_id": index_id,
        "index_content_sha256": index_content_sha256,
        "holdout_partition": MODEL_EVALUATION_HOLDOUT_PARTITION,
        "holdout_partition_sha256": holdout_partition_sha256,
        "holdout_row_count": holdout_row_count,
        "evaluated_engine": _EVALUATED_ENGINE,
        "operational_index_role": _INDEX_EVALUATION_ROLE,
        "metric": KNN_METRIC,
        "top_k": MODEL_EVALUATION_TOP_K,
        "working_memory_mib": MODEL_EVALUATION_WORKING_MEMORY_MIB,
        "parity_sample_count": MODEL_EVALUATION_PARITY_SAMPLE_COUNT,
        "latency_warmup_count": MODEL_EVALUATION_LATENCY_WARMUP_COUNT,
        "latency_sample_count": MODEL_EVALUATION_LATENCY_SAMPLE_COUNT,
        "metric_names": list(_METRIC_NAMES),
        "baseline_definitions": _baseline_definitions_payload(),
        "operating_state_policy": operating_state_policy_payload(),
        "calibration_partition": calibration_partition,
        "calibration_partition_sha256": calibration_partition_sha256,
        "policy": {
            "version": policy.version,
            "distance_threshold": policy.distance_threshold,
            "vote_margin_threshold": policy.vote_margin_threshold,
            "minimum_class_count": policy.minimum_class_count,
            "distance_quantile": policy.distance_quantile,
            "vote_margin_quantile": policy.vote_margin_quantile,
            "calibration_sample_count": policy.calibration_sample_count,
        },
    }


def _baseline_definitions_payload() -> dict[str, object]:
    return {
        "always_problem": {
            "metric_type": _ALWAYS_PROBLEM_BASELINE_TYPE,
            "strategy": _ALWAYS_PROBLEM_BASELINE_STRATEGY,
            "formula": _ALWAYS_PROBLEM_BASELINE_FORMULA,
            "scopes": ["candidate_all_rows", "selective_accepted"],
        }
    }


def _metric_definitions() -> dict[str, str]:
    return {
        "candidate_operational_objective_accuracy": (
            "Candidato k-NN e verdade concordam no recorte binário estado "
            "operacional versus problema antes da abstenção; não representa "
            "a condição emitida pelo sistema."
        ),
        "candidate_always_problem_baseline_accuracy": (
            "Acurácia da candidata constante problema em todas as linhas: "
            "quantidade real de problemas dividida pelo total do holdout."
        ),
        "candidate_operational_objective_balanced_accuracy": (
            "Média não ponderada dos recalls do candidato pré-abstenção."
        ),
        "candidate_operational_recall": (
            "Estados operacionais candidatos divididos pelos estados "
            "operacionais verdadeiros antes da abstenção."
        ),
        "candidate_problem_recall": (
            "Problemas candidatos divididos pelos problemas verdadeiros antes "
            "da abstenção."
        ),
        "selective_operational_objective_accuracy": (
            "Candidato e verdade concordam entre as linhas aceitas; denominador: "
            "linhas sem abstenção."
        ),
        "selective_always_problem_baseline_accuracy": (
            "Acurácia da candidata constante problema entre aceitas: quantidade "
            "real de problemas aceita dividida pelo total de linhas aceitas."
        ),
        "selective_operational_objective_balanced_accuracy": (
            "Média não ponderada dos recalls operacional e de problema somente "
            "entre as linhas aceitas."
        ),
        "selective_operational_recall": (
            "Estados operacionais aceitos como operacionais divididos pelos "
            "estados operacionais verdadeiros aceitos."
        ),
        "selective_problem_recall": (
            "Problemas aceitos como problemas divididos pelos problemas "
            "verdadeiros aceitos."
        ),
        "candidate_top1_accuracy": (
            "Diagnóstico secundário exato: candidata escolhida por votos, soma "
            "de distâncias e slug igual ao target; denominador: linhas do escopo."
        ),
        "neighbor_hit_at_1": (
            "Target do primeiro vizinho igual ao target consultado; denominador: "
            "linhas do escopo."
        ),
        "neighbor_hit_at_k": (
            "Ao menos um dos K vizinhos possui o target consultado; para uma "
            "relevância categórica por consulta, equivale ao Recall@K binário."
        ),
        "neighbor_precision_at_k": (
            "Vizinhos com target exato relevante divididos pelas linhas de treino "
            "recuperadas até K."
        ),
        "neighbor_recall_at_k": (
            "Linhas de treino relevantes recuperadas até K divididas por todas as "
            "linhas de treino relevantes disponíveis para cada consulta."
        ),
        "neighbor_incremental_hit_at_positions_2_to_k": (
            "Primeiros acertos que surgem exatamente em cada posição de 2 até K."
        ),
        "neighbor_mrr_at_k": (
            "Média de 1/r para o primeiro vizinho relevante até K; zero quando "
            "não há target relevante no ranking."
        ),
        "majority_class_accuracy": (
            "Predição constante da classe majoritária do treino, com empate por "
            "slug; o slug não é publicado."
        ),
        "coverage": "Linhas sem abstenção divididas pelas linhas do escopo.",
        "abstention_rate": "Linhas abstidas divididas pelas linhas do escopo.",
        "selective_candidate_accuracy": (
            "Candidatas corretas entre linhas aceitas; denominador: aceitas."
        ),
        "open_set_known_coverage": (
            "Classes conhecidas aceitas divididas pelas classes conhecidas, com "
            "intervalo de Wilson de 95%."
        ),
        "open_set_known_selective_accuracy": (
            "Candidatas exatas corretas entre classes conhecidas aceitas, com "
            "intervalo de Wilson de 95%."
        ),
        "open_set_unknown_false_acceptance_rate": (
            "Classes ausentes do treino aceitas pelo modelo divididas pelas classes "
            "ausentes do treino, com intervalo de Wilson de 95%."
        ),
        "open_set_unknown_rejection_rate": (
            "Classes ausentes do treino rejeitadas divididas pelas classes ausentes "
            "do treino, com intervalo de Wilson de 95%."
        ),
    }


def _operational_candidate_scope_payload(
    *,
    prefix: str,
    baseline_metric_name: str,
    operational_as_operational: int,
    operational_as_problem: int,
    problem_as_operational: int,
    problem_as_problem: int,
) -> dict[str, object]:
    operational_count = operational_as_operational + operational_as_problem
    problem_count = problem_as_operational + problem_as_problem
    row_count = operational_count + problem_count
    operational_recall = _rate_payload(
        operational_as_operational,
        operational_count,
    )
    problem_recall = _rate_payload(problem_as_problem, problem_count)
    operational_value = cast(float | None, operational_recall["value"])
    problem_value = cast(float | None, problem_recall["value"])
    balanced_accuracy = (
        None
        if operational_value is None or problem_value is None
        else round((operational_value + problem_value) / 2.0, 12)
    )
    return {
        "row_count": row_count,
        "counts": {
            "actual_operational": operational_count,
            "actual_problem": problem_count,
            f"{prefix}_operational": (
                operational_as_operational + problem_as_operational
            ),
            f"{prefix}_problem": operational_as_problem + problem_as_problem,
            "operational_as_operational": operational_as_operational,
            "operational_as_problem": operational_as_problem,
            "problem_as_operational": problem_as_operational,
            "problem_as_problem": problem_as_problem,
        },
        f"{prefix}_accuracy": _rate_payload(
            operational_as_operational + problem_as_problem,
            row_count,
        ),
        baseline_metric_name: AlwaysProblemBaselineAccuracy(
            actual_problem_count=problem_count,
            scope_row_count=row_count,
        ).to_payload(),
        f"{prefix}_balanced_accuracy": {"value": balanced_accuracy},
        f"{prefix}_operational_recall": operational_recall,
        f"{prefix}_problem_recall": problem_recall,
    }


def _neighbor_ranking_payload(metrics: ScopeMetrics) -> dict[str, object]:
    by_k: list[dict[str, object]] = []
    for position, (hits, relevant, retrieved, first_hits) in enumerate(
        zip(
            metrics.hit_counts_by_k,
            metrics.relevant_retrieved_counts_by_k,
            metrics.retrieved_counts_by_k,
            metrics.first_hit_counts_by_position,
            strict=True,
        ),
        start=1,
    ):
        by_k.append(
            {
                "k": position,
                "hit": _rate_payload(hits, metrics.row_count),
                "precision": _rate_payload(relevant, retrieved),
                "recall": _rate_payload(
                    relevant,
                    metrics.relevant_available_count,
                ),
                "first_hit_at_position": _rate_payload(
                    first_hits,
                    metrics.row_count,
                ),
            }
        )
    return {
        "unit": "training_rows",
        "relevance": "exact_target_slug_match",
        "relevant_available_count": metrics.relevant_available_count,
        "absent_relevance_row_count": metrics.absent_relevance_row_count,
        "by_k": by_k,
        "incremental_first_hits_at_positions_2_to_k": [
            {
                "position": position,
                "first_hit": _rate_payload(
                    metrics.first_hit_counts_by_position[position - 1],
                    metrics.row_count,
                ),
            }
            for position in range(2, MODEL_EVALUATION_TOP_K + 1)
        ],
        "mrr_at_k": {
            "reciprocal_rank_sum": round(metrics.reciprocal_rank_sum, 12),
            "denominator": metrics.row_count,
            "value": (
                None
                if metrics.row_count == 0
                else _ratio(metrics.reciprocal_rank_sum, metrics.row_count)
            ),
        },
    }


def _open_set_payload(
    known: ScopeMetrics,
    unknown: ScopeMetrics,
) -> dict[str, object]:
    unknown_rejected = unknown.row_count - unknown.accepted_count
    return {
        "definition": "exact_target_slug_presence_in_training_partition",
        "calibration_role": "train_and_validation_only",
        "holdout_role": "post_freeze_measurement_only",
        "confidence_interval": "wilson_score_95",
        "known_train_classes": {
            "row_count": known.row_count,
            "coverage": _rate_payload_with_wilson(
                known.accepted_count,
                known.row_count,
            ),
            "selective_candidate_accuracy": _rate_payload_with_wilson(
                known.accepted_candidate_correct_count,
                known.accepted_count,
            ),
        },
        "unknown_train_classes": {
            "row_count": unknown.row_count,
            "false_acceptance_rate": _rate_payload_with_wilson(
                unknown.accepted_count,
                unknown.row_count,
            ),
            "rejection_rate": _rate_payload_with_wilson(
                unknown_rejected,
                unknown.row_count,
            ),
        },
    }


def _rate_payload_with_wilson(numerator: int, denominator: int) -> dict[str, object]:
    return {
        **_rate_payload(numerator, denominator),
        "wilson_95": _wilson_interval(numerator, denominator),
    }


def _wilson_interval(numerator: int, denominator: int) -> dict[str, float | None]:
    if denominator == 0:
        return {"lower": None, "upper": None}
    if denominator < 0 or numerator < 0 or numerator > denominator:
        raise ModelEvaluationError("Wilson interval counts are invalid.")
    z_score = 1.959963984540054
    z_squared = z_score * z_score
    proportion = numerator / denominator
    scale = 1.0 + z_squared / denominator
    center = (proportion + z_squared / (2.0 * denominator)) / scale
    margin = (
        z_score
        * (
            proportion * (1.0 - proportion) / denominator
            + z_squared / (4.0 * denominator * denominator)
        )
        ** 0.5
        / scale
    )
    return {
        "lower": round(max(0.0, center - margin), 12),
        "upper": round(min(1.0, center + margin), 12),
    }


def _rate_payload(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else _ratio(numerator, denominator),
    }


def _ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        raise ModelEvaluationError("Metric denominator is invalid.")
    return round(numerator / denominator, 12)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values or not 0.0 <= quantile <= 1.0:
        raise ModelEvaluationError("Latency percentile input is invalid.")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _evenly_spaced_indices(row_count: int, limit: int) -> tuple[int, ...]:
    sample_count = min(row_count, limit)
    if sample_count <= 0:
        return ()
    if sample_count == 1:
        return (0,)
    return tuple(
        index * (row_count - 1) // (sample_count - 1) for index in range(sample_count)
    )


def _row_features(frame: pd.DataFrame, row_index: int) -> dict[str, float]:
    return {name: float(frame.iloc[row_index][name]) for name in ANALYSIS_FEATURE_NAMES}


def _hardware_profile() -> Mapping[str, object]:
    processor = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor()
    processor = " ".join(processor.split())[:160] if processor else "unavailable"
    return {
        "operating_system": platform.system() or "unavailable",
        "release": platform.release() or "unavailable",
        "machine": platform.machine() or "unavailable",
        "processor": processor,
        "logical_cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn_version,
    }


def _verify_materialized_plan(context: FrozenEvaluationContext) -> None:
    if (
        _SHA256_PATTERN.fullmatch(context.materialized_plan_sha256) is None
        or sha256(context.materialized_plan).hexdigest()
        != context.materialized_plan_sha256
        or context.materialized_plan != _canonical_json_bytes(context.plan.to_payload())
    ):
        raise ModelEvaluationError("Frozen evaluation plan materialization is invalid.")
    expected_plan_id = (
        f"{_PLAN_ID_PREFIX}"
        f"{sha256(_canonical_json_bytes(_plan_identity_payload_from_plan(context.plan))).hexdigest()[:32]}"
    )
    if context.plan.plan_id != expected_plan_id:
        raise ModelEvaluationError("Frozen evaluation plan identity is invalid.")


def _process_peak_memory(
    *,
    platform_name: str = sys.platform,
    windows_reader: Callable[[], int | None] | None = None,
    unix_reader: Callable[[], float | int | None] | None = None,
) -> _ProcessPeakMemory:
    if platform_name == "win32":
        value = _safe_memory_read(windows_reader or _read_windows_peak_working_set)
        return _ProcessPeakMemory(
            value_bytes=_valid_nonnegative_integer(value),
            source="windows_peak_working_set_lifetime",
        )
    if platform_name == "darwin" or platform_name.startswith(
        ("linux", "freebsd", "openbsd", "netbsd", "aix", "sunos")
    ):
        raw = _safe_memory_read(unix_reader or _read_unix_peak_rss)
        return _ProcessPeakMemory(
            value_bytes=_unix_peak_rss_bytes(raw, platform_name=platform_name),
            source="unix_ru_maxrss_lifetime",
        )
    return _ProcessPeakMemory(value_bytes=None, source="unavailable")


def _safe_memory_read(
    reader: Callable[[], float | int | None],
) -> float | int | None:
    try:
        return reader()
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def _valid_nonnegative_integer(value: float | int | None) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _unix_peak_rss_bytes(
    value: float | int | None,
    *,
    platform_name: str,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    numeric = float(value)
    if not np.isfinite(numeric) or numeric < 0.0:
        return None
    multiplier = 1 if platform_name == "darwin" else 1024
    converted = numeric * multiplier
    if converted > sys.maxsize:
        return None
    return int(converted)


def _read_windows_peak_working_set() -> int | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    get_current_process = cast(
        _WindowsGetCurrentProcess,
        kernel32.GetCurrentProcess,
    )
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info = cast(
        _WindowsGetProcessMemoryInfo,
        psapi.GetProcessMemoryInfo,
    )
    get_process_memory_info.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WindowsProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    get_process_memory_info.restype = ctypes.c_int
    counters = _WindowsProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = get_current_process()
    if not handle or not get_process_memory_info(
        handle,
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return int(counters.PeakWorkingSetSize)


def _read_unix_peak_rss() -> float | int | None:
    resource = importlib.import_module("resource")
    resource_values = vars(resource)
    getrusage = cast(Callable[[int], object], resource_values.get("getrusage"))
    self_selector = cast(int, resource_values.get("RUSAGE_SELF"))
    value = getattr(getrusage(self_selector), "ru_maxrss", None)
    return (
        value
        if isinstance(value, (float, int)) and not isinstance(value, bool)
        else None
    )


def _process_peak_payload(
    before: _ProcessPeakMemory,
    after: _ProcessPeakMemory,
) -> dict[str, object]:
    available = (
        before.source == after.source
        and before.source != "unavailable"
        and before.value_bytes is not None
        and after.value_bytes is not None
    )
    if not available:
        return {
            "available": False,
            "measure": after.source,
            "peak_bytes": None,
            "peak_mib": None,
            "observed_increase_bytes": None,
            "limitation": (
                "O pico RSS/working set do processo não está disponível nesta "
                "plataforma; não foi substituído por tracemalloc."
            ),
        }
    before_bytes = cast(int, before.value_bytes)
    after_bytes = cast(int, after.value_bytes)
    return {
        "available": True,
        "measure": after.source,
        "peak_bytes": after_bytes,
        "peak_mib": round(after_bytes / (1024 * 1024), 6),
        "observed_before_bytes": before_bytes,
        "observed_increase_bytes": max(0, after_bytes - before_bytes),
        "scope": "process_lifetime_high_water_observed_after_evaluation",
        "limitation": (
            "É o maior RSS/working set da vida do processo, não um pico isolado "
            "instantâneo apenas da avaliação."
        ),
    }


def _read_bounded_regular_file(path: Path, maximum_bytes: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        raise ModelEvaluationError("Evaluation input is unavailable.") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise ModelEvaluationError("Evaluation input is not a bounded file.")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum_bytes
            or before.st_size != len(raw)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ModelEvaluationError("Evaluation input changed while being read.")
        return raw
    except OSError:
        raise ModelEvaluationError("Evaluation input could not be read.") from None
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _decode_json(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ModelEvaluationError("Dataset manifest JSON is invalid.") from None
    if not isinstance(value, dict):
        raise ModelEvaluationError("Dataset manifest JSON is invalid.")
    return cast(dict[str, object], value)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("invalid JSON constant")


def _required_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ModelEvaluationError(f"Dataset manifest {context} is invalid.")
    return cast(Mapping[str, object], value)


def _required_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ModelEvaluationError(f"Dataset manifest {context} is invalid.")
    return cast(Sequence[object], value)


def _required_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelEvaluationError(f"Dataset manifest {context} is invalid.")
    return value


def _required_integer(value: object, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise ModelEvaluationError(f"Dataset manifest {context} is invalid.")
    return value


def _required_sha256(value: object, context: str) -> str:
    text = _required_text(value, context)
    if _SHA256_PATTERN.fullmatch(text) is None:
        raise ModelEvaluationError(f"Dataset manifest {context} is invalid.")
    return text


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Avalia o k-NN temporal pelo protocolo congelado, sem opções de tuning."
        )
    )
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--index-artifact", type=Path, required=True)
    parser.add_argument(
        "--report-output",
        type=Path,
        help="Destino local novo para o relatório sanitizado; nunca sobrescreve.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_evaluation(
            dataset_manifest_path=cast(Path, args.dataset_manifest),
            holdout_path=cast(Path, args.holdout),
            model_artifact_directory=cast(Path, args.model_artifact),
            index_artifact_directory=cast(Path, args.index_artifact),
        )
        report_output = cast(Path | None, args.report_output)
        if report_output is not None:
            save_evaluation_report(report, report_output)
    except ModelEvaluationError as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 2
    if report_output is None:
        print(report.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
