"""Tests for scaffold.py (plan section 7, owner A)."""

import json
import subprocess
import sys
from pathlib import Path

import cm_common

SCAFFOLD = str(Path(cm_common.plugin_root()) / "skills" / "codebase-migrator" / "scripts" / "scaffold.py")


def _run(args):
    return subprocess.run(
        [sys.executable, SCAFFOLD] + args,
        capture_output=True,
        text=True,
    )


def test_fresh_outcome_creates_layout(work_root):
    root = work_root / "durable"
    proc = _run(["--root", str(root)])
    assert proc.returncode == 1  # migration.json is all CHOOSE_ placeholders
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["outcome"] == "fresh"
    assert out["created"] is True
    assert set(out["unanswered"]) == set(
        __import__("migration_validate").QUESTIONNAIRE
    )

    assert (root / ".codebase-migrator-root.json").is_file()
    marker = json.loads((root / ".codebase-migrator-root.json").read_text(encoding="utf-8"))
    assert marker["schema"] == 1
    assert marker["plugin_version"] == cm_common.PLUGIN_VERSION
    assert (root / "migration.json").is_file()
    assert (root / "conventions.md").is_file()
    assert "CHOOSE_CONVENTIONS" in (root / "conventions.md").read_text(encoding="utf-8")
    assert json.loads((root / "ledger.json").read_text(encoding="utf-8")) == {
        "schema": 1,
        "units": {},
    }
    assert json.loads((root / "registry.json").read_text(encoding="utf-8")) == {
        "schema": 1,
        "rows": [],
    }
    for sub in ("cases", "nets", "runs"):
        assert (root / sub).is_dir()


def test_resumed_outcome_touches_nothing_else(work_root):
    root = work_root / "durable"
    _run(["--root", str(root)])
    # Full-tree digests cover every pre-existing file (ledger.json,
    # registry.json, conventions.md, migration.json, the ownership marker,
    # and anything else under root) in one comparison, not just one of them.
    before = cm_common.tree_digests(root)

    (root / "cases").rmdir()  # simulate a missing layout dir

    proc = _run(["--root", str(root)])
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["outcome"] == "resumed"
    assert out["created"] is False
    assert (root / "cases").is_dir()

    after = cm_common.tree_digests(root)
    assert after == before


def test_ambiguous_without_adopt_refused(work_root):
    root = work_root / "foreign"
    root.mkdir()
    (root / "some_file.txt").write_text("preexisting", encoding="utf-8")

    proc = _run(["--root", str(root)])
    assert proc.returncode == cm_common.EXIT_FAIL
    assert not (root / ".codebase-migrator-root.json").exists()


def test_adopt_writes_marker_only(work_root):
    root = work_root / "foreign"
    root.mkdir()
    (root / "some_file.txt").write_text("preexisting", encoding="utf-8")

    proc = _run(["--root", str(root), "--adopt"])
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 0
    assert out["outcome"] == "adopted"
    assert out["created"] is False
    assert (root / ".codebase-migrator-root.json").is_file()
    assert not (root / "migration.json").exists()


def test_marker_unreadable_or_wrong_schema_refused(work_root):
    root = work_root / "durable"
    root.mkdir()
    (root / ".codebase-migrator-root.json").write_text("not json", encoding="utf-8")
    proc = _run(["--root", str(root)])
    assert proc.returncode == cm_common.EXIT_CANNOT

    root2 = work_root / "durable2"
    root2.mkdir()
    (root2 / ".codebase-migrator-root.json").write_text(
        json.dumps({"schema": 2}), encoding="utf-8"
    )
    proc = _run(["--root", str(root2)])
    assert proc.returncode == cm_common.EXIT_CANNOT


def test_root_under_temp_root_refused(work_root):
    import tempfile

    temp_based_root = Path(tempfile.gettempdir()) / f"cm-test-{__import__('uuid').uuid4().hex}"
    proc = _run(["--root", str(temp_based_root)])
    assert proc.returncode == cm_common.EXIT_CANNOT
    assert not temp_based_root.exists()
