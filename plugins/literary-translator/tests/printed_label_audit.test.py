"""tests/printed_label_audit.test.py -- scripts/printed_label_audit.py, the
report-only printed-entity-label vs canon.json audit (#929). See that
script's own module docstring and plan-929.md (round 5) for the full
contract this file asserts against.

## Fixture strategy

Two isolation shapes are used, matching the two things under test:

  - SUBPROCESS / file-staging (`make_durable_root` + `run_audit`): the REAL
    printed_label_audit.py, json_stdout.py, final_audit.py, assemble.py,
    ledger_merge.py and validate_draft.py copied into an isolated
    `durable_root`'s scripts/ (plus the real schema into schemas/), so the
    script's self-anchored `SCRIPTS_DIR`/`DURABLE_ROOT` resolve against the
    fixture exactly as they do in production. Used for the full CLI
    contract: --build-corpus's gather/fail-closed rules and the --report
    end-to-end integration seam.
  - IN-PROCESS (`pla`, loaded once at module scope from printed_label_
    audit.py's own REAL location -- its only sibling touched at import time
    is json_stdout.py, which is right there for real): used for
    unit-level tests of the pure helper functions (_pack_shards,
    _excerpt_window, _canon_rows_for_source_text, _carrier_kind_from_
    locator) that need no durable-root fixture at all.

Most --report validation tests hand-build a MINIMAL corpus fixture
(`write_corpus`) rather than deriving one from a real --build-corpus run
each time -- mirroring canon_harmonisation.test.py's own
harmonisation_doc()/corpus factory-helper style. Exactly ONE test
(test_report_end_to_end_real_build_corpus_output) is the INTEGRATION SEAM:
it runs --build-corpus for real, then feeds that ACTUAL corpus file and
its real sha256 into --report with a hand-written attempt shard, proving
the wire contract between the two phases rather than two separately
hand-built fixtures that could each pass while disagreeing with the other.

## Coverage (mirrors plan-929.md §8)

  - corpus gather: converged draft included; stale-review draft excluded
    and counted; unreadable ledger.d fatal; malformed span fatal.
  - all three carrier kinds -- block, footnote, verse (verse by fixture:
    the real corpus this plan was priced against has none).
  - mode resolution: absent block -> off (and scans NOTHING, not even the
    ledger); default block -> strip, fully scanned; index_from: markup ->
    index, fully scanned.
  - zero canon rows is a legitimate site; the canon-row cap and the
    anchor-byte cap each route a site to unavailable_sites.
  - a site too large for any shard (_pack_shards unit test), deterministic
    sharding, and the shard byte budget.
  - no off-canon prefilter (the X->Aaron / Y->Bob counterexample); one
    site per span, never one per label (two same-label spans stay two).
  - --report tamper refusals: corpus-hash mismatch, moved canon.json,
    moved draft, schema-invalid attempt (incl. a no_canon_match carrying a
    forbidden source_form), invented site_id, invented anchor pair,
    missing/extra/duplicate shard, wrong-shard site, duplicate verdict,
    unanswered dispatchable site -- each renders nothing and exits 2.
  - a populated render on the success path, and the internal
    sites_total/verdicts-count self-check.
  - the walk-count guard: a converged segment with zero carriers is fatal
    (an implausible zero), never a silent empty corpus.
  - the run writes no file anywhere in --report mode.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "printed_label_audit.py"
SCHEMA_SRC = SCHEMAS_SRC_DIR / "printed-label-audit.schema.json"
assert SCRIPT_SRC.is_file(), f"printed_label_audit.py not found at {SCRIPT_SRC}"
assert SCHEMA_SRC.is_file(), f"printed-label-audit.schema.json not found at {SCHEMA_SRC}"

SIBLING_SCRIPTS = (
    "printed_label_audit.py", "json_stdout.py", "final_audit.py",
    "assemble.py", "ledger_merge.py", "validate_draft.py",
    # final_audit.py bare-imports bootstrap_names.py at module scope;
    # assemble.py bare-imports output_resolve.py and cache_key.py at module
    # scope (after inserting its own SCRIPTS_DIR onto sys.path). None of
    # these three are called by printed_label_audit.py directly -- they are
    # transitively required just to IMPORT their callers.
    "bootstrap_names.py", "output_resolve.py", "cache_key.py",
)

# ---------------------------------------------------------------------------
# In-process load, from printed_label_audit.py's own REAL location -- its
# only sibling touched at IMPORT time is json_stdout.py, which is right
# there for real. Used only for pure-function unit tests that never call
# _load_sibling().
# ---------------------------------------------------------------------------
import importlib.util as _importlib_util  # noqa: E402

_pla_spec = _importlib_util.spec_from_file_location("printed_label_audit", SCRIPT_SRC)
pla = _importlib_util.module_from_spec(_pla_spec)
_pla_spec.loader.exec_module(pla)


# ---------------------------------------------------------------------------
# Subprocess fixture construction
# ---------------------------------------------------------------------------


def make_durable_root(root: Path) -> Path:
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir.mkdir(parents=True, exist_ok=True)
    for name in SIBLING_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    shutil.copy2(SCHEMA_SRC, schemas_dir / SCHEMA_SRC.name)
    return root


def run_audit(root: Path, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "printed_label_audit.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def assert_no_stdout(proc) -> None:
    assert proc.stdout == "", f"expected no stdout at all, got:\n{proc.stdout!r}"


def write_marker_and_profile(
    root: Path, *, entity_markup=None, target="obsidian",
    verse_mode="skip", apparatus_policy="translate_all",
) -> Path:
    profile = {
        "verse_policy": {"mode": verse_mode},
        "footnotes": {"apparatus_policy": apparatus_policy},
        "validation": {"untranslated_sentinel": "[UNTRANSLATED]"},
        "output": {"target": target},
    }
    if entity_markup is not None:
        profile["output"]["entity_markup"] = entity_markup
    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    marker = {"owner_profile_path": str(profile_path)}
    (root / ".literary-translator-root.json").write_text(json.dumps(marker), encoding="utf-8")
    return profile_path


def write_canon(root: Path, entries: dict) -> Path:
    canon_path = root / "canon.json"
    canon_path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return canon_path


def canon_entry(target: str, basis="transliterated", confidence="medium") -> dict:
    return {
        "canonical_target_form": target, "is_proper_name": True,
        "basis": basis, "confidence": confidence,
    }


def write_manifest(root: Path, verse_store=()) -> Path:
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps({"verse": {"store": list(verse_store)}}, ensure_ascii=False), encoding="utf-8"
    )
    return manifest_path


def _draft_content_sha1(doc: dict) -> str:
    """MUST match final_audit.py::draft_content_sha1's own algorithm
    byte-for-byte -- never re-derived, copied here only because building a
    ledger fragment fixture needs the SAME digest the real script will
    recompute from the SAME draft file."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_segment(
    root: Path, seg: str, *, blocks=(), footnotes=(), verses=(),
    draft_blocks=None, draft_footnotes=None, draft_verses=None,
    status="converged", tamper_reviewed_sha1=False, omit_reviewed_sha1=False,
) -> str:
    """Writes segpack + draft + ledger fragment for one segment. Returns the
    draft's real draft_content_sha1 (for a test to compare against, or to
    deliberately mismatch)."""
    segments_dir = root / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segpack_doc = {
        "seg": seg, "title": seg, "kind": "prose", "word_count": 0,
        "blocks": list(blocks), "footnotes": list(footnotes), "verses": list(verses),
        "names": [], "canon_names": [], "new_names": [], "canon_map": {},
        "split_names": [], "generation_hashes": {},
    }
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps(segpack_doc, ensure_ascii=False), encoding="utf-8"
    )

    draft_doc = {
        "seg": seg,
        "blocks": draft_blocks if draft_blocks is not None else {},
        "footnotes": draft_footnotes if draft_footnotes is not None else {},
        "verses": draft_verses if draft_verses is not None else {},
        "names": [],
        "notes": "",
    }
    draft_path = segments_dir / f"{seg}.draft.json"
    draft_path.write_text(json.dumps(draft_doc, ensure_ascii=False), encoding="utf-8")
    current_sha1 = _draft_content_sha1(draft_doc)

    ledger_dir = root / "runs" / "ledger.d"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    fragment = {"status": status}
    if not omit_reviewed_sha1:
        fragment["reviewed_draft_sha1"] = ("0" * 40) if tamper_reviewed_sha1 else current_sha1
    (ledger_dir / f"{seg}.json").write_text(json.dumps(fragment), encoding="utf-8")
    return current_sha1


