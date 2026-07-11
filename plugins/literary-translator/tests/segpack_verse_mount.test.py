"""tests/segpack_verse_mount.test.py -- regression-lock suite for cluster
#96 (verse mount/n_line threading through segpack.py + segpack.schema.json)
and the Point-1 fix (segpack.py discovering footnotes cited INSIDE an
EMBEDDED verse, so a footnote quoted only within a poem is no longer
silently dropped from footnotes_out -- the segpack half of the #93/#106
end-to-end footnote-carry-through).

Loads the real, shipped segpack.py via importlib, mirroring tests/
seg_safety_segpack.test.py's own `_load_module` helper -- segpack.py's
`from bootstrap_names import ...` only resolves via sys.path[0] under a
real `python3 segpack.py` invocation, so its own scripts/ directory must be
inserted onto sys.path around the in-process load.

Three concerns, in order:
  1. `_verse_line_count()` -- direct unit tests against synthetic manifest
     verse.store nodes (n_line threaded PRIMARY, plain_text/source_html
     LEGACY fallback only).
  2. `build_pack()` -- verses_out threads `mount` (tolerantly normalized)
     and `n_line` for both block-mount and embedded verses; and the Point-1
     footnote-discovery pass surfaces a footnote cited only inside an
     embedded verse's own text.
  3. `validate_segpack()` -- the hand-rolled structural self-check gains
     mount/n_line rules in lockstep with segpack.schema.json's new fields.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
LANGUAGES_DIR = ASSETS_DIR / "languages"

assert SEGPACK_SCRIPT.is_file(), f"segpack.py not found at {SEGPACK_SCRIPT}"
assert (LANGUAGES_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_DIR}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors seg_safety_segpack.test.py's own loader exactly (see that
    file's docstring for why the sys.path dance is needed)."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK_MODULE = _load_module("segpack_verse_mount_under_test", SEGPACK_SCRIPT, SCRIPTS_DIR)

# Real shipped particle config -- build_pack()'s name-scanning pass needs a
# genuinely valid LanguageConfig (never hand-rolled JSON here).
LANG_CONFIG = SEGPACK_MODULE.load_language_config("fr.json", LANGUAGES_DIR)


# ---------------------------------------------------------------------------
# 1. _verse_line_count() -- direct unit tests against synthetic manifest
#    verse.store nodes. No real extractor needed here (the extractor's own
#    #92 producer-side fix, which makes n_line authoritative even for a
#    bare-<p> stanza poem, is a separate track) -- this locks down the
#    CONSUMER side: n_line threaded directly, primary over plain_text.
# ---------------------------------------------------------------------------


def test_verse_line_count_threads_n_line_directly():
    """n_line is PRIMARY: a manifest carrying a positive n_line must win
    even when plain_text (already newline-collapsed by normalize_text)
    would suggest a different, wrong count."""
    v = {"n_line": 3, "plain_text": "one single collapsed line"}
    assert SEGPACK_MODULE._verse_line_count(v) == 3


def test_verse_line_count_legacy_fallback_plain_text_when_n_line_zero():
    """A LEGACY manifest (pre-#92 extractor) records n_line==0 for a bare-
    <p> stanza poem -- fall back to plain_text's own non-blank line count."""
    v = {"n_line": 0, "plain_text": "line one\nline two\nline three"}
    assert SEGPACK_MODULE._verse_line_count(v) == 3


def test_verse_line_count_legacy_fallback_source_html_when_no_plain_text():
    """No plain_text at all (a source_html-only adapter): recover an
    approximate line count from tag boundaries."""
    v = {"n_line": None, "source_html": "<p>Premiere ligne</p><p>Deuxieme ligne</p>"}
    assert SEGPACK_MODULE._verse_line_count(v) == 2


def test_verse_line_count_missing_everything_returns_zero():
    assert SEGPACK_MODULE._verse_line_count({}) == 0


def test_verse_line_count_bool_n_line_is_not_treated_as_int():
    """isinstance(True, int) is True in Python -- the helper must exclude
    bool explicitly (mirroring the word_count/order_index idiom elsewhere in
    this script), else a stray n_line=true in a hand-edited manifest would
    thread as n_line=1 instead of falling back to the real line count."""
    v = {"n_line": True, "plain_text": "line one\nline two"}
    assert SEGPACK_MODULE._verse_line_count(v) == 2


def test_verse_line_count_negative_n_line_falls_back():
    v = {"n_line": -5, "plain_text": "line one\nline two"}
    assert SEGPACK_MODULE._verse_line_count(v) == 2


# ---------------------------------------------------------------------------
# Shared manifest/canon fixture helpers for build_pack() tests below.
# ---------------------------------------------------------------------------


def _minimal_canon():
    return {
        "entries": {},
        "generation_hashes": {
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def _base_generation_hashes():
    return {"source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40}


# ---------------------------------------------------------------------------
# 2a. build_pack() verses_out -- mount/n_line threading (#96).
# ---------------------------------------------------------------------------


def _manifest_with_two_verses():
    """One BLOCK-mount standalone verse (parent_block is its own VERSE:
    block among blocks[]) and one EMBEDDED verse (parent_block is the prose
    block p1) -- exercises both branches of the tolerant mount normalization
    and both branches of n_line threading."""
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 20,
                "block_ids": ["p1", "vblockA"],
            }
        ],
        "blocks": {
            "p1": {
                "id": "p1",
                "order_index": 0,
                "plain_text": "Prose with an embedded poem ⟦VERSE_vEmbed⟧ inside it.",
            },
            "vblockA": {
                "id": "vblockA",
                "order_index": 1,
                "plain_text": "⟦VERSE_vBlock⟧",
            },
        },
        "footnotes": [],
        "verse": {
            "store": [
                {
                    "vid": "vBlock",
                    "placeholder": "⟦VERSE_vBlock⟧",
                    "parent_block": "vblockA",
                    "mount": "block",
                    "plain_text": "Line one\nLine two\nLine three",
                    "n_line": 3,
                },
                {
                    "vid": "vEmbed",
                    "placeholder": "⟦VERSE_vEmbed⟧",
                    "parent_block": "p1",
                    "mount": "embedded",
                    "plain_text": "Only one embedded line",
                    "n_line": 1,
                },
            ]
        },
        "generation_hashes": _base_generation_hashes(),
    }


def test_build_pack_threads_mount_and_n_line_for_both_verses():
    manifest = _manifest_with_two_verses()
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    by_vid = {v["vid"]: v for v in pack["verses"]}
    assert by_vid["vBlock"] == {
        "vid": "vBlock",
        "placeholder": "⟦VERSE_vBlock⟧",
        "parent_block": "vblockA",
        "mount": "block",
        "n_line": 3,
    }
    assert by_vid["vEmbed"] == {
        "vid": "vEmbed",
        "placeholder": "⟦VERSE_vEmbed⟧",
        "parent_block": "p1",
        "mount": "embedded",
        "n_line": 1,
    }


def test_build_pack_normalizes_unknown_mount_value_to_block():
    """Tolerant normalization: any manifest value OTHER than the literal
    string "embedded" (missing, "block", or a future/unknown adapter value)
    must fold to segpack's own "block" enum member -- mount is read straight
    off the manifest node, never re-derived from parent-kind."""
    manifest = _manifest_with_two_verses()
    manifest["verse"]["store"][1]["mount"] = "some-future-adapter-value"
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    by_vid = {v["vid"]: v for v in pack["verses"]}
    assert by_vid["vEmbed"]["mount"] == "block"


def test_build_pack_missing_mount_normalizes_to_block():
    """A LEGACY manifest verse.store node (pre-#92/#93, no mount field at
    all) must normalize to "block" -- never raise, never surface as
    "embedded"."""
    manifest = _manifest_with_two_verses()
    del manifest["verse"]["store"][1]["mount"]
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    by_vid = {v["vid"]: v for v in pack["verses"]}
    assert by_vid["vEmbed"]["mount"] == "block"


# ---------------------------------------------------------------------------
# 2b. Point 1 -- footnote cited INSIDE an embedded verse must be discovered
#     and carried into footnotes_out, even though neither the carrier
#     block's own fnrefs[] nor its plain_text ever mentions it.
# ---------------------------------------------------------------------------


def _embedded_footnote_manifest(seg_id="seg01", second_segment_block=None):
    """seg_id's own block "p1" carries NO fnrefs and NO ⟦FNREF_2⟧ sentinel
    in its own plain_text -- the only place footnote 2 is cited is inside
    the embedded verse "vPoem"'s own plain_text/fnrefs. `def_block` "fn2def"
    is a real manifest block (so the footnote entry resolves), but it is
    NEVER a member of seg_id's own block_ids."""
    blocks = {
        "p1": {
            "id": "p1",
            "order_index": 0,
            "plain_text": "Some prose introducing the poem below.",
        },
        "fn2def": {
            "id": "fn2def",
            "order_index": 99,
            "plain_text": "A note about the poem's imagery.",
        },
    }
    verse_store = [
        {
            "vid": "vPoem",
            "placeholder": "⟦VERSE_vPoem⟧",
            "parent_block": "p1",
            "mount": "embedded",
            "fnrefs": [2],
            "plain_text": "Line one of the poem ⟦FNREF_2⟧\nLine two of the poem",
            "n_line": 2,
        }
    ]
    if second_segment_block is not None:
        blocks[second_segment_block["id"]] = second_segment_block["block"]
        verse_store.append(second_segment_block["verse"])

    return {
        "segments": [
            {
                "seg": seg_id,
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 12,
                "block_ids": ["p1"],
            }
        ],
        "blocks": blocks,
        "footnotes": [{"n": 2, "def_block": "fn2def"}],
        "verse": {"store": verse_store},
        "generation_hashes": _base_generation_hashes(),
    }


def test_embedded_verse_footnote_discovered_and_carried_into_footnotes_out():
    """Point 1 regression: a footnote cited ONLY inside an embedded verse's
    own text (never in the carrier block's own fnrefs[]/plain_text) must
    still surface in segpack's footnotes[] -- else it is silently dropped
    from the draft and a raw ⟦FNREF_n⟧ sentinel leaks at render (the
    #93/#106 end-to-end footnote-carry-through this fixes)."""
    manifest = _embedded_footnote_manifest()
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    assert pack["footnotes"] == [
        {"n": 2, "source_text": "A note about the poem's imagery."}
    ], pack["footnotes"]


def test_embedded_verse_footnote_scan_does_not_spuriously_warn(capsys):
    """The verse pass feeds BOTH fn_ns_recorded (from fnrefs[]) and
    fn_ns_in_text (from scanning plain_text) -- so the pre-existing
    "FNREF sentinels found in text ... disagree with manifest fnrefs[]"
    cross-check WARN must NOT fire for a footnote cited only inside an
    embedded verse."""
    manifest = _embedded_footnote_manifest()
    canon = _minimal_canon()

    SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    captured = capsys.readouterr()
    assert "disagree with manifest fnrefs" not in captured.err, captured.err


def test_embedded_verse_in_other_segment_not_scanned_for_this_segpack():
    """An embedded verse parented to a block that is NOT one of THIS
    segment's own block_ids (e.g. it belongs to a different segment) must
    never contribute its footnote citations here -- the guard is
    `parent_block in seg_block_ids`, not "any embedded verse in the whole
    manifest.verse.store"."""
    other_block = {"id": "p2", "order_index": 0, "plain_text": "A different segment's prose."}
    other_verse = {
        "vid": "vOther",
        "placeholder": "⟦VERSE_vOther⟧",
        "parent_block": "p2",
        "mount": "embedded",
        "fnrefs": [9],
        "plain_text": "A line citing ⟦FNREF_9⟧ from another segment entirely",
        "n_line": 1,
    }
    manifest = _embedded_footnote_manifest(
        second_segment_block={"id": "p2", "block": other_block, "verse": other_verse}
    )
    # footnote 9 has no def_block entry at all -- if it were (wrongly)
    # scanned for seg01, build_pack would at least WARN "no definition in
    # manifest.json footnotes[]"; assert it is never even attempted.
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    fn_ns = {fo["n"] for fo in pack["footnotes"]}
    assert fn_ns == {2}, fn_ns


def test_no_verse_store_segment_footnote_discovery_is_a_no_op():
    """Control: a segment with no verse.store entries at all must behave
    byte-identically to before this change (footnotes_out derived from
    blocks-only scanning)."""
    manifest = _embedded_footnote_manifest()
    manifest["verse"]["store"] = []
    manifest["footnotes"] = []
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    assert pack["footnotes"] == []
    assert pack["verses"] == []


# ---------------------------------------------------------------------------
# 2c. Fix C (#118 item 2) -- a footnote cited only inside a verse that is
#     itself EMBEDDED in another footnote's def block must be discovered, at
#     arbitrary nesting depth. The old single-pass scan only looked at embedded
#     verses parented to the segment's OWN block_ids, so a verse embedded in a
#     footnote-def block (whose parent_block is that def block, never a
#     block_id) was never scanned -> a footnote cited only inside it was
#     silently dropped. The worklist seeds the frontier with the segment's
#     blocks AND every discovered footnote's def block, then chases the chain to
#     a fixed point.
# ---------------------------------------------------------------------------


def _nested_footnote_manifest(deeper=False):
    """seg01's block p1 cites fn1 (a round-0 block-scan discovery). fn1's def
    block (fn1def) -- NOT one of seg01's own block_ids -- embeds a verse V2
    whose OWN text cites fn2. With ``deeper=True``, fn2's def block (fn2def) in
    turn embeds a verse V3 whose text cites fn3, one level further down."""
    blocks = {
        "p1": {"id": "p1", "order_index": 0,
               "plain_text": "Prose citing ⟦FNREF_1⟧ then quoting a poem.", "fnrefs": [1]},
        "fn1def": {"id": "fn1def", "order_index": 90,
                   "plain_text": "A note quoting a poem: ⟦VERSE_V2⟧"},
        "fn2def": {"id": "fn2def", "order_index": 91,
                   "plain_text": "A deeper note about the inner poem."},
    }
    footnotes = [{"n": 1, "def_block": "fn1def"}, {"n": 2, "def_block": "fn2def"}]
    verse_store = [
        {"vid": "V2", "placeholder": "⟦VERSE_V2⟧", "parent_block": "fn1def",
         "mount": "embedded", "fnrefs": [2],
         "plain_text": "Inner poem line ⟦FNREF_2⟧\nInner poem line two", "n_line": 2},
    ]
    if deeper:
        blocks["fn2def"]["plain_text"] = "A deeper note quoting: ⟦VERSE_V3⟧"
        blocks["fn3def"] = {"id": "fn3def", "order_index": 92,
                            "plain_text": "The deepest note."}
        footnotes.append({"n": 3, "def_block": "fn3def"})
        verse_store.append(
            {"vid": "V3", "placeholder": "⟦VERSE_V3⟧", "parent_block": "fn2def",
             "mount": "embedded", "fnrefs": [3],
             "plain_text": "Deepest poem line ⟦FNREF_3⟧", "n_line": 1}
        )
    return {
        "segments": [
            {"seg": "seg01", "title_text": "Ch1", "kind": "body",
             "word_count": 12, "block_ids": ["p1"]},
        ],
        "blocks": blocks,
        "footnotes": footnotes,
        "verse": {"store": verse_store},
        "generation_hashes": _base_generation_hashes(),
    }


def test_footnote_in_verse_embedded_in_footnote_def_is_discovered():
    """Fix C literal issue scenario: fn2, cited only inside verse V2 which is
    itself embedded in fn1's def block, must land in footnotes_out. Pre-fix the
    worklist never scanned fn1def (not one of seg01's own block_ids), so V2 was
    never scanned and fn2 was silently dropped."""
    manifest = _nested_footnote_manifest()
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    fn_ns = {fo["n"] for fo in pack["footnotes"]}
    assert fn_ns == {1, 2}, fn_ns
    by_n = {fo["n"]: fo for fo in pack["footnotes"]}
    assert by_n[2]["source_text"] == "A deeper note about the inner poem."


def test_footnote_nested_two_levels_deep_via_footnote_def_verses_is_discovered():
    """Fix C genuine fixed point (not a one-extra-level patch): fn1def embeds
    V2 (cites fn2); fn2def embeds V3 (cites fn3). All three footnotes must be
    discovered by chasing the frontier to convergence."""
    manifest = _nested_footnote_manifest(deeper=True)
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    fn_ns = {fo["n"] for fo in pack["footnotes"]}
    assert fn_ns == {1, 2, 3}, fn_ns


def test_nested_footnote_discovery_does_not_spuriously_warn(capsys):
    """The per-verse fnrefs[]/plain_text cross-check must stay consistent for a
    footnote discovered only in a LATER worklist round (fn2, surfaced by
    scanning fn1's def block) -- no stale-manifest WARN for it."""
    manifest = _nested_footnote_manifest(deeper=True)
    canon = _minimal_canon()

    SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    captured = capsys.readouterr()
    assert "disagree with manifest fnrefs" not in captured.err, captured.err


def test_footnote_def_embedded_verse_citing_nothing_adds_no_extra_footnote():
    """Control (segpack analog of assemble corpus case g): when the
    def-embedded verse V2 cites NO footnote, the worklist scans fn1def, finds
    V2, discovers nothing new, and terminates -- footnotes_out stays exactly
    {fn1}, never over-collecting."""
    manifest = _nested_footnote_manifest()
    # V2 no longer cites any footnote.
    manifest["verse"]["store"][0]["fnrefs"] = []
    manifest["verse"]["store"][0]["plain_text"] = "Inner poem line one\nInner poem line two"
    # fn2 is no longer cited anywhere -> drop its manifest entry too.
    manifest["footnotes"] = [{"n": 1, "def_block": "fn1def"}]
    canon = _minimal_canon()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "translate_all")

    fn_ns = {fo["n"] for fo in pack["footnotes"]}
    assert fn_ns == {1}, fn_ns
    # V2 (parented to fn1's def block) is still carried into verses_out.
    assert {v["vid"] for v in pack["verses"]} == {"V2"}


