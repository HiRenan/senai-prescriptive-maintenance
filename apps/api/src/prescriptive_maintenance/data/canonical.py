"""Deterministic local canonical dataset and temporal partitions for banner."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from importlib.resources import files
from itertools import pairwise
from math import isfinite
from pathlib import Path
from typing import Final, NoReturn, cast

import pandas as pd

from prescriptive_maintenance.data._decimal import isolated_decimal_context
from prescriptive_maintenance.data.baseline import parse_banner_csv
from prescriptive_maintenance.data.contract import (
    BANNER_COLUMN_CATALOG,
    BANNER_COLUMN_NAMES,
    BANNER_CONTRACT_VERSION,
    BannerUtcTimestamp,
    parse_banner_utc_timestamp,
    validate_banner_dataframe,
)
from prescriptive_maintenance.data.fault_labels import (
    FAULT_LABEL_NORMALIZATION_VERSION,
    FAULT_LABEL_UNICODE_VERSION,
    FaultLabelInventory,
    load_fault_label_inventory,
)
from prescriptive_maintenance.data.quality_policy import (
    Action,
    BannerQualityPolicy,
    UnitRelationPolicy,
    load_banner_quality_policy,
)
from prescriptive_maintenance.data.source import (
    BannerSourceFingerprint,
    consume_banner_source_audited,
)

CANONICAL_PIPELINE_SCHEMA_VERSION: Final = 1
CANONICAL_PIPELINE_VERSION: Final = 1
CANONICAL_FEATURE_CONTRACT_VERSION: Final = 1
CANONICAL_DATASET_SCHEMA_VERSION: Final = 1
CANONICAL_MANIFEST_SCHEMA_VERSION: Final = 1

CANONICAL_ARTIFACT_FILENAMES: Final[tuple[str, ...]] = (
    "canonical.parquet",
    "dispositions.parquet",
    "train.parquet",
    "validation.parquet",
    "test.parquet",
    "manifest.json",
)

_PIPELINE_RESOURCE_PACKAGE: Final = "prescriptive_maintenance.data.pipelines"
_PIPELINE_RESOURCE_NAME: Final = "banner_pipeline.v1.json"
_SCHEMA_RESOURCE_NAME: Final = "banner_dataset_schema.v1.json"
_DECIMAL_PRECISION: Final = 128
_SHA256_LENGTH: Final = 64
_PARQUET_COMPRESSION: Final = "zstd"
_MAX_TEMPORAL_FIT_ITERATIONS: Final = 64

_CANONICAL_GATE_NAMES: Final[tuple[str, ...]] = (
    "ledger.complete_destination",
    "canonical.eligible_coverage",
    "partitions.assignment_alignment",
    "partitions.occurrence_disjoint",
    "partitions.projection_exact",
    "partitions.temporal_order",
    "partitions.purge_gap",
    "features.inference_only",
    "partitions.nonempty",
    "fit.statistics_train_only",
    "fit.target_independent",
)

_PARQUET_ARTIFACT_FILENAMES: Final[tuple[str, ...]] = tuple(
    name for name in CANONICAL_ARTIFACT_FILENAMES if name != "manifest.json"
)

type _FeatureValues = Mapping[str, tuple[float, ...]]


def _new_text_list() -> list[str]:
    return []


class CanonicalPipelineError(Exception):
    """Base class for sanitized canonical-pipeline failures."""


class CanonicalConfigurationError(CanonicalPipelineError):
    """Raised when a packaged pipeline contract is invalid."""


class CanonicalContractError(CanonicalPipelineError):
    """Raised when an input does not satisfy the banner contract."""


class CanonicalLabelError(CanonicalPipelineError):
    """Raised when a raw label has no approved one-to-one slug."""


class CanonicalPartitionError(CanonicalPipelineError):
    """Raised when safe temporal partitions cannot be produced."""


class CanonicalOutputError(CanonicalPipelineError):
    """Raised when local artifacts cannot be written atomically."""


class CanonicalCheckError(CanonicalPipelineError):
    """Raised when local artifacts fail an offline validation."""


class Disposition(StrEnum):
    """Exactly one final quality disposition for every source record."""

    KEPT = "kept"
    CORRECTED = "corrected"
    MAPPED = "mapped"
    FLAGGED = "flagged"
    REJECTED = "rejected"


class Partition(StrEnum):
    """Chronological model-data partitions."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class DatasetDestination(StrEnum):
    """Final dataset destination, including explicit exclusions."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    PURGE = "purge"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SourceColumnDisposition:
    """Versioned destination and reason for one raw source position."""

    position: int
    name: str
    destination: str | None
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class CanonicalFeature:
    """One inference-time feature in its frozen order."""

    name: str
    dtype: str
    unit: str
    nullable: bool
    domain: str
    source_column: str
    availability: str


@dataclass(frozen=True, slots=True)
class TrustedUnitRelation:
    """Feature-contract proof selecting one canonical side of a relation."""

    relation_id: str
    trusted_column: str
    excluded_column: str


@dataclass(frozen=True, slots=True)
class CanonicalPipelineConfig:
    """Validated immutable semantics of the packaged pipeline configuration."""

    config_id: str
    source_columns: tuple[SourceColumnDisposition, ...]
    features: tuple[CanonicalFeature, ...]
    feature_denylist: frozenset[str]
    trusted_unit_relations: tuple[TrustedUnitRelation, ...]
    disposition_precedence: tuple[Disposition, ...]
    gap_multiplier: int
    gap_quantile: Decimal
    duration_limit_seconds: Decimal
    train_ratio: Decimal
    validation_ratio: Decimal
    test_ratio: Decimal
    target_usage: str

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the exact inference feature order."""

        return tuple(feature.name for feature in self.features)


@dataclass(frozen=True, slots=True)
class DatasetField:
    """One ordered field in a local artifact schema."""

    name: str
    dtype: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class CanonicalDatasetSchema:
    """Validated local schemas for canonical, ledger, and split artifacts."""

    schema_id: str
    canonical_fields: tuple[DatasetField, ...]
    disposition_fields: tuple[DatasetField, ...]
    partition_fields: tuple[DatasetField, ...]

    @property
    def canonical_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.canonical_fields)

    @property
    def disposition_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.disposition_fields)

    @property
    def partition_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.partition_fields)


@dataclass(frozen=True, slots=True)
class CanonicalLabelEntry:
    """One exact raw-label to slug mapping approved for a build."""

    raw_label: str
    slug: str


@dataclass(frozen=True, slots=True)
class CanonicalLabelMap:
    """One-to-one categorical mapping bound to a source identity."""

    inventory_id: str
    source_sha256: str
    entries: tuple[CanonicalLabelEntry, ...]

    def resolve(self, raw_label: str) -> str:
        """Resolve an exact raw label without semantic fallback."""

        for entry in self.entries:
            if entry.raw_label == raw_label:
                return entry.slug
        raise CanonicalLabelError("Raw fault label is not in the approved inventory.")


@dataclass(frozen=True, slots=True)
class CanonicalBuildResult:
    """Sanitized aggregate result of one successful local build."""

    dataset_id: str
    output_directory: Path
    source_row_count: int
    canonical_row_count: int
    occurrence_count: int
    disposition_counts: Mapping[str, int]
    destination_counts: Mapping[str, int]
    partition_counts: Mapping[str, int]
    artifact_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CanonicalCheckResult:
    """Sanitized aggregate result of an offline artifact check."""

    dataset_id: str
    source_row_count: int
    canonical_row_count: int
    occurrence_count: int
    partition_counts: Mapping[str, int]
    artifact_sha256: Mapping[str, str]


@dataclass(slots=True)
class _Record:
    source_index: int
    source_position: int
    source_id: int
    timestamp: BannerUtcTimestamp
    timestamp_text: str
    row_hash: str
    target_independent_row_hash: str
    record_id: str
    rule_ids: list[str] = field(default_factory=_new_text_list)
    quality_matches: list[str] = field(default_factory=_new_text_list)
    transformations: list[str] = field(default_factory=_new_text_list)
    occurrence_id: str | None = None
    partition: Partition | None = None
    split_exclusion_reason: str | None = None


@dataclass(slots=True)
class _Occurrence:
    ordinal: int
    occurrence_id: str
    start: BannerUtcTimestamp
    end: BannerUtcTimestamp
    record_indices: list[int]
    partition: Partition | None = None
    purged: bool = False


@dataclass(frozen=True, slots=True)
class _OccurrenceGapFit:
    threshold: Decimal
    record_ids: tuple[str, ...]
    occurrence_ids: tuple[str, ...]

    @property
    def membership_sha256(self) -> str:
        return sha256(
            _canonical_json_bytes(
                {
                    "record_ids": list(self.record_ids),
                    "occurrence_ids": list(self.occurrence_ids),
                }
            )
        ).hexdigest()


