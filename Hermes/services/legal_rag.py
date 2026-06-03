"""
Legal RAG — Phase 3A.

Two surfaces:

  ensure_collection()            One-time bootstrap. Creates the Qdrant
                                 collection if missing. Idempotent.

  ingest_chunks(...)             Insert chunks (already split by caller)
                                 into Qdrant + record metadata rows in
                                 the legal_documents table.

  search(query, tenant_id?)      Embed query, hit Qdrant with payload
                                 filter (scope=global OR tenant=ours),
                                 return top-K chunks with their full
                                 metadata for citation rendering.

Design choices:

* Single Qdrant collection with a `scope` payload field rather than
  per-tenant collections — simpler ops, no orphaned collections when a
  tenant churns. Tenant isolation enforced both at Qdrant filter level
  AND at Postgres RLS level on the metadata fetch (defense in depth).

* Cosine distance: text-embedding-3-small is already L2-normalized, and
  cosine is the standard distance for both BGE-M3 (default) and OpenAI
  embeddings — both produce L2-normalised vectors.

* qdrant_point_id = a generated UUIDv4 stored as text in both Qdrant
  (point id) and legal_documents.qdrant_point_id. Lets us JOIN cleanly.

* Citation contract: every chunk surfaces {source, article, source_url,
  chunk_text, score}. Skills that don't include at least source+article
  in their response should be rejected by the constitutional critic.

Failure modes:

* Qdrant down → search raises; legal skill catches and falls back to
  pre-RAG checklist mode.
* OpenAI down → embed raises EmbeddingDisabled / EmbeddingError; same
  fallback path.
* RLS misconfigured → Postgres returns 0 rows for tenant docs; user
  still gets global laws back. Logged as warning.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from sqlalchemy import text as _sql_text

from config import settings
from services.db import tenant_session
from services.embeddings import (
    EmbeddingDisabled,
    EmbeddingError,
    embed_one,
    embed_texts,
)


logger = logging.getLogger("hermes.legal_rag")


# ─── Types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChunkInput:
    """Input to ingest_chunks. Caller has already split the doc."""
    source: str           # '152-ФЗ' / 'WB Oferta' / etc.
    article: Optional[str]  # 'ст. 18 п. 4' / 'раздел V п. 3.2'
    title: Optional[str]
    chunk_text: str
    chunk_index: int
    source_url: Optional[str] = None
    version_tag: Optional[str] = None
    language: str = "ru"


@dataclass(frozen=True)
class SearchHit:
    """Returned by search(). Suitable for direct rendering in citations."""
    source: str
    article: Optional[str]
    title: Optional[str]
    source_url: Optional[str]
    chunk_text: str
    score: float
    scope: str             # 'global' | 'tenant'


# ─── Qdrant raw HTTP client ──────────────────────────────────────────


def _qdrant_url(path: str) -> str:
    base = settings.QDRANT_URL.rstrip("/")
    return f"{base}{path}"


async def _qdrant_get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(_qdrant_url(path))
        r.raise_for_status()
        return r.json()


async def _qdrant_put(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.put(_qdrant_url(path), json=body)
        r.raise_for_status()
        return r.json()


async def _qdrant_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(_qdrant_url(path), json=body)
        r.raise_for_status()
        return r.json()


# ─── Collection bootstrap ────────────────────────────────────────────


async def ensure_collection() -> None:
    """
    Create the legal corpus collection if it doesn't exist. Idempotent —
    safe to call on every Hermes boot. Indexes the payload fields we
    filter on (scope, tenant_id, cabinet_id, source) so search is fast.
    """
    coll = settings.LEGAL_QDRANT_COLLECTION
    try:
        await _qdrant_get(f"/collections/{coll}")
        return
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise

    logger.info(f"legal_rag creating qdrant collection={coll}")
    await _qdrant_put(
        f"/collections/{coll}",
        {
            "vectors": {
                "size": settings.EMBEDDING_DIMENSIONS,
                "distance": "Cosine",
            },
        },
    )
    # Payload indexes — keep filtered search snappy.
    for field, schema in (
        ("scope", "keyword"),
        ("tenant_id", "keyword"),
        ("cabinet_id", "keyword"),
        ("source", "keyword"),
    ):
        try:
            await _qdrant_put(
                f"/collections/{coll}/index",
                {"field_name": field, "field_schema": schema},
            )
        except httpx.HTTPStatusError as e:
            # 409 (already indexed) is fine.
            if e.response.status_code != 409:
                logger.warning(
                    f"legal_rag index_failed field={field} status={e.response.status_code}"
                )


# ─── Ingestion ───────────────────────────────────────────────────────


async def ingest_chunks(
    chunks: list[ChunkInput],
    *,
    scope: str,
    tenant_id: Optional[str] = None,
    cabinet_id: Optional[str] = None,
    batch_size: int = 64,
) -> int:
    """
    Embed chunks, upsert into Qdrant, persist metadata rows.

    scope='global'  → tenant_id MUST be None (federal laws baseline).
    scope='tenant'  → tenant_id MUST be set (WB/Ozon Oferta opt-in).

    Returns the number of chunks successfully ingested.
    """
    if not chunks:
        return 0
    if scope == "global" and tenant_id is not None:
        raise ValueError("global-scope ingest must not have tenant_id")
    if scope == "tenant" and not tenant_id:
        raise ValueError("tenant-scope ingest requires tenant_id")

    await ensure_collection()
    coll = settings.LEGAL_QDRANT_COLLECTION
    ingested = 0

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        try:
            vectors = await embed_texts([c.chunk_text for c in batch])
        except (EmbeddingDisabled, EmbeddingError) as exc:
            logger.error(f"legal_rag embed_batch_failed: {exc}")
            raise

        points: list[dict[str, Any]] = []
        metadata_rows: list[dict[str, Any]] = []
        for chunk, vec in zip(batch, vectors):
            point_id = str(uuid.uuid4())
            payload = {
                "scope": scope,
                "tenant_id": tenant_id or "",
                "cabinet_id": cabinet_id or "",
                "source": chunk.source,
                "article": chunk.article or "",
                "language": chunk.language,
            }
            points.append(
                {
                    "id": point_id,
                    "vector": vec,
                    "payload": payload,
                }
            )
            metadata_rows.append(
                {
                    "qdrant_point_id": point_id,
                    "scope": scope,
                    "source": chunk.source,
                    "source_url": chunk.source_url,
                    "article": chunk.article,
                    "title": chunk.title,
                    "language": chunk.language,
                    "chunk_text": chunk.chunk_text,
                    "chunk_index": chunk.chunk_index,
                    "version_tag": chunk.version_tag,
                }
            )

        # Qdrant upsert first — if it fails, no orphan metadata row.
        await _qdrant_put(
            f"/collections/{coll}/points?wait=true",
            {"points": points},
        )

        # Metadata in Postgres. Use admin context for global; tenant
        # context for tenant scope (RLS check on INSERT).
        async with tenant_session(tenant_id, admin=(scope == "global")) as s:
            for row in metadata_rows:
                await s.execute(
                    _sql_text(
                        """
                        INSERT INTO legal_documents (
                            tenant_id, cabinet_id, scope, source, source_url,
                            article, title, language, chunk_text, chunk_index,
                            token_count, qdrant_point_id, embedding_model,
                            version_tag
                        ) VALUES (
                            :tenant_id, :cabinet_id, :scope, :source, :source_url,
                            :article, :title, :language, :chunk_text, :chunk_index,
                            :token_count, :qdrant_point_id, :embedding_model,
                            :version_tag
                        )
                        ON CONFLICT (qdrant_point_id) DO NOTHING
                        """
                    ),
                    {
                        **row,
                        "tenant_id": tenant_id,
                        "cabinet_id": cabinet_id,
                        # Token count is the chunk length in chars / 4
                        # heuristic — good enough for budget tracking.
                        "token_count": max(1, len(row["chunk_text"]) // 4),
                        "embedding_model": settings.EMBEDDING_MODEL,
                    },
                )
            await s.commit()
        ingested += len(points)

    logger.info(
        "legal_rag_ingested",
        extra={
            "count": ingested,
            "scope": scope,
            "tenant_id": tenant_id,
            "source": chunks[0].source if chunks else None,
        },
    )
    return ingested


# ─── Search ──────────────────────────────────────────────────────────


async def search(
    query: str,
    *,
    tenant_id: Optional[str] = None,
    cabinet_id: Optional[str] = None,
    top_k: int = 5,
    include_tenant_oferta: bool = True,
) -> list[SearchHit]:
    """
    RAG search:
      1. embed query (OpenAI)
      2. Qdrant filter: scope=global OR (tenant=ours AND opt-in oferta)
      3. JOIN metadata via qdrant_point_id (RLS-aware)
      4. return SearchHit objects suitable for citation rendering

    Caller (Legal skill) is responsible for rejecting unsourced answers
    via constitutional critic.
    """
    if not query.strip():
        return []
    try:
        query_vec = await embed_one(query)
    except (EmbeddingDisabled, EmbeddingError) as exc:
        logger.warning(f"legal_rag search_embed_failed: {exc}")
        return []

    coll = settings.LEGAL_QDRANT_COLLECTION

    should_filters: list[dict[str, Any]] = [
        {"key": "scope", "match": {"value": "global"}}
    ]
    if tenant_id and include_tenant_oferta:
        tenant_clause: dict[str, Any] = {
            "must": [
                {"key": "scope", "match": {"value": "tenant"}},
                {"key": "tenant_id", "match": {"value": tenant_id}},
            ]
        }
        if cabinet_id:
            tenant_clause["must"].append(
                {"key": "cabinet_id", "match": {"value": cabinet_id}}
            )
        should_filters.append(tenant_clause)

    body = {
        "vector": query_vec,
        "limit": top_k,
        "with_payload": True,
        "filter": {"should": should_filters},
    }

    try:
        result = await _qdrant_post(
            f"/collections/{coll}/points/search", body
        )
    except httpx.HTTPStatusError as e:
        logger.error(
            f"legal_rag qdrant_search_failed status={e.response.status_code}"
        )
        return []

    hits = result.get("result") or []
    if not hits:
        return []

    # Fetch metadata for the returned point ids. RLS-isolated session so
    # tenant docs only resolve for the right tenant.
    point_ids = [h.get("id") for h in hits if h.get("id")]
    if not point_ids:
        return []

    scores_by_id: dict[str, float] = {
        str(h.get("id")): float(h.get("score", 0.0)) for h in hits
    }

    async with tenant_session(tenant_id) as s:
        rows = (
            await s.execute(
                _sql_text(
                    """
                    SELECT qdrant_point_id, scope, source, source_url,
                           article, title, chunk_text
                    FROM legal_documents
                    WHERE qdrant_point_id = ANY(:ids)
                    """
                ),
                {"ids": [str(p) for p in point_ids]},
            )
        ).all()

    by_id: dict[str, SearchHit] = {}
    for row in rows:
        pid = row[0]
        by_id[pid] = SearchHit(
            scope=row[1],
            source=row[2],
            source_url=row[3],
            article=row[4],
            title=row[5],
            chunk_text=row[6],
            score=scores_by_id.get(pid, 0.0),
        )

    # Re-order to match Qdrant's relevance ranking (DB ANY() loses it).
    ordered: list[SearchHit] = []
    for pid in point_ids:
        hit = by_id.get(str(pid))
        if hit:
            ordered.append(hit)
    return ordered


# ─── Tenant cleanup ──────────────────────────────────────────────────


async def remove_tenant_oferta(
    *,
    tenant_id: str,
    cabinet_id: str,
    marketplace: str,
) -> int:
    """
    Wipe a tenant's Oferta chunks (e.g. on re-ingest or opt-out).
    Removes Qdrant points + Postgres rows that match
    (scope='tenant', tenant_id, cabinet_id, source=marketplace label).

    Returns chunks removed.
    """
    coll = settings.LEGAL_QDRANT_COLLECTION
    source_label = {
        "wildberries": "WB Oferta",
        "ozon": "Ozon Договор",
    }.get(marketplace, marketplace)

    # 1) Postgres: collect point ids first (RLS gates the read).
    async with tenant_session(tenant_id) as s:
        rows = (
            await s.execute(
                _sql_text(
                    """
                    SELECT qdrant_point_id FROM legal_documents
                    WHERE tenant_id = :t
                      AND cabinet_id = :c
                      AND scope = 'tenant'
                      AND source = :src
                    """
                ),
                {"t": tenant_id, "c": cabinet_id, "src": source_label},
            )
        ).all()
        point_ids = [r[0] for r in rows]
        if point_ids:
            await s.execute(
                _sql_text(
                    "DELETE FROM legal_documents "
                    "WHERE qdrant_point_id = ANY(:ids)"
                ),
                {"ids": point_ids},
            )
            await s.commit()

    # 2) Qdrant: delete points by ids.
    if point_ids:
        try:
            await _qdrant_post(
                f"/collections/{coll}/points/delete?wait=true",
                {"points": point_ids},
            )
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"legal_rag remove_oferta qdrant_delete_failed "
                f"status={e.response.status_code}"
            )

    return len(point_ids)
