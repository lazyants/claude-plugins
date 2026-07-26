"""--approve-to: canon_validate.py --check-batch snapshots the EXACT validated
bytes so the citation reviewer audits an immutable copy and the merge consumes
that same copy (LT 1.16.0, the producer side of "bind the merge to the bytes
that were reviewed").

The load-bearing test here is byte-fidelity under CRLF: an LF-only fixture
cannot tell read_bytes() from read_text(), because both yield identical bytes
for LF content. Only a fragment whose on-disk bytes contain CR proves the
snapshot preserved them rather than universal-newline-normalising them.
"""

import itertools
import json
import subprocess
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
# The CONSUMER side of the snapshot seam: every fragment read is byte-exact.
#
# Not a byte-IDENTITY check -- that is unobservable here, and deliberately not
# tested: a raw CR inside a JSON string is invalid JSON under Python's strict
# parser (so such a fragment never passes --check-batch and never becomes a
# snapshot), and outside a string it is inter-token whitespace the parser
# discards. read_text() and read_bytes() therefore always yield the SAME parsed
# document, and no assertion over merged canon content can tell them apart.
#
# What IS observable, and what these tests pin: read_text() raises
# UnicodeDecodeError on a non-UTF-8 fragment, and _read_json_file does not catch
# it (it catches FileNotFoundError/OSError/JSONDecodeError; UnicodeDecodeError is
# a ValueError). So a reachable failure escaped into main()'s defensive
# `except Exception` catch-all and surfaced as "unexpected error: 'utf-8' codec
# can't decode byte 0xff...", instead of this module's own named failure naming
# the offending file. Reading fragments as bytes closes that.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mode_flags",
    [
        ("--merge-batches",),
        ("--verify-merged", "--batch"),
        ("--batch",),
        ("--check-batch",),
    ],
    ids=["merge_batches", "verify_merged", "legacy_batch", "check_batch"],
)
def test_a_non_utf8_fragment_fails_with_this_modules_own_named_error(tmp_path, mode_flags):
    """Every mode that reads a fragment must reject a non-UTF-8 one through
    CanonValidationError -- naming the file -- never through main()'s catch-all,
    which is marked `# pragma: no cover -- defensive` precisely because nothing
    reachable is supposed to land there."""
    root = _valid_project(tmp_path)
    # Valid JSON, valid schema, ONE defect: a lone 0xFF byte inside a string
    # value, so the encoding is the only thing under test.
    raw = json.dumps(
        [accepted_item("Sappho", "Sapho")], indent=2, ensure_ascii=False
    ).encode("utf-8").replace(b"Sapho", b"Sap\xffho")
    frag = root / "bad_utf8.json"
    frag.write_bytes(raw)

    proc = run_canon_validate(root, *mode_flags, str(frag))

    assert proc.returncode == 1, f"expected a clean failure, got:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload.get("success") is False, payload
    error = payload.get("error", "")
    assert "unexpected error" not in error, (
        "a non-UTF-8 fragment escaped as an unhandled UnicodeDecodeError into "
        f"main()'s defensive catch-all instead of a named failure: {error!r}"
    )
    assert "is not valid UTF-8" in error and str(frag) in error, (
        f"the failure must name the offending fragment and why: {error!r}"
    )


# ---------------------------------------------------------------------------
# Write-once-per-content: an audited snapshot is never silently replaced.
#
# A plain atomic write cannot carry the invariant this snapshot exists for. The
# reviewer audits the bytes at approved_{i}_attempt_{n}.json and the merge later
# consumes that same path, so a SECOND --approve-to call landing different bytes
# there -- a duplicate STEP-1 call, or two overlapping reviewer dispatches for
# the same batch/attempt -- makes the merge consume bytes nobody reviewed, with
# nothing anywhere reporting that the audited copy was lost.
# ---------------------------------------------------------------------------

