"""FastAPI routes and sanitized error semantics for API v1."""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Body, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.models import Example
from fastapi.responses import JSONResponse

from prescriptive_maintenance.contracts import (
    AnalysisId,
    AnalysisRequest,
    AnalysisResponse,
    ApprovedDocument,
    ApproveDocumentRequest,
    DocumentId,
    DocumentListResponse,
    DocumentResponse,
    ErrorDetail,
    ErrorResponse,
    ProcessingDocument,
    ReceivedDocument,
    RegisterDocumentRequest,
    RejectDocumentRequest,
    RejectedDocument,
    ValidationIssue,
)
from prescriptive_maintenance.fakes import (
    SYNTHETIC_ANALYSIS_REQUESTS,
    SYNTHETIC_DOCUMENT_REGISTER_REQUEST,
    SyntheticDocumentService,
    build_synthetic_analysis_service,
)
from prescriptive_maintenance.services import (
    AnalysisNotFoundError,
    AnalysisService,
    AnalysisUnavailableError,
    DocumentConflictError,
    DocumentLifecycleService,
    DocumentNotFoundError,
    DocumentServiceUnavailableError,
    InvalidDocumentRequestError,
    InvalidDocumentTransitionError,
)


class ApiContractError(Exception):
    """Sanitized HTTP error whose shape is frozen by API v1."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(code: str, message: str) -> ErrorResponse:
    return ErrorResponse(
        error=ErrorDetail(code=code, message=message, issues=()),
    )


async def handle_api_contract_error(
    request: Request,
    error: ApiContractError,
) -> JSONResponse:
    del request
    response = _error_response(error.code, error.message)
    return JSONResponse(
        status_code=error.status_code,
        content=response.model_dump(mode="json"),
    )


async def handle_request_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    del request, error
    response = ErrorResponse(
        error=ErrorDetail(
            code="invalid_request",
            message="A requisição não atende ao contrato da API v1.",
            issues=(ValidationIssue(field="request", code="invalid"),),
        )
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=response.model_dump(mode="json"),
    )


def analysis_response_examples() -> dict[str, dict[str, Any]]:
    service = build_synthetic_analysis_service()
    examples: dict[str, dict[str, Any]] = {}
    for outcome, request in SYNTHETIC_ANALYSIS_REQUESTS.items():
        examples[outcome] = {
            "summary": f"Resultado sintético {outcome}",
            "value": service.analyze(request).model_dump(mode="json"),
        }
    return examples


def analysis_request_examples() -> dict[str, Example]:
    return {
        outcome: Example(
            summary=f"Entrada sintética {outcome}",
            value=request.model_dump(mode="json"),
        )
        for outcome, request in SYNTHETIC_ANALYSIS_REQUESTS.items()
    }


def document_response_examples() -> dict[str, dict[str, Any]]:
    service = SyntheticDocumentService()
    return {
        document.status.value: {
            "summary": f"Documento sintético {document.status.value}",
            "value": document.model_dump(mode="json"),
        }
        for document in service.list().items
    }


def _error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        404: "Recurso não encontrado.",
        409: "Transição documental inválida.",
        422: "Requisição inválida; nenhuma porta interna é chamada.",
        503: "O modelo não pode produzir um resultado seguro.",
    }
    responses: dict[int | str, dict[str, Any]] = {}
    for code in codes:
        responses[code] = {
            "model": ErrorResponse,
            "description": descriptions[code],
        }
    return responses


def build_api_router(
    *,
    analysis_service: AnalysisService,
    document_service: DocumentLifecycleService,
) -> APIRouter:
    """Bind API v1 contracts to injected application services."""

    router = APIRouter()

    @router.post(
        "/analysis",
        response_model=AnalysisResponse,
        operation_id="createAnalysis",
        tags=["analysis"],
        summary="Executa uma análise prescritiva",
        responses={
            200: {
                "description": "Um dos cinco resultados fechados da API v1.",
                "content": {
                    "application/json": {"examples": analysis_response_examples()}
                },
            },
            **_error_responses(422, 503),
        },
    )
    def _create_analysis(
        payload: Annotated[
            AnalysisRequest,
            Body(openapi_examples=analysis_request_examples()),
        ],
    ) -> AnalysisResponse:
        try:
            return analysis_service.analyze(payload)
        except AnalysisUnavailableError:
            _raise_api_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "analysis_unavailable",
                "A análise está temporariamente indisponível.",
            )

    @router.get(
        "/analysis/{analysis_id}",
        response_model=AnalysisResponse,
        operation_id="getAnalysis",
        tags=["analysis"],
        summary="Consulta uma análise",
        responses={
            200: {
                "description": "Resultado previamente criado no catálogo sintético.",
                "content": {
                    "application/json": {"examples": analysis_response_examples()}
                },
            },
            **_error_responses(404, 422),
        },
    )
    def _get_analysis(analysis_id: AnalysisId) -> AnalysisResponse:
        try:
            return analysis_service.get(analysis_id)
        except AnalysisNotFoundError:
            _raise_api_error(
                status.HTTP_404_NOT_FOUND,
                "analysis_not_found",
                "A análise solicitada não foi encontrada.",
            )

    @router.post(
        "/documents",
        response_model=ReceivedDocument,
        status_code=status.HTTP_201_CREATED,
        operation_id="registerDocument",
        tags=["documents"],
        summary="Registra metadados de um documento",
        responses={
            201: {
                "description": "Registro recebido; nunca implica aprovação.",
                "content": {
                    "application/json": {
                        "example": SyntheticDocumentService()
                        .register(SYNTHETIC_DOCUMENT_REGISTER_REQUEST)
                        .model_dump(mode="json")
                    }
                },
            },
            **_error_responses(422),
        },
    )
    def _register_document(payload: RegisterDocumentRequest) -> ReceivedDocument:
        try:
            return document_service.register(payload)
        except InvalidDocumentRequestError:
            _raise_invalid_document_request()
        except DocumentConflictError:
            _raise_document_conflict()
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    @router.get(
        "/documents",
        response_model=DocumentListResponse,
        operation_id="listDocuments",
        tags=["documents"],
        summary="Lista documentos e estados do ciclo",
        responses=_error_responses(422),
    )
    def _list_documents() -> DocumentListResponse:
        try:
            return document_service.list()
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    @router.get(
        "/documents/{document_id}",
        response_model=DocumentResponse,
        operation_id="getDocument",
        tags=["documents"],
        summary="Consulta um documento",
        responses={
            200: {
                "description": "Documento sintético em um estado válido.",
                "content": {
                    "application/json": {"examples": document_response_examples()}
                },
            },
            **_error_responses(404, 422),
        },
    )
    def _get_document(document_id: DocumentId) -> DocumentResponse:
        try:
            return document_service.get(document_id)
        except DocumentNotFoundError:
            _raise_api_error(
                status.HTTP_404_NOT_FOUND,
                "document_not_found",
                "O documento solicitado não foi encontrado.",
            )
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    @router.post(
        "/documents/{document_id}/approve",
        response_model=ApprovedDocument,
        operation_id="approveDocument",
        tags=["documents"],
        summary="Aprova um documento pendente",
        responses=_error_responses(404, 409, 422),
    )
    def _approve_document(
        document_id: DocumentId,
        payload: ApproveDocumentRequest,
    ) -> ApprovedDocument:
        try:
            return document_service.approve(document_id, payload)
        except DocumentNotFoundError:
            _raise_document_not_found()
        except InvalidDocumentRequestError:
            _raise_invalid_document_request()
        except DocumentConflictError:
            _raise_document_conflict()
        except InvalidDocumentTransitionError:
            _raise_invalid_transition()
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    @router.post(
        "/documents/{document_id}/reject",
        response_model=RejectedDocument,
        operation_id="rejectDocument",
        tags=["documents"],
        summary="Rejeita um documento pendente",
        responses=_error_responses(404, 409, 422),
    )
    def _reject_document(
        document_id: DocumentId,
        payload: RejectDocumentRequest,
    ) -> RejectedDocument:
        try:
            return document_service.reject(document_id, payload)
        except DocumentNotFoundError:
            _raise_document_not_found()
        except InvalidDocumentRequestError:
            _raise_invalid_document_request()
        except DocumentConflictError:
            _raise_document_conflict()
        except InvalidDocumentTransitionError:
            _raise_invalid_transition()
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    @router.post(
        "/documents/{document_id}/reprocess",
        response_model=ProcessingDocument,
        operation_id="reprocessDocument",
        tags=["documents"],
        summary="Reprocessa um documento rejeitado ou com falha",
        responses=_error_responses(404, 409, 422),
    )
    def _reprocess_document(document_id: DocumentId) -> ProcessingDocument:
        try:
            return document_service.reprocess(document_id)
        except DocumentNotFoundError:
            _raise_document_not_found()
        except DocumentConflictError:
            _raise_document_conflict()
        except InvalidDocumentTransitionError:
            _raise_invalid_transition()
        except DocumentServiceUnavailableError:
            _raise_document_unavailable()

    # Route decorators consume these handlers dynamically; keep that access visible
    # to the repository's strict static analysis as well.
    _registered_handlers = (
        _create_analysis,
        _get_analysis,
        _register_document,
        _list_documents,
        _get_document,
        _approve_document,
        _reject_document,
        _reprocess_document,
    )
    del _registered_handlers

    return router


def _raise_api_error(status_code: int, code: str, message: str) -> NoReturn:
    raise ApiContractError(status_code=status_code, code=code, message=message)


def _raise_document_not_found() -> NoReturn:
    _raise_api_error(
        status.HTTP_404_NOT_FOUND,
        "document_not_found",
        "O documento solicitado não foi encontrado.",
    )


def _raise_invalid_transition() -> NoReturn:
    _raise_api_error(
        status.HTTP_409_CONFLICT,
        "invalid_document_transition",
        "A transição solicitada não é válida para o estado atual.",
    )


def _raise_invalid_document_request() -> NoReturn:
    _raise_api_error(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "invalid_request",
        "A requisição não atende ao contrato da API v1.",
    )


def _raise_document_conflict() -> NoReturn:
    _raise_api_error(
        status.HTTP_409_CONFLICT,
        "document_conflict",
        "O comando documental conflita com o estado armazenado.",
    )


def _raise_document_unavailable() -> NoReturn:
    _raise_api_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "document_service_unavailable",
        "O ciclo documental está temporariamente indisponível.",
    )


def restore_required_nulls_in_examples(schema: dict[str, Any]) -> None:
    """Undo FastAPI's metadata-only ``exclude_none`` example normalization."""

    analysis_examples = analysis_response_examples()
    schema["paths"]["/analysis"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["examples"] = analysis_examples
    schema["paths"]["/analysis/{analysis_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["examples"] = analysis_examples
    schema["paths"]["/documents/{document_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["examples"] = document_response_examples()
