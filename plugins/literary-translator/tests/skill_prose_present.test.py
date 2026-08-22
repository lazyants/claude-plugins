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
10. SKILL.md's W5 section instructs substituting the NEW `{{PLUGIN_ROOT}}`
    Workflow-template token (#412), names it as a mechanically DIFFERENT
    action from this skill's other `{{PLUGIN_ROOT}}` occurrences (plain
    prose the reader substitutes when typing an example command), states
    `resume_setup.py`'s payload carries it as a top-level field deliberately
    excluded from `subst`, and states plainly what omitting it gives up
    (the pre-#412 vulnerability stays open) rather than describing the
    omission as merely "the default".
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
    # #582: the Step 0 definition is pinned POSITIVELY on the value the
    # consumers actually resolve -- this skill's own directory, which is
    # where assets/ lives. It used to read "denotes the plugin's install
    # directory ... ${CLAUDE_PLUGIN_ROOT}", which names a directory with no
    # assets/ under it: every `--plugin-root` consumer appends
    # assets/scripts|schemas|templates, so codex_job.py exits 2 on it.
    # Both halves are pinned so neither can drift back alone.
    text = _skill_text()
    assert "denotes this skill's own directory" in text
    assert "${CLAUDE_PLUGIN_ROOT}/skills/literary-translator" in text
    assert "denotes the plugin's install" not in text


def test_plugin_root_definition_matches_the_shipped_layout():
    # #582: the prose pin above can only catch a reverted SENTENCE. This
    # catches the thing the sentence is about -- that the directory the
    # definition names is really the one holding assets/scripts/. It goes
    # red if the skill directory is renamed or moved without the definition
    # following, which is how the original defect ("${CLAUDE_PLUGIN_ROOT}",
    # one level too high) shipped and stayed shipped.
    skill_dir = PLUGIN_ROOT / "skills" / "literary-translator"
    assert (skill_dir / "assets" / "scripts").is_dir(), (
        f"assets/scripts/ is not under {skill_dir}"
    )
    assert not (PLUGIN_ROOT / "assets").exists(), (
        "the PLUGIN root now has an assets/ too -- the Step 0 definition and "
        "every --plugin-root consumer need re-deciding, not just this test"
    )
    assert f"skills/{skill_dir.name}" in _skill_text()


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


def test_plugin_root_412_redirect_substitution_present():
    # #412. Each fragment below sits fully on one line each as of this
    # writing, verified with grep (never by eye -- see this file's own
    # docstring's hard-wrap warning):
    #   "THIS one is a literal Workflow-template token: it must be written into the"
    #   "payload, as a new top-level `plugin_root` field (deliberately NOT inside"
    #   "substitution is not a neutral default: it leaves the pre-#412"
    text = _skill_text()
    # The mechanical-distinction note: unlike this skill's OTHER
    # {{PLUGIN_ROOT}} occurrences (plain prose the reader substitutes when
    # typing an example command), THIS one is a literal Workflow-template
    # token that must be written into the instantiated .js file itself.
    assert "THIS one is a literal Workflow-template token" in text
    # resume_setup.py's payload carries plugin_root as a top-level field,
    # deliberately excluded from `subst` (and therefore never hashed).
    assert "as a new top-level `plugin_root` field (deliberately NOT inside" in text
    # The omission consequence stated plainly, never as merely "the default".
    assert "not a neutral default: it leaves the pre-#412" in text


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