def load_canonical_pipeline_config() -> CanonicalPipelineConfig:
    """Load and strictly validate the packaged v1 pipeline configuration."""

    payload = _load_resource_json(_PIPELINE_RESOURCE_NAME)
    _exact_keys(
        payload,
        (
            "schema_version",
            "pipeline_version",
            "subject",
            "feature_contract_version",
            "manifest_schema_version",
            "disposition_precedence",
            "source_columns",
            "features",
            "feature_denylist",
            "trusted_unit_relations",
            "occurrence_grouping",
            "partitioning",
        ),
        "pipeline",
    )
    if (
        _integer(payload["schema_version"], "schema_version")
        != CANONICAL_PIPELINE_SCHEMA_VERSION
        or _integer(payload["pipeline_version"], "pipeline_version")
        != CANONICAL_PIPELINE_VERSION
        or _text(payload["subject"], "subject") != "banner"
        or _integer(payload["feature_contract_version"], "feature_contract_version")
        != CANONICAL_FEATURE_CONTRACT_VERSION
        or _integer(payload["manifest_schema_version"], "manifest_schema_version")
        != CANONICAL_MANIFEST_SCHEMA_VERSION
    ):
        raise CanonicalConfigurationError("Pipeline version metadata is invalid.")

    source_columns = tuple(
        _parse_source_column(item) for item in _sequence(payload["source_columns"])
    )
    if tuple((item.position, item.name) for item in source_columns) != tuple(
        (item.position, item.name) for item in BANNER_COLUMN_CATALOG
    ):
        raise CanonicalConfigurationError("Source-column mapping is incompatible.")

    features = tuple(_parse_feature(item) for item in _sequence(payload["features"]))
    expected_features = (
        "z_rms_velocity_mm_s",
        "temperature_c",
        "x_rms_velocity_mm_s",
        "z_peak_acceleration_g",
        "x_peak_acceleration_g",
        "z_peak_vel_comp_freq_hz",
        "x_peak_vel_comp_freq_hz",
        "z_rms_acceleration_g",
        "x_rms_acceleration_g",
        "z_kurtosis",
        "x_kurtosis",
        "z_crest_factor",
        "x_crest_factor",
        "z_peak_velocity_mm_s",
        "x_peak_velocity_mm_s",
        "z_high_freq_rms_accel_g",
        "x_high_freq_rms_accel_g",
        "rpm",
    )
    if tuple(item.name for item in features) != expected_features:
        raise CanonicalConfigurationError("Feature contract is incompatible.")
    expected_destinations: dict[str, tuple[str | None, str]] = {
        "id": ("source_id", "metadata"),
        "created_at": ("event_timestamp_utc", "metadata"),
        "fault": ("y", "target"),
        **{name: (name, "feature") for name in expected_features},
        **{
            name: (None, "excluded")
            for name in (
                "z_rms_velocity_in_s",
                "temperature_f",
                "x_rms_velocity_in_s",
                "z_peak_velocity_in_s",
                "x_peak_velocity_in_s",
            )
        },
    }
    if any(
        (item.destination, item.kind) != expected_destinations[item.name]
        for item in source_columns
    ):
        raise CanonicalConfigurationError("Source-column destinations are invalid.")
    catalog = {item.name: item for item in BANNER_COLUMN_CATALOG}
    if any(
        item.dtype != "float64"
        or item.nullable
        or item.source_column != item.name
        or item.unit != catalog[item.name].canonical_unit
        or item.availability != "event_time"
        for item in features
    ):
        raise CanonicalConfigurationError("Feature metadata is incompatible.")

    denylist_items = tuple(
        _text(item, "feature_denylist")
        for item in _sequence(payload["feature_denylist"])
    )
    if len(denylist_items) != len(set(denylist_items)) or set(expected_features) & set(
        denylist_items
    ):
        raise CanonicalConfigurationError("Feature denylist is invalid.")
    required_exclusions = {
        "id",
        "created_at",
        "fault",
        "target_slug",
        "y",
        "occurrence_id",
        "partition",
        "z_rms_velocity_in_s",
        "temperature_f",
        "x_rms_velocity_in_s",
        "z_peak_velocity_in_s",
        "x_peak_velocity_in_s",
    }
    if not required_exclusions.issubset(denylist_items):
        raise CanonicalConfigurationError("Feature denylist is incomplete.")

    trusted_relations = tuple(
        _parse_trusted_relation(item)
        for item in _sequence(payload["trusted_unit_relations"])
    )
    policy = load_banner_quality_policy()
    relations = {item.relation_id: item for item in policy.unit_relations}
    if set(relations) != {item.relation_id for item in trusted_relations} or any(
        {
            relation.trusted_column,
            relation.excluded_column,
        }
        != {
            relations[relation.relation_id].left_column,
            relations[relation.relation_id].right_column,
        }
        or relation.trusted_column not in expected_features
        or relation.excluded_column in expected_features
        for relation in trusted_relations
    ):
        raise CanonicalConfigurationError("Trusted unit relations are invalid.")

    precedence = tuple(
        _enum_value(Disposition, item, "disposition_precedence")
        for item in _sequence(payload["disposition_precedence"])
    )
    if precedence != (
        Disposition.REJECTED,
        Disposition.CORRECTED,
        Disposition.MAPPED,
        Disposition.FLAGGED,
        Disposition.KEPT,
    ):
        raise CanonicalConfigurationError("Disposition precedence is invalid.")

    grouping = _mapping(payload["occurrence_grouping"], "occurrence_grouping")
    _exact_keys(
        grouping,
        (
            "stream",
            "gap_multiplier",
            "gap_quantile",
            "gap_fit_scope",
            "duration_limit_seconds",
            "duration_break_operator",
            "gap_boundary_inclusive",
        ),
        "occurrence_grouping",
    )
    if (
        _text(grouping["stream"], "stream") != "single_source_stream"
        or _text(grouping["gap_fit_scope"], "gap_fit_scope")
        != "final_train_occurrences"
        or _text(grouping["duration_break_operator"], "duration_break_operator")
        != "greater_than_or_equal"
        or _boolean(grouping["gap_boundary_inclusive"], "gap")
    ):
        raise CanonicalConfigurationError("Occurrence grouping policy is invalid.")

    partitioning = _mapping(payload["partitioning"], "partitioning")
    _exact_keys(
        partitioning,
        (
            "order",
            "target_ratios",
            "boundary_tie_break",
            "purge_uses_occurrence_gap_threshold",
            "statistics_fit_partition",
            "target_usage",
        ),
        "partitioning",
    )
    if (
        tuple(
            _text(item, "partition_order") for item in _sequence(partitioning["order"])
        )
        != tuple(item.value for item in Partition)
        or _text(partitioning["boundary_tie_break"], "tie_break") != "earliest"
        or not _boolean(partitioning["purge_uses_occurrence_gap_threshold"], "purge")
        or _text(partitioning["statistics_fit_partition"], "fit_partition")
        != Partition.TRAIN.value
        or _text(partitioning["target_usage"], "target_usage")
        != "post_partition_y_only"
    ):
        raise CanonicalConfigurationError("Partition policy is invalid.")
    ratios = tuple(
        _decimal_number(item, "target_ratios")
        for item in _sequence(partitioning["target_ratios"])
    )
    if len(ratios) != 3 or sum(ratios, Decimal(0)) != Decimal(1):
        raise CanonicalConfigurationError("Partition ratios are invalid.")

    return CanonicalPipelineConfig(
        config_id=sha256(_canonical_json_bytes(payload)).hexdigest(),
        source_columns=source_columns,
        features=features,
        feature_denylist=frozenset(denylist_items),
        trusted_unit_relations=trusted_relations,
        disposition_precedence=precedence,
        gap_multiplier=_positive_integer(grouping["gap_multiplier"], "gap_multiplier"),
        gap_quantile=_probability(grouping["gap_quantile"], "gap_quantile"),
        duration_limit_seconds=_positive_decimal(
            grouping["duration_limit_seconds"], "duration_limit_seconds"
        ),
        train_ratio=ratios[0],
        validation_ratio=ratios[1],
        test_ratio=ratios[2],
        target_usage="post_partition_y_only",
    )


def load_canonical_dataset_schema() -> CanonicalDatasetSchema:
    """Load and strictly validate local artifact schemas."""

    payload = _load_resource_json(_SCHEMA_RESOURCE_NAME)
    _exact_keys(
        payload,
        (
            "schema_version",
            "canonical_columns",
            "disposition_columns",
            "partition_columns",
        ),
        "dataset_schema",
    )
    if (
        _integer(payload["schema_version"], "schema_version")
        != CANONICAL_DATASET_SCHEMA_VERSION
    ):
        raise CanonicalConfigurationError("Dataset schema version is invalid.")
    canonical = tuple(
        _parse_dataset_field(item) for item in _sequence(payload["canonical_columns"])
    )
    dispositions = tuple(
        _parse_dataset_field(item) for item in _sequence(payload["disposition_columns"])
    )
    partitions = tuple(
        _parse_dataset_field(item) for item in _sequence(payload["partition_columns"])
    )
    config = load_canonical_pipeline_config()
    metadata_names = (
        "record_id",
        "source_position",
        "source_id",
        "event_timestamp_utc",
        "y",
        "occurrence_id",
        "partition",
        "split_exclusion_reason",
    )
    if tuple(item.name for item in canonical) != metadata_names + config.feature_names:
        raise CanonicalConfigurationError("Canonical artifact schema is invalid.")
    if tuple(item.name for item in partitions) != (*config.feature_names, "y"):
        raise CanonicalConfigurationError("Partition artifact schema is invalid.")
    if tuple(item.name for item in dispositions) != (
        "record_id",
        "source_position",
        "disposition",
        "reason_codes",
        "quality_matches",
        "transformations",
        "dataset_destination",
    ):
        raise CanonicalConfigurationError("Disposition artifact schema is invalid.")
    for fields in (canonical, dispositions, partitions):
        _validate_dataset_field_types(fields)
    return CanonicalDatasetSchema(
        schema_id=sha256(_canonical_json_bytes(payload)).hexdigest(),
        canonical_fields=canonical,
        disposition_fields=dispositions,
        partition_fields=partitions,
    )


def canonical_label_map_from_inventory(
    inventory: FaultLabelInventory,
) -> CanonicalLabelMap:
    """Adapt the validated SEN-30 inventory without semantic merging."""

    mapping = CanonicalLabelMap(
        inventory_id=inventory.inventory_id,
        source_sha256=inventory.source_fingerprint.sha256,
        entries=tuple(
            CanonicalLabelEntry(raw_label=item.raw_label, slug=item.slug)
            for item in inventory.entries
        ),
    )
    _validate_label_map(mapping)
    return mapping


def project_banner_features(
    dataframe: pd.DataFrame,
    *,
    config: CanonicalPipelineConfig | None = None,
) -> pd.DataFrame:
    """Project exactly the 18 ordered inference features, fail-closed."""

    selected_config = config or load_canonical_pipeline_config()
    if tuple(dataframe.columns) != BANNER_COLUMN_NAMES:
        raise CanonicalContractError(
            "Feature projection requires the exact source schema."
        )
    projected = dataframe.loc[:, list(selected_config.feature_names)].copy()
    if tuple(projected.columns) != selected_config.feature_names or any(
        str(projected[name].dtype).lower() != "float64"
        for name in selected_config.feature_names
    ):
        raise CanonicalContractError("Feature projection does not match its contract.")
    if set(projected.columns) & selected_config.feature_denylist:
        raise CanonicalContractError("Feature projection contains a denied column.")
    return projected


def _materialize_feature_values(
    dataframe: pd.DataFrame,
    config: CanonicalPipelineConfig,
) -> dict[str, tuple[float, ...]]:
    return {
        name: tuple(float(value) for value in dataframe[name])
        for name in config.feature_names
    }


def build_banner_dataset(
    *,
    input_path: Path,
    manifest_path: Path,
    inventory_path: Path,
    baseline_json_path: Path,
    baseline_markdown_path: Path,
    lock_path: Path,
    output_directory: Path,
) -> CanonicalBuildResult:
    """Read ``banner.csv`` only through the audited source port and build locally."""

    inventory = load_fault_label_inventory(
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_json_path,
        baseline_markdown_path=baseline_markdown_path,
    )
    label_map = canonical_label_map_from_inventory(inventory)
    receipt = consume_banner_source_audited(
        input_path=input_path,
        manifest_path=manifest_path,
        consumer=parse_banner_csv,
    )
    if receipt.pre_fingerprint != receipt.post_fingerprint:
        raise CanonicalContractError("Source integrity receipt is inconsistent.")
    return build_canonical_dataset(
        dataframe=receipt.result,
        source_fingerprint=receipt.pre_fingerprint,
        label_map=label_map,
        lock_path=lock_path,
        output_directory=output_directory,
    )


def build_canonical_dataset(
    *,
    dataframe: pd.DataFrame,
    source_fingerprint: BannerSourceFingerprint,
    label_map: CanonicalLabelMap,
    lock_path: Path,
    output_directory: Path,
) -> CanonicalBuildResult:
    """Transform an already loaded table and atomically publish local artifacts."""

    config = load_canonical_pipeline_config()
    schema = load_canonical_dataset_schema()
    policy = load_banner_quality_policy()
    _validate_source_fingerprint(source_fingerprint)
    _validate_label_map(label_map)
    if label_map.source_sha256 != source_fingerprint.sha256:
        raise CanonicalLabelError("Label inventory does not match the source identity.")
    try:
        lock_sha256 = _hash_regular_file(lock_path)
    except OSError:
        raise CanonicalConfigurationError(
            "Frozen workspace lock is unavailable."
        ) from None

    report = validate_banner_dataframe(dataframe)
    if not report.is_valid:
        codes = ",".join(
            sorted({item.code.value for item in report.blocking_violations})
        )
        raise CanonicalContractError(f"Banner contract failed with codes: {codes}.")
    if dataframe.empty:
        raise CanonicalPartitionError("Canonical pipeline requires source records.")

    records = _build_records(
        dataframe=dataframe,
        source_sha256=source_fingerprint.sha256,
    )
    feature_values = _materialize_feature_values(dataframe, config)
    _apply_non_statistical_quality(
        dataframe=dataframe,
        records=records,
        config=config,
        policy=policy,
    )
    eligible_indices = tuple(
        index
        for index, record in enumerate(records)
        if _resolve_disposition(policy, record.rule_ids) is not Disposition.REJECTED
    )
    if not eligible_indices:
        raise CanonicalPartitionError("Quality policy rejected every source record.")

    sorted_eligible = tuple(
        sorted(eligible_indices, key=lambda index: _record_order_key(records[index]))
    )
    _occurrences, gap_fit = _fit_final_train_temporal_partitions(
        records=records,
        ordered_indices=sorted_eligible,
        source_sha256=source_fingerprint.sha256,
        config=config,
    )
    gap_threshold = gap_fit.threshold

    train_indices = tuple(
        index
        for index in sorted_eligible
        if records[index].partition is Partition.TRAIN
    )
    iqr_fences = _fit_iqr_fences(
        feature_values=feature_values,
        record_indices=train_indices,
        records=records,
        config=config,
        policy=policy,
    )
    _apply_iqr_quality(
        feature_values=feature_values,
        records=records,
        record_indices=eligible_indices,
        fences=iqr_fences,
        config=config,
    )
    target_values = _map_targets_after_partition(
        dataframe=dataframe,
        records=records,
        label_map=label_map,
    )

    canonical, dispositions, partitions = _build_artifact_frames(
        feature_values=feature_values,
        target_values=target_values,
        records=records,
        sorted_eligible=sorted_eligible,
        config=config,
        schema=schema,
        policy=policy,
    )
    gates = _calculate_leakage_gates(
        canonical=canonical,
        dispositions=dispositions,
        partitions=partitions,
        config=config,
        gap_threshold=gap_threshold,
        gap_fit=gap_fit,
    )
    if not all(gates.values()):
        raise CanonicalPartitionError("One or more temporal leakage gates failed.")

    output, manifest = _write_artifacts_atomically(
        output_directory=output_directory,
        canonical=canonical,
        dispositions=dispositions,
        partitions=partitions,
        config=config,
        schema=schema,
        policy=policy,
        label_map=label_map,
        source_fingerprint=source_fingerprint,
        lock_sha256=lock_sha256,
        gap_fit=gap_fit,
        iqr_fences=iqr_fences,
        gates=gates,
    )
    return _build_result(output, manifest)


