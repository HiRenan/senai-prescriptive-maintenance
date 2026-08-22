"""Deterministic textual normalization and audited fault-label inventory."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Final, NoReturn, cast, overload
from warnings import catch_warnings, simplefilter

import pandas as pd

from prescriptive_maintenance.data.baseline import (
    BannerBaselineError,
    validate_banner_baseline_artifacts,
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

FAULT_LABEL_INVENTORY_SCHEMA_VERSION: Final = 1
FAULT_LABEL_NORMALIZATION_VERSION: Final = 1
FAULT_LABEL_UNICODE_VERSION: Final = "15.1.0"
FAULT_LABEL_CATEGORICAL_SCOPE: Final = "approved_categorical_only"
FAULT_LABEL_INVENTORY_FILENAME: Final = "fault-labels.v1.json"

FAULT_LABEL_NORMALIZATION_STEPS: Final[tuple[str, ...]] = (
    "unicode_nfkc",
    "trim",
    "collapse_whitespace",
    "casefold",
    "normalize_separators",
    "stable_slug",
)

# Unicode White_Space code points that are not control characters. Controls are
# rejected before normalization instead of being silently collapsed.
FAULT_LABEL_WHITESPACE_CODE_POINTS: Final[tuple[int, ...]] = (
    0x0020,
    0x00A0,
    0x1680,
    *range(0x2000, 0x200B),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)
FAULT_LABEL_SEPARATOR_CODE_POINTS: Final[tuple[int, ...]] = (
    0x002D,  # hyphen-minus
    0x002F,  # solidus
    0x005C,  # reverse solidus
    0x005F,  # low line
)

_BANNER_BASENAME: Final = "banner.csv"
_SUPPORTED_MANIFEST_SCHEMA: Final = 1
_SUPPORTED_HASH_ALGORITHM: Final = "sha256"
_ROUND_COUNT: Final = 2
_STAGING_PREFIX: Final = ".fault-label-inventory-"
_SLUG_SCHEME: Final = "utf8-percent-v1"
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_WHITESPACE: Final = frozenset(
    chr(value) for value in FAULT_LABEL_WHITESPACE_CODE_POINTS
)
_SEPARATORS: Final = frozenset(
    chr(value) for value in FAULT_LABEL_SEPARATOR_CODE_POINTS
)
_APPROVED_FIELDS: Final[tuple[str, ...]] = (
    "raw_label",
    "frequency",
    "normalized_label",
    "slug",
    "collision_status",
    "collision_resolution",
)


class FaultLabelError(Exception):
    """Base class for sanitized fault-label failures."""


class FaultLabelNormalizationError(FaultLabelError):
    """Base class for invalid normalization inputs."""


class NullFaultLabelError(FaultLabelNormalizationError):
    """Raised when a raw label is null."""


class InvalidFaultLabelTypeError(FaultLabelNormalizationError):
    """Raised when a raw label is not text."""


class EmptyFaultLabelError(FaultLabelNormalizationError):
    """Raised when normalization would produce an empty label."""


class ControlCharacterFaultLabelError(FaultLabelNormalizationError):
    """Raised when a raw label contains a Unicode control character."""


class FormatCharacterFaultLabelError(FaultLabelNormalizationError):
    """Raised for bidi, zero-width, and other Unicode format characters."""


class SurrogateFaultLabelError(FaultLabelNormalizationError):
    """Raised when a raw label contains an unpaired surrogate."""


class InvalidUnicodeFaultLabelError(FaultLabelNormalizationError):
    """Raised when text cannot be represented as valid UTF-8 Unicode."""


class UnsupportedUnicodeVersionError(FaultLabelNormalizationError):
    """Raised when the runtime Unicode database differs from the frozen one."""


class UnknownFaultLabelError(FaultLabelError):
    """Raised when exact raw-label lookup fails closed."""


class FaultLabelInventoryError(FaultLabelError):
    """Base class for sanitized inventory failures."""


class FaultLabelInventoryJsonError(FaultLabelInventoryError):
    """Raised when inventory JSON is invalid or non-canonical."""


class FaultLabelInventorySchemaError(FaultLabelInventoryError):
    """Raised when an inventory violates the versioned public schema."""


class FaultLabelInventoryIntegrityError(FaultLabelInventoryError):
    """Raised when inventory identity or integrity evidence is invalid."""


class FaultLabelInventoryCollisionError(FaultLabelInventoryError):
    """Raised when inventory collision state is invalid."""


class FaultLabelInventoryBaselineError(FaultLabelInventoryError):
    """Raised when the public baseline cannot anchor an inventory."""


class CollisionStatus(StrEnum):
    """Audit status stored for every raw-label mapping."""

    CLEAR = "clear"
    RESOLVED = "resolved"


class CollisionResolution(StrEnum):
    """Explicit resolution stored for every raw-label mapping."""

    NOT_REQUIRED = "not_required"
    APPROVED_TEXTUAL_EQUIVALENCE = "approved_textual_equivalence"


class FaultLabelInventoryStatus(StrEnum):
    """Terminal state of an audited inventory run."""

    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class NormalizedFaultLabel:
    """Textual normalized form and its reversible stable slug."""

    normalized_label: str
    slug: str


@dataclass(frozen=True, slots=True)
class FaultLabelInventoryEntry:
    """One approved categorical mapping and its global frequency."""

    raw_label: str
    frequency: int
    normalized_label: str
    slug: str
    collision_status: CollisionStatus
    collision_resolution: CollisionResolution


@dataclass(frozen=True, slots=True)
class FaultLabelCollisionGroup:
    """Read-only categorical evidence for one normalization collision."""

    group_id: str
    normalization_version: int
    normalized_label: str
    raw_labels: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class FaultLabelCollisionSummary:
    """Sanitized counts and fingerprints for collision decision gates."""

    normalized_label_group_count: int
    normalized_label_raw_count: int
    slug_group_count: int
    slug_normalized_label_count: int
    fingerprint: str
    normalized_label_group_ids: tuple[str, ...] = ()
    slug_group_ids: tuple[str, ...] = ()
    normalized_label_groups: tuple[FaultLabelCollisionGroup, ...] = field(
        default=(), repr=False
    )


@dataclass(frozen=True, slots=True)
class FaultLabelInventoryRoundReceipt:
    """Safe pre/post integrity evidence for one independent round."""

    round_number: int
    pre_fingerprint: BannerSourceFingerprint
    post_fingerprint: BannerSourceFingerprint


@dataclass(frozen=True, slots=True)
class FaultLabelInventory:
    """Validated offline inventory suitable for fail-closed lookup."""

    inventory_id: str
    source_fingerprint: BannerSourceFingerprint
    row_count: int
    collision_summary: FaultLabelCollisionSummary
    entries: tuple[FaultLabelInventoryEntry, ...]


@dataclass(frozen=True, slots=True)
class FaultLabelInventoryRunResult:
    """Sanitized result of two audited source rounds."""

    status: FaultLabelInventoryStatus
    failure_codes: tuple[str, ...]
    collision_summary: FaultLabelCollisionSummary
    round_receipts: tuple[FaultLabelInventoryRoundReceipt, ...]
    output_path: Path | None
    json_bytes: bytes | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    basename: str
    fingerprint: BannerSourceFingerprint


@dataclass(frozen=True, slots=True)
class _BaselineExpectations:
    schema_version: int
    source_sha256: str
    row_count: int
    raw_label_count: int


@dataclass(frozen=True, slots=True)
class _CoreLabel:
    raw_label: str
    frequency: int
    normalized_label: str
    slug: str


@dataclass(frozen=True, slots=True)
class _RoundSnapshot:
    row_count: int
    labels: tuple[_CoreLabel, ...] = field(repr=False)
    normalized_colliding_values: frozenset[str] = field(repr=False)
    collision_summary: FaultLabelCollisionSummary
    canonical_bytes: bytes = field(repr=False)


class _RoundFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@overload
def normalize_fault_label(raw_label: str) -> NormalizedFaultLabel: ...


@overload
def normalize_fault_label(raw_label: None) -> NormalizedFaultLabel: ...


def normalize_fault_label(raw_label: object) -> NormalizedFaultLabel:
    """Apply the frozen textual pipeline without semantic interpretation."""

    _validate_runtime_unicode_version()
    if raw_label is None:
        raise NullFaultLabelError("Raw fault label must not be null.")
    if not isinstance(raw_label, str):
        raise InvalidFaultLabelTypeError("Raw fault label must be text.")

    _validate_unicode_text(raw_label)
    try:
        normalized = unicodedata.normalize("NFKC", raw_label)
    except UnicodeError:
        raise InvalidUnicodeFaultLabelError(
            "Raw fault label is not valid Unicode text."
        ) from None
    _validate_unicode_text(normalized)

    normalized = _trim_frozen_whitespace(normalized)
    normalized = _collapse_frozen_whitespace(normalized)
    normalized = normalized.casefold()
    normalized = _normalize_allowed_separators(normalized)
    if not normalized:
        raise EmptyFaultLabelError("Raw fault label normalizes to empty text.")

    return NormalizedFaultLabel(
        normalized_label=normalized,
        slug=_stable_slug(normalized),
    )


def run_fault_label_inventory(
    *,
    input_path: Path,
    manifest_path: Path,
    baseline_json_path: Path,
    baseline_markdown_path: Path,
    output_root: Path,
    approved_normalized_collisions: Set[str] | None = None,
) -> FaultLabelInventoryRunResult:
    """Run two independent audited rounds and atomically publish one JSON."""

    empty_summary = _collision_summary((), (), ())
    try:
        identity = _load_source_identity(manifest_path)
    except FaultLabelInventoryError:
        return _blocked("inventory.manifest_invalid", empty_summary)
    try:
        baseline = _load_validated_baseline_expectations(
            json_path=baseline_json_path,
            markdown_path=baseline_markdown_path,
            manifest_path=manifest_path,
        )
    except FaultLabelInventoryError:
        return _blocked("inventory.baseline_invalid", empty_summary)
    if baseline.source_sha256 != identity.fingerprint.sha256:
        return _blocked("inventory.baseline_source_mismatch", empty_summary)

    receipts: list[BannerSourceReceipt[_RoundSnapshot]] = []
    for _round_number in range(1, _ROUND_COUNT + 1):
        try:
            receipt = consume_banner_source_audited(
                input_path=input_path,
                manifest_path=manifest_path,
                consumer=_analyze_fault_descriptor,
            )
        except BannerSourceError as error:
            return _blocked(_source_failure_code(error), empty_summary)
        except _RoundFailure as error:
            return _blocked(error.code, empty_summary)
        except Exception:
            return _blocked("inventory.runner_failed", empty_summary)
        receipts.append(receipt)

    round_receipts = _public_round_receipts(receipts)
    first = receipts[0].result
    second = receipts[1].result
    collision_summary = first.collision_summary
    if not _receipts_match_identity(identity, receipts):
        return _blocked(
            "inventory.integrity_receipt_mismatch",
            collision_summary,
            round_receipts,
        )
    if first.canonical_bytes != second.canonical_bytes:
        return _blocked(
            "inventory.round_byte_mismatch",
            collision_summary,
            round_receipts,
        )
    if first.row_count != baseline.row_count:
        return _blocked(
            "inventory.row_count_mismatch",
            collision_summary,
            round_receipts,
        )
    if len(first.labels) != baseline.raw_label_count:
        return _blocked(
            "inventory.raw_label_count_mismatch",
            collision_summary,
            round_receipts,
        )
    if collision_summary.slug_group_count:
        return _blocked(
            "inventory.slug_collision",
            collision_summary,
            round_receipts,
        )

    try:
        approvals = _validated_collision_approvals(
            approved_normalized_collisions,
            collision_summary.normalized_label_group_ids,
        )
    except FaultLabelInventoryCollisionError:
        return _blocked(
            "inventory.collision_approval_invalid",
            collision_summary,
            round_receipts,
        )
    if approvals != frozenset(collision_summary.normalized_label_group_ids):
        return _blocked(
            "inventory.normalized_collision_unresolved",
            collision_summary,
            round_receipts,
        )

    try:
        first_bytes = _build_inventory_bytes(
            snapshot=first,
            identity=identity,
            baseline=baseline,
            receipts=round_receipts,
            approved_normalized_collisions=approvals,
        )
        second_bytes = _build_inventory_bytes(
            snapshot=second,
            identity=identity,
            baseline=baseline,
            receipts=round_receipts,
            approved_normalized_collisions=approvals,
        )
    except FaultLabelInventoryError:
        return _blocked(
            "inventory.serialization_failed",
            collision_summary,
            round_receipts,
        )
    if first_bytes != second_bytes:
        return _blocked(
            "inventory.final_byte_mismatch",
            collision_summary,
            round_receipts,
        )

    try:
        output_path = _write_inventory_atomically(
            output_root=output_root,
            source_sha256=identity.fingerprint.sha256,
            json_bytes=first_bytes,
        )
    except FaultLabelInventoryError:
        return FaultLabelInventoryRunResult(
            status=FaultLabelInventoryStatus.BLOCKED,
            failure_codes=("inventory.output_write_failed",),
            collision_summary=collision_summary,
            round_receipts=round_receipts,
            output_path=None,
            json_bytes=first_bytes,
        )

    return FaultLabelInventoryRunResult(
        status=FaultLabelInventoryStatus.PASSED,
        failure_codes=(),
        collision_summary=collision_summary,
        round_receipts=round_receipts,
        output_path=output_path,
        json_bytes=first_bytes,
    )


def load_fault_label_inventory(
    *,
    inventory_path: Path,
    manifest_path: Path,
    baseline_json_path: Path,
    baseline_markdown_path: Path,
) -> FaultLabelInventory:
    """Load and validate an inventory offline without accessing ``banner.csv``."""

    identity = _load_source_identity(manifest_path)
    baseline = _load_validated_baseline_expectations(
        json_path=baseline_json_path,
        markdown_path=baseline_markdown_path,
        manifest_path=manifest_path,
    )
    if baseline.source_sha256 != identity.fingerprint.sha256:
        raise FaultLabelInventoryBaselineError(
            "Public baseline source identity is invalid."
        )
    _validate_inventory_path(inventory_path, identity.fingerprint.sha256)
    try:
        json_bytes = inventory_path.read_bytes()
    except OSError:
        raise FaultLabelInventoryJsonError(
            "Public fault-label inventory is unavailable."
        ) from None
    payload = _load_strict_json_object(json_bytes, canonical=True)
    return _validate_inventory_payload(
        payload,
        identity=identity,
        baseline=baseline,
    )


def validate_fault_label_inventory(
    *,
    inventory_path: Path,
    manifest_path: Path,
    baseline_json_path: Path,
    baseline_markdown_path: Path,
) -> None:
    """Validate the tracked categorical artifact entirely offline."""

    load_fault_label_inventory(
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_json_path,
        baseline_markdown_path=baseline_markdown_path,
    )


def resolve_known_fault_label(
    raw_label: str | None,
    inventory: FaultLabelInventory,
) -> FaultLabelInventoryEntry:
    """Resolve an exact observed raw label and fail closed for every unknown."""

    normalize_fault_label(raw_label)
    for entry in inventory.entries:
        if entry.raw_label == raw_label:
            return entry
    raise UnknownFaultLabelError("Raw fault label is not present in the inventory.")


def _validate_runtime_unicode_version() -> None:
    if unicodedata.unidata_version != FAULT_LABEL_UNICODE_VERSION:
        raise UnsupportedUnicodeVersionError(
            "Runtime Unicode database does not match the normalization contract."
        )


def _validate_unicode_text(value: str) -> None:
    for character in value:
        code_point = ord(character)
        category = unicodedata.category(character)
        if category == "Cs":
            raise SurrogateFaultLabelError(
                "Raw fault label contains an invalid surrogate."
            )
        if category == "Cc":
            raise ControlCharacterFaultLabelError(
                "Raw fault label contains a control character."
            )
        if category == "Cf":
            raise FormatCharacterFaultLabelError(
                "Raw fault label contains a format character."
            )
        if 0xFDD0 <= code_point <= 0xFDEF or code_point & 0xFFFF in {
            0xFFFE,
            0xFFFF,
        }:
            raise InvalidUnicodeFaultLabelError(
                "Raw fault label contains a Unicode noncharacter."
            )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise InvalidUnicodeFaultLabelError(
            "Raw fault label is not valid UTF-8 text."
        ) from None


def _trim_frozen_whitespace(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and value[start] in _WHITESPACE:
        start += 1
    while end > start and value[end - 1] in _WHITESPACE:
        end -= 1
    return value[start:end]


def _collapse_frozen_whitespace(value: str) -> str:
    result: list[str] = []
    pending_space = False
    for character in value:
        if character in _WHITESPACE:
            pending_space = bool(result)
            continue
        if pending_space:
            result.append(" ")
            pending_space = False
        result.append(character)
    return "".join(result)


def _normalize_allowed_separators(value: str) -> str:
    separated = "".join(
        " " if character in _SEPARATORS else character for character in value
    )
    return _collapse_frozen_whitespace(_trim_frozen_whitespace(separated))


def _stable_slug(normalized_label: str) -> str:
    parts: list[str] = []
    for character in normalized_label:
        if character == " ":
            parts.append("-")
        elif "a" <= character <= "z" or "0" <= character <= "9":
            parts.append(character)
        else:
            parts.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
    return "".join(parts)


def _analyze_fault_descriptor(source: BinaryIO) -> _RoundSnapshot:
    if source.writable():
        raise _RoundFailure("inventory.source_descriptor_writable")
    try:
        row_count, frequencies = _parse_fault_frequencies(source)
        labels: list[_CoreLabel] = []
        for raw_label in sorted(frequencies):
            first = normalize_fault_label(raw_label)
            second = normalize_fault_label(raw_label)
            if first != second:
                raise _RoundFailure("inventory.normalization_nondeterministic")
            labels.append(
                _CoreLabel(
                    raw_label=raw_label,
                    frequency=frequencies[raw_label],
                    normalized_label=first.normalized_label,
                    slug=first.slug,
                )
            )
    except _RoundFailure:
        raise
    except FaultLabelNormalizationError:
        raise _RoundFailure("inventory.invalid_raw_label") from None
    except Exception:
        raise _RoundFailure("inventory.parsing_failed") from None

    label_tuple = tuple(labels)
    normalized_groups, slug_groups = _collision_groups(label_tuple)
    summary = _collision_summary_from_groups(normalized_groups, slug_groups)
    snapshot_payload: dict[str, object] = {
        "row_count": row_count,
        "labels": [
            {
                "raw_label": label.raw_label,
                "frequency": label.frequency,
                "normalized_label": label.normalized_label,
                "slug": label.slug,
            }
            for label in label_tuple
        ],
        "collision_fingerprint": summary.fingerprint,
    }
    return _RoundSnapshot(
        row_count=row_count,
        labels=label_tuple,
        normalized_colliding_values=frozenset(normalized_groups),
        collision_summary=summary,
        canonical_bytes=_canonical_json_bytes(snapshot_payload),
    )


def _parse_fault_frequencies(source: BinaryIO) -> tuple[int, Counter[str]]:
    with catch_warnings():
        simplefilter("error", pd.errors.ParserWarning)
        simplefilter("error", pd.errors.DtypeWarning)
        parsed = pd.read_csv(
            source,
            sep=",",
            header=0,
            names=None,
            index_col=False,
            usecols=("fault",),
            dtype={"fault": "string"},
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
    if tuple(parsed.columns) != ("fault",):
        raise FaultLabelInventorySchemaError("Fault-only CSV projection is invalid.")

    row_count = len(parsed)
    frequencies: Counter[str] = Counter()
    try:
        for value in parsed["fault"].array:
            if value is None or value is pd.NA:
                raise NullFaultLabelError("Raw fault label must not be null.")
            if not isinstance(value, str):
                raise InvalidFaultLabelTypeError("Raw fault label must be text.")
            frequencies[value] += 1
    finally:
        del parsed
    return row_count, frequencies


def _collision_groups(
    labels: tuple[_CoreLabel, ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    raw_by_normalized: defaultdict[str, list[str]] = defaultdict(list)
    normalized_by_slug: defaultdict[str, set[str]] = defaultdict(set)
    for label in labels:
        raw_by_normalized[label.normalized_label].append(label.raw_label)
        normalized_by_slug[label.slug].add(label.normalized_label)
    normalized_groups = {
        normalized: tuple(sorted(raw_labels))
        for normalized, raw_labels in raw_by_normalized.items()
        if len(raw_labels) > 1
    }
    slug_groups = {
        slug: tuple(sorted(normalized_labels))
        for slug, normalized_labels in normalized_by_slug.items()
        if len(normalized_labels) > 1
    }
    return normalized_groups, slug_groups


def _collision_summary_from_groups(
    normalized_groups: Mapping[str, tuple[str, ...]],
    slug_groups: Mapping[str, tuple[str, ...]],
) -> FaultLabelCollisionSummary:
    public_normalized_groups = tuple(
        FaultLabelCollisionGroup(
            group_id=_collision_group_id(
                kind="normalized_label",
                target=normalized_label,
                members=raw_labels,
            ),
            normalization_version=FAULT_LABEL_NORMALIZATION_VERSION,
            normalized_label=normalized_label,
            raw_labels=raw_labels,
        )
        for normalized_label, raw_labels in sorted(normalized_groups.items())
    )
    slug_ids = tuple(
        sorted(
            _collision_group_id(
                kind="slug",
                target=slug,
                members=normalized_labels,
            )
            for slug, normalized_labels in slug_groups.items()
        )
    )
    return _collision_summary(
        public_normalized_groups,
        slug_ids,
        tuple(len(values) for values in slug_groups.values()),
    )


def _collision_summary(
    normalized_groups: tuple[FaultLabelCollisionGroup, ...],
    slug_ids: tuple[str, ...],
    slug_sizes: tuple[int, ...],
) -> FaultLabelCollisionSummary:
    normalized_ids = tuple(sorted(group.group_id for group in normalized_groups))
    fingerprint_payload: dict[str, object] = {
        "normalized_label_group_ids": list(normalized_ids),
        "slug_group_ids": list(slug_ids),
    }
    return FaultLabelCollisionSummary(
        normalized_label_group_count=len(normalized_ids),
        normalized_label_raw_count=sum(
            len(group.raw_labels) for group in normalized_groups
        ),
        slug_group_count=len(slug_ids),
        slug_normalized_label_count=sum(slug_sizes),
        fingerprint=sha256(_canonical_json_bytes(fingerprint_payload)).hexdigest(),
        normalized_label_group_ids=normalized_ids,
        slug_group_ids=slug_ids,
        normalized_label_groups=normalized_groups,
    )


def _collision_group_id(*, kind: str, target: str, members: tuple[str, ...]) -> str:
    return sha256(
        _canonical_json_bytes(
            {
                "normalization_version": FAULT_LABEL_NORMALIZATION_VERSION,
                "kind": kind,
                "target": target,
                "members": list(members),
            }
        )
    ).hexdigest()


def _validated_collision_approvals(
    approvals: object,
    actual_group_ids: tuple[str, ...],
) -> frozenset[str]:
    if approvals is None:
        raw_approvals: Set[object] = frozenset()
    elif not isinstance(approvals, Set):
        raise FaultLabelInventoryCollisionError(
            "Collision approvals must be an unordered set."
        )
    else:
        raw_approvals = cast(Set[object], approvals)
    if any(
        not isinstance(item, str) or _SHA256_PATTERN.fullmatch(item) is None
        for item in raw_approvals
    ):
        raise FaultLabelInventoryCollisionError(
            "Collision approval contains an invalid fingerprint."
        )
    approved = frozenset(cast(str, item) for item in raw_approvals)
    if not approved.issubset(actual_group_ids):
        raise FaultLabelInventoryCollisionError(
            "Collision approval does not match this inventory."
        )
    return approved


def _build_inventory_bytes(
    *,
    snapshot: _RoundSnapshot,
    identity: _SourceIdentity,
    baseline: _BaselineExpectations,
    receipts: tuple[FaultLabelInventoryRoundReceipt, ...],
    approved_normalized_collisions: frozenset[str],
) -> bytes:
    body = _inventory_body(
        snapshot=snapshot,
        identity=identity,
        baseline=baseline,
        receipts=receipts,
        approved_normalized_collisions=approved_normalized_collisions,
    )
    inventory_id = sha256(_canonical_json_bytes(body)).hexdigest()
    payload = {**body, "inventory_id": inventory_id}
    _validate_inventory_payload(payload, identity=identity, baseline=baseline)
    return _canonical_json_bytes(payload)


def _inventory_body(
    *,
    snapshot: _RoundSnapshot,
    identity: _SourceIdentity,
    baseline: _BaselineExpectations,
    receipts: tuple[FaultLabelInventoryRoundReceipt, ...],
    approved_normalized_collisions: frozenset[str],
) -> dict[str, object]:
    entries = [
        {
            "raw_label": label.raw_label,
            "frequency": label.frequency,
            "normalized_label": label.normalized_label,
            "slug": label.slug,
            "collision_status": (
                CollisionStatus.RESOLVED.value
                if label.normalized_label in snapshot.normalized_colliding_values
                else CollisionStatus.CLEAR.value
            ),
            "collision_resolution": (
                CollisionResolution.APPROVED_TEXTUAL_EQUIVALENCE.value
                if label.normalized_label in snapshot.normalized_colliding_values
                else CollisionResolution.NOT_REQUIRED.value
            ),
        }
        for label in snapshot.labels
    ]
    return {
        "inventory_schema_version": FAULT_LABEL_INVENTORY_SCHEMA_VERSION,
        "versions": {
            "inventory_schema": FAULT_LABEL_INVENTORY_SCHEMA_VERSION,
            "normalization": FAULT_LABEL_NORMALIZATION_VERSION,
            "unicode": FAULT_LABEL_UNICODE_VERSION,
            "baseline_schema": baseline.schema_version,
        },
        "scope": {
            "classification": FAULT_LABEL_CATEGORICAL_SCOPE,
            "fields": list(_APPROVED_FIELDS),
            "row_level_data": False,
        },
        "pipeline": _pipeline_payload(),
        "source": {
            "basename": identity.basename,
            "size_bytes": identity.fingerprint.size_bytes,
            "source_sha256": identity.fingerprint.sha256,
        },
        "integrity": {
            "round_count": _ROUND_COUNT,
            "rounds": [_round_receipt_payload(receipt) for receipt in receipts],
        },
        "expectations": {
            "row_count": baseline.row_count,
            "raw_label_count": baseline.raw_label_count,
        },
        "reconciliations": _reconciliation_payload(entries, baseline),
        "collisions": _collision_payload(
            snapshot.collision_summary,
            approved_normalized_collisions=approved_normalized_collisions,
        ),
        "labels": entries,
    }


def _pipeline_payload() -> dict[str, object]:
    return {
        "normalization_version": FAULT_LABEL_NORMALIZATION_VERSION,
        "unicode_normalization": "NFKC",
        "unicode_data_version": FAULT_LABEL_UNICODE_VERSION,
        "steps": list(FAULT_LABEL_NORMALIZATION_STEPS),
        "whitespace_code_points": [
            f"U+{value:04X}" for value in FAULT_LABEL_WHITESPACE_CODE_POINTS
        ],
        "separator_code_points": [
            f"U+{value:04X}" for value in FAULT_LABEL_SEPARATOR_CODE_POINTS
        ],
        "separator_replacement": "U+0020",
        "slug_scheme": _SLUG_SCHEME,
    }


def _round_receipt_payload(
    receipt: FaultLabelInventoryRoundReceipt,
) -> dict[str, object]:
    return {
        "round": receipt.round_number,
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


def _collision_payload(
    summary: FaultLabelCollisionSummary,
    *,
    approved_normalized_collisions: frozenset[str],
) -> dict[str, object]:
    if approved_normalized_collisions != frozenset(summary.normalized_label_group_ids):
        raise FaultLabelInventoryCollisionError(
            "Approved collision decisions do not match the observed groups."
        )
    approved_decisions = [
        {
            "group_id": group.group_id,
            "normalization_version": group.normalization_version,
            "normalized_label": group.normalized_label,
            "raw_labels": list(group.raw_labels),
            "resolution": CollisionResolution.APPROVED_TEXTUAL_EQUIVALENCE.value,
        }
        for group in summary.normalized_label_groups
    ]
    return {
        "normalized_label_group_count": summary.normalized_label_group_count,
        "normalized_label_raw_count": summary.normalized_label_raw_count,
        "slug_group_count": summary.slug_group_count,
        "slug_normalized_label_count": summary.slug_normalized_label_count,
        "status": (
            CollisionStatus.RESOLVED.value
            if summary.normalized_label_group_count
            else CollisionStatus.CLEAR.value
        ),
        "fingerprint": summary.fingerprint,
        "approved_normalized_label_decisions": approved_decisions,
    }


def _reconciliation_payload(
    entries: Sequence[Mapping[str, object]], baseline: _BaselineExpectations
) -> list[dict[str, object]]:
    frequency_total = sum(cast(int, entry["frequency"]) for entry in entries)
    return [
        {
            "code": "frequency.total",
            "expected": baseline.row_count,
            "actual": frequency_total,
            "passed": frequency_total == baseline.row_count,
        },
        {
            "code": "raw_label.cardinality",
            "expected": baseline.raw_label_count,
            "actual": len(entries),
            "passed": len(entries) == baseline.raw_label_count,
        },
        {
            "code": "frequency.positive",
            "expected": len(entries),
            "actual": sum(cast(int, entry["frequency"]) > 0 for entry in entries),
            "passed": all(cast(int, entry["frequency"]) > 0 for entry in entries),
        },
    ]


def _validate_inventory_payload(
    payload: Mapping[str, object],
    *,
    identity: _SourceIdentity,
    baseline: _BaselineExpectations,
) -> FaultLabelInventory:
    _require_keys(
        payload,
        (
            "inventory_schema_version",
            "versions",
            "scope",
            "pipeline",
            "source",
            "integrity",
            "expectations",
            "reconciliations",
            "collisions",
            "labels",
            "inventory_id",
        ),
    )
    if (
        _required_int(payload, "inventory_schema_version")
        != FAULT_LABEL_INVENTORY_SCHEMA_VERSION
    ):
        raise FaultLabelInventorySchemaError("Inventory schema version is invalid.")

    versions = _required_mapping(payload, "versions")
    expected_versions: dict[str, object] = {
        "inventory_schema": FAULT_LABEL_INVENTORY_SCHEMA_VERSION,
        "normalization": FAULT_LABEL_NORMALIZATION_VERSION,
        "unicode": FAULT_LABEL_UNICODE_VERSION,
        "baseline_schema": baseline.schema_version,
    }
    if not _matches_exact_json_value(versions, expected_versions):
        raise FaultLabelInventorySchemaError("Inventory versions are invalid.")
    scope = _required_mapping(payload, "scope")
    expected_scope: dict[str, object] = {
        "classification": FAULT_LABEL_CATEGORICAL_SCOPE,
        "fields": list(_APPROVED_FIELDS),
        "row_level_data": False,
    }
    if not _matches_exact_json_value(scope, expected_scope):
        raise FaultLabelInventorySchemaError("Inventory categorical scope is invalid.")
    if not _matches_exact_json_value(
        _required_mapping(payload, "pipeline"), _pipeline_payload()
    ):
        raise FaultLabelInventorySchemaError(
            "Inventory normalization pipeline is invalid."
        )

    source = _required_mapping(payload, "source")
    _require_keys(source, ("basename", "size_bytes", "source_sha256"))
    if (
        _required_string(source, "basename") != identity.basename
        or _required_int(source, "size_bytes") != identity.fingerprint.size_bytes
        or _required_string(source, "source_sha256") != identity.fingerprint.sha256
        or baseline.source_sha256 != identity.fingerprint.sha256
    ):
        raise FaultLabelInventoryIntegrityError("Inventory source identity is invalid.")

    _validate_integrity_payload(
        _required_mapping(payload, "integrity"), identity.fingerprint
    )
    expectations = _required_mapping(payload, "expectations")
    expected_expectations: dict[str, object] = {
        "row_count": baseline.row_count,
        "raw_label_count": baseline.raw_label_count,
    }
    if not _matches_exact_json_value(expectations, expected_expectations):
        raise FaultLabelInventoryBaselineError("Inventory expectations are invalid.")

    raw_entries = _required_sequence(payload, "labels")
    entries = tuple(_validate_entry(item) for item in raw_entries)
    if len(entries) != baseline.raw_label_count:
        raise FaultLabelInventoryBaselineError("Inventory cardinality is invalid.")
    if tuple(entry.raw_label for entry in entries) != tuple(
        sorted(entry.raw_label for entry in entries)
    ) or len({entry.raw_label for entry in entries}) != len(entries):
        raise FaultLabelInventorySchemaError("Inventory raw-label order is invalid.")

    core_labels = tuple(
        _CoreLabel(
            raw_label=entry.raw_label,
            frequency=entry.frequency,
            normalized_label=entry.normalized_label,
            slug=entry.slug,
        )
        for entry in entries
    )
    normalized_groups, slug_groups = _collision_groups(core_labels)
    if slug_groups:
        raise FaultLabelInventoryCollisionError("Inventory contains a slug collision.")
    summary = _collision_summary_from_groups(normalized_groups, slug_groups)
    if not _matches_exact_json_value(
        _required_mapping(payload, "collisions"),
        _collision_payload(
            summary,
            approved_normalized_collisions=frozenset(
                summary.normalized_label_group_ids
            ),
        ),
    ):
        raise FaultLabelInventoryCollisionError(
            "Inventory collision summary is invalid."
        )
    for entry in entries:
        collision = entry.normalized_label in normalized_groups
        expected_status = (
            CollisionStatus.RESOLVED if collision else CollisionStatus.CLEAR
        )
        expected_resolution = (
            CollisionResolution.APPROVED_TEXTUAL_EQUIVALENCE
            if collision
            else CollisionResolution.NOT_REQUIRED
        )
        if (
            entry.collision_status is not expected_status
            or entry.collision_resolution is not expected_resolution
        ):
            raise FaultLabelInventoryCollisionError(
                "Inventory collision resolution is invalid."
            )

    generated_entries = [
        {
            "raw_label": entry.raw_label,
            "frequency": entry.frequency,
            "normalized_label": entry.normalized_label,
            "slug": entry.slug,
            "collision_status": entry.collision_status.value,
            "collision_resolution": entry.collision_resolution.value,
        }
        for entry in entries
    ]
    expected_reconciliations = _reconciliation_payload(generated_entries, baseline)
    if not _matches_exact_json_value(
        _required_sequence(payload, "reconciliations"), expected_reconciliations
    ) or any(not cast(bool, item["passed"]) for item in expected_reconciliations):
        raise FaultLabelInventoryBaselineError("Inventory reconciliation is invalid.")

    inventory_id = _required_string(payload, "inventory_id")
    if _SHA256_PATTERN.fullmatch(inventory_id) is None:
        raise FaultLabelInventoryIntegrityError("Inventory content ID is invalid.")
    body = dict(payload)
    body.pop("inventory_id")
    if sha256(_canonical_json_bytes(body)).hexdigest() != inventory_id:
        raise FaultLabelInventoryIntegrityError("Inventory content ID does not match.")

    return FaultLabelInventory(
        inventory_id=inventory_id,
        source_fingerprint=identity.fingerprint,
        row_count=baseline.row_count,
        collision_summary=summary,
        entries=entries,
    )


def _validate_entry(value: object) -> FaultLabelInventoryEntry:
    mapping = _as_mapping(value)
    _require_keys(mapping, _APPROVED_FIELDS)
    raw_label = _required_string(mapping, "raw_label")
    frequency = _required_int(mapping, "frequency")
    if frequency <= 0:
        raise FaultLabelInventorySchemaError("Inventory frequency is invalid.")
    normalized = normalize_fault_label(raw_label)
    if (
        _required_string(mapping, "normalized_label") != normalized.normalized_label
        or _required_string(mapping, "slug") != normalized.slug
    ):
        raise FaultLabelInventorySchemaError("Inventory normalization is invalid.")
    try:
        status = CollisionStatus(_required_string(mapping, "collision_status"))
        resolution = CollisionResolution(
            _required_string(mapping, "collision_resolution")
        )
    except ValueError:
        raise FaultLabelInventoryCollisionError(
            "Inventory collision state is invalid."
        ) from None
    return FaultLabelInventoryEntry(
        raw_label=raw_label,
        frequency=frequency,
        normalized_label=normalized.normalized_label,
        slug=normalized.slug,
        collision_status=status,
        collision_resolution=resolution,
    )


def _validate_integrity_payload(
    integrity: Mapping[str, object], expected: BannerSourceFingerprint
) -> tuple[FaultLabelInventoryRoundReceipt, ...]:
    _require_keys(integrity, ("round_count", "rounds"))
    rounds = _required_sequence(integrity, "rounds")
    if (
        _required_int(integrity, "round_count") != _ROUND_COUNT
        or len(rounds) != _ROUND_COUNT
    ):
        raise FaultLabelInventoryIntegrityError("Inventory rounds are invalid.")
    receipts: list[FaultLabelInventoryRoundReceipt] = []
    for round_number, item in enumerate(rounds, start=1):
        mapping = _as_mapping(item)
        _require_keys(mapping, ("round", "pre", "post"))
        if _required_int(mapping, "round") != round_number:
            raise FaultLabelInventoryIntegrityError("Inventory round order is invalid.")
        pre = _validate_fingerprint_payload(
            _required_mapping(mapping, "pre"), "matched_manifest"
        )
        post = _validate_fingerprint_payload(
            _required_mapping(mapping, "post"), "unchanged"
        )
        if pre != expected or post != expected:
            raise FaultLabelInventoryIntegrityError(
                "Inventory fingerprint evidence is invalid."
            )
        receipts.append(FaultLabelInventoryRoundReceipt(round_number, pre, post))
    return tuple(receipts)


def _validate_fingerprint_payload(
    payload: Mapping[str, object], expected_status: str
) -> BannerSourceFingerprint:
    _require_keys(payload, ("size_bytes", "sha256", "status"))
    size_bytes = _required_int(payload, "size_bytes")
    digest = _required_string(payload, "sha256")
    if (
        size_bytes < 0
        or _SHA256_PATTERN.fullmatch(digest) is None
        or _required_string(payload, "status") != expected_status
    ):
        raise FaultLabelInventoryIntegrityError(
            "Inventory fingerprint evidence is invalid."
        )
    return BannerSourceFingerprint(size_bytes=size_bytes, sha256=digest)


def _load_validated_baseline_expectations(
    *, json_path: Path, markdown_path: Path, manifest_path: Path
) -> _BaselineExpectations:
    try:
        validate_banner_baseline_artifacts(
            json_path=json_path,
            markdown_path=markdown_path,
            manifest_path=manifest_path,
        )
        payload = _load_strict_json_object(json_path.read_bytes(), canonical=True)
        source = _required_mapping(payload, "source")
        profile = _required_mapping(payload, "profile")
        volume = _required_mapping(profile, "volume")
        labels = _required_mapping(profile, "labels")
        result = _required_string(payload, "result")
        expectations = _BaselineExpectations(
            schema_version=_required_int(payload, "baseline_schema_version"),
            source_sha256=_required_string(source, "sha256"),
            row_count=_required_int(volume, "row_count"),
            raw_label_count=_required_int(labels, "distinct_observed_label_count"),
        )
    except (OSError, BannerBaselineError, FaultLabelInventoryError):
        raise FaultLabelInventoryBaselineError(
            "Public banner baseline is unavailable or invalid."
        ) from None
    if (
        result != "passed"
        or expectations.row_count <= 0
        or expectations.raw_label_count <= 0
        or _SHA256_PATTERN.fullmatch(expectations.source_sha256) is None
    ):
        raise FaultLabelInventoryBaselineError(
            "Public banner baseline is unavailable or invalid."
        )
    return expectations


def _load_source_identity(manifest_path: Path) -> _SourceIdentity:
    try:
        payload = _load_strict_json_object(manifest_path.read_bytes(), canonical=False)
    except (OSError, FaultLabelInventoryError):
        raise FaultLabelInventorySchemaError(
            "Public source manifest is unavailable or invalid."
        ) from None
    if (
        _required_int(payload, "schema_version") != _SUPPORTED_MANIFEST_SCHEMA
        or _required_string(payload, "hash_algorithm") != _SUPPORTED_HASH_ALGORITHM
    ):
        raise FaultLabelInventorySchemaError("Public source manifest is invalid.")
    matches: list[Mapping[str, object]] = []
    for item in _required_sequence(payload, "files"):
        if isinstance(item, Mapping):
            candidate = cast(Mapping[str, object], item)
            if candidate.get("name") == _BANNER_BASENAME:
                matches.append(candidate)
    if len(matches) != 1:
        raise FaultLabelInventorySchemaError("Public source manifest is invalid.")
    entry = matches[0]
    size_bytes = _required_int(entry, "size_bytes")
    digest = _required_string(entry, "sha256")
    if size_bytes < 0 or _SHA256_PATTERN.fullmatch(digest) is None:
        raise FaultLabelInventorySchemaError("Public source manifest is invalid.")
    return _SourceIdentity(
        basename=_BANNER_BASENAME,
        fingerprint=BannerSourceFingerprint(size_bytes=size_bytes, sha256=digest),
    )


def _load_strict_json_object(
    json_bytes: bytes, *, canonical: bool
) -> dict[str, object]:
    try:
        decoded = json_bytes.decode("utf-8", errors="strict")
        value: object = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise FaultLabelInventoryJsonError("Public JSON is invalid.") from None
    if not isinstance(value, dict):
        raise FaultLabelInventoryJsonError("Public JSON root is invalid.")
    payload = cast(dict[str, object], value)
    if canonical and _canonical_json_bytes(payload) != json_bytes:
        raise FaultLabelInventoryJsonError("Public JSON is not canonical.")
    return payload


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            raise ValueError("duplicate JSON key")
        mapping[key] = value
    return mapping


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
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
        raise FaultLabelInventoryJsonError(
            "Public JSON cannot be serialized safely."
        ) from None


def _validate_inventory_path(path: Path, source_sha256: str) -> None:
    try:
        entries = tuple(path.parent.iterdir())
        valid = (
            path.name == FAULT_LABEL_INVENTORY_FILENAME
            and path.parent.name == source_sha256
            and not path.is_symlink()
            and path.is_file()
            and len(entries) == 1
            and entries[0] == path
        )
    except OSError:
        valid = False
    if not valid:
        raise FaultLabelInventorySchemaError(
            "Public fault-label inventory location is invalid."
        )


def _write_inventory_atomically(
    *, output_root: Path, source_sha256: str, json_bytes: bytes
) -> Path:
    final_directory = output_root / source_sha256
    final_path = final_directory / FAULT_LABEL_INVENTORY_FILENAME
    if final_directory.exists() or final_directory.is_symlink():
        try:
            if (
                not final_directory.is_symlink()
                and final_directory.is_dir()
                and tuple(final_directory.iterdir()) == (final_path,)
                and not final_path.is_symlink()
                and final_path.is_file()
                and final_path.read_bytes() == json_bytes
            ):
                return final_path
        except OSError:
            pass
        raise FaultLabelInventoryIntegrityError(
            "Existing public fault-label inventory differs from this run."
        )
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=output_root))
    except OSError:
        raise FaultLabelInventoryIntegrityError(
            "Public fault-label inventory staging failed."
        ) from None
    staging_path = staging / FAULT_LABEL_INVENTORY_FILENAME
    try:
        if staging_path.write_bytes(json_bytes) != len(json_bytes):
            raise OSError
        os.replace(staging, final_directory)
    except OSError:
        try:
            staging_path.unlink(missing_ok=True)
            staging.rmdir()
        except OSError:
            pass
        raise FaultLabelInventoryIntegrityError(
            "Public fault-label inventory atomic write failed."
        ) from None
    return final_path


def _receipts_match_identity(
    identity: _SourceIdentity,
    receipts: list[BannerSourceReceipt[_RoundSnapshot]],
) -> bool:
    expected = identity.fingerprint
    return len(receipts) == _ROUND_COUNT and all(
        receipt.pre_fingerprint == expected
        and receipt.post_fingerprint == expected
        and receipt.pre_fingerprint == receipt.post_fingerprint
        for receipt in receipts
    )


def _public_round_receipts(
    receipts: list[BannerSourceReceipt[_RoundSnapshot]],
) -> tuple[FaultLabelInventoryRoundReceipt, ...]:
    return tuple(
        FaultLabelInventoryRoundReceipt(
            round_number=round_number,
            pre_fingerprint=receipt.pre_fingerprint,
            post_fingerprint=receipt.post_fingerprint,
        )
        for round_number, receipt in enumerate(receipts, start=1)
    )


def _blocked(
    code: str,
    collision_summary: FaultLabelCollisionSummary,
    receipts: tuple[FaultLabelInventoryRoundReceipt, ...] = (),
) -> FaultLabelInventoryRunResult:
    return FaultLabelInventoryRunResult(
        status=FaultLabelInventoryStatus.BLOCKED,
        failure_codes=(code,),
        collision_summary=collision_summary,
        round_receipts=receipts,
        output_path=None,
        json_bytes=None,
    )


def _source_failure_code(error: BannerSourceError) -> str:
    if isinstance(error, SourceHashMismatchError):
        return "inventory.source_hash_mismatch"
    if isinstance(error, SourceSizeMismatchError):
        return "inventory.source_size_mismatch"
    if isinstance(error, SourceChangedError):
        return "inventory.source_changed"
    if isinstance(error, SourceNotFoundError):
        return "inventory.source_not_found"
    if isinstance(error, SourcePermissionError):
        return "inventory.source_permission_denied"
    if isinstance(error, UnexpectedSourceNameError):
        return "inventory.source_name_mismatch"
    if isinstance(error, SourceManifestError):
        return "inventory.manifest_invalid"
    if isinstance(error, SourceAccessError):
        return "inventory.source_access_failed"
    return "inventory.source_failed"


def _require_keys(mapping: Mapping[str, object], expected: tuple[str, ...]) -> None:
    if tuple(mapping.keys()) != expected:
        raise FaultLabelInventorySchemaError("Public object fields are invalid.")


def _matches_exact_json_value(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        actual_mapping = cast(dict[str, object], value)
        expected_mapping = cast(dict[str, object], expected)
        return tuple(actual_mapping) == tuple(expected_mapping) and all(
            _matches_exact_json_value(actual_mapping[key], expected_mapping[key])
            for key in expected_mapping
        )
    if isinstance(expected, list):
        actual_sequence = cast(list[object], value)
        expected_sequence = cast(list[object], expected)
        return len(actual_sequence) == len(expected_sequence) and all(
            _matches_exact_json_value(actual, wanted)
            for actual, wanted in zip(actual_sequence, expected_sequence, strict=True)
        )
    return value == expected


def _required_mapping(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    if key not in mapping:
        raise FaultLabelInventorySchemaError("Required public object is unavailable.")
    return _as_mapping(mapping[key])


def _required_sequence(mapping: Mapping[str, object], key: str) -> list[object]:
    if key not in mapping or not isinstance(mapping[key], list):
        raise FaultLabelInventorySchemaError("Required public sequence is unavailable.")
    return cast(list[object], mapping[key])


def _required_string(mapping: Mapping[str, object], key: str) -> str:
    if key not in mapping or not isinstance(mapping[key], str):
        raise FaultLabelInventorySchemaError("Required public text is unavailable.")
    return cast(str, mapping[key])


def _required_int(mapping: Mapping[str, object], key: str) -> int:
    if key not in mapping or type(mapping[key]) is not int:
        raise FaultLabelInventorySchemaError("Required public integer is unavailable.")
    return cast(int, mapping[key])


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FaultLabelInventorySchemaError("Public object has an invalid type.")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise FaultLabelInventorySchemaError("Public object has an invalid key.")
    return cast(Mapping[str, object], raw)
