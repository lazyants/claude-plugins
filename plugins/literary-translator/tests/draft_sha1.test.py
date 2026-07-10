"""tests/draft_sha1.test.py -- regression-lock suite for
scripts/draft_sha1.py, the tiny stdlib-only (hashlib) script that prints
the sha1 of segments/{seg}.draft.json for a given segment ID (see
scripts/draft_sha1.py's own docstring and
references/ledger-and-resumability.md, "Canonical path invariants").

Two headline guarantees locked here:

1. draft_sha1.py's stdout digest for segments/{seg}.draft.json is BYTE-
   IDENTICAL to the sha1 ledger_update.py independently recomputes over
   the exact same on-disk file at convergence-write time. 1.2.0 CHANGE
   (CONTRACT-1.2.0-reliability.md section 2): both scripts now compute a
   CONTENT hash, not a raw-bytes hash -- each parses the draft as JSON,
   drops the top-level `dispatch_token` metadata field if present, and
   re-serializes the remainder via sorted-key canonical JSON
   (`sort_keys=True, ensure_ascii=False, separators=(",", ":")`) before
   hashing, so the digest is stable across a token-only change and
   independent of the file's own on-disk key order/whitespace. Both
   scripts document this (in their own docstrings, which cross-reference
   each other) as `draft_content_sha1()` -- this file proves the
   byte-identical-match invariant directly against the two REAL shipped
   scripts (draft_sha1.py exercised as a real subprocess exactly as
   production invokes it; ledger_update.py's draft_content_sha1() loaded
   by file identity out of the real shipped module and called directly),
   never a reimplementation of either that could silently drift from the
   real logic. (ledger_update.py separately keeps an UNRELATED
   sha1_bytes_of_file() helper for hashing its own ledger-fragment output
   file -- a plain file with no dispatch_token to exclude -- not exercised
   here; see ledger_confirmation_schema.test.py/durable_root_reachability
   .test.py for that one.)
2. No template or prompt anywhere in the shipped plugin shells out to a
   platform-dependent raw `sha1sum`/`shasum` command instead of this
   script. The one sanctioned real call site -- reviewDispatchPrompt() in
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
the script's own on-disk location, never cwd), content-hash canonicalization
(two JSON fixtures with logically-equal content but DIFFERENT byte
serialization -- key order, whitespace -- must now hash IDENTICALLY,
proving the content IS re-canonicalized, the inverse of the pre-1.2.0
raw-bytes invariant), dispatch_token exclusion (a token-only change, or the
field's outright presence/absence, must never perturb the hash, while a
genuine content change still must), and the three failure paths (wrong
argc, empty seg, missing draft file) plus the two new draft-content failure
modes (not valid JSON, valid JSON but not an object).
"""
import hashlib
import importlib.util
import json
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


