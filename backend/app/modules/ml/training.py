"""Bounded CPU training with purged chronological selection/calibration/test periods."""

from __future__ import annotations

import hashlib
import json
import platform
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import psutil

from app.modules.ml.artifacts import load_artifact, write_json
from app.modules.ml.config import TrainingConfig
from app.modules.ml.data import UTC, Array, Demand, checksum, load_demand, load_locations
from app.modules.ml.features import (
    FEATURES,
    NY,
    SCHEMA,
    SCHEMA_HASH,
    FeatureBuilder,
    Seasonal,
    samples,
)
from app.modules.ml.metrics import calibrate, interval_metrics, metrics
from app.modules.ml.reporting import render_report


class MemoryMonitor:
    def __init__(self) -> None:
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self) -> None:
        process = psutil.Process()
        while not self.stop.is_set():
            info = process.memory_info()
            self.peak = max(self.peak, info.rss, getattr(info, "peak_wset", 0))
            self.stop.wait(0.05)


def predictions(models: dict[str, Any], x: Array, threads: int) -> dict[str, Array]:
    result = {"seasonal": x[:, 12].copy(), "recent": x[:, 16].copy()}
    for name, model in models.items():
        result[name] = np.maximum(0, model.predict(x, num_threads=threads)).astype(np.float32)
    return result


def choose_model(
    selection: dict[str, Any],
    test: dict[str, Any],
    candidate: str,
    baselines: list[str],
    margin: float,
) -> tuple[str, str]:
    baseline = min(baselines, key=lambda name: selection[name]["mae"])
    chosen = (
        candidate
        if selection[candidate]["mae"] < selection[baseline]["mae"] * (1 - margin)
        else baseline
    )
    if chosen == candidate and test[candidate]["mae"] >= test[baseline]["mae"]:
        return baseline, "Rejected by one-way final-period baseline acceptance gate; no retuning"
    return chosen, "Early-validation MAE selection, independently audited on final period"


def evaluate_task(
    demand: Demand,
    builder: FeatureBuilder,
    config: TrainingConfig,
    direction: int,
    bins: int,
    models: dict[str, Any],
    calibrations: dict[str, Any],
    archive: str,
    history: str,
) -> dict[str, Any]:
    boundaries = config.boundaries()
    indices, y = samples(demand, boundaries[3], boundaries[4], direction, bins)
    if len(y) < 100:
        raise ValueError("Held-out period is missing or too short")
    x = builder.matrix(indices, direction, bins)
    pred = predictions(models, x, config.threads)
    # The calendar model was trained without lag inputs; mask identically at inference.
    if "lgbm_calendar" in models:
        archive_x = x.copy()
        archive_x[:, 14:16], archive_x[:, 16], archive_x[:, 17:19] = np.nan, archive_x[:, 12], 1
        pred["lgbm_calendar"] = np.maximum(
            0, models["lgbm_calendar"].predict(archive_x, num_threads=config.threads)
        ).astype(np.float32)
    test = {name: metrics(y, value) for name, value in pred.items()}
    intervals = {
        name: interval_metrics(y, value, calibrations[name]) for name, value in pred.items()
    }
    station_rate = np.nanmean(demand.counts[:, demand.times < boundaries[1], direction], axis=1)
    threshold = float(np.nanmedian(station_rate))
    conditions = {
        "zero_trips": y == 0,
        "1_to_3_trips": (y > 0) & (y <= 3),
        "over_3_trips": y > 3,
        "low_demand_station": station_rate[indices[:, 0]] <= threshold,
        "high_demand_station": station_rate[indices[:, 0]] > threshold,
    }
    strata = {
        group: {name: metrics(y[mask], p[mask]) for name, p in pred.items()}
        for group, mask in conditions.items()
    }
    per_station = {}
    for s, station in enumerate(demand.stations):
        mask = indices[:, 0] == s
        per_station[station] = {name: metrics(y[mask], p[mask]) for name, p in pred.items()}
    dates = np.asarray(
        [
            datetime.fromtimestamp(int(at), UTC).astimezone(NY).date().isoformat()
            for at in demand.times
        ]
    )
    row_dates = dates[indices[:, 1]]
    daily = {}
    for day in np.unique(row_dates):
        mask = row_dates == day
        daily[str(day)] = {name: metrics(y[mask], p[mask]) for name, p in pred.items()}
    return {
        "test": test,
        "intervals": intervals,
        "strata": strata,
        "per_station": per_station,
        "station_demand_threshold": threshold,
        "daily_backtest": daily,
        "selected_archive": archive,
        "selected_history": history,
    }


