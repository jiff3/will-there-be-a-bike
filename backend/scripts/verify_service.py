"""Real HTTP and persisted-data verification; run only against an isolated local DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.persistence.database import engine  # noqa: E402

UTC = timezone.utc  # noqa: UP017 -- native QA runner


async def verify(url: str, output: Path) -> None:
    email = f"http-validation-{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex + uuid.uuid4().hex
    report: dict[str, object] = {"synthetic_accounts_only": True, "station_measurements_real": True}
    report["verification_runtime"] = {
        "python": platform.python_version(),
        **{name: version(name) for name in ("fastapi", "sqlalchemy", "asyncpg", "lightgbm")},
    }
    try:
        async with httpx.AsyncClient(base_url=url, timeout=30) as client:
            for route in (
                "/api/v1/health/live",
                "/api/v1/health/ready",
                "/api/v1/metadata",
                "/openapi.json",
            ):
                result = await client.get(route)
                assert result.status_code == 200, (route, result.text)
            page = await client.get(
                "/api/v1/stations", params={"limit": 10, "bbox": "-74.1,40.6,-73.8,40.9"}
            )
            assert page.status_code == 200, page.text
            stations = page.json()
            assert len(stations["items"]) == 10 and stations["next_offset"] == 10
            station = stations["items"][0]
            identifier = station["external_id"]
            assert not station["is_demo"] and station["bikes"] is not None
            report["measured_station"] = station
            nearby = await client.get(
                "/api/v1/stations/nearby",
                params={"lat": station["latitude"], "lon": station["longitude"]},
            )
            assert nearby.status_code == 200 and nearby.json()[0]["walking_meters"] < 0.1
            history = await client.get(
                f"/api/v1/stations/{identifier}/demand",
                params={"start": "2025-01-15T00:00:00-05:00", "end": "2025-01-16T00:00:00-05:00"},
            )
            assert history.status_code == 200, history.text
            assert history.json()["observations"] and len(history.json()["sources"]) == 2
            report["historical_observations_checked"] = len(history.json()["observations"])
            for suffix in ("", "/availability", "/predictions"):
                result = await client.get(f"/api/v1/stations/{identifier}{suffix}")
                assert result.status_code == 200, result.text
                if suffix == "/predictions":
                    report["forecast"] = result.json()
                    assert len(result.json()["forecasts"]) == 4
                    assert result.json()["availability_risk"]["status"] == "unavailable"
            result = await client.post(
                "/api/v1/recommendations/trip",
                json={
                    "origin": {"latitude": 40.748, "longitude": -73.985},
                    "destination": {"latitude": 40.755, "longitude": -73.99},
                    "departure_time": datetime.now(UTC).isoformat(),
                    "limit": 3,
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["pickup"] and result.json()["destination"]
            report["recommendations"] = result.json()
            assert (await client.get("/api/v1/operations/status")).status_code == 401
            origin = {"Origin": get_settings().app_public_origin}
            credentials = {"email": email, "password": password}
            result = await client.post("/api/v1/auth/register", json=credentials, headers=origin)
            assert result.status_code == 201, result.text
            csrf = result.json()["csrf_token"]
            assert "HttpOnly" in result.headers["set-cookie"]
            assert (await client.get("/api/v1/operations/status")).status_code == 403
            headers = {**origin, "X-CSRF-Token": csrf}
            result = await client.put(
                "/api/v1/auth/preferences", json={"max_walk_meters": 700}, headers=headers
            )
            assert result.status_code == 200
            assert (await client.get("/api/v1/auth/preferences")).json()["max_walk_meters"] == 700
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE users SET is_admin=true WHERE email=:email"), {"email": email}
                )
            result = await client.get("/api/v1/operations/status")
            assert result.status_code == 200, result.text
            report["operations"] = result.json()
            assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
            assert (await client.get("/api/v1/auth/me")).status_code == 401
            result = await client.post("/api/v1/auth/login", json=credentials, headers=origin)
            assert result.status_code == 200, result.text
            assert (await client.get("/api/v1/auth/me")).status_code == 200
        async with engine.connect() as connection:
            counts = {}
            for table in (
                "stations",
                "availability_snapshots",
                "historical_trips",
                "historical_demand",
                "ingestion_runs",
                "forecast_cache",
            ):
                counts[table] = await connection.scalar(text(f"SELECT count(*) FROM {table}"))
            assert counts["stations"] == 100 and counts["historical_trips"] == 950604
            assert counts["ingestion_runs"] == 2
            report["persisted_counts"] = counts
            report["postgres_version"] = await connection.scalar(text("SELECT version()"))
            report["postgis_version"] = await connection.scalar(
                text("SELECT PostGIS_Full_Version()")
            )
            report["historical_totals"] = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT sum(departures) departures,sum(arrivals) arrivals "
                            "FROM historical_demand"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert report["historical_totals"] == {"departures": 611768, "arrivals": 612709}
        report["verified_at"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            output.write_text, json.dumps(report, default=str, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "report": str(output),
                    "persisted_counts": report["persisted_counts"],
                    "http_checks": "passed",
                }
            )
        )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE email=:email"), {"email": email})
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("../data/validation/service.json"))
    args = parser.parse_args()
    asyncio.run(verify(args.base_url, args.output))
