"""Run Locust in a separate process and sample optional local server RSS/CPU.

No host mutation, no CPU saturation target, no network calls other than the chosen
loopback API. Saves machine-readable evidence and fails on any HTTP errors.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import psutil


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--users", type=int, choices=range(1, 17), default=8)
    parser.add_argument("--seconds", type=int, choices=range(10, 121), default=30)
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--output", type=Path, default=Path("reports/load"))
    args = parser.parse_args()
    if urlsplit(args.host).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Only loopback QA targets are permitted")
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = args.output / "locust"
    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(Path(__file__).with_name("locustfile.py")),
        "--headless",
        "--host",
        args.host,
        "--users",
        str(args.users),
        "--spawn-rate",
        "4",
        "--run-time",
        f"{args.seconds}s",
        "--stop-timeout",
        "5",
        "--csv",
        str(prefix),
        "--csv-full-history",
        "--html",
        str(args.output / "report.html"),
        "--only-summary",
    ]
    server = psutil.Process(args.server_pid) if args.server_pid else None
    if server:
        server.cpu_percent()
    samples = []
    started = time.monotonic()
    final = args.output / "final-statistics.json"
    with (args.output / "console.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "BIKE_LOAD_FINAL_JSON": str(final.resolve())},
        )
        while process.poll() is None:
            if server:
                try:
                    samples.append(
                        {
                            "elapsed_seconds": time.monotonic() - started,
                            "rss_mib": server.memory_info().rss / 1024**2,
                            "cpu_percent_one_core": server.cpu_percent(),
                        }
                    )
                except psutil.NoSuchProcess:
                    process.terminate()
                    raise RuntimeError("API process exited during benchmark") from None
            time.sleep(0.5)
    rows = json.loads(final.read_text(encoding="utf-8"))
    summary = {
        "host": args.host,
        "users": args.users,
        "duration_requested_seconds": args.seconds,
        "elapsed_seconds": time.monotonic() - started,
        "exit_code": process.returncode,
        "statistics": rows,
        "server_resource_samples": samples,
        "resource_scope": "only supplied API PID; excludes PostgreSQL/browser/load generator",
        "max_api_rss_mib": max((s["rss_mib"] for s in samples), default=None),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"statistics", "server_resource_samples"}
            }
        )
    )
    if process.returncode or any(int(row["Failure Count"]) for row in rows):
        raise SystemExit("Load failed; inspect report.html and console.log")
    aggregate = next(row for row in rows if row["Name"] == "Aggregated")
    if float(aggregate["95%"]) > 2000:
        raise SystemExit("p95 exceeds the generous 2-second QA smoke budget")


if __name__ == "__main__":
    main()
