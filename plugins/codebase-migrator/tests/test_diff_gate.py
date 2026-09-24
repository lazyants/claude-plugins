"""Tests for diff_gate.py: R3, the differential gate (plan section 4.8,
tests owned by C)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import cm_common
import diff_gate
import inventory
import net_capture
import observe

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
LEGACY_SRC = FIXTURES_DIR / "legacy" / "shop"
REGISTRY_SRC = FIXTURES_DIR / "registry.shop.json"
CASES_DIR = FIXTURES_DIR / "cases"
PORTS_DIR = FIXTURES_DIR / "ports"
SCRIPTS_DIR = Path(cm_common.__file__).resolve().parent


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
    moment it's created, not just after this test finishes — that was the
    actual cause of an intermittent "tampered outside the sandbox" failure
    seen under concurrent runs."""
    _created_legacy_siblings.append(path)
    return path


def _sibling_legacy_root(root: Path) -> Path:
    """A legacy source directory OUTSIDE `root` — `migration_validate`
    refuses a durable root that equals, contains, or is contained by
    `legacy_root` (plan 2.1), so it can never live nested under `root`.
    This matters even for tests that call `run()` directly (bypassing
    `cm_common.load_config()`): any test that also drives a script as a
    real subprocess goes through that validation, so every test here uses
    the same valid layout rather than two different ones."""
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


def _freeze_base_registry(root: Path) -> None:
    rows = json.loads(REGISTRY_SRC.read_text())["rows"]
    _freeze_rows(root, rows)


def _put_cases(root: Path, unit: str, cases: list) -> None:
    cm_common.atomic_write_json(root / "cases" / f"{unit}.json", {"schema": 1, "cases": cases})


def _copy_cases_fixture(root: Path, unit: str) -> None:
    shutil.copy(CASES_DIR / f"{unit}.json", root / "cases" / f"{unit}.json")


def _capture_pricing(root: Path, cfg: dict) -> None:
    _freeze_base_registry(root)
    _copy_cases_fixture(root, "shop.pricing")
    result = net_capture.run(root, cfg, "shop.pricing")
    assert result["ok"] is True, result


def _install_port(root: Path, variant: str) -> None:
    target = root / "target" / "shop2"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(PORTS_DIR / variant / "shop2", target, ignore=shutil.ignore_patterns("__pycache__"))


def test_harness_failure_still_runs_the_tamper_check(work_root, monkeypatch, capsys):
    """If `observe.run_harness` raises (HarnessFailure, a stage_trees I/O
    error, ...) the after-run `protected_digests` check must still run —
    the exception unwinding out of the replay loop must never skip it."""
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    tamper_path = Path(cfg["legacy_root"]) / "shop" / "TAMPERED.txt"

    def _fake_run_harness(job, stage, timeout_s=120):
        tamper_path.write_text("unexpected")
        raise diff_gate.observe.HarnessFailure("forced failure for test")

    monkeypatch.setattr(diff_gate.observe, "run_harness", _fake_run_harness)

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert any("TAMPERED.txt" in t for t in payload.get("tampered", []))


# ---------------------------------------------------------------------------
# The good port
# ---------------------------------------------------------------------------


def test_good_port_passes_with_equal_nonzero_counts_both_envs(work_root):
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert result["ok"] is True, result
    assert result["cases_in_corpus"] == result["cases_executed"] == result["cases_counted"] == 8
    assert result["mismatch_count"] == 0
    assert result["mismatches"] == []

    r3 = cm_common.read_json(work_root / "runs" / "shop.pricing" / "r3.json", "r3.json")
    assert r3["ok"] is True
    assert "cache_key" in r3 and "key_sha256" in r3 and "target_sha256" in r3


