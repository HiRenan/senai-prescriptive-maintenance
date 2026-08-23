from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
from prescriptive_maintenance.contracts import ANALYSIS_FEATURE_NAMES, MAX_TOP_K
from prescriptive_maintenance.modeling import similarity_index as index_module
from prescriptive_maintenance.modeling.knn import (
    KNN_ARTIFACT_SCHEMA_VERSION,
    KNN_MODEL_VERSION,
    fit_knn_model,
    save_knn_model,
)
from prescriptive_maintenance.modeling.similarity_index import (
    SIMILARITY_CONFIGURATION_VERSION,
    SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION,
    SIMILARITY_INDEX_DIMENSION,
    SIMILARITY_INDEX_FILENAMES,
    SIMILARITY_INDEX_METRIC,
    SIMILARITY_INDEX_VERSION,
    SIMILARITY_PREPROCESSOR_VERSION,
    SIMILARITY_VECTOR_DTYPE,
    InMemorySimilarityIndexAdapter,
    LoadedSimilarityIndex,
    SimilarityIndexArtifactError,
    SimilarityIndexCompatibility,
    SimilarityIndexCompatibilityError,
    SimilarityIndexQueryError,
    SimilarityIndexSelector,
    SimilarityQuery,
    load_similarity_index,
    save_similarity_index_from_knn_artifact,
)

DATASET_ID = "a" * 64
SCHEMA_ID = "b" * 64


