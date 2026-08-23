"""tests/stale_fragment_injection.test.py -- #491 post-review fix: a
hand-written (or attacker/operator-planted) `runs/ledger.d/*.json` fragment
must never be able to fabricate `stale_mismatched_fields` on the
materialized `runs/ledger.json` entry.

THE DEFECT this file pins shut. `stale_mismatched_fields` (1.25.0, #491) is
an AUTHORIZATION TOKEN: assemble.py's own machinery-only carve-out
(`_stale_carveout_refusal_reason`) trusts it to mean "ledger_merge.py itself
diffed the stored and current cache keys, and every field that moved is
machinery-only" -- and ships a 'stale' segment without re-review whenever
that trust holds. But `_read_fragments()` applies NO fragment-schema
validation on read, so nothing stops a fragment from carrying this key with
an arbitrary, never-computed value. Before this fix, the materialization
loop (`merge()`, right after `entry = dict(record)`) only ever ADDED
`stale_mismatched_fields` -- it never REMOVED one a fragment already
supplied -- so a planted list survived verbatim onto the materialized
entry. And because ledger.schema.json (1.25.0) now DECLARES the property
(deliberately absent from ledger-fragment.schema.json and
ledger-record-base.schema.json, so a fragment carrying it fails ITS OWN
schema), the final `unevaluatedProperties: false` validation on ledger.json
no longer catches the leak the way it would have before this release. A
planted `["plugin_bundle_hash"]` on a converged fragment with no dict
`cache_key` (so `_compute_stale_segments()`'s own "nothing to diff" branch
fires and computes no diff at all) would therefore materialize as a
schema-valid 'stale' entry that assemble.py's carve-out would ship without
ever actually comparing anything -- defeating the change's central
fail-safe.

THE FIX (ledger_merge.py's materialization loop): `entry.pop(
"stale_mismatched_fields", None)` immediately after `entry = dict(record)`,
unconditionally, for every entry -- before the stale branch below gets a
chance to set the real one from its own freshly computed diff. The
materialized value is now provably merge-computed, never inherited.

Cases 1-2 below are the injection cases (case 1 through the full
ledger_merge.py -> assemble.py pipeline, matching how these two scripts are
actually chained in production by W9's mergeLedgerPrompt / assemble step;
case 2 is the sibling shape on a segment that stays 'converged'). Cases 3-4
are MANDATORY positive controls: without them, a "fix" that simply deleted
stale_mismatched_fields support entirely (rather than fixing the
inheritance bug) would also make case 1 pass vacuously.

## Fixture strategy

One merged durable_root, wired for BOTH scripts under test -- unlike
tests/stale_carveout.test.py's two separate harnesses (one per script),
this file's whole point is the SEAM between them, so ledger_merge.py and
assemble.py are copied into the SAME root and run in sequence against it,
exactly as W9 chains them in production. Each script still self-anchors
independently (`Path(__file__).resolve().parents[1]` for ledger_merge.py,
`SCRIPTS_DIR.parent` for assemble.py) -- both resolve to this one root.
Mirrors tests/stale_carveout.test.py's own conventions throughout (the fake
cache_key.py stub, the book-scaffold/draft/sentinel helpers, the
subprocess-driving pattern) but restated here rather than imported, per
this suite's "each test file stays self-contained" convention.
"""
import importlib.util
import json
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
CACHE_KEY_REAL_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

for _src in (
    LEDGER_MERGE_SRC, ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
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


# The authoritative 15-field cache-key list, restated (house convention for
# this plugin's self-contained scripts -- see ledger_merge.py's own
# CACHE_KEY_FIELDS docstring note).
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

# One representative field from each side of assemble.py's own
# SAFE_STALE_CARVEOUT_FIELDS = {"plugin_bundle_hash", "schema_hash",
# "derivation_bundle_hash"} allowlist -- used to build genuine (case 3) vs.
# content-affecting (case 4) positive-control mismatches.
MACHINERY_ONLY_FIELD = "plugin_bundle_hash"
CONTENT_AFFECTING_FIELD = "used_terms_hash"

# A fixture stand-in for the real cache_key.py -- same `--seg <id>` -> JSON-
# object-on-stdout interface, restated per tests/stale_carveout.test.py's own
# FAKE_CACHE_KEY_PY (duplicated deliberately -- see module docstring).
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


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def _yaml_dump(obj) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False)


