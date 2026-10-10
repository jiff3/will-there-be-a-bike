from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.modules.ml.data import Array


def metrics(y: Array, prediction: Array) -> dict[str, Any]:
    if len(y) == 0:
        return {"n": 0, "mae": None, "rmse": None, "poisson_deviance": None}
    mu = np.maximum(np.asarray(prediction, dtype=np.float64), 1e-8)
    target = np.asarray(y, dtype=np.float64)
    if (
        (target < 0).any()
        or (prediction < 0).any()
        or not np.isfinite(target).all()
        or not np.isfinite(mu).all()
    ):
        raise ValueError("Metrics require finite nonnegative count targets/predictions")
    error = prediction - y
    positive = target > 0
    deviance = mu - target
    deviance[positive] += target[positive] * np.log(target[positive] / mu[positive])
    return {
        "n": len(y),
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "poisson_deviance": float(2 * deviance.mean()),
        "mean_observed": float(target.mean()),
        "mean_predicted": float(mu.mean()),
        "zero_fraction": float((target == 0).mean()),
    }


def calibrate(y: Array, prediction: Array, coverage: float) -> dict[str, Any]:
    if len(y) < 100:
        raise ValueError("At least 100 calibration rows required")
    residual = np.abs(y - prediction) / np.sqrt(1 + prediction)
    level = min(1.0, math.ceil((len(y) + 1) * coverage) / len(y))
    quantile = float(np.quantile(residual, level, method="higher"))
    # Dispersion for a transparent Gamma-Poisson simulation, NOT stockout calibration.
    excess = np.maximum(0, np.square(y - prediction) - prediction)
    alpha = float(np.clip(excess.sum() / max(1e-8, np.square(prediction).sum()), 0, 5))
    return {
        "coverage_target": coverage,
        "scaled_absolute_residual_quantile": quantile,
        "count_dispersion_alpha": alpha,
        "rows": len(y),
        "method": "split-validation scaled residual; empirical temporal coverage, not guaranteed",
    }


def interval(prediction: Array, calibration: dict[str, Any]) -> tuple[Array, Array]:
    spread = calibration["scaled_absolute_residual_quantile"] * np.sqrt(1 + prediction)
    return np.maximum(0, np.floor(prediction - spread)), np.ceil(prediction + spread)


def interval_metrics(y: Array, prediction: Array, calibration: dict[str, Any]) -> dict[str, float]:
    lo, hi = interval(prediction, calibration)
    return {
        "empirical_coverage": float(((lo <= y) & (y <= hi)).mean()),
        "mean_width": float((hi - lo).mean()),
    }
