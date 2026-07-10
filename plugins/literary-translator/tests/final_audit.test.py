"""tests/final_audit.test.py -- regression-lock / integration suite for
scripts/final_audit.py, the W7 final audit gate (see that script's own
module docstring and SKILL.md's "W7 Final audit" section for the
authoritative spec).

## Fixture strategy

Every test below builds a REAL, self-contained ``durable_root`` on disk and
invokes the ACTUAL ``final_audit.py`` as a subprocess -- exactly the way it
is invoked in production (``python3 {durable_root}/scripts/final_audit.py``)
-- so its ``Path(__file__)``-based self-anchoring resolves against the
isolated fixture root. final_audit.py's own hard checks import
``validate_draft.py``/``bootstrap_names.py`` directly (in-process, via
``sys.path.insert``) and its whole-project completeness gate shells out to
the REAL ``select_segments.py`` -> ``ledger_merge.py`` -> ``cache_key.py``
chain -- ALL of these are REAL copies of the actual shipped scripts, never
stubs. This is deliberate: `select_segments.py`/`ledger_merge.py`/
`cache_key.py`'s own internal classification/hashing correctness is each
covered by its OWN dedicated test file (`select_segments.test.py`,
`ledger_merge.test.py`, `ledger_composite_key.test.py`) -- what this file
proves is that `final_audit.py` correctly INTEGRATES with the real,
currently-shipped behavior of those scripts, not a hand-maintained stand-in
that could quietly drift from the real contract.

Every ``cache_key`` recorded in a ledger fragment fixture is computed by
actually invoking ``cache_key.py --seg <id>`` at fixture-build time (never
hand-typed), so a fragment's recorded cache_key is always self-consistent
with whatever the current durable_root's profile/segpack/scripts actually
hash to.

## Two known, confirmed integration bugs (see the dedicated tests below)

Building this fixture chain for real (rather than stubbing select_segments.py)
surfaced two genuine, confirmed bugs in final_audit.py itself. Per this
project's own testing discipline (see e.g. schema_literal_drift.test.py /
prompt_contract_drift.test.py, which exist specifically to catch this class
of issue), these are reported here as failing, un-weakened assertions of the
CORRECT documented behavior -- not worked around or hidden.

1. **The ``--allow-empty`` bug.** final_audit.py's whole-project completeness
   gate invokes ``select_segments.py`` with NO arguments at all. But
   ``select_segments.py``'s own documented, BY-DESIGN default behavior (see
   ``select_segments.test.py::test_default_run_fatals_on_empty_segs_unless_allow_empty``)
   is to FATAL (``success: false``, exit 1) whenever its emitted ``SEGS`` list
   comes up empty, unless ``--allow-empty`` is passed -- a guard against a
   silently-no-op W5 mass-translate DISPATCH batch. ``SEGS`` is empty
   precisely when every manifest segment is already classified ``reusable``
   -- i.e. *exactly* the fully-converged project state W7's completeness
   gate exists to report as ``project_complete: true``. Because
   ``final_audit.py`` never passes ``--allow-empty``, that one case crashes
   at exit 2 (``run_completeness_gate()``'s own ``_fatal()``) before any JSON
   summary is ever printed -- the exact opposite of the documented contract.
   Most tests below sidestep this pre-existing bug with an inert, always-
   ``not_started`` padding segment (see ``PAD_SEG``) so they can exercise the
   checks they actually target; the dedicated test near the bottom asserts
   the CORRECT, documented behavior directly (no padding) and is expected to
   currently FAIL against the real script, with the failure message spelling
   out the bug and its one-line fix.

2. **The frontback ``status`` shape bug.** ``build_frontback_coverage()``
   assigns ``status = classification_by_seg.get(fb_id)`` directly for a
   ``translate``-decision entry. But ``classification_by_seg`` is
   ``select_segments.py``'s own ``classification`` map, whose per-segment
   VALUE is itself a dict (e.g. ``{"category": "reusable"}`` or
   ``{"category": "stale", "stale_reason": [...]}"``), never a bare string --
   see select_segments.py's own module docstring: ``"classification":
   {seg: {...}}``. final_audit.py never extracts ``.get("category")`` from
   it, so ``status`` ends up as the whole nested dict, directly violating
   final-audit-summary.schema.json's own requirement that
   ``frontback_coverage[].status`` be a plain string (or null) -- confirmed
   empirically: ``test_frontback_coverage_translate_vs_regenerate_omit``
   below currently fails schema validation with ``status`` holding
   ``{'category': 'reusable'}`` instead of ``'reusable'``. Fix: ``status =
   classification_by_seg.get(fb_id, {}).get("category")``.

Coverage (per the test's own enumeration):
  - hard check 1 (coverage_failures) via a real re-invocation of
    validate_draft.py, isolated from hard check 2;
  - hard check 2 (stale_review_failures) via a current-draft-sha1 vs.
    ledger-fragment reviewed_draft_sha1 mismatch, isolated from hard check 1;
  - the hard_failures rollup invariant across two segments, one failing each
    hard check;
  - all four WARN-only advisory checks (glossary-diff name-form drift +
    canon.json self-consistency, link-graph sentinel bijection,
    foreign-remainder stopword-density scan, verse-structure per
    verse_policy.mode);
  - the whole-project completeness gate, both directions (incomplete when a
    not_started segment exists; the documented-but-currently-broken
    "complete" case);
  - the frontback coverage report (translate-decision cross-referenced to
    segment classification; regenerate/omit reported by decision alone).
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)
SCHEMAS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
)

FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
assert FINAL_AUDIT_SRC.is_file(), f"final_audit.py not found at {FINAL_AUDIT_SRC}"

# Every real script final_audit.py depends on, directly or transitively
# (imported in-process, or shelled out to via the completeness gate).
SCRIPTS_TO_COPY = (
    "final_audit.py",
    "validate_draft.py",
    "bootstrap_names.py",
    "select_segments.py",
    "ledger_merge.py",
    "cache_key.py",
)
for _name in SCRIPTS_TO_COPY:
    assert (SCRIPTS_SRC_DIR / _name).is_file(), f"{_name} not found at {SCRIPTS_SRC_DIR}"

FINAL_AUDIT_SUMMARY_SCHEMA = json.loads(
    (SCHEMAS_SRC_DIR / "final-audit-summary.schema.json").read_text(encoding="utf-8")
)

FN_PH = "⟦FNREF_1⟧"
V_PH_A = "⟦VERSE_vA⟧"
V_PH_B = "⟦VERSE_vB⟧"

# A manifest segment that is deliberately NEVER given a segpack/draft/ledger
# fragment -- classifies "not_started". Included in most fixtures below
# purely to keep select_segments.py's emitted SEGS non-empty, sidestepping
# the pre-existing --allow-empty integration bug documented at the top of
# this file and in the dedicated test at the bottom, so the OTHER checks
# under test can be exercised without that unrelated crash. Its presence
# means such tests do NOT assert project_complete (it is always False in
# those fixtures because of this pad) -- project_complete itself is covered
# by its own dedicated tests below.
PAD_SEG = "zz_not_started_pad"

DEFAULT_STOPWORDS = ["de", "la", "le", "et", "un", "une", "des", "du", "les", "dans"]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile(particle_config="fr_test.json", verse_mode="full_rhymed_plus_literal"):
    return {
        "project": {"pipeline_version": "v1"},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "source": {
            "format": "plain_text",
            "path": "/logical/source.txt",
            "language": {"code": "fr", "particle_config": particle_config},
            "adapter_config": {
                "plain_text": {
                    "segmentation": {"method": "blank_line_run", "blank_line_threshold": 2}
                },
                "gutenberg_epub": {},
                "custom": {},
            },
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": verse_mode, "threshold_lines": None},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }


def make_durable_root(
    tmp_path,
    seg_ids=("seg01",),
    frontback=None,
    verse_mode="full_rhymed_plus_literal",
    stopwords=None,
    canon=None,
) -> Path:
    """Build a COMPLETE, internally-consistent durable_root: real copies of
    every script final_audit.py touches, real schemas/ (ledger_merge.py
    genuinely validates against these via jsonschema), a full profile.yml
    satisfying both validate_draft.py's and cache_key.py's own required
    fields, a resolved particle_config, and a minimal manifest.json/canon.json.
    No segment content is written here -- call add_converged_segment() per
    segment, or leave a manifest id untouched for a genuine not_started case.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in SCRIPTS_TO_COPY:
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    # cache_key.py's derivation_bundle_hash hashes this file's raw bytes
    # alongside bootstrap_names.py -- content is irrelevant, only needs to
    # exist (segpack.py itself is never imported by anything in this chain).
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py fixture placeholder\n")

    (root / "profile.yml").write_text(
        yaml.safe_dump(default_profile(verse_mode=verse_mode), sort_keys=False),
        encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")

    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr_test.json").write_text(
        json.dumps(
            {
                "PARTICLES": ["de", "du", "des"],
                "STOPWORDS": sorted(stopwords if stopwords is not None else DEFAULT_STOPWORDS),
                "has_elision": False,
                "ELISION_RE": None,
            }
        ),
        encoding="utf-8",
    )

    source_file = root / "source_original.txt"
    source_file.write_bytes(b"Ceci est un texte source de test.\n")

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "source_inputs": [str(source_file.resolve())],
                "segments": [{"seg": s} for s in seg_ids],
                "frontback": frontback or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (root / "canon.json").write_text(
        json.dumps(canon if canon is not None else {"entries": {}}, ensure_ascii=False),
        encoding="utf-8",
    )

    runs_dir = root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text("test-plugin-bundle-marker-v1\n", encoding="utf-8")

    (root / "segments").mkdir()

    return root


