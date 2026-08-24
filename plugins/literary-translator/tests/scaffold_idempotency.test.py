"""tests/scaffold_idempotency.test.py -- regression lock for the two
"copy-once, never clobber" guards that make Step 0 and Step 0a safe to
re-invoke across a project's whole lifetime:

  (A) Step 0's auto-copy-then-halt (``profile_validate.py``'s
      ``ensure_profile_exists``): if ``.claude/literary-translator/
      profile.yml`` is ABSENT, the shipped ``assets/profile.example.yml`` is
      copied there verbatim and the run halts. If it is PRESENT -- in
      particular, a real, filled-in profile with a project's own real
      values -- it must be left completely untouched, checked fresh on
      EVERY invocation, forever. This suite drives the REAL
      ``profile_validate.py`` as a subprocess, exactly the way SKILL.md
      documents invoking it (``python3 assets/scripts/profile_validate.py
      --profile <path>``, always from the plugin's own install path --
      this is one of THREE plugin-path scripts never copied to
      durable_root: ``profile_validate.py``, ``validate_extraction.py``
      (the W2 post-extraction gate), and ``glossary_preflight.py`` (1.4.0,
      the W3 glossary staleness gate). (``resolve_codex_companion.py``, the
      W5 codex-companion path resolver, was a fourth such exclusion from
      1.4.7 until its stated reason was found false and reverted -- it is
      copied like every other self-anchored script now.)
      Against a constructed fixture profile built from the real shipped
      ``profile.example.yml`` with every placeholder substituted for real
      values (mirroring the "case 3" fixture in
      ``profile_example_validation.test.py``), and asserts the file's bytes
      never change across repeated invocations.

  (B) Step 0a's one-time template copy: ``style_bible.template.md`` /
      ``PLAN.template.md`` (and their sibling one-time-seed templates) are
      copied to their durable-root destination exactly ONCE, each
      individually guarded on its own destination's absence -- never
      re-copied, never regenerated, once a project has hand-adapted them.

      IMPORTANT: unlike (A), Step 0a's one-time TEMPLATE-COPY logic (half B,
      this suite's subject) has no standalone shipped script -- it is
      orchestrating-session prose the Claude session itself executes at
      scaffold time, not an importable/subprocess-able module. (As of 1.9.0 /
      #194, Step 0a's OTHER half -- writing the ``.plugin_bundle_hash`` /
      ``.orchestration_bundle_hash`` markers -- IS a shipped script,
      ``scaffold_setup.py``; but the one-time template copy this suite locks
      stays prose.) The pipeline gate scripts SKILL.md names as the "three
      plugin-path scripts never copied" (``profile_validate.py`` at Step 0,
      ``validate_extraction.py`` at W2, ``glossary_preflight.py`` at W3
      (1.4.0)) are likewise not Step 0a's copy logic -- nor is
      ``resolve_codex_companion.py`` at W5 (1.4.7), which no longer belongs
      to that set. So this half of the suite (1) transcribes the documented
      guard literally, as a small reference implementation using the exact
      same "copy iff destination absent" idiom as
      ``profile_validate.ensure_profile_exists`` (see
      ``one_time_copy_if_absent`` below), (2) exercises it against
      constructed fixture template content shaped exactly like the real
      thing (the ``LT_REQUIRED_FILL_BEGIN/END`` marker pairs and
      ``LT_PLACEHOLDER_UNFILLED`` sentinel ``scaffold_validate.py`` -- a
      REAL shipped script -- actually scans for), and (3) cross-checks the
      result against ``scaffold_validate.py``'s own real ``scan_markers``
      function: the freshly-copied scaffold must trip that scanner (proves
      the fixture is a faithful, still-unfilled template), and the
      hand-adapted, repeatedly-re-run result must NOT (proves the hand
      adaptation genuinely survived). A companion negative-control test
      (``test_repeatable_overwrite_helper_actually_overwrites``) proves the
      guard helper isn't a trivial no-op stub by contrasting it with the
      DIFFERENT, deliberately-unguarded "repeatable overwrite" treatment
      Step 0a gives ``mass-translate-wf.template.js`` /
      ``glossary-pass-wf.template.js``.
"""
import hashlib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
PROFILE_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "profile_validate.py"
SCAFFOLD_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "scaffold_validate.py"
EXAMPLE_PROFILE_PATH = ASSETS_ROOT / "profile.example.yml"

assert PROFILE_VALIDATE_SCRIPT.is_file(), f"profile_validate.py not found at {PROFILE_VALIDATE_SCRIPT}"
assert SCAFFOLD_VALIDATE_SCRIPT.is_file(), f"scaffold_validate.py not found at {SCAFFOLD_VALIDATE_SCRIPT}"
assert EXAMPLE_PROFILE_PATH.is_file(), f"profile.example.yml not found at {EXAMPLE_PROFILE_PATH}"

