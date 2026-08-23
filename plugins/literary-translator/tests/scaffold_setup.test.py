"""tests/scaffold_setup.test.py -- regression-lock suite for #194:
``scaffold_setup.py`` writes the two Step-0a bundle-hash marker files.

THE BUG (#194): ``cache_key.compute_plugin_bundle_hash`` (cache_key.py:503)
READS ``${durable_root}/runs/.plugin_bundle_hash`` and
``resume_setup.compute_input_digest`` (resume_setup.py:289-292) reads BOTH
``.plugin_bundle_hash`` and ``.orchestration_bundle_hash`` -- but before
#194 SKILL.md's Step 0a only PROSE-described "computes/writes" them: no
shipped script actually wrote either marker, so a real run failed with
"has Step 0a run for this project?". ``scaffold_setup.py`` is the shipped
writer that closes that gap; SKILL.md Step 0a now invokes it as its final
action. (``derivation_bundle_hash`` is computed live -- no marker -- so the
scaffold writes only these two.)

RED-before-GREEN: with ``scaffold_setup.py`` absent, ``run_scaffold_setup``
invokes a non-existent script -> non-zero exit -> every marker-writing test
below fails; the always-green ``test_readers_fail_without_markers``
separately proves the readers genuinely reject an un-scaffolded root (so the
"markers written -> readers succeed" tests below are not vacuous). Creating
``scaffold_setup.py`` turns the RED tests GREEN.

House style: every fixture copies the REAL shipped members into an isolated
``tmp_path`` durable_root (so ``cache_key.py``'s self-anchored
``Path(__file__).resolve().parents[1]`` resolves to the fixture root exactly
as production does) and invokes the real scripts via ``subprocess.run``. The
expected marker values are recomputed INDEPENDENTLY here (plain
``hashlib.sha1`` over the sorted-by-filename concatenated member bytes),
never by re-calling ``scaffold_setup.py``'s / ``cache_key.py``'s own hashing
helpers -- a genuine cross-check, not a reimplementation asserted against
itself.

NOTE on the drift guards deliberately avoided here: this file NEVER names a
canon_senses CONSUMER script (canon_validate.py / glossary_batch_plan.py /
canon_adjudication_audit.py) as a literal in its own code -- the plugin
members it stages are enumerated from ``cache_key.PLUGIN_BUNDLE_MEMBERS`` at
runtime, and the three files it mutates by literal name (validate_draft.py, a
plugin member only; draft_ready.py, an orchestration member only;
select_segments.py, a member of BOTH since #446) are all non-consumers -- so
tests/senses_fixture_guard.test.py's whole-file consumer scan correctly
skips it.
"""
import collections
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
TEMPLATES_DIR = ASSETS_DIR / "templates"

SCAFFOLD_SETUP_SRC = SCRIPTS_DIR / "scaffold_setup.py"
CACHE_KEY_SRC = SCRIPTS_DIR / "cache_key.py"
RESUME_SETUP_SRC = SCRIPTS_DIR / "resume_setup.py"

# cache_key.py + resume_setup.py must ship for this file's fixtures to mean
# anything (both are load-bearing); scaffold_setup.py is INTENTIONALLY not
# hard-asserted at collection so the file still collects (and the marker-
# writing tests below fail with a clear per-test message) while red-before-
# green, before scaffold_setup.py exists.
assert CACHE_KEY_SRC.is_file(), f"cache_key.py not found at {CACHE_KEY_SRC}"
assert RESUME_SETUP_SRC.is_file(), f"resume_setup.py not found at {RESUME_SETUP_SRC}"


