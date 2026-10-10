"""Managed URL adaptation, single-origin assets and explicit demo boundaries."""

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import install_exception_handlers
from app.core.frontend import install_frontend
from app.modules.ingestion.demo import rides
from app.modules.replay.cli import parse_bin


@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_errors_keep_production_headers_and_metrics(monkeypatch, chunked):
    from app.core import errors

    settings = Settings(
        _env_file=None,
        app_env="production",
        auth_cookie_secure=True,
        app_public_origin="https://app.example",
        database_url="postgresql://owner:private@db/bike",
    )
    monkeypatch.setattr(errors, "get_settings", lambda: settings)
    records = []
    monkeypatch.setattr(errors.metrics, "record", lambda *args: records.append(args))
    api = FastAPI()
    api.add_middleware(errors.RequestContextMiddleware)

    async def chunks():
        yield b"x" * 70000

    async with AsyncClient(
        transport=ASGITransport(app=api), base_url="https://app.example"
    ) as client:
        response = await client.post(
            "/api/v1/auth/login", content=chunks() if chunked else b"x" * 70000
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert len(records) == 1 and records[0][1] == 413


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
def test_managed_database_url_keeps_encoded_credentials_and_ssl(scheme: str) -> None:
    settings = Settings(
        _env_file=None, database_url=f"{scheme}://owner:p%40ss@db:5432/bike?sslmode=require"
    )
    assert settings.database_url == "postgresql+asyncpg://owner:p%40ss@db:5432/bike?ssl=require"
    assert "p%40ss" not in repr(settings)


def test_production_cors_defaults_to_exact_https_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_CORS_ORIGINS", raising=False)
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_public_origin="https://bikes.example.org",
        auth_cookie_secure=True,
        database_url="postgresql://owner:private@db/bike",
    )
    assert settings.app_cors_origins == ["https://bikes.example.org"]


@pytest.mark.parametrize("url", ["sqlite:///bike.db", "mysql://localhost/bike", "https://db/bike"])
def test_wrong_database_driver_fails_early(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "mysql://owner:do-not-log-private@db/bike",
        "postgresql://owner:do-not-log-private@db/bike",
        "postgresql://owner:do-not-log-private@db\uff0fhost/bike",
    ],
)
def test_startup_validation_never_prints_database_credentials(url: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=url,
            app_public_origin="http://app.example",
            auth_cookie_secure=False,
        )
    assert "do-not-log-private" not in str(caught.value)


def test_demo_rejects_background_provider_mutation() -> None:
    with pytest.raises(ValidationError, match="provider jobs disabled"):
        Settings(_env_file=None, demo_mode=True, background_jobs_enabled=True)


def test_synthetic_trip_fixture_is_deterministic_and_replay_is_opt_in() -> None:
    assert rides() == rides()
    rows = list(csv.DictReader(io.StringIO(rides())))
    assert len(rows) > 200 and len({row["ride_id"] for row in rows}) == len(rows)
    assert all(row["ride_id"].startswith("SYNTHETIC-") for row in rows)
    line = json.dumps(
        {
            "station_id": "DEMO-A",
            "bucket_start": "2025-02-28T05:00:00+00:00",
            "is_demo": True,
            "departure_coverage": True,
            "arrival_coverage": True,
            "departures": 0,
            "arrivals": 0,
        }
    )
    with pytest.raises(ValueError):
        parse_bin(line)
    assert parse_bin(line, allow_demo=True)["departures"] == 0


async def test_single_origin_spa_assets_api_errors_and_traversal(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Built React application</h1>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app-123.js").write_text("console.log('built')", encoding="utf-8")
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/api/v1/probe")
    def probe() -> dict[str, bool]:
        return {"api": True}

    install_frontend(app, tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://bikes.example.org"
    ) as client:
        for path in ["/", "/commutes", "/notifications", "/replay", "/account", "/status"]:
            response = await client.get(path)
            assert response.status_code == 200 and "Built React" in response.text
            assert response.headers["Cache-Control"] == "no-cache"
        assert (await client.head("/commutes")).content == b""
        asset = await client.get("/assets/app-123.js")
        assert asset.status_code == 200 and "immutable" in asset.headers["Cache-Control"]
        assert (await client.get("/api/v1/probe")).json() == {"api": True}
        for path in ["/api/v1/missing", "/assets/missing.js", "/.env", "/%2e%2e/.env"]:
            response = await client.get(path)
            assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
        assert (await client.post("/commutes")).status_code == 405


def test_missing_frontend_build_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="built index.html"):
        install_frontend(FastAPI(), tmp_path)


async def test_synthetic_publication_requires_demo_workspace(tmp_path: Path) -> None:
    from app.modules.replay.cli import publish

    with pytest.raises(ValueError, match="DEMO_MODE"):
        await publish(tmp_path / "not-opened.jsonl", allow_demo=True)
