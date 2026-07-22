"""Shared PostgreSQL pool for the Telegram bot process."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from config import DB_CONFIG, DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE


_pool: asyncpg.Pool | None = None


async def init_db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            **DB_CONFIG,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            command_timeout=15,
        )
    return _pool


async def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def db_connection() -> AsyncIterator[asyncpg.Connection]:
    """Yields a pooled connection, with a direct fallback for isolated scripts/tests."""
    if _pool is not None:
        async with _pool.acquire() as connection:
            yield connection
        return

    connection = await asyncpg.connect(**DB_CONFIG, command_timeout=15)
    try:
        yield connection
    finally:
        await connection.close()
