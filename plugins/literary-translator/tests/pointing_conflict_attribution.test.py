"""Tests for #927 -- a pointed Hebrew name must not absorb an unrelated
word's occurrences.

Two DIFFERENT Hebrew words can share the same #238/#241 fold key (the
vowel points are stripped before that key is built) while their vowel
points, shin/sin dots, or dagesh/rafe marks say they are not the same word
at all (``עֵשָׂו`` "Esav" vs ``עָשׂוּ`` "they did"). Before this issue the
fold key was the ONLY thing every attribution site compared, so a name's
occurrence count silently included every differently-pointed word sharing
its skeleton. This file pins the new pointing-aware layer
(``occ_index.pointing_verdict``/``pointing_conflicts``/``attributable_spans``)
and its four downstream sites: ``occ_index.production_occurrences``/
``index_manifest``, ``evidence_verify._group_production_spans_by_name``,
``occurrence_targets.build()`` (via ``occurrence_targets.attribution_group``),
and ``person_registry.build_contexts``.

Modules under test live outside any Python package (standalone scripts
copied to ``${durable_root}/scripts/`` at runtime), so they are loaded here
via importlib from their real paths, with ``SCRIPTS_DIR`` temporarily on
``sys.path`` so their own top-level ``from bootstrap_names import ...``/
``from occ_index import ...``/``from canon_senses import ...`` resolve --
mirrors ``tests/occ_index.test.py``'s and ``tests/occurrence_targets.
test.py``'s own loaders.

Every Hebrew fixture below spells its combining marks as explicit
``\\uXXXX`` escapes, one constant per mark, with a comment naming the mark
-- letters are written literally. This is so a reviewer can see exactly
which vowel/dot/dagesh differs between two fixture words without pasting an
invisible combining character to check.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
OCC_INDEX_SCRIPT = SCRIPTS_DIR / "occ_index.py"
OCCURRENCE_TARGETS_SCRIPT = SCRIPTS_DIR / "occurrence_targets.py"
EVIDENCE_VERIFY_SCRIPT = SCRIPTS_DIR / "evidence_verify.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"
PERSON_REGISTRY_SCRIPT = SCRIPTS_DIR / "person_registry.py"

for _src in (OCC_INDEX_SCRIPT, OCCURRENCE_TARGETS_SCRIPT, EVIDENCE_VERIFY_SCRIPT,
             BOOTSTRAP_NAMES_SCRIPT, PERSON_REGISTRY_SCRIPT):
    assert _src.is_file(), f"fixture source not found: {_src}"


def _load_module(name: str, path: Path, extra_sys_path: Path = SCRIPTS_DIR):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level ``from ... import ...`` resolves exactly like it would under a
    real in-process invocation."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_927_test", BOOTSTRAP_NAMES_SCRIPT)
occ = _load_module("occ_index_for_927_test", OCC_INDEX_SCRIPT)
ot = _load_module("occurrence_targets_for_927_test", OCCURRENCE_TARGETS_SCRIPT)
ev = _load_module("evidence_verify_for_927_test", EVIDENCE_VERIFY_SCRIPT)

import canon_senses as _canon_senses_module  # noqa: E402 -- cached in sys.modules by the loads above
SensesResult = _canon_senses_module.SensesResult
EMPTY_SENSES = SensesResult(is_empty=True, entries_by_source_form={})


# ---------------------------------------------------------------------------
# Hebrew letters and combining marks, spelled as explicit \uXXXX escapes --
# never a pasted character -- one constant per code point, named for exactly
# what it is.
# ---------------------------------------------------------------------------

AYIN = "ע"
SHIN = "ש"
VAV = "ו"
NUN = "נ"
TAV = "ת"
FINAL_NUN = "ן"
QOF = "ק"
HE = "ה"
LAMED = "ל"
ALEF = "א"
BET = "ב"
GIMEL = "ג"
DALET = "ד"

SHEVA = "\u05b0"
HATAF_SEGOL = "\u05b1"
HATAF_PATAH = "\u05b2"
HATAF_QAMATS = "\u05b3"
HIRIQ = "\u05b4"
TSERE = "\u05b5"
SEGOL = "\u05b6"
PATAH = "\u05b7"
QAMATS = "\u05b8"
HOLAM = "\u05b9"
HOLAM_HASER_FOR_VAV = "\u05ba"
QUBUTS = "\u05bb"
DAGESH = "\u05bc"
METEG = "\u05bd"
RAFE = "\u05bf"
SHIN_DOT = "\u05c1"
SIN_DOT = "\u05c2"
QAMATS_QATAN = "\u05c7"
ETNAHTA = "\u0591"  # a cantillation mark

# The issue's own examples (plan §5 / §2).
ESAV = AYIN + TSERE + SHIN + SIN_DOT + QAMATS + VAV  # עֵשָׂו
THEY_DID_A = AYIN + QAMATS + SHIN + SIN_DOT + VAV + DAGESH  # "they did", qamats+shuruq
THEY_DID_B = AYIN + HATAF_PATAH + SHIN + SIN_DOT + VAV + DAGESH  # "they did", hataf-patah variant
ESAV_UNPOINTED = AYIN + SHIN + VAV

NATHAN = NUN + QAMATS + TAV + QAMATS + FINAL_NUN
GAVE = NUN + QAMATS + TAV + PATAH + FINAL_NUN  # "gave" -- differs only on the tav's vowel

KOHELES = QOF + HOLAM + HE + SEGOL + LAMED + SEGOL + TAV
COMMUNITY_OF = QOF + SHEVA + HE + HIRIQ + LAMED + DAGESH + PATAH + TAV


def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    """Mirrors tests/occurrence_targets.test.py's own copy exactly, so
    fixtures stay comparable across suites."""
    import re
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return bn.LanguageConfig(
        path=Path("<test-fixture>"),
        particles=frozenset(p.lower() for p in particles),
        stopwords=frozenset(stopwords),
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=b"{}",
        name_inventory=frozenset(name_inventory),
    )


# ---------------------------------------------------------------------------
# occurrence_targets.test.py's fixture builders, copied so this file never
# imports another test module.
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


def make_nodestream(nodes=None, footnotes=None, link_groups=None):
    nodestream = {
        "book": {"seg_order": [], "title": None},
        "nodes": nodes or [],
        "footnotes": footnotes or [],
        "meta": {},
    }
    if link_groups is not None:
        nodestream["link_groups"] = link_groups
    return nodestream


def make_canon(entries):
    return {"entries": entries}


def make_entry(canonical_target_form="Target", basis="established", is_proper_name=True):
    return {
        "canonical_target_form": canonical_target_form,
        "is_proper_name": is_proper_name,
        "basis": basis,
    }


# ---------------------------------------------------------------------------
# evidence_verify.test.py's small helpers, copied.
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _whole_block_evidence(block_id, seg, text, char_start, char_end, **overrides):
    evidence = {
        "block": block_id, "seg": seg,
        "char_start": char_start, "char_end": char_end,
        "context_start": 0, "context_end": len(text),
        "sha256": _sha256_hex(text.encode("utf-8")),
    }
    evidence.update(overrides)
    return evidence


def _sense(sense_id, evidence):
    return {"sense_id": sense_id, "disambiguator": "test", "index_scope": "narrative",
            "evidence": evidence}


# ===========================================================================
# occ_index.pointing_verdict() / pointing_conflicts()
# ===========================================================================

@pytest.mark.parametrize("a,b", [
    (ESAV, THEY_DID_A),
    (ESAV, THEY_DID_B),
    (NATHAN, GAVE),
    (KOHELES, COMMUNITY_OF),
])
def test_issue_pairs_conflict(a, b):
    """The issue's own three examples (plan §2/§5): a name and an unrelated
    word sharing a fold-key skeleton, distinguished only by vowel points."""
    assert occ.pointing_verdict(a, b) == "conflict"
    assert occ.pointing_conflicts(a, b) is True


def test_doubled_vowel_letter_overlaps_without_agreeing():
    """An OCR/typing artefact TOKEN_RE still accepts: one letter carrying
    TWO vowel marks. Its vowel-class SET ({patah, qamats}) overlaps a
    single-vowel set ({qamats}) without being EQUAL to it -- round 3's
    MAJOR: disjointness is not the test, equality is."""
    single = ALEF + QAMATS
    doubled = ALEF + PATAH + QAMATS
    assert occ.pointing_verdict(single, doubled) == "conflict"
    assert occ.pointing_conflicts(single, doubled) is True


@pytest.mark.parametrize("a,b,label", [
    (ESAV, ESAV, "identical pointing"),
    (ESAV_UNPOINTED, ESAV, "unpointed canon side vs pointed text"),
    (ESAV, ESAV_UNPOINTED, "pointed canon side vs unpointed text (both directions)"),
    (NUN + QAMATS + TAV + FINAL_NUN, NATHAN, "partial pointing that agrees"),
    (NATHAN, NATHAN + ETNAHTA, "cantillation-only side"),
    (BET + DAGESH, BET, "dagesh present vs absent"),
    (BET + QAMATS, BET + QAMATS + METEG, "meteg present vs absent"),
    (BET + QAMATS, BET + QAMATS_QATAN, "qamats vs qamats-qatan (equivalence)"),
    (BET + HOLAM, BET + HOLAM_HASER_FOR_VAV, "holam vs holam-haser-for-vav (equivalence)"),
])
def test_compatible_pairs(a, b, label):
    assert occ.pointing_verdict(a, b) == "compatible", label
    assert occ.pointing_conflicts(a, b) is False, label


def test_nfc_vs_nfd_of_the_same_pointed_word_is_compatible():
    nfc_form = unicodedata.normalize("NFC", ESAV)
    assert occ.pointing_verdict(ESAV, nfc_form) == "compatible"
    assert occ.pointing_verdict(nfc_form, ESAV) == "compatible"


@pytest.mark.parametrize("a,b,label", [
    (NUN + TAV + PATAH + FINAL_NUN, NATHAN, "partial pointing that disagrees"),
    (SHIN + SHIN_DOT, SHIN + SIN_DOT, "shin dot vs sin dot"),
    (BET + DAGESH, BET + RAFE, "dagesh vs rafe on the same letter"),
])
def test_conflicting_pairs(a, b, label):
    assert occ.pointing_verdict(a, b) == "conflict", label
    assert occ.pointing_conflicts(a, b) is True, label


@pytest.mark.parametrize("a,b,label", [
    (ALEF + BET, GIMEL + DALET, "different letters"),
    ("abc", ESAV, "Latin vs Hebrew"),
    (AYIN + SHIN, AYIN + SHIN + VAV, "different lengths"),
    ("", ALEF, "empty string vs one letter"),
])
def test_unaligned_pairs_never_conflict(a, b, label):
    assert occ.pointing_verdict(a, b) == "unaligned", label
    assert occ.pointing_verdict(b, a) == "unaligned", label
    assert occ.pointing_conflicts(a, b) is False, label
    assert occ.pointing_conflicts(b, a) is False, label


@pytest.mark.parametrize("a,b", [
    (ESAV, THEY_DID_A),
    (NATHAN, GAVE),
    (ESAV, ESAV_UNPOINTED),
    (BET + QAMATS, BET + QAMATS_QATAN),
    (ALEF + BET, GIMEL + DALET),
])
def test_pointing_verdict_is_symmetric(a, b):
    assert occ.pointing_verdict(a, b) == occ.pointing_verdict(b, a)


# ===========================================================================
# occ_index.attributable_spans()
# ===========================================================================

def test_attributable_spans_group_any_keeps_a_span_matching_the_second_form():
    masked_text = ESAV + " " + THEY_DID_A
    esav_span = (0, len(ESAV))
    conflict_span = (len(ESAV) + 1, len(ESAV) + 1 + len(THEY_DID_A))
    kept = occ.attributable_spans(masked_text, [esav_span, conflict_span], [ESAV, THEY_DID_A])
    # THEY_DID_A's own span is trivially "compatible" with THEY_DID_A itself
    # -- group-any means the SECOND form in the group can vouch for a span
    # the FIRST form conflicts with.
    assert conflict_span in kept
    assert esav_span in kept
    assert len(kept) == 2


def test_attributable_spans_unaligned_form_never_vouches():
    masked_text = THEY_DID_A
    span = (0, len(THEY_DID_A))
    # ALEF+BET shares no letters with THEY_DID_A -- "unaligned", not
    # "compatible" -- and an unaligned form must never vouch for a span it
    # cannot even letter-align with (round 2, MAJOR: a link group may hold a
    # different-fold-key member).
    assert occ.pointing_verdict(masked_text[0:len(THEY_DID_A)], ALEF + BET) == "unaligned"
    kept = occ.attributable_spans(masked_text, [span], [ALEF + BET])
    assert kept == []


def test_attributable_spans_empty_canon_forms_keeps_everything_unchanged():
    masked_text = ESAV + " " + THEY_DID_A
    spans = [(0, len(ESAV)), (len(ESAV) + 1, len(ESAV) + 1 + len(THEY_DID_A))]
    assert occ.attributable_spans(masked_text, spans, []) == spans


def test_attributable_spans_masked_sentinel_inside_span_still_aligns():
    """A span crossing a ``⟦FNREF_5⟧``-style sentinel -- the raw
    text's Latin letters would corrupt the letter-group walk (a different
    letter COUNT/identity than the two-token canon form), so the span must
    be checked against ``mask_sentinels(text)``, never the raw text."""
    token1 = BET + HE
    token2 = LAMED + ALEF
    two_token_form = token1 + " " + token2
    raw_text = token1 + "⟦FNREF_5⟧" + " " + token2
    masked_text = bn.mask_sentinels(raw_text)
    span = (0, len(raw_text))

    # On the RAW text, the sentinel's own Latin letters (F, N, R, E, F) open
    # spurious letter-groups -- the letter sequence no longer matches the
    # two-token canon form at all.
    assert occ.pointing_verdict(raw_text[span[0]:span[1]], two_token_form) == "unaligned"
    assert occ.attributable_spans(raw_text, [span], [two_token_form]) == []

    # On the MASKED text (same length, sentinel replaced by spaces), the
    # letter sequence is exactly token1 + token2 -- aligned and compatible
    # (both unpointed).
    assert occ.pointing_verdict(masked_text[span[0]:span[1]], two_token_form) == "compatible"
    assert occ.attributable_spans(masked_text, [span], [two_token_form]) == [span]


# ===========================================================================
# Integration through occurrence_targets.build()
# ===========================================================================

def test_a_eligible_pointed_esav_withholds_conflicting_they_did_spans(capsys):
    text = f"{ESAV} {THEY_DID_A} {THEY_DID_B}"
    lang = make_lang(name_inventory=[ESAV])
    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({ESAV: make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)

    # Non-vacuity: if attributable_spans() returned its input spans
    # unchanged (the pre-#927 behavior), this count would be 3, not 1 --
    # this assertion is the one that would fail under that mutation.
    assert len(result["eligible_by_source_form"][ESAV]) == 1
    assert ESAV not in result["unresolved_homonyms"]

    err = capsys.readouterr().err
    warn_lines = [
        line for line in err.splitlines()
        if line.startswith("WARN occurrence_targets.py:")
        and "whose Hebrew pointing contradicts every canon spelling of" in line
    ]
    assert len(warn_lines) == 1, f"expected exactly one #927 WARN line, got: {err!r}"
    assert repr(ESAV) in warn_lines[0]
    assert repr(THEY_DID_A) in warn_lines[0] or repr(THEY_DID_B) in warn_lines[0]


def test_b_unpointed_canon_form_keeps_the_238_guarantee_no_warn(capsys):
    """An UNPOINTED canon entry must still find every pointed spelling
    sharing its skeleton -- the #238 guarantee is explicitly preserved."""
    text = f"{ESAV} {THEY_DID_A} {THEY_DID_B}"
    lang = make_lang(name_inventory=[ESAV_UNPOINTED])
    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({ESAV_UNPOINTED: make_entry()})

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)

    assert len(result["eligible_by_source_form"][ESAV_UNPOINTED]) == 3
    assert ESAV_UNPOINTED not in result["unresolved_homonyms"]
    err = capsys.readouterr().err  # read ONCE -- a second readouterr() sees an emptied buffer
    assert "contradicts every canon spelling" not in err, err  # no pointing WARN at all


