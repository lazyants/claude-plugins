"""Tests for `packets.py`: build selection and batching,
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
        "adapter": {
            "argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")],
            "code_dir": str(FIXTURES_DIR),  # the shared fixture script lives here
            "options": options or {},
        },
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
                    accepted_by=None, audited_target_sha=None, canon_sha256=None):
    # Every fixture in this file sets up an empty canon.lock.json (see
    # `setup_workspace`), so `ledger_mod.canon_sha256(<any message>, EMPTY_CANON_LOCK)`
    # is this same constant regardless of which message the candidate is for.
    return {
        "value": value, "value_sha256": lz_common.value_sha256(value), "origin": origin,
        "source_sha256": "s", "context_sha256": "c", "style_sha256": "st",
        "canon_sha256": canon_sha256 if canon_sha256 is not None else lz_common.sha256_json([]),
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


def test_build_batch_names_deduplicate_case_insensitively(work_root):
    """Security-review fix: two prefixes differing only by case ("Auth" and
    "auth") must not produce the same batch name -- on a case-insensitive
    filesystem (macOS) that would make the second batch's directory silently
    overwrite the first's."""
    cfg = make_cfg()
    ids = ["Auth.x", "auth.y"]
    msgs = make_messages([make_message(i, "x") for i in ids])
    ledger_locales = {"de": {i: make_entry("pending") for i in ids}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)

    names = {b["batch"] for b in result["batches"]}
    assert names == {"Auth", "auth-2"}  # two distinct names, not one overwriting the other
    dirs = {Path(b["dir"]) for b in result["batches"]}
    assert len(dirs) == 2
    for d in dirs:
        assert (d / "packet.json").is_file()


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


def build_one_batch(work_root, cfg, msgs, ledger_locales, kind, locale, canon_lock=None):
    setup_workspace(work_root, cfg, msgs, ledger_locales, canon_lock=canon_lock)
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


def test_accept_translate_batches_the_whole_batch_into_one_parse_call(work_root, monkeypatch):
    """C1 fix: `accept_translate` calls the adapter's parse ONCE for the
    whole batch, not once per item."""
    call_count = 0

    def counting_parse(root, cfg, project_dir, items):
        nonlocal call_count
        call_count += 1
        return fake_parse(root, cfg, project_dir, items)

    monkeypatch.setattr(adapter_client, "parse", counting_parse)

    cfg = make_cfg()
    ids = ["m.a", "m.b", "m.c"]
    msgs = make_messages([make_message(i, "Hello") for i in ids])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {i: make_entry("pending") for i in ids}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"m.a": "Hallo", "m.b": "Hallo", "m.c": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)

    assert result["accepted"] == ["m.a", "m.b", "m.c"]
    assert call_count == 1


def test_accept_translate_malformed_value_becomes_shape_failure_never_reaches_adapter(work_root, monkeypatch):
    """F2 fix (security-review observation): a translation value that is
    not a string and not a well-formed `{"forms": [...]}` (int, None, list,
    or a forms dict with non-string forms) must become a per-item "shape"
    failure -- never reach the adapter's parse -- and must not abort the
    rest of the batch."""
    def guarding_parse(root, cfg, project_dir, items):
        for item in items:
            assert isinstance(item["text"], str), f"a non-string value reached the adapter: {item!r}"
        return fake_parse(root, cfg, project_dir, items)

    monkeypatch.setattr(adapter_client, "parse", guarding_parse)

    cfg = make_cfg()
    ids = ["m.a", "m.b", "m.c", "m.d"]
    msgs = make_messages([make_message(i, "Hello") for i in ids])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {i: make_entry("pending") for i in ids}}, "translate", "de")
    output_path = write_output(run_dir, {
        "translations": {"m.a": None, "m.b": 42, "m.c": ["Hallo"], "m.d": "Hallo"},
    })

    result = packets.do_accept(work_root, run_dir, output_path)

    assert result["accepted"] == ["m.d"]
    failed = {f["id"]: f for f in result["failed"]}
    for bad_id in ("m.a", "m.b", "m.c"):
        assert failed[bad_id]["problems"][0]["check"] == "shape"

    ledger_data = ledger_mod.load(work_root)
    for bad_id in ("m.a", "m.b", "m.c"):
        entry = ledger_data["locales"]["de"][bad_id]
        assert entry["candidate"]["checks"] == "fail"
        assert entry["candidate"]["problems"][0]["check"] == "shape"
        assert entry["rounds"] == 1
    good_entry = ledger_data["locales"]["de"]["m.d"]
    assert good_entry["candidate"]["checks"] == "pass"


