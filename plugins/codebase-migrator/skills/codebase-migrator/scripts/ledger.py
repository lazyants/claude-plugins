#!/usr/bin/env python3
"""Per-unit ledger: state, cache key, R0 eligibility (plan section 4.11).

Importable: `cache_key(root, cfg, unit) -> dict`, `eligible(root, cfg, unit) -> list[str]`.
Keep this module importable with no side effects at import time: `diff_gate.py` (C) and
`unit_gate.py`/`sandbox.py` (D) import `cache_key` and `eligible`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402

CONVENTIONS_SENTINEL = "CHOOSE_CONVENTIONS"
STATES = {
    "pending",
    "drafted",
    "converged",
    "stale",
    "stale_by_convention",
    "blocked",
    "escalated",
    "ineligible",
}
_REVIEW_ROUND_RE = re.compile(r"^review\.r(\d+)\.json$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_ledger(root: Path) -> dict:
    path = Path(root) / "ledger.json"
    if not path.is_file():
        return {"schema": 1, "units": {}}
    return cm_common.read_json(path, "ledger.json")


def _write_ledger(root: Path, ledger: dict) -> None:
    cm_common.atomic_write_json(Path(root) / "ledger.json", ledger)


def _read_registry_lock(root: Path) -> dict:
    path = Path(root) / "registry.lock.json"
    if not path.is_file():
        return {"schema": 1, "rows": {}}
    return cm_common.read_json(path, "registry.lock.json")


def _read_net_lock(root: Path) -> dict:
    path = Path(root) / "net.lock.json"
    if not path.is_file():
        return {"schema": 1, "units": {}}
    return cm_common.read_json(path, "net.lock.json")


def _is_shim(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_line = f.readline().rstrip("\n")
    except OSError:
        return False
    return first_line.startswith("# codebase-migrator: shim for ")


def _inventory_stale_units(root: Path, cfg: dict, inventory: dict, closure: list) -> list:
    """Units in `closure` whose live legacy bytes no longer match the
    inventory's recorded `source_sha256` (or that the inventory does not
    mention at all)."""
    stale = []
    units_info = inventory.get("units", {})
    for u in closure:
        info = units_info.get(u)
        if info is None:
            stale.append(u)
            continue
        f = cm_common.legacy_file(root, cfg, u)
        actual = cm_common.sha256_file(f) if f.is_file() else cm_common.sha256_bytes(b"")
        if actual != info.get("source_sha256"):
            stale.append(u)
    return stale


def cache_key(root: Path, cfg: dict, unit: str) -> dict:
    root = Path(root)
    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    closure = cm_common.unit_closure(inventory, unit)
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]

    legacy_digests = cm_common.closure_digests(legacy_root, cfg["legacy_package"], closure)
    legacy_closure_sha256 = cm_common.sha256_json(legacy_digests)

    unit_info = inventory.get("units", {}).get(unit, {})
    needed_symbols = set(unit_info.get("public_symbols", [])) | set(unit_info.get("imported_symbols", []))
    lock_rows = _read_registry_lock(root).get("rows", {})
    rows = {s: lock_rows[s]["row"] for s in needed_symbols if s in lock_rows}
    rows_sha256 = cm_common.sha256_json(rows)

    conventions_path = root / "conventions.md"
    conventions_sha256 = (
        cm_common.sha256_file(conventions_path) if conventions_path.is_file() else cm_common.sha256_bytes(b"")
    )

    exceptions_path = root / "exceptions.json"
    exceptions_cases = {}
    if exceptions_path.is_file():
        exceptions = cm_common.read_json(exceptions_path, "exceptions.json")
        prefix = f"{unit}/"
        exceptions_cases = {
            k: v for k, v in exceptions.get("cases", {}).items() if k.startswith(prefix)
        }
    fidelity_sha256 = cm_common.sha256_json(
        {"fidelity_policy": cfg.get("fidelity_policy"), "exceptions": exceptions_cases}
    )

    net_lock_entry = _read_net_lock(root).get("units", {}).get(unit)
    net_sha256 = net_lock_entry.get("net_sha256") if net_lock_entry else None
    cases_sha256 = net_lock_entry.get("cases_sha256") if net_lock_entry else None

    templates_dir = cm_common.plugin_root() / "skills" / "codebase-migrator" / "assets" / "templates"
    template_digests = {}
    for name in ("port_TASK.md", "fix_TASK.md", "review_TASK.md", "cases_TASK.md"):
        p = templates_dir / name
        template_digests[name] = cm_common.sha256_file(p) if p.is_file() else cm_common.sha256_bytes(b"")
    templates_sha256 = cm_common.sha256_json(template_digests)

    scripts_dir = cm_common.plugin_root() / "skills" / "codebase-migrator" / "scripts"
    script_digests = {p.name: cm_common.sha256_file(p) for p in sorted(scripts_dir.glob("*.py"))}
    plugin_sha256 = cm_common.sha256_json(script_digests)

    return {
        "legacy_closure_sha256": legacy_closure_sha256,
        "rows_sha256": rows_sha256,
        "conventions_sha256": conventions_sha256,
        "fidelity_sha256": fidelity_sha256,
        "net_sha256": net_sha256,
        "cases_sha256": cases_sha256,
        "templates_sha256": templates_sha256,
        "adapter": "python-python/1",
        "plugin_sha256": plugin_sha256,
    }


def eligible(root: Path, cfg: dict, unit: str) -> list:
    """R0. Returns the list of unmet reasons; empty means eligible."""
    root = Path(root)
    reasons = []

    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    units_info = inventory.get("units", {})
    unit_info = units_info.get(unit)
    if unit_info is None:
        return [f"unit not in inventory: {unit}"]

    closure = cm_common.unit_closure(inventory, unit)
    stale = _inventory_stale_units(root, cfg, inventory, closure)
    if stale:
        # Nothing else here is trustworthy while the inventory describes old
        # source (the static verdict, the symbol lists): report only this.
        return [
            "inventory is stale for " + ", ".join(sorted(stale)) + ": re-run inventory.py"
        ]

    if not unit_info.get("eligible", False):
        reasons.append(
            f"{unit} is not statically eligible: "
            + ", ".join(unit_info.get("ineligible_reasons", []))
        )

    lock_rows = _read_registry_lock(root).get("rows", {})
    needed_symbols = list(unit_info.get("public_symbols", [])) + list(
        unit_info.get("imported_symbols", [])
    )
    missing_rows = sorted(s for s in needed_symbols if s not in lock_rows)
    if missing_rows:
        reasons.append("missing frozen row(s): " + ", ".join(missing_rows))

    non_one_to_one_unported = []
    for s in unit_info.get("imported_symbols", []):
        row_entry = lock_rows.get(s)
        if row_entry is None:
            continue
        owning_unit = s.split(":", 1)[0]
        target_path = cm_common.target_file(root, cfg, owning_unit)
        is_ported = target_path.is_file() and not _is_shim(target_path)
        if not is_ported and row_entry["row"].get("cardinality") != "one_to_one":
            non_one_to_one_unported.append(s)
    if non_one_to_one_unported:
        reasons.append(
            "imported symbol(s) not one_to_one while their unit is unported: "
            + ", ".join(sorted(non_one_to_one_unported))
        )

    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    net_lock = _read_net_lock(root)
    net_entry = net_lock.get("units", {}).get(unit)
    if net_entry is None:
        reasons.append(f"no net recorded for {unit}: run net_capture.py")
    else:
        if not net_entry.get("deterministic", False):
            reasons.append(f"{unit} net is not deterministic")
        if net_entry.get("stateful", True):
            reasons.append(f"{unit} net is stateful")
        floor = cfg.get("coverage_floor_pct")
        coverage_pct = net_entry.get("coverage_pct", -1)
        if isinstance(floor, int) and coverage_pct < floor:
            reasons.append(f"{unit} coverage {coverage_pct} is below the floor {floor}")

        nets_path = root / "nets" / f"{unit}.json"
        nets_data = None
        if nets_path.is_file():
            nets_data = cm_common.read_json(nets_path, f"nets/{unit}.json")
            if cm_common.sha256_file(nets_path) != net_entry.get("net_sha256"):
                reasons.append(f"nets/{unit}.json does not match net.lock.json: re-run net_capture.py")
        else:
            reasons.append(f"nets/{unit}.json is missing: re-run net_capture.py")

        cases_path = root / "cases" / f"{unit}.json"
        if cases_path.is_file():
            if cm_common.sha256_file(cases_path) != net_entry.get("cases_sha256"):
                reasons.append(
                    f"cases/{unit}.json changed since capture: re-run net_capture.py"
                )
        else:
            reasons.append(f"cases/{unit}.json is missing: re-run net_capture.py")

        live_digests = cm_common.closure_digests(legacy_root, cfg["legacy_package"], closure)
        live_closure_sha256 = cm_common.sha256_json(live_digests)
        if live_closure_sha256 != net_entry.get("legacy_closure_sha256"):
            recorded_closure = (nets_data or {}).get("legacy_closure", {})
            changed_units = sorted(
                u
                for u in set(recorded_closure) | set(live_digests)
                if recorded_closure.get(u) != live_digests.get(u)
            )
            if not changed_units:
                changed_units = closure
            reasons.append(
                "legacy closure has drifted for "
                + ", ".join(changed_units)
                + f" since {unit}'s net was captured: "
                "re-run inventory.py, ledger.py accept-drift or net_capture.py"
            )

    conventions_path = root / "conventions.md"
    if conventions_path.is_file():
        if CONVENTIONS_SENTINEL in conventions_path.read_text(encoding="utf-8"):
            reasons.append(f"conventions.md still contains {CONVENTIONS_SENTINEL}")
    else:
        reasons.append("conventions.md is missing")

    return reasons


def _highest_review_round(run_dir: Path):
    best = None
    if not run_dir.is_dir():
        return None
    for p in run_dir.glob("review.r*.json"):
        m = _REVIEW_ROUND_RE.match(p.name)
        if m:
            n = int(m.group(1))
            if best is None or n > best:
                best = n
    return best


def _digest_is_a_finding(run_dir: Path, digest: str) -> bool:
    if not run_dir.is_dir():
        return False
    for p in sorted(run_dir.glob("review.r*.json")):
        review = cm_common.read_json(p, p.name)
        if any(f.get("digest") == digest for f in review.get("findings", [])):
            return True
    return False


def _digest_is_admitted(run_dir: Path, digest: str) -> bool:
    if not run_dir.is_dir():
        return False
    for p in sorted(run_dir.glob("admitted.r*.json")):
        admitted = cm_common.read_json(p, p.name)
        if any(f.get("digest") == digest for f in admitted.get("findings", [])):
            return True
    return False


def _digest_is_refused(run_dir: Path, digest: str) -> bool:
    refusals_path = run_dir / "refusals.json"
    if not refusals_path.is_file():
        return False
    refusals = cm_common.read_json(refusals_path, "refusals.json")
    return any(r.get("digest") == digest for r in refusals.get("refusals", []))


def cmd_key(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    key = cache_key(root, cfg, unit)
    key_sha256 = cm_common.sha256_json(key)
    cm_common.emit({"ok": True, "unit": unit, "cache_key": key, "key_sha256": key_sha256})
    return cm_common.EXIT_OK


def cmd_eligible(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    reasons = eligible(root, cfg, unit)
    for r in reasons:
        print(r, file=sys.stderr)
    cm_common.emit({"ok": not reasons, "unit": unit, "reasons": reasons})
    return cm_common.EXIT_OK if not reasons else cm_common.EXIT_FAIL


def cmd_set(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    if args.state == "converged":
        cm_common.fail(
            "ledger.py set cannot set state converged directly; use `ledger.py converge`",
            cm_common.EXIT_CANNOT,
        )
    if args.state not in STATES:
        cm_common.fail(f"unknown ledger state: {args.state}", cm_common.EXIT_CANNOT)
    ledger = _read_ledger(root)
    entry = ledger["units"].get(unit, {})
    entry["state"] = args.state
    entry["reason"] = args.reason
    entry["updated"] = _now_iso()
    entry.setdefault("cache_key", None)
    entry.setdefault("key_sha256", None)
    ledger["units"][unit] = entry
    _write_ledger(root, ledger)
    cm_common.emit({"ok": True, "unit": unit, "state": args.state})
    return cm_common.EXIT_OK


def cmd_converge(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    reasons = eligible(root, cfg, unit)
    if reasons:
        cm_common.fail(
            "cannot converge, not eligible: " + "; ".join(reasons),
            cm_common.EXIT_FAIL,
            unit=unit,
            reasons=reasons,
        )

    target_path = cm_common.target_file(root, cfg, unit)
    if not target_path.is_file():
        cm_common.fail(f"cannot converge: no target file for {unit}", cm_common.EXIT_FAIL, unit=unit)
    current_target_sha256 = cm_common.sha256_file(target_path)
    key = cache_key(root, cfg, unit)
    key_sha256 = cm_common.sha256_json(key)

    run_dir = cm_common.unit_run_dir(root, unit)
    for name in ("r2", "r3"):
        report_path = run_dir / f"{name}.json"
        if not report_path.is_file():
            cm_common.fail(f"cannot converge: missing runs/{unit}/{name}.json", cm_common.EXIT_FAIL, unit=unit)
        report = cm_common.read_json(report_path, f"runs/{unit}/{name}.json")
        if not report.get("ok"):
            cm_common.fail(f"cannot converge: {name} is not ok", cm_common.EXIT_FAIL, unit=unit)
        if report.get("target_sha256") != current_target_sha256:
            cm_common.fail(
                f"cannot converge: {name} was recorded for a different target_sha256",
                cm_common.EXIT_FAIL,
                unit=unit,
            )
        if report.get("key_sha256") != key_sha256:
            cm_common.fail(
                f"cannot converge: {name} was recorded for a different cache key",
                cm_common.EXIT_FAIL,
                unit=unit,
            )

    round_n = _highest_review_round(run_dir)
    if round_n is None:
        cm_common.fail(f"cannot converge: no review found for {unit}", cm_common.EXIT_FAIL, unit=unit)
    review_path = run_dir / f"review.r{round_n}.json"
    review = cm_common.read_json(review_path, review_path.name)
    if review.get("target_sha256") != current_target_sha256:
        cm_common.fail(
            f"cannot converge: latest review was produced for a different target_sha256",
            cm_common.EXIT_FAIL,
            unit=unit,
        )
    if review.get("malformed"):
        cm_common.fail(
            f"cannot converge: latest review has malformed findings", cm_common.EXIT_FAIL, unit=unit
        )

    refusals_path = run_dir / "refusals.json"
    refusals = (
        cm_common.read_json(refusals_path, "refusals.json")
        if refusals_path.is_file()
        else {"schema": 1, "refusals": []}
    )
    refused_digests = {r.get("digest") for r in refusals.get("refusals", [])}
    unrefused = sorted(
        f.get("digest") for f in review.get("findings", []) if f.get("digest") not in refused_digests
    )
    if unrefused:
        cm_common.fail(
            "cannot converge: unrefused findings remain: " + ", ".join(unrefused),
            cm_common.EXIT_FAIL,
            unit=unit,
        )

    max_rounds = cfg.get("max_fix_rounds", 3)
    if round_n > max_rounds + 1:
        cm_common.fail(
            f"cannot converge: round {round_n} exceeds max_fix_rounds+1 ({max_rounds + 1})",
            cm_common.EXIT_FAIL,
            unit=unit,
        )

    ledger = _read_ledger(root)
    ledger["units"][unit] = {
        "state": "converged",
        "cache_key": key,
        "key_sha256": key_sha256,
        "updated": _now_iso(),
        "reason": None,
    }
    _write_ledger(root, ledger)
    cm_common.emit({"ok": True, "unit": unit, "state": "converged"})
    return cm_common.EXIT_OK


def cmd_classify(root: Path, cfg: dict, args) -> int:
    ledger = _read_ledger(root)
    transitions = []
    for unit, entry in ledger["units"].items():
        if entry.get("state") != "converged":
            continue
        old_key = entry.get("cache_key") or {}
        new_key = cache_key(root, cfg, unit)
        if old_key == new_key:
            continue
        changed = sorted(k for k in set(old_key) | set(new_key) if old_key.get(k) != new_key.get(k))
        new_state = "stale_by_convention" if changed == ["conventions_sha256"] else "stale"
        entry["state"] = new_state
        entry["reason"] = "changed: " + ", ".join(changed)
        entry["updated"] = _now_iso()
        transitions.append({"unit": unit, "state": new_state, "changed": changed})
    _write_ledger(root, ledger)
    cm_common.emit({"ok": True, "transitions": transitions})
    return cm_common.EXIT_OK


def cmd_refuse(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    run_dir = cm_common.unit_run_dir(root, unit)
    if not _digest_is_a_finding(run_dir, args.finding_digest):
        cm_common.fail(
            f"finding digest not found in any review: {args.finding_digest}",
            cm_common.EXIT_FAIL,
            unit=unit,
        )
    if _digest_is_admitted(run_dir, args.finding_digest):
        cm_common.fail(
            f"finding digest already admitted: {args.finding_digest}", cm_common.EXIT_FAIL, unit=unit
        )
    refusals_path = run_dir / "refusals.json"
    refusals = (
        cm_common.read_json(refusals_path, "refusals.json")
        if refusals_path.is_file()
        else {"schema": 1, "refusals": []}
    )
    refusals.setdefault("refusals", []).append(
        {"digest": args.finding_digest, "reason": args.reason, "at": _now_iso()}
    )
    cm_common.atomic_write_json(refusals_path, refusals)
    cm_common.emit({"ok": True, "unit": unit, "digest": args.finding_digest})
    return cm_common.EXIT_OK


def cmd_admit(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    run_dir = cm_common.unit_run_dir(root, unit)
    review_path = run_dir / f"review.r{args.round}.json"
    if not review_path.is_file():
        cm_common.fail(f"no such review: {review_path.name}", cm_common.EXIT_FAIL, unit=unit)
    review = cm_common.read_json(review_path, review_path.name)
    finding = next(
        (f for f in review.get("findings", []) if f.get("digest") == args.finding_digest), None
    )
    if finding is None:
        cm_common.fail(
            f"finding digest not found in {review_path.name}: {args.finding_digest}",
            cm_common.EXIT_FAIL,
            unit=unit,
        )
    if _digest_is_refused(run_dir, args.finding_digest):
        cm_common.fail(
            f"finding digest already refused: {args.finding_digest}", cm_common.EXIT_FAIL, unit=unit
        )
    admitted_path = run_dir / f"admitted.r{args.round}.json"
    admitted = (
        cm_common.read_json(admitted_path, admitted_path.name)
        if admitted_path.is_file()
        else {"schema": 1, "round": args.round, "findings": []}
    )
    if any(f.get("digest") == args.finding_digest for f in admitted.get("findings", [])):
        cm_common.fail(
            f"finding digest already admitted: {args.finding_digest}", cm_common.EXIT_FAIL, unit=unit
        )
    admitted.setdefault("findings", []).append(finding)
    cm_common.atomic_write_json(admitted_path, admitted)
    cm_common.emit({"ok": True, "unit": unit, "digest": args.finding_digest, "round": args.round})
    return cm_common.EXIT_OK


def cmd_pilot(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    pilot_path = root / "runs" / "pilot.json"
    if pilot_path.is_file():
        cm_common.fail(f"a pilot is already recorded: {pilot_path}", cm_common.EXIT_FAIL)

    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    unit_info = inventory.get("units", {}).get(unit, {})
    executable_lines = unit_info.get("executable_lines", [])
    imports_units = unit_info.get("imports_units", [])

    shims_path = root / "runs" / "shims.json"
    ported_units = set()
    if shims_path.is_file():
        shims = cm_common.read_json(shims_path, "shims.json")
        ported_units = set(shims.get("ports", []))
    unported_dependencies = sorted(u for u in imports_units if u not in ported_units)

    record = {
        "unit": unit,
        "reason": args.reason,
        "executable_lines": executable_lines,
        "imports_units": imports_units,
        "unported_dependencies": unported_dependencies,
    }
    cm_common.atomic_write_json(pilot_path, record)
    cm_common.emit({"ok": True, "unit": unit})
    return cm_common.EXIT_OK


def cmd_accept_drift(root: Path, cfg: dict, args) -> int:
    unit = cm_common.require_unit(root, args.unit)
    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    closure = cm_common.unit_closure(inventory, unit)
    stale = _inventory_stale_units(root, cfg, inventory, closure)
    if stale:
        cm_common.fail(
            "inventory is stale for "
            + ", ".join(sorted(stale))
            + ": re-run inventory.py before accept-drift",
            cm_common.EXIT_FAIL,
            unit=unit,
        )

    net_lock = _read_net_lock(root)
    net_entry = net_lock.get("units", {}).get(unit)
    old_closure_sha256 = net_entry.get("legacy_closure_sha256") if net_entry else None

    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    new_digests = cm_common.closure_digests(legacy_root, cfg["legacy_package"], closure)
    new_closure_sha256 = cm_common.sha256_json(new_digests)

    drift_log_path = root / "drift_log.json"
    drift_log = (
        cm_common.read_json(drift_log_path, "drift_log.json")
        if drift_log_path.is_file()
        else {"schema": 1, "entries": []}
    )
    drift_log.setdefault("entries", []).append(
        {
            "unit": unit,
            "old_closure_sha256": old_closure_sha256,
            "new_closure_sha256": new_closure_sha256,
            "operator": args.operator,
            "reason": args.reason,
            "at": _now_iso(),
        }
    )
    cm_common.atomic_write_json(drift_log_path, drift_log)

    if net_entry is not None:
        del net_lock["units"][unit]
        cm_common.atomic_write_json(root / "net.lock.json", net_lock)

    ledger = _read_ledger(root)
    entry = ledger["units"].get(unit, {})
    entry["state"] = "pending"
    entry["reason"] = args.reason
    entry["updated"] = _now_iso()
    entry.setdefault("cache_key", None)
    entry.setdefault("key_sha256", None)
    ledger["units"][unit] = entry
    _write_ledger(root, ledger)

    cm_common.emit(
        {
            "ok": True,
            "unit": unit,
            "old_closure_sha256": old_closure_sha256,
            "new_closure_sha256": new_closure_sha256,
        }
    )
    return cm_common.EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    # `--root` is defined on EVERY subparser (not just the top-level parser):
    # the pinned CLI shape puts the subcommand first (`ledger.py eligible
    # --root R --unit U`), and once argparse enters a subparser it only
    # recognizes that subparser's own arguments.
    root_parent = argparse.ArgumentParser(add_help=False)
    root_parent.add_argument("--root", required=True)

    # The top-level parser does NOT itself take --root: the pinned CLI shape
    # is `ledger.py <cmd> --root R ...`, and giving the top level a required
    # --root would force it to appear before the subcommand name instead.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("key", parents=[root_parent])
    p.add_argument("--unit", required=True)

    p = sub.add_parser("eligible", parents=[root_parent])
    p.add_argument("--unit", required=True)

    p = sub.add_parser("set", parents=[root_parent])
    p.add_argument("--unit", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--reason", default=None)

    p = sub.add_parser("converge", parents=[root_parent])
    p.add_argument("--unit", required=True)

    sub.add_parser("classify", parents=[root_parent])

    p = sub.add_parser("refuse", parents=[root_parent])
    p.add_argument("--unit", required=True)
    p.add_argument("--finding-digest", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("admit", parents=[root_parent])
    p.add_argument("--unit", required=True)
    p.add_argument("--round", required=True, type=int)
    p.add_argument("--finding-digest", required=True)

    p = sub.add_parser("pilot", parents=[root_parent])
    p.add_argument("--unit", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("accept-drift", parents=[root_parent])
    p.add_argument("--unit", required=True)
    p.add_argument("--operator", required=True)
    p.add_argument("--reason", required=True)

    return parser


_HANDLERS = {
    "key": cmd_key,
    "eligible": cmd_eligible,
    "set": cmd_set,
    "converge": cmd_converge,
    "classify": cmd_classify,
    "refuse": cmd_refuse,
    "admit": cmd_admit,
    "pilot": cmd_pilot,
    "accept-drift": cmd_accept_drift,
}


def main() -> int:
    args = _build_parser().parse_args()
    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    handler = _HANDLERS[args.cmd]
    return handler(root, cfg, args)


if __name__ == "__main__":
    cm_common.run_main(main)
