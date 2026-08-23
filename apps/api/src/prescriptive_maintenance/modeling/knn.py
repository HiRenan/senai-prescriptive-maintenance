"""Deterministic in-memory k-NN baseline over the canonical 18 features."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from math import fsum, isfinite
from pathlib import Path
from typing import Final, NoReturn, Protocol, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn import (  # pyright: ignore[reportMissingTypeStubs]
    __version__ as sklearn_version,
)
from sklearn.preprocessing import (  # pyright: ignore[reportMissingTypeStubs]
    StandardScaler,
)

from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_COUNT,
    ANALYSIS_FEATURE_NAMES,
    API_CONTRACT_VERSION,
    DEFAULT_TOP_K,
    MAX_TOP_K,
    AnalysisFeatures,
    Diagnosis,
    OpaqueNeighbor,
)
from prescriptive_maintenance.data import (
    CANONICAL_FEATURE_CONTRACT_VERSION,
    load_canonical_pipeline_config,
)
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPrediction,
)

KNN_ARTIFACT_SCHEMA_VERSION: Final = 2
KNN_MODEL_VERSION: Final = 2
KNN_METRIC: Final = "euclidean"
KNN_SUPPORT_HEURISTIC: Final = "vote_share_times_inverse_distance_ratio"
KNN_ABSTENTION_POLICY_VERSION: Final = 1
KNN_DISTANCE_QUANTILE: Final = 0.95
KNN_VOTE_MARGIN_QUANTILE: Final = 0.10
KNN_MINIMUM_CLASS_COUNT: Final = 2
KNN_CALIBRATION_SAMPLE_LIMIT: Final = 512
KNN_ARTIFACT_FILENAMES: Final[tuple[str, ...]] = (
    "manifest.json",
    "training_vectors.npy",
    "target_indices.npy",
    "neighbor_refs.npy",
)

_MODEL_FAMILY: Final = "knn"
_PREPROCESSOR: Final = "sklearn.preprocessing.StandardScaler"
_IMPUTATION: Final = "not_applied"
_DISTANCE_TIE_BREAK: Final = "neighbor_ref_ascending"
_CLASS_TIE_BREAK: Final = (
    "vote_count_descending_then_distance_sum_ascending_then_target_slug_ascending"
)
_MODEL_ID_PREFIX: Final = "model_knn_v2_"
_CALIBRATION_SAMPLING: Final = "evenly_spaced_input_order"
_FAULT_CODE_PREFIX: Final = "fault_"
_NEIGHBOR_REF_PREFIX: Final = "neighbor_"
_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_FAULT_CODE_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_NEIGHBOR_REF_PATTERN: Final = re.compile(r"^neighbor_[a-z0-9_]{3,64}$")
_TARGET_MAX_LENGTH: Final = 200
_VECTOR_DTYPE: Final = np.dtype("<f8")
_TARGET_INDEX_DTYPE: Final = np.dtype("<i4")
_NEIGHBOR_REF_DTYPE: Final = np.dtype("<U41")

type FloatMatrix = NDArray[np.float64]
type FloatVector = NDArray[np.float64]
type TargetIndexVector = NDArray[np.int32]
type TextVector = NDArray[np.str_]


class _StandardScaler(Protocol):
    mean_: FloatVector
    scale_: FloatVector
    var_: FloatVector
    n_features_in_: int
    n_samples_seen_: int

    def fit_transform(self, values: FloatMatrix) -> FloatMatrix: ...

    def transform(self, values: FloatMatrix) -> FloatMatrix: ...


class KnnError(Exception):
    """Base class for sanitized k-NN failures."""


class KnnConfigurationError(KnnError):
    """Raised when model configuration is incompatible or ambiguous."""


class KnnTrainingError(KnnError):
    """Raised when training data violates the canonical contract."""


class KnnInputError(KnnError):
    """Raised when an inference input violates the feature contract."""


class KnnArtifactError(KnnError):
    """Raised when a serialized artifact is unsafe, incomplete, or altered."""


@dataclass(frozen=True, slots=True)
class KnnLabel:
    """Bijective internal target to public fault-code mapping."""

    target_slug: str
    fault_code: str
    is_normal: bool


@dataclass(frozen=True, slots=True)
class KnnPreprocessorState:
    """Complete fitted state required to reconstruct ``StandardScaler``."""

    mean: tuple[float, ...]
    scale: tuple[float, ...]
    variance: tuple[float, ...]
    sample_count: int


@dataclass(frozen=True, slots=True)
class KnnAbstentionPolicy:
    """Frozen thresholds fitted without consulting the test partition."""

    version: int
    distance_threshold: float
    vote_margin_threshold: float
    minimum_class_count: int
    calibration_partition: str
    calibration_partition_sha256: str
    calibration_sample_count: int
    distance_quantile: float
    vote_margin_quantile: float
    sampling: str


@dataclass(frozen=True, slots=True)
class KnnCandidateNeighbor:
    """Internal neighbor reference without a row, feature, or source identifier."""

    neighbor_ref: str
    rank: int
    target_slug: str
    distance: float


@dataclass(frozen=True, slots=True)
class KnnCandidate:
    """Internal candidate class and non-probabilistic neighborhood support."""

    target_slug: str
    support_score: float
    winning_vote_share: float
    vote_margin: float
    nearest_distance: float
    abstention_reason: ModelAbstentionReason | None
    neighbors: tuple[KnnCandidateNeighbor, ...]


@dataclass(frozen=True, slots=True)
class KnnEvaluationSnapshot:
    """Defensive train-state copy for exact offline evaluation."""

    labels: tuple[KnnLabel, ...]
    training_vectors: FloatMatrix
    target_indices: TargetIndexVector
    neighbor_refs: TextVector
    class_counts: tuple[int, ...]


class InMemoryKnnModel:
    """Exact Euclidean k-NN search over train-only standardized vectors."""

    def __init__(
        self,
        *,
        dataset_id: str,
        labels: tuple[KnnLabel, ...],
        normal_target_labels: tuple[str, ...],
        default_top_k: int,
        training_partition_sha256: str,
        abstention_policy: KnnAbstentionPolicy,
        preprocessor: _StandardScaler,
        training_vectors: FloatMatrix,
        target_indices: TargetIndexVector,
        neighbor_refs: TextVector,
    ) -> None:
        _require_sha256(dataset_id, KnnConfigurationError)
        _require_sha256(training_partition_sha256, KnnConfigurationError)
        _validate_top_k(default_top_k, KnnConfigurationError)
        if type(abstention_policy) is not KnnAbstentionPolicy:
            raise KnnConfigurationError("Abstention policy type is incompatible.")
        safe_policy = replace(abstention_policy)
        _validate_abstention_policy(safe_policy, KnnConfigurationError)
        _validate_label_table(labels, normal_target_labels, KnnConfigurationError)
        state = _preprocessor_state(preprocessor, len(training_vectors))
        _validate_training_arrays(
            training_vectors,
            target_indices,
            neighbor_refs,
            label_count=len(labels),
            error_type=KnnConfigurationError,
        )
        _validate_abstention_policy_binding(
            safe_policy,
            training_partition_sha256=training_partition_sha256,
            training_sample_count=len(training_vectors),
            error_type=KnnConfigurationError,
        )

        vectors = np.array(training_vectors, dtype=_VECTOR_DTYPE, copy=True, order="C")
        indices = np.array(
            target_indices,
            dtype=_TARGET_INDEX_DTYPE,
            copy=True,
            order="C",
        )
        refs = np.array(neighbor_refs, dtype=_NEIGHBOR_REF_DTYPE, copy=True, order="C")
        vectors.flags.writeable = False
        indices.flags.writeable = False
        refs.flags.writeable = False

        self._dataset_id = dataset_id
        self._labels = labels
        self._normal_target_labels = normal_target_labels
        self._default_top_k = default_top_k
        self._training_partition_sha256 = training_partition_sha256
        self._abstention_policy = safe_policy
        self._preprocessor = preprocessor
        self._preprocessor_state = state
        self._training_vectors = vectors
        self._target_indices = indices
        self._neighbor_refs = refs
        self._class_counts = tuple(
            int(np.count_nonzero(indices == index)) for index in range(len(labels))
        )
        self._array_identities = _array_identities(vectors, indices, refs)
        self._content_sha256 = _content_sha256(self)
        self._model_id = f"{_MODEL_ID_PREFIX}{self._content_sha256[:32]}"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def content_sha256(self) -> str:
        return self._content_sha256

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def feature_names(self) -> tuple[str, ...]:
        return ANALYSIS_FEATURE_NAMES

    @property
    def labels(self) -> tuple[KnnLabel, ...]:
        return self._labels

    @property
    def normal_target_labels(self) -> tuple[str, ...]:
        return self._normal_target_labels

    @property
    def default_top_k(self) -> int:
        return self._default_top_k

    @property
    def training_partition_sha256(self) -> str:
        return self._training_partition_sha256

    @property
    def abstention_policy(self) -> KnnAbstentionPolicy:
        return replace(self._abstention_policy)

    @property
    def sample_count(self) -> int:
        return len(self._training_vectors)

    @property
    def preprocessor_state(self) -> KnnPreprocessorState:
        return self._preprocessor_state

    def evaluation_snapshot(self) -> KnnEvaluationSnapshot:
        """Return immutable copies without exposing the model's owned arrays."""

        vectors = np.array(self._training_vectors, copy=True, order="C")
        target_indices = np.array(self._target_indices, copy=True, order="C")
        neighbor_refs = np.array(self._neighbor_refs, copy=True, order="C")
        vectors.flags.writeable = False
        target_indices.flags.writeable = False
        neighbor_refs.flags.writeable = False
        return KnnEvaluationSnapshot(
            labels=self._labels,
            training_vectors=vectors,
            target_indices=target_indices,
            neighbor_refs=neighbor_refs,
            class_counts=self._class_counts,
        )

    def predict_candidate(
        self,
        features: Mapping[str, float],
        *,
        top_k: int | None = None,
    ) -> KnnCandidate:
        """Return the stable candidate from an exact in-memory neighbor search."""

        requested_top_k = self._default_top_k if top_k is None else top_k
        _validate_top_k(requested_top_k, KnnInputError)
        values = _ordered_feature_vector(features)
        try:
            transformed = self._preprocessor.transform(values.reshape(1, -1))
        except (TypeError, ValueError):
            raise KnnInputError("Inference preprocessing failed.") from None
        if (
            transformed.shape != (1, ANALYSIS_FEATURE_COUNT)
            or not np.isfinite(transformed).all()
        ):
            raise KnnInputError("Inference preprocessing produced invalid values.")

        neighbors = _nearest_neighbors(
            transformed[0],
            training_vectors=self._training_vectors,
            target_indices=self._target_indices,
            neighbor_refs=self._neighbor_refs,
            labels=self._labels,
            top_k=max(self._default_top_k, requested_top_k),
            error_type=KnnInputError,
        )
        return self._candidate_from_neighbors(neighbors, requested_top_k)

    def candidate_from_ranked_neighbors(
        self,
        positions: Sequence[object],
        distances: Sequence[object],
    ) -> KnnCandidate:
        """Apply the frozen decision policy to an exact offline ranking."""

        if (
            isinstance(positions, (str, bytes))
            or isinstance(distances, (str, bytes))
            or len(positions) != self._default_top_k
            or len(distances) != self._default_top_k
        ):
            raise KnnInputError("Evaluation neighbor ranking is incompatible.")
        normalized_positions: list[int] = []
        normalized_distances: list[float] = []
        for position, distance in zip(positions, distances, strict=True):
            if (
                type(position) is not int
                or position < 0
                or position >= self.sample_count
                or isinstance(distance, bool)
                or not isinstance(distance, (int, float))
                or not isfinite(float(distance))
                or float(distance) < 0.0
            ):
                raise KnnInputError("Evaluation neighbor ranking is incompatible.")
            normalized_positions.append(position)
            normalized_distances.append(float(distance))
        if len(set(normalized_positions)) != len(normalized_positions):
            raise KnnInputError("Evaluation neighbor ranking is incompatible.")
        ordered_keys = tuple(
            (distance, str(self._neighbor_refs[position]))
            for position, distance in zip(
                normalized_positions,
                normalized_distances,
                strict=True,
            )
        )
        if ordered_keys != tuple(sorted(ordered_keys)):
            raise KnnInputError("Evaluation neighbor ranking is incompatible.")
        neighbors = tuple(
            KnnCandidateNeighbor(
                neighbor_ref=str(self._neighbor_refs[position]),
                rank=rank,
                target_slug=self._labels[
                    int(self._target_indices[position])
                ].target_slug,
                distance=distance,
            )
            for rank, (position, distance) in enumerate(
                zip(normalized_positions, normalized_distances, strict=True),
                start=1,
            )
        )
        return self._candidate_from_neighbors(neighbors, self._default_top_k)

    def _candidate_from_neighbors(
        self,
        neighbors: tuple[KnnCandidateNeighbor, ...],
        requested_top_k: int,
    ) -> KnnCandidate:
        decision_neighbors = neighbors[: min(self._default_top_k, len(neighbors))]
        target_slug, winning_vote_share, vote_margin = _select_candidate(
            decision_neighbors
        )
        nearest_distance = decision_neighbors[0].distance
        label_index = next(
            index
            for index, label in enumerate(self._labels)
            if label.target_slug == target_slug
        )
        abstention_reason = _abstention_reason(
            policy=self._abstention_policy,
            nearest_distance=nearest_distance,
            vote_margin=vote_margin,
            class_count=self._class_counts[label_index],
        )
        return KnnCandidate(
            target_slug=target_slug,
            support_score=_support_score(
                winning_vote_share=winning_vote_share,
                nearest_distance=nearest_distance,
                distance_threshold=self._abstention_policy.distance_threshold,
            ),
            winning_vote_share=winning_vote_share,
            vote_margin=vote_margin,
            nearest_distance=nearest_distance,
            abstention_reason=abstention_reason,
            neighbors=neighbors[: min(requested_top_k, len(neighbors))],
        )

    def label_for_target(self, target_slug: str) -> KnnLabel:
        for label in self._labels:
            if label.target_slug == target_slug:
                return label
        raise KnnConfigurationError("Model label table is inconsistent.")