# Two canon spellings sharing one fold key, differing only in one vowel, plus
# a THIRD, unrelated spelling sharing the same skeleton but conflicting with
# BOTH -- proves group-any excludes the genuinely-conflicting third spelling
# from the collision count, which is the actual #927 change to this route
# (the pre-#927 count for SPAN_A/SPAN_B was already "every span under the
# shared key", collision or not).
SPAN_A = SHIN + PATAH + BET + DAGESH + PATAH + TAV
SPAN_B = SHIN + PATAH + BET + DAGESH + QAMATS + TAV
CONFLICT_SPELLING = SHIN + QAMATS + BET + DAGESH + PATAH + TAV


def _shabbat_fixture(link_groups=None):
    text = f"{CONFLICT_SPELLING} {SPAN_B} {SPAN_A}"
    lang = make_lang(name_inventory=[SPAN_A])
    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")], link_groups=link_groups)
    canon = make_canon({SPAN_A: make_entry(), SPAN_B: make_entry()})
    return manifest, canon, EMPTY_SENSES, lang, nodestream


def test_c_fold_key_collision_group_any_excludes_the_conflicting_third_spelling():
    result = ot.build(*_shabbat_fixture(link_groups=None))
    assert result["unresolved_homonyms"][SPAN_A]["count"] == 2
    assert result["unresolved_homonyms"][SPAN_B]["count"] == 2
    assert result["unresolved_homonyms"][SPAN_A]["reason"] == "fold_match_key_collision"
    assert result["unresolved_homonyms"][SPAN_B]["reason"] == "fold_match_key_collision"


