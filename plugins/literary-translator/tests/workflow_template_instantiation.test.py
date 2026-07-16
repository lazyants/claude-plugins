"""tests/workflow_template_instantiation.test.py

Targets ``assets/templates/mass-translate-wf.template.js`` and
``assets/templates/glossary-pass-wf.template.js`` (see
references/orchestration-and-batching.md's "Prompt functions -- generated
from the profile at instantiation time" section and
references/workflow-schema-validation.md's matching paragraph).

Both files are GENERATED-ONLY templates: the orchestrating Claude session
reads the plugin's own shipped copy and performs a ONE-TIME, plain-text
substitution of every documented ``{{TOKEN}}`` placeholder -- there is no
templating engine at Workflow-runtime, so a substitution the instantiation
step should have performed but didn't is a hard bug, not a cosmetic one
(SKILL.md's W3/W5 steps: "instantiate ... fresh from the plugin's current
copy every time").

This file re-implements that same one-time substitution (the exact
contract each template documents in its own header comment) against a
fixture profile, then, per both reference docs above:

    "greps the output for a literal '{{', asserting zero matches -- no
    substitution token left unresolved"

for BOTH templates. The glossary-pass case runs TWICE, once with
``research_mode: live`` and once with ``research_mode: offline``, proving
``{{RESEARCH_MODE}}`` resolves correctly in both directions.

Beyond the bare "zero {{ matches" grep, this file also positively checks
that each substituted value actually landed in the right place (a
DURABLE_ROOT/RUN_ID const, bare-integer MAX_FIX_ROUNDS/BATCH_AGENT_CAP
literals -- never quoted strings -- and a correctly JSON-escaped
VERSE_POLICY_INSTRUCTION_BLOCK, per that token's own documented escaping
contract) so the "zero matches" assertion can't pass vacuously against an
instantiation helper that silently no-ops.

FORMERLY-KNOWN-FAILING CASE, now resolved: an earlier revision of
``glossary-pass-wf.template.js``'s own header comment contained two literal
``{{`` substrings inside plain English prose (describing this very test),
which survived whole-file plain-text instantiation and made the bare
"zero {{ matches" grep fail for both ``research_mode`` directions even
though every real, named token substituted correctly. The 1.2.0
reliability build's glossary-pass-wf.template.js rewrite (adding
``{{RUN_ID}}`` among other changes) no longer contains that stray prose
``{{`` -- confirmed via ``grep -n '{{' glossary-pass-wf.template.js`` showing
only the five real, named tokens -- so the bare "zero {{ matches" assertion
now passes cleanly for both directions, same as mass-translate-wf's own.
``test_glossary_pass_template_has_no_unresolved_named_token`` below is kept
regardless, as the narrower, second-order check it always was.

``{{RUN_ID}}`` (CONTRACT-1.2.0-reliability.md sec2): a NEW documented
substitution token both templates gained in the 1.2.0 reliability build,
resolved once by the orchestrating session (fresh id, or the identical
value reused via ``resumeFromRunId`` on a matched-digest resumed run) and
substituted the same plain-string way ``{{DURABLE_ROOT}}`` already is. This
file's ``FIXTURE_RUN_ID`` is a stable, colon-free, allowlist-legal value;
``test_run_id_token_resolves_with_zero_unresolved_braces`` further exercises
several other allowlist-legal shapes (the allowlist regex validation itself
is upstream orchestrator logic, out of scope for a plain-text-substitution
test like this one).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

# The exact substitution tokens each template documents in its own header
# comment (references/orchestration-and-batching.md's "Prompt functions"
# section restates the same list). Kept here only to sanity-check the raw,
# un-substituted template still declares each one -- never used as the
# expectation for what the instantiated OUTPUT should equal.
MASS_TRANSLATE_TOKENS = (
    "{{DURABLE_ROOT}}",
    "{{RUN_ID}}",
    "{{SOURCE_LANG}}",
    "{{TARGET_LANG}}",
    "{{MAX_FIX_ROUNDS}}",
    "{{BATCH_AGENT_CAP}}",
    "{{VERSE_POLICY_INSTRUCTION_BLOCK}}",
    # #198 -- resolved codex-companion.mjs path, substituted as a strict
    # json.dumps JS STRING LITERAL (WITH its own quotes -- the token sits
    # OUTSIDE quotes in `const COMPANION = {{CODEX_COMPANION_PATH_JSON}};`).
    "{{CODEX_COMPANION_PATH_JSON}}",
)
GLOSSARY_PASS_TOKENS = (
    "{{DURABLE_ROOT}}",
    "{{RUN_ID}}",
    "{{SOURCE_LANG}}",
    "{{TARGET_LANG}}",
    "{{RESEARCH_MODE}}",
    "{{BATCH_AGENT_CAP}}",
)

# A named-token shape (always {{UPPER_SNAKE_CASE}} in both templates) --
# used for the stricter, second-order check below that specifically targets
# "a documented substitution token was left unresolved", independent of
# whichever literal '{{' substring the primary spec-mandated check greps
# the whole file for.
NAMED_TOKEN_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


# ---------------------------------------------------------------------------
# Fixture profile -- plain resolved values, mirroring what the orchestrating
# session would already have read out of a real profile.yml (source.language
# .code, target.language.code, project.durable_root, engine.max_fix_rounds,
# engine.batch_agent_cap, the resolved verse-policy instruction text, and
# glossary.research_mode) by the time it instantiates either template. This
# test's job starts AFTER that resolution, at the text-substitution step
# itself -- it never parses YAML.
# ---------------------------------------------------------------------------

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
# #198 -- a resolved codex-companion.mjs path. Deliberately includes a space
# and a non-ASCII character (both LEGITIMATE per resolve_codex_companion.py,
# which rejects only a quote / control char / newline) so the json.dumps
# substitution is exercised on a value that would break a naive splice.
FIXTURE_COMPANION_PATH = "/Users/José García/codex/1.0.10/codex-companion.mjs"
# A stable, colon-free, allowlist-legal fixture value -- CONTRACT sec2's
# {{RUN_ID}} allowlist (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, never '.'/'..', no
# '..' substring) plus its own "colon-free YYYYMMDDTHHMMSSZ form" example.
# This file only substitutes it (the allowlist itself is a JS/orchestrator-
# side concern, out of scope here) -- see test_run_id_token_resolves_with_
# zero_unresolved_braces below for coverage across several legal shapes.
FIXTURE_RUN_ID = "20260710T000000Z"
FIXTURE_SOURCE_LANG = "fr"
FIXTURE_TARGET_LANG = "ru"
FIXTURE_MAX_FIX_ROUNDS = 4
FIXTURE_BATCH_AGENT_CAP = 1000

# Deliberately includes a double quote, a backslash, and a real embedded
# newline -- exactly the characters the template's own header comment warns
# about ("so any quote or newline in the resolved instruction text stays a
# valid JS string body"). Deliberately does NOT itself contain the literal
# substring "{{" -- that would confound the very "zero {{ after
# substitution" check below with an artifact of the fixture value rather
# than of the substitution mechanism.
FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK = (
    'Render every verse literally, line by line -- no rhyme scheme. Watch '
    'for "quoted" phrases and a stray backslash \\ in the source text.\n'
    "A second physical line follows a real embedded newline."
)


# ---------------------------------------------------------------------------
# Instantiation helpers -- each re-implements the exact substitution
# contract its template's own header comment documents. Plain string
# replacement only, matching "there is no templating engine at
# Workflow-runtime" / "the orchestrating session substitutes once ... before
# the Workflow tool ever executes it" (orchestration-and-batching.md).
# ---------------------------------------------------------------------------


def instantiate_mass_translate(
    *,
    durable_root: str,
    run_id: str,
    source_lang: str,
    target_lang: str,
    max_fix_rounds: int,
    batch_agent_cap: int,
    verse_policy_instruction_block: str,
    companion_path: str = FIXTURE_COMPANION_PATH,
) -> str:
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")

    # Plain string tokens -- each already sits inside its own quotes in the
    # template (e.g. `const ROOT = "{{DURABLE_ROOT}}";`), so the raw value
    # is spliced in as-is.
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)

    # Bare-integer tokens -- the header comment is explicit that these two
    # substitute as a BARE integer literal (`const MAXFIX = {{MAX_FIX_ROUNDS}};`),
    # never a quoted string.
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))

    # VERSE_POLICY_INSTRUCTION_BLOCK -- the header comment requires a
    # JSON-string-escaped form with the outer quotes stripped (the token
    # already sits inside its own quotes in the template:
    # `const VERSE_POLICY_INSTRUCTION_BLOCK = "{{VERSE_POLICY_INSTRUCTION_BLOCK}}";`),
    # so any quote/backslash/newline in the resolved text stays a valid JS
    # string body.
    escaped_verse_block = json.dumps(verse_policy_instruction_block)[1:-1]
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", escaped_verse_block)

    # #198 CODEX_COMPANION_PATH_JSON -- unlike the plain-string tokens, this
    # one sits OUTSIDE its quotes in the template
    # (`const COMPANION = {{CODEX_COMPANION_PATH_JSON}};`), so the orchestrator
    # substitutes a full json.dumps JS string LITERAL (quotes included).
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(companion_path))

    return text


def instantiate_glossary_pass(
    *,
    durable_root: str,
    run_id: str,
    source_lang: str,
    target_lang: str,
    research_mode: str,
    batch_agent_cap: int = FIXTURE_BATCH_AGENT_CAP,
) -> str:
    text = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")

    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)
    # research_mode is passed through literally -- "this script never
    # parses YAML itself" (the template's own header comment).
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    # batch_agent_cap -- the SAME engine.batch_agent_cap field the mass
    # template reads, substituted as a BARE integer literal (never a quoted
    # string), feeding the glossary preflight cost cap.
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))

    return text


def _context_around(text: str, index: int, radius: int = 60) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + radius)
    return text[start:end]


def _assert_no_double_brace(text: str, label: str) -> None:
    idx = text.find("{{")
    if idx != -1:
        pytest.fail(
            f"{label}: found a leftover literal '{{{{' at offset {idx} -- "
            f"a substitution token was left unresolved. Context: "
            f"{_context_around(text, idx)!r}"
        )


# ---------------------------------------------------------------------------
# Sanity: the raw, un-substituted templates actually declare every token
# this file's instantiate helpers replace -- guards against a future rename
# of a token in the template silently turning one of our .replace() calls
# into a no-op that never exercises anything.
# ---------------------------------------------------------------------------


def test_mass_translate_raw_template_declares_every_documented_token():
    raw = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    for token in MASS_TRANSLATE_TOKENS:
        assert raw.count(token) >= 1, (
            f"expected {MASS_TRANSLATE_TEMPLATE.name} to still declare {token} "
            f"at least once; the instantiation contract may have drifted"
        )


def test_glossary_pass_raw_template_declares_every_documented_token():
    raw = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")
    for token in GLOSSARY_PASS_TOKENS:
        assert raw.count(token) >= 1, (
            f"expected {GLOSSARY_PASS_TEMPLATE.name} to still declare {token} "
            f"at least once; the instantiation contract may have drifted"
        )


# ---------------------------------------------------------------------------
# mass-translate-wf.template.js
# ---------------------------------------------------------------------------


def test_mass_translate_template_instantiates_with_zero_unresolved_tokens():
    out = instantiate_mass_translate(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        max_fix_rounds=FIXTURE_MAX_FIX_ROUNDS,
        batch_agent_cap=FIXTURE_BATCH_AGENT_CAP,
        verse_policy_instruction_block=FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
    )

    _assert_no_double_brace(out, "mass-translate-wf.template.js")

    # Positive checks: the "zero {{ left over" assertion above must not pass
    # vacuously -- confirm each value actually landed, in the exact shape
    # the header comment documents.
    assert f'const ROOT = "{FIXTURE_DURABLE_ROOT}";' in out
    assert f'const RUN_ID = "{FIXTURE_RUN_ID}";' in out
    assert f'const SOURCE_LANG = "{FIXTURE_SOURCE_LANG}";' in out
    assert f'const TARGET_LANG = "{FIXTURE_TARGET_LANG}";' in out
    assert f"const MAXFIX = {FIXTURE_MAX_FIX_ROUNDS};" in out, (
        "MAX_FIX_ROUNDS must substitute as a bare integer literal, not a "
        "quoted string"
    )
    assert f"const BATCH_AGENT_CAP = {FIXTURE_BATCH_AGENT_CAP};" in out, (
        "BATCH_AGENT_CAP must substitute as a bare integer literal, not a "
        "quoted string"
    )

    expected_escaped_verse_block = json.dumps(FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK)[1:-1]
    assert (
        f'const VERSE_POLICY_INSTRUCTION_BLOCK = "{expected_escaped_verse_block}";' in out
    ), "VERSE_POLICY_INSTRUCTION_BLOCK must be JSON-string-escaped with the outer quotes stripped"

    # #198 -- COMPANION substitutes as a full json.dumps JS string literal
    # (quotes included, token OUTSIDE quotes in the template), and the
    # space/non-ASCII fixture path stays a valid JS string body.
    assert f"const COMPANION = {json.dumps(FIXTURE_COMPANION_PATH)};" in out, (
        "CODEX_COMPANION_PATH_JSON must substitute as a strict json.dumps JS "
        "string literal (with its own surrounding quotes)"
    )


# ---------------------------------------------------------------------------
# glossary-pass-wf.template.js -- both research_mode directions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("research_mode", ["live", "offline"])
def test_glossary_pass_template_instantiates_with_zero_unresolved_tokens(research_mode):
    out = instantiate_glossary_pass(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        research_mode=research_mode,
    )

    _assert_no_double_brace(out, f"glossary-pass-wf.template.js (research_mode={research_mode})")

    assert f'const ROOT = "{FIXTURE_DURABLE_ROOT}"' in out
    assert f'const RUN_ID = "{FIXTURE_RUN_ID}"' in out
    assert f'const SOURCE_LANG = "{FIXTURE_SOURCE_LANG}"' in out
    assert f'const TARGET_LANG = "{FIXTURE_TARGET_LANG}"' in out
    assert f'const RESEARCH_MODE = "{research_mode}"' in out, (
        f"{{{{RESEARCH_MODE}}}} must resolve to the literal fixture value "
        f"{research_mode!r}"
    )
    assert f"const BATCH_AGENT_CAP = {FIXTURE_BATCH_AGENT_CAP}" in out, (
        "{{BATCH_AGENT_CAP}} must substitute as a bare integer literal, not a "
        "quoted string (matching mass-translate-wf.template.js's own token)"
    )


@pytest.mark.parametrize("research_mode", ["live", "offline"])
def test_glossary_pass_template_has_no_unresolved_named_token(research_mode):
    """A narrower, second-order version of the check above: rather than the
    bare '{{' substring the spec names, this scans specifically for any
    remaining {{UPPER_SNAKE_CASE}}-shaped substitution token (the only shape
    every documented token in this codebase ever takes). This isolates
    "a real substitution token was left unresolved" from any incidental,
    non-token '{{' that might otherwise appear in the file's own prose (see
    the known template defect this file's docstring / returned notes call
    out for glossary-pass-wf.template.js's header comment)."""
    out = instantiate_glossary_pass(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        research_mode=research_mode,
    )

    leftover = NAMED_TOKEN_RE.findall(out)
    assert leftover == [], f"unresolved named substitution token(s) remain: {leftover}"


