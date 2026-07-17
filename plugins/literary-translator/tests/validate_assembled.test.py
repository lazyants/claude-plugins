"""tests/validate_assembled.test.py -- regression-lock for
scripts/validate_assembled.py (#202), the union structural-completeness
gate: every manifest-DECLARED heading (`manifest.heading_types` plus the
built-in `"HEAD"`, #210) must surface, book-wide, as non-empty translated
text. See that script's own module docstring for the full invariant spec
this file was written against.

## Fixture strategy

Every behavioral test builds a REAL, self-contained ``durable_root`` on disk
(``manifest.json``, per-segment ``{seg}.draft.json``, the materialized
``runs/ledger.json``, ``profile.yml`` + ownership marker -- and, for
``assembled_book``-scope tests, a hand-built ``out/.assembled/
nodestream.json``) and invokes the ACTUAL ``validate_assembled.py`` as a
subprocess -- ``python3 {durable_root}/scripts/validate_assembled.py``, no
CLI arguments, self-anchored exactly like ``final_audit.py``. Only
``validate_draft.py`` needs to be copied alongside it (its sole sibling
import, for ``load_profile()`` -- reused, never reimplemented).

The ``assembled_book``-scope RED fixture hand-builds ``nodestream.json``
directly rather than running the real ``assemble.py`` -- this file's own
job is proving THIS gate's coverage check, independent of whether #210's
``_classify_kind`` itself is correct (that is ``tests/assemble.test.py``'s
job).

## Mutation-proof tests

A handful of tests near the end load the real script as an IN-PROCESS
module (via ``importlib``, from a COPIED fixture path so its own
self-anchored ``DURABLE_ROOT`` still resolves against a real fixture) rather
than driving it as a subprocess -- the only way to (a) monkeypatch
``compute_missing_heading_defects`` to a no-op and prove the RED fixtures
above are not vacuously green regardless of what that function does, and
(b) prove the invariant genuinely depends on ``Counter`` MULTIPLICITY, not
mere key presence, by constructing a set-collapsed mutant of the source
side and showing it would wrongly pass the exact repeated-key case a
Counter catches.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/validate_assembled.test.py``.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

assert VALIDATE_ASSEMBLED_SRC.is_file(), f"validate_assembled.py not found at {VALIDATE_ASSEMBLED_SRC} -- #202 has not landed yet"
assert VALIDATE_DRAFT_SRC.is_file(), f"validate_draft.py not found at {VALIDATE_DRAFT_SRC}"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_root(tmp_path, v1_scope: str = "segment_drafts_and_audit") -> Path:
    """A bare durable_root: real copies of validate_assembled.py + its sole
    sibling import (validate_draft.py, for load_profile()), a minimal
    profile.yml + ownership marker. validate_assembled.py's own
    load_profile() call only ever reads output.v1_scope from the parsed
    profile -- it never runs profile.schema.json validation at runtime (that
    is Step 0's own job, not this script's), so a minimal profile fixture is
    sufficient and honest here."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
        _copy(src, scripts_dir / src.name)

    profile = {"output": {"v1_scope": v1_scope}}
    (root / "profile.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def _copy(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())


def make_block(raw_type: str, plain_text: str = "Source text.", order_index: int = 0) -> dict:
    return {"type": raw_type, "order_index": order_index, "plain_text": plain_text}


def write_manifest(root: Path, blocks: dict, segments: list, heading_types=None) -> None:
    """blocks: dict[block_id -> block dict, WITHOUT 'id'/'source_file'/'sha1'
    -- filled in here]. segments: list of already-fully-shaped segment
    dicts. heading_types: omitted entirely (not even as an empty list)
    unless a caller passes one, so the default fixture exercises the
    schema's own "absent -> only HEAD is a heading" back-compat path."""
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
    sorted-key canonical JSON, matching validate_assembled.py's/
    final_audit.py's/assemble.py's own draft_content_sha1() byte for byte.
    Mirrors tests/assemble.test.py's own draft_content_sha1_of()."""
    import hashlib

    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_draft(root: Path, seg: str, blocks: dict) -> dict:
    draft = {"seg": seg, "blocks": blocks, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return draft


def write_ledger(root: Path, entries: dict) -> None:
    """entries: seg -> {"status": ..., optionally "reviewed_draft_sha1": literal
    override}. When status=="converged" and no literal override is given,
    reviewed_draft_sha1 is auto-computed from whatever draft is CURRENTLY on
    disk for that seg (the same convention tests/assemble.test.py's own
    write_ledger() uses) -- so re-calling this after mutating a draft
    re-binds the ledger to the new bytes, and calling it BEFORE mutating
    leaves a stale (now-mismatched) recorded sha1 in place, exactly what the
    stale-review-since-audit tests below rely on."""
    segments = {}
    for seg, cfg in entries.items():
        record = {"status": cfg["status"]}
        if cfg["status"] == "converged":
            if "reviewed_draft_sha1" in cfg:
                record["reviewed_draft_sha1"] = cfg["reviewed_draft_sha1"]
            else:
                draft_path = root / "segments" / f"{seg}.draft.json"
                draft_doc = json.loads(draft_path.read_text(encoding="utf-8"))
                record["reviewed_draft_sha1"] = draft_content_sha1_of(draft_doc)
        segments[seg] = record
    (root / "runs" / "ledger.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")


def write_nodestream(root: Path, nodestream: dict) -> None:
    assembled_dir = root / "out" / ".assembled"
    assembled_dir.mkdir(parents=True, exist_ok=True)
    (assembled_dir / "nodestream.json").write_text(json.dumps(nodestream, ensure_ascii=False), encoding="utf-8")


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


# ===========================================================================
# RED -- assembled_book scope: nodestream missing a heading node for a
# declared source marker.
# ===========================================================================


def test_red_assembled_book_missing_heading_node(tmp_path):
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {
        "HEAD:seg01": make_block("HEAD", plain_text="Chapter One"),
        "PARA:seg01:0001": make_block("PARA", plain_text="Body text.", order_index=1),
    }
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)  # heading_types absent -> only the built-in HEAD is a heading

    # Hand-built nodestream, deliberately DROPPING the heading node for
    # HEAD:seg01 -- independent of assemble.py's own _classify_kind (#210):
    # this test's job is this gate's own coverage check, not #210's
    # classifier correctness.
    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [
            {"id": "PARA:seg01:0001", "seg": "seg01", "kind": "prose", "text": "Telo teksta."},
        ],
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


def test_green_assembled_book_heading_node_present(tmp_path):
    """Positive control for the assembled_book scope, proving the RED test
    above is a genuine negative control, not an always-red fixture."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [
            {"id": "HEAD:seg01", "seg": "seg01", "kind": "heading", "text": "Glava Odna"},
        ],
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout_json(proc)["defects"] == []


def test_red_nodestream_heading_node_non_string_seg_id(tmp_path):
    """codex round-4 FIX 9 (revised by the team lead after the first pass):
    a REAL, non-empty heading node's own seg/id become a Counter-KEY in
    collect_nodestream_output_markers -- an unhashable component (e.g. a
    list) would otherwise crash with an uncaught TypeError. Rather than
    `_MalformedArtifact` (fatal, exit 2), this is SKIPPED -- consistent
    with this scope's own existing non-dict-node fence (fail-closed via
    omission, not via a hard stop): the skipped node's own source key
    simply never surfaces, so it naturally REDs via the ordinary
    missing_heading coverage invariant -- exit 1, never exit 2, and never
    an uncaught traceback either way."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [
            {"id": [], "seg": "seg01", "kind": "heading", "text": "Glava Odna"},  # non-str id -- the bug
        ],
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "a heading node with a non-string id must be SKIPPED (not fatal), "
        "so its own source marker fails to surface and REDs via the "
        "ordinary missing_heading coverage invariant -- never an uncaught "
        "TypeError, never exit 2\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must never leak a raw Python traceback"
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