def _load_module(name, path):
    """Load a shipped script as an importable module. Registers it in
    sys.modules under `name` so an intra-directory ``import cache_key`` inside
    scaffold_setup.py resolves to this same real module."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The real cache_key must be importable as the bare name "cache_key" so
# scaffold_setup.py's own top-level `import cache_key` (a plugin-path sibling
# import) resolves to it when we load scaffold_setup below.
cache_key = _load_module("cache_key", CACHE_KEY_SRC)
resume_setup = _load_module("resume_setup", RESUME_SETUP_SRC)

# The plugin-bundle members (the *.py scripts plus the *.template.js workflow
# templates) and the orchestration-bundle members -- named, never counted, so
# this comment cannot go stale the next time a member joins either tuple. The
# plugin set is the AUTHORITATIVE constant from cache_key.py itself
# (drift-catcher: if scaffold_setup.py ever hashes a different set, the
# independent-recompute assertions below diverge). The
# orchestration set is pinned here as plain data -- mirrored, never imported
# from scaffold_setup.py, so test_orchestration_members_pinned genuinely
# guards scaffold_setup.py's own local tuple.
PLUGIN_BUNDLE_MEMBERS = cache_key.PLUGIN_BUNDLE_MEMBERS
EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS = (
    # #438: registered in BOTH bundles -- it gates dispatch (plugin bundle)
    # AND select_segments.py imports it (transitive-import invisibility).
    "claim_record.py",
    "draft_ready.py",
    # #369: the THIRD double registration, same transitive-import reason --
    # ledger_merge.py and select_segments.py load it here, six plugin members
    # load it there.
    "json_stdout.py",
    "ledger_merge.py",
    "language_smoke_report.py",
    # #446: registered in BOTH bundles as well -- it owns the dispatch gate
    # itself, not merely the claim predicate.
    "select_segments.py",
)

# A plugin member that is NOT an orchestration member, an orchestration member
# that is NOT a plugin member, and a member of BOTH -- used by the mutation
# test to prove each marker reacts to its OWN bundle, and that a dual member
# moves both. All three are non-consumer scripts (see the module docstring's
# drift-guard note).
PLUGIN_ONLY_MEMBER = "validate_draft.py"
ORCHESTRATION_ONLY_MEMBER = "draft_ready.py"
DUAL_MEMBER = "select_segments.py"

MARKER_REL = {
    "plugin": ("runs", ".plugin_bundle_hash"),
    "orchestration": ("runs", ".orchestration_bundle_hash"),
}

# A minimal-but-valid profile.yml + ownership marker so the REAL
# `cache_key.py --field plugin_bundle_hash` CLI path (which loads the profile
# before reaching compute_plugin_bundle_hash) can run end to end against the
# fixture -- exactly the production invocation #194 stops failing.
OWNER_MARKER_NAME = ".literary-translator-root.json"
PROFILE_NAME = "profile.yml"


def _member_source(name):
    """Where a bundle member physically ships in the plugin: *.template.js
    under assets/templates/, everything else under assets/scripts/."""
    if name.endswith(".template.js"):
        return TEMPLATES_DIR / name
    return SCRIPTS_DIR / name


def _independent_bundle_hash(durable_root, members):
    """sha1 of the sorted-by-filename concatenated raw bytes of `members`
    under ${durable_root}/scripts/ -- an INDEPENDENT recompute (plain
    hashlib/sorted, no cache_key/scaffold helpers), the cross-check the
    written marker must equal."""
    paths = sorted((durable_root / "scripts" / name for name in members), key=lambda p: p.name)
    blob = b"".join(p.read_bytes() for p in paths)
    return hashlib.sha1(blob).hexdigest()


def _make_scaffold_root(tmp_path):
    """Isolated durable_root with scripts/ populated by the REAL shipped
    members (as Step 0a's copy pass would), plus a minimal ownership
    marker + profile.yml so the cache_key.py CLI round-trip resolves. runs/
    is left WITHOUT markers -- writing them is scaffold_setup.py's job."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in sorted(set(PLUGIN_BUNDLE_MEMBERS) | set(EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)):
        shutil.copy2(_member_source(name), scripts_dir / name)
    (root / "runs").mkdir(parents=True)

    profile_path = root / PROFILE_NAME
    # compute_plugin_bundle_hash reads NO profile field -- a minimal valid
    # mapping is all load_profile() needs to get past its isinstance(dict)
    # gate on the way to the marker read.
    profile_path.write_text("project: {}\n", encoding="utf-8")
    (root / OWNER_MARKER_NAME).write_text(
        json.dumps({"owner_profile_path": PROFILE_NAME}), encoding="utf-8"
    )
    return root


def run_scaffold_setup(durable_root, timeout=60):
    """Invoke the REAL scaffold_setup.py from its plugin path (never a
    durable copy), passing the fixture durable_root -- exactly how SKILL.md
    Step 0a invokes it."""
    return subprocess.run(
        [sys.executable, str(SCAFFOLD_SETUP_SRC), "--durable-root", str(durable_root)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _make_plugin_copy(tmp_path, name="plugin_copy"):
    """A COPY of the plugin's whole assets/ tree under tmp_path -- byte-
    identical to the shipped one, and the tree the --verify tests point at (and,
    where a test cares about writes, INVOKE) instead of mutating the real
    install. `__pycache__` is excluded deliberately: scaffold_setup.py's
    `import cache_key` would otherwise find a valid pre-existing .pyc and
    produce no observable mutation, so a no-write assertion could not tell
    `sys.dont_write_bytecode` from its absence."""
    root = tmp_path / name
    shutil.copytree(
        ASSETS_DIR, root / "assets", ignore=shutil.ignore_patterns("__pycache__")
    )
    return root


def run_scaffold_verify(durable_root, plugin_root=None, script=None, timeout=60):
    """Invoke --verify. `script` defaults to the shipped scaffold_setup.py;
    a test that snapshots for writes passes the COPY's own scaffold_setup.py,
    because the bytecode a sibling import leaves behind lands beside the
    EXECUTED file, not beside the one --plugin-root names."""
    cmd = [
        sys.executable,
        str(script if script is not None else SCAFFOLD_SETUP_SRC),
        "--verify",
        "--durable-root",
        str(durable_root),
    ]
    if plugin_root is not None:
        cmd += ["--plugin-root", str(plugin_root)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _tree_snapshot(root):
    """Every path under `root` -> (file type, mtime_ns, bytes-or-None). Uses
    lstat so a symlink is never followed, and records CONTENT as well as
    mtime so an in-place rewrite that preserves mtime is still caught."""
    snap = {}
    for path in sorted(Path(root).rglob("*")):
        st = path.lstat()
        content = None
        if stat.S_ISREG(st.st_mode):
            content = path.read_bytes()
        snap[str(path.relative_to(root))] = (stat.S_IFMT(st.st_mode), st.st_mtime_ns, content)
    return snap


def run_cache_key_field(durable_root, field, timeout=60):
    """Invoke the durable copy of cache_key.py (self-anchored to the fixture)
    for one global field -- the real production CLI path."""
    return subprocess.run(
        [sys.executable, str(durable_root / "scripts" / "cache_key.py"), "--field", field],
        capture_output=True,
        text=True,
        cwd=str(durable_root),
        timeout=timeout,
    )


def _read_marker(durable_root, which):
    return (durable_root.joinpath(*MARKER_REL[which])).read_text(encoding="utf-8")


# ===========================================================================
# Motivating invariant (always GREEN -- proves the readers reject an
# un-scaffolded root, so the "markers written" tests below are not vacuous).
# ===========================================================================


def test_readers_fail_without_markers(tmp_path):
    """Before scaffold_setup.py runs, both readers reject the missing
    plugin_bundle_hash marker: cache_key.py --field plugin_bundle_hash exits
    non-zero, and resume_setup._read_marker raises 'has Step 0a run'. This is
    the #194 failure the scaffold removes."""
    root = _make_scaffold_root(tmp_path)  # runs/ has NO markers

    proc = run_cache_key_field(root, "plugin_bundle_hash")
    assert proc.returncode != 0, (
        f"cache_key.py must reject a durable_root with no .plugin_bundle_hash "
        f"marker; got rc=0\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "plugin_bundle_hash marker" in proc.stderr, (
        f"expected the missing-marker error to name the marker; stderr={proc.stderr!r}"
    )

    with pytest.raises(resume_setup.ResumeSetupError) as exc:
        resume_setup._read_marker(root / "runs" / ".plugin_bundle_hash", "plugin_bundle_hash")
    assert "Step 0a" in str(exc.value), (
        f"resume_setup._read_marker should point at Step 0a; got {exc.value!r}"
    )


# ===========================================================================
# The RED->GREEN drivers: scaffold_setup.py writes correct, readable markers.
# ===========================================================================


def test_scaffold_writes_plugin_bundle_marker(tmp_path):
    """.plugin_bundle_hash is written non-empty, equals an independent sha1
    over cache_key.PLUGIN_BUNDLE_MEMBERS, and round-trips through BOTH readers
    (the real cache_key.py CLI and resume_setup._read_marker) with no failure."""
    root = _make_scaffold_root(tmp_path)
    proc = run_scaffold_setup(root)
    assert proc.returncode == 0, (
        f"scaffold_setup.py must succeed; rc={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )

    written = _read_marker(root, "plugin").strip()
    assert written, "the plugin_bundle_hash marker must be non-empty"
    expected = _independent_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)
    assert written == expected, (
        f"scaffold-written plugin_bundle_hash {written!r} != independent "
        f"recompute {expected!r} over cache_key.PLUGIN_BUNDLE_MEMBERS"
    )

    cli = run_cache_key_field(root, "plugin_bundle_hash")
    assert cli.returncode == 0, (
        f"cache_key.py --field plugin_bundle_hash must now succeed; "
        f"rc={cli.returncode}\nstdout={cli.stdout!r}\nstderr={cli.stderr!r}"
    )
    assert cli.stdout.strip() == written, (
        f"cache_key.py read back {cli.stdout.strip()!r}, marker holds {written!r}"
    )

    round_tripped = resume_setup._read_marker(
        root / "runs" / ".plugin_bundle_hash", "plugin_bundle_hash"
    )
    assert round_tripped == written, (
        f"resume_setup._read_marker read {round_tripped!r}, marker holds {written!r}"
    )


def test_scaffold_writes_orchestration_bundle_marker(tmp_path):
    """.orchestration_bundle_hash is written non-empty, equals an independent
    sha1 over the five orchestration members, and is read back by
    resume_setup._read_marker (its only reader -- it is NOT a cache_key
    field)."""
    root = _make_scaffold_root(tmp_path)
    proc = run_scaffold_setup(root)
    assert proc.returncode == 0, (
        f"scaffold_setup.py must succeed; rc={proc.returncode}\nstderr={proc.stderr!r}"
    )

    written = _read_marker(root, "orchestration").strip()
    assert written, "the orchestration_bundle_hash marker must be non-empty"
    expected = _independent_bundle_hash(root, EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)
    assert written == expected, (
        f"scaffold-written orchestration_bundle_hash {written!r} != independent "
        f"recompute {expected!r} over the five orchestration members"
    )

    round_tripped = resume_setup._read_marker(
        root / "runs" / ".orchestration_bundle_hash", "orchestration_bundle_hash"
    )
    assert round_tripped == written


def test_member_mutation_flips_right_marker(tmp_path):
    """Each marker reacts to its OWN bundle: mutating a plugin-only member
    flips plugin_bundle_hash and leaves orchestration_bundle_hash fixed;
    mutating an orchestration-only member flips orchestration_bundle_hash and
    leaves plugin_bundle_hash fixed; and mutating a DUAL member flips both.

    The third leg is #446's acceptance criterion, proven by running the
    SHIPPED scaffold_setup.py over a real scaffolded root rather than by
    recomputing either hash here. It is also the first coverage of the dual
    case at all -- claim_record.py has been registered in both bundles since
    #438 with nothing exercising that shape."""
    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0
    plugin_0 = _read_marker(root, "plugin").strip()
    orchestration_0 = _read_marker(root, "orchestration").strip()

    # Mutate a PLUGIN member (not an orchestration member).
    plugin_member_path = root / "scripts" / PLUGIN_ONLY_MEMBER
    plugin_member_path.write_bytes(plugin_member_path.read_bytes() + b"\n# scaffold-test mutation\n")
    assert run_scaffold_setup(root).returncode == 0
    plugin_1 = _read_marker(root, "plugin").strip()
    orchestration_1 = _read_marker(root, "orchestration").strip()
    assert plugin_1 != plugin_0, "mutating a plugin member must flip plugin_bundle_hash"
    assert orchestration_1 == orchestration_0, (
        "mutating a plugin member must NOT flip orchestration_bundle_hash"
    )

    # Mutate an ORCHESTRATION member (not a plugin member).
    orchestration_member_path = root / "scripts" / ORCHESTRATION_ONLY_MEMBER
    orchestration_member_path.write_bytes(
        orchestration_member_path.read_bytes() + b"\n# scaffold-test mutation\n"
    )
    assert run_scaffold_setup(root).returncode == 0
    plugin_2 = _read_marker(root, "plugin").strip()
    orchestration_2 = _read_marker(root, "orchestration").strip()
    assert orchestration_2 != orchestration_1, (
        "mutating an orchestration member must flip orchestration_bundle_hash"
    )
    assert plugin_2 == plugin_1, (
        "mutating an orchestration member must NOT flip plugin_bundle_hash"
    )

    # Mutate a member of BOTH bundles (#446).
    dual_member_path = root / "scripts" / DUAL_MEMBER
    dual_member_path.write_bytes(dual_member_path.read_bytes() + b"\n# scaffold-test mutation\n")
    assert run_scaffold_setup(root).returncode == 0
    plugin_3 = _read_marker(root, "plugin").strip()
    orchestration_3 = _read_marker(root, "orchestration").strip()
    assert plugin_3 != plugin_2, (
        f"{DUAL_MEMBER} is a PLUGIN_BUNDLE_MEMBERS entry, so mutating it must "
        "flip plugin_bundle_hash"
    )
    assert orchestration_3 != orchestration_2, (
        f"{DUAL_MEMBER} is an ORCHESTRATION_BUNDLE_MEMBERS entry, so mutating "
        "it must flip orchestration_bundle_hash too -- one edit, two hashes"
    )


def test_scaffold_member_set_matches_cache_key(tmp_path):
    """Drift-catcher: the plugin set scaffold_setup.py hashes must be exactly
    cache_key.PLUGIN_BUNDLE_MEMBERS. Proven by mutating a member cache_key
    declares (a *.template.js -- the pair most at risk of being silently
    dropped from the scaffold's set) and confirming the scaffold-written
    marker still tracks the independent recompute over the canonical set. If
    scaffold hashed a NARROWER set (e.g. the *.py members only), the marker would
    NOT move when a template's bytes change, and this assertion would fail."""
    template_members = [m for m in PLUGIN_BUNDLE_MEMBERS if m.endswith(".template.js")]
    assert template_members, (
        "sanity: cache_key.PLUGIN_BUNDLE_MEMBERS is expected to include the "
        "two *.template.js workflow templates"
    )

    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0
    before = _read_marker(root, "plugin").strip()
    assert before == _independent_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)

    template_path = root / "scripts" / template_members[0]
    template_path.write_bytes(template_path.read_bytes() + b"\n// scaffold-test mutation\n")
    assert run_scaffold_setup(root).returncode == 0
    after = _read_marker(root, "plugin").strip()
    assert after != before, (
        f"mutating a *.template.js bundle member ({template_members[0]}) must flip "
        "plugin_bundle_hash -- if it does not, scaffold_setup.py hashed a set "
        "NARROWER than cache_key.PLUGIN_BUNDLE_MEMBERS (the templates were dropped)"
    )
    assert after == _independent_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS), (
        "scaffold-written plugin_bundle_hash must still equal an independent "
        "recompute over the full cache_key.PLUGIN_BUNDLE_MEMBERS set"
    )


def test_orchestration_members_pinned():
    """scaffold_setup.py's locally-declared ORCHESTRATION_BUNDLE_MEMBERS must
    exactly equal the tuple resume_setup.py's resume-integrity digest
    depends on -- guards the local tuple against a silent desync (no shared
    constant exists to import)."""
    assert SCAFFOLD_SETUP_SRC.is_file(), (
        f"scaffold_setup.py not found at {SCAFFOLD_SETUP_SRC} -- #194 not implemented"
    )
    scaffold_setup = _load_module("scaffold_setup", SCAFFOLD_SETUP_SRC)
    assert scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS == EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS, (
        f"scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS "
        f"{scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS!r} != expected "
        f"{EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS!r}"
    )


# ===========================================================================
# codex review (HIGH, release-blocking): the managed-directory symlink
# surface. Before the fix, scripts_dir.is_dir() and the runs/ mkdir both
# FOLLOW a symlink -- a `${durable_root}/scripts -> /external` symlink lets
# compute_bundle_hash() hash bytes from OUTSIDE durable_root, and a
# `${durable_root}/runs -> /external` symlink lets atomic_write_text's
# mkdir(parents=True) + write land the markers OUTSIDE durable_root too. The
# predictable `.tmp.<pid>` temp name is itself a plantable symlink target
# even when runs/ is a genuine real directory. RED-before-GREEN: run against
# the unpatched script, all three tests below FAIL (the attack currently
# SUCCEEDS); the fix makes them fail-closed instead.
# ===========================================================================


def test_scripts_dir_symlink_refused(tmp_path):
    """A `${durable_root}/scripts -> /external` symlink must be refused
    fail-closed BEFORE any hashing happens -- the external dir's bytes must
    never be read/hashed, and neither marker gets written."""
    root = tmp_path / "durable_root"
    root.mkdir()
    external = tmp_path / "external_scripts"
    external.mkdir()
    for name in sorted(set(PLUGIN_BUNDLE_MEMBERS) | set(EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)):
        shutil.copy2(_member_source(name), external / name)
    # is_dir() FOLLOWS a symlink, so a symlinked scripts/ pointing at a real
    # directory still passes the pre-existing "does scripts/ exist" check --
    # exactly why that check alone does not close the vector.
    (root / "scripts").symlink_to(external, target_is_directory=True)
    (root / "runs").mkdir()

    proc = run_scaffold_setup(root)
    assert proc.returncode != 0, (
        f"scaffold_setup.py must refuse a symlinked scripts/ dir; "
        f"rc=0\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "scripts_dir_is_symlink" in proc.stderr, (
        f"expected the refusal to name the reason; stderr={proc.stderr!r}"
    )
    assert not (root / "runs" / ".plugin_bundle_hash").exists(), (
        "no marker should be written when scripts_dir is refused as a symlink"
    )
    assert not (root / "runs" / ".orchestration_bundle_hash").exists()


def test_runs_dir_symlink_refused(tmp_path):
    """A `${durable_root}/runs -> /external` symlink must be refused
    fail-closed BEFORE any marker write happens -- the external directory
    must never receive the marker files."""
    root = _make_scaffold_root(tmp_path)
    shutil.rmtree(root / "runs")
    external = tmp_path / "external_runs"
    external.mkdir()
    (root / "runs").symlink_to(external, target_is_directory=True)

    proc = run_scaffold_setup(root)
    assert proc.returncode != 0, (
        f"scaffold_setup.py must refuse a symlinked runs/ dir; "
        f"rc=0\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "runs_dir_is_symlink" in proc.stderr, (
        f"expected the refusal to name the reason; stderr={proc.stderr!r}"
    )
    assert list(external.iterdir()) == [], (
        "no marker should be written into the external symlink target"
    )


def _open_dir_fd(scaffold_setup, path):
    """Open `path` the same way main() pins runs_dir/scripts_dir --
    O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC -- for tests driving atomic_write_text
    directly with the dir-fd + leaf-name signature it now takes."""
    flags = (
        os.O_RDONLY
        | scaffold_setup._O_DIRECTORY
        | scaffold_setup._O_NOFOLLOW
        | scaffold_setup._O_CLOEXEC
    )
    return os.open(path, flags)


def test_temp_marker_symlink_is_not_followed(tmp_path, monkeypatch):
    """A symlink pre-planted AT THE EXACT temp-file leaf name is refused,
    never followed -- even when runs/ itself is a genuine real directory
    (so the scripts_dir/runs_dir guards above do not fire). The temp leaf
    is now UNGUESSABLE (secrets.token_hex(8)) rather than the old
    predictable `.tmp.<pid>`, so this test can no longer precompute the
    real future path by reading os.getpid() alone -- it pins
    secrets.token_hex's output via monkeypatch so the exact generated name
    is known ahead of time for planting, then proves the
    O_CREAT|O_EXCL|O_NOFOLLOW open still refuses a collision. This is
    defense in depth on top of unguessability itself: even in the
    (now vanishingly unlikely) case an attacker's guess lands exactly on
    the generated name, the write must still fail closed rather than
    follow the planted symlink."""
    scaffold_setup = _load_module("scaffold_setup_symlink_probe", SCAFFOLD_SETUP_SRC)
    root = tmp_path / "durable_root"
    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    external_target = tmp_path / "external_runs" / "planted.tmp"
    external_target.parent.mkdir(parents=True)
    external_target.write_text("do-not-touch", encoding="utf-8")

    fixed_suffix = "f" * 16  # secrets.token_hex(8) -> 16 hex chars
    monkeypatch.setattr(scaffold_setup.secrets, "token_hex", lambda n: "f" * (n * 2))

    marker_name = ".plugin_bundle_hash"
    tmp_name = f".{marker_name}.tmp.{os.getpid()}.{fixed_suffix}"
    planted_symlink = runs_dir / tmp_name
    planted_symlink.symlink_to(external_target)

    runs_fd = _open_dir_fd(scaffold_setup, runs_dir)
    try:
        with pytest.raises(SystemExit) as exc_info:
            scaffold_setup.atomic_write_text(runs_fd, marker_name, "deadbeef\n")
    finally:
        os.close(runs_fd)
    assert exc_info.value.code != 0, "atomic_write_text must fail closed, not succeed"

    assert external_target.read_text(encoding="utf-8") == "do-not-touch", (
        "atomic_write_text must not follow a pre-planted symlink at the "
        "generated temp path and write through it to an external target"
    )
    assert not (runs_dir / marker_name).exists(), (
        "the real marker must not be written when the temp-path guard fires"
    )


# ===========================================================================
# codex review round 2 (MEDIUM short-write, HIGH TOCTOU): a short os.write()
# and a mid-hash directory swap. Both tests below prove the RED half
# in-line (a reconstructed pre-fix code path, or an explicit monkeypatched
# swap) rather than by reverting scaffold_setup.py's real fix -- this file
# lives in a shared worktree, so git stash is off the table.
# ===========================================================================


def _write_ignoring_return_value(dir_fd, name, text):
    """Reference copy of atomic_write_text's PRE-FIX write body (MEDIUM-1:
    os.write()'s return value ignored). Used only to prove the flaky_write
    monkeypatch in test_atomic_write_text_survives_short_write actually
    reproduces a truncated write -- the RED half of RED-before-GREEN --
    without reverting the real fix in scaffold_setup.py."""
    tmp_name = f".{name}.buggy-probe.{os.getpid()}"
    fd = os.open(tmp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=dir_fd)
    try:
        os.write(fd, text.encode("utf-8"))  # bug: short write silently ignored
    finally:
        os.close(fd)
    os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def test_atomic_write_text_survives_short_write(tmp_path, monkeypatch):
    """RED-before-GREEN (MEDIUM-1): write(2) may legally write FEWER bytes
    than given without raising. Force every os.write() call in this test to
    accept at most one byte, proving first (RED, via
    _write_ignoring_return_value) that this really does truncate a naive
    single-shot write, then confirming (GREEN) that the real
    atomic_write_text loops until every byte lands."""
    scaffold_setup = _load_module("scaffold_setup_short_write_probe", SCAFFOLD_SETUP_SRC)
    root = tmp_path / "durable_root"
    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)

    real_os_write = os.write

    def flaky_write(fd, data):
        return real_os_write(fd, data[:1])  # never accept more than 1 byte

    monkeypatch.setattr(os, "write", flaky_write)

    text = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"  # 41 bytes
    assert len(text.encode("utf-8")) == 41

    runs_fd = _open_dir_fd(scaffold_setup, runs_dir)
    try:
        # RED: the pre-fix write body truncates under a short-writing os.write.
        _write_ignoring_return_value(runs_fd, "buggy_marker", text)
        buggy_written = (runs_dir / "buggy_marker").read_text(encoding="utf-8")
        assert buggy_written != text, (
            "sanity check failed: the flaky_write monkeypatch must reproduce "
            "a truncated write for the pre-fix code path, or this test proves "
            "nothing about the real fix below"
        )
        assert len(buggy_written) == 1, (
            f"expected the buggy path to write exactly 1 byte per os.write() "
            f"call and stop there; got {buggy_written!r}"
        )

        # GREEN: the real, fixed atomic_write_text writes the full text.
        scaffold_setup.atomic_write_text(runs_fd, "real_marker", text)
    finally:
        os.close(runs_fd)

    real_written = (runs_dir / "real_marker").read_text(encoding="utf-8")
    assert real_written == text, (
        f"atomic_write_text must write the FULL text even when os.write() "
        f"short-writes; got {real_written!r} (len={len(real_written)}), "
        f"expected {text!r} (len={len(text)})"
    )


def test_scripts_dir_swap_mid_hash_refused(tmp_path, monkeypatch, capsys):
    """RED-before-GREEN (HIGH-1, TOCTOU): scripts_dir's is_symlink() check
    only reflects the state at CHECK time. Monkeypatch compute_bundle_hash
    so its first call (the plugin-bundle hash) has the side effect of
    swapping scripts_dir for a DIFFERENT real directory -- same member
    filenames and bytes, different inode -- immediately after computing a
    (now stale) hash. main() must detect the swap via the before/after
    dir_identity_or_none check and fail closed with scripts_dir_swapped,
    writing neither marker, rather than silently publishing hashes computed
    across a mix of pre- and post-swap directory state."""
    scaffold_setup = _load_module("scaffold_setup_swap_probe", SCAFFOLD_SETUP_SRC)
    root = _make_scaffold_root(tmp_path)

    real_scripts_dir = root / "scripts"
    decoy_scripts_dir = tmp_path / "decoy_scripts"
    decoy_scripts_dir.mkdir()
    for name in sorted(set(PLUGIN_BUNDLE_MEMBERS) | set(EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)):
        shutil.copy2(_member_source(name), decoy_scripts_dir / name)

    real_compute_bundle_hash = scaffold_setup.compute_bundle_hash
    call_count = {"n": 0}

    def swapping_compute_bundle_hash(durable_root, members, what):
        call_count["n"] += 1
        result = real_compute_bundle_hash(durable_root, members, what)
        if call_count["n"] == 1:
            # Swap scripts/ for a different real directory BETWEEN the
            # before-hash and after-hash identity checks -- a different
            # inode is a swap regardless of byte content.
            real_scripts_dir.rename(root / "scripts_original")
            decoy_scripts_dir.rename(real_scripts_dir)
        return result

    monkeypatch.setattr(scaffold_setup, "compute_bundle_hash", swapping_compute_bundle_hash)

    with pytest.raises(SystemExit) as exc_info:
        scaffold_setup.main(["--durable-root", str(root)])
    assert exc_info.value.code != 0, "main() must fail closed when scripts_dir is swapped mid-hash"
    assert call_count["n"] == 2, (
        "sanity: both compute_bundle_hash calls (plugin + orchestration) "
        "must have run for the swap to have happened strictly BETWEEN the "
        "before/after identity checks"
    )

    captured = capsys.readouterr()
    assert "scripts_dir_swapped" in captured.err, (
        f"expected the refusal to name the reason; stderr={captured.err!r}"
    )
    assert not (root / "runs" / ".plugin_bundle_hash").exists(), (
        "no marker should be written when the mid-hash swap is detected"
    )
    assert not (root / "runs" / ".orchestration_bundle_hash").exists()


# ===========================================================================
# codex review round 3 (HIGH TOCTOU): the create->replace substitution
# window. Before this fix, atomic_write_text() wrote the temp file and
# replaced it into place with no check that the temp file was still the
# exact bytes/inode it had just written -- a substitution of the temp file
# (same name, different inode or size) between the write and the
# os.replace() would be published as if it were this call's own bytes. The
# fix adds an fsync + fstat(fd)-vs-stat(name) identity-and-size verify
# right before publish, failing closed on any mismatch.
# ===========================================================================


def test_atomic_write_fails_closed_on_temp_substitution(tmp_path, monkeypatch):
    """Before-replace verify: fail closed if the on-disk entry at the temp
    leaf name is no longer the exact inode fstat(fd) saw right after the
    write completed -- the signature of a substitution in the
    create->replace window. Monkeypatches os.fstat to report a DIFFERENT
    st_ino than the real temp file's on-disk inode (st_dev/st_size held
    fixed), simulating exactly that substitution without needing a real
    racing process to win the window."""
    scaffold_setup = _load_module("scaffold_setup_substitution_probe", SCAFFOLD_SETUP_SRC)
    root = tmp_path / "durable_root"
    root.mkdir()

    real_os_fstat = os.fstat
    FakeStat = collections.namedtuple("FakeStat", ["st_dev", "st_ino", "st_size"])

    def fake_fstat(fd):
        real = real_os_fstat(fd)
        return FakeStat(st_dev=real.st_dev, st_ino=real.st_ino + 1, st_size=real.st_size)

    monkeypatch.setattr(os, "fstat", fake_fstat)

    dir_fd = _open_dir_fd(scaffold_setup, root)
    try:
        with pytest.raises(SystemExit) as exc_info:
            scaffold_setup.atomic_write_text(dir_fd, "marker", "deadbeef\n")
    finally:
        os.close(dir_fd)
    assert exc_info.value.code != 0, "atomic_write_text must fail closed, not succeed"

    assert not (root / "marker").exists(), (
        "the target marker must not be created/updated when the "
        "before-replace inode verify fails"
    )


def test_atomic_write_text_happy_path(tmp_path):
    """Confirms the hardened atomic_write_text still does the ordinary
    job: writes the given text byte-for-byte (trailing newline preserved)
    with no monkeypatching involved, and leaves no leftover `.tmp.` file
    behind in the directory once the real write/verify/replace has run."""
    scaffold_setup = _load_module("scaffold_setup_happy_path_probe", SCAFFOLD_SETUP_SRC)
    root = tmp_path / "durable_root"
    root.mkdir()

    dir_fd = _open_dir_fd(scaffold_setup, root)
    try:
        scaffold_setup.atomic_write_text(dir_fd, "marker", "deadbeef\n")
    finally:
        os.close(dir_fd)

    written = (root / "marker").read_bytes()
    assert written == b"deadbeef\n", f"expected exact byte-for-byte content; got {written!r}"

    leftover = [p.name for p in root.iterdir() if ".tmp." in p.name]
    assert leftover == [], f"leftover temp file(s) after atomic_write_text: {leftover}"




# ===========================================================================
# #396 -- `--verify`: the markers are Step 0a evidence about code that is
# read LIVE from the plugin install on every later run, and nothing ever
# re-checked that the two still agree.
#
# RED-before-GREEN: with `--verify` absent from the parser, argparse exits 2
# ("unrecognized arguments: --verify") and every test below fails on the exit
# code or the reason it asserts. The one deliberate exception is
# test_write_mode_output_is_unchanged, a CHARACTERIZATION test that passes on
# the unfixed writer by design -- that is its whole point.
# ===========================================================================


def _drift_ready_pair(tmp_path):
    """A scaffolded durable_root plus a plugin-tree copy, with the markers
    freshly rewritten. Bundled because the third step is the subtle one: it is
    what holds leg 1 true, so that a test mutating the PLUGIN copy exercises
    leg 2 rather than tripping `marker_stale` before it gets there. A plain
    helper, not a fixture -- this file builds everything through explicit
    calls."""
    root = _make_scaffold_root(tmp_path)
    plugin_copy = _make_plugin_copy(tmp_path)
    _rewrite_markers(root)
    return root, plugin_copy


def _rewrite_markers(root):
    """Re-run the writer so the markers describe scripts/ AS IT IS NOW. What
    separates the two legs: without this, a durable mutation trips
    `marker_stale` and the live comparison is never reached at all."""
    result = run_scaffold_setup(root)
    assert result.returncode == 0, f"writer failed: {result.stderr}"
    assert _read_marker(root, "plugin").strip() == _independent_bundle_hash(
        root, PLUGIN_BUNDLE_MEMBERS
    ), "marker must agree with the durable copies before the live leg is exercised"


def test_verify_passes_on_a_freshly_scaffolded_root(tmp_path):
    """Both legs hold on a root the copy pass just populated from the real
    shipped members. Guards a false RED: every test below asserting a refusal
    is worthless if the check refuses a correct project too."""
    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0
    result = run_scaffold_verify(root)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "scaffold_setup: verified" in result.stdout
    assert f"plugin_bundle_hash={_independent_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)}" in result.stdout
    assert (
        f"orchestration_bundle_hash={_independent_bundle_hash(root, EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)}"
        in result.stdout
    )


def test_write_mode_output_is_unchanged(tmp_path):
    """Characterization pin: adding --verify must not disturb the write mode's
    exact stdout, which Step 0a's operator reads back. Deliberately GREEN
    before the fix."""
    root = _make_scaffold_root(tmp_path)
    result = run_scaffold_setup(root)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "scaffold_setup: wrote "
        f"plugin_bundle_hash={_independent_bundle_hash(root, PLUGIN_BUNDLE_MEMBERS)} "
        f"orchestration_bundle_hash={_independent_bundle_hash(root, EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS)}\n"
    )
    # stdout alone would not notice a change to what the mode actually
    # PRODUCES. Both markers, exact bytes, trailing newline included.
    for which, members in (
        ("plugin", PLUGIN_BUNDLE_MEMBERS),
        ("orchestration", EXPECTED_ORCHESTRATION_BUNDLE_MEMBERS),
    ):
        assert _read_marker(root, which) == _independent_bundle_hash(root, members) + "\n"


