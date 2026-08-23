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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _workflow_instantiation as _shared  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

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
# #409 stage 0. A value distinct from both FIXTURE_BATCH_AGENT_CAP and the
# schema's own documented default (400), so a substitution that silently
# no-ops (leaving the template's own literal token or the wrong constant)
# would be caught by a positive landed-value assertion, same reasoning as
# FIXTURE_EFFORT's non-default "xhigh" choice below.
FIXTURE_MAX_CODEX_JOBS_PER_BATCH = 2500
# #197 -- a non-default enum value (never the shipped "high" default) so a
# substitution that silently no-ops (leaving the template's own literal
# "high") would be caught by the positive landed-value assertions below.
FIXTURE_EFFORT = "xhigh"
# Empty string = engine.model unset (the common case) -- the mass template's
# own documented sentinel for "no --model flag threaded to codex_job.py".
FIXTURE_MODEL = ""
# #412 -- empty string = not opted into the --plugin-root redirect (the
# common case, and the template's own documented sentinel, mirroring
# FIXTURE_MODEL above). test_mass_translate_template_plugin_root_substitutes_
# a_real_pinned_path below is the companion positive case, mirroring
# test_mass_translate_template_model_substitutes_a_real_pinned_id.
# MASS-TRANSLATE ONLY -- this token predates #412 with real legacy callers
# relying on the flagless default, so its empty-string opt-out stays real.
# glossary-pass-wf.template.js's OWN {{PLUGIN_ROOT}} is a separate, brand-new
# token with no such caller, and deliberately does NOT share this sentinel
# (see FIXTURE_GLOSSARY_PLUGIN_ROOT below) -- do not "harmonise" the two.
FIXTURE_PLUGIN_ROOT = ""

# #412 -- glossary-pass-wf.template.js's own {{PLUGIN_ROOT}}, unlike
# mass-translate-wf's, throws at instantiation when empty (no legacy caller
# to preserve a flagless-merge default for; see that template's own header
# comment). This fixture is therefore a real, non-empty, allowlist-legal
# path rather than FIXTURE_PLUGIN_ROOT's empty sentinel -- deliberately NOT
# the same constant.
FIXTURE_GLOSSARY_PLUGIN_ROOT = "/fixture/plugin/root/skills/literary-translator"

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
    max_codex_jobs_per_batch: int = FIXTURE_MAX_CODEX_JOBS_PER_BATCH,
    companion_path: str = FIXTURE_COMPANION_PATH,
    effort: str = FIXTURE_EFFORT,
    model: str = FIXTURE_MODEL,
    plugin_root: str = FIXTURE_PLUGIN_ROOT,
) -> str:
    # #413 -- the token set and each token's ENCODING (plain / bare integer /
    # JSON string body / full JSON string literal) now live once, in
    # _workflow_instantiation.py, together with the rationale that used to be
    # restated per token here. This wrapper exists only to keep this file's own
    # signature, whose parameters are all REQUIRED where the tests below want
    # the value stated at the call site rather than defaulted invisibly.
    return _shared.instantiate_mass_translate(
        durable_root=durable_root,
        run_id=run_id,
        source_lang=source_lang,
        target_lang=target_lang,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=batch_agent_cap,
        max_codex_jobs_per_batch=max_codex_jobs_per_batch,
        verse_policy_instruction_block=verse_policy_instruction_block,
        codex_companion_path_json=companion_path,
        effort=effort,
        model=model,
        plugin_root=plugin_root,
    )


