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

`--citations-reviewed` is an OPERATOR ATTESTATION, not a proof: nothing on
disk records a `CITATIONS_OK` verdict, and the approved snapshot the reviewer
audits is written BEFORE the evidence is even fetched, so no artifact could
support a kernel check here. What the refusal buys is that a silent freeze
becomes a deliberate act -- the same shape as #412's
`--plugin-root`/`--allow-durable-sibling` and reject_review.py's attested
`--reason`.

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

    proc = run_cli(
        root,
        [
            "--research-mode",
            "live",
            "--merge-batches",
            str(batch_path),
            "--citations-reviewed",
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
    args = [
        "--research-mode",
        "live",
        "--merge-batches",
        str(batch_path),
        "--citations-reviewed",
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