def test_verify_detects_a_stale_marker(tmp_path):
    """A durable copy edited in place without re-running the writer -- the
    #412 shape, where ${durable_root}/scripts/ is codex-writable. Reported as
    marker_stale, NOT as a plugin drift: the durable tree moved, not the
    install, and naming the wrong culprit would send the operator to the
    wrong tree."""
    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0
    (root / "scripts" / ORCHESTRATION_ONLY_MEMBER).write_bytes(
        (root / "scripts" / ORCHESTRATION_ONLY_MEMBER).read_bytes() + b"\n# tampered\n"
    )
    result = run_scaffold_verify(root)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=marker_stale" in result.stderr
    assert "orchestration_bundle_hash" in result.stderr


def test_verify_detects_live_plugin_drift(tmp_path):
    """THE #396 CASE. The marker agrees with the durable copies (leg 1 holds,
    asserted before verify runs), and the live tree has moved underneath --
    exactly what a mid-run `claude plugin update` produces. Only leg 2 can
    catch it, and today nothing does."""
    root, plugin_copy = _drift_ready_pair(tmp_path)
    live_template = plugin_copy / "assets" / "templates" / "mass-translate-wf.template.js"
    live_template.write_bytes(live_template.read_bytes() + b"\n// a later release\n")
    result = run_scaffold_verify(root, plugin_root=plugin_copy)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=live_plugin_drift" in result.stderr
    assert "mass-translate-wf.template.js" in result.stderr
    assert "reason=marker_stale" not in result.stderr