def check_banner_dataset(
    *,
    manifest_path: Path,
    inventory_path: Path,
    baseline_json_path: Path,
    baseline_markdown_path: Path,
    output_directory: Path,
    lock_path: Path,
) -> CanonicalCheckResult:
    """Load the approved public identity and validate one local build offline."""

    inventory = load_fault_label_inventory(
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_json_path,
        baseline_markdown_path=baseline_markdown_path,
    )
    return check_canonical_dataset(
        output_directory=output_directory,
        lock_path=lock_path,
        source_fingerprint=inventory.source_fingerprint,
        label_map=canonical_label_map_from_inventory(inventory),
        expected_source_row_count=inventory.row_count,
    )


def check_canonical_dataset(
    *,
    output_directory: Path,
    lock_path: Path,
    source_fingerprint: BannerSourceFingerprint,
    label_map: CanonicalLabelMap,
    expected_source_row_count: int,
) -> CanonicalCheckResult:
    """Validate manifest, content hashes, reconciliation, and leakage read-only."""

    config = load_canonical_pipeline_config()
    schema = load_canonical_dataset_schema()
    policy = load_banner_quality_policy()
    _validate_source_fingerprint(source_fingerprint)
    _validate_label_map(label_map)
    if label_map.source_sha256 != source_fingerprint.sha256:
        raise CanonicalCheckError("Approved checker identities are inconsistent.")
    if type(expected_source_row_count) is not int or expected_source_row_count <= 0:
        raise CanonicalCheckError("Approved source row count is invalid.")
    try:
        lock_sha256 = _hash_regular_file(lock_path)
    except OSError:
        raise CanonicalCheckError("Frozen workspace lock is unavailable.") from None
    directory = output_directory.resolve()
    if not directory.is_dir() or directory.is_symlink():
        raise CanonicalCheckError("Canonical output directory is unavailable.")
    actual_names = tuple(sorted(item.name for item in directory.iterdir()))
    if actual_names != tuple(sorted(CANONICAL_ARTIFACT_FILENAMES)) or any(
        item.is_symlink() or not item.is_file() for item in directory.iterdir()
    ):
        raise CanonicalCheckError("Canonical output file set is invalid.")
    manifest_path = directory / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        raise CanonicalCheckError("Canonical manifest is unavailable.") from None
    manifest = _decode_json(manifest_bytes, CanonicalCheckError)
    if _manifest_json_bytes(manifest) != manifest_bytes:
        raise CanonicalCheckError("Canonical manifest serialization is invalid.")
    _validate_manifest_shape(
        manifest,
        config=config,
        schema=schema,
        policy=policy,
        source_fingerprint=source_fingerprint,
        label_map=label_map,
        expected_source_row_count=expected_source_row_count,
    )

    components = _mapping_for_check(manifest["components"], "components")
    if (
        components.get("pipeline_config_id") != config.config_id
        or components.get("dataset_schema_id") != schema.schema_id
        or components.get("quality_policy_id") != policy.policy_id
        or components.get("fault_label_inventory_id") != label_map.inventory_id
        or components.get("uv_lock_sha256") != lock_sha256
        or components.get("banner_contract_version") != BANNER_CONTRACT_VERSION
        or components.get("fault_label_normalization_version")
        != FAULT_LABEL_NORMALIZATION_VERSION
        or components.get("fault_label_unicode_version") != FAULT_LABEL_UNICODE_VERSION
    ):
        raise CanonicalCheckError("Canonical manifest components are stale.")

    frames = _read_artifact_frames(directory, schema)
    allowed_targets = {item.slug for item in label_map.entries}
    if any(
        str(item) not in allowed_targets for item in frames["canonical.parquet"]["y"]
    ):
        raise CanonicalCheckError("Canonical target is not in the approved inventory.")
    artifact_hashes = _validate_artifact_hashes(
        directory=directory,
        manifest=manifest,
        frames=frames,
    )
    fit = _mapping_for_check(manifest["fit"], "fit")
    gap_threshold = _decimal_text_value(
        fit.get("occurrence_gap_threshold_seconds"),
        CanonicalCheckError,
    )
    recomputed_gap_fit = _fit_gap_from_canonical(frames["canonical.parquet"], config)
    if (
        recomputed_gap_fit.threshold != gap_threshold
        or fit.get("occurrence_gap_fit_record_count")
        != len(recomputed_gap_fit.record_ids)
        or fit.get("occurrence_gap_fit_occurrence_count")
        != len(recomputed_gap_fit.occurrence_ids)
        or fit.get("occurrence_gap_fit_membership_sha256")
        != recomputed_gap_fit.membership_sha256
    ):
        raise CanonicalCheckError(
            "Occurrence fit is not derived from final train only."
        )
    fences = _fit_iqr_from_canonical(frames["canonical.parquet"], config)
    if fit.get("target_usage") != config.target_usage:
        raise CanonicalCheckError("Target usage contract is invalid.")
    if (
        fit.get("iqr_fences_sha256")
        != sha256(_canonical_json_bytes(_fence_payload(fences))).hexdigest()
    ):
        raise CanonicalCheckError("IQR statistics are not derived from train only.")

    partitions = {
        partition: frames[f"{partition.value}.parquet"] for partition in Partition
    }
    gates = _calculate_leakage_gates(
        canonical=frames["canonical.parquet"],
        dispositions=frames["dispositions.parquet"],
        partitions=partitions,
        config=config,
        gap_threshold=gap_threshold,
        gap_fit=recomputed_gap_fit,
    )
    manifest_gates = _mapping_for_check(manifest["gates"], "gates")
    if not all(gates.values()) or manifest_gates != gates:
        raise CanonicalCheckError("Temporal leakage gates failed.")
    _validate_manifest_reconciliations(manifest, frames)
    expected_dataset_id = _calculate_dataset_id(manifest)
    if manifest.get("dataset_id") != expected_dataset_id:
        raise CanonicalCheckError("Dataset identifier does not match content.")
    return _check_result(manifest, artifact_hashes)


def _build_records(
    *,
    dataframe: pd.DataFrame,
    source_sha256: str,
) -> list[_Record]:
    row_hashes: list[str] = []
    target_independent_row_hashes: list[str] = []
    timestamps: list[BannerUtcTimestamp] = []
    target_position = BANNER_COLUMN_NAMES.index("fault")
    for row in dataframe.itertuples(index=False, name=None):
        row_hashes.append(sha256(_canonical_json_bytes(list(row))).hexdigest())
        target_independent_row_hashes.append(
            sha256(
                _canonical_json_bytes(
                    [*row[:target_position], *row[target_position + 1 :]]
                )
            ).hexdigest()
        )
    for raw_timestamp in dataframe["created_at"]:
        parsed = parse_banner_utc_timestamp(raw_timestamp)
        if parsed is None:
            raise CanonicalContractError("Event timestamp is outside the contract.")
        timestamps.append(parsed)
    duplicate_ordinals: dict[str, int] = defaultdict(int)
    records: list[_Record] = []
    for source_index, row_hash in enumerate(row_hashes):
        duplicate_ordinal = duplicate_ordinals[row_hash]
        duplicate_ordinals[row_hash] += 1
        record_id = sha256(
            (f"record.v1:{source_sha256}:{row_hash}:{duplicate_ordinal}").encode(
                "ascii"
            )
        ).hexdigest()
        records.append(
            _Record(
                source_index=source_index,
                source_position=source_index + 1,
                source_id=int(dataframe.iloc[source_index]["id"]),
                timestamp=timestamps[source_index],
                timestamp_text=timestamps[source_index].canonical_text(),
                row_hash=row_hash,
                target_independent_row_hash=target_independent_row_hashes[source_index],
                record_id=record_id,
            )
        )
    return records


def _map_targets_after_partition(
    *,
    dataframe: pd.DataFrame,
    records: list[_Record],
    label_map: CanonicalLabelMap,
) -> tuple[str, ...]:
    targets: list[str] = []
    for source_index, raw_label in enumerate(dataframe["fault"]):
        if not isinstance(raw_label, str):
            raise CanonicalLabelError("Raw fault label must be text.")
        targets.append(label_map.resolve(raw_label))
        records[source_index].transformations.append(
            f"fault_to_y.v{FAULT_LABEL_NORMALIZATION_VERSION}:post_partition"
        )
    return tuple(targets)


def _apply_non_statistical_quality(
    *,
    dataframe: pd.DataFrame,
    records: list[_Record],
    config: CanonicalPipelineConfig,
    policy: BannerQualityPolicy,
) -> None:
    by_source_id: dict[int, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        by_source_id[record.source_id].append(index)
    for group in by_source_id.values():
        if len(group) < 2:
            continue
        hashes = {records[index].target_independent_row_hash for index in group}
        rule_id = (
            "duplicate.identical.map_identity"
            if len(hashes) == 1
            else "duplicate.conflicting.reject"
        )
        for index in group:
            records[index].rule_ids.append(rule_id)
            records[index].quality_matches.append(rule_id)

    trusted_by_id = {item.relation_id: item for item in config.trusted_unit_relations}
    for relation in policy.unit_relations:
        trust = trusted_by_id[relation.relation_id]
        left_values = dataframe[relation.left_column]
        right_values = dataframe[relation.right_column]
        for index, (left, right) in enumerate(
            zip(left_values, right_values, strict=True)
        ):
            if _unit_pair_is_consistent(
                left=float(left), right=float(right), relation=relation
            ):
                continue
            rule_id = "unit.inconsistent.deterministic"
            records[index].rule_ids.append(rule_id)
            records[index].quality_matches.append(
                "|".join(
                    (
                        rule_id,
                        trust.excluded_column,
                        relation.relation_id,
                        trust.trusted_column,
                    )
                )
            )
            records[index].transformations.append(
                f"trust_canonical_source:{relation.relation_id}:{trust.trusted_column}"
            )


def _unit_pair_is_consistent(
    *, left: float, right: float, relation: UnitRelationPolicy
) -> bool:
    multiplier = Decimal(str(relation.multiplier))
    offset = Decimal(str(relation.offset))
    absolute_tolerance = Decimal(str(relation.absolute_tolerance))
    relative_tolerance = Decimal(str(relation.relative_tolerance))
    with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
        left_decimal = Decimal.from_float(left)
        observed = Decimal.from_float(right)
        expected = left_decimal * multiplier + offset
        difference = abs(observed - expected)
        tolerance = max(
            absolute_tolerance,
            relative_tolerance * max(abs(observed), abs(expected)),
        )
        return difference <= tolerance


def _resolve_disposition(
    policy: BannerQualityPolicy, rule_ids: Sequence[str]
) -> Disposition:
    actions = {policy.rule(rule_id).action for rule_id in set(rule_ids)}
    for action in policy.effective_action_precedence:
        if action not in actions:
            continue
        return {
            Action.REJECT: Disposition.REJECTED,
            Action.CORRECT_DETERMINISTICALLY: Disposition.CORRECTED,
            Action.MAP: Disposition.MAPPED,
            Action.FLAG: Disposition.FLAGGED,
            Action.KEEP: Disposition.KEPT,
        }[action]
    return Disposition.KEPT


def _ordered_reason_codes(
    policy: BannerQualityPolicy, rule_ids: Sequence[str]
) -> tuple[str, ...]:
    selected = {policy.rule(rule_id).reason_code for rule_id in set(rule_ids)}
    return tuple(
        reason.value for reason in policy.reason_code_order if reason in selected
    )


def _record_order_key(record: _Record) -> tuple[object, ...]:
    return (record.timestamp, record.source_position)


def _fit_final_train_temporal_partitions(
    *,
    records: list[_Record],
    ordered_indices: Sequence[int],
    source_sha256: str,
    config: CanonicalPipelineConfig,
) -> tuple[list[_Occurrence], _OccurrenceGapFit]:
    gap_threshold = Decimal(-1)
    seen_states: set[tuple[Decimal, tuple[str, ...]]] = set()
    for _ in range(_MAX_TEMPORAL_FIT_ITERATIONS):
        _reset_temporal_assignments(records, ordered_indices)
        occurrences = _group_occurrences(
            records=records,
            ordered_indices=ordered_indices,
            source_sha256=source_sha256,
            config=config,
            gap_threshold=gap_threshold,
        )
        _assign_temporal_partitions(
            records=records,
            occurrences=occurrences,
            config=config,
            gap_threshold=max(gap_threshold, Decimal(0)),
        )
        train_indices = tuple(
            index
            for index in ordered_indices
            if records[index].partition is Partition.TRAIN
        )
        fitted = _fit_occurrence_gap_threshold(
            records=records,
            ordered_indices=train_indices,
            config=config,
        )
        if fitted.threshold == gap_threshold:
            return occurrences, fitted
        state = (fitted.threshold, fitted.record_ids)
        if state in seen_states:
            raise CanonicalPartitionError("Final-train temporal fit did not converge.")
        seen_states.add(state)
        gap_threshold = fitted.threshold
    raise CanonicalPartitionError("Final-train temporal fit did not converge.")


def _reset_temporal_assignments(
    records: list[_Record], ordered_indices: Sequence[int]
) -> None:
    for index in ordered_indices:
        record = records[index]
        record.occurrence_id = None
        record.partition = None
        record.split_exclusion_reason = None


def _fit_occurrence_gap_threshold(
    *,
    records: list[_Record],
    ordered_indices: Sequence[int],
    config: CanonicalPipelineConfig,
) -> _OccurrenceGapFit:
    if not ordered_indices:
        raise CanonicalPartitionError("Train partition is empty before gap fitting.")
    if any(records[index].occurrence_id is None for index in ordered_indices):
        raise CanonicalPartitionError("Train occurrence identity is unavailable.")
    record_ids = tuple(records[index].record_id for index in ordered_indices)
    occurrence_ids = tuple(
        dict.fromkeys(
            cast(str, records[index].occurrence_id) for index in ordered_indices
        )
    )
    if any(
        records[index].partition is not Partition.TRAIN for index in ordered_indices
    ):
        raise CanonicalPartitionError("Occurrence gap fit is not train-only.")
    if len(ordered_indices) < 2:
        return _OccurrenceGapFit(
            threshold=Decimal(0),
            record_ids=record_ids,
            occurrence_ids=occurrence_ids,
        )
    positive_deltas = sorted(
        delta
        for previous, current in pairwise(ordered_indices)
        if (
            delta := records[current].timestamp.seconds_since(
                records[previous].timestamp
            )
        )
        > 0
    )
    if not positive_deltas:
        threshold = Decimal(0)
    else:
        median = _linear_decimal_quantile(positive_deltas, Decimal("0.5"))
        high_quantile = _linear_decimal_quantile(positive_deltas, config.gap_quantile)
        with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
            threshold = max(
                Decimal(config.gap_multiplier) * median,
                high_quantile,
            )
    return _OccurrenceGapFit(
        threshold=threshold,
        record_ids=record_ids,
        occurrence_ids=occurrence_ids,
    )


def _linear_decimal_quantile(
    sorted_values: Sequence[Decimal], probability: Decimal
) -> Decimal:
    if not sorted_values:
        raise CanonicalPartitionError("Temporal quantile population is empty.")
    if len(sorted_values) == 1:
        return sorted_values[0]
    with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
        position = Decimal(len(sorted_values) - 1) * probability
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(sorted_values) - 1)
        fraction = position - Decimal(lower_index)
        lower = sorted_values[lower_index]
        upper = sorted_values[upper_index]
        return lower + (upper - lower) * fraction


