"""tests/w5_default_launcher.test.py -- #516: W5's launcher designation.

## What this file pins, and why prose needs a pin at all

SKILL.md's W5 section is where a model or operator picks a launcher. Since
#516 the pick is `segment_dispatch_driver.py`, with the
`mass-translate-wf.template.js` + `pipeline()` path retained as the FALLBACK.
Nothing executes that designation -- it is one paragraph of prose against
which a whole book's cost is decided (#409 measured 83.4% of a 25.0M-token
run spent on the fallback's orchestration bookkeeping), so a later edit can
revert it silently and no script, schema or gate will notice.

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
    # Both gates live inside the template, so flipping the default moves two
    # residuals onto the default path. They are disclosed where the launcher
    # is chosen rather than three sections away.
    w5 = _w5_section()
    assert "What the default path does NOT carry" in w5
    assert "a driver-mediated fix turn is outside it entirely" in w5
    assert "(2) The batch-final completeness merge" in w5


def test_the_fix_turn_is_a_step_of_the_default_loop():
    w5 = _w5_section()
    assert "Step of this loop, not an exception to it: the fix turn" in w5
    assert "perform ONE Claude fix turn per `needs_fix` segment" in w5
    # The warning that made it a caveat in the first place must survive the
    # promotion: a driver launched unattended still stalls at needs_fix.
    assert "Do not launch this driver unattended" in w5


def test_the_batch_final_completeness_check_is_a_step_not_an_extra():
    # The driver does not perform ledger_merge.py --expected-segs --run-token;
    # the fallback's batchComplete step does. Flipping the default without
    # documenting this drops a mandatory gate from the documented path.
    w5 = _w5_section()
    assert "Last step of the batch, and it is the caller's" in w5
    assert "--expected-segs SEG1,SEG2,... --run-token RUN_ID" in w5
    assert "a driver run is not complete when the driver exits" in w5
    # Both misuse directions, each verified against ledger_merge.py's own
    # semantics: a non-converged id is SKIPPED by the token re-assertion
    # (which runs only for a `converged` entry), and an id stamped by an
    # earlier run fails the reconstructed `<run_token>:<seg>` comparison.
    assert "Name only ids you are claiming CONVERGED" in w5
    assert "Name only ids dispatched under THAT `run_id`" in w5


def test_the_retired_designations_are_gone():
    # Every needle below occurred exactly once in the W5 section at the base
    # commit 0cad6fe and was deleted by #516. They are asserted absent because
    # a surviving copy would contradict the pins above while all of them stay
    # green -- the failure mode that makes positive-only pinning insufficient
    # for a designation.
    w5 = _w5_section()
    for retired in (
        "Optional dispatch path",
        "remains W5's DEFAULT dispatch mechanism",
        "is an ALTERNATIVE, not a replacement",
        "Switching W5 over to it by default is deferred to a later step",
        "use it only if you deliberately choose to",
        "The driver cannot perform the fix step, and nothing today automates",
    ):
        assert retired not in w5, (
            f"a retired launcher designation is back in W5: {retired!r} -- "
            "the section now says both that the driver is the default and "
            "that it is not"
        )
