"""Tests for scaffold.py, driven as a real subprocess (plan sections 2-3)."""

import json
import subprocess
import sys
from pathlib import Path

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
    assert payload == {"ok": True, "outcome": "fresh", "created": True, "path": str(root / "localize.json")}

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
