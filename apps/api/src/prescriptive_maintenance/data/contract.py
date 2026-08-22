"""Versioned, executable contract for the public banner table shape."""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from math import isfinite
from numbers import Integral, Real
from re import compile as compile_pattern
from typing import Any, Final, Protocol, cast

import pandas as pd
import pandera.pandas as pa
from pandas.api.types import is_string_dtype
from pandera.errors import SchemaErrors

from prescriptive_maintenance.data._decimal import isolated_decimal_context

BANNER_CONTRACT_VERSION: Final = 1


class LogicalType(StrEnum):
    """Logical dtypes accepted without implicit coercion."""

    INT64 = "int64"
    FLOAT64 = "float64"
    STRING = "string"
    UTC_TIMESTAMP_STRING = "utc_timestamp_string"


@dataclass(frozen=True, slots=True)
class BannerColumnContract:
    """Reviewable metadata for one position in the raw banner contract."""

    position: int
    name: str
    logical_type: LogicalType
    source_unit: str
    canonical_unit: str
    nullable: bool
    domain: str
    operational_description: str


@dataclass(frozen=True, order=True, slots=True)
class BannerUtcTimestamp:
    """Exactly parsed UTC instant, including arbitrary decimal precision."""

    whole_second_utc: datetime
    fractional_second: Decimal

    def seconds_since(self, earlier: BannerUtcTimestamp) -> Decimal:
        """Return an exact decimal difference without float conversion."""

        whole_delta = self.whole_second_utc - earlier.whole_second_utc
        whole_seconds = whole_delta.days * 86_400 + whole_delta.seconds
        fraction_places = max(
            0,
            -cast(int, self.fractional_second.as_tuple().exponent),
            -cast(int, earlier.fractional_second.as_tuple().exponent),
        )
        whole_digits = len(str(abs(whole_seconds))) if whole_seconds else 1
        precision = whole_digits + fraction_places + 4
        with localcontext(isolated_decimal_context(precision)):
            return (
                Decimal(whole_seconds)
                + self.fractional_second
                - earlier.fractional_second
            )

    def canonical_text(self) -> str:
        """Render UTC with at least microseconds and all significant precision."""

        fraction = format(self.fractional_second, "f").partition(".")[2]
        significant = fraction.rstrip("0")
        rendered_fraction = (
            significant if len(significant) > 6 else significant.ljust(6, "0")
        )
        whole = self.whole_second_utc
        return (
            f"{whole.year:04d}-{whole.month:02d}-{whole.day:02d}"
            f"T{whole.hour:02d}:{whole.minute:02d}:{whole.second:02d}"
            f".{rendered_fraction}Z"
        )


