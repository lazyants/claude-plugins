"""Tests for cm_common.py (plan section 7, owner A)."""

import io
import json
import os
import shutil
import sys

import pytest

import cm_common


def test_emit_escapes_u2028_and_writes_one_line(capsys):
    cm_common.emit({"note": "line1 line2"})
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    assert captured.out.endswith("\n")
    # ensure_ascii=True means the raw U+2028 codepoint never appears literally
    assert " " not in captured.out
    assert "\\u2028" in captured.out
    parsed = json.loads(captured.out)
    assert parsed["note"] == "line1 line2"


def test_atomic_write_text_leaves_no_temp_file(work_root):
    target = work_root / "sub" / "file.txt"
    cm_common.atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    leftovers = [p for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == []


def test_atomic_write_json_roundtrip(work_root):
    target = work_root / "data.json"
    cm_common.atomic_write_json(target, {"b": 2, "a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    leftovers = [p for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == []


def test_tree_digests_add_remove_change_and_symlink(work_root):
    base = work_root / "tree"
    (base / "sub").mkdir(parents=True)
    (base / "a.txt").write_text("one", encoding="utf-8")
    (base / "sub" / "b.txt").write_text("two", encoding="utf-8")

    before = cm_common.tree_digests(base)
    assert set(before) == {"a.txt", "sub/b.txt"}

    # change
    (base / "a.txt").write_text("changed", encoding="utf-8")
    after_change = cm_common.tree_digests(base)
    assert after_change["a.txt"] != before["a.txt"]
    assert after_change["sub/b.txt"] == before["sub/b.txt"]

    # add
    (base / "c.txt").write_text("three", encoding="utf-8")
    after_add = cm_common.tree_digests(base)
    assert "c.txt" in after_add and "c.txt" not in before

    # remove
    (base / "c.txt").unlink()
    after_remove = cm_common.tree_digests(base)
    assert "c.txt" not in after_remove

    # symlink recorded, not followed
    outside_target = work_root / "outside.txt"
    outside_target.write_text("outside", encoding="utf-8")
    link = base / "link.txt"
    os.symlink(outside_target, link)
    digests = cm_common.tree_digests(base)
    assert digests["link.txt"] == f"symlink:{outside_target}"

    linked_dir = base / "linked_dir"
    real_dir = work_root / "real_dir"
    real_dir.mkdir()
    (real_dir / "hidden.txt").write_text("hidden", encoding="utf-8")
    os.symlink(real_dir, linked_dir)
    digests_with_dir_link = cm_common.tree_digests(base)
    assert digests_with_dir_link["linked_dir"] == f"symlink:{real_dir}"
    assert "linked_dir/hidden.txt" not in digests_with_dir_link


def test_under_temp_root(work_root):
    for root in cm_common.TEMP_ROOTS:
        assert cm_common.under_temp_root(root)
        assert cm_common.under_temp_root(os.path.join(root, "sub", "dir"))
    assert not cm_common.under_temp_root(work_root)


def test_closure_digests_equal_for_copy_and_differ_after_change(work_root):
    legacy = work_root / "legacy"
    (legacy / "pkg").mkdir(parents=True)
    (legacy / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "pkg" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (legacy / "pkg" / "b.py").write_text("VALUE = 2\n", encoding="utf-8")

    units = ["pkg.a", "pkg.b"]
    digests_1 = cm_common.closure_digests(legacy, "pkg", units)

    copy_root = work_root / "legacy_copy"
    shutil.copytree(legacy, copy_root)
    digests_2 = cm_common.closure_digests(copy_root, "pkg", units)
    assert digests_1 == digests_2

    (legacy / "pkg" / "b.py").write_text("VALUE = 999\n", encoding="utf-8")
    digests_3 = cm_common.closure_digests(legacy, "pkg", units)
    assert digests_3["pkg.a"] == digests_1["pkg.a"]
    assert digests_3["pkg.b"] != digests_1["pkg.b"]


def test_require_unit_rejects_traversal_absolute_and_unknown(work_root, capsys):
    inventory = {"schema": 1, "units": {"shop.pricing": {}}}
    cm_common.atomic_write_json(work_root / "inventory.json", inventory)

    for bad_unit in ("../x", "/etc/passwd", "shop.nonexistent"):
        with pytest.raises(SystemExit) as exc_info:
            cm_common.require_unit(work_root, bad_unit)
        assert exc_info.value.code == cm_common.EXIT_CANNOT
        capsys.readouterr()

    assert cm_common.require_unit(work_root, "shop.pricing") == "shop.pricing"
