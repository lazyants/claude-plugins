"""tests/prompt_contract_drift.test.py

Targets ``profile_validate.py``'s Step 12 resumed-project
``PROMPT_CONTRACT_VERSION`` marker check on ``translate_TASK.md`` /
``review_TASK.md`` / ``glossary_TASK.md`` (see profile_validate.py's own
module docstring, step 12, and ``check_contract_marker()``).

Four malformed marker states, each asserting a named, non-zero-exit halt:

  - missing marker (treated as version 0, always stale)
  - malformed, non-integer marker value
  - duplicated marker with two CONFLICTING values
  - non-leading marker (present, but not the file's first non-blank line)

Exercises the real CLI entry point (``main()``) against a fully
schema-valid ``profile.yml`` + ``durable_root`` fixture -- not the internal
``check_contract_marker`` / ``check_resumed_contract_versions`` helpers
directly -- so a regression that breaks the wiring between Step 5 (schema
validation) and Step 12 (the contract-marker check) would also be caught
here, not just a unit-level regression in the marker parser itself.

Two extra edge cases are locked alongside the four states because they are
named in the same spec sentence and are the most likely place a "tighten
the check" regression would overshoot: a duplicated marker where BOTH
occurrences AGREE is explicitly NOT fatal (only conflicting values are),
and a marker preceded only by blank lines still counts as "leading". A
final case confirms a brand-new project with no *_TASK.md files yet (not
resumed) is never mistaken for the "missing marker" violation.
"""
import importlib.util
import re
import textwrap
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "literary-translator" / "assets" / "scripts" / "profile_validate.py"
)

TASK_FILENAMES = ("translate_TASK.md", "review_TASK.md", "glossary_TASK.md")


def _load_profile_validate():
    """Imports profile_validate.py fresh from its real, shipped install
    path -- ``assets/scripts/`` is not a package on sys.path, so a plain
    ``import`` won't reach it. A fresh module per call (see the ``pv``
    fixture below) avoids any cross-test state leakage through the
    module-level ``yaml``/``jsonschema`` handles ``dependency_preflight()``
    populates."""
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def pv():
    assert SCRIPT_PATH.is_file(), f"expected profile_validate.py at {SCRIPT_PATH}"
    return _load_profile_validate()


def _well_formed_marker(version: int) -> str:
    return f"<!-- PROMPT_CONTRACT_VERSION: {version} -->\n"


def _write_valid_profile(tmp_path: Path) -> tuple[Path, Path]:
    """Builds a fully schema-valid, placeholder-free profile.yml (same
    recipe as tests/profile_example_validation.test.py's clean case:
    assets/profile.example.yml with every placeholder replaced by a real
    value) plus its prerequisite filesystem fixtures -- an existing
    source file, and a durable_root whose parent exists and is writable.
    pytest's own tmp_path resolves under a literal `tmp` path component
    on Linux (e.g. CI runners honoring `TMPDIR=/tmp`), so any caller
    expecting Step 0's exit-0 pass must set
    `LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT=1` (see profile_validate.py's
    `check_durable_root`) -- callers that only assert a halt don't need
    it. Returns (profile_path, durable_root)."""
    durable_root = tmp_path / "durable"
    durable_root.mkdir()
    source_file = tmp_path / "source.epub"
    source_file.write_bytes(b"fake epub bytes for the fixture")

    profile_path = tmp_path / "profile.yml"
    profile_text = textwrap.dedent(f"""\
        profile_version: 1
        project:
          title: "A Real Test Book"
          durable_root: "{durable_root}"
          durable_root_adopt_existing: false
          pipeline_version: v1
          max_segment_words: 15000
        source:
          format: gutenberg_epub
          path: "{source_file}"
          gutenberg_id: null
          language:
            code: fr
            particle_config: "fr.json"
            smoke_test:
              report_path: null
          adapter_config:
            gutenberg_epub: {{}}
            plain_text:
              segmentation:
                method: blank_line_run
                blank_line_threshold: 2
                heading_regex: null
              verse_detection: none_confirmed
              verse_regex: null
              footnotes: none_confirmed
              footnote_anchor_regex: null
              footnote_def_regex: null
            custom:
              extractor_path: null
        target:
          language:
            code: ru
            register_notes: "no register axis notes needed for this fixture"
        verse_policy:
          mode: literal_only
          threshold_lines: null
        engine:
          effort: high
          max_fix_rounds: 4
          batch_agent_cap: 1000
        footnotes:
          apparatus_policy: translate_all
        glossary:
          research_mode: offline
        validation:
          untranslated_sentinel: "NOT TRANSLATED YET"
        output:
          v1_scope: segment_drafts_and_audit
          destination: "{durable_root / 'out'}"
        """)
    profile_path.write_text(profile_text, encoding="utf-8")
    return profile_path, durable_root


def _run_main(pv_module, profile_path: Path, capsys):
    """Invokes the real CLI entry point exactly as the plugin does
    (``profile_validate.py --profile <path>``) and returns
    ``(exit_code, stdout, stderr)``. ``main()`` always ends in
    ``sys.exit()``, never a bare return, so every call site must catch
    ``SystemExit``."""
    with pytest.raises(SystemExit) as exc_info:
        pv_module.main(["--profile", str(profile_path)])
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


