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
import json
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
    "async function foo("), through its matching closing brace. Tracks
    single/double-quoted string state (with backslash-escape handling) and
    skips `//` line comments, so braces inside string literals or comments
    don't unbalance the count. Raises if the prefix isn't found or braces
    never balance -- both are meant to fail the test loudly, not silently
    degrade to a truncated/garbage extraction."""
    idx = source.index(signature_prefix)
    open_brace = source.index("{", idx)
    depth = 0
    i = open_brace
    in_str = None
    escape = False
    while i < len(source):
        c = source[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
        else:
            if c in ("\"", "'"):
                in_str = c
            elif c == "/" and i + 1 < len(source) and source[i + 1] == "/":
                i = source.index("\n", i)
                continue
            elif c == "{":
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
    declarations that aren't function bodies."""
    idx = source.index(f"const {const_name} ")
    end = source.index(";", idx)
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


if __name__ == "__main__":
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))
