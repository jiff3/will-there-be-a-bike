"""Managed single-process entry point; migrations remain a separate release job."""

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    if not 1 <= port <= 65535 or not 1 <= workers <= 2:
        raise ValueError("PORT must be 1..65535 and WEB_CONCURRENCY 1..2")
    # Do not trust arbitrary forwarded chains. Render's proxy peer is grouped
    # conservatively by default; operators can configure verified proxy IPs.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        access_log=False,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
