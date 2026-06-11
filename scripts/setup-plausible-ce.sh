#!/usr/bin/env bash
# AGNT SCALE — optional Plausible Community Edition (separate stack).
# Does NOT merge into agnt-postgres. Internal-only by default (127.0.0.1:7780).
# Requires ~2GB RAM for ClickHouse — skip on small VPS shared with MAO.
set -euo pipefail

DEST="${PLAUSIBLE_CE_DIR:-$HOME/Container2/plausible-ce}"
TAG="${PLAUSIBLE_CE_TAG:-v3.2.0}"
BASE_URL="${PLAUSIBLE_BASE_URL:-http://127.0.0.1:7780}"
HTTP_PORT=80

if [[ ! -d "$DEST/.git" ]]; then
  git clone -b "$TAG" --single-branch https://github.com/plausible/community-edition "$DEST"
fi

cd "$DEST"
if [[ ! -f .env ]]; then
  echo "BASE_URL=$BASE_URL" > .env
  echo "SECRET_KEY_BASE=$(openssl rand -base64 48)" >> .env
  echo "HTTP_PORT=$HTTP_PORT" >> .env
fi

cat > compose.override.yml <<EOF
services:
  plausible:
    ports:
      - "127.0.0.1:7780:${HTTP_PORT}"
EOF

echo "[plausible-ce] ready in $DEST"
echo "  Start:  cd $DEST && docker compose up -d"
echo "  UI:     $BASE_URL (localhost only)"
echo "  Hermes: PLAUSIBLE_API_URL=$BASE_URL PLAUSIBLE_API_KEY=<from Plausible settings>"
