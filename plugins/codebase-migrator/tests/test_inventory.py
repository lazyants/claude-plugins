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
    fixture (never mutate the shared fixture in place). legacy_root is a
    SIBLING of the durable root, never nested under it: plan 2.1's root
    safety rules refuse a durable root that equals, contains, or is
    contained by legacy_root."""
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
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
    legacy = tmp_path / "legacy"
    (legacy / "broken").mkdir(parents=True)
    (legacy / "broken" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (legacy / "broken" / "bad.py").write_text("def f(:\n", encoding="utf-8")
    _write_migration_json(root, legacy, "broken", root / "target")
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 1
    assert payload["ok"] is False
    assert "bad.py" in payload["file"]


def test_broken_init_py_exits_naming_file(tmp_path):
    # _is_init_unit must not swallow a SyntaxError: a syntax-invalid
    # __init__.py has to fail the same named-parse-error way as any other
    # file, not be silently treated as "not a unit" (which would just drop
    # it from the inventory with no error at all).
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    (legacy / "brokeninit").mkdir(parents=True)
    (legacy / "brokeninit" / "__init__.py").write_text("def f(:\n", encoding="utf-8")
    _write_migration_json(root, legacy, "brokeninit", root / "target")
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 1
    assert payload["ok"] is False
    assert "__init__.py" in payload["file"]


def test_zero_units_exits_1(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    (legacy / "empty").mkdir(parents=True)
    (legacy / "empty" / "__init__.py").write_text('"""just a docstring"""\n', encoding="utf-8")
    _write_migration_json(root, legacy, "empty", root / "target")
    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 1
    assert payload["ok"] is False


def test_static_eligibility_propagates_through_executable_ancestor_package(tmp_path):
    # Importing "pkgz.mod" always runs pkgz/__init__.py first. If that
    # ancestor package is itself an executable unit with a flag (review
    # round 2, finding 2), "pkgz.mod" must be ineligible too, naming the
    # ancestor — even though pkgz.mod itself has zero flags and no explicit
    # import of "pkgz" in its own imports_units.
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    pkgz = legacy / "pkgz"
    pkgz.mkdir(parents=True)
    (pkgz / "__init__.py").write_text(
        "import os\n\nVALUE = os.environ.get('X')\n", encoding="utf-8"
    )
    (pkgz / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _write_migration_json(root, legacy, "pkgz", root / "target")

    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 0, stderr
    data = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    assert data["units"]["pkgz"]["eligible"] is False
    mod_row = data["units"]["pkgz.mod"]
    assert mod_row["imports_units"] == []  # no explicit import of the ancestor
    assert mod_row["eligible"] is False
    assert "ineligible dependency: pkgz" in mod_row["ineligible_reasons"]


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


def test_pathlib_path_constructor_chain_flagged_on_unexercised_branch():
    # `_dotted` cannot see through a CALL receiver, so `Path(p).write_text`
    # needs its own resolution path (review round 2, finding 1). The write
    # sits behind an `if` branch that never runs (flag is False) — this is
    # static analysis, so the flag must fire regardless of which branch a
    # real execution would take.
    source = (
        "from pathlib import Path\n\n"
        "def f(p, flag):\n"
        "    if flag:\n"
        "        Path(p).write_text('x')\n"
        "    else:\n"
        "        return None\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:pathlib.Path.write_text@f" in info["io"]


def test_pathlib_path_constructor_chain_other_write_methods_and_forms():
    source = (
        "import pathlib\n"
        "from pathlib import Path\n\n"
        "def g(p):\n"
        "    pathlib.Path(p).unlink()\n\n"
        "def h(p):\n"
        "    return Path(p).rmdir()\n\n"
        "def i(p):\n"
        "    return Path(p).symlink_to('other')\n\n"
        "def j(p):\n"
        "    return Path(p).read_text()\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:pathlib.Path.unlink@g" in info["io"]
    assert "call:pathlib.Path.rmdir@h" in info["io"]
    assert "call:pathlib.Path.symlink_to@i" in info["io"]
    # a read-only method must not be flagged
    assert not any("read_text" in flag for flag in info["io"])


def test_pathlib_write_method_flagged_on_any_receiver_on_unexercised_branch():
    # review round 3, finding 2: `p = Path(x); p.write_text(...)` never
    # resolves `p` to "pathlib.Path" (no type inference), so the
    # constructor-chain check alone misses it. Once the module imports
    # pathlib in any form, the method name alone is enough — and the write
    # sits behind an `if` that never runs, proving this is static.
    source = (
        "from pathlib import Path\n\n"
        "def f(x, flag):\n"
        "    p = Path(x)\n"
        "    if flag:\n"
        "        p.write_text('y')\n"
        "    else:\n"
        "        return None\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:write_text@f" in info["io"]


def test_pathlib_str_replace_not_flagged_by_name_alone():
    # `replace`, `rename` and `open` are deliberately excluded from the
    # any-receiver pre-filter: str.replace is common enough that flagging it
    # by name alone would false-positive constantly.
    source = (
        "import pathlib\n\n"
        "def g(s):\n"
        "    return s.replace('a', 'b')\n\n"
        "def h(s, new):\n"
        "    return s.rename(new)\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert not any("replace" in flag for flag in info["io"])
    assert not any("rename" in flag for flag in info["io"])


def test_environ_subscript_read_flagged_as_uncontrolled_reference():
    # A bare subscript read is not a call, so the pre-existing "calls
    # resolving to os.environ*" rule alone misses it (this is exactly what a
    # `return os.environ["CM_MODE"]` reviewer probe caught).
    source = (
        "import os\n\n"
        "def f():\n"
        "    return os.environ['CM_MODE']\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert any(flag.startswith("ref:os.environ@f") for flag in info["uncontrolled_input"])


def test_environ_flagged_through_import_alias():
    source = (
        "from os import environ\n\n"
        "def g():\n"
        "    return environ.get('CM_MODE')\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "call:os.environ.get@g" in info["uncontrolled_input"]
    assert "ref:os.environ@g" in info["uncontrolled_input"]


def test_getenv_reference_flagged_even_when_not_called():
    source = (
        "import os\n\n"
        "def h():\n"
        "    fn = os.getenv\n"
        "    return fn\n"
    )
    info = inventory.analyze_module(source, "pkg.mod", "pkg")
    assert "ref:os.getenv@h" in info["uncontrolled_input"]


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


def test_imported_symbols_from_module_bound_attribute_access(tmp_path):
    # review round 2, finding 3: `from pkg import M` (no direct
    # `from pkg.M import name`) followed by `M.name` used to record NO
    # imported symbol at all — --with-imported would then freeze nothing
    # for M, and bridge could never shim it. Also covers the
    # `import pkg.M as M` aliased form.
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    pkg = legacy / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "money.py").write_text("def round_money(x):\n    return x\n", encoding="utf-8")
    (pkg / "pricing.py").write_text(
        "from pkg import money\n\n"
        "def apply(x):\n"
        "    return money.round_money(x)\n",
        encoding="utf-8",
    )
    (pkg / "cart.py").write_text(
        "import pkg.money as m\n\n"
        "def total(x):\n"
        "    return m.round_money(x)\n",
        encoding="utf-8",
    )
    _write_migration_json(root, legacy, "pkg", root / "target")

    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 0, stderr
    data = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    assert data["units"]["pkg.pricing"]["imported_symbols"] == ["pkg.money:round_money"]
    assert data["units"]["pkg.cart"]["imported_symbols"] == ["pkg.money:round_money"]


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


# --- closure_files: every file Python's own import machinery executes -----


def test_closure_files_includes_the_package_root_init():
    # review round 3, finding 1: shop2/pricing.py never itself imports
    # shop2/__init__.py by name, but importing shop2.pricing always runs it
    # first. A closure built only from discovered units silently misses it.
    base = FIXTURES_DIR / "ports" / "good"
    files = inventory.closure_files(base, "shop2", "shop2.pricing")
    assert files == ["shop2/__init__.py", "shop2/money.py", "shop2/pricing.py"]


def test_closure_files_includes_every_ancestor_init_nested(tmp_path):
    base = tmp_path / "base"
    pkg = base / "pkgn"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""top docstring only"""\n', encoding="utf-8")
    (sub / "__init__.py").write_text('"""sub docstring only"""\n', encoding="utf-8")
    (sub / "leaf.py").write_text("X = 1\n", encoding="utf-8")

    files = inventory.closure_files(base, "pkgn", "pkgn.sub.leaf")
    # every ancestor's __init__.py is included, docstring-only or not —
    # neither "pkgn" nor "pkgn.sub" is a discovered unit at all.
    assert files == ["pkgn/__init__.py", "pkgn/sub/__init__.py", "pkgn/sub/leaf.py"]