# Every literal placeholder profile.example.yml ships, transcribed from
# profile_validate.py's own PLACEHOLDER_SUBSTRINGS constant -- see that
# script's own constant if this list ever needs re-deriving.
PLACEHOLDER_SUBSTRINGS = (
    "YOUR BOOK TITLE HERE",
    "/ABS/PATH/TO/YOUR_PROJECT",
    "/ABS/PATH/TO/YOUR_SOURCE",
)
# #727: the shipped example ships several CHOOSE_-prefixed sentinels, and
# the set keeps growing (#730 added verse_policy.mode) -- a hand-typed tuple
# here (as this used to be) freezes whatever sentinels existed at authoring
# time and silently stops covering the next one added. Read the current set
# off profile.example.yml, never off a count restated here.
# make_real_values_profile() below replaces every individual sentinel it
# knows about by name (so a bad replacement is attributed to a specific
# field, not a generic "CHOOSE_ survived somewhere"), and then asserts the
# GENERIC `"CHOOSE_" not in text` invariant as the actual completeness
# guard -- see the assertion at the bottom of that function.
# Must match profile_validate.py's own ALLOW_TMP_ROOT_ENV_VAR constant --
# this file drives profile_validate.py as a subprocess (see module
# docstring, Part A), so it can't import that constant, only mirror the
# literal name. pytest's own tmp_path resolves under a literal `tmp` path
# component on Linux (e.g. CI runners honoring `TMPDIR=/tmp`), which
# check_durable_root would otherwise reject.
ALLOW_TMP_ROOT_ENV_VAR = "LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scaffold_validate = _load_module("scaffold_validate_under_test", SCAFFOLD_VALIDATE_SCRIPT)


def _iter_string_values(obj):
    """Yields every string LEAF value anywhere in a parsed YAML/JSON-like
    structure -- deliberately values only, never the raw text, so a
    CHOOSE_-prefix search over this iterator cannot be tripped by a prose
    comment that merely discusses a sentinel by name (comments never survive
    yaml.safe_load in the first place)."""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_string_values(value)
    elif isinstance(obj, str):
        yield obj


# ---------------------------------------------------------------------------
# Part A -- Step 0's auto-copy-then-halt (real profile_validate.py)
# ---------------------------------------------------------------------------

def make_real_values_profile(tmp_path):
    """Build a ``.claude/literary-translator/profile.yml`` fixture that is
    the shipped ``profile.example.yml`` with every documented placeholder
    substituted for a real value -- structurally identical otherwise (same
    approach as ``profile_example_validation.test.py``'s "case 3" fixture),
    so this drives profile_validate.py's REAL schema + procedural checks
    end to end (not just its Step-1 existence-check short-circuit) and
    still lands on a clean, exit-0 pass.

    Returns (profile_path, durable_root, source_path).
    """
    durable_root = tmp_path / "book_project"  # deliberately NOT created --
    # Step 0 only requires durable_root's PARENT to exist; durable_root
    # itself is Step 0a's job.
    source_path = tmp_path / "source" / "book.epub"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"fake epub bytes for existence-check purposes only")

    text = EXAMPLE_PROFILE_PATH.read_text(encoding="utf-8")
    text = text.replace("YOUR BOOK TITLE HERE", "Le Comte de Test")
    text = text.replace("/ABS/PATH/TO/YOUR_SOURCE.epub", str(source_path))
    # Replaces BOTH occurrences (project.durable_root itself, and the
    # output.destination value that carries the same prefix) in one shot.
    text = text.replace("/ABS/PATH/TO/YOUR_PROJECT", str(durable_root))
    text = text.replace("CHOOSE_none_confirmed_or_regex", "none_confirmed")
    text = text.replace(
        "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex", "none_confirmed"
    )
    text = text.replace("CHOOSE_true_or_false", "true")
    text = text.replace("CHOOSE_live_or_offline", "offline")
    text = text.replace(
        "CHOOSE_translate_all_or_preserve_source_or_omit_apparatus_or_body_refs_only",
        "translate_all",
    )
    text = text.replace(
        "CHOOSE_segment_drafts_and_audit_or_assembled_book", "segment_drafts_and_audit"
    )
    text = text.replace("CHOOSE_obsidian_or_epub_or_custom", "obsidian")
    # #730. A non-mixed_by_length answer on purpose: that is the one mode the
    # schema makes threshold_lines REQUIRED for, and the example ships it null.
    text = text.replace(
        "CHOOSE_full_rhymed_plus_literal_or_full_rhymed_only_or_rhythmic_approximation"
        "_or_mixed_by_length_or_literal_only_or_skip",
        "full_rhymed_plus_literal",
    )

    # Defensive: fail loudly here, not with a confusing downstream schema
    # error, if this fixture builder ever drifts out of sync with
    # profile_validate.py's own placeholder list.
    for placeholder in PLACEHOLDER_SUBSTRINGS:
        assert placeholder not in text, f"fixture still contains placeholder {placeholder!r}"
    # #727: generic completeness guard, replacing a hand-typed sentinel
    # tuple -- any FURTHER CHOOSE_-prefixed sentinel added to the shipped
    # example must fail this assertion loudly, rather than silently surviving
    # because nobody remembered to add its literal string to a list here.
    # Checked over the PARSED document's own string VALUES, mirroring what
    # profile_validate.py's real placeholder scan actually walks -- a raw
    # substring search over the YAML text would also trip on the shipped
    # example's own prose COMMENTS discussing the sentinel by name (e.g. the
    # plain_text.verse_detection comment block, which says "this CHOOSE_
    # sentinel" without ever being one), which is not the property this
    # guard exists to check.
    survivors = [
        v for v in _iter_string_values(yaml.safe_load(text)) if v.startswith("CHOOSE_")
    ]
    assert survivors == [], (
        f"fixture still contains an unreplaced CHOOSE_ sentinel value -- a "
        f"new sentinel was added to profile.example.yml without a matching "
        f"replacement here: {survivors!r}"
    )

    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(text, encoding="utf-8")
    return profile_path, durable_root, source_path


