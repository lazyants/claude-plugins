"""tests/canon_merge_citation_attestation.test.py -- #505: the WRITER refuses
to freeze a `basis:"established"` citation that no citation review has
approved.

The pre-merge citation review (1.16.0/1.16.1) lives entirely inside
glossary-pass-wf.template.js's control flow: it fetches every established
item's `source` through fetch_citation.py and rejects the batch unless the
retrieved page exists, documents the right entity, and attests the claimed
`canonical_target_form`. `canon_validate.py` never knew about it -- so a merge
driven BY HAND (the path canon_validate.py's own module docstring records the
real historiettes-t3 project used, and the path #505's 7-of-23-unusable
measurement came from) froze fabricated evidence into an immutable canon row
with no signal at all.

`--citations-reviewed` is an OPERATOR ATTESTATION, not a proof, and it stays
one. What the refusal buys is that a silent freeze becomes a deliberate act --
the same shape as #412's `--plugin-root`/`--allow-durable-sibling` and
reject_review.py's attested `--reason`.

WHAT #723 CHANGED, AND WHAT IT POINTEDLY DID NOT. This docstring used to say
"nothing on disk records a `CITATIONS_OK` verdict", and that was the defect
rather than the design: an operator merging by hand had to GUESS which snapshot
the reviewer approved, and on the measured run they guessed wrong for one batch
whose only recorded verdicts were rejections. Since #723 the pass writes a
verdict record (`canon_validate.py --record-approval-to`) naming the sha256 of
every approved fragment, so the attesting operator can select those exact bytes
by digest. The approved snapshot remains no evidence of a verdict -- it is still
written BEFORE the evidence is fetched, which is exactly why a separate record
was needed. THIS SCRIPT STILL READS NEITHER. No kernel check consults the
record, deliberately: it lives in a directory the dispatch agent can write, so a
gate that trusted it would be a forgeable one. The attestation remains the
operator's, made from outside that directory -- the record just gives it
something true to rest on.

Scope of the guard, pinned below:

  * `--research-mode live` only -- `offline` already forbids
    `basis:"established"` outright through the merge-time backstop, which must
    keep firing with its OWN message (test 8).
  * BOTH merge modes -- `--merge-batches` and the legacy bare `--batch`, the
    one that historically escaped every table-driven guard.
  * DISPOSITION-INDEPENDENT -- a `review_queue` item may carry
    `basis:"established"` (canon-batch.schema.json's queued branch requires
    only `note`), and the Workflow reviewer scopes by basis, so an
    implementation that scanned only `accepted` items would be wrong. Test 3
    is the one that fails such an implementation.
  * REFUSAL BEFORE ANY WRITE -- every refusing test asserts canon.json is
    byte-identical afterwards, because a guard that fires after the atomic
    write would still have frozen the row.

Fixture convention follows canon_format_validation.test.py: the REAL
canon_validate.py and the REAL canon-*.schema.json files are copied into an
isolated tmp_path durable_root (via the sanctioned _senses_fixture helper),
with a stub cache_key.py, so the script's self-anchoring resolves against the
fixture and never this repo's assets tree.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)

VALID_URI = "https://example.org/reference/sun-king"

FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field")
    parser.add_argument("--seg", default=None)
    args = parser.parse_args()
    if not args.field:
        sys.stderr.write("fake cache_key.py: test stub requires --field\\n")
        return 1
    print(f"fixture-{args.field}-hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    (root / "scripts" / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC / name, schemas_dir / name)
    return root


def write_batch(root, batch, name="batch.json"):
    path = root / name
    path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    return path


def write_approval_record(root, batch_path, name=None, sha256=None,
                          schema="glossary-approval/1"):
    """The #734 verdict record for `batch_path`, digested from the bytes ON
    DISK -- never from the object that produced them, so a test that writes a
    fragment and then a record is asserting about the same bytes the script
    will read.

    `sha256` and `schema` are overridable so a fixture can build a record that
    is well-formed but WRONG, which is the only kind this file's refusal tests
    are about; the default is the honest one."""
    raw = Path(batch_path).read_bytes()
    path = root / (name or (Path(batch_path).stem + "_approval.json"))
    path.write_text(
        json.dumps({
            "schema": schema,
            "sha256": sha256 if sha256 is not None else hashlib.sha256(raw).hexdigest(),
            "recorded_from": str(batch_path),
        }),
        encoding="utf-8",
    )
    return path


def accepted_established(source_form, target="Sun King", source=VALID_URI):
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": target,
        "basis": "established",
        "confidence": "high",
        "source": source,
        "disposition": "accepted",
    }


def accepted_transliterated(source_form, target="Gerin"):
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": target,
        "basis": "transliterated",
        "confidence": "medium",
        "disposition": "accepted",
    }


def queued_established(source_form, source=VALID_URI):
    """canon-batch.schema.json's QUEUED branch requires only `note`; `basis`
    and `source` are optional and unconstrained there, so an established claim
    can ride into canon.json's review_queue[] verbatim."""
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "review_queue",
        "note": "cannot settle the spelling",
        "basis": "established",
        "source": source,
    }


