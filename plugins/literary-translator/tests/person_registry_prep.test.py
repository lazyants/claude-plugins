"""tests/person_registry_prep.test.py -- `person_registry.py --prep`, the
deterministic half of W9r (#550).

Every test here drives the SHIPPED script as a subprocess against a real
durable root (see `_registry_fixture.py`), never a hand-shaped stand-in for
what the pipeline emits, because the defects this mode can have are all join
defects: a population the universe forgets, a locator that points at the wrong
container, a number that is a zero where it should be an honest null.

The prep universe is the load-bearing assertion. Canon alone is NOT the cast:
an adjudicated homonym split is deliberately absent from `canon.json`'s
`entries{}` (that is the whole point of the sidecar -- see
`glossary_batch_plan.py`'s split-form exclusion), so a canon-only universe
silently omits exactly the people a genealogy registry exists for. The fixture
carries such a form, and `test_universe_includes_senses_only_form` is the
regression-catcher for it.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _registry_fixture as fx  # noqa: E402

SCRIPT = fx.SCRIPT

assert SCRIPT.is_file(), f"person_registry.py not found at {SCRIPT}"


def _load_module():
    """Load the script in-process for the pure helpers (`spread`,
    `boundary_ok`, `count_surfaces`). The pipeline-driving tests still go
    through the CLI."""
    spec = importlib.util.spec_from_file_location("person_registry_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr = _load_module()


@pytest.fixture()
def root(tmp_path):
    return fx.build_root(tmp_path)


def _prep(root):
    code, payload = fx.run(root, "--prep")
    return code, payload, (
        json.loads((root / "registry" / "registry_input.json").read_text(encoding="utf-8"))
        if (root / "registry" / "registry_input.json").is_file() else None
    )


def _units_by_key(doc):
    return {(u["unit"]["source_form"], u["unit"]["sense_id"]): u for u in doc["units"]}


# ---------------------------------------------------------------------------
# The prep universe
# ---------------------------------------------------------------------------

def test_prep_succeeds_and_covers_three_populations(root):
    code, payload, doc = _prep(root)
    assert code == 0, payload
    assert payload["canon_entry"] == 4      # Jean, Paul, Marie, Tulle
    assert payload["canon_senses"] == 2     # Jean Valjean x2 senses
    assert payload["canon_review_queue"] == 1
    assert payload["units"] == 7
    assert doc["input_sha256"] == payload["input_sha256"]


def test_universe_includes_senses_only_form(root):
    """The regression-catcher for a canon-only universe. `Jean Valjean` is in
    NO canon entry -- only in canon_senses.json -- and must still appear, once
    per sense, with its disambiguator."""
    _, _, doc = _prep(root)
    units = _units_by_key(doc)
    assert ("Jean Valjean", "convict") in units
    assert ("Jean Valjean", "mayor") in units
    assert units[("Jean Valjean", "convict")]["disambiguator"] == "the convict"
    assert units[("Jean Valjean", "convict")]["origin_population"] == "canon_senses"
    # And the bare spelling is NOT also emitted -- the pair key must not collide.
    assert ("Jean Valjean", None) not in units


def test_canon_declaration_excluded_rather_than_dropped(root):
    _, _, doc = _prep(root)
    excluded = {row["source_form"] for row in doc["excluded_by_canon_declaration"]}
    assert excluded == {"Le Livre"}
    assert ("Le Livre", None) not in _units_by_key(doc)


def test_review_queue_rows_coalesce_and_keep_every_note(root):
    """Two queued rows for one form, two different reasons. The schema does not
    require uniqueness there, so both notes must survive into ONE unit --
    collapsing to the first would lose the project's own record of why."""
    _, _, doc = _prep(root)
    unit = _units_by_key(doc)[("Bernard", None)]
    assert "two bearers in the source, unresolved" in unit["note"]
    assert "SOURCE_UNAVAILABLE: no citable form" in unit["note"]
    assert unit["refusal_only"] is True


