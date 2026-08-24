"""Operational health, correlation, and sanitized request logging boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar, Token
from math import ceil, isfinite
from typing import Final, Protocol, TextIO
from uuid import uuid4

from psycopg import Connection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prescriptive_maintenance.settings import AnalysisMode, Settings

ANALYSIS_MODE_HEADER: Final = "X-Analysis-Mode"
CORRELATION_ID_HEADER: Final = "X-Correlation-ID"
READINESS_TIMEOUT_SECONDS: Final = 1.0
MAX_READINESS_TIMEOUT_SECONDS: Final = 10.0
_CORRELATION_ID_HEADER_BYTES: Final = CORRELATION_ID_HEADER.lower().encode("ascii")
_ANALYSIS_MODE_HEADER_BYTES: Final = ANALYSIS_MODE_HEADER.lower().encode("ascii")
_CORRELATION_ID_PATTERN: Final = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,62}[A-Za-z0-9])?"
)
_REQUEST_LOGGER: Final = logging.getLogger("prescriptive_maintenance.requests")
_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "prescriptive_maintenance_correlation_id",
    default=None,
)


class _JsonLineHandler(logging.StreamHandler[TextIO]):
    """Identify the single application-owned JSON line handler."""


def _configure_request_logger() -> None:
    if not any(
        type(handler) is _JsonLineHandler for handler in _REQUEST_LOGGER.handlers
    ):
        handler = _JsonLineHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _REQUEST_LOGGER.addHandler(handler)
    _REQUEST_LOGGER.setLevel(logging.INFO)
    _REQUEST_LOGGER.propagate = False


_configure_request_logger()


class ApplicationStartupError(RuntimeError):
    """A sanitized startup failure safe for operational logs."""


class RequiredDependencyUnavailableError(RuntimeError):
    """A required dependency did not pass its bounded readiness probe."""


class ReadinessProbe(Protocol):
    """Small synchronous port for a profile's required dependency."""

    def check(self) -> None:
        """Return only when the dependency is ready."""

        ...


class PostgresReadinessProbe:
    """Check PostgreSQL connectivity without retaining a connection."""

    __slots__ = ("_connect_timeout_seconds", "_database_url")

    def __init__(self, database_url: str, *, connect_timeout_seconds: float) -> None:
        if type(database_url) is not str or not database_url:
            raise ValueError("PostgreSQL readiness requires a database URL.")
        if (
            type(connect_timeout_seconds) is not float
            or not isfinite(connect_timeout_seconds)
            or connect_timeout_seconds <= 0
        ):
            raise ValueError("PostgreSQL readiness timeout is invalid.")
        self._database_url = database_url
        self._connect_timeout_seconds = connect_timeout_seconds

    def check(self) -> None:
        connect_timeout = max(1, ceil(self._connect_timeout_seconds))
        with Connection.connect(
            self._database_url,
            connect_timeout=connect_timeout,
        ) as connection:
            row = connection.execute("SELECT 1").fetchone()
        if row is None or len(row) != 1 or row[0] != 1:
            raise RequiredDependencyUnavailableError(
                "PostgreSQL readiness check returned an unexpected result."
            )


class ReadinessService:
    """Evaluate only the dependencies required by the selected profile."""

    __slots__ = (
        "_in_flight_probe",
        "_probe",
        "_runtime_available",
        "_timeout_seconds",
    )

    def __init__(
        self,
        settings: Settings,
        *,
        database_probe: ReadinessProbe | None,
        runtime_available: bool = True,
        timeout_seconds: float = READINESS_TIMEOUT_SECONDS,
    ) -> None:
        if type(settings) is not Settings:
            raise TypeError("Readiness settings must use the canonical type.")
        if (
            type(runtime_available) is not bool
            or type(timeout_seconds) is not float
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_READINESS_TIMEOUT_SECONDS
        ):
            raise ValueError("Readiness timeout is invalid.")

        if settings.persistence_backend == "memory":
            selected_probe = None
        else:
            database_url = settings.database_url
            if database_url is None:
                raise ValueError("PostgreSQL backend requires a database URL.")
            selected_probe = database_probe or PostgresReadinessProbe(
                str(database_url),
                connect_timeout_seconds=timeout_seconds,
            )

        self._probe = selected_probe
        self._runtime_available = runtime_available
        self._timeout_seconds = timeout_seconds
        self._in_flight_probe: asyncio.Task[None] | None = None

    async def check(self) -> None:
        """Fail closed without exposing a probe exception or timing out the API."""

        if not self._runtime_available:
            raise RequiredDependencyUnavailableError(
                "The configured analysis runtime is unavailable."
            )
        probe = self._probe
        if probe is None:
            return
        task = self._in_flight_probe
        if task is None or task.done():
            task = asyncio.create_task(asyncio.to_thread(probe.check))
            task.add_done_callback(_consume_probe_result)
            self._in_flight_probe = task
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._timeout_seconds,
            )
        except Exception:
            raise RequiredDependencyUnavailableError(
                "A required dependency is unavailable."
            ) from None


