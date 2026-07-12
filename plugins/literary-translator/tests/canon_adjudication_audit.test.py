"""tests/canon_adjudication_audit.test.py -- regression-lock suite for
scripts/canon_adjudication_audit.py, the opt-in human-adjudication rollout
gate ported from historiettes-t3's audit_human_adjudications.py (see that
script's own module docstring and SKILL.md's "Canon human-adjudication
audit" section for the authoritative spec -- the frozen build plan's §1/§2
are the source of truth this file asserts against).

## Fixture strategy

Every test builds an isolated `durable_root` on disk (the REAL
canon_adjudication_audit.py copied into `{root}/scripts/`, so its
self-anchored `DURABLE_ROOT = Path(__file__).resolve().parents[1]` resolves
against the fixture, never this repo's real assets tree) and invokes it
exactly as production does: `python3 {durable_root}/scripts/
canon_adjudication_audit.py --check [--init] [--force] [--advisory]
[--pair-review-cap N]`. `canon.json` / `canon_adjudications.json` live at
the durable_root default paths -- no `--canon-path`/`--adjudications-path`
override is exercised (default-path reading is implicitly covered by every
test).

Assertions target the CONTRACT -- schema-validated stdout keys, exit codes,
specific `totals` counts -- never fragile exact stderr wording (only
structural stderr non-emptiness, where the contract explicitly promises a
named error / visible note).

## Key computation (test-side)

The adjudication/override "key" a required item must be addressed by is
`"{kind}::" + sha256(canonical_json(identity_struct)).hexdigest()` (full
64-hex digest; `canonical_json = json.dumps(x, sort_keys=True,
ensure_ascii=False, separators=(",", ":"))`) -- a frozen, exhaustively
specified deterministic hash (plan §1 "KEY CONSTRUCTION" / §7.5 / §7b.5),
NOT the categorization/enumeration/blocking-count logic actually under
test. The summary schema is aggregate-only (no per-item key listing), so
fixtures that need to pre-author a `confirmed_ok`/`adverse`/risk-override
verdict for a specific required item independently RECOMPUTE that item's
key from the same documented algorithm (see `N()`/`canonical_json()`/
`make_key()`/`cat1_key()`/`cat2_key()`/`cat3_key()`/`cat4_key()` below) --
mirroring how `review_artifact_check.test.py` computes `hashlib.sha1(...)`
directly rather than needing the script to print a hash anywhere.

## Coverage (mapped to the frozen plan's §4 numbered cases; see the
   individual test docstrings/comments for the exact case each covers)

  1. --init lifecycle: fresh write, untouched re-init, --force reset.
  2. neither --init nor --check: usage error, exit 2, no stdout.
  3. absent canon.json: canon_present:false, 0 items, exit 0.
  4. top-level malformed canon (not object / entries not object /
     review_queue not array / unreadable JSON): fatal exit 2, no stdout.
  5. Cat 1 same-target duplicate + verdict lifecycle (missing -> confirmed
     -> adverse).
  6. Cat 1 record-count rule (two map keys, identical source_form field).
  7. Cat 2 existing_merge + verdict lifecycle.
  8. Cat 3 all-pairs under cap + verdict lifecycle on one pair.
  9. Cat 3 disjointness: a shared-normalized-source pair excluded from Cat 3
     (shows as Cat 1 instead).
 10. Cat 3 cap early-stop: cap-note + zero per-pair items, override
     lifecycle, stale-fingerprint re-blocking.
 11. Cat 4 review_queue lifecycle: risk-override clears, drain clears,
     content change re-blocks, is_proper_name:false still counted, a
     missing-only-`note` item stays enumerated (not fatal).
 12. Enumeration-critical row malformation (entry missing
     canonical_target_form/is_proper_name/basis; queued missing
     source_form): fatal exit 2.
 13. Invalid verdict_class (blocking, not fatal); empty reviewed_by.
 14. Structural adjudications malformation (top-level/section/record not an
     object; unreadable JSON): fatal exit 2.
 15. Orphaned adjudication record and orphaned stale cap override (cap no
     longer exceeded): informational, non-blocking.
 16. --advisory suppresses blocking exit 1 but never masks fatal exit 2.
 17. Scope filter: is_proper_name:false / basis:"not_a_name" excluded from
     Cats 1-3.
 18. --init --check together: exactly one stdout JSON line.
 19. Key collision resistance: no naive delimiter-join collision.
 20. Determinism / cwd-independence.
 21. map-key != entry.source_form field: warned, not crashed.

Plus a dedicated schema-validation test for a hand-built
canon_adjudications.json sample against canon-adjudications.schema.json, and
a block of regression tests locking in 5 codex code-review findings (Cat 3
cap-note correctness/non-materialization at scale; a directory at
--canon-path is fatal, not absent; an unwritable --init path fails clean,
never an uncaught traceback; a negative/non-integer --pair-review-cap is
rejected; the adjudications schema's optional-sections and
whitespace-only-string semantics match runtime) -- see the section comment
above those tests for details.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_adjudication_audit.py"
assert SCRIPT_SRC.is_file(), f"canon_adjudication_audit.py not found at {SCRIPT_SRC}"

SUMMARY_SCHEMA_PATH = SCHEMAS_SRC_DIR / "canon-adjudication-audit-summary.schema.json"
ADJUDICATIONS_SCHEMA_PATH = SCHEMAS_SRC_DIR / "canon-adjudications.schema.json"
assert SUMMARY_SCHEMA_PATH.is_file(), (
    f"canon-adjudication-audit-summary.schema.json not found at {SUMMARY_SCHEMA_PATH}"
)
assert ADJUDICATIONS_SCHEMA_PATH.is_file(), (
    f"canon-adjudications.schema.json not found at {ADJUDICATIONS_SCHEMA_PATH}"
)

SUMMARY_SCHEMA = json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))
ADJUDICATIONS_SCHEMA = json.loads(ADJUDICATIONS_SCHEMA_PATH.read_text(encoding="utf-8"))

DEFAULT_GENERATION_HASHES = {
    "particle_config_hash": "test-particle-hash",
    "derivation_bundle_hash": "test-bundle-hash",
}
DEFAULT_TIMESTAMP = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Key computation (test-side) -- see module docstring "Key computation".
# ---------------------------------------------------------------------------


def N(s):
    """Normalize per the frozen key-construction spec (plan §1): NFC ->
    casefold -> collapse-internal-whitespace -> strip. `.split()` with no
    argument already collapses any whitespace run and drops leading/
    trailing whitespace, so re-joining with a single space is equivalent to
    "collapse-internal-whitespace(...).strip()"."""
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


def canonical_json(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_key(kind, identity_struct):
    digest = hashlib.sha256(canonical_json(identity_struct).encode("utf-8")).hexdigest()
    return f"{kind}::{digest}"


def cat1_key(records):
    """records: an iterable of (map_key, raw_source_form, target) triples
    sharing one N(source_form) group."""
    records = list(records)
    ns = N(records[0][1])
    identity = {
        "normalized_source": ns,
        "records": sorted([mk, raw, N(tgt)] for mk, raw, tgt in records),
    }
    return make_key("duplicate_source_form", identity)


def cat2_key(target, source_forms):
    identity = {
        "normalized_target": N(target),
        "source_forms": sorted({N(s) for s in source_forms}),
    }
    return make_key("existing_merge", identity)


def cat3_key(target_a, target_b):
    identity = sorted([N(target_a), N(target_b)])
    return make_key("candidate_missed_merge_pair", identity)


def cat4_key(queued_item):
    return make_key("review_queue_unresolved", queued_item)


def entity_set_fingerprint(targets):
    """sha256(canonical_json(sorted(distinct N(canonical_target_form)))) --
    the cap-override freshness binding (plan §2 "Cap-override freshness" /
    R2-2)."""
    distinct_nts = sorted({N(t) for t in targets})
    return hashlib.sha256(canonical_json(distinct_nts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def entry(source_form, target, is_proper_name=True, basis="transliterated", confidence="high", **extra):
    """A canon-entry.schema.json-shaped ACCEPTED entry (entries{} value).
    Never pass basis="established" without an explicit source=<uri> kwarg --
    the schema only requires/constrains `source` in that case, and every
    fixture in this file deliberately avoids it (see the team brief)."""
    e = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": target,
        "basis": basis,
        "confidence": confidence,
    }
    e.update(extra)
    return e


def queued(source_form, note="queued for review", is_proper_name=True, **extra):
    """A review_queue[] QUEUED item -- canon-batch.schema.json's
    disposition:"review_queue" branch. Only source_form/is_proper_name/
    disposition/note are required; canonical_target_form/basis/source/
    confidence are all optional and unconstrained here."""
    q = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "disposition": "review_queue",
        "note": note,
    }
    q.update(extra)
    return q


def adjudication_record(kind, verdict_class, reviewed_by="test-reviewer", reason="fixture-authored verdict", timestamp=DEFAULT_TIMESTAMP, **extra):
    record = {
        "kind": kind,
        "verdict_class": verdict_class,
        "reviewed_by": reviewed_by,
        "reason": reason,
        "timestamp": timestamp,
    }
    record.update(extra)
    return record


def cap_override_record(entity_count, pair_count, cap, entity_set_fingerprint, risk_accepted_by="test-reviewer", reason="fixture-authored risk acceptance", timestamp=DEFAULT_TIMESTAMP):
    return {
        "risk_accepted_by": risk_accepted_by,
        "reason": reason,
        "timestamp": timestamp,
        "entity_count": entity_count,
        "pair_count": pair_count,
        "cap": cap,
        "entity_set_fingerprint": entity_set_fingerprint,
    }


def risk_override_record(risk_accepted_by="test-reviewer", reason="fixture-authored risk acceptance", timestamp=DEFAULT_TIMESTAMP):
    return {
        "risk_accepted_by": risk_accepted_by,
        "reason": reason,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL
    canon_adjudication_audit.py into {root}/scripts/ (so its self-anchored
    DURABLE_ROOT = Path(__file__).resolve().parents[1] resolves to THIS
    fixture root, exactly matching production invocation -- never assumes
    cwd == durable_root, never takes a --durable-root flag). No canon.json /
    canon_adjudications.json is written here; call write_canon()/
    write_adjudications() (or leave either absent for the absent-file test
    cases)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "canon_adjudication_audit.py")
    return root


def write_canon_raw(root, doc):
    """Writes an arbitrary canon.json-shaped value VERBATIM -- used by
    fixtures that deliberately violate the schema shape (top-level
    malformed / enumeration-critical field missing), where write_canon()'s
    friendly auto-keying would get in the way."""
    (root / "canon.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def write_canon(root, entries, review_queue=None, generation_hashes=None):
    """Friendly canon.json fixture builder. `entries` may be:
      - a list of entry() dicts -- auto-keyed by source_form (a raw-string
        collision gets an index-suffixed key, e.g. "Renaud__1");
      - a dict -- written verbatim as entries{} (map_key -> entry dict), for
        hand-edited fixtures where the map key must deliberately differ from
        the entry's own source_form field.
    """
    if isinstance(entries, list):
        keyed = {}
        for i, e in enumerate(entries):
            key = e["source_form"] if e["source_form"] not in keyed else f"{e['source_form']}__{i}"
            keyed[key] = e
        entries = keyed
    doc = {
        "entries": entries,
        "review_queue": review_queue if review_queue is not None else [],
        "generation_hashes": generation_hashes if generation_hashes is not None else dict(DEFAULT_GENERATION_HASHES),
    }
    return write_canon_raw(root, doc)


def write_adjudications(root, adjudications=None, degenerate_cap_overrides=None, review_queue_risk_overrides=None):
    doc = {
        "schema_version": 1,
        "_contract": (
            "TEST FIXTURE -- hand-authored per canon_adjudication_audit.py's "
            "iron-rule authoring boundary (never written by the script itself)."
        ),
        "adjudications": adjudications if adjudications is not None else {},
        "degenerate_cap_overrides": degenerate_cap_overrides if degenerate_cap_overrides is not None else {},
        "review_queue_risk_overrides": review_queue_risk_overrides if review_queue_risk_overrides is not None else {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def run_audit(root, *args, timeout=30, cwd=None):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def parse_stdout(proc):
    """Asserts EXACTLY ONE JSON line on stdout (the contract's 'exactly one
    JSON line to stdout' rule, finding 8) and parses it."""
    assert proc.stdout.strip(), (
        f"expected exactly one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}"
    return json.loads(lines[0])


def assert_summary_schema_valid(summary):
    jsonschema.validate(instance=summary, schema=SUMMARY_SCHEMA)


def assert_adjudications_schema_valid(doc):
    jsonschema.validate(instance=doc, schema=ADJUDICATIONS_SCHEMA)


def assert_fatal(proc):
    """A genuine fatal: exit 2, NO stdout JSON line (nothing schema-shaped),
    a named stderr error."""
    assert proc.returncode == 2, (
        f"expected fatal exit 2, got rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"a fatal run must never print a summary-shaped stdout line, got:\n{proc.stdout!r}"
    )
    assert proc.stderr.strip() != "", "a fatal run must print a named error to stderr"


# ===========================================================================
# 1 (plan case 1). --init lifecycle.
# ===========================================================================


def test_init_writes_template_when_absent(tmp_path):
    root = make_durable_root(tmp_path)
    adjudications_path = root / "canon_adjudications.json"
    assert not adjudications_path.exists()

    proc = run_audit(root, "--init")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)  # validates against the schema's init-only oneOf branch
    assert summary["success"] is True
    assert summary["mode"] == "init"
    assert summary["created"] is True
    assert summary["existing_adjudications"] == 0
    assert summary["existing_cap_overrides"] == 0
    assert summary["existing_review_queue_risk_overrides"] == 0
    assert adjudications_path.is_file()
    on_disk = json.loads(adjudications_path.read_text(encoding="utf-8"))
    assert_adjudications_schema_valid(on_disk)
    assert on_disk["adjudications"] == {}
    assert on_disk["degenerate_cap_overrides"] == {}
    assert on_disk["review_queue_risk_overrides"] == {}


def test_init_reinit_without_force_leaves_file_untouched(tmp_path):
    root = make_durable_root(tmp_path)
    write_adjudications(
        root, adjudications={"pre-existing::key": adjudication_record("duplicate_source_form", "confirmed_ok")}
    )
    before = (root / "canon_adjudications.json").read_text(encoding="utf-8")

    proc = run_audit(root, "--init")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["created"] is False
    assert summary["existing_adjudications"] == 1
    after = (root / "canon_adjudications.json").read_text(encoding="utf-8")
    assert after == before, "a plain re-init without --force must leave an existing file byte-identical"


def test_init_force_resets_nonempty_file(tmp_path):
    root = make_durable_root(tmp_path)
    write_adjudications(
        root, adjudications={"pre-existing::key": adjudication_record("duplicate_source_form", "confirmed_ok")}
    )

    proc = run_audit(root, "--init", "--force")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["success"] is True
    on_disk = json.loads((root / "canon_adjudications.json").read_text(encoding="utf-8"))
    assert_adjudications_schema_valid(on_disk)
    assert on_disk["adjudications"] == {}
    assert on_disk["degenerate_cap_overrides"] == {}
    assert on_disk["review_queue_risk_overrides"] == {}


# ===========================================================================
# 2 (plan case 2). Neither --init nor --check -> usage error.
# ===========================================================================


def test_no_flags_is_usage_error(tmp_path):
    root = make_durable_root(tmp_path)

    proc = run_audit(root)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "", (
        f"a usage error must never print a summary-shaped line, got:\n{proc.stdout!r}"
    )
    assert proc.stderr.strip() != ""


# ===========================================================================
# 3 (plan case 3). Absent canon.json.
# ===========================================================================


def test_check_absent_canon_reports_not_present_exit_0(tmp_path):
    root = make_durable_root(tmp_path)
    # canon.json deliberately never written.

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["totals"]["required_items"] == 0
    assert summary["blocking_count"] == 0
    assert summary["gate_passed"] is True
    assert proc.stderr.strip() != "", "absent canon must still print a visible stderr NOTE"


# ===========================================================================
# 4 (plan case 4). Top-level malformed canon -> fatal exit 2.
# ===========================================================================


def test_check_malformed_canon_top_level_not_object_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, [])

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_malformed_canon_entries_not_object_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {"entries": [], "review_queue": [], "generation_hashes": DEFAULT_GENERATION_HASHES})

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_malformed_canon_review_queue_not_array_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {"entries": {}, "review_queue": {}, "generation_hashes": DEFAULT_GENERATION_HASHES})

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_malformed_canon_unreadable_json_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    (root / "canon.json").write_text("{not valid json", encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)