def _group_occurrences(
    *,
    records: list[_Record],
    ordered_indices: Sequence[int],
    source_sha256: str,
    config: CanonicalPipelineConfig,
    gap_threshold: Decimal,
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    current: _Occurrence | None = None
    for record_index in ordered_indices:
        record = records[record_index]
        should_break = current is None
        if current is not None:
            previous = records[current.record_indices[-1]]
            gap = record.timestamp.seconds_since(previous.timestamp)
            duration = record.timestamp.seconds_since(current.start)
            should_break = (
                duration >= config.duration_limit_seconds or gap > gap_threshold
            )
        if should_break:
            ordinal = len(occurrences) + 1
            occurrence_id = sha256(
                (
                    f"occurrence.v1:{source_sha256}:{config.config_id}:"
                    f"{ordinal}:{record.timestamp_text}:{record.source_position}"
                ).encode("ascii")
            ).hexdigest()
            current = _Occurrence(
                ordinal=ordinal,
                occurrence_id=occurrence_id,
                start=record.timestamp,
                end=record.timestamp,
                record_indices=[record_index],
            )
            occurrences.append(current)
        else:
            if current is None:
                raise AssertionError("Occurrence state is unavailable.")
            current.end = record.timestamp
            current.record_indices.append(record_index)
        record.occurrence_id = current.occurrence_id
    return occurrences


def _assign_temporal_partitions(
    *,
    records: list[_Record],
    occurrences: list[_Occurrence],
    config: CanonicalPipelineConfig,
    gap_threshold: Decimal,
) -> None:
    if len(occurrences) < 3:
        raise CanonicalPartitionError(
            "At least three eligible occurrences are required for temporal splits."
        )
    cumulative: list[int] = []
    running = 0
    for occurrence in occurrences:
        running += len(occurrence.record_indices)
        cumulative.append(running)
    total = cumulative[-1]
    first_boundary = _nearest_boundary(
        cumulative=cumulative,
        target=Decimal(total) * config.train_ratio,
        minimum=1,
        maximum=len(occurrences) - 2,
    )
    second_boundary = _nearest_boundary(
        cumulative=cumulative,
        target=Decimal(total) * (config.train_ratio + config.validation_ratio),
        minimum=first_boundary + 1,
        maximum=len(occurrences) - 1,
    )
    train = occurrences[:first_boundary]
    validation = occurrences[first_boundary:second_boundary]
    test = occurrences[second_boundary:]
    for occurrence in train:
        occurrence.partition = Partition.TRAIN
    for occurrence in validation:
        occurrence.partition = Partition.VALIDATION
    for occurrence in test:
        occurrence.partition = Partition.TEST

    _purge_tail_before(
        previous=train,
        following=validation,
        gap_threshold=gap_threshold,
    )
    _purge_tail_before(
        previous=validation,
        following=test,
        gap_threshold=gap_threshold,
    )
    if not any(not item.purged for item in train) or not any(
        not item.purged for item in validation
    ):
        raise CanonicalPartitionError("Temporal purge emptied a required partition.")

    for occurrence in occurrences:
        for record_index in occurrence.record_indices:
            record = records[record_index]
            if occurrence.purged:
                record.partition = None
                record.split_exclusion_reason = "purge"
            else:
                record.partition = occurrence.partition


def _nearest_boundary(
    *,
    cumulative: Sequence[int],
    target: Decimal,
    minimum: int,
    maximum: int,
) -> int:
    candidates = range(minimum, maximum + 1)
    try:
        return min(
            candidates,
            key=lambda boundary: (
                abs(Decimal(cumulative[boundary - 1]) - target),
                boundary,
            ),
        )
    except ValueError:
        raise CanonicalPartitionError(
            "Temporal partition boundaries are unavailable."
        ) from None


def _purge_tail_before(
    *,
    previous: Sequence[_Occurrence],
    following: Sequence[_Occurrence],
    gap_threshold: Decimal,
) -> None:
    if not previous or not following:
        raise CanonicalPartitionError("Temporal partition is empty before purge.")
    following_start = following[0].start
    for occurrence in reversed(previous):
        delta = following_start.seconds_since(occurrence.end)
        if delta > 0 and delta >= gap_threshold:
            break
        occurrence.purged = True


def _fit_iqr_fences(
    *,
    feature_values: _FeatureValues,
    record_indices: Sequence[int],
    records: list[_Record],
    config: CanonicalPipelineConfig,
    policy: BannerQualityPolicy,
) -> dict[str, tuple[Decimal, Decimal]]:
    if not record_indices:
        raise CanonicalPartitionError("Train partition is empty before IQR fitting.")
    fences: dict[str, tuple[Decimal, Decimal]] = {}
    multiplier = Decimal(str(policy.iqr.multiplier))
    for name in config.feature_names:
        values = sorted(
            Decimal.from_float(feature_values[name][records[index].source_index])
            for index in record_indices
        )
        q1 = _linear_decimal_quantile(values, Decimal(str(policy.iqr.q1_probability)))
        q3 = _linear_decimal_quantile(values, Decimal(str(policy.iqr.q3_probability)))
        with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
            iqr = q3 - q1
            fences[name] = (q1 - multiplier * iqr, q3 + multiplier * iqr)
    return fences


def _apply_iqr_quality(
    *,
    feature_values: _FeatureValues,
    records: list[_Record],
    record_indices: Sequence[int],
    fences: Mapping[str, tuple[Decimal, Decimal]],
    config: CanonicalPipelineConfig,
) -> None:
    rule_id = "outlier.iqr.flag"
    for record_index in record_indices:
        record = records[record_index]
        for name in config.feature_names:
            value = Decimal.from_float(feature_values[name][record.source_index])
            lower, upper = fences[name]
            if value < lower or value > upper:
                record.rule_ids.append(rule_id)
                record.quality_matches.append(f"{rule_id}|{name}")


def _build_artifact_frames(
    *,
    feature_values: _FeatureValues,
    target_values: Sequence[str],
    records: list[_Record],
    sorted_eligible: Sequence[int],
    config: CanonicalPipelineConfig,
    schema: CanonicalDatasetSchema,
    policy: BannerQualityPolicy,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[Partition, pd.DataFrame]]:
    eligible_records = [records[index] for index in sorted_eligible]
    canonical_columns: dict[str, Sequence[object]] = {
        "record_id": [record.record_id for record in eligible_records],
        "source_position": [record.source_position for record in eligible_records],
        "source_id": [record.source_id for record in eligible_records],
        "event_timestamp_utc": [record.timestamp_text for record in eligible_records],
        "y": [target_values[record.source_index] for record in eligible_records],
        "occurrence_id": [record.occurrence_id for record in eligible_records],
        "partition": [
            None if record.partition is None else record.partition.value
            for record in eligible_records
        ],
        "split_exclusion_reason": [
            record.split_exclusion_reason for record in eligible_records
        ],
        **{
            name: [values[record.source_index] for record in eligible_records]
            for name, values in feature_values.items()
        },
    }
    canonical = pd.DataFrame(canonical_columns, columns=schema.canonical_names)
    _apply_frame_dtypes(canonical, schema.canonical_fields)

    disposition_columns: dict[str, list[object]] = {
        name: [] for name in schema.disposition_names
    }
    for record in records:
        disposition = _resolve_disposition(policy, record.rule_ids)
        if disposition is Disposition.REJECTED:
            destination = DatasetDestination.REJECTED
        elif record.split_exclusion_reason == "purge":
            destination = DatasetDestination.PURGE
        elif record.partition is not None:
            destination = DatasetDestination(record.partition.value)
        else:
            raise CanonicalPartitionError("Eligible record has no final destination.")
        disposition_columns["record_id"].append(record.record_id)
        disposition_columns["source_position"].append(record.source_position)
        disposition_columns["disposition"].append(disposition.value)
        disposition_columns["reason_codes"].append(
            list(_ordered_reason_codes(policy, record.rule_ids))
        )
        disposition_columns["quality_matches"].append(
            sorted(set(record.quality_matches))
        )
        disposition_columns["transformations"].append(
            sorted(set(record.transformations))
        )
        disposition_columns["dataset_destination"].append(destination.value)
    dispositions = pd.DataFrame(
        disposition_columns,
        columns=schema.disposition_names,
    )
    _apply_frame_dtypes(dispositions, schema.disposition_fields)

    partitions: dict[Partition, pd.DataFrame] = {}
    for partition in Partition:
        selected = canonical.loc[
            canonical["partition"] == partition.value,
            [*config.feature_names, "y"],
        ].copy()
        selected = selected.loc[:, list(schema.partition_names)]
        _apply_frame_dtypes(selected, schema.partition_fields)
        partitions[partition] = selected.reset_index(drop=True)
    return canonical, dispositions, partitions


def _apply_frame_dtypes(
    dataframe: pd.DataFrame, fields: Sequence[DatasetField]
) -> None:
    for item in fields:
        if item.dtype == "int64":
            dataframe[item.name] = dataframe[item.name].astype("int64")
        elif item.dtype == "float64":
            dataframe[item.name] = dataframe[item.name].astype("float64")
        elif item.dtype == "string":
            dataframe[item.name] = dataframe[item.name].astype("string")


def _calculate_leakage_gates(
    *,
    canonical: pd.DataFrame,
    dispositions: pd.DataFrame,
    partitions: Mapping[Partition, pd.DataFrame],
    config: CanonicalPipelineConfig,
    gap_threshold: Decimal,
    gap_fit: _OccurrenceGapFit,
) -> dict[str, bool]:
    destinations = tuple(str(item) for item in dispositions["dataset_destination"])
    allowed_destinations = {item.value for item in DatasetDestination}
    destinations_valid = all(item in allowed_destinations for item in destinations)
    destination_counts = Counter(destinations)
    source_count = len(dispositions)
    destination_coverage = (
        destinations_valid
        and sum(destination_counts[item] for item in allowed_destinations)
        == source_count
    )
    source_position_counts = Counter(
        int(item) for item in dispositions["source_position"]
    )
    ledger_record_counts = Counter(str(item) for item in dispositions["record_id"])
    dispositions_valid = all(
        str(item) in {value.value for value in Disposition}
        for item in dispositions["disposition"]
    )
    disposition_destination_alignment = all(
        (str(disposition) == Disposition.REJECTED.value)
        == (str(destination) == DatasetDestination.REJECTED.value)
        for disposition, destination in zip(
            dispositions["disposition"],
            dispositions["dataset_destination"],
            strict=True,
        )
    )
    ledger_complete = (
        source_position_counts == Counter(range(1, source_count + 1))
        and all(count == 1 for count in ledger_record_counts.values())
        and len(ledger_record_counts) == source_count
        and dispositions_valid
        and disposition_destination_alignment
    )
    expected_canonical_ids = Counter(
        str(row.record_id)
        for row in dispositions.itertuples(index=False)
        if str(row.dataset_destination)
        in allowed_destinations - {DatasetDestination.REJECTED.value}
    )
    canonical_ids = Counter(str(item) for item in canonical["record_id"])
    canonical_coverage = (
        destinations_valid
        and canonical_ids == expected_canonical_ids
        and all(count == 1 for count in canonical_ids.values())
    )

    occurrence_partition_sets: dict[str, set[str]] = defaultdict(set)
    occurrence_destination_sets: dict[str, set[str]] = defaultdict(set)
    ledger_destination = {
        str(row.record_id): str(row.dataset_destination)
        for row in dispositions.itertuples(index=False)
    }
    partition_alignment = True
    for row in canonical.itertuples(index=False):
        occurrence_id = str(row.occurrence_id)
        partition = None if pd.isna(row.partition) else str(row.partition)
        exclusion = (
            None
            if pd.isna(row.split_exclusion_reason)
            else str(row.split_exclusion_reason)
        )
        destination = ledger_destination.get(str(row.record_id))
        expected_destination = (
            DatasetDestination.PURGE.value if exclusion == "purge" else partition
        )
        if destination != expected_destination:
            partition_alignment = False
        occurrence_destination_sets[occurrence_id].add(str(destination))
        if partition is not None:
            occurrence_partition_sets[occurrence_id].add(partition)
    occurrence_disjoint = all(
        len(values) <= 1 for values in occurrence_partition_sets.values()
    ) and all(
        len(values) == 1 and values <= allowed_destinations
        for values in occurrence_destination_sets.values()
    )

    partition_projection = True
    for partition in Partition:
        expected = canonical.loc[
            canonical["partition"] == partition.value,
            (*config.feature_names, "y"),
        ].reset_index(drop=True)
        actual = partitions[partition].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                actual,
                expected,
                check_dtype=False,
                check_exact=True,
            )
        except AssertionError:
            partition_projection = False

    temporal_order = _partition_temporal_order(canonical)
    purge_gap = _partition_purge_gap(canonical, gap_threshold)
    feature_isolation = all(
        tuple(frame.columns) == (*config.feature_names, "y")
        and not (set(frame.columns[:-1]) & config.feature_denylist)
        for frame in partitions.values()
    )
    nonempty_partitions = all(not frame.empty for frame in partitions.values())
    ordered_train = canonical.loc[
        canonical["partition"] == Partition.TRAIN.value
    ].sort_values(
        by=["event_timestamp_utc", "source_position"],
        kind="stable",
    )
    expected_fit_record_ids = tuple(str(item) for item in ordered_train["record_id"])
    expected_fit_occurrence_ids = tuple(
        dict.fromkeys(str(item) for item in ordered_train["occurrence_id"])
    )
    statistics_train_only = (
        gap_fit.threshold == gap_threshold
        and gap_fit.record_ids == expected_fit_record_ids
        and gap_fit.occurrence_ids == expected_fit_occurrence_ids
    )
    gates = {
        "ledger.complete_destination": destination_coverage and ledger_complete,
        "canonical.eligible_coverage": canonical_coverage,
        "partitions.assignment_alignment": partition_alignment,
        "partitions.occurrence_disjoint": occurrence_disjoint,
        "partitions.projection_exact": partition_projection,
        "partitions.temporal_order": temporal_order,
        "partitions.purge_gap": purge_gap,
        "features.inference_only": feature_isolation,
        "partitions.nonempty": nonempty_partitions,
        "fit.statistics_train_only": statistics_train_only,
        "fit.target_independent": True,
    }
    if tuple(gates) != _CANONICAL_GATE_NAMES:
        raise AssertionError("Canonical gate registry is inconsistent.")
    return gates


