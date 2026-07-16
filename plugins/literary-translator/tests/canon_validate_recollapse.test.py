"""tests/canon_validate_recollapse.test.py -- regression coverage for
scripts/canon_validate.py's refuse-recollapse guard (RFC #215 item 1d):
an ACCEPTED batch item whose source_form is an adjudicated homonym split
in canon_senses.json (>=2 senses) must be refused outright rather than
merged into entries{} as a single bare entry, which would silently
collapse the two (or more) distinct target senses an adjudicator already
recorded back into one undifferentiated resolution.

Two layers of coverage:

  1. Pure in-process unit tests directly against `_merge_batch` (this
     file's first section) -- loads the REAL, in-place canon_validate.py
     via the same `sys.path.insert(SCRIPTS_DIR) + spec_from_file_location`
     idiom already used by tests/sense_translated_behaviour.test.py and
     tests/canon_map_delivery.test.py. Because this loads the script from
     its OWN real location (never a copy into an isolated durable_root),
     the sibling `from canon_senses import ...` resolves naturally against
     the real, adjacent canon_senses.py -- no isolated-fixture staging is
     needed for a test that only calls `_merge_batch` directly on in-memory
     dicts (it never touches DEFAULT_CANON_PATH/DEFAULT_SENSES_PATH or the
     filesystem at all).

  2. Real-CLI path-state/present-default and post-contract regression
     tests (this file's second section) -- these DO exercise the CLI's own
     default-sidecar-path resolution and therefore DO need an isolated
     durable_root with canon_validate.py, canon_senses.py, and
     canon-senses.schema.json all staged together, mirroring
     tests/canon_format_validation.test.py's/tests/glossary_fragment_merge.
     test.py's own `make_durable_root` pattern.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"

CANON_VALIDATE_SCRIPT = SCRIPTS_DIR / "canon_validate.py"
CANON_SENSES_SCRIPT = SCRIPTS_DIR / "canon_senses.py"
CANON_SENSES_SCHEMA = SCHEMAS_DIR / "canon-senses.schema.json"

assert CANON_VALIDATE_SCRIPT.is_file(), f"canon_validate.py not found at {CANON_VALIDATE_SCRIPT}"
assert CANON_SENSES_SCRIPT.is_file(), f"canon_senses.py not found at {CANON_SENSES_SCRIPT}"
assert CANON_SENSES_SCHEMA.is_file(), f"canon-senses.schema.json not found at {CANON_SENSES_SCHEMA}"


# ===========================================================================
# Section 1 -- pure in-process unit tests against `_merge_batch`
# ===========================================================================


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/sense_translated_behaviour.test.py's own loader exactly
    -- canon_validate.py's `from canon_senses import ...` only resolves via
    sys.path[0] under a real `python3 canon_validate.py` invocation, so its
    own scripts/ directory must be inserted onto sys.path around the
    in-process load."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


CANON_VALIDATE = _load_module("canon_validate_recollapse_under_test", CANON_VALIDATE_SCRIPT, SCRIPTS_DIR)


def _sense(sense_id: str, disambiguator: str) -> dict:
    """A minimal but schema-shaped sense record (canon-senses.schema.json's
    required evidence fields all present) -- content is otherwise
    arbitrary, since these unit tests never run it through
    canon_senses.py's own schema validator."""
    return {
        "sense_id": sense_id,
        "disambiguator": disambiguator,
        "index_scope": "narrative",
        "evidence": {
            "block": "PARA:seg01:0001",
            "seg": "seg01",
            "char_start": 0,
            "char_end": 4,
            "context_start": 0,
            "context_end": 10,
            "sha256": "0" * 64,
        },
    }


def _senses_result(entries_by_source_form: dict):
    return CANON_VALIDATE.SensesResult(
        is_empty=not entries_by_source_form,
        entries_by_source_form=entries_by_source_form,
    )


EMPTY_SENSES = _senses_result({})


def _accepted_item(source_form: str, **overrides) -> dict:
    item = {"source_form": source_form, "is_proper_name": True, "disposition": "accepted"}
    item.update(overrides)
    return item


