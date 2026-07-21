"""tests/canon_adjudication_audit_homonym_split.test.py -- RFC #215 Phase 1
(1c/1e) coverage for canon_adjudication_audit.py's `homonym_split` category
(5), the `collapsed_split` reconciliation, `canon_absent_with_senses`, the
narrowed `--advisory` contract, `--particle-config` required/optional, and
the `--senses-path` present-default policy (plan §10 / contract §10) --
everything NOT requiring a real evidence-verification pass against
manifest.json (see tests/canon_adjudication_audit_evidence_matrix.test.py
for that half: wrong-offset/shifted-name/hash-mismatch/elision-parity/
config-threading).

Every sense built here carries GENUINELY VALID evidence (a real matcher
span from occ_index.production_occurrences + a real sha256 of the
enclosing block) so evidence_unverified never fires as a side effect --
each test isolates exactly the one dimension its name describes.

## Fixture strategy

Same isolated-durable_root convention as tests/canon_adjudication_audit.
test.py: the REAL canon_adjudication_audit.py staged into {root}/scripts/
(via Infra's sanctioned tests/_senses_fixture.py::stage_consumer(), which
also brings canon_senses.py + its schema), run as a subprocess exactly as
production does. Since 1c/1e added `from bootstrap_names import ...` /
`from evidence_verify import verify_senses` to the audit script itself
(on top of its `from canon_senses import ...`), make_durable_root() below
ALSO stages bootstrap_names.py + occ_index.py + evidence_verify.py --
this audit script's own extra deps, not covered by stage_consumer()
(which only knows about canon_senses.py, shared by every consumer).
"""
import hashlib
import json
import shutil
import subprocess
import sys
import importlib.util
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
LANGUAGES_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_adjudication_audit.py"
assert SCRIPT_SRC.is_file(), f"canon_adjudication_audit.py not found at {SCRIPT_SRC}"

SUMMARY_SCHEMA_PATH = SCHEMAS_SRC_DIR / "canon-adjudication-audit-summary.schema.json"
SUMMARY_SCHEMA = json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))
assert (LANGUAGES_SRC_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_SRC_DIR}"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

# See module docstring: canon_adjudication_audit.py's own EXTRA dependency closure beyond
# canon_senses.py (which stage_consumer() already brings), post-1c/1e.
_EXTRA_DEP_SCRIPTS = ("bootstrap_names.py", "occ_index.py", "evidence_verify.py")
for _dep in _EXTRA_DEP_SCRIPTS:
    assert (SCRIPTS_SRC_DIR / _dep).is_file(), f"{_dep} not found at {SCRIPTS_SRC_DIR / _dep}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/evidence_verify.test.py's own loader: loads a standalone
    script in-process (from the SOURCE assets tree, never the copied
    fixture) with `extra_sys_path` on sys.path so its own top-level
    `from bootstrap_names import ...` resolves -- used here only to compute
    REAL production spans + a real occurrence-window sha256 for fixture
    construction, never to exercise the module under test itself (that
    always happens via the real CLI subprocess, see run_audit())."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bn_for_homonym_split_test", SCRIPTS_SRC_DIR / "bootstrap_names.py", SCRIPTS_SRC_DIR)
oi = _load_module("oi_for_homonym_split_test", SCRIPTS_SRC_DIR / "occ_index.py", SCRIPTS_SRC_DIR)

FR_LANG = bn.load_language_config("fr.json", languages_dir=LANGUAGES_SRC_DIR)


# ---------------------------------------------------------------------------
# Key computation (test-side) -- mirrors canon_adjudication_audit.test.py's
# own N()/canonical_json()/cat*_key() discipline for category 5.
# ---------------------------------------------------------------------------


def N(s):
    import unicodedata
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


