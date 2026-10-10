"""Transparent station alternatives, not turn-by-turn routes or availability guarantees."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.errors import ApplicationError
from app.modules.auth.router import Preferences
from app.modules.forecasts.service import count_forecast
from app.modules.ingestion.live import walking_distance
from app.modules.ingestion.persistence import nearby
from app.modules.ingestion.router import NearbyStation

UTC = timezone.utc  # noqa: UP017 -- native QA runner
router = APIRouter()


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Point
    destination: Point
    departure_time: AwareDatetime
    preferences: Preferences = Field(default_factory=Preferences)
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def bounded_time(self) -> TripRequest:
        delay = (self.departure_time - datetime.now(UTC)).total_seconds()
        if not -60 <= delay <= 3600:
            raise ValueError("departure_time must be now through one hour ahead")
        return self


def rank_station(
    row: dict[str, Any],
    purpose: Literal["pickup", "destination"],
    minutes: float,
    forecast: dict[str, Any] | None,
    preferences: Preferences,
    issuance_age_minutes: float = 0,
) -> dict[str, Any]:
    inventory_key = "bikes" if purpose == "pickup" else "docks"
    flag = "is_renting" if purpose == "pickup" else "is_returning"
    fresh = not row["stale"] and not row["is_demo"] and row.get("bikes") is not None
    operational = row.get("is_installed") is not False and row.get(flag) is not False
    measured = row.get(inventory_key) if fresh and operational else None
    reasons = [f"Approximately {round(row['walking_meters'])} m walking; not pedestrian routing"]
    expected = None
    mode = "distance_only"
    inventory_score = 0.0
    if measured is not None:
        mode = "live_inventory_only"
        inventory_score = min(measured / 5, 1)
        reasons.append(f"{measured} measured {inventory_key} at last fresh report")
    else:
        reasons.append("Live inventory is missing, stale, synthetic or operationally unavailable")
    if not operational:
        reasons.append("Station is not currently accepting this operation")
    predictions = forecast.get("forecasts", {}) if forecast else {}
    confident = bool(predictions) and all(
        value["confidence"] != "low" for value in predictions.values()
    )
    target_minutes = issuance_age_minutes + minutes
    coherent = confident and all(
        predictions[f"{direction}_60"]["mean"] >= predictions[f"{direction}_30"]["mean"]
        for direction in ("arrivals", "departures")
    )
    if measured is not None and coherent and 0 <= issuance_age_minutes <= target_minutes <= 60:

        def cumulative(direction: str, minute: float) -> float:
            first = float(predictions[f"{direction}_30"]["mean"])
            full = float(predictions[f"{direction}_60"]["mean"])
            return (
                first * minute / 30 if minute <= 30 else first + (full - first) * (minute - 30) / 30
            )

        arrivals = cumulative("arrivals", target_minutes) - cumulative(
            "arrivals", issuance_age_minutes
        )
        departures = cumulative("departures", target_minutes) - cumulative(
            "departures", issuance_age_minutes
        )
        usable = row["bikes"] + row["docks"]
        bikes = max(0, min(usable, row["bikes"] + arrivals - departures))
        expected = bikes if purpose == "pickup" else usable - bikes
        mode = "uncalibrated_inventory_scenario"
        inventory_score = min(measured / 5, 1) * 0.4 + min(expected / 5, 1) * 0.6
        reasons.append(
            "Estimated inventory combines observed rides with current usable slots; no guarantee"
        )
    elif not confident:
        reasons.append("Forecast unavailable or low confidence; future inventory is not estimated")
    elif not coherent:
        reasons.append("Incoherent forecast horizons; future inventory is not estimated")
    elif target_minutes > 60:
        reasons.append("Arrival is beyond the evaluated 60-minute forecast horizon")
    distance_score = max(0, 1 - row["walking_meters"] / preferences.max_walk_meters)
    weight = preferences.reliability_weight
    score = (1 - weight) * distance_score + weight * inventory_score
    # Known-closed/empty stations cannot beat a genuinely usable alternative just on distance.
    tier = (
        0 if measured is not None and measured > 0 else 1 if measured is None and operational else 2
    )
    return {
        **row,
        "score": round(score, 6),
        "eligibility_tier": tier,
        "estimated_usable_inventory": round(expected, 2) if expected is not None else None,
        "estimate_minutes_ahead": round(minutes, 1),
        "mode": mode,
        "reasons": reasons,
        "model_version": forecast.get("model_version") if forecast else None,
        "calibrated_stockout_probability": False,
    }


class Alternative(NearbyStation):
    score: float
    eligibility_tier: int
    estimated_usable_inventory: float | None
    estimate_minutes_ahead: float
    mode: str
    reasons: list[str]
    model_version: str | None
    calibrated_stockout_probability: Literal[False]


class TripResponse(BaseModel):
    departure_time: datetime
    generated_at: datetime
    pickup: list[Alternative]
    destination: list[Alternative]
    approximate_cycling_minutes: float
    warnings: list[str]
    distance_method: str
    travel_time_method: str
    future_inventory_method: str
    preferences: Preferences


@router.post("/trip", response_model=TripResponse)
async def recommend(body: TripRequest) -> dict[str, Any]:
    now = datetime.now(UTC)
    at = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    radius = math.ceil(body.preferences.max_walk_meters / 1.3)
    pickup = await nearby(body.origin.latitude, body.origin.longitude, radius, 30)
    destination = await nearby(body.destination.latitude, body.destination.longitude, radius, 30)
    # A transparent travel-time heuristic; never label it a routed journey time.
    trip_minutes = (
        walking_distance(
            body.origin.latitude,
            body.origin.longitude,
            body.destination.latitude,
            body.destination.longitude,
        )
        / 1.3
        * 1.2
        / 250
    )
    delay = max(0, (body.departure_time - now).total_seconds() / 60)
    warnings = []
    ranked: dict[str, list[dict[str, Any]]] = {}
    forecasts: dict[str, dict[str, Any] | None] = {}
    model_unavailable = False
    candidate_groups: list[tuple[Literal["pickup", "destination"], list[dict[str, Any]]]] = [
        ("pickup", pickup),
        ("destination", destination),
    ]
    for purpose, rows in candidate_groups:
        alternatives = []
        for row in rows:
            if row["walking_meters"] > body.preferences.max_walk_meters:
                continue
            station = row["external_id"]
            if station not in forecasts:
                forecasts[station] = None
                try:
                    if not model_unavailable:
                        forecasts[station] = await count_forecast(station, at)
                except ApplicationError as error:
                    if error.status_code != 503:
                        raise
                    forecasts[station] = None
                    model_unavailable = True
                    warnings.append("model_unavailable_using_measured_inventory_or_distance")
            minutes = delay if purpose == "pickup" else delay + trip_minutes
            alternatives.append(
                rank_station(
                    row,
                    purpose,
                    minutes,
                    forecasts[station],
                    body.preferences,
                    (now - at).total_seconds() / 60,
                )
            )
        ranked[purpose] = sorted(
            alternatives,
            key=lambda item: (item["eligibility_tier"], -item["score"], item["external_id"]),
        )[: body.limit]
    if not ranked["pickup"] or not ranked["destination"]:
        warnings.append("no_station_within_requested_walking_limit")
    if any(item["mode"] == "distance_only" for rows in ranked.values() for item in rows):
        warnings.append("some_alternatives_have_no_reliable_inventory")
    return {
        "departure_time": body.departure_time,
        "generated_at": now,
        "pickup": ranked["pickup"],
        "destination": ranked["destination"],
        "approximate_cycling_minutes": round(trip_minutes, 1),
        "warnings": sorted(set(warnings)),
        "distance_method": "great_circle_times_1.3_not_verified_pedestrian_routing",
        "travel_time_method": "great_circle_times_1.2_at_15_kmh_not_routing",
        "future_inventory_method": (
            "piecewise_linear_issuance_counts_minus_elapsed_counts; current_report_approximation"
        ),
        "preferences": body.preferences.model_dump(),
    }
