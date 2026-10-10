"""Offline provider-contract tests: exact commits, no credential forwarding/retry."""

from types import ModuleType

import httpx
import pytest

from app import render_release


@pytest.fixture
def release_module() -> ModuleType:
    return render_release


@pytest.mark.parametrize(
    "origin", ["http://app.example", "https://secret@app.example", "https://app.example/path"]
)
def test_release_rejects_unsafe_origin(release_module: ModuleType, origin: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))
    ) as client:
        with pytest.raises(ValueError):
            release_module.release(client, "srv-test", "a" * 40, origin)


@pytest.mark.parametrize("wrong_model", [False, True])
def test_release_smokes_with_separate_credential_free_client(
    release_module: ModuleType, monkeypatch: pytest.MonkeyPatch, wrong_model: bool
) -> None:
    requests: list[httpx.Request] = []
    sha = "a" * 40

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-test-only"
        if request.method == "POST":
            assert request.content == ('{"commitId":"' + sha + '"}').encode()
            return httpx.Response(201, json={"id": "dep-test"})
        return httpx.Response(200, json={"status": "live", "commit": {"id": sha}})

    def public(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "authorization" not in request.headers
        assert request.url.host == "app.example"
        if request.url.path == "/":
            return httpx.Response(200, text='<div id="root"></div>')
        if request.url.path == "/api/v1/forecasts/model":
            return httpx.Response(
                200,
                json={
                    "version": "wrong-version"
                    if wrong_model
                    else release_module.expected_model_version()
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.Client
    with real_client(
        transport=httpx.MockTransport(provider),
        headers={"Authorization": "Bearer private-test-only"},
    ) as client:
        monkeypatch.setattr(
            release_module.httpx,
            "Client",
            lambda **kwargs: real_client(transport=httpx.MockTransport(public), **kwargs),
        )
        if wrong_model:
            with pytest.raises(ValueError, match="live model"):
                release_module.release(client, "srv-test", sha, "https://app.example")
        else:
            assert (
                release_module.release(client, "srv-test", sha, "https://app.example") == "dep-test"
            )
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.parametrize("status", ["build_failed", "pre_deploy_failed", "update_failed"])
def test_release_fails_closed(release_module: ModuleType, status: str) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "dep-test"} if request.method == "POST" else {"status": status}
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(ValueError, match="failed"):
            release_module.release(client, "srv-test", "a" * 40, "https://app.example")


def test_ambiguous_deploy_write_is_never_retried(release_module: ModuleType) -> None:
    calls = 0

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("Simulated timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(httpx.ReadTimeout):
            release_module.release(client, "srv-test", "a" * 40, "https://app.example")
    assert calls == 1


def test_live_wrong_commit_fails_before_any_public_smoke(release_module: ModuleType) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "dep-test"}
            if request.method == "POST"
            else {"status": "live", "commit": {"id": "b" * 40}},
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(ValueError, match="requested commit"):
            release_module.release(client, "srv-test", "a" * 40, "https://app.example")