def run_profile_validate(profile_path, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(PROFILE_VALIDATE_SCRIPT), "--profile", str(profile_path)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_step0_real_profile_stays_byte_identical_across_repeated_invocations(tmp_path):
    """The core assertion this test file exists for: a fixture profile.yml
    with real values must come out of Step 0 byte-for-byte unchanged, every
    time -- proving ``ensure_profile_exists``'s absence-guard genuinely
    never re-fires once a real file is sitting there, no matter how many
    times Step 0 is re-run (a project resumed across many sessions)."""
    profile_path, _durable_root, _source_path = make_real_values_profile(tmp_path)

    original_bytes = profile_path.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    # "Repeated" -- not just a single second invocation -- matching the
    # same rigor Step 0a's template-copy half of this suite applies below.
    for i in range(3):
        result = run_profile_validate(profile_path, extra_env={ALLOW_TMP_ROOT_ENV_VAR: "1"})
        assert result.returncode == 0, (
            f"invocation #{i + 1} did not exit clean; this fixture is meant "
            f"to be a fully valid, real profile so a non-zero exit means "
            f"the fixture (or the script) has a real problem, not just the "
            f"idempotency guard under test.\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        current_bytes = profile_path.read_bytes()
        assert current_bytes == original_bytes, (
            f"profile.yml was modified by Step 0 invocation #{i + 1} -- the "
            f"auto-copy-then-halt guard re-fired (or otherwise touched the "
            f"file) even though a real, filled-in profile already existed"
        )
        assert hashlib.sha256(current_bytes).hexdigest() == original_hash


def test_step0_auto_copy_only_fires_when_profile_is_genuinely_absent(tmp_path):
    """Negative control for the test above: proves the guard is actually
    doing absence-based work, rather than the script simply never writing
    to the profile path under any circumstance (which would make the
    "byte-identical" assertion above vacuous). When the profile path is
    genuinely absent, Step 0 copies the shipped example there verbatim and
    halts non-zero."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    assert not profile_path.exists()

    result = run_profile_validate(profile_path)

    assert result.returncode != 0, "Step 0 must halt on a freshly auto-copied profile"
    assert profile_path.is_file(), "Step 0 must have created the starter profile"
    assert profile_path.read_bytes() == EXAMPLE_PROFILE_PATH.read_bytes(), (
        "the auto-copied starter profile must be a verbatim copy of "
        "assets/profile.example.yml"
    )


# ---------------------------------------------------------------------------
# Part B -- Step 0a's one-time template copy (no shipped script; see module
# docstring). Reference implementation of the documented guard, exercised
# against constructed fixtures and cross-checked against the REAL
# scaffold_validate.py scanner.
# ---------------------------------------------------------------------------

def one_time_copy_if_absent(template_path: Path, dest_path: Path) -> bool:
    """Literal transcription of Step 0a's documented one-time-seed copy:
    'guarded on its own destination's absence -- never re-copied, never
    regenerated'. Same idiom as profile_validate.py's own
    ``ensure_profile_exists`` (copy iff absent), generalized to an
    arbitrary (template, destination) pair, since Step 0a applies this
    identical guard individually to seven different files
    (``PLAN.template.md`` -> ``PLAN.md``, ``style_bible.template.md`` ->
    ``style_bible.md``, etc.) -- see SKILL.md's Step 0a section.

    Returns True if a copy was actually performed (dest was absent), False
    if the guard fired and dest was left completely untouched.
    """
    if dest_path.exists():
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, dest_path)
    return True


def repeatable_overwrite(template_path: Path, dest_path: Path) -> None:
    """Contrast helper: the DIFFERENT treatment Step 0a documents for
    ``mass-translate-wf.template.js`` / ``glossary-pass-wf.template.js`` --
    'repeatable-overwrite ... NEVER the one-time-seed treatment the other
    templates get'. Used only to prove ``one_time_copy_if_absent``'s
    idempotency in the tests below is a real, deliberate guard and not an
    artifact of a helper that simply never writes."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, dest_path)


# Constructed fixture template content, shaped exactly like the real
# LT_REQUIRED_FILL_BEGIN/END marker pairs + LT_PLACEHOLDER_UNFILLED
# sentinel that the REAL scaffold_validate.py scans for (see its
# MARKER_BEGIN_RE / MARKER_END_RE / SENTINEL).
STYLE_BIBLE_TEMPLATE_FIXTURE = """# Style Bible

<!-- LT_REQUIRED_FILL_BEGIN: section_a_register -->
LT_PLACEHOLDER_UNFILLED
<!-- LT_REQUIRED_FILL_END -->

Some free-form illustrative notes that live outside any marker span and are
never scanned by scaffold_validate.py.

<!-- LT_REQUIRED_FILL_BEGIN: section_g_glossary -->
LT_PLACEHOLDER_UNFILLED
<!-- LT_REQUIRED_FILL_END -->
"""

PLAN_TEMPLATE_FIXTURE = """# PLAN

<!-- LT_REQUIRED_FILL_BEGIN: book_summary -->
LT_PLACEHOLDER_UNFILLED
<!-- LT_REQUIRED_FILL_END -->

<!-- LT_REQUIRED_FILL_BEGIN: risk_notes -->
LT_PLACEHOLDER_UNFILLED
<!-- LT_REQUIRED_FILL_END -->
"""

STYLE_BIBLE_HAND_ADAPTED = """# Style Bible

<!-- LT_REQUIRED_FILL_BEGIN: section_a_register -->
Formal 17th-century register throughout; use "vy" not "ty" (see
target.language.register_notes). Archaisms preserved where they read as
period flavor, not as errors.
<!-- LT_REQUIRED_FILL_END -->

Some free-form illustrative notes that live outside any marker span and are
never scanned by scaffold_validate.py.

<!-- LT_REQUIRED_FILL_BEGIN: section_g_glossary -->
See canon.json for the frozen proper-noun glossary; this section intentionally
left as a pointer rather than a duplicate list.
<!-- LT_REQUIRED_FILL_END -->
"""

PLAN_HAND_ADAPTED = """# PLAN

<!-- LT_REQUIRED_FILL_BEGIN: book_summary -->
A three-volume memoir of 17th-century French court gossip; this project
covers tome 3 only, picking up mid-volume.
<!-- LT_REQUIRED_FILL_END -->

<!-- LT_REQUIRED_FILL_BEGIN: risk_notes -->
Heavy footnote apparatus (19th-c. editorial notes); no verse in this volume.
<!-- LT_REQUIRED_FILL_END -->
"""


def _run_one_template_case(tmp_path, template_name, dest_name, template_text, hand_adapted_text):
    durable_root = tmp_path / "durable_root"
    durable_root.mkdir()
    template_path = tmp_path / template_name
    template_path.write_text(template_text, encoding="utf-8")
    dest_path = durable_root / dest_name

    assert not dest_path.exists()

    # --- fresh Step 0a run: destination absent -> copy happens ----------
    copied = one_time_copy_if_absent(template_path, dest_path)
    assert copied is True
    assert dest_path.read_bytes() == template_path.read_bytes()

    # Sanity: the freshly-copied scaffold is still genuinely unfilled --
    # the REAL scaffold_validate.py scanner must trip on it. This proves
    # the fixture faithfully represents an as-shipped, not-yet-adapted
    # scaffold file (not a placeholder assertion).
    fresh_findings = scaffold_validate.scan_markers(dest_path, dest_path.read_text(encoding="utf-8"))
    assert fresh_findings, "freshly-copied scaffold fixture should still trip the LT_PLACEHOLDER_UNFILLED scan"

    # --- W1: user hand-adapts the file (fills every marker span) --------
    dest_path.write_text(hand_adapted_text, encoding="utf-8")
    hand_adapted_bytes = dest_path.read_bytes()

    # --- repeated Step 0a re-runs (project resumed across N sessions) ---
    for i in range(3):
        copied_again = one_time_copy_if_absent(template_path, dest_path)
        assert copied_again is False, f"re-run #{i + 1} incorrectly re-copied over a hand-adapted file"
        assert dest_path.read_bytes() == hand_adapted_bytes, (
            f"re-run #{i + 1} altered the hand-adapted {dest_name} -- Step 0a's "
            f"one-time-seed guard must never touch this file again once it exists"
        )

    # The hand-adapted, repeatedly-preserved file must now pass the REAL
    # scaffold_validate.py scan cleanly (no surviving sentinel).
    final_findings = scaffold_validate.scan_markers(dest_path, dest_path.read_text(encoding="utf-8"))
    assert final_findings == [], f"hand-adapted {dest_name} unexpectedly still trips scaffold_validate.py: {final_findings}"

    # And it must genuinely differ from the raw template -- proof the
    # preserved content is the hand-adapted version, not a coincidental
    # match with the template.
    assert dest_path.read_bytes() != template_path.read_bytes()


def test_style_bible_survives_repeated_step0a_reruns_after_hand_adaptation(tmp_path):
    _run_one_template_case(
        tmp_path,
        "style_bible.template.md",
        "style_bible.md",
        STYLE_BIBLE_TEMPLATE_FIXTURE,
        STYLE_BIBLE_HAND_ADAPTED,
    )


def test_plan_survives_repeated_step0a_reruns_after_hand_adaptation(tmp_path):
    _run_one_template_case(
        tmp_path,
        "PLAN.template.md",
        "PLAN.md",
        PLAN_TEMPLATE_FIXTURE,
        PLAN_HAND_ADAPTED,
    )


def test_repeatable_overwrite_helper_actually_overwrites(tmp_path):
    """Negative control for Part B: proves ``one_time_copy_if_absent``'s
    idempotency above is a deliberate, meaningful guard rather than an
    artifact of a helper that just never writes to an existing path.
    Contrasts it against ``repeatable_overwrite`` -- the DIFFERENT
    treatment Step 0a documents for ``mass-translate-wf.template.js`` /
    ``glossary-pass-wf.template.js``, which ARE re-instantiated fresh every
    run, unconditionally."""
    template_path = tmp_path / "mass-translate-wf.template.js"
    template_path.write_text("// v2 template content\n", encoding="utf-8")
    dest_path = tmp_path / "durable_root" / "runs" / "workflows" / "mass-translate-wf.js"
    dest_path.parent.mkdir(parents=True)
    dest_path.write_text("// stale content from a previous run\n", encoding="utf-8")

    repeatable_overwrite(template_path, dest_path)

    assert dest_path.read_text(encoding="utf-8") == "// v2 template content\n"


# ===========================================================================
# Part C -- the migration GUARD (refuse, never a clever preserving copy)
# for resolve_codex_companion.py's copy pass, added when its exclusion was
# reverted. Every OTHER bundle member gets plain unconditional overwrite
# ("safe since these files are never hand-edited") -- but that premise was
# never true for THIS path: it was explicitly excluded from the copy pass
# before this release, so a project that hit the resulting exit-2 default-
# launch defect could have reasonably worked around it by hand-adapting
# its own copy exactly there, on the documented strength of that
# destination being untouched. This deliberately does NOT try to preserve
# a divergent file automatically via rename-then-copy: that shape has
# three real failure modes -- symlink-through, pointer-only backups,
# concurrent-backup races -- none of which Step 0a's own
# orchestrating-session-executed prose can close for real. See
# SKILL.md's own "Migration note" in its Step 0a section for the full
# reasoning this transcribes literally.
# ===========================================================================


def apply_resolve_codex_companion_migration(shipped_source: Path, dest_path: Path) -> str:
    """Literal transcription of SKILL.md's Step 0a migration note for
    resolve_codex_companion.py: absent -> copy; ANY genuine regular file ->
    copy (the ordinary managed overwrite every other bundle member gets);
    anything NON-REGULAR -> HALT, never a silent overwrite and never an
    automatic backup.

    The regular-file branch used to demand byte-identity to the shipped
    source. That was the migration limb, and #287 -- the first release to
    change the resolver's bytes -- retired it: identity was standing in for
    "is this copy managed?", which held only while the shipped bytes were
    frozen, so afterwards every ordinary project's own MANAGED copy reads as
    divergent and halts on the majority path. Measured on both live books at
    the time: two managed copies, zero hand adaptations.

    "Anything non-regular" is still deliberately broad: a symlink (regardless
    of what it points at -- its target content is never even read), a
    directory, or any other non-absent, non-regular entry os.lstat() reports. Renaming a divergent file aside
    before copying over it is NOT what this does, because that shape has
    three real failure modes: a byte-identical symlink stays a symlink
    after a naive copy, since copying OVER a symlink writes THROUGH it
    rather than replacing it; a divergent symlink's "backup" preserves
    only a pointer, not the adapted bytes it points at; and two
    concurrent migrations can race on the same backup name -- see
    SKILL.md's own note for the full reasoning. A halt has none of them,
    because it performs no automatic write to a non-regular destination at
    all.

    Classifies with os.lstat() -- NEVER Path.exists()/is_file(), which
    FOLLOW a symlink and would silently write through it, leaving
    whatever was actually there in place while reporting success.

    Returns one of "copied-fresh" (was absent), "copied-over" (was
    already a genuine regular file, whatever its bytes -- so the copy may
    replace DIFFERENT bytes; it is deliberately not called a no-op), "halt" (anything
    non-regular) -- the three outcomes SKILL.md's own migration note names, so a
    test can assert on which one fired rather than only on the end
    state."""
    try:
        st = dest_path.lstat()
    except FileNotFoundError:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shipped_source, dest_path)
        return "copied-fresh"
    if stat.S_ISREG(st.st_mode):
        shutil.copyfile(shipped_source, dest_path)
        return "copied-over"
    return "halt"


def _resolve_codex_companion_shipped_fixture(tmp_path: Path) -> Path:
    shipped = tmp_path / "shipped_resolve_codex_companion.py"
    shipped.write_text("# the real, shipped resolve_codex_companion.py\n", encoding="utf-8")
    return shipped


def test_resolve_codex_companion_migration_copies_freely_when_absent(tmp_path):
    """The overwhelming majority case: any project that never hit the old
    exit-2 default-launch defect (or hit it but never worked around it) has
    nothing at this destination at all. Must copy exactly like every other
    bundle member -- no backup, no special handling, because there is
    nothing to protect."""
    shipped = _resolve_codex_companion_shipped_fixture(tmp_path)
    dest = tmp_path / "durable_root" / "scripts" / "resolve_codex_companion.py"

    outcome = apply_resolve_codex_companion_migration(shipped, dest)

    assert outcome == "copied-fresh"
    assert dest.read_bytes() == shipped.read_bytes()


def test_resolve_codex_companion_migration_copies_over_a_byte_identical_copy(tmp_path):
    """A project already on the corrected copy pass gets the ordinary
    unconditional-overwrite treatment, matching every other bundle member.
    An earlier version of this docstring said such a project "sees the
    byte-identical case forever after"; #287 falsified that -- it is the
    first release to change the shipped resolver's bytes, after which the
    same project presents a DIVERGENT managed copy. That case is now this
    same branch (see the test below), which is why the classifier no longer
    compares bytes at all."""
    shipped = _resolve_codex_companion_shipped_fixture(tmp_path)
    dest = tmp_path / "durable_root" / "scripts" / "resolve_codex_companion.py"
    dest.parent.mkdir(parents=True)
    shutil.copyfile(shipped, dest)

    outcome = apply_resolve_codex_companion_migration(shipped, dest)

    assert outcome == "copied-over"
    assert dest.read_bytes() == shipped.read_bytes()


def test_resolve_codex_companion_migration_overwrites_a_stale_managed_copy(tmp_path):
    """The case that retired the byte-identity branch (#287). A project whose
    durable root holds an OLDER managed copy -- the shape every ordinary
    project has after any release that edits the resolver -- must get the
    ordinary overwrite, not a halt. This test previously asserted the
    opposite ("halts rather than overwrite a hand-adapted copy"): the
    classifier could not distinguish a stale managed copy from a hand
    adaptation, and telling them apart needs the prior-version digest this
    design deliberately refuses to carry. Measured on the two live books
    before the change: both held a plain regular file byte-identical to the
    then-shipped resolver, so the population the halt would have caught was
    two managed copies and zero adaptations.

    What is NOT waved through is any non-regular entry -- see the symlink
    and directory tests below, which are the limb that did not expire."""
    shipped = _resolve_codex_companion_shipped_fixture(tmp_path)
    dest = tmp_path / "durable_root" / "scripts" / "resolve_codex_companion.py"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"# an older MANAGED copy, from a previous release\n")

    outcome = apply_resolve_codex_companion_migration(shipped, dest)

    assert outcome == "copied-over"
    assert dest.read_bytes() == shipped.read_bytes()
    assert not list(dest.parent.glob("resolve_codex_companion.py.*")), (
        "still no backup sibling of any name -- the overwrite is the ordinary "
        "managed one, not a preserving copy"
    )