def test_c_link_group_credited_primary_also_excludes_the_conflicting_spelling():
    result = ot.build(*_shabbat_fixture(link_groups={SPAN_A: SPAN_A, SPAN_B: SPAN_A}))
    assert len(result["eligible_by_source_form"][SPAN_A]) == 2
    assert result["unresolved_homonyms"][SPAN_B] == {
        "count": 2, "segs": ["seg01", "seg01"],
        "reason": "fold_group_credited_to_link_group_primary",
    }


# ---------------------------------------------------------------------------
# (d) parity: production_occurrences excludes the conflicting span, and
# _group_production_spans_by_name()/index_manifest() agree with it exactly
# (the #243 "never drift" invariant, now widened to cover #927).
# ---------------------------------------------------------------------------

def test_d_production_occurrences_and_index_manifest_and_evidence_verify_agree(tmp_path):
    text = f"{ESAV} {THEY_DID_A}"
    lang = make_lang(name_inventory=[ESAV])

    spans = occ.production_occurrences(ESAV, text, lang)
    assert spans == [(0, len(ESAV))], (
        "production_occurrences must exclude the conflicting THEY_DID_A span"
    )

    competitors = ev.fold_collision_map([ESAV])
    grouped = ev._group_production_spans_by_name(text, lang, competitors)
    assert list(grouped.get(ESAV, [])) == spans

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"blocks": {
        "b1": {"id": "b1", "type": "PARA", "seg": "seg01", "order_index": 0,
               "source_file": "x.txt", "plain_text": text, "sha1": "a"},
    }}), encoding="utf-8")
    records = occ.index_manifest(manifest_path, [ESAV], lang)
    assert [(r["char_start"], r["char_end"]) for r in records] == spans


