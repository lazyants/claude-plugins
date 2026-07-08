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


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
