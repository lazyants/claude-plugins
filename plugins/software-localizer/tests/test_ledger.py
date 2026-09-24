"""Tests for `ledger.py` (plan section 7): every state transition `sync()`
performs, plus the plain functions `packets.py`/`export_values.py` call
directly, plus one real-subprocess check of the CLI's adapter wiring.

Most tests call `ledger.sync()` directly with a fake `parse_fn` -- most
transitions never need to re-run script checks, and a fake makes it easy to
assert `parse_fn` was (or was not) called at all. `parse_fn` defaults to one
that fails the test if invoked, so a transition that is supposed to need no
adapter call proves it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"
sys.path.insert(0, str(SCRIPTS_DIR))

import ledger  # noqa: E402
import lz_common  # noqa: E402

EMPTY_CANON_LOCK = {"schema": 1, "entries": []}


# --- helpers ---------------------------------------------------------------


def make_cfg(target_locales=("de",), style=None, allow_identical=None, project_root="/does-not-need-to-exist"):
    """A cfg dict for tests that call `ledger.sync()` directly. `sync()`
    never reads `project_root` or validates anything, so this need not pass
    `lz_common.load_config`."""
    if style is None:
        style = {loc: {"formality": "Sie", "notes": ""} for loc in target_locales}
    return {
        "schema": 1, "project_root": project_root, "source_locale": "en",
        "target_locales": list(target_locales),
        "adapter": {"argv": ["true"], "options": {}},
        "style": style, "allow_identical": list(allow_identical or []),
        "batch_size": 40, "max_rounds": 3, "adapter_timeout_s": 300,
    }


def make_real_cfg(target_locales=("de",)):
    """A cfg dict that also passes `lz_common.load_config`, for CLI
    subprocess tests: a real project directory and a real adapter (the
    fixture toy adapter over the fixture toy project, both read-only here)."""
    return {
        "schema": 1, "project_root": str(FIXTURES_DIR / "toy_project"), "source_locale": "en",
        "target_locales": list(target_locales),
        "adapter": {"argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")], "options": {}},
        "style": {loc: {"formality": "Sie", "notes": ""} for loc in target_locales},
        "allow_identical": [], "batch_size": 40, "max_rounds": 3, "adapter_timeout_s": 30,
    }


def make_message(msg_id, source, targets=None, max_length=None):
    return {
        "id": msg_id, "source": source,
        "context": {"file": "f", "key": msg_id, "max_length": max_length, "comment": None},
        "targets": dict(targets or {}),
    }


def make_plural_message(msg_id, source_forms, target_labels_by_locale, general_index,
                         count_arguments=("count",), targets=None, max_length=None):
    return {
        "id": msg_id,
        "source": {"forms": list(source_forms)},
        "plural": {
            "source_labels": [{"label": str(i), "exact": False} for i in range(len(source_forms))],
            "target_labels": {loc: list(labels) for loc, labels in target_labels_by_locale.items()},
            "count_arguments": list(count_arguments),
            "general_index": general_index,
        },
        "context": {"file": "f", "key": msg_id, "max_length": max_length, "comment": None},
        "targets": dict(targets or {}),
    }


def make_messages(msgs):
    return {"schema": 1, "files": [], "messages": msgs}


def unexpected_parse_fn(items):
    raise AssertionError(f"parse_fn should not have been called; items={items!r}")


def make_pass_parse_fn():
    def parse_fn(items):
        return {item["key"]: {"ok": True, "tokens": []} for item in items}
    return parse_fn


def make_fail_parse_fn(fail_key_substring=":value:"):
    def parse_fn(items):
        out = {}
        for item in items:
            if fail_key_substring in item["key"]:
                out[item["key"]] = {"ok": False, "error": "boom"}
            else:
                out[item["key"]] = {"ok": True, "tokens": []}
        return out
    return parse_fn


def sync_once(root, cfg, messages, parse_fn=unexpected_parse_fn, canon_lock=None):
    return ledger.sync(root, cfg, messages, parse_fn, canon_lock if canon_lock is not None else EMPTY_CANON_LOCK)


def make_candidate(value="Hallo", value_sha256=None, origin="translate", source_sha256="s",
                    context_sha256="c", style_sha256="st", canon_sha256=None, audited_target_sha256=None,
                    checks="pass", verdict=None, accepted_by=None):
    # Every test in this file that syncs against a canon lock uses
    # EMPTY_CANON_LOCK (see `sync_once`), so `ledger.canon_sha256(<any
    # message>, EMPTY_CANON_LOCK)` is this same constant regardless of
    # which message the candidate is for.
    return {
        "value": value, "value_sha256": value_sha256 or lz_common.value_sha256(value),
        "origin": origin, "source_sha256": source_sha256, "context_sha256": context_sha256,
        "style_sha256": style_sha256,
        "canon_sha256": canon_sha256 if canon_sha256 is not None else lz_common.sha256_json([]),
        "audited_target_sha256": audited_target_sha256,
        "checks": checks, "problems": [], "verdict": verdict, "accepted_by": accepted_by,
    }


def run_cli(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "ledger.py"), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


# --- load / save -------------------------------------------------------


def test_load_returns_empty_shape_when_no_file(work_root):
    assert ledger.load(work_root) == {"schema": 1, "locales": {}}


def test_save_then_load_roundtrips(work_root):
    data = {"schema": 1, "locales": {"de": {"m1": {"state": "pending"}}}}
    ledger.save(work_root, data)
    assert ledger.load(work_root) == data


def test_save_writes_one_file_per_locale_in_the_pinned_shape(work_root):
    data = {"schema": 1, "locales": {
        "de": {"m1": {"state": "pending"}},
        "ru": {"m1": {"state": "existing"}},
    }}
    ledger.save(work_root, data)

    assert not (work_root / "ledger.json").exists()  # no single shared file
    de_on_disk = json.loads((work_root / "ledger" / "de.json").read_text(encoding="utf-8"))
    assert de_on_disk == {"schema": 1, "locale": "de", "entries": {"m1": {"state": "pending"}}}
    ru_on_disk = json.loads((work_root / "ledger" / "ru.json").read_text(encoding="utf-8"))
    assert ru_on_disk == {"schema": 1, "locale": "ru", "entries": {"m1": {"state": "existing"}}}


def test_load_combines_every_per_locale_file(work_root):
    (work_root / "ledger").mkdir()
    (work_root / "ledger" / "de.json").write_text(
        json.dumps({"schema": 1, "locale": "de", "entries": {"m1": {"state": "pending"}}}),
        encoding="utf-8",
    )
    (work_root / "ledger" / "ru.json").write_text(
        json.dumps({"schema": 1, "locale": "ru", "entries": {"m1": {"state": "existing"}}}),
        encoding="utf-8",
    )
    data = ledger.load(work_root)
    assert data == {"schema": 1, "locales": {
        "de": {"m1": {"state": "pending"}},
        "ru": {"m1": {"state": "existing"}},
    }}


def test_save_scoped_to_one_locale_leaves_other_files_untouched(work_root):
    data = {"schema": 1, "locales": {
        "de": {"a": {"state": "pending"}},
        "ru": {"a": {"state": "pending"}},
    }}
    ledger.save(work_root, data)  # writes both files

    # Mutate both locales in memory, then save only "ru" -- a locale-scoped
    # caller (packets.py/export_values.py working one locale in its own
    # process) must never rewrite a sibling locale's file.
    data["locales"]["ru"]["a"]["state"] = "translated"
    data["locales"]["de"]["a"]["state"] = "SHOULD_NOT_REACH_DISK"

    ledger.save(work_root, data, locales=["ru"])

    de_on_disk = json.loads((work_root / "ledger" / "de.json").read_text(encoding="utf-8"))
    assert de_on_disk["entries"]["a"]["state"] == "pending"

    ru_on_disk = json.loads((work_root / "ledger" / "ru.json").read_text(encoding="utf-8"))
    assert ru_on_disk["entries"]["a"]["state"] == "translated"


# --- content hashes ------------------------------------------------------


def test_source_sha256_changes_with_text():
    a = make_message("m1", "Hi")
    b = make_message("m1", "Hello")
    assert ledger.source_sha256(a) != ledger.source_sha256(b)


def test_context_sha256_changes_with_max_length():
    msg = make_message("m1", "Hi")
    h1 = ledger.context_sha256(msg, "de")
    msg["context"]["max_length"] = 10
    h2 = ledger.context_sha256(msg, "de")
    assert h1 != h2


def test_context_sha256_ignores_source_text():
    a = make_message("m1", "Hi")
    b = make_message("m1", "A completely different sentence")
    assert ledger.context_sha256(a, "de") == ledger.context_sha256(b, "de")


def test_context_sha256_changes_with_general_index():
    msg = make_plural_message(
        "m1", ["{count} item", "{count} items"],
        {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
        general_index=0,
    )
    h1 = ledger.context_sha256(msg, "de")
    msg["plural"]["general_index"] = 1
    h2 = ledger.context_sha256(msg, "de")
    assert h1 != h2


def test_context_sha256_changes_with_source_labels():
    msg = make_plural_message(
        "m1", ["{count} item", "{count} items"],
        {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
        general_index=0,
    )
    h1 = ledger.context_sha256(msg, "de")
    msg["plural"]["source_labels"][0]["exact"] = True
    h2 = ledger.context_sha256(msg, "de")
    assert h1 != h2


def test_style_sha256_differs_per_locale():
    cfg = make_cfg(target_locales=("de", "ru"), style={
        "de": {"formality": "Sie", "notes": ""},
        "ru": {"formality": "ты", "notes": ""},
    })
    assert ledger.style_sha256(cfg, "de") != ledger.style_sha256(cfg, "ru")


# --- relevant_canon / canon_sha256 (item 2 of the review fix) ------------


def test_relevant_canon_matches_by_source_substring():
    msg = make_message("m1", "Add to Cart")
    canon_lock = {"schema": 1, "entries": [
        {"id": "t-cart", "kind": "term", "source": "Cart", "occurrences": []},
        {"id": "t-nope", "kind": "term", "source": "Unrelated", "occurrences": []},
    ]}
    assert [e["id"] for e in ledger.relevant_canon(msg, canon_lock)] == ["t-cart"]


def test_relevant_canon_matches_dnt_by_occurrence_even_without_source_match():
    """A `dnt` entry naming this message's id in `occurrences` is relevant
    even when its `source` text never occurs in the message's own source --
    the occurrence rule is an OR with the source-substring rule, not a
    refinement of it."""
    msg = make_message("m1", "Hello")
    canon_lock = {"schema": 1, "entries": [
        {"id": "d-brand", "kind": "dnt", "source": "Brand", "occurrences": ["m1"]},
    ]}
    assert [e["id"] for e in ledger.relevant_canon(msg, canon_lock)] == ["d-brand"]


def test_relevant_canon_dnt_not_occurring_here_and_not_source_matching_is_excluded():
    msg = make_message("m1", "Hello")
    canon_lock = {"schema": 1, "entries": [
        {"id": "d-brand", "kind": "dnt", "source": "Brand", "occurrences": ["some-other-id"]},
    ]}
    assert ledger.relevant_canon(msg, canon_lock) == []


def test_canon_sha256_changes_when_relevant_entries_change():
    msg = make_message("m1", "Add to Cart")
    empty = ledger.canon_sha256(msg, EMPTY_CANON_LOCK)
    with_entry = ledger.canon_sha256(msg, {"schema": 1, "entries": [
        {"id": "t-cart", "kind": "term", "source": "Cart", "occurrences": []},
    ]})
    assert empty != with_entry


def test_canon_sha256_ignores_an_irrelevant_entry():
    msg = make_message("m1", "Add to Cart")
    with_irrelevant = ledger.canon_sha256(msg, {"schema": 1, "entries": [
        {"id": "t-nope", "kind": "term", "source": "Unrelated", "occurrences": []},
    ]})
    assert with_irrelevant == ledger.canon_sha256(msg, EMPTY_CANON_LOCK)


# --- sync: bootstrap -----------------------------------------------------


def test_sync_bootstraps_pending_when_no_target(work_root):
    cfg = make_cfg()
    report = sync_once(work_root, cfg, make_messages([make_message("m1", "Hello")]))
    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "pending"
    assert entry["project_value_sha256"] is None
    assert entry["candidate"] is None
    assert report["counts"]["de"]["new"] == 1


def test_sync_bootstraps_existing_when_target_present(work_root):
    cfg = make_cfg()
    sync_once(work_root, cfg, make_messages([make_message("m1", "Hello", targets={"de": "Hallo"})]))
    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "existing"
    assert entry["project_value_sha256"] == lz_common.value_sha256("Hallo")


def test_sync_handles_a_plural_message(work_root):
    cfg = make_cfg()
    msg = make_plural_message(
        "m1", ["{count} item", "{count} items"],
        {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
        general_index=1,
    )
    sync_once(work_root, cfg, make_messages([msg]))
    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "pending"


def test_sync_writes_a_separate_file_per_target_locale(work_root):
    cfg = make_cfg(target_locales=("de", "ru"))
    msg = make_message("m1", "Hello", targets={"de": "Hallo", "ru": "Privet"})
    sync_once(work_root, cfg, make_messages([msg]))

    assert not (work_root / "ledger.json").exists()
    assert (work_root / "ledger" / "de.json").is_file()
    assert (work_root / "ledger" / "ru.json").is_file()

    data = ledger.load(work_root)
    assert data["locales"]["de"]["m1"]["state"] == "existing"
    assert data["locales"]["ru"]["m1"]["state"] == "existing"


# --- sync: pending / stale / escalated -> existing on a human target -----


def test_sync_pending_to_existing_on_human_target_clears_candidate(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello")
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    ledger.set_candidate(ledger_data, "de", "m1", make_candidate(
        source_sha256=ledger.source_sha256(msg),
        context_sha256=ledger.context_sha256(msg, "de"),
        style_sha256=ledger.style_sha256(cfg, "de"),
    ))
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo"}
    report = sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "existing"
    assert entry["candidate"] is None
    assert report["counts"]["de"]["existing_from_pending"] == 1


def test_sync_stale_to_existing_on_new_human_target(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "stale"
    entry["last_exported_sha256"] = lz_common.value_sha256("Hallo")
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo (edited by a person)"}
    sync_once(work_root, cfg, make_messages([msg]))

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "existing"


def test_sync_stale_stays_stale_when_target_still_matches_last_export(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "stale"
    entry["last_exported_sha256"] = lz_common.value_sha256("Hallo")
    ledger.save(work_root, ledger_data)

    sync_once(work_root, cfg, make_messages([msg]))  # nothing changed

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "stale"


def test_sync_escalated_to_existing_on_new_human_target(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello")  # pending: last_exported_sha256 stays None
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    ledger_data["locales"]["de"]["m1"]["state"] = "escalated"
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo"}
    sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "existing"
    assert entry["candidate"] is None


# --- sync: existing / human_locked note on drift, state kept -------------


def test_sync_existing_source_change_notes_and_keeps_state(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))  # existing

    msg["source"] = "Hello there"
    report = sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "existing"
    assert entry["notes"] and "source" in entry["notes"][-1]["note"]
    assert report["notes"][-1] == {"locale": "de", "id": "m1", "note": entry["notes"][-1]["note"]}


def test_sync_human_locked_source_change_notes_and_keeps_state(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))  # existing

    ledger_data = ledger.load(work_root)
    ledger.adopt(ledger_data, "de", ["m1"], "alice")  # -> translated
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo (a person edited this)"}
    sync_once(work_root, cfg, make_messages([msg]))  # -> human_locked
    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "human_locked"

    msg["source"] = "Hello there"
    sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "human_locked"
    assert entry["notes"] and "source" in entry["notes"][-1]["note"]


# --- sync: translated ------------------------------------------------------


def test_sync_translated_to_human_locked_on_target_edit(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo!! edited"}
    sync_once(work_root, cfg, make_messages([msg]))

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "human_locked"


def test_sync_translated_to_stale_on_source_change(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    msg["source"] = "Hello there"
    sync_once(work_root, cfg, make_messages([msg]))  # parse_fn must not be called

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "stale"


def test_sync_translated_to_stale_on_style_change(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    cfg2 = make_cfg(style={"de": {"formality": "du", "notes": "casual now"}})
    sync_once(work_root, cfg2, make_messages([msg]))  # parse_fn must not be called

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "stale"


def test_sync_translated_context_change_recheck_pass_stays_translated(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    msg["context"]["max_length"] = 100  # context change only
    report = sync_once(work_root, cfg, make_messages([msg]), parse_fn=make_pass_parse_fn())

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "translated"
    assert report["counts"]["de"]["context_rechecked"] == 1


def test_sync_translated_context_change_recheck_fail_becomes_stale(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    msg["context"]["max_length"] = 100
    sync_once(work_root, cfg, make_messages([msg]), parse_fn=make_fail_parse_fn())

    entry2 = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry2["state"] == "stale"
    assert entry2["notes"][-1]["note"] == "context changed and the re-check failed"


def test_sync_translated_general_index_change_recheck_fails(work_root):
    """`general_index` alone moving must be seen as a context change (item 1
    of the review fix): the source form the general_index now points at
    requires an argument ("extra") the existing translated forms do not
    carry, so the re-check this triggers must fail and stale the entry.
    Before the fix `context_sha256` ignored `general_index`, so this change
    was invisible to `sync()` and the entry stayed `translated` forever."""
    cfg = make_cfg()
    msg = make_plural_message(
        "m1", ["{count} apple", "{count} apples {extra}"],
        {"de": [{"label": "one", "exact": True}, {"label": "other", "exact": False}]},
        general_index=0, count_arguments=("count",),
        targets={"de": {"forms": ["Hallo {count}", "Hallo {count}"]}},
    )
    sync_once(work_root, cfg, make_messages([msg]))  # existing (a project target is set)

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    old_context_sha = ledger.context_sha256(msg, "de")
    msg["plural"]["general_index"] = 1  # only general_index changes
    new_context_sha = ledger.context_sha256(msg, "de")
    assert old_context_sha != new_context_sha

    def parse_fn(items):
        out = {}
        for item in items:
            text = item["text"]
            tokens = [{"kind": "argument", "name": "count", "signature": "{count}"}]
            if "extra" in text:
                tokens.append({"kind": "argument", "name": "extra", "signature": "{extra}"})
            out[item["key"]] = {"ok": True, "tokens": tokens}
        return out

    report = sync_once(work_root, cfg, make_messages([msg]), parse_fn=parse_fn)

    entry2 = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry2["state"] == "stale"
    assert report["counts"]["de"]["context_rechecked"] == 1
    assert report["counts"]["de"]["stale"] == 1


def test_sync_translated_unchanged_stays_translated(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["m1"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    sync_once(work_root, cfg, make_messages([msg]))  # nothing changed; parse_fn must not be called

    assert ledger.load(work_root)["locales"]["de"]["m1"]["state"] == "translated"


# --- sync: candidate clearing ----------------------------------------------


def test_sync_clears_candidate_on_source_snapshot_mismatch(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello")
    sync_once(work_root, cfg, make_messages([msg]))  # pending

    ledger_data = ledger.load(work_root)
    ledger.set_candidate(ledger_data, "de", "m1", make_candidate(
        source_sha256=ledger.source_sha256(msg),
        context_sha256=ledger.context_sha256(msg, "de"),
        style_sha256=ledger.style_sha256(cfg, "de"),
    ))
    ledger.save(work_root, ledger_data)

    msg["source"] = "Hello there"
    report = sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "pending"  # unaffected: this id has no project target
    assert entry["candidate"] is None
    assert report["counts"]["de"]["candidates_cleared"] == 1


def test_sync_clears_candidate_on_canon_snapshot_mismatch(work_root):
    """Item 2 of the review fix: a candidate whose canon snapshot no longer
    matches the current canon lock's relevant entries must be cleared, the
    same way a source/context/style mismatch clears it -- a newly approved
    or changed canon entry must force a fresh check."""
    cfg = make_cfg()
    msg = make_message("m1", "Add to Cart")
    sync_once(work_root, cfg, make_messages([msg]))  # pending

    ledger_data = ledger.load(work_root)
    ledger.set_candidate(ledger_data, "de", "m1", make_candidate(
        source_sha256=ledger.source_sha256(msg),
        context_sha256=ledger.context_sha256(msg, "de"),
        style_sha256=ledger.style_sha256(cfg, "de"),
        canon_sha256=ledger.canon_sha256(msg, EMPTY_CANON_LOCK),
    ))
    ledger.save(work_root, ledger_data)

    new_canon_lock = {"schema": 1, "entries": [
        {"id": "t-cart", "kind": "term", "source": "Cart", "occurrences": []},
    ]}
    report = sync_once(work_root, cfg, make_messages([msg]), canon_lock=new_canon_lock)

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "pending"  # unaffected: this id has no project target
    assert entry["candidate"] is None
    assert report["counts"]["de"]["candidates_cleared"] == 1


def test_sync_clears_audit_candidate_on_project_value_drift(work_root):
    cfg = make_cfg()
    msg = make_message("m1", "Hello", targets={"de": "Hallo"})
    sync_once(work_root, cfg, make_messages([msg]))  # existing

    ledger_data = ledger.load(work_root)
    ledger.set_candidate(ledger_data, "de", "m1", make_candidate(
        value="Hallo (proposed)", origin="audit",
        source_sha256=ledger.source_sha256(msg),
        context_sha256=ledger.context_sha256(msg, "de"),
        style_sha256=ledger.style_sha256(cfg, "de"),
        audited_target_sha256=lz_common.value_sha256("Hallo"),
    ))
    ledger.save(work_root, ledger_data)

    msg["targets"] = {"de": "Hallo (changed again)"}
    report = sync_once(work_root, cfg, make_messages([msg]))

    entry = ledger.load(work_root)["locales"]["de"]["m1"]
    assert entry["state"] == "existing"  # candidate clearing alone does not move state
    assert entry["candidate"] is None
    assert report["counts"]["de"]["candidates_cleared"] == 1


# --- sync: gone ids ----------------------------------------------------


def test_sync_reports_gone_ids_but_keeps_the_entry(work_root):
    cfg = make_cfg()
    msg1 = make_message("m1", "Hello")
    msg2 = make_message("m2", "World")
    sync_once(work_root, cfg, make_messages([msg1, msg2]))

    report = sync_once(work_root, cfg, make_messages([msg1]))  # m2 dropped from the source

    assert {"locale": "de", "id": "m2"} in report["gone"]
    assert report["counts"]["de"]["gone"] == 1
    ledger_data = ledger.load(work_root)
    assert "m2" in ledger_data["locales"]["de"]


# --- set_candidate / record_verdict / mark_escalated ----------------------


def test_record_verdict_ignored_on_hash_mismatch():
    ledger_data = {"locales": {"de": {"m1": {"candidate": make_candidate(value_sha256="abc")}}}}
    ledger.record_verdict(ledger_data, "de", "m1", {"value_sha256": "xyz", "verdict": "pass"})
    assert ledger_data["locales"]["de"]["m1"]["candidate"]["verdict"] is None


def test_record_verdict_applied_on_hash_match():
    ledger_data = {"locales": {"de": {"m1": {"candidate": make_candidate(value_sha256="abc")}}}}
    ledger.record_verdict(ledger_data, "de", "m1", {"value_sha256": "abc", "verdict": "pass"})
    assert ledger_data["locales"]["de"]["m1"]["candidate"]["verdict"] == {"value_sha256": "abc", "verdict": "pass"}


def test_mark_escalated():
    ledger_data = {"locales": {"de": {"m1": {"state": "pending", "notes": []}}}}
    ledger.mark_escalated(ledger_data, "de", "m1", [{"check": "parse", "detail": "x"}])
    entry = ledger_data["locales"]["de"]["m1"]
    assert entry["state"] == "escalated"
    assert entry["notes"][-1]["problems"] == [{"check": "parse", "detail": "x"}]


# --- exportable / record_export -------------------------------------------


def test_exportable_requires_pass_checks_and_a_matching_pass_verdict():
    passing_verdict = {"value_sha256": lz_common.value_sha256("Hallo"), "verdict": "pass"}
    ledger_data = {"locales": {"de": {
        "a": {"state": "pending", "candidate": make_candidate(checks="fail", verdict=passing_verdict)},
        "b": {"state": "pending", "candidate": make_candidate(checks="pass", verdict=None)},
        "c": {"state": "pending", "candidate": make_candidate(checks="pass", verdict=passing_verdict)},
    }}}
    assert ledger.exportable(ledger_data, "de") == ["c"]


def test_exportable_verdict_must_be_bound_to_the_current_hash():
    stale_verdict = {"value_sha256": lz_common.value_sha256("a different value"), "verdict": "pass"}
    ledger_data = {"locales": {"de": {
        "a": {"state": "pending", "candidate": make_candidate(checks="pass", verdict=stale_verdict)},
    }}}
    assert ledger.exportable(ledger_data, "de") == []


def test_exportable_excludes_existing_and_human_locked_by_default():
    passing_verdict = {"value_sha256": lz_common.value_sha256("Hallo"), "verdict": "pass"}
    for state in ("existing", "human_locked"):
        ledger_data = {"locales": {"de": {
            "a": {"state": state, "candidate": make_candidate(checks="pass", verdict=passing_verdict)},
        }}}
        assert ledger.exportable(ledger_data, "de") == []


def test_exportable_accepted_by_waives_the_state_gate():
    passing_verdict = {"value_sha256": lz_common.value_sha256("Hallo"), "verdict": "pass"}
    ledger_data = {"locales": {"de": {
        "a": {"state": "human_locked", "candidate": make_candidate(
            checks="pass", verdict=passing_verdict, accepted_by="alice",
        )},
    }}}
    assert ledger.exportable(ledger_data, "de") == ["a"]


def test_exportable_escalated_then_fixed():
    passing_verdict = {"value_sha256": lz_common.value_sha256("Hallo"), "verdict": "pass"}
    ledger_data = {"locales": {"de": {
        "a": {"state": "escalated", "candidate": make_candidate(checks="pass", verdict=passing_verdict)},
    }}}
    assert ledger.exportable(ledger_data, "de") == ["a"]


def test_record_export_sets_translated_and_both_hashes():
    ledger_data = {"locales": {"de": {"a": {
        "state": "pending", "last_exported_sha256": None, "project_value_sha256": None,
    }}}}
    ledger.record_export(ledger_data, "de", {"a": "Hallo"})
    entry = ledger_data["locales"]["de"]["a"]
    expected = lz_common.value_sha256("Hallo")
    assert entry["state"] == "translated"
    assert entry["last_exported_sha256"] == expected
    assert entry["project_value_sha256"] == expected


# --- adopt -----------------------------------------------------------------


def test_adopt_moves_existing_and_human_locked_to_translated():
    ledger_data = {"locales": {"de": {
        "a": {"state": "existing", "project_value_sha256": "h1", "last_exported_sha256": None},
        "b": {"state": "human_locked", "project_value_sha256": "h2", "last_exported_sha256": "old"},
        "c": {"state": "pending", "project_value_sha256": None, "last_exported_sha256": None},
    }}}
    result = ledger.adopt(ledger_data, "de", ["a", "b", "c"], "alice")
    assert sorted(result["adopted"]) == ["a", "b"]
    assert result["skipped"] == ["c"]
    assert ledger_data["locales"]["de"]["a"]["state"] == "translated"
    assert ledger_data["locales"]["de"]["a"]["last_exported_sha256"] == "h1"
    assert ledger_data["locales"]["de"]["b"]["state"] == "translated"
    assert ledger_data["locales"]["de"]["b"]["last_exported_sha256"] == "h2"
    assert ledger_data["locales"]["de"]["c"]["state"] == "pending"


def test_adopt_all_existing():
    ledger_data = {"locales": {"de": {
        "a": {"state": "existing", "project_value_sha256": "h1", "last_exported_sha256": None},
        "b": {"state": "pending", "project_value_sha256": None, "last_exported_sha256": None},
    }}}
    result = ledger.adopt(ledger_data, "de", None, "alice")
    assert result["adopted"] == ["a"]


# --- CLI (real subprocess) --------------------------------------------------


def test_sync_cli_bootstrap(work_root):
    cfg = make_real_cfg()
    msg = make_message("user.greeting", "Hello, {name}!", targets={"de": "Hallo, {name}!"})
    (work_root / "localize.json").write_text(json.dumps(cfg), encoding="utf-8")
    (work_root / "messages.json").write_text(json.dumps(make_messages([msg])), encoding="utf-8")

    code, result = run_cli(["sync", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True

    entry = ledger.load(work_root)["locales"]["de"]["user.greeting"]
    assert entry["state"] == "existing"


def test_sync_cli_context_recheck_uses_the_real_adapter(work_root):
    cfg = make_real_cfg()
    msg = make_message("user.greeting", "Hello, {name}!", targets={"de": "Hallo, {name}!"})
    (work_root / "localize.json").write_text(json.dumps(cfg), encoding="utf-8")
    (work_root / "messages.json").write_text(json.dumps(make_messages([msg])), encoding="utf-8")

    code, result = run_cli(["sync", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True

    ledger_data = ledger.load(work_root)
    entry = ledger_data["locales"]["de"]["user.greeting"]
    entry["state"] = "translated"
    entry["last_exported_sha256"] = entry["project_value_sha256"]
    ledger.save(work_root, ledger_data)

    msg["context"]["max_length"] = 200  # context change only -- triggers a real `parse` call
    (work_root / "messages.json").write_text(json.dumps(make_messages([msg])), encoding="utf-8")

    code, result = run_cli(["sync", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True

    entry2 = ledger.load(work_root)["locales"]["de"]["user.greeting"]
    assert entry2["state"] == "translated"  # "Hallo, {name}!" parses fine and matches the source's argument


def test_adopt_cli(work_root):
    cfg = make_real_cfg()
    msg = make_message("user.greeting", "Hello, {name}!", targets={"de": "Hallo, {name}!"})
    (work_root / "localize.json").write_text(json.dumps(cfg), encoding="utf-8")
    (work_root / "messages.json").write_text(json.dumps(make_messages([msg])), encoding="utf-8")

    code, result = run_cli(["sync", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True

    code, result = run_cli(["adopt", "--root", str(work_root), "--locale", "de", "--id", "user.greeting", "--by", "alice"])
    assert code == 0 and result["ok"] is True
    assert result["adopted"] == ["user.greeting"]

    entry = ledger.load(work_root)["locales"]["de"]["user.greeting"]
    assert entry["state"] == "translated"


def test_adopt_cli_only_writes_its_own_locale_file(work_root):
    cfg = make_real_cfg(target_locales=("de", "ru"))
    msg = make_message(
        "user.greeting", "Hello, {name}!",
        targets={"de": "Hallo, {name}!", "ru": "Privet, {name}!"},
    )
    (work_root / "localize.json").write_text(json.dumps(cfg), encoding="utf-8")
    (work_root / "messages.json").write_text(json.dumps(make_messages([msg])), encoding="utf-8")

    code, result = run_cli(["sync", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True

    de_before = (work_root / "ledger" / "de.json").read_text(encoding="utf-8")

    code, result = run_cli([
        "adopt", "--root", str(work_root), "--locale", "ru", "--id", "user.greeting", "--by", "alice",
    ])
    assert code == 0 and result["ok"] is True

    de_after = (work_root / "ledger" / "de.json").read_text(encoding="utf-8")
    assert de_after == de_before  # adopting "ru" must not touch "de"'s file

    data = ledger.load(work_root)
    assert data["locales"]["ru"]["user.greeting"]["state"] == "translated"
    assert data["locales"]["de"]["user.greeting"]["state"] == "existing"  # untouched
