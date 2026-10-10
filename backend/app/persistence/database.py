from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()
SCHEMA_REVISION = "0006_demo_provenance"
pool_options: dict[str, Any] = (
    {"poolclass": NullPool}
    if settings.app_env == "test"
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": 2,
        "pool_timeout": 3,
        "pool_recycle": 900,
    }
)
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    **pool_options,
    connect_args={
        "timeout": 3,
        "command_timeout": 10,
        "server_settings": {"statement_timeout": "8000", "lock_timeout": "2000"},
    },
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
