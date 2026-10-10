"""Deterministic fault injection at provider, configuration, and API boundaries."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.ingestion.live import stale, status_records, timestamp, walking_distance
from app.modules.ingestion.sources import RateLimited, SourceClient


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_delayed_or_failed_provider_is_bounded_and_recovers(
    error: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    delays: list[int] = []
    monkeypatch.setattr("app.modules.ingestion.sources.time.sleep", delays.append)

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 4:
            raise error("injected failure", request=request)
        return httpx.Response(200, json={"recovered": True})

    source = SourceClient(httpx.Client(transport=httpx.MockTransport(respond)))
    assert source.get("https://fixture.invalid/status").json() == {"recovered": True}
    assert len(calls) == 4 and delays == [1, 2, 4]


def test_exhausted_timeouts_and_nonretryable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[int] = []
    monkeypatch.setattr("app.modules.ingestion.sources.time.sleep", delays.append)

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("injected delay", request=request)

    source = SourceClient(httpx.Client(transport=httpx.MockTransport(fail)))
    with pytest.raises(httpx.ReadTimeout):
        source.get("https://fixture.invalid/status")
    assert delays == [1, 2, 4]
    source = SourceClient(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    )
    with pytest.raises(httpx.HTTPStatusError):
        source.get("https://fixture.invalid/status")
    assert delays == [1, 2, 4]  # Never retry permanent 404s.


def test_long_rate_limit_never_sleeps_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[int] = []
    monkeypatch.setattr("app.modules.ingestion.sources.time.sleep", delays.append)
    source = SourceClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "3600"})
            )
        )
    )
    with pytest.raises(RateLimited) as caught:
        source.get("https://fixture.invalid/status")
    assert caught.value.retry_after == 3600 and delays == []


@pytest.mark.parametrize(
    "document",
    [
        {"data": {"feeds": []}},
        {
            "data": {
                "feeds": [{"name": "station_information", "url": "https://fixture.invalid/info"}]
            }
        },
    ],
)
def test_incomplete_discovery_rejected(document: dict[str, Any]) -> None:
    source = SourceClient(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document)))
    )
    with pytest.raises(ValueError, match="missing"):
        source.feeds()


def envelope(**changes: Any) -> dict[str, Any]:
    return {
        "version": "2.3",
        "last_updated": 1735732800,
        "ttl": 60,
        "data": {
            "stations": [
                {
                    "station_id": "QA",
                    "last_reported": 1735732800,
                    "num_bikes_available": 3,
                    "num_docks_available": 7,
                    "is_installed": True,
                    "is_renting": 1,
                    "is_returning": 0,
                    **changes,
                }
            ]
        },
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"num_bikes_available": True},
        {"num_docks_available": 1.5},
        {"is_renting": 1.0},
        {"is_renting": "true"},
        {"last_reported": True},
        {"last_reported": "2025-01-01T12:00:00"},
        {"last_reported": 1735732801},
        {"station_id": " "},
    ],
)
def test_invalid_observations_are_excluded_not_coerced(changes: dict[str, Any]) -> None:
    assert status_records(envelope(**changes)) == ([], 1)


@pytest.mark.parametrize("field", ["reported", "updated", "fetched"])
def test_freshness_boundary_and_future_times(field: str) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    values = {"reported": now, "updated": now, "fetched": now}
    values[field] = now - timedelta(seconds=300)
    assert not stale(**values, now=now)
    values[field] -= timedelta(microseconds=1)
    assert stale(**values, now=now)
    values[field] = now + timedelta(microseconds=1)
    assert stale(**values, now=now)


@pytest.mark.parametrize(
    "points", [(90, 0, -90, 0), (0, 179.99, 0, -179.99), (40.7, -74, 40.8, -73.9)]
)
def test_spatial_symmetry_poles_and_antimeridian(points: tuple[float, ...]) -> None:
    forward = walking_distance(*points)
    backward = walking_distance(*points[2:], *points[:2])
    assert forward == pytest.approx(backward) and forward > 0
    if points[1] == 179.99:
        assert forward < 3000


@pytest.mark.parametrize(
    "config",
    [
        {"auth_cookie_secure": False},
        {"app_public_origin": "http://example.com"},
        {"database_url": "postgresql+asyncpg://bike:change-me@db/bike"},
        {"app_debug": True},
        {"app_cors_origins": ["*"]},
        {"app_public_origin": "https://user:pass@example.com"},
    ],
)
def test_production_missing_or_unsafe_configuration_fails_closed(config: dict[str, Any]) -> None:
    safe = {
        "app_env": "production",
        "auth_cookie_secure": True,
        "app_public_origin": "https://example.com",
        "database_url": "postgresql+asyncpg://qa:private@db/bike",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**safe, **config})


async def test_extreme_replay_date_returns_validation_not_500(client: httpx.AsyncClient) -> None:
    for path in ("/api/v1/replay/day", "/api/v1/replay/stations/QA/comparisons"):
        response = await client.get(path, params={"day": "9999-12-31"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_boolean_epoch_is_not_a_valid_timestamp() -> None:
    with pytest.raises(ValueError):
        timestamp(True)
