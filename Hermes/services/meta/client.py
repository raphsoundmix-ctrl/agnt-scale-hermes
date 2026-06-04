"""Meta Graph API client — async httpx, mock-able, rate-limit aware.

Token resolution order: explicit arg → env META_SYSTEM_USER_TOKEN → env META_ACCESS_TOKEN.
Version from env META_API_VERSION (default v22.0; the changelog watcher owns this).
Mock mode (META_MOCK=1) returns fixtures so the surface is testable without a token.

DRY-RUN writes never reach `graph_post` — only an approved execute path does.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger("hermes.meta")

GRAPH = "https://graph.facebook.com"

# Meta rate-limit / transient error codes worth a short backoff.
_RETRY_CODES = {1, 2, 4, 17, 32, 341, 613}


def api_version() -> str:
    return os.environ.get("META_API_VERSION", "v22.0")


def _base() -> str:
    return f"{GRAPH}/{api_version()}"


def is_mock() -> bool:
    return os.environ.get("META_MOCK", "0").lower() in ("1", "true", "yes")


def _resolve_token(token: Optional[str]) -> Optional[str]:
    return token or os.environ.get("META_SYSTEM_USER_TOKEN") or os.environ.get("META_ACCESS_TOKEN")


class MetaAPIError(Exception):
    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.code = err.get("code")
        self.subcode = err.get("error_subcode")
        self.is_transient = err.get("is_transient", False) or self.code in _RETRY_CODES
        msg = err.get("message") if err else str(body)[:300]
        super().__init__(f"Meta API {status} (code {self.code}): {msg}")


def _encode_form(payload: dict) -> dict:
    # Meta form-encodes scalars; nested objects (targeting, object_story_spec) go as JSON strings.
    out: dict[str, Any] = {}
    for k, v in payload.items():
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def _handle(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"raw": r.text[:500]}
    if r.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        raise MetaAPIError(r.status_code, body)
    buc = r.headers.get("x-business-use-case-usage")
    if buc and isinstance(body, dict):
        body.setdefault("_meta", {})["buc_usage"] = buc  # surface rate-limit budget for callers
    return body


async def graph_get(path: str, *, token: Optional[str] = None, params: Optional[dict] = None) -> dict:
    if is_mock():
        from services.meta import mock
        return mock.get(path, params or {})
    tok = _resolve_token(token)
    if not tok:
        raise MetaAPIError(401, {"error": {"message": "no Meta token (set META_SYSTEM_USER_TOKEN or pass token)"}})
    p = dict(params or {})
    p["access_token"] = tok
    async with httpx.AsyncClient(timeout=30) as c:
        return _handle(await c.get(f"{_base()}/{path.lstrip('/')}", params=p))


async def graph_post(path: str, *, token: Optional[str] = None, data: Optional[dict] = None) -> dict:
    """LIVE write. Reached only by an approved execute path — never by dry-run."""
    if is_mock():
        from services.meta import mock
        return mock.post(path, data or {})
    tok = _resolve_token(token)
    if not tok:
        raise MetaAPIError(401, {"error": {"message": "no Meta token for write"}})
    payload = dict(data or {})
    payload["access_token"] = tok
    async with httpx.AsyncClient(timeout=30) as c:
        return _handle(await c.post(f"{_base()}/{path.lstrip('/')}", data=_encode_form(payload)))
