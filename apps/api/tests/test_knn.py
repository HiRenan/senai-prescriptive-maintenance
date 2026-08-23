"""Synthetic contract tests for the deterministic k-NN baseline."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd
import prescriptive_maintenance.modeling.knn as knn_module
import pytest
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    MAX_TOP_K,
    AnalysisFeatures,
)
from prescriptive_maintenance.modeling import (
    KNN_ABSTENTION_POLICY_VERSION,
    KNN_ARTIFACT_FILENAMES,
    KNN_CALIBRATION_SAMPLE_LIMIT,
    KNN_DISTANCE_QUANTILE,
    KNN_METRIC,
    KNN_SUPPORT_HEURISTIC,
    KNN_VOTE_MARGIN_QUANTILE,
    InMemoryKnnModel,
    KnnAbstentionPolicy,
    KnnArtifactError,
    KnnConfigurationError,
    KnnInputError,
    KnnModelPortAdapter,
    KnnTrainingError,
    fit_knn_model,
    load_knn_model,
    save_knn_model,
)
from prescriptive_maintenance.ports import ModelAbstentionReason, ModelDisposition

DATASET_ID = "a" * 64
TRAINING_PARTITION_SHA256 = "b" * 64
VALIDATION_PARTITION_SHA256 = "c" * 64
ALTERNATE_VALIDATION_PARTITION_SHA256 = "e" * 64


def _features(first_value: float) -> dict[str, float]:
    values = {name: 1.0 for name in ANALYSIS_FEATURE_NAMES}
    values["z_rms_velocity_mm_s"] = first_value
    values["temperature_c"] = 20.0
    values["rpm"] = 1200.0
    return values


def _training_frame(
    first_values: tuple[float, ...], labels: tuple[str, ...]
) -> pd.DataFrame:
    frame = pd.DataFrame(
        [_features(value) for value in first_values],
        columns=ANALYSIS_FEATURE_NAMES,
        dtype="float64",
    )
    frame["y"] = pd.Series(labels, dtype="string")
    return frame


def _model(
    *,
    first_values: tuple[float, ...] = (0.0, 2.0),
    labels: tuple[str, ...] = ("synthetic-normal", "synthetic-fault"),
    normal_target_labels: tuple[str, ...] = ("synthetic-normal",),
    validation_values: tuple[float, ...] | None = None,
    minimum_class_count: int = 1,
    distance_quantile: float = KNN_DISTANCE_QUANTILE,
    vote_margin_quantile: float = KNN_VOTE_MARGIN_QUANTILE,
    default_top_k: int = 5,
) -> InMemoryKnnModel:
    validation = (
        None
        if validation_values is None
        else _training_frame(
            validation_values,
            tuple("synthetic-validation" for _ in validation_values),
        )
    )
    return fit_knn_model(
        _training_frame(first_values, labels),
        dataset_id=DATASET_ID,
        training_partition_sha256=TRAINING_PARTITION_SHA256,
        validation_frame=validation,
        validation_partition_sha256=(
            None if validation is None else VALIDATION_PARTITION_SHA256
        ),
        normal_target_labels=normal_target_labels,
        minimum_class_count=minimum_class_count,
        distance_quantile=distance_quantile,
        vote_margin_quantile=vote_margin_quantile,
        default_top_k=default_top_k,
    )


def _analysis_features(first_value: float) -> AnalysisFeatures:
    return AnalysisFeatures.model_validate(_features(first_value))


def _write_manifest(path: Path, manifest: object) -> None:
    path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_scaler_is_fit_only_on_the_supplied_train_partition() -> None:
    training = _training_frame(
        (0.0, 2.0, 4.0),
        ("synthetic-normal", "synthetic-fault", "synthetic-fault"),
    )
    holdout = _training_frame((1000.0,), ("synthetic-fault",))

    model = fit_knn_model(
        training,
        dataset_id=DATASET_ID,
        training_partition_sha256=TRAINING_PARTITION_SHA256,
        normal_target_labels=("synthetic-normal",),
        minimum_class_count=1,
    )

    expected_train_mean = tuple(
        float(value) for value in training.loc[:, list(ANALYSIS_FEATURE_NAMES)].mean()
    )
    leaked_mean = float(
        pd.concat([training, holdout], ignore_index=True)["z_rms_velocity_mm_s"].mean()
    )
    assert model.preprocessor_state.mean == expected_train_mean
    assert model.preprocessor_state.mean[0] != leaked_mean
    assert model.preprocessor_state.sample_count == len(training)


def test_validation_calibrates_versioned_thresholds_without_refitting_scaler() -> None:
    training = _training_frame(
        (0.0, 2.0, 4.0, 6.0),
        ("synthetic-a", "synthetic-a", "synthetic-b", "synthetic-b"),
    )
    validation = _training_frame(
        (1.0, 3.0, 5.0),
        ("unseen-a", "unseen-b", "unseen-c"),
    )

    model = fit_knn_model(
        training,
        validation_frame=validation,
        dataset_id=DATASET_ID,
        training_partition_sha256=TRAINING_PARTITION_SHA256,
        validation_partition_sha256=VALIDATION_PARTITION_SHA256,
        minimum_class_count=1,
    )

    policy = model.abstention_policy
    assert policy.version == KNN_ABSTENTION_POLICY_VERSION
    assert policy.calibration_partition == "validation"
    assert policy.calibration_partition_sha256 == VALIDATION_PARTITION_SHA256
    assert policy.calibration_sample_count == len(validation)
    assert policy.distance_quantile == KNN_DISTANCE_QUANTILE
    assert policy.vote_margin_quantile == KNN_VOTE_MARGIN_QUANTILE
    assert model.training_partition_sha256 == TRAINING_PARTITION_SHA256
    assert model.preprocessor_state.mean == tuple(
        float(value) for value in training.loc[:, list(ANALYSIS_FEATURE_NAMES)].mean()
    )


def test_validation_targets_do_not_change_fitted_threshold_values() -> None:
    training = _training_frame(
        (0.0, 2.0, 4.0),
        ("synthetic-a", "synthetic-b", "synthetic-b"),
    )
    first_validation = _training_frame(
        (1.0, 3.0),
        ("synthetic-first", "synthetic-first"),
    )
    second_validation = _training_frame(
        (1.0, 3.0),
        ("synthetic-other", "synthetic-unseen"),
    )

    first = fit_knn_model(
        training,
        validation_frame=first_validation,
        dataset_id=DATASET_ID,
        training_partition_sha256=TRAINING_PARTITION_SHA256,
        validation_partition_sha256=VALIDATION_PARTITION_SHA256,
        minimum_class_count=1,
    )
    second = fit_knn_model(
        training,
        validation_frame=second_validation,
        dataset_id=DATASET_ID,
        training_partition_sha256=TRAINING_PARTITION_SHA256,
        validation_partition_sha256=ALTERNATE_VALIDATION_PARTITION_SHA256,
        minimum_class_count=1,
    )

    assert first.abstention_policy.distance_threshold == (
        second.abstention_policy.distance_threshold
    )
    assert first.abstention_policy.vote_margin_threshold == (
        second.abstention_policy.vote_margin_threshold
    )
    assert first.abstention_policy.calibration_sample_count == (
        second.abstention_policy.calibration_sample_count
    )
    assert first.abstention_policy.calibration_partition_sha256 != (
        second.abstention_policy.calibration_partition_sha256
    )
    assert first.model_id != second.model_id


@pytest.mark.parametrize(
    ("with_validation", "with_hash"),
    ((True, False), (False, True)),
)
def test_validation_calibration_requires_frame_and_hash_together(
    with_validation: bool,
    with_hash: bool,
) -> None:
    validation = _training_frame((1.0,), ("synthetic-validation",))

    with pytest.raises(KnnTrainingError, match="both a frame and its SHA-256"):
        fit_knn_model(
            _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b")),
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
            validation_frame=validation if with_validation else None,
            validation_partition_sha256=(
                VALIDATION_PARTITION_SHA256 if with_hash else None
            ),
        )


def test_validation_rejects_non_finite_features() -> None:
    validation = _training_frame((1.0,), ("synthetic-validation",))
    validation.loc[0, ANALYSIS_FEATURE_NAMES[4]] = float("nan")

    with pytest.raises(KnnTrainingError, match="finite and canonical"):
        fit_knn_model(
            _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b")),
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
            validation_frame=validation,
            validation_partition_sha256=VALIDATION_PARTITION_SHA256,
        )


def test_validation_partition_identity_must_differ_from_training() -> None:
    with pytest.raises(KnnTrainingError, match="identities must be distinct"):
        fit_knn_model(
            _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b")),
            validation_frame=_training_frame(
                (1.0,),
                ("synthetic-validation",),
            ),
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
            validation_partition_sha256=TRAINING_PARTITION_SHA256,
        )


def test_leave_one_out_requires_at_least_two_training_samples() -> None:
    with pytest.raises(KnnTrainingError, match="at least two training samples"):
        fit_knn_model(
            _training_frame((0.0,), ("synthetic-a",)),
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
        )


@pytest.mark.parametrize(
    ("parameter", "value"),
    (
        ("distance_quantile", 0.0),
        ("distance_quantile", 1.0),
        ("vote_margin_quantile", float("nan")),
        ("minimum_class_count", 0),
        ("minimum_class_count", True),
    ),
)
def test_abstention_configuration_fails_closed(
    parameter: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "dataset_id": DATASET_ID,
        "training_partition_sha256": TRAINING_PARTITION_SHA256,
    }
    arguments[parameter] = value

    with pytest.raises(KnnTrainingError, match="policy configuration"):
        fit_knn_model(
            _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b")),
            **arguments,  # type: ignore[arg-type]
        )


def test_calibration_sampling_is_bounded_and_deterministic() -> None:
    validation_values = tuple(float(index) for index in range(600))
    model = _model(validation_values=validation_values)
    repeated = _model(validation_values=validation_values)

    assert model.abstention_policy.calibration_sample_count == (
        KNN_CALIBRATION_SAMPLE_LIMIT
    )
    assert model.abstention_policy == repeated.abstention_policy
    assert model.model_id == repeated.model_id


@pytest.mark.parametrize("mutation", ("missing", "extra", "reordered"))
def test_training_requires_exact_canonical_feature_order(mutation: str) -> None:
    frame = _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b"))
    if mutation == "missing":
        frame = frame.drop(columns=[ANALYSIS_FEATURE_NAMES[-1]])
    elif mutation == "extra":
        frame.insert(0, "synthetic_extra", 1.0)
    else:
        frame = pd.DataFrame(
            frame.loc[
                :,
                (
                    ANALYSIS_FEATURE_NAMES[1],
                    ANALYSIS_FEATURE_NAMES[0],
                    *ANALYSIS_FEATURE_NAMES[2:],
                    "y",
                ),
            ]
        )

    with pytest.raises(KnnTrainingError, match="canonical order"):
        fit_knn_model(
            frame,
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
        )


def test_inference_requires_exact_canonical_feature_order() -> None:
    model = _model()
    reordered = OrderedDict(_features(1.0))
    reordered.move_to_end(ANALYSIS_FEATURE_NAMES[0])

    with pytest.raises(KnnInputError, match="canonical order"):
        model.predict_candidate(reordered)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_training_rejects_non_finite_values_without_imputation(value: float) -> None:
    frame = _training_frame((0.0, 2.0), ("synthetic-a", "synthetic-b"))
    frame.loc[0, ANALYSIS_FEATURE_NAMES[3]] = value

    with pytest.raises(KnnTrainingError, match="finite"):
        fit_knn_model(
            frame,
            dataset_id=DATASET_ID,
            training_partition_sha256=TRAINING_PARTITION_SHA256,
        )


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_inference_rejects_non_finite_values(value: float) -> None:
    model = _model()
    features = _features(1.0)
    features[ANALYSIS_FEATURE_NAMES[3]] = value

    with pytest.raises(KnnInputError, match="finite"):
        model.predict_candidate(features)


def test_euclidean_distances_are_computed_after_train_scaling() -> None:
    model = _model(labels=("synthetic-a", "synthetic-b"), normal_target_labels=())

    prediction = model.predict_candidate(_features(0.0), top_k=2)

    assert [neighbor.distance for neighbor in prediction.neighbors] == [0.0, 2.0]
    assert prediction.neighbors[0].target_slug == "synthetic-a"
    assert prediction.support_score == 0.5


@pytest.mark.parametrize("top_k", (0, MAX_TOP_K + 1, True, 1.5, "2"))
def test_top_k_is_bounded_by_the_public_contract(top_k: object) -> None:
    model = _model()

    with pytest.raises(KnnInputError, match="top_k"):
        model.predict_candidate(_features(1.0), top_k=top_k)  # type: ignore[arg-type]


def test_top_k_is_limited_by_available_training_rows() -> None:
    model = _model()

    prediction = model.predict_candidate(_features(1.0), top_k=MAX_TOP_K)

    assert len(prediction.neighbors) == model.sample_count == 2
    assert tuple(item.rank for item in prediction.neighbors) == (1, 2)


def test_distance_and_class_ties_have_stable_total_order() -> None:
    model = _model(
        labels=("synthetic-zeta", "synthetic-alpha"),
        normal_target_labels=(),
    )

    first = model.predict_candidate(_features(1.0), top_k=2)
    second = model.predict_candidate(_features(1.0), top_k=2)

    assert first == second
    assert tuple(item.neighbor_ref for item in first.neighbors) == tuple(
        sorted(item.neighbor_ref for item in first.neighbors)
    )
    assert first.target_slug == "synthetic-alpha"
    assert first.support_score == 0.333333333333
    assert first.winning_vote_share == 0.5
    assert first.vote_margin == 0.0
    assert first.nearest_distance == 1.0
    assert first.abstention_reason is ModelAbstentionReason.INCONCLUSIVE_VOTE


def test_distance_boundary_is_inclusive_and_extreme_distance_abstains() -> None:
    model = _model(validation_values=(4.0,), default_top_k=1)

    at_boundary = model.predict_candidate(_features(4.0), top_k=1)
    beyond_boundary = model.predict_candidate(_features(4.000001), top_k=1)

    assert model.abstention_policy.distance_threshold == 2.0
    assert at_boundary.nearest_distance == model.abstention_policy.distance_threshold
    assert at_boundary.abstention_reason is None
    assert beyond_boundary.abstention_reason is (
        ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION
    )


def test_inconclusive_vote_abstains_at_the_exact_margin_boundary() -> None:
    model = _model(validation_values=(0.0,))

    candidate = model.predict_candidate(_features(0.0), top_k=2)

    assert model.abstention_policy.vote_margin_threshold == 0.0
    assert candidate.vote_margin == model.abstention_policy.vote_margin_threshold
    assert candidate.abstention_reason is ModelAbstentionReason.INCONCLUSIVE_VOTE


def test_rare_candidate_class_has_a_typed_abstention_reason() -> None:
    model = _model(
        first_values=(0.0, 1.0, 2.0),
        labels=("synthetic-rare", "synthetic-common", "synthetic-common"),
        normal_target_labels=(),
        validation_values=(0.0,),
        minimum_class_count=2,
        default_top_k=1,
    )

    candidate = model.predict_candidate(_features(0.0), top_k=1)

    assert candidate.target_slug == "synthetic-rare"
    assert candidate.abstention_reason is ModelAbstentionReason.RARE_CLASS_SUPPORT


def test_support_heuristic_decreases_with_distance_for_the_same_vote_share() -> None:
    model = _model(
        labels=("synthetic-a", "synthetic-a"),
        normal_target_labels=(),
        validation_values=(4.0,),
    )

    exact = model.predict_candidate(_features(0.0), top_k=1)
    farther = model.predict_candidate(_features(3.0), top_k=1)

    assert exact.winning_vote_share == farther.winning_vote_share == 1.0
    assert exact.nearest_distance < farther.nearest_distance
    assert 0.0 <= farther.support_score < exact.support_score <= 1.0


def test_abstention_priority_is_distance_then_rarity_then_vote() -> None:
    distance_model = _model(
        validation_values=(0.0,),
        minimum_class_count=2,
    )
    rare_model = _model(
        first_values=(0.0, 2.0, 4.0),
        labels=("synthetic-rare", "synthetic-common", "synthetic-common"),
        normal_target_labels=(),
        validation_values=(0.0,),
        minimum_class_count=2,
        default_top_k=1,
    )

    assert (
        distance_model.predict_candidate(_features(100.0), top_k=2).abstention_reason
        is ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION
    )
    assert (
        rare_model.predict_candidate(_features(0.0), top_k=2).abstention_reason
        is ModelAbstentionReason.RARE_CLASS_SUPPORT
    )


def test_label_translation_is_bijective_and_collision_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def colliding_fault_code(_target: str) -> str:
        return "fault_collision"

    monkeypatch.setattr(knn_module, "_fault_code", colliding_fault_code)

    with pytest.raises(KnnTrainingError, match="bijective"):
        _model(labels=("synthetic-a", "synthetic-b"), normal_target_labels=())


def test_normal_labels_must_be_explicit_training_classes() -> None:
    with pytest.raises(KnnTrainingError, match="training classes"):
        _model(normal_target_labels=("synthetic-absent",))


def test_model_port_adapter_maps_normal_and_fault_without_abstention() -> None:
    adapter = KnnModelPortAdapter(_model(default_top_k=1))

    normal = adapter.predict(_analysis_features(0.0), top_k=1)
    fault = adapter.predict(_analysis_features(2.0), top_k=1)

    assert normal.disposition is ModelDisposition.NORMAL
    assert normal.abstention_reason is None
    assert normal.retrieval_key is None
    assert fault.disposition is ModelDisposition.FAULT
    assert fault.abstention_reason is None
    assert fault.retrieval_key == "synthetic-fault"
    assert normal.diagnosis is not None
    assert fault.diagnosis is not None
    assert normal.diagnosis.code.startswith("fault_")
    assert fault.diagnosis.code.startswith("fault_")
    assert 0.0 <= normal.support_score <= 1.0
    assert 0.0 <= fault.support_score <= 1.0


@pytest.mark.parametrize(
    ("features", "top_k", "reason"),
    (
        (
            100.0,
            1,
            ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION,
        ),
        (0.0, 2, ModelAbstentionReason.INCONCLUSIVE_VOTE),
    ),
)
def test_model_port_maps_typed_abstention_without_document_lookup(
    features: float,
    top_k: int,
    reason: ModelAbstentionReason,
) -> None:
    prediction = KnnModelPortAdapter(_model(validation_values=(0.0,))).predict(
        _analysis_features(features), top_k=top_k
    )

    assert prediction.disposition is ModelDisposition.OUT_OF_DISTRIBUTION
    assert prediction.abstention_reason is reason
    assert prediction.diagnosis is None
    assert prediction.retrieval_key is None
    assert prediction.neighbors


def test_model_port_exposes_only_opaque_neighbor_contract() -> None:
    prediction = KnnModelPortAdapter(_model()).predict(
        _analysis_features(1.0),
        top_k=2,
    )

    assert prediction.neighbors
    for neighbor in prediction.neighbors:
        payload = neighbor.model_dump(mode="json")
        assert set(payload) == {"neighbor_ref", "rank", "fault_code", "distance"}
        assert payload["neighbor_ref"].startswith("neighbor_")
        assert not {
            "row",
            "features",
            "feature_vector",
            "source_id",
            "record_id",
            "timestamp",
            "target_slug",
        } & set(payload)


def test_model_port_exposes_exact_unicode_diagnosis_summary() -> None:
    prediction = KnnModelPortAdapter(_model(default_top_k=1)).predict(
        _analysis_features(1.0),
        top_k=1,
    )

    assert prediction.diagnosis is not None
    assert prediction.diagnosis.summary == (
        "Classe candidata da baseline k-NN; o suporte combina "
        "votos e distância como heurística, não probabilidade."
    )


def test_requested_top_k_changes_only_returned_evidence() -> None:
    model = _model(
        first_values=tuple(float(index) for index in range(MAX_TOP_K)),
        labels=tuple(
            "synthetic-a" if index % 2 == 0 else "synthetic-b"
            for index in range(MAX_TOP_K)
        ),
        normal_target_labels=(),
        validation_values=(0.25,),
        minimum_class_count=1,
    )
    adapter = KnnModelPortAdapter(model)

    candidates = tuple(
        model.predict_candidate(_features(0.25), top_k=top_k)
        for top_k in range(1, MAX_TOP_K + 1)
    )
    candidate_signatures = {
        (
            candidate.target_slug,
            candidate.support_score,
            candidate.winning_vote_share,
            candidate.vote_margin,
            candidate.nearest_distance,
            candidate.abstention_reason,
        )
        for candidate in candidates
    }

    assert len(candidate_signatures) == 1
    assert candidates[0].abstention_reason is ModelAbstentionReason.INCONCLUSIVE_VOTE
    assert tuple(len(candidate.neighbors) for candidate in candidates) == tuple(
        range(1, MAX_TOP_K + 1)
    )
    assert (
        tuple(candidate.abstention_reason for candidate in candidates[:4])
        == (candidates[0].abstention_reason,) * 4
    )

    predictions = tuple(
        adapter.predict(_analysis_features(0.25), top_k=top_k)
        for top_k in range(1, MAX_TOP_K + 1)
    )
    public_signatures = {
        (
            prediction.disposition,
            prediction.abstention_reason,
            prediction.support_score,
            None if prediction.diagnosis is None else prediction.diagnosis.code,
            prediction.retrieval_key,
        )
        for prediction in predictions
    }
    assert len(public_signatures) == 1
    assert tuple(len(prediction.neighbors) for prediction in predictions) == tuple(
        range(1, MAX_TOP_K + 1)
    )


def test_abstention_policy_is_copied_on_constructor_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_policy = _model().abstention_policy

    def external_fit(**_arguments: object) -> KnnAbstentionPolicy:
        return external_policy

    monkeypatch.setattr(knn_module, "_fit_abstention_policy", external_fit)
    model = _model()
    model_id = model.model_id
    prediction = model.predict_candidate(_features(100.0), top_k=1)

    object.__setattr__(external_policy, "distance_threshold", 1000.0)

    assert model.abstention_policy.distance_threshold != 1000.0
    assert model.model_id == model_id
    assert model.predict_candidate(_features(100.0), top_k=1) == prediction


def test_constructor_snapshots_policy_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _model()
    external_policy = baseline.abstention_policy
    expected_threshold = external_policy.distance_threshold
    expected_prediction = baseline.predict_candidate(_features(100.0), top_k=1)

    def external_fit(**_arguments: object) -> KnnAbstentionPolicy:
        return external_policy

    def mutate_external_during_binding(
        policy: KnnAbstentionPolicy,
        *,
        training_partition_sha256: str,
        training_sample_count: int,
        error_type: type[Exception],
    ) -> None:
        assert policy.distance_threshold == expected_threshold
        assert training_partition_sha256 == TRAINING_PARTITION_SHA256
        assert training_sample_count == baseline.sample_count
        assert error_type is KnnConfigurationError
        object.__setattr__(external_policy, "distance_threshold", -1.0)

    monkeypatch.setattr(knn_module, "_fit_abstention_policy", external_fit)
    monkeypatch.setattr(
        knn_module,
        "_validate_abstention_policy_binding",
        mutate_external_during_binding,
    )

    model = _model()

    assert external_policy.distance_threshold == -1.0
    assert model.abstention_policy.distance_threshold == expected_threshold
    assert model.model_id == baseline.model_id
    assert model.predict_candidate(_features(100.0), top_k=1) == expected_prediction


def test_abstention_policy_property_returns_a_defensive_copy() -> None:
    model = _model()
    exposed_policy = model.abstention_policy
    model_id = model.model_id
    prediction = model.predict_candidate(_features(100.0), top_k=1)

    object.__setattr__(exposed_policy, "distance_threshold", 1000.0)

    assert model.abstention_policy.distance_threshold != 1000.0
    assert model.model_id == model_id
    assert model.predict_candidate(_features(100.0), top_k=1) == prediction


def test_constructor_rejects_cross_partition_policy_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_policy = _model().abstention_policy
    object.__setattr__(
        invalid_policy,
        "calibration_partition_sha256",
        VALIDATION_PARTITION_SHA256,
    )

    def invalid_fit(**_arguments: object) -> KnnAbstentionPolicy:
        return invalid_policy

    monkeypatch.setattr(knn_module, "_fit_abstention_policy", invalid_fit)

    with pytest.raises(KnnConfigurationError, match="policy binding"):
        _model()


def test_fit_and_prediction_are_repeatable() -> None:
    first = _model()
    second = _model()

    assert first.model_id == second.model_id
    assert first.content_sha256 == second.content_sha256
    assert first.labels == second.labels
    assert first.predict_candidate(_features(1.0), top_k=2) == (
        second.predict_candidate(_features(1.0), top_k=2)
    )


def test_threshold_configuration_and_partition_hash_change_model_identity() -> None:
    default = _model(validation_values=(1.0, 3.0))
    changed_quantile = _model(
        validation_values=(1.0, 3.0),
        distance_quantile=0.75,
    )
    changed_training_hash = fit_knn_model(
        _training_frame(
            (0.0, 2.0),
            ("synthetic-normal", "synthetic-fault"),
        ),
        validation_frame=_training_frame(
            (1.0, 3.0),
            ("synthetic-validation", "synthetic-validation"),
        ),
        dataset_id=DATASET_ID,
        training_partition_sha256="d" * 64,
        validation_partition_sha256=VALIDATION_PARTITION_SHA256,
        normal_target_labels=("synthetic-normal",),
        minimum_class_count=1,
    )

    identities = {
        default.model_id,
        changed_quantile.model_id,
        changed_training_hash.model_id,
    }
    assert len(identities) == 3


def test_safe_artifact_is_byte_stable_and_round_trips(tmp_path: Path) -> None:
    model = _model()
    first_path = save_knn_model(model, tmp_path / "first")
    second_path = save_knn_model(model, tmp_path / "second")

    assert tuple(sorted(item.name for item in first_path.iterdir())) == tuple(
        sorted(KNN_ARTIFACT_FILENAMES)
    )
    assert all(
        (first_path / name).read_bytes() == (second_path / name).read_bytes()
        for name in KNN_ARTIFACT_FILENAMES
    )
    loaded = load_knn_model(first_path, expected_model_id=model.model_id)
    assert loaded.model_id == model.model_id
    assert loaded.labels == model.labels
    assert loaded.normal_target_labels == model.normal_target_labels
    assert loaded.preprocessor_state == model.preprocessor_state
    assert loaded.abstention_policy == model.abstention_policy
    assert loaded.training_partition_sha256 == model.training_partition_sha256
    assert loaded.predict_candidate(_features(1.0), top_k=2) == (
        model.predict_candidate(_features(1.0), top_k=2)
    )

    manifest = json.loads((first_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["configuration"] == {
        "metric": KNN_METRIC,
        "default_top_k": 5,
        "max_top_k": MAX_TOP_K,
        "decision_top_k_source": "default_top_k",
        "requested_top_k_role": "evidence_only",
        "distance_order": "distance_ascending",
        "distance_tie_break": "neighbor_ref_ascending",
        "class_tie_break": (
            "vote_count_descending_then_distance_sum_ascending_then_"
            "target_slug_ascending"
        ),
        "support_heuristic": KNN_SUPPORT_HEURISTIC,
        "support_components": [
            "winning_vote_share",
            "nearest_neighbor_distance",
        ],
        "support_is_probability": False,
        "distance_abstention_boundary": "greater_than_threshold",
        "vote_margin_abstention_boundary": "less_than_or_equal_to_threshold",
        "rare_class_abstention_boundary": "less_than_minimum_class_count",
        "test_partition_usage": "forbidden",
        "preprocessor": "sklearn.preprocessing.StandardScaler",
        "preprocessor_fit_partition": "train",
        "imputation": "not_applied",
        "normal_target_labels": ["synthetic-normal"],
    }
    assert manifest["abstention_policy"] == {
        "version": KNN_ABSTENTION_POLICY_VERSION,
        "distance_threshold": 2.0,
        "vote_margin_threshold": 0.9999999999999999,
        "minimum_class_count": 1,
        "calibration_partition": "train_leave_one_out",
        "calibration_partition_sha256": TRAINING_PARTITION_SHA256,
        "calibration_sample_count": 2,
        "distance_quantile": KNN_DISTANCE_QUANTILE,
        "vote_margin_quantile": KNN_VOTE_MARGIN_QUANTILE,
        "sampling": "evenly_spaced_input_order",
    }
    assert manifest["training"]["partition_sha256"] == (TRAINING_PARTITION_SHA256)


def test_load_rejects_altered_array(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")
    path = directory / "training_vectors.npy"
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)

    with pytest.raises(KnnArtifactError, match="integrity"):
        load_knn_model(directory)


def test_load_rejects_incomplete_or_extra_artifact(tmp_path: Path) -> None:
    incomplete = save_knn_model(_model(), tmp_path / "incomplete")
    (incomplete / "neighbor_refs.npy").unlink()
    with pytest.raises(KnnArtifactError, match="file set"):
        load_knn_model(incomplete)

    extra = save_knn_model(_model(), tmp_path / "extra")
    (extra / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    with pytest.raises(KnnArtifactError, match="file set"):
        load_knn_model(extra)


def test_load_rejects_wrong_expected_model_id(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")

    with pytest.raises(KnnArtifactError, match="identity"):
        load_knn_model(directory, expected_model_id="model_knn_v2_wrong")


def test_load_rejects_invalid_abstention_threshold(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["abstention_policy"]["distance_threshold"] = -1.0
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(KnnArtifactError, match="policy"):
        load_knn_model(directory)


def test_load_rejects_incompatible_manifest_runtime(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["compatibility"]["numpy_version"] = "0.0.0"
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(KnnArtifactError, match="runtime"):
        load_knn_model(directory)


def test_load_rejects_leave_one_out_partition_hash_mismatch(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["abstention_policy"]["calibration_partition_sha256"] = (
        VALIDATION_PARTITION_SHA256
    )
    _write_manifest(manifest_path, manifest)

    with pytest.raises(KnnArtifactError, match="policy binding"):
        load_knn_model(directory)


def test_load_rejects_leave_one_out_sample_count_mismatch(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["abstention_policy"]["calibration_sample_count"] = 1
    _write_manifest(manifest_path, manifest)

    with pytest.raises(KnnArtifactError, match="policy binding"):
        load_knn_model(directory)


def test_load_rejects_validation_hash_equal_to_training(tmp_path: Path) -> None:
    directory = save_knn_model(
        _model(validation_values=(1.0,)),
        tmp_path / "model",
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["abstention_policy"]["calibration_partition_sha256"] = (
        TRAINING_PARTITION_SHA256
    )
    _write_manifest(manifest_path, manifest)

    with pytest.raises(KnnArtifactError, match="policy binding"):
        load_knn_model(directory)


def test_save_rejects_non_ignored_destination_inside_worktree() -> None:
    repository = Path(__file__).parents[3]
    destination = repository / "synthetic-unsafe-model-output"

    with pytest.raises(KnnArtifactError, match="not ignored"):
        save_knn_model(_model(), destination)
    assert not destination.exists()


def test_artifact_format_never_uses_executable_pickle(tmp_path: Path) -> None:
    directory = save_knn_model(_model(), tmp_path / "model")

    assert {item.suffix for item in directory.iterdir()} == {".json", ".npy"}
    for name in KNN_ARTIFACT_FILENAMES[1:]:
        with (directory / name).open("rb") as stream:
            array = np.load(stream, allow_pickle=False)
        assert isinstance(array, np.ndarray)