def test_closure_files_includes_docstring_only_init_even_when_unrelated(tmp_path):
    base = tmp_path / "base"
    pkg = base / "pkgo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""nothing but a docstring"""\n', encoding="utf-8")
    (pkg / "mod.py").write_text("Y = 1\n", encoding="utf-8")

    files = inventory.closure_files(base, "pkgo", "pkgo.mod")
    assert "pkgo/__init__.py" in files
    assert files == ["pkgo/__init__.py", "pkgo/mod.py"]


# --- is_package: a package unit's relative import resolves against itself --


def test_analyze_module_package_init_resolves_relative_import_against_itself():
    source = (
        "from . import helper\n\n"
        "def use():\n"
        "    return helper.VALUE\n"
    )
    # A NESTED package unit ("pkgy.sub", an executable __init__.py): its own
    # __package__ is itself, not its parent, so `from . import helper` must
    # reach pkgy.sub.helper, never pkgy.helper.
    as_package = inventory.analyze_module(source, "pkgy.sub", "pkgy", is_package=True)
    as_module = inventory.analyze_module(source, "pkgy.sub", "pkgy", is_package=False)
    assert "pkgy.sub.helper" in as_package["imports"]
    assert "pkgy.helper" in as_module["imports"]
    assert "pkgy.sub.helper" not in as_module["imports"]


def test_build_inventory_resolves_package_init_relative_import(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    pkgy = legacy / "pkgy"
    pkgy.mkdir(parents=True)
    (pkgy / "__init__.py").write_text('"""namespace only"""\n', encoding="utf-8")
    sub = pkgy / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text(
        "from . import helper\n\n"
        "def use():\n"
        "    return helper.VALUE\n",
        encoding="utf-8",
    )
    (sub / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    _write_migration_json(root, legacy, "pkgy", root / "target")

    code, payload, stderr = _run("inventory.py", "--root", str(root))
    assert code == 0, stderr
    data = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    row = data["units"]["pkgy.sub"]
    # Without is_package this would resolve to "pkgy.helper" (a file that
    # does not exist under the legacy tree) and get silently dropped from
    # imports_units — the real dependency on pkgy.sub.helper would vanish.
    assert row["imports_units"] == ["pkgy.sub.helper"]
    # `helper.VALUE` is a module-bound attribute access (plan review round
    # 2, finding 3): the module-alias walk records it as a real symbol use.
    assert row["imported_symbols"] == ["pkgy.sub.helper:VALUE"]
