"""Tests for lz_common.py: the shared library every other script imports."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import lz_common


# ---------------------------------------------------------------------------
# emit / fail / run_main / make_parser
# ---------------------------------------------------------------------------


def test_emit_prints_one_sorted_ascii_json_line(capsys):
    lz_common.emit({"b": 1, "a": "café"})
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0] == '{"a": "caf\\u00e9", "b": 1}'
    assert json.loads(lines[0]) == {"a": "café", "b": 1}


def test_fail_emits_ok_false_with_extra_fields_and_exits(capsys):
    with pytest.raises(SystemExit) as exc_info:
        lz_common.fail("bad thing", lz_common.EXIT_FAIL, extra="x")
    assert exc_info.value.code == lz_common.EXIT_FAIL
    out = capsys.readouterr()
    payload = json.loads(out.out.splitlines()[0])
    assert payload == {"ok": False, "error": "bad thing", "extra": "x"}
    assert "bad thing" in out.err


def test_fail_default_code_is_exit_fail(capsys):
    with pytest.raises(SystemExit) as exc_info:
        lz_common.fail("nope")
    assert exc_info.value.code == lz_common.EXIT_FAIL


def test_run_main_returns_the_exit_code(capsys):
    with pytest.raises(SystemExit) as exc_info:
        lz_common.run_main(lambda: 7)
    assert exc_info.value.code == 7


def test_run_main_converts_uncaught_exception_to_exit_cannot(capsys):
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(SystemExit) as exc_info:
        lz_common.run_main(boom)
    assert exc_info.value.code == lz_common.EXIT_CANNOT
    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert payload["ok"] is False
    assert "kaboom" in payload["error"]


def test_run_main_lets_system_exit_and_keyboard_interrupt_propagate():
    def raises_system_exit():
        raise SystemExit(3)

    with pytest.raises(SystemExit) as exc_info:
        lz_common.run_main(raises_system_exit)
    assert exc_info.value.code == 3


def test_make_parser_bad_argument_emits_one_json_line_and_exits_cannot(capsys):
    parser = lz_common.make_parser(prog="x", description="d")
    parser.add_argument("--root", required=True)
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([])
    assert exc_info.value.code == lz_common.EXIT_CANNOT
    out = capsys.readouterr().out
    payload = json.loads(out.splitlines()[0])
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# read_json / atomic writes
# ---------------------------------------------------------------------------


def test_read_json_round_trips(work_root):
    path = work_root / "a.json"
    lz_common.atomic_write_json(path, {"x": 1})
    assert lz_common.read_json(path, "a.json") == {"x": 1}


def test_read_json_missing_file_fails_cannot(work_root, capsys):
    with pytest.raises(SystemExit) as exc_info:
        lz_common.read_json(work_root / "missing.json", "missing.json")
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_read_json_invalid_json_fails_cannot(work_root, capsys):
    path = work_root / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        lz_common.read_json(path, "bad.json")
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_atomic_write_text_creates_parent_dirs_and_leaves_no_temp_file(work_root):
    path = work_root / "sub" / "dir" / "f.txt"
    lz_common.atomic_write_text(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".lz-tmp-")]
    assert leftovers == []


def test_atomic_write_json_preserves_non_ascii_and_is_pretty(work_root):
    path = work_root / "u.json"
    lz_common.atomic_write_json(path, {"greeting": "日本"})
    text = path.read_text(encoding="utf-8")
    assert "日本" in text
    assert "\n" in text  # indented, not a single line


def test_resolve_root_fails_cannot_on_non_directory(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        lz_common.resolve_root(tmp_path / "does-not-exist")
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_resolve_root_returns_resolved_path(work_root):
    assert lz_common.resolve_root(work_root) == work_root.resolve()


# ---------------------------------------------------------------------------
# sha256 helpers / value_sha256
# ---------------------------------------------------------------------------


def test_sha256_bytes_and_text_agree():
    assert lz_common.sha256_text("hello") == lz_common.sha256_bytes(b"hello")


def test_sha256_file_matches_sha256_bytes(work_root):
    path = work_root / "f.bin"
    path.write_bytes(b"some content \xff\x00")
    assert lz_common.sha256_file(path) == lz_common.sha256_bytes(b"some content \xff\x00")


def test_sha256_json_is_order_independent_and_keeps_unicode_literal():
    a = lz_common.sha256_json({"x": 1, "y": "日本"})
    b = lz_common.sha256_json({"y": "日本", "x": 1})
    assert a == b
    text = json.dumps({"x": 1, "y": "日本"}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert a == lz_common.sha256_bytes(text.encode("utf-8"))


def test_value_sha256_accepts_string():
    assert lz_common.value_sha256("hi") == lz_common.sha256_json("hi")


def test_value_sha256_accepts_forms_dict():
    value = {"forms": ["a", "b"]}
    assert lz_common.value_sha256(value) == lz_common.sha256_json(value)


def test_value_sha256_rejects_other_shapes():
    with pytest.raises(TypeError):
        lz_common.value_sha256(42)
    with pytest.raises(TypeError):
        lz_common.value_sha256({"not_forms": []})


def test_now_iso_shape():
    stamp = lz_common.now_iso()
    assert stamp.endswith("Z")
    assert "." not in stamp
    assert len(stamp) == len("2024-01-02T03:04:05Z")


# ---------------------------------------------------------------------------
# validate_config / load_config
# ---------------------------------------------------------------------------


def _valid_config(project_root: Path) -> dict:
    return {
        "schema": 1,
        "project_root": str(project_root),
        "source_locale": "en",
        "target_locales": ["de", "ru"],
        "adapter": {"argv": [sys.executable, "-c", "pass"], "options": {}},
        "style": {
            "de": {"formality": "Sie", "notes": ""},
            "ru": {"formality": "вы", "notes": ""},
        },
        "allow_identical": [],
        "batch_size": 40,
        "max_rounds": 3,
        "adapter_timeout_s": 300,
    }


def test_validate_config_accepts_a_well_formed_config(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    assert lz_common.validate_config(cfg, work_root) == []


def test_validate_config_flags_every_remaining_choose_sentinel(work_root):
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
    problems = lz_common.validate_config(cfg, work_root)
    fields = {p["field"] for p in problems}
    assert "project_root" in fields
    assert "source_locale" in fields
    assert "target_locales" in fields
    assert "adapter.argv" in fields


def test_validate_config_reports_every_problem_at_once(work_root):
    cfg = {
        "schema": 2,
        "project_root": "CHOOSE_PROJECT_ROOT",
        "source_locale": "",
        "target_locales": ["en", "en"],
        "adapter": {"argv": [], "options": {}},
        "style": {},
    }
    problems = lz_common.validate_config(cfg, work_root)
    fields = {p["field"] for p in problems}
    assert "schema" in fields
    assert "source_locale" in fields
    assert any(f.startswith("target_locales[") for f in fields)
    assert "adapter.argv" in fields


def test_validate_config_refuses_project_root_equal_to_workspace_root(work_root):
    cfg = _valid_config(work_root)
    cfg["project_root"] = str(work_root)
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "project_root" for p in problems)


def test_validate_config_refuses_project_root_inside_workspace_root(work_root):
    inside = work_root / "proj"
    inside.mkdir()
    cfg = _valid_config(inside)
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "project_root" for p in problems)


def test_validate_config_refuses_project_root_containing_workspace_root(work_root, tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    nested_root = outer / "nested" / "root"
    nested_root.mkdir(parents=True)
    cfg = _valid_config(outer)
    problems = lz_common.validate_config(cfg, nested_root)
    assert any(p["field"] == "project_root" for p in problems)


def test_validate_config_refuses_source_locale_among_targets(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["source_locale"] = "de"
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "target_locales" for p in problems)


def test_validate_config_refuses_style_missing_or_extra_locale(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    del cfg["style"]["ru"]
    cfg["style"]["fr"] = {"formality": "vous", "notes": ""}
    problems = lz_common.validate_config(cfg, work_root)
    fields = {p["field"] for p in problems}
    assert "style.ru" in fields
    assert "style.fr" in fields


def test_validate_config_refuses_unresolvable_adapter_argv(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["adapter"]["argv"] = ["no-such-command-anywhere-xyz"]
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "adapter.argv[0]" for p in problems)


def test_validate_config_accepts_adapter_argv_relative_to_root(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("# adapter\n", encoding="utf-8")
    cfg = _valid_config(project)
    cfg["adapter"]["argv"] = ["adapter/adapter.py"]
    problems = lz_common.validate_config(cfg, work_root)
    assert not any(p["field"].startswith("adapter.argv") for p in problems)


def test_validate_config_refuses_a_project_relative_adapter_script(work_root, tmp_path):
    # A script that exists only under the project, not under R, used to run
    # anyway: `adapter_client` invokes the adapter with `cwd=project_dir`, so
    # the OS resolved this relative element against the project, not R --
    # while `adapter_digest` looked for it under R and never found it, so it
    # was never hashed either. Refusing it here (a relative element that
    # LOOKS like a path -- it has a separator -- and is not under R) closes
    # that gap before `load_config` can ever hand it to either.
    project = tmp_path / "project"
    project.mkdir()
    (project / "scripts").mkdir()
    (project / "scripts" / "adapter.js").write_text("", encoding="utf-8")
    cfg = _valid_config(project)
    cfg["adapter"]["argv"] = [sys.executable, "scripts/adapter.js"]
    problems = lz_common.validate_config(cfg, work_root)
    matching = [p for p in problems if p["field"] == "adapter.argv[1]"]
    assert len(matching) == 1
    assert "workspace" in matching[0]["message"]


@pytest.mark.parametrize("field", ["batch_size", "max_rounds", "adapter_timeout_s"])
@pytest.mark.parametrize("bad_value", [0, -1, "40", 1.5, True])
def test_validate_config_refuses_non_positive_int_tuning_fields(work_root, tmp_path, field, bad_value):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg[field] = bad_value
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == field for p in problems)


def test_validate_config_accepts_tuning_fields_absent(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    del cfg["batch_size"]
    del cfg["max_rounds"]
    del cfg["adapter_timeout_s"]
    assert lz_common.validate_config(cfg, work_root) == []


def test_validate_config_refuses_allow_identical_not_a_list_of_strings(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["allow_identical"] = ["m1", 2]
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "allow_identical" for p in problems)


def test_validate_config_refuses_a_path_unsafe_source_locale(work_root, tmp_path):
    # A locale is joined straight into output paths (ledger.py); an
    # unrestricted string lets "../../outside" escape the workspace tree.
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["source_locale"] = "../x"
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "source_locale" for p in problems)


def test_validate_config_refuses_a_path_unsafe_target_locale(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["target_locales"] = ["../../outside"]
    del cfg["style"]["de"]
    del cfg["style"]["ru"]
    problems = lz_common.validate_config(cfg, work_root)
    assert any(p["field"] == "target_locales[0]" for p in problems)


def test_validate_config_accepts_locale_identifiers_with_underscore_and_hyphen(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["source_locale"] = "en_US"
    cfg["target_locales"] = ["pt-BR"]
    cfg["style"] = {"pt-BR": {"formality": "você", "notes": ""}}
    assert lz_common.validate_config(cfg, work_root) == []


def test_load_config_fails_cannot_with_problems(work_root, capsys):
    lz_common.atomic_write_json(work_root / "localize.json", {"schema": 1})
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_config(work_root)
    assert exc_info.value.code == lz_common.EXIT_CANNOT
    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert payload["problems"]


def test_load_config_returns_cfg_when_valid(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    loaded = lz_common.load_config(work_root)
    assert loaded["source_locale"] == "en"


def test_load_config_fills_documented_defaults_when_absent(work_root, tmp_path):
    # validate_config accepts these fields absent (they are optional); a
    # consumer indexing cfg["adapter_timeout_s"] straight (adapter_client.run
    # does) used to KeyError when they were never filled in anywhere.
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    del cfg["batch_size"]
    del cfg["max_rounds"]
    del cfg["adapter_timeout_s"]
    del cfg["allow_identical"]
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    loaded = lz_common.load_config(work_root)

    assert loaded["batch_size"] == 40
    assert loaded["max_rounds"] == 3
    assert loaded["adapter_timeout_s"] == 300
    assert loaded["allow_identical"] == []


def test_load_config_runs_an_adapter_command_when_tuning_fields_were_absent(work_root, tmp_path):
    # End-to-end version of the above: a config missing adapter_timeout_s
    # must still let adapter_client.run index cfg["adapter_timeout_s"]
    # directly, because load_config is the one place that fills it in.
    project = tmp_path / "project"
    project.mkdir()
    adapter_script = work_root / "adapter.py"
    adapter_script.write_text(
        "import json, sys\n"
        "print(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    cfg = _valid_config(project)
    cfg["adapter"]["argv"] = [sys.executable, "adapter.py"]
    del cfg["batch_size"]
    del cfg["max_rounds"]
    del cfg["adapter_timeout_s"]
    del cfg["allow_identical"]
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    loaded = lz_common.load_config(work_root)

    import adapter_client

    reply = adapter_client.run(str(work_root), loaded, str(project), "collect", [])
    assert reply == {"ok": True}


def test_load_config_resolves_relative_project_root_against_root(tmp_path, monkeypatch):
    # A relative project_root resolves against `root` (R), not the process's
    # current working directory -- a script run from anywhere else must
    # still target the same, R-relative, directory. (project_root must sit
    # outside R, so it is a sibling here, reached by "../project".)
    root = tmp_path / "R"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    cfg["project_root"] = "../project"
    lz_common.atomic_write_json(root / "localize.json", cfg)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    loaded = lz_common.load_config(root)
    assert loaded["project_root"] == str(project.resolve())


def test_load_config_leaves_an_absolute_project_root_unchanged(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_config(project)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    loaded = lz_common.load_config(work_root)
    assert loaded["project_root"] == str(project.resolve())


# ---------------------------------------------------------------------------
# load_messages
# ---------------------------------------------------------------------------


def _write_messages(work_root, data) -> Path:
    path = work_root / "messages.json"
    lz_common.atomic_write_json(path, data)
    return path


def test_load_messages_accepts_a_non_plural_message(work_root):
    data = {
        "schema": 1,
        "files": ["a.json"],
        "messages": [
            {
                "id": "x.y",
                "source": "Hello {name}",
                "context": {"file": "a.json", "key": "x.y", "max_length": None, "comment": None},
                "targets": {"de": "Hallo {name}", "ru": None},
            }
        ],
    }
    path = _write_messages(work_root, data)
    assert lz_common.load_messages(path) == data


def test_load_messages_accepts_a_plural_message(work_root):
    data = {
        "schema": 1,
        "files": ["a.json"],
        "messages": [
            {
                "id": "x.plural",
                "source": {"forms": ["{n} item", "{n} items"]},
                "plural": {
                    "source_labels": [{"label": "one", "exact": True}, {"label": "other", "exact": False}],
                    "target_labels": {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
                    "count_arguments": ["n"],
                    "general_index": 1,
                },
                "context": {"file": "a.json", "key": "x.plural", "max_length": None, "comment": None},
                "targets": {"de": {"forms": ["{n} Stück", "{n} Stücke"]}},
            }
        ],
    }
    path = _write_messages(work_root, data)
    assert lz_common.load_messages(path) == data


def test_load_messages_fails_on_duplicate_id(work_root, capsys):
    data = {
        "schema": 1,
        "files": [],
        "messages": [
            {"id": "x", "source": "a", "context": {"file": "f", "key": "x", "max_length": None, "comment": None}, "targets": {}},
            {"id": "x", "source": "b", "context": {"file": "f", "key": "x", "max_length": None, "comment": None}, "targets": {}},
        ],
    }
    path = _write_messages(work_root, data)
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_messages(path)
    assert exc_info.value.code == lz_common.EXIT_CANNOT
    assert "duplicate" in capsys.readouterr().out


def test_load_messages_fails_when_general_index_out_of_range(work_root):
    data = {
        "schema": 1,
        "files": [],
        "messages": [
            {
                "id": "x",
                "source": {"forms": ["a", "b"]},
                "plural": {
                    "source_labels": [{"label": "one", "exact": True}, {"label": "other", "exact": False}],
                    "target_labels": {},
                    "count_arguments": ["n"],
                    "general_index": 5,
                },
                "context": {"file": "f", "key": "x", "max_length": None, "comment": None},
                "targets": {},
            }
        ],
    }
    path = _write_messages(work_root, data)
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_messages(path)
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_load_messages_fails_when_target_forms_length_mismatches_label_count(work_root):
    data = {
        "schema": 1,
        "files": [],
        "messages": [
            {
                "id": "x",
                "source": {"forms": ["a", "b"]},
                "plural": {
                    "source_labels": [{"label": "one", "exact": True}, {"label": "other", "exact": False}],
                    "target_labels": {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
                    "count_arguments": ["n"],
                    "general_index": 1,
                },
                "context": {"file": "f", "key": "x", "max_length": None, "comment": None},
                "targets": {"de": {"forms": ["only one form"]}},
            }
        ],
    }
    path = _write_messages(work_root, data)
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_messages(path)
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_load_messages_fails_on_wrong_schema(work_root):
    path = _write_messages(work_root, {"schema": 2, "files": [], "messages": []})
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_messages(path)
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_load_messages_accepts_a_zero_max_length(work_root):
    data = {
        "schema": 1,
        "files": [],
        "messages": [
            {
                "id": "x", "source": "a",
                "context": {"file": "f", "key": "x", "max_length": 0, "comment": None},
                "targets": {},
            }
        ],
    }
    path = _write_messages(work_root, data)
    assert lz_common.load_messages(path) == data


def test_load_messages_fails_on_a_negative_max_length(work_root):
    data = {
        "schema": 1,
        "files": [],
        "messages": [
            {
                "id": "x", "source": "a",
                "context": {"file": "f", "key": "x", "max_length": -1, "comment": None},
                "targets": {},
            }
        ],
    }
    path = _write_messages(work_root, data)
    with pytest.raises(SystemExit) as exc_info:
        lz_common.load_messages(path)
    assert exc_info.value.code == lz_common.EXIT_CANNOT


# ---------------------------------------------------------------------------
# resolve_argv
# ---------------------------------------------------------------------------


def test_resolve_argv_resolves_relative_file_under_root(work_root):
    (work_root / "adapter.py").write_text("", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter.py"]}}
    resolved = lz_common.resolve_argv(work_root, cfg)
    assert resolved[0] == "python3"
    assert resolved[1] == str(work_root / "adapter.py")


def test_resolve_argv_leaves_bare_command_that_is_not_a_file_under_root(work_root):
    cfg = {"adapter": {"argv": ["python3", "adapter.py"]}}
    resolved = lz_common.resolve_argv(work_root, cfg)
    assert resolved == ["python3", "adapter.py"]


def test_resolve_argv_leaves_absolute_path_untouched(work_root):
    cfg = {"adapter": {"argv": ["/usr/bin/env", "adapter.py"]}}
    resolved = lz_common.resolve_argv(work_root, cfg)
    assert resolved[0] == "/usr/bin/env"


# ---------------------------------------------------------------------------
# adapter_digest / require_accepted_adapter
# ---------------------------------------------------------------------------


def test_adapter_digest_hashes_the_whole_adapter_dir_when_present(work_root):
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("one", encoding="utf-8")
    (adapter_dir / "helper.py").write_text("two", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {"k": "v"}}}

    digest = lz_common.adapter_digest(work_root, cfg)

    assert set(digest["files"]) == {"adapter/adapter.py", "adapter/helper.py"}
    assert digest["files"]["adapter/adapter.py"] == lz_common.sha256_file(adapter_dir / "adapter.py")
    assert digest["options_sha256"] == lz_common.sha256_json({"k": "v"})


def test_adapter_digest_hashes_outside_script_when_no_adapter_dir(work_root, tmp_path):
    script = tmp_path / "adapter_outside.py"
    script.write_text("outside", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", str(script)], "options": {}}}

    digest = lz_common.adapter_digest(work_root, cfg)

    assert list(digest["files"].values()) == [lz_common.sha256_file(script)]


def test_adapter_digest_fails_cannot_when_nothing_found(work_root):
    cfg = {"adapter": {"argv": ["python3"], "options": {}}}
    with pytest.raises(SystemExit) as exc_info:
        lz_common.adapter_digest(work_root, cfg)
    assert exc_info.value.code == lz_common.EXIT_CANNOT


def test_adapter_digest_changes_when_a_file_changes(work_root):
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {}}}
    before = lz_common.adapter_digest(work_root, cfg)
    (adapter_dir / "adapter.py").write_text("v2", encoding="utf-8")
    after = lz_common.adapter_digest(work_root, cfg)
    assert before != after


def test_adapter_digest_changes_when_argv_changes(work_root):
    # The digest must cover what actually executes, not just file content:
    # a changed argument invalidates the lock even when adapter.py itself
    # is untouched.
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("same content", encoding="utf-8")
    cfg_before = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {}}}
    cfg_after = {"adapter": {"argv": ["python3", "adapter/adapter.py", "--strict"], "options": {}}}

    before = lz_common.adapter_digest(work_root, cfg_before)
    after = lz_common.adapter_digest(work_root, cfg_after)

    assert before != after
    assert before["argv"] == ["python3", "adapter/adapter.py"]
    assert after["argv"] == ["python3", "adapter/adapter.py", "--strict"]


def test_adapter_digest_hashes_an_argv_script_outside_root_even_with_an_adapter_dir(work_root, tmp_path):
    # `R/adapter/` having content must not suppress hashing an argv element
    # that resolves to a file outside R -- both sources are additive.
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("dir content", encoding="utf-8")
    external = tmp_path / "outside_helper.py"
    external.write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", str(external)], "options": {}}}

    before = lz_common.adapter_digest(work_root, cfg)
    assert str(external.resolve()) in before["files"]

    external.write_text("v2 -- changed", encoding="utf-8")
    after = lz_common.adapter_digest(work_root, cfg)

    assert before != after


def test_require_accepted_adapter_fails_when_lock_missing(work_root):
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {}}}
    with pytest.raises(SystemExit) as exc_info:
        lz_common.require_accepted_adapter(work_root, cfg)
    assert exc_info.value.code == lz_common.EXIT_FAIL


def test_require_accepted_adapter_passes_when_lock_matches(work_root):
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {}}}
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.require_accepted_adapter(work_root, cfg)  # must not raise


def test_require_accepted_adapter_fails_when_adapter_changed_since_lock(work_root):
    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", "adapter/adapter.py"], "options": {}}}
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)

    (adapter_dir / "adapter.py").write_text("v2 -- changed", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        lz_common.require_accepted_adapter(work_root, cfg)
    assert exc_info.value.code == lz_common.EXIT_FAIL


def test_require_accepted_adapter_fails_when_an_absolute_external_script_changes(work_root, tmp_path):
    # No R/adapter/ tree at all here -- the only file identifying the
    # adapter is an absolute argv element outside R. adapter_digest must go
    # through the same resolve_argv rule adapter_client.run executes, so
    # this is hashed too, and editing it after acceptance invalidates the
    # lock exactly like an in-tree adapter file would.
    script = tmp_path / "adapter_outside.py"
    script.write_text("v1", encoding="utf-8")
    cfg = {"adapter": {"argv": ["python3", str(script)], "options": {}}}
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.require_accepted_adapter(work_root, cfg)  # accepted: does not raise

    script.write_text("v2 -- changed", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        lz_common.require_accepted_adapter(work_root, cfg)
    assert exc_info.value.code == lz_common.EXIT_FAIL


# ---------------------------------------------------------------------------
# CLI smoke: importing and running lz_common itself is not a script, but a
# subprocess run of a downstream script proves make_parser/run_main/emit
# work end to end as actual argv processing, not just as importable calls.
# ---------------------------------------------------------------------------


def test_cli_bad_argument_via_subprocess_prints_one_json_line(work_root):
    scripts_dir = Path(lz_common.__file__).resolve().parent
    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "config_validate.py")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == lz_common.EXIT_CANNOT
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False