class KnnModelPortAdapter:
    """Translate canonical target slugs to the frozen public ``ModelPort``."""

    def __init__(self, model: InMemoryKnnModel) -> None:
        self._model = model

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        candidate = self._model.predict_candidate(
            features.model_dump(mode="python"),
            top_k=top_k,
        )
        candidate_label = self._model.label_for_target(candidate.target_slug)
        public_neighbors = tuple(
            OpaqueNeighbor(
                neighbor_ref=neighbor.neighbor_ref,
                rank=neighbor.rank,
                fault_code=self._model.label_for_target(
                    neighbor.target_slug
                ).fault_code,
                distance=neighbor.distance,
            )
            for neighbor in candidate.neighbors
        )
        is_normal = candidate_label.is_normal
        is_abstained = candidate.abstention_reason is not None
        return ModelPrediction(
            disposition=(
                ModelDisposition.OUT_OF_DISTRIBUTION
                if is_abstained
                else ModelDisposition.NORMAL
                if is_normal
                else ModelDisposition.FAULT
            ),
            abstention_reason=candidate.abstention_reason,
            diagnosis=(
                None
                if is_abstained
                else Diagnosis(
                    code=candidate_label.fault_code,
                    summary=(
                        "Classe candidata da baseline k-NN; o suporte combina "
                        "votos e distância como heurística, não probabilidade."
                    ),
                )
            ),
            support_score=candidate.support_score,
            model_id=self._model.model_id,
            neighbors=public_neighbors,
            retrieval_key=(
                None if is_abstained or is_normal else candidate.target_slug
            ),
        )


