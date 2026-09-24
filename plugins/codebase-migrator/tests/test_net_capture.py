"""Tests for net_capture.py: W3b/W4c golden-master capture and the dynamic
half of eligibility (plan section 4.7, tests owned by C)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import cm_common
import inventory
import net_capture

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
LEGACY_SRC = FIXTURES_DIR / "legacy" / "shop"
REGISTRY_SRC = FIXTURES_DIR / "registry.shop.json"
CASES_DIR = FIXTURES_DIR / "cases"


_created_legacy_siblings: list[Path] = []


def _register_legacy_sibling(path: Path) -> Path:
    """Record a sibling directory this process created outside `work_root`,
    so teardown removes EXACTLY the paths recorded here — never a glob
    sweep over `tests/.work/`, which is shared with every other pytest
    process that might be running concurrently (this session's or a
    teammate's). A name-derived glob is unsafe two ways: it can delete
    another process's still-in-use directory, and a path whose derived
    "paired root" name never exists at all (e.g. a "<uuid>-pkgf-legacy"
    sibling, whose non-existent "<uuid>-pkgf" pair is not this test's own
    `work_root`) reads as orphaned to EVERY process's sweep from the
    moment it's created, not just after this test finishes."""
    _created_legacy_siblings.append(path)
    return path


def _sibling_legacy_root(root: Path) -> Path:
    """A legacy source directory OUTSIDE `root` — `migration_validate`
    refuses a durable root that equals, contains, or is contained by
    `legacy_root` (plan 2.1), so it can never live nested under `root`."""
    return _register_legacy_sibling(root.parent / (root.name + "-legacy"))


@pytest.fixture(autouse=True)
def _cleanup_created_legacy_siblings():
    """`work_root` (conftest.py, owned by A) only removes its own
    `tests/.work/<uuid>/` directory; a sibling this file creates via
    `_register_legacy_sibling` is not conftest's to know about, so it is
    removed here — by the exact path recorded, never a directory glob."""
    yield
    while _created_legacy_siblings:
        path = _created_legacy_siblings.pop()
        shutil.rmtree(path, ignore_errors=True)


def _scaffold(root: Path, coverage_floor: int = 50) -> dict:
    legacy_root = _sibling_legacy_root(root)
    shutil.copytree(LEGACY_SRC, legacy_root / "shop", dirs_exist_ok=True)
    (root / "cases").mkdir(parents=True, exist_ok=True)
    (root / "nets").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "conventions.md").write_text("conventions\n")
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "shop",
        "target_root": "target",
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": "port",
        "coverage_floor_pct": coverage_floor,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }
    cm_common.atomic_write_json(root / "migration.json", cfg)
    inv = inventory.build_inventory(root, cfg)
    cm_common.atomic_write_json(root / "inventory.json", inv)
    return cfg


def _freeze_rows(root: Path, rows: list) -> None:
    cm_common.atomic_write_json(root / "registry.json", {"schema": 1, "rows": rows})
    lock = {"schema": 1, "rows": {r["source"]: {"row": r, "digest": cm_common.sha256_json(r)} for r in rows}}
    cm_common.atomic_write_json(root / "registry.lock.json", lock)


def _freeze_base_registry(root: Path, extra_rows=None) -> None:
    rows = json.loads(REGISTRY_SRC.read_text())["rows"]
    if extra_rows:
        rows = rows + extra_rows
    _freeze_rows(root, rows)


def _rows_for_all_public_symbols(inv: dict, unit: str, target_package: str, legacy_package: str) -> list:
    target_unit = target_package + unit[len(legacy_package):]
    rows = []
    for sym in inv["units"][unit]["public_symbols"]:
        name = sym.split(":", 1)[1]
        rows.append(
            {
                "source": sym,
                "cardinality": "one_to_one",
                "entry": f"{target_unit}:{name}",
                "targets": [f"{target_unit}:{name}"],
                "reason": None,
            }
        )
    return rows


def _put_cases(root: Path, unit: str, cases: list) -> None:
    cm_common.atomic_write_json(root / "cases" / f"{unit}.json", {"schema": 1, "cases": cases})


def _copy_cases_fixture(root: Path, unit: str) -> None:
    shutil.copy(CASES_DIR / f"{unit}.json", root / "cases" / f"{unit}.json")


# ---------------------------------------------------------------------------
# The pure comparison function
# ---------------------------------------------------------------------------


