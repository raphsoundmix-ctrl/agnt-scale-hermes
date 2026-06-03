"""
Cross-agent event bus — Phase 3B (Postgres LISTEN/NOTIFY).

Two public surfaces:

  emit_event(...)       — publisher. Called from inside a skill after a
                          meaningful state change (e.g. Reviews detected
                          a legal threat; Inventory found a critical SKU).
                          Inserts a row in `agent_events`. The DB trigger
                          fires NOTIFY; the listener daemon picks it up.

  start_listener()      — starts the LISTEN/NOTIFY daemon. Runs for the
                          whole process lifetime. Dispatches events to
                          per-target handlers registered in handlers.py.

Anti-loop:
  - hop_depth incremented when a handler emits a chained event.
    handlers refuse to process events with hop_depth >= 1 (so the chain
    is exactly: source → 1 handler → optional emit → STOP).
  - dedup_key + dedup_window_seconds — when set, swallows duplicate
    emits within the window. Use this for noisy triggers like
    "stock low" that may fire on every inventory run.

Failure semantics:
  - Handler raises → status='failed', error recorded, NO retry. We log
    loudly so a human can re-emit if needed. MVP-grade.
  - Listener disconnects → reconnect with exponential backoff. While
    down, events still queue in DB; on reconnect, dispatcher does one
    catch-up SELECT of pending events before resuming LISTEN.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import asyncpg
from sqlalchemy import text

from config import settings
from services.db import tenant_session


logger = logging.getLogger("hermes.events")


# ─── Types ───────────────────────────────────────────────────────────


EventHandler = Callable[["EventRow"], Awaitable[None]]


class EventRow:
    """Lightweight wrapper for a fetched agent_events row."""

    __slots__ = (
        "id",
        "tenant_id",
        "cabinet_id",
        "source_skill",
        "target_skill",
        "event_type",
        "payload",
        "hop_depth",
        "parent_id",
    )

    def __init__(
        self,
        *,
        id: str,
        tenant_id: str,
        cabinet_id: Optional[str],
        source_skill: str,
        target_skill: str,
        event_type: str,
        payload: dict[str, Any],
        hop_depth: int,
        parent_id: Optional[str],
    ) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.cabinet_id = cabinet_id
        self.source_skill = source_skill
        self.target_skill = target_skill
        self.event_type = event_type
        self.payload = payload
        self.hop_depth = hop_depth
        self.parent_id = parent_id


# ─── Publisher ───────────────────────────────────────────────────────


async def emit_event(
    *,
    tenant_id: str,
    source_skill: str,
    target_skill: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    cabinet_id: Optional[str] = None,
    dedup_key: Optional[str] = None,
    dedup_window_seconds: int = 3600,
    parent_id: Optional[str] = None,
    hop_depth: int = 0,
) -> Optional[str]:
    """
    Insert an event row → trigger fires NOTIFY → listener dispatches.

    Returns the new event ID, or None if dedup swallowed it.

    Dedup: when `dedup_key` is set, this checks for an event with the
    same key inside the last `dedup_window_seconds`. If found, the
    insert is skipped and we return None. Use for any high-frequency
    trigger (e.g. "SKU X is critical" fires every inventory run).

    Hop-depth guard: refuses to insert when hop_depth >= 1. This caps
    cascade depth at exactly one hop. The caller (a handler emitting a
    chained event) is responsible for incrementing.
    """
    if hop_depth >= 1:
        logger.warning(
            "event_chain_capped",
            extra={
                "tenant_id": tenant_id,
                "source_skill": source_skill,
                "event_type": event_type,
                "hop_depth": hop_depth,
            },
        )
        return None

    payload = payload or {}
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    async with tenant_session(tenant_id) as s:
        if dedup_key:
            since = datetime.now(timezone.utc) - timedelta(
                seconds=dedup_window_seconds
            )
            existing = (
                await s.execute(
                    text(
                        "SELECT id FROM agent_events "
                        "WHERE tenant_id = :t "
                        "  AND dedup_key = :k "
                        "  AND created_at >= :since "
                        "LIMIT 1"
                    ),
                    {"t": tenant_id, "k": dedup_key, "since": since},
                )
            ).first()
            if existing:
                logger.info(
                    "event_deduped",
                    extra={
                        "tenant_id": tenant_id,
                        "dedup_key": dedup_key,
                        "skipped_event_type": event_type,
                    },
                )
                return None

        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO agent_events (
                        tenant_id, cabinet_id, source_skill, target_skill,
                        event_type, payload, dedup_key, parent_id, hop_depth
                    ) VALUES (
                        :t, :c, :src, :tgt, :type,
                        CAST(:payload AS jsonb), :dedup, :parent, :hop
                    )
                    RETURNING id
                    """
                ),
                {
                    "t": tenant_id,
                    "c": cabinet_id,
                    "src": source_skill,
                    "tgt": target_skill,
                    "type": event_type,
                    "payload": payload_json,
                    "dedup": dedup_key,
                    "parent": parent_id,
                    "hop": hop_depth,
                },
            )
        ).first()
        await s.commit()
        new_id = str(row[0]) if row else None
        logger.info(
            "event_emitted",
            extra={
                "tenant_id": tenant_id,
                "event_id": new_id,
                "source_skill": source_skill,
                "target_skill": target_skill,
                "event_type": event_type,
                "hop_depth": hop_depth,
            },
        )
        return new_id


# ─── Listener daemon ─────────────────────────────────────────────────


# Populated by handlers.register(). Maps target_skill → coroutine.
_HANDLERS: dict[str, EventHandler] = {}


def register_handler(target_skill: str, handler: EventHandler) -> None:
    """Register an event handler for a target skill."""
    _HANDLERS[target_skill] = handler
    logger.info(f"event_handler_registered target={target_skill}")


