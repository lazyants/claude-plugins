"""Tests for cm_common.py (plan section 7, owner A)."""

import io
import json
import os
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


def test_file_digests_hashes_relpaths_and_refuses_a_missing_one(work_root):
    base = work_root / "base"
    (base / "sub").mkdir(parents=True)
    (base / "a.py").write_text("A = 1\n", encoding="utf-8")
    (base / "sub" / "b.py").write_text("B = 2\n", encoding="utf-8")

    digests = cm_common.file_digests(base, ["a.py", "sub/b.py"])
    assert digests == {
        "a.py": cm_common.sha256_file(base / "a.py"),
        "sub/b.py": cm_common.sha256_file(base / "sub" / "b.py"),
    }

    with pytest.raises(SystemExit) as exc_info:
        cm_common.file_digests(base, ["a.py", "missing.py"])
    assert exc_info.value.code == cm_common.EXIT_CANNOT


def test_file_digests_refuses_missing_file_names_it(work_root, capsys):
    base = work_root / "base"
    base.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        cm_common.file_digests(base, ["missing.py"])
    assert exc_info.value.code == cm_common.EXIT_CANNOT

    captured = capsys.readouterr()
    assert "missing.py" in captured.err
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "missing.py" in payload["error"]


def test_unit_closure_includes_ancestor_package_units(work_root):
    # pkg/sub/__init__.py has real code, so it is a unit in its own right
    # (unlike a docstring-only package __init__.py); importing pkg.sub.mod
    # always executes pkg.sub's __init__.py first, so it is an implicit
    # dependency that must be in the closure even though nothing declares
    # it in imports_units.
    legacy = work_root / "legacy"
    (legacy / "pkg" / "sub").mkdir(parents=True)
    (legacy / "pkg" / "__init__.py").write_text('"""docstring only."""\n', encoding="utf-8")
    (legacy / "pkg" / "sub" / "__init__.py").write_text("CONST = 1\n", encoding="utf-8")
    (legacy / "pkg" / "sub" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    inventory = {
        "schema": 1,
        "units": {
            "pkg.sub": {"imports_units": []},
            "pkg.sub.mod": {"imports_units": []},
        },
    }
    closure = cm_common.unit_closure(inventory, "pkg.sub.mod")
    assert closure == ["pkg.sub", "pkg.sub.mod"]
    # "pkg" itself is never added: it is not a key of inventory["units"],
    # i.e. not a unit at all (docstring-only __init__.py).
    assert "pkg" not in closure


def test_make_parser_error_emits_one_json_line_exit_2(capsys):
    parser = cm_common.make_parser(prog="test-prog", description="d")
    parser.add_argument("--root", required=True)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--nonexistent-flag", "x"])
    assert exc_info.value.code == cm_common.EXIT_CANNOT
    out = capsys.readouterr().out
    lines = [line for line in out.strip().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False


def test_make_parser_subcommand_bad_flag_emits_one_json_line_exit_2(capsys):
    # add_subparsers() defaults parser_class to type(self): a subcommand
    # parser must inherit the same failure behaviour with no extra wiring.
    parser = cm_common.make_parser(prog="test-prog", description="d")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("go")
    p.add_argument("--unit", required=True)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["go", "--nonexistent-flag", "x"])
    assert exc_info.value.code == cm_common.EXIT_CANNOT
    out = capsys.readouterr().out
    lines = [line for line in out.strip().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False

    # a missing required subcommand goes through the TOP-LEVEL parser's
    # error(), which must behave the same way.
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([])
    assert exc_info.value.code == cm_common.EXIT_CANNOT
    out = capsys.readouterr().out
    lines = [line for line in out.strip().splitlines() if line.strip()]
    assert len(lines) == 1
    json.loads(lines[0])


def test_require_unit_rejects_traversal_absolute_and_unknown(work_root, capsys):
    inventory = {"schema": 1, "units": {"shop.pricing": {}}}
    cm_common.atomic_write_json(work_root / "inventory.json", inventory)

    for bad_unit in ("../x", "/etc/passwd", "shop.nonexistent"):
        with pytest.raises(SystemExit) as exc_info:
            cm_common.require_unit(work_root, bad_unit)
        assert exc_info.value.code == cm_common.EXIT_CANNOT
        capsys.readouterr()

    assert cm_common.require_unit(work_root, "shop.pricing") == "shop.pricing"
