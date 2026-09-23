"""Tests for bridge.py (plan section 4.5): the in-process strangler bridge."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "codebase-migrator" / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
import bridge  # noqa: E402


def _run(script: str, *args: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True,
        text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else None
    return proc.returncode, payload, proc.stderr


def _cfg(legacy_root: Path, target_root: Path, dead_code_policy: str = "port") -> dict:
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
        "dead_code_policy": dead_code_policy,
        "coverage_floor_pct": 0,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }


def _base_rows() -> list[dict]:
    fixture = json.loads((FIXTURES_DIR / "registry.shop.json").read_text(encoding="utf-8"))
    return copy.deepcopy(fixture["rows"])


def _make_root(tmp_path: Path, dead_code_policy: str = "port") -> Path:
    root = tmp_path / "root"
    legacy = root / "legacy"
    shutil.copytree(FIXTURES_DIR / "legacy", legacy)
    cfg = _cfg(legacy, root / "target", dead_code_policy)
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "inventory.py"), "--root", str(root)], check=True, capture_output=True)
    (root / "registry.json").write_text(json.dumps({"schema": 1, "rows": _base_rows()}), encoding="utf-8")
    return root


def _freeze(root: Path, *args: str) -> None:
    code, payload, stderr = _run("registry_validate.py", "--root", str(root), *args, "--freeze")
    assert code == 0, (payload, stderr)


def test_shims_generated_for_unported_units_only_pilot_walkthrough(tmp_path):
    """Mirrors the W3->W4 pilot walkthrough (plan section 7): freezing the
    pilot shop.pricing plus its imports shims only the unported dependency,
    shop.money — never the pilot itself, which will get its own real port."""
    root = _make_root(tmp_path)
    _freeze(root, "--units", "shop.pricing", "--with-imported")

    code, payload, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr
    assert payload["shims"] == 1
    assert payload["ports"] == 0
    assert payload["skipped_non_one_to_one"] == []

    census = json.loads((root / "runs" / "shims.json").read_text(encoding="utf-8"))
    assert set(census["shims"]) == {"shop.money"}
    assert census["shims"]["shop.money"]["symbols"] == ["shop2.money:round_money"]
    assert census["ports"] == []

    target_file = root / "target" / "shop2" / "money.py"
    lines = target_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# codebase-migrator: shim for shop.money"
    assert "from shop.money import round_money as round_money" in lines

    # the pilot itself gets no shim: no target file was written for it.
    assert not (root / "target" / "shop2" / "pricing.py").exists()

    shim_bytes_before = target_file.read_text(encoding="utf-8")

    # Second run, real CLI, against the SAME root: target_root and the shim
    # now already exist. migration_validate.py must accept an existing
    # target_root (plan 2.3: "may not exist yet" only meant it need not, not
    # that it must not), and bridge.py must recognize its own shim and leave
    # it exactly as it was (no duplicate write, same content).
    code, payload, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr
    assert payload["shims"] == 1
    assert payload["ports"] == 0
    assert target_file.read_text(encoding="utf-8") == shim_bytes_before


def test_port_is_never_overwritten(tmp_path):
    root = _make_root(tmp_path)
    _freeze(root, "--units", "shop.pricing", "--with-imported")

    # First run, real CLI: bridge.py creates the shop.money shim (target_root
    # does not exist yet at this point).
    code, payload, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr
    assert payload["shims"] == 1

    # Replace the generated shim with a hand-written real port, then run
    # bridge.py a SECOND time, real CLI, against the SAME root: this is the
    # only way to prove a pre-existing target_root AND a pre-existing port
    # are both handled correctly by the actual CLI path (a direct
    # build_bridge() call alone would never exercise migration_validate.py's
    # target_root check on a second pass).
    target_root = root / "target" / "shop2"
    port_content = "# a real, hand-written port\ndef round_money(x):\n    return round(x, 2)\n"
    (target_root / "money.py").write_text(port_content, encoding="utf-8")

    code, payload, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr
    assert payload["shims"] == 0
    assert payload["ports"] == 1

    assert (target_root / "money.py").read_text(encoding="utf-8") == port_content
    census = json.loads((root / "runs" / "shims.json").read_text(encoding="utf-8"))
    assert census["ports"] == ["shop.money"]
    assert census["shims"] == {}


def test_non_one_to_one_rows_skipped_and_listed(tmp_path):
    root = _make_root(tmp_path, dead_code_policy="drop_with_census")
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    to_cents = next(r for r in registry["rows"] if r["source"] == "shop.money:to_cents")
    to_cents["cardinality"] = "dropped"
    to_cents["entry"] = None
    to_cents["targets"] = []
    to_cents["reason"] = "never called from the port"
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    _freeze(root, "--units", "shop.pricing,shop.money")

    code, payload, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr
    assert "shop.money:to_cents" in payload["skipped_non_one_to_one"]
    # shop.money still gets a shim, but only for round_money.
    census = json.loads((root / "runs" / "shims.json").read_text(encoding="utf-8"))
    assert census["shims"]["shop.money"]["symbols"] == ["shop2.money:round_money"]


def test_shim_exports_exactly_the_frozen_symbols_and_imports_resolve(tmp_path):
    root = _make_root(tmp_path)
    _freeze(root, "--units", "shop.pricing", "--with-imported")
    code, _, stderr = _run("bridge.py", "--root", str(root))
    assert code == 0, stderr

    # copy the real 'good' port's pricing.py/cart.py alongside the generated
    # shim for money.py, and prove the whole shop2 package actually imports
    # and runs against the fixture legacy tree.
    good = FIXTURES_DIR / "ports" / "good" / "shop2"
    target_shop2 = root / "target" / "shop2"
    shutil.copy2(good / "pricing.py", target_shop2 / "pricing.py")
    shutil.copy2(good / "cart.py", target_shop2 / "cart.py")

    legacy_root = root / "legacy"
    check = subprocess.run(
        [sys.executable, "-c", "import shop2.pricing; print(shop2.pricing.apply_discount(100, 10))"],
        cwd=str(root / "target"),
        env={"PYTHONPATH": f"{root / 'target'}:{legacy_root}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr
    assert check.stdout.strip() == "90.0"


def test_check_mode_writes_nothing(tmp_path):
    root = _make_root(tmp_path)
    _freeze(root, "--units", "shop.pricing", "--with-imported")

    code, payload, stderr = _run("bridge.py", "--root", str(root), "--check")
    assert code == 0, stderr
    assert payload["shims"] == 1
    assert not (root / "target").exists()
    assert not (root / "runs" / "shims.json").exists()


def test_is_shim_detects_exact_marker(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text("# codebase-migrator: shim for shop.money\nfrom shop.money import round_money\n", encoding="utf-8")
    assert bridge.is_shim(path, "shop.money") is True
    assert bridge.is_shim(path, "shop.other") is False

    path.write_text("# not a shim\n", encoding="utf-8")
    assert bridge.is_shim(path, "shop.money") is False
