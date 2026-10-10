# ruff: noqa: UP017 -- timezone.utc keeps compatibility for local Python 3.10 smoke tests.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.persistence.database import SCHEMA_REVISION, get_session

router = APIRouter()
UTC = timezone.utc


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    timestamp: datetime
    checks: dict[str, Literal["ok", "unavailable"]] | None = None


@router.get("/live", response_model=HealthResponse, summary="Process liveness")
async def liveness() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    summary="Dependency readiness",
)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse | JSONResponse:
    settings = get_settings()
    try:
        result = await asyncio.wait_for(
            session.execute(text("SELECT version_num FROM alembic_version")), timeout=2.0
        )
        if result.scalar_one_or_none() != SCHEMA_REVISION:
            raise SQLAlchemyError("Required schema revision unavailable")
    except (SQLAlchemyError, OSError, asyncio.TimeoutError):  # noqa: UP041 -- Python 3.10 smoke runner
        payload = HealthResponse(
            status="degraded",
            service=settings.app_name,
            version=settings.app_version,
            timestamp=datetime.now(UTC),
            checks={"database": "unavailable"},
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump(mode="json")
        )
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=datetime.now(UTC),
        checks={"database": "ok"},
    )
