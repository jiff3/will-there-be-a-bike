"""Transparent bounded inventory simulation; NOT calibrated actual stockout rates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from app.modules.ingestion.live import stale, timestamp
from app.modules.ml.data import UTC, Array

DISCLAIMER = (
    "Scenario probability under independent Gamma-Poisson observed-ride rates, one-minute "
    "random event ordering, constant usable slots and no rebalancing. NOT empirically calibrated "
    "actual stockout probability. Observed rides omit unserved demand."
)


def inventory_risk(
    forecasts: dict[str, Any],
    inventory: dict[str, Any] | None,
    at: int,
    station: str,
    seed: int,
    low_confidence: bool,
    paths: int = 2048,
) -> dict[str, Any]:
    unavailable: dict[str, Any] = {
        "status": "unavailable",
        "reason": "missing_inventory",
        "calibrated_stockout_probability": False,
        "assumptions": DISCLAIMER,
    }
    if inventory is None:
        return unavailable
    if not 256 <= paths <= 4096:
        raise ValueError("Simulation budget requires 256..4096 paths")
    if inventory.get("station_id") != station:
        raise ValueError("Inventory belongs to a different station")
    for key in ("bikes", "docks"):
        if type(inventory.get(key)) is not int or not 0 <= inventory[key] <= 1000:
            raise ValueError("Inventory must contain bounded integer bikes/docks")
    for key in ("is_installed", "is_renting", "is_returning", "is_demo"):
        if type(inventory.get(key)) is not bool:
            raise ValueError("Inventory operational/provenance flags must be explicit booleans")
    now = datetime.fromtimestamp(at, UTC)
    if stale(
        timestamp(inventory["reported_at"]),
        timestamp(inventory["feed_updated_at"]),
        timestamp(inventory["fetched_at"]),
        now,
    ):
        return {**unavailable, "reason": "stale_or_future_inventory"}
    if inventory["is_demo"]:
        return {**unavailable, "reason": "synthetic_inventory_not_a_real_risk_estimate"}
    if not all(inventory[key] for key in ("is_installed", "is_renting", "is_returning")):
        return {**unavailable, "reason": "station_not_operational"}
    if low_confidence:
        return {**unavailable, "reason": "forecast_low_confidence"}
    slots = inventory["bikes"] + inventory["docks"]
    if slots == 0:
        return {**unavailable, "reason": "no_usable_slots"}
    rates, alphas = {}, {}
    for direction in ("departures", "arrivals"):
        first = forecasts[f"{direction}_30"]["mean"]
        total = forecasts[f"{direction}_60"]["mean"]
        if total < first:
            return {**unavailable, "reason": "incoherent_horizon_forecasts"}
        rates[direction] = (first / 30, (total - first) / 30)
        alphas[direction] = max(
            forecasts[f"{direction}_30"]["dispersion_alpha"],
            forecasts[f"{direction}_60"]["dispersion_alpha"],
        )
    rng = np.random.default_rng(seed)
    factors = {
        d: rng.gamma(1 / a, a, paths) if a > 1e-8 else np.ones(paths) for d, a in alphas.items()
    }
    state: Array = np.full(paths, inventory["bikes"], dtype=np.int32)
    empty, full = state == 0, state == slots
    result = {}
    for minute in range(60):
        dep = rng.poisson(
            np.minimum(1000, factors["departures"] * rates["departures"][minute // 30])
        )
        arr = rng.poisson(np.minimum(1000, factors["arrivals"] * rates["arrivals"][minute // 30]))
        departures_first = rng.random(paths) < 0.5
        interim = np.where(
            departures_first, np.maximum(0, state - dep), np.minimum(slots, state + arr)
        )
        empty |= interim == 0
        full |= interim == slots
        state = np.where(
            departures_first, np.minimum(slots, interim + arr), np.maximum(0, interim - dep)
        )
        empty |= state == 0
        full |= state == slots
        if minute + 1 in (30, 60):
            result[str(minute + 1)] = {
                "scenario_empty_at_any_time": float(empty.mean()),
                "scenario_full_at_any_time": float(full.mean()),
                "scenario_empty_at_end": float((state == 0).mean()),
                "scenario_full_at_end": float((state == slots).mean()),
                "expected_bikes_at_end": float(state.mean()),
                "bikes_at_end_p10": int(np.quantile(state, 0.1)),
                "bikes_at_end_p90": int(np.quantile(state, 0.9)),
                "monte_carlo_standard_error_max": float(0.5 / np.sqrt(paths)),
            }
    return {
        "status": "estimated_scenario",
        "paths": paths,
        "seed": seed,
        "usable_slots": slots,
        "measured_bikes": inventory["bikes"],
        "measured_docks": inventory["docks"],
        "inventory_reported_at": inventory["reported_at"],
        "calibrated_stockout_probability": False,
        "assumptions": DISCLAIMER,
        "horizons": result,
    }