def canonical_json(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def split_key(source_form, senses):
    """The homonym_split required-item key (plan §1c / contract §4):
    'homonym_split::' + sha256(canonical_json({source_form, senses}))."""
    identity = {"source_form": source_form, "senses": senses}
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return f"homonym_split::{digest}"


def adjudication_record(verdict_class, reviewed_by="test-reviewer", reason="fixture-authored verdict"):
    return {"verdict_class": verdict_class, "reviewed_by": reviewed_by, "reason": reason}


# ---------------------------------------------------------------------------
# Manifest / senses fixture builders -- every evidence record here is
# GENUINELY VALID (real matcher span, real sha256) unless a test explicitly
# corrupts one field to isolate a specific failure mode.
# ---------------------------------------------------------------------------


def real_spans(source_form, block_text, lang=FR_LANG):
    return oi.production_occurrences(source_form, block_text, lang)


def make_sense(sense_id, disambiguator, block_text, char_start, char_end,
               block="b1", seg=None, index_scope="narrative"):
    context_start, context_end = 0, len(block_text)
    sha = hashlib.sha256(block_text[context_start:context_end].encode("utf-8")).hexdigest()
    return {
        "sense_id": sense_id, "disambiguator": disambiguator, "index_scope": index_scope,
        "evidence": {
            "block": block, "seg": seg, "char_start": char_start, "char_end": char_end,
            "context_start": context_start, "context_end": context_end, "sha256": sha,
        },
    }


def two_jean_senses(block_text="Jean parla à Jean encore une fois."):
    """A genuinely-valid 2-sense split for 'Jean' in a block with exactly two
    real occurrences -- the standard fixture most tests below build on."""
    spans = real_spans("Jean", block_text)
    assert len(spans) == 2, f"expected 2 'Jean' occurrences, got {spans}"
    (s0, e0), (s1, e1) = spans
    senses = [
        make_sense("s1", "the baker", block_text, s0, e0),
        make_sense("s2", "the soldier", block_text, s1, e1),
    ]
    return block_text, senses


def three_jean_senses(block_text="Jean parla à Jean, puis Jean partit."):
    """A genuinely-valid 3-sense split -- lets the freshness 'drop a sense'
    case shrink to 2 senses (still schema-valid, minItems:2) instead of 1
    (which load_senses hard-rejects, testing a fatal instead of freshness)."""
    spans = real_spans("Jean", block_text)
    assert len(spans) == 3, f"expected 3 'Jean' occurrences, got {spans}"
    senses = [
        make_sense(f"s{i}", f"sense {i}", block_text, s, e)
        for i, (s, e) in enumerate(spans)
    ]
    return block_text, senses


def write_manifest(root, blocks):
    (root / "manifest.json").write_text(json.dumps({"blocks": blocks}, ensure_ascii=False), encoding="utf-8")


def write_senses_raw(root, doc):
    (root / "canon_senses.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def write_senses(root, entries_by_source_form):
    doc = {"schema_version": 1, "entries_by_source_form": entries_by_source_form}
    return write_senses_raw(root, doc)


def write_canon_raw(root, doc):
    (root / "canon.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def write_canon(root, entries, review_queue=None):
    if isinstance(entries, list):
        keyed = {}
        for i, e in enumerate(entries):
            key = e["source_form"] if e["source_form"] not in keyed else f"{e['source_form']}__{i}"
            keyed[key] = e
        entries = keyed
    doc = {
        "entries": entries,
        "review_queue": review_queue if review_queue is not None else [],
        "generation_hashes": {"particle_config_hash": "test-hash", "derivation_bundle_hash": "test-hash"},
    }
    return write_canon_raw(root, doc)


def entry(source_form, target, is_proper_name=True, basis="transliterated"):
    return {
        "source_form": source_form, "canonical_target_form": target,
        "is_proper_name": is_proper_name, "basis": basis,
    }


def write_adjudications(root, adjudications=None):
    doc = {
        "schema_version": 1,
        "adjudications": adjudications if adjudications is not None else {},
        "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def make_durable_root(tmp_path, with_languages=True):
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_adjudication_audit.py")
    scripts_dir = root / "scripts"
    for dep in _EXTRA_DEP_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / dep, scripts_dir / dep)
    if with_languages:
        shutil.copytree(LANGUAGES_SRC_DIR, root / "languages")
    return root


def run_audit(root, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected exactly one JSON line, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}"
    return json.loads(lines[0])


def assert_summary_schema_valid(summary):
    jsonschema.validate(instance=summary, schema=SUMMARY_SCHEMA)


def assert_fatal(proc):
    assert proc.returncode == 2, f"expected fatal exit 2, got rc={proc.returncode}\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    assert proc.stdout.strip() == "", f"a fatal run must never print stdout, got: {proc.stdout!r}"
    assert proc.stderr.strip() != "", "a fatal run must print a named stderr error"


# ===========================================================================
# 1. homonym_split category wiring
# ===========================================================================


def test_homonym_split_enumerated_and_missing_verdict_when_senses_nonempty(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])  # unrelated entry, no collapse

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 1
    assert summary["totals"]["missing_verdict"] == 1
    assert summary["totals"]["collapsed_split"] == 0
    assert summary["totals"]["evidence_unverified"] == 0
    assert summary["blocking_count"] == 1 and summary["gate_passed"] is False
    assert proc.returncode == 1


def test_homonym_split_confirmed_ok_clears_gate(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["totals"]["missing_verdict"] == 0
    assert summary["blocking_count"] == 0 and summary["gate_passed"] is True
    assert proc.returncode == 0


def test_homonym_split_adverse_blocks(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("adverse")})

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["adverse"] == 1
    assert summary["blocking_count"] == 1 and proc.returncode == 1


@pytest.mark.parametrize("mutate", ["disambiguator", "drop_sense", "swap_evidence"])
def test_homonym_split_freshness_any_edit_reopens(tmp_path, mutate):
    """Freshness (RFC #215 1c / plan 'Freshness' test): approve a split, then
    edit a disambiguator / drop a sense / swap an evidence span -> the prior
    confirmed_ok no longer satisfies the (new) key, re-blocking as a fresh
    missing_verdict. Uses a 3-sense fixture (not 2) so 'drop a sense' can
    shrink to 2 senses -- still schema-valid (minItems:2) -- and genuinely
    exercise 'the identity changed because a sense disappeared', rather than
    emptying the whole sidecar (which would test a DIFFERENT thing:
    category 5 vanishing entirely, not a stale-but-still-split identity)."""
    root = make_durable_root(tmp_path)
    block_text, senses = three_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])
    old_key = split_key("Jean", senses)
    write_adjudications(root, {old_key: adjudication_record("confirmed_ok")})

    proc_before = run_audit(root, "--check", "--particle-config", "fr.json")
    summary_before = parse_stdout(proc_before)
    assert summary_before["blocking_count"] == 0 and proc_before.returncode == 0

    mutated = json.loads(json.dumps(senses))  # deep copy
    if mutate == "disambiguator":
        mutated[0]["disambiguator"] = "a DIFFERENT gloss"
    elif mutate == "drop_sense":
        mutated = mutated[:2]  # 3 -> 2 senses: still >= minItems:2, still genuinely split
    elif mutate == "swap_evidence":
        mutated[1]["evidence"]["char_start"], mutated[1]["evidence"]["char_end"] = (
            mutated[0]["evidence"]["char_start"], mutated[0]["evidence"]["char_end"],
        )

    write_senses(root, {"Jean": {"senses": mutated}})
    new_key = split_key("Jean", mutated)
    assert new_key != old_key, "the mutation must actually change the identity hash"

    proc_after = run_audit(root, "--check", "--particle-config", "fr.json")
    summary_after = parse_stdout(proc_after)
    assert_summary_schema_valid(summary_after)
    assert summary_after["totals"]["by_kind"]["homonym_split"] == 1, "still a genuine split (>=2 senses), category 5 still fires"
    assert summary_after["totals"]["missing_verdict"] == 1, "the OLD confirmed_ok must not satisfy the NEW key"
    assert summary_after["totals"]["orphaned_records"] == 1, "the stale record is reported as an orphan, not silently dropped"
    assert summary_after["blocking_count"] == 1 and proc_after.returncode == 1


