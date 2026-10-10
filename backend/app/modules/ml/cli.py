"""Explicit offline training commands; the API never starts a training job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.modules.ml.config import TrainingConfig
from app.modules.ml.data import epoch, load_demand


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducible bike-share count forecasting")
    commands = parser.add_subparsers(dest="command", required=True)
    geo = commands.add_parser(
        "geography", help="Recover pre-training coordinates from official trips"
    )
    geo.add_argument("--catalog", type=Path, required=True)
    geo.add_argument("--month", default="2025-01")
    geo.add_argument("--archive", type=Path)
    geo.add_argument("--before", default="2025-01-15")
    geo.add_argument("--output", type=Path, required=True)
    geo.add_argument("--keep-raw", action="store_true")
    train = commands.add_parser(
        "train", help="Fit, select, calibrate, held-out audit and seal artifact"
    )
    train.add_argument("--data", type=Path, required=True)
    train.add_argument("--locations", type=Path)
    train.add_argument("--config", type=Path)
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--report", type=Path, required=True)
    train.add_argument("--allow-demo", action="store_true")
    for command in ("evaluate", "backtest"):
        evaluation = commands.add_parser(command, help="Replay sealed artifact without retraining")
        evaluation.add_argument("--artifact", type=Path, required=True)
        evaluation.add_argument("--data", type=Path, required=True)
        evaluation.add_argument("--report", type=Path, required=True)
        evaluation.add_argument("--allow-demo", action="store_true")
    predict = commands.add_parser(
        "predict", help="Forecast; optionally simulate from timestamped inventory"
    )
    predict.add_argument("--artifact", type=Path, required=True)
    predict.add_argument("--station", required=True)
    predict.add_argument("--at", required=True, help="Timezone-aware, 15-minute-aligned as-of")
    predict.add_argument(
        "--history", type=Path, help="Completed trip history; never GBFS inventory"
    )
    predict.add_argument("--inventory", type=Path, help="One timestamped inventory JSON document")
    predict.add_argument(
        "--replay", action="store_true", help="Explicit retrospective event-time replay"
    )
    args = parser.parse_args()
    if args.command == "geography":
        from app.modules.ingestion.sources import SourceClient
        from app.modules.ml.geography import extract_locations

        client = SourceClient()
        try:
            if args.archive:
                archive = args.archive
            else:
                choices = client.archives(args.month)
                keys = [key for key, _ in choices if "JC-" not in key]
                if len(keys) != 1:
                    raise ValueError("Choose one NYC archive explicitly with --archive")
                print(json.dumps({"phase": "download", "key": keys[0]}), flush=True)
                archive = client.download(keys[0], args.output.parent / "raw")
            body = extract_locations(archive, args.catalog, args.before)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(body, sort_keys=True, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "phase": "geography",
                        "stations": len(body["stations"]),
                        "rows_scanned": body["rows_scanned"],
                        "sha256": body["sha256"],
                    }
                )
            )
            if not args.archive and not args.keep_raw:
                # Command-owned download; durable derived provenance is already saved.
                archive.unlink()
        finally:
            client.client.close()
    elif args.command == "train":
        from app.modules.ml.training import train_models

        config = (
            TrainingConfig.model_validate_json(args.config.read_text())
            if args.config
            else TrainingConfig()
        )
        print(
            json.dumps(
                train_models(
                    args.data, args.locations, config, args.output, args.report, args.allow_demo
                ),
                indent=2,
            )
        )
    elif args.command in ("evaluate", "backtest"):
        from app.modules.ml.training import evaluate_artifact

        print(
            json.dumps(
                evaluate_artifact(args.artifact, args.data, args.report, args.allow_demo), indent=2
            )
        )
    else:
        from app.modules.ml.inference import ForecastEngine

        engine = ForecastEngine.load(args.artifact)
        history = load_demand(args.history) if args.history else None
        inventory = json.loads(args.inventory.read_text()) if args.inventory else None
        print(
            json.dumps(
                engine.predict(
                    args.station, epoch(args.at), history, inventory, replay=args.replay
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
