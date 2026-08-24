"""Synthetic formula tests for the validation-only open-set gate."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from prescriptive_maintenance.contracts import ANALYSIS_FEATURE_NAMES
from prescriptive_maintenance.modeling.knn import KnnTrainingError, fit_knn_model
from prescriptive_maintenance.modeling.open_set import (
    OPEN_SET_MAX_UNKNOWN_FAR_WILSON_UPPER,
    OPEN_SET_MIN_KNOWN_COVERAGE_WILSON_LOWER,
    OpenSetGateError,
    OpenSetValidationReport,
    audit_validation_open_set,
    require_open_set_gate,
    select_open_set_policy,
)


def _frame(first_values: tuple[float, ...], targets: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            name: pd.Series(
                (
                    first_values
                    if position == 0
                    else (float(position),) * len(first_values)
                ),
                dtype="float64",
            )
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
    )
    frame["y"] = pd.Series(targets, dtype="string")
    return frame


def test_pre_registered_gate_selects_a_validation_policy_with_wilson_bounds() -> None:
    selection = select_open_set_policy(
        nearest_distances=(0.1,) * 80 + (10.0,) * 80,
        vote_margins=(1.0,) * 160,
        class_is_supported=(True,) * 160,
        known_mask=(True,) * 80 + (False,) * 80,
        candidate_is_correct=(True,) * 80 + (False,) * 80,
    )

    assert selection.passed is True
    assert selection.distance_threshold == 0.1
    assert selection.unknown_far.numerator == 0
    assert selection.unknown_far.upper is not None
    assert selection.unknown_far.upper <= OPEN_SET_MAX_UNKNOWN_FAR_WILSON_UPPER
    assert selection.known_coverage.numerator == 80
    assert selection.known_coverage.lower is not None
    assert selection.known_coverage.lower >= OPEN_SET_MIN_KNOWN_COVERAGE_WILSON_LOWER


def test_infeasible_gate_is_sanitized_and_blocks_promotion() -> None:
    selection = select_open_set_policy(
        nearest_distances=(0.1,) * 160,
        vote_margins=(1.0,) * 160,
        class_is_supported=(True,) * 160,
        known_mask=(True,) * 80 + (False,) * 80,
        candidate_is_correct=(True,) * 80 + (False,) * 80,
    )
    report = OpenSetValidationReport(
        dataset_id="a" * 64,
        model_id="model_synthetic_gate",
        validation_partition_sha256="b" * 64,
        selection=selection,
    )

    assert selection.passed is False
    assert selection.failure_reason == "no_policy_satisfied_both_gates"
    with pytest.raises(OpenSetGateError, match="did not pass") as captured:
        require_open_set_gate(report)
    payload = json.dumps(captured.value.report.to_payload())
    assert "validation_only" in payload
    assert "test_partition_usage" in payload
    assert "synthetic-a" not in payload
    assert "synthetic-unseen" not in payload


def test_open_set_formulas_reject_missing_class_scopes_and_bad_counts() -> None:
    with pytest.raises(ValueError, match="known and unknown"):
        select_open_set_policy(
            nearest_distances=(0.1, 0.2),
            vote_margins=(1.0, 1.0),
            class_is_supported=(True, True),
            known_mask=(True, True),
            candidate_is_correct=(True, False),
        )

    with pytest.raises(ValueError, match="statistics"):
        select_open_set_policy(
            nearest_distances=(0.1,),
            vote_margins=(1.0, 0.5),
            class_is_supported=(True,),
            known_mask=(False,),
            candidate_is_correct=(False,),
        )


def test_model_bound_audit_uses_validation_identity_and_excludes_private_labels() -> (
    None
):
    training = _frame(
        (0.0, 0.1, 0.2, 100.0),
        ("synthetic-a", "synthetic-a", "synthetic-a", "synthetic-b"),
    )
    validation = _frame(
        (0.0,) * 80 + (10_000.0,) * 80,
        ("synthetic-a",) * 80 + ("synthetic-unseen",) * 80,
    )
    model = fit_knn_model(
        training,
        dataset_id="a" * 64,
        training_partition_sha256="b" * 64,
        validation_frame=validation,
        validation_partition_sha256="c" * 64,
        default_top_k=3,
        minimum_class_count=1,
    )

    with pytest.raises(KnnTrainingError, match="incompatible"):
        audit_validation_open_set(
            model,
            validation,
            validation_partition_sha256="d" * 64,
        )

    report = audit_validation_open_set(
        model,
        validation,
        validation_partition_sha256="c" * 64,
    )

    assert report.passed is True
    assert report.dataset_id == model.dataset_id
    assert report.model_id == model.model_id
    assert report.validation_partition_sha256 == "c" * 64
    require_open_set_gate(report)
    serialized = json.dumps(report.to_payload())
    assert "synthetic-a" not in serialized
    assert "synthetic-unseen" not in serialized
