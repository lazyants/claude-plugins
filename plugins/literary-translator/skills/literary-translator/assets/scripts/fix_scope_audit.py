#!/usr/bin/env python3
"""fix_scope_audit.py -- copy-fidelity check over the durable root's
plugin-installed files, run around the W5 review-fix turn (#607).

PLUGIN-PATH-ONLY -- NEVER copied into a durable_root, and NOT a bundle
member. It joins `profile_validate.py`, `validate_extraction.py` and
`glossary_preflight.py` in SKILL.md's enumerated never-copied list, for the
same reason `validate_extraction.py` is there: a checker that lives inside
the tree it audits can be edited by the party it is checking, and would then
report on itself. `scaffold_setup.py` is separately plugin-path-only for its
own reason (#194).

WHAT THIS ANSWERS, and nothing else:

    Does every file Step 0a copied from the plugin install tree into this
    durable root still equal the plugin bytes it was copied from?

It is NOT a write audit and NOT a drift detector, and no caller may describe
it as one. There is no snapshot, no baseline, and no stored stamp: the
expected content is the plugin tree's own bytes, read fresh at check time.

THE GAP THIS CLOSES (#607, split out of #582). On a non-clean, non-final
review inside the fix-round budget, `mass-translate-wf.template.js`'s
`runRound()` calls `callFix()`, which dispatches a PLAIN Claude `agent()`
turn -- no `agentType`, so none of #409's `codex_job.py` sandboxing applies
to it -- and hands it a review whose `issue`/`suggest` text codex authored
and which `REVIEW_SCHEMA` constrains only in SHAPE. Since #532 that turn
adjudicates the free text rather than executing it, but it is still a
write-capable agent, it was never told it has exactly one write target, and
nothing afterwards looked at what it changed. The prompt half is fixed in
`fixPrompt` itself; this script is the half that can see a violation.

SKILL.md's #582 paragraph states the specific hole this closes in its own
words: "`plugin_bundle_hash` does not detect one either (`cache_key.py`
reads the Step-0a marker; it never re-hashes the copies)." It does now.

WHY A COPY-FIDELITY CHECK RATHER THAN A BEFORE/AFTER SNAPSHOT. Three
successive snapshot designs were reviewed and each was unsound in a
different direction, which is the finding, not an accident:

  * an enumerated ALLOWLIST of frozen paths kept omitting members
    (`translate_TASK.md`/`review_TASK.md`, then `schemas/`, the selected
    `languages/` preset, `extract.py`, the manifest-resolved source inputs,
    the two bundle markers, a prior run's `input.digest`,
    `.resume_gate_ack`). Its failure mode is SILENT under-coverage -- the
    exact class the gate exists to catch.
  * a whole-root sweep minus an EXCLUSION list produced guaranteed false
    REDs on every ordinary batch (`segments/.codex_job.<seg>.json`, the
    terminal hygiene joblog; and `scripts/__pycache__/*.pyc`, because
    `codex_job.py` imports the durable sibling `claim_record.py` and runs on
    every translate and every review dispatch) -- and ALSO failed silently,
    because excluding `runs/**` wholesale re-hid `input.digest` and
    `.resume_gate_ack`. Both polarities fail silently; the direction of the
    list was never the problem.

The set of "files that must not change" is not knowable by enumeration from
outside the pipeline. So this script does not enumerate it. It takes the set
from the Step 0a COPY PASS -- a shipped, tested contract -- and the expected
content of every member from the authority those members were copied from.
Nothing the pipeline writes at runtime has a plugin-tree twin, so nothing
the pipeline writes at runtime is compared, and no exclusion list is needed.

WHAT IS COMPARED (SKILL.md Step 0a, the "Copies (unconditional overwrite)"
paragraph and its `assets/templates/` exception, are the authority):

  * `assets/scripts/<name>.py` -> `${durable_root}/scripts/<name>.py`, for
    every shipped script EXCEPT the never-copied ones in `NEVER_COPIED`.
  * `assets/templates/<name>-wf.template.js` ->
    `${durable_root}/scripts/<name>-wf.template.js`, for the three workflow
    templates, which get "`scripts/`-style repeatable-overwrite treatment".
    They are copied VERBATIM (tokens unsubstituted); the instantiated
    workflow is written elsewhere, per run.
  * `assets/schemas/*.json` -> `${durable_root}/schemas/`. FLAT only:
    `assets/schemas/registry/` is deliberately plugin-only (SKILL.md:4176-4178)
    and is never copied, so it is never compared.
  * `assets/languages/*` -> `${durable_root}/languages/`, every shipped file
    including `README.md`.

WHAT IS NOT COMPARED, because it has no plugin twin: `canon.json`,
`canon_senses.json`, `manifest.json`, the segpacks, the ledger, and the
one-time template SEEDS the operator then hand-edits (`style_bible.md`,
`PLAN.md`, `consistency_issues.md`, `extract.py`, `translate_TASK.md`,
`review_TASK.md`, `glossary_TASK.md`). Those are copied once, guarded on
their destination's absence, and are MEANT to diverge -- comparing them
would be a guaranteed false RED.

THE TWO DERIVABLE MARKERS. Most no-twin files are translation CONTENT: a
tampered `canon.json` degrades a translation. Three are not -- they are
cache/resume IDENTITY, where tampering is gate integrity:
`runs/.plugin_bundle_hash`, `runs/.orchestration_bundle_hash` and
`runs/<id>/.resume_gate_ack`. The first two have no twin but DO have a
derivable expected value: they must equal the same sha1 scheme
`scaffold_setup.py` used, computed over the PLUGIN tree's own member bytes.
So they are verified here too, from the same authority and still with no
baseline. `.resume_gate_ack` is per-run authorization with no derivable
authority and remains a disclosed residual, named in SKILL.md.

VERDICTS, all of them RED (see `audit()`):

  differs    -- a compared file's bytes no longer match the plugin's.
  missing    -- a compared file is absent from the durable root.
  irregular  -- a compared path is not a regular file. An `lstat` check, so
                a symlink pointing at byte-identical content is still RED:
                a COPY is never a link, and a link's target can change
                afterwards outside anything this script looks at.
  extra      -- a `${durable_root}/scripts/*.py` with no plugin twin.
  orphaned   -- a `${durable_root}/schemas/*.json` with no plugin twin. The
                durable-side half of the check: `n_checked`/`n_expected` both
                come from `compared_pairs()`, so a plugin tree missing schema
                files shrinks both and still agrees.
  degenerate -- a plugin-side class (scripts/schemas/languages) that yields
                nothing while the durable root still holds files of it. Same
                blind spot, for the `languages/` class an orphan sweep cannot
                cover without false-REDing a documented `fr.local.json`.

  marker     -- a bundle marker whose stored value is not the one derivable
                from the plugin tree.

KNOWN, PINNED HOLE. `degenerate` fires only on WHOLESALE class loss, so
PARTIAL loss in `languages/` is not detected: a plugin tree missing exactly
`fr.json` while keeping the other presets drops the durable `fr.json` out of
`compared_pairs()`, shrinks n_checked and n_expected together, leaves
`degenerate` silent because plugin languages still exist, and lets a widened
edit to that durable file audit CLEAN. An incomplete refresh reaches it, not
only a party rewriting this script. `scripts/` and `schemas/` do not have the
hole -- their durable-side sweeps cover partial loss -- and closing it for
`languages/` needs either a stored baseline (rejected above, three times) or
a naming rule for legitimate overrides that SKILL.md does not fix. It ships
open and stated, and `tests/fix_scope_audit.test.py` pins the clean result so
that a later reader cannot mistake it for coverage.
`extra` is reported for `${durable_root}/scripts/*.py`, and `orphaned` for
`${durable_root}/schemas/*.json` -- never for `languages/`. That asymmetry is
not fastidiousness: SKILL.md's copy paragraph says the pass "never clobbers a
project-local override coexisting under a different filename (e.g.
`fr.local.json`)", and SKILL.md walks an operator through creating exactly
such a preset, so a sweep over `languages/` would fire on a legitimate,
documented file. `scripts/` and `schemas/` have no sanctioned addition, and
the schema case is not inert either: `canon_validate.py` loads every
`*.schema.json` under the durable `schemas/` into the validation registry and
`skeptic_setup.py` (mirroring `resume_setup.py`) hashes all of them into the
run identity, so a file dropped there changes what validates a canon and what
a resume compares against. An unexpected `.py` or `.json` in those two
directories is precisely what a widened fix turn would leave behind. `scripts/__pycache__/` is excluded from
`extra` because it belongs to the interpreter, not to any turn: reproduced
directly, running `codex_job.py` from a durable `scripts/` writes
`claim_record.cpython-<N>.pyc` there, and `codex_job.py` runs on every
translate and every review dispatch.

A `differs`/`missing` VERDICT DOES NOT ASSERT TAMPERING, and this script's
own refusal text must not imply that it does. A plugin upgraded mid-project
produces exactly the same signal, and nothing here can tell the two apart.
The one remedy serves both readings -- re-run Step 0a's copy pass and re-run
the batch -- and that overwrite also replaces a tampered copy with the
trusted one.

WHAT THIS CANNOT DO, stated here so no caller overstates it downstream:

  * It does not confine the fix turn. That turn is a plain Claude agent
    holding the operator's own permissions; the Workflow `agent()` API
    exposes no filesystem confinement.
  * A party able to alter the PLUGIN tree -- including this script -- can
    make the check report clean. There is no root of trust reachable from a
    Workflow script that the fix turn cannot also reach. What the check
    changes is that the tamper must now cover a second tree.
  * It sees nothing outside the compared set: another segment's draft,
    `runs/`, `$HOME`.
  * **It is a point-in-time comparison, so a write that lands AFTER it returns
    is not in it.** A fix turn that spawns a DETACHED child which writes once
    the turn has already returned defeats this round's audit specifically --
    reproduced. What it does not defeat is the NEXT round's audit, or the next
    batch's first audited round, since the check has no baseline and compares
    against the plugin bytes every time: the divergence is found whenever an
    audit next runs on that root, just not by the round that caused it. On the
    LAST fix round of a converging segment there is no next audit, and that is
    the uncovered case. This is the same family as "it cannot confine the
    turn", but it is stated separately because the confinement sentence does
    not by itself tell a reader that TIMING is the gap.
  * **No `.pyc` is ever compared**, and `${durable_root}/scripts/__pycache__/`
    is an EXECUTION surface: `codex_job.py` runs from the durable `scripts/`
    on every translate and every review dispatch and imports the durable
    `claim_record.py`, and CPython prefers a cached `.pyc` whose header
    matches the source's mtime and size -- both forgeable by anything that can
    write there. The exclusion is not optional (the interpreter writes that
    directory itself on every ordinary batch, so counting it would fail every
    batch), which is exactly why the gap is stated rather than closed here.
    Against this release's adversary of record -- a compliant model widening
    its edit -- forging a bytecode header is not the likely path; a
    non-compliant writer is not covered.
  * A driver-mediated fix turn (`segment_dispatch_driver.py` returns the
    rendered fix prompt as `needs_fix` for an external Claude turn) is not
    audited at all -- that route truncates the Workflow template before
    every top-level preflight, so no call site here ever fires.

CLI:

    python3 <plugin_root>/assets/scripts/fix_scope_audit.py \\
        --verify-copies --durable-root PATH

Prints exactly one JSON line and exits 0 when `ok` is true, 1 otherwise. The
calling agent relays that line verbatim as its schema-validated return; it
never performs or re-judges the comparison itself, which is the same
division of labour `review_artifact_check.py` uses.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

# This script's own directory is `assets/scripts/`; its parent is the assets
# root. Resolved from __file__ rather than from a --plugin-root flag on
# purpose: the flag would let a caller point the AUTHORITY at a tree the
# checked party controls, which is the one input this script must not accept
# from outside.
ASSETS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_SCRIPTS_DIR = ASSETS_DIR / "scripts"
PLUGIN_TEMPLATES_DIR = ASSETS_DIR / "templates"
PLUGIN_SCHEMAS_DIR = ASSETS_DIR / "schemas"
PLUGIN_LANGUAGES_DIR = ASSETS_DIR / "languages"

# The shipped scripts Step 0a does NOT copy. SKILL.md's copy paragraph is the
# authority on the first three and on `scaffold_setup.py`; this file adds
# itself for the reason in the module docstring. A name here is never
# reported `missing`.
NEVER_COPIED = frozenset(
    {
        "profile_validate.py",
        "validate_extraction.py",
        "glossary_preflight.py",
        "scaffold_setup.py",
        "fix_scope_audit.py",
    }
)

# The three workflow templates Step 0a copies into scripts/ with
# repeatable-overwrite treatment, keeping their basenames. Two of them are
# also PLUGIN_BUNDLE_MEMBERS entries, which is why the marker derivation
# below has to reach into assets/templates/ and not only assets/scripts/.
WORKFLOW_TEMPLATES = (
    "mass-translate-wf.template.js",
    "glossary-pass-wf.template.js",
    "skeptic-pass-wf.template.js",
)

MARKER_SPECS = (
    ("plugin_bundle_hash", ("runs", ".plugin_bundle_hash"), "PLUGIN_BUNDLE_MEMBERS"),
    (
        "orchestration_bundle_hash",
        ("runs", ".orchestration_bundle_hash"),
        "ORCHESTRATION_BUNDLE_MEMBERS",
    ),
)


def fail(message: str) -> NoReturn:
    """Emit the single failure line every caller parses, and exit 1. Never
    raises past main(): a relay agent that gets a traceback instead of a
    JSON line cannot report anything useful, and the workflow would read the
    call as an infrastructure failure rather than as a verdict."""
    # n_checked/n_expected are 0 here and are emitted anyway: FIX_SCOPE_SCHEMA
    # in mass-translate-wf.template.js REQUIRES both on every result, and the
    # relay agent is told to repeat this line verbatim. Omitting them would
    # make an exact relay of a real diagnostic ("durable root not found")
    # schema-invalid -- so the workflow would spend a retry and lose the
    # message, or the relay would have to invent fields. `ok: false` is what
    # blocks; the zeros only keep the shape reportable.
    print(json.dumps(
        {"ok": False, "verdict": "error", "error": message, "n_checked": 0, "n_expected": 0},
        sort_keys=True,
    ))
    raise SystemExit(1)


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def plugin_member_paths(members) -> list:
    """Plugin-side path for each bundle member name. A member is either a
    shipped script (assets/scripts/) or one of the workflow templates
    (assets/templates/) -- in the DURABLE root both live side by side under
    scripts/, which is what `scaffold_setup.compute_bundle_hash` hashes, so
    the plugin-side derivation has to re-split them by origin."""
    paths = []
    for name in members:
        if name in WORKFLOW_TEMPLATES:
            paths.append(PLUGIN_TEMPLATES_DIR / name)
        else:
            paths.append(PLUGIN_SCRIPTS_DIR / name)
    return paths


def expected_bundle_hash(members) -> str:
    """`scaffold_setup.compute_bundle_hash`'s scheme -- sorted by FILENAME,
    raw bytes concatenated, sha1 -- computed over the PLUGIN tree instead of
    over ${durable_root}/scripts/. Sorting by `.name` matches
    `cache_key.concat_sorted_bytes` exactly, which matters because the two
    trees order differently by full path."""
    ordered = sorted(plugin_member_paths(members), key=lambda p: p.name)
    blob = b"".join(p.read_bytes() for p in ordered)
    return sha1_hex(blob)


def load_member_tuples():
    """PLUGIN_BUNDLE_MEMBERS / ORCHESTRATION_BUNDLE_MEMBERS, read from the
    PLUGIN copies of the modules that declare them. `cache_key.py` owns the
    first and `scaffold_setup.py` the second; neither tuple is restated
    here, because a restated membership list is a second authority that
    silently rots (`cache_key.py`'s own comment makes the same point about
    counts)."""
    # This checker's authority is the plugin tree, and importing from it
    # would otherwise write assets/scripts/__pycache__/*.pyc INTO that tree.
    # Nothing hashes those, but a read-only check should not mutate the thing
    # it is reading.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PLUGIN_SCRIPTS_DIR))
    try:
        import cache_key  # noqa: E402
        import scaffold_setup  # noqa: E402
    except ImportError as exc:  # pragma: no cover - a broken install tree
        fail(f"cannot import the plugin's own bundle-member declarations: {exc}")
    return {
        "PLUGIN_BUNDLE_MEMBERS": cache_key.PLUGIN_BUNDLE_MEMBERS,
        "ORCHESTRATION_BUNDLE_MEMBERS": scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS,
    }


def listdir_or_empty(directory: Path) -> list:
    """`sorted(directory.iterdir())`, or [] when the directory is absent or
    unreadable.

    EVERY directory walk in this file goes through here, and that is the point:
    `glob()` swallows both errors and returns empty while `iterdir()` RAISES,
    so a mixed file would fail LOUDLY on one class of authority and silently on
    another -- and the loud failure is a traceback with no JSON line, which
    breaks fail()'s own invariant and reaches the workflow as "the relay is
    flaky" rather than "the install is broken". An empty class is not a pass:
    sweep_degenerate() turns an empty PLUGIN class over a populated durable one
    into a RED, and an unreadable DURABLE directory can only hide files that
    would have been reported, never invent a clean verdict for a compared pair
    (those are read individually, and a failed read is `unreadable`).
    """
    if not directory.is_dir():
        return []
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def compared_pairs() -> list:
    """Every (plugin_path, durable_relative_path) pair the Step 0a copy pass
    creates, derived from the plugin tree's own contents rather than from a
    hand list -- so a script or schema added to the plugin is compared from
    the release that adds it, with nothing here to update."""
    pairs = []
    for path in sorted(PLUGIN_SCRIPTS_DIR.glob("*.py")):
        if path.name in NEVER_COPIED:
            continue
        pairs.append((path, Path("scripts") / path.name))
    for name in WORKFLOW_TEMPLATES:
        pairs.append((PLUGIN_TEMPLATES_DIR / name, Path("scripts") / name))
    # Flat only -- assets/schemas/registry/ is plugin-only and uncopied.
    for path in sorted(PLUGIN_SCHEMAS_DIR.glob("*.json")):
        pairs.append((path, Path("schemas") / path.name))
    # listdir_or_empty, never a bare iterdir(): see its docstring -- an absent
    # OR unreadable authority directory must be a VERDICT, never a traceback.
    for path in listdir_or_empty(PLUGIN_LANGUAGES_DIR):
        if path.is_file():
            pairs.append((path, Path("languages") / path.name))
    return pairs


def sweep_extra(durable_root: Path, expected_script_names) -> list:
    """`${durable_root}/scripts/*.py` with no plugin twin. Scoped to
    scripts/ and to `.py` on purpose -- see the module docstring on
    `fr.local.json`."""
    extra = []
    for entry in listdir_or_empty(durable_root / "scripts"):
        # The `.py` filter is what keeps `__pycache__/` and its `*.pyc` out of
        # this sweep, and that exclusion is deliberate rather than incidental:
        # the interpreter writes there whenever a durable script imports a
        # durable sibling (reproduced -- `codex_job.py` imports the durable
        # `claim_record.py` and runs on every translate and every review
        # dispatch), so counting it would fail every batch. A `.pyc` is
        # therefore never compared either -- stated in WHAT THIS CANNOT DO.
        if not entry.name.endswith(".py"):
            continue
        if entry.name in expected_script_names:
            continue
        extra.append(entry.name)
    return extra


def sweep_orphaned_schemas(durable_root: Path, expected_schema_names) -> list:
    """`${durable_root}/schemas/*.json` with no plugin twin.

    This is the DURABLE-side half of the comparison, and it exists because
    `n_checked` and `n_expected` are BOTH derived from `compared_pairs()`:
    a plugin tree that has lost schema files makes the two shrink together
    and still agree, so the count binding alone cannot see it. The durable
    root is an independent population -- it holds what Step 0a actually
    copied -- so an orphan here is exactly the signal the counts cannot
    carry.

    Schemas ONLY. `languages/` is excluded for the reason the module
    docstring gives (`fr.local.json`: a documented, operator-created
    project-local override would read as an orphan); `${durable_root}/
    schemas/` has no such sanctioned addition -- every durable consumer
    (`canon_validate.py`, `ledger_update.py`, `glossary_preflight.py`,
    `canon_senses.py`, `canon_link_groups.py`) only ever READS from it.
    """
    orphaned = []
    for entry in listdir_or_empty(durable_root / "schemas"):
        if not entry.is_file():
            continue
        if not entry.name.endswith(".json"):
            continue
        if entry.name in expected_schema_names:
            continue
        orphaned.append(entry.name)
    return orphaned


def sweep_degenerate(durable_root: Path) -> list:
    """A plugin-side class that yields NOTHING while the durable root still
    holds files of that class.

    The other half of the same blind spot, for the class an orphan sweep
    cannot cover. Wholesale loss of `assets/languages/` (or of the schemas,
    or of the copied scripts) shrinks `compared_pairs()` to a set that
    excludes it, and `n_checked == n_expected` still holds -- with the two
    markers alone, both would read 2. Reporting the empty AUTHORITY needs no
    guess about which durable filenames are legitimate, so it costs no false
    RED on a documented override.
    """
    degenerate = []
    classes = (
        ("scripts", PLUGIN_SCRIPTS_DIR.glob("*.py"), durable_root / "scripts", ".py"),
        ("schemas", PLUGIN_SCHEMAS_DIR.glob("*.json"), durable_root / "schemas", ".json"),
        ("languages", PLUGIN_LANGUAGES_DIR.glob("*"), durable_root / "languages", ""),
    )
    for label, plugin_entries, durable_dir, suffix in classes:
        plugin_names = {
            path.name for path in plugin_entries
            if path.is_file() and path.name not in NEVER_COPIED
        }
        if plugin_names:
            continue
        durable_names = [
            entry.name for entry in listdir_or_empty(durable_dir)
            if entry.is_file() and entry.name.endswith(suffix)
        ]
        if durable_names:
            degenerate.append(label)
    return degenerate


def audit(durable_root: Path) -> dict:
    """The whole check. Returns the result object main() prints; raises
    nothing the caller has to catch."""
    differing = []
    missing = []
    irregular = []
    unreadable = []
    n_checked = 0

    # ONE call, reused everywhere below. Four separate calls would re-glob the
    # plugin tree four times and -- worse -- would be the only way n_checked
    # and n_expected could ever be computed over different populations.
    pairs = compared_pairs()

    for plugin_path, rel in pairs:
        durable_path = durable_root / rel
        rel_str = rel.as_posix()
        if not os.path.lexists(durable_path):
            missing.append(rel_str)
            continue
        # lstat, never is_file(): is_file() follows a symlink, so a link to
        # byte-identical content would read as a clean regular file.
        if not os.path.isfile(durable_path) or os.path.islink(durable_path):
            irregular.append(rel_str)
            continue
        try:
            # Size first. Different sizes ARE different bytes, so this is the
            # same verdict without reading either file -- and it bounds what a
            # planted multi-gigabyte scripts/*.py can make this process
            # allocate. A size match still reads both and compares.
            if durable_path.stat().st_size != plugin_path.stat().st_size:
                n_checked += 1
                differing.append(rel_str)
                continue
            durable_bytes = durable_path.read_bytes()
            plugin_bytes = plugin_path.read_bytes()
        except OSError as exc:
            unreadable.append(f"{rel_str}: {exc}")
            continue
        n_checked += 1
        if durable_bytes != plugin_bytes:
            differing.append(rel_str)

    expected_script_names = {
        rel.name for _, rel in pairs if rel.parent.as_posix() == "scripts"
    }
    extra = sweep_extra(durable_root, expected_script_names)
    expected_schema_names = {
        rel.name for _, rel in pairs if rel.parent.as_posix() == "schemas"
    }
    orphaned = sweep_orphaned_schemas(durable_root, expected_schema_names)
    degenerate = sweep_degenerate(durable_root)

    marker_mismatches = []
    member_tuples = load_member_tuples()
    for label, rel_parts, tuple_name in MARKER_SPECS:
        marker_path = durable_root.joinpath(*rel_parts)
        rel_str = "/".join(rel_parts)
        if not os.path.lexists(marker_path):
            missing.append(rel_str)
            continue
        # lstat, never is_file(): is_file() FOLLOWS a symlink, so a marker
        # replaced by a link to a file holding the expected hash read as a
        # clean regular file. The copied files above already reject that shape
        # for the same reason -- a link's target changes outside anything this
        # script looks at -- and the markers are gate identity, not content.
        if not os.path.isfile(marker_path) or os.path.islink(marker_path):
            irregular.append(rel_str)
            continue
        try:
            stored = marker_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            unreadable.append(f"{rel_str}: {exc}")
            continue
        try:
            expected = expected_bundle_hash(member_tuples[tuple_name])
        except OSError as exc:
            unreadable.append(f"{tuple_name}: {exc}")
            continue
        n_checked += 1
        if stored != expected:
            marker_mismatches.append(label)

    ok = not (
        differing or missing or irregular or extra or orphaned or degenerate
        or marker_mismatches or unreadable
    )
    # n_expected is the size of the set this run was SUPPOSED to compare --
    # len(compared_pairs()) plus the two markers. The caller binds
    # n_checked == n_expected and n_expected > 0, which catches a walk that
    # ABORTED part-way (the classic false GREEN: a loop that runs zero times
    # prints exactly like one that covered everything).
    #
    # What the pair does NOT prove is COVERAGE, and the reason is worth
    # stating where the number is computed: both sides are read off the single
    # `pairs` list above, so a plugin tree that lost members makes them shrink
    # TOGETHER and still agree. That is why the two durable-side
    # cross-checks above exist -- sweep_orphaned_schemas() and
    # sweep_degenerate() read the population from the durable root instead,
    # which the plugin tree cannot shrink. Neither defends against a RELAY
    # that fabricates the whole line; that residual is disclosed in SKILL.md
    # rather than papered over here.
    result = {
        "ok": ok,
        "n_checked": n_checked,
        "n_expected": len(pairs) + len(MARKER_SPECS),
    }
    if not ok:
        result["verdict"] = "mismatch"
        result["differing"] = differing
        result["missing"] = missing
        result["irregular"] = irregular
        result["extra"] = extra
        result["orphaned"] = orphaned
        result["degenerate"] = degenerate
        result["marker_mismatch"] = marker_mismatches
        result["unreadable"] = unreadable
        result["remedy"] = (
            "A durable copy no longer matches the plugin it was installed from. "
            "This is NOT by itself proof of tampering -- a plugin upgraded "
            "mid-project produces the same signal, and this check cannot tell "
            "the two apart. Either way the remedy is the same: re-run Step 0a's "
            "copy pass from the plugin path, then re-run this batch."
        )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy-fidelity check over a durable root's plugin-installed "
            "files. Runs ONLY from the plugin path; never from a durable copy."
        )
    )
    parser.add_argument(
        "--verify-copies",
        action="store_true",
        required=True,
        help="The only mode. Named rather than implicit so a future second "
        "mode cannot silently change what a bare invocation does.",
    )
    parser.add_argument(
        "--durable-root",
        required=True,
        metavar="PATH",
        help="The project's durable_root.",
    )
    args = parser.parse_args(argv)

    durable_root = Path(args.durable_root).expanduser()
    if not durable_root.is_dir():
        fail(f"durable root {durable_root} does not exist or is not a directory")
    durable_root = durable_root.resolve()

    # Refuse to run as a durable copy. This script is never copied, so this
    # can only fire if someone placed it inside the tree it audits -- where
    # it would be comparing that tree against itself and reporting clean.
    try:
        ASSETS_DIR.relative_to(durable_root)
    except ValueError:
        pass
    else:
        fail(
            f"refusing to run: this script's own assets root {ASSETS_DIR} is "
            f"inside the durable root {durable_root} it was asked to audit, so "
            "it would be checking that tree against itself. Run the plugin "
            "install tree's copy."
        )

    result = audit(durable_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
