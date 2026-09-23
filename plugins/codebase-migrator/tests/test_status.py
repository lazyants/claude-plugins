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
    assert out["eligible"] == "absent"
    assert out["ineligible"] == "absent"
    assert out["netted"] == "absent"
    assert out["frozen_rows"] == "absent"
    assert out["shims"] == "absent"
    assert out["ports"] == "absent"
    assert out["probe"] == "absent"


def test_status_is_read_only(work_root):
    root = work_root
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
            },
        },
    )
    before = cm_common.tree_digests(root)
    proc = _run(root)
    after = cm_common.tree_digests(root)
    assert before == after

    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["units"] == 2
    assert out["by_state"] == {"converged": 1, "pending": 1}
    assert out["eligible"] == 1
    assert out["ineligible"] == 1