def instantiate_glossary_pass(
    *,
    durable_root: str,
    run_id: str,
    source_lang: str,
    target_lang: str,
    research_mode: str,
    batch_agent_cap: int = FIXTURE_BATCH_AGENT_CAP,
    effort: str = FIXTURE_EFFORT,
    citation_content_types: str = "",
    plugin_root: str = FIXTURE_GLOSSARY_PLUGIN_ROOT,
) -> str:
    # #413 -- see instantiate_mass_translate above. The default plugin_root is
    # FIXTURE_GLOSSARY_PLUGIN_ROOT, a real non-empty path, never
    # FIXTURE_PLUGIN_ROOT's empty sentinel: the glossary template throws at
    # instantiation for a blank PLUGIN_ROOT, and that asymmetry with
    # mass-translate-wf's own token is deliberate, not an oversight.
    return _shared.instantiate_glossary_pass(
        durable_root=durable_root,
        run_id=run_id,
        source_lang=source_lang,
        target_lang=target_lang,
        research_mode=research_mode,
        batch_agent_cap=batch_agent_cap,
        effort=effort,
        citation_content_types=citation_content_types,
        plugin_root=plugin_root,
    )


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
# The token authority (#413).
#
# `_workflow_instantiation.TEMPLATE_DEFAULTS` is the ONE declaration of which
# shipped template takes which substitution tokens. The tests below are what
# make it an authority rather than a second copy: they compare it against the
# templates on disk in BOTH directions.
#
# The direction that did not exist before #413 is the load-bearing one. The
# checks these replace looped over a hand-typed token tuple asserting each name
# still appeared in the template -- so a token ADDED to a template with nothing
# to substitute it passed the whole suite, and the miss surfaced later as a
# stale value rendered by whichever call site had not been updated. That is the
# quiet failure #409 actually shipped. `template - declared` closes it; the
# `declared - template` direction closes the mirror case, a default kept for a
# token no template declares any more, whose renderer call silently no-ops.
#
# Neither side is a list this file maintains: one comes from the shipped
# template text, the other from the shared module. There is no third copy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    sorted(_shared.TEMPLATE_DEFAULTS, key=lambda p: p.name),
    ids=lambda p: p.name,
)
def test_declared_token_set_matches_the_shipped_template(template):
    raw = template.read_text(encoding="utf-8")
    in_template = set(_shared.TOKEN_RE.findall(raw))
    declared = set(_shared.TEMPLATE_DEFAULTS[template])

    undeclared = sorted(in_template - declared)
    assert not undeclared, (
        f"{template.name} declares {undeclared} but tests/_workflow_instantiation.py "
        f"has no default for them -- add one there (and an ENCODERS entry), which is "
        f"the ONLY edit a new token needs. Leaving it out renders a stale value at "
        f"every call site that predates the token, which no other test would catch."
    )

    orphaned = sorted(declared - in_template)
    assert not orphaned, (
        f"tests/_workflow_instantiation.py declares {orphaned} for {template.name}, "
        f"but the template no longer contains them -- the substitution is a silent "
        f"no-op. Remove the default (and its ENCODERS entry if no other template "
        f"uses the token)."
    )


def test_every_declared_token_has_an_encoder():
    """A default with no encoder is a `KeyError` at render time, in whichever
    unrelated test file happens to instantiate first. Fail here instead."""
    for template, defaults in _shared.TEMPLATE_DEFAULTS.items():
        missing = sorted(set(defaults) - set(_shared.ENCODERS))
        assert not missing, (
            f"{template.name} declares {missing} with no ENCODERS entry -- an encoder "
            f"says whether the token sits inside its own quotes, outside them, or as a "
            f"bare integer literal, and there is no safe default for that."
        )


def test_integer_tokens_are_bound_to_the_bare_integer_encoder():
    """Presence of an encoder is not the same as the RIGHT encoder.

    `test_every_declared_token_has_an_encoder` above goes red when a token has
    no `ENCODERS` entry, but rebinding one from `_bare_int` to `_plain` left
    every other authority test green -- the rendered value only changes for an
    input that is not already a canonical decimal string, and no current fixture
    passes one. That is the whole failure mode this file exists to make loud, so
    the binding is pinned rather than left to the next reader to notice.

    The pin is a BIJECTION derived from the defaults, not a hand-typed list of
    integer tokens: a list would freeze exactly what it is supposed to detect,
    and would have to be edited for every new token -- the unbounded edit #413
    closed. `type(value) is int` deliberately excludes `bool` (a bool default
    would be a separate mistake, not an integer token).

    The BIJECTION is a convention, and this is where it is declared: **a
    default's Python type is the encoding tag.** An `int` default means "this
    token renders as a bare integer literal"; anything else means it does not.
    Two consequences for whoever adds the next token, because the biconditional
    would otherwise read as a surprising false RED:

    * A token that carries a NUMBER but sits inside its own quotes (`const N =
      "{{SOME_COUNT}}"`) must declare a STRING default -- `"5"`, not `5`. That
      is also what it literally renders as, so the default stays honest.
    * `_bare_int` accepts a numeric string or a bool so a CALLER can pass one
      (`instantiate(..., max_fix_rounds="004")` still emits `4`). That
      normalization is for OVERRIDES; a default that wants to stress it belongs
      at the call site, not in the defaults map.

    The alternative -- a wrapper type carrying the lexical context separately
    from the Python type -- buys nothing here and costs a concept, so it is
    deliberately not built.
    """
    for template, defaults in _shared.TEMPLATE_DEFAULTS.items():
        for name, value in defaults.items():
            encoder = _shared.ENCODERS[name]
            if type(value) is int:
                assert encoder is _shared._bare_int, (
                    f"{template.name}'s {name} defaults to an int but renders via "
                    f"{encoder.__name__} -- a token that sits as a BARE integer "
                    f"literal must use _bare_int, or a non-canonical value like "
                    f'"004" emits invalid JS'
                )
            else:
                assert encoder is not _shared._bare_int, (
                    f"{template.name}'s {name} renders via _bare_int but its "
                    f"default is {type(value).__name__}, not int -- one of the two "
                    f"is wrong about whether this token is a bare integer literal"
                )