def test_ancestor_package_init_is_allowed_but_units_own_legacy_file_is_not(work_root):
    """The legacy `shop` package's own `__init__.py` is not a unit (docstring
    only), yet importing a dependency reached through a shim always
    initializes it first — the route rule must not treat that mechanical
    side effect as a violation. `good`'s pricing.py imports `shop2.money`,
    not legacy `shop.*`, so with a real money PORT that ancestor init never
    even appears on the route — a prior version of this test asserted its
    absence from `route_violations`, which held vacuously. Shimming money
    instead forces `legacy/shop/__init__.py` onto the route for real, so
    this proves the exemption actually distinguishes cases rather than
    the ancestor init simply never showing up."""
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)

    target_dir = work_root / "target" / "shop2"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text("")
    (target_dir / "money.py").write_text(
        "# codebase-migrator: shim for shop.money\nfrom shop.money import round_money, to_cents\n"
    )
    shutil.copy(PORTS_DIR / "good" / "shop2" / "pricing.py", target_dir / "pricing.py")

    good_result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert good_result["ok"] is True, good_result
    assert "shop.money" in good_result["crossed_shims"]
    assert not any(
        "legacy/shop/__init__.py" in rv["reason"] for rv in good_result["route_violations"]
    )

    # Confirm directly (not just by absence-of-violation) that the ancestor
    # init really was on the route: import shop2.pricing alone (no case
    # needed) already pulls in the shim, which pulls in legacy shop.money,
    # which pulls in legacy shop's own package __init__.py.
    with tempfile.TemporaryDirectory(prefix="probe-stage-") as tmp:
        stage = Path(tmp)
        staged = observe.stage_trees(work_root, cfg, stage)
        probe_job = {
            "mode": "replay",
            "env": "A",
            "preload": [],
            "stage_root": str(stage),
            "sys_path": [str(staged["target"]), str(staged["legacy"])],
            "module": "shop2.pricing",
            "calls": {},
            "cases": [],
            "trace_file": None,
        }
        probe = observe.run_harness(probe_job, stage=stage, timeout_s=30)
    route_files = probe["import_route_files"] or []
    assert "legacy/shop/__init__.py" in route_files

    live_legacy_root = cm_common.resolved_paths(work_root, cfg)["legacy_root"]
    live_closure_files = inventory.closure_files(live_legacy_root, cfg["legacy_package"], "shop.pricing")
    unit_own_rel = "legacy/shop/pricing.py"
    allowed_legacy_rels = {f"legacy/{rel}" for rel in live_closure_files} - {unit_own_rel}
    assert diff_gate._route_violations(route_files, unit_own_rel, allowed_legacy_rels) == []

    # The discriminating half: `allowed_legacy_rels` is a closed set built
    # from `inventory.closure_files` — a path that is not in it, whatever
    # it is (a real unrelated unit or a made-up one), is still a violation
    # when reached. No separate "is this a real unit" lookup remains to
    # accidentally exempt something outside the closure.
    executable_ancestor_route = ["target/shop2/pricing.py", "legacy/shop/groupA/__init__.py"]
    executable_ancestor_violations = diff_gate._route_violations(
        executable_ancestor_route, unit_own_rel, allowed_legacy_rels
    )
    assert any("outside the dependency closure" in v for v in executable_ancestor_violations)

    # And the negative half: a port that reaches its OWN legacy file is
    # still refused, whatever else is on the route.
    _install_port(work_root, "back_to_legacy")
    reach_result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert reach_result["ok"] is False
    assert any(
        "reached the unit's own legacy file" in rv["reason"] for rv in reach_result["route_violations"]
    )


def test_appending_a_case_after_capture_refuses_on_cases_digest(work_root):
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    doc = cm_common.read_json(work_root / "cases" / "shop.pricing.json", "cases")
    doc["cases"].append({"id": "p9", "call": "apply_discount", "args": [50, 20]})
    cm_common.atomic_write_json(work_root / "cases" / "shop.pricing.json", doc)

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL


def test_duplicate_case_ids_in_the_net_are_refused(work_root):
    """`diff_gate` builds `cases_by_id`/`legacy_by_id` dicts keyed by
    case_id: a repeated id in the net would silently overwrite an earlier
    observation while every count still agrees — a false pass. Corrupt an
    already-captured net directly (net_capture itself now refuses to ever
    produce one) and confirm diff_gate refuses before doing any replay."""
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    net_path = work_root / "nets" / "shop.pricing.json"
    net_doc = cm_common.read_json(net_path, "net")
    net_doc["observations"].append(dict(net_doc["observations"][0]))
    cm_common.atomic_write_json(net_path, net_doc)
    net_sha256 = cm_common.sha256_file(net_path)
    lock_doc = cm_common.read_json(work_root / "net.lock.json", "lock")
    lock_doc["units"]["shop.pricing"]["net_sha256"] = net_sha256
    cm_common.atomic_write_json(work_root / "net.lock.json", lock_doc)

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL


