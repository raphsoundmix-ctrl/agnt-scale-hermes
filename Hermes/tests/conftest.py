"""Make `services.*` importable when pytest runs from the repo root.

Tests cover ONLY the deterministic core (pure functions, payload builders,
contracts) — no DB, no network, no API keys. That is deliberate: the modules
under test are the ones that touch money, so they must be runnable anywhere.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
