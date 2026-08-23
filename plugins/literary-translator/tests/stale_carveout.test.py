"""tests/stale_carveout.test.py -- #491 Design A: "the merge records why,
assembly reads it."

Covers plan tests 7-17 (see the #491 plan, "Design" section A):

  1. ledger_merge.py now records WHY a converged segment went stale --
     `stale_mismatched_fields`, the sorted, non-empty field-by-field diff it
     already computed and used to discard -- onto the MATERIALIZED entry
     only (tests 7-8; scripts/ledger_merge.py, schemas/ledger.schema.json).
  2. assemble.py's whole-project completeness gate carves out a 'stale'
     segment whose ENTIRE `stale_mismatched_fields` set is machinery-only
     (never the source text, style bible, prompts, canon terms, or engine
     config) AND whose `.ever_converged` sentinel is not ABSENT -- treating
     it exactly like `status=="converged"` for every check downstream,
     including the FATAL draft-sha1 guard (tests 9-17; scripts/assemble.py).

## Fixture strategy

Two self-contained harnesses, each mirroring an EXISTING sibling test
file's own conventions rather than inventing new ones (per this suite's
"each test file stays self-contained" convention -- duplicated here, not
imported):

  - ledger_merge.py tests (7-8) reuse tests/ledger_merge.test.py's own
    pattern: copy the REAL ledger_merge.py + assets/schemas/*.schema.json
    into an isolated tmp_path root, install a fake cache_key.py stub (same
    `--seg <id>` -> JSON-object stdout interface as the real one), and
    invoke the real script as a subprocess.
  - assemble.py tests (9-16) reuse tests/assemble.test.py's own pattern:
    copy the REAL assemble.py + its siblings (output_resolve.py,
    render_obsidian.py, validate_draft.py) into an isolated tmp_path root
    with a real profile.yml, and invoke it as a subprocess -- except test
    15, which calls `load_converged_segments()` directly (via a SEPARATE
    importlib-loaded copy, still self-anchored to its own tmp_path) for
    speed, since it drives ~8 table rows against a pure function that
    touches only `runs/ledger.json` (a plain dict) + one sentinel path +
    one draft file, none of which need a full manifest/segpack/NodeStream
    build to exercise.

Tests 15 and 17 also load final_audit.py and select_segments.py directly,
IN PLACE (never a copy -- they are read-only accesses to a pure function
and to plain module-level constants, matching tests/final_audit.test.py's
own `load_final_audit_module()` convention for "durable-root-independent"
helpers).
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
# #492: assemble.py imports cache_key.py as a third sibling and recomputes
# every content-affecting field from the live root.
CACHE_KEY_REAL_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"

for _src in (
    LEDGER_MERGE_SRC, ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC,
    VALIDATE_DRAFT_SRC, FINAL_AUDIT_SRC, SELECT_SEGMENTS_SRC,
):
    assert _src.is_file(), f"required source not found: {_src}"
assert SCHEMAS_SRC_DIR.is_dir(), f"schemas dir not found: {SCHEMAS_SRC_DIR}"


def _load_module_from_source(src_path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, src_path)
    assert spec is not None and spec.loader is not None, f"cannot load spec for {src_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_real_final_audit_module():
    """final_audit.py loaded IN PLACE (never a copy) -- only used here to
    call count_stale_previously_converged(classification, sentinel_states=
    ...) with an explicit, test-supplied sentinel_states dict, which never
    touches SEGMENTS_DIR (no filesystem read at all), and to read
    classify_ever_converged_sentinel()/SAFE_STALE_CARVEOUT_FIELDS -- both
    pure. Mirrors tests/final_audit.test.py's own
    load_final_audit_module()."""
    return _load_module_from_source(FINAL_AUDIT_SRC, "stale_carveout__final_audit_ref")


def load_real_select_segments_module():
    """select_segments.py loaded IN PLACE -- only used to read the module-
    level MACHINERY_ONLY_CACHE_KEY_FIELDS constant (test 17)."""
    return _load_module_from_source(SELECT_SEGMENTS_SRC, "stale_carveout__select_segments_ref")


def load_real_assemble_module():
    """assemble.py loaded IN PLACE -- only used to read the module-level
    SAFE_STALE_CARVEOUT_FIELDS constant (test 17); every BEHAVIORAL test
    below uses either the real subprocess or a self-anchored COPY (see
    load_assemble_module), never this loader."""
    return _load_module_from_source(ASSEMBLE_SRC, "stale_carveout__assemble_ref")


# ===========================================================================
# ledger_merge.py fixture harness (mirrors tests/ledger_merge.test.py)
# ===========================================================================

CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]

FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_durable_root(tmp_path, name="durable_root"):
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")
    (root / "runs" / "ledger.d").mkdir(parents=True)
    return root


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return frag_path


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def run_merge(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def converged_fragment(cache_key, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 0,
        "reviewed_draft_sha1": "d" * 40,
    }


def load_ledger_merge_module(root):
    """The COPY inside `root/scripts/` (self-anchored SCHEMAS_DIR resolves
    to `root/schemas`) -- used only to reach `_build_schema_registry()` /
    `_validator_for()`, the REAL schema-loading machinery ledger_merge.py
    itself uses, so the negative-control check in test 7 drives the actual
    implementation rather than a hand-rolled reimplementation of it."""
    return _load_module_from_source(
        root / "scripts" / "ledger_merge.py", f"stale_carveout__ledger_merge_{id(root)}"
    )


# ===========================================================================
# 7-8. ledger_merge.py records WHY, on the materialized entry only.
# ===========================================================================


def test_ledger_merge_writes_stale_mismatched_fields_on_materialized_entry_only(tmp_path):
    """Plan test 7. Mutations: write it to the fragment -> red on the
    fragment assertion below; declare the property on
    ledger-record-base.schema.json instead of ledger.schema.json -> red on
    the negative control below, which is mandatory per the plan -- without
    it, the relocation mutant is invisible (the materialized entry still
    validates via $ref either way, and the fragment on disk never carries
    the field regardless of WHERE it's declared, since merge() only ever
    copies the fragment's own record and writes ledger.json)."""
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = dict(stored_key)
    current_key["plugin_bundle_hash"] = "plugin_bundle_hash-DIFFERENT"
    current_key["schema_hash"] = "schema_hash-DIFFERENT"
    write_fixture_cache_keys(root, {"seg01": current_key})
    frag_path = write_fragment(root, "seg01", converged_fragment(stored_key))
    fragment_bytes_before = frag_path.read_bytes()

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"]

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert doc["segments"]["seg01"]["status"] == "stale"
    assert doc["segments"]["seg01"]["stale_mismatched_fields"] == [
        "plugin_bundle_hash",
        "schema_hash",
    ], "must be sorted and contain exactly the fields that differ"

    assert frag_path.read_bytes() == fragment_bytes_before, (
        "the on-disk fragment must never be rewritten"
    )
    frag_doc = json.loads(frag_path.read_text(encoding="utf-8"))
    assert "stale_mismatched_fields" not in frag_doc, (
        "the fragment must never carry stale_mismatched_fields -- only the "
        "materialized entry does"
    )

    # NEGATIVE CONTROL (mandatory -- see docstring above).
    merge_mod = load_ledger_merge_module(root)
    registry = merge_mod._build_schema_registry(root / "schemas")
    fragment_validator = merge_mod._validator_for(
        "ledger-fragment.schema.json", registry, root / "schemas"
    )
    poisoned_fragment = dict(converged_fragment(stored_key))
    poisoned_fragment["stale_mismatched_fields"] = ["plugin_bundle_hash"]
    errors = list(fragment_validator.iter_errors(poisoned_fragment))
    assert errors, (
        "a fragment carrying stale_mismatched_fields must be REJECTED by "
        "ledger-fragment.schema.json (unevaluatedProperties: false) -- if "
        "this passes, the property drifted onto ledger-record-base.schema."
        "json (which fragments also compose via $ref) instead of staying "
        "on ledger.schema.json alone"
    )


def test_ledger_schema_accepts_stale_mismatched_fields_on_a_stale_entry(tmp_path):
    """Plan test 8. Mutation: omit the schema property -> every merge over
    a mismatching converged fragment fails schema validation (`unevaluated
    Properties: false` rejects the new field) -> red, both here directly
    against the real validator and via test 7's own returncode==0
    assertion above."""
    root = make_durable_root(tmp_path)
    merge_mod = load_ledger_merge_module(root)
    registry = merge_mod._build_schema_registry(root / "schemas")
    ledger_validator = merge_mod._validator_for("ledger.schema.json", registry, root / "schemas")

    doc = {
        "segments": {
            "seg01": {
                "timestamp": "2026-01-01T00:00:00Z",
                "status": "stale",
                "rounds": 1,
                "cache_key": make_cache_key("X"),
                "n_blocks": 1,
                "n_footnotes": 0,
                "n_verses": 0,
                "reviewed_draft_sha1": "d" * 40,
                "stale_mismatched_fields": ["plugin_bundle_hash", "schema_hash"],
            }
        }
    }
    errors = sorted(ledger_validator.iter_errors(doc), key=lambda e: [str(p) for p in e.path])
    assert not errors, (
        f"ledger.schema.json rejected a valid stale_mismatched_fields entry: "
        f"{[e.message for e in errors]}"
    )


def test_missing_cache_key_on_a_converged_fragment_does_not_fabricate_stale_mismatched_fields(tmp_path):
    """Real-data edge case, from a census of a live affected book: a
    'converged' fragment can carry NO `cache_key` at all -- one live
    record's own keys are only reason/rounds/status/timestamp. A naive
    stored-vs-current comparison over CACHE_KEY_FIELDS against an ABSENT
    stored key would read as "all 15 fields differ", an artifact of
    comparing against nothing, not real drift -- and if that fabricated
    list ever reached `stale_mismatched_fields`, the carve-out would refuse
    for the WRONG reason (a bogus 15-field mismatch, naming zero real
    fields) rather than the true one (no baseline to compare against at
    all).

    ledger_merge.py's own `isinstance(stored_key, dict)` guard
    (`_compute_stale_segments`, right after `stored_key =
    record.get("cache_key")`) already short-circuits with `stale.add(seg);
    continue` BEFORE the field-by-field diff runs -- LOAD-BEARING for
    `stale_mismatched_fields` too: the `mismatched_fields[seg] = moved`
    write lives only inside the later branch that computes a real
    `current_key`, which this record's own `continue` never reaches. End
    to end via the real merge subprocess: omitting `cache_key` entirely is
    schema-valid once status flips to 'stale' (ledger-record-base.schema.
    json's `cache_key` is unconditionally optional; only status=="converged"
    requires it, and the MATERIALIZED status here is "stale").

    Mutation: paired with this file's assembly-side test right after test
    13 below, which is where the SAME real bug would actually cost
    something (a silent carve-out of an unreviewed segment)."""
    root = make_durable_root(tmp_path)
    # A real, resolvable current key -- IRRELEVANT on the correct code path
    # (the guard's `continue` never reaches the subprocess call at all), but
    # load-bearing for the mutation: without it, a mutant that removes the
    # early `continue` would still fail this test, just for the WRONG
    # reason (the subprocess erroring on a missing fixture file, not the
    # fabricated-15-fields bug this test exists to catch).
    write_fixture_cache_keys(root, {"seg01": make_cache_key("current")})
    fragment = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "reason": "pre-existing anomalous record with no recorded cache_key",
        "rounds": 1,
    }
    write_fragment(root, "seg01", fragment)

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"], (
        "an absent stored cache_key must still surface as stale (cannot "
        "confirm it's still current) -- matches the existing, unchanged "
        "stale.add(seg) branch"
    )

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    entry = doc["segments"]["seg01"]
    assert entry["status"] == "stale"
    assert "stale_mismatched_fields" not in entry, (
        f"must NOT fabricate a stale_mismatched_fields list (a bogus 'all "
        f"15 fields differ' among them) from comparing against an absent "
        f"stored key -- got {entry.get('stale_mismatched_fields')!r}"
    )


