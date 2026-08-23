"""FastAPI application entry point."""

from typing import Any, cast

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from prescriptive_maintenance.contracts import API_CONTRACT_VERSION
from prescriptive_maintenance.fakes import (
    SyntheticDocumentService,
    build_synthetic_analysis_service,
)
from prescriptive_maintenance.http_api import (
    ApiContractError,
    build_api_router,
    handle_api_contract_error,
    handle_request_validation_error,
    restore_required_nulls_in_examples,
)
from prescriptive_maintenance.services import (
    AnalysisService,
    DocumentLifecycleService,
)


def _liveness() -> dict[str, str]:
    return {"status": "ok"}


def create_app(
    *,
    analysis_service: AnalysisService | None = None,
    document_service: DocumentLifecycleService | None = None,
) -> FastAPI:
    """Create an isolated FastAPI application instance."""
    application = FastAPI(
        title="Prescriptive Maintenance API",
        summary="Contrato v1 de análise e ciclo documental.",
        description=(
            "Contrato público congelado com fakes inteiramente sintéticos; "
            "não executa modelo, recuperação, geração ou persistência reais."
        ),
        version=API_CONTRACT_VERSION,
        openapi_version="3.1.0",
    )

    application.add_api_route(
        "/health/live",
        _liveness,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
    )

    selected_analysis_service = analysis_service or build_synthetic_analysis_service()
    selected_document_service = document_service or SyntheticDocumentService()
    application.include_router(
        build_api_router(
            analysis_service=selected_analysis_service,
            document_service=selected_document_service,
        )
    )
    application.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, handle_request_validation_error),
    )
    application.add_exception_handler(
        ApiContractError,
        cast(ExceptionHandler, handle_api_contract_error),
    )

    default_openapi = application.openapi

    def openapi_with_complete_examples() -> dict[str, Any]:
        schema = default_openapi()
        restore_required_nulls_in_examples(schema)
        return schema

    application.openapi = openapi_with_complete_examples

    return application


app = create_app()
