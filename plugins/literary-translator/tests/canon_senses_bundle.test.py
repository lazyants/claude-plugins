"""tests/canon_senses_bundle.test.py -- RFC #215's cache-bundle registration
of canon_senses.py (Cache section "CODE != data", R5-F3; contract §7).

canon_senses.py is imported by TWO existing PLUGIN_BUNDLE_MEMBERS scripts
(canon_validate.py's recollapse guard, glossary_batch_plan.py's split-form
exclusion), so its own bytes must be part of plugin_bundle_hash -- the
bundle is a literal filename allowlist (`cache_key.py`'s
`PLUGIN_BUNDLE_MEMBERS`) that hashes only the listed files' bytes; a
transitive import is otherwise invisible to it. This file locks down three
things, per the plan's own enumeration:

  (a) A canon_senses.json sidecar DATA-only edit leaves the full 15-field
      cache_key unchanged (characterization -- canon_senses.json is a
      runtime sidecar, never a bundle member; green on `main` too, since
      cache_key.py has never read it).
  (b) "canon_senses.py" is registered in PLUGIN_BUNDLE_MEMBERS, and a byte
      edit to it changes the documented Step-0a plugin_bundle_hash formula
      (sha1 over sorted-by-filename concatenated bundle-member bytes,
      re-derived independently here, never imported from any script --
      same convention as tests/ledger_composite_key.test.py) ONLY when it
      is a registered member -- proving the same edit would have been
      INVISIBLE to the formula before this fix (post-contract, NOT green
      on `main`). An end-to-end proof then threads that changed
      plugin_bundle_hash value through the REAL select_segments.py
      classifier (same stub-cache_key.py convention as
      tests/select_segments.test.py) to confirm a converged segment goes
      straight to `stale` (never `blocked_needs_regeneration` --
      plugin_bundle_hash is not one of the four DERIVATION_STATE_FIELDS).
  (c) A doc-count guard: references/ledger-and-resumability.md's "eleven
      scripts" enumeration under the plugin_bundle_hash bullet matches
      PLUGIN_BUNDLE_MEMBERS's own script filenames (the two workflow
      templates are documented separately and excluded from the count).

cache_key.py itself never recomputes plugin_bundle_hash (it reads
`runs/.plugin_bundle_hash` verbatim, stamped once per run by Step 0a --
see tests/ledger_composite_key.test.py's own
test_plugin_bundle_hash_read_from_marker_verbatim) -- so (b)'s formula
re-derivation is checked independently against the doc's own prose, exactly
like every other field in ledger_composite_key.test.py.
"""
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
LEDGER_DOC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "ledger-and-resumability.md"
)

assert CACHE_KEY_SRC.is_file(), f"cache_key.py not found at {CACHE_KEY_SRC}"
assert LEDGER_DOC.is_file(), f"ledger-and-resumability.md not found at {LEDGER_DOC}"

CACHE_KEY_FIELD_ORDER = (
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
)