def test_resolve_codex_companion_migration_halts_on_a_symlink_even_with_an_identical_looking_target(tmp_path):
    """codex's specific finding against the earlier backup-and-copy design,
    still worth pinning now that the design is refuse-only: a symlink
    whose TARGET happens to be byte-identical to the shipped source must
    still halt, never be waved through as "copied-over". os.lstat()
    classifies the symlink itself (S_ISLNK), never resolving to compare
    the target's content -- the whole point is refusing to make ANY
    judgment about what a symlink at this path might mean, since a
    symlink is not the "genuine regular file" the copy branch is
    defined over at all."""
    shipped = _resolve_codex_companion_shipped_fixture(tmp_path)
    dest = tmp_path / "durable_root" / "scripts" / "resolve_codex_companion.py"
    dest.parent.mkdir(parents=True)
    identical_target = tmp_path / "elsewhere_but_identical.py"
    shutil.copyfile(shipped, identical_target)
    dest.symlink_to(identical_target)

    outcome = apply_resolve_codex_companion_migration(shipped, dest)

    assert outcome == "halt", (
        "a symlink must halt regardless of what its target contains -- "
        "os.lstat() must never be resolved into a content comparison"
    )
    assert dest.is_symlink(), "the symlink itself must be left exactly as it was"


def test_resolve_codex_companion_migration_halts_on_a_directory_at_the_destination(tmp_path):
    """A directory is a real, non-absent entry -- os.lstat() succeeds on it
    -- so it must halt exactly like a divergent file, never be silently
    treated as "absent" the way a naive exists()-only check might invite
    (a directory IS something exists() reports True for, but a plain
    is_file()-style check some other naive implementation might reach for
    reports False for it, risking the opposite mistake of treating a real
    directory as absent and attempting to copy INTO or OVER it)."""
    shipped = _resolve_codex_companion_shipped_fixture(tmp_path)
    dest = tmp_path / "durable_root" / "scripts" / "resolve_codex_companion.py"
    dest.mkdir(parents=True)

    outcome = apply_resolve_codex_companion_migration(shipped, dest)

    assert outcome == "halt"
    assert dest.is_dir(), "the directory must be left exactly as it was"


