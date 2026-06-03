"""AGNT SCALE — agent roster (path A).

Provider-swappable: each agent has a `model` (OpenRouter slug). Manus AI is the
target provider for the Meta-interaction agent (Ad Setting) once its API is wired;
until then everything runs on Claude via OpenRouter.
"""
from __future__ import annotations

import os

# Defaults (override per agent). OPENROUTER_MODEL env wins as the house model.
SONNET = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
HAIKU = "anthropic/claude-3.5-haiku"

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
    "ad_setting": {
        "name": "Ad Setting Agent",
        "model": SONNET,
        "system": (
            "You diagnose Meta Ads from data or screenshots and plan campaign setup. "
            "You may operate the Ads Manager via a visible browser, but EVERY state-changing "
            "action (budget, targeting, publish) requires explicit user approval first. "
            "Never spend without approval. Respond in English."
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
        "model": HAIKU,
        "system": (
            "You coordinate the AGNT SCALE agents (creative_strategic, script_writer, "
            "ad_setting, assistant), read their shared memory for the account, and "
            "synthesize a single coherent answer or plan. Respond in English."
        ),
    },
}
