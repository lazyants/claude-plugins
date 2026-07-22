"""tests/backlink_integrity_e2e.test.py -- the binding, in-tree acceptance
test for the appendix backlink-integrity feature (RFC lt-appendix-backlink-
integrity, 1.8.0). SSK vol.2's real data is not in-tree (see
`canon_senses.test.py:7-10`'s own precedent), so THIS file's fixture chain
vs. the committed `fixtures/backlink_e2e/expected_vault/` is the binding
proof the whole D1-D4 chain actually works end to end, not just per-module
unit tests.

## What this drives (the REAL pipeline, no stubs)

`fixtures/backlink_e2e/case_spec.py`'s hand-authored manifest/segpack/draft
content -> `assemble.py` (subprocess, `mentions_section.enabled` flag on or
off) -> `render_obsidian.render()` (via `dispatch_adapter`, in-process
inside that same subprocess) -> `validate_backlinks.py` (subprocess). Every
script under test is a REAL COPY of the shipped
`skills/literary-translator/assets/scripts/*.py` file, run exactly as
production runs it (self-anchored `${durable_root}/scripts/`, no stubs, no
monkeypatching) -- mirrors `tests/assemble.test.py`'s own "real subprocess,
real durable_root" fixture strategy, extended with the sibling scripts D1-D4
added (`occurrence_targets.py`, `validate_backlinks.py`,
`bootstrap_names.py`, `occ_index.py`, `canon_senses.py`).

## Fixture staging: reused helpers, not reinvented

The manifest/segpack/draft/ledger FILE-WRITING mechanics below are copied
(never imported -- `tests/assemble.test.py` is a `*.test.py` file pytest
owns, not an importable module; `tests/verse_footnote_corpus.py`'s own
module docstring documents this same convention) from
`tests/assemble.test.py`'s `write_manifest`/`write_segpack`/`write_draft`/
`write_ledger`, extended here with a minimal, schema-valid `canon_map: {}`
on every segpack (`segpack.schema.json:132` requires the key; assemble.py
itself never reads it, so an empty dict is harmless-but-schema-complete --
see `test_fixture_static_files_and_staged_manifest_are_schema_valid`
below, which validates the staged segpacks against the real schema).

## Dependency gate

If a teammate's piece (`occurrence_targets.py`, `validate_backlinks.py`, or
render_obsidian.py's D1/D3 mentions-section wiring) has not landed in this
worktree yet, every test in this module is SKIPPED (not errored/failed)
with a clear one-line reason naming exactly which piece is missing -- see
`_MISSING_DEPENDENCIES` below.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "backlink_e2e"

# ---------------------------------------------------------------------------
# Dependency gate -- see module docstring. Checked at COLLECTION time so a
# missing piece SKIPS cleanly rather than erroring on the first import that
# needs it.
# ---------------------------------------------------------------------------

_REQUIRED_SCRIPTS = (
    "assemble.py", "output_resolve.py", "render_obsidian.py", "validate_draft.py",
    "occurrence_targets.py", "validate_backlinks.py", "bootstrap_names.py",
    "occ_index.py", "canon_senses.py",
)


def _detect_missing_dependencies() -> list:
    missing = [name for name in _REQUIRED_SCRIPTS if not (SCRIPTS_SRC_DIR / name).is_file()]
    if "render_obsidian.py" not in missing:
        text = (SCRIPTS_SRC_DIR / "render_obsidian.py").read_text(encoding="utf-8")
        if "lt:mentions:begin" not in text or "_effective_mentions_enabled" not in text:
            missing.append("render_obsidian.py (D1/D3 mentions-section + collision_delink wiring)")
    if "assemble.py" not in missing:
        text = (SCRIPTS_SRC_DIR / "assemble.py").read_text(encoding="utf-8")
        if "_attach_mentions" not in text:
            missing.append("assemble.py (D1 nodestream[\"mentions\"] attachment wiring)")
    return missing


_MISSING_DEPENDENCIES = _detect_missing_dependencies()

pytestmark = pytest.mark.skipif(
    bool(_MISSING_DEPENDENCIES),
    reason=(
        "backlink_integrity_e2e.test.py pending dependencies: "
        + ", ".join(_MISSING_DEPENDENCIES)
    ),
)

# ---------------------------------------------------------------------------
# Generic self-anchoring module loader (mirrors every other test file's own
# convention, e.g. verse_footnote_corpus.py's _load_module).
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path, extra_sys_path: Path | None = None):
    if extra_sys_path is not None:
        sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if extra_sys_path is not None:
            sys.path.remove(str(extra_sys_path))


case_spec = _load_module("backlink_e2e_case_spec", FIXTURE_DIR / "case_spec.py")

# ---------------------------------------------------------------------------
# Manifest/segpack/draft/ledger staging helpers -- copied from
# tests/assemble.test.py (see module docstring), extended with canon_map.
# ---------------------------------------------------------------------------

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


def write_manifest(root, blocks, segments, footnotes=None, verse_store=None, frontback=None):
    """`blocks`: dict[id -> block dict WITHOUT 'id'] (filled in here);
    mirrors tests/assemble.test.py's write_manifest exactly (heading_types
    unused by this fixture -- vh1's declared-heading case uses the
    built-in raw_type "HEAD", which always wins, no heading_types needed)."""
    for bid, b in blocks.items():
        b.setdefault("id", bid)
        b.setdefault("sha1", hashlib.sha1(bid.encode()).hexdigest())
        b.setdefault("source_file", "source.txt")
    manifest = {
        "blocks": blocks,
        "spine": [{"pos": 0, "file": "body.txt", "klass": "body"}],
        "segments": segments,
        "footnotes": footnotes or [],
        "frontback": frontback or [],
        "verse": {"store": verse_store or []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {
            "source_extraction_hash": "backlink-e2e-x",
            "source_input_hash": "backlink-e2e-y",
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def write_segpack(root, seg, blocks, footnotes=None, verses=None, canon_map=None):
    pack = {
        "seg": seg,
        "title": seg,
        "kind": "body",
        "word_count": 10,
        "blocks": blocks,
        "footnotes": footnotes or [],
        "verses": verses or [],
        "names": [],
        "canon_names": [],
        "new_names": [],
        # segpack.schema.json:132 requires canon_map (always present,
        # possibly {}) -- assemble.py never reads it, so an empty dict is
        # harmless-but-schema-complete (codex R4 minor 2).
        "canon_map": canon_map if canon_map is not None else {},
        "generation_hashes": {
            "source_extraction_hash": "backlink-e2e-x",
            "source_input_hash": "backlink-e2e-y",
            "particle_config_hash": "backlink-e2e-x",
            "derivation_bundle_hash": "backlink-e2e-y",
        },
    }
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )
    return pack


def draft_content_sha1_of(doc: dict) -> str:
    """1.2.0 canonical draft-content hash (drop dispatch_token, sha1 the
    sorted-key canonical re-serialization) -- copied from
    tests/assemble.test.py's own identical helper."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_draft(root, seg, blocks, footnotes=None, verses=None, names=None, notes=None) -> bytes:
    draft = {
        "seg": seg,
        "blocks": blocks,
        "footnotes": footnotes or {},
        "verses": verses or {},
        "names": names or [],
        "notes": notes or [],
    }
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(draft_bytes)
    return draft_bytes


