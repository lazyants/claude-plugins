#!/usr/bin/env python3
"""profile_validate.py -- Step 0: read + validate ``profile.yml``.

Authoritative spec: SKILL.md's "Step 0 -- Read + validate profile.yml"
section, cross-checked against ``assets/schemas/profile.schema.json`` and
``assets/profile.example.yml``. Read those before changing anything here.

**ONE OF THREE PLUGIN-PATH-ONLY SCRIPTS NEVER COPIED TO ``durable_root``** (the
other two are ``validate_extraction.py``, the W2 post-extraction gate, and
``glossary_preflight.py``, the W3 glossary pre-dispatch staleness gate --
``scaffold_setup.py`` is ALSO never copied, but for a wholly unrelated reason,
in a different category from these three: it is Step 0a's own bundle-hash
marker writer, deliberately not a bundle member at all; see SKILL.md's Step 0a
copy-exclusion list for both categories). Every *other* script in this plugin
gets physically copied to ``${durable_root}/scripts/`` by Step 0a and
self-anchors relative to ITS OWN location under durable_root. This script is
never copied for a specific reason: it runs *before* Step 0a exists to do that
copying -- there is no durable-root copy of it yet, and there never will be
one. (``validate_extraction.py`` is never copied for a *different* reason -- it
is kept plugin-only so a hand-edited extractor cannot bypass it; see
``references/false-green-gate.md``. ``glossary_preflight.py`` is never copied
for a *third* reason -- a copied durable instance would resolve its own schemas
from the durable root and compare durable-vs-durable, a vacuous pass that can
never detect staleness; see SKILL.md's Step 0a copy-exclusion list.
``resolve_codex_companion.py`` used to be a *fourth* such exception, on the
claimed reason that it must glob the plugin's own install locations to find
the newest installed ``codex-companion.mjs``, which a durable-root copy could
not do. That reason was false -- the script never READS ``__file__`` (pinned by
``tests/resolve_codex_companion.test.py``'s
``test_the_resolver_contains_no_executable_reference_to_dunder_file``, which
parses it rather than grepping it; the file's own docstring mentions the name
in prose, so a text search over it is not the check)
and its DEFAULT search is rooted at the RUNNING Claude config profile
(``$CLAUDE_CONFIG_DIR``, else ``~/.claude``) and then at ``~/.claude*/plugins/
cache/openai-codex/**``, a different plugin's own install cache, found
identically regardless of where ``resolve_codex_companion.py`` itself runs from
(the search root is an ENVIRONMENT fact, never the script's own location) -- so it
is copied like every other self-anchored script now; see SKILL.md's Step 0a
copy-exclusion list for the full disproof.) This script
is always invoked directly from the plugin's own install path:

    python3 {{PLUGIN_ROOT}}/assets/scripts/profile_validate.py \\
        --profile .claude/literary-translator/profile.yml

...run by the orchestrating Claude session itself, not by a generated
workflow script. For the exact same reason, it loads
``assets/profile.example.yml`` and ``assets/schemas/profile.schema.json``
straight out of the plugin's own ``assets/`` directory (self-anchored via
``SCRIPTS_DIR.parent`` -- one level up from this script's own
``assets/scripts/`` directory, giving ``assets/``) rather than a
durable-root copy of either.

Order of operations (numbered to match SKILL.md's Step 0 list exactly):

  1. Existence check FIRST. If the profile path is absent, copy the shipped
     ``assets/profile.example.yml`` there verbatim and then CONTINUE into
     this same run's steps 2-5, so the invocation that creates the starter
     profile is also the one that prints the intake questionnaire (#751) --
     an existing, filled-in profile is never touched again (checked fresh
     every run). This branch used to halt immediately, telling the reader to
     fill the placeholders in and re-run; that left a window in which a
     sentinel-laden profile existed and the questions had not been relayed,
     and an orchestrator that filled the fresh copy from its own inline
     comments closed that window by answering every intake decision itself.
  2. Dependency preflight: ``import yaml`` and ``import jsonschema``, each in
     its own try/except, with an actionable ``pip install`` message naming
     the missing package.
  3. Parse YAML via ``yaml.safe_load`` (never ``yaml.load``). Reject a
     non-mapping document. Check ``profile_version`` against a hardcoded
     current-version constant, with a migration hint on mismatch.
  4. Unknown top-level keys are FATAL by default, naming the exact key --
     except keys under the reserved ``x_*`` namespace (forward-compat
     extension point), which are silently allowed.
  5. Whole-profile placeholder-substring scan, run BEFORE schema validation
     (#727): every string value anywhere in the parsed document, not a named
     subset of fields, is checked against every literal placeholder
     ``assets/profile.example.yml`` ships (PLACEHOLDER_SUBSTRINGS below) and
     every ``CHOOSE_*``-prefixed sentinel -- FATAL, halting right here.
     Running this before jsonschema (not after, as it used to) matters: a
     ``CHOOSE_*`` sentinel left in an inactive adapter sub-block (e.g.
     ``plain_text.verse_detection`` while ``source.format: gutenberg_epub``
     is active) is validated for basic shape only by the schema, so
     jsonschema alone never reports it -- only this scan does, and it must
     run before jsonschema would otherwise halt the whole run on some
     unrelated, unconditionally-required field's own sentinel (e.g.
     ``glossary.research_mode``) and hide every other still-open intake
     decision from the operator in the same breath. Each surviving
     ``CHOOSE_*`` sentinel's error line is enriched with a plain-language
     question (``KNOB_QUESTIONS``, keyed by dotted path) naming what the
     choice costs, when that path is a known knob; a one-line header
     precedes the whole group of sentinel errors, once, only when at least
     one sentinel survived.
  6. Validate whole-file shape via
     ``jsonschema.Draft202012Validator(profile.schema.json,
     format_checker=jsonschema.FormatChecker())``.
  7. Only once schema validation passes, run the procedural checks a schema
     alone cannot express: ``source.path`` existence; ``project.durable_root``'s
     PARENT existence/writability/not-under-tmp-or-scratchpad;
     ``output.destination``'s parent, checked only when it resolves OUTSIDE
     durable_root. (``source.language.particle_config``'s file existence is
     deliberately NOT checked here -- deferred to the end of Step 0a, since
     the preset hasn't been copied into the project yet on a fresh project.)
  8. ``adapter_config.plain_text.segmentation.heading_regex``: compilability
     check (``re.compile`` in try/except) whenever ``method: heading_regex``
     is the active segmentation method -- FATAL on ``re.error``. Non-fatal
     cross-field WARNING when the *unselected* method's own sibling field is
     still non-null (dead configuration left lying around).
  9. ``source.format: custom`` selected -> non-fatal WARNING naming it
     experimental/unpiloted, pointing at
     ``references/source-format-adapters/custom.md``.
  10. ``source.language.particle_config``: FATAL, field-named rejection of
      any value containing a forward slash, a backslash, a ``..`` segment, or
      an absolute-path prefix -- checked BEFORE any path-join is attempted.
  11. ``source.language.smoke_test.report_path``: FATAL rejection of any
      value containing the literal substring ``..`` anywhere -- checked
      BEFORE any path-join is attempted.
  12. On a RESUMED project: ``translate_TASK.md`` / ``review_TASK.md`` /
      ``glossary_TASK.md`` under durable_root, if they exist, each get their
      leading ``<!-- PROMPT_CONTRACT_VERSION: N -->`` HTML-comment marker
      checked against a hardcoded ``CURRENT_PROMPT_CONTRACT_VERSION``
      constant. Four separately-named fatal states: missing marker (treated
      as version 0, therefore always stale), a malformed non-integer value, a
      duplicated marker with two conflicting values, and a marker present but
      not on the file's first non-blank line -- plus the ordinary stale-vs-
      current version mismatch once the marker itself is well-formed.
  13. Same four-state (plus mismatch) check for ``extract.py`` under
      durable_root, if it exists, against its own leading
      ``# EXTRACTOR_CONTRACT_VERSION: N`` **Python comment** (not an
      HTML comment -- this file must stay valid, importable Python), compared
      against a hardcoded ``CURRENT_EXTRACTOR_CONTRACT_VERSION`` constant.
      **Skipped when ``source.format`` is ``custom``**: Step 0a still copies
      ``extract.py.template`` to ``extract.py`` unconditionally, but for a
      custom source that copy is never adapted or run -- the real extractor
      lives at ``scripts/custom_extractors/<value>`` -- so drift against this
      constant is meaningless there (see
      ``references/source-format-adapters/custom.md``).
  14. ``output.target``: FATAL when it names a BUILT-IN adapter whose module
      has not shipped (``epub`` -> ``render_epub.py``, which does not exist),
      delegated to ``output_resolve.assert_builtin_adapter_shipped()`` so the
      mapping and the actionable message have exactly one home. **Gated on
      ``output.v1_scope == "assembled_book"``**: under the default
      ``segment_drafts_and_audit`` the target is never consulted by anything,
      and refusing an inert value would reject profiles that work today.
      ``output.target`` is an OPTIONAL field (``output``'s own ``required``
      list is ``v1_scope`` + ``destination``), so it is read with ``.get`` --
      an absent target is not this check's business, and stays a Step 0d
      condition. Runs at Step 0, i.e. before Step 0a has created a durable
      root, which is the whole point: the same failure otherwise surfaces at
      W9 assembly with the book already translated and converged (#726).
  15. NEW (#727): ``glossary.enabled`` exactly ``False`` together with
      ``glossary.skeptic_pass.enabled`` exactly ``True`` is FATAL, naming
      both fields -- they state two different intentions (the skeptic pass
      has nothing to audit once the glossary pass itself is skipped; see
      SKILL.md's W3 third branch). Tests ``is False``/``is True``
      explicitly, never a falsy ``.get()``: an ABSENT ``glossary.enabled``
      means ``true`` (the schema's own default), so every profile written
      before this key existed -- including one with
      ``skeptic_pass.enabled: true`` -- must keep validating unchanged.

Every violation is printed as its own field-named, actionable line. The
script exits non-zero if ANY fatal violation was found (across every step
above -- this is a "collect everything, then report everything" validator,
not a stop-at-the-first-error one), 0 if clean (warnings alone do not fail
the run).

Exit codes: 0 = clean (see stdout for any warnings); 1 = one or more fatal
validation failures (see stderr); 2 = usage or environment error (bad CLI
args, missing dependency).
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-anchoring: this script is one of THREE deliberate exceptions to "every
# script lives under ${durable_root}/scripts/ and self-anchors via
# Path(__file__).resolve().parents[1]" (the other two are validate_extraction.py
# and glossary_preflight.py -- resolve_codex_companion.py used to be a fourth
# exception here too, on a reason since found false; it is copied like every
# other self-anchored script now, see this file's own module docstring) -- it
# lives at the PLUGIN'S OWN ``assets/scripts/`` directory and is never copied to
# durable_root, so ``SCRIPTS_DIR.parent`` gives the plugin's ``assets/`` root
# instead of a durable_root (the convention names it ``parents[1]``; this file
# spells the same value in two named steps because SCRIPTS_DIR is itself needed
# below, for the flat sibling import).
# It never assumes cwd and never takes a --plugin-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
ASSETS_ROOT = SCRIPTS_DIR.parent
EXAMPLE_PROFILE_PATH = ASSETS_ROOT / "profile.example.yml"
SCHEMA_PATH = ASSETS_ROOT / "schemas" / "profile.schema.json"

# The ONE sibling script this one imports (flat, by name, the same shape
# assemble.py and diff_rendered_output.py already use for it): output_resolve.py
# owns the output.target -> adapter-module mapping AND the actionable message
# for a built-in adapter that has not shipped. Duplicating either here would be
# a silent drift surface with nothing watching it. Imported at module load
# rather than inside the check because output_resolve.py's module level is
# stdlib-only -- no PyYAML, no jsonschema, no sibling import of its own -- so it
# cannot disturb step 1's "profile absent" branch or step 2's dependency
# preflight, both of which must still work with neither package installed --
# step 1 still COPIES the example with neither installed; since #751 it then
# falls through into the preflight rather than exiting, so a missing package
# is reported there instead of one run later.
sys.path.insert(0, str(SCRIPTS_DIR))
import output_resolve  # noqa: E402  -- must follow the sys.path insert above

# Deferred dependency handles -- populated by _dependency_preflight(), never
# imported at module load time, so that step 1's COPY of the shipped example
# happens with neither package installed and the operator ends up holding a
# real starter profile plus an actionable install message (see step 1 above).
# Since #751 that branch no longer exits before the preflight, so the install
# message is what a dependency-less run prints INSTEAD of the questionnaire --
# the copy has still been made either way.
yaml = None
jsonschema = None

# --- Hardcoded version constants -------------------------------------------
# Bump CURRENT_PROFILE_VERSION only in lockstep with profile.schema.json's
# own `profile_version: {"const": N}`. Bump CURRENT_PROMPT_CONTRACT_VERSION
# whenever translate_TASK.template.md / review_TASK.template.md /
# glossary_TASK.template.md's prompt CONTRACT (required fields, role
# boundaries) changes in a way that makes an old, hand-adapted copy stale.
# Bump CURRENT_EXTRACTOR_CONTRACT_VERSION whenever extract.py.template's
# OUTPUT CONTRACT (manifest.json shape) changes in a way that makes an old,
# hand-adapted copy stale. All three are plugin-build constants, never
# profile.yml fields.
CURRENT_PROFILE_VERSION = 1
CURRENT_PROMPT_CONTRACT_VERSION = 3
CURRENT_EXTRACTOR_CONTRACT_VERSION = 2

# The exact top-level keys profile.schema.json's own `required` list names --
# kept as a plain constant here (rather than re-derived from the schema at
# runtime) so the "unknown top-level key" check (step 4) can run, with a
# friendly field-named message, BEFORE the heavier jsonschema pass (step 6).
KNOWN_TOP_LEVEL_KEYS = frozenset({
    "profile_version",
    "project",
    "source",
    "target",
    "verse_policy",
    "engine",
    "footnotes",
    "glossary",
    "validation",
    "output",
})
RESERVED_KEY_PREFIX = "x_"

# Every literal placeholder string assets/profile.example.yml ships,
# transcribed verbatim from that file (read it directly, don't re-derive).
# Substring match -- "/ABS/PATH/TO/YOUR_SOURCE.epub" still contains
# "/ABS/PATH/TO/YOUR_SOURCE" -- which is also why #874's register token is
# bracketed: no register note a project writes can contain it by accident.
PLACEHOLDER_SUBSTRINGS = (
    "YOUR BOOK TITLE HERE",
    "/ABS/PATH/TO/YOUR_PROJECT",
    "/ABS/PATH/TO/YOUR_SOURCE",
    "[TODO: target-language register]",
)
# The example ships several separate CHOOSE_-prefixed sentinels. Read WHICH,
# and how many, off assets/profile.example.yml itself (or off KNOB_QUESTIONS
# below, which a two-way drift guard holds equal to it) -- a name list and a
# count restated here have now gone stale twice, once per release that added
# one. Rather than enumerate them at all, any string value starting with this
# prefix anywhere in the document is rejected, so a sentinel added tomorrow is
# caught with no edit here.
CHOOSE_PREFIX = "CHOOSE_"

# #727. One plain-language question per intake knob that ships a CHOOSE_
# sentinel in assets/profile.example.yml, keyed by the exact dotted path
# _walk_strings() yields for that field. Appended to that field's sentinel
# error line (see _scan_choose_sentinels()) so the operator sees what the
# choice actually costs, not just that a value is missing. A dotted path
# with NO entry here still errors -- it just loses the question, never the
# error itself -- so a future sentinel added to the example without a
# matching entry here fails loudly (see the drift-guard test asserting set
# equality both directions between this mapping's keys and the example's own
# shipped CHOOSE_ paths) rather than silently shipping a mute questionnaire
# line.
KNOB_QUESTIONS = {
    "glossary.enabled": (
        "Does this project want a researched name/realia canon at all? "
        "`true` runs the W3 glossary pass -- name research plus "
        "per-candidate adjudication, one of the most expensive stages in "
        "this pipeline. `false` skips that pass and translates against an "
        "EMPTY canon: each segment's translator then chooses its own "
        "rendering for every detected name and records it as `NEW:`, so "
        "cross-segment name consistency is no longer frozen up front and "
        "shows up only as an advisory warning in the final audit. Enabling "
        "it later can re-stale and re-dispatch already-translated segments "
        "once names get canonized. An existing `canon.json` is never "
        "discarded: `false` keeps injecting the entries it already holds."
    ),
    "glossary.research_mode": (
        "Does the glossary-pass agent have real web access on this run? "
        "`live` admits cited `basis:\"established\"` entries and pays "
        "research round-trips; `offline` forbids them outright. Inert when "
        "`glossary.enabled` is false, but still required."
    ),
    "footnotes.apparatus_policy": (
        "What happens to the source's footnote apparatus: `translate_all` "
        "(translate every note -- the most expensive), `preserve_source` "
        "(keep the source-language notes), `omit_apparatus` (drop them), or "
        "`body_refs_only` (keep only the in-body reference marks)."
    ),
    "output.v1_scope": (
        "What v1 delivers: `segment_drafts_and_audit` (converged, audited "
        "per-segment drafts -- the lean default) or `assembled_book` (also "
        "runs the whole assembly/render stage)."
    ),
    "output.target": (
        "How an assembled book is rendered: `obsidian` (a wiki vault), "
        "`epub`, or `custom` (a renderer you co-design). Consulted only "
        "under `v1_scope: assembled_book`, but it decides how much output "
        "apparatus gets provisioned. `epub` has no renderer yet, so "
        "pairing it with `assembled_book` is refused at Step 0 itself; "
        "`custom` requires co-designing a renderer first."
    ),
    # #730. The six-value enum reaches the user as a real question, with what
    # each mode costs, instead of being filled in from the orchestrator's own
    # reading of the source -- which is how one series spent four review rounds
    # on volume 2 rediscovering a decision its user had already made on volume 1.
    "verse_policy.mode": (
        "How is verse translated in this book? `full_rhymed_plus_literal` "
        "requires a full rhymed rendering AND a mandatory literal gloss on "
        "every verse; `full_rhymed_only` requires the rhymed rendering with no "
        "literal safety net beside it; `rhythmic_approximation` keeps a "
        "metrical line but does not require end-rhyme; `mixed_by_length` "
        "applies the rhymed-plus-literal policy at or above "
        "`verse_policy.threshold_lines` and the rhythmic one below it, and is "
        "the one mode that REQUIRES that threshold; `literal_only` asks for a "
        "faithful prose gloss only, no rhyme and no meter; `skip` leaves verse "
        "untranslated and passed through. This is the field that decides what "
        "a review is ALLOWED TO FAIL A SEGMENT ON, so a rhyme-requiring mode "
        "against a source whose sense is dense makes rhyme and accuracy "
        "collide at exactly the passages that matter most. Answer it now "
        "rather than later: the mode is hashed into `profile_semantics_hash`, "
        "a GLOBAL cache-key field, so changing it once work has started "
        "RESTALES EVERY HEALTHY ALREADY-CONVERGED SEGMENT in the volume, "
        "prose-only segments included; `select_segments.py` then REFUSES that "
        "re-dispatch until you pass `--allow-retranslate-converged`; and "
        "authorizing it mints a fresh RUN_ID, which ALSO ORPHANS EVERY "
        "NOT-YET-CONVERGED DRAFT in the same selection -- since #742 the "
        "driver REFUSES the dispatch over those by name rather than "
        "retranslating them over fixes already applied by hand, so answering "
        "this late costs you a halt per orphaned draft."
    ),
    "source.adapter_config.plain_text.verse_detection": (
        "How the `plain_text` adapter finds verse: `none_confirmed` (you "
        "have confirmed the source has none) or `regex` (you supply the "
        "pattern). Consulted only when `source.format: plain_text`, but it "
        "must still be answered."
    ),
    "source.adapter_config.plain_text.footnotes": (
        "How the `plain_text` adapter finds footnotes: `none_confirmed`, "
        "`markdown_ref`, or `custom_regex`. Consulted only when "
        "`source.format: plain_text`, but it must still be answered."
    ),
}

TMP_OR_SCRATCHPAD_MARKERS = frozenset({"tmp", "temp", "scratchpad"})
# Narrow, default-off override for check_durable_root()'s tmp/scratchpad
# rejection -- see that function's docstring. Set to "1" to accept a
# durable_root that resolves under a tmp/temp/scratchpad path component;
# any other value (including unset) leaves the rejection in force.
ALLOW_TMP_ROOT_ENV_VAR = "LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT"

PROMPT_CONTRACT_MARKER_RE = re.compile(r"^\s*<!--\s*PROMPT_CONTRACT_VERSION:\s*(.+?)\s*-->\s*$")
EXTRACTOR_CONTRACT_MARKER_RE = re.compile(r"^\s*#\s*EXTRACTOR_CONTRACT_VERSION:\s*(.+?)\s*$")

RESUMED_PROMPT_CONTRACT_FILENAMES = (
    "translate_TASK.md",
    "review_TASK.md",
    "glossary_TASK.md",
)


# ---------------------------------------------------------------------------
# Step 1/2: existence check + dependency preflight
# ---------------------------------------------------------------------------

def ensure_profile_exists(profile_path: Path) -> bool:
    """Step 1. Returns True if `profile_path` already existed. If absent,
    copies the shipped example there and returns False -- checked fresh on
    every invocation, so an existing, filled-in profile is NEVER touched
    again.

    The return value says which of the two happened, and NOT whether the
    caller should stop: since #751 both branches go on to run steps 2-5, so
    that the run which creates a starter profile is also the run that prints
    its intake questionnaire. Callers use the False case only to print the
    creation notice."""
    if profile_path.exists():
        return True
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_PROFILE_PATH, profile_path)
    return False


def _find_requirements_txt(max_up: int = 6):
    """Best-effort resolution of the plugin's own requirements.txt for an
    actionable pip-install message -- walks up from this script's own
    location (never assumes a fixed depth) rather than hardcoding a
    {{PLUGIN_ROOT}}-style path that may not match this install layout."""
    here = Path(__file__).resolve()
    for ancestor in list(here.parents)[:max_up]:
        candidate = ancestor / "requirements.txt"
        if candidate.is_file():
            return candidate
    return None


def _missing_dependency_message(package_name: str) -> str:
    req_path = _find_requirements_txt()
    where = str(req_path) if req_path else (
        "requirements.txt (see the literary-translator plugin's own root directory)"
    )
    return (
        f"ERROR: this plugin requires the {package_name!r} Python package. "
        f"Install with: pip install -r {where}"
    )


def dependency_preflight():
    """Step 2. Wraps `import yaml` and `import jsonschema` each in their own
    try/except, printing an actionable, package-named message and exiting
    non-zero on ImportError. Populates the module-level `yaml`/`jsonschema`
    names on success."""
    global yaml, jsonschema
    try:
        import yaml as _yaml
    except ImportError:
        print(_missing_dependency_message("PyYAML"), file=sys.stderr)
        sys.exit(2)
    try:
        import jsonschema as _jsonschema
    except ImportError:
        print(_missing_dependency_message("jsonschema"), file=sys.stderr)
        sys.exit(2)
    yaml = _yaml
    jsonschema = _jsonschema


# ---------------------------------------------------------------------------
# Step 3/4: parse + profile_version + unknown-top-level-key checks
# ---------------------------------------------------------------------------

def parse_profile_yaml(profile_path: Path):
    """Step 3 (parse half). Returns the parsed mapping, or halts (exit 1)
    naming the parse problem."""
    assert yaml is not None, (
        "dependency_preflight() must run before parse_profile_yaml() -- "
        "the yaml module is not yet loaded"
    )
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read {profile_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"ERROR: {profile_path} is not valid YAML: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(profile, dict):
        print(
            f"ERROR: {profile_path} did not parse to a mapping "
            f"(got {type(profile).__name__}) -- profile.yml's top level must "
            f"be a YAML mapping of the documented keys.",
            file=sys.stderr,
        )
        sys.exit(1)
    return profile


def check_profile_version(profile: dict):
    """Step 3 (version half). A dedicated, friendlier-messaged check that
    runs BEFORE the heavier jsonschema pass -- an unknown/missing
    profile_version gets a migration hint, not a generic schema error."""
    version = profile.get("profile_version")
    if version != CURRENT_PROFILE_VERSION:
        return [
            f"profile_version: {version!r} is not a version this plugin build "
            f"understands (expected {CURRENT_PROFILE_VERSION}). If this profile "
            f"predates a plugin upgrade, see CHANGELOG.md for migration notes; "
            f"otherwise start from a fresh assets/profile.example.yml and "
            f"re-apply your values."
        ]
    return []


def check_unknown_top_level_keys(profile: dict):
    """Step 4. Unknown top-level keys are FATAL by default, naming the exact
    key -- except the reserved `x_*` forward-compat namespace."""
    errors = []
    for key in profile:
        if key in KNOWN_TOP_LEVEL_KEYS:
            continue
        if isinstance(key, str) and key.startswith(RESERVED_KEY_PREFIX):
            continue
        errors.append(
            f"unknown top-level key {key!r} -- not part of profile.schema.json, "
            f"and not under the reserved 'x_' forward-compat namespace. Remove "
            f"it, or rename it with an 'x_' prefix if it's a deliberate "
            f"project-local extension."
        )
    return errors


# ---------------------------------------------------------------------------
# Step 5: whole-profile placeholder-substring scan (#727: moved here, ahead
# of jsonschema -- see this file's own module docstring for the fuller "why")
# ---------------------------------------------------------------------------

def _walk_strings(obj, path="", _stack=None):
    """Yields (dotted_path, string_value) for every string leaf anywhere in a
    parsed YAML/JSON-like structure (dicts, lists, scalars).

    #727: tracks the CURRENT RECURSION STACK, not a whole-walk visited set --
    a container's `id()` is added right before descending into it and
    removed right after (on unwind, via try/finally), so it is only
    "on stack" while one of ITS OWN ancestors-to-descendants chains is being
    walked. That distinguishes a true CYCLE (a container that is its own
    ancestor -- e.g. `title: &loop [*loop]`) from a shared, non-cyclic YAML
    alias reached via two sibling paths (e.g. `x_alias: &g {...}` /
    `glossary: *g`): the alias's `id()` is off the stack again by the time
    the second, sibling path reaches it, so it is walked -- and every
    dotted path it legitimately occurs at gets reported, each with its own
    CHOOSE_ sentinel question intact. An earlier version of this guard used
    a whole-walk set instead, which over-collapsed the alias case: the
    second path to the same shared object was silently skipped even though
    it isn't a cycle, so that path's own sentinel lost its questionnaire
    line (the sentinel still failed the run -- nothing was silently
    accepted -- but the questionnaire degraded). This scan runs at step 5,
    before jsonschema's own shape check would otherwise have rejected a
    cyclic document on TYPE grounds alone; without the stack guard, a true
    cycle recurses forever and dies with a raw RecursionError traceback
    instead of the ordinary, field-named ERROR line every other malformed
    value gets."""
    if _stack is None:
        _stack = set()
    if isinstance(obj, (dict, list)):
        obj_id = id(obj)
        if obj_id in _stack:
            return
        _stack.add(obj_id)
        try:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    yield from _walk_strings(value, child_path, _stack)
            else:
                for index, value in enumerate(obj):
                    yield from _walk_strings(value, f"{path}[{index}]", _stack)
        finally:
            _stack.discard(obj_id)
    elif isinstance(obj, str):
        yield path, obj


def _scan_placeholder_substrings(profile: dict):
    """Half of step 5: every literal PLACEHOLDER_SUBSTRINGS hit, anywhere in
    the document. Message text unchanged by #727 -- only the CHOOSE_-sentinel
    half (`_scan_choose_sentinels()` below) gained a question."""
    errors = []
    for location, value in _walk_strings(profile):
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in value:
                errors.append(
                    f"{location}: still contains the unreplaced placeholder "
                    f"{placeholder!r} (current value: {value!r}) -- copy the "
                    f"shipped assets/profile.example.yml's comment for this "
                    f"field and replace it with a real value."
                )
    return errors


def _scan_choose_sentinels(profile: dict):
    """The other half of step 5: every surviving CHOOSE_-prefixed enum
    sentinel. #727 enriches the message with KNOB_QUESTIONS' plain-language
    question when the dotted path is a known knob, appended after the
    original message -- a path with NO entry keeps today's message
    unchanged (a future sentinel loses its question, never its error)."""
    errors = []
    for location, value in _walk_strings(profile):
        if value.startswith(CHOOSE_PREFIX):
            message = (
                f"{location}: still has the shipped placeholder sentinel "
                f"{value!r} -- consciously choose one of its documented "
                f"real values before proceeding."
            )
            question = KNOB_QUESTIONS.get(location)
            if question:
                message = f"{message} {question}"
            errors.append(message)
    return errors


def scan_placeholders(profile: dict):
    """Step 5. Scans EVERY field, not a named subset -- FATAL if any value
    anywhere still contains a shipped profile.example.yml placeholder
    substring, or is still exactly one of the CHOOSE_-prefixed enum
    sentinels. A flat, combined list -- callers that only care whether the
    profile is clean (`scan_placeholders(profile) == []`) don't need to know
    about the split below. main() itself calls `_scan_placeholder_substrings()`
    and `_scan_choose_sentinels()` directly rather than this wrapper, so it
    can print the intake-questionnaire header immediately before only the
    sentinel half (see main()'s own step 5 block)."""
    return _scan_placeholder_substrings(profile) + _scan_choose_sentinels(profile)


# ---------------------------------------------------------------------------
# Step 6: whole-file jsonschema validation
# ---------------------------------------------------------------------------

def load_profile_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_against_schema(profile: dict, schema: dict):
    assert jsonschema is not None, (
        "dependency_preflight() must run before validate_against_schema() -- "
        "the jsonschema module is not yet loaded"
    )
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    errors = sorted(validator.iter_errors(profile), key=lambda e: [str(p) for p in e.path])
    formatted = []
    for e in errors:
        location = ".".join(str(p) for p in e.path) or "<root>"
        formatted.append(f"{location}: {e.message}")
    return formatted


# ---------------------------------------------------------------------------
# Step 7: procedural checks a schema alone cannot express
# ---------------------------------------------------------------------------

def check_source_path(profile: dict):
    raw = profile["source"]["path"]
    if not raw or not Path(raw).expanduser().exists():
        return [f"source.path: does not exist: {raw!r}"]
    return []


def _resolves_under_tmp_or_scratchpad(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any(part.lower() in TMP_OR_SCRATCHPAD_MARKERS for part in resolved.parts)


def check_durable_root(profile: dict):
    """`project.durable_root`'s PARENT must exist and be writable, and must
    NOT resolve under a tmp/scratchpad directory. durable_root itself is NOT
    required to exist yet -- Step 0a creates it.

    The tmp/scratchpad rejection alone (never the parent-exists/writable
    checks) is skipped when the `LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT`
    environment variable is exactly "1" -- a narrow, default-off override
    for ephemeral/CI/test environments that intentionally place
    durable_root under a tmp dir (e.g. pytest's tmp_path, which resolves
    under /tmp on Linux CI runners)."""
    errors = []
    raw = profile["project"]["durable_root"]
    durable_root = Path(raw).expanduser()

    allow_tmp_root = os.environ.get(ALLOW_TMP_ROOT_ENV_VAR) == "1"
    if not allow_tmp_root and _resolves_under_tmp_or_scratchpad(durable_root):
        errors.append(
            f"project.durable_root: must not resolve under a tmp/temp/"
            f"scratchpad directory (resolves to {durable_root.resolve()})"
        )

    parent = durable_root.parent
    if not parent.exists():
        errors.append(f"project.durable_root: parent directory does not exist: {parent}")
    elif not os.access(parent, os.W_OK):
        errors.append(f"project.durable_root: parent directory is not writable: {parent}")

    return errors


def check_output_destination(profile: dict):
    """`output.destination`'s parent is checked ONLY when it resolves
    OUTSIDE durable_root (the common default, inside durable_root, defers
    to Step 0a, which creates it)."""
    dest_raw = profile["output"]["destination"]
    durable_root_raw = profile["project"]["durable_root"]

    dest = Path(dest_raw).expanduser().resolve()
    durable_root = Path(durable_root_raw).expanduser().resolve()

    try:
        dest.relative_to(durable_root)
        return []  # inside durable_root -- Step 0a will create it
    except ValueError:
        pass

    errors = []
    parent = dest.parent
    if not parent.exists():
        errors.append(
            f"output.destination: parent directory does not exist: {parent} "
            f"(destination resolves outside durable_root, so Step 0a will "
            f"not auto-create it)"
        )
    elif not os.access(parent, os.W_OK):
        errors.append(f"output.destination: parent directory is not writable: {parent}")
    return errors


# ---------------------------------------------------------------------------
# Step 8: plain_text.segmentation.heading_regex compilability + cross-field
# warning
# ---------------------------------------------------------------------------

def check_plain_text_segmentation(profile: dict):
    """Returns (fatal_errors, warnings)."""
    errors, warnings = [], []
    plain_text = profile["source"]["adapter_config"]["plain_text"]
    if not plain_text:
        return errors, warnings

    segmentation = plain_text.get("segmentation") or {}
    method = segmentation.get("method")
    heading_regex = segmentation.get("heading_regex")
    blank_line_threshold = segmentation.get("blank_line_threshold")

    if method == "heading_regex" and heading_regex:
        try:
            re.compile(heading_regex)
        except re.error as exc:
            errors.append(
                f"source.adapter_config.plain_text.segmentation.heading_regex: "
                f"does not compile as a regular expression ({exc}): {heading_regex!r}"
            )

    if method == "blank_line_run" and heading_regex is not None:
        warnings.append(
            "source.adapter_config.plain_text.segmentation.heading_regex is "
            "set but segmentation.method is 'blank_line_run' -- heading_regex "
            "is inert while this method is inactive; clear it to avoid "
            "confusion, or switch method if that was the intent."
        )
    elif method == "heading_regex" and blank_line_threshold is not None:
        warnings.append(
            "source.adapter_config.plain_text.segmentation.blank_line_threshold "
            "is set but segmentation.method is 'heading_regex' -- "
            "blank_line_threshold is inert while this method is inactive; "
            "clear it to avoid confusion, or switch method if that was the "
            "intent."
        )

    return errors, warnings


# ---------------------------------------------------------------------------
# Step 9: source.format: custom experimental warning
# ---------------------------------------------------------------------------

def check_custom_format_warning(profile: dict):
    if profile["source"]["format"] == "custom":
        return [
            "source.format: 'custom' is selected -- this adapter is "
            "experimental and not yet pilot-proven end-to-end; see "
            "references/source-format-adapters/custom.md before relying on "
            "it for a real project."
        ]
    return []


# ---------------------------------------------------------------------------
# Steps 10/11: path-traversal rejections
# ---------------------------------------------------------------------------

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _path_traversal_violation(value: str):
    """Returns a human-readable violation description, or None if `value` is
    a safe bare filename/relative fragment."""
    if "/" in value:
        return "must not contain a forward slash"
    if "\\" in value:
        return "must not contain a backslash"
    if ".." in value:
        return "must not contain a '..' path-traversal segment"
    if value.startswith(("/", "~")) or _WINDOWS_DRIVE_RE.match(value):
        return "must not be an absolute path"
    return None


def check_particle_config(profile: dict):
    """Step 10. Rejects (FATAL, field-named) any particle_config value
    containing a forward slash, a backslash, a '..' segment, or an
    absolute-path prefix -- BEFORE any path-join is attempted."""
    value = profile["source"]["language"]["particle_config"]
    if not isinstance(value, str):
        return []
    violation = _path_traversal_violation(value)
    if violation:
        return [
            f"source.language.particle_config: {violation} (got {value!r}) -- "
            f"this must be a bare filename, resolved as "
            f"${{durable_root}}/languages/<value>, never a path."
        ]
    return []


def check_smoke_test_report_path(profile: dict):
    """Step 11. Rejects (FATAL) any report_path value containing the literal
    substring '..' anywhere -- BEFORE any path-join is attempted."""
    value = profile["source"]["language"]["smoke_test"]["report_path"]
    # None is not a str -- one isinstance check covers both the "omitted" and
    # "wrong type" cases; wrong-type is left for the schema pass to name.
    if not isinstance(value, str):
        return []
    if ".." in value:
        return [
            f"source.language.smoke_test.report_path: must not contain a "
            f"'..' path-traversal segment anywhere (got {value!r})"
        ]
    return []


# ---------------------------------------------------------------------------
# Step 15 (NEW, #727): glossary.enabled vs glossary.skeptic_pass.enabled
# cross-field contradiction
# ---------------------------------------------------------------------------

def check_glossary_disabled_conflicts_with_skeptic_pass(profile: dict):
    """`glossary.enabled: false` and `glossary.skeptic_pass.enabled: true`
    state two different intentions -- the skeptic pass is an ADVISORY audit
    over the glossary research pass's own output (RFC #215 Phase 2), and
    there is nothing for it to audit once the glossary pass itself is
    skipped (SKILL.md's W3 third branch refuses to run the skeptic pass on
    the `glossary.enabled: false` branch no matter what this field says).

    Tests `glossary.enabled is False` explicitly, NEVER a falsy `.get()`: an
    ABSENT `glossary.enabled` means `true` (profile.schema.json's own
    default), so every profile written before this key existed -- including
    one with `skeptic_pass.enabled: true` -- must keep validating unchanged.
    Symmetrically checks `is True` on the skeptic_pass side, since an absent
    or explicit-false `skeptic_pass.enabled` is never a conflict.

    House style for a mutually-exclusive-intent refusal: canon_validate.py's
    --plugin-root/--allow-durable-sibling check."""
    glossary = profile.get("glossary") or {}
    if glossary.get("enabled") is not False:
        return []
    skeptic_pass = glossary.get("skeptic_pass") or {}
    if skeptic_pass.get("enabled") is not True:
        return []
    return [
        "glossary.enabled: false and glossary.skeptic_pass.enabled: true "
        "state two different intentions -- skipping the glossary research "
        "pass entirely versus running an advisory skeptic pass over its own "
        "output. The skeptic pass has nothing to review once the glossary "
        "pass itself is skipped (see glossary.enabled's own schema "
        "description). Disable glossary.skeptic_pass.enabled too, or set "
        "glossary.enabled back to true."
    ]


# ---------------------------------------------------------------------------
# Steps 12/13: resumed-project PROMPT_CONTRACT_VERSION / EXTRACTOR_CONTRACT_
# VERSION drift checks
# ---------------------------------------------------------------------------

def _first_non_blank_line_index(lines):
    for index, line in enumerate(lines):
        if line.strip():
            return index
    return None


def _find_marker_occurrences(lines, pattern):
    occurrences = []
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            occurrences.append((index, match.group(1).strip()))
    return occurrences


def check_contract_marker(path: Path, marker_name: str, pattern, current_version: int):
    """Shared four-state (+ mismatch) check for a single file's leading
    contract-version marker, used identically for the three *_TASK.md files
    (PROMPT_CONTRACT_VERSION, HTML-comment syntax) and extract.py
    (EXTRACTOR_CONTRACT_VERSION, Python-comment syntax). Only runs at all
    when `path` exists -- a missing file just means this isn't a resumed
    project yet, not a violation."""
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: could not read file to check its {marker_name} marker: {exc}"]

    lines = text.splitlines()
    first_non_blank = _first_non_blank_line_index(lines)
    occurrences = _find_marker_occurrences(lines, pattern)

    if not occurrences:
        return [
            f"{path}: no leading {marker_name} marker found -- treated as "
            f"version 0, which is always stale against the current version "
            f"{current_version}. Re-apply the current template by hand "
            f"(never auto-overwrite a hand-adapted file) and add the marker "
            f"as the file's first non-blank line."
        ]

    malformed = [(idx, val) for idx, val in occurrences if not re.fullmatch(r"-?\d+", val)]
    if malformed:
        idx, val = malformed[0]
        return [
            f"{path}: {marker_name} marker on line {idx + 1} has a malformed, "
            f"non-integer value {val!r} -- expected a bare integer."
        ]

    distinct_values = {int(val) for _, val in occurrences}
    if len(occurrences) > 1 and len(distinct_values) > 1:
        return [
            f"{path}: duplicated {marker_name} marker with conflicting "
            f"values {sorted(distinct_values)} -- exactly one leading marker "
            f"is expected."
        ]

    marker_line_index = occurrences[0][0]
    if marker_line_index != first_non_blank:
        return [
            f"{path}: {marker_name} marker found on line {marker_line_index + 1}, "
            f"but that is not the file's first non-blank line (first "
            f"non-blank content is on line {(first_non_blank or 0) + 1}) -- "
            f"the marker must lead the file."
        ]

    version = distinct_values.pop()
    if version != current_version:
        return [
            f"{path}: {marker_name} is version {version}, current is "
            f"{current_version} -- stale. Re-apply the current template by "
            f"hand (never auto-overwrite) and bump the marker once migrated."
        ]
    return []


def check_resumed_contract_versions(durable_root: Path, source_format=None):
    """`source_format` gates the extract.py EXTRACTOR_CONTRACT_VERSION check:
    for a `custom` source, Step 0a's `extract.py` is an unadapted copy of
    extract.py.template (never the real extractor -- that lives at
    scripts/custom_extractors/<value>, see custom.md), so drift against
    CURRENT_EXTRACTOR_CONTRACT_VERSION is meaningless there and must NOT be
    checked. A missing/unrecognized `source_format` is treated as non-custom
    (fail-safe -- the check stays ON unless we positively know it's custom).
    The three *_TASK.md PROMPT_CONTRACT_VERSION checks are format-independent
    and always run."""
    errors = []
    for filename in RESUMED_PROMPT_CONTRACT_FILENAMES:
        errors.extend(
            check_contract_marker(
                durable_root / filename,
                "PROMPT_CONTRACT_VERSION",
                PROMPT_CONTRACT_MARKER_RE,
                CURRENT_PROMPT_CONTRACT_VERSION,
            )
        )
    if source_format != "custom":
        errors.extend(
            check_contract_marker(
                durable_root / "extract.py",
                "EXTRACTOR_CONTRACT_VERSION",
                EXTRACTOR_CONTRACT_MARKER_RE,
                CURRENT_EXTRACTOR_CONTRACT_VERSION,
            )
        )
    return errors


# ---------------------------------------------------------------------------
# Step 14: output.target names a built-in adapter that has not shipped
# ---------------------------------------------------------------------------

def check_output_target_shipped(profile: dict):
    """Step 14 -- see this module's docstring item 14 for the gating and
    why each arm of it is drawn where it is. Both reads are `.get`
    deliberately: `output.target` is optional in the schema, and an absent
    one is a Step 0d condition, not this check's.
    """
    output = profile["output"]
    if output.get("v1_scope") != "assembled_book":
        return []
    target = output.get("target")
    if target not in output_resolve.BUILTIN_ADAPTER_MODULES:
        return []
    try:
        output_resolve.assert_builtin_adapter_shipped(target)
    except output_resolve.OutputResolveError as exc:
        # No `output.target: ` prefix here, unlike the sibling checks above:
        # the resolver's own message already opens with the field name, and
        # prefixing it again would print `output.target: output.target '...'`.
        return [str(exc)]
    return []


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Step 0: read + validate .claude/literary-translator/profile.yml. "
            "Always invoked from the plugin's own install path -- never a "
            "durable-root copy."
        )
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Path to the project's profile.yml (e.g. "
             ".claude/literary-translator/profile.yml).",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    profile_path = Path(args.profile)

    # --- Step 1: existence check, before anything else -----------------
    # Deliberately does NOT exit: the placeholder scan at step 5 below is the
    # intake questionnaire, and it has to reach the operator in the SAME run
    # that put the sentinels on disk (#751). Exiting here instead left the
    # copy sitting there un-asked-about, and the message that used to print
    # ("fill in every placeholder, then re-run") sent the reader to the
    # example's own inline comments for answers that were the user's to give.
    if not ensure_profile_exists(profile_path):
        print(
            f"Created a starter profile at {profile_path} from "
            f"assets/profile.example.yml. Its placeholders ARE the intake "
            "questions (every literal placeholder that file ships, and every "
            "CHOOSE_-prefixed field); once the dependency preflight below "
            f"succeeds they are listed in "
            f"full. Relay them to the user and fill in their answers -- "
            f"never answer them from this file's own inline comments.",
            file=sys.stderr,
        )

    # --- Step 2: dependency preflight ------------------------------------
    dependency_preflight()

    # --- Step 3: parse + profile_version --------------------------------
    profile = parse_profile_yaml(profile_path)

    version_errors = check_profile_version(profile)
    if version_errors:
        for err in version_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Step 4: unknown top-level keys ----------------------------------
    unknown_key_errors = check_unknown_top_level_keys(profile)
    if unknown_key_errors:
        for err in unknown_key_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Step 5: whole-profile placeholder scan, BEFORE jsonschema (#727) ---
    # Runs here, not after schema validation, so an unanswered CHOOSE_
    # sentinel in a currently-inactive adapter sub-block (never load-bearing
    # for jsonschema's own enum check) still gets reported, and every open
    # intake decision surfaces together in one pass rather than jsonschema
    # halting the run on the first unconditionally-required field's own
    # sentinel and hiding the rest. See scan_placeholders()'s own docstring.
    substring_errors = _scan_placeholder_substrings(profile)
    sentinel_errors = _scan_choose_sentinels(profile)
    if substring_errors or sentinel_errors:
        for err in substring_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        if sentinel_errors:
            print(
                f"ERROR: Step 0 needs these intake decisions answered in "
                f"{profile_path} before scaffolding can proceed -- relay "
                f"this list to the user and fill in their answers:",
                file=sys.stderr,
            )
            for err in sentinel_errors:
                print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Step 6: whole-file schema validation ----------------------------
    schema = load_profile_schema()
    schema_errors = validate_against_schema(profile, schema)
    if schema_errors:
        for err in schema_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Steps 7-15: procedural checks (schema already passed) -----------
    fatal_errors = []
    warnings = []

    fatal_errors += check_source_path(profile)
    fatal_errors += check_durable_root(profile)
    fatal_errors += check_output_destination(profile)
    fatal_errors += check_output_target_shipped(profile)

    seg_errors, seg_warnings = check_plain_text_segmentation(profile)
    fatal_errors += seg_errors
    warnings += seg_warnings

    warnings += check_custom_format_warning(profile)

    fatal_errors += check_particle_config(profile)
    fatal_errors += check_smoke_test_report_path(profile)

    fatal_errors += check_glossary_disabled_conflicts_with_skeptic_pass(profile)

    durable_root = Path(profile["project"]["durable_root"]).expanduser()
    fatal_errors += check_resumed_contract_versions(
        durable_root, profile.get("source", {}).get("format")
    )

    for warning in warnings:
        print(f"WARNING: {warning}")
    for err in fatal_errors:
        print(f"ERROR: {err}", file=sys.stderr)

    if fatal_errors:
        sys.exit(1)

    suffix = " (see warnings above)" if warnings else ""
    print(f"{profile_path}: OK -- Step 0 validation passed{suffix}")
    sys.exit(0)


if __name__ == "__main__":
    main()
