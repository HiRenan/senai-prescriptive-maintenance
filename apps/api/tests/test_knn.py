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
    KNN_ARTIFACT_FILENAMES,
    KNN_METRIC,
    KNN_SUPPORT_HEURISTIC,
    InMemoryKnnModel,
    KnnArtifactError,
    KnnInputError,
    KnnModelPortAdapter,
    KnnTrainingError,
    fit_knn_model,
    load_knn_model,
    save_knn_model,
)
from prescriptive_maintenance.ports import ModelDisposition

DATASET_ID = "a" * 64


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
) -> InMemoryKnnModel:
    return fit_knn_model(
        _training_frame(first_values, labels),
        dataset_id=DATASET_ID,
        normal_target_labels=normal_target_labels,
    )


def _analysis_features(first_value: float) -> AnalysisFeatures:
    return AnalysisFeatures.model_validate(_features(first_value))


def test_scaler_is_fit_only_on_the_supplied_train_partition() -> None:
    training = _training_frame(
        (0.0, 2.0, 4.0),
        ("synthetic-normal", "synthetic-fault", "synthetic-fault"),
    )
    holdout = _training_frame((1000.0,), ("synthetic-fault",))

    model = fit_knn_model(
        training,
        dataset_id=DATASET_ID,
        normal_target_labels=("synthetic-normal",),
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
        fit_knn_model(frame, dataset_id=DATASET_ID)


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
        fit_knn_model(frame, dataset_id=DATASET_ID)


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
    assert first.support_score == 0.5


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
    adapter = KnnModelPortAdapter(_model())

    normal = adapter.predict(_analysis_features(0.0), top_k=1)
    fault = adapter.predict(_analysis_features(2.0), top_k=1)

    assert normal.disposition is ModelDisposition.NORMAL
    assert normal.retrieval_key is None
    assert fault.disposition is ModelDisposition.FAULT
    assert fault.retrieval_key == "synthetic-fault"
    assert normal.diagnosis is not None
    assert fault.diagnosis is not None
    assert normal.diagnosis.code.startswith("fault_")
    assert fault.diagnosis.code.startswith("fault_")
    assert 0.0 <= normal.support_score <= 1.0
    assert 0.0 <= fault.support_score <= 1.0


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


def test_fit_and_prediction_are_repeatable() -> None:
    first = _model()
    second = _model()

    assert first.model_id == second.model_id
    assert first.content_sha256 == second.content_sha256
    assert first.labels == second.labels
    assert first.predict_candidate(_features(1.0), top_k=2) == (
        second.predict_candidate(_features(1.0), top_k=2)
    )


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
    assert loaded.predict_candidate(_features(1.0), top_k=2) == (
        model.predict_candidate(_features(1.0), top_k=2)
    )

    manifest = json.loads((first_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["configuration"] == {
        "metric": KNN_METRIC,
        "default_top_k": 5,
        "max_top_k": MAX_TOP_K,
        "distance_order": "distance_ascending",
        "distance_tie_break": "neighbor_ref_ascending",
        "class_tie_break": (
            "vote_count_descending_then_distance_sum_ascending_then_"
            "target_slug_ascending"
        ),
        "support_heuristic": KNN_SUPPORT_HEURISTIC,
        "support_is_probability": False,
        "preprocessor": "sklearn.preprocessing.StandardScaler",
        "preprocessor_fit_partition": "train",
        "imputation": "not_applied",
        "normal_target_labels": ["synthetic-normal"],
    }


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
        load_knn_model(directory, expected_model_id="model_knn_v1_wrong")


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
