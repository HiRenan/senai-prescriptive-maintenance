"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from prescriptive_maintenance.contracts import API_CONTRACT_VERSION
from prescriptive_maintenance.document_registry import RuntimeDocumentLifecycleService
from prescriptive_maintenance.fakes import build_synthetic_analysis_service
from prescriptive_maintenance.http_api import (
    ApiContractError,
    build_api_router,
    handle_api_contract_error,
    handle_request_validation_error,
    restore_required_nulls_in_examples,
)
from prescriptive_maintenance.operations import (
    READINESS_TIMEOUT_SECONDS,
    ApplicationStartupError,
    CorrelationIdMiddleware,
    ReadinessProbe,
    ReadinessService,
    RequiredDependencyUnavailableError,
)
from prescriptive_maintenance.services import (
    AnalysisService,
    DocumentLifecycleService,
)
from prescriptive_maintenance.settings import Settings, load_settings


def _liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _readiness(request: Request) -> dict[str, str]:
    readiness = cast(ReadinessService, request.app.state.readiness)
    try:
        await readiness.check()
    except RequiredDependencyUnavailableError:
        raise ApiContractError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="service_not_ready",
            message="O serviço não está pronto para receber tráfego.",
        ) from None
    return {"status": "ready"}


def create_app(
    *,
    analysis_service: AnalysisService | None = None,
    document_service: DocumentLifecycleService | None = None,
    settings: Settings | None = None,
    settings_loader: Callable[[], Settings] = load_settings,
    database_probe: ReadinessProbe | None = None,
    readiness_timeout_seconds: float = READINESS_TIMEOUT_SECONDS,
) -> FastAPI:
    """Create an isolated FastAPI application instance."""

    runtime_document_service = (
        RuntimeDocumentLifecycleService() if document_service is None else None
    )
    selected_document_service = document_service or runtime_document_service
    if selected_document_service is None:
        raise AssertionError("Document service composition is incomplete.")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        try:
            loaded_settings = settings if settings is not None else settings_loader()
            if type(loaded_settings) is not Settings:
                raise TypeError("Startup settings must use the canonical type.")
            selected_settings = Settings.model_validate(
                loaded_settings.model_dump(mode="python")
            )
            readiness = ReadinessService(
                selected_settings,
                database_probe=database_probe,
                timeout_seconds=readiness_timeout_seconds,
            )
            if runtime_document_service is not None:
                runtime_document_service.configure(selected_settings)
        except Exception:
            raise ApplicationStartupError(
                "Application startup configuration is invalid."
            ) from None
        application.state.readiness = readiness
        application.state.environment = selected_settings.environment
        application.state.persistence_backend = selected_settings.persistence_backend
        application.state.document_service = selected_document_service
        yield

    application = FastAPI(
        title="Prescriptive Maintenance API",
        summary="Contrato v1 de análise e ciclo documental.",
        description=(
            "Contrato público congelado com fakes inteiramente sintéticos; "
            "não executa modelo, recuperação, geração ou persistência reais."
        ),
        version=API_CONTRACT_VERSION,
        openapi_version="3.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(CorrelationIdMiddleware)

    application.add_api_route(
        "/health/live",
        _liveness,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
    )
    application.add_api_route(
        "/health/ready",
        _readiness,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
        include_in_schema=False,
    )

    selected_analysis_service = analysis_service or build_synthetic_analysis_service()
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
