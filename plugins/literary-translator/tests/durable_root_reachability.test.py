"""tests/durable_root_reachability.test.py -- Step 0a's ownership-marker /
adoption logic, plus every copied script's self-anchoring guarantee.

Two genuinely different mechanisms live in this one file because SKILL.md's
own spec groups them together (both are about "can a script reliably find
its real durable_root, and can Step 0a tell a safe adoption from a real
conflict"):

  (A) Step 0a's four-outcome ownership check (SKILL.md, "Step 0a -- Create
      durable root; install scripts/languages/schemas; ownership marker"):
      fresh adoption / resumed-matching-root / ambiguous adoption-prompt
      (with per-file collision enumeration) / unconditional fatal conflict.

      IMPORTANT, verified against this repo's actual current state (not
      assumed): Step 0a has NO standalone shipped script under
      assets/scripts/. SKILL.md names exactly FOUR plugin-path script
      invocations in the whole document ("Implemented by scripts/
      profile_validate.py..." at Step 0, validate_extraction.py at W2,
      glossary_preflight.py at W3 (1.4.0), and resolve_codex_companion.py at
      W5 (1.4.7)), none of which is Step 0a's
      own copy/ownership logic; profile_validate.py's own
      real main() deliberately stops at "Step 0 validation passed" and
      never touches MANAGED_ENTRIES/ownership markers (see its
      check_durable_root(), which only checks durable_root's PARENT), and
      the only two real scripts that mention
      ``.literary-translator-root.json`` at all -- cache_key.py's
      load_owner_marker() and validate_draft.py's load_profile() -- only
      ever READ a marker Step 0a is supposed to have already written;
      neither CREATES it. This is the identical, independently-reached
      conclusion tests/scaffold_idempotency.test.py's own module docstring
      documents for Step 0a's one-time template copy (see its Part B) --
      Step 0a's copy/scaffold/ownership-check logic is orchestrating-session
      prose the calling Claude session executes directly at scaffold time,
      not an importable/subprocess-able module.

      Consequently, this half of the suite (1) transcribes SKILL.md's
      documented four-outcome algorithm literally as a small reference
      implementation (``run_step0a`` below, mirroring the exact same idiom
      ``scaffold_idempotency.test.py`` uses for Step 0a's template-copy
      half), (2) exercises it against constructed fixtures covering every
      case the spec enumerates, and (3) cross-checks the result against a
      REAL consumer script's real function (cache_key.py's
      ``load_owner_marker``) wherever the two halves meet, so a marker
      shape this reference implementation gets right but the real plugin's
      own downstream reader would reject is still caught here, not silently
      assumed compatible.

  (B) Self-anchoring (``Path(__file__).resolve().parents[1]``): a REAL,
      currently-shipped script (``draft_sha1.py`` -- tiny, dependency-free)
      copied into TWO separate constructed durable_root fixtures and
      invoked via subprocess from a THIRD, unrelated cwd, proving each
      copy resolves its OWN durable_root from its own on-disk location --
      never cwd, never a shared/global path -- exactly as documented in
      every copied script's own module docstring.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"

CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
SELF_ANCHOR_SCRIPT_SRC = SCRIPTS_DIR / "draft_sha1.py"

assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"
assert CACHE_KEY_SCRIPT.is_file(), f"cache_key.py not found at {CACHE_KEY_SCRIPT}"
assert SELF_ANCHOR_SCRIPT_SRC.is_file(), f"draft_sha1.py not found at {SELF_ANCHOR_SCRIPT_SRC}"

# Exact constants from SKILL.md's Step 0a section.
MANAGED_DIRS = [
    "scripts", "languages", "schemas", "segments",
    "glossary", "verses", "runs", "out",
]
ROOT_MARKER_NAME = ".literary-translator-root.json"
DIR_MARKER_NAME = ".literary-translator-managed"
FIXED_SKELETON = [
    "segments", "glossary", "verses", "runs", "runs/ledger.d",
    "runs/workflows", "scripts", "languages", "schemas", "out",
]

# Sanity: the two constants this whole file hinges on are literally present
# in SKILL.md's Step 0a section -- if a future SKILL.md edit renames either
# marker, this trips before any fixture-based test below gets a chance to
# silently test the WRONG filename.
_SKILL_TEXT = SKILL_MD.read_text(encoding="utf-8")
assert ROOT_MARKER_NAME in _SKILL_TEXT, (
    f"expected {ROOT_MARKER_NAME!r} in SKILL.md's Step 0a section"
)
assert DIR_MARKER_NAME in _SKILL_TEXT, (
    f"expected {DIR_MARKER_NAME!r} in SKILL.md's Step 0a section"
)
for _name in MANAGED_DIRS:
    assert f"`{_name}/`" in _SKILL_TEXT or f"``{_name}/``" in _SKILL_TEXT, (
        f"expected MANAGED_ENTRIES name {_name!r} in SKILL.md's Step 0a section"
    )


# The four plugin-path scripts Step 0a NEVER copies into
# ${durable_root}/scripts/ (each runs only from the plugin's own install path)
# -- so the collision enumeration must not treat any of them as a
# shipped-into-scripts name. See SKILL.md's Step 0a copy-exclusion list and
# profile_validate.py's own module docstring. (1.4.7 added
# resolve_codex_companion.py, the W5 codex-companion path resolver.)
NEVER_COPIED_SCRIPTS = frozenset({
    "profile_validate.py",
    "validate_extraction.py",
    "glossary_preflight.py",
    "resolve_codex_companion.py",
})


def _shipped_filenames(managed_dir_name: str) -> list[str]:
    """The exact shipped filenames Step 0a's collision-enumeration check
    stats against an adopted directory's existing contents, read from THIS
    repo's real assets/ subdirectories (never a hardcoded, could-drift
    list) -- see SKILL.md: 'stat every assets/scripts/*.py/assets/
    schemas/*.json/assets/languages/*.json shipped name'. The
    NEVER_COPIED_SCRIPTS are excluded because Step 0a does not copy them into
    scripts/, so a foreign copy of one is not a collision with anything Step
    0a writes."""
    if managed_dir_name == "scripts":
        return sorted(
            p.name
            for p in (ASSETS_DIR / "scripts").glob("*.py")
            if p.name not in NEVER_COPIED_SCRIPTS
        )
    if managed_dir_name == "schemas":
        return sorted(p.name for p in (ASSETS_DIR / "schemas").glob("*.json"))
    if managed_dir_name == "languages":
        return sorted(p.name for p in (ASSETS_DIR / "languages").glob("*.json"))
    return []


def _collisions_for(existing_dir: Path, managed_dir_name: str) -> list[str]:
    shipped = _shipped_filenames(managed_dir_name)
    if not shipped or not existing_dir.is_dir():
        return []
    existing_names = {p.name for p in existing_dir.iterdir()}
    return sorted(n for n in shipped if n in existing_names)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Step0aResult:
    """``exit_code`` is this reference implementation's own invented
    convention (0=clean pass, 1=fatal conflict, 2=adoption halt) -- a real
    future script need not literally reuse these numbers, but MUST keep the
    fatal-halt path and the adoption-halt path distinguishable from each
    other and from a clean pass, which is the load-bearing invariant this
    file's tests actually pin (see e.g.
    test_unrelated_unmarked_segments_dir_triggers_adoption_prompt_not_fatal's
    assertion that it is never mistaken for the fatal path)."""
    outcome: str  # "fresh" | "resumed" | "adoption_prompt" | "fatal"
    exit_code: int
    message: str
    collisions: dict = field(default_factory=dict)


def _scaffold(
    durable_root: Path,
    owner_profile_path: str,
    output_destination: "Path | None",
    smoke_report_path: "str | None",
    *,
    preserve_existing_root_marker: bool,
) -> None:
    """The mutating half of a clean pass (fresh / resumed / adopted):
    create the fixed skeleton, backfill any missing per-directory marker,
    write (or preserve) the root marker, and create the two documented
    non-default parent directories when they resolve inside durable_root."""
    durable_root.mkdir(parents=True, exist_ok=True)
    for rel in FIXED_SKELETON:
        (durable_root / rel).mkdir(parents=True, exist_ok=True)
    for name in MANAGED_DIRS:
        marker = durable_root / name / DIR_MARKER_NAME
        if not marker.is_file():
            marker.write_text("{}\n", encoding="utf-8")

    root_marker = durable_root / ROOT_MARKER_NAME
    if not (preserve_existing_root_marker and root_marker.is_file()):
        payload = {"owner_profile_path": owner_profile_path, "created_at": _now_iso()}
        root_marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if output_destination is not None:
        resolved_root = durable_root.resolve()
        resolved_dest = Path(str(output_destination)).resolve() if durable_root.exists() else None
        try:
            (durable_root.resolve() / "x").relative_to(resolved_root)  # keeps resolved_root defined
            output_destination.resolve().relative_to(resolved_root)
            output_destination.mkdir(parents=True, exist_ok=True)
        except ValueError:
            pass  # resolves outside durable_root -- not Step 0a's job (see profile_validate.py)

    if smoke_report_path is not None:
        (durable_root / smoke_report_path).parent.mkdir(parents=True, exist_ok=True)


def run_step0a(
    durable_root: Path,
    profile_path: Path,
    *,
    adopt_existing: bool = False,
    output_destination: "Path | None" = None,
    smoke_report_path: "str | None" = None,
) -> Step0aResult:
    """Literal transcription of SKILL.md's Step 0a four-outcome ownership
    check, in the exact priority order the spec states ('Four outcomes, in
    this exact order'), with one necessary disambiguation the prose leaves
    implicit: a root marker that names a DIFFERENT owner is fatal
    regardless of whether any managed directory also happens to carry its
    own per-directory marker (SKILL.md's own required test case -- 'a root
    marker for a DIFFERENT profile path asserts fatal' -- is unconditional
    on that point; treating a wrong-owner root marker as merely
    'ambiguous, safe to adopt' whenever no directory marker also happens to
    be present would be a real safety regression, silently adopting into
    a DIFFERENT project's own durable_root).
    """
    owner_profile_path = str(profile_path)
    root_marker_path = durable_root / ROOT_MARKER_NAME

    if root_marker_path.is_file():
        try:
            existing_marker = json.loads(root_marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return Step0aResult(
                "fatal", 1,
                f"{durable_root}: FATAL -- unreadable/corrupt {ROOT_MARKER_NAME}: {exc}",
            )
        existing_owner = existing_marker.get("owner_profile_path")
        if existing_owner == owner_profile_path:
            _scaffold(
                durable_root, owner_profile_path, output_destination, smoke_report_path,
                preserve_existing_root_marker=True,
            )
            return Step0aResult(
                "resumed", 0,
                f"{durable_root}: OK -- resumed project, ownership confirmed",
            )
        return Step0aResult(
            "fatal", 1,
            f"{durable_root}: FATAL -- {ROOT_MARKER_NAME} claimed by a different "
            f"project ({existing_owner})",
        )

    existing_dirs = [name for name in MANAGED_DIRS if (durable_root / name).is_dir()]
    dirs_with_own_marker = [
        name for name in existing_dirs if (durable_root / name / DIR_MARKER_NAME).is_file()
    ]

    if dirs_with_own_marker:
        return Step0aResult(
            "fatal", 1,
            f"{durable_root}: FATAL -- no ownership marker found at "
            f"{root_marker_path}, but {', '.join(dirs_with_own_marker)} already "
            f"carries its own {DIR_MARKER_NAME} (real prior plugin involvement)",
        )

    if existing_dirs and not adopt_existing:
        collisions = {name: _collisions_for(durable_root / name, name) for name in existing_dirs}
        lines = [
            f"{durable_root}: ADOPTION PROMPT -- pre-existing managed director"
            f"{'y' if len(existing_dirs) == 1 else 'ies'} found with no ownership "
            f"marker: {', '.join(existing_dirs)}",
        ]
        for name in existing_dirs:
            if collisions[name]:
                lines.append(f"  {name}/: collisions -- {', '.join(collisions[name])}")
            else:
                lines.append(f"  {name}/: no shipped-filename collisions found")
        lines.append(
            "Set project.durable_root_adopt_existing: true and re-run to proceed, "
            "or repoint durable_root if unsafe."
        )
        return Step0aResult("adoption_prompt", 2, "\n".join(lines), collisions=collisions)

    # Either nothing managed exists yet (fresh), or the caller has already
    # confirmed adoption via durable_root_adopt_existing: true.
    _scaffold(
        durable_root, owner_profile_path, output_destination, smoke_report_path,
        preserve_existing_root_marker=False,
    )
    outcome = "fresh" if not existing_dirs else "resumed"
    return Step0aResult(outcome, 0, f"{durable_root}: OK -- {outcome} durable_root ready")


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_profile_path(tmp_path: Path) -> Path:
    """A profile.yml's exact CONTENT is irrelevant to Step 0a's ownership
    check (which only ever records/compares this PATH, as a string, inside
    the root marker) -- so this fixture just needs a real file to exist at
    a real path, mirroring how every other test file in this suite still
    builds a genuinely schema-valid profile for anything that DOES parse
    it. Placed under its own .claude/literary-translator/ subdirectory,
    matching the real project layout, distinct from durable_root by
    default (individual tests override this when durable_root == project
    root is exactly the case under test)."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("profile_version: 1  # fixture placeholder\n", encoding="utf-8")
    return profile_path


def _assert_full_skeleton_and_markers(durable_root: Path, profile_path: Path) -> None:
    for rel in FIXED_SKELETON:
        assert (durable_root / rel).is_dir(), f"expected {rel}/ to exist under {durable_root}"
    for name in MANAGED_DIRS:
        marker = durable_root / name / DIR_MARKER_NAME
        assert marker.is_file(), f"expected per-directory marker at {marker}"
    root_marker = durable_root / ROOT_MARKER_NAME
    assert root_marker.is_file(), f"expected root marker at {root_marker}"
    payload = json.loads(root_marker.read_text(encoding="utf-8"))
    assert payload["owner_profile_path"] == str(profile_path)
    assert "created_at" in payload and payload["created_at"]


# ---------------------------------------------------------------------------
# (A) Step 0a ownership / adoption logic
# ---------------------------------------------------------------------------

def test_skill_lists_resolve_codex_companion_as_fourth_plugin_path_script():
    """1.4.7: SKILL.md's copy-exclusion sweep now names FOUR plugin-path
    scripts never copied to durable_root -- resolve_codex_companion.py (the W5
    codex-companion path resolver) joins profile_validate.py,
    validate_extraction.py, and glossary_preflight.py. Guards the count from
    silently drifting back to three when the sweep is next touched."""
    assert "resolve_codex_companion.py" in _SKILL_TEXT
    assert "four plugin-path scripts never copied" in _SKILL_TEXT


def test_fresh_empty_durable_root_passes_and_marks(tmp_path):
    durable_root = tmp_path / "book_project"  # deliberately not created yet
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "fresh"
    assert result.exit_code == 0
    _assert_full_skeleton_and_markers(durable_root, profile_path)


def test_durable_root_equals_project_root_passes_and_marks(tmp_path):
    """durable_root coinciding with the project's own root is an explicitly
    supported config (SKILL.md: '.claude/, book source files, .git/, README
    is ignored for this check') -- only profile.yml's own .claude/ ancestry
    exists inside durable_root here, no MANAGED_ENTRIES name at all."""
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    profile_path = durable_root / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("profile_version: 1  # fixture placeholder\n", encoding="utf-8")

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "fresh"
    assert result.exit_code == 0
    _assert_full_skeleton_and_markers(durable_root, profile_path)
    assert profile_path.is_file(), "the project's own .claude/ tree must be left alone"


def test_durable_root_with_unrelated_content_passes_and_marks(tmp_path):
    """Book source files, a README, a .git/ directory -- none of these are
    MANAGED_ENTRIES names, so their mere presence must not trip the
    ambiguous-adoption path; this is still a fresh adoption."""
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    (durable_root / "book.epub").write_bytes(b"fake epub bytes for the fixture")
    (durable_root / "README.md").write_text("# Some project readme\n", encoding="utf-8")
    (durable_root / ".git").mkdir()
    (durable_root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "fresh"
    assert result.exit_code == 0
    _assert_full_skeleton_and_markers(durable_root, profile_path)
    assert (durable_root / "book.epub").is_file()
    assert (durable_root / "README.md").is_file()
    assert (durable_root / ".git" / "HEAD").is_file()


def test_resumed_matching_root_passes_unchanged_and_backfills_missing_markers(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    profile_path = _make_profile_path(tmp_path)

    created_at = "2025-01-01T00:00:00+00:00"
    (durable_root / ROOT_MARKER_NAME).write_text(
        json.dumps({"owner_profile_path": str(profile_path), "created_at": created_at}),
        encoding="utf-8",
    )
    # Two managed dirs already exist from an OLDER prior run -- before
    # per-directory markers existed -- deliberately without their own marker.
    (durable_root / "segments").mkdir()
    (durable_root / "segments" / "seg001.draft.json").write_text('{"kept": true}', encoding="utf-8")
    (durable_root / "scripts").mkdir()
    assert not (durable_root / "segments" / DIR_MARKER_NAME).exists()
    assert not (durable_root / "scripts" / DIR_MARKER_NAME).exists()

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "resumed"
    assert result.exit_code == 0
    _assert_full_skeleton_and_markers(durable_root, profile_path)
    # The root marker's own identity survives a resume unchanged -- a
    # resumed run CONFIRMS ownership, it does not rewrite the marker.
    payload = json.loads((durable_root / ROOT_MARKER_NAME).read_text(encoding="utf-8"))
    assert payload["owner_profile_path"] == str(profile_path)
    assert payload["created_at"] == created_at
    assert (durable_root / "segments" / "seg001.draft.json").read_text(encoding="utf-8") == '{"kept": true}'


def test_unrelated_unmarked_segments_dir_triggers_adoption_prompt_not_fatal(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    (durable_root / "segments").mkdir()
    (durable_root / "segments" / "my_own_notes.txt").write_text("unrelated notes\n", encoding="utf-8")
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "adoption_prompt", (
        f"a pre-existing, unmarked managed-directory NAME with no root marker "
        f"must NOT be treated as a fatal conflict -- got outcome={result.outcome!r}, "
        f"message:\n{result.message}"
    )
    assert result.exit_code != 0
    assert "ADOPTION" in result.message
    assert "segments" in result.message
    # A halt must be a pure halt -- no partial mutation of durable_root.
    assert not (durable_root / ROOT_MARKER_NAME).exists()
    for name in MANAGED_DIRS:
        if name != "segments":
            assert not (durable_root / name).exists(), f"{name}/ must not be created by a halted run"
    assert not (durable_root / "segments" / DIR_MARKER_NAME).exists()
    assert (durable_root / "segments" / "my_own_notes.txt").is_file(), "pre-existing content must survive a halt"


def test_same_segments_fixture_with_adopt_existing_true_passes_and_marks_both_levels(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    (durable_root / "segments").mkdir()
    (durable_root / "segments" / "my_own_notes.txt").write_text("unrelated notes\n", encoding="utf-8")
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path, adopt_existing=True)

    assert result.exit_code == 0, result.message
    assert result.outcome != "adoption_prompt" and result.outcome != "fatal"
    _assert_full_skeleton_and_markers(durable_root, profile_path)
    assert (durable_root / "segments" / "my_own_notes.txt").is_file(), (
        "pre-existing unrelated content in an adopted directory must survive"
    )


def test_managed_dir_with_own_marker_but_no_root_marker_is_original_unconditional_fatal(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / DIR_MARKER_NAME).write_text("{}\n", encoding="utf-8")
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "fatal", (
        f"real prior plugin involvement (a directory already carrying its "
        f"OWN marker) with NO root marker must be the original unconditional "
        f"fatal halt, never the adoption prompt -- got {result.outcome!r}: {result.message}"
    )
    assert result.exit_code != 0
    assert "no ownership marker found" in result.message
    assert str(scripts_dir) in result.message or "scripts" in result.message


def test_root_marker_for_different_profile_path_is_fatal_naming_conflicting_owner(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    other_owner = str(tmp_path / "some_other_project" / ".claude" / "literary-translator" / "profile.yml")
    (durable_root / ROOT_MARKER_NAME).write_text(
        json.dumps({"owner_profile_path": other_owner, "created_at": "2025-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "fatal"
    assert result.exit_code != 0
    assert other_owner in result.message, result.message
    assert "different project" in result.message


def test_unrelated_scripts_dir_with_validate_draft_py_names_it_as_a_collision(tmp_path):
    shipped_scripts = _shipped_filenames("scripts")
    assert "validate_draft.py" in shipped_scripts, (
        "sanity: this fixture assumes validate_draft.py genuinely ships in "
        "assets/scripts/ -- re-derive the fixture filename if that ever changes"
    )

    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "validate_draft.py").write_text(
        "# not the real plugin file -- just a name collision for this fixture\n",
        encoding="utf-8",
    )
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "adoption_prompt"
    assert "validate_draft.py" in result.message, result.message
    assert result.collisions["scripts"] == ["validate_draft.py"]


def test_unrelated_scripts_dir_with_no_shipped_filenames_states_no_collisions(tmp_path):
    durable_root = tmp_path / "book_project"
    durable_root.mkdir()
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "my_own_reading_notes.txt").write_text("nothing that ships\n", encoding="utf-8")
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)

    assert result.outcome == "adoption_prompt"
    assert "no shipped-filename collisions found" in result.message, result.message
    assert result.collisions["scripts"] == []
    assert "my_own_reading_notes.txt" not in result.message


def test_non_default_output_destination_creates_needed_parent_directory(tmp_path):
    durable_root = tmp_path / "book_project"  # fresh
    profile_path = _make_profile_path(tmp_path)
    destination = durable_root / "exports" / "final"

    result = run_step0a(durable_root, profile_path, output_destination=destination)

    assert result.exit_code == 0, result.message
    assert destination.is_dir(), f"expected {destination} to be created by Step 0a"


def test_smoke_test_report_path_creates_needed_parent_directory(tmp_path):
    durable_root = tmp_path / "book_project"  # fresh
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path, smoke_report_path="runs/custom/report.json")

    assert result.exit_code == 0, result.message
    assert (durable_root / "runs" / "custom").is_dir()


def test_root_marker_shape_is_accepted_by_the_real_cache_key_load_owner_marker(tmp_path):
    """Cross-check against REAL, currently-shipped consumer code (not just
    this file's own reference transcription): cache_key.py's
    load_owner_marker() is the actual function every per-segment script
    downstream of Step 0a relies on to resolve profile.yml. If this file's
    marker-writing logic ever drifted from the shape that function expects
    (e.g. a renamed key), this test -- not just an internal self-consistency
    check -- would catch it."""
    import importlib.util

    durable_root = tmp_path / "book_project"
    profile_path = _make_profile_path(tmp_path)

    result = run_step0a(durable_root, profile_path)
    assert result.exit_code == 0, result.message

    spec = importlib.util.spec_from_file_location("cache_key_under_test", CACHE_KEY_SCRIPT)
    assert spec is not None and spec.loader is not None, f"could not load spec for {CACHE_KEY_SCRIPT}"
    cache_key = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cache_key)

    marker = cache_key.load_owner_marker(durable_root)
    assert marker["owner_profile_path"] == str(profile_path)


# ---------------------------------------------------------------------------
# (B) Self-anchoring: a representative REAL script, invoked via subprocess
# from a cwd that is neither durable_root nor the script's own directory.
# ---------------------------------------------------------------------------

def _make_self_anchor_fixture(tmp_path: Path, name: str, draft_bytes: bytes) -> Path:
    durable_root = tmp_path / name
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SELF_ANCHOR_SCRIPT_SRC, scripts_dir / "draft_sha1.py")
    segments_dir = durable_root / "segments"
    segments_dir.mkdir()
    (segments_dir / "testseg.draft.json").write_bytes(draft_bytes)
    return durable_root


def _canonical_draft_sha1(draft_bytes: bytes) -> str:
    """Independent, stdlib-only ground truth for draft_sha1.py's 1.2.0
    content-hash algorithm (CONTRACT-1.2.0-reliability.md section 2):
    parse as JSON, drop 'dispatch_token' if present, sha1 the sorted-key
    canonical re-serialization -- see tests/draft_sha1.test.py's own
    canonical_expected_sha1() for the authoritative, more-exhaustively-
    tested copy of this same small algorithm; duplicated here (not
    imported) so this file stays self-contained like every sibling test
    file in this directory."""
    doc = json.loads(draft_bytes)
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def test_representative_script_self_anchors_regardless_of_invocation_cwd(tmp_path):
    """draft_sha1.py -- tiny, dependency-free, self-anchors via
    Path(__file__).resolve().parents[1] (see its own module docstring).
    Two SEPARATE durable_root fixtures, each with its own copy of the real
    script and its own distinct draft content, both invoked from a THIRD,
    unrelated cwd -- proves each copy resolves durable_root from its OWN
    on-disk location, never cwd and never some fixed/shared path (each
    fixture must independently produce its OWN correct, DIFFERENT hash).
    1.2.0: draft_sha1.py now hashes canonicalized CONTENT (dispatch_token
    excluded), not raw on-disk bytes -- see _canonical_draft_sha1() above."""
    root_a = _make_self_anchor_fixture(tmp_path, "project_a", b'{"paragraphs": ["fixture A content"]}')
    root_b = _make_self_anchor_fixture(tmp_path, "project_b", b'{"paragraphs": ["fixture B, deliberately different bytes"]}')

    foreign_cwd = tmp_path / "somewhere_else_entirely"
    foreign_cwd.mkdir()
    assert foreign_cwd not in (root_a, root_b, root_a / "scripts", root_b / "scripts")

    expected_a = _canonical_draft_sha1((root_a / "segments" / "testseg.draft.json").read_bytes())
    expected_b = _canonical_draft_sha1((root_b / "segments" / "testseg.draft.json").read_bytes())
    assert expected_a != expected_b, "sanity: the two fixtures must be genuinely distinct"

    result_a = subprocess.run(
        [sys.executable, str(root_a / "scripts" / "draft_sha1.py"), "testseg"],
        cwd=str(foreign_cwd), capture_output=True, text=True, timeout=30,
    )
    result_b = subprocess.run(
        [sys.executable, str(root_b / "scripts" / "draft_sha1.py"), "testseg"],
        cwd=str(foreign_cwd), capture_output=True, text=True, timeout=30,
    )

    assert result_a.returncode == 0, f"stdout:\n{result_a.stdout}\nstderr:\n{result_a.stderr}"
    assert result_b.returncode == 0, f"stdout:\n{result_b.stdout}\nstderr:\n{result_b.stderr}"
    assert result_a.stdout.strip() == expected_a
    assert result_b.stdout.strip() == expected_b
