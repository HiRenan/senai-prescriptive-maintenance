"""Deterministic, entirely synthetic fakes for the frozen API contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, TypedDict

from prescriptive_maintenance.contracts import (
    AnalysisFeatures,
    AnalysisRequest,
    ApprovedDocument,
    ApproveDocumentRequest,
    Citation,
    Diagnosis,
    Document,
    DocumentFailure,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    FailedDocument,
    OpaqueNeighbor,
    PendingApprovalDocument,
    Prescription,
    PrescriptionPriority,
    ProcessingDocument,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
    RejectedDocument,
    SupersededDocument,
)
from prescriptive_maintenance.ports import (
    DocumentEvidence,
    GenerationPort,
    ModelDisposition,
    ModelPort,
    ModelPrediction,
    PortUnavailableError,
    RetrievalPort,
)
from prescriptive_maintenance.services import (
    AnalysisService,
    DocumentNotFoundError,
    InvalidDocumentTransitionError,
    document_with_transition,
)

_FIXED_TIME = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
_TRANSITION_TIME = datetime(2030, 1, 2, 4, 5, 6, tzinfo=UTC)
_SYNTHETIC_SHA = "a" * 64


def _synthetic_features(*, rpm: float) -> AnalysisFeatures:
    return AnalysisFeatures(
        z_rms_velocity_mm_s=1.2,
        temperature_c=42.0,
        x_rms_velocity_mm_s=1.1,
        z_peak_acceleration_g=0.3,
        x_peak_acceleration_g=0.25,
        z_peak_vel_comp_freq_hz=60.0,
        x_peak_vel_comp_freq_hz=58.0,
        z_rms_acceleration_g=0.08,
        x_rms_acceleration_g=0.07,
        z_kurtosis=3.1,
        x_kurtosis=3.0,
        z_crest_factor=1.8,
        x_crest_factor=1.7,
        z_peak_velocity_mm_s=2.4,
        x_peak_velocity_mm_s=2.2,
        z_high_freq_rms_accel_g=0.04,
        x_high_freq_rms_accel_g=0.03,
        rpm=rpm,
    )


SYNTHETIC_ANALYSIS_REQUESTS: dict[str, AnalysisRequest] = {
    "normal": AnalysisRequest(features=_synthetic_features(rpm=1000.0), top_k=3),
    "documented_fault": AnalysisRequest(
        features=_synthetic_features(rpm=1100.0),
        top_k=3,
    ),
    "undocumented_fault": AnalysisRequest(
        features=_synthetic_features(rpm=1200.0),
        top_k=3,
    ),
    "out_of_distribution": AnalysisRequest(
        features=_synthetic_features(rpm=1300.0),
        top_k=3,
    ),
    "degraded": AnalysisRequest(
        features=_synthetic_features(rpm=1400.0),
        top_k=3,
    ),
}


class SyntheticModelPort(ModelPort):
    """Select fixed scenarios by an obviously synthetic RPM sentinel."""

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        scenario = int(features.rpm)
        if scenario == 1300:
            return ModelPrediction(
                disposition=ModelDisposition.OUT_OF_DISTRIBUTION,
                diagnosis=None,
                support_score=0.05,
                model_id="model_synthetic_v1",
                neighbors=_synthetic_neighbors(
                    top_k,
                    fault_code="synthetic_reference_fault",
                    distance_start=1.4,
                ),
                retrieval_key=None,
            )

        if scenario == 1100:
            code = "synthetic_documented_fault"
            summary = "Falha sintética com documentação aprovada."
            support_score = 0.92
            retrieval_key = "documented"
            disposition = ModelDisposition.FAULT
        elif scenario == 1200:
            code = "synthetic_undocumented_fault"
            summary = "Falha sintética sem documentação aprovada."
            support_score = 0.79
            retrieval_key = "undocumented"
            disposition = ModelDisposition.FAULT
        elif scenario == 1400:
            code = "synthetic_degraded"
            summary = "Condição sintética com dependência indisponível."
            support_score = 0.72
            retrieval_key = "degraded"
            disposition = ModelDisposition.FAULT
        else:
            code = "synthetic_normal"
            summary = "Condição sintética dentro da faixa esperada."
            support_score = 0.98
            retrieval_key = None
            disposition = ModelDisposition.NORMAL

        return ModelPrediction(
            disposition=disposition,
            diagnosis=Diagnosis(
                code=code,
                summary=summary,
            ),
            support_score=support_score,
            model_id="model_synthetic_v1",
            neighbors=_synthetic_neighbors(top_k, fault_code=code),
            retrieval_key=retrieval_key,
        )


def _synthetic_neighbors(
    top_k: int,
    *,
    fault_code: str,
    distance_start: float = 0.4,
) -> tuple[OpaqueNeighbor, ...]:
    return tuple(
        OpaqueNeighbor(
            neighbor_ref=f"neighbor_synthetic_{rank:02d}",
            rank=rank,
            fault_code=fault_code,
            distance=round(distance_start + (rank - 1) * 0.9, 2),
        )
        for rank in range(1, top_k + 1)
    )


class SyntheticRetrievalPort(RetrievalPort):
    def retrieve(self, retrieval_key: str, *, top_k: int) -> DocumentEvidence:
        citations = tuple(
            Citation(
                document_id="doc_synthetic_manual",
                document_version="docver_synthetic_manual_v1",
                chunk=f"chunk_synthetic_manual_{rank:02d}",
                page_number=rank,
            )
            for rank in range(1, top_k + 1)
        )
        if retrieval_key == "undocumented":
            return DocumentEvidence(
                support_score=0.12,
                citations=(),
            )
        return DocumentEvidence(
            support_score=0.88,
            citations=citations,
        )


class SyntheticGenerationPort(GenerationPort):
    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription:
        del evidence
        if diagnosis.code == "synthetic_degraded":
            raise PortUnavailableError("synthetic generation outage")
        return Prescription(
            summary="Programar inspeção sintética controlada.",
            priority=PrescriptionPriority.SCHEDULED,
            actions=(
                "Confirmar a condição em uma nova leitura sintética.",
                "Revisar o manual sintético citado.",
            ),
        )


def build_synthetic_analysis_service() -> AnalysisService:
    service = AnalysisService(
        model=SyntheticModelPort(),
        retrieval=SyntheticRetrievalPort(),
        generation=SyntheticGenerationPort(),
    )
    service.seed(SYNTHETIC_ANALYSIS_REQUESTS.values())
    return service


class SyntheticDocumentService:
    """Ephemeral lifecycle fake; it never reads or stores document bytes."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = _synthetic_documents()

    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument:
        document = ReceivedDocument(
            document_id=f"doc_{request.sha256[:12]}",
            filename=request.filename,
            media_type=request.media_type,
            size_bytes=request.size_bytes,
            sha256=request.sha256,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
            status=DocumentStatus.RECEIVED,
            decision_note=None,
            failure=None,
            superseded_by_document_id=None,
        )
        self._documents[document.document_id] = document
        return document

    def list(self) -> DocumentListResponse:
        return DocumentListResponse(
            items=tuple(self._documents[key] for key in sorted(self._documents))
        )

    def get(self, document_id: str) -> DocumentResponse:
        return DocumentResponse(root=self._get(document_id))

    def approve(
        self,
        document_id: str,
        request: ApproveDocumentRequest,
    ) -> ApprovedDocument:
        document = self._get(document_id)
        if not isinstance(document, PendingApprovalDocument):
            raise InvalidDocumentTransitionError(
                "Only pending documents can be approved."
            )
        transitioned = document_with_transition(
            document,
            status="approved",
            updated_at=_TRANSITION_TIME,
            decision_note=request.note,
        )
        if not isinstance(transitioned, ApprovedDocument):
            raise AssertionError("Unexpected synthetic transition result.")
        self._documents[document_id] = transitioned
        return transitioned

    def reject(
        self,
        document_id: str,
        request: RejectDocumentRequest,
    ) -> RejectedDocument:
        document = self._get(document_id)
        if not isinstance(document, PendingApprovalDocument):
            raise InvalidDocumentTransitionError(
                "Only pending documents can be rejected."
            )
        transitioned = document_with_transition(
            document,
            status="rejected",
            updated_at=_TRANSITION_TIME,
            decision_note=request.reason,
        )
        if not isinstance(transitioned, RejectedDocument):
            raise AssertionError("Unexpected synthetic transition result.")
        self._documents[document_id] = transitioned
        return transitioned

    def reprocess(self, document_id: str) -> ProcessingDocument:
        document = self._get(document_id)
        if not isinstance(document, (RejectedDocument, FailedDocument)):
            raise InvalidDocumentTransitionError(
                "Only rejected or failed documents can be reprocessed."
            )
        transitioned = document_with_transition(
            document,
            status="processing",
            updated_at=_TRANSITION_TIME,
        )
        if not isinstance(transitioned, ProcessingDocument):
            raise AssertionError("Unexpected synthetic transition result.")
        self._documents[document_id] = transitioned
        return transitioned

    def _get(self, document_id: str) -> Document:
        try:
            return self._documents[document_id]
        except KeyError:
            raise DocumentNotFoundError("Document was not found.") from None


