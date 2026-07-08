"""tests/seg_safety_segpack.test.py -- regression-lock suite for the
segment-id path/shell-injection guard in scripts/segpack.py, and for the
matching `seg` pattern documented in manifest.schema.json/segpack.schema.json.

segpack.py splices a caller-supplied segment id into a filesystem path
(`segments/segpack_{seg}.json`, both via its CLI positional `seg` and via
every manifest.json `segments[].seg` value it iterates under `--all`) and,
downstream, into workflow shell commands. A malicious id containing `../`,
an absolute path, or shell metacharacters could escape durable_root or
inject into the pipeline. segpack.py now guards this three ways, matching
the identical contract already shipped in cache_key.py/ledger_update.py/
validate_draft.py/draft_ready.py/draft_sha1.py/review_artifact_check.py
(see tests/seg_safety_argparse.test.py for those):

  1. `validate_seg()` (`(FRONTBACK:)?[A-Za-z0-9_]+` via `re.fullmatch`) is
     called on the CLI's positional `seg` immediately after argument
     parsing, before any manifest/canon lookup or path is built.
  2. `main()`'s `--all` loop calls `validate_seg(seg_id)` on every id pulled
     from manifest.json `segments[]`, immediately on loop entry -- BEFORE
     `build_pack()`/the `out_path` write -- so the guarantee is LOCAL to
     the loop, not merely inherited from validate_segpack() downstream.
  3. `validate_segpack()` -- segpack.py's own hand-rolled, dependency-free
     structural self-check that runs on every assembled pack (single-seg OR
     `--all`, and regardless of where the `seg` value originated) -- ALSO
     rejects a well-formed-but-unsafe `seg` value, a second, independent
     backstop: even if guard 2 were ever removed, `out_path` is only ever
     built downstream of a clean `validate_segpack()` result.

This file does NOT re-test manifest.schema.json's cross-reference
invariant (that is manifest_validation.test.py's job) or segpack.py's
happy-path assembly behavior (that is a separate, dedicated concern) -- it
targets only the seg-id safety contract, per this suite's existing
seg_safety_argparse.test.py precedent.

Also asserts the schema `pattern` strings that DOCUMENT this same contract
in manifest.schema.json (`segments[].seg` and `frontback[].id`) and
segpack.schema.json (`seg`) actually encode it -- guarding against pattern
drift, since nothing runs `jsonschema` against these files at runtime (see
segpack.py's own module docstring: no jsonschema import, hand-rolled check
only) -- so a schema-pattern typo could otherwise sit undetected forever.

Collection note: like every ``*.test.py`` file in this suite, run via
`python3 -m pytest --import-mode=importlib` (already configured project-
wide via `pytest.ini`).
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"
MANIFEST_SCHEMA_PATH = ASSETS_DIR / "schemas" / "manifest.schema.json"
SEGPACK_SCHEMA_PATH = ASSETS_DIR / "schemas" / "segpack.schema.json"

assert SEGPACK_SCRIPT.is_file(), f"segpack.py not found at {SEGPACK_SCRIPT}"
assert BOOTSTRAP_NAMES_SCRIPT.is_file(), f"bootstrap_names.py not found at {BOOTSTRAP_NAMES_SCRIPT}"
assert MANIFEST_SCHEMA_PATH.is_file(), f"manifest.schema.json not found at {MANIFEST_SCHEMA_PATH}"
assert SEGPACK_SCHEMA_PATH.is_file(), f"segpack.schema.json not found at {SEGPACK_SCHEMA_PATH}"


# The shared accept/reject vocabulary every validate_seg() in this plugin
# must agree on -- mirrors tests/seg_safety_argparse.test.py's own list
# exactly, so a divergence between segpack.py and its siblings is caught.
VALID_SEGS = (
    "seg01",
    "seg0",
    "seg001",
    "segA",
    "segC",
    "segAnchor",
    "seg_alpha",
    "seg01_reusable",
    "seg05_blocked_regen",
    "FRONTBACK:fm01",
    "FRONTBACK:cover",
    "FRONTBACK:back_omit",
    "FRONTBACK:never_dispatched",
)

INVALID_SEGS = (
    "../../etc/passwd",
    "/etc/passwd",
    "seg/../x",
    "seg;rm -rf x",
    "seg 01",
    "FRONTBACK:../x",
    "",
    "seg01\n",  # the re.fullmatch-vs-trailing-newline trap
    "seg\\x",
    "seg|x",
    "seg&x",
    "seg$x",
    "seg`x`",
    "seg(x)",
    "seg<x>",
    "seg*x",
    "seg?x",
    "seg~x",
    "seg#x",
    "seg'x'",
    'seg"x"',
    "seg.x",
    "..",
)

# Non-empty malicious payloads only -- excludes "" from CLI-integration
# assertions below. segpack.py's positional `seg` uses `nargs="?"` (to
# distinguish a single-SEG run from `--all`), so an explicitly-passed empty
# string is still assigned to args.seg (argparse: nargs="?" with a value
# present, even "", is NOT "absent") but trips parse_args()'s own
# pre-existing "either SEG or --all is required" `parser.error()` BEFORE
# validate_seg() ever runs -- still a non-zero exit with a fatal message,
# just not the "segment id" wording validate_seg() itself produces. That
# pre-existing argparse guard is not this file's concern; the unit-test
# coverage below still locks down that validate_seg("") itself rejects.
MALICIOUS_SEGS = tuple(seg for seg in INVALID_SEGS if seg != "")


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Imports a script fresh from its real shipped path via importlib.
    Unlike final_audit.py (which self-anchors its own sys.path.insert off
    its own __file__), segpack.py's `from bootstrap_names import ...` is a
    plain top-level import that only resolves via Python's IMPLICIT
    sys.path[0] == script's own directory behavior when run as `python3
    segpack.py` -- which does not happen for an in-process importlib load.
    `extra_sys_path` (segpack.py's own directory) is inserted/removed around
    the load to reproduce that same resolution for this in-process test."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK_MODULE = _load_module("segpack_seg_safety_under_test", SEGPACK_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# 1. Direct unit tests -- validate_seg() itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seg", VALID_SEGS)
def test_validate_seg_accepts_valid(seg):
    assert SEGPACK_MODULE.validate_seg(seg) is None


@pytest.mark.parametrize("seg", INVALID_SEGS)
def test_validate_seg_rejects_invalid(seg):
    assert SEGPACK_MODULE.validate_seg(seg) is not None, f"expected {seg!r} to be rejected"


def test_trailing_newline_trap_is_specifically_a_fullmatch_case():
    """Names the trap directly: a naive `re.match(pattern + "$", seg)` would
    wrongly accept "seg01\\n" because "$" also matches just before a
    trailing newline. segpack.py's validate_seg() must reject it."""
    assert SEGPACK_MODULE.validate_seg("seg01\n") is not None


