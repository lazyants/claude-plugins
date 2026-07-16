"""tests/seg_identity_enforced.test.py -- #178 regression lock: a draft whose
own `seg` field does not match the requested segment id (a mislabeled/
cross-wired draft -- e.g. `seg01.draft.json` accidentally carrying
`"seg": "seg02"`, a copy-paste or a stale write from a different run) must
be rejected by BOTH validate_draft.py (`FAIL`) and draft_ready.py
(`not ready`), not silently pass as `OK`/`READY`.

Pre-#178, `check_draft_structure()` only TYPE-checks draft["seg"] (must be a
str); neither caller compared it to the requested seg CLI argument, so such
a draft cleared every one of validate_draft.py's six content checks AND
draft_ready.py's readiness probe.

Harness modeled on tests/validate_draft.test.py's own fixture (real durable
root, ownership marker + profile.yml, invoking the ACTUAL scripts as
subprocesses -- never a hand-rolled minimal fixture). Unlike that file, this
one copies BOTH real scripts (validate_draft.py needs profile.yml via
load_profile(); draft_ready.py needs neither profile.yml nor the ownership
marker, but copying both scripts into one fixture root keeps the setup
uniform). Schemas are not needed -- neither script schema-validates via
jsonschema.

The tampered draft is otherwise FULLY VALID: it clears every one of
validate_draft.py's six content checks and carries a dispatch_token matching
--expect-token, so the seg mismatch is the SOLE possible failure cause in
each assertion (never a false-fail for the wrong reason). A control fixture
with a matching seg proves the guard doesn't over-reject a legitimate draft.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
VALIDATE_DRAFT_SRC = SCRIPTS_DIR / "validate_draft.py"
DRAFT_READY_SRC = SCRIPTS_DIR / "draft_ready.py"

assert VALIDATE_DRAFT_SRC.is_file(), f"validate_draft.py not found at {VALIDATE_DRAFT_SRC}"
assert DRAFT_READY_SRC.is_file(), f"draft_ready.py not found at {DRAFT_READY_SRC}"

FN_PH = "⟦FNREF_1⟧"        # footnote-anchor sentinel: FNREF_1
V_PH_A = "⟦VERSE_vA⟧"      # standalone-verse placeholder for vA
V_PH_B = "⟦VERSE_vB⟧"      # standalone-verse placeholder for vB

DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}

DISPATCH_TOKEN = "RUN123:seg01"


# ---------------------------------------------------------------------------
# Fixture harness -- same shape as validate_draft.test.py's make_durable_root,
# but copies BOTH real scripts into {root}/scripts/ so each script's own
# self-anchoring `Path(__file__).resolve().parents[1]` resolves to THIS temp
# root, exactly matching how they are actually invoked in production.
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path, profile=None):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
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


def run_validate_draft(root, requested_seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_draft.py"), requested_seg],
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_draft_ready(root, requested_seg, expect_token):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "draft_ready.py"),
            requested_seg,
            "--expect-token",
            expect_token,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Fixture content -- one prose block with a footnote-anchor sentinel, two
# standalone verse blocks each parented to their OWN verse via the per-block
# bijection, one footnote -- identical in shape to validate_draft.test.py's
# clean baseline, so the tampered draft is fully valid content-wise and the
# ONLY possible failure cause is the seg mismatch.
# ---------------------------------------------------------------------------

def clean_segpack(seg):
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


def clean_draft(seg):
    """A fully valid draft (clears all six content checks) for the segment id
    passed in `seg` -- this argument controls draft["seg"] and is the ONLY
    thing this test varies between the tampered and control fixtures."""
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
        "dispatch_token": DISPATCH_TOKEN,
    }


# ---------------------------------------------------------------------------
# Tampered fixture: segments/segpack_seg01.json + segments/seg01.draft.json
# on disk (the actual FILE is seg01's), but draft["seg"] == "seg02" -- a
# mislabeled/cross-wired draft. The segpack itself is left keyed "seg01" (the
# segpack is the source of truth for the REQUESTED seg's content; only the
# draft's self-reported seg field is tampered).
# ---------------------------------------------------------------------------

def tampered_draft():
    draft = clean_draft("seg01")
    draft["seg"] = "seg02"  # injected defect: cross-wired seg field
    return draft


def test_validate_draft_rejects_seg_mismatch(tmp_path):
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack("seg01"), tampered_draft())

    result = run_validate_draft(root, "seg01")

    assert result.returncode != 0, (
        f"a draft whose 'seg' field ('seg02') doesn't match the requested "
        f"seg ('seg01') must fail validate_draft.py, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "'seg' is 'seg02'" in result.stdout
    assert "seg01" in result.stdout


def test_draft_ready_rejects_seg_mismatch(tmp_path):
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack("seg01"), tampered_draft())

    result = run_draft_ready(root, "seg01", DISPATCH_TOKEN)

    assert result.returncode != 0, (
        f"a draft whose 'seg' field ('seg02') doesn't match the requested "
        f"seg ('seg01') must fail draft_ready.py, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "not ready" in result.stdout
    assert "expected 'seg01'" in result.stdout


def test_matching_seg_control_passes_both(tmp_path):
    """Control: the SAME fixture shape, but draft["seg"] == "seg01" (matching
    the requested/filed seg) -- must pass BOTH scripts. Proves the guard
    fires ONLY on a real mismatch (no over-rejection), and -- since this
    fixture is byte-identical to the tampered one except for the seg field --
    isolates the seg mismatch as the sole cause of the two failures above.
    This control is green even against the pre-#178 (unpatched) scripts."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack("seg01"), clean_draft("seg01"))

    validate_result = run_validate_draft(root, "seg01")
    assert validate_result.returncode == 0, (
        f"expected the matching-seg control to pass validate_draft.py, got "
        f"rc={validate_result.returncode}\nstdout:\n{validate_result.stdout}\n"
        f"stderr:\n{validate_result.stderr}"
    )
    assert "[seg01] OK" in validate_result.stdout

    ready_result = run_draft_ready(root, "seg01", DISPATCH_TOKEN)
    assert ready_result.returncode == 0, (
        f"expected the matching-seg control to pass draft_ready.py, got rc="
        f"{ready_result.returncode}\nstdout:\n{ready_result.stdout}\nstderr:\n"
        f"{ready_result.stderr}"
    )
    assert "[seg01] READY" in ready_result.stdout