def test_shim_as_target_is_refused(work_root):
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    target_dir = work_root / "target" / "shop2"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text("")
    (target_dir / "pricing.py").write_text(
        "# codebase-migrator: shim for shop.pricing\nfrom shop.pricing import apply_discount\n"
    )
    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL


# ---------------------------------------------------------------------------
# Legacy drift, and its recovery recipe
# ---------------------------------------------------------------------------


def test_legacy_dependency_drift_refuses_naming_it_then_recovers(work_root, capsys):
    # This test drives inventory.py/ledger.py as real subprocesses below,
    # which go through cm_common.load_config()'s migration_validate check
    # (unlike run() called directly) — _scaffold()'s sibling legacy_root
    # already satisfies it.
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")
    assert diff_gate.run(work_root, cfg, "shop.pricing")["ok"] is True

    money_path = Path(cfg["legacy_root"]) / "shop" / "money.py"
    money_path.write_text(money_path.read_text().replace("round(x + 0.0, 2)", "round(x + 0.0, 3)"))

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "shop/money.py" in payload.get("changed_legacy_files", [])

    # Execute the printed recovery recipe for real: inventory.py, then
    # ledger.py accept-drift, then net_capture.py.
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "inventory.py"), "--root", str(work_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "ledger.py"),
            "accept-drift",
            "--root",
            str(work_root),
            "--unit",
            "shop.pricing",
            "--operator",
            "test",
            "--reason",
            "rounding precision widened",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _copy_cases_fixture(work_root, "shop.pricing")
    recapture = net_capture.run(work_root, cfg, "shop.pricing")
    assert recapture["ok"] is True

    final = diff_gate.run(work_root, cfg, "shop.pricing")
    assert final["ok"] is True, final


# ---------------------------------------------------------------------------
# Each C-owned port variant fails for its own named reason, and only that one
# ---------------------------------------------------------------------------


def _has_route_reason(result, case_id, needle):
    return any(
        rv["case_id"] == case_id and needle in rv["reason"] for rv in result["route_violations"]
    )


def _has_mismatch(result, case_id, channel_substr):
    return any(
        m["case_id"] == case_id and channel_substr in m["channel"] for m in result["mismatches"]
    )


def _has_denied(result, needle):
    return any(needle in d for d in result["denied"])


VARIANT_CHECKS = {
    "aliasing_copy": lambda r: (
        _has_mismatch(r, "p6", "return")
        and not r["route_violations"]
        and not r["state_changes"]
    ),
    "no_inplace": lambda r: (
        _has_mismatch(r, "p5", "args_after") and not r["route_violations"] and not r["state_changes"]
    ),
    "wrong_exception": lambda r: (
        _has_mismatch(r, "p3", "error") and not r["route_violations"] and not r["state_changes"]
    ),
    "back_to_legacy": lambda r: _has_route_reason(r, "p7", "own legacy file"),
    "import_time_table": lambda r: (
        r["cases_counted"] == 0 and any("own legacy file" in rv["reason"] for rv in r["route_violations"])
    ),
    "profiler_off": lambda r: _has_denied(r, "tamper:sys.setprofile"),
    "writes_on_import": lambda r: r["cases_executed"] == 0 and r["cases_in_corpus"] > 0,
    "module_cache": lambda r: any(
        "_memo" in key for keys in r["state_changes"].values() for key in keys
    ),
}


@pytest.mark.parametrize("variant", sorted(VARIANT_CHECKS))
def test_each_variant_fails_for_its_own_reason(work_root, variant):
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, variant)

    result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert result["ok"] is False, (variant, result)
    assert VARIANT_CHECKS[variant](result), (variant, result)


