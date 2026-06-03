"""
OpenRouter media generation client — images (Nano Banana Pro) and videos
(Seedance 2.0 / Kling 3.0).

OpenRouter exposes vendor models behind a uniform `/chat/completions`
endpoint. For image models like `google/gemini-2.5-flash-image-preview`
(Nano Banana Pro lives in this family) we send a multimodal request and
the response contains an `image_url` block. For video models the response
is a hosted asset URL we then mirror to MinIO.

This module returns:
  - asset URL (after MinIO mirror), or
  - external job id if the model is async-only and needs polling.

If OPENROUTER_API_KEY isn't configured or a model fails, we return a
structured error so the caller can fall back gracefully.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx

from config import settings
from services import storage

logger = logging.getLogger("hermes.media")


@dataclass
class MediaResult:
    success: bool
    kind: Literal["image", "video"]
    storage_url: Optional[str] = None
    storage_path: Optional[str] = None
    external_job_id: Optional[str] = None
    model: str = ""
    cost_usd: Optional[float] = None
    error: Optional[str] = None
    raw_response: Optional[dict] = None


# ───── Image (Nano Banana Pro) ─────────────────────────────────────


async def generate_image(
    prompt: str,
    *,
    tenant_id: str,
    cabinet_id: str,
    generation_id: str,
    slide_index: int = 1,
    reference_image_urls: Optional[list[str]] = None,
    aspect_ratio: str = "3:4",
    model: Optional[str] = None,
) -> MediaResult:
    """
    Generate one image via OpenRouter image model. Mirrors the result to
    MinIO and returns the public URL.

    `reference_image_urls` enables image-to-image / character consistency
    when the model supports it (Nano Banana Pro does).
    """
    model_id = model or settings.OPENROUTER_IMAGE_MODEL
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://mao.ai",
        "X-Title": "MAO.ai Visual",
        "Content-Type": "application/json",
    }

    # Build multimodal content
    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in reference_image_urls or []:
        user_content.append({"type": "image_url", "image_url": {"url": url}})

    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": user_content}],
        "modalities": ["image", "text"],
        # Provider-specific hints — OpenRouter passes through unknown keys
        "extra_body": {"aspect_ratio": aspect_ratio},
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return MediaResult(
            success=False,
            kind="image",
            model=model_id,
            error=f"openrouter {e.response.status_code}: {e.response.text[:300]}",
        )
    except httpx.RequestError as e:
        return MediaResult(
            success=False,
            kind="image",
            model=model_id,
            error=f"openrouter unreachable: {e}",
        )

    # Extract image URL from response. OpenRouter image models put it under
    # choices[0].message.images[0].image_url.url OR as a base64 in content.
    image_url = _extract_image_url(data)
    if not image_url:
        return MediaResult(
            success=False,
            kind="image",
            model=model_id,
            error="response missing image_url",
            raw_response=data,
        )

    # Mirror to MinIO. OpenRouter image models return EITHER an https URL
    # OR an inline base64 data URI (Gemini image models do the latter).
    # Handle both: data: → decode + upload_bytes; https: → fetch + mirror.
    try:
        if image_url.startswith("data:"):
            raw_bytes, mime = _decode_data_uri(image_url)
            ext = _ext_from_mime(mime)
            dest_path = (
                f"{tenant_id}/{cabinet_id}/generations/{generation_id}/{slide_index}.{ext}"
            )
            # upload_bytes is sync (boto3) — run off the event loop.
            import asyncio as _asyncio
            mirrored_url = await _asyncio.get_running_loop().run_in_executor(
                None,
                lambda: storage.upload_bytes(dest_path, raw_bytes, content_type=mime),
            )
        else:
            ext = _guess_ext_from_url(image_url, default="png")
            dest_path = (
                f"{tenant_id}/{cabinet_id}/generations/{generation_id}/{slide_index}.{ext}"
            )
            mirrored_url = await storage.upload_from_url(image_url, dest_path)
    except Exception as e:
        logger.exception("MinIO upload failed")
        return MediaResult(
            success=False,
            kind="image",
            model=model_id,
            error=f"storage upload failed: {e}",
            raw_response=data,
        )

    cost = _extract_cost(data)
    return MediaResult(
        success=True,
        kind="image",
        storage_url=mirrored_url,
        storage_path=dest_path,
        model=model_id,
        cost_usd=cost,
        raw_response=data,
    )


# ───── Video (Seedance 2.0 / Kling 3.0) ────────────────────────────


async def generate_video(
    prompt: str,
    *,
    tenant_id: str,
    cabinet_id: str,
    generation_id: str,
    reference_image_url: Optional[str] = None,
    aspect_ratio: str = "3:4",
    duration_seconds: int = 5,
    model: Optional[str] = None,
) -> MediaResult:
    """
    Generate a short video. Video models on OpenRouter are typically async:
    we either get the final URL synchronously (Seedance often does), or an
    external job id we poll later.
    """
    model_id = model or settings.OPENROUTER_VIDEO_MODEL
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://mao.ai",
        "X-Title": "MAO.ai Visual",
        "Content-Type": "application/json",
    }

    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if reference_image_url:
        user_content.append(
            {"type": "image_url", "image_url": {"url": reference_image_url}}
        )

    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": user_content}],
        "modalities": ["video", "text"],
        "extra_body": {
            "aspect_ratio": aspect_ratio,
            "duration": duration_seconds,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return MediaResult(
            success=False,
            kind="video",
            model=model_id,
            error=f"openrouter {e.response.status_code}: {e.response.text[:300]}",
        )
    except httpx.RequestError as e:
        return MediaResult(
            success=False,
            kind="video",
            model=model_id,
            error=f"openrouter unreachable: {e}",
        )

    video_url = _extract_video_url(data)
    job_id = _extract_job_id(data)

    if not video_url and job_id:
        # Async-only path — return job id, frontend polls /api/visual/jobs/{id}
        return MediaResult(
            success=True,
            kind="video",
            external_job_id=job_id,
            model=model_id,
            cost_usd=_extract_cost(data),
            raw_response=data,
        )

    if not video_url:
        return MediaResult(
            success=False,
            kind="video",
            model=model_id,
            error="response missing video_url and job_id",
            raw_response=data,
        )

    dest_path = f"{tenant_id}/{cabinet_id}/generations/{generation_id}/video.mp4"
    try:
        mirrored_url = await storage.upload_from_url(
            video_url, dest_path, content_type="video/mp4"
        )
    except Exception as e:
        return MediaResult(
            success=False,
            kind="video",
            model=model_id,
            error=f"storage upload failed: {e}",
            raw_response=data,
        )

    return MediaResult(
        success=True,
        kind="video",
        storage_url=mirrored_url,
        storage_path=dest_path,
        model=model_id,
        cost_usd=_extract_cost(data),
        raw_response=data,
    )


# ───── helpers ──────────────────────────────────────────────────────


def _extract_image_url(data: dict) -> Optional[str]:
    """Search the OpenRouter response shape for an image URL."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError):
        return None

    # OpenAI-style multimodal response
    images = message.get("images") or []
    for img in images:
        url = (img.get("image_url") or {}).get("url") or img.get("url")
        if url:
            return url

    # Gemini-style: content may be a list of parts
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("image_url", "image"):
                url = (part.get("image_url") or {}).get("url") or part.get("url")
                if url:
                    return url

    return None


