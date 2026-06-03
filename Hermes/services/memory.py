"""
Memory service — read/write `tenant_memory` and `agent_states` with
optional cabinet scoping.

Scoping rules:
  - cabinet_id=None → tenant-wide row (shared across all cabinets)
  - cabinet_id=<uuid> → per-cabinet row

UPSERT semantics: writing the same (tenant_id, key, cabinet_id) twice
updates `value` and `updated_at`. Uniqueness is enforced by partial
unique indexes — see Backend/alembic/versions/0002_*.

Hermes does not own the schema; we use raw SQL via SQLAlchemy text()
to avoid duplicating ORM definitions across services.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.db import AsyncSessionLocal, tenant_session

logger = logging.getLogger("hermes.memory")


# ───── tenant_memory ─────────────────────────────────────────────


async def get_memory(
    tenant_id: str,
    key: str,
    cabinet_id: Optional[str] = None,
) -> Optional[Any]:
    """Read one memory value. Returns the JSONB `value` or None."""
    async with tenant_session(tenant_id) as session:
        if cabinet_id is None:
            result = await session.execute(
                text(
                    "SELECT value FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id IS NULL AND key = :k"
                ),
                {"t": tenant_id, "k": key},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT value FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id = :c AND key = :k"
                ),
                {"t": tenant_id, "c": cabinet_id, "k": key},
            )
        row = result.first()
        return row[0] if row else None


async def set_memory(
    tenant_id: str,
    key: str,
    value: Any,
    cabinet_id: Optional[str] = None,
) -> None:
    """Upsert a memory value. Creates or updates atomically.

    Uses PostgreSQL `INSERT ... ON CONFLICT ... DO UPDATE` against the
    partial unique indexes created in alembic migration 0002. This is
    race-free under concurrent writers — the old UPDATE-then-INSERT
    pattern could lose writes or violate the unique index when two
    workers raced on the same (tenant, [cabinet], key).
    """
    async with tenant_session(tenant_id) as session:
        if cabinet_id is None:
            # Targets uq_tenant_memory_global (WHERE cabinet_id IS NULL)
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_memory
                        (id, tenant_id, cabinet_id, key, value, updated_at)
                    VALUES
                        (gen_random_uuid(), :t, NULL, :k, CAST(:v AS jsonb), now())
                    ON CONFLICT (tenant_id, key) WHERE cabinet_id IS NULL
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """
                ),
                {"t": tenant_id, "k": key, "v": _to_jsonb(value)},
            )
        else:
            # Targets uq_tenant_memory_per_cabinet (WHERE cabinet_id IS NOT NULL)
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_memory
                        (id, tenant_id, cabinet_id, key, value, updated_at)
                    VALUES
                        (gen_random_uuid(), :t, :c, :k, CAST(:v AS jsonb), now())
                    ON CONFLICT (tenant_id, cabinet_id, key)
                        WHERE cabinet_id IS NOT NULL
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """
                ),
                {"t": tenant_id, "c": cabinet_id, "k": key, "v": _to_jsonb(value)},
            )
        await session.commit()
        logger.debug(
            f"[{tenant_id}] memory.set key={key} cabinet={cabinet_id or 'tenant-wide'}"
        )


async def list_memory(
    tenant_id: str,
    cabinet_id: Optional[str] = None,
    include_tenant_wide: bool = True,
) -> dict[str, Any]:
    """
    Return all memory entries for tenant (+ optional cabinet) as a dict.
    If cabinet_id provided and include_tenant_wide=True, cabinet-scoped
    values override tenant-wide ones for the same key.
    """
    out: dict[str, Any] = {}
    async with tenant_session(tenant_id) as session:
        if include_tenant_wide:
            result = await session.execute(
                text(
                    "SELECT key, value FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id IS NULL"
                ),
                {"t": tenant_id},
            )
            for row in result.all():
                out[row[0]] = row[1]

        if cabinet_id:
            result = await session.execute(
                text(
                    "SELECT key, value FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id = :c"
                ),
                {"t": tenant_id, "c": cabinet_id},
            )
            for row in result.all():
                # cabinet-scoped overrides tenant-wide
                out[row[0]] = row[1]
    return out


async def delete_memory(
    tenant_id: str,
    key: str,
    cabinet_id: Optional[str] = None,
) -> bool:
    """Delete one key. Returns True if row was removed."""
    async with tenant_session(tenant_id) as session:
        if cabinet_id is None:
            result = await session.execute(
                text(
                    "DELETE FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id IS NULL AND key = :k"
                ),
                {"t": tenant_id, "k": key},
            )
        else:
            result = await session.execute(
                text(
                    "DELETE FROM tenant_memory "
                    "WHERE tenant_id = :t AND cabinet_id = :c AND key = :k"
                ),
                {"t": tenant_id, "c": cabinet_id, "k": key},
            )
        await session.commit()
        return result.rowcount > 0


# ───── agent_states ──────────────────────────────────────────────


async def get_state(
    tenant_id: str,
    skill_id: str,
    cabinet_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return the persisted state dict for a skill. Empty dict if absent."""
    async with tenant_session(tenant_id) as session:
        if cabinet_id is None:
            result = await session.execute(
                text(
                    "SELECT state FROM agent_states "
                    "WHERE tenant_id = :t AND skill_id = :s AND cabinet_id IS NULL"
                ),
                {"t": tenant_id, "s": skill_id},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT state FROM agent_states "
                    "WHERE tenant_id = :t AND skill_id = :s AND cabinet_id = :c"
                ),
                {"t": tenant_id, "s": skill_id, "c": cabinet_id},
            )
        row = result.first()
        return row[0] if row else {}


