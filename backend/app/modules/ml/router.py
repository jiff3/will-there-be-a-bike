"""Forecasts consume measured database inventory, never user-invented stockout labels."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.persistence.forecast_repository import measured_inventory

if TYPE_CHECKING:
    from app.modules.ml.inference import ForecastEngine

UTC = timezone.utc  # noqa: UP017 -- native Python 3.10 QA

router = APIRouter()
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_forecaster() -> ForecastEngine:
    try:
        from app.modules.ml.inference import ForecastEngine

        return ForecastEngine.load(get_settings().ml_artifact_path)
    except Exception as error:
        logger.exception("model_artifact_unavailable")
        raise ApplicationError(
            "model_unavailable", "Approved model artifact unavailable", 503
        ) from error


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: str = Field(min_length=1, max_length=128)
    as_of: AwareDatetime | None = None

    @field_validator("as_of")
    @classmethod
    def aligned(cls, value: datetime | None) -> datetime | None:
        if value and (value.second or value.microsecond or int(value.timestamp()) % 900):
            raise ValueError("as_of must be on a 15-minute issuance boundary")
        return value


@router.get("/model")
def model_card() -> dict[str, Any]:
    model = get_forecaster()
    return {
        "version": model.metadata["version"],
        "created_at": model.metadata["created_at"],
        "is_demo": model.metadata["is_demo"],
        "source": model.metadata["source"],
        "config": model.metadata["config"],
        "tasks": model.metadata["tasks"],
        "evaluation_semantics": model.metadata["evaluation_semantics"],
        "calibrated_stockout_probability": False,
    }


@router.post("/predict")
async def forecast(request: ForecastRequest) -> dict[str, Any]:
    now = datetime.now(UTC)
    at = request.as_of or now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    if at > now or (now - at).total_seconds() > 1800:
        raise ApplicationError(
            "invalid_as_of", "API supports current/recent issuance only; use CLI for replay"
        )
    model = get_forecaster()
    if int(at.timestamp()) < int(datetime.fromisoformat(model.metadata["created_at"]).timestamp()):
        raise ApplicationError(
            "model_not_yet_issued", "First model issuance is the next 15-minute boundary", 503
        )
    inventory = await measured_inventory(request.station_id, at)
    try:
        return await run_in_threadpool(
            model.predict, request.station_id, int(at.timestamp()), None, inventory
        )
    except ValueError as error:
        raise ApplicationError("invalid_forecast_context", str(error)) from error
