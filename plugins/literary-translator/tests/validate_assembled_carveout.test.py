"""tests/validate_assembled_carveout.test.py -- #491 code-simplifier
finding (MAJOR, independently verified against source by the team lead):
`validate_assembled.py` is a HARD structural-completeness gate that runs
AFTER `final_audit.py`/`assemble.py` and BEFORE W8 Deliver (SKILL.md,
"Structural-completeness gate (scripts/validate_assembled.py, #202)"), but
was never updated for #491's machinery-only stale carve-out. Before the
fix this file pins:

  - `collect_reviewed_draft_rebind()` selected its population with a bare
    `if status != "converged": continue`, so a carved-out `stale` record
    never reached `_rebind_or_flag_stale()` and never entered
    `trusted_drafts`.
  - `collect_source_markers()` builds `source_counter` from the FULL
    manifest, independent of ledger status -- so a carved-out segment's
    own declared heading keys stayed part of the REQUIRED population.
  - The result: a book #491 exists to unblock would go hard RED at THIS
    earlier gate for every carved-out segment owning a declared heading
    block, and a carved-out segment owning NO heading block would
    silently lose its reviewed-SHA rebind altogether (never checked at
    all, not even the weaker check it had before #491).

The fix widens `collect_reviewed_draft_rebind()`'s population to also
accept a `stale` record that qualifies for the carve-out -- see
`_stale_qualifies_for_carveout()`'s own docstring in
`scripts/validate_assembled.py` for the exact three conditions restated
from `assemble.py`'s own predicate, and the DELIBERATE ASYMMETRY (the
`.ever_converged` sentinel condition is intentionally NOT restated here).
#491 R2 (round-2 review) corrected that docstring's own rationale for the
asymmetry: it is NOT because assemble.py always re-checks the sentinel
downstream (false in this gate's own default `segment_drafts_and_audit`
scope, which runs before any assembly decision may even exist) but
because (1) this gate has never checked the sentinel for ANY record,
carved out or not -- a plain `converged` record was never sentinel-gated
here either -- and (2) `final_audit.py`'s own
`count_stale_previously_converged()` already enforces the sentinel on
this exact population, ahead of this gate. See
`_stale_qualifies_for_carveout()`'s own docstring for the full two-reason
argument.

This same round also scoped `collect_reviewed_draft_rebind()`'s NEW
stale-carve-out branch to segments the CURRENT manifest still requires
(a new `manifest_seg_ids` parameter) -- `runs/ledger.json` deliberately
RETAINS entries for segments the manifest no longer names (see
`ledger_merge.py`'s own module docstring), and the pre-#491-R2 widening
let such a retained, out-of-manifest entry reach `_rebind_or_flag_stale()`
and hard-fail an otherwise-deliverable CURRENT book. See section "7." in
this file for the cases pinning that fix.

## Fixture strategy

Mirrors tests/validate_assembled.test.py's own conventions exactly --
self-contained, duplicated here rather than imported (this suite's own
"each test file stays self-contained" convention, see e.g.
tests/stale_carveout.test.py's own module docstring) -- a real,
self-contained `durable_root` on disk, driving the ACTUAL
`validate_assembled.py` as a subprocess, in the default
`segment_drafts_and_audit` v1_scope (the only scope the ledger-driven
carve-out is relevant to). The only addition over
tests/validate_assembled.test.py's own `write_ledger()` (which only ever
writes `status=="converged"`) is a `write_ledger()` capable of a `stale`
record carrying `stale_mismatched_fields`.

The four-way drift assertion (case 6 / `test_four_way_drift_guard`) also
loads `final_audit.py`, `select_segments.py`, `assemble.py` and
`validate_assembled.py` itself IN PLACE (never copies -- read-only
accesses to a module-level constant) to compare each one's own
machinery-only field-set constant. This mirrors
tests/stale_carveout.test.py's own `load_real_*_module()` helpers and its
`test_three_copy_drift_guard_for_machinery_only_fields`, widened to FOUR
copies here because that existing three-way census lives in a file this
#491-follow-up change does not own (a concurrent teammate is editing
tests/stale_carveout.test.py in the same worktree) and cannot see
`validate_assembled.py`'s new copy.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/validate_assembled_carveout.test.py``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"

for _src in (
    VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC, FINAL_AUDIT_SRC,
    SELECT_SEGMENTS_SRC, ASSEMBLE_SRC,
):
    assert _src.is_file(), f"required source not found: {_src}"


# ---------------------------------------------------------------------------
# Fixture builders -- duplicated from tests/validate_assembled.test.py's own
# (house convention: each *.test.py file stays self-contained).
# ---------------------------------------------------------------------------


def make_root(tmp_path, v1_scope: str = "segment_drafts_and_audit") -> Path:
    """A bare durable_root: real copies of validate_assembled.py + its sole
    sibling import (validate_draft.py, for load_profile()), a minimal
    profile.yml + ownership marker."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
        (scripts_dir / src.name).write_bytes(src.read_bytes())
    # json_stdout.py (#369): every routed script above loads it by exact
    # path from its own directory and sys.exit()s if the sibling is absent.
    (scripts_dir / "json_stdout.py").write_bytes(
        (SCRIPTS_SRC_DIR / "json_stdout.py").read_bytes()
    )

    profile = {"output": {"v1_scope": v1_scope}}
    (root / "profile.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def make_block(raw_type: str, plain_text: str = "Source text.", order_index: int = 0) -> dict:
    return {"type": raw_type, "order_index": order_index, "plain_text": plain_text}


def write_manifest(root: Path, blocks: dict, segments: list, heading_types=None) -> None:
    full_blocks = {}
    for bid, b in blocks.items():
        full = dict(b)
        full.setdefault("id", bid)
        full.setdefault("source_file", "source.txt")
        full.setdefault("sha1", "0" * 40)
        full_blocks[bid] = full
    manifest = {
        "blocks": full_blocks,
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": segments,
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    if heading_types is not None:
        manifest["heading_types"] = heading_types
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def draft_content_sha1_of(doc: dict) -> str:
    """Ground-truth draft-content sha1, independently computed here (never
    imported from the script under test) -- dispatch_token excluded,
    sorted-key canonical JSON, matching validate_assembled.py's own
    draft_content_sha1() byte for byte."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_draft(root: Path, seg: str, blocks: dict) -> dict:
    draft = {"seg": seg, "blocks": blocks, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return draft


_UNSET = object()


def write_ledger(root: Path, entries: dict) -> None:
    """entries: seg -> {"status": "converged"|"stale", optionally
    "reviewed_draft_sha1" (a literal override) and, only meaningful for
    status=="stale", "stale_mismatched_fields"}. Widened from
    tests/validate_assembled.test.py's own write_ledger() (which only
    ever writes status=="converged") to also drive a materialized
    "stale" record shaped like #491's own ledger_merge.py output -- see
    tests/stale_carveout.test.py's stale_ledger_record() for the
    reference shape this mirrors.

    reviewed_draft_sha1, for BOTH statuses, auto-computes from whatever
    draft is CURRENTLY on disk unless a literal override is given -- so
    (matching the original write_ledger()'s own documented convention)
    calling this AFTER mutating a draft re-binds the ledger to the new
    bytes, and calling it BEFORE mutating leaves a stale (now-mismatched)
    recorded sha1 in place.

    stale_mismatched_fields is omitted from the record entirely unless a
    caller explicitly passes it (even `[]` is written verbatim if given)
    -- so the "key missing" malformed shape (the pre-#491 legacy ledger
    format) is just "don't pass it", never conflated with an explicit
    empty list."""
    segments = {}
    for seg, cfg in entries.items():
        status = cfg["status"]
        record = {"status": status}
        if "reviewed_draft_sha1" in cfg:
            record["reviewed_draft_sha1"] = cfg["reviewed_draft_sha1"]
        else:
            draft_path = root / "segments" / f"{seg}.draft.json"
            draft_doc = json.loads(draft_path.read_text(encoding="utf-8"))
            record["reviewed_draft_sha1"] = draft_content_sha1_of(draft_doc)
        if status == "stale" and "stale_mismatched_fields" in cfg:
            record["stale_mismatched_fields"] = cfg["stale_mismatched_fields"]
        segments[seg] = record
    (root / "runs" / "ledger.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")


def run_validate_assembled(root: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_assembled.py")],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout_json(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def _load_module_from_source(src_path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, src_path)
    assert spec is not None and spec.loader is not None, f"cannot load spec for {src_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# 1. THE DEFECT, reproduced: a carved-out stale segment owning a declared
#    heading block must not false-RED at this earlier gate.
# ===========================================================================


def test_carved_out_stale_segment_with_declared_heading_is_accepted(tmp_path):
    """Before the fix: `collect_reviewed_draft_rebind()`'s bare
    `if status != "converged": continue` never lets this segment reach
    `_rebind_or_flag_stale()`, so it never enters `trusted_drafts` --
    `collect_default_output_markers()` then credits 0 to
    (seg01, HEAD:seg01), and `compute_missing_heading_defects()` reports it
    `missing_heading` even though the reviewed draft is untouched and the
    only thing that moved is a machinery cache-key field. After the fix:
    a `stale` record whose entire `stale_mismatched_fields` is machinery-
    only reaches the SAME rebind treatment as `status=="converged"`, the
    sha1 matches, and the gate is clean."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {
        "seg01": {"status": "stale", "stale_mismatched_fields": ["plugin_bundle_hash"]},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a stale-but-machinery-only-carved-out segment with a matching "
        "reviewed draft must be clean, not false-RED on a heading it "
        "genuinely satisfies\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert payload["defects"] == [], payload


# ===========================================================================
# 2. NO-HEADING carved-out segment still gets the reviewed-SHA rebind --
#    the most important case: proves the widening ADDS a check, not just
#    relaxes one.
# ===========================================================================


def test_carved_out_stale_segment_with_no_heading_still_gets_rebind(tmp_path):
    """A carved-out `stale` segment that declares NO heading block at all
    (so the coverage invariant alone would never look at it) whose on-disk
    draft has been edited AFTER the ledger recorded reviewed_draft_sha1.

    Before the fix: `status == "stale" != "converged"` -- this segment was
    ALREADY skipped by the old bare `!= "converged"` test, exactly like
    every non-converged status, so it got NO rebind at all (silently
    passed, exit 0) -- a hand-edited, unreviewed draft would ship
    undetected. This is worse than "false RED", it is a false GREEN, and
    it is why this is the most important case in this file: it proves the
    #491 widening ADDS a real check (the rebind) rather than merely
    relaxing the coverage invariant for carved-out segments that happen to
    own a heading.

    After the fix: this segment now qualifies for the carve-out and
    reaches `_rebind_or_flag_stale()`, which recomputes the CURRENT
    on-disk sha1 and finds it does not match `reviewed_draft_sha1` -- a
    HARD `stale_review_since_audit` defect, exit 1, naming seg01."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)  # no declared heading_types at all

    write_draft(root, "seg01", {"PARA:seg01:0001": "Original reviewed text."})
    # Ledger written BEFORE the hand edit below -- reviewed_draft_sha1
    # auto-computes from what is on disk RIGHT NOW, so it captures the
    # ORIGINAL bytes; the edit that follows makes it stale.
    write_ledger(root, {
        "seg01": {"status": "stale", "stale_mismatched_fields": ["schema_hash"]},
    })
    # Hand edit landing strictly after the (simulated) review -- exactly
    # the class of tamper the rebind exists to catch.
    write_draft(root, "seg01", {"PARA:seg01:0001": "Tampered text nobody reviewed."})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "a carved-out stale segment with NO declared heading must still "
        "get the reviewed-SHA rebind and RED on a post-review hand edit -- "
        "before the #491 widening this segment got no rebind at all and "
        "sailed through silently (exit 0)\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": None, "kind": "stale_review_since_audit"} in payload["defects"], payload


# ===========================================================================
# 3. CONTENT-AFFECTING stale is still excluded -- the carve-out predicate
#    itself must stay narrow.
# ===========================================================================


def test_content_affecting_stale_segment_still_excluded(tmp_path):
    """Same shape as case 1 (a declared heading, matching reviewed draft)
    but the moved field is content-affecting (`used_terms_hash`, outside
    SAFE_STALE_CARVEOUT_FIELDS) -- the segment must NOT qualify for the
    carve-out, so it stays excluded from trusted_drafts and the gate
    reports the same missing_heading it always did for a non-converged
    segment owning a declared heading."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {
        "seg01": {"status": "stale", "stale_mismatched_fields": ["used_terms_hash"]},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "a content-affecting stale field must NOT be carved out\n"
        + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"], payload


# ===========================================================================
# 4. MALFORMED stale_mismatched_fields -- still excluded, never crashes.
# ===========================================================================


@pytest.mark.parametrize(
    "cfg_extra,label",
    [
        ({}, "key missing entirely (pre-#491 legacy ledger)"),
        ({"stale_mismatched_fields": []}, "empty list"),
        ({"stale_mismatched_fields": "not-a-list"}, "wrong type (a string)"),
        ({"stale_mismatched_fields": [{}]}, "non-string member, unhashable (a dict)"),
        ({"stale_mismatched_fields": ["plugin_bundle_hash", None]}, "non-string member, hashable (None)"),
    ],
)
def test_malformed_stale_mismatched_fields_excluded_never_crashes(tmp_path, cfg_extra, label):
    """A hand-edited or corrupted runs/ledger.json (this script never
    schema-validates the ledger it reads, matching assemble.py's own
    documented stance) can carry a stale_mismatched_fields shape that is
    missing, empty, the wrong type, or -- the sharpest case -- a list
    containing an UNHASHABLE member ([{}]/[[]]), which would raise
    TypeError at a bare `f not in SAFE_STALE_CARVEOUT_FIELDS` frozenset
    membership test if the string-type guard were skipped (see
    _stale_qualifies_for_carveout()'s own docstring, condition 2). Every
    one of these shapes must be treated as NOT qualifying for the carve-out
    -- same fate as any other non-machinery-only stale record (case 3
    above): excluded from trusted_drafts, reported via the ordinary
    missing_heading path, exit 1 -- and, above all, must never let an
    uncaught traceback escape this script's own clean exit contract."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "stale", **cfg_extra}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, f"{label}:\n{proc.stdout}\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, (
        f"{label}: a malformed stale_mismatched_fields shape must never leak "
        f"a raw Python traceback\nstderr:\n{proc.stderr}"
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"], (
        f"{label}: {payload}"
    )


# ===========================================================================
# 5. UNCHANGED BEHAVIOUR -- regression fence for ordinary converged
#    projects, with no stale record involved at all.
# ===========================================================================


def test_all_converged_project_still_passes(tmp_path):
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout_json(proc)["defects"] == []


def test_genuinely_incomplete_converged_project_still_fails(tmp_path):
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})  # nothing converged, no stale record either -- no draft exists

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


# ===========================================================================
# 6. Four-way drift guard for the machinery-only field set.
# ===========================================================================


def test_four_way_drift_guard_for_machinery_only_fields():
    """The machinery-only field set is now restated FOUR times across this
    plugin (final_audit.py, select_segments.py, assemble.py, and -- as of
    #491's own follow-up fix in validate_assembled.py -- a fourth copy).
    tests/stale_carveout.test.py's own
    test_three_copy_drift_guard_for_machinery_only_fields pins the first
    three against each other; it lives in a file this change does not own
    (a concurrent teammate edits it in this same worktree), so this is a
    SEPARATE, wider assertion that also reads validate_assembled.py's own
    copy -- with a minimum-size assertion so a parse failure that silently
    empties all four cannot pass vacuously.

    Mutation: any one of the four copies drifts -> red."""
    final_audit_mod = _load_module_from_source(
        FINAL_AUDIT_SRC, "validate_assembled_carveout__final_audit_ref"
    )
    select_segments_mod = _load_module_from_source(
        SELECT_SEGMENTS_SRC, "validate_assembled_carveout__select_segments_ref"
    )
    assemble_mod = _load_module_from_source(
        ASSEMBLE_SRC, "validate_assembled_carveout__assemble_ref"
    )
    validate_assembled_mod = _load_module_from_source(
        VALIDATE_ASSEMBLED_SRC, "validate_assembled_carveout__validate_assembled_ref"
    )

    sets = {
        "final_audit.SAFE_STALE_CARVEOUT_FIELDS": final_audit_mod.SAFE_STALE_CARVEOUT_FIELDS,
        "select_segments.MACHINERY_ONLY_CACHE_KEY_FIELDS": select_segments_mod.MACHINERY_ONLY_CACHE_KEY_FIELDS,
        "assemble.SAFE_STALE_CARVEOUT_FIELDS": assemble_mod.SAFE_STALE_CARVEOUT_FIELDS,
        "validate_assembled.SAFE_STALE_CARVEOUT_FIELDS": validate_assembled_mod.SAFE_STALE_CARVEOUT_FIELDS,
    }

    for name, value in sets.items():
        assert len(value) == 3, (
            f"sanity: the known field count for {name} -- a parse failure "
            f"that silently empties a copy must not pass the equality "
            f"check below vacuously (got {sorted(value)!r})"
        )

    values = list(sets.values())
    assert all(v == values[0] for v in values), (
        "the four machinery-only field sets have drifted: "
        + ", ".join(f"{name}={sorted(value)!r}" for name, value in sets.items())
    )


# ===========================================================================
# 7. #491 R2 (MAJOR, round-2 review): a RETAINED, out-of-manifest
#    `runs/ledger.json` entry -- one for a segment the CURRENT manifest no
#    longer names, which `ledger_merge.py` deliberately keeps across
#    batches (see that script's own module docstring) -- must never reach
#    `_rebind_or_flag_stale()` at all merely because it happens to be
#    carve-out-eligible. Before this fix, the #491 widening above pulled
#    such an entry into the SAME rebind population as an in-manifest
#    carved-out segment, and a shape it cannot evaluate (missing sha1,
#    missing draft, sha1 mismatch, corrupt draft, malformed `blocks`) could
#    hard-fail an otherwise-deliverable CURRENT book for a segment it does
#    not even need. Every case here builds a project with ONE healthy,
#    in-manifest, converged segment (seg01) PLUS a retained out-of-manifest
#    entry ("seg_retired") in the given shape, and asserts the gate is
#    CLEAN -- proving the healthy segment's own delivery is not held
#    hostage by a retained entry the current book was never going to look
#    at anyway.
# ===========================================================================


def _make_healthy_converged_seg01(root: Path) -> dict:
    """Writes manifest.json + the on-disk draft for a single healthy,
    in-manifest, converged segment ("seg01") and returns its own
    runs/ledger.json record -- the SAME record write_ledger() itself would
    produce, computed directly here (rather than via write_ledger(), which
    always OVERWRITES the whole `segments` object) so it can be merged with
    a second, out-of-manifest record built by each case below via
    write_ledger_raw()."""
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    draft_doc = json.loads((root / "segments" / "seg01.draft.json").read_text(encoding="utf-8"))
    return {"seg01": {"status": "converged", "reviewed_draft_sha1": draft_content_sha1_of(draft_doc)}}


def write_ledger_raw(root: Path, segments: dict) -> None:
    """Writes runs/ledger.json's `segments` object VERBATIM -- no
    auto-computed reviewed_draft_sha1, unlike write_ledger() above. Cases
    7.1-7.5 and 8 below need precise control over shapes write_ledger()'s
    own auto-compute convention cannot express: an entry with NO
    reviewed_draft_sha1 key at all while its draft file DOES exist, a sha1
    that matches nothing because no draft file exists, a literal
    (deliberately wrong) sha1, or a seg KEY that is itself not a valid
    segment id."""
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def test_out_of_manifest_stale_no_sha1_does_not_hard_fail(tmp_path):
    """7.1 -- the retained entry's draft file exists (so the ONLY unmet
    condition is the missing reviewed_draft_sha1 key), yet the gate must
    still be clean: pre-#491-R2, `_rebind_or_flag_stale()`'s own
    `not isinstance(expected, str)` branch would add "seg_retired" to
    `stale_segs`, surfacing a `stale_review_since_audit` HARD defect for a
    segment the current manifest never asked about."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    write_draft(root, "seg_retired", {"PARA:seg_retired:0001": "Retired prose."})
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["plugin_bundle_hash"],
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a retained out-of-manifest stale entry missing reviewed_draft_sha1 "
        "must never hard-fail an otherwise-deliverable book\n"
        + proc.stdout + proc.stderr
    )
    assert parse_stdout_json(proc)["defects"] == []


def test_out_of_manifest_stale_missing_draft_does_not_hard_fail(tmp_path):
    """7.2 -- the retained entry names a plausible reviewed_draft_sha1, but
    no draft file for "seg_retired" was ever written (or it was cleaned up
    long ago, since the manifest no longer needs it)."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["schema_hash"],
        "reviewed_draft_sha1": "a" * 40,
    }
    write_ledger_raw(root, segments)
    assert not (root / "segments" / "seg_retired.draft.json").exists()

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a retained out-of-manifest stale entry with a missing draft file "
        "must never hard-fail an otherwise-deliverable book\n"
        + proc.stdout + proc.stderr
    )
    assert parse_stdout_json(proc)["defects"] == []


def test_out_of_manifest_stale_sha1_mismatch_does_not_hard_fail(tmp_path):
    """7.3 -- the retained entry's draft file exists and is well-formed,
    but its recorded reviewed_draft_sha1 no longer matches (the genuine
    HARD-defect shape case 6 below still catches when the entry IS in the
    manifest)."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    write_draft(root, "seg_retired", {"PARA:seg_retired:0001": "Retired prose."})
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["derivation_bundle_hash"],
        "reviewed_draft_sha1": "b" * 40,  # deliberately wrong
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a retained out-of-manifest stale entry with a sha1 mismatch must "
        "never hard-fail an otherwise-deliverable book\n"
        + proc.stdout + proc.stderr
    )
    assert parse_stdout_json(proc)["defects"] == []