def test_main_module_tampering_cannot_hide_a_legacy_route(work_root):
    """A port that reaches into `sys.modules["__main__"]` — the harness
    itself, when run as a subprocess — and pokes at whatever it finds there
    before delegating to legacy code must still have that legacy call show
    up on the route. This reproduces the exact shape of a real vulnerability
    (a module-level `_trace_state["suspended"]` flag reachable this way let
    a port silence the tracer around a legacy call with the tamper count
    staying clean) and confirms it is now a no-op: `_trace_state` no longer
    exists, and the harness's route/coverage sets are plain local variables
    inside the tracer's own closure, never module attributes reachable at
    all. This must not rely on R2 (unit_gate's dynamic_call check) — it
    asserts directly on R3's own route detection."""
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)

    target_dir = work_root / "target" / "shop2"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text("")
    shutil.copy(PORTS_DIR / "good" / "shop2" / "money.py", target_dir / "money.py")
    (target_dir / "pricing.py").write_text(
        "import importlib\n"
        "import sys\n\n\n"
        "from shop2.money import round_money\n\n\n"
        "class PricingError(ValueError):\n"
        "    pass\n\n\n"
        "def apply_discount(price, pct):\n"
        "    if pct < 0 or pct > 100:\n"
        "        raise PricingError(\"pct out of range\")\n"
        "    return round_money(price * (100 - pct) / 100)\n\n\n"
        "def normalize_items(items):\n"
        "    items.sort()\n\n\n"
        "def dedupe(items):\n"
        "    main_mod = sys.modules.get(\"__main__\")\n"
        "    trace_state = getattr(main_mod, \"_trace_state\", None)\n"
        "    if isinstance(trace_state, dict):\n"
        "        trace_state[\"suspended\"] = True\n"
        "    return importlib.import_module(\"shop.pricing\").dedupe(items)\n\n\n"
        "def describe(price):\n"
        "    print(\"price:\", price)\n"
        "    return str(price)\n"
    )

    result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert result["ok"] is False
    assert any(
        "reached the unit's own legacy file" in rv["reason"] for rv in result["route_violations"]
    )


# ---------------------------------------------------------------------------
# crossed_shims and bug_for_bug_with_exceptions
# ---------------------------------------------------------------------------


def test_crossed_shims_nonempty_when_pricing_is_still_a_shim(work_root):
    cfg = _scaffold(work_root)
    _freeze_base_registry(work_root)
    _copy_cases_fixture(work_root, "shop.cart")
    result = net_capture.run(work_root, cfg, "shop.cart")
    assert result["ok"] is True, result

    target_dir = work_root / "target" / "shop2"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text("")
    shutil.copy(PORTS_DIR / "good" / "shop2" / "money.py", target_dir / "money.py")
    (target_dir / "pricing.py").write_text(
        "# codebase-migrator: shim for shop.pricing\n"
        "from shop.pricing import apply_discount, normalize_items, dedupe, describe, PricingError\n"
    )
    shutil.copy(PORTS_DIR / "good" / "shop2" / "cart.py", target_dir / "cart.py")

    gate_result = diff_gate.run(work_root, cfg, "shop.cart")
    assert "shop.pricing" in gate_result["crossed_shims"], gate_result
    assert gate_result["ok"] is True, gate_result


def test_bug_for_bug_with_exceptions_rejects_a_case_matching_legacy(work_root):
    cfg = _scaffold(work_root)
    cfg["fidelity_policy"] = "bug_for_bug_with_exceptions"
    cm_common.atomic_write_json(work_root / "migration.json", cfg)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    net_doc = cm_common.read_json(work_root / "nets" / "shop.pricing.json", "nets")
    p1 = next(o for o in net_doc["observations"] if o["case_id"] == "p1")
    assert p1["return"] == {"$float": "90.0"}

    exceptions_doc = {
        "schema": 1,
        "cases": {
            "shop.pricing/p1": {"expected": {"return": {"$float": "90.5"}}, "reason": "test: declared fix"}
        },
    }
    cm_common.atomic_write_json(work_root / "exceptions.json", exceptions_doc)

    result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert result["ok"] is False
    assert any(m["case_id"] == "p1" for m in result["mismatches"])


def test_bug_for_bug_with_exceptions_rejects_a_no_op_declaration(work_root, capsys):
    """A declared `expected` identical to the legacy observation is a no-op:
    a target that never fixed the bug (still matches legacy) would also
    match this "expectation" — must be refused before any replay runs."""
    cfg = _scaffold(work_root)
    cfg["fidelity_policy"] = "bug_for_bug_with_exceptions"
    cm_common.atomic_write_json(work_root / "migration.json", cfg)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")

    net_doc = cm_common.read_json(work_root / "nets" / "shop.pricing.json", "nets")
    p1 = next(o for o in net_doc["observations"] if o["case_id"] == "p1")
    assert p1["return"] == {"$float": "90.0"}

    exceptions_doc = {
        "schema": 1,
        "cases": {
            "shop.pricing/p1": {"expected": {"return": p1["return"]}, "reason": "test: no-op"}
        },
    }
    cm_common.atomic_write_json(work_root / "exceptions.json", exceptions_doc)

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "p1" in payload.get("no_op_exception_ids", [])


