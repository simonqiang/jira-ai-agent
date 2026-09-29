# scrum-agent

Scrum Master Jira assistant (personal pilot). Python 3.11+, src layout — code in `src/scrum_agent/`, tests in `tests/`. Setup and CLI usage live in README.md.

## Commands

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest                          # full suite; no live Jira needed (uses tests/agent_fakes.py)
ruff check . && ruff format --check .
python -m scrum_agent           # CLI probe; needs live Jira config
```

## Conventions

- Ruff: line-length 100, py311 target, rules `E,F,I,UP,B` (see pyproject.toml).
- pytest: asyncio_mode=auto (async tests need no decorator), quiet by default.
- Agent runtime is Google ADK (`google-adk`); chat/baseline/LLM code lives in `src/scrum_agent/agent/`.
- Tests must not hit live Jira — fake the tool layer (see `tests/agent_fakes.py`).
