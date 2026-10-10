"""Account persistence; session-derived IDs only, parameterized SQL and explicit writes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.persistence.database import engine


async def limited(keys: list[str], maximum: int) -> bool:
    async with engine.begin() as connection:
        for key in keys:
            count = await connection.scalar(
                text("""
              INSERT INTO request_limits(key,window_start,attempts) VALUES(:key,now(),1)
              ON CONFLICT(key) DO UPDATE SET
                attempts=CASE WHEN request_limits.window_start < now()-interval '15 minutes'
                  THEN 1 ELSE request_limits.attempts+1 END,
                window_start=CASE WHEN request_limits.window_start < now()-interval '15 minutes'
                  THEN now() ELSE request_limits.window_start END RETURNING attempts
            """),
                {"key": key},
            )
            if int(count or 0) > maximum:
                # Returning commits the increment; throwing here would roll it back.
                return True
    return False


async def session_user(token_hash: str) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
          SELECT u.id,u.email,u.is_admin,s.csrf_hash,s.token_hash FROM auth_sessions s
          JOIN users u ON u.id=s.user_id WHERE s.token_hash=:token AND s.expires_at>now()
        """),
                    {"token": token_hash},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


async def store_session(
    user_id: uuid.UUID, token_hash: str, csrf_hash: str, expires: datetime, old_hash: str | None
) -> None:
    async with engine.begin() as connection:
        if old_hash:
            await connection.execute(
                text("DELETE FROM auth_sessions WHERE token_hash=:hash"), {"hash": old_hash}
            )
        await connection.execute(
            text("""
          INSERT INTO auth_sessions(token_hash,user_id,csrf_hash,expires_at)
          VALUES(:hash,:user,:csrf,:expires)
        """),
            {"hash": token_hash, "user": user_id, "csrf": csrf_hash, "expires": expires},
        )


async def create_account(email: str, encoded: str) -> dict[str, Any] | None:
    async with engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    text("""
          INSERT INTO users(id,email,password_hash) VALUES(:id,:email,:hash)
          ON CONFLICT(email) DO NOTHING RETURNING id,email
        """),
                    {"id": uuid.uuid4(), "email": email, "hash": encoded},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


async def account(email: str) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("SELECT id,email,password_hash FROM users WHERE email=:email"),
                    {"email": email},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


async def rotate_csrf(session_hash: str, csrf_hash: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE auth_sessions SET csrf_hash=:hash WHERE token_hash=:session"),
            {"hash": csrf_hash, "session": session_hash},
        )


async def delete_session(session_hash: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM auth_sessions WHERE token_hash=:token"), {"token": session_hash}
        )


async def preferences(user_id: uuid.UUID) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
          SELECT max_walk_meters,reliability_weight FROM user_preferences WHERE user_id=:id
        """),
                    {"id": user_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


async def save_preferences(user_id: uuid.UUID, walk: int, weight: float) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("""
          INSERT INTO user_preferences(user_id,max_walk_meters,reliability_weight)
          VALUES(:id,:walk,:weight) ON CONFLICT(user_id) DO UPDATE SET
          max_walk_meters=:walk,reliability_weight=:weight,updated_at=now()
        """),
            {"id": user_id, "walk": walk, "weight": weight},
        )
