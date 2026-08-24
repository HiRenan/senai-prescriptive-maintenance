"""Versioned whole-value policy for operating-state labels."""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Final

OPERATING_STATE_POLICY_SCHEMA_VERSION: Final = 1
OPERATING_STATE_POLICY_VERSION: Final = "operating-states.v1"


class OperatingState(StrEnum):
    """Closed operating-state vocabulary declared by the challenge statement."""

    NORMAL = "normal"
    BASELINE = "baseline"
    TEST = "teste"
    ACCELERATING = "acelerando"
    MOTOR_OFF = "motor_desligado"


OPERATING_STATES: Final[tuple[OperatingState, ...]] = tuple(OperatingState)


def resolve_operating_state(value: object) -> OperatingState | None:
    """Resolve only an exact canonical value after bounded normalization."""

    if not isinstance(value, str):
        return None
    try:
        lowered = value.lower()
        decomposed = unicodedata.normalize("NFD", lowered)
    except (TypeError, ValueError):
        return None
    normalized = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    ).replace("-", "_")
    try:
        return OperatingState(normalized)
    except ValueError:
        return None


def operating_state_policy_payload() -> dict[str, object]:
    """Return the exact semantic policy bound into model artifacts."""

    return {
        "schema_version": OPERATING_STATE_POLICY_SCHEMA_VERSION,
        "policy_version": OPERATING_STATE_POLICY_VERSION,
        "canonical_states": [state.value for state in OPERATING_STATES],
        "normalization": {
            "case": "lowercase",
            "accents": "unicode_nfd_remove_combining_marks",
            "separator": "hyphen_to_underscore",
        },
        "matching": "whole_value_only",
    }
