"""Tests for `packets.py`: build selection and batching,
accept's per-kind rules (translate/review/audit/canon), and `accept-audit`.

The usage-research kinds (`research`, `research_verify`) are built and
accepted against a hand-written `canon.json`; they never touch the ledger.

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
import report as report_mod  # noqa: E402
import toy_adapter  # noqa: E402
from handoff_fixture import record_agent_turn  # noqa: E402

EMPTY_CANON_LOCK = {"schema": 1, "entries": []}


@pytest.fixture
def approved_context():
    return json.loads((FIXTURES_DIR / "approved_canon_context.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("restricted", [False, True])
def test_audit_approved_context_golden_packet(work_root, approved_context, restricted):
    """Freeze real decisions, build both audit paths, inspect the rendered prompt.

    This proves context transfer, not the model's eventual linguistic verdict.
    """
    lock = canon_mod.freeze(work_root, approved_context["canon"])
    messages = make_messages([make_message(message["id"], message["source"], targets=message["targets"])
                              for message in approved_context["messages"]])
    setup_workspace(work_root, make_cfg(), messages,
                    {"de": {message["id"]: make_entry("existing") for message in messages["messages"]}},
                    canon_lock=lock)
    if restricted:
        lz_common.atomic_write_json(work_root / "runs/de/canon-audit-t-evidence.json", {
            "occurrences": [message["id"] for message in messages["messages"]],
            "source": "evidence", "before": "Nachweis", "after": "Beweis", "reason": "Owner decision",
        })
    result = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR,
                              entry_ids=["t-evidence"] if restricted else None)
    assert sum(len(_load_batch_items(batch)) for batch in result["batches"]) == 3
    actual_context = {}
    for batch in result["batches"]:
        packet = lz_common.read_json(Path(batch["dir"]) / "packet.json", "packet")
        # Golden data omits only generated entry hashes; approval, scope,
        # membership, order and per-message association must match exactly.
        for msg_id, context in packet["canon_context"].items():
            actual_context[msg_id] = {
                "entries": [{key: value for key, value in entry.items() if key != "sha256"}
                            for entry in context["entries"]],
                "groups": context["groups"],
            }
        assert [entry["id"] for entry in packet["canon"]["entries"]] in ([], ["t-evidence"])
        for item in packet["items"]:
            message = packets.item_to_message(item, "de")
            assert ledger_mod.canon_sha256(message, packet["canon"]) == ledger_mod.canon_sha256(message, lock)
        prompt = (Path(batch["dir"]) / "prompt.md").read_text(encoding="utf-8")
        assert "never expands `canon`'s mechanical rules" in prompt
        assert "waives a failed check" in prompt
        for context in packet["canon_context"].values():
            for entry in context["entries"]:
                assert entry["translations"]["de"]["value"] in prompt
    assert actual_context == approved_context["expected_context"]


def test_audit_without_groups_does_not_duplicate_existing_canon(work_root, approved_context):
    del approved_context["canon"]["groups"]
    lock = canon_mod.freeze(work_root, approved_context["canon"])
    msg = make_message("app:rank.rowCouple", "ancestor couple", targets={"de": "Vorfahrenpaar"})
    run_dir = build_one_batch(work_root, make_cfg(), make_messages([msg]),
                              {"de": {msg["id"]: make_entry("existing")}}, "audit", "de", canon_lock=lock)
    packet = lz_common.read_json(run_dir / "packet.json", "packet")
    assert [entry["id"] for entry in packet["canon"]["entries"]] == ["t-ancestor-couple"]
    assert packet["canon_context"] == {}


def test_canon_context_is_locale_scoped_one_hop_and_approved_only(work_root, approved_context):
    canon = approved_context["canon"]
    evidence_group = canon["groups"][0]
    canon["groups"].extend([
        {**evidence_group, "id": "transitive", "entry_ids": ["t-evidence-based-genealogy", "t-save"]},
        {**evidence_group, "id": "proposed", "approved_by": None},
    ])
    lock = canon_mod.freeze(work_root, canon)
    assert "proposed" not in {group["id"] for group in lock["groups"]}
    context = packets.audit_canon_context(lock, approved_context["messages"], "de")
    assert [group["id"] for group in context["app:analysis"]["groups"]] == ["evidence"]
    assert "t-save" not in {entry["id"] for entry in context["app:analysis"]["entries"]}
    assert packets.audit_canon_context(lock, approved_context["messages"], "fr") == {}
    # Matching an occurrence is exact; it introduces no aliases or case folding.
    assert packets.audit_canon_context(lock, [{"id": "app:rank.rowcouple", "source": "Ancestor couple"}], "de") == {}
    assert packets.audit_canon_context(lock, [{"id": "different", "source": "Evidence-based analysis"}], "de") == {}


@pytest.mark.parametrize("invalid", ["missing_member", "unapproved_member", "duplicate_member", "missing_scope"])
def test_freeze_refuses_incomplete_approved_group_without_replacing_lock(work_root, approved_context, invalid):
    canon = approved_context["canon"]
    canon_mod.freeze(work_root, canon)
    before = canon_mod.load_lock(work_root)
    group = canon["groups"][0]
    if invalid == "missing_member":
        group["entry_ids"].append("missing")
    elif invalid == "unapproved_member":
        canon["entries"][1]["translations"]["de"]["status"] = "proposed"
    elif invalid == "duplicate_member":
        group["entry_ids"].append(group["entry_ids"][0])
    else:
        del group["scope"]
    with pytest.raises(ValueError):
        canon_mod.freeze(work_root, canon)
    assert canon_mod.load_lock(work_root) == before


def test_audit_context_does_not_waive_checks_or_change_candidate_hash(work_root, approved_context):
    approved_context["canon"]["entries"].append({
        "id": "d-brand", "kind": "dnt", "source": "Acme", "occurrences": [],
        "approved_by": "owner", "approved_at": "2026-10-25",
    })
    approved_context["canon"]["groups"][0]["entry_ids"].append("d-brand")
    lock = canon_mod.freeze(work_root, approved_context["canon"])
    msg = make_message("app:analysis", "evidence-based analysis {count}",
                       targets={"de": "evidenzbasierte Analysen {count}"})
    run_dir = build_one_batch(work_root, make_cfg(), make_messages([msg]),
                              {"de": {msg["id"]: make_entry("existing")}}, "audit", "de", canon_lock=lock)
    packet = lz_common.read_json(run_dir / "packet.json", "packet")
    assert packet["canon_context"][msg["id"]]["groups"]
    for proposal, expected_checks in [("evidenzbasierte Analysen", "fail"),
                                       ("evidenzbasierte Analysen {count}", "pass")]:
        output = write_output(run_dir, {"verdicts": {msg["id"]: {
            "value_sha256": packet["items"][0]["value_sha256"],
            "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "fail",
            "issues": [{"kind": "grammar", "text": "Example proposal for mechanical checks"}],
            "proposed": proposal, "new_canon_candidates": [],
        }}})
        packets.do_accept(work_root, run_dir, output)
        stored = ledger_mod.load(work_root)["locales"]["de"][msg["id"]]["audit_proposal"]
        assert stored["checks"] == expected_checks
        assert stored["canon_sha256"] == ledger_mod.canon_sha256(msg, lock)


# --- helpers -----------------------------------------------------------


def fake_parse(root, cfg, project_dir, items):
    results = {}
    for item in items:
        assert isinstance(item.get("id"), str), f"a parse item reached the adapter without its message id: {item!r}"
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


@pytest.mark.parametrize("kind", packets.KINDS)
def test_build_packs_small_nested_groups(work_root, kind):
    cfg = make_cfg(batch_size=40)
    ids = [f"app.screen{n:03d}.{key}" for n in range(100) for key in ("label", "title")]
    msgs = make_messages([make_message(i, "Hello", targets={"de": "Hallo"}) for i in reversed(ids)])
    entries = {
        i: make_entry(
            "existing" if kind == "audit" else "pending",
            candidate=make_candidate() if kind == "review" else None,
        ) for i in reversed(ids)
    }
    setup_workspace(work_root, cfg, msgs, {"de": entries})

    result = packets.do_build(work_root, kind, None if kind == "canon" else "de", TEMPLATES_DIR)

    assert [b["items"] for b in result["batches"]] == [40] * 5
    assert [b["batch"] for b in result["batches"]] == [f"app.screen{n:03d}" for n in range(0, 100, 20)]
    packed_ids = [[i["id"] for i in _load_batch_items(b)] for b in result["batches"]]
    assert packed_ids == [ids[start:start + 40] for start in range(0, len(ids), 40)]


def test_make_batches_keeps_fitting_groups_whole():
    groups = [[f"{prefix}.{n}" for n in range(size)] for prefix, size in (("a", 3), ("b", 3), ("c", 2))]
    ids = [i for group in groups for i in group]
    assert packets.make_batches(list(reversed(ids)), 5) == [groups[0], groups[1] + groups[2]]


def test_make_batches_splits_oversized_group_on_its_own():
    before = [f"a.{n:02d}" for n in range(3)]
    large = [f"b.{n:02d}" for n in range(90)]
    after = ["c.label", "c.title", "d.label", "d.title"]
    batches = packets.make_batches(list(reversed(before + large + after)), 40)
    assert batches == [before, large[:40], large[40:80], large[80:], after]
    assert [len(b) for b in packets.make_batches(large, 40)] == [40, 40, 10]


@pytest.mark.parametrize("ids, size, expected", [
    ([], 40, []),
    (["c", "a", "b"], 2, [["a", "b"], ["c"]]),
    (["a.x", "a.y", "b.x"], 2, [["a.x", "a.y"], ["b.x"]]),
])
def test_make_batches_boundaries(ids, size, expected):
    assert packets.make_batches(ids, size) == expected


@pytest.mark.parametrize("locale", ["de", "ru"])
def test_build_keeps_each_kinds_evidence_and_legacy_runs(work_root, locale):
    cfg = make_cfg(target_locales=("de", "ru"))
    msgs = make_messages([make_message("app.a", "Hello", targets={locale: "Hallo"})])
    setup_workspace(work_root, cfg, msgs, {
        locale: {"app.a": make_entry("existing", candidate=make_candidate("Hallo"))},
    })
    legacy_dir = work_root / "runs" / locale / "app"
    lz_common.atomic_write_json(legacy_dir / "packet.json", {"kind": "audit", "items": []})
    lz_common.atomic_write_text(legacy_dir / "prompt.md", "Legacy audit evidence\n")
    snapshots = {path: path.read_bytes() for path in legacy_dir.iterdir()}

    for kind in ("audit", "review", "translate"):
        if kind == "translate":
            data = ledger_mod.load(work_root)
            data["locales"][locale]["app.a"]["state"] = "pending"
            ledger_mod.save(work_root, data)
        result = packets.do_build(work_root, kind, locale, TEMPLATES_DIR)
        batch = result["batches"][0]
        batch_dir = Path(batch["dir"])
        assert batch["batch"] == "app"
        assert batch_dir == work_root / "runs" / locale / "_packets" / kind / "app"
        assert not (batch_dir / "answer.json").exists()
        packet = lz_common.read_json(batch_dir / "packet.json", "packet.json")
        assert packet["kind"] == kind
        assert packet["locale"] == locale
        assert [item["id"] for item in packet["items"]] == ["app.a"]
        lz_common.atomic_write_json(batch_dir / "answer.json", {"turn": kind})
        lz_common.atomic_write_text(batch_dir / "model-transcript.txt", f"{kind} transcript\n")
        lz_common.atomic_write_json(batch_dir / "new_canon_candidates.json", {"turn": kind})
        snapshots.update({path: path.read_bytes() for path in batch_dir.iterdir()})
        assert all(path.read_bytes() == content for path, content in snapshots.items())


@pytest.mark.parametrize("kind", packets.KINDS)
def test_build_avoids_files_in_legacy_kind_named_batches(work_root, kind):
    cfg = make_cfg(batch_size=1)
    ids = [f"{kind}.title", "packet.json.title"]
    msgs = make_messages([make_message(i, "Hello", targets={"de": "Hallo"}) for i in ids])
    entries = {
        i: make_entry("existing" if kind == "audit" else "pending", candidate=make_candidate())
        for i in ids
    }
    setup_workspace(work_root, cfg, msgs, {"de": entries})
    locale_dir = "_canon" if kind == "canon" else "de"
    legacy_dir = work_root / "runs" / locale_dir / kind
    lz_common.atomic_write_json(legacy_dir / "packet.json", {"kind": kind, "items": []})
    lz_common.atomic_write_text(legacy_dir / "prompt.md", "Legacy prompt\n")
    snapshots = {path: path.read_bytes() for path in legacy_dir.iterdir()}

    result = packets.do_build(work_root, kind, None if kind == "canon" else "de", TEMPLATES_DIR)

    assert {b["batch"] for b in result["batches"]} == {kind, "packet.json"}
    assert {i["id"] for b in result["batches"] for i in _load_batch_items(b)} == set(ids)
    assert all(path.read_bytes() == content for path, content in snapshots.items())


def test_build_translate_fix_round_reuses_its_directory(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("app.a", "Hello")])
    setup_workspace(work_root, cfg, msgs, {"de": {"app.a": make_entry("pending")}})
    initial = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)["batches"][0]
    data = ledger_mod.load(work_root)
    problems = [{"check": "style", "detail": "too informal"}]
    data["locales"]["de"]["app.a"]["candidate"] = make_candidate("Hi", checks="fail", problems=problems)
    ledger_mod.save(work_root, data)

    retry = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)["batches"][0]

    assert retry["dir"] == initial["dir"]
    item = _load_batch_items(retry)[0]
    assert item["previous"] == "Hi"
    assert item["problems"] == problems


def test_build_batches_by_prefix_and_batch_size(work_root):
    cfg = make_cfg(batch_size=2)
    ids = ["app.a", "app.b", "app.c", "nav.a"]
    msgs = make_messages([make_message(i, "x") for i in ids])
    ledger_locales = {"de": {i: make_entry("pending") for i in ids}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)

    result = packets.do_build(work_root, "translate", "de", TEMPLATES_DIR)
    # "app" has 3 ids with batch_size=2 -> two same-prefix batches (2 + 1);
    # "nav" has 1 id -> one batch. The oversized "app" group splits on its own.
    sizes = sorted(b["items"] for b in result["batches"])
    assert sizes == [1, 1, 2]
    names = {b["batch"] for b in result["batches"]}
    assert names == {"app", "app-2", "nav"}


def test_build_batch_names_deduplicate_case_insensitively(work_root):
    """Security-review fix: two prefixes differing only by case ("Auth" and
    "auth") must not produce the same batch name -- on a case-insensitive
    filesystem (macOS) that would make the second batch's directory silently
    overwrite the first's."""
    cfg = make_cfg(batch_size=1)
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

    result = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR, entry_ids=["t-cart"])
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
    record_agent_turn(run_dir, path)
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


