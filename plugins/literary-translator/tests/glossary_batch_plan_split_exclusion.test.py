"""tests/glossary_batch_plan_split_exclusion.test.py -- regression suite for
glossary_batch_plan.py's W3 split-form exclusion (RFC #215 item 1f).

Additive sibling of tests/glossary_batch_plan.test.py (that file is left
untouched; this one owns only the new canon_senses.json-driven behavior).
Drives the REAL, on-disk script via subprocess, same house style as the
sibling suite.

The bug this closes: a source_form the sidecar marks as an adjudicated
homonym split (>=2 senses) is intentionally ABSENT from canon.json's
entries{} -- that absence is the whole point of the sidecar. Without this
exclusion, W3 re-extracts the split form as a fresh candidate, the planner
dispatches it to the glossary agent, canon_validate.py's recollapse guard
(1d) then refuses to merge it back FOREVER, and the Workflow's readiness
wait loop deadlocks. Observed RED against the pre-fix script (no
canon_senses import, no --senses-path, no is_split() check in
select_included) before this suite was accepted: the split-exclusion tests
below found the split form present in `args`/`batches` on the unmodified
script, for the right reason (there was nothing there to exclude it).
"""
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts"
    / "glossary_batch_plan.py"
)


# ---------------------------------------------------------------------------
# Fixture builders (self-contained -- deliberately not imported from the
# sibling suite; test modules with a `.test.py` suffix are not ordinary
# importable packages in this repo's convention).
# ---------------------------------------------------------------------------


def cand(name, freq=5, likely_name=True, mid_sentence=1, **extra):
    words = name.split()
    row = {
        "name": name,
        "freq": freq,
        "mid_sentence": mid_sentence,
        "multiword": len(words) > 1,
        "abbrev": len(words) == 1 and len(words[0]) == 1,
        "n_segments": 1,
        "likely_name": likely_name,
    }
    row.update(extra)
    return row


def write_inputs(tmp_path, candidates, entries=None, review_queue=None):
    nc_path = tmp_path / "name_candidates.json"
    nc_path.write_text(
        json.dumps(
            {
                "n_candidates": len(candidates),
                "n_strong": sum(1 for c in candidates if c.get("likely_name")),
                "candidates": candidates,
            }
        ),
        encoding="utf-8",
    )
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(
        json.dumps(
            {
                "entries": entries or {},
                "review_queue": review_queue or [],
                "generation_hashes": {
                    "particle_config_hash": "pc",
                    "derivation_bundle_hash": "db",
                },
            }
        ),
        encoding="utf-8",
    )
    return nc_path, canon_path


def valid_evidence(**overrides):
    evidence = {
        "block": "PARA:seg01:0001",
        "seg": "seg01",
        "char_start": 10,
        "char_end": 16,
        "context_start": 0,
        "context_end": 40,
        "sha256": "a" * 64,
    }
    evidence.update(overrides)
    return evidence


def valid_sense(sense_id, disambiguator="sense", index_scope="narrative", evidence=None):
    return {
        "sense_id": sense_id,
        "disambiguator": disambiguator,
        "index_scope": index_scope,
        "evidence": evidence if evidence is not None else valid_evidence(),
    }


def split_entry(*sense_ids):
    """A schema-valid `entries_by_source_form` value: an adjudicated split
    (>=2 senses)."""
    return {"senses": [valid_sense(sid) for sid in sense_ids]}


def write_senses(tmp_path, entries_by_source_form, name="canon_senses.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {"schema_version": 1, "entries_by_source_form": entries_by_source_form},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run(nc_path, canon_path, *extra):
    argv = [
        sys.executable,
        str(SCRIPT),
        "--name-candidates",
        str(nc_path),
        "--canon",
        str(canon_path),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True)


def run_ok(nc_path, canon_path, *extra):
    proc = run(nc_path, canon_path, *extra)
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr}"
    return json.loads(proc.stdout)


def names_in_args(result):
    """Flat set of every dispatched candidate name -- empty for the
    `no_new_candidates` marker (no `args` key in that shape)."""
    if result.get("no_new_candidates"):
        return set()
    out = set()
    for batch in result["args"]:
        for row in batch["candidates"]:
            out.add(row["name"])
    return out


# ---------------------------------------------------------------------------
# Staged-consumer helpers (for the default-sidecar-resolution tests below,
# which must run against a REAL default-path lookup -- never the shared
# plugin tree). Mirrors tests/canon_validate_recollapse.test.py's own
# `make_durable_root`/`run_cli` pair: `stage_consumer` (the sanctioned
# tests/_senses_fixture.py helper) copies the REAL glossary_batch_plan.py +
# canon_senses.py + canon-senses.schema.json into an isolated tmp_path
# durable_root, so the staged copy's own self-anchored
# `DEFAULT_SENSES_PATH` (sibling of `root/scripts/glossary_batch_plan.py`'s
# parents[1], i.e. `root/canon_senses.json`) resolves against a private
# root instead of the shared skills/literary-translator/assets/ tree --
# closing the race/dirty-tree risk a direct write to that shared path would
# carry while other teammates' suites run concurrently in this worktree.
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    stage_consumer(root, "glossary_batch_plan.py")
    return root


