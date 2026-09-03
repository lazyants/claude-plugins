"""tests/canon_harmonisation.test.py -- scripts/canon_harmonisation.py, the
structural gate (--check) and advisory report (--report) for
canon_harmonisation.json, the whole-canon target-form harmonisation sidecar
(#823). See that script's own module docstring and
references/canon-and-glossary.md for the full contract this file asserts
against.

## Fixture strategy

Every test builds an isolated `durable_root` on disk: the REAL
canon_harmonisation.py, the REAL json_stdout.py, and the REAL
canon-harmonisation.schema.json copied into `{root}/scripts/` /
`{root}/schemas/`, so the script's self-anchored `SCRIPTS_DIR =
Path(__file__).resolve().parent` / `DURABLE_ROOT = SCRIPTS_DIR.parent`
resolves against the fixture exactly as it does in production -- never
this repo's real assets tree. Invoked via `sys.executable`, an explicit
`timeout=`, `capture_output=True, text=True`, exactly as
`tests/canon_adjudication_audit.test.py`/`tests/audit_unchanged_regression
.test.py` invoke their own subjects.

Assertions target the CONTRACT: exit code, the parsed stdout SUMMARY
object (asserted field-by-field against the frozen shape in the module
docstring -- a DIFFERENT object from the sidecar artifact itself), stderr
non-emptiness/content where the contract promises a named refusal, and
jsonschema-validation of hand-built sidecar FIXTURES against the real
`canon-harmonisation.schema.json`. Factory helpers (`canon_entry`,
`proposal`, `member`, `harmonisation_doc`) return dicts each test perturbs
in exactly one place.

## Coverage (mirrors the frozen plan's own case list)

  - --check exit 0: a two-member divergent_spelling proposal; proposals:[].
  - --check --approve-to: publishes on PASS; leaves DEST untouched (absent,
    or byte-identical if pre-existing) on every exit-1/exit-2 path.
  - --check exit 1, schema failures (a): blank/whitespace note, unknown
    kind, missing required field, stray additional property -- each names
    the DOCUMENT, never a proposal index.
  - --check exit 1, anchor failure (b): canon_sha256 mismatch -- names the
    document.
  - --check exit 1, proposal failures (c)-(g): unknown source_form; an
    NFC-vs-NFD near-miss (MUST refuse -- byte-exact membership only);
    misquoted canonical_target_form; a single-member proposal; a duplicate
    source_form inside one proposal; all members sharing one target form;
    a duplicate proposal -- each names the offending proposal INDEX.
  - --check exit 2: canon.json absent; PATH is a directory (unreadable);
    schemas dir absent. No stdout JSON on any exit-2 (or exit-1) path.
  - --report exit 0: with proposals; with proposals:[] (the "not a
    certificate" wording); with canon_current:false after canon.json
    changes underneath the artifact; with a REMOVED member.
  - --report exit 2 on a schema-invalid artifact, no stdout JSON.
  - Usage errors (neither/both of --check/--report): exit 2, no stdout.
  - ONE-LINE STDOUT under a U+2028 boundary character. The frozen CLI
    contract's stdout summary carries no free-text `note` field at all
    (see the module docstring: only proposals_count/entries_in_canon/
    canon_sha256/approved_to/canon_current ever reach stdout) -- so this
    suite exercises json_stdout's escape through the one stdout field that
    IS caller-supplied free text, `approved_to` (a --approve-to DEST path),
    rather than through `note` as sibling suites do for their own JSON
    fields. See `test_check_stdout_one_physical_line_with_boundary_char_in_
    approved_to_path` for the full reasoning.
  - NON-INTERFERENCE: a canon_harmonisation.json sitting in the durable
    root leaves canon_validate.py (validate-only) and
    canon_adjudication_audit.py --check byte-identical (mod `generated_at`)
    to a run without it -- modeled on
    tests/audit_unchanged_regression.test.py.
  - RTL / multiword / apostrophe-bearing forms round-trip through both
    modes.

## Coverage added by the #823 code-review round

  - Finding 1 (MAJOR): a hostile `note` -- newline, U+2028, U+0085, and a
    full counterfeit --correct skeleton, every boundary character built
    via chr() -- can never forge an extra top-level '{' line or a second
    "--correct skeleton(s)" marker; the whole note collapses into one
    escaped physical line.
  - Finding 2 (MAJOR): the printed --correct skeleton's own
    `new_entry.canonical_target_form` is `null`, not a string placeholder
    -- proven fail-closed by extracting the REAL printed skeleton verbatim
    and running the shipped canon_validate.py --correct against it (never
    a re-implementation of its schema check).
  - Finding 3 (MINOR): an os.replace() failure during --approve-to (forced
    for real -- DEST is an existing directory, never a filesystem mock)
    does not orphan the `.{dest.name}.tmp.*` temp file.
  - Finding 4 (NIT): a missing scripts/ dependency (json_stdout.py absent)
    exits 2, this plugin's fatal convention, not 1 (gate-fail).
  - Finding 5 (NIT): --report, documented read-only, never writes a
    scripts/__pycache__ bytecode cache.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_harmonisation.py"
SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-harmonisation.schema.json"
CORPUS_SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-harmonisation-corpus.schema.json"
assert SCRIPT_SRC.is_file(), f"canon_harmonisation.py not found at {SCRIPT_SRC}"
assert SCHEMA_SRC.is_file(), f"canon-harmonisation.schema.json not found at {SCHEMA_SRC}"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

HARMONISATION_SCHEMA = json.loads(SCHEMA_SRC.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def make_durable_root(root: Path) -> Path:
    """The ordinary fixture: canon_harmonisation.py + json_stdout.py +
    canon-harmonisation.schema.json staged exactly as Step 0a stages them
    in production. No canon.json yet -- each test writes its own via
    write_canon()."""
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "canon_harmonisation.py")
    shutil.copy2(SCRIPTS_SRC_DIR / "json_stdout.py", scripts_dir / "json_stdout.py")
    shutil.copy2(SCHEMA_SRC, schemas_dir / "canon-harmonisation.schema.json")
    shutil.copy2(
        CORPUS_SCHEMA_SRC, schemas_dir / "canon-harmonisation-corpus.schema.json")
    return root


def make_durable_root_without_schemas(root: Path) -> Path:
    """A fixture root with the script staged but NO schemas/ directory at
    all -- for the "schemas dir absent" exit-2 case. canon.json is written
    separately by the caller so _load_canon() (which runs before the
    schemas dir is ever touched) succeeds first."""
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "canon_harmonisation.py")
    shutil.copy2(SCRIPTS_SRC_DIR / "json_stdout.py", scripts_dir / "json_stdout.py")
    return root


def run_harmonisation(root: Path, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_harmonisation.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def assert_no_stdout(proc) -> None:
    assert proc.stdout == "", f"expected no stdout at all, got:\n{proc.stdout!r}"


def canon_entry(source_form, target, is_proper_name=True, basis="transliterated"):
    """A minimal canon.json entries{} record -- canon_harmonisation.py only
    ever reads canonical_target_form off it, so this stays deliberately
    thin (unlike canon_validate.py's own fixtures, which need the full
    canon-entry.schema.json shape). `confidence: "high"` is included anyway
    -- harmless extra field for this script, and it lets the SAME helper
    double as a canon_validate.py-clean fixture in the non-interference
    test below."""
    return {
        "source_form": source_form,
        "canonical_target_form": target,
        "is_proper_name": is_proper_name,
        "basis": basis,
        "confidence": "high",
    }


def write_canon(root: Path, entries: list) -> bytes:
    """Writes {root}/canon.json from a list of canon_entry() dicts (keyed by
    their own source_form) and returns the EXACT bytes written, so a test
    can compute canon_harmonisation.py's own sha256 over them without
    re-deriving canon.json's serialisation."""
    keyed = {e["source_form"]: e for e in entries}
    doc = {
        "entries": keyed,
        "review_queue": [],
        "generation_hashes": {"particle_config_hash": "test-hash", "derivation_bundle_hash": "test-hash"},
    }
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    (root / "canon.json").write_bytes(raw)
    return raw