# ===========================================================================
# 5 (plan case 5). Cat 1 same-target duplicate + verdict lifecycle.
# ===========================================================================


def test_cat1_same_target_duplicate_and_verdict_lifecycle(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Renaud", "Rainault"),
        entry("renaud", "Rainault"),
    ])

    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["by_kind"]["existing_merge"] == 0, (
        "same N(source_form) -> Cat 1 ONLY, never also a Cat 2 merge (R2-1b disjointness)"
    )
    assert summary["totals"]["missing_verdict"] == 1
    assert summary["blocking_count"] == 1
    assert summary["gate_passed"] is False

    key = cat1_key([("Renaud", "Renaud", "Rainault"), ("renaud", "renaud", "Rainault")])

    write_adjudications(root, adjudications={key: adjudication_record("duplicate_source_form", "confirmed_ok")})
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["blocking_count"] == 0
    assert summary["gate_passed"] is True

    write_adjudications(root, adjudications={key: adjudication_record("duplicate_source_form", "adverse")})
    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["adverse"] == 1
    assert summary["blocking_count"] == 1
    assert summary["gate_passed"] is False


# ===========================================================================
# 6 (plan case 6). Cat 1 record-count rule: two map keys, identical
#    source_form FIELD, different targets -- still one Cat 1 item, and the
#    two resulting entities are excluded from Cat 3 (they share N(source)).
# ===========================================================================


def test_cat1_record_count_rule_two_map_keys_same_field(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, {
        "k1": entry("Renaud", "TargetA"),
        "k2": entry("Renaud", "TargetB"),
    })

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0, (
        "TargetA/TargetB share N(source_form)='renaud' -> excluded from Cat 3 (R2-1c)"
    )
    assert summary["totals"]["required_items"] == 1
    assert summary["blocking_count"] == 1

    key = cat1_key([("k1", "Renaud", "TargetA"), ("k2", "Renaud", "TargetB")])
    write_adjudications(root, adjudications={key: adjudication_record("duplicate_source_form", "confirmed_ok")})
    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ===========================================================================
# 7 (plan case 7). Cat 2 existing_merge + verdict lifecycle.
# ===========================================================================


def test_cat2_existing_merge_and_verdict_lifecycle(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Jean", "Zhan"),
        entry("Valjean", "Zhan"),
    ])

    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["existing_merge"] == 1
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 0
    assert summary["blocking_count"] == 1

    key = cat2_key("Zhan", ["Jean", "Valjean"])
    write_adjudications(root, adjudications={key: adjudication_record("existing_merge", "confirmed_ok")})
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["gate_passed"] is True


# ===========================================================================
# 8 (plan case 8). Cat 3 all-pairs under cap + verdict lifecycle.
# ===========================================================================