def test_out_of_manifest_stale_corrupt_draft_does_not_hard_fail(tmp_path):
    """7.4 -- THE MOST VALUABLE CASE. Pre-fix, this shape does not merely
    add a HARD defect -- it makes `_rebind_or_flag_stale()` raise
    `_MalformedArtifact` (`draft could not be read/decoded/parsed`),
    escaping this gate's exit-1 defect list entirely and taking the whole
    process down at exit 2, for a segment the current book does not even
    need. reviewed_draft_sha1 is a well-formed-looking (but irrelevant)
    hex string -- the corrupt JSON is what must matter, not the sha1."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    (root / "segments" / "seg_retired.draft.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["plugin_bundle_hash"],
        "reviewed_draft_sha1": "c" * 40,
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a retained out-of-manifest stale entry with a corrupt/unreadable "
        "draft must never crash this gate (exit 2) for an otherwise-"
        "deliverable book\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    assert parse_stdout_json(proc)["defects"] == []


def test_out_of_manifest_stale_malformed_blocks_does_not_hard_fail(tmp_path):
    """7.5 -- the retained entry's draft parses fine and its sha1 matches
    (write_ledger()'s own auto-compute, reused here since a malformed
    `blocks` field is still valid JSON), but `blocks` itself is not an
    object -- draft.schema.json requires `blocks` to be an object, and
    pre-fix this reaches the `_MalformedArtifact` raise in
    `_rebind_or_flag_stale()` for that reason alone."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    (root / "segments" / "seg_retired.draft.json").write_text(
        json.dumps({"seg": "seg_retired", "blocks": ["not", "an", "object"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    draft_doc = json.loads((root / "segments" / "seg_retired.draft.json").read_text(encoding="utf-8"))
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["schema_hash"],
        "reviewed_draft_sha1": draft_content_sha1_of(draft_doc),
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a retained out-of-manifest stale entry with a malformed `blocks` "
        "field must never hard-fail an otherwise-deliverable book\n"
        + proc.stdout + proc.stderr
    )
    assert parse_stdout_json(proc)["defects"] == []


# ===========================================================================
# 8. Regression fences -- what the #491 R2 scoping fix must NOT weaken.
# ===========================================================================


def test_in_manifest_carved_out_stale_sha1_mismatch_still_hard_fails(tmp_path):
    """8.6 -- an IN-manifest carved-out stale entry with a genuine sha1
    mismatch is EXACTLY the shape the reviewed-SHA rebind exists to catch
    (a hand edit landing after review) -- the #491 R2 scoping fix must not
    touch this at all. Still HARD, exit 1, naming the segment."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"PARA:seg01:0001": "Original reviewed text."})
    write_ledger(root, {
        "seg01": {"status": "stale", "stale_mismatched_fields": ["plugin_bundle_hash"]},
    })
    write_draft(root, "seg01", {"PARA:seg01:0001": "Tampered text nobody reviewed."})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "an IN-manifest carved-out stale segment with a genuine sha1 "
        "mismatch must still hard-fail\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": None, "kind": "stale_review_since_audit"} in payload["defects"], payload


def test_in_manifest_carved_out_stale_corrupt_draft_still_exits_2(tmp_path):
    """8.7 -- an IN-manifest carved-out stale entry whose draft is corrupt
    JSON must still raise _MalformedArtifact (exit 2) -- this gate cannot
    evaluate a corrupt draft regardless of whether it is carve-out-eligible
    or in the manifest."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {
        "seg01": {"status": "stale", "stale_mismatched_fields": ["plugin_bundle_hash"]},
    })
    (root / "segments" / "seg01.draft.json").write_text("{not valid json", encoding="utf-8")

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an IN-manifest carved-out stale segment with a corrupt draft must "
        "still exit 2\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, proc.stderr