def canon_sha256_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def member(source_form, canonical_target_form, corpus="canon", **extra) -> dict:
    """One proposal member. `corpus` defaults to "canon" because that is what
    every pre-existing test in this file is about; a draft or candidate member
    additionally carries n_segments / freq, which --check verifies against the
    corpus observation it matched."""
    row = {
        "corpus": corpus,
        "source_form": source_form,
        "canonical_target_form": canonical_target_form,
    }
    row.update(extra)
    return row


def observation(source_form, target_form, corpus="canon", **extra) -> dict:
    """One corpus-file observation -- the thing a member must byte-match on
    all three of (corpus, source_form, target_form)."""
    row = {"corpus": corpus, "source_form": source_form, "target_form": target_form}
    row.update(extra)
    return row


def corpus_doc(canon_sha256, observations, candidates_source="bootstrap",
               converged_segments=0, drafts_excluded_stale_review=0,
               draft_rows_skipped=0,
               generated_at="2026-01-01T00:00:00+00:00") -> dict:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "canon_sha256": canon_sha256,
        "candidates_source": candidates_source,
        "converged_segments": converged_segments,
        "drafts_excluded_stale_review": drafts_excluded_stale_review,
        "draft_rows_skipped": draft_rows_skipped,
        "observations": observations,
    }


def write_corpus(root: Path, doc: dict, name="corpus_1.json"):
    """Writes a corpus file under {root}/harmonisation/ and returns
    (path, sha256_of_its_exact_bytes). The digest is what the SESSION would
    hold: --check compares it, the file's current bytes, and the artifact's
    own corpus_sha256, and all three must agree."""
    path = root / "harmonisation" / name
    raw = write_json(path, doc)
    return path, hashlib.sha256(raw).hexdigest()


def observations_for(entries: list) -> list:
    """The canon observations a corpus would carry for these canon entries."""
    return [observation(e["source_form"], e["canonical_target_form"]) for e in entries]


def proposal(kind, members, note="A harmonisation note explaining the identity call.") -> dict:
    return {"kind": kind, "members": members, "note": note}


def harmonisation_doc(canon_sha256, proposals, corpus_sha256="c" * 64,
                      generated_at="2026-01-01T00:00:00+00:00") -> dict:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "canon_sha256": canon_sha256,
        "corpus_sha256": corpus_sha256,
        "proposals": proposals,
    }


def write_json(path: Path, doc: dict) -> bytes:
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _extract_first_correct_skeleton(stderr: str) -> str:
    """Pulls the FIRST --correct skeleton block out of --report's stderr,
    as raw text -- the exact bytes _render_report() printed, never a
    re-serialisation. _render_report() prints each skeleton via
    `json.dumps(skeleton, indent=2, ...)` as one `lines.append()` element,
    so within the joined report text the block's own outer braces are the
    only "{" / "}" lines at COLUMN ZERO (every nested value is indented by
    at least 2 spaces); this walks physical lines looking for exactly
    that. Used to prove a test feeds canon_validate.py --correct what the
    script actually printed, not a hand-built stand-in."""
    lines = stderr.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln == "{"), None)
    assert start is not None, f"no --correct skeleton found in stderr:\n{stderr}"
    end = next((j for j in range(start + 1, len(lines)) if lines[j] == "}"), None)
    assert end is not None, f"unterminated --correct skeleton block in stderr:\n{stderr}"
    return "\n".join(lines[start:end + 1])


def check_attempt(root: Path, doc: dict, corpus_path, corpus_sha, *extra,
                  name="attempt_1.json", stamp=True):
    """Writes `doc` as a per-attempt artifact and runs --check against it with
    the corpus arguments every real invocation carries. Returns (attempt_path,
    CompletedProcess)."""
    attempt = root / "harmonisation" / name
    if stamp:
        doc = {**doc, "corpus_sha256": corpus_sha}
    write_json(attempt, doc)
    proc = run_harmonisation(
        root, "--check", str(attempt),
        "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha,
        *extra,
    )
    return attempt, proc


def two_member_fixture(root: Path):
    """The ordinary happy-path fixture: two canon entries under diverging
    canonical_target_forms, and a matching, correctly-anchored two-member
    divergent_spelling proposal naming both. Returns
    (canon_sha256, entries_list, artifact_doc, corpus_path, corpus_sha256).
    The corpus carries one observation per canon entry, which is what every
    --check in this file is validated against."""
    entries = [
        canon_entry("בָאבְרִינִצֶער", "Mordechai Babrinitzer"),
        canon_entry("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
    ]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal(
            "divergent_spelling",
            [
                member("בָאבְרִינִצֶער", "Mordechai Babrinitzer"),
                member("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
            ],
            note="One byname, two vowelisations of the same place-name base.",
        ),
    ])
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc["corpus_sha256"] = corpus_sha
    return csha, entries, doc, corpus_path, corpus_sha


# ===========================================================================
# --check exit 0
# ===========================================================================


def test_check_exit0_two_member_divergent_spelling_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary == {
        "success": True,
        "mode": "check",
        "proposals_count": 1,
        "entries_in_canon": 2,
        "canon_sha256": csha,
        "corpus_sha256": corpus_sha,
        "observations_in_corpus": 2,
        "candidates_source": "bootstrap",
        "converged_segments": 0,
        "drafts_excluded_stale_review": 0,
        "draft_rows_skipped": 0,
        "approved_to": None,
    }


def test_check_exit0_empty_proposals_nonzero_entries_in_canon(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Marie", "Marie"), canon_entry("Jean", "Jean")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc(csha, [], corpus_sha))

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha)
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["proposals_count"] == 0
    assert summary["entries_in_canon"] == 2
    assert summary["approved_to"] is None


# ===========================================================================
# --check --approve-to
# ===========================================================================


