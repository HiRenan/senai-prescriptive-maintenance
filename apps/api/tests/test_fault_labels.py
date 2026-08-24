"""Synthetic proofs for fault-label normalization and inventory publication."""

from __future__ import annotations

import csv
import gc
import json
from collections.abc import Callable, Mapping, Set
from hashlib import sha256
from io import BufferedReader, BytesIO, StringIO
from pathlib import Path
from typing import BinaryIO, Final, NoReturn, cast
from weakref import ReferenceType, ref

import pandas as pd
import prescriptive_maintenance.data.fault_labels as fault_module
import pytest
from prescriptive_maintenance.data import (
    FAULT_LABEL_CATEGORICAL_SCOPE,
    FAULT_LABEL_INVENTORY_SCHEMA_VERSION,
    FAULT_LABEL_NORMALIZATION_STEPS,
    FAULT_LABEL_NORMALIZATION_VERSION,
    FAULT_LABEL_UNICODE_VERSION,
    CollisionResolution,
    CollisionStatus,
    ControlCharacterFaultLabelError,
    EmptyFaultLabelError,
    FaultLabelCollisionGroup,
    FaultLabelInventoryCollisionError,
    FaultLabelInventoryIntegrityError,
    FaultLabelInventoryJsonError,
    FaultLabelInventoryRunResult,
    FaultLabelInventorySchemaError,
    FaultLabelInventoryStatus,
    FormatCharacterFaultLabelError,
    InvalidFaultLabelTypeError,
    InvalidUnicodeFaultLabelError,
    NormalizedFaultLabel,
    NullFaultLabelError,
    SurrogateFaultLabelError,
    UnknownFaultLabelError,
    UnsupportedUnicodeVersionError,
    load_fault_label_inventory,
    normalize_fault_label,
    resolve_known_fault_label,
    run_fault_label_inventory,
    validate_fault_label_inventory,
)
from prescriptive_maintenance.data.source import (
    BannerSourceFingerprint,
    BannerSourceReceipt,
)

_SOURCE_NAME: Final = "banner.csv"
_BASELINE_JSON_NAME: Final = "baseline.v1.json"
_BASELINE_MARKDOWN_NAME: Final = "summary.md"


class _FakeSourcePort:
    def __init__(self, contents: tuple[bytes, ...], expected_content: bytes) -> None:
        self.contents = contents
        self.expected_content = expected_content
        self.call_count = 0
        self.descriptor_writable: list[bool] = []
        self.input_paths: list[Path] = []
        self.manifest_paths: list[Path] = []

    def __call__[ConsumerResult](
        self,
        *,
        input_path: Path,
        manifest_path: Path,
        consumer: Callable[[BinaryIO], ConsumerResult],
    ) -> BannerSourceReceipt[ConsumerResult]:
        content = self.contents[self.call_count % len(self.contents)]
        self.call_count += 1
        self.input_paths.append(input_path)
        self.manifest_paths.append(manifest_path)
        with BufferedReader(BytesIO(content)) as descriptor:
            self.descriptor_writable.append(descriptor.writable())
            result = consumer(descriptor)
        fingerprint = BannerSourceFingerprint(
            size_bytes=len(self.expected_content),
            sha256=sha256(self.expected_content).hexdigest(),
        )
        return BannerSourceReceipt(
            result=result,
            pre_fingerprint=fingerprint,
            post_fingerprint=fingerprint,
        )


def _synthetic_csv(labels: tuple[str, ...]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("synthetic_id", "fault", "synthetic_sensor", "created_at"))
    for ordinal, label in enumerate(labels, start=1):
        writer.writerow(
            (
                f"synthetic-id-{ordinal}",
                label,
                f"synthetic-sensor-value-{ordinal}",
                f"2099-01-{ordinal:02d}T00:00:00Z",
            )
        )
    return output.getvalue().encode("utf-8")


