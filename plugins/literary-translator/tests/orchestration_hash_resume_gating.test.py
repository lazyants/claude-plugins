"""tests/orchestration_hash_resume_gating.test.py

Regression-lock for #186: `orchestration_bundle_hash` is non-gating for
CONVERGENCE (never a member of cache_key.py's 15-field
`CACHE_KEY_FIELD_ORDER` composite) but IS gating for RESUME -- its marker
byte is one of resume_setup.py's `version` dict inputs (alongside
`plugin_bundle_hash` and a hash of `schemas/`), so a changed marker forces
a fresh, no-resume run even when everything else about a resume attempt is
identical. Pre-1.4.4, several restatement sites described this marker with
a flat "diagnostic-only"/"non-gating"/"provenance-only" characterization
that is TRUE for convergence but FALSE for resume. This file has two
halves:

  1. CODE TRUTH -- exercises the real, shipped `resume_setup.py` end to
     end (same fixture shape as tests/resume_integrity.test.py's
     `test_case4_changed_orchestration_bundle_hash_forces_fresh_run`,
     scoped down to this one scenario since that file already owns the
     full 12-case suite) to prove the marker gates resume, and reads
     cache_key.py's own CACHE_KEY_FIELD_ORDER to prove it never gates
     convergence. These two properties were already true pre-#186-fix --
     they document the invariant, they are not this file's red->green
     driver.
  2. DOC/COMMENT HONESTY -- the red->green driver. Asserts each of the
     nine 1.4.4-scoped restatement sites named in issue #186 no longer
     carries its pre-fix flat-diagnostic phrasing, and that each reworded
     file now mentions "resume" -- while confirming the sites that were
     ALREADY accurate (ledger-and-resumability.md's `ledger_merge.py`
     aside, resume_setup.py's own docstring, orchestration-and-batching.md's
     review_ready/resume_setup paragraph) are not spuriously flagged by the
     same string keys.

Self-run via `pytest.main([__file__])`.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_DIR / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
REFERENCES_DIR = SKILL_DIR / "references"

CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
# #538: the two bundle memberships live in DIFFERENT files -- cache_key.py
# owns PLUGIN_BUNDLE_MEMBERS, scaffold_setup.py owns
# ORCHESTRATION_BUNDLE_MEMBERS -- so checking one side says nothing about
# the other, and no single file can be read to learn which price a script's
# edit carries.
SCAFFOLD_SETUP_SCRIPT = SCRIPTS_DIR / "scaffold_setup.py"
RESUME_SETUP_SCRIPT = SCRIPTS_DIR / "resume_setup.py"
DRAFT_READY_SCRIPT = SCRIPTS_DIR / "draft_ready.py"
REVIEW_READY_SCRIPT = SCRIPTS_DIR / "review_ready.py"
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"
SKILL_MD = SKILL_DIR / "SKILL.md"
LEDGER_DOC = REFERENCES_DIR / "ledger-and-resumability.md"
ORCHESTRATION_DOC = REFERENCES_DIR / "orchestration-and-batching.md"
SCHEMA_LITERAL_DRIFT_TEST = PLUGIN_ROOT / "tests" / "schema_literal_drift.test.py"

for _p in (
    CACHE_KEY_SCRIPT, RESUME_SETUP_SCRIPT, DRAFT_READY_SCRIPT, REVIEW_READY_SCRIPT,
    SELECT_SEGMENTS_SCRIPT, SKILL_MD, LEDGER_DOC, ORCHESTRATION_DOC, SCHEMA_LITERAL_DRIFT_TEST,
):
    assert _p.is_file(), f"expected a real file at {_p}"


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cache_key_module() -> types.ModuleType:
    """Imports the real, shipped cache_key.py so its own
    CACHE_KEY_FIELD_ORDER constant can be read directly."""
    return _load_module("cache_key_under_test_orch_resume_gating", CACHE_KEY_SCRIPT)


@pytest.fixture(scope="module")
def orchestration_bundle_members() -> tuple:
    """scaffold_setup.py's ORCHESTRATION_BUNDLE_MEMBERS, read from the SOURCE
    rather than imported: that module imports cache_key at module scope, which
    is not importable from here, and this test needs the literal it declares
    and nothing else. The node must BE a tuple -- a string of the right length
    would otherwise answer membership questions character by character."""
    import ast

    tree = ast.parse(SCAFFOLD_SETUP_SCRIPT.read_text(encoding="utf-8"))
    # EVERY binding of the name anywhere in the module, not the first one:
    # reading the first assignment and stopping would let a later rebind or
    # `+=` drop select_segments.py out of the tuple the shipped module
    # actually holds while this fixture kept returning the original literal
    # and the assertions below kept passing. ast.walk rather than tree.body
    # so a binding inside a function or a conditional counts too.
    bindings = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AugAssign))
        and any(
            isinstance(t, ast.Name) and t.id == "ORCHESTRATION_BUNDLE_MEMBERS"
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    assert len(bindings) == 1, (
        f"ORCHESTRATION_BUNDLE_MEMBERS is bound {len(bindings)} times in "
        f"scaffold_setup.py -- reading a literal is only equivalent to reading "
        f"the shipped value while there is exactly one binding. Read the "
        f"effective value instead of relaxing this."
    )
    node = bindings[0]
    assert isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple), (
        "ORCHESTRATION_BUNDLE_MEMBERS is no longer a plain tuple literal"
    )
    return tuple(ast.literal_eval(node.value))


# ===========================================================================
# 1. CODE TRUTH -- documents the invariant (already true pre-fix)
# ===========================================================================


def test_orchestration_bundle_hash_never_a_cache_key_field(cache_key_module):
    """Never gates convergence: not one of the 15 composite cache_key
    fields."""
    assert "orchestration_bundle_hash" not in cache_key_module.CACHE_KEY_FIELD_ORDER


# #538. Which SIDE of this split a script sits on is what a release entry's
# "nothing re-translates" sentence rests on, and it is not something a reader
# can tell by looking at the file: select_segments.py and resume_setup.py sat
# on opposite sides of it until #446, despite being the two halves of one W5
# preflight invoked one after the other on every claim run. 1.38.0's first
# draft claimed the orchestration-only cost for BOTH, and a docstring-sized
# edit to resume_setup.py would therefore have moved plugin_bundle_hash and
# re-staled every converged segment in every project -- a whole-book
# retranslation, paid for a comment. Pinned here, on both sides, so the claim
# is checked rather than restated.
#
# #446 moved select_segments.py to the OTHER side, and this pin moved with it
# rather than being deleted: its purpose was never to keep the script out of
# the plugin bundle, it was to make a release state the true price of touching
# it. The price it now names is the dual one.
def test_select_segments_is_registered_in_BOTH_bundles(
    cache_key_module, orchestration_bundle_members
):
    assert "select_segments.py" in cache_key_module.PLUGIN_BUNDLE_MEMBERS, (
        "#446 registered the dispatch-gate owner in the plugin bundle -- a "
        "release that changes select_segments.py must price itself as moving "
        "plugin_bundle_hash AND orchestration_bundle_hash, so every converged "
        "segment reclassifies stale and the next run is a fresh RUN_ID"
    )
    assert "select_segments.py" in orchestration_bundle_members


def test_resume_setup_is_a_plugin_bundle_member_and_so_is_never_free_to_edit(cache_key_module):
    assert "resume_setup.py" in cache_key_module.PLUGIN_BUNDLE_MEMBERS, (
        "the bundle hashes this file's RAW BYTES, so no edit to it -- comment, "
        "docstring or code -- is free; a release that touches it pays a full "
        "re-stale of every converged segment and must say so"
    )


# A stub cache_key.py -- resume_setup.py shells out to the real one via
# `cache_key.py --seg <id>` to compute each mass-kind segment's composite
# itself. This stub reads a test-controlled test_fixture_cache_keys.json
# mapping and prints the requested segment's entry verbatim, the same
# pattern tests/resume_integrity.test.py uses for the same script.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

DURABLE_ROOT = Path(__file__).resolve().parents[1]
KEYS_PATH = DURABLE_ROOT / "test_fixture_cache_keys.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    args, _ = parser.parse_known_args()
    data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# The authoritative 15-field cache-key list -- used only to build a
# well-shaped fixture, never asserted against here (that's test #1's job).
CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]

# #413 -- the `subst` payload resume_setup.py's SUBST_FIELDS requires. Kept in
# tests/_resume_subst_fixture.py because this file and resume_integrity.test.py
# carried byte-identical copies of it, comment included. That module explains
# why it is NOT tests/_workflow_instantiation.py's token map, which is a
# different contract on the other side of the same step.
from _resume_subst_fixture import BASE_SUBST  # noqa: E402


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _make_resume_setup_root(tmp_path: Path) -> Path:
    """Minimal fixture durable_root -- same shape as
    tests/resume_integrity.test.py's make_resume_setup_root, scoped down to
    only what this file's single scenario needs."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(RESUME_SETUP_SCRIPT, scripts_dir / "resume_setup.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(RESUME_SETUP_SCRIPT.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    # version.schemas is a hash of the whole schemas/ dir; only needs real,
    # stable bytes to exist here, not real schema semantics.
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    _write_json(schemas_dir / "dummy.schema.json", {"type": "object"})

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / ".plugin_bundle_hash").write_text("pbh-v1", encoding="utf-8")
    (runs_dir / ".orchestration_bundle_hash").write_text("obh-A", encoding="utf-8")

    _write_json(root / "test_fixture_cache_keys.json", {
        "seg01": {field: f"{field}-s1" for field in CACHE_KEY_FIELDS},
        "seg02": {field: f"{field}-s2" for field in CACHE_KEY_FIELDS},
    })

    # LT-409: the mass-kind digest domain now comes from manifest.json's own
    # segments[] (resume_setup.py's _load_manifest_seg_ids()), matching this
    # file's own seg01/seg02 fixture pair -- same shape as
    # tests/resume_integrity.test.py's make_resume_setup_root().
    _write_json(root / "manifest.json", {"segments": [{"seg": "seg01"}, {"seg": "seg02"}]})
    return root