def test_accept_translate_still_reads_legacy_run_directory(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("app.a", "Hello")])
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"app.a": make_entry("pending")}}, "translate", "de",
    )
    legacy_dir = work_root / "runs" / "de" / "app"
    run_dir.rename(legacy_dir)
    output_path = write_output(legacy_dir, {"translations": {"app.a": "Hallo"}})

    result = packets.do_accept(work_root, legacy_dir, output_path)

    assert result["accepted"] == ["app.a"]
    assert ledger_mod.load(work_root)["locales"]["de"]["app.a"]["candidate"]["value"] == "Hallo"


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


def _record_parse_items(monkeypatch):
    """Wrap the autouse `fake_parse`, keeping every item that reaches the
    adapter's parse."""
    recorded: list = []

    def recording_parse(root, cfg, project_dir, items):
        recorded.extend(items)
        return fake_parse(root, cfg, project_dir, items)

    monkeypatch.setattr(adapter_client, "parse", recording_parse)
    return recorded


def _plural_and_plain_messages():
    plural = make_plural_message(
        "m.plural", ["1 Artikel", "{count} Artikel"], {"de": [{"label": "other", "exact": False}]}, general_index=1,
    )
    return plural, make_message("m.plain", "Hello plain")


def test_accept_translate_parse_items_carry_the_id_of_their_own_message(work_root, monkeypatch):
    """#980: every source and value item names the message its text belongs
    to. Texts are unique, so a missing, constant or neighbouring id fails."""
    recorded = _record_parse_items(monkeypatch)
    cfg = make_cfg()
    plural, plain = _plural_and_plain_messages()
    run_dir = build_one_batch(
        work_root, cfg, make_messages([plural, plain]),
        {"de": {"m.plural": make_entry("pending"), "m.plain": make_entry("pending")}}, "translate", "de",
    )
    output_path = write_output(run_dir, {"translations": {"m.plural": {"forms": ["{count} Dinge"]}, "m.plain": "Hallo schlicht"}})

    result = packets.do_accept(work_root, run_dir, output_path)

    assert sorted(result["accepted"]) == ["m.plain", "m.plural"]
    expected = {
        "1 Artikel": "m.plural", "{count} Artikel": "m.plural", "{count} Dinge": "m.plural",
        "Hello plain": "m.plain", "Hallo schlicht": "m.plain",
    }
    assert sorted((item["text"], item["id"]) for item in recorded) == sorted(expected.items())