def test_non_dict_cache_key_does_not_fabricate_stale_mismatched_fields(tmp_path):
    """The OTHER shape the same `isinstance(stored_key, dict)` guard covers:
    `cache_key` present but not a dict (e.g. a stray string). Unit-level,
    calling `_compute_stale_segments()` directly, rather than through the
    full merge subprocess like the sibling test above: a non-dict
    `cache_key` value fails ledger.schema.json's OWN `cache_key: {"type":
    "object", ...}` constraint on the MATERIALIZED entry regardless of
    status (that property, when present at all, is unconditionally
    required to be an object) -- so the full pipeline would correctly
    refuse to write ledger.json at all, for a reason that has nothing to do
    with this test's actual concern. Calling the function directly isolates
    exactly the claim being pinned: this shape must not fabricate
    `stale_mismatched_fields` either."""
    root = make_durable_root(tmp_path)
    merge_mod = load_ledger_merge_module(root)
    # Load-bearing for the mutation, not the correct code path -- see the
    # sibling test's own comment on its identical write_fixture_cache_keys
    # call.
    write_fixture_cache_keys(root, {"seg01": make_cache_key("current")})
    fragments = {
        "seg01": {
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "converged",
            "cache_key": "not-a-dict",
            "rounds": 1,
        }
    }
    stale, mismatched_fields = merge_mod._compute_stale_segments(
        fragments,
        skip_stale_check=False,
        cache_key_script=root / "scripts" / "cache_key.py",
        durable_root=root,
    )
    assert stale == {"seg01"}, "a non-dict stored cache_key must still surface as stale"
    assert "seg01" not in mismatched_fields, (
        f"must NOT fabricate a stale_mismatched_fields entry from comparing "
        f"against a non-dict stored key -- got {mismatched_fields.get('seg01')!r}"
    )


# ===========================================================================
# assemble.py fixture harness (mirrors tests/assemble.test.py)
# ===========================================================================

DUMMY_CACHE_KEY = {
    "input_sha1": "a" * 40,
    "style_contract_hash": "b" * 40,
    "used_terms_hash": "c" * 40,
    "pipeline_version": "v1",
    "schema_hash": "d" * 40,
    "prompt_hash": "e" * 40,
    "agent_config_hash": "f" * 40,
    "profile_semantics_hash": "0" * 40,
    "particle_config_hash": "1" * 40,
    "source_extraction_hash": "2" * 40,
    "source_input_hash": "3" * 40,
    "derivation_bundle_hash": "4" * 40,
    "verse_map_hash": "5" * 40,
    "note_map_hash": "6" * 40,
    "plugin_bundle_hash": "7" * 40,
}


def _yaml_dump(obj) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False)