def _empty_canon() -> dict:
    return {"entries": {}, "review_queue": []}


def test_merge_batch_refuses_recollapse_of_adjudicated_split():
    canon = _empty_canon()
    batch = [_accepted_item("Bob")]
    split_senses = _senses_result(
        {"Bob": {"senses": [_sense("s1", "the sailor"), _sense("s2", "the king")]}}
    )

    with pytest.raises(CANON_VALIDATE.CanonValidationError) as exc_info:
        CANON_VALIDATE._merge_batch(canon, batch, split_senses)

    message = str(exc_info.value)
    assert "'Bob'" in message
    assert "adjudicated homonym split" in message
    assert "2 senses in canon_senses.json" in message
    assert "recollapse" in message
    # _merge_batch never mutates its `canon` argument in place, and the
    # real call sites (run_merge/run_merge_batches) only write to disk
    # AFTER this returns successfully -- so a raised exception here means
    # canon.json is untouched. Assert the original dict itself proves that.
    assert canon == {"entries": {}, "review_queue": []}


def test_merge_batch_same_batch_succeeds_without_the_guard_red_before_green():
    """RED-before-GREEN witness: the identical canon+batch that the test
    above refuses is happily collapsed into a single bare entry when
    `senses` has no matching split -- proving the refusal above is caused
    BY the guard (is_split(senses, source_form)), not by some unrelated
    Pass-1/schema rejection this file never exercises. (The guard and
    `_merge_batch`'s new `senses` parameter landed in the same change, so
    there is no pre-change 2-arg call site left to stash back to for a
    literal git-stash red-before-green rerun; this guard-disabled control
    is the substitute the contract calls for.)"""
    canon = _empty_canon()
    batch = [_accepted_item("Bob")]

    merged = CANON_VALIDATE._merge_batch(canon, batch, EMPTY_SENSES)

    assert merged["entries"]["Bob"]["source_form"] == "Bob"


def test_merge_batch_refuses_recollapse_on_overwrite_of_an_existing_bare_entry():
    """The guard fires for an OVERWRITE of a pre-existing bare entry too,
    not only a brand-new insertion -- matching the docstring's "a
    brand-new insertion, an overwrite, AND a resubmission alike" claim."""
    canon = {
        "entries": {"Bob": {"source_form": "Bob", "is_proper_name": True}},
        "review_queue": [],
    }
    batch = [_accepted_item("Bob")]
    split_senses = _senses_result(
        {"Bob": {"senses": [_sense("s1", "a"), _sense("s2", "b")]}}
    )

    with pytest.raises(CANON_VALIDATE.CanonValidationError) as exc_info:
        CANON_VALIDATE._merge_batch(canon, batch, split_senses)
    assert "recollapse" in str(exc_info.value)


def test_merge_batch_refuses_recollapse_across_nfc_nfd_equivalent_source_form():
    """The guard compares via normalize_form (NFC + casefold +
    whitespace-collapse), never a raw dict-key lookup -- so an adjudicated
    split keyed under one Unicode normalization of a name still blocks an
    accepted item spelled with the OTHER, canonically-equivalent
    normalization. e-acute: senses keyed by the DECOMPOSED form (e +
    COMBINING ACUTE ACCENT, NFD); batch item spelled with the COMPOSED
    form (single U+00E9 codepoint, NFC) -- distinct raw strings, same
    string post-normalization."""
    composed = "Ren\u00e9"  # NFC: e-acute is one codepoint
    decomposed = "Rene\u0301"  # NFD: bare e + combining acute accent (U+0301)
    assert composed != decomposed  # distinct raw strings pre-normalization

    canon = _empty_canon()
    batch = [_accepted_item(composed)]
    split_senses = _senses_result(
        {decomposed: {"senses": [_sense("s1", "a"), _sense("s2", "b")]}}
    )

    with pytest.raises(CANON_VALIDATE.CanonValidationError) as exc_info:
        CANON_VALIDATE._merge_batch(canon, batch, split_senses)
    assert "recollapse" in str(exc_info.value)


