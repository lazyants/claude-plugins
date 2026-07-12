"""tests/draft_path_convention.test.py -- regression-lock for the plugin's
two load-bearing canonical path invariants (see
references/ledger-and-resumability.md, "Canonical path invariants", and
references/gotchas.md item 3):

    draft_path(seg)  = {durable_root}/segments/{seg}.draft.json
    review_path(seg) = {durable_root}/segments/{seg}.review.json

Both DELIBERATELY carry NO target-language suffix -- a divergence from the
real historiettes-t3 reference project's own per-language `.ru.draft.json`
naming (v1 has exactly one target language per project, already recorded
in profile.yml, so a suffix adds no information). "A ported script
hardcoding `.ru.draft.json` is a BUG" (ledger-and-resumability.md) -- this
file's job is to catch exactly that class of regression, at EVERY call
site named in the spec, not just in one script.

Call sites this file instantiates against real fixtures, per
ledger-and-resumability.md's own enumeration:

  draft_path(seg) (8 sites):
    1. scripts/validate_draft.py
    2. scripts/draft_ready.py
    3. scripts/ledger_update.py
    4. scripts/final_audit.py
    5. scripts/draft_sha1.py
    6. templates/review_TASK.template.md
    7. templates/translate_TASK.template.md
    8. templates/mass-translate-wf.template.js (translatePrompt/fixPrompt)

  review_path(seg) (6 sites -- 1.2.0 CONTRACT §8 split the old single
  reviewPrompt writer/`readReview`-in-one call into a DISPATCH-then-READ
  pair, adding one genuinely new direct-read call site; 1.3.6/#132 option b
  added a second new direct-read call site, fixPrompt):
    1. mass-translate-wf.template.js's reviewDispatchPrompt (1.2.0; formerly
       reviewPrompt -- writes it)
    2. mass-translate-wf.template.js's readReviewPrompt (1.2.0, NEW -- reads
       it directly, mechanically, returning only the 4 REVIEW_SCHEMA
       verdict fields)
    3. mass-translate-wf.template.js's verifyReviewArtifactPrompt (reads it,
       indirectly -- see that test's own docstring; UNCHANGED by 1.2.0)
    4. mass-translate-wf.template.js's fixPrompt (1.3.6/#132 option b, NEW
       -- reads it directly for its findings[] array, closing the gap
       where a read-agent transcription slip in issue/suggest text could
       reach the fixer via the in-memory revObj copy; see
       references/engine-loop.md's R1)
    5. scripts/review_artifact_check.py (reads it)
    6. scripts/ledger_update.py (reads it, for the reviewed_draft_sha1
       binding check)

Design confirmation, UPDATED for 1.3.6 (#132 option b): `fixPrompt` is now
DELIBERATELY a review_path(seg) reader (it was NOT, pre-1.3.6 -- see
references/engine-loop.md's R1 for the full history of both the round-60
in-memory design and this round's reversal of it) -- it instructs the
agent to READ review_path(seg) and apply every entry in its on-disk
findings[] array, rather than working from the in-memory `revObj` its own
3rd argument still carries (that argument is kept for other consumers --
the convergence decision and the review-artifact gate's own
`--expected-file` -- but fixPrompt's own prompt text no longer splices its
JSON as the findings source). This file asserts the new shape directly: an
affirmative "Read ... review.json" instruction, no trace of the old "do
not re-read ... for findings" negation, and no trace of the revObj
fixture's own distinctive marker text in the rendered prompt (proving the
in-memory object is genuinely not spliced in anymore).

For every script call site, each test builds an ISOLATED durable_root
fixture (the REAL script copied into {root}/scripts/, so its
Path(__file__)-based self-anchoring resolves against the fixture, exactly
as production invokes it) and proves, via genuine file I/O through the
real script (never a reimplementation), that:
  (a) content placed at the CANONICAL path is what the script actually
      reads/writes, and
  (b) a decoy placed at the legacy-suffixed variant (`{seg}.ru.draft.json`
      / `{seg}.ru.review.json`) is never picked up instead -- the
      strongest regression lock against exactly the bug class this
      invariant exists to prevent.

For the two JS prompt-builder functions, the real shipped
mass-translate-wf.template.js is instantiated (its `{{TOKEN}}`
placeholders substituted, exactly as the orchestrating session would do)
and run under real node -- not grepped as static text -- so the assertions
exercise the actual function output, not a guess about it.

Known, currently-genuine gap this file surfaces rather than papers over:
`review_TASK.template.md` and `translate_TASK.template.md` are named as
draft_path(seg) call sites by ledger-and-resumability.md and by SKILL.md's
Step 0a copy list, but neither file exists yet anywhere under
assets/templates/ at the time this test was written. The corresponding
tests below fail loudly, naming the missing file -- this is the correct,
intended behavior per this file's own charter ("failing loudly and naming
the offender"), not a bug in the test. See this module's own test
functions for the exact assertion.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"

VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_SRC_DIR / "bootstrap_names.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
REVIEW_ARTIFACT_CHECK_SRC = SCRIPTS_SRC_DIR / "review_artifact_check.py"
MASS_TRANSLATE_WF_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"

for _p in (
    VALIDATE_DRAFT_SRC, DRAFT_READY_SRC, LEDGER_UPDATE_SRC, FINAL_AUDIT_SRC,
    BOOTSTRAP_NAMES_SRC, DRAFT_SHA1_SRC, REVIEW_ARTIFACT_CHECK_SRC,
    MASS_TRANSLATE_WF_SRC,
):
    assert _p.is_file(), f"expected plugin asset not found: {_p}"

NODE_PATH = shutil.which("node")
requires_node = pytest.mark.skipif(
    NODE_PATH is None,
    reason="node not found on PATH -- cannot exercise the real "
           "mass-translate-wf.template.js prompt-builder functions",
)


# ---------------------------------------------------------------------------
# Shared small helpers
# ---------------------------------------------------------------------------

def sha1_of_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def wrong_suffix_draft_name(seg: str) -> str:
    """The exact bug class the spec calls out: the real historiettes-t3
    reference project's own per-language draft filename."""
    return f"{seg}.ru.draft.json"


