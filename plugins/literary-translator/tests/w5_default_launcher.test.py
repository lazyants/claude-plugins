"""tests/w5_default_launcher.test.py -- #516: W5's launcher designation.

## What this file pins, and why prose needs a pin at all

SKILL.md's W5 section is where a model or operator picks a launcher. Since
#516 the pick is `segment_dispatch_driver.py`, with the
`mass-translate-wf.template.js` + `pipeline()` path retained as the FALLBACK.
Nothing executes that designation -- it is one paragraph of prose against
which a whole book's cost is decided (the measured share of a real run's
tokens that went to the fallback's orchestration bookkeeping is stated in
that paragraph, and is deliberately NOT copied here: nothing can check that
two hand-copied sets of digits still agree), so a later edit can revert it
silently and no script, schema or gate will notice.

## Two directions, and why BOTH are needed here

The positive pins alone are not enough, and the reason is specific rather
than theoretical: a new "the driver is the default" sentence can coexist with
an old "`pipeline()` remains W5's DEFAULT dispatch mechanism" sentence three
paragraphs away, and every positive assertion stays green while the document
gives two opposite instructions. That is exactly the shape #516's own review
found in the reference docs. So the retired designators are asserted GONE as
well.

Each negative needle is real by construction rather than by hope: it is text
this change DELETED from the base tree (`0cad6fe`), where each occurred. An
absence assertion whose needle never matched anything is green forever and
indistinguishable, from inside a passing run, from one doing its job -- so a
needle here is only allowed if it names wording that demonstrably shipped.

## Scoping and whitespace

Pins run against the W5 section only (`**W5 Mass-translate**` up to the W6
heading), so a phrase elsewhere in this ~130 KB document cannot satisfy one,
and against whitespace-collapsed text, because this document hard-wraps at
~75 columns and a pin that breaks on a rewrap is a pin nobody keeps.
"""
import importlib.util
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"


