"""
Visual Worker — background pipeline for the Design Studio content factory.

Why it exists:
  - The `produce` action (Design Director) fans a single brief out into N
    `visual_generations` rows with status='queued' and returns instantly.
    Something has to actually generate them. That's this worker.
  - The old `generate_video` path could return an `external_job_id` with
    status='queued' and NOTHING ever polled it → videos hung forever.
    This worker is also the poller.

What it does (single loop, started in main.py lifespan):
  every POLL_INTERVAL seconds:
    1. atomically claim one queued row (UPDATE queued→running RETURNING)
    2. dispatch by kind:
         image  → build prompt (LLM, if needs_prompt) → generate → mirror
         video  → build prompt (template) → generate (sync) → mirror,
                  or store external_job_id + leave for a future poll tick
    3. write succeeded/failed back to the row

Design choices:
  - Runs as the raw AsyncSessionLocal connection (Supabase `postgres`
    role, BYPASSRLS) — same as the existing visual skill. The worker is
    server-internal, processes all tenants, and the tenant_id on each row
    scopes every write. No SET LOCAL needed.
  - One row per tick keeps it simple + observable and naturally rate-limits
    the OpenRouter spend. Bump CLAIM_BATCH later if throughput matters.
  - Reuses VisualSkill's loaders + prompt_builder + openrouter_media so the
    prompt/quality logic stays in one place.

Failure-safe: any exception on a row marks it failed with the error text;
the loop continues. A crash of the whole loop reconnects with backoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from sqlalchemy import text

from config import settings
from services import fal_video, openrouter_media, prompt_builder, storage
from services.db import AsyncSessionLocal

logger = logging.getLogger("hermes.visual_worker")

POLL_INTERVAL = 5.0          # seconds between ticks when idle


# ─── claim ───────────────────────────────────────────────────────────


async def _claim_one() -> Optional[dict[str, Any]]:
    """
    Atomically grab the oldest actionable row and flip it to 'running'.

    Actionable =
      - status='queued'  (fresh image/video to generate), OR
      - status='running' AND kind='video' AND external_job_id IS NOT NULL
        AND created < MAX_VIDEO_POLL_AGE  (async video still cooking)

    The UPDATE ... RETURNING is atomic so two worker instances never grab
    the same row (defensive — we run one instance today).
    """
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    UPDATE visual_generations
                    SET status = 'running'
                    WHERE id = (
                        SELECT id FROM visual_generations
                        WHERE status = 'queued'
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, tenant_id, cabinet_id, brief_id, avatar_id,
                              moodboard_id, kind, slide_type, slide_index,
                              model_id, prompt, params, external_job_id
                    """
                )
            )
        ).first()
        await db.commit()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "cabinet_id": str(row[2]) if row[2] else None,
            "brief_id": str(row[3]) if row[3] else None,
            "avatar_id": str(row[4]) if row[4] else None,
            "moodboard_id": str(row[5]) if row[5] else None,
            "kind": row[6],
            "slide_type": row[7],
            "slide_index": row[8] or 1,
            "model_id": row[9],
            "prompt": row[10],
            "params": row[11] if isinstance(row[11], dict) else {},
            "external_job_id": row[12],
        }


# ─── status writers ──────────────────────────────────────────────────


async def _mark_succeeded(
    gen_id: str, *, storage_url: str, storage_path: str, cost: Any, model: str
) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE visual_generations SET status='succeeded', "
                "storage_url=:u, storage_path=:p, cost_usd=:cost, model_id=:m, "
                "finished_at=now() WHERE id=:id"
            ),
            {"id": gen_id, "u": storage_url, "p": storage_path, "cost": cost, "m": model},
        )
        await db.commit()


async def _mark_failed(gen_id: str, error: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE visual_generations SET status='failed', error=:e, "
                "finished_at=now() WHERE id=:id"
            ),
            {"id": gen_id, "e": error[:1000]},
        )
        await db.commit()