def test_merge_batch_recollapse_collision_blocks_the_whole_batch_atomically():
    """A recollapse collision on ONE item must reject the WHOLE batch, not
    just skip the offending item -- `_merge_batch` never returns a partial
    `merged` doc on any collision (mirrors its pre-existing entries{}
    collision behavior, now shared by this guard)."""
    canon = _empty_canon()
    batch = [_accepted_item("Alice"), _accepted_item("Bob")]
    split_senses = _senses_result(
        {"Bob": {"senses": [_sense("s1", "a"), _sense("s2", "b")]}}
    )

    with pytest.raises(CANON_VALIDATE.CanonValidationError):
        CANON_VALIDATE._merge_batch(canon, batch, split_senses)
    # Alice's item was individually unblocked, but must not have leaked
    # into canon.entries either -- the collision keeps the merge atomic.
    assert canon == {"entries": {}, "review_queue": []}


def test_merge_batch_does_not_block_review_queue_disposition_for_a_split_source_form():
    """The guard only fires for disposition:"accepted" -- a `review_queue`
    item is not itself a recollapse (nothing is being merged as a bare
    single-sense entry yet), so it still queues normally even when its
    source_form separately has an adjudicated split."""
    canon = _empty_canon()
    batch = [{"source_form": "Bob", "disposition": "review_queue", "note": "needs review"}]
    split_senses = _senses_result(
        {"Bob": {"senses": [_sense("s1", "a"), _sense("s2", "b")]}}
    )

    merged = CANON_VALIDATE._merge_batch(canon, batch, split_senses)
    assert merged["review_queue"] == [batch[0]]


def test_matching_senses_entry_collision_index_matches_linear_scan_first_key_wins():
    """Mirrors canon_senses.test.py's own is_split collision-index test:
    two keys colliding under normalize_form can never survive
    load_senses's own procedural check, so this drives
    entries_by_source_form directly (bypassing load_senses) to pin
    _matching_senses_entry's collision semantics -- the indexed O(1) path
    (built the same way load_senses builds SensesResult.normalized_index:
    setdefault per normalized key) must return the exact same entry the
    linear scan would, the FIRST key in iteration order, never a later
    colliding key overwriting it."""
    first_entry = {"senses": [_sense("s1", "a")]}
    second_entry = {"senses": [_sense("s2", "a"), _sense("s3", "b")]}
    entries = {"Bob": first_entry, " bob ": second_entry}

    normalized_index = {}
    for key, entry in entries.items():
        normalized_index.setdefault(CANON_VALIDATE.normalize_form(key), entry)

    linear = _senses_result(entries)
    indexed = CANON_VALIDATE.SensesResult(
        is_empty=False, entries_by_source_form=entries, normalized_index=normalized_index
    )

    assert CANON_VALIDATE._matching_senses_entry(linear, "Bob") is first_entry
    assert CANON_VALIDATE._matching_senses_entry(indexed, "Bob") is first_entry


def test_matching_senses_entry_lookup_cost_does_not_scale_with_entry_count(monkeypatch):
    """Same O(1)-lookup regression as canon_senses.test.py's own is_split
    call-count witness, but for _matching_senses_entry's independent
    linear-scan-turned-indexed-lookup (canon_validate.py never imports
    canon_senses.load_senses's own index-building helper, so this test
    builds the index by hand the same way load_senses does, then drives
    _matching_senses_entry directly -- it never touches load_senses
    itself, matching this file's own Section-1 pure in-process style)."""
    n = 200
    entries = {
        f"name{i:04d}": {"senses": [_sense(f"s{i}a", "a"), _sense(f"s{i}b", "b")]}
        for i in range(n)
    }
    normalized_index = {}
    for key, entry in entries.items():
        normalized_index.setdefault(CANON_VALIDATE.normalize_form(key), entry)
    indexed = CANON_VALIDATE.SensesResult(
        is_empty=False, entries_by_source_form=entries, normalized_index=normalized_index
    )

    calls = []
    real_normalize_form = CANON_VALIDATE.normalize_form

    def _counting_normalize_form(s):
        calls.append(s)
        return real_normalize_form(s)

    monkeypatch.setattr(CANON_VALIDATE, "normalize_form", _counting_normalize_form)

    calls.clear()
    entry = CANON_VALIDATE._matching_senses_entry(indexed, "name0100")
    assert entry is entries["name0100"]
    assert len(calls) == 1, (
        f"expected exactly 1 normalize_form call (indexed), got {len(calls)} "
        f"for n={n} entries -- _matching_senses_entry is re-scanning every key"
    )


