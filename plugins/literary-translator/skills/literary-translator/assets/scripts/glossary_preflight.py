#!/usr/bin/env python3
"""glossary_preflight.py -- W3 glossary pre-dispatch staleness gate (#138).

A durable_root scaffolded before the plugin build that introduced
basis:"sense_translated" ships STALE copies of canon-entry.schema.json /
canon-batch.schema.json / glossary_TASK.md under `${durable_root}/`. If the
glossary pass were dispatched against that stale copy, the agent could emit
(or the durable schema could accept) an item the FRESH plugin's own contract
no longer agrees with -- or, worse, the agent could be taught the new value
by a fresh prompt while the durable schema still rejects it, producing an
unbounded retry-until-valid hang (the same T-14 hang class canon_validate.py
--- `_format_errors`'s D17 branch-select/key-recovery -- exists to make
convergent, from the opposite direction). This script is the deterministic,
stdlib-only preflight SKILL orchestration runs immediately before every
glossary pre-dispatch (see SKILL.md's W3 glossary section): it halts BEFORE
any agent is dispatched whenever the durable copy has drifted from the
plugin's own shipped, fresh contract.

**THIS SCRIPT IS NEVER COPIED TO `durable_root`** -- exactly like
`profile_validate.py` and `validate_extraction.py` (SKILL.md's Step 0a
copy-exclusion list), it is always invoked directly from the plugin's own
install path and self-anchors relative to its own `assets/scripts/`
location. Being copied would be actively harmful here, not merely
redundant: a durable copy's own `Path(__file__)`-relative resolution would
land on the DURABLE schemas as its "plugin" side too, comparing
durable-vs-durable -- a vacuous pass that can never detect staleness.

    python3 {{PLUGIN_ROOT}}/assets/scripts/glossary_preflight.py \\
        --durable-root ${durable_root}

Order of operations:

  1. Load the plugin's OWN shipped canon-entry.schema.json /
     canon-batch.schema.json (self-anchored via `Path(__file__).resolve()
     .parents[1]` -- this script lives at `assets/scripts/`, so its
     schemas are siblings at `assets/schemas/`, its prompt template at
     `assets/templates/`). An unreadable/corrupt SHIPPED schema is an
     install/packaging error (exit 2), not a durable-root staleness --
     this plugin installation is broken, not the project.
  2. Check `${durable_root}/schemas/` exists. Absent (Step 0a has not run
     yet, or a project-root-coincidence config) -> exit 2, actionable,
     naming the remedy.
  3. Load the durable copy of each schema, guarding every read
     (`os.path.isfile` + `try/except (OSError, ValueError, RecursionError)`
     -- `json.JSONDecodeError` and an oversized-int-literal `ValueError`
     are both `ValueError` subclasses, and a pathologically deep document
     can raise `RecursionError` during parse -- plus rejecting a
     syntactically-valid-but-non-object document) -- an unreadable/corrupt/
     malformed durable schema -> exit 2, actionable, NEVER a traceback.
  4. Extract the PROJECTION from each schema document via `_project(doc)`
     -- ONE function, applied IDENTICALLY to the plugin's copy and the
     durable copy of each schema file. The projection IS the WHOLE schema
     document, coerced to a dict (`_as_dict`, so a structurally-wrong top
     level -- a bare array, `None`, ... -- degrades safely) and
     depth-checked (`_assert_bounded_depth`, raising `_SchemaTooDeepError`
     -> caught by the caller -> clean halt, never a `RecursionError`) --
     but otherwise UNTRANSFORMED. Two earlier designs were replaced: a
     hand-enumerated "sense_translated-relevant parts" projection (leaky --
     any construct outside the hand-picked list could drift and still
     compare "equal"), then a whole-schema design that additionally SORTED
     "set-semantic" arrays (required/enum/type/allOf/anyOf/oneOf) for
     order-insensitivity -- itself unsound (codex round-5): that sort was
     CONTEXT-BLIND, so it also silently sorted INSTANCE DATA sitting
     inside a `const`/`default`/enum member under one of those same key
     names, corrupting genuine data differences into false-PASSes; two
     independently-truncated deep subtrees could also collide into
     "equal" via a shared sentinel. Array-order-insensitivity was never
     actually needed: a healthy durable is a byte-copy of the plugin's own
     shipped schema, so array order never legitimately varies -- only
     object-KEY order can (see step 5's `sort_keys=True`), which needs no
     special per-key handling at all. Nothing is sorted, nothing is
     stripped (not even purely cosmetic keywords like `title`/
     `description` -- stripping would open a false-PASS vector: a
     validation-relevant construct could hide under a key sharing a
     stripped keyword's name), nothing is transformed. See "Robustness",
     below.
  5. Compare the plugin's projection against the durable's
     (`_diff_projection`) via ORDER-EXACT canonical JSON
     (`json.dumps(sort_keys=True)`: object-KEY order is insensitive, but
     ARRAY order and scalar TYPE are exact -- never Python `==`, which
     collapses JSON scalar-type distinctions such as `True == 1`).
     Presence is checked EXPLICITLY, never via a `.get(field, <sentinel>)`
     default value (a fixed sentinel string can collide with a real
     schema value on the other side -- codex round-5). ANY inequality ->
     exit 2, naming which top-level schema keys drifted -- this single
     equality invariant catches a stale enum, a stray sibling keyword on
     a conditional clause, a duplicated `oneOf` branch, a partial
     migration, a plugin superset/downgrade, AND a hand-reordered array,
     alike: every case correctly means "the durable copy needs
     re-scaffolding". STRICTNESS-BIASED throughout: when in doubt, this
     gate HALTS -- a false HALT's remedy (re-apply the plugin's schema)
     is safe and idempotent, while a false PASS is the unbounded hang
     this whole script exists to prevent. The only practical cost of
     order-EXACT arrays is that a hand-reordered (but otherwise
     unchanged) durable array now halts where array-order-insensitivity
     would have passed -- deliberately accepted, since a genuinely healthy
     durable schema is a byte-copy and never reorders arrays at all.
  6. Prompt axis: if the plugin's OWN `assets/templates/
     glossary_TASK.template.md` contains the literal substring
     "sense_translated", its leading (first-NON-BLANK-line-only; a marker
     that isn't genuinely leading, or a later conflicting one, is never
     picked up instead) `<!-- PROMPT_CONTRACT_VERSION: N -->` marker is
     compared against the durable `${durable_root}/glossary_TASK.md`'s own
     leading marker (guarding that read too) -- durable absent/
     unparseable/strictly LESS than the plugin's -> exit 2, axis=prompt.
     **If the PLUGIN's own marker itself is absent/unparseable, that is a
     plugin-packaging bug and HALTS too -- it is never treated as "nothing
     to check" and silently skipped**, exactly like an unreadable plugin
     template. A structured VERSION comparison, not a substring check (a
     bare `"sense_translated" in text` would pass vacuously on a stray
     comment mentioning the string without the file actually carrying the
     current contract) -- and not a strict content-equality one either,
     because the durable copy is a one-time hand-migratable seed that may
     be reformatted (unlike the shipped templates `canon_enum_drift.test
     .py`'s TP-3 strictly set-compares).
  7. All pass -> exit 0, stdout `{"preflight":"ok"}`.

Robustness -- nothing here to keep in sync with the schemas by hand: the
projection is the WHOLE schema document, UNTRANSFORMED (`_project` only
coerces-to-dict and depth-checks -- it never sorts, strips, or otherwise
rewrites any value), and asserts the plugin's and the durable's are EQUAL
under ORDER-EXACT canonical JSON; the "expected" shape is read from the
plugin's OWN shipped schema at runtime, never a hardcoded literal. If a
future release changes ANY part of a schema's shape, the plugin schema
changes and this gate automatically compares durable-vs-new-plugin --
there is no second copy of any construct here to drift out of sync, and
no un-enumerated construct (a sibling keyword, a duplicated branch, a
top-level `not`, ...) can silently leak through as a false-PASS. Array
order-insensitivity was DELIBERATELY REMOVED (codex round-5): sorting
"set-semantic" arrays for order-insensitivity is CONTEXT-BLIND -- it
cannot distinguish a genuinely order-irrelevant schema-structural array
(e.g. `required`) from instance DATA that happens to sit inside a
`const`/`default`/enum member under the identical key name, so it
silently corrupted real data differences into false-PASSes. A healthy
byte-copy durable schema never reorders arrays at all, so order-exactness
costs nothing in practice; the only behavioral change from
order-insensitivity is that a hand-REORDERED array now halts (the safe,
idempotent direction) instead of silently passing. Depth is bounded by
RAISING (`_SchemaTooDeepError`), never by truncating to a substitute
sentinel value -- a raise has no value to collide with, so a
pathologically-nested schema (plugin- or durable-side) always halts
cleanly, never a `RecursionError` and never a false-PASS collision between
two independently-truncated subtrees.

Exit codes: 0 = durable root is current, dispatch may proceed (stdout
`{"preflight":"ok"}`); 2 = stale/fatal -- one actionable line on stderr
(guaranteed single-line even if an interpolated value, e.g. --durable-root
itself, embeds a newline -- see `_halt`), nothing on stdout, the
orchestrator halts and dispatches nothing. Never exit 1 for any case here
-- 1 is reserved for canon_validate.py's own data-validation failures, a
different script and a different caller contract. A malformed/absurd
PROMPT_CONTRACT_VERSION marker value (e.g. exceeding Python 3.11+'s
str->int conversion digit limit) is treated as "no marker" -> a clean exit
2, never a raised exception.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

SENSE_TRANSLATED = "sense_translated"

# Same marker syntax/regex as profile_validate.py's own
# PROMPT_CONTRACT_MARKER_RE (kept in sync by eye -- this gate's own concern,
# durable-vs-plugin staleness at glossary pre-dispatch, is deliberately
# narrower than profile_validate.py's Step-12 malformed/duplicate/position
# pedantry, so it is not reused directly; see _leading_prompt_contract_version).
_PROMPT_CONTRACT_MARKER_RE = re.compile(r"^\s*<!--\s*PROMPT_CONTRACT_VERSION:\s*(.+?)\s*-->\s*$")

# Self-anchored to the PLUGIN's own install path (see the module docstring's
# "NEVER COPIED" note above) -- this script lives at assets/scripts/, so its
# schemas are siblings at assets/schemas/ and its prompt template lives at
# assets/templates/glossary_TASK.template.md.
_SCRIPT_FILE = Path(__file__).resolve()
ASSETS_DIR = _SCRIPT_FILE.parents[1]
PLUGIN_SCHEMAS_DIR = ASSETS_DIR / "schemas"
PLUGIN_GLOSSARY_TASK_TEMPLATE = ASSETS_DIR / "templates" / "glossary_TASK.template.md"

SCHEMA_FILENAMES = ("canon-entry.schema.json", "canon-batch.schema.json")


def _halt(message: str) -> NoReturn:
    """Prints ONE actionable line to stderr and exits 2. Never a traceback,
    never exit 1 -- see the module docstring's Exit codes section.
    `" ".join(message.split())` collapses ANY internal whitespace run --
    including a literal newline/tab -- to a single space: every message
    this module builds is single-line prose, but a value interpolated into
    one (e.g. --durable-root itself, or a path derived from it) could
    embed a newline, which would otherwise split the "one line" contract
    into more than one. Display-only normalization, no correctness impact."""
    sys.stderr.write(" ".join(message.split()) + "\n")
    sys.exit(2)


def _read_text_guarded(path: Path):
    """Reads `path` as UTF-8 text, returning (text, error_message). Never
    raises -- a missing file, an OSError, OR invalid UTF-8 bytes
    (UnicodeDecodeError -- a ValueError subclass, NOT an OSError, so it
    must be caught explicitly) is captured and returned as a plain string
    instead of propagating."""
    if not os.path.isfile(path):
        return None, f"not found: {path}"
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"could not read {path}: {e}"


def _reject_duplicate_keys(pairs):
    """`object_pairs_hook` for `json.loads`: builds the object from `pairs`
    but RAISES `ValueError` on a DUPLICATE key anywhere in the document.
    Plain `json.loads` silently keeps the LAST duplicate member (last-wins),
    so a durable schema carrying a stale enum as a first `"basis"` key and
    the current enum as a second, duplicate `"basis"` key would parse to the
    CURRENT value and false-PASS the currency gate -- even though the on-disk
    document is corrupt and parser-dependent (a first-wins consumer elsewhere
    sees the STALE contract). A duplicate-key document is malformed, so the
    strictness-safe direction is to HALT. The raised `ValueError` is caught by
    `_read_json_guarded`'s `except (ValueError, RecursionError)` -> clean exit
    2, and applies symmetrically to BOTH the plugin-side and durable-side
    reads (both flow through this one reader)."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _read_json_guarded(path: Path):
    """JSON counterpart of _read_text_guarded: returns (doc, error_message),
    error_message None iff `doc` is a genuine JSON OBJECT read successfully.
    Never raises -- every failure (missing file, unreadable, malformed JSON,
    a DUPLICATE object key (via `_reject_duplicate_keys`), or
    syntactically-valid-but-non-object JSON) is captured and returned as a
    plain string instead. Catches `(ValueError, RecursionError)`, not just
    `json.JSONDecodeError`: `JSONDecodeError` IS a `ValueError` subclass, but
    `json.loads` can also raise a BARE `ValueError` for an oversized integer
    literal (Python 3.11+'s str->int conversion digit cap -- e.g. a
    5000-digit number embedded in the schema) OR a duplicate key, and
    `RecursionError` for a pathologically deep document -- all three must
    degrade to the same clean exit 2, never a raised exception."""
    text, error = _read_text_guarded(path)
    if error is not None:
        return None, error
    try:
        doc = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError) as e:
        return None, f"invalid or unreadable JSON in {path}: {e}"
    if not isinstance(doc, dict):
        return None, f"{path} does not contain a JSON object (got {type(doc).__name__})"
    return doc, None


