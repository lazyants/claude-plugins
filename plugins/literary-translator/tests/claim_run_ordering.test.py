"""tests/claim_run_ordering.test.py -- #438 D1a: pins the ONE assumption
the claim mechanism's run-ordering inversion rests on.

## The property, and why it needs a dedicated, DIRECT test

D1a (PLAN.md) inverts one step of W5: for a claim run, `resume_setup.py`
(kind `mass`) must run BEFORE `select_segments.py`, and `select_segments.py`
takes `--run-id` explicitly instead of deriving it. Today's ordinary order
is the opposite -- `select_segments.py` -> `resume_setup.py` -- so the
inversion is sound only if `resume_setup.py` has ZERO input dependency on
`select_segments.py`'s own output (`SEGS`). If it ever consulted `SEGS` (or
the deprecated, still-accepted `segs` payload field) to build its digest
domain, running it FIRST would be building that digest over the wrong (or
no) segment set, and the whole ordering would be unsound.

PLAN.md's own words: **"nothing else in the suite would notice if a future
change made `resume_setup.py` read `SEGS` again"** -- `tests/
resume_integrity.test.py` already has `test_mass_segs_field_is_ignored_
entirely` / `test_mass_domain_now_comes_from_manifest_not_segs` /
`test_mass_domain_stable_when_only_segs_shrinks_not_manifest`, and all
three assert only the FINAL `input_digest` string, never what was actually
QUERIED to build it. A test that only compares two digests can pass for the
wrong reason if a future refactor makes the digest dominated by something
else entirely (`version`, say) -- two digests being equal would prove
nothing about whether `domain` itself still depends on manifest.json.

This file proves the DOMAIN directly: it monkeypatches `_load_manifest_seg_
ids()` and `_cache_key_for_seg()` -- the two functions `compute_input_
digest()` calls to build `domain` for `kind="mass"` -- and records which
segment ids `_cache_key_for_seg()` was actually invoked with. That is not a
property of the digest; it is a property of the QUERY, and it is what the
run-ordering inversion actually needs to hold.

## What this file does NOT re-test

It does not re-derive `cache_key.py`'s own 15-field hashing (that is
`ledger_composite_key.test.py`'s job), and it does not repeat resume_
integrity.test.py's digest-equality coverage (cases 1-6, `test_mass_*`) --
this file is additive: the in-process spy tests below could not have been
written by editing that file without changing its own subprocess-only house
style, and the black-box test at the end proves the OTHER half of D1a
(a resume_setup.py-minted RUN_ID is one `select_segments.py`'s own `--run-
id` validator actually accepts) that no existing file touches, because no
existing file composes the two scripts in this order at all.

House style: self-contained, duplicated fixtures rather than shared imports
(this plugin's established convention -- see resume_integrity.test.py's own
module docstring for the same rule stated at length). The in-process spy
tests use `importlib.util.spec_from_file_location` + `exec_module`, the same
technique `tests/resume_integrity.test.py`'s own `_load_module()` /
`_load_resume_setup_module()` already use for direct-function-call testing.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"

RESUME_SETUP_SRC = SCRIPTS_DIR / "resume_setup.py"
SELECT_SEGMENTS_SRC = SCRIPTS_DIR / "select_segments.py"
CLAIM_RECORD_SRC = SCRIPTS_DIR / "claim_record.py"

assert RESUME_SETUP_SRC.is_file(), f"resume_setup.py not found at {RESUME_SETUP_SRC}"
assert SELECT_SEGMENTS_SRC.is_file(), f"select_segments.py not found at {SELECT_SEGMENTS_SRC}"
assert CLAIM_RECORD_SRC.is_file(), f"claim_record.py not found at {CLAIM_RECORD_SRC}"


def _load_module(name, path):
    """The established in-process-import pattern for direct-function-call
    testing elsewhere in this suite (tests/resume_integrity.test.py's own
    `_load_module`)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The 15-field cache_key shape (references/ledger-and-resumability.md), kept
