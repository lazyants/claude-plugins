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
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"


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