def test_verify_detects_orchestration_drift(tmp_path):
    """The second marker is really covered. draft_ready.py is an
    ORCHESTRATION member -- outside plugin_bundle_hash entirely -- yet
    codex_job.py resolves it from the live tree via --plugin-root, so
    .orchestration_bundle_hash is frozen evidence over live code in exactly
    the same way."""
    root, plugin_copy = _drift_ready_pair(tmp_path)
    live = plugin_copy / "assets" / "scripts" / "draft_ready.py"
    live.write_bytes(live.read_bytes() + b"\n# a later release\n")
    result = run_scaffold_verify(root, plugin_root=plugin_copy)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=live_plugin_drift" in result.stderr
    assert "draft_ready.py" in result.stderr


def test_verify_reports_every_drifted_member(tmp_path):
    """Every offender in ONE run, not the first -- and claim_record.py, one of
    the THREE members deliberately registered in BOTH tuples (select_segments.py
    since #446 and json_stdout.py since #369 are the others), named EXACTLY once.
    That single count is what pins the dedup: reporting it twice would present
    one drifted file as two."""
    root, plugin_copy = _drift_ready_pair(tmp_path)
    # One member from each tuple plus the one registered in BOTH: an
    # implementation that aggregated the plugin tuple but reported only the
    # first orchestration offender would pass a plugin-only pair.
    for name in ("claim_record.py", PLUGIN_ONLY_MEMBER, ORCHESTRATION_ONLY_MEMBER):
        live = plugin_copy / "assets" / "scripts" / name
        live.write_bytes(live.read_bytes() + b"\n# a later release\n")
    result = run_scaffold_verify(root, plugin_root=plugin_copy)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=live_plugin_drift" in result.stderr
    reported = [
        line.strip()[2:].split(":", 1)[0]
        for line in result.stderr.splitlines()
        if line.startswith("  - ")
    ]
    assert PLUGIN_ONLY_MEMBER in reported, result.stderr
    assert ORCHESTRATION_ONLY_MEMBER in reported, result.stderr
    # Counted over the REPORTED-MEMBER column, never over the whole message:
    # every bullet also spells the member name inside its path, so a raw
    # substring count is 2 for a correctly deduped report and cannot
    # distinguish that from a genuine double entry.
    assert reported.count("claim_record.py") == 1, result.stderr


