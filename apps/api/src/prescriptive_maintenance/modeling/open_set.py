"""Validation-only open-set gate for model promotion decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from prescriptive_maintenance.contracts import ANALYSIS_FEATURE_NAMES
from prescriptive_maintenance.modeling.knn import (
    InMemoryKnnModel,
    KnnError,
    KnnTrainingError,
)

OPEN_SET_GATE_VERSION: Final = 1
OPEN_SET_MAX_UNKNOWN_FAR_WILSON_UPPER: Final = 0.05
OPEN_SET_MIN_KNOWN_COVERAGE_WILSON_LOWER: Final = 0.50

_WILSON_Z_95: Final = 1.959963984540054

type FloatVector = NDArray[np.float64]
type BoolVector = NDArray[np.bool_]
type IntVector = NDArray[np.int64]


class OpenSetGateError(KnnError):
    """Fail a promotion whose validation evidence misses the frozen gate."""

    def __init__(self, report: OpenSetValidationReport) -> None:
        super().__init__("Validation open-set gate did not pass.")
        self.report = report


@dataclass(frozen=True, slots=True)
class WilsonRate:
    """One binomial rate with explicit counts and a Wilson 95% interval."""

    numerator: int
    denominator: int
    lower: float | None
    upper: float | None

    def to_payload(self) -> dict[str, object]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": (
                None
                if self.denominator == 0
                else round(self.numerator / self.denominator, 12)
            ),
            "wilson_95": {
                "confidence_level": 0.95,
                "lower": self.lower,
                "upper": self.upper,
            },
        }


@dataclass(frozen=True, slots=True)
class OpenSetPolicySelection:
    """Sanitized result of the pre-registered validation policy search."""

    passed: bool
    distance_threshold: float
    vote_margin_threshold: float
    unknown_far: WilsonRate
    known_coverage: WilsonRate
    known_selective_accuracy: WilsonRate
    evaluated_policy_count: int
    failure_reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "gate": {
                "version": OPEN_SET_GATE_VERSION,
                "maximum_unknown_far_wilson_upper": (
                    OPEN_SET_MAX_UNKNOWN_FAR_WILSON_UPPER
                ),
                "minimum_known_coverage_wilson_lower": (
                    OPEN_SET_MIN_KNOWN_COVERAGE_WILSON_LOWER
                ),
            },
            "selected_policy": {
                "distance_threshold": self.distance_threshold,
                "vote_margin_threshold": self.vote_margin_threshold,
                "unknown_far": self.unknown_far.to_payload(),
                "known_coverage": self.known_coverage.to_payload(),
                "known_selective_accuracy": (
                    self.known_selective_accuracy.to_payload()
                ),
            },
            "search": {
                "strategy": (
                    "maximize_known_coverage_then_minimize_far_upper_then_"
                    "conservative_thresholds"
                ),
                "evaluated_policy_count": self.evaluated_policy_count,
                "failure_reason": self.failure_reason,
            },
        }


@dataclass(frozen=True, slots=True)
class OpenSetValidationReport:
    """Model-bound validation evidence that never contains rows or labels."""

    dataset_id: str
    model_id: str
    validation_partition_sha256: str
    selection: OpenSetPolicySelection

    @property
    def passed(self) -> bool:
        return self.selection.passed

    def to_payload(self) -> dict[str, object]:
        return {
            "report_schema_version": 1,
            "partition_role": "validation_only",
            "test_partition_usage": "prohibited",
            "dataset_id": self.dataset_id,
            "model_id": self.model_id,
            "validation_partition_sha256": self.validation_partition_sha256,
            "validation_open_set": self.selection.to_payload(),
        }


def audit_validation_open_set(
    model: InMemoryKnnModel,
    validation_frame: object,
    *,
    validation_partition_sha256: str,
) -> OpenSetValidationReport:
    """Search the frozen gate using validation labels and model statistics only."""

    expected_columns = (*ANALYSIS_FEATURE_NAMES, "y")
    if (
        type(model) is not InMemoryKnnModel
        or not isinstance(validation_frame, pd.DataFrame)
        or tuple(validation_frame.columns) != expected_columns
        or validation_frame.empty
        or any(
            str(validation_frame[name].dtype).lower() != "float64"
            for name in ANALYSIS_FEATURE_NAMES
        )
        or str(validation_frame["y"].dtype).lower() != "string"
        or model.abstention_policy.calibration_partition != "validation"
        or validation_partition_sha256
        != model.abstention_policy.calibration_partition_sha256
    ):
        raise KnnTrainingError("Validation open-set input is incompatible.")

    snapshot = model.evaluation_snapshot()
    class_counts: Mapping[str, int] = {
        label.target_slug: snapshot.class_counts[position]
        for position, label in enumerate(snapshot.labels)
    }
    nearest_distances: list[float] = []
    vote_margins: list[float] = []
    supported: list[bool] = []
    known: list[bool] = []
    correct: list[bool] = []
    for values in validation_frame.itertuples(index=False, name=None):
        truth = values[-1]
        if not isinstance(truth, str) or not truth:
            raise KnnTrainingError("Validation open-set input is incompatible.")
        features = {
            name: float(value)
            for name, value in zip(
                ANALYSIS_FEATURE_NAMES,
                values[:-1],
                strict=True,
            )
        }
        candidate = model.predict_candidate(features)
        nearest_distances.append(candidate.nearest_distance)
        vote_margins.append(candidate.vote_margin)
        supported.append(
            class_counts[candidate.target_slug]
            >= model.abstention_policy.minimum_class_count
        )
        known.append(truth in class_counts)
        correct.append(candidate.target_slug == truth)

    selection = select_open_set_policy(
        nearest_distances=nearest_distances,
        vote_margins=vote_margins,
        class_is_supported=supported,
        known_mask=known,
        candidate_is_correct=correct,
    )
    return OpenSetValidationReport(
        dataset_id=model.dataset_id,
        model_id=model.model_id,
        validation_partition_sha256=validation_partition_sha256,
        selection=selection,
    )


def require_open_set_gate(report: OpenSetValidationReport) -> None:
    """Block promotion unless both pre-registered Wilson gates pass."""

    if type(report) is not OpenSetValidationReport or not report.passed:
        if type(report) is OpenSetValidationReport:
            raise OpenSetGateError(report)
        raise TypeError("Open-set validation report is incompatible.")


def select_open_set_policy(
    *,
    nearest_distances: Sequence[float],
    vote_margins: Sequence[float],
    class_is_supported: Sequence[bool],
    known_mask: Sequence[bool],
    candidate_is_correct: Sequence[bool],
) -> OpenSetPolicySelection:
    """Evaluate every deterministic threshold pair without consulting a test."""

    distances = _float_vector(nearest_distances, "distance")
    margins = _float_vector(vote_margins, "vote margin")
    supported = _bool_vector(class_is_supported)
    known = _bool_vector(known_mask)
    correct = _bool_vector(candidate_is_correct)
    row_count = len(distances)
    if (
        row_count == 0
        or margins.shape != (row_count,)
        or supported.shape != (row_count,)
        or known.shape != (row_count,)
        or correct.shape != (row_count,)
        or np.any(distances < 0.0)
        or np.any(margins < 0.0)
        or np.any(margins > 1.0)
    ):
        raise ValueError("Open-set calibration statistics are invalid.")
    known_count = int(np.count_nonzero(known))
    unknown_count = row_count - known_count
    if known_count == 0 or unknown_count == 0:
        raise ValueError("Validation must contain known and unknown classes.")

    candidates: list[OpenSetPolicySelection] = []
    margin_thresholds = tuple(sorted({0.0, 1.0, *(float(item) for item in margins)}))
    for margin_threshold in margin_thresholds:
        eligible = supported & (margins > margin_threshold)
        positions = np.flatnonzero(eligible)
        distance_thresholds = {0.0}
        distance_thresholds.update(float(distances[position]) for position in positions)
        ordered = positions[np.lexsort((positions, distances[positions]))]
        ordered_distances = distances[ordered]
        cumulative_known = np.cumsum(known[ordered].astype(np.int64))
        cumulative_unknown = np.cumsum((~known[ordered]).astype(np.int64))
        cumulative_correct = np.cumsum(
            (known[ordered] & correct[ordered]).astype(np.int64)
        )
        for distance_threshold in sorted(distance_thresholds):
            accepted_end = int(
                np.searchsorted(ordered_distances, distance_threshold, side="right")
            )
            known_accepted = _cumulative_value(cumulative_known, accepted_end)
            unknown_accepted = _cumulative_value(cumulative_unknown, accepted_end)
            known_correct = _cumulative_value(cumulative_correct, accepted_end)
            candidates.append(
                _selection(
                    distance_threshold=distance_threshold,
                    vote_margin_threshold=margin_threshold,
                    unknown_accepted=unknown_accepted,
                    unknown_count=unknown_count,
                    known_accepted=known_accepted,
                    known_count=known_count,
                    known_correct=known_correct,
                )
            )

    passing = tuple(candidate for candidate in candidates if candidate.passed)
    pool = passing or tuple(candidates)
    selected = min(pool, key=_selection_key)
    return replace(
        selected,
        passed=bool(passing),
        evaluated_policy_count=len(candidates),
        failure_reason=None if passing else "no_policy_satisfied_both_gates",
    )


def _selection(
    *,
    distance_threshold: float,
    vote_margin_threshold: float,
    unknown_accepted: int,
    unknown_count: int,
    known_accepted: int,
    known_count: int,
    known_correct: int,
) -> OpenSetPolicySelection:
    unknown_far = _wilson_rate(unknown_accepted, unknown_count)
    known_coverage = _wilson_rate(known_accepted, known_count)
    if unknown_far.upper is None or known_coverage.lower is None:
        raise ValueError("Open-set gate denominators are invalid.")
    return OpenSetPolicySelection(
        passed=(
            unknown_far.upper <= OPEN_SET_MAX_UNKNOWN_FAR_WILSON_UPPER
            and known_coverage.lower >= OPEN_SET_MIN_KNOWN_COVERAGE_WILSON_LOWER
        ),
        distance_threshold=distance_threshold,
        vote_margin_threshold=vote_margin_threshold,
        unknown_far=unknown_far,
        known_coverage=known_coverage,
        known_selective_accuracy=_wilson_rate(known_correct, known_accepted),
        evaluated_policy_count=0,
    )


def _selection_key(selection: OpenSetPolicySelection) -> tuple[float, ...]:
    far_upper = selection.unknown_far.upper
    if far_upper is None:
        raise ValueError("Open-set FAR interval is invalid.")
    return (
        -selection.known_coverage.numerator,
        far_upper,
        selection.distance_threshold,
        -selection.vote_margin_threshold,
    )


def _wilson_rate(numerator: int, denominator: int) -> WilsonRate:
    if denominator < 0 or not 0 <= numerator <= denominator:
        raise ValueError("Wilson interval counts are invalid.")
    if denominator == 0:
        return WilsonRate(0, 0, None, None)
    proportion = numerator / denominator
    z_squared = _WILSON_Z_95 * _WILSON_Z_95
    adjustment = 1.0 + z_squared / denominator
    center = (proportion + z_squared / (2.0 * denominator)) / adjustment
    radius = (
        _WILSON_Z_95
        * (
            proportion * (1.0 - proportion) / denominator
            + z_squared / (4.0 * denominator * denominator)
        )
        ** 0.5
        / adjustment
    )
    return WilsonRate(
        numerator=numerator,
        denominator=denominator,
        lower=max(0.0, center - radius),
        upper=min(1.0, center + radius),
    )


def _float_vector(values: Sequence[float], label: str) -> FloatVector:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"Open-set {label} values are invalid.")
    try:
        result = np.asarray(tuple(values), dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"Open-set {label} values are invalid.") from None
    if result.ndim != 1 or not all(isfinite(float(item)) for item in result):
        raise ValueError(f"Open-set {label} values are invalid.")
    return result


def _bool_vector(values: Sequence[bool]) -> BoolVector:
    if isinstance(values, (str, bytes)):
        raise ValueError("Open-set boolean values are invalid.")
    items = tuple(values)
    if any(type(item) is not bool for item in items):
        raise ValueError("Open-set boolean values are invalid.")
    return np.asarray(items, dtype=np.bool_)


def _cumulative_value(values: IntVector, end: int) -> int:
    return 0 if end == 0 else int(values[end - 1])
