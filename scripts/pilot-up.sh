#!/usr/bin/env bash
# Start the local pilot: Postgres (if needed), migrate, refresh data, preflight, serve.
# Usage: scripts/pilot-up.sh [--no-collect]   (Ctrl-C stops the server)
set -euo pipefail
cd "$(dirname "$0")/.."

source .venv/bin/activate

if ! nc -z 127.0.0.1 5432 2>/dev/null; then
  echo "== starting Postgres"
  docker compose up -d
  sleep 2
else
  echo "== Postgres already running on 5432"
fi

echo "== migrate"
PYTHONPATH=src python -m scrum_agent migrate

if [[ "${1:-}" != "--no-collect" ]]; then
  echo "== collect (refresh stored Jira data)"
  PYTHONPATH=src python -m scrum_agent collect
fi

echo "== preflight"
PYTHONPATH=src python -m scrum_agent preflight

echo "== serve (Ctrl-C to stop)"
exec env PYTHONPATH=src python -m scrum_agent serve