def default_profile():
    """A minimal, schema-valid profile.yml -- restated from
    tests/stale_carveout.test.py's own default_profile(), trimmed to
    nothing this file's single-segment fixtures exercise (no verse mode,
    index, or mentions-section variation needed here)."""
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


def make_root(tmp_path) -> Path:
    """A single durable_root wired for BOTH scripts under test. Both
    self-anchor to it independently (ledger_merge.py via
    `Path(__file__).resolve().parents[1]`, assemble.py via
    `SCRIPTS_DIR.parent`), so running ledger_merge.py first and then
    assemble.py against the SAME root reproduces the real production
    chain: W9's mergeLedgerPrompt runs ledger_merge.py, then the assemble
    step reads exactly what it wrote."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)

    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_REAL_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(src.parent / "json_stdout.py", scripts_dir / "json_stdout.py")

    # #492: the two scripts need DIFFERENT cache_key.py copies in this one
    # root, so the fake no longer sits at scripts/cache_key.py.
    #
    # ledger_merge.py wants the FAKE -- its whole point here is a fixed,
    # fixture-controlled key table (test_fixture_cache_keys.json), and it
    # resolves the checker from --plugin-root as
    # `{plugin_root}/assets/scripts/cache_key.py` precisely so the copy it
    # trusts is not the one sitting in the writable durable root. So the fake
    # is staged at exactly that path and run_merge() passes --plugin-root.
    #
    # assemble.py wants the REAL module: since #492 it IMPORTS cache_key as a
    # sibling of its own SCRIPTS_DIR to recompute the live content-affecting
    # fields, and the fake exposes only a CLI main() -- accessing
    # CACHE_KEY_FIELD_ORDER on it raises AttributeError at import.
    fake_plugin_scripts = root / "fake_plugin" / "assets" / "scripts"
    fake_plugin_scripts.mkdir(parents=True)
    (fake_plugin_scripts / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")
    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()

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
    write_fixture_cache_keys(root, {})
    return root


def write_fragment(root, seg, record) -> Path:
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return frag_path


def write_fixture_cache_keys(root, mapping) -> None:
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def converged_fragment(cache_key=None, rounds=1, reviewed_draft_sha1=None, extra=None) -> dict:
    """A 'converged' fragment. `cache_key=None` reproduces the real-world
    shape a converged fragment with no recorded cache key takes (the
    `not isinstance(stored_key, dict)` branch in `_compute_stale_segments`)
    -- omits `cache_key`/`n_blocks`/`n_footnotes`/`n_verses` entirely, all
    of which the base schema's conditional only requires for a MATERIALIZED
    entry that is still status=='converged' (this fragment's materialized
    status, once merged, will be 'stale' either way here). `extra`, when
    given, is merged in last -- used by the injection tests to plant
    `stale_mismatched_fields` directly onto the fragment, exactly as an
    unvalidated hand-written/tampered fragment could."""
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "reviewed_draft_sha1": reviewed_draft_sha1 or ("d" * 40),
    }
    if cache_key is not None:
        record["cache_key"] = cache_key
        record["n_blocks"] = 3
        record["n_footnotes"] = 1
        record["n_verses"] = 0
    if extra:
        record.update(extra)
    return record


def run_merge(root, *extra_args, timeout=30) -> subprocess.CompletedProcess:
    # --plugin-root points at the staged FAKE cache_key.py (see make_root):
    # this is ledger_merge.py's own documented resolution path, not a test
    # workaround. Callers may still pass their own --plugin-root; argparse
    # takes the last occurrence, so an explicit one in *extra_args wins.
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"),
         "--plugin-root", str(root / "fake_plugin"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc) -> dict:
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def load_ledger_json(root) -> dict:
    return json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))


def load_ledger_merge_module(root):
    """The COPY inside `root/scripts/` -- self-anchored, so
    `_build_schema_registry()`/`_validator_for()` resolve against THIS
    fixture's own `root/schemas`, never the real plugin assets tree."""
    return _load_module_from_source(
        root / "scripts" / "ledger_merge.py",
        f"stale_fragment_injection__ledger_merge_{id(root)}",
    )


