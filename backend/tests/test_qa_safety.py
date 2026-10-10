"""Destructive test-fixture commands must refuse application databases before SQL."""

from types import SimpleNamespace

import pytest

from tests.support import qa


@pytest.mark.parametrize(
    "environment,name,jobs",
    [
        ("production", "bike_test", False),
        ("test", "bike", False),
        ("test", "", False),
        ("test", "bike_test", True),
    ],
)
def test_fixture_guard_refuses_unsafe_configuration(
    monkeypatch: pytest.MonkeyPatch, environment: str, name: str, jobs: bool
) -> None:
    monkeypatch.setattr(
        qa,
        "get_settings",
        lambda: SimpleNamespace(
            app_env=environment,
            database_url=f"postgresql+asyncpg://localhost/{name}",
            background_jobs_enabled=jobs,
        ),
    )
    with pytest.raises(RuntimeError):
        qa.guard()


def test_fixture_guard_accepts_isolated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        qa,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="test",
            database_url="postgresql+asyncpg://localhost/bike_qa_test",
            background_jobs_enabled=False,
        ),
    )
    qa.guard()


@pytest.mark.parametrize("operation", [qa.seed, qa.observations])
async def test_direct_fixture_helpers_also_guard_before_connecting(
    monkeypatch: pytest.MonkeyPatch, operation
) -> None:
    def refuse() -> None:
        raise RuntimeError("unsafe fixture target")

    monkeypatch.setattr(qa, "guard", refuse)
    with pytest.raises(RuntimeError, match="unsafe fixture target"):
        await operation()