def _partition_temporal_order(canonical: pd.DataFrame) -> bool:
    bounds: list[tuple[BannerUtcTimestamp, BannerUtcTimestamp]] = []
    for partition in Partition:
        values = [
            parse_banner_utc_timestamp(value)
            for value in canonical.loc[
                canonical["partition"] == partition.value,
                "event_timestamp_utc",
            ]
        ]
        if not values or any(value is None for value in values):
            return False
        typed = cast(list[BannerUtcTimestamp], values)
        bounds.append((min(typed), max(typed)))
    return bounds[0][1] < bounds[1][0] and bounds[1][1] < bounds[2][0]


def _partition_purge_gap(canonical: pd.DataFrame, gap_threshold: Decimal) -> bool:
    bounds: list[tuple[BannerUtcTimestamp, BannerUtcTimestamp]] = []
    for partition in Partition:
        parsed = [
            parse_banner_utc_timestamp(value)
            for value in canonical.loc[
                canonical["partition"] == partition.value,
                "event_timestamp_utc",
            ]
        ]
        if not parsed or any(item is None for item in parsed):
            return False
        typed = cast(list[BannerUtcTimestamp], parsed)
        bounds.append((min(typed), max(typed)))
    first_gap = bounds[1][0].seconds_since(bounds[0][1])
    second_gap = bounds[2][0].seconds_since(bounds[1][1])
    return first_gap >= gap_threshold and second_gap >= gap_threshold


def _validate_output_destination(output_directory: Path) -> Path:
    if output_directory.name in {"", ".", ".."} or ".." in output_directory.parts:
        raise CanonicalOutputError("Canonical output destination is unsafe.")
    try:
        destination = Path(os.path.abspath(os.fspath(output_directory)))
    except (OSError, TypeError, ValueError):
        raise CanonicalOutputError("Canonical output destination is invalid.") from None
    if destination.parent == destination:
        raise CanonicalOutputError("Canonical output destination is unsafe.")

    _reject_linked_path_components(destination)
    try:
        destination_metadata = os.lstat(destination)
    except FileNotFoundError:
        destination_metadata = None
    except OSError:
        raise CanonicalOutputError(
            "Canonical output path metadata is unavailable."
        ) from None
    if destination_metadata is not None and not stat.S_ISDIR(
        destination_metadata.st_mode
    ):
        raise CanonicalOutputError("Canonical output destination is invalid.")
    worktree = _find_enclosing_git_worktree(destination)
    if worktree is None:
        return destination
    try:
        relative = destination.relative_to(worktree).as_posix()
    except ValueError:
        raise CanonicalOutputError(
            "Canonical output destination escapes its worktree."
        ) from None
    result = _run_git(
        worktree,
        "check-ignore",
        "--quiet",
        "--",
        relative,
    )
    if result.returncode == 1:
        raise CanonicalOutputError(
            "Canonical output destination is not ignored by Git."
        )
    if result.returncode != 0:
        raise CanonicalOutputError("Canonical output Git-ignore status is unavailable.")
    return destination


