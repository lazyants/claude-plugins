"""tests/render_connective_candidates.test.py -- #926: a canon label that is
one entity's target with a leading connective glued onto ANOTHER emitted
note's label (`from Kremenchug` beside `Kremenchug`) ships as two vault
notes and nothing counts it. This file pins `_build_connective_candidates`
and its manifest key, `connective_candidates` (`CONNECTIVE_CANDIDATES_KEY`),
which the renderer computes on EVERY obsidian render and reports -- never
folds, never halts, never recommends a canon command.

Self-contained (mirrors tests/render_obsidian_link_groups.test.py's and
tests/entity_markup_render.test.py's conventions: no cross-test-file
imports, module loaded via importlib.util.spec_from_file_location).

## What is pinned here, and why

- The report is CLASSICAL string matching (strip a leading connective,
  NFC-compare the remainder), never an identity claim -- it never says two
  labels name one referent, only that they differ by a leading connective.
  Folding, if any, is the operator's or #823's LLM harmonisation pass's
  call, made downstream of this render (canon-and-glossary.md, #871's "one
  note per canon entry" paragraph, cited not changed).
- The universe of "emitted note labels" is exactly two things: canon
  TARGETS reachable through `_owners_by_target` (which already drops blank
  `canonical_target_form` -- a blank target is not a note identity, and a
  test below pins that as an accepted tradeoff, not an oversight) and
  markup identities `_markup_note_records` actually minted. A canon target
  that collision de-linking removed from `target_to_entity`, or one owned
  only by a `sense_translated` entry, is still a row (the LABEL was still
  emitted) but is reported as NOT currently linkable
  (`reduces_to_linkable: false`) -- that flag is what saves an operator
  from re-deriving link status by hand.
- Compatibility is the SAME both-directions rule `_category_compatible`
  already uses for canon/markup composition, extended to canon/canon: a
  blank/absent category or tag is a wildcard, two non-blank values must
  agree. A category contradiction is 0 rows, never a merged row -- the
  #837 lesson (don't let a category-blind match assert an identity this
  plugin refuses to assert elsewhere).
- The lookup for a remainder prefers a COMPATIBLE canon target over a
  markup identity, but an existing, category-INCOMPATIBLE canon target
  must not swallow a genuinely matching markup identity underneath it
  (round-2 finding, test 12f below).
"""
from __future__ import annotations

import importlib.util
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"

assert RENDER_OBSIDIAN_SRC.is_file(), f"render_obsidian.py not found at {RENDER_OBSIDIAN_SRC}"


def _load_render_obsidian_module():
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_connective_candidates_under_test", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()


# ---------------------------------------------------------------------------
# Fixture builders (mirrors render_obsidian_link_groups.test.py /
# entity_markup_render.test.py)
# ---------------------------------------------------------------------------

FOLDERS = {"person": "People", "place": "Places"}


def make_node(node_id, seg, text, kind="prose", order_index=0, verses=None, fnrefs=None):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": "PARA",
        "order_index": order_index, "medium": "plain", "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }


def make_nodestream(nodes, footnotes=None, spans=None, target="en"):
    """`spans=None` omits the `entity_markup` key entirely (the "index_from:
    markup" knob inactive -- most cases here). `spans={...}` writes it, one
    span per key, mirroring assemble.py's own emitted shape."""
    ns = {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
    }
    if spans is not None:
        ns["entity_markup"] = {"spans": spans}
    return ns


def ent(n, payload):
    """The exact sentinel assemble.py emits in `index` mode."""
    return f"⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧"


def span(tag, payload, ref=None):
    record = {"tag": tag, "payload": payload}
    if ref is not None:
        record["ref"] = ref
    return record


def canon_entry(source_form, canonical_target_form, category="", basis="transliterated",
                is_proper_name=True, confidence="high"):
    return {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
        "category": category,
    }


def make_canon(entries: dict):
    return {"entries": entries, "review_queue": [], "generation_hashes": {}}


def make_profile(entity_markup=False, index_from="markup", tags=("person", "place"),
                  folders=None, mentions_enabled=False):
    """`output.target` is always "obsidian" -- `_build_connective_candidates`
    runs on every real obsidian render, never gated on `entity_markup`."""
    output = {
        "target": "obsidian",
        "name_display": {"parenthetical_originals": "never"},
        "adapter_config": {
            "obsidian": {
                "folders": FOLDERS if folders is None else folders,
                "mentions_section": {"enabled": mentions_enabled},
            },
        },
    }
    if entity_markup:
        block = {"tags": list(tags)}
        if index_from is not None:
            block["index_from"] = index_from
        output["entity_markup"] = block
    return {"target": {"language": {"code": "en"}}, "output": output}


