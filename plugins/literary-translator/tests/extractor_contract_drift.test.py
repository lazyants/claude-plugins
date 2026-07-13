"""tests/extractor_contract_drift.test.py

Targets ``profile_validate.py``'s Step 13 resumed-project
``EXTRACTOR_CONTRACT_VERSION`` marker check on a resumed project's
``extract.py`` (see profile_validate.py's own module docstring, step 13,
and ``check_contract_marker()``/``check_resumed_contract_versions()``).

Mirrors ``tests/prompt_contract_drift.test.py``'s four malformed-marker
states, each asserting a named, non-zero-exit halt:

  - missing marker (treated as version 0, always stale)
  - malformed, non-integer marker value
  - duplicated marker with two CONFLICTING values
  - non-leading marker (present, but not the file's first non-blank line)

...plus the same two named edge cases (a duplicated marker where both
occurrences AGREE is not fatal; a marker preceded only by blank lines still
counts as "leading"), and a "no extract.py yet" not-a-violation baseline.

The one respect in which this file does NOT mirror ``prompt_contract_drift``
verbatim: ``extract.py``'s marker is a Python ``#`` comment
(``EXTRACTOR_CONTRACT_MARKER_RE = re.compile(r"^\\s*#\\s*EXTRACTOR_CONTRACT_VERSION:\\s*(.+?)\\s*$")``),
never the HTML-comment syntax the three ``*_TASK.md`` files use, because
``extract.py`` must stay valid, importable Python. Two extra cases lock
that distinction down: pasting the sibling files' own
``<!-- EXTRACTOR_CONTRACT_VERSION: N -->`` HTML-comment convention into
``extract.py`` by mistake must NOT be recognized (falls back to the
"missing marker" state, not silently accepted), and a compact, space-free
``#EXTRACTOR_CONTRACT_VERSION:N`` Python comment -- unremarkable Python
style, unlike the HTML-comment form -- must still be recognized as
well-formed.

Exercises the real CLI entry point (``main()``) against a fully
schema-valid ``profile.yml`` + ``durable_root`` fixture -- not the internal
``check_contract_marker()``/``check_resumed_contract_versions()`` helpers
directly -- so a regression that breaks the wiring between Step 5 (schema
validation) and Step 13 (the contract-marker check) would also be caught
here, not just a unit-level regression in the marker parser itself.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve the dotted module name (e.g.
``No module named 'extractor_contract_drift'``) -- run with
``python3 -m pytest --import-mode=importlib tests/extractor_contract_drift.test.py``.
"""
import importlib.util
import textwrap
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "literary-translator" / "assets" / "scripts" / "profile_validate.py"
)

# beside SCRIPT_PATH: the SHIPPED template's own marker, checked against
# CURRENT_EXTRACTOR_CONTRACT_VERSION below -- every OTHER test in this file
# exercises the resumed-project logic against SYNTHETIC fixtures built from
# the constant and never reads this file, so nothing else here catches a
# one-sided bump (mirrors extractor_selfcheck_hash_drift.test.py's own
# TEMPLATE_PATH on the OTHER stale-detection axis).
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "literary-translator" / "assets" / "templates" / "extract.py.template"
)

EXTRACT_PY_FILENAME = "extract.py"


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
    return f"# EXTRACTOR_CONTRACT_VERSION: {version}\n"


def _realistic_extract_py_body() -> str:
    """A small but structurally realistic extract.py body -- module-level
    comment block, module docstring, an import, a function -- so the
    "non-leading" fixture (a real comment header pushing the marker down)
    and the "missing marker" fixture (an otherwise unremarkable extractor
    module) both look like a real hand-adapted extract.py, not a bare
    toy string."""
    return (
        '"""Deterministic extractor (fixture stand-in for a real, '
        'hand-adapted extract.py)."""\n'
        "import json\n"
        "\n"
        "def extract():\n"
        "    return {}\n"
    )


def _write_valid_profile(tmp_path: Path, source_format: str = "gutenberg_epub") -> tuple[Path, Path]:
    """Builds a fully schema-valid, placeholder-free profile.yml (same
    recipe as tests/prompt_contract_drift.test.py's own fixture) plus its
    prerequisite filesystem fixtures -- an existing source file, and a
    durable_root whose parent exists and is writable. pytest's own
    tmp_path resolves under a literal `tmp` path component on Linux (e.g.
    CI runners honoring `TMPDIR=/tmp`), so any caller expecting Step 0's
    exit-0 pass must set `LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT=1` (see
    profile_validate.py's `check_durable_root`) -- callers that only
    assert a halt don't need it. Returns (profile_path, durable_root).

    ``source_format`` defaults to ``gutenberg_epub`` (every pre-existing
    caller in this file relies on that default unchanged); pass ``"custom"``
    for #180's format-gate tests below -- the fixture's
    ``adapter_config.custom: {{extractor_path: null}}`` and ``gutenberg_id:
    null`` already satisfy profile.schema.json's custom-format `allOf`
    branch (schema.json:362-380) with no further changes needed."""
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
          format: {source_format}
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