def test_red_nodestream_nodes_truthy_non_list(tmp_path):
    """codex R5-6 (MAJOR): `nodestream.get("nodes") or []` tolerated any
    falsy-or-absent value as "no nodes", but a truthy NON-list value
    (e.g. an int) survives the `or` unchanged only to crash on the very
    next line -- `for node in 5` raises an uncaught `TypeError: 'int'
    object is not iterable`. There is no nodestream JSON Schema
    (assemble.py's own contract is the sole source of truth), so this is
    hand-rolled and checked up front."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": 5,  # truthy non-list -- the bug
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-array nodestream 'nodes' must fail closed at exit 2, never "
        "crash with an uncaught TypeError: object is not iterable\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must never leak a raw Python traceback"


def test_red_nodestream_nodes_wrong_shape_dict(tmp_path):
    """codex R5-6, companion case: a wrong-shaped but FALSY container (an
    empty dict) used to silently iterate zero nodes -- verified by direct
    scratch-copy-swap reproduction to genuinely RED pre-fix at rc=1 (a
    real declared heading in THIS fixture never surfaces, so it correctly
    reports `missing_heading` rather than exiting 0 -- the exit-0-clean
    scenario would only occur for a project with NO declared headings at
    all, a narrower claim than "any project"). Either way, a wrong-shaped
    'nodes' container is a corrupt artifact this gate cannot meaningfully
    evaluate and must fail closed at exit 2, the same as the
    truthy-non-list case above -- not silently coerced to an empty
    population under either exit code."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": {},  # wrong-shape, falsy -- the bug
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_assembled_book_duplicate_heading_occurrence_dropped_node(tmp_path):
    """ped-ant PR#230 [P1], the genuine home for occurrence-level
    multiplicity: a heading declared TWICE (the same block_id cited twice
    in one segment's own block_ids[], schema-legal -- manifest.schema.json
    sets no uniqueItems on block_ids) must produce TWO real heading nodes
    in the assembled nodestream -- assemble.py's own block loop
    (`for bid in block_ids:`) emits one node per block_ids[] occurrence,
    even though both reuse the identical underlying draft text. If
    assembly (or a hand-edited nodestream) ever surfaces only ONE
    surviving node for that key, that IS a genuine occurrence drop --
    unlike the default (no-render) scope, which cannot represent
    occurrences at all and correctly treats a present keyed draft as
    satisfying every declared occurrence (see
    test_green_default_repeated_same_key_present_draft_satisfies_all_occurrences),
    the assembled_book scope has REAL per-occurrence nodes to count, so
    THIS is where the Counter's multiplicity-awareness earns its keep."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    # Only ONE heading node for a key declared TWICE -- a genuine occurrence drop.
    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [
            {"id": "HEAD:seg01", "seg": "seg01", "kind": "heading", "text": "Glava Odna"},
        ],
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "a heading declared TWICE but rendered with only ONE surviving "
        "node must RED -- the assembled_book scope's own multiplicity-"
        "aware invariant is the genuine home for occurrence-drop "
        "detection\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


def test_green_assembled_book_duplicate_heading_occurrence_both_nodes_present(tmp_path):
    """Companion GREEN for the test above: the SAME doubly-declared
    heading, but the nodestream carries BOTH real occurrence nodes
    (matching assemble.py's own actual behavior -- it replicates one node
    per block_ids[] occurrence) -- must be clean, proving the RED test is
    a genuine negative control, not an always-red fixture."""
    root = make_root(tmp_path, v1_scope="assembled_book")
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [
            {"id": "HEAD:seg01", "seg": "seg01", "kind": "heading", "text": "Glava Odna"},
            {"id": "HEAD:seg01", "seg": "seg01", "kind": "heading", "text": "Glava Odna"},
        ],
        "footnotes": [],
        "meta": {},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout_json(proc)["defects"] == []


# ===========================================================================
# RED -- default scope: source-empty declared heading, never surfaced.
# ===========================================================================


def test_red_default_source_empty_declared_heading(tmp_path):
    """A declared heading whose SOURCE text is itself empty is still a
    genuine source marker (Counter counts occurrence of the (seg, block_id)
    KEY, never conditioned on source-text content) -- so an equally-empty
    surfaced translation still leaves C_output < C_source. This is the
    genuine incremental default-scope case validate_draft.py does not catch
    (its own empty-translation check is conditioned on a NON-empty source)."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 0}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": ""})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


# ===========================================================================
# RED -- default scope: draft changed after W7 review (reviewed_draft_sha1
# rebind, mirrors assemble.py:357) -- plus its GREEN twin.
# ===========================================================================


def test_red_default_draft_changed_after_review_and_green_twin(tmp_path):
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    # Deliberately WRONG reviewed_draft_sha1 -- simulates a hand edit that
    # landed strictly between final_audit.py's own stale-review check and
    # this gate.
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1": "0" * 40}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert any(
        d["kind"] == "stale_review_since_audit" and d["seg"] == "seg01" and d["block_id"] is None
        for d in payload["defects"]
    ), payload

    # -- GREEN twin: identical draft content, but reviewed_draft_sha1
    #    correctly re-bound to what is CURRENTLY on disk --
    write_ledger(root, {"seg01": {"status": "converged"}})
    proc2 = run_validate_assembled(root)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert parse_stdout_json(proc2)["defects"] == []


# ===========================================================================
# RED -- default scope: whitespace-only surfaced heading text.
# ===========================================================================


def test_red_default_whitespace_only_heading_text(tmp_path):
    """Matches the renderer's own strip-and-drop of a whitespace-only
    heading (render_obsidian.py) -- text.strip() != "" is the surfaced
    test, never a bare truthiness check."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "   \n\t  "})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


# ===========================================================================
# RED -- default scope: a REPEATED same (seg, block_id) key -- the
# Counter-vs-set proof (codex R2 MAJOR-1).
# ===========================================================================


def test_green_default_repeated_same_key_present_draft_satisfies_all_occurrences(tmp_path):
    """ped-ant PR#230 [P1]: the SAME block_id, repeated TWICE inside one
    segment's own block_ids[] (manifest.schema.json enforces no uniqueness
    there) -> C_source((seg01, HEAD:seg01)) == 2. draft.blocks{} is a
    plain dict KEYED by block_id -- exactly ONE string per id, never
    one-per-occurrence -- and assemble.py's own block loop
    (`for bid in block_ids:`) iterates BOTH occurrences and emits a node
    for EACH, both reading that SAME keyed string (the assembler
    REPLICATES one keyed draft across every occurrence; its own
    `duplicate_order_index` check iterates the unique manifest.blocks dict
    and never inspects a segment's own possibly-repeating block_ids[], so
    it does not and was never meant to reject this). This is schema-valid,
    assembler-supported content -- a PRESENT, non-empty draft block
    genuinely satisfies ALL of that key's declared occurrences in this
    (no-render) default scope, which structurally cannot represent
    per-occurrence drops at all (only key presence). Must be GREEN, not a
    false missing_heading. Occurrence-level drop detection belongs to the
    assembled_book scope instead, where per-occurrence nodes actually
    exist to be counted -- see
    test_red_assembled_book_duplicate_heading_occurrence_dropped_node
    below for that genuine case."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, (
        "a doubly-cited heading block_id with a present, non-empty draft "
        "must NOT false-RED -- the assembler replicates the single keyed "
        "draft string across both occurrences, so this is genuinely "
        "satisfied content, not a coverage defect\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert payload["defects"] == []


# ===========================================================================
# RED -- default scope: zero converged, declared headings present -- no
# vacuous pass on a fresh/all-not-started project.
# ===========================================================================


def test_red_default_zero_converged_with_declared_headings(tmp_path):
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})  # nothing converged; no draft exists at all

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": "HEAD:seg01", "kind": "missing_heading"} in payload["defects"]