def wrong_suffix_review_name(seg: str) -> str:
    return f"{seg}.ru.review.json"


# ===========================================================================
# draft_path(seg) call site 1/8 -- scripts/validate_draft.py
# ===========================================================================

DEFAULT_PROFILE = {
    "verse_policy": {"mode": "skip", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def make_validate_draft_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(DEFAULT_PROFILE, sort_keys=False), encoding="utf-8")
    marker = {"owner_profile_path": str(profile_path)}
    (root / ".literary-translator-root.json").write_text(json.dumps(marker), encoding="utf-8")
    return root


def run_validate_draft(root, seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_draft.py"), seg],
        capture_output=True, text=True, timeout=30,
    )


def test_validate_draft_reads_canonical_draft_path(tmp_path):
    """Positive control: a clean segpack+draft placed at exactly
    segments/{seg}.draft.json is what validate_draft.py actually validates
    (OK), proving the canonical path is genuinely wired up end to end."""
    root = make_validate_draft_root(tmp_path)
    seg = "seg01"
    segpack = {
        "seg": seg,
        "blocks": [{"id": "p1", "order_index": 0, "source_html": "<p>Bonjour</p>"}],
        "footnotes": [],
        "verses": [],
    }
    draft = {
        "seg": seg, "blocks": {"p1": "Privet"}, "footnotes": {}, "verses": {}, "names": [], "notes": [],
        # 1.2.0: draft.schema.json requires dispatch_token -- any string is
        # fine here, this test's own invariant is the canonical PATH, not
        # the token's content.
        "dispatch_token": "tok-seg01",
    }
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(json.dumps(segpack), encoding="utf-8")
    (segments_dir / f"{seg}.draft.json").write_text(json.dumps(draft), encoding="utf-8")

    result = run_validate_draft(root, seg)
    assert result.returncode == 0, (
        f"expected OK on a clean draft placed at the canonical path, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert f"[{seg}] OK" in result.stdout


def test_validate_draft_ignores_ru_suffixed_decoy(tmp_path):
    """Negative control: a draft placed ONLY at the legacy-suffixed
    {seg}.ru.draft.json is invisible to validate_draft.py -- it must report
    the draft missing, naming the CANONICAL path, never silently falling
    back to the decoy."""
    root = make_validate_draft_root(tmp_path)
    seg = "seg02"
    segpack = {
        "seg": seg,
        "blocks": [{"id": "p1", "order_index": 0, "source_html": "<p>Bonjour</p>"}],
        "footnotes": [],
        "verses": [],
    }
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(json.dumps(segpack), encoding="utf-8")
    decoy_draft = {"seg": seg, "blocks": {"p1": "DECOY -- must never be read"}, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (segments_dir / wrong_suffix_draft_name(seg)).write_text(json.dumps(decoy_draft), encoding="utf-8")

    result = run_validate_draft(root, seg)
    assert result.returncode != 0, (
        f"a draft that only exists at the legacy-suffixed path must be "
        f"treated as missing, got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    canonical = str(segments_dir / f"{seg}.draft.json")
    assert canonical in result.stdout, (
        f"validate_draft.py must name the canonical path {canonical!r} as "
        f"missing, got:\n{result.stdout}"
    )
    assert wrong_suffix_draft_name(seg) not in result.stdout.replace(canonical, ""), (
        "the decoy's language-suffixed filename must never surface as if "
        "it were an accepted draft location"
    )


# ===========================================================================
# draft_path(seg) call site 2/8 -- scripts/draft_ready.py
# ===========================================================================

def make_draft_ready_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
    (root / "segments").mkdir()
    return root


def run_draft_ready(root, seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_ready.py"), seg],
        capture_output=True, text=True, timeout=30,
    )


def test_draft_ready_reports_ready_from_canonical_path(tmp_path):
    root = make_draft_ready_root(tmp_path)
    seg = "seg01"
    segments_dir = root / "segments"
    segpack = {"blocks": [], "footnotes": [], "verses": []}
    draft = {"seg": seg, "blocks": {}, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (segments_dir / f"segpack_{seg}.json").write_text(json.dumps(segpack), encoding="utf-8")
    (segments_dir / f"{seg}.draft.json").write_text(json.dumps(draft), encoding="utf-8")

    result = run_draft_ready(root, seg)
    assert result.returncode == 0, f"expected READY, got:\n{result.stdout}\n{result.stderr}"
    assert "READY" in result.stdout


def test_draft_ready_ignores_ru_suffixed_decoy(tmp_path):
    root = make_draft_ready_root(tmp_path)
    seg = "seg02"
    segments_dir = root / "segments"
    decoy = {"seg": seg, "blocks": {}, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (segments_dir / wrong_suffix_draft_name(seg)).write_text(json.dumps(decoy), encoding="utf-8")
    # Deliberately no segpack either -- draft_ready.py's own first check is
    # draft existence, so it must report "not ready" before ever touching
    # the segpack, proving the canonical path (not the decoy) is checked
    # first and exclusively.

    result = run_draft_ready(root, seg)
    assert result.returncode == 1, (
        f"a draft only present at the legacy-suffixed path must be "
        f"reported not-ready, got rc={result.returncode}\n{result.stdout}"
    )
    canonical = str(segments_dir / f"{seg}.draft.json")
    assert canonical in result.stdout, (
        f"draft_ready.py must name the canonical path {canonical!r} as "
        f"absent, got:\n{result.stdout}"
    )


# ===========================================================================
# draft_path(seg) + review_path(seg) call site 3/8 & 6/6 -- scripts/ledger_update.py
# ===========================================================================

FULL_CACHE_KEY = {
    "input_sha1": "a1", "style_contract_hash": "b2", "used_terms_hash": "c3",
    "pipeline_version": "v1", "schema_hash": "d4", "prompt_hash": "e5",
    "agent_config_hash": "f6", "profile_semantics_hash": "g7",
    "particle_config_hash": "h8", "source_extraction_hash": "i9",
    "source_input_hash": "j10", "derivation_bundle_hash": "k11",
    "verse_map_hash": "l12", "note_map_hash": "m13", "plugin_bundle_hash": "n14",
}


def make_ledger_update_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    # 1.2.0: ledger_update.py's own draft_content_sha1() re-serializes the
    # draft as canonical (sorted-key, dispatch_token-excluded) JSON before
    # hashing -- a byte-identical duplicate of draft_sha1.py's own
    # algorithm (see that script's 1.2.0 module docstring). Copying the
    # REAL draft_sha1.py alongside it lets this file's tests get a
    # ground-truth draft_sha1 value from the real script (real_draft_sha1()
    # below) instead of reimplementing that canonicalization here.
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    shutil.copy2(SCHEMAS_SRC_DIR / "ledger-record-base.schema.json", schemas_dir / "ledger-record-base.schema.json")
    shutil.copy2(SCHEMAS_SRC_DIR / "ledger-fragment.schema.json", schemas_dir / "ledger-fragment.schema.json")
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def run_ledger_update(root, seg, payload):
    payload_path = root / "runs" / f".ledger_update_payload.{seg}.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_update.py"), seg, "--payload-file", str(payload_path)],
        capture_output=True, text=True, timeout=30,
    )


def real_draft_sha1(root, seg):
    """The REAL draft_sha1.py's own reported digest for the draft currently
    on disk at the canonical path -- never reimplemented here (see
    make_ledger_update_root's own comment above)."""
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"draft_sha1.py failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def test_ledger_update_converged_reads_canonical_draft_path(tmp_path):
    """The ONLY place ledger_update.py reads draft_path(seg) is the
    convergence enrichment path (reviewed_draft_sha1 binding). Proves it
    hashes the file at the canonical path, and that a decoy at the
    legacy-suffixed path is never substituted in its place."""
    root = make_ledger_update_root(tmp_path)
    segments_dir = root / "segments"

    # -- positive: draft at canonical path, review.json's draft_sha1 matches it.
    seg = "seg01"
    draft_bytes = json.dumps({"seg": seg, "blocks": {"p1": "hi"}, "dispatch_token": "tok-seg01"}).encode("utf-8")
    (segments_dir / f"{seg}.draft.json").write_bytes(draft_bytes)
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8"
    )
    # 1.2.0: ledger_update.py's own draft_content_sha1() canonicalizes (and
    # excludes dispatch_token) before hashing, so the ground-truth value
    # here must come from the REAL draft_sha1.py, not a raw-byte hash of
    # this fixture's own on-disk bytes (see real_draft_sha1()'s docstring).
    (segments_dir / f"{seg}.review.json").write_text(
        json.dumps({"draft_sha1": real_draft_sha1(root, seg)}), encoding="utf-8"
    )
    result = run_ledger_update(root, seg, {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})
    assert result.returncode == 0, f"expected convergence to succeed:\n{result.stdout}\n{result.stderr}"
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True

    # -- negative: draft ONLY at the legacy-suffixed decoy path; canonical
    #    path absent. Must be refused as "draft not found", never silently
    #    hashing the decoy instead.
    seg2 = "seg02"
    decoy_bytes = json.dumps({"seg": seg2, "blocks": {"p1": "DECOY"}}).encode("utf-8")
    (segments_dir / wrong_suffix_draft_name(seg2)).write_bytes(decoy_bytes)
    (segments_dir / f"segpack_{seg2}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8"
    )
    (segments_dir / f"{seg2}.review.json").write_text(
        json.dumps({"draft_sha1": sha1_of_bytes(decoy_bytes)}), encoding="utf-8"
    )
    result2 = run_ledger_update(root, seg2, {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})
    assert result2.returncode != 0, (
        f"convergence must be refused when the draft only exists at the "
        f"legacy-suffixed path, got rc={result2.returncode}\n{result2.stdout}"
    )
    stdout2 = json.loads(result2.stdout.strip())
    assert stdout2["success"] is False
    canonical2 = str(segments_dir / f"{seg2}.draft.json")
    assert canonical2 in stdout2["error"], (
        f"error must name the canonical draft path {canonical2!r}, got: {stdout2['error']!r}"
    )


def test_ledger_update_converged_reads_canonical_review_path(tmp_path):
    """Proves the SAME convergence write reads review_path(seg) at exactly
    segments/{seg}.review.json for the reviewed_draft_sha1 binding check --
    a decoy at the legacy-suffixed review filename must never be picked up
    instead."""
    root = make_ledger_update_root(tmp_path)
    segments_dir = root / "segments"

    # -- positive baseline (mirrors the draft_path test's own positive case,
    #    proving this harness is sound before trusting the negative below).
    seg = "seg03"
    draft_bytes = json.dumps({"seg": seg, "blocks": {"p1": "hi"}, "dispatch_token": "tok-seg03"}).encode("utf-8")
    (segments_dir / f"{seg}.draft.json").write_bytes(draft_bytes)
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8"
    )
    # 1.2.0: ground-truth draft_sha1 must come from the REAL draft_sha1.py
    # (canonicalized, dispatch_token-excluded), not a raw-byte hash -- see
    # the sibling draft_path test's own comment above.
    expected_sha1 = real_draft_sha1(root, seg)
    (segments_dir / f"{seg}.review.json").write_text(
        json.dumps({"draft_sha1": expected_sha1}), encoding="utf-8"
    )
    result = run_ledger_update(root, seg, {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})
    assert result.returncode == 0, f"expected convergence to succeed:\n{result.stdout}\n{result.stderr}"
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    fragment = json.loads((root / "runs" / "ledger.d" / f"{seg}.json").read_text(encoding="utf-8"))
    assert fragment["reviewed_draft_sha1"] == expected_sha1

    # -- negative: review.json ONLY at the legacy-suffixed decoy path;
    #    canonical review path absent. Must be refused as "review artifact
    #    not found", never falling back to the decoy's (wrong) draft_sha1.
    seg2 = "seg04"
    draft_bytes2 = json.dumps({"seg": seg2, "blocks": {"p1": "hi"}}).encode("utf-8")
    (segments_dir / f"{seg2}.draft.json").write_bytes(draft_bytes2)
    (segments_dir / f"segpack_{seg2}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8"
    )
    (segments_dir / wrong_suffix_review_name(seg2)).write_text(
        json.dumps({"draft_sha1": sha1_of_bytes(draft_bytes2)}), encoding="utf-8"
    )
    result2 = run_ledger_update(root, seg2, {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})
    assert result2.returncode != 0, (
        f"convergence must be refused when the review artifact only exists "
        f"at the legacy-suffixed path, got rc={result2.returncode}\n{result2.stdout}"
    )
    stdout2 = json.loads(result2.stdout.strip())
    assert stdout2["success"] is False
    canonical_review2 = str(segments_dir / f"{seg2}.review.json")
    assert canonical_review2 in stdout2["error"], (
        f"error must name the canonical review path {canonical_review2!r}, got: {stdout2['error']!r}"
    )


# ===========================================================================
# draft_path(seg) call site 4/8 -- scripts/final_audit.py
# ===========================================================================
#
# final_audit.py's full main() needs a whole-project fixture (manifest.json,
# ledger.d fragments, canon.json, a select_segments.py subprocess) that is
# squarely a different test file's job (its own dedicated
# tests/final_audit.test.py). This file's narrower concern is only the
# canonical-path invariant, so it loads the REAL shipped module directly
# (never reimplementing draft_path) and exercises its draft_path(seg)
# function against genuine file I/O in an isolated fixture -- final_audit.py
# self-anchors its DURABLE_ROOT/SEGMENTS_DIR off its OWN Path(__file__), so
# this still resolves against the fixture root, exactly as production does,
# without needing the rest of main()'s machinery.

def make_final_audit_scripts_dir(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    # final_audit.py does `sys.path.insert(0, SCRIPTS_DIR); import validate_draft
    # as vd; import bootstrap_names as bn` -- both siblings must be physically
    # alongside the copied final_audit.py for that import to resolve.
    shutil.copy2(FINAL_AUDIT_SRC, scripts_dir / "final_audit.py")
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    shutil.copy2(BOOTSTRAP_NAMES_SRC, scripts_dir / "bootstrap_names.py")
    (root / "segments").mkdir()
    return root, scripts_dir


def probe_final_audit_draft_path(scripts_dir, seg):
    """Runs a throwaway subprocess that imports the copied final_audit.py
    module (via importlib, never reimplemented) and calls its REAL
    draft_path(seg) function, printing the resolved path plus whatever is
    actually readable there. A fresh subprocess per call keeps this from
    polluting this test process's own sys.modules with a module literally
    named 'final_audit'/'validate_draft'/'bootstrap_names'."""
    code = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('final_audit_probe', {str(scripts_dir / 'final_audit.py')!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"p = mod.draft_path({seg!r})\n"
        "out = {'resolved': str(p), 'is_file': p.is_file()}\n"
        "if p.is_file():\n"
        "    out['content'] = p.read_text(encoding='utf-8')\n"
        "print(json.dumps(out))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"probing final_audit.py's real draft_path() failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_final_audit_draft_path_resolves_and_reads_canonical_location(tmp_path):
    root, scripts_dir = make_final_audit_scripts_dir(tmp_path)
    seg = "seg01"
    segments_dir = root / "segments"
    canonical_content = "REAL DRAFT CONTENT"
    (segments_dir / f"{seg}.draft.json").write_text(canonical_content, encoding="utf-8")

    out = probe_final_audit_draft_path(scripts_dir, seg)
    resolved = Path(out["resolved"])
    assert resolved.name == f"{seg}.draft.json", f"expected no language suffix, got {resolved.name!r}"
    assert resolved.parent.name == "segments"
    assert resolved.parent.parent == root, (
        f"draft_path(seg) must resolve under THIS fixture's own durable_root "
        f"({root}), got parent-of-segments={resolved.parent.parent}"
    )
    assert out["is_file"] is True
    assert out["content"] == canonical_content, (
        "final_audit.py's real draft_path(seg) must read back the exact "
        "content placed at the canonical path"
    )


def test_final_audit_draft_path_ignores_ru_suffixed_decoy(tmp_path):
    root, scripts_dir = make_final_audit_scripts_dir(tmp_path)
    seg = "seg02"
    segments_dir = root / "segments"
    (segments_dir / wrong_suffix_draft_name(seg)).write_text("DECOY -- must never be read", encoding="utf-8")

    out = probe_final_audit_draft_path(scripts_dir, seg)
    assert out["is_file"] is False, (
        f"final_audit.py's draft_path(seg) must NOT resolve to a file just "
        f"because a legacy-suffixed decoy exists; got resolved={out['resolved']!r}, "
        f"is_file=True unexpectedly"
    )
    assert Path(out["resolved"]).name == f"{seg}.draft.json"


# ===========================================================================
# draft_path(seg) call site 5/8 -- scripts/draft_sha1.py
# ===========================================================================

def make_draft_sha1_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    (root / "segments").mkdir()
    return root


def run_draft_sha1(root, seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, timeout=30,
    )


def test_draft_sha1_hashes_canonical_path_content(tmp_path):
    """Positive control: draft_sha1.py's reported digest is a genuine
    function of draft_path(seg)'s CURRENT content -- reproducible for the
    SAME content, and different for different content at that same
    canonical path. 1.2.0 changed draft_sha1.py's own hashing to a
    canonicalized (sorted-key, dispatch_token-excluded) form (see that
    script's own module docstring), so a plain raw-byte sha1 of this
    fixture's own on-disk bytes is no longer the right comparison target
    here -- verifying the ALGORITHM is draft_sha1.test.py's own job, not
    this file's (whose charter is the canonical PATH invariant); this test
    proves the path invariant without reimplementing that algorithm."""
    root = make_draft_sha1_root(tmp_path)
    seg = "seg01"
    content_a = b'{"seg": "seg01", "blocks": {"p1": "hi"}}'
    (root / "segments" / f"{seg}.draft.json").write_bytes(content_a)

    result_a = run_draft_sha1(root, seg)
    assert result_a.returncode == 0, f"{result_a.stdout}\n{result_a.stderr}"
    digest_a = result_a.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", digest_a), f"expected a sha1 hex digest, got {digest_a!r}"

    # Re-running against the SAME canonical-path content reproduces the
    # identical digest (determinism)...
    result_a2 = run_draft_sha1(root, seg)
    assert result_a2.returncode == 0
    assert result_a2.stdout.strip() == digest_a

    # ...while DIFFERENT content at that SAME canonical path changes it --
    # together, this proves the digest is genuinely a function of
    # draft_path(seg)'s current content, not a fixed/cached value.
    content_b = b'{"seg": "seg01", "blocks": {"p1": "DIFFERENT"}}'
    (root / "segments" / f"{seg}.draft.json").write_bytes(content_b)
    result_b = run_draft_sha1(root, seg)
    assert result_b.returncode == 0, f"{result_b.stdout}\n{result_b.stderr}"
    digest_b = result_b.stdout.strip()
    assert digest_b != digest_a, "different canonical-path content must hash differently"


def test_draft_sha1_ignores_ru_suffixed_decoy(tmp_path):
    root = make_draft_sha1_root(tmp_path)
    seg = "seg02"
    segments_dir = root / "segments"
    decoy = b"DECOY CONTENT -- must never be hashed"
    (segments_dir / wrong_suffix_draft_name(seg)).write_bytes(decoy)

    result = run_draft_sha1(root, seg)
    assert result.returncode != 0, (
        f"a draft only present at the legacy-suffixed path must be treated "
        f"as not found, got rc={result.returncode}, stdout={result.stdout!r}"
    )
    assert result.stdout.strip() != sha1_of_bytes(decoy), (
        "must never print the decoy's sha1 as if it were the real draft's"
    )
    canonical = str(segments_dir / f"{seg}.draft.json")
    assert canonical in result.stderr, (
        f"draft_sha1.py must name the canonical path {canonical!r} as not "
        f"found, got stderr:\n{result.stderr}"
    )


# ===========================================================================
# draft_path(seg) call sites 6/8 & 7/8 -- the two one-time-seed TASK templates
# ===========================================================================
#
# SKILL.md's Step 0a copy list and ledger-and-resumability.md both name
# templates/review_TASK.template.md and templates/translate_TASK.template.md
# as required, canonical-path-following call sites. At the time this test
# was written, NEITHER file exists yet anywhere under assets/templates/ --
# this is a genuine, currently-real gap in the plugin build (not a flaw in
# this test), and per this test file's own charter these two tests fail
# loudly, naming the missing file, rather than being silently skipped or
# weakened. Once each template is authored, these tests additionally lock
# down that its content never regresses to the legacy-suffixed naming.

def _check_task_template_draft_path_convention(template_path):
    assert template_path.is_file(), (
        f"required draft_path(seg) call site missing: {template_path} does "
        f"not exist under assets/templates/ -- ledger-and-resumability.md "
        f"and SKILL.md's Step 0a both require this one-time-seed template "
        f"to exist and to reference the canonical segments/{{seg}}.draft.json "
        f"convention (no target-language suffix)"
    )
    text = template_path.read_text(encoding="utf-8")
    assert ".ru.draft.json" not in text, (
        f"{template_path} must never reference the legacy language-suffixed "
        f"draft filename convention"
    )


def test_review_task_template_follows_draft_path_convention():
    _check_task_template_draft_path_convention(TEMPLATES_SRC_DIR / "review_TASK.template.md")


def test_translate_task_template_follows_draft_path_convention():
    _check_task_template_draft_path_convention(TEMPLATES_SRC_DIR / "translate_TASK.template.md")


# ===========================================================================
# JS prompt-builder harness -- shared by the remaining call sites:
#   draft_path(seg)  site 8/8: mass-translate-wf.template.js (translatePrompt/fixPrompt)
#   review_path(seg) site 1/6: reviewDispatchPrompt (1.2.0; formerly reviewPrompt -- writes it)
#   review_path(seg) site 2/6: readReviewPrompt (1.2.0, NEW -- reads it directly)
#   review_path(seg) site 3/6: verifyReviewArtifactPrompt (reads it, indirectly; UNCHANGED)
#   review_path(seg) site 4/6: fixPrompt (1.3.6/#132 option b, NEW -- reads it directly)
#
# 1.2.0 (CONTRACT §8) replaced the old single reviewPrompt/callReview call
# with a DISPATCH -> WAIT -> READ -> CHECK sequence: reviewDispatchPrompt
# (codex, writes review_path(seg)) -> reviewWaitPrompt (bounded poll of
# review_ready.py) -> readReviewPrompt (mechanical read, returns the
# 4-field REVIEW_SCHEMA projection) -> verifyReviewArtifactPrompt (the
# artifact-check step, UNCHANGED, kept as a separate prompt-builder
# function per the PLAN's own test-update note). reviewWaitPrompt is
# included in the probe below for basic instantiation/arity coverage; its
# OWN bounded-poll content is tests/bounded_poll_present.test.py's job, not
# this file's (whose charter is the canonical draft_path/review_path
# invariant, not the #97 poll-boundedness invariant).
# ===========================================================================
#
# The real mass-translate-wf.template.js has top-level `await pipeline(...)`
# statements near its end that depend on this Workflow tool's own live
# agent()/pipeline()/log() globals -- those are irrelevant to the prompt
# TEXT this test cares about and are never reached by any of the functions
# under test here. Rather than stub the whole Workflow runtime, this harness
# slices the real file at its own documented boundary (immediately before
# the `batch_agent_cap preflight` block, i.e. before the only code that
# would actually invoke pipeline()/agent()) -- so every function definition
# above that point (the actual call sites under test) is the REAL, unedited
# shipped source, substituted exactly as the orchestrating session would at
# instantiation time, and run under real node.

_JS_CUT_MARKER = "const estimatedCalls"


def _instantiate_and_slice_js(durable_root_str):
    raw = MASS_TRANSLATE_WF_SRC.read_text(encoding="utf-8")
    assert _JS_CUT_MARKER in raw, (
        "mass-translate-wf.template.js no longer contains the expected "
        f"{_JS_CUT_MARKER!r} slice boundary -- update this test's harness "
        "to match the file's current structure"
    )
    head, _, _tail = raw.partition(_JS_CUT_MARKER)

    substitutions = {
        "{{DURABLE_ROOT}}": durable_root_str,
        # 1.2.0 (CONTRACT §2): a stable fixture run id -- colon-free
        # YYYYMMDDTHHMMSSZ form, matching the allowlist
        # ^[A-Za-z0-9][A-Za-z0-9._-]*$. Only this file's own no-leftover-
        # token assertion below cares that it's substituted at all; the
        # exact value is irrelevant to every prompt-text assertion in this
        # file (they only check draft_path(seg)/review_path(seg) strings).
        "{{RUN_ID}}": "20260710T000000Z",
        "{{SOURCE_LANG}}": "fr",
        "{{TARGET_LANG}}": "ru",
        "{{MAX_FIX_ROUNDS}}": "3",
        "{{BATCH_AGENT_CAP}}": "999",
        "{{VERSE_POLICY_INSTRUCTION_BLOCK}}": "Test verse policy instructions.",
    }
    for token, value in substitutions.items():
        head = head.replace(token, value)
    # The file's sole `export` keyword (on `export const meta = {...}`) is
    # ESM syntax; strip it so the sliced body runs as a plain CommonJS
    # script under `node file.js` (no --input-type=module dance needed).
    head = head.replace("export const meta", "const meta", 1)
    # A genuine unresolved substitution token always has this ALL-CAPS/
    # underscore shape ({{RUN_ID}}, {{DURABLE_ROOT}}, ...) -- every token
    # this template documents is named that way (see the template's own
    # header comment). A blind "{{" / "}}" substring check is too broad:
    # this file's own "RELAXED union" schema-literal comment legitimately
    # contains the literal text `{type:"string"}}` (a nested JS object
    # literal's own closing braces, not a template token), which a plain
    # substring check would wrongly flag as an unresolved token.
    leftover = re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", head)
    assert leftover is None, (
        f"an instantiation substitution token survived unresolved "
        f"({leftover.group(0)!r}) -- the template's own token list "
        f"changed; update `substitutions` above"
    )
    return head, raw


def run_prompt_probe(tmp_path, durable_root_str, seg, round_num, rev_obj):
    """Instantiates + slices the real template, appends a small footer that
    calls the real prompt-builder functions under test (translatePrompt,
    reviewDispatchPrompt, reviewWaitPrompt, readReviewPrompt, fixPrompt,
    verifyReviewArtifactPrompt -- the 1.2.0 DISPATCH/WAIT/READ/CHECK
    sequence, see the JS prompt-builder harness section comment above),
    and executes the whole thing under real node."""
    assert NODE_PATH is not None, "node executable not found on PATH -- required to run this test file"
    head, raw = _instantiate_and_slice_js(durable_root_str)
    footer = (
        "\nvar __seg = " + json.dumps(seg) + ";\n"
        "var __round = " + json.dumps(round_num) + ";\n"
        "var __roundLabel = String(__round);\n"
        "var __revObj = " + json.dumps(rev_obj) + ";\n"
        "var __dispatchToken = RUN_ID + \":\" + __seg + \":r\" + __roundLabel;\n"
        "var __out = {\n"
        "  translatePrompt: translatePrompt(__seg),\n"
        "  reviewDispatchPrompt: reviewDispatchPrompt(__seg, __roundLabel),\n"
        "  reviewWaitPrompt: reviewWaitPrompt(__seg, __dispatchToken),\n"
        "  readReviewPrompt: readReviewPrompt(__seg),\n"
        "  fixPrompt: fixPrompt(__seg, __round, __revObj),\n"
        "  verifyReviewArtifactPrompt: verifyReviewArtifactPrompt(__seg, __revObj),\n"
        "  arities: {\n"
        "    translatePrompt: translatePrompt.length,\n"
        "    reviewDispatchPrompt: reviewDispatchPrompt.length,\n"
        "    reviewWaitPrompt: reviewWaitPrompt.length,\n"
        "    readReviewPrompt: readReviewPrompt.length,\n"
        "    fixPrompt: fixPrompt.length,\n"
        "    verifyReviewArtifactPrompt: verifyReviewArtifactPrompt.length\n"
        "  }\n"
        "};\n"
        "console.log(JSON.stringify(__out));\n"
    )
    script = 'var args = "[]";\n' + head + footer
    script_path = tmp_path / "prompt_probe.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run([NODE_PATH, str(script_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"node execution of the real, instantiated mass-translate-wf.template.js "
        f"prompt builders failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1]), raw


@requires_node
def test_mass_translate_wf_static_source_never_mentions_legacy_suffix():
    """Blanket, static regression lock over the whole file: neither
    language-suffixed variant may appear anywhere in the shipped source,
    substituted or not."""
    raw = MASS_TRANSLATE_WF_SRC.read_text(encoding="utf-8")
    assert ".ru.draft.json" not in raw
    assert ".ru.review.json" not in raw


@requires_node
def test_mass_translate_wf_translate_prompt_writes_canonical_draft_path(tmp_path):
    """1.2.0: translatePrompt(seg) also embeds a dispatch_token/RUN_ID
    (CONTRACT §2) -- the assertion below only checks the canonical
    draft_path(seg) SUBSTRING is still present (a substring check already
    tolerates/ignores any extra token content around it; it never requires
    the whole prompt text to match exactly), so no change was needed here
    beyond this note."""
    root_str = str(tmp_path / "durable_root")
    seg = "segA1"
    out, _raw = run_prompt_probe(tmp_path, root_str, seg, 1, {
        "clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef",
    })
    expected_draft = f"{root_str}/segments/{seg}.draft.json"
    assert expected_draft in out["translatePrompt"], (
        f"translatePrompt(seg) must instruct writing to the canonical "
        f"draft_path(seg); expected substring {expected_draft!r} not found "
        f"in:\n{out['translatePrompt']}"
    )
    assert f"{seg}.ru.draft.json" not in out["translatePrompt"]
    assert out["arities"]["translatePrompt"] == 1


@requires_node
def test_mass_translate_wf_review_dispatch_prompt_reads_draft_and_writes_review_path(tmp_path):
    """review_path(seg) call site 1/6: reviewDispatchPrompt (1.2.0;
    formerly the single combined reviewPrompt) is the WRITER -- the
    DISPATCH half of CONTRACT §8's restructured review sequence
    (reviewDispatchPrompt -> reviewWaitPrompt -> readReviewPrompt ->
    verifyReviewArtifactPrompt). Same invariant the old reviewPrompt test
    locked (reads draft_path(seg), writes review_path(seg), no legacy
    suffix, an explicit "write" instruction), repointed to the new
    2-argument function (seg, roundLabel) 1.2.0 split it into."""
    root_str = str(tmp_path / "durable_root")
    seg = "segA2"
    out, _raw = run_prompt_probe(tmp_path, root_str, seg, 1, {
        "clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef",
    })
    expected_draft = f"{root_str}/segments/{seg}.draft.json"
    expected_review = f"{root_str}/segments/{seg}.review.json"
    assert expected_draft in out["reviewDispatchPrompt"], (
        f"reviewDispatchPrompt(seg, roundLabel) must read the canonical "
        f"draft_path(seg); expected substring {expected_draft!r} not found "
        f"in:\n{out['reviewDispatchPrompt']}"
    )
    assert expected_review in out["reviewDispatchPrompt"], (
        f"reviewDispatchPrompt(seg, roundLabel) must write the canonical "
        f"review_path(seg); expected substring {expected_review!r} not "
        f"found in:\n{out['reviewDispatchPrompt']}"
    )
    assert f"{seg}.ru.draft.json" not in out["reviewDispatchPrompt"]
    assert f"{seg}.ru.review.json" not in out["reviewDispatchPrompt"]
    assert out["arities"]["reviewDispatchPrompt"] == 2, (
        "reviewDispatchPrompt is a deliberate 2-argument (seg, roundLabel) "
        "shape -- CONTRACT §8"
    )
    # It is specifically the WRITER -- the write instruction is phrased as
    # an explicit "write" sentence, not merely a passing mention.
    assert "write" in out["reviewDispatchPrompt"].lower()


@requires_node
def test_mass_translate_wf_read_review_prompt_reads_canonical_review_path(tmp_path):
    """review_path(seg) call site 2/6 (1.2.0, NEW): readReviewPrompt is a
    direct, mechanical READER of review_path(seg) -- distinct from
    verifyReviewArtifactPrompt's INDIRECT read (delegated entirely to
    review_artifact_check.py, see the next test group). Pre-1.2.0 there
    was no separate read step at all (the single reviewPrompt wrote and
    the calling Workflow trusted its own schema-validated return);
    CONTRACT §8 splits DISPATCH (write) from this mechanical read."""
    root_str = str(tmp_path / "durable_root")
    seg = "segA2b"
    out, _raw = run_prompt_probe(tmp_path, root_str, seg, 1, {
        "clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef",
    })
    expected_review = f"{root_str}/segments/{seg}.review.json"
    assert expected_review in out["readReviewPrompt"], (
        f"readReviewPrompt(seg) must read the canonical review_path(seg); "
        f"expected substring {expected_review!r} not found in:\n{out['readReviewPrompt']}"
    )
    assert f"{seg}.ru.review.json" not in out["readReviewPrompt"]
    assert out["arities"]["readReviewPrompt"] == 1


@requires_node
def test_mass_translate_wf_verify_review_artifact_prompt_delegates_the_read(tmp_path):
    """review_path(seg) call site 3/6: verifyReviewArtifactPrompt is a
    READER, but INDIRECTLY -- UNCHANGED by 1.2.0 (kept as a separate
    prompt-builder function, per the PLAN's own test-update note). Per
    references/workflow-schema-validation.md,
    the actual byte-for-byte read of review_path(seg) is performed by
    review_artifact_check.py (see that script's own dedicated test group
    below), never by the JS prompt text itself. This test locks down that
    indirection: verifyReviewArtifactPrompt's own generated text invokes
    review_artifact_check.py for this exact segment, and does NOT itself
    reference review_path(seg)'s filename directly (proving it never
    tries to read/compare the file itself)."""
    root_str = str(tmp_path / "durable_root")
    seg = "segA3"
    rev_obj = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "cafef00d"}
    out, _raw = run_prompt_probe(tmp_path, root_str, seg, 1, rev_obj)

    verify_text = out["verifyReviewArtifactPrompt"]
    expected_invocation = f"review_artifact_check.py {seg} --expected-file"
    assert expected_invocation in verify_text, (
        f"verifyReviewArtifactPrompt(seg, revObj) must invoke "
        f"review_artifact_check.py for this exact segment; expected "
        f"substring {expected_invocation!r} not found in:\n{verify_text}"
    )
    assert ".review.json" not in verify_text, (
        "verifyReviewArtifactPrompt's own prompt text must never reference "
        "review_path(seg)'s filename directly -- the actual read is "
        "entirely delegated to review_artifact_check.py"
    )
    assert out["arities"]["verifyReviewArtifactPrompt"] == 2


@requires_node
def test_mass_translate_wf_fix_prompt_reads_canonical_review_path(tmp_path):
    """Design confirmation, UPDATED for 1.3.6 (#132 option b): fixPrompt(seg,
    round, revObj) is now DELIBERATELY one of review_path(seg)'s readers
    (a reversal of the round-60 in-memory design -- see
    references/engine-loop.md's R1 for the full history). It instructs the
    agent to READ review_path(seg) and apply every entry in its on-disk
    findings[] array, closing the gap where a read-agent transcription slip
    in issue/suggest text (while loc/severity still matched) could reach
    the fixer via an in-memory copy that review_artifact_check.py's
    narrowed #132 compare no longer binds byte-for-byte. `revObj` (the 3rd
    argument) is kept -- other callers still use it for the convergence
    decision and the review-artifact gate's own `--expected-file` -- but
    fixPrompt's own prompt text must no longer splice its JSON in as the
    findings source at all. This test asserts the affirmative read
    instruction, the absence of the old "do not re-read ... for findings"
    negation, and the absence of the revObj fixture's own distinctive
    marker text (proving the in-memory object is genuinely not spliced in
    anymore, not merely re-worded)."""
    root_str = str(tmp_path / "durable_root")
    seg = "segA4"
    round_num = 2
    rev_obj = {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "minor", "issue": "x", "suggest": "y"}],
        "draft_sha1": "0123456789abcdef",
    }
    out, _raw = run_prompt_probe(tmp_path, root_str, seg, round_num, rev_obj)

    fix_text = out["fixPrompt"]
    assert out["arities"]["fixPrompt"] == 3, (
        "fixPrompt must keep its deliberate 3-argument (seg, round, revObj) "
        "shape -- reverting to the proven reference's 2-argument "
        "fixPrompt(seg, round) shape is the exact regression "
        "references/gotchas.md item 5 warns against"
    )

    expected_review = f"{root_str}/segments/{seg}.review.json"
    assert expected_review in fix_text, (
        f"fixPrompt(seg, round, revObj) must instruct reading the canonical "
        f"review_path(seg); expected substring {expected_review!r} not "
        f"found in:\n{fix_text}"
    )
    assert f"Read {expected_review}" in fix_text, (
        f"fixPrompt must issue an explicit, affirmative READ instruction for "
        f"review_path(seg), not merely mention the path in passing; expected "
        f"substring 'Read {expected_review}' not found in:\n{fix_text}"
    )
    assert "findings[]" in fix_text or "findings[" in fix_text, (
        "fixPrompt must instruct applying the on-disk findings[] array"
    )

    stale_negation = f"do not re-read {expected_review} for findings"
    assert stale_negation not in fix_text, (
        "fixPrompt must no longer carry the pre-1.3.6 'do not re-read ... "
        "for findings' negation -- it now instructs an affirmative read "
        "(#132 option b)"
    )

    # The revObj JSON itself must NO LONGER be spliced in as the findings
    # source -- draft_sha1 is a distinctive marker unique to this test's
    # revObj fixture; its ABSENCE proves the in-memory object is genuinely
    # not spliced into the prompt anymore.
    assert rev_obj["draft_sha1"] not in fix_text, (
        "fixPrompt must no longer splice revObj's own JSON text into its "
        "prompt -- the fixer now reads review_path(seg) from disk instead "
        "(#132 option b)"
    )

    # draft_path(seg) IS legitimately read/rewritten by fixPrompt (it edits
    # an existing draft) -- confirm that separately, and that no
    # legacy-suffixed variant appears anywhere in its output.
    expected_draft = f"{root_str}/segments/{seg}.draft.json"
    assert expected_draft in fix_text
    assert f"{seg}.ru.draft.json" not in fix_text
    assert f"{seg}.ru.review.json" not in fix_text


# ===========================================================================
# review_path(seg) call site 5/6 -- scripts/review_artifact_check.py
# ===========================================================================

def make_review_artifact_check_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REVIEW_ARTIFACT_CHECK_SRC, scripts_dir / "review_artifact_check.py")
    (root / "segments").mkdir()
    return root