def test_unattributable_units_report_null_not_zero(root):
    """A zero would read as "not in the book". These units have no occurrence
    path at all, which is a different fact and must say so."""
    _, _, doc = _prep(root)
    units = _units_by_key(doc)
    for key in (("Bernard", None), ("Jean Valjean", "convict")):
        assert units[key]["occurrences"] is None
        assert units[key]["occurrences_reason"]
        assert units[key]["attributable"] is False


def test_canon_entry_occurrences_come_from_the_production_engine(root):
    """`Jean` occurs in both prose blocks and in the footnote definition. The
    count is the engine's record count, never a substring scan of this
    script's own devising."""
    _, _, doc = _prep(root)
    jean = _units_by_key(doc)[("Jean", None)]
    assert jean["attributable"] is True
    assert jean["occurrences"] == len(jean["mentions"]) >= 2
    origins = {m["origin"] for m in jean["mentions"]}
    assert "block" in origins


def test_contexts_carry_origin_aware_locators(root):
    """Every context is locatable, and ALL THREE origins really occur here.

    Asserting only inside the loop would pass on an empty context list and on a
    fixture that happened to produce block origins alone -- and the two origins
    a block-only locator cannot verify are exactly the ones that would vanish.
    """
    _, _, doc = _prep(root)
    jean = _units_by_key(doc)[("Jean", None)]
    contexts = jean["contexts"]
    assert len(contexts) >= 4, contexts
    seen = {}
    for ctx in contexts:
        loc = ctx["locator"]
        assert loc["origin"] in {"block", "embedded_verse", "footnote"}
        if loc["origin"] == "block":
            assert loc["block"]
        elif loc["origin"] == "embedded_verse":
            assert loc["vid"] == "V001"
        else:
            assert loc["footnote_n"] == 1
        assert ctx["text"]
        seen.setdefault(loc["origin"], []).append(ctx["text"])
    assert set(seen) == {"block", "embedded_verse", "footnote"}, seen
    # The verse context is the VERSE's prose, never its parent block's -- the
    # parent carries only the placeholder.
    assert seen["embedded_verse"] == [fx.VERSE_1]
    assert seen["footnote"] == [fx.FOOTNOTE_1]


# ---------------------------------------------------------------------------
# The assembly-currency gate
# ---------------------------------------------------------------------------

def test_missing_nodestream_refuses(root):
    (root / "out" / ".assembled" / "nodestream.json").unlink()
    code, payload, _ = _prep(root)
    assert code == 2
    assert payload["reason"] == "missing_input"


def test_body_segment_absent_from_the_nodestream_refuses(root):
    """A scope change or a partial assembly. The manifest declares seg01 as
    body; an assembly that carries no node for it is not this book."""
    path = root / "out" / ".assembled" / "nodestream.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["nodes"] = []
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload, _ = _prep(root)
    assert code == 2
    assert payload["reason"] == "assembly_incomplete"
    assert "seg01" in payload["error"]


def test_draft_edited_after_assembly_refuses(root):
    """A hand-edit the assembled book never saw. The ledger's
    reviewed_draft_sha1 no longer describes the draft on disk."""
    path = root / "segments" / "seg01.draft.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg01:0001"] = "Something the reviewer never read."
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload, _ = _prep(root)
    assert code == 2
    assert payload["reason"] == "assembly_stale"


def test_senses_evidence_that_no_longer_verifies_refuses(root):
    """`canon_senses.load_senses` validates STRUCTURE only, so the sidecar
    still loads cleanly after the block it cites has moved. The real verifier
    -- evidence_verify.verify_senses -- is what catches it, and --prep must
    actually call it: without that, the one authenticated place in the book a
    senses-only person has is an unchecked assertion."""
    path = root / "manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg01:0002"]["plain_text"] = "Un texte entièrement différent."
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload, _ = _prep(root)
    assert code == 2
    assert payload["reason"] == "senses_evidence_unverified"