BANNER_COLUMN_CATALOG: Final[tuple[BannerColumnContract, ...]] = (
    BannerColumnContract(
        1,
        "id",
        LogicalType.INT64,
        "1",
        "1",
        False,
        "Inteiro assinado de 64 bits.",
        (
            "Identificador do registro fornecido pela fonte, sem pressupor "
            "sequência ou unicidade."
        ),
    ),
    BannerColumnContract(
        2,
        "created_at",
        LogicalType.UTC_TIMESTAMP_STRING,
        "UTC",
        "UTC",
        False,
        "Texto ISO 8601 em UTC, terminado por Z, com segundos e fração opcional.",
        "Instante de criação do registro conforme publicado pela fonte.",
    ),
    BannerColumnContract(
        3,
        "z_rms_velocity_in_s",
        LogicalType.FLOAT64,
        "in/s",
        "in/s",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Velocidade RMS no eixo z expressa em polegadas por segundo.",
    ),
    BannerColumnContract(
        4,
        "z_rms_velocity_mm_s",
        LogicalType.FLOAT64,
        "mm/s",
        "mm/s",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Velocidade RMS no eixo z expressa em milímetros por segundo.",
    ),
    BannerColumnContract(
        5,
        "temperature_f",
        LogicalType.FLOAT64,
        "°F",
        "°F",
        False,
        "Número float64 finito maior ou igual ao zero absoluto (-459,67 °F).",
        "Temperatura publicada pela fonte em graus Fahrenheit.",
    ),
    BannerColumnContract(
        6,
        "temperature_c",
        LogicalType.FLOAT64,
        "°C",
        "°C",
        False,
        "Número float64 finito maior ou igual ao zero absoluto (-273,15 °C).",
        "Temperatura publicada pela fonte em graus Celsius.",
    ),
    BannerColumnContract(
        7,
        "x_rms_velocity_in_s",
        LogicalType.FLOAT64,
        "in/s",
        "in/s",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Velocidade RMS no eixo x expressa em polegadas por segundo.",
    ),
    BannerColumnContract(
        8,
        "x_rms_velocity_mm_s",
        LogicalType.FLOAT64,
        "mm/s",
        "mm/s",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Velocidade RMS no eixo x expressa em milímetros por segundo.",
    ),
    BannerColumnContract(
        9,
        "z_peak_acceleration_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito.",
        "Aceleração de pico no eixo z expressa em múltiplos da gravidade padrão.",
    ),
    BannerColumnContract(
        10,
        "x_peak_acceleration_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito.",
        "Aceleração de pico no eixo x expressa em múltiplos da gravidade padrão.",
    ),
    BannerColumnContract(
        11,
        "z_peak_vel_comp_freq_hz",
        LogicalType.FLOAT64,
        "Hz",
        "Hz",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Frequência da componente de pico de velocidade no eixo z.",
    ),
    BannerColumnContract(
        12,
        "x_peak_vel_comp_freq_hz",
        LogicalType.FLOAT64,
        "Hz",
        "Hz",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Frequência da componente de pico de velocidade no eixo x.",
    ),
    BannerColumnContract(
        13,
        "z_rms_acceleration_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Aceleração RMS no eixo z expressa em múltiplos da gravidade padrão.",
    ),
    BannerColumnContract(
        14,
        "x_rms_acceleration_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito maior ou igual a zero.",
        "Aceleração RMS no eixo x expressa em múltiplos da gravidade padrão.",
    ),
    BannerColumnContract(
        15,
        "z_kurtosis",
        LogicalType.FLOAT64,
        "1",
        "1",
        False,
        "Número float64 finito.",
        "Curtose adimensional do sinal no eixo z conforme publicada pela fonte.",
    ),
    BannerColumnContract(
        16,
        "x_kurtosis",
        LogicalType.FLOAT64,
        "1",
        "1",
        False,
        "Número float64 finito.",
        "Curtose adimensional do sinal no eixo x conforme publicada pela fonte.",
    ),
    BannerColumnContract(
        17,
        "z_crest_factor",
        LogicalType.FLOAT64,
        "1",
        "1",
        False,
        "Número float64 finito.",
        "Fator de crista adimensional do sinal no eixo z.",
    ),
    BannerColumnContract(
        18,
        "x_crest_factor",
        LogicalType.FLOAT64,
        "1",
        "1",
        False,
        "Número float64 finito.",
        "Fator de crista adimensional do sinal no eixo x.",
    ),
    BannerColumnContract(
        19,
        "z_peak_velocity_in_s",
        LogicalType.FLOAT64,
        "in/s",
        "in/s",
        False,
        "Número float64 finito.",
        "Velocidade de pico no eixo z expressa em polegadas por segundo.",
    ),
    BannerColumnContract(
        20,
        "z_peak_velocity_mm_s",
        LogicalType.FLOAT64,
        "mm/s",
        "mm/s",
        False,
        "Número float64 finito.",
        "Velocidade de pico no eixo z expressa em milímetros por segundo.",
    ),
    BannerColumnContract(
        21,
        "x_peak_velocity_in_s",
        LogicalType.FLOAT64,
        "in/s",
        "in/s",
        False,
        "Número float64 finito.",
        "Velocidade de pico no eixo x expressa em polegadas por segundo.",
    ),
    BannerColumnContract(
        22,
        "x_peak_velocity_mm_s",
        LogicalType.FLOAT64,
        "mm/s",
        "mm/s",
        False,
        "Número float64 finito.",
        "Velocidade de pico no eixo x expressa em milímetros por segundo.",
    ),
    BannerColumnContract(
        23,
        "z_high_freq_rms_accel_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito maior ou igual a zero.",
        (
            "Aceleração RMS de alta frequência no eixo z em múltiplos da "
            "gravidade padrão."
        ),
    ),
    BannerColumnContract(
        24,
        "x_high_freq_rms_accel_g",
        LogicalType.FLOAT64,
        "g",
        "g",
        False,
        "Número float64 finito maior ou igual a zero.",
        (
            "Aceleração RMS de alta frequência no eixo x em múltiplos da "
            "gravidade padrão."
        ),
    ),
    BannerColumnContract(
        25,
        "fault",
        LogicalType.STRING,
        "1",
        "1",
        False,
        (
            "Rótulo bruto não vazio; uma allowlist só é aplicada quando "
            "fornecida explicitamente."
        ),
        (
            "Categoria bruta de falha, preservada sem normalização ou "
            "vocabulário real embutido."
        ),
    ),
    BannerColumnContract(
        26,
        "rpm",
        LogicalType.FLOAT64,
        "rpm",
        "rpm",
        False,
        "Número float64 finito.",
        "Rotação publicada pela fonte em revoluções por minuto.",
    ),
)