def _load_cache_key_module():
    """In-process load of the REAL cache_key.py, purely to read its own
    PLUGIN_BUNDLE_MEMBERS tuple -- never to call its compute functions (see
    module docstring: every hash below is re-derived independently)."""
    spec = importlib.util.spec_from_file_location("cache_key_bundle_test", CACHE_KEY_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CACHE_KEY_MODULE = _load_cache_key_module()
PLUGIN_BUNDLE_MEMBERS = CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS


# ===========================================================================
# (a) canon_senses.json sidecar DATA-only edit leaves the 15-field key
#     unchanged -- characterization, green on `main`.
# ===========================================================================


def canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _default_profile() -> dict:
    return {
        "project": {"pipeline_version": "v1.0.0"},
        "engine": {"effort": "medium", "max_fix_rounds": 3, "batch_agent_cap": 10},
        "source": {
            "format": "plain_text",
            "path": "/logical/original/path.txt",
            "language": {"code": "fr", "particle_config": "fr_particles.json"},
            "adapter_config": {"plain_text": {"encoding": "utf-8"}},
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": 4},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }


def make_durable_root(tmp_path) -> Path:
    """A complete, self-consistent durable_root fixture sufficient for
    cache_key.py's full `--seg` computation to succeed unmodified -- same
    shape as tests/ledger_composite_key.test.py's own make_durable_root
    (kept independent/self-contained per this plugin's test convention:
    fixture builders are not shared across .test.py files)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(CACHE_KEY_SRC, scripts_dir / "cache_key.py")

    import yaml

    (root / "profile.yml").write_text(yaml.safe_dump(_default_profile(), sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    begin, end = b"<!-- STYLE_CONTRACT_BEGIN -->", b"<!-- STYLE_CONTRACT_END -->"
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n" + begin + b"\n## A. Tone\nFormal.\n" + end + b"\n\n## G. Glossary\n"
    )

    schemas_dir = root / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "draft.schema.json").write_bytes(b'{"draft":"v1"}')
    (schemas_dir / "review.schema.json").write_bytes(b'{"review":"v1"}')
    (schemas_dir / "segpack.schema.json").write_bytes(b'{"segpack":"v1"}')

    (root / "translate_TASK.md").write_bytes(b"TRANSLATE v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW v1\n")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr_particles.json").write_bytes(b'{"particles": ["de"]}')

    (root / "extract.py").write_bytes(b"# extract.py v1\n")

    source_file = root / "source_original.txt"
    source_file.write_bytes(b"Ceci est le texte source.\n")
    (root / "manifest.json").write_text(
        json.dumps({"source_inputs": [str(source_file.resolve())]}), encoding="utf-8"
    )

    (scripts_dir / "bootstrap_names.py").write_bytes(b"# bootstrap_names.py v1\n")
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py v1\n")

    runs_dir = root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text("baseline-marker\n", encoding="utf-8")

    (root / "canon.json").write_text(
        json.dumps({"entries": {"Jean": {"target": "Жан"}}, "review_queue": []}), encoding="utf-8"
    )

    (root / "segments").mkdir()
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(
            {
                "seg": "seg01",
                "blocks": [{"id": "b0", "order_index": 0, "plain_text": "Bonjour"}],
                "canon_names": ["Jean"],
                "new_names": [],
                "verses": [],
                "footnotes": [],
            }
        ),
        encoding="utf-8",
    )

    return root


def full_key(root: Path, seg: str = "seg01") -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"cache_key.py --seg {seg} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_canon_senses_json_sidecar_data_edit_leaves_cache_key_unchanged(tmp_path):
    root = make_durable_root(tmp_path)
    baseline = full_key(root, seg="seg01")

    (root / "canon_senses.json").write_text(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "entries_by_source_form": {
                    "Jean": {
                        "senses": [
                            {
                                "sense_id": "s1",
                                "disambiguator": "the apostle",
                                "index_scope": "narrative",
                                "evidence": {
                                    "block": "b0", "seg": "seg01",
                                    "char_start": 0, "char_end": 4,
                                    "context_start": 0, "context_end": 7,
                                    "sha256": "0" * 64,
                                },
                            },
                            {
                                "sense_id": "s2",
                                "disambiguator": "a fisherman",
                                "index_scope": "allusion",
                                "evidence": {
                                    "block": "b0", "seg": "seg01",
                                    "char_start": 0, "char_end": 4,
                                    "context_start": 0, "context_end": 7,
                                    "sha256": "1" * 64,
                                },
                            },
                        ]
                    }
                },
            }
        ).decode("utf-8"),
        encoding="utf-8",
    )
    after_write = full_key(root, seg="seg01")
    assert after_write == baseline

    # Editing the sidecar's DATA further (a totally different split) still
    # doesn't move a single one of the 15 fields.
    (root / "canon_senses.json").write_text(
        canonical_json_bytes({"schema_version": 1, "entries_by_source_form": {}}).decode("utf-8"),
        encoding="utf-8",
    )
    after_second_edit = full_key(root, seg="seg01")
    assert after_second_edit == baseline


# ===========================================================================
# (b) canon_senses.py registration + the documented bundle-hash formula.
# ===========================================================================


def test_canon_senses_py_registered_in_plugin_bundle_members():
    assert "canon_senses.py" in PLUGIN_BUNDLE_MEMBERS
    # And still exactly the members the doc enumerates, not e.g. duplicated.
    assert PLUGIN_BUNDLE_MEMBERS.count("canon_senses.py") == 1


def _documented_plugin_bundle_hash(scripts_dir: Path, members) -> str:
    """Re-derives the doc's OWN stated formula (ledger-and-resumability.md:
    "single sha1s over the concatenated bytes of their member files, sorted
    by filename for determinism") -- never calls any script's own compute
    function, exactly like every other hash in tests/ledger_composite_key.
    test.py."""
    paths = sorted((scripts_dir / name for name in members), key=lambda p: p.name)
    blob = b"".join(p.read_bytes() for p in paths)
    return hashlib.sha1(blob).hexdigest()


def _stage_all_bundle_members(scripts_dir: Path) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in PLUGIN_BUNDLE_MEMBERS:
        if name.endswith(".template.js"):
            shutil.copy2(TEMPLATES_SRC_DIR / name, scripts_dir / name)
        else:
            shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)


def test_canon_senses_py_byte_edit_changes_bundle_hash_only_when_registered(tmp_path):
    scripts_dir = tmp_path / "scripts"
    _stage_all_bundle_members(scripts_dir)

    unregistered_members = tuple(m for m in PLUGIN_BUNDLE_MEMBERS if m != "canon_senses.py")

    hash_registered_before = _documented_plugin_bundle_hash(scripts_dir, PLUGIN_BUNDLE_MEMBERS)
    hash_unregistered_before = _documented_plugin_bundle_hash(scripts_dir, unregistered_members)

    # Edit ONLY canon_senses.py's bytes (append a harmless trailing comment
    # -- mirrors ledger_composite_key.test.py's own byte-edit style).
    canon_senses_copy = scripts_dir / "canon_senses.py"
    canon_senses_copy.write_bytes(canon_senses_copy.read_bytes() + b"\n# trailing comment\n")

    hash_registered_after = _documented_plugin_bundle_hash(scripts_dir, PLUGIN_BUNDLE_MEMBERS)
    hash_unregistered_after = _documented_plugin_bundle_hash(scripts_dir, unregistered_members)

    # WITH canon_senses.py registered (the real, post-contract
    # PLUGIN_BUNDLE_MEMBERS), the edit is visible to the bundle hash.
    assert hash_registered_after != hash_registered_before

    # The SAME edit, computed over the bundle as it would have been
    # BEFORE this fix (canon_senses.py absent from the member list), is
    # completely invisible -- this is exactly the bug R5-F3 closes: a
    # loader-logic edit that changes what canon.json is built from, with
    # no corresponding change to plugin_bundle_hash.
    assert hash_unregistered_after == hash_unregistered_before


# ===========================================================================
# End-to-end: that changed plugin_bundle_hash value flips a converged
# segment straight to `stale` via the REAL select_segments.py classifier
# (never blocked_needs_regeneration -- plugin_bundle_hash isn't a
# DERIVATION_STATE_FIELD). Same stub-cache_key.py convention as
# tests/select_segments.test.py.
# ===========================================================================

SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

assert SELECT_SEGMENTS_SRC.is_file(), f"select_segments.py not found at {SELECT_SEGMENTS_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"

FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DURABLE_ROOT = HERE.parent
KEYS_PATH = DURABLE_ROOT / "test_fixture_cache_keys.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    args = parser.parse_args()
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_select_segments_root(tmp_path) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    return root


def _make_cache_key(seed: str) -> dict:
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELD_ORDER}


def test_canon_senses_py_bundle_hash_mismatch_flips_segment_direct_stale(tmp_path):
    scripts_dir = tmp_path / "bundle_scripts"
    _stage_all_bundle_members(scripts_dir)
    hash_before_edit = _documented_plugin_bundle_hash(scripts_dir, PLUGIN_BUNDLE_MEMBERS)

    canon_senses_copy = scripts_dir / "canon_senses.py"
    canon_senses_copy.write_bytes(canon_senses_copy.read_bytes() + b"\n# edited\n")
    hash_after_edit = _documented_plugin_bundle_hash(scripts_dir, PLUGIN_BUNDLE_MEMBERS)
    assert hash_after_edit != hash_before_edit

    root = make_select_segments_root(tmp_path)
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": "seg01"}]}, ensure_ascii=False), encoding="utf-8"
    )

    current_key = _make_cache_key("current")
    current_key["plugin_bundle_hash"] = hash_after_edit
    stored_key = dict(current_key)
    stored_key["plugin_bundle_hash"] = hash_before_edit

    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps({"seg01": current_key}, ensure_ascii=False), encoding="utf-8"
    )

    draft_content = json.dumps({"text": "draft"}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (root / "segments" / "seg01.draft.json").write_bytes(draft_content)
    reviewed_draft_sha1 = hashlib.sha1(draft_content).hexdigest()

    fragment = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "cache_key": stored_key,
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }
    (root / "runs" / "ledger.d" / "seg01.json").write_text(
        json.dumps(fragment, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py")],
        capture_output=True, text=True, timeout=30, cwd=str(root),
    )
    assert proc.returncode in (0, 1), f"unexpected select_segments.py failure:\n{proc.stdout}\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got:\n{proc.stdout}"
    result = json.loads(lines[0])

    seg_result = result["classification"]["seg01"]
    assert seg_result == {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": ["plugin_bundle_hash"],
    }


# ===========================================================================
# (c) Doc-count guard: ledger-and-resumability.md's "eleven scripts"
#     enumeration matches PLUGIN_BUNDLE_MEMBERS exactly (scripts only, the
#     two workflow templates are documented/counted separately).
# ===========================================================================


def _parse_ledger_bundle_count_and_enumeration():
    text = LEDGER_DOC.read_text(encoding="utf-8")
    # Anchor on the "three separate bundle hashes -- exact membership"
    # section specifically: an EARLIER, summary-only plugin_bundle_hash
    # bullet (in the 15-field composite-key section) also mentions a
    # script count but never enumerates individual filenames -- a
    # whole-file search for either "**`plugin_bundle_hash`**" or "plus the
    # two" would grab that wrong span instead of this section's own
    # detailed enumeration.
    section_start = text.index("## The three separate bundle hashes")
    bullet_start = text.index("**`plugin_bundle_hash`**", section_start)
    m = re.search(r"covers exactly \*\*([a-z]+)\s+scripts\*\*", text[bullet_start:])
    assert m, "no 'covers exactly **<word> scripts**' phrase found under plugin_bundle_hash"
    count_word = m.group(1)

    # The enumeration line(s) follow "plus the two\n  workflow templates:"
    # and end at the templates themselves ("mass-translate-wf.template.js").
    start = text.index("plus the two", bullet_start)
    end = text.index("mass-translate-wf.template.js", start)
    enumeration_span = text[start:end]
    names = re.findall(r"`([A-Za-z0-9_.-]+\.py)`", enumeration_span)
    assert names, f"no backtick-quoted script names found in enumeration span: {enumeration_span!r}"
    return count_word, names


_NUMBER_WORDS = {
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def test_ledger_doc_bundle_count_and_enumeration_match_plugin_bundle_members():
    count_word, doc_names = _parse_ledger_bundle_count_and_enumeration()

    script_members = tuple(m for m in PLUGIN_BUNDLE_MEMBERS if m.endswith(".py"))

    assert count_word in _NUMBER_WORDS, f"unrecognized count word {count_word!r}"
    assert _NUMBER_WORDS[count_word] == len(script_members), (
        f"doc says {count_word!r} ({_NUMBER_WORDS[count_word]}) scripts, "
        f"PLUGIN_BUNDLE_MEMBERS has {len(script_members)} .py members: {script_members}"
    )
    assert set(doc_names) == set(script_members), (
        f"doc enumeration {sorted(doc_names)} != PLUGIN_BUNDLE_MEMBERS .py members "
        f"{sorted(script_members)}"
    )
    assert "canon_senses.py" in doc_names, "canon_senses.py missing from the doc's own enumeration"


def test_ledger_doc_summary_bullet_count_also_in_sync():
    """The EARLIER, summary-only plugin_bundle_hash bullet (in the 15-field
    composite-key section, before "## The three separate bundle hashes")
    also states a script count in prose ("the sha1 of sorted,
    filename-concatenated bytes of the <N> generic scripts...") without
    enumerating filenames -- a second site that would silently go stale if
    only the detailed section were updated."""
    text = LEDGER_DOC.read_text(encoding="utf-8")
    section_start = text.index("## The three separate bundle hashes")
    summary_text = text[:section_start]
    m = re.search(r"bytes of the ([a-z]+) generic scripts", summary_text)
    assert m, "no 'bytes of the <word> generic scripts' summary phrase found"
    count_word = m.group(1)
    script_members = tuple(mem for mem in PLUGIN_BUNDLE_MEMBERS if mem.endswith(".py"))
    assert _NUMBER_WORDS[count_word] == len(script_members), (
        f"summary bullet says {count_word!r} generic scripts, but "
        f"PLUGIN_BUNDLE_MEMBERS has {len(script_members)} .py members"
    )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