# ---------------------------------------------------------------------------
# {{RUN_ID}} -- a NEW substitution token both templates gained in the 1.2.0
# reliability build (CONTRACT-1.2.0-reliability.md sec2: "NEW documented
# substitution token in BOTH templates' token lists"). Beyond the blanket
# "zero unresolved tokens" coverage above (which already exercises ONE fixed
# RUN_ID value per template), this exercises RUN_ID specifically, across
# several allowlist-legal shapes (a colon-free timestamp, a short id, and an
# id containing every allowlisted punctuation character), mirroring how
# this file already gives every other individual token its own targeted
# check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_id", ["20260710T000000Z", "a1", "run-01.beta_2"])
def test_run_id_token_resolves_with_zero_unresolved_braces(run_id):
    mass_out = instantiate_mass_translate(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=run_id,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        max_fix_rounds=FIXTURE_MAX_FIX_ROUNDS,
        batch_agent_cap=FIXTURE_BATCH_AGENT_CAP,
        verse_policy_instruction_block=FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
    )
    _assert_no_double_brace(mass_out, f"mass-translate-wf.template.js (run_id={run_id})")
    assert f'const RUN_ID = "{run_id}";' in mass_out

    glossary_out = instantiate_glossary_pass(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=run_id,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        research_mode="live",
    )
    leftover = NAMED_TOKEN_RE.findall(glossary_out)
    assert leftover == [], f"unresolved named substitution token(s) remain: {leftover}"
    assert f'const RUN_ID = "{run_id}"' in glossary_out


