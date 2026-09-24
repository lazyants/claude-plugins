"""The one integration test (plan section 7): the W3->W4 pilot path with
`shop.pricing` as the pilot, wiring A's, B's, C's and D's real scripts and
fixtures together end to end. Every other test file in this suite exercises
one module against hand-built fixtures; this is the only place that proves
the real outputs compose.

Depends on B's `inventory.py`, `registry_validate.py`, `bridge.py`; C's
`net_capture.py`, `diff_gate.py` and the `shop.pricing` cases/port fixtures;
D's `unit_gate.py`, `sandbox.py` and `fake_codex.py`. Until those land this
file will fail on a missing script or fixture -- that is expected, not a
defect in A's own modules (see plan section 1 file ownership).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cm_common

PLUGIN_ROOT = cm_common.plugin_root()
SCRIPTS = PLUGIN_ROOT / "skills" / "codebase-migrator" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKES = Path(__file__).resolve().parent / "fakes"


def _run(script, args, env=None):
    full_env = None
    if env is not None:
        full_env = dict(os.environ)
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script)] + args,
        capture_output=True,
        text=True,
        env=full_env,
    )
    return proc


def _fake_codex_env(scenario):
    return {"CM_CODEX_BIN": str(FAKES / "fake_codex.py"), "FAKE_CODEX_SCENARIO": scenario}


def _last_json(proc):
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, f"no stdout from {proc.args}: stderr={proc.stderr}"
    return json.loads(lines[-1])


def test_pilot_path_shop_pricing(work_root):
    # `root` (the durable migration root) and `legacy_root` are SIBLINGS
    # under work_root, never nested in each other: migration_validate.py
    # refuses a root that equals, contains, or is contained by legacy_root.
    root = work_root / "root"

    # 1. scaffold.py -- fresh root (still absent), all keys unanswered.
    proc = _run("scaffold.py", ["--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_FAIL
    assert out["outcome"] == "fresh"
    assert out["created"] is True

    # copy the legacy fixture into a sibling of the now-scaffolded root.
    legacy_root = work_root / "legacy"
    shutil.copytree(FIXTURES / "legacy" / "shop", legacy_root / "shop")

    # 2. fill migration.json and conventions.md.
    migration_path = root / "migration.json"
    cfg = json.loads(migration_path.read_text(encoding="utf-8"))
    cfg.update(
        {
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
            "codex_bin": str(FAKES / "fake_codex.py"),
        }
    )
    cm_common.atomic_write_json(migration_path, cfg)
    cm_common.atomic_write_text(root / "conventions.md", "# Conventions\n\nUse snake_case.\n")

    # 3. migration_validate.py exits 0.
    proc = _run("migration_validate.py", ["--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True
    assert out["unanswered"] == []

    # 4. inventory.py.
    proc = _run("inventory.py", ["--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True
    assert out["units"] >= 2
    assert out["eligible"] >= 2

    # 5. copy registry.shop.json -> registry.json.
    registry_src = FIXTURES / "registry.shop.json"
    cm_common.atomic_write_json(
        root / "registry.json", json.loads(registry_src.read_text(encoding="utf-8"))
    )

    # 6. registry_validate.py --units shop.pricing --with-imported --freeze
    proc = _run(
        "registry_validate.py",
        ["--root", str(root), "--units", "shop.pricing", "--with-imported", "--freeze"],
    )
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True
    assert out["frozen"] >= 2

    # 7. copy the shop.pricing cases fixture.
    cases_src = FIXTURES / "cases" / "shop.pricing.json"
    cm_common.atomic_write_json(
        root / "cases" / "shop.pricing.json", json.loads(cases_src.read_text(encoding="utf-8"))
    )

    # 8. net_capture.py --unit shop.pricing -- netted, legacy_closure covers shop.money.
    proc = _run("net_capture.py", ["--root", str(root), "--unit", "shop.pricing"])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["verdict"] == "netted"
    net = json.loads((root / "nets" / "shop.pricing.json").read_text(encoding="utf-8"))
    # legacy_closure is keyed by relpath (inventory.closure_files), not by
    # unit dotted-name.
    assert "shop/money.py" in net["legacy_closure"]

    # 9. bridge.py -- exactly one shim: shop.money, exporting round_money.
    proc = _run("bridge.py", ["--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["shims"] == 1
    shims = json.loads((root / "runs" / "shims.json").read_text(encoding="utf-8"))
    assert list(shims["shims"].keys()) == ["shop.money"]
    assert "shop2.money:round_money" in shims["shims"]["shop.money"]["symbols"]

    # 10. ledger.py pilot.
    proc = _run(
        "ledger.py",
        [
            "pilot",
            "--root",
            str(root),
            "--unit",
            "shop.pricing",
            "--reason",
            "smallest unit with exactly one dependency",
        ],
    )
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    pilot = json.loads((root / "runs" / "pilot.json").read_text(encoding="utf-8"))
    assert pilot["unit"] == "shop.pricing"

    # 11. ledger.py eligible exits 0.
    proc = _run("ledger.py", ["eligible", "--root", str(root), "--unit", "shop.pricing"])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["reasons"] == []

    # 12. copy the good port's shop2/pricing.py into target_root.
    good_pricing = FIXTURES / "ports" / "good" / "shop2" / "pricing.py"
    target_pricing = cm_common.target_file(root, cfg, "shop.pricing")
    target_pricing.parent.mkdir(parents=True, exist_ok=True)
    target_pricing.write_text(good_pricing.read_text(encoding="utf-8"), encoding="utf-8")
    (target_pricing.parent / "__init__.py").write_text(
        "# codebase-migrator: package marker\n", encoding="utf-8"
    )

    # 13. unit_gate.py and diff_gate.py pass; crossed_shims == [shop.money].
    proc = _run("unit_gate.py", ["--root", str(root), "--unit", "shop.pricing"])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True

    proc = _run("diff_gate.py", ["--root", str(root), "--unit", "shop.pricing"])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True
    assert out["crossed_shims"] == ["shop.money"]

    # 14. record a fake probe against fake_codex in its obeying scenario.
    proc = _run(
        "sandbox.py", ["probe", "--root", str(root)], env=_fake_codex_env("probe_denied")
    )
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["ok"] is True
    probe = json.loads((root / "runs" / "sandbox_probe.json").read_text(encoding="utf-8"))
    assert probe["result"] == "denied"

    # 15. sandbox.py dispatch --kind review --round 1, fake_codex returns zero findings.
    proc = _run(
        "sandbox.py",
        [
            "dispatch",
            "--root",
            str(root),
            "--unit",
            "shop.pricing",
            "--kind",
            "review",
            "--round",
            "1",
        ],
        env=_fake_codex_env("review_empty"),
    )
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    review = json.loads(
        (root / "runs" / "shop.pricing" / "review.r1.json").read_text(encoding="utf-8")
    )
    assert review["findings"] == []
    assert review["malformed"] == []

    # 16. ledger.py converge.
    proc = _run("ledger.py", ["converge", "--root", str(root), "--unit", "shop.pricing"])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["state"] == "converged"

    # 17. status.py shows 1 converged, 1 shim.
    proc = _run("status.py", ["--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    assert out["by_state"].get("converged") == 1
    assert out["shims"] == 1

    # 18. edit conventions.md, ledger.py classify -> stale_by_convention.
    cm_common.atomic_write_text(root / "conventions.md", "# Conventions\n\nUse camelCase now.\n")
    proc = _run("ledger.py", ["classify", "--root", str(root)])
    out = _last_json(proc)
    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "stale_by_convention"
