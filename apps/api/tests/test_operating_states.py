"""Synthetic tests for the closed operating-state policy."""

from __future__ import annotations

import pytest
from prescriptive_maintenance.operating_states import (
    OPERATING_STATE_POLICY_SCHEMA_VERSION,
    OPERATING_STATE_POLICY_VERSION,
    OPERATING_STATES,
    OperatingState,
    operating_state_policy_payload,
    resolve_operating_state,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("normal", OperatingState.NORMAL),
        ("baseline", OperatingState.BASELINE),
        ("teste", OperatingState.TEST),
        ("acelerando", OperatingState.ACCELERATING),
        ("motor_desligado", OperatingState.MOTOR_OFF),
        ("NÓRMAL", OperatingState.NORMAL),
        ("BÁSELÍNE", OperatingState.BASELINE),
        ("TÉSTE", OperatingState.TEST),
        ("ACELERÁNDO", OperatingState.ACCELERATING),
        ("MÓTOR-DESLIGÁDO", OperatingState.MOTOR_OFF),
    ),
)
def test_policy_resolves_only_canonical_whole_values(
    value: str,
    expected: OperatingState,
) -> None:
    assert resolve_operating_state(value) is expected


@pytest.mark.parametrize(
    "value",
    (
        "synthetic_teste_condition",
        "normal-teste",
        "motor-_desligado",
        "motor desligado",
        " normal",
        "normal ",
        "",
        None,
    ),
)
def test_policy_fails_closed_for_near_collisions_and_ambiguity(
    value: object,
) -> None:
    assert resolve_operating_state(value) is None


def test_policy_payload_is_exact_and_versioned() -> None:
    assert tuple(OperatingState) == OPERATING_STATES
    assert operating_state_policy_payload() == {
        "schema_version": OPERATING_STATE_POLICY_SCHEMA_VERSION,
        "policy_version": OPERATING_STATE_POLICY_VERSION,
        "canonical_states": [
            "normal",
            "baseline",
            "teste",
            "acelerando",
            "motor_desligado",
        ],
        "normalization": {
            "case": "lowercase",
            "accents": "unicode_nfd_remove_combining_marks",
            "separator": "hyphen_to_underscore",
        },
        "matching": "whole_value_only",
    }