def _write_manifest(directory: Path, content: bytes) -> Path:
    manifest_path = directory / "source-manifest.json"
    payload = {
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "files": [
            {
                "name": _SOURCE_NAME,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return manifest_path


def _patch_synthetic_baseline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: bytes,
    row_count: int,
    raw_label_count: int,
) -> None:
    baseline_factory = cast(
        Callable[..., object], fault_module.__dict__["_BaselineExpectations"]
    )

    def load_synthetic_baseline(
        *, json_path: Path, markdown_path: Path, manifest_path: Path
    ) -> object:
        assert json_path.name == _BASELINE_JSON_NAME
        assert markdown_path.name == _BASELINE_MARKDOWN_NAME
        assert manifest_path.name == "source-manifest.json"
        return baseline_factory(
            schema_version=1,
            source_sha256=sha256(content).hexdigest(),
            row_count=row_count,
            raw_label_count=raw_label_count,
        )

    monkeypatch.setattr(
        fault_module,
        "_load_validated_baseline_expectations",
        load_synthetic_baseline,
    )


def _run_synthetic(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    labels: tuple[str, ...],
    round_contents: tuple[bytes, ...] | None = None,
    approvals: Set[str] | None = None,
    use_real_source_port: bool = False,
    output_name: str = "inventories",
) -> tuple[FaultLabelInventoryRunResult, _FakeSourcePort | None, Path, Path, Path]:
    content = _synthetic_csv(labels)
    manifest_path = _write_manifest(tmp_path, content)
    source_path = tmp_path / _SOURCE_NAME
    baseline_json_path = tmp_path / _BASELINE_JSON_NAME
    baseline_markdown_path = tmp_path / _BASELINE_MARKDOWN_NAME
    _patch_synthetic_baseline(
        monkeypatch,
        content=content,
        row_count=len(labels),
        raw_label_count=len(set(labels)),
    )
    port: _FakeSourcePort | None = None
    if use_real_source_port:
        source_path.write_bytes(content)
    else:
        port = _FakeSourcePort(round_contents or (content,), content)
        monkeypatch.setattr(fault_module, "consume_banner_source_audited", port)
    result = run_fault_label_inventory(
        input_path=source_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_json_path,
        baseline_markdown_path=baseline_markdown_path,
        output_root=tmp_path / output_name,
        approved_normalized_collisions=approvals,
    )
    return result, port, manifest_path, baseline_json_path, baseline_markdown_path


def _payload(json_bytes: bytes | None) -> dict[str, object]:
    assert json_bytes is not None
    value: object = json.loads(json_bytes)
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
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


def _reidentify(payload: dict[str, object]) -> bytes:
    body = {key: value for key, value in payload.items() if key != "inventory_id"}
    payload["inventory_id"] = sha256(_canonical_json_bytes(body)).hexdigest()
    return _canonical_json_bytes(payload)


def test_pipeline_order_and_versions_are_frozen() -> None:
    assert FAULT_LABEL_INVENTORY_SCHEMA_VERSION == 1
    assert FAULT_LABEL_NORMALIZATION_VERSION == 1
    assert FAULT_LABEL_UNICODE_VERSION == "15.1.0"
    assert fault_module.unicodedata.unidata_version == FAULT_LABEL_UNICODE_VERSION
    assert FAULT_LABEL_NORMALIZATION_STEPS == (
        "unicode_nfkc",
        "trim",
        "collapse_whitespace",
        "casefold",
        "normalize_separators",
        "stable_slug",
    )


def test_pipeline_applies_nfkc_trim_whitespace_casefold_and_separators_in_order() -> (
    None
):
    raw = (
        "\u3000\uff33\uff59\uff4e\uff54\uff48\uff45\uff54\uff49\uff43"
        "\u2003\uff21\uff4c\uff50\uff48\uff41\uff0f\uff3a\uff4f\uff4e"
        "\uff45\u3000"
    )

    normalized = normalize_fault_label(raw)

    assert normalized == NormalizedFaultLabel(
        normalized_label="synthetic alpha zone",
        slug="synthetic-alpha-zone",
    )


@pytest.mark.parametrize(
    "synthetic_value",
    (
        "Synthetic Alpha",
        " synthetic\u2003alpha ",
        "SYNTHETIC_ALPHA",
        "Café",
        "Synthetic 🚨",
    ),
)
def test_normalization_of_normalized_output_is_idempotent(synthetic_value: str) -> None:
    first = normalize_fault_label(synthetic_value)
    second = normalize_fault_label(first.normalized_label)

    assert second == first
    assert second == normalize_fault_label(synthetic_value)


def test_composed_and_decomposed_unicode_are_equivalent() -> None:
    composed = normalize_fault_label("Synthetic Café")
    decomposed = normalize_fault_label("Synthetic Cafe\u0301")

    assert composed == decomposed


def test_unicode_whitespace_collapses_without_accepting_control_whitespace() -> None:
    normalized = normalize_fault_label("\u2003Synthetic\u202f\u2009Alpha\u3000")

    assert normalized.normalized_label == "synthetic alpha"


def test_difficult_casefolding_is_not_locale_dependent() -> None:
    assert normalize_fault_label("Synthetic Straße").normalized_label == (
        "synthetic strasse"
    )


def test_separator_allowlist_collapses_runs_and_platform_path_variants() -> None:
    normalized = normalize_fault_label("Synthetic_-Alpha/\\Zone")

    assert normalized.normalized_label == "synthetic alpha zone"
    assert normalized.slug == "synthetic-alpha-zone"


def test_accents_emoji_and_punctuation_are_preserved_without_transliteration() -> None:
    normalized = normalize_fault_label("Falha Café 🚨+")

    assert normalized.normalized_label == "falha café 🚨+"
    assert normalized.slug == "falha-caf%C3%A9-%F0%9F%9A%A8%2B"


def test_slug_is_stable_across_cwd_and_hash_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = normalize_fault_label("Synthetic Café 🚨")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONHASHSEED", "987654")

    after = normalize_fault_label("Synthetic Café 🚨")

    assert after == before


@pytest.mark.parametrize(
    ("synthetic_value", "expected_error"),
    (
        (None, NullFaultLabelError),
        (cast(str, b"synthetic-bytes"), InvalidFaultLabelTypeError),
        ("", EmptyFaultLabelError),
        ("\u2003\u3000", EmptyFaultLabelError),
        ("synthetic\x00label", ControlCharacterFaultLabelError),
        ("synthetic\nlabel", ControlCharacterFaultLabelError),
        ("synthetic\u202elabel", FormatCharacterFaultLabelError),
        ("synthetic\u200blabel", FormatCharacterFaultLabelError),
        ("synthetic\ud800label", SurrogateFaultLabelError),
        ("synthetic\ufdd0label", InvalidUnicodeFaultLabelError),
    ),
)
def test_invalid_values_fail_with_typed_sanitized_errors(
    synthetic_value: str | None,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error) as raised:
        normalize_fault_label(synthetic_value)

    assert "synthetic" not in str(raised.value).casefold()


def test_runtime_unicode_version_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fault_module, "FAULT_LABEL_UNICODE_VERSION", "0.0.0")

    with pytest.raises(UnsupportedUnicodeVersionError):
        normalize_fault_label("synthetic label")


def test_two_rounds_use_fault_only_once_each_and_discard_dataframes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta", "Synthetic Alpha")
    original_read_csv = cast(Callable[..., pd.DataFrame], fault_module.pd.read_csv)
    read_calls = 0
    dataframe_references: list[ReferenceType[pd.DataFrame]] = []

    def tracked_read_csv(*args: object, **kwargs: object) -> pd.DataFrame:
        nonlocal read_calls
        read_calls += 1
        assert len(args) == 1
        source = cast(BinaryIO, args[0])
        assert source.readable()
        assert not source.writable()
        assert kwargs["usecols"] == ("fault",)
        parsed = original_read_csv(*args, **kwargs)
        assert tuple(parsed.columns) == ("fault",)
        dataframe_references.append(ref(parsed))
        return parsed

    monkeypatch.setattr(fault_module.pd, "read_csv", tracked_read_csv)
    result, port, manifest_path, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=labels,
    )

    assert result.status is FaultLabelInventoryStatus.PASSED
    assert result.failure_codes == ()
    assert port is not None
    assert port.call_count == 2
    assert port.descriptor_writable == [False, False]
    assert read_calls == 2
    assert result.output_path is not None
    assert result.output_path.name == "fault-labels.v1.json"
    assert result.output_path.read_bytes() == result.json_bytes
    assert all(
        receipt.pre_fingerprint == receipt.post_fingerprint
        for receipt in result.round_receipts
    )

    gc.collect()
    assert all(reference() is None for reference in dataframe_references)

    inventory = load_fault_label_inventory(
        inventory_path=result.output_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_json,
        baseline_markdown_path=baseline_markdown,
    )
    assert inventory.row_count == len(labels)
    assert len(inventory.entries) == len(set(labels))


