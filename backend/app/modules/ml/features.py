"""One implementation for batch and inference; targets are separate from features."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from app.modules.ml.data import STEP, UTC, Array, Demand, epoch

NY = ZoneInfo("America/New_York")
FEATURES = [
    "station_code",
    "latitude",
    "longitude",
    "capacity",
    "weekday",
    "quarter_of_day",
    "weekend",
    "month",
    "day_of_year_sin",
    "day_of_year_cos",
    "clock_sin",
    "clock_cos",
    "seasonal_mean",
    "seasonal_support",
    "prior_day",
    "prior_week",
    "recent_comparable",
    "day_missing",
    "week_missing",
]
SCHEMA = {
    "version": 1,
    "names": FEATURES,
    "dtype": "float32",
    "timezone": "America/New_York",
    "count_order": ["departures", "arrivals"],
    "bucket_seconds": STEP,
}
SCHEMA_HASH = hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest()


def validate_features(values: Array) -> None:
    if values.ndim != 2 or values.shape[1] != len(FEATURES) or values.dtype != np.float32:
        raise ValueError("Feature schema mismatch: require ordered float32 matrix")
    if np.isinf(values).any():
        raise ValueError("Infinite feature values")
    required = [0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17, 18]
    if not np.isfinite(values[:, required]).all():
        raise ValueError("Missing required calendar/seasonal features")


class Seasonal:
    """Station x weekday x hour means, fitted ONLY on the pre-training warm-up."""

    def __init__(self, stations: list[str], means: Array, support: Array, cutoff: int) -> None:
        self.stations, self.means, self.support, self.cutoff = stations, means, support, cutoff
        self.lookup = {station: index for index, station in enumerate(stations)}

    @classmethod
    def fit(cls, demand: Demand, cutoff: int, delay_seconds: int = 86400) -> Seasonal:
        if delay_seconds < 0:
            raise ValueError("Seasonal publication delay cannot be negative")
        sums = np.zeros((len(demand.stations), 7, 24, 2), dtype=np.float64)
        support = np.zeros_like(sums)
        for t, stamp in enumerate(demand.times):
            if stamp + STEP + delay_seconds > cutoff:
                break
            local = datetime.fromtimestamp(int(stamp), UTC).astimezone(NY)
            values = demand.counts[:, t, :]
            valid = np.isfinite(values)
            sums[:, local.weekday(), local.hour, :] += np.nan_to_num(values)
            support[:, local.weekday(), local.hour, :] += valid
        # Shrink sparse cells toward station-hour averages; no final-period information.
        hour_mean = sums.sum(axis=1) / np.maximum(1, support.sum(axis=1))
        means = (sums + 4 * hour_mean[:, None, :, :]) / (support + 4)
        return cls(demand.stations, means.astype(np.float32), support.astype(np.float32), cutoff)

    def value(self, station: str, at: int, direction: int, bins: int) -> tuple[float, float]:
        if at < self.cutoff:
            raise ValueError("Seasonal table is not yet available at this as-of time")
        s = self.lookup.get(station)
        mean, support = 0.0, 0.0
        for b in range(bins):
            local = datetime.fromtimestamp(at + b * STEP, UTC).astimezone(NY)
            if s is None:
                mean += float(self.means[:, local.weekday(), local.hour, direction].mean())
            else:
                mean += float(self.means[s, local.weekday(), local.hour, direction])
                support += float(self.support[s, local.weekday(), local.hour, direction]) / bins
        return mean, support

    def document(self) -> dict[str, Any]:
        return {
            "stations": self.stations,
            "means": self.means.tolist(),
            "support": self.support.tolist(),
            "cutoff": self.cutoff,
        }

    @classmethod
    def restore(cls, body: dict[str, Any]) -> Seasonal:
        stations = body["stations"]
        means = np.asarray(body["means"], dtype=np.float32)
        support = np.asarray(body["support"], dtype=np.float32)
        if not 1 <= len(stations) <= 120 or len(set(stations)) != len(stations):
            raise ValueError("Invalid artifact station map")
        expected = (len(stations), 7, 24, 2)
        if means.shape != expected or support.shape != expected:
            raise ValueError("Invalid seasonal shape")
        if (
            not np.isfinite(means).all()
            or (means < 0).any()
            or not np.isfinite(support).all()
            or (support < 0).any()
        ):
            raise ValueError("Invalid seasonal values")
        return cls(stations, means, support, int(body["cutoff"]))


class FeatureBuilder:
    def __init__(
        self,
        seasonal: Seasonal,
        locations: dict[str, dict[str, Any]],
        history: Demand | None = None,
        history_delay_minutes: int = 1440,
    ) -> None:
        if history_delay_minutes < 0:
            raise ValueError("History delay cannot be negative")
        self.seasonal, self.locations, self.history = seasonal, locations, history
        self.delay = history_delay_minutes * 60
        self.history_lookup = {s: i for i, s in enumerate(history.stations)} if history else {}

    def lag(self, station: str, at: int, days: int, direction: int, bins: int) -> float:
        if self.history is None or station not in self.history_lookup:
            return float("nan")
        local = datetime.fromtimestamp(at, UTC).astimezone(NY)
        earlier = int((local - timedelta(days=days)).timestamp())
        # Whole requested window must have closed and passed the provider's publication delay.
        if earlier + bins * STEP > at - self.delay:
            return float("nan")
        start = self.history.index(earlier)
        if start < 0 or start + bins > len(self.history.times):
            return float("nan")
        values = self.history.counts[self.history_lookup[station], start : start + bins, direction]
        return float(values.sum()) if np.isfinite(values).all() else float("nan")

    def row(self, station: str, at: int, direction: int, bins: int, use_lags: bool = True) -> Array:
        if bins not in (2, 4) or direction not in (0, 1) or at % STEP:
            raise ValueError("Supported targets: arrivals/departures, 30/60 minutes on UTC grid")
        local = datetime.fromtimestamp(at, UTC).astimezone(NY)
        quarter = local.hour * 4 + local.minute // 15
        seasonal, support = self.seasonal.value(station, at, direction, bins)
        geo = self.locations.get(station, {})
        geo_known = bool(geo) and epoch(geo["observed_at"]) <= at
        day = self.lag(station, at, 1, direction, bins) if use_lags else float("nan")
        week = self.lag(station, at, 7, direction, bins) if use_lags else float("nan")
        previous = self.lag(station, at, 14, direction, bins) if use_lags else float("nan")
        comparable = [v for v in (day, week, previous) if np.isfinite(v)]
        recent = float(np.mean(comparable)) if comparable else seasonal
        values = [
            self.seasonal.lookup.get(station, -1),
            geo.get("lat", np.nan) if geo_known else np.nan,
            geo.get("lon", np.nan) if geo_known else np.nan,
            geo.get("capacity", np.nan) if geo_known else np.nan,
            local.weekday(),
            quarter,
            int(local.weekday() >= 5),
            local.month,
            np.sin(2 * np.pi * local.timetuple().tm_yday / 366),
            np.cos(2 * np.pi * local.timetuple().tm_yday / 366),
            np.sin(2 * np.pi * quarter / 96),
            np.cos(2 * np.pi * quarter / 96),
            seasonal,
            support,
            day,
            week,
            recent,
            int(not np.isfinite(day)),
            int(not np.isfinite(week)),
        ]
        result = np.asarray([values], dtype=np.float32)
        validate_features(result)
        return np.asarray(result[0], dtype=np.float32)

    def matrix(self, indices: Array, direction: int, bins: int, use_lags: bool = True) -> Array:
        if self.history is None:
            raise ValueError("Batch features require explicit history input")
        if self.history.stations != self.seasonal.stations:
            raise ValueError("Evaluation station map differs from training map")
        s, t = indices[:, 0], indices[:, 1]
        stamps = self.history.times[t]
        if (stamps < self.seasonal.cutoff).any():
            raise ValueError("Seasonal features requested before fit cutoff")
        unique, inverse = np.unique(stamps, return_inverse=True)
        local = [datetime.fromtimestamp(int(v), UTC).astimezone(NY) for v in unique]
        weekday = np.asarray([v.weekday() for v in local])[inverse]
        quarter = np.asarray([v.hour * 4 + v.minute // 15 for v in local])[inverse]
        ordinal = np.asarray([v.timetuple().tm_yday for v in local])[inverse]
        result = np.full((len(indices), len(FEATURES)), np.nan, dtype=np.float32)
        result[:, 0], result[:, 4], result[:, 5] = s, weekday, quarter
        result[:, 6] = weekday >= 5
        result[:, 7] = np.asarray([v.month for v in local])[inverse]
        result[:, 8:12] = np.column_stack(
            (
                np.sin(2 * np.pi * ordinal / 366),
                np.cos(2 * np.pi * ordinal / 366),
                np.sin(2 * np.pi * quarter / 96),
                np.cos(2 * np.pi * quarter / 96),
            )
        )
        for station, code in self.seasonal.lookup.items():
            geo = self.locations.get(station)
            if geo:
                mask = (s == code) & (stamps >= epoch(geo["observed_at"]))
                result[mask, 1:4] = [geo["lat"], geo["lon"], geo.get("capacity", np.nan)]
        result[:, 12:14] = 0
        for b in range(bins):
            future = [datetime.fromtimestamp(int(v) + b * STEP, UTC).astimezone(NY) for v in unique]
            wd = np.asarray([v.weekday() for v in future])[inverse]
            hour = np.asarray([v.hour for v in future])[inverse]
            result[:, 12] += self.seasonal.means[s, wd, hour, direction]
            result[:, 13] += self.seasonal.support[s, wd, hour, direction] / bins
        comparables = []
        for days, column in ((1, 14), (7, 15), (14, None)):
            values = np.full(len(indices), np.nan, dtype=np.float32)
            if use_lags:
                earlier = np.asarray([int((v - timedelta(days=days)).timestamp()) for v in local])[
                    inverse
                ]
                offsets = (earlier - self.history.times[0]) // STEP
                valid = (offsets >= 0) & (offsets + bins <= len(self.history.times))
                valid &= earlier + bins * STEP <= stamps - self.delay
                values[valid] = 0
                for b in range(bins):
                    values[valid] += self.history.counts[s[valid], offsets[valid] + b, direction]
            if column is not None:
                result[:, column] = values
            comparables.append(values)
        comp = np.asarray(comparables)
        n = np.isfinite(comp).sum(axis=0)
        result[:, 16] = np.where(n > 0, np.nansum(comp, axis=0) / np.maximum(1, n), result[:, 12])
        result[:, 17], result[:, 18] = ~np.isfinite(result[:, 14]), ~np.isfinite(result[:, 15])
        validate_features(result)
        return result


def samples(demand: Demand, begin: int, end: int, direction: int, bins: int) -> tuple[Array, Array]:
    # Target END must be inside the split: purge overlapping 30/60-minute windows.
    sums = np.zeros(demand.counts.shape[:2], dtype=np.float32)
    valid = np.ones_like(sums, dtype=np.bool_)
    for b in range(bins):
        values = demand.counts[:, b:, direction]
        sums[:, : len(demand.times) - b] += np.nan_to_num(values)
        valid[:, : len(demand.times) - b] &= np.isfinite(values)
        if b:
            valid[:, -b:] = False
    valid &= (demand.times >= begin)[None, :] & (demand.times + bins * STEP <= end)[None, :]
    indices = np.argwhere(valid).astype(np.int32)
    return indices, sums[valid]
