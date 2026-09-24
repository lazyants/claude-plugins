"""Tests for unit_gate.py -- R2, the mechanical gate (plan sections 4.9, 7, owner D)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cm_common
import inventory
import unit_gate

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
UNIT_GATE_SCRIPT = Path(unit_gate.__file__).resolve()

ALL_CHECKS = (
    "target_present",
    "compiles",
    "surface_matches",
    "no_stubs",
    "no_direct_legacy_import",
    "protected_intact",
    "all_rows_frozen",
    "target_self_contained",
)


def _shop_cfg(legacy_root: Path, target_root: Path) -> dict:
    return {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "shop",
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


def _build_shop_root(work_root: Path) -> tuple[Path, dict]:
    """A durable root around the shared shop fixture: legacy tree, a current
    inventory.json and a frozen registry.lock.json for shop.money, shop.pricing
    and shop.cart (tests/fixtures/registry.shop.json). Kept as a SIBLING of
    legacy_root and target_root under the same work_root -- never a parent
    of either -- since migration_validate.py refuses a durable root that
    equals, contains, or is contained by legacy_root, and sandbox.py's
    promotion-destination check separately refuses a target write that
    resolves into the durable root.

    Returns `(root, cfg)`, with `cfg` as `cm_common.load_config()` itself
    loads and validates it -- an existing target_root is not a validation
    problem (fixed by A), so this and every CLI-level `main()` call below
    are free to run more than once against the same root, exactly like the
    real pipeline does once a port or shim exists."""
    root = work_root / "proj"
    root.mkdir(parents=True, exist_ok=True)
    legacy_root = work_root / "legacy"
    target_root = work_root / "target"
    shutil.copytree(FIXTURES_DIR / "legacy" / "shop", legacy_root / "shop")

    cfg = _shop_cfg(legacy_root, target_root)
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / "conventions.md").write_text("Plain functions, preserve names.\n", encoding="utf-8")
    for d in ("cases", "nets", "runs"):
        (root / d).mkdir(parents=True, exist_ok=True)

    inv = inventory.build_inventory(root, cfg)
    cm_common.atomic_write_json(root / "inventory.json", inv)

    registry = json.loads((FIXTURES_DIR / "registry.shop.json").read_text(encoding="utf-8"))
    lock_rows = {
        row["source"]: {"row": row, "digest": cm_common.sha256_json(row)}
        for row in registry["rows"]
    }
    cm_common.atomic_write_json(root / "registry.lock.json", {"schema": 1, "rows": lock_rows})
    return root, cm_common.load_config(root)


def _install_port(cfg: dict, variant: str) -> None:
    target_root = Path(cfg["target_root"])
    shutil.copytree(FIXTURES_DIR / "ports" / variant / "shop2", target_root / "shop2")


@pytest.fixture
def shop_root(work_root):
    return _build_shop_root(work_root)


def _only_failed(result: dict) -> list:
    return sorted(name for name, ok in result["checks"].items() if not ok)


def test_good_port_passes_every_check(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "good")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert result["problems"] == []
    assert set(result["checks"]) == set(ALL_CHECKS)
    assert all(result["checks"].values()), result["checks"]
    assert result["ok"] is True


def test_stub_fails_no_stubs_only(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "stub")

    # to_cents' stub body lives in shop2/money.py, so it is shop.money's own
    # gate that must catch it -- no_stubs only inspects a unit's own target
    # file, never a dependency's.
    result = unit_gate.run_gate(root, cfg, "shop.money")

    assert _only_failed(result) == ["no_stubs"]
    assert any("to_cents" in p for p in result["problems"])


def test_extra_export_fails_surface_matches_only(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "extra_export")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["surface_matches"]
    assert any("helper" in p for p in result["problems"])


def test_direct_legacy_import_fails_that_check_only(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "direct_legacy_import")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["no_direct_legacy_import"]
    assert any("shop.money" in p for p in result["problems"])


def test_reads_clock_fails_target_self_contained_against_the_helper(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "reads_clock")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["target_self_contained"]
    # Named by its FILE now (inventory.closure_files, not a bare module
    # name), matching what a helper that isn't itself a discovered unit
    # still needs to be identified by.
    assert any("shop2/_util.py" in p for p in result["problems"])
    assert any("uncontrolled_input" in p for p in result["problems"])


def test_helper_that_imports_legacy_fails_no_direct_legacy_import_against_the_helper(shop_root):
    """`no_direct_legacy_import` now walks `inventory.closure_files`, not
    just U's own file: a private helper the target module imports, which
    itself imports the legacy package directly, must fail this check too,
    named by the helper's own file."""
    root, cfg = shop_root
    _install_port(cfg, "helper_imports_legacy")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["no_direct_legacy_import"]
    assert any("shop2/_helper.py" in p for p in result["problems"])
    assert any("shop.money" in p for p in result["problems"])