def test_e_evidence_citing_a_withheld_span_fails_not_a_production_match():
    text = f"{ESAV} {THEY_DID_A}"
    lang = make_lang(name_inventory=[ESAV])
    block_id, seg = "b1", "seg01"
    manifest = {"blocks": {
        block_id: {"id": block_id, "type": "PARA", "seg": seg, "order_index": 0,
                   "plain_text": text},
    }}
    withheld_start = text.index(THEY_DID_A)
    withheld_end = withheld_start + len(THEY_DID_A)
    evidence = _whole_block_evidence(block_id, seg, text, withheld_start, withheld_end)

    failure = ev.verify_evidence(ESAV, _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "not a production match" in failure.reason


# ---------------------------------------------------------------------------
# (f) footnote-origin and embedded-verse-origin records are filtered too.
# ---------------------------------------------------------------------------

def test_f_footnote_origin_record_is_pointing_filtered():
    manifest = make_manifest(
        blocks={"bFNDef": make_block(f"{ESAV} {THEY_DID_A} said so.", seg=None)},
        footnotes=[{"n": 1, "anchor_block": "bAnchor", "anchor_seg": "seg05", "def_block": "bFNDef"}],
    )
    nodestream = make_nodestream(nodes=[], footnotes=[{"n": 1, "text": "translated footnote"}])
    canon = make_canon({ESAV: make_entry()})
    lang = make_lang(name_inventory=[ESAV])

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)
    records = result["eligible_by_source_form"][ESAV]
    assert len(records) == 1
    assert records[0]["origin"] == "footnote"


