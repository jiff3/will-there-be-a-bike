from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.errors import ApplicationError
from app.modules.ml import router

pytest.importorskip("lightgbm")


async def test_real_packaged_model_card(client: AsyncClient) -> None:
    response = await client.get("/api/v1/forecasts/model")
    assert response.status_code == 200
    body = response.json()
    assert body["version"].startswith("v1-") and not body["is_demo"]
    assert not body["calibrated_stockout_probability"]
    assert body["source"]["station_count"] == 100


async def test_api_contract_and_read_only_inventory_boundary(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(router.UTC)
    at = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    seen = []

    async def snapshot(station: str, as_of: datetime) -> None:
        seen.append((station, as_of))
        return None

    # Exercise the genuine engine; move only its creation timestamp so a new local build
    # can be tested immediately rather than waiting for the next issuance boundary.
    engine = router.get_forecaster()
    previous = engine.metadata["created_at"]
    engine.metadata["created_at"] = (at - timedelta(minutes=15)).isoformat()
    monkeypatch.setattr(router, "measured_inventory", snapshot)
    try:
        response = await client.post("/api/v1/forecasts/predict", json={"station_id": "UNKNOWN"})
    finally:
        engine.metadata["created_at"] = previous
    assert response.status_code == 200
    body = response.json()
    assert body["availability_risk"]["status"] == "unavailable"
    assert body["warnings"] and all(v["confidence"] == "low" for v in body["forecasts"].values())
    assert seen == [("UNKNOWN", at)]


@pytest.mark.parametrize("stamp", ["2025-01-01T08:00:00", "2025-01-01T08:03:00-05:00"])
async def test_invalid_time_and_fake_inventory_rejected(client: AsyncClient, stamp: str) -> None:
    result = await client.post(
        "/api/v1/forecasts/predict", json={"station_id": "A", "as_of": stamp}
    )
    assert result.status_code == 422
    result = await client.post("/api/v1/forecasts/predict", json={"station_id": "A", "bikes": 50})
    assert result.status_code == 422


async def test_inventory_database_failure_is_structured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stub:
        metadata = {"created_at": "2020-01-01T00:00:00+00:00"}

    async def unavailable(station: str, at: datetime) -> dict[str, Any] | None:
        raise ApplicationError("inventory_unavailable", "Inventory database unavailable", 503)

    monkeypatch.setattr(router, "get_forecaster", lambda: Stub())
    monkeypatch.setattr(router, "measured_inventory", unavailable)
    response = await client.post("/api/v1/forecasts/predict", json={"station_id": "A"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "inventory_unavailable"
