#!/usr/bin/env python3
"""The write-boundary dispatcher (plan section 4.10): probe / dispatch / digests.

Every model-authored write in this plugin goes through `dispatch`. Ground
truth for what happened is always a digest comparison of the durable, legacy
and target trees, never the model's own report of what it did.
"""
from __future__ import annotations

import argparse
import json
import os
import stat as stat_module
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402
import ledger  # noqa: E402

TEMPLATE_NAMES = {
    "port": "port_TASK.md",
    "fix": "fix_TASK.md",
    "review": "review_TASK.md",
    "cases": "cases_TASK.md",
}
SANDBOX_MODE = {
    "port": "workspace-write",
    "fix": "workspace-write",
    "review": "read-only",
    "cases": "workspace-write",
}
REQUIRED_FINDING_FIELDS = ("rule", "severity", "location", "issue", "suggestion")
SEVERITIES = ("blocker", "major", "minor")
DISPATCH_TIMEOUT_S = 1800


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _codex_bin(cfg: dict) -> str:
    return os.environ.get("CM_CODEX_BIN") or cfg["codex_bin"]


def _codex_version(binpath: str) -> str:
    try:
        proc = subprocess.run([binpath, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        cm_common.fail(f"could not run {binpath} --version: {exc}", cm_common.EXIT_CANNOT)
    text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return text


def render_template(template_path: Path, unit: str, target_module: str, round_num: int) -> str:
    """Fill `{{UNIT}}`, `{{TARGET_MODULE}}`, `{{ROUND}}` in `template_path`'s
    text. Raises `ValueError` if any `{{...}}` placeholder survives -- a typo
    or an unrendered template must never reach a dispatch."""
    text = template_path.read_text(encoding="utf-8")
    text = text.replace("{{UNIT}}", unit)
    text = text.replace("{{TARGET_MODULE}}", target_module)
    text = text.replace("{{ROUND}}", str(round_num))
    if "{{" in text:
        raise ValueError(f"template has an unreplaced placeholder: {template_path.name}")
    return text


def _is_shim(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first_line = fh.readline().rstrip("\n")
    except OSError:
        return False
    return first_line.startswith("# codebase-migrator: shim for ")


# --- probe -------------------------------------------------------------------


def cmd_probe(root: Path, cfg: dict) -> int:
    paths = cm_common.resolved_paths(root, cfg)
    legacy_root = paths["legacy_root"]
    target_root = paths["target_root"]
    target_root.mkdir(parents=True, exist_ok=True)

    stage = Path(tempfile.mkdtemp(prefix="cm-stage-"))
    check = subprocess.run(["git", "-C", str(stage), "rev-parse"], capture_output=True)
    if check.returncode == 0:
        cm_common.fail("probe stage landed inside a git worktree", cm_common.EXIT_CANNOT, stage=str(stage))

    token = uuid.uuid4().hex
    canaries = [root / "runs" / ".cm_canary", legacy_root / ".cm_canary", target_root / ".cm_canary"]
    for canary in canaries:
        cm_common.atomic_write_text(canary, token)

    probe_dir = stage / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh"]
    for i, canary in enumerate(canaries):
        err = probe_dir / f"{i}.err"
        rc_file = probe_dir / f"{i}.rc"
        lines.append(f"# CANARY {i} {canary}")
        lines.append(f"sh -c 'printf tampered > \"{canary}\"' 2> \"{err}\"")
        lines.append(f"echo $? > \"{rc_file}\"")
    inside = probe_dir / "inside.txt"
    lines.append(f'printf ok > "{inside}"')
    probe_sh = stage / "probe.sh"
    cm_common.atomic_write_text(probe_sh, "\n".join(lines) + "\n")

    binpath = _codex_bin(cfg)
    version = _codex_version(binpath)
    out_file = stage / "probe.out"
    prompt = f"Run exactly this command, then stop: sh {probe_sh}\n"
    argv = [
        binpath, "exec", "-s", "workspace-write", "-C", str(stage),
        "--skip-git-repo-check", "--ephemeral", "-o", str(out_file), "-",
    ]
    try:
        subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=DISPATCH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        for canary in canaries:
            canary.unlink(missing_ok=True)
        cm_common.fail(f"codex binary unavailable for the probe: {exc}", cm_common.EXIT_CANNOT)

    def _read(path: Path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    rc_values = {}
    attempts = {}
    for i in range(len(canaries)):
        rc_text = _read(probe_dir / f"{i}.rc")
        err_text = _read(probe_dir / f"{i}.err") or ""
        rc_values[i] = rc_text.strip() if rc_text is not None else None
        attempts[str(i)] = {"rc": rc_values[i], "stderr_head": err_text[:500]}

    tokens_intact = all(_read(c) == token for c in canaries)

    if not inside.exists() or any(rc_values[i] is None for i in range(len(canaries))):
        result = "NOT_EXERCISED"
    elif not tokens_intact or any(rc_values[i] == "0" for i in range(len(canaries))):
        result = "NOT_DENIED"
    else:
        result = "denied"

    for canary in canaries:
        canary.unlink(missing_ok=True)

    record = {"schema": 1, "codex_version": version, "result": result, "at": _now_iso(), "attempts": attempts}
    cm_common.atomic_write_json(root / "runs" / "sandbox_probe.json", record)
    cm_common.emit({"ok": result == "denied", "result": result, "codex_version": version})
    return cm_common.EXIT_OK if result == "denied" else cm_common.EXIT_FAIL


# --- dispatch: pack construction ----------------------------------------------


def _latest_review(run_dir: Path):
    if not run_dir.is_dir():
        return None
    best_n, best = -1, None
    for p in run_dir.glob("review.r*.json"):
        try:
            n = int(p.stem.rsplit("r", 1)[-1])
        except ValueError:
            continue
        if n > best_n:
            best_n, best = n, p
    return _try_read_json(best) if best is not None else None


def _latest_uncovered(run_dir: Path):
    """The uncovered-lines report from the last `net_capture.py` run for this
    unit, if that report is persisted at `runs/<unit>/net_capture.json`.
    Absent when no such run has happened yet, or net_capture.py does not
    persist one (its own contract does not pin a location for this file)."""
    doc = _try_read_json(run_dir / "net_capture.json")
    return doc


def _build_pack(root: Path, cfg: dict, unit: str, kind: str, round_num: int) -> dict:
    pack: dict[str, str] = {}

    legacy_path = cm_common.legacy_file(root, cfg, unit)
    pack["source.py"] = legacy_path.read_text(encoding="utf-8")

    lock = _try_read_json(root / "registry.lock.json") or {"rows": {}}
    inventory_doc = cm_common.read_json(root / "inventory.json", "inventory.json")
    info = inventory_doc.get("units", {}).get(unit, {})
    wanted = set(info.get("public_symbols", [])) | set(info.get("imported_symbols", []))
    rows = [entry["row"] for src, entry in lock.get("rows", {}).items() if src in wanted]
    pack["rows.json"] = json.dumps({"rows": rows}, indent=2, sort_keys=True)

    conventions_path = root / "conventions.md"
    pack["conventions.md"] = conventions_path.read_text(encoding="utf-8") if conventions_path.is_file() else ""

    policy = {
        "fidelity_policy": cfg.get("fidelity_policy"),
        "naming_policy": cfg.get("naming_policy"),
        "target_module": cm_common.target_module(cfg, unit),
    }
    pack["policy.json"] = json.dumps(policy, indent=2, sort_keys=True)

    run_dir = cm_common.unit_run_dir(root, unit)

    if kind in ("fix", "review"):
        target_path = cm_common.target_file(root, cfg, unit)
        pack["target.py"] = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
        prev = _latest_review(run_dir)
        pack["previous_review.json"] = json.dumps(prev if prev is not None else {}, indent=2, sort_keys=True)
        refusals_doc = _try_read_json(run_dir / "refusals.json") or {"schema": 1, "refusals": []}
        pack["refusals.json"] = json.dumps(refusals_doc, indent=2, sort_keys=True)

    if kind == "fix":
        admitted_doc = _try_read_json(run_dir / f"admitted.r{round_num}.json") or {"schema": 1, "findings": []}
        pack["findings.json"] = json.dumps({"findings": admitted_doc.get("findings", [])}, indent=2, sort_keys=True)

    if kind == "cases":
        uncovered = _latest_uncovered(run_dir)
        pack["uncovered_lines.json"] = json.dumps(
            uncovered if uncovered is not None else {"uncovered_lines": []}, indent=2, sort_keys=True
        )
        cases_path = root / "cases" / f"{unit}.json"
        existing = _try_read_json(cases_path) or {"cases": []}
        pack["existing_cases.json"] = json.dumps(existing, indent=2, sort_keys=True)
        dropped = sorted(
            entry["row"]["source"]
            for entry in lock.get("rows", {}).values()
            if entry["row"].get("cardinality") == "dropped" and entry["row"]["source"].split(":", 1)[0] == unit
        )
        pack["dropped_symbols.json"] = json.dumps({"dropped": dropped}, indent=2, sort_keys=True)

    return pack


# --- dispatch: preconditions ---------------------------------------------------


def _check_probe(root: Path, cfg: dict) -> None:
    probe = _try_read_json(root / "runs" / "sandbox_probe.json")
    if probe is None:
        cm_common.fail("no sandbox probe on record: run sandbox.py probe first", cm_common.EXIT_FAIL)
    if probe.get("result") != "denied":
        cm_common.fail(
            f"sandbox probe result is {probe.get('result')!r}, not denied", cm_common.EXIT_FAIL,
            probe_result=probe.get("result"),
        )
    current_version = _codex_version(_codex_bin(cfg))
    if probe.get("codex_version") != current_version:
        cm_common.fail(
            "sandbox probe was recorded for a different codex version", cm_common.EXIT_FAIL,
            probed_version=probe.get("codex_version"), current_version=current_version,
        )


def _check_eligibility(root: Path, cfg: dict, unit: str, kind: str) -> None:
    if kind in ("port", "fix", "review"):
        reasons = ledger.eligible(root, cfg, unit)
        if reasons:
            cm_common.fail("unit is not eligible for dispatch", cm_common.EXIT_FAIL, unit=unit, reasons=reasons)
        return
    # kind == "cases": only the static verdict and frozen rows are required --
    # a net cannot exist before its cases do.
    inventory_doc = cm_common.read_json(root / "inventory.json", "inventory.json")
    info = inventory_doc.get("units", {}).get(unit)
    if info is None or not info.get("eligible"):
        cm_common.fail("unit is not statically eligible", cm_common.EXIT_FAIL, unit=unit)
    lock = _try_read_json(root / "registry.lock.json") or {"rows": {}}
    missing = sorted(s for s in info.get("public_symbols", []) if s not in lock.get("rows", {}))
    if missing:
        cm_common.fail("unit has unfrozen public symbols", cm_common.EXIT_FAIL, unit=unit, missing=missing)


def _check_fix_preconditions(root: Path, cfg: dict, unit: str, round_num: int) -> None:
    target_path = cm_common.target_file(root, cfg, unit)
    if not target_path.is_file() or _is_shim(target_path):
        cm_common.fail("fix dispatch requires an existing port", cm_common.EXIT_FAIL, unit=unit)
    run_dir = cm_common.unit_run_dir(root, unit)
    review_path = run_dir / f"review.r{round_num}.json"
    review_doc = _try_read_json(review_path)
    if review_doc is None:
        cm_common.fail("no review recorded for this round", cm_common.EXIT_FAIL, unit=unit, round=round_num)
    admitted_doc = _try_read_json(run_dir / f"admitted.r{round_num}.json") or {"findings": []}
    refusals_doc = _try_read_json(run_dir / "refusals.json") or {"refusals": []}
    admitted_digests = {f.get("digest") for f in admitted_doc.get("findings", [])}
    refused_digests = {r.get("digest") for r in refusals_doc.get("refusals", [])}
    unadjudicated = [
        f.get("digest") for f in review_doc.get("findings", [])
        if f.get("digest") not in admitted_digests and f.get("digest") not in refused_digests
    ]
    if unadjudicated:
        cm_common.fail(
            "unadjudicated findings block the fix dispatch", cm_common.EXIT_FAIL,
            unit=unit, findings=unadjudicated,
        )


# --- dispatch: promotion --------------------------------------------------------


def _is_regular_file(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat_module.S_ISREG(st.st_mode)


def _promote_port_or_fix(root: Path, cfg: dict, unit: str, stage: Path) -> tuple[list, list]:
    out_dir = stage / "out"
    others = sorted(p.name for p in out_dir.iterdir()) if out_dir.is_dir() else []
    if "target.py" not in others:
        cm_common.fail("dispatch produced no out/target.py", cm_common.EXIT_FAIL, unit=unit)
    target_stage = out_dir / "target.py"
    if not _is_regular_file(target_stage):
        cm_common.fail("out/target.py is not a regular file", cm_common.EXIT_FAIL, unit=unit, path=str(target_stage))
    try:
        text = target_stage.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        cm_common.fail("out/target.py is not valid UTF-8", cm_common.EXIT_FAIL, unit=unit)
    dest = cm_common.target_file(root, cfg, unit)
    try:
        compile(text, str(dest), "exec")
    except SyntaxError as exc:
        cm_common.fail(f"out/target.py does not compile: {exc}", cm_common.EXIT_FAIL, unit=unit)
    cm_common.atomic_write_text(dest, text)
    promoted = ["out/target.py"]
    ignored = [f"out/{name}" for name in others if name != "target.py"]
    return promoted, ignored


def _valid_finding_shape(item) -> bool:
    if not isinstance(item, dict):
        return False
    if not all(k in item for k in REQUIRED_FINDING_FIELDS):
        return False
    if item.get("severity") not in SEVERITIES:
        return False
    for k in ("rule", "location", "issue", "suggestion"):
        if not isinstance(item.get(k), str) or not item[k]:
            return False
    return True


def _promote_review(root: Path, cfg: dict, unit: str, round_num: int, out_file: Path) -> tuple[list, list, list]:
    raw = out_file.read_text(encoding="utf-8") if out_file.is_file() else ""
    stripped = raw.strip()
    findings: list[dict] = []
    malformed: list = []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        malformed.append("<message not a single JSON object>")
    else:
        for item in parsed["findings"]:
            if _valid_finding_shape(item):
                finding = {k: item[k] for k in REQUIRED_FINDING_FIELDS}
                finding["digest"] = cm_common.sha256_json(finding)
                findings.append(finding)
            else:
                malformed.append(item)

    target_path = cm_common.target_file(root, cfg, unit)
    target_sha256 = cm_common.sha256_file(target_path) if target_path.is_file() else None
    run_dir = cm_common.unit_run_dir(root, unit)
    review_path = run_dir / f"review.r{round_num}.json"
    if review_path.is_file():
        cm_common.fail("review round already recorded", cm_common.EXIT_FAIL, unit=unit, round=round_num)
    doc = {
        "schema": 1, "round": round_num, "target_sha256": target_sha256,
        "findings": findings, "malformed": malformed,
    }
    cm_common.atomic_write_json(review_path, doc)
    return [f"runs/{unit}/review.r{round_num}.json"], [], malformed


def _valid_case_shape(case) -> bool:
    if not isinstance(case, dict):
        return False
    if not isinstance(case.get("id"), str) or not case["id"]:
        return False
    if not isinstance(case.get("call"), str) or not case["call"]:
        return False
    for key in ("args", "init_args"):
        if key in case and not isinstance(case[key], list):
            return False
    for key in ("kwargs", "init_kwargs"):
        if key in case and not isinstance(case[key], dict):
            return False
    return True


def _promote_cases(root: Path, unit: str, stage: Path) -> tuple[list, list]:
    out_dir = stage / "out"
    others = sorted(p.name for p in out_dir.iterdir()) if out_dir.is_dir() else []
    if "cases.json" not in others:
        cm_common.fail("dispatch produced no out/cases.json", cm_common.EXIT_FAIL, unit=unit)
    cases_stage = out_dir / "cases.json"
    if not _is_regular_file(cases_stage):
        cm_common.fail("out/cases.json is not a regular file", cm_common.EXIT_FAIL, unit=unit)
    doc = _try_read_json(cases_stage)
    if not isinstance(doc, dict) or not isinstance(doc.get("cases"), list):
        cm_common.fail("out/cases.json is not the pinned shape", cm_common.EXIT_FAIL, unit=unit)

    cases_path = root / "cases" / f"{unit}.json"
    existing_doc = _try_read_json(cases_path) or {"cases": []}
    existing_ids = {c.get("id") for c in existing_doc.get("cases", [])}

    invalid = []
    valid_new = []
    for case in doc["cases"]:
        if not _valid_case_shape(case):
            invalid.append(case.get("id") if isinstance(case, dict) else None)
            continue
        if case["id"] in existing_ids:
            continue
        valid_new.append(case)

    merged = {"cases": existing_doc.get("cases", []) + valid_new}
    cm_common.atomic_write_json(cases_path, merged)
    promoted = [f"cases/{unit}.json (+{len(valid_new)})"]
    ignored = [f"invalid case: {cid}" for cid in invalid]
    ignored.extend(f"out/{name}" for name in others if name != "cases.json")
    return promoted, ignored


def _append_journal(run_dir: Path, kind: str, round_num: int, stage: Path, exit_status: int, tampered: list, promoted: list, ignored_outputs: list) -> None:
    entry = {
        "at": _now_iso(), "kind": kind, "round": round_num, "stage": str(stage),
        "exit": exit_status, "tampered": tampered, "promoted": promoted, "ignored_outputs": ignored_outputs,
    }
    journal_path = run_dir / "journal.jsonl"
    with open(journal_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def cmd_dispatch(root: Path, cfg: dict, unit: str, kind: str, round_num: int) -> int:
    _check_probe(root, cfg)

    if cm_common.under_temp_root(root):
        cm_common.fail("root is under a temp root", cm_common.EXIT_FAIL, root=str(root))

    _check_eligibility(root, cfg, unit, kind)
    if kind == "fix":
        _check_fix_preconditions(root, cfg, unit, round_num)

    stage = Path(tempfile.mkdtemp(prefix="cm-stage-"))
    pack_dir = stage / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack = _build_pack(root, cfg, unit, kind, round_num)
    for name, text in pack.items():
        cm_common.atomic_write_text(pack_dir / name, text)

    units_dir = root / "units" / unit
    units_dir.mkdir(parents=True, exist_ok=True)
    cm_common.atomic_write_json(units_dir / f"pack.{kind}.json", pack)

    (stage / "out").mkdir(parents=True, exist_ok=True)

    templates_dir = cm_common.plugin_root() / "skills" / "codebase-migrator" / "assets" / "templates"
    template_path = templates_dir / TEMPLATE_NAMES[kind]
    target_module_name = cm_common.target_module(cfg, unit)
    try:
        prompt = render_template(template_path, unit, target_module_name, round_num)
    except ValueError as exc:
        cm_common.fail(str(exc), cm_common.EXIT_CANNOT)

    run_dir = cm_common.unit_run_dir(root, unit)
    before = cm_common.protected_digests(root, cfg)

    out_file = stage / "last_message.txt"
    binpath = _codex_bin(cfg)
    mode = SANDBOX_MODE[kind]
    argv = [binpath, "exec", "-s", mode, "-C", str(stage), "--skip-git-repo-check", "-o", str(out_file), "-"]
    try:
        subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=DISPATCH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        cm_common.fail(f"codex binary unavailable for dispatch: {exc}", cm_common.EXIT_CANNOT)

    after = cm_common.protected_digests(root, cfg)
    tampered = cm_common.diff_digests(before, after)
    if tampered:
        _append_journal(run_dir, kind, round_num, stage, 1, tampered, [], [])
        cm_common.fail(
            "dispatch tampered with a protected tree; nothing promoted", cm_common.EXIT_FAIL,
            tampered=tampered,
        )

    malformed_findings: list = []
    if kind in ("port", "fix"):
        promoted, ignored_outputs = _promote_port_or_fix(root, cfg, unit, stage)
    elif kind == "review":
        promoted, ignored_outputs, malformed_findings = _promote_review(root, cfg, unit, round_num, out_file)
    else:
        promoted, ignored_outputs = _promote_cases(root, unit, stage)

    _append_journal(run_dir, kind, round_num, stage, 0, [], promoted, ignored_outputs)

    result = {
        "ok": True, "unit": unit, "kind": kind, "round": round_num,
        "promoted": promoted, "tampered": [], "ignored_outputs": ignored_outputs,
        "malformed_findings": malformed_findings,
    }
    cm_common.emit(result)
    return cm_common.EXIT_OK


def cmd_digests(root: Path, cfg: dict) -> int:
    digests = cm_common.protected_digests(root, cfg)
    cm_common.emit({"ok": True, "count": len(digests), "sha256": cm_common.sha256_json(digests)})
    return cm_common.EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    probe_p = sub.add_parser("probe")
    probe_p.add_argument("--root", required=True)

    dispatch_p = sub.add_parser("dispatch")
    dispatch_p.add_argument("--root", required=True)
    dispatch_p.add_argument("--unit", required=True)
    dispatch_p.add_argument("--kind", required=True, choices=["port", "fix", "review", "cases"])
    dispatch_p.add_argument("--round", type=int, default=1)

    digests_p = sub.add_parser("digests")
    digests_p.add_argument("--root", required=True)

    args = parser.parse_args()
    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)

    if args.command == "probe":
        return cmd_probe(root, cfg)
    if args.command == "dispatch":
        unit = cm_common.require_unit(root, args.unit)
        return cmd_dispatch(root, cfg, unit, args.kind, args.round)
    return cmd_digests(root, cfg)


if __name__ == "__main__":
    cm_common.run_main(main)