def run_cli(root, args, timeout=30):
    cmd = (
        [sys.executable, str(root / "scripts" / "canon_validate.py")]
        + list(args)
        + ["--allow-durable-sibling"]
    )
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root)
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def canon_bytes(root):
    path = root / "canon.json"
    return path.read_bytes() if path.exists() else None


# ---------------------------------------------------------------------------
# 1-2. --merge-batches, accepted established item
# ---------------------------------------------------------------------------


def test_live_merge_batches_refuses_an_unattested_established_item(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    before = canon_bytes(root)

    proc = run_cli(root, ["--research-mode", "live", "--merge-batches", str(batch_path)])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["offending"] == ["Roi Soleil"]
    assert "--citations-reviewed" in payload["error"]
    # The refusal must precede the write, not follow it: a guard that fired
    # after _atomic_write_json would leave the fabricated row frozen anyway.
    assert canon_bytes(root) == before


def test_live_merge_batches_accepts_an_attested_established_item(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    # #734: the attestation no longer travels alone. Every call in this file
    # that expects the merge to SUCCEED now carries the record the flag rests
    # on; the calls that expect a refusal deliberately do not, and say so.
    record_path = write_approval_record(root, batch_path)

    proc = run_cli(
        root,
        [
            "--research-mode",
            "live",
            "--merge-batches",
            str(batch_path),
            "--citations-reviewed",
            "--approval-records",
            str(record_path),
        ],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["Roi Soleil"]["source"] == VALID_URI


# ---------------------------------------------------------------------------
# 3. review_queue -- the test that fails an accepted-only implementation
# ---------------------------------------------------------------------------


def test_live_merge_refuses_a_queued_established_item_too(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [queued_established("Duc de Guise")])
    before = canon_bytes(root)

    proc = run_cli(root, ["--research-mode", "live", "--merge-batches", str(batch_path)])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["offending"] == ["Duc de Guise"], (
        "the guard must key on basis, never on disposition -- a review_queue "
        "item carrying basis:'established' freezes its source into "
        "canon.json's review_queue[] just as verbatim as an accepted one"
    )
    assert canon_bytes(root) == before


# ---------------------------------------------------------------------------
# 4. the legacy bare --batch merge inherits the same refusal
# ---------------------------------------------------------------------------


def test_legacy_batch_merge_refuses_an_unattested_established_item(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    before = canon_bytes(root)

    proc = run_cli(root, ["--research-mode", "live", "--batch", str(batch_path)])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["offending"] == ["Roi Soleil"]
    assert canon_bytes(root) == before


# ---------------------------------------------------------------------------
# 5. every NON-merge mode refuses the flag, each with its own stated reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode_args, expected_flag, expected_reason",
    [
        (["--init"], "--init", "reads no fragment"),
        (["--restamp-derivation"], "--restamp-derivation", "reads no fragment"),
        (["--check-batch", "batch.json"], "--check-batch", "writes no canon row"),
        (
            ["--verify-merged", "--batch", "batch.json"],
            "--verify-merged",
            "writes no canon row",
        ),
        ([], "validate-only", "writes no canon row"),
    ],
)
def test_non_merge_modes_refuse_the_attestation(
    tmp_path, mode_args, expected_flag, expected_reason
):
    """Parameterized over EVERY mode that writes no canon row, not
    --check-batch alone: the point is that the refusal is table-driven (plus
    validate-only's own hand guard), so a mode added later inherits it.

    The `unrecognized arguments` exclusion is load-bearing. Without it four of
    these five cases pass VACUOUSLY on a tree where the flag does not exist at
    all: argparse's own usage banner names every mode flag, so `--init in
    stderr` is satisfied by the parse error itself. Watched failing that way
    before the exclusion was added."""
    root = make_durable_root(tmp_path)
    write_batch(root, [accepted_transliterated("Guerin")])

    proc = run_cli(
        root, ["--research-mode", "live"] + mode_args + ["--citations-reviewed"]
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unrecognized arguments" not in proc.stderr, (
        "this must be a REASONED per-mode refusal, not argparse failing to "
        "know the flag at all"
    )
    assert "--citations-reviewed" in proc.stderr
    assert expected_flag in proc.stderr
    assert expected_reason in proc.stderr


# ---------------------------------------------------------------------------
# 6. idempotency -- a second attested merge of the same bytes changes nothing
# ---------------------------------------------------------------------------


def test_a_second_attested_merge_of_the_same_bytes_moves_nothing(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    record_path = write_approval_record(root, batch_path)
    args = [
        "--research-mode",
        "live",
        "--merge-batches",
        str(batch_path),
        "--citations-reviewed",
        "--approval-records",
        str(record_path),
    ]

    first = run_cli(root, args)
    assert first.returncode == 0, first.stdout + first.stderr
    after_first = canon_bytes(root)

    second = run_cli(root, args)
    assert second.returncode == 0, second.stdout + second.stderr
    assert canon_bytes(root) == after_first, (
        "the attestation must not disturb #291's stamp conservation -- an "
        "identical re-submission still changes nothing on disk"
    )


# ---------------------------------------------------------------------------
# 7-8. negative controls: the guard must not over-catch, and must not
#      displace the offline backstop. Both are GREEN on the unfixed tree.
# ---------------------------------------------------------------------------


def test_live_merge_without_any_established_item_needs_no_attestation(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_transliterated("Guerin")])

    proc = run_cli(root, ["--research-mode", "live", "--merge-batches", str(batch_path)])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout(proc)["success"] is True


def test_offline_established_item_still_fails_through_the_backstop(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])

    proc = run_cli(
        root, ["--research-mode", "offline", "--merge-batches", str(batch_path)]
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["offending"] == ["Roi Soleil"]
    assert "--citations-reviewed" not in payload["error"], (
        "offline forbids basis:'established' outright -- the NEW guard must "
        "never displace that message, or an operator is told to attest a "
        "review that offline has no way to run"
    )


# ---------------------------------------------------------------------------
# 9. #734 -- the attestation must rest on a record, and the record must be
#    about THESE bytes.
#
# WHY THIS SECTION EXISTS AT ALL, since #723 shipped the record deliberately
# unread. The pass already decided whether to merge on whether the record had
# been written -- and it decided it by reading an AGENT'S SENTENCE claiming so.
# That is not "no consumer"; it is a consumer trusting an unverified claim. The
# reviewer on #734 pointed at it and the fix moves the decision onto the
# filesystem.
#
# What these tests do NOT claim, because the check cannot deliver it: that the
# record is HONEST. The same agent turn runs the write command and reports on
# it, so a turn willing to fabricate the sentinel can equally run a command that
# writes a well-formed record. What is closed is the case that happens without
# malice -- the command never ran, or failed -- and every test below is written
# against that case rather than against an adversary.
# ---------------------------------------------------------------------------


def test_live_attested_merge_refuses_without_a_record(tmp_path):
    """The direction that closes the hole. A caller that attests and offers no
    evidence is halted before anything is merged, and the message names the
    flag it must add rather than only the flag it may not use."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(batch_path),
         "--citations-reviewed"],
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--approval-records" in proc.stderr
    assert canon_bytes(root) == before, "the refusal must precede every write"


def test_a_record_for_other_bytes_refuses_the_merge(tmp_path):
    """THE test of this section, and the only one that fails an implementation
    which merely checks the record EXISTS.

    The record is well-formed and its schema is right; it simply attests to a
    different fragment. That is exactly the shape a stale record takes -- a
    batch regenerated to a later attempt while an earlier attempt's record sits
    beside it -- and it is the shape #723's own motivating incident had: a batch
    merged under an attestation whose only recorded verdicts belonged to other
    bytes."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    other_path = write_batch(root, [accepted_established("Vert Galant")],
                             name="other.json")
    stale_record = write_approval_record(root, other_path)
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(batch_path),
         "--citations-reviewed", "--approval-records", str(stale_record)],
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "sha256" in payload["error"]
    assert canon_bytes(root) == before, "the refusal must precede every write"


def test_a_record_of_the_wrong_schema_refuses_the_merge(tmp_path):
    """A record whose digest is RIGHT but whose schema is not this one. Worth
    its own case rather than folding into the digest test: the digest is the
    interesting field, so an implementation that checked only the digest would
    accept any JSON object that happened to carry a matching sha256 -- including
    one written by a future, differently-meaning record format."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    wrong = write_approval_record(root, batch_path, schema="glossary-approval/2")
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(batch_path),
         "--citations-reviewed", "--approval-records", str(wrong)],
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "schema" in payload["error"]
    assert canon_bytes(root) == before


def test_a_missing_record_file_refuses_the_merge(tmp_path):
    """The literal case the sentinel could lie about: the write command never
    ran, so there is no file at all."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(batch_path),
         "--citations-reviewed", "--approval-records",
         str(root / "approval_that_was_never_written.json")],
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert canon_bytes(root) == before


