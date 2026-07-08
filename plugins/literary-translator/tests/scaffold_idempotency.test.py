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
      this is the ONE script never copied to durable_root), against a
      constructed fixture profile built from the real shipped
      ``profile.example.yml`` with every placeholder substituted for real
      values (mirroring the "case 3" fixture in
      ``profile_example_validation.test.py``), and asserts the file's bytes
      never change across repeated invocations.

  (B) Step 0a's one-time template copy: ``style_bible.template.md`` /
      ``PLAN.template.md`` (and their sibling one-time-seed templates) are
      copied to their durable-root destination exactly ONCE, each
      individually guarded on its own destination's absence -- never
      re-copied, never regenerated, once a project has hand-adapted them.

      IMPORTANT: unlike (A), Step 0a has NO standalone shipped script under
      ``assets/scripts/`` -- SKILL.md names exactly one script invocation
      anywhere ("Implemented by scripts/profile_validate.py..."; grep
      SKILL.md for "python3"/"Implemented by" turns up nothing else). Step
      0a's copy logic is orchestrating-session prose the Claude session
      itself executes at scaffold time, not an importable/subprocess-able
      module. So this half of the suite (1) transcribes the documented
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
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
PROFILE_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "profile_validate.py"
SCAFFOLD_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "scaffold_validate.py"
EXAMPLE_PROFILE_PATH = ASSETS_ROOT / "profile.example.yml"

assert PROFILE_VALIDATE_SCRIPT.is_file(), f"profile_validate.py not found at {PROFILE_VALIDATE_SCRIPT}"
assert SCAFFOLD_VALIDATE_SCRIPT.is_file(), f"scaffold_validate.py not found at {SCAFFOLD_VALIDATE_SCRIPT}"
assert EXAMPLE_PROFILE_PATH.is_file(), f"profile.example.yml not found at {EXAMPLE_PROFILE_PATH}"

# Every literal placeholder profile.example.yml ships, transcribed from
# profile_validate.py's own PLACEHOLDER_SUBSTRINGS + the three CHOOSE_
# sentinels it names in its module docstring -- see that script's own
# constant if this list ever needs re-deriving.
PLACEHOLDER_SUBSTRINGS = (
    "YOUR BOOK TITLE HERE",
    "/ABS/PATH/TO/YOUR_PROJECT",
    "/ABS/PATH/TO/YOUR_SOURCE",
)
CHOOSE_SENTINELS = (
    "CHOOSE_none_confirmed_or_regex",
    "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex",
    "CHOOSE_live_or_offline",
)
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
    text = text.replace("CHOOSE_live_or_offline", "offline")

    # Defensive: fail loudly here, not with a confusing downstream schema
    # error, if this fixture builder ever drifts out of sync with
    # profile_validate.py's own placeholder list.
    for placeholder in PLACEHOLDER_SUBSTRINGS:
        assert placeholder not in text, f"fixture still contains placeholder {placeholder!r}"
    for sentinel in CHOOSE_SENTINELS:
        assert sentinel not in text, f"fixture still contains CHOOSE_ sentinel {sentinel!r}"

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
