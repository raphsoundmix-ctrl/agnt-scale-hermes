"""
Agent context aggregator — builds a compact "what's been happening
across all agents" snapshot for the Orchestrator's planning calls.

Cost model:
  - One DB scan per call (SELECT … LIMIT 100 + dedupe per skill in Python)
  - One Redis HSET/HGET roundtrip
  - Result cached 300s under `agent_ctx:{tenant_id}:{cabinet_id|"_"}`
  - Formatted text is ~300-500 tokens vs ~5000 tokens if we sent raw
    result JSON for every recent run

The result string is dropped directly into the Orchestrator's user
message — no further prompting needed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from skills.registry import SKILLS

from services.db import tenant_session
from services.redis_client import get_redis


logger = logging.getLogger("hermes.agent_context")

CACHE_TTL_S = 300  # 5 minutes — long enough to dedupe within a session,
                   # short enough that newly-completed runs land in the
                   # next plan request.
MAX_ROWS_PER_TENANT = 100  # we dedupe in Python to "last run per skill"


def _cache_key(tenant_id: str, cabinet_id: Optional[str]) -> str:
    return f"agent_ctx:{tenant_id}:{cabinet_id or '_'}"


def _humanize_age(iso_ts: str) -> str:
    """Convert ISO timestamp to compact relative time (RU)."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "только что"
        if secs < 3600:
            return f"{secs // 60} мин назад"
        if secs < 86400:
            return f"{secs // 3600} ч назад"
        return f"{secs // 86400} д назад"
    except (ValueError, AttributeError):
        return iso_ts[:10]


async def _fetch_recent_runs(
    tenant_id: str, cabinet_id: Optional[str]
) -> list[dict]:
    """Raw query — last MAX_ROWS_PER_TENANT runs across all skills."""
    if cabinet_id:
        sql = text(
            "SELECT skill_id, status, result, created_at, finished_at "
            "FROM agent_runs "
            "WHERE tenant_id = :t AND (cabinet_id = :c OR cabinet_id IS NULL) "
            "ORDER BY created_at DESC LIMIT :lim"
        )
        params = {"t": tenant_id, "c": cabinet_id, "lim": MAX_ROWS_PER_TENANT}
    else:
        sql = text(
            "SELECT skill_id, status, result, created_at, finished_at "
            "FROM agent_runs "
            "WHERE tenant_id = :t "
            "ORDER BY created_at DESC LIMIT :lim"
        )
        params = {"t": tenant_id, "lim": MAX_ROWS_PER_TENANT}

    async with tenant_session(tenant_id) as session:
        result = await session.execute(sql, params)
        rows = result.fetchall()
    return [
        {
            "skill_id": r[0],
            "status": r[1],
            "result": r[2],
            "created_at": r[3].isoformat() if r[3] else "",
            "finished_at": r[4].isoformat() if r[4] else "",
        }
        for r in rows
    ]


def _dedupe_latest_per_skill(runs: list[dict]) -> dict[str, dict]:
    """Pick the most-recent run per skill_id (runs come in DESC order)."""
    seen: dict[str, dict] = {}
    for run in runs:
        sid = run["skill_id"]
        if sid not in seen:
            seen[sid] = run
    return seen


def _extract_summary(result: object) -> str:
    """Pull the `summary` field out of a result JSON, capped at 100 chars."""
    if not result:
        return ""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return str(result)[:100]
    if isinstance(result, dict):
        s = result.get("summary") or result.get("detail") or ""
        return str(s)[:100]
    return ""


def _format(latest: dict[str, dict]) -> str:
    """Render dedupe map as a compact Russian table for the system prompt."""
    lines: list[str] = []
    for skill_id, cls in SKILLS.items():
        if skill_id == "orchestrator":
            continue  # self-reference noise
        run = latest.get(skill_id)
        if not run:
            lines.append(f"  {skill_id:18s}  никогда не запускался")
            continue
        age = _humanize_age(run["created_at"])
        status = run["status"]
        summary = _extract_summary(run["result"])
        line = f"  {skill_id:18s}  {age:14s}  {status:14s}  {summary}"
        lines.append(line.rstrip())
    return "\n".join(lines)


async def build_orchestrator_context(
    tenant_id: str, cabinet_id: Optional[str] = None
) -> str:
    """
    Returns a Russian text block describing every skill's most-recent state.
    Cached 5 min in Redis. Safe to call from Orchestrator on every request.
    """
    key = _cache_key(tenant_id, cabinet_id)
    redis = await get_redis()

    cached = await redis.get(key)
    if cached:
        try:
            return cached.decode() if isinstance(cached, bytes) else str(cached)
        except (UnicodeDecodeError, AttributeError):
            pass  # fall through to rebuild

    try:
        runs = await _fetch_recent_runs(tenant_id, cabinet_id)
    except Exception as exc:  # noqa: BLE001 — never let context fetch break planning
        logger.warning("agent_context_fetch_failed", extra={"err": str(exc)})
        return ""  # graceful degrade — orchestrator falls back to static knowledge

    latest = _dedupe_latest_per_skill(runs)
    block = _format(latest) if latest else "  (нет истории запусков агентов)"

    full = (
        "Последние действия агентов этого селлера "
        f"(всего {len(runs)} runs за всё время, показан последний по каждому):\n"
        f"{block}"
    )

    await redis.set(key, full, ex=CACHE_TTL_S)
    return full