def test_verify_refuses_a_non_regular_member(tmp_path):
    """Byte equality at a SYMLINKED member path is not evidence about the
    tree that member's own siblings resolve in: review_ready.py executes the
    draft_sha1.py beside its RESOLVED __file__, and codex_job.py invokes it
    without forwarding --plugin-root. The symlink here points at a
    BYTE-IDENTICAL copy, so a bytes-only check would pass. The same predicate
    covers the ordinary case of a member missing from one tree."""
    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0

    symlinked = _make_plugin_copy(tmp_path, name="plugin_symlinked")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    member = symlinked / "assets" / "scripts" / "review_ready.py"
    identical = elsewhere / "review_ready.py"
    identical.write_bytes(member.read_bytes())
    member.unlink()
    member.symlink_to(identical)
    assert member.read_bytes() == (root / "scripts" / "review_ready.py").read_bytes()
    result = run_scaffold_verify(root, plugin_root=symlinked)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=member_not_regular" in result.stderr
    assert "review_ready.py" in result.stderr
    assert "is a symlink" in result.stderr

    absent = _make_plugin_copy(tmp_path, name="plugin_absent")
    (absent / "assets" / "scripts" / "draft_sha1.py").unlink()
    result = run_scaffold_verify(root, plugin_root=absent)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=member_not_regular" in result.stderr
    assert "draft_sha1.py" in result.stderr
    assert "is missing" in result.stderr