BANNER_COLUMN_NAMES: Final[tuple[str, ...]] = tuple(
    column.name for column in BANNER_COLUMN_CATALOG
)


class ValidationSeverity(StrEnum):
    """Severity carried by sanitized validation issues."""

    ERROR = "error"
    WARNING = "warning"


class ContractViolationCode(StrEnum):
    """Stable machine-readable codes for blocking contract violations."""

    COLUMN_MISSING = "contract.column_missing"
    COLUMN_EXTRA = "contract.column_extra"
    COLUMN_NAME_MISMATCH = "contract.column_name_mismatch"
    COLUMN_ORDER_MISMATCH = "contract.column_order_mismatch"
    DTYPE_MISMATCH = "contract.dtype_mismatch"
    NULL_NOT_ALLOWED = "contract.null_not_allowed"
    NAN_NOT_ALLOWED = "contract.nan_not_allowed"
    INFINITE_NOT_ALLOWED = "contract.infinite_not_allowed"
    TIMESTAMP_FORMAT = "contract.timestamp_format"
    EMPTY_FAULT = "contract.empty_fault"
    UNKNOWN_FAULT_CATEGORY = "contract.unknown_fault_category"
    PHYSICAL_LOWER_BOUND = "contract.physical_lower_bound"
    CHECK_FAILED = "contract.check_failed"


@dataclass(frozen=True, slots=True)
class ContractViolation:
    """Blocking, sanitized contract violation without row or cell content."""

    code: ContractViolationCode
    severity: ValidationSeverity
    column: str | None
    message: str


@dataclass(frozen=True, slots=True)
class StatisticalFinding:
    """Non-blocking finding type reserved for an explicit later profiling stage."""

    code: str
    severity: ValidationSeverity
    column: str | None
    message: str


@dataclass(frozen=True, slots=True)
class BannerValidationReport:
    """Sanitized result that keeps blocking contract errors apart from findings."""

    contract_version: int
    blocking_violations: tuple[ContractViolation, ...]
    statistical_findings: tuple[StatisticalFinding, ...]

    @property
    def is_valid(self) -> bool:
        """Return whether the blocking contract accepted the dataframe."""

        return not self.blocking_violations


class _SchemaErrorMetadata(Protocol):
    schema: object
    check: object


_UTC_TIMESTAMP_PATTERN: Final = compile_pattern(
    r"(?P<whole>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?Z"
)
_CHECK_FINITE: Final = "finite"
_CHECK_TIMESTAMP: Final = "utc_timestamp_format"
_CHECK_NON_EMPTY_FAULT: Final = "non_empty_fault"
_CHECK_ALLOWED_FAULT: Final = "allowed_fault_category"
_CHECK_PHYSICAL_MINIMUM: Final = "physical_lower_bound"