def _reject_linked_path_components(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            break
        except OSError:
            raise CanonicalOutputError(
                "Canonical output path metadata is unavailable."
            ) from None
        try:
            is_junction = candidate.is_junction()
        except OSError:
            raise CanonicalOutputError(
                "Canonical output path metadata is unavailable."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or is_junction:
            raise CanonicalOutputError(
                "Canonical output path cannot contain a link or junction."
            )


def _find_enclosing_git_worktree(destination: Path) -> Path | None:
    probe = destination
    while True:
        try:
            metadata = os.lstat(probe)
        except FileNotFoundError:
            if probe.parent == probe:
                return None
            probe = probe.parent
            continue
        except OSError:
            raise CanonicalOutputError(
                "Canonical output path metadata is unavailable."
            ) from None
        if not stat.S_ISDIR(metadata.st_mode):
            probe = probe.parent
        break

    for candidate in (probe, *probe.parents):
        marker = candidate / ".git"
        try:
            marker_metadata = os.lstat(marker)
        except FileNotFoundError:
            continue
        except OSError:
            raise CanonicalOutputError(
                "Git worktree metadata is unavailable."
            ) from None
        try:
            marker_is_junction = marker.is_junction()
        except OSError:
            raise CanonicalOutputError(
                "Git worktree metadata is unavailable."
            ) from None
        if stat.S_ISLNK(marker_metadata.st_mode) or marker_is_junction:
            raise CanonicalOutputError("Git worktree metadata is unsafe.")
        result = _run_git(candidate, "rev-parse", "--show-toplevel")
        if result.returncode != 0:
            raise CanonicalOutputError("Git worktree identity is unavailable.")
        try:
            reported = Path(result.stdout.strip()).resolve(strict=True)
            expected = candidate.resolve(strict=True)
        except (OSError, ValueError):
            raise CanonicalOutputError("Git worktree identity is invalid.") from None
        if reported != expected:
            raise CanonicalOutputError("Git worktree identity is inconsistent.")
        return expected
    return None


def _run_git(worktree: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise CanonicalOutputError("Git output validation is unavailable.")
    try:
        return subprocess.run(  # noqa: S603 - executable and arguments are bounded here.
            (git_executable, "-C", os.fspath(worktree), *arguments),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise CanonicalOutputError("Git output validation is unavailable.") from None


def _write_artifacts_atomically(
    *,
    output_directory: Path,
    canonical: pd.DataFrame,
    dispositions: pd.DataFrame,
    partitions: Mapping[Partition, pd.DataFrame],
    config: CanonicalPipelineConfig,
    schema: CanonicalDatasetSchema,
    policy: BannerQualityPolicy,
    label_map: CanonicalLabelMap,
    source_fingerprint: BannerSourceFingerprint,
    lock_sha256: str,
    gap_fit: _OccurrenceGapFit,
    iqr_fences: Mapping[str, tuple[Decimal, Decimal]],
    gates: Mapping[str, bool],
) -> tuple[Path, dict[str, object]]:
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
        raise CanonicalOutputError(
            "Canonical staging directory is unavailable."
        ) from None

    frames: dict[str, pd.DataFrame] = {
        "canonical.parquet": canonical,
        "dispositions.parquet": dispositions,
        **{
            f"{partition.value}.parquet": frame
            for partition, frame in partitions.items()
        },
    }
    try:
        for filename, frame in frames.items():
            frame.to_parquet(
                staging / filename,
                engine="pyarrow",
                compression=_PARQUET_COMPRESSION,
                index=False,
            )
        artifacts: list[dict[str, object]] = [
            {
                "filename": filename,
                "row_count": len(frame),
                "column_count": len(frame.columns),
                "logical_sha256": _logical_dataframe_hash(frame),
                "physical_sha256": _hash_regular_file(staging / filename),
            }
            for filename, frame in frames.items()
        ]
        manifest = _build_manifest(
            canonical=canonical,
            dispositions=dispositions,
            config=config,
            schema=schema,
            policy=policy,
            label_map=label_map,
            source_fingerprint=source_fingerprint,
            lock_sha256=lock_sha256,
            gap_fit=gap_fit,
            iqr_fences=iqr_fences,
            gates=gates,
            artifacts=artifacts,
        )
        (staging / "manifest.json").write_bytes(_manifest_json_bytes(manifest))
        if destination.exists():
            if _directories_have_equal_files(staging, destination):
                shutil.rmtree(staging)
                return destination, manifest
            raise CanonicalOutputError(
                "Canonical output already exists with different content."
            )
        os.replace(staging, destination)
    except CanonicalPipelineError:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    except (OSError, ValueError, TypeError):
        if staging.exists():
            shutil.rmtree(staging)
        raise CanonicalOutputError(
            "Canonical artifacts could not be written."
        ) from None
    return destination, manifest


def _build_manifest(
    *,
    canonical: pd.DataFrame,
    dispositions: pd.DataFrame,
    config: CanonicalPipelineConfig,
    schema: CanonicalDatasetSchema,
    policy: BannerQualityPolicy,
    label_map: CanonicalLabelMap,
    source_fingerprint: BannerSourceFingerprint,
    lock_sha256: str,
    gap_fit: _OccurrenceGapFit,
    iqr_fences: Mapping[str, tuple[Decimal, Decimal]],
    gates: Mapping[str, bool],
    artifacts: list[dict[str, object]],
) -> dict[str, object]:
    disposition_counts = Counter(str(item) for item in dispositions["disposition"])
    destination_counts = Counter(
        str(item) for item in dispositions["dataset_destination"]
    )
    partition_summaries: list[dict[str, object]] = []
    for partition in Partition:
        selected = canonical[canonical["partition"] == partition.value]
        partition_summaries.append(
            {
                "name": partition.value,
                "row_count": len(selected),
                "occurrence_count": int(selected["occurrence_id"].nunique()),
                "target_ratio": _decimal_text(
                    {
                        Partition.TRAIN: config.train_ratio,
                        Partition.VALIDATION: config.validation_ratio,
                        Partition.TEST: config.test_ratio,
                    }[partition]
                ),
            }
        )
    manifest: dict[str, object] = {
        "manifest_schema_version": CANONICAL_MANIFEST_SCHEMA_VERSION,
        "dataset_id": "0" * _SHA256_LENGTH,
        "source": {
            "basename": "banner.csv",
            "size_bytes": source_fingerprint.size_bytes,
            "sha256": source_fingerprint.sha256,
        },
        "components": {
            "pipeline_config_id": config.config_id,
            "dataset_schema_id": schema.schema_id,
            "banner_contract_version": BANNER_CONTRACT_VERSION,
            "quality_policy_id": policy.policy_id,
            "fault_label_inventory_id": label_map.inventory_id,
            "fault_label_normalization_version": FAULT_LABEL_NORMALIZATION_VERSION,
            "fault_label_unicode_version": FAULT_LABEL_UNICODE_VERSION,
            "uv_lock_sha256": lock_sha256,
        },
        "fit": {
            "occurrence_gap_fit_scope": "final_train_occurrences",
            "occurrence_gap_threshold_seconds": _decimal_text(gap_fit.threshold),
            "occurrence_gap_fit_record_count": len(gap_fit.record_ids),
            "occurrence_gap_fit_occurrence_count": len(gap_fit.occurrence_ids),
            "occurrence_gap_fit_membership_sha256": gap_fit.membership_sha256,
            "iqr_fit_partition": Partition.TRAIN.value,
            "iqr_fences_sha256": sha256(
                _canonical_json_bytes(_fence_payload(iqr_fences))
            ).hexdigest(),
            "scaling": "not_applied",
            "imputation": "not_applied",
            "target_usage": config.target_usage,
        },
        "summary": {
            "source_row_count": len(dispositions),
            "canonical_row_count": len(canonical),
            "occurrence_count": int(canonical["occurrence_id"].nunique()),
            "disposition_counts": _complete_counts(disposition_counts, Disposition),
            "destination_counts": _complete_counts(
                destination_counts, DatasetDestination
            ),
        },
        "partitions": partition_summaries,
        "artifacts": artifacts,
        "gates": dict(gates),
    }
    manifest["dataset_id"] = _calculate_dataset_id(manifest)
    return manifest


def _calculate_dataset_id(manifest: Mapping[str, object]) -> str:
    artifacts = _sequence_for_check(manifest.get("artifacts"), "artifacts")
    identity = {
        "manifest_schema_version": manifest.get("manifest_schema_version"),
        "source": manifest.get("source"),
        "components": manifest.get("components"),
        "fit": manifest.get("fit"),
        "summary": manifest.get("summary"),
        "partitions": manifest.get("partitions"),
        "artifact_logical_hashes": [
            {
                "filename": _mapping_for_check(item, "artifact").get("filename"),
                "logical_sha256": _mapping_for_check(item, "artifact").get(
                    "logical_sha256"
                ),
            }
            for item in artifacts
        ],
        "gates": manifest.get("gates"),
    }
    return sha256(_canonical_json_bytes(identity)).hexdigest()


def _read_artifact_frames(
    directory: Path, schema: CanonicalDatasetSchema
) -> dict[str, pd.DataFrame]:
    expected_fields = {
        "canonical.parquet": schema.canonical_fields,
        "dispositions.parquet": schema.disposition_fields,
        **{
            f"{partition.value}.parquet": schema.partition_fields
            for partition in Partition
        },
    }
    frames: dict[str, pd.DataFrame] = {}
    for filename, fields in expected_fields.items():
        try:
            frame = pd.read_parquet(directory / filename)
        except Exception:
            raise CanonicalCheckError(
                "Canonical Parquet artifact is invalid."
            ) from None
        if tuple(frame.columns) != tuple(item.name for item in fields):
            raise CanonicalCheckError("Canonical Parquet schema is invalid.")
        _validate_frame_values(frame, fields)
        frames[filename] = frame
    return frames


def _validate_frame_values(
    dataframe: pd.DataFrame, fields: Sequence[DatasetField]
) -> None:
    for item in fields:
        series = dataframe[item.name]
        if not item.nullable and bool(series.isna().any()):
            raise CanonicalCheckError("Canonical artifact contains a forbidden null.")
        if item.dtype == "int64" and str(series.dtype).lower() != "int64":
            raise CanonicalCheckError("Canonical integer dtype is invalid.")
        if item.dtype == "float64" and (
            str(series.dtype).lower() != "float64"
            or any(not isfinite(float(value)) for value in series)
        ):
            raise CanonicalCheckError("Canonical numeric dtype is invalid.")
        if item.dtype == "string" and any(
            not isinstance(value, str) for value in series if not pd.isna(value)
        ):
            raise CanonicalCheckError("Canonical string dtype is invalid.")
        if item.dtype == "list[string]" and any(
            not _is_text_sequence(value) for value in series
        ):
            raise CanonicalCheckError("Canonical list dtype is invalid.")


def _is_text_sequence(value: object) -> bool:
    if isinstance(value, str | bytes) or not isinstance(value, Iterable):
        return False
    return all(isinstance(item, str) for item in cast(Iterable[object], value))


def _validate_artifact_hashes(
    *,
    directory: Path,
    manifest: Mapping[str, object],
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, str]:
    entries = _sequence_for_check(manifest.get("artifacts"), "artifacts")
    by_name: dict[str, Mapping[str, object]] = {}
    for item in entries:
        entry = _mapping_for_check(item, "artifact")
        filename = entry.get("filename")
        if not isinstance(filename, str) or filename in by_name:
            raise CanonicalCheckError("Canonical artifact registry is invalid.")
        by_name[filename] = entry
    if set(by_name) != set(frames):
        raise CanonicalCheckError("Canonical artifact registry is incomplete.")
    physical_hashes: dict[str, str] = {}
    for filename, frame in frames.items():
        entry = by_name[filename]
        physical = _hash_regular_file(directory / filename)
        logical = _logical_dataframe_hash(frame)
        if (
            entry.get("physical_sha256") != physical
            or entry.get("logical_sha256") != logical
            or entry.get("row_count") != len(frame)
            or entry.get("column_count") != len(frame.columns)
        ):
            raise CanonicalCheckError("Canonical artifact hash is invalid.")
        physical_hashes[filename] = physical
    return physical_hashes


def _fit_gap_from_canonical(
    canonical: pd.DataFrame, config: CanonicalPipelineConfig
) -> _OccurrenceGapFit:
    ordered = canonical.loc[
        canonical["partition"] == Partition.TRAIN.value
    ].sort_values(
        by=["event_timestamp_utc", "source_position"],
        kind="stable",
    )
    if ordered.empty:
        raise CanonicalCheckError("Train partition is empty before gap fitting.")
    timestamps = [
        parse_banner_utc_timestamp(value) for value in ordered["event_timestamp_utc"]
    ]
    if any(item is None for item in timestamps):
        raise CanonicalCheckError("Canonical timestamp is invalid.")
    typed = cast(list[BannerUtcTimestamp], timestamps)
    record_ids = tuple(str(item) for item in ordered["record_id"])
    occurrence_ids = tuple(
        dict.fromkeys(str(item) for item in ordered["occurrence_id"])
    )
    if len(typed) < 2:
        return _OccurrenceGapFit(
            threshold=Decimal(0),
            record_ids=record_ids,
            occurrence_ids=occurrence_ids,
        )
    positive = sorted(
        delta
        for previous, current in pairwise(typed)
        if (delta := current.seconds_since(previous)) > 0
    )
    if not positive:
        threshold = Decimal(0)
    else:
        median = _linear_decimal_quantile(positive, Decimal("0.5"))
        high = _linear_decimal_quantile(positive, config.gap_quantile)
        with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
            threshold = max(Decimal(config.gap_multiplier) * median, high)
    return _OccurrenceGapFit(
        threshold=threshold,
        record_ids=record_ids,
        occurrence_ids=occurrence_ids,
    )


def _fit_iqr_from_canonical(
    canonical: pd.DataFrame, config: CanonicalPipelineConfig
) -> dict[str, tuple[Decimal, Decimal]]:
    train = canonical[canonical["partition"] == Partition.TRAIN.value]
    if train.empty:
        raise CanonicalCheckError("Train partition is empty.")
    policy = load_banner_quality_policy()
    multiplier = Decimal(str(policy.iqr.multiplier))
    fences: dict[str, tuple[Decimal, Decimal]] = {}
    for name in config.feature_names:
        values = sorted(Decimal.from_float(float(item)) for item in train[name])
        q1 = _linear_decimal_quantile(values, Decimal(str(policy.iqr.q1_probability)))
        q3 = _linear_decimal_quantile(values, Decimal(str(policy.iqr.q3_probability)))
        with localcontext(isolated_decimal_context(_DECIMAL_PRECISION)):
            iqr = q3 - q1
            fences[name] = (q1 - multiplier * iqr, q3 + multiplier * iqr)
    return fences


def _validate_manifest_reconciliations(
    manifest: Mapping[str, object], frames: Mapping[str, pd.DataFrame]
) -> None:
    summary = _mapping_for_check(manifest.get("summary"), "summary")
    canonical = frames["canonical.parquet"]
    dispositions = frames["dispositions.parquet"]
    _validate_destination_reconciliation(canonical, dispositions)
    disposition_counts = Counter(str(item) for item in dispositions["disposition"])
    destination_counts = Counter(
        str(item) for item in dispositions["dataset_destination"]
    )
    if (
        summary.get("source_row_count") != len(dispositions)
        or summary.get("canonical_row_count") != len(canonical)
        or summary.get("occurrence_count") != int(canonical["occurrence_id"].nunique())
        or summary.get("disposition_counts")
        != _complete_counts(disposition_counts, Disposition)
        or summary.get("destination_counts")
        != _complete_counts(destination_counts, DatasetDestination)
    ):
        raise CanonicalCheckError("Canonical summary does not reconcile.")
    partition_entries = _sequence_for_check(manifest.get("partitions"), "partitions")
    names = tuple(
        str(_mapping_for_check(item, "partition").get("name"))
        for item in partition_entries
    )
    if names != tuple(item.value for item in Partition):
        raise CanonicalCheckError("Partition manifest is incomplete.")
    by_name = {
        name: _mapping_for_check(item, "partition")
        for name, item in zip(names, partition_entries, strict=True)
    }
    for partition in Partition:
        selected = canonical[canonical["partition"] == partition.value]
        entry = by_name[partition.value]
        if (
            entry.get("row_count") != len(selected)
            or entry.get("occurrence_count") != int(selected["occurrence_id"].nunique())
            or len(frames[f"{partition.value}.parquet"]) != len(selected)
        ):
            raise CanonicalCheckError("Partition counts do not reconcile.")


def _validate_destination_reconciliation(
    canonical: pd.DataFrame, dispositions: pd.DataFrame
) -> None:
    allowed_destinations = {item.value for item in DatasetDestination}
    allowed_dispositions = {item.value for item in Disposition}
    destination_values = tuple(
        str(item) for item in dispositions["dataset_destination"]
    )
    if any(item not in allowed_destinations for item in destination_values) or any(
        str(item) not in allowed_dispositions for item in dispositions["disposition"]
    ):
        raise CanonicalCheckError("Disposition ledger contains an unknown value.")
    if any(
        (str(disposition) == Disposition.REJECTED.value)
        != (destination == DatasetDestination.REJECTED.value)
        for disposition, destination in zip(
            dispositions["disposition"], destination_values, strict=True
        )
    ):
        raise CanonicalCheckError("Disposition ledger destination is inconsistent.")

    ledger_ids = tuple(str(item) for item in dispositions["record_id"])
    canonical_ids = tuple(str(item) for item in canonical["record_id"])
    source_positions = tuple(int(item) for item in dispositions["source_position"])
    if (
        Counter(ledger_ids) != Counter(set(ledger_ids))
        or Counter(canonical_ids) != Counter(set(canonical_ids))
        or Counter(source_positions) != Counter(range(1, len(dispositions) + 1))
    ):
        raise CanonicalCheckError("Canonical row identity coverage is invalid.")
    expected_canonical_ids = Counter(
        str(row.record_id)
        for row in dispositions.itertuples(index=False)
        if str(row.dataset_destination) != DatasetDestination.REJECTED.value
    )
    if Counter(canonical_ids) != expected_canonical_ids:
        raise CanonicalCheckError("Canonical destination coverage is incomplete.")

    ledger_destination = {
        str(row.record_id): str(row.dataset_destination)
        for row in dispositions.itertuples(index=False)
    }
    occurrence_destinations: dict[str, set[str]] = defaultdict(set)
    for row in canonical.itertuples(index=False):
        partition = None if pd.isna(row.partition) else str(row.partition)
        exclusion = (
            None
            if pd.isna(row.split_exclusion_reason)
            else str(row.split_exclusion_reason)
        )
        if partition in {item.value for item in Partition} and exclusion is None:
            expected_destination = partition
        elif partition is None and exclusion == DatasetDestination.PURGE.value:
            expected_destination = DatasetDestination.PURGE.value
        else:
            raise CanonicalCheckError("Canonical destination reference is invalid.")
        actual_destination = ledger_destination[str(row.record_id)]
        if actual_destination != expected_destination:
            raise CanonicalCheckError(
                "Canonical destination reference is inconsistent."
            )
        occurrence_destinations[str(row.occurrence_id)].add(actual_destination)
    if any(len(values) != 1 for values in occurrence_destinations.values()):
        raise CanonicalCheckError("Occurrence destination is not atomic.")


def _validate_manifest_shape(
    manifest: Mapping[str, object],
    *,
    config: CanonicalPipelineConfig,
    schema: CanonicalDatasetSchema,
    policy: BannerQualityPolicy,
    source_fingerprint: BannerSourceFingerprint,
    label_map: CanonicalLabelMap,
    expected_source_row_count: int,
) -> None:
    _exact_keys_for_check(
        manifest,
        (
            "manifest_schema_version",
            "dataset_id",
            "source",
            "components",
            "fit",
            "summary",
            "partitions",
            "artifacts",
            "gates",
        ),
    )
    if (
        type(manifest.get("manifest_schema_version")) is not int
        or manifest.get("manifest_schema_version") != CANONICAL_MANIFEST_SCHEMA_VERSION
    ):
        raise CanonicalCheckError("Canonical manifest version is unsupported.")
    _require_sha256(manifest.get("dataset_id"), CanonicalCheckError)
    source = _mapping_for_check(manifest.get("source"), "source")
    _exact_keys_for_check(source, ("basename", "size_bytes", "sha256"))
    if (
        source.get("basename") != "banner.csv"
        or source.get("size_bytes") != source_fingerprint.size_bytes
        or source.get("sha256") != source_fingerprint.sha256
    ):
        raise CanonicalCheckError("Canonical source identity is invalid.")
    _require_sha256(source.get("sha256"), CanonicalCheckError)
    if type(source.get("size_bytes")) is not int or cast(int, source["size_bytes"]) < 0:
        raise CanonicalCheckError("Canonical source size is invalid.")

    components = _mapping_for_check(manifest.get("components"), "components")
    _exact_keys_for_check(
        components,
        (
            "pipeline_config_id",
            "dataset_schema_id",
            "banner_contract_version",
            "quality_policy_id",
            "fault_label_inventory_id",
            "fault_label_normalization_version",
            "fault_label_unicode_version",
            "uv_lock_sha256",
        ),
    )
    for name in (
        "pipeline_config_id",
        "dataset_schema_id",
        "quality_policy_id",
        "fault_label_inventory_id",
        "uv_lock_sha256",
    ):
        _require_sha256(components.get(name), CanonicalCheckError)
    if (
        components.get("pipeline_config_id") != config.config_id
        or components.get("dataset_schema_id") != schema.schema_id
        or type(components.get("banner_contract_version")) is not int
        or components.get("banner_contract_version") != BANNER_CONTRACT_VERSION
        or components.get("quality_policy_id") != policy.policy_id
        or components.get("fault_label_inventory_id") != label_map.inventory_id
        or type(components.get("fault_label_normalization_version")) is not int
        or components.get("fault_label_normalization_version")
        != FAULT_LABEL_NORMALIZATION_VERSION
        or components.get("fault_label_unicode_version") != FAULT_LABEL_UNICODE_VERSION
    ):
        raise CanonicalCheckError("Canonical manifest components are incompatible.")

    fit = _mapping_for_check(manifest.get("fit"), "fit")
    _exact_keys_for_check(
        fit,
        (
            "occurrence_gap_fit_scope",
            "occurrence_gap_threshold_seconds",
            "occurrence_gap_fit_record_count",
            "occurrence_gap_fit_occurrence_count",
            "occurrence_gap_fit_membership_sha256",
            "iqr_fit_partition",
            "iqr_fences_sha256",
            "scaling",
            "imputation",
            "target_usage",
        ),
    )
    threshold = _decimal_text_value(
        fit.get("occurrence_gap_threshold_seconds"), CanonicalCheckError
    )
    if (
        threshold < 0
        or fit.get("occurrence_gap_threshold_seconds") != _decimal_text(threshold)
        or fit.get("occurrence_gap_fit_scope") != "final_train_occurrences"
        or not _is_positive_manifest_integer(fit.get("occurrence_gap_fit_record_count"))
        or not _is_positive_manifest_integer(
            fit.get("occurrence_gap_fit_occurrence_count")
        )
        or fit.get("iqr_fit_partition") != Partition.TRAIN.value
        or fit.get("scaling") != "not_applied"
        or fit.get("imputation") != "not_applied"
        or fit.get("target_usage") != config.target_usage
    ):
        raise CanonicalCheckError("Canonical fit manifest is invalid.")
    _require_sha256(
        fit.get("occurrence_gap_fit_membership_sha256"), CanonicalCheckError
    )
    _require_sha256(fit.get("iqr_fences_sha256"), CanonicalCheckError)

    summary = _mapping_for_check(manifest.get("summary"), "summary")
    _exact_keys_for_check(
        summary,
        (
            "source_row_count",
            "canonical_row_count",
            "occurrence_count",
            "disposition_counts",
            "destination_counts",
        ),
    )
    source_row_count = summary.get("source_row_count")
    canonical_row_count = summary.get("canonical_row_count")
    occurrence_count = summary.get("occurrence_count")
    if (
        source_row_count != expected_source_row_count
        or not _is_positive_manifest_integer(source_row_count)
        or not _is_positive_manifest_integer(canonical_row_count)
        or not _is_positive_manifest_integer(occurrence_count)
        or cast(int, occurrence_count) > cast(int, canonical_row_count)
    ):
        raise CanonicalCheckError("Canonical summary shape is invalid.")
    disposition_counts = _mapping_for_check(
        summary.get("disposition_counts"), "disposition_counts"
    )
    destination_counts = _mapping_for_check(
        summary.get("destination_counts"), "destination_counts"
    )
    _validate_manifest_count_registry(disposition_counts, Disposition)
    _validate_manifest_count_registry(destination_counts, DatasetDestination)
    if (
        sum(cast(int, value) for value in disposition_counts.values())
        != source_row_count
        or sum(cast(int, value) for value in destination_counts.values())
        != source_row_count
        or canonical_row_count
        != cast(int, source_row_count)
        - cast(int, destination_counts[DatasetDestination.REJECTED.value])
    ):
        raise CanonicalCheckError("Canonical summary coverage is invalid.")

    partition_entries = _sequence_for_check(manifest.get("partitions"), "partitions")
    if len(partition_entries) != len(Partition):
        raise CanonicalCheckError("Partition manifest shape is invalid.")
    partition_rows: dict[str, int] = {}
    expected_ratios = {
        Partition.TRAIN.value: _decimal_text(config.train_ratio),
        Partition.VALIDATION.value: _decimal_text(config.validation_ratio),
        Partition.TEST.value: _decimal_text(config.test_ratio),
    }
    partition_names: list[str] = []
    for item in partition_entries:
        entry = _mapping_for_check(item, "partition")
        _exact_keys_for_check(
            entry,
            ("name", "row_count", "occurrence_count", "target_ratio"),
        )
        name = entry.get("name")
        if not isinstance(name, str):
            raise CanonicalCheckError("Partition manifest name is invalid.")
        partition_names.append(name)
        if (
            name not in expected_ratios
            or not _is_positive_manifest_integer(entry.get("row_count"))
            or not _is_positive_manifest_integer(entry.get("occurrence_count"))
            or entry.get("target_ratio") != expected_ratios[name]
        ):
            raise CanonicalCheckError("Partition manifest entry is invalid.")
        partition_rows[name] = cast(int, entry["row_count"])
    if tuple(partition_names) != tuple(item.value for item in Partition):
        raise CanonicalCheckError("Partition manifest registry is invalid.")
    if any(
        partition_rows[item.value] != cast(int, destination_counts[item.value])
        for item in Partition
    ) or canonical_row_count != sum(partition_rows.values()) + cast(
        int, destination_counts[DatasetDestination.PURGE.value]
    ):
        raise CanonicalCheckError("Partition manifest references are inconsistent.")

    artifacts = _sequence_for_check(manifest.get("artifacts"), "artifacts")
    if len(artifacts) != len(_PARQUET_ARTIFACT_FILENAMES):
        raise CanonicalCheckError("Canonical artifact registry shape is invalid.")
    artifact_names: list[str] = []
    artifact_rows: dict[str, int] = {}
    expected_columns = {
        "canonical.parquet": len(schema.canonical_fields),
        "dispositions.parquet": len(schema.disposition_fields),
        **{
            f"{partition.value}.parquet": len(schema.partition_fields)
            for partition in Partition
        },
    }
    for item in artifacts:
        entry = _mapping_for_check(item, "artifact")
        _exact_keys_for_check(
            entry,
            (
                "filename",
                "row_count",
                "column_count",
                "logical_sha256",
                "physical_sha256",
            ),
        )
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise CanonicalCheckError("Canonical artifact filename is invalid.")
        artifact_names.append(filename)
        if (
            filename not in expected_columns
            or not _is_nonnegative_manifest_integer(entry.get("row_count"))
            or entry.get("column_count") != expected_columns[filename]
        ):
            raise CanonicalCheckError("Canonical artifact entry is invalid.")
        _require_sha256(entry.get("logical_sha256"), CanonicalCheckError)
        _require_sha256(entry.get("physical_sha256"), CanonicalCheckError)
        artifact_rows[filename] = cast(int, entry["row_count"])
    if tuple(artifact_names) != _PARQUET_ARTIFACT_FILENAMES:
        raise CanonicalCheckError("Canonical artifact registry is invalid.")
    if (
        artifact_rows["canonical.parquet"] != canonical_row_count
        or artifact_rows["dispositions.parquet"] != source_row_count
        or any(
            artifact_rows[f"{partition.value}.parquet"]
            != partition_rows[partition.value]
            for partition in Partition
        )
    ):
        raise CanonicalCheckError("Canonical artifact references are inconsistent.")

    gates = _mapping_for_check(manifest.get("gates"), "gates")
    _exact_keys_for_check(gates, _CANONICAL_GATE_NAMES)
    if any(type(value) is not bool for value in gates.values()):
        raise CanonicalCheckError("Canonical gate registry is invalid.")


def _validate_manifest_count_registry(
    counts: Mapping[str, object], enum_type: type[StrEnum]
) -> None:
    _exact_keys_for_check(counts, tuple(item.value for item in enum_type))
    if any(not _is_nonnegative_manifest_integer(value) for value in counts.values()):
        raise CanonicalCheckError("Canonical count registry is invalid.")


def _is_nonnegative_manifest_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_positive_manifest_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _logical_dataframe_hash(dataframe: pd.DataFrame) -> str:
    digest = sha256()
    digest.update(_canonical_json_bytes(list(dataframe.columns)))
    for row in dataframe.itertuples(index=False, name=None):
        digest.update(_canonical_json_bytes([_logical_value(item) for item in row]))
    return digest.hexdigest()


def _directories_have_equal_files(left: Path, right: Path) -> bool:
    try:
        left_names = tuple(sorted(item.name for item in left.iterdir()))
        right_names = tuple(sorted(item.name for item in right.iterdir()))
        if left_names != right_names or left_names != tuple(
            sorted(CANONICAL_ARTIFACT_FILENAMES)
        ):
            return False
        return all(
            not (left / name).is_symlink()
            and not (right / name).is_symlink()
            and (left / name).is_file()
            and (right / name).is_file()
            and _hash_regular_file(left / name) == _hash_regular_file(right / name)
            for name in left_names
        )
    except OSError:
        return False


def _build_result(output: Path, manifest: Mapping[str, object]) -> CanonicalBuildResult:
    summary = _mapping_for_check(manifest["summary"], "summary")
    partitions = _sequence_for_check(manifest["partitions"], "partitions")
    artifacts = _sequence_for_check(manifest["artifacts"], "artifacts")
    return CanonicalBuildResult(
        dataset_id=cast(str, manifest["dataset_id"]),
        output_directory=output,
        source_row_count=cast(int, summary["source_row_count"]),
        canonical_row_count=cast(int, summary["canonical_row_count"]),
        occurrence_count=cast(int, summary["occurrence_count"]),
        disposition_counts=cast(
            Mapping[str, int],
            _mapping_for_check(summary["disposition_counts"], "dispositions"),
        ),
        destination_counts=cast(
            Mapping[str, int],
            _mapping_for_check(summary["destination_counts"], "destinations"),
        ),
        partition_counts={
            cast(str, _mapping_for_check(item, "partition")["name"]): cast(
                int, _mapping_for_check(item, "partition")["row_count"]
            )
            for item in partitions
        },
        artifact_sha256={
            cast(str, _mapping_for_check(item, "artifact")["filename"]): cast(
                str, _mapping_for_check(item, "artifact")["physical_sha256"]
            )
            for item in artifacts
        },
    )


def _check_result(
    manifest: Mapping[str, object], artifact_hashes: Mapping[str, str]
) -> CanonicalCheckResult:
    summary = _mapping_for_check(manifest["summary"], "summary")
    partitions = _sequence_for_check(manifest["partitions"], "partitions")
    return CanonicalCheckResult(
        dataset_id=cast(str, manifest["dataset_id"]),
        source_row_count=cast(int, summary["source_row_count"]),
        canonical_row_count=cast(int, summary["canonical_row_count"]),
        occurrence_count=cast(int, summary["occurrence_count"]),
        partition_counts={
            cast(str, _mapping_for_check(item, "partition")["name"]): cast(
                int, _mapping_for_check(item, "partition")["row_count"]
            )
            for item in partitions
        },
        artifact_sha256=dict(artifact_hashes),
    )


def _parse_source_column(value: object) -> SourceColumnDisposition:
    item = _mapping(value, "source_column")
    _exact_keys(
        item,
        ("position", "name", "destination", "kind", "reason"),
        "source_column",
    )
    destination = item["destination"]
    if destination is not None and not isinstance(destination, str):
        raise CanonicalConfigurationError("Source destination is invalid.")
    kind = _text(item["kind"], "source_column.kind")
    if kind not in {"metadata", "feature", "target", "excluded"}:
        raise CanonicalConfigurationError("Source destination kind is invalid.")
    if (kind == "excluded") != (destination is None):
        raise CanonicalConfigurationError("Source destination is inconsistent.")
    return SourceColumnDisposition(
        position=_positive_integer(item["position"], "source_column.position"),
        name=_text(item["name"], "source_column.name"),
        destination=destination,
        kind=kind,
        reason=_text(item["reason"], "source_column.reason"),
    )


def _parse_feature(value: object) -> CanonicalFeature:
    item = _mapping(value, "feature")
    _exact_keys(
        item,
        (
            "name",
            "dtype",
            "unit",
            "nullable",
            "domain",
            "source_column",
            "availability",
        ),
        "feature",
    )
    return CanonicalFeature(
        name=_text(item["name"], "feature.name"),
        dtype=_text(item["dtype"], "feature.dtype"),
        unit=_text(item["unit"], "feature.unit"),
        nullable=_boolean(item["nullable"], "feature.nullable"),
        domain=_text(item["domain"], "feature.domain"),
        source_column=_text(item["source_column"], "feature.source_column"),
        availability=_text(item["availability"], "feature.availability"),
    )


def _parse_trusted_relation(value: object) -> TrustedUnitRelation:
    item = _mapping(value, "trusted_relation")
    _exact_keys(
        item,
        ("relation_id", "trusted_column", "excluded_column"),
        "trusted_relation",
    )
    return TrustedUnitRelation(
        relation_id=_text(item["relation_id"], "relation_id"),
        trusted_column=_text(item["trusted_column"], "trusted_column"),
        excluded_column=_text(item["excluded_column"], "excluded_column"),
    )


def _parse_dataset_field(value: object) -> DatasetField:
    item = _mapping(value, "dataset_field")
    _exact_keys(item, ("name", "dtype", "nullable"), "dataset_field")
    return DatasetField(
        name=_text(item["name"], "dataset_field.name"),
        dtype=_text(item["dtype"], "dataset_field.dtype"),
        nullable=_boolean(item["nullable"], "dataset_field.nullable"),
    )


def _validate_dataset_field_types(fields: Sequence[DatasetField]) -> None:
    if any(
        item.dtype not in {"int64", "float64", "string", "list[string]"}
        for item in fields
    ):
        raise CanonicalConfigurationError("Dataset field dtype is unsupported.")
    names = tuple(item.name for item in fields)
    if any(not name for name in names) or len(set(names)) != len(names):
        raise CanonicalConfigurationError("Dataset field name is invalid.")


def _validate_label_map(label_map: CanonicalLabelMap) -> None:
    _require_sha256(label_map.inventory_id, CanonicalLabelError)
    _require_sha256(label_map.source_sha256, CanonicalLabelError)
    if not label_map.entries:
        raise CanonicalLabelError("Approved label inventory is empty.")
    raw_labels = tuple(item.raw_label for item in label_map.entries)
    slugs = tuple(item.slug for item in label_map.entries)
    if (
        any(not item.strip() for item in raw_labels)
        or any(not item.strip() for item in slugs)
        or len(raw_labels) != len(set(raw_labels))
        or len(slugs) != len(set(slugs))
    ):
        raise CanonicalLabelError("Approved label mapping is not one-to-one.")


def _validate_source_fingerprint(fingerprint: BannerSourceFingerprint) -> None:
    if fingerprint.size_bytes < 0:
        raise CanonicalContractError("Source fingerprint size is invalid.")
    _require_sha256(fingerprint.sha256, CanonicalContractError)


def _fence_payload(
    fences: Mapping[str, tuple[Decimal, Decimal]],
) -> dict[str, object]:
    return {
        name: {"lower": _decimal_text(bounds[0]), "upper": _decimal_text(bounds[1])}
        for name, bounds in sorted(fences.items())
    }


def _complete_counts(
    counts: Mapping[str, int], enum_type: type[StrEnum]
) -> dict[str, int]:
    return {item.value: counts.get(item.value, 0) for item in enum_type}


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise CanonicalOutputError("Canonical decimal value is not finite.")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _decimal_text_value(
    value: object, error_type: type[CanonicalPipelineError]
) -> Decimal:
    if not isinstance(value, str) or not value:
        raise error_type("Canonical decimal text is invalid.")
    try:
        parsed = Decimal(value)
    except ArithmeticError:
        raise error_type("Canonical decimal text is invalid.") from None
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise error_type("Canonical decimal text is invalid.")
    return parsed


def _hash_regular_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise OSError("not a regular file")
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _json_compatible(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise CanonicalOutputError("Canonical JSON value is invalid.") from None


def _manifest_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                _json_compatible(value),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise CanonicalOutputError("Canonical manifest value is invalid.") from None


def _json_compatible(value: object) -> object:
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("non-finite")
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        raw = cast(Mapping[object, object], value)
        if any(not isinstance(key, str) for key in raw):
            raise TypeError("non-text key")
        return {cast(str, key): _json_compatible(item) for key, item in raw.items()}
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        return [_json_compatible(item) for item in cast(Iterable[object], value)]
    raise TypeError("unsupported JSON value")


def _logical_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        return [_logical_value(item) for item in cast(Iterable[object], value)]
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool | str | int | float):
        if isinstance(value, float) and not isfinite(value):
            raise CanonicalOutputError("Logical artifact contains a non-finite value.")
        return value
    raise CanonicalOutputError("Logical artifact contains an unsupported value.")


def _load_resource_json(name: str) -> dict[str, object]:
    try:
        raw = files(_PIPELINE_RESOURCE_PACKAGE).joinpath(name).read_bytes()
    except OSError:
        raise CanonicalConfigurationError("Pipeline resource is unavailable.") from None
    return _decode_json(raw, CanonicalConfigurationError)


def _decode_json(
    raw: bytes, error_type: type[CanonicalPipelineError]
) -> dict[str, object]:
    try:
        value: object = json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, CanonicalConfigurationError):
        raise error_type("Canonical JSON document is invalid.") from None
    if not isinstance(value, dict):
        raise error_type("Canonical JSON document is invalid.")
    return cast(dict[str, object], value)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalConfigurationError("Canonical JSON has duplicate keys.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise CanonicalConfigurationError("Canonical JSON contains a non-finite number.")


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CanonicalConfigurationError(f"Pipeline {context} must be an object.")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise CanonicalConfigurationError(f"Pipeline {context} has invalid keys.")
    return dict(cast(Mapping[str, object], raw))


def _mapping_for_check(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CanonicalCheckError(f"Canonical {context} must be an object.")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise CanonicalCheckError(f"Canonical {context} has invalid keys.")
    return dict(cast(Mapping[str, object], raw))


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise CanonicalConfigurationError("Pipeline value must be an array.")
    return cast(list[object], value)


def _sequence_for_check(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CanonicalCheckError(f"Canonical {context} must be an array.")
    return cast(list[object], value)


def _exact_keys(
    value: Mapping[str, object], expected: Sequence[str], context: str
) -> None:
    if set(value) != set(expected) or len(value) != len(expected):
        raise CanonicalConfigurationError(f"Pipeline {context} fields are invalid.")


def _exact_keys_for_check(value: Mapping[str, object], expected: Sequence[str]) -> None:
    if set(value) != set(expected) or len(value) != len(expected):
        raise CanonicalCheckError("Canonical manifest fields are invalid.")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalConfigurationError(f"Pipeline {context} must be text.")
    return value


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise CanonicalConfigurationError(f"Pipeline {context} must be an integer.")
    return value


def _positive_integer(value: object, context: str) -> int:
    parsed = _integer(value, context)
    if parsed <= 0:
        raise CanonicalConfigurationError(f"Pipeline {context} must be positive.")
    return parsed


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise CanonicalConfigurationError(f"Pipeline {context} must be boolean.")
    return value


def _decimal_number(value: object, context: str) -> Decimal:
    if type(value) not in {int, float}:
        raise CanonicalConfigurationError(f"Pipeline {context} must be numeric.")
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise CanonicalConfigurationError(f"Pipeline {context} must be finite.")
    return parsed


def _positive_decimal(value: object, context: str) -> Decimal:
    parsed = _decimal_number(value, context)
    if parsed <= 0:
        raise CanonicalConfigurationError(f"Pipeline {context} must be positive.")
    return parsed


def _probability(value: object, context: str) -> Decimal:
    parsed = _decimal_number(value, context)
    if not Decimal(0) <= parsed <= Decimal(1):
        raise CanonicalConfigurationError(f"Pipeline {context} is invalid.")
    return parsed


def _enum_value[EnumValue: StrEnum](
    enum_type: type[EnumValue], value: object, context: str
) -> EnumValue:
    try:
        return enum_type(_text(value, context))
    except ValueError:
        raise CanonicalConfigurationError(
            f"Pipeline {context} is unsupported."
        ) from None


def _require_sha256(value: object, error_type: type[CanonicalPipelineError]) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise error_type("SHA-256 identifier is invalid.")
    return value