def run_review_artifact_check(root, seg, expected_file):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "review_artifact_check.py"), seg, "--expected-file", str(expected_file)],
        capture_output=True, text=True, timeout=30,
    )


def test_review_artifact_check_reads_canonical_review_path(tmp_path):
    root = make_review_artifact_check_root(tmp_path)
    seg = "seg01"
    rev_obj = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "aa11bb22"}
    (root / "segments" / f"{seg}.review.json").write_text(json.dumps(rev_obj), encoding="utf-8")
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(rev_obj), encoding="utf-8")

    result = run_review_artifact_check(root, seg, expected_file)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert json.loads(result.stdout.strip()) == {"match": True}


def test_review_artifact_check_ignores_ru_suffixed_decoy(tmp_path):
    root = make_review_artifact_check_root(tmp_path)
    seg = "seg02"
    segments_dir = root / "segments"
    decoy = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "DECOY"}
    (segments_dir / wrong_suffix_review_name(seg)).write_text(json.dumps(decoy), encoding="utf-8")
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(decoy), encoding="utf-8")

    result = run_review_artifact_check(root, seg, expected_file)
    assert result.returncode != 0, (
        f"a review artifact only present at the legacy-suffixed path must "
        f"be treated as not found, got rc={result.returncode}\nstdout:\n{result.stdout}"
    )
    assert '"match"' not in result.stdout, (
        "a genuine script-level failure must never print a {'match': ...} line"
    )
    canonical = str(segments_dir / f"{seg}.review.json")
    assert canonical in result.stderr, (
        f"review_artifact_check.py must name the canonical path {canonical!r} "
        f"as not found, got stderr:\n{result.stderr}"
    )