# ---------------------------------------------------------------------------
# 2. validate_segpack(): a well-formed-except-for-`seg` pack must be
#    rejected via a seg-specific error, isolated from every other field.
# ---------------------------------------------------------------------------


def _well_formed_pack(seg: str) -> dict:
    """A pack dict satisfying every OTHER validate_segpack() rule, so a
    failure can only be attributed to `seg` itself."""
    return {
        "seg": seg,
        "title": "Chapter One",
        "kind": "body",
        "word_count": 4,
        "blocks": [],
        "footnotes": [],
        "verses": [],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def test_validate_segpack_accepts_well_formed_baseline():
    """Regression-lock companion: the fixture itself is innocent before any
    perturbation below is asserted to fail."""
    errors = SEGPACK_MODULE.validate_segpack(_well_formed_pack("seg01"))
    assert errors == [], f"well-formed baseline must validate clean; got: {errors}"


@pytest.mark.parametrize("seg", MALICIOUS_SEGS)
def test_validate_segpack_rejects_unsafe_seg(seg):
    """A pack['seg'] that is a non-empty string (so it clears the pre-
    existing 'must be a non-empty string' check) but FAILS the seg-id
    allowlist must still be flagged -- this is what closes the `--all`-mode
    hole: `out_path` is only ever built downstream of a clean
    validate_segpack() result, so a malicious id sourced from manifest.json
    itself (not just the CLI) can never reach the filesystem write."""
    errors = SEGPACK_MODULE.validate_segpack(_well_formed_pack(seg))
    assert errors, f"expected pack['seg']={seg!r} to be rejected"
    assert any("safe segment id" in e for e in errors), errors


# ---------------------------------------------------------------------------
# 3. Integration test -- real segpack.py CLI subprocess, malicious
#    positional `seg`, before any manifest/canon file is ever touched.
# ---------------------------------------------------------------------------


def make_segpack_root(tmp_path) -> Path:
    """Minimal fixture: segpack.py's self-anchoring (`Path(__file__).
    resolve().parents[1]`) only needs the script + its bootstrap_names.py
    sibling copied to {root}/scripts/ -- validate_seg() rejects a malicious
    positional `seg` immediately after argparse, before manifest.json/
    canon.json/languages/ are ever read, so no further fixture content is
    required (mirrors seg_safety_argparse.test.py's identical make_*_root()
    reasoning for cache_key.py/ledger_update.py)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SEGPACK_SCRIPT, scripts_dir / "segpack.py")
    shutil.copy2(BOOTSTRAP_NAMES_SCRIPT, scripts_dir / "bootstrap_names.py")
    return root


@pytest.mark.parametrize("seg", MALICIOUS_SEGS)
def test_segpack_cli_rejects_malicious_seg(tmp_path, seg):
    root = make_segpack_root(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "segpack.py"),
            seg,
            "--particle-config", "fr.json",
            "--apparatus-policy", "translate_all",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected a malicious seg {seg!r} to be rejected, got rc=0\n"
        f"stdout:\n{result.stdout}"
    )
    assert "FATAL" in result.stderr and "segment id" in result.stderr, (
        f"expected segpack.py's fatal exit to name the segment-id problem "
        f"on stderr for seg {seg!r}, got stderr:\n{result.stderr}"
    )
    # Never wrote anything under a segments/ dir for the rejected seg.
    assert not (root / "segments").exists(), (
        f"segpack.py must reject seg {seg!r} before ever creating segments/"
    )


def test_segpack_cli_empty_seg_still_fatals_via_preexisting_argparse_guard(tmp_path):
    """The "" case excluded from MALICIOUS_SEGS above: still a non-zero
    exit (segpack.py's own pre-existing 'either SEG or --all is required'
    parser.error()), just not through validate_seg() -- locks down that
    this pre-existing guard has not silently regressed either."""
    root = make_segpack_root(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "segpack.py"),
            "",
            "--particle-config", "fr.json",
            "--apparatus-policy", "translate_all",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not (root / "segments").exists()


# ---------------------------------------------------------------------------
# 4. Schema regex drift guard -- manifest.schema.json's segments[].seg /
#    frontback[].id patterns, and segpack.schema.json's seg pattern, must
#    themselves encode the identical accept/reject vocabulary as
#    validate_seg() (via re.fullmatch, the exact matching mode
#    jsonschema's own "pattern" keyword uses -- jsonschema's `pattern` is a
#    SEARCH by spec, but both patterns below are anchored with ^...$, which
#    makes search and fullmatch equivalent here).
# ---------------------------------------------------------------------------


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_schema_segments_seg_pattern_matches_validate_seg_vocabulary():
    schema = _load_schema(MANIFEST_SCHEMA_PATH)
    pattern = schema["properties"]["segments"]["items"]["properties"]["seg"]["pattern"]
    compiled = re.compile(pattern)

    for seg in VALID_SEGS:
        assert compiled.fullmatch(seg), f"manifest.schema.json segments[].seg pattern rejects valid id {seg!r}"
    for seg in INVALID_SEGS:
        assert not compiled.fullmatch(seg), f"manifest.schema.json segments[].seg pattern accepts unsafe id {seg!r}"


def test_manifest_schema_frontback_id_pattern_matches_validate_seg_vocabulary():
    schema = _load_schema(MANIFEST_SCHEMA_PATH)
    pattern = schema["properties"]["frontback"]["items"]["properties"]["id"]["pattern"]
    compiled = re.compile(pattern)

    frontback_valid = tuple(seg for seg in VALID_SEGS if seg.startswith("FRONTBACK:"))
    assert frontback_valid, "expected at least one FRONTBACK:* id in VALID_SEGS"
    for seg in frontback_valid:
        assert compiled.fullmatch(seg), f"manifest.schema.json frontback[].id pattern rejects valid id {seg!r}"

    frontback_invalid = tuple(seg for seg in INVALID_SEGS if seg.startswith("FRONTBACK:")) + (
        "FRONTBACK:",  # the old "^FRONTBACK:.+$" hole this pattern must now close
        "FRONTBACK:/etc/passwd",
        "FRONTBACK:a/../b",
    )
    for seg in frontback_invalid:
        assert not compiled.fullmatch(seg), f"manifest.schema.json frontback[].id pattern accepts unsafe id {seg!r}"


def test_segpack_schema_seg_pattern_matches_validate_seg_vocabulary():
    schema = _load_schema(SEGPACK_SCHEMA_PATH)
    pattern = schema["properties"]["seg"]["pattern"]
    compiled = re.compile(pattern)

    for seg in VALID_SEGS:
        assert compiled.fullmatch(seg), f"segpack.schema.json seg pattern rejects valid id {seg!r}"
    for seg in INVALID_SEGS:
        assert not compiled.fullmatch(seg), f"segpack.schema.json seg pattern accepts unsafe id {seg!r}"


def test_all_three_seg_patterns_are_textually_identical():
    """The two `(?:FRONTBACK:)?[A-Za-z0-9_]+`-shaped patterns (manifest.json
    segments[].seg and segpack.schema.json's own seg) are restatements of
    the exact same contract in two files -- lock them byte-identical (minus
    the ^...$ anchors segpack.schema.json/manifest.schema.json both apply)
    so a future edit to one can't silently drift from the other."""
    manifest_schema = _load_schema(MANIFEST_SCHEMA_PATH)
    segpack_schema = _load_schema(SEGPACK_SCHEMA_PATH)

    manifest_seg_pattern = manifest_schema["properties"]["segments"]["items"]["properties"]["seg"]["pattern"]
    segpack_seg_pattern = segpack_schema["properties"]["seg"]["pattern"]

    assert manifest_seg_pattern == segpack_seg_pattern == "^(?:FRONTBACK:)?[A-Za-z0-9_]+$"


# ---------------------------------------------------------------------------
# 5. Integration test -- `--all` mode rejects a POISONED manifest.json seg id
#    before ever writing. Complements section 3 above (which targets the
#    CLI's own positional `seg`): here the untrusted value comes from
#    manifest.json `segments[].seg` itself (as an untrusted/compromised
#    custom extractor could emit), reaching main()'s `for seg_id in
#    seg_ids:` loop under `--all`. Proves guard 2 from the module docstring
#    end-to-end: real subprocess, real manifest.json on disk, real
#    segments/ directory checked for what did (not) get written.
# ---------------------------------------------------------------------------

FR_PARTICLE_CONFIG = ASSETS_DIR / "languages" / "fr.json"
assert FR_PARTICLE_CONFIG.is_file(), f"fr.json not found at {FR_PARTICLE_CONFIG}"


def make_all_mode_root(tmp_path, unsafe_seg: str) -> Path:
    """A minimal-but-complete durable_root for `segpack.py --all` whose
    manifest.json `segments[]` contains EXACTLY ONE entry, carrying
    `unsafe_seg` -- proving the loop-level guard fires even when the
    poisoned manifest is the SOLE source of segments, with no innocent
    segment for a future reorder-bug to hide behind. canon.json and the
    real `languages/fr.json` (copied verbatim, never hand-rolled -- so
    load_language_config()'s own PARTICLES/STOPWORDS/ELISION_RE/has_elision
    validation always succeeds) are present only because parse_args()/
    main() read that far before ever reaching the loop; validate_seg()
    rejects `unsafe_seg` before build_pack(), so neither file's CONTENT
    (beyond being loadable) is ever actually used."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SEGPACK_SCRIPT, scripts_dir / "segpack.py")
    shutil.copy2(BOOTSTRAP_NAMES_SCRIPT, scripts_dir / "bootstrap_names.py")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    shutil.copy2(FR_PARTICLE_CONFIG, languages_dir / "fr.json")

    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": unsafe_seg}]}), encoding="utf-8"
    )
    (root / "canon.json").write_text(json.dumps({}), encoding="utf-8")

    return root


def _run_segpack_all(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "segpack.py"),
            "--all",
            "--particle-config", "fr.json",
            "--apparatus-policy", "omit_apparatus",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("seg", MALICIOUS_SEGS)
def test_segpack_all_mode_rejects_poisoned_manifest_seg(tmp_path, seg):
    """The manifest's ONLY segments[] entry is unsafe -- the run must fail
    and no segpack_*.json may ever be written, across the full malicious
    vocabulary (path traversal, absolute paths, shell metacharacters, the
    trailing-newline fullmatch trap)."""
    root = make_all_mode_root(tmp_path, seg)
    result = _run_segpack_all(root)

    assert result.returncode != 0, (
        f"expected --all to reject poisoned manifest seg {seg!r}, got rc=0\n"
        f"stdout:\n{result.stdout}"
    )
    assert "segment id" in result.stderr, (
        f"expected the loop-level validate_seg() rejection to name the "
        f"segment-id problem on stderr for poisoned seg {seg!r}, got "
        f"stderr:\n{result.stderr}"
    )
    segments_dir = root / "segments"
    written = list(segments_dir.glob("segpack_*.json")) if segments_dir.exists() else []
    assert written == [], (
        f"--all must never write a segpack_*.json for poisoned seg {seg!r}; "
        f"found: {written}"
    )


def test_segpack_all_mode_single_unsafe_segment_manifest_fails_cleanly(tmp_path):
    """Explicit worked example, naming a concrete path-traversal payload:
    manifest.json's segments[] contains exactly ONE entry, and it is unsafe
    ("../../evil") -- the run must fail and never write, with no innocent
    segment for a broken guard to fall back on and mask the regression."""
    root = make_all_mode_root(tmp_path, "../../evil")
    result = _run_segpack_all(root)

    assert result.returncode != 0
    assert "segment id" in result.stderr
    assert list((root / "segments").glob("segpack_*.json")) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
