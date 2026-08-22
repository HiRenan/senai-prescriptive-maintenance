"""Synthetic proofs for the deterministic audited banner baseline runner."""

from __future__ import annotations

import gc
import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from io import BufferedReader, BytesIO
from pathlib import Path
from typing import BinaryIO, Final, NoReturn, cast
from weakref import ReferenceType, ref

import pandas as pd
import prescriptive_maintenance.data.baseline as baseline_module
import pytest
from prescriptive_maintenance.data import (
    BANNER_COLUMN_CATALOG,
    PUBLIC_BANNER_BASELINE_SCHEMA,
    BannerBaselinePrivacyError,
    BannerBaselineStatus,
    BannerSourceFingerprint,
    BannerSourceReceipt,
    ContractViolationCode,
    ProfileFieldClassification,
    SourceChangedError,
    SourceHashMismatchError,
    consume_banner_source_audited,
    profile_banner_dataframe,
    render_banner_baseline_markdown,
    run_banner_baseline,
    validate_banner_baseline_artifacts,
    validate_banner_dataframe,
    validate_public_baseline_schema,
)
from synthetic_banner_factory import BannerScenario, make_banner_dataframe

_SYNTHETIC_SOURCE_NAME: Final = "banner.csv"


class _FakeSourcePort:
    def __init__(
        self,
        content: bytes,
        *,
        pre_error: Exception | None = None,
        post_error: Exception | None = None,
    ) -> None:
        self.content = content
        self.pre_error = pre_error
        self.post_error = post_error
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
        self.call_count += 1
        self.input_paths.append(input_path)
        self.manifest_paths.append(manifest_path)
        if self.pre_error is not None:
            raise self.pre_error
        with BufferedReader(BytesIO(self.content)) as descriptor:
            self.descriptor_writable.append(descriptor.writable())
            result = consumer(descriptor)
        if self.post_error is not None:
            raise self.post_error
        fingerprint = BannerSourceFingerprint(
            size_bytes=len(self.content),
            sha256=sha256(self.content).hexdigest(),
        )
        return BannerSourceReceipt(
            result=result,
            pre_fingerprint=fingerprint,
            post_fingerprint=fingerprint,
        )


