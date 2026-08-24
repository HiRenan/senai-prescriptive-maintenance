"""Load the reviewed prompt and build deterministic provider requests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from importlib.resources import files
from typing import Final

from prescriptive_maintenance.generation.contracts import (
    GENERATION_CONTRACT_VERSION,
    GenerationRequest,
    ProviderOutput,
)
from prescriptive_maintenance.generation.provider import ProviderRequest

GENERATION_SYSTEM_PROMPT_VERSION: Final = "prescriptive-generation-system.v2"
UNTRUSTED_DOCUMENT_ENVELOPE_VERSION: Final = "untrusted-document-envelope.v1"
_PROMPT_FILENAME: Final = "prescription_system.v2.txt"


@dataclass(frozen=True, slots=True)
class VersionedPrompt:
    """Reviewed prompt text bound to an explicit stable identifier."""

    version: str
    text: str = field(repr=False)


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
    """Serialize evidence inside deterministic untrusted-document envelopes."""

    ordered_evidence = tuple(
        sorted(request.evidence, key=lambda evidence: evidence.evidence_id)
    )
    payload = {
        "contract_version": GENERATION_CONTRACT_VERSION,
        "diagnosis": request.diagnosis.model_dump(mode="json", round_trip=True),
        "evidence": [
            _untrusted_evidence_payload(evidence) for evidence in ordered_evidence
        ],
        "output_schema": ProviderOutput.model_json_schema(mode="validation"),
    }
    input_json = json.dumps(
        payload,
        ensure_ascii=True,
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


def _untrusted_evidence_payload(evidence: object) -> dict[str, object]:
    from prescriptive_maintenance.generation.contracts import Evidence

    if type(evidence) is not Evidence:
        raise ValueError("Generation evidence is invalid.")
    content = evidence.content
    if type(content) is not str:
        raise ValueError("Generation evidence is invalid.")
    content_sha256 = sha256(content.encode("utf-8", errors="strict")).hexdigest()
    begin_sentinel, end_sentinel = _collision_free_sentinels(
        evidence.evidence_id,
        content,
    )
    return {
        "evidence_id": evidence.evidence_id,
        "locator": evidence.locator,
        "source_id": evidence.source_id,
        "untrusted_document": {
            "begin_sentinel": begin_sentinel,
            "content": content,
            "content_sha256": content_sha256,
            "encoding": "utf-8-json-string",
            "end_sentinel": end_sentinel,
            "schema_version": UNTRUSTED_DOCUMENT_ENVELOPE_VERSION,
            "trust": "untrusted",
        },
    }


def _collision_free_sentinels(
    evidence_id: str,
    content: str,
) -> tuple[str, str]:
    identity = sha256(evidence_id.encode("utf-8", errors="strict")).hexdigest()
    for counter in range(len(content) + 2):
        prefix = f"UNTRUSTED_DOCUMENT_{identity}_{counter}"
        begin = f"{prefix}_BEGIN"
        end = f"{prefix}_END"
        if begin not in content and end not in content:
            return begin, end
    raise ValueError("Generation evidence boundary could not be isolated.")
