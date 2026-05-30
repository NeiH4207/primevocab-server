"""Database engine bootstrap.

We keep one async engine per database technology and expose simple
context-managed sessions / connections to the rest of the app.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import redis.asyncio as redis_async
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

settings = get_settings()


# ============================================================================
# Postgres
# ============================================================================

_pg_engine: Optional[AsyncEngine] = None
_pg_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def init_pg() -> AsyncEngine:
    global _pg_engine, _pg_sessionmaker
    if _pg_engine is None:
        _pg_engine = create_async_engine(
            settings.pg_dsn_async,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            future=True,
        )
        _pg_sessionmaker = async_sessionmaker(
            _pg_engine, expire_on_commit=False, autoflush=False
        )
        logger.info("Postgres engine initialised: {}", settings.pg_host)
    return _pg_engine


def pg_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _pg_sessionmaker is None:
        init_pg()
    assert _pg_sessionmaker is not None
    return _pg_sessionmaker


@asynccontextmanager
async def pg_session() -> AsyncIterator[AsyncSession]:
    """Open a transactional session, commit on success, rollback on error."""
    sm = pg_sessionmaker()
    session = sm()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ============================================================================
# Redis
# ============================================================================

_redis: Optional[redis_async.Redis] = None


def init_redis() -> redis_async.Redis:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
        )
        logger.info("Redis client initialised: {}", settings.redis_url)
    return _redis


def redis_client() -> redis_async.Redis:
    if _redis is None:
        init_redis()
    assert _redis is not None
    return _redis


# ============================================================================
# Lifespan helpers
# ============================================================================


async def ping_all() -> None:
    """Ping each backend with a short timeout to fail fast on bad config."""
    init_pg()
    init_redis()

    async def ping_pg() -> None:
        from sqlalchemy import text

        async with pg_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))

    async def ping_redis() -> None:
        await redis_client().ping()

    async def _retry(coro_factory, label: str, attempts: int = 5) -> None:
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await coro_factory()
                return
            except Exception as exc:
                last = exc
                if attempt < attempts:
                    await asyncio.sleep(min(2 * attempt, 8))
        assert last is not None
        raise last

    await _retry(ping_pg, "postgres")
    await _retry(ping_redis, "redis")
    logger.info("✅ All databases reachable")


async def shutdown_all() -> None:
    if _pg_engine is not None:
        await _pg_engine.dispose()
    if _redis is not None:
        await _redis.aclose()
    logger.info("Database connections closed")
