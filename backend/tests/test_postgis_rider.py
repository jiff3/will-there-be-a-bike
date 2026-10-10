"""Real PostgreSQL ownership, notification concurrency and atomic replay publication."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.commutes import alerts
from app.modules.ingestion import persistence
from app.modules.replay import service
from app.modules.replay.cli import publish
from app.persistence.database import engine
from app.persistence.rider_tables import replay_imports
from tests.test_rider_features import config, observation

pytestmark = pytest.mark.skipif(
    os.environ.get("APP_ENV") != "test", reason="Requires migrated PostGIS test DB"
)
ORIGIN = {"Origin": "http://localhost:5173"}


async def test_rider_private_crud_alert_evaluation_and_history(client, monkeypatch):
    suffix = uuid.uuid4().hex
    emails = [f"rider-{suffix}@example.com", f"other-rider-{suffix}@example.com"]
    external = "rider-fixture-" + suffix
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM request_limits"))
    await persistence.update_stations(
        {
            "data": {
                "stations": [
                    {
                        "station_id": external,
                        "name": "Synthetic isolated test",
                        "lat": 1,
                        "lon": 1,
                        "capacity": 20,
                    }
                ]
            }
        }
    )
    try:
        assert (await client.get("/api/v1/commutes")).status_code == 401
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": emails[0], "password": "Synthetic isolated test password"},
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        headers = {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
        now = datetime(2025, 2, 28, 13, 10, tzinfo=UTC)
        body = config(departure_local="08:30", weekdays=[5])
        assert (await client.post("/api/v1/commutes", json=body, headers=ORIGIN)).status_code == 403
        assert (
            await client.post(
                "/api/v1/commutes", json={**body, "user_id": "spoof"}, headers=headers
            )
        ).status_code == 422
        result = await client.post("/api/v1/commutes", json=body, headers=headers)
        assert result.status_code == 201, result.text
        commute = result.json()["id"]
        assert "no-store" in result.headers["cache-control"]
        path = f"/api/v1/commutes/{commute}"
        assert (await client.get(path)).json()["departure_local"] == "08:30"
        revised = {
            **body,
            "name": "Edited ride",
            "preferences": {"max_walk_meters": 700, "reliability_weight": 0.7},
        }
        assert (await client.put(path, json=revised, headers=headers)).status_code == 200
        assert (await client.get(path)).json()["preferences"]["max_walk_meters"] == 700
        # Actual recommendation code, empty isolated geography is a useful valid result.
        recommended = await client.get(path + "/recommendations")
        assert recommended.status_code == 200, recommended.text
        alert = {
            "station_id": external,
            "kind": "pickup_empty",
            "mode": "inventory",
            "threshold": 3,
            "lead_minutes": 30,
            "cooldown_minutes": 15,
            "enabled": True,
        }
        result = await client.post(path + "/alerts", json=alert, headers=headers)
        assert result.status_code == 201, result.text
        rule_id = result.json()["id"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
            auth = await other.post(
                "/api/v1/auth/register",
                json={"email": emails[1], "password": "Another synthetic test password"},
                headers=ORIGIN,
            )
            other_headers = {**ORIGIN, "X-CSRF-Token": auth.json()["csrf_token"]}
            assert (await other.get("/api/v1/commutes")).json()["items"] == []
            for method, url, payload in [
                ("GET", path, None),
                ("PUT", path, body),
                ("DELETE", path, None),
                ("GET", path + "/alerts", None),
                ("POST", path + "/alerts", alert),
                ("PUT", path + "/alerts/" + rule_id, alert),
                ("DELETE", path + "/alerts/" + rule_id, None),
                ("GET", path + "/recommendations", None),
            ]:
                assert (
                    await other.request(method, url, json=payload, headers=other_headers)
                ).status_code == 404

            async def measured(_):
                return {**observation(now), "external_id": external}

            monkeypatch.setattr(alerts, "station", measured)
            # Competing evaluation is serialized by row locks and unique occurrence.
            await asyncio.gather(alerts.evaluate_alerts(now), alerts.evaluate_alerts(now))
            inbox = (await client.get("/api/v1/notifications")).json()
            assert inbox["unread_count"] == 1 and len(inbox["items"]) == 1
            notification = inbox["items"][0]["id"]
            assert (
                await other.patch(
                    "/api/v1/notifications/" + notification,
                    json={"read": True},
                    headers=other_headers,
                )
            ).status_code == 404
            assert (
                await client.patch(
                    "/api/v1/notifications/" + notification, json={"read": True}, headers=headers
                )
            ).status_code == 204
            assert (await client.get("/api/v1/notifications")).json()["unread_count"] == 0
            assert (
                await client.patch(
                    "/api/v1/notifications/" + notification, json={"read": False}, headers=headers
                )
            ).status_code == 204
            # After cooldown, identical departure still cannot produce a duplicate.
            monkeypatch.setattr(alerts, "station", lambda _: measured(None))
            async with engine.connect() as conn:
                row = dict(
                    (
                        await conn.execute(
                            text(
                                "SELECT a.*,c.user_id,c.config FROM availability_alerts a "
                                "JOIN saved_commutes c ON c.id=a.commute_id WHERE a.id=:id"
                            ),
                            {"id": uuid.UUID(rule_id)},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert (
                await alerts.record(
                    row,
                    "triggered",
                    inbox["items"][0]["evidence"],
                    datetime(2025, 2, 28, 13, 30, tzinfo=UTC),
                    now + timedelta(minutes=16),
                )
                == "already_notified"
            )
            # Pausing and editing persist, next occurrence can notify only when enabled.
            paused = await client.put(
                path + "/alerts/" + rule_id, json={**alert, "enabled": False}, headers=headers
            )
            assert paused.status_code == 200 and paused.json()["enabled"] is False
            await alerts.evaluate_alerts(now + timedelta(days=7))
            assert (await client.get("/api/v1/notifications")).json()["unread_count"] == 1
            # Four-rule cap is transactionally enforced; disabled rules stay disabled.
            for _ in range(3):
                assert (
                    await client.post(
                        path + "/alerts", json={**alert, "enabled": False}, headers=headers
                    )
                ).status_code == 201
            assert (
                await client.post(path + "/alerts", json=alert, headers=headers)
            ).status_code == 409
            assert (
                await client.delete(path + "/alerts/" + rule_id, headers=headers)
            ).status_code == 204
            assert len((await client.get(path + "/alerts")).json()["items"]) == 3
            # Delete cascade rules, retain private notification history.
            assert (await client.delete(path, headers=headers)).status_code == 204
            assert (await client.get(path)).status_code == 404
            history = (await client.get("/api/v1/notifications")).json()
            assert history["items"][0]["rule_id"] is None
            assert (await other.get("/api/v1/notifications")).json()["items"] == []
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE email=ANY(CAST(:emails AS text[]))"),
                {"emails": emails},
            )
            await conn.execute(
                text(
                    "DELETE FROM station_aliases WHERE station_id IN "
                    "(SELECT id FROM stations WHERE external_id=:id)"
                ),
                {"id": external},
            )
            await conn.execute(text("DELETE FROM stations WHERE external_id=:id"), {"id": external})
            await conn.execute(text("DELETE FROM request_limits"))


async def test_replay_atomic_idempotent_null_counts_and_empty(client, tmp_path, monkeypatch):
    suffix = uuid.uuid4().hex
    external = "replay-fixture-" + suffix
    await persistence.update_stations(
        {
            "data": {
                "stations": [
                    {
                        "station_id": external,
                        "name": "Synthetic isolated replay test",
                        "lat": 1,
                        "lon": 1,
                        "capacity": 20,
                    }
                ]
            }
        }
    )
    async with engine.connect() as conn:
        previous = await conn.scalar(select(replay_imports.c.id).where(replay_imports.c.active))
    created = None
    try:
        data = tmp_path / "synthetic-test-only.jsonl"
        row = {
            "station_id": external,
            "bucket_start": "2025-03-09T07:00:00Z",
            "is_demo": False,
            "departures": 1,
            "arrivals": 0,
            "departure_coverage": False,
            "arrival_coverage": True,
        }
        rows = [
            {
                **row,
                "bucket_start": (
                    datetime(2025, 3, 9, 7, tzinfo=UTC) + timedelta(minutes=15 * i)
                ).isoformat(),
            }
            for i in range(4)
        ]
        data.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
        result = await publish(data)
        created = uuid.UUID(result["id"])
        assert result["rows"] == 4
        assert (await publish(data))["status"] == "already_published"
        response = await client.get("/api/v1/replay/catalog")
        assert response.status_code == 200, response.text
        assert response.json()["days"] == ["2025-03-09"]
        response = await client.get("/api/v1/replay/day", params={"day": "2025-03-09"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["buckets"]) == 92
        assert payload["series"][0]["departures"][8] is None
        assert payload["series"][0]["arrivals"][8] == 0
        assert payload["series"][0]["arrivals"][0] is None
        assert "bikes" not in payload["stations"][0]
        assert (await client.get("/api/v1/replay/day", params={"day": "2025-03-10"})).json()[
            "buckets"
        ] == []
        assert (
            await client.get("/api/v1/replay/day", params={"day": "invalid"})
        ).status_code == 422
        assert (
            await client.get(
                f"/api/v1/replay/stations/{external}/comparisons", params={"day": "2025-03-09"}
            )
        ).json()["items"] == []
        from datetime import date

        from app.modules.replay import cli

        calls = []

        class SyntheticModel:
            metadata = {"data_sha256": result["source_sha256"], "version": "synthetic-test-only"}

            class config:
                @staticmethod
                def boundaries():
                    return [
                        0,
                        0,
                        0,
                        datetime(2025, 3, 1, tzinfo=UTC).timestamp(),
                        datetime(2025, 4, 1, tzinfo=UTC).timestamp(),
                    ]

            def predict(self, station, at, **kwargs):
                calls.append((station, at, kwargs))
                return {
                    "forecasts": {
                        f"{direction}_{minutes}": {"mean": 1, "interval": [0, 3]}
                        for direction in ("departures", "arrivals")
                        for minutes in (30, 60)
                    }
                }

        monkeypatch.setattr(cli, "get_forecaster", lambda: SyntheticModel())
        with pytest.raises(ValueError, match="held-out"):
            await cli.backtest(date(2025, 2, 28), [external])
        assert (await cli.backtest(date(2025, 3, 9), [external]))["stored"] == 4
        assert all(kwargs == {"replay": True} for _, _, kwargs in calls)
        assert (await cli.backtest(date(2025, 3, 9), [external]))["stored"] == 0
        compared = (
            await client.get(
                f"/api/v1/replay/stations/{external}/comparisons", params={"day": "2025-03-09"}
            )
        ).json()["items"]
        assert compared[0]["observed"]["arrivals_60"] == 0
        assert compared[0]["observed"]["departures_30"] is None
        assert compared[-1]["observed"]["arrivals_30"] is None
        duplicate = tmp_path / "duplicate.jsonl"
        duplicate.write_text((json.dumps(row) + "\n") * 2, encoding="utf-8")
        with pytest.raises(IntegrityError):
            await publish(duplicate)
        assert (await service.catalog())["dataset"]["id"] == created
        async with engine.begin() as conn:
            await conn.execute(update(replay_imports).values(active=False))
        assert (await client.get("/api/v1/replay/catalog")).json()["days"] == []
        assert (await client.get("/api/v1/replay/day", params={"day": "2025-03-09"})).json()[
            "series"
        ] == []
    finally:
        async with engine.begin() as conn:
            if created:
                await conn.execute(delete(replay_imports).where(replay_imports.c.id == created))
            if previous:
                await conn.execute(
                    update(replay_imports)
                    .where(replay_imports.c.id == previous)
                    .values(active=True)
                )
            await conn.execute(
                text(
                    "DELETE FROM station_aliases WHERE station_id IN "
                    "(SELECT id FROM stations WHERE external_id=:id)"
                ),
                {"id": external},
            )
            await conn.execute(text("DELETE FROM stations WHERE external_id=:id"), {"id": external})
