#!/bin/bash
# ASX Ex-Dividend Recovery Pattern Analyzer — one-command launcher (macOS/Linux)
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ (or: brew install python)"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run — creating virtual environment and installing dependencies (1-2 min)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

echo ""
echo "  Starting ASX Ex-Dividend Recovery Pattern Analyzer…"
echo "  Open http://127.0.0.1:8477 in your browser (Ctrl+C to stop)."
echo ""
( sleep 2 && { open "http://127.0.0.1:8477" 2>/dev/null || xdg-open "http://127.0.0.1:8477" 2>/dev/null || true; } ) &
cd backend
exec ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8477