def clean_segpack(seg="seg01", extra_footnotes=None, vblockA_source="<p>Premiere ligne du poeme<br/>Deuxieme ligne du poeme</p>"):
    footnotes = [{"n": 1, "source_text": "Une note en francais."}]
    if extra_footnotes:
        footnotes.extend(extra_footnotes)
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1, "source_html": vblockA_source},
            {"id": "vblockB", "order_index": 2, "source_html": "<p>Autre premiere ligne<br/>Autre deuxieme ligne</p>"},
        ],
        "footnotes": footnotes,
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
    }


def clean_draft(seg="seg01", p1_text=None, extra_footnotes=None, names=None):
    footnotes = {"1": "A translated note in English."}
    if extra_footnotes:
        footnotes.update(extra_footnotes)
    return {
        "seg": seg,
        "blocks": {
            "p1": p1_text if p1_text is not None else f"Some translated prose with a note {FN_PH} attached.",
            "vblockA": V_PH_A,
            "vblockB": V_PH_B,
        },
        "footnotes": footnotes,
        "verses": {
            "vA": {
                "rendered": "First line rendered so\nSecond line rendered so",
                "literal_gloss": (
                    "The first line means one thing, the second line means "
                    "another thing entirely"
                ),
            },
            "vB": {
                "rendered": "Another line rendered here\nAnother second line here",
                "literal_gloss": (
                    "This gloss says something completely different from "
                    "the rendering above"
                ),
            },
        },
        "names": names or [],
        "notes": [],
    }


