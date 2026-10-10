from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.modules.auth.router import require_admin
from app.modules.monitoring import metrics
from app.persistence.operations_repository import status_records

router = APIRouter()


@router.get("/metadata")
async def metadata() -> dict[str, Any]:
    settings = get_settings()
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "api_version": "v1",
        "data_provider": "Synthetic offline demo"
        if settings.demo_mode
        else "Citi Bike GBFS and official trip archives",
        "is_demo": settings.demo_mode,
        "forecast_horizons_minutes": [30, 60],
        "walking_distance": "approximation_not_pedestrian_routing",
        "stockout_probabilities_calibrated": False,
    }


@router.get("/operations/status")
async def operational_status(
    _: Annotated[dict[str, Any], Depends(require_admin)],
) -> dict[str, Any]:
    records = await status_records()
    return {
        **records,
        "metrics_scope": "this_worker_since_start",
        "request_counts": dict(metrics.requests),
        "events": dict(metrics.events),
        "duration_ms_sum": dict(metrics.durations_ms),
        "background_jobs_enabled": get_settings().background_jobs_enabled,
    }
