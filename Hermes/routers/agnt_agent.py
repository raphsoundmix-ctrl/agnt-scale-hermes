"""AGNT SCALE — clean agent runtime (path A).

Endpoints (token-protected by main.py X-Internal-Token middleware):
  GET  /agent/agents          — roster
  GET  /agent/memory/ping     — RLS memory round-trip probe
  POST /agent/chat            — conversational (per-agent memory; orchestrator reads all)
  POST /agent/run             — STRUCTURED task → JSON result, stored as long-term memory

No MAO coupling. Memory via services.agnt_memory (RLS: per-account / per-agent /
orchestrator-reads-all). LLM via services.llm_router (OpenRouter).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.llm_router import call_llm
from services import agnt_memory as mem
from services.meta import tools as meta_tools
from services.meta import architect as meta_architect
from agents import AGENTS

router = APIRouter(tags=["agnt"])
log = logging.getLogger("hermes.agnt")


# ───────────────────────── models ─────────────────────────

class ChatRequest(BaseModel):
    account_id: str
    agent_id: str
    message: str
    ad_account_id: Optional[str] = None


class ChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    agent_id: str
    name: str
    model: str
    reply: str


class RunRequest(BaseModel):
    account_id: str
    agent_id: str
    input: dict[str, Any]
    ad_account_id: Optional[str] = None


class NoteRequest(BaseModel):
    account_id: str
    agent_id: str
    content: str
    ad_account_id: Optional[str] = None


class SearchRequest(BaseModel):
    account_id: str
    agent_id: str
    query: str
    limit: int = 8


# ───────────────────────── helpers ─────────────────────────

def _strip(prefix: str, c: str) -> str:
    return c[len(prefix):] if c.startswith(prefix) else c


def _parse_json(text: str) -> dict:
    """Robustly pull the first JSON object out of an LLM reply."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(json)?", "", t).strip().rstrip("`").strip()
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        return {"_unparsed": text[:1500]}