def test_real_safe_port_is_used_twice_for_a_synthetic_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta")
    result, port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=labels,
        use_real_source_port=True,
    )

    expected_content = _synthetic_csv(labels)
    expected = BannerSourceFingerprint(
        size_bytes=len(expected_content),
        sha256=sha256(expected_content).hexdigest(),
    )
    assert port is None
    assert result.status is FaultLabelInventoryStatus.PASSED
    assert tuple(
        (receipt.pre_fingerprint, receipt.post_fingerprint)
        for receipt in result.round_receipts
    ) == ((expected, expected), (expected, expected))


def test_input_order_is_removed_before_round_byte_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_labels = ("Synthetic Alpha", "Synthetic Beta", "Synthetic Alpha")
    second_labels = ("Synthetic Beta", "Synthetic Alpha", "Synthetic Alpha")
    first_content = _synthetic_csv(first_labels)
    second_content = _synthetic_csv(second_labels)

    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=first_labels,
        round_contents=(first_content, second_content),
    )

    assert result.status is FaultLabelInventoryStatus.PASSED


def test_independent_runs_produce_identical_bytes_without_path_or_time_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta", "Synthetic Alpha")
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()

    first, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=first_directory,
        monkeypatch=monkeypatch,
        labels=labels,
    )
    second, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=second_directory,
        monkeypatch=monkeypatch,
        labels=labels,
    )

    assert first.json_bytes == second.json_bytes
    assert b"generated_at" not in cast(bytes, first.json_bytes)
    assert str(tmp_path).encode("utf-8") not in cast(bytes, first.json_bytes)