# ===========================================================================
# Section 2 -- real-CLI path-state/present-default (contract §10) and
# post-contract --senses-path merge regression (contract §11).
# ===========================================================================

CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)

# Only needed by tests that actually write canon.json (--merge-batches /
# --batch stamp generation_hashes via this stub); --check-batch never
# shells out to it. Mirrors tests/glossary_fragment_merge.test.py's own
# FAKE_CACHE_KEY_PY exactly.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field")
    parser.add_argument("--seg", default=None)
    args = parser.parse_args()
    if not args.field:
        sys.stderr.write("fake cache_key.py: test stub requires --field\\n")
        return 1
    print(f"fixture-{args.field}-hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_durable_root(tmp_path, with_cache_key=False):
    """Builds an isolated durable_root mirroring tests/glossary_fragment_
    merge.test.py's own `make_durable_root`: stages the REAL
    canon_validate.py + canon_senses.py + canon-senses.schema.json via the
    sanctioned tests/_senses_fixture.py helper, copies the REAL
    canon-*.schema.json files into {root}/schemas/, and -- only for tests
    that actually write canon.json -- the fake cache_key.py stamping
    stub."""
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    scripts_dir = root / "scripts"
    if with_cache_key:
        (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_DIR / name, schemas_dir / name)

    return root


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli(root: Path, args, timeout=30):
    cmd = [sys.executable, str(root / "scripts" / "canon_validate.py")] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))