def test_input_cap_refuses_rather_than_truncating(root):
    code, payload = fx.run(root, "--prep", "--max-input-chars", "10")
    assert code == 2
    assert payload["reason"] == "input_too_large"
    assert not (root / "registry" / "registry_input.json").exists()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_spread_keeps_first_and_last_never_the_first_n():
    """The first N occurrences of a name all come from one part of the book,
    which is exactly the view under which two men who share a spelling look
    like one person."""
    items = list(range(20))
    kept = pr.spread(items, 4)
    assert len(kept) == 4
    assert kept[0] == 0 and kept[-1] == 19
    assert kept != items[:4]
    assert kept == sorted(kept)
    assert pr.spread(items, 4) == kept          # deterministic
    assert pr.spread([1, 2], 5) == [1, 2]       # under the cap, untouched


def test_boundary_ok_matches_the_shipped_linkers_rule():
    """Byte-identical in behaviour to render_obsidian's own #587 guard: the
    ADJACENT character, isalnum(), never a \\b."""
    text = "Johnson met John, and John's dog."
    assert not pr.boundary_ok(text, 0, 4)                 # "John" inside "Johnson"
    i = text.index("John,")
    assert pr.boundary_ok(text, i, i + 4)                 # followed by a comma
    j = text.index("John's")
    assert pr.boundary_ok(text, j, j + 4)                 # followed by an apostrophe