def test_ancestor_init_helper_reached_through_a_shim_is_not_a_false_route_violation(work_root):
    """B's `inventory.closure_files` fixpoint fix: a package's own
    `__init__.py` doing `from . import helpers` puts `helpers.py` in the
    closure of EVERY unit under that package (any import of any sibling
    always runs the ancestor init first), not only of whatever module
    directly imports `helpers`. Before the fix, `helpers.py` was missing
    from a faithfully-ported unit's closure whenever the reach to it came
    through a SHIMMED DEPENDENCY's ancestor init rather than through the
    unit's own imports — producing a false "outside the dependency
    closure" route violation for a completely faithful port."""
    legacy_root = _register_legacy_sibling(work_root.parent / (work_root.name + "-pkgf-legacy"))
    pkg_dir = legacy_root / "pkgf"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("from . import helpers\n")
    (pkg_dir / "helpers.py").write_text("def helper_fn():\n    return 1\n")
    (pkg_dir / "dep.py").write_text("def dep_fn(x):\n    return x * 2\n")
    (pkg_dir / "mainmod.py").write_text(
        "from pkgf.dep import dep_fn\n\n\ndef apply(x):\n    return dep_fn(x) + 1\n"
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
        "legacy_package": "pkgf",
        "target_root": "target",
        "target_package": "pkgf2",
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
    assert inv["units"]["pkgf.mainmod"]["eligible"] is True

    rows = [
        {
            "source": "pkgf.mainmod:apply",
            "cardinality": "one_to_one",
            "entry": "pkgf2.mainmod:apply",
            "targets": ["pkgf2.mainmod:apply"],
            "reason": None,
        },
        {
            "source": "pkgf.dep:dep_fn",
            "cardinality": "one_to_one",
            "entry": "pkgf2.dep:dep_fn",
            "targets": ["pkgf2.dep:dep_fn"],
            "reason": None,
        },
    ]
    _freeze_rows(work_root, rows)

    _put_cases(work_root, "pkgf.mainmod", [{"id": "c1", "call": "apply", "args": [2]}])
    capture_result = net_capture.run(work_root, cfg, "pkgf.mainmod")
    assert capture_result["ok"] is True, capture_result

    target_dir = work_root / "target" / "pkgf2"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text("")
    (target_dir / "dep.py").write_text(
        "# codebase-migrator: shim for pkgf.dep\nfrom pkgf.dep import dep_fn\n"
    )
    # mainmod itself is a faithful, non-shim port; dep is the shimmed
    # dependency whose reach (through the shim, into legacy pkgf.dep) is
    # what forces legacy pkgf's ancestor __init__ — and thus helpers.py —
    # onto the route.
    (target_dir / "mainmod.py").write_text(
        "from pkgf2.dep import dep_fn\n\n\ndef apply(x):\n    return dep_fn(x) + 1\n"
    )

    result = diff_gate.run(work_root, cfg, "pkgf.mainmod")
    assert result["ok"] is True, result
    assert result["route_violations"] == []
    assert "pkgf.dep" in result["crossed_shims"]

    # Confirm directly (not just via absence-of-violation) that helpers.py
    # really was on the route — proving the "no violation" assertion above
    # is not vacuously true because helpers.py never got touched at all.
    with tempfile.TemporaryDirectory(prefix="probe-stage-") as tmp:
        stage = Path(tmp)
        staged = observe.stage_trees(work_root, cfg, stage)
        probe_job = {
            "mode": "replay",
            "env": "A",
            "preload": [],
            "stage_root": str(stage),
            "sys_path": [str(staged["target"]), str(staged["legacy"])],
            "module": "pkgf2.mainmod",
            "calls": {},
            "cases": [],
            "trace_file": None,
        }
        probe = observe.run_harness(probe_job, stage=stage, timeout_s=30)
    probe_route = probe["import_route_files"] or []
    assert "legacy/pkgf/helpers.py" in probe_route
