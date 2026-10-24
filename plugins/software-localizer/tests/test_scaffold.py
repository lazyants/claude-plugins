"""Tests for scaffold.py, driven as a real subprocess."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import lz_common

SCAFFOLD = Path(lz_common.__file__).resolve().parent / "scaffold.py"


def _run(*args):
    proc = subprocess.run(
        [sys.executable, str(SCAFFOLD), *args],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got: {proc.stdout!r}"
    return proc.returncode, json.loads(lines[0])


def test_fresh_scaffold_writes_localize_json_with_choose_sentinels(work_root):
    root = work_root / "R"
    code, payload = _run("--root", str(root))
    assert code == 0
    assert payload == {
        "ok": True, "outcome": "fresh", "created": True, "path": str(root / "localize.json"),
        "gitignore_created": True,
    }

    cfg = json.loads((root / "localize.json").read_text(encoding="utf-8"))
    assert cfg["schema"] == 1
    assert cfg["project_root"] == "CHOOSE_PROJECT_ROOT"
    assert cfg["source_locale"] == "CHOOSE_SOURCE_LOCALE"
    assert cfg["target_locales"] == "CHOOSE_TARGET_LOCALES"
    assert cfg["adapter"]["argv"] == "CHOOSE_ADAPTER_ARGV"
    assert cfg["batch_size"] == 40
    assert cfg["max_rounds"] == 3
    assert cfg["adapter_timeout_s"] == 300


def test_rerunning_scaffold_does_not_overwrite_an_edited_localize_json(work_root):
    root = work_root / "R"
    _run("--root", str(root))

    config_path = root / "localize.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg["source_locale"] = "en"  # simulate the operator having answered a question
    config_path.write_text(json.dumps(cfg), encoding="utf-8")

    code, payload = _run("--root", str(root))
    assert code == 0
    assert payload["outcome"] == "resumed"
    assert payload["created"] is False

    reread = json.loads(config_path.read_text(encoding="utf-8"))
    assert reread["source_locale"] == "en"


def test_scaffold_creates_the_root_directory(work_root):
    root = work_root / "does" / "not" / "exist" / "yet"
    assert not root.exists()
    code, payload = _run("--root", str(root))
    assert code == 0
    assert root.is_dir()


def test_scaffold_refuses_when_root_is_an_existing_file(work_root):
    root_as_file = work_root / "R"
    root_as_file.write_text("not a directory", encoding="utf-8")
    code, payload = _run("--root", str(root_as_file))
    assert code == 2
    assert payload["ok"] is False


def test_scaffold_missing_root_argument_exits_cannot_with_one_json_line():
    proc = subprocess.run([sys.executable, str(SCAFFOLD)], capture_output=True, text=True)
    assert proc.returncode == 2
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["ok"] is False


def test_fresh_scaffold_writes_the_exact_gitignore(work_root):
    root = work_root / "R"
    _run("--root", str(root))
    assert (root / ".gitignore").read_bytes() == (
        b"# software-localizer workspace: commit the decisions, keep run state local.\n"
        b'# Add a "!/<name>" line to commit another file or directory.\n'
        b"/*\n"
        b"!/.gitignore\n"
        b"!/localize.json\n"
        b"!/adapter/\n"
        b"!/canon.json\n"
        b"!/canon.lock.json\n"
        b"!/canon.history.json\n"
        b"__pycache__/\n"
    )


def test_existing_gitignore_is_kept_byte_for_byte(work_root):
    root = work_root / "R"
    root.mkdir()
    custom = b"# mine\n*.tmp\n"
    (root / ".gitignore").write_bytes(custom)

    code, payload = _run("--root", str(root))
    assert code == 0
    assert payload["gitignore_created"] is False
    assert (root / ".gitignore").read_bytes() == custom


def test_gitignore_commits_only_the_decisions_in_a_real_repository(work_root):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    project = work_root / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    root = project / "localization"
    _run("--root", str(root))

    durable = ["localize.json", "adapter/adapter.py", "canon.json", "canon.lock.json", "canon.history.json"]
    run_state = [
        "runs/de/x.json", "runs/_adapter_check.json", "ledger/de.json", "messages.json",
        "exports/20261024T000000Z/journal.json", "exports/.lock", "reports/de.md",
        "project-checks/baseline.txt", "adapter.lock.json", "tmpabc123/project/locales/de.json",
        ".lz-tmp-abc", "adapter/__pycache__/adapter.cpython-314.pyc",
    ]
    for rel in durable + run_state:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # keep scaffold's own localize.json
            path.write_text("x\n", encoding="utf-8")

    status = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    expected = {f"?? localization/{rel}" for rel in durable + [".gitignore"]}
    assert set(status) == expected