def test_out_of_manifest_traversal_seg_key_still_fatal(tmp_path):
    """8.8 -- proves the manifest-membership test cannot have been slipped
    in AHEAD of the three fatal element-shape/security guards
    (collect_reviewed_draft_rebind's own docstring): an out-of-manifest
    ledger `segments{}` key that is itself not a valid segment id (fails
    vd.validate_seg()'s own path-safety allowlist) must still be fatal
    (exit 2), never silently skipped merely because it is "not in the
    manifest" -- the SECURITY guard runs for every record regardless of
    membership."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    segments["../../etc/x"] = {
        "status": "stale",
        "stale_mismatched_fields": ["plugin_bundle_hash"],
        "reviewed_draft_sha1": "d" * 40,
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an out-of-manifest ledger entry whose seg KEY fails path-safety "
        "validation must still be fatal, proving the security guard was "
        "not scoped away by the manifest-membership test\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, proc.stderr


def test_out_of_manifest_converged_sha1_mismatch_still_hard_fails(tmp_path):
    """8.9 -- an out-of-manifest CONVERGED entry with a sha1 mismatch is
    PRE-EXISTING behaviour (BLOCKER 1, predates #491 R2 entirely) and the
    #491 R2 scoping fix deliberately does NOT touch the "converged" branch
    -- asserted here so a later refactor cannot silently extend the
    manifest-membership test to that branch too. Still HARD, exit 1."""
    root = make_root(tmp_path)
    segments = dict(_make_healthy_converged_seg01(root))
    write_draft(root, "seg_retired", {"PARA:seg_retired:0001": "Retired prose."})
    segments["seg_retired"] = {
        "status": "converged",
        "reviewed_draft_sha1": "e" * 40,  # deliberately wrong
    }
    write_ledger_raw(root, segments)

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "an out-of-manifest CONVERGED entry with a sha1 mismatch must "
        "still hard-fail -- this is pre-existing behaviour the #491 R2 "
        "scoping fix must not touch\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg_retired", "block_id": None, "kind": "stale_review_since_audit"} in payload["defects"], payload


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