LEDGER_DRAFT_CONTENT_SHA1 = _load_function(
    LEDGER_UPDATE_SRC, "_ledger_update_module_for_draft_sha1_test", "draft_content_sha1"
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


def canonical_expected_sha1(doc):
    """Independent, stdlib-only (never imports draft_sha1.py/ledger_update.py)
    ground-truth computation of the 1.2.0 content-hash algorithm: drop
    'dispatch_token' if present, re-serialize the remainder via sorted-key
    canonical JSON, hash. `doc` must be a dict. Deliberately duplicated here
    (not imported from either real script) so this file's expectations don't
    just trivially restate the scripts' own implementation -- see this
    file's own house style note in the module docstring."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


# ---------------------------------------------------------------------------
# 1. Exact-hash-match: draft_sha1.py's stdout must equal both an
#    independent ground-truth canonicalization AND ledger_update.py's own
#    real draft_content_sha1(), for a variety of realistic/edge-case
#    fixtures. Every fixture here must be a valid JSON object (1.2.0's
#    draft_content_sha1() requires it) -- unlike the pre-1.2.0 raw-bytes
#    scheme, an empty file or non-JSON content is now a genuine failure
#    mode, covered separately below (section 1b).
# ---------------------------------------------------------------------------

FIXTURE_CONTENTS = {
    "simple_ascii_json": b'{"seg": "seg01", "blocks": [{"id": "b1", "text": "hello"}]}',
    "unicode_content": (
        '{"seg": "seg02", "blocks": [{"id": "b1", '
        '"text": "Il pleuvait à Créteil — Привет 🌟"}]}'
    ).encode("utf-8"),
    "empty_object": b"{}",
    "trailing_newline": b'{"seg": "seg04"}\n',
    "no_trailing_newline": b'{"seg": "seg04"}',
    # A large draft (well past the pre-1.2.0 sha1_bytes_of_file's
    # 65536-byte read-chunk size) -- 1.2.0's draft_content_sha1() reads the
    # whole file via Path.read_text() + json.loads(), not a chunked binary
    # read, so this no longer exercises a chunk-boundary code path the way
    # it did pre-1.2.0; kept as a plain "doesn't choke on a large, valid
    # draft" sanity fixture instead.
    "large_draft": json.dumps(
        {"seg": "seg05", "blocks": {f"b{i}": "x" * 50 for i in range(2000)}}
    ).encode("utf-8"),
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

    expected = canonical_expected_sha1(json.loads(content))
    assert printed_digest == expected, (
        f"fixture {fixture_name!r}: draft_sha1.py printed {printed_digest}, "
        f"expected {expected} (independent sorted-key canonical JSON sha1, "
        f"dispatch_token excluded)"
    )

    ledger_digest = LEDGER_DRAFT_CONTENT_SHA1(draft_path)
    assert printed_digest == ledger_digest, (
        f"fixture {fixture_name!r}: draft_sha1.py's digest {printed_digest} must "
        f"match ledger_update.py's own independently-recomputed draft_content_sha1 "
        f"{ledger_digest} for the exact same on-disk file -- this is the "
        f"binding convergence-write-time check "
        f"(ledger_update.py compares this value against the reviewer's "
        f"pre-read draft_sha1 before it will ever record 'converged')"
    )


def test_json_reserialization_of_same_content_hashes_identically(tmp_path):
    """1.2.0 CHANGE (inverse of the pre-1.2.0 invariant this test used to
    lock): two files that parse to the SAME logical JSON object but differ
    in byte serialization (key order, whitespace) MUST now hash
    IDENTICALLY -- proving draft_sha1.py re-canonicalizes the JSON (sorted
    keys, compact separators) rather than hashing raw on-disk bytes. Both
    digests must still independently match ledger_update.py's own
    draft_content_sha1() and the standalone ground-truth helper."""
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

    assert digest_a == digest_b, (
        "logically-equal-but-byte-different JSON fixtures must produce the "
        "SAME sha1 digest post-1.2.0 -- if they differed, draft_sha1.py "
        "would still be hashing raw on-disk bytes instead of canonicalized "
        "content"
    )
    expected = canonical_expected_sha1({"a": 1, "b": 2})
    assert digest_a == digest_b == expected
    assert digest_a == LEDGER_DRAFT_CONTENT_SHA1(path_a)
    assert digest_b == LEDGER_DRAFT_CONTENT_SHA1(path_b)


def test_genuinely_different_content_still_hashes_differently(tmp_path):
    """Companion positive/negative pairing to the reserialization test
    above: this isn't a hash that's gone trivially constant -- a REAL
    content difference (not just key order/whitespace) must still change
    the digest."""
    root = make_durable_root(tmp_path)
    path_a = write_draft(root, "segP", b'{"seg": "segP", "blocks": {"p1": "hello"}}')
    path_b = write_draft(root, "segQ", b'{"seg": "segQ", "blocks": {"p1": "goodbye"}}')

    digest_a = run_draft_sha1(root, "segP").stdout.strip()
    digest_b = run_draft_sha1(root, "segQ").stdout.strip()

    assert digest_a != digest_b
    assert digest_a == LEDGER_DRAFT_CONTENT_SHA1(path_a)
    assert digest_b == LEDGER_DRAFT_CONTENT_SHA1(path_b)


def test_dispatch_token_excluded_from_hash(tmp_path):
    """CONTRACT-1.2.0-reliability.md section 2: dispatch_token is a
    run-scoped freshness metadata field, deliberately EXCLUDED from the
    content hash. Two drafts identical except for their dispatch_token
    VALUE must hash identically, and a draft WITH the field present must
    hash identically to the same draft with the field entirely ABSENT
    (proving true exclusion, not mere value-insensitivity to a field the
    algorithm still folds in some other way)."""
    root = make_durable_root(tmp_path)
    base = {"seg": "segTok", "blocks": {"p1": "hello"}}
    no_token = dict(base)
    token_a = dict(base, dispatch_token="20260101T000000Z:segTok")
    token_b = dict(base, dispatch_token="20260710T000000Z:segTok:rfinal")

    path_none = write_draft(root, "segTokNone", json.dumps(no_token).encode("utf-8"))
    path_a = write_draft(root, "segTokA", json.dumps(token_a).encode("utf-8"))
    path_b = write_draft(root, "segTokB", json.dumps(token_b).encode("utf-8"))

    digest_none = run_draft_sha1(root, "segTokNone").stdout.strip()
    digest_a = run_draft_sha1(root, "segTokA").stdout.strip()
    digest_b = run_draft_sha1(root, "segTokB").stdout.strip()

    assert digest_none == digest_a == digest_b, (
        "dispatch_token's presence or value must never perturb the content "
        f"hash: no-token={digest_none!r} token_a={digest_a!r} token_b={digest_b!r}"
    )
    expected = canonical_expected_sha1(base)
    assert digest_none == expected
    assert digest_none == LEDGER_DRAFT_CONTENT_SHA1(path_none) == LEDGER_DRAFT_CONTENT_SHA1(path_a) == LEDGER_DRAFT_CONTENT_SHA1(path_b)


# ---------------------------------------------------------------------------
# 1b. New 1.2.0 draft-content failure modes: draft_content_sha1() now
#     parses the file as JSON and requires a JSON object -- both are new
#     ways for this script to fail that didn't exist under the pre-1.2.0
#     raw-bytes scheme.
# ---------------------------------------------------------------------------

def test_draft_not_valid_json_errors_nonzero(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "segBadJSON"
    write_draft(root, seg, b"not valid json at all {")

    result = run_draft_sha1(root, seg)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "not valid JSON" in result.stderr


def test_draft_valid_json_but_not_an_object_errors_nonzero(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "segArrayDraft"
    write_draft(root, seg, b'["not", "an", "object"]')

    result = run_draft_sha1(root, seg)

    assert result.returncode == 1
    assert result.stdout == ""
    assert seg in result.stderr or "object" in result.stderr.lower()


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
    assert result.stdout.strip() == canonical_expected_sha1(json.loads(content))


# ---------------------------------------------------------------------------
# 3. Failure paths.
# ---------------------------------------------------------------------------

def test_missing_draft_file_errors_nonzero_and_names_path(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg_does_not_exist"

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
    seg = "seg_is_a_dir"
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