def _load(stem: str):
    """Load a SHIPPED script by file identity, under a name that cannot collide
    with a real module. `scaffold_setup` imports its sibling `cache_key`, so the
    scripts directory has to be importable while it executes -- the scripts are
    self-anchored and do nothing else at import time."""
    path = SCRIPTS_DIR / f"{stem}.py"
    assert path.is_file(), f"shipped script not found at {path}"
    spec = importlib.util.spec_from_file_location(f"{stem}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    inserted = str(SCRIPTS_DIR)
    # `scaffold_setup` imports `cache_key` as a bare sibling, which lands in
    # sys.modules under that plain name and OUTLIVES this call. Left there, a
    # later test in the same worker that loads a fixture copy through its own
    # bare sibling import would bind the SHIPPED module instead of its fixture.
    # The whole sys.modules delta is undone, absence included.
    before = dict(sys.modules)
    sys.path.insert(0, inserted)
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == inserted:
            sys.path.pop(0)
        for name in set(sys.modules) - set(before):
            del sys.modules[name]
        for name, was in before.items():
            if sys.modules.get(name) is not was:
                sys.modules[name] = was
    return module


def _figure_missing(w5: str, before: str, number: int, after: str) -> bool:
    """True when W5 does not state exactly `number` between `before` and `after`.

    A bare substring needle is not enough and the gap is not theoretical: a
    review of this file simulated `52 scripts` becoming `152 scripts` and
    `87 artifacts` becoming `187 artifacts`, and a plain `f"{n} scripts"`
    needle stayed GREEN through both -- the wrong figure CONTAINS the right
    one. So the number is matched between digit boundaries."""
    pattern = (
        re.escape(before) + r"(?<!\d)" + str(number) + r"(?!\d)" + re.escape(after)
    )
    return re.search(pattern, w5) is None


def _copied_destinations(fix_scope_audit=None) -> set:
    """Every durable-relative path the Step 0a copy pass creates, taken from the
    AUTHORITATIVE implementation -- `fix_scope_audit.compared_pairs()` is the
    audit's own manifest, and re-deriving it here from a directory listing is
    exactly the mistake #834 was filed about: the prose is a claim about what is
    COPIED and COMPARED, which is not the question `ls` answers.

    A caller that also needs the module's own constants passes it in, so the
    script is executed once per test rather than once per quantity read off it."""
    if fix_scope_audit is None:
        fix_scope_audit = _load("fix_scope_audit")
    return {dest for _plugin_path, dest in fix_scope_audit.compared_pairs()}


def _verified_member_names() -> set:
    """The member NAMES `scaffold_setup.py --verify` byte-compares: the union of
    the two shipped bundle tuples, deduped by name because a member registered in
    both bundles is one file, not two. `run_verify()` builds the same union
    inline; the set union of two literal tuples is a fact about their contents
    rather than an algorithm, and the dedup itself already has its own negative
    control in tests/scaffold_setup.test.py."""
    scaffold = _load("scaffold_setup")
    return set(scaffold.cache_key.PLUGIN_BUNDLE_MEMBERS) | set(
        scaffold.ORCHESTRATION_BUNDLE_MEMBERS
    )


def _w5_section() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.find("**W5 Mass-translate**")
    assert start != -1, "the W5 Mass-translate heading is gone from SKILL.md"
    end = text.find("**W6 ", start)
    assert end != -1 and end > start, "could not delimit W5 (no W6 heading after it)"
    section = text[start:end]
    assert len(section) > 20000, (
        f"the extracted W5 section is implausibly short ({len(section)} chars) -- "
        "the delimiters moved and these pins would be checking almost nothing"
    )
    return re.sub(r"\s+", " ", section)


def test_w5_names_the_driver_as_its_default_launcher():
    w5 = _w5_section()
    assert "W5's DEFAULT launcher is `segment_dispatch_driver.py`" in w5, (
        "the W5 opener must name the driver as the default launcher"
    )
    # The documented launch recipe carries --plugin-root: an omitted flag
    # self-anchors into the codex-writable durable tree without refusing.
    assert "--plugin-root {plugin_root} \\" in w5
    assert "**Default dispatch path — `segment_dispatch_driver.py`" in w5, (
        "the designation paragraph must be titled as the DEFAULT dispatch path"
    )


def test_w5_keeps_the_pipeline_path_as_a_documented_fallback():
    # The flip is "flip the designation, keep the fallback shipped" -- a
    # reader who cannot use the driver must still find the template path
    # documented as supported, not as a deprecated remnant.
    w5 = _w5_section()
    assert "**The fallback is RETAINED, not retired.**" in w5
    assert "stays shipped and supported" in w5
    assert "Only then — on the FALLBACK path — is `mass-translate-wf.template.js`" in w5


def test_w5_states_why_the_fallback_is_not_removed_now():
    # Without the ordering rule the retention reads as inertia, and the next
    # editor deletes it. #432 is the live example of a driver-only path with
    # no escape hatch.
    w5 = _w5_section()
    assert "before the driver has carried a book end to end as the default" in w5
    assert "#432" in w5


def test_w5_discloses_what_the_default_path_does_not_carry():
    # Three residuals move onto the default path with the flip, and each is
    # disclosed where the launcher is chosen rather than sections away. The
    # pins are the substantive sentence of each, not its numbered label: a
    # label survives having the claim under it reversed or hollowed out.
    w5 = _w5_section()
    assert "What the default path does NOT carry" in w5
    # (1) is a COPY-FIDELITY delta, not "the fix turn is unaudited" -- the
    # understated version is what the security pass caught, and the count
    # of artifacts left uncompared is the load-bearing half.
    assert "does not fire here at all" in w5
    assert "COPY-FIDELITY comparison of every file Step 0a copied" in w5
    assert "have no byte comparison on this path" in w5
    assert "is itself one of the uncompared copies" in w5
    # (2) the fallback refuses an empty PLUGIN_ROOT; the driver's omitted
    # flag silently self-anchors into the codex-writable durable tree.
    assert "A missing `--plugin-root` is not refused" in w5
    assert "fails open rather than loud" in w5
    # (3) the batch-final merge, whose replacement is pinned separately.
    assert "this path has no per-batch equivalent, deliberately" in w5


def test_the_fix_turn_is_a_step_of_the_default_loop():
    w5 = _w5_section()
    assert "Step of this loop, not an exception to it: the fix turn" in w5
    assert "perform ONE Claude fix turn per `needs_fix` segment" in w5
    # The warning that made it a caveat in the first place must survive the
    # promotion: a driver launched unattended still stalls at needs_fix.
    assert "Do not launch this driver unattended" in w5


def test_what_replaces_the_fallback_batch_final_check_is_stated():
    # Three review rounds landed on this one paragraph, each finding another
    # way an operator-assembled roster can be wrong. The paragraph no longer
    # tells anyone to assemble one: it says why a driver run has no batch
    # roster to reconstruct, names the whole-book gate that does carry the
    # guarantee, and states the two non-refusing weak cases of the optional
    # merge. Each of those is pinned, because dropping any one of them turns
    # the paragraph back into a recipe that reads complete and is not.
    w5 = _w5_section()
    assert "it does NOT perform the batch-final" in w5
    assert "a repeated SUBSET invocation, not one batch" in w5
    # The gate that actually decides it, named by the script that runs it.
    assert "`final_audit.py` runs over EVERY currently-converged segment" in w5
    assert "recomputes each draft's content sha1" in w5
    # Both weak cases of the optional merge return success, so the output --
    # not the exit status -- is what an operator has to read.
    # W7 is broader on draft identity and NARROWER on the token binding;
    # an unqualified "stronger" invited the opposite reading.
    assert "What W7 does not carry is" in w5
    assert "read its OUTPUT rather than its exit status" in w5
    assert "listing that id in `stale_segments`" in w5
    assert "--expected-segs SEG1,SEG2,... --run-token RUN_ID" in w5


def test_the_retired_designations_are_gone():
    # Every needle below occurred exactly once in the W5 section at the base
    # commit 0cad6fe and was deleted by #516. They are asserted absent because
    # a surviving copy would contradict the pins above while all of them stay
    # green -- the failure mode that makes positive-only pinning insufficient
    # for a designation.
    w5 = _w5_section()
    for retired in (
        "Optional dispatch path — `segment_dispatch_driver.py`",
        "remains W5's DEFAULT dispatch mechanism",
        "is an ALTERNATIVE, not a replacement",
        "Switching W5 over to it by default is deferred to a later step",
        "use it only if you deliberately choose to, and never against the same",
        "The driver cannot perform the fix step, and nothing today automates",
    ):
        assert retired not in w5, (
            f"a retired launcher designation is back in W5: {retired!r} -- "
            "the section now says both that the driver is the default and "
            "that it is not"
        )


def test_the_copied_artifact_figures_are_re_derived_from_the_tree():
    """#834. The figures in item (1) are DERIVED here, never transcribed.

    This paragraph shipped "48 scripts ... 24 schemas ... 81 artifacts" and
    "58 copied artifacts have no byte comparison" -- four self-consistent
    numbers that had gone stale by up to seven scripts. Nothing reads them: the
    consumer is a model or an operator deciding, at the point the launcher is
    chosen, how much of the durable root the default path leaves unchecked. The
    comment in test_w5_discloses_what_the_default_path_does_not_carry above
    already calls that count "the load-bearing half", and until now this file
    pinned every SENTENCE of the paragraph and none of its numbers.

    Why a derivation and not a second copy of the digits: this file's own
    docstring refuses to hand-copy a measurement, because nothing can check that
    two hand-copied sets of digits still agree. A derivation is not a copy --
    each row below calls the shipped implementation that OWNS the quantity and
    compares its result against the prose.

    What this does NOT cover, stated so a later reader cannot mistake it for
    completeness: only the figures asserted below. An undeclared numeral
    elsewhere in SKILL.md is unchecked, and a derivation that hardcoded its own
    answer would pass every assertion here. Each one was watched RED by mutating
    the TREE (adding a schema file), never by mutating the assertion.
    """
    w5 = _w5_section()
    fix_scope_audit = _load("fix_scope_audit")
    copied = _copied_destinations(fix_scope_audit)

    # Bucketed the way the prose enumerates. The three workflow templates land
    # in scripts/ keeping their basenames, so they are separated by NAME rather
    # than by destination -- the same split compared_pairs() makes plugin-side.
    template_names = set(fix_scope_audit.WORKFLOW_TEMPLATES)
    scripts = {
        d for d in copied if d.parts[0] == "scripts" and d.name not in template_names
    }
    templates = {d for d in copied if d.name in template_names}
    schemas = {d for d in copied if d.parts[0] == "schemas"}
    languages = {d for d in copied if d.parts[0] == "languages"}

    # The classes must exhaust the total, or a class added to the copy pass
    # would move `artifacts` alone and leave the enumeration silently short.
    assert len(scripts) + len(templates) + len(schemas) + len(languages) == len(
        copied
    ), "the copy pass now writes a destination class W5 does not enumerate"

    for before, number, after in (
        ("bytes it came from — ", len(scripts), " scripts, the three"),
        ("workflow templates, ", len(schemas), " schemas"),
        ("schemas and the ", len(languages), " language files"),
        ("language files, ", len(copied), " artifacts"),
    ):
        assert not _figure_missing(w5, before, number, after), (
            "SKILL.md's copied-artifact figures no longer match the Step 0a "
            f"copy pass ({len(scripts)} scripts, {len(templates)} templates, "
            f"{len(schemas)} schemas, {len(languages)} language files, "
            f"{len(copied)} artifacts) -- W5 does not say "
            f"{before!r} {number} {after!r}"
        )

    # "the three workflow templates" is spelled as a WORD, so it carries no
    # numeral to compare against. The count is asserted directly rather than
    # rewording the sentence to suit this guard.
    assert len(templates) == 3, (
        f"Step 0a now copies {len(templates)} workflow templates, so W5's "
        "'the three workflow templates' is wrong"
    )
    assert "the three workflow templates" in w5


def test_the_uncompared_count_is_a_set_difference_not_a_subtraction():
    """#834. `--verify`'s bundle figures, and the count of copied artifacts it
    leaves without a byte comparison.

    Derived as a SET DIFFERENCE over destination paths, deliberately, never as
    `len(copied) - len(verified)`: a bundle member that is NOT a copied artifact
    would shrink that subtraction while comparing none of the copied set, and the
    arithmetic would bless a second wrong figure exactly the way #834's did. So
    the containment the prose implies is asserted here rather than assumed.
    """
    w5 = _w5_section()
    copied = _copied_destinations()
    verified_names = _verified_member_names()
    # Every bundle member is copied into the durable root's scripts/ -- the two
    # workflow templates among them keep their basenames there.
    verified = {Path("scripts") / name for name in verified_names}

    assert verified <= copied, (
        "a `scaffold_setup.py --verify` bundle member is not one of the files "
        "Step 0a copies, so W5's count of copied artifacts with no byte "
        "comparison can no longer be their difference: "
        f"{sorted(str(p) for p in verified - copied)}"
    )

    verified_scripts = {name for name in verified_names if name.endswith(".py")}
    uncompared = copied - verified

    for before, number, after in (
        ("the two BUNDLES — ", len(verified_scripts), " scripts plus"),
        ("`glossary-pass-wf.template.js`, ", len(verified_names), " members."),
        ("members. So ", len(uncompared), " copied artifacts have no byte comparison"),
    ):
        assert not _figure_missing(w5, before, number, after), (
            "W5's `--verify` figures no longer match the shipped bundle tuples "
            f"({len(verified_scripts)} scripts, {len(verified_names)} members, "
            f"{len(uncompared)} uncompared) -- W5 does not say "
            f"{before!r} {number} {after!r}"
        )

    # The sentence enumerates what that count includes. A count that stopped
    # covering them would leave the sentence refuting itself.
    for name in ("skeptic-pass-wf.template.js", "final_audit.py", "assemble.py"):
        assert Path("scripts") / name in uncompared, (
            f"W5 names {name} among the copied artifacts with no byte "
            "comparison, but it is now compared (or no longer copied)"
        )
    assert any(
        d.parts[0] == "schemas" for d in uncompared
    ), "W5 says every durable schema is uncompared on this path"
    assert any(
        d.parts[0] == "languages" for d in uncompared
    ), "W5 says every language preset is uncompared on this path"
