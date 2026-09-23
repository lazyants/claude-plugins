"""Tests for inventory.py (plan section 4.3): the static AST inventory."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "codebase-migrator" / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
import inventory  # noqa: E402


def _run(script_name: str, *args: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), *args],
        capture_output=True,
        text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else None
    return proc.returncode, payload, proc.stderr


def _write_migration_json(root: Path, legacy_root: Path, package: str, target_root: Path, **overrides) -> None:
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": package,
        "target_root": str(target_root),
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": "port",
        "coverage_floor_pct": 0,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }
    cfg.update(overrides)
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")


def _shop_root(tmp_path: Path) -> Path:
    """A durable root whose legacy tree is a private copy of the pinned shop
    fixture (never mutate the shared fixture in place)."""
    root = tmp_path / "root"
    root.mkdir()
    legacy = root / "legacy"
    shutil.copytree(FIXTURES_DIR / "legacy", legacy)
    _write_migration_json(root, legacy, "shop", root / "target")
    return root


# --- the pinned static verdict table (plan 5.1) -----------------------------

_EXPECTED_STATIC = {
    "shop.money": [],
    "shop.pricing": [],
    "shop.cart": [],
    "shop.stateful": [],
    "shop.closures": [],
    "shop.defaults": [],
    "shop.alias": [],
    "shop.audit": [],
    "shop.settings": [],
    "shop.tags": [],
    "shop.checkout": [],
    "shop.clock": ["call:time.time@stamp"],
    "shop.dynamic": ["getattr@call_named"],
}


def test_static_verdict_table_matches_plan(tmp_path):
    root = _shop_root(tmp_path)
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 0, stderr
    assert payload["ok"] is True
    assert payload["units"] == 13
    assert payload["eligible"] == 11

    data = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    units = data["units"]
    assert set(units) == set(_EXPECTED_STATIC)
    for unit, expected_reasons in _EXPECTED_STATIC.items():
        row = units[unit]
        assert row["eligible"] == (expected_reasons == []), unit
        assert row["ineligible_reasons"] == expected_reasons, unit
    assert "shop" not in units  # __init__.py is docstring-only: not a unit


def test_symbol_spans_match_fixture_line_numbers(tmp_path):
    root = _shop_root(tmp_path)
    code, _, stderr = _run("inventory.py", "--root", str(root))
    assert code == 0, stderr
    data = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    spans = data["units"]["shop.pricing"]["symbol_spans"]
    assert spans == {
        "shop.pricing:PricingError": [4, 5],
        "shop.pricing:apply_discount": [8, 11],
        "shop.pricing:normalize_items": [14, 15],
        "shop.pricing:dedupe": [18, 27],
        "shop.pricing:describe": [30, 32],
    }


def test_parse_error_exits_naming_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    legacy = root / "legacy"
    (legacy / "broken").mkdir(parents=True)
    (legacy / "broken" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (legacy / "broken" / "bad.py").write_text("def f(:\n", encoding="utf-8")
    _write_migration_json(root, legacy, "broken", root / "target")
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 1
    assert payload["ok"] is False
    assert "bad.py" in payload["file"]


def test_zero_units_exits_1(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    legacy = root / "legacy"
    (legacy / "empty").mkdir(parents=True)
    (legacy / "empty" / "__init__.py").write_text('"""just a docstring"""\n', encoding="utf-8")
    _write_migration_json(root, legacy, "empty", root / "target")
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 1
    assert payload["ok"] is False


# --- analyze_module: flag detection -----------------------------------------


def test_dunder_all_honoured(tmp_path):
    source = (
        "__all__ = ['a']\n\n"
        "def a():\n"
        "    return 1\n\n"
        "def b():\n"
        "    return 2\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert info["public_symbols"] == ["pkg.mod:a"]


def test_aliased_from_import_still_flagged():
    source = (
        "from time import time as now\n\n"
        "def f():\n"
        "    return now()\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:time.time@f" in info["uncontrolled_input"]


def test_value_alias_module_and_function_scope_and_default_arg():
    source = (
        "import time\n\n"
        "clock = time.time\n\n"
        "def use_module_clock():\n"
        "    return clock()\n\n"
        "def make():\n"
        "    clock2 = time.time\n"
        "    return clock2()\n\n"
        "def f(now=time.time):\n"
        "    return now()\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:time.time@use_module_clock" in info["uncontrolled_input"]
    assert "call:time.time@make" in info["uncontrolled_input"]
    assert "call:time.time@f" in info["uncontrolled_input"]


def test_dynamic_call_forms():
    source = (
        "import sys\n"
        "import importlib\n\n"
        "def g1():\n"
        "    return sys.modules['x']\n\n"
        "def g2():\n"
        "    sys.setprofile(None)\n\n"
        "def g3():\n"
        "    return importlib.import_module('x')\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "sys.modules@g1" in info["dynamic_call"]
    assert "sys.setprofile@g2" in info["dynamic_call"]
    assert "importlib.import_module@g3" in info["dynamic_call"]


def test_io_flag_on_open_call():
    source = (
        "def f(path):\n"
        "    return open(path)\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:open@f" in info["io"]


# --- imports field, function-local and relative imports ---------------------


def _build_pkgx(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    pkgx = base / "pkgx"
    pkgx.mkdir(parents=True)
    (pkgx / "__init__.py").write_text("", encoding="utf-8")
    (pkgx / "a.py").write_text("X = 1\n", encoding="utf-8")
    sub = pkgx / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "b.py").write_text(
        "from .. import a\n\n"
        "def use_relative():\n"
        "    return a.X\n\n"
        "def use_local():\n"
        "    from . import c\n"
        "    return c.VALUE\n"
    , encoding="utf-8")
    (sub / "c.py").write_text("VALUE = 2\n", encoding="utf-8")
    (sub / "toohigh.py").write_text("from ... import ghost\n", encoding="utf-8")
    return base


def test_imports_field_relative_and_function_local(tmp_path):
    base = _build_pkgx(tmp_path)
    source = (base / "pkgx" / "sub" / "b.py").read_text(encoding="utf-8")
    info = inventory.analyze_module(source, "pkgx.sub.b", "pkgx")
    assert "pkgx.a" in info["imports"]
    assert "pkgx.sub.c" in info["imports"]


def test_imports_field_from_pkg_import_mod():
    source = "from pkgx import a\n"
    info = inventory.analyze_module(source, "pkgx.d", "pkgx")
    assert "pkgx" in info["imports"]
    assert "pkgx.a" in info["imports"]


def test_import_closure_follows_relative_import_two_levels(tmp_path):
    base = _build_pkgx(tmp_path)
    closure = inventory.import_closure(base, "pkgx", "pkgx.sub.b")
    # `from .. import a` (two dots) reaches pkgx.a; `use_local`'s function-local
    # `from . import c` reaches pkgx.sub and pkgx.sub.c too.
    assert closure == sorted({"pkgx.sub.b", "pkgx", "pkgx.a", "pkgx.sub", "pkgx.sub.c"})


def test_import_closure_stops_at_package_boundary(tmp_path):
    base = _build_pkgx(tmp_path)
    closure = inventory.import_closure(base, "pkgx", "pkgx.sub.toohigh")
    assert closure == ["pkgx.sub.toohigh"]


def test_import_closure_reaches_helper_from_shop2_pricing():
    base = FIXTURES_DIR / "ports" / "reads_clock"
    closure = inventory.import_closure(base, "shop2", "shop2.pricing")
    assert "shop2._util" in closure
    assert "shop2.money" in closure


def test_analyze_module_flags_reads_clock_helper():
    source = (FIXTURES_DIR / "ports" / "reads_clock" / "shop2" / "_util.py").read_text(encoding="utf-8")
    info = inventory.analyze_module(source, "shop2._util", "shop2")
    assert any(flag.startswith("call:time.time@") for flag in info["uncontrolled_input"])
