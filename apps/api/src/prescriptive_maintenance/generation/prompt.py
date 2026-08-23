"""Load the reviewed prompt and build deterministic provider requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Final

from prescriptive_maintenance.generation.contracts import (
    GENERATION_CONTRACT_VERSION,
    GenerationRequest,
    ProviderOutput,
)
from prescriptive_maintenance.generation.provider import ProviderRequest

GENERATION_SYSTEM_PROMPT_VERSION: Final = "prescriptive-generation-system.v1"
_PROMPT_FILENAME: Final = "prescription_system.v1.txt"


@dataclass(frozen=True, slots=True)
class VersionedPrompt:
    """Reviewed prompt text bound to an explicit stable identifier."""

    version: str
    text: str


def _load_system_prompt() -> VersionedPrompt:
    prompt_text = (
        files("prescriptive_maintenance.generation.prompts")
        .joinpath(_PROMPT_FILENAME)
        .read_text(encoding="utf-8")
    )
    if GENERATION_SYSTEM_PROMPT_VERSION not in prompt_text:
        raise RuntimeError("Versioned generation prompt has an invalid identifier.")
    return VersionedPrompt(
        version=GENERATION_SYSTEM_PROMPT_VERSION,
        text=prompt_text,
    )


GENERATION_SYSTEM_PROMPT: Final = _load_system_prompt()


def build_provider_request(request: GenerationRequest) -> ProviderRequest:
    """Serialize evidence and the strict output schema without provider details."""

    ordered_evidence = tuple(
        sorted(request.evidence, key=lambda evidence: evidence.evidence_id)
    )
    payload = {
        "contract_version": GENERATION_CONTRACT_VERSION,
        "diagnosis": request.diagnosis.model_dump(mode="json", round_trip=True),
        "evidence": [
            evidence.model_dump(mode="json", round_trip=True)
            for evidence in ordered_evidence
        ],
        "output_schema": ProviderOutput.model_json_schema(mode="validation"),
    }
    input_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return ProviderRequest(
        prompt_version=GENERATION_SYSTEM_PROMPT.version,
        system_prompt=GENERATION_SYSTEM_PROMPT.text,
        input_json=input_json,
        diagnosis_fault_code=request.diagnosis.fault_code,
        allowed_evidence_ids=tuple(
            evidence.evidence_id for evidence in ordered_evidence
        ),
    )
