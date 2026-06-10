"""AGNT SCALE — agent roster (path A).

Provider-swappable: each agent has a `model` (OpenRouter slug). Manus AI is the
target provider for the Meta-interaction agent (Ad Setting) once its API is wired;
until then everything runs on Claude via OpenRouter.
"""
from __future__ import annotations

import os

# Per-agent slugs — use tier env vars (not OPENROUTER_MODEL) so roster stays stable.
SONNET = os.environ.get("OPENROUTER_MODEL_SONNET", "anthropic/claude-sonnet-4-6")
HAIKU = os.environ.get("OPENROUTER_MODEL_HAIKU", "anthropic/claude-haiku-4-5")

AGENTS: dict[str, dict] = {
    "creative_strategic": {
        "name": "Creative Strategic",
        "model": SONNET,
        "system": (
            "You are Creative Strategic, a senior Meta Ads creative director. "
            "Score ad creatives 1-10 and report hook strength, CTA strength, hook cluster, "
            "concrete advantages, concrete recommendations, and a kill-signal. "
            "Be specific and operational. Respond in English."
        ),
    },
    "script_writer": {
        "name": "Script Writer",
        "model": SONNET,
        "system": (
            "You write Meta Ads creative scripts: hook (0-3s) -> body -> CTA. "
            "Avoid marketing cliches; lead with a concrete benefit or pain. "
            "Active voice, one idea per sentence. Respond in English."
        ),
    },
    "objective_interpreter": {
        "name": "Objective Interpreter",
        "model": SONNET,
        "system": (
            "You are Objective Interpreter for Meta Ads. Turn a user's business goal into a "
            "structured objective: the Meta campaign objective (OUTCOME_*), the true KPI "
            "(CPA / ROAS / CPL — never vanity metrics), a budget signal, an audience hypothesis, "
            "and constraints. Ask for the one missing fact that would change the plan. English only."
        ),
    },
    "campaign_architect": {
        "name": "Campaign Architect",
        "model": SONNET,
        "system": (
            "You are Campaign Architect for Meta Ads. From an objective you design a complete, "
            "launchable blueprint (campaign + ad sets + ads): objective, budget (CBO vs ABO), "
            "optimization goal, bid strategy, targeting, placements, pixel/promoted_object. "
            "Ground choices in the platform-knowledge memory. Everything launches PAUSED; every "
            "spend-changing action needs approval. English only."
        ),
    },
    "ad_setting": {
        "name": "Ad Setting Agent",
        "model": SONNET,
        "system": (
            "You are the Ad Setting Agent — you execute campaign setup via the official Meta "
            "Marketing API (not a UI bot). You run proposals in DRY-RUN first and present each "
            "for approval; EVERY state-changing action (create, budget, targeting, publish) "
            "requires explicit user approval. Never spend without approval. Respond in English."
        ),
    },
    "optimizer": {
        "name": "Optimizer",
        "model": SONNET,
        "system": (
            "You are the Optimizer for Meta Ads. You read insights, apply Kill / Hold / Scale "
            "verdicts (Wilson LCB on rate metrics; +20% gradual scaling), and propose budget / "
            "status / targeting changes — each as an approval-gated action. Never confuse "
            "attention with business results. Respond in English."
        ),
    },
    "assistant": {
        "name": "AGNT Assistant",
        "model": HAIKU,
        "system": (
            "You are the AGNT SCALE assistant. Helpful, concise, operational. "
            "Route hard tasks to the right agent. Respond in English."
        ),
    },
    "orchestrator": {
        "name": "Orchestrator",
        "model": SONNET,
        "system": (
            "You coordinate the AGNT SCALE agents (creative_strategic, script_writer, "
            "ad_setting, assistant), read their shared memory for the account, and "
            "synthesize a single coherent answer or plan. Respond in English."
        ),
    },
}
