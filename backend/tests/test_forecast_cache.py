"""Immutable, bounded, version-aware count caching, never inventory/risk caching."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import ApplicationError
from app.modules.forecasts import service


@pytest.fixture(autouse=True)
def isolate_memory_cache():
    service._counts.clear()
    yield
    service._counts.clear()


def test_cache_bound_and_copy_on_store() -> None:
    at = datetime.now(UTC)
    payload = {"forecasts": {"departures_30": {"mean": 5}}}
    for i in range(service.MEMORY_CACHE_LIMIT + 2):
        service.remember((str(i), "version", at), payload)
    payload["forecasts"]["departures_30"]["mean"] = 999
    assert len(service._counts) == service.MEMORY_CACHE_LIMIT
    assert ("0", "version", at) not in service._counts
    assert service._counts[("2", "version", at)]["forecasts"]["departures_30"]["mean"] == 5


async def test_memory_hit_returns_independent_copy_without_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = datetime.now(UTC).replace(second=0, microsecond=0)
    model = SimpleNamespace(
        metadata={"version": "qa-version", "created_at": "2025-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(service, "get_forecaster", lambda: model)
    service.remember(("A", "qa-version", at), {"forecasts": {"mean": 3}})

    class NoDatabase:
        def connect(self):
            raise AssertionError("Memory count cache hit must not query DB")

    monkeypatch.setattr(service, "engine", NoDatabase())
    first = await service.count_forecast("A", at)
    first["forecasts"]["mean"] = 999
    first["availability_risk"] = {"incorrectly_cached": True}
    assert await service.count_forecast("A", at) == {"forecasts": {"mean": 3}}


@pytest.mark.parametrize("change", ["station", "version", "expiry", "issuance"])
async def test_changed_identity_or_expired_cache_never_reused(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    at = now - timedelta(hours=1) if change == "expiry" else now
    service.remember(("A", "qa-version", at), {"forecasts": {"mean": 3}})
    model = SimpleNamespace(
        metadata={
            "version": "new-version" if change == "version" else "qa-version",
            "created_at": "2025-01-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(service, "get_forecaster", lambda: model)

    class ExpectedMiss:
        def connect(self):
            raise OSError("expected cache miss")

    monkeypatch.setattr(service, "engine", ExpectedMiss())
    with pytest.raises(OSError, match="expected cache miss"):
        await service.count_forecast(
            "B" if change == "station" else "A",
            at + timedelta(minutes=15) if change == "issuance" else at,
        )


async def test_failed_native_inference_is_safe_503_not_partial_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, *args):
            return None

    class Database:
        def connect(self):
            return Connection()

    def fail(*args):
        raise RuntimeError("sensitive native model path")

    monkeypatch.setattr(service, "engine", Database())
    monkeypatch.setattr(
        service,
        "get_forecaster",
        lambda: SimpleNamespace(
            metadata={"version": "qa", "created_at": "2025-01-01T00:00:00Z"},
            predict=fail,
        ),
    )
    with pytest.raises(ApplicationError) as caught:
        await service.count_forecast("QA", datetime.now(UTC))
    assert caught.value.status_code == 503 and "sensitive" not in caught.value.message
    assert service._counts == {}
