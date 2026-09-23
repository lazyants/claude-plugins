#!/usr/bin/env python3
"""W3 symbol registry validation and freeze (plan section 4.4).

`registry.json` maps every legacy public symbol to where it lands in the
target package. Freezing a row is a promise the rest of the pipeline
(bridge.py, net_capture.py, ledger.py) relies on: a frozen row never
changes except through --correct, which is an audited, explicit act.
"""
from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cm_common  # noqa: E402

_CARDINALITIES = {"one_to_one", "split", "merge", "dropped"}
_FROZEN_MISMATCH = "frozen row differs from registry.json; raise it with --correct, never edit a frozen row"


def _unit_of(source: str) -> str:
    return source.split(":", 1)[0]


def _read_optional(path: Path, what: str, default: dict) -> dict:
    if not path.exists():
        return default
    return cm_common.read_json(path, what)


def row_problems(rows: list[dict], inventory: dict, cfg: dict) -> list[dict]:
    """Every per-row and cross-row rule in plan 4.4, each problem naming its source."""
    problems: list[dict] = []

    all_public: set[str] = set()
    for unit_row in inventory["units"].values():
        all_public.update(unit_row["public_symbols"])
    unreferenced = set(inventory["unreferenced_public"])

    source_positions: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        source = row.get("source")
        source_positions.setdefault(source, []).append(idx)

    for idx, row in enumerate(rows):
        source = row.get("source")
        cardinality = row.get("cardinality")
        entry = row.get("entry")
        targets = row.get("targets") or []
        reason = row.get("reason")

        if source is None or source not in all_public:
            problems.append({"source": source, "message": "source is not a public symbol in the inventory"})
            continue
        if cardinality not in _CARDINALITIES:
            problems.append({"source": source, "message": f"unknown cardinality {cardinality!r}"})
            continue

        unit = _unit_of(source)

        if cardinality == "dropped":
            if entry is not None:
                problems.append({"source": source, "message": "a dropped row must have entry: null"})
            if targets:
                problems.append({"source": source, "message": "a dropped row must have empty targets"})
            if not reason:
                problems.append({"source": source, "message": "a dropped row needs a non-empty reason"})
            if cfg.get("dead_code_policy") != "drop_with_census":
                problems.append({"source": source, "message": "dropping a symbol requires dead_code_policy: drop_with_census"})
            if source not in unreferenced:
                problems.append({"source": source, "message": "source is referenced by another unit's imported_symbols and can never be dropped"})
            continue

        expected_module = cm_common.target_module(cfg, unit)
        if entry is None:
            problems.append({"source": source, "message": "a non-dropped row needs a non-null entry"})
        elif entry not in targets:
            problems.append({"source": source, "message": "entry must be one of targets"})
        for target in targets:
            target_module = target.split(":", 1)[0]
            if target_module != expected_module:
                problems.append({"source": source, "message": f"target {target} is not in module {expected_module}"})
        if cardinality == "one_to_one" and len(targets) != 1:
            problems.append({"source": source, "message": "one_to_one needs exactly one target"})
        if cardinality == "split" and len(targets) < 2:
            problems.append({"source": source, "message": "split needs at least two targets"})
        if cardinality == "merge" and len(targets) != 1:
            problems.append({"source": source, "message": "merge needs exactly one target"})

    for source, idxs in source_positions.items():
        if source is not None and len(idxs) > 1:
            problems.append({"source": source, "message": "source appears in more than one row"})

    target_rows: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        for target in row.get("targets") or []:
            target_rows.setdefault(target, []).append(idx)
    for target, idxs in target_rows.items():
        if len(idxs) < 2:
            continue
        for idx in idxs:
            row = rows[idx]
            if row.get("cardinality") != "merge":
                problems.append({"source": row.get("source"), "message": f"target {target} is shared by multiple rows, so this row must be merge"})
    for idx, row in enumerate(rows):
        if row.get("cardinality") != "merge":
            continue
        targets = row.get("targets") or []
        if len(targets) != 1:
            continue
        sharers = target_rows.get(targets[0], [])
        if len(sharers) < 2:
            problems.append({"source": row.get("source"), "message": f"merge target {targets[0]} must be shared by at least one other merge row"})

    return problems


def completeness_problems(rows: list[dict], inventory: dict, units_scope: list[str], with_imported: bool) -> list[dict]:
    row_sources = {row.get("source") for row in rows}
    required: set[str] = set()
    for unit in units_scope:
        required.update(inventory["units"][unit]["public_symbols"])
    if with_imported:
        for unit in units_scope:
            required.update(inventory["units"][unit]["imported_symbols"])
    return [
        {"source": source, "message": "missing a registry row"}
        for source in sorted(required)
        if source not in row_sources
    ]