def test_public_bytes_contain_only_approved_categorical_source_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta")
    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=labels,
    )
    payload = _payload(result.json_bytes)
    scope = _mapping(payload["scope"])

    assert scope["classification"] == FAULT_LABEL_CATEGORICAL_SCOPE
    assert scope["row_level_data"] is False
    assert scope["fields"] == [
        "raw_label",
        "frequency",
        "normalized_label",
        "slug",
        "collision_status",
        "collision_resolution",
    ]
    assert b"synthetic-id-" not in cast(bytes, result.json_bytes)
    assert b"synthetic-sensor-value-" not in cast(bytes, result.json_bytes)
    assert b"2099-01-" not in cast(bytes, result.json_bytes)


def test_normalized_collision_blocks_until_exact_fingerprint_is_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "synthetic-alpha")
    blocked_directory = tmp_path / "blocked"
    approved_directory = tmp_path / "approved"
    blocked_directory.mkdir()
    approved_directory.mkdir()

    blocked, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=blocked_directory,
        monkeypatch=monkeypatch,
        labels=labels,
    )

    assert blocked.status is FaultLabelInventoryStatus.BLOCKED
    assert blocked.failure_codes == ("inventory.normalized_collision_unresolved",)
    assert blocked.output_path is None
    assert blocked.collision_summary.normalized_label_group_count == 1
    assert blocked.collision_summary.normalized_label_raw_count == 2
    group_ids = blocked.collision_summary.normalized_label_group_ids
    assert len(group_ids) == 1
    groups = blocked.collision_summary.normalized_label_groups
    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, FaultLabelCollisionGroup)
    assert group.normalization_version == FAULT_LABEL_NORMALIZATION_VERSION
    assert group.normalized_label == "synthetic alpha"
    assert group.raw_labels == tuple(sorted(labels))
    assert "frequency" not in FaultLabelCollisionGroup.__slots__
    assert (
        group.group_id
        == sha256(
            _canonical_json_bytes(
                {
                    "normalization_version": FAULT_LABEL_NORMALIZATION_VERSION,
                    "kind": "normalized_label",
                    "target": group.normalized_label,
                    "members": list(group.raw_labels),
                }
            )
        ).hexdigest()
    )

    approved, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=approved_directory,
        monkeypatch=monkeypatch,
        labels=labels,
        approvals=frozenset(group_ids),
    )
    assert approved.status is FaultLabelInventoryStatus.PASSED
    assert approved.output_path is not None
    approved_payload = _payload(approved.json_bytes)
    decisions = _sequence(
        _mapping(approved_payload["collisions"])["approved_normalized_label_decisions"]
    )
    assert decisions == [
        {
            "group_id": group.group_id,
            "normalization_version": FAULT_LABEL_NORMALIZATION_VERSION,
            "normalized_label": group.normalized_label,
            "raw_labels": list(group.raw_labels),
            "resolution": "approved_textual_equivalence",
        }
    ]
    approved_body = {
        key: value for key, value in approved_payload.items() if key != "inventory_id"
    }
    assert (
        approved_payload["inventory_id"]
        == sha256(_canonical_json_bytes(approved_body)).hexdigest()
    )
    inventory = load_fault_label_inventory(
        inventory_path=approved.output_path,
        manifest_path=manifest,
        baseline_json_path=baseline_json,
        baseline_markdown_path=baseline_markdown,
    )
    assert all(
        entry.collision_status is CollisionStatus.RESOLVED
        and entry.collision_resolution
        is CollisionResolution.APPROVED_TEXTUAL_EQUIVALENCE
        for entry in inventory.entries
    )