def default_profile():
    return {
        "profile_version": 1,
        "project": {
            "title": "Test Book",
            "durable_root": "/placeholder",
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "plain_text",
            "path": "/logical/source.txt",
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr_test.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": None,
                "plain_text": {
                    "segmentation": {
                        "method": "blank_line_run",
                        "blank_line_threshold": 2,
                        "heading_regex": None,
                    },
                    "verse_detection": "none_confirmed",
                    "verse_regex": None,
                    "footnotes": "none_confirmed",
                    "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {"language": {"code": "ru", "register_notes": "informal"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {
            "v1_scope": "assembled_book",
            "destination": "/placeholder/out/",
            "target": "obsidian",
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {
                "obsidian": {"folders": {}, "mentions_section": {"enabled": False}},
                "epub": None,
                "custom": {"renderer_path": None},
            },
        },
    }


PARTICLE_CONFIG_NAME = "fr_test.json"


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """#492: the durable-root files cache_key.py's own field computers read.
    assemble.py now recomputes every content-affecting cache-key field from
    the live root and refuses on a mismatch, so this fixture must carry real
    inputs and a real stored key. Restated from tests/final_audit.test.py's
    make_durable_root() rather than imported -- house convention is one
    self-contained file per test module. Only style_bible.md's two
    STYLE_CONTRACT markers are load-bearing; `runs/.plugin_bundle_hash` is the
    marker Step 0a writes and cache_key.py reads back rather than re-hashing
    the bundle."""
    # Fill a gap, never clobber: whichever of these the caller already staged
    # as the REAL module wins. cache_key.py only needs the paths to exist and
    # to hash stably, so deferring to a real copy serves both purposes -- and a
    # placeholder written over a real dependency fails far from its cause
    # (verified on assemble_link_groups_wiring.test.py, whose #497 cases need
    # bootstrap_names.extract_candidate_spans).
    for _name, _body in (("bootstrap_names.py", b"# bootstrap_names.py fixture\n"),
                         ("segpack.py", b"# segpack.py fixture\n")):
        if not (scripts_dir / _name).exists():
            (scripts_dir / _name).write_bytes(_body)
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / "a.txt").write_bytes(b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    (languages_dir / PARTICLE_CONFIG_NAME).write_text(
        json.dumps({"PARTICLES": ["de"], "STOPWORDS": ["le"], "has_elision": False,
                    "ELISION_RE": None}),
        encoding="utf-8",
    )
    (root / "schemas").mkdir(exist_ok=True)
    for _name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (root / "schemas" / _name).write_bytes(b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    (runs_dir / ".plugin_bundle_hash").write_text(
        "test-plugin-bundle-marker-v1\n", encoding="utf-8"
    )


def real_cache_key(root: Path, seg: str) -> dict:
    """The segment's REAL 15-field cache key, from the SHIPPED cache_key.py run
    against this fixture root -- never hand-typed, so it cannot drift from what
    assemble.py recomputes at run time."""
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def make_root(tmp_path) -> Path:
    """A bare durable_root for assemble.py: real copies of assemble.py + its
    four sibling scripts, profile.yml + ownership marker, an empty
    canon.json. Manifest/segpack/draft/ledger content is written per-test
    by the helpers below (mirrors tests/assemble.test.py's own make_root(),
    trimmed to this file's fixed defaults -- no test here varies verse
    mode/output target/mentions)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_REAL_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    profile = default_profile()
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(_yaml_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps({"entries": {}, "review_queue": []}), encoding="utf-8"
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    # #492: last, so it can reuse the runs/ dir just created above.
    _write_cache_key_inputs(root, scripts_dir)
    return root


def write_book_scaffold(root, seg_ids):
    """Manifest + segpacks for N trivial one-block, no-sentinel segments --
    just enough for assemble.py's whole-project completeness gate and (for
    the tests that reach it) a full successful assembly, matching the
    proven-working shape tests/assemble.test.py's own
    build_clean_two_segment_book() uses for its own plain-prose blocks
    (e.g. its "p2": no source_html, no verses/footnotes needed). Draft
    files are written separately (write_segment_draft below) so a test can
    control each segment's draft content/timing independently of this."""
    blocks = {}
    segments = []
    for i, seg in enumerate(seg_ids):
        bid = f"p_{seg}"
        blocks[bid] = {
            "type": "PARA",
            "seg": seg,
            "order_index": i,
            "plain_text": f"Prose for {seg}.",
        }
        segments.append(
            {"seg": seg, "kind": "body", "title_text": seg, "block_ids": [bid], "word_count": 3}
        )
    manifest = {
        "blocks": blocks,
        "spine": [{"pos": 0, "file": "a.txt", "klass": "body"}],
        "segments": segments,
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": ["a.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for seg in seg_ids:
        bid = f"p_{seg}"
        pack = {
            "seg": seg,
            "title": seg,
            "kind": "body",
            "word_count": 3,
            "blocks": [{"id": bid, "order_index": 0, "plain_text": f"Prose for {seg}."}],
            "footnotes": [],
            "verses": [],
            "names": [],
            "canon_names": [],
            "new_names": [],
            "generation_hashes": {
                "source_extraction_hash": "x",
                "source_input_hash": "y",
                "particle_config_hash": "x",
                "derivation_bundle_hash": "y",
            },
        }
        (root / "segments" / f"segpack_{seg}.json").write_text(
            json.dumps(pack, ensure_ascii=False), encoding="utf-8"
        )


def draft_content_sha1_of(doc: dict) -> str:
    """Independent, stdlib-only ground truth -- see assemble.test.py's own
    copy of this exact helper for why it's duplicated rather than
    imported."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_segment_draft(root, seg, text=None) -> bytes:
    bid = f"p_{seg}"
    draft = {
        "seg": seg,
        "blocks": {bid: text or f"Translated {seg}."},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(draft_bytes)
    return draft_bytes


def write_ledger_segments(root, segments: dict) -> None:
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def converged_ledger_record(root, seg, reviewed_draft_sha1_override=None) -> dict:
    draft_doc = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    sha1 = reviewed_draft_sha1_override or draft_content_sha1_of(draft_doc)
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "status": "converged",
        "rounds": 1,
        # #492: a REAL key -- this record is manifest-required, so assembly
        # recomputes and compares its content-affecting fields. The two
        # DUMMY_CACHE_KEY uses that remain below are both OUT-OF-MANIFEST
        # entries, which the live check never reaches by design.
        "cache_key": real_cache_key(root, seg),
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": sha1,
    }


_UNSET = object()


def stale_ledger_record(root, seg, mismatched_fields=_UNSET, reviewed_draft_sha1_override=None) -> dict:
    """A materialized 'stale' ledger record, #491-shaped. `mismatched_fields`
    left at its default omits `stale_mismatched_fields` from the record
    entirely -- the pre-1.25.0 legacy shape (tests 10c/13); an explicit `[]`
    or a non-list value exercises the OTHER two "unusable" shapes; a real
    list is the normal carve-out-eligible or carve-out-ineligible shape,
    depending on its members."""
    record = converged_ledger_record(root, seg, reviewed_draft_sha1_override)
    record["status"] = "stale"
    if mismatched_fields is not _UNSET:
        record["stale_mismatched_fields"] = mismatched_fields
    return record


def mark_sentinel_present(root, seg) -> Path:
    path = root / "segments" / f".ever_converged.{seg}"
    path.write_bytes(b"converged\n")
    return path


def mark_sentinel_ambiguous(root, seg) -> Path:
    """A dangling symlink -- AMBIGUOUS, not ABSENT (Path.exists() would
    read this as absent, which is exactly the #490-shaped bug the carve-out
    must not reintroduce; see classify_ever_converged_sentinel()'s own
    docstring)."""
    path = root / "segments" / f".ever_converged.{seg}"
    path.symlink_to(root / "segments" / "no-such-target")
    return path


def run_assemble(
    root: Path, timeout: int = 60, env: "dict | None" = None
) -> subprocess.CompletedProcess:
    """`env=None` (every existing call site) inherits the parent process's
    environment exactly as before this parameter was added -- only
    test_assembly_gains_no_write_and_no_subprocess passes an explicit
    env, to suppress __pycache__ writes (see that test's own docstring)."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def read_nodestream(root: Path) -> dict:
    path = root / "out" / ".assembled" / "nodestream.json"
    assert path.is_file(), f"expected nodestream.json artifact at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def load_assemble_module(root):
    """The COPY inside `root/scripts/` -- self-anchored, so its own
    SEGMENTS_DIR/DURABLE_ROOT resolve against THIS fixture, never the real
    plugin assets tree. Used only by test 15, which calls
    load_converged_segments() directly; every other assemble.py test in
    this file drives the real subprocess entrypoint instead."""
    return _load_module_from_source(
        root / "scripts" / "assemble.py", f"stale_carveout__assemble_{id(root)}"
    )


# ===========================================================================
# 9. Carve-out accepts a machinery-only stale segment alongside converged.
# ===========================================================================


def test_carveout_accepts_machinery_only_stale_alongside_converged(tmp_path):
    """Plan test 9. Mutation: remove the carve-out (treat any 'stale' as
    unconditionally not-converged) -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01", "seg02"])
    write_segment_draft(root, "seg01")
    write_segment_draft(root, "seg02")
    mark_sentinel_present(root, "seg02")
    write_ledger_segments(
        root,
        {
            "seg01": converged_ledger_record(root, "seg01"),
            "seg02": stale_ledger_record(root, "seg02", mismatched_fields=["plugin_bundle_hash"]),
        },
    )

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a machinery-only stale segment alongside a converged one must "
        f"assemble cleanly:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    node_ids = {n["id"] for n in ns["nodes"]}
    assert "p_seg02" in node_ids, (
        "seg02 was carved out but its content never reached the book -- the "
        "carve-out must ADMIT the record into the assembled book, not "
        "merely avoid refusing it"
    )


# ===========================================================================
# 10. Carve-out refuses, each with its own reason.
# ===========================================================================


@pytest.mark.parametrize(
    "unsafe_fields,label",
    [
        (["used_terms_hash"], "content-affecting field"),
        (["some_future_field_never_allowlisted"], "unrecognised/future field name"),
    ],
)
def test_carveout_refuses_a_non_machinery_field(tmp_path, unsafe_fields, label):
    """Plan test 10 (a) and (d). Mutation: drop the allowlist subtraction
    (accept any field name) -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root, {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=unsafe_fields)}
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{label}:\n{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "content-affecting" in payload["error"], (
        f"{label}: refusal must name a content-affecting field, not a bare "
        f"'not converged': {payload['error']}"
    )


def test_carveout_refuses_when_sentinel_is_absent(tmp_path):
    """Plan test 10 (b): a clean-ENOENT sentinel. Mutation: accept ABSENT
    too -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    # Deliberately never marked -- ABSENT.
    write_ledger_segments(
        root,
        {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=["plugin_bundle_hash"])},
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "sentinel is absent" in payload["error"]


@pytest.mark.parametrize(
    "mismatched_fields,label",
    [([], "empty list"), ("not-a-list", "wrong type (a string)")],
)
def test_carveout_refuses_unusable_stale_mismatched_fields(tmp_path, mismatched_fields, label):
    """Plan test 10 (c), the empty/wrong-type shapes. (The THIRD shape --
    the key missing entirely -- is plan test 13's own dedicated backward-
    compatibility test below.) Mutation: treat an empty/non-list value as
    machinery-only -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root,
        {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=mismatched_fields)},
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{label}:\n{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "no usable stale_mismatched_fields" in payload["error"], (
        f"{label}: fail-safe direction requires a SPECIFIC reason, not a "
        f"bare 'not converged': {payload['error']}"
    )


# ===========================================================================
# 11. All-refused project still diagnoses via project_incomplete.
# ===========================================================================


@pytest.mark.parametrize(
    "mismatched_fields,label",
    [
        ([{}], "dict member (unhashable)"),
        ([None], "None member (hashable, non-string)"),
        ([1], "int member (hashable, non-string)"),
        (["plugin_bundle_hash", 2], "one safe string plus an int member"),
    ],
)
def test_carveout_refuses_a_non_string_member_without_crashing(tmp_path, mismatched_fields, label):
    """Code-review finding on the original #491 patch (#490/#491): a
    malformed `stale_mismatched_fields` MEMBER -- reachable via a hand-
    edited or corrupted runs/ledger.json, which assemble.py never schema-
    validates -- must produce this function's own per-segment
    `project_incomplete` refusal, never crash. `[{}]`/`[[]]` are unhashable
    and would raise TypeError at the old code's `f not in
    SAFE_STALE_CARVEOUT_FIELDS` frozenset test; `[1]`/`[None]` are hashable
    but would then raise TypeError at `sorted()`/`', '.join()` over a mixed
    or non-string `unsafe`. Either crash reaches main()'s generic
    'unexpected error' handler instead of this refusal -- still fail-closed
    (assembly still aborts), but the wrong diagnostic for a gate.
    Mutation: drop the new non-string-member check -> assemble.py crashes
    with an unhandled TypeError instead of exiting 2 with this reason."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root, {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=mismatched_fields)}
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{label}:\n{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "non-string member" in payload["error"], (
        f"{label}: refusal must name the non-string-member condition, not "
        f"a bare 'not converged' or a generic crash: {payload['error']}"
    )


def test_all_refused_project_still_diagnoses_via_project_incomplete(tmp_path):
    """Plan test 11. Mutation: keep the early return (main()'s
    `if not converged: raise AssemblePrecondition("no_converged_segments",
    ...)` firing before assert_project_complete() ever runs) -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root,
        {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=["used_terms_hash"])},
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete", (
        f"an all-refused project must diagnose via project_incomplete "
        f"(naming WHY), never fold into the generic no_converged_segments "
        f"precondition: got reason={payload.get('reason')!r}"
    )
    assert "seg01" in payload["error"]
    assert "content-affecting" in payload["error"]


# ===========================================================================
# 12. The sha1 fatal survives the carve-out.
# ===========================================================================


def test_sha1_fatal_survives_the_carveout(tmp_path):
    """Plan test 12 -- "the most important test in the file". A machinery-
    only stale entry whose on-disk draft no longer matches
    reviewed_draft_sha1 must still raise fatally (exit 1), exactly like a
    plain converged record would. Mutation: skip the sha1 check on the
    accepted path -> red (this would silently assemble a hand-edit the
    reviewer never saw)."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root,
        {
            "seg01": stale_ledger_record(
                root,
                "seg01",
                mismatched_fields=["plugin_bundle_hash"],
                reviewed_draft_sha1_override="0" * 40,  # never matches the real draft
            )
        },
    )

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a sha1 mismatch on a carved-out record must be FATAL (exit 1), "
        f"never silently accepted:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "seg01" in payload["error"]
    assembled_dir = root / "out" / ".assembled"
    assert not (assembled_dir / "nodestream.json").is_file(), (
        "a fatally-refused project must leave no partial artifacts"
    )


# ===========================================================================
# 13. Backward compatibility: a legacy ledger (no field at all) blocks.
# ===========================================================================


def test_backward_compat_legacy_ledger_without_the_field_blocks(tmp_path):
    """Plan test 13, named explicitly per the plan's own instruction: a
    runs/ledger.json written before this change -- 'stale' with NO
    stale_mismatched_fields key at all -- must block assembly, never ship.
    Mutation: treat a missing stale_mismatched_fields as machinery-only ->
    the legacy ledger assembles -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    legacy_record = stale_ledger_record(root, "seg01")  # mismatched_fields left unset
    assert "stale_mismatched_fields" not in legacy_record, "sanity: this IS the legacy shape"
    write_ledger_segments(root, {"seg01": legacy_record})

    result = run_assemble(root)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "no usable stale_mismatched_fields" in payload["error"]


def test_carveout_refuses_a_stale_record_that_never_recorded_stale_mismatched_fields(tmp_path):
    """Real-data MIRROR case (team lead, paired with this file's
    ledger_merge.py-side absent/non-dict-cache_key test above): if a future
    edit made that absent-stored-cache_key path produce an empty or
    machinery-only stale_mismatched_fields list instead of omitting the
    field, THIS is where it would actually cost something -- a segment
    whose staleness was never genuinely diffed would silently carve out
    with no re-review. Distinct from test 13 (a pre-1.25.0 legacy ledger)
    even though the materialized shape reaching assembly is identical
    (status="stale", no stale_mismatched_fields key): this one traces to a
    real, still-live root cause -- a converged fragment that legitimately
    never recorded a cache_key -- not merely an old ledger format.
    Mutation: treat an absent stored key as "no fields moved" -> it carves
    out -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    # The exact materialized shape ledger_merge.py writes today for a
    # converged fragment with no cache_key: status flipped to "stale",
    # stale_mismatched_fields never set (see the ledger_merge.py-side test
    # above -- this is that record's OWN output, reconstructed here rather
    # than re-run through the merge subprocess, since assemble.py's gate
    # only ever sees the materialized ledger.json, never the fragment).
    record = stale_ledger_record(root, "seg01")
    record.pop("cache_key", None)
    write_ledger_segments(root, {"seg01": record})

    result = run_assemble(root)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete"
    assert "seg01" in payload["error"]
    assert "no usable stale_mismatched_fields" in payload["error"]


# ===========================================================================
# 14. AMBIGUOUS parity: a dangling-symlink sentinel carves out too.
# ===========================================================================


def test_ambiguous_sentinel_carves_out_same_as_present(tmp_path):
    """Plan test 14. A dangling symlink at the sentinel path classifies
    AMBIGUOUS, not ABSENT -- and AMBIGUOUS must carve out exactly like
    PRESENT, mirroring final_audit.py's own carve-out (never read an
    unreadable dotfile as "this segment never converged" -- #490 in
    miniature). Mutation: require strict PRESENT (reject AMBIGUOUS) ->
    red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    sentinel_path = mark_sentinel_ambiguous(root, "seg01")

    # Fixture sanity: the REAL predicate (final_audit.py's own, read
    # directly, never reimplemented here) must classify this AMBIGUOUS --
    # else the test below would silently exercise a different state than
    # the one it claims to.
    final_audit_mod = load_real_final_audit_module()
    state, _detail = final_audit_mod.classify_ever_converged_sentinel(sentinel_path)
    assert state == final_audit_mod.SENTINEL_AMBIGUOUS, (
        f"fixture sanity check failed: expected AMBIGUOUS, got {state!r} -- "
        f"this platform's dangling-symlink lstat() behavior may differ"
    )

    write_ledger_segments(
        root, {"seg01": stale_ledger_record(root, "seg01", mismatched_fields=["schema_hash"])}
    )

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"AMBIGUOUS must carve out exactly like PRESENT:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ===========================================================================
# 15. Snapshot-level gate parity with final_audit.py's own carve-out.
# ===========================================================================