def test_skill_migration_note_actually_says_halt_not_backup_and_copy():
    """codex's finding on the reference implementation above: it is tested
    thoroughly, but nothing here ties it back to what SKILL.md's own
    prose actually instructs a Claude session to do -- someone could
    revert SKILL.md's migration note to the earlier backup-and-copy shape
    (the one this design deliberately replaced) while leaving THIS file's
    reference implementation and its own tests untouched, and every test
    above would keep passing green. Pins the actual prose: the migration
    note names HALT explicitly, and does not instruct a
    `.pre-upgrade-backup`-style rename-then-copy (the literal filename
    pattern the earlier, superseded design used) -- catching a reversion
    of the DOCUMENTED CONTRACT itself, independent of whether this file's
    own transcription also reverted.

    Checking only for the bare word
    "HALT" is too weak -- a document saying "HALT only on directories"
    would pass it while silently dropping the symlink case. Pins that the
    halt covers BOTH non-regular entry kinds the note's own per-entry-kind
    instruction distinguishes (a symlink and a directory).

    It used to pin a THIRD kind, a divergent regular file, and #287 cut
    that limb deliberately -- so this test now pins the opposite in that
    slot: a genuine regular file, WHATEVER its bytes, copies normally. The
    byte-identity test was standing in for "is this copy managed?", which
    held only while the shipped resolver's bytes never changed; the first
    release to edit them makes every ordinary project's own managed copy
    read as divergent, so the halt fired on the majority path. Re-adding
    that branch would re-break Step 0a for every existing project, which is
    why it is pinned in the new direction rather than simply unasserted --
    an unasserted contract is the one that quietly reverts."""
    skill_md = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    start = text.find("Migration note, mandatory before")
    assert start != -1, "could not locate SKILL.md's resolve_codex_companion.py migration note"
    # Narrower than the whole note: JUST the operative instruction (the
    # three-outcome list), stopping before the "deliberately a REFUSAL"
    # paragraph -- which legitimately explains, and therefore legitimately
    # NAMES, the superseded backup-and-copy shape it replaced. Checking
    # the whole note would make this test fail against its own correct,
    # historical prose -- the same trap fetch_citation_bundle.test.py's
    # own exclusion-span technique exists to route around.
    end = text.find("**This is deliberately a REFUSAL", start)
    assert end != -1, "could not locate the end of the migration note's operative instruction"
    instruction_section = text[start:end]

    # Checking for "HALT" and
    # for the three entry-kind labels INDEPENDENTLY cannot distinguish an
    # instruction from its own negation, because presence is invariant
    # under "do not" -- a migration note rewritten to say "do not HALT
    # before copying anything" keeps every token asserted below present
    # and still passes. Bind the verb to its OWN trigger clause instead of
    # scattering the check across the whole section: the note's
    # established style already writes each outcome as "<condition> ->
    # <action>" (see the "Absent" and regular-file bullets above this
    # one, both "-> copy normally"), so the one substring a negation like
    # "do not HALT" is forced to break is the arrow sitting directly
    # against the verb -- "→ HALT before copying anything." -- since
    # inserting "do not"/"never"/"should not anything" between them is
    # exactly what a negation needs to do. A bag-of-words check over the
    # same span would not notice; this one does, because it pins the
    # ADJACENCY, not just the token.
    trigger_clause_end = text.find("- **Symlink**", start)
    assert trigger_clause_end != -1 and start < trigger_clause_end < end, (
        "could not locate the start of the per-entry-kind recovery list "
        "to bound the 'Anything else' trigger clause"
    )
    trigger_clause = text[start:trigger_clause_end]
    assert "→ HALT before copying anything." in trigger_clause, (
        "the trigger clause must say the verb ADJACENT to its own arrow -- "
        "'→ HALT before copying anything.', exactly as the note's own "
        "'Absent' and regular-file bullets say '→ copy normally.' "
        "-- not merely contain the word HALT somewhere in the section. "
        "'do not HALT before copying anything' keeps HALT present but "
        "breaks this exact adjacency, which is the property that actually "
        "distinguishes the instruction from its own negation"
    )
    assert "HALT" in instruction_section, (
        "the migration note's ACTUAL INSTRUCTION must say HALT on a non-"
        "absent, NON-REGULAR destination -- this is the operative safety "
        "property, and its absence here means the documented contract "
        "reverted to something else"
    )
    assert "pre-upgrade-backup" not in instruction_section, (
        "the migration note's ACTUAL INSTRUCTION must NOT describe the "
        "superseded backup-and-copy shape (rename to a .pre-upgrade-backup "
        "sibling) -- that design was replaced because it cannot be made "
        "symlink-safe or concurrency-safe from orchestrating-session prose "
        "alone. (The explanatory paragraph AFTER this instruction may still "
        "name it historically -- that is not what this check bounds.)"
    )
    assert "**A genuine regular file** — whatever its bytes → copy" in instruction_section, (
        "the note must say a genuine regular file copies normally WHATEVER "
        "its bytes (#287). Re-introducing a byte-identity condition here "
        "re-breaks Step 0a on every project that already holds a managed "
        "copy from an earlier release -- the majority path, not the "
        "exceptional one -- because a stale managed copy and a hand "
        "adaptation are indistinguishable without the prior-version digest "
        "this design refuses to carry"
    )
    # The assertion above pins a PREFIX, and a prefix is satisfied by a
    # bullet that goes on to re-add the very condition #287 cut ("...→ copy
    # normally. If its bytes differ from the shipped source → HALT."). Bound
    # the regular-file bullet and pin that NOTHING inside it routes to a
    # halt, using the same arrow-adjacency the trigger-clause check above
    # relies on: a re-added byte-identity limb has to write its own
    # "→ HALT" to be an instruction at all, while prose that merely
    # NARRATES the cut limb ("the halt fired on the majority path") never
    # puts the verb against an arrow.
    reg_start = instruction_section.find("- **A genuine regular file**")
    reg_end = instruction_section.find("- **Anything else**", reg_start)
    assert reg_start != -1 and reg_start < reg_end, (
        "could not bound the regular-file bullet between its own label and "
        "the 'Anything else' bullet that follows it"
    )
    assert "→ HALT" not in instruction_section[reg_start:reg_end], (
        "the regular-file bullet must not route ANY condition to a halt "
        "(#287): re-adding 'if its bytes differ → HALT' after the required "
        "'whatever its bytes → copy' prefix re-breaks Step 0a on every "
        "project holding a managed copy from an earlier release, and a "
        "prefix-only check would pass it"
    )
    # `trigger_clause` above is bounded at its END only -- it still opens
    # at the note's first line, so it legitimately contains the "Absent"
    # and regular-file bullets and their "→ copy normally.". Bound the
    # halting bullet on BOTH sides to say what may not appear INSIDE it.
    else_start = text.find("- **Anything else**", start)
    assert else_start != -1 and else_start < trigger_clause_end, (
        "could not locate the '- **Anything else**' bullet that carries "
        "the HALT"
    )
    else_bullet = " ".join(text[else_start:trigger_clause_end].split())
    assert "a symlink" in else_bullet and "a directory" in else_bullet, (
        "both non-regular kinds must be named in the HALT's OWN bullet, "
        "not merely somewhere in the section -- the later "
        "'- **Symlink**' recovery label keeps the token present even when "
        "the bullet has stopped halting on symlinks"
    )
    assert "copy normally" not in else_bullet and "copies normally" not in else_bullet, (
        "nothing inside the halting bullet may be routed BACK to a copy: "
        "'a symlink copies normally like any other entry; a directory ... "
        "→ HALT' names both kinds and keeps the arrow adjacency, yet "
        "reopens exactly the copy-through-a-symlink failure mode the "
        "backup-and-copy design was rejected for"
    )
    assert "Divergent regular file" not in instruction_section, (
        "the per-entry-kind recovery list must no longer instruct the "
        "operator to move a divergent regular file aside -- that branch no "
        "longer halts, so an instruction for it is unreachable prose"
    )
    assert "Symlink" in instruction_section, (
        "the HALT must explicitly cover a SYMLINK -- omitting it would "
        "silently reopen the copy-through-a-symlink failure mode the "
        "earlier backup-and-copy design was rejected for"
    )
    assert "Directory" in instruction_section, (
        "the HALT must explicitly cover a DIRECTORY -- omitting it risks "
        "a naive implementation treating a directory as \"not a file, so "
        "not really there\" and attempting to copy into or over it"
    )
