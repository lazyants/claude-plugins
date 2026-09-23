#!/usr/bin/env python3
"""Create or resume a codebase-migrator durable root (plan section 2.1-2.2, 4.2)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402

MARKER_NAME = ".codebase-migrator-root.json"
CONVENTIONS_SENTINEL = "CHOOSE_CONVENTIONS"


def _check_legacy_root_overlap(resolved_root: Path, migration_path: Path) -> None:
    """Refuse if `migration.json` already names a real (non-placeholder)
    `legacy_root` that equals, contains, or is contained by `resolved_root`.
    A no-op while `legacy_root` is still a `CHOOSE_` placeholder."""
    try:
        cfg = json.loads(migration_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(cfg, dict):
        return
    legacy_root_value = cfg.get("legacy_root")
    if not isinstance(legacy_root_value, str) or legacy_root_value.startswith("CHOOSE_"):
        return
    legacy_root_path = Path(legacy_root_value)
    if not legacy_root_path.is_absolute():
        legacy_root_path = resolved_root / legacy_root_path
    try:
        legacy_resolved = legacy_root_path.resolve()
    except OSError:
        return
    overlap = legacy_resolved == resolved_root
    if not overlap:
        try:
            resolved_root.relative_to(legacy_resolved)
            overlap = True
        except ValueError:
            pass
    if not overlap:
        try:
            legacy_resolved.relative_to(resolved_root)
            overlap = True
        except ValueError:
            pass
    if overlap:
        cm_common.fail(
            f"root must not equal, contain, or be contained by legacy_root: {legacy_resolved}",
            cm_common.EXIT_CANNOT,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--adopt", action="store_true")
    args = parser.parse_args()

    root_arg_path = Path(args.root)
    if root_arg_path.exists() and not root_arg_path.is_dir():
        cm_common.fail(f"root exists and is not a directory: {args.root}", cm_common.EXIT_CANNOT)
    resolved = root_arg_path.resolve()

    if cm_common.under_temp_root(resolved):
        cm_common.fail(f"root must not be under a temp root: {resolved}", cm_common.EXIT_CANNOT)

    marker_path = resolved / MARKER_NAME
    exists = resolved.exists()
    entries = list(resolved.iterdir()) if exists and resolved.is_dir() else []

    if not exists or not entries:
        outcome = "fresh"
    elif marker_path.exists():
        marker = cm_common.read_json(marker_path, "ownership marker")
        if marker.get("schema") != 1:
            cm_common.fail(f"ownership marker has wrong schema: {marker_path}", cm_common.EXIT_CANNOT)
        outcome = "resumed"
    elif args.adopt:
        outcome = "adopted"
    else:
        cm_common.fail(
            f"root is non-empty with no ownership marker: {resolved} (use --adopt to adopt it)",
            cm_common.EXIT_FAIL,
        )

    resolved.mkdir(parents=True, exist_ok=True)
    for sub in ("cases", "nets", "runs"):
        (resolved / sub).mkdir(exist_ok=True)

    migration_path = resolved / "migration.json"
    _check_legacy_root_overlap(resolved, migration_path)

    created = False
    if outcome == "fresh":
        created = True
        example_path = (
            cm_common.plugin_root() / "skills" / "codebase-migrator" / "assets" / "migration.example.json"
        )
        if not migration_path.exists():
            migration_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")

        conventions_path = resolved / "conventions.md"
        if not conventions_path.exists():
            cm_common.atomic_write_text(
                conventions_path,
                "# Conventions\n\n"
                f"{CONVENTIONS_SENTINEL}\n\n"
                "Replace this sentinel with the target-idiom rulebook for this migration "
                "before any unit can be judged eligible (see `ledger.py eligible`).\n",
            )

        ledger_path = resolved / "ledger.json"
        if not ledger_path.exists():
            cm_common.atomic_write_json(ledger_path, {"schema": 1, "units": {}})

        registry_path = resolved / "registry.json"
        if not registry_path.exists():
            cm_common.atomic_write_json(registry_path, {"schema": 1, "rows": []})

    if outcome in ("fresh", "adopted"):
        marker = {
            "schema": 1,
            "project_id": uuid.uuid4().hex,
            "created": datetime.now(timezone.utc).isoformat(),
            "plugin_version": cm_common.PLUGIN_VERSION,
        }
        cm_common.atomic_write_json(marker_path, marker)

    unanswered = []
    result_ok = True
    exit_code = cm_common.EXIT_OK

    if outcome == "fresh":
        cfg = cm_common.read_json(migration_path, "migration.json")
        import migration_validate

        problems = migration_validate.validate(cfg, resolved)
        unanswered = sorted(p["key"] for p in problems if p["kind"] == "unanswered")
        if unanswered:
            print("migration.json created; the following keys need answers:", file=sys.stderr)
            for key in unanswered:
                question, cost = migration_validate.QUESTIONNAIRE[key]
                print(f"  {key}: {question}", file=sys.stderr)
                print(f"    cost: {cost}", file=sys.stderr)
            result_ok = False
            exit_code = cm_common.EXIT_FAIL

    cm_common.emit({"ok": result_ok, "outcome": outcome, "created": created, "unanswered": unanswered})
    return exit_code


if __name__ == "__main__":
    cm_common.run_main(main)
