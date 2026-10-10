"""Failure responses never leak private fields or dependency details."""

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from app.main import app
from app.modules.auth.router import current_user
from app.modules.replay import service
from app.persistence import rider_repository


@pytest.mark.parametrize(
    "path,target", [("/api/v1/commutes", "list_commutes"), ("/api/v1/notifications", "inbox")]
)
async def test_private_database_failure(client, monkeypatch, path, target):
    async def authenticated():
        return {"id": uuid.uuid4()}

    async def fail(*args, **kwargs):
        raise OperationalError("private location sql", {"secret": "never expose"}, Exception())

    app.dependency_overrides[current_user] = authenticated
    monkeypatch.setattr(rider_repository, target, fail)
    try:
        response = await client.get(path)
        assert response.status_code == 503
        assert "private location" not in response.text and "never expose" not in response.text
        assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_replay_database_failure(client, monkeypatch):
    async def fail():
        raise OperationalError("private sql", {}, Exception())

    monkeypatch.setattr(service, "catalog", fail)
    response = await client.get("/api/v1/replay/catalog")
    assert response.status_code == 503 and "private sql" not in response.text
