"""--approve-to: canon_validate.py --check-batch snapshots the EXACT validated
bytes so the citation reviewer audits an immutable copy and the merge consumes
that same copy (LT 1.16.0, the producer side of "bind the merge to the bytes
that were reviewed").

The load-bearing test here is byte-fidelity under CRLF: an LF-only fixture
cannot tell read_bytes() from read_text(), because both yield identical bytes
for LF content. Only a fragment whose on-disk bytes contain CR proves the
snapshot preserved them rather than universal-newline-normalising them.
"""

import json
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _canon_project_fixture import (  # noqa: E402
    make_project,
    run_canon_init,
    run_canon_validate,
    accepted_item,
    write_fragment,
)


def _valid_project(tmp_path):
    root = make_project(tmp_path)
    init = run_canon_init(root)
    assert init.returncode == 0, f"init failed:\n{init.stdout}\n{init.stderr}"
    return root


def _valid_fragment_bytes(newline: bytes) -> bytes:
    """One accepted item, pretty-printed, with the given line terminator.
    JSON permits CR/CRLF as inter-token whitespace, so this stays valid while
    carrying bytes read_text() would rewrite."""
    text = json.dumps([accepted_item("Sappho", "Sapho")], indent=2, ensure_ascii=False)
    return text.replace("\n", newline.decode("latin-1")).encode("utf-8")


# ---------------------------------------------------------------------------
# Byte fidelity -- the reason the snapshot exists at all.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "newline,label",
    [(b"\r\n", "crlf"), (b"\r", "lone_cr"), (b"\n", "lf")],
    ids=["crlf", "lone_cr", "lf"],
)
def test_snapshot_is_byte_identical_to_the_validated_fragment(tmp_path, newline, label):
    root = _valid_project(tmp_path)
    raw = _valid_fragment_bytes(newline)
    frag = root / f"frag_{label}.json"
    frag.write_bytes(raw)
    out = root / "approved_0_attempt_0.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--approve-to", str(out)
    )
    assert proc.returncode == 0, f"check-batch failed:\n{proc.stdout}\n{proc.stderr}"
    assert out.read_bytes() == raw, (
        f"the {label} snapshot is not byte-identical to the fragment it validated. "
        f"read_text() would normalise the line endings here -- the snapshot must "
        f"use read_bytes() and copy the raw bytes"
    )
    # The stdout JSON line names the path it wrote, so a caller can bank it.
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload.get("approved_path") == str(out)


# ---------------------------------------------------------------------------
# Snapshot only on a PASS.
# ---------------------------------------------------------------------------

def test_a_rejected_fragment_leaves_no_approved_copy(tmp_path):
    root = _valid_project(tmp_path)
    # A fragment that fails Pass-1 schema validation (missing required fields).
    frag = write_fragment(root, [{"source_form": "X"}], name="bad.json")
    out = root / "approved_0_attempt_0.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--approve-to", str(out)
    )
    assert proc.returncode == 1, f"expected validation failure, got:\n{proc.stdout}"
    assert not out.exists(), (
        "a fragment that failed --check-batch left an approved snapshot; the "
        "snapshot must happen only after every check passes"
    )


def test_no_partial_tmp_file_survives_a_successful_snapshot(tmp_path):
    root = _valid_project(tmp_path)
    frag = root / "frag.json"
    frag.write_bytes(_valid_fragment_bytes(b"\n"))
    out = root / "approved_1_attempt_0.json"

    proc = run_canon_validate(
        root, "--check-batch", str(frag), "--approve-to", str(out)
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    leftovers = list(out.parent.glob(f".{out.name}.tmp.*"))
    assert leftovers == [], f"atomic write left a temp file behind: {leftovers}"


# ---------------------------------------------------------------------------
# Refused in every mode that is not --check-batch.
# ---------------------------------------------------------------------------

def test_approve_to_refused_in_validate_only(tmp_path):
    root = _valid_project(tmp_path)
    # validate-only = no mode flag; a canon.json exists so validation runs.
    proc = run_canon_validate(root, "--approve-to", str(root / "x.json"))
    assert proc.returncode == 2, proc.stdout
    assert "validate-only" in proc.stderr and "--approve-to" in proc.stderr
    assert not (root / "x.json").exists()


@pytest.mark.parametrize(
    "mode_args",
    [
        ["--init"],
        ["--restamp-derivation"],
        ["--merge-batches", "frag.json"],
        ["--verify-merged", "--batch", "frag.json"],
    ],
    ids=["init", "restamp", "merge_batches", "verify_merged"],
)
def test_approve_to_refused_in_other_modes(tmp_path, mode_args):
    root = _valid_project(tmp_path)
    proc = run_canon_validate(root, *mode_args, "--approve-to", str(root / "x.json"))
    assert proc.returncode == 2, f"expected refusal, got:\n{proc.stdout}\n{proc.stderr}"
    assert "--approve-to" in proc.stderr
    assert not (root / "x.json").exists()
