"""Tests for diff_gate.py: R3, the differential gate (plan section 4.8,
tests owned by C)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cm_common
import diff_gate
import inventory
import net_capture

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
LEGACY_SRC = FIXTURES_DIR / "legacy" / "shop"
REGISTRY_SRC = FIXTURES_DIR / "registry.shop.json"
CASES_DIR = FIXTURES_DIR / "cases"
PORTS_DIR = FIXTURES_DIR / "ports"
SCRIPTS_DIR = Path(cm_common.__file__).resolve().parent


def _scaffold(root: Path, coverage_floor: int = 50) -> dict:
    (root / "legacy").mkdir(parents=True, exist_ok=True)
    shutil.copytree(LEGACY_SRC, root / "legacy" / "shop", dirs_exist_ok=True)
    (root / "cases").mkdir(parents=True, exist_ok=True)
    (root / "nets").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "conventions.md").write_text("conventions\n")
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": "legacy",
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
    only), yet importing `shop.pricing` (or a dependency reached through a
    shim) always initializes it first — the route rule must not treat that
    mechanical side effect as a violation. Reaching U's own legacy file must
    still be refused, even though it is also an ancestor-package init for
    nothing in particular here."""
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)

    _install_port(work_root, "good")
    good_result = diff_gate.run(work_root, cfg, "shop.pricing")
    assert good_result["ok"] is True, good_result
    assert not any(
        "legacy/shop/__init__.py" in rv["reason"] for rv in good_result["route_violations"]
    )

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
    cfg = _scaffold(work_root)
    _capture_pricing(work_root, cfg)
    _install_port(work_root, "good")
    assert diff_gate.run(work_root, cfg, "shop.pricing")["ok"] is True

    money_path = work_root / "legacy" / "shop" / "money.py"
    money_path.write_text(money_path.read_text().replace("round(x + 0.0, 2)", "round(x + 0.0, 3)"))

    with pytest.raises(SystemExit) as exc:
        diff_gate.run(work_root, cfg, "shop.pricing")
    assert exc.value.code == cm_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "shop.money" in payload.get("changed_units", [])

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