def fit_knn_model(
    training_frame: pd.DataFrame,
    *,
    dataset_id: str,
    training_partition_sha256: str,
    validation_frame: pd.DataFrame | None = None,
    validation_partition_sha256: str | None = None,
    normal_target_labels: Sequence[str] = (),
    default_top_k: int = DEFAULT_TOP_K,
    distance_quantile: float = KNN_DISTANCE_QUANTILE,
    vote_margin_quantile: float = KNN_VOTE_MARGIN_QUANTILE,
    minimum_class_count: int = KNN_MINIMUM_CLASS_COUNT,
) -> InMemoryKnnModel:
    """Fit train-only preprocessing and a test-blind abstention policy."""

    _validate_feature_contract()
    _require_sha256(dataset_id, KnnTrainingError)
    _require_sha256(training_partition_sha256, KnnTrainingError)
    _validate_top_k(default_top_k, KnnTrainingError)
    _validate_policy_configuration(
        distance_quantile=distance_quantile,
        vote_margin_quantile=vote_margin_quantile,
        minimum_class_count=minimum_class_count,
        error_type=KnnTrainingError,
    )
    if (validation_frame is None) != (validation_partition_sha256 is None):
        raise KnnTrainingError(
            "Validation calibration requires both a frame and its SHA-256."
        )
    if validation_partition_sha256 is not None:
        _require_sha256(validation_partition_sha256, KnnTrainingError)
        if validation_partition_sha256 == training_partition_sha256:
            raise KnnTrainingError(
                "Train and validation partition identities must be distinct."
            )
    expected_columns = (*ANALYSIS_FEATURE_NAMES, "y")
    if tuple(training_frame.columns) != expected_columns:
        raise KnnTrainingError("Training columns do not match the canonical order.")
    if training_frame.empty:
        raise KnnTrainingError("Training data cannot be empty.")
    if validation_frame is None and len(training_frame) < 2:
        raise KnnTrainingError(
            "Leave-one-out calibration requires at least two training samples."
        )
    if any(
        str(training_frame[name].dtype).lower() != "float64"
        for name in ANALYSIS_FEATURE_NAMES
    ):
        raise KnnTrainingError("Training feature dtypes are incompatible.")

    try:
        matrix = np.asarray(
            training_frame.loc[:, list(ANALYSIS_FEATURE_NAMES)].to_numpy(copy=True),
            dtype=_VECTOR_DTYPE,
            order="C",
        )
    except (TypeError, ValueError):
        raise KnnTrainingError("Training features are invalid.") from None
    if matrix.shape != (len(training_frame), ANALYSIS_FEATURE_COUNT):
        raise KnnTrainingError("Training feature shape is incompatible.")
    if not np.isfinite(matrix).all():
        raise KnnTrainingError(
            "Training features must be finite; imputation is not configured."
        )

    targets = _training_targets(training_frame["y"])
    normal = _normal_target_labels(normal_target_labels)
    labels, target_indices = _build_label_table(targets, normal)
    neighbor_refs = _build_neighbor_refs(dataset_id, targets)

    preprocessor = cast(
        _StandardScaler,
        StandardScaler(copy=True, with_mean=True, with_std=True),
    )
    try:
        transformed = preprocessor.fit_transform(matrix)
    except (TypeError, ValueError):
        raise KnnTrainingError("Training preprocessing failed.") from None
    transformed = np.asarray(transformed, dtype=_VECTOR_DTYPE, order="C")
    if transformed.shape != matrix.shape or not np.isfinite(transformed).all():
        raise KnnTrainingError("Training preprocessing produced invalid values.")

    if validation_frame is None:
        calibration_vectors = transformed
        calibration_partition = "train_leave_one_out"
        calibration_partition_sha256 = training_partition_sha256
        calibration_neighbor_refs: TextVector | None = neighbor_refs
    else:
        calibration_matrix = _calibration_feature_matrix(validation_frame)
        try:
            calibration_vectors = np.asarray(
                preprocessor.transform(calibration_matrix),
                dtype=_VECTOR_DTYPE,
                order="C",
            )
        except (TypeError, ValueError):
            raise KnnTrainingError("Validation preprocessing failed.") from None
        if (
            calibration_vectors.shape != calibration_matrix.shape
            or not np.isfinite(calibration_vectors).all()
        ):
            raise KnnTrainingError("Validation preprocessing produced invalid values.")
        calibration_partition = "validation"
        calibration_partition_sha256 = cast(str, validation_partition_sha256)
        calibration_neighbor_refs = None

    abstention_policy = _fit_abstention_policy(
        calibration_vectors=calibration_vectors,
        calibration_neighbor_refs=calibration_neighbor_refs,
        calibration_partition=calibration_partition,
        calibration_partition_sha256=calibration_partition_sha256,
        training_vectors=transformed,
        target_indices=target_indices,
        neighbor_refs=neighbor_refs,
        labels=labels,
        top_k=default_top_k,
        distance_quantile=distance_quantile,
        vote_margin_quantile=vote_margin_quantile,
        minimum_class_count=minimum_class_count,
    )

    return InMemoryKnnModel(
        dataset_id=dataset_id,
        labels=labels,
        normal_target_labels=normal,
        default_top_k=default_top_k,
        training_partition_sha256=training_partition_sha256,
        abstention_policy=abstention_policy,
        preprocessor=preprocessor,
        training_vectors=transformed,
        target_indices=target_indices,
        neighbor_refs=neighbor_refs,
    )


def save_knn_model(model: InMemoryKnnModel, output_directory: Path) -> Path:
    """Write a deterministic, non-executable artifact into an ignored directory."""

    destination = _validate_output_destination(output_directory)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-",
                dir=destination.parent,
            )
        )
    except OSError:
        raise KnnArtifactError("Model staging directory is unavailable.") from None

    arrays: tuple[tuple[str, NDArray[np.generic]], ...] = (
        ("training_vectors.npy", model._training_vectors),  # pyright: ignore[reportPrivateUsage]
        ("target_indices.npy", model._target_indices),  # pyright: ignore[reportPrivateUsage]
        ("neighbor_refs.npy", model._neighbor_refs),  # pyright: ignore[reportPrivateUsage]
    )
    try:
        physical_hashes: dict[str, str] = {}
        for filename, array in arrays:
            path = staging / filename
            with path.open("wb") as stream:
                np.save(stream, array, allow_pickle=False)
            physical_hashes[filename] = _hash_regular_file(path)
        manifest = _manifest(model, physical_hashes)
        (staging / "manifest.json").write_bytes(_canonical_json_bytes(manifest))
        if destination.exists():
            if _directories_have_equal_files(staging, destination):
                shutil.rmtree(staging)
                return destination
            raise KnnArtifactError(
                "Model output already exists with different content."
            )
        os.replace(staging, destination)
    except KnnError:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    except (OSError, TypeError, ValueError):
        if staging.exists():
            shutil.rmtree(staging)
        raise KnnArtifactError("Model artifact could not be written.") from None
    return destination