def frozen_mismatch_problems(rows: list[dict], lock: dict) -> list[dict]:
    row_by_source = {row.get("source"): row for row in rows if row.get("source") is not None}
    problems = []
    for source, entry in lock.get("rows", {}).items():
        current = row_by_source.get(source)
        if current is None or cm_common.sha256_json(current) != entry["digest"]:
            problems.append({"source": source, "message": _FROZEN_MISMATCH})
    return problems


def do_correct(root: Path, rows: list[dict], args: argparse.Namespace) -> int:
    if not args.expect_digest or not args.reason:
        cm_common.fail("--correct requires both --expect-digest and --reason", cm_common.EXIT_CANNOT)
    lock_path = root / "registry.lock.json"
    lock = _read_optional(lock_path, "registry.lock.json", {"schema": 1, "rows": {}})
    source = args.correct
    existing = lock["rows"].get(source)
    if existing is None:
        cm_common.fail(f"{source} is not a frozen row", cm_common.EXIT_FAIL, source=source)
    if existing["digest"] != args.expect_digest:
        cm_common.fail(f"{source}: --expect-digest does not match the current lock entry", cm_common.EXIT_FAIL, source=source)
    row_by_source = {row.get("source"): row for row in rows if row.get("source") is not None}
    new_row = row_by_source.get(source)
    if new_row is None:
        cm_common.fail(f"{source} has no current row in registry.json", cm_common.EXIT_FAIL, source=source)
    old_row = existing["row"]
    lock["rows"][source] = {"row": new_row, "digest": cm_common.sha256_json(new_row)}
    cm_common.atomic_write_json(lock_path, lock)

    corrections_path = root / "registry.corrections.json"
    corrections = _read_optional(corrections_path, "registry.corrections.json", {"schema": 1, "corrections": []})
    corrections.setdefault("corrections", []).append({
        "source": source,
        "old": old_row,
        "new": new_row,
        "reason": args.reason,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    cm_common.atomic_write_json(corrections_path, corrections)

    cm_common.emit({"ok": True, "rows": len(rows), "frozen": len(lock["rows"]), "problems": []})
    return cm_common.EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--units")
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--with-imported", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--correct")
    parser.add_argument("--expect-digest")
    parser.add_argument("--reason")
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    registry = cm_common.read_json(root / "registry.json", "registry.json")
    rows = registry.get("rows", [])

    if args.correct:
        return do_correct(root, rows, args)

    if args.with_imported and not args.units:
        cm_common.fail("--with-imported requires --units", cm_common.EXIT_CANNOT)
    if not args.units and not args.all:
        cm_common.fail("one of --units or --all is required", cm_common.EXIT_CANNOT)

    if args.all:
        units_scope = sorted(unit for unit, row in inventory["units"].items() if row["eligible"])
    else:
        units_scope = [u.strip() for u in args.units.split(",") if u.strip()]
        for unit in units_scope:
            cm_common.require_unit(root, unit)

    lock_path = root / "registry.lock.json"
    lock = _read_optional(lock_path, "registry.lock.json", {"schema": 1, "rows": {}})

    problems = row_problems(rows, inventory, cfg)
    problems += completeness_problems(rows, inventory, units_scope, args.with_imported)
    problems += frozen_mismatch_problems(rows, lock)

    frozen_count = len(lock["rows"])
    if args.freeze and not problems:
        row_by_source = {row.get("source"): row for row in rows if row.get("source") is not None}
        checked_sources: set[str] = set()
        for unit in units_scope:
            checked_sources.update(inventory["units"][unit]["public_symbols"])
        if args.with_imported:
            for unit in units_scope:
                checked_sources.update(inventory["units"][unit]["imported_symbols"])
        for source in sorted(checked_sources):
            row = row_by_source[source]
            digest = cm_common.sha256_json(row)
            existing = lock["rows"].get(source)
            if existing is not None and existing["digest"] != digest:
                problems.append({"source": source, "message": _FROZEN_MISMATCH})
                continue
            lock["rows"][source] = {"row": row, "digest": digest}
        if not problems:
            cm_common.atomic_write_json(lock_path, lock)
            frozen_count = len(lock["rows"])

    ok = len(problems) == 0
    cm_common.emit({"ok": ok, "rows": len(rows), "frozen": frozen_count, "problems": problems})
    return cm_common.EXIT_OK if ok else cm_common.EXIT_FAIL


if __name__ == "__main__":
    cm_common.run_main(main)
