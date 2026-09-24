"""Tests for `collect.py`: collect the project's current strings into
`R/messages.json`, refusing unless the adapter is accepted.

Reuses `test_adapter_check.py`'s workspace helpers (the toy fixture adapter
and project) instead of duplicating them.
"""

from __future__ import annotations

import json
import os

import pytest

import lz_common
from test_adapter_check import ADAPTER_CHECK, COLLECT, _make_workspace, _run, _write_coverage


def _accept(root, by="tester"):
    coverage_path = root.parent / "coverage.json"
    _write_coverage(root, coverage_path, [])
    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", by]
    )
    assert code == 0, reply
    return reply


def test_collect_writes_messages_json_after_acceptance(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 0
    _accept(root)

    code, reply = _run(COLLECT, ["--root", str(root)])

    assert code == 0
    assert reply["ok"] is True
    assert reply["count"] == 12
    assert sorted(reply["files"]) == ["locales/de.json", "locales/en.json", "locales/ru.json"]

    messages_path = root / "messages.json"
    assert messages_path.is_file()
    messages = lz_common.load_messages(messages_path)
    assert len(messages["messages"]) == 12
    by_id = {m["id"]: m for m in messages["messages"]}
    assert by_id["footer.copyright"]["targets"]["de"] is None
    assert by_id["footer.copyright"]["targets"]["ru"] == "Все права защищены"
    cart = by_id["cart.itemCount"]
    assert cart["plural"]["general_index"] == 1
    assert len(cart["plural"]["target_labels"]["ru"]) == 3


def test_collect_accepts_a_relative_root_from_a_different_cwd(work_root):
    # Same hazard as adapter_check.py's `run`: an unresolved relative --root
    # stays relative all the way into the adapter subprocess's argv, which
    # runs with cwd=project_dir -- a directory the relative root string was
    # never valid from.
    #
    # `elsewhere` is a sibling of R (one ".." level), not some unrelated
    # deeply-nested tmp directory: with enough ".." segments a broken
    # relative path can walk past the filesystem root and coincide with the
    # right absolute path by pure depth accident, masking the bug this test
    # exists to catch.
    root, project_dir, cfg = _make_workspace(work_root)
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 0
    _accept(root)

    elsewhere = work_root / "elsewhere"
    elsewhere.mkdir()
    rel_root = os.path.relpath(root, start=elsewhere)
    assert rel_root == "../R"

    code, reply = _run(COLLECT, ["--root", rel_root], cwd=elsewhere)

    assert code == 0, reply
    assert reply["ok"] is True


def test_collect_refuses_without_an_accepted_adapter(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    # No adapter_check.py run/accept at all -- there is no adapter.lock.json yet.

    code, reply = _run(COLLECT, ["--root", str(root)])

    assert code == lz_common.EXIT_FAIL
    assert reply["ok"] is False
    assert not (root / "messages.json").exists()


def test_collect_refuses_after_the_adapter_file_changes(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 0
    _accept(root)

    # A first collect succeeds while the acceptance is still current.
    code, _ = _run(COLLECT, ["--root", str(root)])
    assert code == 0

    adapter_file = root / "adapter" / "adapter.py"
    adapter_file.write_text(adapter_file.read_text(encoding="utf-8") + "\n# mutated after acceptance\n", encoding="utf-8")

    code, reply = _run(COLLECT, ["--root", str(root)])

    assert code == lz_common.EXIT_FAIL
    assert reply["ok"] is False