def test_snapshot_level_gate_parity_with_final_audit(tmp_path):
    """Plan test 15. A fixture table over stale_mismatched_fields and every
    sentinel state, driving BOTH gates from the SAME underlying parameters
    for each named case id: assemble.py's own load_converged_segments()
    (via a self-anchored copy) and final_audit.py's own
    count_stale_previously_converged() (called directly, with an explicit
    sentinel_states dict so it never touches the filesystem). sha1 is held
    MATCHING throughout this table -- see the standalone sha1-mismatch
    check below, which is deliberately NOT a parity comparison:
    count_stale_previously_converged() has no sha1 concept at all (that
    axis is final_audit.py's own SEPARATE hard_check_stale_review, outside
    this function), so "agreement" there specifically means assemble.py is
    never LESS strict, not that the two booleans match.

    Mutation: change either side's policy on any axis -> red for that case
    id."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    assemble_mod = load_assemble_module(root)
    final_audit_mod = load_real_final_audit_module()

    draft_path = root / "segments" / "seg01.draft.json"
    matching_sha1 = assemble_mod.draft_content_sha1(draft_path)
    # Sanity: this file's own independent sha1 helper agrees with the
    # loaded module's -- both implement the same documented algorithm.
    assert matching_sha1 == draft_content_sha1_of(
        json.loads(draft_path.read_text(encoding="utf-8"))
    )

    sentinel_path = root / "segments" / ".ever_converged.seg01"

    def set_sentinel(state):
        sentinel_path.unlink(missing_ok=True)
        if state == "present":
            sentinel_path.write_bytes(b"converged\n")
        elif state == "ambiguous":
            sentinel_path.symlink_to(root / "segments" / "no-such-target")
        elif state == "absent":
            pass
        else:
            raise ValueError(state)

    def assemble_accepts(mismatched_fields, sha1) -> bool:
        record = {
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "stale",
            "reviewed_draft_sha1": sha1,
        }
        if mismatched_fields is not None:
            record["stale_mismatched_fields"] = mismatched_fields
        # {"seg01"} keeps seg01 IN the manifest population -- this table
        # tests the carve-out predicate itself (test 15's whole point),
        # never the round-2 manifest-membership scoping (that has its own
        # dedicated tests below), so it must never accidentally exercise
        # the new skip branch.
        # Three values since #533; this helper drives the #491 machinery-only
        # path only, so the third (contract-admitted) list is always empty
        # here -- asserted rather than discarded, so a #533 regression that
        # widened THIS path would surface in the #491 suite too.
        converged, refusals, contract_admitted = assemble_mod.load_converged_segments(
            {"segments": {"seg01": record}}, {"seg01"}
        )
        assert contract_admitted == [], (
            "the #491 machinery-only carve-out must never route a record "
            f"through #533's opt-in path (got {contract_admitted!r})"
        )
        assert ("seg01" in converged) != ("seg01" in refusals), "must be exactly one of the two"
        return "seg01" in converged

    def final_audit_counts(mismatched_fields, sentinel_state) -> bool:
        classification = {
            "seg01": {
                "category": "stale",
                "stale_reason": ["cache_key_mismatch"],
                "mismatched_fields": mismatched_fields,
            }
        }
        n = final_audit_mod.count_stale_previously_converged(
            classification, sentinel_states={"seg01": (sentinel_state, "")}
        )
        return n == 1

    all_machinery = sorted(assemble_mod.SAFE_STALE_CARVEOUT_FIELDS)
    rows = [
        ("machinery_present", ["plugin_bundle_hash"], "present"),
        ("content_present", ["used_terms_hash"], "present"),
        ("all_three_machinery_present", all_machinery, "present"),
        ("mixed_machinery_and_content", ["plugin_bundle_hash", "used_terms_hash"], "present"),
        ("empty_fields_present", [], "present"),
        ("machinery_absent_sentinel", ["plugin_bundle_hash"], "absent"),
        ("machinery_ambiguous_sentinel", ["plugin_bundle_hash"], "ambiguous"),
    ]
    for case_id, fields, sentinel_state in rows:
        set_sentinel(sentinel_state)
        got_assemble = assemble_accepts(fields, matching_sha1)
        got_final_audit = final_audit_counts(fields, sentinel_state)
        assert got_assemble == got_final_audit, (
            f"case {case_id!r}: assemble.py accepts={got_assemble}, "
            f"final_audit.py carves out={got_final_audit} -- the two "
            f"whole-project completeness gates disagree about the SAME "
            f"materialized snapshot"
        )

    # The sha1-mismatch row -- see docstring: not a parity comparison.
    set_sentinel("present")
    with pytest.raises(assemble_mod.AssembleError):
        assemble_accepts(["plugin_bundle_hash"], "0" * 40)
    assert final_audit_counts(["plugin_bundle_hash"], "present") is True, (
        "sanity: final_audit's carve-out (no sha1 concept in this function) "
        "would still count this row -- proving assemble.py's refusal here "
        "is its OWN stricter, independent check (test 12), not accidental "
        "agreement"
    )


# ===========================================================================
# 16. Assembly gains no write and no subprocess.
# ===========================================================================

PROBE_STUB_TEMPLATE = """#!/usr/bin/env python3
import sys
from pathlib import Path


