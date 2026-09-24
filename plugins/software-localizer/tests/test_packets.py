"""Tests for `packets.py` (plan section 10): build selection and batching,
accept's per-kind rules (translate/review/audit/canon), and `accept-audit`.

Most tests call `packets.do_build`/`packets.do_accept`/`packets.do_accept_audit`
directly against a hand-built `messages.json`/`ledger.json` (packets.py never
re-derives its own state; hand-writing these here matches how `test_ledger.py`
and `test_canon.py` set up their fixtures). `adapter_client.parse` is
monkeypatched to the fixture toy adapter's own tokenizer, reused directly so
argument/structure token shapes stay realistic without a live subprocess.
`localize.json`'s `project_root`/`adapter.argv` still point at the real
fixture project/adapter so `lz_common.load_config` and `adapter.lock.json`
validate for real -- only the `parse` call itself is faked.
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
TEMPLATES_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "assets" / "templates"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(FIXTURES_DIR))

import adapter_client  # noqa: E402
import canon as canon_mod  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import lz_common  # noqa: E402
import packets  # noqa: E402
import toy_adapter  # noqa: E402

EMPTY_CANON_LOCK = {"schema": 1, "entries": []}


# --- helpers -----------------------------------------------------------


def fake_parse(root, cfg, project_dir, items):
    results = {}
    for item in items:
        tokens, error = toy_adapter.parse_text(item["text"])
        results[item["key"]] = {"ok": True, "tokens": tokens} if tokens is not None else {"ok": False, "error": error}
    return results


@pytest.fixture(autouse=True)
def _fake_adapter_parse(monkeypatch):
    monkeypatch.setattr(adapter_client, "parse", fake_parse)


def make_cfg(target_locales=("de",), style=None, batch_size=40, max_rounds=3, options=None):
    if style is None:
        style = {loc: {"formality": "Sie", "notes": ""} for loc in target_locales}
    return {
        "schema": 1, "project_root": str(FIXTURES_DIR / "toy_project"), "source_locale": "en",
        "target_locales": list(target_locales),
        "adapter": {"argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")], "options": options or {}},
        "style": style, "allow_identical": [], "batch_size": batch_size, "max_rounds": max_rounds,
        "adapter_timeout_s": 30,
    }


def make_message(msg_id, source, context=None, targets=None):
    return {
        "id": msg_id, "source": source,
        "context": context or {"file": "f.json", "key": msg_id, "max_length": None, "comment": None},
        "targets": dict(targets or {}),
    }


def make_plural_message(msg_id, source_forms, target_labels_by_locale, general_index,
                         count_arguments=("count",), targets=None):
    return {
        "id": msg_id, "source": {"forms": list(source_forms)},
        "plural": {
            "source_labels": [{"label": str(i), "exact": False} for i in range(len(source_forms))],
            "target_labels": {loc: list(labels) for loc, labels in target_labels_by_locale.items()},
            "count_arguments": list(count_arguments),
            "general_index": general_index,
        },
        "context": {"file": "f.json", "key": msg_id, "max_length": None, "comment": None},
        "targets": dict(targets or {}),
    }


def make_messages(msgs):
    return {"schema": 1, "files": [], "messages": msgs}


def make_entry(state="pending", candidate=None, rounds=0, project_value_sha=None):
    return {
        "state": state, "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "project_value_sha256": project_value_sha, "last_exported_sha256": None,
        "candidate": candidate, "rounds": rounds, "notes": [],
    }


def make_candidate(value="Hallo", checks="pass", problems=None, verdict=None, origin="translate",
                    accepted_by=None, audited_target_sha=None):
    return {
        "value": value, "value_sha256": lz_common.value_sha256(value), "origin": origin,
        "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "audited_target_sha256": audited_target_sha, "checks": checks, "problems": list(problems or []),
        "verdict": verdict, "accepted_by": accepted_by,
    }


def setup_workspace(root, cfg, messages, ledger_locales=None, canon_lock=None):
    lz_common.atomic_write_json(root / "localize.json", cfg)
    # load_config re-validates from disk, so this also proves the cfg is well-formed.
    loaded_cfg = lz_common.load_config(root)
    lock = lz_common.adapter_digest(root, loaded_cfg)
    lz_common.atomic_write_json(root / "adapter.lock.json", lock)
    lz_common.atomic_write_json(root / "messages.json", messages)
    ledger_data = {"schema": 1, "locales": ledger_locales or {}}
    ledger_mod.save(root, ledger_data)
    lz_common.atomic_write_json(root / "canon.lock.json", canon_lock or EMPTY_CANON_LOCK)
    return loaded_cfg


def run_cli(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "packets.py"), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


# --- build: selection ----------------------------------------------------


def test_build_translate_selects_pending_and_stale_excludes_ready(work_root):
    cfg = make_cfg()
    msgs = make_messages([
        make_message("a", "Hello"), make_message("b", "World"), make_message("c", "Ready"),
    ])
    ready_verdict = {"value_sha256": lz_common.value_sha256("Bereit"), "verdict": "pass", "issues": [], "run": "x"}
    ledger_locales = {
        "de": {
            "a": make_entry("pending"),
            "b": make_entry("stale"),
            "c": make_entry("pending", candidate=make_candidate("Bereit", verdict=ready_verdict)),
        }
    }
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)
    selected_ids = {i["id"] for b in result["batches"] for i in _load_batch_items(b)}
    assert selected_ids == {"a", "b"}


def _load_batch_items(batch_info):
    packet = lz_common.read_json(Path(batch_info["dir"]) / "packet.json", "packet.json")
    return packet["items"]


def test_build_translate_includes_fix_round_previous_and_problems(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    problems = [{"check": "arguments", "detail": "missing {x}"}]
    ledger_locales = {"de": {"a": make_entry("pending", candidate=make_candidate("Hi", checks="fail", problems=problems))}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)
    items = _load_batch_items(result["batches"][0])
    assert items[0]["previous"] == "Hi"
    assert items[0]["problems"] == problems


def test_build_review_selects_checks_pass_no_verdict(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello"), make_message("b", "World")])
    ledger_locales = {
        "de": {
            "a": make_entry("pending", candidate=make_candidate("Hallo", checks="pass")),
            "b": make_entry("pending", candidate=make_candidate("Welt", checks="fail")),
        }
    }
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "review", "de", TEMPLATES_DIR)
    selected_ids = {i["id"] for b in result["batches"] for i in _load_batch_items(b)}
    assert selected_ids == {"a"}


def test_build_review_excludes_when_verdict_matches_hash(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    candidate["verdict"] = {"value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "run": "x"}
    ledger_locales = {"de": {"a": make_entry("pending", candidate=candidate)}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "review", "de", TEMPLATES_DIR)
    assert result["batches"] == []


def test_build_audit_selects_existing_translated_human_locked_skips_missing_target(work_root):
    cfg = make_cfg()
    msgs = make_messages([
        make_message("a", "Hello", targets={"de": "Hallo"}),
        make_message("b", "World", targets={"de": "Welt"}),
        make_message("c", "Foo", targets={"de": "Bar"}),
        make_message("d", "NoTarget", targets={"de": None}),
    ])
    ledger_locales = {
        "de": {
            "a": make_entry("existing"), "b": make_entry("translated"), "c": make_entry("human_locked"),
            "d": make_entry("existing"),
        }
    }
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)
    selected_ids = {i["id"] for b in result["batches"] for i in _load_batch_items(b)}
    assert selected_ids == {"a", "b", "c"}
    assert result["skipped"] == [{"id": "d", "reason": "no current target value for this locale"}]


def test_build_canon_no_locale_scans_all_messages(work_root):
    cfg = make_cfg(target_locales=("de", "ru"))
    msgs = make_messages([
        make_message("a", "Hello", targets={"de": "Hallo", "ru": None}),
        make_message("b", "World", targets={"de": None, "ru": None}),
    ])
    setup_workspace(work_root, cfg, msgs, {})

    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    assert result["locale"] is None
    packets_by_batch = [
        lz_common.read_json(Path(b["dir"]) / "packet.json", "packet.json") for b in result["batches"]
    ]
    for packet in packets_by_batch:
        assert packet["locales"] == ["de", "ru"]
    by_id = {i["id"]: i for packet in packets_by_batch for i in packet["items"]}
    assert by_id["a"]["targets"] == {"de": "Hallo"}
    assert "targets" not in by_id["b"]
    assert (work_root / "runs" / "_canon").is_dir()


# --- build: batching, locale/template validation -------------------------


def test_build_batches_by_prefix_and_batch_size(work_root):
    cfg = make_cfg(batch_size=2)
    ids = ["app.a", "app.b", "app.c", "nav.a"]
    msgs = make_messages([make_message(i, "x") for i in ids])
    ledger_locales = {"de": {i: make_entry("pending") for i in ids}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)
    # "app" has 3 ids with batch_size=2 -> two same-prefix batches (2 + 1);
    # "nav" has 1 id -> one batch. A batch never mixes the two prefixes.
    sizes = sorted(b["items"] for b in result["batches"])
    assert sizes == [1, 1, 2]
    names = {b["batch"] for b in result["batches"]}
    assert names == {"app", "app-2", "nav"}


def test_build_requires_locale_for_non_canon(work_root):
    cfg = make_cfg()
    setup_workspace(work_root, cfg, make_messages([]), {})
    with pytest.raises(SystemExit) as exc:
        packets.do_build(work_root, "translate", None, TEMPLATES_DIR)
    assert exc.value.code == lz_common.EXIT_CANNOT


def test_build_rejects_unknown_locale(work_root):
    cfg = make_cfg()
    setup_workspace(work_root, cfg, make_messages([]), {})
    with pytest.raises(SystemExit) as exc:
        packets.do_build(work_root, "translate", "fr", TEMPLATES_DIR)
    assert exc.value.code == lz_common.EXIT_CANNOT


def test_build_missing_template_fails_cannot_run(work_root, tmp_path):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}})
    empty_templates = tmp_path / "empty_templates"
    empty_templates.mkdir()
    with pytest.raises(SystemExit) as exc:
        packets.do_build(work_root, "translate", "de", empty_templates)
    assert exc.value.code == lz_common.EXIT_CANNOT


def test_build_templates_dir_override_used(work_root, tmp_path):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}})
    templates_dir = tmp_path / "custom_templates"
    templates_dir.mkdir()
    (templates_dir / "translate_TASK.md").write_text("CUSTOM\n\n{{PACKET_JSON}}\n", encoding="utf-8")

    result = packets.do_build(work_root, "translate", "de", templates_dir)
    prompt = (Path(result["batches"][0]["dir"]) / "prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("CUSTOM")
    assert '"id": "a"' in prompt


def test_build_restricted_canon_audit_from_entry(work_root):
    cfg = make_cfg()
    msgs = make_messages([
        make_message("a", "Cart", targets={"de": "Warenkorb"}),
        make_message("b", "Other", targets={"de": "Andere"}),
    ])
    ledger_locales = {"de": {"a": make_entry("translated"), "b": make_entry("translated")}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)
    request = {
        "entry": "t-cart", "kind": "term", "source": "Cart", "locale": "de",
        "before": "Warenkorb", "after": "Einkaufswagen", "reason": "consistency", "by": "alice",
        "at": "2026-01-01T00:00:00Z", "occurrences": ["a"],
    }
    lz_common.atomic_write_json(work_root / "runs" / "de" / "canon-audit-t-cart.json", request)

    result = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR, entry_id="t-cart")
    assert result["ok"] is True
    packet = lz_common.read_json(Path(result["batches"][0]["dir"]) / "packet.json", "packet.json")
    assert [i["id"] for i in packet["items"]] == ["a"]
    assert packet["canon_entry"]["entry"] == "t-cart"
    assert packet["canon_entry"]["before"] == "Warenkorb"


# --- accept: translate -----------------------------------------------------


def build_one_batch(work_root, cfg, msgs, ledger_locales, kind, locale):
    setup_workspace(work_root, cfg, msgs, ledger_locales)
    result = packets.do_build(work_root, kind, locale, TEMPLATES_DIR)
    assert result["batches"], "expected at least one batch"
    return Path(result["batches"][0]["dir"])


def write_output(run_dir, output):
    path = run_dir / "output.json"
    lz_common.atomic_write_json(path, output)
    return path


def test_accept_translate_success_pass(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"a": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["accepted"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"]["checks"] == "pass"
    assert entry["candidate"]["value"] == "Hallo"
    assert entry["rounds"] == 1


def test_accept_translate_missing_id_reported_failed(work_root):
    cfg = make_cfg()
    # Both ids share the "m" prefix so build puts them in the same batch.
    msgs = make_messages([make_message("m.a", "Hello"), make_message("m.b", "World")])
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"m.a": make_entry("pending"), "m.b": make_entry("pending")}}, "translate", "de",
    )
    output_path = write_output(run_dir, {"translations": {"m.a": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["accepted"] == ["m.a"]
    failed_ids = {f["id"] for f in result["failed"]}
    assert "m.b" in failed_ids
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["m.b"]["candidate"] is None


def test_accept_translate_extra_id_ignored_and_listed(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"a": "Hallo", "ghost": "Nope"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["extra"] == ["ghost"]
    assert result["accepted"] == ["a"]


def test_accept_translate_checks_fail_increments_rounds(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello {name}")])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}}, "translate", "de")
    # dropped the {name} argument -> the 'arguments' check must fail
    output_path = write_output(run_dir, {"translations": {"a": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["accepted"] == []
    assert any(f["id"] == "a" for f in result["failed"])
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"]["checks"] == "fail"
    assert entry["rounds"] == 1
    assert entry["state"] == "pending"  # not yet at max_rounds


def test_accept_translate_escalates_at_max_rounds(work_root):
    cfg = make_cfg(max_rounds=1)
    msgs = make_messages([make_message("a", "Hello {name}")])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"a": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["escalated"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["state"] == "escalated"


# --- accept: review -------------------------------------------------------


def test_accept_review_pass_records_verdict(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["passed"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    verdict = ledger_data["locales"]["de"]["a"]["candidate"]["verdict"]
    assert verdict["verdict"] == "pass"


def test_accept_review_hash_mismatch_treated_as_missing(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": "not-the-real-hash", "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    assert result["passed"] == []
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["candidate"]["verdict"] is None


def test_accept_review_fail_with_proposed_becomes_new_candidate_needing_verdict(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too informal"}], "proposed": "Hallo",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["failed"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    new_candidate = entry["candidate"]
    assert new_candidate["value"] == "Hallo"
    assert new_candidate["verdict"] is None
    assert new_candidate["checks"] == "pass"
    assert entry["rounds"] == 1


def test_accept_review_fail_without_proposed_keeps_old_candidate_marked_failed(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!!!", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too many exclamation marks"}], "proposed": None,
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["failed"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"]["value"] == "Hallo!!!"
    assert entry["candidate"]["verdict"]["verdict"] == "fail"


def test_accept_review_escalates_and_keeps_proposal_uninstalled(work_root):
    cfg = make_cfg(max_rounds=1)
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!", checks="pass")
    entry = make_entry("pending", candidate=candidate, rounds=1)
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": entry}}, "review", "de")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "still wrong"}], "proposed": "Hallo",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["escalated"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    e = ledger_data["locales"]["de"]["a"]
    assert e["state"] == "escalated"
    assert e["candidate"]["value"] == "Hallo!"  # never replaced with the proposal
    assert e["review_proposal"]["value"] == "Hallo"


def test_accept_review_new_canon_candidates_written_to_side_file(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": [{"kind": "term", "source": "Cart", "proposed": "Warenkorb", "note": "seen twice"}],
    }}})

    packets.do_accept(work_root, run_dir, output_path)
    side_file = run_dir / "new_canon_candidates.json"
    assert side_file.is_file()
    data = json.loads(side_file.read_text(encoding="utf-8"))
    assert data["candidates"][0]["source"] == "Cart"


# --- accept: audit ---------------------------------------------------------


def test_accept_audit_fail_with_proposed_stores_audit_proposal(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("existing")}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    value_sha = packet["items"][0]["value_sha256"]
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": value_sha, "verdict": "fail",
        "issues": [{"kind": "meaning", "text": "wrong"}], "proposed": "Hallo!",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["audit_proposals"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"] is None  # audit accept never touches the candidate directly
    assert entry["audit_proposal"]["value"] == "Hallo!"
    assert entry["audit_proposal"]["checks"] == "pass"
    assert entry["audit_proposal"]["audited_target_sha256"] == value_sha


def test_accept_audit_pass_does_not_mutate_ledger(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("existing")}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    value_sha = packet["items"][0]["value_sha256"]
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": value_sha, "verdict": "pass", "issues": [], "proposed": None, "new_canon_candidates": [],
    }}})

    packets.do_accept(work_root, run_dir, output_path)
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"] is None
    assert entry.get("audit_proposal") is None


# --- accept-audit -----------------------------------------------------------


def test_accept_audit_promotes_passing_proposal(work_root):
    cfg = make_cfg()
    audit_proposal = {
        "value": "Hallo!", "value_sha256": lz_common.value_sha256("Hallo!"),
        "audited_target_sha256": "abc", "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "checks": "pass", "problems": [], "issues": [], "run": "batch1",
    }
    entry = make_entry("existing")
    entry["audit_proposal"] = audit_proposal
    setup_workspace(work_root, cfg, make_messages([make_message("a", "Hello")]), {"de": {"a": entry}})

    result = packets.do_accept_audit(work_root, "de", ["a"], "alice")
    assert result["accepted"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    updated = ledger_data["locales"]["de"]["a"]
    assert updated["candidate"]["origin"] == "audit"
    assert updated["candidate"]["accepted_by"] == "alice"
    assert updated["candidate"]["verdict"] is None  # still needs its own review verdict
    assert updated["audit_proposal"] is None


def test_accept_audit_rejects_missing_proposal(work_root):
    cfg = make_cfg()
    setup_workspace(work_root, cfg, make_messages([make_message("a", "Hello")]), {"de": {"a": make_entry("existing")}})
    result = packets.do_accept_audit(work_root, "de", ["a"], "alice")
    assert result["accepted"] == []
    assert result["failed"][0]["reason"] == "no stored audit proposal"


def test_accept_audit_rejects_failed_checks_proposal(work_root):
    cfg = make_cfg()
    audit_proposal = {
        "value": "Hallo!", "value_sha256": lz_common.value_sha256("Hallo!"),
        "audited_target_sha256": "abc", "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "checks": "fail", "problems": [{"check": "arguments", "detail": "x"}], "issues": [], "run": "batch1",
    }
    entry = make_entry("existing")
    entry["audit_proposal"] = audit_proposal
    setup_workspace(work_root, cfg, make_messages([make_message("a", "Hello")]), {"de": {"a": entry}})

    result = packets.do_accept_audit(work_root, "de", ["a"], "alice")
    assert result["accepted"] == []
    assert result["failed"][0]["reason"] == "audit proposal failed checks"


# --- accept: canon ---------------------------------------------------------


def test_accept_canon_writes_candidates_json(work_root):
    cfg = make_cfg(target_locales=("de", "ru"))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [
        {"kind": "term", "source": "Cart", "note": "", "occurrences": ["a"],
         "translations": {"de": {"proposed": "Warenkorb", "current": []}}},
    ]})

    accept_result = packets.do_accept(work_root, run_dir, output_path)
    assert accept_result["ok"] is True
    candidates_path = run_dir / "candidates.json"
    assert candidates_path.is_file()
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert data["candidates"][0]["source"] == "Cart"


def test_accept_canon_invalid_shape_refused(work_root):
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [{"kind": "bogus", "source": "Cart"}]})

    with pytest.raises(SystemExit) as exc:
        packets.do_accept(work_root, run_dir, output_path)
    assert exc.value.code == lz_common.EXIT_FAIL


# --- CLI subprocess smoke test ----------------------------------------------


def test_build_and_accept_translate_via_cli(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}})

    code, payload = run_cli(["build", "--root", str(work_root), "--kind", "translate", "--locale", "de"])
    assert code == 0, payload
    run_dir = Path(payload["batches"][0]["dir"])
    output_path = write_output(run_dir, {"translations": {"a": "Hallo"}})

    code, payload = run_cli(["accept", "--root", str(work_root), "--run", str(run_dir), "--output", str(output_path)])
    assert code == 0, payload
    assert payload["accepted"] == ["a"]
