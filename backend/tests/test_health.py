from httpx import AsyncClient

from app.main import app
from app.persistence.database import SCHEMA_REVISION, get_session


class HealthySession:
    async def execute(self, _: object) -> "HealthySession":
        return self

    def scalar_one_or_none(self) -> str:
        return SCHEMA_REVISION


async def test_liveness_returns_versioned_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"
    assert response.headers["x-request-id"]


async def test_unknown_route_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"]


async def test_readiness_reports_database_check(client: AsyncClient) -> None:
    async def override_session() -> HealthySession:
        return HealthySession()

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok"}


async def test_spatial_query_rejects_invalid_geography(client: AsyncClient) -> None:
    response = await client.get("/api/v1/stations/nearby?lat=100&lon=-73.9&radius=99999")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_readiness_handles_connection_and_dns_failure(client: AsyncClient) -> None:
    class UnavailableSession:
        async def execute(self, _: object) -> None:
            raise OSError("Database DNS unavailable")

    async def override_session() -> UnavailableSession:
        return UnavailableSession()

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


async def test_readiness_rejects_old_schema(client: AsyncClient) -> None:
    class OldSchema(HealthySession):
        def scalar_one_or_none(self) -> str:
            return "20261010_0002"

    async def override_session() -> OldSchema:
        return OldSchema()

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
