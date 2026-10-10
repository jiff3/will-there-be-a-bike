"""Explicit operator commands; HTTP never exposes arbitrary job execution."""

import argparse
import asyncio
import json
import uuid

from sqlalchemy import text

from app.modules.monitoring.jobs import run_job
from app.persistence.database import engine


async def execute(command: str, user_id: str | None) -> None:
    try:
        if command == "grant-admin":
            async with engine.begin() as connection:
                result = await connection.execute(
                    text("UPDATE users SET is_admin=true WHERE id=:id RETURNING id"),
                    {"id": uuid.UUID(str(user_id))},
                )
                if result.scalar_one_or_none() is None:
                    raise ValueError("No such user")
            print(json.dumps({"admin_user_id": user_id}))
        else:
            job = await run_job(command, force=False)
            print(json.dumps(job))
            if job["status"] == "failed":
                raise SystemExit(1)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted local operations")
    parser.add_argument(
        "command", choices=["live", "alerts", "forecasts", "retention", "grant-admin"]
    )
    parser.add_argument("--user-id")
    args = parser.parse_args()
    if args.command == "grant-admin" and args.user_id is None:
        parser.error("--user-id is required")
    asyncio.run(execute(args.command, args.user_id))


if __name__ == "__main__":
    main()
