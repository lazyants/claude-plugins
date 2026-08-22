"""tests/verbatim_census.test.py -- regression-lock suite for
scripts/verbatim_census.py, the report-only census of Hebrew source text
reproduced inside a translated draft (#502).

Two things this file is built to make impossible.

**A silently EMPTY scan.** A tokenizer that produces nothing prints exactly
what a working one prints: no queue, no complaint, exit 0. So every assertion
of the form "this run is not queued" is paired with an assertion that the
census actually scanned something (`totals['runs'] > 0`), and the class
counters are read out of the artifact rather than inferred from the queue.

**A one-sided class test.** Each class is pinned twice: the case that MUST
receive it, and the adjacent case that must NOT. The negative half is where
the real defects were found during review -- `fold_match_key()` alone erases
which connector FAMILY was used, so `אב־גד` and `אב׳גד` share a key, and a
scalar suffix test lets a compound letter+family change masquerade as a
one-letter prefix.

The character-table test additionally records `unicodedata.unidata_version`
and pins named members AND non-members, because a range plus a category
filter cannot distinguish "correctly empty" from "silently empty": four empty
ranges would satisfy every negative assertion on their own.

The script is exercised as a real subprocess exactly as an operator invokes
it, plus by-file-identity import of the shipped module for the unit-level
tokenizer/classifier tests -- never a reimplementation of either.
"""
import hashlib
import importlib.util
import os
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
CENSUS_SRC = SCRIPTS_DIR / "verbatim_census.py"

assert CENSUS_SRC.is_file(), f"verbatim_census.py not found at {CENSUS_SRC}"


