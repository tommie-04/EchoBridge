#!/usr/bin/env bash
# One-click launcher for EchoBridge Web Dashboard and API
set -e
cd "$(dirname "$0")"

PYTHON_BIN="python3"
if [ -f "$HOME/.local/share/uv/tools/nanobot-ai/bin/python" ]; then
    PYTHON_BIN="$HOME/.local/share/uv/tools/nanobot-ai/bin/python"
fi

echo "Starting EchoBridge Hub using $PYTHON_BIN..."
exec "$PYTHON_BIN" web_server.py "$@"