# ---------------------------------------------------------------------------
# --report hand-built corpus/attempt factory helpers
# ---------------------------------------------------------------------------


def shard_digest_for(site_ids) -> str:
    return hashlib.sha256(
        json.dumps(sorted(site_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def make_site(site_id, shard_id, *, tag="person", label="Bob", canon_rows=()) -> dict:
    return {
        "site_id": site_id, "tag": tag, "label": label,
        "segment": "001", "carrier_kind": "block", "carrier_locator": "blocks['b1']",
        "shard_id": shard_id,
        "translated_excerpt": {"text": f"...<{tag}>{label}</{tag}>...",
                                "truncated_start": False, "truncated_end": False},
        "canon_rows": [
            {"source_form": sf, "canonical_target_form": tf,
             "source_excerpt": {"text": sf, "truncated_start": False, "truncated_end": False}}
            for sf, tf in canon_rows
        ],
    }


def write_corpus(
    root: Path, *, canon_sha256: str, mode="strip", draft_content_sha1=None,
    dispatchable_sites=(), unavailable_sites=(), durable_root_override=None,
) -> tuple:
    sites = list(dispatchable_sites)
    shards = {}
    by_shard = {}
    for site in sites:
        by_shard.setdefault(site["shard_id"], []).append(site["site_id"])
    for shard_id, site_ids in by_shard.items():
        shards[shard_id] = shard_digest_for(site_ids)

    doc = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "durable_root": durable_root_override if durable_root_override is not None else str(root),
        "mode": mode,
        "canon_sha256": canon_sha256,
        "converged_segments": 1,
        "drafts_excluded_stale_review": 0,
        "carriers_scanned": max(1, len(sites) + len(unavailable_sites)),
        "draft_content_sha1": draft_content_sha1 or {},
        "dispatchable_sites": sites,
        "unavailable_sites": list(unavailable_sites),
        "shards": shards,
        "should_dispatch": bool(sites),
    }
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    out_dir = root / "printed_label_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"corpus_{uuid.uuid4().hex}.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def make_attempt(shard_id, shard_digest, verdicts) -> dict:
    return {
        "schema_version": 1, "shard_id": shard_id, "shard_digest": shard_digest,
        "verdicts": list(verdicts),
    }


def write_attempt(root: Path, name: str, attempt_doc: dict) -> Path:
    path = root / f"{name}.json"
    path.write_text(json.dumps(attempt_doc, ensure_ascii=False), encoding="utf-8")
    return path


def verdict_match(site_id, tag, label, source_form, target_form) -> dict:
    return {
        "site_id": site_id, "tag": tag, "label": label, "verdict": "matches_frozen_target",
        "source_form": source_form, "canonical_target_form": target_form,
    }


def verdict_no_match(site_id, tag, label) -> dict:
    return {"site_id": site_id, "tag": tag, "label": label, "verdict": "no_canon_match"}


def report_fixture(tmp_path: Path, canon_entries=None) -> tuple:
    """A staged durable root plus the sha256 of the canon.json just written
    into it -- the opening every --report test needs, because --report
    re-reads canon.json from disk and refuses a corpus anchored to
    different bytes."""
    root = make_durable_root(tmp_path)
    write_canon(root, {} if canon_entries is None else canon_entries)
    return root, hashlib.sha256((root / "canon.json").read_bytes()).hexdigest()


def run_audit_report(root: Path, corpus_path, corpus_sha256: str, *attempt_paths):
    """--report with one --attempt per path given (none is legitimate: a
    corpus that declared zero shards). Every argument is passed through
    verbatim, so a test can still hand it a deliberately wrong digest."""
    args = ["--report", "--corpus", str(corpus_path), "--expect-corpus-sha256", corpus_sha256]
    for attempt_path in attempt_paths:
        args += ["--attempt", str(attempt_path)]
    return run_audit(root, *args)


# ===========================================================================
# --build-corpus
# ===========================================================================


def test_build_corpus_off_mode_scans_nothing(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup=None)
    write_canon(root, {})
    # A converged segment WITH a marked span sits on disk -- off mode must
    # not even look at it.
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["entity_markup_mode"] == "off"
    assert summary["converged_segments"] == 0
    assert summary["carriers_scanned"] == 0
    assert summary["dispatchable_sites"] == 0
    assert summary["should_dispatch"] is False
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    assert corpus["mode"] == "off"


def test_build_corpus_default_strip_mode_scans_fully(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})  # index_from omitted -> canon -> strip
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["entity_markup_mode"] == "strip"
    assert summary["converged_segments"] == 1
    assert summary["dispatchable_sites"] == 1


def test_build_corpus_index_mode_scans_fully(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(
        root, entity_markup={"tags": ["person"], "index_from": "markup"}, target="obsidian",
    )
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["entity_markup_mode"] == "index"
    assert summary["dispatchable_sites"] == 1


def test_build_corpus_all_three_carrier_kinds(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(
        root, entity_markup={"tags": ["person"]},
        verse_mode="literal_only", apparatus_policy="translate_all",
    )
    write_canon(root, {})
    write_manifest(root, verse_store=[{"vid": "v1", "plain_text": "source verse about Bob"}])
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "prose about Bob"}],
        footnotes=[{"n": 1, "source_text": "footnote about Bob"}],
        verses=[{"vid": "v1", "placeholder": "⟦VERSE_1⟧",
                  "parent_block": "b1", "mount": "embedded"}],
        draft_blocks={"b1": "<person>Bob</person> in prose."},
        draft_footnotes={"1": "<person>Bob</person> in a footnote."},
        draft_verses={"v1": {"rendered": "<person>Bob</person> rendered.",
                              "literal_gloss": "plain gloss, no markup here"}},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    # 4 carriers: block, footnote, verse.rendered, verse.literal_gloss --
    # only 3 of them carry a marked span.
    assert summary["carriers_scanned"] == 4
    assert summary["dispatchable_sites"] == 3
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    kinds = sorted(s["carrier_kind"] for s in corpus["dispatchable_sites"])
    assert kinds == ["block", "footnote", "verse"]


def test_build_corpus_stale_review_draft_excluded_and_counted(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
        tamper_reviewed_sha1=True,
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["converged_segments"] == 0
    assert summary["drafts_excluded_stale_review"] == 1
    assert summary["dispatchable_sites"] == 0


def test_build_corpus_unreadable_ledger_d_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    ledger_dir = root / "runs" / "ledger.d"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "001.json").write_text(json.dumps({"status": "converged"}), encoding="utf-8")
    os.chmod(ledger_dir, 0o000)
    try:
        proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    finally:
        os.chmod(ledger_dir, 0o755)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr


def test_build_corpus_malformed_span_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob spoke."},  # unterminated -- malformed
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert "malformed" in proc.stderr.lower()


def test_build_corpus_malformed_span_in_empty_source_carrier_is_fatal(tmp_path):
    """Round-2 review MINOR: final_audit.term_carriers()'s own add() drops
    ANY carrier whose source text is empty, before _entity_markup_scan()
    ever runs on it -- an ordinary block (b1) is present so the
    zero-carriers guard above cannot mask the gap, and a SECOND block
    (b2) has an empty source but a draft reading an unterminated
    <person> tag. Without the round-2 fix this builds a clean corpus and
    exits 0, exactly the "silently understates the population" failure
    plan §4 forbids."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[
            {"id": "b1", "order_index": 0, "plain_text": "Bob spoke."},
            {"id": "b2", "order_index": 1, "plain_text": ""},
        ],
        draft_blocks={
            "b1": "<person>Bob</person> spoke.",
            "b2": "<person>unterminated",
        },
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert "malformed" in proc.stderr.lower()
    assert "b2" in proc.stderr


def test_build_corpus_malformed_span_in_empty_source_footnote_is_fatal(tmp_path):
    """Round-3 review MINOR 3: _carriers_dropped_for_missing_source()'s
    FOOTNOTE branch, falsifiable on its own -- deleting only this branch
    (leaving the block branch intact) must not leave the suite green."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]}, apparatus_policy="translate_all")
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        footnotes=[{"n": 1, "source_text": ""}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
        draft_footnotes={"1": "<person>unterminated"},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert "malformed" in proc.stderr.lower()
    assert "footnotes" in proc.stderr


def test_build_corpus_malformed_span_in_missing_source_verse_is_fatal(tmp_path):
    """Round-3 review MINOR 3: _carriers_dropped_for_missing_source()'s
    VERSE branch, falsifiable on its own -- the verse's source is MISSING
    entirely from manifest.json's verse.store (never declared, the verse
    analogue of an empty block/footnote source), while the draft still
    delivers rendered content of its own."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]}, verse_mode="literal_only")
    write_canon(root, {})
    write_manifest(root, verse_store=())  # v1 never declared
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        verses=[{"vid": "v1", "placeholder": "⟦VERSE_1⟧", "parent_block": "b1", "mount": "embedded"}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
        draft_verses={"v1": {"rendered": "<person>unterminated", "literal_gloss": "plain gloss"}},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert "malformed" in proc.stderr.lower()
    assert "verses" in proc.stderr


def test_build_corpus_zero_carriers_on_converged_segment_is_fatal(tmp_path):
    """The walk-count guard: a converged segment that contributes zero
    carriers at all (empty blocks/footnotes/verses) is implausible and
    must never read as a clean, empty corpus."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(root, "001", blocks=[], footnotes=[], verses=[])
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "implausible" in proc.stderr.lower()


def test_build_corpus_no_offcanon_prefilter(tmp_path):
    """canon: X -> Aaron, Y -> Bob. Source carries X only; the draft prints
    <person>Bob</person> -- a real defect (label matches ANOTHER canon
    entry's target) that a naive "label is already some canon target"
    prefilter would drop. Must still be reported as a site, anchored only
    to the canon row whose source_form is actually present (X)."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {"X": canon_entry("Aaron"), "Y": canon_entry("Bob")})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "X spoke there."}],
        draft_blocks={"b1": "<person>Bob</person> spoke there."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["dispatchable_sites"] == 1
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    site = corpus["dispatchable_sites"][0]
    assert site["label"] == "Bob"
    assert [r["source_form"] for r in site["canon_rows"]] == ["X"]
    assert [r["canonical_target_form"] for r in site["canon_rows"]] == ["Aaron"]


def test_build_corpus_two_sites_same_label_stay_two(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob and Bob again."}],
        draft_blocks={"b1": "<person>Bob</person> and <person>Bob</person> again."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["dispatchable_sites"] == 2
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    site_ids = [s["site_id"] for s in corpus["dispatchable_sites"]]
    assert len(set(site_ids)) == 2


def test_build_corpus_zero_canon_rows_is_legitimate(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})  # empty canon
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Nothing canon-worthy here."}],
        draft_blocks={"b1": "<person>Bob</person> appears."},
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["dispatchable_sites"] == 1
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    assert corpus["dispatchable_sites"][0]["canon_rows"] == []


def test_build_corpus_canon_row_cap_exceeded_goes_unavailable(tmp_path):
    """Two-sided boundary at the NEW row-count threshold (250/251), now
    genuinely reachable end-to-end: with MAX_ANCHOR_BYTES_PER_SITE raised
    to 16384 in step with this cap, a site with exactly 250 modestly-sized
    rows (measured here at 16,079 anchor bytes -- under budget) is fully
    dispatchable, while one more row (251, 16,647 anchor bytes) is refused
    by the ROW-COUNT gate specifically, which fires before the anchor-byte
    check is even reached."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})

    at_cap_forms = [f"Src{i}" for i in range(pla.MAX_CANON_ROWS_PER_SITE)]
    over_cap_forms = [f"Extra{i}" for i in range(pla.MAX_CANON_ROWS_PER_SITE + 1)]
    write_canon(root, {
        **{f: canon_entry(f"Target{i}") for i, f in enumerate(at_cap_forms)},
        **{f: canon_entry(f"Target{i}") for i, f in enumerate(over_cap_forms)},
    })
    write_segment(
        root, "001",
        blocks=[
            {"id": "at_cap", "order_index": 0, "plain_text": " ".join(at_cap_forms)},
            {"id": "over_cap", "order_index": 1, "plain_text": " ".join(over_cap_forms)},
        ],
        draft_blocks={
            "at_cap": "<person>Bob</person> appears.",
            "over_cap": "<person>Carl</person> appears.",
        },
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["dispatchable_sites"] == 1
    assert summary["unavailable_sites"] == 1
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))

    dispatchable, = corpus["dispatchable_sites"]
    # All THREE anchor fields, not just the label: source_form and
    # canonical_target_form are what validation binds byte-exactly, so a
    # truncation of either after classification would break the anchor
    # five-tuple match silently while a label-only check stayed green.
    assert dispatchable["label"] == "Bob"
    assert len(dispatchable["canon_rows"]) == pla.MAX_CANON_ROWS_PER_SITE
    rows_by_source = {r["source_form"]: r["canonical_target_form"] for r in dispatchable["canon_rows"]}
    assert rows_by_source == {f: f"Target{i}" for i, f in enumerate(at_cap_forms)}

    unavailable, = corpus["unavailable_sites"]
    assert unavailable["reason"] == "canon_row_cap_exceeded"
    assert unavailable["carrier_locator"] == "blocks['over_cap']"


def test_build_corpus_anchor_budget_exceeded_goes_unavailable(tmp_path):
    """Two-sided boundary at the NEW byte value (16384): a site whose
    anchor-only JSON is exactly MAX_ANCHOR_BYTES_PER_SITE bytes is
    dispatchable; one byte more tips it into unavailable_sites. Padding is
    computed, never hardcoded, by growing an ASCII target form one
    character (one UTF-8 byte) at a time until the SAME anchor-JSON
    construction printed_label_audit.py itself builds lands exactly on the
    boundary -- robust to any future change in field names or ordering."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})

    def anchor_bytes(label, source_form, target_form):
        payload = {
            "tag": "person", "label": label,
            "canon_rows": [{"source_form": source_form, "canonical_target_form": target_form}],
        }
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def pad_target_to(label, source_form, size):
        target = ""
        while anchor_bytes(label, source_form, target) < size:
            target += "X"
        assert anchor_bytes(label, source_form, target) == size, (
            "1-byte-per-char ASCII padding could not land exactly on the boundary"
        )
        return target

    at_cap_target = pad_target_to("Bob", "SrcAtCap", pla.MAX_ANCHOR_BYTES_PER_SITE)
    over_cap_target = pad_target_to("Carl", "SrcOverCap", pla.MAX_ANCHOR_BYTES_PER_SITE + 1)

    write_canon(root, {
        "SrcAtCap": canon_entry(at_cap_target),
        "SrcOverCap": canon_entry(over_cap_target),
    })
    write_segment(
        root, "001",
        blocks=[
            {"id": "at_cap", "order_index": 0, "plain_text": "SrcAtCap appears."},
            {"id": "over_cap", "order_index": 1, "plain_text": "SrcOverCap appears."},
        ],
        draft_blocks={
            "at_cap": "<person>Bob</person> appears.",
            "over_cap": "<person>Carl</person> appears.",
        },
    )
    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["dispatchable_sites"] == 1
    assert summary["unavailable_sites"] == 1
    corpus = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))

    dispatchable, = corpus["dispatchable_sites"]
    # All THREE anchor fields, not just the label -- round-1 review MINOR:
    # source_form/canonical_target_form are what validation binds
    # byte-exactly, and a label-only check stays green even if either were
    # truncated after this site cleared the budget check.
    assert dispatchable["label"] == "Bob"
    row, = dispatchable["canon_rows"]
    assert row["source_form"] == "SrcAtCap"
    assert row["canonical_target_form"] == at_cap_target

    unavailable, = corpus["unavailable_sites"]
    assert unavailable["reason"] == "anchor_budget_exceeded"
    assert unavailable["carrier_locator"] == "blocks['over_cap']"


def test_build_corpus_default_durable_root_argument_required(tmp_path):
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup=None)
    write_canon(root, {})
    proc = run_audit(root, "--build-corpus")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "--durable-root" in proc.stderr


def test_usage_errors_neither_or_both_mode(tmp_path):
    root = make_durable_root(tmp_path)
    proc = run_audit(root)
    assert proc.returncode == 2
    assert_no_stdout(proc)
    proc2 = run_audit(root, "--build-corpus", "--report", "--durable-root", str(root))
    assert proc2.returncode == 2
    assert_no_stdout(proc2)


def test_build_corpus_refuses_a_symlinked_output_directory(tmp_path):
    """Security fix (closing pass, MEDIUM): a symlinked printed_label_audit/
    (the corpus/shard output directory) must not silently redirect the
    corpus and every shard file outside durable_root. Verified before the
    fix landed: this exact setup produced exit 0, an in-root corpus_path
    on the success line, and both files written OUTSIDE durable_root --
    O_CREAT|O_EXCL on the two file names and os.link's own refusal to
    follow a symlink AT the destination name do nothing to guard the
    DIRECTORY those names live in."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "printed_label_audit").symlink_to(outside, target_is_directory=True)

    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert list(outside.iterdir()) == [], "nothing may be written outside durable_root"


def test_build_corpus_leaves_no_temp_link_beside_a_published_file(tmp_path):
    """ped-ant P2: the publisher uses os.link, which leaves the SOURCE name in
    place, so the temp name must be unlinked on SUCCESS too. The cleanup was
    guarded by `if not replaced`, so every published corpus and shard kept a
    hidden `.<name>.tmp.<pid>.<hex>` beside it, sharing its inode -- hundreds
    per audit on a large book, and deleting the visible files would not reclaim
    the data because the link count never reached zero. Asserted on the
    DIRECTORY LISTING rather than on link counts, so it stays true whatever the
    temp name happens to be."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )

    proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert proc.returncode == 0, proc.stderr
    line = json.loads(proc.stdout)
    out_dir = Path(line["corpus_path"]).parent
    leftovers = [q.name for q in out_dir.iterdir() if q.name.startswith(".")]
    assert leftovers == [], (
        f"temp links left beside the published files: {leftovers}"
    )


def test_build_corpus_refuses_an_unusable_output_parent_without_a_traceback(tmp_path):
    """ped-ant P2: the parent mkdir sat OUTSIDE the publisher's error
    translation, so a parent that cannot be created raised a bare OSError that
    main() does not catch -- exit 1 and a traceback, against this script's
    documented exit 0/2 contract and the W7 step's failure disposition, which
    disposes of a named FATAL line. Here the output parent is occupied by a
    REGULAR FILE, which is the reproduction the reviewer used."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory\n", encoding="utf-8")

    proc = run_audit(
        root, "--build-corpus", "--durable-root", str(root),
        "--out", str(blocked / "sub" / "corpus.json"),
    )
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "FATAL" in proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr


