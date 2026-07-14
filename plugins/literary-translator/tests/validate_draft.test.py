"""tests/validate_draft.test.py -- regression-lock suite for
scripts/validate_draft.py, the false-green gate generalized from
references/false-green-gate.md's six invariants.

Each test below builds a real durable_root fixture (ownership marker,
profile.yml, segments/segpack_{seg}.json, segments/{seg}.draft.json) on disk
and invokes the ACTUAL validate_draft.py as a subprocess -- the exact way it
is invoked in production (`python3 {durable_root}/scripts/validate_draft.py
SEG`) -- so its Path(__file__)-based self-anchoring resolves against the
isolated fixture root rather than this repo's real assets/scripts directory.

Per known failure class, one deliberately-injected-defect fixture asserts the
gate FAILS (exit 1) with the expected diagnostic. This is a regression lock:
if the corresponding check is ever weakened or removed from validate_draft.py,
the injected defect would silently pass (exit 0) and these tests would break.

Failure classes covered (see references/false-green-gate.md, six-check spec):
  - empty footnote translation (check 4)
  - swapped verse placeholder, breaking the per-block parent_block bijection
    (check 3 -- a flat set-membership check would miss this; the bijection
    check must not)
  - dropped sentinel, breaking a prose block's placeholder multiset (check 2)
  - whitespace-only "distinct" verse under full_rhymed_plus_literal --
    rendered/literal_gloss differ only by whitespace, so they must be flagged
    as NOT distinct after normalization (check 5)
  - dropped body_ref_markers marker under apparatus_policy=body_refs_only,
    breaking the sentinel-lite multiset-count check (check 6)

A clean baseline fixture (and a body_refs_only clean companion) proves the
harness itself is sound, isolating each injected defect as the SOLE cause of
its fixture's failure.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "validate_draft.py"
)

assert SCRIPT_SRC.is_file(), f"validate_draft.py not found at {SCRIPT_SRC}"

FN_PH = "⟦FNREF_1⟧"          # footnote-anchor sentinel: FNREF_1
V_PH_A = "⟦VERSE_vA⟧"        # standalone-verse placeholder for vA
V_PH_B = "⟦VERSE_vB⟧"        # standalone-verse placeholder for vB

DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path, profile=None):
    """Build an isolated durable_root: copies the REAL validate_draft.py into
    {root}/scripts/ (so its self-anchoring `Path(__file__).resolve().parents[1]`
    resolves to THIS temp root, exactly matching how it is actually invoked in
    production -- never assumes cwd == durable_root, never takes a
    --durable-root flag), writes the ownership marker + profile.yml, and
    creates segments/."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "validate_draft.py")
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(
        yaml.safe_dump(profile if profile is not None else DEFAULT_PROFILE, sort_keys=False),
        encoding="utf-8",
    )

    marker = {"owner_profile_path": str(profile_path)}
    (root / ".literary-translator-root.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    return root


def write_segment(root, seg, segpack, draft):
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    (segments_dir / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def run_validate(root, seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_draft.py"), seg],
        capture_output=True,
        text=True,
        timeout=30,
    )


def defect_count(stdout):
    """Extract the N from validate_draft.py's own `[{seg}] FAIL (N defects):`
    summary line -- lets a test assert the injected defect is the ONLY
    problem the gate found (isolating the failure class), not just that
    *some* defect fired."""
    m = re.search(r"FAIL \((\d+) defects?\)", stdout)
    assert m, f"expected a 'FAIL (N defects)' summary line, got:\n{stdout}"
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Shared clean baseline: one prose block carrying a footnote-anchor sentinel,
# two standalone verse blocks each parented to their OWN verse via the
# per-block bijection, one footnote -- everything valid under
# verse_policy.mode=full_rhymed_plus_literal / apparatus_policy=translate_all.
# ---------------------------------------------------------------------------

def clean_segpack(seg="seg01"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>",
            },
            {
                "id": "vblockA",
                "order_index": 1,
                "source_html": "<p>Premiere ligne du poeme<br/>Deuxieme ligne du poeme</p>",
            },
            {
                "id": "vblockB",
                "order_index": 2,
                "source_html": "<p>Autre premiere ligne<br/>Autre deuxieme ligne</p>",
            },
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def clean_draft(seg="seg01"):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached.",
            "vblockA": V_PH_A,
            "vblockB": V_PH_B,
        },
        "footnotes": {"1": "A translated note in English."},
        "verses": {
            "vA": {
                "rendered": "First line rendered so\nSecond line rendered so",
                "literal_gloss": (
                    "The first line means one thing, the second line means "
                    "another thing entirely"
                ),
            },
            "vB": {
                "rendered": "Another line rendered here\nAnother second line here",
                "literal_gloss": (
                    "This gloss says something completely different from "
                    "the rendering above"
                ),
            },
        },
        "names": [],
        "notes": [],
    }


