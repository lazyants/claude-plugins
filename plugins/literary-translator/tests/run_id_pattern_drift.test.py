#!/usr/bin/env python3
"""The RUN_ID pattern lives in FIVE hand-maintained copies. This pins every
one of them against the copy that owns the contract, TOTALLY rather than
pairwise.

## Why total, and why that word is load-bearing

`resume_setup.py` owns the RUN_ID contract: it is the script that mints run
ids (`fresh_run_id()`), validates a caller-supplied `resume_from_run_id`,
and builds `runs/<RUN_ID>/` paths from the result. Four other scripts carry
their own copy of its pattern, per this project's "no shared lib between
self-contained scripts" convention -- `skeptic_setup.py`,
`segment_dispatch_driver.py`, `select_segments.py`,
`backfill_resume_gate_ack.py`.

A PAIRWISE pin -- each new copy checked against the owner as it appears --
reproduces the defect it is meant to fix: the next copy arrives unpinned and
nothing notices, because nothing is watching for copies that no test names.
So this test does not check a list of known copies. It ENUMERATES every
module-level `re.compile(<literal>)` in every shipped script, keeps the ones
whose name mentions RUN_ID, and asserts three separate things:

  1. every pattern found equals the owner's, byte for byte;
  2. the exact SET of (script, variable) pairs equals a frozen roster, so a
     NEW copy fails this test until someone adds it deliberately;
  3. the enumeration found at least as many as we know exist.

(3) exists because of a real incident: a sweep in this same work ran
`pytest $FILES` under zsh, which does not word-split, so pytest received one
nonexistent path, matched nothing, and printed "no tests ran" with no FAILED
line -- indistinguishable at a glance from a clean run. The same shape
threatens this file: an enumeration that silently matches nothing has
nothing to compare, and "no drift found" is what both a clean repo and a
broken scan report.

MEASURED, because the first version of this paragraph asserted it and was
wrong: mutating the name filter so it can never match leaves THREE of the
tests below red, not one. Assertion (1) fails on its own owner-presence
guard (`the OWNER's own pattern was not found`), and (2) fails on the
`missing` half of the roster comparison, because EXPECTED_COPIES is a frozen
literal rather than something derived from the same scan. So the vacuous
case is caught three independent ways, and the minimum count is the
belt-and-braces third -- the one that survives someone later relaxing either
guard, and the only one that states the expectation as a number. Same
principle as the `drafts_scanned` field in select_segments.py's own gate,
and the reason to keep it is not that the others are missing but that a
count is the assertion hardest to weaken by accident.

## Why ast and not a regex over the source

A regex that hunts for `RUN_ID_RE = re.compile(...)` only matches the
spelling whoever wrote it thought of. The five copies are already spelled
three different ways (`RUN_ID_RE`, `_RUN_ID_RE`, `_RUN_ID_DIR_RE`), which is
the same blind spot one level up from the drift being checked. `ast` sees
the assignment regardless of spelling, whitespace, or comment placement.

## What this test deliberately does NOT assert

That the GENERATORS agree. They do not, and that is intentional:
`skeptic_setup.fresh_run_id()` emits a microsecond-resolution timestamp plus
a random hex suffix, while `resume_setup.fresh_run_id()` emits the bare
`%Y%m%dT%H%M%SZ` form. `skeptic_setup`'s own docstring gives the reason (its
run ids live in a wholly separate namespace and back-to-back runs collide at
1-second resolution). What IS asserted below is the invariant that survives
that divergence: whatever any generator emits must satisfy the OWNER's
validator, so a future generator change cannot start producing ids the
shared shape rejects.
"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

assert SCRIPTS_DIR.is_dir(), f"scripts dir not found at {SCRIPTS_DIR}"

# The script that OWNS the contract -- every other copy is measured against
# this one, never against each other.
OWNER_SCRIPT = "resume_setup.py"
OWNER_VARNAME = "RUN_ID_RE"

# The frozen roster. A new copy must be added here deliberately, which is the
# whole point: an unpinned copy appearing silently is the defect.
EXPECTED_COPIES = frozenset(
    {
        ("resume_setup.py", "RUN_ID_RE"),
        ("skeptic_setup.py", "RUN_ID_RE"),
        ("segment_dispatch_driver.py", "_RUN_ID_DIR_RE"),
        ("select_segments.py", "_RUN_ID_DIR_RE"),
        ("backfill_resume_gate_ack.py", "_RUN_ID_RE"),
        # #438: claim_record.py builds runs/<RUN_ID>/.claimed.<seg>, so it owns
        # the same rejection its writers do -- an unsafe run id must not be able
        # to relocate a claim path out of the durable root. Registered here
        # deliberately, and byte-identical to the owner's literal.
        ("claim_record.py", "_RUN_ID_DIR_RE"),
    }
)


def _compiled_pattern_assignments(path: Path) -> dict:
    """{varname: pattern_string} for every module-level assignment of the
    form `NAME = re.compile("<literal>")` in `path`. Uses ast so the match
    does not depend on how the assignment happens to be spelled or
    formatted."""
    found = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "compile"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.args[0].value
    return found


def _all_run_id_patterns() -> dict:
    """{(script_name, varname): pattern_string} across every shipped
    script."""
    scripts = sorted(SCRIPTS_DIR.glob("*.py"))
    assert len(scripts) >= 30, (
        f"only {len(scripts)} script(s) found under {SCRIPTS_DIR} -- the glob "
        f"matched implausibly few files, so any 'no drift' verdict below "
        f"would be about almost nothing"
    )
    out = {}
    for path in scripts:
        for varname, pattern in _compiled_pattern_assignments(path).items():
            if "RUN_ID" in varname.upper():
                out[(path.name, varname)] = pattern
    return out


def test_every_run_id_pattern_equals_the_owners():
    """(1) No copy has drifted from resume_setup.py's."""
    patterns = _all_run_id_patterns()
    owner_key = (OWNER_SCRIPT, OWNER_VARNAME)
    assert owner_key in patterns, (
        f"the OWNER's own pattern was not found ({owner_key}) -- without it "
        f"every comparison below is vacuous"
    )
    owner_pattern = patterns[owner_key]

    drifted = {k: v for k, v in patterns.items() if v != owner_pattern}
    assert not drifted, (
        f"{len(drifted)} RUN_ID pattern copy/copies have drifted from "
        f"{OWNER_SCRIPT}'s {owner_pattern!r}:\n"
        + "\n".join(f"  {s}::{n} = {p!r}" for (s, n), p in sorted(drifted.items()))
        + f"\n\n{OWNER_SCRIPT} owns this contract -- it mints run ids, validates "
        f"caller-supplied ones, and builds runs/<RUN_ID>/ paths. A copy that "
        f"accepts ids the owner rejects (or vice versa) means one script "
        f"writes a run directory another cannot find."
    )