def _leading_prompt_contract_version(text) -> "int | None":
    """The integer value of the marker on `text`'s FIRST NON-BLANK line,
    IFF that line matches `<!-- PROMPT_CONTRACT_VERSION: N -->` with a bare
    integer value -- never a marker found further down. Matches this
    plugin's own PROMPT_CONTRACT_VERSION convention (see profile_validate
    .py's `check_contract_marker`: "the marker must lead the file", via its
    own `_first_non_blank_line_index`): if the file's true leading content
    line is not a valid marker, the file is treated as having NONE at all
    -- this deliberately does NOT keep scanning past that line, so neither
    a marker appearing only deeper in the file (never actually "leading")
    nor a LATER, conflicting marker line can be picked up instead of the
    genuine leading one. Never raises.

    Used (Step 6) to compare the durable glossary_TASK.md's contract
    version against the plugin's own shipped glossary_TASK.template.md --
    a structured comparison, deliberately replacing a bare
    `"sense_translated" in text` substring check, which a stray comment
    (e.g. `<!-- TODO: sense_translated -->`) could satisfy vacuously
    without the file actually carrying the current contract."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        if line.strip() == "":
            continue
        match = _PROMPT_CONTRACT_MARKER_RE.match(line)
        if not match:
            return None
        value = match.group(1).strip()
        if not re.fullmatch(r"-?\d+", value):
            return None
        # Python 3.11+ caps str->int conversion at ~4300 digits (a DoS
        # guard, PEP wasn't retroactive) -- an absurdly long digit string
        # (corrupt/hostile marker) would raise ValueError here and escape
        # as a traceback rather than a clean halt. Treat it as unparseable,
        # same as any other malformed marker -- the safe direction.
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_dict(value) -> dict:
    """Returns `value` if it is a dict, else an empty dict. Used by
    `_project` to coerce its top-level `doc` -- a structurally-wrong top
    level (a bare array, `None`, ...) degrades to a clean empty-dict
    mismatch instead of an AttributeError deeper in `_diff_projection`."""
    return value if isinstance(value, dict) else {}


class _SchemaTooDeepError(Exception):
    """A schema nests past `_MAX_SCHEMA_DEPTH` -- malformed/pathological.
    Raised by `_assert_bounded_depth` so neither this walk NOR the
    downstream `json.dumps(sort_keys=True)` canonicalization (in
    `_diff_projection`) can raise `RecursionError`; caught in `main()` ->
    clean exit-2 halt. RAISING (instead of truncating to a sentinel value,
    this script's rev-4 approach) is collision-proof: there is no VALUE for
    a real schema to collide with, so a too-deep schema ALWAYS halts, never
    false-PASSes (codex round-5 BLOCKER-3 -- two independently-truncated
    subtrees, one plugin-side and one durable-side, could otherwise
    truncate to the SAME sentinel and compare "equal")."""


# Far beyond any real schema's nesting depth, far below Python's own
# recursion limit.
_MAX_SCHEMA_DEPTH = 100


def _assert_bounded_depth(node, _depth=0) -> None:
    """Walks `node` and raises `_SchemaTooDeepError` if it nests past
    `_MAX_SCHEMA_DEPTH` -- never truncates, never returns a substitute
    value (see `_SchemaTooDeepError`'s own docstring for why). Called once,
    up front, in `_project`, before the document is ever handed to
    `_diff_projection`'s `json.dumps(sort_keys=True)` -- which has no
    depth guard of its own and would otherwise risk a raw `RecursionError`
    on a pathologically nested durable schema."""
    if _depth >= _MAX_SCHEMA_DEPTH:
        raise _SchemaTooDeepError(_depth)
    if isinstance(node, dict):
        for value in node.values():
            _assert_bounded_depth(value, _depth + 1)
    elif isinstance(node, list):
        for item in node:
            _assert_bounded_depth(item, _depth + 1)


def _project(doc):
    """The projection IS the whole schema document, coerced to a dict and
    depth-bounded -- NO transformation of its content. Applied IDENTICALLY
    to the plugin's own shipped copy and the durable copy of each schema
    file; currency is checked by ORDER-EXACT canonical-JSON equality in
    `_diff_projection` (`json.dumps(sort_keys=True)`: object-KEY order is
    insensitive, but ARRAY order and scalar TYPE are exact).

    This replaces TWO earlier, narrower designs. The first hand-enumerated
    only the "sense_translated-relevant parts" (the basis enum + the
    sense_translated allOf clause's if/then shape) -- FUNDAMENTALLY leaky,
    since whether an item is actually accepted depends on the WHOLE
    accepted-item schema, so any construct outside the hand-picked list
    (a sibling keyword, a duplicated oneOf branch, a top-level `not`, ...)
    could drift on the durable side and still compare "equal", a
    false-PASS. The second (rev 4) fixed that by comparing the WHOLE
    schema, but SORTED every "set-semantic" array (required/enum/type/
    allOf/anyOf/oneOf) for order-insensitivity -- codex round-5 found this
    sort was itself unsound: it is CONTEXT-BLIND, so it also sorts
    INSTANCE DATA that happens to sit inside a `const`/`default`/enum
    member under one of those same key names (e.g. a `const` value that is
    itself `{"required": [...]}`), silently corrupting genuine data
    differences into false-PASSes; a two-sided depth-truncation sentinel
    could also collide two independently-malformed schemas into "equal".

    Array-order-insensitivity was never actually needed for THIS gate's
    real job: a healthy durable is a byte-copy of the plugin's OWN shipped
    schema (via Step 0a's copy or a hand-migration that preserves array
    order), so array order never legitimately varies -- only object-KEY
    order can (a hand-formatted/re-dumped JSON file), and
    `json.dumps(sort_keys=True)` already normalizes THAT. So nothing is
    sorted, nothing is stripped (not even title/description -- see the
    module docstring's Robustness section), nothing is transformed at all.
    The only cost is that a hand-REORDERED array now HALTs where an
    (unneeded) order-insensitive design would have passed -- the safe,
    idempotent direction (re-apply the plugin's schema fixes it), never a
    false-PASS.

    `doc` is coerced via `_as_dict` and depth-checked via
    `_assert_bounded_depth` (which raises `_SchemaTooDeepError`, caught by
    the caller) so a structurally-wrong top level (a bare array, `None`,
    ...) or a pathologically nested document degrades safely rather than
    crashing or false-passing."""
    doc = _as_dict(doc)
    _assert_bounded_depth(doc)
    return doc


def _diff_projection(plugin_projection: dict, durable_projection: dict) -> list:
    """Every TOP-LEVEL key of the two (whole-schema) projections where the
    two disagree, as (field, plugin_value, durable_value) triples -- e.g.
    "properties" or "allOf" for canon-entry.schema.json, "items" for
    canon-batch.schema.json. Field-by-field (not a single whole-dict `!=`)
    so the halt message can name exactly which top-level schema key
    drifted, without dumping the (possibly large) nested value that
    actually differed.

    PRESENCE is checked EXPLICITLY (`field in plugin_projection` /
    `field in durable_projection`), never via a `.get(field, <sentinel>)`
    default value -- codex round-5 BLOCKER-2: a fixed sentinel string
    (this script's rev-4 approach used `"<absent>"`) can COLLIDE with a
    real schema value on the other side (a plugin schema legitimately
    carrying a top-level key whose value happens to be the literal string
    `"<absent>"` would then compare "equal" against a durable schema
    entirely missing that key) -- a false-PASS. A key present on only one
    side is unconditionally a mismatch; the OTHER two triple values in
    that case are DISPLAY-ONLY labels (`"<only in durable>"`/`"<only in
    plugin>"`), never compared against anything, so they cannot collide.

    Values present on BOTH sides are compared via CANONICAL JSON
    (`json.dumps(..., sort_keys=True)`), NOT Python `==` -- `==` collapses
    JSON scalar-type distinctions (`True == 1`, `1 == 1.0`), which would
    let a durable schema swap some nested `is_proper_name:{"const":true}`
    for the numerically-equal-but-semantically-different `{"const":1}`
    (however deep in the tree) and still compare "equal", a false-PASS.
    `sort_keys=True` normalizes only object-KEY order (a hand-formatted/
    re-dumped JSON file may reorder keys harmlessly); it does NOT reorder
    arrays or otherwise transform the value -- see `_project`'s own
    docstring for why array reordering is deliberately NOT tolerated."""
    fields = sorted(set(plugin_projection) | set(durable_projection))
    mismatches = []
    for field in fields:
        in_plugin = field in plugin_projection
        in_durable = field in durable_projection
        if not (in_plugin and in_durable):
            mismatches.append((
                field,
                plugin_projection[field] if in_plugin else "<only in durable>",
                durable_projection[field] if in_durable else "<only in plugin>",
            ))
            continue
        if json.dumps(plugin_projection[field], sort_keys=True) != json.dumps(
            durable_projection[field], sort_keys=True
        ):
            mismatches.append((field, plugin_projection[field], durable_projection[field]))
    return mismatches


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "W3 glossary pre-dispatch staleness gate -- compares the "
            "durable_root's copy of canon-entry.schema.json / "
            "canon-batch.schema.json / glossary_TASK.md against the "
            "plugin's own shipped copies for basis:\"sense_translated\" "
            "support. See this file's own module docstring for the full "
            "spec."
        )
    )
    parser.add_argument(
        "--durable-root",
        required=True,
        metavar="PATH",
        help="The project's durable_root (profile.yml's project.durable_root).",
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    durable_root = Path(args.durable_root)
    durable_schemas_dir = durable_root / "schemas"

    # --- Step 1: load the plugin's OWN shipped schemas ----------------------
    plugin_projections = {}
    for filename in SCHEMA_FILENAMES:
        plugin_path = PLUGIN_SCHEMAS_DIR / filename
        doc, err = _read_json_guarded(plugin_path)
        if err is not None:
            _halt(
                f"glossary_preflight: could not read this plugin's OWN shipped "
                f"{plugin_path} ({err}) -- this is a literary-translator plugin "
                f"install/packaging problem, not a durable_root staleness issue; "
                f"reinstall the plugin."
            )
        try:
            plugin_projections[filename] = _project(doc)
        except _SchemaTooDeepError:
            _halt(
                f"glossary_preflight: this plugin's OWN shipped {plugin_path} "
                f"nests pathologically deep (past {_MAX_SCHEMA_DEPTH} levels) -- "
                f"this is a literary-translator plugin install/packaging "
                f"problem, not a durable_root staleness issue; reinstall the "
                f"plugin."
            )

    # --- Step 2: durable schemas/ must exist ---------------------------------
    if not durable_schemas_dir.is_dir():
        _halt(
            f"glossary_preflight: durable schemas/ not found at "
            f"{durable_schemas_dir} -- Step 0a has not copied this plugin's "
            f"schemas into this project yet (or durable_root is misconfigured). "
            f"Re-run Step 0 + Step 0a before dispatching the glossary pass."
        )

    # --- Steps 3-5: load + project + compare each durable schema -----------
    for filename in SCHEMA_FILENAMES:
        durable_path = durable_schemas_dir / filename
        doc, err = _read_json_guarded(durable_path)
        if err is not None:
            _halt(
                f"glossary_preflight: durable schema {durable_path} could not "
                f"be read ({err}). Re-run Step 0 + Step 0a to refresh the "
                f"durable schemas/ from this plugin's own shipped copies, then "
                f"retry the glossary pass."
            )
        try:
            durable_projection = _project(doc)
        except _SchemaTooDeepError:
            _halt(
                f"glossary_preflight: durable schema {durable_path} nests "
                f"pathologically deep (past {_MAX_SCHEMA_DEPTH} levels) -- "
                f"malformed/stale. Re-run Step 0 + Step 0a to refresh the "
                f"durable schemas/ from this plugin's own shipped copies, then "
                f"retry the glossary pass."
            )
        mismatches = _diff_projection(plugin_projections[filename], durable_projection)
        if mismatches:
            # Name only the differing TOP-LEVEL schema keys, not a dump of
            # their whole (possibly huge -- e.g. the entire `properties` or
            # `items` subtree) values -- _diff_projection's own return still
            # carries the full values for anyone inspecting programmatically,
            # but the halt message stays a single readable line.
            drifted = ", ".join(field for field, _plugin_value, _durable_value in mismatches)
            _halt(
                f"glossary_preflight: durable schemas/{filename} differs "
                f"structurally from this plugin's own shipped copy (which now "
                f"carries basis:\"{SENSE_TRANSLATED}\" support) at: {drifted}. "
                f"Re-run Step 0 + Step 0a to refresh the durable schemas/, then "
                f"retry the glossary pass -- dispatching against a stale schema "
                f"risks an unbounded retry-until-valid hang."
            )

    # --- Step 6: prompt axis --------------------------------------------------
    plugin_task_text, err = _read_text_guarded(PLUGIN_GLOSSARY_TASK_TEMPLATE)
    if err is not None:
        _halt(
            f"glossary_preflight: could not read this plugin's OWN shipped "
            f"{PLUGIN_GLOSSARY_TASK_TEMPLATE} ({err}) -- this is a "
            f"literary-translator plugin install/packaging problem, not a "
            f"durable_root staleness issue; reinstall the plugin."
        )
    if SENSE_TRANSLATED in plugin_task_text:
        # A bare substring check on the DURABLE copy is vacuous -- a stray
        # `<!-- TODO: sense_translated -->` comment would satisfy it without
        # the file actually carrying the current contract. Compare the
        # leading PROMPT_CONTRACT_VERSION marker instead: durable must be
        # present, parseable, and >= the plugin's own shipped marker.
        plugin_marker = _leading_prompt_contract_version(plugin_task_text)
        if plugin_marker is None:
            # CRITICAL: falling through here (treating "can't determine the
            # plugin's own marker" as "nothing to check") would SILENTLY
            # DISABLE this entire safety axis -- a plugin-packaging defect
            # (a shipped template teaching sense_translated with no leading
            # marker) must halt loud, exactly like the unreadable-plugin-
            # template halt above, never fall through to success.
            _halt(
                f"glossary_preflight: this plugin's own shipped "
                f"{PLUGIN_GLOSSARY_TASK_TEMPLATE} teaches basis:\"{SENSE_TRANSLATED}\" "
                f"but has no leading, parseable PROMPT_CONTRACT_VERSION marker -- "
                f"this is a literary-translator plugin install/packaging problem, "
                f"not a durable_root staleness issue; reinstall the plugin."
            )
        durable_task_path = durable_root / "glossary_TASK.md"
        durable_task_text, err = _read_text_guarded(durable_task_path)
        durable_marker = None if err is not None else _leading_prompt_contract_version(durable_task_text)
        if err is not None:
            reason = err
        elif durable_marker is None:
            reason = "no leading PROMPT_CONTRACT_VERSION marker found (or its value is not a bare integer)"
        elif durable_marker < plugin_marker:
            reason = (
                f"durable marker is version {durable_marker}, this plugin's own "
                f"shipped glossary_TASK.template.md is version {plugin_marker}"
            )
        else:
            reason = None
        if reason is not None:
            _halt(
                f"glossary_preflight: durable {durable_task_path} is STALE, "
                f"axis=prompt ({reason}) -- this plugin's shipped "
                f"glossary_TASK.template.md now teaches basis:\"{SENSE_TRANSLATED}\", "
                f"but the durable, one-time-seeded copy is behind. "
                f"glossary_TASK.md is NEVER auto-overwritten -- hand-re-apply "
                f"this plugin's current glossary_TASK.template.md (and bump its "
                f"PROMPT_CONTRACT_VERSION marker to {plugin_marker}) before "
                f"retrying the glossary pass."
            )

    # --- Step 7: all clear ----------------------------------------------------
    print(json.dumps({"preflight": "ok"}, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
