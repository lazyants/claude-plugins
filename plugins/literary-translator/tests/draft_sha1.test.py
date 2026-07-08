"""tests/draft_sha1.test.py -- regression-lock suite for
scripts/draft_sha1.py, the tiny stdlib-only (hashlib) script that prints
the sha1 of segments/{seg}.draft.json for a given segment ID (see
scripts/draft_sha1.py's own docstring and
references/ledger-and-resumability.md, "Canonical path invariants").

Two headline guarantees locked here:

1. draft_sha1.py's stdout digest for segments/{seg}.draft.json is BYTE-
   IDENTICAL to the sha1 ledger_update.py independently recomputes over
   the exact same on-disk file at convergence-write time. Both scripts
   document (in their own docstrings, which cross-reference each other)
   that their respective sha1_bytes_of_file() implementations hash the
   file's raw on-disk bytes in binary mode, chunked, nothing re-
   serialized or re-canonicalized as JSON -- this file proves that
   invariant directly against the two REAL shipped scripts (draft_sha1.py
   exercised as a real subprocess exactly as production invokes it;
   ledger_update.py's sha1_bytes_of_file loaded by file identity out of
   the real shipped module and called directly), never a reimplementation
   of either that could silently drift from the real logic.
2. No template or prompt anywhere in the shipped plugin shells out to a
   platform-dependent raw `sha1sum`/`shasum` command instead of this
   script. The one sanctioned real call site -- reviewPrompt() in
   mass-translate-wf.template.js, instructing the reviewer to compute
   draft_sha1 by shelling out to this exact script before reading the
   draft -- is asserted to literally reference `scripts/draft_sha1.py`,
   and every *.template.js file, every scripts/*.py file (other than
   draft_sha1.py's own self-documenting docstring), SKILL.md, and every
   references/*.md file is scanned for a `sha1sum`/`shasum` mention that
   is NOT sitting inside a negation ("never"/"no"/"not") -- which would
   indicate an actual (or newly-introduced, un-negated) raw-sha1sum
   invocation rather than a documented "we deliberately don't do this"
   note.

Plus ordinary CLI-contract behavior tests exercising the real script
against constructed fixtures: self-anchoring (finds segments/ relative to
the script's own on-disk location, never cwd), raw-bytes hashing (two
JSON fixtures with logically-equal content but different byte
serialization must hash DIFFERENTLY, proving nothing is re-canonicalized),
binary-mode fidelity (CRLF vs LF must hash differently, proving no
newline translation), chunk-boundary correctness for a file bigger than
the 65536-byte read chunk, and the three failure paths (wrong argc, empty
seg, missing draft file).
"""
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_DIR / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
TEMPLATES_DIR = ASSETS_DIR / "templates"
REFERENCES_DIR = SKILL_DIR / "references"
SKILL_MD = SKILL_DIR / "SKILL.md"

DRAFT_SHA1_SRC = SCRIPTS_DIR / "draft_sha1.py"
LEDGER_UPDATE_SRC = SCRIPTS_DIR / "ledger_update.py"

assert DRAFT_SHA1_SRC.is_file(), f"draft_sha1.py not found at {DRAFT_SHA1_SRC}"
assert LEDGER_UPDATE_SRC.is_file(), f"ledger_update.py not found at {LEDGER_UPDATE_SRC}"