def test_f_embedded_verse_origin_record_is_pointing_filtered():
    manifest = make_manifest(
        blocks={"bCarrier": make_block(f"He said: ⟦VERSE_VE1_xx⟧", seg="seg01")},
        verse_store=[{"vid": "VE1", "mount": "embedded", "parent_block": "bCarrier",
                      "plain_text": f"{ESAV} {THEY_DID_A} sang."}],
    )
    nodestream = make_nodestream(nodes=[
        make_node("bCarrier", "seg01",
                  verses=[make_claim("VE1", rendered="he sang", literal_gloss="")]),
    ])
    canon = make_canon({ESAV: make_entry()})
    lang = make_lang(name_inventory=[ESAV])

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)
    records = result["eligible_by_source_form"][ESAV]
    assert len(records) == 1
    assert records[0]["origin"] == "embedded_verse"


def test_g_eligible_form_beside_a_not_a_name_sibling_gets_nothing_no_collision(capsys):
    """A `basis: "not_a_name"` sibling sharing the fold key is NOT part of
    the eligible attribution group (round 1, MAJOR 1) -- its occurrence must
    not be credited to the eligible name, and this is NOT a fold-key
    collision (only one ELIGIBLE form exists at this key)."""
    text = THEY_DID_A  # ESAV itself never occurs
    lang = make_lang(name_inventory=[ESAV])
    manifest = make_manifest(blocks={"b1": make_block(text, seg="seg01")})
    nodestream = make_nodestream(nodes=[make_node("b1", "seg01")])
    canon = make_canon({
        ESAV: make_entry(),
        THEY_DID_A: make_entry(basis="not_a_name"),
    })

    result = ot.build(manifest, canon, EMPTY_SENSES, lang, nodestream)

    assert ESAV not in result["eligible_by_source_form"]
    assert ESAV not in result["unresolved_homonyms"]
    err = capsys.readouterr().err
    assert "fold_match_key_collision" not in err
    assert "whose Hebrew pointing contradicts every canon spelling of" in err
    assert repr(ESAV) in err


