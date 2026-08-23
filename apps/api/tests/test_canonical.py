"""Synthetic integration proofs for the canonical banner data pipeline."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, cast

import pandas as pd
import prescriptive_maintenance.data.canonical as canonical_module
import pytest
from prescriptive_maintenance.data.canonical import (
    CanonicalCheckError,
    CanonicalContractError,
    CanonicalLabelEntry,
    CanonicalLabelError,
    CanonicalLabelMap,
    CanonicalOutputError,
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


def _occurrence_dataframe(sizes: tuple[int, ...]) -> pd.DataFrame:
    template = make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS)
    rows: list[pd.Series[Any]] = []
    start = pd.Timestamp("2099-07-01T00:00:00Z")
    ordinal = 0
    for group, size in enumerate(sizes):
        group_start = start + pd.Timedelta(hours=group * 2)
        for offset in range(size):
            row = template.iloc[ordinal % len(template)].copy()
            row["id"] = -70_000 - ordinal
            row["created_at"] = (group_start + pd.Timedelta(seconds=offset)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            row["fault"] = "synthetic_nominal"
            rows.append(row)
            ordinal += 1
    dataframe = pd.DataFrame(rows, columns=template.columns)
    return dataframe.astype(template.dtypes.to_dict())


def _build_synthetic(
    dataframe: pd.DataFrame, output: Path
) -> tuple[BannerSourceFingerprint, CanonicalLabelMap]:
    fingerprint = _source_fingerprint(dataframe)
    label_map = _label_map(fingerprint)
    build_canonical_dataset(
        dataframe=dataframe,
        source_fingerprint=fingerprint,
        label_map=label_map,
        lock_path=_LOCK_PATH,
        output_directory=output,
    )
    return fingerprint, label_map


def _reseal_manifest(directory: Path, manifest: dict[str, object]) -> None:
    artifacts = cast(list[object], manifest["artifacts"])
    for value in artifacts:
        entry = _mapping(value)
        filename = cast(str, entry["filename"])
        frame = pd.read_parquet(directory / filename)
        entry["row_count"] = len(frame)
        entry["column_count"] = len(frame.columns)
        entry["logical_sha256"] = canonical_module._logical_dataframe_hash(frame)
        entry["physical_sha256"] = canonical_module._hash_regular_file(
            directory / filename
        )
    manifest["dataset_id"] = canonical_module._calculate_dataset_id(manifest)
    (directory / "manifest.json").write_bytes(
        canonical_module._manifest_json_bytes(manifest)
    )


def _check_synthetic(
    directory: Path,
    fingerprint: BannerSourceFingerprint,
    label_map: CanonicalLabelMap,
    row_count: int,
) -> None:
    check_canonical_dataset(
        output_directory=directory,
        lock_path=_LOCK_PATH,
        source_fingerprint=fingerprint,
        label_map=label_map,
        expected_source_row_count=row_count,
    )


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
        source_fingerprint=fingerprint,
        label_map=label_map,
        expected_source_row_count=_ROW_COUNT,
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


def test_gap_fit_uses_only_final_train_occurrences_for_10_10_40(
    tmp_path: Path,
) -> None:
    dataframe = _occurrence_dataframe((10, 10, 40))
    output = tmp_path / "atomic-boundary"
    fingerprint, label_map = _build_synthetic(dataframe, output)

    canonical = pd.read_parquet(output / "canonical.parquet")
    config = load_canonical_pipeline_config()
    fit = canonical_module._fit_gap_from_canonical(canonical, config)
    train = canonical[canonical["partition"] == Partition.TRAIN.value]
    validation = canonical[canonical["partition"] == Partition.VALIDATION.value]
    test = canonical[canonical["partition"] == Partition.TEST.value]
    excluded_ids = set(validation["record_id"]) | set(test["record_id"])
    excluded_occurrences = set(validation["occurrence_id"]) | set(test["occurrence_id"])

    assert tuple(len(frame) for frame in (train, validation, test)) == (10, 10, 40)
    assert set(fit.record_ids) == set(train["record_id"])
    assert set(fit.record_ids).isdisjoint(excluded_ids)
    assert set(fit.occurrence_ids) == set(train["occurrence_id"])
    assert set(fit.occurrence_ids).isdisjoint(excluded_occurrences)
    manifest = _manifest(output)
    manifest_fit = _mapping(manifest["fit"])
    assert manifest_fit["occurrence_gap_fit_record_count"] == 10
    assert manifest_fit["occurrence_gap_fit_occurrence_count"] == 1
    assert manifest_fit["occurrence_gap_fit_membership_sha256"] == (
        fit.membership_sha256
    )
    assert all(_mapping(manifest["gates"]).values())
    _check_synthetic(output, fingerprint, label_map, len(dataframe))


def test_exact_24_hours_starts_a_new_occurrence() -> None:
    dataframe = _occurrence_dataframe((3,))
    dataframe.loc[:, "created_at"] = (
        "2099-07-01T00:00:00Z",
        "2099-07-01T23:59:59.999999Z",
        "2099-07-02T00:00:00Z",
    )
    fingerprint = _source_fingerprint(dataframe)
    records = canonical_module._build_records(
        dataframe=dataframe,
        source_sha256=fingerprint.sha256,
    )
    config = load_canonical_pipeline_config()

    occurrences = canonical_module._group_occurrences(
        records=records,
        ordered_indices=tuple(range(len(records))),
        source_sha256=fingerprint.sha256,
        config=config,
        gap_threshold=Decimal("100000"),
    )

    assert tuple(len(item.record_indices) for item in occurrences) == (2, 1)
    assert records[0].occurrence_id == records[1].occurrence_id
    assert records[2].occurrence_id != records[1].occurrence_id


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
    original_fit = _mapping(_manifest(original_directory)["fit"])
    rebuilt_fit = _mapping(_manifest(changed_directory)["fit"])
    first_pair = original[original["source_position"].isin((1, 2))]
    dispositions = pd.read_parquet(original_directory / "dispositions.parquet")
    rebuilt_dispositions = pd.read_parquet(changed_directory / "dispositions.parquet")
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
    assert (
        original_fit["occurrence_gap_threshold_seconds"]
        == rebuilt_fit["occurrence_gap_threshold_seconds"]
    )
    pd.testing.assert_series_equal(
        dispositions["disposition"],
        rebuilt_dispositions["disposition"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        dispositions["dataset_destination"],
        rebuilt_dispositions["dataset_destination"],
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


@pytest.mark.parametrize(
    "mode", ("shadow", "missing", "duplicate", "disposition_mismatch")
)
def test_checker_rejects_invalid_or_incomplete_destination_coverage(
    tmp_path: Path,
    mode: str,
) -> None:
    dataframe = _temporal_dataframe()
    output = tmp_path / mode
    fingerprint, label_map = _build_synthetic(dataframe, output)
    manifest = _manifest(output)
    dispositions = pd.read_parquet(output / "dispositions.parquet")

    if mode == "shadow":
        canonical = pd.read_parquet(output / "canonical.parquet")
        test_rows = canonical[canonical["partition"] == Partition.TEST.value]
        occurrence_id = str(test_rows.iloc[-1]["occurrence_id"])
        shadow_ids = set(
            str(item)
            for item in test_rows.loc[
                test_rows["occurrence_id"] == occurrence_id, "record_id"
            ]
        )
        dispositions.loc[
            dispositions["record_id"].isin(shadow_ids), "dataset_destination"
        ] = "shadow"
    elif mode == "missing":
        dispositions.loc[dispositions.index[-1], "record_id"] = "f" * 64
    elif mode == "duplicate":
        dispositions.loc[dispositions.index[-1], "record_id"] = dispositions.loc[
            dispositions.index[0], "record_id"
        ]
    else:
        dispositions.loc[dispositions.index[-1], "disposition"] = "rejected"

    dispositions.to_parquet(output / "dispositions.parquet", index=False)
    _reseal_manifest(output, manifest)

    with pytest.raises(CanonicalCheckError):
        _check_synthetic(output, fingerprint, label_map, len(dataframe))


@pytest.mark.parametrize(
    "mode",
    (
        "inventory_id",
        "nested_extra",
        "duplicate_partition",
        "inconsistent_reference",
        "component_id",
        "nested_type",
    ),
)
def test_checker_rejects_coherently_resealed_manifest_tampering(
    tmp_path: Path,
    mode: str,
) -> None:
    dataframe = _temporal_dataframe()
    output = tmp_path / mode
    fingerprint, label_map = _build_synthetic(dataframe, output)
    manifest = _manifest(output)

    if mode == "inventory_id":
        _mapping(manifest["components"])["fault_label_inventory_id"] = "b" * 64
    elif mode == "nested_extra":
        _mapping(manifest["fit"])["unexpected"] = True
    elif mode == "duplicate_partition":
        partitions = cast(list[object], manifest["partitions"])
        partitions[2] = json.loads(json.dumps(partitions[1]))
    elif mode == "inconsistent_reference":
        train = _mapping(cast(list[object], manifest["partitions"])[0])
        train["row_count"] = cast(int, train["row_count"]) + 1
        train_artifact = next(
            _mapping(item)
            for item in cast(list[object], manifest["artifacts"])
            if _mapping(item)["filename"] == "train.parquet"
        )
        train_artifact["row_count"] = cast(int, train_artifact["row_count"]) + 1
    elif mode == "component_id":
        _mapping(manifest["components"])["pipeline_config_id"] = "c" * 64
    else:
        _mapping(manifest["components"])["fault_label_normalization_version"] = True

    manifest["dataset_id"] = canonical_module._calculate_dataset_id(manifest)
    (output / "manifest.json").write_bytes(
        canonical_module._manifest_json_bytes(manifest)
    )

    with pytest.raises(CanonicalCheckError):
        _check_synthetic(output, fingerprint, label_map, len(dataframe))


def test_output_destination_requires_git_ignore_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "external-output"
    assert (
        canonical_module._validate_output_destination(external) == external.absolute()
    )

    repository = tmp_path / "repository"
    repository.mkdir()
    git_executable = shutil.which("git")
    assert git_executable is not None
    subprocess.run(  # noqa: S603 - resolved Git executable and fixed arguments.
        (git_executable, "init", "--quiet", repository),
        check=True,
        capture_output=True,
        text=True,
    )
    (repository / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored = repository / "ignored" / "build"
    visible = repository / "visible" / "build"
    assert canonical_module._validate_output_destination(ignored) == ignored.absolute()
    with pytest.raises(CanonicalOutputError, match="not ignored"):
        canonical_module._validate_output_destination(visible)
    with pytest.raises(CanonicalOutputError, match="unsafe"):
        canonical_module._validate_output_destination(
            repository / "ignored" / ".." / "escape"
        )

    original_run_git: Callable[..., subprocess.CompletedProcess[str]] = (
        canonical_module._run_git
    )

    def fail_check_ignore(
        worktree: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "check-ignore":
            return subprocess.CompletedProcess(arguments, 2, "", "synthetic error")
        return original_run_git(worktree, *arguments)

    with monkeypatch.context() as scoped:
        scoped.setattr(canonical_module, "_run_git", fail_check_ignore)
        with pytest.raises(CanonicalOutputError, match="status is unavailable"):
            canonical_module._validate_output_destination(ignored)

    junction = repository / "ignored" / "junction"
    junction.mkdir(parents=True)
    original_is_junction = Path.is_junction

    def synthetic_junction(path: Path) -> bool:
        return path == junction.absolute() or original_is_junction(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "is_junction", synthetic_junction)
        with pytest.raises(CanonicalOutputError, match="link or junction"):
            canonical_module._validate_output_destination(junction / "build")


def test_output_destination_rejects_symlink_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")

    with pytest.raises(CanonicalOutputError, match="link or junction"):
        canonical_module._validate_output_destination(link / "build")


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