def _training_frame(
    values: tuple[float, ...] = (0.0, 2.0, 4.0),
    labels: tuple[str, ...] = (
        "synthetic-alpha",
        "synthetic-zeta",
        "synthetic-alpha",
    ),
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for value, label in zip(values, labels, strict=True):
        row: dict[str, object] = {
            name: float(value if position == 0 else position)
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


def _compatibility(
    *,
    dataset_id: str = DATASET_ID,
    schema_id: str = SCHEMA_ID,
) -> SimilarityIndexCompatibility:
    return SimilarityIndexCompatibility(
        dataset_id=dataset_id,
        schema_id=schema_id,
    )


def _build_index(tmp_path: Path) -> tuple[Path, LoadedSimilarityIndex]:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    model_directory = save_knn_model(model, tmp_path / "model")
    index_directory = save_similarity_index_from_knn_artifact(
        model_directory,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "index",
    )
    return index_directory, load_similarity_index(
        index_directory,
        expected=_compatibility(),
    )


def _features(first_value: float) -> tuple[float, ...]:
    return tuple(
        float(first_value if position == 0 else position)
        for position, _name in enumerate(ANALYSIS_FEATURE_NAMES)
    )


def _query(
    index: LoadedSimilarityIndex,
    *,
    first_value: float = 1.0,
    top_k: int = 3,
    fault_codes: tuple[str, ...] = (),
    selector: SimilarityIndexSelector | None = None,
) -> SimilarityQuery:
    return SimilarityQuery(
        selector=index.selector if selector is None else selector,
        features=_features(first_value),
        top_k=top_k,
        fault_codes=fault_codes,
    )


def _write_canonical_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_artifact_manifest_is_complete_safe_and_byte_stable(tmp_path: Path) -> None:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    source = save_knn_model(model, tmp_path / "model")
    first = save_similarity_index_from_knn_artifact(
        source,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "first",
    )
    second = save_similarity_index_from_knn_artifact(
        source,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "second",
    )

    assert tuple(sorted(item.name for item in first.iterdir())) == tuple(
        sorted(SIMILARITY_INDEX_FILENAMES)
    )
    assert all(
        (first / filename).read_bytes() == (second / filename).read_bytes()
        for filename in SIMILARITY_INDEX_FILENAMES
    )
    manifest = cast(
        dict[str, object],
        json.loads((first / "manifest.json").read_text(encoding="utf-8")),
    )
    compatibility = cast(dict[str, object], manifest["compatibility"])
    source_model = cast(dict[str, object], manifest["source_model"])
    assert manifest["artifact_schema_version"] == (
        SIMILARITY_INDEX_ARTIFACT_SCHEMA_VERSION
    )
    assert compatibility == {
        "dataset_id": DATASET_ID,
        "schema_id": SCHEMA_ID,
        "feature_contract_version": 1,
        "preprocessor_version": SIMILARITY_PREPROCESSOR_VERSION,
        "index_version": SIMILARITY_INDEX_VERSION,
        "configuration_version": SIMILARITY_CONFIGURATION_VERSION,
        "dimension": SIMILARITY_INDEX_DIMENSION,
        "metric": SIMILARITY_INDEX_METRIC,
    }
    assert manifest["record_count"] == 3
    assert manifest["vector_dtype"] == SIMILARITY_VECTOR_DTYPE.str
    assert manifest["distance_tie_break"] == "opaque_id_ascending"
    assert source_model["artifact_schema_version"] == KNN_ARTIFACT_SCHEMA_VERSION == 2
    assert source_model["model_version"] == KNN_MODEL_VERSION == 2


def test_artifact_round_trip_preserves_preprocessor_records_and_ranking(
    tmp_path: Path,
) -> None:
    directory, first = _build_index(tmp_path)
    second = load_similarity_index(
        directory,
        expected=_compatibility(),
        expected_index_id=first.selector.index_id,
    )

    assert second.manifest == first.manifest
    assert second.preprocessor == first.preprocessor
    assert second.records == first.records
    assert np.array_equal(second.vectors_copy(), first.vectors_copy())
    assert InMemorySimilarityIndexAdapter(first).query(_query(first)) == (
        InMemorySimilarityIndexAdapter(second).query(_query(second))
    )


@pytest.mark.parametrize(
    "replaced_filename",
    SIMILARITY_INDEX_FILENAMES,
)
def test_load_hashes_and_parses_each_immutable_file_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced_filename: str,
) -> None:
    directory, expected = _build_index(tmp_path)
    original_reader = cast(
        Callable[[Path, int], bytes],
        vars(index_module)["_read_bounded_regular_file"],
    )
    observed: dict[str, int] = {}

    def read_then_replace(path: Path, maximum_bytes: int) -> bytes:
        payload = original_reader(path, maximum_bytes)
        if path.parent == directory:
            observed[path.name] = observed.get(path.name, 0) + 1
            if path.name == replaced_filename:
                path.write_bytes(b"synthetic-concurrent-replacement")
        return payload

    monkeypatch.setattr(
        index_module,
        "_read_bounded_regular_file",
        read_then_replace,
    )

    loaded = load_similarity_index(directory, expected=_compatibility())

    assert loaded.manifest == expected.manifest
    assert loaded.preprocessor == expected.preprocessor
    assert loaded.records == expected.records
    assert np.array_equal(loaded.vectors_copy(), expected.vectors_copy())
    assert observed == {filename: 1 for filename in SIMILARITY_INDEX_FILENAMES}


def test_bounded_reader_rejects_descriptor_substituted_after_path_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_path = tmp_path / "expected.json"
    substituted_path = tmp_path / "substituted.json"
    expected_path.write_bytes(b"expected")
    substituted_path.write_bytes(b"attacker")
    original_open = index_module.os.open

    def substitute_descriptor(path: Path, flags: int) -> int:
        assert path == expected_path
        return original_open(substituted_path, flags)

    monkeypatch.setattr(index_module.os, "open", substitute_descriptor)

    with pytest.raises(SimilarityIndexArtifactError, match="invalid"):
        reader = cast(
            Callable[[Path, int], bytes],
            vars(index_module)["_read_bounded_regular_file"],
        )
        reader(expected_path, 100)


def test_bounded_reader_rejects_in_place_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b"original")
    original_open = index_module.os.open
    original_fstat = index_module.os.fstat
    observed_fstat_calls = 0

    def open_for_test(candidate: Path, _flags: int) -> int:
        return original_open(
            candidate,
            index_module.os.O_RDWR | cast(int, getattr(index_module.os, "O_BINARY", 0)),
        )

    def mutate_before_final_fstat(descriptor: int) -> Any:
        nonlocal observed_fstat_calls
        observed_fstat_calls += 1
        if observed_fstat_calls == 2:
            index_module.os.lseek(descriptor, 0, index_module.os.SEEK_SET)
            assert index_module.os.write(descriptor, b"attacker") == 8
            index_module.os.fsync(descriptor)
        return original_fstat(descriptor)

    monkeypatch.setattr(index_module.os, "open", open_for_test)
    monkeypatch.setattr(index_module.os, "fstat", mutate_before_final_fstat)
    reader = cast(
        Callable[[Path, int], bytes],
        vars(index_module)["_read_bounded_regular_file"],
    )

    with pytest.raises(SimilarityIndexArtifactError, match="changed"):
        reader(path, 100)