async def _requeue(gen_id: str, params: dict) -> None:
    """Put a claimed row back to 'queued' (e.g. video waiting for its
    start frame to finish rendering). Persists updated params."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE visual_generations SET status='queued', "
                "params=CAST(:p AS jsonb) WHERE id=:id"
            ),
            {"id": gen_id, "p": json.dumps(params, ensure_ascii=False, default=str)},
        )
        await db.commit()


async def _store_prompt(gen_id: str, prompt: str, negative: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE visual_generations SET prompt=:p, negative_prompt=:n "
                "WHERE id=:id"
            ),
            {"id": gen_id, "p": prompt, "n": negative},
        )
        await db.commit()


# ─── loaders (reuse VisualSkill's, instantiated server-side) ─────────


def _skill_for(tenant_id: str, cabinet_id: Optional[str]):
    # Imported lazily to avoid a circular import (visual imports nothing
    # from this module, but keep it lazy for safety).
    from skills.visual import VisualSkill

    return VisualSkill(tenant_id=tenant_id, cabinet_id=cabinet_id)


# ─── dispatch ────────────────────────────────────────────────────────


async def _process_image(row: dict[str, Any]) -> None:
    gen_id = row["id"]
    params = row["params"]
    needs_prompt = bool(params.get("needs_prompt"))

    prompt = row["prompt"]
    negative = ""

    if needs_prompt:
        # Build the real prompt now (LLM). Load brief/avatar/moodboard via
        # the skill's loaders to keep prompt logic in one place.
        skill = _skill_for(row["tenant_id"], row["cabinet_id"])
        async with AsyncSessionLocal() as db:
            brief = await skill._load_brief(db, row["brief_id"]) if row["brief_id"] else None
            avatar = await skill._load_avatar(db, row["avatar_id"]) if row["avatar_id"] else None
            moodboard = (
                await skill._load_moodboard(db, row["moodboard_id"])
                if row["moodboard_id"]
                else None
            )
        if not brief:
            await _mark_failed(gen_id, f"brief {row['brief_id']} not found")
            return

        pr = await prompt_builder.build_image_prompt(
            brief=brief["input"],
            slide_type=row["slide_type"] or "lifestyle",
            slide_index=row["slide_index"],
            avatar=avatar["input"] if avatar else None,
            moodboard=moodboard["input"] if moodboard else None,
            extra_intent=params.get("intent"),
        )
        prompt = pr.prompt
        negative = pr.negative_prompt
        await _store_prompt(gen_id, prompt, negative)

        # Reference images for character/product consistency.
        references: list[str] = []
        if avatar and avatar.get("preview_url"):
            references.append(avatar["preview_url"])
        for src in brief["source_image_urls"][:3]:
            references.append(src)
        params["_references"] = references

    media = await openrouter_media.generate_image(
        prompt,
        tenant_id=row["tenant_id"],
        cabinet_id=row["cabinet_id"] or "_",
        generation_id=gen_id,
        slide_index=row["slide_index"],
        reference_image_urls=params.get("_references") or None,
        aspect_ratio=params.get("aspect", "3:4"),
        model=row["model_id"] or settings.OPENROUTER_IMAGE_MODEL,
    )

    if media.success and media.storage_url:
        await _mark_succeeded(
            gen_id,
            storage_url=media.storage_url,
            storage_path=media.storage_path or "",
            cost=media.cost_usd,
            model=media.model,
        )
        logger.info("visual_slide_done id=%s type=%s", gen_id, row["slide_type"])
    else:
        await _mark_failed(gen_id, media.error or "unknown image error")


async def _to_fal_image(url: str) -> str:
    """Convert a MinIO asset URL → base64 data URI fal can fetch; pass
    external https URLs through unchanged."""
    key = storage.object_key_from_url(url)
    if not key:
        return url  # external, reachable URL
    import base64
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, lambda: storage.get_object_bytes(key))
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else "png"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
        ext, "image/png"
    )
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


async def _resolve_start_frame(row: dict[str, Any]) -> Optional[str]:
    """
    The Kling i2v start frame = identity anchor. Priority:
      1. an explicit reference_image_url in params (a chosen card)
      2. the best succeeded slide of the SAME production (prefer hero)
      3. the brief's first uploaded source image (an "element")
    Returns None if nothing usable yet (→ caller re-queues to wait for a
    slide to finish).
    """
    params = row["params"]
    if params.get("reference_image_url"):
        return params["reference_image_url"]

    pid = params.get("production_id")
    if pid:
        async with AsyncSessionLocal() as db:
            r = (
                await db.execute(
                    text(
                        "SELECT storage_url FROM visual_generations "
                        "WHERE params->>'production_id' = :pid AND kind='image' "
                        "AND status='succeeded' AND storage_url IS NOT NULL "
                        "ORDER BY (slide_type='hero') DESC, slide_index ASC LIMIT 1"
                    ),
                    {"pid": pid},
                )
            ).first()
            if r and r[0]:
                return r[0]

    if row["brief_id"]:
        skill = _skill_for(row["tenant_id"], row["cabinet_id"])
        async with AsyncSessionLocal() as db:
            brief = await skill._load_brief(db, row["brief_id"])
        if brief and brief["source_image_urls"]:
            return brief["source_image_urls"][0]
    return None


async def _process_video_submit(row: dict[str, Any]) -> bool:
    """
    Phase A for video: submit a Kling image-to-video job to fal.ai and
    leave the row in 'running' with the request id. Phase B (_poll_video)
    finishes it. Non-blocking — Kling takes 1-5 min.

    Returns True when the row advanced (submitted or failed), False when it
    was re-queued to wait for a start frame — so the tick loop sleeps
    instead of busy-spinning while slides render.
    """
    gen_id = row["id"]
    params = row["params"]

    if not fal_video.is_enabled():
        await _mark_failed(
            gen_id,
            "video disabled — set FAL_KEY in Hermes/.env to enable Kling video",
        )
        return True

    # Need a start frame for identity. If no slide is ready yet, re-queue
    # and let it retry on a later tick (slides generate first).
    start_url = await _resolve_start_frame(row)
    if not start_url:
        attempts = int(params.get("_video_wait", 0)) + 1
        if attempts > 60:  # ~60 × POLL_INTERVAL ≈ 5 min of waiting → give up
            await _mark_failed(gen_id, "no start frame became available for video")
            return True
        params["_video_wait"] = attempts
        await _requeue(gen_id, params)
        return False

    prompt = row["prompt"]
    if params.get("needs_prompt"):
        skill = _skill_for(row["tenant_id"], row["cabinet_id"])
        async with AsyncSessionLocal() as db:
            brief = await skill._load_brief(db, row["brief_id"]) if row["brief_id"] else None
            avatar = await skill._load_avatar(db, row["avatar_id"]) if row["avatar_id"] else None
        if not brief:
            await _mark_failed(gen_id, f"brief {row['brief_id']} not found")
            return
        prompt = _build_video_prompt(brief, avatar, params.get("intent"))
        params["needs_prompt"] = False
        await _store_prompt(gen_id, prompt, "")

    # fal/Kling fetches start_image_url from ITS servers. Our MinIO URL is
    # internal (localhost:9000) and unreachable externally — so for any
    # asset we issued, hand fal the bytes as a base64 data URI instead
    # (fal decodes data URIs). External https URLs pass through as-is.
    start_payload = await _to_fal_image(start_url)

    sub = await fal_video.submit_i2v(
        start_image_url=start_payload,
        prompt=prompt,
        duration=int(params.get("duration", 5)),
        elements=params.get("elements"),
    )
    if not sub.ok:
        await _mark_failed(gen_id, sub.error or "fal submit failed")
        return

    # Stay 'running'; record the job so the poll phase can finish it.
    params["fal_request_id"] = sub.request_id
    params["fal_status_url"] = sub.status_url
    params["fal_response_url"] = sub.response_url
    params["start_image_url"] = start_url
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE visual_generations SET external_job_id=:j, "
                "params=CAST(:p AS jsonb), status='running' WHERE id=:id"
            ),
            {"id": gen_id, "j": sub.request_id, "p": json.dumps(params, ensure_ascii=False, default=str)},
        )
        await db.commit()
    logger.info("visual_video_submitted id=%s request_id=%s", gen_id, sub.request_id)


async def _poll_video(row: dict[str, Any]) -> None:
    """Phase B: poll a running Kling job; on COMPLETED mirror to MinIO."""
    gen_id = row["id"]
    params = row["params"]
    status_url = params.get("fal_status_url")
    response_url = params.get("fal_response_url")
    if not status_url or not response_url:
        await _mark_failed(gen_id, "video running but missing fal urls")
        return

    res = await fal_video.poll(status_url=status_url, response_url=response_url)
    if res.status == "processing":
        return  # leave running, re-poll next tick
    if res.status == "failed":
        await _mark_failed(gen_id, res.error or "fal video failed")
        return

    # done — mirror the https video url to MinIO
    dest = f"{row['tenant_id']}/{row['cabinet_id'] or '_'}/generations/{gen_id}/video.mp4"
    try:
        mirrored = await storage.upload_from_url(
            res.video_url, dest, content_type="video/mp4"
        )
    except Exception as e:
        await _mark_failed(gen_id, f"video mirror failed: {e}")
        return
    await _mark_succeeded(
        gen_id, storage_url=mirrored, storage_path=dest, cost=None,
        model=settings.FAL_KLING_MODEL,
    )
    logger.info("visual_video_done id=%s", gen_id)


def _build_video_prompt(brief: dict, avatar: Optional[dict], intent: Optional[str]) -> str:
    binput = brief["input"]
    parts = [
        "GOAL: Виральное вертикальное видео 3:4 для карточки Wildberries. "
        "Показ товара в реальной жизни. Цепляющие первые 2 секунды.",
        f"PRODUCT: {binput.product_name}",
    ]
    if binput.product_description:
        parts.append(f"DESCRIPTION: {binput.product_description[:300]}")
    parts.append(f"GEOMETRY LOCK:\n{binput.geometry_lock}")
    if avatar:
        ai = avatar["input"]
        parts.append(f"IDENTITY LOCK ({ai.name}):\n{ai.identity_lock}")
    if intent:
        parts.append(f"INTENT: {intent}")
    parts.append(
        "MOTION: smooth handheld, subtle parallax, no abrupt cuts. "
        "End frame holds product front-and-center 0.5s."
    )
    parts.append("EXCLUDE: text overlays, jump cuts, blurry frames, distorted geometry")
    parts.append("FORMAT: 3:4 aspect, H.264, max 10s, up to 50 MB")
    return "\n\n".join(parts)


# ─── loop ────────────────────────────────────────────────────────────


async def _next_running_video() -> Optional[dict[str, Any]]:
    """A video job already submitted to fal and awaiting completion."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT id, tenant_id, cabinet_id, params, external_job_id "
                    "FROM visual_generations "
                    "WHERE kind='video' AND status='running' "
                    "AND external_job_id IS NOT NULL "
                    "ORDER BY created_at ASC LIMIT 1"
                )
            )
        ).first()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "cabinet_id": str(row[2]) if row[2] else None,
            "params": row[3] if isinstance(row[3], dict) else {},
            "external_job_id": row[4],
        }