def test_unknown_or_extra_collision_approval_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
        approvals=frozenset({"0" * 64}),
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.collision_approval_invalid",)


@pytest.mark.parametrize(
    "invalid_approvals",
    (["0" * 64], ("0" * 64,), "0" * 64),
)
def test_collision_approvals_require_a_set_at_runtime(
    invalid_approvals: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
        approvals=cast(Set[str], invalid_approvals),
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.collision_approval_invalid",)
    assert result.output_path is None


def test_distinct_normalized_labels_with_the_same_slug_are_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forced_slug(_normalized_label: str) -> str:
        return "synthetic-forced-slug"

    monkeypatch.setattr(
        fault_module,
        "_stable_slug",
        forced_slug,
    )

    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.slug_collision",)
    assert result.collision_summary.slug_group_count == 1
    assert result.output_path is None


def test_nondeterministic_normalizer_is_detected_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def alternating_normalizer(_raw_label: str) -> NormalizedFaultLabel:
        nonlocal call_count
        call_count += 1
        suffix = "one" if call_count % 2 else "two"
        return NormalizedFaultLabel(suffix, suffix)

    monkeypatch.setattr(
        fault_module,
        "normalize_fault_label",
        alternating_normalizer,
    )
    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha",),
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.normalization_nondeterministic",)
    assert result.output_path is None


def test_round_byte_difference_is_blocking_even_with_matching_fake_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _synthetic_csv(("Synthetic Alpha", "Synthetic Beta"))
    second = _synthetic_csv(("Synthetic Alpha", "Synthetic Gamma"))

    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
        round_contents=(first, second),
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.round_byte_mismatch",)
    assert result.output_path is None


def test_row_count_and_raw_cardinality_reconcile_with_public_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta")
    content = _synthetic_csv(labels)
    manifest_path = _write_manifest(tmp_path, content)
    port = _FakeSourcePort((content,), content)
    monkeypatch.setattr(fault_module, "consume_banner_source_audited", port)
    baseline_factory = cast(
        Callable[..., object], fault_module.__dict__["_BaselineExpectations"]
    )

    def divergent_baseline(
        *, json_path: Path, markdown_path: Path, manifest_path: Path
    ) -> object:
        del json_path, markdown_path, manifest_path
        return baseline_factory(
            schema_version=1,
            source_sha256=sha256(content).hexdigest(),
            row_count=3,
            raw_label_count=2,
        )

    monkeypatch.setattr(
        fault_module,
        "_load_validated_baseline_expectations",
        divergent_baseline,
    )
    result = run_fault_label_inventory(
        input_path=tmp_path / _SOURCE_NAME,
        manifest_path=manifest_path,
        baseline_json_path=tmp_path / _BASELINE_JSON_NAME,
        baseline_markdown_path=tmp_path / _BASELINE_MARKDOWN_NAME,
        output_root=tmp_path / "inventories",
    )

    assert result.status is FaultLabelInventoryStatus.BLOCKED
    assert result.failure_codes == ("inventory.row_count_mismatch",)
    assert result.output_path is None


def test_lookup_is_exact_and_fails_closed_for_normalized_but_unseen_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None
    inventory = load_fault_label_inventory(
        inventory_path=result.output_path,
        manifest_path=manifest,
        baseline_json_path=baseline_json,
        baseline_markdown_path=baseline_markdown,
    )

    known = resolve_known_fault_label("Synthetic Alpha", inventory)
    assert known.raw_label == "Synthetic Alpha"
    with pytest.raises(UnknownFaultLabelError) as raised:
        resolve_known_fault_label("SYNTHETIC_ALPHA", inventory)
    assert "SYNTHETIC_ALPHA" not in str(raised.value)


def test_offline_validator_never_calls_the_source_port_or_discovers_pdfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None

    def reject_source(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("offline validation must not access banner.csv")

    def reject_discovery(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("offline validation must not discover protected materials")

    monkeypatch.setattr(fault_module, "consume_banner_source_audited", reject_source)
    monkeypatch.setattr(Path, "glob", reject_discovery)
    monkeypatch.setattr(Path, "rglob", reject_discovery)

    validate_fault_label_inventory(
        inventory_path=result.output_path,
        manifest_path=manifest,
        baseline_json_path=baseline_json,
        baseline_markdown_path=baseline_markdown,
    )


def test_inventory_id_hashes_the_canonical_body_and_source_sha_matches_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "Synthetic Beta")
    result, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=labels,
    )
    payload = _payload(result.json_bytes)
    inventory_id = payload.pop("inventory_id")
    source = _mapping(payload["source"])

    assert inventory_id == sha256(_canonical_json_bytes(payload)).hexdigest()
    assert source["source_sha256"] == sha256(_synthetic_csv(labels)).hexdigest()


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None
    original = cast(bytes, result.json_bytes)
    duplicate = original.replace(
        b'  "inventory_schema_version": 1,',
        b'  "inventory_schema_version": 1,\n  "inventory_schema_version": 1,',
        1,
    )
    result.output_path.write_bytes(duplicate)
    with pytest.raises(FaultLabelInventoryJsonError):
        load_fault_label_inventory(
            inventory_path=result.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )

    nonfinite = original.replace(b'"frequency": 1', b'"frequency": NaN', 1)
    result.output_path.write_bytes(nonfinite)
    with pytest.raises(FaultLabelInventoryJsonError):
        load_fault_label_inventory(
            inventory_path=result.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )


@pytest.mark.parametrize(
    "mutation",
    ("extra", "type", "nested_type", "order", "nested_order", "canonical", "id"),
)
def test_strict_json_rejects_schema_type_order_canonical_and_id_mutations(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None
    payload = _payload(result.json_bytes)
    if mutation == "extra":
        payload["synthetic_extra"] = False
        content = _canonical_json_bytes(payload)
    elif mutation == "type":
        _mapping(_sequence(payload["labels"])[0])["frequency"] = True
        content = _canonical_json_bytes(payload)
    elif mutation == "nested_type":
        _mapping(payload["scope"])["row_level_data"] = 0
        content = _canonical_json_bytes(payload)
    elif mutation == "order":
        reordered = {"versions": payload["versions"]}
        reordered.update(payload)
        content = _canonical_json_bytes(reordered)
    elif mutation == "nested_order":
        versions = _mapping(payload["versions"])
        payload["versions"] = {
            "normalization": versions["normalization"],
            "inventory_schema": versions["inventory_schema"],
            "unicode": versions["unicode"],
            "baseline_schema": versions["baseline_schema"],
        }
        content = _canonical_json_bytes(payload)
    elif mutation == "canonical":
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    else:
        payload["inventory_id"] = "0" * 64
        content = _canonical_json_bytes(payload)
    result.output_path.write_bytes(content)

    with pytest.raises(
        (
            FaultLabelInventoryJsonError,
            FaultLabelInventorySchemaError,
            FaultLabelInventoryIntegrityError,
        )
    ):
        load_fault_label_inventory(
            inventory_path=result.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )


def test_loader_rejects_duplicate_raws_and_unresolved_normalized_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "synthetic-alpha")
    blocked_directory = tmp_path / "blocked"
    approved_directory = tmp_path / "approved"
    blocked_directory.mkdir()
    approved_directory.mkdir()
    blocked, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=blocked_directory,
        monkeypatch=monkeypatch,
        labels=labels,
    )
    group_ids = blocked.collision_summary.normalized_label_group_ids
    approved, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=approved_directory,
        monkeypatch=monkeypatch,
        labels=labels,
        approvals=frozenset(group_ids),
    )
    assert approved.output_path is not None
    original = _payload(approved.json_bytes)

    duplicate_raw = cast(dict[str, object], json.loads(_canonical_json_bytes(original)))
    duplicate_entries = _sequence(duplicate_raw["labels"])
    first_entry = _mapping(duplicate_entries[0])
    second_entry = _mapping(duplicate_entries[1])
    second_entry.update(first_entry)
    approved.output_path.write_bytes(_reidentify(duplicate_raw))
    with pytest.raises(FaultLabelInventorySchemaError):
        load_fault_label_inventory(
            inventory_path=approved.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )

    unresolved = cast(dict[str, object], json.loads(_canonical_json_bytes(original)))
    for item in _sequence(unresolved["labels"]):
        entry = _mapping(item)
        entry["collision_status"] = CollisionStatus.CLEAR.value
        entry["collision_resolution"] = CollisionResolution.NOT_REQUIRED.value
    approved.output_path.write_bytes(_reidentify(unresolved))
    with pytest.raises(FaultLabelInventoryCollisionError):
        load_fault_label_inventory(
            inventory_path=approved.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )


