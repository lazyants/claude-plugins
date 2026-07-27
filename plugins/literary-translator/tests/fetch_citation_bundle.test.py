"""tests/fetch_citation_bundle.test.py -- #347's cache-bundle registration of
fetch_citation.py.

fetch_citation.py is the validated retrieval boundary for W3's pre-merge
citation audit: under `glossary.research_mode: live`, every byte the judging
agent ever sees about a cited `source` URI arrives through its scheme
allowlist, its resolve-and-check-EVERY-address rule, and its manual per-hop
redirect re-validation. Its bytes therefore shape what gets frozen into
canon.json, which is the bar `cache_key.py`'s `PLUGIN_BUNDLE_MEMBERS` comment
sets for membership.

THE FALSE-GREEN THIS CLOSES. `plugin_bundle_hash` is a literal filename
allowlist -- it hashes ONLY the listed files' bytes. Before this fix
fetch_citation.py was unlisted, so WEAKENING the security boundary (deleting a
check, widening the scheme allowlist) moved no hash at all. A durable root
scaffolded before that edit would have gone on classifying its converged
segments as reusable against a plugin that no longer behaved the same way --
exactly the staleness `plugin_bundle_hash` exists to detect.

That is why the mutation tests below are the point of this file and the
membership assertion is only its precondition: "the tuple grew by one" is a
statement about a literal, while "editing this file's CONTENT now moves the
hash, and would not have before" is the property actually being bought. Both
directions are pinned -- `test_bundle_hash_ignores_fetch_citation_when_unlisted`
keeps the pre-fix RED permanently reproducible, so this file could not go green
by accident on a build where the registration had been reverted.

Two independent methods are used deliberately, since they fail differently:

  * the DOCUMENTED FORMULA, re-derived here from
    references/ledger-and-resumability.md's prose (sha1 over sorted-by-filename
    concatenated member bytes) and never imported from any shipped script --
    same convention as tests/canon_senses_bundle.test.py and
    tests/ledger_composite_key.test.py;
  * the REAL shipped scaffold_setup.py, run end to end as a subprocess. That
    is the production writer of `runs/.plugin_bundle_hash`, and it imports the
    plugin's own cache_key.PLUGIN_BUNDLE_MEMBERS -- so it exercises the actual
    Step 0a path rather than this file's reading of it.

A re-derivation alone would share a blind spot with its target (both would be
working from this file's own idea of the scheme); the subprocess run is what
makes the claim about the pipeline.
"""
import ast
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_DIR / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
FETCH_CITATION_SRC = SCRIPTS_SRC_DIR / "fetch_citation.py"
SCAFFOLD_SETUP_SRC = SCRIPTS_SRC_DIR / "scaffold_setup.py"
SKILL_MD = SKILL_DIR / "SKILL.md"

assert CACHE_KEY_SRC.is_file(), f"cache_key.py not found at {CACHE_KEY_SRC}"
assert FETCH_CITATION_SRC.is_file(), f"fetch_citation.py not found at {FETCH_CITATION_SRC}"
assert SCAFFOLD_SETUP_SRC.is_file(), f"scaffold_setup.py not found at {SCAFFOLD_SETUP_SRC}"
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"

MEMBER = "fetch_citation.py"


