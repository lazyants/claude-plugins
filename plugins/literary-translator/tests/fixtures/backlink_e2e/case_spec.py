"""tests/fixtures/backlink_e2e/case_spec.py -- the hand-authored source/
translation content for the backlink-integrity e2e acceptance fixture (RFC
lt-appendix-backlink-integrity, 1.8.0, plan Contract/D2/D4 sections).

NOT a `*.test.py` file -- pytest's `--import-mode=importlib` collection
glob only picks up `*.test.py` (`pytest.ini`), so this ordinary `.py`
module is never collected as a test itself. `tests/backlink_integrity_e2e.
test.py` loads it via `importlib.util.spec_from_file_location`, the same
self-anchoring convention `tests/verse_footnote_corpus.py` uses -- and,
like that module, this one is pure DATA plus small derivations; the actual
manifest/segpack/draft/ledger FILE-WRITING mechanics live in the test file
itself (copied from `tests/assemble.test.py`'s own `write_manifest`/
`write_segpack`/`write_draft`/`write_ledger` helpers, extended with a
minimal `canon_map` -- see that file's own module docstring for why those
helpers are copied, never imported, from a `*.test.py` sibling pytest
already owns).

## What this fixture proves (see the plan's Contract + D2 table)

One small book, two segments, staged directly at the manifest/segpack/
draft layer (no real EPUB/extraction -- `manifest.blocks{}` plain_text
here is genuinely the "source" the production name matcher scans). Twelve
canon entries, each isolating exactly one occurrence-eligibility rule:

  - **Aldric** (#206, divergent target): occurs in TWO blocks, one per
    segment (`p1`/seg01, `p4`/seg02). `p1`'s DRAFT literally contains
    "Aldric" (the old inline linker finds it); `p4`'s draft renders it as
    "The old man" -- no literal "Aldric" substring, so the OLD linker
    would silently miss this occurrence. Both are still real SOURCE
    occurrences, so source-anchored `## Mentions` captures both.
  - **Petro** / **Pavlo** (#207-a, collision): distinct source_forms in
    the SAME block (`p2`/seg01), both `canonical_target_form: "Peter"`.
    Draft `p2` renders both as "Peter" (the real-world collapse).
    Collision de-linking (D3) is gated on `target == "obsidian"`, NOT on
    the `## Mentions` appendix flag -- so BOTH flag-on and flag-off
    obsidian renders de-link "Peter" from the inline map entirely (neither
    owner gets the inline link; flag-on additionally gives both notes
    their OWN source-anchored Mentions entry). The old shortest-then-
    lexicographic tiebreak ("Pavlo" wins) survives only on the non-obsidian
    (`target: "custom"`) render path, where D3 stays inert -- unexercised
    by this fixture, which only ever renders `target: "obsidian"`.
  - **Marek** (#207-b, split): one occurrence (`p5`/seg02), but
    `canon_senses.json` splits it into two senses -- excluded from
    `eligible_by_source_form` entirely, surfaced only in
    `unresolved_homonyms`.
  - **Lucky** (sense_translated): one occurrence (`p6`/seg02). Gets a
    `## Mentions` entry (source-anchoring includes `sense_translated`,
    Contract section) but NO inline auto-link (`build_entity_index` still
    skips `basis == "sense_translated"`, unchanged from 1.7.0).
  - **Almanac** (not_a_name / is_proper_name:false): one occurrence
    (`p7`/seg02), `canonical_target_form: ""` (degenerate -- also skipped
    by the OLD linker's own empty-target rule, so this entry is a clean,
    single-mechanism test of `entry_is_index_eligible`, never conflated
    with inline-link degenerate-target skipping). Must appear in NONE of
    the three views.
  - **Ysolde** (footnote-only mention): occurs ONLY inside footnote 1's
    OWN definition text (`FN1`, anchored from `p1`/seg01) -- never in any
    body/verse block. Eligible iff the ASSEMBLED footnote text is
    non-empty; links at the footnote's `anchor_seg`.
  - **Corentin** (body-embedded-verse-only mention): occurs ONLY inside
    an EMBEDDED verse's (`V001`, `mount:"embedded"`) own source text,
    parented to prose block `p3`/seg01 (never a footnote def) -- eligible,
    links at the carrier block's seg.
  - **Thibaut** (footnote-embedded verse, INELIGIBLE): occurs ONLY inside
    an embedded verse (`V003`) whose carrier IS a footnote-definition
    block (`FN2`) -- D2's embedded_verse row explicitly excludes a
    footnote-def carrier. Zero eligible records anywhere.
  - **Ozias** (skip-mode standalone verse under a DECLARED HEADING,
    INELIGIBLE): occurs inside a `mount:"block"` (standalone) verse
    (`V002`) whose carrier block (`vh1`) is ALSO `type: "HEAD"` (declared
    heading precedence, #210) -- and whose DRAFT verse content is
    skip-mode empty (`{}`). `block_renders_nonempty` keys on the source
    block's own verse-mount claim, not the node's classified `kind`, so
    this stays ineligible even though `vh1` classifies as a heading node.
  - **Cassian** (frontback `omit` carrier, INELIGIBLE): occurs only in
    `FRONTBACK:cover`, whose `frontback[].decision == "omit"` drops the
    block from the NodeStream entirely.
  - **Domnall** (frontback `regenerate` carrier, INELIGIBLE): occurs only
    in `FRONTBACK:foreword`'s ORIGINAL source text, whose
    `frontback[].decision == "regenerate"` replaces the real text with a
    synthesized placeholder node (`raw_type ==
    "FRONTBACK_REGENERATE_PLACEHOLDER"`) -- the original text (and its
    "Domnall" occurrence) never reaches the render at all. NOTE: the
    synthesized placeholder node still gets its OWN rendered segment note
    (verified empirically -- `assemble.test.py`'s
    `test_frontback_regenerate_emits_a_placeholder_node_and_a_warning`
    documents the same "not silently dropped" behavior), which shifts the
    vault's note NUMBERING: `001 FRONTBACK_foreword.md` (the placeholder)
    precedes `002 Chapter One.md`/`003 Chapter Two.md` -- every `[[NNN
    slug]]` reference in this fixture/its tests uses `002`/`003`
    accordingly, never `001`/`002`.

## STOPWORDS note

`languages/backlink_e2e.json`'s STOPWORDS list includes "The" specifically
so "The Almanac lay open..." tokenizes as an ISOLATED "Almanac" candidate
(a leading capitalized "The" immediately followed by another capitalized
token would otherwise FUSE into one two-token run "The Almanac", per
`bootstrap_names.extract_candidate_spans`'s pass-1 algorithm, and
`occ_index.production_occurrences("Almanac", ...)` would then never match
-- functionally harmless here since Almanac is excluded by
`entry_is_index_eligible` regardless of whether the matcher can even see
it, but keeping the fixture's candidate extraction clean avoids a
confusing red herring).
"""
from __future__ import annotations