def _drift_scenario(root, plugin_copy):
    live = plugin_copy / "assets" / "scripts" / "draft_ready.py"
    live.write_bytes(live.read_bytes() + b"\n# a later release\n")
    return 1, "reason=live_plugin_drift"


def _stale_scenario(root, plugin_copy):
    member = root / "scripts" / ORCHESTRATION_ONLY_MEMBER
    member.write_bytes(member.read_bytes() + b"\n# tampered\n")
    live = plugin_copy / "assets" / "scripts" / ORCHESTRATION_ONLY_MEMBER
    live.write_bytes(member.read_bytes())
    return 1, "reason=marker_stale"


def _success_scenario(root, plugin_copy):
    return 0, "scaffold_setup: verified"


def test_verify_refuses_a_non_regular_durable_member(tmp_path):
    """The shape predicate walks BOTH trees, not just the live one -- a check
    that only ever looked at the plugin side would pass every assertion in the
    test above. Its own test so that it fails on its own terms rather than
    being silenced by an unrelated failure earlier in a shared body."""
    root = _make_scaffold_root(tmp_path)
    _rewrite_markers(root)
    clean = _make_plugin_copy(tmp_path, name="plugin_for_durable_case")
    member = root / "scripts" / ORCHESTRATION_ONLY_MEMBER
    target = tmp_path / "durable_elsewhere.py"
    target.write_bytes(member.read_bytes())
    member.unlink()
    member.symlink_to(target)
    result = run_scaffold_verify(root, plugin_root=clean)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=member_not_regular" in result.stderr
    assert f"{ORCHESTRATION_ONLY_MEMBER}: durable copy is a symlink" in result.stderr