def test_check_approve_to_writes_validated_bytes_on_pass(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    raw = write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"
    assert not dest.exists()

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["approved_to"] == str(dest)
    assert dest.is_file()
    assert dest.read_bytes() == raw, "approve-to must publish the EXACT bytes read from PATH"


@pytest.mark.parametrize("preexisting", [False, True])
def test_check_approve_to_not_created_or_touched_on_exit1(tmp_path, preexisting):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    # Corrupt the anchor (b) so this is a clean exit-1 gate-fail, not exit 2.
    doc["canon_sha256"] = "0" * 64 if csha != "0" * 64 else "1" * 64
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"
    preexisting_bytes = b'{"stale": "snapshot"}'
    if preexisting:
        dest.write_bytes(preexisting_bytes)

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    if preexisting:
        assert dest.read_bytes() == preexisting_bytes
    else:
        assert not dest.exists()


def test_check_approve_to_not_created_on_exit2(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    # No canon.json at all -> exit 2 before the artifact is even read.
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    dest = root / "canon_harmonisation.json"

    # Any well-formed corpus: these cases fail before membership is ever
    # reached, so passing one keeps the test about the failure it names.
    corpus_path, corpus_sha = write_corpus(root, corpus_doc("0" * 64, []))
    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert not dest.exists()


def test_check_approve_to_os_replace_failure_does_not_orphan_temp_file(tmp_path):
    """#823 code review, finding 3 (MINOR): _atomic_publish()'s cleanup used
    to cover only the write/fsync step, not os.replace() itself (which runs
    AFTER that scope) -- a caught os.replace() failure left the
    `.{dest.name}.tmp.*` temp file behind in DEST's own directory. DEST
    stayed atomic and uncorrupted either way, but the "no orphan" property
    this function's own docstring claims was false.

    Forces the REAL failure rather than mocking the filesystem: DEST is a
    pre-existing DIRECTORY, so os.replace(tmp_file, DEST) raises
    IsADirectoryError (POSIX rename(2) onto an existing directory) --
    deterministic on every platform this suite runs on, no fragile mock."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"
    dest.mkdir()  # DEST is a directory -- os.replace(tmp, DEST) must fail

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 2, proc.stdout  # CanonHarmonisationFatalError -> exit 2
    assert_no_stdout(proc)
    assert dest.is_dir(), "DEST itself must be untouched by a failed publish"
    leftovers = list(root.glob(f".{dest.name}.tmp.*"))
    assert leftovers == [], f"os.replace() failure orphaned a temp file: {leftovers}"


def test_check_approve_to_unusable_parent_is_fatal_not_a_gate_failure(tmp_path):
    """#823 closing security pass, MINOR 1. _atomic_publish()'s
    `dest.parent.mkdir(...)` sat OUTSIDE the try/except OSError wrapping
    the rest of the publish, so a DEST whose parent path is a regular file
    raised a bare FileExistsError -- an uncaught traceback under exit 1.

    Exit 1 in this script's convention means the ARTIFACT is bad, so a
    perfectly valid proposal set was discarded and the W-step reported the
    wrong reason for it. A publish that cannot happen is a FATAL (exit 2),
    which is exactly what the os.replace sibling above already asserts.

    Forces the REAL failure, no mock: `blocker` is a regular file, and
    DEST is a path underneath it."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    blocker = root / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    dest = blocker / "canon_harmonisation.json"

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 2, proc.stdout  # CanonHarmonisationFatalError -> exit 2
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr, (
        f"a filesystem fault must surface as this script's named fatal, "
        f"not a raw traceback:\n{proc.stderr}"
    )
    assert blocker.read_text(encoding="utf-8") == "not a directory"


@pytest.mark.parametrize("mode", ["check", "report"])
def test_canon_entry_that_is_not_an_object_is_fatal(tmp_path, mode):
    """#823 closing security pass, MINOR 2. _load_canon() checked that
    entries{} is a dict but never that its VALUES are. Both modes then call
    `.get("canonical_target_form")` on a record, so a canon.json mapping a
    name straight to a string -- a hand edit, a truncated write -- raised
    AttributeError as an uncaught traceback under exit 1, blaming the
    artifact for a canon fault. A canon fault is a fatal (exit 2), and the
    difference decides whether the operator re-runs the pass or fixes
    their canon."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    # Rewrite canon.json with one entry mapped to a bare string.
    keyed = {e["source_form"]: e for e in entries}
    keyed[entries[0]["source_form"]] = entries[0]["canonical_target_form"]
    (root / "canon.json").write_bytes(json.dumps({
        "entries": keyed,
        "review_queue": [],
        "generation_hashes": {
            "particle_config_hash": "test-hash",
            "derivation_bundle_hash": "test-hash",
        },
    }, ensure_ascii=False).encode("utf-8"))

    if mode == "check":
        attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    else:
        write_json(root / "canon_harmonisation.json", doc)
        proc = run_harmonisation(root, "--report")

    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr, (
        f"a malformed canon entry must surface as this script's named "
        f"fatal, not a raw traceback:\n{proc.stderr}"
    )
    assert "not an entry object" in proc.stderr


# ===========================================================================
# --check exit 1 -- schema failures (a), naming the DOCUMENT, not an index
# ===========================================================================


def test_check_exit1_blank_note(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    doc["proposals"][0]["note"] = "   "  # whitespace-only -- pattern "\\S" must reject
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr, "a schema failure must name the document, not an index"


def test_check_exit1_unknown_kind(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    doc["proposals"][0]["kind"] = "not_a_real_kind"
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


def test_check_exit1_missing_required_field(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    del doc["proposals"][0]["note"]
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


def test_check_exit1_stray_additional_property(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    doc["unexpected_top_level_key"] = True
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


# ===========================================================================
# --check exit 1 -- anchor failure (b), naming the DOCUMENT
# ===========================================================================


def test_check_exit1_canon_sha256_mismatch(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    wrong = ("0" if csha[0] != "0" else "1") + csha[1:]
    doc["canon_sha256"] = wrong
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert wrong in proc.stderr and csha in proc.stderr
    assert "proposals[" not in proc.stderr, "the anchor check must name the document, not an index"


# ===========================================================================
# --check exit 1 -- proposal failures (c)-(g), naming the PROPOSAL INDEX
# ===========================================================================


def test_check_exit1_unknown_source_form(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    doc["proposals"][0]["members"][1]["source_form"] = "not-a-canon-key-at-all"
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "not-a-canon-key-at-all" in proc.stderr


def test_check_exit1_nfc_nfd_near_miss_must_refuse(tmp_path):
    """A source_form matching a canon key only after NFC/NFD normalisation
    must be refused, never silently tolerated -- byte-exact membership
    only (canon_link_groups.py's own rule)."""
    root = make_durable_root(tmp_path / "durable_root")
    nfc_form = unicodedata.normalize("NFC", "café-Rebbe")
    nfd_form = unicodedata.normalize("NFD", "café-Rebbe")
    assert nfc_form != nfd_form, "fixture sanity: the two forms must differ byte-for-byte"

    entries = [canon_entry(nfc_form, "Cafe Rebbe"), canon_entry("Other Name", "Cafe Rebbe")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member(nfd_form, "Cafe Rebbe"),  # NFD -- NOT a byte-exact canon key
            member("Other Name", "Cafe Rebbe"),
        ]),
    ])
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert nfd_form in proc.stderr


def test_check_exit1_misquoted_canonical_target_form(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    doc["proposals"][0]["members"][0]["canonical_target_form"] = "Some Wrong Target"
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "Some Wrong Target" in proc.stderr


def test_check_exit1_single_member_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Solo", "Solo Target")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [member("Solo", "Solo Target")]),
    ])
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr


def test_check_exit1_duplicate_source_form_within_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Alpha", "Target A"), canon_entry("Beta", "Target B")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member("Alpha", "Target A"),
            member("Beta", "Target B"),
            member("Alpha", "Target A"),  # repeats the first member's source_form
        ]),
    ])
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "Alpha" in proc.stderr


def test_check_exit1_all_members_share_one_target_form(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [
        canon_entry("Alpha", "Same Target"),
        canon_entry("Beta", "Same Target"),
    ]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member("Alpha", "Same Target"),
            member("Beta", "Same Target"),
        ]),
    ])
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "nothing to harmonise" in proc.stderr


def test_check_exit1_duplicate_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    duplicate = json.loads(json.dumps(doc["proposals"][0]))
    duplicate["note"] = "A different note, same kind and same member source_forms."
    doc["proposals"].append(duplicate)
    attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[1]" in proc.stderr
    assert "duplicate" in proc.stderr.lower()


# ===========================================================================
# --check exit 2
# ===========================================================================


