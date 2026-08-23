"""Synthetic integration proofs for the canonical banner data pipeline."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, cast

import pandas as pd
import pytest
from prescriptive_maintenance.data.canonical import (
    CanonicalContractError,
    CanonicalLabelEntry,
    CanonicalLabelError,
    CanonicalLabelMap,
    Partition,
    build_canonical_dataset,
    check_canonical_dataset,
    load_canonical_dataset_schema,
    load_canonical_pipeline_config,
    project_banner_features,
)
from prescriptive_maintenance.data.source import BannerSourceFingerprint
from synthetic_banner_factory import BannerScenario, make_banner_dataframe

_REPOSITORY_ROOT: Final = Path(__file__).parents[3]
_LOCK_PATH: Final = _REPOSITORY_ROOT / "uv.lock"
_ROW_COUNT: Final = 120


def _temporal_dataframe() -> pd.DataFrame:
    template = make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS)
    start = pd.Timestamp("2099-04-01T00:00:00Z")
    rows: list[pd.Series[Any]] = []
    for index in range(_ROW_COUNT):
        row = template.iloc[index % len(template)].copy()
        row["id"] = -50_000 - index
        row["created_at"] = (start + pd.Timedelta(hours=6 * index)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        row["fault"] = "synthetic_warning" if index == 1 else "synthetic_nominal"
        rows.append(row)
    dataframe = pd.DataFrame(rows, columns=template.columns)
    dataframe = dataframe.astype(template.dtypes.to_dict())
    dataframe.iloc[1] = dataframe.iloc[0]
    dataframe.loc[1, "fault"] = "synthetic_warning"
    return dataframe


def _source_fingerprint(dataframe: pd.DataFrame) -> BannerSourceFingerprint:
    content = dataframe.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return BannerSourceFingerprint(
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
    )


def _label_map(fingerprint: BannerSourceFingerprint) -> CanonicalLabelMap:
    return CanonicalLabelMap(
        inventory_id=sha256(b"synthetic-label-inventory.v1").hexdigest(),
        source_sha256=fingerprint.sha256,
        entries=(
            CanonicalLabelEntry(
                raw_label="synthetic_nominal", slug="synthetic-nominal"
            ),
            CanonicalLabelEntry(
                raw_label="synthetic_warning", slug="synthetic-warning"
            ),
        ),
    )


def _manifest(directory: Path) -> dict[str, object]:
    value: object = json.loads((directory / "manifest.json").read_bytes())
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_pipeline_contract_projects_only_ordered_inference_features() -> None:
    dataframe = make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS)
    config = load_canonical_pipeline_config()
    schema = load_canonical_dataset_schema()

    projected = project_banner_features(dataframe)

    assert len(config.feature_names) == 18
    assert tuple(projected.columns) == config.feature_names
    assert tuple(schema.partition_names) == (*config.feature_names, "y")
    assert not (set(projected.columns) & config.feature_denylist)
    assert config.target_usage == "post_partition_y_only"
    target_mapping = next(
        item for item in config.source_columns if item.name == "fault"
    )
    assert (target_mapping.destination, target_mapping.kind) == ("y", "target")


def test_build_is_deterministic_and_checker_is_read_only(tmp_path: Path) -> None:
    dataframe = _temporal_dataframe()
    fingerprint = _source_fingerprint(dataframe)
    label_map = _label_map(fingerprint)
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"

    first = build_canonical_dataset(
        dataframe=dataframe,
        source_fingerprint=fingerprint,
        label_map=label_map,
        lock_path=_LOCK_PATH,
        output_directory=first_directory,
    )
    second = build_canonical_dataset(
        dataframe=dataframe,
        source_fingerprint=fingerprint,
        label_map=label_map,
        lock_path=_LOCK_PATH,
        output_directory=second_directory,
    )

    assert first.dataset_id == second.dataset_id
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.source_row_count == _ROW_COUNT
    assert sum(first.destination_counts.values()) == _ROW_COUNT
    assert sum(first.partition_counts.values()) < _ROW_COUNT
    assert first.destination_counts["purge"] > 0
    assert all(value > 0 for value in first.partition_counts.values())

    before = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns)
        for item in first_directory.iterdir()
    }
    checked = check_canonical_dataset(
        output_directory=first_directory,
        lock_path=_LOCK_PATH,
    )
    after = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns)
        for item in first_directory.iterdir()
    }
    assert checked.dataset_id == first.dataset_id
    assert checked.artifact_sha256 == first.artifact_sha256
    assert after == before

    manifest = _manifest(first_directory)
    assert all(_mapping(manifest["gates"]).values())
    assert _mapping(manifest["fit"])["target_usage"] == "post_partition_y_only"


def test_label_change_stays_in_occurrence_and_never_drives_split(
    tmp_path: Path,
) -> None:
    dataframe = _temporal_dataframe()
    fingerprint = _source_fingerprint(dataframe)
    label_map = _label_map(fingerprint)
    original_directory = tmp_path / "original"
    changed_directory = tmp_path / "changed"

    build_canonical_dataset(
        dataframe=dataframe,
        source_fingerprint=fingerprint,
        label_map=label_map,
        lock_path=_LOCK_PATH,
        output_directory=original_directory,
    )
    changed = dataframe.copy()
    changed.loc[changed.index % 3 == 0, "fault"] = "synthetic_warning"
    build_canonical_dataset(
        dataframe=changed,
        source_fingerprint=fingerprint,
        label_map=label_map,
        lock_path=_LOCK_PATH,
        output_directory=changed_directory,
    )

    original = pd.read_parquet(original_directory / "canonical.parquet")
    rebuilt = pd.read_parquet(changed_directory / "canonical.parquet")
    first_pair = original[original["source_position"].isin((1, 2))]
    dispositions = pd.read_parquet(original_directory / "dispositions.parquet")
    first_pair_dispositions = dispositions.loc[
        dispositions["source_position"].isin((1, 2))
    ]
    assert first_pair["y"].nunique() == 2
    assert first_pair["occurrence_id"].nunique() == 1
    assert first_pair["partition"].nunique(dropna=False) == 1
    assert set(first_pair_dispositions["disposition"]) == {"mapped"}
    pd.testing.assert_series_equal(
        original["occurrence_id"], rebuilt["occurrence_id"], check_names=False
    )
    pd.testing.assert_series_equal(
        original["partition"], rebuilt["partition"], check_names=False
    )
    pd.testing.assert_series_equal(
        original["split_exclusion_reason"],
        rebuilt["split_exclusion_reason"],
        check_names=False,
    )

    destinations = original["partition"].fillna(original["split_exclusion_reason"])
    assert (
        original.assign(destination=destinations)
        .groupby("occurrence_id")["destination"]
        .nunique()
        .eq(1)
        .all()
    )
    config = load_canonical_pipeline_config()
    for partition in Partition:
        frame = pd.read_parquet(original_directory / f"{partition.value}.parquet")
        assert tuple(frame.columns) == (*config.feature_names, "y")
        assert "target_slug" not in frame.columns


def test_contract_and_unknown_label_fail_before_output(tmp_path: Path) -> None:
    dataframe = _temporal_dataframe()
    fingerprint = _source_fingerprint(dataframe)
    label_map = _label_map(fingerprint)
    invalid_output = tmp_path / "invalid"
    unknown_output = tmp_path / "unknown"

    with pytest.raises(CanonicalContractError, match="column_missing"):
        build_canonical_dataset(
            dataframe=dataframe.drop(columns="rpm"),
            source_fingerprint=fingerprint,
            label_map=label_map,
            lock_path=_LOCK_PATH,
            output_directory=invalid_output,
        )
    unknown = dataframe.copy()
    unknown.loc[0, "fault"] = "synthetic_unknown"
    with pytest.raises(CanonicalLabelError, match="approved inventory"):
        build_canonical_dataset(
            dataframe=unknown,
            source_fingerprint=fingerprint,
            label_map=label_map,
            lock_path=_LOCK_PATH,
            output_directory=unknown_output,
        )

    assert not invalid_output.exists()
    assert not unknown_output.exists()