def test_accept_review_parse_items_carry_the_id_of_their_own_message(work_root, monkeypatch):
    """#980: a failing verdict's `proposed` items name their message too."""
    recorded = _record_parse_items(monkeypatch)
    cfg = make_cfg()
    plural, plain = _plural_and_plain_messages()
    cand_plural = make_candidate({"forms": ["{count} Dinge"]}, checks="pass")
    cand_plain = make_candidate("Hallo schlicht", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, make_messages([plural, plain]),
        {"de": {"m.plural": make_entry("pending", candidate=cand_plural),
                "m.plain": make_entry("pending", candidate=cand_plain)}},
        "review", "de",
    )
    issues = [{"kind": "style", "text": "too informal"}]
    output_path = write_output(run_dir, {"verdicts": {
        "m.plural": {"value_sha256": cand_plural["value_sha256"], "verdict": "fail", "issues": issues,
                     "proposed": {"forms": ["{count} Sachen"]}, "new_canon_candidates": []},
        "m.plain": {"value_sha256": cand_plain["value_sha256"], "verdict": "fail", "issues": issues,
                    "proposed": "Hallo freundlich", "new_canon_candidates": []},
    }})

    result = packets.do_accept(work_root, run_dir, output_path)

    assert result["missing"] == []
    expected = {
        "1 Artikel": "m.plural", "{count} Artikel": "m.plural", "{count} Sachen": "m.plural",
        "Hello plain": "m.plain", "Hallo freundlich": "m.plain",
    }
    assert sorted((item["text"], item["id"]) for item in recorded) == sorted(expected.items())


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


def test_packed_translate_preserves_canon_order_and_candidate_hashes(work_root):
    msgs = [make_message("a.label", "Workspace"), make_message("b.label", "Cart Workspace")]
    canon_lock = {"schema": 1, "entries": [
        {"id": "d-cart", "kind": "dnt", "source": "Cart", "note": "", "occurrences": []},
        {"id": "d-other", "kind": "dnt", "source": "Other", "note": "", "occurrences": []},
        {"id": "d-workspace", "kind": "dnt", "source": "Workspace", "note": "", "occurrences": []},
    ]}
    run_dir = build_one_batch(
        work_root, make_cfg(batch_size=2), make_messages(msgs),
        {"de": {msg["id"]: make_entry("pending") for msg in msgs}}, "translate", "de",
        canon_lock=canon_lock,
    )
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    assert [i["id"] for i in packet["items"]] == ["a.label", "b.label"]
    assert [e["id"] for e in packet["canon"]["entries"]] == ["d-cart", "d-workspace"]
    output_path = write_output(run_dir, {"translations": {
        "a.label": "Mein Workspace", "b.label": "Mein Cart Workspace",
    }})

    result = packets.do_accept(work_root, run_dir, output_path)

    assert result["accepted"] == ["a.label", "b.label"]
    entries = ledger_mod.load(work_root)["locales"]["de"]
    for msg in msgs:
        assert entries[msg["id"]]["candidate"]["canon_sha256"] == ledger_mod.canon_sha256(msg, canon_lock)


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
        "value_sha256": value_sha, "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "nope",
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