class _DocumentCommon(TypedDict):
    document_id: str
    filename: str
    media_type: Literal["application/pdf"]
    size_bytes: int
    sha256: str
    created_at: datetime
    updated_at: datetime


def _document_common(document_id: str, filename: str) -> _DocumentCommon:
    return {
        "document_id": document_id,
        "filename": filename,
        "media_type": "application/pdf",
        "size_bytes": 1024,
        "sha256": _SYNTHETIC_SHA,
        "created_at": _FIXED_TIME,
        "updated_at": _FIXED_TIME,
    }


def _synthetic_documents() -> dict[str, Document]:
    received = ReceivedDocument(
        **_document_common("doc_synthetic_received", "received.synthetic.pdf"),
        status=DocumentStatus.RECEIVED,
        decision_note=None,
        failure=None,
        superseded_by_document_id=None,
    )
    processing = ProcessingDocument(
        **_document_common("doc_synthetic_processing", "processing.synthetic.pdf"),
        status=DocumentStatus.PROCESSING,
        decision_note=None,
        failure=None,
        superseded_by_document_id=None,
    )
    pending = PendingApprovalDocument(
        **_document_common("doc_synthetic_pending", "pending.synthetic.pdf"),
        status=DocumentStatus.PENDING_APPROVAL,
        decision_note=None,
        failure=None,
        superseded_by_document_id=None,
    )
    approved = ApprovedDocument(
        **_document_common("doc_synthetic_manual", "manual.synthetic.pdf"),
        status=DocumentStatus.APPROVED,
        decision_note="Conteúdo inteiramente sintético aprovado para o fake.",
        failure=None,
        superseded_by_document_id=None,
    )
    rejected = RejectedDocument(
        **_document_common("doc_synthetic_rejected", "rejected.synthetic.pdf"),
        status=DocumentStatus.REJECTED,
        decision_note="Metadados sintéticos incompletos.",
        failure=None,
        superseded_by_document_id=None,
    )
    failed = FailedDocument(
        **_document_common("doc_synthetic_failed", "failed.synthetic.pdf"),
        status=DocumentStatus.FAILED,
        decision_note=None,
        failure=DocumentFailure(
            code="synthetic_processing_failure",
            message="Falha controlada do fake sintético.",
        ),
        superseded_by_document_id=None,
    )
    superseded = SupersededDocument(
        **_document_common("doc_synthetic_superseded", "old.synthetic.pdf"),
        status=DocumentStatus.SUPERSEDED,
        decision_note=None,
        failure=None,
        superseded_by_document_id="doc_synthetic_manual",
    )
    documents: tuple[Document, ...] = (
        received,
        processing,
        pending,
        approved,
        rejected,
        failed,
        superseded,
    )
    return {document.document_id: document for document in documents}


SYNTHETIC_DOCUMENT_REGISTER_REQUEST = RegisterDocumentRequest(
    filename="inspection.synthetic.pdf",
    media_type="application/pdf",
    size_bytes=2048,
    sha256="b" * 64,
)
