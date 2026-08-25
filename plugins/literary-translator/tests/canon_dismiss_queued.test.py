"""tests/canon_dismiss_queued.test.py -- coverage for issue #653:
disposition:"dismiss", the third canon_validate.py --correct disposition.

`review_queue[]` could only ever be drained by ACCEPTING a candidate (a
`--merge-batches` item freezing it into `entries{}`). A human who looked at
a queued candidate and judged it deliberately NOT canon-worthy -- a common
noun the detector mis-flagged, a duplicate spelling already covered under a
different queued form, a name with zero real occurrences -- had no
sanctioned way to say so: the row just sat in `review_queue[]` forever, with
no record that anyone ever looked at it. `dismiss` is the record: it drops
the row and states, in `corrections[]`, what was decided and why -- the
document names the row and a free-text `reason`, with no actor-identity
field anywhere in canon-correction.schema.json -- entries{} is never
touched.

WHY `old_item` MATTERS as its own field, not a `dismiss` disposition on the
existing `old_entry`: a queue row is not shaped like an entries{} record, and
`review_queue[]` can carry a bare STRING row (the legacy shape -- `_load_canon`
type-checks the array, never its items). `old_item` is UNCONSTRAINED in the
schema, symmetric with `old_entry` and for the same reason (#653 code review
reversed an earlier schema-level shape constraint here -- see
canon-correction.schema.json's own description for why: it was strictly
weaker than the runtime check and produced a FALSE error message on
rejection). The ACTUAL check is `_attributable_to` in canon_validate.py's
`run_correct`, admitting exactly two shapes (a mapping whose own
`source_form` equals the document's, or a bare string equal to it) and
refusing everything else at RUNTIME, before any search, so a document naming
the wrong source_form can never be matched against some other name's row --
see D2 in the issue's plan for the concrete failure this closes (a
`source_form:"Vertus"` document whose `old_item` is actually `"Pilou"`'s row
would otherwise drop Pilou's row while recording a fabricated decision about
Vertus).

WHAT THE BARE-STRING SHAPE ACTUALLY BUYS -- narrower than it first looks. It
lets a queue whose LAST remaining malformed row is a bare string be drained;
it does NOT let a queue holding SEVERAL such rows be drained one at a time.
`_stamp_write_verify` Pass-2-validates the WHOLE post-dismissal document
before writing, and canon-file.schema.json types every review_queue[] item
as the queued OBJECT shape, so two or more bare-string rows in one queue is a
CORRUPT FILE the write path refuses regardless of which row is being
dismissed -- the same boundary #495 already drew for entries{}
(`tests/canon_correct_entry.test.py::test_more_than_one_malformed_row_blocks_every_writing_mode_alike`),
pinned here for review_queue[] by
`test_two_malformed_queue_rows_block_dismissal_of_either` and
`test_dismiss_of_the_last_malformed_queue_row_still_succeeds` below. An
earlier revision of this docstring claimed the shape exists to drain the 61
bare-string rows measured live for #653's own plan -- that is false: those 61
sit in a canon.json that already fails whole-file validation for unrelated
legacy reasons and cannot be written by ANY mode today, dismiss included.

Drives the REAL, on-disk `canon_validate.py --correct` as a subprocess, via
`_canon_project_fixture`'s shared durable-root builder (the same fixture
`canon_correct_entry.test.py` uses) -- never a hand-rolled reimplementation
of the write path. One test also drives the real `glossary_batch_plan.py`
against the canon.json `--correct` just wrote, to prove the exclusion a
dismissed row represented in `review_queue[]` is not simply lost once the
row is gone (#653's issue explicitly forbids a dismissal silently
re-opening the name to automated re-research) -- that half of the exclusion
(the DISMISSED-set lookup inside `glossary_batch_plan.py` itself) is a
sibling teammate's file; see that test's own docstring for what it proves
independent of whether that half has landed yet.

Covered (see the issue's plan, section 5 "Acceptance", and D8):
  1. The four acceptance criteria in one test: a queued row is dropped with
     a recorded reason; entries{} is byte-identical (both a value-equality
     AND a canonical-serialization-byte-equality assertion, so the claim is
     literal, not merely "no visible diff"); `--merge-batches` still
     supersedes a queued row on accept (a light regression, unchanged by
     this issue); `canon_adjudication_audit.py --check` no longer
     enumerates the dismissed row.
  2. The legacy bare-string queue row -- the case `_attributable_to`'s
     bare-string branch exists to serve, and the exact fixture D2's own
     "removing the one bad row is what makes the file valid enough to
     write" argument describes.
  3. The attribution interlock refusing BEFORE any search (D2's concrete
     Pilou/Vertus failure mode) and the value interlock refusing when
     `old_item` is attributable but matches no row currently queued under
     that source_form -- both name both values, and both leave canon.json
     byte-untouched.
  4. The overlap repair: a source_form that is simultaneously a bare-string
     review_queue[] row AND an entries{} key is dismissible, and the
     entries{} record is left completely untouched -- the state
     `_assert_no_entries_review_queue_overlap` forbids for anything merged
     through `_merge_batch`, and dismissing the queue row is its repair.
  5. Two rows queued for one form under two different reasons (ordinary --
     `_merge_batch` appends whenever the whole object differs) take two
     separate dismissals; dismissing one leaves the other's row (and its
     exclusion) in place.
  6. Whole-value equality drops every row equal to old_item, including a
     byte-identical duplicate a hand edit -- never `_merge_batch` -- could
     produce.
  7. The full schema matrix: `dismiss` forbids old_entry/new_entry and
     requires old_item; `correct`/`remove` now forbid the new old_item
     field, so this schema change cannot silently widen either of them.
  8. Two already-shipped disposition shapes (`correct`, `remove`) still
     validate and still work end to end after old_entry left the
     unconditional `required` list.
  9. The review_queue[] twin of #495's own malformed-row boundary: TWO
     bare-string rows in one queue is a corrupt file the shared write path
     refuses regardless of which row is being dismissed (naming the OTHER
     row, and leaving canon.json byte-untouched), while a queue down to
     exactly ONE such row -- its last malformed row -- still dismisses
     cleanly.
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
from _canon_project_fixture import (  # noqa: E402
    SCRIPTS_SRC,
    accepted_item,
    make_project,
    queued_item,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    write_fragment,
)
from _senses_fixture import stage_consumer  # noqa: E402


def _entry(source_form: str, target_form: str, **overrides) -> dict:
    entry = {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": target_form,
        "basis": "transliterated",
        "confidence": "high",
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def seed_canon(root: Path, review_queue=None, entries=None) -> dict:
    """Hand-edits the fixture's canon.json (already `--init`-bootstrapped, so
    it carries a genuine cache_key.py-computed stamp) to hold exactly the
    review_queue[]/entries{} content a test needs -- including shapes
    `_merge_batch` itself could never produce (a bare-string row, or a form
    that is both a queued row and an entries{} key), matching the same
    read-mutate-write convention `canon_correct_entry.test.py` uses for its
    own malformed-row fixtures."""
    canon = read_canon(root)
    if review_queue is not None:
        canon["review_queue"] = review_queue
    if entries is not None:
        canon["entries"] = entries
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    return canon


def write_correction(root: Path, doc, name="correction.json") -> Path:
    return write_fragment(root, doc, name=name)


def dismiss_doc(source_form: str, old_item, reason=None, **overrides) -> dict:
    doc = {
        "source_form": source_form,
        "disposition": "dismiss",
        "old_item": old_item,
        "reason": reason or f"{source_form!r} is a common noun the detector mis-flagged as a name",
    }
    doc.update(overrides)
    return doc


def run_correct(root: Path, correction_path: Path, research_mode="offline"):
    # allow_durable_sibling=False: --correct is a NON-stamping mode (every
    # disposition, dismiss included, carries the stamp forward verbatim), so
    # it resolves no sibling cache_key.py and must not need either #412 flag.
    return run_canon_validate(
        root,
        "--correct",
        str(correction_path),
        research_mode=research_mode,
        allow_durable_sibling=False,
    )


def canon_bytes(root: Path) -> bytes:
    return (root / "canon.json").read_bytes()


def payload_of(proc) -> dict:
    return json.loads(proc.stdout)


def entries_span_bytes(raw: bytes) -> bytes:
    """The LITERAL bytes of the top-level "entries" value inside a raw,
    already-on-disk canon.json document -- located with the real
    `json.JSONDecoder` (its `raw_decode`, to find the exact end offset,
    never a brace-counting regex, which would mis-handle a value containing
    a literal `{`/`}` inside a quoted string), so a comparison between two
    calls proves ACTUAL ON-DISK BYTE identity, not merely that the PARSED
    values are `==`.

    A prior version of this helper re-serialized `canon["entries"]` (the
    PARSED dict) with `_atomic_write_json`'s own settings and compared
    those re-serializations -- which is deterministic given equal parsed
    dicts and so can never actually fail once the (separately asserted)
    value-equality check already passed: it is a second spelling of the
    same comparison, not a byte assertion (code review, #653). It also hid
    a genuine format difference this suite's callers must avoid: seeding a
    fixture through `seed_canon`'s hand write (compact `json.dumps`, no
    indent/sort_keys) leaves canon.json in a DIFFERENT literal format than
    the canonical one `_atomic_write_json` writes, so a "before" snapshot
    taken from a hand-written file is not a fair byte comparison against an
    "after" snapshot the real writer produced -- callers of this helper
    must seed through the REAL merge/correct path first.

    SHADOW GUARD (code review, #653): `text.index(key)` finds the FIRST
    textual occurrence of `"entries":` in the whole document. Under
    canonical key order, top-level "corrections" sorts BEFORE top-level
    "entries" -- and a corrections[] record's `old_entry` is an
    UNCONSTRAINED JSON value (see canon-correction.schema.json), so it
    could in principle carry its own nested `{"entries": ...}` key that
    appears earlier in the text than the real top-level one. That would
    silently extract the wrong span and still return SOME bytes, which
    could coincidentally compare equal on a before/after diff and pass for
    the wrong reason. The current fixtures never nest that key, so nothing
    is vacuous today, but a helper whose failure mode is "measured the
    wrong bytes and still passed" is not one to leave standing -- assert
    below that the decoded span is genuinely the document's own top-level
    entries{}, so a shadow match is a loud failure instead of a quiet
    one."""
    text = raw.decode("utf-8")
    key = '"entries":'
    idx = text.index(key)
    start = idx + len(key)
    while text[start] == " ":
        start += 1
    _, end = json.JSONDecoder().raw_decode(text, start)
    span = text[start:end]
    assert json.loads(span) == json.loads(raw)["entries"], (
        "entries_span_bytes located the WRONG \"entries\" occurrence -- "
        "shadowed by a nested key earlier in the document, most likely "
        "inside a corrections[] record's unconstrained old_entry"
    )
    return span.encode("utf-8")


def assert_refused(proc, root: Path, before: bytes, *needles):
    """Every refusal in this mode has the same three obligations: exit 1, a
    JSON failure payload naming what it saw, and a canon.json that did not
    move a single byte. Mirrors canon_correct_entry.test.py's own helper."""
    assert proc.returncode == 1, (
        f"expected a refusal (exit 1), got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    )
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    for needle in needles:
        assert needle in payload["error"], (
            f"refusal does not name {needle!r}: {payload['error']!r}"
        )
    assert canon_bytes(root) == before, "a refused dismissal still modified canon.json"


CANON_ADJUDICATION_EXTRA_DEPS = ("occ_index.py", "evidence_verify.py")


def stage_adjudication_audit(root: Path) -> None:
    """canon_adjudication_audit.py is a canon_senses CONSUMER (it does
    `from canon_senses import ...`), so it is staged through the sanctioned
    `stage_consumer()` route -- NEVER a raw `shutil.copy2` -- or an isolated
    suite that copies it alone loses canon_senses.py alongside it and fails
    with ModuleNotFoundError before any of this suite's own assertions run
    (tests/senses_fixture_guard.test.py is the repo-wide drift guard for
    exactly this; CI caught an earlier raw-copy version of this function).
    `stage_consumer` is idempotent/overwrite-safe, so calling it again here
    -- after `make_project`'s own `stage_consumer(root, "canon_validate.py")`
    already ran -- is safe: it re-copies canon_senses.py/its schema
    (byte-identical) and additionally stages THIS script.
    `bootstrap_names.py` -- one of its three extra deps -- is already
    staged by `make_project`'s own STAGED_SCRIPTS, so only the other two
    need copying here (a plain shutil.copy2 is fine for these -- neither is
    a canon_senses consumer). See tests/audit_unchanged_regression.test.py's
    `make_durable_root` for the same recipe."""
    stage_consumer(root, "canon_adjudication_audit.py")
    scripts_dir = root / "scripts"
    for dep in CANON_ADJUDICATION_EXTRA_DEPS:
        dep_src = SCRIPTS_SRC / dep
        assert dep_src.is_file(), f"{dep} not found at {dep_src}"
        shutil.copy2(dep_src, scripts_dir / dep)


def run_audit(root: Path, *args, timeout=60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 1. The four acceptance criteria
# ---------------------------------------------------------------------------


def test_dismiss_meets_the_four_acceptance_criteria(tmp_path):
    """Acceptance criterion 1's "entries{} is byte-identical" is a claim
    about the WRITE PATH's existing behaviour, not a new guarantee this
    feature builds: `entries{}` is unchanged as a JSON VALUE always (the
    dismiss branch never reads or writes it at all), and byte-identical on
    disk whenever the file was ALREADY canonically serialized -- which
    every tool-written canon.json is, because `_atomic_write_json`
    reserializes the WHOLE document (indent=2, sort_keys=True) on every
    write, for every mode, and that is pre-existing, not introduced here.
    A hand-compacted legacy file is reserialized wholesale by that same
    shared writer the moment any mode touches it -- exactly what dismiss
    exists to be usable on -- so there is deliberately no preflight gate
    here comparing the would-be serialized span against the current raw
    span and refusing on a mismatch: that would reject precisely the
    hand-compacted files this feature exists to repair (code review,
    #653). This test proves the claim is real by seeding through the REAL
    merge path first, so "before" is already canonical."""
    root = make_project(tmp_path)
    init = run_canon_init(root)
    assert init.returncode == 0, f"--init failed:\n{init.stdout}\n{init.stderr}"

    # A frozen entry, merged through the REAL path, so entries{} is
    # non-trivial and canonically serialized -- exactly the state the
    # byte-identical assertions below need to be a real claim.
    fragment = write_fragment(root, [accepted_item("Cosette", "Cosette")])
    merged = run_canon_validate(root, "--merge-batches", str(fragment))
    assert merged.returncode == 0, f"--merge-batches failed:\n{merged.stdout}\n{merged.stderr}"

    row = queued_item("Fantomas", note="detector false positive -- a common noun")
    # Seeded through the REAL --merge-batches path, NOT seed_canon's hand
    # write (compact json.dumps, no indent/sort_keys) -- the "before" bytes
    # captured below must already be in the canonical format
    # _atomic_write_json produces, or a literal on-disk byte comparison
    # against the "after" write would trip on a FORMAT difference that has
    # nothing to do with entries{} content (code review, #653).
    queue_fragment = write_fragment(root, [row], name="queue.json")
    queued_proc = run_canon_validate(root, "--merge-batches", str(queue_fragment))
    assert queued_proc.returncode == 0, f"{queued_proc.stdout}\n{queued_proc.stderr}"

    before = read_canon(root)
    before_stamp = before["generation_hashes"]
    before_entries_bytes = entries_span_bytes(canon_bytes(root))

    doc = dismiss_doc("Fantomas", row, reason="not a name -- 'fantomas' means 'ghost' here")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct dismiss failed:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is True
    assert payload["disposition"] == "dismiss"
    assert payload["rows_dropped"] == 1
    assert payload["review_queue_count"] == 0
    assert payload["corrections_count"] == 1
    # A dismissal carries the stamp forward verbatim, exactly like
    # correct/remove -- never advances the derivation-bundle provenance
    # claim (#291), since it regenerates nothing.
    assert payload["generation_hashes_restamped"] is False

    after = read_canon(root)

    # (1) Row dropped with a recorded reason.
    assert after["review_queue"] == []
    assert after["corrections"] == [doc]

    # (1, continued) entries{} byte-identical -- value equality AND the
    # LITERAL on-disk bytes of the "entries" span, read back off the file
    # dismiss just wrote (not reconstructed from the parsed value).
    assert after["entries"] == before["entries"]
    assert entries_span_bytes(canon_bytes(root)) == before_entries_bytes

    # Stamp preservation.
    assert after["generation_hashes"] == before_stamp

    # Whole-file re-validation.
    healthy = run_canon_validate(root)
    assert healthy.returncode == 0, f"canon.json fails validate-only after dismiss:\n{healthy.stdout}"

    # (3) --merge-batches still supersedes a queued row on accept, unchanged
    # by this issue -- a light regression, not #653's own subject.
    seed_canon(root, review_queue=[queued_item("Cosette", note="disputed spelling")])
    accept_fragment = write_fragment(
        root, [accepted_item("Cosette", "Cosette")], name="accept.json"
    )
    accepted = run_canon_validate(root, "--merge-batches", str(accept_fragment))
    assert accepted.returncode == 0, accepted.stdout
    assert queued_item("Cosette", note="disputed spelling") not in read_canon(root)["review_queue"]

    # (4) canon_adjudication_audit.py --check no longer enumerates the row,
    # with no review_queue_risk_overrides entry needed at all.
    stage_adjudication_audit(root)
    seed_canon(root, review_queue=[queued_item("Thenardier")])
    before_audit = run_audit(root, "--check")
    assert before_audit.returncode != 0, before_audit.stdout  # a real, un-adjudicated finding
    before_totals = json.loads(before_audit.stdout)["totals"]
    assert before_totals["review_queue_items"] == 1, before_totals

    audit_doc = dismiss_doc(
        "Thenardier", queued_item("Thenardier"), reason="already covered under 'Thenardiers'"
    )
    dismissed = run_correct(root, write_correction(root, audit_doc, name="audit_dismiss.json"))
    assert dismissed.returncode == 0, f"{dismissed.stdout}\n{dismissed.stderr}"

    after_audit = run_audit(root, "--check")
    after_totals = json.loads(after_audit.stdout)["totals"]
    assert after_totals["review_queue_items"] == 0, after_totals
    assert after_totals["review_queue_unaccepted"] == 0, after_totals


# ---------------------------------------------------------------------------
# 2. The legacy bare-string queue row
# ---------------------------------------------------------------------------


def test_dismiss_drops_a_bare_string_queue_row(tmp_path):
    """The case `_attributable_to`'s bare-string branch exists to serve
    (D2): a review_queue[] row that is a plain string, not the QUEUED
    object shape -- `_load_canon` type-checks the array itself, never its
    items, so a hand-edited or otherwise not-merged-through-`_merge_batch`
    canon.json can hold one. `old_item` is schema-UNCONSTRAINED (#653 code
    review reversed an earlier shape constraint there), so this row's own
    correction document reaches the runtime equality interlock rather than
    being refused by the schema first -- exactly the hole #495's own review
    already closed once for old_entry.

    This is the DRAINABLE shape of the boundary: exactly ONE bare-string
    row, which is also this queue's LAST remaining row. A queue holding
    several bare-string rows is a different, corrupt-file case --
    `test_two_malformed_queue_rows_block_dismissal_of_either` below."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0

    seed_canon(root, review_queue=["Pilou"])
    unhealthy = run_canon_validate(root)
    assert unhealthy.returncode == 1, (
        "the bare-string row does not fail validate-only, so this fixture is "
        "not the state old_item's unconstrained shape exists to serve"
    )

    doc = dismiss_doc("Pilou", "Pilou", reason="OCR artifact, not a real candidate")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"a bare-string queue row cannot be dismissed:\n{proc.stdout}\n{proc.stderr}"
    assert payload_of(proc)["rows_dropped"] == 1

    after = read_canon(root)
    assert after["review_queue"] == []
    assert after["corrections"] == [doc]
    assert run_canon_validate(root).returncode == 0, "canon.json is still unhealthy after the repair"


def test_two_malformed_queue_rows_block_dismissal_of_either(tmp_path):
    """The review_queue[] twin of #495's own boundary
    (`tests/canon_correct_entry.test.py::test_more_than_one_malformed_row_blocks_every_writing_mode_alike`):
    a CORRUPT FILE, not "one malformed row" (code review, #653, reproduced
    directly against the shipped script before this fix).

    `_stamp_write_verify` Pass-2-validates the WHOLE post-dismissal document
    before writing, and canon-file.schema.json types every review_queue[]
    item as the queued OBJECT shape. So with TWO bare-string rows, dismissing
    ONE still leaves a file whose OTHER row fails whole-file validation, and
    the write is refused -- not because the dismissal itself did anything
    wrong, but because Pass 2 is a property every writing mode shares, and
    relaxing it for one disposition would let a dismissal succeed while
    leaving a canon.json that still fails validation, which blocks every
    other mode anyway. This is the gate behaving correctly; a queue holding
    several malformed rows is file corruption, not one candidate's
    adjudication. The refusal message names the OTHER row -- the operator
    can see what is still blocking the write."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=["Alpha", "Beta"])
    before = canon_bytes(root)

    doc = dismiss_doc("Alpha", "Alpha", reason="dismissing the first malformed row")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "Beta", "is not of type 'object'")


def test_dismiss_of_the_last_malformed_queue_row_still_succeeds(tmp_path):
    """The companion to the test above, so the boundary reads as a genuine
    boundary rather than a dead end: with the queue down to exactly ONE
    remaining malformed row, dismissing it succeeds -- the SAME `Alpha`/
    `Beta` fixture, minus `Beta`, driven through the SAME dismiss call."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=["Alpha"])

    doc = dismiss_doc("Alpha", "Alpha", reason="dismissing the last malformed row")
    proc = run_correct(root, write_correction(root, doc))

    assert proc.returncode == 0, f"the last malformed row cannot be dismissed:\n{proc.stdout}\n{proc.stderr}"
    assert read_canon(root)["review_queue"] == []
    assert run_canon_validate(root).returncode == 0


# ---------------------------------------------------------------------------
# 3. The attribution and value interlocks
# ---------------------------------------------------------------------------


def test_dismiss_refuses_old_item_not_attributable_to_its_own_source_form(tmp_path):
    """D2's concrete failure mode: review_queue holds "Pilou" (a bare
    string); the document claims source_form "Vertus" but states old_item
    "Pilou". Without the attribution check FIRST, whole-value equality would
    match "Pilou"'s row, drop it, and record a fabricated decision about
    "Vertus" -- a name nobody adjudicated -- while Pilou silently returns to
    research. Refused before any search, naming both.

    "Naming both" is asserted as ONE exact, ORDERED fragment binding
    source_form to old_item (`"'Vertus': stated old_item 'Pilou'"`), not as
    two independent substring checks -- five unordered needles would still
    pass a message that named "Vertus" and "Pilou" in the WRONG pairing, or
    named each beside an unrelated label (code review, #653)."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=["Pilou"])
    before = canon_bytes(root)

    source_form, old_item = "Vertus", "Pilou"
    doc = dismiss_doc(source_form, old_item, reason="mistaken correction attempt")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(
        proc,
        root,
        before,
        f"{source_form!r}: stated old_item {old_item!r} is not attributable",
    )


def test_dismiss_refuses_old_item_that_matches_no_currently_queued_row(tmp_path):
    """old_item IS attributable to source_form (same value, so trivially
    self-consistent) but the row it names is not what is actually queued --
    a stale read. Refused naming both the stated value and what is
    currently queued under that source_form, same as old_entry's own
    blind-use interlock.

    "Naming both" is asserted as ONE exact, ORDERED fragment binding each
    LABEL to its OWN row -- `"Stated: {stale_row!r}. Currently queued under
    'Pilou': {on_disk_row!r}"` -- not five independent, unordered substring
    checks. Five unordered needles ("Stated:", the stale note, "Currently
    queued under", the on-disk note) would still pass a message like
    "Stated: <on-disk row>. Currently queued under: <on-disk row>. Debug:
    <stale note>" that actually names only ONE side twice and buries the
    other under an unrelated label (code review, #653) -- binding both
    labels to both values in one contiguous string is what actually proves
    the operator can see what it read versus what is really there."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    source_form = "Pilou"
    on_disk_row = queued_item(source_form, note="original note")
    seed_canon(root, review_queue=[on_disk_row])
    before = canon_bytes(root)

    stale_row = queued_item(source_form, note="a note this row never actually carried")
    doc = dismiss_doc(source_form, stale_row, reason="stale read")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(
        proc,
        root,
        before,
        f"Stated: {stale_row!r}. Currently queued under {source_form!r}: {on_disk_row!r}",
    )


@pytest.mark.parametrize(
    "wrong_shape",
    [["Pilou"], 17, None, {"note": "no source_form field at all"}],
    ids=["list", "int", "null", "dict_without_source_form"],
)
def test_dismiss_refuses_a_wrong_shape_old_item_at_runtime(tmp_path, wrong_shape):
    """`old_item` is UNCONSTRAINED in canon-correction.schema.json (#653 code
    review reversed an earlier `oneOf` there -- it was strictly weaker than
    `_attributable_to`, which refuses every shape it refused and more, AND
    it produced a FALSE error message: jsonschema's oneOf formatter is
    written for canon-batch's disposition-discriminated union, and neither
    old_item branch carried a disposition const, so the reported reason
    claimed the disposition was absent/unrecognized when it was actually
    present and correct). So a wrong-shape old_item must clear schema
    validation and be refused by the RUNTIME attribution check instead,
    `_attributable_to` returning False for anything that is not a dict or a
    str -- naming both values, exactly like the shape-VALID attribution
    mismatch above. This case was untested in either file before this fix,
    which is part of why the bad schema message went unnoticed."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=[queued_item("Pilou")])
    before = canon_bytes(root)

    doc = dismiss_doc("Pilou", wrong_shape, reason="malformed old_item")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(
        proc,
        root,
        before,
        f"'Pilou': stated old_item {wrong_shape!r} is not attributable",
    )


# ---------------------------------------------------------------------------
# 4. The entries{}/review_queue[] overlap repair
# ---------------------------------------------------------------------------


def test_dismiss_of_form_also_present_in_entries_leaves_the_entry_untouched(tmp_path):
    """The overlap `_assert_no_entries_review_queue_overlap` forbids for
    anything merged through `_merge_batch` -- but that check only sees
    isinstance(item, dict) rows, so a BARE-STRING queue row sharing its
    source_form with an entries{} key is invisible to it, and measured
    (#653's plan) to be a real shape: every one of the 61 bare-string rows in
    the one real corpus that has them is ALSO an entries{} key. Dismissing
    the queue row is the repair; removing the ENTRY is a different decision
    (disposition:"remove"). No entries{}-key refusal exists for dismiss
    precisely so this case is reachable.

    That real corpus's canon.json is NOT what this fixture reproduces,
    though -- it already fails whole-file validation for unrelated legacy
    reasons and cannot be written by ANY mode today, dismiss included (61
    bare-string rows in one queue, not one). This fixture isolates the
    OVERLAP shape alone -- one bare-string row that is also an entries{}
    key, in an otherwise-healthy file -- the reachable, narrower case."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    frozen = _entry("Vertus", "Vertus")
    seed_canon(root, review_queue=["Vertus"], entries={"Vertus": frozen})

    doc = dismiss_doc("Vertus", "Vertus", reason="already frozen; the queue row is a stale duplicate")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    after = read_canon(root)
    assert after["review_queue"] == []
    assert after["entries"] == {"Vertus": frozen}, "dismissing the queue row touched the frozen entry"
    assert run_canon_validate(root).returncode == 0


# ---------------------------------------------------------------------------
# 5. Two rows queued for one form
# ---------------------------------------------------------------------------


def test_dismiss_one_of_two_rows_queued_for_the_same_form_leaves_the_other(tmp_path):
    """`_merge_batch` appends whenever the whole object differs, so one
    form queued by two batches for two different reasons is TWO rows, not
    one -- ordinary, not a hand-edit artifact (person_registry.py:899-910
    documents and coalesces exactly this). Matching on the whole value
    means dismissing one reason must not silently dismiss the other; two
    decisions take two documents."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    row_a = queued_item("Gavroche", note="possibly a nickname, not a proper name")
    row_b = queued_item("Gavroche", note="possibly a place name in the same passage")
    seed_canon(root, review_queue=[row_a, row_b])

    doc = dismiss_doc("Gavroche", row_a, reason="confirmed nickname reading is wrong; still queued for the place-name reason")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert payload_of(proc)["rows_dropped"] == 1

    after_first = read_canon(root)["review_queue"]
    assert after_first == [row_b], after_first

    doc2 = dismiss_doc("Gavroche", row_b, reason="confirmed not a place name either")
    proc2 = run_correct(root, write_correction(root, doc2, name="second.json"))
    assert proc2.returncode == 0, f"{proc2.stdout}\n{proc2.stderr}"
    assert read_canon(root)["review_queue"] == []
    assert read_canon(root)["corrections"] == [doc, doc2]


def test_dismiss_drops_every_byte_identical_duplicate_row(tmp_path):
    """Byte-identical duplicate rows are unreachable through `_merge_batch`
    (its append guard is `if item not in review_queue`) but reachable in a
    hand-edited file, and there they are indistinguishable from each other
    -- matching on the whole value and dropping every match is the only way
    to actually drain the queue rather than leaving one behind with nothing
    left to tell it apart from the one just dismissed."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    row = queued_item("Eponine")
    seed_canon(root, review_queue=[row, dict(row)])  # two independent dict objects, same value

    doc = dismiss_doc("Eponine", row, reason="not canon-worthy")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert payload_of(proc)["rows_dropped"] == 2
    assert read_canon(root)["review_queue"] == []


# ---------------------------------------------------------------------------
# 6. Schema matrix
# ---------------------------------------------------------------------------


def test_dismiss_document_requires_old_item(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=[queued_item("Azelma")])
    before = canon_bytes(root)

    doc = {"source_form": "Azelma", "disposition": "dismiss", "reason": "not canon-worthy"}
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "schema validation")


def test_dismiss_document_forbids_old_entry(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=[queued_item("Azelma")])
    before = canon_bytes(root)

    doc = dismiss_doc("Azelma", queued_item("Azelma"))
    doc["old_entry"] = _entry("Azelma", "Azelma")
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "schema validation")


def test_dismiss_document_forbids_new_entry(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    seed_canon(root, review_queue=[queued_item("Azelma")])
    before = canon_bytes(root)

    doc = dismiss_doc("Azelma", queued_item("Azelma"))
    doc["new_entry"] = _entry("Azelma", "Azelma")
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "schema validation")


def test_correct_document_forbids_old_item(tmp_path):
    """The reverse direction: this schema change must not widen `correct` to
    accept the new field either."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    fragment = write_fragment(root, [accepted_item("Marius", "Marius")])
    assert run_canon_validate(root, "--merge-batches", str(fragment)).returncode == 0
    before = canon_bytes(root)

    doc = {
        "source_form": "Marius",
        "disposition": "correct",
        "old_entry": _entry("Marius", "Marius"),
        "new_entry": _entry("Marius", "Marius", note="fixed"),
        "old_item": "Marius",
        "reason": "should not validate",
    }
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "schema validation")


def test_remove_document_forbids_old_item(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    fragment = write_fragment(root, [accepted_item("Enjolras", "Enjolras")])
    assert run_canon_validate(root, "--merge-batches", str(fragment)).returncode == 0
    before = canon_bytes(root)

    doc = {
        "source_form": "Enjolras",
        "disposition": "remove",
        "old_entry": _entry("Enjolras", "Enjolras"),
        "old_item": "Enjolras",
        "reason": "should not validate",
    }
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "schema validation")


# ---------------------------------------------------------------------------
# 7. correct/remove still validate and still work after old_entry left the
#    unconditional required list
# ---------------------------------------------------------------------------


def test_correct_and_remove_still_work_after_the_schema_change(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    fragment = write_fragment(
        root, [accepted_item("Javert", "Javert"), accepted_item("Fauchelevent", "Fauchelevent")]
    )
    assert run_canon_validate(root, "--merge-batches", str(fragment)).returncode == 0

    correct_doc = {
        "source_form": "Javert",
        "disposition": "correct",
        "old_entry": _entry("Javert", "Javert"),
        "new_entry": _entry("Javert", "Javert", confidence="medium"),
        "reason": "confidence downgraded on review",
    }
    corrected = run_correct(root, write_correction(root, correct_doc))
    assert corrected.returncode == 0, f"{corrected.stdout}\n{corrected.stderr}"
    assert read_canon(root)["entries"]["Javert"]["confidence"] == "medium"

    remove_doc = {
        "source_form": "Fauchelevent",
        "disposition": "remove",
        "old_entry": _entry("Fauchelevent", "Fauchelevent"),
        "reason": "interpolated name, zero real occurrences",
    }
    removed = run_correct(root, write_correction(root, remove_doc, name="remove.json"))
    assert removed.returncode == 0, f"{removed.stdout}\n{removed.stderr}"
    assert "Fauchelevent" not in read_canon(root)["entries"]


# ---------------------------------------------------------------------------
# 8. Producer/consumer integration -- canon_validate.py --correct dismiss,
#    then the REAL glossary_batch_plan.py against the canon.json it wrote.
#    This is the one seam this file owns end to end; glossary_batch_plan.py
#    itself (the dismissed-set lookup this test's second assertion needs) is
#    a sibling teammate's file. If that half has not landed yet, the
#    exclusion assertion below fails -- that is expected, not a bug in this
#    test, and is called out in the dispatch report.
# ---------------------------------------------------------------------------


def _candidate_row(name: str, freq: int = 5) -> dict:
    words = name.split()
    return {
        "name": name,
        "freq": freq,
        "mid_sentence": 1,
        "multiword": len(words) > 1,
        "abbrev": len(words) == 1 and len(words[0]) == 1,
        "n_segments": 1,
        "likely_name": True,
    }


def test_dismissed_name_excluded_from_glossary_batch_plan(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0

    row = queued_item("Bamatabois", note="candidate under dispute")
    seed_canon(root, review_queue=[row])

    doc = dismiss_doc("Bamatabois", row, reason="a common word, not a proper name")
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert read_canon(root)["review_queue"] == []

    # The name is still IN THE TEXT (a re-extraction after the dismissal
    # would surface it again, exactly like a still-queued name would --
    # #653's issue explicitly forbids automated re-research reopening it).
    name_candidates_path = root / "name_candidates.json"
    candidates = [_candidate_row("Bamatabois"), _candidate_row("Marius")]
    name_candidates_path.write_text(
        json.dumps(
            {
                "n_candidates": len(candidates),
                "n_strong": sum(1 for c in candidates if c["likely_name"]),
                "candidates": candidates,
            }
        ),
        encoding="utf-8",
    )

    proc = run_script(
        root, "glossary_batch_plan.py", "--name-candidates", str(name_candidates_path)
    )
    assert proc.returncode == 0, f"glossary_batch_plan.py failed:\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout)
    dispatched_names = {
        cand_row["name"] for batch in result["args"] for cand_row in batch["candidates"]
    }
    assert "Marius" in dispatched_names  # control: proves the exclusion, not a drop of everything
    assert "Bamatabois" not in dispatched_names, (
        "a dismissed name was re-dispatched for research -- #653 forbids a dismissal "
        "silently re-opening the name it recorded a decision about"
    )