def test_homonym_split_freshness_emptying_sidecar_orphans_the_verdict(tmp_path):
    """A related but distinct freshness case: dropping the split down to
    NOTHING (the whole sidecar entry removed, senses.is_empty) rather than
    merely editing it -- category 5 vanishes entirely, the old confirmed_ok
    becomes a pure orphan, and the gate is clean (nothing left to block) --
    a stronger statement than 'the old key no longer satisfies the current
    identity' since there IS no current identity to satisfy at all."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})

    write_senses(root, {})  # empty sidecar
    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 0
    assert summary["totals"]["orphaned_records"] == 1
    assert summary["blocking_count"] == 0 and proc.returncode == 0


# ===========================================================================
# 2. collapsed_split reconciliation
# ===========================================================================


def test_collapsed_split_blocks_and_never_masked_by_advisory(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    # Jean is ALSO present in canon.json as a bare single entry -- the reconciliation must
    # catch this even though a confirmed_ok is provided for the split item itself.
    write_canon(root, [entry("Jean", "Jean")])
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["collapsed_split"] == 1
    assert summary["blocking_count"] == 1 and proc.returncode == 1

    proc_adv = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    summary_adv = parse_stdout(proc_adv)
    assert_summary_schema_valid(summary_adv)
    assert proc_adv.returncode == 1, "--advisory must NEVER mask collapsed_split"


def test_collapsed_split_mismatched_map_key_still_caught(tmp_path):
    """R4 must-fix 4: a bare entry filed under an unrelated/legacy map key,
    whose own RECORD source_form field matches the split key, must still be
    caught -- a map-key-only comparison would falsely pass this."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon_raw(root, {
        "entries": {
            "legacy-jean-key-not-matching-source-form": {
                "source_form": "Jean", "canonical_target_form": "Jean",
                "is_proper_name": True, "basis": "transliterated",
            },
        },
        "review_queue": [],
        "generation_hashes": {"particle_config_hash": "x", "derivation_bundle_hash": "y"},
    })
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["collapsed_split"] == 1, "the record's own source_form field must be used, not the map key"
    assert proc.returncode == 1