def test_loader_requires_exact_persisted_collision_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = ("Synthetic Alpha", "synthetic-alpha")
    blocked_directory = tmp_path / "blocked"
    approved_directory = tmp_path / "approved"
    blocked_directory.mkdir()
    approved_directory.mkdir()
    blocked, _port, _manifest, _baseline_json, _baseline_markdown = _run_synthetic(
        tmp_path=blocked_directory,
        monkeypatch=monkeypatch,
        labels=labels,
    )
    approved, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=approved_directory,
        monkeypatch=monkeypatch,
        labels=labels,
        approvals=frozenset(blocked.collision_summary.normalized_label_group_ids),
    )
    assert approved.output_path is not None
    original = _payload(approved.json_bytes)

    for mutation in (
        "missing",
        "group_id",
        "version",
        "target",
        "members",
        "resolution",
        "extra",
        "order",
    ):
        payload = cast(dict[str, object], json.loads(_canonical_json_bytes(original)))
        collisions = _mapping(payload["collisions"])
        if mutation == "missing":
            collisions["approved_normalized_label_decisions"] = []
        else:
            decision = _mapping(
                _sequence(collisions["approved_normalized_label_decisions"])[0]
            )
            if mutation == "group_id":
                decision["group_id"] = "0" * 64
            elif mutation == "version":
                decision["normalization_version"] = (
                    FAULT_LABEL_NORMALIZATION_VERSION + 1
                )
            elif mutation == "target":
                decision["normalized_label"] = "synthetic changed target"
            elif mutation == "members":
                decision["raw_labels"] = list(
                    reversed(_sequence(decision["raw_labels"]))
                )
            elif mutation == "resolution":
                decision["resolution"] = CollisionResolution.NOT_REQUIRED.value
            elif mutation == "extra":
                decision["synthetic_extra"] = False
            else:
                collisions["approved_normalized_label_decisions"] = [
                    {
                        "normalization_version": decision["normalization_version"],
                        "group_id": decision["group_id"],
                        "normalized_label": decision["normalized_label"],
                        "raw_labels": decision["raw_labels"],
                        "resolution": decision["resolution"],
                    }
                ]
        approved.output_path.write_bytes(_reidentify(payload))

        with pytest.raises(FaultLabelInventoryCollisionError):
            load_fault_label_inventory(
                inventory_path=approved.output_path,
                manifest_path=manifest,
                baseline_json_path=baseline_json,
                baseline_markdown_path=baseline_markdown,
            )