def draft_content_sha1_of(doc: dict) -> str:
    """1.2.0: ledger_update.py/draft_sha1.py/final_audit.py all hash a
    segment draft's CONTENT, not its raw on-disk bytes -- CONTRACT-1.2.0-
    reliability.md section 2. Independent, stdlib-only ground truth (drop
    'dispatch_token' if present, sha1 the sorted-key canonical
    re-serialization); duplicated here rather than imported, matching this
    suite's "each test file stays self-contained" convention (see
    tests/draft_sha1.test.py's own canonical_expected_sha1() for the
    more-exhaustively-tested original)."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def add_converged_segment(
    root: Path, seg: str, segpack: dict, draft: dict, reviewed_sha1_override=None, rounds=1,
    raw_draft_bytes: bytes | None = None,
) -> dict:
    """Writes segments/segpack_{seg}.json + {seg}.draft.json, computes the
    REAL 15-field cache_key by actually invoking cache_key.py --seg <seg>
    (never hand-typed), and writes a schema-shaped converged ledger fragment
    to runs/ledger.d/{seg}.json. Returns the computed cache_key dict.

    reviewed_sha1_override, when given, deliberately records a WRONG
    reviewed_draft_sha1 (simulating a hand-edit after the review that
    approved the draft) -- the sole mechanism these tests use to trigger
    hard check 2 (stale_review_failures) in isolation from hard check 1.

    raw_draft_bytes, when given, writes these EXACT bytes to disk instead of
    a canonical serialization of `draft` -- for exercising a deliberately
    non-canonical on-disk draft (unsorted keys, pretty-printed, a
    dispatch_token field present) while reviewed_draft_sha1 is still
    computed from `draft`'s own canonical content hash, matching what a
    real ledger_update.py write records. `raw_draft_bytes` must decode to
    content equivalent to `draft` (minus any dispatch_token) or the fixture
    is internally inconsistent.
    """
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    if raw_draft_bytes is not None:
        draft_bytes = raw_draft_bytes
    else:
        # Canonical (sorted keys, compact separators) -- not load-bearing for
        # correctness (reviewed_draft_sha1 below is computed from `draft`
        # itself via draft_content_sha1_of, independent of how these bytes
        # are serialized), just keeps the on-disk fixture tidy by default.
        draft_bytes = json.dumps(
            draft, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    (segments_dir / f"{seg}.draft.json").write_bytes(draft_bytes)

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    cache_key = json.loads(proc.stdout)

    # The exact algorithm ledger_update.py/final_audit.py/assemble.py all use
    # in production -- NOT a raw-bytes hash of draft_bytes above -- so this
    # stays correct regardless of how the on-disk file happens to be
    # serialized (see raw_draft_bytes above).
    reviewed_sha1 = (
        reviewed_sha1_override
        if reviewed_sha1_override is not None
        else draft_content_sha1_of(draft)
    )

    fragment = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": len(segpack.get("blocks", [])),
        "n_footnotes": len(segpack.get("footnotes", [])),
        "n_verses": len(segpack.get("verses", [])),
        "reviewed_draft_sha1": reviewed_sha1,
    }
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / f"{seg}.json").write_text(json.dumps(fragment, ensure_ascii=False), encoding="utf-8")

    return cache_key


def run_final_audit(root: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "final_audit.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_summary(proc: subprocess.CompletedProcess) -> dict:
    assert proc.stdout.strip(), (
        f"expected final_audit.py to print exactly one JSON line to stdout, "
        f"got nothing. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}"
    return json.loads(lines[0])


def assert_schema_valid(summary: dict) -> None:
    jsonschema.validate(instance=summary, schema=FINAL_AUDIT_SUMMARY_SCHEMA)


# ---------------------------------------------------------------------------
# 1. Clean baseline: hard checks AND all four WARN checks clean.
# ---------------------------------------------------------------------------


def test_clean_project_all_checks_pass(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"expected a clean converged segment to pass (exit 0), got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["coverage_failures"] == 0
    assert summary["stale_review_failures"] == 0
    assert summary["hard_failures"] == 0
    assert summary["warnings"] == 0
    assert "HARD (coverage=0, stale_review=0): CLEAN" in result.stderr
    assert "WARN / MANUAL-REVIEW (0):" in result.stderr


# ---------------------------------------------------------------------------
# 2. Hard check 1 (coverage_failures), isolated from hard check 2: the
#    fragment's reviewed_draft_sha1 is left to auto-match the CURRENT
#    (already-defective) on-disk draft, so the stale-review check sees no
#    mismatch -- the coverage defect is the sole cause of the failure.
# ---------------------------------------------------------------------------


def test_coverage_failure_isolated_from_stale_review(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    draft = clean_draft()
    draft["footnotes"]["1"] = ""  # injected defect: blanked footnote translation
    add_converged_segment(root, "seg01", clean_segpack(), draft)  # sha1 auto-matches

    result = run_final_audit(root)

    assert result.returncode == 1, (
        f"a coverage defect on a converged segment must fail the gate, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["coverage_failures"] == 1
    assert summary["stale_review_failures"] == 0
    assert summary["hard_failures"] == 1
    assert "[seg01] COVERAGE [FN:1] empty translation" in result.stderr


# ---------------------------------------------------------------------------
# 3. Hard check 2 (stale_review_failures), isolated from hard check 1: the
#    draft itself is fully valid (passes validate_draft.py cleanly), but the
#    ledger fragment's own reviewed_draft_sha1 deliberately does not match
#    the current on-disk draft's sha1 -- simulating a hand-edit that stayed
#    structurally valid but substituted prose the reviewer never saw.
# ---------------------------------------------------------------------------


def test_stale_review_failure_isolated_from_coverage(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
    # Same canonical serialization add_converged_segment writes to disk with,
    # so actual_sha1 below matches the real on-disk file byte for byte.
    draft_bytes = json.dumps(
        clean_draft(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    wrong_sha1 = hashlib.sha1(draft_bytes + b"tamper").hexdigest()
    add_converged_segment(
        root, "seg01", clean_segpack(), clean_draft(), reviewed_sha1_override=wrong_sha1
    )
    # No PAD_SEG needed: a draft_sha1 mismatch classifies this segment
    # "stale" (in DEFAULT_ELIGIBLE_CATEGORIES), so select_segments.py's
    # emitted SEGS is non-empty on its own.

    result = run_final_audit(root)

    assert result.returncode == 1, (
        f"a stale-review mismatch on a converged segment must fail the "
        f"gate, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["coverage_failures"] == 0
    assert summary["stale_review_failures"] == 1
    assert summary["hard_failures"] == 1
    actual_sha1 = hashlib.sha1(draft_bytes).hexdigest()
    assert (
        f"[seg01] STALE-REVIEW current draft sha1 {actual_sha1} != "
        f"reviewed_draft_sha1 {wrong_sha1}" in result.stderr
    )


def test_stale_review_survives_non_canonical_draft_bytes(tmp_path):
    """Companion to test_stale_review_failure_isolated_from_coverage above:
    the OPPOSITE case must NOT false-positive. A converged segment's on-disk
    draft is deliberately NON-canonical (keys in human-authored,
    non-alphabetical order -- clean_draft()'s own natural key order --
    pretty-printed with indentation, and a 'dispatch_token' metadata field
    present) -- exactly what a real draft looks like on disk, never the
    compact sorted-key form draft_content_sha1() re-serializes to.
    final_audit.py's hard check 2 must NOT flag this stale: its own freshly
    recomputed draft-content-sha1 must equal the ledger's
    reviewed_draft_sha1 (itself recorded via the very same canonical
    draft_content_sha1() algorithm ledger_update.py uses in production). A
    regression back to a raw-bytes hash in final_audit.py would
    misclassify this as a stale_review_failures hard failure even though
    nothing about the draft actually changed since review.
    """
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    draft = clean_draft()
    raw_draft_bytes = json.dumps(
        {"dispatch_token": "some-run-token:seg01", **draft}, indent=2, ensure_ascii=False
    ).encode("utf-8")
    add_converged_segment(root, "seg01", clean_segpack(), draft, raw_draft_bytes=raw_draft_bytes)

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"a non-canonical but otherwise unchanged draft must not trip a "
        f"false stale-review failure:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["coverage_failures"] == 0
    assert summary["stale_review_failures"] == 0
    assert summary["hard_failures"] == 0


# ---------------------------------------------------------------------------
# 4. Rollup invariant: hard_failures == coverage_failures +
#    stale_review_failures, exercised across TWO segments each failing a
#    DIFFERENT one of the two hard checks.
# ---------------------------------------------------------------------------


def test_hard_failures_rollup_equals_sum_across_two_segments(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))

    coverage_draft = clean_draft(seg="seg01")
    coverage_draft["footnotes"]["1"] = ""  # coverage defect only
    add_converged_segment(root, "seg01", clean_segpack(seg="seg01"), coverage_draft)

    stale_draft_bytes = json.dumps(clean_draft(seg="seg02"), ensure_ascii=False).encode("utf-8")
    wrong_sha1 = hashlib.sha1(stale_draft_bytes + b"tamper").hexdigest()
    add_converged_segment(
        root,
        "seg02",
        clean_segpack(seg="seg02"),
        clean_draft(seg="seg02"),
        reviewed_sha1_override=wrong_sha1,
    )

    result = run_final_audit(root)

    assert result.returncode == 1
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["coverage_failures"] == 1
    assert summary["stale_review_failures"] == 1
    assert summary["hard_failures"] == 2
    assert summary["hard_failures"] == summary["coverage_failures"] + summary["stale_review_failures"]


# ---------------------------------------------------------------------------
# 5. WARN: glossary-diff -- cross-segment source_form -> target_form drift.
# ---------------------------------------------------------------------------


def test_warn_glossary_diff_cross_segment_drift(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02", PAD_SEG))
    add_converged_segment(
        root, "seg01", clean_segpack(seg="seg01"),
        clean_draft(seg="seg01", names=[{"source_form": "Jean", "target_form": "John"}]),
    )
    add_converged_segment(
        root, "seg02", clean_segpack(seg="seg02"),
        clean_draft(seg="seg02", names=[{"source_form": "Jean", "target_form": "Zhan"}]),
    )

    result = run_final_audit(root)

    assert result.returncode == 0, f"WARN checks must not gate exit code:\n{result.stderr}"
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    assert (
        "GLOSSARY-DIFF source_form 'Jean': 2 distinct target forms across segments"
        in result.stderr
    )


def test_warn_glossary_diff_canon_self_inconsistency(tmp_path):
    canon = {
        "entries": {
            "Jean_A": {
                "source_form": "Jean", "canonical_target_form": "John",
                "is_proper_name": True, "basis": "transliterated", "confidence": "high",
            },
            "Jean_B": {
                "source_form": "Jean", "canonical_target_form": "Zhan",
                "is_proper_name": True, "basis": "transliterated", "confidence": "high",
            },
        }
    }
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG), canon=canon)
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    assert (
        "GLOSSARY-DIFF canon.json self-inconsistent: source_form 'Jean' -> ['John', 'Zhan']"
        in result.stderr
    )


# ---------------------------------------------------------------------------
# 6. WARN: link-graph -- a footnote defined in both segpack and draft but
#    never referenced by any ⟦FNREF_N⟧ anywhere in the draft is an orphan.
#    validate_draft.py's own key-set check does not care whether a footnote
#    is ever anchored anywhere, only that the key sets match -- so this is
#    clean under hard check 1 and exists solely as a WARN.
# ---------------------------------------------------------------------------


def test_warn_link_graph_orphan_footnote(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    segpack = clean_segpack(extra_footnotes=[{"n": 2, "source_text": "Une autre note."}])
    draft = clean_draft(extra_footnotes={"2": "Another translated note, never anchored."})
    add_converged_segment(root, "seg01", segpack, draft)

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    assert (
        "[seg01] LINK-GRAPH orphan footnote 2: no ⟦FNREF_2⟧ referenced "
        "anywhere in this draft -- MANUAL" in result.stderr
    )


# ---------------------------------------------------------------------------
# 7. WARN: foreign-remainder -- a run of source-language stopwords in a
#    translated block, using the resolved language preset's own STOPWORDS.
# ---------------------------------------------------------------------------


def test_warn_foreign_remainder_stopword_run(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    p1_text = f"Some translated prose with a note {FN_PH} attached. Voici de la le texte."
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft(p1_text=p1_text))

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    assert (
        "[seg01] FOREIGN-REMNANT possible untranslated source-language text "
        "in p1: stopword_hits=3 longest_run=3" in result.stderr
    )


# ---------------------------------------------------------------------------
# 8. WARN: verse-structure -- a verse's own parent block carries NO source
#    text at all in the segpack, so a citation of the original would be
#    empty. Independent of validate_draft.py's own checks (which never look
#    at whether the SOURCE text is present, only the draft's own coverage).
# ---------------------------------------------------------------------------


def test_warn_verse_structure_missing_source_text(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG))
    segpack = clean_segpack(vblockA_source="")  # injected defect: no source text at all
    add_converged_segment(root, "seg01", segpack, clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    assert (
        "[seg01] VERSE-STRUCTURE verse vA: segpack has NO original source "
        "text for parent block 'vblockA' (a citation of the original would "
        "be empty)" in result.stderr
    )


# ---------------------------------------------------------------------------
# 9. WARN: verse-structure -- paste/duplicate detection: two distinct,
#    non-empty string fields on the same verse entry identical up to
#    whitespace. Uses verse_policy.mode=skip specifically so
#    validate_draft.py's OWN distinctness check (which only applies under
#    full_rhymed_plus_literal) never fires -- isolating this as a pure WARN,
#    mode-agnostic per final_audit.py's own design.
# ---------------------------------------------------------------------------


def test_warn_verse_structure_paste_duplicate_field(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG), verse_mode="skip")
    segpack = {
        "seg": "seg01",
        "blocks": [{"id": "vblockA", "order_index": 0, "source_html": "Ligne un\nLigne deux"}],
        "footnotes": [],
        "verses": [{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
        "names": [], "canon_names": [], "new_names": [],
    }
    draft = {
        "seg": "seg01",
        "blocks": {"vblockA": V_PH_A},
        "footnotes": {},
        "verses": {
            "vA": {
                "rendered": "Line one here\nLine two here",
                "literal_gloss": "Line one here    Line two here",  # identical up to whitespace
            }
        },
        "names": [], "notes": [],
    }
    add_converged_segment(root, "seg01", segpack, draft)

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"verse_policy.mode=skip must exempt content checks in validate_draft.py -- "
        f"a hard failure here means this fixture's isolation assumption broke:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] >= 1
    # warn_verse_structure() reports fields in the on-disk draft's own key
    # order (whichever field it meets second names the first as its match).
    # add_converged_segment() now writes the draft as canonical JSON (sorted
    # keys), so 'literal_gloss' precedes 'rendered' on disk regardless of
    # this dict literal's own key order above -- the message names 'rendered'
    # (met second) as matching the already-seen 'literal_gloss'.
    assert (
        "[seg01] VERSE-STRUCTURE verse vA: field 'rendered' == field "
        "'literal_gloss' up to whitespace (paste/duplicate -- need genuinely "
        "distinct content)" in result.stderr
    )


# ---------------------------------------------------------------------------
# 10. Whole-project completeness gate, incomplete direction: a genuinely
#     not_started segment (no fragment at all) keeps project_complete false,
#     with completeness_counts naming exactly which category it fell into.
#     This is the SAME real select_segments.py -> ledger_merge.py ->
#     cache_key.py chain final_audit.py invokes in production.
# ---------------------------------------------------------------------------


def test_completeness_gate_project_incomplete_when_not_started_present(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    # seg02 deliberately gets no segpack/draft/ledger fragment at all.

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"an incomplete project is not itself a hard failure -- only "
        f"seg01 (clean) is converged, so hard checks must stay clean:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"] == {
        "not_started": 1,
        "recoverable": 0,
        "stale": 0,
        "blocked_needs_regeneration": 0,
        "human_escalation": 0,
    }
    assert "WHOLE-PROJECT COMPLETENESS: INCOMPLETE" in result.stderr


# ---------------------------------------------------------------------------
# 11. Frontback coverage report: a translate-decision entry cross-references
#     the SAME select_segments.py classification computed for the
#     completeness gate; regenerate/omit entries are reported by decision
#     alone (status is always null for them, regardless of any segment
#     state).
#
#     CONFIRMED BUG (see this file's module docstring, bug #2): asserts the
#     CORRECT, schema-documented shape -- status is the plain classification
#     CATEGORY STRING ("reusable") for a translate-decision entry. This
#     currently FAILS: final_audit.py's build_frontback_coverage() stores
#     select_segments.py's whole per-segment classification DICT
#     (e.g. {"category": "reusable"}) as status instead of extracting
#     .get("category"), which both the schema-validation assertion just
#     above and the literal list-equality assertion below catch.
# ---------------------------------------------------------------------------


def test_frontback_coverage_translate_vs_regenerate_omit(tmp_path):
    frontback = [
        {"id": "seg01", "decision": "translate"},
        {"id": "FRONTBACK:cover", "decision": "regenerate"},
        {"id": "FRONTBACK:toc", "decision": "omit"},
    ]
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG), frontback=frontback)
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    # assert_schema_valid() alone already raises jsonschema.ValidationError
    # here, naming the offending 'status' value -- see this file's module
    # docstring (bug #2) and the test's own docstring above for the full
    # explanation and one-line fix.
    assert_schema_valid(summary)
    assert summary["frontback_coverage"] == [
        {"id": "seg01", "decision": "translate", "status": "reusable"},
        {"id": "FRONTBACK:cover", "decision": "regenerate", "status": None},
        {"id": "FRONTBACK:toc", "decision": "omit", "status": None},
    ], (
        "CONFIRMED BUG in final_audit.py's build_frontback_coverage(): "
        "'status' for a translate-decision entry must be the plain "
        "classification category STRING (e.g. 'reusable'), per "
        "final-audit-summary.schema.json -- but the real script stores "
        "select_segments.py's whole per-segment classification DICT "
        "verbatim instead of extracting .get('category'). Fix: status = "
        "classification_by_seg.get(fb_id, {}).get('category').\n"
        f"actual frontback_coverage: {summary.get('frontback_coverage')!r}"
    )


# ---------------------------------------------------------------------------
# 12. Whole-project completeness gate, COMPLETE direction -- documents a
#     CONFIRMED, currently-live integration bug (see this file's module
#     docstring). This asserts the CORRECT, spec-documented behavior and is
#     expected to presently FAIL against final_audit.py as shipped.
# ---------------------------------------------------------------------------


def test_completeness_gate_reports_project_complete_true_when_all_reusable(tmp_path):
    # Deliberately NO padding segment: every manifest segment (just seg01)
    # is converged and fully matching -> select_segments.py classifies it
    # "reusable" -> its own default emitted SEGS is EMPTY -- exactly the
    # fully-converged project state final_audit.py's completeness gate
    # exists to report as project_complete: true.
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0, (
        "CONFIRMED BUG in final_audit.py's run_completeness_gate(): it "
        "invokes select_segments.py with NO arguments. select_segments.py's "
        "own documented, by-design default behavior (see "
        "select_segments.test.py::"
        "test_default_run_fatals_on_empty_segs_unless_allow_empty) is to "
        "FATAL whenever its emitted SEGS list is empty, unless --allow-empty "
        "is passed -- a guard meant for a silently-no-op W5 DISPATCH batch. "
        "SEGS is empty precisely when every manifest segment already "
        "classifies 'reusable' -- i.e. exactly the fully-converged project "
        "state this gate exists to report as project_complete=true (see "
        "final-audit-summary.schema.json's own project_complete<->"
        "completeness_counts invariant). Because final_audit.py never "
        "passes --allow-empty, that one case crashes at exit 2 "
        "(run_completeness_gate()'s own _fatal()) before any JSON summary "
        "is ever printed -- the opposite of the documented contract. Fix: "
        "pass '--allow-empty' when final_audit.py invokes select_segments.py.\n"
        f"actual rc={result.returncode}\nstdout={result.stdout!r}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is True
    assert summary["completeness_counts"] == {
        "not_started": 0,
        "recoverable": 0,
        "stale": 0,
        "blocked_needs_regeneration": 0,
        "human_escalation": 0,
    }


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