async def set_state(
    tenant_id: str,
    skill_id: str,
    state: dict[str, Any],
    cabinet_id: Optional[str] = None,
) -> None:
    """Upsert the LangGraph state for a skill (race-free).

    See `set_memory` for the rationale — same UPSERT pattern against the
    partial unique indexes `uq_agent_states_global` /
    `uq_agent_states_per_cabinet` from alembic migration 0002.
    """
    async with tenant_session(tenant_id) as session:
        if cabinet_id is None:
            await session.execute(
                text(
                    """
                    INSERT INTO agent_states
                        (id, tenant_id, cabinet_id, skill_id, state, updated_at)
                    VALUES
                        (gen_random_uuid(), :t, NULL, :s, CAST(:st AS jsonb), now())
                    ON CONFLICT (tenant_id, skill_id) WHERE cabinet_id IS NULL
                    DO UPDATE SET
                        state = EXCLUDED.state,
                        updated_at = now()
                    """
                ),
                {"t": tenant_id, "s": skill_id, "st": _to_jsonb(state)},
            )
        else:
            await session.execute(
                text(
                    """
                    INSERT INTO agent_states
                        (id, tenant_id, cabinet_id, skill_id, state, updated_at)
                    VALUES
                        (gen_random_uuid(), :t, :c, :s, CAST(:st AS jsonb), now())
                    ON CONFLICT (tenant_id, skill_id, cabinet_id)
                        WHERE cabinet_id IS NOT NULL
                    DO UPDATE SET
                        state = EXCLUDED.state,
                        updated_at = now()
                    """
                ),
                {"t": tenant_id, "s": skill_id, "c": cabinet_id, "st": _to_jsonb(state)},
            )
        await session.commit()


# ───── idempotency (processed_external_ids) ─────────────────────


async def mark_processed(
    tenant_id: str,
    external_id: str,
    skill_id: str,
) -> bool:
    """
    Idempotency guard. Returns True if this external_id is NEW (we should
    process it). Returns False if already processed before.
    """
    async with tenant_session(tenant_id) as session:
        try:
            await session.execute(
                text(
                    "INSERT INTO processed_external_ids "
                    "(tenant_id, external_id, skill_id) "
                    "VALUES (:t, :e, :s)"
                ),
                {"t": tenant_id, "e": external_id, "s": skill_id},
            )
            await session.commit()
            return True
        except IntegrityError:
            # Duplicate key — the expected "already processed" case.
            await session.rollback()
            return False
        except Exception:
            await session.rollback()
            logger.exception(
                "mark_processed failed for tenant=%s external_id=%s skill=%s",
                tenant_id, external_id, skill_id,
            )
            raise  # propagate so caller knows DB is down vs. duplicate


# ───── helpers ──────────────────────────────────────────────────


def _to_jsonb(value: Any) -> str:
    """Serialize Python value to a JSON string for JSONB column."""
    import json
    return json.dumps(value, ensure_ascii=False, default=str)