def test_green_no_declared_headings_at_all_is_clean(tmp_path):
    """No-false-RED control: a book with no HEAD blocks and no declared
    heading_types yields an empty source Counter -- HARD clean by
    construction, when NOTHING is converged either (the reviewed-SHA rebind
    below iterates the ledger's own converged population, which is empty
    here). This is NOT "clean regardless of convergence state" in general
    -- see test_red_default_non_heading_segment_edited_after_review right
    below for the case where a converged (but stale-since-review) non-
    heading segment DOES flip this RED (codex BLOCKER 1)."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})  # zero converged -- irrelevant, nothing was ever declared

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert payload == {"defects": [], "warnings": []}


def test_red_default_non_heading_segment_edited_after_review(tmp_path):
    """codex BLOCKER 1: the reviewed-SHA rebind must cover EVERY converged
    segment in the ledger, not merely the heading-bearing subset the
    coverage invariant happens to iterate. An all-prose (no declared
    heading at all) segment that converged, then got hand-edited (or its
    draft silently replaced) AFTER review, must still RED -- the rebind is
    a general draft-trust guard, not scoped to headings. Before the fix,
    this segment never appears in source_counter (it declares no heading),
    so the old collect_default_output_markers(source_counter.keys(), ...)
    never even looked at it -- a converged-but-tampered non-heading draft
    sailed through silently."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)  # no declared heading_types at all

    write_draft(root, "seg01", {"PARA:seg01:0001": "Nekotoriy prozaicheskiy tekst."})
    # Deliberately WRONG reviewed_draft_sha1 -- simulates a hand edit that
    # landed strictly between the review that approved this (non-heading)
    # segment and this gate's own run.
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1": "0" * 40}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 1, (
        "an all-prose converged segment edited after review must RED via "
        "the reviewed-SHA rebind, even though it declares no heading at "
        "all\n" + proc.stdout + proc.stderr
    )
    payload = parse_stdout_json(proc)
    assert {"seg": "seg01", "block_id": None, "kind": "stale_review_since_audit"} in payload["defects"]


# ===========================================================================
# RED -- malformed manifest.json must fail CLOSED (exit 2), never silently
# coerce to an empty/degenerate population and pass open (codex BLOCKER 2).
# ===========================================================================