def test_bounded_reader_sanitizes_descriptor_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b"synthetic")
    original_close = index_module.os.close

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError("private-path-must-not-escape")

    monkeypatch.setattr(index_module.os, "close", close_then_fail)
    reader = cast(
        Callable[[Path, int], bytes],
        vars(index_module)["_read_bounded_regular_file"],
    )

    with pytest.raises(SimilarityIndexArtifactError) as captured:
        reader(path, 100)

    assert str(captured.value) == (
        "Similarity index artifact file could not be closed safely."
    )


def test_idempotent_reuse_rejects_a_substituted_destination_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _index = _build_index(tmp_path / "first")
    second, _index = _build_index(tmp_path / "second")
    substituted = tmp_path / "substituted.bin"
    substituted.write_bytes(b"synthetic-substitution")
    original_open = index_module.os.open

    def substitute_destination_descriptor(path: Path, flags: int) -> int:
        if path.parent == second and path.name == "vectors.npy":
            return original_open(substituted, flags)
        return original_open(path, flags)

    monkeypatch.setattr(index_module.os, "open", substitute_destination_descriptor)

    with pytest.raises(SimilarityIndexArtifactError, match="cannot be verified"):
        compare_directories = cast(
            Callable[[Path, Path], bool],
            vars(index_module)["_directories_have_equal_files"],
        )
        compare_directories(first, second)


def test_build_uses_the_single_validated_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    source = save_knn_model(model, tmp_path / "model")
    expected = save_similarity_index_from_knn_artifact(
        source,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "expected",
    )
    original_load = index_module.load_knn_model
    source_vectors = source / "training_vectors.npy"
    original_source_bytes = source_vectors.read_bytes()
    mutated = False

    def load_then_mutate_source(directory: Path) -> object:
        nonlocal mutated
        loaded = original_load(directory)
        payload = bytearray(source_vectors.read_bytes())
        payload[-8] ^= 1
        source_vectors.write_bytes(payload)
        mutated = True
        return loaded

    monkeypatch.setattr(index_module, "load_knn_model", load_then_mutate_source)
    actual = save_similarity_index_from_knn_artifact(
        source,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "actual",
    )

    assert mutated
    assert source_vectors.read_bytes() != original_source_bytes
    assert all(
        (actual / filename).read_bytes() == (expected / filename).read_bytes()
        for filename in SIMILARITY_INDEX_FILENAMES
    )


def test_index_preserves_sen42_neighbor_ranking(tmp_path: Path) -> None:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    model_directory = save_knn_model(model, tmp_path / "model")
    index_directory = save_similarity_index_from_knn_artifact(
        model_directory,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "index",
    )
    index = load_similarity_index(index_directory, expected=_compatibility())

    source = model.predict_candidate(
        OrderedDict(zip(ANALYSIS_FEATURE_NAMES, _features(1.0), strict=True)),
        top_k=3,
    )
    retrieved = InMemorySimilarityIndexAdapter(index).query(_query(index, top_k=3))

    assert tuple(item.opaque_id for item in retrieved) == tuple(
        item.neighbor_ref for item in source.neighbors
    )
    assert tuple(item.fault_code for item in retrieved) == tuple(
        model.label_for_target(item.target_slug).fault_code for item in source.neighbors
    )
    assert tuple(item.distance for item in retrieved) == pytest.approx(
        tuple(item.distance for item in source.neighbors),
        rel=1e-6,
        abs=1e-6,
    )


