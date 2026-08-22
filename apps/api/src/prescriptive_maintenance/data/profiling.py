"""Deterministic, aggregate-only quality profiling for loaded banner tables."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, dataclass, is_dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from html import escape
from itertools import pairwise
from math import isfinite, isinf, isnan
from numbers import Real
from re import compile as compile_pattern
from typing import Final, cast

import pandas as pd

from prescriptive_maintenance.data.contract import (
    BANNER_COLUMN_CATALOG,
    BANNER_COLUMN_NAMES,
    BANNER_CONTRACT_VERSION,
    BannerColumnContract,
    BannerUtcTimestamp,
    LogicalType,
    banner_value_violates_domain,
    matches_banner_logical_type,
    parse_banner_utc_timestamp,
)

BANNER_PROFILE_SCHEMA_VERSION: Final = 1
PROFILE_DECIMAL_PLACES: Final = 6
PROFILE_QUANTILES: Final = (0.25, 0.50, 0.75)
PROFILE_IQR_MULTIPLIER: Final = 1.5
PROFILE_UNIT_ABSOLUTE_TOLERANCE: Final = 0.000001
PROFILE_UNIT_RELATIVE_TOLERANCE: Final = 0.000001

_ROUNDING_QUANTUM: Final = Decimal("0.000001")
_DECIMAL_QUANTILES: Final = tuple(Decimal(str(value)) for value in PROFILE_QUANTILES)
_DECIMAL_IQR_MULTIPLIER: Final = Decimal(str(PROFILE_IQR_MULTIPLIER))
_DECIMAL_UNIT_ABSOLUTE_TOLERANCE: Final = Decimal(str(PROFILE_UNIT_ABSOLUTE_TOLERANCE))
_DECIMAL_UNIT_RELATIVE_TOLERANCE: Final = Decimal(str(PROFILE_UNIT_RELATIVE_TOLERANCE))
_OPAQUE_LABEL_PATTERN: Final = compile_pattern(r"unapproved_label_\d{4,}")


class BannerProfileConfigurationError(ValueError):
    """Raised when a profiling configuration is ambiguous or invalid."""


class BannerProfileInputError(ValueError):
    """Raised when input cells cannot be aggregated safely."""


class ProfilePrivacyError(ValueError):
    """Raised when a public profile schema or payload is not aggregate-safe."""


class TemporalOrder(StrEnum):
    """Ordering observed among valid timestamps in dataframe row order."""

    NOT_APPLICABLE = "not_applicable"
    CONSTANT = "constant"
    NONDECREASING = "nondecreasing"
    NONINCREASING = "nonincreasing"
    UNORDERED = "unordered"


class ProfileFieldClassification(StrEnum):
    """Privacy classification required for every public output field."""

    AGGREGATE = "aggregate"
    CONFIGURATION = "configuration"
    SCHEMA = "schema"
    ROW_LEVEL = "row_level"
    LOCAL_PATH = "local_path"
    SAMPLE = "sample"
    INDIVIDUAL_IDENTIFIER = "individual_identifier"
    INDIVIDUAL_TIMESTAMP = "individual_timestamp"
    REIDENTIFIABLE_COMBINATION = "reidentifiable_combination"


@dataclass(frozen=True, slots=True)
class PublicProfileField:
    """One recursively classified field in the public profile schema."""

    name: str
    classification: ProfileFieldClassification
    children: tuple[PublicProfileField, ...] = ()
    sequence: bool = False


@dataclass(frozen=True, slots=True)
class ProfileDefinitions:
    """Fixed definitions that make profile calculations reproducible."""

    quantile_probabilities: tuple[float, ...]
    quantile_method: str
    standard_deviation: str
    temporal_order_basis: str
    cadence_method: str
    gap_definition: str
    iqr_multiplier: float
    iqr_outlier_boundaries: str
    unit_absolute_tolerance: float
    unit_relative_tolerance: float
    timezone: str
    timestamp_precision: str
    decimal_places: int
    rounding_mode: str
    unavailable_numeric_value: str
    unrepresentable_numeric_policy: str
    percentage_denominators: str
    column_order: str
    category_order: str
    label_publication_policy: str
    json_key_order: str


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """Aggregate volume and contract-shape observations."""

    row_count: int
    observed_column_count: int
    expected_column_count: int
    present_expected_column_count: int
    missing_expected_column_count: int
    unexpected_column_count: int
    columns_in_contract_order: bool


@dataclass(frozen=True, slots=True)
class TemporalProfile:
    """Aggregate time coverage, ordering, cadence, and gap observations."""

    valid_timestamp_count: int
    invalid_timestamp_count: int
    distinct_timestamp_count: int
    period_start_utc: str | None
    period_end_utc: str | None
    input_order: TemporalOrder
    cadence_interval_count: int
    nominal_cadence_seconds: float | None
    irregular_interval_count: int
    gap_count: int
    total_gap_seconds: float | None
    maximum_interval_seconds: float | None


@dataclass(frozen=True, slots=True)
class NumericStatistics:
    """Fixed descriptive statistics over finite, non-identifier values."""

    finite_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    population_standard_deviation: float | None
    quantile_25: float | None
    median: float | None
    quantile_75: float | None
    iqr: float | None
    iqr_lower_bound: float | None
    iqr_upper_bound: float | None
    iqr_outlier_count: int


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """Aggregate findings for one expected contract column."""

    position: int
    name: str
    logical_type: LogicalType
    present: bool
    dtype_matches_contract: bool
    observed_count: int
    null_count: int
    null_percentage: float | None
    nan_count: int
    nan_percentage: float | None
    missing_count: int
    missing_percentage: float | None
    infinite_count: int
    infinite_percentage: float | None
    domain_violation_count: int
    domain_violation_percentage: float | None
    unknown_category_check_applied: bool
    unknown_category_count: int
    unknown_category_percentage: float | None
    numeric_statistics: NumericStatistics | None


@dataclass(frozen=True, slots=True)
class DuplicateProfile:
    """Aggregate duplicate and declared-key conflict counts."""

    key_columns: tuple[str, ...]
    key_columns_available: bool
    rows_with_incomplete_key_count: int
    complete_duplicate_group_count: int
    complete_duplicate_excess_row_count: int
    duplicate_key_group_count: int
    duplicate_key_excess_row_count: int
    conflicting_key_group_count: int
    conflicting_row_count: int


@dataclass(frozen=True, slots=True)
class LabelCategoryProfile:
    """One aggregate label count, including allowed labels absent from input."""

    label: str
    count: int
    percentage: float | None
    is_allowed: bool | None


@dataclass(frozen=True, slots=True)
class LabelDistributionProfile:
    """Aggregate public-label distribution and deterministic balance measures."""

    source_column: str
    allowed_category_check_applied: bool
    allowed_category_count: int | None
    absent_allowed_category_count: int | None
    valid_label_count: int
    missing_label_count: int
    invalid_label_count: int
    distinct_observed_label_count: int
    distribution: tuple[LabelCategoryProfile, ...]
    majority_count: int | None
    minority_count: int | None
    majority_to_minority_ratio: float | None
    normalized_entropy: float | None


@dataclass(frozen=True, slots=True)
class UnitPairConsistencyProfile:
    """Aggregate consistency of one redundant measurement pair."""

    left_column: str
    right_column: str
    relation: str
    comparable_count: int
    unavailable_count: int
    consistent_count: int
    inconsistent_count: int
    consistency_percentage: float | None
    maximum_absolute_error: float | None


@dataclass(frozen=True, slots=True)
class BannerDataProfile:
    """Typed public profile containing aggregate observations only."""

    profile_schema_version: int
    contract_version: int
    definitions: ProfileDefinitions
    volume: VolumeProfile
    temporal: TemporalProfile
    columns: tuple[ColumnProfile, ...]
    duplicates: DuplicateProfile
    labels: LabelDistributionProfile
    redundant_unit_pairs: tuple[UnitPairConsistencyProfile, ...]


@dataclass(frozen=True, slots=True)
class _UnitPairSpec:
    left_column: str
    right_column: str
    multiplier: Decimal
    offset: Decimal
    relation: str


PROFILE_DEFINITIONS: Final = ProfileDefinitions(
    quantile_probabilities=PROFILE_QUANTILES,
    quantile_method="linear_type_7",
    standard_deviation="population_ddof_0",
    temporal_order_basis="valid_timestamps_in_input_row_order",
    cadence_method="mode_of_sorted_distinct_intervals_ties_to_smallest",
    gap_definition="interval_strictly_greater_than_nominal_cadence",
    iqr_multiplier=PROFILE_IQR_MULTIPLIER,
    iqr_outlier_boundaries="strictly_outside_inclusive_fences",
    unit_absolute_tolerance=PROFILE_UNIT_ABSOLUTE_TOLERANCE,
    unit_relative_tolerance=PROFILE_UNIT_RELATIVE_TOLERANCE,
    timezone="UTC",
    timestamp_precision="exact_input_fraction_period_and_interval_comparison",
    decimal_places=PROFILE_DECIMAL_PLACES,
    rounding_mode="decimal_half_even",
    unavailable_numeric_value="json_null",
    unrepresentable_numeric_policy=(
        "json_null_after_public_rounding_underflow_or_float64_overflow"
    ),
    percentage_denominators="column=observed;label=valid;unit_pair=comparable",
    column_order="banner_contract_position_ascending",
    category_order="trusted_unicode_then_unapproved_count_descending",
    label_publication_policy="trusted_allowlist_or_opaque_sequential_alias",
    json_key_order="public_schema_declaration_order",
)

_UNIT_PAIR_SPECS: Final = (
    _UnitPairSpec(
        "z_rms_velocity_in_s",
        "z_rms_velocity_mm_s",
        Decimal("25.4"),
        Decimal(0),
        "right = left * 25.4",
    ),
    _UnitPairSpec(
        "temperature_c",
        "temperature_f",
        Decimal("1.8"),
        Decimal(32),
        "right = left * 1.8 + 32",
    ),
    _UnitPairSpec(
        "x_rms_velocity_in_s",
        "x_rms_velocity_mm_s",
        Decimal("25.4"),
        Decimal(0),
        "right = left * 25.4",
    ),
    _UnitPairSpec(
        "z_peak_velocity_in_s",
        "z_peak_velocity_mm_s",
        Decimal("25.4"),
        Decimal(0),
        "right = left * 25.4",
    ),
    _UnitPairSpec(
        "x_peak_velocity_in_s",
        "x_peak_velocity_mm_s",
        Decimal("25.4"),
        Decimal(0),
        "right = left * 25.4",
    ),
)

_SAFE_FIELD_CLASSIFICATIONS: Final = frozenset(
    {
        ProfileFieldClassification.AGGREGATE,
        ProfileFieldClassification.CONFIGURATION,
        ProfileFieldClassification.SCHEMA,
    }
)


def _field(
    name: str,
    classification: ProfileFieldClassification,
    *children: PublicProfileField,
    sequence: bool = False,
) -> PublicProfileField:
    return PublicProfileField(name, classification, children, sequence)


_NUMERIC_STATISTICS_SCHEMA: Final = (
    _field("finite_count", ProfileFieldClassification.AGGREGATE),
    _field("minimum", ProfileFieldClassification.AGGREGATE),
    _field("maximum", ProfileFieldClassification.AGGREGATE),
    _field("mean", ProfileFieldClassification.AGGREGATE),
    _field("population_standard_deviation", ProfileFieldClassification.AGGREGATE),
    _field("quantile_25", ProfileFieldClassification.AGGREGATE),
    _field("median", ProfileFieldClassification.AGGREGATE),
    _field("quantile_75", ProfileFieldClassification.AGGREGATE),
    _field("iqr", ProfileFieldClassification.AGGREGATE),
    _field("iqr_lower_bound", ProfileFieldClassification.AGGREGATE),
    _field("iqr_upper_bound", ProfileFieldClassification.AGGREGATE),
    _field("iqr_outlier_count", ProfileFieldClassification.AGGREGATE),
)

PUBLIC_BANNER_PROFILE_SCHEMA: Final = _field(
    "banner_profile",
    ProfileFieldClassification.AGGREGATE,
    _field("profile_schema_version", ProfileFieldClassification.SCHEMA),
    _field("contract_version", ProfileFieldClassification.SCHEMA),
    _field(
        "definitions",
        ProfileFieldClassification.CONFIGURATION,
        _field("quantile_probabilities", ProfileFieldClassification.CONFIGURATION),
        _field("quantile_method", ProfileFieldClassification.CONFIGURATION),
        _field("standard_deviation", ProfileFieldClassification.CONFIGURATION),
        _field("temporal_order_basis", ProfileFieldClassification.CONFIGURATION),
        _field("cadence_method", ProfileFieldClassification.CONFIGURATION),
        _field("gap_definition", ProfileFieldClassification.CONFIGURATION),
        _field("iqr_multiplier", ProfileFieldClassification.CONFIGURATION),
        _field("iqr_outlier_boundaries", ProfileFieldClassification.CONFIGURATION),
        _field("unit_absolute_tolerance", ProfileFieldClassification.CONFIGURATION),
        _field("unit_relative_tolerance", ProfileFieldClassification.CONFIGURATION),
        _field("timezone", ProfileFieldClassification.CONFIGURATION),
        _field("timestamp_precision", ProfileFieldClassification.CONFIGURATION),
        _field("decimal_places", ProfileFieldClassification.CONFIGURATION),
        _field("rounding_mode", ProfileFieldClassification.CONFIGURATION),
        _field(
            "unavailable_numeric_value",
            ProfileFieldClassification.CONFIGURATION,
        ),
        _field(
            "unrepresentable_numeric_policy",
            ProfileFieldClassification.CONFIGURATION,
        ),
        _field("percentage_denominators", ProfileFieldClassification.CONFIGURATION),
        _field("column_order", ProfileFieldClassification.CONFIGURATION),
        _field("category_order", ProfileFieldClassification.CONFIGURATION),
        _field("label_publication_policy", ProfileFieldClassification.CONFIGURATION),
        _field("json_key_order", ProfileFieldClassification.CONFIGURATION),
    ),
    _field(
        "volume",
        ProfileFieldClassification.AGGREGATE,
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "row_count",
                "observed_column_count",
                "expected_column_count",
                "present_expected_column_count",
                "missing_expected_column_count",
                "unexpected_column_count",
                "columns_in_contract_order",
            )
        ),
    ),
    _field(
        "temporal",
        ProfileFieldClassification.AGGREGATE,
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "valid_timestamp_count",
                "invalid_timestamp_count",
                "distinct_timestamp_count",
                "period_start_utc",
                "period_end_utc",
                "input_order",
                "cadence_interval_count",
                "nominal_cadence_seconds",
                "irregular_interval_count",
                "gap_count",
                "total_gap_seconds",
                "maximum_interval_seconds",
            )
        ),
    ),
    _field(
        "columns",
        ProfileFieldClassification.AGGREGATE,
        _field("position", ProfileFieldClassification.SCHEMA),
        _field("name", ProfileFieldClassification.SCHEMA),
        _field("logical_type", ProfileFieldClassification.SCHEMA),
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "present",
                "dtype_matches_contract",
                "observed_count",
                "null_count",
                "null_percentage",
                "nan_count",
                "nan_percentage",
                "missing_count",
                "missing_percentage",
                "infinite_count",
                "infinite_percentage",
                "domain_violation_count",
                "domain_violation_percentage",
                "unknown_category_check_applied",
                "unknown_category_count",
                "unknown_category_percentage",
            )
        ),
        _field(
            "numeric_statistics",
            ProfileFieldClassification.AGGREGATE,
            *_NUMERIC_STATISTICS_SCHEMA,
        ),
        sequence=True,
    ),
    _field(
        "duplicates",
        ProfileFieldClassification.AGGREGATE,
        _field("key_columns", ProfileFieldClassification.SCHEMA),
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "key_columns_available",
                "rows_with_incomplete_key_count",
                "complete_duplicate_group_count",
                "complete_duplicate_excess_row_count",
                "duplicate_key_group_count",
                "duplicate_key_excess_row_count",
                "conflicting_key_group_count",
                "conflicting_row_count",
            )
        ),
    ),
    _field(
        "labels",
        ProfileFieldClassification.AGGREGATE,
        _field("source_column", ProfileFieldClassification.SCHEMA),
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "allowed_category_check_applied",
                "allowed_category_count",
                "absent_allowed_category_count",
                "valid_label_count",
                "missing_label_count",
                "invalid_label_count",
                "distinct_observed_label_count",
            )
        ),
        _field(
            "distribution",
            ProfileFieldClassification.AGGREGATE,
            _field("label", ProfileFieldClassification.AGGREGATE),
            _field("count", ProfileFieldClassification.AGGREGATE),
            _field("percentage", ProfileFieldClassification.AGGREGATE),
            _field("is_allowed", ProfileFieldClassification.AGGREGATE),
            sequence=True,
        ),
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "majority_count",
                "minority_count",
                "majority_to_minority_ratio",
                "normalized_entropy",
            )
        ),
    ),
    _field(
        "redundant_unit_pairs",
        ProfileFieldClassification.AGGREGATE,
        _field("left_column", ProfileFieldClassification.SCHEMA),
        _field("right_column", ProfileFieldClassification.SCHEMA),
        _field("relation", ProfileFieldClassification.CONFIGURATION),
        *(
            _field(name, ProfileFieldClassification.AGGREGATE)
            for name in (
                "comparable_count",
                "unavailable_count",
                "consistent_count",
                "inconsistent_count",
                "consistency_percentage",
                "maximum_absolute_error",
            )
        ),
        sequence=True,
    ),
)


def profile_banner_dataframe(
    dataframe: pd.DataFrame,
    *,
    key_columns: Sequence[str],
    allowed_fault_categories: Set[str] | None = None,
) -> BannerDataProfile:
    """Profile an already-loaded dataframe without mutating or reading sources."""

    keys = _validate_key_columns(key_columns)
    allowed_faults = _validate_allowed_fault_categories(allowed_fault_categories)
    if not dataframe.columns.is_unique:
        raise BannerProfileConfigurationError(
            "Profiling requires unique dataframe column names."
        )

    return BannerDataProfile(
        profile_schema_version=BANNER_PROFILE_SCHEMA_VERSION,
        contract_version=BANNER_CONTRACT_VERSION,
        definitions=PROFILE_DEFINITIONS,
        volume=_profile_volume(dataframe),
        temporal=_profile_time(dataframe),
        columns=tuple(
            _profile_column(dataframe, column, allowed_faults)
            for column in BANNER_COLUMN_CATALOG
        ),
        duplicates=_profile_duplicates(dataframe, keys),
        labels=_profile_labels(dataframe, allowed_faults),
        redundant_unit_pairs=tuple(
            _profile_unit_pair(dataframe, spec) for spec in _UNIT_PAIR_SPECS
        ),
    )


def banner_profile_json_bytes(profile: BannerDataProfile) -> bytes:
    """Serialize a profile to stable UTF-8 JSON with a trailing LF."""

    validate_public_profile_schema(PUBLIC_BANNER_PROFILE_SCHEMA)
    _validate_profile_value_policy(profile)
    payload = _public_value(profile)
    _validate_public_payload(payload, PUBLIC_BANNER_PROFILE_SCHEMA)
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def render_banner_profile_markdown(profile: BannerDataProfile) -> str:
    """Render a deterministic aggregate Markdown summary."""

    validate_public_profile_schema(PUBLIC_BANNER_PROFILE_SCHEMA)
    _validate_profile_value_policy(profile)
    payload = _public_value(profile)
    _validate_public_payload(payload, PUBLIC_BANNER_PROFILE_SCHEMA)

    lines = [
        "# Perfil agregado de qualidade do banner",
        "",
        "## Volume e estrutura",
        "",
        f"- Linhas: {profile.volume.row_count}",
        (
            "- Colunas observadas/esperadas: "
            f"{profile.volume.observed_column_count}/"
            f"{profile.volume.expected_column_count}"
        ),
        f"- Colunas ausentes: {profile.volume.missing_expected_column_count}",
        f"- Colunas inesperadas: {profile.volume.unexpected_column_count}",
        (f"- Ordem contratual: {_yes_no(profile.volume.columns_in_contract_order)}"),
        "",
        "## Tempo",
        "",
        f"- Período UTC: {_markdown_period(profile.temporal)}",
        f"- Ordenação de entrada: {profile.temporal.input_order.value}",
        (
            "- Cadência nominal (s): "
            f"{_format_optional_number(profile.temporal.nominal_cadence_seconds)}"
        ),
        f"- Intervalos irregulares: {profile.temporal.irregular_interval_count}",
        f"- Lacunas: {profile.temporal.gap_count}",
        "",
        "## Indicadores por coluna",
        "",
        (
            "| Pos. | Coluna | Presente | Null | NaN | Infinito | "
            "Domínio | Categoria desconhecida |"
        ),
        "| ---: | --- | :---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        (
            f"| {column.position} | {_markdown_schema_cell(column.name)} | "
            f"{_yes_no(column.present)} | {column.null_count} | "
            f"{column.nan_count} | {column.infinite_count} | "
            f"{column.domain_violation_count} | "
            f"{column.unknown_category_count} |"
        )
        for column in profile.columns
    )
    lines.extend(
        [
            "",
            "## Duplicatas e conflitos",
            "",
            (
                "- Chave declarada: "
                + ", ".join(
                    _markdown_schema_cell(key) for key in profile.duplicates.key_columns
                )
            ),
            (
                "- Grupos de duplicatas completas: "
                f"{profile.duplicates.complete_duplicate_group_count}"
            ),
            (
                "- Grupos de chaves duplicadas: "
                f"{profile.duplicates.duplicate_key_group_count}"
            ),
            (
                "- Grupos conflitantes: "
                f"{profile.duplicates.conflicting_key_group_count}"
            ),
            "",
            "## Rótulos",
            "",
            "| Rótulo agregado | Contagem | Percentual | Permitido |",
            "| --- | ---: | ---: | :---: |",
        ]
    )
    lines.extend(
        (
            f"| {_markdown_value_cell(category.label)} | {category.count} | "
            f"{_format_percentage(category.percentage)} | "
            f"{_optional_yes_no(category.is_allowed)} |"
        )
        for category in profile.labels.distribution
    )
    lines.extend(
        [
            "",
            "## Consistência de pares redundantes",
            "",
            "| Par | Comparáveis | Inconsistentes | Consistência |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    lines.extend(
        (
            f"| {_markdown_schema_cell(pair.left_column)} → "
            f"{_markdown_schema_cell(pair.right_column)} | {pair.comparable_count} | "
            f"{pair.inconsistent_count} | "
            f"{_format_percentage(pair.consistency_percentage)} |"
        )
        for pair in profile.redundant_unit_pairs
    )
    return "\n".join(lines) + "\n"


def validate_public_profile_schema(schema: PublicProfileField) -> None:
    """Recursively reject any public field classified as disclosure-prone."""

    if schema.classification not in _SAFE_FIELD_CLASSIFICATIONS:
        raise ProfilePrivacyError(
            "Public profile schema contains a disclosure-prone field "
            f"classification: {schema.classification.value}."
        )
    for child in schema.children:
        validate_public_profile_schema(child)


def _validate_profile_value_policy(profile: BannerDataProfile) -> None:
    public_labels: set[str] = set()
    for category in profile.labels.distribution:
        if category.label in public_labels:
            raise ProfilePrivacyError(
                "Public label distribution contains duplicate names."
            )
        public_labels.add(category.label)
        if category.is_allowed is True:
            if not category.label.strip():
                raise ProfilePrivacyError(
                    "Trusted public label must be a non-empty configured value."
                )
            continue
        if _OPAQUE_LABEL_PATTERN.fullmatch(category.label) is None:
            raise ProfilePrivacyError(
                "Unapproved public labels must use opaque aliases."
            )


def _validate_key_columns(key_columns: Sequence[str]) -> tuple[str, ...]:
    keys = tuple(cast(Sequence[object], key_columns))
    if not keys:
        raise BannerProfileConfigurationError(
            "At least one declared key column is required."
        )
    if any(not isinstance(key, str) for key in keys):
        raise BannerProfileConfigurationError(
            "Declared key columns must be contract column names."
        )
    string_keys = cast(tuple[str, ...], keys)
    if any(key not in BANNER_COLUMN_NAMES for key in string_keys):
        raise BannerProfileConfigurationError(
            "Declared key columns must belong to the banner contract."
        )
    if len(string_keys) != len(set(string_keys)):
        raise BannerProfileConfigurationError(
            "Declared key columns must not contain duplicates."
        )
    return string_keys


def _validate_allowed_fault_categories(
    allowed_fault_categories: Set[str] | None,
) -> frozenset[str] | None:
    if allowed_fault_categories is None:
        return None
    raw_allowed = tuple(cast(Set[object], allowed_fault_categories))
    if any(not isinstance(value, str) or not value.strip() for value in raw_allowed):
        raise BannerProfileConfigurationError(
            "Allowed fault categories must be non-empty strings."
        )
    return frozenset(cast(tuple[str, ...], raw_allowed))


def _profile_volume(dataframe: pd.DataFrame) -> VolumeProfile:
    actual_columns = tuple(dataframe.columns)
    present_count = sum(name in actual_columns for name in BANNER_COLUMN_NAMES)
    return VolumeProfile(
        row_count=len(dataframe),
        observed_column_count=len(actual_columns),
        expected_column_count=len(BANNER_COLUMN_NAMES),
        present_expected_column_count=present_count,
        missing_expected_column_count=len(BANNER_COLUMN_NAMES) - present_count,
        unexpected_column_count=sum(
            name not in BANNER_COLUMN_NAMES for name in actual_columns
        ),
        columns_in_contract_order=actual_columns == BANNER_COLUMN_NAMES,
    )


def _profile_time(dataframe: pd.DataFrame) -> TemporalProfile:
    if "created_at" not in dataframe.columns:
        return TemporalProfile(
            valid_timestamp_count=0,
            invalid_timestamp_count=0,
            distinct_timestamp_count=0,
            period_start_utc=None,
            period_end_utc=None,
            input_order=TemporalOrder.NOT_APPLICABLE,
            cadence_interval_count=0,
            nominal_cadence_seconds=None,
            irregular_interval_count=0,
            gap_count=0,
            total_gap_seconds=None,
            maximum_interval_seconds=None,
        )

    parsed: list[BannerUtcTimestamp] = []
    invalid_count = 0
    for value in dataframe["created_at"]:
        if _is_missing(value):
            continue
        timestamp = parse_banner_utc_timestamp(value)
        if timestamp is None:
            invalid_count += 1
        else:
            parsed.append(timestamp)

    if not parsed:
        return TemporalProfile(
            valid_timestamp_count=0,
            invalid_timestamp_count=invalid_count,
            distinct_timestamp_count=0,
            period_start_utc=None,
            period_end_utc=None,
            input_order=TemporalOrder.NOT_APPLICABLE,
            cadence_interval_count=0,
            nominal_cadence_seconds=None,
            irregular_interval_count=0,
            gap_count=0,
            total_gap_seconds=None,
            maximum_interval_seconds=None,
        )

    distinct = sorted(set(parsed))
    intervals = [right.seconds_since(left) for left, right in pairwise(distinct)]
    cadence = _nominal_cadence(intervals)
    irregular_count = (
        0 if cadence is None else sum(interval != cadence for interval in intervals)
    )
    gap_intervals = (
        []
        if cadence is None
        else [interval for interval in intervals if interval > cadence]
    )
    if cadence is None:
        total_gap = None
    else:
        with localcontext() as context:
            context.prec = _decimal_calculation_precision(intervals)
            gap_excess = sum(
                (interval - cadence for interval in gap_intervals), Decimal(0)
            )
        total_gap = _public_number(gap_excess)
    return TemporalProfile(
        valid_timestamp_count=len(parsed),
        invalid_timestamp_count=invalid_count,
        distinct_timestamp_count=len(distinct),
        period_start_utc=distinct[0].canonical_text(),
        period_end_utc=distinct[-1].canonical_text(),
        input_order=_temporal_order(parsed),
        cadence_interval_count=len(intervals),
        nominal_cadence_seconds=(None if cadence is None else _public_number(cadence)),
        irregular_interval_count=irregular_count,
        gap_count=len(gap_intervals),
        total_gap_seconds=total_gap,
        maximum_interval_seconds=(
            None if not intervals else _public_number(max(intervals))
        ),
    )


def _profile_column(
    dataframe: pd.DataFrame,
    column: BannerColumnContract,
    allowed_faults: frozenset[str] | None,
) -> ColumnProfile:
    present = column.name in dataframe.columns
    if not present:
        return ColumnProfile(
            position=column.position,
            name=column.name,
            logical_type=column.logical_type,
            present=False,
            dtype_matches_contract=False,
            observed_count=0,
            null_count=0,
            null_percentage=None,
            nan_count=0,
            nan_percentage=None,
            missing_count=0,
            missing_percentage=None,
            infinite_count=0,
            infinite_percentage=None,
            domain_violation_count=0,
            domain_violation_percentage=None,
            unknown_category_check_applied=(
                column.name == "fault" and allowed_faults is not None
            ),
            unknown_category_count=0,
            unknown_category_percentage=None,
            numeric_statistics=(
                _empty_numeric_statistics()
                if _numeric_statistics_allowed(column)
                else None
            ),
        )

    series = dataframe[column.name]
    values = tuple(series)
    observed_count = len(values)
    null_count = sum(_is_null(value) for value in values)
    nan_count = sum(_is_nan(value) for value in values)
    infinite_count = sum(_is_infinite(value) for value in values)
    domain_violation_count = sum(_violates_domain(value, column) for value in values)
    unknown_check = column.name == "fault" and allowed_faults is not None
    unknown_count = (
        sum(
            isinstance(value, str)
            and bool(value.strip())
            and value not in allowed_faults
            for value in values
        )
        if unknown_check and allowed_faults is not None
        else 0
    )
    missing_count = null_count + nan_count
    numeric_statistics = (
        _numeric_statistics(values) if _numeric_statistics_allowed(column) else None
    )
    return ColumnProfile(
        position=column.position,
        name=column.name,
        logical_type=column.logical_type,
        present=True,
        dtype_matches_contract=matches_banner_logical_type(series, column.logical_type),
        observed_count=observed_count,
        null_count=null_count,
        null_percentage=_percentage(null_count, observed_count),
        nan_count=nan_count,
        nan_percentage=_percentage(nan_count, observed_count),
        missing_count=missing_count,
        missing_percentage=_percentage(missing_count, observed_count),
        infinite_count=infinite_count,
        infinite_percentage=_percentage(infinite_count, observed_count),
        domain_violation_count=domain_violation_count,
        domain_violation_percentage=_percentage(domain_violation_count, observed_count),
        unknown_category_check_applied=unknown_check,
        unknown_category_count=unknown_count,
        unknown_category_percentage=(
            _percentage(unknown_count, observed_count) if unknown_check else None
        ),
        numeric_statistics=numeric_statistics,
    )


def _profile_duplicates(
    dataframe: pd.DataFrame, key_columns: tuple[str, ...]
) -> DuplicateProfile:
    try:
        return _profile_hashable_duplicates(dataframe, key_columns)
    except (TypeError, ValueError):
        raise BannerProfileInputError(
            "Duplicate aggregation requires hashable scalar dataframe cells."
        ) from None


def _profile_hashable_duplicates(
    dataframe: pd.DataFrame, key_columns: tuple[str, ...]
) -> DuplicateProfile:
    complete_duplicate_mask = dataframe.duplicated(keep=False)
    complete_duplicate_rows = dataframe.loc[complete_duplicate_mask]
    complete_group_count = (
        0
        if complete_duplicate_rows.empty
        else len(complete_duplicate_rows.drop_duplicates())
    )
    complete_excess_count = int(dataframe.duplicated(keep="first").sum())

    if any(key not in dataframe.columns for key in key_columns):
        return DuplicateProfile(
            key_columns=key_columns,
            key_columns_available=False,
            rows_with_incomplete_key_count=0,
            complete_duplicate_group_count=complete_group_count,
            complete_duplicate_excess_row_count=complete_excess_count,
            duplicate_key_group_count=0,
            duplicate_key_excess_row_count=0,
            conflicting_key_group_count=0,
            conflicting_row_count=0,
        )

    incomplete_key_mask = dataframe.loc[:, list(key_columns)].isna().any(axis=1)
    keyed = dataframe.loc[~incomplete_key_mask]
    duplicate_key_mask = keyed.duplicated(subset=list(key_columns), keep=False)
    duplicate_key_rows = keyed.loc[duplicate_key_mask]
    duplicate_key_group_count = (
        0
        if duplicate_key_rows.empty
        else len(duplicate_key_rows.loc[:, list(key_columns)].drop_duplicates())
    )
    duplicate_key_excess_count = int(
        keyed.duplicated(subset=list(key_columns), keep="first").sum()
    )
    conflicting_group_count = 0
    conflicting_row_count = 0
    if not duplicate_key_rows.empty:
        grouped = duplicate_key_rows.groupby(
            list(key_columns), dropna=False, sort=False
        )
        for _, group in grouped:
            if len(group.drop_duplicates()) > 1:
                conflicting_group_count += 1
                conflicting_row_count += len(group)

    return DuplicateProfile(
        key_columns=key_columns,
        key_columns_available=True,
        rows_with_incomplete_key_count=int(incomplete_key_mask.sum()),
        complete_duplicate_group_count=complete_group_count,
        complete_duplicate_excess_row_count=complete_excess_count,
        duplicate_key_group_count=duplicate_key_group_count,
        duplicate_key_excess_row_count=duplicate_key_excess_count,
        conflicting_key_group_count=conflicting_group_count,
        conflicting_row_count=conflicting_row_count,
    )


def _profile_labels(
    dataframe: pd.DataFrame, allowed_faults: frozenset[str] | None
) -> LabelDistributionProfile:
    if "fault" not in dataframe.columns:
        distribution = tuple(
            LabelCategoryProfile(label, 0, None, True)
            for label in sorted(allowed_faults or ())
        )
        return LabelDistributionProfile(
            source_column="fault",
            allowed_category_check_applied=allowed_faults is not None,
            allowed_category_count=(
                None if allowed_faults is None else len(allowed_faults)
            ),
            absent_allowed_category_count=(
                None if allowed_faults is None else len(allowed_faults)
            ),
            valid_label_count=0,
            missing_label_count=0,
            invalid_label_count=0,
            distinct_observed_label_count=0,
            distribution=distribution,
            majority_count=None,
            minority_count=None,
            majority_to_minority_ratio=None,
            normalized_entropy=None,
        )

    values = tuple(dataframe["fault"])
    labels = [
        value for value in values if isinstance(value, str) and bool(value.strip())
    ]
    counts = Counter(labels)
    valid_count = len(labels)
    trusted_categories = tuple(sorted(allowed_faults or ()))
    unapproved_counts = tuple(
        sorted(
            (
                count
                for label, count in counts.items()
                if allowed_faults is None or label not in allowed_faults
            ),
            reverse=True,
        )
    )
    alias_width = max(4, len(str(len(unapproved_counts))))
    opaque_aliases = _opaque_label_aliases(
        len(unapproved_counts),
        reserved=frozenset(counts) | frozenset(allowed_faults or ()),
        width=alias_width,
    )
    trusted_distribution = tuple(
        LabelCategoryProfile(
            label=label,
            count=counts[label],
            percentage=_percentage(counts[label], valid_count),
            is_allowed=True,
        )
        for label in trusted_categories
    )
    unapproved_distribution = tuple(
        LabelCategoryProfile(
            label=alias,
            count=count,
            percentage=_percentage(count, valid_count),
            is_allowed=(None if allowed_faults is None else False),
        )
        for alias, count in zip(opaque_aliases, unapproved_counts, strict=True)
    )
    distribution = trusted_distribution + unapproved_distribution
    category_counts = [category.count for category in distribution]
    majority = max(category_counts) if valid_count else None
    minority = min(category_counts) if valid_count else None
    if majority is None or minority in (None, 0):
        ratio = None
    else:
        with localcontext() as context:
            context.prec = max(50, len(str(majority)) + len(str(minority)) + 20)
            decimal_ratio = Decimal(majority) / Decimal(minority)
        ratio = _public_number(decimal_ratio)
    entropy = _normalized_entropy(category_counts, valid_count)
    return LabelDistributionProfile(
        source_column="fault",
        allowed_category_check_applied=allowed_faults is not None,
        allowed_category_count=(
            None if allowed_faults is None else len(allowed_faults)
        ),
        absent_allowed_category_count=(
            None
            if allowed_faults is None
            else sum(counts[label] == 0 for label in allowed_faults)
        ),
        valid_label_count=valid_count,
        missing_label_count=sum(_is_missing(value) for value in values),
        invalid_label_count=sum(
            not _is_missing(value) and (not isinstance(value, str) or not value.strip())
            for value in values
        ),
        distinct_observed_label_count=len(counts),
        distribution=distribution,
        majority_count=majority,
        minority_count=minority,
        majority_to_minority_ratio=ratio,
        normalized_entropy=entropy,
    )


def _opaque_label_aliases(
    count: int, *, reserved: frozenset[str], width: int
) -> tuple[str, ...]:
    aliases: list[str] = []
    candidate_number = 1
    while len(aliases) < count:
        candidate = f"unapproved_label_{candidate_number:0{width}d}"
        candidate_number += 1
        if candidate not in reserved:
            aliases.append(candidate)
    return tuple(aliases)


def _profile_unit_pair(
    dataframe: pd.DataFrame, spec: _UnitPairSpec
) -> UnitPairConsistencyProfile:
    if (
        spec.left_column not in dataframe.columns
        or spec.right_column not in dataframe.columns
    ):
        return UnitPairConsistencyProfile(
            left_column=spec.left_column,
            right_column=spec.right_column,
            relation=spec.relation,
            comparable_count=0,
            unavailable_count=len(dataframe),
            consistent_count=0,
            inconsistent_count=0,
            consistency_percentage=None,
            maximum_absolute_error=None,
        )

    errors: list[Decimal] = []
    consistent_count = 0
    for left, right in zip(
        dataframe[spec.left_column], dataframe[spec.right_column], strict=True
    ):
        left_number = _finite_decimal(left)
        right_number = _finite_decimal(right)
        if left_number is None or right_number is None:
            continue
        with localcontext() as context:
            context.prec = _decimal_calculation_precision(
                (left_number, right_number, spec.multiplier, spec.offset)
            )
            expected = left_number * spec.multiplier + spec.offset
            observed = right_number
            error = abs(observed - expected)
            is_consistent = _within_unit_tolerance(observed, expected)
        errors.append(error)
        if is_consistent:
            consistent_count += 1

    comparable_count = len(errors)
    inconsistent_count = comparable_count - consistent_count
    return UnitPairConsistencyProfile(
        left_column=spec.left_column,
        right_column=spec.right_column,
        relation=spec.relation,
        comparable_count=comparable_count,
        unavailable_count=len(dataframe) - comparable_count,
        consistent_count=consistent_count,
        inconsistent_count=inconsistent_count,
        consistency_percentage=_percentage(consistent_count, comparable_count),
        maximum_absolute_error=(None if not errors else _public_number(max(errors))),
    )


def _numeric_statistics(values: tuple[object, ...]) -> NumericStatistics:
    finite_values = sorted(
        number for value in values if (number := _finite_decimal(value)) is not None
    )
    if not finite_values:
        return _empty_numeric_statistics()

    with localcontext() as context:
        context.prec = _decimal_calculation_precision(finite_values)
        count = Decimal(len(finite_values))
        mean = sum(finite_values, Decimal(0)) / count
        variance = (
            sum(((value - mean) ** 2 for value in finite_values), Decimal(0)) / count
        )
        standard_deviation = variance.sqrt()
        quantile_25 = _linear_quantile(finite_values, _DECIMAL_QUANTILES[0])
        median = _linear_quantile(finite_values, _DECIMAL_QUANTILES[1])
        quantile_75 = _linear_quantile(finite_values, _DECIMAL_QUANTILES[2])
        iqr = quantile_75 - quantile_25
        lower = quantile_25 - _DECIMAL_IQR_MULTIPLIER * iqr
        upper = quantile_75 + _DECIMAL_IQR_MULTIPLIER * iqr
    return NumericStatistics(
        finite_count=len(finite_values),
        minimum=_public_number(finite_values[0]),
        maximum=_public_number(finite_values[-1]),
        mean=_public_number(mean),
        population_standard_deviation=_public_number(standard_deviation),
        quantile_25=_public_number(quantile_25),
        median=_public_number(median),
        quantile_75=_public_number(quantile_75),
        iqr=_public_number(iqr),
        iqr_lower_bound=_public_number(lower),
        iqr_upper_bound=_public_number(upper),
        iqr_outlier_count=sum(
            value < lower or value > upper for value in finite_values
        ),
    )


def _empty_numeric_statistics() -> NumericStatistics:
    return NumericStatistics(
        finite_count=0,
        minimum=None,
        maximum=None,
        mean=None,
        population_standard_deviation=None,
        quantile_25=None,
        median=None,
        quantile_75=None,
        iqr=None,
        iqr_lower_bound=None,
        iqr_upper_bound=None,
        iqr_outlier_count=0,
    )


def _linear_quantile(sorted_values: list[Decimal], probability: Decimal) -> Decimal:
    position = Decimal(len(sorted_values) - 1) * probability
    lower_index = int(position)
    upper_index = lower_index if position == lower_index else lower_index + 1
    if lower_index == upper_index:
        return sorted_values[lower_index]
    weight = position - Decimal(lower_index)
    return (
        sorted_values[lower_index] * (Decimal(1) - weight)
        + sorted_values[upper_index] * weight
    )


def _normalized_entropy(counts: list[int], total: int) -> float | None:
    if not counts or total == 0:
        return None
    if len(counts) == 1:
        return 0.0
    with localcontext() as context:
        context.prec = 80
        decimal_total = Decimal(total)
        probabilities = (
            Decimal(count) / decimal_total for count in counts if count > 0
        )
        entropy = -sum(
            (probability * probability.ln() for probability in probabilities),
            Decimal(0),
        )
        normalized = entropy / Decimal(len(counts)).ln()
    return _public_number(normalized)


def _numeric_statistics_allowed(column: BannerColumnContract) -> bool:
    return column.logical_type is LogicalType.FLOAT64


def _violates_domain(value: object, column: BannerColumnContract) -> bool:
    if _is_missing(value) or _is_infinite(value):
        return False
    return banner_value_violates_domain(value, column)


def _is_null(value: object) -> bool:
    return value is None or value is pd.NA or value is pd.NaT


def _is_nan(value: object) -> bool:
    if type(value) is bool:
        return False
    if not isinstance(value, Real):
        return False
    try:
        return isnan(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _is_missing(value: object) -> bool:
    return _is_null(value) or _is_nan(value)


def _is_infinite(value: object) -> bool:
    if type(value) is bool:
        return False
    if not isinstance(value, Real):
        return False
    try:
        return isinf(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _finite_float(value: object) -> float | None:
    if type(value) is bool or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _finite_decimal(value: object) -> Decimal | None:
    number = _finite_float(value)
    return None if number is None else Decimal.from_float(number)


def _temporal_order(values: list[BannerUtcTimestamp]) -> TemporalOrder:
    if len(values) < 2:
        return TemporalOrder.NOT_APPLICABLE
    nondecreasing = all(left <= right for left, right in pairwise(values))
    nonincreasing = all(left >= right for left, right in pairwise(values))
    if nondecreasing and nonincreasing:
        return TemporalOrder.CONSTANT
    if nondecreasing:
        return TemporalOrder.NONDECREASING
    if nonincreasing:
        return TemporalOrder.NONINCREASING
    return TemporalOrder.UNORDERED


def _nominal_cadence(intervals: list[Decimal]) -> Decimal | None:
    if not intervals:
        return None
    counts = Counter(intervals)
    highest_frequency = max(counts.values())
    return min(
        interval
        for interval, frequency in counts.items()
        if frequency == highest_frequency
    )


def _within_unit_tolerance(observed: Decimal, expected: Decimal) -> bool:
    difference = abs(observed - expected)
    permitted = max(
        _DECIMAL_UNIT_ABSOLUTE_TOLERANCE,
        _DECIMAL_UNIT_RELATIVE_TOLERANCE * max(abs(observed), abs(expected)),
    )
    return difference <= permitted


def _percentage(count: int, total: int) -> float | None:
    if total == 0:
        return None
    with localcontext() as context:
        context.prec = max(50, len(str(total)) * 2 + 20)
        value = Decimal(count) * Decimal(100) / Decimal(total)
    return _public_number(value)


def _public_number(decimal_value: Decimal) -> float | None:
    if not decimal_value.is_finite():
        return None
    try:
        with localcontext() as context:
            context.prec = max(
                34,
                abs(decimal_value.adjusted()) + PROFILE_DECIMAL_PLACES + 10,
                len(decimal_value.as_tuple().digits) + PROFILE_DECIMAL_PLACES + 10,
            )
            rounded = decimal_value.quantize(
                _ROUNDING_QUANTUM, rounding=ROUND_HALF_EVEN
            )
    except ArithmeticError:
        return None
    if decimal_value != 0 and rounded == 0:
        return None
    try:
        result = float(rounded)
    except (OverflowError, ValueError):
        return None
    if not isfinite(result):
        return None
    return 0.0 if result == 0 else result


def _decimal_calculation_precision(values: Sequence[Decimal]) -> int:
    maximum_adjusted = max(
        (value.adjusted() for value in values if value != 0), default=0
    )
    minimum_exponent = min(cast(int, value.as_tuple().exponent) for value in values)
    decimal_span = maximum_adjusted - minimum_exponent + 1
    return max(80, decimal_span * 2 + len(str(len(values))) + 32)


def _public_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        raw_dataclass = cast(dict[str, object], asdict(value))
        return {key: _public_value(item) for key, item in raw_dataclass.items()}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        raw_mapping = cast(Mapping[object, object], value)
        return {str(key): _public_value(item) for key, item in raw_mapping.items()}
    if isinstance(value, tuple | list):
        raw_sequence = cast(Sequence[object], value)
        return [_public_value(item) for item in raw_sequence]
    if isinstance(value, float) and not isfinite(value):
        raise ProfilePrivacyError("Public profile contains a non-finite number.")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ProfilePrivacyError("Public profile contains an unsupported value type.")


def _validate_public_payload(value: object, schema: PublicProfileField) -> None:
    if schema.children:
        if value is None:
            return
        if schema.sequence:
            if not isinstance(value, list):
                raise ProfilePrivacyError(
                    "Public profile sequence does not match its classified schema."
                )
            for item in cast(list[object], value):
                _validate_public_mapping(item, schema.children)
            return
        _validate_public_mapping(value, schema.children)
        return
    _reject_unclassified_container(value)


def _validate_public_mapping(
    value: object, fields: tuple[PublicProfileField, ...]
) -> None:
    if not isinstance(value, Mapping):
        raise ProfilePrivacyError(
            "Public profile object does not match its classified schema."
        )
    typed_value = cast(Mapping[str, object], value)
    expected_keys = tuple(field.name for field in fields)
    if tuple(typed_value.keys()) != expected_keys:
        raise ProfilePrivacyError(
            "Public profile fields do not exactly match the classified schema."
        )
    for field in fields:
        _validate_public_payload(typed_value[field.name], field)


def _reject_unclassified_container(value: object) -> None:
    if isinstance(value, Mapping):
        raise ProfilePrivacyError("Public profile contains an unclassified object.")
    if isinstance(value, list):
        for item in cast(list[object], value):
            if isinstance(item, Mapping | list):
                raise ProfilePrivacyError(
                    "Public profile contains an unclassified nested value."
                )


def _yes_no(value: bool) -> str:
    return "sim" if value else "não"


def _optional_yes_no(value: bool | None) -> str:
    return "não aplicável" if value is None else _yes_no(value)


def _format_optional_number(value: float | None) -> str:
    return "não disponível" if value is None else f"{value:.{PROFILE_DECIMAL_PLACES}f}"


def _format_percentage(value: float | None) -> str:
    return "não disponível" if value is None else f"{value:.{PROFILE_DECIMAL_PLACES}f}%"


def _markdown_period(profile: TemporalProfile) -> str:
    if profile.period_start_utc is None or profile.period_end_utc is None:
        return "não disponível"
    return f"{profile.period_start_utc} a {profile.period_end_utc}"


def _markdown_schema_cell(value: str) -> str:
    return escape(value.replace("\r", " ").replace("\n", " ")).replace("|", "\\|")


def _markdown_value_cell(value: str) -> str:
    sanitized = value.replace("\r", " ").replace("\n", " ")
    active_syntax = {
        "\\": "&#92;",
        "`": "&#96;",
        "*": "&#42;",
        "_": "&#95;",
        "{": "&#123;",
        "}": "&#125;",
        "[": "&#91;",
        "]": "&#93;",
        "(": "&#40;",
        ")": "&#41;",
        "#": "&#35;",
        "+": "&#43;",
        "-": "&#45;",
        ".": "&#46;",
        "!": "&#33;",
        "|": "&#124;",
        ">": "&gt;",
        "<": "&lt;",
        "&": "&amp;",
        ":": "&#58;",
        "/": "&#47;",
        "~": "&#126;",
    }
    return "".join(active_syntax.get(character, character) for character in sanitized)
