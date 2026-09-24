#!/usr/bin/env python3
"""Read-only status report (plan section 4.12). Never writes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402


def _read_optional(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = cm_common.make_parser(prog="status.py", description="Read-only status report.")
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = cm_common.resolve_root(args.root)

    ledger = _read_optional(root / "ledger.json")
    inventory = _read_optional(root / "inventory.json")
    registry_lock = _read_optional(root / "registry.lock.json")
    net_lock = _read_optional(root / "net.lock.json")
    shims = _read_optional(root / "runs" / "shims.json")
    probe = _read_optional(root / "runs" / "sandbox_probe.json")

    # `units` is the inventory's unit count -- the denominator for "N of
    # units converged" -- not how many units the ledger happens to have
    # touched yet (a unit can be fully discovered and still have no ledger
    # entry at all until something first sets its state).
    units: object = "absent"
    eligible: object = "absent"
    ineligible: object = "absent"
    if inventory is not None:
        inv_units = inventory.get("units", {})
        units = len(inv_units)
        eligible = sum(1 for u in inv_units.values() if u.get("eligible"))
        ineligible = sum(1 for u in inv_units.values() if not u.get("eligible"))

    in_ledger: object = "absent"
    by_state: dict = {}
    if ledger is not None:
        ledger_units = ledger.get("units", {})
        in_ledger = len(ledger_units)
        for entry in ledger_units.values():
            state = entry.get("state", "unknown")
            by_state[state] = by_state.get(state, 0) + 1

    netted: object = "absent"
    if net_lock is not None:
        netted = len(net_lock.get("units", {}))

    frozen_rows: object = "absent"
    if registry_lock is not None:
        frozen_rows = len(registry_lock.get("rows", {}))

    shims_n: object = "absent"
    ports_n: object = "absent"
    if shims is not None:
        shims_n = len(shims.get("shims", {}))
        ports_n = len(shims.get("ports", []))

    probe_result: object = "absent"
    probe_codex_version: object = "absent"
    if probe is not None:
        probe_result = probe.get("result", "absent")
        probe_codex_version = probe.get("codex_version", "absent")

    result = {
        "ok": True,
        "units": units,
        "in_ledger": in_ledger,
        "by_state": by_state,
        "eligible": eligible,
        "ineligible": ineligible,
        "netted": netted,
        "frozen_rows": frozen_rows,
        "shims": shims_n,
        "ports": ports_n,
        "probe": probe_result,
        "probe_codex_version": probe_codex_version,
    }

    print("codebase-migrator status", file=sys.stderr)
    print(f"  units: {units}  in_ledger: {in_ledger}", file=sys.stderr)
    for state in sorted(by_state):
        print(f"    {state}: {by_state[state]}", file=sys.stderr)
    print(f"  eligible: {eligible}  ineligible: {ineligible}", file=sys.stderr)
    print(f"  netted: {netted}  frozen_rows: {frozen_rows}", file=sys.stderr)
    print(f"  shims: {shims_n}  ports: {ports_n}", file=sys.stderr)
    print(f"  probe: {probe_result} ({probe_codex_version})", file=sys.stderr)

    cm_common.emit(result)
    return cm_common.EXIT_OK


if __name__ == "__main__":
    cm_common.run_main(main)