async def _fetch_event(tenant_id: str, event_id: str) -> Optional[EventRow]:
    async with tenant_session(tenant_id) as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT
                        id, tenant_id, cabinet_id, source_skill, target_skill,
                        event_type, payload, hop_depth, parent_id, status
                    FROM agent_events
                    WHERE id = :id
                    """
                ),
                {"id": event_id},
            )
        ).first()
        if not row:
            return None
        # row[9] = status; skip if already processed
        if row[9] != "pending":
            return None
        return EventRow(
            id=str(row[0]),
            tenant_id=str(row[1]),
            cabinet_id=str(row[2]) if row[2] else None,
            source_skill=row[3],
            target_skill=row[4],
            event_type=row[5],
            payload=row[6] if isinstance(row[6], dict) else {},
            hop_depth=row[7] or 0,
            parent_id=str(row[8]) if row[8] else None,
        )


async def _mark_status(
    tenant_id: str,
    event_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    result: Optional[dict[str, Any]] = None,
) -> None:
    async with tenant_session(tenant_id) as s:
        await s.execute(
            text(
                """
                UPDATE agent_events
                SET status = :status,
                    error = :error,
                    result = CAST(:result AS jsonb),
                    processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": event_id,
                "status": status,
                "error": error,
                "result": json.dumps(result, ensure_ascii=False, default=str)
                if result is not None
                else None,
            },
        )
        await s.commit()


async def _dispatch(event: EventRow) -> None:
    """Look up handler for target_skill, run it, record outcome."""
    handler = _HANDLERS.get(event.target_skill)
    if not handler:
        logger.warning(
            "event_no_handler",
            extra={
                "event_id": event.id,
                "target_skill": event.target_skill,
                "event_type": event.event_type,
            },
        )
        await _mark_status(
            event.tenant_id,
            event.id,
            "skipped",
            error=f"no handler for target_skill={event.target_skill}",
        )
        return

    # Mark processing so concurrent listeners don't double-handle.
    await _mark_status(event.tenant_id, event.id, "processing")

    try:
        await handler(event)
        await _mark_status(event.tenant_id, event.id, "done")
        logger.info(
            "event_handled",
            extra={
                "event_id": event.id,
                "target_skill": event.target_skill,
                "event_type": event.event_type,
            },
        )
    except Exception as exc:  # noqa: BLE001 — top-of-loop boundary
        logger.exception(
            "event_handler_failed",
            extra={
                "event_id": event.id,
                "target_skill": event.target_skill,
                "event_type": event.event_type,
            },
        )
        await _mark_status(
            event.tenant_id,
            event.id,
            "failed",
            error=str(exc)[:1000],
        )


async def _catchup_pending() -> None:
    """
    On startup / reconnect, sweep events that were inserted while no
    listener was attached. Limit to last 1 hour and 200 events — anything
    older is unlikely to still be actionable and is left as audit.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    # We need to read across all tenants for catch-up. Use admin RLS
    # bypass (literal 'admin' is the only value the policy lets through).
    async with tenant_session(None, admin=True) as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, tenant_id
                    FROM agent_events
                    WHERE status = 'pending'
                      AND created_at >= :cutoff
                    ORDER BY created_at ASC
                    LIMIT 200
                    """
                ),
                {"cutoff": cutoff},
            )
        ).all()
    if not rows:
        return
    logger.info(f"event_catchup count={len(rows)}")
    for row in rows:
        event = await _fetch_event(str(row[1]), str(row[0]))
        if event:
            await _dispatch(event)


async def _parse_notify(payload_str: str) -> Optional[tuple[str, str]]:
    """Returns (tenant_id, event_id) from a NOTIFY envelope, or None."""
    try:
        env = json.loads(payload_str)
        return env.get("tenant_id"), env.get("id")
    except json.JSONDecodeError:
        logger.warning(f"event_notify_bad_envelope payload={payload_str[:200]}")
        return None


async def _run_listener_once() -> None:
    """
    Open a raw asyncpg connection (NOT through SQLAlchemy — LISTEN needs
    a long-lived dedicated connection) and process notifications until
    the connection dies. The outer loop in start_listener() reconnects.

    DATABASE_URL in settings uses postgresql+asyncpg://...  but asyncpg
    itself wants plain postgresql://...  — strip the driver suffix.
    """
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await _catchup_pending()

        # asyncpg's add_listener callback is sync-only. Queue events to a
        # task that processes them sequentially per-tenant to keep
        # ordering predictable. For MVP a single shared queue is fine.
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        def _on_notify(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
            # Sync callback — schedule the parse + enqueue on the loop.
            asyncio.create_task(_handle_notify_payload(payload, queue))

        await conn.add_listener("agent_events", _on_notify)
        logger.info("event_listener_attached channel=agent_events")

        # Worker: drain the queue forever (or until cancellation).
        while True:
            tenant_id, event_id = await queue.get()
            event = await _fetch_event(tenant_id, event_id)
            if event:
                await _dispatch(event)
    finally:
        await conn.close()


async def _handle_notify_payload(
    payload: str, queue: asyncio.Queue[tuple[str, str]]
) -> None:
    parsed = await _parse_notify(payload)
    if not parsed:
        return
    tenant_id, event_id = parsed
    if tenant_id and event_id:
        await queue.put((tenant_id, event_id))


async def start_listener() -> None:
    """
    Top-level entry. Run forever with exponential reconnect backoff.
    Called from app lifespan in main.py.
    """
    backoff = 1
    while True:
        try:
            await _run_listener_once()
        except asyncio.CancelledError:
            logger.info("event_listener_cancelled")
            raise
        except Exception:
            logger.exception("event_listener_crashed reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        backoff = 1