def _seed_other_task_files(durable_root: Path, current_version: int, skip: str) -> None:
    """Writes a well-formed marker into every RESUMED_PROMPT_CONTRACT
    filename except `skip` -- so whichever assertion follows can only be
    caused by the one file under test, never incidental noise from a
    sibling file that was never written at all (unwritten files are
    silently skipped by check_contract_marker, not a violation -- but
    seeding them well-formed additionally proves the check doesn't
    false-positive on a clean neighbor)."""
    for filename in TASK_FILENAMES:
        if filename == skip:
            continue
        (durable_root / filename).write_text(
            _well_formed_marker(current_version) + "Ordinary task body text.\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Baseline sanity: well-formed markers on every resumed-project file pass
# clean -- proves the fixture construction itself, not the marker check, is
# innocent before any halt is asserted below.
# ---------------------------------------------------------------------------

def test_well_formed_markers_pass_clean(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    for filename in TASK_FILENAMES:
        (durable_root / filename).write_text(
            _well_formed_marker(pv.CURRENT_PROMPT_CONTRACT_VERSION) + "Some task body text.\n",
            encoding="utf-8",
        )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"expected a clean exit, got {exit_code!r}; stderr:\n{err}"


def test_no_task_files_is_not_a_resumed_project_violation(pv, tmp_path, monkeypatch, capsys):
    """A genuinely fresh project (no *_TASK.md files exist yet at all) must
    not be mistaken for the 'missing marker' violation -- check_contract_marker
    only fires once the file exists (`if not path.is_file(): return []`)."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, _durable_root = _write_valid_profile(tmp_path)

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"a fresh project with no TASK.md files should pass; stderr:\n{err}"


# ---------------------------------------------------------------------------
# The four malformed marker states, applied to each of the three
# resumed-project files in turn.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", TASK_FILENAMES)
def test_missing_marker_halts(pv, tmp_path, capsys, filename):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    _seed_other_task_files(durable_root, pv.CURRENT_PROMPT_CONTRACT_VERSION, skip=filename)
    (durable_root / filename).write_text(
        "No marker here at all, just ordinary prose.\n", encoding="utf-8"
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a missing PROMPT_CONTRACT_VERSION marker must be a fatal halt"
    assert str(durable_root / filename) in err, err
    assert "no leading PROMPT_CONTRACT_VERSION marker found" in err, err
    assert "treated as" in err and "version 0" in err, err


@pytest.mark.parametrize("filename", TASK_FILENAMES)
def test_malformed_non_integer_marker_halts(pv, tmp_path, capsys, filename):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    _seed_other_task_files(durable_root, pv.CURRENT_PROMPT_CONTRACT_VERSION, skip=filename)
    (durable_root / filename).write_text(
        "<!-- PROMPT_CONTRACT_VERSION: latest -->\nOrdinary task body.\n",
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a non-integer marker value must be a fatal halt"
    assert str(durable_root / filename) in err, err
    assert "malformed, non-integer value" in err, err
    assert "'latest'" in err, err


@pytest.mark.parametrize("filename", TASK_FILENAMES)
def test_duplicated_marker_conflicting_values_halts(pv, tmp_path, capsys, filename):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_PROMPT_CONTRACT_VERSION
    _seed_other_task_files(durable_root, current, skip=filename)
    (durable_root / filename).write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {current} -->\n"
        "Some body text in between the two markers.\n"
        f"<!-- PROMPT_CONTRACT_VERSION: {current + 1} -->\n",
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "two conflicting marker values in one file must be a fatal halt"
    assert str(durable_root / filename) in err, err
    assert "duplicated PROMPT_CONTRACT_VERSION marker" in err, err
    assert "conflicting values" in err, err


@pytest.mark.parametrize("filename", TASK_FILENAMES)
def test_non_leading_marker_halts(pv, tmp_path, capsys, filename):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_PROMPT_CONTRACT_VERSION
    _seed_other_task_files(durable_root, current, skip=filename)
    (durable_root / filename).write_text(
        "# Some heading that precedes the marker\n"
        f"<!-- PROMPT_CONTRACT_VERSION: {current} -->\n",
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a marker that isn't the file's first non-blank line must halt"
    assert str(durable_root / filename) in err, err
    assert "not the file's first non-blank line" in err, err


# ---------------------------------------------------------------------------
# Edge cases named alongside the four states in the same spec sentence --
# regression-locks against an over-eager fix that would turn these into
# false positives.
# ---------------------------------------------------------------------------

def test_duplicated_marker_with_identical_values_is_not_fatal(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_PROMPT_CONTRACT_VERSION
    filename = "translate_TASK.md"
    _seed_other_task_files(durable_root, current, skip=filename)
    (durable_root / filename).write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {current} -->\n"
        "Some body text.\n"
        f"<!-- PROMPT_CONTRACT_VERSION: {current} -->\n",
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a duplicated marker where both occurrences AGREE must not halt; stderr:\n{err}"
    )


def test_marker_after_leading_blank_lines_is_still_leading(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_PROMPT_CONTRACT_VERSION
    filename = "review_TASK.md"
    _seed_other_task_files(durable_root, current, skip=filename)
    (durable_root / filename).write_text(
        "\n\n" + _well_formed_marker(current) + "Body.\n", encoding="utf-8"
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a marker on the file's first NON-BLANK line should pass, blank lines "
        f"before it notwithstanding; stderr:\n{err}"
    )


# ===========================================================================
# CANON_MAP PROMPT CONTRACT (#130) -- unrelated to the PROMPT_CONTRACT_VERSION
# marker mechanism above (a distinct "prompt contract" concern sharing this
# file only by name convention, the same appended-unrelated-section
# convention tests/batch_size_estimator.test.py already uses for its own
# glossary-pass section below the mass-translate one):
#
# Locks that mass-translate-wf.template.js's translatePrompt and
# reviewDispatchPrompt actually reference the segpack's canon_map field and
# the declined-stem enforcement rule (CONTRACT §4) instead of the old "Use
# canon_names forms verbatim, no exceptions" wording -- which would make the
# reviewer flag a correctly inflected/declined form as wrong, causing chronic
# non-convergence (the exact failure #130 fixes). Pure string-level checks
# on the REAL, shipped prompt-builder function bodies -- no Node dependency:
# extracts each function's body via review_prompt_schema_drift.test.py's
# brace-matching helper (house style, via importlib -- see
# tests/agent_schema_top_level_object.test.py's identical reuse), the same
# technique tests/transient_failure_recoverable.test.py uses for its own
# #131 structural locks.
# ===========================================================================

MASS_TRANSLATE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "literary-translator" / "assets" / "templates" / "mass-translate-wf.template.js"
)

_DRIFT_TEST_PATH_FOR_CANON_MAP = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_TEST_PATH_FOR_CANON_MAP.is_file(), f"expected sibling test file not found: {_DRIFT_TEST_PATH_FOR_CANON_MAP}"

_drift_spec_for_canon_map = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_prompt_contract_drift", _DRIFT_TEST_PATH_FOR_CANON_MAP
)
assert _drift_spec_for_canon_map is not None and _drift_spec_for_canon_map.loader is not None, (
    f"could not load spec for {_DRIFT_TEST_PATH_FOR_CANON_MAP}"
)
_drift_for_canon_map = importlib.util.module_from_spec(_drift_spec_for_canon_map)
_drift_spec_for_canon_map.loader.exec_module(_drift_for_canon_map)

_find_balanced_brace_span_for_canon_map = _drift_for_canon_map._find_balanced_brace_span


def _extract_prompt_function_body(source: str, function_name: str) -> str:
    """Locates ``function <function_name>(...) {`` (a plain, non-async
    function -- both translatePrompt and reviewDispatchPrompt are) and
    returns the text strictly BETWEEN its outer braces."""
    m = re.search(r"function\s+" + re.escape(function_name) + r"\s*\([^)]*\)\s*", source)
    assert m, f"expected 'function {function_name}(...)' in {MASS_TRANSLATE_TEMPLATE_PATH}"
    brace_start = source.index("{", m.end())
    brace_end = _find_balanced_brace_span_for_canon_map(source, brace_start)
    return source[brace_start + 1 : brace_end - 1]


@pytest.fixture(scope="module")
def mass_translate_js_source() -> str:
    assert MASS_TRANSLATE_TEMPLATE_PATH.is_file(), f"expected {MASS_TRANSLATE_TEMPLATE_PATH}"
    return MASS_TRANSLATE_TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def translate_prompt_body(mass_translate_js_source: str) -> str:
    return _extract_prompt_function_body(mass_translate_js_source, "translatePrompt")


@pytest.fixture(scope="module")
def review_dispatch_prompt_body(mass_translate_js_source: str) -> str:
    return _extract_prompt_function_body(mass_translate_js_source, "reviewDispatchPrompt")


def test_translate_prompt_references_canon_map_and_declined_stem_rule(translate_prompt_body):
    assert "canon_map" in translate_prompt_body, (
        "translatePrompt must reference the segpack's canon_map field (#130)"
    )
    assert "declined as the target grammar requires" in translate_prompt_body, (
        "translatePrompt must state the declined-stem enforcement rule (CONTRACT §4)"
    )
    assert "Use canon_names forms verbatim" not in translate_prompt_body, (
        "the old 'verbatim, no exceptions' wording must be gone -- it would make the "
        "reviewer flag a correctly inflected/declined form as wrong, causing chronic "
        "non-convergence (#130)"
    )


def test_review_dispatch_prompt_references_canon_map_fidelity_rule(review_dispatch_prompt_body):
    assert "canon_map" in review_dispatch_prompt_body, (
        "reviewDispatchPrompt must reference the segpack's canon_map field (#130)"
    )
    assert "correctly inflected/declined form of the canonical stem is CORRECT" in review_dispatch_prompt_body, (
        "reviewDispatchPrompt must state that a correctly inflected/declined form "
        "is CORRECT and must not be flagged (CONTRACT §4)"
    )
    assert "must NOT be flagged" in review_dispatch_prompt_body
