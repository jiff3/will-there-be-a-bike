from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ForecastItem(BaseModel):
    mean: float
    interval: list[int]
    interval_nominal_coverage: float
    interval_method: str
    interval_validated_for_this_context: bool
    model: str
    feature_mode: str
    confidence: str
    prior_day_available: bool
    prior_week_available: bool
    dispersion_alpha: float
    drivers: list[dict[str, Any]]


class ForecastResponse(BaseModel):
    station_id: str
    as_of: datetime
    model_version: str
    is_demo: bool
    retrospective_replay: bool
    training_age_days: float
    warnings: list[str]
    forecasts: dict[str, ForecastItem]
    availability_risk: dict[str, Any]
    target_semantics: str
    inventory_boundary: str | None = None