_PHYSICAL_MINIMUMS: Final[dict[str, float]] = {
    "z_rms_velocity_in_s": 0.0,
    "z_rms_velocity_mm_s": 0.0,
    "temperature_f": -459.67,
    "temperature_c": -273.15,
    "x_rms_velocity_in_s": 0.0,
    "x_rms_velocity_mm_s": 0.0,
    "z_peak_vel_comp_freq_hz": 0.0,
    "x_peak_vel_comp_freq_hz": 0.0,
    "z_rms_acceleration_g": 0.0,
    "x_rms_acceleration_g": 0.0,
    "z_high_freq_rms_accel_g": 0.0,
    "x_high_freq_rms_accel_g": 0.0,
}
_SIGNED_INT64_MIN: Final = -(2**63)
_SIGNED_INT64_MAX: Final = 2**63 - 1

_CHECK_CODES: Final[dict[str, ContractViolationCode]] = {
    _CHECK_TIMESTAMP: ContractViolationCode.TIMESTAMP_FORMAT,
    _CHECK_NON_EMPTY_FAULT: ContractViolationCode.EMPTY_FAULT,
    _CHECK_ALLOWED_FAULT: ContractViolationCode.UNKNOWN_FAULT_CATEGORY,
    _CHECK_PHYSICAL_MINIMUM: ContractViolationCode.PHYSICAL_LOWER_BOUND,
}

_CHECK_MESSAGES: Final[dict[ContractViolationCode, str]] = {
    ContractViolationCode.TIMESTAMP_FORMAT: (
        "Column contains text outside the declared UTC timestamp format."
    ),
    ContractViolationCode.EMPTY_FAULT: "Column contains an empty raw fault label.",
    ContractViolationCode.UNKNOWN_FAULT_CATEGORY: (
        "Column contains a category outside the explicit allowed set."
    ),
    ContractViolationCode.PHYSICAL_LOWER_BOUND: (
        "Column contains a value below its unequivocal physical lower bound."
    ),
    ContractViolationCode.CHECK_FAILED: "Column failed a declared contract check.",
}


def parse_banner_utc_timestamp(value: object) -> BannerUtcTimestamp | None:
    """Parse the contract UTC syntax without truncating fractional seconds."""

    if not isinstance(value, str):
        return None
    match = _UTC_TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        return None
    try:
        whole_second = datetime.fromisoformat(match.group("whole") + "+00:00")
        raw_fraction = match.group("fraction")
        fractional_second = (
            Decimal(0) if raw_fraction is None else Decimal(f"0.{raw_fraction}")
        )
    except (ValueError, ArithmeticError):
        return None
    return BannerUtcTimestamp(whole_second, fractional_second)


def matches_banner_logical_type(
    series: pd.Series[Any], logical_type: LogicalType
) -> bool:
    """Return whether a series dtype matches the canonical contract rule."""

    dtype_name = str(series.dtype).lower()
    if logical_type is LogicalType.INT64:
        return dtype_name == "int64"
    if logical_type is LogicalType.FLOAT64:
        return dtype_name == "float64"
    return bool(is_string_dtype(series))


def banner_value_violates_domain(value: object, column: BannerColumnContract) -> bool:
    """Apply the canonical scalar domain rule for a non-missing cell."""

    if column.logical_type is LogicalType.INT64:
        if type(value) is bool or not isinstance(value, Integral):
            return True
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError):
            return True
        return not (_SIGNED_INT64_MIN <= integer <= _SIGNED_INT64_MAX)

    if column.logical_type is LogicalType.FLOAT64:
        if type(value) is bool or not isinstance(value, Real):
            return True
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return True
        if not isfinite(number):
            return True
        minimum = _PHYSICAL_MINIMUMS.get(column.name)
        return minimum is not None and number < minimum

    if column.logical_type is LogicalType.UTC_TIMESTAMP_STRING:
        return parse_banner_utc_timestamp(value) is None

    return not isinstance(value, str) or not value.strip()


