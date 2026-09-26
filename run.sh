#!/usr/bin/env bash
# WorkFlowOS Launcher Script

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================================="
echo "⚡ Starting WorkFlowOS Dashboard Server..."
echo "=========================================================="

if [ -d ".venv" ]; then
    PYTHON_BIN=".venv/bin/python"
    UVICORN_BIN=".venv/bin/uvicorn"
else
    PYTHON_BIN="python3"
    UVICORN_BIN="uvicorn"
fi

echo "Access the Dashboard at: http://127.0.0.1:8000"
echo "Press Ctrl+C to stop the server."
echo "=========================================================="

exec "$UVICORN_BIN" backend.api.main:app --host 127.0.0.1 --port 8000 --reload