# ---------------------------------------------------------------------------
# #198 -- draft_ready.py `--candidate-file`: the W5 codex_job.py driver FULLY
# validates an isolated attempt artifact BEFORE promoting it to canonical. The
# option overrides draft_path(seg); the segpack is STILL read from its
# canonical path; all existing checks (schema shape, seg field == seg, key
# sets, --expect-token) run against the candidate. Backward compatible: absent
# option == today's canonical-path behavior (already covered by the seg-
# mismatch tests above, which pass no --candidate-file).
# ---------------------------------------------------------------------------

def run_draft_ready_candidate(root, requested_seg, expect_token, candidate_file):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "draft_ready.py"),
            requested_seg,
            "--expect-token",
            expect_token,
            "--candidate-file",
            candidate_file,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_segpack_only(root, seg, segpack):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )


def test_draft_ready_candidate_file_valid_passes_without_canonical(tmp_path):
    """A VALID draft at a non-canonical candidate path passes via
    --candidate-file even when NO canonical seg01.draft.json exists -- proving
    the option truly overrides draft_path(seg) (a script still reading the
    canonical path would report 'draft file absent'). The segpack is at its
    canonical path, proving it is still read from there."""
    root = make_durable_root(tmp_path)
    write_segpack_only(root, "seg01", clean_segpack("seg01"))
    candidate = root / "segments" / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(clean_draft("seg01"), ensure_ascii=False), encoding="utf-8")
    # canonical seg01.draft.json deliberately absent.

    result = run_draft_ready_candidate(root, "seg01", DISPATCH_TOKEN, str(candidate))

    assert result.returncode == 0, (
        f"a valid --candidate-file draft must be READY even with no canonical "
        f"draft on disk, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "[seg01] READY" in result.stdout


def test_draft_ready_candidate_file_wrong_token_rejected(tmp_path):
    """The --expect-token check runs against the CANDIDATE: a candidate draft
    whose dispatch_token differs from --expect-token must be not-ready,
    proving --candidate-file does not weaken the freshness gate."""
    root = make_durable_root(tmp_path)
    write_segpack_only(root, "seg01", clean_segpack("seg01"))
    stale = clean_draft("seg01")
    stale["dispatch_token"] = "RUN999:seg01"  # stale token, != DISPATCH_TOKEN
    candidate = root / "segments" / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")

    result = run_draft_ready_candidate(root, "seg01", DISPATCH_TOKEN, str(candidate))

    assert result.returncode != 0, (
        f"a wrong-token --candidate-file draft must be not-ready, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "not ready" in result.stdout
    assert "dispatch_token mismatch" in result.stdout


def test_draft_ready_candidate_file_seg_mismatch_rejected(tmp_path):
    """The seg field == seg check (#178) runs against the CANDIDATE: a
    candidate draft whose own 'seg' is cross-wired must be not-ready, proving
    the full readiness checks (not just the token) run on the candidate."""
    root = make_durable_root(tmp_path)
    write_segpack_only(root, "seg01", clean_segpack("seg01"))
    candidate = root / "segments" / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(tampered_draft(), ensure_ascii=False), encoding="utf-8")

    result = run_draft_ready_candidate(root, "seg01", DISPATCH_TOKEN, str(candidate))

    assert result.returncode != 0, (
        f"a cross-wired --candidate-file draft must be not-ready, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "not ready" in result.stdout
    assert "expected 'seg01'" in result.stdout


def test_draft_ready_candidate_file_absent_uses_canonical(tmp_path):
    """Backward compatibility: with --candidate-file ABSENT, draft_ready.py
    reads the canonical draft and ignores any stray attempt file on disk. A
    BROKEN candidate file at the isolated-attempt path must NOT affect the
    result when the flag is not passed."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack("seg01"), clean_draft("seg01"))
    (root / "segments" / ".att.seg01.1.draft.json").write_text(
        "{ not valid json", encoding="utf-8"
    )

    result = run_draft_ready(root, "seg01", DISPATCH_TOKEN)  # no --candidate-file

    assert result.returncode == 0, (
        f"absent --candidate-file must read the canonical draft unchanged, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg01] READY" in result.stdout


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