def _add_frontback_node(root) -> None:
    """A `decision: regenerate` front/back unit, as assemble.py emits it: a node
    with its own `FRONTBACK:{id}` seg, and NO entry in manifest.segments[]."""
    ns_path = root / "out" / ".assembled" / "nodestream.json"
    ns = json.loads(ns_path.read_text(encoding="utf-8"))
    ns["nodes"].append({"id": "PARA:FRONTBACK:preface:0001", "seg": "FRONTBACK:preface",
                        "kind": "prose", "raw_type": "PARA", "level": None,
                        "order_index": 0, "medium": "prose",
                        "text": "A preface, regenerated in the target language.",
                        "fnrefs": [], "verses": []})
    ns_path.write_text(json.dumps(ns, ensure_ascii=False, indent=1), encoding="utf-8")

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frontback"] = [{"id": "preface", "decision": "regenerate",
                              "block_ids": ["PARA:FRONTBACK:preface:0001"]}]
    assert all(entry["seg"] != "FRONTBACK:preface" for entry in manifest["segments"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def test_a_frontback_regenerate_node_is_not_demanded_of_the_ledger(root):
    """`assemble.py` gives a `decision: regenerate` front/back unit its own
    `FRONTBACK:{id}` seg and deliberately never lists it in
    `manifest.segments[]`, so it has no draft and no ledger row. A currency
    check keyed on every seg the NodeStream carries would reject a perfectly
    valid book -- and reject it at the ONE gate the whole pass runs behind.
    """
    _add_frontback_node(root)
    code, payload = fx.run(root, "--prep")
    assert code == 0, payload


def test_the_frontback_exemption_does_not_disable_the_check_it_narrows(root):
    """The other side of the same boundary: with the very same undeclared
    frontback node present, a DECLARED segment's hand-edited draft is still
    caught. Exempting the undeclared seg must not turn the check off."""
    _add_frontback_node(root)
    path = root / "segments" / "seg01.draft.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg01:0001"] = "Something the reviewer never read."
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload = fx.run(root, "--prep")
    assert code == 2
    assert payload["reason"] == "assembly_stale"


def test_two_occurrences_in_one_container_are_two_distinct_contexts(root):
    """`occurrence_targets` emits one location-only record per physical
    occurrence, so two occurrences of a form in one block arrive as two records
    with IDENTICAL locators. Windowing both on the container's first span shows
    the same sentence twice while `contexts_total` says two were shown and no
    truncation flag fires — a second, possibly distinguishing occurrence hidden
    behind a number that claims it was not. That is the merge failure this pass
    exists to prevent, wearing the appearance of full coverage.
    """
    code, payload = fx.run(root, "--prep", "--context-chars", "40")
    assert code == 0, payload
    doc = json.loads((root / "registry" / "registry_input.json").read_text(encoding="utf-8"))
    paul = next(u for u in doc["units"] if u["unit"]["source_form"] == "Paul")

    block_3 = [c for c in paul["contexts"] if c["locator"].get("block") == "PARA:seg01:0003"]
    assert len(block_3) == 2, block_3
    assert block_3[0]["text"] != block_3[1]["text"]
    assert all(c["window_centred_on_match"] for c in block_3)
    # Each window is centred on ITS OWN occurrence, so each shows what stands
    # beside THAT mention: the first opening, the second the other town.
    assert "parla le premier" in block_3[0]["text"]
    assert "autre ville" in block_3[1]["text"]
    assert "parla le premier" not in block_3[1]["text"]
    assert paul["contexts_total"] == len(paul["contexts"])


def test_the_prep_cap_measures_the_bytes_the_model_receives(root):
    """Same rule as the claims cap: the guard and the file must be the same
    serialization, or the guard is about bytes nobody ever reads."""
    assert fx.run(root, "--prep")[0] == 0
    path = root / "registry" / "registry_input.json"
    emitted = len(path.read_bytes())
    doc = json.loads(path.read_text(encoding="utf-8"))
    compact = len(pr.canonical_json_bytes(doc))
    assert compact < emitted

    path.unlink()
    code, payload = fx.run(root, "--prep", "--max-input-chars", str(compact))
    assert code == 2
    assert payload["reason"] == "input_too_large"
    assert f"would be {emitted} bytes" in payload["error"]
    assert not path.exists()


# ---------------------------------------------------------------------------
# #497 -- W9r is the third caller of occurrence_targets.build(), and the one
# that made "credit the primary alone" the right shape: it builds one
# attributable unit per canon form and, when Pass A groups those units as ONE
# person, SUMS their mention lists. Two forms holding the same records would
# count one physical occurrence twice in person_registry.json and PEOPLE.md.
#
# These drive the SHIPPED script, which is unchanged by #497 -- the point is
# that it needs no change, and this is what proves it rather than asserting it.
# ---------------------------------------------------------------------------

# `fold_match_key`'s connector fold is scoped to Hebrew -- a Latin hyphen is
# NOT folded (`fold_match_key("Jean-Luc") == "Jean-Luc"`), so a fold-key
# collision cannot be spelled in this fixture's French. The pair below is the
# real shape: one Hebrew name written with a maqaf and with a space. Reaching
# it needs a `name_inventory`, because an uncased script gives the matcher no
# capitalization to key on -- so `_with_fold_group` repoints the root at a
# language config carrying fr.json's own PARTICLES/STOPWORDS plus that
# inventory, rather than pretending the shipped fr.json would find it.
FOLD_PRIMARY = "\u05de\u05e9\u05d4 \u05dc\u05d9\u05d9\u05d1"      # "Moshe Leib", space-joined
FOLD_SIBLING = "\u05de\u05e9\u05d4\u05be\u05dc\u05d9\u05d9\u05d1"  # the same name, maqaf-joined
FOLD_SENTENCE = f" Puis {FOLD_SIBLING} arriva."
FOLD_LANG_CONFIG = "lt497_fold.json"


def _with_fold_group(root, link_groups):
    """Adds a fold-key colliding canon pair to the fixture root, ONE physical
    source occurrence of it, and (optionally) the `link_groups` projection
    `assemble.py` would have persisted for a `canon_link_groups.json` ruling.

    The single occurrence is spelled with the MAQAF form, which both canon
    entries retrieve through the shared fold key -- that is the whole reason
    the collision exists, and why crediting both would count it twice.
    """
    fr = json.loads((root / "languages" / "fr.json").read_text(encoding="utf-8"))
    fr["name_inventory"] = [FOLD_PRIMARY]
    (root / "languages" / FOLD_LANG_CONFIG).write_text(
        json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "literary-translator" / "profile.yml").write_text(
        f"source:\n  language:\n    particle_config: {FOLD_LANG_CONFIG}\n",
        encoding="utf-8")

    canon_path = root / "canon.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    for form in (FOLD_PRIMARY, FOLD_SIBLING):
        canon["entries"][form] = {
            "source_form": form, "is_proper_name": True,
            "canonical_target_form": "Moshe Leib", "basis": "established",
            "confidence": "high", "category": "person",
        }
    canon_path.write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["blocks"]["PARA:seg01:0001"]["plain_text"] += FOLD_SENTENCE
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    ns_path = root / "out" / ".assembled" / "nodestream.json"
    nodestream = json.loads(ns_path.read_text(encoding="utf-8"))
    if link_groups is not None:
        nodestream["link_groups"] = link_groups
    ns_path.write_text(json.dumps(nodestream, ensure_ascii=False), encoding="utf-8")
    return root


def _prep_in_root(root):
    """Runs the durable root's OWN copy of the script, not the plugin's.

    `bootstrap_names.LANGUAGES_DIR` is `{script_dir}/../languages`, so only
    the root-local copy resolves the project-local language config this pair
    needs (the shipped presets carry no `name_inventory`, and an uncased
    script is unfindable without one). `--plugin-root` is then required for
    the registry schemas, which Step 0a's non-recursive copy pass does not
    bring into the root -- the same `#412` arrangement W9r documents.
    """
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "person_registry.py"), "--prep",
         "--durable-root", str(root),
         "--plugin-root", str(fx.ASSETS.parent)],
        capture_output=True, text=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    doc_path = root / "registry" / "registry_input.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8")) if doc_path.is_file() else None
    return proc.returncode, payload, doc


