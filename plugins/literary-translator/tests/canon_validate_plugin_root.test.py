"""tests/canon_validate_plugin_root.test.py -- #412: canon_validate.py's
--plugin-root override for the cache_key.py sibling it shells out to when
STAMPING generation_hashes into canon.json (--init, --restamp-derivation,
--merge-batches, legacy --batch).

## The defect this closes

canon_validate.py is itself Step-0a-copied (not among SKILL.md's four
never-copied plugin-path scripts), so in production its own SCRIPTS_DIR --
where it resolves cache_key.py from -- IS the durable-root copy the codex
process can write to. A tampered cache_key.py there would let a poisoned
copy compute the very hashes that later gate canon reuse
(select_segments.py's derivation-state gate).

## Fixture strategy

Reuses `_canon_project_fixture.py` -- the SAME shared builder
`canon_stamp_conservation.test.py`/`canon_init_zero_candidate_bootstrap.
test.py` already use, staging the REAL cache_key.py (never a stub), so
every question this file asks about `generation_hashes` is about a
GENUINE hash, not a fixture artifact. `--init` is used as the simplest of
the four writing modes to establish the proof shape below; the same shape
is then re-run for the other three (`--restamp-derivation`, legacy
`--batch` / `run_merge`, and `--merge-batches`) further down, since all
four funnel through the SAME `_stamp_write_verify`/`_stamp_generation_hash`
call chain this override lives in -- and, until this file grew these
extra cases, only `--init`'s own leg of that chain was ever exercised
with `--plugin-root` at all.

## The poisoned-sibling proof, both halves

The durable root's own copy of cache_key.py is replaced with a tampered
stand-in that always fails loudly and distinctively (never silently fakes
success), then BOTH directions are asserted: `--plugin-root` pointing at a
separate, untampered location bypasses it, AND running the durable sibling
genuinely executes the poisoned copy. Without the second half the first
proves nothing -- a script that never touched cache_key.py at all would look
identical to one that correctly routed around the poison.

## #412: the flag is no longer optional, and this file owns that guard

The self-anchored sibling used to be the SILENT DEFAULT, so a call
site that simply never learned about `--plugin-root` stamped through
whatever `cache_key.py` happened to sit in `${durable_root}/scripts/` -- the
directory the codex process the stamp gates holds `--write` over. Every
STAMPING mode now refuses to proceed without an ANSWER: `--plugin-root PATH`
(the trusted plugin install root) or the explicit `--allow-durable-sibling`
escape hatch for a hand-run recovery with no orchestrating session to supply
one. Both together is itself an error. The NON-stamping modes resolve no
sibling and are deliberately untouched.

Four properties per stamping mode, and a guard is only proven by BOTH sides:

  * neither flag  -> exit 2, naming both flags AND the mode's own flag;
  * `--plugin-root <trusted>` -> works, and provably routes around a
    poisoned durable sibling;
  * `--allow-durable-sibling` -> works, and provably RUNS the durable
    sibling (the opt-out must opt into the OLD behaviour, not into some
    third thing);
  * both flags    -> exit 2.

The per-mode battery is parameterized off `canon_validate.py`'s OWN
`MODE_SPECS.stamps_generation_hashes` column rather than a hand-typed list
of four, with a drift guard pinning the two sets equal -- so a stamping mode
added later inherits this battery instead of having to be remembered by it,
which is the same failure mode the table itself exists to remove.

Assertions are on the SEMANTIC content of the refusal (both flag names
present, the mode's own flag named), never on the whole sentence: a
wording-exact assertion would freeze prose that is meant to be improvable.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    SCRIPTS_SRC,
    accepted_item,
    load_canon_validate_module,
    make_project,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    write_fragment,
)


def parse_last_json_line(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"expected at least one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# The poisoned-sibling fixture -- same shape select_segments.test.py/
# ledger_merge.test.py already use for their own --plugin-root batteries.
# ---------------------------------------------------------------------------

_TAMPERED_CACHE_KEY_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_CACHE_KEY_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_cache_key(root: Path) -> None:
    """Overwrites the durable-root copy of cache_key.py with a stand-in for
    a codex-tampered script: it always fails loudly and distinctively
    rather than silently faking success, so a test can tell whether THIS
    copy ran at all, in either direction."""
    (root / "scripts" / "cache_key.py").write_text(_TAMPERED_CACHE_KEY_SRC, encoding="utf-8")


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install") -> Path:
    """A SEPARATE physical location holding the REAL cache_key.py at the
    {plugin_root}/assets/scripts/ layout SKILL.md documents for the
    plugin-anchored scripts -- standing in for the plugin's actual install
    tree, physically apart from any durable_root fixture."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPTS_SRC / "cache_key.py", plugin_scripts_dir / "cache_key.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(SCRIPTS_SRC / "json_stdout.py", plugin_scripts_dir / "json_stdout.py")
    return plugin_root