def test_approve_to_refuses_to_overwrite_an_audited_snapshot_with_different_bytes(tmp_path):
    root = _valid_project(tmp_path)
    out = root / "approved_0_attempt_0.json"

    audited = _valid_fragment_bytes(b"\n")
    frag_x = root / "frag_x.json"
    frag_x.write_bytes(audited)
    first = run_canon_validate(root, "--check-batch", str(frag_x), "--approve-to", str(out))
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    assert out.read_bytes() == audited

    # A different, equally VALID fragment approved into the same slot -- so the
    # refusal below is about the duplicate write, not about a failed check.
    usurper = json.dumps(
        [accepted_item("Alcaeus", "Alkey")], indent=2, ensure_ascii=False
    ).encode("utf-8")
    assert usurper != audited
    frag_y = root / "frag_y.json"
    frag_y.write_bytes(usurper)
    second = run_canon_validate(root, "--check-batch", str(frag_y), "--approve-to", str(out))

    assert second.returncode != 0, (
        "a second --approve-to wrote DIFFERENT bytes over an already-approved "
        f"snapshot and still succeeded:\n{second.stdout}\n{second.stderr}"
    )
    assert out.read_bytes() == audited, (
        "the already-audited snapshot bytes were replaced; the audited copy must "
        "survive so the merge still consumes exactly what was reviewed"
    )
    assert str(out) in second.stdout, (
        f"the refusal must name the snapshot path it protected; got {second.stdout!r}"
    )


def test_re_approving_the_identical_bytes_is_an_idempotent_no_op(tmp_path):
    """The guard above must fail CLOSED on a conflict without breaking a plain
    re-run: approving the SAME bytes to the same path twice is how a retried
    step behaves, and it changes nothing."""
    root = _valid_project(tmp_path)
    out = root / "approved_0_attempt_0.json"
    raw = _valid_fragment_bytes(b"\r\n")
    frag = root / "frag.json"
    frag.write_bytes(raw)

    for attempt in ("first", "second"):
        proc = run_canon_validate(
            root, "--check-batch", str(frag), "--approve-to", str(out)
        )
        assert proc.returncode == 0, (
            f"the {attempt} approve of identical bytes failed:\n{proc.stdout}\n{proc.stderr}"
        )
        assert out.read_bytes() == raw
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload.get("approved_path") == str(out)


# ---------------------------------------------------------------------------
# ...including CONCURRENT first writers, which a check-then-act guard misses.
#
# `if path.exists(): refuse; else: write` closes only the SEQUENTIAL duplicate.
# Writers that ALL observe an absent path all write, the last one silently wins,
# and NONE of them is told: reviewer A audits bytes A, B lands bytes B, A returns
# CITATIONS_OK, and the merge consumes B.
#
# WHAT THIS TEST IS: a probabilistic REGRESSION CHECK. It has caught a
# check-then-act publish on more than one machine, but it does not prove the
# guarantee and nothing in the module rests on it -- that is os.link()'s
# create-once semantics, per references/canon-and-glossary.md, "What the approved
# snapshot guarantees, and the preconditions it rests on". Two mechanisms raise
# its odds of reaching the breaking interleaving, and neither makes it certain:
#
# 1. A READINESS HANDSHAKE, not a shared deadline. Every writer announces itself
#    and then blocks until it has seen every sibling announce, so none can enter
#    the critical section until all of them are already at its door. A wall-clock
#    deadline fixed before launch cannot promise even that -- on a slow, loaded or
#    single-core runner the first writer can complete the whole check-and-write
#    before the last one even starts, every assertion below still passes, and a
#    check-then-act guard survives the test untouched.
# 2. AN OVERLAP CHECK, whose limit is worth stating exactly. Each writer reports
#    when it entered and left the call, and an attempt only COUNTS once two of
#    those intervals intersect, so a run that degenerated into sequential calls is
#    retried rather than banked -- that much closes the vacuity hole. What it
#    establishes is that two writers were inside the FUNCTION together. It does
#    NOT establish that two of them observed the target ABSENT, which is the
#    interleaving that actually breaks check-then-act: the function writes its
#    temp file BEFORE publishing, so writers can overlap there while the publish
#    still serialises. Sampling several attempts is what makes the breaking
#    interleaving likely; nothing here makes it guaranteed.
#
# The racers call _write_approved_snapshot directly rather than running
# --check-batch end to end, deliberately: the window is microseconds wide, so
# full CLI runs would only ever sample it by luck. The CLI path over this same
# function is covered by the sequential tests above.
# ---------------------------------------------------------------------------

