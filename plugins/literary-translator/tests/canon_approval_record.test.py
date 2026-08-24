"""--record-approval-to: the #723 verdict record.

WHAT IS BEING GUARDED. The glossary pass's citation review is the only thing
standing between a fabricated `source` URL and a permanently frozen canon row,
and until #723 its verdict existed ONLY inside one Workflow run's memory.
Nothing on disk said "batch i, these exact bytes, passed the review". The
approved snapshot is not that record and cannot become it: `approveBatchCmd()`
is `checkBatchCmd() + " --approve-to ..."`, run in step 1 of the PREPARE agent,
before the judge ever sees the fragment -- so snapshot existence proves shape
and coverage, never a verdict. Measured on the live run in #723: 40 snapshots
against 53 fragments, and only some of those snapshots were ever judged.

The consequence that made it a bug rather than an inconvenience: an operator who
stopped the pass and merged by hand selected each batch's input as "the first
`approved_{i}_attempt_*.json` whose bytes equal its `out_*` fragment", and that
heuristic picked, for batch 17, a fragment whose only recorded verdicts were
REJECTIONS. Its 15 `basis:"established"` rows merged under `--citations-reviewed`
-- an attestation that, for that composition of rows, was false. `--verify-merged`
did not catch it and cannot: it re-checks shape and coverage off disk, not
verdicts.

WHAT THIS FLAG IS, AND THE ONE THING IT MUST NEVER BECOME. It writes an audit
record for a HUMAN: the sha256 of the exact approved bytes, so the operator
selects the attested snapshot by digest instead of by that heuristic. Nothing in
the pass reads it back, and no gate in canon_validate.py consults it. That is
deliberate and is the whole reason the record is safe to keep in a directory the
dispatch agent can write: a record that authorizes nothing cannot be forged into
an authorization. The moment anything reads it to skip work, it becomes a
review-skip credential sitting inside the writable run directory -- which is
exactly the design #723 was descoped away from. `test_no_shipped_caller_reads_the_record_back`
below is the enforcement, not this paragraph.

SCOPE, vs tests/canon_approve_to.test.py. That file owns `--approve-to`: byte
fidelity, create-once publication, the concurrent-writer race. This file owns
`--record-approval-to`: that the digest is over the bytes that were VALIDATED,
that a failing check leaves no record, that a later approval REPLACES an earlier
record (the opposite of --approve-to's create-once, and for the opposite
reason), and that every other mode refuses the flag by name. The two flags share
one MODE_SPECS refusal column, so the refusal battery here is what keeps that
sharing honest.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _canon_project_fixture import (  # noqa: E402
    accepted_item,
    load_canon_validate_module,
    make_project,
    run_canon_init,
    run_canon_validate,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"

EXPECTED_SCHEMA = "glossary-approval/1"


def _valid_project(tmp_path):
    root = make_project(tmp_path)
    init = run_canon_init(root)
    assert init.returncode == 0, f"init failed:\n{init.stdout}\n{init.stderr}"
    return root


def _fragment_bytes(source_form: str, target_form: str, newline: bytes = b"\n") -> bytes:
    """One accepted item, pretty-printed, with the given line terminator.

    The CRLF/lone-CR variants are load-bearing exactly as they are for
    --approve-to: an LF-only fixture cannot tell read_bytes() from read_text(),
    since both yield identical bytes for LF content. Only a fragment whose
    on-disk bytes carry CR proves the DIGEST was taken over what was validated
    rather than over a universal-newline-translated re-read.
    """
    text = json.dumps([accepted_item(source_form, target_form)], indent=2, ensure_ascii=False)
    return text.replace("\n", newline.decode("latin-1")).encode("utf-8")


def _write(root: Path, name: str, raw: bytes) -> Path:
    path = root / name
    path.write_bytes(raw)
    return path


def _read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The digest is over the bytes that were VALIDATED.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "newline,label",
    [(b"\r\n", "crlf"), (b"\r", "lone_cr"), (b"\n", "lf")],
    ids=["crlf", "lone_cr", "lf"],
)
def test_record_digest_is_over_the_exact_validated_bytes(tmp_path, newline, label):
    root = _valid_project(tmp_path)
    raw = _fragment_bytes("Sappho", "Sapho", newline)
    frag = _write(root, f"frag_{label}.json", raw)
    record = root / f"approval_{label}.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--record-approval-to", str(record)
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    doc = _read_record(record)
    assert doc["schema"] == EXPECTED_SCHEMA
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest(), (
        "the record must name the digest of the fragment's EXACT on-disk bytes; "
        "a digest over re-read or re-serialised content would silently differ "
        "for any fragment carrying CR"
    )
    assert doc["recorded_from"] == str(frag)


def test_record_and_snapshot_agree_when_both_flags_are_given(tmp_path):
    """The pair the glossary pass actually issues: the record vouches for the
    bytes the snapshot published, so the operator can match one to the other."""
    root = _valid_project(tmp_path)
    raw = _fragment_bytes("Ninon", "Ninon", b"\r\n")
    frag = _write(root, "frag.json", raw)
    snapshot = root / "approved_0_attempt_0.json"
    record = root / "approval_0_attempt_0.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag),
        "--approve-to", str(snapshot),
        "--record-approval-to", str(record),
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    assert snapshot.read_bytes() == raw
    assert _read_record(record)["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()

    printed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert printed["approved_path"] == str(snapshot)
    assert printed["approval_record_path"] == str(record)


# ---------------------------------------------------------------------------
# A record is never written for bytes that failed a check. This is the ordering
# property, and it is the only thing that makes the record mean anything.
# ---------------------------------------------------------------------------

def test_a_rejected_fragment_leaves_no_record(tmp_path):
    root = _valid_project(tmp_path)
    # Missing canonical_target_form on an accepted item -- a Pass 1 failure.
    bad = json.dumps(
        [{"source_form": "Sappho", "is_proper_name": True, "disposition": "accepted"}],
        ensure_ascii=False,
    ).encode("utf-8")
    frag = _write(root, "bad.json", bad)
    record = root / "approval.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--record-approval-to", str(record)
    )
    assert proc.returncode != 0
    assert not record.exists(), (
        "a fragment that failed validation must leave NO verdict record -- a "
        "record written before the checks would vouch for bytes nothing approved"
    )


def test_a_coverage_failure_leaves_no_record(tmp_path):
    """The coverage half specifically: shape can pass while the fragment covers
    the wrong candidate set, and that batch is not the batch the manifest names."""
    root = _valid_project(tmp_path)
    frag = _write(root, "frag.json", _fragment_bytes("Sappho", "Sapho"))
    manifest = root / "manifest_0.json"
    manifest.write_text(json.dumps(["Sappho", "Ninon"]), encoding="utf-8")
    record = root / "approval.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag),
        "--expect-source-forms-file", str(manifest),
        "--record-approval-to", str(record),
    )
    assert proc.returncode != 0
    assert not record.exists()


def test_no_partial_temp_file_survives_a_successful_record_write(tmp_path):
    root = _valid_project(tmp_path)
    frag = _write(root, "frag.json", _fragment_bytes("Sappho", "Sapho"))
    record = root / "approval.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--record-approval-to", str(record)
    )
    assert proc.returncode == 0, proc.stderr
    leftovers = [p.name for p in root.iterdir() if p.name.startswith(".approval")]
    assert leftovers == [], f"atomic write left a temp file behind: {leftovers}"


# ---------------------------------------------------------------------------
# REPLACING, not create-once -- the deliberate asymmetry with --approve-to.
# ---------------------------------------------------------------------------

def test_a_later_approval_replaces_an_earlier_record(tmp_path):
    """A resumed run that re-reviews a batch and approves DIFFERENT bytes must
    supersede the stale record. --approve-to refuses that overwrite, because it
    guards a slot two concurrent reviewers could both claim within one run; the
    record answers a different question ("what was approved LAST") and a refusal
    there would leave the operator reading a record for bytes this run rejected.
    """
    root = _valid_project(tmp_path)
    record = root / "approval.json"

    first = _fragment_bytes("Sappho", "Sapho")
    frag_a = _write(root, "a.json", first)
    proc = run_canon_validate(
        root, "--check-batch", str(frag_a), "--record-approval-to", str(record)
    )
    assert proc.returncode == 0, proc.stderr
    assert _read_record(record)["sha256"] == hashlib.sha256(first).hexdigest()

    second = _fragment_bytes("Sappho", "Sappho")
    assert second != first
    frag_b = _write(root, "b.json", second)
    proc = run_canon_validate(
        root, "--check-batch", str(frag_b), "--record-approval-to", str(record)
    )
    assert proc.returncode == 0, f"the record must REPLACE, not refuse:\n{proc.stderr}"
    doc = _read_record(record)
    assert doc["sha256"] == hashlib.sha256(second).hexdigest()
    assert doc["recorded_from"] == str(frag_b)


# ---------------------------------------------------------------------------
# The shared MODE_SPECS refusal column, exercised over BOTH flags. The column is
# shared precisely so these two lists cannot drift; this battery is what proves
# the sharing is real rather than a comment.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--approve-to", "--record-approval-to"])
def test_fragment_bytes_flags_refused_in_validate_only(tmp_path, flag):
    root = _valid_project(tmp_path)
    proc = run_canon_validate(root, flag, str(root / "x.json"))
    assert proc.returncode == 2, proc.stdout
    assert f"validate-only (no mode flag) does not accept {flag}" in proc.stderr, (
        f"expected validate-only's own refusal phrase for {flag}, got:\n{proc.stderr}"
    )
    assert not (root / "x.json").exists()


@pytest.mark.parametrize("flag", ["--approve-to", "--record-approval-to"])
@pytest.mark.parametrize(
    "mode_args,refusing_flag",
    [
        (["--init"], "--init"),
        (["--restamp-derivation"], "--restamp-derivation"),
        (["--merge-batches", "frag.json"], "--merge-batches"),
        (["--verify-merged", "--batch", "frag.json"], "--verify-merged"),
        (["--batch", "frag.json"], "--batch (legacy single-fragment merge)"),
    ],
    ids=["init", "restamp", "merge_batches", "verify_merged", "legacy_bare_batch"],
)
def test_fragment_bytes_flags_refused_in_other_modes(tmp_path, mode_args, refusing_flag, flag):
    root = _valid_project(tmp_path)
    proc = run_canon_validate(root, *mode_args, flag, str(root / "x.json"))
    assert proc.returncode == 2, f"expected refusal, got:\n{proc.stdout}\n{proc.stderr}"
    assert f"{refusing_flag} does not accept {flag}" in proc.stderr, (
        f"expected {refusing_flag}'s own MODE_SPECS refusal for {flag}, got:\n{proc.stderr}"
    )
    assert not (root / "x.json").exists()


def test_the_refusal_column_is_one_column_governing_both_flags():
    """Read off the script's own table, never restated here: a second column
    carrying the same values row for row is the two-hand-maintained-lists defect
    MODE_SPECS exists to remove, and it is how one flag comes to be guarded in a
    mode where the other is not."""
    module = load_canon_validate_module()
    for spec in module.MODE_SPECS:
        assert hasattr(spec, "fragment_bytes_flag_refusal"), (
            f"{spec.flag} has no fragment_bytes_flag_refusal column"
        )
    assert not any(
        f.startswith("approve_to_refusal") or f.startswith("record_approval")
        for f in module.MODE_SPECS[0]._fields
    ), (
        "a per-flag refusal column reappeared beside the shared one; the reason "
        "is a property of the MODE, not of which flag was passed"
    )
    assert "record_approval_to" in module.NON_MODE_DESTS, (
        "the new destination must be declared as an OPTION, or "
        "canon_stamp_conservation.test.py's bidirectional drift check reads it "
        "as an undeclared MODE"
    )


# ---------------------------------------------------------------------------
# The record has no in-band consumer, and must not grow one.
# ---------------------------------------------------------------------------

def test_no_shipped_caller_reads_the_record_back():
    """The record sits in a directory the dispatch agent can write. That is safe
    for exactly as long as nothing reads it to authorize work -- at which point
    it becomes a forgeable review-skip credential, which is the design #723 was
    deliberately descoped away from (the trusted point stays the operator, who
    holds --citations-reviewed from outside the writable zone).

    So: canon_validate.py may WRITE the record and must never READ one, and the
    glossary template may name the record path in exactly one emitted command --
    the write. A reading flag, or a second mention, is this property breaking.
    """
    script = (SCRIPTS_DIR / "canon_validate.py").read_text(encoding="utf-8")
    assert "--record-approval-to" in script
    for reading_flag in ("--approved-record", "--require-approval", "--verify-approval"):
        assert reading_flag not in script, (
            f"{reading_flag} would make the audit record an in-band gate"
        )
    assert "_read_approval_record" not in script and "_load_approval_record" not in script

    template = (TEMPLATES_DIR / "glossary-pass-wf.template.js").read_text(encoding="utf-8")
    # The COMMAND-BUILDING form specifically -- ` --record-approval-to ` spliced
    # into a concatenation -- not every mention of the flag's name. The operator
    # message on the merge-refusal path names the command in prose so a human can
    # run it by hand, and counting that as a consumer would be the assertion
    # misreading its own subject. A SECOND builder is what this forbids.
    builder_sites = template.count('" --record-approval-to "')
    assert builder_sites == 1, (
        f"the record path must be spliced into exactly ONE emitted command -- the "
        f"write. Found {builder_sites} command-building sites; a second one is "
        f"something starting to consume the record."
    )
    assert "approvalRecordPath(" in template
    # And nothing may read it: no emitted command may pass the record path to a
    # flag that consumes it, under any spelling.
    for reading_flag in ("--approved-record", "--require-approval", "--verify-approval"):
        assert reading_flag not in template, (
            f"{reading_flag} in the template would make the audit record an "
            f"in-band gate -- the review-skip credential #723 was descoped away "
            f"from"
        )