def test_memory_ranking_ties_and_filters_are_deterministic(tmp_path: Path) -> None:
    _directory, index = _build_index(tmp_path)
    adapter = InMemorySimilarityIndexAdapter(index)

    first = adapter.query(_query(index, top_k=2))
    second = adapter.query(_query(index, top_k=2))

    assert first == second
    assert len(first) == 2
    assert first[0].distance == first[1].distance
    assert tuple(item.opaque_id for item in first) == tuple(
        sorted(item.opaque_id for item in first)
    )
    selected_code = first[0].fault_code
    filtered = adapter.query(
        _query(index, top_k=MAX_TOP_K, fault_codes=(selected_code,))
    )
    assert filtered
    assert all(item.fault_code == selected_code for item in filtered)
    assert (
        adapter.query(
            _query(index, fault_codes=("fault_00000000000000000000000000000000",))
        )
        == ()
    )


@pytest.mark.parametrize("dimension", (0, SIMILARITY_INDEX_DIMENSION - 1, 19))
def test_query_rejects_wrong_dimension(tmp_path: Path, dimension: int) -> None:
    _directory, index = _build_index(tmp_path)
    query = SimilarityQuery(
        selector=index.selector,
        features=tuple(0.0 for _ in range(dimension)),
        top_k=1,
    )

    with pytest.raises(SimilarityIndexQueryError, match="dimension"):
        InMemorySimilarityIndexAdapter(index).query(query)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_query_rejects_non_finite_values(tmp_path: Path, value: float) -> None:
    _directory, index = _build_index(tmp_path)
    features = list(_features(1.0))
    features[4] = value

    with pytest.raises(SimilarityIndexQueryError, match="finite"):
        InMemorySimilarityIndexAdapter(index).query(
            SimilarityQuery(
                selector=index.selector,
                features=tuple(features),
                top_k=1,
            )
        )


@pytest.mark.parametrize("top_k", (0, MAX_TOP_K + 1, True, 1.5, "2"))
def test_query_rejects_invalid_top_k(tmp_path: Path, top_k: object) -> None:
    _directory, index = _build_index(tmp_path)

    with pytest.raises(SimilarityIndexQueryError, match="top_k"):
        InMemorySimilarityIndexAdapter(index).query(
            SimilarityQuery(
                selector=index.selector,
                features=_features(1.0),
                top_k=top_k,  # type: ignore[arg-type]
            )
        )


def test_query_rejects_incompatible_selector_before_search(tmp_path: Path) -> None:
    _directory, index = _build_index(tmp_path)
    incompatible = SimilarityIndexSelector(
        index_id=index.selector.index_id,
        compatibility=_compatibility(schema_id="c" * 64),
    )

    with pytest.raises(SimilarityIndexCompatibilityError, match="incompatible"):
        InMemorySimilarityIndexAdapter(index).query(
            _query(index, selector=incompatible)
        )


def test_query_requires_canonical_filter_order(tmp_path: Path) -> None:
    _directory, index = _build_index(tmp_path)
    codes: tuple[str, ...] = tuple(
        sorted({record.fault_code for record in index.records})
    )
    first_code = next(iter(codes))
    invalid = tuple(reversed(codes)) if len(codes) > 1 else (first_code, first_code)

    with pytest.raises(SimilarityIndexQueryError, match="filters"):
        InMemorySimilarityIndexAdapter(index).query(_query(index, fault_codes=invalid))


def test_load_rejects_wrong_expected_dataset_schema_and_index(tmp_path: Path) -> None:
    directory, index = _build_index(tmp_path)

    with pytest.raises(SimilarityIndexCompatibilityError, match="contract"):
        load_similarity_index(
            directory,
            expected=_compatibility(dataset_id="c" * 64),
        )
    with pytest.raises(SimilarityIndexCompatibilityError, match="contract"):
        load_similarity_index(
            directory,
            expected=_compatibility(schema_id="c" * 64),
        )
    with pytest.raises(SimilarityIndexCompatibilityError, match="identity"):
        load_similarity_index(
            directory,
            expected=_compatibility(),
            expected_index_id=f"similarity_index_v1_{'f' * 32}",
        )
    assert index.selector.index_id.startswith("similarity_index_v1_")