def build_banner_dataframe_schema(
    *, allowed_fault_categories: Set[str] | None = None
) -> pa.DataFrameSchema:
    """Build the v1 Pandera schema, optionally restricting raw fault labels."""

    allowed_faults = (
        None
        if allowed_fault_categories is None
        else frozenset(allowed_fault_categories)
    )
    columns = {
        column.name: _build_pandera_column(column, allowed_faults)
        for column in BANNER_COLUMN_CATALOG
    }
    return pa.DataFrameSchema(
        columns=columns,
        strict=True,
        ordered=True,
        coerce=False,
        name=f"banner_v{BANNER_CONTRACT_VERSION}",
    )


def validate_banner_dataframe(
    dataframe: pd.DataFrame,
    *,
    allowed_fault_categories: Set[str] | None = None,
) -> BannerValidationReport:
    """Validate without coercion and return only sanitized contract metadata."""

    violations = list(_validate_structure(dataframe))
    if violations:
        return _report(violations)

    violations.extend(_validate_missing_values(dataframe))
    if violations:
        return _report(violations)

    violations.extend(_validate_dtypes(dataframe))
    if violations:
        return _report(violations)

    violations.extend(_validate_infinite_values(dataframe))
    if violations:
        return _report(violations)

    schema = (
        BANNER_DATAFRAME_SCHEMA
        if allowed_fault_categories is None
        else build_banner_dataframe_schema(
            allowed_fault_categories=allowed_fault_categories
        )
    )
    try:
        schema.validate(dataframe, lazy=True)
    except SchemaErrors as error:
        violations.extend(_sanitize_schema_errors(error))

    return _report(violations)


def _build_pandera_column(
    column: BannerColumnContract, allowed_faults: frozenset[str] | None
) -> pa.Column:
    checks: list[pa.Check] = []
    if column.logical_type is LogicalType.FLOAT64:
        checks.append(pa.Check(_is_finite, element_wise=True, name=_CHECK_FINITE))
    if column.name in _PHYSICAL_MINIMUMS:
        checks.append(_domain_check(column, _CHECK_PHYSICAL_MINIMUM))
    if column.logical_type is LogicalType.UTC_TIMESTAMP_STRING:
        checks.append(_domain_check(column, _CHECK_TIMESTAMP))
    if column.name == "fault":
        checks.append(_domain_check(column, _CHECK_NON_EMPTY_FAULT))
        if allowed_faults is not None:
            checks.append(_allowed_fault_check(allowed_faults))

    if column.logical_type is LogicalType.INT64:
        dtype = "int64"
    elif column.logical_type is LogicalType.FLOAT64:
        dtype = "float64"
    else:
        dtype = "string"

    return pa.Column(dtype=dtype, checks=checks, nullable=column.nullable)


def _domain_check(column: BannerColumnContract, name: str) -> pa.Check:
    def is_in_domain(value: object) -> bool:
        return not banner_value_violates_domain(value, column)

    return pa.Check(
        is_in_domain,
        element_wise=True,
        name=name,
    )


def _allowed_fault_check(allowed_faults: frozenset[str]) -> pa.Check:
    def is_allowed(value: str) -> bool:
        return value in allowed_faults

    return pa.Check(
        is_allowed,
        element_wise=True,
        name=_CHECK_ALLOWED_FAULT,
    )


def _is_finite(value: float) -> bool:
    return isfinite(value)