def test_dependency_still_a_shim_passes_r2(shop_root):
    """A bridge shim's whole body IS a direct legacy import by design
    (`from shop.money import round_money as round_money`) -- when
    shop.pricing's own port is otherwise clean but its shop.money dependency
    is still a shim, R2 must pass, not fail `no_direct_legacy_import`
    against the shim it did not write."""
    root, cfg = shop_root
    _install_port(cfg, "dependency_still_shim")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert result["problems"] == []
    assert result["ok"] is True


def test_executable_init_with_a_flag_fails_target_self_contained(shop_root):
    """`inventory.closure_files` always includes every ancestor package
    `__init__.py`, whether or not it is itself a discovered unit: an
    executable `shop2/__init__.py` that reads the clock at import time must
    fail `target_self_contained`, named by its own file, even though nothing
    in `shop2.pricing` imports `shop2` for any symbol of its own."""
    root, cfg = shop_root
    _install_port(cfg, "executable_init")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["target_self_contained"]
    assert any("shop2/__init__.py" in p for p in result["problems"])
    assert any("uncontrolled_input" in p for p in result["problems"])


def test_todo_comment_fails_no_stubs(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "good")
    target = Path(cfg["target_root"]) / "shop2" / "pricing.py"
    text = target.read_text(encoding="utf-8")
    target.write_text(text + "\n# TODO: revisit rounding\n", encoding="utf-8")

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert _only_failed(result) == ["no_stubs"]
    assert any("TODO" in p for p in result["problems"])


def test_tampered_net_fails_protected_intact(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "good")

    nets_path = root / "nets" / "shop.pricing.json"
    cases_path = root / "cases" / "shop.pricing.json"
    cm_common.atomic_write_json(nets_path, {"schema": 1, "unit": "shop.pricing", "observations": []})
    cm_common.atomic_write_json(cases_path, {"cases": []})
    net_lock = {
        "schema": 1,
        "units": {
            "shop.pricing": {
                "net_sha256": cm_common.sha256_file(nets_path),
                "cases_sha256": cm_common.sha256_file(cases_path),
                "legacy_closure_sha256": "irrelevant-to-this-check",
                "coverage_pct": 100,
                "kept": 1,
                "dropped": {},
                "skipped_dropped_symbol": [],
                "deterministic": True,
                "stateful": False,
            }
        },
    }
    cm_common.atomic_write_json(root / "net.lock.json", net_lock)

    # Sanity: untampered, protected_intact must pass alongside everything else.
    clean = unit_gate.run_gate(root, cfg, "shop.pricing")
    assert clean["checks"]["protected_intact"] is True

    # Tamper with the net after freezing it -- its digest no longer matches
    # net.lock.json's recorded net_sha256.
    with open(nets_path, "a", encoding="utf-8") as fh:
        fh.write("\n")

    tampered = unit_gate.run_gate(root, cfg, "shop.pricing")
    assert tampered["checks"]["protected_intact"] is False
    assert any("nets/shop.pricing.json" in p for p in tampered["problems"])
    # Nothing else should have been perturbed by this specific tamper.
    for name, ok in tampered["checks"].items():
        if name != "protected_intact":
            assert ok is True, (name, tampered["problems"])