# ---------------------------------------------------------------------------
# #91 -- the glossary dispatch prompt carries the elision-adjudication rule
# and names the two new candidate fields (elision_ambiguous /
# elision_stripped_form). This rule is prose inside batchDispatchPrompt(),
# regenerated fresh every run, so a content-regression lock here is the only
# guard against the rule being silently dropped -- the "zero unresolved
# braces" greps above check substitution, never prompt content. Red against
# origin/main, which ships neither field name.
# ---------------------------------------------------------------------------


def test_glossary_dispatch_prompt_carries_elision_adjudication_rule():
    raw = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")

    # Both new candidate fields are named to the adjudicator.
    assert "elision_ambiguous" in raw, (
        "batchDispatchPrompt must reference the elision_ambiguous flag (#91)"
    )
    assert "elision_stripped_form" in raw, (
        "batchDispatchPrompt must reference elision_stripped_form (#91)"
    )

    # The adjudication rule itself: an elision_ambiguous row must route to
    # review_queue unless confirmed. Asserting a single line ties the flag to
    # review_queue prevents a future edit from keeping the field name while
    # silently dropping the 'queue it for a human' instruction.
    rule_lines = [
        ln for ln in raw.splitlines()
        if "elision_ambiguous" in ln and "review_queue" in ln
    ]
    assert rule_lines, (
        "expected a batchDispatchPrompt line that routes an elision_ambiguous "
        "candidate to review_queue unless confirmed (#91 adjudication rule)"
    )