def run_staged(root, nc_path, canon_path, *extra):
    argv = [
        sys.executable,
        str(root / "scripts" / "glossary_batch_plan.py"),
        "--name-candidates",
        str(nc_path),
        "--canon",
        str(canon_path),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True, cwd=str(root))


def run_staged_ok(root, nc_path, canon_path, *extra):
    proc = run_staged(root, nc_path, canon_path, *extra)
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr}"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Core split exclusion (RFC #215 item 1f)
# ---------------------------------------------------------------------------


def test_split_form_excluded_via_explicit_senses_path(tmp_path):
    """THE regression: "Jean" is a valid split in canon_senses.json and
    intentionally absent from canon.json's entries{}/review_queue -- W3
    re-extracts it as a fresh, strong candidate. Without the fix it would
    survive step (1) and be dispatched; with the fix it is excluded."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Jean", freq=20, likely_name=True), cand("Alice", freq=20, likely_name=True)],
    )
    senses = write_senses(tmp_path, {"Jean": split_entry("s1", "s2")})
    result = run_ok(nc, canon, "--senses-path", str(senses))
    got = names_in_args(result)
    assert "Alice" in got  # control: proves Jean's absence is the exclusion
    assert "Jean" not in got


def test_split_exclusion_is_normalized_comparison(tmp_path):
    """The sidecar key and the re-extracted candidate name need not be
    byte-identical -- exclusion compares via canon_senses.py's shared
    normalize_form() (NFC + casefold + whitespace-collapse), same as the
    recollapse guard (1d)."""
    nc, canon = write_inputs(tmp_path, [cand("  jean ", freq=20)])
    senses = write_senses(tmp_path, {"Jean": split_entry("s1", "s2")})
    result = run_ok(nc, canon, "--senses-path", str(senses))
    assert names_in_args(result) == set()


def test_split_exclusion_absent_without_senses_data(tmp_path):
    """Control for the above: the SAME candidate set with an EMPTY sidecar
    (schema-valid, no entries) is not excluded -- proves the exclusion is
    driven by actual split data, not a blanket drop."""
    nc, canon = write_inputs(tmp_path, [cand("Jean", freq=20)])
    senses = write_senses(tmp_path, {})
    result = run_ok(nc, canon, "--senses-path", str(senses))
    assert names_in_args(result) == {"Jean"}


def test_split_exclusion_not_overridable_by_retry(tmp_path):
    """A split is a settled, evidence-verified identity decision, not a
    review_queue research item -- unlike the review_queue exclusion,
    --retry must NOT reinstate a split-excluded form (redispatching it
    would only be rejected forever by canon_validate.py's recollapse
    guard, per the module docstring)."""
    nc, canon = write_inputs(tmp_path, [cand("Jean", freq=20)])
    senses = write_senses(tmp_path, {"Jean": split_entry("s1", "s2")})
    result = run_ok(nc, canon, "--senses-path", str(senses), "--retry", "Jean")
    assert names_in_args(result) == set()


def test_split_exclusion_retry_emits_non_overridable_note(tmp_path):
    """The non-fatal --retry diagnostics (stderr only, exit stays 0) explain
    a split-excluded retry rather than misreporting it as a likely_name/freq
    curation drop."""
    nc, canon = write_inputs(tmp_path, [cand("Jean", freq=20, likely_name=True)])
    senses = write_senses(tmp_path, {"Jean": split_entry("s1", "s2")})
    proc = run(nc, canon, "--senses-path", str(senses), "--retry", "Jean")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"no_new_candidates": True, "batches": []}
    assert "Jean" in proc.stderr
    assert "cannot be overridden by --retry" in proc.stderr


def test_split_exclusion_leaves_non_split_candidates_alone(tmp_path):
    """A multi-sense sidecar with an unrelated split does not touch any
    other candidate."""
    nc, canon = write_inputs(
        tmp_path, [cand("Jean", freq=20), cand("Marie", freq=20), cand("Paul", freq=20)]
    )
    senses = write_senses(tmp_path, {"Jean": split_entry("s1", "s2")})
    result = run_ok(nc, canon, "--senses-path", str(senses))
    assert names_in_args(result) == {"Marie", "Paul"}


# ---------------------------------------------------------------------------
# --senses-path path-state policy (contract §10 / §11b, mirrors --canon's
# explicit-vs-default split at glossary_batch_plan.py:470)
# ---------------------------------------------------------------------------


def test_no_senses_flag_and_no_default_file_is_ok(tmp_path):
    """(§10.1) No --senses-path AND no file at the true self-anchored
    default -> treated as empty (no splits yet), never fatal. Runs the
    REAL script staged into a private durable_root (see `make_durable_root`
    above) so the default-path resolution is exercised for real, never
    against the shared plugin tree."""
    root = make_durable_root(tmp_path)
    nc, canon = write_inputs(root, [cand("Alice", freq=20)])
    result = run_staged_ok(root, nc, canon)
    assert names_in_args(result) == {"Alice"}


def test_explicit_missing_senses_path_fails(tmp_path):
    """(§10.2) An explicit --senses-path that does not exist is a caller
    error -> BLOCK (allow_absent=False), proving
    `senses_explicit = args.senses_path is not None` mirrors
    `canon_explicit` at glossary_batch_plan.py:470 exactly. A silently-empty
    result here would let a typo'd --senses-path bypass the exclusion
    entirely."""
    nc, canon = write_inputs(tmp_path, [cand("Alice", freq=20)])
    proc = run(nc, canon, "--senses-path", str(tmp_path / "nope-canon-senses.json"))
    assert proc.returncode != 0
    assert "canon_senses.json not found" in proc.stderr
    assert proc.stdout.strip() == ""


def test_explicit_malformed_senses_path_fails(tmp_path):
    """An explicit --senses-path pointing at a schema-invalid document
    (here: a 1-sense record, rejected by canon-senses.schema.json's own
    minItems:2) fails loudly via the same load_senses()->fail() path,
    never silently treated as "no splits"."""
    nc, canon = write_inputs(tmp_path, [cand("Alice", freq=20)])
    senses = write_senses(tmp_path, {"Jean": {"senses": [valid_sense("s1")]}})
    proc = run(nc, canon, "--senses-path", str(senses))
    assert proc.returncode != 0
    assert "failed schema validation" in proc.stderr
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------------------
# PRESENT-implicit-default coverage (contract §10.3/§10.4, R8-F1): no
# --senses-path flag at all, but a REAL file sits at the (staged) script's
# own self-anchored default path. Closes the gap the two sections above
# cannot: a consumer coded as `if args.senses_path is None: senses = EMPTY`
# passes every explicit-path test above while never loading the real
# default -- exactly how the Workflow invokes this script in production
# (glossary-pass-wf.template.js:228/267/280 call --check-batch with no
# override). Each test uses `make_durable_root` so the default sidecar is
# written to a private root, never the shared plugin tree.
# ---------------------------------------------------------------------------


def test_present_default_senses_path_excludes_split(tmp_path):
    """(§10.3) A valid, nonempty canon_senses.json sitting at the staged
    root's own default path -- no --senses-path flag -- must still drive
    the exclusion. Without this test, a planner that only ever loads
    `--senses-path` (and treats a bare flag-less invocation as "no senses
    data") would pass every test above yet still deadlock the real
    Workflow, which never passes the flag."""
    root = make_durable_root(tmp_path)
    nc, canon = write_inputs(
        root,
        [cand("Jean", freq=20, likely_name=True), cand("Alice", freq=20, likely_name=True)],
    )
    write_senses(root, {"Jean": split_entry("s1", "s2")})
    result = run_staged_ok(root, nc, canon)
    got = names_in_args(result)
    assert "Alice" in got
    assert "Jean" not in got


def test_present_default_malformed_senses_path_fails(tmp_path):
    """(§10.4) A schema-invalid document at the staged root's default path
    -- again no --senses-path flag -- BLOCKS, never silently treated as
    "no splits"."""
    root = make_durable_root(tmp_path)
    nc, canon = write_inputs(root, [cand("Alice", freq=20)])
    write_senses(root, {"Jean": {"senses": [valid_sense("s1")]}})
    proc = run_staged(root, nc, canon)
    assert proc.returncode != 0
    assert "failed schema validation" in proc.stderr
    assert proc.stdout.strip() == ""


def test_present_default_directory_senses_path_fails(tmp_path):
    """(§10.4, non-regular half) A directory sitting at the staged root's
    default path BLOCKS regardless of allow_absent -- mirrors
    canon_senses.py's `_path_state` "irregular" classification."""
    root = make_durable_root(tmp_path)
    nc, canon = write_inputs(root, [cand("Alice", freq=20)])
    (root / "canon_senses.json").mkdir()
    proc = run_staged(root, nc, canon)
    assert proc.returncode != 0
    assert "not a regular file" in proc.stderr
    assert proc.stdout.strip() == ""
