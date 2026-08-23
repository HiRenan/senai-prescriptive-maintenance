"""Command-line entrypoint for the local canonical data pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from prescriptive_maintenance.data.canonical import (
    CanonicalCheckError,
    CanonicalConfigurationError,
    CanonicalContractError,
    CanonicalLabelError,
    CanonicalOutputError,
    CanonicalPartitionError,
    build_banner_dataset,
    check_canonical_dataset,
)
from prescriptive_maintenance.data.fault_labels import FaultLabelInventoryError
from prescriptive_maintenance.data.source import BannerSourceError

_EXIT_SOURCE = 3
_EXIT_CONTRACT = 4
_EXIT_PARTITION = 5
_EXIT_ARTIFACT = 6


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit build or read-only check and return a stable exit code."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            result = build_banner_dataset(
                input_path=arguments.input,
                manifest_path=arguments.manifest,
                inventory_path=arguments.inventory,
                baseline_json_path=arguments.baseline_json,
                baseline_markdown_path=arguments.baseline_markdown,
                lock_path=arguments.lock,
                output_directory=arguments.output,
            )
            payload = {
                "status": "passed",
                "dataset_id": result.dataset_id,
                "source_row_count": result.source_row_count,
                "canonical_row_count": result.canonical_row_count,
                "occurrence_count": result.occurrence_count,
                "disposition_counts": dict(result.disposition_counts),
                "destination_counts": dict(result.destination_counts),
                "partition_counts": dict(result.partition_counts),
                "artifact_sha256": dict(result.artifact_sha256),
            }
        else:
            checked = check_canonical_dataset(
                output_directory=arguments.output,
                lock_path=arguments.lock,
            )
            payload = {
                "status": "passed",
                "dataset_id": checked.dataset_id,
                "source_row_count": checked.source_row_count,
                "canonical_row_count": checked.canonical_row_count,
                "occurrence_count": checked.occurrence_count,
                "partition_counts": dict(checked.partition_counts),
                "artifact_sha256": dict(checked.artifact_sha256),
            }
    except (BannerSourceError, FaultLabelInventoryError) as error:
        return _failure(_EXIT_SOURCE, error)
    except (CanonicalContractError, CanonicalLabelError) as error:
        return _failure(_EXIT_CONTRACT, error)
    except CanonicalPartitionError as error:
        return _failure(_EXIT_PARTITION, error)
    except (
        CanonicalCheckError,
        CanonicalConfigurationError,
        CanonicalOutputError,
    ) as error:
        return _failure(_EXIT_ARTIFACT, error)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canonical-data",
        description="Constrói ou verifica artefatos locais do dataset canônico.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Constrói artefatos locais atomicamente.")
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--inventory", type=Path, required=True)
    build.add_argument("--baseline-json", type=Path, required=True)
    build.add_argument("--baseline-markdown", type=Path, required=True)
    build.add_argument("--lock", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check", help="Verifica artefatos sem reescrever.")
    check.add_argument("--lock", type=Path, required=True)
    check.add_argument("--output", type=Path, required=True)
    return parser


def _failure(exit_code: int, error: Exception) -> int:
    print(
        json.dumps(
            {"status": "blocked", "error": str(error)},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