def render_into(tmp_path, nodestream, canon, profile, name="out"):
    out_dir = tmp_path / name
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def one_node(text="Text."):
    return [make_node("n1", "seg01", text)]


# ---------------------------------------------------------------------------
# 1. The minimal reduction: one prefixed target, one bare target, both blank
#    category.
# ---------------------------------------------------------------------------

def test_one_prefixed_target_reduces_to_one_bare_target(tmp_path, capsys):
    entries = {
        "src1": canon_entry("src1", "from X"),
        "src2": canon_entry("src2", "X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    assert result == {
        "rows": [{
            "label": "from X", "kind": "canon", "owners": ["src1"], "categories": [],
            "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
            "reduces_to_linkable": True,
        }],
        "candidate_labels": 1,
        "candidate_notes": 1,
    }

    err = capsys.readouterr().err
    assert "from X" in err and "'X'" in err, err
    assert "1" in err
    assert "one note per canon entry" in err
    assert "downstream" in err
    assert "obsidian.md" in err
    # The bare stem, not the ".py" name: tests/senses_fixture_guard.test.py
    # reads a consumer script's FULL filename beside this file's own
    # spec_from_file_location as an unstaged isolation, and this line is a
    # WARN-wording assertion, not a script reference.
    assert "canon_validate" not in err
    assert "--correct" not in err
    assert "canon_link_groups.json" not in err


# ---------------------------------------------------------------------------
# 2. Three different connectives reducing to the same bare target -- sorted
#    by label, grouped in the WARN.
# ---------------------------------------------------------------------------

def test_three_connectives_reduce_to_one_target_sorted_by_label(tmp_path, capsys):
    entries = {
        "s_in": canon_entry("s_in", "in X"),
        "s_to": canon_entry("s_to", "to X"),
        "s_andto": canon_entry("s_andto", "and to X"),
        "s_bare": canon_entry("s_bare", "X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    assert result["candidate_labels"] == 3
    assert result["candidate_notes"] == 3
    labels = [row["label"] for row in result["rows"]]
    assert labels == ["and to X", "in X", "to X"], labels
    assert all(row["reduces_to"] == "X" for row in result["rows"])

    err = capsys.readouterr().err
    assert "'X' <-" in err, err
    assert "'and to X'" in err and "'in X'" in err and "'to X'" in err


# ---------------------------------------------------------------------------
# 3. Several owners of one prefixed target collapse into ONE row.
# ---------------------------------------------------------------------------

def test_several_owners_of_the_prefixed_target_are_one_row(tmp_path):
    entries = {
        "a": canon_entry("a", "from X"),
        "b": canon_entry("b", "from X"),
        "c": canon_entry("c", "from X"),
        "bare": canon_entry("bare", "X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    assert result["candidate_labels"] == 1
    assert result["candidate_notes"] == 3
    assert result["rows"] == [{
        "label": "from X", "kind": "canon", "owners": ["a", "b", "c"], "categories": [],
        "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
        "reduces_to_linkable": True,
    }]


# ---------------------------------------------------------------------------
# 4/5. `reduces_to_linkable` is False when the remainder target is not
#      CURRENTLY linkable -- collision de-linked, or sense_translated-only.
# ---------------------------------------------------------------------------

def test_collision_delinked_remainder_is_not_linkable(tmp_path):
    entries = {
        "x1": canon_entry("x1", "X"),
        "x2": canon_entry("x2", "X"),  # >=2 owners of the bare target -> de-linked
        "prefixed": canon_entry("prefixed", "from X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    assert result["rows"] == [{
        "label": "from X", "kind": "canon", "owners": ["prefixed"], "categories": [],
        "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
        "reduces_to_linkable": False,
    }]


def test_sense_translated_only_remainder_is_not_linkable(tmp_path):
    entries = {
        "bare": canon_entry("bare", "X", basis="sense_translated"),
        "prefixed": canon_entry("prefixed", "from X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    assert result["rows"] == [{
        "label": "from X", "kind": "canon", "owners": ["prefixed"], "categories": [],
        "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
        "reduces_to_linkable": False,
    }]


# ---------------------------------------------------------------------------
# 6. Category contradiction refuses the row; a blank category composes.
# ---------------------------------------------------------------------------

def test_category_contradiction_between_canon_targets_is_zero_rows(tmp_path):
    entries = {
        "prefixed": canon_entry("prefixed", "of Orleans", category="person"),
        "bare": canon_entry("bare", "Orleans", category="place"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []
    assert result["candidate_labels"] == 0
    assert result["candidate_notes"] == 0


def test_blank_category_on_either_side_composes(tmp_path):
    entries = {
        "prefixed": canon_entry("prefixed", "of Orleans", category="person"),
        "bare": canon_entry("bare", "Orleans", category=""),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "of Orleans", "kind": "canon", "owners": ["prefixed"],
        "categories": ["person"], "connective": "of", "reduces_to": "Orleans",
        "reduces_to_kind": "canon", "reduces_to_linkable": True,
    }]


# ---------------------------------------------------------------------------
# 7. No connective anywhere -- key present, both counts zero, no WARN.
# ---------------------------------------------------------------------------

def test_no_connective_anywhere_is_zero_and_silent(tmp_path, capsys):
    entries = {"s1": canon_entry("s1", "Plainville")}
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result == {"rows": [], "candidate_labels": 0, "candidate_notes": 0}
    err = capsys.readouterr().err
    assert "connective" not in err.lower(), err


# ---------------------------------------------------------------------------
# 8. A prefixed target with no bare remainder note -- zero rows.
# ---------------------------------------------------------------------------

def test_prefixed_target_with_no_bare_remainder_is_zero_rows(tmp_path):
    entries = {"s1": canon_entry("s1", "from X")}
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


# ---------------------------------------------------------------------------
# 9. Near-miss spellings never match: capitalisation, an unlisted
#    connective, extra whitespace, a connective with no remainder.
# ---------------------------------------------------------------------------

def test_near_miss_connective_spellings_never_match(tmp_path):
    entries = {
        "bare": canon_entry("bare", "X"),
        "capitalized": canon_entry("capitalized", "From X"),
        "unlisted": canon_entry("unlisted", "into X"),
        "double_space": canon_entry("double_space", "from  X"),
        "bare_connective": canon_entry("bare_connective", "from"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


def test_split_leading_connective_unit_cases():
    split = render_obsidian._split_leading_connective
    assert split("and to X") == ("and to", "X")
    assert split("to X") == ("to", "X")
    assert split("In X") is None  # capitalized connective is not a connective
    assert split("from") is None  # no remainder at all
    assert split("from  X") is None  # two spaces -- not exactly one
    assert split("of  ") is None  # blank remainder after the connective


# ---------------------------------------------------------------------------
# 10. NFD-stored bare target still matches an NFC-typed prefixed label; the
#     reported reduces_to is the NFC form.
# ---------------------------------------------------------------------------

def test_nfd_stored_bare_target_matches_nfc_prefixed_label(tmp_path):
    nfc_name = "Zlotšov"  # š precomposed
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    entries = {
        "bare": canon_entry("bare", nfd_name),
        "prefixed": canon_entry("prefixed", f"from {nfc_name}"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert len(result["rows"]) == 1
    assert result["rows"][0]["reduces_to"] == nfc_name


# ---------------------------------------------------------------------------
# 11. A blank-target entry is not compared -- the universe is emitted
#     TARGETS, never bare source forms (pinned accepted tradeoff).
# ---------------------------------------------------------------------------

def test_blank_target_entry_is_not_in_the_universe(tmp_path):
    entries = {
        "blank": canon_entry("blank", ""),  # source_form "blank", target blank
        "prefixed": canon_entry("prefixed", "from X"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


# ---------------------------------------------------------------------------
# 12. Markup identities join the same universe as canon targets.
# ---------------------------------------------------------------------------

def test_markup_label_reduces_to_a_compatible_canon_target(tmp_path):
    entries = {"bare": canon_entry("bare", "X", category="")}
    spans = {"1": span("place", "in X")}
    ns = make_nodestream([make_node("n1", "seg01", ent(1, "in X") + ".")], spans=spans)
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "in X", "kind": "markup", "tag": "place", "connective": "in",
        "reduces_to": "X", "reduces_to_kind": "canon", "reduces_to_linkable": True,
    }]


def test_markup_label_does_not_reduce_to_a_category_incompatible_canon_target(tmp_path):
    entries = {"bare": canon_entry("bare", "X", category="person")}
    spans = {"1": span("place", "in X")}
    ns = make_nodestream([make_node("n1", "seg01", ent(1, "in X") + ".")], spans=spans)
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


def test_markup_label_reduces_to_a_bare_markup_identity(tmp_path):
    spans = {"1": span("place", "in Y"), "2": span("place", "Y")}
    ns = make_nodestream(
        [make_node("n1", "seg01", ent(1, "in Y") + " and " + ent(2, "Y") + ".")], spans=spans
    )
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon({}), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "in Y", "kind": "markup", "tag": "place", "connective": "in",
        "reduces_to": "Y", "reduces_to_kind": "markup",
    }]


def test_markup_label_does_not_reduce_across_a_tag_mismatch(tmp_path):
    spans = {"1": span("person", "in Y"), "2": span("place", "Y")}
    ns = make_nodestream(
        [make_node("n1", "seg01", ent(1, "in Y") + " and " + ent(2, "Y") + ".")], spans=spans
    )
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon({}), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


def test_canon_label_reduces_to_a_markup_identity(tmp_path):
    entries = {"prefixed": canon_entry("prefixed", "from Z", category="")}
    spans = {"1": span("place", "Z")}
    ns = make_nodestream([make_node("n1", "seg01", ent(1, "Z") + ".")], spans=spans)
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "from Z", "kind": "canon", "owners": ["prefixed"], "categories": [],
        "connective": "from", "reduces_to": "Z", "reduces_to_kind": "markup",
    }]
    assert result["candidate_labels"] == 1
    assert result["candidate_notes"] == 1  # one owner of the canon row


def test_incompatible_canon_target_does_not_swallow_a_compatible_markup_match(tmp_path):
    """Round-2 finding: "canon first, then markup" must not drop a
    legitimate markup/markup row just because a category-incompatible
    canon target of the same bare label also exists."""
    entries = {"jordan_person": canon_entry("jordan_person", "Jordan", category="person")}
    spans = {"1": span("place", "in Jordan"), "2": span("place", "Jordan")}
    ns = make_nodestream(
        [make_node("n1", "seg01", ent(1, "in Jordan") + " and " + ent(2, "Jordan") + ".")],
        spans=spans,
    )
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "in Jordan", "kind": "markup", "tag": "place", "connective": "in",
        "reduces_to": "Jordan", "reduces_to_kind": "markup",
    }]


# ---------------------------------------------------------------------------
# 13. Sort order is (reduces_to, label), independent of insertion order; the
#     key is present with entity markup entirely inactive.
# ---------------------------------------------------------------------------

def test_rows_sorted_by_reduces_to_then_label_with_entity_markup_inactive(tmp_path):
    entries = {
        "bet_from": canon_entry("bet_from", "from Bet"),
        "bet_bare": canon_entry("bet_bare", "Bet"),
        "alef_from": canon_entry("alef_from", "from Alef"),
        "alef_bare": canon_entry("alef_bare", "Alef"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=False)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    reduces_to_order = [row["reduces_to"] for row in result["rows"]]
    assert reduces_to_order == ["Alef", "Bet"], reduces_to_order


# ---------------------------------------------------------------------------
# Round-1 code review additions: a blank category is a wildcard on EITHER
# side, and compatibility is checked PAIRWISE across every owner, not just
# the first one on either side.
# ---------------------------------------------------------------------------

def test_blank_category_on_the_rows_own_side_composes(tmp_path):
    """The existing blank-category test only blanks the REMAINDER side
    (`Orleans`). Here it is the candidate row's OWN owner that is blank,
    beside a non-blank remainder -- the wildcard must work symmetrically."""
    entries = {
        "prefixed": canon_entry("prefixed", "from X", category=""),
        "bare": canon_entry("bare", "X", category="place"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "from X", "kind": "canon", "owners": ["prefixed"], "categories": [],
        "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
        "reduces_to_linkable": True,
    }]


def test_one_incompatible_owner_among_several_on_the_rows_side_refuses_the_row(tmp_path):
    """Two owners of the SAME prefixed target, `place` and `person`: even
    though one of them (`place`) would be compatible with the remainder
    alone, the OTHER (`person`) is not -- compatibility is pairwise across
    every owner, never decided by the first one checked."""
    entries = {
        "p1": canon_entry("p1", "from X", category="place"),
        "p2": canon_entry("p2", "from X", category="person"),
        "bare": canon_entry("bare", "X", category="place"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == []


def test_one_blank_owner_among_several_on_the_remainders_side_still_composes(tmp_path):
    """The mirror of the case above, on the REMAINDER's owner list: `X` is
    owned by a `place` entry and a blank-category entry. Neither is
    incompatible with the row's own `place` category (blank is a wildcard),
    so the row stands -- pairwise means every pair must agree, not that any
    blank anywhere poisons the match.

    `X` has two owners here, so collision de-linking removes it from
    `target_to_entity` regardless of category -- the row is still reported
    (the LABEL was still emitted), just not currently linkable."""
    entries = {
        "prefixed": canon_entry("prefixed", "from X", category="place"),
        "bare_place": canon_entry("bare_place", "X", category="place"),
        "bare_blank": canon_entry("bare_blank", "X", category=""),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "from X", "kind": "canon", "owners": ["prefixed"], "categories": ["place"],
        "connective": "from", "reduces_to": "X", "reduces_to_kind": "canon",
        "reduces_to_linkable": False,
    }]


def test_canon_row_reduces_to_a_markup_identity_past_an_incompatible_canon_target(tmp_path):
    """Mirrors the markup/markup Jordan case (test 12f above) on the OTHER
    side of the lookup: a CANON row (`from Jordan`) must not be blocked by
    a same-spelled, category-incompatible canon target (`Jordan`/person)
    when a compatible MARKUP identity at that remainder exists too
    (`<place>Jordan</place>`, minted because the incompatible canon entry
    refused to compose with it)."""
    entries = {
        "prefixed": canon_entry("prefixed", "from Jordan", category="place"),
        "jordan_person": canon_entry("jordan_person", "Jordan", category="person"),
    }
    spans = {"1": span("place", "Jordan")}
    ns = make_nodestream([make_node("n1", "seg01", ent(1, "Jordan") + ".")], spans=spans)
    out_dir, manifest = render_into(
        tmp_path, ns, make_canon(entries), make_profile(entity_markup=True)
    )
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]
    assert result["rows"] == [{
        "label": "from Jordan", "kind": "canon", "owners": ["prefixed"],
        "categories": ["place"], "connective": "from", "reduces_to": "Jordan",
        "reduces_to_kind": "markup",
    }]
    assert result["candidate_labels"] == 1


# ---------------------------------------------------------------------------
# Round-1 code review addition: the WARN preview caps at three groups even
# when four distinct `reduces_to` targets have rows, and the two counts
# reflect an UNEQUAL owners-per-label distribution correctly.
# ---------------------------------------------------------------------------

def test_warn_preview_shows_only_the_first_three_groups_of_four(tmp_path, capsys):
    entries = {
        "and_to_a": canon_entry("and_to_a", "and to A"),
        "from_a_1": canon_entry("from_a_1", "from A"),
        "from_a_2": canon_entry("from_a_2", "from A"),
        "in_a": canon_entry("in_a", "in A"),
        "bare_a": canon_entry("bare_a", "A"),
        "from_b": canon_entry("from_b", "from B"),
        "bare_b": canon_entry("bare_b", "B"),
        "from_c": canon_entry("from_c", "from C"),
        "bare_c": canon_entry("bare_c", "C"),
        "from_d": canon_entry("from_d", "from D"),
        "bare_d": canon_entry("bare_d", "D"),
    }
    ns = make_nodestream(one_node())
    out_dir, manifest = render_into(tmp_path, ns, make_canon(entries), make_profile())
    result = manifest[render_obsidian.CONNECTIVE_CANDIDATES_KEY]

    # 6 rows (and to A, from A, in A, from B, from C, from D); "from A" alone
    # carries 2 owners, so notes (7) outnumber labels (6) by exactly one.
    assert result["candidate_labels"] == 6
    assert result["candidate_notes"] == 7

    err = capsys.readouterr().err
    assert "6 label(s) (7 note(s))" in err, err
    assert "'A' <- 'and to A', 'from A', 'in A'" in err, err
    assert "'B' <- 'from B'" in err, err
    assert "'C' <- 'from C'" in err, err
    assert "'D'" not in err, err
    assert "from D" not in err, err
