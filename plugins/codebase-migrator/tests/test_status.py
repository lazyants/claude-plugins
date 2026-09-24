"""Tests for status.py (plan section 7, owner A)."""

import json
import subprocess
import sys
from pathlib import Path

import cm_common

STATUS = str(Path(cm_common.plugin_root()) / "skills" / "codebase-migrator" / "scripts" / "status.py")


def _run(root):
    return subprocess.run(
        [sys.executable, STATUS, "--root", str(root)],
        capture_output=True,
        text=True,
    )


def test_status_absent_inputs_reported_as_absent(work_root):
    root = work_root
    proc = _run(root)
    assert proc.returncode == 0
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["units"] == "absent"
    assert out["in_ledger"] == "absent"
    assert out["eligible"] == "absent"
    assert out["ineligible"] == "absent"
    assert out["netted"] == "absent"
    assert out["frozen_rows"] == "absent"
    assert out["shims"] == "absent"
    assert out["ports"] == "absent"
    assert out["probe"] == "absent"


def test_status_is_read_only(work_root):
    root = work_root
    # ledger.json and inventory.json deliberately have DIFFERENT unit
    # counts: a unit can be fully discovered (in the inventory) with no
    # ledger entry at all yet, until something first sets its state. `units`
    # must track the inventory (the denominator for "N of units converged"),
    # never the ledger's own count.
    cm_common.atomic_write_json(
        root / "ledger.json",
        {
            "schema": 1,
            "units": {
                "shop.pricing": {"state": "converged", "cache_key": None, "key_sha256": None, "updated": "x", "reason": None},
                "shop.money": {"state": "pending", "cache_key": None, "key_sha256": None, "updated": "x", "reason": None},
            },
        },
    )
    cm_common.atomic_write_json(
        root / "inventory.json",
        {
            "schema": 1,
            "units": {
                "shop.pricing": {"eligible": True},
                "shop.clock": {"eligible": False},
                "shop.money": {"eligible": True},
            },
        },
    )
    before = cm_common.tree_digests(root)
    proc = _run(root)
    after = cm_common.tree_digests(root)
    assert before == after

    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["units"] == 3
    assert out["in_ledger"] == 2
    assert out["units"] != out["in_ledger"]
    assert out["by_state"] == {"converged": 1, "pending": 1}
    assert out["eligible"] == 2
    assert out["ineligible"] == 1
