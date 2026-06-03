"""
Redis client — lazy singleton for Hermes.

Uses REDIS_URL env (Upstash rediss:// or local redis://). Connection is
created on first call and reused. Hermes processes a few hundred RPS
max, so a single shared async client is enough — no pool tuning needed.
"""
from __future__ import annotations

import os
from typing import Optional

import redis.asyncio as redis


_client: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """Return a shared async Redis client. Creates on first call."""
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://redis:6379")
        _client = redis.from_url(
            url,
            decode_responses=False,  # we handle bytes/str explicitly
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    return _client


async def close_redis() -> None:
    """Tear down the shared client on graceful shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001 — close errors don't matter on shutdown
            pass
        _client = None
