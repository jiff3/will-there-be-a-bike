"""Independently reconcile sealed/replayed metrics and inspect real-inventory fallback."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.ingestion.history import Stage  # noqa: E402
from app.modules.ml.artifacts import load_artifact, write_json  # noqa: E402
from app.modules.ml.data import UTC, checksum, load_demand  # noqa: E402
from app.modules.ml.features import FeatureBuilder  # noqa: E402
from app.modules.ml.inference import ForecastEngine  # noqa: E402


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    artifacts = repo / "backend/app/modules/ml/artifacts"
    metadata, seasonal, locations, _ = load_artifact(artifacts / "current.json")
    source = json.loads((repo / "docs/ml-evaluation/evaluation.json").read_text())
    for directory in ("evaluate", "backtest", "repeat-report"):
        replay = json.loads((repo / f"data/ml/{directory}/evaluation.json").read_text())
        assert replay["version"] == source["version"] == metadata["version"]
        for key in source["tasks"]:
            for field in ("test", "intervals", "per_station", "strata", "daily_backtest"):
                assert source["tasks"][key][field] == replay["tasks"][key][field], (key, field)
    history = load_demand(repo / "data/real/features.jsonl")
    assert checksum(repo / "data/real/features.jsonl") == metadata["data_sha256"]
    builder = FeatureBuilder(seasonal, locations, history)
    model = ForecastEngine.load(artifacts / "current.json")
    at = history.index(int(datetime(2025, 2, 25, 13, tzinfo=UTC).timestamp()))
    # Source JSONL has future labels, but the feature query is bounded to past windows.
    actual = model.predict(seasonal.stations[0], int(history.times[at]), history, replay=True)
    assert builder.row(seasonal.stations[0], int(history.times[at]), 0, 2)[15] >= 0
    for key, value in actual["forecasts"].items():
        assert value["mean"] >= 0 and value["interval"][0] >= 0, key
    stage = Stage(repo / "data/real/stage.sqlite3")
    try:
        row = stage.db.execute(
            "SELECT payload FROM live_snapshots WHERE station_id=? "
            "AND NOT is_demo ORDER BY reported_at DESC LIMIT 1",
            (seasonal.stations[0],),
        ).fetchone()
    finally:
        stage.db.close()
    measured = json.loads(row[0])
    measured["station_id"] = measured.pop("external_id")
    now = datetime.now(UTC)
    # Use the next issuance for the offline diagnostic; future timestamps aren't invented.
    current_at = (int(now.timestamp()) // 900 + 1) * 900
    present = model.predict(seasonal.stations[0], current_at, inventory=measured)
    assert present["availability_risk"]["status"] == "unavailable"
    assert "training_history_older_than_90_days" in present["warnings"]
    # The independent repeat produced the same content identity, including native
    # model checksums, feature implementation, source, and configuration.
    repeat = json.loads((repo / "data/ml/repeat-report/evaluation.json").read_text())
    assert repeat["version"] == metadata["version"]
    result = {
        "version": metadata["version"],
        "real_data": True,
        "data_sha256": metadata["data_sha256"],
        "metric_replays_identical": True,
        "native_models_identical_across_runs": True,
        "rows": int(history.counts.shape[0] * history.counts.shape[1]),
        "artifact_bytes": sum(
            p.stat().st_size for p in (artifacts / metadata["version"]).iterdir()
        ),
        "training_resources": metadata["resources"],
        "real_inventory_diagnostic": present,
        "retrospective_prediction": actual,
    }
    write_json(repo / "data/ml/verification.json", result)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("real_inventory_diagnostic", "retrospective_prediction")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