@pytest.mark.parametrize("kind", ["audit", "review"])
@pytest.mark.parametrize("legacy", [False, True])
def test_accept_new_canon_candidates_written_beside_packet(work_root, kind, legacy):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    candidate = make_candidate("Hallo", checks="pass")
    run_dir = build_one_batch(
        work_root, cfg, msgs, {"de": {"a": make_entry("existing", candidate=candidate)}}, kind, "de",
    )
    if legacy:
        legacy_dir = work_root / "runs" / "de" / "a"
        run_dir.rename(legacy_dir)
        run_dir = legacy_dir
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": candidate["value_sha256"], "verdict": "pass", "issues": [], "proposed": None,
        **({"audit_sha256": packet["items"][0]["audit_sha256"]} if kind == "audit" else {}),
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
        "value_sha256": value_sha, "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "fail",
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


@pytest.mark.parametrize("state", ["existing", "translated", "human_locked"])
@pytest.mark.parametrize("plural", [False, True])
def test_accept_audit_without_replacement_survives_save_load_and_report(work_root, state, plural):
    cfg = make_cfg()
    if plural:
        message = make_plural_message(
            "a", ["One item", "{count} items"],
            {"de": [{"label": "one", "exact": False}, {"label": "other", "exact": False}]},
            general_index=1, targets={"de": {"forms": ["Ein Artikel", "{count} Artikel"]}},
        )
    else:
        message = make_message("a", "Hello", targets={"de": "Hallo"})
    original_entry = make_entry(state)
    run_dir = build_one_batch(work_root, cfg, make_messages([message]), {"de": {"a": original_entry}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    item = packet["items"][0]
    issues = [{"kind": "meaning", "text": "Needs domain clarification before a safe replacement."}]
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": item["value_sha256"], "audit_sha256": item["audit_sha256"],
        "verdict": "fail", "issues": issues,
        "proposed": None, "new_canon_candidates": [],
    }}})

    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["ok"] is True
    assert result["failed"] == ["a"]
    assert result["missing"] == result["extra"] == result["audit_proposals"] == []
    saved = ledger_mod.load(work_root)
    entry = saved["locales"]["de"]["a"]
    finding = entry["audit_proposal"]
    assert finding == {
        "audit_scope": "full",
        "value": None, "value_sha256": None, "checks": None, "problems": [], "issues": issues,
        "audited_target_sha256": item["value_sha256"],
        "source_sha256": ledger_mod.source_sha256(message),
        "context_sha256": ledger_mod.context_sha256(message, "de"),
        "style_sha256": ledger_mod.style_sha256(cfg, "de"),
        "canon_sha256": ledger_mod.canon_sha256(message, packet["canon"]), "run": packet["batch"],
    }
    assert {key: entry[key] for key in original_entry} == original_entry
    assert ledger_mod.exportable(saved, "de") == []

    # A finding is durable evidence, never an operator-approved candidate.
    acceptance = packets.do_accept_audit(work_root, "de", ["a"], "alice")
    assert acceptance["accepted"] == []
    assert acceptance["failed"] == [{"id": "a", "reason": "audit finding has no proposed replacement"}]
    assert ledger_mod.load(work_root) == saved
    report_result = report_mod.do_report(work_root, "de")
    text = Path(report_result["report"]).read_text(encoding="utf-8")
    audit_section = text.split("## Audit findings")[1].split("## Existing")[0]
    assert "`a`" in audit_section
    assert "unresolved: no proposed replacement" in audit_section
    assert "meaning: Needs domain clarification before a safe replacement." in audit_section
    assert "proposed: None" not in text
    assert "checks pass" not in audit_section
    existing_section = text.split("## Existing")[1].split("## Canon candidates")[0]
    assert "`a`" not in existing_section


@pytest.mark.parametrize("proposed", [None, "Hallo!"])
def test_accept_audit_retains_packet_style_provenance(work_root, proposed):
    cfg = make_cfg()
    message = make_message("a", "Hello", targets={"de": "Hallo"})
    run_dir = build_one_batch(work_root, cfg, make_messages([message]), {"de": {"a": make_entry("existing")}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": packet["items"][0]["value_sha256"],
        "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "Needs follow-up."}], "proposed": proposed,
    }}})
    changed_cfg = {**cfg, "style": {"de": {"formality": "du", "notes": "Changed since build."}}}
    lz_common.atomic_write_json(work_root / "localize.json", changed_cfg)

    packets.do_accept(work_root, run_dir, output_path)
    finding = ledger_mod.load(work_root)["locales"]["de"]["a"]["audit_proposal"]
    assert finding["style_sha256"] == ledger_mod.style_sha256(cfg, "de")
    assert finding["style_sha256"] != ledger_mod.style_sha256(changed_cfg, "de")


def test_accept_audit_without_replacement_replaces_known_full_scope_proposal(work_root):
    cfg = make_cfg()
    message = make_message("a", "Hello", targets={"de": "Hallo"})
    entry = make_entry("existing")
    entry["audit_proposal"] = {"audit_scope": "full", "value": "Old correction", "checks": "pass", "issues": []}
    run_dir = build_one_batch(work_root, cfg, make_messages([message]), {"de": {"a": entry}}, "audit", "de")
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": packet["items"][0]["value_sha256"],
        "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "fail",
        "issues": [{"kind": "meaning", "text": "The earlier correction is unsafe."}], "proposed": None,
    }}})

    packets.do_accept(work_root, run_dir, output_path)
    assert ledger_mod.load(work_root)["locales"]["de"]["a"]["audit_proposal"]["value"] is None
    assert packets.do_accept_audit(work_root, "de", ["a"], "alice")["accepted"] == []


def test_accept_audit_pass_persists_provenance_without_changing_translation_state(work_root):
    cfg = make_cfg()
    msgs = make_messages([make_message("a", "Hello", targets={"de": "Hallo"})])
    run_dir = build_one_batch(work_root, cfg, msgs, {"de": {"a": make_entry("existing")}}, "audit", "de")
    before = ledger_mod.load(work_root)["locales"]["de"]["a"]
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    value_sha = packet["items"][0]["value_sha256"]
    output_path = write_output(run_dir, {"verdicts": {"a": {
        "value_sha256": value_sha, "audit_sha256": packet["items"][0]["audit_sha256"], "verdict": "pass",
        "issues": [], "proposed": None, "new_canon_candidates": [],
    }}})

    packets.do_accept(work_root, run_dir, output_path)
    ledger_data = ledger_mod.load(work_root)
    entry = ledger_data["locales"]["de"]["a"]
    assert {key: entry[key] for key in before} == before
    assert entry.get("audit_proposal") is None
    assert entry["audit"]["verdict"] == "pass"
    assert entry["audit"]["snapshot"] == ledger_mod.audit_snapshot(msgs["messages"][0], "de", cfg, EMPTY_CANON_LOCK)
    assert entry["audit"]["run"] == packet["batch"]


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


# --- usage research ------------------------------------------------------------


