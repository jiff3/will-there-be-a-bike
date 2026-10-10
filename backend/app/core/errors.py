from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import get_settings
from app.modules.monitoring import metrics

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", supplied) else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 65536:
                return self.complete(
                    request,
                    JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "payload_too_large",
                                "message": "Request exceeds 64 KiB",
                                "request_id": request_id,
                            }
                        },
                        headers={"X-Request-ID": request_id},
                    ),
                    started,
                    request_id,
                )
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65536:
                    return self.complete(
                        request,
                        JSONResponse(
                            status_code=413,
                            content={
                                "error": {
                                    "code": "payload_too_large",
                                    "message": "Request exceeds 64 KiB",
                                    "request_id": request_id,
                                }
                            },
                            headers={"X-Request-ID": request_id},
                        ),
                        started,
                        request_id,
                    )
                body.extend(chunk)
            request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error(
                "unhandled_request_error",
                extra={"request_id": request_id, "exception_type": type(error).__name__},
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_server_error",
                        "message": "An unexpected error occurred.",
                        "request_id": request_id,
                    }
                },
            )
        return self.complete(request, response, started, request_id)

    def complete(
        self, request: Request, response: Response, started: float, request_id: str
    ) -> Response:
        response.headers["X-Request-ID"] = request_id
        elapsed = (time.perf_counter() - started) * 1000
        route = getattr(request.scope.get("route"), "path", "unmatched")
        metrics.record(route, response.status_code, elapsed)
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "route": route,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round(elapsed, 2),
            },
        )
        response.headers["Server-Timing"] = f"app;dur={elapsed:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        if get_settings().app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(
            ("/api/v1/auth", "/api/v1/commutes", "/api/v1/notifications")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Do not log bound SQL parameters, passwords, tokens or DSNs.
        logger.warning(
            "dependency_unavailable",
            extra={"request_id": _request_id(request), "exception_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "dependency_unavailable",
                    "message": "A required dependency is unavailable",
                    "request_id": _request_id(request),
                }
            },
        )

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(code=exc.code, message=exc.message, request_id=_request_id(request))
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(payload),
            headers={"Retry-After": "900"} if exc.status_code == 429 else None,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(
                code="not_found" if exc.status_code == 404 else "http_error",
                message=str(exc.detail),
                request_id=_request_id(request),
            )
        )
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(payload))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(
                code="validation_error",
                message="The request could not be validated.",
                request_id=_request_id(request),
                # Never echo inputs (especially passwords) or validator exception context.
                details=[
                    {key: value for key, value in item.items() if key in {"loc", "msg", "type"}}
                    for item in exc.errors()
                ],
            )
        )
        return JSONResponse(status_code=422, content=jsonable_encoder(payload))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.error(
            "unhandled_request_error",
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        payload = ErrorResponse(
            error=ErrorDetail(
                code="internal_server_error",
                message="An unexpected error occurred.",
                request_id=request_id,
            )
        )
        return JSONResponse(status_code=500, content=jsonable_encoder(payload))