def test_lt497_a_ruled_fold_group_credits_each_occurrence_to_one_unit(root):
    """The primary's unit is attributable and carries the occurrence; the
    sibling's is not, and carries none. Summing the two -- which is exactly
    what Pass B does once Pass A calls them one person -- yields the physical
    count, not twice it."""
    _with_fold_group(root, {FOLD_PRIMARY: FOLD_PRIMARY, FOLD_SIBLING: FOLD_PRIMARY})
    code, payload, doc = _prep_in_root(root)
    assert code == 0, payload
    units = _units_by_key(doc)

    primary = units[(FOLD_PRIMARY, None)]
    sibling = units[(FOLD_SIBLING, None)]

    assert primary["attributable"] is True
    assert primary["occurrences"] == len(primary["mentions"]) == 1

    assert sibling["attributable"] is False
    assert sibling["occurrences"] is None
    assert "fold_group_credited_to_link_group_primary" in sibling["occurrences_reason"]
    assert sibling["mentions"] == []

    # The invariant Pass B depends on, stated as Pass B computes it.
    attributable = [u for u in (primary, sibling) if u["attributable"]]
    assert sum(len(u["mentions"]) for u in attributable) == 1


def test_lt497_without_a_ruling_the_same_pair_stays_unattributable_on_both_sides(root):
    """The control. Nothing about W9r changed for an unruled collision: both
    units report an honest null and the pre-existing collision reason."""
    _with_fold_group(root, None)
    code, payload, doc = _prep_in_root(root)
    assert code == 0, payload
    units = _units_by_key(doc)

    for form in (FOLD_PRIMARY, FOLD_SIBLING):
        unit = units[(form, None)]
        assert unit["attributable"] is False
        assert unit["occurrences"] is None
        assert "fold_match_key_collision" in unit["occurrences_reason"]
