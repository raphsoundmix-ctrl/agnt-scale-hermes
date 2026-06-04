"""AGNT SCALE — Meta Ads service (Marketing API / Graph API).

API-first spine for the auto-campaign agents (ADR Р-32). Read tools work today;
write tools default to DRY-RUN (return the exact payload + estimate, execute nothing)
and only run live once `ads_management` access + per-action approval are in place.

Layout:
  client.py     — async Graph API client (httpx), token + version resolution, mock mode
  knowledge.py  — Meta domain model + primer (objectives/optimization/pixel/CAPI/…)
  tools.py      — read functions + write payload builders (dry-run by default)
  mock.py       — fixtures so the whole surface is testable without a live token
"""
from __future__ import annotations
