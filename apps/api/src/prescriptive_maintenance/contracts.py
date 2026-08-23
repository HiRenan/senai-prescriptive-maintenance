"""Public, versioned HTTP contracts for API v1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)

API_CONTRACT_VERSION: Final = "1.0.0"
DEFAULT_TOP_K: Final = 5
MAX_TOP_K: Final = 10
ANALYSIS_FEATURE_COUNT: Final = 18

AnalysisId = Annotated[
    str,
    StringConstraints(pattern=r"^ana_[a-z0-9_]{3,64}$"),
]
ModelId = Annotated[
    str,
    StringConstraints(pattern=r"^model_[a-z0-9_.-]{3,64}$"),
]
DocumentId = Annotated[
    str,
    StringConstraints(pattern=r"^doc_[a-z0-9_]{3,64}$"),
]
DocumentVersionRef = Annotated[
    str,
    StringConstraints(pattern=r"^docver_[a-z0-9_]{3,64}$"),
]
ChunkRef = Annotated[
    str,
    StringConstraints(pattern=r"^chunk_[a-z0-9_]{3,64}$"),
]
PdfFilename = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}\.[Pp][Dd][Ff]$"),
]
SupportScore = Annotated[
    float,
    Field(
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        description=(
            "Heurística agregada não calibrada; não representa probabilidade "
            "nem confiança estatística."
        ),
    ),
]


class ContractModel(BaseModel):
    """Reject undeclared fields and non-finite numbers at the HTTP boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )


class AnalysisFeatures(ContractModel):
    """The 18 ordered, metric model inputs frozen by API v1."""

    z_rms_velocity_mm_s: Annotated[float, Field(ge=0.0)]
    temperature_c: Annotated[float, Field(ge=-273.15)]
    x_rms_velocity_mm_s: Annotated[float, Field(ge=0.0)]
    z_peak_acceleration_g: float
    x_peak_acceleration_g: float
    z_peak_vel_comp_freq_hz: Annotated[float, Field(ge=0.0)]
    x_peak_vel_comp_freq_hz: Annotated[float, Field(ge=0.0)]
    z_rms_acceleration_g: Annotated[float, Field(ge=0.0)]
    x_rms_acceleration_g: Annotated[float, Field(ge=0.0)]
    z_kurtosis: float
    x_kurtosis: float
    z_crest_factor: float
    x_crest_factor: float
    z_peak_velocity_mm_s: float
    x_peak_velocity_mm_s: float
    z_high_freq_rms_accel_g: Annotated[float, Field(ge=0.0)]
    x_high_freq_rms_accel_g: Annotated[float, Field(ge=0.0)]
    rpm: float


ANALYSIS_FEATURE_NAMES: Final[tuple[str, ...]] = tuple(AnalysisFeatures.model_fields)

if len(ANALYSIS_FEATURE_NAMES) != ANALYSIS_FEATURE_COUNT:
    raise RuntimeError("API v1 must expose exactly 18 analysis features.")


class AnalysisRequest(ContractModel):
    """Request accepted by ``POST /analysis``."""

    features: AnalysisFeatures
    top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K)] = DEFAULT_TOP_K


class AnalysisOutcome(StrEnum):
    """Complete public outcome vocabulary for API v1."""

    NORMAL = "normal"
    DOCUMENTED_FAULT = "documented_fault"
    UNDOCUMENTED_FAULT = "undocumented_fault"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    DEGRADED = "degraded"


