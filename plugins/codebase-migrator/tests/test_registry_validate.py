"""Tests for registry_validate.py (plan section 4.4): registry row rules,
completeness, freeze and --correct."""
from __future__ import annotations

import copy
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
import registry_validate  # noqa: E402


def _run(*args: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "registry_validate.py"), *args],
        capture_output=True,
        text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else None
    return proc.returncode, payload, proc.stderr


def _cfg(dead_code_policy: str = "port", **overrides) -> dict:
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": "IGNORED",
        "legacy_package": "shop",
        "target_root": "IGNORED",
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": dead_code_policy,
        "coverage_floor_pct": 0,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture(scope="module")
def shop_inventory(tmp_path_factory):
    # legacy_root is a SIBLING of the durable root, never nested under it:
    # plan 2.1 refuses a durable root that equals, contains, or is
    # contained by legacy_root.
    base = tmp_path_factory.mktemp("registry_inventory")
    root = base / "root"
    root.mkdir()
    legacy = base / "legacy"
    shutil.copytree(FIXTURES_DIR / "legacy", legacy)
    cfg = _cfg(legacy_root=str(legacy), target_root=str(root / "target"))
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "inventory.py"), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    inventory = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    return inventory, cfg


def _base_rows() -> list[dict]:
    fixture = json.loads((FIXTURES_DIR / "registry.shop.json").read_text(encoding="utf-8"))
    return copy.deepcopy(fixture["rows"])


def _row_for(rows: list[dict], source: str) -> dict:
    return next(r for r in rows if r["source"] == source)


def _problem_sources(problems: list[dict]) -> set:
    return {p["source"] for p in problems}


# --- row rules, each violated once ------------------------------------------


def test_source_not_a_public_symbol(shop_inventory):
    inventory, cfg = shop_inventory
    rows = [{"source": "shop.money:nope", "cardinality": "one_to_one", "entry": "shop2.money:nope", "targets": ["shop2.money:nope"], "reason": None}]
    problems = registry_validate.row_problems(rows, inventory, cfg)
    assert "shop.money:nope" in _problem_sources(problems)


def test_unknown_cardinality(shop_inventory):
    inventory, cfg = shop_inventory
    rows = _base_rows()
    _row_for(rows, "shop.money:round_money")["cardinality"] = "bogus"
    problems = registry_validate.row_problems([r for r in rows if r["source"] == "shop.money:round_money"], inventory, cfg)
    assert any(p["source"] == "shop.money:round_money" and "cardinality" in p["message"] for p in problems)