async def _tick() -> bool:
    """
    Two-phase. Returns True only when QUEUED work was done (→ loop fast);
    False when we either polled a running video or found nothing (→ sleep,
    since videos are slow and shouldn't be hammered).
    """
    # Phase A — claim + process one queued row (image gen, or video submit).
    row = await _claim_one()
    if row:
        try:
            if row["kind"] == "video":
                advanced = await _process_video_submit(row)
            else:
                await _process_image(row)
                advanced = True
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.exception("visual_worker row failed id=%s", row["id"])
            try:
                await _mark_failed(row["id"], str(exc))
            except Exception:
                pass
            advanced = True
        # advanced=False means a video re-queued to wait for its start
        # frame → sleep so we don't busy-spin while slides render.
        return advanced

    # Phase B — poll one in-flight Kling video.
    vrow = await _next_running_video()
    if vrow:
        try:
            await _poll_video(vrow)
        except Exception:
            logger.exception("visual_worker poll failed id=%s", vrow["id"])
    return False


async def start_visual_worker() -> None:
    """Top-level entry — run forever with reconnect backoff. Called from
    main.py lifespan, same pattern as the event-bus listener."""
    backoff = 1
    logger.info("visual_worker_started interval=%ss", POLL_INTERVAL)
    while True:
        try:
            did_work = await _tick()
            backoff = 1
            if not did_work:
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            logger.info("visual_worker_cancelled")
            raise
        except Exception:
            logger.exception("visual_worker_crashed reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