class Diagnosis(ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    summary: Annotated[str, Field(min_length=1, max_length=500)]


class SufficientSupport(ContractModel):
    level: Literal["sufficient"]
    support_score: SupportScore


class InsufficientSupport(ContractModel):
    level: Literal["insufficient"]
    support_score: SupportScore


class AbstentionReason(StrEnum):
    UNDOCUMENTED_FAULT = "undocumented_fault"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


class Abstention(ContractModel):
    reason: AbstentionReason
    message: Annotated[str, Field(min_length=1, max_length=500)]


class OpaqueNeighbor(ContractModel):
    """Ranked model neighbor without row, timestamp, measurement, or vector data."""

    neighbor_ref: Annotated[
        str,
        StringConstraints(pattern=r"^neighbor_[a-z0-9_]{3,64}$"),
    ]
    rank: Annotated[int, Field(ge=1, le=MAX_TOP_K)]
    fault_code: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=80,
            pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
        ),
    ]
    distance: Annotated[
        float,
        Field(
            ge=0.0,
            allow_inf_nan=False,
            description="Distância padronizada não negativa e sem limite superior.",
        ),
    ]


class PrescriptionPriority(StrEnum):
    ROUTINE = "routine"
    SCHEDULED = "scheduled"
    URGENT = "urgent"


class Prescription(ContractModel):
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    priority: PrescriptionPriority
    actions: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = Field(
        min_length=1,
        max_length=5,
    )


class Citation(ContractModel):
    """Auditable opaque location without source paths or raw document text."""

    document_id: DocumentId
    document_version: DocumentVersionRef
    chunk: ChunkRef
    title: Annotated[str, Field(min_length=1, max_length=200)]
    locator: Annotated[str, Field(min_length=1, max_length=200)]