def test_compare_captures_is_pure_and_ignores_paths():
    common = {
        "status": "ok",
        "return": 1,
        "error": None,
        "receiver_after": None,
        "args_after": [],
        "kwargs_after": {},
        "stdout": "",
        "stderr": "",
    }
    a = [dict(common, case_id="c1"), dict(common, case_id="c2")]
    b = [dict(common, case_id="c1"), dict(common, case_id="c2", **{"return": 2})]
    assert net_capture.compare_captures(a, b) == ["c2"]
    assert net_capture.compare_captures(a, a) == []
    # covered_lines/route_files differ freely without affecting the comparison
    a2 = [dict(common, case_id="c1", covered_lines=[1, 2])]
    b2 = [dict(common, case_id="c1", covered_lines=[3, 4])]
    assert net_capture.compare_captures(a2, b2) == []


def test_net_capture_wires_the_ab_comparison_for_hash_order_nondeterminism(work_root, capsys):
    """`compare_captures` alone is a pure-function unit test: it proves
    nothing about whether `run()` actually calls it. A throwaway unit whose
    only nondeterminism is set iteration order under PYTHONHASHSEED (no
    static flag would ever catch this) proves the real A/B environments
    reach the verdict end to end, and that no net is written on refusal."""
    legacy_root = _sibling_legacy_root(work_root)
    (legacy_root / "oddpkg").mkdir(parents=True, exist_ok=True)
    (legacy_root / "oddpkg" / "__init__.py").write_text('"""Throwaway package."""\n')
    (legacy_root / "oddpkg" / "setmod.py").write_text(
        "def names():\n    return list({'alpha', 'beta', 'gamma', 'delta', 'eps'})\n"
    )
    (work_root / "cases").mkdir(parents=True, exist_ok=True)
    (work_root / "nets").mkdir(parents=True, exist_ok=True)
    (work_root / "runs").mkdir(parents=True, exist_ok=True)
    (work_root / "conventions.md").write_text("conventions\n")
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "oddpkg",
        "target_root": "target",
        "target_package": "oddpkg2",
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
    cm_common.atomic_write_json(work_root / "migration.json", cfg)
    inv = inventory.build_inventory(work_root, cfg)
    cm_common.atomic_write_json(work_root / "inventory.json", inv)
    # No static rule (uncontrolled_input/io/dynamic_call) exists for "uses a
    # set": the unit is statically eligible, so only the dynamic A/B
    # comparison can catch this.
    assert inv["units"]["oddpkg.setmod"]["eligible"] is True

    rows = _rows_for_all_public_symbols(inv, "oddpkg.setmod", cfg["target_package"], cfg["legacy_package"])
    _freeze_rows(work_root, rows)
    _put_cases(work_root, "oddpkg.setmod", [{"id": "n1", "call": "names", "args": []}])

    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "oddpkg.setmod")
    assert exc.value.code == cm_common.EXIT_FAIL

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["verdict"] == "nondeterministic"
    assert "n1" in payload["case_ids"]

    assert not (work_root / "nets" / "oddpkg.setmod.json").exists()
    lock_path = work_root / "net.lock.json"
    if lock_path.is_file():
        lock_doc = cm_common.read_json(lock_path, "net.lock.json")
        assert "oddpkg.setmod" not in lock_doc.get("units", {})


def test_harness_failure_still_runs_the_tamper_check(work_root, monkeypatch, capsys):
    """If `observe.run_harness` raises (HarnessFailure, a stage_trees I/O
    error, ...) the after-run `protected_digests` check must still run —
    the exception unwinding out of the capture loop must never skip it."""
    cfg = _scaffold(work_root)
    _freeze_base_registry(work_root)
    _copy_cases_fixture(work_root, "shop.pricing")

    tamper_path = Path(cfg["legacy_root"]) / "shop" / "TAMPERED.txt"

    def _fake_run_harness(job, stage, timeout_s=120):
        tamper_path.write_text("unexpected")
        raise net_capture.observe.HarnessFailure("forced failure for test")

    monkeypatch.setattr(net_capture.observe, "run_harness", _fake_run_harness)

    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert any("TAMPERED.txt" in t for t in payload.get("tampered", []))


# ---------------------------------------------------------------------------
# The three clean fixtures
# ---------------------------------------------------------------------------


