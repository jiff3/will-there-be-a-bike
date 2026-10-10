"""Bounded scheduled evaluation. No email/SMS and no fabricated risk fallback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.errors import ApplicationError
from app.modules.commutes.schemas import next_departure
from app.modules.forecasts.service import current_forecast
from app.modules.stations.service import station
from app.persistence.database import engine
from app.persistence.rider_tables import commutes, notifications, rules


def decision(
    rule: dict[str, Any],
    observation: dict[str, Any],
    forecast: dict[str, Any] | None,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    purpose = "bikes" if rule["kind"] == "pickup_empty" else "docks"
    flag = "is_renting" if purpose == "bikes" else "is_returning"
    timestamps = [observation.get(key) for key in ("reported_at", "feed_updated_at", "fetched_at")]
    if (
        observation.get("is_demo")
        or observation.get("stale")
        or any(
            value is None or not 0 <= (now - value).total_seconds() <= 300 for value in timestamps
        )
    ):
        return "stale_or_missing", {}
    if not observation.get("is_installed") or not observation.get(flag):
        return "station_closed", {}
    evidence: dict[str, Any] = {
        "mode": rule["mode"],
        "station_id": observation["external_id"],
        "station_name": observation["name"],
        "reported_at": observation["reported_at"].isoformat(),
        "threshold": rule["threshold"],
        "units": purpose,
        "inventory": observation[purpose],
    }
    if rule["mode"] == "inventory":
        return ("triggered" if observation[purpose] <= rule["threshold"] else "clear"), evidence
    if not forecast or any(item["confidence"] == "low" for item in forecast["forecasts"].values()):
        return "model_unavailable_or_low_confidence", {}
    risk = forecast.get("availability_risk", {})
    if risk.get("status") != "estimated_scenario":
        return "risk_unavailable", {}
    issuance = datetime.fromisoformat(forecast["as_of"])
    if not 0 <= (now - issuance).total_seconds() < 900:
        return "risk_unavailable", {}
    key = "scenario_empty_at_any_time" if purpose == "bikes" else "scenario_full_at_any_time"
    horizon = risk.get("horizons", {}).get(str(rule["lead_minutes"]))
    if not horizon:
        return "risk_unavailable", {}
    probability = float(horizon[key]) * 100
    evidence.update(
        {
            "scenario_percent": probability,
            "units": "percent, uncalibrated scenario",
            "model_version": forecast["model_version"],
            "issuance": forecast["as_of"],
            "horizon_minutes": rule["lead_minutes"],
            "calibrated_stockout_probability": False,
        }
    )
    return ("triggered" if probability >= rule["threshold"] else "clear"), evidence


async def record(
    rule: dict[str, Any], status: str, evidence: dict[str, Any], scheduled: datetime, now: datetime
) -> str:
    async with engine.begin() as connection:
        config = await connection.scalar(
            select(commutes.c.config).where(commutes.c.id == rule["commute_id"]).with_for_update()
        )
        if config != rule["config"]:
            return "changed"
        current = (
            (
                await connection.execute(
                    select(rules).where(rules.c.id == rule["id"]).with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if not current or any(
            current[key] != rule[key]
            for key in (
                "station_id",
                "kind",
                "mode",
                "threshold",
                "lead_minutes",
                "cooldown_minutes",
                "enabled",
            )
        ):
            return "changed"
        if status == "triggered":
            recent = await connection.scalar(
                select(notifications.c.id)
                .where(
                    notifications.c.rule_id == rule["id"],
                    notifications.c.created_at > now - timedelta(minutes=rule["cooldown_minutes"]),
                )
                .limit(1)
            )
            if recent:
                status = "cooldown"
            else:
                estimated = rule["mode"] == "estimated_risk"
                message = (
                    f"Uncalibrated scenario: {evidence['scenario_percent']:.0f}% boundary risk "
                    f"in the {rule['lead_minutes']}-minute issuance window. "
                    "Not a validated stockout probability."
                    if estimated
                    else f"Latest measurement: {evidence['inventory']} {evidence['units']}; "
                    f"your threshold is {rule['threshold']:g}. "
                    "This is not a future inventory prediction."
                )
                identifier = await connection.scalar(
                    pg_insert(notifications)
                    .values(
                        id=uuid.uuid4(),
                        user_id=rule["user_id"],
                        rule_id=rule["id"],
                        scheduled_for=scheduled,
                        created_at=now,
                        title=f"{rule['config']['name']} · {evidence['station_name']}",
                        message=message,
                        evidence={**evidence, "commute_id": str(rule["commute_id"])},
                    )
                    .on_conflict_do_nothing(constraint="uq_notification_occurrence")
                    .returning(notifications.c.id)
                )
                status = "notified" if identifier else "already_notified"
        await connection.execute(
            update(rules)
            .where(rules.c.id == rule["id"])
            .values(last_checked_at=now, last_status=status)
        )
    return status


async def evaluate_alerts(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    async with engine.connect() as connection:
        rows = [
            dict(row)
            for row in (
                await connection.execute(
                    text("""
          SELECT a.*,c.user_id,c.config,s.external_id FROM availability_alerts a
          JOIN saved_commutes c ON c.id=a.commute_id JOIN stations s ON s.id=a.station_id
          WHERE a.enabled ORDER BY a.last_checked_at NULLS FIRST,a.id LIMIT 200
        """)
                )
            ).mappings()
        ]
    statuses: dict[str, int] = {}
    observed: dict[str, dict[str, Any]] = {}
    forecasts: dict[str, dict[str, Any] | None] = {}
    for rule in rows:
        scheduled = next_departure(rule["config"], now)
        status = "outside_window"
        evidence: dict[str, Any] = {}
        if (scheduled - now).total_seconds() <= rule["lead_minutes"] * 60:
            key = rule["external_id"]
            try:
                if key not in observed:
                    observed[key] = await station(key)
                if rule["mode"] == "estimated_risk" and key not in forecasts:
                    try:
                        forecasts[key] = await current_forecast(key)
                    except ApplicationError as error:
                        if error.status_code != 503:
                            raise
                        forecasts[key] = None
                status, evidence = decision(rule, observed[key], forecasts.get(key), now)
            except ApplicationError as error:
                if error.status_code not in {404, 503}:
                    raise
                status = "dependency_unavailable"
        status = await record(rule, status, evidence, scheduled, now)
        statuses[status] = statuses.get(status, 0) + 1
    return {"checked": len(rows), "batch_limit": 200, "statuses": statuses}
