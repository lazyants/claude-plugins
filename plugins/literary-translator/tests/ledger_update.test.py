"""tests/ledger_update.test.py -- regression-lock suite for
scripts/ledger_update.py, the atomic per-segment ledger fragment writer
(see references/ledger-and-resumability.md, "scripts/ledger_update.py --
the fragment writer").

Every write ledger_update.py performs is documented as a FULL REPLACE,
never a read-modify-write merge: the fragment it writes is built entirely
fresh from (1) a freshly generated timestamp, (2) status plus whichever
other fields THIS payload supplied, (3) n_blocks/n_footnotes/n_verses/
reviewed_draft_sha1 -- derived by the script itself, only for status ==
'converged'. The prior on-disk fragment's field VALUES are never read
into the new record. This file locks that guarantee down with real
fixtures and the real script, invoked exactly as production does
(`python3 {durable_root}/scripts/ledger_update.py {seg} --payload-file
<path>`), plus the payload sub-schema's rejection of a non-bare-integer
`rounds` value, plus the separate JS-side payload-intent-mismatch check
that lives in mass-translate-wf.template.js's `recordLedgerCall` (a
mocked/tampered stdout claim from ledger_update.py -- a different segment
or status than the caller actually requested -- must be caught there,
independent of ledger_update.py itself, which has no way to know the
caller's original intent).

Four groups of tests:

  1. non_converged -> in_progress: the resulting fragment must have
     exactly {timestamp, status} -- no reason/rounds survive.
  2. converged -> in_progress: the resulting fragment must have exactly
     {timestamp, status} -- no rounds/cache_key/n_blocks/n_footnotes/
     n_verses/reviewed_draft_sha1 survive.
  3. An object-shaped `rounds` payload (e.g. {translate, review, fix}) is
     explicitly REJECTED -- rounds must be a bare integer, every branch of
     reviewFixLoop() returns a bare int -- with no fragment write at all,
     whether or not a prior fragment already existed for that segment.
  4. The JS-side payload-intent-mismatch check: `recordLedgerCall` in
     mass-translate-wf.template.js is extracted VERBATIM (via a
     brace-counting source extractor, not reimplemented) from the real
     shipped template and executed under real node with a stubbed
     `agent()` returning a tampered ledger_update.py stdout claim. A
     mismatched status or mismatched segment must be caught
     (`reason: 'ledger-write-mismatch'`); a genuine, untampered claim for
     the seg/status the caller actually intended must be accepted (the
     control case, proving the harness itself doesn't just default to
     failure).

Each successful write additionally re-derives the fragment's sha1
independently (raw file bytes, same as ledger_update.py's own
sha1_bytes_of_file) and compares it against the stdout claim -- mirroring
recordLedgerPrompt's own mandated "never trust the command's own
fragment_sha1 claim without this independent check" discipline.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_SRC = ASSETS_DIR / "scripts" / "ledger_update.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"
TEMPLATE_JS_PATH = ASSETS_DIR / "templates" / "mass-translate-wf.template.js"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _js_source_projection import js_code_only  # noqa: E402

assert SCRIPT_SRC.is_file(), f"ledger_update.py not found at {SCRIPT_SRC}"
assert (SCHEMAS_SRC / "ledger-record-base.schema.json").is_file()
assert (SCHEMAS_SRC / "ledger-fragment.schema.json").is_file()
assert TEMPLATE_JS_PATH.is_file(), f"mass-translate-wf.template.js not found at {TEMPLATE_JS_PATH}"

NODE_PATH = shutil.which("node")
requires_node = pytest.mark.skipif(
    NODE_PATH is None,
    reason="node not found on PATH -- cannot exercise the JS-side "
           "payload-intent-mismatch check in mass-translate-wf.template.js",
)

# The composite 15-field cache_key -- every field ledger-record-base.schema.json
# requires inside cache_key, used only to build a realistic PRIOR converged
# fragment fixture (ledger_update.py never reads any of this back in).
FULL_CACHE_KEY = {
    "input_sha1": "a1",
    "style_contract_hash": "b2",
    "used_terms_hash": "c3",
    "pipeline_version": "v1",
    "schema_hash": "d4",
    "prompt_hash": "e5",
    "agent_config_hash": "f6",
    "profile_semantics_hash": "g7",
    "particle_config_hash": "h8",
    "source_extraction_hash": "i9",
    "source_input_hash": "j10",
    "derivation_bundle_hash": "k11",
    "verse_map_hash": "l12",
    "note_map_hash": "m13",
    "plugin_bundle_hash": "n14",
}


# ---------------------------------------------------------------------------
# Fixture harness -- durable_root for the real ledger_update.py subprocess.
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL ledger_update.py into
    {root}/scripts/ (so its self-anchoring `Path(__file__).resolve().parents[1]`
    resolves to THIS temp root, exactly matching production -- the script
    never assumes cwd == durable_root and never takes a --durable-root flag)
    plus the two real schema files it loads at runtime, and creates
    segments/ and runs/."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(
        SCHEMAS_SRC / "ledger-record-base.schema.json",
        schemas_dir / "ledger-record-base.schema.json",
    )
    shutil.copy2(
        SCHEMAS_SRC / "ledger-fragment.schema.json",
        schemas_dir / "ledger-fragment.schema.json",
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def write_payload(root, name, payload):
    path = root / "runs" / f".ledger_update_payload.{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def write_prior_fragment(root, seg, fragment):
    """Hand-authors a PRIOR on-disk fragment, standing in for whatever an
    earlier ledger_update.py invocation (or a resumed/interrupted run) left
    behind. ledger_update.py never reads a prior fragment's field VALUES
    back in (only os.replace()'s rename-target-existing check touches it),
    so a hand-authored fixture here exercises exactly the same code path a
    script-produced prior fragment would."""
    ledger_dir = root / "runs" / "ledger.d"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / f"{seg}.json").write_text(
        json.dumps(fragment, ensure_ascii=False), encoding="utf-8"
    )


def read_fragment(root, seg):
    path = root / "runs" / "ledger.d" / f"{seg}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_ledger_update(root, seg, payload_path):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "ledger_update.py"),
            seg,
            "--payload-file",
            str(payload_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def sha1_of_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. non_converged -> in_progress: full replace, no reason/rounds survive.
# ---------------------------------------------------------------------------

def test_non_converged_to_in_progress_is_full_replace(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg01"
    write_prior_fragment(root, seg, {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "non_converged",
        "reason": "translate-timeout",
        "rounds": 2,
    })
    payload_path = write_payload(root, "p1", {"status": "in_progress"})

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0, (
        f"a plain in_progress payload must succeed, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    assert stdout["status"] == "in_progress"

    fragment = read_fragment(root, seg)
    assert set(fragment.keys()) == {"timestamp", "status"}, (
        f"an in_progress write over a prior non_converged fragment must be a "
        f"full replace with no leftover fields, got keys {sorted(fragment.keys())}"
    )
    assert fragment["status"] == "in_progress"
    assert "reason" not in fragment
    assert "rounds" not in fragment

    # Independent sha1 re-check -- mirrors recordLedgerPrompt's own mandated
    # "never trust the command's own fragment_sha1 claim without this
    # independent check" discipline.
    assert sha1_of_file(Path(stdout["fragment_path"])) == stdout["fragment_sha1"]


# ---------------------------------------------------------------------------
# 2. converged -> in_progress: full replace, no rounds/cache_key/n_blocks/etc.
# ---------------------------------------------------------------------------

def test_converged_to_in_progress_is_full_replace(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg02"
    write_prior_fragment(root, seg, {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "cache_key": FULL_CACHE_KEY,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 0,
        "reviewed_draft_sha1": "deadbeef" * 5,
    })
    payload_path = write_payload(root, "p2", {"status": "in_progress"})

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0, (
        f"a plain in_progress payload must succeed, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    assert stdout["status"] == "in_progress"

    fragment = read_fragment(root, seg)
    assert set(fragment.keys()) == {"timestamp", "status"}, (
        f"an in_progress write over a prior converged fragment must be a "
        f"full replace with no leftover fields, got keys {sorted(fragment.keys())}"
    )
    for leftover_key in (
        "rounds", "cache_key", "n_blocks", "n_footnotes", "n_verses",
        "reviewed_draft_sha1",
    ):
        assert leftover_key not in fragment, (
            f"'{leftover_key}' from the prior converged fragment must not "
            f"survive an in_progress full-replace write"
        )

    assert sha1_of_file(Path(stdout["fragment_path"])) == stdout["fragment_sha1"]


# ---------------------------------------------------------------------------
# 3. Object-shaped `rounds` payload is explicitly REJECTED.
# ---------------------------------------------------------------------------

def test_object_shaped_rounds_rejected_no_write(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg03"
    payload_path = write_payload(root, "p3", {
        "status": "non_converged",
        "reason": "cap",
        "rounds": {"translate": 1, "review": 2, "fix": 3},
    })

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode != 0, (
        f"an object-shaped rounds payload must be rejected, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is False
    assert "Malformed payload" in stdout["error"]
    assert "not of type 'integer'" in stdout["error"], stdout["error"]
    # Failure shapes never claim a fragment_path/fragment_sha1 that was
    # never written.
    assert "fragment_path" not in stdout
    assert "fragment_sha1" not in stdout
    assert not (root / "runs" / "ledger.d" / f"{seg}.json").exists(), (
        "a rejected payload must never produce a fragment write"
    )


def test_object_shaped_rounds_rejected_does_not_clobber_existing_fragment(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg04"
    prior = {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress"}
    write_prior_fragment(root, seg, prior)
    payload_path = write_payload(root, "p4", {
        "status": "non_converged",
        "reason": "cap",
        "rounds": {"translate": 1},
    })

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode != 0
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is False

    fragment = read_fragment(root, seg)
    assert fragment == prior, (
        "a rejected payload must leave any prior on-disk fragment byte-for-"
        "byte untouched"
    )


def test_bare_integer_rounds_is_accepted_control(tmp_path):
    """Control alongside the two rejection cases above: proves the object
    shape (not `rounds` itself, and not the `reason`/`status` combination)
    is what's rejected -- the exact same payload with a bare integer
    `rounds` must succeed."""
    root = make_durable_root(tmp_path)
    seg = "seg05"
    payload_path = write_payload(root, "p5", {
        "status": "non_converged",
        "reason": "cap",
        "rounds": 4,
    })

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0, (
        f"a bare-integer rounds payload must be accepted, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    fragment = read_fragment(root, seg)
    assert fragment["rounds"] == 4
    assert fragment["status"] == "non_converged"
    assert fragment["reason"] == "cap"


# ---------------------------------------------------------------------------
# 4. JS-side payload-intent-mismatch check (recordLedgerCall in
#    mass-translate-wf.template.js). The real functions are extracted
#    VERBATIM from the shipped template via a brace-counting source
#    extractor -- never reimplemented -- so a future edit to the real
#    mismatch logic is exercised here as-is, and a rename/removal of any of
#    the three functions below fails this file loudly at collection time
#    rather than silently testing a stale copy.
# ---------------------------------------------------------------------------

def _extract_js_function(source, signature_prefix):
    """Returns the full source text of a JS function/async-function
    declaration starting at `signature_prefix` (e.g. "function foo(" or
    "async function foo("), through its matching closing brace.

    Both the LOCATE step (`signature_prefix`, the opening `{`) and the
    brace-counting scan run over `js_code_only(source)` -- the #306/#289
    offset-preserving projection with every comment, string literal,
    template literal and regex literal blanked to spaces -- so a commented-
    out or prose copy of the same declaration ahead of the real one can't
    win, and a brace inside a string or comment can't unbalance the count.
    The projection preserves every offset, so the match found in it is
    sliced out of the RAW `source` for a verbatim result. Raises if the
    prefix isn't found or braces never balance -- both are meant to fail the
    test loudly, not silently degrade to a truncated/garbage extraction."""
    code = js_code_only(source)
    try:
        idx = code.index(signature_prefix)
    except ValueError:
        raise ValueError(
            f"{signature_prefix!r} not found in the template's CODE -- it "
            "may exist only inside a comment or a prompt string, which is "
            "exactly what this projection-anchored lookup refuses"
        ) from None
    open_brace = code.index("{", idx)
    depth = 0
    i = open_brace
    while i < len(code):
        c = code[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[idx:i + 1]
        i += 1
    raise ValueError(f"unbalanced braces extracting {signature_prefix!r}")


def _extract_js_const(source, const_name):
    """Returns the full `const NAME = ...;` statement text for a single-line
    const declaration (the exact-key-set guard array literals below are all
    single-line) -- a lighter sibling of _extract_js_function for
    declarations that aren't function bodies.

    Both the LOCATE step and the terminating `;` are found in
    `js_code_only(source)`, so a decoy comment ahead of the real declaration
    can't win, and a `;` inside the declaration's own string literals (e.g.
    an array entry like `"a;b"`) can't truncate the extraction early -- the
    projection has already blanked those to spaces. The match is then
    sliced out of the RAW `source`, so the result stays verbatim."""
    code = js_code_only(source)
    try:
        idx = code.index(f"const {const_name} ")
    except ValueError:
        raise ValueError(
            f"'const {const_name} ' not found in the template's CODE -- it "
            "may exist only inside a comment or a prompt string, which is "
            "exactly what this projection-anchored lookup refuses"
        ) from None
    end = code.index(";", idx)
    return source[idx:end + 1]


_TEMPLATE_JS_SOURCE = TEMPLATE_JS_PATH.read_text(encoding="utf-8")

ENDS_WITH_SEG_JSON_SRC = _extract_js_function(
    _TEMPLATE_JS_SOURCE, "function endsWithSegJson("
)
RECORD_LEDGER_PROMPT_SRC = _extract_js_function(
    _TEMPLATE_JS_SOURCE, "function recordLedgerPrompt("
)
RECORD_LEDGER_CALL_SRC = _extract_js_function(
    _TEMPLATE_JS_SOURCE, "async function recordLedgerCall("
)
assert "ledger-write-mismatch" in RECORD_LEDGER_CALL_SRC, (
    "expected the extracted recordLedgerCall source to contain the "
    "'ledger-write-mismatch' reason literal -- extraction may have grabbed "
    "the wrong function, or the mismatch reason string was renamed"
)

# 1.2.0 (CONTRACT-1.2.0-reliability.md section 5, #87 fix): recordLedgerCall
# now gates on ledgerWriteSucceeded(raw) -- the consume-site JS guard --
# instead of trusting a bare `raw.success` truthiness check the way the
# pre-1.2.0 template did. That guard, and its own small dependency chain
# (isNonEmptyString/isEmptyString/isZeroExitCode/hasOnlyKeys/
# hasFailureEvidence/NO_FAILURE_EVIDENCE/LEDGER_WRITE_SUCCESS_KEYS/
# FAILURE_EVIDENCE_KEYS/LEDGER_WRITE_ALLOWED_KEYS), must be extracted and
# spliced into the harness alongside the three functions above, or
# recordLedgerCall's first line throws a bare ReferenceError before ever
# reaching the payload-intent-mismatch logic this section exists to test.
# Splice order is load-bearing twice over: the two key-set consts must
# precede LEDGER_WRITE_ALLOWED_KEYS, which `.concat()`s them at declaration
# time, and the benign-value predicates must precede NO_FAILURE_EVIDENCE,
# whose object literal references them by identifier.
IS_NON_EMPTY_STRING_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function isNonEmptyString(")
IS_EMPTY_STRING_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function isEmptyString(")
IS_ZERO_EXIT_CODE_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function isZeroExitCode(")
HAS_ONLY_KEYS_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function hasOnlyKeys(")
HAS_FAILURE_EVIDENCE_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function hasFailureEvidence(")
NO_FAILURE_EVIDENCE_SRC = _extract_js_const(_TEMPLATE_JS_SOURCE, "NO_FAILURE_EVIDENCE")
LEDGER_WRITE_SUCCESS_KEYS_SRC = _extract_js_const(_TEMPLATE_JS_SOURCE, "LEDGER_WRITE_SUCCESS_KEYS")
FAILURE_EVIDENCE_KEYS_SRC = _extract_js_const(_TEMPLATE_JS_SOURCE, "FAILURE_EVIDENCE_KEYS")
LEDGER_WRITE_ALLOWED_KEYS_SRC = _extract_js_const(_TEMPLATE_JS_SOURCE, "LEDGER_WRITE_ALLOWED_KEYS")
LEDGER_WRITE_SUCCEEDED_SRC = _extract_js_function(_TEMPLATE_JS_SOURCE, "function ledgerWriteSucceeded(")


def build_harness_js(tmp_path):
    """Assembles a standalone node script around the three REAL, verbatim-
    extracted functions above. Everything recordLedgerCall/recordLedgerPrompt
    reference that lives OUTSIDE those three functions in the real template
    (ROOT, PY, LEDGER_WRITE_SCHEMA, and the Workflow-tool-injected agent())
    is stubbed here -- agent() returns whatever mocked/tampered stdout-claim
    object this test wants ledger_update.py to have printed, passed in as
    the script's first CLI argument."""
    harness = tmp_path / "recordLedgerCall_harness.js"
    harness.write_text(
        "const ROOT = \"/fixture/durable_root\";\n"
        "const PY = \"python3\";\n"
        "const LEDGER_WRITE_SCHEMA = {};\n"
        "\n"
        + ENDS_WITH_SEG_JSON_SRC + "\n"
        "\n"
        + RECORD_LEDGER_PROMPT_SRC + "\n"
        "\n"
        + IS_NON_EMPTY_STRING_SRC + "\n"
        "\n"
        + IS_EMPTY_STRING_SRC + "\n"
        "\n"
        + IS_ZERO_EXIT_CODE_SRC + "\n"
        "\n"
        + HAS_ONLY_KEYS_SRC + "\n"
        "\n"
        + LEDGER_WRITE_SUCCESS_KEYS_SRC + "\n"
        + FAILURE_EVIDENCE_KEYS_SRC + "\n"
        + LEDGER_WRITE_ALLOWED_KEYS_SRC + "\n"
        + NO_FAILURE_EVIDENCE_SRC + "\n"
        "\n"
        + HAS_FAILURE_EVIDENCE_SRC + "\n"
        "\n"
        + LEDGER_WRITE_SUCCEEDED_SRC + "\n"
        "\n"
        "const __MOCK_RAW__ = JSON.parse(process.argv[2]);\n"
        "async function agent(prompt, opts) { return __MOCK_RAW__; }\n"
        "\n"
        + RECORD_LEDGER_CALL_SRC + "\n"
        "\n"
        "(async () => {\n"
        "  const seg = process.argv[3];\n"
        "  const fields = JSON.parse(process.argv[4]);\n"
        "  const result = await recordLedgerCall(seg, fields, 'test');\n"
        "  console.log(JSON.stringify(result));\n"
        "})();\n",
        encoding="utf-8",
    )
    return harness


def run_record_ledger_call(tmp_path, mock_raw, seg, fields):
    assert NODE_PATH is not None, "node executable not found on PATH -- required to run this test file"
    harness = build_harness_js(tmp_path)
    result = subprocess.run(
        [NODE_PATH, str(harness), json.dumps(mock_raw), seg, json.dumps(fields)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"node harness crashed: rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip())


@requires_node
def test_js_side_catches_mismatched_status(tmp_path):
    """A real ledger_update.py write happens for status in_progress; the
    stdout claim is then tampered to say status: converged instead -- the
    JS-side check must catch this as a payload-intent mismatch, never as a
    silent success."""
    root = make_durable_root(tmp_path)
    seg = "beta"
    payload_path = write_payload(root, "p6", {"status": "in_progress"})
    result = run_ledger_update(root, seg, payload_path)
    assert result.returncode == 0
    genuine_raw = json.loads(result.stdout.strip())

    tampered = dict(genuine_raw)
    tampered["status"] = "converged"  # falsely claims a different status

    js_result = run_record_ledger_call(
        tmp_path, tampered, seg, {"status": "in_progress"}
    )
    assert js_result["ok"] is False
    assert js_result["failResult"]["reason"] == "ledger-write-mismatch"
    assert js_result["failResult"]["seg"] == seg
    assert "status=converged" in js_result["failResult"]["detail"]


@requires_node
def test_js_side_catches_mismatched_segment(tmp_path):
    """Same real write, but the stdout claim's fragment_path is tampered to
    point at a DIFFERENT segment's fragment while status matches -- must
    also be caught as a mismatch."""
    root = make_durable_root(tmp_path)
    seg = "gamma"
    payload_path = write_payload(root, "p7", {"status": "in_progress"})
    result = run_ledger_update(root, seg, payload_path)
    assert result.returncode == 0
    genuine_raw = json.loads(result.stdout.strip())

    tampered = dict(genuine_raw)
    assert tampered["fragment_path"].endswith(f"{seg}.json")
    tampered["fragment_path"] = tampered["fragment_path"][: -len(f"{seg}.json")] + "some-other-seg.json"

    js_result = run_record_ledger_call(
        tmp_path, tampered, seg, {"status": "in_progress"}
    )
    assert js_result["ok"] is False
    assert js_result["failResult"]["reason"] == "ledger-write-mismatch"


@requires_node
def test_js_side_accepts_genuine_matching_stdout(tmp_path):
    """Control: an UNTAMPERED, genuine stdout claim for the exact seg/status
    the caller intended must be accepted -- proves the two mismatch tests
    above fail because of the tampering specifically, not because the
    harness/extraction always reports a mismatch."""
    root = make_durable_root(tmp_path)
    seg = "delta"
    payload_path = write_payload(root, "p8", {"status": "in_progress"})
    result = run_ledger_update(root, seg, payload_path)
    assert result.returncode == 0
    genuine_raw = json.loads(result.stdout.strip())

    js_result = run_record_ledger_call(
        tmp_path, genuine_raw, seg, {"status": "in_progress"}
    )
    assert js_result["ok"] is True
    assert js_result["raw"]["status"] == "in_progress"


# ---------------------------------------------------------------------------
# 8a. Decoy lock (#306): a prose/comment copy of a signature ahead of the
#     real declaration, or a `;` inside the real declaration's own string
#     literal, must not fool either extractor above. Mirrors
#     ledger_confirmation_schema.test.py's
#     test_extractors_ignore_a_commented_out_declaration, for THIS file's
#     own extractors. Every mutation here is applied to a MUTATED COPY of
#     the template text held in memory -- the shipped template on disk is
#     never touched.
# ---------------------------------------------------------------------------

def test_extract_js_function_ignores_a_commented_out_decoy():
    anchor = "async function recordLedgerCall("
    assert anchor in _TEMPLATE_JS_SOURCE, (
        f"non-vacuity: {anchor!r} must actually be present in the shipped "
        "template before mutating it"
    )
    decoy = '// old: async function recordLedgerCall(raw) { return "DECOY"; }\n'
    mutated = _TEMPLATE_JS_SOURCE.replace(anchor, decoy + anchor, 1)

    extracted = _extract_js_function(mutated, anchor)

    assert "ledger-write-mismatch" in extracted, (
        "extraction returned the decoy instead of the real recordLedgerCall "
        f"body: {extracted!r}"
    )
    assert "DECOY" not in extracted


def test_extract_js_const_ignores_a_commented_out_decoy():
    anchor = "const FAILURE_EVIDENCE_KEYS "
    assert anchor in _TEMPLATE_JS_SOURCE, (
        f"non-vacuity: {anchor!r} must actually be present in the shipped "
        "template before mutating it"
    )
    decoy = '// historical: const FAILURE_EVIDENCE_KEYS = ["error"];\n'
    mutated = _TEMPLATE_JS_SOURCE.replace(anchor, decoy + anchor, 1)

    extracted = _extract_js_const(mutated, "FAILURE_EVIDENCE_KEYS")

    assert "exit_code" in extracted, (
        "extraction returned the decoy instead of the real "
        f"FAILURE_EVIDENCE_KEYS statement: {extracted!r}"
    )


def test_extract_js_const_ignores_a_semicolon_inside_its_own_string_literal():
    """The raw-scanning failure mode a comment decoy doesn't exercise: the
    REAL statement widened so its array carries a `;` inside one of its own
    string entries. A raw-`;`-terminated scan truncates mid-string, splicing
    invalid JS -- a false RED, not a false GREEN. The projection blanks
    string literals before the terminator search runs, so the `;` inside
    the string is invisible to it."""
    real = _extract_js_const(_TEMPLATE_JS_SOURCE, "FAILURE_EVIDENCE_KEYS")
    assert real in _TEMPLATE_JS_SOURCE, (
        "non-vacuity: the real FAILURE_EVIDENCE_KEYS statement must actually "
        "be present in the shipped template before mutating it"
    )
    assert real.endswith('"stderr"];'), (
        f"unexpected FAILURE_EVIDENCE_KEYS shape, update this test: {real!r}"
    )
    widened = real[:-len('"stderr"];')] + '"stderr", "a;b"];'
    mutated = _TEMPLATE_JS_SOURCE.replace(real, widened, 1)

    extracted = _extract_js_const(mutated, "FAILURE_EVIDENCE_KEYS")

    assert "exit_code" in extracted, (
        "extraction truncated mid-string at the `;` inside \"a;b\" instead "
        f"of the statement's real terminating `;`: {extracted!r}"
    )
    assert extracted.endswith('"a;b"];'), (
        f"extraction did not reach the statement's real end: {extracted!r}"
    )


# ---------------------------------------------------------------------------
# 9. --durable-root PATH (LT-409): an explicit, caller-supplied root that
#    REPLACES self-anchoring when given, byte-identical to today's
#    self-anchored behavior when omitted.
# ---------------------------------------------------------------------------

def run_ledger_update_from(script_path, seg, payload_path, *extra_args):
    return subprocess.run(
        [sys.executable, str(script_path), seg, "--payload-file", str(payload_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_segpack_fixture(root, seg):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps({"blocks": [{"id": "b1"}], "footnotes": [], "verses": []}),
        encoding="utf-8",
    )


def write_draft_fixture(root, seg, dispatch_token=None):
    doc = {"seg": seg, "blocks": {"b1": "hello"}}
    if dispatch_token is not None:
        doc["dispatch_token"] = dispatch_token
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(doc), encoding="utf-8")
    # Independent (not-reimplemented-from-the-script) canonical content sha1,
    # matching draft_sha1.py's/ledger_update.py's own dispatch_token-excluded
    # algorithm -- mirrors draft_sha1.test.py's own canonical_expected_sha1().
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_review_fixture(root, seg, draft_sha1_value):
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps({"draft_sha1": draft_sha1_value}), encoding="utf-8"
    )


def test_durable_root_flag_redirects_in_progress_write(tmp_path):
    """ledger_update.py's own copy lives ALONE -- no schemas/, no runs/, no
    segments/ anywhere near it -- self-anchoring alone cannot possibly
    succeed. --durable-root pointing at a SEPARATE, real fixture root must
    make the write land at THAT root's runs/ledger.d/{seg}.json."""
    real_root = make_durable_root(tmp_path)
    seg = "segRedirect"
    payload_path = write_payload(real_root, "pRedirect", {"status": "in_progress"})

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_update.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_ledger_update_from(
        orphan_script, seg, payload_path, "--durable-root", str(real_root)
    )

    assert result.returncode == 0, (
        f"--durable-root must redirect path resolution to the given root, "
        f"regardless of the script's own on-disk location -- got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    fragment = read_fragment(real_root, seg)
    assert fragment["status"] == "in_progress"


def test_durable_root_flag_redirects_converged_enrich(tmp_path):
    """The 'converged' status path (enrich_converged_fields) reads THREE
    separate on-disk files -- segpack, draft, review -- all via paths this
    script derives from its durable_root. This is the function with the
    most path derivation, so it gets its own dedicated redirect proof
    rather than trusting the simpler in_progress case above to cover it."""
    real_root = make_durable_root(tmp_path)
    seg = "segConverged"
    write_segpack_fixture(real_root, seg)
    draft_sha1_value = write_draft_fixture(real_root, seg)
    write_review_fixture(real_root, seg, draft_sha1_value)
    payload_path = write_payload(
        real_root,
        "pConverged",
        {"status": "converged", "cache_key": FULL_CACHE_KEY, "rounds": 1},
    )

    orphan_dir = tmp_path / "orphan_location2" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_update.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_ledger_update_from(
        orphan_script, seg, payload_path, "--durable-root", str(real_root)
    )

    assert result.returncode == 0, (
        f"converged status via --durable-root must read segpack/draft/review "
        f"from the GIVEN root -- got rc={result.returncode}\nstdout:\n"
        f"{result.stdout}\nstderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    assert stdout["status"] == "converged"
    fragment = read_fragment(real_root, seg)
    assert fragment["reviewed_draft_sha1"] == draft_sha1_value
    assert fragment["n_blocks"] == 1
    assert fragment["n_footnotes"] == 0
    assert fragment["n_verses"] == 0


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: the orphan copy, invoked WITHOUT --durable-root,
    cannot succeed via self-anchoring. Asserts the SPECIFIC reason -- no
    schemas/ dir to load ledger-record-base.schema.json from -- not merely
    that some failure occurred: a bare "it failed" cannot distinguish this
    correct refusal from an unrelated crash, so a future defect that broke
    the orphan-copy path for the WRONG reason would pass this test
    silently."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_update.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)
    payload_path = orphan_dir.parent / "scratch_payload.json"
    payload_path.write_text(json.dumps({"status": "in_progress"}), encoding="utf-8")

    result = run_ledger_update_from(orphan_script, "segX", payload_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "schema file not found" in (payload.get("error") or "").lower(), (
        f"expected the orphan copy to fail specifically on its missing "
        f"schemas/ directory; got a different reason: {payload}"
    )


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --durable-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)
    seg = "segNoFlag"
    payload_path = write_payload(root, "pNoFlag", {"status": "in_progress"})

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is True
    assert read_fragment(root, seg)["status"] == "in_progress"


# ---------------------------------------------------------------------------
# 10. mark_ever_converged() failure is FATAL to recording convergence
#     (post-review correction, LT-409).
# ---------------------------------------------------------------------------

def test_sentinel_write_failure_refuses_to_record_convergence(tmp_path):
    """When the sentinel write inside mark_ever_converged() fails, the whole
    'converged' write must be refused -- no ledger fragment written at all --
    rather than recording convergence anyway. A fragment recorded as
    'converged' without its sentinel is invisible to the one check that
    refuses to re-select and retranslate an already-converged segment, so
    failing OPEN here (the pre-fix behavior) is the dangerous direction. The
    positive control -- convergence recorded successfully WITH its sentinel
    -- is test_durable_root_flag_redirects_converged_enrich above.

    ledger_update.py runs as a real subprocess here, so there is no
    in-process call to monkeypatch (the way an in-process test would patch
    around an unreliable chmod, per skeptic_ready.test.py's own precedent).
    The failure is induced at the OS level instead: segments/ is made
    read-only so os.open()'s O_CREAT cannot create the new sentinel file.
    Guarded with a runtime write-probe, not just a geteuid()==0 check --
    some sandboxes/containers ignore permission bits even for a non-root
    user, and a false negative here would silently degrade this into
    testing nothing.
    """
    root = make_durable_root(tmp_path)
    seg = "segSentinelFail"
    write_segpack_fixture(root, seg)
    draft_sha1_value = write_draft_fixture(root, seg)
    write_review_fixture(root, seg, draft_sha1_value)
    payload_path = write_payload(
        root, "pSentinelFail",
        {"status": "converged", "cache_key": FULL_CACHE_KEY, "rounds": 1},
    )

    segments_dir = root / "segments"
    original_mode = segments_dir.stat().st_mode
    segments_dir.chmod(0o555)  # read + execute only -- no new entry creatable

    probe_path = segments_dir / ".write_probe"
    try:
        probe_path.touch()
    except PermissionError:
        blocked = True
    else:
        probe_path.unlink()
        blocked = False

    if not blocked:
        segments_dir.chmod(original_mode)
        pytest.skip(
            "segments/ chmod 0o555 did not actually block file creation -- "
            "running as root or in a sandbox that ignores permission bits; "
            "cannot exercise the sentinel-write-failure path this way here"
        )

    try:
        result = run_ledger_update(root, seg, payload_path)
    finally:
        segments_dir.chmod(original_mode)  # restore for pytest's tmp_path cleanup

    assert result.returncode != 0, (
        f"a sentinel write failure must refuse the whole write, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is False
    assert "sentinel" in stdout["error"].lower(), stdout["error"]
    # Failure shapes never claim a fragment_path/fragment_sha1 that was
    # never written.
    assert "fragment_path" not in stdout
    assert "fragment_sha1" not in stdout
    assert not (root / "runs" / "ledger.d" / f"{seg}.json").exists(), (
        "no ledger fragment may be written for a 'converged' status whose "
        "sentinel could not be created -- recording convergence without its "
        "sentinel is exactly the unprotected state this fix closes"
    )
    assert not (segments_dir / f".ever_converged.{seg}").exists()

    # Post-review correction: the STDERR warning from mark_ever_converged()
    # itself must describe what NOW happens (refused, nothing lost), never
    # the pre-correction fail-open story -- an operator reads stderr first,
    # during the incident, at the moment they're deciding whether anything
    # needs recovering. The message used to say the opposite of what this
    # test's own assertions above just proved.
    stderr_lower = result.stderr.lower()
    assert "was not recorded" in stderr_lower, result.stderr
    assert "nothing on disk was lost" in stderr_lower, result.stderr
    assert "convergence is recorded" not in stderr_lower, (
        f"stderr must not claim convergence IS recorded -- that was the "
        f"pre-correction fail-open story this fix exists to prevent, and "
        f"telling an operator their work is safely recorded when the ledger "
        f"write was actually refused is worse than saying nothing: "
        f"{result.stderr!r}"
    )


def _load_module(name, path):
    """Imports a script as an in-process module -- the established pattern
    for direct-function-call unit testing elsewhere in this test suite
    (e.g. resume_integrity.test.py's own `_load_module`, used there for the
    identical reason: forcing a specific failure deterministically needs a
    patchable in-process call, which a subprocess boundary would hide)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_failure_after_sentinel_create_is_reported_as_a_clean_refusal(tmp_path):
    """mark_ever_converged()'s O_CREAT|O_EXCL open() PUBLISHES the sentinel's
    NAME in segments/ before the single os.write() that fills it in ever
    runs. Pre-fix, an OSError from that write (or from the os.close() that
    follows it -- some filesystems, notably NFS, defer reporting a write
    error until close()) was OUTSIDE the try/except that produces this
    function's documented "clean False plus stderr explanation" contract --
    it propagated as an uncaught exception instead, on exactly the failure
    that contract exists for.

    Genuinely only reachable in-process: there is no portable, reliable way
    to make a REAL os.write() to a freshly os.open()'d fd fail on a normal
    filesystem without root/fuse/quota machinery (the write-time analogue of
    why this codebase's own case-10 TOCTOU tests call a true race "not
    practically unit-testable"). ledger_update.py is otherwise exercised
    exclusively via subprocess in this file (matching its own house style),
    but the one function under test here takes plain seg/segments_dir
    arguments and has no other side effects worth isolating a subprocess
    for, so a direct in-process call plus a narrow os.write() patch is the
    faithful way to reach this specific seam."""
    ledger_update = _load_module("ledger_update_under_test_write_failure", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segWriteFail"

    real_write = ledger_update.os.write
    real_stderr = ledger_update.sys.stderr

    def failing_write(fd, data):
        raise OSError(28, "No space left on device")  # ENOSPC

    captured_stderr = io.StringIO()
    ledger_update.os.write = failing_write
    ledger_update.sys.stderr = captured_stderr
    try:
        result = ledger_update.mark_ever_converged(seg, segments_dir)
    finally:
        ledger_update.os.write = real_write
        ledger_update.sys.stderr = real_stderr

    assert result is False, (
        "a write-time OSError must produce the SAME clean False an open-time "
        "OSError already does -- not an uncaught exception propagating past "
        "this function's own documented contract"
    )
    stderr_lower = captured_stderr.getvalue().lower()
    assert "could not create the ever-converged sentinel" in stderr_lower
    assert "was not recorded" in stderr_lower
    assert "nothing on disk was lost" in stderr_lower


def test_close_failure_after_successful_write_is_reported_as_a_clean_refusal(tmp_path):
    """Sibling of the write-failure test above, for the OTHER OS call this
    function makes after a successful open(): os.close(). Some filesystems
    (notably NFS) defer reporting a write error until close() specifically,
    so this is not a redundant echo of the write-failure case -- it is the
    one place a write can appear to have succeeded and still turn out to
    have failed."""
    ledger_update = _load_module("ledger_update_under_test_close_failure", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segCloseFail"

    real_close = ledger_update.os.close
    real_stderr = ledger_update.sys.stderr

    def failing_close(fd):
        raise OSError(5, "Input/output error")  # EIO, as e.g. NFS may defer

    captured_stderr = io.StringIO()
    ledger_update.os.close = failing_close
    ledger_update.sys.stderr = captured_stderr
    try:
        result = ledger_update.mark_ever_converged(seg, segments_dir)
    finally:
        ledger_update.os.close = real_close
        ledger_update.sys.stderr = real_stderr

    assert result is False, (
        "a close-time OSError must produce the SAME clean False an open- or "
        "write-time OSError already does -- not an uncaught exception"
    )
    stderr_lower = captured_stderr.getvalue().lower()
    assert "could not create the ever-converged sentinel" in stderr_lower
    assert "was not recorded" in stderr_lower
    assert "nothing on disk was lost" in stderr_lower


# ---------------------------------------------------------------------------
# 10b. EEXIST is not proof of prior marking (1.19.1 fail-closed correction).
#
# `os.open(O_CREAT|O_EXCL)` raises FileExistsError for ANY existing entry, not
# only for a regular sentinel this function previously published: a directory
# raises it, and so does a DANGLING SYMLINK. Pre-fix, the bare
# `except FileExistsError: return True` reported all of them as successfully
# marked. The dangling-symlink case is the data-loss one, because
# select_segments.py's reader disagreed about that same path -- `Path.exists()`
# follows the link and reports it ABSENT -- so the segment was recorded as
# converged while the dispatch gate saw it as unprotected and retranslated it
# on the next cache-key move. 1.19.1 moves plugin_bundle_hash for every
# converged segment in every live project, which is exactly that move.
#
# Both halves now route through classify_ever_converged_sentinel(), duplicated
# verbatim in the two scripts and drift-tested against each other in
# tests/select_segments.test.py.
# ---------------------------------------------------------------------------

def test_a_dangling_symlink_is_not_accepted_as_a_prior_marking(tmp_path):
    """FAILS on the unfixed code at `assert result is False` -- the pre-fix
    `except FileExistsError: return True` returns True here, and the entry a
    reader would look for was never published."""
    ledger_update = _load_module("ledger_update_under_test_dangling", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segDangling"

    link = segments_dir / f".ever_converged.{seg}"
    link.symlink_to(segments_dir / "no-such-target")
    assert link.is_symlink() and not link.exists(), (
        "precondition: a DANGLING link -- the entry exists (so O_CREAT|O_EXCL "
        "raises EEXIST) while Path.exists() reports absent, which is the "
        "disagreement under test"
    )

    captured_stderr = io.StringIO()
    real_stderr = ledger_update.sys.stderr
    ledger_update.sys.stderr = captured_stderr
    try:
        result = ledger_update.mark_ever_converged(seg, segments_dir)
    finally:
        ledger_update.sys.stderr = real_stderr

    assert result is False, (
        "EEXIST from a dangling symlink is not proof this function ever "
        "published a sentinel; returning True records convergence while the "
        "reader still sees the segment as unprotected"
    )
    assert link.is_symlink(), "the writer must not silently replace the entry"
    stderr_lower = captured_stderr.getvalue().lower()
    assert "symbolic link" in stderr_lower, (
        "the refusal must say what is actually at the path"
    )
    assert "was not recorded" in stderr_lower
    assert "nothing on disk was lost" in stderr_lower


def test_a_directory_is_not_accepted_as_a_prior_marking(tmp_path):
    """Same branch, other non-regular entry. FAILS on the unfixed code at
    `assert result is False` (pre-fix: True)."""
    ledger_update = _load_module("ledger_update_under_test_dir", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segDirEntry"
    (segments_dir / f".ever_converged.{seg}").mkdir()

    captured_stderr = io.StringIO()
    real_stderr = ledger_update.sys.stderr
    ledger_update.sys.stderr = captured_stderr
    try:
        result = ledger_update.mark_ever_converged(seg, segments_dir)
    finally:
        ledger_update.sys.stderr = real_stderr

    assert result is False
    assert "a directory" in captured_stderr.getvalue().lower()


def test_a_non_enoent_lstat_error_refuses_rather_than_recording_convergence(tmp_path):
    """The arm that motivated the finding, writer side: the entry exists as
    far as O_CREAT|O_EXCL is concerned, but examining it fails with something
    other than ENOENT (EACCES here; ESTALE on a stale NFS handle is the same
    shape). "Cannot tell" must refuse, never assume a valid sentinel.

    The two halves are induced separately because no single filesystem state
    produces both at once -- a directory this process cannot search makes
    os.open() itself fail with EACCES, short-circuiting the FileExistsError
    branch entirely. So os.open is patched to report EEXIST (what a racing
    peer or a stale handle looks like) while the lstat failure is REAL.

    FAILS on the unfixed code at `assert result is False`: the pre-fix branch
    never examines the entry at all and returns True."""
    ledger_update = _load_module("ledger_update_under_test_lstat_error", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    locked = segments_dir / "locked"
    locked.mkdir()
    seg = "segLstatError"
    (locked / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")

    real_open = ledger_update.os.open
    real_stderr = ledger_update.sys.stderr

    def eexist_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            # mark_ever_converged() now pins segments/ with its own
            # os.open(O_RDONLY|O_DIRECTORY) BEFORE it touches the entry, and
            # creates the sentinel relative to that descriptor. Only the
            # dir_fd-relative call is the sentinel create this fake stands
            # in for; the pin is passed through untouched. Keyed on dir_fd
            # rather than on the flags so that a future edit dropping
            # O_CREAT|O_EXCL cannot slip past by looking like the pin.
            return real_open(path, flags, mode)
        # The fake ASSERTS on the flags rather than ignoring them: this test's
        # premise is that EEXIST can only arise from an exclusive create, so a
        # future edit that dropped O_EXCL (and with it the whole race-free
        # publish) would otherwise leave this test green while testing a
        # branch the real code could no longer reach.
        assert flags & os.O_CREAT and flags & os.O_EXCL, (
            f"mark_ever_converged must still open the sentinel with "
            f"O_CREAT|O_EXCL; got flags={flags:#o}"
        )
        raise FileExistsError(17, "File exists")

    captured_stderr = io.StringIO()
    ledger_update.os.open = eexist_open
    ledger_update.sys.stderr = captured_stderr
    # 0o444, not 0o000: mark_ever_converged() now OPENS segments/ before it
    # touches the entry, and 0o000 would fail that open with EACCES before
    # the EEXIST branch this test exists for was ever reached -- the test
    # would still see result False and "eacces" on stderr and pass for
    # entirely the wrong reason. Read-without-search keeps the pin working
    # and still denies the name lookup inside, which is the condition under
    # test. Both halves are asserted below rather than assumed.
    locked.chmod(0o444)
    try:
        probe_pin = None
        try:
            probe_pin = os.open(str(locked), os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            pytest.skip(
                "this platform refuses O_RDONLY|O_DIRECTORY on a mode-0444 "
                "directory, so the pin cannot be made to succeed while the "
                "lookup inside it fails"
            )
        probe_blocked = True
        try:
            os.lstat(f".ever_converged.{seg}", dir_fd=probe_pin)
            probe_blocked = False
        except PermissionError:
            pass
        finally:
            os.close(probe_pin)
        if not probe_blocked:
            pytest.skip(
                "chmod 0o444 did not actually block the lookup -- running as "
                "root or in a sandbox that ignores permission bits; cannot "
                "induce a non-ENOENT lookup error this way here"
            )
        result = ledger_update.mark_ever_converged(seg, locked)
    finally:
        locked.chmod(0o755)
        ledger_update.os.open = real_open
        ledger_update.sys.stderr = real_stderr

    assert result is False, (
        "a lookup that FAILED is not a lookup that found a valid sentinel; "
        "recording convergence here asserts a protection nobody verified"
    )
    stderr_lower = captured_stderr.getvalue().lower()
    assert "eacces" in stderr_lower, "the errno must reach the operator"
    assert "was not recorded" in stderr_lower


def test_an_eexist_whose_entry_has_vanished_refuses_and_says_to_retry(tmp_path):
    """The remaining branch: os.open reports EEXIST and the follow-up lstat
    gets a clean ENOENT -- the entry was removed in between. Refusing (rather
    than looping) is the work-preserving call, and the operator-facing remedy
    differs from every other arm, so it is asserted rather than assumed.

    FAILS on the unfixed code at `assert result is False` (pre-fix: True, on
    a path where nothing whatsoever exists)."""
    ledger_update = _load_module("ledger_update_under_test_vanished", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segVanished"

    real_open = ledger_update.os.open
    real_stderr = ledger_update.sys.stderr

    def eexist_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            # mark_ever_converged() now pins segments/ with its own
            # os.open(O_RDONLY|O_DIRECTORY) BEFORE it touches the entry, and
            # creates the sentinel relative to that descriptor. Only the
            # dir_fd-relative call is the sentinel create this fake stands
            # in for; the pin is passed through untouched. Keyed on dir_fd
            # rather than on the flags so that a future edit dropping
            # O_CREAT|O_EXCL cannot slip past by looking like the pin.
            return real_open(path, flags, mode)
        # The fake ASSERTS on the flags rather than ignoring them: this test's
        # premise is that EEXIST can only arise from an exclusive create, so a
        # future edit that dropped O_EXCL (and with it the whole race-free
        # publish) would otherwise leave this test green while testing a
        # branch the real code could no longer reach.
        assert flags & os.O_CREAT and flags & os.O_EXCL, (
            f"mark_ever_converged must still open the sentinel with "
            f"O_CREAT|O_EXCL; got flags={flags:#o}"
        )
        raise FileExistsError(17, "File exists")

    captured_stderr = io.StringIO()
    ledger_update.os.open = eexist_open
    ledger_update.sys.stderr = captured_stderr
    try:
        result = ledger_update.mark_ever_converged(seg, segments_dir)
    finally:
        ledger_update.os.open = real_open
        ledger_update.sys.stderr = real_stderr

    assert result is False
    stderr_lower = captured_stderr.getvalue().lower()
    assert "vanished" in stderr_lower
    assert "just retry" in stderr_lower


def test_an_existing_regular_sentinel_is_still_idempotently_accepted(tmp_path):
    """FALSE-POSITIVE BOUND, and the one that matters most: the fix must not
    turn the idempotent re-record path into a refusal. Every converged segment
    in every live project re-enters this function on a re-record, so an
    over-strict predicate here would refuse work that is genuinely protected.

    Green both before and after the fix by design -- it exists to catch a fix
    that over-blocks, which the discriminating tests above cannot see."""
    ledger_update = _load_module("ledger_update_under_test_idempotent", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    assert ledger_update.mark_ever_converged("segFresh", segments_dir) is True, (
        "a clean create must still succeed"
    )
    assert ledger_update.mark_ever_converged("segFresh", segments_dir) is True, (
        "and calling it again against the regular file it just wrote must "
        "still be an idempotent no-op"
    )
    sentinel = segments_dir / ".ever_converged.segFresh"
    body = json.loads(sentinel.read_text(encoding="utf-8"))
    assert sentinel.is_file()
    assert (body["marker"], body["v"], body["by"]) == ("ever_converged", 1, "ledger_update"), (
        f"#443: the marker must name the writer that earned it, got {body!r}"
    )


def test_a_dangling_symlink_sentinel_refuses_the_whole_converged_write(tmp_path):
    """The end-to-end consequence, through the real CLI: a 'converged' payload
    whose sentinel path holds a dangling symlink must be refused outright, with
    NO ledger fragment written. Sibling of
    test_sentinel_write_failure_refuses_to_record_convergence above, which
    covers the same refusal for an un-creatable sentinel.

    This is the test that pins the data-loss behavior rather than the helper's
    return value. FAILS on the unfixed code at `assert result.returncode != 0`:
    pre-fix mark_ever_converged() returns True for the dangling link, so the
    fragment is written as 'converged' and the process exits 0 -- a segment
    recorded as protected that the dispatch gate will happily retranslate."""
    root = make_durable_root(tmp_path)
    seg = "segDanglingE2E"
    write_segpack_fixture(root, seg)
    draft_sha1_value = write_draft_fixture(root, seg)
    write_review_fixture(root, seg, draft_sha1_value)
    payload_path = write_payload(
        root, "pDanglingE2E",
        {"status": "converged", "cache_key": FULL_CACHE_KEY, "rounds": 1},
    )

    segments_dir = root / "segments"
    link = segments_dir / f".ever_converged.{seg}"
    link.symlink_to(segments_dir / "no-such-target")

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode != 0, (
        f"a sentinel path occupied by a dangling symlink must refuse the whole "
        f"write, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is False
    assert "sentinel" in stdout["error"].lower(), stdout["error"]
    assert not (root / "runs" / "ledger.d" / f"{seg}.json").exists(), (
        "no ledger fragment may be written for a 'converged' status whose "
        "sentinel is not actually in place -- that is precisely the "
        "looks-done-but-unprotected state the sentinel exists to prevent"
    )
    assert link.is_symlink(), "the entry the operator has to fix must survive"
    assert "symbolic link" in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# 11. now_iso8601()'s exact output shape -- the "house format" other scripts'
#     own copies are named after (e.g. backfill_resume_gate_ack.py), pinned
#     here since nothing else in this file asserts on the timestamp field's
#     format at all: a mutation widening timespec to milliseconds would pass
#     every other test silently (still schema-valid ISO-8601, nothing
#     downstream refuses it).
# ---------------------------------------------------------------------------

_ISO8601_WHOLE_SECOND_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_fragment_timestamp_is_whole_second_iso8601_with_bare_z(tmp_path):
    """Pins now_iso8601()'s exact shape via the real fragment it writes:
    whole-second precision (timespec='seconds', no fractional digits) and a
    bare 'Z' suffix (never a '+00:00' offset)."""
    root = make_durable_root(tmp_path)
    seg = "segTimestampFormat"
    payload_path = write_payload(root, "pTimestampFormat", {"status": "in_progress"})

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0
    fragment = read_fragment(root, seg)
    assert _ISO8601_WHOLE_SECOND_Z_RE.match(fragment["timestamp"]), (
        f"fragment timestamp {fragment['timestamp']!r} must be exactly "
        f"whole-second ISO-8601 with a bare 'Z' suffix -- the house format "
        f"other scripts' own now_iso8601() copies are named after"
    )


if __name__ == "__main__":
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# #443. The sentinel used to be ten fixed bytes, identical whoever wrote it.
# These lock the provenance it carries now -- and, first, that carrying it
# changed nothing about what the marker PROTECTS.
# ---------------------------------------------------------------------------

SENTINEL_LEGACY_BODY = b"converged\n"


def _sentinel_body_of(root, seg):
    return (root / "segments" / f".ever_converged.{seg}").read_bytes()


def _converged_run(tmp_path, seg, *, run_token=None, round_label="2"):
    """Drive the REAL script through a genuine convergence and return
    (root, draft_sha1, completed_process). Not a hand-built marker: the whole
    point of #443 is what the writer records about a convergence it actually
    performed, so a fixture that wrote the body itself would measure the
    fixture."""
    root = make_durable_root(tmp_path)
    write_segpack_fixture(root, seg)
    draft_token = f"{run_token}:{seg}" if run_token is not None else None
    draft_sha1_value = write_draft_fixture(root, seg, dispatch_token=draft_token)
    review = {"draft_sha1": draft_sha1_value}
    if draft_token is not None:
        review["dispatch_token"] = f"{draft_token}:r{round_label}"
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps(review), encoding="utf-8"
    )
    payload = {"status": "converged", "cache_key": FULL_CACHE_KEY, "rounds": 1}
    if run_token is not None:
        payload["run_token"] = run_token
    payload_path = write_payload(root, f"p{seg}", payload)
    return root, draft_sha1_value, run_ledger_update(root, seg, payload_path)


def test_the_sentinel_records_the_evidence_this_convergence_actually_had(tmp_path):
    """#443's central pin. The marker must carry the run token, the round
    label and the reviewed draft sha1 of the convergence that wrote it.

    Each expected value is taken from what THIS run used -- the sha1 the
    fixture computed independently of the script, and the round label spelled
    into review.json's own dispatch_token -- rather than from a constant, so
    the test cannot pass by agreeing with a body the script invented.

    Fails on the pre-#443 writer at the json.loads(): the body was the ten
    bytes `converged\n`."""
    seg = "segProv"
    run_id = "RUN-20260823-abcdef"
    root, draft_sha1_value, result = _converged_run(
        tmp_path, seg, run_token=run_id, round_label="3"
    )

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    body = json.loads(_sentinel_body_of(root, seg).decode("utf-8"))
    assert body == {
        "marker": "ever_converged",
        "v": 1,
        "by": "ledger_update",
        "seg": seg,
        "run_token": run_id,
        "round": "3",
        "reviewed_draft_sha1": draft_sha1_value,
    }, body
    assert read_fragment(root, seg)["reviewed_draft_sha1"] == draft_sha1_value, (
        "the marker must agree with the fragment written in the same call -- "
        "a provenance that disagrees with the ledger is worse than none"
    )


def test_a_call_without_a_run_token_records_nothing_it_cannot_prove(tmp_path):
    """The pre-1.2.0 call shape carries no run_token, so there is no anchor to
    read a round label off. The marker must then record the one thing this run
    DID verify -- the reviewed draft sha1 -- and stay silent about the rest
    rather than inventing a token or splitting review.json's dispatch_token on
    a ':r' that could occur inside a run id or a segment id."""
    seg = "segNoToken"
    root, draft_sha1_value, result = _converged_run(tmp_path, seg, run_token=None)

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    body = json.loads(_sentinel_body_of(root, seg).decode("utf-8"))
    assert body == {
        "marker": "ever_converged",
        "v": 1,
        "by": "ledger_update",
        "seg": seg,
        "reviewed_draft_sha1": draft_sha1_value,
    }, body


def test_a_legacy_provenance_free_marker_still_protects_and_is_never_rewritten(tmp_path):
    """THE BACKWARD-COMPATIBILITY PIN, and the one that decides whether #443
    was safe to ship at all. Every marker on every live project predates this
    change and is the ten bytes `converged\n` -- 42 of them on the book that
    surfaced the issue. The convergence path must accept such a marker exactly
    as before (the sentinel write is a hard precondition, so a refusal here
    would refuse the whole converged write) and must not rewrite its body:
    create-only idempotence covers the content, not just the entry."""
    seg = "segLegacy"
    root = make_durable_root(tmp_path)
    legacy = root / "segments" / f".ever_converged.{seg}"
    legacy.write_bytes(SENTINEL_LEGACY_BODY)

    write_segpack_fixture(root, seg)
    draft_sha1_value = write_draft_fixture(root, seg)
    write_review_fixture(root, seg, draft_sha1_value)
    payload_path = write_payload(
        root, "pLegacy", {"status": "converged", "cache_key": FULL_CACHE_KEY, "rounds": 1}
    )

    result = run_ledger_update(root, seg, payload_path)

    assert result.returncode == 0, (
        f"a provenance-free marker must still satisfy the sentinel "
        f"precondition -- refusing here would make #443 reclassify every "
        f"marker in existence; stderr={result.stderr!r}"
    )
    assert json.loads(result.stdout.strip())["status"] == "converged"
    assert legacy.read_bytes() == SENTINEL_LEGACY_BODY, (
        "the existing marker was rewritten -- mark_ever_converged() has never "
        "replaced or overwritten an entry it found, and adding a body must "
        "not change that"
    )


def test_evidence_cannot_forge_the_writer_owned_identity_fields(tmp_path):
    """`provenance` is the CALLER's evidence, and a direct in-process caller
    is a supported shape. It must not be able to sign the marker as the other
    writer, rename the marker, move the version, or claim a different segment
    -- otherwise the attribution #443 adds is forgeable by the very code path
    it exists to tell apart."""
    module = _load_module("ledger_update_forge", SCRIPT_SRC)
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segForge"

    assert module.mark_ever_converged(seg, segments_dir, {
        "by": "backfill_ever_converged",
        "marker": "something_else",
        "v": 99,
        "seg": "a_different_segment",
        "reviewed_draft_sha1": "deadbeef",
    }) is True

    body = json.loads(
        (segments_dir / f".ever_converged.{seg}").read_text(encoding="utf-8")
    )
    assert (body["by"], body["marker"], body["v"], body["seg"]) == (
        "ledger_update", "ever_converged", 1, seg
    ), body
    assert body["reviewed_draft_sha1"] == "deadbeef", (
        "the evidence fields themselves are still the caller's to supply"
    )


def test_write_all_finishes_a_short_write_and_refuses_a_zero_length_one(tmp_path):
    """The body is an order of magnitude longer than the ten bytes the old
    single `os.write()` published, so a short write stops being a theoretical
    concern about a value the call site ignored.

    Both directions are pinned. A short write must be RESUMED until the body
    is out; a zero-byte return must RAISE rather than be looped on, because
    spinning there would hang the ledger writer -- strictly worse than the
    clean refusal every other write failure gets."""
    module = _load_module("ledger_update_write_all", SCRIPT_SRC)

    written = []
    real_write = module.os.write

    def short_write(fd, data):
        # One byte at a time: if write_all() trusted a single call's return
        # value the body would be truncated to one byte.
        written.append(len(data))
        return real_write(fd, bytes(data[:1]))

    target = tmp_path / "out.bin"
    payload = b'{"marker":"ever_converged"}\n'
    fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o644)
    module.os.write = short_write
    try:
        module.write_all(fd, payload)
    finally:
        module.os.write = real_write
        os.close(fd)

    assert target.read_bytes() == payload
    assert len(written) == len(payload), (
        f"write_all() must keep going until the body is out; it made "
        f"{len(written)} call(s) for {len(payload)} byte(s)"
    )

    module.os.write = lambda fd, data: 0
    try:
        fd = os.open(str(tmp_path / "zero.bin"), os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            with pytest.raises(OSError):
                module.write_all(fd, payload)
        finally:
            os.close(fd)
    finally:
        module.os.write = real_write


ROUND_LABEL_CASES = [
    ("numeric", "RUN-1", "segA", "RUN-1:segA:r3", "3"),
    ("the terminal label", "RUN-1", "segA", "RUN-1:segA:rfinal", "final"),
    # ':r' occurring INSIDE the run id and inside the segment id. A reader
    # that searched for ':r' instead of splitting by the length of the token
    # already accepted would cut here and record a label never dispatched.
    ("':r' inside the run id", "RUN:right", "segA", "RUN:right:segA:r2", "2"),
    ("':r' inside the segment id", "RUN-1", "seg:rear", "RUN-1:seg:rear:r2", "2"),
    ("a token for another segment", "RUN-1", "segA", "RUN-1:segB:r2", None),
    ("no round suffix at all", "RUN-1", "segA", "RUN-1:segA", None),
    ("not a string", "RUN-1", "segA", 7, None),
    ("absent", "RUN-1", "segA", None, None),
]


@pytest.mark.parametrize("label,run,seg,token,expected", ROUND_LABEL_CASES,
                         ids=[case[0] for case in ROUND_LABEL_CASES])
def test_the_round_label_is_split_by_length_not_by_searching_for_a_marker(
    label, run, seg, token, expected
):
    """#443 records the round a segment converged at, read off review.json's
    own `'<draft_token>:r<roundLabel>'`. The split is by the LENGTH of the
    token review_token_matches() already accepted -- these cases are why. Two
    of them put ':r' inside the run id and inside the segment id, where a
    search-based split records a label that was never dispatched; recording a
    wrong round in the marker is worse than recording none, because the whole
    value of the field is that it can be checked against the run."""
    module = _load_module("ledger_update_round_" + label.replace(" ", "_"), SCRIPT_SRC)
    review = {} if token is None else {"dispatch_token": token}
    expected_token = module.expected_draft_token(run, seg)
    assert module._review_round_label(review, expected_token) == expected


def test_the_round_label_is_bounded_so_the_marker_cannot_be_grown_through_it(tmp_path):
    """The label is the one field whose LENGTH is not fixed by this script.
    It reaches the marker only through a token the checks above accepted, so
    the cap is a bound on the marker's SIZE rather than a validation -- the
    census reads one body per segment and nothing downstream should have to
    defend against an unbounded one."""
    module = _load_module("ledger_update_round_cap", SCRIPT_SRC)
    expected_token = module.expected_draft_token("RUN-1", "segA")
    long_label = "9" * 500
    got = module._review_round_label(
        {"dispatch_token": f"{expected_token}:r{long_label}"}, expected_token
    )
    assert got == "9" * module._MAX_ROUND_LABEL
    assert module._review_round_label({"dispatch_token": expected_token + ":r"},
                                      expected_token) is None, (
        "an EMPTY label is not a label -- recording '' would put a field in "
        "the marker that says nothing while looking like evidence"
    )


def test_no_run_token_means_no_round_label_even_with_a_review_token_present(tmp_path):
    """The pre-1.2.0 call shape has no anchor to split against. A review
    artifact may still carry a dispatch_token, and guessing where the prefix
    ends is exactly the search-based split the cases above rule out."""
    module = _load_module("ledger_update_round_noanchor", SCRIPT_SRC)
    assert module._review_round_label({"dispatch_token": "RUN-1:segA:r3"}, None) is None
