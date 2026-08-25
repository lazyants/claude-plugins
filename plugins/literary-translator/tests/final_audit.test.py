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

## Two integration behaviors locked in by dedicated tests below

Building this fixture chain for real (rather than stubbing select_segments.py)
originally surfaced two integration bugs in final_audit.py, both since
fixed in the shipped script; the dedicated tests below now assert the
CORRECT, fixed behavior directly (not a padded-around workaround), locking
it in as a regression guard:

1. **``--allow-empty``.** final_audit.py's whole-project completeness gate
   invokes ``select_segments.py`` WITH ``--allow-empty`` specifically because
   ``select_segments.py``'s own documented, BY-DESIGN default behavior (see
   ``select_segments.test.py::test_default_run_fatals_on_empty_segs_unless_allow_empty``)
   is to FATAL (``success: false``, exit 1) whenever its emitted ``SEGS`` list
   comes up empty -- a guard against a silently-no-op W5 mass-translate
   DISPATCH batch. ``SEGS`` is empty precisely when every manifest segment
   is already classified ``reusable`` -- i.e. *exactly* the fully-converged
   project state W7's completeness gate exists to report as
   ``project_complete: true``. Most tests below still pad with an inert,
   always-``not_started`` segment (see ``PAD_SEG``) purely to isolate the
   check they actually target from the completeness gate's own exit code
   (#208, see below) -- not to work around this bug, which no longer exists.
   The dedicated test near the bottom exercises the fully-converged,
   no-padding case directly.

2. **The frontback ``status`` shape.** ``build_frontback_coverage()`` must
   unwrap ``classification_by_seg``'s per-segment VALUE -- itself a dict
   (e.g. ``{"category": "reusable"}``), never a bare string, per
   select_segments.py's own module docstring: ``"classification": {seg:
   {...}}`` -- down to the plain classification CATEGORY string via
   ``.get("category")``, matching final-audit-summary.schema.json's own
   requirement that ``frontback_coverage[].status`` be a plain string (or
   null). ``test_frontback_coverage_translate_vs_regenerate_omit`` below
   locks this in directly.

## #208: whole-project completeness now gates the exit code

``run_completeness_gate()``'s ``project_complete`` used to be purely
informational -- ``main()`` exited ``1 if hard_failures else 0`` regardless
of it, so an incomplete project (segments still ``not_started``/
``recoverable``/``stale``/``blocked_needs_regeneration``/
``human_escalation``) exited ``0``, the same as a genuinely finished one.
final_audit.py now exits ``completeness_exit_code(hard_failures,
project_complete)``: ``0`` only when both hard checks are clean AND the
project is complete, ``1`` if any hard check fails (unchanged priority),
``3`` if hard checks are clean but the project is not yet complete. See the
dedicated tests below (module-docstring section 13 onward) plus the direct
unit test of ``completeness_exit_code`` itself.