def test_every_shipped_template_has_a_declared_token_set():
    """A FOURTH shipped workflow template must not be able to appear with no
    declared token set -- that would restore the unbounded search #413 closed,
    silently, for the one template nothing yet drives."""
    on_disk = {p.resolve() for p in _shared.TEMPLATES_DIR.glob("*.template.js")}
    declared = {p.resolve() for p in _shared.TEMPLATE_DEFAULTS}
    assert on_disk == declared, (
        f"templates on disk with no declared token set: "
        f"{sorted(p.name for p in on_disk - declared)}; "
        f"declared but absent from disk: "
        f"{sorted(p.name for p in declared - on_disk)}"
    )


@pytest.mark.parametrize(
    "template",
    sorted(_shared.TEMPLATE_DEFAULTS, key=lambda p: p.name),
    ids=lambda p: p.name,
)
def test_shipped_defaults_alone_instantiate_with_zero_unresolved_tokens(template):
    """Every template renders from its declared defaults with no override at
    all. Without this, a default could be missing for a token no CURRENT call
    site happens to leave defaulted, and the set-equality test above would
    still pass on a map whose renderer had never been exercised."""
    _assert_no_double_brace(
        _shared.instantiate(template), f"{template.name} @ declared defaults"
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
    assert f"const MAX_CODEX_JOBS_PER_BATCH = {FIXTURE_MAX_CODEX_JOBS_PER_BATCH};" in out, (
        "MAX_CODEX_JOBS_PER_BATCH must substitute as a bare integer literal, not a "
        "quoted string"
    )
    assert f'const EFFORT = "{FIXTURE_EFFORT}";' in out
    assert f'const MODEL = "{FIXTURE_MODEL}";' in out, (
        "MODEL must substitute to the empty string when engine.model is unset"
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

    # #412 -- PLUGIN_ROOT, same json.dumps JS string literal contract as
    # COMPANION above. FIXTURE_PLUGIN_ROOT is the empty/unset sentinel here;
    # test_mass_translate_template_plugin_root_substitutes_a_real_pinned_path
    # below is the companion positive case with a real, non-empty path.
    assert f"const PLUGIN_ROOT = {json.dumps(FIXTURE_PLUGIN_ROOT)};" in out, (
        "PLUGIN_ROOT must substitute to the json.dumps empty string when not "
        "opted into the --plugin-root redirect"
    )


def test_mass_translate_template_model_substitutes_a_real_pinned_id():
    """Companion positive case for the FIXTURE_MODEL="" (unset) coverage
    above: a real, non-empty engine.model id also lands correctly in
    `const MODEL = "{{MODEL}}";`, exercising the substitution with a value
    the template's own MODEL_ARG conditional treats as truthy."""
    out = instantiate_mass_translate(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        max_fix_rounds=FIXTURE_MAX_FIX_ROUNDS,
        batch_agent_cap=FIXTURE_BATCH_AGENT_CAP,
        verse_policy_instruction_block=FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
        model="gpt-5.3-codex",
    )
    _assert_no_double_brace(out, "mass-translate-wf.template.js (model=gpt-5.3-codex)")
    assert 'const MODEL = "gpt-5.3-codex";' in out


def test_mass_translate_template_plugin_root_substitutes_a_real_pinned_path():
    """Companion positive case for the FIXTURE_PLUGIN_ROOT="" (not opted in)
    coverage above: a real, non-empty plugin_root path also lands correctly
    in `const PLUGIN_ROOT = {{PLUGIN_ROOT}};`, exercising the json.dumps
    substitution -- same shape as CODEX_COMPANION_PATH_JSON's own test --
    with a value the template's own PLUGIN_ROOT_ARG conditional treats as
    truthy. Deliberately includes a space and a non-ASCII character (both
    legitimate in a real install path) so the json.dumps escaping is
    actually exercised, mirroring FIXTURE_COMPANION_PATH's own reasoning."""
    # #582: the pinned value carries the `skills/literary-translator` tail a
    # real plugin_root has. `{{PLUGIN_ROOT}}` is this SKILL's directory, not
    # ${CLAUDE_PLUGIN_ROOT} -- consumers append assets/scripts|schemas|
    # templates to it, and the plugin root has no assets/ under it.
    pinned_plugin_root = (
        "/Users/José García/.claude/plugins/literary-translator"
        "/skills/literary-translator"
    )
    out = instantiate_mass_translate(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        max_fix_rounds=FIXTURE_MAX_FIX_ROUNDS,
        batch_agent_cap=FIXTURE_BATCH_AGENT_CAP,
        verse_policy_instruction_block=FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
        plugin_root=pinned_plugin_root,
    )
    _assert_no_double_brace(out, "mass-translate-wf.template.js (plugin_root pinned)")
    assert f"const PLUGIN_ROOT = {json.dumps(pinned_plugin_root)};" in out, (
        "PLUGIN_ROOT must substitute as a strict json.dumps JS string "
        "literal (with its own surrounding quotes), same contract as "
        "CODEX_COMPANION_PATH_JSON"
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
    assert f'const EFFORT = "{FIXTURE_EFFORT}"' in out
    # #412 -- PLUGIN_ROOT, same json.dumps JS string literal contract as
    # mass-translate-wf.template.js's own token, but UNLIKE that token this
    # one is REQUIRED -- the template throws at instantiation for an empty
    # value (see FIXTURE_GLOSSARY_PLUGIN_ROOT's own comment), so this test's
    # default (deliberately not FIXTURE_PLUGIN_ROOT's empty sentinel) must be
    # a real, non-empty path.
    # test_glossary_pass_template_plugin_root_substitutes_a_real_pinned_path
    # below is a second positive case with a different (space + non-ASCII)
    # path, proving the substitution is not coincidentally tied to this one
    # fixture's shape.
    assert f"const PLUGIN_ROOT = {json.dumps(FIXTURE_GLOSSARY_PLUGIN_ROOT)};" in out, (
        "PLUGIN_ROOT must substitute as a strict json.dumps JS string "
        "literal (with its own surrounding quotes)"
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


def test_glossary_pass_template_plugin_root_substitutes_a_real_pinned_path():
    """A second, differently-shaped real plugin_root path (space + non-ASCII
    character, same fixture shape as mass-translate-wf's own companion test)
    also lands correctly in `const PLUGIN_ROOT = {{PLUGIN_ROOT}};` -- proving
    the substitution above is not an artifact of FIXTURE_GLOSSARY_PLUGIN_ROOT's
    particular (plain-ASCII) shape. Unlike mass-translate-wf.template.js,
    there is no "empty/unset" companion case to pair this with here -- an
    empty PLUGIN_ROOT is not a valid value for this template at all (see
    FIXTURE_GLOSSARY_PLUGIN_ROOT's own comment)."""
    pinned_plugin_root = (
        "/Users/José García/.claude/plugins/literary-translator"
        "/skills/literary-translator"
    )
    out = instantiate_glossary_pass(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        source_lang=FIXTURE_SOURCE_LANG,
        target_lang=FIXTURE_TARGET_LANG,
        research_mode="offline",
        plugin_root=pinned_plugin_root,
    )
    _assert_no_double_brace(out, "glossary-pass-wf.template.js (plugin_root pinned)")
    assert f"const PLUGIN_ROOT = {json.dumps(pinned_plugin_root)};" in out, (
        "PLUGIN_ROOT must substitute as a strict json.dumps JS string "
        "literal (with its own surrounding quotes)"
    )


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
