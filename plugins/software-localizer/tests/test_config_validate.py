"""Tests for config_validate.py, driven as a real subprocess (plan section 3).

The validation RULES themselves are covered in depth by test_lz_common.py
against `lz_common.validate_config`; these tests cover the script's own
CLI contract (argv, exit codes, the one JSON line).
"""

import json
import subprocess
import sys
from pathlib import Path

import lz_common

CONFIG_VALIDATE = Path(lz_common.__file__).resolve().parent / "config_validate.py"


def _run(root):
    proc = subprocess.run(
        [sys.executable, str(CONFIG_VALIDATE), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got: {proc.stdout!r}"
    return proc.returncode, json.loads(lines[0])


def test_valid_config_exits_ok(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = {
        "schema": 1,
        "project_root": str(project),
        "source_locale": "en",
        "target_locales": ["de"],
        "adapter": {"argv": [sys.executable, "-c", "pass"], "options": {}},
        "style": {"de": {"formality": "Sie", "notes": ""}},
        "allow_identical": [],
        "batch_size": 40,
        "max_rounds": 3,
        "adapter_timeout_s": 300,
    }
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    code, payload = _run(work_root)
    assert code == 0
    assert payload == {"ok": True, "problems": []}


def test_config_with_choose_sentinels_exits_fail_with_problems(work_root):
    cfg = {
        "schema": 1,
        "project_root": "CHOOSE_PROJECT_ROOT",
        "source_locale": "CHOOSE_SOURCE_LOCALE",
        "target_locales": "CHOOSE_TARGET_LOCALES",
        "adapter": {"argv": "CHOOSE_ADAPTER_ARGV", "options": {}},
        "style": {},
        "allow_identical": [],
        "batch_size": 40,
        "max_rounds": 3,
        "adapter_timeout_s": 300,
    }
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    code, payload = _run(work_root)
    assert code == 1
    assert payload["ok"] is False
    assert payload["problems"]


def test_missing_localize_json_exits_cannot(work_root):
    code, payload = _run(work_root)
    assert code == 2
    assert payload["ok"] is False


def test_root_not_a_directory_exits_cannot(tmp_path):
    code, payload = _run(tmp_path / "does-not-exist")
    assert code == 2
    assert payload["ok"] is False