def make_canon_entry(entry_id, source, kind="term", locales=None, occurrences=(), note=""):
    """`locales` maps a locale to `(value, status)` or `(value, status, current)`."""
    translations = {}
    for locale, spec in (locales or {}).items():
        translation = {"value": spec[0], "status": spec[1], "approved_by": None, "approved_at": None}
        if len(spec) > 2:
            translation["current"] = list(spec[2])
        translations[locale] = translation
    return {"id": entry_id, "kind": kind, "source": source, "note": note,
            "occurrences": list(occurrences), "translations": translations}


def setup_research_workspace(root, entries, cfg=None, messages=None):
    cfg = cfg or make_cfg()
    setup_workspace(root, cfg, messages or make_messages([]))
    canon_mod.save(root, {"schema": 1, "entries": list(entries)})
    return cfg


def research_term(recommendation="Konto", confidence="high", note="Matches the field.", findings=None):
    if findings is None:
        findings = [{"site": "Example", "url": "https://example.org/a", "rendering": "Konto",
                     "quote": "Ihr Konto verwalten"}]
    return {"recommendation": recommendation, "confidence": confidence, "note": note, "findings": findings}


def verdict_for(item, verdict="holds", confirmed=True, **overrides):
    out = {
        "research_sha256": item["research_sha256"],
        "findings": [{"index": n, "confirmed": confirmed, "detail": "found"}
                     for n in range(len(item["research"]["findings"]))],
        "verdict": verdict, "problems": "", "better": "",
    }
    out.update(overrides)
    return out


def packet_of(batch_info):
    return lz_common.read_json(Path(batch_info["dir"]) / "packet.json", "packet.json")


def research_accept(root, batch_info, output):
    """Run `do_accept` over a built batch with `output`; returns the result."""
    run_dir = Path(batch_info["dir"])
    return packets.do_accept(root, run_dir, write_output(run_dir, output))


def research_records(root, locale="de"):
    return canon_mod.load_research(root, locale)["entries"]


def test_build_research_default_selection_batches_at_most_eight(work_root):
    eligible = [make_canon_entry(f"t-w{n:02d}", f"Word{n}", locales={"de": (f"Wort{n}", "proposed")})
                for n in range(9)]
    eligible.append(make_canon_entry("t-none", "Bare"))
    eligible.append(make_canon_entry("u-label", "Save", kind="ui_label", locales={"de": ("Speichern", "proposed")}))
    skipped = [
        make_canon_entry("d-api", "API", kind="dnt"),
        make_canon_entry("t-done", "Done", locales={"de": ("Fertig", "approved")}),
        make_canon_entry("t-seen", "Seen", locales={"de": ("Gesehen", "proposed")}),
    ]
    setup_research_workspace(work_root, eligible + skipped)
    canon_mod.save_research(work_root, "de", {"schema": 1, "locale": "de", "entries": {"t-seen": {
        "kind": "term", "source": "Seen", "research": research_term(), "research_sha256": "x",
        "researched": {"run": "r", "at": "t"}, "verification": None}}})

    result = packets.do_build(work_root, "research", "de", TEMPLATES_DIR)

    assert result["selected"] == len(eligible) == 11
    sizes = [b["items"] for b in result["batches"]]
    assert packets.RESEARCH_BATCH_SIZE == 8
    assert len(sizes) == 2 and max(sizes) <= packets.RESEARCH_BATCH_SIZE
    ids = [i["id"] for b in result["batches"] for i in packet_of(b)["items"]]
    assert sorted(ids) == sorted(e["id"] for e in eligible)


def test_build_research_entry_flag_reresearches_and_refuses_bad_ids(work_root):
    entries = [
        make_canon_entry("t-done", "Done", locales={"de": ("Fertig", "approved")}),
        make_canon_entry("t-seen", "Seen", locales={"de": ("Gesehen", "proposed")}),
        make_canon_entry("d-api", "API", kind="dnt"),
    ]
    setup_research_workspace(work_root, entries)
    canon_mod.save_research(work_root, "de", {"schema": 1, "locale": "de", "entries": {"t-seen": {
        "kind": "term", "source": "Seen", "research": research_term(), "research_sha256": "x",
        "researched": {"run": "r", "at": "t"}, "verification": None}}})

    result = packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=["t-done", "t-seen"])
    assert sorted(i["id"] for b in result["batches"] for i in packet_of(b)["items"]) == ["t-done", "t-seen"]

    for bad in ("t-nope", "d-api"):
        with pytest.raises(SystemExit) as exc:
            packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=["t-done", bad])
        assert exc.value.code == lz_common.EXIT_CANNOT


def test_build_research_item_examples_are_the_first_five_present_with_targets(work_root):
    occurrences = ["m0", "gone", "m1", "m2", "m3", "m4", "m5", "m6"]
    msgs = make_messages([
        make_message("m0", "Open account", targets={"de": "Konto öffnen"}),
        make_message("m1", "Your account"),
        make_message("m2", "Account settings", targets={"de": "Kontoeinstellungen"}),
        make_message("m3", "Account A"), make_message("m4", "Account B"),
        make_message("m5", "Account C"), make_message("m6", "Account D"),
    ])
    entry = make_canon_entry("t-account", "account", note="a user login",
                             locales={"de": ("Konto", "proposed", ["Zugang", "Profil"])}, occurrences=occurrences)
    cfg = make_cfg(style={"de": {"formality": "Sie", "notes": "formal"}})
    cfg["research"] = {"field": "genealogy web app", "sites": {"de": ["wiki.genealogy.net"]}}
    setup_research_workspace(work_root, [entry], cfg=cfg, messages=msgs)

    result = packets.do_build(work_root, "research", "de", TEMPLATES_DIR)

    batch = result["batches"][0]
    packet = packet_of(batch)
    assert packet["kind"] == "research" and packet["locale"] == "de"
    assert packet["field"] == "genealogy web app"
    assert packet["sites"] == ["wiki.genealogy.net"]
    assert packet["style"] == {"formality": "Sie", "notes": "formal"}
    item = packet["items"][0]
    assert item["examples"] == [
        {"id": "m0", "source": "Open account", "value": "Konto öffnen"},
        {"id": "m1", "source": "Your account"},
        {"id": "m2", "source": "Account settings", "value": "Kontoeinstellungen"},
        {"id": "m3", "source": "Account A"},
        {"id": "m4", "source": "Account B"},
    ]
    assert (item["kind"], item["source"], item["note"]) == ("term", "account", "a user login")
    assert (item["proposed"], item["status"], item["current"]) == ("Konto", "proposed", ["Zugang", "Profil"])
    prompt = (Path(batch["dir"]) / "prompt.md").read_text(encoding="utf-8")
    assert "{{PACKET_JSON}}" not in prompt and '"t-account"' in prompt


def test_build_research_without_config_block_has_null_field_and_no_sites(work_root):
    setup_research_workspace(work_root, [make_canon_entry("t-a", "A", locales={"de": ("Ä", "proposed")})])
    packet = packet_of(packets.do_build(work_root, "research", "de", TEMPLATES_DIR)["batches"][0])
    assert packet["field"] is None and packet["sites"] == []


