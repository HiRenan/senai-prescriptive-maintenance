"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from prescriptive_maintenance.analysis_runtime import (
    AnalysisRuntimeComposition,
    ConfiguredAnalysisService,
    compose_analysis_runtime,
)
from prescriptive_maintenance.contracts import API_CONTRACT_VERSION
from prescriptive_maintenance.document_registry import RuntimeDocumentLifecycleService
from prescriptive_maintenance.grounded_assistant import (
    AssistantQueryService,
    ConfiguredAssistantService,
    build_synthetic_grounded_assistant,
)
from prescriptive_maintenance.http_api import (
    ApiContractError,
    build_api_router,
    handle_api_contract_error,
    handle_request_validation_error,
    restore_required_nulls_in_examples,
)
from prescriptive_maintenance.operations import (
    READINESS_TIMEOUT_SECONDS,
    AnalysisModeHeaderMiddleware,
    ApplicationStartupError,
    CorrelationIdMiddleware,
    ReadinessProbe,
    ReadinessService,
    RequiredDependencyUnavailableError,
)
from prescriptive_maintenance.services import (
    AnalysisLifecycleService,
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
    analysis_service: AnalysisLifecycleService | None = None,
    assistant_service: AssistantQueryService | None = None,
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
    runtime_analysis_service = ConfiguredAnalysisService()
    runtime_assistant_service = ConfiguredAssistantService()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        try:
            loaded_settings = settings if settings is not None else settings_loader()
            if type(loaded_settings) is not Settings:
                raise TypeError("Startup settings must use the canonical type.")
            selected_settings = Settings.model_validate(
                loaded_settings.model_dump(mode="python")
            )
            runtime_analysis_service.select(selected_settings.analysis_mode)
            runtime_assistant_service.select(selected_settings.analysis_mode)
            if runtime_document_service is not None:
                runtime_document_service.configure(selected_settings)
        except Exception:
            raise ApplicationStartupError(
                "Application startup configuration is invalid."
            ) from None

        runtime_composition: AnalysisRuntimeComposition | None = None
        if analysis_service is not None:
            if selected_settings.analysis_mode != "synthetic_demo":
                raise ApplicationStartupError(
                    "Application startup configuration is invalid."
                ) from None
            runtime_analysis_service.configure(analysis_service)
        else:
            try:
                runtime_composition = compose_analysis_runtime(selected_settings)
                runtime_analysis_service.configure(runtime_composition.service)
            except Exception:
                runtime_composition = None
        if assistant_service is not None:
            if selected_settings.analysis_mode != "synthetic_demo":
                raise ApplicationStartupError(
                    "Application startup configuration is invalid."
                )
            runtime_assistant_service.configure(assistant_service)
        elif selected_settings.analysis_mode == "synthetic_demo":
            with suppress(Exception):
                runtime_assistant_service.configure(
                    build_synthetic_grounded_assistant()
                )
        try:
            readiness = ReadinessService(
                selected_settings,
                database_probe=database_probe,
                runtime_available=runtime_analysis_service.available
                and (
                    selected_settings.analysis_mode != "synthetic_demo"
                    or runtime_assistant_service.available
                ),
                timeout_seconds=readiness_timeout_seconds,
            )
        except Exception:
            raise ApplicationStartupError(
                "Application startup configuration is invalid."
            ) from None
        application.state.readiness = readiness
        application.state.environment = selected_settings.environment
        application.state.persistence_backend = selected_settings.persistence_backend
        application.state.analysis_mode = selected_settings.analysis_mode
        application.state.analysis_runtime = runtime_composition
        application.state.assistant_service = runtime_assistant_service
        application.state.document_service = selected_document_service
        yield

    application = FastAPI(
        title="Prescriptive Maintenance API",
        summary="Contrato v1 de análise e ciclo documental.",
        description=(
            "Contrato público v1 com runtime de análise selecionado "
            "explicitamente por configuração. O cabeçalho X-Analysis-Mode "
            "informa somente o modo sanitizado em uso."
        ),
        version=API_CONTRACT_VERSION,
        openapi_version="3.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(
        AnalysisModeHeaderMiddleware,
        mode_provider=lambda: runtime_analysis_service.mode,
    )

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

    application.include_router(
        build_api_router(
            analysis_service=runtime_analysis_service,
            assistant_service=runtime_assistant_service,
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
        components = schema.setdefault("components", {})
        headers = components.setdefault("headers", {})
        headers["AnalysisMode"] = {
            "description": "Modo de análise configurado, com valor sanitizado.",
            "schema": {
                "type": "string",
                "enum": ["synthetic_demo", "artifacts"],
            },
        }
        paths = cast(dict[str, object], schema.get("paths", {}))
        for raw_path in paths.values():
            if not isinstance(raw_path, dict):
                continue
            path = cast(dict[str, object], raw_path)
            for operation in path.values():
                if not isinstance(operation, dict):
                    continue
                typed_operation = cast(dict[str, object], operation)
                responses = cast(
                    dict[str, object],
                    typed_operation.get("responses", {}),
                )
                for response in responses.values():
                    if not isinstance(response, dict):
                        continue
                    typed_response = cast(dict[str, object], response)
                    response_headers = cast(
                        dict[str, object],
                        typed_response.setdefault("headers", {}),
                    )
                    response_headers["X-Analysis-Mode"] = {
                        "$ref": "#/components/headers/AnalysisMode"
                    }
        return schema

    application.openapi = openapi_with_complete_examples

    return application


app = create_app()