import hashlib

# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------

FN_PH_1 = "⟦FNREF_1⟧"
FN_PH_2 = "⟦FNREF_2⟧"


def _verse_placeholder(vid: str, plain_text: str) -> str:
    """Mirrors the real extractor's own convention (`tests/
    verse_footnote_corpus.py`'s module docstring): an 8-hex
    `sha1(plain_text)[:8]` suffix. Opaque to assemble.py/render_obsidian.py
    (the placeholder is free-form per segpack.schema.json) -- computed here
    only for realism/documentation, not because anything validates it."""
    short = hashlib.sha1(plain_text.encode("utf-8")).hexdigest()[:8]
    return f"⟦VERSE_{vid}_{short}⟧"


# ---------------------------------------------------------------------------
# Verse source texts + placeholders
# ---------------------------------------------------------------------------

V001_TEXT = "Corentin sailed beneath a distant star, alone."   # embedded, body carrier p3
V002_TEXT = "Ozias walked in silence through the hall."        # standalone (block-mount), carrier vh1 (declared HEAD)
V003_TEXT = "Thibaut wandered through fields of gold."         # embedded, footnote-def carrier FN2

V001_PH = _verse_placeholder("V001", V001_TEXT)
V002_PH = _verse_placeholder("V002", V002_TEXT)
V003_PH = _verse_placeholder("V003", V003_TEXT)