def test_build_corpus_publishes_no_corpus_when_a_shard_write_fails(tmp_path):
    """Security/robustness fix (closing pass): shards publish BEFORE the
    corpus. A create-once corpus published FIRST left, on any later
    shard-write failure, a corpus on disk referencing shards that do not
    exist -- and permanently wedged a re-run under the SAME explicit
    --out, since create-once refuses to publish a path twice. Forces a
    shard-write failure by pre-creating the exact path build_corpus() will
    try to publish."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "Bob spoke."}],
        draft_blocks={"b1": "<person>Bob</person> spoke."},
    )
    out_path = root / "printed_label_audit" / "corpus.json"
    out_path.parent.mkdir(parents=True)
    (out_path.parent / "corpus.shard_0001.json").write_bytes(b"{}")

    proc = run_audit(root, "--build-corpus", "--durable-root", str(root), "--out", str(out_path))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert not out_path.exists(), (
        "the corpus must not be published when a shard write fails -- publishing it first "
        "would leave it referencing a shard that was never written"
    )


# ===========================================================================
# _pack_shards -- pure-function unit tests (in-process, real module)
# ===========================================================================


def test_named_caps_are_the_measured_literals():
    """A test that reads a cap constant and asserts against ITSELF cannot
    catch the constant being changed -- round-1 review MINOR. These four
    literals are what someone with a reason to move one should have to
    justify against: MAX_CANON_ROWS_PER_SITE (a backstop, real observed
    max 90), MAX_ANCHOR_BYTES_PER_SITE (derived from the shard-fit
    guarantee -- ~8% of MAX_SHARD_BYTES), MAX_SHARD_BYTES (the real
    prompt-size bound), EXCERPT_CONTEXT_CHARS (unrelated to either cap)."""
    assert pla.MAX_CANON_ROWS_PER_SITE == 250
    assert pla.MAX_ANCHOR_BYTES_PER_SITE == 16384
    assert pla.MAX_SHARD_BYTES == 200_000
    assert pla.EXCERPT_CONTEXT_CHARS == 100


def test_pack_shards_site_too_large_for_any_shard_goes_unavailable():
    huge_text = "x" * (pla.MAX_SHARD_BYTES + 1000)
    site = {
        "site_id": "s1", "tag": "person", "label": "Bob", "segment": "001",
        "carrier_kind": "block", "carrier_locator": "blocks['b1']",
        "translated_excerpt": {"text": huge_text, "truncated_start": False, "truncated_end": False},
        "canon_rows": [],
    }
    packed, unavailable, shards = pla._pack_shards([site])
    assert packed == []
    assert shards == {}
    assert len(unavailable) == 1
    assert unavailable[0]["reason"] == "exceeds_shard_budget_alone"
    assert unavailable[0]["site_id"] == "s1"


def test_pack_shards_deterministic_and_within_budget():
    def make(i):
        return {
            "site_id": f"s{i:03d}", "tag": "person", "label": "Bob", "segment": "001",
            "carrier_kind": "block", "carrier_locator": "blocks['b1']",
            "translated_excerpt": {"text": "x" * 1000, "truncated_start": False, "truncated_end": False},
            "canon_rows": [],
        }
    sites = [make(i) for i in range(50)]
    packed1, unavail1, shards1 = pla._pack_shards([dict(s) for s in sites])
    packed2, unavail2, shards2 = pla._pack_shards([dict(s) for s in sites])
    assert unavail1 == unavail2 == []
    assert shards1 == shards2
    assert [s["site_id"] for s in packed1] == [s["site_id"] for s in packed2]
    assert len(shards1) >= 1
    for shard_id in shards1:
        shard_sites = [s for s in packed1 if s["shard_id"] == shard_id]
        # THE SAME SERIALIZATION _pack_shards itself sizes with, real
        # digest included -- round 3 review: a hand-rolled json.dumps
        # call, even one built from _build_shard_payload's own shape, is
        # a SECOND serialization that can silently disagree with the one
        # packing actually used (different separators/sort_keys change
        # the byte count on their own, independent of ensure_ascii).
        size = len(pla._serialize_shard_payload(shard_id, shards1[shard_id], shard_sites))
        assert size <= pla.MAX_SHARD_BYTES
        expected_digest = shard_digest_for([s["site_id"] for s in shard_sites])
        assert shards1[shard_id] == expected_digest


def test_pack_shards_splits_into_multiple_shards_when_budget_forces_it():
    # Each site is ~1000 bytes; force > MAX_SHARD_BYTES total so more than
    # one shard is required.
    n = (pla.MAX_SHARD_BYTES // 1000) + 5
    sites = [{
        "site_id": f"s{i:04d}", "tag": "person", "label": "Bob", "segment": "001",
        "carrier_kind": "block", "carrier_locator": "blocks['b1']",
        "translated_excerpt": {"text": "x" * 950, "truncated_start": False, "truncated_end": False},
        "canon_rows": [],
    } for i in range(n)]
    packed, unavailable, shards = pla._pack_shards(sites)
    assert unavailable == []
    assert len(packed) == n
    assert len(shards) >= 2


def test_pack_shards_final_serialized_shard_stays_within_budget_near_the_boundary():
    """Regression covering all three review rounds found in this function:

      - round 1 (MAJOR): the packer's size probe must measure each site
        WITH the shard_id key it will actually carry once dispatched, not
        without it -- invisible against a handful of ~1000-byte sites but
        costing thousands of bytes across hundreds of small ones (the
        reviewer's own repro: 593 sites, 199,878 bytes measured without
        the key, 215,296 once every site carried it).
      - round 2 (MAJOR): the sized object and the SHIPPED object must be
        the SAME object, built by ONE function (_build_shard_payload) --
        not two separately-maintained ones that can drift a field apart.
        This test therefore builds its own "final, as-dispatched" object
        through that SAME builder, with the packer's own real digest, not
        by hand -- reconstructing `{"shard_id", "sites"}` independently
        (as this test used to) is exactly the drift round 2 found, one
        level up, and would stay green while the real payload -- which
        also carries shard_digest -- goes over budget.
      - round 1's second bug: a site that triggers a shard close must be
        re-stamped with the FRESH shard_id it will actually belong to,
        not the stale one computed before the close.

    Packs just enough ~50-byte sites (computed via the same builder, so
    the marginal cost used to size the run is exact) to fill a shard
    within a couple of marginal-costs of MAX_SHARD_BYTES -- close to the
    boundary, not comfortably under it."""
    def make_site(i, text_len):
        return {
            "site_id": f"s{i:06d}", "tag": "person", "label": "Bob", "segment": "001",
            "carrier_kind": "block", "carrier_locator": "blocks['b1']",
            "translated_excerpt": {"text": "x" * text_len, "truncated_start": False, "truncated_end": False},
            "canon_rows": [],
        }

    text_len = 50
    probe_shard_id = "shard_0001"

    def size_with_shard_id(sites):
        sited = [{**s, "shard_id": probe_shard_id} for s in sites]
        return len(pla._serialize_shard_payload(probe_shard_id, pla._PLACEHOLDER_SHARD_DIGEST, sited))

    one = make_site(0, text_len)
    two = make_site(1, text_len)
    marginal = size_with_shard_id([one, two]) - size_with_shard_id([one])
    # Deliberately overshoot one shard's worth by a healthy margin, so the
    # first (full) shard is packed as close to the cap as the greedy
    # packer's own stopping rule allows, and a second shard is guaranteed.
    n_sites = (pla.MAX_SHARD_BYTES // marginal) + 50
    sites = [make_site(i, text_len) for i in range(n_sites)]

    packed, unavailable, shards = pla._pack_shards(sites)
    assert unavailable == []
    assert len(shards) >= 2
    # No site dropped and none duplicated across the two closes this test
    # forces -- the shape that exposed the stale-shard_id bug.
    assert sorted(s["site_id"] for s in packed) == sorted(s["site_id"] for s in sites)

    by_shard = {}
    for s in packed:
        by_shard.setdefault(s["shard_id"], []).append(s)
    assert set(by_shard) == set(shards)

    max_observed = 0
    for shard_id, sites_in_shard in by_shard.items():
        # THE SAME SERIALIZATION packing used, with the REAL digest -- this
        # is what a dispatched shard actually is, never a hand-rolled
        # json.dumps call that can silently disagree on separators/
        # sort_keys even when built from the right shape (round 3).
        real_digest = shards[shard_id]
        size = len(pla._serialize_shard_payload(shard_id, real_digest, sites_in_shard))
        assert size <= pla.MAX_SHARD_BYTES, (
            f"{shard_id} serialises to {size} bytes, over the {pla.MAX_SHARD_BYTES} budget "
            f"-- packing's sizing did not match _serialize_shard_payload's real bytes"
        )
        max_observed = max(max_observed, size)
        # The shard's declared digest must match EXACTLY the site_ids
        # grouped under this shard_id by each site's OWN field -- the
        # stale-shard_id bug left a site's field pointing at the shard
        # that had just closed, one below its digest's real membership,
        # so a per-shard byte count alone (above) would not have caught
        # it: both sides of the mismatch stayed the same length.
        expected_digest = shard_digest_for([s["site_id"] for s in sites_in_shard])
        assert real_digest == expected_digest, (
            f"{shard_id}'s declared digest does not match the site_ids whose own "
            "shard_id field claims membership in it"
        )

    # Close to the boundary, not comfortably under it: proves this test
    # actually exercises the fix rather than padding with headroom the
    # pre-fix bug would never have reached.
    assert max_observed > pla.MAX_SHARD_BYTES - marginal * 2


def test_pack_shards_never_settles_on_a_batch_whose_real_payload_is_over_budget():
    """A DETERMINISTIC (not probabilistic) regression for the round-2
    review MAJOR, complementing the near-boundary test above. That test
    packs many sites and checks whether the result happens to land close
    to the cap -- proportionate to round 1's bug (a PER-SITE field, whose
    omitted cost scales with site count and eventually crosses any
    budget), but a missing shard_digest is a single PER-SHARD, fixed-size
    field (~84 bytes here): whether omitting it flips an accept/reject
    decision depends on the packer's stopping point landing within that
    narrow window of the cap, which an "overshoot broadly" test may or
    may not hit on a given run.

    This test instead ENGINEERS the exact adversarial batch: it pads a
    site so that the batch's size WITHOUT a shard_digest field --
    precisely what a sizer missing that field would compute -- lands
    EXACTLY at MAX_SHARD_BYTES. The batch's REAL payload (built through
    _build_shard_payload with its real digest, a pure function of the
    site_id set and thus computable in advance) is therefore a fixed,
    known number of bytes OVER budget. A correctly-sized packer must
    never produce a shard equal to this exact batch -- it has to stop one
    site earlier. A packer whose size_of() omits (or otherwise
    under-builds) the digest field will land on it every time, by
    construction, not by chance."""
    def make_site(i, text_len=0):
        return {
            "site_id": f"s{i:06d}", "tag": "person", "label": "Bob", "segment": "001",
            "carrier_kind": "block", "carrier_locator": "blocks['b1']",
            "translated_excerpt": {"text": "x" * text_len, "truncated_start": False, "truncated_end": False},
            "canon_rows": [],
        }

    n_filler = 600
    filler = [make_site(i) for i in range(n_filler)]

    def without_digest_size(sites):
        # Deliberately the OLD, incomplete shape (round 2's own bug) --
        # used only to TARGET the adversarial boundary, never asserted as
        # what packing should measure. Matches _serialize_shard_payload's
        # OTHER options (compact separators, sort_keys) exactly, so the
        # ONLY difference from the real serialization is the missing
        # shard_digest field -- not an incidental formatting difference.
        sited = [{**s, "shard_id": "shard_0001"} for s in sites]
        payload = {"shard_id": "shard_0001", "sites": sited}
        return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # Padding length computed directly, not grown one ASCII character (one
    # byte) at a time re-serializing a 600+-item list on every step -- each
    # 'X' costs exactly 1 byte, so one baseline measurement (text="") gives
    # the exact remaining budget in a single arithmetic step.
    padded = make_site(n_filler)
    baseline = without_digest_size(filler + [padded])
    needed = pla.MAX_SHARD_BYTES - baseline
    assert needed >= 0, "n_filler is already too large to leave room for padding"
    padded["translated_excerpt"]["text"] = "X" * needed
    batch = filler + [padded]
    assert without_digest_size(batch) == pla.MAX_SHARD_BYTES

    real_digest = shard_digest_for([s["site_id"] for s in batch])
    real_size = len(pla._serialize_shard_payload(
        "shard_0001", real_digest, [{**s, "shard_id": "shard_0001"} for s in batch]
    ))
    digest_field_cost = real_size - pla.MAX_SHARD_BYTES
    assert digest_field_cost > 0, "the digest field must cost SOME bytes, or this test proves nothing"

    straw = make_site(999999)
    packed, unavailable, shards = pla._pack_shards(batch + [straw])
    assert unavailable == []

    by_shard = {}
    for s in packed:
        by_shard.setdefault(s["shard_id"], []).append(s)

    batch_ids = {s["site_id"] for s in batch}
    shard1_ids = {s["site_id"] for s in by_shard.get("shard_0001", [])}
    assert shard1_ids != batch_ids, (
        f"shard_0001 landed on EXACTLY the engineered batch, whose real payload is "
        f"{digest_field_cost} bytes over MAX_SHARD_BYTES -- packing's sizing does not "
        "match _build_shard_payload's real shape"
    )
    for shard_id, sites_in_shard in by_shard.items():
        size = len(pla._serialize_shard_payload(shard_id, shards[shard_id], sites_in_shard))
        assert size <= pla.MAX_SHARD_BYTES


def test_pack_shards_ensure_ascii_false_is_load_bearing_on_non_ascii_content():
    """Round-3 review item 6: an engineered NON-ASCII boundary. The
    reviewer's own repro found the SAME shard 199,853 bytes with
    ensure_ascii=False (this script's real choice) and 442,641 bytes with
    Python's json.dumps DEFAULT (ensure_ascii=True) -- more than double,
    on Hebrew content. Every other boundary test in this file is
    ASCII-only, which is exactly why that difference went unnoticed on a
    corpus that is almost entirely Hebrew. Builds sites carrying real
    Hebrew text and confirms: (a) the REAL packer/serializer (this
    script's actual ensure_ascii=False choice) fits each shard within
    budget, and (b) the SAME exact payload, re-serialized with
    json.dumps's own DEFAULT (escaping every Hebrew codepoint to
    \\uXXXX), would NOT have fit -- proving the choice is load-bearing,
    not incidental."""
    hebrew_text = "שלום עולם זה מבחן של תווים לא לטיניים בתוך המטען הזה"

    def make_site(i):
        return {
            "site_id": f"s{i:04d}", "tag": "person", "label": "בוב",
            "segment": "001", "carrier_kind": "block", "carrier_locator": "blocks['b1']",
            "translated_excerpt": {
                "text": hebrew_text * 6, "truncated_start": False, "truncated_end": False,
            },
            "canon_rows": [
                {
                    "source_form": hebrew_text, "canonical_target_form": hebrew_text,
                    "source_excerpt": {
                        "text": hebrew_text * 4, "truncated_start": False, "truncated_end": False,
                    },
                }
                for _ in range(20)
            ],
        }

    sites = [make_site(i) for i in range(15)]
    packed, unavailable, shards = pla._pack_shards(sites)
    assert unavailable == []
    assert len(shards) >= 2  # enough sites that at least one shard is genuinely full

    by_shard = {}
    for s in packed:
        by_shard.setdefault(s["shard_id"], []).append(s)

    found_a_shard_where_default_would_have_overflowed = False
    for shard_id, sites_in_shard in by_shard.items():
        real_size = len(pla._serialize_shard_payload(shard_id, shards[shard_id], sites_in_shard))
        assert real_size <= pla.MAX_SHARD_BYTES

        # The SAME shape (_build_shard_payload), re-serialized with
        # json.dumps's own default -- never asserted as what this script
        # does, only as the comparison point that makes the choice visible.
        default_ascii_payload = pla._build_shard_payload(shard_id, shards[shard_id], sites_in_shard)
        default_ascii_size = len(json.dumps(default_ascii_payload, ensure_ascii=True).encode("utf-8"))
        if default_ascii_size > pla.MAX_SHARD_BYTES:
            found_a_shard_where_default_would_have_overflowed = True

    assert found_a_shard_where_default_would_have_overflowed, (
        "this test's Hebrew content did not actually exercise the ensure_ascii=False vs "
        "default(True) gap -- strengthen it rather than treating a pass as proof"
    )


def test_pack_shards_survives_the_shard_id_width_rollover():
    """Round-3 review MINOR 2: the single-site "does this fit alone"
    check sizes against the CURRENT prospective shard id, but a site that
    triggers a shard close gets RESTAMPED with the next id afterward and
    was never re-sized -- at shard_9999 -> shard_10000 the id string
    itself grows by one character in TWO places (the shard payload's own
    "shard_id" key and this site's own "shard_id" field: +2 bytes total),
    which can tip a site that fit under the old, shorter id over budget
    under the new, longer one.

    A uniform "every site is half the budget" run does NOT exercise this:
    with that much headroom a 2-byte width change never flips the
    decision (measured while writing this test -- it silently passed).
    This test instead precisely engineers ONE site (index 9999, the one
    that lands exactly on the shard_9999 -> shard_10000 transition when
    every other site forces exactly one shard each) to fit EXACTLY at
    MAX_SHARD_BYTES under a 4-digit id and 2 bytes OVER it under a
    5-digit one -- computed via the same _serialize_shard_payload packing
    itself uses, not an approximation."""
    def make_site(i, text_len):
        return {
            "site_id": f"s{i:06d}", "tag": "person", "label": "Bob", "segment": "001",
            "carrier_kind": "block", "carrier_locator": "blocks['b1']",
            "translated_excerpt": {"text": "x" * text_len, "truncated_start": False, "truncated_end": False},
            "canon_rows": [],
        }

    def size_alone(shard_id, text_len):
        sited = {**make_site(0, text_len), "shard_id": shard_id}
        return len(pla._serialize_shard_payload(shard_id, pla._PLACEHOLDER_SHARD_DIGEST, [sited]))

    # Half the budget each -- two filler sites never fit in the same
    # shard, so shard_n increments by exactly one per site and the
    # rollover is reached predictably at a known index.
    filler_text_len = pla.MAX_SHARD_BYTES // 2
    n_before = 9999  # sites 0..9998 -> shard_0001..shard_9999
    boundary_index = n_before  # site 9999 -> lands on shard_10000

    baseline = size_alone("shard_9999", 0)
    boundary_text_len = pla.MAX_SHARD_BYTES - baseline
    assert boundary_text_len >= 0
    assert size_alone("shard_9999", boundary_text_len) == pla.MAX_SHARD_BYTES
    over_by = size_alone("shard_10000", boundary_text_len) - pla.MAX_SHARD_BYTES
    assert over_by > 0, (
        "the engineered boundary site is not actually over budget under the wider id -- "
        "strengthen this test rather than treating a pass as proof"
    )

    sites = [make_site(i, filler_text_len) for i in range(n_before)]
    sites.append(make_site(boundary_index, boundary_text_len))
    sites += [make_site(boundary_index + 1 + j, filler_text_len) for j in range(10)]
    n = len(sites)

    packed, unavailable, shards = pla._pack_shards(sites)
    assert len(packed) + len(unavailable) == n
    assert len(set(s["site_id"] for s in packed) | set(u["site_id"] for u in unavailable)) == n
    assert any(int(shard_id.split("_")[1]) >= 10000 for shard_id in shards), (
        "this test did not actually reach the shard_9999 -> shard_10000 rollover -- "
        "increase n_before rather than treating a pass as proof"
    )

    boundary_site_id = make_site(boundary_index, boundary_text_len)["site_id"]
    boundary_packed = [s for s in packed if s["site_id"] == boundary_site_id]
    boundary_unavailable = [u for u in unavailable if u["site_id"] == boundary_site_id]
    assert len(boundary_packed) + len(boundary_unavailable) == 1
    if boundary_packed:
        assert boundary_packed[0]["shard_id"] != "shard_10000", (
            "the engineered boundary site was packed into shard_10000 anyway -- the "
            "rollover re-check did not fire"
        )
    else:
        assert boundary_unavailable[0]["reason"] == "exceeds_shard_budget_alone"

    by_shard = {}
    for s in packed:
        by_shard.setdefault(s["shard_id"], []).append(s)
    for shard_id, sites_in_shard in by_shard.items():
        size = len(pla._serialize_shard_payload(shard_id, shards[shard_id], sites_in_shard))
        assert size <= pla.MAX_SHARD_BYTES, (
            f"{shard_id} serialises to {size} bytes, over the {pla.MAX_SHARD_BYTES} budget -- "
            "the shard-id-width rollover was not re-checked after a restamp"
        )
    for entry in unavailable:
        assert entry["reason"] == "exceeds_shard_budget_alone"


def test_excerpt_window_truncation_markers():
    text = "0123456789" * 50  # 500 chars
    win = pla._excerpt_window(text, 250, 255)
    assert win["truncated_start"] is True
    assert win["truncated_end"] is True
    assert win["text"].startswith(pla.TRUNCATION_MARKER)
    assert win["text"].endswith(pla.TRUNCATION_MARKER)

    short = "short text"
    win2 = pla._excerpt_window(short, 0, len(short))
    assert win2["truncated_start"] is False
    assert win2["truncated_end"] is False
    assert win2["text"] == short


def test_canon_rows_for_source_text_deterministic_order():
    entries = {
        "Beta": canon_entry("BetaTarget"),
        "Alpha": canon_entry("AlphaTarget"),
    }
    source = "Alpha appears before Beta in this sentence."
    rows = pla._canon_rows_for_source_text(entries, source)
    assert [r[0] for r in rows] == ["Alpha", "Beta"]


def test_carrier_kind_from_locator():
    assert pla._carrier_kind_from_locator("blocks['b1']") == "block"
    assert pla._carrier_kind_from_locator("footnotes['1']") == "footnote"
    assert pla._carrier_kind_from_locator("verses['v1'].rendered") == "verse"
    with pytest.raises(pla.PrintedLabelAuditFatalError):
        pla._carrier_kind_from_locator("nodes['n1']")


# ===========================================================================
# --report
# ===========================================================================


def test_report_refuses_matches_frozen_target_missing_canonical_target_form(tmp_path):
    """Schema-level: a matches_frozen_target verdict missing either anchor
    field is refused (source_form present here, canonical_target_form
    absent)."""
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001", canon_rows=[("X", "Aaron")])
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    shard_digest = shard_digest_for(["s1"])
    bad_verdict = {"site_id": "s1", "tag": "person", "label": "Bob",
                   "verdict": "matches_frozen_target", "source_form": "X"}
    attempt_path = write_attempt(root, "bad", make_attempt("shard_0001", shard_digest, [bad_verdict]))
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "schema" in proc.stderr.lower()


def test_report_end_to_end_real_build_corpus_output(tmp_path):
    """THE INTEGRATION SEAM: --build-corpus for real, then feed that ACTUAL
    corpus file and its real sha256 into --report with hand-written
    attempt shard(s) -- never two separately hand-built fixtures."""
    root = make_durable_root(tmp_path)
    write_marker_and_profile(root, entity_markup={"tags": ["person"]})
    write_canon(root, {"X": canon_entry("Aaron")})
    write_segment(
        root, "001",
        blocks=[{"id": "b1", "order_index": 0, "plain_text": "X spoke, and Carl too."}],
        draft_blocks={"b1": "<person>Bob</person> spoke, and <person>Carl</person> too."},
    )
    build_proc = run_audit(root, "--build-corpus", "--durable-root", str(root))
    assert build_proc.returncode == 0, build_proc.stderr
    build_summary = parse_stdout(build_proc)
    corpus_path = Path(build_summary["corpus_path"])
    corpus_sha256 = build_summary["corpus_sha256"]
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))

    assert len(corpus["dispatchable_sites"]) == 2
    assert len(corpus["shards"]) == 1
    (shard_id, shard_digest), = corpus["shards"].items()

    # THE EMITTED SHARD FILE (round 3): --build-corpus writes it beside the
    # corpus, and it is what a session dispatches VERBATIM -- read it back
    # and confirm it matches the corpus's own records, rather than trusting
    # a reconstruction from dispatchable_sites.
    assert build_summary["shard_dir"] == str(corpus_path.parent)
    shard_path = corpus_path.parent / f"{corpus_path.stem}.{shard_id}.json"
    assert shard_path.is_file(), f"no emitted shard file at {shard_path}"
    assert shard_path.stat().st_size <= pla.MAX_SHARD_BYTES
    shard_payload = json.loads(shard_path.read_bytes())
    assert shard_payload["shard_id"] == shard_id
    assert shard_payload["shard_digest"] == shard_digest
    assert {s["site_id"] for s in shard_payload["sites"]} == {
        s["site_id"] for s in corpus["dispatchable_sites"]
    }

    by_label = {s["label"]: s for s in corpus["dispatchable_sites"]}
    bob_site = by_label["Bob"]
    carl_site = by_label["Carl"]
    assert bob_site["canon_rows"] == [
        {"source_form": "X", "canonical_target_form": "Aaron",
         "source_excerpt": bob_site["canon_rows"][0]["source_excerpt"]}
    ]

    attempt = make_attempt(shard_id, shard_digest, [
        verdict_match(bob_site["site_id"], "person", "Bob", "X", "Aaron"),
        verdict_no_match(carl_site["site_id"], "person", "Carl"),
    ])
    attempt_path = write_attempt(root, "attempt_shard1", attempt)

    report_proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert report_proc.returncode == 0, report_proc.stderr
    render = parse_stdout(report_proc)
    assert render["success"] is True
    assert render["mode"] == "report"
    assert render["sites_total"] == 2
    assert render["matches_frozen_target_count"] == 1
    assert render["no_canon_match_count"] == 1
    assert render["unavailable_sites_total"] == 0
    rendered_by_label = {s["label"]: s for s in render["sites"]}
    assert rendered_by_label["Bob"]["verdict"] == "matches_frozen_target"
    assert rendered_by_label["Bob"]["source_form"] == "X"
    assert rendered_by_label["Bob"]["canonical_target_form"] == "Aaron"
    assert rendered_by_label["Carl"]["verdict"] == "no_canon_match"
    assert "source_form" not in rendered_by_label["Carl"]


def test_report_writes_no_file(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    corpus_path, corpus_sha256 = write_corpus(root, canon_sha256=canon_sha256)
    before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 0, proc.stderr
    after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    assert before == after


def test_report_refuses_corpus_hash_mismatch(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    corpus_path, _real_sha = write_corpus(root, canon_sha256=canon_sha256)
    proc = run_audit_report(root, corpus_path, "0" * 64)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "REFUSED" in proc.stderr


def test_report_refuses_a_corpus_from_a_different_durable_root(tmp_path):
    """Security/correctness fix (closing pass): --report is self-anchored
    to THIS install's own durable root by design, but nothing previously
    compared the corpus's OWN recorded durable_root against it -- a
    corpus built from a different root re-checked canon.json and every
    draft against the WRONG project and blamed the wrong cause. Before
    this fix the failure surfaced as "draft ... changed since this corpus
    was built", which is false: no draft was ever touched."""
    root, canon_sha256 = report_fixture(tmp_path)
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, durable_root_override="/somewhere/else/entirely",
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "different durable root" in proc.stderr.lower()
    assert "changed since this corpus was built" not in proc.stderr, (
        "must name the real cause (a durable_root mismatch), never the misleading "
        "\"draft ... changed since this corpus was built\" message a stale check "
        "used to produce for the exact same symptom"
    )


def test_report_refuses_a_deeply_nested_attempt_without_crashing(tmp_path):
    """Security fix (closing pass): _read_json_bytes only caught
    json.JSONDecodeError, but a sufficiently deeply nested document
    raises RecursionError instead -- not a subclass -- escaping as an
    uncaught traceback (exit 1), contradicting this module's own
    documented "0 clean, 2 every failure" contract. Uses the reviewer's
    own verified repro depth."""
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    depth = 200_000
    nested_path = root / "deeply_nested_attempt.json"
    nested_path.write_text("[" * depth + "]" * depth, encoding="utf-8")

    proc = run_audit_report(root, corpus_path, corpus_sha256, nested_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr
    assert "FATAL" in proc.stderr


def test_report_refuses_moved_canon(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, {"A": canon_entry("Alpha")})
    corpus_path, corpus_sha256 = write_corpus(root, canon_sha256="0" * 64)
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "canon.json" in proc.stderr


def test_report_refuses_moved_draft(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    write_segment(root, "001", blocks=[{"id": "b1", "order_index": 0, "plain_text": "hi"}],
                  draft_blocks={"b1": "hi there"})
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, draft_content_sha1={"001": "0" * 40},
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "draft" in proc.stderr.lower()


def test_report_refuses_schema_invalid_attempt_missing_shard_digest(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    bad_attempt = {"schema_version": 1, "shard_id": "shard_0001",
                   "verdicts": [verdict_no_match("s1", "person", "Bob")]}  # missing shard_digest
    attempt_path = write_attempt(root, "bad", bad_attempt)
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "schema" in proc.stderr.lower()


def test_report_refuses_no_canon_match_carrying_source_form(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    shard_digest = shard_digest_for(["s1"])
    bad_verdict = {"site_id": "s1", "tag": "person", "label": "Bob",
                   "verdict": "no_canon_match", "source_form": "X"}
    attempt_path = write_attempt(root, "bad", make_attempt("shard_0001", shard_digest, [bad_verdict]))
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


def test_report_refuses_invented_site_id(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    shard_digest = shard_digest_for(["s1"])
    attempt_path = write_attempt(
        root, "bad",
        make_attempt("shard_0001", shard_digest, [verdict_no_match("nonexistent", "person", "Bob")]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "does not exist in the corpus" in proc.stderr


def test_report_refuses_invented_anchor_pair(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site = make_site("s1", "shard_0001", canon_rows=[("X", "Aaron")])
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site],
    )
    shard_digest = shard_digest_for(["s1"])
    attempt_path = write_attempt(
        root, "bad",
        make_attempt("shard_0001", shard_digest, [
            verdict_match("s1", "person", "Bob", "Y", "Zephyr"),  # never offered by the corpus
        ]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "never offered" in proc.stderr


def test_report_refuses_missing_shard(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    site2 = make_site("s2", "shard_0002", label="Carl")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1, site2],
    )
    shard1_digest = shard_digest_for(["s1"])
    attempt_path = write_attempt(
        root, "shard1",
        make_attempt("shard_0001", shard1_digest, [verdict_no_match("s1", "person", "Bob")]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "missing" in proc.stderr.lower()


def test_report_refuses_extra_shard(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1],
    )
    shard1_digest = shard_digest_for(["s1"])
    shard2_digest = shard_digest_for(["s2"])
    attempt1 = write_attempt(
        root, "shard1",
        make_attempt("shard_0001", shard1_digest, [verdict_no_match("s1", "person", "Bob")]),
    )
    attempt2 = write_attempt(
        root, "shard2",
        make_attempt("shard_0002", shard2_digest, [verdict_no_match("s2", "person", "Carl")]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt1, attempt2)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


def test_report_refuses_duplicate_shard(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1],
    )
    shard1_digest = shard_digest_for(["s1"])
    attempt = make_attempt("shard_0001", shard1_digest, [verdict_no_match("s1", "person", "Bob")])
    attempt_a = write_attempt(root, "a", attempt)
    attempt_b = write_attempt(root, "b", attempt)
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_a, attempt_b)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "already answered" in proc.stderr


def test_report_refuses_site_answered_in_wrong_shard(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    site2 = make_site("s2", "shard_0002", label="Carl")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1, site2],
    )
    shard1_digest = shard_digest_for(["s1"])
    shard2_digest = shard_digest_for(["s2"])
    # shard_0001's attempt answers s2, which actually belongs to shard_0002.
    attempt1 = write_attempt(
        root, "shard1",
        make_attempt("shard_0001", shard1_digest, [verdict_no_match("s2", "person", "Carl")]),
    )
    attempt2 = write_attempt(
        root, "shard2",
        make_attempt("shard_0002", shard2_digest, [verdict_no_match("s1", "person", "Bob")]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt1, attempt2)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "wrong shard" in proc.stderr


def test_report_refuses_duplicate_verdict_within_shard(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1],
    )
    shard1_digest = shard_digest_for(["s1"])
    attempt_path = write_attempt(
        root, "shard1",
        make_attempt("shard_0001", shard1_digest, [
            verdict_no_match("s1", "person", "Bob"),
            verdict_no_match("s1", "person", "Bob"),
        ]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "twice" in proc.stderr


def test_report_refuses_unanswered_dispatchable_site(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    site1 = make_site("s1", "shard_0001")
    site2 = make_site("s2", "shard_0001", label="Carl")
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[site1, site2],
    )
    shard1_digest = shard_digest_for(["s1", "s2"])
    attempt_path = write_attempt(
        root, "shard1",
        make_attempt("shard_0001", shard1_digest, [verdict_no_match("s1", "person", "Bob")]),
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256, attempt_path)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "never answered" in proc.stderr


def test_report_renders_unavailable_sites_with_totals(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    unavailable = [{
        "site_id": "u1", "tag": "person", "segment": "001",
        "carrier_kind": "block", "carrier_locator": "blocks['b1']",
        "reason": "canon_row_cap_exceeded",
    }]
    corpus_path, corpus_sha256 = write_corpus(
        root, canon_sha256=canon_sha256, dispatchable_sites=[], unavailable_sites=unavailable,
    )
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 0, proc.stderr
    render = parse_stdout(proc)
    assert render["sites_total"] == 0
    assert render["unavailable_sites_total"] == 1
    assert render["unavailable_sites"] == unavailable


def test_report_zero_shards_needs_zero_attempts(tmp_path):
    root, canon_sha256 = report_fixture(tmp_path)
    corpus_path, corpus_sha256 = write_corpus(root, canon_sha256=canon_sha256, mode="off")
    proc = run_audit_report(root, corpus_path, corpus_sha256)
    assert proc.returncode == 0, proc.stderr
    render = parse_stdout(proc)
    assert render["sites_total"] == 0
    assert render["entity_markup_mode"] == "off"


def test_report_bad_hex_expect_sha256_is_usage_error(tmp_path):
    root = make_durable_root(tmp_path)
    proc = run_audit_report(root, root / "nonexistent.json", "not-hex")
    assert proc.returncode == 2
    assert_no_stdout(proc)


def test_report_requires_corpus_and_expect_sha256(tmp_path):
    root = make_durable_root(tmp_path)
    proc = run_audit(root, "--report")
    assert proc.returncode == 2
    assert_no_stdout(proc)