# ---------------------------------------------------------------------------
# Baseline sanity: a well-formed marker passes clean -- proves the fixture
# construction itself, not the marker check, is innocent before any halt is
# asserted below.
# ---------------------------------------------------------------------------

def test_well_formed_marker_passes_clean(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _well_formed_marker(pv.CURRENT_EXTRACTOR_CONTRACT_VERSION)
        + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"expected a clean exit, got {exit_code!r}; stderr:\n{err}"


def test_well_formed_marker_passes_clean_alongside_resumed_task_files(pv, tmp_path, monkeypatch, capsys):
    """A well-formed extract.py marker must not be disturbed by the sibling
    PROMPT_CONTRACT_VERSION check also running against well-formed
    *_TASK.md files in the same durable_root -- the two marker checks are
    independent, not mutually interfering."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _well_formed_marker(pv.CURRENT_EXTRACTOR_CONTRACT_VERSION)
        + _realistic_extract_py_body(),
        encoding="utf-8",
    )
    for filename in ("translate_TASK.md", "review_TASK.md", "glossary_TASK.md"):
        (durable_root / filename).write_text(
            f"<!-- PROMPT_CONTRACT_VERSION: {pv.CURRENT_PROMPT_CONTRACT_VERSION} -->\n"
            "Ordinary task body text.\n",
            encoding="utf-8",
        )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"expected a clean exit, got {exit_code!r}; stderr:\n{err}"


def test_no_extract_py_is_not_a_resumed_project_violation(pv, tmp_path, monkeypatch, capsys):
    """A genuinely fresh project (no extract.py yet at all) must not be
    mistaken for the 'missing marker' violation -- check_contract_marker
    only fires once the file exists (`if not path.is_file(): return []`)."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, _durable_root = _write_valid_profile(tmp_path)

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"a fresh project with no extract.py should pass; stderr:\n{err}"


# ---------------------------------------------------------------------------
# The four malformed marker states.
# ---------------------------------------------------------------------------

def test_missing_marker_halts(pv, tmp_path, capsys):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _realistic_extract_py_body(), encoding="utf-8"
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a missing EXTRACTOR_CONTRACT_VERSION marker must be a fatal halt"
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert "no leading EXTRACTOR_CONTRACT_VERSION marker found" in err, err
    assert "treated as" in err and "version 0" in err, err


def test_malformed_non_integer_marker_halts(pv, tmp_path, capsys):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        "# EXTRACTOR_CONTRACT_VERSION: latest\n" + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a non-integer marker value must be a fatal halt"
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert "malformed, non-integer value" in err, err
    assert "'latest'" in err, err


def test_duplicated_marker_conflicting_values_halts(pv, tmp_path, capsys):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        f"# EXTRACTOR_CONTRACT_VERSION: {current}\n"
        "# Some body comment in between the two markers.\n"
        f"# EXTRACTOR_CONTRACT_VERSION: {current + 1}\n"
        + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "two conflicting marker values in one file must be a fatal halt"
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert "duplicated EXTRACTOR_CONTRACT_VERSION marker" in err, err
    assert "conflicting values" in err, err


def test_non_leading_marker_halts(pv, tmp_path, capsys):
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        "# Some heading comment that precedes the marker\n"
        f"# EXTRACTOR_CONTRACT_VERSION: {current}\n"
        + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a marker that isn't the file's first non-blank line must halt"
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert "not the file's first non-blank line" in err, err


# ---------------------------------------------------------------------------
# Edge cases named alongside the four states in the same spec sentence --
# regression-locks against an over-eager fix that would turn these into
# false positives.
# ---------------------------------------------------------------------------

def test_duplicated_marker_with_identical_values_is_not_fatal(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        f"# EXTRACTOR_CONTRACT_VERSION: {current}\n"
        "# Some body comment.\n"
        f"# EXTRACTOR_CONTRACT_VERSION: {current}\n"
        + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a duplicated marker where both occurrences AGREE must not halt; stderr:\n{err}"
    )


def test_marker_after_leading_blank_lines_is_still_leading(pv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        "\n\n" + _well_formed_marker(current) + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a marker on the file's first NON-BLANK line should pass, blank lines "
        f"before it notwithstanding; stderr:\n{err}"
    )


# ---------------------------------------------------------------------------
# extract.py-specific: the marker must stay valid Python (a '#' comment),
# never the sibling *_TASK.md files' HTML-comment syntax.
# ---------------------------------------------------------------------------

def test_html_comment_syntax_marker_is_not_recognized(pv, tmp_path, capsys):
    """Pasting the *_TASK.md files' own
    ``<!-- PROMPT_CONTRACT_VERSION: N -->`` HTML-comment convention into
    extract.py by mistake is not valid Python and must not be silently
    accepted as a marker -- EXTRACTOR_CONTRACT_MARKER_RE requires a
    leading '#'. It must fall back to the ordinary 'missing marker'
    state, not some other error, and not a silent pass."""
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        f"<!-- EXTRACTOR_CONTRACT_VERSION: {current} -->\n" + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, (
        "an HTML-comment-syntax marker is not valid Python and must not be "
        "accepted as a well-formed EXTRACTOR_CONTRACT_VERSION marker"
    )
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert "no leading EXTRACTOR_CONTRACT_VERSION marker found" in err, err


def test_marker_without_inner_whitespace_is_still_recognized(pv, tmp_path, monkeypatch, capsys):
    """Ordinary, unremarkable Python comment style -- no space after '#' or
    around the colon -- must still be recognized as a well-formed marker
    (unlike the HTML-comment form above, this is genuinely valid Python and
    the marker's own regex is deliberately whitespace-tolerant)."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path)
    current = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        f"#EXTRACTOR_CONTRACT_VERSION:{current}\n" + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a space-free '#' comment marker is valid Python and must be "
        f"recognized as well-formed; stderr:\n{err}"
    )


# ---------------------------------------------------------------------------
# #180: source.format: custom -- the drift check must be SKIPPED, because
# Step 0a's extract.py is an unadapted extract.py.template copy for custom,
# never the real extractor (scripts/custom_extractors/<value>). Drift against
# CURRENT_EXTRACTOR_CONTRACT_VERSION is meaningless for a file nobody runs or
# adapts. custom.md already documented this intent; check_resumed_contract_
# versions() now honors it via the `source_format` gate.
# ---------------------------------------------------------------------------

def test_extractor_contract_drift_check_skipped_for_custom_format(pv, tmp_path, monkeypatch, capsys):
    """A STALE (well-formed but wrong-version) EXTRACTOR_CONTRACT_VERSION
    marker on extract.py must NOT be a fatal drift when source.format is
    custom. Red proof: before the format-gate, this fixture is byte-identical
    to a gutenberg_epub stale-version fixture and would halt exit!=0."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path, source_format="custom")
    stale = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION - 1
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _well_formed_marker(stale) + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a stale extract.py EXTRACTOR_CONTRACT_VERSION marker must not be a "
        f"fatal drift for source.format: custom (extract.py isn't the real "
        f"custom extractor); stderr:\n{err}"
    )
    assert "EXTRACTOR_CONTRACT_VERSION" not in err, err