# ===========================================================================
# (h) person_registry.build_contexts() -- attribution_forms threading
# ===========================================================================

def _load_person_registry():
    spec = importlib.util.spec_from_file_location("person_registry_for_927_test", PERSON_REGISTRY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_h_build_contexts_attribution_forms_credits_both_spellings():
    pr = _load_person_registry()
    mods = pr.import_siblings()
    lang = make_lang(name_inventory=[SPAN_A])
    # Alternative spelling first, then the primary -- both are physically
    # present, and both must be found once attribution_forms widens the
    # search past SPAN_A alone.
    text = f"{SPAN_B} then {SPAN_A}."
    manifest = {"blocks": {"b1": {"plain_text": text}}}
    records = [
        {"seg": "seg01", "origin": "block", "source_block": "b1"},
        {"seg": "seg01", "origin": "block", "source_block": "b1"},
    ]

    with_group, total, truncated = pr.build_contexts(
        SPAN_A, records, manifest, lang, mods, max_contexts=10, context_chars=200,
        attribution_forms=[SPAN_A, SPAN_B],
    )
    assert total == 2 and truncated is False
    assert [c["window_centred_on_match"] for c in with_group] == [True, True]
    assert SPAN_B in with_group[0]["text"]
    assert SPAN_A in with_group[1]["text"]

    without_group, _, _ = pr.build_contexts(
        SPAN_A, records, manifest, lang, mods, max_contexts=10, context_chars=200,
    )
    # SPAN_A alone conflicts with SPAN_B's own span, so only SPAN_A's span
    # survives -- the second record has no span left to pair with (the
    # exact drift round 1's MAJOR 2 named: "the primary's own single-form
    # production_occurrences would return fewer spans and the windows would
    # pair with the wrong span").
    matches = [c["window_centred_on_match"] for c in without_group]
    assert matches.count(True) == 1, (
        f"expected exactly one context still paired without the widened group, got {matches}"
    )


# ===========================================================================
# (h2) End-to-end through the real --prep entry point (mirrors
# tests/person_registry_prep.test.py's `_with_fold_group`/`_prep_in_root`).
# ===========================================================================

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _registry_fixture as fx  # noqa: E402

POINTED_PRIMARY = SPAN_A
POINTED_ALT = SPAN_B
# A third linked member with a DIFFERENT fold key -- #497 fold-credit can
# legally cross targets; it must never vouch for a span it cannot align
# with (round 2, MAJOR).
POINTED_THIRD = BET + HE
POINTED_VERB = SHIN + SHEVA + BET + DAGESH + PATAH + TAV  # conflicts with BOTH primary/alt
POINTED_LANG_CONFIG = "pointing_927_fixture.json"
# Wide gaps so a small --context-chars window around one occurrence never
# reaches another -- the assertion below depends on this.
POINTED_SENTENCE = (
    f" Puis {POINTED_VERB} arriva un jour tout a fait different, "
    f"et bien plus tard dans le recit {POINTED_ALT} puis enfin "
    f"{POINTED_PRIMARY} conclurent l'affaire ensemble."
)


def _with_pointed_group(root: Path, link_groups) -> Path:
    fr = json.loads((root / "languages" / "fr.json").read_text(encoding="utf-8"))
    fr["name_inventory"] = [POINTED_PRIMARY]
    (root / "languages" / POINTED_LANG_CONFIG).write_text(
        json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "literary-translator" / "profile.yml").write_text(
        f"source:\n  language:\n    particle_config: {POINTED_LANG_CONFIG}\n",
        encoding="utf-8")

    canon_path = root / "canon.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    for form in (POINTED_PRIMARY, POINTED_ALT, POINTED_THIRD):
        canon["entries"][form] = {
            "source_form": form, "is_proper_name": True,
            "canonical_target_form": "Placeholder", "basis": "established",
            "confidence": "high", "category": "person",
        }
    canon_path.write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["blocks"]["PARA:seg01:0001"]["plain_text"] += POINTED_SENTENCE
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    ns_path = root / "out" / ".assembled" / "nodestream.json"
    nodestream = json.loads(ns_path.read_text(encoding="utf-8"))
    if link_groups is not None:
        nodestream["link_groups"] = link_groups
    ns_path.write_text(json.dumps(nodestream, ensure_ascii=False), encoding="utf-8")
    return root


def _prep_pointed_root(root: Path):
    """Mirrors tests/person_registry_prep.test.py's own `_prep_in_root`: the
    root's OWN copy of the script must run (so its language config resolves),
    with a small --context-chars so a context window cannot accidentally
    reach across the sentence to a neighboring occurrence."""
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "person_registry.py"), "--prep",
         "--durable-root", str(root), "--plugin-root", str(fx.ASSETS.parent),
         "--context-chars", "16"],
        capture_output=True, text=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    doc_path = root / "registry" / "registry_input.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8")) if doc_path.is_file() else None
    return proc.returncode, payload, doc


