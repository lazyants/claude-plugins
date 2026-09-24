#!/usr/bin/env python3
"""R2 -- the mechanical gate (plan section 4.9).

Static checks only: no LLM turn, no execution of the ported module. A port
must pass every check here before the differential gate (R3) ever imports
and calls it.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402
import inventory  # noqa: E402
import ledger  # noqa: E402

_COMMENT_MARKERS = ("TODO", "FIXME", "XXX")


def _try_read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _is_stub_body(body: list) -> bool:
    """A body of only `pass`, `...`, or `raise NotImplementedError[(...)]`,
    a leading docstring aside."""
    stmts = list(body)
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        call = stmt.exc
        func = call.func if isinstance(call, ast.Call) else call
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "NotImplementedError":
            return True
    return False


def _empty_except_body(body: list) -> bool:
    return len(body) == 1 and (
        isinstance(body[0], ast.Pass)
        or (
            isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis
        )
    )


def check_no_stubs(source: str, tree: ast.Module) -> list[str]:
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_stub_body(node.body):
                problems.append(f"stub body: {node.name}")
        elif isinstance(node, ast.ExceptHandler):
            if _empty_except_body(node.body):
                problems.append(f"empty except handler at line {node.lineno}")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                for marker in _COMMENT_MARKERS:
                    if marker in tok.string:
                        problems.append(f"comment marker {marker} at line {tok.start[0]}")
    except tokenize.TokenError:
        problems.append("source could not be tokenized for comment markers")
    return problems


def _module_file(base: Path, dotted: str) -> Path:
    rel = dotted.replace(".", "/")
    pkg_dir = base / rel
    if pkg_dir.is_dir():
        return pkg_dir / "__init__.py"
    return base / (rel + ".py")


def _frozen_rows_of_unit(lock: dict, unit: str) -> list[dict]:
    prefix_unit = unit
    rows = []
    for src, entry in lock.get("rows", {}).items():
        if src.split(":", 1)[0] == prefix_unit:
            rows.append(entry["row"])
    return rows


def check_surface_matches(target_source: str, target_module_name: str, target_package: str, frozen_rows: list[dict]) -> tuple[bool, list[str]]:
    expected = set()
    for row in frozen_rows:
        for target in row.get("targets", []):
            expected.add(target.split(":", 1)[1])
    info = inventory.analyze_module(target_source, target_module_name, target_package)
    actual = {sym.split(":", 1)[1] for sym in info["public_symbols"]}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    problems = []
    if missing:
        problems.append("missing from target surface: " + ", ".join(missing))
    if extra:
        problems.append("not in any frozen row's targets: " + ", ".join(extra))
    return (not problems), problems


def _absolute_import_bases(tree: ast.Module) -> set[str]:
    """Every module named by an absolute `import` or `from ... import`
    statement in `tree`. A relative import (`level > 0`) always resolves
    inside the importing module's own package, so it can never reach an
    unrelated top-level package and is not a candidate here. Deliberately
    independent of `inventory.analyze_module`'s `imports` field, which is
    filtered to one package boundary and would never surface a legacy
    import in the first place."""
    bases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bases.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                bases.add(node.module)
    return bases


def check_no_direct_legacy_import(tree: ast.Module, legacy_package: str) -> tuple[bool, list[str]]:
    bases = _absolute_import_bases(tree)
    offending = sorted(b for b in bases if b == legacy_package or b.startswith(legacy_package + "."))
    if offending:
        return False, [f"imports legacy module(s): {', '.join(offending)}"]
    return True, []


def check_target_self_contained(target_root: Path, target_package: str, target_module_name: str) -> tuple[bool, list[str]]:
    closure = inventory.import_closure(target_root, target_package, target_module_name)
    problems = []
    for mod in closure:
        path = _module_file(target_root, mod)
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
            info = inventory.analyze_module(source, mod, target_package)
        except (OSError, SyntaxError) as exc:
            problems.append(f"{mod}: could not be analyzed ({exc})")
            continue
        for flag_name in ("uncontrolled_input", "io", "dynamic_call"):
            flags = info.get(flag_name, [])
            if flags:
                problems.append(f"{mod}: {flag_name}: {', '.join(flags)}")
    return (not problems), problems


def check_protected_intact(root: Path) -> tuple[bool, list[str]]:
    problems = []

    inventory_path = root / "inventory.json"
    if _try_read_json(inventory_path) is None:
        problems.append("inventory.json is not readable")

    net_lock = _try_read_json(root / "net.lock.json") or {"units": {}}
    for unit_name, entry in net_lock.get("units", {}).items():
        net_path = root / "nets" / f"{unit_name}.json"
        if not net_path.is_file() or cm_common.sha256_file(net_path) != entry.get("net_sha256"):
            problems.append(f"nets/{unit_name}.json does not match net.lock.json")
        cases_path = root / "cases" / f"{unit_name}.json"
        if not cases_path.is_file() or cm_common.sha256_file(cases_path) != entry.get("cases_sha256"):
            problems.append(f"cases/{unit_name}.json does not match net.lock.json")

    registry_lock = _try_read_json(root / "registry.lock.json") or {"rows": {}}
    for src, entry in registry_lock.get("rows", {}).items():
        if cm_common.sha256_json(entry.get("row")) != entry.get("digest"):
            problems.append(f"registry.lock.json entry for {src} does not match its own digest")

    return (not problems), problems


def check_all_rows_frozen(inventory_doc: dict, lock: dict, unit: str) -> tuple[bool, list[str]]:
    info = inventory_doc.get("units", {}).get(unit, {})
    rows = lock.get("rows", {})
    missing = sorted(s for s in info.get("public_symbols", []) if s not in rows)
    if missing:
        return False, [f"no frozen row for: {', '.join(missing)}"]
    return True, []


def run_gate(root: Path, cfg: dict, unit: str) -> dict:
    checks: dict[str, bool] = {}
    problems: list[str] = []

    target_path = cm_common.target_file(root, cfg, unit)
    target_module_name = cm_common.target_module(cfg, unit)
    target_root = cm_common.resolved_paths(root, cfg)["target_root"]

    def _is_shim(path: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                first_line = fh.readline().rstrip("\n")
        except OSError:
            return False
        return first_line.startswith("# codebase-migrator: shim for ")

    target_present = target_path.is_file() and not _is_shim(target_path)
    checks["target_present"] = target_present
    if not target_present:
        problems.append(f"target_present: {target_path} is absent or is a shim")

    target_source = None
    tree = None
    if target_present:
        try:
            target_source = target_path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"compiles: could not read {target_path} ({exc})")
            checks["compiles"] = False
        else:
            try:
                compile(target_source, str(target_path), "exec")
                tree = ast.parse(target_source, filename=str(target_path))
                checks["compiles"] = True
            except SyntaxError as exc:
                problems.append(f"compiles: {exc}")
                checks["compiles"] = False
    else:
        checks["compiles"] = False

    inventory_doc = cm_common.read_json(root / "inventory.json", "inventory.json")
    lock = _try_read_json(root / "registry.lock.json") or {"schema": 1, "rows": {}}
    frozen_rows = _frozen_rows_of_unit(lock, unit)

    if target_source is not None and tree is not None:
        ok, ps = check_surface_matches(target_source, target_module_name, cfg["target_package"], frozen_rows)
        checks["surface_matches"] = ok
        problems.extend(f"surface_matches: {p}" for p in ps)

        stub_problems = check_no_stubs(target_source, tree)
        checks["no_stubs"] = not stub_problems
        problems.extend(f"no_stubs: {p}" for p in stub_problems)

        ok, ps = check_no_direct_legacy_import(tree, cfg["legacy_package"])
        checks["no_direct_legacy_import"] = ok
        problems.extend(f"no_direct_legacy_import: {p}" for p in ps)

        ok, ps = check_target_self_contained(target_root, cfg["target_package"], target_module_name)
        checks["target_self_contained"] = ok
        problems.extend(f"target_self_contained: {p}" for p in ps)
    else:
        for name in ("surface_matches", "no_stubs", "no_direct_legacy_import", "target_self_contained"):
            checks[name] = False
            problems.append(f"{name}: no readable target source")

    ok, ps = check_protected_intact(root)
    checks["protected_intact"] = ok
    problems.extend(f"protected_intact: {p}" for p in ps)

    ok, ps = check_all_rows_frozen(inventory_doc, lock, unit)
    checks["all_rows_frozen"] = ok
    problems.extend(f"all_rows_frozen: {p}" for p in ps)

    return {
        "ok": all(checks.values()),
        "unit": unit,
        "checks": checks,
        "problems": problems,
    }


def main() -> int:
    parser = cm_common.make_parser(prog="unit_gate.py")
    parser.add_argument("--root", required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    unit = cm_common.require_unit(root, args.unit)

    result = run_gate(root, cfg, unit)

    target_path = cm_common.target_file(root, cfg, unit)
    target_sha256 = cm_common.sha256_file(target_path) if target_path.is_file() else None
    key = ledger.cache_key(root, cfg, unit)
    key_sha256 = cm_common.sha256_json(key)
    report = dict(result)
    report["target_sha256"] = target_sha256
    report["cache_key"] = key
    report["key_sha256"] = key_sha256
    cm_common.atomic_write_json(cm_common.unit_run_dir(root, unit) / "r2.json", report)

    cm_common.emit(result)
    return cm_common.EXIT_OK if result["ok"] else cm_common.EXIT_FAIL


if __name__ == "__main__":
    cm_common.run_main(main)