def test_the_roster_of_copies_is_exactly_what_we_think_it_is():
    """(2) TOTALITY. A new copy appearing anywhere fails here until it is
    added to EXPECTED_COPIES on purpose.

    This is the assertion that makes the pin total rather than pairwise. It
    is expected to fail when someone legitimately adds a sixth copy -- that
    failure IS the mechanism, not a false alarm. Add the copy to the roster
    in the same commit that introduces it."""
    found = frozenset(_all_run_id_patterns())
    missing = EXPECTED_COPIES - found
    unexpected = found - EXPECTED_COPIES
    assert not missing, (
        f"expected RUN_ID pattern copies that no longer exist: {sorted(missing)}. "
        f"If a copy was legitimately removed (e.g. the script now imports the "
        f"owner's), drop it from EXPECTED_COPIES in the same commit."
    )
    assert not unexpected, (
        f"NEW, unpinned RUN_ID pattern copy/copies: {sorted(unexpected)}. "
        f"Every copy of this pattern must be measured against {OWNER_SCRIPT}'s. "
        f"Add them to EXPECTED_COPIES deliberately -- this failure exists so a "
        f"copy cannot appear unnoticed, which is exactly how the five current "
        f"ones accumulated."
    )


def test_the_enumeration_found_a_plausible_number_of_copies():
    """(3) ANTI-VACUITY. An enumeration that silently matched nothing would
    satisfy both assertions above -- zero patterns are trivially all equal,
    and the roster check would compare two empty sets if the roster were
    derived the same way. Only a minimum count catches that."""
    patterns = _all_run_id_patterns()
    assert len(patterns) >= 5, (
        f"the ast enumeration found only {len(patterns)} RUN_ID pattern(s) "
        f"({sorted(patterns)}). At least 5 are known to exist, so this is an "
        f"enumeration failure -- a wrong scripts directory, a parse that "
        f"silently returned nothing -- not a clean result."
    )


