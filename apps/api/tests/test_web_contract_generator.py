"""Verify the guardrails of the web contract generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPOSITORY_ROOT / "scripts" / "generate_web_contract.py"


def _load_generator() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "generate_web_contract",
        GENERATOR_PATH,
    )
    if specification is None or specification.loader is None:
        pytest.skip("O gerador do contrato web não está disponível.")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


generator = _load_generator()


@pytest.mark.parametrize(
    "pattern",
    [
        "^ana_[a-z0-9_]{3,64}$",
        "^model_[a-z0-9_.-]{3,64}$",
        "^[a-z0-9]+(?:_[a-z0-9]+)*$",
        r"^doc_[a-z0-9_]{3,64}\.json$",
    ],
)
def test_supported_patterns_are_preserved(pattern: str) -> None:
    node: dict[str, Any] = {"type": "string", "pattern": pattern}
    assert generator._string_pattern(node, "campo") == pattern


def test_absent_pattern_stays_absent() -> None:
    assert generator._string_pattern({"type": "string"}, "campo") is None


@pytest.mark.parametrize(
    ("pattern", "reason"),
    [
        ("ana_[a-z]{3}$", "ancorado"),
        ("^ana_[a-z]{3}", "ancorado"),
        ("^(?i)ana_[a-z]{3}$", "não suportada"),
        ("^(?P<id>ana_[a-z]{3})$", "não suportada"),
        ("^(?<=x)ana_$", "não suportada"),
        ("^[[:alpha:]]+$", "não suportada"),
        (r"^\pL+$", "não suportado"),
        (r"^(a)\1$", "não suportado"),
        # Shorthand classes read as Unicode in Python and as ASCII in
        # `RegExp`, so they never cross the contract unchanged.
        (r"^doc_\w{3,64}$", "não suportado"),
        (r"^ana_\d{3,64}$", "não suportado"),
        (r"^ana_[a-z]+\s[a-z]+$", "não suportado"),
        (r"^ana_\W+$", "não suportado"),
        (r"^ana_\D+$", "não suportado"),
        (r"^ana_\S+$", "não suportado"),
        ("^café_[a-z]+$", "não suportado"),
        ("^ana_[a-z$", "não é uma expressão válida"),
    ],
)
def test_unsupported_patterns_fail_generation(pattern: str, reason: str) -> None:
    node: dict[str, Any] = {"type": "string", "pattern": pattern}
    with pytest.raises(generator.ContractGenerationError) as error:
        generator._string_pattern(node, "campo")
    assert reason in str(error.value)


def test_published_contract_matches_the_tracked_modules() -> None:
    for path, expected in generator._rendered_modules():
        assert path.read_bytes() == expected, f"{path.name} está desatualizado."
