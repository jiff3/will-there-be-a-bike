"""Pure local inference interface for the application; no network and no training."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.modules.ml.artifacts import load_artifact
from app.modules.ml.config import TrainingConfig
from app.modules.ml.data import UTC, Demand
from app.modules.ml.features import FEATURES, NY, FeatureBuilder, Seasonal
from app.modules.ml.metrics import interval
from app.modules.ml.risk import inventory_risk


class ForecastEngine:
    def __init__(
        self,
        metadata: dict[str, Any],
        seasonal: Seasonal,
        locations: dict[str, Any],
        models: dict[str, Any],
    ) -> None:
        self.metadata, self.seasonal, self.locations, self.models = (
            metadata,
            seasonal,
            locations,
            models,
        )
        self.config = TrainingConfig.model_validate(metadata["config"])

    @classmethod
    def load(cls, path: Path) -> ForecastEngine:
        return cls(*load_artifact(path))

    def predict(
        self,
        station: str,
        at: int,
        history: Demand | None = None,
        inventory: dict[str, Any] | None = None,
        replay: bool = False,
    ) -> dict[str, Any]:
        if at % 900:
            raise ValueError("Predictions require 15-minute-aligned timezone-aware as-of times")
        if not replay and at < int(datetime.fromisoformat(self.metadata["created_at"]).timestamp()):
            raise ValueError(
                "Prediction predates artifact creation; use explicit retrospective replay"
            )
        if history is not None and history.is_demo != self.metadata["is_demo"]:
            raise ValueError("Cannot mix real and synthetic feature histories")
        builder = FeatureBuilder(
            self.seasonal, self.locations, history, self.config.history_delay_minutes
        )
        warnings = []
        known = station in self.seasonal.lookup
        end = self.config.boundaries()[1]
        if at < end:
            raise ValueError("Prediction predates the end of model training window")
        if at < self.config.boundaries()[3]:
            raise ValueError("Prediction predates completed selection and interval calibration")
        local = datetime.fromtimestamp(at, UTC).astimezone(NY)
        age = (at - end) / 86400
        warmup = datetime.fromtimestamp(self.seasonal.cutoff, UTC).astimezone(NY)
        if not known:
            warnings.append("unknown_station_pooled_seasonal_fallback")
        elif self.seasonal.means[self.seasonal.lookup[station]].sum() == 0:
            warnings.append("station_without_prior_observed_rides")
        if age > 90:
            warnings.append("training_history_older_than_90_days")
        if local.month not in {warmup.month, datetime.fromtimestamp(end, UTC).astimezone(NY).month}:
            warnings.append("unvalidated_calendar_season")
        if self.metadata["is_demo"]:
            warnings.append("synthetic_training_data")
        low = bool(warnings)
        forecasts: dict[str, Any] = {}
        for key, task in self.metadata["tasks"].items():
            direction = int(key.startswith("arrivals"))
            bins = int(key.rsplit("_", 1)[1]) // 15
            values = builder.row(station, at, direction, bins)
            has_week = bool(np.isfinite(values[15]))
            mode = "timely_history" if has_week else "archive_only"
            name = task["selected_history"] if has_week else task["selected_archive"]
            if low:
                name, mode = "seasonal", "low_confidence_fallback"
            if name == "lgbm_calendar":
                values = builder.row(station, at, direction, bins, use_lags=False)
            drivers: list[dict[str, Any]] = []
            if name.startswith("lgbm_"):
                model = self.models[f"{key}/{name}"]
                mean = float(max(0, model.predict(values[None, :], num_threads=1)[0]))
                contributions = model.predict(values[None, :], pred_contrib=True, num_threads=1)[0]
                top = np.argsort(np.abs(contributions[:-1]))[-5:][::-1]
                drivers = [
                    {
                        "feature": FEATURES[int(i)],
                        "log_mean_contribution": float(contributions[i]),
                        "value": float(values[i]) if np.isfinite(values[i]) else None,
                    }
                    for i in top
                ]
            else:
                mean = float(values[16] if name == "recent" else values[12])
                drivers = [
                    {"feature": "station_weekday_hour_historical_mean", "value": float(values[12])},
                    {"feature": "historical_support", "value": float(values[13])},
                ]
            calibration = task["candidates"][name]["calibration"]
            lo, hi = interval(np.asarray([mean]), calibration)
            forecasts[key] = {
                "mean": mean,
                "interval": [int(lo[0]), int(hi[0])],
                "interval_nominal_coverage": calibration["coverage_target"],
                "interval_method": calibration["method"],
                "interval_validated_for_this_context": not low,
                "model": name,
                "feature_mode": mode,
                "confidence": "low" if low else "evaluated_winter_context",
                "prior_day_available": bool(np.isfinite(values[14])),
                "prior_week_available": has_week,
                "dispersion_alpha": calibration["count_dispersion_alpha"],
                "drivers": drivers,
            }
        if history is None:
            warnings.append("recent_trip_counts_unavailable_using_archive_only_path")
        risk = inventory_risk(forecasts, inventory, at, station, self.config.seed, low)
        return {
            "station_id": station,
            "as_of": datetime.fromtimestamp(at, UTC).isoformat(),
            "model_version": self.metadata["version"],
            "is_demo": self.metadata["is_demo"],
            "retrospective_replay": replay,
            "training_age_days": age,
            "warnings": warnings,
            "forecasts": forecasts,
            "availability_risk": risk,
            "target_semantics": "Observed completed rides, not unconstrained latent demand; "
            "no inferred historical inventory or calibrated stockout labels",
        }
