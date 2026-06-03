#!/bin/bash
# Hermes Gateway — startup script for WSL2
# Usage: ./start.sh [--reload]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install / update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Start server
RELOAD_FLAG=""
if [[ "$1" == "--reload" ]]; then
    RELOAD_FLAG="--reload"
    echo "Starting Hermes Gateway in DEV mode (auto-reload)..."
else
    echo "Starting Hermes Gateway..."
fi

uvicorn main:app \
    --host 0.0.0.0 \
    --port 7777 \
    $RELOAD_FLAG \
    --log-level info
