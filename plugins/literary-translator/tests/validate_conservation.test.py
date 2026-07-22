"""tests/validate_conservation.test.py -- regression-lock for
scripts/validate_conservation.py (#196/#202 content-conservation gate).

See that script's own module docstring for the full invariant spec this
file was written against: `wrapper-conservation` (HARD, opt-in, four
defect kinds) and `output-coverage` (WARN-only v1 floor, plus the opt-in
within-cohort ratio-outlier lane, #202 R3 PARTIAL -- see the
"output-coverage -- within-cohort ratio-outlier lane" test section below).

## Fixture strategy

Mirrors tests/validate_assembled.test.py's own approach: build a REAL,
self-contained `durable_root` on disk (real copies of
`validate_conservation.py` + its sibling imports `validate_assembled.py` /
`validate_draft.py`, a `profile.yml` + ownership marker, `manifest.json`,
and whatever baseline/provenance/allowed-omissions/draft/ledger/nodestream
artifacts a given test needs) and invoke the ACTUAL script as a subprocess
-- `python3 {durable_root}/scripts/validate_conservation.py <mode>`.

Baseline offsets are never hand-counted: `build_baseline()` concatenates
labeled text chunks and returns the (start, end) each label landed at, so a
test's provenance spans are always internally consistent with the baseline
string actually written to disk.

Collection note: like every `*.test.py` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with `python3 -m pytest --import-mode=importlib tests/validate_conservation.test.py`
(the project's own pytest.ini already sets this).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

VALIDATE_CONSERVATION_SRC = SCRIPTS_SRC_DIR / "validate_conservation.py"
VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
PROFILE_SCHEMA_SRC = SCHEMAS_SRC_DIR / "profile.schema.json"
PROFILE_EXAMPLE_SRC = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "profile.example.yml"

assert VALIDATE_CONSERVATION_SRC.is_file(), (
    f"validate_conservation.py not found at {VALIDATE_CONSERVATION_SRC} -- "
    f"#196/#202 content-conservation gate has not landed yet"
)
assert VALIDATE_ASSEMBLED_SRC.is_file(), f"validate_assembled.py not found at {VALIDATE_ASSEMBLED_SRC}"
assert VALIDATE_DRAFT_SRC.is_file(), f"validate_draft.py not found at {VALIDATE_DRAFT_SRC}"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _copy(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())


def make_root(tmp_path) -> Path:
    """A bare durable_root: real copies of validate_conservation.py + its
    two sibling imports. profile.yml is NOT written here -- each test calls
    write_profile() itself, since the conservation config varies per test."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_CONSERVATION_SRC, VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
        _copy(src, scripts_dir / src.name)
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def write_profile(
    root: Path,
    v1_scope: str = "segment_drafts_and_audit",
    conservation=None,
    hollow_floor=None,
    ratio_band=None,
) -> None:
    profile: dict = {"output": {"v1_scope": v1_scope}}
    if conservation is not None:
        profile["source"] = {"conservation": conservation}
    validation_cfg = {}
    if hollow_floor is not None:
        validation_cfg["conservation_hollow_floor"] = hollow_floor
    if ratio_band is not None:
        validation_cfg["conservation_ratio_band"] = ratio_band
    if validation_cfg:
        profile["validation"] = validation_cfg
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


def build_baseline(parts):
    """parts: list of (label, text). Returns (baseline_text, offsets), where
    offsets[label] == (start, end) -- computed by concatenation, never
    hand-counted, so a test's provenance spans can never drift from the
    baseline string actually written to disk."""
    baseline = ""
    offsets = {}
    for label, text in parts:
        start = len(baseline)
        baseline += text
        offsets[label] = (start, len(baseline))
    return baseline, offsets


def write_baseline(root: Path, text: str, name: str = "baseline.txt") -> str:
    (root / name).write_text(text, encoding="utf-8")
    return name