def test_records_are_paired_positionally_and_the_counts_must_match(tmp_path):
    """One record per fragment, same order. A count mismatch is refused rather
    than zipped short -- Python's zip() would silently drop the unpaired tail,
    merging a fragment nothing attested to while every check that DID run
    passed."""
    root = make_durable_root(tmp_path)
    first = write_batch(root, [accepted_established("Roi Soleil")], name="a.json")
    second = write_batch(root, [accepted_established("Vert Galant")], name="b.json")
    only_one = write_approval_record(root, first)
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(first), str(second),
         "--citations-reviewed", "--approval-records", str(only_one)],
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "one record per merged fragment" in payload["error"]
    assert canon_bytes(root) == before


def test_a_later_fragments_bad_record_refuses_before_the_first_is_merged(tmp_path):
    """The all-or-nothing property #505 already claims for the attestation,
    extended to its evidence. Pinned because the loop that enforces it is the
    same pre-merge loop, and moving the record check into the merge loop would
    leave fragment 0 merged and fragment 1 refused -- a canon.json nobody
    intended and no mode reports."""
    root = make_durable_root(tmp_path)
    first = write_batch(root, [accepted_established("Roi Soleil")], name="a.json")
    second = write_batch(root, [accepted_established("Vert Galant")], name="b.json")
    good = write_approval_record(root, first)
    bad = write_approval_record(root, second, name="b_bad.json",
                                sha256="0" * 64)
    before = canon_bytes(root)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(first), str(second),
         "--citations-reviewed", "--approval-records", str(good), str(bad)],
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert canon_bytes(root) == before, (
        "the FIRST fragment must not have been merged -- a later fragment's "
        "unbacked attestation has to refuse the whole call"
    )