def _load_cache_key_module():
    """In-process load of the REAL cache_key.py, purely to read its own
    PLUGIN_BUNDLE_MEMBERS tuple -- never to call its compute functions (every
    hash in this file is re-derived independently or produced by the real
    scaffold_setup.py subprocess)."""
    spec = importlib.util.spec_from_file_location("cache_key_fetch_citation_test", CACHE_KEY_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CACHE_KEY_MODULE = _load_cache_key_module()
PLUGIN_BUNDLE_MEMBERS = CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS


def _plugin_bundle_members_from_source():
    """The `PLUGIN_BUNDLE_MEMBERS` tuple as it is LITERALLY WRITTEN in
    cache_key.py, parsed from source with `ast` rather than read off the
    imported module.

    Both readings are compared below. The import alone would accept a tuple
    assembled at import time (a glob, a filter, a concatenation), which is a
    different contract from the literal byte-allowlist this bundle is
    documented to be -- and a dynamically-built list could start silently
    including or dropping members as the scripts/ directory changes."""
    tree = ast.parse(CACHE_KEY_SRC.read_text(encoding="utf-8"), filename=str(CACHE_KEY_SRC))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PLUGIN_BUNDLE_MEMBERS":
                assert isinstance(node.value, ast.Tuple), (
                    "PLUGIN_BUNDLE_MEMBERS must stay a literal tuple of filenames -- "
                    f"found {type(node.value).__name__}"
                )
                names = []
                for elt in node.value.elts:
                    assert isinstance(elt, ast.Constant) and isinstance(elt.value, str), (
                        "every PLUGIN_BUNDLE_MEMBERS entry must be a literal string, "
                        f"found {ast.dump(elt)}"
                    )
                    names.append(elt.value)
                return tuple(names)
    raise AssertionError(f"no `PLUGIN_BUNDLE_MEMBERS = (...)` assignment found in {CACHE_KEY_SRC}")


# ---------------------------------------------------------------------------
# The documented plugin_bundle_hash formula, re-derived independently.
# ---------------------------------------------------------------------------


def _documented_bundle_hash(durable_root: Path, members) -> str:
    """references/ledger-and-resumability.md's own formula: sha1 over the
    sorted-by-FILENAME, concatenated raw bytes of each member read from
    `${durable_root}/scripts/<name>`. Written out here rather than imported so
    a change to cache_key.py's helpers cannot move this yardstick with it."""
    ordered = sorted(members)
    blob = b"".join((durable_root / "scripts" / name).read_bytes() for name in ordered)
    return hashlib.sha1(blob).hexdigest()


def _make_durable_root(tmp_path) -> Path:
    """A durable_root fixture whose scripts/ mirrors Step 0a's real copy pass:
    every shipped assets/scripts/*.py except the five plugin-path-only ones,
    plus the workflow templates (which land FLAT under scripts/, since the
    durable tree has no scripts/templates/ subdir).

    Kept self-contained rather than shared with the other bundle suites, per
    this plugin's fixture convention.

    The staging is deliberately built from the shipped directory rather than
    from PLUGIN_BUNDLE_MEMBERS: a fixture populated FROM the list under test
    would contain a member iff the list named it, so the "editing
    fetch_citation.py moves the hash" assertion could pass on an empty read.
    """
    plugin_path_only = {
        "profile_validate.py",
        "validate_extraction.py",
        "glossary_preflight.py",
        "resolve_codex_companion.py",
        "scaffold_setup.py",
    }
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)

    staged = 0
    for src in sorted(SCRIPTS_SRC_DIR.glob("*.py")):
        if src.name in plugin_path_only:
            continue
        shutil.copy2(src, scripts_dir / src.name)
        staged += 1
    for src in sorted(TEMPLATES_SRC_DIR.glob("*.template.js")):
        shutil.copy2(src, scripts_dir / src.name)
        staged += 1

    # Silent-zero guard: a glob that matched nothing stages a tree that looks
    # exactly like a correctly staged one right up until every hash below
    # agrees trivially.
    assert staged >= 30, f"implausibly few files staged ({staged}) -- glob or path is wrong"

    # The load-bearing precondition for everything below. A fixture MISSING
    # this file is the precise false-green this suite is about: the mutation
    # would edit nothing and the hash would correctly not move.
    assert (scripts_dir / MEMBER).is_file(), (
        f"fixture is missing scripts/{MEMBER} -- every assertion in this file "
        "would be vacuous"
    )
    for name in PLUGIN_BUNDLE_MEMBERS:
        assert (scripts_dir / name).is_file(), (
            f"fixture is missing declared bundle member scripts/{name}; Step 0a's "
            "copy pass must place every member under scripts/"
        )
    return root


# ===========================================================================
# 1. Registration
# ===========================================================================