def assert_ledger_json_valid(root, doc) -> None:
    """Independent, explicit schema check -- belt-and-suspenders alongside
    a successful merge's own internal validation (merge() already refuses
    to write an invalid document; returncode==0 already implies this), using
    the REAL schema-loading machinery ledger_merge.py itself uses rather
    than a hand-rolled reimplementation of it."""
    merge_mod = load_ledger_merge_module(root)
    registry = merge_mod._build_schema_registry(root / "schemas")
    validator = merge_mod._validator_for("ledger.schema.json", registry, root / "schemas")
    errors = list(validator.iter_errors(doc))
    assert not errors, (
        f"materialized ledger.json failed its own schema: {[e.message for e in errors]}"
    )


def write_book_scaffold(root, seg_ids) -> None:
    """Manifest + segpacks for N trivial one-block, no-sentinel segments --
    restated from tests/stale_carveout.test.py's own write_book_scaffold()."""
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


def mark_sentinel_present(root, seg) -> Path:
    path = root / "segments" / f".ever_converged.{seg}"
    path.write_bytes(b"converged\n")
    return path


def run_assemble(root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


# ===========================================================================
# 1. INJECTION, end to end: ledger_merge.py -> assemble.py.
# ===========================================================================


def test_injected_stale_mismatched_fields_stripped_end_to_end_and_assembly_refuses(tmp_path):
    """Case 1 (mandatory). A converged fragment with NO dict `cache_key` --
    so `_compute_stale_segments()`'s own "nothing to diff" branch fires
    (`stale.add(seg); continue`, BEFORE any subprocess call or field-by-field
    comparison) -- plants `stale_mismatched_fields` anyway. Before the fix,
    `entry = dict(record)` copied it straight onto the materialized entry
    and the old code only ever ADDED the key inside the stale branch, never
    removing an inherited one, so this planted list survived verbatim: a
    schema-valid 'stale' entry claiming a diff that was never computed.

    End to end: run the REAL ledger_merge.py, then the REAL assemble.py over
    the resulting runs/ledger.json, matching how W9 actually chains them.

    Mutation M1 (remove the `entry.pop(...)` call, restoring inheritance):
    both the ledger.json assertion and the assemble.py refusal-reason
    assertion below go red -- the planted field survives and the carve-out
    ships the segment without ever diffing anything."""
    root = make_root(tmp_path)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    mark_sentinel_present(root, "seg01")

    write_fragment(
        root,
        "seg01",
        converged_fragment(
            cache_key=None,
            extra={"stale_mismatched_fields": [MACHINERY_ONLY_FIELD]},
        ),
    )

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"]

    doc = load_ledger_json(root)
    entry = doc["segments"]["seg01"]
    assert entry["status"] == "stale"
    assert "stale_mismatched_fields" not in entry, (
        f"a fragment-planted stale_mismatched_fields must never survive "
        f"into the materialized entry -- got "
        f"{entry.get('stale_mismatched_fields')!r}"
    )
    assert_ledger_json_valid(root, doc)

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"with the planted field correctly stripped, nothing was ever "
        f"actually diffed for this segment, so the carve-out must refuse:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assembled = parse_one_json_line(result)
    assert assembled["reason"] == "project_incomplete"
    assert "seg01" in assembled["error"]
    assert "no usable stale_mismatched_fields" in assembled["error"], (
        f"must refuse for the SPECIFIC 'carries no usable "
        f"stale_mismatched_fields' reason (the fail-safe direction for a "
        f"missing/empty/non-list value), not some other check: "
        f"{assembled['error']}"
    )


# ===========================================================================
# 2. INJECTION on a segment that stays converged.
# ===========================================================================


def test_injected_stale_mismatched_fields_stripped_on_a_converged_entry(tmp_path):
    """Case 2 (mandatory). A fragment whose `cache_key` matches the current
    one exactly -- the segment never even enters the stale branch -- still
    plants `stale_mismatched_fields`. Before the fix, `entry = dict(record)`
    copied it through regardless of status: the old `if seg in
    stale_segments:` guard controlled only whether `status` flipped and a
    FRESH value was computed, never whether an already-present inherited
    value leaked through for segments that never reached that branch at
    all.

    Mutation M1: red -- the planted field survives on a converged entry."""
    root = make_root(tmp_path)
    stored_key = make_cache_key("match")
    write_fixture_cache_keys(root, {"seg01": stored_key})
    write_fragment(
        root,
        "seg01",
        converged_fragment(
            cache_key=stored_key,
            extra={"stale_mismatched_fields": [MACHINERY_ONLY_FIELD]},
        ),
    )

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == [], "the matching cache_key must not flip this stale"

    doc = load_ledger_json(root)
    entry = doc["segments"]["seg01"]
    assert entry["status"] == "converged"
    assert "stale_mismatched_fields" not in entry, (
        f"a fragment-planted stale_mismatched_fields must never survive on "
        f"a converged entry either -- got "
        f"{entry.get('stale_mismatched_fields')!r}"
    )
    assert_ledger_json_valid(root, doc)


# ===========================================================================
# 3-4. Positive controls: a genuine diff is still recorded.
# ===========================================================================


def test_genuine_machinery_only_mismatch_is_still_recorded(tmp_path):
    """Case 3 (mandatory positive control). Without this test, a "fix" that
    simply deleted stale_mismatched_fields support entirely -- rather than
    fixing the inheritance bug -- would also make case 1 pass vacuously.
    Pins that a REAL, freshly computed diff (a dict `cache_key` that differs
    from the current one in exactly one machinery-only field) is still
    recorded exactly as before this fix.

    Mutation M2 (drop the `if fields:` set entirely, turning the pop into an
    unconditional always-delete): red here -- the genuine diff is stripped
    too, right alongside the planted one."""
    root = make_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = dict(stored_key)
    current_key[MACHINERY_ONLY_FIELD] = f"{MACHINERY_ONLY_FIELD}-DIFFERENT"
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(cache_key=stored_key))

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"]

    doc = load_ledger_json(root)
    entry = doc["segments"]["seg01"]
    assert entry["status"] == "stale"
    assert entry["stale_mismatched_fields"] == [MACHINERY_ONLY_FIELD], (
        f"a genuine, freshly-computed diff must still be recorded -- got "
        f"{entry.get('stale_mismatched_fields')!r}"
    )
    assert_ledger_json_valid(root, doc)


def test_genuine_content_affecting_mismatch_is_still_recorded(tmp_path):
    """Case 4 (mandatory positive control), the content-affecting sibling of
    case 3 -- pins that the fix's `entry.pop(...)` is unconditional (runs for
    every entry, machinery-only or not) while the SET below it still fires
    correctly for a content-affecting diff too. Which field is
    machinery-only vs. content-affecting is assemble.py's own carve-out
    decision (SAFE_STALE_CARVEOUT_FIELDS), never this script's -- this test
    only pins that ledger_merge.py still reports the diff it found.

    Mutation M2: red here too, for the same reason as case 3."""
    root = make_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = dict(stored_key)
    current_key[CONTENT_AFFECTING_FIELD] = f"{CONTENT_AFFECTING_FIELD}-DIFFERENT"
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(cache_key=stored_key))

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"]

    doc = load_ledger_json(root)
    entry = doc["segments"]["seg01"]
    assert entry["status"] == "stale"
    assert entry["stale_mismatched_fields"] == [CONTENT_AFFECTING_FIELD], (
        f"got {entry.get('stale_mismatched_fields')!r}"
    )
    assert_ledger_json_valid(root, doc)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
