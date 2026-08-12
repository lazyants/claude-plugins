"""tests/validate_conservation_carveout.test.py -- codex round-3 coverage
gap on the #491 R2 fix (validate_assembled.py's `collect_reviewed_draft_
rebind()` gained a required `manifest_seg_ids` parameter so its NEW
machinery-only stale carve-out branch cannot pull a RETAINED, out-of-
manifest `runs/ledger.json` entry into a gate that segment was never
subject to -- see tests/validate_assembled_carveout.test.py section "7."
for the full defect history and its own fixtures against
validate_assembled.py directly).

WHY THIS FILE EXISTS. `validate_conservation.py`'s own `output-coverage`
lane (default `segment_drafts_and_audit` scope) is a SECOND caller of the
same `collect_reviewed_draft_rebind()` (validate_conservation.py:1114) --
reused, never reimplemented, per that script's own module docstring
("Population scope"/"Reuses validate_assembled.py's own reviewed-SHA
rebind machinery" section). The #491 R2 fix could not edit that call site
itself (a different file, owned by a concurrent change), so the team lead
wired it centrally: `va.collect_reviewed_draft_rebind(ledger_segments,
va.collect_manifest_seg_ids(manifest_segments))` (validate_conservation.py
:1114-1116). That wiring is correct today, but every out-of-manifest
fixture in the existing suite (tests/validate_assembled_carveout.test.py)
drives `validate_assembled.py` only -- nothing exercises the SAME retained-
entry shape THROUGH `validate_conservation.py`'s own call site. A mutant
there (e.g. passing the ledger's own key set instead of the real
manifest-derived one) was not causally isolated by any test. This file
closes that gap.

## A structural fact this file's own cases had to be built around

`run_output_coverage()`'s default-scope branch DISCARDS the rebind's
`stale_segs` return value outright (bound to `_stale_segs`,
validate_conservation.py:1114) and derives `eligible_keys` as
`{(seg, bid) for (seg, bid) in source_marker_counter if seg in
trusted_drafts}` (validate_conservation.py:1125) -- `source_marker_counter`
comes from `collect_source_markers(manifest_segments, ...)`, which by
construction can NEVER name a `(seg, bid)` pair for a segment the manifest
does not cite. So an out-of-manifest segment's own `stale_segs`-only
outcome (a missing draft, a missing `reviewed_draft_sha1`, or a genuine
sha1 mismatch -- none of them raise) is, in THIS lane specifically,
unobservable via exit code or `warnings[]` regardless of whether the
retained entry was scoped in or out: neither branch ever lets it reach
`eligible_keys`. The ONLY channel through which the #491 R2 scoping fix is
observable here is the FATAL path: a retained entry's draft that cannot be
parsed (or has a malformed `blocks` field) makes `_rebind_or_flag_stale()`
raise `_MalformedArtifact`, which `run_output_coverage()`'s own `except
va._MalformedArtifact as exc: raise ConservationError(str(exc))` turns
into exit 2 -- for a segment this WARN-only lane, which is documented to
"never exit 1" for `output-coverage`, was never supposed to be able to
crash the process over at all. See the mutation-proof section at the
bottom of this file for the MEASURED confirmation of this asymmetry (the
non-fatal shape does NOT go red under the M1 mutant; the fatal shape
does) -- included per the brief's own instruction to report a mutation
result plainly rather than adjust an assertion until it passes.

## Fixture strategy

Mirrors tests/validate_conservation.test.py's own approach (a real,
self-contained `durable_root`, the actual script driven as a subprocess)
and tests/validate_assembled_carveout.test.py's own out-of-manifest
fixture shape (a `write_ledger_raw()` that writes `runs/ledger.json`
verbatim, for full control over a retained entry's exact shape) --
self-contained, duplicated here rather than imported, per this suite's
"each *.test.py file stays self-contained" convention.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/validate_conservation_carveout.test.py``
(the project's own pytest.ini already sets this).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

VALIDATE_CONSERVATION_SRC = SCRIPTS_SRC_DIR / "validate_conservation.py"
VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

for _src in (VALIDATE_CONSERVATION_SRC, VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
    assert _src.is_file(), f"required source not found: {_src}"


# ---------------------------------------------------------------------------
# Fixture builders -- duplicated from tests/validate_conservation.test.py's
# own (house convention: each *.test.py file stays self-contained), plus a
# write_ledger_raw() mirroring tests/validate_assembled_carveout.test.py's
# own helper of the same name.
# ---------------------------------------------------------------------------


def make_root(tmp_path) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_CONSERVATION_SRC, VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
        (scripts_dir / src.name).write_bytes(src.read_bytes())
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def write_profile(root: Path, v1_scope: str = "segment_drafts_and_audit") -> None:
    profile = {"output": {"v1_scope": v1_scope}}
    (root / "profile.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )


def make_block(raw_type: str, plain_text: str = "Source text.", order_index: int = 0) -> dict:
    return {"type": raw_type, "order_index": order_index, "plain_text": plain_text}


def write_manifest(root: Path, blocks: dict, segments: list) -> None:
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


def write_ledger_raw(root: Path, segments: dict) -> None:
    """Writes runs/ledger.json's `segments` object VERBATIM -- no
    auto-computed reviewed_draft_sha1. Every case below needs precise
    control over a shape a simpler auto-computing write_ledger() cannot
    express: an entry with NO reviewed_draft_sha1 key at all while no
    draft file exists either, or a literal (deliberately arbitrary) sha1
    on an entry whose draft is corrupt JSON and so can never be hashed."""
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def run_validate_conservation(root: Path, mode: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_conservation.py"), mode],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout_json(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# The project's own healthy segment, shared by every case below.
#
# Deliberately built to be HOLLOW (real source, empty draft) rather than
# merely present -- test_output_coverage_warn_hollowed_default_scope (in
# tests/validate_conservation.test.py) already establishes this exact
# shape earns a real `hollowed_output_block` WARN. Reusing it here means
# every case below proves not just "exit 0" (trivially true for a lane
# that silently no-ops on EVERYTHING, retained garbage included) but "exit
# 0 AND this project's own genuine warning still surfaces" -- a strictly
# stronger, harder-to-fake positive control than an exit code alone.
# ---------------------------------------------------------------------------

_HOLLOWED_WARNING = ("seg01", "PARA:seg01:0001", "hollowed_output_block")


def _make_healthy_hollowed_project(root: Path) -> dict:
    """Writes manifest.json + the on-disk draft for the project's own
    healthy, in-manifest segment ("seg01"): one declared block with real
    source content and a deliberately EMPTY draft. Returns its own
    runs/ledger.json record (status "converged", reviewed_draft_sha1
    computed from what is on disk right now) for write_ledger_raw() to
    merge with a second, out-of-manifest record -- write_ledger_raw()
    always OVERWRITES the whole `segments` object, so the caller must
    build the full merged dict itself."""
    blocks = {"PARA:seg01:0001": make_block("PARA", "This block has real, substantial source content.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 7}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"PARA:seg01:0001": ""})
    draft_doc = json.loads((root / "segments" / "seg01.draft.json").read_text(encoding="utf-8"))
    return {"seg01": {"status": "converged", "reviewed_draft_sha1": draft_content_sha1_of(draft_doc)}}


# ===========================================================================
# 1. POSITIVE CONTROL -- no out-of-manifest noise at all. Establishes the
#    baseline every case below compares itself against: the lane must be
#    doing REAL work, not silently no-op'ing.
# ===========================================================================


def test_healthy_project_output_coverage_does_real_work(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    seg01_record = _make_healthy_hollowed_project(root)
    write_ledger_raw(root, dict(seg01_record))

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert _HOLLOWED_WARNING in kinds, (
        "POSITIVE CONTROL failed -- the output-coverage lane produced no "
        "warning for a genuinely hollow block. Every case below relies on "
        "this SAME warning still surfacing around a retained out-of-"
        "manifest ledger entry; if the lane itself is not doing real work, "
        "those cases would pass vacuously\n" + proc.stdout + proc.stderr
    )


# ===========================================================================
# 2. Out-of-manifest, carve-out-eligible `stale` retained entry -- must
#    never break this lane, and the healthy segment's own warning must
#    still surface around it.
# ===========================================================================


def test_out_of_manifest_stale_missing_draft_does_not_break_lane(tmp_path):
    """Case 1 (missing draft / no reviewed_draft_sha1) -- see this file's
    own module docstring for why this specific shape is NOT actually
    mutation-sensitive in this lane (`_rebind_or_flag_stale()`'s non-fatal
    `stale_segs.add()` branch, whose result `run_output_coverage()`
    discards outright). Included for completeness and because the brief
    asked for both shapes explicitly; the measured mutation result is
    reported honestly in section 4 below rather than adjusted to pass."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    seg01_record = _make_healthy_hollowed_project(root)
    segments = dict(seg01_record)
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["schema_hash"],
        "reviewed_draft_sha1": "a" * 40,
    }
    write_ledger_raw(root, segments)
    assert not (root / "segments" / "seg_retired.draft.json").exists()

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, (
        "a retained out-of-manifest carve-out-eligible stale entry with a "
        "missing draft must never break this WARN-only lane\n"
        + proc.stdout + proc.stderr
    )
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert _HOLLOWED_WARNING in kinds, (
        "the healthy project's own genuine warning must still surface "
        "around the retained entry\n" + proc.stdout + proc.stderr
    )


