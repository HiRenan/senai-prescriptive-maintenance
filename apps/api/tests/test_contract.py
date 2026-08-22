"""Synthetic tests for the versioned banner dataframe contract."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from prescriptive_maintenance.data import (
    BANNER_COLUMN_CATALOG,
    BANNER_COLUMN_NAMES,
    BANNER_CONTRACT_VERSION,
    BANNER_DATAFRAME_SCHEMA,
    BannerValidationReport,
    ContractViolationCode,
    ValidationSeverity,
    build_banner_dataframe_schema,
    parse_banner_utc_timestamp,
    validate_banner_dataframe,
)

_FIXTURE_PATH = Path(__file__).parents[3] / "data" / "fixtures" / "banner.synthetic.csv"
_SYNTHETIC_FAULT_CATEGORIES = frozenset(
    {
        "synthetic_healthy",
        "synthetic_imbalance",
        "synthetic_bearing_warning",
    }
)


def _valid_dataframe() -> pd.DataFrame:
    return pd.read_csv(_FIXTURE_PATH)


def _codes(report: BannerValidationReport) -> set[ContractViolationCode]:
    return {violation.code for violation in report.blocking_violations}


def test_catalog_v2_matches_the_public_synthetic_header_exactly() -> None:
    dataframe = _valid_dataframe()

    assert BANNER_CONTRACT_VERSION == 2
    assert len(BANNER_COLUMN_CATALOG) == 26
    assert tuple(column.position for column in BANNER_COLUMN_CATALOG) == tuple(
        range(1, 27)
    )
    assert tuple(dataframe.columns) == BANNER_COLUMN_NAMES


def test_catalog_has_complete_reviewable_metadata_without_unit_conversion() -> None:
    forbidden_placeholders = {"", "todo", "tbd", "unknown", "n/a"}

    for column in BANNER_COLUMN_CATALOG:
        assert column.name.strip().lower() not in forbidden_placeholders
        assert column.source_unit.strip().lower() not in forbidden_placeholders
        assert column.canonical_unit.strip().lower() not in forbidden_placeholders
        assert column.domain.strip().lower() not in forbidden_placeholders
        assert (
            column.operational_description.strip().lower() not in forbidden_placeholders
        )
        assert column.source_unit == column.canonical_unit
        assert column.nullable is False


def test_pandera_schema_is_strict_ordered_and_never_coerces() -> None:
    dataframe = _valid_dataframe()
    dtypes_before = dataframe.dtypes.copy()

    validated = BANNER_DATAFRAME_SCHEMA.validate(dataframe)

    assert BANNER_DATAFRAME_SCHEMA.strict is True
    assert BANNER_DATAFRAME_SCHEMA.ordered is True
    assert BANNER_DATAFRAME_SCHEMA.coerce is False
    assert all(
        column.coerce is False for column in BANNER_DATAFRAME_SCHEMA.columns.values()
    )
    pd.testing.assert_series_equal(validated.dtypes, dtypes_before)


def test_valid_synthetic_dataframe_passes_with_an_explicit_fault_allowlist() -> None:
    dataframe = _valid_dataframe()

    report = validate_banner_dataframe(
        dataframe,
        allowed_fault_categories=_SYNTHETIC_FAULT_CATEGORIES,
    )

    assert report.is_valid
    assert report.blocking_violations == ()
    assert report.statistical_findings == ()


def test_raw_fault_domain_does_not_embed_the_private_source_vocabulary() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "fault"] = "synthetic_caller_defined_raw_label"

    report = validate_banner_dataframe(dataframe)

    assert report.is_valid


def test_missing_column_is_blocking() -> None:
    dataframe = _valid_dataframe().drop(columns="rpm")

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.COLUMN_MISSING}
    assert report.blocking_violations[0].column == "rpm"


def test_extra_column_is_blocking_without_exposing_its_name() -> None:
    private_synthetic_column = "synthetic_private_extra_column_9187"
    dataframe = _valid_dataframe().assign(**{private_synthetic_column: 1.0})

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.COLUMN_EXTRA}
    assert private_synthetic_column not in repr(report)


def test_renamed_column_is_blocking_without_exposing_the_received_name() -> None:
    private_synthetic_name = "synthetic_private_rotation_name_2741"
    dataframe = _valid_dataframe().rename(columns={"rpm": private_synthetic_name})

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.COLUMN_NAME_MISMATCH}
    assert report.blocking_violations[0].column == "rpm"
    assert private_synthetic_name not in repr(report)


def test_reordered_columns_are_blocking() -> None:
    dataframe = _valid_dataframe()
    reordered = dataframe[
        [BANNER_COLUMN_NAMES[1], BANNER_COLUMN_NAMES[0], *BANNER_COLUMN_NAMES[2:]]
    ]

    report = validate_banner_dataframe(reordered)

    assert _codes(report) == {ContractViolationCode.COLUMN_ORDER_MISMATCH}


def test_incompatible_dtype_is_blocking_and_not_coerced() -> None:
    dataframe = _valid_dataframe()
    dataframe["rpm"] = dataframe["rpm"].astype("str")

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.DTYPE_MISMATCH}
    assert str(dataframe["rpm"].dtype) == "str"


def test_null_is_classified_separately_from_numeric_nan() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "fault"] = None

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.NULL_NOT_ALLOWED}
    assert report.blocking_violations[0].column == "fault"


def test_null_in_integer_column_precedes_promoted_dtype_mismatch() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "id"] = None

    report = validate_banner_dataframe(dataframe)

    assert str(dataframe["id"].dtype) == "float64"
    assert _codes(report) == {ContractViolationCode.NULL_NOT_ALLOWED}
    assert report.blocking_violations[0].column == "id"


def test_nan_is_classified_deterministically() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "rpm"] = float("nan")

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.NAN_NOT_ALLOWED}
    assert report.blocking_violations[0].column == "rpm"


def test_infinity_is_classified_deterministically() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "rpm"] = float("inf")

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.INFINITE_NOT_ALLOWED}
    assert report.blocking_violations[0].column == "rpm"


def test_unknown_explicit_fault_category_is_sanitized() -> None:
    private_synthetic_value = "synthetic_private_unknown_fault_6329"
    dataframe = _valid_dataframe()
    dataframe.loc[0, "fault"] = private_synthetic_value

    report = validate_banner_dataframe(
        dataframe,
        allowed_fault_categories=_SYNTHETIC_FAULT_CATEGORIES,
    )

    assert _codes(report) == {ContractViolationCode.UNKNOWN_FAULT_CATEGORY}
    assert private_synthetic_value not in repr(report)
    assert report.blocking_violations[0].severity is ValidationSeverity.ERROR


def test_empty_fault_label_is_blocking() -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "fault"] = ""

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.EMPTY_FAULT}


def test_whitespace_only_fault_label_is_blocking_without_normalization() -> None:
    raw_synthetic_value = " \t "
    dataframe = _valid_dataframe()
    dataframe.loc[0, "fault"] = raw_synthetic_value

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.EMPTY_FAULT}
    assert dataframe.loc[0, "fault"] == raw_synthetic_value


def test_contract_v2_keeps_z_timestamp_compatibility_without_coercion() -> None:
    raw_synthetic_value = "2099-01-01T00:00:00.123456Z"
    dataframe = _valid_dataframe()
    dataframe.loc[0, "created_at"] = raw_synthetic_value

    report = validate_banner_dataframe(dataframe)

    assert report.is_valid
    assert dataframe.loc[0, "created_at"] == raw_synthetic_value


@pytest.mark.parametrize(
    "synthetic_value",
    (
        "2099-01-01T03:30:00.123456789+03:30",
        "2099-01-01 03:30:00.123456789+03:30",
        "2098-12-31T16:45:00.123456789-07:15",
    ),
)
def test_contract_v2_accepts_colon_form_numeric_offsets(
    synthetic_value: str,
) -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "created_at"] = synthetic_value

    report = validate_banner_dataframe(dataframe)

    assert report.is_valid
    assert dataframe.loc[0, "created_at"] == synthetic_value


def test_equivalent_z_and_offset_instants_compare_identically() -> None:
    synthetic_values = (
        "2099-01-01T00:00:00.123456789Z",
        "2099-01-01T02:00:00.123456789+02:00",
        "2099-01-01 00:00:00.123456789+00:00",
        "2098-12-31T20:30:00.123456789-03:30",
    )

    parsed = tuple(parse_banner_utc_timestamp(value) for value in synthetic_values)

    assert all(timestamp is not None for timestamp in parsed)
    assert parsed[0] == parsed[1] == parsed[2] == parsed[3]
    assert parsed[0] is not None
    assert parsed[0].canonical_text() == "2099-01-01T00:00:00.123456789Z"


def test_contract_v2_handles_known_and_unknown_zero_offsets() -> None:
    known_offset = "2099-01-01T00:00:00.123456+00:00"
    unknown_offset = "2099-01-01T00:00:00.123456-00:00"
    assert parse_banner_utc_timestamp(known_offset) == parse_banner_utc_timestamp(
        "2099-01-01T00:00:00.123456Z"
    )

    dataframe = _valid_dataframe()
    dataframe.loc[0, "created_at"] = unknown_offset
    report = validate_banner_dataframe(dataframe)

    assert parse_banner_utc_timestamp(unknown_offset) is None
    assert _codes(report) == {ContractViolationCode.TIMESTAMP_FORMAT}


@pytest.mark.parametrize(
    ("synthetic_value", "expected_utc"),
    (
        (
            "2099-01-01T00:15:00.0000001+01:00",
            "2098-12-31T23:15:00.0000001Z",
        ),
        (
            "2098-12-31 23:45:00.0000001-01:00",
            "2099-01-01T00:45:00.0000001Z",
        ),
    ),
)
def test_offset_normalization_crosses_date_boundaries_exactly(
    synthetic_value: str,
    expected_utc: str,
) -> None:
    parsed = parse_banner_utc_timestamp(synthetic_value)

    assert parsed is not None
    assert parsed.canonical_text() == expected_utc


@pytest.mark.parametrize(
    "private_synthetic_value",
    (
        "2099-01-01 00:00:00",
        "2099-01-01T00:00:00",
        "2026-13-40T25:61:61Z",
        "2026-02-29T00:00:00Z",
        "2026-12-31T25:61:61Z",
        "2099-01-01T00:00:00+24:00",
        "2099-01-01T00:00:00-24:00",
        "2099-01-01T00:00:00+01:60",
        "2099-01-01T00:00:00+0100",
        "2099-01-01T00:00:00+1:00",
        "2099-01-01T00:00:00+01",
        "2099-01-01t00:00:00+01:00",
        "2099-01-01T00:00:00z",
        "2099-01-01  00:00:00+01:00",
        "2099-01-01\t00:00:00+01:00",
        " 2099-01-01 00:00:00+01:00",
        "2099-01-01 00:00:00+01:00 ",
    ),
)
def test_invalid_timestamp_is_blocking_after_semantic_validation(
    private_synthetic_value: str,
) -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, "created_at"] = private_synthetic_value

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.TIMESTAMP_FORMAT}
    assert private_synthetic_value not in repr(report)


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (
        ("z_rms_velocity_in_s", -0.001),
        ("temperature_f", -459.68),
        ("temperature_c", -273.16),
        ("x_peak_vel_comp_freq_hz", -0.001),
        ("z_high_freq_rms_accel_g", -0.001),
    ),
)
def test_unequivocal_physical_lower_bounds_are_blocking(
    column: str, invalid_value: float
) -> None:
    dataframe = _valid_dataframe()
    dataframe.loc[0, column] = invalid_value

    report = validate_banner_dataframe(dataframe)

    assert _codes(report) == {ContractViolationCode.PHYSICAL_LOWER_BOUND}
    assert report.blocking_violations[0].column == column
    assert str(invalid_value) not in repr(report)


def test_explicit_fault_allowlist_is_part_of_the_executable_schema() -> None:
    schema = build_banner_dataframe_schema(
        allowed_fault_categories=_SYNTHETIC_FAULT_CATEGORIES
    )

    assert len(schema.columns["fault"].checks) == 2
