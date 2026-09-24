"""Tests for migration_validate.py (plan section 7, owner A)."""

import json
import os
from pathlib import Path

import migration_validate


def _base_cfg(work_root):
    """A fully-answered, otherwise-valid config, plus the `root` to validate
    it against. `root` and `legacy_root` are SIBLINGS under `work_root`,
    never nested in each other -- validate() refuses that overlap."""
    root = work_root / "root"
    legacy_root = work_root / "legacy"
    (legacy_root / "shop").mkdir(parents=True, exist_ok=True)
    (legacy_root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "shop",
        "target_root": str(work_root / "target"),
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": "port",
        "coverage_floor_pct": 80,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }
    return cfg, root


def test_example_file_yields_every_key_unanswered(work_root):
    import cm_common

    example_path = (
        cm_common.plugin_root()
        / "skills"
        / "codebase-migrator"
        / "assets"
        / "migration.example.json"
    )
    cfg = json.loads(example_path.read_text(encoding="utf-8"))
    problems = migration_validate.validate(cfg, work_root)
    unanswered = {p["key"] for p in problems if p["kind"] == "unanswered"}
    assert unanswered == set(migration_validate.QUESTIONNAIRE)


def test_each_enum_rejects_out_of_v01_value_by_name(work_root):
    cfg, root = _base_cfg(work_root)
    for key in (
        "source_stack",
        "target_stack",
        "fidelity_policy",
        "seam",
        "unit_granularity",
        "naming_policy",
        "net_source",
        "dead_code_policy",
    ):
        bad_cfg = dict(cfg)
        bad_cfg[key] = "not_a_v01_value"
        problems = migration_validate.validate(bad_cfg, root)
        matches = [p for p in problems if p["key"] == key and p["kind"] == "unsupported"]
        assert matches, f"expected an unsupported problem for {key}"
        assert "not_a_v01_value" in matches[0]["message"]


def test_legacy_package_equals_target_package_refused(work_root):
    cfg, root = _base_cfg(work_root)
    cfg["target_package"] = cfg["legacy_package"]
    problems = migration_validate.validate(cfg, root)
    assert any(
        p["key"] == "target_package" and "differ" in p["message"] for p in problems
    )


def test_missing_init_py_refused(work_root):
    cfg, root = _base_cfg(work_root)
    (work_root / "legacy" / "shop" / "__init__.py").unlink()
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "legacy_package" for p in problems)


def test_valid_config_has_no_problems(work_root):
    cfg, root = _base_cfg(work_root)
    problems = migration_validate.validate(cfg, root)
    assert problems == []


def test_unknown_key_reported():
    cfg = {"schema": 1, "spurious_key": "x"}
    problems = migration_validate.validate(cfg, "/nonexistent")
    assert any(p["key"] == "spurious_key" and p["kind"] == "unknown_key" for p in problems)


def test_coverage_floor_and_max_fix_rounds_bounds(work_root):
    cfg, root = _base_cfg(work_root)
    cfg["coverage_floor_pct"] = 150
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "coverage_floor_pct" for p in problems)

    cfg, root = _base_cfg(work_root)
    cfg["max_fix_rounds"] = 0
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "max_fix_rounds" for p in problems)


def test_target_root_valid_before_and_after_it_is_created(work_root):
    # The pipeline itself creates and grows target_root (bridge.py's shims,
    # a promoted port) between one load_config() call and the next, so an
    # already-existing target_root must not become a permanent problem.
    cfg, root = _base_cfg(work_root)
    target_root = Path(cfg["target_root"])
    assert not target_root.exists()
    problems_before = migration_validate.validate(cfg, root)
    assert not any(p["key"] == "target_root" for p in problems_before)

    os.makedirs(target_root / "shop2")
    problems_after = migration_validate.validate(cfg, root)
    assert not any(p["key"] == "target_root" for p in problems_after)


def test_target_root_existing_non_directory_refused(work_root):
    cfg, root = _base_cfg(work_root)
    target_root = Path(cfg["target_root"])
    target_root.parent.mkdir(parents=True, exist_ok=True)
    target_root.write_text("not a directory", encoding="utf-8")
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "target_root" for p in problems)


def test_target_root_must_not_overlap_legacy_root(work_root):
    cfg, root = _base_cfg(work_root)
    cfg["target_root"] = cfg["legacy_root"]
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "target_root" for p in problems)

    cfg, root = _base_cfg(work_root)
    cfg["target_root"] = str(Path(cfg["legacy_root"]) / "sub")
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "target_root" for p in problems)


def test_missing_required_key_is_a_problem(work_root):
    cfg, root = _base_cfg(work_root)
    del cfg["source_stack"]
    problems = migration_validate.validate(cfg, root)
    matches = [p for p in problems if p["key"] == "source_stack"]
    assert matches, "a deleted required key must not be silently accepted"
    assert matches[0]["kind"] == "missing"

    # a second, non-questionnaire key (has a literal example default, but is
    # still required) is caught the same way
    cfg, root = _base_cfg(work_root)
    del cfg["codex_bin"]
    problems = migration_validate.validate(cfg, root)
    matches = [p for p in problems if p["key"] == "codex_bin"]
    assert matches and matches[0]["kind"] == "missing"


def test_root_must_not_overlap_legacy_root(work_root):
    # root equals legacy_root
    cfg, root = _base_cfg(work_root)
    cfg["legacy_root"] = str(root)
    (root / "shop").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "legacy_root" and "durable root" in p["message"] for p in problems)

    # legacy_root contains root
    cfg, root = _base_cfg(work_root)
    nested_root = Path(cfg["legacy_root"]) / "nested_root"
    problems = migration_validate.validate(cfg, nested_root)
    assert any(p["key"] == "legacy_root" and "durable root" in p["message"] for p in problems)

    # root contains legacy_root
    cfg, root = _base_cfg(work_root)
    inside_legacy = root / "legacy_inside" / "shop"
    inside_legacy.mkdir(parents=True)
    (inside_legacy / "__init__.py").write_text("", encoding="utf-8")
    cfg["legacy_root"] = str(root / "legacy_inside")
    problems = migration_validate.validate(cfg, root)
    assert any(p["key"] == "legacy_root" and "durable root" in p["message"] for p in problems)

    # the ordinary sibling layout from _base_cfg is still accepted
    cfg, root = _base_cfg(work_root)
    problems = migration_validate.validate(cfg, root)
    assert not any("durable root" in p["message"] for p in problems)
