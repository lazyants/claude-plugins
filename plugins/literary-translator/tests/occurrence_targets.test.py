"""Tests for scripts/occurrence_targets.py -- the source-anchored occurrence-
eligibility engine behind the Obsidian adapter's opt-in `## Mentions`
appendix section (RFC appendix-backlink-integrity, 1.8.0).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime, sibling of
``bootstrap_names.py``/``canon_senses.py``/``render_obsidian.py``, all three
of which it imports at its own top level), so it is loaded here via
importlib from its real path, with ``SCRIPTS_DIR`` temporarily on
``sys.path`` so its own ``from bootstrap_names import
extract_candidate_spans``/``from canon_senses import ...``/``from
render_obsidian import _verse_texts`` resolve -- mirrors
``tests/occ_index.test.py``'s own loader.

Every occurrence in these fixtures uses plain (no-particle, no-elision)
``LanguageConfig`` unless a test is specifically about the elision-config
dependency -- ``make_lang`` mirrors ``tests/occ_index.test.py``'s own copy so
fixtures stay comparable across both suites (see that file's own comment on
why this small helper is duplicated rather than shared).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
OCCURRENCE_TARGETS_SCRIPT = SCRIPTS_DIR / "occurrence_targets.py"

assert OCCURRENCE_TARGETS_SCRIPT.is_file(), f"occurrence_targets.py not found at {OCCURRENCE_TARGETS_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level ``from occ_index import ...``/``from canon_senses import
    ...``/``from render_obsidian import ...`` resolve exactly like they
    would under a real in-process ``assemble.py`` run."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


ot = _load_module("occurrence_targets_under_test", OCCURRENCE_TARGETS_SCRIPT, SCRIPTS_DIR)

# ot's own `from render_obsidian import _verse_texts` bound this name as an
# attribute of ot itself -- reusing it here (rather than a second,
# independent import) guarantees the parity test below compares against the
# EXACT function object occurrence_targets.py itself calls, never a
# coincidentally-identical second copy.
_verse_texts = ot._verse_texts

import canon_senses as _canon_senses_module  # noqa: E402 -- only to construct SensesResult fixtures below

SensesResult = _canon_senses_module.SensesResult


# ---------------------------------------------------------------------------
# LanguageConfig fixture builder (mirrors tests/occ_index.test.py exactly)
# ---------------------------------------------------------------------------

def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    import re
    from bootstrap_names import LanguageConfig
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return LanguageConfig(
        path=Path("<test-fixture>"),
        particles=frozenset(p.lower() for p in particles),
        stopwords=frozenset(stopwords),
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=b"{}",
        name_inventory=frozenset(name_inventory),
    )


FR_ELISION_PATTERN = r"^([dl])['’]([A-ZÀÂÄÆÇÉÈÊËÎÏÔŒÖÙÛÜŸ].*)$"

PLAIN_LANG = make_lang()


# ---------------------------------------------------------------------------
# Fixture builders -- manifest / nodestream / canon
# ---------------------------------------------------------------------------

def make_block(plain_text, seg=None):
    return {"plain_text": plain_text, "seg": seg, "order_index": 0, "type": "PARA"}


def make_manifest(blocks=None, verse_store=None, footnotes=None):
    return {
        "blocks": blocks or {},
        "verse": {"store": verse_store or []},
        "footnotes": footnotes or [],
    }


def make_node(block_id, seg, raw_type="PARA", kind="prose", verses=None):
    return {
        "id": block_id,
        "seg": seg,
        "kind": kind,
        "raw_type": raw_type,
        "order_index": 0,
        "medium": "plain",
        "text": "",
        "fnrefs": [],
        "verses": verses or [],
    }


def make_claim(vid, rendered="", literal_gloss="", placeholder=None):
    return {
        "vid": vid,
        "placeholder": placeholder or f"⟦VERSE_{vid}_xx⟧",
        "content": {"rendered": rendered, "literal_gloss": literal_gloss},
    }


def make_nodestream(nodes=None, footnotes=None):
    return {
        "book": {"seg_order": [], "title": None},
        "nodes": nodes or [],
        "footnotes": footnotes or [],
        "meta": {},
    }


def make_canon(entries):
    return {"entries": entries}


def make_entry(canonical_target_form="Target", basis="established", is_proper_name=True):
    return {
        "canonical_target_form": canonical_target_form,
        "is_proper_name": is_proper_name,
        "basis": basis,
    }


EMPTY_SENSES = SensesResult(is_empty=True, entries_by_source_form={})


def split_senses(source_form):
    """A SensesResult where `source_form` has an adjudicated >=2-sense split
    entry (canon_senses.is_split() -> True for it)."""
    return SensesResult(
        is_empty=False,
        entries_by_source_form={
            source_form: {
                "senses": [
                    {"sense_id": "s1", "canonical_target_form": "A"},
                    {"sense_id": "s2", "canonical_target_form": "B"},
                ]
            }
        },
    )


# ---------------------------------------------------------------------------
# entry_is_index_eligible -- direct unit tests
# ---------------------------------------------------------------------------

def test_entry_is_index_eligible_excludes_not_a_name():
    assert ot.entry_is_index_eligible({"basis": "not_a_name", "is_proper_name": True}) is False


def test_entry_is_index_eligible_excludes_is_proper_name_false():
    assert ot.entry_is_index_eligible({"basis": "established", "is_proper_name": False}) is False


def test_entry_is_index_eligible_includes_ordinary_established_entry():
    assert ot.entry_is_index_eligible({"basis": "established", "is_proper_name": True}) is True


def test_entry_is_index_eligible_includes_sense_translated():
    # #138/codex R5 b1: sense_translated stays INDEX-eligible even though
    # build_entity_index (the inline linker) separately skips it for its own,
    # unrelated reason (unanchored target-text auto-linking is unsafe for an
    # ordinary-word rendering).
    assert ot.entry_is_index_eligible({"basis": "sense_translated", "is_proper_name": True}) is True


# ---------------------------------------------------------------------------
# verse_renders_nonempty <-> render_obsidian._verse_texts parity (no drift)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [
    None,
    {},
    {"rendered": "", "literal_gloss": ""},
    {"rendered": "   ", "literal_gloss": "  "},
    {"rendered": "Hello", "literal_gloss": ""},
    {"rendered": "", "literal_gloss": "Glossy"},
    {"rendered": "Hello", "literal_gloss": "Glossy"},
])
def test_verse_renders_nonempty_matches_verse_texts_exactly(content):
    assert ot.verse_renders_nonempty(content) == any(_verse_texts(content))


def test_verse_renders_nonempty_true_and_false_cases_both_reachable():
    # Guards against a vacuous parity test (both sides always False/True).
    assert ot.verse_renders_nonempty({"rendered": "Hello", "literal_gloss": ""}) is True
    assert ot.verse_renders_nonempty({"rendered": "", "literal_gloss": ""}) is False


# ---------------------------------------------------------------------------
# #206 narrowed: source-anchoring is IMMUNE to translated-target divergence,
# because this module never reads translated text at all.
# ---------------------------------------------------------------------------

def test_206_divergent_target_surfaces_both_captured():
    manifest = make_manifest(blocks={
        "b1": make_block("Ivan marchait.", seg="seg01"),
        "b2": make_block("Ivan revint.", seg="seg02"),
    })
    nodestream = make_nodestream(nodes=[
        make_node("b1", "seg01"),
        make_node("b2", "seg02"),
    ])
    # canonical_target_form is deliberately irrelevant to this module -- a
    # different TRANSLATED rendering per occurrence (the #206 failure mode)
    # would not change anything here, since build() never inspects it.
    canon = make_canon({"Ivan": make_entry(canonical_target_form="Johnny")})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    records = result["eligible_by_source_form"]["Ivan"]
    assert len(records) == 2
    assert {r["seg"] for r in records} == {"seg01", "seg02"}
    assert {r["source_block"] for r in records} == {"b1", "b2"}
    assert all(r["origin"] == "block" for r in records)


def test_documented_residual_different_matcher_name_not_captured():
    # "Ivan" is a genuine in-bounds substring of "Ivanovich" but the
    # production matcher never emits it as a separate completed run --
    # occurrence_targets must not (and cannot) recover this without a source-
    # side inflection/agglutination folding pass (explicitly out of scope,
    # see the plan's "Out of scope" section).
    manifest = make_manifest(blocks={"b1": make_block("Ivanovich marchait.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]


# ---------------------------------------------------------------------------
# Paired language-config mutation (codex R2 b1) -- a one-byte particle_config
# change flips the spans production_occurrences returns.
# ---------------------------------------------------------------------------

def test_paired_language_config_mutation_flips_capture():
    manifest = make_manifest(blocks={"b1": make_block("d'Effiat arriva.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Effiat": make_entry()})

    with_elision = make_lang(elision_pattern=FR_ELISION_PATTERN)
    result_with = ot.build(manifest, canon, EMPTY_SENSES, with_elision, nodestream)
    assert len(result_with["eligible_by_source_form"]["Effiat"]) == 1

    without_elision = make_lang(elision_pattern=None, has_elision=False)
    result_without = ot.build(manifest, canon, EMPTY_SENSES, without_elision, nodestream)
    assert "Effiat" not in result_without["eligible_by_source_form"]


# ---------------------------------------------------------------------------
# Distinct source forms sharing a target -> two independent entities.
# ---------------------------------------------------------------------------

def test_distinct_source_forms_sharing_a_target_are_independent():
    manifest = make_manifest(blocks={
        "b1": make_block("Piotr arriva.", seg="seg01"),
        "b2": make_block("Pavel arriva.", seg="seg02"),
    })
    nodestream = make_nodestream(nodes=[
        make_node("b1", "seg01"),
        make_node("b2", "seg02"),
    ])
    canon = make_canon({
        "Piotr": make_entry(canonical_target_form="Ivan"),
        "Pavel": make_entry(canonical_target_form="Ivan"),  # SAME target -- irrelevant here
    })

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert set(result["eligible_by_source_form"]) == {"Piotr", "Pavel"}
    assert len(result["eligible_by_source_form"]["Piotr"]) == 1
    assert len(result["eligible_by_source_form"]["Pavel"]) == 1


# ---------------------------------------------------------------------------
# omit / regenerate / skip carriers excluded (named mutations)
# ---------------------------------------------------------------------------

def test_omit_carrier_block_absent_from_nodestream_excluded():
    # Mutation: "treat block absence as True -> a frontback-omit block's
    # source text leaks a phantom mention."
    manifest = make_manifest(blocks={"b1": make_block("Ivan omitted.", seg="seg01")})
    nodestream = make_nodestream(nodes=[])  # no node at all -- frontback omit
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]


def test_regenerate_placeholder_carrier_excluded():
    # Mutation: "ignore raw_type -> a regenerate placeholder's REAL manifest
    # source text (never actually rendered) leaks a phantom mention."
    manifest = make_manifest(blocks={"b1": make_block("Ivan regenerated.", seg="FRONTBACK:x")})
    nodestream = make_nodestream(nodes=[
        make_node("b1", "FRONTBACK:x", raw_type="FRONTBACK_REGENERATE_PLACEHOLDER"),
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]


def test_skip_mode_standalone_verse_ineligible_ordinary_kind():
    manifest = make_manifest(
        blocks={"bVerse": make_block("Ivan spoke.", seg="seg03")},
        verse_store=[{"vid": "V1", "mount": "block", "parent_block": "bVerse", "plain_text": "Ivan spoke."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bVerse", "seg03", raw_type="VERSE", kind="verse",
                  verses=[make_claim("V1", rendered="", literal_gloss="")]),  # skip-mode -- empty
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]


def test_skip_mode_standalone_verse_ineligible_even_as_declared_heading():
    # codex R3 b1: declared-heading precedence (#210) can make a mount:block
    # verse's own block render as a HEADING node -- eligibility must be keyed
    # on the manifest's own verse-mount claim, NEVER on the assembled node's
    # classified `kind`. Mutation: "branch on node['kind'] == 'verse' -> the
    # heading-verse phantom mention returns."
    manifest = make_manifest(
        blocks={"bHeadVerse": make_block("Ivan intoned.", seg="seg04")},
        verse_store=[{"vid": "V2", "mount": "block", "parent_block": "bHeadVerse", "plain_text": "Ivan intoned."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bHeadVerse", "seg04", raw_type="HEAD", kind="heading",
                  verses=[make_claim("V2", rendered="", literal_gloss="")]),  # skip-mode -- empty
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]


def test_non_skip_standalone_verse_eligible_positive_control():
    # Guards against a vacuous "always ineligible for verse blocks" bug --
    # the SAME shape as the two tests above, but with real (non-empty)
    # verse content, must be eligible.
    manifest = make_manifest(
        blocks={"bVerse": make_block("Ivan spoke.", seg="seg03")},
        verse_store=[{"vid": "V3", "mount": "block", "parent_block": "bVerse", "plain_text": "Ivan spoke."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bVerse", "seg03", raw_type="VERSE", kind="verse",
                  verses=[make_claim("V3", rendered="Ivan spoke.", literal_gloss="")]),
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert len(result["eligible_by_source_form"]["Ivan"]) == 1
    assert result["eligible_by_source_form"]["Ivan"][0]["origin"] == "block"


# ---------------------------------------------------------------------------
# Embedded verse -- surfaced at the CARRIER's seg, not double-counted with
# the standalone (block-mount) case.
# ---------------------------------------------------------------------------

def test_embedded_verse_only_surfaced_at_carrier_seg():
    manifest = make_manifest(
        blocks={"bCarrier": make_block("He sang a poem: ⟦VERSE_VE1_xx⟧", seg="seg01")},
        verse_store=[{"vid": "VE1", "mount": "embedded", "parent_block": "bCarrier",
                      "plain_text": "Ivan sang a song."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bCarrier", "seg01",
                  verses=[make_claim("VE1", rendered="Ivan sang a song.", literal_gloss="")]),
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    records = result["eligible_by_source_form"]["Ivan"]
    assert len(records) == 1
    rec = records[0]
    assert rec == {
        "source_form": "Ivan",
        "seg": "seg01",
        "origin": "embedded_verse",
        "source_block": "bCarrier",
        "vid": "VE1",
    }


def test_standalone_verse_counted_once_not_double_scanned_as_embedded():
    # mount:"block" verses must be excluded from the embedded-verse scanner
    # entirely -- occ_index's own block scan already counts them (the same
    # block text IS the verse text here, no sentinel/placeholder involved).
    manifest = make_manifest(
        blocks={"bStandalone": make_block("Ivan speaks.", seg="seg02")},
        verse_store=[{"vid": "VS1", "mount": "block", "parent_block": "bStandalone",
                      "plain_text": "Ivan speaks."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bStandalone", "seg02", kind="verse",
                  verses=[make_claim("VS1", rendered="Ivan speaks.", literal_gloss="")]),
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    records = result["eligible_by_source_form"]["Ivan"]
    assert len(records) == 1
    assert records[0]["origin"] == "block"


def test_multi_verse_carrier_only_nonempty_vid_eligible():
    manifest = make_manifest(
        blocks={"bMulti": make_block("carrier text.", seg="seg07")},
        verse_store=[
            {"vid": "VM1", "mount": "embedded", "parent_block": "bMulti", "plain_text": "Ivan one text."},
            {"vid": "VM2", "mount": "embedded", "parent_block": "bMulti", "plain_text": "Ivan two text."},
        ],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bMulti", "seg07", verses=[
            make_claim("VM1", rendered="Ivan one text.", literal_gloss=""),
            make_claim("VM2", rendered="", literal_gloss=""),  # skip-mode -- empty
        ]),
    ])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    records = result["eligible_by_source_form"]["Ivan"]
    assert len(records) == 1
    assert records[0]["vid"] == "VM1"


def test_non_empty_footnote_embedded_verse_ineligible():
    # An embedded verse whose SOLE carrier is a footnote-definition block is
    # referenced-only -- it is never in any node's verses[] inventory
    # (assemble.py never turns a def_block into an ordinary node), so it
    # renders nowhere regardless of how non-empty its own content is.
    manifest = make_manifest(
        blocks={"bFNDef2": make_block("See below.", seg=None)},
        verse_store=[{"vid": "VF1", "mount": "embedded", "parent_block": "bFNDef2",
                      "plain_text": "Ivan whispered a verse."}],
        footnotes=[{"n": 8, "anchor_block": "bAnchor2", "anchor_seg": "seg06", "def_block": "bFNDef2"}],
    )
    nodestream = make_nodestream(
        nodes=[],  # bFNDef2 never becomes a node
        footnotes=[{"n": 8, "text": "whatever nonempty translated text"}],
    )
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]


# ---------------------------------------------------------------------------
# Footnote origin -- anchor_seg comes from the MANIFEST footnotes[] relation,
# never from the nodestream (which carries only {n, text}, no seg at all).
# ---------------------------------------------------------------------------

def test_footnote_occurrence_anchors_via_manifest_relation():
    manifest = make_manifest(
        blocks={"bFNDef": make_block("Ivan explained further.", seg=None)},
        footnotes=[{"n": 7, "anchor_block": "bAnchor", "anchor_seg": "seg05", "def_block": "bFNDef"}],
    )
    nodestream = make_nodestream(
        nodes=[],  # footnote-def blocks never become ordinary nodes
        footnotes=[{"n": 7, "text": "Ivan explained further (translated)."}],
    )
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    records = result["eligible_by_source_form"]["Ivan"]
    assert len(records) == 1
    assert records[0] == {
        "source_form": "Ivan",
        "seg": "seg05",
        "origin": "footnote",
        "source_block": "bFNDef",
        "footnote_n": 7,
    }


def test_footnote_occurrence_excluded_when_nodestream_text_empty():
    # Mutation: "trust the manifest def_block text alone -> a footnote the
    # renderer itself voided still leaks a phantom mention."
    manifest = make_manifest(
        blocks={"bFNDef": make_block("Ivan explained further.", seg=None)},
        footnotes=[{"n": 7, "anchor_block": "bAnchor", "anchor_seg": "seg05", "def_block": "bFNDef"}],
    )
    nodestream = make_nodestream(nodes=[], footnotes=[{"n": 7, "text": ""}])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]


# ---------------------------------------------------------------------------
# Split forms (canon_senses homonym splits) -> unresolved_homonyms only.
# ---------------------------------------------------------------------------

def test_split_form_goes_to_unresolved_homonyms_not_eligible():
    # Mutation: "ignore canon_senses -> split occurrences leak into
    # eligible_by_source_form (the rendered Mentions section)."
    manifest = make_manifest(blocks={"b1": make_block("Ivan spoke. Ivan left.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, split_senses("Ivan"), PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert result["unresolved_homonyms"]["Ivan"] == {
        "count": 2, "segs": ["seg01", "seg01"], "reason": "is_split",
    }


# ---------------------------------------------------------------------------
# Canon-entry-level exclusions end-to-end (real occurrences still excluded).
# ---------------------------------------------------------------------------

def test_not_a_name_entry_excluded_end_to_end_even_with_real_occurrences():
    manifest = make_manifest(blocks={"b1": make_block("Ivan spoke.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry(basis="not_a_name")})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]


def test_is_proper_name_false_entry_excluded_end_to_end_even_with_real_occurrences():
    manifest = make_manifest(blocks={"b1": make_block("Ivan spoke.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry(is_proper_name=False)})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]


def test_sense_translated_entry_gets_eligible_mentions_records():
    # codex R5 b1: a sense_translated proper name DOES get an eligible
    # Mentions record, even though build_entity_index gives it no inline
    # auto-link. Mutation: "exclude sense_translated -> speaking names lose
    # their authoritative index."
    manifest = make_manifest(blocks={"b1": make_block("Hope endured.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Hope": make_entry(basis="sense_translated", is_proper_name=True)})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert len(result["eligible_by_source_form"]["Hope"]) == 1


# ---------------------------------------------------------------------------
# Zero-occurrence entities produce no key in either dict.
# ---------------------------------------------------------------------------

def test_zero_occurrence_entry_produces_no_key_in_either_dict():
    manifest = make_manifest(blocks={"b1": make_block("Nothing relevant here.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    assert "Ivan" not in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]


# ---------------------------------------------------------------------------
# block_renders_nonempty / build_render_index -- direct unit-level coverage
# (the same predicate build() exercises end-to-end above).
# ---------------------------------------------------------------------------

def test_block_renders_nonempty_false_for_absent_block():
    render_index = ot.build_render_index(make_manifest(), make_nodestream(nodes=[]))
    assert ot.block_renders_nonempty("missing", render_index) is False


def test_block_renders_nonempty_true_for_ordinary_node():
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    render_index = ot.build_render_index(make_manifest(), nodestream)
    assert ot.block_renders_nonempty("b1", render_index) is True


# ---------------------------------------------------------------------------
# Performance regression: the tokenizer runs ONCE per source text, never once
# per (text, source_form) pair -- a large canon must not multiply the number
# of extract_candidate_spans() calls (P1, review-bot finding: 200 blocks x
# 200 forms benchmarked at 40,000 extractions under the O(texts x forms)
# implementation; a real novel x a real canon is far worse, and
# validate_backlinks.py re-runs build() again immediately afterward).
# ---------------------------------------------------------------------------

def test_tokenizer_called_once_per_text_not_once_per_source_form(monkeypatch):
    manifest = make_manifest(blocks={
        "b1": make_block("Piotr and Pavel spoke.", seg="seg01"),
        "b2": make_block("Piotr returned alone.", seg="seg02"),
        "b3": make_block("Nothing relevant here.", seg="seg03"),
    })
    nodestream = make_nodestream(nodes=[
        make_node("b1", "seg01"),
        make_node("b2", "seg02"),
        make_node("b3", "seg03"),
    ])
    # 5 canon entries: an O(texts x forms) implementation would call the
    # tokenizer 3 blocks x 5 forms = 15 times; the fixed implementation must
    # call it exactly once per block (3), regardless of canon size.
    canon = make_canon({name: make_entry() for name in ["Piotr", "Pavel", "Ivan", "Boris", "Anna"]})

    call_count = 0
    real_extract_candidate_spans = ot.extract_candidate_spans

    def counting_extract_candidate_spans(text, language_config):
        nonlocal call_count
        call_count += 1
        return real_extract_candidate_spans(text, language_config)

    monkeypatch.setattr(ot, "extract_candidate_spans", counting_extract_candidate_spans)

    result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)

    assert call_count == 3, (
        f"expected exactly one tokenizer call per distinct rendering-eligible "
        f"source text (3 blocks), got {call_count} -- the tokenizer is being "
        f"re-run once per (text, source_form) pair instead of once per text"
    )
    # Sanity: results are still correct once routed through the patched (but
    # still delegating) wrapper -- the call-count assertion alone could pass
    # vacuously if spans_by_name silently dropped every match.
    assert len(result["eligible_by_source_form"]["Piotr"]) == 2
    assert len(result["eligible_by_source_form"]["Pavel"]) == 1
    assert "Ivan" not in result["eligible_by_source_form"]


# ---------------------------------------------------------------------------
# #238 SCOPE test (session-a §7 test 3) -- the fold must reach the LOOKUP
# KEY, not just the trie descent, and the emitted eligible_by_source_form
# dict key must stay the UNFOLDED canon key (Contract 3).
# ---------------------------------------------------------------------------

def test_238_fold_reaches_lookup_key_record_filed_under_unfolded_canon_key():
    """canon source_form is the space-joined, UNPOINTED spelling; the source
    text spells the SAME name pointed AND maqaf-joined. The #238/#241 fold
    must let the matcher's grouping key and this lookup agree (trie descent
    alone is not enough -- see occurrence_targets.py's own module docstring)
    while the emitted Record stays filed under the literal unfolded canon
    key "משה לייב", never a folded one (a folded key would be invisible to
    every one of B's three canon-derived lookups -- Contract 3)."""
    unfolded_source_form = "משה לייב"
    pointed_maqaf_text = "ראה מֹשֶׁה־לַיִיב אתמול."
    lang = make_lang(name_inventory=[unfolded_source_form])

    manifest = make_manifest(blocks={"b1": make_block(pointed_maqaf_text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({unfolded_source_form: make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)

    assert unfolded_source_form in result["eligible_by_source_form"], (
        f"lookup missed -- got keys {sorted(result['eligible_by_source_form'])} "
        "(if the trie found the match but the lookup key stayed unfolded, "
        "this dict would be empty even though a Record was matcher-found)"
    )
    records = result["eligible_by_source_form"][unfolded_source_form]
    assert len(records) == 1
    assert records[0]["source_form"] == unfolded_source_form  # UNFOLDED, Contract 3
    assert records[0]["seg"] == "seg01"
    # a folded dict key (the mutation Contract 3 forbids) would be some
    # other string -- assert it is genuinely absent, not just that the
    # unfolded key happens to also be present.
    assert set(result["eligible_by_source_form"]) == {unfolded_source_form}


def test_238_scope_mutation_a_documented_an_unfolded_grouping_key_would_miss():
    """Documents mutation (a) from session-a §7 test 3: if _spans_by_name's
    grouping key stayed UNFOLDED while only the trie descent were folded,
    the matcher would still find the maqaf-joined+pointed surface (folded
    descent lets the trie reach it) but the RAW emitted name would group it
    under itself, and an unfolded `.get(unfolded_source_form)` lookup
    against the canon key would miss -- reproduced directly against
    bootstrap_names.py (bypassing occurrence_targets.py's own already-fixed
    lookup) to pin the failure mode the fold in _spans_by_name prevents."""
    from bootstrap_names import extract_candidate_spans
    unfolded_source_form = "משה לייב"
    pointed_maqaf_text = "ראה מֹשֶׁה־לַיִיב אתמול."
    lang = make_lang(name_inventory=[unfolded_source_form])

    spans_by_name_unfolded = {}
    for name, _mid, s, e in extract_candidate_spans(pointed_maqaf_text, lang):
        spans_by_name_unfolded.setdefault(name, []).append((s, e))

    # the trie found the match (emitted under its own raw name)...
    assert "מֹשֶׁה־לַיִיב" in spans_by_name_unfolded
    # ...but an UNFOLDED lookup against the canon key finds nothing.
    assert spans_by_name_unfolded.get(unfolded_source_form, ()) == ()


# ---------------------------------------------------------------------------
# MAJOR 1 (post-merge codex finding) -- #238/#241 fold-key COLLISION: two
# DISTINCT eligible canon.json source_forms that fold to the SAME match key
# (e.g. a pointed entry and a separately-canonized maqaf-joined entry) must
# NOT both retrieve -- and both get credited with -- the SAME physical
# occurrence. Routed to unresolved_homonyms with reason:
# "fold_match_key_collision", never double-filed into eligible_by_source_form.
# ---------------------------------------------------------------------------

def test_major1_fold_key_collision_routes_to_unresolved_homonyms_not_double_filed():
    """Two eligible canon entries, "משה לייב" (space-joined) and "משה־לייב"
    (maqaf-joined), fold to the SAME #238/#241 match key. ONE physical
    occurrence in the source text ("משה־לייב") is what BOTH entries' own
    lookups would retrieve. Neither may appear in eligible_by_source_form;
    both must land in unresolved_homonyms with the collision reason."""
    space_form = "משה לייב"
    maqaf_form = "משה־לייב"
    text = "ראה משה־לייב אתמול."
    lang = make_lang(name_inventory=[space_form])

    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({
        space_form: make_entry(),
        maqaf_form: make_entry(),
    })

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)

    # NOT double-filed (or filed at all) into eligible_by_source_form.
    assert space_form not in result["eligible_by_source_form"]
    assert maqaf_form not in result["eligible_by_source_form"]

    # Both members of the collision group land in unresolved_homonyms with
    # the distinct collision reason (distinguishable from is_split).
    assert result["unresolved_homonyms"][space_form]["reason"] == "fold_match_key_collision"
    assert result["unresolved_homonyms"][maqaf_form]["reason"] == "fold_match_key_collision"
    assert result["unresolved_homonyms"][space_form]["count"] == 1
    assert result["unresolved_homonyms"][maqaf_form]["count"] == 1
    assert result["unresolved_homonyms"][space_form]["segs"] == ["seg01"]
    assert result["unresolved_homonyms"][maqaf_form]["segs"] == ["seg01"]


def test_major1_fold_key_collision_warns_to_stderr(capsys):
    space_form = "משה לייב"
    maqaf_form = "משה־לייב"
    text = "ראה משה־לייב אתמול."
    lang = make_lang(name_inventory=[space_form])

    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({space_form: make_entry(), maqaf_form: make_entry()})

    ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "fold_match_key_collision" in captured.err
    assert space_form in captured.err and maqaf_form in captured.err


def test_major1_no_collision_no_warning_and_normal_eligible_routing():
    """Control: a single, non-colliding canon entry must NOT be swept into
    the collision route -- proves the detection is scoped to genuine
    multi-entry collisions, not vacuously firing on every build()."""
    manifest = make_manifest(blocks={"b1": make_block("Ivan spoke.", seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({"Ivan": make_entry()})

    import io
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        result = ot.build(manifest, canon, EMPTY_SENSES, PLAIN_LANG, nodestream)
    finally:
        sys.stderr = real_stderr

    assert "Ivan" in result["eligible_by_source_form"]
    assert "Ivan" not in result["unresolved_homonyms"]
    assert "WARN" not in buf.getvalue()


def test_major1_collision_takes_precedence_over_is_split():
    """A source_form that is BOTH a fold-key collision member AND is_split
    must route under reason: "fold_match_key_collision", not "is_split" --
    the documented precedence (module docstring + build()'s own comment)."""
    space_form = "משה לייב"
    maqaf_form = "משה־לייב"
    text = "ראה משה־לייב אתמול."
    lang = make_lang(name_inventory=[space_form])

    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({space_form: make_entry(), maqaf_form: make_entry()})

    result = ot.build(manifest, canon, split_senses(space_form), lang, nodestream)
    assert result["unresolved_homonyms"][space_form]["reason"] == "fold_match_key_collision"