def _consume_probe_result(task: asyncio.Task[None]) -> None:
    """Retrieve a late probe result after every caller has timed out."""

    if not task.cancelled():
        task.exception()


def normalize_correlation_id(value: object) -> str | None:
    """Accept only an exact, bounded ASCII identifier safe for logs and headers."""

    if type(value) is not str or _CORRELATION_ID_PATTERN.fullmatch(value) is None:
        return None
    return value


def current_correlation_id() -> str | None:
    """Return the identifier isolated to the current request context."""

    return _CORRELATION_ID.get()


class AnalysisModeHeaderMiddleware:
    """Expose only the closed configured mode as response metadata."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        mode_provider: Callable[[], AnalysisMode | None],
    ) -> None:
        self._app = app
        self._mode_provider = mode_provider

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_analysis_mode(message: Message) -> None:
            if message["type"] == "http.response.start":
                mode = self._mode_provider()
                if mode in {"synthetic_demo", "artifacts"}:
                    headers = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() != _ANALYSIS_MODE_HEADER_BYTES
                    ]
                    headers.append((_ANALYSIS_MODE_HEADER_BYTES, mode.encode("ascii")))
                    message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_with_analysis_mode)


class CorrelationIdMiddleware:
    """Own correlation IDs and emit one content-free JSON record per request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        correlation_id = _correlation_id_from_scope(scope)
        token = _CORRELATION_ID.set(correlation_id)
        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id
        status_code = 500
        response_started = False

        async def send_with_correlation_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                raw_status = message.get("status", 500)
                status_code = raw_status if type(raw_status) is int else 500
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _CORRELATION_ID_HEADER_BYTES
                ]
                headers.append(
                    (
                        _CORRELATION_ID_HEADER_BYTES,
                        correlation_id.encode("ascii"),
                    )
                )
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_correlation_id)
        except Exception:
            _write_request_log(
                scope,
                correlation_id=correlation_id,
                status_code=500,
                event="http_request_failed",
                level=logging.ERROR,
            )
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "O serviço não pôde concluir a requisição.",
                        "issues": [],
                    }
                },
            )
            await response(scope, receive, send_with_correlation_id)
        else:
            _write_request_log(
                scope,
                correlation_id=correlation_id,
                status_code=status_code,
                event="http_request_completed",
                level=logging.INFO,
            )
        finally:
            _reset_correlation_id(token)


def _correlation_id_from_scope(scope: Scope) -> str:
    raw_values: list[bytes] = []
    for name, value in scope.get("headers", ()):
        if type(name) is bytes and name.lower() == _CORRELATION_ID_HEADER_BYTES:
            if type(value) is not bytes:
                return _new_correlation_id()
            raw_values.append(value)
    if len(raw_values) != 1:
        return _new_correlation_id()
    try:
        candidate = raw_values[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return _new_correlation_id()
    return normalize_correlation_id(candidate) or _new_correlation_id()


def _new_correlation_id() -> str:
    return f"req_{uuid4().hex}"


def _reset_correlation_id(token: Token[str | None]) -> None:
    _CORRELATION_ID.reset(token)


def _write_request_log(
    scope: Scope,
    *,
    correlation_id: str,
    status_code: int,
    event: str,
    level: int,
) -> None:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if (
        type(route_path) is not str
        or len(route_path) > 200
        or not route_path.isprintable()
    ):
        route_path = "unmatched"
    raw_method = scope.get("method")
    method = (
        raw_method
        if type(raw_method) is str
        and raw_method
        in {
            "DELETE",
            "GET",
            "HEAD",
            "OPTIONS",
            "PATCH",
            "POST",
            "PUT",
        }
        else "UNKNOWN"
    )
    record = {
        "correlation_id": correlation_id,
        "event": event,
        "method": method,
        "route": route_path,
        "status_code": status_code,
    }
    _REQUEST_LOGGER.log(
        level,
        json.dumps(
            record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
