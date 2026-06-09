"""AGNT SCALE — server-side agent memory client (path A).

Per-account (workspace) + per-agent isolation enforced by Postgres RLS.
Every query runs under role `mem_app` (non-superuser) inside a transaction with
SET LOCAL app.account_id / app.agent_id, so the RLS policies apply:
  - an agent reads/writes only its own rows within its account
  - the orchestrator (agent_id='orchestrator') reads ALL agents in its account
No MAO coupling — plain asyncpg against agnt-postgres.

Long-term rows (scope='long') get a local self-hosted embedding (services.embeddings,
bge-small-en-v1.5, 384-dim) so `search()` can recall by meaning, not just recency.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from services import embeddings as emb_svc

_pool: Optional[asyncpg.Pool] = None


def _dsn() -> str:
    # asyncpg wants a plain postgresql:// DSN (strip SQLAlchemy's +asyncpg).
    return os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=5)
    return _pool


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _expires_at_for_scope(scope: str) -> Optional[datetime]:
    """Per-scope expiry for new rows only. Unknown scope → NULL (never expires)."""
    if scope == "short":
        days = _env_int("MEM_EXPIRES_SHORT_DAYS", 7)
        if days <= 0:
            return None
        return datetime.now(timezone.utc) + timedelta(days=days)
    if scope == "long":
        days = _env_int("MEM_EXPIRES_LONG_DAYS", 0)
        if days <= 0:
            return None
        return datetime.now(timezone.utc) + timedelta(days=days)
    return None


async def _scope(con: asyncpg.Connection, account_id: str, agent_id: str) -> None:
    # transaction-scoped → auto-revert on tx end. set_config(...,true) == SET LOCAL.
    await con.execute("SET LOCAL ROLE mem_app")
    await con.execute("SELECT set_config('app.account_id', $1, true)", account_id)
    await con.execute("SELECT set_config('app.agent_id', $1, true)", agent_id)


async def remember(
    account_id: str,
    agent_id: str,
    content: str,
    *,
    kind: str = "fact",
    scope: str = "long",
    ad_account_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> int:
    # Embed only durable rows; short-term chat turns stay cheap.
    vec_str: Optional[str] = None
    if scope == "long":
        try:
            vec_str = emb_svc.to_pgvector(await emb_svc.aembed(content))
        except Exception:  # noqa: BLE001 — embedding is best-effort, never blocks a write
            vec_str = None

    expires_at = _expires_at_for_scope(scope)

    pool = await _get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            await _scope(con, account_id, agent_id)
            row = await con.fetchrow(
                """INSERT INTO agent_memory
                     (account_id, ad_account_id, agent_id, scope, kind, content,
                      meta, embedding, expires_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::vector,$9)
                   RETURNING id""",
                account_id, ad_account_id, agent_id, scope, kind, content,
                json.dumps(meta or {}), vec_str, expires_at,
            )
            return int(row["id"])


async def recall(
    account_id: str,
    agent_id: str,
    *,
    scope: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recency recall (ORDER BY created_at DESC)."""
    pool = await _get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            await _scope(con, account_id, agent_id)
            q = ("SELECT id, account_id, ad_account_id, agent_id, scope, kind, "
                 "content, meta, created_at FROM agent_memory WHERE TRUE")
            args: list[Any] = []
            if scope:
                args.append(scope); q += f" AND scope = ${len(args)}"
            if kind:
                args.append(kind); q += f" AND kind = ${len(args)}"
            args.append(limit); q += f" ORDER BY created_at DESC LIMIT ${len(args)}"
            rows = await con.fetch(q, *args)
            return [dict(r) for r in rows]


async def search(
    account_id: str,
    agent_id: str,
    query: str,
    *,
    scope: str = "long",
    kind: Optional[str] = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Semantic recall: cosine nearest-neighbour over embeddings, RLS-scoped.

    For agent_id='orchestrator' RLS exposes ALL agents' rows in the account, so
    the orchestrator searches the whole account's long-term memory; a normal agent
    only searches its own.
    """
    vec_str = emb_svc.to_pgvector(await emb_svc.aembed(query))
    if not vec_str:
        return []
    pool = await _get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            await _scope(con, account_id, agent_id)
            q = ("SELECT id, agent_id, kind, content, created_at, "
                 "1 - (embedding <=> $1::vector) AS score "
                 "FROM agent_memory WHERE scope = $2 AND embedding IS NOT NULL")
            args: list[Any] = [vec_str, scope]
            if kind:
                args.append(kind); q += f" AND kind = ${len(args)}"
            args.append(limit); q += f" ORDER BY embedding <=> $1::vector LIMIT ${len(args)}"
            rows = await con.fetch(q, *args)
            return [dict(r) for r in rows]


async def ping() -> dict[str, Any]:
    """Health probe: confirms pool + RLS round-trip works."""
    pool = await _get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            await _scope(con, "_healthcheck", "assistant")
            n = await con.fetchval("SELECT count(*) FROM agent_memory")
    return {"ok": True, "visible_rows_for_probe": int(n)}
