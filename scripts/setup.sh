#!/usr/bin/env bash
# One-time setup for a fresh clone: venv, dependencies, .env, pre-commit hooks.
# Afterwards fill in .env and run scripts/pilot-up.sh to start the pilot.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== checking Python 3.11-3.13"
PYTHON=""
for v in python3.13 python3.12 python3.11 python3; do
  if command -v "$v" >/dev/null && "$v" -c 'import sys; sys.exit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)'; then
    PYTHON="$v"; break
  fi
done
[[ -n "$PYTHON" ]] || { echo "ERROR: no Python 3.11-3.13 found (https://www.python.org/downloads/)"; exit 1; }
echo "   using $PYTHON ($($PYTHON --version))"

echo "== creating .venv"
[[ -d .venv ]] || "$PYTHON" -m venv .venv
source .venv/bin/activate

echo "== installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e '.[dev]'

echo "== creating .env"
if [[ -f .env ]]; then
  echo "   .env already exists, leaving it alone"
else
  cp .env.example .env
  echo "   created from .env.example -- you MUST fill in the Jira (and model) values"
fi

echo "== installing pre-commit hooks"
python -m pre_commit install || echo "   (skipped; run 'pre-commit install' later, needs network on first run)"

if grep -qE '^SCRUM_AGENT_DATABASE_URL=..' .env; then
  echo "== starting Postgres"
  if ! command -v docker >/dev/null; then
    echo "ERROR: docker not found (install Docker Desktop, then re-run scripts/setup.sh)"; exit 1
  fi
  if ! nc -z 127.0.0.1 5432 2>/dev/null; then
    docker compose up -d
    until nc -z 127.0.0.1 5432 2>/dev/null; do sleep 1; done
  else
    echo "   already running on 5432"
  fi

  echo "== applying migrations"
  python -m scrum_agent migrate

  echo "== loading base data (first run reads every issue, ~4 min)"
  if grep -qE '^SCRUM_AGENT_JIRA_(SITE|API_TOKEN)=..' .env; then
    python -m scrum_agent collect
  else
    echo "   SKIPPED: .env has no Jira credentials yet."
    echo "   Fill in .env, then re-run scripts/setup.sh (or just scripts/pilot-up.sh)."
  fi
else
  echo
  echo "== database steps SKIPPED: SCRUM_AGENT_DATABASE_URL missing from .env."
  echo "   Copy the line from .env.example, then re-run scripts/setup.sh."
fi

echo
echo "Setup done. Next steps:"
if grep -qE '^SCRUM_AGENT_JIRA_(SITE|API_TOKEN)=..' .env && grep -qE '^SCRUM_AGENT_DATABASE_URL=..' .env; then
  echo "  scripts/pilot-up.sh             # starts everything, serves the UI"
  echo "  open http://127.0.0.1:8741"
else
  echo "  1. Fill in .env (Jira site/email/token/cloud_id/board/project + model key)"
  echo "  2. scripts/pilot-up.sh          # starts everything, serves the UI"
  echo "  3. open http://127.0.0.1:8741"
fi