@pytest.mark.parametrize("kind", ["research", "research_verify"])
@pytest.mark.parametrize("occurrences,unresolved,example_ids,resolvable", [
    (["gone", "m0", "m1"], ["gone"], ["m0", "m1"], 2),
    (["gone-a", "gone-b"], ["gone-a", "gone-b"], [], 0),
    ([f"m{n}" for n in range(7)] + ["gone-tail"], ["gone-tail"],
     [f"m{n}" for n in range(5)], 7),
    ([f"m{n}" for n in range(7)], [], [f"m{n}" for n in range(5)], 7),
    ([], [], [], 0),
])
def test_research_build_reports_unresolved_occurrences_without_changing_state(
        work_root, kind, occurrences, unresolved, example_ids, resolvable):
    # m1 has no German target: it is still a resolvable source example.
    messages = make_messages([make_message(f"m{n}", f"Account {n}",
                                         targets={"de": f"Konto {n}"} if n != 1 else {})
                              for n in range(7)])
    entry = make_canon_entry("t-account", "account", note="a login",
                             locales={"de": ("Konto", "approved")}, occurrences=occurrences)
    entry["translations"]["de"].update(approved_by="operator", approved_at="2026-10-24T12:00:00Z")
    setup_research_workspace(work_root, [entry], messages=messages)
    research = research_term()
    canon_mod.save_research(work_root, "de", {"schema": 1, "locale": "de", "entries": {"t-account": {
        "kind": "term", "source": "account", "research": research,
        "research_sha256": canon_mod.research_sha256(research),
        "researched": {"run": "earlier", "at": "2026-10-24T12:00:00Z"}, "verification": None}}})
    checkpoint_sha256 = lz_common.sha256_json(research_records(work_root)["t-account"])
    lz_common.atomic_write_json(work_root / "canon.history.json", [{"entry": "t-account", "action": "approve"}])
    old_packet = work_root / "runs" / "de" / "earlier" / "packet.json"
    lz_common.atomic_write_json(old_packet, {"kind": "audit", "items": []})
    old_packet_bytes = old_packet.read_bytes()
    before = _snapshot(work_root, skip={"runs"})

    code, result = run_cli(["build", "--root", str(work_root), "--kind", kind,
                            "--locale", "de", "--entry", "t-account"])

    assert code == 0 and result["ok"] is True
    assert result["selected"] == 1 and result["skipped"] == []
    assert result["diagnostics"] == ([{
        "code": "unresolved_canon_occurrences", "entry_id": "t-account",
        "unresolved_ids": unresolved, "declared_count": len(occurrences),
        "resolvable_count": resolvable,
    }] if unresolved else [])
    item = packet_of(result["batches"][0])["items"][0]
    expected_examples = [{"id": i, "source": f"Account {i[1:]}",
                          **({"value": f"Konto {i[1:]}"} if i != "m1" else {})}
                         for i in example_ids]
    assert item["examples"] == expected_examples
    expected_item = {"id": "t-account", "kind": "term", "source": "account", "note": "a login",
                     "examples": expected_examples, "checkpoint_sha256": checkpoint_sha256}
    if kind == "research":
        expected_item.update(proposed="Konto", status="approved", current=[])
    else:
        expected_item.update(research=research, research_sha256=canon_mod.research_sha256(research))
    assert item == expected_item
    assert _snapshot(work_root, skip={"runs"}) == before
    assert old_packet.read_bytes() == old_packet_bytes


def build_research_batch(work_root, ids):
    return packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=list(ids))["batches"][0]


def test_accept_research_stores_valid_record_and_reports_the_rest(work_root):
    names = ["ok", "badurl", "noquote", "badconf", "nonote", "emptyhigh", "absent"]
    setup_research_workspace(work_root, [make_canon_entry(f"t-{n}", n.title(), locales={"de": (n, "proposed")})
                                         for n in names])
    batch = build_research_batch(work_root, [f"t-{n}" for n in names])
    good_finding = research_term()["findings"][0]
    terms = {
        "t-ok": research_term(),
        "t-badurl": research_term(findings=[dict(good_finding, url="ftp://example.org/a")]),
        "t-noquote": research_term(findings=[dict(good_finding, quote="")]),
        "t-badconf": research_term(confidence="certain"),
        "t-nonote": research_term(note="  "),
        "t-emptyhigh": research_term(findings=[], confidence="high"),
        "t-ghost": research_term(),
    }

    result = research_accept(work_root, batch, {"terms": terms})

    assert result["ok"] is True and result["kind"] == "research" and result["locale"] == "de"
    assert result["stored"] == ["t-ok"]
    assert sorted(i["id"] for i in result["invalid"]) == ["t-badconf", "t-badurl", "t-emptyhigh", "t-nonote", "t-noquote"]
    assert all(i["reason"] for i in result["invalid"])
    assert result["missing"] == ["t-absent"]
    assert result["extra"] == ["t-ghost"]
    records = research_records(work_root)
    assert list(records) == ["t-ok"]
    record = records["t-ok"]
    assert record["research"] == research_term()
    assert record["research_sha256"] == canon_mod.research_sha256(record["research"])
    assert record["verification"] is None
    assert (record["kind"], record["source"]) == ("term", "Ok")
    assert record["researched"]["run"] == Path(batch["dir"]).name


def test_accept_research_empty_findings_are_honest_only_at_low_confidence(work_root):
    setup_research_workspace(work_root, [make_canon_entry("t-a", "A", locales={"de": ("Ä", "proposed")})])
    batch = build_research_batch(work_root, ["t-a"])
    result = research_accept(work_root, batch, {"terms": {"t-a": research_term(findings=[], confidence="low")}})
    assert result["stored"] == ["t-a"]
    assert research_records(work_root)["t-a"]["research"]["findings"] == []


def test_accept_research_without_terms_object_is_refused(work_root):
    setup_research_workspace(work_root, [make_canon_entry("t-a", "A", locales={"de": ("Ä", "proposed")})])
    batch = build_research_batch(work_root, ["t-a"])
    with pytest.raises(SystemExit) as exc:
        research_accept(work_root, batch, {"terms": ["t-a"]})
    assert exc.value.code == lz_common.EXIT_FAIL
    assert not canon_mod.research_path(work_root, "de").exists()