V001_SHA1 = hashlib.sha1(V001_TEXT.encode("utf-8")).hexdigest()
V002_SHA1 = hashlib.sha1(V002_TEXT.encode("utf-8")).hexdigest()
V003_SHA1 = hashlib.sha1(V003_TEXT.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# manifest.blocks{} (global order_index assigned by iteration order below --
# the test file's write_manifest() helper fills in id/sha1/source_file).
# ---------------------------------------------------------------------------

MANIFEST_BLOCKS = {
    "FRONTBACK:cover": {
        "type": "FRONTBACK", "seg": None, "order_index": 0,
        "plain_text": "In memory of Cassian, this labor is dedicated.",
        "decision": "omit", "origin": "front-matter", "reason": "fixture",
    },
    "FRONTBACK:foreword": {
        "type": "FRONTBACK", "seg": None, "order_index": 1,
        "plain_text": "A foreword briefly mentioning Domnall.",
        "decision": "regenerate", "origin": "front-matter", "reason": "fixture",
    },
    "h1": {"type": "HEAD", "seg": "seg01", "order_index": 2, "plain_text": "Chapter One"},
    "p1": {
        "type": "PARA", "seg": "seg01", "order_index": 3,
        "plain_text": f"Aldric returned to the manor and sat by the fire.{FN_PH_1}{FN_PH_2}",
        "fnrefs": [1, 2],
    },
    "p2": {
        "type": "PARA", "seg": "seg01", "order_index": 4,
        "plain_text": "Petro spoke quietly to Pavlo before they parted ways.",
    },
    "p3": {
        "type": "PARA", "seg": "seg01", "order_index": 5,
        "plain_text": f"He remembered an old verse:{V001_PH} before falling silent.",
    },
    "vh1": {
        # Declared-heading precedence (#210): raw_type "HEAD" wins even
        # though this block is ALSO claimed by a standalone (mount:"block")
        # verse -- see MANIFEST_VERSE_STORE's V002 entry below.
        "type": "HEAD", "seg": "seg01", "order_index": 6,
        "plain_text": V002_PH,
    },
    "FN1": {
        "type": "FN", "seg": None, "order_index": 7,
        "plain_text": "An old legend concerning Ysolde and her voyage.",
    },
    "FN2": {
        "type": "FN", "seg": None, "order_index": 8,
        "plain_text": f"A myth, quoted at length:{V003_PH}",
    },
    "h2": {"type": "HEAD", "seg": "seg02", "order_index": 9, "plain_text": "Chapter Two"},
    "p4": {
        "type": "PARA", "seg": "seg02", "order_index": 10,
        "plain_text": "Aldric, now much older, gazed at the same stars.",
    },
    "p5": {
        "type": "PARA", "seg": "seg02", "order_index": 11,
        "plain_text": "Marek the elder gave his blessing.",
    },
    "p6": {
        "type": "PARA", "seg": "seg02", "order_index": 12,
        "plain_text": "Lucky proved his own name true once more.",
    },
    "p7": {
        "type": "PARA", "seg": "seg02", "order_index": 13,
        "plain_text": "The Almanac lay open on the table.",
    },
}

MANIFEST_SEGMENTS = [
    {"seg": "seg01", "kind": "body", "title_text": "Chapter One",
     "block_ids": ["h1", "p1", "p2", "p3", "vh1"], "word_count": 100},
    {"seg": "seg02", "kind": "body", "title_text": "Chapter Two",
     "block_ids": ["h2", "p4", "p5", "p6", "p7"], "word_count": 100},
]

MANIFEST_FOOTNOTES = [
    {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"},
    {"n": 2, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN2"},
]

MANIFEST_VERSE_STORE = [
    {"vid": "V001", "placeholder": V001_PH, "context": "body", "mount": "embedded",
     "parent_block": "p3", "plain_text": V001_TEXT, "sha1": V001_SHA1},
    {"vid": "V002", "placeholder": V002_PH, "context": "body", "mount": "block",
     "parent_block": "vh1", "plain_text": V002_TEXT, "sha1": V002_SHA1},
    {"vid": "V003", "placeholder": V003_PH, "context": "footnote", "mount": "embedded",
     "parent_block": "FN2", "plain_text": V003_TEXT, "sha1": V003_SHA1},
]

MANIFEST_FRONTBACK = [
    {"id": "FRONTBACK:cover", "decision": "omit"},
    {"id": "FRONTBACK:foreword", "decision": "regenerate"},
]

# ---------------------------------------------------------------------------
# Per-segment segpacks (local order_index 0-based; the test file's
# write_segpack() helper extends this with a minimal, schema-valid
# canon_map: {} -- segpack.schema.json:132 requires the key, harmless-empty
# is schema-valid since assemble.py never reads it).
# ---------------------------------------------------------------------------

SEGPACKS = {
    "seg01": {
        "blocks": [
            {"id": "h1", "order_index": 0, "plain_text": "Chapter One"},
            {"id": "p1", "order_index": 1,
             "plain_text": f"Aldric returned to the manor and sat by the fire.{FN_PH_1}{FN_PH_2}"},
            {"id": "p2", "order_index": 2, "plain_text": "Petro spoke quietly to Pavlo before they parted ways."},
            {"id": "p3", "order_index": 3,
             "plain_text": f"He remembered an old verse:{V001_PH} before falling silent."},
            {"id": "vh1", "order_index": 4, "plain_text": V002_PH},
        ],
        "footnotes": [
            {"n": 1, "source_text": "An old legend concerning Ysolde and her voyage."},
            {"n": 2, "source_text": f"A myth, quoted at length:{V003_PH}"},
        ],
        "verses": [
            {"vid": "V001", "placeholder": V001_PH, "parent_block": "p3", "mount": "embedded", "n_line": 1},
            {"vid": "V002", "placeholder": V002_PH, "parent_block": "vh1", "mount": "block", "n_line": 1},
            # V003 is parented to a footnote-DEFINITION block (FN2), never a
            # member of seg01's own block_ids -- mirrors the real segpack
            # shape a footnote-embedded verse gets (verified empirically
            # against segpack.build_pack() over verse_footnote_corpus.py's
            # own case "c").
            {"vid": "V003", "placeholder": V003_PH, "parent_block": "FN2", "mount": "embedded", "n_line": 1},
        ],
    },
    "seg02": {
        "blocks": [
            {"id": "h2", "order_index": 0, "plain_text": "Chapter Two"},
            {"id": "p4", "order_index": 1, "plain_text": "Aldric, now much older, gazed at the same stars."},
            {"id": "p5", "order_index": 2, "plain_text": "Marek the elder gave his blessing."},
            {"id": "p6", "order_index": 3, "plain_text": "Lucky proved his own name true once more."},
            {"id": "p7", "order_index": 4, "plain_text": "The Almanac lay open on the table."},
        ],
        "footnotes": [],
        "verses": [],
    },
}

# ---------------------------------------------------------------------------
# Per-segment drafts (translated content). Note p1 vs p4: p1's translation
# keeps the literal "Aldric" substring (old inline linker finds it); p4's
# translation deliberately does NOT (the #206 divergent-target case).
# ---------------------------------------------------------------------------

DRAFTS = {
    "seg01": {
        "blocks": {
            "h1": "Chapter One",
            "p1": f"Aldric came back to the manor and sat by the fire.{FN_PH_1}{FN_PH_2}",
            "p2": "Peter spoke quietly to Peter before they parted ways.",
            "p3": f"He recalled an old verse:{V001_PH} before falling quiet.",
            "vh1": V002_PH,
        },
        "footnotes": {
            "1": "An old legend concerning Ysolde's voyage.",
            "2": f"A myth, told at length:{V003_PH}",
        },
        "verses": {
            "V001": {
                "rendered": "Corentin sailed beneath a distant star, alone.\nHe never saw the shore again.",
                "literal_gloss": "A literal rendering of the same two lines, worded differently.",
            },
            "V002": {},  # skip-mode: both rendered/literal_gloss blank -- the INELIGIBLE case
            "V003": {
                "rendered": "Thibaut wandered through fields of gold.\nNo one called his name.",
                "literal_gloss": "A literal gloss of the wandering verse, worded differently.",
            },
        },
        "names": [],
        "notes": [],
    },
    "seg02": {
        "blocks": {
            "h2": "Chapter Two",
            "p4": "The old man gazed at the same stars, remembering everything.",
            "p5": "Marek offered his blessing once more.",
            "p6": "Lucky proved his own name true once more.",
            "p7": "The almanac lay open on the table.",
        },
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    },
}

LEDGER_CONVERGED_SEGS = ["seg01", "seg02"]

# ---------------------------------------------------------------------------
# Expected occurrence-eligibility outcome (the plan's Contract/D2 table,
# applied to the fixture above) -- the test file asserts the REAL pipeline
# output against these constants, never the reverse.
# ---------------------------------------------------------------------------

# (source_form, seg) pairs expected in eligible_by_source_form / the
# persisted nodestream["mentions"] / the rendered "## Mentions" sections.
EXPECTED_ELIGIBLE_PAIRS = {
    ("Aldric", "seg01"),
    ("Aldric", "seg02"),
    ("Petro", "seg01"),
    ("Pavlo", "seg01"),
    ("Lucky", "seg02"),
    ("Ysolde", "seg01"),
    ("Corentin", "seg01"),
}

# source_forms with a canon entry and a real source occurrence, but ZERO
# eligible records anywhere (each isolates one D2 ineligibility rule).
EXPECTED_INELIGIBLE_SOURCE_FORMS = {
    "Thibaut",  # footnote-embedded verse
    "Ozias",    # skip-mode standalone verse under a declared heading
    "Cassian",  # frontback omit carrier
    "Domnall",  # frontback regenerate carrier
}

# Excluded from the canon population entirely (entry_is_index_eligible).
EXPECTED_NOT_A_NAME_SOURCE_FORM = "Almanac"

# Split form -> unresolved_homonyms.
EXPECTED_SPLIT_SOURCE_FORM = "Marek"
EXPECTED_SPLIT_SEGS = ["seg02"]

# sense_translated: eligible for Mentions, but never inline-auto-linked.
EXPECTED_SENSE_TRANSLATED_SOURCE_FORM = "Lucky"

# #207-a collision: shared canonical_target_form, sorted owner list.
EXPECTED_COLLISION_TARGET = "Peter"
EXPECTED_COLLISION_OWNERS = ["Pavlo", "Petro"]
# NOTE: the 1.7.0 shortest-then-lexicographic tiebreak ("Pavlo" wins, both
# "Petro"/"Pavlo" are 5 chars) is no longer this fixture's flag-off
# behavior -- D3 (collision de-linking) now gates on `target == "obsidian"`
# alone, independent of the Mentions flag, so both flag-on and flag-off
# obsidian renders de-link "Peter" unconditionally. The tiebreak survives
# only on the (unexercised-by-this-fixture) non-obsidian render path; no
# constant for it is kept here since nothing in this fixture consumes it.
# #240 gate half: both owners are ordinary transliterated entries (neither
# is basis: sense_translated) sharing one canonical_target_form, same case
# -- render_obsidian.build_entity_index DOES de-link this target under
# collision_delink=True (flag on). See validate_backlinks.py's
# `_renderer_delinked_targets`/`_compute_collisions`.
EXPECTED_COLLISION_DELINKED = True
