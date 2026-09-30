"""Postgres connection and SQL-file migration runner (Week 4).

Migrations are numbered ``.sql`` files shipped in ``scrum_agent/migrations/``;
the runner applies each once, in a single transaction, and records the
filename in ``public.schema_migrations``. The table lives in ``public``
because the ``scrum_agent`` schema itself is created by ``001_init.sql``.
"""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

from scrum_agent.config import Settings

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def connect(settings: Settings) -> psycopg.Connection:
    """Open a sync connection pinned to UTC (all timestamps are UTC).

    Autocommit: every snapshot/event/checkpoint write is durable the moment it
    executes, so an interrupted run leaves resumable partial state and only the
    checkpoint marks success. run_migrations() still wraps each file in an
    explicit transaction (a real one, now that implicit transactions cannot
    silently swallow it).
    """
    return psycopg.connect(
        settings.database_url or "",
        row_factory=dict_row,
        options="-c timezone=UTC",
        autocommit=True,
    )


def migration_names() -> list[str]:
    """Sorted names of the SQL migrations shipped with the package."""
    root = resources.files("scrum_agent").joinpath("migrations")
    return sorted(item.name for item in root.iterdir() if item.name.endswith(".sql"))


def run_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations in filename order; returns the applied names."""
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(_MIGRATIONS_TABLE)
        cur.execute("SELECT filename FROM public.schema_migrations")
        done = {row["filename"] for row in cur.fetchall()}
    for name in migration_names():
        if name in done:
            continue
        sql = (
            resources.files("scrum_agent")
            .joinpath("migrations")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
        # One transaction per file: the migration and its bookkeeping row
        # commit together or not at all.
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO public.schema_migrations (filename) VALUES (%s)", (name,))
        applied.append(name)
    return applied