def test_check_exit2_canon_json_absent(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    assert not (root / "canon.json").exists()

    # Any well-formed corpus: these cases fail before membership is ever
    # reached, so passing one keeps the test about the failure it names.
    corpus_path, corpus_sha = write_corpus(root, corpus_doc("0" * 64, []))
    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "canon.json" in proc.stderr


def test_check_exit2_path_is_unreadable_directory(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    attempt = root / "harmonisation" / "attempt_1.json"
    attempt.mkdir(parents=True)  # a directory, not a file -- unreadable as JSON

    # Any well-formed corpus: these cases fail before membership is ever
    # reached, so passing one keeps the test about the failure it names.
    corpus_path, corpus_sha = write_corpus(root, corpus_doc("0" * 64, []))
    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr


def test_check_exit2_schemas_dir_absent(tmp_path):
    root = make_durable_root_without_schemas(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    assert not (root / "schemas").exists()

    # Any well-formed corpus: these cases fail before membership is ever
    # reached, so passing one keeps the test about the failure it names.
    corpus_path, corpus_sha = write_corpus(root, corpus_doc("0" * 64, []))
    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "schemas" in proc.stderr


# ===========================================================================
# --report exit 0
# ===========================================================================


def test_report_exit0_with_proposals(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    write_json(root / "canon_harmonisation.json", doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary == {
        "success": True,
        "mode": "report",
        "proposals_count": 1,
        "entries_in_canon": 2,
        "canon_current": True,
    }
    assert "divergent_spelling" in proc.stderr
    assert "בָאבְרִינִצֶער" in proc.stderr
    assert "One byname, two vowelisations" in proc.stderr
    assert "canon_validate.py --correct" in proc.stderr
    assert '"canonical_target_form": null' in proc.stderr, (
        "the printed --correct skeleton must never pick a winner itself, and must render "
        "a value canon_validate.py --correct itself refuses rather than a placeholder "
        "STRING an unedited paste could freeze as canon -- see "
        "test_report_correct_skeleton_null_placeholder_refused_by_real_validator below "
        "for the fail-closed proof against the real validator"
    )


def test_report_correct_skeleton_null_placeholder_refused_by_real_validator(tmp_path):
    """#823 code review, finding 2 (MAJOR): the printed --correct skeleton
    used to render `"canonical_target_form": "<CHOOSE-ONE-CANONICAL-FORM>"`
    -- a STRING, and canon-entry.schema.json types that field as a bare
    string, so an operator who pasted the skeleton unedited FROZE THE
    LITERAL PLACEHOLDER AS CANON and canon_validate.py --correct reported
    success. The fix renders `null` instead, which the same schema refuses.

    This proves fail-closed against the REAL canon_validate.py --correct,
    not a re-implementation of its schema check: the skeleton is extracted
    verbatim from --report's own stderr (_extract_first_correct_skeleton),
    written untouched to a correction file, and fed to the shipped
    canon_validate.py --correct in a fixture durable root built the same
    way tests/canon_harmonisation.test.py's own non-interference test
    stages canon_validate.py -- against the SAME canon.json this
    --report run was anchored to, so old_entry matches what's on disk."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    write_json(root / "canon_harmonisation.json", doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    skeleton_text = _extract_first_correct_skeleton(proc.stderr)
    skeleton = json.loads(skeleton_text)
    assert skeleton["new_entry"]["canonical_target_form"] is None, (
        "sanity: the extracted block must be the skeleton this test targets"
    )

    correction_path = root / "harmonisation" / "untouched_skeleton.json"
    correction_path.parent.mkdir(parents=True, exist_ok=True)
    correction_path.write_text(skeleton_text, encoding="utf-8")

    # Stage the REAL canon_validate.py, plus the four schemas its --correct
    # path needs (entry/batch/file/correction -- the same set
    # tests/_canon_project_fixture.py's own CANON_SCHEMA_FILES hand-lists,
    # since canon_validate.py's schema registry is a glob over whatever is
    # staged, not an import), into the SAME durable root canon_harmonisation
    # was already anchored to.
    stage_consumer(root, "canon_validate.py")
    for name in (
        "canon-entry.schema.json", "canon-batch.schema.json",
        "canon-file.schema.json", "canon-correction.schema.json",
    ):
        shutil.copy2(SCHEMAS_SRC_DIR / name, root / "schemas" / name)
    canon_before = (root / "canon.json").read_bytes()

    proc_correct = subprocess.run(
        [
            sys.executable, str(root / "scripts" / "canon_validate.py"),
            "--research-mode", "offline", "--correct", str(correction_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert proc_correct.returncode != 0, (
        "an UNTOUCHED --correct skeleton must be refused by the real validator, never "
        f"accepted as canon:\nSTDOUT:\n{proc_correct.stdout}\nSTDERR:\n{proc_correct.stderr}"
    )
    # #823 code review round 2, finding 3 (NIT): returncode != 0 alone is
    # satisfied by ANY unexpected exception in canon_validate.py (it turns
    # every one into a non-zero exit, canon left untouched) -- a staging or
    # dependency failure in this fixture would pass this assertion without
    # ever proving the null was refused BY THE SCHEMA. Parse the validator's
    # own stdout JSON (pinned against a live run of this exact fixture, not
    # assumed) and require its error to name the specific offending path and
    # the specific jsonschema type failure.
    validate_summary = json.loads(proc_correct.stdout)
    assert validate_summary["success"] is False
    assert "new_entry/canonical_target_form" in validate_summary["error"], (
        f"the refusal must name the offending path, not just fail for some other reason:\n"
        f"{validate_summary['error']}"
    )
    assert "is not of type 'string'" in validate_summary["error"], (
        f"the refusal must be the schema's type check on the null placeholder, not a "
        f"staging or dependency failure:\n{validate_summary['error']}"
    )
    assert (root / "canon.json").read_bytes() == canon_before, (
        "canon.json must be byte-identical after a refused --correct"
    )


def test_report_exit0_empty_proposals_prints_not_a_certificate_wording(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Marie", "Marie")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    write_json(root / "canon_harmonisation.json", harmonisation_doc(csha, [], corpus_sha))

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["proposals_count"] == 0
    assert "no proposals returned" in proc.stderr
    assert "not a certificate" in proc.stderr
    assert "model's answer" in proc.stderr


def test_report_exit0_canon_current_false_after_canon_changes_underneath(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    write_json(root / "canon_harmonisation.json", doc)

    # Mutate canon.json underneath the artifact: change one entry's own
    # canonical_target_form (still schema-shaped, just different bytes).
    new_entries = [
        canon_entry("בָאבְרִינִצֶער", "Mordechai Babrinitzer-CORRECTED"),
        canon_entry("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
    ]
    write_canon(root, new_entries)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["canon_current"] is False
    # Every member target rendered is read FRESH from the current canon --
    # the new, corrected value must appear, never the artifact's stale one.
    assert "Mordechai Babrinitzer-CORRECTED" in proc.stderr


def test_report_exit0_removed_member_renders_stored_value_marked_removed(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    write_json(root / "canon_harmonisation.json", doc)

    # Remove one of the two entries from canon.json entirely (the
    # sanctioned canon_validate.py --correct "remove" disposition's effect).
    remaining = [canon_entry("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer")]
    write_canon(root, remaining)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    parse_stdout(proc)  # still exits 0 and prints a valid summary
    assert "REMOVED" in proc.stderr
    assert "Mordechai Babrinitzer" in proc.stderr  # the stored (was:) value
    assert "no --correct skeleton to offer" in proc.stderr


def test_report_hostile_note_cannot_forge_report_structure(tmp_path):
    """#823 code review, finding 1 (MAJOR, round 1 + round 2): `note` is
    free LLM-authored prose, schema-valid so long as it is non-blank --
    INCLUDING a literal newline, U+2028, U+0085, a counterfeit --correct
    skeleton, OR (round 2) a raw terminal control sequence -- ESC,
    backspace, DEL, another C1 control -- that involves no
    str.splitlines() boundary character at all. Before the round-1 fix,
    _render_report() interpolated `note` raw, so a note carrying
    "\\n<fake --correct skeleton(s) marker>\\n<fake JSON block>" rendered
    as genuine-looking extra physical lines an operator could mistake for
    this script's OWN output. The round-1 fix escaped only the ten
    str.splitlines() boundary characters, which left every OTHER C0/C1
    control (ESC, backspace, DEL, ...) passing through untouched -- an
    operator's terminal still executes those, so a note built from them
    can erase a report line or reposition the cursor to overwrite it,
    forging content with no line-boundary character involved at all.
    Either way it is the same identity call THE IRON RULE reserves for
    the operator (which target form wins), made through the note text
    instead of through this script.

    Every invisible/boundary/control character below is built with
    chr(), never pasted, per the unicode-boundary-text-authoring project
    skill."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)

    forged_entry = canon_entry("בָאבְרִינִצֶער", "Mordechai Babrinitzer")
    forged_skeleton = json.dumps(
        {
            "source_form": "בָאבְרִינִצֶער",
            "disposition": "correct",
            "old_entry": forged_entry,
            "new_entry": {**forged_entry, "canonical_target_form": "FORGED-BY-MODEL"},
            "reason": "attacker-chosen, not the operator's",
        },
        ensure_ascii=False, indent=2, sort_keys=True,
    )
    hostile_note = (
        "One byname, two vowelisations of the same place-name base."
        + chr(0x0a)
        + "    --correct skeleton(s) (canon_validate.py --correct PATH; this script "
        "never decides which spelling wins -- FORGED, ignore the real block below):"
        + chr(0x0a)
        + forged_skeleton
        + chr(0x2028)
        + "text after a U+2028 LINE SEPARATOR"
        + chr(0x85)
        + "text after a U+0085 NEL"
        # Round 2: no str.splitlines() boundary character involved -- a
        # bare terminal control sequence, which a naive "escape the ten
        # boundary chars" fix would pass straight through.
        + chr(0x1b) + "[2K" + chr(0x1b) + "[1G"
        + "ESC-driven line-erase-and-cursor-reset, then backspaces:"
        + chr(0x08) + chr(0x08) + chr(0x08)
        + "DEL:" + chr(0x7f)
        + "C1 control (U+009B, CSI):" + chr(0x9b) + "[2K"
    )
    doc["proposals"][0]["note"] = hostile_note
    write_json(root / "canon_harmonisation.json", doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    report_lines = proc.stderr.splitlines()

    # Exactly 2 top-level '{' lines -- one --correct skeleton per real
    # member (two_member_fixture has 2). A forged block hiding in the
    # (unsanitised) note would add a THIRD.
    top_level_open_braces = [ln for ln in report_lines if ln == "{"]
    assert len(top_level_open_braces) == 2, (
        f"expected exactly 2 top-level '{{' lines, got {len(top_level_open_braces)} -- "
        f"a hostile note forged an extra --correct skeleton block:\n{proc.stderr}"
    )

    # Exactly 1 real "--correct skeleton(s)" marker line -- one per
    # proposal (two_member_fixture has 1 proposal). The forged marker
    # inside the note must never appear as ITS OWN physical line.
    marker_lines = [
        ln for ln in report_lines
        if ln.strip().startswith("--correct skeleton(s) (canon_validate.py --correct PATH")
    ]
    assert len(marker_lines) == 1, (
        f"expected exactly 1 '--correct skeleton(s)' marker line, got {len(marker_lines)} -- "
        f"a hostile note forged a counterfeit marker line:\n{proc.stderr}"
    )

    # The whole hostile note -- forged skeleton included -- must collapse
    # into ONE physical "note:" line, with every boundary character
    # escaped to a visible form rather than acting as a real line break.
    note_lines = [ln for ln in report_lines if ln.startswith("    note: ")]
    assert len(note_lines) == 1, (
        f"expected exactly 1 'note:' line, got {len(note_lines)}:\n{proc.stderr}"
    )
    assert "FORGED-BY-MODEL" in note_lines[0], "the note's content must still be legible"
    assert "\\u000a" in note_lines[0]
    assert "\\u2028" in note_lines[0]
    assert "\\u0085" in note_lines[0]
    assert chr(0x2028) not in proc.stderr
    assert chr(0x85) not in proc.stderr

    # Round 2: assert the RULE (every Unicode `Cc` control escaped), not a
    # character list -- a list-based assertion has the same drift problem
    # the round-1 fix itself had. The only `Cc` character the script may
    # legitimately emit is '\n' (0x0a), the physical line separator
    # between ITS OWN report lines (str.join("\n", lines) then print());
    # every other Cc character anywhere in the whole rendered report --
    # ESC, backspace, DEL, the C1 control -- must have been escaped to a
    # visible \\uXXXX form, never reaching stderr raw.
    offending = sorted(
        {ch for ch in proc.stderr if unicodedata.category(ch) == "Cc" and ch != "\n"}
    )
    assert not offending, (
        f"raw Cc control character(s) reached stderr unescaped: "
        f"{[hex(ord(ch)) for ch in offending]}\n{proc.stderr}"
    )
    assert "\\u001b" in note_lines[0]
    assert "\\u0008" in note_lines[0]
    assert "\\u007f" in note_lines[0]
    assert "\\u009b" in note_lines[0]


def test_report_control_bearing_canon_entry_cannot_forge_via_skeleton(tmp_path):
    """#823 code review, finding A (MAJOR, round 3): the round-2 fix closed
    the whole Unicode `Cc` category on `note`, but `note` is not the only
    model-authored string the report prints. Each --correct skeleton is a
    json.dumps() of `source_form` plus the WHOLE canon entry, and
    json.dumps escapes only C0 (U+0000-U+001F, which JSON forbids raw) --
    it leaves U+007F, the entire C1 block (U+0080-U+009F, DEL and CSI
    among them) and U+2028/U+2029 as literal characters. The member lines
    a few lines above are safe only because repr() escapes those
    codepoints; a JSON dump is not a repr. So a canon entry carrying a
    control character -- canon.json rows are LLM-authored too, and
    canon-entry.schema.json constrains them by pattern, not by category --
    reached the operator's terminal raw through the skeleton block, able
    to repaint or split the report exactly as a hostile note could.

    Note is deliberately BENIGN here: this test must fail if and only if
    the SKELETON path is unescaped. Every control character is built with
    chr(), never pasted, per the unicode-boundary-text-authoring project
    skill."""
    root = make_durable_root(tmp_path / "durable_root")

    # THE WHOLE CLASS json.dumps() leaves raw, not a sample of it: every
    # codepoint from U+007F through U+009F (DEL plus the entire C1 block,
    # NEL and the CSI introducer among them), and the two Zl/Zp
    # separators. Naming only a handful would let a mutant that escaped
    # exactly those pass while leaving, say, U+0080 raw -- the same
    # character-list drift that cost the note sanitiser two rounds.
    # json.dumps escapes C0 (U+0000-U+001F) itself, so C0 is not part of
    # what this fixture has to prove.
    raw_survivors = "".join(chr(cp) for cp in range(0x7f, 0xa0))
    hostile_source = "בָאבְרִינִצֶער" + raw_survivors + "[2K" + chr(0x2028)
    hostile_target = "Mordechai Babrinitzer" + raw_survivors + chr(0x2029) + "{"
    entries = [
        canon_entry(hostile_source, hostile_target),
        canon_entry("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
    ]
    raw = write_canon(root, entries)
    doc = harmonisation_doc(canon_sha256_of(raw), [
        proposal(
            "divergent_spelling",
            [
                member(hostile_source, hostile_target),
                member("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
            ],
            note="One byname, two vowelisations of the same place-name base.",
        ),
    ])
    write_json(root / "canon_harmonisation.json", doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    parse_stdout(proc)

    # The RULE, asserted over the WHOLE rendered report: the only `Cc`
    # character this script may emit is '\n', its own physical line
    # separator. Everything else -- whatever its provenance -- must have
    # been escaped to a visible \uXXXX form.
    offending = sorted(
        {ch for ch in proc.stderr if unicodedata.category(ch) == "Cc" and ch != "\n"}
    )
    assert not offending, (
        f"raw Cc control character(s) reached stderr unescaped: "
        f"{[hex(ord(ch)) for ch in offending]}\n{proc.stderr}"
    )
    assert chr(0x2028) not in proc.stderr
    assert chr(0x2029) not in proc.stderr
    missing = [
        f"\\u{cp:04x}" for cp in range(0x7f, 0xa0)
        if f"\\u{cp:04x}" not in proc.stderr
    ]
    assert not missing, (
        f"every U+007F..U+009F codepoint must appear as its visible escape; "
        f"missing: {missing}\n{proc.stderr}"
    )
    assert "\\u2028" in proc.stderr
    assert "\\u2029" in proc.stderr

    # Escaping the SERIALISED dump must be lossless: \uXXXX inside a JSON
    # string literal parses back to the identical character, so the
    # skeleton an operator pastes still names the exact canon row.
    skeleton = json.loads(_extract_first_correct_skeleton(proc.stderr))
    assert skeleton["source_form"] == hostile_source
    assert skeleton["old_entry"]["canonical_target_form"] == hostile_target
    assert skeleton["new_entry"]["canonical_target_form"] is None

    # And the escaping must not have broken the report's own structure:
    # still exactly 2 top-level skeleton blocks, one per member.
    top_level_open_braces = [ln for ln in proc.stderr.splitlines() if ln == "{"]
    assert len(top_level_open_braces) == 2, (
        f"expected exactly 2 top-level '{{' lines, got {len(top_level_open_braces)}:"
        f"\n{proc.stderr}"
    )


def test_report_exit2_schema_invalid_artifact_no_stdout(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    bad_doc = {"schema_version": 1, "generated_at": "x", "canon_sha256": "0" * 64}  # missing proposals
    write_json(root / "canon_harmonisation.json", bad_doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


# ===========================================================================
# Usage errors
# ===========================================================================


def test_usage_error_neither_check_nor_report(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    proc = run_harmonisation(root)
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert proc.stderr.strip() != ""


def test_usage_error_both_check_and_report(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    proc = run_harmonisation(root, "--check", str(attempt), "--report")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


# ===========================================================================
# Startup faults -- exit 2, not 1 (#823 code review, finding 4, NIT)
# ===========================================================================


def test_startup_fatal_missing_json_stdout_exits_2_not_1(tmp_path):
    """#823 code review, finding 4 (NIT): a missing scripts/ dependency
    (json_stdout.py absent -- a deployment fault, Step 0a's copy pass
    failed or was skipped) used to exit 1 via `sys.exit(message)`, the same
    code as an ordinary --check gate-fail. This plugin's convention is 0
    clean / 1 gate-fail (--check only) / 2 fatal -- a fatal must exit 2."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "canon_harmonisation.py")
    shutil.copy2(SCHEMA_SRC, schemas_dir / "canon-harmonisation.schema.json")
    shutil.copy2(
        CORPUS_SCHEMA_SRC, schemas_dir / "canon-harmonisation-corpus.schema.json")
    # json_stdout.py deliberately NOT staged.
    write_canon(root, [canon_entry("Marie", "Marie")])

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "json_stdout.py" in proc.stderr


def test_report_read_only_mode_does_not_write_bytecode_cache(tmp_path):
    """#823 code review, finding 5 (NIT): --report is documented (this
    script's own module docstring) as a read-only render, but the
    exact-path dynamic load of json_stdout.py used to execute without
    sys.dont_write_bytecode, so a writable ${durable_root}/scripts/ could
    gain a __pycache__/json_stdout....pyc on every --report run."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    write_json(root / "canon_harmonisation.json", doc)

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    assert not (root / "scripts" / "__pycache__").exists(), (
        "--report is documented read-only and must not write a bytecode cache under scripts/"
    )


# ===========================================================================
# ONE-LINE STDOUT under a str.splitlines() boundary character
# ===========================================================================


def test_check_stdout_one_physical_line_with_boundary_char_in_approved_to_path(tmp_path):
    """The frozen CLI contract's stdout summary carries only
    proposals_count/entries_in_canon/canon_sha256/approved_to -- no
    free-text `note` field reaches stdout at all (every human-readable
    detail, including every proposal's note, goes to stderr, always -- see
    the module docstring). So the ONE stdout field that can carry
    caller-supplied free text is `approved_to`, the --approve-to DEST path
    echoed back verbatim. This test builds that path's own FILENAME with a
    literal U+2028 LINE SEPARATOR (via chr(), never pasted) and asserts the
    json_stdout escape still yields exactly one physical stdout line under
    str.splitlines() -- the same property sibling suites pin through their
    own JSON fields."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    boundary_char = chr(0x2028)
    dest = root / f"canon_harmonisation{boundary_char}sidecar.json"

    proc = run_harmonisation(
        root, "--check", str(attempt), "--corpus", str(corpus_path),
        "--expect-corpus-sha256", corpus_sha, "--approve-to", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 1, (
        f"a literal U+2028 in approved_to must not split stdout into two physical lines:\n"
        f"{proc.stdout!r}"
    )
    summary = parse_stdout(proc)
    assert summary["approved_to"] == str(dest), (
        "dumps_line() must escape the boundary character on the wire while the DECODED "
        "value still equals the real path"
    )


# ===========================================================================
# NON-INTERFERENCE: canon_validate.py / canon_adjudication_audit.py are
# byte-for-byte unaffected by canon_harmonisation.json's presence.
# ===========================================================================


def _without_generated_at(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if k != "generated_at"}


def _run_subprocess(script_path: Path, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), *args], capture_output=True, text=True, timeout=timeout,
    )


def test_noninterference_canon_validate_and_audit_unaffected_by_sidecar(tmp_path):
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    for name in ("canon-entry.schema.json", "canon-batch.schema.json", "canon-file.schema.json"):
        shutil.copy2(SCHEMAS_SRC_DIR / name, root / "schemas" / name)
    stage_consumer(root, "canon_adjudication_audit.py")
    for dep in ("bootstrap_names.py", "occ_index.py", "evidence_verify.py"):
        shutil.copy2(SCRIPTS_SRC_DIR / dep, root / "scripts" / dep)

    write_canon(root, [canon_entry("Marie", "Marie")])
    assert not (root / "canon_harmonisation.json").exists()

    proc_v_before = _run_subprocess(root / "scripts" / "canon_validate.py", "--research-mode", "offline")
    summary_v_before = parse_stdout(proc_v_before)
    proc_a_before = _run_subprocess(root / "scripts" / "canon_adjudication_audit.py", "--check")
    summary_a_before = parse_stdout(proc_a_before)

    raw_canon = (root / "canon.json").read_bytes()
    csha = canon_sha256_of(raw_canon)
    # The sidecar's own corpus digest is immaterial here: this test is about
    # canon_validate.py and canon_adjudication_audit.py being blind to the
    # file's existence, and neither ever reads it.
    write_json(root / "canon_harmonisation.json", harmonisation_doc(csha, [], "0" * 64))
    assert (root / "canon_harmonisation.json").is_file()

    proc_v_after = _run_subprocess(root / "scripts" / "canon_validate.py", "--research-mode", "offline")
    summary_v_after = parse_stdout(proc_v_after)
    proc_a_after = _run_subprocess(root / "scripts" / "canon_adjudication_audit.py", "--check")
    summary_a_after = parse_stdout(proc_a_after)

    assert proc_v_after.returncode == proc_v_before.returncode == 0
    assert summary_v_after == summary_v_before, (
        "canon_validate.py --research-mode offline (validate-only) carries no generated_at "
        "field, so this must be an EXACT match"
    )

    assert proc_a_after.returncode == proc_a_before.returncode == 0
    assert _without_generated_at(summary_a_after) == _without_generated_at(summary_a_before)


# ===========================================================================
# RTL / multiword / apostrophe-bearing forms
# ===========================================================================


def test_rtl_multiword_apostrophe_forms_round_trip_through_check_and_report(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    formal_form = "רבי ישראל מרוז'ין"
    affectionate_form = "ר' ישראל'ן"
    entries = [
        canon_entry(formal_form, "Rebbe Yisrael of Ruzhin"),
        canon_entry(affectionate_form, "Reb Yisrolen"),
    ]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries)))
    doc = harmonisation_doc(csha, [
        proposal(
            "divergent_policy",
            [
                member(formal_form, "Rebbe Yisrael of Ruzhin"),
                member(affectionate_form, "Reb Yisrolen"),
            ],
            note="Same rebbe: a formal title beside an affectionate byname.",
        ),
    ])
    dest = root / "canon_harmonisation.json"
    attempt, proc_check = check_attempt(
        root, doc, corpus_path, corpus_sha, "--approve-to", str(dest))
    assert proc_check.returncode == 0, proc_check.stderr
    check_summary = parse_stdout(proc_check)
    assert check_summary["proposals_count"] == 1
    assert dest.read_bytes() == attempt.read_bytes()

    proc_report = run_harmonisation(root, "--report")
    assert proc_report.returncode == 0, proc_report.stderr
    report_summary = parse_stdout(proc_report)
    assert report_summary["proposals_count"] == 1
    assert formal_form in proc_report.stderr
    assert affectionate_form in proc_report.stderr
    assert "Rebbe Yisrael of Ruzhin" in proc_report.stderr
    assert "Reb Yisrolen" in proc_report.stderr


# ===========================================================================
# Sidecar fixtures validate against the REAL schema
# ===========================================================================


def test_two_member_fixture_validates_against_the_real_schema(tmp_path):
    root = tmp_path / "durable_root_for_schema_check"
    root.mkdir()
    _csha, _entries, doc, corpus_path, corpus_sha = two_member_fixture(root)
    jsonschema.Draft202012Validator(HARMONISATION_SCHEMA).validate(doc)


def test_empty_proposals_fixture_validates_against_the_real_schema():
    doc = harmonisation_doc("0" * 64, [])
    jsonschema.Draft202012Validator(HARMONISATION_SCHEMA).validate(doc)


def test_rtl_fixture_validates_against_the_real_schema():
    doc = harmonisation_doc("0" * 64, [
        proposal("divergent_policy", [
            member("רבי ישראל מרוז'ין", "Rebbe Yisrael of Ruzhin"),
            member("ר' ישראל'ן", "Reb Yisrolen"),
        ]),
    ])
    jsonschema.Draft202012Validator(HARMONISATION_SCHEMA).validate(doc)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ===========================================================================
# The five-kind matrix (#823 scope correction 1) and the corpus contract
# (#823 scope correction 2)
# ===========================================================================


def matrix_fixture(root: Path):
    """A canon of two entries sharing ONE target plus a third under its own,
    a converged-draft observation that DISAGREES with canon about the first
    entry, and one candidate observation that never reached canon. Every kind
    in the matrix has a legitimate proposal over this fixture, so a test can
    vary one rule at a time instead of rebuilding the world."""
    entries = [
        canon_entry("Noson", "R. Nathan"),
        canon_entry("Nathan b. Leibele", "R. Nathan"),
        canon_entry("Chaykel", "Chaykel"),
    ]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    observations = observations_for(entries) + [
        observation("Noson", "Reb Noson", corpus="draft", n_segments=4),
        observation("Weinberg", None, corpus="candidate", freq=7),
    ]
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations, converged_segments=4))
    return csha, corpus_path, corpus_sha


def test_shared_target_two_canon_members_one_target_passes(tmp_path):
    """#823 scope correction 1: 'one target string frozen for two different
    people' is the INVERSE of the divergent case and the more damaging one --
    two men land on one vault page. Before this kind existed the schema could
    not express it at all and --check refused any group whose members agreed
    on a target."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Noson", "R. Nathan"),
        member("Nathan b. Leibele", "R. Nathan"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 0, proc.stderr
    assert parse_stdout(proc)["proposals_count"] == 1


def test_shared_target_with_two_distinct_targets_is_refused(tmp_path):
    """A shared_target proposal whose members do NOT share a target is a
    divergent_* proposal wearing the wrong label. Refused naming the kind and
    the count, so it is never silently re-read as another kind."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Noson", "R. Nathan"),
        member("Chaykel", "Chaykel"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "shared_target" in proc.stderr
    assert "exactly 1 distinct target" in proc.stderr


def test_shared_target_needs_two_canon_members_not_a_draft_one(tmp_path):
    """A frozen target is a CANON property: only two canon entries can put two
    referents on one vault page. A draft row saying the same thing is an
    observation about a translation, not about what canon froze -- so one
    canon member plus one draft member is refused."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Noson", "R. Nathan"),
        member("Noson", "Reb Noson", corpus="draft", n_segments=4),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "at least 2 members whose corpus is 'canon'" in proc.stderr


def test_divergent_spelling_with_one_distinct_target_is_refused(tmp_path):
    """The original #823 rule, still enforced for the divergent kinds: members
    that already agree on a target have nothing to harmonise."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        member("Noson", "R. Nathan"),
        member("Nathan b. Leibele", "R. Nathan"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "divergent_spelling" in proc.stderr
    assert "at least 2 distinct target" in proc.stderr


def test_all_draft_divergent_passes_and_offers_no_correct_skeleton(tmp_path):
    """#823 scope correction 2: roughly a fifth of the real findings never
    reach canon.json at all. A divergence living wholly in the per-segment
    corpus must therefore be REPORTABLE -- requiring a canon anchor would make
    exactly that class unreportable. Its route is not --correct, which refuses
    a source_form it cannot find in entries{}, so the report must offer no
    skeleton and say what the route actually is."""
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Anchor", "Anchor")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    observations = observations_for(entries) + [
        observation("Lipovetsker", "Lipovetsker", corpus="draft", n_segments=2),
        observation("Lipovetsker", "Lipovetzker", corpus="draft", n_segments=1),
    ]
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations, converged_segments=3))
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        member("Lipovetsker", "Lipovetsker", corpus="draft", n_segments=2),
        member("Lipovetsker", "Lipovetzker", corpus="draft", n_segments=1),
    ])])
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha,
        "--approve-to", str(root / "canon_harmonisation.json"))
    assert proc.returncode == 0, proc.stderr

    report = run_harmonisation(root, "--report")
    assert report.returncode == 0, report.stderr
    assert "NOT IN CANON" in report.stderr
    assert "ordinary glossary merge" in report.stderr
    assert [ln for ln in report.stderr.splitlines() if ln == "{"] == [], (
        "an all-draft proposal over forms absent from canon must offer no "
        f"--correct skeleton:\n{report.stderr}"
    )


def test_canon_and_disagreeing_draft_row_are_two_members_of_one_proposal(tmp_path):
    """The observation TRIPLE, not the source form, is what a member names.
    `canon: X -> A` beside a converged `draft: X -> B` is the cross-segment
    discrepancy final_audit.py's WARN glossary-diff already reports; keying
    membership or duplicate-detection by source_form alone would collapse the
    two rows this pass exists to put in front of the operator."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        member("Noson", "R. Nathan"),
        member("Noson", "Reb Noson", corpus="draft", n_segments=4),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 0, proc.stderr


def test_member_absent_from_the_corpus_is_refused(tmp_path):
    """Membership is checked against the corpus the session DISPATCHED, not
    against live canon.json: a row the pass invented is refused even when a
    form of that name exists somewhere."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        member("Noson", "R. Nathan"),
        member("Invented", "Invented Target"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "is not a byte-exact observation in the corpus" in proc.stderr


@pytest.mark.parametrize("mutation,label", [
    ({"corpus": "draft"}, "wrong corpus"),
    ({"canonical_target_form": "Something Else"}, "wrong target"),
])
def test_member_matching_only_part_of_the_triple_is_refused(tmp_path, mutation, label):
    """Matching on the whole triple is what makes a corpus tag unforgeable: a
    row relabelled into another corpus, or requoted with another target, is
    simply not in the corpus and is refused."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    bad = member("Noson", "R. Nathan")
    bad.update(mutation)
    if bad["corpus"] == "draft":
        bad["n_segments"] = 4
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        bad, member("Chaykel", "Chaykel"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "is not a byte-exact observation in the corpus" in proc.stderr, label


def test_member_count_disagreeing_with_the_observation_is_refused(tmp_path):
    """--report never receives --corpus, so it renders n_segments/freq from the
    artifact. An unverified count would therefore be a number the pass chose
    for the operator to read."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("divergent_spelling", [
        member("Noson", "R. Nathan"),
        member("Noson", "Reb Noson", corpus="draft", n_segments=99),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "n_segments=99" in proc.stderr


def test_multi_referent_one_canon_member_with_two_referents_passes(tmp_path):
    """#823 scope correction 1, vol4's largest instance: twelve bare
    given-name entries each merging every bearer onto one page. The mandatory
    homonym-split gate cannot find these -- it only enumerates splits ALREADY
    authored in canon_senses.json and states NOT ENUMERATED otherwise -- so
    nothing in the pipeline discovered them before this kind."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [dict(
        proposal("multi_referent", [member("Chaykel", "Chaykel")]),
        referents=["the Chaykel of vol1", "Chaykel the shochet"],
    )])
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha,
        "--approve-to", str(root / "canon_harmonisation.json"))
    assert proc.returncode == 0, proc.stderr

    report = run_harmonisation(root, "--report")
    assert report.returncode == 0, report.stderr
    assert "referents claimed:" in report.stderr
    assert "canon_senses.json" in report.stderr
    assert [ln for ln in report.stderr.splitlines() if ln == "{"] == [], (
        "multi_referent's route is a canon_senses.json split, not a retarget -- "
        f"it must offer no --correct skeleton:\n{report.stderr}"
    )


def test_multi_referent_with_a_second_member_is_refused(tmp_path):
    """'Exactly one member TOTAL', not 'one canon member plus whatever': an
    implementation counting only canon members would let arbitrary draft or
    candidate rows ride along on a claim about a single entry."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [dict(
        proposal("multi_referent", [
            member("Chaykel", "Chaykel"),
            member("Noson", "Reb Noson", corpus="draft", n_segments=4),
        ]),
        referents=["one", "two"],
    )])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "exactly 1 member whose corpus is 'canon'" in proc.stderr


def test_multi_referent_referents_differing_only_by_whitespace_are_one(tmp_path):
    """`referents` is this kind's ONLY structural protection against a
    content-free assertion, so distinctness is measured after collapsing
    internal whitespace -- raw distinctness would let a duplicate defeat it."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [dict(
        proposal("multi_referent", [member("Chaykel", "Chaykel")]),
        referents=["Reb Noson", "Reb  Noson"],
    )])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "distinct after whitespace normalisation" in proc.stderr


def test_uncanonized_variant_passes_and_routes_the_candidate_to_a_new_entry(tmp_path):
    """#823 scope correction 2's own examples -- Weinberg, Lipovetsker and the
    rest -- exist only outside canon. The route for such a form is a NEW canon
    entry through the ordinary glossary merge, never --correct, which refuses
    a source_form it cannot find."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("uncanonized_variant", [
        member("Chaykel", "Chaykel"),
        member("Weinberg", None, corpus="candidate", freq=7),
    ])])
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha,
        "--approve-to", str(root / "canon_harmonisation.json"))
    assert proc.returncode == 0, proc.stderr

    report = run_harmonisation(root, "--report")
    assert "NOT IN CANON (candidate, freq 7)" in report.stderr
    assert "ordinary glossary merge" in report.stderr
    assert len([ln for ln in report.stderr.splitlines() if ln == "{"]) == 1, (
        "exactly one --correct skeleton, for the canon member only:\n"
        f"{report.stderr}"
    )


def test_uncanonized_variant_naming_a_live_canon_key_is_refused(tmp_path):
    """A corpus tag records WHERE a row was read, never what canon holds now.
    name_candidates.json carries the extractor's complete list and only
    glossary_batch_plan.py later drops the forms already resolved, so a
    candidate observation's source_form may well be a canon key -- and then it
    is not uncanonized at all."""
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Anchor", "Anchor"), canon_entry("Weinberg", "Weinberg")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    observations = observations_for(entries) + [
        observation("Weinberg", None, corpus="candidate", freq=7),
    ]
    corpus_path, corpus_sha = write_corpus(root, corpus_doc(csha, observations))
    doc = harmonisation_doc(csha, [proposal("uncanonized_variant", [
        member("Anchor", "Anchor"),
        member("Weinberg", None, corpus="candidate", freq=7),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "IS a key of canon.json" in proc.stderr


def test_corpus_digest_mismatch_is_refused_and_publishes_nothing(tmp_path):
    """The artifact must quote the digest the session dispatched."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Noson", "R. Nathan"),
        member("Nathan b. Leibele", "R. Nathan"),
    ])], corpus_sha256="d" * 64)
    dest = root / "canon_harmonisation.json"
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha, "--approve-to", str(dest), stamp=False)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert not dest.exists()