def load_knn_model(
    input_directory: Path,
    *,
    expected_model_id: str | None = None,
) -> InMemoryKnnModel:
    """Load a verified artifact without pickle or executable deserialization."""

    directory = _validate_input_directory(input_directory)
    manifest_path = directory / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        raise KnnArtifactError("Model manifest is unavailable.") from None
    manifest = _decode_json(manifest_bytes)
    if _canonical_json_bytes(manifest) != manifest_bytes:
        raise KnnArtifactError("Model manifest serialization is invalid.")
    _validate_manifest_shape(manifest)

    arrays = _mapping(manifest["training"], "training")["arrays"]
    entries = tuple(_mapping(item, "array") for item in _sequence(arrays, "arrays"))
    for entry in entries:
        filename = _text(entry["filename"], "array.filename")
        if _hash_regular_file(directory / filename) != _require_sha256(
            entry["physical_sha256"], KnnArtifactError
        ):
            raise KnnArtifactError("Model array integrity check failed.")

    vectors = cast(
        FloatMatrix,
        _load_array(directory / "training_vectors.npy", _VECTOR_DTYPE),
    )
    target_indices = cast(
        TargetIndexVector,
        _load_array(directory / "target_indices.npy", _TARGET_INDEX_DTYPE),
    )
    neighbor_refs = cast(
        TextVector,
        _load_array(directory / "neighbor_refs.npy", _NEIGHBOR_REF_DTYPE),
    )
    loaded_arrays = _array_identities(vectors, target_indices, neighbor_refs)
    declared_arrays = {
        _text(entry["filename"], "array.filename"): _require_sha256(
            entry["logical_sha256"], KnnArtifactError
        )
        for entry in entries
    }
    if loaded_arrays != declared_arrays:
        raise KnnArtifactError("Model array content is inconsistent.")

    labels = tuple(
        KnnLabel(
            target_slug=_text(item["target_slug"], "label.target_slug"),
            fault_code=_text(item["fault_code"], "label.fault_code"),
            is_normal=_boolean(item["is_normal"], "label.is_normal"),
        )
        for item in (
            _mapping(value, "label")
            for value in _sequence(manifest["labels"], "labels")
        )
    )
    configuration = _mapping(manifest["configuration"], "configuration")
    abstention_policy = _policy_from_manifest(
        _mapping(manifest["abstention_policy"], "abstention_policy")
    )
    normal = tuple(
        _text(item, "normal_target_labels")
        for item in _sequence(
            configuration["normal_target_labels"], "normal_target_labels"
        )
    )
    state = _state_from_manifest(_mapping(manifest["preprocessor"], "preprocessor"))
    preprocessor = _restore_preprocessor(state)
    model = InMemoryKnnModel(
        dataset_id=_require_sha256(manifest["dataset_id"], KnnArtifactError),
        labels=labels,
        normal_target_labels=normal,
        default_top_k=_integer(configuration["default_top_k"], "default_top_k"),
        training_partition_sha256=_require_sha256(
            _mapping(manifest["training"], "training")["partition_sha256"],
            KnnArtifactError,
        ),
        abstention_policy=abstention_policy,
        preprocessor=preprocessor,
        training_vectors=vectors,
        target_indices=target_indices,
        neighbor_refs=neighbor_refs,
    )
    declared_model_id = _text(manifest["model_id"], "model_id")
    declared_content = _require_sha256(manifest["content_sha256"], KnnArtifactError)
    if (
        model.model_id != declared_model_id
        or model.content_sha256 != declared_content
        or (expected_model_id is not None and model.model_id != expected_model_id)
    ):
        raise KnnArtifactError("Model identity does not match verified content.")
    return model


def _validate_feature_contract() -> None:
    config = load_canonical_pipeline_config()
    if (
        config.feature_names != ANALYSIS_FEATURE_NAMES
        or len(config.feature_names) != ANALYSIS_FEATURE_COUNT
    ):
        raise KnnConfigurationError("Canonical and API feature contracts diverge.")


def _ordered_feature_vector(features: Mapping[str, float]) -> FloatVector:
    if tuple(features) != ANALYSIS_FEATURE_NAMES:
        raise KnnInputError("Inference features do not match the canonical order.")
    values: list[float] = []
    for name in ANALYSIS_FEATURE_NAMES:
        value = features[name]
        if isinstance(value, bool):
            raise KnnInputError("Inference features must be finite numbers.")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise KnnInputError("Inference features must be finite numbers.") from None
        if not isfinite(number):
            raise KnnInputError("Inference features must be finite numbers.")
        values.append(number)
    return np.asarray(values, dtype=_VECTOR_DTYPE)


def _calibration_feature_matrix(frame: pd.DataFrame) -> FloatMatrix:
    expected_columns = (*ANALYSIS_FEATURE_NAMES, "y")
    if tuple(frame.columns) != expected_columns or frame.empty:
        raise KnnTrainingError(
            "Validation columns must match the non-empty canonical partition."
        )
    if any(
        str(frame[name].dtype).lower() != "float64" for name in ANALYSIS_FEATURE_NAMES
    ):
        raise KnnTrainingError("Validation feature dtypes are incompatible.")
    try:
        matrix = np.asarray(
            frame.loc[:, list(ANALYSIS_FEATURE_NAMES)].to_numpy(copy=True),
            dtype=_VECTOR_DTYPE,
            order="C",
        )
    except (TypeError, ValueError):
        raise KnnTrainingError("Validation features are invalid.") from None
    if (
        matrix.shape != (len(frame), ANALYSIS_FEATURE_COUNT)
        or not np.isfinite(matrix).all()
    ):
        raise KnnTrainingError("Validation features must be finite and canonical.")
    return matrix


def _fit_abstention_policy(
    *,
    calibration_vectors: FloatMatrix,
    calibration_neighbor_refs: TextVector | None,
    calibration_partition: str,
    calibration_partition_sha256: str,
    training_vectors: FloatMatrix,
    target_indices: TargetIndexVector,
    neighbor_refs: TextVector,
    labels: tuple[KnnLabel, ...],
    top_k: int,
    distance_quantile: float,
    vote_margin_quantile: float,
    minimum_class_count: int,
) -> KnnAbstentionPolicy:
    if calibration_vectors.ndim != 2 or len(calibration_vectors) <= 0:
        raise KnnTrainingError("Abstention calibration partition is empty.")
    sample_indices = _calibration_indices(len(calibration_vectors))
    nearest_distances: list[float] = []
    vote_margins: list[float] = []
    for index in sample_indices:
        excluded_ref = (
            None
            if calibration_neighbor_refs is None
            else str(calibration_neighbor_refs[index])
        )
        neighbors = _nearest_neighbors(
            calibration_vectors[index],
            training_vectors=training_vectors,
            target_indices=target_indices,
            neighbor_refs=neighbor_refs,
            labels=labels,
            top_k=top_k,
            error_type=KnnTrainingError,
            excluded_neighbor_ref=excluded_ref,
        )
        _target, _share, margin = _select_candidate(neighbors)
        nearest_distances.append(neighbors[0].distance)
        vote_margins.append(margin)

    fitted_margin = float(
        np.quantile(vote_margins, vote_margin_quantile, method="lower")
    )
    policy = KnnAbstentionPolicy(
        version=KNN_ABSTENTION_POLICY_VERSION,
        distance_threshold=float(
            np.quantile(nearest_distances, distance_quantile, method="higher")
        ),
        vote_margin_threshold=(
            float(np.nextafter(1.0, 0.0)) if fitted_margin == 1.0 else fitted_margin
        ),
        minimum_class_count=minimum_class_count,
        calibration_partition=calibration_partition,
        calibration_partition_sha256=calibration_partition_sha256,
        calibration_sample_count=len(sample_indices),
        distance_quantile=distance_quantile,
        vote_margin_quantile=vote_margin_quantile,
        sampling=_CALIBRATION_SAMPLING,
    )
    _validate_abstention_policy(policy, KnnTrainingError)
    return policy


def _calibration_indices(row_count: int) -> tuple[int, ...]:
    sample_count = min(row_count, KNN_CALIBRATION_SAMPLE_LIMIT)
    if sample_count == 1:
        return (0,)
    return tuple(
        index * (row_count - 1) // (sample_count - 1) for index in range(sample_count)
    )


def _nearest_neighbors(
    transformed: FloatVector,
    *,
    training_vectors: FloatMatrix,
    target_indices: TargetIndexVector,
    neighbor_refs: TextVector,
    labels: tuple[KnnLabel, ...],
    top_k: int,
    error_type: type[KnnError],
    excluded_neighbor_ref: str | None = None,
) -> tuple[KnnCandidateNeighbor, ...]:
    with np.errstate(over="ignore", invalid="ignore"):
        differences = training_vectors - transformed
        distances = np.linalg.norm(differences, axis=1)
    if distances.shape != (len(training_vectors),) or not np.isfinite(distances).all():
        raise error_type("Inference distance calculation failed.")
    order = np.lexsort((neighbor_refs, distances))
    selected = [
        int(index)
        for index in order
        if excluded_neighbor_ref is None
        or str(neighbor_refs[index]) != excluded_neighbor_ref
    ][: min(top_k, len(training_vectors))]
    if not selected:
        raise error_type("Model has no eligible calibration neighbors.")
    return tuple(
        KnnCandidateNeighbor(
            neighbor_ref=str(neighbor_refs[index]),
            rank=rank,
            target_slug=labels[int(target_indices[index])].target_slug,
            distance=float(distances[index]),
        )
        for rank, index in enumerate(selected, start=1)
    )


def _training_targets(series: pd.Series) -> tuple[str, ...]:
    targets: list[str] = []
    for value in series:
        if not isinstance(value, str):
            raise KnnTrainingError("Training targets must be non-empty strings.")
        _validate_target_slug(value, KnnTrainingError)
        targets.append(value)
    return tuple(targets)


