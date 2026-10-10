"""Owner-authorized Render release: exact commit, bounded polling, verified TLS smoke.

Secrets come only from environment. Never print HTTP exceptions/URLs/headers that
could expose credentials. No automatic retry of an ambiguous deployment POST.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def expected_model_version() -> str:
    pointer = Path(__file__).parent / "modules" / "ml" / "artifacts" / "current.json"
    version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"v[1-9][0-9]*-[a-f0-9]{16}", version):
        raise ValueError("Release lacks an approved model version pointer")
    return version


def release(client: httpx.Client, service: str, sha: str, origin: str) -> str:
    if not re.fullmatch(r"srv-[a-z0-9]+", service) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Set a Render service ID and exact 40-character release commit SHA")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Public smoke target must be an exact HTTPS origin")
    result = client.post(
        f"https://api.render.com/v1/services/{service}/deploys", json={"commitId": sha}
    )
    result.raise_for_status()
    identifier = result.json()["id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"dep-[a-z0-9]+", identifier):
        raise ValueError("Unexpected Render deployment identifier")
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        response = client.get(f"https://api.render.com/v1/services/{service}/deploys/{identifier}")
        response.raise_for_status()
        data = response.json()
        status = data["status"]
        if status == "live":
            if data.get("commit", {}).get("id") != sha:
                raise ValueError("Live deploy did not confirm the requested commit")
            # A separate client must NOT send the Render API bearer token to the app.
            with httpx.Client(timeout=15, follow_redirects=False) as public:
                for path in ["/", "/api/v1/health/ready", "/api/v1/forecasts/model"]:
                    smoke = public.get(origin.rstrip("/") + path)
                    smoke.raise_for_status()
                    if path == "/" and 'id="root"' not in smoke.text:
                        raise ValueError("The live frontend entry point was not served")
                    if (
                        path == "/api/v1/forecasts/model"
                        and smoke.json().get("version") != expected_model_version()
                    ):
                        raise ValueError(
                            "The live model did not match the reviewed release artifact"
                        )
                if public.get(origin.rstrip("/") + "/api/v1/health/ready").json()["status"] != "ok":
                    raise ValueError("Live readiness did not report healthy dependencies")
            return identifier
        if status in {
            "build_failed",
            "pre_deploy_failed",
            "update_failed",
            "canceled",
            "deactivated",
        }:
            raise ValueError("Render release failed; inspect the provider's private deploy logs")
        time.sleep(15)
    raise TimeoutError("Release did not finish within the bounded wait; inspect provider status")


def main() -> None:
    try:
        token = os.environ["RENDER_API_KEY"]
        with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"}) as client:
            identifier = release(
                client,
                os.environ["RENDER_SERVICE_ID"],
                os.environ["RELEASE_SHA"],
                os.environ["APP_PUBLIC_ORIGIN"],
            )
        print(f"Verified Render release: {identifier}")
    except Exception as error:
        print(
            f"Release failed ({type(error).__name__}); inspect provider status. No secrets logged.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
