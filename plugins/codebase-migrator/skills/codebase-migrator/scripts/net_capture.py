#!/usr/bin/env python3
"""W3b/W4c: golden-master capture and the dynamic half of eligibility (plan
section 4.7). Runs the legacy unit twice (environments `A` and `B`, see
`observe.py`) and refuses on any state change, any cross-environment
behavioural difference, or coverage below the floor; otherwise writes
`nets/<unit>.json` and records `net.lock.json`.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402
import observe  # noqa: E402

_BEHAVIOURAL_CHANNELS = (
    "status",
    "return",
    "error",
    "receiver_after",
    "args_after",
    "kwargs_after",
    "stdout",
    "stderr",
)


def compare_captures(a: list, b: list) -> list:
    """Case ids whose behavioural channels differ between two observation
    lists (typically environment `A` vs `B`). Never compares `covered_lines`,
    `route_files` or any path — those legitimately differ. A pure function of
    its two arguments, so it is unit-testable on synthetic data."""
    by_id_a = {o["case_id"]: o for o in a}
    by_id_b = {o["case_id"]: o for o in b}
    diffs = []
    for case_id in sorted(set(by_id_a) | set(by_id_b)):
        oa, ob = by_id_a.get(case_id), by_id_b.get(case_id)
        if oa is None or ob is None:
            diffs.append(case_id)
            continue
        for channel in _BEHAVIOURAL_CHANNELS:
            if oa.get(channel) != ob.get(channel):
                diffs.append(case_id)
                break
    return diffs


def _staged_legacy_file(root: Path, cfg: dict, stage_legacy_base: Path, unit: str) -> Path:
    live = cm_common.legacy_file(root, cfg, unit)
    live_base = cm_common.resolved_paths(root, cfg)["legacy_root"]
    return stage_legacy_base / live.relative_to(live_base)


def _read_registry_lock(root: Path) -> dict:
    path = root / "registry.lock.json"
    if not path.is_file():
        return {"schema": 1, "rows": {}}
    return cm_common.read_json(path, "registry.lock.json")


def _read_net_lock(root: Path) -> dict:
    path = root / "net.lock.json"
    if not path.is_file():
        return {"schema": 1, "units": {}}
    return cm_common.read_json(path, "net.lock.json")


def _dropped_symbols(lock_rows: dict) -> set:
    return {s for s, entry in lock_rows.items() if entry["row"].get("cardinality") == "dropped"}


def _validate_case_shape(case, index: int):
    if not isinstance(case, dict):
        return f"case #{index} is not an object"
    cid = case.get("id")
    if not isinstance(cid, str) or not cid:
        return f"case #{index} has no string id"
    call = case.get("call")
    if not isinstance(call, str) or not call:
        return f"case {cid}: no string call"
    for key in ("args", "init_args"):
        if key in case and not isinstance(case[key], list):
            return f"case {cid}: {key} must be a list"
    for key in ("kwargs", "init_kwargs"):
        if key in case and not isinstance(case[key], dict):
            return f"case {cid}: {key} must be an object"
    return None


def _call_source_symbol(unit: str, call_spec: str) -> str:
    head = call_spec.split(".", 1)[0]
    return f"{unit}:{head}"


def _symbol_span_lines(spans: dict, symbols) -> set:
    lines: set = set()
    for sym in symbols:
        span = spans.get(sym)
        if span:
            start, end = span
            lines.update(range(start, end + 1))
    return lines


def _scan_unsupported(value):
    if isinstance(value, dict):
        if "$unsupported" in value:
            return value["$unsupported"]
        for v in value.values():
            found = _scan_unsupported(v)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _scan_unsupported(v)
            if found:
                return found
    return None


def _find_unsupported(obs: dict):
    for channel in _BEHAVIOURAL_CHANNELS:
        found = _scan_unsupported(obs.get(channel))
        if found:
            return found
    return None


def _fail(root: Path, unit: str, msg: str, code: int, **fields) -> "NoReturn":
    """Like `cm_common.fail`, but first persists the exact same payload to
    `runs/<unit>/net_capture.json` — `sandbox.py`'s `cases` dispatch reads
    that file's `uncovered_lines` (default empty if absent), and a
    below-floor refusal is exactly the run whose `uncovered_lines` the next
    cases turn needs. Persisted on every run, refusals included."""
    payload = {"ok": False, "error": msg}
    payload.update(fields)
    cm_common.atomic_write_json(cm_common.unit_run_dir(root, unit) / "net_capture.json", payload)
    cm_common.fail(msg, code, **fields)


def run(root: Path, cfg: dict, unit: str) -> dict:
    inventory = cm_common.read_json(root / "inventory.json", "inventory.json")
    units_info = inventory.get("units", {})
    unit_info = units_info.get(unit)
    if unit_info is None:
        _fail(root, unit, f"unknown unit: {unit}", cm_common.EXIT_CANNOT)

    if not unit_info.get("eligible", False):
        _fail(root, unit,
            f"{unit} is not statically eligible: " + ", ".join(unit_info.get("ineligible_reasons", [])),
            cm_common.EXIT_FAIL,
        )

    lock = _read_registry_lock(root)
    lock_rows = lock.get("rows", {})
    dropped = _dropped_symbols(lock_rows)
    missing_rows = sorted(s for s in unit_info.get("public_symbols", []) if s not in lock_rows)
    if missing_rows:
        _fail(root, unit,
            f"{unit} has public symbol(s) with no frozen row: " + ", ".join(missing_rows),
            cm_common.EXIT_FAIL,
        )

    cases_path = root / "cases" / f"{unit}.json"
    if not cases_path.is_file():
        _fail(root, unit, f"cases/{unit}.json is missing", cm_common.EXIT_FAIL)
    cases_doc = cm_common.read_json(cases_path, f"cases/{unit}.json")
    all_cases = cases_doc.get("cases", [])
    if not all_cases:
        _fail(root, unit, f"cases/{unit}.json has zero cases", cm_common.EXIT_FAIL)

    for i, case in enumerate(all_cases):
        problem = _validate_case_shape(case, i)
        if problem:
            _fail(root, unit, f"invalid case shape: {problem}", cm_common.EXIT_FAIL)

    id_counts: dict[str, int] = {}
    for case in all_cases:
        id_counts[case["id"]] = id_counts.get(case["id"], 0) + 1
    duplicate_ids = sorted(cid for cid, n in id_counts.items() if n > 1)
    if duplicate_ids:
        _fail(
            root,
            unit,
            f"cases/{unit}.json has duplicate case ids: " + ", ".join(duplicate_ids),
            cm_common.EXIT_FAIL,
            duplicate_case_ids=duplicate_ids,
        )

    skipped_dropped = []
    kept_cases = []
    for case in all_cases:
        source_symbol = _call_source_symbol(unit, case["call"])
        if source_symbol in dropped:
            skipped_dropped.append(case["id"])
            continue
        kept_cases.append(case)
    if not kept_cases:
        _fail(root, unit,
            f"{unit}: every case targets a dropped symbol: " + ", ".join(skipped_dropped),
            cm_common.EXIT_FAIL,
        )

    before = cm_common.protected_digests(root, cfg)

    closure_units = sorted(set(cm_common.unit_closure(inventory, unit)) - {unit})
    calls_map = {c["call"]: c["call"] for c in kept_cases}

    captures = {}
    legacy_closure_units = sorted(set(closure_units) | {unit})
    legacy_closure: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="cm-stage-") as tmp:
        for env in ("A", "B"):
            stage = Path(tmp) / f"stage-{env}"
            stage.mkdir(parents=True, exist_ok=True)
            staged = observe.stage_trees(root, cfg, stage)
            trace_file = str(_staged_legacy_file(root, cfg, staged["legacy"], unit))
            job = {
                "mode": "capture",
                "env": env,
                "preload": closure_units,
                "stage_root": str(stage),
                "sys_path": [str(staged["legacy"])],
                "module": unit,
                "calls": calls_map,
                "cases": kept_cases,
                "trace_file": trace_file,
            }
            captures[env] = observe.run_harness(job, stage=stage)
            if env == "A":
                # The net is bound to the exact legacy bytes the capture
                # actually executed (plan 4.7 step 11) — hash the staged
                # copy here, before this `with` block tears it down, never
                # the live legacy_root (which could differ if something
                # else edits it between staging and this point).
                legacy_closure = cm_common.closure_digests(
                    staged["legacy"], cfg["legacy_package"], legacy_closure_units
                )

    after = cm_common.protected_digests(root, cfg)
    changed = cm_common.diff_digests(before, after)
    if changed:
        _fail(root, unit,
            "capture tampered outside the sandbox: " + ", ".join(changed),
            cm_common.EXIT_FAIL,
            tampered=changed,
        )

    obs_a = captures["A"]["observations"]
    obs_b = captures["B"]["observations"]

    state_changes: dict[str, set] = {}
    for obs_list in (obs_a, obs_b):
        for obs in obs_list:
            if obs.get("state_changes"):
                state_changes.setdefault(obs["case_id"], set()).update(obs["state_changes"])
    if state_changes:
        detail = {cid: sorted(keys) for cid, keys in state_changes.items()}
        _fail(root, unit,
            f"{unit} is stateful: "
            + "; ".join(f"{cid}: {keys}" for cid, keys in sorted(detail.items())),
            cm_common.EXIT_FAIL,
            verdict="stateful",
            state_changes=detail,
        )

    nondeterministic = compare_captures(obs_a, obs_b)
    if nondeterministic:
        _fail(root, unit,
            f"{unit} is nondeterministic across environments: " + ", ".join(sorted(nondeterministic)),
            cm_common.EXIT_FAIL,
            verdict="nondeterministic",
            case_ids=sorted(nondeterministic),
        )

    kept = []
    dropped_obs: dict[str, str] = {}
    for obs in obs_a:
        reason = None
        if obs["status"] == "harness_error":
            reason = obs.get("harness_error") or "harness_error"
        elif obs.get("denied"):
            reason = "denied: " + ", ".join(obs["denied"])
        else:
            unsupported = _find_unsupported(obs)
            if unsupported:
                reason = f"unsupported value: {unsupported}"
        if reason:
            dropped_obs[obs["case_id"]] = reason
        else:
            kept.append(obs)

    if not kept:
        _fail(root, unit,
            f"{unit}: zero cases kept after dropping " + ", ".join(sorted(dropped_obs)),
            cm_common.EXIT_FAIL,
        )

    dropped_symbol_spans = _symbol_span_lines(
        unit_info.get("symbol_spans", {}), dropped & set(unit_info.get("public_symbols", []))
    )
    executable = set(unit_info.get("executable_lines", [])) - dropped_symbol_spans
    import_covered = set(captures["A"].get("import_covered_lines") or [])
    case_covered: set = set()
    for obs in kept:
        case_covered.update(obs.get("covered_lines") or [])
    covered = import_covered | case_covered

    if executable:
        coverage_pct = round(100.0 * len(covered & executable) / len(executable), 4)
    else:
        coverage_pct = 100.0
    uncovered_lines = sorted(executable - covered)
    floor = cfg.get("coverage_floor_pct", 0)
    if coverage_pct < floor:
        _fail(root, unit,
            f"{unit} coverage {coverage_pct}% is below the floor {floor}%",
            cm_common.EXIT_FAIL,
            verdict="below_floor",
            uncovered_lines=uncovered_lines,
        )

    legacy_closure_sha256 = cm_common.sha256_json(legacy_closure)

    kept_stored = [{"case_id": obs["case_id"], **{k: obs[k] for k in _BEHAVIOURAL_CHANNELS}} for obs in kept]
    net_doc = {
        "schema": 1,
        "unit": unit,
        "legacy_closure": legacy_closure,
        "legacy_closure_sha256": legacy_closure_sha256,
        "observations": kept_stored,
    }
    nets_dir = root / "nets"
    nets_dir.mkdir(parents=True, exist_ok=True)
    net_path = nets_dir / f"{unit}.json"
    cm_common.atomic_write_json(net_path, net_doc)
    net_sha256 = cm_common.sha256_file(net_path)
    cases_sha256 = cm_common.sha256_file(cases_path)

    lock_doc = _read_net_lock(root)
    lock_doc.setdefault("units", {})[unit] = {
        "net_sha256": net_sha256,
        "cases_sha256": cases_sha256,
        "legacy_closure_sha256": legacy_closure_sha256,
        "coverage_pct": coverage_pct,
        "kept": len(kept),
        "dropped": dropped_obs,
        "skipped_dropped_symbol": skipped_dropped,
        "deterministic": True,
        "stateful": False,
    }
    cm_common.atomic_write_json(root / "net.lock.json", lock_doc)

    result = {
        "ok": True,
        "unit": unit,
        "verdict": "netted",
        "kept": len(kept),
        "dropped": len(dropped_obs),
        "coverage_pct": coverage_pct,
        "uncovered_lines": uncovered_lines,
        "state_changes": {},
    }
    cm_common.atomic_write_json(cm_common.unit_run_dir(root, unit) / "net_capture.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    unit = cm_common.require_unit(root, args.unit)

    result = run(root, cfg, unit)
    cm_common.emit(result)
    return cm_common.EXIT_OK


if __name__ == "__main__":
    cm_common.run_main(main)