def _normal_target_labels(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise KnnTrainingError("Normal target labels require an ordered sequence.")
    labels = tuple(value)
    if len(labels) != len(set(labels)):
        raise KnnTrainingError("Normal target labels must be unique.")
    for label in labels:
        _validate_target_slug(label, KnnTrainingError)
    return tuple(sorted(labels))


def _build_label_table(
    targets: tuple[str, ...], normal: tuple[str, ...]
) -> tuple[tuple[KnnLabel, ...], TargetIndexVector]:
    target_labels = tuple(sorted(set(targets)))
    if not set(normal).issubset(target_labels):
        raise KnnTrainingError("Normal target labels must be training classes.")
    labels = tuple(
        KnnLabel(
            target_slug=target,
            fault_code=_fault_code(target),
            is_normal=target in normal,
        )
        for target in target_labels
    )
    _validate_label_table(labels, normal, KnnTrainingError)
    positions = {label.target_slug: index for index, label in enumerate(labels)}
    indices = np.asarray(
        [positions[target] for target in targets],
        dtype=_TARGET_INDEX_DTYPE,
    )
    return labels, indices


def _fault_code(target_slug: str) -> str:
    return f"{_FAULT_CODE_PREFIX}{sha256(target_slug.encode('utf-8')).hexdigest()[:32]}"


def _build_neighbor_refs(dataset_id: str, targets: tuple[str, ...]) -> TextVector:
    refs = tuple(
        f"{_NEIGHBOR_REF_PREFIX}"
        f"{sha256(f'{dataset_id}:{index}:{target}'.encode()).hexdigest()[:32]}"
        for index, target in enumerate(targets)
    )
    if len(refs) != len(set(refs)):
        raise KnnTrainingError("Opaque neighbor reference collision detected.")
    return np.asarray(refs, dtype=_NEIGHBOR_REF_DTYPE)


def _select_candidate(
    neighbors: tuple[KnnCandidateNeighbor, ...],
) -> tuple[str, float, float]:
    if not neighbors:
        raise KnnConfigurationError("Model has no neighbors.")
    counts = Counter(item.target_slug for item in neighbors)
    distances: defaultdict[str, list[float]] = defaultdict(list)
    for neighbor in neighbors:
        distances[neighbor.target_slug].append(neighbor.distance)
    winner = min(
        counts,
        key=lambda target: (
            -counts[target],
            fsum(distances[target]),
            target,
        ),
    )
    winning_count = counts[winner]
    runner_up_count = max(
        (count for target, count in counts.items() if target != winner),
        default=0,
    )
    return (
        winner,
        winning_count / len(neighbors),
        (winning_count - runner_up_count) / len(neighbors),
    )


def _abstention_reason(
    *,
    policy: KnnAbstentionPolicy,
    nearest_distance: float,
    vote_margin: float,
    class_count: int,
) -> ModelAbstentionReason | None:
    if nearest_distance > policy.distance_threshold:
        return ModelAbstentionReason.DISTANCE_OUT_OF_DISTRIBUTION
    if class_count < policy.minimum_class_count:
        return ModelAbstentionReason.RARE_CLASS_SUPPORT
    if vote_margin <= policy.vote_margin_threshold:
        return ModelAbstentionReason.INCONCLUSIVE_VOTE
    return None


def _support_score(
    *,
    winning_vote_share: float,
    nearest_distance: float,
    distance_threshold: float,
) -> float:
    if distance_threshold == 0.0:
        distance_component = 1.0 if nearest_distance == 0.0 else 0.0
    else:
        distance_component = 1.0 / (1.0 + nearest_distance / distance_threshold)
    return round(winning_vote_share * distance_component, 12)


def _validate_label_table(
    labels: tuple[KnnLabel, ...],
    normal: tuple[str, ...],
    error_type: type[KnnError],
) -> None:
    if (
        not labels
        or normal != tuple(sorted(set(normal)))
        or tuple(label.target_slug for label in labels)
        != tuple(sorted(label.target_slug for label in labels))
    ):
        raise error_type("Model label table is invalid.")
    targets: set[str] = set()
    codes: set[str] = set()
    for label in labels:
        _validate_target_slug(label.target_slug, error_type)
        if (
            label.target_slug in targets
            or label.fault_code in codes
            or label.fault_code != _fault_code(label.target_slug)
            or not _FAULT_CODE_PATTERN.fullmatch(label.fault_code)
            or label.is_normal != (label.target_slug in normal)
        ):
            raise error_type("Model label table is not bijective.")
        targets.add(label.target_slug)
        codes.add(label.fault_code)
    if set(normal) - targets:
        raise error_type("Normal target labels are not model classes.")


def _validate_target_slug(value: object, error_type: type[KnnError]) -> str:
    if not isinstance(value, str) or not value or len(value) > _TARGET_MAX_LENGTH:
        raise error_type("Model target label is invalid.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise error_type("Model target label is invalid.") from None
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
    ):
        raise error_type("Model target label is invalid.")
    return value


def _validate_top_k(value: object, error_type: type[KnnError]) -> int:
    if type(value) is not int or not 1 <= value <= MAX_TOP_K:
        raise error_type("top_k must be an integer within the public limit.")
    return value


def _validate_policy_configuration(
    *,
    distance_quantile: object,
    vote_margin_quantile: object,
    minimum_class_count: object,
    error_type: type[KnnError],
) -> None:
    if (
        type(distance_quantile) not in {int, float}
        or not isfinite(float(cast(int | float, distance_quantile)))
        or not 0.0 < float(cast(int | float, distance_quantile)) < 1.0
        or type(vote_margin_quantile) not in {int, float}
        or not isfinite(float(cast(int | float, vote_margin_quantile)))
        or not 0.0 < float(cast(int | float, vote_margin_quantile)) < 1.0
        or type(minimum_class_count) is not int
        or minimum_class_count <= 0
    ):
        raise error_type("Abstention policy configuration is invalid.")


def _validate_abstention_policy(
    policy: KnnAbstentionPolicy,
    error_type: type[KnnError],
) -> None:
    if type(policy) is not KnnAbstentionPolicy:
        raise error_type("Abstention policy type is incompatible.")
    _validate_policy_configuration(
        distance_quantile=policy.distance_quantile,
        vote_margin_quantile=policy.vote_margin_quantile,
        minimum_class_count=policy.minimum_class_count,
        error_type=error_type,
    )
    _require_sha256(policy.calibration_partition_sha256, error_type)
    if (
        type(policy.version) is not int
        or policy.version != KNN_ABSTENTION_POLICY_VERSION
        or type(policy.distance_threshold) is not float
        or not isfinite(policy.distance_threshold)
        or policy.distance_threshold < 0.0
        or type(policy.vote_margin_threshold) is not float
        or not isfinite(policy.vote_margin_threshold)
        or not 0.0 <= policy.vote_margin_threshold <= 1.0
        or type(policy.calibration_partition) is not str
        or policy.calibration_partition not in {"train_leave_one_out", "validation"}
        or type(policy.calibration_sample_count) is not int
        or not 1 <= policy.calibration_sample_count <= KNN_CALIBRATION_SAMPLE_LIMIT
        or type(policy.sampling) is not str
        or policy.sampling != _CALIBRATION_SAMPLING
    ):
        raise error_type("Abstention policy is incompatible.")


def _validate_abstention_policy_binding(
    policy: KnnAbstentionPolicy,
    *,
    training_partition_sha256: str,
    training_sample_count: int,
    error_type: type[KnnError],
) -> None:
    _require_sha256(training_partition_sha256, error_type)
    if type(training_sample_count) is not int or training_sample_count <= 0:
        raise error_type("Abstention policy binding is incompatible.")
    if policy.calibration_partition == "train_leave_one_out":
        expected_sample_count = min(
            training_sample_count,
            KNN_CALIBRATION_SAMPLE_LIMIT,
        )
        if (
            training_sample_count < 2
            or policy.calibration_partition_sha256 != training_partition_sha256
            or policy.calibration_sample_count != expected_sample_count
        ):
            raise error_type("Abstention policy binding is incompatible.")
    elif policy.calibration_partition_sha256 == training_partition_sha256:
        raise error_type("Abstention policy binding is incompatible.")


def _preprocessor_state(
    preprocessor: _StandardScaler, expected_sample_count: int
) -> KnnPreprocessorState:
    try:
        mean = tuple(float(item) for item in preprocessor.mean_)
        scale = tuple(float(item) for item in preprocessor.scale_)
        variance = tuple(float(item) for item in preprocessor.var_)
        feature_count = int(preprocessor.n_features_in_)
        raw_sample_count = preprocessor.n_samples_seen_
        sample_count = int(raw_sample_count)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise KnnConfigurationError("StandardScaler state is incomplete.") from None
    state = KnnPreprocessorState(
        mean=mean,
        scale=scale,
        variance=variance,
        sample_count=sample_count,
    )
    _validate_preprocessor_state(state, expected_sample_count, feature_count)
    return state


def _validate_preprocessor_state(
    state: KnnPreprocessorState,
    expected_sample_count: int,
    feature_count: int = ANALYSIS_FEATURE_COUNT,
) -> None:
    if (
        feature_count != ANALYSIS_FEATURE_COUNT
        or state.sample_count != expected_sample_count
        or expected_sample_count <= 0
        or any(
            len(values) != ANALYSIS_FEATURE_COUNT
            for values in (state.mean, state.scale, state.variance)
        )
        or not all(isfinite(value) for value in state.mean)
        or not all(isfinite(value) and value > 0 for value in state.scale)
        or not all(isfinite(value) and value >= 0 for value in state.variance)
    ):
        raise KnnConfigurationError("StandardScaler state is incompatible.")


def _restore_preprocessor(state: KnnPreprocessorState) -> _StandardScaler:
    _validate_preprocessor_state(state, state.sample_count)
    preprocessor = cast(
        _StandardScaler,
        StandardScaler(copy=True, with_mean=True, with_std=True),
    )
    preprocessor.mean_ = np.asarray(state.mean, dtype=np.float64)
    preprocessor.scale_ = np.asarray(state.scale, dtype=np.float64)
    preprocessor.var_ = np.asarray(state.variance, dtype=np.float64)
    preprocessor.n_features_in_ = ANALYSIS_FEATURE_COUNT
    preprocessor.n_samples_seen_ = state.sample_count
    return preprocessor


def _validate_training_arrays(
    vectors: FloatMatrix,
    target_indices: TargetIndexVector,
    neighbor_refs: TextVector,
    *,
    label_count: int,
    error_type: type[KnnError],
) -> None:
    sample_count = len(vectors)
    if (
        vectors.dtype != _VECTOR_DTYPE
        or vectors.ndim != 2
        or vectors.shape != (sample_count, ANALYSIS_FEATURE_COUNT)
        or sample_count <= 0
        or target_indices.dtype != _TARGET_INDEX_DTYPE
        or target_indices.shape != (sample_count,)
        or neighbor_refs.dtype != _NEIGHBOR_REF_DTYPE
        or neighbor_refs.shape != (sample_count,)
        or not np.isfinite(vectors).all()
        or np.any(target_indices < 0)
        or np.any(target_indices >= label_count)
    ):
        raise error_type("Model training arrays are incompatible.")
    refs = tuple(str(item) for item in neighbor_refs)
    if len(refs) != len(set(refs)) or any(
        not _NEIGHBOR_REF_PATTERN.fullmatch(item) for item in refs
    ):
        raise error_type("Model neighbor references are invalid.")


def _array_identities(
    vectors: FloatMatrix,
    target_indices: TargetIndexVector,
    neighbor_refs: TextVector,
) -> dict[str, str]:
    return {
        "training_vectors.npy": _logical_array_hash(vectors),
        "target_indices.npy": _logical_array_hash(target_indices),
        "neighbor_refs.npy": _logical_array_hash(neighbor_refs),
    }


def _logical_array_hash(array: NDArray[np.generic]) -> str:
    header = _canonical_json_bytes(
        {
            "dtype": array.dtype.str,
            "shape": list(array.shape),
            "order": "C",
        }
    )
    return sha256(header + b"\x00" + array.tobytes(order="C")).hexdigest()


def _compatibility() -> dict[str, object]:
    return {
        "python_requires": ">=3.13,<3.14",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "numpy_version": np.__version__,
        "scikit_learn_version": sklearn_version,
        "api_contract_version": API_CONTRACT_VERSION,
        "canonical_feature_contract_version": CANONICAL_FEATURE_CONTRACT_VERSION,
        "feature_names": list(ANALYSIS_FEATURE_NAMES),
    }


def _configuration(model: InMemoryKnnModel) -> dict[str, object]:
    return {
        "metric": KNN_METRIC,
        "default_top_k": model.default_top_k,
        "max_top_k": MAX_TOP_K,
        "decision_top_k_source": "default_top_k",
        "requested_top_k_role": "evidence_only",
        "distance_order": "distance_ascending",
        "distance_tie_break": _DISTANCE_TIE_BREAK,
        "class_tie_break": _CLASS_TIE_BREAK,
        "support_heuristic": KNN_SUPPORT_HEURISTIC,
        "support_components": ["winning_vote_share", "nearest_neighbor_distance"],
        "support_is_probability": False,
        "distance_abstention_boundary": "greater_than_threshold",
        "vote_margin_abstention_boundary": "less_than_or_equal_to_threshold",
        "rare_class_abstention_boundary": "less_than_minimum_class_count",
        "test_partition_usage": "forbidden",
        "preprocessor": _PREPROCESSOR,
        "preprocessor_fit_partition": "train",
        "imputation": _IMPUTATION,
        "normal_target_labels": list(model.normal_target_labels),
    }


def _preprocessor_payload(state: KnnPreprocessorState) -> dict[str, object]:
    return {
        "mean": list(state.mean),
        "scale": list(state.scale),
        "variance": list(state.variance),
        "sample_count": state.sample_count,
    }


def _abstention_policy_payload(policy: KnnAbstentionPolicy) -> dict[str, object]:
    return {
        "version": policy.version,
        "distance_threshold": policy.distance_threshold,
        "vote_margin_threshold": policy.vote_margin_threshold,
        "minimum_class_count": policy.minimum_class_count,
        "calibration_partition": policy.calibration_partition,
        "calibration_partition_sha256": policy.calibration_partition_sha256,
        "calibration_sample_count": policy.calibration_sample_count,
        "distance_quantile": policy.distance_quantile,
        "vote_margin_quantile": policy.vote_margin_quantile,
        "sampling": policy.sampling,
    }


def _label_payload(labels: tuple[KnnLabel, ...]) -> list[dict[str, object]]:
    return [
        {
            "target_slug": label.target_slug,
            "fault_code": label.fault_code,
            "is_normal": label.is_normal,
        }
        for label in labels
    ]


def _identity(model: InMemoryKnnModel) -> dict[str, object]:
    return {
        "artifact_schema_version": KNN_ARTIFACT_SCHEMA_VERSION,
        "model_family": _MODEL_FAMILY,
        "model_version": KNN_MODEL_VERSION,
        "dataset_id": model.dataset_id,
        "compatibility": _compatibility(),
        "configuration": _configuration(model),
        "abstention_policy": _abstention_policy_payload(model.abstention_policy),
        "preprocessor": _preprocessor_payload(model.preprocessor_state),
        "labels": _label_payload(model.labels),
        "training": {
            "partition_sha256": model.training_partition_sha256,
            "sample_count": model.sample_count,
            "feature_count": ANALYSIS_FEATURE_COUNT,
            "array_logical_sha256": dict(
                model._array_identities  # pyright: ignore[reportPrivateUsage]
            ),
        },
    }


def _content_sha256(model: InMemoryKnnModel) -> str:
    return sha256(_canonical_json_bytes(_identity(model))).hexdigest()


def _manifest(
    model: InMemoryKnnModel,
    physical_hashes: Mapping[str, str],
) -> dict[str, object]:
    array_entries = [
        {
            "filename": filename,
            "dtype": {
                "training_vectors.npy": _VECTOR_DTYPE.str,
                "target_indices.npy": _TARGET_INDEX_DTYPE.str,
                "neighbor_refs.npy": _NEIGHBOR_REF_DTYPE.str,
            }[filename],
            "shape": {
                "training_vectors.npy": [
                    model.sample_count,
                    ANALYSIS_FEATURE_COUNT,
                ],
                "target_indices.npy": [model.sample_count],
                "neighbor_refs.npy": [model.sample_count],
            }[filename],
            "logical_sha256": model._array_identities[  # pyright: ignore[reportPrivateUsage]
                filename
            ],
            "physical_sha256": physical_hashes[filename],
        }
        for filename in KNN_ARTIFACT_FILENAMES[1:]
    ]
    return {
        "artifact_schema_version": KNN_ARTIFACT_SCHEMA_VERSION,
        "model_family": _MODEL_FAMILY,
        "model_version": KNN_MODEL_VERSION,
        "model_id": model.model_id,
        "content_sha256": model.content_sha256,
        "dataset_id": model.dataset_id,
        "compatibility": _compatibility(),
        "configuration": _configuration(model),
        "abstention_policy": _abstention_policy_payload(model.abstention_policy),
        "preprocessor": _preprocessor_payload(model.preprocessor_state),
        "labels": _label_payload(model.labels),
        "training": {
            "partition_sha256": model.training_partition_sha256,
            "sample_count": model.sample_count,
            "feature_count": ANALYSIS_FEATURE_COUNT,
            "arrays": array_entries,
        },
    }


def _state_from_manifest(value: Mapping[str, object]) -> KnnPreprocessorState:
    _exact_keys(value, ("mean", "scale", "variance", "sample_count"))
    state = KnnPreprocessorState(
        mean=tuple(
            _finite_float(item, "preprocessor.mean")
            for item in _sequence(value["mean"], "mean")
        ),
        scale=tuple(
            _finite_float(item, "preprocessor.scale")
            for item in _sequence(value["scale"], "scale")
        ),
        variance=tuple(
            _finite_float(item, "preprocessor.variance")
            for item in _sequence(value["variance"], "variance")
        ),
        sample_count=_integer(value["sample_count"], "sample_count"),
    )
    try:
        _validate_preprocessor_state(state, state.sample_count)
    except KnnConfigurationError:
        raise KnnArtifactError("Model preprocessor state is incompatible.") from None
    return state


def _policy_from_manifest(value: Mapping[str, object]) -> KnnAbstentionPolicy:
    _exact_keys(
        value,
        (
            "version",
            "distance_threshold",
            "vote_margin_threshold",
            "minimum_class_count",
            "calibration_partition",
            "calibration_partition_sha256",
            "calibration_sample_count",
            "distance_quantile",
            "vote_margin_quantile",
            "sampling",
        ),
    )
    policy = KnnAbstentionPolicy(
        version=_integer(value["version"], "abstention_policy.version"),
        distance_threshold=_finite_float(
            value["distance_threshold"], "abstention_policy.distance_threshold"
        ),
        vote_margin_threshold=_finite_float(
            value["vote_margin_threshold"],
            "abstention_policy.vote_margin_threshold",
        ),
        minimum_class_count=_integer(
            value["minimum_class_count"], "abstention_policy.minimum_class_count"
        ),
        calibration_partition=_text(
            value["calibration_partition"],
            "abstention_policy.calibration_partition",
        ),
        calibration_partition_sha256=_require_sha256(
            value["calibration_partition_sha256"], KnnArtifactError
        ),
        calibration_sample_count=_integer(
            value["calibration_sample_count"],
            "abstention_policy.calibration_sample_count",
        ),
        distance_quantile=_finite_float(
            value["distance_quantile"], "abstention_policy.distance_quantile"
        ),
        vote_margin_quantile=_finite_float(
            value["vote_margin_quantile"],
            "abstention_policy.vote_margin_quantile",
        ),
        sampling=_text(value["sampling"], "abstention_policy.sampling"),
    )
    _validate_abstention_policy(policy, KnnArtifactError)
    return policy


def _validate_manifest_shape(manifest: Mapping[str, object]) -> None:
    _exact_keys(
        manifest,
        (
            "artifact_schema_version",
            "model_family",
            "model_version",
            "model_id",
            "content_sha256",
            "dataset_id",
            "compatibility",
            "configuration",
            "abstention_policy",
            "preprocessor",
            "labels",
            "training",
        ),
    )
    if (
        manifest["artifact_schema_version"] != KNN_ARTIFACT_SCHEMA_VERSION
        or manifest["model_family"] != _MODEL_FAMILY
        or manifest["model_version"] != KNN_MODEL_VERSION
    ):
        raise KnnArtifactError("Model artifact version is unsupported.")
    model_id = _text(manifest["model_id"], "model_id")
    if not re.fullmatch(r"model_[a-z0-9_.-]{3,64}", model_id):
        raise KnnArtifactError("Model identifier is invalid.")
    _require_sha256(manifest["content_sha256"], KnnArtifactError)
    _require_sha256(manifest["dataset_id"], KnnArtifactError)

    compatibility = _mapping(manifest["compatibility"], "compatibility")
    expected_compatibility = _compatibility()
    _exact_keys(compatibility, tuple(expected_compatibility))
    if compatibility != expected_compatibility:
        raise KnnArtifactError("Model artifact runtime is incompatible.")

    configuration = _mapping(manifest["configuration"], "configuration")
    _exact_keys(
        configuration,
        (
            "metric",
            "default_top_k",
            "max_top_k",
            "decision_top_k_source",
            "requested_top_k_role",
            "distance_order",
            "distance_tie_break",
            "class_tie_break",
            "support_heuristic",
            "support_components",
            "support_is_probability",
            "distance_abstention_boundary",
            "vote_margin_abstention_boundary",
            "rare_class_abstention_boundary",
            "test_partition_usage",
            "preprocessor",
            "preprocessor_fit_partition",
            "imputation",
            "normal_target_labels",
        ),
    )
    if (
        configuration["metric"] != KNN_METRIC
        or configuration["max_top_k"] != MAX_TOP_K
        or configuration["decision_top_k_source"] != "default_top_k"
        or configuration["requested_top_k_role"] != "evidence_only"
        or configuration["distance_order"] != "distance_ascending"
        or configuration["distance_tie_break"] != _DISTANCE_TIE_BREAK
        or configuration["class_tie_break"] != _CLASS_TIE_BREAK
        or configuration["support_heuristic"] != KNN_SUPPORT_HEURISTIC
        or configuration["support_components"]
        != ["winning_vote_share", "nearest_neighbor_distance"]
        or configuration["support_is_probability"] is not False
        or configuration["distance_abstention_boundary"] != "greater_than_threshold"
        or configuration["vote_margin_abstention_boundary"]
        != "less_than_or_equal_to_threshold"
        or configuration["rare_class_abstention_boundary"]
        != "less_than_minimum_class_count"
        or configuration["test_partition_usage"] != "forbidden"
        or configuration["preprocessor"] != _PREPROCESSOR
        or configuration["preprocessor_fit_partition"] != "train"
        or configuration["imputation"] != _IMPUTATION
    ):
        raise KnnArtifactError("Model configuration is incompatible.")
    _validate_top_k(configuration["default_top_k"], KnnArtifactError)
    policy = _policy_from_manifest(
        _mapping(manifest["abstention_policy"], "abstention_policy")
    )

    labels = _sequence(manifest["labels"], "labels")
    if not labels:
        raise KnnArtifactError("Model label table is incomplete.")
    for raw_label in labels:
        label = _mapping(raw_label, "label")
        _exact_keys(label, ("target_slug", "fault_code", "is_normal"))

    training = _mapping(manifest["training"], "training")
    _exact_keys(
        training,
        ("partition_sha256", "sample_count", "feature_count", "arrays"),
    )
    training_partition_sha256 = _require_sha256(
        training["partition_sha256"], KnnArtifactError
    )
    sample_count = _integer(training["sample_count"], "sample_count")
    if sample_count <= 0 or training["feature_count"] != ANALYSIS_FEATURE_COUNT:
        raise KnnArtifactError("Model training metadata is invalid.")
    _validate_abstention_policy_binding(
        policy,
        training_partition_sha256=training_partition_sha256,
        training_sample_count=sample_count,
        error_type=KnnArtifactError,
    )
    arrays = tuple(
        _mapping(item, "array") for item in _sequence(training["arrays"], "arrays")
    )
    if len(arrays) != 3:
        raise KnnArtifactError("Model array registry is incomplete.")
    expected = {
        "training_vectors.npy": (
            _VECTOR_DTYPE.str,
            [sample_count, ANALYSIS_FEATURE_COUNT],
        ),
        "target_indices.npy": (_TARGET_INDEX_DTYPE.str, [sample_count]),
        "neighbor_refs.npy": (_NEIGHBOR_REF_DTYPE.str, [sample_count]),
    }
    filenames: list[str] = []
    for entry in arrays:
        _exact_keys(
            entry,
            (
                "filename",
                "dtype",
                "shape",
                "logical_sha256",
                "physical_sha256",
            ),
        )
        filename = _text(entry["filename"], "array.filename")
        filenames.append(filename)
        if (
            filename not in expected
            or (entry["dtype"], entry["shape"]) != expected[filename]
        ):
            raise KnnArtifactError("Model array metadata is invalid.")
        _require_sha256(entry["logical_sha256"], KnnArtifactError)
        _require_sha256(entry["physical_sha256"], KnnArtifactError)
    if tuple(filenames) != KNN_ARTIFACT_FILENAMES[1:]:
        raise KnnArtifactError("Model array registry order is invalid.")


def _validate_output_destination(output_directory: Path) -> Path:
    if output_directory.name in {"", ".", ".."} or ".." in output_directory.parts:
        raise KnnArtifactError("Model output destination is unsafe.")
    try:
        destination = Path(os.path.abspath(os.fspath(output_directory)))
    except (OSError, TypeError, ValueError):
        raise KnnArtifactError("Model output destination is invalid.") from None
    if destination.parent == destination:
        raise KnnArtifactError("Model output destination is unsafe.")
    _reject_linked_path_components(destination)
    worktree = _find_enclosing_git_worktree(destination)
    if worktree is None:
        return destination
    try:
        relative = destination.relative_to(worktree).as_posix()
    except ValueError:
        raise KnnArtifactError(
            "Model output destination escapes its worktree."
        ) from None
    result = _run_git(worktree, "check-ignore", "--quiet", "--", relative)
    if result.returncode == 1:
        raise KnnArtifactError("Model output destination is not ignored by Git.")
    if result.returncode != 0:
        raise KnnArtifactError("Model output Git-ignore status is unavailable.")
    return destination


def _validate_input_directory(input_directory: Path) -> Path:
    try:
        directory = Path(os.path.abspath(os.fspath(input_directory)))
    except (OSError, TypeError, ValueError):
        raise KnnArtifactError("Model input directory is invalid.") from None
    _reject_linked_path_components(directory)
    try:
        if not directory.is_dir() or directory.is_symlink():
            raise KnnArtifactError("Model input directory is unavailable.")
        entries = tuple(directory.iterdir())
    except OSError:
        raise KnnArtifactError("Model input directory is unavailable.") from None
    if tuple(sorted(item.name for item in entries)) != tuple(
        sorted(KNN_ARTIFACT_FILENAMES)
    ) or any(item.is_symlink() or not item.is_file() for item in entries):
        raise KnnArtifactError("Model artifact file set is invalid.")
    return directory


def _reject_linked_path_components(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            break
        except OSError:
            raise KnnArtifactError("Model path metadata is unavailable.") from None
        try:
            is_junction = candidate.is_junction()
        except OSError:
            raise KnnArtifactError("Model path metadata is unavailable.") from None
        if stat.S_ISLNK(metadata.st_mode) or is_junction:
            raise KnnArtifactError("Model path cannot contain a link or junction.")


def _find_enclosing_git_worktree(destination: Path) -> Path | None:
    probe = destination
    while True:
        try:
            metadata = os.lstat(probe)
        except FileNotFoundError:
            if probe.parent == probe:
                return None
            probe = probe.parent
            continue
        except OSError:
            raise KnnArtifactError("Model path metadata is unavailable.") from None
        if not stat.S_ISDIR(metadata.st_mode):
            probe = probe.parent
        break
    for candidate in (probe, *probe.parents):
        marker = candidate / ".git"
        try:
            marker_metadata = os.lstat(marker)
        except FileNotFoundError:
            continue
        except OSError:
            raise KnnArtifactError("Git worktree metadata is unavailable.") from None
        try:
            marker_is_junction = marker.is_junction()
        except OSError:
            raise KnnArtifactError("Git worktree metadata is unavailable.") from None
        if stat.S_ISLNK(marker_metadata.st_mode) or marker_is_junction:
            raise KnnArtifactError("Git worktree metadata is unsafe.")
        result = _run_git(candidate, "rev-parse", "--show-toplevel")
        if result.returncode != 0:
            raise KnnArtifactError("Git worktree identity is unavailable.")
        try:
            reported = Path(result.stdout.strip()).resolve(strict=True)
            expected = candidate.resolve(strict=True)
        except (OSError, ValueError):
            raise KnnArtifactError("Git worktree identity is invalid.") from None
        if reported != expected:
            raise KnnArtifactError("Git worktree identity is inconsistent.")
        return expected
    return None


def _run_git(worktree: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise KnnArtifactError("Git model-output validation is unavailable.")
    try:
        return subprocess.run(  # noqa: S603 - executable and arguments are bounded.
            (git_executable, "-C", os.fspath(worktree), *arguments),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise KnnArtifactError("Git model-output validation is unavailable.") from None


def _directories_have_equal_files(left: Path, right: Path) -> bool:
    try:
        if not right.is_dir() or right.is_symlink():
            return False
        left_names = tuple(sorted(item.name for item in left.iterdir()))
        right_names = tuple(sorted(item.name for item in right.iterdir()))
        return left_names == right_names == tuple(
            sorted(KNN_ARTIFACT_FILENAMES)
        ) and all(
            not (left / name).is_symlink()
            and not (right / name).is_symlink()
            and (left / name).is_file()
            and (right / name).is_file()
            and (left / name).read_bytes() == (right / name).read_bytes()
            for name in left_names
        )
    except OSError:
        raise KnnArtifactError("Existing model output could not be verified.") from None


def _load_array(path: Path, dtype: np.dtype[np.generic]) -> NDArray[np.generic]:
    try:
        with path.open("rb") as stream:
            value: object = np.load(stream, allow_pickle=False)
    except (OSError, TypeError, ValueError):
        raise KnnArtifactError("Model array could not be loaded safely.") from None
    if not isinstance(value, np.ndarray):
        raise KnnArtifactError("Model array dtype is incompatible.")
    array = cast(NDArray[np.generic], value)
    if array.dtype != dtype:
        raise KnnArtifactError("Model array dtype is incompatible.")
    return array


def _hash_regular_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise KnnArtifactError("Model artifact file is unavailable.")
    digest = sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise KnnArtifactError("Model artifact file is unavailable.") from None
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise KnnArtifactError("Model manifest value is invalid.") from None


def _decode_json(value: bytes) -> dict[str, object]:
    try:
        payload = json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, KnnArtifactError):
        raise KnnArtifactError("Model manifest JSON is invalid.") from None
    if not isinstance(payload, dict):
        raise KnnArtifactError("Model manifest root is invalid.")
    return cast(dict[str, object], payload)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise KnnArtifactError("Model manifest contains duplicate keys.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise KnnArtifactError("Model manifest contains a non-finite number.")


def _exact_keys(value: Mapping[str, object], expected: Sequence[str]) -> None:
    if tuple(value) != tuple(expected):
        raise KnnArtifactError("Model manifest fields are invalid.")


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    mapping = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in mapping):
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return cast(dict[str, object], mapping)


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return cast(list[object], value)


def _text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return value


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return value


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return value


def _finite_float(value: object, context: str) -> float:
    if type(value) not in {int, float}:
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    result = float(cast(int | float, value))
    if not isfinite(result):
        raise KnnArtifactError(f"Model manifest {context} is invalid.")
    return result


def _require_sha256(value: object, error_type: type[KnnError]) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise error_type("Model SHA-256 identity is invalid.")
    return value
