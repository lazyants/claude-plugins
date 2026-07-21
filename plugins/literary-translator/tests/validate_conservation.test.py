"""tests/validate_conservation.test.py -- regression-lock for
scripts/validate_conservation.py (#196/#202 content-conservation gate).

See that script's own module docstring for the full invariant spec this
file was written against: `wrapper-conservation` (HARD, opt-in, four
defect kinds) and `output-coverage` (WARN-only v1 floor).

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

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

VALIDATE_CONSERVATION_SRC = SCRIPTS_SRC_DIR / "validate_conservation.py"
VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

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


def write_profile(root: Path, v1_scope: str = "segment_drafts_and_audit", conservation=None, hollow_floor=None) -> None:
    profile: dict = {"output": {"v1_scope": v1_scope}}
    if conservation is not None:
        profile["source"] = {"conservation": conservation}
    if hollow_floor is not None:
        profile["validation"] = {"conservation_hollow_floor": hollow_floor}
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
