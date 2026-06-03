#!/usr/bin/env bash
# AGNT SCALE — server self-update from GitHub. Runs ON the server, no PC needed.
# Pulls latest agnt-scale-hermes, syncs code into the live stack dir, rebuilds.
# Does NOT touch .env and does NOT run DB migrations (run those manually —
# some are destructive, e.g. 004 rebuilds the embedding column).
set -euo pipefail

REPO="$HOME/agnt-scale-hermes-git"
DEST="$HOME/Container2"

echo "[deploy] pull…"
git -C "$REPO" pull --ff-only

echo "[deploy] sync code → $DEST (keeping .env)…"
rsync -a --delete \
  --exclude='.env' --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  "$REPO/Hermes/" "$DEST/Hermes/"
cp "$REPO/docker-compose.yml" "$DEST/docker-compose.yml"

echo "[deploy] rebuild hermes…"
cd "$DEST"
docker compose up -d --build hermes

sleep 6
echo "[deploy] health: $(curl -s --max-time 10 localhost:7778/health || echo FAILED)"
echo "[deploy] engine parity: $(docker exec agnt-hermes python -m services.engine._parity 2>&1 | tail -1)"
echo "[deploy] done @ $(git -C "$REPO" rev-parse --short HEAD)"