def test_accept_translate_escalates_at_max_rounds(work_root):
    cfg = make_cfg(max_rounds=1)
    msgs = make_messages([make_message("a", "Hello {name}")])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("pending")}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"a": "Hallo"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["escalated"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["state"] == "escalated"


def test_accept_translate_plural_single_target_label_succeeds(work_root):
    """A plural message whose locale needs only one target form must not
    crash accept: `_single_or_list` must keep the one-element parse-result
    list list-shaped, since `checks.check_candidate` always treats a
    plural's parse results as a list -- collapsing it to a bare dict would
    make that code iterate the dict's keys instead of forms, raising
    `AttributeError` on the first `.get()` call."""
    cfg = make_cfg()
    msg = make_plural_message(
        "a", ["1 Artikel", "{count} Artikel"], {"de": [{"label": "other", "exact": False}]}, general_index=1,
    )
    run_dir = build_one_batch(work_root, cfg, make_messages([msg]), {"de": {"a": make_entry("pending")}}, "translate", "de")
    output_path = write_output(run_dir, {"translations": {"a": {"forms": ["{count} Dinge"]}}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["accepted"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["candidate"]["checks"] == "pass"
    assert entry["candidate"]["value"] == {"forms": ["{count} Dinge"]}


def test_build_translate_embeds_canon_relevant_by_occurrence_not_only_source_match(work_root):
    """Item 2 of the review fix: `ledger.relevant_canon`'s occurrence rule
    (a `dnt` entry naming this message's id) must embed the entry into the
    packet even when its `source` text does not occur in the message's own
    source -- the old packets-local rule matched by source substring only
    and would have left this entry out."""
    cfg = make_cfg()
    msg = make_message("a", "Hello")
    canon_lock = {"schema": 1, "entries": [
        {"id": "d-brand", "kind": "dnt", "source": "Brand", "note": "", "occurrences": ["a"]},
    ]}
    run_dir = build_one_batch(
        work_root, cfg, make_messages([msg]), {"de": {"a": make_entry("pending")}}, "translate", "de",
        canon_lock=canon_lock,
    )
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    assert [e["id"] for e in packet["canon"]["entries"]] == ["d-brand"]


def test_accept_translate_stores_canon_sha256_matching_ledger_relevant_canon(work_root):
    cfg = make_cfg()
    msg = make_message("a", "Hello")
    canon_lock = {"schema": 1, "entries": [
        {"id": "d-brand", "kind": "dnt", "source": "Brand", "note": "", "occurrences": ["a"]},
    ]}
    run_dir = build_one_batch(
        work_root, cfg, make_messages([msg]), {"de": {"a": make_entry("pending")}}, "translate", "de",
        canon_lock=canon_lock,
    )
    output_path = write_output(run_dir, {"translations": {"a": "Hallo Brand"}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["accepted"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    candidate = ledger_data["locales"]["de"]["a"]["candidate"]
    assert candidate["canon_sha256"] == ledger_mod.canon_sha256(msg, canon_lock)


def test_accept_review_rebuilt_candidate_under_changed_canon_treated_as_missing(work_root):
    """Item 2 of the review fix: a review verdict must also bind to the
    canon snapshot the packet was built from, the same way it binds to
    source/context/style. This simulates a candidate whose `canon_sha256`
    moved after the packet was built (the canon lock changed and
    `ledger.sync` rebuilt/cleared the candidate) -- the old verdict must
    not attach even though `value_sha256` still matches."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    item = packet["items"][0]
    assert item["canon_sha256"] == candidate["canon_sha256"]  # the packet froze the candidate's snapshot as-built

    ledger_data = ledger_mod.load(work_root)
    ledger_data["locales"]["de"]["a"]["candidate"]["canon_sha256"] = "canon-changed"
    ledger_mod.save(work_root, ledger_data, locales=["de"])

    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    assert result["passed"] == []
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["candidate"]["verdict"] is None


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


def test_accept_review_invalid_verdict_enum_treated_as_missing_no_rounds_increment(work_root):
    """Item 4 of the review fix: an unrecognized `verdict` string must be
    treated exactly like a missing verdict -- no rounds increment, no
    candidate change, no escalation -- never silently applied as a fail."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "maybe", "issues": [], "proposed": None,
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    assert result["failed"] == []
    assert result["passed"] == []
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["rounds"] == 0
    assert entry["candidate"]["verdict"] is None


def test_accept_review_malformed_issues_treated_as_missing(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style"}], "proposed": None,  # issue is missing "text"
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["rounds"] == 0


def test_accept_review_malformed_proposed_shape_treated_as_missing(work_root):
    """A non-plural message's `proposed` must be a plain string; a
    plural-shaped `{"forms": [...]}` on a non-plural message is invalid and
    the whole verdict is treated as missing -- never reaching
    `checks_mod.forms_of` unguarded."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too informal"}], "proposed": {"forms": ["Hallo"]},
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry["rounds"] == 0
    assert entry["candidate"]["value"] == "Hallo!"  # untouched


def test_accept_review_plural_proposed_wrong_shape_treated_as_missing(work_root):
    """The inverse of the above: a plural message's `proposed` must be
    `{"forms": [...]}`; a bare string is invalid."""
    cfg = make_cfg()
    msg = make_plural_message(
        "a", ["1 Artikel", "{count} Artikel"], {"de": [{"label": "other", "exact": False}]}, general_index=1,
    )
    candidate = make_candidate({"forms": ["{count} Artikel!"]}, checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, make_messages([msg]), {"de": {"a": make_entry("pending", candidate=candidate)}},
        "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too informal"}], "proposed": "{count} Dinge",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    assert ledger_data["locales"]["de"]["a"]["rounds"] == 0


def test_accept_audit_invalid_verdict_enum_treated_as_missing(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("existing")}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    value_sha = packet["items"][0]["value_sha256"]
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": value_sha, "verdict": "nope",
        "issues": [], "proposed": "Hallo!", "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert entry.get("audit_proposal") is None


def test_accept_review_rebuilt_candidate_under_changed_style_treated_as_missing(work_root):
    """Item 2 of the review fix: a verdict must bind to the review context,
    not only the value hash. Here the candidate is rebuilt (simulating a
    retranslation after `ledger.style_sha256` changed) with the *same*
    value, so `value_sha256` still matches what the packet captured -- only
    `style_sha256` moved. The old verdict must not attach."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    item = packet["items"][0]
    assert item["style_sha256"] == "st"  # the packet froze the candidate's snapshot as-built

    # Simulate a rebuild under a changed style: same value, new style_sha256.
    ledger_data = ledger_mod.load(work_root)
    ledger_data["locales"]["de"]["a"]["candidate"]["style_sha256"] = "st-changed"
    ledger_mod.save(work_root, ledger_data, locales=["de"])

    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
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


def test_accept_review_fail_below_max_rounds_installs_proposal_and_increments(work_root):
    """Review round 3, item 3: escalation timing. A failure must be tested
    against `rounds` as it stood BEFORE this failure, not after bumping it
    for this round -- before the fix, the bump happened first, so a failure
    at `rounds == max_rounds - 1` escalated one round early and discarded a
    proposal that should still have been installed and reviewed."""
    cfg = make_cfg(max_rounds=2)
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!", checks="pass")
    entry = make_entry("pending", candidate=candidate, rounds=1)  # max_rounds - 1
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": entry}}, "review", "de")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too informal"}], "proposed": "Hallo",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["escalated"] == []
    assert result["failed"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    e = ledger_data["locales"]["de"]["a"]
    assert e["state"] == "pending"  # not escalated
    assert e["rounds"] == 2
    assert e["candidate"]["value"] == "Hallo"  # the checked proposal was installed
    assert e["candidate"]["verdict"] is None


def test_accept_review_fail_at_max_rounds_escalates_without_a_further_increment(work_root):
    """The other half of item 3's boundary: a failure at `rounds ==
    max_rounds` (already reached) escalates and must not bump `rounds`
    again or touch the existing candidate."""
    cfg = make_cfg(max_rounds=2)
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo!", checks="pass")
    entry = make_entry("pending", candidate=candidate, rounds=2)  # == max_rounds already
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
    assert e["rounds"] == 2  # not bumped further
    assert e["candidate"]["value"] == "Hallo!"  # never replaced with the proposal
    assert e["review_proposal"]["value"] == "Hallo"


def test_accept_review_fail_on_audit_candidate_reroutes_to_audit_proposal(work_root):
    """A review packet built from a candidate whose `origin` is "audit"
    (installed by `accept-audit`, which still needs a review verdict on its
    hash before export) must, on a failed review with a `proposed`
    replacement, store that replacement back as the entry's
    `audit_proposal` -- not as a fresh candidate with `accepted_by: None`,
    which `ledger.exportable` can never select (its state gate only waives
    for a set `accepted_by`, and this entry's state is "existing") and
    `accept-audit` can never reach again (it reads `audit_proposal`,
    already cleared when this candidate was accepted)."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo (audited)"})])
    audited_sha = lz_common.value_sha256("Hallo (audited)")
    candidate = make_candidate(
        "Hallo (audited)", checks="pass", origin="audit", accepted_by="alice", audited_target_sha=audited_sha,
    )
    entry = make_entry("existing", candidate=candidate)
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": entry}}, "review", "de")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "too stiff"}], "proposed": "Hallo (better)",
        "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["failed"] == ["a"]
    assert result["audit_proposals"] == ["a"]
    ledger_data = ledger_mod.load(work_root)
    e = ledger_data["locales"]["de"]["a"]
    # the old accepted candidate is untouched (state gate still waived by
    # `accepted_by`) except for the fail verdict just recorded on it
    assert e["candidate"]["value"] == "Hallo (audited)"
    assert e["candidate"]["origin"] == "audit"
    assert e["candidate"]["accepted_by"] == "alice"
    assert e["candidate"]["verdict"]["verdict"] == "fail"
    # the checked replacement is a fresh audit proposal, not a candidate
    proposal = e["audit_proposal"]
    assert proposal["value"] == "Hallo (better)"
    assert proposal["checks"] == "pass"
    assert proposal["audited_target_sha256"] == audited_sha
    # never exportable as it stands (accepted_by None, no verdict yet) and
    # it does not silently become so
    assert "a" not in ledger_mod.exportable(ledger_data, "de")


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

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["invalid_canon_candidates"] == []
    side_file = run_dir / "new_canon_candidates.json"
    assert side_file.is_file()
    data = json.loads(side_file.read_text(encoding="utf-8"))
    cand = data["candidates"][0]
    assert cand["source"] == "Cart"
    # item 3: the side file is written in the canon import shape (plan
    # section 10 / `canon.py import --file`'s documented shape), not the
    # raw `{"kind","source","proposed","note"}` a review turn emits.
    assert cand["occurrences"] == ["a"]
    assert cand["translations"] == {"de": {"proposed": "Warenkorb", "current": []}}


def test_accept_review_invalid_new_canon_candidates_dropped_and_listed(work_root):
    """Item 3 of the review fix: a `new_canon_candidates` entry that is not
    a valid candidate object is dropped, not written to the side file, and
    listed in the accept output instead of breaking `report.py` later."""
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": "not-a-list",
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["passed"] == ["a"]  # the verdict itself is unaffected
    assert result["invalid_canon_candidates"] == [{"id": "a", "candidate": "not-a-list"}]
    side_file = run_dir / "new_canon_candidates.json"
    assert not side_file.is_file()  # nothing invalid ever reaches disk


def test_accept_review_mixed_valid_and_invalid_canon_candidate_entries(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello")])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("pending", candidate=candidate)}}, "review", "de",
    )
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        "new_canon_candidates": [
            {"kind": "term", "source": "Cart", "proposed": "Warenkorb", "note": ""},
            {"kind": "bogus", "source": "Cart"},  # bad kind
            {"kind": "term", "source": ""},  # empty source
            "just a string",
        ],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert len(result["invalid_canon_candidates"]) == 3
    side_file = run_dir / "new_canon_candidates.json"
    data = json.loads(side_file.read_text(encoding="utf-8"))
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["source"] == "Cart"


def test_accept_review_valid_canon_candidate_imports_into_canon(work_root):
    """The side file's shape is exactly what `canon.import_candidates`
    documents -- import it and confirm the occurrence and locale proposal
    land where `canon.py import --file` would put them."""
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
    data = json.loads(side_file.read_text(encoding="utf-8"))

    canon = canon_mod.load(work_root)
    result = canon_mod.import_candidates(canon, data["candidates"])
    assert len(result["added"]) == 1
    entry = canon["entries"][0]
    assert entry["source"] == "Cart"
    assert entry["occurrences"] == ["a"]
    assert entry["translations"]["de"]["value"] == "Warenkorb"
    assert entry["translations"]["de"]["status"] == "proposed"


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
        "canon_sha256": lz_common.sha256_json([]),
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


def test_accept_canon_missing_candidates_list_refused(work_root):
    """The envelope itself (no 'candidates' list at all) is the one shape
    nothing can be salvaged from -- still a hard failure."""
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"nope": "not a candidates list"})

    with pytest.raises(SystemExit) as exc:
        packets.do_accept(work_root, run_dir, output_path)
    assert exc.value.code == lz_common.EXIT_FAIL


def test_accept_canon_invalid_candidate_dropped_and_listed_not_refused(work_root):
    """Unlike a malformed envelope, one invalid candidate inside an
    otherwise-valid list must not fail the whole batch -- it is dropped and
    listed in the result, and `accept` still succeeds and keeps the valid
    candidates."""
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [{"kind": "bogus", "source": "Cart"}]})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["ok"] is True
    assert result["candidates"] == 0
    assert len(result["dropped"]) == 1
    data = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    assert data["candidates"] == []


def test_accept_canon_non_string_occurrences_dropped(work_root):
    """`occurrences: [123]` must not reach `candidates.json`: a non-string
    entry there would let `canon.py import` keep it, and
    `checks._check_dnt` -- which matches occurrences by exact string id --
    would then silently never find the message again."""
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [
        {"kind": "dnt", "source": "Cart", "note": "", "occurrences": [123]},
    ]})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["candidates"] == 0
    assert result["dropped"] == [{"index": 0, "candidate": {"kind": "dnt", "source": "Cart", "note": "", "occurrences": [123]}}]


def test_accept_canon_occurrence_id_not_in_packet_dropped(work_root):
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [
        {"kind": "term", "source": "Cart", "note": "", "occurrences": ["not-in-packet"]},
    ]})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["candidates"] == 0
    assert len(result["dropped"]) == 1


def test_accept_canon_mixed_valid_and_invalid_candidates(work_root):
    cfg = make_cfg(target_locales=("de",))
    msgs = make_messages([make_message("a", "Cart")])
    setup_workspace(work_root, cfg, msgs, {})
    result = packets.do_build(work_root, "canon", None, TEMPLATES_DIR)
    run_dir = Path(result["batches"][0]["dir"])
    output_path = write_output(run_dir, {"candidates": [
        {"kind": "term", "source": "Cart", "note": "", "occurrences": ["a"],
         "translations": {"de": {"proposed": "Warenkorb", "current": []}}},
        {"kind": "term", "source": "Cart", "occurrences": [123]},
        {"kind": "term", "source": ""},  # empty source
        "just a string",
    ]})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["candidates"] == 1
    assert len(result["dropped"]) == 3
    data = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["source"] == "Cart"


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
