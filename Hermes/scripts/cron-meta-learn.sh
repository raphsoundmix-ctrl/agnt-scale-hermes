#!/usr/bin/env bash
# Install the daily continuous-learning tick for the AGNT Meta watcher.
# Idempotent: replaces any existing agent/meta/learn cron line. Runs ON the server.
set -e

TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'agent/meta/learn' > "$TMP" || true
cat >> "$TMP" <<'CRON'
# AGNT Meta continuous-learning tick — daily 09:00 (version check + drift summary)
0 9 * * * bash -lc 'TOK=$(grep "^HERMES_INTERNAL_TOKEN=" $HOME/Container2/Hermes/.env | cut -d= -f2); curl -s -X POST -H "X-Internal-Token: $TOK" http://localhost:7778/agent/meta/learn >/dev/null 2>&1'
CRON
crontab "$TMP"
rm -f "$TMP"
echo "installed AGNT meta-learn cron:"
crontab -l | grep 'agent/meta/learn'
