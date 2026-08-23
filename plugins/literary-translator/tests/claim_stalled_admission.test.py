"""tests/claim_stalled_admission.test.py -- #455: `--from-stalled`, the third
claim admission profile in select_segments.py.

THE POPULATION. A unit that converged once and whose convergence bookkeeping
never landed: materialized ledger `status: in_progress`, the
`.ever_converged.<seg>` sentinel PRESENT, NO `reviewed_draft_sha1`, a draft on
disk, and a stored review whose `draft_sha1` describes a draft that no longer
exists. `--from-cap` refuses it (wrong status -- `in_progress`, not
non_converged/reason=cap; since 1.27.0/#537 the sentinel alone no longer
refuses under that profile) and
`--from-converged` refuses it (wrong status, and there is no drift baseline), so
before this profile the only route was a hand-driven `ledger_update.py`
convergence write.

WHAT THIS FILE IS ORGANISED AROUND is the split the profile itself is built on
(select_segments.py's own `FROM_STALLED_DISCLOSURE`): two facts are PROVED by
the kernel and everything else is the operator's ASSERTION. So the tests come in
three kinds, and conflating them is the failure this file is shaped to avoid:

  * the CLOSED CONDITION LIST over artifacts -- one test per condition, each
    fixture violating exactly ONE axis away from an admitting control, and each
    assertion pinning the refusal's own named reason. A refusal test that leaves
    a second condition also violated is answered by whichever reason is appended
    first, and then stays green through the deletion of the condition it claims
    to cover.
  * the TWO KERNEL LEASES -- `runs/.driver.lock` and
    `segments/.codex_job.<seg>.lock`. These are not asserted by reading the
    code: this test process takes the real lock with the real `fcntl.flock`, and
    the "held across the whole decision" property is proved at SYNCHRONISATION
    POINTS INSIDE a live selector run (see `SYNC_VALIDATE_DRAFT_PY` and
    `SYNC_CLAIM_RECORD_PY`), where a second, independent `LOCK_EX|LOCK_NB` from
    this process must still be refused. A probe taken before or after the run
    would answer a question about a moment already past.
  * the ASSERTION ITSELF, which cannot be tested as a behaviour because there is
    no behaviour -- so what is pinned is the DISCLOSURE WORDING, so it cannot
    silently regress into a promise the profile does not make.

NO FIXTURE HERE CLAIMS GENERAL MID-FLIGHT DETECTION. A Workflow fix turn or a
Workflow phase outside a codex job presents every artifact this profile keys on
and is admissible BY DESIGN; asserting otherwise would encode a guarantee the
profile explicitly disclaims.

HOUSE STYLE. Self-contained, no cross-file imports (see
tests/claim_selector.test.py's own module docstring for the same rule stated
explicitly), a real durable root on disk, and the SHIPPED select_segments.py
driven as a subprocess exactly as production drives it -- S1/S2 run for real
against a genuinely valid draft/segpack pair, and only cache_key.py is stubbed
(its 15-field hashing has its own dedicated test file).
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

SELECT_SCRIPT_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
REJECT_REVIEW_SRC = SCRIPTS_SRC_DIR / "reject_review.py"

for _src in (
    SELECT_SCRIPT_SRC, LEDGER_MERGE_SRC, DRAFT_READY_SRC, VALIDATE_DRAFT_SRC,
    CLAIM_RECORD_SRC, DRAFT_SHA1_SRC, REJECT_REVIEW_SRC,
):
    assert _src.is_file(), f"required sibling script not found at {_src}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture harness -- the same `make_durable_root` convention
# tests/claim_selector.test.py and tests/select_segments.test.py already use.
# ---------------------------------------------------------------------------

CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]

FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# validate_draft.py's own load_profile()/ProfileConfig requires all three
# sections -- the same proven-good fixture tests/claim_selector.test.py and
# tests/validate_draft.test.py use.
DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}

FN_PH = "⟦FNREF_1⟧"
V_PH_A = "⟦VERSE_vA⟧"
V_PH_B = "⟦VERSE_vB⟧"

RUN_ID = "20260810T000000Z"
SOURCE_RUN_ID = "20260801T090000Z"
OTHER_RUN_ID = "20260811T000000Z"

# The two live units this profile was built for, by name. Their SHAPES are what
# the fixtures reproduce (measured read-only against the Hebrew root's
# runs/ledger.json on 2026-08-11): both in_progress with reason None, no
# reviewed_draft_sha1, sentinel present, draft and review on disk, and the
# stored review's draft_sha1 disagreeing with the current draft -- differing
# from each other ONLY in `clean`. That disagreement is the measured reason the
# profile must not constrain that field, so both are carried here rather than
# one standing in for the other.
LIVE_CLEAN_SEG = "seg21"
LIVE_DIRTY_SEG = "FRONTBACK:errata_02"


def make_durable_root(tmp_path, name="durable_root"):
    """Isolated durable_root carrying every REAL sibling select_segments.py
    shells out to (validate_draft.py, draft_ready.py) or imports
    (claim_record.py), the REAL ledger_merge.py, the REAL schemas, and a
    stubbed cache_key.py -- plus profile.yml, the Step-0a ownership marker and
    an empty canon.json."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for script_name, src in (
        ("select_segments.py", SELECT_SCRIPT_SRC),
        ("ledger_merge.py", LEDGER_MERGE_SRC),
        ("draft_ready.py", DRAFT_READY_SRC),
        ("validate_draft.py", VALIDATE_DRAFT_SRC),
        ("claim_record.py", CLAIM_RECORD_SRC),
        ("draft_sha1.py", DRAFT_SHA1_SRC),
        ("reject_review.py", REJECT_REVIEW_SRC),
    ):
        shutil.copy2(src, scripts_dir / script_name)
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC, root / "schemas")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(DEFAULT_PROFILE, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    write_canon(root, {})
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fragment(root, seg, record):
    (root / "runs" / "ledger.d" / f"{seg}.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def write_canon(root, entries):
    (root / "canon.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def draft_content_sha1_of(doc: dict) -> str:
    """Independent ground-truth reimplementation of draft_content_sha1()
    (projects out dispatch_token, sorted-key compact-separator canonical JSON)
    -- an oracle, matching tests/claim_selector.test.py's own copy. It is
    CROSS-CHECKED against the shipped draft_sha1.py by
    test_the_fixture_hash_oracle_agrees_with_the_shipped_draft_sha1, so a
    fixture built on it cannot silently follow a drifting production hash."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    raw = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def clean_segpack(seg):
    """One prose block with a footnote anchor, two standalone verses each
    parented to their own block -- the proven-valid shape
    tests/validate_draft.test.py's own clean_segpack() uses, so S1
    (validate_draft.py) genuinely passes rather than being stubbed away."""
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1, "source_html": "<p>Premiere ligne<br/>Deuxieme ligne</p>"},
            {"id": "vblockB", "order_index": 2, "source_html": "<p>Autre premiere<br/>Autre deuxieme</p>"},
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "canon_map": {},
        "generation_hashes": {
            "source_extraction_hash": "sxh-0",
            "source_input_hash": "sih-0",
            "particle_config_hash": "pch-0",
            "derivation_bundle_hash": "dbh-0",
        },
    }


def clean_draft(seg):
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
                "literal_gloss": "The first line means one thing, the second means another",
            },
            "vB": {
                "rendered": "Another line rendered here\nAnother second line here",
                "literal_gloss": "This gloss says something different from the rendering above",
            },
        },
        "names": [],
        "notes": [],
    }


def write_segpack(root, seg, segpack):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )


def write_draft_doc(root, seg, draft):
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def write_review(root, seg, review):
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps(review, ensure_ascii=False), encoding="utf-8"
    )


def mark_ever_converged(root, seg):
    (root / "segments" / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")


def make_run_dir(root, run_id):
    """The SOURCE run's own runs/<run_id>/ directory -- S3 requires it to
    exist. The stub input.digest keeps the UNRELATED #409 Step 3
    resume-integrity gate (which scans every draft's dispatch_token
    project-wide) silently satisfied; it is not this file's subject."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"
    if not digest_path.exists():
        digest_path.write_text(json.dumps({"digest": f"stub-{run_id}"}), encoding="utf-8")


def in_progress_fragment(rounds=1, **overrides):
    """The stalled unit's materialized ledger shape, measured: status
    in_progress, no `reason`, and -- the defining absence -- no
    `reviewed_draft_sha1` at all. `cache_key` is absent too: a fragment written
    for an in_progress unit carries none, which is why claim_record.py's own
    note about a missing historical cache key had to stop saying 'expected for
    --from-cap' alone.

    `reason` is OMITTED rather than written as null. The live units read as
    `reason: None` through `record.get("reason")`, which is what an ABSENT key
    yields -- and ledger-record-base.schema.json types `reason` as a string, so
    a literal null fails materialization in ledger_merge.py before any claim
    gate is reached. Writing the null would make every fixture here fail for a
    schema reason and prove nothing about --from-stalled."""
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "in_progress",
        "rounds": rounds,
    }
    record.update(overrides)
    return record


def build_from_stalled_segment(
    root,
    seg,
    fixture_keys: dict,
    *,
    source_run_id=SOURCE_RUN_ID,
    clean_review=True,
    sentinel_present=True,
    ledger_status="in_progress",
    fragment_overrides=None,
    review_overrides=None,
    review_stale=True,
    dispatch_token=True,
    source_run_dir=True,
):
    """The #455 population, with one keyword per axis so a test can flip
    EXACTLY ONE away from the admitting default -- the shape every refusal test
    in this file depends on, since a fixture violating two conditions is
    answered by whichever reason lands first.

    Defaults reproduce the measured live shape: in_progress / reason None /
    sentinel present / no reviewed_draft_sha1 / draft and review on disk /
    review.draft_sha1 disagreeing with the current draft's content sha1.
    `clean_review` selects between the two real units -- seg21's clean stale
    verdict and errata_02's dirty one -- and NEITHER is the "correct" one: the
    profile does not constrain that field.

    Returns the facts a caller needs to build a CURRENT review or an authentic
    continuation claim without recomputing them."""
    segpack = clean_segpack(seg)
    write_segpack(root, seg, segpack)

    draft = clean_draft(seg)
    # The hand-corrected bytes the stored review no longer describes. This is
    # what makes the stored verdict stale, and it is also what an operator
    # would lose if a concurrent writer landed over it.
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-corrected after the driver died."
    if dispatch_token:
        draft["dispatch_token"] = f"{source_run_id}:{seg}"
    write_draft_doc(root, seg, draft)
    current_sha1 = draft_content_sha1_of(draft)

    if source_run_dir:
        make_run_dir(root, source_run_id)

    ck = make_cache_key(seg)
    fixture_keys[seg] = ck

    if review_stale:
        # A well-formed sha1 of the draft as it stood BEFORE the hand
        # correction -- a genuinely reachable prior state, not an impossible
        # constant, so the staleness under test is the real one.
        pre_edit = json.loads(json.dumps(draft))
        pre_edit["blocks"]["p1"] = clean_draft(seg)["blocks"]["p1"]
        reviewed_sha1 = draft_content_sha1_of(pre_edit)
        assert reviewed_sha1 != current_sha1
    else:
        reviewed_sha1 = current_sha1

    review = {
        "clean": bool(clean_review),
        "coverage_ok": True,
        "findings": [] if clean_review else [
            {"loc": "p1", "severity": "major",
             "issue": "the source names a phrase absent from the block",
             "suggest": "restore the omitted clause"},
        ],
        "draft_sha1": reviewed_sha1,
        "dispatch_token": f"{source_run_id}:{seg}:r1",
    }
    if review_overrides:
        review.update(review_overrides)
    write_review(root, seg, review)

    if sentinel_present:
        mark_ever_converged(root, seg)

    fragment = in_progress_fragment()
    fragment["status"] = ledger_status
    if fragment_overrides:
        fragment.update(fragment_overrides)
    write_fragment(root, seg, fragment)

    return {
        "current_sha1": current_sha1,
        "reviewed_sha1": reviewed_sha1,
        "draft": draft,
        "review": review,
        "source_run_id": source_run_id,
    }


def stalled_project(tmp_path, segs=(LIVE_CLEAN_SEG,), **kwargs):
    """A durable root holding exactly `segs`, every one of them in the stalled
    shape, manifest and cache keys written. Returns (root, {seg: facts})."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    facts = {}
    for i, seg in enumerate(segs):
        facts[seg] = build_from_stalled_segment(
            root, seg, fixture_keys, clean_review=(i % 2 == 0), **kwargs
        )
    write_manifest(root, list(segs))
    write_fixture_cache_keys(root, fixture_keys)
    return root, facts


def run_select(root, *extra_args, timeout=60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def claim_stalled(root, *segs, run_id=RUN_ID, extra=()):
    return run_select(
        root, "--from-stalled", ",".join(segs), "--run-id", run_id,
        "--run-resume", "false", *extra,
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def claim_marker(root, seg, run_id=RUN_ID):
    return root / "runs" / run_id / f".claimed.{seg}"


def driver_lock(root):
    return root / "runs" / ".driver.lock"


def job_lock(root, seg):
    return root / "segments" / f".codex_job.{seg}.lock"


def hold(path: Path):
    """Take the REAL `fcntl.flock(LOCK_EX|LOCK_NB)` on `path` from THIS
    process, creating the file if needed. Returns the fd, which the caller must
    close. Used rather than racing a second subprocess: a race that reproduces
    "usually" is a test that fails "sometimes"."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def can_acquire(path: Path) -> bool:
    """Could an INDEPENDENT open of `path` take LOCK_EX|LOCK_NB right now?
    Never leaves anything held. `flock` is scoped per OPEN FILE DESCRIPTION, so
    this contends for real even against a lease this same process holds through
    a different descriptor."""
    if not path.exists():
        return True
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def refusal_reasons(proc, seg):
    """The per-id reason list select_segments.py's own claim fatal emits for
    `seg`, read from the machine-readable `claim_failures` field rather than
    grepped out of the prose."""
    payload = parse_stdout(proc)
    failures = payload.get("claim_failures")
    assert isinstance(failures, dict), (
        f"expected a machine-readable claim_failures object, got {payload!r}"
    )
    assert seg in failures, f"{seg!r} is not among the refused ids: {sorted(failures)}"
    return failures[seg]


def joined_reasons(proc, seg):
    return " | ".join(refusal_reasons(proc, seg))


def run_help(root, columns=10000):
    """`--help` with argparse's line wrapping DISABLED, so the text can be
    compared with an ordinary exact substring check.

    argparse's HelpFormatter wraps to `shutil.get_terminal_size()`, which
    honours `COLUMNS`, and it breaks on HYPHENS -- so at width 80 the shipped
    disclosure comes back carrying "re- stamped" and an exact match fails for a
    reason that has nothing to do with the text. Measured on this help string:
    absent at COLUMNS=80, present at COLUMNS=10000.

    Setting COLUMNS high is the repair that costs no strength. The alternatives
    -- normalising hyphens away, or stripping all whitespace before comparing --
    buy the same green by weakening the assertion, and "this constant appears
    VERBATIM in both surfaces" is the entire property under test: a check that
    ignores spacing would pass over a reflowed paraphrase, which is exactly the
    drift the one-constant design exists to prevent."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), "--help"],
        capture_output=True, text=True, timeout=60, cwd=str(root),
        env={**os.environ, "COLUMNS": str(columns)},
    )


# Widths a person might actually read this help at. The FLOOR is measured, not
# chosen for convenience: at 50 and below, textwrap force-breaks tokens longer
# than the remaining line (`break_long_words`) at arbitrary positions, which no
# hyphen-scoped tolerance can undo -- measured on this help text, the
# hyphen-tolerant comparison holds at 60+ and fails at 50 and 40. A terminal
# narrower than 60 columns is not a realistic environment for reading it, so the
# floor is stated rather than engineered around.
OPERATOR_HELP_WIDTHS = (60, 70, 80, 100, 120, 160)


def _hyphen_tolerant(text: str) -> str:
    """`text` with runs of whitespace collapsed AND any space that immediately
    follows a hyphen removed.

    That is the exact and only artifact argparse's wrapping introduces inside a
    word: textwrap breaks on hyphens by default, so "re-stamped" comes back as
    "re-\nstamped" and normalisation alone leaves "re- stamped". Applied to BOTH
    sides of a comparison, so a legitimate "-- " in the source is transformed
    identically and the two still line up.

    Deliberately NARROWER than stripping all whitespace: this cannot see a drift
    that only changes spacing next to a hyphen, and that is the one blind spot
    it buys. It is not a substitute for the verbatim check."""
    return re.sub(r"-\s+", "-", " ".join(text.split()))


def assert_refused(proc, condition):
    """A refusal assertion whose FAILURE message says what went wrong rather
    than dumping a successful run's JSON. A guard deleted from the profile
    makes the fixture ADMIT, and "returncode != 0" alone reports that as an
    unreadable blob at exactly the moment a reader needs to know which
    direction it failed in."""
    assert proc.returncode != 0, (
        f"this fixture violates ONE condition -- {condition} -- and must be REFUSED. "
        f"It was ADMITTED instead, which is what deleting that condition looks like."
        f"\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


# ---------------------------------------------------------------------------
# The fixture's own ground truth. Both of these exist because a fixture that
# is wrong in the SAME direction as the code under test proves nothing, and
# neither fact is visible from any assertion further down.
# ---------------------------------------------------------------------------

def test_the_fixture_hash_oracle_agrees_with_the_shipped_draft_sha1(tmp_path):
    """`draft_content_sha1_of()` above is a reimplementation, and every
    staleness fixture in this file is built on it. A reimplementation that
    drifted from the shipped hash would build fixtures whose review looks stale
    to the test and CURRENT to select_segments.py, or the reverse -- and every
    admission and refusal below would then be about the wrong thing while
    reading exactly as it does now.

    Cross-checked against the SHIPPED draft_sha1.py, over both fixture states
    (the hand-corrected draft and the pre-edit one the stored review names), so
    the agreement covers the pair the staleness comparison is actually made
    between rather than one arbitrary document."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    sha1_mod = _load_module(root / "scripts" / "draft_sha1.py", "draft_sha1_fixture_oracle")

    shipped = sha1_mod.draft_content_sha1(root / "segments" / f"{seg}.draft.json")
    assert shipped == facts[seg]["current_sha1"], (
        f"the fixture oracle and the shipped draft_sha1.py disagree about the "
        f"CURRENT draft: oracle {facts[seg]['current_sha1']}, shipped {shipped}"
    )

    pre_edit_path = root / "segments" / "pre_edit_probe.draft.json"
    pre_edit = json.loads(json.dumps(facts[seg]["draft"]))
    pre_edit["blocks"]["p1"] = clean_draft(seg)["blocks"]["p1"]
    pre_edit_path.write_text(json.dumps(pre_edit, ensure_ascii=False), encoding="utf-8")
    assert sha1_mod.draft_content_sha1(pre_edit_path) == facts[seg]["reviewed_sha1"], (
        "and they must agree about the PRE-EDIT draft too -- that is the value the "
        "stored review carries, and the one the staleness comparison is made against"
    )
    assert facts[seg]["reviewed_sha1"] != facts[seg]["current_sha1"], (
        "the two states must really differ, or 'stale' is not what these fixtures are"
    )


def test_both_live_shapes_admit_and_only_a_claim_never_a_translate(tmp_path):
    """THE HAPPY PATH, over BOTH real units at once: `seg21`'s CLEAN stale
    review and `FRONTBACK:errata_02`'s DIRTY one, in a single invocation.

    Carrying both is the point rather than tidiness. `clean` is the one field
    `--from-stalled` deliberately does not constrain, and the measured reason is
    that the two live units disagree on it while being the same stalled state by
    every other condition -- so a suite that exercised only one of them would
    stay green through a build that constrained `clean` either way and sent the
    other unit back to the hand procedure this profile exists to retire.

    The colon-bearing id is not incidental either: `FRONTBACK:errata_02` is
    spliced into a lock FILENAME by codex_job_lock_path(), so an id shape that
    survives validate_seg() but breaks a path would surface here."""
    root, facts = stalled_project(tmp_path, segs=(LIVE_CLEAN_SEG, LIVE_DIRTY_SEG))
    assert facts[LIVE_CLEAN_SEG]["review"]["clean"] is True
    assert facts[LIVE_DIRTY_SEG]["review"]["clean"] is False, (
        "the two fixtures must genuinely disagree on `clean`, or this test is one "
        "fixture written twice"
    )

    proc = claim_stalled(root, LIVE_CLEAN_SEG, LIVE_DIRTY_SEG)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out = parse_stdout(proc)

    for seg in (LIVE_CLEAN_SEG, LIVE_DIRTY_SEG):
        assert seg in out["segs"]
        assert seg in out["claims"], f"{seg!r} must be reported as claimed: {out['claims']!r}"
        claim = out["claims"][seg]
        assert claim["profile"] == "from-stalled", (
            f"the durable record must name THIS profile, not an inherited one: {claim!r}"
        )
        assert claim["run_id"] == RUN_ID
        assert claim["source_run_id"] == SOURCE_RUN_ID
        assert claim["previous_dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}"
        assert claim["pre_claim_content_sha1"] == facts[seg]["current_sha1"]

        marker = claim_marker(root, seg)
        assert marker.is_file(), "the durable claim record must actually be on disk"
        assert json.loads(marker.read_text(encoding="utf-8")) == claim, (
            "the reported authorization and the durable one must be the SAME object"
        )

        # The draft is re-stamped to THIS run -- the channel the driver reads --
        # and its CONTENT is untouched. A claim authorizes re-review and never
        # re-translation, so the hand-corrected bytes must survive verbatim.
        draft_now = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
        assert draft_now["dispatch_token"] == f"{RUN_ID}:{seg}"
        assert draft_now["blocks"] == facts[seg]["draft"]["blocks"], (
            "the claim must not touch a single byte of the draft's content"
        )

    # `--only-segs` was NOT passed, and the ids were still emitted: an
    # in_progress unit classifies as `recoverable`, which is default-eligible.
    # Pinned as an observed fact because --from-cap's population is
    # human_escalation and needs --only-segs, and reasoning by analogy from that
    # profile gives the wrong answer here.
    assert out["counts"]["recoverable"] == 2, (
        f"both stalled units must classify as recoverable, got {out['counts']!r}"
    )
    assert out["counts"]["human_escalation"] == 0


# ---------------------------------------------------------------------------
# D5.2 -- the clearing, written against the REAL fatal.
# ---------------------------------------------------------------------------

def test_an_admitted_from_stalled_id_does_not_leave_the_invocation_in_previously_converged(tmp_path):
    """D5.2, and the ONE comprehension that is the whole behavioural fix.

    `previously_converged` is built from sentinel state ALONE for every emitted
    seg, and EVERY --from-stalled unit carries a sentinel by definition -- so
    without the clearing, a fully successful admission fatals its own
    invocation on the unconditional previously-converged refusal, and D5.3
    (below) rejects the --allow-retranslate-converged escape outright, leaving
    the profile with no route at all.

    WRITTEN AGAINST THE REAL FATAL, not against the comprehension. The
    assertion that matters is `returncode == 0`: a build that cleared the list
    for reporting while still fataling would satisfy `seg not in
    previously_converged` and remain completely unusable. The emitted list is
    asserted too, because the driver reads it.

    THE CONTROL is the same fixture with the claim NOT requested: the identical
    selection must then FAIL on precisely that refusal, naming this id. Without
    it, `returncode == 0` is equally consistent with a fixture whose sentinel
    was never seen -- in which case the clearing was never exercised."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    assert (root / "segments" / f".ever_converged.{seg}").is_file()

    # CONTROL FIRST -- no claim. The sentinel really does reach the refusal.
    plain = run_select(root)
    assert plain.returncode != 0, (
        f"a sentinel-bearing id must refuse WITHOUT a claim, or the clearing below "
        f"is never exercised\nstdout={plain.stdout}\nstderr={plain.stderr}"
    )
    control = parse_stdout(plain)
    assert seg in control.get("previously_converged", []), (
        f"the control's refusal must be the previously-converged one, naming this id: "
        f"{control!r}"
    )
    assert "previously CONVERGED" in control["error"]

    proc = claim_stalled(root, seg)
    assert proc.returncode == 0, (
        f"an admitted --from-stalled id must not fatal its own invocation on "
        f"previously_converged -- this is the whole D5.2 clearing, and a build that "
        f"only cleared the REPORTED list would still exit non-zero here.\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = parse_stdout(proc)
    assert seg not in out["previously_converged"], (
        f"and the emitted list -- which the driver reads -- must not carry it either: "
        f"{out['previously_converged']!r}"
    )
    assert seg in out["claims"]


# A test named test_the_clearing_covers_only_the_claimed_id_never_a_sentinel_
# bearing_sibling stood here. It built a root of two stalled units, claimed ONE
# under --from-stalled, and asserted that the unclaimed sentinel-bearing sibling
# survived the D5.2 clearing and still fataled the invocation. It was deleted
# when the --only-segs subset requirement shipped, and the reason is recorded
# here rather than in a commit message because the gap it leaves is one a future
# reader WILL notice and try to fill.
#
# ITS SCENARIO IS NOT MERELY UNTESTED NOW -- IT IS STRUCTURALLY UNREACHABLE.
# With `segs ⊆ claim_requests` enforced whenever a --from-stalled id is
# requested, every emitted seg is a claimed id; and every sentinel-bearing
# claimed id is cleared by D5.2 -- ALL THREE profiles clear since #537, so a
# --from-cap id that does carry a sentinel is cleared here too rather than
# being the one uncleared case this paragraph used to rely on.
# Therefore `previously_converged` after the clearing is ALWAYS empty in any
# invocation carrying a --from-stalled id. An unclaimed sentinel-bearing sibling
# cannot coexist with one. Rebuilding this test would mean building a fixture the
# guard rejects, and it would fail on the guard's refusal rather than on
# anything about the clearing.
#
# THE PROPERTY IT COVERED IS STILL COVERED, and by exactly one owner, which is
# the point: tests/claim_selector.test.py's own
# test_clearance_covers_only_the_claimed_id_not_a_sibling (:783) pins it for
# --from-converged, whose population IS reachable in that shape. Re-scoping this
# one to --from-converged would have produced a second copy of that test -- a
# duplicate guard with two owners and no clear one, which rots. The
# --from-stalled shape is now owned by
# test_a_from_stalled_invocation_that_emits_an_unclaimed_id_is_refused below.


# ---------------------------------------------------------------------------
# D5.3 -- the --allow-retranslate-converged overlap.
# ---------------------------------------------------------------------------

def test_from_stalled_with_allow_retranslate_converged_is_rejected_before_any_write(tmp_path):
    """D5.3. `--from-stalled`'s population carries a sentinel, so its ids reach
    `previously_converged` and the collision with `--allow-retranslate-converged`
    is reachable -- exactly as for `--from-converged`, and (since #537) for
    `--from-cap` as well, whose population CAN carry a sentinel; all three
    profiles are inside D5.3's guard now.

    REJECTED OUTRIGHT rather than resolved by precedence: one flag authorizes
    RE-TRANSLATION and the other authorizes RE-REVIEW only, and "the claim wins"
    would be one flag silently changing the other's meaning.

    NOTHING WRITTEN is asserted by ENUMERATING the durable side effects rather
    than by checking the exit code: this refusal fires before any admission
    work, so no claim record, no re-stamped draft token, and -- since the check
    sits above the lease acquisition -- no lock file either. The draft's bytes
    AND its token are compared against a snapshot taken before the run, because
    a re-stamp changes only the token and would be invisible to a content
    check."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    draft_path = root / "segments" / f"{seg}.draft.json"
    before = draft_path.read_bytes()

    proc = claim_stalled(root, seg, extra=("--allow-retranslate-converged",))
    assert proc.returncode != 0, (
        f"the overlap must be rejected outright\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = parse_stdout(proc)
    assert "sentinel-bearing claim profile" in out["error"], (
        f"the refusal must be the D5.3 overlap one, not some other gate: {out['error']!r}"
    )
    assert f"{seg} (--from-stalled)" in out["error"], (
        f"and it must name the id together with the flag it was claimed under: "
        f"{out['error']!r}"
    )

    assert not claim_marker(root, seg).exists(), "no claim record may be written"
    assert draft_path.read_bytes() == before, (
        "and the draft must be byte-identical -- token included, since a re-stamp "
        "changes nothing else"
    )
    assert not driver_lock(root).exists(), (
        "the rejection fires above the lease acquisition, so not even a lock FILE "
        "may appear"
    )
    assert not job_lock(root, seg).exists()


# ---------------------------------------------------------------------------
# Collision -- an id named under two profiles, over all three pairs.
# ---------------------------------------------------------------------------

def test_an_id_named_under_two_claim_profiles_is_fatal_and_names_both_flags(tmp_path):
    """All THREE pairs, not just the pre-#455 one.

    With three profiles there are three possible collisions, and the pre-#455
    test exercised only cap+converged. Deleting the collision handling for
    either NEW pair leaves that test green, which is why this one enumerates
    the pairs and counts them.

    The refusal must name the TWO FLAGS IN CONFLICT, not merely say "named under
    more than one": with three flags on a command line, an unattributed refusal
    sends an operator to re-read all of them.

    The fixture is irrelevant to this gate -- parse_claim_requests() runs on
    argument strings before any I/O -- so one root serves every pair, and the
    exit code alone would be satisfied by any argument error, which is why each
    case asserts the two specific flag names."""
    root, _ = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    pairs = [
        ("--from-cap", "--from-converged"),
        ("--from-cap", "--from-stalled"),
        ("--from-converged", "--from-stalled"),
    ]
    checked = 0
    for first, second in pairs:
        proc = run_select(
            root, first, seg, second, seg, "--run-id", RUN_ID, "--run-resume", "false",
        )
        assert proc.returncode != 0, (
            f"{first} + {second} over the same id must be fatal\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        error = parse_stdout(proc)["error"]
        assert "more than one claim profile" in error, (
            f"the refusal must be the collision one, not some other argument error: "
            f"{error!r}"
        )
        assert first in error and second in error, (
            f"the refusal must name BOTH conflicting flags -- {first} and {second} -- "
            f"or the operator cannot tell which two of three to reconcile: {error!r}"
        )
        assert seg in error
        assert not claim_marker(root, seg).exists()
        checked += 1
    assert checked == 3, (
        f"all three pairs must have been exercised, only {checked} were -- a loop that "
        f"runs fewer times than it claims prints exactly what a complete one prints"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 4 -- no behaviour change off the --from-stalled path.
# ---------------------------------------------------------------------------

def test_an_ordinary_invocation_acquires_no_lock_even_while_both_locks_are_held(tmp_path):
    """ACCEPTANCE CRITERION 4, proved by CONTENTION rather than by absence.

    "It creates no lock file" is the weak half and is asserted too, but a build
    that acquired the driver lease for every invocation would still pass that
    check whenever the file already existed. So this test HOLDS both locks --
    `runs/.driver.lock` and this segment's own `.codex_job.<seg>.lock` -- from
    the test process for the WHOLE run, with the real `fcntl.flock`, and
    requires an ordinary selection to succeed unchanged anyway. A build that
    took either lease unconditionally cannot: it would be refused by this
    process.

    THE CONTROL is the same held locks plus a --from-stalled request, which MUST
    refuse. Without it, "the ordinary run succeeded" is equally consistent with
    a test whose locks were never really held."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    held = [hold(driver_lock(root)), hold(job_lock(root, seg))]
    try:
        # Ordinary run: no claim at all. --allow-retranslate-converged is
        # passed because this fixture's sentinel would otherwise refuse for a
        # reason that has nothing to do with locks.
        ordinary = run_select(root, "--allow-retranslate-converged")
        assert ordinary.returncode == 0, (
            f"an invocation requesting no --from-stalled id must be unchanged, "
            f"including taking NO lease -- it is refused here only if it tried\n"
            f"stdout={ordinary.stdout}\nstderr={ordinary.stderr}"
        )
        assert seg in parse_stdout(ordinary)["segs"]

        # CONTROL: the same held locks, with a --from-stalled id. This one must
        # refuse, or the locks above were not really held.
        blocked = claim_stalled(root, seg)
        assert blocked.returncode != 0, (
            f"with both locks held, a --from-stalled request MUST refuse -- if it "
            f"does not, the locks this test holds are not the ones the code takes, "
            f"and the assertion above proves nothing\n"
            f"stdout={blocked.stdout}\nstderr={blocked.stderr}"
        )
    finally:
        for fd in held:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def test_an_ordinary_claim_profile_creates_no_lock_file_at_all(tmp_path):
    """The other half of criterion 4: a --from-cap or --from-converged claim is
    also off the --from-stalled path and must remain lock-free.

    Asserted on the FILES, in a root where neither lock path exists beforehand
    -- `_open_lock_file()` opens with O_CREAT, so any lease attempt leaves a
    file behind even when it succeeds. That makes absence a real signal here,
    where it would not be in the contended test above.

    A --from-converged fixture is built rather than reusing the stalled one:
    reaching the lock code needs an invocation that gets PAST argument parsing
    with a claim, and a claim that is refused on its own conditions would leave
    the same empty directory a lock-free build does."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    seg = "seg22"

    segpack = clean_segpack(seg)
    write_segpack(root, seg, segpack)
    converged = clean_draft(seg)
    converged_sha1 = draft_content_sha1_of(converged)
    edited = json.loads(json.dumps(converged))
    edited["blocks"]["p1"] += " Hand-edited by the operator."
    edited["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, edited)
    make_run_dir(root, SOURCE_RUN_ID)
    ck = make_cache_key(seg)
    fixture_keys[seg] = ck
    write_review(root, seg, {"clean": True, "coverage_ok": True, "findings": [],
                             "draft_sha1": "0" * 40})
    mark_ever_converged(root, seg)
    write_fragment(root, seg, {
        "timestamp": "2026-01-01T00:00:00Z", "status": "converged", "rounds": 1,
        "cache_key": ck, "n_blocks": 3, "n_footnotes": 1, "n_verses": 2,
        "reviewed_draft_sha1": converged_sha1,
    })
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    assert not driver_lock(root).exists() and not job_lock(root, seg).exists()
    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, (
        f"the --from-converged control must genuinely succeed, or it never reached "
        f"the claim block\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert seg in parse_stdout(proc)["claims"]
    assert not driver_lock(root).exists(), (
        "a --from-converged claim must create no driver lock file -- _open_lock_file() "
        "uses O_CREAT, so the file's absence is what proves no lease was attempted"
    )
    assert not job_lock(root, seg).exists()


def test_driver_lease_held_without_any_from_stalled_id_is_refused(tmp_path):
    """`--driver-lease-held` is an ASSERTION ABOUT THE KERNEL, and nothing else
    in the script reads it. Accepted-and-ignored, it would teach an operator (and
    a future caller) that passing it is harmless -- which is how a pointer
    becomes a grant. Refused outright instead, before any I/O."""
    root, _ = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    proc = run_select(
        root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false",
        "--driver-lease-held",
    )
    assert proc.returncode != 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    error = parse_stdout(proc)["error"]
    assert "--driver-lease-held was passed without any --from-stalled id" in error, (
        f"the refusal must be this one specifically: {error!r}"
    )
    assert not driver_lock(root).exists(), "and it must fire before anything touches a lock"


# ---------------------------------------------------------------------------
# The closed condition list -- one fixture axis per test.
#
# EVERY test below starts from an ADMITTING control and flips exactly ONE
# keyword of build_from_stalled_segment(). That is not tidiness: a fixture
# violating two conditions is answered by whichever reason is appended first,
# and then stays green through the deletion of the condition it is named for.
# Each assertion therefore pins the profile's OWN sentence for that condition,
# read out of the machine-readable `claim_failures` field rather than grepped
# from the prose blob.
# ---------------------------------------------------------------------------

def test_a_status_other_than_in_progress_is_refused_by_name(tmp_path):
    """Condition 1. `in_progress` is the state the other two profiles exclude,
    and it is what an abandoned convergence write leaves behind.

    `pending` is the flipped value rather than `converged` or `non_converged`,
    and the choice is load-bearing: both of those classify OUT of the
    default-eligible set (human_escalation / a converged classification), so the
    id would never be emitted and the D3 "not in this invocation's own emitted
    segs" fatal would answer this test instead of the condition it names.
    `pending` classifies as `recoverable`, exactly as `in_progress` does, so the
    ONLY thing that changed is the status the profile reads."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    seg = LIVE_CLEAN_SEG
    build_from_stalled_segment(root, seg, fixture_keys, ledger_status="pending")
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = claim_stalled(root, seg)
    assert_refused(proc, "its materialized ledger status is 'pending', not 'in_progress'")
    reasons = joined_reasons(proc, seg)
    assert "its materialized ledger status is 'pending', not 'in_progress'" in reasons, (
        f"the refusal must name the status condition and both values: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()


def test_an_absent_ever_converged_sentinel_is_refused_by_name(tmp_path):
    """Condition 2. The sentinel is what narrows this profile to units that HAVE
    converged. An `in_progress` unit with no sentinel is ordinary first-pass
    work, and re-reviewing it is not what this authorizes.

    Note what this fixture ALSO removes: with no sentinel the id never reaches
    `previously_converged`, so the invocation's only refusal is the claim one --
    which is what makes the assertion attributable."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    seg = LIVE_CLEAN_SEG
    build_from_stalled_segment(root, seg, fixture_keys, sentinel_present=False)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = claim_stalled(root, seg)
    assert_refused(proc, "it carries no .ever_converged sentinel")
    reasons = joined_reasons(proc, seg)
    assert "carries no .ever_converged sentinel (absent)" in reasons, (
        f"the refusal must name the sentinel condition and the state it read: {reasons!r}"
    )
    assert "first-pass work" in reasons, (
        f"and must say WHY that population is excluded, or an operator reads it as "
        f"a bookkeeping quibble: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()


def test_a_ledger_record_that_already_carries_reviewed_draft_sha1_is_refused_by_name(tmp_path):
    """Condition 3 -- the EXACT field whose absence makes `--from-converged`
    refuse ("the drift baseline this profile requires"). Its absence is the
    defining property here, not an incidental one: a unit that HAS a baseline
    has a drift comparison available, so its remedy is `--from-converged`.

    The value planted is a REAL sha1 of a REAL prior state of this draft, not a
    placeholder constant: a well-formed value is what makes the refusal
    attributable to the field being PRESENT rather than to it being malformed."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    seg = LIVE_CLEAN_SEG
    facts = build_from_stalled_segment(root, seg, fixture_keys)
    write_fragment(root, seg, in_progress_fragment(
        reviewed_draft_sha1=facts["reviewed_sha1"],
    ))
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    assert len(facts["reviewed_sha1"]) == 40, (
        "the planted baseline must be a well-formed sha1, or a FORMAT problem could "
        "be what refuses instead of the field's presence"
    )

    proc = claim_stalled(root, seg)
    assert_refused(proc, "its ledger record already carries 'reviewed_draft_sha1'")
    reasons = joined_reasons(proc, seg)
    assert "its ledger record already carries 'reviewed_draft_sha1'" in reasons, (
        f"the refusal must name the field that must be absent: {reasons!r}"
    )
    assert facts["reviewed_sha1"] in reasons, (
        f"and must show the value it found, so an operator can tell whose baseline "
        f"it is: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()


def test_a_unit_with_no_usable_stored_review_is_refused_by_the_profiles_own_condition(tmp_path):
    """Condition 4. The population is DEFINED by having been reviewed at least
    once, so "no review" is not merely a missing gate input -- the unit is not in
    this population at all, and the refusal has to say that rather than leave S4's
    file-not-found standing alone.

    This condition deliberately has NO arm in the shared S4/S5 chain above it:
    that block is unreachable when the review could not be read, so stating the
    condition there would make the one case that matters silently unreported.
    S4 also fires here, on the same fixture change -- but S4's sentence is about
    a file and this one is about a POPULATION, and no other --from-stalled
    condition can produce it, which is what keeps the assertion attributable."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    (root / "segments" / f"{seg}.review.json").unlink()

    proc = claim_stalled(root, seg)
    assert_refused(proc, "no usable stored review exists for it")
    reasons = joined_reasons(proc, seg)
    assert "no usable stored review was read for it" in reasons, (
        f"the profile's own condition must be reported, not only S4's read failure: "
        f"{reasons!r}"
    )
    assert "has been reviewed at least once" in reasons
    assert any("S4 (stored review)" in r for r in refusal_reasons(proc, seg)), (
        "and S4's own detail must still be there -- the profile condition says WHICH "
        "condition failed, S4 says what was wrong with the file"
    )
    assert not claim_marker(root, seg).exists()


def test_a_schema_invalid_stored_review_is_refused_as_not_in_the_population(tmp_path):
    """Condition 4's other half: "absent" and "present but unusable" must both
    land on the profile's own condition. A review that PARSES but is not
    schema-valid is the case a `.is_file()` check would wave through, and the
    stored verdict is what this profile compares against the current draft --
    so a document no reviewer in this pipeline could have produced authorizes
    nothing.

    `draft_sha1` is the dropped field on purpose: it is the review's only
    binding to the bytes it judged, and therefore the exact input the staleness
    comparison would otherwise read as missing rather than invalid."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    broken = dict(facts[seg]["review"])
    broken.pop("draft_sha1")
    write_review(root, seg, broken)

    proc = claim_stalled(root, seg)
    assert_refused(proc, "its stored review is not schema-valid")
    reasons = joined_reasons(proc, seg)
    assert "no usable stored review was read for it" in reasons, (
        f"an unusable review must reach the same profile condition an absent one "
        f"does: {reasons!r}"
    )
    assert "not schema-valid" in reasons and "draft_sha1" in reasons, (
        f"and S4 must name the field that is missing, or an operator cannot act on "
        f"it: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()


def test_a_draft_whose_content_sha1_cannot_be_computed_is_refused_rather_than_read_as_stale(tmp_path):
    """Condition 5, and the reason it is its own branch rather than folded into
    the staleness comparison: `None == "<sha1>"` is False, so an UNHASHABLE
    draft would have looked STALE and ADMITTED -- the unsafe direction, reached
    by the code path that knows the least about the file.

    The draft is REMOVED to reach it. That also fires S1, S2 and S3, and this
    test asserts none of those: no other --from-stalled condition can produce
    the sentence below, so the attribution rests on the sentence rather than on
    the fixture being minimal. Delete this branch and the invocation still
    refuses -- on S1/S2/S3 -- while THIS assertion goes red, which is the
    discrimination the test exists to make."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    (root / "segments" / f"{seg}.draft.json").unlink()

    proc = claim_stalled(root, seg)
    assert_refused(proc, "its draft's content sha1 cannot be computed")
    reasons = joined_reasons(proc, seg)
    assert "current draft's content sha1 could not be computed" in reasons, (
        f"the refusal must state that the staleness comparison could not be MADE: "
        f"{reasons!r}"
    )
    assert "refusing rather than treat an unhashable draft as one whose review no longer applies" in reasons, (
        f"and must name the direction it refuses in, since the alternative is a "
        f"silent admission: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()


def test_a_review_that_is_current_against_the_draft_is_refused_without_an_open_loop(tmp_path):
    """Condition 6 -- the ENTRY condition. The stored verdict must describe a
    draft that no longer exists; a review that describes the bytes on disk NOW
    is not the stalled population, and its remedy is a convergence write rather
    than a re-review.

    The fixture is one keyword away from the admitting control -- the review's
    `draft_sha1` is set to the CURRENT draft's hash and nothing else moves -- so
    the refusal cannot be about anything else. The CONTROL runs first, in this
    same test, because "it refused" is otherwise equally consistent with a
    fixture that was never admissible."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    seg = LIVE_CLEAN_SEG

    # CONTROL: the identical fixture with a STALE review admits.
    facts = build_from_stalled_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    control = claim_stalled(root, seg)
    assert control.returncode == 0, (
        f"the stale control must admit, or the refusal below is not about staleness\n"
        f"stdout={control.stdout}\nstderr={control.stderr}"
    )

    # THE FLIP, on a fresh root so the control's own claim record and re-stamp
    # cannot become the open loop this test is asserting the absence of.
    root2 = make_durable_root(tmp_path, name="durable_root_current")
    keys2: dict = {}
    flipped = build_from_stalled_segment(root2, seg, keys2, review_stale=False)
    write_manifest(root2, [seg])
    write_fixture_cache_keys(root2, keys2)
    # The flip really happened: the stored review now names the bytes on disk.
    # Without this, `review_stale=False` silently doing nothing would leave the
    # refusal below to fire for the ORDINARY entry reason and the test would
    # read as green while never reaching the branch it is named for.
    assert flipped["reviewed_sha1"] == flipped["current_sha1"], (
        "the flipped fixture's review must be CURRENT, or this test is the stale "
        "entry case wearing a different name"
    )
    assert flipped["review"]["draft_sha1"] == flipped["current_sha1"]
    assert facts["reviewed_sha1"] != facts["current_sha1"], (
        "and the control's must be STALE, or the two halves are the same fixture"
    )

    proc = claim_stalled(root2, seg)
    assert_refused(proc, "its stored review is CURRENT against the draft, with no open loop")
    reasons = joined_reasons(proc, seg)
    assert "still matches the current draft" in reasons, (
        f"the refusal must name the entry condition: {reasons!r}"
    )
    assert "the continuation of a re-review loop this profile already opened" in reasons, (
        f"and must name the ONE way a current review is admitted, or the operator "
        f"reads a dead end where there is a door: {reasons!r}"
    )
    assert "holds no readable claim record" in reasons, (
        f"and must say what was actually missing: {reasons!r}"
    )
    assert not claim_marker(root2, seg).exists()


def test_every_population_condition_has_its_own_refusal_test(tmp_path):
    """A COUNT over this file's own condition tests, so a condition that gains
    a branch in select_segments.py without gaining a test here is visible rather
    than merely absent.

    Counting the TEST FUNCTIONS is the weaker half and is not what this does; it
    counts the profile's own refusal SENTENCES reachable in
    evaluate_claim_admission's --from-stalled block, read out of the shipped
    source, and requires each to be asserted somewhere in this file. A new
    condition therefore turns this red at the moment it ships without a test."""
    source = SELECT_SCRIPT_SRC.read_text(encoding="utf-8")
    start = source.index("elif profile == CLAIM_PROFILE_FROM_STALLED:")
    end = source.index("# ---- D6: fresh-segpack precondition", start)
    block = source[start:end]
    # Every refusal sentence in that block is written as a run of implicitly
    # concatenated f-strings across several source lines, so a plain substring
    # search finds only fragments that happen not to straddle a wrap -- and
    # rewrapping the prose would then silently unmap a condition while every
    # assertion here stayed green. The adjacent literals are joined first, so
    # what is searched is the SENTENCE rather than the source line.
    joined = re.sub(r'"\s*\n\s*f?"', "", block)

    # The distinctive fragment of each refusal sentence in that block, paired
    # with the test that owns it. Written out rather than derived, because a
    # regex over the block would drift with the prose while still matching.
    owned = {
        "not 'in_progress'": "test_a_status_other_than_in_progress_is_refused_by_name",
        "carries no .ever_converged sentinel": "test_an_absent_ever_converged_sentinel_is_refused_by_name",
        "already carries 'reviewed_draft_sha1'": "test_a_ledger_record_that_already_carries_reviewed_draft_sha1_is_refused_by_name",
        "no usable stored review was read": "test_a_unit_with_no_usable_stored_review_is_refused_by_the_profiles_own_condition",
        "content sha1 could not be computed": "test_a_draft_whose_content_sha1_cannot_be_computed_is_refused_rather_than_read_as_stale",
        "still matches the current draft": "test_a_review_that_is_current_against_the_draft_is_refused_without_an_open_loop",
    }
    this_file = Path(__file__).read_text(encoding="utf-8")
    checked = 0
    for fragment, owner in owned.items():
        assert fragment in joined, (
            f"{fragment!r} is no longer a refusal in the --from-stalled block -- the "
            f"condition list moved and this file's map of it did not"
        )
        assert f"def {owner}(" in this_file, f"{owner} is missing from this file"
        checked += 1
    assert checked == 6, f"expected 6 mapped conditions, checked {checked}"

    # And the block holds no refusal this map does not know about. Counted from
    # the `reasons.append(` sites, which is what a new condition adds.
    appended = block.count("reasons.append(")
    assert appended == 6, (
        f"the --from-stalled block appends {appended} refusal(s) but this file maps "
        f"6 -- a condition shipped without a red-before-green test of its own"
    )


# ---------------------------------------------------------------------------
# The two kernel leases.
#
# Every test below takes the REAL lock with the REAL fcntl.flock from THIS
# process rather than racing a second subprocess: a race that reproduces
# "usually" is a test that fails "sometimes". `flock` is scoped per OPEN FILE
# DESCRIPTION, so a lock this process holds contends for real against the
# selector's own independent open -- and against this file's own `can_acquire`
# probes.
# ---------------------------------------------------------------------------

def test_a_successful_standalone_admission_takes_both_leases_and_drops_them_on_exit(tmp_path):
    """Direction 1 of four: the standalone selector ACQUIRES, admits, and the
    kernel releases on exit.

    "It took the lease" is proved by the CONTENTION the run creates, not by the
    lock files appearing -- a build that only ever created the files would pass
    a file check. So the run happens while nothing is held, and afterwards both
    paths are acquirable again, which is the release the process never performs
    explicitly (the fds are parked in a module-level list and dropped by the
    kernel at exit -- deliberately, so an "unused local" can never become a
    reason to close one early).

    The CONTENTION half is owned by the two tests below; this one owns the
    other end of the same fact, which no other test here asserts: that the
    lease does not OUTLIVE the process and strand the next invocation.

    The second invocation claims a DIFFERENT id in the same root rather than
    re-claiming the first under a new run: re-claiming would be answered by
    #438's superseded-authority guard in rewrite_draft_dispatch_token(), which
    is a different subject entirely and would make this test green or red for
    reasons that have nothing to do with a lease."""
    root, _facts = stalled_project(tmp_path, segs=(LIVE_CLEAN_SEG, LIVE_DIRTY_SEG))
    seg, second_seg = LIVE_CLEAN_SEG, LIVE_DIRTY_SEG
    assert not driver_lock(root).exists() and not job_lock(root, seg).exists()

    # --only-segs narrows the emitted set to the claimed id, so the OTHER
    # stalled unit's own sentinel does not fatal this invocation on the
    # previously-converged refusal -- a refusal that has nothing to do with
    # leases and is owned by its own test above.
    proc = claim_stalled(root, seg, extra=("--only-segs", seg))
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert driver_lock(root).is_file(), (
        "the driver lease path must exist after a --from-stalled admission -- "
        "_open_lock_file() creates it with O_CREAT, so its absence would mean no "
        "lease was ever attempted"
    )
    assert job_lock(root, seg).is_file()
    assert can_acquire(driver_lock(root)), (
        "the driver lease must be RELEASED once the process exits -- it is held by "
        "descriptor and never unlinked, so a lease that outlived its holder would "
        "strand every later invocation with no way to tell it from a live one"
    )
    assert can_acquire(job_lock(root, seg))

    # A SECOND invocation, needing the same project-wide driver lease: it must
    # be able to take it again. That is the release above, observed from the
    # only side that matters.
    # The first claim re-stamped seg21's draft to RUN_ID, which now counts as
    # dispatch evidence -- the UNRELATED #409 resume-integrity gate refuses any
    # later invocation until that run id has an input.digest. Written here (as
    # resume_setup.py would) so that gate cannot answer this test.
    make_run_dir(root, RUN_ID)
    make_run_dir(root, OTHER_RUN_ID)
    again = claim_stalled(root, second_seg, run_id=OTHER_RUN_ID,
                          extra=("--only-segs", second_seg))
    assert again.returncode == 0, (
        f"a second invocation must be able to take the project-wide lease again\n"
        f"stdout={again.stdout}\nstderr={again.stderr}"
    )
    assert second_seg in parse_stdout(again)["claims"]


def test_a_second_process_holding_the_driver_lease_refuses_every_from_stalled_id(tmp_path):
    """Direction 2: the lease is HELD by someone else, so admission refuses --
    and refuses ALL of them, because that lease is project-wide.

    The refusal must also carry the disclosure. This is the refusal an operator
    is most likely to over-read: "it refused because something is running"
    invites "so it must SEE everything that runs", and the profile proves
    nothing of the kind.

    THE CONTROL is the identical invocation with the lock released -- it must
    admit, or the refusal is consistent with a fixture that could never have
    been claimed."""
    root, _facts = stalled_project(tmp_path, segs=(LIVE_CLEAN_SEG, LIVE_DIRTY_SEG))
    fd = hold(driver_lock(root))
    try:
        proc = claim_stalled(root, LIVE_CLEAN_SEG, LIVE_DIRTY_SEG)
        assert proc.returncode != 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        failures = parse_stdout(proc)["claim_failures"]
        assert sorted(failures) == sorted([LIVE_CLEAN_SEG, LIVE_DIRTY_SEG]), (
            f"the driver lease is PROJECT-WIDE, so every requested id must be "
            f"refused, not just one: {sorted(failures)}"
        )
        for seg in (LIVE_CLEAN_SEG, LIVE_DIRTY_SEG):
            reasons = joined_reasons(proc, seg)
            assert "could not take the project-wide driver lease" in reasons, (
                f"the refusal must name the lease: {reasons!r}"
            )
            assert str(driver_lock(root)) in reasons, (
                f"and the path, or an operator cannot go look at it: {reasons!r}"
            )
            assert "is HELD by another process" in reasons
            assert "released automatically when its holder exits or crashes" in reasons, (
                f"and must say the lock cannot be stale, or the first move an operator "
                f"makes is to delete it: {reasons!r}"
            )
            assert "Everything else is YOUR ASSERTION" in reasons, (
                f"and it must carry the disclosure -- this is exactly the refusal that "
                f"reads as liveness DETECTION: {reasons!r}"
            )
            assert not claim_marker(root, seg).exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    control = claim_stalled(root, LIVE_CLEAN_SEG, LIVE_DIRTY_SEG)
    assert control.returncode == 0, (
        f"the identical invocation must admit once the lease is free, or the refusal "
        f"above was not about the lease\nstdout={control.stdout}\nstderr={control.stderr}"
    )


def test_driver_lease_held_is_re_confirmed_against_the_kernel_in_both_directions(tmp_path):
    """Directions 3 and 3': `--driver-lease-held` is a POINTER, never a grant.

    The driver spawns this script with no `pass_fds`, so `close_fds=True` strips
    its lease descriptor and a fresh acquire here would be refused by our own
    parent on every dispatch -- which is why the flag exists at all. What the
    script CAN do is re-confirm the assertion against the kernel: an independent
    LOCK_EX|LOCK_NB must FAIL.

    BOTH directions are here, in one test, because either alone is satisfiable
    by a build that does the wrong thing:

      * the lease genuinely FREE -> REFUSE. A build that trusted the flag admits
        here, with nothing holding anything.
      * the lease genuinely HELD -> ADMIT. A build that always refused would pass
        the first half and be entirely unusable from the driver, which is the
        only caller that ever passes this flag.

    What kernel contention proves is that SOME independent open file description
    holds the lease -- never that it is our parent's. The refusal text has to say
    that rather than claim more, and that wording is pinned below."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG

    # Direction 3: nobody holds it.
    assert can_acquire(driver_lock(root)), "precondition: the lease must be free"
    free = claim_stalled(root, seg, extra=("--driver-lease-held",))
    assert free.returncode != 0, (
        f"--driver-lease-held over a FREE lease must refuse -- the flag asserts a "
        f"fact about the kernel and the kernel disagrees\n"
        f"stdout={free.stdout}\nstderr={free.stderr}"
    )
    reasons = joined_reasons(free, seg)
    assert "that assertion could not be confirmed against the kernel" in reasons, (
        f"the refusal must be the re-confirmation one: {reasons!r}"
    )
    assert "SUCCEEDED" in reasons, (
        f"and must report what the probe actually did: {reasons!r}"
    )
    assert "nobody holds the lease (so the flag is false)" in reasons
    assert "does not enforce flock at all" in reasons, (
        f"and must name the OTHER reading of the same observation, since the two "
        f"call for different actions: {reasons!r}"
    )
    assert not claim_marker(root, seg).exists()

    # Direction 3': somebody really does hold it. This is the driver's own
    # situation, and it must ADMIT.
    fd = hold(driver_lock(root))
    try:
        held = claim_stalled(root, seg, extra=("--driver-lease-held",))
        assert held.returncode == 0, (
            f"--driver-lease-held over a genuinely HELD lease must ADMIT -- this is "
            f"the driver's own path, and a build that refused here would be unusable "
            f"from the only caller that passes the flag\n"
            f"stdout={held.stdout}\nstderr={held.stderr}"
        )
        assert seg in parse_stdout(held)["claims"]
        assert claim_marker(root, seg).is_file()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# A stand-in for validate_draft.py that SIGNALS and then WAITS, giving this file
# a synchronisation point INSIDE a live selector run -- after both leases have
# been taken (they are acquired before any admission gate reads an artifact) and
# before anything durable has been written. It exits 0, so S1 passes; S2
# (draft_ready.py) is still the real script. The wait is bounded so a failing
# build cannot hang the suite, and `_run_leaf_gate`'s own 120s subprocess
# timeout is the outer bound.
SYNC_VALIDATE_DRAFT_PY = '''#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seg")
    p.add_argument("--durable-root", default=None)
    p.add_argument("--candidate-file", default=None)
    args = p.parse_args()
    root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    (root / "test_fixture_gate_reached.txt").write_text(args.seg + "\\n", encoding="utf-8")
    release = root / "test_fixture_gate_release.txt"
    deadline = time.monotonic() + 30.0
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# A drop-in claim_record.py that re-exports the REAL module and wraps ONE
# function. select_segments.py calls `claim_record.fsync_directory()` in exactly
# one place -- inside rewrite_draft_dispatch_token(), AFTER os.replace() has
# installed the re-stamped draft, which is itself after the durable claim record
# was written. So this is a synchronisation point strictly LATER than the claim
# write, which is what the "still held" assertion needs.
#
# The real module's own internal calls (write_claim_record() fsyncs its own
# directory) are NOT affected: those functions carry the real module's globals,
# so they reach the real fsync_directory. That is why this wrapper fires exactly
# once per admitted id and not three times.
SYNC_CLAIM_RECORD_PY = '''#!/usr/bin/env python3
import importlib.util
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

_spec = importlib.util.spec_from_file_location("claim_record_real", str(_HERE / "claim_record_real.py"))
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

for _name, _value in vars(_real).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_real_fsync = _real.fsync_directory


def fsync_directory(directory):
    problem = _real_fsync(directory)
    reached = _ROOT / "test_fixture_post_write_reached.txt"
    if not reached.exists():
        reached.write_text(str(directory) + "\\n", encoding="utf-8")
        release = _ROOT / "test_fixture_post_write_release.txt"
        deadline = time.monotonic() + 30.0
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    return problem
'''


def stage_pre_write_sync_point(root):
    (root / "scripts" / "validate_draft.py").write_text(SYNC_VALIDATE_DRAFT_PY, encoding="utf-8")


def stage_post_write_sync_point(root):
    scripts = root / "scripts"
    shutil.copy2(CLAIM_RECORD_SRC, scripts / "claim_record_real.py")
    (scripts / "claim_record.py").write_text(SYNC_CLAIM_RECORD_PY, encoding="utf-8")


def run_select_async(root, *extra_args):
    return subprocess.Popen(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(root),
    )


def wait_for_file(path: Path, timeout=30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


def test_both_leases_are_still_held_midway_through_the_admission_decision(tmp_path):
    """Direction 4: the leases are HELD ACROSS THE DECISION, not probed once and
    dropped.

    A probe that acquires and releases answers "was anything running a moment
    ago", which is not the question. What has to hold is "nothing else is
    writing this draft WHILE the claim record and the token re-stamp happen" --
    so this test stops the selector INSIDE the admission gates (a validate_draft
    stand-in that signals and waits) and, from this process, attempts an
    independent LOCK_EX|LOCK_NB on both paths. Both must be REFUSED.

    THE POSITION IS ASSERTED, not assumed: at the sync point no claim record
    exists yet, which is what makes this "midway through the decision" rather
    than "after it". The run is then released and must succeed, so the
    contention observed was a live selector's and not a corpse's.

    THE CONTROL is the same two probes taken after the process has exited: both
    must then succeed. Without it, "refused" is equally consistent with this
    test's own bookkeeping holding the locks."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    stage_pre_write_sync_point(root)
    reached = root / "test_fixture_gate_reached.txt"
    release = root / "test_fixture_gate_release.txt"

    proc = run_select_async(
        root, "--from-stalled", seg, "--run-id", RUN_ID, "--run-resume", "false",
    )
    try:
        assert wait_for_file(reached), (
            "the selector never reached the admission gates -- the sync point was "
            "never hit, so nothing below was measured"
        )
        assert not claim_marker(root, seg).exists(), (
            "the sync point must sit BEFORE the claim write, or this test is about "
            "a decision that has already finished"
        )
        assert can_acquire(driver_lock(root)) is False, (
            "the driver lease must still be held midway through the decision -- a "
            "probe-and-release implementation would have dropped it by now"
        )
        assert can_acquire(job_lock(root, seg)) is False, (
            "and so must this segment's codex-job lease, which is the leg that "
            "actually protects the draft"
        )
    finally:
        release.write_text("go\n", encoding="utf-8")
        stdout, stderr = proc.communicate(timeout=90)

    assert proc.returncode == 0, (
        f"and the run must then complete normally, or the contention above was a "
        f"stuck process rather than a live decision\nstdout={stdout}\nstderr={stderr}"
    )
    assert claim_marker(root, seg).is_file()
    assert can_acquire(driver_lock(root)) is True, (
        "CONTROL: both leases must be acquirable once the process has exited, or "
        "the refusals above were this test's own descriptors"
    )
    assert can_acquire(job_lock(root, seg)) is True


def test_the_codex_job_lease_is_still_held_after_the_claim_record_is_written(tmp_path):
    """The per-segment lease across the SECOND write, which is the one that
    matters most.

    codex_job.py flocks exactly `segments/.codex_job.<seg>.lock` immediately
    before launch() and releases it after finalize(), and its canonical
    promotion -- os.replace(attempt, canonical) -- sits INSIDE that window.
    Since #483 it re-checks whether the canonical's dispatch_token moved since
    the job's own first observation, refusing if it did, but it still consults
    no claim record and is still not atomic -- which is exactly why this lease
    is still load-bearing here. So a
    lease dropped after the claim record lands but before the token re-stamp
    would let an already-launched job overwrite the freshly claimed draft and
    put its old token back: a silent draft overwrite, not a lost turn.

    The sync point is claim_record.fsync_directory(), which select_segments.py
    calls in exactly one place -- inside rewrite_draft_dispatch_token(), after
    the re-stamped draft has been installed. That the claim record ALREADY
    EXISTS at this point is asserted rather than assumed, and it is what makes
    this test about the window after the first write."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    stage_post_write_sync_point(root)
    reached = root / "test_fixture_post_write_reached.txt"
    release = root / "test_fixture_post_write_release.txt"

    proc = run_select_async(
        root, "--from-stalled", seg, "--run-id", RUN_ID, "--run-resume", "false",
    )
    try:
        assert wait_for_file(reached), (
            "the post-write sync point was never reached -- nothing below was measured"
        )
        assert claim_marker(root, seg).is_file(), (
            "the sync point must sit AFTER the durable claim record was written, or "
            "this test is measuring the same window as the one above"
        )
        assert can_acquire(job_lock(root, seg)) is False, (
            "the codex-job lease must STILL be held after the claim record landed -- "
            "dropping it here is exactly the window in which an already-launched "
            "codex job promotes its own attempt over the claimed draft"
        )
        assert can_acquire(driver_lock(root)) is False, (
            "and the driver lease with it"
        )
    finally:
        release.write_text("go\n", encoding="utf-8")
        stdout, stderr = proc.communicate(timeout=90)

    assert proc.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    draft_now = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft_now["dispatch_token"] == f"{RUN_ID}:{seg}", (
        "and the re-stamp really did happen inside that window"
    )
    assert can_acquire(job_lock(root, seg)) is True, "CONTROL: released on exit"


def test_a_held_codex_job_lock_refuses_that_id_and_leaves_the_others_evaluated(tmp_path):
    """The per-segment lease is PER SEGMENT: a held job lock refuses ITS id and
    no other, unlike the project-wide driver lease above.

    The refused id is refused WITHOUT running its gates. That is not an
    optimisation: with a codex job holding the segment, every artifact those
    gates read is one the job may be part-way through replacing, so appending
    "and also its review is stale" would be reporting on state we have just
    established we do not own. Asserted by the refusal carrying exactly ONE
    reason, and that reason being the lock.

    THE CONTROL is the same command with the lock released -- both ids must
    then admit."""
    root, _facts = stalled_project(tmp_path, segs=(LIVE_CLEAN_SEG, LIVE_DIRTY_SEG))
    locked, other = LIVE_DIRTY_SEG, LIVE_CLEAN_SEG
    fd = hold(job_lock(root, locked))
    try:
        proc = claim_stalled(root, LIVE_CLEAN_SEG, LIVE_DIRTY_SEG)
        assert proc.returncode != 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        failures = parse_stdout(proc)["claim_failures"]
        assert list(failures) == [locked], (
            f"only the id whose job lock is held may be refused -- this lease is "
            f"per-segment, not project-wide: {sorted(failures)}"
        )
        reasons = refusal_reasons(proc, locked)
        assert len(reasons) == 1, (
            f"a lock-refused id must carry exactly ONE reason -- its gates are "
            f"deliberately not run, because the artifacts they would read belong to "
            f"the holder: {reasons!r}"
        )
        assert "could not take that segment's own codex-job lease" in reasons[0], (
            f"and that reason must name the job lease: {reasons[0]!r}"
        )
        assert str(job_lock(root, locked)) in reasons[0], (
            f"and the exact path, segment included: {reasons[0]!r}"
        )
        assert "os.replace(attempt, canonical)" in reasons[0], (
            f"and what is at stake inside that lock's critical section: {reasons[0]!r}"
        )
        assert "Everything else is YOUR ASSERTION" in reasons[0]
        assert not claim_marker(root, locked).exists()
        assert not claim_marker(root, other).exists(), (
            "and the whole invocation refuses, so the unlocked id is not claimed "
            "either -- D2 reports every failure together rather than half-performing"
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    control = claim_stalled(root, LIVE_CLEAN_SEG, LIVE_DIRTY_SEG)
    assert control.returncode == 0, (
        f"CONTROL: both ids must admit once the job lock is free\n"
        f"stdout={control.stdout}\nstderr={control.stderr}"
    )
    assert sorted(parse_stdout(control)["claims"]) == sorted([LIVE_CLEAN_SEG, LIVE_DIRTY_SEG])


# Simulates a filesystem that does not enforce flock, by INJECTING a stub into
# `sys.modules` before select_segments.py is executed and then running the real
# script in that process via runpy under `__main__`.
#
# WHY NOT THE OBVIOUS THING. The first version staged a `fcntl.py` in the
# durable root's own scripts/ directory and relied on it shadowing the stdlib
# module via sys.path[0]. That works only because `fcntl` happens to be a SHARED
# EXTENSION on the interpreter it was written against -- which is a property of
# how CPython was BUILT, not of fcntl. On a build where `fcntl` is statically
# compiled in, `sys.builtin_module_names` contains it and BuiltinImporter
# resolves it BEFORE sys.path is ever consulted: the stub is silently never
# imported, flock is real, the acquire succeeds, and the claim is ADMITTED --
# so the test fails, or worse, a differently-shaped version of it passes while
# testing nothing. Measured on this very machine, where `errno` IS a builtin and
# `fcntl` is not: a sys.path[0] `errno.py` does NOT shadow, a sys.path[0]
# `fcntl.py` DOES.
#
# `sys.modules` injection is immune to that, and the immunity is structural
# rather than lucky: the import system checks `sys.modules` FIRST, before
# BuiltinImporter and before any path finder, so a name already bound there is
# returned whatever the interpreter build did with it. Verified by the same
# experiment run the other way -- injecting a stub `errno` (a genuine builtin
# here) into sys.modules DOES override it.
#
# runpy rather than execv, for the reason tests/review_rejection.test.py's own
# O_EXCL runner states: execv would replace the interpreter and take the patched
# sys.modules with it. Staying in-process is what keeps the injection alive, and
# `run_name="__main__"` is what still makes the script's own
# `if __name__ == "__main__"` block run -- so the CLI, its argv, its
# self-anchoring off __file__ and its exit code are all the real ones.
#
# EVERY CALL IS LOGGED, and that log is the test's evidence that the stub was
# really used. A stub that silently is not used produces a refusal for some
# OTHER reason, and an assertion that only checks "the run refused" cannot tell
# the two apart -- which is exactly how the sys.path[0] version came to look
# correct on one interpreter and be vacuous on another.
UNENFORCED_FLOCK_RUNNER_PY = '''#!/usr/bin/env python3
import runpy
import sys
import types

log_path, script = sys.argv[1:3]

stub = types.ModuleType("fcntl")
stub.LOCK_SH = 1
stub.LOCK_EX = 2
stub.LOCK_NB = 4
stub.LOCK_UN = 8


def _flock(fd, operation):
    # A no-op that NEVER refuses -- precisely how a mount without flock behaves
    # (NFS/SMB): every acquire "succeeds", including two that should contend.
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("%d\\n" % (operation,))
    return None


stub.flock = _flock
sys.modules["fcntl"] = stub

sys.argv = [script] + sys.argv[3:]
runpy.run_path(script, run_name="__main__")
'''

# fcntl.LOCK_EX | fcntl.LOCK_NB, the operation select_segments.py's own lease
# code passes. Spelled from the REAL module rather than hard-coded, so the
# assertion cannot drift from what the script actually asks for.
_LOCK_EX_NB = fcntl.LOCK_EX | fcntl.LOCK_NB


def claim_stalled_unenforced(root, *segs, run_id=RUN_ID, extra=()):
    """Run the REAL select_segments.py with flock stubbed out. Returns
    `(CompletedProcess, [operation, ...])` -- the second element is every
    `flock()` operation the stub actually received, which is what proves the
    injection took effect rather than being assumed."""
    runner = root / "scripts" / "unenforced_flock_runner.py"
    runner.write_text(UNENFORCED_FLOCK_RUNNER_PY, encoding="utf-8")
    log = root / "test_fixture_flock_calls.txt"
    if log.exists():
        log.unlink()
    argv = [
        sys.executable, str(runner), str(log), str(root / "scripts" / "select_segments.py"),
        "--from-stalled", ",".join(segs), "--run-id", run_id, "--run-resume", "false", *extra,
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=str(root))
    calls = [int(line) for line in log.read_text(encoding="utf-8").split()] if log.exists() else []
    return proc, calls


def assert_stub_really_ran(calls, label):
    """The guard the sys.path[0] version was missing. A stub that is not used
    leaves NO calls, and the run then refuses (or admits) for a reason unrelated
    to flock enforcement -- indistinguishable from the outside. Asserting the
    exact operation the lease code passes is what makes this positive evidence
    that select_segments.py's own lock path went through the injected module."""
    assert calls, (
        f"[{label}] the flock stub recorded NO calls -- it was never used, so this "
        f"run says nothing about unenforced flock. That is the exact failure the "
        f"sys.modules injection replaced sys.path[0] shadowing to prevent"
    )
    assert _LOCK_EX_NB in calls, (
        f"[{label}] the stub was called, but never with LOCK_EX|LOCK_NB "
        f"({_LOCK_EX_NB}) -- select_segments.py's own lease code did not go "
        f"through it. got {calls!r}"
    )


def test_unenforced_flock_refuses_every_from_stalled_id_on_both_paths(tmp_path):
    """WHERE THE ASYMMETRY WITH THE DRIVER IS PAID FOR.

    segment_dispatch_driver.py ships the same enforcement self-test and WARNS on
    failure; this script REFUSES. The direction each fails in is the whole
    argument: on an unenforced mount the driver's own acquire is merely not
    exclusive, whereas this script's standalone path would FALSELY ACQUIRE
    runs/.driver.lock while a real driver holds it and then admit a live unit --
    and an operator would read that admission as "the plugin checked".

    BOTH PATHS, because they reach the refusal through different code and one
    does not imply the other: the standalone path fails its post-acquire
    self-test, and the --driver-lease-held path fails its kernel
    re-confirmation.

    THE CONTROLS ARE WHAT MAKE THIS ATTRIBUTABLE. Each half runs first with the
    REAL fcntl and must ADMIT in exactly the same fixture state -- for the
    driver half, that means this test holds the real lease, so the only thing
    that changes between the admitting run and the refusing one is whether the
    filesystem enforces the lock.

    AND EACH REFUSING HALF ASSERTS THE STUB WAS REALLY USED, via the operations
    it recorded (see assert_stub_really_ran). Without that, an injection that
    silently did not take effect produces a run that refuses -- or admits -- for
    an unrelated reason, and no assertion here could tell the difference. That
    is not hypothetical: the first version of this test simulated the unenforced
    mount by shadowing `fcntl` on sys.path, which is silently inert on an
    interpreter that compiles fcntl in as a builtin.

    EACH RUN GETS ITS OWN ROOT, deliberately. Re-running a claim over an
    already-claimed segment is answered by #438's superseded-authority guard in
    rewrite_draft_dispatch_token(), so a shared root would let THAT refusal
    stand in for this one -- a refusal is a refusal from the outside, and this
    test is named for a specific reason."""
    seg = LIVE_CLEAN_SEG

    def fresh(name):
        root, _ = stalled_project(tmp_path / name)
        return root

    # ---- standalone path -------------------------------------------------
    control_root = fresh("standalone_control")
    control = claim_stalled(control_root, seg)
    assert control.returncode == 0, (
        f"CONTROL: with a real fcntl the standalone path admits\n"
        f"stdout={control.stdout}\nstderr={control.stderr}"
    )

    standalone_root = fresh("standalone_unenforced")
    standalone, standalone_calls = claim_stalled_unenforced(standalone_root, seg)
    assert_stub_really_ran(standalone_calls, "standalone")
    assert standalone.returncode != 0, (
        f"on an unenforced mount the standalone path must REFUSE, not warn -- the "
        f"lease it just 'took' is worthless\nstdout={standalone.stdout}\n"
        f"stderr={standalone.stderr}"
    )
    reasons = joined_reasons(standalone, seg)
    assert "flock is NOT enforced for" in reasons, (
        f"the refusal must name unenforced flock specifically, not some other lease "
        f"failure: {reasons!r}"
    )
    assert str(driver_lock(standalone_root)) in reasons
    assert "REFUSES rather than warn-and-continue" in reasons, (
        f"and must state the deliberate divergence from the driver's own choice, or "
        f"the next reader 'fixes' it back: {reasons!r}"
    )
    assert "false ACQUIRE, not a false refusal" in reasons, (
        f"and name the direction that makes refusing the right call: {reasons!r}"
    )
    assert not claim_marker(standalone_root, seg).exists()

    # ---- driver-invoked path ---------------------------------------------
    # The real lease is HELD by this process for BOTH runs, so a conforming
    # build admits in this exact state (asserted, first). The only difference
    # between the two is whether flock is enforced.
    driver_control_root = fresh("driver_control")
    fd = hold(driver_lock(driver_control_root))
    try:
        driver_control = claim_stalled(driver_control_root, seg, extra=("--driver-lease-held",))
        assert driver_control.returncode == 0, (
            f"CONTROL: with a real fcntl and the lease genuinely held, the "
            f"--driver-lease-held path admits\nstdout={driver_control.stdout}\n"
            f"stderr={driver_control.stderr}"
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    driver_root = fresh("driver_unenforced")
    fd = hold(driver_lock(driver_root))
    try:
        driver_path, driver_calls = claim_stalled_unenforced(
            driver_root, seg, extra=("--driver-lease-held",)
        )
        assert_stub_really_ran(driver_calls, "driver-invoked")
        assert driver_path.returncode != 0, (
            f"on an unenforced mount the --driver-lease-held path must refuse too -- "
            f"its probe succeeds, and a succeeding probe cannot tell 'nobody holds "
            f"it' from 'this mount enforces nothing'\nstdout={driver_path.stdout}\n"
            f"stderr={driver_path.stderr}"
        )
        driver_reasons = joined_reasons(driver_path, seg)
        assert "that assertion could not be confirmed against the kernel" in driver_reasons, (
            f"the refusal must be the re-confirmation one: {driver_reasons!r}"
        )
        assert "does not enforce flock at all (so no lease here means anything)" in driver_reasons, (
            f"and must name unenforcement as one of the readings, since that is the "
            f"true one here: {driver_reasons!r}"
        )
        assert not claim_marker(driver_root, seg).exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# Continuation -- staleness gates ENTRY only, and re-entry is AUTHENTICATED.
#
# Once --from-stalled dispatches a fresh review and that review is promoted, the
# review is CURRENT. If the driver then dies before the convergence write -- a
# path it explicitly handles -- or the fresh verdict is rejected via
# reject_review.py without editing the draft, the unit is back to in_progress +
# sentinel + no reviewed_draft_sha1, with a CURRENT review. A STANDING staleness
# gate would refuse re-entry and strand the exact loop this profile exists to
# open.
#
# So a current review is admitted, but only as the continuation of a loop this
# machinery already opened: the DRAFT'S OWN OWNER must hold a COMPLETE claim
# record for this segment UNDER THIS PROFILE. "The review is current" is just as
# true of a segment nobody ever claimed, so it authorizes nothing by itself.
# ---------------------------------------------------------------------------

def open_a_from_stalled_loop(root, seg, current_sha1, *, run_id=RUN_ID, clean=True,
                             findings=None):
    """Drive the REAL first --from-stalled claim, then put the unit into the
    state a driver that died before its convergence write leaves behind: a
    CURRENT review over an UNCHANGED draft, still in_progress, still no
    reviewed_draft_sha1.

    The claim record is produced BY THE SHIPPED CODE, never hand-written. A
    hand-written one is written to the consumer's expectations by construction
    and would agree with evaluate_open_review_loop() through any drift in what
    build_claim_record() actually writes -- which is the whole thing the
    fourteen-field check is for."""
    first = claim_stalled(root, seg, run_id=run_id)
    assert first.returncode == 0, (
        f"the loop-opening claim must succeed\nstdout={first.stdout}\nstderr={first.stderr}"
    )
    record = json.loads(claim_marker(root, seg, run_id=run_id).read_text(encoding="utf-8"))
    assert record["profile"] == "from-stalled"

    # The fresh review the re-review round promoted: bound to the draft that is
    # on disk NOW, which the claim did not touch (only its token moved, and the
    # token is projected out of the content hash).
    write_review(root, seg, {
        "clean": bool(clean),
        "coverage_ok": True,
        "findings": findings if findings is not None else ([] if clean else [
            {"loc": "p1", "severity": "major", "issue": "unfounded", "suggest": "n/a"},
        ]),
        "draft_sha1": current_sha1,
        "dispatch_token": f"{run_id}:{seg}:r1",
    })
    # resume_setup.py's own artifact for the claiming run -- without it the
    # UNRELATED #409 resume-integrity gate refuses every later invocation,
    # because the re-stamped draft is now dispatch evidence for `run_id`.
    make_run_dir(root, run_id)
    return record


def reclaim(root, seg, *, run_id=RUN_ID, extra=()):
    """Re-enter the loop: the same command an operator re-runs, with
    --run-resume true (the run id already carries dispatch evidence of its own,
    which is what a resume IS)."""
    return run_select(
        root, "--from-stalled", seg, "--run-id", run_id, "--run-resume", "true", *extra
    )


def test_a_current_clean_review_continues_when_the_drafts_owner_holds_the_claim(tmp_path):
    """THE HALF THAT KEEPS THE PROFILE FROM STRANDING ITS OWN LOOP.

    Round 1 of the re-review came back CLEAN and the driver died before writing
    convergence. Every entry condition now reads the other way: the stored
    review describes exactly the bytes on disk. A standing staleness gate would
    refuse, and the operator would be back at the hand procedure this profile
    was built to retire.

    THE CONTROL runs first and matters: the same current-review state WITHOUT
    the claim record must refuse (owned by
    test_a_review_that_is_current_against_the_draft_is_refused_without_an_open_loop
    above), so what admits here is the RECORD and not the currency."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    record = open_a_from_stalled_loop(root, seg, facts[seg]["current_sha1"], clean=True)
    assert record["run_id"] == RUN_ID and record["profile"] == "from-stalled", (
        f"the loop must have been opened by the run that owns the draft, under THIS "
        f"profile -- that pairing is the entire authorization being tested: {record!r}"
    )

    review_now = json.loads((root / "segments" / f"{seg}.review.json").read_text(encoding="utf-8"))
    draft_now = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert review_now["draft_sha1"] == facts[seg]["current_sha1"], (
        "the review must genuinely be CURRENT, or this is the ordinary entry case"
    )
    assert draft_now["dispatch_token"] == f"{RUN_ID}:{seg}", (
        "and the draft's owner must be the run that holds the claim"
    )

    proc = reclaim(root, seg)
    assert proc.returncode == 0, (
        f"a CURRENT review must continue when the draft's owner holds a complete "
        f"from-stalled claim -- refusing here strands the loop this profile opens\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = parse_stdout(proc)
    assert seg in out["claims"]
    assert out["claims"][seg]["profile"] == "from-stalled"
    assert seg not in out["previously_converged"]


def test_a_current_dirty_review_rejected_via_reject_review_still_continues(tmp_path):
    """THE OTHER RE-ENTRY, and the one #461 made reachable: round 1 of the
    re-review came back DIRTY over a draft the operator has verified is
    correct, so the verdict is set aside with reject_review.py rather than
    "fixed". The draft is never edited, so the review stays CURRENT and the unit
    stays stalled.

    THE PRODUCER IS THE REAL ONE, driven exactly as an operator reaches it
    (--print-verdict-digest, then the rejection), because the rejection record's
    own gate is what makes this state legitimate rather than hand-made.

    SCOPE, STATED. What is asserted here is the SELECTOR's half: the claim
    admits, and the rejection artifact is on disk beside it. What the driver
    then does with that artifact -- advancing to a fresh review round rather
    than looping on needs_fix -- is derive_next_action()'s behaviour, owned by
    tests/review_rejection.test.py. Asserting it here would be re-proving
    another file's subject through a fixture that cannot fail for its
    reasons."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    open_a_from_stalled_loop(root, seg, facts[seg]["current_sha1"], clean=False)

    read = subprocess.run(
        [sys.executable, str(root / "scripts" / "reject_review.py"), seg, "--print-verdict-digest"],
        capture_output=True, text=True, timeout=30, cwd=str(root),
    )
    assert read.returncode == 0, f"stdout={read.stdout}\nstderr={read.stderr}"
    printed = json.loads(read.stdout.strip())
    assert printed["round_label_problem"] is None, printed

    rejected = subprocess.run(
        [
            sys.executable, str(root / "scripts" / "reject_review.py"), seg,
            "--reason", "verified: the claimed source string occurs zero times in block p1",
            "--round-label", printed["round_label"],
            "--expect-token", printed["dispatch_token"],
            "--expect-verdict-digest", printed["verdict_digest"],
        ],
        capture_output=True, text=True, timeout=30, cwd=str(root),
    )
    assert rejected.returncode == 0, (
        f"the real reject_review.py must accept this verdict\nstdout={rejected.stdout}\n"
        f"stderr={rejected.stderr}"
    )
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"
    assert rejection_path.is_file()

    proc = reclaim(root, seg)
    assert proc.returncode == 0, (
        f"a rejected-but-current dirty review must still continue the loop -- the "
        f"rejection is what makes the round advanceable, and refusing the claim "
        f"would leave the operator with a sanctioned rejection and no way to spend "
        f"it\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert seg in parse_stdout(proc)["claims"]
    assert rejection_path.is_file(), "and the claim must not disturb the rejection record"


def test_a_partial_or_wrong_profile_claim_record_does_not_continue_the_loop(tmp_path):
    """The record is judged by its CONTENTS, never by its presence at the right
    path.

    read_claim_record() establishes "a regular file holding a JSON object" and
    nothing more, so a three-key file in the right place would otherwise be a
    complete authorization to re-review a draft nobody re-read. Each case below
    starts from the record THE SHIPPED CODE WROTE and changes exactly one thing,
    with the untouched record asserted to authorize first -- so a refusal is
    attributable to that one change.

    THE PROFILE CASE is the one #455 added and the one a default parameter would
    have hidden: evaluate_open_review_loop() takes `expected_profile`
    keyword-only with NO default, because a from-cap claim authorizes a
    different population for different reasons, and a call site that inherited
    'from-converged' silently would make this function REFUSE rather than crash
    -- surfacing only as a continuation that mysteriously never continues."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    genuine = open_a_from_stalled_loop(root, seg, facts[seg]["current_sha1"], clean=True)
    marker = claim_marker(root, seg)

    # CONTROL: untouched, it continues. Everything below is one edit away.
    control = reclaim(root, seg)
    assert control.returncode == 0, (
        f"the genuine record must authorize, or every refusal below is vacuous\n"
        f"stdout={control.stdout}\nstderr={control.stderr}"
    )

    def _place(record):
        marker.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return reclaim(root, seg)

    # A record granted under ANOTHER profile. Its seg, its run_id and all
    # fourteen fields are right; only the profile differs.
    wrong_profile = _place({**genuine, "profile": "from-converged"})
    assert wrong_profile.returncode != 0, (
        f"a claim granted under --from-converged must not continue a --from-stalled "
        f"loop\nstdout={wrong_profile.stdout}"
    )
    reasons = joined_reasons(wrong_profile, seg)
    assert "was granted under profile 'from-converged', not 'from-stalled'" in reasons, (
        f"the refusal must name BOTH profiles -- which one is wrong decides the "
        f"operator's next move: {reasons!r}"
    )

    # A PARTIAL record. A half-finished write and a hand-made file look exactly
    # like this, and the fourteen fields are what make a record something only
    # this project's own claim path produces.
    partial = dict(genuine)
    partial.pop("cache_key_at_claim")
    partial.pop("pre_claim_review")
    partial_proc = _place(partial)
    assert partial_proc.returncode != 0, (
        f"a partial record must not continue the loop\nstdout={partial_proc.stdout}"
    )
    partial_reasons = joined_reasons(partial_proc, seg)
    assert "is missing 2 of the 14 fields" in partial_reasons, (
        f"the refusal must count what is missing against what this project writes: "
        f"{partial_reasons!r}"
    )
    assert "cache_key_at_claim" in partial_reasons and "pre_claim_review" in partial_reasons, (
        f"and name them: {partial_reasons!r}"
    )

    # A record that disagrees with its own LOCATION -- the three-key-file case,
    # generalised: the file is at run RUN_ID's path but records another run.
    displaced = _place({**genuine, "run_id": OTHER_RUN_ID})
    assert displaced.returncode != 0
    assert "disagrees with its own location" in joined_reasons(displaced, seg)

    # And restored: the genuine record continues again, so the three refusals
    # above were the three edits and not a fixture that went stale.
    assert _place(genuine).returncode == 0, (
        "the restored record must authorize again"
    )


def test_the_continuation_asks_the_drafts_owner_not_the_invoking_run(tmp_path):
    """OWNER SELECTION -- the #438 lesson, and the one substitution that is
    INVISIBLE in a same-identity fixture.

    `evaluate_open_review_loop()` is passed `source_run_id` (the draft's own
    owner), never `args.run_id`. The two coincide whenever a run re-enters its
    own loop -- the easy case, and exactly why substituting `args.run_id` looks
    harmless: the substitution only diverges when the draft belongs to a
    DIFFERENT run, and then it answers "have I ever claimed this?" instead of
    "does the draft's owner hold an open loop?". Those are different facts and
    only one of them authorizes anything.

    SO THE FIXTURE IS DELIBERATELY NOT SAME-IDENTITY. Run A (this invocation)
    holds a genuine, complete, from-stalled claim record for this segment,
    written by the shipped code. The draft has since moved to owner B, who holds
    no record at all. Invoking as A must refuse, and the refusal must be
    *because B holds nothing* -- naming B.

    MUTATION that must turn this red: pass `args.run_id` in place of
    `source_run_id` at that call site. A's record is right there and complete,
    so the mutant ADMITS. Every other continuation test in this file stays green
    through it.

    A's record is asserted to be complete and from-stalled BEFORE the refusal is
    read, so "B holds nothing" cannot be confused with "nobody holds anything"
    -- which would make the refusal true for the wrong reason."""
    root, facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG
    invoking_run, draft_owner = RUN_ID, OTHER_RUN_ID
    genuine = open_a_from_stalled_loop(root, seg, facts[seg]["current_sha1"],
                                       run_id=invoking_run, clean=True)

    # The draft moves to owner B. Only its token changes, so the content hash --
    # and therefore the review's currency -- is untouched.
    draft_path = root / "segments" / f"{seg}.draft.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["dispatch_token"] = f"{draft_owner}:{seg}"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    make_run_dir(root, draft_owner)

    assert claim_marker(root, seg, run_id=invoking_run).is_file(), (
        "A's record must be present, or 'B holds none' is not what refuses"
    )
    assert genuine["profile"] == "from-stalled" and genuine["seg"] == seg
    assert not claim_marker(root, seg, run_id=draft_owner).exists(), (
        "and B must hold none -- that is the whole fixture"
    )

    proc = reclaim(root, seg, run_id=invoking_run)
    assert proc.returncode != 0, (
        f"the continuation must ask the DRAFT'S OWNER, and B holds no claim -- a "
        f"build that asked the invoking run instead would find A's complete record "
        f"and admit\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    reasons = joined_reasons(proc, seg)
    assert f"the draft's owner {draft_owner!r} holds no readable claim record" in reasons, (
        f"the refusal must name B specifically -- that is what distinguishes 'the "
        f"wrong run was asked' from 'nobody has claimed this': {reasons!r}"
    )
    assert "still matches the current draft" in reasons, (
        f"and it must still be the continuation branch that refused: {reasons!r}"
    )
    assert not claim_marker(root, seg, run_id=draft_owner).exists()


def test_the_disclosure_survives_argparse_wrapping_at_real_terminal_widths(tmp_path):
    """READABLE -- a SECOND property, not a second copy of the verbatim check.
    Deleting either one loses something the other never had.

      * test_the_disclosure_says_what_is_asserted_rather_than_proved runs --help
        with wrapping DISABLED (COLUMNS=10000) and compares EXACTLY. That proves
        the constant is present character-for-character, which is what catches a
        reflowed paraphrase -- someone re-typing the disclosure into the help
        text instead of interpolating FROM_STALLED_DISCLOSURE. It proves nothing
        about any width a person actually uses.
      * THIS one runs --help at real widths and asks a weaker question of a
        realer artifact: can an operator at 60-160 columns still find the
        disclosure intact? That is the failure mode we were actually fixing when
        an added paragraph shifted every wrap position and pushed a break into
        the middle of the constant.

    WHY THE TOLERANCE EXISTS. argparse wraps to the terminal and breaks on
    HYPHENS, so at these widths the text legitimately contains "re- stamped".
    That is a rendering artifact of the reader's window, not drift, and a test
    that called it drift would fail in someone's terminal for a cosmetic reason.
    `_hyphen_tolerant()` removes exactly that artifact and nothing else.

    WHAT IT THEREFORE CANNOT CATCH: a change that alters only the whitespace
    adjacent to a hyphen. The verbatim test above is what covers that, which is
    the whole reason both exist.

    The helper's own behaviour is pinned first, on synthetic input, so this test
    does not depend on WHERE argparse happens to break today -- a control that
    stays meaningful when the help text changes length again."""
    # The tolerance does what it claims, and no more.
    assert _hyphen_tolerant("re-\nstamped") == "re-stamped", (
        "the tolerance must undo a hyphen break, or it is not doing the job this "
        "test rests on"
    )
    assert _hyphen_tolerant("foo\nbar") == "foo bar", (
        "and it must NOT join across an ordinary space break -- a tolerance that "
        "wide would be the whitespace-stripping comparison this deliberately is not"
    )

    root, _facts = stalled_project(tmp_path)
    select_mod = _load_module(root / "scripts" / "select_segments.py",
                              "select_segments_width_probe")
    assert select_mod._HELD_LOCK_FDS == [], (
        "this probe must acquire no lease either -- see the disclosure test for why"
    )
    disclosure = _hyphen_tolerant(select_mod.FROM_STALLED_DISCLOSURE)

    checked = []
    for columns in OPERATOR_HELP_WIDTHS:
        helped = run_help(root, columns=columns)
        assert helped.returncode == 0, (
            f"--help must succeed at COLUMNS={columns}: {helped.stderr!r}"
        )
        assert disclosure in _hyphen_tolerant(helped.stdout), (
            f"the disclosure must survive argparse's wrapping at COLUMNS={columns} -- "
            f"an operator reading --help in a {columns}-column terminal has to be "
            f"able to find it intact, and a text edit anywhere ABOVE it can push a "
            f"break into the middle of it without changing a character of the "
            f"constant itself"
        )
        checked.append(columns)
    assert checked == list(OPERATOR_HELP_WIDTHS), (
        f"every width must have been exercised, got {checked!r} -- a loop that runs "
        f"fewer times than it claims prints exactly what a complete one prints"
    )


def test_the_help_text_pins_why_d3b_exists_and_not_only_that_it_requires_only_segs(tmp_path):
    """The --only-segs requirement is now TRUE for both --from-cap and
    --from-stalled, and it holds for DIFFERENT REASONS. That distinction is
    load-bearing, which is why it is pinned rather than left to prose drift.

    --from-cap's population is `human_escalation`, which is outside
    DEFAULT_ELIGIBLE_CATEGORIES, so a capped id never reaches the dispatch set
    at all unless --only-segs names it -- the requirement is a CONSEQUENCE of
    the classification, enforced by nothing. --from-stalled's population is
    `in_progress`, which classifies as `recoverable` and IS dispatch-eligible,
    so nothing forces it and D3b has to.

    A reader who believes both profiles are gated by the same mechanism will
    eventually delete D3b as redundant with D3 -- the help text is where that
    reader looks first, and this test is what stops the explanation being
    trimmed back to the bare requirement.

    IT ALSO PINS THE OLD SENTENCE AS GONE. The help used to assert that
    --from-stalled IS human_escalation, which was measurably false. `--from-cap`
    is legitimately described that way in the same paragraph, so the negative
    assertion targets the exact discredited phrasing rather than the word."""
    root, _facts = stalled_project(tmp_path)
    helped = run_help(root)
    assert helped.returncode == 0
    help_text = " ".join(helped.stdout.split())
    start = help_text.index("#455: claim these ids")
    block = help_text[start:help_text.index("--driver-lease-held", start)]

    assert "enforced by D3b, this profile's OWN check, for a different reason than --from-cap's" in block, (
        f"the help must say the requirement is enforced HERE and why that differs "
        f"from --from-cap: {block!r}"
    )
    assert "A capped id is human_escalation and so never reaches the dispatch set unless --only-segs names it" in block, (
        f"and state --from-cap's mechanism: {block!r}"
    )
    assert "a stalled id is in_progress, which classifies as `recoverable` and IS dispatch-eligible by default" in block, (
        f"and this profile's, which is the fact that makes D3b necessary rather "
        f"than redundant: {block!r}"
    )
    assert "being human_escalation, --only-segs" not in block, (
        f"the discredited claim that --from-stalled IS human_escalation must be "
        f"gone -- it was measurably false and it is what made the requirement look "
        f"like a consequence of something already enforced: {block!r}"
    )


# ---------------------------------------------------------------------------
# The disclosure -- the one thing that can be tested about an ASSERTION.
# ---------------------------------------------------------------------------

def test_the_disclosure_says_what_is_asserted_rather_than_proved(tmp_path):
    """THE ASSERTION HAS NO BEHAVIOUR, so what is pinned is the WORDING.

    `--from-stalled` proves two facts and asserts a third. Nothing runnable can
    check the asserted one -- that is what makes it an assertion -- so the only
    thing standing between the profile and a false guarantee is what it SAYS,
    and prose regresses silently. This test pins the four parts that make it an
    honest disclosure rather than a caveat:

      1. the two PROVED facts, named individually, so "it refused because
         something is running" cannot grow into "it sees everything that runs";
      2. the ASSERTED part, attributed to the operator by the act of naming the
         id;
      3. that the plugin CANNOT check it -- the sentence a reader looks for
         before deciding how much the refusal is worth;
      4. the COST, specifically. "Work may be lost" understates it: a concurrent
         fix turn writes the canonical draft DIRECTLY and copies whatever
         dispatch_token it read, so the two orderings have genuinely different
         outcomes and an operator triaging afterwards needs to know which one
         they are looking at.

    Pinned in BOTH surfaces a user meets: --help (read before running) and the
    refusal (read after). They come from ONE constant precisely so they cannot
    drift into two differently-strong promises, and this test would notice if
    someone re-typed one of them."""
    root, _facts = stalled_project(tmp_path)
    seg = LIVE_CLEAN_SEG

    helped = run_help(root)
    assert helped.returncode == 0
    # Newlines collapsed only so a multi-line help block is one searchable
    # string; with wrapping disabled by run_help() no word was ever split, so
    # this changes no character inside the text being matched.
    help_text = " ".join(helped.stdout.split())

    fd = hold(driver_lock(root))
    try:
        refused = claim_stalled(root, seg)
        assert refused.returncode != 0
        refusal = " ".join(joined_reasons(refused, seg).split())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    proved = [
        "no competing driver holds runs/.driver.lock",
        "no codex job holds this segment's own segments/.codex_job.<seg>.lock",
    ]
    for fact in proved:
        assert fact in refusal, (
            f"the refusal must name each PROVED fact individually: {fact!r} missing "
            f"from {refusal!r}"
        )

    asserted = "no Workflow fix turn and no OTHER select_segments.py claim invocation is touching it"
    assert asserted in refusal, (
        f"the refusal must name what is ASSERTED, not merely hedge: {refusal!r}"
    )
    assert "Everything else is YOUR ASSERTION, made by naming the id" in refusal, (
        f"and attribute it to the operator's own act: {refusal!r}"
    )
    assert "This plugin cannot check that" in refusal, (
        f"and say plainly that it is unchecked: {refusal!r}"
    )
    assert "writes the canonical draft directly and copies whatever dispatch_token it read" in refusal, (
        f"and state the COST specifically rather than as 'work may be lost': {refusal!r}"
    )
    assert "either loses its own work or leaves this claim's re-stamped draft carrying content that nobody has re-reviewed" in refusal, (
        f"including BOTH orderings, since they call for different triage: {refusal!r}"
    )

    # --help, the surface read BEFORE the command runs, must draw the same line.
    #
    # Exact substring, deliberately -- see run_help() for why that is safe here
    # and why the whitespace-insensitive alternatives were rejected.
    assert "WHAT THIS PROFILE PROVES, AND WHAT IT ONLY ASSERTS" in help_text, (
        f"--help must draw the same line the refusal does: {help_text[:400]!r}"
    )

    # AND THE TWO SURFACES MUST BE THE SAME SENTENCE, not two paraphrases that
    # happen to agree today. FROM_STALLED_DISCLOSURE is read out of the SHIPPED
    # module and required in both -- so a rewrite of either surface that does not
    # go through the constant turns this red.
    select_mod = _load_module(root / "scripts" / "select_segments.py",
                              "select_segments_disclosure_probe")
    # THE ONE IN-PROCESS LOAD OF select_segments.py IN THIS FILE, and it must
    # stay a pure read.
    #
    # `_HELD_LOCK_FDS` is append-only with no release hook -- correct for a CLI,
    # where the process exits and the kernel drops the leases, but a test that
    # acquired IN-PROCESS would hold them for the whole pytest run. A later
    # acquire against the same durable root would then be refused by nothing but
    # this file's own earlier call, and that refusal is INDISTINGUISHABLE from a
    # correctly-detected competing holder: the suite would go green for the
    # wrong reason, and a mutation that broke real detection could still pass.
    #
    # Every other test here drives select_segments.py as a SUBPROCESS, which is
    # immune by construction. This probe is the only in-process load, so the
    # invariant is asserted here rather than trusted: importing the module runs
    # its top level only, `main()` sits behind an `if __name__ == "__main__"`
    # guard, and no lock is taken.
    assert select_mod._HELD_LOCK_FDS == [], (
        f"loading select_segments.py in-process must acquire NO lease -- this "
        f"file's leases would otherwise outlive the test that took them and make "
        f"a later refusal unattributable: {select_mod._HELD_LOCK_FDS!r}"
    )
    disclosure = " ".join(select_mod.FROM_STALLED_DISCLOSURE.split())
    assert disclosure in refusal, (
        "the refusal must carry the disclosure CONSTANT verbatim, not a paraphrase"
    )
    assert disclosure in help_text, (
        "and --help must carry the same constant -- one string is what stops the "
        "flag's help, the refusals and SKILL.md drifting into three differently-"
        "strong promises"
    )


# ===========================================================================
# THE --only-segs SUBSET REQUIREMENT (#455 follow-up).
#
# STATUS: the first test below is EXPECTED RED until the enforcement ships in
# select_segments.py. It is deliberately not xfail-marked -- an xfail would hide
# exactly the signal this section exists to carry.
#
# WHAT WAS MEASURED. Four documents (the --from-stalled --help text, plan
# Design §2, SKILL.md and the changelog) state that the profile is
# `human_escalation` and therefore requires --only-segs naming the same ids.
# It is not, and it does not. `status: in_progress` classifies as `recoverable`
# (select_segments.py:1433), `recoverable` is in DEFAULT_ELIGIBLE_CATEGORIES
# (:1451), and D3 (:4276) enforces only the ONE direction
# `claim_requests ⊆ segs`. For --from-cap the subset direction forces
# --only-segs as a side effect of its population being human_escalation;
# --from-stalled's population is default-eligible, so nothing forces it.
#
# WHY THAT MATTERS MORE THAN THE PROSE. Without --only-segs, a --from-stalled
# invocation claims the named ids AND dispatches every other eligible segment in
# the root as ordinary work -- paid turns nobody asked for, on units the operator
# said nothing about. And the disclosed operator assertion is scoped to the ids
# NAMED ON THE FLAG ("no fix turn is touching THESE ids"), so an invocation that
# also dispatches others is acting outside the assertion it collected. Frozen
# acceptance criterion 1 says every other unit is refused.
#
# THE RULE THESE TESTS PIN: when any --from-stalled id is requested, every
# emitted seg must be a claimed id -- `segs ⊆ claim_requests`. With D3's
# existing `claim_requests ⊆ segs` that yields equality, while still permitting
# a mixed invocation that also carries --from-cap/--from-converged ids. NOT
# `segs == from_stalled_ids`, which would break the mixed case for no gain --
# which is why the mixed case is pinned here BEFORE the guard is written rather
# than after.
# ===========================================================================

def build_from_cap_segment(root, seg, fixture_keys: dict, *, source_run_id=SOURCE_RUN_ID):
    """P2 shape (--from-cap): non_converged/reason=cap, no sentinel here (the
    profile admits a PRESENT one too since #537 -- this builder simply does not
    need one), no cache_key on the fragment, stored review clean:false WITH
    findings.
    Verbatim in shape from tests/claim_selector.test.py's own builder, trimmed
    to the axes this file needs -- present only so the MIXED invocation below
    carries a genuinely different profile rather than a second --from-stalled id
    wearing another flag's name."""
    write_segpack(root, seg, clean_segpack(seg))
    draft = clean_draft(seg)
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-fixed after the cap."
    draft["dispatch_token"] = f"{source_run_id}:{seg}"
    write_draft_doc(root, seg, draft)
    make_run_dir(root, source_run_id)
    fixture_keys[seg] = make_cache_key(seg)
    write_review(root, seg, {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "medium", "issue": "awkward phrasing",
                      "suggest": "rephrase"}],
        "draft_sha1": "0" * 40,
    })
    write_fragment(root, seg, {
        "timestamp": "2026-01-01T00:00:00Z", "status": "non_converged",
        "reason": "cap", "rounds": 4,
    })


def build_bare_eligible_segment(root, seg, fixture_keys: dict):
    """A segment that is DEFAULT-ELIGIBLE and nothing else: a manifest entry
    with a segpack and no ledger fragment at all, so classify_segment() returns
    `not_started`. No sentinel, so it never reaches previously_converged -- which
    is what makes it a clean probe for "was an unclaimed id emitted", with no
    second refusal available to answer the test for the wrong reason."""
    write_segpack(root, seg, clean_segpack(seg))
    fixture_keys[seg] = make_cache_key(seg)


def test_a_from_stalled_invocation_that_emits_an_unclaimed_id_is_refused(tmp_path):
    """D3b: when a --from-stalled id is requested, every emitted seg must be a
    claimed id. With D3's existing `claim_requests ⊆ segs` that yields equality,
    while a mixed invocation carrying other profiles' ids stays legal.

    TWO SIBLING KINDS, EACH IN ITS OWN ROOT, and the pairing is the whole test:

      * a BARE not_started sibling -- no sentinel, so no other gate has anything
        to say about it. Measured before the guard shipped: the run exited 0 and
        emitted `['seg21', 'seg98']` while claiming only `seg21`. This is the
        defect.
      * a SENTINEL-BEARING stalled sibling -- this one was ALREADY refused
        before the guard, by the previously-converged fatal. That makes it the
        dangerous case, not the redundant one: a guard placed BELOW that fatal
        passes any test asking only "did the run refuse", while the operator
        following the message reaches for --allow-retranslate-converged, which
        on this population is the flag that re-translates hand-corrected
        drafts. So this case does not merely assert a refusal -- it asserts
        WHICH refusal, and that the destructive flag is not named. That pair of
        assertions is what pins D3b's placement above the fatal; nothing else
        in this suite can.

    MUTATION that must turn this red: delete the D3b check (`if
    stalled_requested:` -> `if False:`)."""
    checked = 0
    for label, build_sibling in (
        ("sentinel_bearing", lambda root, keys: build_from_stalled_segment(root, "seg99", keys)),
        ("bare_eligible", lambda root, keys: build_bare_eligible_segment(root, "seg98", keys)),
    ):
        root = make_durable_root(tmp_path / label)
        fixture_keys: dict = {}
        claimed = LIVE_CLEAN_SEG
        build_from_stalled_segment(root, claimed, fixture_keys)
        build_sibling(root, fixture_keys)
        sibling = next(s for s in fixture_keys if s != claimed)
        write_manifest(root, [claimed, sibling])
        write_fixture_cache_keys(root, fixture_keys)
        draft_before = (root / "segments" / f"{claimed}.draft.json").read_bytes()

        proc = claim_stalled(root, claimed)
        payload = parse_stdout(proc)
        emitted = payload.get("segs", [])
        assert proc.returncode != 0, (
            f"[{label}] a --from-stalled invocation must not emit an id it did not "
            f"claim. {sibling!r} was emitted as ordinary work alongside the claim, so "
            f"this invocation dispatches a segment the operator said nothing about -- "
            f"and the disclosed assertion it collected covers only {claimed!r}. "
            f"emitted={emitted!r}"
        )
        error = payload["error"]
        assert (
            f"--from-stalled was requested for {claimed}, but 1 emitted seg(s) are not "
            f"claimed by this invocation: {sibling}."
        ) in error, (
            f"[{label}] the refusal must name the claimed id, the COUNT and the "
            f"unclaimed id, or the operator cannot tell which segment to add or drop: "
            f"{error!r}"
        )
        assert "A --from-stalled run must dispatch ONLY what it claims -- pass --only-segs naming exactly the claimed ids." in error, (
            f"[{label}] and it must name the flag that fixes it: {error!r}"
        )
        assert "This profile's population is `in_progress`, which is dispatch-eligible by default" in error, (
            f"[{label}] and state WHY this profile needs the flag when the others do "
            f"not -- that sentence is the whole finding, and without it the next "
            f"reader 'simplifies' the guard back out: {error!r}"
        )
        assert "outside the assertion --from-stalled collects from you, which covers only the ids on the flag" in error, (
            f"[{label}] and tie it to the disclosure, which is what makes this a "
            f"safety requirement rather than a tidiness one: {error!r}"
        )

        # PLACEMENT, pinned empirically. Before the guard, this exact
        # sentinel-bearing root refused with "1 previously CONVERGED segment(s)
        # would be translated again ... Pass --allow-retranslate-converged".
        # Both assertions below fail if D3b is ever moved below that fatal, and
        # the second one is the one that matters: on a hand-corrected draft that
        # flag is the most destructive action available, so a refusal naming it
        # is worse than no refusal at all.
        assert "previously CONVERGED" not in error, (
            f"[{label}] D3b must answer BEFORE the previously-converged fatal: "
            f"{error!r}"
        )
        assert "--allow-retranslate-converged" not in error, (
            f"[{label}] and the refusal must never coach the operator toward the flag "
            f"that re-translates the hand-corrected drafts this profile exists to "
            f"protect: {error!r}"
        )

        # NOTHING LEFT BEHIND. The refusal is about the SHAPE of the invocation,
        # so it fires before any durable write -- enumerated rather than
        # asserted as one exit code, because each of these is a separate
        # side effect and a guard placed one block lower would leave some of
        # them. The draft is compared byte-for-byte, token included: a re-stamp
        # changes only the token and a content check would miss it.
        assert not claim_marker(root, claimed).exists(), (
            f"[{label}] no claim record may be written"
        )
        assert (root / "segments" / f"{claimed}.draft.json").read_bytes() == draft_before, (
            f"[{label}] and the draft must be byte-identical -- no dispatch_token re-stamp"
        )
        assert not driver_lock(root).exists(), (
            f"[{label}] and D3b sits above the lease acquisition, so not even a lock "
            f"FILE may appear"
        )
        assert not job_lock(root, claimed).exists()
        checked += 1
    assert checked == 2, (
        f"both sibling kinds must have been exercised, only {checked} were -- a loop "
        f"that runs fewer times than it claims prints exactly what a complete one prints"
    )


def test_the_same_invocation_with_only_segs_naming_the_claimed_id_admits(tmp_path):
    """THE GATE MUST BE A GATE, NOT A WALL. The identical root and the identical
    claim, with --only-segs narrowing the emitted set to exactly the claimed id,
    must ADMIT.

    Without this, the guard above is satisfiable by refusing every
    --from-stalled invocation, and the profile ships unusable. This one passes
    today and must still pass after -- which is the point: it is the half that
    stops the fix from over-reaching."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    claimed = LIVE_CLEAN_SEG
    build_from_stalled_segment(root, claimed, fixture_keys)
    build_bare_eligible_segment(root, "seg98", fixture_keys)
    write_manifest(root, [claimed, "seg98"])
    write_fixture_cache_keys(root, fixture_keys)

    proc = claim_stalled(root, claimed, extra=("--only-segs", claimed))
    assert proc.returncode == 0, (
        f"--only-segs naming exactly the claimed id must admit -- a guard that "
        f"refused here would make the profile unusable rather than narrow\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = parse_stdout(proc)
    assert out["segs"] == [claimed], (
        f"and the emitted set must be exactly the claimed id: {out['segs']!r}"
    )
    assert claimed in out["claims"]
    assert "seg98" not in out["segs"], (
        "the unclaimed sibling must not be dispatched -- that is the whole cost "
        "the requirement exists to stop"
    )


def test_a_mixed_claim_invocation_admits_when_only_segs_names_every_claimed_id(tmp_path):
    """THE CASE A NAIVE EQUALITY CHECK BREAKS, pinned BEFORE the guard is
    written.

    `segs == from_stalled_ids` would satisfy the refusal test above and silently
    forbid a legitimate invocation that claims one id under --from-stalled and
    another under a different profile in the same run. The rule has to be
    `segs ⊆ claim_requests` -- every emitted id is claimed, under SOME profile.

    --from-cap is the second profile on purpose: its population is genuinely
    human_escalation, so it already requires --only-segs, and it carries no
    sentinel -- which means this invocation exercises the D5.2 clearing over a
    MIXED set where only one of the two claimed ids is sentinel-bearing."""
    root = make_durable_root(tmp_path)
    fixture_keys: dict = {}
    stalled_seg, cap_seg = LIVE_CLEAN_SEG, "seg14"
    build_from_stalled_segment(root, stalled_seg, fixture_keys)
    build_from_cap_segment(root, cap_seg, fixture_keys)
    write_manifest(root, [stalled_seg, cap_seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root,
        "--from-stalled", stalled_seg,
        "--from-cap", cap_seg,
        "--only-segs", f"{stalled_seg},{cap_seg}",
        "--run-id", RUN_ID, "--run-resume", "false",
    )
    assert proc.returncode == 0, (
        f"a mixed claim invocation naming every emitted id must admit -- a guard "
        f"written as `segs == from_stalled_ids` forbids this for no gain\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = parse_stdout(proc)
    assert sorted(out["claims"]) == sorted([stalled_seg, cap_seg]), (
        f"both claims must land, each under its own profile: {out['claims']!r}"
    )
    assert out["claims"][stalled_seg]["profile"] == "from-stalled"
    assert out["claims"][cap_seg]["profile"] == "from-cap", (
        "the second id must genuinely be a DIFFERENT profile, or this is one "
        "profile tested twice"
    )
    assert out["previously_converged"] == [], (
        "and the sentinel-bearing half must still be cleared by D5.2"
    )
    # The per-segment lease is taken for the --from-stalled id and NOT for the
    # --from-cap one: criterion 4 is about the PROFILE, not about the
    # invocation, and a mixed run is the only place the two can be told apart.
    assert job_lock(root, stalled_seg).is_file()
    assert not job_lock(root, cap_seg).exists(), (
        "a --from-cap id in a mixed invocation must still take no per-segment "
        "lease -- the leases belong to --from-stalled's own ids"
    )
