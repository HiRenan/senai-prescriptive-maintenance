"""Deterministic OpenAPI rendering shared by generation and tests."""

from __future__ import annotations

import json

from prescriptive_maintenance.main import create_app


def openapi_bytes() -> bytes:
    """Return canonical UTF-8 JSON with stable indentation and LF termination."""

    payload = create_app().openapi()
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    return f"{rendered}\n".encode()
