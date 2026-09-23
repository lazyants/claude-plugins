#!/usr/bin/env python3
"""In-process strangler bridge (plan section 4.5).

For every unit with a frozen one_to_one row and no port yet, writes a thin
shim that re-exports the legacy symbol under its target name, so a dependent
unit can be ported against the target package before its dependency is.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cm_common  # noqa: E402

_SHIM_PREFIX = "# codebase-migrator: shim for "
_PACKAGE_MARKER = "# codebase-migrator: package marker\n"


def _unit_of(source: str) -> str:
    return source.split(":", 1)[0]


def _first_line(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.readline().rstrip("\n")
    except OSError:
        return None


def is_shim(path: Path, unit: str) -> bool:
    return _first_line(path) == f"{_SHIM_PREFIX}{unit}"


def _ensure_init_chain(target_root: Path, unit_dir: Path) -> None:
    rel = unit_dir.relative_to(target_root)
    current = target_root
    for part in rel.parts:
        current = current / part
        current.mkdir(parents=True, exist_ok=True)
        init_path = current / "__init__.py"
        if not init_path.exists():
            cm_common.atomic_write_text(init_path, _PACKAGE_MARKER)


def build_bridge(root: Path, cfg: dict, check: bool) -> dict:
    paths = cm_common.resolved_paths(root, cfg)
    target_root = paths["target_root"]
    lock = cm_common.read_json(root / "registry.lock.json", "registry.lock.json")
    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")

    frozen_by_unit: dict[str, list[dict]] = {}
    for source, entry in lock.get("rows", {}).items():
        frozen_by_unit.setdefault(_unit_of(source), []).append(entry["row"])
    active_units = set(frozen_by_unit)

    # A shim exists so an ACTIVE unit (one with frozen rows, so the pipeline
    # is currently working on it) can import a not-yet-ported dependency
    # through the target package. A unit with no active consumer has nobody
    # trying to import it that way yet, so it gets no shim even if its own
    # rows are frozen (plan 5.4's crossed_shims scenario and the W3->W4 pilot
    # walkthrough in plan 7 both turn on this: the pilot's own unit is never
    # shimmed for itself, only its unported dependencies are).
    depended_on_by_active: set[str] = set()
    for unit in active_units:
        row = inventory.get("units", {}).get(unit)
        if not row:
            continue
        for dep in row.get("imports_units", []):
            if dep != unit:
                depended_on_by_active.add(dep)

    shims: dict[str, dict] = {}
    ports: list[str] = []
    skipped_non_one_to_one: list[str] = []

    for unit in sorted(frozen_by_unit):
        if unit not in depended_on_by_active:
            continue
        rows = frozen_by_unit[unit]
        one_to_one_rows = sorted(
            (r for r in rows if r["cardinality"] == "one_to_one"), key=lambda r: r["source"]
        )
        skipped_non_one_to_one.extend(
            sorted(r["source"] for r in rows if r["cardinality"] != "one_to_one")
        )
        if not one_to_one_rows:
            continue

        target_path = cm_common.target_file(root, cfg, unit)
        if target_path.exists() and not is_shim(target_path, unit):
            ports.append(unit)
            continue

        symbols = [row["entry"] for row in one_to_one_rows]
        if not check:
            lines = [f"{_SHIM_PREFIX}{unit}"]
            for row in one_to_one_rows:
                legacy_module, legacy_name = row["source"].split(":", 1)
                _, target_name = row["entry"].split(":", 1)
                lines.append(f"from {legacy_module} import {legacy_name} as {target_name}")
            _ensure_init_chain(target_root, target_path.parent)
            cm_common.atomic_write_text(target_path, "\n".join(lines) + "\n")
        shims[unit] = {
            "file": target_path.relative_to(target_root).as_posix(),
            "symbols": symbols,
        }

    census = {
        "schema": 1,
        "shims": shims,
        "ports": sorted(ports),
    }
    if not check:
        (root / "runs").mkdir(parents=True, exist_ok=True)
        cm_common.atomic_write_json(root / "runs" / "shims.json", census)
    return {
        "shims": len(shims),
        "ports": len(ports),
        "skipped_non_one_to_one": sorted(skipped_non_one_to_one),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    result = build_bridge(root, cfg, args.check)
    cm_common.emit({"ok": True, **result})
    return cm_common.EXIT_OK


if __name__ == "__main__":
    cm_common.run_main(main)