def test_build_research_verify_selects_unverified_with_note_and_examples(work_root):
    msgs = make_messages([make_message("m0", "Your account", targets={"de": "Ihr Konto"})])
    entries = [
        make_canon_entry("t-a", "account", note="a login", locales={"de": ("Konto", "proposed")}, occurrences=["m0"]),
        make_canon_entry("t-b", "board", locales={"de": ("Tafel", "proposed")}),
    ]
    setup_research_workspace(work_root, entries, messages=msgs)
    research_accept(work_root, build_research_batch(work_root, ["t-a", "t-b"]),
                    {"terms": {"t-a": research_term(), "t-b": research_term(recommendation="Tafel")}})
    data = canon_mod.load_research(work_root, "de")
    data["entries"]["t-b"]["verification"] = {"verdict": "holds"}
    canon_mod.save_research(work_root, "de", data)

    result = packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR)

    packet = packet_of(result["batches"][0])
    assert packet["kind"] == "research_verify" and packet["locale"] == "de"
    assert set(packet) == {"kind", "locale", "batch", "field", "style", "items"}
    assert [i["id"] for i in packet["items"]] == ["t-a"]
    item = packet["items"][0]
    record = research_records(work_root)["t-a"]
    assert item["note"] == "a login"
    assert item["examples"] == [{"id": "m0", "source": "Your account", "value": "Ihr Konto"}]
    assert item["research"] == record["research"] and item["research_sha256"] == record["research_sha256"]
    assert (item["kind"], item["source"]) == ("term", "account")


def test_build_research_verify_entry_without_record_is_refused(work_root):
    setup_research_workspace(work_root, [make_canon_entry("t-a", "A", locales={"de": ("Ä", "proposed")})])
    with pytest.raises(SystemExit) as exc:
        packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR, entry_ids=["t-a"])
    assert exc.value.code == lz_common.EXIT_CANNOT


def researched_workspace(work_root, ids=("t-a",), findings=None, locales=("de",)):
    entries = [make_canon_entry(i, i, locales={loc: ("X", "proposed") for loc in locales}) for i in ids]
    setup_research_workspace(work_root, entries, cfg=make_cfg(target_locales=locales))
    batch = build_research_batch(work_root, ids)
    research_accept(work_root, batch, {"terms": {i: research_term(findings=findings) for i in ids}})


def build_verify_batch(work_root, ids=None):
    return packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR,
                            entry_ids=list(ids) if ids else None)["batches"][0]


def test_accept_research_verify_stores_the_verification(work_root):
    researched_workspace(work_root)
    batch = build_verify_batch(work_root)
    item = packet_of(batch)["items"][0]

    result = research_accept(work_root, batch, {"verdicts": {"t-a": verdict_for(
        item, verdict="weak", problems="thin", better="Benutzerkonto")}})

    assert result["ok"] is True and result["kind"] == "research_verify"
    assert result["verified"] == ["t-a"] and result["invalid"] == [] and result["missing"] == []
    verification = research_records(work_root)["t-a"]["verification"]
    assert verification["verdict"] == "weak" and verification["better"] == "Benutzerkonto"
    assert verification["research_sha256"] == item["research_sha256"]
    assert verification["findings"] == [{"index": 0, "confirmed": True, "detail": "found"}]
    assert verification["run"] == Path(batch["dir"]).name and verification["at"]


def test_accept_research_verify_reports_missing_and_extra(work_root):
    researched_workspace(work_root, ids=("t-a", "t-b"))
    batch = build_verify_batch(work_root)
    item = packet_of(batch)["items"][0]
    result = research_accept(work_root, batch, {"verdicts": {item["id"]: verdict_for(item), "t-ghost": {}}})
    assert result["verified"] == [item["id"]]
    assert result["missing"] == [i["id"] for i in packet_of(batch)["items"] if i["id"] != item["id"]]
    assert result["extra"] == ["t-ghost"]


def test_accept_research_verify_hash_mismatch_is_missing(work_root):
    researched_workspace(work_root)
    batch = build_verify_batch(work_root)
    item = packet_of(batch)["items"][0]
    result = research_accept(work_root, batch, {"verdicts": {"t-a": verdict_for(item, research_sha256="0" * 64)}})
    assert result["missing"] == ["t-a"] and result["verified"] == []
    assert research_records(work_root)["t-a"]["verification"] is None


def test_accept_research_verify_after_a_quotation_only_rewrite_is_missing(work_root):
    researched_workspace(work_root)
    old_batch = build_verify_batch(work_root)
    old_item = packet_of(old_batch)["items"][0]
    # Re-research keeps the recommendation and changes one quotation: a
    # check that compared only the recommendation would attach the old
    # verdict to evidence nobody re-opened.
    changed = research_term()
    changed["findings"][0]["quote"] = "Das Konto verwalten"
    rerun = build_research_batch(work_root, ["t-a"])
    research_accept(work_root, rerun, {"terms": {"t-a": changed}})
    assert research_records(work_root)["t-a"]["research_sha256"] != old_item["research_sha256"]

    result = research_accept(work_root, old_batch, {"verdicts": {"t-a": verdict_for(old_item)}})

    assert result["verified"] == [] and result["missing"] == ["t-a"]
    assert research_records(work_root)["t-a"]["verification"] is None


def test_accept_research_resets_verification(work_root):
    researched_workspace(work_root)
    batch = build_verify_batch(work_root)
    research_accept(work_root, batch, {"verdicts": {"t-a": verdict_for(packet_of(batch)["items"][0])}})
    assert research_records(work_root)["t-a"]["verification"] is not None

    rerun = build_research_batch(work_root, ["t-a"])
    research_accept(work_root, rerun, {"terms": {"t-a": research_term(recommendation="Benutzerkonto")}})

    record = research_records(work_root)["t-a"]
    assert record["research"]["recommendation"] == "Benutzerkonto"
    assert record["verification"] is None


TWO_FINDINGS = [
    {"site": "A", "url": "https://a.example/x", "rendering": "Konto", "quote": "ein Konto"},
    {"site": "B", "url": "https://b.example/x", "rendering": "Konto", "quote": "das Konto"},
]


@pytest.mark.parametrize("change", [
    {"findings": [{"index": 0, "confirmed": True, "detail": ""}]},
    {"findings": [{"index": 0, "confirmed": True, "detail": ""}, {"index": 0, "confirmed": False, "detail": ""}]},
    {"findings": [{"index": 0, "confirmed": True, "detail": ""}, {"index": 1, "confirmed": "yes", "detail": ""}]},
    {"findings": [{"index": 0, "confirmed": True, "detail": ""}, {"index": 2, "confirmed": True, "detail": ""}]},
    {"verdict": "fine"},
    {"problems": None},
    {"better": 3},
])
def test_accept_research_verify_malformed_verdict_is_invalid(work_root, change):
    researched_workspace(work_root, findings=TWO_FINDINGS)
    batch = build_verify_batch(work_root)
    item = packet_of(batch)["items"][0]
    assert len(item["research"]["findings"]) == 2

    result = research_accept(work_root, batch, {"verdicts": {"t-a": verdict_for(item, **change)}})

    assert result["verified"] == [] and result["missing"] == []
    assert [i["id"] for i in result["invalid"]] == ["t-a"] and result["invalid"][0]["reason"]
    assert research_records(work_root)["t-a"]["verification"] is None


def test_accept_research_verify_without_verdicts_object_is_refused(work_root):
    researched_workspace(work_root)
    batch = build_verify_batch(work_root)
    with pytest.raises(SystemExit) as exc:
        research_accept(work_root, batch, {"terms": {}})
    assert exc.value.code == lz_common.EXIT_FAIL


