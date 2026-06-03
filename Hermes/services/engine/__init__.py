"""AGNT SCALE decision engine — Python port (server-side, for agents).

Source of truth = the TypeScript engine in the app repo (`src/lib/engine/`),
which runs on Vercel for the UI. This Python port runs inside Hermes so the
server-side agents can compute the same deterministic verdicts WITHOUT calling
Vercel — keeping the server self-sufficient. Each module mirrors its .ts twin
1:1; parity is asserted in `_parity.py` against the same cases as the TS tests.
"""
