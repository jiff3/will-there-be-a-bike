from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import ApplicationError
from app.modules.auth.router import Preferences
from app.modules.auth.security import hash_password, verify_password
from app.modules.recommendations import router as recommendations
from app.modules.stations import router as stations

UTC = timezone.utc  # noqa: UP017 -- native QA runner


def observation(**overrides: Any) -> dict[str, Any]:
    return {
        "external_id": "A",
        "name": "Synthetic unit fixture",
        "capacity": 20,
        "latitude": 40.748,
        "longitude": -73.985,
        "reported_at": datetime.now(UTC),
        "feed_updated_at": datetime.now(UTC),
        "fetched_at": datetime.now(UTC),
        "walking_meters": 200,
        "stale": False,
        "is_demo": False,
        "bikes": 5,
        "docks": 15,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        **overrides,
    }


def forecast(low: bool = False) -> dict[str, Any]:
    return {
        "model_version": "unit-fixture",
        "forecasts": {
            f"{direction}_{horizon}": {
                "mean": 4 if direction == "departures" else 1,
                "confidence": "low" if low else "evaluated_winter_context",
            }
            for direction in ("departures", "arrivals")
            for horizon in (30, 60)
        },
    }


async def test_argon2_and_dummy_verification() -> None:
    encoded = await hash_password("a long unique password")
    assert encoded.startswith("$argon2id$")
    assert await verify_password(encoded, "a long unique password")
    assert not await verify_password(encoded, "incorrect password")
    assert not await verify_password(None, "a long unique password")


@pytest.mark.parametrize(
    "overrides",
    [{"stale": True}, {"is_demo": True}, {"bikes": None, "docks": None}, {"is_renting": False}],
)
def test_recommendations_do_not_invent_inventory(overrides: dict[str, Any]) -> None:
    ranked = recommendations.rank_station(
        observation(**overrides), "pickup", 30, forecast(), Preferences()
    )
    assert ranked["estimated_usable_inventory"] is None
    assert ranked["mode"] == "distance_only"


def test_recommendation_ranking_and_time_horizon() -> None:
    usable = recommendations.rank_station(observation(), "pickup", 30, forecast(), Preferences())
    empty = recommendations.rank_station(
        observation(bikes=0, walking_meters=0), "pickup", 30, forecast(), Preferences()
    )
    assert usable["eligibility_tier"] < empty["eligibility_tier"]
    assert usable["estimated_usable_inventory"] == 2
    assert not usable["calibrated_stockout_probability"]
    for model, minutes in ((forecast(True), 30), (None, 30), (forecast(), 90)):
        result = recommendations.rank_station(
            observation(), "destination", minutes, model, Preferences()
        )
        assert result["estimated_usable_inventory"] is None
        assert result["mode"] == "live_inventory_only"


def test_recommendations_bound_issuance_horizon_and_elapsed_demand() -> None:
    future = forecast()
    for direction in ("arrivals", "departures"):
        future["forecasts"][f"{direction}_60"]["mean"] *= 2
    result = recommendations.rank_station(observation(), "pickup", 30, future, Preferences(), 10)
    assert result["estimated_usable_inventory"] == 2
    result = recommendations.rank_station(observation(), "pickup", 55, future, Preferences(), 10)
    assert result["estimated_usable_inventory"] is None
    future["forecasts"]["departures_60"]["mean"] = 0
    result = recommendations.rank_station(observation(), "pickup", 30, future, Preferences())
    assert result["mode"] == "live_inventory_only"


async def test_recommendation_model_failure_fallback(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nearby(*args: Any) -> list[dict[str, Any]]:
        return [observation()]

    async def unavailable(*args: Any) -> dict[str, Any]:
        raise ApplicationError("model_unavailable", "Unavailable", 503)

    monkeypatch.setattr(recommendations, "nearby", nearby)
    monkeypatch.setattr(recommendations, "count_forecast", unavailable)
    result = await client.post(
        "/api/v1/recommendations/trip",
        json={
            "origin": {"latitude": 40.748, "longitude": -73.985},
            "destination": {"latitude": 40.75, "longitude": -73.99},
            "departure_time": datetime.now(UTC).isoformat(),
        },
    )
    assert result.status_code == 200
    assert result.json()["pickup"][0]["mode"] == "live_inventory_only"
    assert "model_unavailable_using_measured_inventory_or_distance" in result.json()["warnings"]


@pytest.mark.parametrize("bbox", ["1,2,3", "nan,1,2,3", "0,90,1,91", "2,0,1,2", "a,b,c,d"])
async def test_station_bounds_validation(client: AsyncClient, bbox: str) -> None:
    result = await client.get("/api/v1/stations", params={"bbox": bbox})
    assert result.status_code == 422 and result.json()["error"]["code"] == "invalid_bbox"


async def test_dependency_errors_and_secret_redaction(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OperationalError("secret sql", {"password": "do-not-expose"}, Exception())

    monkeypatch.setattr(stations, "catalog", fail)
    result = await client.get("/api/v1/stations", headers={"X-Request-ID": "safe-id"})
    assert result.status_code == 503 and result.headers["X-Request-ID"] == "safe-id"
    assert "secret sql" not in result.text and "do-not-expose" not in result.text
    invalid = await client.post(
        "/api/v1/auth/register", json={"email": "bad", "password": "secret"}
    )
    assert invalid.status_code == 422 and "secret" not in invalid.text
    assert "input" not in invalid.json()["error"]["details"][0]


async def test_csrf_origin_and_request_bounds(client: AsyncClient) -> None:
    body = {"email": "user@example.com", "password": "long password safe"}
    result = await client.post(
        "/api/v1/auth/login", json=body, headers={"Origin": "https://evil.example"}
    )
    assert result.status_code == 403
    large = await client.post("/api/v1/auth/login", content="x" * 65537)
    assert large.status_code == 413
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/operations/status")).status_code == 401


async def test_unhandled_error_keeps_request_context(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("secret-internal-details")

    monkeypatch.setattr(stations, "catalog", fail)
    response = await client.get("/api/v1/stations")
    assert response.status_code == 500 and "secret-internal-details" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "Server-Timing" in response.headers


async def test_time_validation_and_openapi(client: AsyncClient) -> None:
    result = await client.post(
        "/api/v1/recommendations/trip",
        json={
            "origin": {"latitude": 40.7, "longitude": -74},
            "destination": {"latitude": 40.8, "longitude": -74},
            "departure_time": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert result.status_code == 422
    schema = (await client.get("/openapi.json")).json()
    assert "/api/v1/auth/login" in schema["paths"]
    assert schema["paths"]["/api/v1/stations"]["get"]["responses"]["503"]


def test_production_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(app_env="production")
    with pytest.raises(ValueError, match="explicit origins"):
        Settings(app_cors_origins=["*"])
