"""Opaque sessions and bounded Argon2id work; no signing-secret defaults."""

from __future__ import annotations

import asyncio
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from starlette.concurrency import run_in_threadpool

hasher = PasswordHasher(time_cost=3, memory_cost=19456, parallelism=1)
password_slots = asyncio.Semaphore(2)
dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def hash_password(password: str) -> str:
    async with password_slots:
        return await run_in_threadpool(hasher.hash, password)


async def verify_password(encoded: str | None, password: str) -> bool:
    async with password_slots:
        try:
            await run_in_threadpool(hasher.verify, encoded or dummy_hash, password)
            return encoded is not None
        except VerificationError:
            return False