def test_cat3_all_pairs_under_cap(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 3, (
        "3 entities, no similarity filter -> all C(3,2)=3 pairs, including "
        "unrelated/non-confusable pairs (regression for finding 1)"
    )
    assert summary["totals"]["cap_notes"] == 0
    assert summary["blocking_count"] == 3

    key = cat3_key("TargetOne", "TargetTwo")
    write_adjudications(root, adjudications={key: adjudication_record("candidate_missed_merge_pair", "confirmed_ok")})
    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # 2 pairs still unresolved
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["blocking_count"] == 2


# ===========================================================================
# 9 (plan case 9). Cat 3 disjointness -- a shared-N(source_form) pair is
#    excluded from Cat 3, appearing only as a Cat 1 duplicate.
# ===========================================================================


def test_cat3_excludes_pair_sharing_normalized_source_form(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Renaud", "TargetA"),
        entry("renaud", "TargetB"),  # same N(source_form), different target
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0
    assert summary["blocking_count"] == 1


# ===========================================================================
# 10 (plan case 10). Cat 3 cap early-stop: cap-note + zero per-pair items,
#     override lifecycle, stale-fingerprint re-blocking.
# ===========================================================================


def test_cat3_cap_early_stop_and_override_lifecycle(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0, (
        "over cap -> a single cap-note replaces per-pair items, never both"
    )
    assert summary["totals"]["cap_notes"] == 1
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["blocking_count"] == 1

    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo", "TargetThree"])

    # A valid, FRESH override satisfies the cap-note.
    write_adjudications(root, degenerate_cap_overrides={
        "__canon__": cap_override_record(entity_count=3, pair_count=3, cap=1, entity_set_fingerprint=fingerprint)
    })
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_ok"] == 1
    assert summary["totals"]["cap_overrides_missing"] == 0
    assert summary["gate_passed"] is True

    # An override missing 'reason' does not satisfy -> stays blocking.
    bad_override = cap_override_record(entity_count=3, pair_count=3, cap=1, entity_set_fingerprint=fingerprint)
    del bad_override["reason"]
    write_adjudications(root, degenerate_cap_overrides={"__canon__": bad_override})
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["blocking_count"] == 1

    # A STALE entity_set_fingerprint (entity composition changed -- same
    # entity_count/pair_count/cap, so ONLY the fingerprint diverges) ->
    # blocking (R2-2 freshness). NOTE: this is "stale" (a cap-note still
    # exists and the override just doesn't match it), NOT "orphaned" --
    # orphaned_records only counts records that no longer correspond to ANY
    # currently-required item at all (see the dedicated orphaned-cap-override
    # test below, where raising the cap makes the cap-note vanish entirely).
    write_adjudications(root, degenerate_cap_overrides={
        "__canon__": cap_override_record(entity_count=3, pair_count=3, cap=1, entity_set_fingerprint=fingerprint)
    })
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetFour"),  # renamed from TargetThree
    ])
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["blocking_count"] == 1
    assert summary["totals"]["orphaned_records"] == 0


# ===========================================================================
# 11 (plan case 11). Cat 4 review_queue lifecycle.
# ===========================================================================


def test_cat4_review_queue_unresolved_lifecycle(tmp_path):
    root = make_durable_root(tmp_path)
    item = queued("Mystery Name")
    write_canon(root, [], review_queue=[item])

    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["review_queue_unresolved"] == 1
    assert summary["totals"]["review_queue_items"] == 1
    assert summary["totals"]["review_queue_unaccepted"] == 1
    assert summary["blocking_count"] == 1

    key = cat4_key(item)

    # A risk-override clears it.
    write_adjudications(root, review_queue_risk_overrides={key: risk_override_record()})
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["review_queue_unaccepted"] == 0
    assert summary["gate_passed"] is True

    # Draining the item (removing it from review_queue) also clears it.
    write_adjudications(root)
    write_canon(root, [], review_queue=[])
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["review_queue_items"] == 0
    assert summary["totals"]["review_queue_unaccepted"] == 0

    # A content change re-blocks (new key -- old override no longer matches).
    changed_item = queued("Mystery Name", note="updated note text")
    write_canon(root, [], review_queue=[changed_item])
    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["review_queue_unaccepted"] == 1

    # is_proper_name:false is still counted.
    write_canon(root, [], review_queue=[queued("Not A Name Thing", is_proper_name=False)])
    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["review_queue_unresolved"] == 1

    # A queued item missing only 'note' is still enumerated (blocking), NOT
    # fatal -- note is display-only here (R2-3).
    incomplete_item = {"source_form": "No Note Here", "is_proper_name": True, "disposition": "review_queue"}
    write_canon(root, [], review_queue=[incomplete_item])
    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["review_queue_unresolved"] == 1


# ===========================================================================
# 12 (plan case 12). Enumeration-critical row malformation -> fatal exit 2.
# ===========================================================================


def test_check_entry_missing_canonical_target_form_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {
        "entries": {"k1": {"source_form": "X", "is_proper_name": True, "basis": "transliterated", "confidence": "high"}},
        "review_queue": [],
        "generation_hashes": DEFAULT_GENERATION_HASHES,
    })

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_entry_missing_is_proper_name_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {
        "entries": {"k1": {"source_form": "X", "canonical_target_form": "Y", "basis": "transliterated", "confidence": "high"}},
        "review_queue": [],
        "generation_hashes": DEFAULT_GENERATION_HASHES,
    })

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_entry_missing_basis_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {
        "entries": {"k1": {"source_form": "X", "canonical_target_form": "Y", "is_proper_name": True, "confidence": "high"}},
        "review_queue": [],
        "generation_hashes": DEFAULT_GENERATION_HASHES,
    })

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_queued_missing_source_form_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon_raw(root, {
        "entries": {},
        "review_queue": [{"is_proper_name": True, "disposition": "review_queue", "note": "x"}],
        "generation_hashes": DEFAULT_GENERATION_HASHES,
    })

    proc = run_audit(root, "--check")

    assert_fatal(proc)


# ===========================================================================
# 13 (plan case 13). Invalid verdict_class (blocking, not fatal); empty
#     reviewed_by (does not satisfy).
# ===========================================================================


def test_invalid_verdict_class_is_blocking_not_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    key = cat2_key("Zhan", ["Jean", "Valjean"])

    write_adjudications(root, adjudications={key: adjudication_record("existing_merge", "maybe")})
    proc = run_audit(root, "--check")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["invalid_verdict_class"] == 1
    assert summary["blocking_count"] == 1
    assert summary["gate_passed"] is False


