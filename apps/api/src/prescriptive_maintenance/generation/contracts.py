"""Provider-neutral contracts for evidence-grounded prescription generation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GENERATION_CONTRACT_VERSION: Final = "prescriptive-generation.v1"
EVIDENCE_GAP_WARNING_CODE: Final = "evidence_gap"
EVIDENCE_CONFLICT_WARNING_CODE: Final = "evidence_conflict"
MAX_EVIDENCE_ITEMS: Final = 12
MAX_EVIDENCE_CONTENT_CHARACTERS: Final = 4_000
MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS: Final = 24_000

type Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
type ShortText = Annotated[str, Field(min_length=1, max_length=512)]
type DiagnosticText = Annotated[str, Field(min_length=1, max_length=2_000)]
type NarrativeText = Annotated[str, Field(min_length=1, max_length=4_000)]
type EvidenceText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_EVIDENCE_CONTENT_CHARACTERS),
]


class _StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must contain at least one visible character.")
    return value


def _citation_ids_are_unique(citations: tuple[Citation, ...]) -> bool:
    identifiers = tuple(citation.evidence_id for citation in citations)
    return len(identifiers) == len(set(identifiers))


class Evidence(_StrictContractModel):
    """One authorized excerpt and its caller-supplied provenance."""

    evidence_id: Identifier
    source_id: Identifier
    locator: ShortText
    content: EvidenceText

    @field_validator("locator", "content")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        return _require_non_blank(value)


class Diagnosis(_StrictContractModel):
    """Immutable diagnosis produced upstream by the diagnostic model."""

    fault_code: Identifier
    technical_summary: DiagnosticText

    @field_validator("technical_summary")
    @classmethod
    def validate_technical_summary(cls, value: str) -> str:
        return _require_non_blank(value)


class GenerationRequest(_StrictContractModel):
    """Upstream diagnosis plus evidence supplied by a retrieval boundary."""

    diagnosis: Diagnosis
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_budget(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.evidence)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Evidence identifiers must be unique.")
        if len(self.evidence) > MAX_EVIDENCE_ITEMS:
            raise ValueError("Evidence count exceeds the generation budget.")
        if (
            sum(len(item.content) for item in self.evidence)
            > MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS
        ):
            raise ValueError("Evidence content exceeds the generation budget.")
        return self


class Citation(_StrictContractModel):
    """Reference to one supplied evidence item, never free-form provenance."""

    evidence_id: Identifier


class DiagnosticSupportStatus(StrEnum):
    """Whether supplied documents support the immutable model diagnosis."""

    SUPPORTED = "supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DiagnosticSupport(_StrictContractModel):
    """Documentary support assessment that cannot replace the diagnosis."""

    fault_code: Identifier
    status: DiagnosticSupportStatus
    assessment: NarrativeText | None
    citations: tuple[Citation, ...] = ()

    @field_validator("assessment")
    @classmethod
    def validate_assessment(cls, value: str | None) -> str | None:
        return None if value is None else _require_non_blank(value)

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if not _citation_ids_are_unique(self.citations):
            raise ValueError("Diagnosis citations must be unique.")
        if self.status is DiagnosticSupportStatus.SUPPORTED:
            if self.assessment is None or not self.citations:
                raise ValueError(
                    "Documentary support requires an assessment and citations."
                )
        elif self.assessment is not None or self.citations:
            raise ValueError(
                "Insufficient evidence cannot contain an assessment or citations."
            )
        return self


class Prescription(_StrictContractModel):
    """One evidence-grounded maintenance action and its rationale."""

    action: NarrativeText
    rationale: NarrativeText
    citations: tuple[Citation, ...]

    @field_validator("action", "rationale")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        return _require_non_blank(value)

    @model_validator(mode="after")
    def validate_citations(self) -> Self:
        if not self.citations:
            raise ValueError("A prescription requires at least one citation.")
        if not _citation_ids_are_unique(self.citations):
            raise ValueError("Prescription citations must be unique.")
        return self


class GenerationWarning(_StrictContractModel):
    """Bounded warning that can optionally reference supplied evidence."""

    code: Identifier
    message: ShortText
    citations: tuple[Citation, ...] = ()

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _require_non_blank(value)

    @model_validator(mode="after")
    def validate_citations(self) -> Self:
        if not _citation_ids_are_unique(self.citations):
            raise ValueError("Warning citations must be unique.")
        return self


class ProviderOutput(_StrictContractModel):
    """Strict JSON shape accepted from every generation provider."""

    schema_version: Literal["prescriptive-generation.v1"]
    diagnostic_support: DiagnosticSupport
    prescriptions: tuple[Prescription, ...]
    warnings: tuple[GenerationWarning, ...]

    @model_validator(mode="after")
    def validate_evidence_limitation_shape(self) -> Self:
        warning_codes = {warning.code for warning in self.warnings}
        evidence_limitation_codes = warning_codes.intersection(
            {EVIDENCE_GAP_WARNING_CODE, EVIDENCE_CONFLICT_WARNING_CODE}
        )
        if self.diagnostic_support.status is DiagnosticSupportStatus.SUPPORTED:
            if evidence_limitation_codes:
                raise ValueError(
                    "Supported diagnosis cannot contain evidence limitation warnings."
                )
            if not self.prescriptions:
                raise ValueError(
                    "A supported diagnosis requires at least one prescription."
                )
            return self

        if self.prescriptions:
            raise ValueError(
                "Insufficient evidence cannot produce maintenance prescriptions."
            )
        if not evidence_limitation_codes:
            raise ValueError(
                "Insufficient evidence requires an evidence limitation warning."
            )
        return self


class ProviderUsage(_StrictContractModel):
    """Allowlisted, provider-neutral usage counters."""

    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("Provider usage total must match its token counters.")
        return self


class GenerationStatus(StrEnum):
    """Stable outcomes exposed by the generation boundary."""

    GENERATED = "generated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_EVIDENCE = "no_evidence"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_ERROR = "provider_error"
    INVALID_OUTPUT = "invalid_output"


class GenerationResult(_StrictContractModel):
    """Sanitized boundary result with no provider-specific fields."""

    schema_version: Literal["prescriptive-generation.v1"] = GENERATION_CONTRACT_VERSION
    status: GenerationStatus
    diagnosis: Diagnosis
    diagnostic_support: DiagnosticSupport | None = None
    prescriptions: tuple[Prescription, ...] = ()
    warnings: tuple[GenerationWarning, ...] = ()
    usage: ProviderUsage | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.status is GenerationStatus.GENERATED:
            if (
                self.diagnostic_support is None
                or self.diagnostic_support.status
                is not DiagnosticSupportStatus.SUPPORTED
                or not self.prescriptions
            ):
                raise ValueError("Generated results require grounded content.")
            return self

        if self.status is GenerationStatus.INSUFFICIENT_EVIDENCE:
            if (
                self.diagnostic_support is None
                or self.diagnostic_support.status
                is not DiagnosticSupportStatus.INSUFFICIENT_EVIDENCE
                or self.prescriptions
            ):
                raise ValueError(
                    "Insufficient-evidence results cannot contain prescriptions."
                )
            return self

        if (
            self.diagnostic_support is not None
            or self.prescriptions
            or self.usage is not None
        ):
            raise ValueError("Failure results cannot contain generated provider data.")
        if not self.warnings:
            raise ValueError("Failure results require a sanitized warning.")
        return self