@pytest.fixture()
def root(tmp_path):
    return fx.build_root(tmp_path)


def test_h2_end_to_end_prep_credits_the_group_and_excludes_the_verb(root):
    _with_pointed_group(root, {
        POINTED_PRIMARY: POINTED_PRIMARY,
        POINTED_ALT: POINTED_PRIMARY,
        POINTED_THIRD: POINTED_PRIMARY,
    })
    code, payload, doc = _prep_pointed_root(root)
    assert code == 0, payload
    units = {(u["unit"]["source_form"], u["unit"]["sense_id"]): u for u in doc["units"]}

    primary = units[(POINTED_PRIMARY, None)]
    alt = units[(POINTED_ALT, None)]
    third = units[(POINTED_THIRD, None)]

    assert primary["attributable"] is True
    assert primary["occurrences"] == 2
    assert len(primary["mentions"]) == 2
    # Two contexts, BOTH centred on their own span, the alternative spelling's
    # first (it precedes the primary's in the block). Without the cmd_prep
    # call site passing attribution_group() through, production_occurrences
    # (primary alone) refuses the alternative's span, the first context falls
    # back to a start-of-block window (centred False) and the verb spelling
    # would still be absent from it -- so the verb check alone cannot pin
    # the wiring; the centring and the per-window spellings do.
    contexts = primary["contexts"]
    assert len(contexts) == 2, contexts
    assert [ctx["window_centred_on_match"] for ctx in contexts] == [True, True], contexts
    assert POINTED_ALT in contexts[0]["text"] and POINTED_PRIMARY not in contexts[0]["text"], contexts[0]
    assert POINTED_PRIMARY in contexts[1]["text"], contexts[1]
    for ctx in contexts:
        assert POINTED_VERB not in ctx["text"], (
            f"the conflicting verb spelling leaked into a primary context: {ctx['text']!r}"
        )

    assert alt["attributable"] is False
    assert alt["occurrences"] is None
    assert "fold_group_credited_to_link_group_primary" in alt["occurrences_reason"]
    assert alt["mentions"] == []

    # POINTED_THIRD shares no letters with the shabbat-key pair -- it must
    # never be credited the verb's (or anyone else's) occurrence just
    # because a link group happens to name it too.
    assert third["occurrences"] == 0