def _extract_video_url(data: dict) -> Optional[str]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError):
        return None

    for key in ("videos", "video", "media"):
        items = message.get(key)
        if not items:
            continue
        if isinstance(items, dict):
            items = [items]
        for item in items:
            url = (item.get("video_url") or {}).get("url") or item.get("url")
            if url:
                return url

    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("video_url", "video"):
                url = (part.get("video_url") or {}).get("url") or part.get("url")
                if url:
                    return url
    return None


def _extract_job_id(data: dict) -> Optional[str]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError):
        return None
    for key in ("job_id", "external_job_id", "task_id"):
        v = message.get(key)
        if v:
            return str(v)
    return data.get("id")


def _extract_cost(data: dict) -> Optional[float]:
    usage = data.get("usage") or {}
    cost = usage.get("cost") or usage.get("total_cost")
    try:
        return float(cost) if cost is not None else None
    except (ValueError, TypeError):
        return None


def _guess_ext_from_url(url: str, default: str = "png") -> str:
    lower = url.lower().split("?")[0]
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        if lower.endswith("." + ext):
            return ext
    return default


def _decode_data_uri(uri: str) -> tuple[bytes, str]:
    """`data:image/png;base64,iVBOR...` → (raw_bytes, mime). Raises on
    malformed input."""
    import base64

    header, _, b64 = uri.partition(",")
    if not b64:
        raise ValueError("malformed data URI: no comma payload")
    mime = "image/png"
    if header.startswith("data:"):
        meta = header[len("data:"):]
        mime = meta.split(";", 1)[0] or mime
    return base64.b64decode(b64), mime


def _ext_from_mime(mime: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(mime.lower(), "png")