class AnalysisWarning(ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class NormalAnalysisResult(ContractModel):
    analysis_id: AnalysisId
    outcome: Literal[AnalysisOutcome.NORMAL]
    diagnosis: Diagnosis
    support: SufficientSupport
    abstention: None = Field(...)
    model_id: ModelId
    neighbors: tuple[OpaqueNeighbor, ...] = Field(max_length=MAX_TOP_K)
    prescription: None = Field(...)
    citations: tuple[Citation, ...] = Field(max_length=0)
    warnings: tuple[AnalysisWarning, ...]


class DocumentedFaultAnalysisResult(ContractModel):
    analysis_id: AnalysisId
    outcome: Literal[AnalysisOutcome.DOCUMENTED_FAULT]
    diagnosis: Diagnosis
    support: SufficientSupport
    abstention: None = Field(...)
    model_id: ModelId
    neighbors: tuple[OpaqueNeighbor, ...] = Field(
        min_length=1,
        max_length=MAX_TOP_K,
    )
    prescription: Prescription
    citations: tuple[Citation, ...] = Field(
        min_length=1,
        max_length=MAX_TOP_K,
    )
    warnings: tuple[AnalysisWarning, ...]


class UndocumentedFaultAnalysisResult(ContractModel):
    analysis_id: AnalysisId
    outcome: Literal[AnalysisOutcome.UNDOCUMENTED_FAULT]
    diagnosis: Diagnosis
    support: SufficientSupport
    abstention: Abstention
    model_id: ModelId
    neighbors: tuple[OpaqueNeighbor, ...] = Field(
        min_length=1,
        max_length=MAX_TOP_K,
    )
    prescription: None = Field(...)
    citations: tuple[Citation, ...] = Field(max_length=0)
    warnings: tuple[AnalysisWarning, ...] = Field(min_length=1)


class OutOfDistributionAnalysisResult(ContractModel):
    analysis_id: AnalysisId
    outcome: Literal[AnalysisOutcome.OUT_OF_DISTRIBUTION]
    diagnosis: None = Field(...)
    support: InsufficientSupport
    abstention: Abstention
    model_id: ModelId
    neighbors: tuple[OpaqueNeighbor, ...] = Field(max_length=MAX_TOP_K)
    prescription: None = Field(...)
    citations: tuple[Citation, ...] = Field(max_length=0)
    warnings: tuple[AnalysisWarning, ...] = Field(min_length=1)


class DegradedAnalysisResult(ContractModel):
    analysis_id: AnalysisId
    outcome: Literal[AnalysisOutcome.DEGRADED]
    diagnosis: Diagnosis
    support: SufficientSupport
    abstention: Abstention
    model_id: ModelId
    neighbors: tuple[OpaqueNeighbor, ...] = Field(max_length=MAX_TOP_K)
    prescription: None = Field(...)
    citations: tuple[Citation, ...] = Field(max_length=MAX_TOP_K)
    warnings: tuple[AnalysisWarning, ...] = Field(min_length=1)


AnalysisResult = Annotated[
    NormalAnalysisResult
    | DocumentedFaultAnalysisResult
    | UndocumentedFaultAnalysisResult
    | OutOfDistributionAnalysisResult
    | DegradedAnalysisResult,
    Field(discriminator="outcome"),
]


class AnalysisResponse(RootModel[AnalysisResult]):
    """Named discriminated union consumed directly by generated clients."""


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class RegisterDocumentRequest(ContractModel):
    filename: PdfFilename
    media_type: Literal["application/pdf"]
    size_bytes: Annotated[int, Field(ge=1, le=25_000_000)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ApproveDocumentRequest(ContractModel):
    note: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class RejectDocumentRequest(ContractModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class DocumentFailure(ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class DocumentBase(ContractModel):
    document_id: DocumentId
    filename: PdfFilename
    media_type: Literal["application/pdf"]
    size_bytes: Annotated[int, Field(ge=1, le=25_000_000)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.created_at.utcoffset() is None or self.updated_at.utcoffset() is None:
            raise ValueError("Document timestamps must be timezone-aware.")
        if self.updated_at < self.created_at:
            raise ValueError("Document updated_at cannot precede created_at.")
        return self


class ReceivedDocument(DocumentBase):
    status: Literal[DocumentStatus.RECEIVED]
    decision_note: None = Field(...)
    failure: None = Field(...)
    superseded_by_document_id: None = Field(...)


class ProcessingDocument(DocumentBase):
    status: Literal[DocumentStatus.PROCESSING]
    decision_note: None = Field(...)
    failure: None = Field(...)
    superseded_by_document_id: None = Field(...)


class PendingApprovalDocument(DocumentBase):
    status: Literal[DocumentStatus.PENDING_APPROVAL]
    decision_note: None = Field(...)
    failure: None = Field(...)
    superseded_by_document_id: None = Field(...)


class ApprovedDocument(DocumentBase):
    status: Literal[DocumentStatus.APPROVED]
    decision_note: Annotated[str, Field(min_length=1, max_length=500)] | None = Field(
        ...
    )
    failure: None = Field(...)
    superseded_by_document_id: None = Field(...)


class RejectedDocument(DocumentBase):
    status: Literal[DocumentStatus.REJECTED]
    decision_note: Annotated[str, Field(min_length=1, max_length=500)]
    failure: None = Field(...)
    superseded_by_document_id: None = Field(...)


class FailedDocument(DocumentBase):
    status: Literal[DocumentStatus.FAILED]
    decision_note: None = Field(...)
    failure: DocumentFailure
    superseded_by_document_id: None = Field(...)


class SupersededDocument(DocumentBase):
    status: Literal[DocumentStatus.SUPERSEDED]
    decision_note: None = Field(...)
    failure: None = Field(...)
    superseded_by_document_id: DocumentId


Document = Annotated[
    ReceivedDocument
    | ProcessingDocument
    | PendingApprovalDocument
    | ApprovedDocument
    | RejectedDocument
    | FailedDocument
    | SupersededDocument,
    Field(discriminator="status"),
]


class DocumentResponse(RootModel[Document]):
    """Named discriminated document lifecycle union."""


class DocumentListResponse(ContractModel):
    items: tuple[Document, ...]


class ValidationIssue(ContractModel):
    field: Annotated[str, Field(min_length=1, max_length=200)]
    code: Annotated[str, Field(min_length=1, max_length=100)]


class ErrorDetail(ContractModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    message: Annotated[str, Field(min_length=1, max_length=500)]
    issues: tuple[ValidationIssue, ...]


class ErrorResponse(ContractModel):
    error: ErrorDetail
