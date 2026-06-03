"""
Hermes legal router — Oferta opt-in ingest + opt-out wipe.

Called by Backend's /api/legal/oferta after the seller confirms opt-in.
Runs the actual fetch → chunk → embed → Qdrant upsert pipeline as a
background task so Backend can return 202 immediately.

Marketplace → ingester mapping (MVP):

  wildberries → WB Oferta page at seller.wildberries.ru/static/legal/...
                Public HTML, no auth needed. Parsed via simple regex /
                heuristic chunker (200-300 char chunks split on section
                headers).

  ozon        → Ozon Договор at seller.ozon.ru/legal/...
                Similar pipeline.

Current MVP: a deterministic stub that produces 3 placeholder chunks per
cabinet so the wiring is verifiable end-to-end. Real fetch + parser
lands in 3A.1 follow-up — the Hermes ingest service is already in place,
this router just needs the chunker swapped in.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as _sql_text

from config import settings
from services.db import tenant_session
from services.legal_rag import (
    ChunkInput,
    ensure_collection,
    ingest_chunks,
    remove_tenant_oferta,
)


logger = logging.getLogger("hermes.legal")
router = APIRouter(prefix="/legal", tags=["legal"])


class OfertaRequest(BaseModel):
    tenant_id: str
    cabinet_id: str
    marketplace: str  # 'wildberries' | 'ozon'


# ── Auth ──────────────────────────────────────────────────────────


def _require_internal_token(token: str | None) -> None:
    """Same contract as the rest of Hermes internal endpoints."""
    if not settings.HERMES_INTERNAL_TOKEN:
        return  # dev: no token enforced
    if not token or token != settings.HERMES_INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="invalid internal token")


# ── Marketplace ingester stubs ────────────────────────────────────


# In production this would call out to seller.wildberries.ru and parse
# the oferta HTML. For MVP we ship a deterministic placeholder so the
# RAG plumbing (Qdrant collection, embeddings, citation flow) can be
# tested without depending on a flaky external scrape.
def _stub_chunks_for(marketplace: str) -> list[ChunkInput]:
    label = {
        "wildberries": "WB Oferta",
        "ozon": "Ozon Договор",
    }.get(marketplace, marketplace)
    return [
        ChunkInput(
            source=label,
            article="раздел 1",
            title="Предмет договора",
            chunk_text=(
                f"Маркетплейс {marketplace} оказывает услуги по реализации "
                "товара продавца через свою площадку. Продавец отвечает за "
                "качество товара, маркировку, сертификаты соответствия."
            ),
            chunk_index=0,
            source_url=f"https://seller.{marketplace}.ru/legal/oferta",
            version_tag="stub-2026-05",
        ),
        ChunkInput(
            source=label,
            article="раздел 4",
            title="Возвраты и претензии",
            chunk_text=(
                "В случае претензии покупателя продавец обязан рассмотреть "
                "обращение в течение 10 дней. При обоснованной претензии — "
                "возврат денежных средств в течение 14 дней."
            ),
            chunk_index=1,
            source_url=f"https://seller.{marketplace}.ru/legal/oferta",
            version_tag="stub-2026-05",
        ),
        ChunkInput(
            source=label,
            article="раздел 9",
            title="Ответственность сторон",
            chunk_text=(
                "Продавец несёт ответственность за достоверность сведений о "
                "товаре, его легальность, маркировку «Честный знак» и наличие "
                "разрешительных документов."
            ),
            chunk_index=2,
            source_url=f"https://seller.{marketplace}.ru/legal/oferta",
            version_tag="stub-2026-05",
        ),
    ]


# ── Background ingest job ─────────────────────────────────────────


async def _run_ingest(req: OfertaRequest) -> None:
    """Heavy work: chunk + embed + Qdrant upsert + status updates."""
    await ensure_collection()

    async def _update_status(
        status: str,
        *,
        chunks: int | None = None,
        error: str | None = None,
        version_tag: str | None = None,
        preview: dict[str, Any] | None = None,
    ) -> None:
        async with tenant_session(req.tenant_id) as s:
            params: dict[str, Any] = {
                "t": req.tenant_id,
                "c": req.cabinet_id,
                "m": req.marketplace,
                "status": status,
                "error": error,
            }
            set_fragments = [
                "status = :status",
                "error = :error",
            ]
            if chunks is not None:
                params["chunks"] = chunks
                set_fragments.append("chunks_count = :chunks")
            if version_tag is not None:
                params["version_tag"] = version_tag
                set_fragments.append("version_tag = :version_tag")
            if preview is not None:
                import json as _json
                params["preview"] = _json.dumps(preview, ensure_ascii=False)
                set_fragments.append("preview = CAST(:preview AS jsonb)")
            if status == "done":
                set_fragments.append("completed_at = now()")
            sql = (
                "UPDATE legal_oferta_ingests SET "
                + ", ".join(set_fragments)
                + " WHERE tenant_id = :t AND cabinet_id = :c AND marketplace = :m"
            )
            await s.execute(_sql_text(sql), params)
            await s.commit()

    try:
        await _update_status("fetching", preview={"note": "scraping public oferta page"})
        # In production: actual HTTP fetch + parse → list[ChunkInput].
        chunks = _stub_chunks_for(req.marketplace)
        await _update_status(
            "indexing",
            preview={"chunks_planned": len(chunks)},
        )

        # Wipe any old chunks from previous ingest before re-inserting.
        await remove_tenant_oferta(
            tenant_id=req.tenant_id,
            cabinet_id=req.cabinet_id,
            marketplace=req.marketplace,
        )

        count = await ingest_chunks(
            chunks,
            scope="tenant",
            tenant_id=req.tenant_id,
            cabinet_id=req.cabinet_id,
        )
        await _update_status(
            "done",
            chunks=count,
            version_tag=chunks[0].version_tag if chunks else None,
        )
        logger.info(
            "legal_oferta_ingest_done",
            extra={
                "tenant_id": req.tenant_id,
                "cabinet_id": req.cabinet_id,
                "marketplace": req.marketplace,
                "chunks": count,
            },
        )
    except Exception as exc:
        logger.exception("legal_oferta_ingest_failed")
        try:
            await _update_status("failed", error=str(exc)[:1000])
        except Exception:
            pass


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/ingest_oferta", status_code=202)
async def ingest_oferta(
    req: OfertaRequest,
    background: BackgroundTasks,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Backend → Hermes trigger. Returns 202 immediately."""
    _require_internal_token(x_internal_token)
    if req.marketplace not in ("wildberries", "ozon"):
        raise HTTPException(status_code=400, detail="unsupported marketplace")
    background.add_task(_run_ingest, req)
    return {"status": "accepted"}


@router.post("/remove_oferta", status_code=200)
async def remove_oferta(
    req: OfertaRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """Backend → Hermes opt-out trigger. Synchronous (no background task)."""
    _require_internal_token(x_internal_token)
    removed = await remove_tenant_oferta(
        tenant_id=req.tenant_id,
        cabinet_id=req.cabinet_id,
        marketplace=req.marketplace,
    )
    return {"removed": removed}