def test_dropped_row_must_have_null_entry(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.cart:Cart", "cardinality": "dropped", "entry": "shop2.cart:Cart", "targets": [], "reason": "unused"}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="drop_with_census"))
    assert any("entry" in p["message"] for p in problems if p["source"] == "shop.cart:Cart")


def test_dropped_row_must_have_empty_targets(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.cart:Cart", "cardinality": "dropped", "entry": None, "targets": ["shop2.cart:Cart"], "reason": "unused"}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="drop_with_census"))
    assert any("targets" in p["message"] for p in problems if p["source"] == "shop.cart:Cart")


def test_dropped_row_needs_a_reason(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.cart:Cart", "cardinality": "dropped", "entry": None, "targets": [], "reason": ""}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="drop_with_census"))
    assert any("reason" in p["message"] for p in problems if p["source"] == "shop.cart:Cart")


def test_dropped_row_refused_under_dead_code_policy_port(shop_inventory):
    inventory, _ = shop_inventory
    row = {"source": "shop.cart:Cart", "cardinality": "dropped", "entry": None, "targets": [], "reason": "unused"}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="port"))
    assert any("dead_code_policy" in p["message"] for p in problems if p["source"] == "shop.cart:Cart")


def test_dropped_row_refused_for_a_symbol_another_unit_imports(shop_inventory):
    inventory, _ = shop_inventory
    # shop.money:round_money is imported by shop.pricing: never droppable.
    row = {"source": "shop.money:round_money", "cardinality": "dropped", "entry": None, "targets": [], "reason": "unused"}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="drop_with_census"))
    assert any("referenced" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_dropped_row_accepted_for_an_unreferenced_symbol(shop_inventory):
    inventory, _ = shop_inventory
    assert "shop.cart:Cart" in inventory["unreferenced_public"]
    row = {"source": "shop.cart:Cart", "cardinality": "dropped", "entry": None, "targets": [], "reason": "unused"}
    problems = registry_validate.row_problems([row], inventory, _cfg(dead_code_policy="drop_with_census"))
    assert problems == []


def test_non_dropped_needs_non_null_entry(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "one_to_one", "entry": None, "targets": ["shop2.money:round_money"], "reason": None}
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("entry" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_entry_must_be_one_of_targets(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "one_to_one", "entry": "shop2.money:other", "targets": ["shop2.money:round_money"], "reason": None}
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("targets" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_target_module_must_equal_target_module_of_unit(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "one_to_one", "entry": "wrongmod:round_money", "targets": ["wrongmod:round_money"], "reason": None}
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("module" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_one_to_one_needs_exactly_one_target(shop_inventory):
    inventory, cfg = shop_inventory
    row = {
        "source": "shop.money:round_money",
        "cardinality": "one_to_one",
        "entry": "shop2.money:round_money",
        "targets": ["shop2.money:round_money", "shop2.money:extra"],
        "reason": None,
    }
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("one_to_one" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_split_needs_at_least_two_targets(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "split", "entry": "shop2.money:round_money", "targets": ["shop2.money:round_money"], "reason": None}
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("split" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_merge_needs_exactly_one_target(shop_inventory):
    inventory, cfg = shop_inventory
    row = {
        "source": "shop.money:round_money",
        "cardinality": "merge",
        "entry": "shop2.money:combined",
        "targets": ["shop2.money:combined", "shop2.money:combined2"],
        "reason": None,
    }
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("merge" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_source_appears_in_more_than_one_row(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "one_to_one", "entry": "shop2.money:round_money", "targets": ["shop2.money:round_money"], "reason": None}
    problems = registry_validate.row_problems([row, copy.deepcopy(row)], inventory, cfg)
    assert any("more than one row" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_shared_target_requires_all_merge(shop_inventory):
    inventory, cfg = shop_inventory
    row_a = {"source": "shop.money:round_money", "cardinality": "one_to_one", "entry": "shop2.money:x", "targets": ["shop2.money:x"], "reason": None}
    row_b = {"source": "shop.money:to_cents", "cardinality": "one_to_one", "entry": "shop2.money:x", "targets": ["shop2.money:x"], "reason": None}
    problems = registry_validate.row_problems([row_a, row_b], inventory, cfg)
    sources = _problem_sources(problems)
    assert "shop.money:round_money" in sources and "shop.money:to_cents" in sources


def test_merge_target_must_be_shared_by_another_merge_row(shop_inventory):
    inventory, cfg = shop_inventory
    row = {"source": "shop.money:round_money", "cardinality": "merge", "entry": "shop2.money:combined", "targets": ["shop2.money:combined"], "reason": None}
    problems = registry_validate.row_problems([row], inventory, cfg)
    assert any("shared by at least one other merge row" in p["message"] for p in problems if p["source"] == "shop.money:round_money")


def test_valid_shop_registry_has_no_problems(shop_inventory):
    inventory, cfg = shop_inventory
    problems = registry_validate.row_problems(_base_rows(), inventory, cfg)
    assert problems == []


def test_completeness_names_missing_symbol(shop_inventory):
    inventory, cfg = shop_inventory
    rows = [r for r in _base_rows() if r["source"] != "shop.pricing:describe"]
    problems = registry_validate.completeness_problems(rows, inventory, ["shop.pricing"], with_imported=False)
    assert any(p["source"] == "shop.pricing:describe" for p in problems)


# --- CLI-level: freeze, re-freeze mismatch, --correct -----------------------


def _make_full_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    legacy = tmp_path / "legacy"
    shutil.copytree(FIXTURES_DIR / "legacy", legacy)
    cfg = _cfg(legacy_root=str(legacy), target_root=str(root / "target"))
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "inventory.py"), "--root", str(root)], check=True, capture_output=True)
    registry = {"schema": 1, "rows": _base_rows()}
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    return root


def test_freeze_with_imported_freezes_only_pilot_plus_imported(tmp_path):
    root = _make_full_root(tmp_path)
    code, payload, stderr = _run("--root", str(root), "--units", "shop.pricing", "--with-imported", "--freeze")
    assert code == 0, stderr
    lock = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    frozen_sources = set(lock["rows"])
    expected = {
        "shop.pricing:PricingError",
        "shop.pricing:apply_discount",
        "shop.pricing:normalize_items",
        "shop.pricing:dedupe",
        "shop.pricing:describe",
        "shop.money:round_money",
    }
    assert frozen_sources == expected
    assert "shop.money:to_cents" not in frozen_sources


def test_editing_a_frozen_row_is_refused(tmp_path):
    root = _make_full_root(tmp_path)
    code, _, stderr = _run("--root", str(root), "--units", "shop.pricing", "--with-imported", "--freeze")
    assert code == 0, stderr

    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    row = _row_for(registry["rows"], "shop.pricing:dedupe")
    row["entry"] = "shop2.pricing:dedupe_renamed"
    row["targets"] = ["shop2.pricing:dedupe_renamed"]
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    code, payload, stderr = _run("--root", str(root), "--units", "shop.pricing")
    assert code == 1
    assert any(p["source"] == "shop.pricing:dedupe" for p in payload["problems"])


def test_correct_wrong_digest_refused_right_digest_applied(tmp_path):
    root = _make_full_root(tmp_path)
    code, _, stderr = _run("--root", str(root), "--units", "shop.pricing", "--with-imported", "--freeze")
    assert code == 0, stderr

    lock = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    real_digest = lock["rows"]["shop.pricing:dedupe"]["digest"]

    code, payload, stderr = _run(
        "--root", str(root), "--correct", "shop.pricing:dedupe",
        "--expect-digest", "0" * 64, "--reason", "renaming",
    )
    assert code != 0
    assert not payload["ok"]

    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    row = _row_for(registry["rows"], "shop.pricing:dedupe")
    row["entry"] = "shop2.pricing:dedupe_renamed"
    row["targets"] = ["shop2.pricing:dedupe_renamed"]
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    code, payload, stderr = _run(
        "--root", str(root), "--correct", "shop.pricing:dedupe",
        "--expect-digest", real_digest, "--reason", "renaming",
    )
    assert code == 0, stderr
    lock = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    assert lock["rows"]["shop.pricing:dedupe"]["row"]["entry"] == "shop2.pricing:dedupe_renamed"
    corrections = json.loads((root / "registry.corrections.json").read_text(encoding="utf-8"))
    assert corrections["corrections"][-1]["source"] == "shop.pricing:dedupe"
    assert corrections["corrections"][-1]["reason"] == "renaming"


def test_correct_to_invalid_dropped_row_refused_lock_unchanged(tmp_path):
    # The proposed row must be validated (row rules AND global sharing
    # rules) BEFORE the lock or the correction history change: a correction
    # that would leave registry.json invalid must be refused outright, with
    # nothing written.
    root = _make_full_root(tmp_path)
    code, _, stderr = _run("--root", str(root), "--units", "shop.pricing", "--with-imported", "--freeze")
    assert code == 0, stderr

    lock_before = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    entry_before = lock_before["rows"]["shop.pricing:describe"]
    corrections_path = root / "registry.corrections.json"
    assert not corrections_path.exists()

    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    row = _row_for(registry["rows"], "shop.pricing:describe")
    row["cardinality"] = "dropped"
    row["entry"] = None
    row["targets"] = []
    row["reason"] = "never called"
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    # migration.json's dead_code_policy is "port" (the _cfg default): a
    # dropped row is refused outright, regardless of digest correctness.
    code, payload, stderr = _run(
        "--root", str(root), "--correct", "shop.pricing:describe",
        "--expect-digest", entry_before["digest"], "--reason", "drop it",
    )
    assert code != 0
    assert payload["ok"] is False

    lock_after = json.loads((root / "registry.lock.json").read_text(encoding="utf-8"))
    assert lock_after["rows"]["shop.pricing:describe"] == entry_before
    assert not corrections_path.exists()


def test_correct_requires_both_flags(tmp_path):
    root = _make_full_root(tmp_path)
    code, payload, stderr = _run("--root", str(root), "--correct", "shop.pricing:dedupe")
    assert code == 2