def test_a_self_consistent_rewrite_of_the_corpus_is_still_refused(tmp_path):
    """THE TRUSTED CHANNEL, negative control. The corpus file lives in the
    durable root the dispatched pass can WRITE, so a digest recomputed from
    that file and compared only against the artifact's own field proves
    self-consistency and nothing else: the pass can rewrite the corpus with
    fabricated observations AND stamp the matching digest into its artifact.
    --expect-corpus-sha256 carries the digest the session computed BEFORE
    dispatching, which the pass cannot reach -- so this run, consistent on
    disk in every respect, must still be refused and publish nothing."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)

    forged_observations = [
        observation("Fabricated A", "One Target"),
        observation("Fabricated B", "One Target"),
    ]
    forged_raw = write_json(corpus_path, corpus_doc(csha, forged_observations))
    forged_sha = hashlib.sha256(forged_raw).hexdigest()
    assert forged_sha != corpus_sha, "fixture sanity: the rewrite must move the digest"

    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Fabricated A", "One Target"),
        member("Fabricated B", "One Target"),
    ])], corpus_sha256=forged_sha)
    dest = root / "canon_harmonisation.json"
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha, "--approve-to", str(dest), stamp=False)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert not dest.exists(), "nothing may be published on a corpus the session did not send"
    assert "--expect-corpus-sha256" in proc.stderr


@pytest.mark.parametrize("argv_tail,label", [
    ((), "neither"),
    (("--corpus", "SOME_PATH"), "corpus without the expected digest"),
    (("--expect-corpus-sha256", "0" * 64), "digest without the corpus"),
])
def test_check_without_the_corpus_arguments_is_a_usage_error(tmp_path, argv_tail, label):
    """Both arguments are REQUIRED with --check. Making them optional would
    silently restore the old behaviour -- membership checked against live
    canon.json -- for any caller that forgot one."""
    root = make_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    tail = tuple(str(attempt.parent / "corpus_1.json") if a == "SOME_PATH" else a
                 for a in argv_tail)
    proc = run_harmonisation(root, "--check", str(attempt), *tail)
    assert proc.returncode == 2, (label, proc.stdout)
    assert_no_stdout(proc)


def test_only_the_corpus_bytes_change_and_the_check_still_refuses(tmp_path):
    """#823 delta review, MINOR: the two digest tests above both move the
    ARTIFACT's field, so an implementation that compared only the artifact
    against --expect-corpus-sha256 and never hashed the corpus file would
    refuse both and pass both. This case moves ONLY the file on disk: the
    expected digest and the artifact's field stay at the original value, so
    it fails if and only if the on-disk leg of the three-way comparison is
    really computed."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("shared_target", [
        member("Noson", "R. Nathan"),
        member("Nathan b. Leibele", "R. Nathan"),
    ])], corpus_sha256=corpus_sha)

    tampered = write_json(corpus_path, corpus_doc(
        csha, observations_for([canon_entry("Noson", "R. Nathan")])))
    assert hashlib.sha256(tampered).hexdigest() != corpus_sha, (
        "fixture sanity: the rewrite must move the file's digest"
    )

    dest = root / "canon_harmonisation.json"
    _attempt, proc = check_attempt(
        root, doc, corpus_path, corpus_sha, "--approve-to", str(dest), stamp=False)
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert not dest.exists()