# only so a fake composite is schema-shaped -- these spy tests never hash a
# real one, so the field VALUES are irrelevant, only that each seg's is
# distinct.
CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]


def _fake_composite(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


BASE_SUBST = {
    "research_mode": "live",
    "verse_policy": "skip",
    "source_lang": "he",
    "target_lang": "en",
    "max_fix_rounds": 3,
    "batch_agent_cap": 5,
    "max_codex_jobs_per_batch": 400,
    "effort": "high",
    "citation_content_types": "",
}


def mass_payload(**over):
    """kind='mass', args pinned to {} (LT-409), no 'segs' key at all by
    default -- the actual D1a scenario: at claim-run time select_segments.py
    has not run yet, so there IS no SEGS to put in the payload."""
    payload = {"kind": "mass", "args": {}, "subst": dict(BASE_SUBST)}
    payload.update(over)
    return payload


def _fake_dirs(tmp_path):
    """A minimal stand-in for resolve_dirs()'s own returned dict -- only
    'durable_root' is ever actually read by the code path these spy tests
    exercise (_load_manifest_seg_ids and _cache_key_for_seg are both
    monkeypatched out, and _read_marker/_schemas_dir_hash are monkeypatched
    too), so the other three keys are present but never dereferenced."""
    return {
        "durable_root": tmp_path,
        "schemas_dir": tmp_path / "schemas",
        "runs_dir": tmp_path / "runs",
        "cache_key_script": tmp_path / "scripts" / "cache_key.py",
    }


def _patch_version_inputs(monkeypatch, mod):
    """Neutralizes the two version-gating inputs (_read_marker,
    _schemas_dir_hash) that compute_input_digest() also calls -- they read
    real files this fixture never creates, and this file's tests are about
    the DOMAIN input, not the version one, so both are pinned to constants."""
    monkeypatch.setattr(mod, "_read_marker", lambda path, what: f"const-{what}")
    monkeypatch.setattr(mod, "_schemas_dir_hash", lambda schemas_dir=None: "const-schemas")


# ---------------------------------------------------------------------------
# Part 1 -- in-process spy: prove the DOMAIN, not just the digest.
# ---------------------------------------------------------------------------

def test_mass_domain_is_queried_over_manifest_ids_not_payload_segs(tmp_path, monkeypatch):
    """The core D1a assumption, proven directly: `compute_input_digest()`
    for kind='mass' calls `_cache_key_for_seg()` for exactly the ids
    `_load_manifest_seg_ids()` returns -- never for anything drawn from the
    payload. The payload's deprecated `segs` field is set to a set with ZERO
    overlap with the manifest ids, so if a future regression ever unions,
    intersects, or substitutes the payload's segs for the manifest's, the
    recorded call set diverges from the manifest set and this test catches
    it -- a same-digest-across-two-SEGS-values test could not distinguish
    any of those three broken shapes from the correct one."""
    mod = _load_module("resume_setup_under_test_ordering", RESUME_SETUP_SRC)
    _patch_version_inputs(monkeypatch, mod)

    manifest_ids = ["mseg_a", "mseg_b", "mseg_c"]
    monkeypatch.setattr(mod, "_load_manifest_seg_ids", lambda durable_root: list(manifest_ids))

    queried = []

    def fake_cache_key_for_seg(seg, cache_key_script, durable_root, durable_root_str, plugin_root_str):
        queried.append(seg)
        return _fake_composite(seg)
    monkeypatch.setattr(mod, "_cache_key_for_seg", fake_cache_key_for_seg)

    payload = mass_payload(segs=["totally_unrelated_x", "totally_unrelated_y"])
    digest = mod.compute_input_digest(payload, dirs=_fake_dirs(tmp_path))

    assert isinstance(digest, str) and digest, "compute_input_digest must still return a real digest"
    assert sorted(queried) == sorted(manifest_ids), (
        f"the cache-key domain must be queried over manifest.json's own ids "
        f"exactly -- got {sorted(queried)!r}, expected {sorted(manifest_ids)!r}. "
        f"A payload 'segs' field disjoint from the manifest must have ZERO "
        f"effect on which segments are queried."
    )
    assert len(queried) == len(manifest_ids), (
        "each manifest id must be queried exactly once -- a differing count "
        "means the domain is not a clean 1:1 mirror of manifest.json"
    )


def test_mass_domain_unaffected_by_segs_being_entirely_absent(tmp_path, monkeypatch):
    """The literal D1a scenario: at claim-run time select_segments.py has
    not run at all, so the real caller has no SEGS to put in the payload --
    the 'segs' key is not merely ignorable, it is ABSENT. Proves compute_
    input_digest() has no input dependency on the key's presence at all,
    which is the property that makes running resume_setup.py FIRST safe."""
    mod = _load_module("resume_setup_under_test_ordering_absent", RESUME_SETUP_SRC)
    _patch_version_inputs(monkeypatch, mod)

    manifest_ids = ["seg01", "seg02"]
    monkeypatch.setattr(mod, "_load_manifest_seg_ids", lambda durable_root: list(manifest_ids))
    queried = []

    def fake_cache_key_for_seg(seg, cache_key_script, durable_root, durable_root_str, plugin_root_str):
        queried.append(seg)
        return _fake_composite(seg)
    monkeypatch.setattr(mod, "_cache_key_for_seg", fake_cache_key_for_seg)

    payload = mass_payload()  # no 'segs' key at all
    assert "segs" not in payload
    digest = mod.compute_input_digest(payload, dirs=_fake_dirs(tmp_path))

    assert isinstance(digest, str) and digest
    assert sorted(queried) == sorted(manifest_ids)


def test_mass_digest_identical_whether_segs_is_absent_present_or_wrong(tmp_path, monkeypatch):
    """Non-regression control pairing the two tests above: the same
    manifest-derived domain must produce the IDENTICAL digest regardless of
    whether the payload's 'segs' key is absent, matches the manifest, or
    names segments that don't exist anywhere -- three shapes a real caller
    could produce depending on which release built the payload, all of
    which must be equivalent."""
    manifest_ids = ["seg01", "seg02"]

    def digest_for(segs_value):
        mod = _load_module(f"resume_setup_under_test_variant_{id(segs_value)}", RESUME_SETUP_SRC)
        monkeypatch.setattr(mod, "_read_marker", lambda path, what: f"const-{what}")
        monkeypatch.setattr(mod, "_schemas_dir_hash", lambda schemas_dir=None: "const-schemas")
        monkeypatch.setattr(mod, "_load_manifest_seg_ids", lambda durable_root: list(manifest_ids))
        monkeypatch.setattr(
            mod, "_cache_key_for_seg",
            lambda seg, cache_key_script, durable_root, durable_root_str, plugin_root_str: _fake_composite(seg),
        )
        payload = mass_payload() if segs_value is None else mass_payload(segs=segs_value)
        return mod.compute_input_digest(payload, dirs=_fake_dirs(tmp_path))

    d_absent = digest_for(None)
    d_matching = digest_for(["seg01", "seg02"])
    d_bogus = digest_for(["nonexistent_seg_zz"])

    assert d_absent == d_matching == d_bogus, (
        f"digest must be identical across segs=absent/matching/bogus, got "
        f"{d_absent!r} / {d_matching!r} / {d_bogus!r}"
    )


# ---------------------------------------------------------------------------
# Part 2 -- black-box: the inverted order actually composes end to end.
# ---------------------------------------------------------------------------

def _make_ordering_root(tmp_path, manifest_segs=("seg01", "seg02")):
    """A minimal real durable_root for driving the REAL, shipped
    resume_setup.py via subprocess -- no select_segments.py fixture needed
    for Part 2 (its own `--run-id` acceptance is exercised in-process via
    validate_run_id(), never through a full claim dispatch, which would
    need a much heavier fixture and would couple this file to selector's
    still-moving surface for no added assurance -- validate_run_id() is the
    exact, narrow interface point the ordering claim depends on)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(RESUME_SETUP_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")

    fake_cache_key = '''#!/usr/bin/env python3
import argparse, json, sys
p = argparse.ArgumentParser()
p.add_argument("--seg", required=True)
p.add_argument("--durable-root", default=None)
a = p.parse_args()
print(json.dumps({f: f + "-" + a.seg for f in %r}))
''' % (CACHE_KEY_FIELDS,)
    (scripts_dir / "cache_key.py").write_text(fake_cache_key, encoding="utf-8")

    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "dummy.schema.json").write_text('{"type": "object"}', encoding="utf-8")

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / ".plugin_bundle_hash").write_text("pbh-v1", encoding="utf-8")
    (runs_dir / ".orchestration_bundle_hash").write_text("obh-v1", encoding="utf-8")

    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in manifest_segs]}), encoding="utf-8"
    )
    return root


def _run_resume_setup(root, payload_obj, timeout=30):
    payload_path = root / "scratch_payload.json"
    payload_path.write_text(json.dumps(payload_obj), encoding="utf-8")
    cmd = [sys.executable, str(root / "scripts" / "resume_setup.py"), "--payload-file", str(payload_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    parsed = json.loads(lines[0]) if len(lines) == 1 else None
    return proc, parsed


def test_resume_setup_runs_with_no_segs_available_at_all_and_mints_a_valid_run_id(tmp_path):
    """Part 2, step 1 -- the actual claim-run sequence, exercised for real:
    resume_setup.py is invoked BEFORE anything resembling select_segments.py
    output exists (no 'segs' key in the payload, matching the true ordering
    D1a describes), against the REAL shipped script and a real manifest.json
    -- and succeeds, minting a well-formed RUN_ID."""
    root = _make_ordering_root(tmp_path)
    payload = mass_payload()
    assert "segs" not in payload

    proc, parsed = _run_resume_setup(root, payload)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert parsed is not None and parsed.get("success") is True, parsed
    run_id = parsed.get("effectiveRunId")
    assert isinstance(run_id, str) and run_id, f"expected a minted RUN_ID, got {parsed!r}"


def test_resume_setup_minted_run_id_is_accepted_by_select_segments_own_validator(tmp_path):
    """Part 2, step 2 -- the actual interface boundary the ordering
    inversion crosses: the RUN_ID resume_setup.py just minted (a real value,
    from a real subprocess run, never a hand-typed guess at the format) must
    be accepted by select_segments.py's OWN `validate_run_id()` -- the exact
    function D1a's `select_segments.py --run-id` flag consults
    (select_segments.py:6003, `run_id_problem = validate_run_id(run_id)`).

    This is deliberately narrower than a full claim dispatch: it proves the
    single interface point the ordering claim actually depends on (does a
    freshly-minted value from one script pass the other script's own
    acceptance check), without coupling this file to selector's still-moving
    claim-fixture internals for no added assurance. tests/resume_gate_skip_
    detection.test.py's test_both_copies_of_validate_run_id_agree already
    pins that the two validate_run_id() copies AGREE with each other in the
    abstract; this test additionally proves a REAL minted value clears the
    consuming copy, not just that the two copies would agree on some
    hypothetical input."""
    root = _make_ordering_root(tmp_path)
    proc, parsed = _run_resume_setup(root, mass_payload())
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert parsed is not None and parsed.get("success") is True, parsed
    run_id = parsed["effectiveRunId"]

    selector_mod = _load_module("select_segments_under_test_ordering", SELECT_SEGMENTS_SRC)
    problem = selector_mod.validate_run_id(run_id)
    assert problem is None, (
        f"select_segments.py's own validate_run_id() rejected a RUN_ID "
        f"resume_setup.py itself just minted ({run_id!r}): {problem}"
    )


def test_resume_setup_minted_run_id_is_accepted_by_the_chokepoints_own_validator(tmp_path):
    """Part 2, step 3 -- the SECOND interface point the inverted order crosses,
    and the one that fails closed if it is wrong.

    D1a's minted RUN_ID does not stop at select_segments.py: it is threaded on
    to every codex_job.py dispatch as `--run-id`, where the D8 chokepoint uses
    it to build runs/<RUN_ID>/.claimed.<seg>. codex_job.py's main() now REFUSES
    a --run-id that claim_record.py's own validate_run_id() rejects, with a
    usage exit 2 -- so a validator that disagreed with the minter would not
    degrade gracefully: EVERY translate and review dispatch of that run would
    die at usage time, after the claim step had already re-stamped the drafts.

    Deliberately checks a REAL minted value from a real subprocess run rather
    than a hand-typed guess at the format, exactly as the select_segments.py
    test above does -- tests/run_id_pattern_drift.test.py already pins that the
    copies AGREE with each other on an adversarial probe corpus, which is a
    different property from "the value the minter actually produces clears the
    consumer". Tighten claim_record's pattern (a fixed length, a lowercase-only
    class, a stricter timestamp shape) and this fails here rather than in
    production on the first dispatch of a claim run."""
    root = _make_ordering_root(tmp_path)
    proc, parsed = _run_resume_setup(root, mass_payload())
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert parsed is not None and parsed.get("success") is True, parsed
    run_id = parsed["effectiveRunId"]

    claim_record = _load_module("claim_record_under_test_ordering", CLAIM_RECORD_SRC)
    problem = claim_record.validate_run_id(run_id)
    assert problem is None, (
        f"claim_record.py's validate_run_id() -- the copy codex_job.py's own "
        f"--run-id check calls -- rejected a RUN_ID resume_setup.py itself just "
        f"minted ({run_id!r}): {problem}. Every dispatch of a claim run would "
        f"exit 2 at usage time."
    )
    # The value must also survive the path join it exists for: claimed_path()
    # raises on anything validate_run_id() refuses, and a colon-bearing seg id
    # (a real, shipped shape) must still round-trip through it untouched.
    path = claim_record.claimed_path(run_id, "FRONTBACK:errata_02", Path("/durable/runs"))
    assert path.name == ".claimed.FRONTBACK:errata_02", path
    assert path.parent.name == run_id, path


def test_manifest_change_still_forces_fresh_run_even_with_segs_absent(tmp_path):
    """Regression control for Part 2's own fixture shape (not a repeat of
    resume_integrity.test.py's cases, which never test the segs-ABSENT
    payload shape): growing manifest.json between two invocations, with
    'segs' never present in the payload at all, must still force a fresh,
    non-resuming RUN_ID -- proving the earlier tests' "digest unaffected by
    segs" property is not because domain stopped mattering to the digest
    altogether."""
    root = _make_ordering_root(tmp_path, manifest_segs=("seg01", "seg02"))
    payload = mass_payload()
    proc0, parsed0 = _run_resume_setup(root, payload)
    assert parsed0 is not None and parsed0.get("success") is True, parsed0
    run_id0 = parsed0["effectiveRunId"]

    resumed_payload = dict(payload)
    resumed_payload["resume_from_run_ids"] = [run_id0]
    proc1, parsed1 = _run_resume_setup(root, resumed_payload)
    assert parsed1 is not None and parsed1.get("success") is True, parsed1
    assert parsed1.get("resume") is True, f"identical inputs must resume: {parsed1}"
    assert parsed1.get("effectiveRunId") == run_id0

    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": "seg01"}, {"seg": "seg02"}, {"seg": "seg03"}]}),
        encoding="utf-8",
    )
    proc2, parsed2 = _run_resume_setup(root, resumed_payload)
    assert parsed2 is not None and parsed2.get("success") is True, parsed2
    assert parsed2.get("resume") is False, (
        f"growing manifest.json must force a fresh run even with 'segs' "
        f"absent throughout: {parsed2}"
    )
    assert parsed2.get("effectiveRunId") != run_id0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
