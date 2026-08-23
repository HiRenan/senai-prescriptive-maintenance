"""Application services that enforce API v1 outcome semantics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol

from prescriptive_maintenance.contracts import (
    AbstentionReason,
    AnalysisOutcome,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisResult,
    AnalysisWarning,
    ApprovedDocument,
    ApproveDocumentRequest,
    DegradedAnalysisResult,
    DependencyUnavailableAbstention,
    Document,
    DocumentedFaultAnalysisResult,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    InsufficientSupport,
    NormalAnalysisResult,
    OutOfDistributionAbstention,
    OutOfDistributionAnalysisResult,
    ProcessingDocument,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
    RejectedDocument,
    SufficientSupport,
    UndocumentedFaultAbstention,
    UndocumentedFaultAnalysisResult,
)
from prescriptive_maintenance.ports import (
    DocumentEvidence,
    GenerationPort,
    ModelDisposition,
    ModelPort,
    ModelPrediction,
    PortContractError,
    PortUnavailableError,
    RetrievalPort,
)


class AnalysisNotFoundError(Exception):
    """Raised when the fake query catalog has no matching analysis."""


class AnalysisUnavailableError(Exception):
    """Raised when no safe analysis result can be produced."""


class DocumentNotFoundError(Exception):
    """Raised when the synthetic document catalog has no matching resource."""


class InvalidDocumentTransitionError(Exception):
    """Raised when a lifecycle action is invalid for the current state."""


class DocumentLifecycleService(Protocol):
    def register(self, request: RegisterDocumentRequest) -> ReceivedDocument: ...

    def list(self) -> DocumentListResponse: ...

    def get(self, document_id: str) -> DocumentResponse: ...

    def approve(
        self,
        document_id: str,
        request: ApproveDocumentRequest,
    ) -> ApprovedDocument: ...

    def reject(
        self,
        document_id: str,
        request: RejectDocumentRequest,
    ) -> RejectedDocument: ...

    def reprocess(self, document_id: str) -> ProcessingDocument: ...


class AnalysisService:
    """Coordinate the three internal ports and preserve state invariants."""

    def __init__(
        self,
        *,
        model: ModelPort,
        retrieval: RetrievalPort,
        generation: GenerationPort,
    ) -> None:
        self._model = model
        self._retrieval = retrieval
        self._generation = generation
        self._results: dict[str, AnalysisResult] = {}

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        try:
            prediction = self._model.predict(request.features, top_k=request.top_k)
        except PortUnavailableError:
            raise AnalysisUnavailableError(
                "The analysis model is temporarily unavailable."
            ) from None

        result = self._result_from_prediction(prediction, top_k=request.top_k)
        self._results[result.analysis_id] = result
        return AnalysisResponse(root=result)

    def get(self, analysis_id: str) -> AnalysisResponse:
        try:
            return AnalysisResponse(root=self._results[analysis_id])
        except KeyError:
            raise AnalysisNotFoundError("Analysis was not found.") from None

    def seed(self, requests: Iterable[AnalysisRequest]) -> None:
        """Populate only the deterministic synthetic query catalog."""

        for request in requests:
            self.analyze(request)

    def _result_from_prediction(
        self,
        prediction: ModelPrediction,
        *,
        top_k: int,
    ) -> AnalysisResult:
        if prediction.disposition is ModelDisposition.OUT_OF_DISTRIBUTION:
            return OutOfDistributionAnalysisResult(
                analysis_id="ana_synthetic_out_of_distribution",
                outcome=AnalysisOutcome.OUT_OF_DISTRIBUTION,
                diagnosis=None,
                support=InsufficientSupport(
                    level="insufficient",
                    support_score=prediction.support_score,
                ),
                abstention=OutOfDistributionAbstention(
                    reason=AbstentionReason.OUT_OF_DISTRIBUTION,
                    message=(
                        "A entrada sintética está fora da distribuição suportada."
                    ),
                ),
                model_id=prediction.model_id,
                neighbors=prediction.neighbors[:top_k],
                prescription=None,
                citations=(),
                warnings=(
                    AnalysisWarning(
                        code="out_of_distribution",
                        message="Nenhuma prescrição foi produzida.",
                    ),
                ),
            )

        diagnosis = prediction.diagnosis
        if diagnosis is None:
            raise AnalysisUnavailableError(
                "The analysis model returned an incomplete result."
            )

        if prediction.disposition is ModelDisposition.NORMAL:
            return NormalAnalysisResult(
                analysis_id="ana_synthetic_normal",
                outcome=AnalysisOutcome.NORMAL,
                diagnosis=diagnosis,
                support=SufficientSupport(
                    level="sufficient",
                    support_score=prediction.support_score,
                ),
                abstention=None,
                model_id=prediction.model_id,
                neighbors=prediction.neighbors[:top_k],
                prescription=None,
                citations=(),
                warnings=(),
            )

        retrieval_key = prediction.retrieval_key
        if retrieval_key is None:
            raise AnalysisUnavailableError(
                "The analysis model returned an incomplete result."
            )

        try:
            evidence = self._retrieval.retrieve(retrieval_key, top_k=top_k)
        except (PortUnavailableError, PortContractError):
            return self._degraded_result(prediction, evidence=None, top_k=top_k)

        if not evidence.citations:
            return UndocumentedFaultAnalysisResult(
                analysis_id="ana_synthetic_undocumented_fault",
                outcome=AnalysisOutcome.UNDOCUMENTED_FAULT,
                diagnosis=diagnosis,
                support=SufficientSupport(
                    level="sufficient",
                    support_score=prediction.support_score,
                ),
                abstention=UndocumentedFaultAbstention(
                    reason=AbstentionReason.UNDOCUMENTED_FAULT,
                    message="Não há documentação suficiente para prescrever uma ação.",
                ),
                model_id=prediction.model_id,
                neighbors=prediction.neighbors[:top_k],
                prescription=None,
                citations=(),
                warnings=(
                    AnalysisWarning(
                        code="documentation_not_found",
                        message="O diagnóstico não possui suporte documental aprovado.",
                    ),
                ),
            )

        bounded_evidence = DocumentEvidence(
            support_score=evidence.support_score,
            citations=evidence.citations[:top_k],
        )
        try:
            prescription = self._generation.generate(diagnosis, bounded_evidence)
        except PortUnavailableError:
            return self._degraded_result(
                prediction,
                evidence=bounded_evidence,
                top_k=top_k,
            )

        return DocumentedFaultAnalysisResult(
            analysis_id="ana_synthetic_documented_fault",
            outcome=AnalysisOutcome.DOCUMENTED_FAULT,
            diagnosis=diagnosis,
            support=SufficientSupport(
                level="sufficient",
                support_score=prediction.support_score,
            ),
            abstention=None,
            model_id=prediction.model_id,
            neighbors=prediction.neighbors[:top_k],
            prescription=prescription,
            citations=bounded_evidence.citations,
            warnings=(),
        )

    @staticmethod
    def _degraded_result(
        prediction: ModelPrediction,
        *,
        evidence: DocumentEvidence | None,
        top_k: int,
    ) -> DegradedAnalysisResult:
        diagnosis = prediction.diagnosis
        if diagnosis is None:
            raise AnalysisUnavailableError(
                "The analysis model returned an incomplete result."
            )
        return DegradedAnalysisResult(
            analysis_id="ana_synthetic_degraded",
            outcome=AnalysisOutcome.DEGRADED,
            diagnosis=diagnosis,
            support=SufficientSupport(
                level="sufficient",
                support_score=prediction.support_score,
            ),
            abstention=DependencyUnavailableAbstention(
                reason=AbstentionReason.DEPENDENCY_UNAVAILABLE,
                message="A análise parcial não permite uma prescrição segura.",
            ),
            model_id=prediction.model_id,
            neighbors=prediction.neighbors[:top_k],
            prescription=None,
            citations=() if evidence is None else evidence.citations,
            warnings=(
                AnalysisWarning(
                    code="dependency_unavailable",
                    message="Recuperação ou geração está temporariamente indisponível.",
                ),
            ),
        )


def document_with_transition(
    document: Document,
    *,
    status: Literal["approved", "rejected", "processing"],
    updated_at: datetime,
    decision_note: str | None = None,
) -> ApprovedDocument | RejectedDocument | ProcessingDocument:
    """Build a validated lifecycle transition without copying internal state."""

    if status == "approved":
        return ApprovedDocument(
            document_id=document.document_id,
            filename=document.filename,
            media_type=document.media_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            created_at=document.created_at,
            updated_at=updated_at,
            status=DocumentStatus.APPROVED,
            decision_note=decision_note,
            failure=None,
            superseded_by_document_id=None,
        )
    if status == "rejected":
        if decision_note is None:
            raise ValueError("Rejected documents require a decision note.")
        return RejectedDocument(
            document_id=document.document_id,
            filename=document.filename,
            media_type=document.media_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            created_at=document.created_at,
            updated_at=updated_at,
            status=DocumentStatus.REJECTED,
            decision_note=decision_note,
            failure=None,
            superseded_by_document_id=None,
        )
    return ProcessingDocument(
        document_id=document.document_id,
        filename=document.filename,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        created_at=document.created_at,
        updated_at=updated_at,
        status=DocumentStatus.PROCESSING,
        decision_note=None,
        failure=None,
        superseded_by_document_id=None,
    )
