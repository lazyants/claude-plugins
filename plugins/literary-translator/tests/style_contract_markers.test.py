"""tests/style_contract_markers.test.py -- regression-catcher for #129: the
shipped `style_bible.template.md` carried no `STYLE_CONTRACT_BEGIN`/
`STYLE_CONTRACT_END` HTML-comment markers, so `cache_key.py`'s
`compute_style_contract_hash` -- which hard-requires exactly one of each,
in order -- FATALed on every fresh project's very first convergence write
("0 converged / ledger-write-failed"), with no earlier, cheaper signal at
W1 scaffold time.

Three independent layers, neither alone sufficient:

(a) Exact scope + adjacency, against the REAL shipped
    `style_bible.template.md`: exactly one BEGIN and one END marker, BEGIN
    before END; BEGIN sits immediately before section A (only whitespace
    between); END sits immediately after section F, with only whitespace
    and an optional `---` separator (no heading, no prose) before section
    G; the slice strictly between the markers contains every section A-F
    heading and excludes section G and the `## style_contract` heading.

(b) End-to-end repro: the REAL `cache_key.compute_style_contract_hash`,
    run against a byte-for-byte copy of the shipped template, must
    actually succeed (a 40-hex sha1, no `SystemExit`) and match a hash
    this test computes independently from its own marker slice -- proving
    the fix closes the literal "0 converged" gap, not just that this
    test's own slicing logic looks right in isolation.

(c) `scaffold_validate.py`'s new STYLE_CONTRACT check (#129): passes on
    the shipped template and FATALs, naming `style_bible.md`, on each of
    the five ways the markers can be malformed (a missing BEGIN and a
    missing END together cover the "both missing" state) -- zero BEGIN,
    zero END, duplicate BEGIN, duplicate END, END-before-BEGIN -- plus one
    subprocess-level smoke test proving the check is actually wired into
    `main()`, not just defined and unused.

(d) `scaffold_validate.py`'s clean-exit STYLE_CONTRACT span-size report
    (#578, the code half of #521): the number it prints is the length of
    the exact preimage `compute_style_contract_hash` hashes -- proved
    against the REAL hasher rather than against a second copy of the
    slice. The CLI smoke test that follows proves only that the report is
    wired into `main()`, and does re-derive the length for that. Every
    fixture in this layer carries NON-ASCII text inside the span on
    purpose: the shipped template's own span is all-ASCII (measured), so a
    fixture modelled on it could not tell a byte count from a character
    count and would pass either way.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATES_DIR = ASSETS_ROOT / "templates"
SCRIPTS_DIR = ASSETS_ROOT / "scripts"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"
SCAFFOLD_VALIDATE_SCRIPT = SCRIPTS_DIR / "scaffold_validate.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"

assert STYLE_BIBLE_TEMPLATE.is_file(), f"expected {STYLE_BIBLE_TEMPLATE} to exist"
assert SCAFFOLD_VALIDATE_SCRIPT.is_file(), f"expected {SCAFFOLD_VALIDATE_SCRIPT} to exist"
assert CACHE_KEY_SCRIPT.is_file(), f"expected {CACHE_KEY_SCRIPT} to exist"

# Byte-for-byte identical to both cache_key.py's and scaffold_validate.py's
# own marker literals -- see gotcha in scaffold_validate.py's module
# docstring: these must never diverge on whitespace tolerance.
BEGIN_MARKER = "<!-- STYLE_CONTRACT_BEGIN -->"
END_MARKER = "<!-- STYLE_CONTRACT_END -->"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scaffold_validate = _load_module("scaffold_validate_under_test_style_contract", SCAFFOLD_VALIDATE_SCRIPT)
cache_key = _load_module("cache_key_under_test_style_contract", CACHE_KEY_SCRIPT)


def _shipped_text() -> str:
    return STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) exact scope + adjacency, against the real shipped template
# ---------------------------------------------------------------------------


def test_shipped_template_has_exactly_one_begin_and_one_end_in_order():
    text = _shipped_text()
    assert text.count(BEGIN_MARKER) == 1
    assert text.count(END_MARKER) == 1
    assert text.index(BEGIN_MARKER) < text.index(END_MARKER)


def test_begin_marker_immediately_precedes_section_a_with_only_whitespace_between():
    text = _shipped_text()
    begin_end = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    section_a = text.index("### A. Register and voice")
    between = text[begin_end:section_a]
    assert between.strip() == "", (
        f"expected only whitespace between STYLE_CONTRACT_BEGIN and section A, got {between!r}"
    )


def test_end_marker_precedes_section_g_with_only_whitespace_and_an_optional_separator():
    text = _shipped_text()
    end_start = text.index(END_MARKER)
    section_g = text.index("## G. glossary")
    between = text[end_start + len(END_MARKER):section_g]
    stripped_lines = [line.strip() for line in between.splitlines() if line.strip()]
    assert all(line == "---" for line in stripped_lines), (
        f"expected only blank lines and an optional '---' separator between "
        f"STYLE_CONTRACT_END and section G -- no heading, no prose; got {stripped_lines}"
    )
    assert "### " not in between, f"expected no heading between END and section G, got {between!r}"


def test_between_markers_slice_contains_every_section_a_through_f_heading():
    text = _shipped_text()
    begin_end = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_start = text.index(END_MARKER)
    # Whitespace-normalize exactly as scaffold_validate does for its own
    # closed-list checks -- the section-F-BODY phrase asserted below spans a
    # real newline in the source, so a raw substring check would false-fail.
    normalized = re.sub(r"\s+", " ", text[begin_end:end_start])
    for heading in [
        "### A. Register and voice",
        "### B.",
        "### C. Names, titles, realia",
        "### C-translit.",
        "### D. Formatting",
        "### E. Techniques and hard cases",
        "### F. Reference samples",
    ]:
        assert heading in normalized, f"expected {heading!r} inside the style_contract span"
    assert "voice anchor every subsequent batch" in normalized
    assert "## G. glossary" not in normalized
    assert "## style_contract" not in normalized


# ---------------------------------------------------------------------------
# (b) end-to-end repro -- the real cache_key.compute_style_contract_hash
# ---------------------------------------------------------------------------


def test_compute_style_contract_hash_succeeds_on_shipped_template(tmp_path):
    """The literal '0 converged' reproduction (#129): before the fix, the
    shipped template carried no markers at all, so
    compute_style_contract_hash's own find_unique_marker would fail() ->
    SystemExit on every fresh project's first convergence write. Copying
    the REAL shipped template into a tmp durable root and calling the REAL
    function proves the fix actually closes that gap end to end, not just
    that this test's own marker slice looks right in isolation."""
    raw = STYLE_BIBLE_TEMPLATE.read_bytes()
    (tmp_path / "style_bible.md").write_bytes(raw)

    result = cache_key.compute_style_contract_hash({}, tmp_path)

    assert re.fullmatch(r"[0-9a-f]{40}", result), f"expected a 40-hex sha1, got {result!r}"

    begin_marker_bytes = BEGIN_MARKER.encode("utf-8")
    end_marker_bytes = END_MARKER.encode("utf-8")
    begin_idx = raw.index(begin_marker_bytes) + len(begin_marker_bytes)
    end_idx = raw.index(end_marker_bytes)
    expected = hashlib.sha1(raw[begin_idx:end_idx]).hexdigest()
    assert result == expected


# ---------------------------------------------------------------------------
# (c) scaffold_validate.py's STYLE_CONTRACT check -- direct function calls
# ---------------------------------------------------------------------------


def test_scan_style_contract_markers_passes_on_shipped_template():
    text = _shipped_text()
    findings = scaffold_validate.scan_style_contract_markers(STYLE_BIBLE_TEMPLATE, text)
    assert findings == [], f"expected the shipped template to pass clean, got {findings}"


def test_scan_style_contract_markers_both_missing():
    text = "# Style bible\n\n### A. Register and voice\nbody\n### F. Reference samples\nmore\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("STYLE_CONTRACT_BEGIN" in f and "missing" in f for f in findings)
    assert any("STYLE_CONTRACT_END" in f and "missing" in f for f in findings)
    assert len(findings) == 2


def test_scan_style_contract_markers_begin_only_missing():
    text = f"### A. Register and voice\nbody\n{END_MARKER}\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("STYLE_CONTRACT_BEGIN" in f and "missing" in f for f in findings)
    assert not any("STYLE_CONTRACT_END" in f for f in findings)
    assert len(findings) == 1


def test_scan_style_contract_markers_end_only_missing():
    text = f"{BEGIN_MARKER}\n### A. Register and voice\nbody\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("STYLE_CONTRACT_END" in f and "missing" in f for f in findings)
    assert not any("STYLE_CONTRACT_BEGIN" in f for f in findings)
    assert len(findings) == 1


def test_scan_style_contract_markers_duplicate_begin():
    text = f"{BEGIN_MARKER}\n### A. Register and voice\nbody\n{BEGIN_MARKER}\n{END_MARKER}\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("2 STYLE_CONTRACT_BEGIN markers" in f for f in findings)
    assert len(findings) == 1


def test_scan_style_contract_markers_duplicate_end():
    text = f"{BEGIN_MARKER}\n### A. Register and voice\nbody\n{END_MARKER}\n{END_MARKER}\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("2 STYLE_CONTRACT_END markers" in f for f in findings)
    assert len(findings) == 1


def test_scan_style_contract_markers_reversed_order():
    text = f"{END_MARKER}\n### A. Register and voice\nbody\n{BEGIN_MARKER}\n"
    findings = scaffold_validate.scan_style_contract_markers(Path("style_bible.md"), text)
    assert any("precedes" in f for f in findings)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# (c) scaffold_validate.py's STYLE_CONTRACT check -- CLI wiring smoke tests
# ---------------------------------------------------------------------------


def _seed_clean_durable_root(tmp_path: Path) -> Path:
    """Self-contained durable-root fixture (deliberately not shared with
    tests/required_fill_gates.test.py -- disjoint file ownership): the real
    scaffold_validate.py copied to <tmp>/scripts/ (self-anchoring means
    parents[1] resolves to tmp_path), plus every sibling file it scans
    seeded clean, so the only thing under test is the STYLE_CONTRACT check."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "scaffold_validate.py").write_bytes(SCAFFOLD_VALIDATE_SCRIPT.read_bytes())

    contents = {
        "PLAN.md": "# Project plan\n\nNothing unfilled.\n",
        "style_bible.md": (
            f"# Style bible\n\n{BEGIN_MARKER}\n### A. Register and voice\nbody\n{END_MARKER}\n\n"
            "## G. glossary\n\nNo unfilled brackets here.\n"
        ),
        "consistency_issues.md": "# Consistency issues\n\nNone logged yet.\n",
        "translate_TASK.md": "# Translate task\n\nNo era/domain trap example here.\n",
        "review_TASK.md": "# Review task\n\nNo era/domain trap example here.\n",
        "glossary_TASK.md": "# Glossary task\n\nNo unfilled brackets here.\n",
    }
    for name, text in contents.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run_scaffold_validate(durable_root: Path):
    return subprocess.run(
        [sys.executable, str(durable_root / "scripts" / "scaffold_validate.py")],
        cwd=durable_root,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_clean_synthetic_style_bible_passes_cli_smoke_test(tmp_path):
    durable_root = _seed_clean_durable_root(tmp_path)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode == 0, (
        f"expected a clean exit with well-formed STYLE_CONTRACT markers\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_missing_markers_fails_cli_naming_style_bible(tmp_path):
    """Proves the check is actually wired into main() -- a check that's
    defined but never called from main() would leave this subprocess run
    green even though the direct scan_style_contract_markers tests above
    correctly observe findings."""
    durable_root = _seed_clean_durable_root(tmp_path)
    (durable_root / "style_bible.md").write_text(
        "# Style bible\n\n### A. Register and voice\nbody\n\n## G. glossary\n",
        encoding="utf-8",
    )

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0, (
        f"expected a non-zero exit with both STYLE_CONTRACT markers missing\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "style_bible.md" in result.stdout
    assert "STYLE_CONTRACT_BEGIN" in result.stdout
    assert "STYLE_CONTRACT_END" in result.stdout


# ---------------------------------------------------------------------------
# (d) the clean-exit STYLE_CONTRACT span-size report (#578)
# ---------------------------------------------------------------------------

# Deliberately non-ASCII, and deliberately non-ASCII INSIDE the markers: the
# span's byte length must differ from its character length, or a helper that
# measured decoded characters would satisfy every assertion below.
NON_ASCII_SPAN_BODY = (
    "### A. Register and voice\n"
    "Читаемый современный русский с лёгкой патиной эпохи.\n"
    "Boundary case: не переводить дословно там, где идиома есть и в цели.\n"
)


def _seed_non_ascii_span_root(tmp_path: Path) -> Path:
    """A clean durable root whose style_contract span is multi-byte."""
    durable_root = _seed_clean_durable_root(tmp_path)
    (durable_root / "style_bible.md").write_text(
        f"# Style bible\n\n{BEGIN_MARKER}\n{NON_ASCII_SPAN_BODY}{END_MARKER}\n\n"
        "## G. glossary\n\nNo unfilled brackets here.\n",
        encoding="utf-8",
    )
    return durable_root


def _span_start(raw: bytes) -> int:
    begin_marker_bytes = BEGIN_MARKER.encode("utf-8")
    return raw.index(begin_marker_bytes) + len(begin_marker_bytes)


def test_reported_span_size_is_the_length_of_the_hashed_preimage(tmp_path, monkeypatch):
    """The reported number, taken as a byte length from the start of the span,
    must reproduce the REAL compute_style_contract_hash digest.

    Anchored on the hasher rather than on a second copy of the slice: an
    off-by-one at either marker, or a character count instead of a byte count,
    changes the preimage and breaks the digest equality. scaffold_validate is
    imported from the assets tree, so DURABLE_ROOT resolves to `assets/` --
    it has to be rebound to the seeded root or the helper reads the wrong file.
    """
    durable_root = _seed_non_ascii_span_root(tmp_path)
    monkeypatch.setattr(scaffold_validate, "DURABLE_ROOT", durable_root)

    reported = scaffold_validate.style_contract_span_bytes()

    raw = (durable_root / "style_bible.md").read_bytes()
    begin = _span_start(raw)
    expected_digest = cache_key.compute_style_contract_hash({}, durable_root)

    assert cache_key.sha1_hex(raw[begin:begin + reported]) == expected_digest


def test_non_ascii_fixture_actually_separates_bytes_from_characters(tmp_path):
    """Non-vacuity guard for the test above: if this fixture's span were
    all-ASCII, a character-counting helper would pass it and the byte-vs-char
    distinction -- the whole reason the report is specified in bytes -- would
    go unpinned.

    Measured straight off the seeded file, deliberately without calling
    `style_contract_span_bytes()`: a guard that asked the function under test
    for the quantity it is guarding would put the same bug on both sides of
    the comparison."""
    durable_root = _seed_non_ascii_span_root(tmp_path)

    raw = (durable_root / "style_bible.md").read_bytes()
    span = raw[_span_start(raw):raw.index(END_MARKER.encode("utf-8"))]

    assert len(span) > len(span.decode("utf-8"))


def test_clean_run_prints_the_span_size_cli_smoke_test(tmp_path):
    """Proves the report is wired into main(): a helper defined but never
    called would leave the direct tests above green."""
    durable_root = _seed_non_ascii_span_root(tmp_path)

    result = _run_scaffold_validate(durable_root)

    raw = (durable_root / "style_bible.md").read_bytes()
    expected = raw.index(END_MARKER.encode("utf-8")) - _span_start(raw)
    assert result.returncode == 0, (
        f"expected a clean exit\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert f"style_contract span is {expected} bytes" in result.stdout, (
        f"expected the OK line to report {expected} bytes\nstdout:\n{result.stdout}"
    )


@pytest.mark.parametrize(
    "span_wrapper",
    [
        pytest.param(lambda body: f"{body}{END_MARKER}\n", id="begin-marker-gone"),
        pytest.param(
            lambda body: f"{BEGIN_MARKER}\n{body}{BEGIN_MARKER}\n{END_MARKER}\n",
            id="begin-marker-duplicated",
        ),
        pytest.param(
            lambda body: f"{END_MARKER}\n{body}{BEGIN_MARKER}\n", id="markers-reversed"
        ),
    ],
)
def test_span_size_refuses_to_report_a_number_for_a_file_edited_under_it(
    tmp_path, monkeypatch, span_wrapper
):
    """The scan and this report read the file twice. If style_bible.md is
    edited in between, the second read must not turn unchecked arithmetic
    into a confident, wrong number underneath an OK line: a duplicated
    marker silently picks the first one, a reversal yields a negative
    length, and an absent marker raises only an unshaped ValueError from
    `bytes.index` that names nothing. The guards make all three raise with
    the file named. Raising is the accepted behaviour; returning a number
    is not."""
    durable_root = _seed_non_ascii_span_root(tmp_path)
    (durable_root / "style_bible.md").write_text(
        f"# Style bible\n\n{span_wrapper(NON_ASCII_SPAN_BODY)}\n## G. glossary\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scaffold_validate, "DURABLE_ROOT", durable_root)

    with pytest.raises(ValueError, match="style_bible.md changed"):
        scaffold_validate.style_contract_span_bytes()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