def test_empty_reviewed_by_does_not_satisfy(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    key = cat2_key("Zhan", ["Jean", "Valjean"])

    write_adjudications(
        root, adjudications={key: adjudication_record("existing_merge", "confirmed_ok", reviewed_by="")}
    )
    proc = run_audit(root, "--check")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["blocking_count"] == 1
    assert summary["gate_passed"] is False


# ===========================================================================
# 14 (plan case 14). Structural adjudications malformation -> fatal exit 2.
# ===========================================================================


def test_check_adjudications_top_level_not_object_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_text(json.dumps([]), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_adjudications_section_not_object_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_text(
        json.dumps({
            "schema_version": 1, "adjudications": [],
            "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
        }),
        encoding="utf-8",
    )

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_adjudications_record_not_object_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_text(
        json.dumps({
            "schema_version": 1, "adjudications": {"x::y": "not-an-object"},
            "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
        }),
        encoding="utf-8",
    )

    proc = run_audit(root, "--check")

    assert_fatal(proc)


def test_check_adjudications_unreadable_json_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_text("{not valid json", encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)


# ===========================================================================
# 15 (plan case 15). Orphaned records -> informational, non-blocking.
# ===========================================================================


def test_orphaned_adjudication_record_is_informational_non_blocking(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [])  # no required items at all

    write_adjudications(root, adjudications={
        "duplicate_source_form::" + "0" * 64: adjudication_record("duplicate_source_form", "confirmed_ok"),
    })

    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["orphaned_records"] >= 1
    assert summary["blocking_count"] == 0
    assert summary["gate_passed"] is True


def test_orphaned_stale_cap_override_when_cap_no_longer_exceeded_is_non_blocking(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo", "TargetThree"])
    write_adjudications(root, degenerate_cap_overrides={
        "__canon__": cap_override_record(entity_count=3, pair_count=3, cap=1, entity_set_fingerprint=fingerprint)
    })
    # Confirm the override currently satisfies the cap-note at cap=1.
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Drain down to a single entity -- zero possible pairs, so no cap-note
    # is required any more regardless of cap (raising the cap alone would
    # only swap the ONE cap-note for 3 unresolved per-pair items -- still
    # blocking; eliminating the pairs entirely is what actually orphans the
    # override). The stale override now corresponds to nothing current, so
    # it is informational/prunable only, and nothing else is blocking.
    write_canon(root, [entry("Alpha", "TargetOne")])
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_notes"] == 0
    assert summary["totals"]["required_items"] == 0
    assert summary["totals"]["orphaned_records"] >= 1
    assert summary["gate_passed"] is True


# ===========================================================================
# 16 (plan case 16). --advisory suppresses blocking, never masks fatal.
# ===========================================================================


def test_advisory_suppresses_blocking_but_not_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])  # missing verdict -> blocking

    proc = run_audit(root, "--check", "--advisory")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["advisory"] is True
    assert summary["gate_passed"] is False
    assert summary["blocking_count"] > 0

    # --advisory must NOT mask a top-level-malformed fatal.
    write_canon_raw(root, {"entries": "not-an-object", "review_queue": [], "generation_hashes": DEFAULT_GENERATION_HASHES})
    proc = run_audit(root, "--check", "--advisory")
    assert_fatal(proc)

    # --advisory must NOT mask an enumeration-critical-row fatal.
    write_canon_raw(root, {
        "entries": {"k1": {"source_form": "X", "is_proper_name": True, "basis": "transliterated", "confidence": "high"}},
        "review_queue": [],
        "generation_hashes": DEFAULT_GENERATION_HASHES,
    })
    proc = run_audit(root, "--check", "--advisory")
    assert_fatal(proc)


# ===========================================================================
# 17 (plan case 17). Scope filter excludes non-proper-names from Cats 1-3.
# ===========================================================================


def test_scope_filter_excludes_non_proper_names_from_cats_1_to_3(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Renaud", "TargetA", is_proper_name=False),  # excluded -- would dup with the entry below
        entry("renaud", "TargetA"),  # sole surviving proper-name record for TargetA
        entry("Riviere", "TargetB", basis="not_a_name"),  # excluded -- would merge with the entry below
        entry("Fleuve", "TargetB"),  # sole surviving proper-name record for TargetB
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # the one LEGITIMATE Cat 3 pair below
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 0, (
        "excluded is_proper_name:false 'Renaud' must not pair with 'renaud' into a Cat 1 duplicate"
    )
    assert summary["totals"]["by_kind"]["existing_merge"] == 0, (
        "excluded basis:'not_a_name' 'Riviere' must not pair with 'Fleuve' into a Cat 2 merge"
    )
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 1, (
        "the two SURVIVING proper-name entities ('renaud'->TargetA, 'Fleuve'->TargetB) "
        "still legitimately form one Cat 3 pair"
    )
    assert summary["totals"]["required_items"] == 1


# ===========================================================================
# 17b (#138 TP-14). basis:"sense_translated" scope-filter participation.
#
# CHARACTERIZATION -- red-before-green is STRUCTURALLY IMPOSSIBLE for both
# tests below. The scope filter (see this module's own docstring, "Scope",
# and `_proper_name_records` at scripts/canon_adjudication_audit.py:533) is
# a DENYLIST -- `is_proper_name is True and basis != "not_a_name"` -- with
# zero hardcoded enum literals besides "not_a_name" itself. A
# basis:"sense_translated" entry was therefore ALREADY included in Cats 1-3
# before #138 touched a single schema/prompt surface (there is no code path
# that could ever have excluded it), and D11's is_proper_name:true
# requirement was already enforced by this SAME pre-existing filter, not by
# anything #138 adds here. These two tests lock both halves of D11 for the
# new basis value specifically.
# ===========================================================================


def test_sense_translated_included_in_cat1_scope(tmp_path):
    """D11 inclusion half: two basis:"sense_translated" proper-name records
    sharing N(source_form) must still form a Cat 1 duplicate -- the
    denylist admits any basis other than "not_a_name"."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Loup", "Wolf", basis="sense_translated", note="sense-rendering of a speaking name"),
        entry("loup", "Wolf", basis="sense_translated", note="sense-rendering of a speaking name"),
    ])

    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1, (
        "two sense_translated proper-name records sharing N(source_form) must "
        "still form a Cat 1 duplicate -- the denylist admits any basis != 'not_a_name'"
    )
    assert summary["blocking_count"] == 1


def test_sense_translated_excluded_from_scope_when_is_proper_name_false(tmp_path):
    """D11 negative half: is_proper_name:false EXCLUDES a
    basis:"sense_translated" entry from Cats 1-3, mirroring
    test_scope_filter_excludes_non_proper_names_from_cats_1_to_3 above for
    other bases -- the excluded record must not pair with its
    is_proper_name:true twin into a Cat 1 duplicate."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Loup", "Wolf", basis="sense_translated", is_proper_name=False, note="x"),
        entry("loup", "Wolf", basis="sense_translated", note="x"),  # sole surviving proper-name record
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 0, proc.stdout + proc.stderr  # only 1 surviving proper-name record -> nothing to dup
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 0, (
        "excluded is_proper_name:false sense_translated record must not pair "
        "into a Cat 1 duplicate with its is_proper_name:true twin"
    )
    assert summary["totals"]["required_items"] == 0


# ===========================================================================
# 18 (plan case 18). --init --check together -> exactly one stdout line.
# ===========================================================================


def test_init_and_check_together_prints_exactly_one_line(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [])

    proc = run_audit(root, "--init", "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)  # asserts exactly one JSON line internally
    assert_summary_schema_valid(summary)
    assert summary["mode"] == "init+check"
    assert (root / "canon_adjudications.json").is_file()


# ===========================================================================
# 19 (plan case 19). Key collision resistance.
# ===========================================================================


def test_key_collision_resistance_special_characters(tmp_path):
    """Regression test for R2-5/finding-5: keys are built from a genuine
    JSON canonicalization (json.dumps(..., separators=(",", ":"))), never a
    naive delimiter-join of raw strings -- so two DISTINCT identity structs
    whose naive '::'-joined representation WOULD collide (['A', 'B::C'] and
    ['A::B', 'C'] both naively join to 'A::B::C') must still produce
    distinct, non-colliding keys."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("SrcA1", "A"),
        entry("SrcA2", "B::C"),
        entry("SrcB1", "A::B"),
        entry("SrcB2", "C"),
    ])

    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 6  # C(4,2), no shared sources

    key_pair1 = cat3_key("A", "B::C")
    key_pair2 = cat3_key("A::B", "C")
    assert key_pair1 != key_pair2, "naive delimiter-join collision -- keys must differ"

    write_adjudications(root, adjudications={
        key_pair1: adjudication_record("candidate_missed_merge_pair", "confirmed_ok"),
    })
    proc = run_audit(root, "--check", "--pair-review-cap", "10")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["confirmed_ok"] == 1, (
        "confirming pair1's key must satisfy ONLY pair1, never accidentally "
        "also satisfy pair2 via a delimiter-join collision"
    )
    assert summary["blocking_count"] == 5


# ===========================================================================
# 20 (plan case 20). Determinism / self-anchoring / cwd-independence.
# ===========================================================================


def test_determinism_and_cwd_independence(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])

    proc1 = run_audit(root, "--check")
    proc2 = run_audit(root, "--check")
    assert proc1.returncode == 1 and proc2.returncode == 1
    summary1 = parse_stdout(proc1)
    summary2 = parse_stdout(proc2)
    assert_summary_schema_valid(summary1)
    assert_summary_schema_valid(summary2)
    assert summary1["totals"] == summary2["totals"]

    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    proc3 = run_audit(root, "--check", cwd=str(other_cwd))
    assert proc3.returncode == 1, proc3.stdout + proc3.stderr
    summary3 = parse_stdout(proc3)
    assert summary3["totals"] == summary1["totals"], "cwd must never affect a self-anchored script's result"


# ===========================================================================
# 21 (plan case 21). map-key != entry.source_form field -> warned, not
#     crashed.
# ===========================================================================


def test_map_key_mismatch_with_source_form_field_is_warned_not_crashed(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, {
        "some_other_key": entry("Jean", "Zhan"),
    })

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert isinstance(summary["warnings"], list)
    assert len(summary["warnings"]) >= 1, "a map-key/source_form mismatch must surface as a warning, not a crash"
    assert proc.stderr.strip() != ""


# ===========================================================================
# 22. Regression tests for codex code-review findings (post-1.0.0
#     hardening). Builder is fixing the script + canon-adjudications.schema
#     .json for these in parallel -- each test locks in the CORRECT,
#     post-fix behavior, so a test may legitimately fail until that fix
#     lands (see the lead's report for which).
# ===========================================================================


def _pairwise_distinct_entities(n, source_prefix="Source", target_prefix="Target"):
    """n proper-name entries, pairwise-distinct source forms AND targets --
    every unordered pair among them is a genuine Cat 3 candidate (no
    shared-N(source_form) exclusions), so pair_count == C(n, 2) exactly."""
    return [entry(f"{source_prefix}{i:03d}", f"{target_prefix}{i:03d}") for i in range(n)]


def _cap_note_warning(summary):
    """The cap-note's own warnings[] entry (module docstring "CAP-OVERRIDE
    FRESHNESS": copy-pasteable) -- shared by the Cat 3 cap-note tests below
    that assert on its exact numeric fields."""
    return next(w for w in summary["warnings"] if w.startswith("CAP-NOTE"))


def test_cat3_cap_note_at_moderate_scale_no_per_pair_materialization(tmp_path):
    """Codex finding: over a moderate-but-realistic entity count (30 ->
    C(30,2)=435 pairs, well over the default cap of 40), the script must
    emit exactly ONE cap-note with ZERO per-pair items -- never materialize
    (hash + build a display dict for) all 435 individual pair items only to
    discard them -- and must complete comfortably inside a generous
    timeout (a true per-pair materialization bug would still be fast at
    n=30, but this locks in the mutual-exclusion contract at a scale
    bigger than the other Cat 3 tests' n=3 fixtures)."""
    root = make_durable_root(tmp_path)
    write_canon(root, _pairwise_distinct_entities(30))

    proc = run_audit(root, "--check", timeout=15)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_notes"] == 1
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["blocking_count"] >= 1

    # The cap-note's exact pair_count is folded into warnings[] verbatim
    # (module docstring "CAP-OVERRIDE FRESHNESS": "copy-pasteable") -- assert
    # the correct combinatorial count actually made it through, not just
    # that SOME cap-note fired.
    assert '"pair_count":435' in _cap_note_warning(summary)


def test_cat3_cap_pair_count_excludes_shared_source_pairs(tmp_path):
    """Codex finding, exclusion-arithmetic sub-case: 30 entities where
    exactly 2 share a normalized source form (so their pair is EXCLUDED as
    category 1's territory, per R2-1c) -- the cap-note's pair_count must be
    C(30,2) - 1, not the raw C(30,2), proving the exclusion actually runs
    at this scale rather than being silently skipped for the sake of a
    cheap `C(n,2) > cap` shortcut."""
    root = make_durable_root(tmp_path)
    entities = _pairwise_distinct_entities(30)
    # Give entity 1 the SAME source_form as entity 0 (case-identical is fine
    # -- N() would collapse a case difference too) while keeping its own
    # distinct target -- this excludes exactly ONE pair (entity 0, entity 1)
    # from Cat 3, and also makes them a Cat 1 duplicate.
    entities[1] = entry("Source000", entities[1]["canonical_target_form"])
    write_canon(root, entities)

    proc = run_audit(root, "--check", timeout=15)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["cap_notes"] == 1

    expected_pair_count = (30 * 29 // 2) - 1
    assert f'"pair_count":{expected_pair_count}' in _cap_note_warning(summary)


def test_check_canon_path_pointing_at_a_directory_is_fatal(tmp_path):
    """Codex finding: --canon-path pointing at an existing DIRECTORY (not a
    missing path) must be a genuine FATAL -- never silently folded into
    'canon absent' (canon_present:false is reserved for a path that
    genuinely does not exist; a directory sitting at that path is a real
    misconfiguration canon_validate.py itself would never produce, and
    must not false-green as 0-required-items/exit-0)."""
    root = make_durable_root(tmp_path)

    proc = run_audit(root, "--check", "--canon-path", str(root))  # root itself is a directory

    assert_fatal(proc)


def test_init_to_unwritable_path_is_fatal_no_traceback(tmp_path):
    """Codex finding: a filesystem failure while writing the --init
    template (an unwritable/nonsensical parent path) must surface as a
    clean, named FATAL -- never an uncaught Python traceback (which would
    violate the contract's 'no stdout can be mistaken for a summary' AND
    'a named stderr error' promises simultaneously)."""
    root = make_durable_root(tmp_path)

    proc = run_audit(root, "--init", "--adjudications-path", "/dev/null/canon_adjudications.json")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""
    assert "Traceback" not in proc.stderr, (
        f"a filesystem write failure must be a clean named FATAL, not an uncaught "
        f"traceback:\n{proc.stderr}"
    )
    assert proc.stderr.strip() != ""


def test_negative_pair_review_cap_is_rejected(tmp_path):
    """Codex finding: a negative --pair-review-cap is nonsensical (a count
    cap cannot be negative) and must be rejected as a usage error before
    any computation runs -- exit 2, no stdout JSON."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])

    proc = run_audit(root, "--check", "--pair-review-cap", "-1")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""


def test_non_integer_pair_review_cap_is_rejected(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])

    proc = run_audit(root, "--check", "--pair-review-cap", "abc")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout.strip() == ""


def test_adjudications_missing_override_sections_is_schema_valid_and_accepted_at_runtime(tmp_path):
    """Codex finding: a hand-authored adjudications doc carrying only
    schema_version + adjudications (omitting degenerate_cap_overrides/
    review_queue_risk_overrides entirely) must be schema-VALID -- runtime
    already defaults absent sections to {} (read_adjudications's own
    docstring: 'Absent sections default to empty -- a not-yet-created
    override section is not itself malformed') -- and must NOT be fatal
    when the script actually reads it."""
    minimal_doc = {"schema_version": 1, "adjudications": {}}
    assert_adjudications_schema_valid(minimal_doc)

    root = make_durable_root(tmp_path)
    write_canon(root, [])
    (root / "canon_adjudications.json").write_text(
        json.dumps(minimal_doc, ensure_ascii=False), encoding="utf-8"
    )

    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)


def test_adjudications_whitespace_only_reviewed_by_is_schema_invalid():
    """Codex finding: the schema's reviewed_by/reason/risk_accepted_by
    constraints must match runtime's _nonempty_str() semantics (a
    .strip()-based non-blank check), not a bare minLength:1 -- a
    whitespace-only string satisfies minLength:1 but is blank, and runtime
    already treats it as not satisfying the gate (see
    _nonempty_str/crosscheck_regular_items)."""
    bad_doc = {
        "schema_version": 1,
        "adjudications": {
            "duplicate_source_form::" + "a" * 64: adjudication_record(
                "duplicate_source_form", "confirmed_ok", reviewed_by="   "
            ),
        },
        "degenerate_cap_overrides": {},
        "review_queue_risk_overrides": {},
    }
    with pytest.raises(jsonschema.ValidationError):
        assert_adjudications_schema_valid(bad_doc)


# ===========================================================================
# 23. Regression tests for codex RE-review findings (round 2 hardening).
#     Builder is fixing the script + canon-adjudications.schema.json for
#     these in parallel -- each test locks in the CORRECT, post-fix
#     behavior, so a test may legitimately fail until that fix lands (see
#     the lead's report for which).
# ===========================================================================


def _shared_source_entities(n, source_form="SharedSource", target_prefix="Target"):
    """n proper-name entries that ALL carry the SAME source_form (so every
    possible pair among their distinct targets shares that one normalized
    source form -- a degenerate, maximally-cliqued Cat 1/Cat 3 input) with
    pairwise-distinct targets."""
    return [entry(source_form, f"{target_prefix}{i:05d}") for i in range(n)]


def test_cat3_degenerate_scale_budget_guard_reports_upper_bound_pair_count(tmp_path):
    """Codex finding: when a single shared-normalized-source clique's
    excluded-pair count would itself exceed EXCLUSION_MATERIALIZATION_BUDGET
    (1_000_000) -- here, ~1500 entities ALL carrying the SAME source_form,
    so C(1500,2)~=1.12M > budget -- the script must NEVER materialize that
    many frozenset pairs (a real risk of a multi-second-to-minutes hang /
    large memory spike at genuinely degenerate scale). Instead it falls
    back to reporting the DEGENERATE upper-bound pair_count =
    C(entity_count,2) (skipping the exclusion subtraction, which would
    itself require full materialization) -- a safe over-estimate that still
    correctly triggers the cap-note path, never a false-green."""
    n = 1500
    root = make_durable_root(tmp_path)
    write_canon(root, _shared_source_entities(n))

    proc = run_audit(root, "--check", timeout=30)
    assert proc.returncode == 1, proc.stdout + proc.stderr  # blocking: huge Cat 1 item + cap-note, both unresolved
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)

    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1, (
        "sanity check on the fixture: all n entries sharing one source_form "
        "must collapse into exactly ONE Cat 1 duplicate-source-form item"
    )
    assert summary["totals"]["cap_notes"] == 1
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0
    assert summary["totals"]["cap_overrides_missing"] == 1

    total_pairs = n * (n - 1) // 2
    assert total_pairs > 1_000_000  # sanity-check the fixture actually exceeds the budget
    cap_note_warning = _cap_note_warning(summary)
    assert f'"pair_count":{total_pairs}' in cap_note_warning, (
        f"over-budget must report the DEGENERATE upper bound (total_pairs="
        f"{total_pairs}, skipping the too-expensive exact exclusion "
        f"subtraction), not attempt full materialization:\n{cap_note_warning}"
    )


def test_cat3_small_scale_shared_source_clique_still_exact_under_budget(tmp_path):
    """Guards against the budget guard (see the degenerate-scale test above)
    over-triggering on ordinary small/moderate canons: 30 entities that ALL
    share ONE normalized source form is comfortably under
    EXCLUSION_MATERIALIZATION_BUDGET (C(30,2)=435 clique edges) -- the exact
    exclusion-subtraction path must still run, correctly finding that EVERY
    one of the 435 possible pairs is excluded (they all share the one
    common source form -- Cat 1's territory), leaving pair_count == 0 and
    therefore NO cap-note and NO per-pair items at all -- never the
    degenerate upper-bound fallback, which would wrongly demand a
    --pair-review-cap risk-acceptance for something Cat 1 already covers."""
    n = 30
    root = make_durable_root(tmp_path)
    write_canon(root, _shared_source_entities(n))

    proc = run_audit(root, "--check")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # the one Cat 1 item is still missing_verdict
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["by_kind"]["candidate_missed_merge_pair"] == 0
    assert summary["totals"]["cap_notes"] == 0
    assert summary["totals"]["required_items"] == 1


def test_adjudications_records_without_timestamp_are_schema_valid_and_accepted_at_runtime(tmp_path):
    """Codex finding: 'timestamp' is no longer a required field on any of
    the three record shapes (adjudications{}/degenerate_cap_overrides{}/
    review_queue_risk_overrides{}) -- a hand-authored record omitting it
    entirely must be schema-VALID, and the script must still accept it at
    runtime (the verdict/override still clears its item -- the runtime-side
    checks (_nonempty_str/_risk_accepted/cap-identity match) never read
    timestamp at all, so runtime acceptance holds even before the schema
    fix lands; only the schema-validity half of this test exercises the
    actual finding). Exercises all three record shapes at once: a Cat 2
    existing_merge adjudication, a Cat 3 degenerate_cap_override, and a
    Cat 4 review_queue_risk_override."""
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        [
            entry("Jean", "Zhan"),
            entry("Valjean", "Zhan"),
            entry("Alpha", "TargetOne"),
            entry("Beta", "TargetTwo"),
            entry("Gamma", "TargetThree"),
        ],
        review_queue=[queued("Mystery Name")],
    )

    cat2_k = cat2_key("Zhan", ["Jean", "Valjean"])
    fingerprint = entity_set_fingerprint(["Zhan", "TargetOne", "TargetTwo", "TargetThree"])
    queue_item = queued("Mystery Name")
    queue_k = cat4_key(queue_item)

    doc = {
        "schema_version": 1,
        "adjudications": {
            cat2_k: {
                "kind": "existing_merge", "verdict_class": "confirmed_ok",
                "reviewed_by": "test-reviewer", "reason": "fixture-authored verdict",
                # no timestamp
            },
        },
        "degenerate_cap_overrides": {
            "__canon__": {
                "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
                "entity_count": 4, "pair_count": 6, "cap": 1, "entity_set_fingerprint": fingerprint,
                # no timestamp
            },
        },
        "review_queue_risk_overrides": {
            queue_k: {
                "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
                # no timestamp
            },
        },
    }
    assert_adjudications_schema_valid(doc)
    (root / "canon_adjudications.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True
    assert summary["blocking_count"] == 0
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["totals"]["cap_overrides_ok"] == 1
    assert summary["totals"]["review_queue_unaccepted"] == 0

    # Backward-compat: a record WITH timestamp is still schema-valid too --
    # already covered by test_sample_adjudications_file_validates_against_schema
    # below (every record there carries a timestamp via the factory helpers'
    # defaults), so not re-asserted here to avoid duplicating that test.


def test_check_adjudications_path_pointing_at_a_directory_is_fatal(tmp_path):
    """Codex finding: --adjudications-path pointing at an existing
    DIRECTORY must be a genuine FATAL, mirroring read_canon's own
    not-a-regular-file handling -- never silently folded into 'treat as
    empty/not-yet-initialized' (a genuinely ABSENT adjudications file is a
    legitimate, non-fatal case -- 'run --init first' -- but a directory
    sitting at that path is a real misconfiguration, not a not-yet-
    initialized project)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    a_directory = root / "adjudications_is_actually_a_dir"
    a_directory.mkdir()

    proc = run_audit(root, "--check", "--adjudications-path", str(a_directory))

    assert_fatal(proc)


def _make_dangling_symlink(root, name):
    """A symlink at root/name whose target does not exist -- shared by the
    two dangling-symlink fatal tests below (--canon-path and
    --adjudications-path). Skips the test outright when the sandbox doesn't
    support symlink creation."""
    dangling = root / name
    try:
        os.symlink(str(root / "does_not_exist.json"), str(dangling))
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported in this sandbox")
    return dangling


def test_check_canon_path_dangling_symlink_is_fatal(tmp_path):
    """Codex finding: a dangling symlink at --canon-path (the symlink
    exists but its target does not) must be a genuine FATAL --
    Path.exists() follows symlinks and returns False for a dangling one, so
    a naive 'not path.exists() -> absent' check would silently misreport a
    broken symlink as 'canon not present' instead of the real
    misconfiguration it is."""
    root = make_durable_root(tmp_path)
    dangling = _make_dangling_symlink(root, "canon_dangling_symlink.json")

    proc = run_audit(root, "--check", "--canon-path", str(dangling))

    assert_fatal(proc)


def test_check_adjudications_path_dangling_symlink_is_fatal(tmp_path):
    """Codex finding: same dangling-symlink fatal as the canon-path test
    above, but for --adjudications-path (read_adjudications's own
    not-a-regular-file check must apply here too)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    dangling = _make_dangling_symlink(root, "adjudications_dangling_symlink.json")

    proc = run_audit(root, "--check", "--adjudications-path", str(dangling))

    assert_fatal(proc)


# ===========================================================================
# 24. Regression tests for codex round-3 findings (--init's OWN presence
#     check, adjacent to round-2's read_canon/read_adjudications fix).
#     Builder is fixing do_init in parallel -- each test locks in the
#     CORRECT, post-fix behavior, so a test may legitimately fail until
#     that fix lands (see the lead's report for which).
# ===========================================================================


def test_init_without_force_against_dangling_symlink_is_fatal_and_does_not_clobber(tmp_path):
    """Codex round-3 finding: --init (no --force) against a DANGLING
    symlink at the adjudications path must be a genuine FATAL -- the old
    path.is_file()-only presence check treated a broken symlink as 'absent'
    and happily clobbered it with a fresh template, silently destroying
    whatever the symlink was SUPPOSED to point at (a real, if temporarily
    unreachable, adjudications file) without the explicit --force the
    contract requires for any destructive reset. do_init now uses
    os.path.lexists (mirroring read_canon/read_adjudications), so a dangling
    symlink counts as 'existing' -- routing into the not-force branch,
    which then fatals cleanly via read_adjudications's own
    not-a-regular-file check."""
    root = make_durable_root(tmp_path)
    dangling = _make_dangling_symlink(root, "canon_adjudications.json")  # the DEFAULT path

    proc = run_audit(root, "--init")

    assert_fatal(proc)
    # The broken symlink must survive untouched -- no template was written.
    assert dangling.is_symlink(), "a dangling symlink must never be silently clobbered without --force"
    assert not dangling.exists(), "the symlink's target must still be unreachable (unchanged)"


def test_init_with_force_against_dangling_symlink_succeeds(tmp_path):
    """--init --force against the SAME dangling symlink must succeed,
    replacing the broken symlink with a fresh, regular-file empty
    template -- --force is exactly the explicit destructive-reset escape
    hatch the contract documents. os.replace() atomically swaps out
    whatever sits at the destination name (symlink or not), so the broken
    link itself is gone afterward, not just its target."""
    root = make_durable_root(tmp_path)
    dangling = _make_dangling_symlink(root, "canon_adjudications.json")

    proc = run_audit(root, "--init", "--force")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert summary["success"] is True
    assert summary["mode"] == "init"
    assert summary["created"] is True
    assert not dangling.is_symlink(), "--force must replace the symlink with a real file"
    assert dangling.is_file()
    on_disk = json.loads(dangling.read_text(encoding="utf-8"))
    assert_adjudications_schema_valid(on_disk)
    assert on_disk["adjudications"] == {}
    assert on_disk["degenerate_cap_overrides"] == {}
    assert on_disk["review_queue_risk_overrides"] == {}


def test_init_without_force_against_a_directory_is_fatal(tmp_path):
    """Codex round-3 finding, directory sub-case: --init (no --force)
    against a DIRECTORY sitting at the adjudications path must likewise be
    fatal, not silently treated as absent-and-clobberable."""
    root = make_durable_root(tmp_path)
    a_directory = root / "canon_adjudications.json"  # the DEFAULT path
    a_directory.mkdir()

    proc = run_audit(root, "--init")

    assert_fatal(proc)
    assert a_directory.is_dir(), "a directory at the path must never be silently clobbered without --force"


# ===========================================================================
# 25. Regression tests for codex round-4 findings. Builder is fixing the
#     script in parallel -- each test locks in the CORRECT, post-fix
#     behavior, so a test may legitimately fail until that fix lands (see
#     the lead's report for which).
# ===========================================================================


def test_check_canon_json_invalid_utf8_bytes_is_fatal_no_traceback(tmp_path):
    """Codex round-4 finding A: canon.json containing raw bytes that are NOT
    valid UTF-8 (undecodable BYTES, not merely malformed-but-decodable JSON
    text -- distinct from the existing invalid-JSON tests, which write a
    decodable string) must be a clean, named FATAL -- never an uncaught
    UnicodeDecodeError traceback. _read_json_file's `path.read_text(
    encoding="utf-8")` call only catches OSError today; UnicodeDecodeError
    is a ValueError subclass and slips right past it."""
    root = make_durable_root(tmp_path)
    (root / "canon.json").write_bytes(b"\xff\xfe\x00bad")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr, (
        f"invalid UTF-8 bytes must surface as a clean named FATAL, not an "
        f"uncaught traceback:\n{proc.stderr}"
    )


def test_check_adjudications_invalid_utf8_bytes_is_fatal_no_traceback(tmp_path):
    """Same as above, for canon_adjudications.json -- with a valid canon
    present, so the adjudications reader (not the canon reader) is the
    thing actually exercised."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_bytes(b"\xff\xfe\x00bad")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr, (
        f"invalid UTF-8 bytes must surface as a clean named FATAL, not an "
        f"uncaught traceback:\n{proc.stderr}"
    )


def test_orphaned_cap_override_with_non_canon_scope_key_is_counted(tmp_path):
    """Codex round-4 finding B: degenerate_cap_overrides may carry a key
    OTHER than the single project-global scope token '__canon__' (e.g. a
    stale/hand-typed scope from before a rename, or plain drift) -- this
    must be counted as an orphaned record too, not silently ignored. The
    prior implementation only ever checked CAP_SCOPE_TOKEN's own presence
    (`CAP_SCOPE_TOKEN in adjudications_doc["degenerate_cap_overrides"]`), so
    any OTHER key in that dict was invisible to the orphan count -- a
    genuine drift in the doc that would never surface anywhere. Canon here
    has zero required items at all (well under any cap, no active
    cap-note), so the gate must stay fully clean -- the stray key is purely
    informational, never blocking."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])
    write_adjudications(root, degenerate_cap_overrides={
        "old-scope": cap_override_record(entity_count=1, pair_count=0, cap=40, entity_set_fingerprint="a" * 64),
    })

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True
    assert summary["blocking_count"] == 0
    assert summary["totals"]["cap_notes"] == 0
    assert summary["totals"]["orphaned_records"] >= 1
    assert any("old-scope" in w for w in summary["warnings"]), (
        f"expected a warning naming the orphaned non-__canon__ scope key, got: {summary['warnings']}"
    )


# ===========================================================================
# 26. Regression test for a codex round-5 finding (MAJOR). Builder is fixing
#     the script in parallel -- may legitimately fail until that fix lands
#     (see the lead's report for which).
# ===========================================================================


def test_cap_override_with_boolean_identity_value_is_stale_not_fresh(tmp_path):
    """Codex round-5 finding (MAJOR): a degenerate_cap_overrides record whose
    numeric cap-identity fields (entity_count/pair_count/cap) use PYTHON
    BOOLEANS that happen to numerically equal the real values (True==1,
    False==0) must NOT be treated as a fresh, matching override -- Python's
    `==` operator treats bool as a subtype of int, so a naive `override.get(
    "pair_count") == cap_note["pair_count"]` comparison would silently
    accept `pair_count: true` as matching a real pair_count of 1. This is a
    genuine false-green: the override never actually recorded the real
    integer values a human/codex signed off on. Runtime must require plain
    ints (matching the persisted-file schema's own type:"integer", which
    rejects bool under strict JSON Schema semantics)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo"])

    bool_override = {
        "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
        "entity_count": 2,   # real int, unchanged
        "pair_count": True,  # True == 1, the REAL pair_count -- must NOT satisfy
        "cap": False,        # False == 0, the REAL cap -- must NOT satisfy
        "entity_set_fingerprint": fingerprint,
    }
    write_adjudications(root, degenerate_cap_overrides={"__canon__": bool_override})

    proc = run_audit(root, "--check", "--pair-review-cap", "0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_notes"] == 1
    assert summary["totals"]["cap_overrides_ok"] == 0
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["gate_passed"] is False
    assert summary["blocking_count"] >= 1


def test_cap_override_with_matching_int_identity_values_is_fresh(tmp_path):
    """Companion/control for the boolean-identity test above: the SAME
    fixture (same 2 entities, same --pair-review-cap 0) but with plain ints
    (pair_count:1, cap:0) instead of bools -- proves the fixture is
    otherwise a genuine match and isolates the bool coercion as the ONLY
    variable that flips the outcome (the existing cap-override lifecycle
    test at n=3/cap=1 already covers a generic all-int fresh path, but not
    this exact fixture)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo"])

    int_override = {
        "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
        "entity_count": 2, "pair_count": 1, "cap": 0,
        "entity_set_fingerprint": fingerprint,
    }
    write_adjudications(root, degenerate_cap_overrides={"__canon__": int_override})

    proc = run_audit(root, "--check", "--pair-review-cap", "0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_ok"] == 1
    assert summary["totals"]["cap_overrides_missing"] == 0
    assert summary["gate_passed"] is True


# ===========================================================================
# 27. Regression tests for codex round-6 finding -- lone surrogate. Builder
#     is fixing the script in parallel -- may legitimately fail until that
#     fix lands (see the lead's report for which).
#
#     CRITICAL byte-level detail: each fixture below writes the LITERAL
#     6-character JSON escape `\ud800` (backslash, u, d, 8, 0, 0) as plain
#     ASCII text -- clean, decodable UTF-8 bytes on disk. json.loads() then
#     turns that escape into a genuine Python lone-surrogate code point
#     (U+D800, an unpaired UTF-16 surrogate), which IS valid JSON but is
#     NOT UTF-8 encodable. In Python source, writing that literal 6-char
#     escape into a string requires a DOUBLE backslash (`"\\ud800"`) --  a
#     single backslash would be interpreted by Python itself as a lone-
#     surrogate escape in the SOURCE string, which .write_text(encoding=
#     "utf-8") would then fail to encode at write time (a different,
#     already-covered code path -- undecodable raw bytes, round 4). The
#     JSON text is written directly (not via json.dumps, which would
#     serialize a Python lone-surrogate string differently) so the on-disk
#     bytes are exactly and only the literal escape sequence.
# ===========================================================================


def test_check_canon_json_lone_surrogate_is_fatal_no_traceback(tmp_path):
    """Codex round-6 finding: canon.json containing a JSON string with a
    lone-surrogate \\uXXXX escape (valid JSON, NOT UTF-8 encodable) must be
    a clean, named FATAL -- never an uncaught UnicodeEncodeError traceback
    downstream at key construction or stdout emission. This is the SAME
    _read_json_file boundary that already rejects undecodable raw UTF-8
    bytes (round 4), extended to also reject a decodable-but-unencodable
    parsed VALUE."""
    root = make_durable_root(tmp_path)
    canon_text = '{"entries":{},"review_queue":[{"source_form":"\\ud800","note":"x"}]}'
    (root / "canon.json").write_text(canon_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not UTF-8 encodable" in proc.stderr


def test_check_canon_json_lone_surrogate_in_proper_entry_is_fatal_not_false_green(tmp_path):
    """Codex round-6 finding, false-green guard: a canon whose ONLY content
    is a single proper-name entry keyed by (and carrying) a lone-surrogate
    source_form. Before the fix, the enumeration-critical checks (which
    only require a non-empty STRING -- a lone surrogate IS a non-empty
    Python str) would pass this through cleanly, and since it is the only
    entry, no group of 2+ records ever forms -- required_items=0,
    gate_passed=true, a silent pass of genuinely malformed data. Must now
    be fatal exit 2 (never rc 0, never gate_passed) with the SAME
    not-UTF-8-encodable error, caught at the read boundary before
    enumeration ever runs."""
    root = make_durable_root(tmp_path)
    canon_text = (
        '{"entries":{"\\ud800":{"source_form":"\\ud800",'
        '"canonical_target_form":"Whoever","is_proper_name":true,'
        '"basis":"transliterated","confidence":"high"}},"review_queue":[]}'
    )
    (root / "canon.json").write_text(canon_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not UTF-8 encodable" in proc.stderr


def test_check_adjudications_lone_surrogate_is_fatal_no_traceback(tmp_path):
    """Codex round-6 finding: same lone-surrogate fatal, but in
    canon_adjudications.json -- proves the adjudications reader inherits
    the SAME _read_json_file boundary check as the canon reader (both call
    through the one shared helper)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    adjudications_text = (
        '{"schema_version":1,"adjudications":{"x":{"verdict_class":"confirmed_ok",'
        '"reviewed_by":"\\ud800","reason":"r"}},"degenerate_cap_overrides":{},'
        '"review_queue_risk_overrides":{}}'
    )
    (root / "canon_adjudications.json").write_text(adjudications_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not UTF-8 encodable" in proc.stderr


# ===========================================================================
# 28. Regression tests for codex round-7 finding -- whitespace-only canon
#     fields. Builder is fixing the script in parallel -- may legitimately
#     fail until that fix lands (see the lead's report for which). Every
#     malformed fixture below is otherwise fully valid (valid
#     is_proper_name/basis/generation_hashes/etc.) so the ONLY defect under
#     test is the one blank field named in each test. NOTE: "whitespace-
#     only" here means genuine ASCII spaces ("   ") -- a distinct code path
#     from invalid UTF-8 bytes (round 4) or a lone surrogate (round 6).
# ===========================================================================


def test_check_canon_whitespace_only_target_form_is_fatal_not_false_green(tmp_path):
    """Codex round-7 finding, the false-green case (codex's exact repro): a
    single proper-name entry whose canonical_target_form is
    whitespace-only ("   ") -- the OLD `not isinstance(x, str) or not x`
    check treated "   " as a non-empty string (truthy), silently passing
    it through as a legitimate target form. Since it was the only entry,
    no Cat 1/2/3 group ever formed -- required_items=0, gate_passed=true, a
    silent false-green of genuinely blank data. Must now be a clean fatal
    exit 2 -- never rc 0, and (since a fatal prints NO stdout JSON at all)
    there is no gate_passed field left to have falsely reported true --
    never an uncaught traceback."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Alice", "   ")])  # canonical_target_form whitespace-only

    proc = run_audit(root, "--check")

    assert_fatal(proc)  # rc==2, empty stdout (so no gate_passed:true anywhere), non-empty stderr
    assert "Traceback" not in proc.stderr
    assert "canonical_target_form" in proc.stderr


def test_check_canon_whitespace_only_source_form_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("   ", "Target")])  # source_form whitespace-only

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "source_form" in proc.stderr


def test_check_canon_whitespace_only_basis_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Alice", "Target", basis="   ")])  # basis whitespace-only

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "basis" in proc.stderr


def test_check_queued_whitespace_only_source_form_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [], review_queue=[queued("   ")])  # queued source_form whitespace-only

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "source_form" in proc.stderr


def test_check_canon_leading_trailing_whitespace_around_content_is_accepted(tmp_path):
    """Positive control for the whitespace-only tests above: a value with
    genuine LEADING/TRAILING whitespace around real content (e.g.
    " Alice ") is NOT blank after stripping -- _nonempty_str only rejects
    values that are EMPTY once stripped, never a real value that merely
    has incidental surrounding whitespace. Must be accepted, no fatal."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry(" Alice ", " Target ")])

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)


# ===========================================================================
# 29. Regression tests for codex round-8 findings -- non-finite constants +
#     integer-valued float cap identity. Builder is fixing the script in
#     parallel -- may legitimately fail until that fix lands (see the
#     lead's report for which).
# ===========================================================================


def test_check_canon_json_nan_constant_is_fatal(tmp_path):
    """Codex round-8 finding 1: json.loads() accepts the non-standard JSON
    constants NaN/Infinity/-Infinity by default (a Python-specific
    extension, not valid per RFC 8259). Before the fix, a canon.json
    containing a bare NaN token parsed successfully into a Python
    float('nan') and silently passed every enumeration-critical check
    (generation_hashes is never re-validated by this audit) -- a
    false-green (rc=0, gate_passed=true) of genuinely invalid JSON. Must
    now be a clean fatal exit 2 via a json.loads(parse_constant=...) hook,
    never an uncaught traceback. Written as literal file TEXT (not via
    json.dumps, which would stay in Python-float land) so the on-disk
    bytes contain the bare `NaN` token itself."""
    root = make_durable_root(tmp_path)
    canon_text = '{"entries":{},"review_queue":[],"generation_hashes":NaN}'
    (root / "canon.json").write_text(canon_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not valid JSON" in proc.stderr


def test_check_canon_json_infinity_constant_is_fatal(tmp_path):
    """Same as above, for the Infinity constant."""
    root = make_durable_root(tmp_path)
    canon_text = '{"entries":{},"review_queue":[],"generation_hashes":Infinity}'
    (root / "canon.json").write_text(canon_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not valid JSON" in proc.stderr


def test_cap_override_with_integer_valued_float_identity_is_fresh(tmp_path):
    """Codex round-8 finding 2: cap-identity fields as integer-VALUED
    floats (entity_count:2.0, pair_count:1.0, cap:0.0 -- ordinary finite
    JSON numbers that parse to Python float but represent whole numbers)
    must be accepted as FRESH, matching jsonschema's own "type":"integer"
    semantics (which treats 1.0 as a valid integer) -- rejecting them would
    falsely report a schema-valid override as stale. Same fixture as the
    round-5 boolean-identity tests (2 entities, --pair-review-cap 0)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo"])

    float_override = {
        "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
        "entity_count": 2.0, "pair_count": 1.0, "cap": 0.0,
        "entity_set_fingerprint": fingerprint,
    }
    write_adjudications(root, degenerate_cap_overrides={"__canon__": float_override})

    proc = run_audit(root, "--check", "--pair-review-cap", "0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_ok"] == 1
    assert summary["totals"]["cap_overrides_missing"] == 0
    assert summary["gate_passed"] is True


def test_cap_override_with_fractional_float_identity_is_stale(tmp_path):
    """Companion to the integer-valued-float test above: a genuinely
    FRACTIONAL float (pair_count:1.5) must still be rejected as stale --
    only WHOLE-number floats are accepted (matching float.is_integer()),
    never a value that merely rounds to the right number."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo"])

    fractional_override = {
        "risk_accepted_by": "test-reviewer", "reason": "fixture-authored risk acceptance",
        "entity_count": 2.0, "pair_count": 1.5, "cap": 0.0,
        "entity_set_fingerprint": fingerprint,
    }
    write_adjudications(root, degenerate_cap_overrides={"__canon__": fractional_override})

    proc = run_audit(root, "--check", "--pair-review-cap", "0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_ok"] == 0
    assert summary["totals"]["cap_overrides_missing"] == 1
    assert summary["gate_passed"] is False


# Point 5 of the round-8 ask: the existing round-5
# test_cap_override_with_boolean_identity_value_is_stale_not_fresh already
# locks bool-still-rejected under the now-widened _int_eq (it stays green
# below -- re-verified as part of this round's full-file run, not
# duplicated here).


# ===========================================================================
# 30. Regression tests for codex round-9 findings -- numeric overflow to
#     inf + schema_version enforcement. Builder is fixing the script in
#     parallel -- may legitimately fail until that fix lands (see the
#     lead's report for which).
# ===========================================================================


def test_check_canon_json_exponent_overflow_to_inf_is_fatal(tmp_path):
    """Codex round-9 finding 1: a valid JSON numeric literal that overflows
    to inf when parsed (e.g. 1e999 -- valid JSON SYNTAX, but json.loads
    parses it to float('inf'), a non-finite VALUE) must be a clean fatal
    exit 2. DISTINCT from round-8's named-constant path (bare `NaN`/
    `Infinity` tokens, which fail json.loads outright with "not valid
    JSON") -- 1e999 parses successfully as a NUMBER, so it is caught by a
    separate allow_nan=False re-serialization check with its own
    "non-finite number" wording. Written as literal file TEXT (not
    json.dumps(float('inf')), which would emit the string "Infinity" --
    the WRONG on-disk representation, already covered by round-8) so the
    raw bytes contain the numeric literal `1e999` itself."""
    root = make_durable_root(tmp_path)
    canon_text = '{"entries":{},"review_queue":[],"generation_hashes":1e999}'
    (root / "canon.json").write_text(canon_text, encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "non-finite number" in proc.stderr
    assert "not valid JSON" not in proc.stderr  # distinct from round-8's named-constant path


def test_check_adjudications_wrong_schema_version_is_fatal(tmp_path):
    """Codex round-9 finding 2 (codex's repro): canon_adjudications.json
    schema_version:2 -- the schema's own {"schema_version":{"const":1}}
    constraint was never enforced at runtime, so ANY value silently passed.
    Must now be a clean fatal exit 2, mirroring the schema exactly. Built
    manually (not via write_adjudications, which always injects
    schema_version:1)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    adjudications_doc = {
        "schema_version": 2,
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(adjudications_doc), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "schema_version" in proc.stderr


def test_check_adjudications_missing_schema_version_is_accepted(tmp_path):
    """schema_version is OPTIONAL (the schema has no top-level `required`
    listing it) -- omitting it entirely must NOT be fatal, locking the
    'optional' half of the round-9 fix so a future change doesn't wrongly
    make a missing schema_version fatal too."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])
    adjudications_doc = {
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(adjudications_doc), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True


def test_check_adjudications_integer_valued_float_schema_version_is_accepted(tmp_path):
    """schema_version:1.0 (a JSON float that is integer-valued) must be
    accepted, consistent with round-8's widened _int_eq and jsonschema's
    own const:1 accepting 1.0 -- schema_version is checked via the SAME
    _int_eq helper as the cap-identity fields."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])
    adjudications_doc = {
        "schema_version": 1.0,
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(adjudications_doc), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True


def test_check_adjudications_boolean_schema_version_is_fatal(tmp_path):
    """schema_version:true (True==1 in Python) must still be rejected --
    the same bool-exclusion discipline as round-5/round-8's cap-identity
    fields, now applying to schema_version too (both route through the
    same _int_eq helper, whose `type(value) is int` check excludes bool by
    exact-type identity)."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    adjudications_doc = {
        "schema_version": True,
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(adjudications_doc), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "schema_version" in proc.stderr


# ===========================================================================
# 31. Regression tests for codex round-10 finding -- advisory-field
#     schema/runtime convergence. Builder is fixing the script/schema in
#     parallel -- may legitimately fail until that fix lands (see the
#     lead's report for which).
# ===========================================================================


def test_adjudications_advisory_kind_wrong_type_is_schema_valid_and_gate_passes(tmp_path):
    """Codex round-10 finding, codex's repro: the schema over-constrained
    'kind' (an ADVISORY human-readability label -- the record KEY, not
    'kind', is what the runtime matches against) to type:"string". A
    schema-invalid kind (e.g. 123) previously failed jsonschema.validate()
    while the RUNTIME gate still passed cleanly (it never reads 'kind' at
    all) -- a genuine schema-vs-runtime drift. The schema now leaves
    'kind' unconstrained so both agree: a mistyped advisory label must
    never block the gate, and must never fail schema validation either."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    key = cat2_key("Zhan", ["Jean", "Valjean"])

    doc = {
        "schema_version": 1,
        "adjudications": {
            key: {
                "kind": 123,  # wrong type -- advisory, must not matter
                "verdict_class": "confirmed_ok",
                "reviewed_by": "r", "reason": "ok",
            },
        },
        "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    assert_adjudications_schema_valid(doc)  # must NOT raise

    (root / "canon_adjudications.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True
    assert summary["totals"]["confirmed_ok"] == 1


def test_adjudications_advisory_timestamp_wrong_type_is_schema_valid(tmp_path):
    """Same drift, for the advisory 'timestamp' field: a non-string value
    (123) must not fail schema validation, and the runtime (which never
    reads timestamp) must not fatal on it either. Minimal fixture -- the
    record doesn't need to correspond to a real required item; an
    orphaned record is informational, non-blocking, never fatal."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])
    doc = {
        "schema_version": 1,
        "adjudications": {
            "duplicate_source_form::" + "a" * 64: {
                "verdict_class": "confirmed_ok", "reviewed_by": "r", "reason": "ok",
                "timestamp": 123,  # wrong type -- advisory
            },
        },
        "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    assert_adjudications_schema_valid(doc)

    (root / "canon_adjudications.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr  # not fatal, not even blocking (orphaned)
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)


def test_adjudications_advisory_contract_wrong_type_is_schema_valid(tmp_path):
    """Same drift, for the top-level '_contract' field: a non-string value
    (123) must not fail schema validation (it's documentation-only, never
    read by the runtime), and the runtime must not fatal on it either."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])
    doc = {
        "schema_version": 1,
        "_contract": 123,  # wrong type -- documentation only
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    assert_adjudications_schema_valid(doc)

    (root / "canon_adjudications.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = run_audit(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)


def test_adjudications_bad_verdict_class_still_schema_invalid():
    """Load-bearing field, still strict: proves the round-10 loosening was
    surgical (kind/timestamp/_contract only), not a blanket weakening --
    an invalid verdict_class ("bogus") must still fail schema validation.
    (The runtime side -- invalid_verdict_class, blocking, not fatal -- is
    already covered by test_invalid_verdict_class_is_blocking_not_fatal,
    not duplicated here.)"""
    doc = {
        "schema_version": 1,
        "adjudications": {
            "existing_merge::" + "a" * 64: {
                "verdict_class": "bogus",  # not in {confirmed_ok, adverse}
                "reviewed_by": "r", "reason": "ok",
            },
        },
        "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    with pytest.raises(jsonschema.ValidationError):
        assert_adjudications_schema_valid(doc)


def test_adjudications_wrong_schema_version_still_schema_invalid():
    """Load-bearing field, still strict: schema_version's const:1 must
    still reject a wrong value (2) at the schema level -- confirms the
    round-10 loosening didn't touch this. (The runtime side -- fatal exit
    2 -- is already covered by
    test_check_adjudications_wrong_schema_version_is_fatal from round 9,
    not duplicated here.)"""
    doc = {
        "schema_version": 2,
        "adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    with pytest.raises(jsonschema.ValidationError):
        assert_adjudications_schema_valid(doc)


# ===========================================================================
# 32. Ratified boundary: malformed orphan-record content is intentionally
#     non-blocking. Codex round-11 finding + USER DECISION: a malformed
#     ORPHAN adjudication record (its key matches no currently-required
#     item, e.g. verdict_class:"bogus") is schema-INVALID but NON-BLOCKING
#     at the gate. Ratified as INTENTIONAL two-layer design -- the runtime
#     gate is not a full schema validator, and an orphan record cannot hide
#     a current risk because every required item is recomputed FRESH from
#     canon.json on each --check (never read from the adjudications file
#     itself). This test locks the behavior so a future change doesn't
#     accidentally "fix" it into blocking. Behavior is UNCHANGED this
#     round -- this is a pin, not a regression test for a bug fix.
# ===========================================================================


def test_malformed_orphan_adjudication_content_is_non_blocking_by_design(tmp_path):
    """Codex's exact repro: an EMPTY canon (entries {}, review_queue []) --
    zero required items across all 4 categories, so EVERY adjudications
    record is, by definition, an orphan -- plus a hand-authored
    adjudications file carrying one record under a key that matches no
    current required item, whose content is schema-INVALID
    (verdict_class:"bogus", a load-bearing field that would normally be
    rejected). Because the record's key never matches any of this run's
    freshly-recomputed required items, crosscheck_regular_items() never
    even looks at its content -- it is only ever consulted via the
    key-based set-difference in the orphan-detection helper, which is
    purely informational. Runtime gate must report this as non-blocking."""
    root = make_durable_root(tmp_path)
    write_canon(root, [])  # zero required items in every category

    orphan_key = "existing_merge::" + "a" * 64  # matches no current required item
    doc = {
        "schema_version": 1,
        "adjudications": {
            orphan_key: {
                "verdict_class": "bogus",  # schema-invalid load-bearing value
                "reviewed_by": "r", "reason": "ok",
            },
        },
        "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }

    # Documents the deliberate drift: this SAME doc fails schema validation
    # (verdict_class enum is still strict, per round-10) -- the runtime
    # reader intentionally diverges from the full authoring schema for
    # ORPHAN content specifically, since it never inspects it.
    with pytest.raises(jsonschema.ValidationError):
        assert_adjudications_schema_valid(doc)

    (root / "canon_adjudications.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["gate_passed"] is True
    assert summary["blocking_count"] == 0
    assert summary["totals"]["orphaned_records"] >= 1


# ===========================================================================
# 33. Regression tests for codex round-12 finding -- deeply-nested JSON.
#     Builder is fixing the script in parallel -- may legitimately fail
#     until that fix lands (see the lead's report for which).
#
#     N=500000 (matching codex's own repro) reliably triggers a genuine
#     RecursionError in json.loads()/json.dumps() well before this nesting
#     depth would even be reached by the interpreter's default recursion
#     limit -- verified empirically in this environment: parsing completes
#     (and raises) in well under a second, so a large N does not make the
#     test slow. Built as literal bracket TEXT (never json.dumps of a
#     Python-constructed deep list, which could itself RecursionError at
#     construction time, before the file is even written).
# ===========================================================================


def _deeply_nested_json_text(n=500000):
    return "[" * n + "0" + "]" * n


def test_check_canon_json_deeply_nested_is_fatal_no_traceback(tmp_path):
    """Codex round-12 finding: a deeply-nested (but otherwise syntactically
    valid) JSON document must be a clean, named FATAL -- never an uncaught
    RecursionError traceback. _read_json_file now catches RecursionError
    from json.loads()/json.dumps() and converts it to a
    CanonAdjudicationAuditError."""
    root = make_durable_root(tmp_path)
    (root / "canon.json").write_text(_deeply_nested_json_text(), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "too deeply nested" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "RecursionError" not in proc.stderr


def test_check_adjudications_deeply_nested_is_fatal_no_traceback(tmp_path):
    """Same as above, for canon_adjudications.json -- with a valid canon
    present, so the adjudications reader (not the canon reader) is the
    thing actually exercised, proving it inherits the same guard via the
    shared _read_json_file helper."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Jean", "Zhan"), entry("Valjean", "Zhan")])
    (root / "canon_adjudications.json").write_text(_deeply_nested_json_text(), encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "too deeply nested" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "RecursionError" not in proc.stderr


# ===========================================================================
# 34. Regression tests for codex round-13 finding -- adjudications path
#     validated even when canon absent. Builder is fixing the script in
#     parallel -- may legitimately fail until that fix lands (see the
#     lead's report for which). In ALL five tests below, canon.json is
#     DELIBERATELY NOT written.
# ===========================================================================


def test_check_absent_canon_with_adjudications_path_directory_is_fatal(tmp_path):
    """Codex round-13 finding, codex's exact repro: canon.json ABSENT, but
    a DIRECTORY sits at the default canon_adjudications.json path.
    Previously run_check returned exit 0 (canon_present:false) BEFORE ever
    validating the adjudications path, so this bad invocation silently
    passed. Must now be fatal exit 2 -- read_adjudications runs regardless
    of canon presence."""
    root = make_durable_root(tmp_path)
    (root / "canon_adjudications.json").mkdir()

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not a regular file" in proc.stderr


def test_check_absent_canon_with_dangling_symlink_adjudications_is_fatal(tmp_path):
    """Same as above, for a DANGLING symlink at the adjudications path
    instead of a directory."""
    root = make_durable_root(tmp_path)
    _make_dangling_symlink(root, "canon_adjudications.json")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr


def test_check_absent_canon_with_malformed_adjudications_is_fatal(tmp_path):
    """Same as above, for MALFORMED JSON at the adjudications path."""
    root = make_durable_root(tmp_path)
    (root / "canon_adjudications.json").write_text("{not json", encoding="utf-8")

    proc = run_audit(root, "--check")

    assert_fatal(proc)
    assert "Traceback" not in proc.stderr
    assert "not valid JSON" in proc.stderr


def test_check_absent_canon_with_absent_adjudications_still_exit_0(tmp_path):
    """Guard against the round-13 fix over-correcting: canon absent AND
    adjudications absent (neither file written) must still be exit 0 --
    an ABSENT adjudications file stays non-fatal (treated as empty), only
    a PRESENT-but-malformed one is fatal. The summary's warnings[] may now
    include an 'adjudications file not found' note (from read_adjudications
    now always running) -- that's expected, not asserted against."""
    root = make_durable_root(tmp_path)
    # Neither canon.json nor canon_adjudications.json is written.

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["gate_passed"] is True


def test_check_absent_canon_with_valid_adjudications_exit_0(tmp_path):
    """canon absent, but a VALID adjudications file is present -- accepted
    cleanly (its content is simply unused, since there are no required
    items to cross-check it against)."""
    root = make_durable_root(tmp_path)
    write_adjudications(root)

    proc = run_audit(root, "--check")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["gate_passed"] is True


# ===========================================================================
# Extra. Sample canon_adjudications.json validates against its own schema.
# ===========================================================================


def test_sample_adjudications_file_validates_against_schema(tmp_path):
    root = make_durable_root(tmp_path)
    doc = write_adjudications(
        root,
        adjudications={
            "duplicate_source_form::" + "a" * 64: adjudication_record("duplicate_source_form", "confirmed_ok"),
            "candidate_missed_merge_pair::" + "d" * 64: adjudication_record("candidate_missed_merge_pair", "adverse"),
        },
        degenerate_cap_overrides={
            "__canon__": cap_override_record(entity_count=5, pair_count=10, cap=40, entity_set_fingerprint="b" * 64),
        },
        review_queue_risk_overrides={
            "review_queue_unresolved::" + "c" * 64: risk_override_record(),
        },
    )
    assert_adjudications_schema_valid(doc)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
