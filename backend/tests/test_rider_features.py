"""Deterministic rider logic; synthetic observations never become application data."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.modules.commutes.alerts import decision
from app.modules.commutes.schemas import AlertInput, CommuteInput, next_departure
from app.modules.replay.cli import parse_bin
from app.modules.replay.service import day_bounds


def config(**changes):
    return {
        "name": "Test commute",
        "origin": {"latitude": 40.7, "longitude": -74, "label": "A"},
        "destination": {"latitude": 40.8, "longitude": -73.9, "label": "B"},
        "departure_local": "08:30",
        "timezone": "America/New_York",
        "weekdays": [1, 2, 3, 4, 5],
        **changes,
    }


def test_schedule_dst_and_calendar():
    assert next_departure(config(), datetime(2025, 2, 28, 14, tzinfo=UTC)) == datetime(
        2025, 3, 3, 13, 30, tzinfo=UTC
    )
    # Nonexistent Sunday 02:30 is skipped, not silently shifted.
    assert next_departure(
        config(departure_local="02:30", weekdays=[7]), datetime(2025, 3, 9, 5, tzinfo=UTC)
    ) == datetime(2025, 3, 16, 6, 30, tzinfo=UTC)
    # First 01:30 only; never notify twice in the fall-back hour.
    assert next_departure(
        config(departure_local="01:30", weekdays=[7]), datetime(2025, 11, 2, 6, tzinfo=UTC)
    ) == datetime(2025, 11, 9, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "Mars/Fake"},
        {"weekdays": []},
        {"weekdays": [1, 1]},
        {"weekdays": [0]},
        {"departure_local": "25:00"},
        {"user_id": "spoof"},
    ],
)
def test_commute_validation(change):
    with pytest.raises(ValidationError):
        CommuteInput(**config(**change))


def test_alert_units():
    assert AlertInput(station_id="test", kind="pickup_empty").threshold == 3
    for threshold in [1.5, -1, 101, float("nan")]:
        with pytest.raises(ValidationError):
            AlertInput(station_id="test", kind="pickup_empty", threshold=threshold)


def observation(now):
    return {
        "external_id": "synthetic",
        "name": "Synthetic station",
        "bikes": 2,
        "docks": 18,
        "is_demo": False,
        "stale": False,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "reported_at": now,
        "feed_updated_at": now,
        "fetched_at": now,
    }


def test_alert_missing_stale_closed_and_risk_fallback():
    now = datetime(2025, 2, 28, 13, tzinfo=UTC)
    rule = AlertInput(station_id="synthetic", kind="pickup_empty").model_dump()
    obs = observation(now)
    assert decision(rule, obs, None, now)[0] == "triggered"
    assert decision({**rule, "kind": "destination_full"}, obs, None, now)[0] == "clear"
    for change in [
        {"stale": True},
        {"reported_at": None},
        {"fetched_at": now - timedelta(minutes=6)},
        {"feed_updated_at": now + timedelta(seconds=1)},
        {"is_demo": True},
    ]:
        assert decision(rule, {**obs, **change}, None, now)[0] == "stale_or_missing"
    assert decision(rule, {**obs, "is_renting": False}, None, now)[0] == "station_closed"
    riskrule = {**rule, "mode": "estimated_risk", "threshold": 50}
    assert decision(riskrule, obs, None, now)[0] == "model_unavailable_or_low_confidence"
    forecast = {
        "as_of": now.isoformat(),
        "model_version": "synthetic",
        "forecasts": {"departures_30": {"confidence": "normal"}},
        "availability_risk": {
            "status": "estimated_scenario",
            "horizons": {"30": {"scenario_empty_at_any_time": 0.6}},
        },
    }
    status, evidence = decision(riskrule, obs, forecast, now)
    assert status == "triggered" and evidence["calibrated_stockout_probability"] is False
    assert (
        decision(
            riskrule, obs, {**forecast, "as_of": (now - timedelta(minutes=20)).isoformat()}, now
        )[0]
        == "risk_unavailable"
    )
    forecast["forecasts"]["departures_30"]["confidence"] = "low"
    assert decision(riskrule, obs, forecast, now)[0] == "model_unavailable_or_low_confidence"


@pytest.mark.parametrize(
    "day,frames", [(date(2025, 3, 9), 92), (date(2025, 11, 2), 100), (date(2025, 2, 28), 96)]
)
def test_replay_day_boundaries(day, frames):
    start, end = day_bounds(day)
    assert (end - start).total_seconds() == frames * 900


def test_replay_coverage_not_zero_filled():
    row = {
        "station_id": "synthetic",
        "bucket_start": "2025-02-28T05:00:00Z",
        "is_demo": False,
        "departures": 2,
        "arrivals": 0,
        "departure_coverage": False,
        "arrival_coverage": True,
    }
    parsed = parse_bin(json.dumps(row))
    assert parsed["departures"] is None and parsed["arrivals"] == 0
    for change in [
        {"is_demo": True},
        {"bucket_start": "2025-02-28T05:01:00Z"},
        {"bucket_start": "2025-02-28T05:00:00"},
        {"arrivals": -1},
        {"arrival_coverage": "yes"},
    ]:
        with pytest.raises(ValueError):
            parse_bin(json.dumps({**row, **change}))
