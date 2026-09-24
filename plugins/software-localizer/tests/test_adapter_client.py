"""Tests for `adapter_client.py` (plan section 5): the only module that
invokes a project adapter.

Every test writes its own tiny throwaway adapter script under `work_root`
(never the shared fixture adapter in `tests/fixtures/`), so each test's
adapter behavior is visible right next to the assertion that checks it.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

import adapter_client
from adapter_client import AdapterError


def _write_adapter(root: Path, body: str) -> None:
    """Write `root/adapter.py`: a stdlib-only script with `emit`/`fail`
    helpers already in scope, plus argparse wired for collect/export/parse.
    `body` supplies each `cmd_<command>(args)` function."""
    header = textwrap.dedent(
        """\
        import argparse, json, sys

        def emit(obj):
            sys.stdout.write(json.dumps(obj))
            sys.stdout.write("\\n")
            sys.stdout.flush()

        def fail(message, code, **fields):
            payload = {"ok": False, "error": message}
            payload.update(fields)
            emit(payload)
            sys.exit(code)

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        for name in ("collect", "export", "parse"):
            p = sub.add_parser(name)
            p.add_argument("--options")
            p.add_argument("--source-locale")
            p.add_argument("--target-locales")
            p.add_argument("--out")
            p.add_argument("--locale")
            p.add_argument("--values")
            p.add_argument("--in", dest="in_path")
        args = parser.parse_args()
        """
    )
    footer = textwrap.dedent(
        """\
        if args.command == "collect":
            cmd_collect(args)
        elif args.command == "export":
            cmd_export(args)
        elif args.command == "parse":
            cmd_parse(args)
        """
    )
    (root / "adapter.py").write_text(header + "\n" + textwrap.dedent(body) + "\n" + footer, encoding="utf-8")


def _cfg(argv=("python3", "adapter.py"), timeout=5, options=None):
    return {
        "adapter": {"argv": list(argv), "options": options or {}},
        "adapter_timeout_s": timeout,
        "source_locale": "en",
        "target_locales": ["de"],
    }


@pytest.fixture
def project_dir(work_root):
    p = work_root / "project"
    p.mkdir()
    return p


# --- run() -------------------------------------------------------------

def test_run_returns_parsed_reply(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": True, "marker": "hi"})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert reply == {"ok": True, "marker": "hi"}


def test_run_resolves_relative_argv_against_root_not_cwd(work_root, project_dir):
    # adapter.py sits under work_root (R), not under project_dir (cwd); a
    # correct relative-argv resolution finds it anyway.
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert reply["ok"] is True


def test_run_uses_project_dir_as_cwd(work_root, project_dir):
    (project_dir / "here.txt").write_text("project-marker", encoding="utf-8")
    _write_adapter(work_root, """
        from pathlib import Path
        def cmd_collect(args):
            emit({"ok": True, "content": Path("here.txt").read_text()})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert reply["content"] == "project-marker"


