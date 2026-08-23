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
11. Every shipped STAMPING-mode `canon_validate.py` command -- the ones
    that write `canon.json`'s `generation_hashes` -- carries
    `--plugin-root {{PLUGIN_ROOT}}`, across SKILL.md and the three
    references that spell one out (#412). These commands no longer merely
    SHOULD carry it: `canon_validate.py` argparse-errors (exit 2) on a
    stamping mode given neither `--plugin-root` nor
    `--allow-durable-sibling`, so an unflagged shipped command is a
    documented command that cannot run.
12. SKILL.md's glossary-pass instantiation section mandates substituting
    `{{PLUGIN_ROOT}}` into `glossary-pass-wf.template.js` too (#412), with
    the same "not a neutral default" framing W5 uses for
    `mass-translate-wf.template.js`.
13. SKILL.md's W7 section ships a literal `final_audit.py` command that
    keeps the `${durable_root}/scripts/` ENTRY POINT (#582) while carrying
    `--plugin-root {{PLUGIN_ROOT}}` for the sibling `select_segments.py`.
14. references/canon-and-glossary.md's "`canon_validate.py`'s CLI modes"
    section records the same refusal beside its own enumeration of what
    every mode requires -- the enumeration a reader consults, and which
    would otherwise be false by omission for the four stamping modes.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
OPERATING_CONSTELLATION = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "operating-constellation.md"
)
REFERENCES = PLUGIN_ROOT / "skills" / "literary-translator" / "references"
LEDGER_AND_RESUMABILITY = REFERENCES / "ledger-and-resumability.md"
CANON_AND_GLOSSARY = REFERENCES / "canon-and-glossary.md"
ORCHESTRATION_AND_BATCHING = REFERENCES / "orchestration-and-batching.md"

assert SKILL_MD.is_file(), f"expected {SKILL_MD} to exist"


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


# --- #412 command-wiring helpers ------------------------------------------------
#
# A bare `"--plugin-root {{PLUGIN_ROOT}}" in text` assertion is wrong in BOTH
# directions here, which is why none of the tests below uses one.
#
#   * FALSE RED: these docs hard-wrap at a fixed column, and a shell command is
#     spelled across lines with a trailing backslash. A needle that happens to
#     straddle the wrap misses even though the flag is intact.
#   * FALSE GREEN: the token appears many times per file. A whole-file
#     membership test stays green after the flag is deleted from the ONE command
#     it was added to, because some unrelated occurrence elsewhere satisfies it.
#
# So: bound a WINDOW between two stable markers around the target command, pull
# the fenced blocks out of that window, select the one naming both the script
# and the mode, and match the flag as a whole argument on the JOINED command.
PLUGIN_ROOT_ARG_RE = re.compile(r"(?<!\S)--plugin-root\s+\{\{PLUGIN_ROOT\}\}(?=\s|$)")


def _window(text: str, start_marker: str, end_marker: str) -> str:
    """The slice of `text` between two markers, each asserted UNIQUE.

    Uniqueness is asserted rather than assumed: a marker that later acquires a
    second occurrence would silently move the window somewhere else, and the
    test would then be pinning a command it was never written about.
    """
    assert text.count(start_marker) == 1, f"start marker not unique: {start_marker!r}"
    assert text.count(end_marker) == 1, f"end marker not unique: {end_marker!r}"
    start = text.index(start_marker)
    end = text.index(end_marker)
    assert start < end, f"markers out of order: {start_marker!r} / {end_marker!r}"
    return text[start:end]


def _fenced_commands(window: str) -> list[str]:
    """Every fenced block in `window`, each joined into ONE logical line.

    Backslash-continuations are folded first, then all runs of whitespace
    collapse to a single space -- so a command's spelling here is independent
    of how the surrounding prose happens to be wrapped today.
    """
    blocks = re.findall(r"```[^\n]*\n(.*?)\n```", window, re.DOTALL)
    return [re.sub(r"\s+", " ", b.replace("\\\n", " ")).strip() for b in blocks]


def _select_command(commands: list[str], *needles: str) -> str:
    """The single command in `commands` naming every needle (script + mode).

    Exactly one match is required in both directions. Zero means the command
    was deleted or renamed; more than one means the window grew and the test no
    longer knows which command it is asserting about.
    """
    matches = [c for c in commands if all(n in c for n in needles)]
    assert len(matches) == 1, (
        f"expected exactly 1 command naming {needles!r}, found {len(matches)}: {matches!r}"
    )
    return matches[0]


def _normalized(path: Path) -> str:
    """Whole-file text with every whitespace run collapsed to one space.

    Used for the INLINE (non-fenced) command sites -- a backticked command
    there is wrapped mid-span by the hard wrap, so it can only be asserted
    after normalization, and the backticks themselves stay part of the needle,
    which is what keeps the assertion pinned to the command rather than to
    loose prose -- and for the #412 PROSE needles, where normalization buys
    something different: a needle that sits on one physical line today keeps
    matching after the paragraph around it is re-wrapped, so a reflow cannot
    turn intact prose into a red.
    """
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


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
    # The wording needle is the real Step-0 pin: the path spelling below also
    # occurs at the #412 substitution paragraph, so it does not localize here.
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


def test_skill_states_what_resume_true_asserts_and_does_not():
    # #538/#544. SKILL.md is the ONLY home for this statement, deliberately:
    # resume_setup.py -- where a reader might expect its own docstring to say
    # it -- is a PLUGIN_BUNDLE_MEMBERS member, so editing a single comment
    # byte there moves plugin_bundle_hash and re-stales every converged
    # segment in every project. A docstring is not worth a book.
    #
    # Each fragment sits fully on one line as of this writing, verified with
    # grep -- never by eye, see this file's own docstring's hard-wrap warning.
    text = _skill_text()
    # The claim itself: digest identity, not evidence of work.
    assert "asserts digest identity and NOT that any work exists" in text
    # And the consequence an operator meets in practice -- a refused claim
    # run's own leftover run directory winning the next resume. A needle on
    # the heading alone would prove a label survived while the explanation
    # under it had been deleted.
    assert "- **A claim run whose Step 1 is REFUSED leaves its `runs/<RUN_ID>/` and" in text


# --- #412: every shipped STAMPING-mode command names a trusted plugin root ------
#
# canon_validate.py's four generation_hashes-stamping modes (--init,
# --restamp-derivation, --merge-batches, and the legacy bare --batch merge) now
# argparse-error (exit 2) unless handed --plugin-root PATH or the explicit
# --allow-durable-sibling escape hatch. So these are not style assertions: a
# shipped stamping command that loses the flag is a documented command that
# cannot run at all. The NON-stamping modes (--check-batch, --verify-merged,
# validate-only) resolve no sibling and are deliberately NOT asserted here --
# adding the flag to one of those would be the defect, not the fix.


def test_w3_init_bootstrap_command_carries_plugin_root():
    # SKILL.md, W3's `no_new_candidates` SKIP branch: the one shipped --init
    # command. Window-bounded rather than file-wide, because `{{PLUGIN_ROOT}}`
    # occurs throughout SKILL.md and a file-wide needle would stay green after
    # this command lost the flag.
    window = _window(
        _skill_text(),
        "or W3a below dies with `FATAL: canon.json not found`:",
        "That writes an empty-but-stamped `canon.json` (`entries: {}`,",
    )
    command = _select_command(_fenced_commands(window), "canon_validate.py", "--init")
    assert PLUGIN_ROOT_ARG_RE.search(command), (
        "W3 SKIP-branch canon_validate.py --init command must carry "
        f"--plugin-root {{{{PLUGIN_ROOT}}}}; got: {command!r}"
    )


def test_w7_final_audit_command_keeps_durable_entry_point_and_carries_plugin_root():
    # SKILL.md W7. Two claims in one command, and they pull in OPPOSITE
    # directions, which is exactly why both are pinned together:
    #   * the ENTRY POINT stays ${durable_root}/scripts/ -- SKILL.md's own
    #     "#582 -- why the ENTRY POINT stays" paragraph decided that
    #     deliberately, so a well-meaning future edit relocating it to
    #     {{PLUGIN_ROOT}} must fail here rather than land quietly;
    #   * the SIBLING select_segments.py the completeness gate shells out to
    #     must come from the trusted tree, which is what the flag does.
    window = _window(
        _skill_text(),
        "- **W7 Final audit (#208):** `final_audit.py`'s exit code is now fail-closed on",
        "- **Frontback coverage report** (advisory, informational, never",
    )
    command = _select_command(_fenced_commands(window), "final_audit.py")
    assert "${durable_root}/scripts/final_audit.py" in command, (
        "W7's final_audit.py entry point must stay in the durable tree (#582); "
        f"got: {command!r}"
    )
    assert PLUGIN_ROOT_ARG_RE.search(command), (
        "W7 final_audit.py command must carry --plugin-root "
        f"{{{{PLUGIN_ROOT}}}} for the sibling select_segments.py; got: {command!r}"
    )


def test_restamp_derivation_command_carries_plugin_root():
    # references/ledger-and-resumability.md, the zero-candidate escape
    # (1.15.0, #193/#291) -- the only fenced --restamp-derivation command
    # shipped anywhere in the skill.
    window = _window(
        LEDGER_AND_RESUMABILITY.read_text(encoding="utf-8"),
        "sanctioned escape is an explicit restamp, which re-records BOTH fields:",
        "then re-run `segpack.py` (in that order — segpack copies `canon.json`'s",
    )
    command = _select_command(
        _fenced_commands(window), "canon_validate.py", "--restamp-derivation"
    )
    assert PLUGIN_ROOT_ARG_RE.search(command), (
        "the zero-candidate --restamp-derivation escape command must carry "
        f"--plugin-root {{{{PLUGIN_ROOT}}}}; got: {command!r}"
    )


def test_canon_reference_inline_restamp_command_carries_plugin_root():
    # references/canon-and-glossary.md spells this one INLINE, inside
    # backticks, and the hard wrap splits the span -- so it is asserted on the
    # whitespace-normalized file, with the backticks themselves in the needle.
    # The backticks are what keep this pinned to the command: without them the
    # assertion would also be satisfied by loose surrounding prose.
    needle = (
        "`canon_validate.py --research-mode <mode> --restamp-derivation "
        "--plugin-root {{PLUGIN_ROOT}}`"
    )
    assert needle in _normalized(CANON_AND_GLOSSARY), (
        "canon-and-glossary.md's inline --restamp-derivation command must read "
        f"{needle}"
    )


def test_canon_reference_inline_init_command_carries_plugin_root():
    # Same file, the #290 SKIP-branch bootstrap sentence -- also inline, also
    # wrapped mid-span. This is the reference-side twin of the fenced SKILL.md
    # command asserted above; the two are edited independently, so they are
    # pinned independently.
    needle = (
        "`canon_validate.py --research-mode <mode> --init "
        "--plugin-root {{PLUGIN_ROOT}}`"
    )
    assert needle in _normalized(CANON_AND_GLOSSARY), (
        "canon-and-glossary.md's inline --init bootstrap command must read "
        f"{needle}"
    )


def test_orchestration_merge_batches_command_carries_plugin_root():
    # references/orchestration-and-batching.md describes the glossary pass's
    # single serialized writer -- the merge is the stamping mode here, so this
    # is the command the template's own builder has to spell out. Inline and
    # wrapped, hence the normalized-file form. The ellipsis is the literal
    # U+2026 the document uses, not three periods.
    needle = (
        "`canon_validate.py --merge-batches <frag1> <frag2> … "
        "--research-mode X --plugin-root {{PLUGIN_ROOT}}`"
    )
    # #505 -- the live-only attestation the same command now carries. Pinned
    # beside the command rather than folded into the needle above, because the
    # flag is appended CONDITIONALLY by the template (CITATION_REVIEW_ENABLED)
    # and the prose says so in words rather than in the code span.
    assert "--citations-reviewed" in _normalized(ORCHESTRATION_AND_BATCHING), (
        "orchestration-and-batching.md's final-merge description must name the "
        "#505 attestation the template appends under research_mode:live"
    )
    assert needle in _normalized(ORCHESTRATION_AND_BATCHING), (
        "orchestration-and-batching.md's final-merge command must read "
        f"{needle}"
    )


def test_glossary_pass_instantiation_mandates_plugin_root_token():
    # #412. The W5 half of this mandate is already pinned by
    # test_plugin_root_412_redirect_substitution_present above; this is the W3
    # glossary-pass half, which is a SEPARATE template
    # (glossary-pass-wf.template.js) instantiated by a separate section.
    #
    # The needles below are matched against the WHITESPACE-NORMALIZED file, so
    # a hard wrap through any of them is harmless -- which is why no
    # "sits on one line" claim is made here, unlike this file's raw-text
    # assertions further up. Composed with grep, never by eye, per this file's
    # own docstring's hard-wrap warning:
    #   "**#412:** that same instantiation ALSO substitutes `{{PLUGIN_ROOT}}` into"
    #   "which is the wrong value: an installed plugin has no `assets/` at its root,"
    #   "not a neutral default: it leaves the pre-#412 vulnerability open** — a"
    #
    # Matched against the NORMALIZED file, not the raw one, so that a future
    # re-wrap of this paragraph -- which moves every wrap point in it -- does
    # not turn an intact mandate into a red.
    text = _normalized(SKILL_MD)
    # The mandate itself, naming the template it applies to.
    needle = "**#412:** that same instantiation ALSO substitutes `{{PLUGIN_ROOT}}` into"
    assert needle in text, f"glossary-pass #412 substitution mandate missing: {needle!r}"
    needle = "`glossary-pass-wf.template.js` — this skill's own directory, the SAME value"
    assert needle in text, f"mandate must name the template it applies to: {needle!r}"
    # ${CLAUDE_PLUGIN_ROOT} itself is the WRONG value -- the trap that makes an
    # otherwise-plausible substitution resolve to a directory that is not there.
    needle = "which is the wrong value: an installed plugin has no `assets/` at its root,"
    assert needle in text, (
        f"mandate must warn that ${{CLAUDE_PLUGIN_ROOT}} itself is WRONG: {needle!r}"
    )
    # And the omission consequence stated plainly, the same framing W5 uses --
    # never as merely "the default".
    needle = "not a neutral default: it leaves the pre-#412 vulnerability open** — a"
    assert needle in text, (
        f"mandate must state the omission consequence, not soften it: {needle!r}"
    )


def test_skill_records_stamping_mode_plugin_root_refusal():
    # #412. The refusal is a behaviour change an operator meets as an exit-2
    # halt, so SKILL.md has to state it where the reader meets the canon
    # commands -- otherwise the halt reads as a bug in the documented command.
    #
    # Matched against the WHITESPACE-NORMALIZED file, so a hard wrap through
    # any needle is harmless. Composed with grep, never by eye, per this file's
    # own docstring's hard-wrap warning:
    #   "**#412 — a stamping mode now REFUSES to guess which `cache_key.py` to"
    #   "them now halts with an argparse error (exit `2`) unless it is handed either"
    #   "`--allow-durable-sibling` is the sanctioned opt-out for a hand-run"
    #   "`--verify-merged`, and validate-only (no mode flag) — resolve no sibling at"
    #
    # Matched against the NORMALIZED file (see the sibling test above).
    text = _normalized(SKILL_MD)
    needle = "**#412 — a stamping mode now REFUSES to guess which `cache_key.py` to"
    assert needle in text, f"stamping-mode refusal heading missing: {needle!r}"
    # The halt itself, with its exit code and BOTH accepted answers -- a needle
    # on the heading alone would prove a label survived while the mechanism
    # under it had been deleted.
    needle = "them now halts with an argparse error (exit `2`) unless it is handed either"
    assert needle in text, (
        f"refusal must state the exit-2 halt and both accepted answers: {needle!r}"
    )
    # The escape hatch, and what it is FOR: a hand-run recovery with no
    # orchestrating session to supply a plugin root.
    needle = "`--allow-durable-sibling` is the sanctioned opt-out for a hand-run"
    assert needle in text, (
        f"refusal must name the escape hatch and what it is for: {needle!r}"
    )
    # And the scope limit -- the non-stamping modes are unaffected, so nobody
    # "fixes" --check-batch by bolting the flag onto it.
    needle = "`--verify-merged`, and validate-only (no mode flag) — resolve no sibling at"
    assert needle in text, (
        f"refusal must state that NON-stamping modes are unaffected: {needle!r}"
    )


def test_canon_reference_cli_modes_section_records_stamping_mode_refusal():
    # #412. canon-and-glossary.md's "`canon_validate.py`'s CLI modes" section
    # opens by enumerating what is required on EVERY mode (`--research-mode`,
    # never defaulted). That enumeration is what a reader consults to answer
    # "what must I pass?", so leaving the stamping-mode refusal out of it makes
    # the enumeration false by omission for four of the seven modes -- and the
    # reader meets the consequence as an unexplained exit-2 halt.
    #
    # Matched against the WHITESPACE-NORMALIZED file, so a hard wrap through
    # any needle is harmless. Composed with grep, never by eye, per this file's
    # own docstring's hard-wrap warning:
    #   "**#412 — a second requirement, on the STAMPING modes only.** The four modes"
    #   "merge — additionally refuse to run, with an argparse error (exit `2`),"
    #   "validate-only — resolve no sibling and accept neither flag's obligation; do"
    #
    # Matched against the NORMALIZED file (see the sibling tests above).
    text = _normalized(CANON_AND_GLOSSARY)
    needle = "**#412 — a second requirement, on the STAMPING modes only.** The four modes"
    assert needle in text, f"CLI-modes section must record the refusal: {needle!r}"
    # The halt itself, with its exit code -- a needle on the heading alone
    # would prove a label survived while the mechanism under it was deleted.
    needle = "merge — additionally refuse to run, with an argparse error (exit `2`),"
    assert needle in text, f"refusal must state the exit-2 halt: {needle!r}"
    # And the scope limit, so nobody "fixes" --check-batch by bolting the flag on.
    needle = "validate-only — resolve no sibling and accept neither flag's obligation; do"
    assert needle in text, f"refusal must exempt the NON-stamping modes: {needle!r}"


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
