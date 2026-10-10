"""Start a real HTTP server against an unreachable DB and verify fail-closed responses."""

import os
import subprocess
import sys
import time

import httpx


def main() -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://unreachable:unreachable@127.0.0.1:9/unreachable",
        "APP_ENV": "development",
        "APP_DEBUG": "false",
        "BACKGROUND_JOBS_ENABLED": "false",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--no-access-log",
            "--log-level",
            "warning",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(base_url="http://127.0.0.1:8001", timeout=10) as client:
            for _ in range(50):
                try:
                    live = client.get("/api/v1/health/live")
                    if live.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Failure-test server did not start")
            for route in ("/api/v1/health/ready", "/api/v1/stations"):
                response = client.get(route)
                assert response.status_code == 503, response.text
                assert response.headers["X-Request-ID"]
                assert "unreachable" not in response.text
            response = client.post(
                "/api/v1/auth/login",
                headers={"Origin": environment.get("APP_PUBLIC_ORIGIN", "http://localhost:5173")},
                json={"email": "nobody@example.com", "password": "synthetic failure-test password"},
            )
            assert response.status_code == 503, response.text
            print("Real HTTP failure checks passed: liveness=200, readiness/stations/login=503")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
