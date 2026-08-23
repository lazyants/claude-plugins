"""tests/_workflow_instantiation.py -- the ONE authority for what substitution
tokens the three shipped workflow templates take, and the one helper that
renders them (#413).

Before this module every test file that drove a workflow template carried its
own `.replace("{{TOKEN}}", ...)` chain, each one a private copy of the token
list. Twenty-one files carried such a copy, and nothing in the suite compared
any of them against the templates on disk -- so adding a token to a template
was an unbounded search through the tests rather than a bounded edit, and a
file whose copy lacked the new key rendered a STALE value instead of an
unresolved token, which is quiet. #409 shipped exactly that miss.

What this module changes, and what it deliberately does NOT:

* The token SET and its ENCODING live here once. Adding a `{{TOKEN}}` to a
  shipped template is now: edit the template, add one default here, add one
  encoder here. No test file needs touching, because every caller takes its
  value from the defaults below unless it overrides it on purpose.
* `workflow_token_authority` (in workflow_template_instantiation.test.py)
  compares each template's token set against the matching defaults map in BOTH
  directions, so a token added to a template with no default here -- or a
  default here for a token no template declares -- fails loudly at this file.
* Per-file fixture VALUES are not centralised away. Many call sites assert on
  the value they substitute (a run id echoed into a dispatch token, a durable
  root spliced into a draft path, a deliberately hostile `effort`), and several
  pick a value precisely BECAUSE it differs from what the shipped default would
  be. Those stay explicit overrides at the call site; the defaults here exist
  for the tokens a given file does not care about.

This reverses the "duplicated, not imported, so this file stays self-contained
like every sibling" convention those helpers each recorded in their docstring.
That self-containment is what #413 asks to give up: it bought nothing except a
guarantee that no two files could disagree by accident -- which is precisely
what they did.

Not a plugin-bundle member: `cache_key.PLUGIN_BUNDLE_MEMBERS`,
`DERIVATION_BUNDLE_MEMBERS` and `scaffold_setup`'s orchestration bundle are
basename allowlists over `assets/scripts` and `assets/templates`, so nothing
under `tests/` can enter a hash. This module therefore costs no
re-translation, no resume invalidation and no render-baseline migration.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"

MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
SKEPTIC_PASS_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"

# The shape every substitution token has, in all three templates. The group
# captures the BARE name (`DURABLE_ROOT`), which is how the defaults maps and
# `ENCODERS` below are keyed; the braces are re-added only at replacement time.
# Keying on the bare name is what lets the authority test compare a template's
# tokens against a defaults map without either side re-spelling the delimiters.
TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


# ---------------------------------------------------------------------------
# Encoders -- how a raw Python value becomes the text that replaces the token.
#
# The choice is fixed by where the token SITS in the template, not by the value:
# a token inside quotes must not carry its own, and a token outside them must.
# No token has two different executable contexts within one template, and no
# token shared between templates is encoded differently in each -- the
# `test_every_token_has_one_encoder` / authority tests below keep that true.
# ---------------------------------------------------------------------------

def _plain(value: object) -> str:
    """Token sits INSIDE its own quotes (`const ROOT = "{{DURABLE_ROOT}}"`).

    Substituted verbatim, matching what every pre-#413 helper did. Deliberately
    NOT escape-hardened: doing so would silently start `\\u`-escaping non-ASCII
    fixture values that today reach the rendered JS as themselves, and several
    call sites assert on the rendered text.
    """
    return str(value)


def _bare_int(value: object) -> str:
    """Token sits as a BARE integer literal (`const MAXFIX = {{MAX_FIX_ROUNDS}};`).

    `int()` first, so a test that passes a bool or a numeric string still emits
    a legal JS integer rather than `True` / a quoted digit.
    """
    return str(int(value))  # type: ignore[arg-type]


def _js_string_body(value: object) -> str:
    """Token sits INSIDE quotes but carries free prose that may contain a quote
    or a newline (`const VERSE_POLICY_INSTRUCTION_BLOCK = "{{...}}";`).

    `json.dumps(...)[1:-1]` is the body of a JSON string literal with the
    surrounding quotes stripped -- i.e. exactly the escaping the enclosing JS
    quotes need, and nothing more. For a value with nothing to escape this is
    byte-identical to `_plain`, which is why the call sites that used to
    substitute a bare sentence here keep rendering the same text.
    """
    return json.dumps(str(value))[1:-1]


def _js_string_literal(value: object) -> str:
    """Token sits OUTSIDE quotes and must supply its own
    (`const COMPANION = {{CODEX_COMPANION_PATH_JSON}};`).

    Plain `json.dumps`, `ensure_ascii` left at its default -- that is what the
    pre-#413 helpers used, so a non-ASCII path keeps rendering as the
    `\\uXXXX`-escaped literal the existing assertions match on.
    """
    return json.dumps(str(value))


ENCODERS: dict[str, Callable[[object], str]] = {
    # --- inside quotes, plain -------------------------------------------------
    "DURABLE_ROOT": _plain,
    "RUN_ID": _plain,
    "SOURCE_LANG": _plain,
    "TARGET_LANG": _plain,
    "EFFORT": _plain,
    "MODEL": _plain,
    "RESEARCH_MODE": _plain,
    "CITATION_CONTENT_TYPES": _plain,
    "PARTICLE_CONFIG": _plain,
    # --- bare integer literals ------------------------------------------------
    "MAX_FIX_ROUNDS": _bare_int,
    "BATCH_AGENT_CAP": _bare_int,
    "MAX_CODEX_JOBS_PER_BATCH": _bare_int,
    # --- inside quotes, free prose -------------------------------------------
    "VERSE_POLICY_INSTRUCTION_BLOCK": _js_string_body,
    # --- outside quotes, supplies its own -------------------------------------
    "CODEX_COMPANION_PATH_JSON": _js_string_literal,
    "PLUGIN_ROOT": _js_string_literal,
}


# ---------------------------------------------------------------------------
# Fixture defaults.
#
# Plain resolved values, mirroring what an orchestrating session would already
# have read out of a real profile.yml by the time it instantiates a template.
# Substitution starts AFTER that resolution: nothing here parses YAML.
#
# A default is the value a test file gets when it has no opinion about the
# token. Every value below is legal for the gate it feeds (a schema-valid
# effort, a colon-free run id, a non-empty plugin root -- since #607 the
# mass-translate and glossary templates refuse an empty one), and the caps
# default generously so a file exercising something else never trips a
# preflight gate it did not mean to exercise.
# ---------------------------------------------------------------------------

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260710T000000Z"
# Deliberately carries a space and a non-ASCII character -- both LEGITIMATE per
# resolve_codex_companion.py, which rejects only a quote / control char /
# newline -- so the `_js_string_literal` substitution is exercised on a value
# that would break a naive splice.
FIXTURE_COMPANION_PATH = "/Users/José García/codex/1.0.10/codex-companion.mjs"
FIXTURE_VERSE_POLICY = "Render every verse literally, line by line."
FIXTURE_PLUGIN_ROOT = "/fixture/plugin/literary-translator"
# The glossary template threads PLUGIN_ROOT into `--merge-batches`, whose guard
# resolves a real cache_key.py underneath it, so this one points at the plugin's
# OWN skill root rather than a fixture path.
FIXTURE_GLOSSARY_PLUGIN_ROOT = str(PLUGIN_ROOT / "skills" / "literary-translator")

MASS_TRANSLATE_DEFAULTS: dict[str, object] = {
    "DURABLE_ROOT": FIXTURE_DURABLE_ROOT,
    # CONTRACT-1.2.0-reliability.md sec2. Resolved ONCE by the orchestrating
    # session (a fresh id, or the identical value reused via `resumeFromRunId`
    # on a matched-digest resumed run). Colon-free and allowlist-legal
    # (`^[A-Za-z0-9][A-Za-z0-9._-]*$`); the allowlist validation itself is
    # upstream orchestrator logic, not part of the substitution step.
    "RUN_ID": FIXTURE_RUN_ID,
    "SOURCE_LANG": "fr",
    "TARGET_LANG": "ru",
    "MAX_FIX_ROUNDS": 3,
    "BATCH_AGENT_CAP": 100_000,
    # #409 stage 0 -- a SECOND, independent preflight cap sized against real
    # codex dispatches rather than Workflow agent() calls. Unlike
    # BATCH_AGENT_CAP, engine.max_codex_jobs_per_batch is OPTIONAL in
    # profile.schema.json; the token itself is still always substituted (with
    # the schema's own documented default when the profile omits it).
    "MAX_CODEX_JOBS_PER_BATCH": 100_000,
    "VERSE_POLICY_INSTRUCTION_BLOCK": FIXTURE_VERSE_POLICY,
    # #198 -- the resolved codex-companion.mjs path.
    "CODEX_COMPANION_PATH_JSON": FIXTURE_COMPANION_PATH,
    # #197 -- engine.effort/engine.model. EFFORT drives codex_job.py's
    # --effort flag plus the Claude fix step's agent() effort option; MODEL
    # threads only to the two codex_job.py launches, and is the empty string
    # when engine.model is unset.
    "EFFORT": "high",
    "MODEL": "",
    # #412 -- the plugin's own install root, threaded to codex_job.py's
    # --plugin-root flag on both dispatch launches. Empty USED to be the
    # documented "not opted into the redirect" sentinel; since #607 the
    # template refuses an empty one before dispatch, because the fix-scope
    # audit has no trusted copy to run without a plugin root. Files that
    # only slice out declarations and never reach that refusal still pass
    # `plugin_root=""` deliberately.
    "PLUGIN_ROOT": FIXTURE_PLUGIN_ROOT,
}

GLOSSARY_PASS_DEFAULTS: dict[str, object] = {
    "DURABLE_ROOT": FIXTURE_DURABLE_ROOT,
    "RUN_ID": FIXTURE_RUN_ID,
    # The glossary pass interpolates the language into prose the adjudicator
    # reads, so its own fixtures spell the language out rather than using the
    # two-letter profile code the mass-translate template carries.
    "SOURCE_LANG": "French",
    "TARGET_LANG": "Russian",
    # Passed through literally -- "this script never parses YAML itself".
    "RESEARCH_MODE": "live",
    # The SAME engine.batch_agent_cap field the mass template reads, feeding
    # the glossary preflight cost cap.
    "BATCH_AGENT_CAP": 10_000,
    # #197 -- engine.effort. There is deliberately no MODEL token here: the
    # glossary pass has no model knob (pinned in mass-translate-wf's header).
    "EFFORT": "high",
    # #347/1.16.1 -- glossary.citation_content_types, comma-separated. Empty =
    # fetch_citation.py's shipped default list, which is the ordinary case and
    # is why every pre-1.16.1 profile keeps working. The token is still always
    # SUBSTITUTED; it is never optional.
    "CITATION_CONTENT_TYPES": "",
    # #412 -- threaded ONLY into mergeBatchesPrompt()'s --merge-batches command
    # (never checkBatchCmd() or glossaryVerifyPrompt(): canon_validate.py's
    # main() does not forward --plugin-root to run_check_batch or
    # run_verify_merged). UNLIKE mass-translate-wf's own PLUGIN_ROOT, an empty
    # string was NEVER a valid opt-out here -- this token arrived with no
    # legacy caller relying on a flagless --merge-batches default to preserve,
    # so the template throws at instantiation for a blank value. The default
    # below is therefore a real, non-empty path whose --allow-durable-sibling
    # guard resolves a genuine cache_key.py underneath it.
    "PLUGIN_ROOT": FIXTURE_GLOSSARY_PLUGIN_ROOT,
}

SKEPTIC_PASS_DEFAULTS: dict[str, object] = {
    "DURABLE_ROOT": FIXTURE_DURABLE_ROOT,
    "RUN_ID": FIXTURE_RUN_ID,
    "SOURCE_LANG": "he",
    # An absolute path to a particle-config JSON. The skeptic pass only forwards
    # it to the scripts it launches, so a fixture path resolves fine; files that
    # actually exercise particle handling write a real one and override this.
    "PARTICLE_CONFIG": "/fixture/project/durable_root/lang/he/particles.json",
    "BATCH_AGENT_CAP": 100_000,
}

# The single authority: which shipped template takes which token set. The
# authority test in workflow_template_instantiation.test.py asserts this covers
# exactly the *.template.js files on disk, so a FOURTH shipped template cannot
# appear with no declared token set.
TEMPLATE_DEFAULTS: dict[Path, dict[str, object]] = {
    MASS_TRANSLATE_TEMPLATE: MASS_TRANSLATE_DEFAULTS,
    GLOSSARY_PASS_TEMPLATE: GLOSSARY_PASS_DEFAULTS,
    SKEPTIC_PASS_TEMPLATE: SKEPTIC_PASS_DEFAULTS,
}


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------

def instantiate(template: Path, *, source: str | None = None, **overrides: object) -> str:
    """Render `template` with its declared defaults, overridden per keyword.

    Keywords are the LOWERCASED token names (`max_fix_rounds=2`). An unknown
    keyword raises `TypeError` naming what is accepted -- a typo'd override
    must never silently no-op, because a substitution that quietly keeps the
    default is the exact failure #413 exists to close.

    `source`, when given, replaces the on-disk read. That is how a
    mutation-proof test drives a MUTATED template string, or a pre-fix baseline
    read out of git's object store, through the real control flow; it is also
    how the harnesses that slice the template before executing it feed their
    own slice in. Mutating the file ON DISK is deliberately never done -- this
    worktree is shared with concurrently running teammates, and an on-disk
    mutation would corrupt whatever suite they are running at that moment.
    """
    defaults = TEMPLATE_DEFAULTS.get(template)
    if defaults is None:
        raise KeyError(
            f"{template} has no declared token set in TEMPLATE_DEFAULTS -- add one "
            f"(known: {sorted(p.name for p in TEMPLATE_DEFAULTS)})"
        )

    by_keyword = {name.lower(): name for name in defaults}
    unknown = sorted(set(overrides) - set(by_keyword))
    if unknown:
        raise TypeError(
            f"instantiate({template.name}) got unexpected keyword argument(s) "
            f"{unknown}; accepted: {sorted(by_keyword)}"
        )

    values = dict(defaults)
    for keyword, value in overrides.items():
        values[by_keyword[keyword]] = value

    text = template.read_text(encoding="utf-8") if source is None else source
    for name, value in values.items():
        text = text.replace("{{%s}}" % name, ENCODERS[name](value))

    # `raise`, not `assert`: this module is imported by every workflow-driving
    # test file, and an `assert` here would vanish under `python -O`, turning
    # the one check that catches an undeclared token into a no-op exactly when
    # nobody is watching.
    leftover = TOKEN_RE.search(text)
    if leftover is not None:
        raise AssertionError(
            f"{template.name} instantiation left {leftover.group(0)!r} unresolved -- "
            f"add it to this module's defaults map, not to a per-file substitution list"
        )
    if "{{" in text:
        raise AssertionError(
            f"{template.name} instantiation left a literal '{{{{' behind -- the "
            f"template grew a token shape TOKEN_RE does not match"
        )
    return text


def instantiate_mass_translate(*, source: str | None = None, **overrides: object) -> str:
    """`mass-translate-wf.template.js` with MASS_TRANSLATE_DEFAULTS."""
    return instantiate(MASS_TRANSLATE_TEMPLATE, source=source, **overrides)


def instantiate_glossary_pass(*, source: str | None = None, **overrides: object) -> str:
    """`glossary-pass-wf.template.js` with GLOSSARY_PASS_DEFAULTS."""
    return instantiate(GLOSSARY_PASS_TEMPLATE, source=source, **overrides)


def instantiate_skeptic_pass(*, source: str | None = None, **overrides: object) -> str:
    """`skeptic-pass-wf.template.js` with SKEPTIC_PASS_DEFAULTS."""
    return instantiate(SKEPTIC_PASS_TEMPLATE, source=source, **overrides)
