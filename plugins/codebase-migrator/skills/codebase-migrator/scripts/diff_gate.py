#!/usr/bin/env python3
"""R3: the differential gate (plan section 4.8). Replays the frozen net's
cases against the ported target module, in both environments, and requires
every count to agree and every behavioural channel to match."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402
import inventory  # noqa: E402
import ledger  # noqa: E402
import observe  # noqa: E402


def _read_net_lock(root: Path) -> dict:
    path = root / "net.lock.json"
    if not path.is_file():
        return {"schema": 1, "units": {}}
    return cm_common.read_json(path, "net.lock.json")


def _read_registry_lock(root: Path) -> dict:
    path = root / "registry.lock.json"
    if not path.is_file():
        return {"schema": 1, "rows": {}}
    return cm_common.read_json(path, "registry.lock.json")


def _staged_rel(root: Path, cfg: dict, unit: str, which: str) -> Path:
    if which == "legacy":
        live = cm_common.legacy_file(root, cfg, unit)
        base = cm_common.resolved_paths(root, cfg)["legacy_root"]
    else:
        live = cm_common.target_file(root, cfg, unit)
        base = cm_common.resolved_paths(root, cfg)["target_root"]
    return live.relative_to(base)


def _route_violations(route_files, unit_own_rel: str, allowed_legacy_rels: set) -> list:
    """`allowed_legacy_rels` is the stage-relative ("legacy/...") form of
    every path `inventory.closure_files(staged legacy, legacy_package, U)`
    names, MINUS U's own file. `closure_files` already walks every
    ancestor-package `__init__.py` along the way — unit or not — so no
    separate exemption or unit-lookup logic is needed here: a file is
    allowed iff it's in that one set, and U's own file is checked first so
    it is refused even though it was excluded from the set for the same
    reason."""
    violations = []
    for rel in route_files or []:
        if not rel.startswith("legacy/"):
            continue
        if rel == unit_own_rel:
            violations.append(f"reached the unit's own legacy file: {rel}")
        elif rel in allowed_legacy_rels:
            continue
        else:
            violations.append(f"reached a legacy file outside the dependency closure: {rel}")
    return violations


def _case_route_violations(route_files, unit_own_rel: str, allowed_legacy_rels: set, target_rel: str) -> list:
    violations = _route_violations(route_files, unit_own_rel, allowed_legacy_rels)
    if target_rel not in (route_files or []):
        violations.append(f"target file was never reached: {target_rel}")
    return violations


def _load_exceptions(root: Path, unit: str) -> dict:
    path = root / "exceptions.json"
    if not path.is_file():
        return {}
    doc = cm_common.read_json(path, "exceptions.json")
    prefix = f"{unit}/"
    return {
        k[len(prefix):]: v for k, v in doc.get("cases", {}).items() if k.startswith(prefix)
    }


def _no_op_exception_ids(exceptions: dict, net_doc: dict) -> list:
    """Case ids whose declared `expected` does not differ from the legacy
    observation on any declared channel — a no-op declaration lets a
    target that never fixed the bug (still matches legacy) also match the
    "expectation", so the check could never catch it."""
    legacy_by_id = {o["case_id"]: o for o in net_doc.get("observations", [])}
    no_op_ids = []
    for case_id, entry in exceptions.items():
        legacy_obs = legacy_by_id.get(case_id)
        if legacy_obs is None:
            continue  # an unknown/stale case id is not this check's job
        expected_fields = entry.get("expected", {})
        if not expected_fields or all(legacy_obs.get(ch) == v for ch, v in expected_fields.items()):
            no_op_ids.append(case_id)
    return sorted(no_op_ids)


def _expected_for_case(case_id: str, legacy_obs: dict, exceptions: dict) -> dict:
    entry = exceptions.get(case_id)
    if entry is None:
        return legacy_obs
    expected = dict(legacy_obs)
    expected.update(entry.get("expected", {}))
    return expected


def _remap_target_value(value, target_to_source: dict):
    if isinstance(value, dict):
        new_value = {k: _remap_target_value(v, target_to_source) for k, v in value.items()}
        if "$obj" in new_value:
            new_value["$obj"] = target_to_source.get(new_value["$obj"], new_value["$obj"])
        return new_value
    if isinstance(value, list):
        return [_remap_target_value(v, target_to_source) for v in value]
    return value


def _remap_observation_for_target(obs: dict, target_to_source: dict) -> dict:
    remapped = dict(obs)
    for channel in ("receiver_after", "args_after", "kwargs_after", "return"):
        remapped[channel] = _remap_target_value(obs.get(channel), target_to_source)
    if obs.get("error") is not None:
        remapped["error"] = {
            "type": target_to_source.get(obs["error"]["type"], obs["error"]["type"]),
            "message": obs["error"]["message"],
        }
    return remapped


def _build_calls_map(unit: str, replay_cases: list, lock_rows: dict) -> dict:
    calls_map = {}
    for case in replay_cases:
        call_spec = case["call"]
        head, sep, tail = call_spec.partition(".")
        source_symbol = f"{unit}:{head}"
        entry = lock_rows.get(source_symbol)
        if entry is None:
            cm_common.fail(
                f"case {case['id']} calls {call_spec}, but {source_symbol} has no frozen row",
                cm_common.EXIT_FAIL,
            )
        row = entry["row"]
        if row.get("cardinality") == "dropped":
            cm_common.fail(
                f"case {case['id']} calls {call_spec}, but {source_symbol} is dropped",
                cm_common.EXIT_FAIL,
            )
        target_qualname = row["entry"].split(":", 1)[1]
        calls_map[call_spec] = target_qualname + (sep + tail if sep else "")
    return calls_map


def _evaluate_run(run_result: dict, unit_own_rel: str, allowed_legacy_rels: set, target_rel: str) -> dict:
    import_violations = _route_violations(run_result.get("import_route_files"), unit_own_rel, allowed_legacy_rels)
    evaluated = {}
    for obs in run_result.get("observations", []):
        cid = obs["case_id"]
        executed = obs["status"] == "ok" and not obs.get("denied") and not obs.get("state_changes")
        route_violations = list(import_violations)
        if executed:
            route_violations += _case_route_violations(
                obs.get("route_files"), unit_own_rel, allowed_legacy_rels, target_rel
            )
        evaluated[cid] = {
            "obs": obs,
            "executed": executed,
            "route_ok": executed and not route_violations,
            "route_violations": route_violations,
        }
    return evaluated


def run(root: Path, cfg: dict, unit: str) -> dict:
    net_lock = _read_net_lock(root)
    net_entry = net_lock.get("units", {}).get(unit)
    if net_entry is None:
        cm_common.fail(f"no net recorded for {unit}: run net_capture.py", cm_common.EXIT_FAIL)

    net_path = root / "nets" / f"{unit}.json"
    if not net_path.is_file() or cm_common.sha256_file(net_path) != net_entry.get("net_sha256"):
        cm_common.fail(f"nets/{unit}.json does not match net.lock.json: re-run net_capture.py", cm_common.EXIT_FAIL)
    net_doc = cm_common.read_json(net_path, f"nets/{unit}.json")
    net_case_id_counts: dict[str, int] = {}
    for obs in net_doc.get("observations", []):
        net_case_id_counts[obs["case_id"]] = net_case_id_counts.get(obs["case_id"], 0) + 1
    duplicate_net_ids = sorted(cid for cid, n in net_case_id_counts.items() if n > 1)
    if duplicate_net_ids:
        cm_common.fail(
            f"nets/{unit}.json has duplicate case ids: " + ", ".join(duplicate_net_ids),
            cm_common.EXIT_FAIL,
            duplicate_case_ids=duplicate_net_ids,
        )

    cases_path = root / "cases" / f"{unit}.json"
    if not cases_path.is_file() or cm_common.sha256_file(cases_path) != net_entry.get("cases_sha256"):
        cm_common.fail(
            f"cases/{unit}.json changed since capture: re-run net_capture.py", cm_common.EXIT_FAIL
        )
    cases_doc = cm_common.read_json(cases_path, f"cases/{unit}.json")
    cases_by_id = {c["id"]: c for c in cases_doc.get("cases", [])}

    target_path = cm_common.target_file(root, cfg, unit)
    if not target_path.is_file():
        cm_common.fail(f"{unit}: target file is absent: {target_path}", cm_common.EXIT_FAIL)
    if cm_common.is_shim_file(target_path):
        cm_common.fail(f"{unit}: target file is a shim, not a port: {target_path}", cm_common.EXIT_FAIL)

    inv = cm_common.read_json(root / "inventory.json", "inventory.json")
    known_units = set(inv.get("units", {}).keys())
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    live_closure_files = inventory.closure_files(legacy_root, cfg["legacy_package"], unit)
    live_digests = cm_common.file_digests(legacy_root, live_closure_files)
    stored_closure = net_doc.get("legacy_closure", {})
    changed_legacy_files = sorted(
        f for f in set(live_digests) | set(stored_closure) if live_digests.get(f) != stored_closure.get(f)
    )
    if changed_legacy_files:
        cm_common.fail(
            f"legacy closure drifted for {unit} since capture: "
            + ", ".join(changed_legacy_files)
            + " (recovery: inventory.py, ledger.py accept-drift, net_capture.py)",
            cm_common.EXIT_FAIL,
            changed_legacy_files=changed_legacy_files,
        )

    lock = _read_registry_lock(root)
    lock_rows = lock.get("rows", {})
    target_to_source: dict[str, str] = {}
    for source, entry in lock_rows.items():
        for t in entry["row"].get("targets", []):
            target_to_source[t] = source

    net_case_ids = [obs["case_id"] for obs in net_doc.get("observations", [])]
    replay_cases = [cases_by_id[cid] for cid in net_case_ids if cid in cases_by_id]
    calls_map = _build_calls_map(unit, replay_cases, lock_rows)

    exceptions = _load_exceptions(root, unit) if cfg.get("fidelity_policy") == "bug_for_bug_with_exceptions" else {}
    if exceptions:
        no_op_ids = _no_op_exception_ids(exceptions, net_doc)
        if no_op_ids:
            cm_common.fail(
                f"{unit}: exceptions.json declares a no-op expectation (identical to legacy) for: "
                + ", ".join(no_op_ids),
                cm_common.EXIT_FAIL,
                no_op_exception_ids=no_op_ids,
            )

    before = cm_common.protected_digests(root, cfg)
    target_module_name = cm_common.target_module(cfg, unit)
    target_rel = "target/" + _staged_rel(root, cfg, unit, "target").as_posix()
    unit_own_rel = "legacy/" + _staged_rel(root, cfg, unit, "legacy").as_posix()

    runs = {}
    allowed_legacy_rels: set = set()
    harness_exc: Exception | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="cm-stage-") as tmp:
            for env in ("A", "B"):
                stage = Path(tmp) / f"stage-{env}"
                stage.mkdir(parents=True, exist_ok=True)
                staged = observe.stage_trees(root, cfg, stage)
                target_closure_files = inventory.closure_files(
                    staged["target"], cfg["target_package"], target_module_name
                )
                preload = sorted(
                    {inventory.module_name_from_closure_rel(rel) for rel in target_closure_files} - {target_module_name}
                )
                if env == "A":
                    # Computed from the staged copy while it still exists
                    # (torn down when this `with` block exits) — the set of
                    # legacy files the route rule allows for this unit.
                    legacy_closure_files = inventory.closure_files(staged["legacy"], cfg["legacy_package"], unit)
                    allowed_legacy_rels = {f"legacy/{rel}" for rel in legacy_closure_files} - {unit_own_rel}
                job = {
                    "mode": "replay",
                    "env": env,
                    "preload": preload,
                    "stage_root": str(stage),
                    "sys_path": [str(staged["target"]), str(staged["legacy"])],
                    "module": target_module_name,
                    "calls": calls_map,
                    "cases": replay_cases,
                    "trace_file": None,
                }
                runs[env] = observe.run_harness(job, stage=stage)
    except Exception as exc:  # noqa: BLE001 - reported below, after the tamper check
        harness_exc = exc

    # The tamper check must run even when the harness itself failed
    # (HarnessFailure, a stage_trees I/O error, ...): a run that tampers
    # with a protected file and then crashes must still be caught, not
    # silently skipped because the crash unwound past this check.
    after = cm_common.protected_digests(root, cfg)
    changed = cm_common.diff_digests(before, after)
    if changed:
        cm_common.fail(
            "replay tampered outside the sandbox: " + ", ".join(changed),
            cm_common.EXIT_FAIL,
            tampered=changed,
        )
    if harness_exc is not None:
        cm_common.fail(f"replay harness failed: {harness_exc}", cm_common.EXIT_CANNOT)

    eval_a = _evaluate_run(runs["A"], unit_own_rel, allowed_legacy_rels, target_rel)
    eval_b = _evaluate_run(runs["B"], unit_own_rel, allowed_legacy_rels, target_rel)

    cases_in_corpus = len(net_case_ids)
    executed_ids = [
        cid for cid in net_case_ids if eval_a.get(cid, {}).get("executed") and eval_b.get(cid, {}).get("executed")
    ]
    counted_ids = [cid for cid in executed_ids if eval_a[cid]["route_ok"] and eval_b[cid]["route_ok"]]

    route_violation_reports = []
    for cid in net_case_ids:
        reasons = []
        if cid in eval_a:
            reasons += [f"A: {r}" for r in eval_a[cid]["route_violations"]]
        if cid in eval_b:
            reasons += [f"B: {r}" for r in eval_b[cid]["route_violations"]]
        if reasons:
            route_violation_reports.append({"case_id": cid, "reason": "; ".join(reasons)})

    state_changes_report: dict[str, list] = {}
    for cid in net_case_ids:
        keys: set = set()
        for e in (eval_a.get(cid), eval_b.get(cid)):
            if e and e["obs"].get("state_changes"):
                keys.update(e["obs"]["state_changes"])
        if keys:
            state_changes_report[cid] = sorted(keys)

    denied_report = []
    for cid in net_case_ids:
        for env_name, e in (("A", eval_a.get(cid)), ("B", eval_b.get(cid))):
            if e and e["obs"].get("denied"):
                denied_report.append(f"{env_name}/{cid}: " + ", ".join(e["obs"]["denied"]))

    crossed_shims: set = set()
    for cid in counted_ids:
        for e in (eval_a[cid], eval_b[cid]):
            for rel in e["obs"].get("route_files") or []:
                if rel.startswith("legacy/"):
                    # A counted case's route already passed `allowed_legacy_rels`
                    # (unit's own file or a violation would have excluded it), so
                    # any legacy file here is either an ancestor-package init
                    # (not itself a unit, not worth naming) or a genuine
                    # dependency unit — `known_units` distinguishes the two.
                    touched_unit = inventory.module_name_from_closure_rel(rel[len("legacy/"):])
                    if touched_unit in known_units:
                        crossed_shims.add(touched_unit)

    legacy_by_id = {o["case_id"]: o for o in net_doc.get("observations", [])}
    mismatches = []
    for cid in counted_ids:
        legacy_obs = legacy_by_id[cid]
        expected = _expected_for_case(cid, legacy_obs, exceptions)
        for env_name, e in (("A", eval_a[cid]), ("B", eval_b[cid])):
            target_obs = _remap_observation_for_target(e["obs"], target_to_source)
            for channel in observe.BEHAVIOURAL_CHANNELS:
                if target_obs.get(channel) != expected.get(channel):
                    mismatches.append(
                        {
                            "case_id": cid,
                            "channel": f"{env_name}:{channel}",
                            "legacy": expected.get(channel),
                            "target": target_obs.get(channel),
                        }
                    )

    counts_agree = cases_in_corpus == len(executed_ids) == len(counted_ids) > 0
    ok = counts_agree and not mismatches

    result = {
        "ok": ok,
        "unit": unit,
        "cases_in_corpus": cases_in_corpus,
        "cases_executed": len(executed_ids),
        "cases_counted": len(counted_ids),
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "route_violations": route_violation_reports,
        "state_changes": state_changes_report,
        "crossed_shims": sorted(crossed_shims),
        "denied": denied_report,
    }

    target_sha256 = cm_common.sha256_file(target_path)
    key = ledger.cache_key(root, cfg, unit)
    key_sha256 = cm_common.sha256_json(key)
    persisted = dict(result)
    persisted["target_sha256"] = target_sha256
    persisted["cache_key"] = key
    persisted["key_sha256"] = key_sha256
    cm_common.atomic_write_json(cm_common.unit_run_dir(root, unit) / "r3.json", persisted)

    return result


def main() -> int:
    parser = cm_common.make_parser(prog="diff_gate.py")
    parser.add_argument("--root", required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    unit = cm_common.require_unit(root, args.unit)

    result = run(root, cfg, unit)
    cm_common.emit(result)
    return cm_common.EXIT_OK if result["ok"] else cm_common.EXIT_FAIL


if __name__ == "__main__":
    cm_common.run_main(main)