# ===========================================================================
# 3. canon_absent_with_senses
# ===========================================================================


def test_canon_absent_with_senses_blocks_never_masked_by_advisory(tmp_path):
    """Case (b): canon absent + non-empty senses + GENUINELY VALID evidence
    -> canon_absent_with_senses:1 ALONE, evidence_unverified:0 -- evidence
    verification still runs in this branch (see the fix below), it just
    finds nothing wrong here. The split itself carries a confirmed_ok
    verdict (Finding 10: cat5 is now computed in this branch too) so it
    contributes a required item/confirmed_ok but stays out of blocking_count,
    keeping the 'ALONE' isolation this test is actually about."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})
    # No canon.json written at all.

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["totals"]["canon_absent_with_senses"] == 1
    assert summary["totals"]["evidence_unverified"] == 0
    assert summary["totals"]["by_kind"]["homonym_split"] == 1, "cat5 must be computed even without canon.json"
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["blocking_count"] == 1 and summary["gate_passed"] is False
    assert proc.returncode == 1

    proc_adv = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    assert proc_adv.returncode == 1, "--advisory must NEVER mask canon_absent_with_senses"


def test_canon_absent_with_senses_also_reports_evidence_unverified(tmp_path):
    """Case (a) -- reporting-completeness fix (codex round, verified live):
    canon absent + non-empty senses + a BAD manifest (evidence cannot
    verify) must fold evidence_unverified into THIS branch's totals and
    blocking_count too, not just report canon_absent_with_senses and stay
    silent about the evidence problem until the NEXT run (after canon.json
    is added back). The gate already blocked either way (unmaskable), so
    this was never a bypass -- just under-informative reporting. The split
    itself carries a confirmed_ok verdict (Finding 10) so this test's own
    dimension (evidence_unverified isolation) stays uncontaminated by cat5's
    own missing-verdict blocker."""
    root = make_durable_root(tmp_path)
    _block_text, senses = two_jean_senses()  # block_text unused -- see the comment below
    # Deliberately do NOT write manifest.json -- every sense's evidence fails to verify
    # (mirrors _read_manifest_for_evidence's own tolerant "missing manifest -> per-sense
    # failure" contract, see canon_adjudication_audit_evidence_matrix.test.py).
    write_senses(root, {"Jean": {"senses": senses}})
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})
    # No canon.json written at all.

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["totals"]["canon_absent_with_senses"] == 1
    assert summary["totals"]["evidence_unverified"] == 2, "both senses fail with no manifest to verify against"
    assert summary["totals"]["by_kind"]["homonym_split"] == 1, "cat5 must be computed even without canon.json"
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["blocking_count"] == 3, "canon_absent_with_senses(1) + evidence_unverified(2)"
    assert summary["gate_passed"] is False
    assert proc.returncode == 1

    proc_adv = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    assert proc_adv.returncode == 1, "--advisory must NEVER mask evidence_unverified either"


def test_canon_absent_with_senses_also_reports_cat5_missing_verdict(tmp_path):
    """Finding 10 (codex round-4, verified live): the canon-absent +
    non-empty-senses branch already ran verify_senses (evidence
    verification -- correctly, since it doesn't need canon.json) but never
    computed category 5 (homonym_split) at all, even though
    compute_cat5_items(senses, key_to_identity, warnings) takes ONLY senses
    -- no canon. So with no canon.json and an UN-adjudicated split, the
    summary used to silently report required_items:0 and
    by_kind.homonym_split:0, and the split's own missing_verdict blocker
    only appeared once canon.json was created -- the SAME reporting-
    completeness gap the evidence-verification fix above already closed for
    evidence_unverified, now closed for cat5 too."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    # No canon.json, no adjudications -- the split has no verdict at all.

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["totals"]["canon_absent_with_senses"] == 1
    assert summary["totals"]["by_kind"]["homonym_split"] == 1, "cat5 must be computed even without canon.json"
    assert summary["totals"]["required_items"] == 1
    assert summary["totals"]["missing_verdict"] == 1
    assert summary["totals"]["collapsed_split"] == 0, "collapsed_split genuinely needs canon.json and must stay 0/absent"
    assert summary["blocking_count"] == 2, "canon_absent_with_senses(1) + the split's own missing_verdict(1)"
    assert summary["gate_passed"] is False
    assert proc.returncode == 1

    proc_adv = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    assert proc_adv.returncode == 1, "--advisory must NEVER mask the split's own missing_verdict either"


def test_canon_absent_with_senses_cat5_confirmed_ok_not_a_blocker(tmp_path):
    """Companion to the above: a split WITH a confirmed_ok verdict is still
    counted as a required item (by_kind.homonym_split / totals.confirmed_ok),
    but does NOT add to blocking_count -- only canon_absent_with_senses
    itself blocks in that case."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})
    # No canon.json.

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 1
    assert summary["totals"]["confirmed_ok"] == 1
    assert summary["totals"]["missing_verdict"] == 0
    assert summary["blocking_count"] == 1, "canon_absent_with_senses(1) ALONE -- a confirmed_ok cat5 item is not a blocker"
    assert summary["gate_passed"] is False, "canon_absent_with_senses itself still blocks unconditionally"
    assert proc.returncode == 1


def test_canon_absent_with_empty_senses_is_still_the_ordinary_green(tmp_path):
    """Sanity-check the OTHER branch is untouched: an empty/absent sidecar
    with canon.json absent stays the pre-existing exit-0 green."""
    root = make_durable_root(tmp_path)
    proc = run_audit(root, "--check")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["canon_present"] is False
    assert summary["totals"]["canon_absent_with_senses"] == 0
    assert summary["blocking_count"] == 0 and summary["gate_passed"] is True
    assert proc.returncode == 0


# ===========================================================================
# 4. --advisory narrowed contract: masks categories 1-4 only
# ===========================================================================


def test_advisory_masks_categories_1_to_4_but_not_a_concurrent_split_blocker(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    # A category-1 duplicate_source_form (two Marie entries) PLUS the homonym_split above,
    # both missing a verdict.
    write_canon(root, [entry("Marie", "Marie-A"), entry("Marie", "Marie-B")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary["totals"]["by_kind"]["homonym_split"] == 1
    assert summary["blocking_count"] > 0 and summary["gate_passed"] is False
    assert proc.returncode == 1, "the split's own missing_verdict must keep exit 1 even under --advisory"

    # Now confirm the split -- category 1's duplicate_source_form is STILL missing a
    # verdict, but with the split cleared, --advisory should mask what remains (categories
    # 1-4 only), proving the narrowing is scoped, not blanket.
    write_adjudications(root, {split_key("Jean", senses): adjudication_record("confirmed_ok")})
    proc2 = run_audit(root, "--check", "--particle-config", "fr.json", "--advisory")
    summary2 = parse_stdout(proc2)
    assert_summary_schema_valid(summary2)
    assert summary2["totals"]["by_kind"]["duplicate_source_form"] == 1
    assert summary2["totals"]["missing_verdict"] >= 1, "category 1's own duplicate_source_form is still unresolved"
    assert summary2["blocking_count"] > 0 and summary2["gate_passed"] is False
    assert proc2.returncode == 0, "--advisory DOES mask a pure categories-1-4 blocking finding"

    # Without --advisory, the SAME state (split resolved, cat 1 still missing) exits 1.
    proc3 = run_audit(root, "--check", "--particle-config", "fr.json")
    assert proc3.returncode == 1


# ===========================================================================
# 5. --particle-config required/optional
# ===========================================================================


def test_particle_config_missing_with_nonempty_senses_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check")
    assert_fatal(proc)
    assert "particle-config" in proc.stderr


def test_particle_config_unresolvable_with_nonempty_senses_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "does-not-exist.json")
    assert_fatal(proc)
    # --particle-config is a VALID flag on both the base and the 1c/1e script -- assert the
    # actual resolution-failure message, not just "some exit 2", so this test cannot pass
    # vacuously against an unrelated argparse rejection (codex-rescue finding).
    assert "could not be resolved" in proc.stderr
    assert "does-not-exist.json" in proc.stderr


def test_particle_config_unused_and_optional_when_senses_empty(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    proc = run_audit(root, "--check")  # no --particle-config, no senses at all
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 0
    assert proc.returncode == 0


# ===========================================================================
# 6. --senses-path present-default policy (plan §10 / contract §10)
# ===========================================================================


def test_senses_path_no_flag_no_default_file_is_empty_ok(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    proc = run_audit(root, "--check")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 0
    assert proc.returncode == 0


def test_senses_path_explicit_missing_path_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    missing_path = root / "does-not-exist.json"
    proc = run_audit(root, "--check", "--senses-path", str(missing_path))
    assert_fatal(proc)
    # --senses-path is a VALID flag on both the base and the 1c/1e script -- assert
    # canon_senses.py::load_senses's own "not found" message + the actual path, not just
    # "some exit 2", so this test cannot pass vacuously against an unrelated argparse
    # rejection (codex-rescue finding).
    assert "canon_senses.json not found" in proc.stderr
    assert str(missing_path) in proc.stderr


def test_senses_path_present_implicit_default_drives_split_audit(tmp_path):
    """R8-F1: NO --senses-path override, but a PRESENT valid nonempty default
    canon_senses.json -- the audit must actually load it and run category 5 /
    collapsed_split, not silently treat 'no flag' as empty."""
    root = make_durable_root(tmp_path)
    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})  # written at the DEFAULT path
    write_canon(root, [entry("Jean", "Jean")])  # also a collapsed bare entry

    proc = run_audit(root, "--check", "--particle-config", "fr.json")  # no --senses-path
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["homonym_split"] == 1, "the PRESENT default sidecar must be loaded without an explicit flag"
    assert summary["totals"]["collapsed_split"] == 1
    assert proc.returncode == 1


def test_senses_path_malformed_default_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    (root / "canon_senses.json").write_text("{not valid json", encoding="utf-8")
    proc = run_audit(root, "--check")
    assert_fatal(proc)


def test_senses_path_non_regular_default_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    (root / "canon_senses.json").mkdir()  # a directory, not a regular file
    proc = run_audit(root, "--check")
    assert_fatal(proc)


def test_senses_path_schema_invalid_default_one_sense_record_is_fatal(tmp_path):
    """load_senses schema-validates BEFORE any emptiness decision -- a
    1-sense record (minItems:2) is a hard load error, never 'not split'."""
    root = make_durable_root(tmp_path)
    write_canon(root, [entry("Marie", "Marie")])
    write_senses_raw(root, {
        "schema_version": 1,
        "entries_by_source_form": {
            "Jean": {"senses": [
                {"sense_id": "s1", "disambiguator": "only one sense", "index_scope": "narrative",
                 "evidence": {"block": "b1", "seg": None, "char_start": 0, "char_end": 4,
                              "context_start": 0, "context_end": 4, "sha256": "x"}},
            ]},
        },
    })
    proc = run_audit(root, "--check")
    assert_fatal(proc)


# ===========================================================================
# #243 competitor universe -- the canon-PRESENT branch of run_check() must
# widen verify_senses()'s competitor universe with canon.json's own entries,
# exactly like the canon-ABSENT branch already (correctly) omits it because
# there is nothing to pass. A senses form colliding with a canon-ONLY
# sibling (never itself present in canon_senses.json) must not read as
# unique and get full evidence credit.
# ===========================================================================

# The real occ_index.test.py collision pair (space-joined/unvocalized vs
# maqaf-joined/vocalized Baal Shem Tov-style forms) -- both fold to the same
# bootstrap_names.fold_match_key. FOLD_FORM_B_HE is used ONLY as a canon-only
# sibling below -- it never appears in canon_senses.json at all.
FOLD_FORM_A_HE = "משה לייב"
FOLD_FORM_B_HE = "מֹשֶׁה־לַיִיב"


def write_he_language_with_inventory(root, name_inventory):
    """he.json is already staged into root/languages by make_durable_root --
    add a name_inventory on top (Hebrew is an uncased script, so
    is_upper_initial() can never see a candidate without one; mirrors
    occ_index.test.py/suspicion_scan.test.py's own convention)."""
    he_path = root / "languages" / "he.json"
    doc = json.loads(he_path.read_text(encoding="utf-8"))
    doc["name_inventory"] = list(name_inventory)
    he_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_243_canon_present_branch_widens_competitor_universe_to_canon_entries(tmp_path):
    """BLOCKER regression (codex review): run_check()'s canon-PRESENT branch
    called ``verify_senses(senses, manifest, language_config)`` with NO
    ``canon=`` argument, so its own #243 competitor universe was
    senses-forms-only. FOLD_FORM_B_HE here is canon-ONLY (never in
    canon_senses.json) -- a senses-only universe can never see it, so
    FOLD_FORM_A_HE's evidence would wrongly verify clean. Passing
    ``canon=canon`` (the fix) must catch the collision and fail both of its
    senses' evidence -- the SAME failure the unit-level
    ``test_243_verify_senses_canon_param_widens_the_competitor_universe``
    (evidence_verify.test.py) already proves at the function level; this is
    the audit-level proof that the CALL SITE actually wires it."""
    root = make_durable_root(tmp_path)
    write_he_language_with_inventory(root, [FOLD_FORM_A_HE])
    he_lang = bn.load_language_config("he.json", languages_dir=root / "languages")

    block_text = f"ראה {FOLD_FORM_A_HE} אתמול."
    spans = oi.production_occurrences(FOLD_FORM_A_HE, block_text, he_lang)
    assert len(spans) == 1, f"expected exactly 1 occurrence, got {spans}"
    char_start, char_end = spans[0]
    senses = [
        make_sense("s1", "sense A", block_text, char_start, char_end),
        make_sense("s2", "sense B", block_text, char_start, char_end),
    ]
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {FOLD_FORM_A_HE: {"senses": senses}})
    # FOLD_FORM_B_HE is a plain, unrelated canon entry -- never appears in
    # canon_senses.json, so it is invisible to a senses-only competitor
    # universe. It fold-collides with FOLD_FORM_A_HE.
    write_canon(root, [entry(FOLD_FORM_B_HE, "Target")])
    # codex round 2: confirmed_ok the homonym_split identity itself so
    # missing_verdict (category 5, its OWN independent -- and already
    # separately tested -- advisory-immune blocker) contributes NOTHING to
    # blocking_count here. Without this, blocking_count/gate_passed/
    # --advisory staying non-zero/False would be confounded: they would
    # pass for the unadjudicated-split reason alone even if evidence_
    # unverified's own contribution (and its own advisory-immunity) were
    # broken, since compute_collapsed_split_findings/verify_senses are
    # never gated by any adjudication verdict either way -- this pins the
    # count to evidence_unverified specifically, nothing else.
    write_adjudications(root, {split_key(FOLD_FORM_A_HE, senses): adjudication_record("confirmed_ok")})

    proc = run_audit(root, "--check", "--particle-config", "he.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    # Mutation: verify_senses() called without canon=canon (the bug this
    # test guards) finds no local collision within senses_result alone --
    # FOLD_FORM_A_HE has no sibling there -- and both senses verify clean,
    # wrongly reporting 0 here instead of 2.
    assert summary["totals"]["evidence_unverified"] == 2, (
        f"canon-only sibling {FOLD_FORM_B_HE!r} must poison BOTH of "
        f"{FOLD_FORM_A_HE!r}'s senses' evidence verification -- got summary={summary}"
    )
    assert summary["totals"]["missing_verdict"] == 0 and summary["totals"]["confirmed_ok"] == 1, (
        "the split itself is confirmed_ok -- blocking_count below must come "
        "from evidence_unverified alone, not a second, unrelated source"
    )
    assert summary["blocking_count"] == 2 and summary["gate_passed"] is False
    # evidence_unverified is a hard blocker, never masked by --advisory.
    proc_advisory = run_audit(root, "--check", "--particle-config", "he.json", "--advisory")
    summary_advisory = parse_stdout(proc_advisory)
    assert summary_advisory["totals"]["evidence_unverified"] == 2
    assert summary_advisory["gate_passed"] is False
    assert proc.returncode == 1 and proc_advisory.returncode == 1