def _load_module():
    """The REAL shipped module, loaded by file identity with SCRIPTS_DIR on
    sys.path so its sibling imports resolve exactly as they do in a durable
    root's scripts/ directory."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("_verbatim_census_under_test", CENSUS_SRC)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


VC = _load_module()


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_segpack(seg, blocks, footnotes=(), verses=()):
    """A segpack that passes segpack.validate_segpack(). `blocks` is a list of
    (id, plain_text) pairs, or (id, None) for a block that carries only
    source_html -- the schema-valid shape this census refuses."""
    out_blocks = []
    for idx, (bid, text) in enumerate(blocks):
        entry = {"id": bid, "order_index": idx}
        if text is None:
            entry["source_html"] = "<p>x</p>"
        else:
            entry["plain_text"] = text
        out_blocks.append(entry)
    return {
        "seg": seg,
        "title": "t",
        "kind": "body",
        "word_count": 1,
        "blocks": out_blocks,
        "footnotes": [{"n": n, "source_text": t} for n, t in footnotes],
        "verses": list(verses),
        "names": [],
        "canon_names": [],
        "new_names": [],
        "canon_map": {},
        "split_names": {},
        "generation_hashes": {
            "source_extraction_hash": "0" * 8,
            "source_input_hash": "0" * 8,
            "particle_config_hash": "0" * 8,
            "derivation_bundle_hash": "0" * 8,
        },
    }


def make_draft(seg, blocks, footnotes=()):
    return {
        "seg": seg,
        "blocks": dict(blocks),
        "footnotes": {str(n): t for n, t in footnotes},
        "verses": {},
        "names": [],
        "notes": [],
    }


def write_root(tmp_path, seg, segpack, draft):
    segments = tmp_path / "segments"
    segments.mkdir(parents=True, exist_ok=True)
    (segments / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8")
    (segments / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def run_census(root, *segs, expect_exit=0):
    proc = subprocess.run(
        [sys.executable, str(CENSUS_SRC), *segs, "--durable-root", str(root)],
        capture_output=True, text=True,
    )
    assert proc.returncode == expect_exit, (
        f"expected exit {expect_exit}, got {proc.returncode}\n"
        f"stdout={proc.stdout[:400]}\nstderr={proc.stderr[:400]}"
    )
    if expect_exit != 0:
        return proc
    return json.loads(proc.stdout)


def blocks_census(tmp_path, src_blocks, draft_blocks, expect_exit=0, **segpack_kw):
    """A segpack and a draft over the same block ids, run as a subprocess.
    Each caller keeps its own fixture data; only the scaffolding is shared."""
    seg = "seg01"
    root = write_root(tmp_path, seg,
                      make_segpack(seg, src_blocks, **segpack_kw),
                      make_draft(seg, draft_blocks))
    return run_census(root, seg, expect_exit=expect_exit)


def one_seg(tmp_path, src_text, draft_text, bid="PARA:0001"):
    """The common single-block fixture: one source block, one draft block."""
    return blocks_census(tmp_path, [(bid, src_text)], [(bid, draft_text)])


# ---------------------------------------------------------------------------
# 1. The Hebrew character table is pinned, not merely filtered
# ---------------------------------------------------------------------------

def test_letter_table_named_members_and_non_members():
    members = {
        "א": "ALEF",
        "ם": "FINAL MEM",
        "ׯ": "YOD TRIANGLE (a U+05D0-U+05EA range misses this one)",
        "װ": "YIDDISH DOUBLE VAV",
        "אַ": "ALEF WITH PATAH (presentation form)",
    }
    for ch, label in members.items():
        assert ch in VC.HEBREW_LETTERS, f"{label} U+{ord(ch):04X} must be a base letter"
    non_members = {
        "﬩": "ALTERNATIVE PLUS SIGN -- a symbol, not a letter",
        "־": "MAQAF -- a connector, never a base letter",
        "׳": "GERESH -- a connector, never a base letter",
        "ָ": "QAMATS -- a mark, never a base letter",
        "ـ": "ARABIC TATWEEL",
        "Ϣ": "COPTIC SHEI -- category Lu, would survive a naive Greek range",
        "ª": "FEMININE ORDINAL INDICATOR",
        "Ω": "OHM SIGN",
        "a": "ASCII latin",
    }
    for ch, label in non_members.items():
        assert ch not in VC.HEBREW_LETTERS, f"{label} U+{ord(ch):04X} must NOT be a base letter"


def test_letter_table_is_not_silently_empty_and_is_all_category_L():
    # A range plus a category filter cannot tell "correctly empty" from
    # "silently empty" -- so pin the size band and the category invariant.
    assert len(VC.HEBREW_LETTERS) >= 27 + 3, len(VC.HEBREW_LETTERS)
    assert all(unicodedata.category(c).startswith("L") for c in VC.HEBREW_LETTERS)
    assert all(unicodedata.category(c) == "Mn" for c in VC.HEBREW_MARKS)
    assert unicodedata.unidata_version  # recorded in the payload; see test below


def test_unassigned_and_nameless_codepoints_neither_crash_nor_join_a_run():
    for cp in (0x0000, 0xE000, 0x17000, 0x05EB):
        text = "א" + chr(cp) + "ב"
        assert VC.hebrew_runs(text) == [], (
            f"U+{cp:04X} must break the run, not join it: {VC.hebrew_runs(text)}"
        )


# ---------------------------------------------------------------------------
# 2-6. Tokenizer
# ---------------------------------------------------------------------------

def test_a_run_of_standalone_marks_invents_nothing():
    assert VC.hebrew_runs("ַָ֑") == []


def test_a_foreign_mark_ends_the_run_rather_than_joining_it():
    # Arabic fatha between two Hebrew letters: two short runs stay visible
    # instead of one run whose foreign mark a blanket fold would erase.
    runs = VC.hebrew_runs("אבَגד")
    assert runs == ["אב", "גד"], runs


def test_threshold_counts_characters_not_base_letters():
    # `וַ` and `וְ` are one base letter plus one mark: two code points each,
    # semantically different, ordinary pointed Hebrew. Counting BASE LETTERS
    # would drop them and the frozen "2+ chars" contract with them.
    assert VC.hebrew_runs("וַ") == ["וַ"]
    assert VC.hebrew_runs("ו") == []


def test_maqaf_and_nbhyphen_join_one_run_but_a_trailing_geresh_does_not():
    assert VC.hebrew_runs("שמו־עש") == ["שמו־עש"]
    assert VC.hebrew_runs("אב‑גד") == ["אב‑גד"]
    # `א׳` is one character after the connector is refused: not scanned.
    assert VC.hebrew_runs("א׳") == []
    assert VC.hebrew_runs("אב׳") == ["אב"]


def test_ascii_quote_joins_two_hebrew_letters_but_never_two_latin_ones():
    assert VC.hebrew_runs('א"ב') == ['א"ב']
    assert VC.hebrew_runs('a"b') == []


def test_placeholder_is_masked_equal_length_and_never_joins_two_runs():
    masked = VC.mask_placeholders("אב⟦FNREF_1⟧גד", set())
    assert len(masked) == len("אב⟦FNREF_1⟧גד")
    assert VC.hebrew_runs(masked) == ["אב", "גד"]


def test_an_undeclared_bracketed_span_is_source_prose_and_is_NOT_masked():
    # The negative half of the masking rule. bootstrap_names.mask_sentinels()
    # would erase this span and the run inside it; the census must not.
    text = "⟦אבגד⟧"
    assert VC.hebrew_runs(VC.mask_placeholders(text, set())) == ["אבגד"]


def test_a_declared_verse_placeholder_IS_masked():
    # The span must carry Hebrew, or this passes with masking replaced by the
    # identity function: `⟦POEM_1⟧` has no Hebrew, so hebrew_runs() returns []
    # either way and the assertion pins nothing. Paired with the negative
    # directly above, which is the same span left UNdeclared.
    assert VC.hebrew_runs(VC.mask_placeholders("⟦אבגד⟧", {"⟦אבגד⟧"})) == []
    assert VC.hebrew_runs(VC.mask_placeholders("⟦אבגד⟧", set())) == ["אבגד"]


# ---------------------------------------------------------------------------
# 7-12. Classification, each class two-sided
# ---------------------------------------------------------------------------

def test_verbatim_is_not_queued_but_one_changed_letter_is(tmp_path):
    same = one_seg(tmp_path / "a", "he said שלום here", "he said שלום there")
    assert same["totals"]["runs"] > 0
    assert same["queue"] == []
    assert same["totals"]["verbatim"] == 1

    diff = one_seg(tmp_path / "b", "he said שלום here", "he said שלומ there")
    assert diff["totals"]["letter_diff"] == 1
    assert diff["queue"][0]["class"] == "letter_diff"
    assert diff["queue"][0]["distance"] == 1


def test_verbatim_other_unit_and_its_own_unit_negative(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג"), ("B2", "nothing שלום")]),
        make_draft(seg, [("B1", "quote שלום"), ("B2", "שלום again")]),
    )
    payload = run_census(root, seg)
    rows = {r["unit"]: r for r in payload["queue"]}
    assert "blocks:B1" in rows, payload["queue"]
    assert rows["blocks:B1"]["class"] == "verbatim_other_unit"
    assert rows["blocks:B1"]["tier"] == 4
    # B2 reproduces it inside its OWN unit: verbatim, not queued.
    assert "blocks:B2" not in rows
    assert payload["totals"]["verbatim"] == 1


def test_verbatim_other_unit_never_bleeds_across_segments(tmp_path):
    root = tmp_path
    for seg, src, drf in (("seg01", "אבג", "אבג"),
                          ("seg02", "קרש", "אבג")):
        write_root(root, seg, make_segpack(seg, [("B1", src)]), make_draft(seg, [("B1", drf)]))
    payload = run_census(root, "seg01", "seg02")
    seg02 = [r for r in payload["queue"] if r["seg"] == "seg02"]
    assert len(seg02) == 1
    assert seg02[0]["class"] == "letter_diff", seg02


def test_fold_equal_fires_in_both_pointing_directions(tmp_path):
    poorer = one_seg(tmp_path / "a", "אָבָ", "אב")
    richer = one_seg(tmp_path / "b", "אב", "אָבָ")
    for payload in (poorer, richer):
        assert payload["totals"]["fold_equal"] == 1, payload["totals"]
        assert payload["queue"][0]["tier"] == 3


def test_fold_equal_covers_the_connector_variant_axis(tmp_path):
    # מ׳וועט (geresh) vs מ'וועט (ASCII apostrophe) -- the same connector
    # family spelled two ways, measured 80-of-84 present in the segpack.
    payload = one_seg(tmp_path, "מ׳וועט", "מ'וועט")
    assert payload["totals"]["fold_equal"] == 1, payload["totals"]


def test_fold_equal_negative_controls(tmp_path):
    # (a) the field control: two unrelated words that are mark-fold neighbours.
    ader = one_seg(tmp_path / "a", "אָבֶׁער", "אדער")
    assert ader["totals"]["fold_equal"] == 0, ader["totals"]
    assert ader["totals"]["letter_diff"] == 1

    # (b) a consonant change is never a mark difference.
    cons = one_seg(tmp_path / "b", "אבג", "אבד")
    assert cons["totals"]["fold_equal"] == 0
    assert cons["totals"]["letter_diff"] == 1

    # (c) CROSS-FAMILY: hyphen family vs apostrophe family. fold_match_key()
    # alone gives both `אב גד` and would call this a mere variant; the
    # connector-family signature is what keeps them apart.
    fam = one_seg(tmp_path / "c", "אב־גד", "אב׳גד")
    assert fam["totals"]["fold_equal"] == 0, fam["totals"]
    assert fam["totals"]["letter_diff"] == 1


def test_a_mark_only_word_swap_is_still_QUEUED(tmp_path):
    # שֵׁם ("name") vs שָׁם ("there") fold to the same key. This is exactly why
    # no class is suppressed: a real semantic corruption lands in tier 3, and
    # tier 3 is READ, not dropped.
    payload = one_seg(
        tmp_path,
        "שֵׁם",
        "שָׁם",
    )
    assert payload["totals"]["fold_equal"] == 1
    assert payload["totals"]["queued"] == 1
    assert payload["queue"][0]["class"] == "fold_equal"


def test_prefix_attached_and_its_two_negatives(tmp_path):
    # חֻפָה quoted out of source הַחֻפָה: one leading letter, tier 2.
    hit = one_seg(tmp_path / "a", "הַחֻפָה", "חֻפָה")
    assert hit["totals"]["prefix_attached"] == 1, hit["totals"]
    assert hit["queue"][0]["tier"] == 2

    # A NON-PROCLITIC head is a letter difference, not a quoted-without-prefix
    # reading: alef does not fuse onto a following word. Accepting any Hebrew
    # letter here mis-tiered 7 rows of the live 42-draft book.
    alef = one_seg(tmp_path / "d", "אבג", "בג")
    assert alef["totals"]["prefix_attached"] == 0, alef["totals"]
    assert alef["totals"]["letter_diff"] == 1

    # A TWO-letter head is not the proclitic class.
    two = one_seg(tmp_path / "b", "אבגדה", "גדה")
    assert two["totals"]["prefix_attached"] == 0, two["totals"]
    assert two["totals"]["letter_diff"] == 1

    # Cross-family compound: source אב־גד vs draft ב׳גד changes BOTH a letter
    # and the connector family. On the space-joined scalar key it looks like a
    # one-letter head; the structural predicate must reject it.
    compound = one_seg(tmp_path / "c", "אב־גד", "ב׳גד")
    assert compound["totals"]["prefix_attached"] == 0, compound["totals"]


def test_the_ladder_prefers_an_own_unit_reading_over_an_exact_match_elsewhere(tmp_path):
    """The overlap the ladder decides deliberately: a run its own unit already
    explains is reported that way even when the identical string also sits
    verbatim in another unit. Pinned in both directions so the order cannot be
    changed silently."""
    seg = "seg01"
    # prefix vs other-unit: own unit has השלום, another unit has שלום exactly.
    root = write_root(
        tmp_path / "a", seg,
        make_segpack(seg, [("B1", "השלום"), ("B2", "שלום")]),
        make_draft(seg, [("B1", "שלום"), ("B2", "שלום")]),
    )
    payload = run_census(root, seg)
    b1 = [r for r in payload["queue"] if r["unit"] == "blocks:B1"]
    assert len(b1) == 1 and b1[0]["class"] == "prefix_attached", payload["queue"]

    # fold vs other-unit: own unit has the pointed spelling, another the bare.
    root = write_root(
        tmp_path / "b", seg,
        make_segpack(seg, [("B1", "אָבָ"), ("B2", "אב")]),
        make_draft(seg, [("B1", "אב"), ("B2", "אב")]),
    )
    payload = run_census(root, seg)
    b1 = [r for r in payload["queue"] if r["unit"] == "blocks:B1"]
    assert len(b1) == 1 and b1[0]["class"] == "fold_equal", payload["queue"]

    # and the fall-through: a run its own unit cannot explain at all.
    root = write_root(
        tmp_path / "c", seg,
        make_segpack(seg, [("B1", "קרש"), ("B2", "אבג")]),
        make_draft(seg, [("B1", "אבג"), ("B2", "אבג")]),
    )
    payload = run_census(root, seg)
    b1 = [r for r in payload["queue"] if r["unit"] == "blocks:B1"]
    assert len(b1) == 1 and b1[0]["class"] == "verbatim_other_unit", payload["queue"]


def test_the_queue_is_ordered_by_tier_then_distance_across_tiers(tmp_path):
    """Acceptance criterion 3, non-vacuously: several tiers AND two different
    measured distances, emitted in deliberately wrong discovery order. An
    assertion over two tier-1 rows alone stays green if the tier key is
    removed altogether."""
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [
            ("B1", "אָבָ"),        # -> fold_equal      (tier 3)
            ("B2", "השלום"),      # -> prefix_attached  (tier 2)
            ("B3", "קרשת"),       # -> letter_diff d=2  (tier 1)
            ("B4", "גדול"),       # -> letter_diff d=1  (tier 1)
        ]),
        make_draft(seg, [
            ("B1", "אב"),
            ("B2", "שלום"),
            ("B3", "קרבן"),
            ("B4", "גדולה"),
        ]),
    )
    payload = run_census(root, seg)
    got = [(r["tier"], r["distance"]) for r in payload["queue"]]
    assert got == sorted(got), got
    assert [r["tier"] for r in payload["queue"]] == [1, 1, 2, 3], payload["queue"]
    tier1 = [r["distance"] for r in payload["queue"] if r["tier"] == 1]
    assert tier1 == sorted(tier1) and len(set(tier1)) == 2, tier1


def test_a_genuine_dropped_leading_letter_is_queued(tmp_path):
    # שלום -> לום. Structurally identical to the proclitic case, so it lands
    # in tier 2 -- and tier 2 is queued. Suppressing that tier would hide it.
    payload = one_seg(tmp_path, "שלום", "לום")
    assert payload["totals"]["prefix_attached"] == 1
    assert payload["totals"]["queued"] == 1


def test_letter_diff_on_the_confirmed_field_instance(tmp_path):
    # ווייטער with a dropped yod, read word-by-word in the field and confirmed
    # as a genuine corruption.
    src = "וַויּיטֶׁער"
    drf = "וַויּטֶׁער"
    payload = one_seg(tmp_path, src, drf)
    assert payload["totals"]["letter_diff"] == 1, payload["totals"]
    assert payload["queue"][0]["distance"] == 1
    assert payload["queue"][0]["nearest_source_run"] == src
    assert payload["queue"][0]["nearest_is_advisory"] is True


def test_no_source_run_carries_null_distance_and_sorts_last_in_its_tier(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "no hebrew at all"), ("B2", "אבג")]),
        make_draft(seg, [("B1", "קרש"), ("B2", "אבד")]),
    )
    payload = run_census(root, seg)
    assert payload["totals"]["no_source_run"] == 1, payload["totals"]
    rows = {r["class"]: r for r in payload["queue"]}
    assert rows["no_source_run"]["distance"] is None
    assert rows["no_source_run"]["nearest_source_run"] is None
    # Both are tier 1; the null-distance row sorts after the measured one.
    assert [r["class"] for r in payload["queue"]] == ["letter_diff", "no_source_run"]


def test_nearest_is_chosen_on_the_folded_form_not_the_raw_one(tmp_path):
    # Draft אָבֶג. Candidate אבד is 1 away folded but 3 away raw; candidate
    # אָכֶד is 2 away either way. Choosing on the RAW form would report the
    # wrong nearest word, which is the ranking this tier depends on.
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבד אָכֶד")]),
        make_draft(seg, [("B1", "אָבֶג")]),
    )
    payload = run_census(root, seg)
    row = payload["queue"][0]
    assert row["nearest_source_run"] == "אבד", row
    assert row["distance"] == 1, row


def test_duplicate_occurrences_are_each_listed(tmp_path):
    payload = one_seg(tmp_path, "אבג", "אבד and אבד")
    assert payload["totals"]["runs"] == 2
    assert payload["totals"]["queued"] == 2


def test_a_declared_verse_placeholder_is_masked_end_to_end(tmp_path):
    """The segpack.verses[] -> _placeholder_strings() -> mask_placeholders()
    wire, which the unit tests above exercise only with a hand-built set. The
    declared placeholder carries HEBREW on purpose: with an ASCII one the
    assertion would hold whether or not masking ran."""
    verse = {"vid": "V1", "placeholder": "⟦אבגד⟧", "parent_block": "B1",
             "mount": "block", "n_line": 0}
    payload = blocks_census(
        tmp_path,
        [("B1", "⟦אבגד⟧ קרש")],
        {"B1": "⟦אבגד⟧ קרשת"},
        verses=[verse],
    )
    runs = [r["run"] for r in payload["queue"]]
    assert "אבגד" not in runs, payload["queue"]
    assert payload["totals"]["runs"] == 1, payload["totals"]
    assert runs == ["קרשת"], payload["queue"]


def test_footnotes_are_scanned_against_their_own_source_text(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג")], footnotes=[(1, "קרש")]),
        make_draft(seg, [("B1", "אבג")], footnotes=[(1, "קרת")]),
    )
    payload = run_census(root, seg)
    assert payload["queue"][0]["unit"] == "footnotes:1", payload["queue"]
    assert payload["queue"][0]["class"] == "letter_diff"


# ---------------------------------------------------------------------------
# 13-16. Source field, exit contract, report-only
# ---------------------------------------------------------------------------

def test_plain_text_is_what_is_read_not_source_html(tmp_path):
    # The block carries BOTH; only source_html contains the draft's run. If
    # the census fell back to markup (as _block_source_text() does) this would
    # come back verbatim.
    seg = "seg01"
    segpack = make_segpack(seg, [("B1", "קרש")])
    segpack["blocks"][0]["source_html"] = "<p>אבג</p>"
    root = write_root(tmp_path, seg, segpack, make_draft(seg, [("B1", "אבג")]))
    payload = run_census(root, seg)
    assert payload["totals"]["verbatim"] == 0, payload["totals"]
    assert payload["totals"]["queued"] == 1


def test_a_block_without_plain_text_is_refused_by_name(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג"), ("B2", None)]),
        make_draft(seg, [("B1", "אבג"), ("B2", "x")]),
    )
    proc = run_census(root, seg, expect_exit=2)
    assert "plain_text" in proc.stderr and "blocks:B2" in proc.stderr


def test_a_project_with_no_hebrew_source_is_refused_not_reported_as_clean(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "purely latin source")]),
        make_draft(seg, [("B1", "purely latin draft")]),
    )
    proc = run_census(root, seg, expect_exit=2)
    assert "Hebrew" in proc.stderr


@pytest.mark.parametrize("mutate,label", [
    (lambda d: d.update(blocks=[]), "blocks is a list, not an object"),
    (lambda d: d.update(seg="seg99"), "cross-wired seg"),
    (lambda d: d.pop("verses"), "missing required key"),
])
def test_a_malformed_draft_exits_2_never_1(tmp_path, mutate, label):
    seg = "seg01"
    draft = make_draft(seg, [("B1", "אבג")])
    mutate(draft)
    root = write_root(tmp_path, seg, make_segpack(seg, [("B1", "אבג")]), draft)
    run_census(root, seg, expect_exit=2)


def test_a_malformed_segpack_exits_2_never_1(tmp_path):
    seg = "seg01"
    segpack = make_segpack(seg, [("B1", "אבג")])
    # segpack.validate_segpack() is not total over JSON values: a canon_names
    # member that is itself a list raises TypeError out of its own set
    # construction. The census must convert that into its own exit 2.
    segpack["canon_names"] = [[]]
    root = write_root(tmp_path, seg, segpack, make_draft(seg, [("B1", "אבג")]))
    proc = run_census(root, seg, expect_exit=2)
    assert "malformed" in proc.stderr or "invalid" in proc.stderr


def test_a_duplicate_footnote_number_is_refused(tmp_path):
    """validate_segpack() type-checks `n` but never asserts uniqueness, so two
    footnotes numbered 1 are schema-valid. Letting the later one win compared
    the draft against a source text chosen by list order: the draft's exact
    reproduction of the FIRST footnote came back `verbatim_other_unit`."""
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג")], footnotes=[(1, "אבג"), (1, "קרש")]),
        make_draft(seg, [("B1", "אבג")], footnotes=[(1, "אבג")]),
    )
    proc = run_census(root, seg, expect_exit=2)
    assert "duplicate footnote number" in proc.stderr


def test_a_repeated_segment_argument_is_refused(tmp_path):
    """`nargs="+"` accepts a repeat. Scanning the segment twice appended its
    queue rows twice while OVERWRITING its per_segment counts, so
    `queued == runs - verbatim` -- the invariant the field-check cast asserts
    -- came out false."""
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג")]),
        make_draft(seg, [("B1", "אבד")]),
    )
    proc = run_census(root, seg, seg, expect_exit=2)
    assert "repeated" in proc.stderr
    # and the single-pass form still holds the invariant it protects
    payload = run_census(root, seg)
    assert payload["totals"]["queued"] == (
        payload["totals"]["runs"] - payload["totals"]["verbatim"])


def test_a_path_unsafe_segment_id_is_refused(tmp_path):
    root = write_root(
        tmp_path, "seg01",
        make_segpack("seg01", [("B1", "אבג")]),
        make_draft("seg01", [("B1", "אבג")]),
    )
    run_census(root, "../etc/passwd", expect_exit=2)


def test_there_is_no_out_flag(tmp_path):
    root = write_root(
        tmp_path, "seg01",
        make_segpack("seg01", [("B1", "אבג")]),
        make_draft("seg01", [("B1", "אבג")]),
    )
    proc = subprocess.run(
        [sys.executable, str(CENSUS_SRC), "seg01", "--durable-root", str(root),
         "--out", str(tmp_path / "anywhere.json")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2, proc.stdout
    assert "unrecognized arguments" in proc.stderr


def test_a_non_empty_queue_still_exits_0_and_writes_nothing(tmp_path):
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג")]),
        make_draft(seg, [("B1", "אבד")]),
    )
    before = {p: hashlib.sha1(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob("*")) if p.is_file()}
    payload = run_census(root, seg)          # exit 0 asserted inside
    assert payload["totals"]["queued"] == 1
    after = {p: hashlib.sha1(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob("*")) if p.is_file()}
    assert before == after, "the census must not modify or add any file"


def test_a_stdout_that_cannot_encode_hebrew_exits_2_not_1(tmp_path):
    """The payload is Hebrew by construction. With an ASCII stdout the write
    raises UnicodeEncodeError; outside the handled region that surfaced as
    exit 1 -- the one status this script promises never to use for an
    environment failure."""
    seg = "seg01"
    root = write_root(
        tmp_path, seg,
        make_segpack(seg, [("B1", "אבג")]),
        make_draft(seg, [("B1", "אבד")]),
    )
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    proc = subprocess.run(
        [sys.executable, str(CENSUS_SRC), seg, "--durable-root", str(root)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout[:200], proc.stderr[:300])
    assert "UTF-8" in proc.stderr


def test_the_payload_records_what_it_scanned_and_how(tmp_path):
    payload = one_seg(tmp_path, "אבג", "אבד")
    assert payload["source_script"] == "hebrew"
    assert payload["scanned_fields"] == ["blocks", "footnotes"]
    assert payload["min_run_chars"] == 2
    assert payload["tier_is_likelihood_only"] is True
    assert payload["unidata_version"] == unicodedata.unidata_version
    assert payload["totals"]["runs"] > 0


def test_self_anchoring_finds_segments_without_the_flag(tmp_path):
    """Production copies scripts/ into the durable root; the script must find
    segments/ from its OWN location, never from cwd."""
    root = tmp_path / "root"
    shutil.copytree(SCRIPTS_DIR, root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    seg = "seg01"
    write_root(root, seg, make_segpack(seg, [("B1", "אבג")]),
               make_draft(seg, [("B1", "אבד")]))
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "verbatim_census.py"), seg],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[:400]
    assert json.loads(proc.stdout)["totals"]["queued"] == 1
    # "writes nothing" includes Python's own bytecode cache: importing the
    # three siblings would otherwise leave scripts/__pycache__ behind in the
    # operator's durable root.
    assert not list((root / "scripts").glob("__pycache__")), \
        "the census must not write a bytecode cache into the durable root"