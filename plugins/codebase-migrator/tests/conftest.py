"""Shared pytest fixtures for the codebase-migrator test suite."""

import shutil
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_DIR / "skills" / "codebase-migrator" / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def work_root():
    """A durable-root directory outside any temp root, git-ignored by the
    plugin's `.gitignore`, removed after the test.

    `pytest`'s own `tmp_path` sits under the system temp root on both macOS
    and Linux, which the plugin refuses as a durable root (see
    `cm_common.TEMP_ROOTS`). Every test that needs a real durable root uses
    this fixture instead; `tmp_path` stays fine for stages and throwaway
    files that are never passed to the plugin as `--root`.
    """
    d = TESTS_DIR / ".work" / uuid.uuid4().hex
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