def _validate_structure(dataframe: pd.DataFrame) -> tuple[ContractViolation, ...]:
    actual_columns = tuple(dataframe.columns)
    if actual_columns == BANNER_COLUMN_NAMES:
        return ()

    if len(actual_columns) == len(BANNER_COLUMN_NAMES):
        if set(actual_columns) == set(BANNER_COLUMN_NAMES):
            return (
                _violation(
                    ContractViolationCode.COLUMN_ORDER_MISMATCH,
                    None,
                    "Columns do not follow the declared contract order.",
                ),
            )
        return tuple(
            _violation(
                ContractViolationCode.COLUMN_NAME_MISMATCH,
                expected,
                "Column name does not match the contract at this position.",
            )
            for expected, actual in zip(
                BANNER_COLUMN_NAMES, actual_columns, strict=True
            )
            if expected != actual
        )

    violations = [
        _violation(
            ContractViolationCode.COLUMN_MISSING,
            expected,
            "Required contract column is missing.",
        )
        for expected in BANNER_COLUMN_NAMES
        if expected not in actual_columns
    ]
    if any(actual not in BANNER_COLUMN_NAMES for actual in actual_columns):
        violations.append(
            _violation(
                ContractViolationCode.COLUMN_EXTRA,
                None,
                "One or more columns are not declared by the contract.",
            )
        )
    if not violations:
        violations.append(
            _violation(
                ContractViolationCode.COLUMN_NAME_MISMATCH,
                None,
                "Column names do not match the declared contract.",
            )
        )
    return tuple(violations)


def _validate_dtypes(dataframe: pd.DataFrame) -> tuple[ContractViolation, ...]:
    return tuple(
        _violation(
            ContractViolationCode.DTYPE_MISMATCH,
            column.name,
            "Column dtype does not match the declared logical type.",
        )
        for column in BANNER_COLUMN_CATALOG
        if not matches_banner_logical_type(dataframe[column.name], column.logical_type)
    )


def _validate_missing_values(
    dataframe: pd.DataFrame,
) -> tuple[ContractViolation, ...]:
    violations: list[ContractViolation] = []
    for column in BANNER_COLUMN_CATALOG:
        series = dataframe[column.name]
        if bool(series.isna().any()):
            code = (
                ContractViolationCode.NAN_NOT_ALLOWED
                if column.logical_type is LogicalType.FLOAT64
                else ContractViolationCode.NULL_NOT_ALLOWED
            )
            message = (
                "Numeric column contains NaN."
                if code is ContractViolationCode.NAN_NOT_ALLOWED
                else "Column contains a null value."
            )
            violations.append(_violation(code, column.name, message))
    return tuple(violations)


def _validate_infinite_values(
    dataframe: pd.DataFrame,
) -> tuple[ContractViolation, ...]:
    return tuple(
        _violation(
            ContractViolationCode.INFINITE_NOT_ALLOWED,
            column.name,
            "Numeric column contains an infinite value.",
        )
        for column in BANNER_COLUMN_CATALOG
        if column.logical_type is LogicalType.FLOAT64
        and bool(dataframe[column.name].isin((float("inf"), float("-inf"))).any())
    )


def _sanitize_schema_errors(error: SchemaErrors) -> tuple[ContractViolation, ...]:
    violations: list[ContractViolation] = []
    seen: set[tuple[ContractViolationCode, str | None]] = set()
    schema_errors = cast(list[_SchemaErrorMetadata], error.schema_errors)
    for schema_error in schema_errors:
        raw_column = getattr(schema_error.schema, "name", None)
        column = (
            raw_column
            if isinstance(raw_column, str) and raw_column in BANNER_COLUMN_NAMES
            else None
        )
        check_name = getattr(schema_error.check, "name", None)
        code = (
            _CHECK_CODES.get(check_name, ContractViolationCode.CHECK_FAILED)
            if isinstance(check_name, str)
            else ContractViolationCode.CHECK_FAILED
        )
        key = (code, column)
        if key in seen:
            continue
        seen.add(key)
        violations.append(
            _violation(
                code,
                column,
                _CHECK_MESSAGES.get(
                    code, _CHECK_MESSAGES[ContractViolationCode.CHECK_FAILED]
                ),
            )
        )
    return tuple(violations)


def _violation(
    code: ContractViolationCode, column: str | None, message: str
) -> ContractViolation:
    return ContractViolation(
        code=code,
        severity=ValidationSeverity.ERROR,
        column=column,
        message=message,
    )


def _report(violations: list[ContractViolation]) -> BannerValidationReport:
    return BannerValidationReport(
        contract_version=BANNER_CONTRACT_VERSION,
        blocking_violations=tuple(violations),
        statistical_findings=(),
    )


BANNER_DATAFRAME_SCHEMA: Final = build_banner_dataframe_schema()