def _snapshot(root, skip):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not any(part in skip for part in p.relative_to(root).parts)}


def test_research_accepts_touch_neither_the_ledger_nor_another_locale(work_root):
    cfg = make_cfg(target_locales=("de", "ru"))
    msgs = make_messages([make_message("a", "Hello")])
    ledger_locales = {"de": {"a": make_entry("pending")}, "ru": {"a": make_entry("pending")}}
    setup_workspace(work_root, cfg, msgs, ledger_locales)
    canon_mod.save(work_root, {"schema": 1, "entries": [
        make_canon_entry("t-a", "A", locales={"de": ("Ä", "proposed"), "ru": ("Я", "proposed")})]})
    other = {"schema": 1, "locale": "ru", "entries": {"t-a": {
        "kind": "term", "source": "A", "research": research_term(), "research_sha256": "x",
        "researched": {"run": "r", "at": "t"}, "verification": None}}}
    canon_mod.save_research(work_root, "ru", other)
    before = _snapshot(work_root, skip={"runs", "canon.research"})
    ru_before = canon_mod.research_path(work_root, "ru").read_bytes()

    batch = build_research_batch(work_root, ["t-a"])
    research_accept(work_root, batch, {"terms": {"t-a": research_term()}})
    verify = build_verify_batch(work_root)
    research_accept(work_root, verify, {"verdicts": {"t-a": verdict_for(packet_of(verify)["items"][0])}})

    assert research_records(work_root)["t-a"]["verification"] is not None
    # The comparison is only meaningful if the ledger files are in the snapshot.
    assert any(name.startswith("ledger") for name in before)
    assert _snapshot(work_root, skip={"runs", "canon.research"}) == before
    assert canon_mod.research_path(work_root, "ru").read_bytes() == ru_before


def test_research_build_and_accept_via_cli(work_root):
    entries = [make_canon_entry(i, i, locales={"de": ("X", "proposed")}) for i in ("t-a", "t-b", "t-c")]
    setup_research_workspace(work_root, entries)

    code, payload = run_cli(["build", "--root", str(work_root), "--kind", "research", "--locale", "de",
                             "--entry", "t-a", "--entry", "t-b"])
    assert code == 0, payload
    run_dir = Path(payload["batches"][0]["dir"])
    assert sorted(i["id"] for i in packet_of(payload["batches"][0])["items"]) == ["t-a", "t-b"]
    out = write_output(run_dir, {"terms": {"t-a": research_term(), "t-b": research_term()}})
    code, payload = run_cli(["accept", "--root", str(work_root), "--run", str(run_dir), "--output", str(out)])
    assert code == 0, payload
    assert payload["stored"] == ["t-a", "t-b"]

    code, payload = run_cli(["build", "--root", str(work_root), "--kind", "research_verify", "--locale", "de"])
    assert code == 0, payload
    run_dir = Path(payload["batches"][0]["dir"])
    items = packet_of(payload["batches"][0])["items"]
    out = write_output(run_dir, {"verdicts": {i["id"]: verdict_for(i) for i in items}})
    code, payload = run_cli(["accept", "--root", str(work_root), "--run", str(run_dir), "--output", str(out)])
    assert code == 0, payload
    assert payload["verified"] == ["t-a", "t-b"]


def audit_workspace(work_root):
    msgs = make_messages([make_message("a", "Cart", targets={"de": "Warenkorb"}),
                          make_message("b", "Other", targets={"de": "Andere"})])
    setup_workspace(work_root, make_cfg(), msgs, {"de": {"a": make_entry("translated"), "b": make_entry("translated")}})
    request = {"entry": "t-cart", "kind": "term", "source": "Cart", "locale": "de", "before": "Warenkorb",
               "after": "Einkaufswagen", "reason": "consistency", "by": "alice", "at": "2026-01-01T00:00:00Z",
               "occurrences": ["a"]}
    lz_common.atomic_write_json(work_root / "runs" / "de" / "canon-audit-t-cart.json", request)


def test_audit_entry_flag_cardinality_via_cli(work_root):
    audit_workspace(work_root)
    base = ["build", "--root", str(work_root), "--kind", "audit", "--locale", "de"]

    code, payload = run_cli(base)
    assert code == 0, payload
    assert [i["id"] for i in packet_of(payload["batches"][0])["items"]] == ["a", "b"]
    assert packet_of(payload["batches"][0])["canon_entry"] is None

    code, payload = run_cli(base + ["--entry", "t-cart"])
    assert code == 0, payload
    assert [i["id"] for i in packet_of(payload["batches"][0])["items"]] == ["a"]
    assert packet_of(payload["batches"][0])["canon_entry"]["entry"] == "t-cart"

    code, payload = run_cli(base + ["--entry", "t-cart", "--entry", "t-other"])
    assert code == lz_common.EXIT_CANNOT and payload["ok"] is False


def test_translate_still_refuses_entry_via_cli(work_root):
    setup_workspace(work_root, make_cfg(), make_messages([make_message("a", "Hello")]),
                    {"de": {"a": make_entry("pending")}})
    code, payload = run_cli(["build", "--root", str(work_root), "--kind", "translate", "--locale", "de",
                             "--entry", "t-a"])
    assert code == lz_common.EXIT_CANNOT and payload["ok"] is False


def test_research_flow_reaches_approval_summary_and_report(work_root):
    entries = [make_canon_entry(i, i, locales={"de": (i.upper(), "proposed")}) for i in ("t-weak", "t-clean")]
    setup_research_workspace(work_root, entries)
    research_accept(work_root, build_research_batch(work_root, ["t-weak", "t-clean"]), {"terms": {
        "t-weak": research_term(recommendation="Schwach", confidence="low", note="thin evidence"),
        "t-clean": research_term(recommendation="Sauber"),
    }})
    verify = build_verify_batch(work_root)
    items = {i["id"]: i for i in packet_of(verify)["items"]}
    research_accept(work_root, verify, {"verdicts": {
        "t-weak": verdict_for(items["t-weak"], verdict="weak", problems="thin", better="Anders"),
        "t-clean": verdict_for(items["t-clean"]),
    }})

    canon = canon_mod.load(work_root)
    result = canon_mod.approve(work_root, canon, "t-weak", "de", None, "tester")
    canon_mod.save(work_root, canon)

    assert result["research"] == {"recommendation": "Schwach", "confidence": "low", "findings": 1,
                                  "confirmed": 1, "verdict": "weak", "better": "Anders"}
    event = canon_mod.load_history(work_root)["events"][-1]
    assert event["entry"] == "t-weak" and event["research"] == result["research"]

    report = report_mod.build_report(work_root, "de")
    section = report.split("## Canon usage research", 1)[1]
    assert "researched: 2, verified: 2, flagged for a native speaker: 1" in section
    assert "`t-weak`" in section and "verdict weak; low confidence" in section
    assert "better: Anders" in section and "T-WEAK (approved)" in section
    assert "`t-clean`" not in section
