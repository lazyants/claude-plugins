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


def member(source_form, canonical_target_form) -> dict:
    return {"source_form": source_form, "canonical_target_form": canonical_target_form}


def proposal(kind, members, note="A harmonisation note explaining the identity call.") -> dict:
    return {"kind": kind, "members": members, "note": note}


def harmonisation_doc(canon_sha256, proposals, generated_at="2026-01-01T00:00:00+00:00") -> dict:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "canon_sha256": canon_sha256,
        "proposals": proposals,
    }


def write_json(path: Path, doc: dict) -> bytes:
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def two_member_fixture(root: Path):
    """The ordinary happy-path fixture: two canon entries under diverging
    canonical_target_forms, and a matching, correctly-anchored two-member
    divergent_spelling proposal naming both. Returns
    (canon_sha256, entries_list, artifact_doc)."""
    entries = [
        canon_entry("בָאבְרִינִצֶער", "Mordechai Babrinitzer"),
        canon_entry("בֳאבְרִינִצֶער", "Mordechai Bobrinitzer"),
    ]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
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
    return csha, entries, doc


# ===========================================================================
# --check exit 0
# ===========================================================================


def test_check_exit0_two_member_divergent_spelling_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary == {
        "success": True,
        "mode": "check",
        "proposals_count": 1,
        "entries_in_canon": 2,
        "canon_sha256": csha,
        "approved_to": None,
    }


def test_check_exit0_empty_proposals_nonzero_entries_in_canon(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Marie", "Marie"), canon_entry("Jean", "Jean")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc(csha, []))

    proc = run_harmonisation(root, "--check", str(attempt))
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
    csha, entries, doc = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    raw = write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"
    assert not dest.exists()

    proc = run_harmonisation(root, "--check", str(attempt), "--approve-to", str(dest))
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["approved_to"] == str(dest)
    assert dest.is_file()
    assert dest.read_bytes() == raw, "approve-to must publish the EXACT bytes read from PATH"


@pytest.mark.parametrize("preexisting", [False, True])
def test_check_approve_to_not_created_or_touched_on_exit1(tmp_path, preexisting):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    # Corrupt the anchor (b) so this is a clean exit-1 gate-fail, not exit 2.
    doc["canon_sha256"] = "0" * 64 if csha != "0" * 64 else "1" * 64
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"
    preexisting_bytes = b'{"stale": "snapshot"}'
    if preexisting:
        dest.write_bytes(preexisting_bytes)

    proc = run_harmonisation(root, "--check", str(attempt), "--approve-to", str(dest))
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

    proc = run_harmonisation(root, "--check", str(attempt), "--approve-to", str(dest))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert not dest.exists()


# ===========================================================================
# --check exit 1 -- schema failures (a), naming the DOCUMENT, not an index
# ===========================================================================


def test_check_exit1_blank_note(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    doc["proposals"][0]["note"] = "   "  # whitespace-only -- pattern "\\S" must reject
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr, "a schema failure must name the document, not an index"


def test_check_exit1_unknown_kind(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    doc["proposals"][0]["kind"] = "not_a_real_kind"
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


def test_check_exit1_missing_required_field(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    del doc["proposals"][0]["note"]
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


def test_check_exit1_stray_additional_property(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    doc["unexpected_top_level_key"] = True
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr
    assert "proposals[" not in proc.stderr


# ===========================================================================
# --check exit 1 -- anchor failure (b), naming the DOCUMENT
# ===========================================================================


def test_check_exit1_canon_sha256_mismatch(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    wrong = ("0" if csha[0] != "0" else "1") + csha[1:]
    doc["canon_sha256"] = wrong
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
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
    csha, entries, doc = two_member_fixture(root)
    doc["proposals"][0]["members"][1]["source_form"] = "not-a-canon-key-at-all"
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
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
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member(nfd_form, "Cafe Rebbe"),  # NFD -- NOT a byte-exact canon key
            member("Other Name", "Cafe Rebbe"),
        ]),
    ])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert nfd_form in proc.stderr


def test_check_exit1_misquoted_canonical_target_form(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    doc["proposals"][0]["members"][0]["canonical_target_form"] = "Some Wrong Target"
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "Some Wrong Target" in proc.stderr


def test_check_exit1_single_member_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Solo", "Solo Target")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [member("Solo", "Solo Target")]),
    ])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr


def test_check_exit1_duplicate_source_form_within_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    entries = [canon_entry("Alpha", "Target A"), canon_entry("Beta", "Target B")]
    raw = write_canon(root, entries)
    csha = canon_sha256_of(raw)
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member("Alpha", "Target A"),
            member("Beta", "Target B"),
            member("Alpha", "Target A"),  # repeats the first member's source_form
        ]),
    ])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
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
    doc = harmonisation_doc(csha, [
        proposal("divergent_spelling", [
            member("Alpha", "Same Target"),
            member("Beta", "Same Target"),
        ]),
    ])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 1, proc.stdout
    assert_no_stdout(proc)
    assert "proposals[0]" in proc.stderr
    assert "nothing to harmonise" in proc.stderr


def test_check_exit1_duplicate_proposal(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
    duplicate = json.loads(json.dumps(doc["proposals"][0]))
    duplicate["note"] = "A different note, same kind and same member source_forms."
    doc["proposals"].append(duplicate)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)

    proc = run_harmonisation(root, "--check", str(attempt))
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

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "canon.json" in proc.stderr


def test_check_exit2_path_is_unreadable_directory(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    attempt = root / "harmonisation" / "attempt_1.json"
    attempt.mkdir(parents=True)  # a directory, not a file -- unreadable as JSON

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert str(attempt) in proc.stderr


def test_check_exit2_schemas_dir_absent(tmp_path):
    root = make_durable_root_without_schemas(tmp_path / "durable_root")
    write_canon(root, [canon_entry("Marie", "Marie")])
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, harmonisation_doc("0" * 64, []))
    assert not (root / "schemas").exists()

    proc = run_harmonisation(root, "--check", str(attempt))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "schemas" in proc.stderr


# ===========================================================================
# --report exit 0
# ===========================================================================


def test_report_exit0_with_proposals(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
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
    assert "<CHOOSE-ONE-CANONICAL-FORM>" in proc.stderr, (
        "the printed --correct skeleton must never pick a winner itself"
    )


def test_report_exit0_empty_proposals_prints_not_a_certificate_wording(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    raw = write_canon(root, [canon_entry("Marie", "Marie")])
    csha = canon_sha256_of(raw)
    write_json(root / "canon_harmonisation.json", harmonisation_doc(csha, []))

    proc = run_harmonisation(root, "--report")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["proposals_count"] == 0
    assert "no proposals returned" in proc.stderr
    assert "not a certificate" in proc.stderr
    assert "model's answer" in proc.stderr


def test_report_exit0_canon_current_false_after_canon_changes_underneath(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")
    csha, entries, doc = two_member_fixture(root)
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
    csha, entries, doc = two_member_fixture(root)
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
    csha, entries, doc = two_member_fixture(root)
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    boundary_char = chr(0x2028)
    dest = root / f"canon_harmonisation{boundary_char}sidecar.json"

    proc = run_harmonisation(root, "--check", str(attempt), "--approve-to", str(dest))
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
    write_json(root / "canon_harmonisation.json", harmonisation_doc(csha, []))
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
    attempt = root / "harmonisation" / "attempt_1.json"
    write_json(attempt, doc)
    dest = root / "canon_harmonisation.json"

    proc_check = run_harmonisation(root, "--check", str(attempt), "--approve-to", str(dest))
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
    _csha, _entries, doc = two_member_fixture(root)
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