def parse_stdout(proc):
    assert proc.stdout.strip(), (
        f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def cli_accepted_item(source_form: str, **overrides) -> dict:
    item = {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": "Placeholder Target",
        "basis": "transliterated",
        "confidence": "high",
        "disposition": "accepted",
    }
    item.update(overrides)
    return item


def senses_doc(entries_by_source_form: dict) -> dict:
    return {"schema_version": 1, "entries_by_source_form": entries_by_source_form}


def split_senses_entry() -> dict:
    return {"senses": [_sense("s1", "the sailor"), _sense("s2", "the king")]}


def test_check_batch_no_senses_path_and_no_default_file_is_ok(tmp_path):
    """Contract §10.1: no --senses-path override, and no default sidecar on
    disk at all -- the genuinely-absent-default case, tolerated as empty
    (no splits yet)."""
    root = make_durable_root(tmp_path)
    fragment = write_json(root / "frag.json", [cli_accepted_item("Alice")])

    proc = run_cli(root, ["--research-mode", "live", "--check-batch", str(fragment)])
    payload = parse_stdout(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload["success"] is True


def test_check_batch_explicit_senses_path_missing_blocks(tmp_path):
    """Contract §10.2: an EXPLICIT --senses-path that does not exist is a
    hard BLOCK (proves allow_absent=(args.senses_path is None) -- a typo'd
    path must never silently read as "no splits yet"), mirroring
    glossary_batch_plan.py's own canon_explicit discipline."""
    root = make_durable_root(tmp_path)
    fragment = write_json(root / "frag.json", [cli_accepted_item("Alice")])
    missing_senses = root / "nope_canon_senses.json"

    proc = run_cli(
        root,
        [
            "--research-mode", "live",
            "--check-batch", str(fragment),
            "--senses-path", str(missing_senses),
        ],
    )
    payload = parse_stdout(proc)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["success"] is False


def test_check_batch_present_valid_nonempty_default_fires_recollapse_block(tmp_path):
    """Contract §10.3, the R8-F1 gotcha this proves against: a consumer
    that only checks `if args.senses_path is None: senses = EMPTY` (never
    actually loading a PRESENT default file) would never fire this guard --
    the real glossary-pass Workflow calls --check-batch with NO
    --senses-path override at all (glossary-pass-wf.template.js), relying
    entirely on the DEFAULT sidecar path being read when it happens to
    already exist on disk. Here the default ${durable_root}/canon_senses.json
    is present and valid (a real split for 'Bob'), with NO --senses-path
    flag given, and the recollapse guard must still fire."""
    root = make_durable_root(tmp_path)
    write_json(root / "canon_senses.json", senses_doc({"Bob": split_senses_entry()}))
    fragment = write_json(root / "frag.json", [cli_accepted_item("Bob")])

    proc = run_cli(root, ["--research-mode", "live", "--check-batch", str(fragment)])
    payload = parse_stdout(proc)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["success"] is False
    assert "recollapse" in payload["error"]
    assert "'Bob'" in payload["error"]


def test_check_batch_malformed_default_senses_file_blocks(tmp_path):
    """Contract §10.4: a malformed default sidecar (invalid JSON) is a
    BLOCK, never silently treated as empty."""
    root = make_durable_root(tmp_path)
    (root / "canon_senses.json").write_text("{not valid json", encoding="utf-8")
    fragment = write_json(root / "frag.json", [cli_accepted_item("Alice")])

    proc = run_cli(root, ["--research-mode", "live", "--check-batch", str(fragment)])
    payload = parse_stdout(proc)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["success"] is False


def test_check_batch_non_regular_default_senses_path_blocks(tmp_path):
    """Contract §10.4: a non-regular default sidecar path (here, a
    directory in its place) is a BLOCK regardless of allow_absent."""
    root = make_durable_root(tmp_path)
    (root / "canon_senses.json").mkdir()
    fragment = write_json(root / "frag.json", [cli_accepted_item("Alice")])

    proc = run_cli(root, ["--research-mode", "live", "--check-batch", str(fragment)])
    payload = parse_stdout(proc)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["success"] is False


def test_merge_batches_senses_path_recollapse_blocks_and_canon_unchanged(tmp_path):
    """Post-contract regression (RFC #215 1d): --merge-batches +
    --senses-path end to end, real subprocess, real write path. A split
    entry for 'Bob' in the custom sidecar blocks the merge with the
    structured recollapse error, and canon.json is left completely
    unchanged -- _merge_batch raises before _stamp_write_verify's atomic
    write ever runs, so the file that never existed still does not."""
    root = make_durable_root(tmp_path, with_cache_key=True)
    senses_path = write_json(
        root / "custom_senses.json", senses_doc({"Bob": split_senses_entry()})
    )
    fragment = write_json(root / "frag.json", [cli_accepted_item("Bob")])
    canon_path = root / "canon.json"
    assert not canon_path.exists()

    proc = run_cli(
        root,
        [
            "--research-mode", "live",
            "--canon-path", str(canon_path),
            "--merge-batches", str(fragment),
            "--senses-path", str(senses_path),
        ],
    )
    payload = parse_stdout(proc)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["success"] is False
    assert "recollapse" in payload["error"]
    assert not canon_path.exists()


def test_merge_batches_senses_path_allows_unrelated_name_through(tmp_path):
    """The same custom --senses-path (a split only for 'Bob') does not
    block an UNRELATED accepted name -- 'Alice' merges normally end to
    end, landing in canon.json's entries{} exactly as any ordinary merge
    would."""
    root = make_durable_root(tmp_path, with_cache_key=True)
    senses_path = write_json(
        root / "custom_senses.json", senses_doc({"Bob": split_senses_entry()})
    )
    fragment = write_json(root / "frag.json", [cli_accepted_item("Alice")])
    canon_path = root / "canon.json"

    proc = run_cli(
        root,
        [
            "--research-mode", "live",
            "--canon-path", str(canon_path),
            "--merge-batches", str(fragment),
            "--senses-path", str(senses_path),
        ],
    )
    payload = parse_stdout(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload["success"] is True
    on_disk = json.loads(canon_path.read_text(encoding="utf-8"))
    assert on_disk["entries"]["Alice"]["source_form"] == "Alice"
