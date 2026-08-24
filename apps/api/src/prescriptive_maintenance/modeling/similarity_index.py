"""Safe, versioned similarity-index artifact and exact in-memory retrieval."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Final, NoReturn, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_COUNT,
    MAX_TOP_K,
)
from prescriptive_maintenance.data import CANONICAL_FEATURE_CONTRACT_VERSION
from prescriptive_maintenance.modeling.knn import (
    KNN_ARTIFACT_FILENAMES,
    KNN_ARTIFACT_SCHEMA_VERSION,
    KNN_METRIC,
    KNN_MODEL_VERSION,
    KnnArtifactError,
    load_knn_model,
)

SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION: Final = 1
SIMILARITY_INDEX_RECORD_SCHEMA_VERSION: Final = 1
SIMILARITY_PREPROCESSOR_SCHEMA_VERSION: Final = 1
SIMILARITY_PREPROCESSOR_VERSION: Final = "knn-standard-scaler.v1"
SIMILARITY_INDEX_VERSION: Final = "exact-flat.v1"
SIMILARITY_CONFIGURATION_VERSION: Final = "euclidean-opaque-ranking.v1"
SIMILARITY_INDEX_DIMENSION: Final = ANALYSIS_FEATURE_COUNT
SIMILARITY_INDEX_METRIC: Final = KNN_METRIC
SIMILARITY_VECTOR_DTYPE: Final = np.dtype("<f4")
SIMILARITY_INDEX_FILENAMES: Final[tuple[str, ...]] = (
    "manifest.json",
    "preprocessor.json",
    "records.json",
    "vectors.npy",
)

_INDEX_ID_PREFIX: Final = "similarity_index_v1_"
_INDEX_ID_PATTERN: Final = re.compile(r"similarity_index_v1_[0-9a-f]{32}")
_MODEL_ID_PATTERN: Final = re.compile(r"model_[a-z0-9_.-]{3,64}")
_OPAQUE_ID_PATTERN: Final = re.compile(r"neighbor_[a-z0-9_]{3,64}")
_FAULT_CODE_PATTERN: Final = re.compile(r"fault_[a-z0-9_]{3,200}")
_VERSION_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{2,79}")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_DISTANCE_ORDER: Final = "distance_ascending"
_DISTANCE_TIE_BREAK: Final = "opaque_id_ascending"
_VECTOR_MEDIA_TYPE: Final = "application/x-npy"
_JSON_MEDIA_TYPE: Final = "application/json"
_MAX_MANIFEST_BYTES: Final = 1_048_576
_MAX_PREPROCESSOR_BYTES: Final = 1_048_576
_MAX_RECORDS_BYTES: Final = 64 * 1_048_576
_MAX_VECTOR_BYTES: Final = 512 * 1_048_576
_MAX_SOURCE_MODEL_FILE_BYTES: Final = 512 * 1_048_576
_FILE_READ_BLOCK_BYTES: Final = 1_048_576

type FloatMatrix = NDArray[np.float32]
type FloatVector = NDArray[np.float32]


class SimilarityIndexError(Exception):
    """Base class for sanitized similarity-index failures."""


class SimilarityIndexArtifactError(SimilarityIndexError):
    """Raised when an index artifact is unsafe, incomplete, or altered."""


class SimilarityIndexCompatibilityError(SimilarityIndexError):
    """Raised when a caller, artifact, or repository version is incompatible."""


class SimilarityIndexQueryError(SimilarityIndexError):
    """Raised when a similarity query violates the frozen contract."""


class SimilarityIndexRepositoryError(SimilarityIndexError):
    """Raised when a similarity repository cannot serve the frozen contract."""


@dataclass(frozen=True, slots=True)
class SimilarityIndexCompatibility:
    """Complete compatibility selector required before loading or querying."""

    dataset_id: str
    schema_id: str
    feature_contract_version: int = CANONICAL_FEATURE_CONTRACT_VERSION
    preprocessor_version: str = SIMILARITY_PREPROCESSOR_VERSION
    index_version: str = SIMILARITY_INDEX_VERSION
    configuration_version: str = SIMILARITY_CONFIGURATION_VERSION
    dimension: int = SIMILARITY_INDEX_DIMENSION
    metric: str = SIMILARITY_INDEX_METRIC

    def __post_init__(self) -> None:
        if (
            _SHA256_PATTERN.fullmatch(self.dataset_id) is None
            or _SHA256_PATTERN.fullmatch(self.schema_id) is None
            or type(self.feature_contract_version) is not int
            or self.feature_contract_version < 1
            or _VERSION_PATTERN.fullmatch(self.preprocessor_version) is None
            or _VERSION_PATTERN.fullmatch(self.index_version) is None
            or _VERSION_PATTERN.fullmatch(self.configuration_version) is None
            or type(self.dimension) is not int
            or self.dimension < 1
            or _VERSION_PATTERN.fullmatch(self.metric) is None
        ):
            raise ValueError("Similarity index compatibility is invalid.")


@dataclass(frozen=True, slots=True)
class SimilarityIndexSelector:
    """Immutable identity and compatibility used by every repository query."""

    index_id: str
    compatibility: SimilarityIndexCompatibility
    model_id: str | None = None

    def __post_init__(self) -> None:
        if _INDEX_ID_PATTERN.fullmatch(self.index_id) is None or (
            self.model_id is not None
            and _MODEL_ID_PATTERN.fullmatch(self.model_id) is None
        ):
            raise ValueError("Similarity index identifier is invalid.")


@dataclass(frozen=True, slots=True)
class SimilarityArtifactFile:
    """Physical and logical identity of one fixed artifact file."""

    filename: str
    media_type: str
    physical_sha256: str
    logical_sha256: str

    def __post_init__(self) -> None:
        if (
            self.filename not in SIMILARITY_INDEX_FILENAMES[1:]
            or self.media_type
            != (
                _VECTOR_MEDIA_TYPE
                if self.filename == "vectors.npy"
                else _JSON_MEDIA_TYPE
            )
            or _SHA256_PATTERN.fullmatch(self.physical_sha256) is None
            or _SHA256_PATTERN.fullmatch(self.logical_sha256) is None
        ):
            raise ValueError("Similarity artifact file identity is invalid.")


@dataclass(frozen=True, slots=True)
class SimilarityPreprocessorState:
    """Safe JSON representation of the train-only StandardScaler state."""

    mean: tuple[float, ...]
    scale: tuple[float, ...]
    variance: tuple[float, ...]
    sample_count: int

    def __post_init__(self) -> None:
        vectors = (self.mean, self.scale, self.variance)
        if (
            any(len(values) != SIMILARITY_INDEX_DIMENSION for values in vectors)
            or any(not isfinite(value) for values in vectors for value in values)
            or any(value <= 0.0 for value in self.scale)
            or any(value < 0.0 for value in self.variance)
            or type(self.sample_count) is not int
            or self.sample_count < 1
        ):
            raise ValueError("Similarity preprocessor state is invalid.")


@dataclass(frozen=True, slots=True)
class SimilarityIndexRecord:
    """Publicly safe metadata paired with one private vector position."""

    opaque_id: str
    fault_code: str

    def __post_init__(self) -> None:
        if (
            _OPAQUE_ID_PATTERN.fullmatch(self.opaque_id) is None
            or _FAULT_CODE_PATTERN.fullmatch(self.fault_code) is None
        ):
            raise ValueError("Similarity index record is invalid.")


@dataclass(frozen=True, slots=True)
class SimilarityIndexManifest:
    """Validated manifest whose content identity anchors every adapter."""

    artifact_schema_version: int
    selector: SimilarityIndexSelector
    content_sha256: str
    source_model_id: str
    source_model_content_sha256: str
    record_count: int
    vector_dtype: str
    distance_order: str
    distance_tie_break: str
    files: tuple[SimilarityArtifactFile, ...]

    def __post_init__(self) -> None:
        if (
            self.artifact_schema_version != SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
            or re.fullmatch(r"model_[a-z0-9_.-]{3,64}", self.source_model_id) is None
            or _SHA256_PATTERN.fullmatch(self.source_model_content_sha256) is None
            or self.selector.model_id != self.source_model_id
            or type(self.record_count) is not int
            or self.record_count < 1
            or self.vector_dtype != SIMILARITY_VECTOR_DTYPE.str
            or self.distance_order != _DISTANCE_ORDER
            or self.distance_tie_break != _DISTANCE_TIE_BREAK
            or tuple(item.filename for item in self.files)
            != SIMILARITY_INDEX_FILENAMES[1:]
        ):
            raise ValueError("Similarity index manifest is invalid.")

    @property
    def manifest_sha256(self) -> str:
        """Return the physical hash of the canonical manifest bytes."""

        return sha256(_canonical_json_bytes(_manifest_payload(self))).hexdigest()


@dataclass(frozen=True, slots=True)
class SimilarityQuery:
    """One exact-search request over raw canonical feature values."""

    selector: SimilarityIndexSelector
    features: tuple[float, ...]
    top_k: int
    fault_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SimilarityNeighbor:
    """Opaque neighbor result shared by memory and PostgreSQL adapters."""

    opaque_id: str
    rank: int
    fault_code: str
    distance: float

    def __post_init__(self) -> None:
        if (
            _OPAQUE_ID_PATTERN.fullmatch(self.opaque_id) is None
            or type(self.rank) is not int
            or self.rank < 1
            or _FAULT_CODE_PATTERN.fullmatch(self.fault_code) is None
            or not isfinite(self.distance)
            or self.distance < 0.0
        ):
            raise ValueError("Similarity neighbor is invalid.")


class SimilarityIndexPort(Protocol):
    """Exact neighbor retrieval with frozen identity and filtering semantics."""

    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]: ...


class LoadedSimilarityIndex:
    """Verified immutable index with a safe, non-executable preprocessor."""

    def __init__(
        self,
        *,
        manifest: SimilarityIndexManifest,
        preprocessor: SimilarityPreprocessorState,
        records: tuple[SimilarityIndexRecord, ...],
        vectors: FloatMatrix,
    ) -> None:
        if (
            len(records) != manifest.record_count
            or preprocessor.sample_count != manifest.record_count
            or vectors.shape
            != (manifest.record_count, manifest.selector.compatibility.dimension)
            or vectors.dtype != SIMILARITY_VECTOR_DTYPE
            or not np.isfinite(vectors).all()
            or tuple(record.opaque_id for record in records)
            != tuple(sorted(record.opaque_id for record in records))
            or len({record.opaque_id for record in records}) != len(records)
        ):
            raise SimilarityIndexArtifactError(
                "Similarity index content is inconsistent."
            )
        immutable_vectors = np.array(
            vectors,
            dtype=SIMILARITY_VECTOR_DTYPE,
            order="C",
            copy=True,
        )
        immutable_vectors.flags.writeable = False
        self._manifest = manifest
        self._preprocessor = preprocessor
        self._records = records
        self._vectors = immutable_vectors
        self._record_by_id = {record.opaque_id: record for record in records}

    @property
    def manifest(self) -> SimilarityIndexManifest:
        return self._manifest

    @property
    def selector(self) -> SimilarityIndexSelector:
        return self._manifest.selector

    @property
    def preprocessor(self) -> SimilarityPreprocessorState:
        return self._preprocessor

    @property
    def records(self) -> tuple[SimilarityIndexRecord, ...]:
        return self._records

    def record_for(self, opaque_id: str) -> SimilarityIndexRecord:
        try:
            return self._record_by_id[opaque_id]
        except KeyError:
            raise SimilarityIndexRepositoryError(
                "Similarity repository returned an unknown opaque identifier."
            ) from None

    def vectors_copy(self) -> FloatMatrix:
        """Return a defensive copy for a persistence adapter."""

        return np.array(self._vectors, copy=True, order="C")

    def transformed_query(self, query: SimilarityQuery) -> FloatVector:
        """Validate identity/input and apply only the verified JSON state."""

        _validate_query(query, self.selector)
        raw = np.asarray(query.features, dtype=np.dtype("<f8"))
        mean = np.asarray(self.preprocessor.mean, dtype=np.dtype("<f8"))
        scale = np.asarray(self.preprocessor.scale, dtype=np.dtype("<f8"))
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            transformed = (raw - mean) / scale
        result = np.asarray(transformed, dtype=SIMILARITY_VECTOR_DTYPE, order="C")
        if (
            result.shape != (self.selector.compatibility.dimension,)
            or not np.isfinite(result).all()
        ):
            raise SimilarityIndexQueryError(
                "Similarity preprocessing produced invalid values."
            )
        result.flags.writeable = False
        return result


class InMemorySimilarityIndexAdapter:
    """Exact flat scan with deterministic distance and opaque-ID ordering."""

    def __init__(self, index: LoadedSimilarityIndex) -> None:
        self._index = index

    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]:
        transformed = self._index.transformed_query(query)
        allowed = frozenset(query.fault_codes)
        vectors = self._index._vectors  # pyright: ignore[reportPrivateUsage]
        with np.errstate(over="ignore", invalid="ignore"):
            differences = vectors - transformed
            distances = cast(
                FloatVector,
                np.sqrt(np.sum(differences * differences, axis=1)),
            )
        if (
            distances.shape != (self._index.manifest.record_count,)
            or not np.isfinite(distances).all()
        ):
            raise SimilarityIndexQueryError("Similarity distance calculation failed.")
        candidates = [
            (float(distances[position]), record.opaque_id, record.fault_code)
            for position, record in enumerate(self._index.records)
            if not allowed or record.fault_code in allowed
        ]
        candidates.sort(key=lambda item: (item[0], item[1]))
        return tuple(
            SimilarityNeighbor(
                opaque_id=opaque_id,
                rank=rank,
                fault_code=fault_code,
                distance=distance,
            )
            for rank, (distance, opaque_id, fault_code) in enumerate(
                candidates[: query.top_k],
                start=1,
            )
        )


def save_similarity_index_from_knn_artifact(
    knn_artifact_directory: Path,
    *,
    schema_id: str,
    output_directory: Path,
) -> Path:
    """Build an atomic safe index from one fully verified k-NN v3 artifact."""

    _require_sha256(schema_id, SimilarityIndexArtifactError)
    source_directory = _validate_source_model_directory(knn_artifact_directory)
    source_snapshot = _read_source_model_snapshot(source_directory)
    try:
        with tempfile.TemporaryDirectory(prefix="similarity-model-snapshot-") as raw:
            snapshot_directory = Path(raw)
            for filename in KNN_ARTIFACT_FILENAMES:
                (snapshot_directory / filename).write_bytes(source_snapshot[filename])
            model = load_knn_model(snapshot_directory)
    except (KnnArtifactError, OSError):
        raise SimilarityIndexArtifactError(
            "Source model artifact is incompatible."
        ) from None
    vectors = _load_source_array_bytes(
        source_snapshot["training_vectors.npy"], np.dtype("<f8")
    )
    target_indices = _load_source_array_bytes(
        source_snapshot["target_indices.npy"], np.dtype("<i4")
    )
    opaque_ids = _load_source_array_bytes(
        source_snapshot["neighbor_refs.npy"], np.dtype("<U41")
    )
    if (
        vectors.shape != (model.sample_count, SIMILARITY_INDEX_DIMENSION)
        or target_indices.shape != (model.sample_count,)
        or opaque_ids.shape != (model.sample_count,)
        or not np.isfinite(vectors).all()
    ):
        raise SimilarityIndexArtifactError("Source model arrays are incompatible.")
    positions = sorted(
        range(model.sample_count),
        key=lambda index: str(opaque_ids[index]),
    )
    records: list[SimilarityIndexRecord] = []
    for position in positions:
        target_index = int(target_indices[position])
        if not 0 <= target_index < len(model.labels):
            raise SimilarityIndexArtifactError(
                "Source model label references are invalid."
            )
        try:
            records.append(
                SimilarityIndexRecord(
                    opaque_id=str(opaque_ids[position]),
                    fault_code=model.labels[target_index].fault_code,
                )
            )
        except ValueError:
            raise SimilarityIndexArtifactError(
                "Source model record metadata is invalid."
            ) from None
    converted = np.asarray(vectors[positions], dtype=SIMILARITY_VECTOR_DTYPE, order="C")
    if not np.isfinite(converted).all():
        raise SimilarityIndexArtifactError(
            "Source model vectors cannot be represented safely."
        )
    state = model.preprocessor_state
    try:
        preprocessor = SimilarityPreprocessorState(
            mean=state.mean,
            scale=state.scale,
            variance=state.variance,
            sample_count=state.sample_count,
        )
    except ValueError:
        raise SimilarityIndexArtifactError(
            "Source model preprocessor is incompatible."
        ) from None
    compatibility = SimilarityIndexCompatibility(
        dataset_id=model.dataset_id,
        schema_id=schema_id,
    )
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
        raise SimilarityIndexArtifactError(
            "Similarity index staging directory is unavailable."
        ) from None
    try:
        preprocessor_bytes = _canonical_json_bytes(_preprocessor_payload(preprocessor))
        records_bytes = _canonical_json_bytes(_records_payload(tuple(records)))
        (staging / "preprocessor.json").write_bytes(preprocessor_bytes)
        (staging / "records.json").write_bytes(records_bytes)
        with (staging / "vectors.npy").open("wb") as stream:
            np.save(stream, converted, allow_pickle=False)
        files = (
            _artifact_file(
                staging / "preprocessor.json",
                media_type=_JSON_MEDIA_TYPE,
                logical_sha256=sha256(preprocessor_bytes).hexdigest(),
            ),
            _artifact_file(
                staging / "records.json",
                media_type=_JSON_MEDIA_TYPE,
                logical_sha256=sha256(records_bytes).hexdigest(),
            ),
            _artifact_file(
                staging / "vectors.npy",
                media_type=_VECTOR_MEDIA_TYPE,
                logical_sha256=_logical_vector_hash(converted),
            ),
        )
        identity = _identity_payload(
            compatibility=compatibility,
            source_model_id=model.model_id,
            source_model_content_sha256=model.content_sha256,
            record_count=model.sample_count,
            files=files,
        )
        content_sha256 = sha256(_canonical_json_bytes(identity)).hexdigest()
        manifest = SimilarityIndexManifest(
            artifact_schema_version=SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION,
            selector=SimilarityIndexSelector(
                index_id=f"{_INDEX_ID_PREFIX}{content_sha256[:32]}",
                compatibility=compatibility,
                model_id=model.model_id,
            ),
            content_sha256=content_sha256,
            source_model_id=model.model_id,
            source_model_content_sha256=model.content_sha256,
            record_count=model.sample_count,
            vector_dtype=SIMILARITY_VECTOR_DTYPE.str,
            distance_order=_DISTANCE_ORDER,
            distance_tie_break=_DISTANCE_TIE_BREAK,
            files=files,
        )
        (staging / "manifest.json").write_bytes(
            _canonical_json_bytes(_manifest_payload(manifest))
        )
        if destination.exists():
            if _directories_have_equal_files(staging, destination):
                shutil.rmtree(staging)
                return destination
            raise SimilarityIndexArtifactError(
                "Similarity index output already exists with different content."
            )
        os.replace(staging, destination)
    except SimilarityIndexError:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    except (OSError, TypeError, ValueError):
        if staging.exists():
            shutil.rmtree(staging)
        raise SimilarityIndexArtifactError(
            "Similarity index artifact could not be written."
        ) from None
    return destination


def load_similarity_index(
    input_directory: Path,
    *,
    expected: SimilarityIndexCompatibility,
    expected_index_id: str | None = None,
) -> LoadedSimilarityIndex:
    """Verify all bytes and versions before loading non-executable arrays."""

    directory = _validate_input_directory(input_directory)
    artifact_snapshot = {
        "manifest.json": _read_bounded_regular_file(
            directory / "manifest.json", _MAX_MANIFEST_BYTES
        )
    }
    manifest_bytes = artifact_snapshot["manifest.json"]
    manifest_value = _decode_json(manifest_bytes, "manifest")
    if _canonical_json_bytes(manifest_value) != manifest_bytes:
        raise SimilarityIndexArtifactError(
            "Similarity index manifest serialization is invalid."
        )
    manifest = _manifest_from_payload(manifest_value)
    if manifest.selector.compatibility != expected:
        raise SimilarityIndexCompatibilityError(
            "Similarity index compatibility does not match the expected contract."
        )
    if (
        expected_index_id is not None
        and manifest.selector.index_id != expected_index_id
    ):
        raise SimilarityIndexCompatibilityError(
            "Similarity index identity does not match the expected index."
        )
    file_by_name = {entry.filename: entry for entry in manifest.files}
    for filename in SIMILARITY_INDEX_FILENAMES[1:]:
        artifact_snapshot[filename] = _read_bounded_regular_file(
            directory / filename,
            _artifact_file_limit(filename),
        )
        if (
            sha256(artifact_snapshot[filename]).hexdigest()
            != file_by_name[filename].physical_sha256
        ):
            raise SimilarityIndexArtifactError(
                "Similarity index file integrity check failed."
            )
    identity = _identity_payload(
        compatibility=manifest.selector.compatibility,
        source_model_id=manifest.source_model_id,
        source_model_content_sha256=manifest.source_model_content_sha256,
        record_count=manifest.record_count,
        files=manifest.files,
    )
    content_sha256 = sha256(_canonical_json_bytes(identity)).hexdigest()
    if (
        content_sha256 != manifest.content_sha256
        or manifest.selector.index_id != f"{_INDEX_ID_PREFIX}{content_sha256[:32]}"
    ):
        raise SimilarityIndexArtifactError(
            "Similarity index content identity is inconsistent."
        )

    preprocessor_bytes = artifact_snapshot["preprocessor.json"]
    records_bytes = artifact_snapshot["records.json"]
    if (
        sha256(preprocessor_bytes).hexdigest()
        != file_by_name["preprocessor.json"].logical_sha256
        or sha256(records_bytes).hexdigest()
        != file_by_name["records.json"].logical_sha256
    ):
        raise SimilarityIndexArtifactError(
            "Similarity index JSON content is inconsistent."
        )
    preprocessor_value = _decode_json(preprocessor_bytes, "preprocessor")
    records_value = _decode_json(records_bytes, "records")
    if (
        _canonical_json_bytes(preprocessor_value) != preprocessor_bytes
        or _canonical_json_bytes(records_value) != records_bytes
    ):
        raise SimilarityIndexArtifactError(
            "Similarity index JSON serialization is invalid."
        )
    preprocessor = _preprocessor_from_payload(preprocessor_value)
    records = _records_from_payload(records_value)
    vectors = cast(
        FloatMatrix,
        _load_array_bytes(
            artifact_snapshot["vectors.npy"],
            SIMILARITY_VECTOR_DTYPE,
            context="Similarity index",
        ),
    )
    if _logical_vector_hash(vectors) != file_by_name["vectors.npy"].logical_sha256:
        raise SimilarityIndexArtifactError(
            "Similarity index vector content is inconsistent."
        )
    return LoadedSimilarityIndex(
        manifest=manifest,
        preprocessor=preprocessor,
        records=records,
        vectors=vectors,
    )


def _validate_query(
    query: SimilarityQuery,
    expected_selector: SimilarityIndexSelector,
) -> None:
    raw_selector = cast(object, query.selector)
    raw_features = cast(object, query.features)
    raw_top_k = cast(object, query.top_k)
    raw_fault_codes = cast(object, query.fault_codes)
    if type(raw_selector) is not SimilarityIndexSelector:
        raise SimilarityIndexQueryError("Similarity selector is invalid.")
    if query.selector != expected_selector:
        raise SimilarityIndexCompatibilityError(
            "Similarity query targets an incompatible index."
        )
    if type(raw_top_k) is not int or not 1 <= raw_top_k <= MAX_TOP_K:
        raise SimilarityIndexQueryError("Similarity top_k is invalid.")
    if not isinstance(raw_features, tuple):
        raise SimilarityIndexQueryError("Similarity query dimension is invalid.")
    features = cast(tuple[object, ...], raw_features)
    if len(features) != (expected_selector.compatibility.dimension):
        raise SimilarityIndexQueryError("Similarity query dimension is invalid.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        for value in features
    ):
        raise SimilarityIndexQueryError(
            "Similarity query features must be finite numbers."
        )
    if not isinstance(raw_fault_codes, tuple):
        raise SimilarityIndexQueryError("Similarity query filters are invalid.")
    fault_codes = cast(tuple[object, ...], raw_fault_codes)
    if (
        any(not isinstance(value, str) for value in fault_codes)
        or fault_codes != tuple(sorted(set(cast(tuple[str, ...], fault_codes))))
        or any(
            not isinstance(value, str) or _FAULT_CODE_PATTERN.fullmatch(value) is None
            for value in fault_codes
        )
    ):
        raise SimilarityIndexQueryError("Similarity query filters are invalid.")


def _identity_payload(
    *,
    compatibility: SimilarityIndexCompatibility,
    source_model_id: str,
    source_model_content_sha256: str,
    record_count: int,
    files: tuple[SimilarityArtifactFile, ...],
) -> dict[str, object]:
    return {
        "artifact_schema_version": SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION,
        "compatibility": _compatibility_payload(compatibility),
        "source_model": {
            "model_id": source_model_id,
            "content_sha256": source_model_content_sha256,
            "artifact_schema_version": KNN_ARTIFACT_SCHEMA_VERSION,
            "model_version": KNN_MODEL_VERSION,
        },
        "record_count": record_count,
        "vector_dtype": SIMILARITY_VECTOR_DTYPE.str,
        "distance_order": _DISTANCE_ORDER,
        "distance_tie_break": _DISTANCE_TIE_BREAK,
        "logical_files": [
            {
                "filename": entry.filename,
                "logical_sha256": entry.logical_sha256,
            }
            for entry in files
        ],
    }


def _manifest_payload(manifest: SimilarityIndexManifest) -> dict[str, object]:
    return {
        "artifact_schema_version": manifest.artifact_schema_version,
        "index_id": manifest.selector.index_id,
        "content_sha256": manifest.content_sha256,
        "compatibility": _compatibility_payload(manifest.selector.compatibility),
        "source_model": {
            "model_id": manifest.source_model_id,
            "content_sha256": manifest.source_model_content_sha256,
            "artifact_schema_version": KNN_ARTIFACT_SCHEMA_VERSION,
            "model_version": KNN_MODEL_VERSION,
        },
        "record_count": manifest.record_count,
        "vector_dtype": manifest.vector_dtype,
        "distance_order": manifest.distance_order,
        "distance_tie_break": manifest.distance_tie_break,
        "files": [
            {
                "filename": entry.filename,
                "media_type": entry.media_type,
                "physical_sha256": entry.physical_sha256,
                "logical_sha256": entry.logical_sha256,
            }
            for entry in manifest.files
        ],
    }


def _compatibility_payload(
    value: SimilarityIndexCompatibility,
) -> dict[str, object]:
    return {
        "dataset_id": value.dataset_id,
        "schema_id": value.schema_id,
        "feature_contract_version": value.feature_contract_version,
        "preprocessor_version": value.preprocessor_version,
        "index_version": value.index_version,
        "configuration_version": value.configuration_version,
        "dimension": value.dimension,
        "metric": value.metric,
    }


def _preprocessor_payload(value: SimilarityPreprocessorState) -> dict[str, object]:
    return {
        "schema_version": SIMILARITY_PREPROCESSOR_SCHEMA_VERSION,
        "mean": list(value.mean),
        "scale": list(value.scale),
        "variance": list(value.variance),
        "sample_count": value.sample_count,
    }


def _records_payload(
    records: tuple[SimilarityIndexRecord, ...],
) -> dict[str, object]:
    return {
        "schema_version": SIMILARITY_INDEX_RECORD_SCHEMA_VERSION,
        "records": [
            {"opaque_id": record.opaque_id, "fault_code": record.fault_code}
            for record in records
        ],
    }


def _manifest_from_payload(value: Mapping[str, object]) -> SimilarityIndexManifest:
    _exact_keys(
        value,
        (
            "artifact_schema_version",
            "index_id",
            "content_sha256",
            "compatibility",
            "source_model",
            "record_count",
            "vector_dtype",
            "distance_order",
            "distance_tie_break",
            "files",
        ),
    )
    if value["artifact_schema_version"] != SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION:
        raise SimilarityIndexCompatibilityError(
            "Similarity index artifact version is unsupported."
        )
    compatibility_value = _mapping(value["compatibility"], "compatibility")
    _exact_keys(
        compatibility_value,
        (
            "dataset_id",
            "schema_id",
            "feature_contract_version",
            "preprocessor_version",
            "index_version",
            "configuration_version",
            "dimension",
            "metric",
        ),
    )
    try:
        compatibility = SimilarityIndexCompatibility(
            dataset_id=_text(compatibility_value["dataset_id"], "dataset_id"),
            schema_id=_text(compatibility_value["schema_id"], "schema_id"),
            feature_contract_version=_integer(
                compatibility_value["feature_contract_version"],
                "feature_contract_version",
            ),
            preprocessor_version=_text(
                compatibility_value["preprocessor_version"],
                "preprocessor_version",
            ),
            index_version=_text(compatibility_value["index_version"], "index_version"),
            configuration_version=_text(
                compatibility_value["configuration_version"],
                "configuration_version",
            ),
            dimension=_integer(compatibility_value["dimension"], "dimension"),
            metric=_text(compatibility_value["metric"], "metric"),
        )
    except ValueError:
        raise SimilarityIndexArtifactError(
            "Similarity index compatibility is invalid."
        ) from None
    expected = SimilarityIndexCompatibility(
        dataset_id=compatibility.dataset_id,
        schema_id=compatibility.schema_id,
    )
    if compatibility != expected:
        raise SimilarityIndexCompatibilityError(
            "Similarity index versions or configuration are unsupported."
        )
    source_model = _mapping(value["source_model"], "source_model")
    _exact_keys(
        source_model,
        (
            "model_id",
            "content_sha256",
            "artifact_schema_version",
            "model_version",
        ),
    )
    if (
        source_model["artifact_schema_version"] != KNN_ARTIFACT_SCHEMA_VERSION
        or source_model["model_version"] != KNN_MODEL_VERSION
    ):
        raise SimilarityIndexCompatibilityError(
            "Similarity index source model version is unsupported."
        )
    files: list[SimilarityArtifactFile] = []
    for raw_entry in _sequence(value["files"], "files"):
        entry = _mapping(raw_entry, "file")
        _exact_keys(
            entry,
            (
                "filename",
                "media_type",
                "physical_sha256",
                "logical_sha256",
            ),
        )
        try:
            files.append(
                SimilarityArtifactFile(
                    filename=_text(entry["filename"], "filename"),
                    media_type=_text(entry["media_type"], "media_type"),
                    physical_sha256=_require_sha256(
                        entry["physical_sha256"], SimilarityIndexArtifactError
                    ),
                    logical_sha256=_require_sha256(
                        entry["logical_sha256"], SimilarityIndexArtifactError
                    ),
                )
            )
        except ValueError:
            raise SimilarityIndexArtifactError(
                "Similarity index file registry is invalid."
            ) from None
    try:
        return SimilarityIndexManifest(
            artifact_schema_version=_integer(
                value["artifact_schema_version"], "artifact_schema_version"
            ),
            selector=SimilarityIndexSelector(
                index_id=_text(value["index_id"], "index_id"),
                compatibility=compatibility,
                model_id=_text(source_model["model_id"], "model_id"),
            ),
            content_sha256=_require_sha256(
                value["content_sha256"], SimilarityIndexArtifactError
            ),
            source_model_id=_text(source_model["model_id"], "model_id"),
            source_model_content_sha256=_require_sha256(
                source_model["content_sha256"], SimilarityIndexArtifactError
            ),
            record_count=_integer(value["record_count"], "record_count"),
            vector_dtype=_text(value["vector_dtype"], "vector_dtype"),
            distance_order=_text(value["distance_order"], "distance_order"),
            distance_tie_break=_text(value["distance_tie_break"], "distance_tie_break"),
            files=tuple(files),
        )
    except ValueError:
        raise SimilarityIndexArtifactError(
            "Similarity index manifest is invalid."
        ) from None


def _preprocessor_from_payload(
    value: Mapping[str, object],
) -> SimilarityPreprocessorState:
    _exact_keys(value, ("schema_version", "mean", "scale", "variance", "sample_count"))
    if value["schema_version"] != SIMILARITY_PREPROCESSOR_SCHEMA_VERSION:
        raise SimilarityIndexCompatibilityError(
            "Similarity preprocessor version is unsupported."
        )
    try:
        return SimilarityPreprocessorState(
            mean=tuple(
                _finite_float(item, "mean") for item in _sequence(value["mean"], "mean")
            ),
            scale=tuple(
                _finite_float(item, "scale")
                for item in _sequence(value["scale"], "scale")
            ),
            variance=tuple(
                _finite_float(item, "variance")
                for item in _sequence(value["variance"], "variance")
            ),
            sample_count=_integer(value["sample_count"], "sample_count"),
        )
    except ValueError:
        raise SimilarityIndexArtifactError(
            "Similarity preprocessor state is invalid."
        ) from None


def _records_from_payload(
    value: Mapping[str, object],
) -> tuple[SimilarityIndexRecord, ...]:
    _exact_keys(value, ("schema_version", "records"))
    if value["schema_version"] != SIMILARITY_INDEX_RECORD_SCHEMA_VERSION:
        raise SimilarityIndexCompatibilityError(
            "Similarity record version is unsupported."
        )
    records: list[SimilarityIndexRecord] = []
    for raw_record in _sequence(value["records"], "records"):
        record = _mapping(raw_record, "record")
        _exact_keys(record, ("opaque_id", "fault_code"))
        try:
            records.append(
                SimilarityIndexRecord(
                    opaque_id=_text(record["opaque_id"], "opaque_id"),
                    fault_code=_text(record["fault_code"], "fault_code"),
                )
            )
        except ValueError:
            raise SimilarityIndexArtifactError(
                "Similarity index record metadata is invalid."
            ) from None
    if not records:
        raise SimilarityIndexArtifactError("Similarity index records are empty.")
    return tuple(records)


def _artifact_file(
    path: Path,
    *,
    media_type: str,
    logical_sha256: str,
) -> SimilarityArtifactFile:
    return SimilarityArtifactFile(
        filename=path.name,
        media_type=media_type,
        physical_sha256=sha256(
            _read_bounded_regular_file(path, _artifact_file_limit(path.name))
        ).hexdigest(),
        logical_sha256=logical_sha256,
    )


def _logical_vector_hash(vectors: FloatMatrix) -> str:
    header = _canonical_json_bytes(
        {
            "dtype": vectors.dtype.str,
            "shape": list(vectors.shape),
            "order": "C",
        }
    )
    return sha256(header + b"\x00" + vectors.tobytes(order="C")).hexdigest()


def _load_source_array_bytes(
    payload: bytes, expected_dtype: np.dtype[np.generic]
) -> NDArray[np.generic]:
    return _load_array_bytes(payload, expected_dtype, context="Source model")


def _load_array_bytes(
    payload: bytes,
    expected_dtype: np.dtype[np.generic],
    *,
    context: str,
) -> NDArray[np.generic]:
    try:
        raw_value: object = np.load(BytesIO(payload), allow_pickle=False)
    except (OSError, TypeError, ValueError):
        raise SimilarityIndexArtifactError(
            f"{context} array could not be loaded safely."
        ) from None
    if not isinstance(raw_value, np.ndarray):
        raise SimilarityIndexArtifactError(f"{context} array is incompatible.")
    value = cast(NDArray[np.generic], raw_value)
    if value.dtype != expected_dtype:
        raise SimilarityIndexArtifactError(f"{context} array is incompatible.")
    return value


def _read_source_model_snapshot(directory: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for filename in KNN_ARTIFACT_FILENAMES:
        path = directory / filename
        _reject_linked_path_components(path)
        snapshot[filename] = _read_bounded_regular_file(
            path,
            _MAX_SOURCE_MODEL_FILE_BYTES,
        )
    return snapshot


def _validate_source_model_directory(input_directory: Path) -> Path:
    directory = _resolve_path(input_directory)
    _reject_linked_path_components(directory)
    if not directory.is_dir():
        raise SimilarityIndexArtifactError(
            "Source model artifact directory is unavailable."
        )
    try:
        names = tuple(sorted(item.name for item in directory.iterdir()))
    except OSError:
        raise SimilarityIndexArtifactError(
            "Source model artifact directory is unavailable."
        ) from None
    if names != tuple(sorted(KNN_ARTIFACT_FILENAMES)):
        raise SimilarityIndexArtifactError("Source model artifact file set is invalid.")
    return directory


def _validate_output_destination(output_directory: Path) -> Path:
    if output_directory.name in {"", ".", ".."} or ".." in output_directory.parts:
        raise SimilarityIndexArtifactError(
            "Similarity index output destination is unsafe."
        )
    destination = _resolve_path(output_directory)
    if destination.parent == destination:
        raise SimilarityIndexArtifactError(
            "Similarity index output destination is unsafe."
        )
    _reject_linked_path_components(destination.parent)
    if destination.exists():
        _reject_linked_path_components(destination)
        if not destination.is_dir():
            raise SimilarityIndexArtifactError(
                "Similarity index output is not a directory."
            )
    worktree = _find_enclosing_git_worktree(destination)
    if worktree is not None:
        relative = destination.relative_to(worktree)
        ignored = _run_git(worktree, "check-ignore", "--quiet", "--", str(relative))
        if ignored.returncode != 0:
            raise SimilarityIndexArtifactError(
                "Similarity index output is not ignored by Git."
            )
    return destination


def _validate_input_directory(input_directory: Path) -> Path:
    directory = _resolve_path(input_directory)
    _reject_linked_path_components(directory)
    if not directory.is_dir():
        raise SimilarityIndexArtifactError("Similarity index directory is unavailable.")
    try:
        entries = tuple(directory.iterdir())
    except OSError:
        raise SimilarityIndexArtifactError(
            "Similarity index directory is unavailable."
        ) from None
    if tuple(sorted(item.name for item in entries)) != tuple(
        sorted(SIMILARITY_INDEX_FILENAMES)
    ):
        raise SimilarityIndexArtifactError(
            "Similarity index artifact file set is invalid."
        )
    for entry in entries:
        try:
            mode = entry.lstat().st_mode
        except OSError:
            raise SimilarityIndexArtifactError(
                "Similarity index artifact file is unavailable."
            ) from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise SimilarityIndexArtifactError(
                "Similarity index artifact contains an unsafe file."
            )
    return directory


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        raise SimilarityIndexArtifactError(
            "Similarity index path is invalid."
        ) from None


def _reject_linked_path_components(path: Path) -> None:
    current = path
    while True:
        if current.exists():
            try:
                mode = current.lstat().st_mode
            except OSError:
                raise SimilarityIndexArtifactError(
                    "Similarity index path cannot be inspected."
                ) from None
            try:
                is_junction = current.is_junction()
            except OSError:
                raise SimilarityIndexArtifactError(
                    "Similarity index path cannot be inspected."
                ) from None
            if stat.S_ISLNK(mode) or is_junction:
                raise SimilarityIndexArtifactError(
                    "Similarity index path cannot contain links."
                )
        if current == current.parent:
            return
        current = current.parent


def _find_enclosing_git_worktree(destination: Path) -> Path | None:
    current = destination if destination.exists() else destination.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    result = _run_git(current, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    try:
        worktree = Path(result.stdout.strip()).resolve(strict=True)
        destination.relative_to(worktree)
    except (OSError, RuntimeError, ValueError):
        return None
    return worktree


def _run_git(worktree: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise SimilarityIndexArtifactError(
            "Git similarity-index validation is unavailable."
        )
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
        raise SimilarityIndexArtifactError(
            "Git protection check could not be executed."
        ) from None


def _directories_have_equal_files(left: Path, right: Path) -> bool:
    try:
        left_names = tuple(sorted(item.name for item in left.iterdir()))
        right_names = tuple(sorted(item.name for item in right.iterdir()))
        if left_names != right_names:
            return False
        for name in left_names:
            maximum_bytes = _artifact_file_limit(name)
            if _read_bounded_regular_file(
                left / name, maximum_bytes
            ) != _read_bounded_regular_file(right / name, maximum_bytes):
                return False
        return True
    except (OSError, SimilarityIndexArtifactError):
        raise SimilarityIndexArtifactError(
            "Existing similarity index output cannot be verified."
        ) from None


def _read_bounded_regular_file(path: Path, maximum_bytes: int) -> bytes:
    descriptor: int | None = None
    try:
        _reject_linked_path_components(path.parent)
        path_metadata = path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISREG(
            path_metadata.st_mode
        ):
            raise SimilarityIndexArtifactError(
                "Similarity index artifact file is invalid."
            )
        flags = os.O_RDONLY | cast(int, getattr(os, "O_BINARY", 0))
        flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(path, flags)
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_size > maximum_bytes
            or _file_identity(opened_metadata) != _file_identity(path_metadata)
            or _portable_file_state(opened_metadata)
            != _portable_file_state(path_metadata)
        ):
            raise SimilarityIndexArtifactError(
                "Similarity index artifact file is invalid."
            )
        chunks: list[bytes] = []
        snapshot_digest = sha256()
        observed_bytes = 0
        while True:
            block = os.read(
                descriptor,
                min(_FILE_READ_BLOCK_BYTES, maximum_bytes + 1 - observed_bytes),
            )
            if not block:
                break
            chunks.append(block)
            snapshot_digest.update(block)
            observed_bytes += len(block)
            if observed_bytes > maximum_bytes:
                raise SimilarityIndexArtifactError(
                    "Similarity index artifact file is invalid."
                )
        post_read_metadata = os.fstat(descriptor)
        # Same-size writes can retain indistinguishable timestamps on some filesystems.
        # A second bounded digest keeps concurrent-mutation detection byte-based.
        os.lseek(descriptor, 0, os.SEEK_SET)
        verification_digest = sha256()
        verified_bytes = 0
        while True:
            block = os.read(
                descriptor,
                min(_FILE_READ_BLOCK_BYTES, maximum_bytes + 1 - verified_bytes),
            )
            if not block:
                break
            verification_digest.update(block)
            verified_bytes += len(block)
            if verified_bytes > maximum_bytes:
                raise SimilarityIndexArtifactError(
                    "Similarity index artifact file is invalid."
                )
        final_descriptor_metadata = os.fstat(descriptor)
        final_path_metadata = path.lstat()
        if (
            opened_metadata.st_size != observed_bytes
            or post_read_metadata.st_size != observed_bytes
            or verified_bytes != observed_bytes
            or final_descriptor_metadata.st_size != verified_bytes
            or not compare_digest(
                snapshot_digest.digest(), verification_digest.digest()
            )
            or _file_identity(post_read_metadata) != _file_identity(opened_metadata)
            or _file_identity(final_descriptor_metadata)
            != _file_identity(opened_metadata)
            or _file_identity(final_path_metadata) != _file_identity(opened_metadata)
            or _file_state(post_read_metadata) != _file_state(opened_metadata)
            or _file_state(final_descriptor_metadata) != _file_state(opened_metadata)
            or _portable_file_state(final_path_metadata)
            != _portable_file_state(opened_metadata)
            or stat.S_ISLNK(final_path_metadata.st_mode)
            or not stat.S_ISREG(final_path_metadata.st_mode)
        ):
            raise SimilarityIndexArtifactError(
                "Similarity index artifact file changed while being read."
            )
        return b"".join(chunks)
    except SimilarityIndexArtifactError:
        raise
    except OSError:
        raise SimilarityIndexArtifactError(
            "Similarity index artifact file is unavailable."
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                raise SimilarityIndexArtifactError(
                    "Similarity index artifact file could not be closed safely."
                ) from None


def _artifact_file_limit(filename: str) -> int:
    limits = {
        "manifest.json": _MAX_MANIFEST_BYTES,
        "preprocessor.json": _MAX_PREPROCESSOR_BYTES,
        "records.json": _MAX_RECORDS_BYTES,
        "vectors.npy": _MAX_VECTOR_BYTES,
    }
    try:
        return limits[filename]
    except KeyError:
        raise SimilarityIndexArtifactError(
            "Similarity index artifact file set is invalid."
        ) from None


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_state(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _portable_file_state(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_size, metadata.st_mtime_ns


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
        raise SimilarityIndexArtifactError(
            "Similarity index JSON value is invalid."
        ) from None


def _decode_json(value: bytes, context: str) -> dict[str, object]:
    try:
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except SimilarityIndexArtifactError:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise SimilarityIndexArtifactError(
            f"Similarity index {context} JSON is invalid."
        ) from None
    if not isinstance(decoded, dict):
        raise SimilarityIndexArtifactError(
            f"Similarity index {context} root is invalid."
        )
    return cast(dict[str, object], decoded)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SimilarityIndexArtifactError(
                "Similarity index JSON contains duplicate keys."
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise SimilarityIndexArtifactError(
        "Similarity index JSON contains a non-finite number."
    )


def _exact_keys(value: Mapping[str, object], expected: Sequence[str]) -> None:
    if tuple(value) != tuple(expected):
        raise SimilarityIndexArtifactError("Similarity index JSON fields are invalid.")


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    raw_mapping = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in raw_mapping):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    return cast(dict[str, object], raw_mapping)


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    return cast(list[object], value)


def _text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    return value


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    return value


def _finite_float(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    result = float(value)
    if not isfinite(result):
        raise SimilarityIndexArtifactError(f"Similarity index {context} is invalid.")
    return result


def _require_sha256(value: object, error_type: type[SimilarityIndexError]) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise error_type("Similarity index SHA-256 identity is invalid.")
    return value