def train_models(
    data_path: Path,
    locations_path: Path | None,
    config: TrainingConfig,
    output: Path,
    report_dir: Path,
    allow_demo: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    monitor = MemoryMonitor()
    monitor.thread.start()
    try:
        return _train(
            data_path, locations_path, config, output, report_dir, allow_demo, started, monitor
        )
    finally:
        monitor.stop.set()
        monitor.thread.join()


def _train(
    data_path: Path,
    locations_path: Path | None,
    config: TrainingConfig,
    output: Path,
    report_dir: Path,
    allow_demo: bool,
    started: float,
    monitor: MemoryMonitor,
) -> dict[str, Any]:
    demand = load_demand(data_path, allow_demo)
    locations = load_locations(locations_path)
    boundaries = config.boundaries()
    if boundaries[0] <= demand.times[0] or boundaries[-1] > demand.times[-1] + 900:
        raise ValueError("Requested splits exceed data coverage or have no warm-up")
    seasonal = Seasonal.fit(demand, boundaries[0], config.history_delay_minutes * 60)
    builder = FeatureBuilder(seasonal, locations, demand, config.history_delay_minutes)
    metadata: dict[str, Any] = {
        "config": config.model_dump(),
        "created_at": datetime.now(UTC).isoformat(),
        "data_sha256": demand.source_sha256,
        "is_demo": demand.is_demo,
        "source": {
            "type": "coverage-aware 15-minute observed ride counts",
            "locations_sha256": checksum(locations_path) if locations_path else None,
            "first_bucket": int(demand.times[0]),
            "last_bucket": int(demand.times[-1]),
            "station_count": len(demand.stations),
            "departure_observed_bins": int(np.isfinite(demand.counts[:, :, 0]).sum()),
            "arrival_observed_bins": int(np.isfinite(demand.counts[:, :, 1]).sum()),
        },
        "evaluation_semantics": "Retrospective event-time replay. Archive publication timestamps "
        "are unknown, so timely-history results are conditional on a completed-trip provider. "
        "Archive-only inference needs only the already-built artifact and calendar.",
        "capacity_semantics": "Only past-observed supplied capacity is used; current GBFS capacity "
        "is never backdated. Citi Bike trip coordinates have no capacity.",
        "tasks": {},
        "implementation_sha256": {
            path.name: checksum(path) for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    report: dict[str, Any] = {
        "data_sha256": demand.source_sha256,
        "is_demo": demand.is_demo,
        "config": config.model_dump(),
        "tasks": {},
    }
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bike-ml-", dir=output) as temporary:
        root = Path(temporary)
        write_json(root / "seasonal.json", seasonal.document())
        write_json(root / "locations.json", locations)
        for direction, label in enumerate(("departures", "arrivals")):
            for bins in (2, 4):
                key = f"{label}_{bins * 15}"
                print(json.dumps({"phase": "train", "target": key}), flush=True)
                indices, y = samples(demand, boundaries[0], boundaries[1], direction, bins)
                if len(y) < 1000 or y.sum() == 0:
                    raise ValueError("Insufficient observed nonzero training counts")
                if len(y) > config.maximum_train_rows:
                    take = np.sort(
                        np.random.default_rng(config.seed).choice(
                            len(y), config.maximum_train_rows, replace=False
                        )
                    )
                    indices, y = indices[take], y[take]
                x = builder.matrix(indices, direction, bins)
                models: dict[str, Any] = {}
                specs: dict[str, Any] = {"seasonal": {}, "recent": {}}
                for name in ("lgbm_calendar", "lgbm_lags"):
                    values = x.copy()
                    if name == "lgbm_calendar":
                        values[:, 14:16], values[:, 16], values[:, 17:19] = np.nan, values[:, 12], 1
                    model = lgb.train(
                        {
                            "objective": "poisson",
                            "verbosity": -1,
                            "device_type": "cpu",
                            "num_threads": config.threads,
                            "deterministic": True,
                            "force_col_wise": True,
                            "seed": config.seed,
                            "data_random_seed": config.seed,
                            "feature_fraction_seed": config.seed,
                            "bagging_seed": config.seed,
                            "num_leaves": config.num_leaves,
                            "max_bin": 63,
                            "histogram_pool_size": 32,
                            "min_data_in_leaf": config.min_data_in_leaf,
                            "learning_rate": config.learning_rate,
                        },
                        lgb.Dataset(
                            values,
                            label=y,
                            feature_name=FEATURES,
                            categorical_feature=["station_code", "weekday", "month"],
                        ),
                        num_boost_round=config.trees,
                    )
                    file = f"{key}-{name}.txt"
                    model.save_model(str(root / file))
                    gain = model.feature_importance(importance_type="gain")
                    specs[name] = {
                        "file": file,
                        "gain_importance": dict(
                            zip(FEATURES, (float(v) for v in gain), strict=True)
                        ),
                    }
                    models[name] = model
                del x, values
                selection, calibrations = {}, {}
                for begin, end, mode in (
                    (boundaries[1], boundaries[2], "selection"),
                    (boundaries[2], boundaries[3], "calibration"),
                ):
                    valid_indices, valid_y = samples(demand, begin, end, direction, bins)
                    if len(valid_y) < 100:
                        raise ValueError("Validation/calibration coverage insufficient")
                    vx = builder.matrix(valid_indices, direction, bins)
                    p = predictions(models, vx, config.threads)
                    vx[:, 14:16], vx[:, 16], vx[:, 17:19] = np.nan, vx[:, 12], 1
                    p["lgbm_calendar"] = np.maximum(
                        0, models["lgbm_calendar"].predict(vx, num_threads=config.threads)
                    ).astype(np.float32)
                    if mode == "selection":
                        selection = {name: metrics(valid_y, value) for name, value in p.items()}
                    else:
                        calibrations = {
                            name: calibrate(valid_y, value, config.interval_coverage)
                            for name, value in p.items()
                        }
                task = evaluate_task(
                    demand,
                    builder,
                    config,
                    direction,
                    bins,
                    models,
                    calibrations,
                    "seasonal",
                    "seasonal",
                )
                archive, archive_reason = choose_model(
                    selection,
                    task["test"],
                    "lgbm_calendar",
                    ["seasonal"],
                    config.acceptance_improvement,
                )
                history, history_reason = choose_model(
                    selection,
                    task["test"],
                    "lgbm_lags",
                    ["seasonal", "recent"],
                    config.acceptance_improvement,
                )
                for name in specs:
                    specs[name]["calibration"] = calibrations[name]
                task.update(
                    {
                        "selection": selection,
                        "selected_archive": archive,
                        "selected_history": history,
                        "archive_selection_reason": archive_reason,
                        "history_selection_reason": history_reason,
                        "train_rows": len(y),
                        "gain_importance": {
                            name: spec["gain_importance"]
                            for name, spec in specs.items()
                            if "gain_importance" in spec
                        },
                    }
                )
                report["tasks"][key] = task
                metadata["tasks"][key] = {
                    "candidates": specs,
                    "selected_archive": archive,
                    "selected_history": history,
                    "archive_selection_reason": archive_reason,
                    "history_selection_reason": history_reason,
                    "selection_metrics": selection,
                    "test_metrics": task["test"],
                    "test_interval_metrics": task["intervals"],
                }
                print(
                    json.dumps(
                        {
                            "phase": "evaluated",
                            "target": key,
                            "archive": archive,
                            "history": history,
                            "test": task["test"],
                        }
                    ),
                    flush=True,
                )
                if monitor.peak > 4_000_000_000:
                    raise MemoryError("Training exceeded 4 GB resource budget")
        resources = {
            "wall_seconds": time.perf_counter() - started,
            "sampled_peak_process_bytes": monitor.peak,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "lightgbm": lgb.__version__,
            "platform": platform.platform(),
            "threads": config.threads,
            "peak_method": "50ms process RSS sampling; OS peak working set where available",
        }
        files = {path.name: checksum(path) for path in sorted(root.iterdir())}
        identity = json.dumps(
            {
                "files": files,
                "schema_hash": SCHEMA_HASH,
                "source": metadata["source"],
                "config": config.model_dump(),
                "data_sha256": demand.source_sha256,
                "implementation_sha256": metadata["implementation_sha256"],
            },
            sort_keys=True,
        )
        version = "v1-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        metadata.update({"version": version, "resources": resources})
        report.update({"version": version, "resources": resources, "source": metadata["source"]})
        render_report(report, report_dir)
        # Include chart generation in process measurements, not just model fitting.
        resources.update(
            {
                "wall_seconds": time.perf_counter() - started,
                "sampled_peak_process_bytes": monitor.peak,
            }
        )
        metadata["created_at"] = datetime.now(UTC).isoformat()
        write_json(report_dir / "evaluation.json", report)
        write_json(root / "metadata.json", metadata)
        files["metadata.json"] = checksum(root / "metadata.json")
        write_json(
            root / "manifest.json",
            {
                "format_version": 1,
                "version": version,
                "schema": SCHEMA,
                "schema_hash": SCHEMA_HASH,
                "lightgbm_version": lgb.__version__,
                "files": files,
            },
        )
        destination = output / version
        if destination.exists():
            # Exact reproducible model identity: reuse its original immutable run metadata.
            previous, _, _, _ = load_artifact(destination)
            report["resources"] = resources
            if previous["version"] != version:
                raise ValueError("Existing artifact identity mismatch")
        else:
            root.rename(destination)
        write_json(output / "current.json", {"version": version})
        return {
            "artifact": str(destination),
            "report": str(report_dir / "REPORT.md"),
            "version": version,
            "resources": resources,
            "selected": {
                key: {"archive": t["selected_archive"], "history": t["selected_history"]}
                for key, t in metadata["tasks"].items()
            },
        }


def evaluate_artifact(
    artifact: Path, data_path: Path, report_dir: Path, allow_demo: bool = False
) -> dict[str, Any]:
    metadata, seasonal, locations, loaded = load_artifact(artifact)
    demand = load_demand(data_path, allow_demo)
    if demand.source_sha256 != metadata["data_sha256"]:
        raise ValueError("This reproducibility replay requires the exact source export checksum")
    config = TrainingConfig.model_validate(metadata["config"])
    builder = FeatureBuilder(seasonal, locations, demand, config.history_delay_minutes)
    report = {
        "version": metadata["version"],
        "data_sha256": demand.source_sha256,
        "is_demo": demand.is_demo,
        "config": config.model_dump(),
        "source": metadata["source"],
        "resources": metadata["resources"],
        "tasks": {},
    }
    for key, spec in metadata["tasks"].items():
        direction = int(key.startswith("arrivals"))
        bins = int(key.rsplit("_", 1)[1]) // 15
        models = {
            name: loaded[f"{key}/{name}"] for name in spec["candidates"] if name.startswith("lgbm_")
        }
        calibrations = {name: v["calibration"] for name, v in spec["candidates"].items()}
        report["tasks"][key] = evaluate_task(
            demand,
            builder,
            config,
            direction,
            bins,
            models,
            calibrations,
            spec["selected_archive"],
            spec["selected_history"],
        )
    render_report(report, report_dir)
    return {
        "report": str(report_dir / "REPORT.md"),
        "version": metadata["version"],
        "tasks": {key: value["test"] for key, value in report["tasks"].items()},
    }