def _run_resume_setup(root: Path, payload_obj: dict, timeout: int = 30):
    payload_path = root / "scratch_resume_payload.json"
    _write_json(payload_path, payload_obj)
    cmd = [sys.executable, str(root / "scripts" / "resume_setup.py"), "--payload-file", str(payload_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    parsed = json.loads(lines[0]) if len(lines) == 1 else None
    return proc, parsed


def _mass_base_payload() -> dict:
    """LT-409: `args` is PINNED to {} for kind="mass" (resume_setup.py hard-
    rejects anything else); `segs` is kept, unread, to prove the
    deprecated-but-accepted field does nothing -- the digest domain now
    comes from manifest.json (written by _make_resume_setup_root())."""
    return {
        "kind": "mass",
        "args": {},
        "subst": dict(BASE_SUBST),
        "segs": ["seg01", "seg02"],
    }


def test_changed_orchestration_bundle_hash_marker_forces_fresh_run(tmp_path):
    """Gates resume: an identical resume attempt succeeds when the
    `.orchestration_bundle_hash` marker is unchanged, then is forced fresh
    (no resume) the moment ONLY that marker changes -- everything else
    (payload, plugin_bundle_hash, schemas/) held fixed."""
    root = _make_resume_setup_root(tmp_path)
    base_payload = _mass_base_payload()

    proc0, parsed0 = _run_resume_setup(root, base_payload)
    assert proc0.returncode == 0 and parsed0 and parsed0.get("success") is True, (
        f"initial setup should succeed: rc={proc0.returncode}\nstdout={proc0.stdout}\nstderr={proc0.stderr}"
    )
    run_id = parsed0["effectiveRunId"]

    resume_payload = dict(base_payload, resume_from_run_id=run_id)
    _, parsed1 = _run_resume_setup(root, resume_payload)
    assert parsed1 and parsed1.get("resume") is True and parsed1.get("effectiveRunId") == run_id, (
        f"unchanged marker should resume the prior run: {parsed1}"
    )

    (root / "runs" / ".orchestration_bundle_hash").write_text("obh-B", encoding="utf-8")
    _, parsed2 = _run_resume_setup(root, resume_payload)
    assert parsed2 and parsed2.get("resume") is False and parsed2.get("effectiveRunId") != run_id, (
        f"changed marker must force a fresh, no-resume run: {parsed2}"
    )


# ===========================================================================
# 2. DOC/COMMENT HONESTY -- the red->green driver
# ===========================================================================

# Each entry: (label, file, exact pre-fix substring that must be ABSENT).
STALE_PHRASES = [
    ("cache_key.py:129", CACHE_KEY_SCRIPT, "orchestration_bundle_hash's diagnostic-only bucket"),
    ("review_ready.py:74", REVIEW_READY_SCRIPT, "orchestration_bundle_hash's diagnostic-only bucket"),
    ("draft_ready.py:12", DRAFT_READY_SCRIPT, "which is diagnostic-only"),
    ("SKILL.md:489", SKILL_MD, "sibling, non-gating"),
    ("SKILL.md:491", SKILL_MD, "provenance-only for W8 reporting"),
    ("SKILL.md:3695-3701", SKILL_MD, "non-gating `orchestration_bundle_hash` instead"),
    ("ledger:607", LEDGER_DOC, "non-gating `orchestration_bundle_hash` instead"),
    ("ledger:719", LEDGER_DOC, "purely diagnostic/provenance"),
    ("schema_literal_drift.test.py:556-558", SCHEMA_LITERAL_DRIFT_TEST, "purely diagnostic/provenance"),
    ("orchestration-and-batching.md:224", ORCHESTRATION_DOC, "is diagnostic only, never part of the composite"),
    ("schema_literal_drift.test.py:31-32", SCHEMA_LITERAL_DRIFT_TEST, "never gated against"),
    ("select_segments.py:12", SELECT_SEGMENTS_SCRIPT, "purely diagnostic/orchestration"),
]


@pytest.mark.parametrize("label,path,phrase", STALE_PHRASES, ids=[s[0] for s in STALE_PHRASES])
def test_stale_diagnostic_only_phrase_absent(label, path, phrase):
    text = path.read_text(encoding="utf-8")
    assert phrase not in text, (
        f"{label}: pre-#186-fix phrase {phrase!r} is still present in {path} -- "
        "orchestration_bundle_hash gates RESUME even though it never gates "
        "convergence, so this flat 'diagnostic-only' framing is misleading."
    )


REWORDED_FILES_MUST_MENTION_RESUME = [
    ("cache_key.py", CACHE_KEY_SCRIPT),
    ("draft_ready.py", DRAFT_READY_SCRIPT),
    ("review_ready.py", REVIEW_READY_SCRIPT),
    ("select_segments.py", SELECT_SEGMENTS_SCRIPT),
    ("SKILL.md", SKILL_MD),
    ("ledger-and-resumability.md", LEDGER_DOC),
    ("orchestration-and-batching.md", ORCHESTRATION_DOC),
]


@pytest.mark.parametrize(
    "label,path", REWORDED_FILES_MUST_MENTION_RESUME, ids=[s[0] for s in REWORDED_FILES_MUST_MENTION_RESUME]
)
def test_reworded_site_mentions_resume(label, path):
    """Anti-vacuity: the fix isn't just deleting the stale phrase -- it
    must actually characterize orchestration_bundle_hash's resume-gating
    role, so "resume" must appear somewhere in the file (case-insensitive;
    several sites already said "resume" elsewhere pre-fix too, so this
    only proves the word survived, not that any one line changed -- the
    STALE_PHRASES absence checks above are what prove the edit happened)."""
    text = path.read_text(encoding="utf-8").lower()
    assert "resume" in text, f"{label}: expected the file to mention 'resume' somewhere"


def test_ledger_already_accurate_ledger_merge_aside_not_flagged():
    """references/ledger-and-resumability.md:168's `ledger_merge.py` aside
    ("diagnostic-only, never part of any segment's cache key") is already
    accurate -- qualified in the very next sentence by the resume-integrity
    digest note -- and must survive untouched. None of the STALE_PHRASES
    keys above match this exact clause, so a correct fix never trips it."""
    text = LEDGER_DOC.read_text(encoding="utf-8")
    accurate_clause = "diagnostic-only, never part of any segment's cache key"
    assert accurate_clause in text, (
        "ledger-and-resumability.md's already-accurate ledger_merge.py aside "
        "must stay in place, unchanged"
    )
    for _, path, phrase in STALE_PHRASES:
        if path == LEDGER_DOC:
            assert phrase not in accurate_clause, (
                f"stale-phrase key {phrase!r} incorrectly matches the "
                "already-accurate ledger_merge.py aside -- would false-flag it"
            )


def test_orchestration_batching_already_accurate_review_resume_sentence_not_flagged():
    """orchestration-and-batching.md:219's `review_ready.py`/
    `resume_setup.py` sentence ("gating members, not diagnostic-only") is
    already accurate and must survive untouched -- distinct from the
    line directly below it (158) which WAS the stale, flat
    orchestration_bundle_hash characterization this file's fix targets."""
    text = ORCHESTRATION_DOC.read_text(encoding="utf-8")
    accurate_clause = "gating members, not\ndiagnostic-only, unlike their sibling readiness/merge scripts below."
    assert accurate_clause in text, (
        "orchestration-and-batching.md's already-accurate review_ready.py/"
        "resume_setup.py sentence must stay in place, unchanged"
    )


def test_resume_setup_py_own_docstring_never_carried_the_stale_phrasing():
    """resume_setup.py:307-309 already correctly self-describes as
    correctness-gating, not diagnostic-only -- it was never one of the
    nine #186 sites, so none of the STALE_PHRASES keys should ever match
    it (a sanity guard, not evidence the fix touched this file)."""
    text = RESUME_SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "not diagnostic-only" in text, (
        "resume_setup.py's own docstring should still self-describe as "
        "correctness-gating, not diagnostic-only"
    )
    for _, _, phrase in STALE_PHRASES:
        assert phrase not in text, (
            f"resume_setup.py unexpectedly carries stale phrase {phrase!r} -- "
            "it was never one of the nine #186-scoped sites"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