def test_verify_refuses_a_symlinked_marker(tmp_path):
    """The markers get the same predicate the members do. atomic_write_text
    refuses to publish over a marker NAME that is a symlink, but a plain read
    would FOLLOW one, and the runs/ guard covers the directory, not the leaf --
    so without this a marker symlinked anywhere would be believed."""
    root = _make_scaffold_root(tmp_path)
    _rewrite_markers(root)
    marker = root.joinpath(*MARKER_REL["plugin"])
    elsewhere = tmp_path / "marker_elsewhere"
    elsewhere.write_text(marker.read_text(), encoding="utf-8")
    marker.unlink()
    marker.symlink_to(elsewhere)
    result = run_scaffold_verify(root)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=member_not_regular" in result.stderr
    assert "plugin_bundle_hash: marker is a symlink" in result.stderr


@pytest.mark.parametrize(
    "scenario",
    [_success_scenario, _stale_scenario, _drift_scenario],
    ids=["success", "marker_stale", "live_plugin_drift"],
)
def test_verify_writes_nothing(tmp_path, scenario):
    """The contract: --verify NEVER repairs, on ANY path -- including the one
    that PASSES, where "it wrote a marker" would be invisible because the
    value it wrote is the value already there. Asserts the intended exit code
    and message FIRST: an unfixed tree exits 2 having touched nothing, so a
    bare no-write assertion is green before the flag exists.

    Snapshots the plugin tree too, which is here both the INVOKED and the
    CHECKED tree, so a stray __pycache__/ has nowhere else to land --
    removing sys.dont_write_bytecode turns the passing case RED."""
    root, plugin_copy = _drift_ready_pair(tmp_path)
    expected_code, expected_message = scenario(root, plugin_copy)

    before_root = _tree_snapshot(root)
    before_plugin = _tree_snapshot(plugin_copy)
    result = run_scaffold_verify(
        root,
        plugin_root=plugin_copy,
        script=plugin_copy / "assets" / "scripts" / "scaffold_setup.py",
    )
    assert result.returncode == expected_code, f"stdout={result.stdout} stderr={result.stderr}"
    assert expected_message in (result.stdout + result.stderr)
    assert _tree_snapshot(root) == before_root, "durable_root was mutated by --verify"
    assert _tree_snapshot(plugin_copy) == before_plugin, "the plugin tree was mutated by --verify"