async def _llm_json(system: str, user: str, model: str, max_tokens: int = 1200) -> dict:
    resp = await call_llm([{"role": "user", "content": user}], system=system, model=model, max_tokens=max_tokens)
    try:
        raw = resp["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raw = str(resp)
    return _parse_json(raw)


# Write-gate: keep long-term memory clean. A durable fact must carry a concrete
# signal (metric / money / named lever / decision) and not be a passing question.
_SIGNAL = re.compile(
    r"\d|%|\$|score|ctr|cpc|cpa|cpl|roas|capi|emq|hook|kill|scal|budget|audience|"
    r"creative|offer|retention|churn|convers|frequency|cpm",
    re.I,
)


def _worth_long(content: str) -> tuple[bool, str]:
    c = (content or "").strip()
    if len(c) < 25:
        return False, "too short"
    if c.endswith("?"):
        return False, "question, not a durable fact"
    if not _SIGNAL.search(c):
        return False, "no concrete signal (metric / lever / decision)"
    return True, "ok"


# ───────────────────────── structured task specs ─────────────────────────

def _creative_input(d: dict) -> str:
    return (
        "CREATIVE DATA:\n"
        f"Name: {d.get('name','(n/a)')}\nType: {d.get('type','(n/a)')}\n"
        f"Headline: {d.get('title','(none)')}\nAd copy: {d.get('body','(none)')}\n"
        f"Transcript/visual: {d.get('transcript') or d.get('image_description') or '(none)'}\n\n"
        "ACCOUNT METRICS:\n"
        f"Spend: ${d.get('spend','?')}\nCTR: {d.get('ctr','?')}% (benchmark {d.get('benchmark_ctr','1.3')}%)\n"
        f"CPC: ${d.get('cpc','?')} (benchmark ${d.get('benchmark_cpc','1.10')})\n"
        f"CPL: {d.get('cpl','?')}\nNiche: {d.get('niche','(n/a)')}\nDays running: {d.get('days','?')}\n\n"
        "Evaluate this creative and return the JSON."
    )


def _script_input(d: dict) -> str:
    return (
        "USER INPUT:\n"
        f"OFFER: {d.get('offer','')}\nAUDIENCE: {d.get('audience','')}\n"
        f"NICHE: {d.get('niche','(n/a)')}\nLENGTH: {d.get('duration_sec',20)} sec\n"
        f"TONE: {d.get('tone','neutral, expert')}\nHINT: {d.get('hint','(none)')}\n\n"
        "Generate the script and return the JSON."
    )


def _diag_input(d: dict) -> str:
    return (
        f"Screenshot category: {d.get('category','OTHER')}\n"
        f"Operator note: {d.get('notes','(none)')}\n"
        f"On-screen text / description: {d.get('text') or d.get('image_description') or '(none)'}\n\n"
        "Diagnose and return the JSON."
    )


TASKS: dict[str, dict] = {
    "creative_strategic": {
        "system": (
            "You are a senior Meta Ads creative director. Evaluate the creative on data. "
            "Return ONLY a JSON object, no prose, with EXACTLY these keys: "
            "score (int 1-10), cluster (curiosity|problem|result|authority|social_proof|transformation|offer), "
            "hook_strength (strong|medium|weak), cta_strength (strong|medium|weak), "
            "audience_fit (high|medium|low), advantages (string[]), recommendations (string[]), "
            "kill_signal (bool), kill_reason (string or null). "
            "kill_signal=true only if score<=3 OR CTR<0.3% at spend>$50 OR CPC>4x benchmark. English only."
        ),
        "build": _creative_input,
        "max_tokens": 1000,
    },
    "script_writer": {
        "system": (
            "You write Meta Ads creative scripts. Return ONLY a JSON object with EXACTLY these keys: "
            "hook (string, 0-3s), body (string), cta (string), "
            "composition (description|narrative|reasoning), "
            "cluster (curiosity|problem|result|authority|social_proof|transformation|offer), "
            "theme (string), rationale (string). "
            "Avoid cliches, active voice, one idea per sentence. English only."
        ),
        "build": _script_input,
        "max_tokens": 1100,
    },
    "ad_setting": {
        "system": (
            "You are a seasoned Meta Ads media buyer diagnosing from a screenshot/description. "
            "Observable only — never invent. Never confuse TOF attention with business results. "
            "Return ONLY a JSON object with EXACTLY these keys: "
            "simpleDiagnosis (string), evidence (string[]), confidence (LOW|MEDIUM|HIGH), "
            "falsePositiveNote (string or null), nextAction (string, one concrete step), "
            "rawExtraction (object of key->value). English only."
        ),
        "build": _diag_input,
        "max_tokens": 900,
    },
}

_HUMANIZE_SYS = (
    "Rewrite ad copy to remove signs of AI writing: no em-dash overuse, no words like "
    "delve/pivotal/tapestry/vibrant/realm/testament, no filler (just/really/basically), "
    "no hedging, no promotional fluff. Active voice, concrete, keep meaning and length. "
    "Return ONLY JSON with keys hook, body, cta."
)


async def _humanize(script: dict, model: str) -> dict:
    payload = json.dumps({k: script.get(k, "") for k in ("hook", "body", "cta")})
    cleaned = await _llm_json(_HUMANIZE_SYS, "Humanize this ad copy:\n" + payload, model, max_tokens=900)
    for k in ("hook", "body", "cta"):
        if isinstance(cleaned.get(k), str) and cleaned[k].strip():
            script[k] = cleaned[k]
    script["_humanized"] = True
    return script


# ───────────────────────── endpoints ─────────────────────────

@router.get("/agents")
async def list_agents():
    return [{"id": k, "name": v["name"], "model": v["model"]} for k, v in AGENTS.items()]


@router.get("/memory/ping")
async def memory_ping():
    return await mem.ping()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    agent = AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {req.agent_id}")

    if req.agent_id == "orchestrator":
        # Cross-agent: semantic-relevant first (RLS exposes ALL agents), then recent.
        relevant = await mem.search(req.account_id, "orchestrator", req.message, scope="long", limit=8)
        recent = await mem.recall(req.account_id, "orchestrator", limit=12)
        seen: set[int] = set()
        notes: list[str] = []
        for h in relevant:
            seen.add(h["id"])
            notes.append(f"[{h['agent_id']}] (rel {float(h['score']):.2f}) {str(h['content'])[:280]}")
        for h in reversed(recent):
            if h["id"] in seen:
                continue
            notes.append(f"[{h['agent_id']}] {str(h['content'])[:200]}")
        ctx = "\n".join(notes) if notes else "(no agent memory yet for this account)"
        system = agent["system"] + "\n\nShared memory across all agents for this account (relevant first):\n" + ctx
        msgs = [{"role": "user", "content": req.message}]
    else:
        history = await mem.recall(req.account_id, req.agent_id, scope="short", limit=10)
        msgs = []
        for h in reversed(history):
            c = str(h["content"])
            if c.startswith("ASSISTANT: "):
                msgs.append({"role": "assistant", "content": _strip("ASSISTANT: ", c)})
            elif c.startswith("USER: "):
                msgs.append({"role": "user", "content": _strip("USER: ", c)})
        msgs.append({"role": "user", "content": req.message})
        # Inject this agent's own relevant long-term findings (semantic recall).
        facts = await mem.search(req.account_id, req.agent_id, req.message, scope="long", limit=4)
        if facts:
            block = "\n".join(f"- {str(f['content'])[:240]}" for f in facts)
            system = agent["system"] + "\n\nRelevant long-term memory (your prior findings):\n" + block
        else:
            system = agent["system"]

    try:
        resp = await call_llm(msgs, system=system, model=agent["model"], max_tokens=800)
        reply = resp["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        log.exception("llm call failed")
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    await mem.remember(req.account_id, req.agent_id, f"USER: {req.message}", kind="msg", scope="short", ad_account_id=req.ad_account_id)
    await mem.remember(req.account_id, req.agent_id, f"ASSISTANT: {reply}", kind="msg", scope="short", ad_account_id=req.ad_account_id)
    return ChatResponse(agent_id=req.agent_id, name=agent["name"], model=agent["model"], reply=reply)


@router.post("/run")
async def run(req: RunRequest):
    agent = AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {req.agent_id}")
    spec = TASKS.get(req.agent_id)
    if not spec:
        raise HTTPException(status_code=400, detail=f"agent {req.agent_id} has no structured task (use /chat)")

    user = spec["build"](req.input)
    result = await _llm_json(spec["system"], user, agent["model"], max_tokens=spec["max_tokens"])

    # Script Writer: run the humanize pass over the generated copy.
    if req.agent_id == "script_writer" and "_unparsed" not in result:
        try:
            result = await _humanize(result, agent["model"])
        except Exception:  # noqa: BLE001
            log.warning("humanize step failed; returning raw script")

    # Persist structured result to LONG-TERM memory (orchestrator can recall it).
    try:
        mid = await mem.remember(
            req.account_id, req.agent_id, json.dumps(result)[:2000],
            kind="result", scope="long", ad_account_id=req.ad_account_id,
            meta={"task": req.agent_id},
        )
    except Exception:  # noqa: BLE001
        mid = None

    return {"agent_id": req.agent_id, "name": agent["name"], "model": agent["model"],
            "memory_id": mid, "result": result}


@router.post("/note")
async def note(req: NoteRequest):
    """Write-gate: an agent proposes a durable fact; only worthy notes reach long-term."""
    if req.agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {req.agent_id}")
    ok, reason = _worth_long(req.content)
    if not ok:
        return {"stored": False, "reason": reason}
    mid = await mem.remember(
        req.account_id, req.agent_id, req.content,
        kind="fact", scope="long", ad_account_id=req.ad_account_id, meta={"via": "note"},
    )
    return {"stored": True, "memory_id": mid, "reason": reason}


@router.post("/memory/search")
async def memory_search(req: SearchRequest):
    """Semantic recall probe. orchestrator → searches all agents in the account."""
    if req.agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {req.agent_id}")
    rows = await mem.search(req.account_id, req.agent_id, req.query, scope="long", limit=req.limit)
    return {"results": [
        {"id": r["id"], "agent_id": r["agent_id"], "kind": r["kind"],
         "score": round(float(r["score"]), 3), "content": str(r["content"])[:220]}
        for r in rows
    ]}


# ── Meta Ads read (token forwarded per-workspace from the app proxy) ───────────

class MetaReadRequest(BaseModel):
    account_id: Optional[str] = None       # workspace id (injected by the proxy)
    tool: str
    ad_account_id: Optional[str] = None    # Meta act_<id> (for account-scoped tools)
    meta_token: Optional[str] = None       # decrypted workspace Meta token
    args: dict[str, Any] = {}


_META_READ = {
    "list_ad_accounts": meta_tools.list_ad_accounts,
    "get_insights": meta_tools.get_insights,
    "list_campaigns": meta_tools.list_campaigns,
    "list_adsets": meta_tools.list_adsets,
    "list_ads": meta_tools.list_ads,
    "list_pixels": meta_tools.list_pixels,
    "search_interests": meta_tools.search_interests,
}


@router.post("/meta")
async def meta(req: MetaReadRequest):
    """Read-only Meta Ads tools. Uses the workspace's own token (reused OAuth
    connection). Write tools stay dry-run + approval-gated elsewhere."""
    fn = _META_READ.get(req.tool)
    if not fn:
        raise HTTPException(status_code=400, detail=f"unsupported meta tool: {req.tool}")
    if not req.meta_token and os.environ.get("META_MOCK", "0").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=409, detail="no active Meta connection for this workspace")
    try:
        if req.tool == "list_ad_accounts":
            data = await fn(token=req.meta_token)
        elif req.tool == "search_interests":
            data = await fn(req.args.get("query", ""), token=req.meta_token)
        else:
            acct = req.ad_account_id or req.account_id
            data = await fn(acct, token=req.meta_token, **req.args)
        return {"tool": req.tool, "count": len(data) if isinstance(data, list) else None, "data": data}
    except Exception as e:  # noqa: BLE001
        log.exception("meta tool failed")
        raise HTTPException(status_code=502, detail=f"meta error: {e}")


# ── Campaign Architect: goal → blueprint → DRY-RUN plan (for approval) ─────────

class CampaignPlanRequest(BaseModel):
    account_id: Optional[str] = None      # workspace id
    ad_account_id: Optional[str] = None   # Meta act_<id>
    goal: str
    budget_cents: Optional[int] = None
    pixel_id: Optional[str] = None
    countries: Optional[list[str]] = None
    niche: Optional[str] = None


@router.post("/campaign/plan")
async def campaign_plan(req: CampaignPlanRequest):
    """Design a launchable campaign blueprint + the ordered DRY-RUN proposals.
    Nothing is created — each proposal needs per-action approval to execute."""
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="goal is required")
    bp = await meta_architect.design_blueprint(
        req.goal, budget_cents=req.budget_cents, pixel_id=req.pixel_id,
        countries=req.countries, niche=req.niche,
    )
    if "_unparsed" in bp or "objective" not in bp:
        raise HTTPException(status_code=502, detail="architect could not produce a valid blueprint")
    plan = await meta_architect.build_dry_run_plan(
        bp, req.ad_account_id or "{{ad_account_id}}", pixel_id=req.pixel_id,
    )
    try:
        camp_name = bp.get("campaign", {}).get("name", "campaign")
        await mem.remember(
            req.account_id or "_global", "ad_setting",
            f"PLAN [{bp.get('objective')}] '{camp_name}' — {len(plan)} actions. goal: {req.goal[:140]}",
            kind="plan", scope="long", ad_account_id=req.ad_account_id,
            meta={"objective": bp.get("objective")},
        )
    except Exception:  # noqa: BLE001
        pass
    return {"blueprint": bp, "plan": plan, "approval_required": True,
            "note": "DRY-RUN — nothing created. Approve each action to execute (needs ads_management)."}
