"""
fal.ai video provider — Kling 3.0 image-to-video.

OpenRouter exposes no video models, so video generation runs through
fal.ai's queue API against Kling. We use IMAGE-TO-VIDEO specifically: the
start frame is one of our generated card slides (or an uploaded product /
avatar element), which is what keeps the товар/лицо identity stable in the
motion — Kling animates around the locked start frame rather than
re-imagining the subject.

fal queue protocol (raw HTTP, no SDK):
  submit : POST {base}/{model}                      → {request_id, status_url, response_url}
  status : GET  {status_url}                        → {status: IN_QUEUE|IN_PROGRESS|COMPLETED|...}
  result : GET  {response_url}                       → {video: {url, ...}}
  auth   : Authorization: Key {FAL_KEY}

Two-phase by design (Kling takes 1-5 min):
  submit_i2v()  — fire the job, return request_id + urls. Non-blocking.
  poll()        — check one job; returns the video url when COMPLETED,
                  None while still cooking, raises/returns error on fail.

Env-gated: empty FAL_KEY → is_enabled() is False and callers skip video
without crashing the image factory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger("hermes.fal_video")


def is_enabled() -> bool:
    return bool(settings.FAL_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {settings.FAL_KEY}",
        "Content-Type": "application/json",
    }


@dataclass
class SubmitResult:
    ok: bool
    request_id: Optional[str] = None
    status_url: Optional[str] = None
    response_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PollResult:
    # status: "processing" (still running), "done" (video_url set),
    #         "failed" (error set)
    status: str
    video_url: Optional[str] = None
    error: Optional[str] = None


async def submit_i2v(
    *,
    start_image_url: str,
    prompt: str,
    duration: int = 5,
    negative_prompt: str = "blur, distort, low quality, deformed, extra limbs, "
    "text artifacts, watermark, logo, mutated hands",
    cfg_scale: float = 0.5,
    elements: Optional[list[dict[str, Any]]] = None,
    end_image_url: Optional[str] = None,
    generate_audio: bool = False,
) -> SubmitResult:
    """
    Submit a Kling image-to-video job. `start_image_url` is REQUIRED by
    Kling i2v — it's the identity anchor. `elements` (optional) are extra
    reference objects Kling uses to keep additional subjects consistent.
    """
    if not is_enabled():
        return SubmitResult(ok=False, error="FAL_KEY not configured")
    if not start_image_url:
        return SubmitResult(ok=False, error="start_image_url required for Kling i2v")

    # Kling `duration` is an enum string "3".."15".
    dur = str(max(3, min(int(duration), 15)))
    payload: dict[str, Any] = {
        "start_image_url": start_image_url,
        "prompt": prompt[:2400],
        "duration": dur,
        "negative_prompt": negative_prompt,
        "cfg_scale": cfg_scale,
        "generate_audio": generate_audio,
    }
    if end_image_url:
        payload["end_image_url"] = end_image_url
    if elements:
        payload["elements"] = elements

    url = f"{settings.FAL_QUEUE_BASE}/{settings.FAL_KLING_MODEL}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=_headers(), json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return SubmitResult(
            ok=False, error=f"fal submit {e.response.status_code}: {e.response.text[:300]}"
        )
    except httpx.RequestError as e:
        return SubmitResult(ok=False, error=f"fal unreachable: {e}")

    req_id = data.get("request_id")
    status_url = data.get("status_url")
    response_url = data.get("response_url")
    if not req_id:
        return SubmitResult(ok=False, error=f"fal: no request_id in {str(data)[:200]}")
    logger.info("fal_kling_submitted request_id=%s", req_id)
    return SubmitResult(
        ok=True,
        request_id=req_id,
        status_url=status_url,
        response_url=response_url,
    )


async def poll(
    *, status_url: str, response_url: str
) -> PollResult:
    """Check one job. COMPLETED → fetch the result for the video url."""
    if not is_enabled():
        return PollResult(status="failed", error="FAL_KEY not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            s = await client.get(status_url, headers=_headers())
            s.raise_for_status()
            sdata = s.json()
            state = (sdata.get("status") or "").upper()

            if state in ("IN_QUEUE", "IN_PROGRESS"):
                return PollResult(status="processing")

            if state == "COMPLETED":
                r = await client.get(response_url, headers=_headers())
                r.raise_for_status()
                rdata = r.json()
                video = rdata.get("video") or {}
                vurl = video.get("url")
                if not vurl:
                    return PollResult(
                        status="failed",
                        error=f"completed but no video url: {str(rdata)[:200]}",
                    )
                return PollResult(status="done", video_url=vurl)

            # ERROR / CANCELLED / unknown
            return PollResult(status="failed", error=f"fal state={state}")
    except httpx.HTTPStatusError as e:
        return PollResult(
            status="failed", error=f"fal poll {e.response.status_code}: {e.response.text[:200]}"
        )
    except httpx.RequestError as e:
        # Network blip — treat as still processing so we retry next tick.
        logger.warning("fal poll transient error: %s", e)
        return PollResult(status="processing")
