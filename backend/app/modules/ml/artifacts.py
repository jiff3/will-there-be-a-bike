"""Deployment-approved local artifacts only. JSON + native text, never pickle/joblib."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.modules.ml.config import TrainingConfig
from app.modules.ml.data import checksum
from app.modules.ml.features import FEATURES, SCHEMA, SCHEMA_HASH, Seasonal


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def read_json(path: Path, limit: int = 10_000_000) -> Any:
    if path.stat().st_size > limit:
        raise ValueError("Artifact exceeds bounded JSON size")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_artifact(path: Path) -> Path:
    if path.is_file():
        pointer = read_json(path, 4096)
        version = pointer["version"]
        if (
            not isinstance(version, str)
            or not version.startswith("v1-")
            or not version[3:].isalnum()
        ):
            raise ValueError("Invalid model version pointer")
        return path.parent / version
    return path


def load_artifact(path: Path) -> tuple[dict[str, Any], Seasonal, dict[str, Any], dict[str, Any]]:
    import lightgbm as lgb

    root = resolve_artifact(path).resolve()
    manifest = read_json(root / "manifest.json", 100000)
    if manifest.get("schema") != SCHEMA or manifest.get("schema_hash") != SCHEMA_HASH:
        raise ValueError("Unsupported feature schema")
    if manifest.get("format_version") != 1 or manifest.get("lightgbm_version") != lgb.__version__:
        raise ValueError("Model runtime version mismatch")
    files = manifest["files"]
    if not isinstance(files, dict) or not 3 <= len(files) <= 16:
        raise ValueError("Invalid artifact manifest")
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or not name.endswith((".json", ".txt"))
        ):
            raise ValueError("Unsafe artifact path")
        file = root / name
        if file.resolve().parent != root or file.stat().st_size > 10_000_000:
            raise ValueError("Unsafe or oversized artifact")
        if checksum(file) != digest:
            raise ValueError("Artifact checksum mismatch")
    if not {"seasonal.json", "locations.json", "metadata.json"} <= files.keys():
        raise ValueError("Incomplete artifact")
    seasonal = Seasonal.restore(read_json(root / "seasonal.json"))
    locations = read_json(root / "locations.json")
    metadata = read_json(root / "metadata.json")
    if metadata.get("version") != manifest.get("version"):
        raise ValueError("Artifact version mismatch")
    implementation = metadata.get("implementation_sha256", {})
    for module in ("data.py", "features.py"):
        if implementation.get(module) != checksum(Path(__file__).parent / module):
            raise ValueError("Feature implementation differs; regenerate a compatible artifact")
    TrainingConfig.model_validate(metadata["config"])
    tasks = {f"{d}_{h}" for d in ("departures", "arrivals") for h in (30, 60)}
    if set(metadata["tasks"]) != tasks:
        raise ValueError("Incomplete forecasting targets")
    models: dict[str, Any] = {}
    for key, task in metadata["tasks"].items():
        required = {"seasonal", "recent", "lgbm_calendar", "lgbm_lags"}
        if set(task["candidates"]) != required:
            raise ValueError("Incomplete candidate metadata")
        if (
            task["selected_archive"] not in {"seasonal", "lgbm_calendar"}
            or task["selected_history"] not in required
        ):
            raise ValueError("Invalid approved candidate")
        for name, spec in task["candidates"].items():
            calibration = spec["calibration"]
            q = calibration["scaled_absolute_residual_quantile"]
            alpha = calibration["count_dispersion_alpha"]
            if (
                not math.isfinite(q)
                or q < 0
                or not math.isfinite(alpha)
                or not 0 <= alpha <= 5
                or not 0.5 <= calibration["coverage_target"] <= 0.99
            ):
                raise ValueError("Invalid uncertainty metadata")
            if not name.startswith("lgbm_"):
                continue
            file = spec["file"]
            if file not in files or not file.endswith(".txt"):
                raise ValueError("Unverified model file")
            model = lgb.Booster(model_file=str(root / file))
            if model.feature_name() != FEATURES or model.num_trees() > 400:
                raise ValueError("Native model feature/schema mismatch")
            models[f"{key}/{name}"] = model
    return metadata, seasonal, locations, models