def test_money_pricing_cart_net_with_coverage_and_determinism(work_root):
    cfg = _scaffold(work_root)
    _freeze_base_registry(work_root)
    for unit in ("shop.money", "shop.pricing", "shop.cart"):
        _copy_cases_fixture(work_root, unit)
        result = net_capture.run(work_root, cfg, unit)
        assert result["ok"] is True, (unit, result)
        assert result["verdict"] == "netted"
        assert result["coverage_pct"] == 100.0
        assert result["kept"] > 0

    net_doc = cm_common.read_json(work_root / "nets" / "shop.pricing.json", "nets")
    # legacy_closure is now keyed by inventory.closure_files' file paths
    # (relative to the staged legacy root), which include shop's own
    # ancestor-package __init__.py alongside the two real units.
    assert set(net_doc["legacy_closure"]) == {"shop/pricing.py", "shop/money.py", "shop/__init__.py"}

    # runs/<unit>/net_capture.json mirrors the stdout object on success too
    # (sandbox.py's `cases` dispatch reads its `uncovered_lines`).
    persisted = cm_common.read_json(work_root / "runs" / "shop.pricing" / "net_capture.json", "net_capture.json")
    assert persisted["ok"] is True
    assert persisted["uncovered_lines"] == []


def test_below_floor_refusal_still_persists_net_capture_json_with_uncovered_lines(work_root):
    cfg = _scaffold(work_root, coverage_floor=100)
    _freeze_base_registry(work_root)
    _put_cases(work_root, "shop.pricing", [{"id": "p1", "call": "apply_discount", "args": [100, 10]}])
    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL

    persisted = cm_common.read_json(work_root / "runs" / "shop.pricing" / "net_capture.json", "net_capture.json")
    assert persisted["ok"] is False
    assert persisted["uncovered_lines"]  # non-empty: exactly what a cases turn needs


def test_clock_is_refused_statically_before_any_capture(work_root):
    cfg = _scaffold(work_root)
    _freeze_base_registry(work_root)
    _put_cases(work_root, "shop.clock", [{"id": "k1", "call": "stamp", "args": ["x"]}])
    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "shop.clock")
    assert exc.value.code == cm_common.EXIT_FAIL


STATEFUL_CASES = {
    "shop.stateful": (
        [{"id": "s1", "call": "remember", "args": ["a", 1]}, {"id": "s2", "call": "bump", "args": []}],
        {"shop.stateful:_CACHE", "shop.stateful:_COUNT"},
    ),
    "shop.closures": ([{"id": "s1", "call": "step", "args": []}], {"shop.closures:step"}),
    "shop.defaults": ([{"id": "s1", "call": "track", "args": [1]}], {"shop.defaults:track"}),
    "shop.alias": ([{"id": "s1", "call": "put", "args": ["k", 1]}], {"shop.alias:_REG"}),
    "shop.audit": ([{"id": "s1", "call": "record", "args": ["e"]}], {"shop.audit:Audit"}),
    "shop.settings": ([{"id": "s1", "call": "set_rate", "args": [5]}], {"shop.settings:SETTINGS"}),
    "shop.tags": (
        [{"id": "s1", "call": "Tags.add", "init_args": [], "args": ["t"]}],
        {"shop.tags:Tags"},
    ),
    "shop.checkout": ([{"id": "s1", "call": "checkout", "args": [100, 10]}], {"shop.audit:Audit"}),
}


@pytest.mark.parametrize("unit", sorted(STATEFUL_CASES))
def test_each_stateful_fixture_is_refused_naming_the_key(work_root, capsys, unit):
    cfg = _scaffold(work_root)
    inv = cm_common.read_json(work_root / "inventory.json", "inventory.json")
    cases, expected_keys = STATEFUL_CASES[unit]
    extra_rows = _rows_for_all_public_symbols(inv, unit, cfg["target_package"], cfg["legacy_package"])
    _freeze_base_registry(work_root, extra_rows=extra_rows)
    _put_cases(work_root, unit, cases)

    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, unit)
    assert exc.value.code == cm_common.EXIT_FAIL

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["verdict"] == "stateful"
    all_changed = {key for keys in payload["state_changes"].values() for key in keys}
    assert expected_keys <= all_changed, (unit, all_changed)


def test_duplicate_case_ids_are_refused(work_root, capsys):
    cfg = _scaffold(work_root)
    _freeze_base_registry(work_root)
    _put_cases(
        work_root,
        "shop.money",
        [
            {"id": "m1", "call": "round_money", "args": [1.0]},
            {"id": "m1", "call": "round_money", "args": [2.0]},
        ],
    )
    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "shop.money")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "m1" in payload.get("duplicate_case_ids", [])


# ---------------------------------------------------------------------------
# Dropped symbols
# ---------------------------------------------------------------------------


