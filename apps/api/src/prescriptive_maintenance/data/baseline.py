"""Deterministic, fail-closed publication of the audited banner baseline."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from math import isfinite
from pathlib import Path
from typing import BinaryIO, Final, cast
from unicodedata import category as unicode_category
from warnings import catch_warnings, simplefilter

import pandas as pd

from prescriptive_maintenance.data.contract import (
    BANNER_COLUMN_CATALOG,
    BANNER_COLUMN_NAMES,
    BANNER_CONTRACT_VERSION,
    BannerValidationReport,
    ContractViolationCode,
    LogicalType,
    ValidationSeverity,
    parse_banner_utc_timestamp,
    validate_banner_dataframe,
)
from prescriptive_maintenance.data.profiling import (
    BANNER_PROFILE_SCHEMA_VERSION,
    BANNER_REDUNDANT_UNIT_PAIR_IDENTITIES,
    PUBLIC_BANNER_PROFILE_SCHEMA,
    ProfileFieldClassification,
    ProfilePrivacyError,
    PublicProfileField,
    TemporalOrder,
    banner_profile_json_bytes,
    profile_banner_dataframe,
    validate_public_banner_profile_payload,
    validate_public_profile_schema,
)
from prescriptive_maintenance.data.source import (
    BannerSourceError,
    BannerSourceFingerprint,
    BannerSourceReceipt,
    SourceAccessError,
    SourceChangedError,
    SourceHashMismatchError,
    SourceManifestError,
    SourceNotFoundError,
    SourcePermissionError,
    SourceSizeMismatchError,
    UnexpectedSourceNameError,
    consume_banner_source_audited,
)

BASELINE_SCHEMA_VERSION: Final = 1
BASELINE_RUNNER_VERSION: Final = 1
BASELINE_SANITIZER_VERSION: Final = 1
BASELINE_CLASSIFICATION_VERSION: Final = 1

EXPECTED_BANNER_ROW_COUNT: Final = 166_796
EXPECTED_BANNER_COLUMN_COUNT: Final = 26
EXPECTED_RAW_FAULT_CARDINALITY: Final = 151

_BANNER_BASENAME: Final = "banner.csv"
_SUPPORTED_MANIFEST_SCHEMA_VERSION: Final = 1
_SUPPORTED_HASH_ALGORITHM: Final = "sha256"
_ROUND_COUNT: Final = 2
_BASELINE_JSON_FILENAME: Final = "baseline.v1.json"
_BASELINE_MARKDOWN_FILENAME: Final = "summary.md"
_EXPECTED_ARTIFACT_FILENAMES: Final = frozenset(
    {_BASELINE_JSON_FILENAME, _BASELINE_MARKDOWN_FILENAME}
)
_STAGING_PREFIX: Final = ".banner-baseline-"
_SAFE_CLASSIFICATIONS: Final = frozenset(
    {
        ProfileFieldClassification.AGGREGATE,
        ProfileFieldClassification.CONFIGURATION,
        ProfileFieldClassification.SCHEMA,
    }
)
_UNSAFE_UNICODE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN: Final = re.compile(
    r"[0-9]+(?:\.[0-9]+)+(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
    r"(?:\+[0-9a-z]+(?:[.-][0-9a-z]+)*)?"
)
_CANONICAL_CONTRACT_VIOLATION_CODES: Final = frozenset(
    code.value for code in ContractViolationCode
)
_WINDOWS_ABSOLUTE_PATH_PATTERN: Final = re.compile(r"^[A-Za-z]:[\\/]")
_SECRET_ASSIGNMENT_PATTERN: Final = re.compile(
    r"(?i)(?:api[_-]?key|password|secret|token|bearer)\s*[:=]\s*\S+"
)
_SECRET_TOKEN_PATTERN: Final = re.compile(
    r"(?i)\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_]{8,}\b"
)
_URI_CREDENTIAL_PATTERN: Final = re.compile(r"(?i)^[a-z][a-z0-9+.-]*://[^/@:]+:[^/@]+@")


class BannerBaselineError(Exception):
    """Base class for sanitized baseline runner failures."""


class BannerBaselinePrivacyError(BannerBaselineError):
    """Raised when a public payload or artifact fails closed."""


class BannerBaselineStatus(StrEnum):
    """Terminal status of an in-memory baseline run."""

    PASSED = "passed"
    BLOCKED = "blocked"


class IndicatorClassification(StrEnum):
    """Quality handling assigned to each published indicator."""

    BLOCKING = "blocking"
    ALERT = "alert"
    OBSERVATION = "observation"


@dataclass(frozen=True, slots=True)
class BannerBaselineRunResult:
    """Safe result of two independent rounds and optional artifact writing."""

    status: BannerBaselineStatus
    failure_codes: tuple[str, ...]
    json_bytes: bytes | None
    markdown_bytes: bytes | None
    output_directory: Path | None


@dataclass(frozen=True, slots=True)
class BaselinePublicField:
    """One recursively classified field in the public baseline envelope."""

    name: str
    classification: ProfileFieldClassification
    children: tuple[BaselinePublicField, ...] = ()
    sequence: bool = False
    profile_schema: PublicProfileField | None = None


@dataclass(frozen=True, slots=True)
class _ManifestIdentity:
    schema_version: int
    basename: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _RoundAnalysis:
    contract_report: dict[str, object]
    profile: dict[str, object]


class _RoundFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _field(
    name: str,
    classification: ProfileFieldClassification,
    *children: BaselinePublicField,
    sequence: bool = False,
    profile_schema: PublicProfileField | None = None,
) -> BaselinePublicField:
    return BaselinePublicField(
        name=name,
        classification=classification,
        children=children,
        sequence=sequence,
        profile_schema=profile_schema,
    )


_SCHEMA = ProfileFieldClassification.SCHEMA
_CONFIGURATION = ProfileFieldClassification.CONFIGURATION
_AGGREGATE = ProfileFieldClassification.AGGREGATE

_FINGERPRINT_SCHEMA: Final = (
    _field("size_bytes", _AGGREGATE),
    _field("sha256", _SCHEMA),
    _field("status", _AGGREGATE),
)
_INTEGRITY_ROUND_SCHEMA: Final = (
    _field("round", _CONFIGURATION),
    _field("pre", _AGGREGATE, *_FINGERPRINT_SCHEMA),
    _field("post", _AGGREGATE, *_FINGERPRINT_SCHEMA),
)
_DTYPE_SCHEMA: Final = (
    _field("column", _SCHEMA),
    _field("dtype", _CONFIGURATION),
)
_CONTRACT_ISSUE_SCHEMA: Final = (
    _field("code", _SCHEMA),
    _field("severity", _SCHEMA),
    _field("column", _SCHEMA),
)
_GATE_SCHEMA: Final = (
    _field("code", _SCHEMA),
    _field("classification", _SCHEMA),
    _field("passed", _AGGREGATE),
    _field("finding_count", _AGGREGATE),
)
_RECONCILIATION_SCHEMA: Final = (
    _field("code", _SCHEMA),
    _field("subject", _SCHEMA),
    _field("expected", _AGGREGATE),
    _field("actual", _AGGREGATE),
    _field("passed", _AGGREGATE),
)

PUBLIC_BANNER_BASELINE_SCHEMA: Final = _field(
    "banner_baseline",
    _AGGREGATE,
    _field("baseline_schema_version", _SCHEMA),
    _field(
        "versions",
        _SCHEMA,
        _field("baseline_schema", _SCHEMA),
        _field("contract", _SCHEMA),
        _field("profile_schema", _SCHEMA),
        _field("runner", _SCHEMA),
        _field("sanitizer", _SCHEMA),
        _field("classification", _SCHEMA),
    ),
    _field(
        "source",
        _SCHEMA,
        _field("basename", _SCHEMA),
        _field("sha256", _SCHEMA),
    ),
    _field(
        "manifest",
        _SCHEMA,
        _field("schema_version", _SCHEMA),
        _field("hash_algorithm", _SCHEMA),
    ),
    _field(
        "integrity",
        _AGGREGATE,
        _field("round_count", _CONFIGURATION),
        _field("rounds", _AGGREGATE, *_INTEGRITY_ROUND_SCHEMA, sequence=True),
    ),
    _field(
        "tooling",
        _CONFIGURATION,
        _field("python", _CONFIGURATION),
        _field("pandas", _CONFIGURATION),
        _field("pandera", _CONFIGURATION),
    ),
    _field(
        "parser",
        _CONFIGURATION,
        _field("format", _CONFIGURATION),
        _field("engine", _CONFIGURATION),
        _field("encoding", _CONFIGURATION),
        _field("encoding_errors", _CONFIGURATION),
        _field("delimiter", _CONFIGURATION),
        _field("header_row", _CONFIGURATION),
        _field("index_column", _CONFIGURATION),
        _field("use_columns", _CONFIGURATION),
        _field("dtypes", _CONFIGURATION, *_DTYPE_SCHEMA, sequence=True),
        _field("complete_dtypes", _CONFIGURATION, *_DTYPE_SCHEMA, sequence=True),
        _field("id_finalization", _CONFIGURATION),
        _field("parse_dates", _CONFIGURATION),
        _field("date_format", _CONFIGURATION),
        _field("day_first", _CONFIGURATION),
        _field("cache_dates", _CONFIGURATION),
        _field("low_memory", _CONFIGURATION),
        _field("iterator", _CONFIGURATION),
        _field("chunk_size", _CONFIGURATION),
        _field("on_bad_lines", _CONFIGURATION),
        _field("skip_blank_lines", _CONFIGURATION),
        _field("skip_initial_space", _CONFIGURATION),
        _field("na_filter", _CONFIGURATION),
        _field("keep_default_na", _CONFIGURATION),
        _field("na_values", _CONFIGURATION),
        _field("decimal", _CONFIGURATION),
        _field("thousands", _CONFIGURATION),
        _field("quote_character", _CONFIGURATION),
        _field("quoting", _CONFIGURATION),
        _field("double_quote", _CONFIGURATION),
        _field("escape_character", _CONFIGURATION),
        _field("comment_character", _CONFIGURATION),
        _field("line_terminator", _CONFIGURATION),
        _field("compression", _CONFIGURATION),
        _field("memory_map", _CONFIGURATION),
        _field("float_precision", _CONFIGURATION),
    ),
    _field(
        "expectations",
        _CONFIGURATION,
        _field("row_count", _CONFIGURATION),
        _field("column_count", _CONFIGURATION),
        _field("raw_fault_cardinality", _CONFIGURATION),
    ),
    _field("gates", _AGGREGATE, *_GATE_SCHEMA, sequence=True),
    _field(
        "contract_report",
        _AGGREGATE,
        _field("contract_version", _SCHEMA),
        _field("passed", _AGGREGATE),
        _field("blocking_violation_count", _AGGREGATE),
        _field(
            "blocking_violations",
            _AGGREGATE,
            *_CONTRACT_ISSUE_SCHEMA,
            sequence=True,
        ),
        _field("statistical_finding_count", _AGGREGATE),
        _field(
            "statistical_findings",
            _AGGREGATE,
            *_CONTRACT_ISSUE_SCHEMA,
            sequence=True,
        ),
    ),
    _field(
        "profile",
        _AGGREGATE,
        profile_schema=PUBLIC_BANNER_PROFILE_SCHEMA,
    ),
    _field(
        "reconciliations",
        _AGGREGATE,
        *_RECONCILIATION_SCHEMA,
        sequence=True,
    ),
    _field("result", _AGGREGATE),
)

_INDICATOR_CLASSIFICATIONS: Final[tuple[tuple[str, IndicatorClassification], ...]] = (
    ("integrity.source", IndicatorClassification.BLOCKING),
    ("parsing.csv", IndicatorClassification.BLOCKING),
    ("contract.banner", IndicatorClassification.BLOCKING),
    ("expectation.dimensions", IndicatorClassification.BLOCKING),
    ("expectation.fault_cardinality", IndicatorClassification.BLOCKING),
    ("determinism.temporal_arithmetic", IndicatorClassification.BLOCKING),
    ("sanitization.public_payload", IndicatorClassification.BLOCKING),
    ("reconciliation.aggregate_counts", IndicatorClassification.BLOCKING),
    ("classification.public_fields", IndicatorClassification.BLOCKING),
    ("determinism.byte_equality", IndicatorClassification.BLOCKING),
    ("quality.complete_duplicates", IndicatorClassification.ALERT),
    ("quality.repeated_ids", IndicatorClassification.ALERT),
    ("quality.conflicting_ids", IndicatorClassification.ALERT),
    ("quality.irregular_cadence", IndicatorClassification.ALERT),
    ("quality.temporal_gaps", IndicatorClassification.ALERT),
    ("quality.redundant_pairs", IndicatorClassification.ALERT),
    ("observation.temporal_period", IndicatorClassification.OBSERVATION),
    ("observation.column_statistics", IndicatorClassification.OBSERVATION),
    ("observation.iqr_outliers", IndicatorClassification.OBSERVATION),
    ("observation.anonymous_fault_distribution", IndicatorClassification.OBSERVATION),
    ("observation.fault_imbalance", IndicatorClassification.OBSERVATION),
    ("observation.redundant_pair_consistency", IndicatorClassification.OBSERVATION),
)

_PARSER_READ_DTYPES: Final[tuple[tuple[str, str], ...]] = tuple(
    (
        column.name,
        {
            LogicalType.INT64: "Int64",
            LogicalType.FLOAT64: "float64",
            LogicalType.STRING: "string",
            LogicalType.UTC_TIMESTAMP_STRING: "string",
        }[column.logical_type],
    )
    for column in BANNER_COLUMN_CATALOG
)
_PARSER_COMPLETE_DTYPES: Final[tuple[tuple[str, str], ...]] = tuple(
    (
        column.name,
        {
            LogicalType.INT64: "int64",
            LogicalType.FLOAT64: "float64",
            LogicalType.STRING: "string",
            LogicalType.UTC_TIMESTAMP_STRING: "string",
        }[column.logical_type],
    )
    for column in BANNER_COLUMN_CATALOG
)


def run_banner_baseline(
    *, input_path: Path, manifest_path: Path, output_root: Path
) -> BannerBaselineRunResult:
    """Run two independent audited rounds and publish only an approved baseline."""

    try:
        identity = _load_manifest_identity(manifest_path)
    except BannerBaselineError:
        return _blocked_result("baseline.manifest_invalid")

    receipts: list[BannerSourceReceipt[_RoundAnalysis]] = []
    for _ in range(_ROUND_COUNT):
        try:
            receipt = consume_banner_source_audited(
                input_path=input_path,
                manifest_path=manifest_path,
                consumer=_analyze_descriptor,
            )
        except BannerSourceError as error:
            return _blocked_result(_source_failure_code(error))
        except _RoundFailure as error:
            return _blocked_result(error.code)
        except Exception:
            return _blocked_result("baseline.runner_failed")
        receipts.append(receipt)

    if not _receipts_match_identity(identity, receipts):
        return _blocked_result("baseline.integrity_receipt_mismatch")

    integrity = _integrity_payload(receipts)
    try:
        first_payload = _build_payload(identity, integrity, receipts[0].result)
        second_payload = _build_payload(identity, integrity, receipts[1].result)
        first_json = _serialize_public_payload(first_payload, identity)
        second_json = _serialize_public_payload(second_payload, identity)
        first_markdown = render_banner_baseline_markdown(first_json)
        second_markdown = render_banner_baseline_markdown(second_json)
    except BannerBaselineError:
        return _blocked_result("baseline.sanitization_failed")
    except Exception:
        return _blocked_result("baseline.serialization_failed")

    if first_json != second_json or first_markdown != second_markdown:
        return _blocked_result("baseline.byte_equality_failed")
    if render_banner_baseline_markdown(first_json) != first_markdown:
        return _blocked_result("baseline.markdown_regeneration_failed")

    payload = _load_json_object(first_json)
    status = BannerBaselineStatus(_required_string(payload, "result"))
    failure_codes = _blocking_failure_codes(payload)
    if status is BannerBaselineStatus.BLOCKED:
        return BannerBaselineRunResult(
            status=status,
            failure_codes=failure_codes,
            json_bytes=first_json,
            markdown_bytes=first_markdown,
            output_directory=None,
        )

    try:
        output_directory = _write_artifacts_atomically(
            output_root=output_root,
            source_sha256=identity.sha256,
            json_bytes=first_json,
            markdown_bytes=first_markdown,
        )
    except BannerBaselineError:
        return BannerBaselineRunResult(
            status=BannerBaselineStatus.BLOCKED,
            failure_codes=("baseline.output_write_failed",),
            json_bytes=first_json,
            markdown_bytes=first_markdown,
            output_directory=None,
        )

    return BannerBaselineRunResult(
        status=BannerBaselineStatus.PASSED,
        failure_codes=(),
        json_bytes=first_json,
        markdown_bytes=first_markdown,
        output_directory=output_directory,
    )


def render_banner_baseline_markdown(json_bytes: bytes) -> bytes:
    """Derive deterministic Markdown exclusively from sanitized JSON bytes."""

    payload = _load_json_object(json_bytes)
    _validate_public_payload(payload, expected_identity=None)

    source = _required_mapping(payload, "source")
    integrity = _required_mapping(payload, "integrity")
    expectations = _required_mapping(payload, "expectations")
    contract = _required_mapping(payload, "contract_report")
    profile = _required_mapping(payload, "profile")
    volume = _required_mapping(profile, "volume")
    temporal = _required_mapping(profile, "temporal")
    duplicates = _required_mapping(profile, "duplicates")
    labels = _required_mapping(profile, "labels")
    gates = _required_sequence(payload, "gates")
    reconciliations = _required_sequence(payload, "reconciliations")
    columns = _required_sequence(profile, "columns")
    pairs = _required_sequence(profile, "redundant_unit_pairs")

    lines = [
        "# Baseline agregada auditada do banner",
        "",
        "## Identidade e resultado",
        "",
        f"- Resultado: `{_markdown_cell(_required_string(payload, 'result'))}`",
        f"- Fonte: `{_markdown_cell(_required_string(source, 'basename'))}`",
        f"- SHA-256 aprovado: `{_required_string(source, 'sha256')}`",
        f"- Rodadas independentes: {_required_int(integrity, 'round_count')}",
        "",
        "## Expectativas",
        "",
        (
            "- Dimensão observada/esperada: "
            f"{_required_int(volume, 'row_count')}x"
            f"{_required_int(volume, 'observed_column_count')} / "
            f"{_required_int(expectations, 'row_count')}x"
            f"{_required_int(expectations, 'column_count')}"
        ),
        (
            "- Cardinalidade bruta anônima de `fault`: "
            f"{_required_int(labels, 'distinct_observed_label_count')} / "
            f"{_required_int(expectations, 'raw_fault_cardinality')}"
        ),
        f"- Contrato: {'passed' if _required_bool(contract, 'passed') else 'blocked'}",
        "",
        "## Gates e classificações",
        "",
        "| Indicador | Classificação | Passou | Achados |",
        "| --- | --- | :---: | ---: |",
    ]
    for item in gates:
        gate = _as_mapping(item)
        lines.append(
            f"| `{_markdown_cell(_required_string(gate, 'code'))}` | "
            f"{_markdown_cell(_required_string(gate, 'classification'))} | "
            f"{_yes_no(_required_bool(gate, 'passed'))} | "
            f"{_required_int(gate, 'finding_count')} |"
        )

    lines.extend(
        [
            "",
            "## Perfil agregado",
            "",
            (
                "- Período UTC agregado: "
                f"{_optional_markdown_value(temporal.get('period_start_utc'))} a "
                f"{_optional_markdown_value(temporal.get('period_end_utc'))}"
            ),
            (
                "- Cadência nominal (s): "
                f"{_optional_markdown_value(temporal.get('nominal_cadence_seconds'))}"
            ),
            (
                "- Intervalos irregulares: "
                f"{_required_int(temporal, 'irregular_interval_count')}"
            ),
            f"- Lacunas: {_required_int(temporal, 'gap_count')}",
            (
                "- Duplicatas completas excedentes: "
                f"{_required_int(duplicates, 'complete_duplicate_excess_row_count')}"
            ),
            (
                "- Grupos de IDs repetidos: "
                f"{_required_int(duplicates, 'duplicate_key_group_count')}"
            ),
            (
                "- Grupos de IDs conflitantes: "
                f"{_required_int(duplicates, 'conflicting_key_group_count')}"
            ),
            "",
            "### Estatísticas numéricas e IQR",
            "",
            "| Coluna | Finitos | Mínimo | Máximo | Média | IQR | Outliers IQR |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in columns:
        column = _as_mapping(item)
        statistics = column.get("numeric_statistics")
        if statistics is None:
            continue
        numeric = _as_mapping(statistics)
        lines.append(
            f"| `{_markdown_cell(_required_string(column, 'name'))}` | "
            f"{_required_int(numeric, 'finite_count')} | "
            f"{_optional_markdown_value(numeric.get('minimum'))} | "
            f"{_optional_markdown_value(numeric.get('maximum'))} | "
            f"{_optional_markdown_value(numeric.get('mean'))} | "
            f"{_optional_markdown_value(numeric.get('iqr'))} | "
            f"{_required_int(numeric, 'iqr_outlier_count')} |"
        )

    lines.extend(
        [
            "",
            "### Distribuição anônima de rótulos",
            "",
            "| Categoria anônima | Contagem | Percentual |",
            "| --- | ---: | ---: |",
        ]
    )
    for item in _required_sequence(labels, "distribution"):
        category = _as_mapping(item)
        ordinal = _required_int(category, "unapproved_ordinal")
        lines.append(
            f"| categoria {ordinal} | {_required_int(category, 'count')} | "
            f"{_optional_markdown_value(category.get('percentage'))} |"
        )

    lines.extend(
        [
            "",
            "### Pares redundantes",
            "",
            "| Par | Comparáveis | Consistentes | Inconsistentes |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for item in pairs:
        pair = _as_mapping(item)
        left = _markdown_cell(_required_string(pair, "left_column"))
        right = _markdown_cell(_required_string(pair, "right_column"))
        lines.append(
            f"| `{left}` → `{right}` | {_required_int(pair, 'comparable_count')} | "
            f"{_required_int(pair, 'consistent_count')} | "
            f"{_required_int(pair, 'inconsistent_count')} |"
        )

    lines.extend(
        [
            "",
            "## Reconciliações",
            "",
            "| Código | Escopo | Esperado | Observado | Passou |",
            "| --- | --- | ---: | ---: | :---: |",
        ]
    )
    for item in reconciliations:
        reconciliation = _as_mapping(item)
        subject = reconciliation.get("subject")
        subject_text = "—" if subject is None else _markdown_cell(_as_string(subject))
        lines.append(
            f"| `{_markdown_cell(_required_string(reconciliation, 'code'))}` | "
            f"{subject_text} | {_required_int(reconciliation, 'expected')} | "
            f"{_required_int(reconciliation, 'actual')} | "
            f"{_yes_no(_required_bool(reconciliation, 'passed'))} |"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_banner_baseline_bytes(*, json_bytes: bytes, manifest_path: Path) -> None:
    """Validate canonical aggregate bytes against the approved manifest identity."""

    try:
        identity = _load_manifest_identity(manifest_path)
    except (OSError, BannerBaselineError):
        raise BannerBaselinePrivacyError(
            "Public baseline identity is unavailable."
        ) from None
    payload = _load_json_object(json_bytes)
    _validate_approved_baseline_payload(payload, identity)


def validate_banner_baseline_artifacts(
    *, json_path: Path, markdown_path: Path, manifest_path: Path
) -> None:
    """Validate tracked aggregate artifacts without accessing the local source."""

    try:
        identity = _load_manifest_identity(manifest_path)
    except OSError:
        raise BannerBaselinePrivacyError(
            "Public baseline artifacts are unavailable."
        ) from None
    if json_path.name != _BASELINE_JSON_FILENAME:
        raise BannerBaselinePrivacyError("Published baseline filename is invalid.")
    if markdown_path.name != _BASELINE_MARKDOWN_FILENAME:
        raise BannerBaselinePrivacyError("Published summary filename is invalid.")
    if json_path.parent != markdown_path.parent:
        raise BannerBaselinePrivacyError("Published artifacts are not colocated.")
    if json_path.parent.name != identity.sha256:
        raise BannerBaselinePrivacyError("Published baseline directory is invalid.")
    if not _has_exact_regular_artifact_files(json_path.parent):
        raise BannerBaselinePrivacyError("Published baseline file set is invalid.")
    try:
        json_bytes = json_path.read_bytes()
        markdown_bytes = markdown_path.read_bytes()
    except OSError:
        raise BannerBaselinePrivacyError(
            "Public baseline artifacts are unavailable."
        ) from None

    payload = _load_json_object(json_bytes)
    _validate_approved_baseline_payload(payload, identity)
    if render_banner_baseline_markdown(json_bytes) != markdown_bytes:
        raise BannerBaselinePrivacyError(
            "Published Markdown does not match sanitized JSON."
        )


def validate_public_baseline_schema(schema: BaselinePublicField) -> None:
    """Reject every unclassified or disclosure-prone baseline field."""

    if schema.classification not in _SAFE_CLASSIFICATIONS:
        raise BannerBaselinePrivacyError(
            "Public baseline schema contains a disclosure-prone field."
        )
    if schema.profile_schema is not None:
        validate_public_profile_schema(schema.profile_schema)
    for child in schema.children:
        validate_public_baseline_schema(child)


def _analyze_descriptor(source: BinaryIO) -> _RoundAnalysis:
    try:
        dataframe = parse_banner_csv(source)
    except Exception:
        raise _RoundFailure("baseline.parsing_failed") from None

    try:
        contract_report = validate_banner_dataframe(
            dataframe,
            allowed_fault_categories=None,
        )
    except Exception:
        raise _RoundFailure("baseline.contract_execution_failed") from None

    try:
        profile = profile_banner_dataframe(
            dataframe,
            key_columns=("id",),
            allowed_fault_categories=None,
        )
        profile_bytes = banner_profile_json_bytes(profile)
        profile_payload = _load_json_object(profile_bytes)
    except Exception:
        raise _RoundFailure("baseline.profiler_execution_failed") from None
    finally:
        del dataframe

    return _RoundAnalysis(
        contract_report=_contract_report_payload(contract_report),
        profile=profile_payload,
    )


def parse_banner_csv(source: BinaryIO) -> pd.DataFrame:
    """Parse one binary descriptor with the complete versioned CSV policy."""

    dtype_mapping = dict(_PARSER_READ_DTYPES)
    with catch_warnings():
        simplefilter("error", pd.errors.ParserWarning)
        simplefilter("error", pd.errors.DtypeWarning)
        parsed = pd.read_csv(
            source,
            sep=",",
            header=0,
            names=None,
            index_col=False,
            usecols=None,
            dtype=dtype_mapping,
            engine="c",
            converters=None,
            true_values=None,
            false_values=None,
            skipinitialspace=False,
            skiprows=None,
            skipfooter=0,
            nrows=None,
            na_values=("",),
            keep_default_na=False,
            na_filter=True,
            skip_blank_lines=False,
            parse_dates=False,
            date_format=None,
            dayfirst=False,
            cache_dates=False,
            iterator=False,
            chunksize=None,
            compression=None,
            thousands=None,
            decimal=".",
            lineterminator=None,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            doublequote=True,
            escapechar=None,
            comment=None,
            encoding="utf-8",
            encoding_errors="strict",
            dialect=None,
            on_bad_lines="error",
            low_memory=False,
            memory_map=False,
            float_precision="round_trip",
            storage_options=None,
        )
    if "id" in parsed and not parsed["id"].isna().any():
        parsed["id"] = parsed["id"].astype("int64")
    return parsed


def _load_manifest_identity(manifest_path: Path) -> _ManifestIdentity:
    try:
        payload: object = json.loads(manifest_path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BannerBaselineError("Public source manifest is invalid.") from None
    manifest = _as_mapping(payload)
    schema_version = _required_int(manifest, "schema_version")
    hash_algorithm = _required_string(manifest, "hash_algorithm")
    if (
        schema_version != _SUPPORTED_MANIFEST_SCHEMA_VERSION
        or hash_algorithm != _SUPPORTED_HASH_ALGORITHM
    ):
        raise BannerBaselineError("Public source manifest is invalid.")

    raw_files = _required_sequence(manifest, "files")
    matches: list[Mapping[str, object]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("name") == _BANNER_BASENAME:
            matches.append(candidate)
    if len(matches) != 1:
        raise BannerBaselineError("Public source manifest is invalid.")
    entry = matches[0]
    size_bytes = _required_int(entry, "size_bytes")
    digest = _required_string(entry, "sha256")
    if size_bytes < 0 or _SHA256_PATTERN.fullmatch(digest) is None:
        raise BannerBaselineError("Public source manifest is invalid.")
    return _ManifestIdentity(
        schema_version=schema_version,
        basename=_BANNER_BASENAME,
        size_bytes=size_bytes,
        sha256=digest,
    )


def _contract_report_payload(report: BannerValidationReport) -> dict[str, object]:
    violations = [
        {
            "code": violation.code.value,
            "severity": violation.severity.value,
            "column": violation.column,
        }
        for violation in report.blocking_violations
    ]
    findings = [
        {
            "code": finding.code,
            "severity": finding.severity.value,
            "column": finding.column,
        }
        for finding in report.statistical_findings
    ]
    return {
        "contract_version": report.contract_version,
        "passed": report.is_valid,
        "blocking_violation_count": len(violations),
        "blocking_violations": violations,
        "statistical_finding_count": len(findings),
        "statistical_findings": findings,
    }


def _build_payload(
    identity: _ManifestIdentity,
    integrity: dict[str, object],
    analysis: _RoundAnalysis,
) -> dict[str, object]:
    reconciliations = _build_reconciliations(
        profile=analysis.profile,
        contract_report=analysis.contract_report,
    )
    gates = _build_gates(
        profile=analysis.profile,
        contract_report=analysis.contract_report,
        reconciliations=reconciliations,
    )
    result = (
        BannerBaselineStatus.PASSED
        if all(
            _required_bool(gate, "passed")
            for gate in gates
            if _required_string(gate, "classification")
            == IndicatorClassification.BLOCKING
        )
        else BannerBaselineStatus.BLOCKED
    )
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "versions": {
            "baseline_schema": BASELINE_SCHEMA_VERSION,
            "contract": BANNER_CONTRACT_VERSION,
            "profile_schema": BANNER_PROFILE_SCHEMA_VERSION,
            "runner": BASELINE_RUNNER_VERSION,
            "sanitizer": BASELINE_SANITIZER_VERSION,
            "classification": BASELINE_CLASSIFICATION_VERSION,
        },
        "source": {
            "basename": identity.basename,
            "sha256": identity.sha256,
        },
        "manifest": {
            "schema_version": identity.schema_version,
            "hash_algorithm": _SUPPORTED_HASH_ALGORITHM,
        },
        "integrity": integrity,
        "tooling": _tooling_payload(),
        "parser": _parser_payload(),
        "expectations": _expectations_payload(),
        "gates": gates,
        "contract_report": analysis.contract_report,
        "profile": analysis.profile,
        "reconciliations": reconciliations,
        "result": result.value,
    }


def _receipts_match_identity(
    identity: _ManifestIdentity,
    receipts: Sequence[BannerSourceReceipt[_RoundAnalysis]],
) -> bool:
    expected = BannerSourceFingerprint(
        size_bytes=identity.size_bytes,
        sha256=identity.sha256,
    )
    return len(receipts) == _ROUND_COUNT and all(
        receipt.pre_fingerprint == expected
        and receipt.post_fingerprint == expected
        and receipt.pre_fingerprint == receipt.post_fingerprint
        for receipt in receipts
    )


def _integrity_payload(
    receipts: Sequence[BannerSourceReceipt[_RoundAnalysis]],
) -> dict[str, object]:
    return {
        "round_count": _ROUND_COUNT,
        "rounds": [
            {
                "round": round_number,
                "pre": {
                    "size_bytes": receipt.pre_fingerprint.size_bytes,
                    "sha256": receipt.pre_fingerprint.sha256,
                    "status": "matched_manifest",
                },
                "post": {
                    "size_bytes": receipt.post_fingerprint.size_bytes,
                    "sha256": receipt.post_fingerprint.sha256,
                    "status": "unchanged",
                },
            }
            for round_number, receipt in enumerate(receipts, start=1)
        ],
    }


def _tooling_payload() -> dict[str, object]:
    python_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return {
        "python": python_version,
        "pandas": pd.__version__,
        "pandera": _installed_version("pandera"),
    }


def _installed_version(package: str) -> str:
    try:
        return package_version(package)
    except PackageNotFoundError:
        raise BannerBaselineError(
            "Required runtime dependency is unavailable."
        ) from None


def _parser_payload() -> dict[str, object]:
    return {
        "format": "csv",
        "engine": "c",
        "encoding": "utf-8",
        "encoding_errors": "strict",
        "delimiter": ",",
        "header_row": 0,
        "index_column": False,
        "use_columns": None,
        "dtypes": [
            {"column": column, "dtype": dtype} for column, dtype in _PARSER_READ_DTYPES
        ],
        "complete_dtypes": [
            {"column": column, "dtype": dtype}
            for column, dtype in _PARSER_COMPLETE_DTYPES
        ],
        "id_finalization": "cast_to_int64_only_when_complete",
        "parse_dates": False,
        "date_format": None,
        "day_first": False,
        "cache_dates": False,
        "low_memory": False,
        "iterator": False,
        "chunk_size": None,
        "on_bad_lines": "error",
        "skip_blank_lines": False,
        "skip_initial_space": False,
        "na_filter": True,
        "keep_default_na": False,
        "na_values": "exact_empty_csv_cell_only",
        "decimal": ".",
        "thousands": None,
        "quote_character": '"',
        "quoting": "minimal",
        "double_quote": True,
        "escape_character": None,
        "comment_character": None,
        "line_terminator": None,
        "compression": None,
        "memory_map": False,
        "float_precision": "round_trip",
    }


def _expectations_payload() -> dict[str, object]:
    return {
        "row_count": EXPECTED_BANNER_ROW_COUNT,
        "column_count": EXPECTED_BANNER_COLUMN_COUNT,
        "raw_fault_cardinality": EXPECTED_RAW_FAULT_CARDINALITY,
    }


def _build_reconciliations(
    *, profile: Mapping[str, object], contract_report: Mapping[str, object]
) -> list[dict[str, object]]:
    volume = _required_mapping(profile, "volume")
    row_count = _required_int(volume, "row_count")
    expected_columns = _required_int(volume, "expected_column_count")
    present_columns = _required_int(volume, "present_expected_column_count")
    missing_columns = _required_int(volume, "missing_expected_column_count")
    observed_columns = _required_int(volume, "observed_column_count")
    unexpected_columns = _required_int(volume, "unexpected_column_count")

    reconciliations = [
        _reconciliation(
            "columns.expected_partition",
            None,
            expected_columns,
            present_columns + missing_columns,
        ),
        _reconciliation(
            "columns.observed_partition",
            None,
            observed_columns,
            present_columns + unexpected_columns,
        ),
    ]

    for item in _required_sequence(profile, "columns"):
        column = _as_mapping(item)
        name = _required_string(column, "name")
        if _required_bool(column, "present"):
            reconciliations.append(
                _reconciliation(
                    "columns.observed_count",
                    name,
                    row_count,
                    _required_int(column, "observed_count"),
                )
            )
        reconciliations.append(
            _reconciliation(
                "columns.missing_count",
                name,
                _required_int(column, "missing_count"),
                _required_int(column, "null_count")
                + _required_int(column, "nan_count"),
            )
        )
        statistics = column.get("numeric_statistics")
        if statistics is not None:
            numeric = _as_mapping(statistics)
            finite_count = _required_int(numeric, "finite_count")
            reconciliations.extend(
                [
                    _reconciliation(
                        "numeric.finite_partition",
                        name,
                        row_count,
                        finite_count
                        + _required_int(column, "missing_count")
                        + _required_int(column, "infinite_count"),
                    ),
                    _bounded_reconciliation(
                        "numeric.iqr_outliers_within_finite",
                        name,
                        finite_count,
                        _required_int(numeric, "iqr_outlier_count"),
                    ),
                ]
            )

    temporal = _required_mapping(profile, "temporal")
    created_at = _profile_column(profile, "created_at")
    distinct_timestamps = _required_int(temporal, "distinct_timestamp_count")
    reconciliations.extend(
        [
            _reconciliation(
                "timestamps.value_partition",
                "created_at",
                row_count,
                _required_int(temporal, "valid_timestamp_count")
                + _required_int(temporal, "invalid_timestamp_count")
                + _required_int(created_at, "missing_count"),
            ),
            _reconciliation(
                "timestamps.interval_count",
                "created_at",
                max(distinct_timestamps - 1, 0),
                _required_int(temporal, "cadence_interval_count"),
            ),
        ]
    )

    labels = _required_mapping(profile, "labels")
    distribution = _required_sequence(labels, "distribution")
    histogram_total = sum(
        _required_int(_as_mapping(item), "count") for item in distribution
    )
    positive_categories = sum(
        _required_int(_as_mapping(item), "count") > 0 for item in distribution
    )
    reconciliations.extend(
        [
            _reconciliation(
                "labels.value_partition",
                "fault",
                row_count,
                _required_int(labels, "valid_label_count")
                + _required_int(labels, "missing_label_count")
                + _required_int(labels, "invalid_label_count"),
            ),
            _reconciliation(
                "labels.histogram_total",
                "fault",
                _required_int(labels, "valid_label_count"),
                histogram_total,
            ),
            _reconciliation(
                "labels.positive_category_count",
                "fault",
                _required_int(labels, "distinct_observed_label_count"),
                positive_categories,
            ),
        ]
    )

    for item in _required_sequence(profile, "redundant_unit_pairs"):
        pair = _as_mapping(item)
        subject = (
            f"{_required_string(pair, 'left_column')}->"
            f"{_required_string(pair, 'right_column')}"
        )
        comparable = _required_int(pair, "comparable_count")
        reconciliations.extend(
            [
                _reconciliation(
                    "unit_pairs.availability_partition",
                    subject,
                    row_count,
                    comparable + _required_int(pair, "unavailable_count"),
                ),
                _reconciliation(
                    "unit_pairs.consistency_partition",
                    subject,
                    comparable,
                    _required_int(pair, "consistent_count")
                    + _required_int(pair, "inconsistent_count"),
                ),
            ]
        )

    duplicates = _required_mapping(profile, "duplicates")
    available_rows = row_count - _required_int(
        duplicates, "rows_with_incomplete_key_count"
    )
    duplicate_key_groups = _required_int(duplicates, "duplicate_key_group_count")
    reconciliations.extend(
        [
            _reconciliation(
                "duplicates.key_available",
                "id",
                1,
                int(_required_bool(duplicates, "key_columns_available")),
            ),
            _bounded_reconciliation(
                "duplicates.complete_excess_denominator",
                None,
                row_count,
                _required_int(duplicates, "complete_duplicate_excess_row_count"),
            ),
            _bounded_reconciliation(
                "duplicates.complete_group_denominator",
                None,
                row_count,
                _required_int(duplicates, "complete_duplicate_group_count"),
            ),
            _bounded_reconciliation(
                "duplicates.key_excess_denominator",
                "id",
                available_rows,
                _required_int(duplicates, "duplicate_key_excess_row_count"),
            ),
            _bounded_reconciliation(
                "duplicates.key_group_denominator",
                "id",
                available_rows,
                duplicate_key_groups,
            ),
            _bounded_reconciliation(
                "duplicates.conflicting_group_denominator",
                "id",
                duplicate_key_groups,
                _required_int(duplicates, "conflicting_key_group_count"),
            ),
            _bounded_reconciliation(
                "duplicates.conflicting_row_denominator",
                "id",
                available_rows,
                _required_int(duplicates, "conflicting_row_count"),
            ),
            _reconciliation(
                "versions.contract_profile",
                None,
                _required_int(contract_report, "contract_version"),
                _required_int(profile, "contract_version"),
            ),
            _reconciliation(
                "classifications.indicator_registry",
                None,
                len(_INDICATOR_CLASSIFICATIONS),
                len({code for code, _ in _INDICATOR_CLASSIFICATIONS}),
            ),
            _reconciliation(
                "markdown.regeneration",
                None,
                1,
                1,
            ),
        ]
    )
    return reconciliations


def _build_gates(
    *,
    profile: Mapping[str, object],
    contract_report: Mapping[str, object],
    reconciliations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    volume = _required_mapping(profile, "volume")
    temporal = _required_mapping(profile, "temporal")
    duplicates = _required_mapping(profile, "duplicates")
    labels = _required_mapping(profile, "labels")
    columns = [_as_mapping(item) for item in _required_sequence(profile, "columns")]
    pairs = [
        _as_mapping(item)
        for item in _required_sequence(profile, "redundant_unit_pairs")
    ]

    dimension_passed = (
        _required_int(volume, "row_count") == EXPECTED_BANNER_ROW_COUNT
        and _required_int(volume, "observed_column_count")
        == EXPECTED_BANNER_COLUMN_COUNT
    )
    cardinality_passed = (
        _required_int(labels, "distinct_observed_label_count")
        == EXPECTED_RAW_FAULT_CARDINALITY
    )
    all_reconciled = all(
        _required_bool(reconciliation, "passed") for reconciliation in reconciliations
    )
    registry_valid = len(_INDICATOR_CLASSIFICATIONS) == len(
        {code for code, _ in _INDICATOR_CLASSIFICATIONS}
    )
    inconsistent_pairs = sum(
        _required_int(pair, "inconsistent_count") for pair in pairs
    )
    numeric_columns = [
        _as_mapping(column["numeric_statistics"])
        for column in columns
        if column.get("numeric_statistics") is not None
    ]
    iqr_outliers = sum(
        _required_int(statistics, "iqr_outlier_count") for statistics in numeric_columns
    )
    majority = labels.get("majority_count")
    minority = labels.get("minority_count")
    imbalance = (
        0
        if majority is None or minority is None
        else max(_as_int(majority) - _as_int(minority), 0)
    )

    values: dict[str, tuple[bool, int]] = {
        "integrity.source": (True, 0),
        "parsing.csv": (True, 0),
        "contract.banner": (
            _required_bool(contract_report, "passed"),
            _required_int(contract_report, "blocking_violation_count"),
        ),
        "expectation.dimensions": (dimension_passed, int(not dimension_passed)),
        "expectation.fault_cardinality": (
            cardinality_passed,
            int(not cardinality_passed),
        ),
        "determinism.temporal_arithmetic": (True, 0),
        "sanitization.public_payload": (True, 0),
        "reconciliation.aggregate_counts": (
            all_reconciled,
            sum(
                not _required_bool(reconciliation, "passed")
                for reconciliation in reconciliations
            ),
        ),
        "classification.public_fields": (registry_valid, int(not registry_valid)),
        "determinism.byte_equality": (True, 0),
        "quality.complete_duplicates": (
            _required_int(duplicates, "complete_duplicate_excess_row_count") == 0,
            _required_int(duplicates, "complete_duplicate_excess_row_count"),
        ),
        "quality.repeated_ids": (
            _required_int(duplicates, "duplicate_key_group_count") == 0,
            _required_int(duplicates, "duplicate_key_group_count"),
        ),
        "quality.conflicting_ids": (
            _required_int(duplicates, "conflicting_key_group_count") == 0,
            _required_int(duplicates, "conflicting_key_group_count"),
        ),
        "quality.irregular_cadence": (
            _required_int(temporal, "irregular_interval_count") == 0,
            _required_int(temporal, "irregular_interval_count"),
        ),
        "quality.temporal_gaps": (
            _required_int(temporal, "gap_count") == 0,
            _required_int(temporal, "gap_count"),
        ),
        "quality.redundant_pairs": (
            inconsistent_pairs == 0,
            inconsistent_pairs,
        ),
        "observation.temporal_period": (
            True,
            int(_required_int(temporal, "valid_timestamp_count") > 0),
        ),
        "observation.column_statistics": (True, len(numeric_columns)),
        "observation.iqr_outliers": (True, iqr_outliers),
        "observation.anonymous_fault_distribution": (
            True,
            _required_int(labels, "distinct_observed_label_count"),
        ),
        "observation.fault_imbalance": (True, imbalance),
        "observation.redundant_pair_consistency": (
            True,
            sum(_required_int(pair, "consistent_count") for pair in pairs),
        ),
    }
    if tuple(values) != tuple(code for code, _ in _INDICATOR_CLASSIFICATIONS):
        raise BannerBaselinePrivacyError(
            "Every public indicator must have exactly one classification."
        )
    return [
        {
            "code": code,
            "classification": classification.value,
            "passed": values[code][0],
            "finding_count": values[code][1],
        }
        for code, classification in _INDICATOR_CLASSIFICATIONS
    ]


def _reconciliation(
    code: str, subject: str | None, expected: int, actual: int
) -> dict[str, object]:
    return {
        "code": code,
        "subject": subject,
        "expected": expected,
        "actual": actual,
        "passed": actual == expected,
    }


def _bounded_reconciliation(
    code: str, subject: str | None, maximum: int, actual: int
) -> dict[str, object]:
    return {
        "code": code,
        "subject": subject,
        "expected": maximum,
        "actual": actual,
        "passed": 0 <= actual <= maximum,
    }


def _serialize_public_payload(
    payload: Mapping[str, object], identity: _ManifestIdentity
) -> bytes:
    _validate_public_payload(payload, expected_identity=identity)
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise BannerBaselinePrivacyError(
            "Public baseline cannot be serialized safely."
        ) from None


def _load_json_object(json_bytes: bytes) -> dict[str, object]:
    try:
        decoded = json_bytes.decode("utf-8", errors="strict")
        payload: object = json.loads(
            decoded,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise BannerBaselinePrivacyError("Public baseline JSON is invalid.") from None
    mapping = dict(_as_mapping(payload))
    canonical = (
        json.dumps(
            mapping,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")
    if canonical != json_bytes:
        raise BannerBaselinePrivacyError("Public baseline JSON is not canonical.")
    return mapping


def _validate_public_payload(
    payload: Mapping[str, object], expected_identity: _ManifestIdentity | None
) -> None:
    validate_public_baseline_schema(PUBLIC_BANNER_BASELINE_SCHEMA)
    _validate_mapping_against_schema(payload, PUBLIC_BANNER_BASELINE_SCHEMA.children)
    _validate_payload_semantics(payload, expected_identity)
    _scan_public_value(payload, path=())


def _validate_approved_baseline_payload(
    payload: Mapping[str, object], identity: _ManifestIdentity
) -> None:
    _validate_public_payload(payload, expected_identity=identity)
    if _required_string(payload, "result") != BannerBaselineStatus.PASSED:
        raise BannerBaselinePrivacyError("Published baseline is not approved.")
    if _blocking_failure_codes(payload):
        raise BannerBaselinePrivacyError("Published baseline contains a blocked gate.")


def _validate_mapping_against_schema(
    value: object, fields: tuple[BaselinePublicField, ...]
) -> None:
    mapping = _as_mapping(value)
    expected_keys = tuple(field.name for field in fields)
    if tuple(mapping.keys()) != expected_keys:
        raise BannerBaselinePrivacyError(
            "Public baseline fields do not match the classified schema."
        )
    for field in fields:
        item = mapping[field.name]
        if field.profile_schema is not None:
            _validate_profile_against_schema(item, field.profile_schema)
        elif field.children:
            if field.sequence:
                for child in _as_sequence(item):
                    _validate_mapping_against_schema(child, field.children)
            else:
                _validate_mapping_against_schema(item, field.children)
        elif isinstance(item, Mapping | list):
            raise BannerBaselinePrivacyError(
                "Public baseline contains an unclassified container."
            )


def _validate_profile_against_schema(value: object, schema: PublicProfileField) -> None:
    validate_public_profile_schema(schema)
    if schema.children:
        mapping = _as_mapping(value)
        expected_keys = tuple(child.name for child in schema.children)
        if tuple(mapping.keys()) != expected_keys:
            raise BannerBaselinePrivacyError(
                "Embedded profile fields do not match the classified schema."
            )
        for child in schema.children:
            item = mapping[child.name]
            if child.children:
                if item is None:
                    if child.nullable:
                        continue
                    raise BannerBaselinePrivacyError(
                        "Embedded profile requires a classified container."
                    )
                if child.sequence:
                    for sequence_item in _as_sequence(item):
                        _validate_profile_mapping(sequence_item, child.children)
                else:
                    _validate_profile_mapping(item, child.children)
            else:
                _reject_unclassified_profile_container(item)


def _validate_profile_mapping(
    value: object, fields: tuple[PublicProfileField, ...]
) -> None:
    mapping = _as_mapping(value)
    if tuple(mapping.keys()) != tuple(field.name for field in fields):
        raise BannerBaselinePrivacyError(
            "Embedded profile fields do not match the classified schema."
        )
    for field in fields:
        item = mapping[field.name]
        if field.children:
            if item is None:
                if field.nullable:
                    continue
                raise BannerBaselinePrivacyError(
                    "Embedded profile requires a classified container."
                )
            if field.sequence:
                for sequence_item in _as_sequence(item):
                    _validate_profile_mapping(sequence_item, field.children)
            else:
                _validate_profile_mapping(item, field.children)
        else:
            _reject_unclassified_profile_container(item)


def _reject_unclassified_profile_container(value: object) -> None:
    if isinstance(value, Mapping):
        raise BannerBaselinePrivacyError(
            "Embedded profile contains an unclassified object."
        )
    if isinstance(value, list) and any(
        isinstance(item, Mapping | list) for item in cast(list[object], value)
    ):
        raise BannerBaselinePrivacyError(
            "Embedded profile contains an unclassified nested value."
        )


def _validate_payload_semantics(
    payload: Mapping[str, object], expected_identity: _ManifestIdentity | None
) -> None:
    if _required_int(payload, "baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        raise BannerBaselinePrivacyError("Public baseline version is unsupported.")

    versions = _required_mapping(payload, "versions")
    expected_versions = {
        "baseline_schema": BASELINE_SCHEMA_VERSION,
        "contract": BANNER_CONTRACT_VERSION,
        "profile_schema": BANNER_PROFILE_SCHEMA_VERSION,
        "runner": BASELINE_RUNNER_VERSION,
        "sanitizer": BASELINE_SANITIZER_VERSION,
        "classification": BASELINE_CLASSIFICATION_VERSION,
    }
    if dict(versions) != expected_versions:
        raise BannerBaselinePrivacyError("Public baseline versions are invalid.")

    source = _required_mapping(payload, "source")
    basename = _required_string(source, "basename")
    digest = _required_string(source, "sha256")
    if basename != _BANNER_BASENAME or _SHA256_PATTERN.fullmatch(digest) is None:
        raise BannerBaselinePrivacyError("Public source identity is invalid.")
    if "/" in basename or "\\" in basename:
        raise BannerBaselinePrivacyError("Public source basename is invalid.")
    if expected_identity is not None and (
        digest != expected_identity.sha256 or basename != expected_identity.basename
    ):
        raise BannerBaselinePrivacyError(
            "Public source identity does not match the manifest."
        )

    manifest = _required_mapping(payload, "manifest")
    if (
        _required_int(manifest, "schema_version") != _SUPPORTED_MANIFEST_SCHEMA_VERSION
        or _required_string(manifest, "hash_algorithm") != _SUPPORTED_HASH_ALGORITHM
    ):
        raise BannerBaselinePrivacyError("Public manifest metadata is invalid.")

    integrity = _required_mapping(payload, "integrity")
    rounds = _required_sequence(integrity, "rounds")
    if _required_int(integrity, "round_count") != _ROUND_COUNT or len(rounds) != 2:
        raise BannerBaselinePrivacyError("Public integrity rounds are invalid.")
    observed_size: int | None = None
    for expected_round, item in enumerate(rounds, start=1):
        round_payload = _as_mapping(item)
        if _required_int(round_payload, "round") != expected_round:
            raise BannerBaselinePrivacyError("Public integrity rounds are invalid.")
        pre = _required_mapping(round_payload, "pre")
        post = _required_mapping(round_payload, "post")
        pre_size = _required_int(pre, "size_bytes")
        post_size = _required_int(post, "size_bytes")
        if observed_size is None:
            observed_size = pre_size
        if (
            pre_size < 0
            or pre_size != post_size
            or pre_size != observed_size
            or (
                expected_identity is not None
                and pre_size != expected_identity.size_bytes
            )
            or _required_string(pre, "sha256") != digest
            or _required_string(post, "sha256") != digest
            or _required_string(pre, "status") != "matched_manifest"
            or _required_string(post, "status") != "unchanged"
        ):
            raise BannerBaselinePrivacyError("Public integrity evidence is invalid.")

    tooling = _required_mapping(payload, "tooling")
    for name in ("python", "pandas", "pandera"):
        value = _required_string(tooling, name)
        if _VERSION_PATTERN.fullmatch(value) is None:
            raise BannerBaselinePrivacyError("Public tooling metadata is invalid.")

    if dict(_required_mapping(payload, "parser")) != _parser_payload():
        raise BannerBaselinePrivacyError("Public parser configuration is invalid.")
    if dict(_required_mapping(payload, "expectations")) != _expectations_payload():
        raise BannerBaselinePrivacyError("Public expectations are invalid.")

    contract = _required_mapping(payload, "contract_report")
    if _required_int(contract, "contract_version") != BANNER_CONTRACT_VERSION:
        raise BannerBaselinePrivacyError("Public contract report version is invalid.")
    _validate_contract_issues(
        contract, "blocking_violations", "blocking_violation_count"
    )
    if _required_int(contract, "statistical_finding_count") != 0 or _required_sequence(
        contract, "statistical_findings"
    ):
        raise BannerBaselinePrivacyError("Public contract report is invalid.")
    if _required_bool(contract, "passed") != (
        _required_int(contract, "blocking_violation_count") == 0
    ):
        raise BannerBaselinePrivacyError("Public contract report is invalid.")

    profile = _required_mapping(payload, "profile")
    _validate_profile_semantics(profile)
    if _required_int(profile, "contract_version") != BANNER_CONTRACT_VERSION:
        raise BannerBaselinePrivacyError(
            "Embedded profile contract version is invalid."
        )
    if (
        _required_int(profile, "profile_schema_version")
        != BANNER_PROFILE_SCHEMA_VERSION
    ):
        raise BannerBaselinePrivacyError("Embedded profile version is invalid.")

    expected_reconciliations = _build_reconciliations(
        profile=profile,
        contract_report=contract,
    )
    reconciliations = [
        dict(_as_mapping(item))
        for item in _required_sequence(payload, "reconciliations")
    ]
    if reconciliations != expected_reconciliations:
        raise BannerBaselinePrivacyError("Public reconciliations are invalid.")

    expected_gates = _build_gates(
        profile=profile,
        contract_report=contract,
        reconciliations=expected_reconciliations,
    )
    gates = [dict(_as_mapping(item)) for item in _required_sequence(payload, "gates")]
    if gates != expected_gates:
        raise BannerBaselinePrivacyError("Public gate classifications are invalid.")
    expected_result = (
        BannerBaselineStatus.PASSED.value
        if not _blocking_failure_codes_from_gates(gates)
        else BannerBaselineStatus.BLOCKED.value
    )
    if _required_string(payload, "result") != expected_result:
        raise BannerBaselinePrivacyError("Public baseline result is invalid.")


def _validate_contract_issues(
    contract: Mapping[str, object], sequence_key: str, count_key: str
) -> None:
    issues = _required_sequence(contract, sequence_key)
    if len(issues) != _required_int(contract, count_key):
        raise BannerBaselinePrivacyError("Public contract report is invalid.")
    for item in issues:
        issue = _as_mapping(item)
        code = _required_string(issue, "code")
        severity = _required_string(issue, "severity")
        column = issue.get("column")
        if (
            code not in _CANONICAL_CONTRACT_VIOLATION_CODES
            or severity != ValidationSeverity.ERROR.value
        ):
            raise BannerBaselinePrivacyError("Public contract report is invalid.")
        if column is not None and _as_string(column) not in BANNER_COLUMN_NAMES:
            raise BannerBaselinePrivacyError("Public contract report is invalid.")


def _validate_profile_semantics(profile: Mapping[str, object]) -> None:
    try:
        validate_public_banner_profile_payload(profile)
    except ProfilePrivacyError:
        raise BannerBaselinePrivacyError("Embedded profile is invalid.") from None

    columns = [_as_mapping(item) for item in _required_sequence(profile, "columns")]
    if (
        tuple(_required_string(column, "name") for column in columns)
        != BANNER_COLUMN_NAMES
    ):
        raise BannerBaselinePrivacyError("Embedded profile columns are invalid.")
    if tuple(_required_int(column, "position") for column in columns) != tuple(
        range(1, len(BANNER_COLUMN_NAMES) + 1)
    ):
        raise BannerBaselinePrivacyError("Embedded profile positions are invalid.")
    if tuple(_required_string(column, "logical_type") for column in columns) != tuple(
        column.logical_type.value for column in BANNER_COLUMN_CATALOG
    ):
        raise BannerBaselinePrivacyError("Embedded profile logical types are invalid.")

    _validate_profile_temporal_semantics(_required_mapping(profile, "temporal"))

    duplicates = _required_mapping(profile, "duplicates")
    if tuple(
        _as_string(item) for item in _required_sequence(duplicates, "key_columns")
    ) != ("id",):
        raise BannerBaselinePrivacyError(
            "Embedded profile key configuration is invalid."
        )

    labels = _required_mapping(profile, "labels")
    if (
        _required_string(labels, "source_column") != "fault"
        or _required_bool(labels, "allowed_category_check_applied")
        or labels.get("allowed_category_count") is not None
        or labels.get("absent_allowed_category_count") is not None
    ):
        raise BannerBaselinePrivacyError("Embedded fault labels are not anonymous.")
    for expected_ordinal, item in enumerate(
        _required_sequence(labels, "distribution"), start=1
    ):
        category = _as_mapping(item)
        if (
            category.get("label") is not None
            or _required_int(category, "unapproved_ordinal") != expected_ordinal
            or category.get("is_allowed") is not None
            or _required_int(category, "count") <= 0
        ):
            raise BannerBaselinePrivacyError("Embedded fault labels are not anonymous.")

    pair_identities = tuple(
        (
            _required_string(_as_mapping(item), "left_column"),
            _required_string(_as_mapping(item), "right_column"),
            _required_string(_as_mapping(item), "relation"),
        )
        for item in _required_sequence(profile, "redundant_unit_pairs")
    )
    if pair_identities != BANNER_REDUNDANT_UNIT_PAIR_IDENTITIES:
        raise BannerBaselinePrivacyError("Embedded redundant pairs are invalid.")


def _validate_profile_temporal_semantics(temporal: Mapping[str, object]) -> None:
    valid_count = _required_int(temporal, "valid_timestamp_count")
    distinct_count = _required_int(temporal, "distinct_timestamp_count")
    input_order = _required_string(temporal, "input_order")
    if input_order not in {order.value for order in TemporalOrder}:
        raise BannerBaselinePrivacyError("Embedded temporal order is invalid.")

    start_value = temporal.get("period_start_utc")
    end_value = temporal.get("period_end_utc")
    if valid_count == 0:
        if (
            distinct_count != 0
            or start_value is not None
            or end_value is not None
            or input_order != TemporalOrder.NOT_APPLICABLE.value
        ):
            raise BannerBaselinePrivacyError("Embedded temporal period is invalid.")
        return

    if (
        distinct_count < 1
        or distinct_count > valid_count
        or not isinstance(start_value, str)
        or not isinstance(end_value, str)
    ):
        raise BannerBaselinePrivacyError("Embedded temporal period is invalid.")
    start = parse_banner_utc_timestamp(start_value)
    end = parse_banner_utc_timestamp(end_value)
    if (
        start is None
        or end is None
        or start.canonical_text() != start_value
        or end.canonical_text() != end_value
        or start > end
        or (distinct_count == 1 and start != end)
        or (distinct_count > 1 and start >= end)
    ):
        raise BannerBaselinePrivacyError("Embedded temporal period is invalid.")
    if (
        (valid_count == 1 and input_order != TemporalOrder.NOT_APPLICABLE.value)
        or (
            valid_count > 1
            and distinct_count == 1
            and input_order != TemporalOrder.CONSTANT.value
        )
        or (
            distinct_count > 1
            and input_order
            in {TemporalOrder.NOT_APPLICABLE.value, TemporalOrder.CONSTANT.value}
        )
    ):
        raise BannerBaselinePrivacyError("Embedded temporal order is invalid.")


def _scan_public_value(value: object, path: tuple[str, ...]) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in cast(Mapping[object, object], value).items():
            if not isinstance(raw_key, str):
                raise BannerBaselinePrivacyError("Public baseline key is invalid.")
            _scan_public_value(item, (*path, raw_key))
        return
    if isinstance(value, list):
        for item in cast(list[object], value):
            _scan_public_value(item, (*path, "[]"))
        return
    if isinstance(value, float) and not isfinite(value):
        raise BannerBaselinePrivacyError(
            "Public baseline contains a non-finite number."
        )
    if isinstance(value, str):
        _validate_public_text(value, path)
        return
    if value is None or type(value) in {bool, int, float}:
        return
    raise BannerBaselinePrivacyError("Public baseline contains an unsupported value.")


def _validate_public_text(value: str, path: tuple[str, ...]) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise BannerBaselinePrivacyError(
            "Public baseline contains unsafe text."
        ) from None
    if any(
        unicode_category(character) in _UNSAFE_UNICODE_CATEGORIES for character in value
    ):
        raise BannerBaselinePrivacyError("Public baseline contains unsafe text.")
    folded = value.casefold()
    if (
        value.startswith(("/", "~/", "~\\", "\\\\"))
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.match(value) is not None
        or "file://" in folded
        or "/home/" in folded
        or "/users/" in folded
        or "\\users\\" in folded
    ):
        raise BannerBaselinePrivacyError("Public baseline contains a local path.")
    if (
        _SECRET_ASSIGNMENT_PATTERN.search(value) is not None
        or _SECRET_TOKEN_PATTERN.search(value) is not None
        or _URI_CREDENTIAL_PATTERN.search(value) is not None
    ):
        raise BannerBaselinePrivacyError("Public baseline contains secret-like text.")
    if _SHA256_PATTERN.fullmatch(value) is not None and (
        not path or path[-1] != "sha256"
    ):
        raise BannerBaselinePrivacyError(
            "Public fingerprint is outside an approved field."
        )


def _write_artifacts_atomically(
    *, output_root: Path, source_sha256: str, json_bytes: bytes, markdown_bytes: bytes
) -> Path:
    final_directory = output_root / source_sha256
    json_path = final_directory / _BASELINE_JSON_FILENAME
    markdown_path = final_directory / _BASELINE_MARKDOWN_FILENAME
    if final_directory.exists() or final_directory.is_symlink():
        try:
            if (
                _has_exact_regular_artifact_files(final_directory)
                and json_path.read_bytes() == json_bytes
                and markdown_path.read_bytes() == markdown_bytes
            ):
                return final_directory
        except OSError:
            pass
        raise BannerBaselineError("Existing public baseline differs from this run.")

    try:
        output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=output_root))
    except OSError:
        raise BannerBaselineError("Public baseline staging failed.") from None

    staging_json = staging / _BASELINE_JSON_FILENAME
    staging_markdown = staging / _BASELINE_MARKDOWN_FILENAME
    try:
        _write_durable_file(staging_json, json_bytes)
        _write_durable_file(staging_markdown, markdown_bytes)
        if not _has_exact_regular_artifact_files(staging):
            raise BannerBaselineError("Public baseline staging is invalid.")
        os.replace(staging, final_directory)
    except (OSError, BannerBaselineError):
        _remove_staging_directory(staging, staging_json, staging_markdown)
        raise BannerBaselineError("Public baseline atomic write failed.") from None
    return final_directory


def _has_exact_regular_artifact_files(directory: Path) -> bool:
    try:
        if directory.is_symlink() or not directory.is_dir():
            return False
        entries = tuple(directory.iterdir())
        if (
            len(entries) != len(_EXPECTED_ARTIFACT_FILENAMES)
            or frozenset(entry.name for entry in entries)
            != _EXPECTED_ARTIFACT_FILENAMES
        ):
            return False
        return all(not entry.is_symlink() and entry.is_file() for entry in entries)
    except OSError:
        return False


def _write_durable_file(path: Path, content: bytes) -> None:
    with path.open("xb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def _remove_staging_directory(staging: Path, *files: Path) -> None:
    try:
        resolved_staging = staging.resolve(strict=False)
        resolved_parent = staging.parent.resolve(strict=False)
        if resolved_staging.parent != resolved_parent or not staging.name.startswith(
            _STAGING_PREFIX
        ):
            return
        for file in files:
            if file.parent == staging:
                file.unlink(missing_ok=True)
        staging.rmdir()
    except OSError:
        return


def _source_failure_code(error: BannerSourceError) -> str:
    if isinstance(error, SourceHashMismatchError):
        return "baseline.source_hash_mismatch"
    if isinstance(error, SourceSizeMismatchError):
        return "baseline.source_size_mismatch"
    if isinstance(error, SourceChangedError):
        return "baseline.source_changed"
    if isinstance(error, SourceNotFoundError):
        return "baseline.source_not_found"
    if isinstance(error, SourcePermissionError):
        return "baseline.source_permission_denied"
    if isinstance(error, UnexpectedSourceNameError):
        return "baseline.source_name_mismatch"
    if isinstance(error, SourceManifestError):
        return "baseline.manifest_invalid"
    if isinstance(error, SourceAccessError):
        return "baseline.source_access_failed"
    return "baseline.source_failed"


def _blocked_result(code: str) -> BannerBaselineRunResult:
    return BannerBaselineRunResult(
        status=BannerBaselineStatus.BLOCKED,
        failure_codes=(code,),
        json_bytes=None,
        markdown_bytes=None,
        output_directory=None,
    )


def _blocking_failure_codes(payload: Mapping[str, object]) -> tuple[str, ...]:
    gates = [dict(_as_mapping(item)) for item in _required_sequence(payload, "gates")]
    return _blocking_failure_codes_from_gates(gates)


def _blocking_failure_codes_from_gates(
    gates: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    return tuple(
        f"gate.{_required_string(gate, 'code')}"
        for gate in gates
        if _required_string(gate, "classification") == IndicatorClassification.BLOCKING
        and not _required_bool(gate, "passed")
    )


def _profile_column(profile: Mapping[str, object], name: str) -> Mapping[str, object]:
    matches: list[Mapping[str, object]] = []
    for item in _required_sequence(profile, "columns"):
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("name") == name:
            matches.append(candidate)
    if len(matches) != 1:
        raise BannerBaselinePrivacyError("Embedded profile column is unavailable.")
    return matches[0]


def _required_mapping(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    if key not in mapping:
        raise BannerBaselinePrivacyError("Required public object is unavailable.")
    return _as_mapping(mapping[key])


def _required_sequence(mapping: Mapping[str, object], key: str) -> list[object]:
    if key not in mapping:
        raise BannerBaselinePrivacyError("Required public sequence is unavailable.")
    return _as_sequence(mapping[key])


def _required_string(mapping: Mapping[str, object], key: str) -> str:
    if key not in mapping:
        raise BannerBaselinePrivacyError("Required public text is unavailable.")
    return _as_string(mapping[key])


def _required_int(mapping: Mapping[str, object], key: str) -> int:
    if key not in mapping:
        raise BannerBaselinePrivacyError("Required public integer is unavailable.")
    return _as_int(mapping[key])


def _required_bool(mapping: Mapping[str, object], key: str) -> bool:
    if key not in mapping or type(mapping[key]) is not bool:
        raise BannerBaselinePrivacyError("Required public boolean is unavailable.")
    return cast(bool, mapping[key])


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BannerBaselinePrivacyError("Public object has an invalid type.")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise BannerBaselinePrivacyError("Public object has an invalid key.")
    return cast(Mapping[str, object], raw)


def _as_sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BannerBaselinePrivacyError("Public sequence has an invalid type.")
    return cast(list[object], value)


def _as_string(value: object) -> str:
    if not isinstance(value, str):
        raise BannerBaselinePrivacyError("Public text has an invalid type.")
    return value


def _as_int(value: object) -> int:
    if type(value) is not int:
        raise BannerBaselinePrivacyError("Public integer has an invalid type.")
    return value


def _yes_no(value: bool) -> str:
    return "sim" if value else "não"


def _markdown_cell(value: str) -> str:
    return (
        value.replace("\\", "&#92;")
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _optional_markdown_value(value: object) -> str:
    if value is None:
        return "não disponível"
    if isinstance(value, str):
        return _markdown_cell(value)
    if type(value) is int:
        return str(value)
    if isinstance(value, float) and isfinite(value):
        return f"{value:.6f}"
    raise BannerBaselinePrivacyError("Markdown value has an invalid type.")