def test_out_of_manifest_stale_corrupt_draft_does_not_break_lane(tmp_path):
    """Case 2 -- THE MOST VALUABLE CASE, and the ONLY one of the two
    out-of-manifest shapes that is actually mutation-sensitive in this
    lane (see module docstring and section 4 below): a corrupt draft makes
    `_rebind_or_flag_stale()` RAISE `_MalformedArtifact`, which
    `run_output_coverage()`'s own `except va._MalformedArtifact as exc:
    raise ConservationError(str(exc))` converts to exit 2 -- for a segment
    the current book does not even need, on a lane documented to never
    exit 1 and never supposed to be able to crash the process at all."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    seg01_record = _make_healthy_hollowed_project(root)
    segments = dict(seg01_record)
    (root / "segments" / "seg_retired.draft.json").write_text("{not valid json", encoding="utf-8")
    segments["seg_retired"] = {
        "status": "stale",
        "stale_mismatched_fields": ["plugin_bundle_hash"],
        "reviewed_draft_sha1": "b" * 40,
    }
    write_ledger_raw(root, segments)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, (
        "a retained out-of-manifest carve-out-eligible stale entry with a "
        "corrupt/unreadable draft must never crash this WARN-only lane "
        "(exit 2) for an otherwise-healthy project\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert _HOLLOWED_WARNING in kinds, (
        "the healthy project's own genuine warning must still surface "
        "around the retained entry\n" + proc.stdout + proc.stderr
    )


# ===========================================================================
# 3. Regression fence -- an IN-manifest carved-out stale entry with a
#    corrupt draft must still be fatal. Proves the scoping fix did not
#    silently swallow a genuine fatal for a segment the CURRENT manifest
#    actually needs.
# ===========================================================================


def test_in_manifest_carved_out_stale_corrupt_draft_still_exits_2(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    blocks = {"PARA:seg01:0001": make_block("PARA", "Just prose, no headings.")}
    manifest_segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, manifest_segments)
    write_draft(root, "seg01", {"PARA:seg01:0001": "Original reviewed text."})
    draft_doc = json.loads((root / "segments" / "seg01.draft.json").read_text(encoding="utf-8"))
    write_ledger_raw(root, {
        "seg01": {
            "status": "stale",
            "stale_mismatched_fields": ["plugin_bundle_hash"],
            "reviewed_draft_sha1": draft_content_sha1_of(draft_doc),
        }
    })
    # Corrupted strictly AFTER the ledger recorded a sha1 for the
    # (still-valid, at that point) draft -- the review-then-tamper
    # sequence this rebind exists to catch, not a fixture ordering bug.
    (root / "segments" / "seg01.draft.json").write_text("{not valid json", encoding="utf-8")

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 2, (
        "an IN-manifest carved-out stale segment with a corrupt draft must "
        "still exit 2 -- the #491 R2 scoping fix must not weaken the fatal "
        "path for a segment the manifest actually needs\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, proc.stderr


# ===========================================================================
# 4. Mutation proof (the deliverable this file exists for).
#
#    M1: validate_conservation.py:1114-1116's call site is mutated from
#        `va.collect_reviewed_draft_rebind(ledger_segments,
#         va.collect_manifest_seg_ids(manifest_segments))` to
#        `va.collect_reviewed_draft_rebind(ledger_segments,
#         set(ledger_segments))` -- i.e. "in scope" degrades back to "is a
#        ledger key at all", which is exactly the pre-#491-R2 defect this
#        wiring exists to prevent, reintroduced at THIS call site only.
#
#    This is not executed by pytest automatically -- it is a manual,
#    reported procedure per the brief (apply, run, observe, restore,
#    diff -q), because it mutates a file this test suite's author does not
#    own. See the chat report for the verbatim red output and the restore
#    confirmation.
# ===========================================================================


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