RACE_WORKER = '''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import canon_validate as cv

target = Path(sys.argv[2])
raw = Path(sys.argv[3]).read_bytes()
barrier = Path(sys.argv[4])
me = sys.argv[5]
expected = int(sys.argv[6])
deadline = time.time() + float(sys.argv[7])

# Announce readiness, then block until EVERY sibling has announced. Bounded, so a
# worker that never meets its siblings reports it and fails the test loudly
# instead of hanging the suite. The 0.5ms sleep keeps N spinners from starving
# each other on a small runner; measured, it does not widen the release skew --
# it narrows the worst case, because tight spinning is itself what delays the
# siblings we are waiting for.
(barrier / ("ready_" + me)).write_bytes(b"")
while len(list(barrier.glob("ready_*"))) < expected:
    if time.time() > deadline:
        sys.stdout.write(json.dumps({"outcome": "BARRIER_TIMEOUT", "id": me}))
        raise SystemExit(0)
    time.sleep(0.0005)

entered = time.time()
try:
    cv._write_approved_snapshot(target, raw)
    outcome = "OK"
except cv.CanonValidationError:
    outcome = "REFUSED"
left = time.time()
sys.stdout.write(json.dumps({"outcome": outcome, "id": me, "entered": entered, "left": left}))
'''

RACE_WRITERS = 8
RACE_SAMPLES = 3
RACE_ATTEMPT_BUDGET = 12
BARRIER_TIMEOUT_SECONDS = 60.0


def _overlapping_pair(reports):
    """The ids of two writers that were inside the critical section at the same
    time -- their [entered, left] intervals intersect -- or None if the writers
    ran strictly one after another, which proves nothing about concurrency."""
    for a, b in itertools.combinations(reports, 2):
        if a["entered"] < b["left"] and b["entered"] < a["left"]:
            return (a["id"], b["id"])
    return None


