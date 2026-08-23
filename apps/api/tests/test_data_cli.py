"""CLI proofs for sanitized canonical-pipeline results and exit codes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

import pytest
from prescriptive_maintenance.data import cli
from prescriptive_maintenance.data.canonical import (
    CanonicalBuildResult,
    CanonicalCheckError,
    CanonicalCheckResult,
    CanonicalConfigurationError,
    CanonicalContractError,
    CanonicalLabelError,
    CanonicalOutputError,
    CanonicalPartitionError,
)
from prescriptive_maintenance.data.fault_labels import FaultLabelInventoryError
from prescriptive_maintenance.data.source import SourceAccessError

_HASH: Final = "a" * 64


def _payload(text: str) -> dict[str, object]:
    value: object = json.loads(text)
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_build_command_prints_only_sanitized_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = CanonicalBuildResult(
        dataset_id=_HASH,
        output_directory=Path("ignored-local-output"),
        source_row_count=10,
        canonical_row_count=9,
        occurrence_count=4,
        disposition_counts={"kept": 9, "rejected": 1},
        destination_counts={"train": 6, "validation": 1, "test": 2, "purge": 0},
        partition_counts={"train": 6, "validation": 1, "test": 2},
        artifact_sha256={"canonical.parquet": _HASH},
    )

    def build(**_: object) -> CanonicalBuildResult:
        return result

    monkeypatch.setattr(cli, "build_banner_dataset", build)
    exit_code = cli.main(
        [
            "build",
            "--input",
            "private/banner.csv",
            "--manifest",
            "manifest.json",
            "--inventory",
            "inventory.json",
            "--baseline-json",
            "baseline.json",
            "--baseline-markdown",
            "baseline.md",
            "--lock",
            "uv.lock",
            "--output",
            "local-output",
        ]
    )

    captured = capsys.readouterr()
    payload = _payload(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["status"] == "passed"
    assert payload["dataset_id"] == _HASH
    assert "private" not in captured.out
    assert "target" not in captured.out


def test_check_command_prints_only_sanitized_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = CanonicalCheckResult(
        dataset_id=_HASH,
        source_row_count=10,
        canonical_row_count=9,
        occurrence_count=4,
        partition_counts={"train": 6, "validation": 1, "test": 2},
        artifact_sha256={"canonical.parquet": _HASH},
    )

    def check(**_: object) -> CanonicalCheckResult:
        return result

    monkeypatch.setattr(cli, "check_canonical_dataset", check)
    exit_code = cli.main(["check", "--lock", "uv.lock", "--output", "local"])

    captured = capsys.readouterr()
    payload = _payload(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["status"] == "passed"
    assert payload["partition_counts"] == {
        "test": 2,
        "train": 6,
        "validation": 1,
    }


@pytest.mark.parametrize(
    ("error_factory", "expected_exit"),
    (
        (SourceAccessError, 3),
        (FaultLabelInventoryError, 3),
        (CanonicalContractError, 4),
        (CanonicalLabelError, 4),
        (CanonicalPartitionError, 5),
        (CanonicalCheckError, 6),
        (CanonicalConfigurationError, 6),
        (CanonicalOutputError, 6),
    ),
)
def test_build_command_maps_sanitized_failures_to_stable_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_factory: Callable[[str], Exception],
    expected_exit: int,
) -> None:
    def fail(**_: object) -> CanonicalBuildResult:
        raise error_factory("sanitized failure")

    monkeypatch.setattr(cli, "build_banner_dataset", fail)
    exit_code = cli.main(
        [
            "build",
            "--input",
            "banner.csv",
            "--manifest",
            "manifest.json",
            "--inventory",
            "inventory.json",
            "--baseline-json",
            "baseline.json",
            "--baseline-markdown",
            "baseline.md",
            "--lock",
            "uv.lock",
            "--output",
            "local-output",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.out == ""
    assert _payload(captured.err) == {
        "error": "sanitized failure",
        "status": "blocked",
    }