def _load(name: str):
    """Import a shipped script by path.

    SCRIPTS_DIR goes on sys.path for the duration: `skeptic_setup.py` does a
    plain `import skeptic_constants` and SystemExits with an install-corrupted
    message if that sibling is not importable -- it expects to be run from
    ${durable_root}/scripts/, where it is. Added and removed around the load
    so the path change cannot leak into any other test in the session."""
    path = SCRIPTS_DIR / name
    inserted = False
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
        inserted = True
    try:
        spec = importlib.util.spec_from_file_location(f"{name}_uut", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(str(SCRIPTS_DIR))


# Probes chosen so that agreeing on the PATTERN is not enough to agree on the
# DECISION. Everything from "z..poison" down passes the shared regex and is
# still refused by the owner, so a copy carrying only the regex answers
# differently on exactly these.
_DECISION_PROBES = (
    "20260710T143022Z",              # the owner's own generated shape
    "20260710T143022123456-a1b2c3",  # skeptic's shape, deliberately different
    "w5-batch1-20260801T0500",       # a real hand-labelled id from tome1
    "a", "A0", "x.y_z-1",
    "z..poison",                     # regex-legal, owner-refused
    "a..b", "..lead", "trail..",
    "..", ".",                       # regex refuses these two; kept as controls
    "", "-leading", ".leading",
    "has:colon", "has/slash", "/absolute", "../traversal",
    "trailing\n", "\nleading", "sp ace", "tab\there",
    123, None, True, ["20260710T143022Z"],   # non-strings
)


def _decision(module, varname: str, run_id) -> bool:
    """Whether `module` ACCEPTS `run_id`, using whatever it actually decides
    with -- its own `validate_run_id()` when it has one, otherwise the named
    regex, which IS its whole decision procedure.

    Deliberately not "does it have a validator": the question is what the
    copy's effective answer is, and a copy whose answer comes from a bare
    `fullmatch` is exactly the drift being measured."""
    validator = getattr(module, "validate_run_id", None)
    if validator is not None:
        return validator(run_id) is None
    pattern = getattr(module, varname)
    if not isinstance(run_id, str):
        return False   # a regex cannot be applied to a non-string at all
    return pattern.fullmatch(run_id) is not None


@pytest.mark.parametrize("script,varname", sorted(EXPECTED_COPIES - {(OWNER_SCRIPT, OWNER_VARNAME)}))
def test_every_copy_makes_THE_SAME_DECISION_as_the_owner_not_merely_the_same_pattern(script, varname):
    """(4) The pattern-equality tests above are necessary and NOT sufficient,
    and this test exists because that gap was real rather than theoretical.

    `resume_setup.py`'s `validate_run_id()` refuses three things the shared
    regex does not express: the literal `.`, the literal `..`, and `..`
    anywhere inside the value. A copy holding a byte-identical regex and no
    validator therefore ACCEPTS `z..poison` while the owner REFUSES it -- so
    every assertion above passes while the two disagree about the only
    question either is asked.

    The consequence is concrete, not stylistic: the driver offers candidate
    run ids scanned off disk, and `resume_setup.py` validates the WHOLE
    offered list before matching any of it. One regex-legal, owner-refused
    directory name in the candidate set aborts the call without ever reaching
    a perfectly good candidate behind it.

    So the invariant worth pinning is the DECISION, not the pattern. This
    compares each copy's effective answer -- its validator where it has one,
    its regex where that is all it has -- against the owner's, over a probe
    corpus built so the two families of answer come apart."""
    owner = _load(OWNER_SCRIPT)
    copy = _load(script)
    disagreements = [
        (probe, _decision(owner, OWNER_VARNAME, probe), _decision(copy, varname, probe))
        for probe in _DECISION_PROBES
        if _decision(owner, OWNER_VARNAME, probe) != _decision(copy, varname, probe)
    ]
    assert not disagreements, (
        f"{script}::{varname} disagrees with {OWNER_SCRIPT}'s own validate_run_id() "
        f"on {len(disagreements)} of {len(_DECISION_PROBES)} probes "
        f"(probe, owner_accepts, copy_accepts):\n"
        + "\n".join(f"  {p!r}: owner={o}, copy={c}" for p, o, c in disagreements)
        + f"\n\nA byte-identical regex is not agreement. {OWNER_SCRIPT} refuses "
        f"'.', '..' and any value CONTAINING '..' beyond what the regex says, "
        f"and it validates every offered candidate before matching any of them."
    )


@pytest.mark.parametrize("script", ["resume_setup.py", "skeptic_setup.py"])
def test_every_generator_emits_ids_the_owners_validator_accepts(script):
    """The generators deliberately DIFFER (see this module's docstring), so
    the invariant worth pinning is not that they agree but that neither can
    start emitting ids the shared validator rejects.

    Ten samples rather than one: skeptic's form carries a random suffix, so a
    single sample could pass by luck on a pattern that rejects some hex
    digits."""
    owner = _load(OWNER_SCRIPT)
    module = _load(script)
    for _ in range(10):
        run_id = module.fresh_run_id()
        assert owner.RUN_ID_RE.fullmatch(run_id), (
            f"{script}'s fresh_run_id() emitted {run_id!r}, which "
            f"{OWNER_SCRIPT}'s own RUN_ID_RE rejects"
        )


def test_the_owners_generator_emits_a_LEXICOGRAPHICALLY_SORTABLE_shape():
    """(5) The validator-acceptance test above is a WEAK oracle and this is
    the measurement that showed it, not a suspicion.

    A mutation battery reformatted `resume_setup.fresh_run_id()` from
    `%Y%m%dT%H%M%SZ` to `%d%m%YT%H%M%SZ` -- still a valid RUN_ID, still
    accepted by `RUN_ID_RE`, no longer sortable -- and NO test in the suite
    detected it, this file's own generator test included. `RUN_ID_RE` is
    `[A-Za-z0-9][A-Za-z0-9._-]*`, which accepts virtually anything, so
    "the owner's validator accepts what the generator emits" is satisfied
    by an id whose field order has been destroyed.

    Sortability is not cosmetic here. `segment_dispatch_driver.py`'s
    resumable-candidate scan takes `sorted(candidates, reverse=True)[:limit]`
    and its own docstring states the dependency outright -- lexicographic
    descending IS chronological descending, *because* the id is the
    colon-free `YYYYMMDDTHHMMSSZ` form. Reverse the day and year fields and
    that equivalence silently stops holding: the scan still returns five
    candidates, still in a stable order, and the order is now wrong. The
    resume it then picks is the wrong run, quietly.

    Two assertions, deliberately independent of how the generator is
    written. The first parses what it emitted against the pinned format and
    checks it against the wall clock -- an oracle the generator cannot
    satisfy by agreeing with itself. The second demonstrates the ordering
    property the driver actually depends on, rather than asserting it in
    prose."""
    from datetime import datetime, timedelta, timezone

    owner = _load(OWNER_SCRIPT)
    run_id = owner.fresh_run_id()

    try:
        parsed = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as err:
        raise AssertionError(
            f"{OWNER_SCRIPT}'s fresh_run_id() emitted {run_id!r}, which does not "
            f"parse as the colon-free '%Y%m%dT%H%M%SZ' form that "
            f"segment_dispatch_driver.py's candidate scan relies on for "
            f"lexicographic == chronological ordering: {err}"
        ) from None

    drift = abs(datetime.now(timezone.utc) - parsed)
    assert drift < timedelta(minutes=10), (
        f"{OWNER_SCRIPT}'s fresh_run_id() emitted {run_id!r}, which parses under "
        f"the pinned format but lands {drift} from now -- the field ORDER has "
        f"changed (a day/year swap parses for some dates and yields a wildly "
        f"wrong instant), so the id is no longer sortable even though it still "
        f"satisfies RUN_ID_RE."
    )

    # The ordering property itself, demonstrated rather than asserted: three
    # instants an hour apart, rendered through the SAME format this test just
    # pinned, must sort lexicographically in chronological order.
    moments = [parsed + timedelta(hours=h) for h in (0, 1, 2)]
    rendered = [m.strftime("%Y%m%dT%H%M%SZ") for m in moments]
    assert rendered == sorted(rendered), (
        f"the pinned format does not sort chronologically: {rendered}. "
        f"segment_dispatch_driver.py takes sorted(candidates, reverse=True) and "
        f"treats the result as newest-first."
    )


def test_the_two_run_id_namespaces_are_disjoint_trees():
    """The two generators produce shapes that do NOT sort together, so the
    fact that their run ids never share a directory is load-bearing rather
    than incidental.

    Concretely: `20260710T143022123456-a1b2c3` sorts BEFORE
    `20260710T143022Z` lexicographically ('1' < 'Z'), while being
    chronologically LATER -- the same second, .123456 into it. Any consumer
    that treats lexicographic order as chronological (the driver's own
    resumable-candidate scan does, taking `sorted(..., reverse=True)[:limit]`)
    would silently mis-order them if the two ever landed in one tree.

    They do not: skeptic writes `{durable_root}/skeptic/runs/`, resume_setup
    writes `{durable_root}/runs/`. This pins that separation, since it is the
    thing keeping the ordering assumption safe."""
    skeptic = _load("skeptic_setup.py")
    assert skeptic.SKEPTIC_RUNS_SUBDIR == "skeptic/runs", (
        f"skeptic run ids moved to {skeptic.SKEPTIC_RUNS_SUBDIR!r}. If that is "
        f"now 'runs', skeptic and mass run ids share a directory -- their "
        f"shapes do not sort together, and skeptic digests would also be "
        f"offered to resume_setup.resolve_run() as mass candidates."
    )
    # Demonstrate the mis-ordering this separation prevents, so the reason is
    # verified rather than asserted in prose.
    mass_id = "20260710T143022Z"
    skeptic_id = "20260710T143022123456-a1b2c3"
    assert skeptic_id < mass_id, (
        "the premise of this test has changed: the skeptic form no longer "
        "sorts before the mass form, so the ordering hazard described above "
        "needs re-deriving"
    )