# ---------------------------------------------------------------------------
# Measured proof of the forwarding asymmetry -- not asserted from the task
# brief's sentence, from actually running the leaf against the flag.
# ---------------------------------------------------------------------------

def test_cache_key_py_itself_rejects_plugin_root(tmp_path):
    """cache_key.py is a LEAF: it has no siblings of its own to resolve, and
    does not accept --plugin-root at all. This is why canon_validate.py
    must never forward the flag to it -- confirmed here by actually running
    the real cache_key.py against it, not by trusting the claim."""
    root = make_project(tmp_path)

    proc = run_script(
        root, "cache_key.py", "--field", "particle_config_hash",
        "--plugin-root", "/nonexistent",
    )

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "unrecognized arguments" in proc.stderr
    assert "--plugin-root" in proc.stderr


# ---------------------------------------------------------------------------
# #412 -- --plugin-root PATH, both halves of the poisoned-sibling proof.
# ---------------------------------------------------------------------------

def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core security property: canon_validate.py runs from its own
    in-place durable-root copy whose SIBLING cache_key.py has been
    POISONED. --plugin-root pointing at a separate, untampered location
    must make it use THAT cache_key.py instead -- a genuine stamp is
    possible ONLY if the poisoned durable-root sibling was never
    executed."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_canon_validate(
        root, "--init", "--plugin-root", str(plugin_root), allow_durable_sibling=False
    )

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must succeed "
        f"even though durable_root's own copy is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["created"] is True
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