Coverage (per the test's own enumeration):
  - hard check 1 (coverage_failures) via a real re-invocation of
    validate_draft.py, isolated from hard check 2;
  - hard check 2 (stale_review_failures) via a current-draft-sha1 vs.
    ledger-fragment reviewed_draft_sha1 mismatch, isolated from hard check 1;
  - the hard_failures rollup invariant across two segments, one failing each
    hard check;
  - all six WARN-only advisory checks (glossary-diff name-form drift +
    canon.json self-consistency, link-graph sentinel bijection,
    foreign-remainder stopword-density scan, verse-structure per
    verse_policy.mode);
  - the whole-project completeness gate (#208): incomplete via each of its
    five non-reusable categories (not_started, human_escalation,
    recoverable, stale, blocked_needs_regeneration) each exiting exactly 3;
    the fully-converged "complete" case exiting 0; a hard defect on top of
    an incomplete project still exiting exactly 1 (priority preserved); and
    a direct unit test of the underlying completeness_exit_code() helper;
  - the frontback coverage report (translate-decision cross-referenced to
    segment classification; regenerate/omit reported by decision alone);
  - #409 Step 2: the stale/ever-converged sentinel carve-out, FIELD-AWARE --
    a 'stale' segment carrying the durable .ever_converged.<seg> sentinel
    AND stale ONLY on a machinery-only field (SAFE_STALE_CARVEOUT_FIELDS:
    plugin_bundle_hash/schema_hash/derivation_bundle_hash) is deliverable
    (exit 0) while completeness_counts['stale'] stays the raw, unchanged
    count and the new stale_previously_converged field reports the
    carve-out; a 'stale' segment with NO sentinel still exits 3 (fail-safe,
    isolating the carve-out from a bare category deletion, on a safe
    field); a partial carve-out across two stale segments (same safe
    field, only one sentineled) still exits 3; a stale caused by a genuine
    style_bible.md edit (style_contract_hash, CONTENT-affecting, the same
    fixture shape tests/ledger_e2e_acceptance.test.py uses) still exits 3
    even WITH the sentinel; and a direct unit test of both
    compute_project_complete() and count_stale_previously_converged()'s
    own field-allowlist gate (including an unrecognized/future field name,
    which the real select_segments.py path can never itself produce, and a
    draft_sha1_mismatch stale_reason blocking despite empty
    mismatched_fields).
"""
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
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


def default_profile(
    particle_config="fr_test.json",
    verse_mode="full_rhymed_plus_literal",
    admit_contract_only_stale=None,
    forbidden_patterns=None,
    terms=None,
    apparatus_policy="translate_all",
):
    """`admit_contract_only_stale=None` OMITS the #533 key entirely -- the
    shape every existing project has -- while True/False write it
    explicitly. The three shapes must stay distinguishable: absent and
    explicitly-false have to behave identically, and a fixture that could
    only express one of them could not prove it."""
    profile = {
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
        "footnotes": {"apparatus_policy": apparatus_policy},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }
    if admit_contract_only_stale is not None:
        profile["validation"]["admit_contract_only_stale"] = admit_contract_only_stale
    # #520, same three-shape discipline as the key above: None OMITS
    # `forbidden_patterns` entirely (every project predating the field),
    # while [] declares it empty and a list declares rules. Absent and
    # declared-empty must be indistinguishable in behaviour, and a fixture
    # that could only express one of them could not prove it.
    if forbidden_patterns is not None:
        profile["validation"]["forbidden_patterns"] = forbidden_patterns
    # #199, same three-shape discipline as the two keys above: None OMITS
    # `terms` entirely (every project predating the field), [] declares it
    # empty, and a list declares pins. Absent and declared-empty must be
    # indistinguishable in behaviour, and a fixture that could only express one
    # of them could not prove it.
    if terms is not None:
        profile["validation"]["terms"] = terms
    return profile


def make_durable_root(
    tmp_path,
    seg_ids=("seg01",),
    frontback=None,
    verse_mode="full_rhymed_plus_literal",
    stopwords=None,
    canon=None,
    admit_contract_only_stale=None,
    forbidden_patterns=None,
    terms=None,
    apparatus_policy="translate_all",
    verse_store=None,
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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(SCRIPTS_SRC_DIR / "json_stdout.py", scripts_dir / "json_stdout.py")
    # cache_key.py's derivation_bundle_hash hashes this file's raw bytes
    # alongside bootstrap_names.py -- content is irrelevant, only needs to
    # exist (segpack.py itself is never imported by anything in this chain).
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py fixture placeholder\n")

    (root / "profile.yml").write_text(
        yaml.safe_dump(
            default_profile(
                verse_mode=verse_mode,
                admit_contract_only_stale=admit_contract_only_stale,
                forbidden_patterns=forbidden_patterns,
                terms=terms,
                apparatus_policy=apparatus_policy,
            ),
            sort_keys=False,
        ),
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

    manifest = {
        "source_inputs": [str(source_file.resolve())],
        "segments": [{"seg": s} for s in seg_ids],
        "frontback": frontback or [],
    }
    # #199: the SOURCE text of a verse lives here, not in any segpack -- W7's
    # term-consistency lane reads `verse.store[]` to get it. Omitted entirely
    # unless a test asks for it, so every pre-existing case keeps the manifest
    # it had.
    if verse_store is not None:
        manifest["verse"] = {"store": verse_store}
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
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


def write_bare_ledger_fragment(root: Path, seg: str, status: str, reason: str | None = None) -> None:
    """Writes a runs/ledger.d/{seg}.json fragment directly, bypassing
    add_converged_segment()'s segpack/draft/cache_key machinery entirely --
    for the classify_segment() (select_segments.py) categories that never
    read a segpack at all: human_escalation (fragment status 'blocked'/
    'non_converged') and recoverable (any other non-terminal status, e.g.
    'pending'/'in_progress'). No segments/*.json file is written for `seg`
    -- these statuses are BY DESIGN never 'converged' on disk, so
    final_audit.py's own load_converged_fragments() (status == 'converged'
    only) never picks them up for the two hard checks; they only ever
    surface through the whole-project completeness gate.
    """
    fragment = {"timestamp": "2026-01-01T00:00:00+00:00", "status": status}
    if reason is not None:
        fragment["reason"] = reason
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / f"{seg}.json").write_text(json.dumps(fragment, ensure_ascii=False), encoding="utf-8")


def corrupt_cache_key_field(root: Path, seg: str, field: str, bogus_value: str = "corrupted-for-test") -> None:
    """Mutates an existing converged ledger fragment's stored
    cache_key[field] in place, leaving status/reviewed_draft_sha1/every
    other field untouched -- the sole mechanism these tests use to force
    select_segments.py's completeness-gate reclassification (stale /
    blocked_needs_regeneration) away from 'reusable' without disturbing
    final_audit.py's own hard checks (which never look at cache_key at
    all, only at the draft's structural validity and its
    reviewed_draft_sha1 match)."""
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    fragment = json.loads(frag_path.read_text(encoding="utf-8"))
    fragment["cache_key"][field] = bogus_value
    frag_path.write_text(json.dumps(fragment, ensure_ascii=False), encoding="utf-8")


def mark_ever_converged(root: Path, seg: str) -> None:
    """Writes the #409 durable ever-converged sentinel directly at
    segments/.ever_converged.<seg> -- this suite never invokes the real
    ledger_update.py as a subprocess, so it cannot call that script's own
    mark_ever_converged() to produce it. Same filename convention and same
    fixed, no-timestamp content (b"converged\\n") the real
    ledger_update.py:mark_ever_converged writes, per that function's own
    docstring ("Content is deliberately fixed, with no timestamp")."""
    (root / "segments" / f".ever_converged.{seg}").write_bytes(b"converged\n")


def load_final_audit_module():
    """Loads the REAL final_audit.py in-process (never a copy), purely to
    unit-test its one pure, durable-root-independent helper,
    completeness_exit_code() -- every other test in this file exercises the
    full subprocess/self-anchoring path instead (see this file's own
    docstring). Same importlib.util pattern bootstrap_names.test.py uses
    for its own standalone (non-package) script under test."""
    spec = importlib.util.spec_from_file_location("final_audit_under_test", FINAL_AUDIT_SRC)
    assert spec is not None and spec.loader is not None, f"could not load spec for {FINAL_AUDIT_SRC}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Clean baseline: hard checks AND all six WARN checks clean.
# ---------------------------------------------------------------------------


def test_clean_project_all_checks_pass(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
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
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",), canon=canon)
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
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


def test_warn_foreign_remainder_stopword_run_with_punctuation(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
    p1_text = f"Some translated prose with a note {FN_PH} attached. Voici de, la, le texte."
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft(p1_text=p1_text))

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert (
        "[seg01] FOREIGN-REMNANT possible untranslated source-language text "
        "in p1: stopword_hits=3 longest_run=3" in result.stderr
    )


def test_warn_foreign_remainder_stopword_run_markdown_emphasis(tmp_path):
    # "_" is a \w word character, so a naive \W-based outer-punctuation strip
    # never unwraps Markdown italic emphasis -- a stopword run adorned with
    # it must still be detected as such.
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
    p1_text = f"Some translated prose with a note {FN_PH} attached. _de_ _la_ _le_ texte."
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


def test_warn_foreign_remainder_nfd_combining_mark_not_stripped(tmp_path):
    # An outer-punctuation strip based on Unicode category "not word char"
    # also matches combining marks (category Mn) -- an NFD-decomposed
    # accented letter (e.g. Spanish "Si" = 'S' 'i' COMBINING ACUTE ACCENT)
    # would lose its trailing mark and collapse into a bare, unaccented
    # stopword of an unrelated language. The mark must stay attached to its
    # base letter so this never produces a false-positive foreign-remnant.
    root = make_durable_root(
        tmp_path,
        seg_ids=("seg01",),
        stopwords=DEFAULT_STOPWORDS + ["si"],
    )
    nfd_si = unicodedata.normalize("NFD", "Sí")
    assert len(nfd_si) == 3, "fixture assumption: NFD 'Si' decomposes to 3 codepoints"
    p1_text = (
        f"Some translated prose with a note {FN_PH} attached. "
        f"{nfd_si} {nfd_si} {nfd_si} posible."
    )
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft(p1_text=p1_text))

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["warnings"] == 0
    assert "FOREIGN-REMNANT" not in result.stderr


def test_warn_foreign_remainder_nfd_stopword_matches_nfd_document(tmp_path):
    # Mirror-image of the NFD-document/NFC-stopword test above: a
    # project-local, user-authored custom preset may legally ship an
    # NFD-decomposed STOPWORDS entry (unlike the shipped presets under
    # assets/languages/*.json, confirmed NFC by inspection). NFC-normalizing
    # only the DOCUMENT-token side (as the previous fix did) is one-sided --
    # it silently breaks an NFD stopword that used to match an NFD document
    # token by lucky same-form consistency before that fix existed. Both
    # sides of the comparison must be NFC-normalized, matching the token
    # side's own unicodedata.normalize("NFC", ...) call.
    nfd_stopword = unicodedata.normalize("NFD", "Sí").lower()
    assert len(nfd_stopword) == 3, "fixture assumption: NFD 'sí' decomposes to 3 codepoints"
    root = make_durable_root(
        tmp_path,
        seg_ids=("seg01",),
        stopwords=DEFAULT_STOPWORDS + [nfd_stopword],
    )
    nfd_si = unicodedata.normalize("NFD", "Sí")
    assert len(nfd_si) == 3, "fixture assumption: NFD 'Sí' decomposes to 3 codepoints"
    p1_text = (
        f"Some translated prose with a note {FN_PH} attached. "
        f"{nfd_si} {nfd_si} {nfd_si} posible."
    )
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


def test_warn_foreign_remainder_nfd_document_matches_nfc_stopword(tmp_path):
    # The shipped language JSONs (assets/languages/*.json) ship their
    # STOPWORDS entries pre-composed (NFC) -- confirmed by inspection, e.g.
    # fr.json's "Après"/"Voilà" and es.json's "Tú"/"Sí" are all single
    # precomposed codepoints, never a base letter + combining mark. But a
    # translated draft's own text is free-form and can legitimately contain
    # an NFD-decomposed accented word (e.g. from a different editor/OS). A
    # stopword match that only works when both sides happen to already share
    # the same normalization form would silently miss a genuine
    # foreign-remnant run whenever the DOCUMENT text (not the stopword list)
    # is NFD -- the mirror-image gap of the NFD-combining-mark-not-stripped
    # test above, which only proves stripping doesn't corrupt an NFD token,
    # not that a genuinely NFD token still matches an NFC stopword.
    nfc_stopword = unicodedata.normalize("NFC", "Sí").lower()
    assert len(nfc_stopword) == 2, "fixture assumption: NFC 'sí' is 2 codepoints (s + í)"
    root = make_durable_root(
        tmp_path,
        seg_ids=("seg01",),
        stopwords=DEFAULT_STOPWORDS + [nfc_stopword],
    )
    nfd_si = unicodedata.normalize("NFD", "Sí")
    assert len(nfd_si) == 3, "fixture assumption: NFD 'Sí' decomposes to 3 codepoints"
    p1_text = (
        f"Some translated prose with a note {FN_PH} attached. "
        f"{nfd_si} {nfd_si} {nfd_si} posible."
    )
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


def test_warn_foreign_remainder_pointed_hebrew_matches_unpointed_stopword(tmp_path):
    # #209: the foreign-remainder check must fold Hebrew niqqud (category Mn
    # in U+0591..U+05C7) on BOTH compare sides, so a POINTED (vocalized) draft
    # token still matches its UNPOINTED consonantal stopword. A shipped he.json
    # ships bare consonantal function words; a real Hebrew source draft may
    # carry those same words fully pointed. Without the fold, NFC(pointed) !=
    # unpointed -> zero stopword hits -> a genuine untranslated Hebrew run
    # slips past this gate entirely.
    #
    # NIQQUD used (all category Mn, all within the folded range 0x0591..0x05C7):
    #   U+05B6 SEGOL, U+05B0 SHEVA, U+05B8 QAMATS.
    SEGOL, SHEVA, QAMATS = "ֶ", "ְ", "ָ"
    # Bare (unpointed) standalone Hebrew function words -> the stopword list.
    bare = ["את", "של", "על"]  # 'et', 'shel', 'al'
    # The SAME three words, each pointed with one interleaved niqqud mark.
    pointed = [
        "א" + SEGOL + "ת",   # aleph + segol + tav
        "ש" + SHEVA + "ל",   # shin  + sheva + lamed
        "ע" + QAMATS + "ל",  # ayin  + qamats + lamed
    ]
    # Fixture sanity (NOT a re-implementation of the fold): each pointed form
    # must genuinely differ from its bare form and carry only in-range Hebrew
    # combining marks -- otherwise the test would be vacuous or the marks would
    # fall outside the Hebrew-scoped fold and prove nothing.
    for p, b in zip(pointed, bare):
        assert p != b, "fixture: pointed form must differ from bare (else vacuous)"
        marks = [ch for ch in p if unicodedata.category(ch) == "Mn"]
        assert marks, "fixture: pointed form must carry at least one combining mark"
        assert all(0x0591 <= ord(ch) <= 0x05C7 for ch in marks), (
            "fixture: every niqqud must sit within the Hebrew fold range"
        )

    root = make_durable_root(tmp_path, seg_ids=("seg01",), stopwords=bare)
    p1_text = (
        f"Some translated prose with a note {FN_PH} attached. "
        f"{pointed[0]} {pointed[1]} {pointed[2]} more."
    )
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft(p1_text=p1_text))

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"WARN checks must not gate the exit code:\n{result.stderr}"
    )
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
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
    root = make_durable_root(tmp_path, seg_ids=("seg01",), verse_mode="skip")
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
# 10. Whole-project completeness gate, incomplete direction (#208): a
#     genuinely not_started segment (no fragment at all) keeps
#     project_complete false, with completeness_counts naming exactly which
#     category it fell into, and MUST fail-closed at exit code 3 -- distinct
#     from 0 (complete) and 1 (hard defects in converged drafts). This is the
#     SAME real select_segments.py -> ledger_merge.py -> cache_key.py chain
#     final_audit.py invokes in production.
# ---------------------------------------------------------------------------


def test_completeness_gate_project_incomplete_when_not_started_present(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    # seg02 deliberately gets no segpack/draft/ledger fragment at all.

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"an incomplete project is not itself a hard failure (hard_failures "
        f"must stay 0 -- only seg01, clean, is converged) but MUST fail-"
        f"closed with the dedicated incomplete exit code 3, distinct from "
        f"both 0 (fully complete) and 1 (hard defects):\n{result.stderr}"
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
#     state). Locks in the plain classification CATEGORY STRING shape (see
#     this file's module docstring, item 2) -- final_audit.py's
#     build_frontback_coverage() must unwrap select_segments.py's per-segment
#     classification DICT down to .get("category"), never store the dict
#     verbatim (final-audit-summary.schema.json requires a plain string).
# ---------------------------------------------------------------------------


def test_frontback_coverage_translate_vs_regenerate_omit(tmp_path):
    frontback = [
        {"id": "seg01", "decision": "translate"},
        {"id": "FRONTBACK:cover", "decision": "regenerate"},
        {"id": "FRONTBACK:toc", "decision": "omit"},
    ]
    root = make_durable_root(tmp_path, seg_ids=("seg01",), frontback=frontback)
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["frontback_coverage"] == [
        {"id": "seg01", "decision": "translate", "status": "reusable"},
        {"id": "FRONTBACK:cover", "decision": "regenerate", "status": None},
        {"id": "FRONTBACK:toc", "decision": "omit", "status": None},
    ]


# ---------------------------------------------------------------------------
# 12. Whole-project completeness gate, COMPLETE direction: every manifest
#     segment converged and fully matching -> select_segments.py classifies
#     all "reusable" -> its own default emitted SEGS is EMPTY -- the
#     fully-converged project state the completeness gate exists to report
#     as project_complete: true, and #208's completeness_exit_code() must
#     still exit 0 for it (the one case where "incomplete" does NOT apply).
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
        f"a fully-converged project must exit 0 -- hard checks clean AND "
        f"project_complete=true:\nrc={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr:\n{result.stderr}"
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


# ---------------------------------------------------------------------------
# 13. #208 fixture A: zero converged / all not_started. Asserts the EXACT
#     exit code -- exit 2 (fatal; many run_completeness_gate() paths crash
#     there) would false-satisfy a bare `!= 0` check, so this locks 3
#     specifically, distinguishing "incomplete" from "environment/usage
#     error".
# ---------------------------------------------------------------------------


def test_completeness_gate_zero_converged_all_not_started_exits_3(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    # No add_converged_segment call at all -- both segments are genuinely
    # not_started (no ledger fragment whatsoever).

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"zero converged segments (all not_started) must exit exactly 3, "
        f"never 0 and never the unrelated fatal exit 2:\n"
        f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["not_started"] == 2


# ---------------------------------------------------------------------------
# 14. #208 fixture B: human_escalation among converged. One clean converged
#     segment plus one 'blocked' (human_escalation) segment -- hard checks
#     stay clean (a blocked fragment is never in final_audit.py's own
#     converged set) but the project as a whole is incomplete.
# ---------------------------------------------------------------------------


def test_completeness_gate_human_escalation_among_converged_exits_3(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    write_bare_ledger_fragment(root, "seg02", status="blocked", reason="needs human review")

    result = run_final_audit(root)

    assert result.returncode == 3
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["human_escalation"] == 1


# ---------------------------------------------------------------------------
# 15-17. #208 fixture C: recoverable / stale / blocked_needs_regeneration
#     each among an otherwise-clean converged segment -- locks that the
#     completeness gate is NOT gated on human_escalation alone (the
#     "not just human_escalation" superset).
# ---------------------------------------------------------------------------


def test_completeness_gate_recoverable_among_converged_exits_3(tmp_path):
    # classify_segment() treats any non-terminal ledger status (here
    # 'in_progress') identically to not_started for dispatch purposes, but
    # as its OWN 'recoverable' category.
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    write_bare_ledger_fragment(root, "seg02", status="in_progress")

    result = run_final_audit(root)

    assert result.returncode == 3
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["recoverable"] == 1


def test_completeness_gate_stale_among_converged_exits_3(tmp_path):
    # seg02 is genuinely 'converged' on disk (final_audit.py's own hard
    # checks see a fully valid, sha1-matching draft -- hard_failures stays
    # 0), but its ledger fragment's stored cache_key has drifted on a
    # NON-derivation field, so select_segments.py's completeness gate
    # reclassifies it 'stale' at merge time.
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "prompt_hash")

    result = run_final_audit(root)

    assert result.returncode == 3
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0, (
        f"a cache_key drift alone must not itself fail either hard check -- "
        f"the draft on disk is still structurally valid and still matches "
        f"its own reviewed_draft_sha1:\n{result.stderr}"
    )
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["stale"] == 1


# ===========================================================================
# #533 -- the contract-only stale admission, END TO END through this script's
# CLI. The predicate itself, the arithmetic, the reader and the cross-gate
# parity live in tests/contract_stale_admission.test.py; what is pinned HERE
# is the wiring only this harness can reach: a REAL style_contract_hash drift
# classified by a REAL select_segments.py run, the exit code, the emitted
# summary (schema-validated), and the stderr disclosure.
# ===========================================================================


def _contract_stale_root(tmp_path, admit, sentinel=True):
    """A two-segment book whose seg02 went stale on style_contract_hash alone.
    Only the setup is shared -- what each case VARIES (the declaration, the
    sentinel, an extra moved field) stays visible at its own call site."""
    root = make_durable_root(
        tmp_path, seg_ids=("seg01", "seg02"), admit_contract_only_stale=admit
    )
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "style_contract_hash")
    if sentinel:
        mark_ever_converged(root, "seg02")
    return root


def _run_contract_stale_book(tmp_path, admit, label):
    """Shared body for the two undeclared shapes below. Written as a helper
    rather than a parametrize because this file imports pytest locally, in
    the one function that needs it, and has no module-level import to hang a
    marker off."""
    root = _contract_stale_root(tmp_path, admit)

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"[{label}] an undeclared contract-stale unit must still block the "
        f"whole-project gate:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0, result.stderr
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["stale"] == 1
    assert summary["stale_previously_converged"] == 0, (
        f"[{label}] style_contract_hash must never reach the #409 "
        f"machinery-only carve-out: {summary!r}"
    )
    assert "stale_contract_admitted" not in summary, (
        f"[{label}] an undeclared run must not emit the #533 key at all: {summary!r}"
    )
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr


def test_contract_only_stale_still_blocks_when_declaration_absent(tmp_path):
    """The refusal this feature exists to make optional -- and which must stay
    the default for every project that has not opted in."""
    _run_contract_stale_book(tmp_path, None, "absent")


def test_contract_only_stale_still_blocks_when_declaration_is_false(tmp_path):
    """Explicitly false must behave exactly like absent. An implementation
    keying on the KEY's presence rather than its VALUE would pass the absent
    case above and fail here -- which is the only reason both exist."""
    _run_contract_stale_book(tmp_path, False, "explicit-false")


def test_declared_contract_only_stale_completes_and_is_named(tmp_path):
    """The whole point: a book whose ONLY remaining incompleteness is a
    contract edit becomes shippable, and says by name which units are
    shipping unjudged against the current contract.

    Mutation: subtract the count but omit the summary key -> the naming
    assertions go red while the exit code still passes."""
    root = _contract_stale_root(tmp_path, True)

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"a declared contract-only stale unit must not block the gate:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["project_complete"] is True
    assert summary["completeness_counts"]["stale"] == 1, (
        f"the RAW stale count must stay visible -- the admission subtracts "
        f"from the VERDICT, never from the operator's view of the book: {summary!r}"
    )
    assert summary["stale_contract_admitted"] == ["seg02"], summary
    assert summary["stale_previously_converged"] == 0, summary
    assert "CONTRACT-ONLY STALE ADMITTED (1)" in result.stderr, result.stderr
    assert "  ~ seg02" in result.stderr, result.stderr


def test_declared_admission_does_not_cover_a_sentinel_less_unit(tmp_path):
    """Same book, same declaration, no sentinel: the unit cannot be shown to
    have converged at all, so it still blocks."""
    root = _contract_stale_root(tmp_path, True, sentinel=False)

    result = run_final_audit(root)

    assert result.returncode == 3, result.stderr
    summary = parse_summary(result)
    assert summary["project_complete"] is False
    assert "stale_contract_admitted" not in summary, summary


def test_declared_admission_does_not_cover_another_content_field(tmp_path):
    """A prompt_hash drift alongside the contract move is a genuinely
    different book: the declaration says "only the standard moved", and here
    something else did."""
    root = _contract_stale_root(tmp_path, True)
    corrupt_cache_key_field(root, "seg02", "prompt_hash")

    result = run_final_audit(root)

    assert result.returncode == 3, result.stderr
    summary = parse_summary(result)
    assert summary["project_complete"] is False
    assert "stale_contract_admitted" not in summary, summary


def test_declared_admission_with_nothing_to_admit_emits_nothing_new(tmp_path):
    """A fully converged, declared project must look exactly like a fully
    converged undeclared one -- an emitted empty list would be a claim about
    a check that had nothing to check."""
    root = make_durable_root(tmp_path, seg_ids=("seg01",), admit_contract_only_stale=True)
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_final_audit(root)

    assert result.returncode == 0, result.stderr
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["project_complete"] is True
    assert "stale_contract_admitted" not in summary, summary
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr


def test_completeness_gate_blocked_needs_regeneration_among_converged_exits_3(tmp_path):
    # Like the stale case above, but the drifted cache_key field is a
    # DERIVATION-state field (source_extraction_hash) that the segpack's
    # own generation_hashes hasn't caught up with -- select_segments.py
    # reclassifies this 'blocked_needs_regeneration', not plain 'stale'.
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "source_extraction_hash")

    result = run_final_audit(root)

    assert result.returncode == 3
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["blocked_needs_regeneration"] == 1


# ---------------------------------------------------------------------------
# 17b. #409 Step 2: a 'stale' segment that ALSO carries the durable
#     .ever_converged.<seg> sentinel (mark_ever_converged() above) is
#     carved out ONLY when it went stale for a reason that can never, by
#     itself, change the segment's own translated prose -- every
#     mismatched_fields entry in SAFE_STALE_CARVEOUT_FIELDS (machinery:
#     plugin_bundle_hash/schema_hash/derivation_bundle_hash) and
#     stale_reason exactly ['cache_key_mismatch']. select_segments.py's own
#     #409 Step 1 gate already refuses to silently re-translate exactly
#     this segment; the whole-project completeness gate must not keep
#     blocking W8 Deliver for work that gate already protects. This
#     carve-out must be VISIBLE, not silent: completeness_counts['stale']
#     stays the raw, unchanged count, and a new stale_previously_converged
#     field reports how many of those are carved out. A CONTENT-affecting
#     field (style_contract_hash, prompt_hash, ...) or an unrecognized
#     field name must keep blocking -- fail-safe.
# ---------------------------------------------------------------------------


def test_completeness_gate_stale_with_ever_converged_sentinel_is_deliverable(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    # plugin_bundle_hash: machinery-only (SAFE_STALE_CARVEOUT_FIELDS) --
    # classifies 'stale' with stale_reason exactly ['cache_key_mismatch'].
    corrupt_cache_key_field(root, "seg02", "plugin_bundle_hash")
    mark_ever_converged(root, "seg02")  # -> but carved out: already converged once

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"a 'stale' segment carrying the #409 ever-converged sentinel, "
        f"stale ONLY on a machinery field, must not block delivery -- its "
        f"translation already converged, only tooling moved:\n"
        f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is True
    assert summary["completeness_counts"]["stale"] == 1, (
        "the raw stale count must stay visible, never silently zeroed by "
        "the carve-out"
    )
    assert summary["stale_previously_converged"] == 1
    assert "stale_previously_converged=1" in result.stderr


def test_an_ambiguous_sentinel_still_carves_out_and_is_reported(tmp_path):
    """1.19.1 fail-closed predicate, final_audit's half -- and the half where
    "fail-closed" points at the OPPOSITE ACTION from the dispatch gate's.

    The writer and the dispatch gate refuse when the sentinel is unreadable;
    backfill's scan reports it unprotected; assemble.py's #491 carve-out
    admits it for the same reason this one does. Here refusing IS the
    destructive branch: a dangling symlink read as "absent" drops seg02 out
    of the carve-out, leaves stale_blocking at 1, and reports a finished
    book as INCOMPLETE -- over a broken dotfile, with no way out, because
    the operator's only route to a fresh sentinel is a retranslate that
    select_segments.py's gate now correctly refuses for this very segment.
    Sentinel respected in one place and not the other is exactly the "tokens
    saved, book undeliverable" shape.

    Fails against the unfixed code at `assert result.returncode == 0` (and at
    project_complete/stale_previously_converged): pre-fix `.exists()` follows
    the dangling link, returns False, the segment is not carved out, and the
    audit exits 3."""
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "plugin_bundle_hash")

    link = root / "segments" / ".ever_converged.seg02"
    link.symlink_to(root / "segments" / "no-such-target")
    assert link.is_symlink() and not link.exists(), (
        "precondition: a DANGLING link -- Path.exists() must report False "
        "here, or the test is not exercising the reported bug"
    )

    result = run_final_audit(root)

    assert result.returncode == 0, (
        f"an unreadable sentinel must not turn a deliverable book into an "
        f"undeliverable one -- the ledger already recorded this segment "
        f"converged, which is what made it 'stale' rather than 'not_started'"
        f":\nrc={result.returncode}\nstdout={result.stdout!r}\n"
        f"stderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["project_complete"] is True
    assert summary["stale_previously_converged"] == 1
    assert summary["completeness_counts"]["stale"] == 1, (
        "the raw stale count must stay visible, exactly as for a valid sentinel"
    )
    # Counted, but never silently: the operator is the only one who can repair
    # the path, so the audit that relied on it has to say so.
    assert "AMBIGUOUS EVER-CONVERGED SENTINELS" in result.stderr, result.stderr
    assert "seg02" in result.stderr
    assert "symbolic link" in result.stderr, (
        "the report must name what is actually at the path, not just that "
        "something is wrong"
    )


def test_a_valid_sentinel_produces_no_ambiguity_report(tmp_path):
    """FALSE-POSITIVE BOUND for the test above. A healthy project must not
    grow a scary AMBIGUOUS banner -- a warning that fires on the normal case
    is one operators learn to skip past, which would cost exactly the
    attention the banner exists to buy.

    Green before and after the fix by design."""
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "plugin_bundle_hash")
    mark_ever_converged(root, "seg02")

    result = run_final_audit(root)

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert parse_summary(result)["project_complete"] is True
    assert "AMBIGUOUS EVER-CONVERGED SENTINELS" not in result.stderr


def test_completeness_gate_stale_without_sentinel_still_exits_3(tmp_path):
    """Fail-safe half of the carve-out: a 'stale' segment with NO sentinel
    must still block delivery exactly as before (#208's pre-existing
    behavior), even though the mismatched field itself (plugin_bundle_hash)
    IS in the safe allowlist -- isolating the sentinel-presence axis from
    the field-safety axis. Without this test, simply deleting the 'stale'
    category from COMPLETENESS_CATEGORIES would satisfy the sentinel-
    carve-out test above just as well -- this is what actually pins the
    carve-out to the sentinel, not to the bare 'stale' classification."""
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    corrupt_cache_key_field(root, "seg02", "plugin_bundle_hash")  # -> classifies 'stale'
    # Deliberately NO mark_ever_converged() call for seg02.

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"a 'stale' segment with NO ever-converged sentinel must still "
        f"block delivery exactly as before the carve-out, even on a safe "
        f"field:\nrc={result.returncode}\nstdout={result.stdout!r}\n"
        f"stderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["stale"] == 1
    assert summary["stale_previously_converged"] == 0


def test_completeness_gate_partial_stale_carveout_still_incomplete(tmp_path):
    """Two 'stale' segments, both stale on the SAME safe field
    (plugin_bundle_hash), only one carrying the sentinel -- the carve-out
    is per-segment, not per-category: a single un-carved-out stale segment
    must keep the whole project incomplete even while the other is
    delivered fine. Both segments share the same (safe) mismatched field
    deliberately, so the only variable under test is sentinel presence."""
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02", "seg03"))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    add_converged_segment(root, "seg03", clean_segpack(seg="seg03"), clean_draft(seg="seg03"))
    corrupt_cache_key_field(root, "seg02", "plugin_bundle_hash")
    corrupt_cache_key_field(root, "seg03", "plugin_bundle_hash")
    mark_ever_converged(root, "seg02")  # carved out
    # seg03 deliberately left without the sentinel.

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"one un-carved-out stale segment must keep the project incomplete "
        f"even though a sibling stale segment IS carved out:\n"
        f"rc={result.returncode}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["stale"] == 2
    assert summary["stale_previously_converged"] == 1


def test_completeness_gate_stale_style_contract_change_still_exits_3(tmp_path):
    """The exact scenario the lead flagged as a live gap: an operator edits
    style_bible.md (a genuine on-disk edit, the SAME mechanism
    tests/ledger_e2e_acceptance.test.py's own style-bible-edit fixture uses
    -- never corrupt_cache_key_field's synthetic ledger-JSON poke), which
    moves the GLOBAL style_contract_hash field for every converged segment.
    style_contract_hash is CONTENT-affecting (the operator's own style
    instructions, read on every translate/review call) -- carving this out
    would report the book deliverable while silently shipping prose that
    predates the operator's own edit. The sentinel must NOT carve it out."""
    root = make_durable_root(tmp_path, seg_ids=("seg01",))
    add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
    mark_ever_converged(root, "seg01")
    # Genuine style_bible.md edit -- moves style_contract_hash for every
    # converged segment, exactly like a real operator edit would.
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Informal register, no Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )

    result = run_final_audit(root)

    assert result.returncode == 3, (
        f"a stale segment caused by a genuine style_bible.md edit "
        f"(style_contract_hash, content-affecting) must NOT be carved out "
        f"by the sentinel, even though it carries it:\n"
        f"rc={result.returncode}\nstderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 0
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["stale"] == 1
    assert summary["stale_previously_converged"] == 0
    assert "STALE-REVIEW" not in result.stderr, (
        "a style_bible.md edit is a cache_key drift, not a draft hand-edit "
        "-- it must never trip hard check 2"
    )


def test_count_stale_previously_converged_field_gating_matrix(tmp_path):
    """Direct unit test of count_stale_previously_converged()'s field-
    allowlist gate (mirrors test_compute_project_complete_matrix's own
    pattern) -- covers the 'unknown/future field name' fail-safe case,
    which cannot be produced via the real select_segments.py path today:
    its own mismatched_fields is computed as `f for f in CACHE_KEY_FIELDS
    if ...`, strictly bounded to the 15 known fields in cache_key.py's
    CACHE_KEY_FIELD_ORDER, so a synthetic classification dict is the only
    way to exercise a name outside that set."""
    fa = load_final_audit_module()
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    fa.SEGMENTS_DIR = segments_dir  # isolate from the real plugin tree
    for seg in ("seg_safe_multi", "seg_one_unsafe", "seg_unknown_field", "seg_draft_edit"):
        mark_ever_converged(tmp_path, seg)
    # seg_no_sentinel deliberately gets NO sentinel file.

    classification = {
        # All 3 SAFE_STALE_CARVEOUT_FIELDS at once, sentinel present -> counted.
        "seg_safe_multi": {
            "category": "stale",
            "stale_reason": ["cache_key_mismatch"],
            "mismatched_fields": ["plugin_bundle_hash", "schema_hash", "derivation_bundle_hash"],
        },
        # One safe + one content-affecting field -> the whole segment blocks.
        "seg_one_unsafe": {
            "category": "stale",
            "stale_reason": ["cache_key_mismatch"],
            "mismatched_fields": ["plugin_bundle_hash", "style_contract_hash"],
        },
        # A name outside the 15 real cache_key fields entirely -> blocks
        # (fail-safe: absent from the allowlist by construction).
        "seg_unknown_field": {
            "category": "stale",
            "stale_reason": ["cache_key_mismatch"],
            "mismatched_fields": ["some_future_cache_key_field"],
        },
        # Draft hand-edited since review -- mismatched_fields is even empty
        # (no cache-key drift at all), but stale_reason rules this out.
        "seg_draft_edit": {
            "category": "stale",
            "stale_reason": ["draft_sha1_mismatch"],
            "mismatched_fields": [],
        },
        # Otherwise-safe, but no sentinel written for this seg.
        "seg_no_sentinel": {
            "category": "stale",
            "stale_reason": ["cache_key_mismatch"],
            "mismatched_fields": ["plugin_bundle_hash"],
        },
        # Not even stale -- must be ignored outright.
        "seg_reusable": {"category": "reusable"},
    }

    assert fa.count_stale_previously_converged(classification) == 1


def test_carveout_count_and_ambiguity_report_read_one_scan(tmp_path):
    """The carve-out count and the operator diagnostic must never disagree
    about the SAME segment's sentinel.

    Both ask a question of `.ever_converged.<seg>` -- "is it absent?" and "is
    it ambiguous?" -- and each used to answer with its own `stat`. Two
    independent reads of one path make the pair non-atomic, so a sentinel that
    changes between them produces a segment counted as converged that nothing
    reports: precisely the silence count_stale_previously_converged()'s own
    comment promises is impossible ("The ambiguity is never silent").

    A DIRECTORY at the sentinel path is the ambiguous case: the predicate
    refuses to read it as a converged marker, and the carve-out counts it
    anyway because refusing would declare a finished book undeliverable over
    an unreadable dotfile (see that function's comment)."""
    fa = load_final_audit_module()
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    fa.SEGMENTS_DIR = segments_dir  # isolate from the real plugin tree
    sentinel = segments_dir / ".ever_converged.seg_amb"
    sentinel.mkdir()  # a directory -> AMBIGUOUS, never ABSENT

    classification = {
        "seg_amb": {
            "category": "stale",
            "stale_reason": ["cache_key_mismatch"],
            "mismatched_fields": ["plugin_bundle_hash"],
        },
    }
    assert fa.scan_sentinel_states(classification)["seg_amb"][0] == fa.SENTINEL_AMBIGUOUS

    # main()'s shape: one scan, then both consumers. The tree is mutated in
    # between -- a concurrent dispatch, an operator cleaning up the broken
    # path, anything -- and neither answer moves, because neither re-reads.
    states = fa.scan_sentinel_states(classification)
    shutil.rmtree(sentinel)
    assert fa.count_stale_previously_converged(classification, states) == 1
    assert [a["seg"] for a in fa.collect_ambiguous_sentinels(classification, states)] == [
        "seg_amb"
    ], "counted as carved out, so it MUST also appear in the operator diagnostic"

    # Non-vacuity: the same sequence with two independent reads -- the pre-fix
    # shape, still reachable by passing no scan -- produces exactly the silent
    # carve-out. Without this block the assertions above would also pass on
    # code that re-stats, since nothing else here forces the mutation to matter.
    sentinel.mkdir()
    counted_solo = fa.count_stale_previously_converged(classification)  # sees AMBIGUOUS
    shutil.rmtree(sentinel)
    ambiguous_solo = fa.collect_ambiguous_sentinels(classification)  # sees ABSENT
    assert counted_solo == 1 and ambiguous_solo == [], (
        "this is the defect the shared scan removes: counted as converged, "
        "reported by nothing. If this assertion ever fails, the two reads have "
        "become atomic by some other means and this test should be revisited."
    )


# ---------------------------------------------------------------------------
# 18. #208 fixture E: hard_failures keeps priority over incompleteness -- a
#     converged segment with a genuine coverage defect, alongside a second,
#     genuinely not_started segment (project incomplete either way), must
#     still exit exactly 1, never 3.
# ---------------------------------------------------------------------------


def test_completeness_gate_hard_failure_priority_over_incomplete_exits_1(tmp_path):
    root = make_durable_root(tmp_path, seg_ids=("seg01", "seg02"))
    draft = clean_draft()
    draft["footnotes"]["1"] = ""  # injected coverage defect
    add_converged_segment(root, "seg01", clean_segpack(), draft)
    # seg02 deliberately gets no fragment at all -> not_started, keeping the
    # project incomplete on top of the hard defect.

    result = run_final_audit(root)

    assert result.returncode == 1, (
        f"a hard defect on a converged segment must win over incompleteness "
        f"-- exit code stays 1, never 3:\nrc={result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    summary = parse_summary(result)
    assert_schema_valid(summary)
    assert summary["hard_failures"] == 1
    assert summary["project_complete"] is False


# ---------------------------------------------------------------------------
# 19. Unit test: completeness_exit_code(hard_failures, project_complete) --
#     the pure helper #208 introduces, over its full priority matrix.
# ---------------------------------------------------------------------------


def test_completeness_exit_code_matrix():
    fa = load_final_audit_module()
    assert fa.completeness_exit_code(hard_failures=0, project_complete=True) == 0
    assert fa.completeness_exit_code(hard_failures=0, project_complete=False) == 3
    assert fa.completeness_exit_code(hard_failures=1, project_complete=True) == 1
    assert fa.completeness_exit_code(hard_failures=1, project_complete=False) == 1
    assert fa.completeness_exit_code(hard_failures=5, project_complete=False) == 1


# ---------------------------------------------------------------------------
# 20. #409 Step 2: compute_project_complete(completeness_counts,
#     stale_previously_converged) -- the pure helper the carve-out
#     introduces, over its own priority matrix (mirrors
#     test_completeness_exit_code_matrix above).
# ---------------------------------------------------------------------------


def test_compute_project_complete_matrix():
    fa = load_final_audit_module()
    all_zero = {
        "not_started": 0, "recoverable": 0, "stale": 0,
        "blocked_needs_regeneration": 0, "human_escalation": 0,
    }
    assert fa.compute_project_complete(all_zero, stale_previously_converged=0) is True

    stale_only_fully_carved_out = {**all_zero, "stale": 3}
    assert fa.compute_project_complete(stale_only_fully_carved_out, stale_previously_converged=3) is True

    stale_only_partially_carved_out = {**all_zero, "stale": 3}
    assert fa.compute_project_complete(stale_only_partially_carved_out, stale_previously_converged=2) is False

    stale_only_no_carveout = {**all_zero, "stale": 1}
    assert fa.compute_project_complete(stale_only_no_carveout, stale_previously_converged=0) is False

    # A non-'stale' category being nonzero must still block completeness
    # regardless of the (irrelevant) stale carve-out.
    not_started_present = {**all_zero, "not_started": 1}
    assert fa.compute_project_complete(not_started_present, stale_previously_converged=0) is False


# ---------------------------------------------------------------------------
# #412 -- --plugin-root PATH override for the SIBLING select_segments.py the
# whole-project completeness gate shells out to.
#
# final_audit.py is itself Step-0a-copied (not among SKILL.md's four
# never-copied plugin-path scripts), so in production its own SCRIPTS_DIR --
# where it resolves select_segments.py from -- IS the durable-root copy the
# codex process can write to. A tampered select_segments.py there could
# report a false "project complete" and let W8 Deliver run over an
# incomplete book. Same poisoned-sibling technique
# select_segments.test.py/ledger_merge.test.py already use for their own
# --plugin-root batteries: both directions are asserted, since a script that
# never touched select_segments.py at all would look identical to one that
# correctly routed around the poison.
#
# The default make_durable_root() fixture (single PAD-like seg01, untouched
# -- not_started) is used throughout: it deterministically reaches the
# whole-project completeness gate's own "incomplete" outcome (exit 3)
# without needing a converged segment at all, which is the cheapest
# observable proof that select_segments.py's REAL JSON contract, not a
# crash, was honored.
# ---------------------------------------------------------------------------

_TAMPERED_SELECT_SEGMENTS_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_SELECT_SEGMENTS_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_select_segments(root: Path) -> None:
    """Overwrites the durable-root copy of select_segments.py with a
    stand-in for a codex-tampered script: it always fails loudly and
    distinctively rather than silently faking success, so a test can tell
    whether THIS copy ran at all, in either direction."""
    (root / "scripts" / "select_segments.py").write_text(
        _TAMPERED_SELECT_SEGMENTS_SRC, encoding="utf-8"
    )


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install") -> Path:
    """A SEPARATE physical location holding the REAL select_segments.py at
    the {plugin_root}/assets/scripts/ layout SKILL.md documents for the
    plugin-anchored scripts. select_segments.py resolves ITS OWN siblings
    (ledger_merge.py, which resolves cache_key.py) the same
    --plugin-root-aware way once relocated here, so all three must be
    staged for a genuinely successful run through this trusted root --
    standing in for the plugin's actual install tree, physically apart
    from any durable_root fixture."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    for name_ in ("select_segments.py", "ledger_merge.py", "cache_key.py"):
        shutil.copy2(SCRIPTS_SRC_DIR / name_, plugin_scripts_dir / name_)
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(SCRIPTS_SRC_DIR / "json_stdout.py", plugin_scripts_dir / "json_stdout.py")
    return plugin_root


def run_final_audit_with(root: Path, *extra_args, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "final_audit.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_select_segments_py_itself_accepts_plugin_root(tmp_path):
    """Measured proof (not asserted from the task brief's sentence) that
    select_segments.py -- unlike cache_key.py -- DOES accept --plugin-root:
    confirmed here by actually running the real select_segments.py against
    it (with a matching --durable-root, since the flag relocates its own
    sibling lookup too), not by trusting the claim."""
    root = make_durable_root(tmp_path)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = subprocess.run(
        [
            sys.executable, str(root / "scripts" / "select_segments.py"),
            "--allow-empty", "--classify-only",
            "--durable-root", str(root),
            "--plugin-root", str(plugin_root),
        ],
        capture_output=True, text=True, timeout=90,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["success"] is True


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --plugin-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)

    proc = run_final_audit_with(root)

    assert proc.returncode == 3, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["not_started"] == 1


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core security property: final_audit.py runs from its own
    in-place durable-root copy whose SIBLING select_segments.py has been
    POISONED. --plugin-root pointing at a separate, untampered location
    must make it use THAT select_segments.py instead -- reaching the
    normal 'incomplete project' outcome is possible ONLY if the poisoned
    durable-root sibling was never executed."""
    root = make_durable_root(tmp_path)
    poison_durable_root_select_segments(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_final_audit_with(root, "--plugin-root", str(plugin_root))

    assert proc.returncode == 3, (
        f"--plugin-root pointing at the REAL select_segments.py must "
        f"succeed (reaching the normal 'incomplete project' outcome) even "
        f"though durable_root's own copy is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["not_started"] == 1


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root select_segments.py, invoked WITHOUT
    --plugin-root, is exactly what today's self-anchored lookup finds --
    unchanged. The poisoned script genuinely runs and fatals when the flag
    is omitted, proving the positive test's success above is attributable
    to --plugin-root specifically, not some other effect. The completeness
    gate's own contract is "cannot be silently skipped" -- a fatal here,
    never a false-green project_complete, is the correct failure mode."""
    root = make_durable_root(tmp_path)
    poison_durable_root_select_segments(root)

    proc = run_final_audit_with(root)  # no --plugin-root

    assert proc.returncode == 2, (
        f"the poisoned select_segments.py must actually run and cause the "
        f"completeness gate to fatal when --plugin-root is omitted:\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", (
        "a FATAL must print NO stdout JSON -- nothing can be mistaken for "
        "a schema-conforming summary"
    )
    assert "TAMPERED_SELECT_SEGMENTS_MUST_NEVER_RUN" in proc.stderr


# ---------------------------------------------------------------------------
# Doubled-path fix's --plugin-root variant: not a doubling (--durable-root
# here is always the resolved str(DURABLE_ROOT) constant, a self-anchored
# value this script never takes as a flag, so it cannot double) but a
# DIVERGENCE. run_completeness_gate() resolves a relative --plugin-root
# against THIS script's own invocation cwd to find select_segments.py, but
# used to forward the RAW string to that child, which launches with
# cwd=str(DURABLE_ROOT) -- a DIFFERENT base. So a relative --plugin-root
# could resolve to two DIFFERENT absolute paths depending which process
# resolved it. select_segments.py's own now-fixed docstring warns about
# exactly this shape. Every existing --plugin-root test above passes an
# absolute path, so none of them would have caught this.
# ---------------------------------------------------------------------------


def test_relative_plugin_root_resolves_against_the_original_invoker_cwd(tmp_path):
    """PROOF. Drives a genuinely RELATIVE --plugin-root from a cwd that is
    its own PARENT directory -- entirely separate from durable_root -- so a
    wrong resolution fails outright (select_segments.py cannot find its own
    ledger_merge.py sibling under the WRONG resolved plugin root, and
    fatals) rather than accidentally landing on the right tree by
    coincidence. Confirmed pre-fix (this exact fixture, against the parent
    commit's copy of final_audit.py) to exit 2 with
    'ledger_merge.py not found'; post-fix reaches the ordinary 'incomplete
    project' outcome (exit 3), proving the child resolved the SAME plugin
    root the parent did."""
    root = make_durable_root(tmp_path)
    plugin_root = make_trusted_plugin_root(tmp_path, name="trusted_plugin_install")

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "final_audit.py"), "--plugin-root", "trusted_plugin_install"],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 3, (
        f"a relative --plugin-root, invoked from its OWN parent directory, "
        f"must resolve the SAME way final_audit.py itself resolved it for "
        f"its own sibling lookup -- got rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert summary["project_complete"] is False
    assert summary["completeness_counts"]["not_started"] == 1


# ---------------------------------------------------------------------------
# 8. WARN 5 -- forbidden-pattern scan (#520).
#
# The plugin ships NO patterns, so every case here declares its own. The
# helper below is the one used by all of them: a clean single-segment root
# whose ONLY reason to warn is the declaration under test, so a nonzero
# `warnings` count is attributable and a zero one is meaningful.
#
# Every assertion runs the REAL script as a subprocess (run_final_audit), so
# each is simultaneously the wiring test: deleting the
# `warn_forbidden_patterns(...)` call from main() turns them red. That
# deletion was watched failing, as was mutating the declaration reader to
# return [] unconditionally.
# ---------------------------------------------------------------------------


def _pattern_root(tmp_path, patterns, draft=None):
    root = make_durable_root(tmp_path, seg_ids=("seg01", PAD_SEG), forbidden_patterns=patterns)
    add_converged_segment(root, "seg01", clean_segpack(), draft or clean_draft())
    return root


def _style_lines(proc):
    return [ln for ln in proc.stderr.splitlines() if "STYLE-PATTERN" in ln]


def _warn_block_lines(proc):
    """The PHYSICAL stderr lines of the WARN section.

    main() emits each warning as `"  • " + w`, so a warning carrying an
    embedded line break lands as a first line with the bullet and one or more
    continuation lines WITHOUT it. Counting lines that contain
    "STYLE-PATTERN" cannot see that -- the marker is only on the first
    fragment -- which is why the split has to be detected here, on bullet
    structure, and not by counting matches."""
    out = []
    inside = False
    for line in proc.stderr.splitlines():
        if line.startswith("WARN / MANUAL-REVIEW ("):
            inside = True
            continue
        if inside:
            if line.startswith("WHOLE-PROJECT COMPLETENESS:") or not line.strip():
                break
            out.append(line)
    return out


def test_forbidden_patterns_absent_adds_no_warning(tmp_path):
    """The compatibility case: a profile.yml predating #520 has no key at
    all, and the audit must behave exactly as it did before the check
    existed."""
    root = _pattern_root(tmp_path, None)
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert _style_lines(proc) == []
    assert summary["warnings"] == 0, proc.stderr


def test_forbidden_patterns_declared_empty_matches_absent(tmp_path):
    """Declared-empty and absent must be indistinguishable. Compared on the
    parsed summary with `generated_at` dropped -- the script stamps a fresh
    timestamp on every run, so raw stdout can never be byte-equal -- plus the
    stderr WARN section, which is where a spurious line would actually show."""
    absent = run_final_audit(_pattern_root(tmp_path / "a", None))
    empty = run_final_audit(_pattern_root(tmp_path / "b", []))

    def comparable(proc):
        summary = dict(parse_summary(proc))
        summary.pop("generated_at")
        return summary

    assert comparable(absent) == comparable(empty)
    assert _style_lines(absent) == _style_lines(empty) == []
    assert absent.returncode == empty.returncode


def test_forbidden_patterns_hit_in_block_reports_once(tmp_path):
    # Keeps {FN_PH} so footnote 1 stays referenced: without it the link-graph
    # WARN also fires and `warnings` stops being attributable to this check.
    draft = clean_draft(p1_text=f"A line with **bold** in it {FN_PH} and nothing else.")
    root = _pattern_root(
        tmp_path,
        [{"id": "adjacent-asterisks", "pattern": r"\*{2,}",
          "message": "two or more adjacent asterisks reach the reader verbatim"}],
        draft=draft,
    )
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    lines = _style_lines(proc)
    assert len(lines) == 1, proc.stderr
    line = lines[0]
    assert "[seg01]" in line
    assert "adjacent-asterisks" in line
    assert "blocks['p1']" in line
    assert "two or more adjacent asterisks reach the reader verbatim" in line
    # Two runs of asterisks around one bold span.
    assert "(hits=2)" in line
    assert summary["warnings"] == 1


def test_forbidden_patterns_scan_footnotes_and_verses_and_nested(tmp_path):
    """The scanned surface is every string LEAF of blocks/footnotes/verses,
    including one nested inside a verse object -- `draft.schema.json`
    constrains a verse value no further than 'is an object', so a field set
    beyond rendered/literal_gloss is schema-valid and must not be a blind
    spot."""
    draft = clean_draft(extra_footnotes={"2": "A note carrying FORBIDDEN text."})
    draft["blocks"]["p1"] = "Ordinary prose with no trigger at all."
    draft["verses"]["vA"]["literal_gloss"] = "A gloss carrying FORBIDDEN text."
    draft["verses"]["vB"]["provenance"] = {"note": ["deep FORBIDDEN leaf"]}
    root = _pattern_root(
        tmp_path,
        [{"id": "no-placeholder", "pattern": "FORBIDDEN", "message": "placeholder left in"}],
        draft=draft,
    )
    proc = run_final_audit(root)
    lines = _style_lines(proc)
    paths = sorted(ln.split(" in ", 1)[1].split(":", 1)[0] for ln in lines)
    assert paths == [
        "footnotes['2']",
        "verses['vA']['literal_gloss']",
        "verses['vB']['provenance']['note'][0]",
    ], proc.stderr


def test_forbidden_patterns_do_not_scan_names_or_notes(tmp_path):
    """`names` and `notes` are machinery/metadata, not translator prose. This
    pins the scanned surface: widening it to the whole draft turns this red."""
    draft = clean_draft(names=[{"source_form": "FORBIDDEN", "target_form": "FORBIDDEN"}])
    draft["notes"] = ["a FORBIDDEN operator note"]
    root = _pattern_root(
        tmp_path,
        [{"id": "no-placeholder", "pattern": "FORBIDDEN", "message": "placeholder left in"}],
        draft=draft,
    )
    proc = run_final_audit(root)
    assert _style_lines(proc) == [], proc.stderr


def test_forbidden_patterns_scan_draft_as_written_not_sentinel_stripped(tmp_path):
    """Both directions of the as-written rule, which is what separates this
    check from warn_foreign_remainder's `SENTINEL_RE.sub(" ", txt)`:

      - a pattern matching INSIDE a sentinel hits (stripping would hide it);
      - a pattern that would only match once a sentinel became a space does
        NOT hit (stripping would manufacture it).
    """
    draft = clean_draft(p1_text=f"Prose before{FN_PH}prose after.")
    root = _pattern_root(
        tmp_path,
        [
            {"id": "inside-sentinel", "pattern": "FNREF", "message": "sentinel body matched"},
            {"id": "only-after-strip", "pattern": r"before prose", "message": "would need stripping"},
        ],
        draft=draft,
    )
    proc = run_final_audit(root)
    lines = _style_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "inside-sentinel" in lines[0]
    assert "only-after-strip" not in proc.stderr


def test_forbidden_patterns_malformed_regex_warns_once_and_siblings_still_run(tmp_path):
    """A pattern that will not compile is REPORTED, never skipped -- a
    silently-unenforced rule is a false green. Reported once for the run, not
    once per segment, and its siblings still compile and fire."""
    draft = clean_draft(p1_text=f"A line with FORBIDDEN text in it {FN_PH}.")
    root = make_durable_root(
        tmp_path,
        seg_ids=("seg01", "seg02", PAD_SEG),
        forbidden_patterns=[
            {"id": "broken", "pattern": "(unclosed", "message": "never mind"},
            {"id": "no-placeholder", "pattern": "FORBIDDEN", "message": "placeholder left in"},
        ],
    )
    add_converged_segment(root, "seg01", clean_segpack(), draft)
    add_converged_segment(root, "seg02", clean_segpack(seg="seg02"), clean_draft(seg="seg02"))
    proc = run_final_audit(root)
    # This fixture carries PAD_SEG, so 3 ("project incomplete") is its baseline
    # for reasons unrelated to the declaration. That makes it USELESS as
    # exit-code evidence on its own -- a mutation forcing project_complete
    # False on a declaration warning would still return 3 here. The exit-code
    # property is pinned instead by
    # test_forbidden_patterns_never_gate_a_complete_project below, on a
    # fixture whose baseline is 0.
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    broken = [ln for ln in _style_lines(proc) if "broken" in ln]
    assert len(broken) == 1, proc.stderr
    assert "does not compile" in broken[0]
    assert "NOT enforced" in broken[0]
    assert len([ln for ln in _style_lines(proc) if "no-placeholder" in ln]) == 1


def test_forbidden_patterns_uncompilable_beyond_re_error_is_still_advisory(tmp_path):
    """`re.compile` does not raise one family. A malformed pattern raises
    `re.error`; an oversized repetition count raises `OverflowError` -- from a
    39-character pattern the schema's 200-codepoint cap admits without
    complaint. Catching only `re.error` turned this advisory check into a
    traceback that aborted the whole audit before its summary, taking the two
    HARD checks' verdict with it.

    The sibling declaration must still fire, and the exit code must still be
    the advisory baseline."""
    draft = clean_draft(p1_text=f"A line with FORBIDDEN text in it {FN_PH}.")
    root = make_durable_root(
        tmp_path,
        seg_ids=("seg01", PAD_SEG),
        forbidden_patterns=[
            {"id": "overflowing", "pattern": "a{" + "9" * 36 + "}", "message": "never mind"},
            {"id": "no-placeholder", "pattern": "FORBIDDEN", "message": "placeholder left in"},
        ],
    )
    add_converged_segment(root, "seg01", clean_segpack(), draft)
    proc = run_final_audit(root)
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    broken = [ln for ln in _style_lines(proc) if "overflowing" in ln]
    assert len(broken) == 1, proc.stderr
    assert "OverflowError" in broken[0], broken[0]
    assert "NOT enforced" in broken[0]
    assert len([ln for ln in _style_lines(proc) if "no-placeholder" in ln]) == 1


def test_forbidden_patterns_never_gate_a_complete_project(tmp_path):
    """Acceptance criterion 6, on the only fixture that can prove it.

    NO padding segment, so a clean run of this project exits 0. Both a HIT and
    a rejected declaration are then asserted to leave that 0 untouched. Any
    mutation that lets an advisory declaration reach `hard_failures` or
    `project_complete` turns this red -- which the same assertions on a
    PAD_SEG fixture cannot do, since that one already exits 3."""
    baseline = make_durable_root(tmp_path / "base", seg_ids=("seg01",))
    add_converged_segment(baseline, "seg01", clean_segpack(), clean_draft())
    clean = run_final_audit(baseline)
    assert clean.returncode == 0, clean.stderr
    assert parse_summary(clean)["project_complete"] is True

    # Both shapes that reach the WARN lane: a hit, and an uncompilable pattern.
    # Malformed DECLARATION shapes are not in this table because they are not
    # this script's to report -- profile.schema.json refuses them at Step 0.
    for name, patterns in (
        ("hit", [{"id": "note-marker", "pattern": "note", "message": "marker left in"}]),
        ("broken", [{"id": "broken", "pattern": "(unclosed", "message": "never mind"}]),
    ):
        root = make_durable_root(tmp_path / name, seg_ids=("seg01",), forbidden_patterns=patterns)
        add_converged_segment(root, "seg01", clean_segpack(), clean_draft())
        proc = run_final_audit(root)
        summary = parse_summary(proc)
        assert_schema_valid(summary)
        assert _style_lines(proc), (name, proc.stderr)
        assert summary["warnings"] >= 1, (name, proc.stderr)
        assert summary["hard_failures"] == 0, (name, proc.stderr)
        assert summary["project_complete"] is True, (name, proc.stderr)
        assert proc.returncode == 0, (name, proc.returncode, proc.stderr)


def test_forbidden_patterns_many_hits_collapse_to_one_line(tmp_path):
    draft = clean_draft(p1_text=f"X X X X X {FN_PH}")
    root = _pattern_root(
        tmp_path,
        [{"id": "x-marker", "pattern": "X", "message": "marker left in"}],
        draft=draft,
    )
    proc = run_final_audit(root)
    lines = _style_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "(hits=5)" in lines[0]


def test_forbidden_patterns_zero_width_pattern_terminates_and_counts(tmp_path):
    """A zero-width pattern must not spin, and its count must be the one the
    schema promises: `finditer`'s non-overlapping matches, which for an
    always-empty match is one per position plus one at the end.

    Asserting only that SOME warning appeared would stay green under an
    implementation that reported every zero-width leaf as `hits=1`, which the
    ordinary counting test cannot see either."""
    # `(?=[ac])` matches zero-width at exactly two of the five positions in
    # "abcd". Deliberately NOT a match-everywhere pattern: that would make the
    # expected count len(text)+1, which coincides with the wrong shortcut "a
    # zero-width pattern hits once per position", so an implementation using
    # that shortcut would pass. This fixture kills that shortcut specifically;
    # it does not uniquely prove non-overlapping semantics, which an
    # every-position probe would also satisfy here.
    body = "abcd"
    draft = clean_draft(p1_text=body)
    draft["footnotes"] = {}
    draft["blocks"]["vblockA"] = V_PH_A
    draft["blocks"]["vblockB"] = V_PH_B
    root = _pattern_root(
        tmp_path,
        [{"id": "zero-width", "pattern": "(?=[ac])", "message": "zero width"}],
        draft=draft,
    )
    proc = run_final_audit(root, timeout=90)
    line = [ln for ln in _style_lines(proc) if "in blocks['p1']" in ln]
    assert len(line) == 1, proc.stderr
    assert "(hits=2)" in line[0], line[0]


def test_forbidden_patterns_count_does_not_retain_every_match(tmp_path):
    """A zero-width rule yields one hit per position, and a draft leaf has no
    length cap, so retaining a Match per hit is not a micro-cost: measured on a
    1 000 000-character leaf the materialized list peaks at ~122 MiB against
    ~0 for a streaming count, and under a constrained address space that is a
    MemoryError raised from an ADVISORY check -- aborting W7 before it emits
    its summary, exactly as the uncompilable-pattern traceback once did.

    Drives the SHIPPED warn_forbidden_patterns() rather than re-creating its
    loop here: a test that mirrors the implementation measures the mirror, and
    would stay green while the shipped code went back to materializing. The
    module is loaded in-process and re-anchored at a tmp segments dir, because
    a leaf this size in a full durable-root subprocess fixture would cost
    minutes for no extra coverage.

    tracemalloc measures the allocation the fix removes, with a threshold an
    order of magnitude below the materialized peak, so it fails on a regression
    without being sensitive to interpreter noise."""
    import tracemalloc

    fa = load_final_audit_module()
    text = "a" * 200_000
    segments = tmp_path / "segments"
    segments.mkdir()
    (segments / "seg01.draft.json").write_text(
        json.dumps({"seg": "seg01", "blocks": {"p1": text}, "footnotes": {}, "verses": {}}),
        encoding="utf-8",
    )
    fa.SEGMENTS_DIR = segments

    compiled, decl_warns = fa.compile_forbidden_patterns(
        [{"id": "zero-width", "pattern": "(?=)", "message": "zero width"}]
    )
    assert decl_warns == []
    assert len(compiled) == 1

    tracemalloc.start()
    try:
        warns = fa.warn_forbidden_patterns("seg01", compiled)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert len(warns) == 1, warns
    assert f"(hits={len(text) + 1})" in warns[0], warns[0]
    assert "blocks['p1']" in warns[0], warns[0]
    # Materializing every Match allocates ~24 MiB at this size; streaming stays
    # in the kilobytes, and the 200 000-character leaf itself is already
    # allocated before the measurement starts. Anything under a megabyte
    # proves the per-match list is gone.
    assert peak < 1_000_000, peak


def test_forbidden_patterns_newlines_never_split_a_warn_line(tmp_path):
    """Three independent line-break sources -- the operator's `message`, the
    matched snippet, and a draft KEY inside the path label -- must each still
    yield exactly ONE physical line, because main() prints each warning
    string raw. Also pins that a key containing a dot renders distinctly from
    the equivalent nesting."""
    draft = clean_draft(p1_text=f"before\nTRIGGER\nafter {FN_PH}")
    draft["verses"]["vA"]["od\ndd"] = "TRIGGER in a newline key"
    draft["verses"]["vA"]["a.b"] = "TRIGGER in a dotted key"
    draft["verses"]["vA"]["a"] = {"b": "TRIGGER in real nesting"}
    root = _pattern_root(
        tmp_path,
        [{"id": "trig", "pattern": "TRIGGER", "message": "a message\nwith a newline"}],
        draft=draft,
    )
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    lines = _style_lines(proc)
    assert len(lines) == 4, proc.stderr

    # The load-bearing assertion: every physical line of the WARN section
    # still carries the bullet, and there are exactly `warnings` of them. A
    # warning split across physical lines leaves a continuation line without
    # one, which counting STYLE-PATTERN markers cannot detect.
    block = _warn_block_lines(proc)
    assert len(block) == summary["warnings"] == 4, proc.stderr
    assert all(ln.startswith("  \u2022 ") for ln in block), proc.stderr

    paths = sorted(ln.split(" in ", 1)[1].split(":", 1)[0] for ln in lines)
    assert "verses['vA']['a.b']" in paths
    assert "verses['vA']['a']['b']" in paths
    assert len(set(paths)) == 4, paths




# ---------------------------------------------------------------------------
# 9. WARN 6 -- term-consistency (#199).
#
# The plugin ships NO terms, so every case here declares its own. The office
# under test is the one the issue was filed for: the Ancien-Régime court
# `président`, rendered `президент` in the body of a delivered volume and
# `председатель` in two of its footnotes.
#
# Every assertion runs the REAL script as a subprocess (run_final_audit), so
# each is simultaneously the wiring test: deleting the `warn_term_drift(...)`
# call from main() turns them red. That deletion was watched failing, as was
# making `declared_terms()` return [] unconditionally.
#
# `PRESIDENT`/`PRESIDENT_RU` are spelled with explicit escapes rather than
# literal characters where a case or normalization form is the POINT of the
# test, so a test cannot silently pass because an editor normalized the file.
# ---------------------------------------------------------------------------

# Built through unicodedata rather than typed as two literals: an editor or a
# copy-paste that normalized this file would silently make the two spellings
# identical, and the NFC test below would then pass while asserting nothing.
PRESIDENT_FR = unicodedata.normalize("NFC", "pr\u00e9sident")
PRESIDENT_FR_NFD = unicodedata.normalize("NFD", PRESIDENT_FR)
PRESIDENT_RU = "президент"
CHAIRMAN_RU = "председатель"
PRESIDENT_PIN = [{"source_form": PRESIDENT_FR, "target_form": PRESIDENT_RU}]


def _term_lines(proc):
    """The TERM-DRIFT lines -- but only after proving the lane actually RAN.

    Every negative case in this section asserts an EMPTY list, and a run that
    died in a traceback satisfies that too, for entirely the wrong reason. Both
    guards belong HERE rather than in each test: the unconditional
    `TERM CONSISTENCY:` line is emitted at the end of main()'s warning report,
    so its presence proves the lane reached the end of that pass, and the
    traceback check catches a death anywhere earlier."""
    assert "Traceback" not in proc.stderr, proc.stderr
    _term_count_line(proc)   # asserts exactly one; one place knows the prefix
    return [ln for ln in proc.stderr.splitlines() if "TERM-DRIFT" in ln]


def _term_count_line(proc):
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("TERM CONSISTENCY:")]
    assert len(lines) == 1, proc.stderr
    return lines[0]


def _office_segpack(block_source=None, footnote_source=None, **kwargs):
    """A clean segpack whose p1 block and footnote 1 both carry the office in
    the SOURCE language. `plain_text` is used rather than `source_html` because
    that is what every schema-valid manifest block actually carries; the
    de-tagging fallback gets its own test below."""
    segpack = clean_segpack(**kwargs)
    for block in segpack["blocks"]:
        if block["id"] == "p1":
            block.pop("source_html", None)
            block["plain_text"] = block_source if block_source is not None else (
                f"Le {PRESIDENT_FR} de la chambre des comptes {FN_PH} y siegeait."
            )
    segpack["footnotes"][0]["source_text"] = footnote_source if footnote_source is not None else (
        f"Le {PRESIDENT_FR} des enquetes, magistrat de la cour souveraine."
    )
    return segpack


def _office_root(tmp_path, terms=PRESIDENT_PIN, block_target=None, footnote_target=None,
                 segpack=None, **kwargs):
    root = make_durable_root(
        tmp_path, seg_ids=("seg01", PAD_SEG), terms=terms, **kwargs
    )
    draft = clean_draft(
        p1_text=block_target if block_target is not None else (
            f"На посту {PRESIDENT_RU}а "
            f"счётной палаты "
            f"{FN_PH} он заседал."
        ),
        extra_footnotes={"1": footnote_target} if footnote_target is not None else None,
    )
    add_converged_segment(root, "seg01", segpack or _office_segpack(), draft)
    return root


def test_terms_absent_adds_no_warning_but_still_reports_zero(tmp_path):
    """The compatibility case: a profile.yml predating #199 has no key at all.
    The lane must add nothing -- and must still SAY it checked nothing, which is
    the whole point of the count line."""
    root = _office_root(tmp_path, terms=None)
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert _term_lines(proc) == []
    assert summary["warnings"] == 0, proc.stderr
    assert _term_count_line(proc) == (
        "TERM CONSISTENCY: 0 declared term(s) checked over 1 converged segment(s)"
    )


def test_terms_declared_empty_matches_absent(tmp_path):
    """Declared-empty and absent must be indistinguishable. Compared on the
    parsed summary with `generated_at` dropped -- the script stamps a fresh
    timestamp every run -- plus the stderr WARN section and the count line."""
    absent = run_final_audit(_office_root(tmp_path / "a", terms=None))
    empty = run_final_audit(_office_root(tmp_path / "b", terms=[]))

    def comparable(proc):
        summary = dict(parse_summary(proc))
        summary.pop("generated_at")
        return summary

    assert comparable(absent) == comparable(empty)
    assert _term_lines(absent) == _term_lines(empty) == []
    assert _term_count_line(absent) == _term_count_line(empty)
    assert absent.returncode == empty.returncode


def test_declared_term_reports_the_footnote_a_correct_body_would_mask(tmp_path):
    """THE case #199 was filed for, and the reason this check is carrier-local
    rather than segment-wide.

    The source block and the source footnote both carry the office. The draft
    block renders it `президент`; the draft footnote renders it `председатель`.
    A whole-draft "contains zero occurrences" test sees the block's correct
    rendering and stays silent -- which is exactly how the real volume shipped.
    Carrier-local absence names the footnote and NOT the block."""
    root = _office_root(
        tmp_path,
        footnote_target=(
            f"{CHAIRMAN_RU}м следственной "
            f"палаты."
        ),
    )
    # The ordinary prose-only book: no `verse` key in the manifest at all, so
    # the verse index is empty. Blocks and footnotes must still compare -- an
    # empty index is a missing SOURCE for verses, never a switched-off lane.
    assert "verse" not in json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    line = lines[0]
    assert "[seg01]" in line
    assert "footnotes['1']" in line
    assert "blocks[" not in line
    assert PRESIDENT_FR in line
    assert PRESIDENT_RU in line
    assert summary["warnings"] == 1
    assert _term_count_line(proc).startswith("TERM CONSISTENCY: 1 declared term(s)")


def test_every_carrier_renders_the_pin_produces_no_warning(tmp_path):
    root = _office_root(
        tmp_path,
        footnote_target=(
            f"{PRESIDENT_RU}ом следственной "
            f"палаты."
        ),
    )
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr
    assert parse_summary(proc)["warnings"] == 0


def test_a_carrier_whose_source_lacks_the_term_never_warns(tmp_path):
    """The source side gates everything: a carrier that never mentions the
    office cannot warn, however its translation reads."""
    root = _office_root(
        tmp_path,
        segpack=_office_segpack(
            footnote_source="Une note sans aucune charge de justice."
        ),
        footnote_target=f"{CHAIRMAN_RU} чего-то.",
    )
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_a_sentence_initial_source_form_still_reaches_the_check(tmp_path):
    """Source-side casefolding, asserted in the direction that can FAIL.

    An earlier version of this test asserted SILENCE on a sentence-initial
    `Président` whose translation was correct -- and silence is exactly what
    dropping the casefold also produces, because the pin then stops matching
    the source at all. Mutation caught it: the assertion has to be that the
    capitalized source form WARNS when its carrier does not render the pin."""
    root = _office_root(
        tmp_path,
        segpack=_office_segpack(
            footnote_source=f"{PRESIDENT_FR.capitalize()} des requetes, dit-on."
        ),
        footnote_target=f"{CHAIRMAN_RU} палаты прошений.",
    )
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "footnotes['1']" in lines[0]


def test_a_suffixed_and_title_cased_target_both_satisfy_the_pin(tmp_path):
    """Target-side, and the two properties that make a pin usable at all.

    The pinned form is a STEM and the draft carries it INFLECTED
    (`президентом`) and TITLE-CASED at the head of the sentence. Both must
    count, so this asserts silence -- and unlike the source direction that is
    non-vacuous here: dropping either the substring match or the casefold makes
    the pin stop matching and a warning APPEAR."""
    root = _office_root(
        tmp_path,
        footnote_target=(
            f"{PRESIDENT_RU.capitalize()}ом Палаты прошений он и остался."
        ),
    )
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_decomposed_source_matches_a_precomposed_declaration(tmp_path):
    """Extraction's own normalize_text() only collapses whitespace, so a
    decomposed `é` reaches W7 as different bytes from a precomposed one. Without
    the NFC pass this is a silent miss on input that looks identical."""
    assert PRESIDENT_FR != PRESIDENT_FR_NFD  # the fixture is only meaningful if so
    root = _office_root(
        tmp_path,
        segpack=_office_segpack(
            footnote_source=f"Le {PRESIDENT_FR_NFD} des enquetes."
        ),
        footnote_target=f"{CHAIRMAN_RU} палаты.",
    )
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "footnotes['1']" in lines[0]


def test_html_only_carrier_counts_visible_text_not_markup(tmp_path):
    """A segpack block carrying `source_html` and no `plain_text` is counted on
    what a reader sees. The office sits in a TAG ATTRIBUTE only, so it is not an
    occurrence -- counting raw markup would warn about a word that appears
    nowhere in the book."""
    segpack = clean_segpack()
    for block in segpack["blocks"]:
        if block["id"] == "p1":
            block["source_html"] = (
                f'<p title="{PRESIDENT_FR}">Un magistrat quelconque {FN_PH}.</p>'
            )
    segpack["footnotes"][0]["source_text"] = "Une note en francais."
    # The draft deliberately does NOT render the pin. An earlier fixture left
    # the default translation in place, which carries `президент` and made the
    # case vacuous -- counting raw markup produced silence too, because the
    # target satisfied the pin anyway. With the pin absent from the draft, the
    # ONLY thing keeping this silent is that the attribute is not visible text.
    root = _office_root(
        tmp_path, segpack=segpack,
        block_target=f"Некий судейский чиновник {FN_PH}.",
    )
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr

    # ...and the converse, on the same de-tagged projection: the office in
    # ELEMENT TEXT is an occurrence, so the same draft now warns.
    segpack2 = clean_segpack()
    for block in segpack2["blocks"]:
        if block["id"] == "p1":
            block["source_html"] = f"<p>Le <em>{PRESIDENT_FR}</em> y siegeait {FN_PH}.</p>"
    segpack2["footnotes"][0]["source_text"] = "Une note en francais."
    root2 = _office_root(
        tmp_path / "b", segpack=segpack2,
        block_target=f"Он заседал {FN_PH}.",
    )
    proc2 = run_final_audit(root2)
    lines = _term_lines(proc2)
    assert len(lines) == 1, proc2.stderr
    assert "blocks['p1']" in lines[0]


# --- the verse lane, and the two policy exclusions --------------------------
#
# A verse's SOURCE text is the one carrier that does NOT live in the segpack:
# extraction substitutes a placeholder into the owning block and keeps the poem
# in manifest.json's `verse.store[]`. These cases drive that path end to end.

VERSE_STORE_WITH_OFFICE = [
    {
        "vid": "vA",
        "placeholder": V_PH_A,
        "context": "body",
        "parent_block": "vblockA",
        "plain_text": f"Le {PRESIDENT_FR} passe en robe rouge\nDeuxieme ligne du poeme",
        "sha1": "0" * 40,
    },
]


def _verse_root(tmp_path, verse_a=None, segpack=None,
               verse_store=None, **kwargs):
    """A root whose verse vA's SOURCE carries the office. `verse_a` is the
    draft's own `verses["vA"]` object, so a case can control exactly which
    fields carry the pin; `segpack` and `verse_store` override the two inputs
    the verse lane reads, the same shape `_office_root` already has."""
    root = make_durable_root(
        tmp_path, seg_ids=("seg01", PAD_SEG), terms=PRESIDENT_PIN,
        verse_store=VERSE_STORE_WITH_OFFICE if verse_store is None else verse_store,
        **kwargs
    )
    draft = clean_draft()
    if verse_a is not None:
        draft["verses"]["vA"] = verse_a
    add_converged_segment(root, "seg01", segpack or clean_segpack(), draft)
    return root


def test_verse_carrier_warns_when_no_delivered_field_carries_the_pin(tmp_path):
    """Round 2's MAJOR: excluding verses outright drops a translated carrier
    under all five non-`skip` modes, so a term of art inside a verse could drift
    into the delivered book unreported."""
    root = _verse_root(tmp_path, verse_a={
        "rendered": f"{CHAIRMAN_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": "Дословно: глава палаты идёт в красном одеянии",
    })
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    # ONE PER DELIVERED FIELD: both reach the reader and both drifted.
    assert len(lines) == 2, proc.stderr
    assert {"rendered", "literal_gloss"} == {
        ln.split("verses['vA'].")[1].split(":")[0] for ln in lines
    }


def test_verse_carrier_is_quiet_when_EVERY_delivered_field_carries_the_pin(tmp_path):
    """Every delivered field, not any -- see
    test_one_verse_field_cannot_satisfy_the_pin_for_the_other for why."""
    root = _verse_root(tmp_path, verse_a={
        "rendered": f"{PRESIDENT_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": f"Дословно: {PRESIDENT_RU} идёт в красном одеянии",
    })
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_a_non_delivered_verse_field_cannot_suppress_the_warning(tmp_path):
    """Round 3's MAJOR, and the one place this check deliberately parts from
    WARN 5's every-string-leaf scan.

    WARN 5 asks "did the translator write something BANNED?", where a superset
    of fields can only over-report. This one asks "is the pin PRESENT?", where a
    superset lets ANY extra field suppress the warning. Here `rendered` ships
    the wrong term while an ignored field holds the pin: a string-leaf scan
    finds it and stays silent, and the book ships the wrong word."""
    root = _verse_root(tmp_path, verse_a={
        "rendered": f"{CHAIRMAN_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": "Дословно: глава палаты идёт в красном одеянии",
        "translator_note": f"передано как {PRESIDENT_RU} в основном тексте",
    })
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 2, proc.stderr
    assert not any("translator_note" in ln for ln in lines), proc.stderr


def test_a_second_drifted_occurrence_in_one_carrier_is_reported(tmp_path):
    """Review found this false-green in the shipped first cut: the rule was
    carrier-local ABSENCE, so one correct occurrence made the whole carrier
    clean. The source footnote names the office TWICE and the translation
    renders it `президент ... председатель` -- the second occurrence is exactly
    the drift this lane exists to expose, and absence-of-the-pin cannot see it.
    The count comparison can."""
    root = _office_root(
        tmp_path,
        segpack=_office_segpack(
            footnote_source=(
                f"Le {PRESIDENT_FR} des enquetes et le "
                f"{PRESIDENT_FR} des requetes."
            )
        ),
        footnote_target=(
            f"{PRESIDENT_RU} следственной палаты и "
            f"{CHAIRMAN_RU} Палаты прошений."
        ),
    )
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "footnotes['1']" in lines[0]
    # Both counts are reported: the shortfall is the finding, and an operator
    # needs to know it is 2-vs-1 rather than 2-vs-0.
    assert "2x" in lines[0] and "1x" in lines[0]


def test_one_verse_field_cannot_satisfy_the_pin_for_the_other(tmp_path):
    """The same masking, one level down, and the reason a verse contributes one
    carrier PER delivered field rather than one carrier holding both.

    Under `full_rhymed_plus_literal` BOTH `rendered` and `literal_gloss` reach
    the reader. Concatenating them made a correct `rendered` cover a drifted
    `literal_gloss`; each is now judged on its own."""
    root = _verse_root(tmp_path, verse_a={
        "rendered": f"{PRESIDENT_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": f"Дословно: {CHAIRMAN_RU} идёт в красном одеянии",
    })
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "verses['vA'].literal_gloss" in lines[0]
    assert "verses['vA'].rendered" not in lines[0]


def test_verse_mode_skip_excludes_verse_carriers(tmp_path):
    """`skip` passes verse content through as-is while still requiring every
    verse KEY to be present, so an unexcluded project would warn on every verse
    for doing exactly what its own policy asks."""
    root = _verse_root(
        tmp_path,
        verse_mode="skip",
        verse_a={"rendered": f"Le {PRESIDENT_FR} passe en robe rouge\nDeuxieme ligne"},
    )
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_a_standalone_verse_block_is_not_compared_as_prose(tmp_path):
    """Extraction leaves the original poem in a `mount:"block"` verse's segpack
    BLOCK while validate_draft.py requires its draft block to be ONLY the verse
    placeholder. Comparing that block would warn on every pinned term the poem
    contains even when the verse itself renders it correctly."""
    segpack = clean_segpack(
        vblockA_source=f"<p>Le {PRESIDENT_FR} passe en robe rouge<br/>Deuxieme ligne</p>"
    )
    root = _verse_root(tmp_path, segpack=segpack, verse_a={
        "rendered": f"{PRESIDENT_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": f"Дословно: {PRESIDENT_RU} идёт в красном одеянии",
    })
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_preserve_source_excludes_footnote_carriers_but_not_blocks(tmp_path):
    """`preserve_source` carries footnote definitions through UNTRANSLATED, so
    every such definition would otherwise warn. The exclusion is scoped to
    footnotes: a block in the SAME segment still warns, which is what proves the
    policy did not switch the whole lane off."""
    root = _office_root(
        tmp_path,
        apparatus_policy="preserve_source",
        block_target=f"Он заседал в счётной палате {FN_PH}.",
        footnote_target=f"Le {PRESIDENT_FR} des enquetes, magistrat.",
    )
    proc = run_final_audit(root)
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "blocks['p1']" in lines[0]
    assert "footnotes[" not in lines[0]


def test_a_duplicate_manifest_vid_is_dropped_rather_than_mis_attributed(tmp_path):
    """`vid` is book-global, but neither manifest.schema.json nor the W2
    derivable check rejects a duplicate -- only assemble.py does, much later.
    Keeping the last writer would compare ONE segment's verse source against
    ANOTHER segment's draft, and a silently mis-attributed comparison is worse
    than none in a lane whose contract is to be quiet on inputs it cannot
    trust."""
    # BOTH entries carry the office, deliberately. An earlier fixture gave the
    # second one innocuous text, which made the test vacuous: "keep the last
    # writer" then produced silence too, for the wrong reason. With the office
    # in both, ANY resolution -- first-wins or last-wins -- warns, and only
    # dropping the ambiguous id is silent.
    duplicated = VERSE_STORE_WITH_OFFICE + [
        {
            "vid": "vA",
            "placeholder": V_PH_A,
            "context": "body",
            "parent_block": "vblockA",
            "plain_text": f"Un autre {PRESIDENT_FR} passe, en robe noire",
            "sha1": "1" * 40,
        },
    ]
    root = _verse_root(tmp_path, verse_store=duplicated, verse_a={
        "rendered": f"{CHAIRMAN_RU} проходит в красной мантии\nВторая строка",
        "literal_gloss": "Дословно: глава палаты идёт в красном одеянии",
    })
    proc = run_final_audit(root)
    assert _term_lines(proc) == [], proc.stderr


def test_a_carrier_missing_from_the_draft_is_left_to_the_hard_check(tmp_path):
    """A segpack carrier with no counterpart in the draft is NOT this lane's
    to report. Hard check 1 (coverage) already fails such a book, and saying it
    twice -- once as a structural failure, once as a term advisory -- makes the
    WARN count read as a translation defect it is not.

    Footnote 2 exists in the segpack, carries the office in its source, and has
    no `footnotes["2"]` in the draft at all. Only TERM-DRIFT lines are asserted
    on: whatever else this deliberately broken book trips is another check's
    business."""
    segpack = _office_segpack()
    segpack["footnotes"].append(
        {"n": 2, "source_text": f"Le {PRESIDENT_FR} des comptes, second renvoi."}
    )
    root = _office_root(
        tmp_path, segpack=segpack,
        footnote_target=f"{CHAIRMAN_RU}м счётной палаты.",
    )
    proc = run_final_audit(root)
    # POSITIVE, not just an absence. Footnote 1 must still be reported, which is
    # what proves the lane ran at all: an earlier version asserted only that no
    # `footnotes['2']` line appeared, and a run that CRASHED on the missing
    # carrier satisfied that too, by emitting no lines whatsoever.
    lines = _term_lines(proc)
    assert len(lines) == 1, proc.stderr
    assert "footnotes['1']" in lines[0]
    assert "footnotes['2']" not in proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr


def test_term_warnings_never_gate_a_complete_project(tmp_path):
    """The contract this lane shares with every other WARN: advisory, and it
    never touches the exit code. Deliberately built with NO padding segment, so
    the project genuinely completes and the exit code has nothing else to say."""
    root = make_durable_root(tmp_path, seg_ids=("seg01",), terms=PRESIDENT_PIN)
    add_converged_segment(
        root, "seg01", _office_segpack(),
        clean_draft(
            p1_text=f"Он заседал в счётной палате {FN_PH}.",
            extra_footnotes={"1": f"{CHAIRMAN_RU} следственной палаты."},
        ),
    )
    proc = run_final_audit(root)
    summary = parse_summary(proc)
    assert_schema_valid(summary)
    assert len(_term_lines(proc)) == 2, proc.stderr
    assert summary["warnings"] == 2
    assert summary["project_complete"] is True
    assert proc.returncode == 0, proc.stderr


def test_an_unreadable_manifest_never_makes_the_term_lane_raise(tmp_path):
    """The verse source is the one input this lane cannot get anywhere else,
    and it comes from a file the lane does not own.

    What is asserted is exactly what the lane owes and no more: a manifest it
    cannot parse produces NO traceback out of the term reader. The run still
    dies at exit 2 -- the whole-project completeness gate FATALs on an
    unreadable manifest, which is select_segments.py's contract and predates
    this check -- and because that fatal happens before main() prints its
    human-readable report, no WARN line is emitted at all. Asserting a warn here
    would be asserting the fatal path had not fired."""
    root = _office_root(
        tmp_path,
        footnote_target=f"{CHAIRMAN_RU}м следственной палаты.",
    )
    (root / "manifest.json").write_text("{ not json", encoding="utf-8")
    proc = run_final_audit(root)
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "is not valid JSON" in proc.stderr


# --- corrupt-but-valid-JSON durable data ------------------------------------
#
# A traceback out of this lane is the WORST outcome available to it: main()
# prints the two HARD verdicts and the summary JSON only after every WARN check
# has run, so an advisory crash destroys the report on exactly the broken book
# that most needs one.
#
# The SEGPACK side of the same class is deliberately NOT tested, and that is a
# measurement rather than an omission: a segpack whose `footnotes` is a mapping,
# or whose verse `parent_block` is unhashable, never reaches any WARN check --
# hard check 1 calls `validate_draft.validate()` first and dies on the identical
# input at validate_draft.py:625 and :622. That is pre-existing and out of
# scope. The `isinstance` guards in `term_carriers()` are kept anyway; they cost
# three calls, and "a lane that never raises" should not depend on which OTHER
# check happens to run before it.


def test_a_corrupt_manifest_verse_block_does_not_crash_the_lane(tmp_path):
    """`manifest.json`'s `verse` key as a LIST -- the one shape in this class
    that IS reachable, because the lane reads the manifest before the
    completeness gate that would otherwise report the corruption, and the
    manifest is not jsonschema-validated at runtime. `x or {}` does not catch
    it: a non-empty list is truthy and reaches a `.get()` that raises."""
    root = _office_root(tmp_path, footnote_target=f"{CHAIRMAN_RU}м палаты.")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["verse"] = ["wrong"]
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    proc = run_final_audit(root)
    lines = _term_lines(proc)   # asserts no traceback, and that the lane ran
    assert len(lines) == 1, proc.stderr
    assert "footnotes['1']" in lines[0]


def test_the_html_projection_is_linear_on_malformed_markup():
    """A run of `<` with no terminator must not take quadratic time.

    `<[^>]*>` restarts a full end-of-string scan at every opener: measured on
    this machine, 120 000 consecutive `<` cost 5.18s against 0.001s for
    `<[^<>]*>`. A segpack is LLM-written and hand-editable, and a stall in an
    ADVISORY lane blocks W7 before it prints the two HARD verdicts.

    Asserted on the shipped function directly rather than through a subprocess,
    and with a budget two orders of magnitude above the fixed version's cost, so
    the case is decided by the regex's complexity class and not by how loaded
    this machine happens to be. The two spellings produce IDENTICAL output on
    real markup, which is why no behavioural assertion can stand in for this
    one -- pinned here alongside the timing so a future edit cannot satisfy the
    budget by changing what the projection returns."""
    fa = load_final_audit_module()
    block = {"source_html": "<" * 120000}
    start = time.monotonic()
    fa._carrier_source_text(block)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"de-tagging 120k unterminated '<' took {elapsed:.2f}s"

    assert fa._carrier_source_text(
        {"source_html": '<p title="x">Le <em>president</em></p>'}
    ) == " Le  president  "


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
