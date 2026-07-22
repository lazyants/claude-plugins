"""tests/skill_prose_present.test.py -- presence test for hand-authored
plugin prose that no schema or script enforces: an accidental deletion
during a later edit would otherwise go unnoticed until a human happened to
reread the file.

Split by file, and every needle below is a fragment verified (by line
number, see the inline comments) to sit fully within one physical source
line at authoring time -- this codebase's docs hard-wrap at a fixed column
width, so a needle spanning a wrap point would silently miss even though
the content is fully intact. Never extend a passing needle into a longer
phrase without re-checking it still lands on one line.

Covers:
1. SKILL.md's "Intake & proportionality" step existing at all.
2. SKILL.md's pipeline-role-assignment prompt within that same step
   (LESSONS item 18: who translates/reviews/fixes/orchestrates, reviewer
   independence, pointing at the constellation doc).
3. SKILL.md's F3 adjudication-fence sentence (gating the opt-in
   canon-adjudication-audit machinery to its justifying deliverable).
4. references/operating-constellation.md existing and actually carrying
   the Part-6 review-orchestration content (LESSONS items 20-24), not just
   an empty stub.
5. SKILL.md's intake step 4 states plainly that codex-translate/review is
   hard-locked (R1) and IS the v1 default, not one of several options.
6. SKILL.md's Step 0 defines `{{PLUGIN_ROOT}}` before its first use.
7. SKILL.md's W5 section names the detached `codex_job.py` driver and the
   `resolve_codex_companion.py` companion resolver (1.4.7 W5 launch change).
8. SKILL.md's W2 section states the new #210 hard fail-loud condition
   (manifest omits `heading_types` entirely) and both of its remedies.
9. SKILL.md's W7 output-coverage section names the new #202
   within-cohort ratio-outlier lane, its WARN-only contract, and its
   stated structural blind spot (cannot close #202).
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
OPERATING_CONSTELLATION = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "operating-constellation.md"
)

assert SKILL_MD.is_file(), f"expected {SKILL_MD} to exist"


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_intake_and_proportionality_step_present():
    # Heading fragment, fully on one line as of this writing:
    # "## Intake & proportionality (do this first)".
    assert "Intake & proportionality" in _skill_text()


def test_pipeline_role_assignment_prompt_present():
    # "Agree pipeline role assignment." sits fully on its own line as of
    # this writing. Deliberately NOT extended to "...independent of the
    # translator" -- that phrase itself wraps across two lines here, the
    # exact hard-wrap trap this file's docstring warns about.
    text = _skill_text()
    assert "Agree pipeline role assignment" in text
    # The prompt must actually point at the constellation doc, not merely
    # use the word "independent" in passing elsewhere in the file.
    assert "operating-constellation.md" in text


def test_codex_translate_review_hard_lock_is_default_present():
    # "hard-locked to codex" sits fully on one line as of this writing
    # ("   **hard-locked to codex** (R1, `references/engine-loop.md`) —").
    # 1.4.7 reframed the old "hard-locked to `codex:codex-rescue`": codex is
    # still the sole translate/review engine, now LAUNCHED by the detached
    # codex_job.py driver rather than the codex:codex-rescue forwarder.
    text = _skill_text()
    assert "hard-locked to codex" in text
    # "not a menu of interchangeable options" also sits fully on one line
    # as of this writing, confirming this IS the v1 default, not a choice.
    assert "not a menu of interchangeable options" in text


def test_w5_codex_job_driver_and_companion_resolution_present():
    # 1.4.7 W5 launch change: the detached `codex_job.py` driver launches
    # codex, and the orchestrator resolves the codex-companion path via
    # `resolve_codex_companion.py`. Both filenames sit fully on one line each
    # as of this writing (in the W5 section / R7 / the Step 0 exception list).
    text = _skill_text()
    assert "codex_job.py" in text
    assert "resolve_codex_companion.py" in text


def test_plugin_root_defined_at_step_0_present():
    # "denotes the plugin's install" sits fully on one line as of this
    # writing ("Throughout this skill, `{{PLUGIN_ROOT}}` denotes the
    # plugin's install" / "directory -- ..."), just before the token's
    # first use in the Step 0 command block.
    assert "denotes the plugin's install" in _skill_text()


def test_f3_adjudication_fence_sentence_present():
    # Fully on one line as of this writing: "identity itself. Enable ONLY
    # when a per-person index, per-person bios, or".
    assert "Enable ONLY when a per-person index" in _skill_text()


def test_w2_undeclared_heading_type_hard_check_present():
    # #210 D2. Each fragment below sits fully on one line each as of this
    # writing (verified with grep, not by eye -- a fragment spanning this
    # file's hard-wrap point would silently miss):
    #   "  manifest omits the `heading_types` key entirely -- a bare absence, never"
    #   "  offending type plus both remedies: declare it in `heading_types`, or set"
    #   "  `heading_types: []` to affirm this source has no heading blocks at all."
    text = _skill_text()
    assert "omits the `heading_types` key entirely" in text
    # Both remedies the gate's own error message names.
    assert "declare it in `heading_types`, or set" in text
    assert "heading_types: []` to affirm this source has no heading blocks at all." in text


def test_w7_output_coverage_ratio_outlier_lane_and_blind_spot_present():
    # #202 D3 (partial -- Refs #202, does NOT close it). Each fragment below
    # sits fully on one line each as of this writing, verified with grep:
    #   "  - **New in 1.12.0 -- a within-cohort output-coverage ratio-outlier"
    #   "  structural-completeness gate above, same scope. **WARN-only -- never"
    #   "  - **Stated limitation -- this lane structurally cannot close #202.** It"
    text = _skill_text()
    assert "within-cohort output-coverage ratio-outlier" in text
    # The WARN-only, never-gates contract, on the sentence introducing the
    # shared subcommand both lanes run under.
    assert "structural-completeness gate above, same scope. **WARN-only" in text
    # The stated structural blind spot -- this lane cannot close #202 --
    # must survive any future edit, not just the lane's happy path.
    assert "structurally cannot close #202" in text


def test_operating_constellation_reference_exists_and_has_review_orchestration_content():
    assert OPERATING_CONSTELLATION.is_file(), f"expected {OPERATING_CONSTELLATION} to exist"
    text = OPERATING_CONSTELLATION.read_text(encoding="utf-8")
    # "Independent reviewer" sits fully on one line as of this writing
    # ("- **Independent reviewer, always.** Whatever checks a piece of
    # work must run"); matched case-insensitively since it's a bold
    # heading phrase, not a fixed-case identifier.
    assert "independent reviewer" in text.lower()


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
