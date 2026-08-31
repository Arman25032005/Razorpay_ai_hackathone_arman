#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

./venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (demo mode — no keys required)"
fi

echo "Starting RecoverAI on http://localhost:8000 ..."
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