def test_fetch_citation_is_a_declared_plugin_bundle_member():
    """fetch_citation.py must be REGISTERED in cache_key.py's
    PLUGIN_BUNDLE_MEMBERS -- not merely present under assets/scripts/. The
    bundle is a literal filename allowlist, so shipping the file changes no
    hash by itself."""
    assert MEMBER in PLUGIN_BUNDLE_MEMBERS, (
        f"{MEMBER} is shipped under assets/scripts/ but is NOT a "
        "cache_key.py PLUGIN_BUNDLE_MEMBERS entry -- register it there so an "
        "edit to the citation-audit retrieval boundary flips "
        f"plugin_bundle_hash. Current members: {PLUGIN_BUNDLE_MEMBERS}"
    )


def test_declared_members_are_a_literal_allowlist_matching_the_import():
    """The tuple parsed from SOURCE must equal the tuple read off the imported
    module -- pinning the bundle as a hand-maintained literal allowlist rather
    than anything computed at import time."""
    from_source = _plugin_bundle_members_from_source()
    assert from_source == tuple(PLUGIN_BUNDLE_MEMBERS), (
        f"PLUGIN_BUNDLE_MEMBERS as written in source {from_source} != as imported "
        f"{tuple(PLUGIN_BUNDLE_MEMBERS)} -- the tuple is being built dynamically"
    )
    assert MEMBER in from_source
    assert len(from_source) == len(set(from_source)), (
        f"PLUGIN_BUNDLE_MEMBERS contains duplicates: {from_source}"
    )


def test_fetch_citation_is_not_in_the_derivation_bundle():
    """fetch_citation.py must not have been parked in DERIVATION_BUNDLE_MEMBERS
    instead: that bucket is bootstrap_names.py/segpack.py's source-derivation
    state, and landing there would route a stale segment to
    `blocked_needs_regeneration` rather than plain `stale`."""
    assert MEMBER not in CACHE_KEY_MODULE.DERIVATION_BUNDLE_MEMBERS, (
        f"{MEMBER} belongs in PLUGIN_BUNDLE_MEMBERS, not DERIVATION_BUNDLE_MEMBERS"
    )


def test_step_0a_copy_pass_does_not_exclude_fetch_citation():
    """SKILL.md's Step 0a copy pass carries an explicit exclusion list of
    plugin-path-only scripts. Naming fetch_citation.py there would make it a
    declared bundle member that never lands under `${durable_root}/scripts/`,
    and scaffold_setup.py would hard-fail reading it -- a bricked Step 0a, not
    a silent one. This guards that coupling from the SKILL.md side."""
    text = SKILL_MD.read_text(encoding="utf-8")
    # Collapse whitespace first: the anchors below are prose in a hard-wrapped
    # document, so a line-oriented search would miss a phrase that wrapped.
    flat = re.sub(r"\s+", " ", text)
    start = flat.find("every file in `assets/scripts/*.py` (except")
    assert start != -1, "could not locate Step 0a's `assets/scripts/*.py` copy-pass sentence in SKILL.md"
    end = flat.find("every shipped file in `assets/languages/`", start)
    assert end != -1, "could not locate the end of Step 0a's copy-pass sentence in SKILL.md"
    exclusion_span = flat[start:end]
    assert MEMBER not in exclusion_span, (
        f"{MEMBER} is a PLUGIN_BUNDLE_MEMBERS entry but SKILL.md's Step 0a copy "
        "pass excludes it from `${durable_root}/scripts/` -- scaffold_setup.py "
        f"would fail reading it. Exclusion span was:\n{exclusion_span}"
    )


# ===========================================================================
# 2. The property being bought -- both directions, both methods.
# ===========================================================================


