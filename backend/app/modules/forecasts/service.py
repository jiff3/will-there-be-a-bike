"""Bounded cached count forecasts; inventory is always read afresh, never cached."""

from __future__ import annotations

import json
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.errors import ApplicationError
from app.modules.ml.router import get_forecaster
from app.modules.monitoring.metrics import events
from app.persistence.database import engine
from app.persistence.forecast_repository import measured_inventory

UTC = timezone.utc  # noqa: UP017 -- native QA runner
_counts: OrderedDict[tuple[str, str, datetime], dict[str, Any]] = OrderedDict()
MEMORY_CACHE_LIMIT = 512


def remember(key: tuple[str, str, datetime], payload: dict[str, Any]) -> None:
    # Count forecasts are immutable within an issuance/version. Inventory and risk
    # are NEVER cached here. Copy on both boundaries prevents request contamination.
    _counts[key] = deepcopy(payload)
    _counts.move_to_end(key)
    while len(_counts) > MEMORY_CACHE_LIMIT:
        _counts.popitem(last=False)


async def count_forecast(station: str, at: datetime) -> dict[str, Any]:
    model = get_forecaster()
    version = model.metadata["version"]
    if int(at.timestamp()) < int(datetime.fromisoformat(model.metadata["created_at"]).timestamp()):
        raise ApplicationError(
            "model_not_yet_issued", "First model issuance is the next 15-minute boundary", 503
        )
    params = {"station": station, "version": version, "at": at}
    key = (station, version, at)
    if key in _counts and datetime.now(UTC) < at + timedelta(minutes=30):
        _counts.move_to_end(key)
        events["forecast_memory_hit"] += 1
        return deepcopy(_counts[key])
    async with engine.connect() as connection:
        cached = await connection.scalar(
            text("""
          SELECT payload FROM forecast_cache WHERE station_id=:station AND model_version=:version
          AND as_of=:at AND expires_at>now()
        """),
            params,
        )
    if cached:
        events["forecast_cache_hit"] += 1
        payload = dict(json.loads(cached) if isinstance(cached, str) else cached)
        remember(key, payload)
        return payload
    events["forecast_cache_miss"] += 1
    try:
        payload = await run_in_threadpool(model.predict, station, int(at.timestamp()))
    except (ValueError, RuntimeError) as error:
        events["forecast_inference_failure"] += 1
        raise ApplicationError(
            "model_unavailable", "Demand model inference unavailable", 503
        ) from error
    if any(item["confidence"] == "low" for item in payload["forecasts"].values()):
        events["forecast_low_confidence"] += 1
    async with engine.begin() as connection:
        await connection.execute(
            text("""
          INSERT INTO forecast_cache(station_id,model_version,as_of,expires_at,payload)
          VALUES(:station,:version,:at,:expires,CAST(:payload AS jsonb))
          ON CONFLICT(station_id,model_version,as_of) DO UPDATE SET
          expires_at=EXCLUDED.expires_at,payload=EXCLUDED.payload
        """),
            {**params, "expires": at + timedelta(minutes=30), "payload": json.dumps(payload)},
        )
    remember(key, payload)
    return payload


async def current_forecast(station: str, at: datetime | None = None) -> dict[str, Any]:
    from app.modules.ml.risk import inventory_risk

    now = datetime.now(UTC)
    at = at or now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    payload = await count_forecast(station, at)
    inventory = await measured_inventory(station, at)
    low = any(value["confidence"] == "low" for value in payload["forecasts"].values())
    model = get_forecaster()
    payload["availability_risk"] = await run_in_threadpool(
        inventory_risk,
        payload["forecasts"],
        inventory,
        int(at.timestamp()),
        station,
        model.config.seed,
        low,
    )
    payload["inventory_boundary"] = "reported,updated,and_fetched_no_later_than_forecast_issuance"
    return payload
