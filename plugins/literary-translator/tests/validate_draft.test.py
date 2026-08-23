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
import importlib.util
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
# 1b. _diff_report()'s own coverage/bijection MISSING and EXTRA branches
# (check 4/5's "footnotes|blocks|verses MISSING/EXTRA" reports). Previously
# untested anywhere in this file: no existing fixture ever DROPS or ADDS a
# key (as opposed to blanking an existing one's VALUE, which
# test_empty_footnote_fails_gate above already covers) -- a whole-file grep
# for "MISSING"/"EXTRA"/"del "/".pop(" all return zero hits. Confirmed by
# mutation: `if missing:` in _diff_report() -> `if False:` survives the
# whole battery.
# ---------------------------------------------------------------------------

def test_dropped_footnote_key_fails_gate_with_missing_report(tmp_path):
    """PROOF for the `if missing:` branch. Drops the footnote key ENTIRELY
    (not merely blanking its value), which removes it from BOTH src_fn's and
    ru_fn's intersection -- so none of the per-key content checks (empty
    translation, placeholder mismatch, sentinel) ever see it either,
    isolating the MISSING report as the ONLY defect this fixture can
    produce."""
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    del draft["footnotes"]["1"]  # injected defect: DROPPED key, not blanked

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"a draft missing a required footnote key must fail the gate, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "footnotes MISSING: ['1']" in result.stdout
    assert defect_count(result.stdout) == 1


