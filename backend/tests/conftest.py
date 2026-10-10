import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-postgis", action="store_true", help="Fail instead of skipping DB tests"
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    # Several integration tests mutate whole fixture tables. Never target application data.
    if os.environ.get("APP_ENV") == "test":
        name = urlsplit(os.environ.get("DATABASE_URL", "")).path.lstrip("/")
        if not name.endswith("_test"):
            raise pytest.UsageError(
                "APP_ENV=test requires a dedicated DATABASE_URL ending in _test"
            )
    elif session.config.getoption("--require-postgis"):
        raise pytest.UsageError("--require-postgis requires APP_ENV=test and a migrated test DB")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.name.startswith("test_postgis"):
            item.add_marker(pytest.mark.postgis)


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missed mock fails immediately rather than silently contacting a provider."""
    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

    def send(self: httpx.Client, *args: object, **kwargs: object) -> httpx.Response:
        if not isinstance(self._transport, httpx.MockTransport):
            raise AssertionError("Tests must use MockTransport; real outbound HTTP is forbidden")
        return sync_send(self, *args, **kwargs)

    async def asend(self: httpx.AsyncClient, *args: object, **kwargs: object) -> httpx.Response:
        if not isinstance(self._transport, (httpx.MockTransport, httpx.ASGITransport)):
            raise AssertionError(
                "Tests must use ASGI/MockTransport; real outbound HTTP is forbidden"
            )
        return await async_send(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", asend)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