# ---------------------------------------------------------------------------
# #412 gap closure -- the same poisoned-sibling proof for the other three
# `_stamp_write_verify` callers. Until now only `--init` (above) exercised
# `--plugin-root` at all: a whole-suite search found `--plugin-root` next to
# `--restamp-derivation`, `--batch`, or `--merge-batches` in NO test file.
# `resolve_cache_key_script`/`_stamp_generation_hash` are shared code, but
# each of these three threads its OWN `plugin_root_str` parameter through
# from `main()`'s dispatch (canon_validate.py:4155-4195) to its own
# `_stamp_write_verify` call -- a regression in any one of those three
# threading sites would not be caught by the `--init` test above.
# ---------------------------------------------------------------------------


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_restamp_derivation(tmp_path):
    """Same core property as the --init test above, for `run_restamp_derivation`
    (~:2030). This mode requires an EXISTING canon.json, so bootstrap happens
    BEFORE the durable root is poisoned -- --init itself needs a working
    cache_key.py to succeed."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_canon_validate(
        root,
        "--restamp-derivation",
        "--plugin-root",
        str(plugin_root),
        allow_durable_sibling=False,
    )

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let "
        f"--restamp-derivation succeed even though durable_root's own copy "
        f"is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "restamp_derivation"
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_merge(tmp_path):
    """Same proof for `run_merge` (legacy `--batch`, ~:2092). No prior
    canon.json is needed: `_load_canon` returns a fresh skeleton for a
    missing file, and `_preservable_prior` returns None for a missing file
    too, so this first merge always restamps -- exactly like --init's own
    bootstrap does. A real accepted item (not an empty fragment) is used so
    this also proves the override is honored on a merge that actually
    writes content, not just on a content-free write."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)
    frag = write_fragment(root, [accepted_item("אברהם", "Abraham")])

    proc = run_canon_validate(
        root, "--batch", str(frag), "--plugin-root", str(plugin_root), allow_durable_sibling=False
    )

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let a legacy "
        f"--batch merge succeed even though durable_root's own copy is "
        f"poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "merge"
    assert payload["merged_accepted"] == 1
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    assert "אברהם" in canon["entries"]
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_merge_batches(tmp_path):
    """Same proof for `run_merge_batches` (~:2201), across TWO fragments in
    one call -- its distinguishing shape from legacy --batch, and the mode
    #193/#291 name as the modern replacement for the unsanctioned restamp
    trick."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)
    frag1 = write_fragment(root, [accepted_item("אברהם", "Abraham")], "f1.json")
    frag2 = write_fragment(root, [accepted_item("רבקה", "Rebecca")], "f2.json")

    proc = run_canon_validate(
        root,
        "--merge-batches",
        str(frag1),
        str(frag2),
        "--plugin-root",
        str(plugin_root),
        allow_durable_sibling=False,
    )

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let "
        f"--merge-batches succeed even though durable_root's own copy is "
        f"poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "merge_batches"
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    assert "אברהם" in canon["entries"] and "רבקה" in canon["entries"]
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )



# ===========================================================================
# #412 -- the trusted-sibling PRECONDITION.
#
# Everything above proves what --plugin-root DOES. This section proves that
# a stamping mode may no longer be run without answering the question at all,
# and that each of the two possible answers does what it says.
#
# Parameterized off canon_validate.py's OWN MODE_SPECS table rather than a
# hand-typed list of four modes. A hand-typed list is exactly what the table
# exists to remove: a stamping mode added later would be added to the script
# and silently missed here, and the drift guard below is what makes that
# impossible -- it fails if the script's stamping set and this file's case
# table stop agreeing, in EITHER direction.
# ===========================================================================


def _argv_init(root: Path) -> list:
    return ["--init"]


def _argv_restamp_derivation(root: Path) -> list:
    return ["--restamp-derivation"]


def _argv_merge_batches(root: Path) -> list:
    return [
        "--merge-batches",
        str(write_fragment(root, [accepted_item("אברהם", "Abraham")], "mb_frag.json")),
    ]


def _argv_legacy_batch(root: Path) -> list:
    return [
        "--batch",
        str(write_fragment(root, [accepted_item("אברהם", "Abraham")], "legacy_frag.json")),
    ]


# dest -> (argv builder, does this mode need an EXISTING canon.json?).
# The key is `spec.dest`, so the legacy bare-`--batch` merge is keyed by None
# exactly as MODE_SPECS keys it -- it is a real mode with a real row, and
# leaving it out of the battery is precisely how it has escaped guards before.
# --restamp-derivation is the one mode that refuses to run without a canon to
# restamp; its bootstrap therefore happens BEFORE any poisoning, since --init
# itself needs a working cache_key.py.
STAMPING_MODE_CASES = {
    "init": (_argv_init, False),
    "restamp_derivation": (_argv_restamp_derivation, True),
    "merge_batches": (_argv_merge_batches, False),
    None: (_argv_legacy_batch, False),
}

# The complement: modes that resolve NO sibling and so must be completely
# unaffected -- neither newly refused nor quietly changed by the escape hatch.
NON_STAMPING_MODE_CASES = {
    "check_batch": (
        lambda root: [
            "--check-batch",
            str(write_fragment(root, [accepted_item("אברהם", "Abraham")], "cb_frag.json")),
        ]
    ),
    "verify_merged": (
        lambda root: [
            "--verify-merged",
            "--batch",
            str(write_fragment(root, [accepted_item("אברהם", "Abraham")], "vm_frag.json")),
        ]
    ),
    # #495. --correct WRITES canon.json but does not STAMP it -- it carries the
    # existing generation_hashes forward verbatim and computes no hash -- so it
    # resolves no sibling cache_key.py and belongs on this side of the table.
    # This fixture has no canon.json, so both invocations fail identically on
    # that; the property under test is that the escape hatch changes NOTHING,
    # which exact-equality asserts regardless of which outcome they share.
    "correct": (
        lambda root: [
            "--correct",
            str(
                write_fragment(
                    root,
                    {
                        "source_form": "אברהם",
                        "disposition": "remove",
                        "old_entry": {
                            "source_form": "אברהם",
                            "is_proper_name": True,
                            "canonical_target_form": "Abraham",
                            "basis": "transliterated",
                            "confidence": "high",
                        },
                        "reason": "fixture correction for the #412 no-op battery",
                    },
                    "correction.json",
                )
            ),
        ]
    ),
}



def _canon_bytes(root: Path):
    """canon.json's exact bytes, or None when it does not exist.

    The before/after pair around a REFUSED invocation has to distinguish
    "unchanged" from "created" from "rewritten", so absence is a value here
    rather than an error -- two of the four stamping modes run against a
    project that has no canon.json yet."""
    path = root / "canon.json"
    return path.read_bytes() if path.exists() else None


def _mode_ids(dest):
    return "legacy-batch" if dest is None else dest


def _spec_for(dest):
    """The MODE_SPECS row for `dest`, read from the REAL script -- so the
    flag name each assertion below looks for is the script's own spelling,
    never a copy of it that could drift."""
    module = load_canon_validate_module()
    matches = [spec for spec in module.MODE_SPECS if spec.dest == dest]
    assert len(matches) == 1, f"expected exactly one MODE_SPECS row for dest={dest!r}, got {matches}"
    return matches[0]


def test_the_precondition_battery_covers_exactly_the_scripts_own_mode_table():
    """The drift guard for this whole section, in BOTH directions.

    A stamping mode present in the script but missing from
    STAMPING_MODE_CASES would ship with no refusal coverage at all -- the
    silent-default failure #412 exists to close, reintroduced one mode at a
    time. A case here with no matching row means the battery is asserting
    against a mode that no longer stamps, so its "refuses without a flag"
    assertions would be testing nothing.

    The non-stamping half matters just as much: it is what proves the guard
    did not widen. Without it, flipping every row to `True` would leave this
    file entirely green."""
    module = load_canon_validate_module()
    stamping = {spec.dest for spec in module.MODE_SPECS if spec.stamps_generation_hashes}
    non_stamping = {spec.dest for spec in module.MODE_SPECS if not spec.stamps_generation_hashes}

    assert stamping == set(STAMPING_MODE_CASES), (
        "canon_validate.py's STAMPING modes and this file's refusal battery "
        "have drifted apart.\n"
        f"  stamps generation_hashes but has NO case here: {sorted(stamping - set(STAMPING_MODE_CASES), key=str)}\n"
        f"  has a case here but no longer stamps: {sorted(set(STAMPING_MODE_CASES) - stamping, key=str)}"
    )
    assert non_stamping == set(NON_STAMPING_MODE_CASES), (
        "canon_validate.py's NON-stamping modes and this file's "
        "unchanged-behaviour battery have drifted apart.\n"
        f"  resolves no sibling but has NO case here: {sorted(non_stamping - set(NON_STAMPING_MODE_CASES), key=str)}\n"
        f"  has a case here but now stamps: {sorted(set(NON_STAMPING_MODE_CASES) - non_stamping, key=str)}"
    )


@pytest.mark.parametrize("dest", list(STAMPING_MODE_CASES), ids=_mode_ids)
def test_a_stamping_mode_refuses_when_neither_sibling_flag_is_given(tmp_path, dest):
    """The core refusal. Before #412 every one of these exited 0 (or 1 on a
    genuine content failure) having stamped through whatever cache_key.py sat
    in the codex-writable ${durable_root}/scripts/.

    The message assertions are SEMANTIC, not verbatim: an operator halted
    here can only act if the message names BOTH ways out and says which mode
    stopped. Pinning the sentence would freeze prose instead."""
    build_argv, needs_canon = STAMPING_MODE_CASES[dest]
    spec = _spec_for(dest)
    root = make_project(tmp_path)
    if needs_canon:
        assert run_canon_init(root).returncode == 0
    canon_before = _canon_bytes(root)

    proc = run_canon_validate(root, *build_argv(root), allow_durable_sibling=False)

    assert proc.returncode == 2, (
        f"{spec.flag} stamps generation_hashes, so with neither --plugin-root "
        f"nor --allow-durable-sibling it must halt as an argparse usage error "
        f"instead of stamping through a sibling nobody vouched for:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "--plugin-root" in proc.stderr, (
        f"the refusal for {spec.flag} does not name --plugin-root, so an "
        f"operator is halted without being told the trusted way out:\n{proc.stderr}"
    )
    assert "--allow-durable-sibling" in proc.stderr, (
        f"the refusal for {spec.flag} does not name --allow-durable-sibling, "
        f"so a hand-run recovery with no plugin root to name is halted with no "
        f"way out at all:\n{proc.stderr}"
    )
    assert spec.flag in proc.stderr, (
        f"the refusal does not name the mode that triggered it ({spec.flag!r}) "
        f"-- with several flags on one command line the operator cannot tell "
        f"which one to fix:\n{proc.stderr}"
    )

    canon_after = _canon_bytes(root)
    assert canon_after == canon_before, (
        "a refused invocation must not have touched canon.json -- the refusal "
        "happens in argument parsing, before any mode runs"
    )


@pytest.mark.parametrize("dest", list(STAMPING_MODE_CASES), ids=_mode_ids)
def test_a_stamping_mode_refuses_both_sibling_flags_at_once(tmp_path, dest):
    """Naming a trusted root AND waiving the requirement to name one state
    two different intentions, so the pair is a usage error rather than a
    silently-resolved precedence question.

    Parameterized over every stamping mode even though the check is
    mode-independent today: the two guards sit next to each other in main()
    and the mutual-exclusion one runs FIRST, so a future reordering that made
    the precondition swallow this case would otherwise go unnoticed."""
    build_argv, needs_canon = STAMPING_MODE_CASES[dest]
    spec = _spec_for(dest)
    root = make_project(tmp_path)
    if needs_canon:
        assert run_canon_init(root).returncode == 0
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_canon_validate(
        root,
        *build_argv(root),
        "--plugin-root",
        str(plugin_root),
        "--allow-durable-sibling",
        allow_durable_sibling=False,
    )

    assert proc.returncode == 2, (
        f"{spec.flag} accepted both --plugin-root and --allow-durable-sibling:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "--plugin-root" in proc.stderr and "--allow-durable-sibling" in proc.stderr, (
        f"the mutual-exclusion refusal must name both flags:\n{proc.stderr}"
    )
    assert "mutually exclusive" in proc.stderr, (
        f"the refusal must say WHY the pair is rejected, not merely reject "
        f"it -- an operator who passed both is otherwise left guessing which "
        f"one to drop:\n{proc.stderr}"
    )


@pytest.mark.parametrize("dest", list(STAMPING_MODE_CASES), ids=_mode_ids)
def test_the_durable_sibling_opt_out_actually_stamps(tmp_path, dest):
    """Half one of the opt-out proof: it must WORK. On an untampered fixture
    --allow-durable-sibling produces a genuine stamp, so the flag is a real
    way past the refusal and not merely a way to fail differently."""
    build_argv, needs_canon = STAMPING_MODE_CASES[dest]
    spec = _spec_for(dest)
    root = make_project(tmp_path)
    if needs_canon:
        assert run_canon_init(root).returncode == 0

    proc = run_canon_validate(root, *build_argv(root))  # appends --allow-durable-sibling

    assert proc.returncode == 0, (
        f"{spec.flag} --allow-durable-sibling must succeed against the "
        f"fixture's own untampered cache_key.py:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_last_json_line(proc)
    assert payload["success"] is True

    canon = read_canon(root)
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


@pytest.mark.parametrize("dest", list(STAMPING_MODE_CASES), ids=_mode_ids)
def test_the_durable_sibling_opt_out_genuinely_runs_the_durable_copy(tmp_path, dest):
    """Half two, and the one that makes the flag mean what it says.

    "It succeeded with --allow-durable-sibling" is satisfied equally by a
    script that ran the durable sibling, by one that quietly fell back to
    some other cache_key.py, and by one that skipped stamping altogether.
    Poisoning ${durable_root}/scripts/cache_key.py separates them: the run
    must FAIL, and fail with the poisoned copy's own distinctive marker --
    which is only possible if that exact file was executed. This is the same
    negative control the --plugin-root tests above depend on, pointed the
    other way.

    It also pins the write discipline: generation_hashes are resolved BEFORE
    the atomic write, so a failed stamp attempt leaves canon.json exactly as
    it found it rather than half-written."""
    build_argv, needs_canon = STAMPING_MODE_CASES[dest]
    spec = _spec_for(dest)
    root = make_project(tmp_path)
    if needs_canon:
        assert run_canon_init(root).returncode == 0
    canon_before = _canon_bytes(root)
    poison_durable_root_cache_key(root)

    proc = run_canon_validate(root, *build_argv(root))  # appends --allow-durable-sibling

    assert proc.returncode == 1, (
        f"{spec.flag} --allow-durable-sibling must genuinely execute the "
        f"poisoned ${{durable_root}}/scripts/cache_key.py -- the opt-out opts "
        f"into the OLD self-anchored behaviour, not into something else:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_last_json_line(proc)
    assert payload["success"] is False
    assert "TAMPERED_CACHE_KEY_MUST_NEVER_RUN" in payload["error"], (
        f"the failure does not carry the poisoned copy's own marker, so it "
        f"does not prove THAT file ran:\n{payload}"
    )

    canon_after = _canon_bytes(root)
    assert canon_after == canon_before, (
        "a failed stamp attempt must write NOTHING -- generation_hashes are "
        "resolved before the atomic write, never patched in afterward"
    )


@pytest.mark.parametrize("dest", list(NON_STAMPING_MODE_CASES), ids=str)
def test_a_non_stamping_mode_is_untouched_by_the_precondition(tmp_path, dest):
    """The guard must not widen. --check-batch, --correct and --verify-merged
    resolve no sibling at all, so none may acquire a new refusal, and the escape
    hatch must be a genuine no-op for them rather than quietly changing what
    they do.

    Asserted as EXACT equality of the two invocations rather than as "both
    exit 0": these modes legitimately differ in outcome on this fixture
    (--check-batch succeeds; --verify-merged and --correct both report a
    missing canon.json),
    and a same-rc assertion would pass even if the flag had changed the
    payload. Comparing rc, stdout AND stderr is what makes "ignored" mean
    ignored."""
    build_argv = NON_STAMPING_MODE_CASES[dest]
    root = make_project(tmp_path)
    argv = build_argv(root)

    without = run_canon_validate(root, *argv, allow_durable_sibling=False)
    with_flag = run_canon_validate(root, *argv)  # appends --allow-durable-sibling

    assert without.returncode != 2, (
        f"--{dest.replace('_', '-')} resolves no sibling and must NOT have "
        f"acquired the stamping precondition:\n"
        f"rc={without.returncode}\nstdout:\n{without.stdout}\nstderr:\n{without.stderr}"
    )
    assert "--allow-durable-sibling" not in without.stderr, (
        f"a non-stamping mode must never be told to name a sibling flag:\n{without.stderr}"
    )
    assert (with_flag.returncode, with_flag.stdout, with_flag.stderr) == (
        without.returncode,
        without.stdout,
        without.stderr,
    ), (
        f"--allow-durable-sibling is documented as ignored by the non-stamping "
        f"modes, but it changed what --{dest.replace('_', '-')} did:\n"
        f"  without: rc={without.returncode} stdout={without.stdout!r} stderr={without.stderr!r}\n"
        f"  with:    rc={with_flag.returncode} stdout={with_flag.stdout!r} stderr={with_flag.stderr!r}"
    )


def test_validate_only_is_untouched_by_the_precondition(tmp_path):
    """Validate-only -- no mode flag at all -- has no MODE_SPECS row, so it
    sits outside the table the battery above is generated from and cannot be
    reached by parameterizing over it. It stamps nothing, so it must not have
    acquired the refusal either; pinned by hand, exactly as the sibling
    table-driven guards pin it by hand for the same reason."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0

    proc = run_canon_validate(root, allow_durable_sibling=False)

    assert proc.returncode == 0, (
        f"validate-only resolves no sibling and must stay runnable with "
        f"neither flag:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert parse_last_json_line(proc)["success"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