def test_verify_plugin_root_binds_the_checked_tree(tmp_path):
    """Leg 3, pinned against the REAL consumer rather than asserted in prose.
    --plugin-root decides which tree the verdict is about, and
    segment_dispatch_driver.resolve_dirs() -- W5's default launcher since
    #516 -- must resolve its template and sibling scripts inside that same
    tree, or the verified tree and the executed tree are different trees."""
    root = _make_scaffold_root(tmp_path)
    clean = _make_plugin_copy(tmp_path, name="plugin_clean")
    updated = _make_plugin_copy(tmp_path, name="plugin_updated")
    _rewrite_markers(root)

    live_template = updated / "assets" / "templates" / "mass-translate-wf.template.js"
    live_template.write_bytes(live_template.read_bytes() + b"\n// a later release\n")

    ok = run_scaffold_verify(root, plugin_root=clean)
    assert ok.returncode == 0, f"stdout={ok.stdout} stderr={ok.stderr}"
    assert f"plugin_root={clean.resolve()}" in ok.stdout, ok.stdout

    bad = run_scaffold_verify(root, plugin_root=updated)
    assert bad.returncode == 1, f"stdout={bad.stdout} stderr={bad.stderr}"
    assert "reason=live_plugin_drift" in bad.stderr

    driver = _load_module(
        "segment_dispatch_driver", SCRIPTS_DIR / "segment_dispatch_driver.py"
    )
    dirs = driver.resolve_dirs(str(root), str(clean))
    assert dirs["template_script"] == clean / "assets" / "templates" / "mass-translate-wf.template.js"
    assert dirs["scripts_dir"] == clean / "assets" / "scripts"


def test_verify_refuses_a_bad_plugin_root(tmp_path):
    """An unsubstituted {{PLUGIN_ROOT}} token renders as the EMPTY STRING, not
    as the flag being omitted, and Path("").resolve() is the CWD -- which
    would silently make wherever the session happens to be launched from the
    tree the verdict is about. Refused by name, like codex_job.py's and
    segment_dispatch_driver.py's identical guards. --plugin-root without
    --verify is refused rather than ignored: accepting it would let an
    operator believe a WRITE had been checked against a named tree."""
    root = _make_scaffold_root(tmp_path)
    assert run_scaffold_setup(root).returncode == 0

    empty = run_scaffold_verify(root, plugin_root="   ")
    assert empty.returncode == 1
    assert "empty/whitespace-only" in empty.stderr
    assert "{{PLUGIN_ROOT}}" in empty.stderr

    not_a_plugin = tmp_path / "not_a_plugin"
    not_a_plugin.mkdir()
    shapeless = run_scaffold_verify(root, plugin_root=not_a_plugin)
    assert shapeless.returncode == 1
    # The DEDICATED refusal, by its own phrase -- and the absence of the
    # member-shape one. Deleting the --plugin-root shape check entirely would
    # still exit 1 here, because every member would then be "missing" under a
    # path that itself contains "assets/scripts/"; asserting only that
    # substring cannot tell the two apart.
    assert "does not resolve to a directory containing assets/scripts/" in shapeless.stderr
    assert "reason=member_not_regular" not in shapeless.stderr

    without_verify = subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD_SETUP_SRC),
            "--durable-root",
            str(root),
            "--plugin-root",
            str(PLUGIN_ROOT / "skills" / "literary-translator"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert without_verify.returncode == 1
    assert "only meaningful with --verify" in without_verify.stderr


def test_verify_refuses_a_symlinked_source_dir_in_the_checked_tree(tmp_path):
    """A checked tree whose assets/scripts is a symlink INTO the durable tree
    makes every member compare equal to itself, so a tampered durable root
    would verify clean -- a vacuous pass, reproduced before the guard existed.
    is_dir() follows symlinks, so the shape gate alone does not catch it."""
    root = _make_scaffold_root(tmp_path)
    _rewrite_markers(root)
    self_referential = tmp_path / "plugin_self_referential"
    (self_referential / "assets").mkdir(parents=True)
    (self_referential / "assets" / "scripts").symlink_to(root / "scripts")
    (self_referential / "assets" / "templates").symlink_to(root / "scripts")
    result = run_scaffold_verify(root, plugin_root=self_referential)
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "reason=plugin_source_dir_not_real" in result.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