def _csv_bytes(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _write_manifest(directory: Path, content: bytes) -> Path:
    manifest_path = directory / "source-manifest.json"
    payload = {
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "files": [
            {
                "name": _SYNTHETIC_SOURCE_NAME,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return manifest_path


def _accept_synthetic_expectations(
    monkeypatch: pytest.MonkeyPatch, dataframe: pd.DataFrame
) -> None:
    valid_labels = {
        value
        for value in dataframe["fault"]
        if isinstance(value, str) and bool(value.strip())
    }
    monkeypatch.setattr(baseline_module, "EXPECTED_BANNER_ROW_COUNT", len(dataframe))
    monkeypatch.setattr(
        baseline_module, "EXPECTED_BANNER_COLUMN_COUNT", len(dataframe.columns)
    )
    monkeypatch.setattr(
        baseline_module, "EXPECTED_RAW_FAULT_CARDINALITY", len(valid_labels)
    )


def _run_synthetic(
    *,
    dataframe: pd.DataFrame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accept_expectations: bool = True,
) -> tuple[baseline_module.BannerBaselineRunResult, _FakeSourcePort, Path]:
    content = _csv_bytes(dataframe)
    manifest_path = _write_manifest(tmp_path, content)
    source_path = tmp_path / _SYNTHETIC_SOURCE_NAME
    port = _FakeSourcePort(content)
    monkeypatch.setattr(baseline_module, "consume_banner_source_audited", port)
    if accept_expectations:
        _accept_synthetic_expectations(monkeypatch, dataframe)
    result = run_banner_baseline(
        input_path=source_path,
        manifest_path=manifest_path,
        output_root=tmp_path / "baselines",
    )
    return result, port, manifest_path


def _json_payload(content: bytes | None) -> dict[str, object]:
    assert content is not None
    payload: object = json.loads(content)
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _canonical_json(payload: MappingForTest) -> bytes:
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


type MappingForTest = dict[str, object]


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _gate(payload: dict[str, object], code: str) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for item in _sequence(payload["gates"]):
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("code") == code:
            matches.append(candidate)
    assert len(matches) == 1
    return matches[0]


def _profile_column(payload: dict[str, object], name: str) -> dict[str, object]:
    profile = _mapping(payload["profile"])
    matches: list[dict[str, object]] = []
    for item in _sequence(profile["columns"]):
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("name") == name:
            matches.append(candidate)
    assert len(matches) == 1
    return matches[0]


def test_two_independent_rounds_parse_once_each_then_write_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataframe = make_banner_dataframe()
    original_parser = baseline_module.parse_banner_csv
    parser_calls = 0
    dataframe_references: list[ReferenceType[pd.DataFrame]] = []

    def tracked_parser(source: BinaryIO) -> pd.DataFrame:
        nonlocal parser_calls
        parser_calls += 1
        parsed = original_parser(source)
        dataframe_references.append(ref(parsed))
        return parsed

    monkeypatch.setattr(baseline_module, "parse_banner_csv", tracked_parser)
    result, port, manifest_path = _run_synthetic(
        dataframe=dataframe,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert result.status is BannerBaselineStatus.PASSED
    assert result.failure_codes == ()
    assert port.call_count == 2
    assert parser_calls == 2
    assert port.descriptor_writable == [False, False]
    assert result.output_directory is not None
    assert result.output_directory.name == sha256(_csv_bytes(dataframe)).hexdigest()
    assert result.json_bytes is not None
    assert result.markdown_bytes == render_banner_baseline_markdown(result.json_bytes)
    assert result.output_directory.joinpath("baseline.v1.json").read_bytes() == (
        result.json_bytes
    )
    assert result.output_directory.joinpath("summary.md").read_bytes() == (
        result.markdown_bytes
    )
    artifact_entries = tuple(result.output_directory.iterdir())
    assert {entry.name for entry in artifact_entries} == {
        "baseline.v1.json",
        "summary.md",
    }
    assert all(entry.is_file() and not entry.is_symlink() for entry in artifact_entries)
    validate_banner_baseline_artifacts(
        json_path=result.output_directory / "baseline.v1.json",
        markdown_path=result.output_directory / "summary.md",
        manifest_path=manifest_path,
    )

    gc.collect()
    assert all(reference() is None for reference in dataframe_references)


def test_existing_writer_and_offline_validator_reject_an_extra_artifact_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataframe = make_banner_dataframe()
    result, port, manifest_path = _run_synthetic(
        dataframe=dataframe,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    (result.output_directory / "synthetic-extra.txt").write_text(
        "synthetic",
        encoding="utf-8",
    )

    with pytest.raises(BannerBaselinePrivacyError):
        validate_banner_baseline_artifacts(
            json_path=result.output_directory / "baseline.v1.json",
            markdown_path=result.output_directory / "summary.md",
            manifest_path=manifest_path,
        )

    repeated = run_banner_baseline(
        input_path=tmp_path / _SYNTHETIC_SOURCE_NAME,
        manifest_path=manifest_path,
        output_root=tmp_path / "baselines",
    )

    assert port.call_count == 4
    assert repeated.status is BannerBaselineStatus.BLOCKED
    assert repeated.failure_codes == ("baseline.output_write_failed",)
    assert repeated.output_directory is None


@pytest.mark.parametrize("mutation", ("missing", "directory", "symlink"))
def test_offline_validator_requires_regular_non_symlink_artifacts(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    summary_path = result.output_directory / "summary.md"
    summary_path.unlink()
    if mutation == "directory":
        summary_path.mkdir()
    elif mutation == "symlink":
        target = tmp_path / "synthetic-summary-target.md"
        target.write_text("synthetic", encoding="utf-8")
        try:
            summary_path.symlink_to(target)
        except OSError:
            pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(BannerBaselinePrivacyError):
        validate_banner_baseline_artifacts(
            json_path=result.output_directory / "baseline.v1.json",
            markdown_path=summary_path,
            manifest_path=manifest_path,
        )


def test_parser_is_strict_explicit_and_preserves_nonempty_labels_exactly() -> None:
    dataframe = make_banner_dataframe()
    dataframe.loc[:, "fault"] = pd.Series(
        ["NA", " synthetic label ", "synthetic/label"], dtype="string"
    )
    dataframe.loc[0, "created_at"] = "2099-04-01T00:00:00.000000123Z"
    content = _csv_bytes(dataframe)

    with BufferedReader(BytesIO(content)) as descriptor:
        assert descriptor.writable() is False
        parsed = baseline_module.parse_banner_csv(descriptor)

    assert tuple(parsed["fault"]) == (
        "NA",
        " synthetic label ",
        "synthetic/label",
    )
    assert parsed.loc[0, "created_at"] == "2099-04-01T00:00:00.000000123Z"
    assert isinstance(parsed.loc[0, "created_at"], str)
    expected_dtypes = {
        column.name: {
            "int64": "int64",
            "float64": "float64",
            "string": "string",
            "utc_timestamp_string": "string",
        }[column.logical_type.value]
        for column in BANNER_COLUMN_CATALOG
    }
    assert {
        name: str(dtype) for name, dtype in parsed.dtypes.items()
    } == expected_dtypes


@pytest.mark.parametrize(
    ("column", "expected_dtype", "expected_code", "missing_counter"),
    (
        ("id", "Int64", ContractViolationCode.NULL_NOT_ALLOWED, "null_count"),
        ("rpm", "float64", ContractViolationCode.NAN_NOT_ALLOWED, "nan_count"),
        (
            "created_at",
            "string",
            ContractViolationCode.NULL_NOT_ALLOWED,
            "null_count",
        ),
        ("fault", "string", ContractViolationCode.NULL_NOT_ALLOWED, "null_count"),
    ),
)
def test_parser_preserves_exact_empty_cells_as_missing_for_quality_analysis(
    column: str,
    expected_dtype: str,
    expected_code: ContractViolationCode,
    missing_counter: str,
) -> None:
    dataframe = make_banner_dataframe()
    if column == "id":
        dataframe[column] = dataframe[column].astype("Int64")
    dataframe.loc[0, column] = pd.NA

    with BufferedReader(BytesIO(_csv_bytes(dataframe))) as descriptor:
        parsed = baseline_module.parse_banner_csv(descriptor)

    assert pd.isna(parsed.loc[0, column])
    assert str(parsed[column].dtype) == expected_dtype
    report = validate_banner_dataframe(parsed, allowed_fault_categories=None)
    assert expected_code in {violation.code for violation in report.blocking_violations}
    profile = profile_banner_dataframe(
        parsed,
        key_columns=("id",),
        allowed_fault_categories=None,
    )
    column_profile = next(item for item in profile.columns if item.name == column)
    assert column_profile.missing_count == 1
    assert getattr(column_profile, missing_counter) == 1


@pytest.mark.parametrize("token", ("NA", "N/A", "null"))
def test_parser_does_not_treat_default_na_tokens_as_missing(token: str) -> None:
    dataframe = make_banner_dataframe()
    dataframe["rpm"] = dataframe["rpm"].astype("object")
    dataframe.loc[0, "rpm"] = token

    with (
        BufferedReader(BytesIO(_csv_bytes(dataframe))) as descriptor,
        pytest.raises(ValueError),
    ):
        baseline_module.parse_banner_csv(descriptor)


@pytest.mark.parametrize(
    "content",
    (
        _csv_bytes(make_banner_dataframe()) + b"too,few,fields\n",
        b"id,created_at\n1,\xff\n",
    ),
)
def test_parser_rejects_malformed_or_non_utf8_input(content: bytes) -> None:
    with (
        BufferedReader(BytesIO(content)) as descriptor,
        pytest.raises((pd.errors.ParserError, UnicodeDecodeError, ValueError)),
    ):
        baseline_module.parse_banner_csv(descriptor)


def test_real_expectation_divergence_is_blocking_and_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, port, _manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        accept_expectations=False,
    )

    assert result.status is BannerBaselineStatus.BLOCKED
    assert port.call_count == 2
    assert set(result.failure_codes) == {
        "gate.expectation.dimensions",
        "gate.expectation.fault_cardinality",
    }
    assert result.output_directory is None
    assert not (tmp_path / "baselines").exists()
    payload = _json_payload(result.json_bytes)
    assert payload["result"] == "blocked"


@pytest.mark.parametrize(
    ("pre_error", "post_error", "expected_code", "expected_parser_calls"),
    (
        (
            SourceHashMismatchError("private expected and actual hashes"),
            None,
            "baseline.source_hash_mismatch",
            0,
        ),
        (
            None,
            SourceChangedError("private post-consumption evidence"),
            "baseline.source_changed",
            1,
        ),
    ),
)
def test_pre_and_post_integrity_failures_are_sanitized_and_never_write(
    pre_error: Exception | None,
    post_error: Exception | None,
    expected_code: str,
    expected_parser_calls: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataframe = make_banner_dataframe()
    content = _csv_bytes(dataframe)
    manifest_path = _write_manifest(tmp_path, content)
    port = _FakeSourcePort(content, pre_error=pre_error, post_error=post_error)
    parser_calls = 0
    original_parser = baseline_module.parse_banner_csv

    def tracked_parser(source: BinaryIO) -> pd.DataFrame:
        nonlocal parser_calls
        parser_calls += 1
        return original_parser(source)

    monkeypatch.setattr(baseline_module, "consume_banner_source_audited", port)
    monkeypatch.setattr(baseline_module, "parse_banner_csv", tracked_parser)
    result = run_banner_baseline(
        input_path=tmp_path / _SYNTHETIC_SOURCE_NAME,
        manifest_path=manifest_path,
        output_root=tmp_path / "baselines",
    )

    assert result.status is BannerBaselineStatus.BLOCKED
    assert result.failure_codes == (expected_code,)
    assert result.json_bytes is None
    assert result.markdown_bytes is None
    assert result.output_directory is None
    assert port.call_count == 1
    assert parser_calls == expected_parser_calls
    assert not (tmp_path / "baselines").exists()


def test_coordinated_manifest_and_source_change_between_rounds_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_dataframe = make_banner_dataframe()
    second_dataframe = first_dataframe.copy()
    second_dataframe.loc[0, "rpm"] = cast(float, second_dataframe.loc[0, "rpm"]) + 1
    first_content = _csv_bytes(first_dataframe)
    second_content = _csv_bytes(second_dataframe)
    source_path = tmp_path / _SYNTHETIC_SOURCE_NAME
    source_path.write_bytes(first_content)
    manifest_path = _write_manifest(tmp_path, first_content)
    call_count = 0

    def switching_port[ConsumerResult](
        *,
        input_path: Path,
        manifest_path: Path,
        consumer: Callable[[BinaryIO], ConsumerResult],
    ) -> BannerSourceReceipt[ConsumerResult]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            input_path.write_bytes(second_content)
            _write_manifest(tmp_path, second_content)
        return consume_banner_source_audited(
            input_path=input_path,
            manifest_path=manifest_path,
            consumer=consumer,
        )

    _accept_synthetic_expectations(monkeypatch, first_dataframe)
    monkeypatch.setattr(
        baseline_module,
        "consume_banner_source_audited",
        switching_port,
    )

    result = run_banner_baseline(
        input_path=source_path,
        manifest_path=manifest_path,
        output_root=tmp_path / "baselines",
    )

    assert call_count == 2
    assert result.status is BannerBaselineStatus.BLOCKED
    assert result.failure_codes == ("baseline.integrity_receipt_mismatch",)
    assert result.json_bytes is None
    assert result.markdown_bytes is None
    assert result.output_directory is None
    assert not (tmp_path / "baselines").exists()


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    (
        ("parser", "baseline.parsing_failed"),
        ("contract", "baseline.contract_execution_failed"),
        ("profiler", "baseline.profiler_execution_failed"),
    ),
)
def test_stage_exceptions_publish_only_sanitized_codes(
    stage: str,
    expected_code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataframe = make_banner_dataframe()
    content = _csv_bytes(dataframe)
    manifest_path = _write_manifest(tmp_path, content)
    port = _FakeSourcePort(content)

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("C:\\Users\\private\\secret-token=private-value")

    monkeypatch.setattr(baseline_module, "consume_banner_source_audited", port)
    if stage == "parser":
        monkeypatch.setattr(baseline_module, "parse_banner_csv", fail)
    elif stage == "contract":
        monkeypatch.setattr(baseline_module, "validate_banner_dataframe", fail)
    else:
        monkeypatch.setattr(baseline_module, "profile_banner_dataframe", fail)

    result = run_banner_baseline(
        input_path=tmp_path / _SYNTHETIC_SOURCE_NAME,
        manifest_path=manifest_path,
        output_root=tmp_path / "baselines",
    )

    assert result.failure_codes == (expected_code,)
    assert "private" not in "".join(result.failure_codes)
    assert result.json_bytes is None
    assert result.output_directory is None


def test_contract_failure_is_a_sanitized_blocking_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataframe = make_banner_dataframe(scenario=BannerScenario.INVALID_TIMESTAMP)
    private_timestamp = cast(str, dataframe.loc[0, "created_at"])
    result, _port, _manifest_path = _run_synthetic(
        dataframe=dataframe,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    payload = _json_payload(result.json_bytes)
    contract = _mapping(payload["contract_report"])

    assert result.status is BannerBaselineStatus.BLOCKED
    assert result.failure_codes == ("gate.contract.banner",)
    assert contract["passed"] is False
    assert contract["blocking_violation_count"] == 1
    assert private_timestamp.encode("utf-8") not in cast(bytes, result.json_bytes)
    assert result.output_directory is None


def test_complete_duplicates_repeated_ids_and_conflicts_are_separate_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = make_banner_dataframe()
    identical = base.iloc[[0]].copy()
    conflicting = base.iloc[[0]].copy()
    conflicting.loc[:, "rpm"] = 1801.0
    dataframe = pd.concat([base, identical, conflicting], ignore_index=True)

    result, _port, _manifest_path = _run_synthetic(
        dataframe=dataframe,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    payload = _json_payload(result.json_bytes)
    profile = _mapping(payload["profile"])
    duplicates = _mapping(profile["duplicates"])

    assert result.status is BannerBaselineStatus.PASSED
    assert duplicates["key_columns"] == ["id"]
    assert duplicates["complete_duplicate_excess_row_count"] == 1
    assert cast(int, duplicates["duplicate_key_group_count"]) > 0
    assert cast(int, duplicates["conflicting_key_group_count"]) > 0
    assert _gate(payload, "quality.complete_duplicates")["passed"] is False
    assert _gate(payload, "quality.repeated_ids")["passed"] is False
    assert _gate(payload, "quality.conflicting_ids")["passed"] is False
    assert b"-24001" not in cast(bytes, result.json_bytes)
    assert all(
        _mapping(item)["passed"] is True
        for item in _sequence(payload["reconciliations"])
    )


def test_fault_aliases_remain_anonymous_and_cannot_change_public_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_banner_dataframe()
    first.loc[:, "fault"] = pd.Series(
        ["private-alias-a", "private-alias-a", "private-alias-b"],
        dtype="string",
    )
    first_directory = tmp_path / "first"
    first_directory.mkdir()
    first_result, _port, _manifest = _run_synthetic(
        dataframe=first,
        tmp_path=first_directory,
        monkeypatch=monkeypatch,
    )

    second = make_banner_dataframe()
    second.loc[:, "fault"] = pd.Series(
        ["other-alias-x", "other-alias-x", "other-alias-y"],
        dtype="string",
    )
    second_directory = tmp_path / "second"
    second_directory.mkdir()
    second_result, _port, _manifest = _run_synthetic(
        dataframe=second,
        tmp_path=second_directory,
        monkeypatch=monkeypatch,
    )

    first_payload = _json_payload(first_result.json_bytes)
    second_payload = _json_payload(second_result.json_bytes)
    first_labels = _mapping(_mapping(first_payload["profile"])["labels"])
    second_labels = _mapping(_mapping(second_payload["profile"])["labels"])

    assert first_labels == second_labels
    assert all(
        _mapping(item)["label"] is None
        for item in _sequence(first_labels["distribution"])
    )
    for private_label in (
        b"private-alias-a",
        b"private-alias-b",
        b"other-alias-x",
        b"other-alias-y",
    ):
        assert private_label not in cast(bytes, first_result.json_bytes)
        assert private_label not in cast(bytes, second_result.json_bytes)


def test_submicrosecond_timestamps_and_extreme_finite_numbers_stay_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataframe = make_banner_dataframe()
    dataframe.loc[:, "created_at"] = pd.Series(
        (
            "2099-04-01T00:00:00.000000001Z",
            "2099-04-01T00:00:00.000000002Z",
            "2099-04-01T00:00:00.000000003Z",
        ),
        dtype="string",
    )
    maximum = float.fromhex("0x1.fffffffffffffp+1023")
    dataframe.loc[:, "z_peak_acceleration_g"] = pd.Series(
        (maximum, -maximum, 5e-324), dtype="float64"
    )

    result, _port, _manifest = _run_synthetic(
        dataframe=dataframe,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    payload = _json_payload(result.json_bytes)
    profile = _mapping(payload["profile"])
    temporal = _mapping(profile["temporal"])
    statistics = _mapping(
        _profile_column(payload, "z_peak_acceleration_g")["numeric_statistics"]
    )

    assert result.status is BannerBaselineStatus.PASSED
    assert temporal["period_start_utc"] == "2099-04-01T00:00:00.000000001Z"
    assert temporal["period_end_utc"] == "2099-04-01T00:00:00.000000003Z"
    assert temporal["nominal_cadence_seconds"] is None
    assert statistics["finite_count"] == 3
    assert b"Infinity" not in cast(bytes, result.json_bytes)
    assert b"NaN" not in cast(bytes, result.json_bytes)
    assert all(
        _mapping(item)["passed"] is True
        for item in _sequence(payload["reconciliations"])
    )


def test_sanitizer_rejects_new_fields_raw_labels_paths_secrets_and_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _port, _manifest = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    original = _json_payload(result.json_bytes)

    mutations: list[tuple[Callable[[dict[str, object]], None], str]] = []

    def add_unclassified(payload: dict[str, object]) -> None:
        payload["middle_timestamp"] = "2099-04-01T00:01:00Z"

    mutations.append((add_unclassified, "2099-04-01T00:01:00Z"))

    def add_path(payload: dict[str, object]) -> None:
        _mapping(payload["source"])["basename"] = "C:\\Users\\private\\banner.csv"

    mutations.append((add_path, "C:\\Users\\private\\banner.csv"))

    def add_secret(payload: dict[str, object]) -> None:
        _mapping(payload["tooling"])["python"] = "token=private-secret-value"

    mutations.append((add_secret, "private-secret-value"))

    def add_control(payload: dict[str, object]) -> None:
        _mapping(payload["tooling"])["python"] = "3.13.5\u0007"

    mutations.append((add_control, "\u0007"))

    def add_raw_label(payload: dict[str, object]) -> None:
        labels = _mapping(_mapping(payload["profile"])["labels"])
        category = _mapping(_sequence(labels["distribution"])[0])
        category["label"] = "private-fault"

    mutations.append((add_raw_label, "private-fault"))

    def add_unexpected_column(payload: dict[str, object]) -> None:
        contract = _mapping(payload["contract_report"])
        contract["blocking_violation_count"] = 1
        contract["blocking_violations"] = [
            {
                "code": "contract.column_extra",
                "severity": "error",
                "column": "private_sensor",
            }
        ]

    mutations.append((add_unexpected_column, "private_sensor"))

    for mutation, forbidden in mutations:
        payload = cast(dict[str, object], json.loads(_canonical_json(original)))
        mutation(payload)
        with pytest.raises(BannerBaselinePrivacyError) as raised:
            render_banner_baseline_markdown(_canonical_json(payload))
        assert forbidden not in str(raised.value)


def test_offline_validator_rejects_passed_contract_with_blocking_violations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    payload = _json_payload(result.json_bytes)
    contract = _mapping(payload["contract_report"])
    contract["passed"] = True
    contract["blocking_violation_count"] = 1
    contract["blocking_violations"] = [
        {
            "code": "contract.timestamp_format",
            "severity": "error",
            "column": "created_at",
        }
    ]
    json_path = result.output_directory / "baseline.v1.json"
    json_path.write_bytes(_canonical_json(payload))

    with pytest.raises(
        BannerBaselinePrivacyError,
        match="Public contract report is invalid",
    ):
        validate_banner_baseline_artifacts(
            json_path=json_path,
            markdown_path=result.output_directory / "summary.md",
            manifest_path=manifest_path,
        )


@pytest.mark.parametrize(
    "mutation",
    ("noncanonical_code", "noncanonical_severity", "statistical_finding"),
)
def test_offline_validator_rejects_noncanonical_contract_issues(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    payload = _json_payload(result.json_bytes)
    contract = _mapping(payload["contract_report"])
    canary = "synthetic.canary"
    if mutation == "statistical_finding":
        contract["statistical_finding_count"] = 1
        contract["statistical_findings"] = [
            {
                "code": canary,
                "severity": "warning",
                "column": "created_at",
            }
        ]
    else:
        contract["passed"] = False
        contract["blocking_violation_count"] = 1
        contract["blocking_violations"] = [
            {
                "code": (
                    canary
                    if mutation == "noncanonical_code"
                    else "contract.timestamp_format"
                ),
                "severity": (
                    "warning" if mutation == "noncanonical_severity" else "error"
                ),
                "column": "created_at",
            }
        ]
    json_path = result.output_directory / "baseline.v1.json"
    json_path.write_bytes(_canonical_json(payload))

    with pytest.raises(BannerBaselinePrivacyError) as raised:
        validate_banner_baseline_artifacts(
            json_path=json_path,
            markdown_path=result.output_directory / "summary.md",
            manifest_path=manifest_path,
        )
    assert canary not in str(raised.value)


@pytest.mark.parametrize("mutation", ("null", "altered"))
def test_offline_validator_rejects_missing_or_altered_profile_definitions(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    payload = _json_payload(result.json_bytes)
    profile = _mapping(payload["profile"])
    if mutation == "null":
        profile["definitions"] = None
    else:
        definitions = _mapping(profile["definitions"])
        definitions["quantile_method"] = "synthetic_altered_definition"
    json_path = result.output_directory / "baseline.v1.json"
    json_path.write_bytes(_canonical_json(payload))

    with pytest.raises(BannerBaselinePrivacyError):
        validate_banner_baseline_artifacts(
            json_path=json_path,
            markdown_path=result.output_directory / "summary.md",
            manifest_path=manifest_path,
        )


@pytest.mark.parametrize(
    "mutation",
    ("period", "input_order", "logical_type", "pair_relation", "tooling"),
)
def test_offline_validator_rejects_arbitrary_public_text_carriers(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.output_directory is not None
    assert result.markdown_bytes is not None
    payload = _json_payload(result.json_bytes)
    profile = _mapping(payload["profile"])
    temporal = _mapping(profile["temporal"])
    canary = "synthetic-private-canary"
    markdown_bytes = result.markdown_bytes
    if mutation == "period":
        original = temporal["period_start_utc"]
        assert isinstance(original, str)
        temporal["period_start_utc"] = canary
        markdown_bytes = markdown_bytes.replace(
            original.encode("utf-8"),
            canary.encode("utf-8"),
        )
        assert canary.encode("utf-8") in markdown_bytes
    elif mutation == "input_order":
        temporal["input_order"] = canary
    elif mutation == "logical_type":
        _mapping(_sequence(profile["columns"])[0])["logical_type"] = canary
    elif mutation == "pair_relation":
        _mapping(_sequence(profile["redundant_unit_pairs"])[0])["relation"] = canary
    else:
        _mapping(payload["tooling"])["python"] = canary

    json_path = result.output_directory / "baseline.v1.json"
    markdown_path = result.output_directory / "summary.md"
    json_path.write_bytes(_canonical_json(payload))
    markdown_path.write_bytes(markdown_bytes)

    with pytest.raises(BannerBaselinePrivacyError) as raised:
        validate_banner_baseline_artifacts(
            json_path=json_path,
            markdown_path=markdown_path,
            manifest_path=manifest_path,
        )
    assert canary not in str(raised.value)


def test_disclosure_classification_and_new_indicator_fail_closed() -> None:
    unsafe = replace(
        PUBLIC_BANNER_BASELINE_SCHEMA,
        classification=ProfileFieldClassification.ROW_LEVEL,
    )

    with pytest.raises(BannerBaselinePrivacyError):
        validate_public_baseline_schema(unsafe)


def test_runner_does_not_discover_or_open_protected_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_discovery(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("the baseline runner must not discover local materials")

    monkeypatch.setattr(Path, "glob", reject_discovery)
    monkeypatch.setattr(Path, "rglob", reject_discovery)
    result, port, manifest_path = _run_synthetic(
        dataframe=make_banner_dataframe(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    source_path = tmp_path / _SYNTHETIC_SOURCE_NAME
    assert not source_path.exists()
    assert result.status is BannerBaselineStatus.PASSED
    assert port.input_paths == [source_path, source_path]
    assert port.manifest_paths == [manifest_path, manifest_path]
    assert all(path.suffix.casefold() != ".pdf" for path in port.input_paths)


def test_tracked_banner_baseline_is_valid_offline_against_manifest() -> None:
    repository_root = Path(__file__).parents[3]
    manifest_path = repository_root / "data" / "source-manifest.json"
    manifest_payload: object = json.loads(manifest_path.read_bytes())
    manifest = _mapping(manifest_payload)
    matches: list[dict[str, object]] = []
    for item in _sequence(manifest["files"]):
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        if candidate.get("name") == _SYNTHETIC_SOURCE_NAME:
            matches.append(candidate)
    assert len(matches) == 1
    source_sha256 = matches[0]["sha256"]
    assert isinstance(source_sha256, str)
    artifact_directory = (
        repository_root / "data" / "baselines" / "banner" / source_sha256
    )

    validate_banner_baseline_artifacts(
        json_path=artifact_directory / "baseline.v1.json",
        markdown_path=artifact_directory / "summary.md",
        manifest_path=manifest_path,
    )