def _load_function(path, module_name, func_name):
    """Loads `func_name` directly out of the REAL shipped script at `path`,
    by file identity (not package import) -- so the comparison test below
    exercises the actual production sha1_bytes_of_file() implementation
    ledger_update.py ships, never a reimplemented copy of it that could
    silently drift from the real logic."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, func_name)


LEDGER_SHA1_BYTES_OF_FILE = _load_function(
    LEDGER_UPDATE_SRC, "_ledger_update_module_for_draft_sha1_test", "sha1_bytes_of_file"
)


# ---------------------------------------------------------------------------
# Fixture harness -- durable_root for the real draft_sha1.py subprocess.
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Isolated durable_root: copies the REAL draft_sha1.py into
    {root}/scripts/ so its self-anchoring `Path(__file__).resolve().parents[1]`
    resolves to THIS temp root -- exactly matching production, which
    physically re-copies scripts/ into the project on every Step 0a run.
    The script never assumes cwd == durable_root and never takes a
    --durable-root flag, so callers of run_draft_sha1() below are free to
    invoke it from any cwd."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "segments").mkdir()
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    return root


def write_draft(root, seg, content_bytes):
    path = root / "segments" / f"{seg}.draft.json"
    path.write_bytes(content_bytes)
    return path


def run_draft_sha1(root, *args, cwd=None):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd) if cwd else None,
    )


def hashlib_sha1_hex(content_bytes):
    return hashlib.sha1(content_bytes).hexdigest()


# ---------------------------------------------------------------------------
# 1. Exact-hash-match: draft_sha1.py's stdout must equal both a plain
#    hashlib recomputation AND ledger_update.py's own real
#    sha1_bytes_of_file(), for a variety of realistic/edge-case fixtures.
# ---------------------------------------------------------------------------

FIXTURE_CONTENTS = {
    "simple_ascii_json": b'{"seg": "seg01", "blocks": [{"id": "b1", "text": "hello"}]}',
    "unicode_content": (
        '{"seg": "seg02", "blocks": [{"id": "b1", '
        '"text": "Il pleuvait à Créteil — Привет 🌟"}]}'
    ).encode("utf-8"),
    "empty_file": b"",
    "trailing_newline": b'{"seg": "seg04"}\n',
    "no_trailing_newline": b'{"seg": "seg04"}',
    # Bigger than the script's 65536-byte read chunk, with a non-round
    # remainder, to exercise the chunked-read loop across more than one
    # iteration and a partial final chunk.
    "multi_chunk": (b"abcdefghij" * 13108) + b"tail-bytes-137-chars-long-padding-x" * 4,
}


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_CONTENTS.keys()))
def test_matches_ledger_update_sha1_for_fixture(tmp_path, fixture_name):
    content = FIXTURE_CONTENTS[fixture_name]
    root = make_durable_root(tmp_path)
    seg = "segX"
    draft_path = write_draft(root, seg, content)

    result = run_draft_sha1(root, seg)

    assert result.returncode == 0, (
        f"draft_sha1.py must succeed for fixture {fixture_name!r}, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    printed_digest = result.stdout.strip()
    assert result.stdout == printed_digest + "\n", (
        "stdout must be the bare hex digest, newline-terminated, nothing else"
    )

    expected = hashlib_sha1_hex(content)
    assert printed_digest == expected, (
        f"fixture {fixture_name!r}: draft_sha1.py printed {printed_digest}, "
        f"expected {expected} (plain hashlib.sha1 of the raw bytes)"
    )

    ledger_digest = LEDGER_SHA1_BYTES_OF_FILE(draft_path)
    assert printed_digest == ledger_digest, (
        f"fixture {fixture_name!r}: draft_sha1.py's digest {printed_digest} must "
        f"match ledger_update.py's own independently-recomputed sha1 "
        f"{ledger_digest} for the exact same on-disk file -- this is the "
        f"binding convergence-write-time check "
        f"(ledger_update.py compares this value against the reviewer's "
        f"pre-read draft_sha1 before it will ever record 'converged')"
    )


def test_raw_bytes_not_reserialized_different_serializations_hash_differently(tmp_path):
    """Two files that parse to the SAME logical JSON object but differ in
    byte serialization (key order, whitespace) MUST hash differently --
    proving draft_sha1.py hashes raw on-disk bytes, never a re-serialized/
    canonicalized form of the JSON. Both digests must still independently
    match ledger_update.py's own sha1_bytes_of_file for their respective
    files."""
    root = make_durable_root(tmp_path)
    content_a = b'{"a": 1, "b": 2}\n'
    content_b = b'{"b":2,"a":1}'

    path_a = write_draft(root, "segA", content_a)
    path_b = write_draft(root, "segB", content_b)

    result_a = run_draft_sha1(root, "segA")
    result_b = run_draft_sha1(root, "segB")
    assert result_a.returncode == 0 and result_b.returncode == 0

    digest_a = result_a.stdout.strip()
    digest_b = result_b.stdout.strip()

    assert digest_a != digest_b, (
        "logically-equal-but-byte-different JSON fixtures must produce "
        "DIFFERENT sha1 digests -- if they matched, draft_sha1.py would be "
        "canonicalizing/re-serializing the JSON instead of hashing raw bytes"
    )
    assert digest_a == LEDGER_SHA1_BYTES_OF_FILE(path_a)
    assert digest_b == LEDGER_SHA1_BYTES_OF_FILE(path_b)


def test_binary_mode_crlf_vs_lf_hash_differently(tmp_path):
    """CRLF- vs LF-terminated content must hash differently -- proving the
    file is opened in binary mode with no newline translation (a text-mode
    open on some platforms would normalize line endings and silently
    change the hash)."""
    root = make_durable_root(tmp_path)
    lf_content = b'{"seg": "segC"}\nline2\n'
    crlf_content = b'{"seg": "segC"}\r\nline2\r\n'

    path_lf = write_draft(root, "segLF", lf_content)
    path_crlf = write_draft(root, "segCRLF", crlf_content)

    digest_lf = run_draft_sha1(root, "segLF").stdout.strip()
    digest_crlf = run_draft_sha1(root, "segCRLF").stdout.strip()

    assert digest_lf != digest_crlf
    assert digest_lf == hashlib_sha1_hex(lf_content) == LEDGER_SHA1_BYTES_OF_FILE(path_lf)
    assert digest_crlf == hashlib_sha1_hex(crlf_content) == LEDGER_SHA1_BYTES_OF_FILE(path_crlf)


# ---------------------------------------------------------------------------
# 2. Self-anchoring: the script finds segments/ relative to its own
#    on-disk location, never cwd.
# ---------------------------------------------------------------------------

def test_self_anchoring_independent_of_cwd(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "segAnchor"
    content = b'{"seg": "segAnchor", "blocks": []}'
    write_draft(root, seg, content)

    foreign_cwd = tmp_path / "somewhere_else_entirely"
    foreign_cwd.mkdir()
    assert foreign_cwd != root and foreign_cwd != (root / "scripts")

    result = run_draft_sha1(root, seg, cwd=foreign_cwd)

    assert result.returncode == 0, (
        f"draft_sha1.py must resolve segments/ via its own on-disk location, "
        f"not cwd -- got rc={result.returncode}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip() == hashlib_sha1_hex(content)


# ---------------------------------------------------------------------------
# 3. Failure paths.
# ---------------------------------------------------------------------------

def test_missing_draft_file_errors_nonzero_and_names_path(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg-does-not-exist"

    result = run_draft_sha1(root, seg)

    assert result.returncode == 1
    assert result.stdout == ""
    assert seg in result.stderr
    assert "not found" in result.stderr.lower()


@pytest.mark.parametrize("argv", [[], ["seg01", "extra-arg"]])
def test_wrong_argc_exits_2_with_usage(tmp_path, argv):
    root = make_durable_root(tmp_path)

    result = run_draft_sha1(root, *argv)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Usage" in result.stderr


def test_empty_seg_argument_exits_2(tmp_path):
    root = make_durable_root(tmp_path)

    result = run_draft_sha1(root, "")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "empty" in result.stderr.lower()


def test_unreadable_draft_file_errors_nonzero(tmp_path):
    """A draft path that exists as a DIRECTORY (not a file) must be treated
    as 'not found' rather than crashing with a raw traceback -- `is_file()`
    is false for a directory, so this exercises the same not-found branch,
    never an unhandled OSError surfacing as an ugly traceback."""
    root = make_durable_root(tmp_path)
    seg = "seg-is-a-dir"
    (root / "segments" / f"{seg}.draft.json").mkdir()

    result = run_draft_sha1(root, seg)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# 4. No template/prompt anywhere shells out to a raw sha1sum/shasum instead
#    of this script.
# ---------------------------------------------------------------------------

SHA1SUM_RE = re.compile(r"\b(sha1sum|shasum)\b", re.IGNORECASE)
NEGATION_RE = re.compile(r"\bnever\b|\bno\b|\bnot\b|\bn't\b", re.IGNORECASE)


def find_unguarded_sha1sum_mentions(text):
    """Returns a list of (offset, context) for every sha1sum/shasum mention
    in `text` that is NOT preceded, within a short window, by a negation
    word ("never"/"no"/"not"). A guarded mention is a documented "we
    deliberately don't do this" note (e.g. draft_sha1.py's own docstring:
    "no template or prompt anywhere in this plugin invokes a raw shell
    sha1sum/shasum command directly", or engine-loop.md's "... draft_sha1.py
    <seg> -- never raw sha1sum."). An UNGUARDED mention would indicate an
    actual raw-sha1sum invocation slipped into a template/prompt/script."""
    unguarded = []
    for m in SHA1SUM_RE.finditer(text):
        window_start = max(0, m.start() - 160)
        window = text[window_start:m.start()]
        if not NEGATION_RE.search(window):
            context = text[max(0, m.start() - 60):m.end() + 40]
            unguarded.append((m.start(), context))
    return unguarded


def _scan_files(paths):
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        unguarded = find_unguarded_sha1sum_mentions(text)
        assert unguarded == [], (
            f"{path} contains an UNGUARDED sha1sum/shasum mention (not "
            f"sitting inside a 'never'/'no'/'not' negation) -- this looks "
            f"like an actual raw-sha1sum invocation rather than a "
            f"documented never-do-this note. Context(s): {unguarded}"
        )


def test_no_unguarded_sha1sum_in_template_files():
    template_files = sorted(TEMPLATES_DIR.glob("*.template.js")) + sorted(
        TEMPLATES_DIR.glob("*.template.md")
    )
    assert template_files, f"expected at least one *.template.js under {TEMPLATES_DIR}"
    _scan_files(template_files)


def test_no_unguarded_sha1sum_in_scripts():
    script_files = sorted(SCRIPTS_DIR.glob("*.py"))
    assert script_files, f"expected script files under {SCRIPTS_DIR}"
    _scan_files(script_files)


def test_no_unguarded_sha1sum_in_skill_and_references():
    doc_files = [SKILL_MD] + sorted(REFERENCES_DIR.rglob("*.md"))
    assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"
    _scan_files(doc_files)


def test_the_one_sanctioned_call_site_uses_draft_sha1_py():
    """Positive control alongside the negative scans above: the real,
    only sanctioned hashing call site (reviewPrompt() in
    mass-translate-wf.template.js, instructing the reviewer to compute its
    pre-read draft_sha1 before touching the draft) must literally reference
    scripts/draft_sha1.py -- proving the negative scans above are passing
    because the real call site correctly uses this script, not because the
    call site was silently removed."""
    template_path = TEMPLATES_DIR / "mass-translate-wf.template.js"
    assert template_path.is_file(), f"not found: {template_path}"
    source = template_path.read_text(encoding="utf-8")
    assert "scripts/draft_sha1.py" in source, (
        "expected mass-translate-wf.template.js's reviewPrompt() to instruct "
        "shelling out to scripts/draft_sha1.py for the reviewer's pre-read "
        "draft_sha1 -- literal reference not found"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
