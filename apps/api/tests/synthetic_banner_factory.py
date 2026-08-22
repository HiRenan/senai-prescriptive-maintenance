"""Typed, deterministic synthetic banner tables for tests."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final

import pandas as pd
from prescriptive_maintenance.data import (
    BANNER_COLUMN_CATALOG,
    BANNER_COLUMN_NAMES,
    LogicalType,
)


class BannerScenario(StrEnum):
    """Named synthetic inputs that isolate one test intention."""

    VALID = "valid"
    MISSING_COLUMN = "missing_column"
    EXTRA_COLUMN = "extra_column"
    RENAMED_COLUMN = "renamed_column"
    REORDERED_COLUMNS = "reordered_columns"
    INVALID_DTYPE = "invalid_dtype"
    NULL_VALUE = "null_value"
    NAN_VALUE = "nan_value"
    INFINITE_VALUE = "infinite_value"
    INVALID_TIMESTAMP = "invalid_timestamp"
    EMPTY_FAULT = "empty_fault"
    PHYSICAL_VIOLATION = "physical_violation"
    IDENTICAL_DUPLICATE = "identical_duplicate"
    CONFLICTING_DUPLICATE = "conflicting_duplicate"
    COHERENT_UNIT_PAIRS = "coherent_unit_pairs"
    INCOHERENT_UNIT_PAIRS = "incoherent_unit_pairs"
    IRREGULAR_CADENCE = "irregular_cadence"
    LONG_GAP = "long_gap"
    LABEL_TRANSITION = "label_transition"
    BOUNDARY_24_HOURS = "boundary_24_hours"
    LABEL_UNICODE_NFKC = "label_unicode_nfkc"
    LABEL_CASE_VARIANTS = "label_case_variants"
    LABEL_SPACE_VARIANTS = "label_space_variants"
    LABEL_SEPARATOR_VARIANTS = "label_separator_variants"
    LABEL_COLLISION = "label_collision"
    UNKNOWN_CATEGORY = "unknown_category"


SYNTHETIC_FAULT_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {"synthetic_nominal", "synthetic_warning"}
)
SYNTHETIC_CSV_FILENAME: Final = "banner.synthetic.csv"
SYNTHETIC_PARQUET_FILENAME: Final = "banner.synthetic.parquet"

_BASE_ROWS: Final[tuple[tuple[object, ...], ...]] = (
    (
        -24001,
        "2099-04-01T00:00:00Z",
        0.25,
        6.35,
        68.0,
        20.0,
        0.50,
        12.70,
        0.80,
        0.90,
        30.0,
        31.0,
        0.10,
        0.20,
        3.00,
        3.10,
        2.00,
        2.10,
        0.75,
        19.05,
        1.00,
        25.40,
        0.30,
        0.40,
        "synthetic_nominal",
        1800.0,
    ),
    (
        -24002,
        "2099-04-01T00:01:00Z",
        0.20,
        5.08,
        77.0,
        25.0,
        0.40,
        10.16,
        0.70,
        0.80,
        40.0,
        41.0,
        0.11,
        0.21,
        3.20,
        3.30,
        2.20,
        2.30,
        0.60,
        15.24,
        0.80,
        20.32,
        0.31,
        0.41,
        "synthetic_nominal",
        1750.0,
    ),
    (
        -24003,
        "2099-04-01T00:02:00Z",
        0.30,
        7.62,
        86.0,
        30.0,
        0.60,
        15.24,
        0.60,
        0.70,
        50.0,
        51.0,
        0.12,
        0.22,
        3.40,
        3.50,
        2.40,
        2.50,
        0.90,
        22.86,
        1.20,
        30.48,
        0.32,
        0.42,
        "synthetic_nominal",
        1700.0,
    ),
)

_DTYPES: Final[dict[str, str]] = {
    column.name: {
        LogicalType.INT64: "int64",
        LogicalType.FLOAT64: "float64",
        LogicalType.STRING: "string",
        LogicalType.UTC_TIMESTAMP_STRING: "string",
    }[column.logical_type]
    for column in BANNER_COLUMN_CATALOG
}


def make_banner_dataframe(
    *, scenario: BannerScenario = BannerScenario.VALID
) -> pd.DataFrame:
    """Return a fresh deterministic table for one named scenario."""

    dataframe = pd.DataFrame(_BASE_ROWS, columns=BANNER_COLUMN_NAMES).astype(_DTYPES)
    return _SCENARIO_BUILDERS[scenario](dataframe)


def write_banner_csv(
    *, directory: Path, scenario: BannerScenario = BannerScenario.VALID
) -> Path:
    """Write a synthetic CSV only inside an existing caller-provided directory."""

    output_path = _output_path(directory, SYNTHETIC_CSV_FILENAME)
    make_banner_dataframe(scenario=scenario).to_csv(
        output_path,
        encoding="utf-8",
        index=False,
        lineterminator="\n",
    )
    return output_path


def write_banner_parquet(
    *, directory: Path, scenario: BannerScenario = BannerScenario.VALID
) -> Path:
    """Write synthetic Parquet only inside an existing caller-provided directory."""

    output_path = _output_path(directory, SYNTHETIC_PARQUET_FILENAME)
    make_banner_dataframe(scenario=scenario).to_parquet(
        output_path,
        engine="pyarrow",
        compression=None,
        index=False,
    )
    return output_path


def _output_path(directory: Path, filename: str) -> Path:
    if not directory.is_dir():
        raise NotADirectoryError("Synthetic output directory must already exist.")
    return directory / filename


def _unchanged(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe


def _missing_column(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.drop(columns="rpm")


def _extra_column(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.assign(synthetic_extra_measurement=1.0)


def _renamed_column(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.rename(columns={"rpm": "synthetic_rotation"})


def _reordered_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe[
        [BANNER_COLUMN_NAMES[1], BANNER_COLUMN_NAMES[0], *BANNER_COLUMN_NAMES[2:]]
    ]


def _invalid_dtype(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe["rpm"] = dataframe["rpm"].astype("string")
    return dataframe


def _null_value(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = pd.NA
    return dataframe


def _nan_value(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "rpm"] = float("nan")
    return dataframe


def _infinite_value(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "rpm"] = float("inf")
    return dataframe


def _invalid_timestamp(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "created_at"] = "2099-04-01 00:00:00"
    return dataframe


def _empty_fault(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = ""
    return dataframe


def _physical_violation(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "z_rms_velocity_in_s"] = -0.001
    return dataframe


def _identical_duplicate(dataframe: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([dataframe, dataframe.iloc[[0]]], ignore_index=True)


def _conflicting_duplicate(dataframe: pd.DataFrame) -> pd.DataFrame:
    duplicated = _identical_duplicate(dataframe)
    duplicated.loc[len(duplicated) - 1, "rpm"] = 1801.0
    return duplicated


def _coherent_unit_pairs(dataframe: pd.DataFrame) -> pd.DataFrame:
    values = {
        "z_rms_velocity_in_s": 0.125,
        "z_rms_velocity_mm_s": 3.175,
        "temperature_f": 50.0,
        "temperature_c": 10.0,
        "x_rms_velocity_in_s": 0.375,
        "x_rms_velocity_mm_s": 9.525,
        "z_peak_velocity_in_s": 0.625,
        "z_peak_velocity_mm_s": 15.875,
        "x_peak_velocity_in_s": 0.875,
        "x_peak_velocity_mm_s": 22.225,
    }
    for column, value in values.items():
        dataframe.loc[0, column] = value
    return dataframe


def _incoherent_unit_pairs(dataframe: pd.DataFrame) -> pd.DataFrame:
    coherent = _coherent_unit_pairs(dataframe)
    coherent.loc[0, "z_rms_velocity_mm_s"] = 3.200
    return coherent


def _irregular_cadence(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[2, "created_at"] = "2099-04-01T00:03:00Z"
    return dataframe


def _long_gap(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[2, "created_at"] = "2099-04-01T08:01:00Z"
    return dataframe


def _label_transition(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[2, "fault"] = "synthetic_warning"
    return dataframe


def _boundary_24_hours(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[1, "created_at"] = "2099-04-02T00:00:00Z"
    dataframe.loc[2, "created_at"] = "2099-04-02T00:01:00Z"
    return dataframe


def _label_unicode_nfkc(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "synthetic_caf\u00e9"
    dataframe.loc[1, "fault"] = "synthetic_cafe\u0301"
    return dataframe


def _label_case_variants(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "synthetic_fault"
    dataframe.loc[1, "fault"] = "SYNTHETIC_FAULT"
    return dataframe


def _label_space_variants(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "synthetic fault"
    dataframe.loc[1, "fault"] = " synthetic   fault "
    return dataframe


def _label_separator_variants(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "synthetic-fault"
    dataframe.loc[1, "fault"] = "synthetic_fault"
    dataframe.loc[2, "fault"] = "synthetic/fault"
    return dataframe


def _label_collision(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "Synthetic-Fault"
    dataframe.loc[1, "fault"] = " synthetic_fault "
    return dataframe


def _unknown_category(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe.loc[0, "fault"] = "synthetic_unknown_category"
    return dataframe


type _ScenarioBuilder = Callable[[pd.DataFrame], pd.DataFrame]
_SCENARIO_BUILDERS: Final[dict[BannerScenario, _ScenarioBuilder]] = {
    BannerScenario.VALID: _unchanged,
    BannerScenario.MISSING_COLUMN: _missing_column,
    BannerScenario.EXTRA_COLUMN: _extra_column,
    BannerScenario.RENAMED_COLUMN: _renamed_column,
    BannerScenario.REORDERED_COLUMNS: _reordered_columns,
    BannerScenario.INVALID_DTYPE: _invalid_dtype,
    BannerScenario.NULL_VALUE: _null_value,
    BannerScenario.NAN_VALUE: _nan_value,
    BannerScenario.INFINITE_VALUE: _infinite_value,
    BannerScenario.INVALID_TIMESTAMP: _invalid_timestamp,
    BannerScenario.EMPTY_FAULT: _empty_fault,
    BannerScenario.PHYSICAL_VIOLATION: _physical_violation,
    BannerScenario.IDENTICAL_DUPLICATE: _identical_duplicate,
    BannerScenario.CONFLICTING_DUPLICATE: _conflicting_duplicate,
    BannerScenario.COHERENT_UNIT_PAIRS: _coherent_unit_pairs,
    BannerScenario.INCOHERENT_UNIT_PAIRS: _incoherent_unit_pairs,
    BannerScenario.IRREGULAR_CADENCE: _irregular_cadence,
    BannerScenario.LONG_GAP: _long_gap,
    BannerScenario.LABEL_TRANSITION: _label_transition,
    BannerScenario.BOUNDARY_24_HOURS: _boundary_24_hours,
    BannerScenario.LABEL_UNICODE_NFKC: _label_unicode_nfkc,
    BannerScenario.LABEL_CASE_VARIANTS: _label_case_variants,
    BannerScenario.LABEL_SPACE_VARIANTS: _label_space_variants,
    BannerScenario.LABEL_SEPARATOR_VARIANTS: _label_separator_variants,
    BannerScenario.LABEL_COLLISION: _label_collision,
    BannerScenario.UNKNOWN_CATEGORY: _unknown_category,
}