def test_extractor_contract_drift_check_skipped_for_custom_format_missing_marker(
    pv, tmp_path, monkeypatch, capsys
):
    """Same skip, but for the 'missing marker' state (treated as version 0,
    normally always stale) -- the format-gate must short-circuit BEFORE
    check_contract_marker() runs at all for extract.py, not merely tolerate
    one particular stale value."""
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile_path, durable_root = _write_valid_profile(tmp_path, source_format="custom")
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _realistic_extract_py_body(), encoding="utf-8"
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, (
        f"a missing extract.py marker must not be a fatal drift for "
        f"source.format: custom; stderr:\n{err}"
    )
    assert "EXTRACTOR_CONTRACT_VERSION" not in err, err


def test_extractor_contract_drift_check_still_enforced_for_gutenberg_epub_stale_version(
    pv, tmp_path, capsys
):
    """CONVERSE of the two tests above: the format-gate lifts the check ONLY
    for custom -- a stale (well-formed, wrong-version) marker for
    gutenberg_epub (this file's default source_format) must still halt."""
    profile_path, durable_root = _write_valid_profile(tmp_path)
    stale = pv.CURRENT_EXTRACTOR_CONTRACT_VERSION - 1
    (durable_root / EXTRACT_PY_FILENAME).write_text(
        _well_formed_marker(stale) + _realistic_extract_py_body(),
        encoding="utf-8",
    )

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, (
        "a stale extract.py EXTRACTOR_CONTRACT_VERSION marker must still be "
        "a fatal drift for gutenberg_epub -- the format-gate must not lift "
        "the check for non-custom formats"
    )
    assert str(durable_root / EXTRACT_PY_FILENAME) in err, err
    assert f"is version {stale}, current is {pv.CURRENT_EXTRACTOR_CONTRACT_VERSION}" in err, err


def test_template_marker_matches_current_constant(pv):
    """template line-1 EXTRACTOR_CONTRACT_VERSION marker MUST equal
    pv.CURRENT_EXTRACTOR_CONTRACT_VERSION -- else a one-sided bump ships
    silently and every freshly-seeded project fails its own first resume
    check. Closes the gap in the rest of this file (synthetic fixtures only,
    never reads template:1); mirrors extractor_selfcheck_hash_drift's
    test_region_hash_matches_pinned_constant on the OTHER stale-detection
    axis. Proof-of-correctness is NOT pristine-vs-final (both would read
    1==1/2==2 green) -- a ONE-SIDED bump of either side is what this guards."""
    assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH
    first = next(
        (ln for ln in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()), None
    )
    assert first is not None, "template has no non-blank lines"
    m = pv.EXTRACTOR_CONTRACT_MARKER_RE.match(first)
    assert m is not None, f"template line 1 not a well-formed marker: {first!r}"
    assert int(m.group(1)) == pv.CURRENT_EXTRACTOR_CONTRACT_VERSION, (
        f"marker {m.group(1)!r} != constant {pv.CURRENT_EXTRACTOR_CONTRACT_VERSION}"
    )
