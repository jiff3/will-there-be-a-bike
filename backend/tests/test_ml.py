"""Deterministic offline synthetic tests. These are NOT real-data experimental results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("lightgbm")

from app.modules.ml.artifacts import load_artifact, write_json  # noqa: E402
from app.modules.ml.config import TrainingConfig  # noqa: E402
from app.modules.ml.data import STEP, UTC, Demand, epoch, load_demand  # noqa: E402
from app.modules.ml.features import (  # noqa: E402
    FEATURES,
    FeatureBuilder,
    Seasonal,
    samples,
    validate_features,
)
from app.modules.ml.inference import ForecastEngine  # noqa: E402
from app.modules.ml.metrics import calibrate, interval, metrics  # noqa: E402
from app.modules.ml.risk import inventory_risk  # noqa: E402
from app.modules.ml.training import choose_model, evaluate_artifact, train_models  # noqa: E402


@pytest.fixture
def demand() -> Demand:
    first = epoch("2025-01-01T00:00:00-05:00")
    times = np.arange(first, first + 40 * 86400, STEP, dtype=np.int64)
    rates = np.asarray([0.3, 1.2, 3.0], dtype=np.float32)[:, None, None]
    activity = (1 + np.sin(np.arange(len(times)) * 2 * np.pi / 96))[None, :, None]
    counts = np.random.default_rng(42).poisson(rates * activity * np.ones((1, 1, 2)))
    return Demand(
        ["SYNTH-A", "SYNTH-B", "SYNTH-C"],
        times,
        counts.astype(np.float32),
        "synthetic-test-only",
        True,
    )


def test_batch_inference_parity_missing_geo_and_future_metadata(demand: Demand) -> None:
    seasonal = Seasonal.fit(demand, int(demand.times[10 * 96]))
    at = int(demand.times[25 * 96])
    builder = FeatureBuilder(
        seasonal,
        {"SYNTH-A": {"lat": 40, "lon": -73, "capacity": 30, "observed_at": "2026-01-01T00:00:00Z"}},
        demand,
    )
    rows = np.asarray([[0, 25 * 96], [1, 25 * 96 + 3]], dtype=np.int32)
    matrix = builder.matrix(rows, 0, 4)
    for i, (s, t) in enumerate(rows):
        np.testing.assert_allclose(
            matrix[i],
            builder.row(demand.stations[s], int(demand.times[t]), 0, 4),
            rtol=1e-6,
            equal_nan=True,
        )
    assert np.isnan(matrix[:, 1:4]).all()
    assert np.isnan(builder.row("SYNTH-A", at, 0, 4)[14])  # full day delay
    assert np.isfinite(matrix[:, 15]).all()
    assert np.isfinite(FeatureBuilder(seasonal, {}, demand, 0).row("SYNTH-A", at, 0, 4)[14])
    with pytest.raises(ValueError, match="schema"):
        validate_features(matrix[:, :-1])
    with pytest.raises(ValueError, match="schema"):
        validate_features(matrix.astype(np.float64))
    matrix[0, 4] = np.nan
    with pytest.raises(ValueError, match="Missing required"):
        validate_features(matrix)


def test_no_target_or_future_feature_leakage_and_purged_splits(demand: Demand) -> None:
    cutoff = int(demand.times[10 * 96])
    at = int(demand.times[25 * 96])
    seasonal = Seasonal.fit(demand, cutoff)
    # The final warm-up day is not released under the 24-hour completed-count delay.
    original_table = seasonal.means.copy()
    demand.counts[:, 9 * 96 : 10 * 96, :] = 777
    np.testing.assert_array_equal(original_table, Seasonal.fit(demand, cutoff).means)
    before = FeatureBuilder(seasonal, {}, demand).row("SYNTH-A", at, 0, 4)
    demand.counts[:, 25 * 96 :, :] = 999
    np.testing.assert_allclose(
        before, FeatureBuilder(seasonal, {}, demand).row("SYNTH-A", at, 0, 4), equal_nan=True
    )
    demand.counts[:, 10 * 96 : 20 * 96, :] = 888
    # Seasonal fit sees neither training nor validation/test targets.
    np.testing.assert_array_equal(seasonal.means, Seasonal.fit(demand, cutoff).means)
    # Compare full and truncated histories after changing the old history consistently.
    demand.counts[:, 10 * 96 : 20 * 96, :] = 0
    builder = FeatureBuilder(seasonal, {}, demand)
    full = builder.row("SYNTH-A", at, 0, 4)
    truncated = Demand(
        demand.stations, demand.times[: 25 * 96], demand.counts[:, : 25 * 96], "test", True
    )
    np.testing.assert_allclose(
        full, FeatureBuilder(seasonal, {}, truncated).row("SYNTH-A", at, 0, 4), equal_nan=True
    )
    assert before[12] == full[12]
    indices, _ = samples(demand, cutoff, at, 0, 4)
    assert np.all(demand.times[indices[:, 1]] + 4 * STEP <= at)
    with pytest.raises(ValueError, match="not yet available"):
        builder.row("SYNTH-A", cutoff - STEP, 0, 2)


def test_uncovered_targets_and_lags_are_not_zero(demand: Demand) -> None:
    demand.counts[0, 20 * 96, 0] = np.nan
    seasonal = Seasonal.fit(demand, int(demand.times[10 * 96]))
    builder = FeatureBuilder(seasonal, {}, demand)
    at = int(demand.times[27 * 96])
    assert np.isnan(builder.lag("SYNTH-A", at, 7, 0, 2))
    indices, _ = samples(demand, int(demand.times[19 * 96]), int(demand.times[21 * 96]), 0, 4)
    assert not any(s == 0 and t <= 20 * 96 < t + 4 for s, t in indices)


def test_day_week_clock_lookbacks_handle_dst() -> None:
    first = epoch("2025-03-01T00:00:00-05:00")
    times = np.arange(first, first + 20 * 86400, STEP)
    demand = Demand(["A"], times, np.zeros((1, len(times), 2), dtype=np.float32), "test")
    at = epoch("2025-03-10T08:00:00-04:00")
    earlier = epoch("2025-03-03T08:00:00-05:00")
    demand.counts[0, demand.index(earlier) : demand.index(earlier) + 2, 0] = [2, 3]
    builder = FeatureBuilder(Seasonal.fit(demand, epoch("2025-03-08T00:00:00-05:00")), {}, demand)
    assert builder.lag("A", at, 7, 0, 2) == 5


def test_count_metrics_zero_handling_and_intervals() -> None:
    y = np.asarray([0, 0, 1, 4], dtype=np.float32)
    p = np.asarray([0, 0, 2, 3], dtype=np.float32)
    result = metrics(y, p)
    assert result["mae"] == 0.5 and result["poisson_deviance"] >= 0
    calibration = calibrate(np.tile(y, 30), np.tile(p, 30), 0.9)
    lo, hi = interval(p, calibration)
    assert (lo >= 0).all() and (hi >= lo).all()
    with pytest.raises(ValueError, match="At least"):
        calibrate(y, p, 0.9)


def test_selection_retains_baseline_when_ml_loses() -> None:
    validation = {"seasonal": {"mae": 1.0}, "ml": {"mae": 0.8}}
    test = {"seasonal": {"mae": 1.0}, "ml": {"mae": 1.1}}
    assert choose_model(validation, test, "ml", ["seasonal"], 0.01)[0] == "seasonal"
    assert choose_model(validation, validation, "ml", ["seasonal"], 0.01)[0] == "ml"


def inventory(at: int) -> dict[str, Any]:
    stamp = datetime.fromtimestamp(at, UTC).isoformat()
    return {
        "station_id": "A",
        "bikes": 5,
        "docks": 5,
        "reported_at": stamp,
        "feed_updated_at": stamp,
        "fetched_at": stamp,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "is_demo": False,
    }


def test_risk_bounded_deterministic_and_operational_fallbacks() -> None:
    at = epoch("2025-02-25T08:00:00-05:00")
    forecasts = {
        f"{d}_{h}": {"mean": 1.0 if d == "arrivals" else h / 2, "dispersion_alpha": 0.1}
        for d in ("departures", "arrivals")
        for h in (30, 60)
    }
    body = inventory(at)
    first = inventory_risk(forecasts, body, at, "A", 42, False)
    assert first == inventory_risk(forecasts, body, at, "A", 42, False)
    assert first["status"] == "estimated_scenario"
    assert not first["calibrated_stockout_probability"]
    assert 0 <= first["horizons"]["60"]["expected_bikes_at_end"] <= 10
    assert first["horizons"]["60"]["scenario_empty_at_any_time"] > 0.9
    assert (
        inventory_risk(forecasts, body, at + 900, "A", 42, False)["reason"]
        == "stale_or_future_inventory"
    )
    assert inventory_risk(forecasts, body, at - 900, "A", 42, False)["status"] == "unavailable"
    assert inventory_risk(forecasts, body, at, "A", 42, True)["reason"] == "forecast_low_confidence"
    body["is_renting"] = False
    assert (
        inventory_risk(forecasts, body, at, "A", 42, False)["reason"] == "station_not_operational"
    )
    body["is_renting"], body["is_demo"] = True, True
    assert inventory_risk(forecasts, body, at, "A", 42, False)["status"] == "unavailable"
    with pytest.raises(ValueError, match="different station"):
        inventory_risk(forecasts, body, at, "B", 42, False)


def export(demand: Demand, path: Path) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for s, station in enumerate(demand.stations):
            for t, at in enumerate(demand.times):
                stream.write(
                    json.dumps(
                        {
                            "station_id": station,
                            "bucket_start": datetime.fromtimestamp(int(at), UTC).isoformat(),
                            "departures": int(demand.counts[s, t, 0]),
                            "arrivals": int(demand.counts[s, t, 1]),
                            "departure_coverage": True,
                            "arrival_coverage": True,
                            "is_demo": True,
                        }
                    )
                    + "\n"
                )


def test_json_input_duplicate_demo_and_schema_validation(tmp_path: Path, demand: Demand) -> None:
    path = tmp_path / "counts.jsonl"
    export(demand, path)
    with pytest.raises(ValueError, match="synthetic"):
        load_demand(path)
    loaded = load_demand(path, True)
    np.testing.assert_array_equal(loaded.counts, demand.counts)
    first = path.read_text().splitlines()[0]
    with path.open("a") as stream:
        stream.write(first + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_demand(path, True)
    row = json.loads(first)
    row["departures"] = None
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="cannot be null"):
        load_demand(path, True)


def test_full_training_serialization_replay_and_inference(tmp_path: Path, demand: Demand) -> None:
    pytest.importorskip("matplotlib")
    path = tmp_path / "synthetic.jsonl"
    export(demand, path)
    config = TrainingConfig(
        warmup_end="2025-01-10",
        train_end="2025-01-25",
        selection_end="2025-01-30",
        calibration_end="2025-02-04",
        test_end="2025-02-10",
        trees=10,
        threads=1,
        min_data_in_leaf=20,
    )
    trained = train_models(path, None, config, tmp_path / "models", tmp_path / "report", True)
    artifact = Path(trained["artifact"])
    engine = ForecastEngine.load(artifact)
    at = epoch("2025-02-05T08:00:00-05:00")
    result = engine.predict("SYNTH-A", at, load_demand(path, True), replay=True)
    assert result["is_demo"] and result["availability_risk"]["status"] == "unavailable"
    assert all(v["mean"] >= 0 and v["interval"][0] >= 0 for v in result["forecasts"].values())
    assert (
        ForecastEngine.load(tmp_path / "models" / "current.json").predict(
            "SYNTH-A", at, load_demand(path, True), replay=True
        )
        == result
    )
    assert all(
        v["model"] == "seasonal"
        for v in engine.predict("UNKNOWN", at, replay=True)["forecasts"].values()
    )
    with pytest.raises(ValueError, match="predates artifact"):
        engine.predict("SYNTH-A", at)
    with pytest.raises(ValueError, match="training window"):
        engine.predict("SYNTH-A", epoch("2025-01-20T00:00:00-05:00"), replay=True)
    with pytest.raises(ValueError, match="interval calibration"):
        engine.predict("SYNTH-A", epoch("2025-02-01T00:00:00-05:00"), replay=True)
    replay = evaluate_artifact(artifact, path, tmp_path / "replay", True)
    report = json.loads((tmp_path / "report" / "evaluation.json").read_text())
    assert replay["tasks"]["departures_30"] == report["tasks"]["departures_30"]["test"]
    assert (tmp_path / "report" / "conditions.svg").exists()
    with (artifact / "seasonal.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        load_artifact(artifact)


def test_safe_pointer_paths(tmp_path: Path) -> None:
    pointer = tmp_path / "current.json"
    write_json(pointer, {"version": "../untrusted"})
    with pytest.raises(ValueError, match="version pointer"):
        ForecastEngine.load(pointer)
    assert len(FEATURES) == 19


def test_packaged_artifact_offline_inference_and_expiry() -> None:
    artifact = Path(__file__).parents[1] / "app/modules/ml/artifacts/current.json"
    engine = ForecastEngine.load(artifact)
    at = epoch("2025-02-25T08:00:00-05:00")
    station = next(
        s
        for s in engine.seasonal.stations
        if engine.seasonal.means[engine.seasonal.lookup[s]].sum() > 0
    )
    result = engine.predict(station, at, replay=True)
    assert not result["is_demo"]
    assert len(result["forecasts"]) == 4
    assert all(v["feature_mode"] == "archive_only" for v in result["forecasts"].values())
    expired = engine.predict(station, epoch("2026-10-10T08:00:00-04:00"), replay=True)
    assert "training_history_older_than_90_days" in expired["warnings"]
    assert all(not v["interval_validated_for_this_context"] for v in expired["forecasts"].values())
    assert expired["availability_risk"]["status"] == "unavailable"
    # Exercise native-model inference/explanations even when honest validation retained a baseline.
    engine.metadata["tasks"]["departures_30"]["selected_archive"] = "lgbm_calendar"
    candidate = engine.predict(station, at, replay=True)["forecasts"]["departures_30"]
    assert candidate["model"] == "lgbm_calendar" and len(candidate["drivers"]) == 5
    assert all("log_mean_contribution" in driver for driver in candidate["drivers"])


def test_manifest_rejects_paths_schema_and_runtime(tmp_path: Path) -> None:
    from app.modules.ml.features import SCHEMA, SCHEMA_HASH

    manifest = {
        "format_version": 1,
        "schema": SCHEMA,
        "schema_hash": SCHEMA_HASH,
        "lightgbm_version": "4.6.0",
        "files": {"../unsafe.txt": "bad", "seasonal.json": "bad", "metadata.json": "bad"},
    }
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Unsafe artifact"):
        load_artifact(tmp_path)
    manifest["schema_hash"] = "incompatible"
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="feature schema"):
        load_artifact(tmp_path)
    manifest["schema_hash"], manifest["lightgbm_version"] = SCHEMA_HASH, "0.0"
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="runtime version"):
        load_artifact(tmp_path)
