from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    ApplicationError,
    ErrorResponse,
    RequestContextMiddleware,
    install_exception_handlers,
)
from app.core.frontend import install_frontend
from app.core.logging import configure_logging
from app.modules.ml.router import get_forecaster
from app.modules.monitoring.jobs import worker
from app.persistence.database import engine

settings = get_settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Load native libraries/artifact off the request event loop before accepting
    # traffic. Missing models must not prevent station/auth APIs from starting.
    try:
        await run_in_threadpool(get_forecaster)
    except ApplicationError as error:
        logging.getLogger(__name__).warning("startup_model_unavailable", extra={"code": error.code})
    stop = asyncio.Event()
    task = asyncio.create_task(worker(stop)) if settings.background_jobs_enabled else None
    try:
        yield
    finally:
        stop.set()
        if task:
            try:
                await asyncio.wait_for(task, timeout=20)
            except asyncio.TimeoutError:  # noqa: UP041
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.app_debug,
        docs_url="/docs" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        responses={
            code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 422, 429, 503)
        },
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID", "Server-Timing"],
    )
    application.add_middleware(RequestContextMiddleware)
    install_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    install_frontend(application, settings.frontend_dist_path)
    return application


app = create_app()