def test_loader_detects_distinct_normalized_labels_sharing_a_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None
    payload = _payload(result.json_bytes)
    for item in _sequence(payload["labels"]):
        _mapping(item)["slug"] = "synthetic-forced-slug"
    result.output_path.write_bytes(_reidentify(payload))

    def forced_slug(_normalized_label: str) -> str:
        return "synthetic-forced-slug"

    monkeypatch.setattr(
        fault_module,
        "_stable_slug",
        forced_slug,
    )

    with pytest.raises(FaultLabelInventoryCollisionError):
        load_fault_label_inventory(
            inventory_path=result.output_path,
            manifest_path=manifest,
            baseline_json_path=baseline_json,
            baseline_markdown_path=baseline_markdown,
        )


def test_runner_and_offline_validator_do_not_discover_or_open_pdfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_discovery(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("fault inventory must not discover protected materials")

    monkeypatch.setattr(Path, "glob", reject_discovery)
    monkeypatch.setattr(Path, "rglob", reject_discovery)
    result, port, manifest, baseline_json, baseline_markdown = _run_synthetic(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        labels=("Synthetic Alpha", "Synthetic Beta"),
    )
    assert result.output_path is not None
    assert port is not None
    assert port.input_paths == [tmp_path / _SOURCE_NAME, tmp_path / _SOURCE_NAME]
    assert all(path.suffix.casefold() != ".pdf" for path in port.input_paths)

    validate_fault_label_inventory(
        inventory_path=result.output_path,
        manifest_path=manifest,
        baseline_json_path=baseline_json,
        baseline_markdown_path=baseline_markdown,
    )


def test_tracked_inventory_is_valid_offline_without_banner_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_source(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("tracked offline validation must not access banner.csv")

    monkeypatch.setattr(fault_module, "consume_banner_source_audited", reject_source)
    repository_root = Path(__file__).parents[3]
    manifest_path = repository_root / "data" / "source-manifest.json"
    manifest_value: object = json.loads(manifest_path.read_bytes())
    manifest = _mapping(manifest_value)
    source_entries: list[dict[str, object]] = []
    for item in _sequence(manifest["files"]):
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("name") == _SOURCE_NAME:
            source_entries.append(candidate)
    assert len(source_entries) == 1
    source_sha256 = source_entries[0]["sha256"]
    assert isinstance(source_sha256, str)
    baseline_root = repository_root / "data" / "baselines" / "banner" / source_sha256
    inventory_path = (
        repository_root
        / "data"
        / "inventories"
        / "banner"
        / source_sha256
        / "fault-labels.v1.json"
    )

    inventory = load_fault_label_inventory(
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        baseline_json_path=baseline_root / _BASELINE_JSON_NAME,
        baseline_markdown_path=baseline_root / _BASELINE_MARKDOWN_NAME,
    )

    assert inventory.row_count == 166_796
    assert len(inventory.entries) == 151
    assert sum(entry.frequency for entry in inventory.entries) == 166_796
    assert inventory.source_fingerprint.sha256 == source_sha256
    assert inventory.collision_summary.normalized_label_group_count == 0
    assert inventory.collision_summary.slug_group_count == 0