def test_records_without_the_attestation_are_refused(tmp_path):
    """The other direction. --approval-records alone verifies something the
    caller then does not claim, so it changes no outcome -- the shape a reader
    mistakes for a guarantee."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_transliterated("Guerin")])
    record_path = write_approval_record(root, batch_path)

    proc = run_cli(
        root,
        ["--research-mode", "live", "--merge-batches", str(batch_path),
         "--approval-records", str(record_path)],
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unrecognized arguments" not in proc.stderr
    assert "--citations-reviewed" in proc.stderr


def test_the_legacy_single_batch_merge_enforces_the_record_too(tmp_path):
    """The mode that historically escaped every table-driven guard. A record
    check present only on --merge-batches would be bypassable by one different
    CLI spelling of the same merge."""
    root = make_durable_root(tmp_path)
    batch_path = write_batch(root, [accepted_established("Roi Soleil")])
    stale = write_approval_record(root, batch_path, name="stale.json",
                                  sha256="1" * 64)
    before = canon_bytes(root)

    refused = run_cli(
        root,
        ["--research-mode", "live", "--batch", str(batch_path),
         "--citations-reviewed", "--approval-records", str(stale)],
    )
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert canon_bytes(root) == before

    # ...and the honest record is accepted by that same mode, so the assertion
    # above is about the RECORD and not about --batch refusing the flag outright.
    good = write_approval_record(root, batch_path)
    accepted = run_cli(
        root,
        ["--research-mode", "live", "--batch", str(batch_path),
         "--citations-reviewed", "--approval-records", str(good)],
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert canon_bytes(root) != before