def test_extra_footnote_key_fails_gate_with_extra_report(tmp_path):
    """Companion for _diff_report()'s sibling `if extra:` branch -- same
    "no fixture ever injects this defect" gap, also zero hits for "EXTRA"
    anywhere in this file. A draft carrying a footnote key the segpack never
    declared; the per-key content loop only iterates the SHARED key set, so
    the undeclared key contributes nothing else."""
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    draft["footnotes"]["99"] = "A translated note with no source footnote."

    write_segment(root, "seg01", clean_segpack(), draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, (
        f"a draft carrying an undeclared footnote key must fail the gate, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "footnotes EXTRA: ['99']" in result.stdout
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


# ---------------------------------------------------------------------------
# 9. #198 -- `--candidate-file`: the W5 codex_job.py driver FULLY validates an
#    isolated attempt artifact BEFORE promoting it to canonical. The option
#    overrides draft_path(seg) with the given path; the segpack is STILL read
#    from its canonical path; the six checks run against the candidate.
#    Backward compatible: absent option == today's canonical-path behavior.
# ---------------------------------------------------------------------------

def run_validate_candidate(root, seg, candidate_file):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "validate_draft.py"),
            seg,
            "--candidate-file",
            candidate_file,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_candidate_file_valid_draft_passes_without_canonical(tmp_path):
    """A VALID draft at a non-canonical candidate path passes via
    --candidate-file even when NO canonical {seg}.draft.json exists at all --
    proving the option truly overrides draft_path(seg) (a script still reading
    the canonical path would report 'draft missing' instead). The segpack is
    written at its canonical path, proving it is still read from there."""
    root = make_durable_root(tmp_path)
    segments_dir = root / "segments"
    # segpack canonical (still read from canonical); NO canonical draft.
    (segments_dir / "segpack_seg01.json").write_text(
        json.dumps(clean_segpack(), ensure_ascii=False), encoding="utf-8"
    )
    candidate = segments_dir / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(clean_draft(), ensure_ascii=False), encoding="utf-8")

    result = run_validate_candidate(root, "seg01", str(candidate))

    assert result.returncode == 0, (
        f"a valid --candidate-file draft must pass even with no canonical "
        f"draft on disk, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "[seg01] OK" in result.stdout


def test_candidate_file_invalid_draft_rejected(tmp_path):
    """The six content checks run against the CANDIDATE: an injected defect
    (empty footnote translation, check 4) in the candidate must fail the gate,
    proving --candidate-file does not weaken validation."""
    root = make_durable_root(tmp_path)
    segments_dir = root / "segments"
    (segments_dir / "segpack_seg01.json").write_text(
        json.dumps(clean_segpack(), ensure_ascii=False), encoding="utf-8"
    )
    draft = clean_draft()
    draft["footnotes"]["1"] = ""  # injected defect in the candidate
    candidate = segments_dir / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    result = run_validate_candidate(root, "seg01", str(candidate))

    assert result.returncode == 1, (
        f"an invalid --candidate-file draft must fail the gate, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    assert "[FN:1] empty translation" in result.stdout
    assert defect_count(result.stdout) == 1


def test_candidate_file_absent_uses_canonical(tmp_path):
    """Backward compatibility: with --candidate-file ABSENT, validate_draft.py
    reads the canonical draft and ignores any stray attempt file on disk. A
    BROKEN candidate file at the isolated-attempt path must NOT affect the
    result when the flag is not passed."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())
    # A broken attempt file that MUST be ignored when the flag is absent.
    (root / "segments" / ".att.seg01.1.draft.json").write_text(
        "{ not valid json", encoding="utf-8"
    )

    result = run_validate(root, "seg01")  # no --candidate-file

    assert result.returncode == 0, (
        f"absent --candidate-file must read the canonical draft unchanged, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg01] OK" in result.stdout


# ---------------------------------------------------------------------------
# #412 prerequisite -- --durable-root PATH. Governs DATA only: segments/, and
# where load_profile() looks for the ownership marker (which then points at
# profile.yml wherever it actually lives -- unchanged by this flag). This
# script is a LEAF -- it shells out to nothing at all, so there is no
# --plugin-root companion and no forwarded-argv assertion to make (unlike
# select_segments.py/ledger_merge.py/resume_setup.py/review_ready.py, each
# of which resolves at least one sibling script). See references/gotchas.md
# §4 for the full two-flag convention this script deliberately does not need.
#
# Every redirect test below uses an ORPHAN-COPY fixture -- the script file
# itself sits somewhere with NO co-located segments/, profile.yml, or
# ownership marker at all -- so co-location can never be what makes a
# redirect test pass; only the flag itself can.
# ---------------------------------------------------------------------------

def run_validate_from(script_path, seg, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), seg, *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with no
    --durable-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_validate(root, "seg01")

    assert result.returncode == 0
    assert "[seg01] OK" in result.stdout


def test_durable_root_flag_omitted_is_byte_identical_to_explicit_self_root(tmp_path):
    """An explicit --durable-root pointing at the SAME root the script would
    have self-anchored to anyway must produce byte-identical stdout -- proof
    the flag changes nothing when it names today's own location."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    without = run_validate(root, "seg01")
    with_flag = run_validate_from(
        root / "scripts" / "validate_draft.py", "seg01", "--durable-root", str(root)
    )

    assert without.returncode == with_flag.returncode == 0
    assert without.stdout == with_flag.stdout


def test_durable_root_flag_redirects_data_reads_orphan_copy(tmp_path):
    """The core property, proven via an ORPHAN COPY: the script file sits at
    a location with NO segments/, profile.yml, or ownership marker
    co-located at all, so success is possible ONLY if --durable-root
    actually redirected every data read -- co-location cannot be what made
    this pass."""
    data_root = make_durable_root(tmp_path)
    write_segment(data_root, "seg01", clean_segpack(), clean_draft())

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "validate_draft.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)
    assert not (orphan_dir.parent / "segments").exists(), (
        "fixture bug: the orphan location must have NO co-located segments/"
    )
    assert not (orphan_dir.parent / ".literary-translator-root.json").exists(), (
        "fixture bug: the orphan location must have NO co-located ownership marker"
    )

    result = run_validate_from(orphan_script, "seg01", "--durable-root", str(data_root))

    assert result.returncode == 0, (
        f"--durable-root must redirect EVERY data read (segments/ AND the "
        f"ownership-marker-resolved profile.yml) to {data_root}, even though "
        f"the script itself has none of that co-located:\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "[seg01] OK" in result.stdout


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control, and proof the positive test above is attributable
    to --durable-root specifically: the SAME orphan copy, invoked WITHOUT
    the flag, cannot succeed via self-anchoring -- there is no ownership
    marker (let alone segments/) anywhere near it, so load_profile() fatals
    before it ever gets to read a draft."""
    data_root = make_durable_root(tmp_path)
    write_segment(data_root, "seg01", clean_segpack(), clean_draft())

    orphan_dir = tmp_path / "orphan_location2" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "validate_draft.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_validate_from(orphan_script, "seg01")  # no --durable-root

    assert result.returncode == 2, (
        f"expected the ownership-marker-not-found FATAL (exit 2), got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ownership marker not found" in result.stderr


def test_durable_root_flag_redirects_a_defect_report_too(tmp_path):
    """The redirect applies to the FAIL path too, not just the clean one: an
    injected defect (empty footnote translation, check 4) must still be
    found and named when the segpack/draft are read via --durable-root from
    an orphan copy."""
    data_root = make_durable_root(tmp_path)
    draft = clean_draft()
    draft["footnotes"]["1"] = ""  # injected defect
    write_segment(data_root, "seg01", clean_segpack(), draft)

    orphan_dir = tmp_path / "orphan_location3" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "validate_draft.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_validate_from(orphan_script, "seg01", "--durable-root", str(data_root))

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "[FN:1] empty translation" in result.stdout
    assert defect_count(result.stdout) == 1


def test_durable_root_flag_uses_the_redirected_profiles_own_settings(tmp_path):
    """Proof --durable-root really redirects PROFILE resolution too (not
    just segments/): the data root's own profile.yml sets
    apparatus_policy=body_refs_only (a DIFFERENT policy from
    DEFAULT_PROFILE's translate_all), and the redirected run must apply
    THAT profile's rules -- a dropped body_ref_marker must be flagged,
    which only fires under body_refs_only."""
    profile = {
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "footnotes": {"apparatus_policy": "body_refs_only"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }
    data_root = make_durable_root(tmp_path, profile=profile)
    segpack = {
        "seg": "seg01",
        "blocks": [
            {
                "id": "p1",
                "order_index": 0,
                "source_html": "<p>Some prose citing [1].</p>",
                "body_ref_markers": ["[1]"],
            }
        ],
        "footnotes": [],
        "verses": [],
    }
    draft = {
        "seg": "seg01",
        "blocks": {"p1": "Translated prose with the marker DROPPED."},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }
    write_segment(data_root, "seg01", segpack, draft)

    orphan_dir = tmp_path / "orphan_location4" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "validate_draft.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_validate_from(orphan_script, "seg01", "--durable-root", str(data_root))

    assert result.returncode == 1, (
        f"the redirected profile's body_refs_only policy must be applied, "
        f"flagging the dropped marker:\nstdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "body_ref marker" in result.stdout


# ---------------------------------------------------------------------------
# #397 -- CHARACTERIZATION, not a regression lock for new behaviour.
#
# This test is GREEN both before and after validate_extraction.py gained its
# two empty-content checks, and it is deliberately NOT counted as
# red-before-green evidence for them: everything it asserts is already
# implemented here. Its job is to make the PREMISE those two W2 checks rest on
# breakable on purpose.
#
# The premise: for a content unit that carries no text for the translator, the
# FAITHFUL draft -- the one that leaves it empty, because there is nothing to
# translate -- can never converge. The segment converges only on a draft that
# invents text the source does not have, which is why refusing the unit at
# extraction is strictly better than discovering it after a paid translation
# job. Two distinct branches produce that outcome, and they do NOT agree on
# what "empty" means:
#
#   * a segment BLOCK with falsy plain_text and a non-blank source_html --
#     _block_source_text() falls back to the markup, so src_text is truthy and
#     the empty draft block is reported `[bid] empty translation`. A
#     WHITESPACE-only plain_text is truthy and returned verbatim, so that
#     block's empty draft DOES converge; the third assertion below pins
#     exactly that boundary, which is why the W2 check tests falsiness
#     rather than `.strip()`.
#   * a FOOTNOTE definition with no text -- source_text is taken from
#     `plain_text` ONLY (no source_html fallback anywhere on that path) and the
#     blank-translation test is unconditional, so whitespace does not rescue it.
#
# Unlike every other fixture in this file, the segpack here is NOT hand-authored
# -- it is produced by the REAL segpack.build_pack() from a manifest, so the
# test also proves the manifest->segpack pass-through the W2 checks assume.
# If a future change makes either unit's faithful draft convergeable, this goes
# red and the two W2 checks must be re-justified rather than silently kept.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = SCRIPT_SRC.parent
_SEGPACK_SCRIPT = _SCRIPTS_DIR / "segpack.py"
_LANGUAGES_DIR = _SCRIPTS_DIR.parent / "languages"


def _load_segpack_module():
    """Mirrors the loader every other segpack suite uses: segpack.py imports
    bootstrap_names, so its own directory must be on sys.path while it loads."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "segpack_under_test_397", _SEGPACK_SCRIPT
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_SCRIPTS_DIR))


def _empty_content_manifest():
    """One body segment carrying three prose blocks: one with real text, one
    whose plain_text is "" with a structural <hr> as source_html, and one whose
    plain_text is whitespace-only with the same markup. Footnote 1's definition
    block carries no text at all."""
    def _blk(bid, order, plain, html=None):
        b = {
            "id": bid, "type": "PARA", "seg": "seg01", "order_index": order,
            "source_file": "body.xhtml", "plain_text": plain,
            "sha1": "0" * 40,
        }
        if html is not None:
            b["source_html"] = html
        if bid == "PARA:seg01:0001":
            # keep build_pack's recorded-vs-sentinel cross-check quiet: this is
            # a fidelity detail of the fixture, not part of what is asserted.
            b["fnrefs"] = [1]
        return b

    return {
        "blocks": {
            "PARA:seg01:0001": _blk(
                "PARA:seg01:0001", 0, "Real prose ⟦FNREF_1⟧.", "<p>Real prose.</p>"
            ),
            "PARA:seg01:0002": _blk(
                "PARA:seg01:0002", 1, "", '<hr class="c30 p4"/>'
            ),
            "PARA:seg01:0003": _blk(
                "PARA:seg01:0003", 2, "   ", '<hr class="c30 p4"/>'
            ),
            "Footnote_1": {
                "id": "Footnote_1", "type": "FN", "seg": None, "order_index": 3,
                "source_file": "notes.xhtml", "plain_text": "",
                "source_html": "<p></p>", "sha1": "0" * 40,
            },
        },
        "segments": [{
            "seg": "seg01", "kind": "body",
            "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002", "PARA:seg01:0003"],
            "word_count": 3, "n_para": 3, "n_verse": 0, "n_quote": 0,
            "source_files": ["body.xhtml"],
        }],
        "footnotes": [{
            "n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01",
            "def_block": "Footnote_1",
        }],
        "verse": {
            "store": [], "n_nodes": 0, "n_block": 0, "n_embedded": 0,
            "by_context": {"body": 0, "footnote": 0, "frontback": 0},
        },
        "frontback": [],
        "spine": [{"pos": 0, "file": "body.xhtml", "klass": "body"}],
        "source_inputs": ["book.epub"],
        "generation_hashes": {
            "source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40,
        },
    }


def test_faithful_draft_of_empty_content_units_cannot_converge(tmp_path):
    segpack_mod = _load_segpack_module()
    lang_config = segpack_mod.load_language_config("fr.json", _LANGUAGES_DIR)
    canon = {
        "entries": {},
        "generation_hashes": {
            "particle_config_hash": "c" * 40, "derivation_bundle_hash": "d" * 40,
        },
    }

    pack = segpack_mod.build_pack(
        "seg01", _empty_content_manifest(), canon, lang_config, "translate_all"
    )

    # The pass-through the W2 checks assume: the manifest's own text and markup
    # reach the segpack verbatim, and the footnote's source_text comes from
    # plain_text with no source_html fallback.
    by_id = {b["id"]: b for b in pack["blocks"]}
    assert by_id["PARA:seg01:0002"]["plain_text"] == ""
    assert by_id["PARA:seg01:0002"]["source_html"] == '<hr class="c30 p4"/>'
    assert by_id["PARA:seg01:0003"]["plain_text"] == "   "
    assert pack["footnotes"] == [{"n": 1, "source_text": ""}]

    # The most faithful draft possible: every block that HAS text is translated,
    # every block that has none is left empty, and the footnote with no source
    # text gets no invented text either.
    draft = {
        "seg": "seg01",
        "blocks": {
            "PARA:seg01:0001": "Vraie prose ⟦FNREF_1⟧.",
            "PARA:seg01:0002": "",
            "PARA:seg01:0003": "",
        },
        "footnotes": {"1": ""},
        "verses": {},
        "names": [],
        "notes": [],
    }

    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", pack, draft)
    result = run_validate(root, "seg01")

    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout + result.stderr
    # The block whose markup is non-blank cannot be satisfied faithfully...
    assert "[PARA:seg01:0002] empty translation" in out, out
    # ...nor can the footnote whose definition carries no text...
    assert "[FN:1] empty translation" in out, out
    # ...while the WHITESPACE-only block is accepted, because
    # _block_source_text returns the truthy "   " verbatim and never consults
    # source_html. This is the boundary the W2 block check must not cross.
    assert "[PARA:seg01:0003] empty translation" not in out, out


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
