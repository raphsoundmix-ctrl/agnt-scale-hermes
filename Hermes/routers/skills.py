"""
Skills Router — universal entry point for all MAO agents.
GET  /skills                    — список всех скиллов
POST /skills/{skill_id}/run     — запустить скилл
"""
import logging
import time
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, Any

from skills.registry import SKILLS, list_skills, get_skill_class

logger = logging.getLogger("hermes.skills")

router = APIRouter(tags=["skills"])


# ─── Per-tenant rate limit ──────────────────────────────────────────────
# Hermes runs without Redis in some deployments, so we keep an in-process
# token bucket keyed by (tenant_id, skill_id). 20 calls / 60 s is generous
# enough for legitimate Backend-driven traffic but firm enough to stop a
# misbehaving caller from looping LLM/skill calls.
_RATE_WINDOW_S = 60
_RATE_MAX = 20
_rate_state: dict[tuple[str, str], list[float]] = defaultdict(list)


def _check_rate_limit(tenant_id: str, skill_id: str) -> None:
    now = time.time()
    key = (tenant_id, skill_id)
    bucket = _rate_state[key]
    cutoff = now - _RATE_WINDOW_S
    # Drop expired timestamps in-place so the dict doesn't grow unbounded.
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _RATE_MAX:
        retry = int(_RATE_WINDOW_S - (now - bucket[0]))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {skill_id}",
            headers={"Retry-After": str(max(retry, 1))},
        )
    bucket.append(now)


class SkillRunRequest(BaseModel):
    tenant_id: str
    cabinet_id: Optional[str] = None
    wb_api_key: Optional[str] = Field(default=None, repr=False)
    params: dict[str, Any] = Field(default_factory=dict)

    def __repr__(self) -> str:
        # Defense in depth: even if Field(repr=False) is bypassed or a
        # consumer reconstructs the model, never include the raw WB key in
        # logs / tracebacks.
        return (
            f"SkillRunRequest(tenant_id={self.tenant_id!r}, "
            f"cabinet_id={self.cabinet_id!r}, "
            f"wb_api_key='***', params={self.params!r})"
        )


@router.get("")
async def all_skills():
    """Список всех зарегистрированных скиллов."""
    return {"count": len(SKILLS), "skills": list_skills()}


@router.get("/{skill_id}")
async def skill_info(skill_id: str):
    """Метаданные конкретного скилла."""
    skill_cls = get_skill_class(skill_id)
    if skill_cls is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return {
        "id": skill_cls.id,
        "name": skill_cls.name,
        "description": skill_cls.description,
        "requires_wb_api": skill_cls.requires_wb_api,
    }


@router.post("/{skill_id}/run")
async def run_skill(skill_id: str, req: SkillRunRequest, request: Request):
    """
    Универсальный запуск скилла. Frontend → FastAPI → Hermes → этот endpoint.
    """
    cid = request.state.correlation_id

    # Per-tenant rate limit BEFORE any expensive work (LLM calls, WB lookups).
    _check_rate_limit(req.tenant_id, skill_id)

    skill_cls = get_skill_class(skill_id)
    if skill_cls is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    if skill_cls.requires_wb_api and not req.wb_api_key:
        raise HTTPException(status_code=400, detail=f"Skill '{skill_id}' requires wb_api_key")

    try:
        skill = skill_cls(
            tenant_id=req.tenant_id,
            wb_api_key=req.wb_api_key,
            cabinet_id=req.cabinet_id,
        )
        result = await skill.run(**req.params)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception:
        # Стектрейс с raw WB-ключами/токенами НЕ должен попадать в response.
        logger.exception(
            f"Skill '{skill_id}' execution failed for tenant={req.tenant_id}",
            extra={"correlation_id": cid},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Skill execution failed (correlation_id={cid})",
        )
