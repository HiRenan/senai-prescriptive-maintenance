"""FastAPI application entry point."""

from fastapi import FastAPI, status

from prescriptive_maintenance import __version__


def _liveness() -> dict[str, str]:
    return {"status": "ok"}


def create_app() -> FastAPI:
    """Create an isolated FastAPI application instance."""
    application = FastAPI(
        title="Prescriptive Maintenance API",
        version=__version__,
    )

    application.add_api_route(
        "/health/live",
        _liveness,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
    )

    return application


app = create_app()
