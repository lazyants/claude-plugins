#!/usr/bin/env python3
"""Validate `migration.json` against the v0.1 schema (plan section 2.3).

Importable: `validate(cfg, root) -> list[problem]`, `QUESTIONNAIRE`.
CLI: `migration_validate.py --root R`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402

# key -> (question, cost of the choice)
QUESTIONNAIRE = {
    "source_stack": (
        "What is the source stack?",
        "Fixes the adapter used for the whole migration; only `python` exists in v0.1.",
    ),
    "target_stack": (
        "What is the target stack?",
        "Fixes the adapter used for the whole migration; only `python` exists in v0.1.",
    ),
    "legacy_root": (
        "Where does the legacy codebase live?",
        "Every unit id and every path in the plugin is derived from this root.",
    ),
    "legacy_package": (
        "What is the top-level legacy package name?",
        "Units are discovered under `legacy_root/<legacy_package>` only.",
    ),
    "target_root": (
        "Where should the ported codebase be written?",
        "Must not equal, sit inside, or contain `legacy_root`; the pipeline creates and "
        "grows it over time, so it need not be empty.",
    ),
    "target_package": (
        "What is the top-level target package name?",
        "Must differ from `legacy_package`: both are imported side by side through the same-runtime seam.",
    ),
    "fidelity_policy": (
        "Must the port reproduce legacy defects?",
        "Hashed: changing it later restales every converged unit. `bug_for_bug_with_exceptions` "
        "requires `exceptions.json`.",
    ),
    "seam": (
        "What seam joins legacy and target code?",
        "Only `in_process` exists in v0.1; other seams are a later adapter.",
    ),
    "unit_granularity": (
        "What is a migration unit?",
        "Only `file` exists in v0.1; a different granularity is a later adapter.",
    ),
    "naming_policy": (
        "Keep identifiers, or re-idiomatise them?",
        "Freezes into the registry.",
    ),
    "net_source": (
        "Where do golden-master cases come from?",
        "Only `generated_golden_master` exists in v0.1; importing existing tests or recorded "
        "traffic is deferred.",
    ),
    "dead_code_policy": (
        "Port unreferenced code, or drop it with a census?",
        "Otherwise every review re-litigates it.",
    ),
    "coverage_floor_pct": (
        "Minimum executed-line coverage of each legacy unit before it may be ported.",
        "Higher = more cases turns before a unit is eligible; lower = a weaker net.",
    ),
}

_ENUMS = {
    "source_stack": {"python"},
    "target_stack": {"python"},
    "fidelity_policy": {"bug_for_bug", "bug_for_bug_with_exceptions"},
    "seam": {"in_process"},
    "unit_granularity": {"file"},
    "naming_policy": {"preserve", "re_idiomatise"},
    "net_source": {"generated_golden_master"},
    "dead_code_policy": {"port", "drop_with_census"},
}

_KNOWN_KEYS = {
    "schema",
    "source_stack",
    "target_stack",
    "legacy_root",
    "legacy_package",
    "target_root",
    "target_package",
    "fidelity_policy",
    "seam",
    "unit_granularity",
    "naming_policy",
    "net_source",
    "dead_code_policy",
    "coverage_floor_pct",
    "max_fix_rounds",
    "codex_bin",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _problem(key: str, kind: str, message: str) -> dict:
    return {"key": key, "kind": kind, "message": message}


def validate(cfg: dict, root: "Path") -> list:
    """Return a list of problems for `cfg` (already-parsed `migration.json`).
    `root` is `cfg`'s directory, used to resolve relative paths."""
    root = Path(root)
    problems = []

    for key, value in cfg.items():
        if key not in _KNOWN_KEYS:
            problems.append(_problem(key, "unknown_key", f"unknown key: {key}"))

    unanswered = set()
    for key in QUESTIONNAIRE:
        value = cfg.get(key)
        if isinstance(value, str) and value.startswith("CHOOSE_"):
            unanswered.add(key)
            problems.append(_problem(key, "unanswered", QUESTIONNAIRE[key][0]))

    if cfg.get("schema") != 1:
        problems.append(_problem("schema", "invalid", "schema must be 1"))

    for key, allowed in _ENUMS.items():
        if key in unanswered:
            continue
        value = cfg.get(key)
        if value is not None and value not in allowed:
            problems.append(
                _problem(key, "unsupported", f"{value!r} is not a v0.1 value for {key}")
            )

    if "coverage_floor_pct" not in unanswered:
        value = cfg.get("coverage_floor_pct")
        if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= 100):
            problems.append(
                _problem("coverage_floor_pct", "invalid", "coverage_floor_pct must be an int 0-100")
            )

    if "max_fix_rounds" in cfg:
        value = cfg.get("max_fix_rounds")
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 10):
            problems.append(
                _problem("max_fix_rounds", "invalid", "max_fix_rounds must be an int 1-10")
            )

    if "codex_bin" in cfg and "codex_bin" not in unanswered:
        value = cfg.get("codex_bin")
        if not isinstance(value, str) or not value:
            problems.append(_problem("codex_bin", "invalid", "codex_bin must be a non-empty string"))

    legacy_package = cfg.get("legacy_package")
    target_package = cfg.get("target_package")
    for key, value in (("legacy_package", legacy_package), ("target_package", target_package)):
        if key in unanswered:
            continue
        if not isinstance(value, str) or not _IDENTIFIER_RE.match(value or ""):
            problems.append(
                _problem(key, "invalid", f"{key} must be a single valid Python identifier")
            )

    if (
        "legacy_package" not in unanswered
        and "target_package" not in unanswered
        and isinstance(legacy_package, str)
        and isinstance(target_package, str)
        and legacy_package == target_package
    ):
        problems.append(
            _problem(
                "target_package",
                "invalid",
                "target_package must differ from legacy_package (same-runtime seam)",
            )
        )

    legacy_root_value = cfg.get("legacy_root")
    legacy_root_path = None
    if "legacy_root" not in unanswered:
        if isinstance(legacy_root_value, str):
            legacy_root_path = Path(legacy_root_value)
            if not legacy_root_path.is_absolute():
                legacy_root_path = root / legacy_root_path
            if not legacy_root_path.is_dir():
                problems.append(
                    _problem("legacy_root", "invalid", f"legacy_root is not a directory: {legacy_root_value}")
                )
                legacy_root_path = None
        else:
            problems.append(_problem("legacy_root", "invalid", "legacy_root must be a string path"))

    if legacy_root_path is not None and "legacy_package" not in unanswered and isinstance(legacy_package, str):
        pkg_dir = legacy_root_path / legacy_package
        init_file = pkg_dir / "__init__.py"
        if not pkg_dir.is_dir() or not init_file.is_file():
            problems.append(
                _problem(
                    "legacy_package",
                    "invalid",
                    f"legacy_root/{legacy_package} must be a directory with __init__.py",
                )
            )

    # Whether target_root already exists is NOT checked here: the pipeline
    # itself creates and grows target_root over time (bridge.py's shims, a
    # promoted port), and this config is re-validated on every downstream
    # script's `load_config()` call, not only once at intake. A permanent
    # rule that fires the moment the pipeline's own first write lands would
    # refuse every script from then on. Only the structural invariant
    # (no overlap with legacy_root) is checked, and it is checked every
    # time regardless of existence.
    target_root_value = cfg.get("target_root")
    if "target_root" not in unanswered:
        if isinstance(target_root_value, str):
            target_root_path = Path(target_root_value)
            if not target_root_path.is_absolute():
                target_root_path = root / target_root_path
            if target_root_path.exists() and not target_root_path.is_dir():
                problems.append(
                    _problem(
                        "target_root", "invalid", f"target_root exists and is not a directory: {target_root_value}"
                    )
                )
            if legacy_root_path is not None:
                try:
                    target_root_path.resolve().relative_to(legacy_root_path.resolve())
                    inside = True
                except ValueError:
                    inside = False
                try:
                    legacy_root_path.resolve().relative_to(target_root_path.resolve())
                    contains = True
                except ValueError:
                    contains = False
                if inside or contains or target_root_path.resolve() == legacy_root_path.resolve():
                    problems.append(
                        _problem(
                            "target_root",
                            "invalid",
                            "target_root must not equal or be inside legacy_root, nor contain it",
                        )
                    )
        else:
            problems.append(_problem("target_root", "invalid", "target_root must be a string path"))

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.read_json(root / "migration.json", "migration.json")
    problems = validate(cfg, root)
    unanswered = sorted(p["key"] for p in problems if p["kind"] == "unanswered")

    if unanswered:
        print("The following migration.json keys are unanswered:", file=sys.stderr)
        for key in unanswered:
            question, cost = QUESTIONNAIRE[key]
            print(f"  {key}: {question}", file=sys.stderr)
            print(f"    cost: {cost}", file=sys.stderr)

    for p in problems:
        if p["kind"] != "unanswered":
            print(f"{p['key']} ({p['kind']}): {p['message']}", file=sys.stderr)

    ok = not problems
    cm_common.emit({"ok": ok, "unanswered": unanswered, "problems": problems})
    return cm_common.EXIT_OK if ok else cm_common.EXIT_FAIL


if __name__ == "__main__":
    cm_common.run_main(main)