def test_run_closes_stdin(work_root, project_dir):
    _write_adapter(work_root, """
        import sys
        def cmd_collect(args):
            data = sys.stdin.read()
            emit({"ok": True, "stdin_len": len(data)})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert reply["stdin_len"] == 0


def test_run_passes_extra_args(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": True, "out": args.out, "source_locale": args.source_locale})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(
        str(work_root), _cfg(), str(project_dir), "collect",
        ["--out", "somewhere.json", "--source-locale", "en"],
    )
    assert reply["out"] == "somewhere.json"
    assert reply["source_locale"] == "en"


def test_run_raises_on_nonzero_exit(work_root, project_dir):
    _write_adapter(work_root, """
        import sys
        def cmd_collect(args):
            print("boom", file=sys.stderr)
            sys.exit(1)
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert "collect" in str(exc_info.value)


def test_run_raises_on_timeout(work_root, project_dir):
    _write_adapter(work_root, """
        import time
        def cmd_collect(args):
            time.sleep(2)
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [], timeout=0.2)
    assert "collect" in str(exc_info.value)
    assert "timed out" in str(exc_info.value)


def test_run_uses_cfg_timeout_when_none_given(work_root, project_dir):
    _write_adapter(work_root, """
        import time
        def cmd_collect(args):
            time.sleep(2)
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.run(str(work_root), _cfg(timeout=0.2), str(project_dir), "collect", [])
    assert "timed out" in str(exc_info.value)


def test_run_raises_on_invalid_json(work_root, project_dir):
    _write_adapter(work_root, """
        import sys
        def cmd_collect(args):
            sys.stdout.write("not json\\n")
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert "collect" in str(exc_info.value)


def test_run_raises_on_two_json_lines(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": True})
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert "one JSON line" in str(exc_info.value)


def test_run_tolerates_trailing_blank_lines(work_root, project_dir):
    _write_adapter(work_root, """
        import sys
        def cmd_collect(args):
            sys.stdout.write(json.dumps({"ok": True}))
            sys.stdout.write("\\n\\n")
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])
    assert reply == {"ok": True}


def test_run_raises_when_reply_is_not_an_object(work_root, project_dir):
    _write_adapter(work_root, """
        import sys
        def cmd_collect(args):
            sys.stdout.write("[1, 2, 3]\\n")
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError):
        adapter_client.run(str(work_root), _cfg(), str(project_dir), "collect", [])


def test_resolve_argv_resolves_relative_file_under_root(work_root):
    (work_root / "adapter.py").write_text("", encoding="utf-8")
    resolved = adapter_client._resolve_argv(str(work_root), ["python3", "adapter.py"])
    assert resolved[0] == "python3"
    assert resolved[1] == str(work_root / "adapter.py")


def test_resolve_argv_leaves_bare_command_that_is_not_a_file_under_root(work_root):
    resolved = adapter_client._resolve_argv(str(work_root), ["python3", "adapter.py"])
    assert resolved == ["python3", "adapter.py"]


def test_resolve_argv_leaves_absolute_path_untouched(work_root):
    resolved = adapter_client._resolve_argv(str(work_root), ["/usr/bin/env", "adapter.py"])
    assert resolved[0] == "/usr/bin/env"


# --- collect() -----------------------------------------------------------

VALID_MESSAGES = {
    "schema": 1,
    "files": ["locales/en.json"],
    "messages": [
        {
            "id": "m1",
            "source": "Hi",
            "context": {"file": "locales/en.json", "key": "m1", "max_length": None, "comment": None},
            "targets": {"de": None},
        }
    ],
}


def test_collect_writes_and_validates_messages(work_root, project_dir):
    _write_adapter(work_root, f"""
        from pathlib import Path
        def cmd_collect(args):
            Path(args.out).write_text({json.dumps(json.dumps(VALID_MESSAGES))})
            emit({{"ok": True}})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    out_path = work_root / "messages.json"
    result = adapter_client.collect(str(work_root), _cfg(), str(project_dir), str(out_path))
    assert result == VALID_MESSAGES
    assert json.loads(out_path.read_text()) == VALID_MESSAGES


def test_collect_fails_when_out_file_missing(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.collect(str(work_root), _cfg(), str(project_dir), str(work_root / "messages.json"))
    assert "did not write" in str(exc_info.value)


def test_collect_fails_when_adapter_reports_not_ok(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            emit({"ok": False, "error": "boom"})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.collect(str(work_root), _cfg(), str(project_dir), str(work_root / "messages.json"))
    assert "boom" in str(exc_info.value)


def test_collect_fails_when_out_file_is_invalid_json(work_root, project_dir):
    _write_adapter(work_root, """
        from pathlib import Path
        def cmd_collect(args):
            Path(args.out).write_text("not json")
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.collect(str(work_root), _cfg(), str(project_dir), str(work_root / "messages.json"))
    assert "invalid JSON" in str(exc_info.value)


def test_collect_exits_cannot_when_messages_shape_invalid(work_root, project_dir):
    _write_adapter(work_root, """
        from pathlib import Path
        def cmd_collect(args):
            Path(args.out).write_text(json.dumps({"not": "valid"}))
            emit({"ok": True})
        def cmd_export(args):
            pass
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(SystemExit) as exc_info:
        adapter_client.collect(str(work_root), _cfg(), str(project_dir), str(work_root / "messages.json"))
    assert exc_info.value.code == 2


# --- export() --------------------------------------------------------------

def test_export_writes_values_and_returns_reply(work_root, project_dir):
    _write_adapter(work_root, """
        from pathlib import Path
        def cmd_collect(args):
            pass
        def cmd_export(args):
            payload = json.loads(Path(args.values).read_text())
            emit({"ok": True, "locale": args.locale, "echo": payload["values"]})
        def cmd_parse(args):
            pass
    """)
    reply = adapter_client.export(str(work_root), _cfg(), str(project_dir), "de", {"m1": "Hallo"})
    assert reply["locale"] == "de"
    assert reply["echo"] == {"m1": "Hallo"}


def test_export_fails_when_not_ok(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            emit({"ok": False, "error": "cannot write"})
        def cmd_parse(args):
            pass
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.export(str(work_root), _cfg(), str(project_dir), "de", {"m1": "Hallo"})
    assert "cannot write" in str(exc_info.value)


# --- parse() -----------------------------------------------------------------

def test_parse_returns_results_by_key(work_root, project_dir):
    _write_adapter(work_root, """
        from pathlib import Path
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            payload = json.loads(Path(args.in_path).read_text())
            results = {}
            for item in payload["items"]:
                results[item["key"]] = {"ok": True, "tokens": []}
            emit({"ok": True, "results": results})
    """)
    items = [{"key": "k1", "text": "Hi"}, {"key": "k2", "text": "Bye"}]
    results = adapter_client.parse(str(work_root), _cfg(), str(project_dir), items)
    assert set(results.keys()) == {"k1", "k2"}
    assert results["k1"] == {"ok": True, "tokens": []}


def test_parse_fails_when_results_missing(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            emit({"ok": True})
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.parse(str(work_root), _cfg(), str(project_dir), [{"key": "k1", "text": "Hi"}])
    assert "results" in str(exc_info.value)


def test_parse_fails_when_result_missing_ok_field(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            emit({"ok": True, "results": {"k1": {"tokens": []}}})
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.parse(str(work_root), _cfg(), str(project_dir), [{"key": "k1", "text": "Hi"}])
    assert "k1" in str(exc_info.value)


def test_parse_fails_when_ok_true_missing_tokens(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            emit({"ok": True, "results": {"k1": {"ok": True}}})
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.parse(str(work_root), _cfg(), str(project_dir), [{"key": "k1", "text": "Hi"}])
    assert "tokens" in str(exc_info.value)


def test_parse_fails_when_ok_false_missing_error(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            emit({"ok": True, "results": {"k1": {"ok": False}}})
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.parse(str(work_root), _cfg(), str(project_dir), [{"key": "k1", "text": "Hi"}])
    assert "error" in str(exc_info.value)


def test_parse_fails_when_a_requested_key_is_missing_from_results(work_root, project_dir):
    _write_adapter(work_root, """
        def cmd_collect(args):
            pass
        def cmd_export(args):
            pass
        def cmd_parse(args):
            emit({"ok": True, "results": {"k1": {"ok": True, "tokens": []}}})
    """)
    with pytest.raises(AdapterError) as exc_info:
        adapter_client.parse(
            str(work_root), _cfg(), str(project_dir),
            [{"key": "k1", "text": "Hi"}, {"key": "k2", "text": "Bye"}],
        )
    assert "k2" in str(exc_info.value)
