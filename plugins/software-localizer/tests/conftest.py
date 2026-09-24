"""Shared pytest fixtures for the software-localizer test suite."""

import shutil
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_DIR / "skills" / "software-localizer" / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def work_root():
    """A fresh durable-root directory for one test, removed afterward.

    Every test that needs a real `--root` uses this fixture instead of
    pytest's own `tmp_path`, so every test's workspace lives in one place
    (`tests/.work/`) and is always cleaned up by removing exactly its own
    directory -- never a glob-delete over `.work` as a whole, which could
    catch a concurrently running test.
    """
    d = TESTS_DIR / ".work" / uuid.uuid4().hex
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