def write_provenance(root: Path, spans: list, name: str = "provenance_map.json") -> str:
    """spans: list of (block_id, start, end)."""
    doc = {
        "schema_version": 1,
        "spans": [{"block_id": bid, "baseline_start": s, "baseline_end": e} for (bid, s, e) in spans],
    }
    (root / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return name


def write_allowed_omissions(root: Path, line_patterns=None, ranges=None, name: str = "allowed_omissions.json") -> str:
    doc = {
        "line_patterns": line_patterns or [],
        "ranges": [{"start": s, "end": e} for (s, e) in (ranges or [])],
    }
    (root / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return name


def draft_content_sha1_of(doc: dict) -> str:
    import hashlib

    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_draft(root: Path, seg: str, blocks: dict) -> dict:
    draft = {"seg": seg, "blocks": blocks, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return draft


def write_ledger(root: Path, entries: dict) -> None:
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


def _words(n: int, tag: str) -> str:
    """n distinct space-separated tokens -- word CONTENT never matters to
    normalize_words(), only the count, but distinct tokens make a mismatch
    easy to spot in a failure message."""
    return " ".join(f"{tag}{i}" for i in range(n)) if n > 0 else ""


def make_cohort_manifest_and_draft(root: Path, raw_type: str, ratio_specs, seg: str = "seg01") -> list:
    """Builds a single-segment manifest + draft + converged ledger entry
    with one block per (source_words, output_words) pair in `ratio_specs`,
    all sharing `raw_type` (one cohort). segment_drafts_and_audit scope.
    Returns the block_ids in creation order."""
    blocks = {}
    draft_blocks = {}
    block_ids = []
    for i, (source_words, out_words) in enumerate(ratio_specs):
        bid = f"{raw_type}:{seg}:{i:04d}"
        blocks[bid] = make_block(raw_type, _words(source_words, f"s{i}_") or "x", order_index=i)
        draft_blocks[bid] = _words(out_words, f"o{i}_")
        block_ids.append(bid)
    write_manifest(root, blocks, [
        {"seg": seg, "kind": "body", "block_ids": block_ids, "word_count": sum(sw for sw, _ in ratio_specs)}
    ])
    write_draft(root, seg, draft_blocks)
    write_ledger(root, {seg: {"status": "converged"}})
    return block_ids


def make_cohort_manifest_and_nodestream(root: Path, raw_type: str, ratio_specs, seg: str = "seg01") -> list:
    """assembled_book-scope counterpart of make_cohort_manifest_and_draft --
    same manifest shape, but output word counts come from a nodestream
    instead of a draft/ledger pair."""
    blocks = {}
    nodes = []
    block_ids = []
    for i, (source_words, out_words) in enumerate(ratio_specs):
        bid = f"{raw_type}:{seg}:{i:04d}"
        blocks[bid] = make_block(raw_type, _words(source_words, f"s{i}_") or "x", order_index=i)
        nodes.append({"id": bid, "seg": seg, "kind": "prose", "text": _words(out_words, f"o{i}_")})
        block_ids.append(bid)
    write_manifest(root, blocks, [
        {"seg": seg, "kind": "body", "block_ids": block_ids, "word_count": sum(sw for sw, _ in ratio_specs)}
    ])
    write_nodestream(root, {"book": {"seg_order": [seg], "title": "Test"}, "nodes": nodes})
    return block_ids


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


# ===========================================================================
# wrapper-conservation -- opt-in skip
# ===========================================================================


def test_wrapper_conservation_skipped_when_no_config(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, conservation=None)
    write_manifest(root, {"PARA:seg01:0001": make_block("PARA", "Body text.")}, [
        {"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 2}
    ])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    assert "SKIPPED" in proc.stderr


# ===========================================================================
# wrapper-conservation -- GREEN: clean wrap, including permitted reflow
# ===========================================================================


def test_wrapper_conservation_green_clean_wrap(tmp_path):
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("head", "Chapter One\n"),
        ("body", "This is the body of chapter one, telling a full story.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("HEAD:seg01", *offsets["head"]),
        ("PARA:seg01:0001", *offsets["body"]),
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "HEAD:seg01": make_block("HEAD", "Chapter One"),
        "PARA:seg01:0001": make_block("PARA", "This is the body of chapter one, telling a full story.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "PARA:seg01:0001"], "word_count": 12}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["defects"] == []


def test_wrapper_conservation_permitted_reflow_not_false_red(tmp_path):
    """The highest-risk false positive: a baseline chunk carrying PDF-layout
    whitespace noise (runs of spaces, mid-line breaks) must NOT be flagged
    merely because it does not byte-match the manifest's own
    whitespace-collapsed plain_text -- normalize_words tokenizes on ANY
    whitespace run, so word content is what is compared, never layout."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("body", "This  is   the\nbody   of chapter\none,  telling a\n\nfull    story.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [("PARA:seg01:0001", *offsets["body"])])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This is the body of chapter one, telling a full story."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 11}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["defects"] == []


# ===========================================================================
# wrapper-conservation -- RED: dangling ref, duplicated/overlapping span,
# dropped content (gap), reordered span, hollowed/truncated block.
# ===========================================================================


def test_wrapper_conservation_red_dangling_block_ref(tmp_path):
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([("body", "Some body text here.")])
    write_baseline(root, baseline)
    write_provenance(root, [("PARA:seg01:9999", *offsets["body"])])  # cites a block that does not exist
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "Some body text here."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "dangling_provenance_block_ref" in kinds


def test_wrapper_conservation_red_duplicated_overlapping_span(tmp_path):
    """The SAME baseline byte range attributed to two different block_ids --
    the 'duplicated span' failure mode."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([("body", "Shared content claimed twice.")])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["body"]),
        ("PARA:seg01:0002", *offsets["body"]),  # identical range, different block
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "Shared content claimed twice."),
        "PARA:seg01:0002": make_block("PARA", "Shared content claimed twice.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 8}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "overlapping_provenance_spans" in kinds


def test_wrapper_conservation_red_content_dropped_in_gap(tmp_path):
    """A whole baseline range between two provenance spans, carrying real
    content, that no span and no allowed omission accounts for -- the #196
    'wrap silently dropped a paragraph' case."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("first", "First paragraph survives the wrap.\n"),
        ("dropped", "This entire paragraph never made it into the EPUB.\n"),
        ("second", "Second paragraph also survives.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["first"]),
        ("PARA:seg01:0002", *offsets["second"]),
    ])  # no span for "dropped"
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "First paragraph survives the wrap."),
        "PARA:seg01:0002": make_block("PARA", "Second paragraph also survives.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 10}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "content_dropped_during_wrap" in kinds


def test_wrapper_conservation_green_gap_covered_by_allowed_omission(tmp_path):
    """The SAME shape as the dropped-content test above, except the gap is
    ONLY a running head + a page number -- both declared as allowed
    omissions -- so it must NOT be flagged."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("first", "First paragraph survives the wrap.\n"),
        ("gap", "SIACH SARFEI KODESH\n12\n"),
        ("second", "Second paragraph also survives.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["first"]),
        ("PARA:seg01:0002", *offsets["second"]),
    ])
    write_allowed_omissions(root, line_patterns=[r"^SIACH SARFEI KODESH$", r"^\d+$"])
    write_profile(root, conservation={
        "baseline_path": "baseline.txt",
        "provenance_path": "provenance_map.json",
        "allowed_omissions_path": "allowed_omissions.json",
    })
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "First paragraph survives the wrap."),
        "PARA:seg01:0002": make_block("PARA", "Second paragraph also survives.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 10}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["defects"] == []


def test_wrapper_conservation_red_hollowed_block(tmp_path):
    """The baseline span carries content the manifest block's own plain_text
    is missing -- the block was truncated/hollowed while being written
    during the hand-wrap (the #202 half #196 alone would not catch)."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("body", "This paragraph has quite a lot of original content in it.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [("PARA:seg01:0001", *offsets["body"])])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        # Truncated: only the first three words survived the hand-wrap.
        "PARA:seg01:0001": make_block("PARA", "This paragraph has"),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 3}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "hollowed_or_truncated_block" in kinds


def test_wrapper_conservation_red_swapped_block_assignment(tmp_path):
    """NOT a reading-order test (see test_wrapper_conservation_red_reading_order_reversal
    below for that) -- this is a content-mismatch case: two spans with
    genuinely distinct content, but the provenance map assigns each span's
    block_id to the OTHER block. Both blocks' plain_text is unrelated to
    what its assigned span actually says, so both must be flagged
    hollowed_or_truncated_block (a swap can never accidentally look like a
    legitimate submultiset match here since the two chunks share no content
    words). Caught by the word-multiset check, not by order_index -- codex
    review flagged an earlier version of this test for being MISLABELED as
    a reordering test when it actually proves the multiset check works, not
    that ordering is verified."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("alpha", "Alpha chunk about foxes and forests.\n"),
        ("beta", "Beta chunk about oceans and whales.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["beta"]),   # swapped: alpha's block gets beta's span
        ("PARA:seg01:0002", *offsets["alpha"]),  # swapped: beta's block gets alpha's span
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "Alpha chunk about foxes and forests."),
        "PARA:seg01:0002": make_block("PARA", "Beta chunk about oceans and whales.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 12}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    flagged_ids = {bid for d in doc["defects"] if d["kind"] == "hollowed_or_truncated_block" for bid in d["block_ids"]}
    assert flagged_ids == {"PARA:seg01:0001", "PARA:seg01:0002"}


def test_wrapper_conservation_red_reading_order_reversal(tmp_path):
    """The genuine reordering case codex's review probed for: each span is
    assigned to its OWN CORRECT block (content matches perfectly, so
    hollowed_or_truncated_block never fires), but the block with the HIGHER
    manifest order_index physically appears FIRST in the baseline -- the
    wrap shuffled reading order even though each block's own content
    survived intact. None of the other four checks can see this (no
    dangling ref, no overlap, no gap, no content mismatch)."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("second_by_manifest", "This physically comes first in the baseline.\n"),
        ("first_by_manifest", "This physically comes second in the baseline.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0002", *offsets["second_by_manifest"]),  # order_index=1, but physically FIRST
        ("PARA:seg01:0001", *offsets["first_by_manifest"]),   # order_index=0, but physically SECOND
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This physically comes second in the baseline.", order_index=0),
        "PARA:seg01:0002": make_block("PARA", "This physically comes first in the baseline.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 14}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "reading_order_reversal" in kinds
    # And it is NOT the multiset check firing for the wrong reason -- each
    # block's own content is fully intact.
    assert "hollowed_or_truncated_block" not in kinds


def test_wrapper_conservation_green_correct_reading_order(tmp_path):
    """Companion GREEN to the reversal test above -- same two blocks, same
    content, but physically in manifest order_index order -- must NOT flag
    reading_order_reversal."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("first_by_manifest", "This physically comes first in the baseline.\n"),
        ("second_by_manifest", "This physically comes second in the baseline.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["first_by_manifest"]),
        ("PARA:seg01:0002", *offsets["second_by_manifest"]),
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This physically comes first in the baseline.", order_index=0),
        "PARA:seg01:0002": make_block("PARA", "This physically comes second in the baseline.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 14}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["defects"] == []


def test_wrapper_conservation_red_reading_order_interleaving(tmp_path):
    """The interleaving gap a min-anchor-per-block reduction cannot see:
    block PARA:seg01:0001 (order_index=0) has TWO spans, and block
    PARA:seg01:0002 (order_index=1) has ONE span physically BETWEEN
    0001's two spans in the baseline -- so 0001 resumes AFTER 0002 already
    began, even though each block's own content is fully intact (so
    hollowed_or_truncated_block must never fire here). A min-of-own-starts
    anchor for 0001 is the position of its FIRST span, which still sorts
    before 0002's anchor -- non-decreasing, no defect -- even though the
    baseline was physically shuffled. Only walking the full span sequence in
    baseline-position order (not per-block anchors) can see this."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("a1", "Alpha first half about foxes.\n"),
        ("b", "Beta entire chunk about oceans.\n"),
        ("a2", "Alpha second half about forests.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["a1"]),
        ("PARA:seg01:0002", *offsets["b"]),
        ("PARA:seg01:0001", *offsets["a2"]),
    ])
    write_profile(root, conservation={"baseline_path": "baseline.txt", "provenance_path": "provenance_map.json"})
    write_manifest(root, {
        "PARA:seg01:0001": make_block(
            "PARA", "Alpha first half about foxes. Alpha second half about forests."
        ),
        "PARA:seg01:0002": make_block("PARA", "Beta entire chunk about oceans.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 15}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "reading_order_reversal" in kinds
    assert "hollowed_or_truncated_block" not in kinds


def test_wrapper_conservation_green_block_split_by_omission_not_interleaving(tmp_path):
    """The legitimate counterpart to the interleaving test above: block
    PARA:seg01:0001 (order_index=0) genuinely has two spans, but the region
    between them is an ALLOWED OMISSION (a running head, carrying no
    provenance span of its own) rather than another block's span. Block
    PARA:seg01:0002 (order_index=1) comes entirely after. This must NOT
    false-flag reading_order_reversal -- same-block multi-spans share one
    order_index, so no adjacent pair in the sorted span sequence ever
    decreases."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("a1", "Alpha first half about foxes.\n"),
        ("headgap", "RUNNING HEAD\n"),
        ("a2", "Alpha second half about forests.\n"),
        ("b", "Beta entire chunk about oceans.\n"),
    ])
    write_baseline(root, baseline)
    write_provenance(root, [
        ("PARA:seg01:0001", *offsets["a1"]),
        ("PARA:seg01:0001", *offsets["a2"]),
        ("PARA:seg01:0002", *offsets["b"]),
    ])
    write_allowed_omissions(root, line_patterns=[r"^RUNNING HEAD$"])
    write_profile(root, conservation={
        "baseline_path": "baseline.txt",
        "provenance_path": "provenance_map.json",
        "allowed_omissions_path": "allowed_omissions.json",
    })
    write_manifest(root, {
        "PARA:seg01:0001": make_block(
            "PARA", "Alpha first half about foxes. Alpha second half about forests."
        ),
        "PARA:seg01:0002": make_block("PARA", "Beta entire chunk about oceans.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002"], "word_count": 15}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["defects"] == []


def test_wrapper_conservation_omission_range_is_characters_not_bytes(tmp_path):
    """Codex review probe (IMPORTANT finding): profile.schema.json documents
    allowed_omissions ranges as covering the baseline, and this script reads
    the baseline as a Python str, so an offset MUST be a Unicode code point
    (character) offset, never a UTF-8 byte offset -- 'א' (U+05D0) is ONE
    character but TWO UTF-8 bytes, so the two interpretations disagree for
    any non-ASCII baseline. An ASCII-only test cannot distinguish them (byte
    offset == character offset for ASCII), which is exactly how the original
    version of this gate shipped without this test and without the bug
    surfacing.

    baseline = 'א' + 'X' + real body text. The omission range [0, 1) is
    declared to cover EXACTLY the one Hebrew character. Correct
    (character-offset) semantics consumes ONLY 'א', so the un-provenanced
    remainder still contains 'X' and must RED as content_dropped_during_wrap
    -- proving the omission did not also silently swallow the adjacent
    real character (which is what a byte-offset misinterpretation of the
    SAME declared range would have done: codex's own probe showed a
    documented-as-'byte' range [0, 2) consuming both 'א' and 'X' as two
    CHARACTERS, silently dropping 'X' with no defect at all)."""
    root = make_root(tmp_path)
    baseline, offsets = build_baseline([
        ("hebrew_char", "א"),
        ("dropped_char", "X"),
        ("body", " real body text that must not be silently dropped.\n"),
    ])
    write_baseline(root, baseline)
    # Only the body has a provenance span -- "א" + "X" are left as an
    # un-provenanced gap at the very start of the baseline, exactly the
    # region the omission range below is meant to (partially) excuse.
    write_provenance(root, [("PARA:seg01:0001", *offsets["body"])])
    write_allowed_omissions(root, ranges=[(0, 1)])  # exactly the 1 character 'א'
    write_profile(root, conservation={
        "baseline_path": "baseline.txt",
        "provenance_path": "provenance_map.json",
        "allowed_omissions_path": "allowed_omissions.json",
    })
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "real body text that must not be silently dropped."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 8}])

    proc = run_validate_conservation(root, "wrapper-conservation")
    assert proc.returncode == 1, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {d["kind"] for d in doc["defects"]}
    assert "content_dropped_during_wrap" in kinds
    # And the flagged remainder is 'X' itself, not the Hebrew character --
    # confirms the omission consumed exactly 1 CHARACTER, not 1 byte (which
    # would have left a dangling half of 'א' behind) and not 2 characters
    # (which would have silently swallowed 'X' too, per codex's probe).
    dropped_detail = " ".join(
        d["detail"] for d in doc["defects"] if d["kind"] == "content_dropped_during_wrap"
    )
    assert "X" in dropped_detail
    assert "א" not in dropped_detail


# ===========================================================================
# output-coverage -- WARN-only v1 floor
# ===========================================================================


def test_output_coverage_warn_hollowed_default_scope(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This block has real, substantial source content."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 7}])
    write_draft(root, "seg01", {"PARA:seg01:0001": ""})  # hollowed on the output side
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr  # WARN-only -- never exits 1
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert ("seg01", "PARA:seg01:0001", "hollowed_output_block") in kinds


def test_output_coverage_no_warn_on_legitimately_short_blocks(tmp_path):
    """A one-word heading and a one-word paragraph, BOTH translated to a
    non-empty (also short) output -- the v1 floor is 'empty vs non-trivial
    source', not a length ratio, so a short-but-present translation must
    never warn."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    write_manifest(root, {
        "HEAD:seg01": make_block("HEAD", "Preface"),
        "PARA:seg01:0001": make_block("PARA", "Yes.", order_index=1),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["HEAD:seg01", "PARA:seg01:0001"], "word_count": 2}])
    write_draft(root, "seg01", {"HEAD:seg01": "Preambule", "PARA:seg01:0001": "Oui."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["warnings"] == []


def test_output_coverage_frontback_omit_never_eligible(tmp_path):
    """A frontback block with decision 'omit' is never cited by any
    segments[].block_ids[] (per manifest.schema.json's own contract, mirrored
    by validate_assembled.py's own frontback_inventory check) -- so it can
    never enter this script's population at all, regardless of how hollow
    its own (nonexistent) draft counterpart would be."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    manifest_blocks = {
        "PARA:seg01:0001": make_block("PARA", "Ordinary body content here."),
        "FRONTBACK:fm01": make_block("FRONTBACK", "Front matter never wrapped.", order_index=1),
    }
    write_manifest(root, manifest_blocks, [
        {"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 4},
        # NOTE: no segments[] entry cites FRONTBACK:fm01 at all -- that is the point.
    ])
    write_draft(root, "seg01", {"PARA:seg01:0001": "Ordinary body content here."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert doc["warnings"] == []
    assert not any("fm01" in json.dumps(w) for w in doc["warnings"])


def test_output_coverage_assembled_book_scope(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="assembled_book")
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This block has real, substantial source content."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 7}])
    write_nodestream(root, {
        "book": {"seg_order": ["seg01"], "title": "Test"},
        "nodes": [{"id": "PARA:seg01:0001", "seg": "seg01", "kind": "prose", "text": ""}],
    })

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert ("seg01", "PARA:seg01:0001", "hollowed_output_block") in kinds


def test_output_coverage_custom_threshold(tmp_path):
    """Raising max_output_words lets a short-but-nonzero translation still
    count as 'near-empty' -- proves the profile-configurable threshold
    actually reaches the check, not just the hardcoded default."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 2},
    )
    write_manifest(root, {
        "PARA:seg01:0001": make_block("PARA", "This block has real, substantial source content."),
    }, [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 7}])
    write_draft(root, "seg01", {"PARA:seg01:0001": "Uh yes"})  # 2 words -- passes default floor, not this one
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert ("seg01", "PARA:seg01:0001", "hollowed_output_block") in kinds


# ===========================================================================
# output-coverage -- within-cohort ratio-outlier lane (D3, #202 R3, PARTIAL)
#
# OPT-IN: absent/null validation.conservation_ratio_band means this lane
# never runs -- output-coverage's output stays byte-identical to 1.11.0 (see
# both validate_conservation.py's own module docstring and
# profile.schema.json). Every test below that exercises the lane passes
# ratio_band= explicitly for that reason.
# ===========================================================================


def test_ratio_band_disabled_by_default(tmp_path):
    """Absent validation.conservation_ratio_band -- no coverage_distribution
    key at all, and the lane emits none of its three new warning kinds."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit")
    make_cohort_manifest_and_draft(root, "PARA", [(100, 100)] * 5)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert "coverage_distribution" not in doc
    assert doc["warnings"] == []


def test_ratio_band_low_coverage_outlier_flagged(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    ratio_specs = [(100, 100)] * 30 + [(100, 10)]  # 30 at ratio 1.0, one at 0.1
    block_ids = make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    outliers = [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"]
    assert len(outliers) == 1, doc["warnings"]
    assert outliers[0]["block_id"] == block_ids[-1]
    assert outliers[0]["seg"] == "seg01"
    assert outliers[0]["raw_type"] == "PARA"


def test_ratio_band_no_flag_at_ratio_0_9_degenerate_mad(tmp_path):
    """30 identical ratio-1.0 blocks + one at 0.9: only one of 31 deviations
    is nonzero, so the median deviation (MAD) is still 0 -- the robust fence
    then equals the median, which alone would flag anything even slightly
    below it. The abs_guard condition is what actually protects this
    near-median block; the sibling test below flips abs_guard and shows the
    SAME fixture then DOES get flagged, proving abs_guard is load-bearing
    rather than assumed."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    ratio_specs = [(100, 100)] * 30 + [(100, 90)]
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"] == []


def test_ratio_band_abs_guard_is_load_bearing(tmp_path):
    """SAME fixture as test_ratio_band_no_flag_at_ratio_0_9_degenerate_mad,
    only abs_guard raised to its schema maximum (1.0) -- the block that was
    NOT flagged at the default (0.5) IS now flagged, proving abs_guard
    actually gates the outcome rather than being vacuous."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={"abs_guard": 1.0})
    ratio_specs = [(100, 100)] * 30 + [(100, 90)]
    block_ids = make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    outliers = [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"]
    assert len(outliers) == 1
    assert outliers[0]["block_id"] == block_ids[-1]


def test_ratio_band_cohort_separation(tmp_path):
    """A terse-by-nature type (uniform ratio 0.2) and a verbose type
    (uniform ratio 1.0) in the SAME project. If cohorts were pooled instead
    of grouped by raw manifest type, the terse cohort's own typical ratio
    would look like a collapse relative to the verbose cohort's dominant
    median. Grouped correctly, each cohort's own median/MAD are computed
    independently -- asserted directly, not just inferred from an absence
    of warnings."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    blocks = {}
    draft_blocks = {}
    seg_block_ids = []
    for i in range(25):
        bid = f"TERSE:seg01:{i:04d}"
        blocks[bid] = make_block("TERSE", _words(100, f"t{i}_"), order_index=len(seg_block_ids))
        draft_blocks[bid] = _words(20, f"to{i}_")  # ratio 0.2
        seg_block_ids.append(bid)
    for i in range(25):
        bid = f"VERBOSE:seg01:{i:04d}"
        blocks[bid] = make_block("VERBOSE", _words(100, f"v{i}_"), order_index=len(seg_block_ids))
        draft_blocks[bid] = _words(100, f"vo{i}_")  # ratio 1.0
        seg_block_ids.append(bid)
    write_manifest(root, blocks, [{"seg": "seg01", "kind": "body", "block_ids": seg_block_ids, "word_count": 5000}])
    write_draft(root, "seg01", draft_blocks)
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"] == []
    dist = doc["coverage_distribution"]
    assert dist["TERSE"]["median_ratio"] == 0.2
    assert dist["VERBOSE"]["median_ratio"] == 1.0


def test_ratio_band_insufficient_sample_below_min_cohort(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={"min_cohort": 5})
    ratio_specs = [(100, 100), (100, 95)]  # only 2 eligible, below min_cohort=5
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    samples = [w for w in doc["warnings"] if w["kind"] == "insufficient_sample"]
    assert len(samples) == 1
    assert samples[0]["cohort"] == "PARA"
    assert samples[0]["n"] == 2
    assert samples[0]["min_cohort"] == 5
    assert samples[0]["reason"] == "too_few_eligible"


def test_ratio_band_no_double_report_at_nonzero_floor(tmp_path):
    """The floor's max_output_words is operator-configurable and not
    necessarily 0 -- a floor-flagged block must be excluded from the ratio
    band via its OWN (seg, block_id) keys from this run, never re-derived
    via an out_words == 0 test, or it would double-report."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 2},
        ratio_band={},
    )
    ratio_specs = [(100, 100)] * 30 + [(100, 1)]  # last block: floor-flagged (1 <= 2)
    block_ids = make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    matches = [w for w in doc["warnings"] if w.get("block_id") == block_ids[-1]]
    kinds = {w["kind"] for w in matches}
    assert kinds == {"hollowed_output_block"}, doc["warnings"]
    assert doc["coverage_distribution"]["PARA"]["excluded_floor_flagged"] == 1


def test_ratio_band_uniform_collapse_blind_spot_characterization(tmp_path):
    """THE documented structural blind spot: if every block in a cohort is
    equally truncated, the median IS the truncated ratio and MAD == 0, so
    nothing is an outlier. Pins the limitation honestly -- if a future
    change makes the lane detect this, this test must be updated
    deliberately rather than the gap being rediscovered in production."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    ratio_specs = [(100, 30)] * 30  # every block uniformly truncated to 0.30
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"] == []
    assert doc["coverage_distribution"]["PARA"]["median_ratio"] == 0.3


def test_ratio_band_all_floor_flagged_reason_visible_and_distinct(tmp_path):
    """A cohort that collapsed hard enough that the floor caught EVERY
    block must be reported as all_floor_flagged -- distinct from
    too_few_eligible, which would mask the collapse as mere small-sample
    noise."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 100},
        ratio_band={},
    )
    ratio_specs = [(100, 50)] * 10  # every block: out(50) <= floor.max(100) -> ALL floor-flagged
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    samples = [w for w in doc["warnings"] if w["kind"] == "insufficient_sample"]
    assert len(samples) == 1
    assert samples[0]["reason"] == "all_floor_flagged"
    assert samples[0]["reason"] != "too_few_eligible"
    assert samples[0]["excluded_floor_flagged"] == 10
    assert samples[0]["n"] == 0


def test_ratio_band_partition_identity_mixed_cohort(tmp_path):
    """Half the cohort floor-flagged, half eligible -- the exact partition
    cohort_size == n + excluded_floor_flagged + excluded_below_min_source_words
    + excluded_zero_output must hold, so a reader never has to trust a
    derived label to know how much of the cohort the median did not see."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 5},
        ratio_band={"min_cohort": 10},
    )
    ratio_specs = [(100, 3)] * 15 + [(100, 100)] * 15  # first 15 floor-flagged, rest eligible
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    dist = doc["coverage_distribution"]["PARA"]
    assert dist["cohort_size"] == 30
    assert dist["n"] == 15
    assert dist["excluded_floor_flagged"] == 15
    assert dist["excluded_below_min_source_words"] == 0
    assert dist["excluded_zero_output"] == 0
    assert dist["cohort_size"] == (
        dist["n"] + dist["excluded_floor_flagged"]
        + dist["excluded_below_min_source_words"] + dist["excluded_zero_output"]
    )
    # n(15) >= the overridden min_cohort(10) -- no insufficient_sample noise,
    # and the 15 eligible blocks are uniform (ratio 1.0) so none is an
    # outlier relative to itself either.
    assert [w for w in doc["warnings"] if w["kind"] in ("insufficient_sample", "low_coverage_outlier")] == []


def test_ratio_band_log_zero_regression_floor_skip_straddle(tmp_path):
    """floor.min_source_words=100 SKIPS (continues past) a source_words=50
    block entirely -- it is never floor-flagged. band.min_source_words_band
    =40 clears it into the band population, where out_words==0 would
    otherwise reach math.log(0) -- a ValueError crash inside a
    contractually WARN-only, exit-0 command. Bucket 3
    (excluded_zero_output) is what makes this unreachable. Without the
    guard this fixture raises ValueError, so this test is red-before-green
    by construction."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 100, "max_output_words": 0},
        ratio_band={"min_source_words_band": 40},
    )
    block_ids = make_cohort_manifest_and_draft(root, "PARA", [(50, 0)])

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr  # not a math domain error crash
    doc = parse_stdout_json(proc)
    zero_output = [w for w in doc["warnings"] if w["kind"] == "zero_output_block"]
    assert len(zero_output) == 1
    assert zero_output[0]["block_id"] == block_ids[0]
    assert [
        w for w in doc["warnings"]
        if w["kind"] == "low_coverage_outlier" and w.get("block_id") == block_ids[0]
    ] == []
    assert doc["coverage_distribution"]["PARA"]["excluded_zero_output"] == 1


def test_ratio_band_reason_too_few_eligible_with_mixed_exclusions(tmp_path):
    """The mixed-cohort case: SOME blocks are eligible, but the cohort still
    has fewer than min_cohort -- must land in too_few_eligible, first in the
    reason precedence, never in an all_* label even though exclusions are
    also present."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 2},
        ratio_band={"min_source_words_band": 20, "min_cohort": 25},
    )
    ratio_specs = [
        (100, 1),   # floor-flagged
        (5, 3),     # below min_source_words_band (5 < 20)
        (100, 50),  # eligible
    ]
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    samples = [w for w in doc["warnings"] if w["kind"] == "insufficient_sample"]
    assert len(samples) == 1
    assert samples[0]["reason"] == "too_few_eligible"
    assert samples[0]["n"] == 1
    assert samples[0]["excluded_floor_flagged"] == 1
    assert samples[0]["excluded_below_min_source_words"] == 1


def test_ratio_band_reason_set_is_exactly_the_documented_set(tmp_path):
    """Collects one cohort per known `reason` branch in a SINGLE run and
    asserts the emitted reason set is EXACTLY the five documented labels --
    no silent extra label, none missing."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 60, "max_output_words": 2},
        ratio_band={"min_source_words_band": 50, "min_cohort": 25},
    )
    blocks = {}
    draft_blocks = {}
    seg_block_ids = []

    def add(raw_type, tag, specs):
        for i, (sw, ow) in enumerate(specs):
            bid = f"{raw_type}:seg01:{i:04d}"
            blocks[bid] = make_block(raw_type, _words(sw, f"{tag}{i}_") or "x", order_index=len(seg_block_ids))
            draft_blocks[bid] = _words(ow, f"{tag}o{i}_")
            seg_block_ids.append(bid)

    # TOOFEW: one floor-flagged (source>=60,out<=2) + one eligible -> n=1, too_few_eligible.
    add("TOOFEW", "a", [(100, 1), (100, 50)])
    # ALLFLOOR: every block floor-flagged (source>=60, out<=2).
    add("ALLFLOOR", "b", [(100, 1)] * 3)
    # ALLBAND: source < 60 -- floor SKIPS (never flags); source < band min(50).
    add("ALLBAND", "c", [(10, 5)] * 3)
    # ALLZERO: source(55) < floor.min(60) -- floor skips; source >= band.min(50); out == 0.
    add("ALLZERO", "d", [(55, 0)] * 3)
    # MIXED: one floor-flagged, one below band min -- both buckets nonzero, n == 0.
    add("MIXED", "e", [(100, 1), (10, 5)])

    write_manifest(root, blocks, [{"seg": "seg01", "kind": "body", "block_ids": seg_block_ids, "word_count": 10000}])
    write_draft(root, "seg01", draft_blocks)
    write_ledger(root, {"seg01": {"status": "converged"}})

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    reasons = {w["cohort"]: w["reason"] for w in doc["warnings"] if w["kind"] == "insufficient_sample"}
    assert reasons == {
        "TOOFEW": "too_few_eligible",
        "ALLFLOOR": "all_floor_flagged",
        "ALLBAND": "all_below_min_source_words",
        "ALLZERO": "all_zero_output",
        "MIXED": "all_excluded_mixed",
    }
    assert set(reasons.values()) == {
        "too_few_eligible",
        "all_floor_flagged",
        "all_below_min_source_words",
        "all_zero_output",
        "all_excluded_mixed",
    }


def test_ratio_band_bucket_disjointness_all_three_predicates_at_once(tmp_path):
    """A block satisfying ALL THREE exclusion predicates simultaneously
    (floor-flagged, below the band minimum, AND zero output) must land in
    exactly ONE bucket -- the FIRST match (floor-flagged) -- never double-
    or triple-counted, and the one-block cohort's reason must be the
    single-bucket label, never all_excluded_mixed."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 0},
        ratio_band={"min_source_words_band": 40},
    )
    make_cohort_manifest_and_draft(root, "PARA", [(10, 0)])

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    dist = doc["coverage_distribution"]["PARA"]
    assert dist["cohort_size"] == 1
    assert dist["excluded_floor_flagged"] == 1
    assert dist["excluded_below_min_source_words"] == 0
    assert dist["excluded_zero_output"] == 0
    samples = [w for w in doc["warnings"] if w["kind"] == "insufficient_sample"]
    assert len(samples) == 1
    assert samples[0]["reason"] == "all_floor_flagged"
    assert samples[0]["reason"] != "all_excluded_mixed"
    # And the block is reported once, as the floor's own finding -- never
    # also as zero_output_block (that would be a double-report).
    assert [w for w in doc["warnings"] if w["kind"] == "zero_output_block"] == []


def test_ratio_band_empty_cohort_json_contract(tmp_path):
    """n == 0 -- median_ratio/mad/fence_ratio are explicit JSON null,
    PRESENT never omitted, and the exact key set is pinned so a future
    refactor that drops one silently fails."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 100},
        ratio_band={},
    )
    make_cohort_manifest_and_draft(root, "PARA", [(100, 50)] * 3)  # all floor-flagged (50 <= 100)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    entry = doc["coverage_distribution"]["PARA"]
    assert entry["n"] == 0
    assert entry["median_ratio"] is None
    assert entry["mad"] is None
    assert entry["fence_ratio"] is None
    assert set(entry.keys()) == {
        "cohort_size", "n", "median_ratio", "mad", "fence_ratio",
        "excluded_floor_flagged", "excluded_below_min_source_words", "excluded_zero_output",
    }


def test_ratio_band_config_validation_errors(tmp_path):
    """non-int min_cohort, k <= 0, abs_guard > 1, a boolean, a null ->
    ConservationError exit 2 -- same defensive shape as
    conservation_hollow_floor's own validation."""
    cases = [
        ({"min_cohort": "5"}, "conservation_ratio_band.min_cohort"),
        ({"min_cohort": True}, "conservation_ratio_band.min_cohort"),
        ({"k": 0}, "conservation_ratio_band.k"),
        ({"k": -1}, "conservation_ratio_band.k"),
        ({"abs_guard": 1.5}, "conservation_ratio_band.abs_guard"),
        ({"abs_guard": None}, "conservation_ratio_band.abs_guard"),
        ({"min_source_words_band": 0}, "conservation_ratio_band.min_source_words_band"),
    ]
    for i, (bad_cfg, needle) in enumerate(cases):
        root = make_root(tmp_path / f"case{i}")
        write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band=bad_cfg)
        make_cohort_manifest_and_draft(root, "PARA", [(100, 100)])

        proc = run_validate_conservation(root, "output-coverage")
        assert proc.returncode == 2, (bad_cfg, proc.stdout, proc.stderr)
        assert needle in proc.stderr, (bad_cfg, proc.stderr)


def test_ratio_band_non_finite_config_yaml_loud_failure(tmp_path):
    """k: .nan / .inf, abs_guard: -.inf, written as REAL YAML (not a Python
    shortcut) -- YAML .nan/.inf parse to real floats that pass a naive
    isinstance(x, float) check, so this must be a LOUD failure (exit 2),
    never merely 'zero warnings emitted'. A NaN fence would make every
    comparison False, silently disabling the whole lane while it reports
    success."""
    cases = [
        {"k": float("nan")},
        {"k": float("inf")},
        {"abs_guard": float("-inf")},
    ]
    for i, bad_cfg in enumerate(cases):
        root = make_root(tmp_path / f"nan_case{i}")
        write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band=bad_cfg)
        make_cohort_manifest_and_draft(root, "PARA", [(100, 100)])

        proc = run_validate_conservation(root, "output-coverage")
        assert proc.returncode == 2, (bad_cfg, proc.stdout, proc.stderr)
        assert "conservation_ratio_band" in proc.stderr
        # Confirm profile.yml genuinely carries the literal YAML token, not
        # a Python-only artifact of this test harness.
        yaml_text = (root / "profile.yml").read_text(encoding="utf-8")
        assert (".nan" in yaml_text) or (".inf" in yaml_text)


def test_ratio_band_allow_nan_guard_rejects_nan_reaching_emission(tmp_path, monkeypatch):
    """Guard 2 (json.dumps(..., allow_nan=False)) is the TOTAL guard: it
    must reject a non-finite value reaching stdout emission via ANY path,
    not just the config-validation path (guard 1). Config validation
    already rejects a NaN k/abs_guard directly, so the only way to exercise
    guard 2 itself is to force a NaN through a path config validation
    cannot see -- monkeypatch statistics.median inside the COPIED script's
    own module (imported directly, since a real subprocess cannot be
    monkeypatched from the test process) and assert the script raises
    loudly rather than emitting a NaN token in stdout."""
    import importlib.util

    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    make_cohort_manifest_and_draft(root, "PARA", [(100, 100)] * 26)

    sys.modules.pop("validate_assembled", None)
    sys.modules.pop("validate_draft", None)
    spec = importlib.util.spec_from_file_location(
        "vc_nan_probe", root / "scripts" / "validate_conservation.py"
    )
    vc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vc)
    monkeypatch.setattr(vc.statistics, "median", lambda values: float("nan"))
    monkeypatch.chdir(root)

    with pytest.raises(ValueError):
        vc.main(["output-coverage"])


def test_ratio_band_bucket_disjointness_partition_identity(tmp_path):
    """Reconfirms the plan's own worked example arithmetically: cohort_size
    == n + excluded_floor_flagged + excluded_below_min_source_words +
    excluded_zero_output for a one-block cohort hitting all three
    predicates -- the partition identity, not just the reason label."""
    root = make_root(tmp_path)
    write_profile(
        root,
        v1_scope="segment_drafts_and_audit",
        hollow_floor={"min_source_words": 1, "max_output_words": 0},
        ratio_band={"min_source_words_band": 40},
    )
    make_cohort_manifest_and_draft(root, "PARA", [(10, 0)])

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    dist = doc["coverage_distribution"]["PARA"]
    assert dist["cohort_size"] == (
        dist["n"] + dist["excluded_floor_flagged"]
        + dist["excluded_below_min_source_words"] + dist["excluded_zero_output"]
    )


def test_ratio_band_schema_rejects_deleted_absolute_threshold_keys(tmp_path):
    """The round-2 absolute-threshold mechanisms (cross_cohort_guard,
    implausible_ratio_floor, implausible_ratio_ceiling) were DELETED as
    ineffective, never shipped -- additionalProperties: false on
    conservation_ratio_band pins that deletion so they cannot silently
    return."""
    import jsonschema

    schema = json.loads(PROFILE_SCHEMA_SRC.read_text(encoding="utf-8"))
    base_profile = yaml.safe_load(PROFILE_EXAMPLE_SRC.read_text(encoding="utf-8"))
    # The shipped example carries a placeholder enum sentinel by design
    # (see tests/profile_example_validation.test.py) -- fill in the one
    # field schema itself unconditionally restricts, so the base fixture is
    # genuinely schema-valid before this test mutates it.
    base_profile["glossary"]["research_mode"] = "offline"
    jsonschema.validate(instance=base_profile, schema=schema)  # sanity: base itself is valid

    for bad_key in ("cross_cohort_guard", "implausible_ratio_floor", "implausible_ratio_ceiling"):
        profile = json.loads(json.dumps(base_profile))
        profile["validation"]["conservation_ratio_band"] = {bad_key: 1}
        with pytest.raises(jsonschema.exceptions.ValidationError):
            jsonschema.validate(instance=profile, schema=schema)

    # The four REAL keys are accepted -- proves this is testing the
    # additionalProperties gate, not an unrelated schema defect.
    ok_profile = json.loads(json.dumps(base_profile))
    ok_profile["validation"]["conservation_ratio_band"] = {
        "min_source_words_band": 10, "min_cohort": 25, "k": 3.0, "abs_guard": 0.5,
    }
    jsonschema.validate(instance=ok_profile, schema=schema)

    # And explicit null (the opt-out form) is accepted too.
    null_profile = json.loads(json.dumps(base_profile))
    null_profile["validation"]["conservation_ratio_band"] = None
    jsonschema.validate(instance=null_profile, schema=schema)


def test_ratio_band_determinism_byte_identical_stdout(tmp_path):
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    make_cohort_manifest_and_draft(root, "PARA", [(100, 100)] * 30 + [(100, 10)])

    proc1 = run_validate_conservation(root, "output-coverage")
    proc2 = run_validate_conservation(root, "output-coverage")
    assert proc1.returncode == 0 == proc2.returncode
    assert proc1.stdout == proc2.stdout


def test_ratio_band_mutation_proof_fence_neutered_via_k(tmp_path):
    """A moderately-spread cohort (ratios alternating 0.95/1.05, MAD
    genuinely nonzero) plus one moderate outlier at ratio 0.3, flagged at
    the default k=3.0. Pushing k way up moves the robust fence far below
    the outlier's own ratio -- the SAME fixture then stops flagging, which
    proves the flag is actually driven by the fence/k, not vacuously
    flagged by something else in the fixture. (A fixture with a degenerate
    MAD == 0, like the ones above, would NOT distinguish this -- k has no
    effect once MAD is 0, since k multiplies it.)"""
    ratio_specs = [(100, 95)] * 15 + [(100, 105)] * 15 + [(100, 30)]

    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    make_cohort_manifest_and_draft(root, "PARA", ratio_specs)
    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    assert len([w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"]) == 1  # RED, confirmed flagging

    root2 = make_root(tmp_path / "neutered")
    write_profile(root2, v1_scope="segment_drafts_and_audit", ratio_band={"k": 50.0})
    make_cohort_manifest_and_draft(root2, "PARA", ratio_specs)
    proc2 = run_validate_conservation(root2, "output-coverage")
    assert proc2.returncode == 0, proc2.stderr
    doc2 = parse_stdout_json(proc2)
    assert [w for w in doc2["warnings"] if w["kind"] == "low_coverage_outlier"] == []


def test_ratio_band_low_coverage_outlier_assembled_book_scope(tmp_path):
    """Both scopes: the ratio-band lane consumes whatever eligible_keys/
    output_words the caller's scope branch resolves -- this proves it also
    works end-to-end against a real nodestream (assembled_book scope), not
    only the default segment_drafts_and_audit scope every test above uses."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="assembled_book", ratio_band={})
    ratio_specs = [(100, 100)] * 30 + [(100, 10)]
    block_ids = make_cohort_manifest_and_nodestream(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    outliers = [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"]
    assert len(outliers) == 1
    assert outliers[0]["block_id"] == block_ids[-1]


def test_default_min_source_words_band_excludes_short_blocks(tmp_path):
    """Pins DEFAULT_RATIO_BAND_MIN_SOURCE_WORDS itself (40), not just the
    mechanism it feeds -- every other test in this file either uses
    source_words=100 (clear of any plausible default) or overrides
    min_source_words_band explicitly, so the compiled-in constant was
    otherwise unpinned: a future refactor (or someone deciding 40 'seems too
    strict') could silently lower it back toward the value this gate exists
    to exclude, and this suite would stay green.

    40 answers a specific codex finding: normalize_words() is NFC +
    whitespace splitting only, with no morphological/markup/sentinel
    normalization, so a short markup- or sentinel-heavy block yields a
    raw-token ratio that is not linguistically comparable -- a lower
    default would silently readmit that population into the band.

    Config carries NO min_source_words_band override, so the compiled-in
    default is what governs. Fixture: 25 normal blocks (ratio 1.0, exactly
    clearing the default min_cohort=25) plus one block at source_words=30
    -- below the 40-word default -- whose output is drastically truncated
    (1 word). At the default (40) that block is excluded via
    excluded_below_min_source_words and never scored; if the default were
    lowered to (e.g.) 10, the same block would become eligible and, being
    drastically truncated against a uniform ratio-1.0 cohort, WOULD be
    flagged as low_coverage_outlier -- this is the red-before-green this
    test is built to prove (verified by hand: temporarily setting
    DEFAULT_RATIO_BAND_MIN_SOURCE_WORDS = 10 flips this test to failing,
    confirming it is not vacuously green)."""
    root = make_root(tmp_path)
    write_profile(root, v1_scope="segment_drafts_and_audit", ratio_band={})
    ratio_specs = [(100, 100)] * 25 + [(30, 1)]
    block_ids = make_cohort_manifest_and_draft(root, "PARA", ratio_specs)

    proc = run_validate_conservation(root, "output-coverage")
    assert proc.returncode == 0, proc.stderr
    doc = parse_stdout_json(proc)
    short_block_id = block_ids[-1]
    outliers = [w for w in doc["warnings"] if w["kind"] == "low_coverage_outlier"]
    assert [w for w in outliers if w["block_id"] == short_block_id] == [], outliers
    dist = doc["coverage_distribution"]["PARA"]
    assert dist["excluded_below_min_source_words"] == 1
    assert dist["n"] == 25


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