def write_ledger(root, converged_segs) -> None:
    segments = {}
    for seg in converged_segs:
        draft_path = root / "segments" / f"{seg}.draft.json"
        draft_doc = json.loads(draft_path.read_text(encoding="utf-8"))
        segments[seg] = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "converged",
            "rounds": 1,
            "cache_key": DUMMY_CACHE_KEY,
            "n_blocks": 1,
            "n_footnotes": 0,
            "n_verses": 0,
            "reviewed_draft_sha1": draft_content_sha1_of(draft_doc),
        }
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Fixture staging: one full durable_root per (tmp_path, mentions_enabled).
# ---------------------------------------------------------------------------


def stage_fixture(tmp_path: Path, mentions_enabled: bool, label: str) -> Path:
    root = tmp_path / f"durable_root_{label}"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in _REQUIRED_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)

    # canon_senses.py/profile_validate.py/validate_extraction.py's own
    # loaders self-anchor their schema lookups to `${their_own_root}/
    # schemas/` (Path(__file__).resolve().parents[1] / "schemas") -- for a
    # SCRIPT COPY running out of this staged root, that means `root/
    # schemas/`, never the real plugin install's own assets/schemas/.
    # Mirrors Step 0a copying the whole scripts/+schemas/ set together
    # (see occ_index.py's own module docstring).
    shutil.copytree(SCHEMAS_DIR, root / "schemas")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    shutil.copy2(
        FIXTURE_DIR / "languages" / "backlink_e2e.json",
        languages_dir / "backlink_e2e.json",
    )

    shutil.copy2(FIXTURE_DIR / "canon.json", root / "canon.json")
    shutil.copy2(FIXTURE_DIR / "canon_senses.json", root / "canon_senses.json")

    profile = yaml.safe_load((FIXTURE_DIR / "profile.yml").read_text(encoding="utf-8"))
    profile["project"]["durable_root"] = str(root)
    profile["source"]["path"] = str(FIXTURE_DIR / "source_stub.txt")
    profile["output"]["destination"] = str(root / "out")
    profile["output"]["adapter_config"]["obsidian"]["mentions_section"]["enabled"] = mentions_enabled
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    write_manifest(
        root,
        blocks={k: dict(v) for k, v in case_spec.MANIFEST_BLOCKS.items()},
        segments=case_spec.MANIFEST_SEGMENTS,
        footnotes=case_spec.MANIFEST_FOOTNOTES,
        verse_store=case_spec.MANIFEST_VERSE_STORE,
        frontback=case_spec.MANIFEST_FRONTBACK,
    )

    (root / "segments").mkdir()
    (root / "runs").mkdir()
    for seg, pack_kwargs in case_spec.SEGPACKS.items():
        write_segpack(root, seg, **pack_kwargs)
    for seg, draft_kwargs in case_spec.DRAFTS.items():
        write_draft(root, seg, **draft_kwargs)
    write_ledger(root, case_spec.LEDGER_CONVERGED_SEGS)

    return root