def test_red_malformed_manifest_scalar_heading_types(tmp_path):
    """A scalar string, if blindly coerced via frozenset(), decomposes into
    its own CHARACTERS -- frozenset("SIMAN") == {'S','I','M','A','N'} --
    silently dropping the real SIMAN declaration and letting a genuine
    SIMAN heading fall through as a mere WARN instead of a HARD miss."""
    root = make_root(tmp_path)
    blocks = {"SIMAN:seg01:0001": make_block("SIMAN", plain_text="Siman One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["SIMAN:seg01:0001"], "word_count": 2}]
    write_manifest(root, blocks, segments, heading_types="SIMAN")  # scalar, not an array -- the bug
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a scalar (non-array) heading_types must fail closed at exit 2, "
        "never silently coerce and fail open\n" + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_non_dict_blocks(tmp_path):
    root = make_root(tmp_path)
    (root / "manifest.json").write_text(json.dumps({
        "blocks": ["not", "a", "dict"],
        "spine": [], "segments": [], "footnotes": [], "frontback": [],
        "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_non_list_segments(tmp_path):
    root = make_root(tmp_path)
    (root / "manifest.json").write_text(json.dumps({
        "blocks": {}, "spine": [], "segments": {"not": "a list"},
        "footnotes": [], "frontback": [], "verse": {"store": []},
        "source_inputs": [], "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_heading_types_element_non_string(tmp_path):
    """codex round-3 FIX 1: a heading_types list that itself passes the
    container-type check (it IS a list) can still carry a non-string
    ELEMENT -- manifest.schema.json: heading_types.items.type == 'string'.
    A non-string element never equals any block's own string 'type' tag,
    so it silently never matches anything -- were manifest.heading_types
    meant to declare "SIMAN" but got mangled to [42], the real SIMAN
    heading present below is demoted to a mere WARN instead of counted."""
    root = make_root(tmp_path)
    blocks = {"SIMAN:seg01:0001": make_block("SIMAN", plain_text="Siman One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["SIMAN:seg01:0001"], "word_count": 2}]
    write_manifest(root, blocks, segments, heading_types=[42])  # non-string element -- the bug
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-string heading_types element must fail closed at exit 2, "
        "never silently coerce and fail open\n" + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_heading_types_present_null(tmp_path):
    """codex round-4 FIX 1: manifest.get("heading_types") returns None for
    BOTH an absent key and a PRESENT "heading_types": null -- a bare
    `is not None` guard silently treats present-null the SAME as absent
    (falls back to only "HEAD" declared), demoting the real SIMAN heading
    below to a mere WARN instead of counting it."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"SIMAN:seg01:0001": {
            "id": "SIMAN:seg01:0001", "type": "SIMAN", "order_index": 0,
            "source_file": "source.txt", "plain_text": "Siman One", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["SIMAN:seg01:0001"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
        "heading_types": None,  # literal JSON null (present key) -- the bug
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a present-but-null heading_types must fail closed at exit 2, "
        "never silently treated the same as an absent key\n"
        + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_heading_types_empty_string_element(tmp_path):
    """codex R5-4 (MAJOR, corrects an earlier WRONG reading of the schema):
    manifest.schema.json's heading_types.items carries `minLength: 1` --
    an earlier version of this gate explicitly declined to reject an
    empty string ("the schema also allows any non-empty string"), which
    misread the schema's own constraint (minLength:1 IS "must be
    non-empty"). An empty-string element can never match any real block's
    own 'type' tag, silently demoting the real SIMAN heading below to
    WARN-only, exactly like a non-string element."""
    root = make_root(tmp_path)
    blocks = {"SIMAN:seg01:0001": make_block("SIMAN", plain_text="Siman One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["SIMAN:seg01:0001"], "word_count": 2}]
    write_manifest(root, blocks, segments, heading_types=[""])  # empty string -- the bug
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an empty-string heading_types element must fail closed at exit 2, "
        "never silently coerce and fail open\n" + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_block_ids_non_list_string(tmp_path):
    """codex round-4 FIX 2: segments[].block_ids is REQUIRED, type
    array-of-string. A bare `for bid in seg_entry.get("block_ids") or []`
    would iterate a STRING's own CHARACTERS if block_ids were e.g.
    "HEAD:seg01" instead of ["HEAD:seg01"] -- a genuinely bogus
    per-character block_id sequence, never a graceful empty fallback."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": "HEAD:seg01", "word_count": 2}]  # string, not array -- the bug
    write_manifest(root, blocks, segments)
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-array block_ids must fail closed at exit 2, never silently "
        "iterate a string's own characters\n" + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_block_ids_non_string_element(tmp_path):
    """codex round-4 FIX 2, second case: block_ids IS an array (passes the
    container check) but carries a non-string ELEMENT -- a non-string id
    could never legitimately key manifest.blocks{}, so silently treating
    it as "absent" would mask a genuine schema violation as the chartered
    absent-block tolerance."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", 42], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_empty_block_ids(tmp_path):
    """codex R5-3 (MAJOR, corrects an earlier WRONG reading of the
    schema): manifest.schema.json's block_ids carries `minItems: 1` -- an
    earlier version of this gate explicitly (and wrongly) tolerated `[]`
    as "present, iterates nothing, therefore valid". A segment with zero
    block_ids contributes zero source markers no matter what it should
    have declared, silently shrinking source_counter just like the
    absent/null cases. A real HEAD block exists in this fixture's own
    manifest.blocks{} -- it is simply never cited."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": [], "word_count": 2}]  # empty -- the bug
    write_manifest(root, blocks, segments)
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an empty block_ids must fail closed at exit 2, never silently "
        "treat a segment with no declared blocks as contributing "
        "nothing\n" + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_seg_non_string(tmp_path):
    """codex round-4 FIX 7 (folded into the consolidated
    _validate_manifest_shape helper): a segments[] entry's own `seg`
    becomes a Counter-KEY component in collect_source_markers
    (`counter[(seg, bid)]`) -- an unhashable seg (e.g. a list) would
    otherwise crash with an uncaught TypeError. manifest.schema.json:
    segments.items.properties.seg is REQUIRED, type "string"."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": {
            "id": "HEAD:seg01", "type": "HEAD", "order_index": 0,
            "source_file": "source.txt", "plain_text": "Chapter One", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": [], "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],  # non-str seg -- the bug
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-string segments[].seg must fail closed at exit 2, never "
        "crash with an uncaught TypeError: unhashable type\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


def test_red_malformed_manifest_block_type_non_string(tmp_path):
    """codex round-4 FIX 8 (folded into the consolidated
    _validate_manifest_shape helper): a manifest.blocks{} entry's own
    `type` becomes an `in heading_types` membership operand in
    collect_source_markers -- an unhashable type (e.g. a list) would
    otherwise crash with an uncaught TypeError."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": {
            "id": "HEAD:seg01", "type": [], "order_index": 0,  # non-str type -- the bug
            "source_file": "source.txt", "plain_text": "Chapter One", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-string block 'type' must fail closed at exit 2, never crash "
        "with an uncaught TypeError: unhashable type\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


def test_red_malformed_manifest_block_type_absent(tmp_path):
    """codex R5-1 (BLOCKER, corrects an earlier WRONG judgment call):
    block `type` is REQUIRED on EVERY block (manifest.schema.json:
    blocks.additionalProperties.required includes "type"). An earlier
    version of this gate explicitly TOLERATED an absent type (reasoning:
    "None in heading_types is harmless, never a match, never a crash") --
    that reasoning missed the actual fail-open hole: a cited HEAD block
    that lost its own 'type' field silently drops out of source_counter
    entirely (never counted as a heading, never even WARNed about),
    shrinking the HARD population without a trace."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": {
            "id": "HEAD:seg01", "order_index": 0,  # 'type' OMITTED entirely -- the bug
            "source_file": "source.txt", "plain_text": "Chapter One", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a cited block with an ABSENT 'type' field must fail closed at "
        "exit 2, never silently drop out of source_counter\n"
        + proc.stdout + proc.stderr
    )


def test_red_malformed_manifest_block_type_null(tmp_path):
    """codex R5-1, companion case: "type": null (a present key, JSON
    null) is likewise not a string and must fail closed the same way as
    an absent key or a non-string scalar."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": {
            "id": "HEAD:seg01", "type": None, "order_index": 0,  # explicit null -- the bug
            "source_file": "source.txt", "plain_text": "Chapter One", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_block_seg_non_string(tmp_path):
    """codex R5-7 (MINOR): a manifest.blocks{} entry's own `seg` field
    (string-or-null per manifest.schema.json) is embedded directly into a
    warning entry by collect_undeclared_heading_like_warnings -- a
    non-string, non-null value would leak a malformed value straight
    into that report."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"TITLE:seg01:0001": {
            "id": "TITLE:seg01:0001", "type": "TITLE", "seg": [],  # non-str, non-null seg -- the bug
            "order_index": 0, "source_file": "source.txt",
            "plain_text": "Some prose tagged TITLE.", "sha1": "0" * 40,
        }},
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["TITLE:seg01:0001"], "word_count": 5}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_uncited_block_non_dict_fails_closed(tmp_path):
    """codex round-4 FIX 10: collect_undeclared_heading_like_warnings
    iterates EVERY manifest.blocks{} value, including UNCITED ones (never
    named by any segment's own block_ids[]) -- a path FIX 3's cited-only
    guard never covered across three review rounds.

    CORRECTED CLAIM (verified by directly re-running this exact fixture
    against the pre-F10 script via the scratch-copy-swap method): the
    codex review's own prose predicted an uncaught AttributeError crash
    here, but that collector already carried its OWN local
    `isinstance(mb, dict)` guard since round 1 -- so the pre-fix behavior
    is a SILENT SKIP (the orphan is simply never WARNed about), not a
    crash; rc stayed 1 (from the unrelated real HEAD:seg01 heading, which
    this fixture still counts correctly), never rc 0 or an uncaught
    traceback. The genuine gap F10 closes is narrower but still real: a
    malformed manifest element was tolerated in this ONE code path (WARN
    generation) while being fatal in every OTHER path that reads
    manifest_blocks -- an inconsistency the consolidated
    _validate_manifest_shape helper now closes uniformly, so a malformed
    manifest is rejected the SAME way regardless of which collector would
    have touched the bad element first."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {
            "HEAD:seg01": {
                "id": "HEAD:seg01", "type": "HEAD", "order_index": 0,
                "source_file": "source.txt", "plain_text": "Chapter One", "sha1": "0" * 40,
            },
            "orphan": None,  # UNCITED by any segment's own block_ids[] -- the bug
        },
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an uncited manifest.blocks{} entry with a malformed (non-dict) "
        "value must fail closed at exit 2, never silently skipped (WARN "
        "generation must treat a malformed manifest identically to every "
        "other code path)\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


def test_red_malformed_manifest_non_dict_segment_entry_with_heading_elsewhere(tmp_path):
    """codex round-3 FIX 2: a segments[] entry that is not itself an object
    must fail closed -- even when a DIFFERENT, well-formed entry in the
    SAME array legitimately carries a real declared heading. Silently
    skipping only the bad entry (rather than failing the whole gate) would
    let a malformed manifest ship as if the corrupted entry's own
    block_ids (potentially including a real heading of its own) simply
    never existed."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [
        {"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2},
        "NOT_AN_OBJECT",  # malformed entry -- the bug
    ]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_present_block_value_non_dict(tmp_path):
    """codex round-3 FIX 3: a manifest.blocks{} entry that IS present (the
    cited block_id resolves to something) but is not itself an object must
    fail closed. (As of codex R5-2, a DANGLING block_id -- one that names
    NO manifest.blocks{} entry at all -- is ALSO fail-closed now, not a
    tolerated case; see test_red_dangling_block_id_reference_fails_closed.
    This test's own scenario -- a PRESENT but wrong-shaped value -- is a
    distinct malformed-shape case, unaffected by that reversal.)"""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": "NOT_AN_OBJECT"},  # present but malformed -- the bug
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_malformed_manifest_present_null_block_value(tmp_path):
    """codex round-4 FIX 3: a manifest.blocks{} entry that IS present with
    an EXPLICIT JSON null value must fail closed -- `.get(bid) is None`
    cannot distinguish "no entry at all" from "a present-null entry", so
    the fix uses `bid in manifest_blocks` membership instead. (As of codex
    R5-2, the "no entry at all" case is ALSO fail-closed now -- see
    test_red_dangling_block_id_reference_fails_closed, which replaced the
    old green/tolerated companion this docstring used to point at.)"""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {"HEAD:seg01": None},  # present, explicit null -- the bug
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_dangling_block_id_reference_fails_closed(tmp_path):
    """codex R5-2 (BLOCKER, REVERSES an earlier ratified tolerance): a
    block_id cited by a segment's own block_ids[] that names NO
    manifest.blocks{} entry AT ALL used to be tolerated (reasoning:
    "an extraction-level defect outside this gate's own charter"). That
    reasoning was itself a fail-open hole, per manifest.schema.json's own
    description of block_ids -- "keys into the top-level blocks{}
    object" -- a dangling citation is an INTERNAL CONSISTENCY violation
    (was this deleted block supposed to be a declared heading?
    unknowable), not an out-of-charter nicety. Deleting a block record
    while leaving its citation in place must now fail closed."""
    root = make_root(tmp_path)
    manifest = {
        "blocks": {},  # "HEAD:seg01" is cited below but has NO entry here at all
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}],
        "footnotes": [], "frontback": [], "verse": {"store": []}, "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a dangling block_id citation (no matching manifest.blocks{} "
        "entry at all) must fail closed at exit 2, never silently "
        "tolerated as an out-of-charter nicety\n" + proc.stdout + proc.stderr
    )


# ===========================================================================
# SECURITY -- pre-commit security-review findings, gate-scoped only (the
# two sibling consumers final_audit.py/assemble.py share the SAME
# unsanitized-seg-key primitive but are pre-existing and out of THIS PR's
# scope -- filed as a follow-up, not touched here).
# ===========================================================================


def test_red_ledger_seg_key_path_traversal_fails_closed(tmp_path):
    """SECURITY (MEDIUM, path-traversal): a ledger segments{} KEY becomes
    draft_path(seg) = SEGMENTS_DIR / f"{seg}.draft.json" via
    _rebind_or_flag_stale -- an unsanitized key like "../../outside_target"
    reads a file OUTSIDE segments/ entirely. ledger.schema.json has no
    propertyNames pattern constraining this key's own shape, and neither
    the record-dict guard nor the status-enum guard validates the KEY
    itself.

    This test proves the primitive DIFFERENTIALLY rather than merely
    asserting an exit code: the planted "outside" file's own `blocks`
    field is deliberately malformed (a list, not a dict) -- if the
    traversal read is ever GENUINELY attempted, THAT file's own
    structural defect fires a content-specific _MalformedArtifact
    ("not an object"), proving the read actually happened. Post-fix, the
    seg KEY itself must be rejected at ingestion -- BEFORE any
    draft_path() is ever built from it -- so the outside file's own
    content-defect message must never appear at all, only a message
    naming the rejected key."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})

    # Plant a file OUTSIDE segments/ -- SEGMENTS_DIR is
    # {tmp_path}/durable_root/segments, so two levels up reaches tmp_path.
    outside_draft = {
        "seg": "whatever", "blocks": ["not", "a", "dict"],  # deliberately malformed -- the tell
        "footnotes": {}, "verses": {}, "names": [], "notes": [],
    }
    outside_path = tmp_path / "outside_target.draft.json"
    outside_path.write_text(json.dumps(outside_draft, ensure_ascii=False), encoding="utf-8")
    outside_sha1 = draft_content_sha1_of(outside_draft)

    traversal_seg = "../../outside_target"
    write_ledger(root, {
        "seg01": {"status": "converged"},
        traversal_seg: {"status": "converged", "reviewed_draft_sha1": outside_sha1},
    })

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a ledger segment key that path-traverses outside segments/ must "
        "fail closed at exit 2, never be silently accepted as a valid "
        "segment id\n" + proc.stdout + proc.stderr
    )
    assert f"segment key {traversal_seg!r}" in proc.stderr, (
        "the rejection must name the offending seg KEY itself -- proving "
        "it was rejected at ingestion\n" + proc.stderr
    )
    assert "not an object" not in proc.stderr, (
        "the outside file's own content-defect message must NEVER appear "
        "-- its presence would mean the traversal read was genuinely "
        "attempted before the key was ever validated\n" + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_manifest_deeply_nested_json_recursion_error_fails_closed(tmp_path):
    """SECURITY (LOW, uncaught RecursionError): deeply-nested adversarial
    JSON makes json.loads() raise RecursionError -- a RuntimeError
    subclass, NOT a ValueError -- which escapes load_json's own except
    tuple as an uncaught traceback + Python's default exit 1,
    misclassifying a malicious/corrupt input as an internal crash instead
    of a clean env/usage precondition. 300000 levels of nesting reliably
    triggers a genuine C-stack RecursionError on CPython's C-accelerated
    JSON decoder (confirmed empirically at ~200000 on this build; 300000
    gives headroom) while parsing in single-digit milliseconds."""
    root = make_root(tmp_path)
    depth = 300_000
    (root / "manifest.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "deeply-nested JSON in manifest.json must fail closed at exit 2, "
        "never crash with an uncaught RecursionError traceback and "
        "Python's default exit 1\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_malformed_ledger_record_non_dict(tmp_path):
    """codex round-3 FIX 4: a runs/ledger.json segments{} record that is
    not itself an object must fail closed -- ledger.schema.json."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": "NOT_AN_OBJECT"}}), encoding="utf-8"
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_ledger_status_typo_dodges_rebind_reincarnates_blocker1(tmp_path):
    """codex round-3 FIX 5 -- the important one: a ledger status value
    OUTSIDE LEDGER_STATUS_ENUM (a typo, e.g. "convergd" for "converged") on
    a GENUINELY-converged, ALL-PROSE segment whose draft was hand-edited
    after review. Before this fix, `record.get("status") != "converged"`
    treats the typo as simply "not converged" and silently skips the
    rebind -- and being all-prose (no declared heading at all), the
    coverage invariant never looks at this segment either, so the tampered
    draft sails through completely undetected: BLOCKER-1 reincarnated via
    a different vector. Must fail closed at exit 2 (cannot classify an
    unrecognized status), never silently treated as non-converged."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)  # no declared heading_types at all

    write_draft(root, "seg01", {"PARA:seg01:0001": "Nekotoriy prozaicheskiy tekst."})
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {"status": "convergd", "reviewed_draft_sha1": "0" * 40}}}),
        encoding="utf-8",
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an unrecognized ledger status must fail closed at exit 2, never "
        "silently treated as 'not converged' and skipped -- a typo'd "
        "status on a genuinely-converged, tampered, all-prose segment must "
        "not sail through undetected\n" + proc.stdout + proc.stderr
    )


def test_red_ledger_status_unhashable_list(tmp_path):
    """codex round-4 FIX 4: `status not in LEDGER_STATUS_ENUM` (a frozenset
    membership test hashes its operand) raises TypeError: unhashable type
    for a list/dict status value -- the isinstance(str) guard must come
    FIRST (and short-circuit via `or`) so this never escapes as an
    uncaught traceback."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {"status": [], "reviewed_draft_sha1": "0" * 40}}}),
        encoding="utf-8",
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an unhashable (list) ledger status must fail closed at exit 2, "
        "never crash with an uncaught TypeError: unhashable type\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


def test_red_ledger_status_null(tmp_path):
    """codex round-4 FIX 4, companion case: status: null (present key,
    JSON null) is likewise not a string and must fail closed."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {"status": None, "reviewed_draft_sha1": "0" * 40}}}),
        encoding="utf-8",
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_red_converged_draft_corrupt_utf8_fails_closed_at_env_error(tmp_path):
    """codex round-3 FIX 6 (finding #5 completion): a converged segment's
    draft that cannot be DECODED at all is a corrupt artifact this gate
    cannot evaluate -- must fail env/usage (exit 2), never folded into the
    same HARD-defect (exit 1) bucket as a genuine reviewed-SHA mismatch."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    (root / "segments" / "seg01.draft.json").write_bytes(b'{"seg": "seg01", "blocks": {"x": "\xff\xfe"}}')
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {"status": "converged", "reviewed_draft_sha1": "0" * 40}}}),
        encoding="utf-8",
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a corrupt (invalid-UTF-8) converged draft must fail closed at "
        "exit 2 (env error), never exit 1 (folded into a HARD "
        "stale_review_since_audit defect)\n" + proc.stdout + proc.stderr
    )


def test_red_converged_draft_invalid_json_fails_closed_at_env_error(tmp_path):
    """codex round-3 FIX 6, second case: a converged draft that decodes
    fine but is not valid JSON at all -- same env-exit-2 treatment as the
    corrupt-UTF-8 case above, not folded into a HARD defect either."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    (root / "segments" / "seg01.draft.json").write_text("{not valid json", encoding="utf-8")
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {"status": "converged", "reviewed_draft_sha1": "0" * 40}}}),
        encoding="utf-8",
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a converged draft with invalid JSON must fail closed at exit 2 "
        "(env error), never exit 1\n" + proc.stdout + proc.stderr
    )


def test_red_trusted_draft_blocks_non_dict_truthy_list(tmp_path):
    """codex round-4 FIX 5: a SHA-bound (genuinely trusted, rebind-passing)
    converged draft whose own 'blocks' field is a TRUTHY non-dict (e.g. a
    list) survives the old `.get("blocks") or {}` fence unchanged -- a
    truthy value is never replaced by the `or` fallback -- then
    `["text"].get(bid)` raises an uncaught AttributeError. draft.blocks is
    REQUIRED, type object; a trusted-but-structurally-corrupt draft is a
    malformed artifact (exit 2), never silently folded into a coverage
    RED or, worse, an uncaught crash."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)

    draft = {"seg": "seg01", "blocks": ["text"], "footnotes": {}, "verses": {}, "names": [], "notes": []}
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / "seg01.draft.json").write_bytes(draft_bytes)
    expected_sha1 = draft_content_sha1_of(draft)  # genuinely matches -- the draft PASSES the rebind
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1": expected_sha1}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a trusted (SHA-matching) converged draft whose own 'blocks' field "
        "is a non-dict must fail closed at exit 2, never crash with an "
        "uncaught AttributeError\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


def test_red_all_prose_trusted_draft_blocks_non_dict(tmp_path):
    """codex R5-5 (MAJOR): the blocks-must-be-a-dict guard used to live in
    collect_default_output_markers, gated behind the heading-bearing
    `source_keys` loop -- so it only ever ran for segments that OWN at
    least one declared-heading key. An ALL-PROSE converged segment (no
    heading at all) with a SHA-matching-but-structurally-corrupt draft
    was never checked -- the exact same heading-bearing-subset blind spot
    BLOCKER-1 (R4) fixed for the reviewed-SHA rebind itself. The guard now
    lives in _rebind_or_flag_stale, which runs for EVERY converged
    segment regardless of whether it declares a heading."""
    root = make_root(tmp_path)
    blocks = {"PARA:seg01:0001": make_block("PARA", plain_text="Just prose, no headings.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}]
    write_manifest(root, blocks, segments)  # no declared heading_types at all

    draft = {"seg": "seg01", "blocks": ["text"], "footnotes": {}, "verses": {}, "names": [], "notes": []}
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / "seg01.draft.json").write_bytes(draft_bytes)
    expected_sha1 = draft_content_sha1_of(draft)  # genuinely matches -- the draft PASSES the rebind
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1": expected_sha1}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an all-prose converged segment's SHA-matching-but-structurally-"
        "corrupt draft must fail closed at exit 2, even though it "
        "declares no heading at all and the old (pre-R5-5) coverage-gated "
        "guard would never have looked at it\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, "must report a clean ERROR line, never leak a raw Python traceback"


# ===========================================================================
# RED -- an unrecognized output.v1_scope must fail CLOSED (exit 2), never
# silently fall through to the default scope's own gate (codex MAJOR 2).
# ===========================================================================


def test_red_bogus_v1_scope_fails_closed(tmp_path):
    """vd.load_profile() (validate_draft.py) does NOT run jsonschema
    validation -- profile.schema.json's own output.v1_scope enum is not
    already enforced upstream of this script's read, so this check is
    load-bearing, not defensive-redundant (confirmed by reading
    validate_draft.py's own load_profile(), which only yaml.safe_load()s
    and isinstance(dict)-checks -- no schema validation anywhere)."""
    root = make_root(tmp_path, v1_scope="assembled-boook")  # typo, not a real enum value
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an unrecognized output.v1_scope must fail closed at exit 2, never "
        "silently fall through to the default scope's own gate\n"
        + proc.stdout + proc.stderr
    )


# ===========================================================================
# RED -- invalid UTF-8 must fail CLOSED (exit 2, a clean env-error report),
# never crash with an uncaught UnicodeDecodeError traceback (codex MINOR 1).
# ===========================================================================


def test_red_invalid_utf8_manifest_fails_closed(tmp_path):
    root = make_root(tmp_path)
    # 0xff is never a valid UTF-8 lead byte -- guaranteed to raise
    # UnicodeDecodeError on decode, never merely a JSONDecodeError.
    (root / "manifest.json").write_bytes(b'{"blocks": {"x": "\xff\xfe"}}')
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "invalid UTF-8 in manifest.json must fail closed at exit 2 (a clean "
        "env/usage precondition), never crash with an uncaught "
        "UnicodeDecodeError traceback and Python's default exit 1\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_oversized_integer_manifest_fails_closed(tmp_path):
    """codex R8-1 (the 10th hole, closes the class): a syntactically-valid
    JSON document can still make json.loads() raise a BARE ValueError that
    is NOT json.JSONDecodeError -- a huge integer literal trips Python's
    own int-string conversion digit limit (default 4300 digits). Before
    R8-1, load_json's own except tuple named json.JSONDecodeError/
    UnicodeDecodeError explicitly but missed this sibling ValueError
    entirely, so it escaped uncaught as a raw traceback + exit 1."""
    root = make_root(tmp_path)
    # 5000 digits reliably trips the default 4300-digit limit.
    (root / "manifest.json").write_text(
        '{"order_index": ' + ("9" * 5000) + "}", encoding="utf-8"
    )
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "an oversized integer literal in manifest.json must fail closed at "
        "exit 2, never crash with an uncaught bare ValueError traceback "
        "and Python's default exit 1\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_invalid_utf8_profile_fails_closed(tmp_path):
    """codex R6-1 (MAJOR, the 8th hole): vd.load_profile() (validate_draft.py)
    reads profile.yml via `.read_text(encoding="utf-8")` inside a try/except
    that ONLY ever catches yaml.YAMLError -- an OSError or UnicodeDecodeError
    from that read (or from the ownership-marker read just before it) is
    caught by NEITHER except clause and escapes uncaught all the way to
    THIS script's own call site. Mirrors
    test_red_invalid_utf8_manifest_fails_closed above, one level removed."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    # 0xff is never a valid UTF-8 lead byte -- overwrite the already-valid
    # profile.yml make_root() wrote with invalid UTF-8 bytes.
    (root / "profile.yml").write_bytes(b"output:\n  v1_scope: \xff\xfe\n")
    write_ledger(root, {})

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "invalid UTF-8 in profile.yml must fail closed at exit 2 (a clean "
        "env/usage precondition), never crash with an uncaught "
        "UnicodeDecodeError traceback and Python's default exit 1\n"
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_unreadable_profile_fails_closed(tmp_path):
    """codex R6-1, companion case: an OSError from profile.yml's own read
    (e.g. permission denied) must likewise fail closed, not crash --
    neither of vd.load_profile()'s two try/excepts catch a bare OSError
    from the profile-content read itself (only the marker read does)."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})
    profile_path = root / "profile.yml"
    profile_path.chmod(0o000)
    try:
        proc = run_validate_assembled(root)
    finally:
        profile_path.chmod(0o644)  # restore so tmp_path cleanup can remove it
    assert proc.returncode == 2, (
        "an unreadable (permission-denied) profile.yml must fail closed at "
        "exit 2, never crash with an uncaught OSError/PermissionError "
        "traceback\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_red_malformed_ownership_marker_wrong_shape_owner_path(tmp_path):
    """codex R7-1 (the 9th hole): a VALID-JSON ownership marker whose own
    `owner_profile_path` is a non-string, truthy scalar (e.g. `1`) passes
    load_profile()'s own `if not owner_profile_path:` truthiness check
    and reaches `Path(owner_profile_path)` -- `Path(1)` raises TypeError,
    a type neither R6-1's `(OSError, UnicodeDecodeError)` tuple nor any
    schema-shape guard elsewhere in this file was ever going to
    anticipate. This is exactly why R7-1 broadened the catch to `except
    Exception` instead of adding a named 3rd/4th/Nth type -- a black-box
    config-load boundary's failure modes are enumerated one at a time
    only until the next exotic shape surfaces."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})
    # Overwrite the ownership marker make_root() wrote with one whose
    # owner_profile_path is a non-string, truthy scalar -- valid JSON,
    # wrong shape.
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": 1}), encoding="utf-8"
    )

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a non-string owner_profile_path (valid JSON, wrong shape) must "
        "fail closed at exit 2, never crash with an uncaught TypeError "
        "from Path(1)\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )


def test_green_load_profile_own_fatal_systemexit_still_reaches_exit_2(tmp_path):
    """REGRESSION GUARD for R7-1's broadened catch: `except Exception`
    deliberately does NOT catch `SystemExit` (a BaseException, not an
    Exception subclass) -- load_profile()'s OWN detected-error path (a
    genuinely malformed profile.yml that fails to parse as YAML at all)
    calls validate_draft.py's own `_fatal()`, which `sys.exit(2)`s
    directly. Proves that SystemExit(2) still propagates through this
    script's own broadened try/except completely untouched.

    codex R8-2: `returncode == 2` + no-traceback ALONE is VACUOUS proof of
    propagation -- a WRONG `except BaseException` (swallowing SystemExit,
    then this script's own wrapper re-raising it as an outer `_fatal()`)
    would satisfy that exact same assertion, exit 2, no traceback, just
    via the WRONG code path. The genuine distinguishing signal is WHICH
    error message reaches stderr: real propagation means
    load_profile()'s OWN inner message ("... is not valid YAML: ...",
    verified verbatim by direct reproduction) reaches stderr UNCHANGED;
    a swallow-and-rewrap would instead print THIS script's own outer
    wrapper string ("could not load profile.yml (via validate_draft.py's
    own profile loader)") -- asserting the inner message IS present AND
    the outer wrapper string is ABSENT is what actually proves
    propagation, not merely a matching exit code."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_ledger(root, {})
    # Genuinely invalid YAML (an unterminated flow mapping) -- triggers
    # load_profile()'s own `except yaml.YAMLError` -> `_fatal()` ->
    # `sys.exit(2)` path, NOT this script's own broadened `except Exception`.
    (root / "profile.yml").write_text("output: [unterminated\n", encoding="utf-8")

    proc = run_validate_assembled(root)
    assert proc.returncode == 2, (
        "a genuinely malformed-YAML profile.yml must still exit 2 via "
        "load_profile()'s own internal _fatal()/SystemExit(2) path -- "
        "the broadened `except Exception` in this script must NOT mask "
        "or otherwise interfere with it\n" + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr, (
        "must report a clean ERROR line, never leak a raw Python traceback"
    )
    assert "is not valid YAML" in proc.stderr, (
        "load_profile()'s OWN inner error message must reach stderr "
        "UNCHANGED -- its absence would mean SystemExit did NOT genuinely "
        "propagate\n" + proc.stderr
    )
    assert "could not load profile.yml" not in proc.stderr, (
        "this script's own OUTER wrapper message must NOT appear -- its "
        "presence would mean SystemExit was wrongly caught and rewrapped "
        "rather than propagating untouched\n" + proc.stderr
    )


# ===========================================================================
# GREEN -- clean uniform set, and the cross-reference-safe case (a marker
# re-cited BY NUMBER in another chapter's own prose must never be
# double-counted -- it is not a block_ids[] reference).
# ===========================================================================


def test_green_clean_uniform_set_and_cross_reference_mention(tmp_path):
    root = make_root(tmp_path)
    blocks = {
        "HEAD:seg01": make_block("HEAD", plain_text="Chapter One"),
        "PARA:seg01:0001": make_block("PARA", plain_text="See Chapter One for background.", order_index=1),
    }
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "PARA:seg01:0001"], "word_count": 8}]
    write_manifest(root, blocks, segments)

    write_draft(root, "seg01", {
        "HEAD:seg01": "Glava Odna",
        # A prose block's own translated text legitimately MENTIONS the
        # heading's own title -- never a block_ids[] reference, so it must
        # never be double-counted as a second source marker or otherwise
        # perturb the invariant.
        "PARA:seg01:0001": "Smotri Glava Odna dlya konteksta.",
    })
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout_json(proc)["defects"] == []


# ===========================================================================
# WARN-only -- an undeclared block whose type looks heading-shaped, never
# gating the exit code.
# ===========================================================================


def test_warn_only_undeclared_heading_like_block(tmp_path):
    root = make_root(tmp_path)
    blocks = {"TITLE:seg01:0001": make_block("TITLE", plain_text="Some prose tagged TITLE.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["TITLE:seg01:0001"], "word_count": 5}]
    write_manifest(root, blocks, segments)  # heading_types absent -> TITLE is undeclared

    write_draft(root, "seg01", {"TITLE:seg01:0001": "Nekotoriy prozaicheskiy tekst."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert payload["defects"] == []
    assert any(
        w["kind"] == "undeclared_heading_like" and w["block_id"] == "TITLE:seg01:0001" and w["raw_type"] == "TITLE"
        for w in payload["warnings"]
    ), payload


def test_warn_does_not_fire_for_a_type_only_substring_matching_the_allowlist(tmp_path):
    """Negative control for the WARN regex: PARTICLE contains the substring
    "PART" but must NOT match -- the allowlist is an exact (fullmatch), not
    substring, comparison."""
    root = make_root(tmp_path)
    blocks = {"PARTICLE:seg01:0001": make_block("PARTICLE", plain_text="Not a heading.")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["PARTICLE:seg01:0001"], "word_count": 3}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"PARTICLE:seg01:0001": "Ne zagolovok."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse_stdout_json(proc)["warnings"] == []


def test_declared_heading_type_never_also_warns(tmp_path):
    """A DECLARED heading_types entry must never also surface as an
    'undeclared_heading_like' WARN -- it is, by construction, declared."""
    root = make_root(tmp_path)
    blocks = {"CHAPTER:seg01:0001": make_block("CHAPTER", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["CHAPTER:seg01:0001"], "word_count": 2}]
    write_manifest(root, blocks, segments, heading_types=["CHAPTER"])
    write_draft(root, "seg01", {"CHAPTER:seg01:0001": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout_json(proc)
    assert payload["defects"] == []
    assert payload["warnings"] == []


# ===========================================================================
# Mutation-proof tests -- IN-PROCESS module loading (never a subprocess:
# these need to monkeypatch/directly call the loaded module's own
# functions). The module is loaded from a COPIED fixture path so its own
# self-anchored DURABLE_ROOT resolves against a real fixture.
# ===========================================================================


# validate_assembled.py's own module-level code does a PLAIN `import
# validate_draft as vd` (after sys.path.insert(0, its own SCRIPTS_DIR)) --
# Python resolves that against the GLOBAL sys.modules cache by the bare
# name "validate_draft" alone, regardless of which fixture's copy the
# CALLER intended. validate_draft.py computes DURABLE_ROOT = Path(__file__)
# .resolve().parents[1] ONCE, at first exec -- so if an EARLIER in-process
# module load anywhere else in the same pytest session (this file's own
# repeated calls, or another test file's own importlib-based probe of a
# sibling-importing script, e.g. final_audit.py/assemble.py, which follow
# the identical sys.path.insert + `import validate_draft` pattern) already
# cached sys.modules['validate_draft'] from a DIFFERENT tmp_path fixture,
# our own script would silently bind to that STALE module and resolve
# profile.yml against the WRONG (possibly already torn-down) fixture root
# -- a real, observed full-suite-order flake, not a hypothetical.
_SIBLING_MODULE_NAMES = ("validate_draft",)


@contextlib.contextmanager
def _hermetic_sibling_imports():
    """Snapshots and clears the sys.modules entries validate_assembled.py's
    own sibling import could collide on, forcing a FRESH import bound to
    THIS call's scripts_dir, then restores whatever was cached before --
    so this test is neither a victim of, nor itself a source of,
    suite-order-dependent sys.modules pollution."""
    saved = {name: sys.modules.pop(name, None) for name in _SIBLING_MODULE_NAMES}
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _load_module_from_copy(scripts_dir: Path):
    with _hermetic_sibling_imports():
        spec = importlib.util.spec_from_file_location(
            "validate_assembled_probe", scripts_dir / "validate_assembled.py"
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _run_main_in_process(mod):
    """Calls mod.main() in-process (so a caller-applied monkeypatch on `mod`
    is actually exercised) with argv pinned to the single self-anchored
    element main() itself requires, returning (exit_code, parsed_stdout_json)."""
    old_argv = sys.argv
    sys.argv = ["validate_assembled.py"]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                mod.main()
                code = 0
            except SystemExit as exc:
                code = 0 if exc.code is None else (exc.code if isinstance(exc.code, int) else 1)
    finally:
        sys.argv = old_argv
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    payload = json.loads(lines[-1]) if lines else None
    return code, payload


def test_mutation_proof_neutered_invariant_wrongly_passes_the_red_fixture(monkeypatch, tmp_path):
    """(a) Monkeypatches compute_missing_heading_defects to an always-clean
    no-op and re-runs a GENUINELY red default-scope fixture -- proving a
    neutered invariant genuinely WOULD slip an incomplete book through
    undetected, so the real (non-monkeypatched) RED assertion elsewhere in
    this file is not itself vacuous.

    ped-ant PR#230 [P1]: this test's fixture used to be the repeated-key
    one (block_ids ["HEAD:seg01", "HEAD:seg01"]) -- but that fixture is
    NOW correctly GREEN in the default scope (a present, non-empty draft
    satisfies ALL declared occurrences of a doubly-cited heading; see
    test_green_default_repeated_same_key_present_draft_satisfies_all_occurrences),
    so it would no longer RED and this proof would be vacuous by
    construction. Retargeted to mirror
    test_mutation_proof_green_fixture_flips_red_on_dropped_key's own
    dropped-text fixture instead -- a single declared HEAD whose draft
    text is empty is unambiguously a real default-scope defect regardless
    of the multiplicity fix."""
    root = make_root(tmp_path)
    scripts_dir = root / "scripts"
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": ""})
    write_ledger(root, {"seg01": {"status": "converged"}})

    mod = _load_module_from_copy(scripts_dir)

    code, payload = _run_main_in_process(mod)
    assert code == 1 and payload and payload["defects"], "sanity: the real invariant must RED this dropped-heading-text fixture"

    monkeypatch.setattr(mod, "compute_missing_heading_defects", lambda *_a, **_kw: [])
    code2, payload2 = _run_main_in_process(mod)
    assert code2 == 0 and payload2 is not None and payload2["defects"] == [], (
        "a neutered (no-op) invariant check wrongly reports this RED "
        "fixture as clean -- proving the harness genuinely exercises the "
        "invariant, not a vacuous always-pass stub"
    )


def test_mutation_proof_green_fixture_flips_red_on_dropped_key(tmp_path):
    """Companion sanity check: the GREEN fixture used elsewhere in this file
    is not itself an always-pass no-op -- deliberately dropping the ONLY
    declared heading's translated text (the exact defect class this gate
    exists to catch) flips it RED."""
    root = make_root(tmp_path)
    blocks = {"HEAD:seg01": make_block("HEAD", plain_text="Chapter One")}
    segments = [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01"], "word_count": 2}]
    write_manifest(root, blocks, segments)
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_assembled(root)
    assert proc.returncode == 0, "sanity: a genuinely clean fixture must be GREEN before mutating it"

    write_draft(root, "seg01", {"HEAD:seg01": ""})
    write_ledger(root, {"seg01": {"status": "converged"}})  # re-bind sha1 to the mutated draft

    proc2 = run_validate_assembled(root)
    assert proc2.returncode == 1, (
        "dropping the ONLY declared heading's translated text must flip "
        "this fixture RED -- if it doesn't, the GREEN baseline elsewhere "
        "in this file is meaningless"
    )


def test_mutation_proof_counter_vs_set_repeated_key(tmp_path):
    """(b) Direct proof that the invariant genuinely depends on Counter
    MULTIPLICITY, not mere key presence (codex R2 MAJOR-1). Builds the same
    repeated-key source population as the RED test above via the REAL
    collect_source_markers(), confirms the real Counter-based comparison
    correctly flags it, then mutates: collapses the source side to
    set-like presence (every count forced to 1 -- exactly what a plain
    `set()` in place of `Counter()` would produce for the counting step)
    and re-runs the SAME comparison function -- proving that mutant would
    WRONGLY pass. A set-based reimplementation could never survive this.

    This is a PURE function-level test of `compute_missing_heading_defects`
    directly (never going through `collect_default_output_markers`, which
    ped-ant PR#230 [P1] now makes credit the FULL source multiplicity for a
    present default-scope draft block -- see
    test_green_default_repeated_same_key_present_draft_satisfies_all_occurrences).
    `output_counter={key: 1}` below does NOT represent what a present
    default-scope draft produces anymore; it represents a genuinely DROPPED
    emitted NODE in the `assembled_book` scope instead -- source declares
    the heading TWICE (two real block_ids[] occurrences) but assemble.py
    only emitted ONE surviving node for that key. That is the scope where
    per-occurrence multiplicity is a physical, countable fact (see
    test_red_assembled_book_duplicate_heading_occurrence_dropped_node
    below for the full end-to-end version of exactly this scenario) --
    `compute_missing_heading_defects` itself is scope-agnostic (a pure
    `got < want` comparison) and unchanged by the P1 fix, so this
    lower-level Counter-vs-set proof stays valid regardless of which
    scope the numbers came from."""
    root = make_root(tmp_path)
    scripts_dir = root / "scripts"
    mod = _load_module_from_copy(scripts_dir)

    manifest_segments = [{"seg": "seg01", "block_ids": ["HEAD:seg01", "HEAD:seg01"]}]
    manifest_blocks = {"HEAD:seg01": {"type": "HEAD"}}
    heading_types = frozenset({"HEAD"})

    source_counter = mod.collect_source_markers(manifest_segments, manifest_blocks, heading_types)
    assert source_counter[("seg01", "HEAD:seg01")] == 2, "fixture sanity: the repeated block_id must accumulate to count 2"

    # Stands in for the assembled_book scope's own C_output: assemble.py
    # emitted only ONE surviving heading node for a key declared TWICE.
    output_counter = Counter({("seg01", "HEAD:seg01"): 1})

    real_defects = mod.compute_missing_heading_defects(source_counter, output_counter, set())
    assert real_defects, "the real Counter-based invariant must flag the dropped repeated occurrence"

    # -- mutant: source multiplicity collapsed to set-like presence --
    mutant_source = Counter({key: 1 for key in source_counter})
    mutant_defects = mod.compute_missing_heading_defects(mutant_source, output_counter, set())
    assert not mutant_defects, (
        "a set-based (multiplicity-blind) source collector scores "
        "C_source==1 for this key -- 1 >= 1 wrongly PASSES, silently "
        "hiding the dropped repeated occurrence; this is the exact "
        "regression the Counter requirement in the module docstring "
        "guards against"
    )


def test_major1_rebind_reads_draft_bytes_exactly_once(tmp_path, monkeypatch):
    """codex MAJOR 1 (TOCTOU): the reviewed-SHA rebind must hash and parse
    the SAME bytes -- two independent path.read_text() calls on the same
    file leave a window for an atomic swap between them (a matching-SHA
    reviewed draft A could be the one hashed while an unreviewed draft B
    is what actually gets evaluated into C_output). A literal race is
    non-deterministic to reproduce in a test; the deterministic, standard
    proof is a call-count assertion: _rebind_or_flag_stale must read the
    draft's own path EXACTLY ONCE per invocation. Before the fix,
    draft_content_sha1(dp) performed its own internal read, and a SEPARATE
    dp.read_text() call supplied the parsed draft -- two reads, a real
    TOCTOU window."""
    root = make_root(tmp_path)
    scripts_dir = root / "scripts"
    write_draft(root, "seg01", {"HEAD:seg01": "Glava Odna"})
    write_ledger(root, {"seg01": {"status": "converged"}})  # correctly bound sha1

    mod = _load_module_from_copy(scripts_dir)
    dp = mod.draft_path("seg01")

    real_read_text = Path.read_text
    call_count = {"n": 0}

    def counting_read_text(self, *args, **kwargs):
        if self == dp:
            call_count["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    ledger = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    record = ledger["segments"]["seg01"]
    stale_segs = set()
    draft = mod._rebind_or_flag_stale("seg01", record, stale_segs)

    assert draft is not None and not stale_segs, "sanity: a genuinely matching draft must pass the rebind"
    assert call_count["n"] == 1, (
        f"_rebind_or_flag_stale must read the draft's own bytes EXACTLY "
        f"ONCE (hash and parse from the SAME read) -- got "
        f"{call_count['n']} reads of {dp}, which reopens a TOCTOU window "
        f"between the hash and the content actually evaluated into C_output"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