def main():
    marker = Path(__file__).resolve().parent.parent / "runs" / {marker_name!r}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("invoked", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def plant_probe_stub(root: Path, script_name: str) -> "tuple[Path, Path]":
    """Writes an executable stub at root/scripts/<script_name> whose ONLY
    effect is creating a marker file at runs/.probe_invoked.<stem> and
    exiting 0. Returns (stub_path, marker_path).

    Replaces the old "just leave cache_key.py/select_segments.py absent"
    approach, which only catches a shell-out that CHECKS the child's exit
    status (an absent script raises FileNotFoundError, loudly) -- never
    one that invokes the script and ignores its result, since
    subprocess.run() does NOT propagate a nonzero/failed child unless
    called with check=True. A marker file records that the child ran at
    all, independent of what assemble.py does with its return code."""
    marker_path = root / "runs" / f".probe_invoked.{Path(script_name).stem}"
    stub_path = root / "scripts" / script_name
    if script_name == "cache_key.py":
        # #492: assemble.py now IMPORTS cache_key.py as a sibling, so a stub
        # that replaces the module's contents would break the import rather
        # than probe for a subprocess -- and the invariant this test pins
        # (assembly shells out to nothing) is exactly as true as before, and
        # exactly as worth pinning. So the probe here is the REAL module with
        # a marker write injected into its __main__ guard: importable in every
        # respect, and still recording any invocation as a child process, with
        # main()'s own behaviour left intact so a shell-out that inspects the
        # child's output is not perturbed either.
        real_source = CACHE_KEY_REAL_SRC.read_text(encoding="utf-8")
        guard = 'if __name__ == "__main__":\n    sys.exit(main())'
        assert guard in real_source, (
            "cache_key.py's __main__ guard is not the shape this probe injects "
            "into -- re-derive it before trusting this test"
        )
        stub_path.write_text(
            real_source.replace(
                guard,
                'if __name__ == "__main__":\n'
                f'    Path(__file__).resolve().parent.parent.joinpath("runs", "{marker_path.name}")'
                '.write_text("invoked", encoding="utf-8")\n'
                "    sys.exit(main())",
            ),
            encoding="utf-8",
        )
    else:
        stub_path.write_text(
            PROBE_STUB_TEMPLATE.format(marker_name=marker_path.name), encoding="utf-8"
        )
    stub_path.chmod(stub_path.stat().st_mode | 0o111)
    return stub_path, marker_path


def snapshot_durable_root(root: Path) -> dict:
    """{relative_path (str): (size, sha1 hex)} for every regular file
    under `root`, EXCLUDING assembly's own declared outputs:

      - the whole `out/` tree -- `out/.assembled/nodestream.json`,
        `out/.assembled/anchor_map.json`, `out/.literary-translator-
        vault.json`, and every file the obsidian adapter renders into it
        (e.g. `out/001 seg01.md`) -- ALL of assembly's real, intended
        output, confirmed empirically by diffing a successful run's file
        tree before/after.
      - `scripts/__pycache__/` -- bytecode the interpreter writes as a
        side effect of `import`ing assemble.py's sibling modules
        (output_resolve, render_obsidian, validate_draft), not a write
        assemble.py's own logic performs. The caller also passes
        PYTHONDONTWRITEBYTECODE=1 to the subprocess to suppress this at
        the source; this exclusion is defense in depth in case that ever
        stops being effective on some platform/interpreter build.

    Used by test_assembly_gains_no_write_and_no_subprocess to catch a
    stray write ANYWHERE in the durable root -- new file, deleted file, or
    changed content -- not just to runs/ledger.json."""
    snapshot = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if parts[0] == "out" or "__pycache__" in parts:
            continue
        data = path.read_bytes()
        snapshot[str(Path(*parts))] = (len(data), hashlib.sha1(data).hexdigest())
    return snapshot


def test_assembly_gains_no_write_and_no_subprocess(tmp_path):
    """Plan test 16, hardened per code review finding on the original
    #491 patch (#490/#491): the original version of this test only
    detected a subprocess shell-out that OMITTED the target script (a
    loud FileNotFoundError) and only detected a write to runs/ledger.json
    specifically. Neither actually discriminates against the mutant it
    claims to catch -- see plant_probe_stub's and snapshot_durable_root's
    own docstrings for what each replaces and why.

    Also still covers the original test's own non-goal: a malformed
    runs/ledger.d/old.json outside the manifest must never block assembly
    -- assemble.py never reads ledger.d/ at all."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    write_ledger_segments(root, {"seg01": converged_ledger_record(root, "seg01")})

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "runs" / "ledger.d" / "old.json").write_text("{not valid json", encoding="utf-8")

    # (a) Subprocess detection (M1's target). Plant probe stubs at the two
    # scripts-dir paths assembly could plausibly invoke.
    probes = {}
    for script_name in ("cache_key.py", "select_segments.py"):
        stub_path, marker_path = plant_probe_stub(root, script_name)
        # Sanity: the stub itself must actually run and write its own
        # marker, or its absence below would be vacuously green regardless
        # of whether assembly ever invokes it -- the stub would be broken,
        # not the thing under test.
        # cache_key.py's probe IS the real module (#492), so its sanity run
        # needs the arguments the real CLI requires; select_segments.py's is a
        # bare stub and takes none.
        sanity_argv = [sys.executable, str(stub_path)]
        if script_name == "cache_key.py":
            sanity_argv += ["--seg", "seg01"]
        sanity = subprocess.run(
            sanity_argv, capture_output=True, text=True, timeout=10
        )
        assert sanity.returncode == 0 and marker_path.is_file(), (
            f"probe stub {script_name} failed its own sanity check:\n"
            f"{sanity.stdout}\n{sanity.stderr}"
        )
        marker_path.unlink()  # reset before the real assembly run below
        probes[script_name] = marker_path

    # (b) Write detection (M2's target). Snapshot the entire durable root
    # (minus assembly's own declared outputs) before and after.
    before = snapshot_durable_root(root)
    assert len(before) >= 10, (
        f"sanity: implausibly small snapshot ({len(before)} files) -- a "
        f"broken walk could vacuously pass an empty-vs-empty comparison"
    )

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = run_assemble(root, env=env)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    for script_name, marker_path in probes.items():
        assert not marker_path.is_file(), (
            f"assembly invoked {script_name} (its marker {marker_path} "
            f"exists) -- assemble.py must never shell out to the segment-"
            f"selection machinery"
        )

    after = snapshot_durable_root(root)
    assert after == before, (
        "assembly wrote to a file outside its declared output area (out/) "
        f"-- changed/added/removed paths: {sorted(set(after) ^ set(before))}"
    )


# ===========================================================================
# 17. Three-copy drift guard for the machinery-only field set.
# ===========================================================================


def test_three_copy_drift_guard_for_machinery_only_fields():
    """Plan test 17. The machinery-only field set is restated three times
    (house convention for this plugin's self-contained scripts -- see the
    #491 plan's own non-goals) -- pin all three against each other, with a
    minimum-size assertion so a parse failure that silently empties all
    three cannot pass vacuously. Mutation: any one copy drifts -> red."""
    final_audit_mod = load_real_final_audit_module()
    select_segments_mod = load_real_select_segments_module()
    assemble_mod = load_real_assemble_module()

    assert len(final_audit_mod.SAFE_STALE_CARVEOUT_FIELDS) == 3, (
        "sanity: the known field count -- a parse failure that silently "
        "empties this constant must not pass the equality check below "
        "vacuously"
    )
    assert (
        final_audit_mod.SAFE_STALE_CARVEOUT_FIELDS
        == select_segments_mod.MACHINERY_ONLY_CACHE_KEY_FIELDS
        == assemble_mod.SAFE_STALE_CARVEOUT_FIELDS
    ), (
        f"the three machinery-only field sets have drifted: "
        f"final_audit={sorted(final_audit_mod.SAFE_STALE_CARVEOUT_FIELDS)!r} "
        f"select_segments={sorted(select_segments_mod.MACHINERY_ONLY_CACHE_KEY_FIELDS)!r} "
        f"assemble={sorted(assemble_mod.SAFE_STALE_CARVEOUT_FIELDS)!r}"
    )


# ===========================================================================
# 18. Round-2 hardening (codex review of #490/#491): the stale carve-out
#     fall-through is scoped to the CURRENT manifest's segment population,
#     never to the whole retained runs/ledger.json map. ledger_merge.py
#     deliberately keeps historical entries for segments the manifest no
#     longer lists (see its own module docstring); pre-#491 that never
#     mattered because ANY stale entry was unconditionally skipped, but
#     #491's carve-out alone would let such a retained entry reach the
#     FATAL sha1/draft-presence guards and abort an otherwise-complete
#     book over a segment it doesn't even require.
# ===========================================================================


def _raw_stale_record(mismatched_fields=("plugin_bundle_hash",), reviewed_draft_sha1=_UNSET):
    """A hand-built 'stale' ledger record for an OUT-OF-MANIFEST segment.
    Unlike stale_ledger_record() above, this never reads an actual draft
    file to compute reviewed_draft_sha1 -- there is no segpack/draft for an
    out-of-manifest segment in these fixtures at all -- so it can express
    "no reviewed_draft_sha1 recorded" (case 1) without one existing on
    disk."""
    record = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "status": "stale",
        "rounds": 1,
        "cache_key": DUMMY_CACHE_KEY,
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "stale_mismatched_fields": list(mismatched_fields),
    }
    if reviewed_draft_sha1 is not _UNSET:
        record["reviewed_draft_sha1"] = reviewed_draft_sha1
    return record


def _healthy_single_segment_root(tmp_path):
    """seg01: one clean, converged, in-manifest segment -- the healthy book
    every case below is layered onto, so a returncode==0 assertion means
    exactly "the retained out-of-manifest entry never blocked assembly",
    never "there happened to be nothing else to block on"."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    write_ledger_segments(root, {"seg01": converged_ledger_record(root, "seg01")})
    return root


def _add_out_of_manifest_entry(root, record) -> None:
    """Merges an extra runs/ledger.json segments{} entry for 'seg99' --
    a segment write_book_scaffold's manifest never listed -- alongside the
    existing seg01 entry, mirroring what a retained historical
    ledger_merge.py entry looks like once the current manifest no longer
    requires it."""
    ledger_path = root / "runs" / "ledger.json"
    doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    doc["segments"]["seg99"] = record
    ledger_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_out_of_manifest_carved_out_stale_entry_with_no_sha1_is_skipped(tmp_path):
    """Case 1: a retained out-of-manifest entry that carves out
    (machinery-only stale_mismatched_fields, sentinel present) but records
    NO reviewed_draft_sha1 at all must not abort an otherwise-complete
    book. Pre-#491 this was unconditionally skipped; #491 alone would make
    it FATAL via load_converged_segments()'s own `if not expected_sha1:
    raise AssembleError(...)` guard. Mutation (M1): drop the manifest-
    membership test -> this goes fatal (exit 1) instead of succeeding."""
    root = _healthy_single_segment_root(tmp_path)
    mark_sentinel_present(root, "seg99")
    _add_out_of_manifest_entry(root, _raw_stale_record())  # no reviewed_draft_sha1 key at all

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an out-of-manifest carved-out stale entry with no "
        f"reviewed_draft_sha1 must never block an otherwise-complete "
        f"book:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "seg99" not in result.stdout, (
        "a silently-skipped out-of-manifest entry must never surface in "
        "the output -- this book does not contain it, so no refusal or "
        "any other mention of it may leak into the result"
    )


def test_out_of_manifest_carved_out_stale_entry_with_missing_draft_is_skipped(tmp_path):
    """Case 2: the retained entry's draft file is missing on disk entirely
    -- pre-#491, skipped unconditionally; #491 alone would make this fatal
    via load_converged_segments()'s own `if not dp.is_file(): raise
    AssembleError(...)` guard."""
    root = _healthy_single_segment_root(tmp_path)
    mark_sentinel_present(root, "seg99")
    _add_out_of_manifest_entry(root, _raw_stale_record(reviewed_draft_sha1="d" * 40))
    assert not (root / "segments" / "seg99.draft.json").exists(), "sanity: no draft for seg99"

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an out-of-manifest carved-out stale entry with a missing draft "
        f"must never block an otherwise-complete book:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "seg99" not in result.stdout


def test_out_of_manifest_carved_out_stale_entry_with_sha1_mismatch_is_skipped(tmp_path):
    """Case 3: the retained entry's draft EXISTS but its sha1 no longer
    matches reviewed_draft_sha1 -- #491 alone would make this fatal via
    load_converged_segments()'s own `if actual_sha1 != expected_sha1:
    raise AssembleError(...)` guard, the SAME guard case 5/12 below prove
    still fires for an IN-manifest entry."""
    root = _healthy_single_segment_root(tmp_path)
    mark_sentinel_present(root, "seg99")
    write_segment_draft(root, "seg99", text="Some drifted seg99 draft text.")
    _add_out_of_manifest_entry(
        root, _raw_stale_record(reviewed_draft_sha1="0" * 40)  # never matches the real draft
    )

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an out-of-manifest carved-out stale entry with a sha1 mismatch "
        f"must never block an otherwise-complete book:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "seg99" not in result.stdout


def test_out_of_manifest_carved_out_stale_entry_with_corrupt_draft_is_skipped(tmp_path):
    """Case 4: the retained entry's draft file exists but is unreadable /
    corrupt JSON -- #491 alone would make this fatal via
    load_converged_segments()'s own draft_content_sha1() try/except
    guard."""
    root = _healthy_single_segment_root(tmp_path)
    mark_sentinel_present(root, "seg99")
    (root / "segments" / "seg99.draft.json").write_text("{not valid json", encoding="utf-8")
    _add_out_of_manifest_entry(root, _raw_stale_record(reviewed_draft_sha1="d" * 40))

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an out-of-manifest carved-out stale entry with a corrupt draft "
        f"must never block an otherwise-complete book:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "seg99" not in result.stdout


def test_out_of_manifest_carved_out_stale_records_are_skipped_with_no_refusal(tmp_path):
    """Direct-call companion to cases 1-4 above (mirrors test 15's own
    load_converged_segments()-via-self-anchored-copy pattern): drives the
    pure function directly so the exact `converged`/`refusals` RETURN
    VALUES can be inspected, not just inferred from the subprocess's exit
    code and JSON payload. That inference is one-directional -- main()'s
    own success-path `result` dict reports `segments_assembled` as a COUNT
    and serializes neither `refusals` nor any seg id, so cases 1-4 can
    observe only the exit code; their `"seg99" not in result.stdout`
    assertion fences the FAILURE payload (which does name the segment) and
    can never catch M3 (recording a refusal instead of skipping silently),
    a mutation that leaves the success-path stdout byte-identical. This
    test is the one that actually exercises M3.

    Two rows, both already fatal-on-their-own-terms if the round-2
    membership scoping is dropped (M1) or misapplied: no reviewed_draft_
    sha1 at all, and a reviewed_draft_sha1 pointing at a draft file that
    doesn't exist. In both, seg99 must land in NEITHER dict."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    assemble_mod = load_assemble_module(root)
    # Sentinel present for seg99 -- load-bearing for the carve-out itself
    # (_stale_carveout_refusal_reason's own condition 4). Without it, seg99
    # would land in `refusals` for the PRE-EXISTING sentinel-absent reason,
    # unrelated to the round-2 scoping this test targets, and the "not in
    # refusals" assertion below would fail for the wrong reason.
    mark_sentinel_present(root, "seg99")

    seg01_record = converged_ledger_record(root, "seg01")
    manifest_ids = {"seg01"}
    rows = {
        "no_sha1": _raw_stale_record(),
        "sha1_but_no_draft_on_disk": _raw_stale_record(reviewed_draft_sha1="d" * 40),
    }
    for label, seg99_record in rows.items():
        ledger = {"segments": {"seg01": seg01_record, "seg99": seg99_record}}
        converged, refusals, _contract_admitted = assemble_mod.load_converged_segments(
            ledger, manifest_ids
        )
        assert "seg99" not in converged, (
            f"{label}: an out-of-manifest carved-out stale record must "
            f"never be silently accepted into `converged`"
        )
        assert "seg99" not in refusals, (
            f"{label}: must not record a refusal either -- a refusal would "
            f"surface a segment this book does not contain in "
            f"assert_project_complete()'s own diagnostics"
        )
        assert "seg01" in converged, f"{label}: the healthy in-manifest segment must be unaffected"


def test_in_manifest_carved_out_stale_entry_with_sha1_mismatch_still_fatal(tmp_path):
    """Case 5, the MOST IMPORTANT fence in this section: proves the round-2
    manifest-membership scoping did not disarm the existing #491 sha1
    guard (test 12's own claim, above) for a segment the book actually
    requires. Mutation (M2 variant): apply the membership test to a record
    that IS in the manifest (i.e. invert the condition) -> red."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    write_ledger_segments(
        root,
        {
            "seg01": stale_ledger_record(
                root,
                "seg01",
                mismatched_fields=["plugin_bundle_hash"],
                reviewed_draft_sha1_override="0" * 40,  # never matches the real draft
            )
        },
    )

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"an IN-manifest carved-out stale entry with a sha1 mismatch must "
        f"still be fatal:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert "draft has changed since review" in payload["error"]


def test_in_manifest_carved_out_stale_entry_with_no_sha1_still_fatal(tmp_path):
    """Case 6: an IN-manifest carved-out stale entry with NO
    reviewed_draft_sha1 recorded at all must still fatal -- the round-2
    scoping only ever SKIPS an out-of-manifest entry; it must never widen
    what an in-manifest entry tolerates."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")
    record = stale_ledger_record(root, "seg01", mismatched_fields=["schema_hash"])
    del record["reviewed_draft_sha1"]
    write_ledger_segments(root, {"seg01": record})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"an IN-manifest carved-out stale entry with no reviewed_draft_sha1 "
        f"must still be fatal:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert "no reviewed_draft_sha1 recorded" in payload["error"]


def test_out_of_manifest_converged_entry_with_sha1_mismatch_still_fatal(tmp_path):
    """Case 7: an out-of-manifest status=="converged" entry with a sha1
    mismatch must STILL be fatal -- PRE-EXISTING behaviour, before AND
    after #491's stale carve-out, that this round-2 fix deliberately
    leaves untouched (see load_converged_segments()'s own docstring: the
    membership scoping applies ONLY to the stale branch). Asserted
    explicitly so a later refactor cannot silently extend the round-2
    scoping to the converged branch too. Mutation (M2): apply the
    membership test to status=="converged" as well -> red."""
    root = _healthy_single_segment_root(tmp_path)
    write_segment_draft(root, "seg99", text="Some out-of-manifest converged draft.")
    _add_out_of_manifest_entry(
        root,
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "converged",
            "rounds": 1,
            "cache_key": DUMMY_CACHE_KEY,
            "n_blocks": 1,
            "n_footnotes": 0,
            "n_verses": 0,
            "reviewed_draft_sha1": "0" * 40,  # never matches the real draft
        },
    )

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"an out-of-manifest CONVERGED entry with a sha1 mismatch must "
        f"still be fatal (pre-existing, unscoped behaviour):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert "draft has changed since review" in payload["error"]


# ===========================================================================
# 19. Round-2-on-round-2 (code-simplifier finding): hoisting the manifest-id
#     extraction into main(), ahead of the ledger read, must never change
#     WHICH pre-existing outcome fires first when the manifest is malformed
#     AND nothing has converged or been refused yet. Before #491 round 2
#     touched this code at all, that combination hit main()'s own
#     `no_converged_segments` precondition (exit 2, non-fatal bootstrap
#     state) -- assert_project_complete() (which raises malformed_manifest,
#     exit 1) was never reached, because main() returns early. The manifest-
#     id extraction must stay non-raising at that call site so this
#     ordering survives; assert_project_complete() remains the sole
#     authoritative raise site.
# ===========================================================================


def _write_manifest_segments(root, segments_value=_UNSET, *, top_level=_UNSET) -> None:
    """Writes manifest.json with `segments` set to exactly `segments_value`
    -- `_UNSET` (the default) omits the `segments` key entirely. Used only
    by this section: every other manifest fixture in this file goes through
    write_book_scaffold(), which always produces a well-formed `segments`
    array -- these tests need a manifest malformed in a specific, chosen
    way instead.

    `top_level`, when given (anything other than `_UNSET`, INCLUDING
    `None`), overrides the entire manifest.json body with
    `json.dumps(top_level)` and `segments_value` is ignored -- round-3
    axis: the PARSED manifest itself is not even a dict (a bare `[]` /
    `null` / a string / a number), distinct from a dict whose `segments`
    value is malformed."""
    if top_level is not _UNSET:
        (root / "manifest.json").write_text(
            json.dumps(top_level, ensure_ascii=False), encoding="utf-8"
        )
        return
    manifest = {
        "blocks": {},
        "spine": [],
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    if segments_value is not _UNSET:
        manifest["segments"] = segments_value
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    "segments_value,top_level,label",
    [
        (_UNSET, _UNSET, "segments key absent entirely"),
        ([], _UNSET, "segments an empty list"),
        ({"not": "a list"}, _UNSET, "segments a non-list (a dict)"),
        ("not-a-list-either", _UNSET, "segments a non-list (a string)"),
        (_UNSET, [], "manifest itself a top-level list"),
        (_UNSET, None, "manifest itself top-level null"),
        (_UNSET, "x", "manifest itself a top-level string"),
        (_UNSET, 3, "manifest itself a top-level number"),
    ],
)
def test_malformed_manifest_with_nothing_converged_or_refused_hits_no_converged_segments(
    tmp_path, segments_value, top_level, label
):
    """Cases 1-3 (a dict manifest with a malformed `segments` value) plus a
    4th non-list shape for extra coverage: a ledger that IS present but has
    zero converged/refused segments, alongside a malformed manifest, must
    still hit main()'s own pre-existing no_converged_segments precondition
    (exit 2) FIRST -- exactly as it did before #491 round 2 ever added a
    manifest read to this call path. Mutation: make main()'s call site
    raise on a malformed manifest again (revert to the bare
    _manifest_segment_ids() call) -> the first 4 rows red (exit 1,
    reason=malformed_manifest, instead of exit 2).

    The LAST 4 rows are round 3's own axis (security-review finding,
    MEASURED): the manifest.json body itself does not even parse to a
    dict (`[]`/`null`/a bare string/a bare number) -- `manifest.get(...)`
    inside _manifest_segment_ids() would raise an UNTYPED AttributeError
    that `_manifest_segment_ids_or_empty()`'s `except AssembleError` alone
    cannot catch, producing exit 1 with NO `reason` field at all instead of
    exit 2. Mutation: replace the isinstance(manifest, dict) guard in
    _manifest_segment_ids_or_empty() with nothing (bare try/except
    AssembleError only) -> these last 4 rows red; the first 4 stay green
    (that mutation does not touch axis (2))."""
    root = make_root(tmp_path)
    _write_manifest_segments(root, segments_value, top_level=top_level)
    write_ledger_segments(root, {})  # ledger present, but zero segments at all

    result = run_assemble(root)
    assert result.returncode == 2, f"{label}:\n{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "no_converged_segments", (
        f"{label}: the malformed manifest must not preempt the pre-existing "
        f"no_converged_segments precondition: {payload}"
    )


def test_malformed_manifest_with_a_converged_segment_present_still_malformed_manifest(tmp_path):
    """Case 4 (fence): once at least one segment HAS converged, the
    pre-existing malformed_manifest raise (assert_project_complete(), exit
    1) must still fire -- proves the round-2-on-round-2 fix (making the
    loader's own call non-raising) did not also disarm the AUTHORITATIVE
    raise a few lines later. Mutation: same as above -- if the loader's
    call independently starts raising again, this test stays green for the
    WRONG reason (an earlier raise, not assert_project_complete()'s own),
    so it must be read alongside the two cases above, not in isolation."""
    root = make_root(tmp_path)
    # The scaffold is written first and then DELIBERATELY overwritten with the
    # malformed manifest below: since #492 the converged record's stored
    # cache_key is computed for real, which needs a well-formed manifest and a
    # segpack to exist at fixture-build time. What the test asserts is
    # unchanged -- assembly still sees only the malformed manifest.
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    write_ledger_segments(root, {"seg01": converged_ledger_record(root, "seg01")})
    _write_manifest_segments(root, [])  # malformed: empty segments array

    result = run_assemble(root)
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "malformed_manifest"


# ===========================================================================
# 20. Round 3 (security-review finding): the membership test in
#     load_converged_segments()'s stale branch must run BEFORE
#     _stale_carveout_refusal_reason() is even called, not merely before
#     the fatal draft/sha1 checks -- otherwise an out-of-manifest entry
#     that FAILS the carve-out still costs a refusal (and the sentinel
#     lstat inside that function's own condition 4), even though the
#     entire point of the scoping is that a retained out-of-manifest entry
#     is invisible to this book.
# ===========================================================================


def test_out_of_manifest_stale_entry_that_fails_the_carveout_is_still_invisible(tmp_path):
    """Complementary to section 18's
    test_out_of_manifest_carved_out_stale_records_are_skipped_with_no_refusal:
    that test covers an out-of-manifest record that PASSES the #491
    carve-out (machinery-only mismatch, sentinel present) -- this one
    covers a record that FAILS it (a content-affecting field moved, so
    _stale_carveout_refusal_reason() would normally return a refusal
    string). Together the pair proves the membership skip is unconditional
    on carve-out outcome, not just on the carve-out's happy path.

    Before this fix, a FAILING out-of-manifest record still landed in
    `refusals` (the old code recorded the refusal, THEN tested
    membership), which flipped main()'s diagnostic from
    no_converged_segments to project_incomplete for a project where
    nothing had actually converged -- observable even though both are
    exit 2, because the reason string differs and the refused segment
    leaks into the diagnostic text. Mutation: move the membership test
    back below the refusal-recording `continue` -> red on the reason
    string (project_incomplete instead of no_converged_segments)."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])  # a well-formed manifest, but seg01 itself never converges
    write_ledger_segments(root, {})  # ledger present; seg01 has no entry at all
    _add_out_of_manifest_entry(
        root,
        # "used_terms_hash" is content-affecting -- fails
        # _stale_carveout_refusal_reason()'s own condition 3, were it ever
        # called for this entry.
        _raw_stale_record(mismatched_fields=("used_terms_hash",)),
    )

    result = run_assemble(root)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "no_converged_segments", (
        f"an out-of-manifest stale record that FAILS the carve-out must "
        f"never cost a refusal either -- that would surface a segment "
        f"this book does not contain and flip the diagnostic to "
        f"project_incomplete: {payload}"
    )
    assert "seg99" not in result.stdout, (
        "a skipped out-of-manifest entry must never surface in the "
        "output, whether it passes or fails the carve-out"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