def test_target_absent_or_shim_fails_target_present(shop_root):
    root, cfg = shop_root
    # No port installed at all.
    result = unit_gate.run_gate(root, cfg, "shop.pricing")
    assert result["checks"]["target_present"] is False
    assert result["ok"] is False

    # A shim (not a port) must also fail target_present.
    target_root = Path(cfg["target_root"])
    (target_root / "shop2").mkdir(parents=True, exist_ok=True)
    (target_root / "shop2" / "__init__.py").write_text(
        "# codebase-migrator: package marker\n", encoding="utf-8"
    )
    (target_root / "shop2" / "pricing.py").write_text(
        "# codebase-migrator: shim for shop.pricing\n"
        "from shop.pricing import apply_discount\n",
        encoding="utf-8",
    )
    result = unit_gate.run_gate(root, cfg, "shop.pricing")
    assert result["checks"]["target_present"] is False


def test_all_rows_frozen_fails_when_a_public_symbol_is_unfrozen(shop_root):
    root, cfg = shop_root
    _install_port(cfg, "good")

    lock = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    del lock["rows"]["shop.pricing:describe"]
    cm_common.atomic_write_json(root / "registry.lock.json", lock)

    result = unit_gate.run_gate(root, cfg, "shop.pricing")

    assert result["checks"]["all_rows_frozen"] is False
    assert any("shop.pricing:describe" in p for p in result["problems"])
    # surface_matches must still be judged on its own terms (describe is
    # still exported and still has no row in `targets` to compare against,
    # so it is reported as an extra name there too) -- the two checks are
    # independent, neither masks the other.
    assert result["checks"]["surface_matches"] is False


def test_cli_main_persists_r2_across_two_invocations_on_the_same_root(shop_root, monkeypatch):
    """`main()` end to end, called twice against the same root, exactly like
    the real pipeline: a first `--root R --unit shop.pricing` call (which
    itself creates target_root's port), then a second one after it. This is
    the exact shape migration_validate.py's fix (an existing target_root is
    valid) was needed for -- a second cm_common.load_config() on this root
    used to fail before that fix landed."""
    root, cfg = shop_root
    _install_port(cfg, "good")

    argv = ["unit_gate.py", "--root", str(root), "--unit", "shop.pricing"]
    monkeypatch.setattr("sys.argv", argv)

    code_first = unit_gate.main()
    assert code_first == cm_common.EXIT_OK

    report_first = json.loads((root / "runs" / "shop.pricing" / "r2.json").read_text(encoding="utf-8"))
    assert report_first["ok"] is True
    assert "key_sha256" in report_first
    assert report_first["target_sha256"] == cm_common.sha256_file(
        cm_common.target_file(root, cfg, "shop.pricing")
    )

    # Second CLI-level invocation against the same root, now that target_root
    # is populated -- must not be refused by config validation.
    code_second = unit_gate.main()
    assert code_second == cm_common.EXIT_OK
    report_second = json.loads((root / "runs" / "shop.pricing" / "r2.json").read_text(encoding="utf-8"))
    assert report_second["key_sha256"] == report_first["key_sha256"]


def test_unit_gate_as_a_real_subprocess_against_an_existing_target_root(shop_root):
    """`unit_gate.py` invoked exactly as the pipeline would, in its own
    process, against a root whose target_root already exists -- built here by
    `_build_shop_root()` calling `cm_common.load_config()` once, then
    `_install_port()` creating target_root, so this subprocess's own
    `load_config()` is a second call against the same root. This is real
    process-boundary coverage, not `main()` called in-process."""
    root, cfg = shop_root
    _install_port(cfg, "good")

    proc = subprocess.run(
        [sys.executable, str(UNIT_GATE_SCRIPT), "--root", str(root), "--unit", "shop.pricing"],
        capture_output=True, text=True, timeout=60,
    )

    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1, proc.stdout
    payload = json.loads(stdout_lines[0])
    assert payload["ok"] is True
    assert payload["unit"] == "shop.pricing"
    assert all(payload["checks"].values()), payload["checks"]

    report = json.loads((root / "runs" / "shop.pricing" / "r2.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert "key_sha256" in report
    assert report["target_sha256"] == cm_common.sha256_file(
        cm_common.target_file(root, cfg, "shop.pricing")
    )
