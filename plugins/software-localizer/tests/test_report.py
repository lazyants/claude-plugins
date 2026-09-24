"""Tests for `report.py` (plan section 11): the native-speaker report --
exported and pending candidates, escalated ids with problems, audit findings
with proposals, canon candidates raised by reviews, and existing/human-locked
notes. Ledger and messages are hand-built (report.py only reads durable
state, never re-derives it), matching `test_ledger.py`'s own convention.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"
sys.path.insert(0, str(SCRIPTS_DIR))

import ledger as ledger_mod  # noqa: E402
import lz_common  # noqa: E402
import report  # noqa: E402


# --- helpers -----------------------------------------------------------


def make_cfg(target_locales=("de",)):
    return {
        "schema": 1, "project_root": str(FIXTURES_DIR / "toy_project"), "source_locale": "en",
        "target_locales": list(target_locales),
        "adapter": {"argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")], "options": {}},
        "style": {loc: {"formality": "Sie", "notes": ""} for loc in target_locales},
        "allow_identical": [], "batch_size": 40, "max_rounds": 3, "adapter_timeout_s": 30,
    }


def make_message(msg_id, source, targets=None, context=None):
    return {
        "id": msg_id, "source": source,
        "context": context or {"file": "f.json", "key": msg_id, "max_length": None, "comment": None},
        "targets": dict(targets or {}),
    }


def make_messages(msgs):
    return {"schema": 1, "files": [], "messages": msgs}


def make_entry(state="pending", candidate=None, notes=None):
    entry = {
        "state": state, "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "project_value_sha256": None, "last_exported_sha256": None,
        "candidate": candidate, "rounds": 0, "notes": list(notes or []),
    }
    return entry


def make_candidate(value="Hallo", checks="pass", problems=None, verdict=None):
    return {
        "value": value, "value_sha256": lz_common.value_sha256(value), "origin": "translate",
        "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "audited_target_sha256": None, "checks": checks, "problems": list(problems or []),
        "verdict": verdict, "accepted_by": None,
    }


def setup_workspace(root, cfg, messages, ledger_locales):
    lz_common.atomic_write_json(root / "localize.json", cfg)
    lz_common.load_config(root)  # proves cfg is well-formed
    lz_common.atomic_write_json(root / "messages.json", messages)
    ledger_data = {"schema": 1, "locales": ledger_locales}
    ledger_mod.save(root, ledger_data)


def run_cli(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "report.py"), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


# --- content -------------------------------------------------------------


def test_report_summary_counts(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello"), make_message("b", "World")])
    ledger_locales = {"de": {"a": make_entry("pending"), "b": make_entry("translated",
                       candidate=make_candidate("Welt"))}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    text = report.build_report(work_root, "de")
    assert "- pending: 1" in text
    assert "- translated: 1" in text


def test_report_exported_section(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo")
    ledger_locales = {"de": {"a": make_entry("translated", candidate=candidate)}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    text = report.build_report(work_root, "de")
    assert "## Exported" in text
    assert "`a`" in text
    assert "value: Hallo" in text


def test_report_ready_vs_still_needs_translation(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello"), make_message("b", "World")])
    ready_candidate = make_candidate("Hallo")
    ready_candidate["verdict"] = {"value_sha256": ready_candidate["value_sha256"], "verdict": "pass",
                                   "issues": [], "run": "x"}
    problems = [{"check": "arguments", "detail": "missing {x}"}]
    failing_candidate = make_candidate("Welt", checks="fail", problems=problems)
    ledger_locales = {
        "de": {
            "a": make_entry("pending", candidate=ready_candidate),
            "b": make_entry("stale", candidate=failing_candidate),
        }
    }
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    text = report.build_report(work_root, "de")
    assert "## Ready to export" in text
    ready_section = text.split("## Ready to export")[1].split("## Still needs translation")[0]
    assert "`a`" in ready_section
    needs_section = text.split("## Still needs translation")[1].split("## Escalated")[0]
    assert "`b`" in needs_section
    assert "missing {x}" in needs_section


def test_report_escalated_with_kept_proposal(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    problems = [{"check": "arguments", "detail": "still broken"}]
    candidate = make_candidate("Hallo!", checks="fail", problems=problems)
    entry = make_entry("escalated", candidate=candidate)
    entry["review_proposal"] = {"value": "Hallo", "issues": [{"kind": "style", "text": "off"}], "run": "b1"}
    setup_workspace(work_root, cfg, msgs, {"de": {"a": entry}})

    text = report.build_report(work_root, "de")
    escalated_section = text.split("## Escalated")[1].split("## Audit findings")[0]
    assert "`a`" in escalated_section
    assert "still broken" in escalated_section
    assert "kept proposal (never installed): Hallo" in escalated_section


def test_report_audit_findings_with_current_and_proposed(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    entry = make_entry("existing")
    entry["audit_proposal"] = {
        "value": "Hallo!", "checks": "pass",
        "issues": [{"kind": "meaning", "text": "too terse"}],
    }
    setup_workspace(work_root, cfg, msgs, {"de": {"a": entry}})

    text = report.build_report(work_root, "de")
    audit_section = text.split("## Audit findings")[1].split("## Existing")[0]
    assert "current: Hallo" in audit_section
    assert "proposed: Hallo! (checks pass)" in audit_section
    assert "too terse" in audit_section
    # An id with an audit proposal is not also listed under existing/human-locked.
    existing_section = text.split("## Existing")[1].split("## Canon candidates")[0]
    assert "`a`" not in existing_section


def test_report_existing_notes(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    entry = make_entry("existing", notes=[{"at": "2026-01-01T00:00:00Z", "note": "source changed while existing"}])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": entry}})

    text = report.build_report(work_root, "de")
    existing_section = text.split("## Existing")[1].split("## Canon candidates")[0]
    assert "`a`" in existing_section
    assert "source changed while existing" in existing_section


def test_report_canon_candidates_from_review_side_files(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("existing")}})
    side_file_dir = work_root / "runs" / "de" / "batch1"
    lz_common.atomic_write_json(side_file_dir / "new_canon_candidates.json", {
        "candidates": [{"kind": "term", "source": "Cart", "note": "seen twice"}],
    })

    text = report.build_report(work_root, "de")
    canon_section = text.split("## Canon candidates raised by reviews")[1]
    assert "Cart" in canon_section
    assert "seen twice" in canon_section


def test_report_unknown_locale_refused(work_root):
    cfg = make_cfg()
    setup_workspace(work_root, cfg, make_messages([]), {})
    with pytest.raises(SystemExit) as exc:
        report.build_report(work_root, "fr")
    assert exc.value.code == lz_common.EXIT_CANNOT


def test_do_report_writes_file(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}})

    result = report.do_report(work_root, "de")
    assert result["ok"] is True
    out_path = Path(result["report"])
    assert out_path == work_root / "reports" / "de.md"
    assert out_path.is_file()
    assert "# Localization report" in out_path.read_text(encoding="utf-8")


def test_report_via_cli(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}})

    code, payload = run_cli(["--root", str(work_root), "--locale", "de"])
    assert code == 0, payload
    assert Path(payload["report"]).is_file()