def test_editing_fetch_citation_moves_the_documented_bundle_hash(tmp_path):
    """GREEN: with fetch_citation.py registered, a CONTENT edit to the
    retrieval boundary changes plugin_bundle_hash."""
    root = _make_durable_root(tmp_path)
    target = root / "scripts" / MEMBER

    before = _documented_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)
    original = target.read_bytes()
    # Stands in for a real weakening of the boundary (a dropped address check,
    # a widened scheme allowlist). A plain append is used deliberately over a
    # targeted search-and-replace: a replace whose needle had drifted would
    # silently mutate nothing, and a hash that then failed to move would read
    # as a real defect.
    target.write_bytes(original + b"\n# weakened boundary\n")
    assert target.read_bytes() != original, "the mutation did not actually change the file"
    after = _documented_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)

    assert before != after, (
        f"editing {MEMBER} left plugin_bundle_hash unchanged ({before}) -- the "
        "security boundary is not covered by the bundle"
    )

    target.write_bytes(original)
    assert _documented_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS) == before, (
        "restoring the original bytes did not restore the hash -- the formula "
        "is reading something other than file content"
    )


def test_bundle_hash_ignores_fetch_citation_when_unlisted(tmp_path):
    """RED, kept permanently reproducible: over the member list MINUS
    fetch_citation.py -- i.e. the pre-#347 bundle -- the very same content edit
    moves nothing.

    This is what makes the test above meaningful rather than tautological. It
    demonstrates that the hash's blindness was a property of the LIST, not of
    the fixture or the mutation, and it would fail loudly if someone "fixed" a
    red suite by reverting the registration."""
    root = _make_durable_root(tmp_path)
    target = root / "scripts" / MEMBER
    pre_fix_members = tuple(m for m in PLUGIN_BUNDLE_MEMBERS if m != MEMBER)
    assert len(pre_fix_members) == len(PLUGIN_BUNDLE_MEMBERS) - 1, (
        "expected exactly one fetch_citation.py entry to drop out"
    )

    before = _documented_bundle_hash(root, pre_fix_members)
    original = target.read_bytes()
    target.write_bytes(original + b"\n# weakened boundary\n")
    after = _documented_bundle_hash(root, pre_fix_members)
    target.write_bytes(original)

    assert before == after, (
        "the pre-#347 member list unexpectedly reacted to a fetch_citation.py "
        "edit -- this control assumes the bundle hashes only LISTED files, so "
        "the green test above may be passing for the wrong reason"
    )


def _stamp_via_real_scaffold_setup(root: Path) -> str:
    """Run the REAL shipped scaffold_setup.py exactly as Step 0a's final action
    does (from the PLUGIN path, so its `import cache_key` binds to the plugin's
    own copy) and return the marker it wrote."""
    proc = subprocess.run(
        [sys.executable, str(SCAFFOLD_SETUP_SRC), "--durable-root", str(root)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"scaffold_setup.py exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    marker = root / "runs" / ".plugin_bundle_hash"
    assert marker.is_file(), f"scaffold_setup.py wrote no marker at {marker}"
    return marker.read_text(encoding="utf-8").strip()


def test_real_scaffold_setup_marker_reacts_to_a_fetch_citation_edit(tmp_path):
    """End-to-end through the production writer, not a re-derivation: the
    `runs/.plugin_bundle_hash` value that cache_key.py actually reads back must
    move when fetch_citation.py's bytes move.

    Also cross-checks the two methods against each other -- if the real
    scaffold's marker and this file's independently re-derived formula ever
    disagree, the documented formula has drifted from the shipped one."""
    root = _make_durable_root(tmp_path)
    target = root / "scripts" / MEMBER

    before = _stamp_via_real_scaffold_setup(root)
    assert before == _documented_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS), (
        "scaffold_setup.py's marker disagrees with the documented "
        "sorted-concatenated-bytes formula -- one of them has drifted"
    )

    original = target.read_bytes()
    target.write_bytes(original + b"\n# weakened boundary\n")
    after = _stamp_via_real_scaffold_setup(root)
    target.write_bytes(original)

    assert before != after, (
        f"the real Step 0a marker did not move after editing {MEMBER} "
        f"({before}) -- editing the citation-audit retrieval boundary would "
        "leave every converged segment wrongly classified as reusable"
    )
    assert _stamp_via_real_scaffold_setup(root) == before, (
        "restoring fetch_citation.py's bytes did not restore the marker"
    )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
