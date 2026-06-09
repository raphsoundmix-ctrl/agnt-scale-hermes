"""Agent memory maintenance (#12): TTL, exact/cosine dedup, per-agent cap.

Runs under RLS via mem_app per (account_id, agent_id) bucket. Bucket enumeration
uses SET ROLE mem_maint (SELECT-only, BYPASSRLS) when migration 006 is applied;
falls back to pool owner with a WARNING if the role is missing.

Default: DRY-RUN (log only). Destructive deletes require MEM_MAINT_DRY_RUN=0.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import asyncpg

from services import agnt_memory as mem

log = logging.getLogger("hermes.mem_maintenance")


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _skip_accounts() -> set[str]:
    raw = os.getenv("MEM_MAINT_SKIP_ACCOUNTS", "_global")
    return {a.strip() for a in raw.split(",") if a.strip()}


@dataclass
class BucketStats:
    account_id: str
    agent_id: str
    examined: int = 0
    expires_would_delete: int = 0
    ttl_would_delete: int = 0
    dedup_would_delete: int = 0
    cap_would_delete: int = 0
    deleted: int = 0


@dataclass
class MaintenanceReport:
    dry_run: bool
    ttl_days: int
    max_per_agent: int
    dedup_cosine: float
    skip_accounts: list[str]
    buckets: list[BucketStats] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "examined": sum(b.examined for b in self.buckets),
            "expires_would_delete": sum(b.expires_would_delete for b in self.buckets),
            "ttl_would_delete": sum(b.ttl_would_delete for b in self.buckets),
            "dedup_would_delete": sum(b.dedup_would_delete for b in self.buckets),
            "cap_would_delete": sum(b.cap_would_delete for b in self.buckets),
            "deleted": sum(b.deleted for b in self.buckets),
        }

    def to_dict(self) -> dict[str, Any]:
        t = self.totals
        return {
            "dry_run": self.dry_run,
            "config": {
                "MEM_TTL_DAYS": self.ttl_days,
                "MEM_MAX_PER_AGENT": self.max_per_agent,
                "MEM_DEDUP_COSINE": self.dedup_cosine,
                "MEM_MAINT_SKIP_ACCOUNTS": self.skip_accounts,
            },
            "totals": t,
            "buckets": [
                {
                    "account_id": b.account_id,
                    "agent_id": b.agent_id,
                    "examined": b.examined,
                    "expires_would_delete": b.expires_would_delete,
                    "ttl_would_delete": b.ttl_would_delete,
                    "dedup_would_delete": b.dedup_would_delete,
                    "cap_would_delete": b.cap_would_delete,
                    "deleted": b.deleted,
                }
                for b in self.buckets
            ],
        }


async def _scope(con: asyncpg.Connection, account_id: str, agent_id: str) -> None:
    await con.execute("SET LOCAL ROLE mem_app")
    await con.execute("SELECT set_config('app.account_id', $1, true)", account_id)
    await con.execute("SELECT set_config('app.agent_id', $1, true)", agent_id)


_BUCKET_SQL = "SELECT DISTINCT account_id, agent_id FROM agent_memory ORDER BY 1, 2"


async def _list_buckets(con: asyncpg.Connection) -> list[tuple[str, str]]:
    rows = await con.fetch(_BUCKET_SQL)
    return [(r["account_id"], r["agent_id"]) for r in rows]


async def _enumerate_buckets(pool: asyncpg.Pool) -> list[tuple[str, str]]:
    """List all buckets; prefer mem_maint (least privilege), fall back if missing."""
    async with pool.acquire() as con:
        role_set = False
        try:
            await con.execute("SET ROLE mem_maint")
            role_set = True
            return await _list_buckets(con)
        except Exception as exc:  # noqa: BLE001 — role may not exist pre-migration
            log.warning(
                "mem_maint role unavailable (%s); listing buckets as pool owner",
                exc,
            )
            return await _list_buckets(con)
        finally:
            if role_set:
                try:
                    await con.execute("RESET ROLE")
                except Exception:  # noqa: BLE001
                    pass


def _ttl_scope_clause(scope_mode: str) -> tuple[str, list[Any]]:
    if scope_mode == "all":
        return "", []
    return " AND scope = $2", [scope_mode]


async def _expires_at_candidates(con: asyncpg.Connection) -> list[int]:
    rows = await con.fetch(
        "SELECT id FROM agent_memory WHERE expires_at IS NOT NULL AND expires_at < now()"
    )
    return [int(r["id"]) for r in rows]


async def _ttl_candidates(
    con: asyncpg.Connection,
    account_id: str,
    agent_id: str,
    ttl_days: int,
    scope_mode: str,
) -> list[int]:
    extra, extra_args = _ttl_scope_clause(scope_mode)
    q = (
        f"SELECT id FROM agent_memory WHERE created_at < now() - ($1 || ' days')::interval{extra}"
    )
    args: list[Any] = [str(ttl_days), *extra_args]
    rows = await con.fetch(q, *args)
    return [int(r["id"]) for r in rows]


async def _exact_dedup_candidates(con: asyncpg.Connection) -> list[int]:
    rows = await con.fetch(
        """
        WITH ranked AS (
          SELECT id,
                 ROW_NUMBER() OVER (
                   PARTITION BY md5(content) ORDER BY created_at DESC, id DESC
                 ) AS rn
          FROM agent_memory
        )
        SELECT id FROM ranked WHERE rn > 1
        """
    )
    return [int(r["id"]) for r in rows]


async def _cosine_dedup_candidates(
    con: asyncpg.Connection, threshold: float
) -> list[int]:
    rows = await con.fetch(
        """
        SELECT id, embedding, created_at
        FROM agent_memory
        WHERE scope = 'long' AND embedding IS NOT NULL
        ORDER BY created_at DESC, id DESC
        """
    )
    if len(rows) < 2:
        return []

    keepers: list[tuple[int, Any]] = []
    to_delete: list[int] = []

    for row in rows:
        rid = int(row["id"])
        emb = row["embedding"]
        duplicate = False
        for kid, kemb in keepers:
            sim = await con.fetchval(
                "SELECT 1 - ($1::vector <=> $2::vector)", emb, kemb
            )
            if sim is not None and float(sim) >= threshold:
                duplicate = True
                break
        if duplicate:
            to_delete.append(rid)
        else:
            keepers.append((rid, emb))
    return to_delete


async def _cap_candidates(con: asyncpg.Connection, max_per_agent: int) -> list[int]:
    rows = await con.fetch(
        """
        WITH ranked AS (
          SELECT id,
                 ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS rn_oldest,
                 COUNT(*) OVER () AS total
          FROM agent_memory
        )
        SELECT id FROM ranked WHERE total > $1 AND rn_oldest <= total - $1
        """,
        max_per_agent,
    )
    return [int(r["id"]) for r in rows]


async def _delete_ids(
    con: asyncpg.Connection, ids: list[int], dry_run: bool
) -> int:
    if not ids or dry_run:
        return 0
    result = await con.execute(
        "DELETE FROM agent_memory WHERE id = ANY($1::bigint[])", ids
    )
    # asyncpg returns 'DELETE N'
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return len(ids)


async def run_maintenance(
    *,
    dry_run: Optional[bool] = None,
    ttl_days: Optional[int] = None,
    max_per_agent: Optional[int] = None,
    dedup_cosine: Optional[float] = None,
    dedup_exact: Optional[bool] = None,
) -> MaintenanceReport:
    """Run all enabled maintenance policies. Default env: dry-run, all policies off."""
    if dry_run is None:
        dry_run = _env_bool("MEM_MAINT_DRY_RUN", "1")
    if ttl_days is None:
        ttl_days = _env_int("MEM_TTL_DAYS", 0)
    if max_per_agent is None:
        max_per_agent = _env_int("MEM_MAX_PER_AGENT", 0)
    if dedup_cosine is None:
        dedup_cosine = _env_float("MEM_DEDUP_COSINE", 0.0)
    if dedup_exact is None:
        dedup_exact = _env_bool("MEM_DEDUP_EXACT", "0")

    scope_mode = os.getenv("MEM_TTL_SCOPE", "short").lower()
    if scope_mode not in ("short", "long", "all"):
        scope_mode = "short"

    skip = _skip_accounts()
    report = MaintenanceReport(
        dry_run=dry_run,
        ttl_days=ttl_days,
        max_per_agent=max_per_agent,
        dedup_cosine=dedup_cosine,
        skip_accounts=sorted(skip),
    )

    pool = await mem._get_pool()  # noqa: SLF001 — shared pool with memory client
    buckets = await _enumerate_buckets(pool)

    for account_id, agent_id in buckets:
        if account_id in skip:
            continue

        stats = BucketStats(account_id=account_id, agent_id=agent_id)
        delete_ids: set[int] = set()

        async with pool.acquire() as con:
            async with con.transaction():
                await _scope(con, account_id, agent_id)
                stats.examined = int(
                    await con.fetchval("SELECT count(*) FROM agent_memory") or 0
                )

                dedup_ids: set[int] = set()

                exp_ids = await _expires_at_candidates(con)
                stats.expires_would_delete = len(exp_ids)
                delete_ids.update(exp_ids)

                if ttl_days > 0:
                    ttl_ids = await _ttl_candidates(
                        con, account_id, agent_id, ttl_days, scope_mode
                    )
                    stats.ttl_would_delete = len(ttl_ids)
                    delete_ids.update(ttl_ids)

                if dedup_exact:
                    dedup_ids.update(await _exact_dedup_candidates(con))

                if dedup_cosine > 0:
                    dedup_ids.update(
                        await _cosine_dedup_candidates(con, dedup_cosine)
                    )
                stats.dedup_would_delete = len(dedup_ids)
                delete_ids.update(dedup_ids)

                if max_per_agent > 0:
                    cap_ids = await _cap_candidates(con, max_per_agent)
                    stats.cap_would_delete = len(cap_ids)
                    delete_ids.update(cap_ids)

                ordered = sorted(delete_ids)
                stats.deleted = await _delete_ids(con, ordered, dry_run)

        report.buckets.append(stats)
        if (
            stats.expires_would_delete
            or stats.ttl_would_delete
            or stats.dedup_would_delete
            or stats.cap_would_delete
        ):
            log.info(
                "mem_maint bucket=%s/%s examined=%d expires=%d ttl=%d dedup=%d cap=%d "
                "would_delete=%d deleted=%d dry_run=%s",
                account_id,
                agent_id,
                stats.examined,
                stats.expires_would_delete,
                stats.ttl_would_delete,
                stats.dedup_would_delete,
                stats.cap_would_delete,
                len(delete_ids),
                stats.deleted,
                dry_run,
            )

    t = report.totals
    log.info(
        "mem_maint done dry_run=%s examined=%d expires=%d ttl=%d dedup=%d cap=%d deleted=%d",
        dry_run,
        t["examined"],
        t["expires_would_delete"],
        t["ttl_would_delete"],
        t["dedup_would_delete"],
        t["cap_would_delete"],
        t["deleted"],
    )
    return report
