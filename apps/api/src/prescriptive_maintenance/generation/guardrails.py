"""Deterministic pre- and post-provider guardrails for governed RAG evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from unicodedata import category

from prescriptive_maintenance.generation.contracts import (
    Diagnosis,
    Evidence,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from prescriptive_maintenance.generation.provider import GenerationProvider
from prescriptive_maintenance.generation.service import generate_prescription
from prescriptive_maintenance.governed_retrieval import (
    GovernedRetrievalResult,
    GovernedRetrievalStatus,
)
from prescriptive_maintenance.knowledge_retrieval import RankedKnowledgeSnapshot


class RagGuardrailStatus(StrEnum):
    """Closed decision returned by the RAG guardrail boundary."""

    ACCEPTED = "accepted"
    REFUSED = "refused"


class RagRefusalCode(StrEnum):
    """Safe, stable reasons for refusing a generation attempt or result."""

    INVALID_DIAGNOSIS = "invalid_diagnosis"
    INVALID_RETRIEVAL = "invalid_retrieval"
    NO_EVIDENCE = "no_evidence"
    UNMAPPED_FAULT = "unmapped_fault"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    DIAGNOSIS_CONFLICT = "diagnosis_conflict"
    EVIDENCE_CONFLICT = "evidence_conflict"
    UNSAFE_EVIDENCE = "unsafe_evidence"
    STALE_EVIDENCE = "stale_evidence"
    CURRENTNESS_UNAVAILABLE = "currentness_unavailable"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_ERROR = "provider_error"
    INVALID_PROVIDER_OUTPUT = "invalid_provider_output"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


_REFUSAL_TEXT: dict[RagRefusalCode, tuple[str, str]] = {
    RagRefusalCode.INVALID_DIAGNOSIS: (
        "The diagnostic input is invalid.",
        "Run the diagnostic step again with a validated model result.",
    ),
    RagRefusalCode.INVALID_RETRIEVAL: (
        "The governed retrieval result is invalid.",
        "Repeat governed retrieval before attempting generation.",
    ),
    RagRefusalCode.NO_EVIDENCE: (
        "No eligible documentary evidence is available.",
        "Review approved documentation coverage before prescribing an action.",
    ),
    RagRefusalCode.UNMAPPED_FAULT: (
        "The diagnosed fault has no governed documentary mapping.",
        "Review the approved fault-to-document mapping.",
    ),
    RagRefusalCode.RETRIEVAL_UNAVAILABLE: (
        "Governed documentary retrieval is unavailable.",
        "Retry after the retrieval boundary is healthy.",
    ),
    RagRefusalCode.DIAGNOSIS_CONFLICT: (
        "The diagnosis and governed retrieval identify different faults.",
        "Repeat retrieval for the immutable diagnosed fault.",
    ),
    RagRefusalCode.EVIDENCE_CONFLICT: (
        "Documentary evidence has conflicting citation identities.",
        "Rebuild the governed evidence snapshot before generation.",
    ),
    RagRefusalCode.UNSAFE_EVIDENCE: (
        "Documentary evidence is blank or structurally unsafe.",
        "Reprocess and approve a structurally valid document version.",
    ),
    RagRefusalCode.STALE_EVIDENCE: (
        "Documentary evidence is no longer current.",
        "Repeat governed retrieval against the current approved versions.",
    ),
    RagRefusalCode.CURRENTNESS_UNAVAILABLE: (
        "Documentary currentness could not be verified.",
        "Retry after lifecycle and index validation are available.",
    ),
    RagRefusalCode.PROVIDER_DISABLED: (
        "The generation provider is disabled.",
        "Enable an approved provider configuration before retrying.",
    ),
    RagRefusalCode.PROVIDER_ERROR: (
        "The generation provider is unavailable.",
        "Retry after the provider boundary is healthy.",
    ),
    RagRefusalCode.INVALID_PROVIDER_OUTPUT: (
        "The generation provider returned an invalid result.",
        "Retry with the reviewed prompt and provider contract.",
    ),
    RagRefusalCode.INSUFFICIENT_EVIDENCE: (
        "The supplied documents do not support a safe prescription.",
        "Review documentation coverage or collect additional approved evidence.",
    ),
}


@dataclass(frozen=True, slots=True)
class RagRefusal:
    """Allowlisted refusal text with one useful next action."""

    code: RagRefusalCode
    reason: str
    next_action: str

    def __post_init__(self) -> None:
        if type(self.code) is not RagRefusalCode:
            raise ValueError("RAG refusal code is invalid.")
        expected = _REFUSAL_TEXT[self.code]
        if (
            type(self.reason) is not str
            or self.reason != expected[0]
            or type(self.next_action) is not str
            or self.next_action != expected[1]
        ):
            raise ValueError("RAG refusal text is invalid.")


@dataclass(frozen=True, slots=True)
class GuardedGenerationResult:
    """Either one grounded generation result or one sanitized refusal."""

    status: RagGuardrailStatus
    diagnosis: Diagnosis | None
    generation: GenerationResult | None
    refusal: RagRefusal | None

    def __post_init__(self) -> None:
        if type(self.status) is not RagGuardrailStatus:
            raise ValueError("RAG guardrail status is invalid.")
        diagnosis = _copy_diagnosis(self.diagnosis)
        object.__setattr__(self, "diagnosis", diagnosis)

        if self.status is RagGuardrailStatus.ACCEPTED:
            if (
                diagnosis is None
                or type(self.generation) is not GenerationResult
                or self.generation.status is not GenerationStatus.GENERATED
                or self.generation.diagnosis != diagnosis
                or self.refusal is not None
            ):
                raise ValueError("Accepted RAG generation result is invalid.")
            return
        if self.generation is not None or type(self.refusal) is not RagRefusal:
            raise ValueError("Refused RAG generation result is invalid.")


class SnapshotCurrentnessPort(Protocol):
    """Revalidate exact SEN-57 snapshots without another ranking operation."""

    def snapshots_are_current(
        self,
        *,
        fault_class: str,
        policy_schema_version: int,
        policy_version: str,
        minimum_score: float,
        policy_sha256: str,
        mapping_version: str,
        mapping_sha256: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> bool | None: ...


class RagGuardrailService:
    """Refuse unsafe inputs and validate grounded output around one provider call."""

    def __init__(
        self,
        *,
        provider: GenerationProvider,
        snapshot_currentness: SnapshotCurrentnessPort,
    ) -> None:
        self._provider = provider
        self._snapshot_currentness = snapshot_currentness

    def generate(
        self,
        *,
        diagnosis: object,
        retrieval: object,
    ) -> GuardedGenerationResult:
        """Run deterministic gates without exposing documents or raw provider data."""

        clean_diagnosis = _copy_diagnosis(diagnosis)
        if clean_diagnosis is None:
            return _refused(None, RagRefusalCode.INVALID_DIAGNOSIS)

        clean_retrieval = _copy_retrieval(retrieval)
        if clean_retrieval is None:
            return _refused(clean_diagnosis, RagRefusalCode.INVALID_RETRIEVAL)
        if clean_retrieval.status is GovernedRetrievalStatus.NO_EVIDENCE:
            return _refused(clean_diagnosis, RagRefusalCode.NO_EVIDENCE)
        if clean_retrieval.status is GovernedRetrievalStatus.UNMAPPED_FAULT:
            return _refused(clean_diagnosis, RagRefusalCode.UNMAPPED_FAULT)
        if clean_retrieval.status is GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE:
            return _refused(clean_diagnosis, RagRefusalCode.RETRIEVAL_UNAVAILABLE)
        if (
            clean_retrieval.status is not GovernedRetrievalStatus.EVIDENCE
            or clean_retrieval.fault_class != clean_diagnosis.fault_code
        ):
            return _refused(clean_diagnosis, RagRefusalCode.DIAGNOSIS_CONFLICT)

        request, refusal_code = _generation_request(
            clean_diagnosis,
            clean_retrieval.evidence,
        )
        if refusal_code is not None:
            return _refused(clean_diagnosis, refusal_code)
        if request is None:
            return _refused(clean_diagnosis, RagRefusalCode.INVALID_RETRIEVAL)

        currentness_method = _currentness_method(self._snapshot_currentness)
        if currentness_method is None:
            return _refused(clean_diagnosis, RagRefusalCode.CURRENTNESS_UNAVAILABLE)
        current = _snapshots_are_current(
            currentness_method,
            clean_retrieval,
        )
        if current is None:
            return _refused(clean_diagnosis, RagRefusalCode.CURRENTNESS_UNAVAILABLE)
        if not current:
            return _refused(clean_diagnosis, RagRefusalCode.STALE_EVIDENCE)

        try:
            generated = cast(
                object,
                generate_prescription(request, self._provider),
            )
        except Exception:
            return _refused(clean_diagnosis, RagRefusalCode.PROVIDER_ERROR)
        if type(generated) is not GenerationResult:
            return _refused(clean_diagnosis, RagRefusalCode.INVALID_PROVIDER_OUTPUT)

        if generated.status is GenerationStatus.GENERATED:
            current = _snapshots_are_current(
                currentness_method,
                clean_retrieval,
            )
            if current is None:
                return _refused(
                    clean_diagnosis,
                    RagRefusalCode.CURRENTNESS_UNAVAILABLE,
                )
            if not current:
                return _refused(clean_diagnosis, RagRefusalCode.STALE_EVIDENCE)
            return GuardedGenerationResult(
                status=RagGuardrailStatus.ACCEPTED,
                diagnosis=clean_diagnosis,
                generation=generated,
                refusal=None,
            )
        return _refused(
            clean_diagnosis,
            _refusal_for_generation_status(generated.status),
        )


def _copy_diagnosis(value: object) -> Diagnosis | None:
    try:
        if type(value) is not Diagnosis:
            return None
        return Diagnosis(
            fault_code=value.fault_code,
            technical_summary=value.technical_summary,
        )
    except Exception:
        return None


def _copy_retrieval(value: object) -> GovernedRetrievalResult | None:
    try:
        if type(value) is not GovernedRetrievalResult:
            return None
        return GovernedRetrievalResult(
            status=value.status,
            fault_class=value.fault_class,
            policy_schema_version=value.policy_schema_version,
            policy_version=value.policy_version,
            minimum_score=value.minimum_score,
            policy_sha256=value.policy_sha256,
            mapping_version=value.mapping_version,
            mapping_sha256=value.mapping_sha256,
            evidence=value.evidence,
        )
    except Exception:
        return None


def _generation_request(
    diagnosis: Diagnosis,
    snapshots: tuple[RankedKnowledgeSnapshot, ...],
) -> tuple[GenerationRequest | None, RagRefusalCode | None]:
    try:
        if not snapshots:
            return None, RagRefusalCode.NO_EVIDENCE
        evidence_ids: set[str] = set()
        evidence: list[Evidence] = []
        for snapshot in snapshots:
            if type(snapshot.content) is not str or not snapshot.content.strip():
                return None, RagRefusalCode.UNSAFE_EVIDENCE
            if _contains_unsafe_structure(snapshot.content):
                return None, RagRefusalCode.UNSAFE_EVIDENCE
            if snapshot.chunk_id in evidence_ids:
                return None, RagRefusalCode.EVIDENCE_CONFLICT
            evidence_ids.add(snapshot.chunk_id)
            evidence.append(
                Evidence(
                    evidence_id=snapshot.chunk_id,
                    source_id=snapshot.document_id,
                    locator=(
                        f"{snapshot.document_version}:page={snapshot.page_number}:"
                        f"section={snapshot.section_id}:chunk={snapshot.chunk_id}"
                    ),
                    content=snapshot.content,
                )
            )
        return (
            GenerationRequest(
                diagnosis=diagnosis,
                evidence=tuple(evidence),
            ),
            None,
        )
    except Exception:
        return None, RagRefusalCode.INVALID_RETRIEVAL


def _contains_unsafe_structure(content: str) -> bool:
    try:
        content.encode("utf-8", errors="strict")
    except Exception:
        return True
    for character in content:
        codepoint = ord(character)
        unicode_category = category(character)
        if unicode_category in {"Cs", "Cf"}:
            return True
        if unicode_category == "Cc" and character not in {"\t", "\n", "\r"}:
            return True
        if 0xFDD0 <= codepoint <= 0xFDEF or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}:
            return True
    return False


def _currentness_method(value: object) -> Callable[..., object] | None:
    try:
        method = cast(SnapshotCurrentnessPort, value).snapshots_are_current
    except Exception:
        return None
    return method if callable(method) else None


def _snapshots_are_current(
    method: Callable[..., object],
    retrieval: GovernedRetrievalResult,
) -> bool | None:
    fault_class = retrieval.fault_class
    mapping_version = retrieval.mapping_version
    mapping_sha256 = retrieval.mapping_sha256
    if fault_class is None or mapping_version is None or mapping_sha256 is None:
        return None
    try:
        copied = tuple(
            RankedKnowledgeSnapshot(
                document_id=item.document_id,
                document_version=item.document_version,
                chunk_id=item.chunk_id,
                page_number=item.page_number,
                section_id=item.section_id,
                content=item.content,
                content_sha256=item.content_sha256,
                score=item.score,
            )
            for item in retrieval.evidence
        )
        result = method(
            fault_class=fault_class,
            policy_schema_version=retrieval.policy_schema_version,
            policy_version=retrieval.policy_version,
            minimum_score=retrieval.minimum_score,
            policy_sha256=retrieval.policy_sha256,
            mapping_version=mapping_version,
            mapping_sha256=mapping_sha256,
            evidence=copied,
        )
    except Exception:
        return None
    return result if type(result) is bool else None


def _refusal_for_generation_status(status: object) -> RagRefusalCode:
    if status is GenerationStatus.INSUFFICIENT_EVIDENCE:
        return RagRefusalCode.INSUFFICIENT_EVIDENCE
    if status is GenerationStatus.PROVIDER_DISABLED:
        return RagRefusalCode.PROVIDER_DISABLED
    if status is GenerationStatus.PROVIDER_ERROR:
        return RagRefusalCode.PROVIDER_ERROR
    if status is GenerationStatus.INVALID_OUTPUT:
        return RagRefusalCode.INVALID_PROVIDER_OUTPUT
    return RagRefusalCode.INVALID_PROVIDER_OUTPUT


def _refused(
    diagnosis: Diagnosis | None,
    code: RagRefusalCode,
) -> GuardedGenerationResult:
    reason, next_action = _REFUSAL_TEXT[code]
    return GuardedGenerationResult(
        status=RagGuardrailStatus.REFUSED,
        diagnosis=diagnosis,
        generation=None,
        refusal=RagRefusal(
            code=code,
            reason=reason,
            next_action=next_action,
        ),
    )