def test_case_targeting_dropped_symbol_is_skipped_and_excluded_from_denominator(work_root):
    cfg = _scaffold(work_root)
    rows = json.loads(REGISTRY_SRC.read_text())["rows"]
    rows = [
        dict(r, cardinality="dropped", entry=None, targets=[], reason="test: unreferenced")
        if r["source"] == "shop.money:to_cents"
        else r
        for r in rows
    ]
    _freeze_rows(work_root, rows)
    _put_cases(
        work_root,
        "shop.money",
        [
            {"id": "m1", "call": "round_money", "args": [2.345]},
            {"id": "m2", "call": "to_cents", "args": [1.5]},
        ],
    )
    result = net_capture.run(work_root, cfg, "shop.money")
    assert result["ok"] is True

    inv = cm_common.read_json(work_root / "inventory.json", "inventory.json")
    span = inv["units"]["shop.money"]["symbol_spans"]["shop.money:to_cents"]
    dropped_lines = set(range(span[0], span[1] + 1))
    assert not (set(result["uncovered_lines"]) & dropped_lines)

    lock_doc = cm_common.read_json(work_root / "net.lock.json", "net.lock.json")
    assert lock_doc["units"]["shop.money"]["skipped_dropped_symbol"] == ["m2"]


def test_case_returning_unsupported_value_is_dropped_and_its_line_uncovered(work_root):
    cfg = _scaffold(work_root)
    (Path(cfg["legacy_root"]) / "shop" / "oddball.py").write_text(
        "def give_function():\n"
        "    return len\n\n\n"
        "def give_int():\n"
        "    return 1\n"
    )
    inv = inventory.build_inventory(work_root, cfg)
    cm_common.atomic_write_json(work_root / "inventory.json", inv)
    rows = _rows_for_all_public_symbols(inv, "shop.oddball", cfg["target_package"], cfg["legacy_package"])
    _freeze_rows(work_root, rows)
    _put_cases(
        work_root,
        "shop.oddball",
        [
            {"id": "f1", "call": "give_function", "args": []},
            {"id": "i1", "call": "give_int", "args": []},
        ],
    )
    result = net_capture.run(work_root, cfg, "shop.oddball")
    assert result["ok"] is True
    assert result["dropped"] == 1

    span = inv["units"]["shop.oddball"]["symbol_spans"]["shop.oddball:give_function"]
    return_line = span[1]
    assert return_line in result["uncovered_lines"]


# ---------------------------------------------------------------------------
# Coverage floor and full coverage on exotic shapes
# ---------------------------------------------------------------------------


def test_below_floor_lists_uncovered_lines(work_root):
    cfg = _scaffold(work_root, coverage_floor=100)
    _freeze_base_registry(work_root)
    # Only exercise part of shop.pricing: below a 100% floor.
    _put_cases(work_root, "shop.pricing", [{"id": "p1", "call": "apply_discount", "args": [100, 10]}])
    with pytest.raises(SystemExit) as exc:
        net_capture.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL


def test_fully_exercised_throwaway_unit_reaches_100_percent_at_100_floor(work_root):
    cfg = _scaffold(work_root, coverage_floor=100)
    (Path(cfg["legacy_root"]) / "shop" / "fully.py").write_text(
        "def identity_decorator(fn):\n"
        "    return fn\n\n\n"
        "@identity_decorator\n"
        "def decorated(x):\n"
        "    return x + 1\n\n\n"
        "def one_liner(x): return x * 2\n\n\n"
        "make_lambda = lambda x: x - 1\n\n\n"
        "def gen(n):\n"
        "    for i in range(n):\n"
        "        yield i\n\n\n"
        "def run_gen(n):\n"
        "    return list(gen(n))\n\n\n"
        "class Worker:\n"
        "    def run(self, x):\n"
        "        return x\n"
    )
    inv = inventory.build_inventory(work_root, cfg)
    cm_common.atomic_write_json(work_root / "inventory.json", inv)
    rows = _rows_for_all_public_symbols(inv, "shop.fully", cfg["target_package"], cfg["legacy_package"])
    _freeze_rows(work_root, rows)
    _put_cases(
        work_root,
        "shop.fully",
        [
            {"id": "c1", "call": "decorated", "args": [1]},
            {"id": "c2", "call": "one_liner", "args": [2]},
            {"id": "c3", "call": "make_lambda", "args": [3]},
            {"id": "c4", "call": "run_gen", "args": [2]},
            {"id": "c5", "call": "Worker.run", "init_args": [], "args": [5]},
        ],
    )
    result = net_capture.run(work_root, cfg, "shop.fully")
    assert result["ok"] is True, result
    assert result["coverage_pct"] == 100.0
    assert result["uncovered_lines"] == []