def test_load_checks_hash_before_array_deserialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _index = _build_index(tmp_path)
    vector_path = directory / "vectors.npy"
    payload = bytearray(vector_path.read_bytes())
    payload[-1] ^= 1
    vector_path.write_bytes(payload)
    called = False

    def forbidden_load(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        del args, kwargs
        raise AssertionError("np.load must not run before integrity validation")

    monkeypatch.setattr(index_module.np, "load", forbidden_load)

    with pytest.raises(SimilarityIndexArtifactError, match="integrity"):
        load_similarity_index(directory, expected=_compatibility())
    assert not called


def test_load_rejects_version_and_configuration_before_deserialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _index = _build_index(tmp_path)
    manifest_path = directory / "manifest.json"
    manifest = cast(
        dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    compatibility = cast(dict[str, object], manifest["compatibility"])
    compatibility["index_version"] = "exact-flat.v999"
    _write_canonical_json(manifest_path, manifest)

    def forbidden_load(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("np.load must not run for an incompatible manifest")

    monkeypatch.setattr(index_module.np, "load", forbidden_load)

    with pytest.raises(SimilarityIndexCompatibilityError, match="configuration"):
        load_similarity_index(directory, expected=_compatibility())


def test_load_always_disables_pickle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _index = _build_index(tmp_path)
    original_load = np.load
    observed: list[object] = []

    def observing_load(file: Any, *args: Any, **kwargs: Any) -> object:
        observed.append(kwargs.get("allow_pickle"))
        return original_load(file, *args, **kwargs)

    monkeypatch.setattr(index_module.np, "load", observing_load)

    loaded = load_similarity_index(directory, expected=_compatibility())

    assert loaded.manifest.record_count == 3
    assert observed == [False]


def test_load_rejects_missing_extra_and_unsafe_files(tmp_path: Path) -> None:
    missing, _index = _build_index(tmp_path / "missing-case")
    (missing / "records.json").unlink()
    with pytest.raises(SimilarityIndexArtifactError, match="file set"):
        load_similarity_index(missing, expected=_compatibility())

    extra, _index = _build_index(tmp_path / "extra-case")
    (extra / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    with pytest.raises(SimilarityIndexArtifactError, match="file set"):
        load_similarity_index(extra, expected=_compatibility())


def test_save_is_idempotent_and_rejects_different_existing_content(
    tmp_path: Path,
) -> None:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    source = save_knn_model(model, tmp_path / "model")
    output = save_similarity_index_from_knn_artifact(
        source,
        schema_id=SCHEMA_ID,
        output_directory=tmp_path / "index",
    )
    assert (
        save_similarity_index_from_knn_artifact(
            source,
            schema_id=SCHEMA_ID,
            output_directory=tmp_path / "index",
        )
        == output
    )
    (output / "records.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SimilarityIndexArtifactError, match="different content"):
        save_similarity_index_from_knn_artifact(
            source,
            schema_id=SCHEMA_ID,
            output_directory=tmp_path / "index",
        )


def test_save_rejects_non_ignored_destination_inside_worktree(tmp_path: Path) -> None:
    model = fit_knn_model(
        _training_frame(),
        dataset_id=DATASET_ID,
        training_partition_sha256=DATASET_ID,
    )
    source = save_knn_model(model, tmp_path / "model")
    repository = Path(__file__).parents[3]
    destination = repository / "synthetic-unsafe-similarity-index"

    with pytest.raises(SimilarityIndexArtifactError, match="not ignored"):
        save_similarity_index_from_knn_artifact(
            source,
            schema_id=SCHEMA_ID,
            output_directory=destination,
        )
    assert not destination.exists()


def test_query_rejects_mapping_and_list_runtime_smuggling(tmp_path: Path) -> None:
    _directory, index = _build_index(tmp_path)
    ordered = OrderedDict(
        (name, value)
        for name, value in zip(
            ANALYSIS_FEATURE_NAMES,
            _features(1.0),
            strict=True,
        )
    )

    with pytest.raises(SimilarityIndexQueryError, match="dimension"):
        InMemorySimilarityIndexAdapter(index).query(
            SimilarityQuery(
                selector=index.selector,
                features=ordered,  # type: ignore[arg-type]
                top_k=1,
            )
        )
    with pytest.raises(SimilarityIndexQueryError, match="filters"):
        InMemorySimilarityIndexAdapter(index).query(
            SimilarityQuery(
                selector=index.selector,
                features=_features(1.0),
                top_k=1,
                fault_codes=list(  # type: ignore[arg-type]
                    sorted({record.fault_code for record in index.records})
                ),
            )
        )
