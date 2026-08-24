"""Render or verify the deterministic API v1 OpenAPI snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

from prescriptive_maintenance.openapi import openapi_bytes

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT: Final = REPOSITORY_ROOT / "apps" / "api" / "openapi" / "v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Falha se o snapshot rastreado divergir do contrato atual.",
    )
    arguments = parser.parse_args()
    expected = openapi_bytes()

    if arguments.check:
        try:
            actual = OPENAPI_SNAPSHOT.read_bytes()
        except OSError:
            raise SystemExit("OpenAPI v1 snapshot is missing.") from None
        if actual != expected:
            raise SystemExit("OpenAPI v1 snapshot is stale.")
        return

    OPENAPI_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_SNAPSHOT.write_bytes(expected)


if __name__ == "__main__":
    main()
