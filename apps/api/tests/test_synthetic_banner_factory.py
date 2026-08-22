"""Contract and source-boundary proofs for the synthetic banner factory."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Final, NoReturn, TypedDict, cast
from unicodedata import normalize

import pandas as pd
import pytest
from prescriptive_maintenance.data import (
    BANNER_COLUMN_NAMES,
    BannerValidationReport,
    ContractViolationCode,
    validate_banner_dataframe,
)
from synthetic_banner_factory import (
    SYNTHETIC_CSV_FILENAME,
    SYNTHETIC_FAULT_ALLOWLIST,
    SYNTHETIC_PARQUET_FILENAME,
    BannerScenario,
    make_banner_dataframe,
    write_banner_csv,
    write_banner_parquet,
)

_REPOSITORY_ROOT: Final = Path(__file__).parents[3]
_MANIFEST_PATH: Final = _REPOSITORY_ROOT / "data" / "source-manifest.json"
_PUBLIC_FIXTURE_PATH: Final = (
    _REPOSITORY_ROOT / "data" / "fixtures" / "banner.synthetic.csv"
)
_UNIT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "z_rms_velocity_in_s",
        "z_rms_velocity_mm_s",
        "temperature_f",
        "temperature_c",
        "x_rms_velocity_in_s",
        "x_rms_velocity_mm_s",
        "z_peak_velocity_in_s",
        "z_peak_velocity_mm_s",
        "x_peak_velocity_in_s",
        "x_peak_velocity_mm_s",
    }
)
_INPUT_REPRODUCIBLE_VIOLATIONS: Final[dict[BannerScenario, ContractViolationCode]] = {
    BannerScenario.MISSING_COLUMN: ContractViolationCode.COLUMN_MISSING,
    BannerScenario.EXTRA_COLUMN: ContractViolationCode.COLUMN_EXTRA,
    BannerScenario.RENAMED_COLUMN: ContractViolationCode.COLUMN_NAME_MISMATCH,
    BannerScenario.REORDERED_COLUMNS: ContractViolationCode.COLUMN_ORDER_MISMATCH,
    BannerScenario.INVALID_DTYPE: ContractViolationCode.DTYPE_MISMATCH,
    BannerScenario.NULL_VALUE: ContractViolationCode.NULL_NOT_ALLOWED,
    BannerScenario.NAN_VALUE: ContractViolationCode.NAN_NOT_ALLOWED,
    BannerScenario.INFINITE_VALUE: ContractViolationCode.INFINITE_NOT_ALLOWED,
    BannerScenario.INVALID_TIMESTAMP: ContractViolationCode.TIMESTAMP_FORMAT,
    BannerScenario.EMPTY_FAULT: ContractViolationCode.EMPTY_FAULT,
    BannerScenario.UNKNOWN_CATEGORY: ContractViolationCode.UNKNOWN_FAULT_CATEGORY,
    BannerScenario.PHYSICAL_VIOLATION: ContractViolationCode.PHYSICAL_LOWER_BOUND,
}


class _SourceEntry(TypedDict):
    name: str
    size_bytes: int
    sha256: str


class _SourceManifest(TypedDict):
    files: list[_SourceEntry]


def _codes(report: BannerValidationReport) -> set[ContractViolationCode]:
    return {violation.code for violation in report.blocking_violations}


def _assert_only_columns_changed(
    actual: pd.DataFrame,
    control: pd.DataFrame,
    changed_columns: frozenset[str],
) -> None:
    assert tuple(actual.columns) == tuple(control.columns)
    assert actual.shape == control.shape
    unchanged_columns = [
        column for column in control.columns if column not in changed_columns
    ]
    changed_in_order = [
        column for column in control.columns if column in changed_columns
    ]
    pd.testing.assert_frame_equal(
        actual.loc[:, unchanged_columns],
        control.loc[:, unchanged_columns],
    )
    assert not actual.loc[:, changed_in_order].equals(control.loc[:, changed_in_order])


def _timestamps(dataframe: pd.DataFrame) -> tuple[datetime, ...]:
    return tuple(datetime.fromisoformat(value) for value in dataframe["created_at"])


def _load_source_manifest() -> _SourceManifest:
    return cast(
        _SourceManifest,
        json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")),
    )


def _assert_source_independent(artifact: Path) -> None:
    payload = artifact.read_bytes()
    folded_payload = payload.lower()
    artifact_hash = sha256(payload).hexdigest()
    for source in _load_source_manifest()["files"]:
        if artifact.name.casefold() == source["name"].casefold():
            raise AssertionError("Synthetic artifact reused a protected source name.")
        if source["name"].casefold().encode("utf-8") in folded_payload:
            raise AssertionError("Synthetic artifact embedded a protected source name.")
        if source["sha256"].encode("ascii") in folded_payload:
            raise AssertionError("Synthetic artifact embedded a protected fingerprint.")
        if source["size_bytes"] == len(payload) and compare_digest(
            source["sha256"], artifact_hash
        ):
            raise AssertionError("Synthetic artifact matched a protected source.")


def test_valid_factory_has_all_columns_and_passes_the_contract() -> None:
    dataframe = make_banner_dataframe()

    assert tuple(dataframe.columns) == BANNER_COLUMN_NAMES
    assert len(dataframe.columns) == 26
    assert validate_banner_dataframe(dataframe).is_valid
    assert validate_banner_dataframe(
        dataframe,
        allowed_fault_categories=SYNTHETIC_FAULT_ALLOWLIST,
    ).is_valid


def test_factory_does_not_read_the_public_static_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_read(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("the in-memory factory must not read a static CSV")

    monkeypatch.setattr(pd, "read_csv", reject_read)

    assert make_banner_dataframe().shape == (3, 26)


@pytest.mark.parametrize("scenario", tuple(BannerScenario))
def test_every_scenario_is_deterministic(scenario: BannerScenario) -> None:
    first = make_banner_dataframe(scenario=scenario)
    second = make_banner_dataframe(scenario=scenario)

    pd.testing.assert_frame_equal(first, second)
    assert first is not second


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    tuple(_INPUT_REPRODUCIBLE_VIOLATIONS.items()),
)
def test_contract_violation_scenarios_target_one_rule(
    scenario: BannerScenario,
    expected_code: ContractViolationCode,
) -> None:
    allowed_fault_categories = (
        SYNTHETIC_FAULT_ALLOWLIST
        if scenario is BannerScenario.UNKNOWN_CATEGORY
        else None
    )
    report = validate_banner_dataframe(
        make_banner_dataframe(scenario=scenario),
        allowed_fault_categories=allowed_fault_categories,
    )

    assert _codes(report) == {expected_code}


def test_factory_covers_every_input_reproducible_violation_code() -> None:
    # CHECK_FAILED is an internal fallback for undeclared Pandera checks, not an
    # input condition exposed by this contract.
    reproducible_codes = set(ContractViolationCode) - {
        ContractViolationCode.CHECK_FAILED
    }

    assert set(_INPUT_REPRODUCIBLE_VIOLATIONS.values()) == reproducible_codes
    assert len(_INPUT_REPRODUCIBLE_VIOLATIONS) == len(reproducible_codes)


def test_unknown_category_targets_the_explicit_allowlist_only() -> None:
    dataframe = make_banner_dataframe(scenario=BannerScenario.UNKNOWN_CATEGORY)

    assert validate_banner_dataframe(dataframe).is_valid
    report = validate_banner_dataframe(
        dataframe,
        allowed_fault_categories=SYNTHETIC_FAULT_ALLOWLIST,
    )

    assert _codes(report) == {ContractViolationCode.UNKNOWN_FAULT_CATEGORY}


def test_structural_scenarios_change_only_the_declared_shape_dimension() -> None:
    valid = make_banner_dataframe()
    missing = make_banner_dataframe(scenario=BannerScenario.MISSING_COLUMN)
    extra = make_banner_dataframe(scenario=BannerScenario.EXTRA_COLUMN)
    renamed = make_banner_dataframe(scenario=BannerScenario.RENAMED_COLUMN)
    reordered = make_banner_dataframe(scenario=BannerScenario.REORDERED_COLUMNS)

    pd.testing.assert_frame_equal(missing, valid.drop(columns="rpm"))
    pd.testing.assert_frame_equal(
        extra.drop(columns="synthetic_extra_measurement"), valid
    )
    assert tuple(extra.columns[:-1]) == BANNER_COLUMN_NAMES
    pd.testing.assert_frame_equal(
        renamed,
        valid.rename(columns={"rpm": "synthetic_rotation"}),
    )
    assert tuple(reordered.columns) == (
        BANNER_COLUMN_NAMES[1],
        BANNER_COLUMN_NAMES[0],
        *BANNER_COLUMN_NAMES[2:],
    )
    pd.testing.assert_frame_equal(reordered[list(BANNER_COLUMN_NAMES)], valid)


@pytest.mark.parametrize(
    ("scenario", "changed_columns", "control_scenario"),
    (
        (BannerScenario.INVALID_DTYPE, frozenset({"rpm"}), BannerScenario.VALID),
        (BannerScenario.NULL_VALUE, frozenset({"fault"}), BannerScenario.VALID),
        (BannerScenario.NAN_VALUE, frozenset({"rpm"}), BannerScenario.VALID),
        (BannerScenario.INFINITE_VALUE, frozenset({"rpm"}), BannerScenario.VALID),
        (
            BannerScenario.INVALID_TIMESTAMP,
            frozenset({"created_at"}),
            BannerScenario.VALID,
        ),
        (BannerScenario.EMPTY_FAULT, frozenset({"fault"}), BannerScenario.VALID),
        (
            BannerScenario.PHYSICAL_VIOLATION,
            frozenset({"z_rms_velocity_in_s"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.CONFLICTING_DUPLICATE,
            frozenset({"rpm"}),
            BannerScenario.IDENTICAL_DUPLICATE,
        ),
        (
            BannerScenario.COHERENT_UNIT_PAIRS,
            _UNIT_COLUMNS,
            BannerScenario.VALID,
        ),
        (
            BannerScenario.INCOHERENT_UNIT_PAIRS,
            frozenset({"z_rms_velocity_mm_s"}),
            BannerScenario.COHERENT_UNIT_PAIRS,
        ),
        (
            BannerScenario.IRREGULAR_CADENCE,
            frozenset({"created_at"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LONG_GAP,
            frozenset({"created_at"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_TRANSITION,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.BOUNDARY_24_HOURS,
            frozenset({"created_at"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_UNICODE_NFKC,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_CASE_VARIANTS,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_SPACE_VARIANTS,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_SEPARATOR_VARIANTS,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.LABEL_COLLISION,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
        (
            BannerScenario.UNKNOWN_CATEGORY,
            frozenset({"fault"}),
            BannerScenario.VALID,
        ),
    ),
)
def test_value_scenarios_change_only_the_intended_columns(
    scenario: BannerScenario,
    changed_columns: frozenset[str],
    control_scenario: BannerScenario,
) -> None:
    _assert_only_columns_changed(
        make_banner_dataframe(scenario=scenario),
        make_banner_dataframe(scenario=control_scenario),
        changed_columns,
    )


def test_duplicate_scenarios_isolate_identity_and_conflict() -> None:
    valid = make_banner_dataframe()
    identical = make_banner_dataframe(scenario=BannerScenario.IDENTICAL_DUPLICATE)
    conflicting = make_banner_dataframe(scenario=BannerScenario.CONFLICTING_DUPLICATE)

    pd.testing.assert_frame_equal(identical.iloc[:-1], valid)
    pd.testing.assert_series_equal(
        identical.iloc[-1],
        valid.iloc[0],
        check_names=False,
    )
    assert conflicting.iloc[-1]["id"] == identical.iloc[-1]["id"]
    assert conflicting.iloc[-1]["created_at"] == identical.iloc[-1]["created_at"]
    assert conflicting.iloc[-1]["rpm"] != identical.iloc[-1]["rpm"]


def test_unit_pair_scenarios_are_coherent_then_change_one_counterpart() -> None:
    coherent = make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS)
    incoherent = make_banner_dataframe(scenario=BannerScenario.INCOHERENT_UNIT_PAIRS)
    row = coherent.iloc[0]

    assert row["z_rms_velocity_mm_s"] == pytest.approx(
        row["z_rms_velocity_in_s"] * 25.4
    )
    assert row["x_rms_velocity_mm_s"] == pytest.approx(
        row["x_rms_velocity_in_s"] * 25.4
    )
    assert row["z_peak_velocity_mm_s"] == pytest.approx(
        row["z_peak_velocity_in_s"] * 25.4
    )
    assert row["x_peak_velocity_mm_s"] == pytest.approx(
        row["x_peak_velocity_in_s"] * 25.4
    )
    assert row["temperature_f"] == pytest.approx(row["temperature_c"] * 1.8 + 32)
    assert incoherent.iloc[0]["z_rms_velocity_mm_s"] != pytest.approx(
        incoherent.iloc[0]["z_rms_velocity_in_s"] * 25.4
    )


def test_temporal_scenarios_expose_each_boundary() -> None:
    irregular = _timestamps(
        make_banner_dataframe(scenario=BannerScenario.IRREGULAR_CADENCE)
    )
    long_gap = _timestamps(make_banner_dataframe(scenario=BannerScenario.LONG_GAP))
    boundary = _timestamps(
        make_banner_dataframe(scenario=BannerScenario.BOUNDARY_24_HOURS)
    )

    assert irregular[1] - irregular[0] != irregular[2] - irregular[1]
    assert long_gap[2] - long_gap[1] == timedelta(hours=8)
    assert boundary[1] - boundary[0] == timedelta(hours=24)


def test_label_scenarios_preserve_raw_variants_and_collision_inputs() -> None:
    transition = make_banner_dataframe(scenario=BannerScenario.LABEL_TRANSITION)
    unicode_variants = make_banner_dataframe(
        scenario=BannerScenario.LABEL_UNICODE_NFKC
    )["fault"]
    case_variants = make_banner_dataframe(scenario=BannerScenario.LABEL_CASE_VARIANTS)[
        "fault"
    ]
    space_variants = make_banner_dataframe(
        scenario=BannerScenario.LABEL_SPACE_VARIANTS
    )["fault"]
    separator_variants = make_banner_dataframe(
        scenario=BannerScenario.LABEL_SEPARATOR_VARIANTS
    )["fault"]
    collisions = make_banner_dataframe(scenario=BannerScenario.LABEL_COLLISION)["fault"]

    assert tuple(transition["fault"]) == (
        "synthetic_nominal",
        "synthetic_nominal",
        "synthetic_warning",
    )
    assert unicode_variants.iloc[0] != unicode_variants.iloc[1]
    assert normalize("NFKC", unicode_variants.iloc[0]) == normalize(
        "NFKC", unicode_variants.iloc[1]
    )
    assert case_variants.iloc[0].casefold() == case_variants.iloc[1].casefold()
    assert tuple(space_variants.iloc[:2]) == (
        "synthetic fault",
        " synthetic   fault ",
    )
    assert tuple(separator_variants) == (
        "synthetic-fault",
        "synthetic_fault",
        "synthetic/fault",
    )
    assert collisions.iloc[0] != collisions.iloc[1]
    assert (
        collisions.iloc[0].casefold().replace("-", "_")
        == collisions.iloc[1].strip().casefold()
    )


def test_csv_writer_is_byte_deterministic_and_stays_in_explicit_directories(
    tmp_path: Path,
) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()

    first = write_banner_csv(directory=first_directory)
    second = write_banner_csv(directory=second_directory)

    assert first.name == SYNTHETIC_CSV_FILENAME
    assert first.parent == first_directory
    assert second.parent == second_directory
    assert first.read_bytes() == second.read_bytes()
    assert validate_banner_dataframe(pd.read_csv(first)).is_valid


def test_parquet_writer_is_logically_deterministic_and_stays_in_directory(
    tmp_path: Path,
) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()

    first = write_banner_parquet(directory=first_directory)
    second = write_banner_parquet(directory=second_directory)
    first_frame = pd.read_parquet(first)
    second_frame = pd.read_parquet(second)

    assert first.name == SYNTHETIC_PARQUET_FILENAME
    assert first.parent == first_directory
    assert second.parent == second_directory
    pd.testing.assert_frame_equal(first_frame, second_frame)
    assert validate_banner_dataframe(first_frame).is_valid


@pytest.mark.parametrize("writer", (write_banner_csv, write_banner_parquet))
def test_writers_require_an_explicit_existing_directory(
    writer: Callable[..., Path],
    tmp_path: Path,
) -> None:
    parameter = inspect.signature(writer).parameters["directory"]
    missing_directory = tmp_path / "missing"

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(NotADirectoryError, match="must already exist"):
        writer(directory=missing_directory)
    assert not missing_directory.exists()


@pytest.mark.parametrize("scenario", tuple(BannerScenario))
@pytest.mark.parametrize("writer", (write_banner_csv, write_banner_parquet))
def test_generated_artifacts_do_not_reuse_source_names_hashes_or_bytes(
    writer: Callable[..., Path],
    scenario: BannerScenario,
    tmp_path: Path,
) -> None:
    artifact = writer(directory=tmp_path, scenario=scenario)

    _assert_source_independent(artifact)


def test_public_fixture_does_not_reuse_source_names_hashes_or_bytes() -> None:
    _assert_source_independent(_PUBLIC_FIXTURE_PATH)