def test_clean_baseline_passes(tmp_path):
    """Sanity check for the harness itself: a fully valid draft (correct key
    sets, correct per-block verse bijection, distinct rendered/literal_gloss,
    intact placeholders, non-empty footnote) clears the gate with exit 0.
    Every defect test below is a single, isolated mutation of this baseline."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_validate(root, "seg01")

    assert result.returncode == 0, (
        f"expected clean baseline to pass, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg01] OK" in result.stdout


# ---------------------------------------------------------------------------
# 1. Empty footnote translation (check 4).
# ---------------------------------------------------------------------------

def test_empty_footnote_fails_gate(tmp_path):
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    draft["footnotes"]["1"] = ""  # injected defect: dropped/blanked translation

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"an empty footnote translation must fail the gate, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "[FN:1] empty translation" in result.stdout
    assert defect_count(result.stdout) == 1


# ---------------------------------------------------------------------------
# 2. Swapped verse placeholder -- breaks the per-block parent_block
#    bijection (check 3). A flat set-membership check would still see both
#    placeholders as members of the source's global placeholder set and
#    wrongly pass; the per-block bijection check must not.
# ---------------------------------------------------------------------------

def test_swapped_verse_placeholder_breaks_bijection(tmp_path):
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    # injected defect: vblockA now carries vB's placeholder and vice versa.
    draft["blocks"]["vblockA"], draft["blocks"]["vblockB"] = (
        draft["blocks"]["vblockB"],
        draft["blocks"]["vblockA"],
    )

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"a swapped verse placeholder must fail the gate, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    assert (
        f"[vblockA] VERSE block must equal its OWN placeholder {V_PH_A!r}"
        in result.stdout
    )
    assert (
        f"[vblockB] VERSE block must equal its OWN placeholder {V_PH_B!r}"
        in result.stdout
    )
    assert defect_count(result.stdout) == 2


# ---------------------------------------------------------------------------
# 3. Dropped sentinel -- breaks a prose block's placeholder MULTISET
#    (check 2).
# ---------------------------------------------------------------------------

def test_dropped_sentinel_breaks_placeholder_multiset(tmp_path):
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    # injected defect: the ⟦FNREF_1⟧ footnote-anchor sentinel is dropped from
    # the translated prose block entirely.
    draft["blocks"]["p1"] = "Some translated prose with a note attached."

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"a dropped sentinel must fail the gate, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}"
    )
    assert f"[p1] placeholder mismatch: src=['{FN_PH}'] draft=[]" in result.stdout
    assert defect_count(result.stdout) == 1


# ---------------------------------------------------------------------------
# 4. Whitespace-only "distinct" verse under full_rhymed_plus_literal -- the
#    post-normalization distinctness check (check 5) must catch a
#    rendered/literal_gloss pair that LOOKS different byte-for-byte (newline
#    vs space) but collapses to the identical string once whitespace runs are
#    normalized -- i.e. a mere re-wrap, not a real rhymed rendering.
# ---------------------------------------------------------------------------

def test_whitespace_only_distinct_verse_fails_gate(tmp_path):
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    # injected defect: literal_gloss is rendered's own text with the newline
    # swapped for extra spaces -- byte-different, but IDENTICAL after
    # collapsing whitespace runs (validate_draft.py's own _norm_ws check).
    draft["verses"]["vA"]["rendered"] = "First line rendered so\nSecond line rendered so"
    draft["verses"]["vA"]["literal_gloss"] = "First line rendered so    Second line rendered so"

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"a whitespace-only-distinct verse must fail the gate, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    assert (
        "[vA] rendered == literal_gloss up to whitespace" in result.stdout
    )
    assert defect_count(result.stdout) == 1


# ---------------------------------------------------------------------------
# 5. Dropped body_refs_only marker -- breaks the sentinel-lite
#    body_ref_markers[] multiset-count check (check 6), which runs ONLY
#    under apparatus_policy=body_refs_only.
# ---------------------------------------------------------------------------

BODY_REFS_PROFILE = {
    "verse_policy": {"mode": "skip"},
    "footnotes": {"apparatus_policy": "body_refs_only"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def body_refs_segpack(seg="seg02"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "b1",
                "order_index": 0,
                "plain_text": "There was a note here [1] in the original text.",
                "body_ref_markers": ["[1]"],
            }
        ],
        "footnotes": [],
        "verses": [],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def body_refs_draft(seg="seg02", marker_present=True):
    text = (
        "There was a note here [1] in the translated text."
        if marker_present
        else "There was a note here in the translated text."
    )
    return {
        "seg": seg,
        "blocks": {"b1": text},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }


def test_body_refs_only_marker_present_passes(tmp_path):
    """Clean companion for the defect test below: the recorded body_ref
    marker DOES survive into the translated text -- must pass, proving the
    fixture's only problem in the next test is the dropped marker."""
    root = make_durable_root(tmp_path, profile=BODY_REFS_PROFILE)
    write_segment(root, "seg02", body_refs_segpack(), body_refs_draft(marker_present=True))

    result = run_validate(root, "seg02")

    assert result.returncode == 0, (
        f"expected marker-present body_refs_only draft to pass, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg02] OK" in result.stdout


def test_dropped_body_refs_only_marker_fails_gate(tmp_path):
    root = make_durable_root(tmp_path, profile=BODY_REFS_PROFILE)
    # injected defect: the literal "[1]" marker recorded in body_ref_markers[]
    # is dropped from the translated block entirely.
    write_segment(
        root, "seg02", body_refs_segpack(), body_refs_draft(marker_present=False)
    )

    result = run_validate(root, "seg02")

    assert result.returncode == 1, (
        f"a dropped body_refs_only marker must fail the gate, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    assert (
        "[b1] body_ref marker '[1]' count mismatch: recorded=1 draft=0"
        in result.stdout
    )
    assert defect_count(result.stdout) == 1


# ---------------------------------------------------------------------------
# 6. #96 -- embedded verse (mount=="embedded") skips the per-block
#    placeholder bijection (check 3) and threads n_line straight off the
#    manifest verse node (check 5), instead of deriving it from the WHOLE
#    prose carrier block's own source text.
# ---------------------------------------------------------------------------

V_PH_MIXED = "⟦VERSE_vMixed⟧"

MIXED_BY_LENGTH_PROFILE = {
    "verse_policy": {"mode": "mixed_by_length", "threshold_lines": 3},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def embedded_mixed_segpack(seg="seg03"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"Some prose introducing a poem {V_PH_MIXED} right here.",
            },
        ],
        "footnotes": [],
        "verses": [
            {
                "vid": "vMixed",
                "placeholder": V_PH_MIXED,
                "parent_block": "p1",
                "mount": "embedded",
                "n_line": 5,
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def embedded_mixed_draft(seg="seg03"):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose introducing a poem {V_PH_MIXED} right here.",
        },
        "footnotes": {},
        "verses": {
            "vMixed": {
                "rendered": "First rendered line\nSecond rendered line",
                "literal_gloss": "A literal gloss that says something else entirely",
            },
        },
        "names": [],
        "notes": [],
    }


def test_embedded_verse_mixed_by_length_uses_threaded_n_line(tmp_path):
    """#96 regression: an EMBEDDED verse (mount=="embedded") under
    verse_policy.mode=mixed_by_length must resolve its effective mode from
    the manifest-threaded n_line (5 >= threshold_lines=3 ->
    full_rhymed_plus_literal), not from _source_line_count() of the whole
    PROSE carrier block (irrelevant to this inline verse's own line count).
    Pre-fix, check 3 ALSO unconditionally added this verse to
    parent_block_claims and then demanded the carrier's ENTIRE translated
    text equal the verse's bare placeholder -- the false per-block
    bijection this cluster fixes -- so this fixture fails that way on
    pristine code."""
    root = make_durable_root(tmp_path, profile=MIXED_BY_LENGTH_PROFILE)
    write_segment(root, "seg03", embedded_mixed_segpack(), embedded_mixed_draft())

    result = run_validate(root, "seg03")

    assert result.returncode == 0, (
        f"expected the embedded-verse mixed_by_length draft to pass, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg03] OK" in result.stdout


V_PH_FNPOEM = "⟦VERSE_vFnPoem⟧"

LITERAL_ONLY_PROFILE = {
    "verse_policy": {"mode": "literal_only"},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def fn_embedded_segpack(seg="seg04"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"Some prose with a note {FN_PH} attached.",
            },
        ],
        "footnotes": [
            {"n": 1, "source_text": f"A note quoting a poem {V_PH_FNPOEM} within it."}
        ],
        "verses": [
            {
                "vid": "vFnPoem",
                "placeholder": V_PH_FNPOEM,
                # parent_block is the FOOTNOTE-DEF block's own id -- NEVER a
                # member of this segpack's own blocks[] (a footnote-def
                # block is not a segment block).
                "parent_block": "fn1def",
                "mount": "embedded",
                "n_line": 2,
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def fn_embedded_draft(seg="seg04"):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached.",
        },
        "footnotes": {
            "1": f"A translated note quoting a poem {V_PH_FNPOEM} within it.",
        },
        "verses": {
            "vFnPoem": {"literal_gloss": "A plain literal rendering of the quoted poem."},
        },
        "names": [],
        "notes": [],
    }


def test_embedded_verse_parented_to_footnote_def_block_passes_gate(tmp_path):
    """#96 regression: an embedded verse quoted INSIDE a footnote definition
    has parent_block == that footnote-def block's own id, which is NEVER a
    member of this segpack's own blocks[] (footnote-def blocks aren't
    segment blocks). Pre-fix, check 3 unconditionally added every verse to
    parent_block_claims and then demanded parent_block be a key of
    block_meta -- false-firing a 'SOURCE DEFECT ... not found among this
    segpack's blocks' for every footnote-embedded verse. Post-fix, check 3
    skips mount=='embedded' entries entirely, so this passes clean."""
    root = make_durable_root(tmp_path, profile=LITERAL_ONLY_PROFILE)
    write_segment(root, "seg04", fn_embedded_segpack(), fn_embedded_draft())

    result = run_validate(root, "seg04")

    assert result.returncode == 0, (
        f"expected the footnote-embedded verse draft to pass, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg04] OK" in result.stdout


SKIP_PROFILE = {
    "verse_policy": {"mode": "skip"},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def ghost_parent_segpack(seg="seg05"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": "Ordinary prose, no verse here.",
            },
        ],
        "footnotes": [],
        "verses": [
            {
                # NON-embedded (mount absent -> the pre-#96 default):
                # parent_block references a block that does not exist
                # anywhere in this segpack -- a genuine SOURCE DEFECT, which
                # the mount=="embedded" skip must NEVER swallow.
                "vid": "vGhost",
                "placeholder": "⟦VERSE_vGhost⟧",
                "parent_block": "missingBlock",
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def ghost_parent_draft(seg="seg05"):
    return {
        "seg": seg,
        "blocks": {"p1": "Ordinary translated prose, no verse here."},
        "footnotes": {},
        "verses": {"vGhost": {}},
        "names": [],
        "notes": [],
    }


def test_source_defect_floor_still_fires_for_non_embedded_verse_with_missing_parent(tmp_path):
    """Control (GREEN both before and after #96): a NON-embedded verse
    (mount absent, i.e. the pre-#96 default) whose parent_block is missing
    from this segpack's blocks[] must still be flagged as a SOURCE DEFECT --
    proves check 3's new mount=="embedded" skip is scoped exactly to
    embedded verses and does not swallow this pre-existing floor."""
    root = make_durable_root(tmp_path, profile=SKIP_PROFILE)
    write_segment(root, "seg05", ghost_parent_segpack(), ghost_parent_draft())

    result = run_validate(root, "seg05")

    assert result.returncode == 1, (
        f"a verse with a missing parent_block must still fail the gate, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert (
        "SOURCE DEFECT: verse 'vGhost' parent_block 'missingBlock' not found "
        "among this segpack's blocks" in result.stdout
    )
    assert defect_count(result.stdout) == 1


# ---------------------------------------------------------------------------
# 7. #173 -- placeholder fidelity hardcoded the VERSE_ prefix. PH_RE only
#    matched ⟦FNREF_N⟧ / ⟦VERSE_...⟧-shaped spans, so a custom adapter's
#    embedded-verse placeholder with a DIFFERENT prefix (e.g. ⟦POEM_1⟧) was
#    invisible to placeholders() entirely -- dropping it from the draft
#    silently passed checks 2 and 4. Fixed via an EXACT MAP: a `⟦…⟧` span is
#    a placeholder iff it is a `⟦FNREF_N⟧` anchor or one of THIS segpack's
#    own declared verses[].placeholder strings -- not a VERSE_-prefix regex,
#    and NOT a naive `⟦[^⟧]+⟧` widening (an earlier draft of the fix; codex
#    rejected it as over-broad, see the over-match-guard test below).
# ---------------------------------------------------------------------------

V_PH_POEM = "⟦POEM_1⟧"                  # custom-adapter naming: no VERSE_/FNREF_ prefix at all
V_PH_V001 = "⟦VERSE_V001_deadbeef⟧"     # real-source naming: VERSE_{vid}_{8hex} -- must still work
V_LITERAL_BRACKET = "⟦variant⟧"         # literal editorial-prose bracket span, NOT a declared placeholder


def embedded_placeholder_segpack(seg, placeholder, vid="vEmbed", n_line=2):
    """An embedded verse (mount=="embedded") quoted inside prose block p1,
    parametrized on the placeholder STRING so the same shape can be
    exercised under a custom-adapter naming (V_PH_POEM) and the real
    source's own VERSE_{vid}_{8hex} naming (V_PH_V001)."""
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"Some prose introducing a poem {placeholder} right here.",
            },
        ],
        "footnotes": [],
        "verses": [
            {
                "vid": vid,
                "placeholder": placeholder,
                "parent_block": "p1",
                "mount": "embedded",
                "n_line": n_line,
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def embedded_placeholder_draft(seg, placeholder, vid="vEmbed", keep_placeholder=True):
    block_text = (
        f"Some translated prose introducing a poem {placeholder} right here."
        if keep_placeholder
        else "Some translated prose introducing a poem right here."
    )
    return {
        "seg": seg,
        "blocks": {"p1": block_text},
        "footnotes": {},
        "verses": {
            vid: {"literal_gloss": "A plain literal rendering of the quoted poem."},
        },
        "names": [],
        "notes": [],
    }


def test_custom_adapter_embedded_placeholder_dropped_fails_gate(tmp_path):
    """THE BUG: a custom adapter's embedded-verse placeholder that does NOT
    follow the VERSE_ prefix convention (⟦POEM_1⟧, declared in
    verses[].placeholder with mount="embedded") must be enforced by the
    prose-block placeholder multiset (check 2) via the exact-map, not a
    prefix regex. Pre-#173 (PH_RE hardcoded to FNREF_/VERSE_-prefixed spans
    only), ⟦POEM_1⟧ was invisible to placeholders() entirely, so this
    fixture's dropped placeholder was OBSERVED PASSING (rc=0) on pre-fix
    code -- must fail post-fix."""
    root = make_durable_root(tmp_path, profile=LITERAL_ONLY_PROFILE)
    write_segment(
        root, "seg06",
        embedded_placeholder_segpack("seg06", V_PH_POEM),
        embedded_placeholder_draft("seg06", V_PH_POEM, keep_placeholder=False),
    )

    result = run_validate(root, "seg06")

    assert result.returncode == 1, (
        f"a dropped custom-adapter embedded placeholder must fail the gate, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert f"[p1] placeholder mismatch: src=['{V_PH_POEM}'] draft=[]" in result.stdout
    assert defect_count(result.stdout) == 1


def test_custom_adapter_embedded_placeholder_kept_passes_gate(tmp_path):
    """Sanity companion: the SAME ⟦POEM_1⟧ placeholder, kept intact in the
    draft, must pass -- proves the previous test's failure is caused solely
    by the drop, not by ⟦POEM_1⟧ being unrecognized outright."""
    root = make_durable_root(tmp_path, profile=LITERAL_ONLY_PROFILE)
    write_segment(
        root, "seg06",
        embedded_placeholder_segpack("seg06", V_PH_POEM),
        embedded_placeholder_draft("seg06", V_PH_POEM, keep_placeholder=True),
    )

    result = run_validate(root, "seg06")

    assert result.returncode == 0, (
        f"expected the placeholder-kept draft to pass, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg06] OK" in result.stdout


def test_builtin_verse_prefixed_embedded_placeholder_still_enforced(tmp_path):
    """Regression guard: the real source's own VERSE_{vid}_{8hex} naming
    (⟦VERSE_V001_deadbeef⟧) must STILL be enforced by the exact-map --
    the fix must not regress built-in verses just because the prefix regex
    was removed. Dropping it from the draft must still fail the gate."""
    root = make_durable_root(tmp_path, profile=LITERAL_ONLY_PROFILE)
    write_segment(
        root, "seg06",
        embedded_placeholder_segpack("seg06", V_PH_V001, vid="v001"),
        embedded_placeholder_draft("seg06", V_PH_V001, vid="v001", keep_placeholder=False),
    )

    result = run_validate(root, "seg06")

    assert result.returncode == 1, (
        f"dropping a VERSE_-prefixed built-in placeholder must still fail "
        f"the gate, got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert f"[p1] placeholder mismatch: src=['{V_PH_V001}'] draft=[]" in result.stdout
    assert defect_count(result.stdout) == 1


def fn_custom_placeholder_segpack(seg, placeholder, vid="vFnPoem"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"Some prose with a note {FN_PH} attached.",
            },
        ],
        "footnotes": [
            {"n": 1, "source_text": f"A note quoting a poem {placeholder} within it."}
        ],
        "verses": [
            {
                "vid": vid,
                "placeholder": placeholder,
                # parent_block is the FOOTNOTE-DEF block's own id -- NEVER a
                # member of this segpack's own blocks[] (a footnote-def
                # block is not a segment block).
                "parent_block": "fn1def",
                "mount": "embedded",
                "n_line": 2,
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def fn_custom_placeholder_draft(seg, placeholder, vid="vFnPoem", keep_placeholder=True):
    fn_text = (
        f"A translated note quoting a poem {placeholder} within it."
        if keep_placeholder
        else "A translated note quoting a poem within it."
    )
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached.",
        },
        "footnotes": {"1": fn_text},
        "verses": {
            vid: {"literal_gloss": "A plain literal rendering of the quoted poem."},
        },
        "names": [],
        "notes": [],
    }


def test_custom_adapter_footnote_placeholder_dropped_fails_gate(tmp_path):
    """THE BUG, check 4 (footnote) variant: a custom adapter's ⟦POEM_1⟧
    placeholder embedded in a footnote's source_text, dropped from the
    translated footnote text, must fail via the exact-map. Pre-#173 this
    was OBSERVED PASSING (rc=0) for the same PH_RE-prefix reason as the
    prose-block case above."""
    root = make_durable_root(tmp_path, profile=LITERAL_ONLY_PROFILE)
    write_segment(
        root, "seg07",
        fn_custom_placeholder_segpack("seg07", V_PH_POEM),
        fn_custom_placeholder_draft("seg07", V_PH_POEM, keep_placeholder=False),
    )

    result = run_validate(root, "seg07")

    assert result.returncode == 1, (
        f"a dropped custom-adapter footnote placeholder must fail the gate, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert f"[FN:1] placeholder mismatch: src=['{V_PH_POEM}'] draft=[]" in result.stdout
    assert defect_count(result.stdout) == 1


def literal_bracket_segpack(seg="seg08"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"The manuscript shows {V_LITERAL_BRACKET} in the margin.",
            },
        ],
        "footnotes": [],
        "verses": [],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def literal_bracket_draft(seg="seg08"):
    return {
        "seg": seg,
        "blocks": {"p1": "The manuscript shows a variant reading in the margin."},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }


def test_literal_bracket_span_not_declared_placeholder_does_not_false_fire(tmp_path):
    """Over-match guard for the exact-map choice: a bracketed span in SOURCE
    prose (⟦variant⟧) that is neither a footnote anchor nor declared in any
    verses[].placeholder is literal editorial text, not a fidelity token --
    a translation that renders it away entirely (no ⟦variant⟧ in the draft)
    must NOT be flagged as a dropped placeholder. This guards against a
    naive `⟦[^⟧]+⟧` widening (rejected during plan review as over-broad,
    since block/footnote source text is unconstrained): that widening would
    wrongly require this literal span to survive verbatim. MUST pass both
    before AND after #173's fix -- this is the boundary the fix must not
    cross, not the bug itself."""
    root = make_durable_root(tmp_path, profile=DEFAULT_PROFILE)
    write_segment(root, "seg08", literal_bracket_segpack(), literal_bracket_draft())

    result = run_validate(root, "seg08")

    assert result.returncode == 0, (
        f"a translated-away literal bracket span must NOT fail the gate, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg08] OK" in result.stdout


# ---------------------------------------------------------------------------
# 8. #188 -- verse-line counting (check 5) must be LF-only, matching
#    render_obsidian.py's _split_lf_lines (#183). str.splitlines() also
#    breaks on exotic Unicode line boundaries -- U+2028 LINE SEPARATOR among
#    them -- so it must not be trusted to count either a rendered verse's
#    own lines (the multi-line-source -> non-single-line-rendering guard in
#    `_verse_required_fields`, check 5) or a block-mount verse's SOURCE line
#    count (`_source_line_count`); fixing only one side would introduce a
#    false positive/negative on the other (see the plan). SEP is built via
#    chr(0x2028), never pasted literally, so no invisible byte lands in
#    this file's source.
# ---------------------------------------------------------------------------

V_PH_EXOTIC_EMBED = "⟦VERSE_vExoticEmbed⟧"


def exotic_embedded_segpack(seg="seg09"):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "plain_text": f"Some prose introducing a poem {V_PH_EXOTIC_EMBED} right here.",
            },
        ],
        "footnotes": [],
        "verses": [
            {
                "vid": "vExoticEmbed",
                "placeholder": V_PH_EXOTIC_EMBED,
                "parent_block": "p1",
                "mount": "embedded",
                "n_line": 2,
            }
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def exotic_embedded_draft(seg, rendered):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose introducing a poem {V_PH_EXOTIC_EMBED} right here.",
        },
        "footnotes": {},
        "verses": {
            "vExoticEmbed": {
                "rendered": rendered,
                "literal_gloss": "A literal gloss that says something else entirely",
            },
        },
        "names": [],
        "notes": [],
    }


def test_exotic_separated_rendered_flagged_single_line_for_embedded_verse(tmp_path):
    """#188: rendered-line counting (check 5 in `_verse_required_fields` --
    the multi-line-source -> non-single-line-rendering guard) must be
    LF-only -- NOT str.splitlines(), which also breaks on the exotic Unicode boundary
    U+2028 LINE SEPARATOR. An embedded verse with a segpack-threaded
    n_line=2 whose 'rendered' uses ONLY U+2028 as its separator (no real
    \\n) is genuinely single-line under LF-only counting and must be
    flagged -- pre-#188, str.splitlines() saw 2 lines there and silently
    let a single-line rendering through."""
    SEP = chr(0x2028)
    assert SEP.encode("utf-8") == bytes((0xE2, 0x80, 0xA8))
    rendered = f"alpha{SEP}beta"
    assert rendered.count(SEP) == 1

    root = make_durable_root(tmp_path, profile=DEFAULT_PROFILE)
    write_segment(
        root, "seg09",
        exotic_embedded_segpack(),
        exotic_embedded_draft("seg09", rendered),
    )

    result = run_validate(root, "seg09")

    assert result.returncode == 1, (
        f"expected an exotic-separator-only rendered verse to fail the gate, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "rendered is a single line for a 2-line source verse" in result.stdout
    assert defect_count(result.stdout) == 1


V_PH_EXOTIC_BLOCK = "⟦VERSE_vExoticBlock⟧"


def exotic_block_mount_segpack(seg, source_text):
    return {
        "seg": seg,
        "blocks": [
            {
                "id": "vblockExotic",
                "order_index": 0,
                "plain_text": source_text,
            },
        ],
        "footnotes": [],
        "verses": [
            {"vid": "vExoticBlock", "placeholder": V_PH_EXOTIC_BLOCK, "parent_block": "vblockExotic"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def exotic_block_mount_draft(seg, rendered):
    return {
        "seg": seg,
        "blocks": {"vblockExotic": V_PH_EXOTIC_BLOCK},
        "footnotes": {},
        "verses": {
            "vExoticBlock": {
                "rendered": rendered,
                "literal_gloss": "A literal gloss that says something else entirely",
            },
        },
        "names": [],
        "notes": [],
    }


def test_exotic_separated_block_source_not_spuriously_flagged(tmp_path):
    """#188: `_source_line_count` (check 5's source side) must ALSO be
    LF-only -- fixing only the rendered side (the multi-line-source ->
    non-single-line-rendering guard in `_verse_required_fields`) would leave
    this asymmetric and introduce a NEW false positive: a block-mount verse
    whose SOURCE uses U+2028 as an interior separator (not a real \\n) is
    genuinely single-line under LF-only counting, so n_line stays < 2 and
    check 5's multi-line-source rule is skipped entirely -- a single real
    rendered line must NOT be flagged, even though str.splitlines() would
    have counted the source as 2 lines and wrongly flagged it."""
    SEP = chr(0x2028)
    assert SEP.encode("utf-8") == bytes((0xE2, 0x80, 0xA8))
    source_text = f"A{SEP}B"
    assert source_text.count(SEP) == 1

    root = make_durable_root(tmp_path, profile=DEFAULT_PROFILE)
    write_segment(
        root, "seg10",
        exotic_block_mount_segpack("seg10", source_text),
        exotic_block_mount_draft("seg10", "one real rendered line"),
    )

    result = run_validate(root, "seg10")

    assert result.returncode == 0, (
        f"expected the exotic-separated-source block-mount verse to pass, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "rendered is a single line" not in result.stdout
    assert "[seg10] OK" in result.stdout


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
