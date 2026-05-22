"""db-guardrails — layer 2 for Django.

Refuses destructive management commands against the real database unless
ALLOW_DESTRUCTIVE=true is set. Covers `flush`, `sqlflush`, `reset_db`
(django-extensions) and `migrate <app> zero` (un-applies every migration for
an app, dropping its tables).

Install:
  1. Place this file at the project root, next to `manage.py` (that directory
     is on sys.path, so `import db_guardrails` resolves).
  2. Add to the TOP of `settings.py`:

         import db_guardrails
         db_guardrails.guard()

`manage.py test` is always allowed — Django builds a throwaway test database
for it and never touches the real one.
"""

from __future__ import annotations

import os
import sys

DESTRUCTIVE_COMMANDS = frozenset({"flush", "sqlflush", "reset_db"})


def guard(argv: list[str] | None = None) -> None:
    """Abort the process if argv invokes a destructive management command."""
    argv = list(sys.argv if argv is None else argv)

    if os.environ.get("ALLOW_DESTRUCTIVE") == "true":
        return

    # `manage.py test` runs against an isolated, throwaway test database.
    if "test" in argv:
        return

    tokens = set(argv)
    hits = DESTRUCTIVE_COMMANDS & tokens

    # `migrate <app> zero` reverses every migration for an app.
    if "migrate" in tokens and "zero" in tokens:
        hits.add("migrate ... zero")

    if hits:
        sys.exit(
            "BLOCKED by db-guardrails: destructive management command "
            f"({', '.join(sorted(hits))}). Set ALLOW_DESTRUCTIVE=true to override."
        )