# ===========================================================================
# Global static regression lock -- every asset under scripts/ and
# templates/, no exceptions, must never actually CONSTRUCT a path using the
# legacy per-language draft/review filename convention.
#
# Every shipped script's docstring deliberately and repeatedly CONTRASTS
# the canonical no-suffix convention against the real historiettes-t3
# reference project's own per-language naming in prose, e.g. "a divergence
# from the real historiettes-t3 reference project's own .ru.draft.json
# naming" -- that explanatory prose legitimately contains the literal
# substring ".ru.draft.json" and must NOT be flagged (a per-script
# behavioral test above already proves, via real file I/O, that none of
# these scripts' actual draft_path()/review_path() functions resolve to
# that filename). What WOULD be a genuine bug is the literal f-string
# path-construction shape a regression could introduce, e.g.
# `f"{seg}.ru.draft.json"` -- that is what this check actually hunts for.
# ===========================================================================

def test_no_shipped_script_or_template_constructs_legacy_suffixed_paths():
    offenders = []
    for path in sorted(SCRIPTS_SRC_DIR.glob("*.py")) + sorted(TEMPLATES_SRC_DIR.glob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for bad in ("{seg}.ru.draft.json", "{seg}.ru.review.json", "' + seg + '.ru.", '" + seg + ".ru.'):
            if bad in text:
                offenders.append(f"{path.relative_to(PLUGIN_ROOT)} contains path-construction shape {bad!r}")
    assert not offenders, (
        "found code that actually BUILDS a legacy target-language-suffixed "
        "path (a ported script hardcoding the real historiettes-t3 "
        "reference project's own .ru.draft.json naming is a documented bug "
        "class, per references/ledger-and-resumability.md) -- this is "
        "distinct from, and narrower than, the explanatory prose every "
        "shipped docstring legitimately carries when contrasting the two "
        "conventions:\n" + "\n".join(offenders)
    )
