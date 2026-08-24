"""Synthetic tests for the frozen temporal model-evaluation protocol."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
import prescriptive_maintenance.modeling.evaluation as evaluation_module
import pytest
from prescriptive_maintenance.contracts import ANALYSIS_FEATURE_NAMES
from prescriptive_maintenance.modeling.evaluation import (
    MODEL_EVALUATION_PROTOCOL_VERSION,
    MODEL_EVALUATION_SCHEMA_VERSION,
    MODEL_EVALUATION_TOP_K,
    EvaluationReport,
    FrozenEvaluationContext,
    ModelEvaluationError,
    OperationalObjectiveMetrics,
    evaluate_frozen_holdout,
    freeze_evaluation_plan,
    run_evaluation,
    save_evaluation_report,
)
from prescriptive_maintenance.modeling.knn import (
    InMemoryKnnModel,
    KnnInputError,
    fit_knn_model,
    save_knn_model,
)
from prescriptive_maintenance.modeling.similarity_index import (
    save_similarity_index_from_knn_artifact,
)
from prescriptive_maintenance.operating_states import operating_state_policy_payload

DATASET_ID = "a" * 64
SCHEMA_ID = "b" * 64


@dataclass(frozen=True, slots=True)
class _SyntheticArtifacts:
    manifest: Path
    holdout: Path
    model: Path
    index: Path
    fitted_model: InMemoryKnnModel


class _MemoryReading(Protocol):
    value_bytes: int | None
    source: str


class _SerializedReport:
    def to_json(self) -> str:
        return '{"synthetic":true}\n'


def _frame(values: tuple[float, ...], labels: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for value, label in zip(values, labels, strict=True):
        row: dict[str, object] = {
            name: float(value if position == 0 else position + 1)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
        row["y"] = label
        rows.append(row)
    frame = pd.DataFrame(rows, columns=(*ANALYSIS_FEATURE_NAMES, "y"))
    frame.loc[:, list(ANALYSIS_FEATURE_NAMES)] = frame.loc[
        :, list(ANALYSIS_FEATURE_NAMES)
    ].astype("float64")
    frame["y"] = frame["y"].astype("string")
    return frame


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _build_artifacts(
    tmp_path: Path,
    *,
    identical_training_vectors: bool = False,
) -> _SyntheticArtifacts:
    training_values = (
        (1.0,) * 6 if identical_training_vectors else (0.0, 0.2, 0.4, 10.0, 10.2, 10.4)
    )
    training = _frame(
        training_values,
        (
            "normal",
            "normal",
            "normal",
            "synthetic-problem",
            "synthetic-problem",
            "synthetic-problem",
        ),
    )
    validation = _frame(
        (0.1, 5.0, 10.1),
        (
            "synthetic-validation-a",
            "synthetic-validation-b",
            "synthetic-validation-c",
        ),
    )
    test = _frame(
        (0.1, 10.1, 30.0, 0.0),
        (
            "normal",
            "synthetic-problem",
            "synthetic-unseen-a",
            "synthetic-unseen-b",
        ),
    )
    data_directory = tmp_path / "dataset"
    data_directory.mkdir()
    train_path = data_directory / "train.parquet"
    validation_path = data_directory / "validation.parquet"
    test_path = data_directory / "test.parquet"
    training.to_parquet(train_path, index=False)
    validation.to_parquet(validation_path, index=False)
    test.to_parquet(test_path, index=False)

    train_hash = _file_sha256(train_path)
    validation_hash = _file_sha256(validation_path)
    fitted = fit_knn_model(
        training,
        dataset_id=DATASET_ID,
        training_partition_sha256=train_hash,
        validation_frame=validation,
        validation_partition_sha256=validation_hash,
        minimum_class_count=1,
    )
    model_directory = save_knn_model(fitted, tmp_path / "model")
    index_directory = save_similarity_index_from_knn_artifact(
        model_directory,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "index",
    )

    artifacts: list[dict[str, object]] = []
    partitions: list[dict[str, object]] = []
    for name, path, frame in (
        ("train", train_path, training),
        ("validation", validation_path, validation),
        ("test", test_path, test),
    ):
        artifacts.append(
            {
                "filename": f"{name}.parquet",
                "row_count": len(frame),
                "column_count": len(frame.columns),
                "physical_sha256": _file_sha256(path),
                "logical_sha256": sha256(f"synthetic-{name}".encode()).hexdigest(),
            }
        )
        partitions.append(
            {
                "name": name,
                "row_count": len(frame),
                "occurrence_count": 1,
                "target_ratio": "synthetic",
            }
        )
    manifest_path = data_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_schema_version": 1,
                "dataset_id": DATASET_ID,
                "components": {"dataset_schema_id": SCHEMA_ID},
                "partitions": partitions,
                "artifacts": artifacts,
                "gates": {"synthetic.test_blind": True},
            },
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return _SyntheticArtifacts(
        manifest=manifest_path,
        holdout=test_path,
        model=model_directory,
        index=index_directory,
        fitted_model=fitted,
    )


def _freeze(artifacts: _SyntheticArtifacts) -> FrozenEvaluationContext:
    return freeze_evaluation_plan(
        dataset_manifest_path=artifacts.manifest,
        model_artifact_directory=artifacts.model,
        index_artifact_directory=artifacts.index,
    )


def test_plan_freezes_without_opening_or_requiring_holdout_bytes(
    tmp_path: Path,
) -> None:
    artifacts = _build_artifacts(tmp_path)
    artifacts.holdout.unlink()

    context = _freeze(artifacts)

    assert context.plan.plan_id.startswith("evaluation_plan_v2_")
    assert context.plan.dataset_id == DATASET_ID
    assert context.plan.model_id == artifacts.fitted_model.model_id
    assert context.plan.calibration_partition == "validation"
    assert (
        context.plan.calibration_partition_sha256
        != context.plan.holdout_partition_sha256
    )
    assert context.plan.to_payload()["protocol_version"] == (
        MODEL_EVALUATION_PROTOCOL_VERSION
    )
    assert context.plan.to_payload()["schema_version"] == (
        MODEL_EVALUATION_SCHEMA_VERSION
    )
    assert sha256(context.materialized_plan).hexdigest() == (
        context.materialized_plan_sha256
    )


def test_real_method_options_are_frozen_and_not_cli_tunable(tmp_path: Path) -> None:
    artifacts = _build_artifacts(tmp_path)
    context = _freeze(artifacts)
    method = cast(dict[str, object], context.plan.to_payload()["method"])

    assert method == {
        "evaluated_engine": "in_memory_knn_exact",
        "operational_index_role": "identity_and_compatibility_binding_only",
        "metric": "euclidean",
        "top_k": MODEL_EVALUATION_TOP_K,
        "search": "exact_full_distance_batched",
        "distance_order": "ascending",
        "distance_tie_break": "neighbor_ref_ascending",
        "candidate_selection": (
            "vote_count_descending_then_distance_sum_ascending_"
            "then_target_slug_ascending"
        ),
        "working_memory_mib": 64,
        "metric_scopes": {
            "primary": (
                "candidate_all_rows_and_selective_accepted_operational_vs_problem"
            ),
            "secondary_exact_label_diagnostics": [
                "all_rows",
                "known_train_classes",
            ],
        },
        "metrics": [
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
            "neighbor_mrr_at_k",
            "majority_class_accuracy",
            "selective_candidate_accuracy",
        ],
        "baseline_definitions": {
            "always_problem": {
                "metric_type": "constant_class_accuracy",
                "strategy": "always_problem",
                "formula": "actual_problem_count / scope_row_count",
                "scopes": ["candidate_all_rows", "selective_accepted"],
            }
        },
        "operating_state_policy": operating_state_policy_payload(),
    }


def test_operational_objective_separates_candidates_selective_and_abstained() -> None:
    payload = OperationalObjectiveMetrics(
        candidate_operational_as_operational_count=1,
        candidate_operational_as_problem_count=2,
        candidate_problem_as_operational_count=2,
        candidate_problem_as_problem_count=3,
        selective_operational_as_operational_count=0,
        selective_operational_as_problem_count=1,
        selective_problem_as_operational_count=1,
        selective_problem_as_problem_count=1,
        abstention_counts=(
            ("distance_out_of_distribution", 2),
            ("inconclusive_vote", 2),
            ("rare_class_support", 1),
        ),
    ).to_payload()

    candidate = cast(dict[str, object], payload["candidate_all_rows"])
    selective = cast(dict[str, object], payload["selective_accepted"])
    assert candidate["candidate_accuracy"] == {
        "numerator": 4,
        "denominator": 8,
        "value": 0.5,
    }
    assert candidate["candidate_always_problem_baseline_accuracy"] == {
        "metric_type": "constant_class_accuracy",
        "strategy": "always_problem",
        "formula": "actual_problem_count / scope_row_count",
        "numerator": 5,
        "denominator": 8,
        "value": 0.625,
    }
    assert candidate["candidate_operational_recall"] == {
        "numerator": 1,
        "denominator": 3,
        "value": 0.333333333333,
    }
    assert selective["selective_candidate_accuracy"] == {
        "numerator": 1,
        "denominator": 3,
        "value": 0.333333333333,
    }
    assert selective["selective_always_problem_baseline_accuracy"] == {
        "metric_type": "constant_class_accuracy",
        "strategy": "always_problem",
        "formula": "actual_problem_count / scope_row_count",
        "numerator": 2,
        "denominator": 3,
        "value": 0.666666666667,
    }
    candidate_accuracy = cast(dict[str, object], candidate["candidate_accuracy"])[
        "value"
    ]
    candidate_baseline = cast(
        dict[str, object],
        candidate["candidate_always_problem_baseline_accuracy"],
    )["value"]
    selective_accuracy = cast(
        dict[str, object], selective["selective_candidate_accuracy"]
    )["value"]
    selective_baseline = cast(
        dict[str, object],
        selective["selective_always_problem_baseline_accuracy"],
    )["value"]
    assert isinstance(candidate_accuracy, float)
    assert isinstance(candidate_baseline, float)
    assert isinstance(selective_accuracy, float)
    assert isinstance(selective_baseline, float)
    assert candidate_accuracy < candidate_baseline
    assert selective_accuracy < selective_baseline
    assert payload["coverage"] == {
        "numerator": 3,
        "denominator": 8,
        "value": 0.375,
    }
    assert payload["abstained"] == {
        "count": 5,
        "rate": {"numerator": 5, "denominator": 8, "value": 0.625},
        "reasons": {
            "distance_out_of_distribution": 2,
            "inconclusive_vote": 2,
            "rare_class_support": 1,
        },
    }
    assert "predicted_" not in json.dumps(payload)


def test_evaluation_reports_all_and_known_scopes_without_private_values(
    tmp_path: Path,
) -> None:
    artifacts = _build_artifacts(tmp_path)
    report = evaluate_frozen_holdout(_freeze(artifacts), holdout_path=artifacts.holdout)
    all_rows = report.all_rows.to_payload()
    known = report.known_train_classes.to_payload()

    assert report.all_rows.row_count == 4
    assert report.known_train_classes.row_count == 2
    assert report.unknown_class_row_count == 2
    assert all_rows["neighbor_hit_at_k"] == {
        "numerator": 2,
        "denominator": 4,
        "value": 0.5,
    }
    assert known["neighbor_hit_at_k"] == {
        "numerator": 2,
        "denominator": 2,
        "value": 1.0,
    }
    known_mrr = cast(dict[str, object], known["neighbor_mrr_at_k"])
    assert known_mrr["value"] == 1.0
    assert report.parity_audit_count == 4
    assert len(report.latency_milliseconds) == 4
    assert report.peak_traced_allocation_bytes >= 0
    execution = cast(dict[str, object], report.to_payload()["execution"])
    memory = cast(dict[str, object], execution["memory"])
    process_peak = cast(dict[str, object], memory["process_peak"])
    assert isinstance(process_peak["available"], bool)
    assert process_peak["measure"] in {
        "windows_peak_working_set_lifetime",
        "unix_ru_maxrss_lifetime",
        "unavailable",
    }
    serialized = report.to_json()
    assert "synthetic-problem" not in serialized
    assert "synthetic-unseen" not in serialized
    assert str(tmp_path) not in serialized
    report_path = tmp_path / "evaluation-report.json"
    save_evaluation_report(report, report_path)
    assert report_path.read_text("utf-8") == serialized
    with pytest.raises(ModelEvaluationError, match="destination"):
        save_evaluation_report(report, report_path)
    assert report_path.read_text("utf-8") == serialized


@pytest.mark.parametrize("operation", ("write", "zero_write", "fsync", "close"))
def test_report_failure_removes_only_the_file_created_by_the_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    output = tmp_path / f"failed-{operation}.json"
    report = cast(EvaluationReport, _SerializedReport())
    real_close = os.close

    def fail_write(_descriptor: int, _payload: bytes) -> int:
        raise OSError("synthetic write failure")

    def return_zero(_descriptor: int, _payload: bytes) -> int:
        return 0

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    def fail_close(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("synthetic close failure")

    replacements: dict[str, object] = {
        "write": fail_write,
        "zero_write": return_zero,
        "fsync": fail_fsync,
        "close": fail_close,
    }
    attribute = "write" if operation == "zero_write" else operation
    monkeypatch.setattr(os, attribute, replacements[operation])

    with pytest.raises(ModelEvaluationError, match="could not be written"):
        save_evaluation_report(report, output)

    assert not output.exists()


def test_boundary_ties_match_direct_model_ranking(tmp_path: Path) -> None:
    artifacts = _build_artifacts(tmp_path, identical_training_vectors=True)

    report = evaluate_frozen_holdout(_freeze(artifacts), holdout_path=artifacts.holdout)

    assert report.boundary_repair_row_count == len(report.latency_milliseconds)
    assert report.parity_audit_count == len(report.latency_milliseconds)


def test_holdout_bytes_are_bound_to_the_preexisting_plan(tmp_path: Path) -> None:
    artifacts = _build_artifacts(tmp_path)
    context = _freeze(artifacts)
    changed = _frame((1.0,), ("synthetic-changed",))
    changed.to_parquet(artifacts.holdout, index=False)

    with pytest.raises(ModelEvaluationError, match="identity"):
        evaluate_frozen_holdout(context, holdout_path=artifacts.holdout)


def test_invalid_freeze_never_calls_holdout_opener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _build_artifacts(tmp_path)
    manifest = cast(
        dict[str, object], json.loads(artifacts.manifest.read_text("utf-8"))
    )
    manifest["gates"] = {"synthetic.test_blind": False}
    artifacts.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    opener_called = False

    def fail_if_opened(_path: Path, *, expected: object) -> pd.DataFrame:
        del expected
        nonlocal opener_called
        opener_called = True
        raise AssertionError("holdout opener must not run")

    monkeypatch.setattr(
        "prescriptive_maintenance.modeling.evaluation._load_holdout_once",
        fail_if_opened,
    )

    with pytest.raises(ModelEvaluationError, match="gates"):
        run_evaluation(
            dataset_manifest_path=artifacts.manifest,
            holdout_path=artifacts.holdout,
            model_artifact_directory=artifacts.model,
            index_artifact_directory=artifacts.index,
        )

    assert not opener_called


def test_materialized_plan_is_verified_before_holdout_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _build_artifacts(tmp_path)
    context = _freeze(artifacts)
    events: list[str] = []
    real_verify = cast(
        Callable[[FrozenEvaluationContext], None],
        vars(evaluation_module)["_verify_materialized_plan"],
    )
    real_opener = cast(
        Callable[..., pd.DataFrame],
        vars(evaluation_module)["_load_holdout_once"],
    )

    def verify(value: FrozenEvaluationContext) -> None:
        assert value.materialized_plan
        assert value.materialized_plan_sha256
        events.append("materialized_plan_verified")
        real_verify(value)

    def open_after_plan(path: Path, *, expected: object) -> pd.DataFrame:
        assert events == ["materialized_plan_verified"]
        events.append("holdout_opened")
        return real_opener(path, expected=expected)

    monkeypatch.setattr(
        "prescriptive_maintenance.modeling.evaluation._verify_materialized_plan",
        verify,
    )
    monkeypatch.setattr(
        "prescriptive_maintenance.modeling.evaluation._load_holdout_once",
        open_after_plan,
    )

    evaluate_frozen_holdout(context, holdout_path=artifacts.holdout)

    assert events == ["materialized_plan_verified", "holdout_opened"]


def test_tampered_materialized_plan_fails_before_holdout_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _build_artifacts(tmp_path)
    context = replace(_freeze(artifacts), materialized_plan_sha256="0" * 64)
    opener_called = False

    def fail_if_opened(_path: Path, *, expected: object) -> pd.DataFrame:
        del expected
        nonlocal opener_called
        opener_called = True
        raise AssertionError("holdout opener must not run")

    monkeypatch.setattr(
        "prescriptive_maintenance.modeling.evaluation._load_holdout_once",
        fail_if_opened,
    )

    with pytest.raises(ModelEvaluationError, match="materialization"):
        evaluate_frozen_holdout(context, holdout_path=artifacts.holdout)

    assert not opener_called


def test_process_peak_collector_normalizes_platform_units_and_falls_back() -> None:
    collector = cast(
        Callable[..., _MemoryReading],
        vars(evaluation_module)["_process_peak_memory"],
    )

    linux = collector(platform_name="linux", unix_reader=lambda: 12)
    macos = collector(platform_name="darwin", unix_reader=lambda: 12)
    windows = collector(platform_name="win32", windows_reader=lambda: 12)
    unavailable = collector(platform_name="unsupported")
    failed = collector(
        platform_name="win32",
        windows_reader=lambda: (_ for _ in ()).throw(OSError("synthetic")),
    )

    assert linux.value_bytes == 12 * 1024
    assert linux.source == "unix_ru_maxrss_lifetime"
    assert macos.value_bytes == 12
    assert windows.value_bytes == 12
    assert unavailable.value_bytes is None
    assert unavailable.source == "unavailable"
    assert failed.value_bytes is None


def test_candidate_evaluation_snapshot_is_defensive(tmp_path: Path) -> None:
    artifacts = _build_artifacts(tmp_path)
    model = artifacts.fitted_model
    before = model.predict_candidate(
        {
            name: float(0.1 if position == 0 else position + 1)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        },
        top_k=MODEL_EVALUATION_TOP_K,
    )
    snapshot = model.evaluation_snapshot()
    snapshot.training_vectors.flags.writeable = True
    snapshot.training_vectors[0, 0] = 999.0

    assert (
        model.predict_candidate(
            {
                name: float(0.1 if position == 0 else position + 1)
                for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
            },
            top_k=MODEL_EVALUATION_TOP_K,
        )
        == before
    )


@pytest.mark.parametrize(
    ("positions", "distances"),
    [
        ((0, 1, 2, 3), (0.0, 1.0, 2.0, 3.0)),
        ((0, 1, 2, 3, 3), (0.0, 1.0, 2.0, 3.0, 4.0)),
        ((0, 1, 2, 3, 4), (0.0, 1.0, 2.0, 3.0, float("nan"))),
        ((1, 0, 2, 3, 4), (1.0, 0.0, 2.0, 3.0, 4.0)),
    ],
)
def test_ranked_neighbor_ingress_fails_closed(
    tmp_path: Path,
    positions: tuple[object, ...],
    distances: tuple[object, ...],
) -> None:
    model = _build_artifacts(tmp_path).fitted_model

    with pytest.raises(KnnInputError, match="ranking"):
        model.candidate_from_ranked_neighbors(positions, distances)
