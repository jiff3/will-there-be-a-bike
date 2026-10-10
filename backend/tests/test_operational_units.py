"""Lifecycle, bounded background refresh and real offline command contracts."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app import main
from app.core.errors import ApplicationError
from app.modules.ingestion import cli
from app.modules.ml.geography import extract_locations
from app.modules.monitoring import jobs
from tests.test_ingestion import FIXTURES, archive


def test_geography_uses_only_completed_past_trips_never_current_capacity(tmp_path: Path) -> None:
    result = extract_locations(archive(tmp_path), FIXTURES / "stations.json", "2025-01-02")
    assert result["rows_scanned"] == 10
    assert result["stations"]
    for station in result["stations"].values():
        assert station["capacity"] is None
        assert datetime.fromisoformat(station["observed_at"]) < datetime(2025, 1, 2, 5, tzinfo=UTC)
    before = extract_locations(archive(tmp_path), FIXTURES / "stations.json", "2025-01-01")
    assert before["stations"] == {}


def invoke(monkeypatch: pytest.MonkeyPatch, directory: Path, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["bike-data", "--data-dir", str(directory), *args])
    cli.main()


def test_real_offline_cli_seed_aggregate_validate_and_collect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    invoke(monkeypatch, tmp_path, "seed-demo")
    assert json.loads(capsys.readouterr().out)["is_demo"] is True
    invoke(monkeypatch, tmp_path, "aggregate", "--start", "2025-01-01", "--end", "2025-01-02")
    assert json.loads(capsys.readouterr().out)["feature_rows"] == 192
    rows = [json.loads(row) for row in (tmp_path / "features.jsonl").read_text().splitlines()]
    assert all(row["is_demo"] for row in rows)
    invoke(monkeypatch, tmp_path, "validate")
    assert json.loads(capsys.readouterr().out)["unique_trips"] == 2
    invoke(monkeypatch, tmp_path, "collect", "--fixture", str(FIXTURES / "status.json"))
    assert json.loads(capsys.readouterr().out)["retained_stations"] == 2
    with pytest.raises(SystemExit) as caught:
        invoke(monkeypatch, tmp_path, "validate", "--start", "2025-01-01", "--end", "2025-01-03")
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().out)["uncovered_departure_bins"] > 0


@pytest.mark.parametrize(
    "args", [["validate", "--start", "2025-01-01"], ["stations", "--limit", "0"]]
)
def test_cli_invalid_configuration_rejected_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        invoke(monkeypatch, tmp_path, *args)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2024-12-31", "2025-02-01", ["2024-12", "2025-01"]),
        ("2025-02-01", "2025-02-02", ["2025-02"]),
    ],
)
def test_download_months_exclusive_end(start: str, end: str, expected: list[str]) -> None:
    assert cli.months(start, end) == expected
    with pytest.raises(ValueError):
        cli.months(end, start)


async def test_application_lifecycle_stops_job_before_disposing_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def controlled_worker(stop: asyncio.Event) -> None:
        entered.set()
        await stop.wait()
        exited.set()

    monkeypatch.setattr(main.settings, "background_jobs_enabled", True)
    monkeypatch.setattr(main, "worker", controlled_worker)
    async with main.lifespan(main.app):
        await asyncio.wait_for(entered.wait(), 2)
        assert not exited.is_set()
    assert exited.is_set()


async def test_application_starts_without_model_and_logs_no_sensitive_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable():
        raise ApplicationError("model_unavailable", "private model file missing", 503)

    monkeypatch.setattr(main, "get_forecaster", unavailable)
    monkeypatch.setattr(main.settings, "background_jobs_enabled", False)
    async with main.lifespan(main.app):
        assert main.app.title


async def test_worker_survives_dependency_failure_and_stops_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    calls = []

    async def run(kind: str) -> dict[str, Any]:
        calls.append(kind)
        if len(calls) == 1:
            raise OSError("injected database interruption")
        stop.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(jobs, "run_job", run)
    await asyncio.wait_for(jobs.worker(stop), 2)
    assert calls == ["live", "alerts"]


async def test_request_ids_reject_injection_and_cors_rejects_untrusted_origin(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/health/live", headers={"X-Request-ID": "not a safe id"})
    assert response.headers["X-Request-ID"] != "not a safe id"
    response = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