# ---------------------------------------------------------------------------
# 3. validate_segpack() -- hand-rolled structural check, mount/n_line
#    lockstep with segpack.schema.json's new fields (#96 finding 3).
# ---------------------------------------------------------------------------


_MISSING = object()


def _pack_with_one_verse(verse_overrides=None):
    verse = {
        "vid": "vA",
        "placeholder": "⟦VERSE_V001_deadbeef⟧",
        "parent_block": "p1",
        "mount": "embedded",
        "n_line": 3,
    }
    if verse_overrides:
        verse.update(verse_overrides)
        for key in [k for k, val in verse_overrides.items() if val is _MISSING]:
            del verse[key]
    return {
        "seg": "seg01",
        "title": "Chapter One",
        "kind": "body",
        "word_count": 4,
        "blocks": [],
        "footnotes": [],
        "verses": [verse],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def test_validate_segpack_accepts_well_formed_verse_with_mount_and_n_line():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_one_verse())
    assert errors == [], errors


def test_validate_segpack_rejects_missing_mount():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_one_verse({"mount": _MISSING}))
    assert any("must be 'block' or 'embedded'" in e for e in errors), errors


def test_validate_segpack_rejects_unknown_mount_value():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_one_verse({"mount": "poem"}))
    assert any("must be 'block' or 'embedded'" in e for e in errors), errors


def test_validate_segpack_rejects_missing_n_line():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_one_verse({"n_line": _MISSING}))
    assert any("must be a non-negative integer" in e for e in errors), errors


@pytest.mark.parametrize("bad_n_line", [-1, "12", True])
def test_validate_segpack_rejects_invalid_n_line(bad_n_line):
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_one_verse({"n_line": bad_n_line}))
    assert any("must be a non-negative integer" in e for e in errors), errors


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