def run_script(root: Path, script_name: str, extra_args=None, timeout: int = 60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / script_name), *(extra_args or [])],
        capture_output=True, text=True, timeout=timeout,
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


def run_flag_pipeline(tmp_path: Path, mentions_enabled: bool, label: str):
    """Stages the fixture and runs the REAL assemble.py subprocess (which,
    in-process, resolves+calls render_obsidian.render() via
    dispatch_adapter -- contract section 10). Returns (root, proc,
    nodestream). Asserts assemble exits 0 -- every test using this helper
    needs a successful assemble as its starting point; a failure here
    means the fixture itself (or the pipeline under test) is broken, not
    that some LATER assertion should quietly continue."""
    root = stage_fixture(tmp_path, mentions_enabled, label)
    proc = run_script(root, "assemble.py")
    assert proc.returncode == 0, (
        f"assemble.py (mentions_enabled={mentions_enabled}) failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    nodestream = read_nodestream(root)
    return root, proc, nodestream


# ---------------------------------------------------------------------------
# occurrence_targets.build() driven fresh, in a SEPARATE PROCESS -- never
# in-process via importlib. Every shipped script here is self-anchored via
# a module-level `DURABLE_ROOT = Path(__file__).resolve().parents[1]`
# constant computed ONCE at first import; since this test file loads
# byte-identical script copies from MULTIPLE distinct staged roots across
# several test functions in the SAME pytest process, an in-process
# importlib load risks a stale `sys.modules["occ_index"]` (etc.) entry
# from an EARLIER root's copy being silently reused for a LATER root's
# data (Python caches bare `import occ_index` by module NAME, not by
# source path) -- which would read the wrong DURABLE_ROOT-anchored files.
# A fresh subprocess sidesteps this entirely, exactly like every other
# script invocation in this file.
# ---------------------------------------------------------------------------

_BUILD_AGGREGATE_DRIVER = """
import json
import sys
from pathlib import Path

scripts_dir = Path(sys.argv[1])
root = Path(sys.argv[2])
sys.path.insert(0, str(scripts_dir))
import bootstrap_names
import canon_senses
import occurrence_targets
import yaml

manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
canon_path = root / "canon.json"
canon = json.loads(canon_path.read_text(encoding="utf-8")) if canon_path.is_file() else {"entries": {}}
senses_result = canon_senses.load_senses(root / "canon_senses.json", allow_absent=True)
profile = yaml.safe_load((root / "profile.yml").read_text(encoding="utf-8"))
particle_config = profile["source"]["language"]["particle_config"]
language_config = bootstrap_names.load_language_config(particle_config)
nodestream = json.loads((root / "out" / ".assembled" / "nodestream.json").read_text(encoding="utf-8"))

aggregate = occurrence_targets.build(manifest, canon, senses_result, language_config, nodestream)
print(json.dumps(aggregate))
"""


def build_aggregate_fresh(root: Path, tmp_path: Path) -> dict:
    driver_path = tmp_path / f"_e2e_build_driver_{root.name}.py"
    driver_path.write_text(_BUILD_AGGREGATE_DRIVER, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver_path), str(root / "scripts"), str(root)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"occurrence_targets.build() driver failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def eligible_pairs(eligible_by_source_form: dict) -> set:
    return {
        (sf, r["seg"])
        for sf, records in (eligible_by_source_form or {}).items()
        for r in records
    }


# ---------------------------------------------------------------------------
# validate_backlinks.parse_mentions_region -- loaded fresh, in-process, ONCE
# per call site is fine here (unlike occurrence_targets.build above):
# parse_mentions_region()'s own call chain (_strip_frontmatter ->
# render_obsidian._split_lf_lines -> _mentions_region_lines ->
# _wikilink_targets) is a PURE string transformation that never touches any
# DURABLE_ROOT-anchored module-level constant, so even a stale cross-root
# sys.modules entry for one of its sibling imports cannot make it read the
# wrong project's files -- there are no files involved at all.
# ---------------------------------------------------------------------------

_validate_backlinks_for_parsing = _load_module(
    "backlink_e2e_validate_backlinks_pure_parse", SCRIPTS_SRC_DIR / "validate_backlinks.py"
)


def vault_mentions_pairs(out_dir: Path) -> set:
    """Every (source_form, seg) pair a REAL rendered vault's entity notes'
    marked `## Mentions` regions resolve to -- built by reading each
    top-level segment note's OWN `seg:` frontmatter field (never
    reimplementing render()'s title/slug algorithm) to map a wikilink
    identity back to its seg, then parsing every entity note's Mentions
    region via `validate_backlinks.parse_mentions_region` (the SAME parser
    the real gate uses -- dogfooding it here is a stronger guarantee than
    an independent reimplementation: it proves the gate's own view agrees
    with the other two, not merely that some THIRD parser does)."""
    identity_to_seg = {}
    entity_notes = []
    for md_path in out_dir.rglob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        fm = _parse_frontmatter_dict(text)
        if fm is None:
            continue
        if "seg" in fm and "source_form" not in fm:
            identity = md_path.relative_to(out_dir).as_posix()[: -len(".md")]
            identity_to_seg[identity] = fm["seg"]
        elif "source_form" in fm:
            entity_notes.append((fm["source_form"], text))

    pairs = set()
    for source_form, text in entity_notes:
        parsed = _validate_backlinks_for_parsing.parse_mentions_region(text)
        if not parsed:
            continue
        for identity in parsed:
            seg = identity_to_seg.get(identity)
            if seg is not None:
                pairs.add((source_form, seg))
    return pairs


def _parse_frontmatter_dict(text: str):
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            try:
                doc = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            return doc if isinstance(doc, dict) else None
    return None


# ===========================================================================
# 1. Fixture self-check: every committed/staged input validates against the
#    REAL validators, not a bare JSON parse (contract: "Fixture inputs are
#    validated in-test against the real validators").
# ===========================================================================


def test_fixture_static_files_are_schema_valid():
    canon_validate_mod = _load_module("backlink_e2e_canon_validate", SCRIPTS_SRC_DIR / "canon_validate.py")
    canon_doc = json.loads((FIXTURE_DIR / "canon.json").read_text(encoding="utf-8"))
    registry = canon_validate_mod._build_schema_registry()
    canon_validate_mod._validate_whole_file(canon_doc, registry)  # raises CanonValidationError on failure

    canon_senses_mod = _load_module("backlink_e2e_canon_senses", SCRIPTS_SRC_DIR / "canon_senses.py")
    result = canon_senses_mod.load_senses(FIXTURE_DIR / "canon_senses.json", allow_absent=False)
    assert not result.is_empty
    assert canon_senses_mod.is_split(result, "Marek"), "Marek must be a genuine >=2-sense split in the fixture"

    lang_config_doc = json.loads((FIXTURE_DIR / "languages" / "backlink_e2e.json").read_text(encoding="utf-8"))
    assert set(lang_config_doc.keys()) <= {"PARTICLES", "STOPWORDS", "has_elision", "ELISION_RE", "name_inventory"}
    assert lang_config_doc["has_elision"] is False


def test_staged_manifest_and_segpacks_are_schema_valid(tmp_path):
    root = stage_fixture(tmp_path, mentions_enabled=True, label="schema_check")

    validate_extraction_mod = _load_module(
        "backlink_e2e_validate_extraction", SCRIPTS_SRC_DIR / "validate_extraction.py"
    )
    validate_extraction_mod._dependency_preflight()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    errors = validate_extraction_mod.validate_manifest_schema(manifest)
    assert errors == [], f"staged manifest.json failed manifest.schema.json validation:\n" + "\n".join(errors)

    import jsonschema

    segpack_schema = json.loads((SCHEMAS_DIR / "segpack.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(segpack_schema)
    for seg in case_spec.SEGPACKS:
        pack = json.loads((root / "segments" / f"segpack_{seg}.json").read_text(encoding="utf-8"))
        pack_errors = sorted(validator.iter_errors(pack), key=lambda e: list(e.path))
        assert pack_errors == [], (
            f"staged segpack_{seg}.json failed segpack.schema.json validation:\n"
            + "\n".join(f"{'.'.join(str(p) for p in e.path)}: {e.message}" for e in pack_errors)
        )


def test_staged_profile_is_schema_valid(tmp_path, monkeypatch):
    root = stage_fixture(tmp_path, mentions_enabled=True, label="profile_check")
    # profile_validate.py is a Step-0 workflow script, not part of the
    # assemble/render/gate pipeline _REQUIRED_SCRIPTS stages -- copied
    # ad hoc here, the one test that needs it.
    shutil.copy2(SCRIPTS_SRC_DIR / "profile_validate.py", root / "scripts" / "profile_validate.py")
    monkeypatch.setenv("LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT", "1")
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "profile_validate.py"), "--profile", str(root / "profile.yml")],
        capture_output=True, text=True, timeout=30,
        env={**__import__("os").environ, "LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT": "1"},
    )
    assert proc.returncode == 0, (
        f"staged profile.yml failed profile_validate.py:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


# ===========================================================================
# 2. Flag-ON end to end: real vault vs. the committed golden expected_vault/,
#    plus the gate's own clean report.
# ===========================================================================


def test_flag_on_end_to_end_matches_expected_vault_and_gate_report(tmp_path):
    root, _proc, nodestream = run_flag_pipeline(tmp_path, mentions_enabled=True, label="flag_on")
    out_dir = root / "out"

    diff_mod = _load_module("backlink_e2e_diff_rendered_output", SCRIPTS_SRC_DIR / "diff_rendered_output.py")
    expected_dir = FIXTURE_DIR / "expected_vault"
    failures = diff_mod.compare(diff_mod.reduce_vault(expected_dir), diff_mod.reduce_vault(out_dir))
    assert failures == [], (
        f"rendered vault differs from the committed expected_vault/ fixture "
        f"({len(failures)} line mismatch(es)):\n" + "\n".join(failures[:20])
    )

    # nodestream["mentions"] carries exactly the expected (source_form, seg)
    # universe -- the persisted-artifact view of the three-way parity.
    assert "mentions" in nodestream, "assemble.py must attach nodestream['mentions'] when the flag is on"
    assert eligible_pairs(nodestream["mentions"]) == case_spec.EXPECTED_ELIGIBLE_PAIRS

    # The gate itself: clean report (no warnings), collision + unresolved
    # diagnostics present but exit-neutral.
    gate_proc = run_script(root, "validate_backlinks.py")
    report = parse_one_json_line(gate_proc)
    assert gate_proc.returncode == 0, (
        f"validate_backlinks.py must exit 0 on a fully-covered fixture:\n{json.dumps(report, indent=1)}"
    )
    assert report["mentions_coverage"]["status"] == "enabled"
    assert report["mentions_coverage"]["missing"] == [], report["mentions_coverage"]["missing"]
    assert report["mentions_coverage"]["checked_entities"] == len(
        {sf for sf, _seg in case_spec.EXPECTED_ELIGIBLE_PAIRS}
    )
    assert report["warnings"] == 0

    assert report["collisions"] == [
        {
            "canonical_target_form": case_spec.EXPECTED_COLLISION_TARGET,
            "owners": case_spec.EXPECTED_COLLISION_OWNERS,
            "renderer_delinked": case_spec.EXPECTED_COLLISION_DELINKED,
            # C2 (#207 gate): both Petro and Pavlo are FULLY covered by
            # their own Mentions entries in this fixture (asserted just
            # above -- mentions_coverage.missing == []), so neither owner
            # of the "Peter" collision is orphaned.
            "orphaned_owners": [],
        }
    ]
    assert report["unresolved_homonyms"] == [
        {
            "source_form": case_spec.EXPECTED_SPLIT_SOURCE_FORM,
            "count": 1,
            "segs": case_spec.EXPECTED_SPLIT_SEGS,
        }
    ]
    # Exit-neutral: a collision + an unresolved homonym are both present,
    # yet warnings stayed exactly 0 (codex R3 b2 -- collisions/unresolved
    # never count toward warnings/exit).

    # Ineligible-but-tracked carriers must render NOWHERE inside the
    # NARRATIVE (segment notes) -- not "no Mentions entry", genuinely
    # absent from the story text. Scoped to segment notes only (root-level
    # files): each of these source_forms' OWN entity note legitimately
    # contains its own name in frontmatter/heading (every canon entry gets
    # a note regardless of Mentions eligibility) -- that is not a phantom
    # mention, so entity notes (People/, Other/) are excluded from this scan.
    segment_notes_text = "\n".join(
        p.read_text(encoding="utf-8") for p in out_dir.glob("*.md")
    )
    for source_form in case_spec.EXPECTED_INELIGIBLE_SOURCE_FORMS:
        assert source_form not in segment_notes_text, (
            f"{source_form!r} (an ineligible carrier: omit/regenerate/footnote-embedded-verse/"
            f"skip-mode-heading-verse) leaked into a rendered segment note -- phantom mention/render"
        )

    # #207-a collision de-linking: flag-on means "Peter" is entirely removed
    # from the inline auto-link map -- neither Petro nor Pavlo's note gets
    # an inline wikilink from p2's "Peter ... Peter ..." text. D3 gates on
    # `target == "obsidian"` alone (not the Mentions flag), so the SAME
    # holds flag-off -- see `test_flag_off_pipeline_preserves_1_7_0_
    # behavior_except_collisions` below (post-C1: neither state ever wraps
    # "Peter" anymore; pre-C1, flag-off used to wrap the leading occurrence
    # via the shortest-source_form tiebreak). Anchored on the TAIL of the
    # sentence rather than the leading "Peter" purely for note-lookup
    # stability (`_find_note_containing` needs one substring unique to one
    # note, valid across both this test and the flag-off one).
    seg01_note = _find_note_containing(out_dir, "spoke quietly to Peter before they parted ways.")
    assert "[[People/Pavlo|Peter]]" not in seg01_note
    assert "[[People/Petro|Peter]]" not in seg01_note
    assert "Peter spoke quietly to Peter before they parted ways." in seg01_note

    # sense_translated ("Lucky"): eligible for Mentions (asserted above via
    # nodestream/expected_vault) but NEVER inline-auto-linked.
    seg02_note = _find_note_containing(out_dir, "Lucky proved his own name")
    assert "[[People/Lucky|Lucky]]" not in seg02_note
    assert "Lucky proved his own name true once more." in seg02_note

    # #206 divergent target: Aldric's ONE literal-substring occurrence (p1)
    # gets the old inline link; the other (p4, rendered "The old man") does
    # not -- proving the #206 gap exists independently of the new flag, and
    # that the Mentions section (asserted above) is what actually closes it.
    p1_note = _find_note_containing(out_dir, "came back to the manor and sat by the fire")
    assert "[[People/Aldric|Aldric]]" in p1_note
    p4_note = _find_note_containing(out_dir, "The old man gazed at the same stars")
    assert "Aldric" not in p4_note


def _find_note_containing(out_dir: Path, needle: str) -> str:
    hits = [p for p in out_dir.rglob("*.md") if needle in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, f"expected exactly one rendered note containing {needle!r}, found {len(hits)}: {hits}"
    return hits[0].read_text(encoding="utf-8")


# ===========================================================================
# 3. Three-view parity (contract: fresh build() aggregate == persisted
#    nodestream["mentions"] == parsed rendered "## Mentions" regions).
# ===========================================================================


def test_flag_on_three_view_parity(tmp_path):
    root, _proc, nodestream = run_flag_pipeline(tmp_path, mentions_enabled=True, label="parity")
    out_dir = root / "out"

    fresh_aggregate = build_aggregate_fresh(root, tmp_path)
    view_fresh_build = eligible_pairs(fresh_aggregate["eligible_by_source_form"])
    view_persisted_nodestream = eligible_pairs(nodestream["mentions"])
    view_rendered_vault = vault_mentions_pairs(out_dir)

    assert view_fresh_build == case_spec.EXPECTED_ELIGIBLE_PAIRS
    assert view_persisted_nodestream == case_spec.EXPECTED_ELIGIBLE_PAIRS
    assert view_rendered_vault == case_spec.EXPECTED_ELIGIBLE_PAIRS, (
        f"parsed-rendered view diverges from the expected universe -- "
        f"missing={case_spec.EXPECTED_ELIGIBLE_PAIRS - view_rendered_vault} "
        f"extra={view_rendered_vault - case_spec.EXPECTED_ELIGIBLE_PAIRS}"
    )

    # not_a_name / is_proper_name:false -> NONE of the three views.
    not_a_name = case_spec.EXPECTED_NOT_A_NAME_SOURCE_FORM
    assert not_a_name not in fresh_aggregate["eligible_by_source_form"]
    assert not_a_name not in nodestream["mentions"]
    assert not any(sf == not_a_name for sf, _seg in view_rendered_vault)

    # sense_translated -> ALL three views (already covered by the equality
    # checks above since it's a member of EXPECTED_ELIGIBLE_PAIRS; asserted
    # again explicitly here for a self-documenting, named failure).
    sense_translated = case_spec.EXPECTED_SENSE_TRANSLATED_SOURCE_FORM
    assert sense_translated in fresh_aggregate["eligible_by_source_form"]
    assert sense_translated in nodestream["mentions"]
    assert any(sf == sense_translated for sf, _seg in view_rendered_vault)

    # Ineligible-but-canon-tracked carriers -> absent from all three too.
    for source_form in case_spec.EXPECTED_INELIGIBLE_SOURCE_FORMS:
        assert source_form not in fresh_aggregate["eligible_by_source_form"]
        assert source_form not in nodestream["mentions"]
        assert not any(sf == source_form for sf, _seg in view_rendered_vault)

    # Split form -> unresolved_homonyms in the fresh aggregate, absent from
    # eligible_by_source_form/nodestream/rendered Mentions alike.
    split_form = case_spec.EXPECTED_SPLIT_SOURCE_FORM
    assert split_form in fresh_aggregate["unresolved_homonyms"]
    assert split_form not in fresh_aggregate["eligible_by_source_form"]
    assert split_form not in nodestream["mentions"]
    assert not any(sf == split_form for sf, _seg in view_rendered_vault)


# ===========================================================================
# 4. Gate spoof-resistance: a deleted Mentions entry -> metric-1 WARN.
# ===========================================================================


def test_gate_deleted_mentions_entry_triggers_warning(tmp_path):
    root, _proc, _nodestream = run_flag_pipeline(tmp_path, mentions_enabled=True, label="deleted_entry")
    mutated_vault = tmp_path / "mutated_vault_deleted_entry"
    shutil.copytree(root / "out", mutated_vault)

    corentin_note = mutated_vault / "People" / "Corentin.md"
    assert corentin_note.is_file()
    text = corentin_note.read_text(encoding="utf-8")
    assert "- [[002 Chapter One]]" in text, f"expected fixture assumption broken -- Corentin's note:\n{text}"
    mutated_text = text.replace("- [[002 Chapter One]]\n", "")
    assert mutated_text != text
    corentin_note.write_text(mutated_text, encoding="utf-8")

    gate_proc = run_script(root, "validate_backlinks.py", extra_args=["--vault", str(mutated_vault)])
    report = parse_one_json_line(gate_proc)
    assert gate_proc.returncode == 1, f"deleted Mentions entry must trip the advisory exit-1 gate:\n{report}"
    assert report["warnings"] >= 1
    assert {"source_form": "Corentin", "seg": "seg01"} in report["mentions_coverage"]["missing"]


# ===========================================================================
# 5. Gate spoof-resistance: a fake marker pair smuggled into an unrestricted
#    frontmatter scalar (category) does NOT satisfy coverage -- the gate
#    strips the whole leading YAML frontmatter block before ever searching
#    for marker lines.
# ===========================================================================


def test_gate_frontmatter_scalar_injection_does_not_satisfy_coverage(tmp_path):
    root, _proc, _nodestream = run_flag_pipeline(tmp_path, mentions_enabled=True, label="frontmatter_spoof")
    mutated_vault = tmp_path / "mutated_vault_frontmatter_spoof"
    shutil.copytree(root / "out", mutated_vault)

    ysolde_note = mutated_vault / "People" / "Ysolde.md"
    assert ysolde_note.is_file()
    original = ysolde_note.read_text(encoding="utf-8")
    assert "<!-- lt:mentions:begin -->" in original, f"expected fixture assumption broken:\n{original}"

    # The REAL Mentions region deleted; a complete FAKE begin/end pair +
    # link smuggled inside the `category:` frontmatter scalar instead.
    # _strip_frontmatter removes this whole block (everything from the
    # first "---" line through the matching closing "---" line) before any
    # marker search runs, so the injected pair must never be seen.
    spoofed = (
        "---\n"
        "aliases:\n"
        "- Ysolde\n"
        "source_form: Ysolde\n"
        "canonical_target_form: Ysolde\n"
        "category: |-\n"
        "  person\n"
        "  <!-- lt:mentions:begin -->\n"
        "\n"
        "  ## Mentions\n"
        "\n"
        "  - [[002 Chapter One]]\n"
        "  <!-- lt:mentions:end -->\n"
        "is_proper_name: true\n"
        "basis: transliterated\n"
        "confidence: medium\n"
        "note: ''\n"
        "direction: ltr\n"
        "---\n"
        "\n"
        "# Ysolde\n"
    )
    ysolde_note.write_text(spoofed, encoding="utf-8")

    gate_proc = run_script(root, "validate_backlinks.py", extra_args=["--vault", str(mutated_vault)])
    report = parse_one_json_line(gate_proc)
    assert gate_proc.returncode == 1, (
        f"a frontmatter-scalar-smuggled fake Mentions region must NOT satisfy coverage:\n{report}"
    )
    assert {"source_form": "Ysolde", "seg": "seg01"} in report["mentions_coverage"]["missing"]


# ===========================================================================
# 6. Flag-OFF: 1.7.0 behavior EXCEPT homonym collisions (#206/#207
#    close-out, C1): no Mentions section anywhere, nothing new attached --
#    but collision de-linking is now gated on `target == "obsidian"` alone,
#    DE-COUPLED from the Mentions-appendix flag -- so the pre-1.10.0
#    shortest-source_form misattribution tiebreak no longer fires on ANY
#    obsidian render, appendix on or off (this fixture only ever renders
#    `target: "obsidian"`; the tiebreak survives, unexercised here, on a
#    non-obsidian `target: "custom"` render, where D3 stays inert). A
#    misattributed inline link actively misleads (reader clicks -> wrong
#    entity's page); that is strictly worse than a missing one (recoverable
#    via the appendix or a manual search), so the renderer de-links a
#    collision on every obsidian render regardless of the flag -- see
#    render_obsidian.py's render()/build_entity_index and this file's own
#    docstring on `orphaned_owners`.
# ===========================================================================


def test_flag_off_pipeline_preserves_1_7_0_behavior_except_collisions(tmp_path):
    root, _proc, nodestream = run_flag_pipeline(tmp_path, mentions_enabled=False, label="flag_off")
    out_dir = root / "out"

    assert "mentions" not in nodestream, "flag-off must never attach nodestream['mentions']"

    all_text = "\n".join(p.read_text(encoding="utf-8") for p in out_dir.rglob("*.md"))
    assert "lt:mentions:" not in all_text, "flag-off render must emit no Mentions markers/section anywhere"
    assert "## Mentions" not in all_text

    # C1 (#207 code fix): collision de-linking now gates on `target ==
    # "obsidian"` alone (decoupled from the Mentions flag) -- the
    # pre-1.10.0 shortest-then-lexicographic tiebreak ("Pavlo" wins, both
    # 5 chars, lexicographic) no longer fires on this (obsidian) render
    # even with the appendix off. NEITHER owner gets an inline link for
    # the shared "Peter" target -- verified (this was RED against
    # unmodified render_obsidian.py, which still misattributed it to
    # "Pavlo"; GREEN once C1 landed).
    seg01_note = _find_note_containing(out_dir, "spoke quietly to Peter before they parted ways.")
    assert "[[People/Pavlo|Peter]]" not in seg01_note
    assert "[[People/Petro|Peter]]" not in seg01_note

    # sense_translated stays unlinked (unchanged 1.7.0 behavior, independent
    # of the new flag).
    seg02_note = _find_note_containing(out_dir, "Lucky proved his own name")
    assert "[[People/Lucky|Lucky]]" not in seg02_note

    gate_proc = run_script(root, "validate_backlinks.py")
    report = parse_one_json_line(gate_proc)
    assert gate_proc.returncode == 0
    assert report == {
        "mentions_coverage": {"status": "disabled", "checked_entities": 0, "missing": []},
        "unresolved_homonyms": [],
        "collisions": [],
        "inline_advisory": {"thin_coverage": []},
        "warnings": 0,
    }