def test_divergent_policy_with_one_distinct_target_is_refused(tmp_path):
    """The kind matrix is per-kind, so divergent_policy needs its own negative
    row: a rule enforced for divergent_spelling only would leave the other
    divergent kind unchecked."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("divergent_policy", [
        member("Noson", "R. Nathan"),
        member("Nathan b. Leibele", "R. Nathan"),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "divergent_policy" in proc.stderr
    assert "at least 2 distinct target" in proc.stderr


def test_uncanonized_variant_without_a_canon_member_is_refused(tmp_path):
    """A proposal made only of candidate rows has nothing anchoring it to the
    canon it proposes to extend."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("uncanonized_variant", [
        member("Weinberg", None, corpus="candidate", freq=7),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "at least 1 'canon' member" in proc.stderr


def test_member_freq_disagreeing_with_the_observation_is_refused(tmp_path):
    """The n_segments case above covers the draft field; freq is the candidate
    one and is verified by the same rule, so it needs its own row -- a check
    written for one field only would leave the other a number the pass chose."""
    root = make_durable_root(tmp_path / "durable_root")
    csha, corpus_path, corpus_sha = matrix_fixture(root)
    doc = harmonisation_doc(csha, [proposal("uncanonized_variant", [
        member("Chaykel", "Chaykel"),
        member("Weinberg", None, corpus="candidate", freq=999),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "freq=999" in proc.stderr


def test_candidate_member_against_a_disabled_corpus_is_refused(tmp_path):
    """On the glossary.enabled:false branch the session writes an empty
    candidate list, so a candidate member cannot match any observation. The
    refusal falls out of the data rather than needing its own rule -- this
    test is what proves that is actually true."""
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Anchor", "Anchor")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    corpus_path, corpus_sha = write_corpus(
        root, corpus_doc(csha, observations_for(entries), candidates_source="disabled"))
    doc = harmonisation_doc(csha, [proposal("uncanonized_variant", [
        member("Anchor", "Anchor"),
        member("Weinberg", None, corpus="candidate", freq=7),
    ])])
    _attempt, proc = check_attempt(root, doc, corpus_path, corpus_sha)
    assert proc.returncode == 1, proc.stdout
    assert "is not a byte-exact observation in the corpus" in proc.stderr
