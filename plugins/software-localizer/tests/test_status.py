"""Tests for status.py, driven as a real subprocess.

status.py is read-only and must never crash, however incomplete the
workspace is -- these tests drive it through every stage of the pipeline's
staging order (see references/state.md) and confirm it never writes
anything.
"""

import json
import subprocess
import sys
from pathlib import Path

import lz_common

STATUS = Path(lz_common.__file__).resolve().parent / "status.py"


def _run(root):
    proc = subprocess.run(
        [sys.executable, str(STATUS), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got: {proc.stdout!r}"
    return proc.returncode, json.loads(lines[0])


def _write_ledger(root, locales: dict) -> None:
    """Write `R/ledger/<locale>.json` for each of `locales` (`{locale: {id:
    entry}}`), the per-locale storage layout: `{"schema": 1, "locale": L,
    "entries": {id: entry}}`. There is no `R/ledger.json`."""
    for locale, entries in locales.items():
        lz_common.atomic_write_json(root / "ledger" / f"{locale}.json", {
            "schema": 1,
            "locale": locale,
            "entries": entries,
        })


def _valid_cfg(project_root) -> dict:
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


def test_status_on_empty_root_recommends_scaffold(work_root):
    code, payload = _run(work_root)
    assert code == 0
    assert payload["ok"] is True
    assert payload["config_ready"] is False
    assert payload["messages_collected"] is False
    assert payload["ledger_present"] is False
    assert payload["counts"] == {}
    assert "scaffold.py" in payload["next_command"]


def test_status_never_writes_anything(work_root):
    lz_common.atomic_write_json(work_root / "localize.json", {"schema": 1})
    before = sorted(p.relative_to(work_root).as_posix() for p in work_root.rglob("*"))
    _run(work_root)
    after = sorted(p.relative_to(work_root).as_posix() for p in work_root.rglob("*"))
    assert before == after


def test_status_with_choose_sentinels_recommends_filling_in_config(work_root):
    cfg = {
        "schema": 1,
        "project_root": "CHOOSE_PROJECT_ROOT",
        "source_locale": "CHOOSE_SOURCE_LOCALE",
        "target_locales": "CHOOSE_TARGET_LOCALES",
        "adapter": {"argv": "CHOOSE_ADAPTER_ARGV", "options": {}},
        "style": {},
    }
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    code, payload = _run(work_root)
    assert code == 0
    assert payload["config_ready"] is False
    assert "config_validate.py" in payload["next_command"]


def test_status_with_valid_config_but_no_adapter_check_recommends_adapter_check(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (work_root / "adapter").mkdir()  # adapter.code_dir's default must exist
    lz_common.atomic_write_json(work_root / "localize.json", _valid_cfg(project))
    code, payload = _run(work_root)
    assert code == 0
    assert payload["config_ready"] is True
    assert payload["adapter_lock"] == {"present": False, "current": False}
    assert "adapter_check.py run" in payload["next_command"]


def test_status_with_no_ledger_directory_recommends_sync(work_root, tmp_path):
    """No `R/ledger/` directory at all: distinct from an empty one, both
    mean "not synced yet"."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    assert not (work_root / "ledger").exists()

    code, payload = _run(work_root)
    assert code == 0
    assert payload["ledger_present"] is False
    assert payload["counts"] == {}
    assert "ledger.py sync" in payload["next_command"]


def test_status_with_empty_ledger_directory_also_recommends_sync(work_root, tmp_path):
    """An empty `R/ledger/` directory (no locale files written yet) is the
    same "not synced yet" state as a missing directory."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})
    (work_root / "ledger").mkdir()

    code, payload = _run(work_root)
    assert code == 0
    assert payload["ledger_present"] is False
    assert "ledger.py sync" in payload["next_command"]


def test_status_reports_adapter_lock_present_but_stale(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    lz_common.atomic_write_json(work_root / "adapter.lock.json", {"files": {"stale": "x"}, "options_sha256": "y"})

    code, payload = _run(work_root)
    assert code == 0
    assert payload["adapter_lock"]["present"] is True
    assert payload["adapter_lock"]["current"] is False
    assert "adapter_check.py accept" in payload["next_command"]


def test_status_reports_adapter_lock_stale_when_only_argv_changed(work_root, tmp_path):
    """`_adapter_lock_status` must compare all three fields
    `lz_common.require_accepted_adapter` does (files, argv, options_sha256),
    not just files/options_sha256: a lock whose `files` and `options_sha256`
    still match the current digest, but whose `argv` does not, must be
    reported stale."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})

    digest = lz_common.adapter_digest(work_root, cfg)
    stale_lock = dict(digest)
    stale_lock["argv"] = digest["argv"] + ["--extra-flag"]  # only argv moved
    lz_common.atomic_write_json(work_root / "adapter.lock.json", stale_lock)

    code, payload = _run(work_root)
    assert code == 0
    assert payload["adapter_lock"]["present"] is True
    assert payload["adapter_lock"]["current"] is False
    assert "adapter_check.py accept" in payload["next_command"]


def test_status_reports_ledger_counts_per_locale_and_state(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    _write_ledger(work_root, {
        "de": {
            "a": {"state": "pending"},
            "b": {"state": "translated"},
            "c": {"state": "translated"},
        },
        "ru": {
            "a": {"state": "existing"},
        },
    })

    code, payload = _run(work_root)
    assert code == 0
    assert payload["adapter_lock"] == {"present": True, "current": True}
    assert payload["messages_collected"] is True
    assert payload["ledger_present"] is True
    assert payload["counts"]["de"] == {"pending": 1, "translated": 2}
    assert payload["counts"]["ru"] == {"existing": 1}
    # "de" has a pending message, so translate is still the recommended step.
    assert "translate" in payload["next_command"]
    assert "--locale de" in payload["next_command"]


def test_status_recommends_review_before_translate(work_root, tmp_path):
    """Item 4: a candidate that already passed checks and only needs a
    verdict outranks a sibling id that still needs a fresh translate round
    -- `next_command` must point at `review`, not `translate`, even though
    a `ru` id is `pending` with no candidate at all."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    _write_ledger(work_root, {
        "de": {"a": {
            "state": "pending",
            "candidate": {"checks": "pass", "value_sha256": "v1", "verdict": None},
        }},
        "ru": {"b": {"state": "pending", "candidate": None}},
    })

    code, payload = _run(work_root)
    assert code == 0
    assert "packets.py build" in payload["next_command"]
    assert "--kind review" in payload["next_command"]
    assert "--locale de" in payload["next_command"]


def test_status_recommends_export_before_translate(work_root, tmp_path):
    """A fully-passing, not-yet-exported candidate outranks a sibling id
    that still needs translation -- `next_command` must point at
    `export_values.py`, not `translate`."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    _write_ledger(work_root, {
        "de": {"a": {
            "state": "pending",
            "candidate": {
                "checks": "pass", "value_sha256": "v1",
                "verdict": {"verdict": "pass", "value_sha256": "v1"},
            },
        }},
        "ru": {"b": {"state": "pending", "candidate": None}},
    })

    code, payload = _run(work_root)
    assert code == 0
    assert "export_values.py" in payload["next_command"]
    assert "--locale de" in payload["next_command"]
    assert "translate" not in payload["next_command"]


def test_status_recommends_export_when_nothing_is_pending_or_stale(work_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    _write_ledger(work_root, {
        "de": {"a": {"state": "human_locked"}},
        "ru": {"a": {"state": "escalated"}},
    })

    code, payload = _run(work_root)
    assert code == 0
    assert "export_values.py" in payload["next_command"]


def test_status_never_crashes_on_a_malformed_ledger_entry(work_root, tmp_path):
    """`ledger.exportable` (now called for the export decision) indexes an
    entry with `entry.get(...)`, unguarded -- a non-dict entry raises. That
    locale must fall back to "unknown" and be left out of every bucket,
    never crash the whole report; a sibling locale with a normal entry is
    still read correctly."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = _valid_cfg(project)
    cfg["adapter"] = {"argv": ["python3", "adapter/adapter.py"], "options": {}}
    lz_common.atomic_write_json(work_root / "localize.json", cfg)

    adapter_dir = work_root / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text("v1", encoding="utf-8")
    (work_root / "runs").mkdir()
    lz_common.atomic_write_json(work_root / "runs" / "_adapter_check.json", {"ok": True})
    digest = lz_common.adapter_digest(work_root, cfg)
    lz_common.atomic_write_json(work_root / "adapter.lock.json", digest)
    lz_common.atomic_write_json(work_root / "messages.json", {"schema": 1, "files": [], "messages": []})

    _write_ledger(work_root, {
        "de": {"a": "not-a-dict-entry"},  # malformed: exportable() raises on this
        "ru": {"b": {"state": "pending", "candidate": None}},
    })

    code, payload = _run(work_root)
    assert code == 0
    assert payload["ok"] is True
    assert "translate" in payload["next_command"]
    assert "--locale ru" in payload["next_command"]