def test_concurrent_first_writers_cannot_both_publish(tmp_path):
    root = _valid_project(tmp_path)
    worker = root / "race_worker.py"
    worker.write_text(RACE_WORKER, encoding="utf-8")

    payloads = []
    for n in range(RACE_WRITERS):
        p = root / f"payload_{n}.json"
        p.write_bytes(
            json.dumps([accepted_item(f"Sappho{n}", f"Sapho{n}")], indent=2, ensure_ascii=False).encode("utf-8")
        )
        payloads.append(p)
    assert len({p.read_bytes() for p in payloads}) == RACE_WRITERS, (
        "every racer must carry DIFFERENT bytes, or the race proves nothing"
    )

    materialised = 0
    degenerate = 0
    for attempt in range(RACE_ATTEMPT_BUDGET):
        if materialised >= RACE_SAMPLES:
            break
        # A fresh target per attempt: a retry must race to CREATE the snapshot,
        # not find a previous attempt's already published there.
        approved = root / f"approved_{attempt}_attempt_0.json"
        barrier = root / f"barrier_{attempt}"
        barrier.mkdir()

        procs = [
            subprocess.Popen(
                [
                    sys.executable, str(worker), str(root / "scripts"), str(approved),
                    str(p), str(barrier), str(n), str(RACE_WRITERS),
                    str(BARRIER_TIMEOUT_SECONDS),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for n, p in enumerate(payloads)
        ]
        try:
            raw_results = [proc.communicate(timeout=BARRIER_TIMEOUT_SECONDS * 2) for proc in procs]
        finally:
            # Never leave workers behind if the barrier deadlocked -- and REAP
            # them: kill() only signals, so without the wait() the children stay
            # zombies until the whole pytest run exits.
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=30)

        reports = []
        for out, err in raw_results:
            assert out, f"attempt {attempt}: a racer produced no report (stderr: {err})"
            reports.append(json.loads(out))
        stalled = [r["id"] for r in reports if r["outcome"] == "BARRIER_TIMEOUT"]
        assert not stalled, (
            f"attempt {attempt}: {len(stalled)} racer(s) never saw their siblings at "
            f"the barrier: {stalled}"
        )

        # The invariant itself -- checked on EVERY attempt, overlapping or not.
        by_id = {int(r["id"]): r for r in reports}
        winners = [i for i, r in by_id.items() if r["outcome"] == "OK"]
        assert len(winners) == 1, (
            f"attempt {attempt}: {len(winners)} of {RACE_WRITERS} concurrent writers "
            f"published DIFFERENT bytes to the same approved path. Exactly one may "
            f"win -- with more, every reviewer that audited a losing fragment still "
            f"reported CITATIONS_OK while the merge consumes somebody else's bytes. "
            f"winners={winners}"
        )
        assert approved.read_bytes() == payloads[winners[0]].read_bytes(), (
            f"attempt {attempt}: the published snapshot is not the bytes of the writer "
            f"that reported success -- a loser's write landed anyway"
        )
        leftovers = list(approved.parent.glob(f".{approved.name}.tmp.*"))
        assert leftovers == [], f"attempt {attempt}: the race left temp files behind: {leftovers}"

        if _overlapping_pair(reports) is not None:
            materialised += 1
        else:
            degenerate += 1

    assert materialised >= RACE_SAMPLES, (
        f"the concurrent race never materialised: only {materialised} of "
        f"{RACE_ATTEMPT_BUDGET} attempts had two writers inside the critical section "
        f"at once ({degenerate} degenerated into sequential writes). The invariant "
        f"above is not evidence without that overlap -- a strictly sequential run "
        f"passes against a vulnerable check-then-act guard too."
    )


# ---------------------------------------------------------------------------
# Refused in every mode that is not --check-batch.
# ---------------------------------------------------------------------------

# Asserting only `"--approve-to" in stderr` here would prove nothing: argparse's
# OWN "unrecognized arguments: --approve-to" also exits 2 and also contains that
# substring, so such a test passes unchanged on pre-feature code that never
# defined the flag at all. Every assertion below therefore pins the DISTINCTIVE
# refusal phrase main() emits ("<flag> does not accept --approve-to"), which
# argparse can never produce, and names the mode that must own the refusal.
def test_approve_to_refused_in_validate_only(tmp_path):
    root = _valid_project(tmp_path)
    # validate-only = no mode flag; a canon.json exists so validation runs.
    proc = run_canon_validate(root, "--approve-to", str(root / "x.json"))
    assert proc.returncode == 2, proc.stdout
    assert "validate-only (no mode flag) does not accept --approve-to" in proc.stderr, (
        f"expected validate-only's own refusal phrase, got:\n{proc.stderr}"
    )
    assert not (root / "x.json").exists()


@pytest.mark.parametrize(
    "mode_args,refusing_flag",
    [
        (["--init"], "--init"),
        (["--restamp-derivation"], "--restamp-derivation"),
        (["--merge-batches", "frag.json"], "--merge-batches"),
        (["--verify-merged", "--batch", "frag.json"], "--verify-merged"),
        # The legacy bare-`--batch` merge -- no flag selects it, so it is the
        # row that historically escaped every table-driven guard. It carries an
        # approve_to_refusal like every other merge mode, so it belongs here.
        (["--batch", "frag.json"], "--batch (legacy single-fragment merge)"),
    ],
    ids=["init", "restamp", "merge_batches", "verify_merged", "legacy_bare_batch"],
)
def test_approve_to_refused_in_other_modes(tmp_path, mode_args, refusing_flag):
    root = _valid_project(tmp_path)
    proc = run_canon_validate(root, *mode_args, "--approve-to", str(root / "x.json"))
    assert proc.returncode == 2, f"expected refusal, got:\n{proc.stdout}\n{proc.stderr}"
    assert f"{refusing_flag} does not accept --approve-to" in proc.stderr, (
        f"expected {refusing_flag}'s own MODE_SPECS refusal, got:\n{proc.stderr}"
    )
    assert not (root / "x.json").exists()
