"""Provider port, sanitized errors, and deterministic offline adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from prescriptive_maintenance.generation.contracts import (
    EVIDENCE_GAP_WARNING_CODE,
    GENERATION_CONTRACT_VERSION,
    ProviderUsage,
)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Provider-neutral prompt and JSON payload."""

    prompt_version: str
    system_prompt: str
    input_json: str
    diagnosis_fault_code: str
    allowed_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Raw provider text plus allowlisted usage counters only."""

    output_text: str
    usage: ProviderUsage | None = None


class GenerationProvider(Protocol):
    """Port implemented by interchangeable text-generation providers."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate one raw response without entering the domain directly."""

        ...


class GenerationProviderError(RuntimeError):
    """Sanitized base error for provider failures."""


class ProviderDisabledError(GenerationProviderError):
    """The configured provider is intentionally disabled."""


class ProviderConfigurationError(GenerationProviderError):
    """The provider cannot run with its explicit local configuration."""


class ProviderExecutionError(GenerationProviderError):
    """The provider failed before returning a response."""


class ProviderInvalidResponseError(GenerationProviderError):
    """The provider returned an unusable transport envelope."""


class InvalidProviderOutputError(ValueError):
    """Provider text does not satisfy the versioned domain contract."""


class FakeGenerationProvider:
    """Deterministic, credential-free provider for tests and CI."""

    def __init__(
        self,
        *,
        response_text: str | None = None,
        usage: ProviderUsage | None = None,
        error: GenerationProviderError | None = None,
    ) -> None:
        self._response_text = response_text
        self._usage = usage or ProviderUsage(
            input_tokens=21,
            output_tokens=13,
            total_tokens=34,
        )
        self._error = error
        self._call_count = 0

    @property
    def call_count(self) -> int:
        """Return how many explicit generation attempts were made."""

        return self._call_count

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return stable synthetic JSON without files, network, or credentials."""

        self._call_count += 1
        if self._error is not None:
            raise self._error
        output_text = self._response_text or _fake_output_text(
            request.diagnosis_fault_code, request.allowed_evidence_ids
        )
        return ProviderResponse(output_text=output_text, usage=self._usage)


def _fake_output_text(
    diagnosis_fault_code: str,
    allowed_evidence_ids: tuple[str, ...],
) -> str:
    if not allowed_evidence_ids:
        payload: dict[str, object] = {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": diagnosis_fault_code,
                "status": "insufficient_evidence",
                "assessment": None,
                "citations": [],
            },
            "prescriptions": [],
            "warnings": [
                {
                    "code": EVIDENCE_GAP_WARNING_CODE,
                    "message": "No synthetic evidence was supplied.",
                    "citations": [],
                }
            ],
        }
    else:
        citation = {"evidence_id": allowed_evidence_ids[0]}
        payload = {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": diagnosis_fault_code,
                "status": "supported",
                "assessment": (
                    "Synthetic documents support the immutable test diagnosis."
                ),
                "citations": [citation],
            },
            "prescriptions": [
                {
                    "action": (
                        "Inspect the synthetic asset under controlled conditions."
                    ),
                    "rationale": (
                        "The supplied synthetic evidence warrants verification."
                    ),
                    "citations": [citation],
                }
            ],
            "warnings": [],
        }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
