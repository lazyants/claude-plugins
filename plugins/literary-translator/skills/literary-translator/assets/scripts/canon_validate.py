#!/usr/bin/env python3
"""canon_validate.py -- two-pass schema validation (and merge) backstop for
canon.json, the literary-translator plugin's frozen, hash-versioned,
cross-segment name/realia glossary.

STATUS: new plugin hardening -- the glossary-pass Workflow template that
feeds this script's batch-fragment modes is itself not yet source-proven
(see references/canon-and-glossary.md's "Glossary-pass call discipline"
section: the real historiettes-t3 project ran its glossary pass as ad hoc
`glossary/TASK.md` + codex batches, never through this schema-validated
pipeline). Authoritative spec for everything below:
references/canon-and-glossary.md, sections "`canon_validate.py`'s two
validation passes" and "Research preflight and offline-fallback policy for
`basis: "established"`" -- read those before changing anything here; this
file's behavior must match that doc exactly.

1.2.0 CHANGE -- fire-and-forget batch fragments, no agent-schema return:
pre-1.2.0, a glossary-pass batch call returned its result directly to the
JS via a discriminated-union agent `schema` (`CANON_BATCH_SCHEMA`) -- which
violates the tool-use API's "top-level object, no combinator" constraint
(issue #87). Since 1.2.0, glossary batches are schema-less, fire-and-forget
codex dispatches (`batchDispatchPrompt`) that write their own fragment file
atomically and self-validate it via THIS script's `--check-batch` mode,
never via an agent-returned schema. `CANON_BATCH_SCHEMA` no longer exists
in any template. Seven CLI modes now exist, selected by which flag is given
(mutually exclusive; `--research-mode {live,offline}` is REQUIRED for
every one of them, even where it has no effect, so no call site can
accidentally omit declaring the precondition):

--init
    Bootstrap mode (#290): writes an EMPTY but fully stamped canon.json
    (`entries: {}`, `review_queue: []`, both generation_hashes fields
    freshly computed) through the SAME `_stamp_write_verify` path every
    merge uses. Exists because W3's `{"no_new_candidates": true}` SKIP
    branch never reaches a merge, and the merge is the only thing that
    ever creates canon.json -- so a project with nothing to research (by
    construction, every uncased-script source that ships no
    `name_inventory`) used to dead-end at W3a with segpack.py's
    "FATAL: canon.json not found". CREATE-ONLY: an already-existing
    canon.json is left byte-untouched and reported `"created": false`,
    never re-stamped -- re-stamping would let an operator clear
    select_segments.py's derivation-state gate without regenerating
    anything, since that gate reads exactly these two hashes.

--restamp-derivation
    Re-records the CURRENT particle_config/derivation-bundle provenance
    onto an EXISTING canon.json, content untouched (#291/#193). Every
    other write path leaves the stamp alone when the document did not
    change (see --merge-batches below), so this is the one deliberate way
    to advance it -- the sanctioned replacement for the
    `--merge-batches <empty-batch.json>` trick #193 records as its only,
    explicitly unsanctioned escape from `blocked_needs_regeneration` on a
    mature, zero-candidate project. Reports which fields moved. Refuses on
    a project with no canon.json yet (use --init).

--check-batch PATH [--expect-source-forms-file M.json]
    Pass 1 (per-item) + the offline backstop on the ONE fragment at PATH.
    NO write. When --expect-source-forms-file is given (a JSON array of
    expected source_form strings, read from the FILE, never inline argv),
    additionally asserts the fragment's item source_forms are an EXACT
    match (no missing, no extra) -- the coverage half of the manifest-
    trust design (references/canon-and-glossary.md's "manifest disk-
    verify"), closing the gap where a codex batch could pass shape
    validation while silently omitting a candidate name.

--merge-batches P1 P2 ... [--expect-source-forms-file M.json is NOT
accepted here -- see --verify-merged]
    ONE process, single canon.json load: validates ALL given fragments
    (Pass 1 + offline backstop) FIRST, before merging any of them, so a
    later fragment's failure never leaves an earlier one half-applied.
    Then threads `acc = _merge_batch(acc, frag, senses)` across every
    fragment IN THE GIVEN ORDER, resolves generation_hashes (stamping them
    fresh ONLY if the merged document actually differs from what is already
    on disk -- #291; an identical re-submission or an empty fragment set
    changes nothing and must not advance the provenance claim
    select_segments.py's derivation-state gate reads), validates the
    in-memory accumulator against canon-file.schema.json (Pass 2) BEFORE
    ever touching disk, performs ONE atomic write, then re-reads the
    JUST-WRITTEN file fresh from disk and Pass-2-validates it AGAIN --
    genuinely from disk this time, with no masking fallback for a missing
    generation_hashes value, so a dropped-hash write corruption is
    actually caught rather than silently papered over.

--verify-merged --batch F1 [--batch F2 ...] [--expect-source-forms-file
M.json]
    Disk-INDEPENDENT verification that a set of already-processed
    fragments is correctly reflected in the CURRENT canon.json -- no
    write, fresh reads only. Also runs Pass 2 (`_validate_whole_file`,
    the same whole-file schema validation plus the entries{}/
    review_queue[] overlap invariant that every write path already runs)
    against the freshly re-read canon.json itself -- this is the
    Workflow's own actual trusted final gate, so a hand-corrupted or
    otherwise not-merged-through-`_merge_batch` canon.json must be caught
    here too, not only by `--batch`/`--merge-batches`' pre-write checks.
    Any Pass-2 failure is folded into `missing` (never raises past this
    function -- same as every other failure this mode reports). Per
    fragment item, by disposition: an 'accepted' item must equal
    `canon["entries"][source_form]` exactly; a 'review_queue' item must
    either still be present verbatim in `canon["review_queue"]`, OR its
    source_form must now be a key in `canon["entries"]` (accept-supersedes
    -- a later batch's ACCEPTED resolution for the same name is not a
    failure, never reported missing). When --expect-source-forms-file is
    given, additionally asserts every manifest name is covered by SOME
    fragment item. Reports `{"verified": true}` or `{"verified": false,
    "missing": [...]}` -- the exact relay shape the glossary-pass
    Workflow's disk-verify agent (`CANON_VERIFY_SCHEMA`) returns.

--batch PATH (legacy, single-fragment merge -- KEPT for existing callers)
    The pre-1.2.0 merge path: Pass 1 + offline backstop on the one
    fragment, merge, stamp generation_hashes, in-memory Pass 2, one atomic
    write, disk-re-read Pass 2 (same no-masking discipline as
    --merge-batches above). Equivalent to `--merge-batches PATH` with
    exactly one fragment, kept as its own code path only because existing
    tests/callers already invoke it this way.

(no batch flag at all) -- VALIDATE-ONLY mode
    A read-only health check: no merge, no write, no offline backstop
    (that backstop only ever applies to NEW entries in an incoming batch,
    per the authoritative spec's own "for every new entry" framing -- an
    already-frozen canon.json is not retroactively re-litigated just
    because this run happens to pass --research-mode offline for other
    reasons). Pass 1 (per-entry) validates every canon.json entries{}
    value against canon-entry.schema.json and every review_queue[] item
    against the QUEUED shape; Pass 2 validates the whole loaded document.

Single-writer note: canon.json has exactly one concurrent writer by
OPERATIONAL PRECONDITION -- the orchestrating Workflow serializes every
merge/verify call for one glossary pass onto a single Claude
`effort:"low"` invocation, never dispatches concurrent merges (see
references/orchestration-and-batching.md, "one serialized final merge").
This script performs no file locking of its own; it relies entirely on
that precondition, same as ledger_merge.py's own materialization step.

Reads canon-entry.schema.json / canon-batch.schema.json /
canon-file.schema.json from ${durable_root}/schemas/ -- never the plugin's
own assets/schemas/ (this script always runs from the durable, per-project
copy).

Usage:
    python3 canon_validate.py --research-mode offline --init
    python3 canon_validate.py --research-mode offline --restamp-derivation
    python3 canon_validate.py --research-mode live --check-batch out_0.json
    python3 canon_validate.py --research-mode live --check-batch out_0.json --expect-source-forms-file manifest_0.json
    python3 canon_validate.py --research-mode live --merge-batches out_0.json out_1.json
    python3 canon_validate.py --research-mode live --verify-merged --batch out_0.json --batch out_1.json --expect-source-forms-file manifest_all.json
    python3 canon_validate.py --research-mode live --batch glossary_out.json
    python3 canon_validate.py --research-mode offline --batch glossary_out.json
    python3 canon_validate.py --research-mode live
    python3 canon_validate.py --research-mode live --canon-path /path/to/canon.json
    python3 canon_validate.py --research-mode live --merge-batches out_0.json --senses-path /path/to/canon_senses.json

Exit code 0 on success, 1 on failure (for --verify-merged, "success" means
`verified: true`). Exactly one JSON line is printed to stdout either way --
callers (the glossary-pass Workflow, tests) should read stdout, not rely
on the exit code alone.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

try:
    import jsonschema
    from jsonschema.exceptions import best_match
    from referencing import Registry, Resource
except ImportError as e:
    sys.stderr.write(
        "canon_validate.py requires the 'jsonschema' package (>=4.26.0), "
        "which pulls in 'referencing' for $ref resolution across the "
        "canon-*.schema.json files. Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

# canon_senses.py is a sibling script under the same durable_root/scripts/
# (the loader+normalizer LEAF every Phase-1 consumer imports, RFC #215
# 1a'/1a'') -- its own jsonschema preflight already exits with an
# actionable message if THAT import fails, so no second try/except is
# needed here; a missing canon_senses.py module itself is a deployment
# bug, not a normal user-facing error.
from canon_senses import (
    CanonSensesLoadError,
    SensesResult,
    is_split,
    load_senses,
    normalize_form,
)

# Self-anchored: this script always lives at
# ${durable_root}/scripts/canon_validate.py, so parents[1] is the durable
# root. Never assumes cwd, never takes a --durable-root flag -- see
# references/ledger-and-resumability.md's "Script self-anchoring" invariant
# (the same rule applies to every copied script, not just the ledger ones).
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DEFAULT_CANON_PATH = DURABLE_ROOT / "canon.json"
# Sibling of DEFAULT_CANON_PATH -- self-anchored the same way, never
# cwd-relative. THE canonical default every Phase-1 consumer of
# canon_senses.json (this script's recollapse guard,
# canon_adjudication_audit.py, glossary_batch_plan.py) computes the same
# way: DURABLE_ROOT / "canon_senses.json".
DEFAULT_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"

RESEARCH_MODES = ("live", "offline")


class ModeSpec(NamedTuple):
    """One selectable CLI mode, declared once.

    flag     -- the spelling used in error messages.
    dest     -- the argparse destination the flag writes to, or None for the
                one mode that no single flag selects: the LEGACY bare-`--batch`
                merge, which is by definition "what you get when no mode flag
                was given". It still gets a row so it inherits every column
                below (and every column added later) instead of escaping the
                table-driven guards, which is exactly how it came to silently
                ignore --expect-source-forms-file. `_selected_modes()` owns
                the one-line special case that selects it; nothing else in
                this file needs to know it is unusual.
    batch_ok -- may a bare `--batch` accompany this mode? Only
                --verify-merged, where --batch NAMES the already-processed
                fragments to verify.
    source_forms_refusal -- None when --expect-source-forms-file is accepted
                with this mode; otherwise the REASON it is refused, shown to
                the operator verbatim.

    That last field deliberately carries both the predicate and its
    explanation in one place. The earlier shape asked "does this mode read a
    fragment?", which is a DIFFERENT question from "does it accept
    --expect-source-forms-file" -- --merge-batches reads fragments yet
    refuses the flag, so it slipped past the table-driven guard and had to be
    caught by a hardcoded `args.merge_batches is not None` check further
    down: the exact per-mode magic this table exists to eliminate. Folding
    the reason in rather than adding a separate bool makes "refused with no
    stated reason" and "a stale reason on a mode that accepts it" both
    unrepresentable, and keeps the two genuinely different explanations
    (nothing to check coverage against vs coverage is enforced elsewhere)
    instead of flattening them into one generic message.
    """

    flag: str
    dest: "str | None"
    batch_ok: bool
    source_forms_refusal: "str | None"


# EVERY mode, declared exactly ONCE, carrying the per-mode facts main()'s
# CROSS-FLAG guards need. That includes the legacy bare-`--batch` merge, which
# no single flag selects and which therefore carries `dest=None` -- see
# ModeSpec.dest and `_selected_modes()`. It is IN the table deliberately:
# while it sat outside, it selected no spec and so escaped every table-driven
# guard, which is precisely how it came to accept `--expect-source-forms-file`
# and silently never enforce coverage while returning `{"success": true}`.
# Giving it a row fixed that with no new guard, and means it inherits every
# column added here later instead of needing to be remembered each time.
#
# What this guarantees, precisely, and ONLY for modes a parser flag selects:
# the three cross-flag guards below (mutual exclusion, --batch
# compatibility, --expect-source-forms-file acceptance) are comprehensions
# over this table, so none of them can be taught about a new FLAG-SELECTED
# mode while another silently is not, and
# tests/canon_stamp_conservation.test.py fails if a parser flag is missing
# from the table or vice versa -- that row is unforgettable.
#
# The scope limit is real, not theoretical: VALIDATE-ONLY, the default mode
# reached when no mode flag is passed at all, has no flag, no dest and no row
# here, so it sits outside the table and outside every guarantee above. Any
# future flagless mode inherits the same gap. The drift test compares parser
# dests against table dests, so it cannot see a mode that has neither.
#
# What it does NOT guarantee: adding a mode is still THREE edits -- a row
# here, an `add_argument()` in build_arg_parser(), and a dispatch branch in
# main(). Two guards also remain hardcoded per-flag (`--verify-merged`
# requires `--batch`; `--batch` is repeatable only under it), because both
# express a REQUIRES relation between two specific flags rather than a
# per-mode property. They are expressible as table columns if that ever
# earns its keep; today it would be a column with one meaningful row.
#
# The reason the table exists at all: the previous shape kept a second,
# hand-maintained subset tuple plus a `!= "--verify-merged"` magic string,
# so a new mode could be added to one guard and missed by another -- the
# same two-hand-maintained-lists defect this release fixes in
# select_segments.py's FIELD_TO_REGEN_STEP.
_READS_NO_FRAGMENT = "it reads no fragment"
_COVERAGE_ENFORCED_ELSEWHERE = (
    "coverage is enforced by --check-batch per fragment and by "
    "--verify-merged for the merged set"
)

MODE_SPECS = (
    ModeSpec("--init", "init", batch_ok=False, source_forms_refusal=_READS_NO_FRAGMENT),
    ModeSpec(
        "--restamp-derivation",
        "restamp_derivation",
        batch_ok=False,
        source_forms_refusal=_READS_NO_FRAGMENT,
    ),
    ModeSpec("--check-batch", "check_batch", batch_ok=False, source_forms_refusal=None),
    ModeSpec(
        "--merge-batches",
        "merge_batches",
        batch_ok=False,
        source_forms_refusal=_COVERAGE_ENFORCED_ELSEWHERE,
    ),
    ModeSpec("--verify-merged", "verify_merged", batch_ok=True, source_forms_refusal=None),
    # The legacy bare-`--batch` merge. batch_ok=True is load-bearing, not
    # cosmetic: `--batch` IS this mode's own selector, so a False here would
    # make the --batch-compatibility guard fire on the mode itself.
    ModeSpec(
        "--batch (legacy single-fragment merge)",
        None,
        batch_ok=True,
        source_forms_refusal=_COVERAGE_ENFORCED_ELSEWHERE,
    ),
)


def _selected_modes(args) -> list:
    """The mode(s) this invocation selects -- at most one in practice.

    The flag-selected rows are checked first; the legacy bare-`--batch` merge
    is appended ONLY when none of them matched, because that is precisely its
    definition. Doing it in that order is what keeps `--verify-merged --batch
    F1` legal: there `--batch` is --verify-merged's own value-carrying flag,
    --verify-merged matches first, and the legacy row is never considered --
    so mutual exclusion cannot fire on a legitimate combination.

    This ordering is the ONE special case the legacy mode needs. Every guard
    downstream then treats it as an ordinary row.
    """
    selected = [spec for spec in MODE_SPECS if spec.dest and _mode_selected(args, spec)]
    if not selected and args.batch is not None:
        selected = [spec for spec in MODE_SPECS if spec.dest is None]
    return selected

# Parser destinations that are OPTIONS, not modes -- the complement of
# MODE_SPECS across the whole parser. Named here rather than inside the test
# so the script itself owns the mode/option distinction.
NON_MODE_DESTS = frozenset(
    {"help", "research_mode", "batch", "expect_source_forms_file", "canon_path", "senses_path"}
)


def _mode_selected(args, spec: "ModeSpec") -> bool:
    """Whether `spec`'s flag was given. Uniform across store_true flags
    (False when absent) and value-carrying flags (None when absent), without
    `==` comparisons that would treat an empty-string argument as absent."""
    value = getattr(args, spec.dest)
    return value is not None and value is not False


# The two global generation_hashes fields canon.json stamps at merge time
# (references/canon-and-glossary.md, "Bootstrap sequence" step 4) -- both
# computed via cache_key.py --field <name>, never independently recomputed
# here.
GENERATION_HASH_FIELDS = ("particle_config_hash", "derivation_bundle_hash")

# canon-entry.schema.json's own shape has no 'disposition' property and is
# additionalProperties:false -- this is the exact set of keys an ACCEPTED
# batch item may carry once merged into entries{}.
CANON_ENTRY_FIELDS = (
    "source_form",
    "is_proper_name",
    "canonical_target_form",
    "basis",
    "source",
    "confidence",
    "note",
    "category",
)


class CanonValidationError(Exception):
    """Raised for any failure that should surface as a FAILURE result.

    `offending`, when not None, is folded into the failure payload verbatim
    -- naming which batch items / entries triggered the failure, so a
    caller never has to re-derive that from a bare error string.
    """

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


# ---------------------------------------------------------------------------
# Schema loading / registry (mirrors ledger_merge.py's own pattern exactly,
# with format_checker added explicitly -- canon-entry.schema.json is the
# one shipped schema that actually needs a real format assertion).
# ---------------------------------------------------------------------------


def _load_schema_document(schema_path: Path) -> dict:
    if not schema_path.is_file():
        raise CanonValidationError(f"schema file not found: {schema_path}")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CanonValidationError(f"invalid JSON in schema {schema_path.name}: {e}")


def _build_schema_registry() -> "Registry":
    """Registers every *.schema.json file under SCHEMAS_DIR by its own
    `$id` (a bare filename, per this project's convention -- e.g.
    "canon-entry.schema.json"), so canon-file.schema.json's/canon-batch's
    $refs to those filenames resolve regardless of load order.
    """
    if not SCHEMAS_DIR.is_dir():
        raise CanonValidationError(f"schemas directory not found: {SCHEMAS_DIR}")
    resources = []
    for schema_file in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        contents = _load_schema_document(schema_file)
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    if not resources:
        raise CanonValidationError(f"no *.schema.json files found under {SCHEMAS_DIR}")
    return Registry().with_resources(resources)


def _draft202012_validator(schema: dict, registry: "Registry") -> "jsonschema.Draft202012Validator":
    """Constructs a jsonschema.Draft202012Validator with format_checker=
    _uri_format_checker() set EXPLICITLY. This is REQUIRED, not optional --
    jsonschema.validate()'s convenience wrapper does not enable format
    assertions by default, and canon-entry.schema.json's established->URI
    conditional depends entirely on the 'uri' format assertion actually
    running.
    """
    return jsonschema.Draft202012Validator(
        schema, registry=registry, format_checker=_uri_format_checker()
    )


def _validator_for_schema_file(schema_filename: str, registry: "Registry"):
    schema = _load_schema_document(SCHEMAS_DIR / schema_filename)
    return _draft202012_validator(schema, registry)


def _validator_for_ref(ref: str, registry: "Registry"):
    """Builds a validator for a bare $ref pointer into an already-registered
    schema document (e.g. "canon-batch.schema.json#/items" for the
    discriminated-union item shape, or
    "canon-batch.schema.json#/items/oneOf/1" for the QUEUED branch alone --
    the exact same pointer canon-file.schema.json itself uses for
    review_queue[] items).
    """
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": ref,
    }
    return _draft202012_validator(wrapper, registry)


def _error_path_key(error):
    """Stable sort key for jsonschema ValidationError instances by
    instance-path, used to make error output deterministic across runs."""
    return [str(p) for p in error.path]


def _sorted_errors(validator, instance):
    """Runs validator.iter_errors() and returns the errors sorted by
    instance-path -- the one canonical way this script orders
    ValidationErrors for deterministic reporting."""
    return sorted(validator.iter_errors(instance), key=_error_path_key)


def _is_boolean_false_property_error(e) -> bool:
    """True for the exact shape D17 identifies: a `properties` keyword
    rejecting a value against a boolean-`false` subschema (e.g.
    `"source": false`) reports with `validator=None` and a `schema_path`
    ending at 'properties' -- NOT at the property's own name. Verified
    empirically against jsonschema==4.26.0."""
    return e.validator is None and bool(e.schema_path) and e.schema_path[-1] == "properties"


def _forbidden_keys_for_schema(schema, instance) -> list:
    """D17(b) key-recovery. A boolean-`false` property rejection reports at
    the PARENT with the offending key STRIPPED -- `absolute_path=[]`, the
    message is a bare "False schema does not allow '<value>'" naming the
    REJECTED VALUE, never the key (verified: `jsonschema==4.26.0`). Neither
    the path nor the message can name the key, so the schema itself is
    walked directly instead: every `allOf` clause whose `if` the instance
    actually satisfies (a plain const-on-one-property check, matching this
    plugin's own conditional shape), intersected against that clause's own
    false-valued `then.properties` keys the instance actually carries.
    Works uniformly against canon-entry.schema.json's own top-level shape
    and a single canon-batch.schema.json `oneOf` branch's shape -- both
    carry the identical `allOf:[{if,then}, ...]` structure (S1/TP-2c)."""
    if not isinstance(schema, dict) or not isinstance(instance, dict):
        return []
    offending = set()
    for clause in schema.get("allOf", []):
        if_props = clause.get("if", {}).get("properties", {})
        if not if_props:
            continue
        satisfied = all(
            isinstance(cond, dict) and "const" in cond and instance.get(prop) == cond["const"]
            for prop, cond in if_props.items()
        )
        if not satisfied:
            continue
        then_props = clause.get("then", {}).get("properties", {})
        offending.update(k for k, v in then_props.items() if v is False and k in instance)
    return sorted(offending)


def _format_single_error(e, prefix: str = "") -> str:
    loc = "/".join(str(p) for p in e.path) or "<root>"
    return f"{prefix}at '{loc}': {e.message}"


def _forbidden_keys_message(keys, basis, prefix: str = "") -> str:
    """Shared D17(b) rendering -- "property <key> is forbidden for basis
    <basis>", joined over every recovered forbidden key. `prefix` lets the
    oneOf-branch path prepend its "<label> item: " marker without repeating
    the (test-pinned) offending-token string in two places."""
    return "; ".join(
        f"{prefix}property {key!r} is forbidden for basis {basis!r}" for key in keys
    )


def _disposition_const_mismatch(subs) -> bool:
    """True iff one of `subs` (one oneOf branch's own sub-errors) is a
    `const` failure on the top-level 'disposition' property -- i.e. this
    branch's own disposition constant does not match the instance's, so it
    is NOT the branch the instance's own discriminator selects."""
    return any(
        sub.validator == "const" and list(sub.absolute_path) == ["disposition"] for sub in subs
    )


def _format_oneof_branch_sub_error(sub, branch_schema, branch_label, instance) -> str:
    if _is_boolean_false_property_error(sub):
        keys = _forbidden_keys_for_schema(branch_schema, instance)
        if keys:
            return _forbidden_keys_message(
                keys, instance.get("basis"), prefix=f"{branch_label} item: "
            )
    return _format_single_error(sub, prefix=f"{branch_label} item ")


def _format_oneof_error(e, instance) -> str:
    """D17(a)+(b)+(c) for a `oneOf`/`anyOf` failure (canon-batch.schema.json's
    discriminated union): through a bare `oneOf`, `e.message` is a whole-
    instance dump -- printing only that leaves the retry agent nothing
    actionable (the T-14 hang by another road). Instead: (a) select the
    branch matching the instance's own 'disposition' (dropping the sibling
    branch's discriminator-mismatch sub-errors); (b) recover a boolean-false
    property rejection's offending key via _forbidden_keys_for_schema;
    (c) on an absent/non-string/unrecognized 'disposition' (matches neither
    branch, or -- a malformed value -- mismatches both), fall back to
    jsonschema's own best_match across every branch's sub-errors, so an
    invalid item still gets an actionable message, never empty or a crash.
    """
    branches = e.schema.get("oneOf") if isinstance(e.schema, dict) else None
    context = list(e.context or [])
    by_branch = {}
    for sub in context:
        if sub.schema_path:
            by_branch.setdefault(sub.schema_path[0], []).append(sub)

    candidates = [idx for idx, subs in by_branch.items() if not _disposition_const_mismatch(subs)]

    if len(candidates) == 1:
        idx = candidates[0]
        branch_schema = branches[idx] if isinstance(branches, list) and idx < len(branches) else {}
        # Label by the branch's own 'disposition' const VALUE (e.g.
        # "accepted"/"review_queue") -- the same lowercase, machine-value
        # form the instance itself carries -- falling back to the branch's
        # schema 'title' only if that const is somehow unavailable.
        if not isinstance(branch_schema, dict):
            branch_label = f"branch[{idx}]"
        else:
            branch_disposition = (
                branch_schema.get("properties", {}).get("disposition", {}).get("const")
            )
            if isinstance(branch_disposition, str):
                branch_label = branch_disposition
            else:
                branch_label = branch_schema.get("title", f"branch[{idx}]")
        return "; ".join(
            _format_oneof_branch_sub_error(sub, branch_schema, branch_label, instance)
            for sub in by_branch[idx]
        )

    # (c) fallback -- disposition absent/non-string, or unrecognized (matches
    # neither branch, or a malformed value mismatches both): never empty or a
    # crash. jsonschema's own best_match ranks the single most-specific
    # sub-error across every branch; if context itself is empty, fall back to
    # the oneOf error's own (whole-instance-dump) message rather than nothing.
    if context:
        best = best_match(context)
        if best is not None:
            return _format_single_error(
                best, prefix="(disposition absent/unrecognized -- best match across all branches) "
            )
    return _format_single_error(e)


def _format_errors(errors, instance=None, root_schema=None) -> str:
    parts = []
    for e in errors:
        if e.validator in ("oneOf", "anyOf") and isinstance(instance, dict):
            parts.append(_format_oneof_error(e, instance))
        elif _is_boolean_false_property_error(e) and isinstance(instance, dict):
            keys = _forbidden_keys_for_schema(root_schema, instance)
            if keys:
                parts.append(_forbidden_keys_message(keys, instance.get("basis")))
            else:
                parts.append(_format_single_error(e))
        else:
            parts.append(_format_single_error(e))
    return "; ".join(parts)


def _indexed_item_label(kind: str, index: int, item) -> str:
    """Builds a "kind[i]" or "kind[i] ('source_form')" label for a batch or
    review_queue item, so a Pass-1 failure names exactly which item broke."""
    source_form = item.get("source_form") if isinstance(item, dict) else None
    return f"{kind}[{index}]" + (f" ({source_form!r})" if source_form else "")


def _is_uri(value: str) -> bool:
    """A value is a valid URI iff urllib.parse.urlparse() yields BOTH a
    non-empty scheme AND a non-empty netloc -- i.e. a real absolute URL
    like "https://host/path", not a bare path or a scheme-less string.
    """
    parsed = urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc)


def _check_uri_format(value) -> bool:
    """The 'uri' format checker registered on _uri_format_checker()'s
    FormatChecker. jsonschema's format_checker protocol requires a checker
    to either return a bool or raise one of its declared `raises` types --
    never silently no-op -- so a malformed value raises ValueError rather
    than returning False, matching how fc.checks(..., raises=(ValueError,))
    is registered below.
    """
    if not isinstance(value, str):
        return True  # format checks only apply to strings; type is schema's job
    if not _is_uri(value):
        raise ValueError(f"{value!r} is not a valid URI (need scheme + netloc)")
    return True


def _uri_format_checker() -> "jsonschema.FormatChecker":
    """Builds a jsonschema.FormatChecker with a stdlib urllib.parse-based
    'uri' checker registered on it, so canon-entry.schema.json's
    basis:"established" -> source:{format:"uri"} conditional is enforced
    deterministically regardless of whether the optional (GPLv3+) 'rfc3987'
    package is installed -- this plugin is intentionally stdlib-first and
    never adds rfc3987 to requirements.txt. Registering a custom 'uri'
    checker overrides jsonschema's own (rfc3987-backed, otherwise-no-op)
    default for that format name.
    """
    fc = jsonschema.FormatChecker()
    fc.checks("uri", raises=(ValueError,))(_check_uri_format)
    return fc


# ---------------------------------------------------------------------------
# canon.json I/O
# ---------------------------------------------------------------------------


def _read_json_file(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CanonValidationError(f"{what} not found at {path}")
    except OSError as e:
        raise CanonValidationError(f"could not read {what} at {path}: {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CanonValidationError(f"{what} at {path} is not valid JSON: {e}")


def _load_canon(canon_path: Path) -> dict:
    """Loads canon.json, or -- if it does not exist yet -- returns a fresh
    skeleton (entries={}, review_queue=[]) for MERGE mode's very first
    glossary-pass batch on a brand-new project. generation_hashes is left
    absent here; MERGE mode always stamps it fresh before writing, and
    VALIDATE-ONLY mode on a not-yet-existing file is a hard error (nothing
    to validate).

    For an EXISTING file, the raw parsed shape is returned exactly as
    loaded -- 'entries'/'review_queue' are NEVER defaulted into existence
    here. Autofilling a missing top-level key before whole-file schema
    validation (Pass 2, `_validate_whole_file` against
    canon-file.schema.json) has run would silently paper over a genuinely
    malformed canon.json that is missing a required top-level field --
    exactly the failure Pass 2 exists to catch loudly, by name. Only the
    TYPE of an already-present key is checked here; presence itself is
    schema-validated downstream, never assumed here.
    """
    if not canon_path.is_file():
        return {"entries": {}, "review_queue": []}
    doc = _read_json_file(canon_path, "canon.json")
    if not isinstance(doc, dict):
        raise CanonValidationError(f"canon.json at {canon_path} is not a JSON object")
    if "entries" in doc and not isinstance(doc["entries"], dict):
        raise CanonValidationError(f"canon.json at {canon_path}: 'entries' is not an object")
    if "review_queue" in doc and not isinstance(doc["review_queue"], list):
        raise CanonValidationError(f"canon.json at {canon_path}: 'review_queue' is not an array")
    return doc


def _load_senses_or_raise(senses_path: Path, allow_absent: bool) -> "SensesResult":
    """Wraps canon_senses.py's `load_senses`, translating a
    CanonSensesLoadError into this module's own CanonValidationError so a
    blocked sidecar load (a schema failure, a typo'd --senses-path, a
    non-regular path) surfaces through the same {"success": false,
    "error": ...} JSON failure payload as every other failure this script
    raises -- never the generic "unexpected error" catch-all in main().
    """
    try:
        return load_senses(senses_path, allow_absent=allow_absent)
    except CanonSensesLoadError as e:
        raise CanonValidationError(str(e), offending=e.offending)


def _load_batch(batch_path_str: str) -> list:
    batch_path = Path(batch_path_str)
    doc = _read_json_file(batch_path, "batch file")
    if not isinstance(doc, list):
        raise CanonValidationError(f"batch file at {batch_path} does not contain a JSON array")
    return doc


def _load_source_forms_manifest(manifest_path_str: str) -> list:
    """Loads --expect-source-forms-file's own JSON array of expected
    source_form strings -- always a FILE, never inline argv (the manifest
    can be arbitrarily long and may contain names with spaces/apostrophes/
    unicode, none of which belong on a command line)."""
    manifest_path = Path(manifest_path_str)
    doc = _read_json_file(manifest_path, "--expect-source-forms-file")
    if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
        raise CanonValidationError(
            f"--expect-source-forms-file at {manifest_path} must be a JSON "
            f"array of strings"
        )
    return doc


def _assert_exact_source_form_coverage(items: list, expected_forms: list) -> None:
    """Asserts the set of source_form values across `items` EXACTLY equals
    `expected_forms` -- no missing, no extra. Raises CanonValidationError
    naming both sides of any discrepancy (mirrors the naming discipline of
    every other CanonValidationError raised in this module)."""
    got = {item.get("source_form") for item in items if isinstance(item, dict)}
    want = set(expected_forms)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing from batch: {missing}")
        if extra:
            parts.append(f"unexpected extra in batch: {extra}")
        raise CanonValidationError(
            "batch does not exactly cover the expected source_form "
            "manifest (" + "; ".join(parts) + ")",
            offending=missing + extra,
        )


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _stamp_generation_hash(field: str) -> str:
    """Shells out to `cache_key.py --field <field>` -- the one shared
    hashing implementation -- and returns its bare stdout value, stripped.
    Never independently recomputed here. A missing/failing cache_key.py, or
    an empty value, is FATAL: canon-file.schema.json only requires the
    generation_hashes KEYS be present strings, it cannot itself catch an
    empty-but-present value, so this script must refuse to write one.
    """
    if not CACHE_KEY_SCRIPT.is_file():
        raise CanonValidationError(
            f"cannot stamp generation_hashes.{field}: {CACHE_KEY_SCRIPT} not found"
        )
    try:
        proc = subprocess.run(
            [sys.executable, str(CACHE_KEY_SCRIPT), "--field", field],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(DURABLE_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CanonValidationError(f"could not run cache_key.py --field {field}: {e}")
    if proc.returncode != 0:
        raise CanonValidationError(
            f"cache_key.py --field {field} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    value = proc.stdout.strip()
    if not value:
        raise CanonValidationError(f"cache_key.py --field {field} printed an empty value")
    return value


# ---------------------------------------------------------------------------
# Pass 1 -- per-item validation
# ---------------------------------------------------------------------------


def _validate_batch_items(batch: list, registry: "Registry") -> None:
    """Pass 1, MERGE mode: every batch item independently re-validated
    against canon-batch.schema.json's own discriminated-union item shape
    (canon-batch.schema.json#/items) -- never the bare
    canon-entry.schema.json shape, since a batch item also carries
    'disposition'. Raises CanonValidationError naming every offending item
    (by index and, when present, source_form) if any item fails.
    """
    validator = _validator_for_ref("canon-batch.schema.json#/items", registry)
    problems = []
    for i, item in enumerate(batch):
        errors = _sorted_errors(validator, item)
        if errors:
            label = _indexed_item_label("batch", i, item)
            problems.append(f"{label}: {_format_errors(errors, instance=item)}")
    if problems:
        raise CanonValidationError(
            "batch failed per-item schema validation:\n  " + "\n  ".join(problems),
            offending=problems,
        )


def _validate_existing_entries(canon: dict, registry: "Registry") -> None:
    """Pass 1, VALIDATE-ONLY mode: every entries{} value validated against
    canon-entry.schema.json directly, and every review_queue[] item against
    the QUEUED shape -- the same per-item discipline as MERGE mode's Pass 1,
    applied to an already-merged file instead of an incoming batch.
    """
    entry_validator = _validator_for_schema_file("canon-entry.schema.json", registry)
    queued_validator = _validator_for_ref("canon-batch.schema.json#/items/oneOf/1", registry)
    problems = []

    for source_form, entry in sorted(canon.get("entries", {}).items()):
        errors = _sorted_errors(entry_validator, entry)
        if errors:
            formatted = _format_errors(errors, instance=entry, root_schema=entry_validator.schema)
            problems.append(f"entries[{source_form!r}]: {formatted}")

    for i, item in enumerate(canon.get("review_queue", [])):
        errors = _sorted_errors(queued_validator, item)
        if errors:
            label = _indexed_item_label("review_queue", i, item)
            problems.append(f"{label}: {_format_errors(errors, instance=item)}")

    if problems:
        raise CanonValidationError(
            "canon.json failed per-item schema validation:\n  " + "\n  ".join(problems),
            offending=problems,
        )


# ---------------------------------------------------------------------------
# Offline research-mode backstop
# ---------------------------------------------------------------------------


def _enforce_offline_backstop(batch: list, research_mode: str) -> None:
    """If research_mode == "offline", FATALLY rejects the whole batch merge
    when ANY item claims basis:"established" -- accepted or queued alike,
    matching the authoritative spec's literal "ANY entry" wording. Nothing
    is written to canon.json when this fires; the correct fix is upstream,
    in the glossary-pass agent's own output (basis:"transliterated" if
    mechanical transliteration suffices, basis:"sense_translated" if a
    project-specific editorial sense-rendering fits -- both are offline-legal,
    neither needs an external citation -- or disposition:"review_queue" with
    a note:"SOURCE_UNAVAILABLE: ..." prefix), never a silent downgrade
    performed by this script.
    """
    if research_mode != "offline":
        return
    offenders = [
        item.get("source_form", f"<item {i}>")
        for i, item in enumerate(batch)
        if isinstance(item, dict) and item.get("basis") == "established"
    ]
    if offenders:
        raise CanonValidationError(
            "research_mode=offline forbids basis:\"established\" for every new "
            "entry, but the batch claims it for: " + ", ".join(repr(o) for o in offenders)
            + ". Reassign basis:\"transliterated\" (if mechanical transliteration "
            "suffices), basis:\"sense_translated\" (if a project-specific editorial "
            "sense-rendering fits -- style_bible.md §C -- no external citation "
            "needed), or disposition:\"review_queue\" with a note carrying the "
            "literal prefix \"SOURCE_UNAVAILABLE:\" instead -- the whole batch "
            "merge is rejected, canon.json is unchanged.",
            offending=offenders,
        )


# ---------------------------------------------------------------------------
# Merge (dedup + collision checks, routing by disposition)
# ---------------------------------------------------------------------------


def _entry_from_accepted_item(item: dict) -> dict:
    """Strips 'disposition' (and any other non-canon-entry key) from an
    ACCEPTED batch item, leaving exactly canon-entry.schema.json's own
    field set -- that schema is additionalProperties:false and has no
    'disposition' property at all.
    """
    return {k: item[k] for k in CANON_ENTRY_FIELDS if k in item}


def _matching_senses_entry(senses: "SensesResult", source_form) -> "dict | None":
    """Returns the canon_senses.json entry matching `source_form` -- via the
    SAME normalize_form comparison `is_split` uses internally -- or None if
    none matches. Used only to recover the sense COUNT for the recollapse
    refusal message below; the split predicate itself is always `is_split`,
    never re-derived here.

    Uses `senses.normalized_index` for an O(1) lookup when present (built
    once by `load_senses`, same field `is_split` itself reads); falls back
    to the original O(n) per-call linear scan only when `senses` was
    constructed directly without an index -- both paths return the exact
    same entry."""
    target = normalize_form(source_form) if isinstance(source_form, str) else source_form
    if senses.normalized_index is not None:
        return senses.normalized_index.get(target)
    for key, entry in senses.entries_by_source_form.items():
        if normalize_form(key) == target:
            return entry
    return None


def _merge_batch(canon: dict, batch: list, senses: "SensesResult") -> dict:
    """Merges a Pass-1-validated, offline-backstop-cleared batch into an
    in-memory copy of `canon`. Never mutates `canon` in place, and never
    touches disk -- the caller writes only after this returns successfully.
    Raises CanonValidationError (naming both old and new values) on a
    genuine cross-run collision: two different resolutions claimed for the
    same source_form. An identical re-submission is a silent no-op.

    Refuse-recollapse guard (RFC #215 1d): an ACCEPTED item whose
    source_form is an adjudicated homonym split in `senses` (>=2 senses,
    normalized-compared via canon_senses.py's `is_split`) is refused
    outright, before any existing-entry lookup -- so this covers a
    brand-new insertion, an overwrite, AND a resubmission alike, never just
    a collision against a pre-existing bare entry.
    """
    entries = dict(canon.get("entries", {}))
    review_queue = list(canon.get("review_queue", []))
    collisions = []

    for item in batch:
        disposition = item.get("disposition")
        source_form = item.get("source_form")

        if disposition == "accepted":
            if is_split(senses, source_form):
                split_entry = _matching_senses_entry(senses, source_form)
                n = len(split_entry.get("senses", [])) if split_entry else 0
                collisions.append(
                    f"{source_form!r}: is an adjudicated homonym split "
                    f"({n} senses in canon_senses.json) -- refusing to merge "
                    f"as a single bare entry (recollapse)"
                )
                continue
            new_entry = _entry_from_accepted_item(item)
            existing = entries.get(source_form)
            if existing is not None and existing != new_entry:
                collisions.append(
                    f"{source_form!r}: existing entry {existing!r} conflicts with "
                    f"newly merged {new_entry!r}"
                )
                continue
            entries[source_form] = new_entry
            # A name that is now resolved and accepted no longer belongs in
            # review_queue -- drop any queued entries for the same
            # source_form, since it has just been frozen.
            review_queue = [
                q for q in review_queue if q.get("source_form") != source_form
            ]

        elif disposition == "review_queue":
            if source_form in entries:
                # Already resolved/accepted -- either from a prior
                # canon.json, or from an EARLIER item in this same batch
                # (or an earlier fragment, when this is called repeatedly
                # by run_merge_batches's threaded acc = _merge_batch(acc,
                # frag) loop) -- a review_queue submission for an
                # already-accepted source_form is superseded, never
                # appended, regardless of merge order.
                continue
            if item not in review_queue:
                review_queue.append(item)

        else:  # pragma: no cover -- Pass 1 schema validation already rejects this
            collisions.append(
                f"{source_form!r}: unrecognized disposition {disposition!r}"
            )

    if collisions:
        raise CanonValidationError(
            "batch merge rejected due to entries{} collision(s):\n  "
            + "\n  ".join(collisions),
            offending=collisions,
        )

    merged = dict(canon)
    merged["entries"] = entries
    merged["review_queue"] = review_queue
    return merged


# ---------------------------------------------------------------------------
# Pass 2 -- whole-file validation
# ---------------------------------------------------------------------------


def _assert_no_entries_review_queue_overlap(canon: dict) -> None:
    """Whole-file invariant (issue #102): no source_form may be both a key
    in entries{} AND appear as a review_queue[] item's source_form -- this
    module's own stated invariant ("a name that is now resolved and
    accepted no longer belongs in review_queue", see _merge_batch's
    accepted-item branch). _merge_batch's own review_queue-append guard
    (`if source_form in entries: continue`) keeps this true for anything
    merged through this script, but a hand-corrupted or otherwise
    not-merged-through-_merge_batch canon.json is not itself schema-
    constrained against it -- a cross-key constraint spanning two top-level
    collections is awkward to express as a JSON-schema `not`-clause, so it
    is checked here directly instead.
    """
    entries_forms = set(canon.get("entries", {}).keys())
    queued_forms = {
        item.get("source_form")
        for item in canon.get("review_queue", [])
        if isinstance(item, dict) and isinstance(item.get("source_form"), str)
    }
    overlap = sorted(entries_forms & queued_forms)
    if overlap:
        raise CanonValidationError(
            "canon.json failed whole-file invariant: source_form(s) present "
            "in both entries{} and review_queue[]: "
            + ", ".join(repr(o) for o in overlap),
            offending=overlap,
        )


def _validate_whole_file(canon: dict, registry: "Registry") -> None:
    validator = _validator_for_schema_file("canon-file.schema.json", registry)
    errors = _sorted_errors(validator, canon)
    if errors:
        raise CanonValidationError(
            f"canon.json failed whole-file schema validation: {_format_errors(errors)}"
        )
    _assert_no_entries_review_queue_overlap(canon)


# ---------------------------------------------------------------------------
# Write path -- stamping policy shared by every mode that writes canon.json
# ---------------------------------------------------------------------------


def _content_view(doc: dict) -> dict:
    """The part of a canon.json document that generation_hashes is a
    provenance claim ABOUT -- i.e. everything except the stamp itself.
    Equality here is the definition of "this merge changed nothing" (#291).

    Deliberately whole-document rather than just entries{}: review_queue[] is
    schema-required content, is written by the merge, and is read back by
    glossary_batch_plan.py (a queued name is excluded from re-research), so a
    review_queue-only merge genuinely changed what this file does and MUST
    re-stamp. Equality is plain `==`, so list order counts as content --
    entries{} is written with sort_keys=True so its order is not observable,
    and review_queue[] is only ever filtered/appended by `_merge_batch`, so a
    pure reorder is not reachable today; treating one as a change is the
    safe direction if that ever stops being true.
    """
    return {k: v for k, v in doc.items() if k != "generation_hashes"}


def _preservable_prior(canon_path: Path) -> "dict | None":
    """The current on-disk canon.json when its generation_hashes stamp is
    trustworthy enough to CARRY FORWARD across a no-op merge, else None
    (meaning: stamp fresh, exactly as every merge did before #291).

    Read from disk rather than taken from the caller's own pre-merge `canon`
    object on purpose: this is the single choke point every writing mode goes
    through, and re-reading makes the guard immune to any future refactor
    that starts mutating the merge accumulator in place. `_merge_batch`
    promises it never does, but a correctness guard should not rest on a
    docstring.

    None is returned for a missing file (nothing to preserve -- a fresh
    bootstrap must stamp) and for a stamp that is absent, non-object,
    incomplete, non-string, or EMPTY. The empty case matters: canon-file.
    schema.json types these fields as plain strings and so cannot reject "",
    while `_stamp_generation_hash` refuses to WRITE one -- preserving an
    empty value would smuggle past exactly the guard that exists to stop it.
    Re-stamping instead keeps a corrupt stamp self-healing.
    """
    if not canon_path.is_file():
        return None
    try:
        prior = _load_canon(canon_path)
    except CanonValidationError:
        # An unparseable/malformed canon.json is not a trustworthy source of
        # provenance; let the normal stamp+validate path surface it.
        return None
    stamp = prior.get("generation_hashes")
    if not isinstance(stamp, dict):
        return None
    for field in GENERATION_HASH_FIELDS:
        value = stamp.get(field)
        if not isinstance(value, str) or not value:
            return None
    return prior


def _stamp_write_verify(
    canon_path: Path, merged: dict, registry: "Registry", force_restamp: bool = False
) -> "tuple[dict, bool]":
    """Shared by every mode that writes canon.json (`run_merge`,
    `run_merge_batches`, `run_init`, `run_restamp_derivation`): resolves
    generation_hashes onto the in-memory `merged` document,
    Pass-2-validates it BEFORE ever touching disk (so a corrupted merge is
    caught before it's written, not just after), performs ONE atomic write,
    then re-reads the JUST-WRITTEN file fresh from disk and Pass-2-validates
    it AGAIN -- genuinely from disk, with NO masking fallback for a missing
    generation_hashes value (the pre-1.2.0 version of this function
    re-injected the just-stamped value via
    `on_disk.setdefault("generation_hashes", ...)` here, which silently
    defeated the whole point of the post-write re-read: a write that
    somehow dropped generation_hashes would still "validate" against the
    value this script itself remembered, not what actually landed on disk).

    1.15.0 (#291) -- CONSERVE THE STAMP ON A NO-OP. This function used to
    re-stamp unconditionally, which meant any merge advanced canon.json's
    provenance claim even when it merged nothing into the document. Since
    segpack.py copies these two hashes verbatim into every pack and
    select_segments.py's derivation-state gate compares that copy against a
    freshly computed cache_key.py value, an unconditional re-stamp let a
    content-free merge clear `blocked_needs_regeneration` without anything
    having been regenerated -- segments then read as caught-up and stale
    output ships. The hole was NOT limited to an empty fragment:
    `_merge_batch` treats an identical re-submission as a silent no-op, so a
    fully populated fragment of already-merged items changed nothing either
    while still reporting merged_accepted > 0. The check therefore keys on
    whether the DOCUMENT changed, never on the fragment's item count.

    `force_restamp=True` is the explicit, operator-driven override
    (`--restamp-derivation`) -- the sanctioned replacement for the
    `--merge-batches <empty-batch.json>` trick issue #193 documents as its
    only, unsanctioned restamp path.

    Returns `(freshly re-read on-disk document, restamped)`.
    """
    # This re-reads canon.json from disk even though the caller already holds
    # a pre-merge copy. That is DELIBERATE, not redundant I/O -- see
    # _preservable_prior's docstring: reading here keeps the guard correct
    # even if a future refactor starts mutating the merge accumulator in
    # place, rather than resting on _merge_batch's docstring promise.
    prior = None if force_restamp else _preservable_prior(canon_path)
    restamped = prior is None or _content_view(merged) != _content_view(prior)

    if restamped:
        merged.setdefault("generation_hashes", {})
        for field in GENERATION_HASH_FIELDS:
            merged["generation_hashes"][field] = _stamp_generation_hash(field)
    else:
        # Carry the existing stamp forward verbatim (extra keys included --
        # this function never edits provenance it did not compute).
        merged["generation_hashes"] = dict(prior["generation_hashes"])

    _validate_whole_file(merged, registry)

    _atomic_write_json(canon_path, merged)

    on_disk = _load_canon(canon_path)
    _validate_whole_file(on_disk, registry)
    return on_disk, restamped


# ---------------------------------------------------------------------------
# Top-level modes
# ---------------------------------------------------------------------------


def run_init(canon_path: Path, research_mode: str, registry: "Registry") -> dict:
    """--init: bootstrap an EMPTY but fully stamped canon.json for a project
    whose glossary pass has nothing to research -- `glossary_batch_plan.py`
    printed `{"no_new_candidates": true, "batches": []}`, so SKILL.md's W3
    SKIP branch runs no merge, and the merge is the only writer of
    canon.json (#290). Reuses `_stamp_write_verify` unchanged, so the
    bootstrap canon carries genuine cache_key.py-computed generation_hashes
    -- exactly what segpack.py copies verbatim into every pack -- rather
    than a hand-rolled stub that would fail its own required-field check at
    W3a.

    CREATE-ONLY, by design: an already-existing canon.json is left
    byte-untouched (`"created": false`) and is not even read here.
    Re-stamping one would hand an operator a way to clear
    select_segments.py's derivation-state gate without regenerating
    anything, since that gate reads precisely these two hashes to decide
    whether a particle_config edit or a bootstrap_names.py/segpack.py fix
    has been regenerated through. Health-checking an existing canon.json is
    VALIDATE-ONLY mode's job, not this one's; keeping --init silent about
    it means the documented SKIP-branch command stays a safe no-op on every
    re-run of an already-bootstrapped project.
    """
    created = not canon_path.is_file()
    restamped = False
    if created:
        # No prior file, so _stamp_write_verify always stamps fresh here --
        # the #291 conservation path cannot apply to a bootstrap.
        _, restamped = _stamp_write_verify(
            canon_path, {"entries": {}, "review_queue": []}, registry
        )

    return {
        "success": True,
        "mode": "init",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "created": created,
        # Every writing mode answers the same question the same way, so a
        # caller can ask "did the provenance move?" without branching on mode.
        "generation_hashes_restamped": restamped,
    }


def run_restamp_derivation(canon_path: Path, research_mode: str, registry: "Registry") -> dict:
    """--restamp-derivation: re-record the CURRENT particle_config /
    derivation-bundle provenance onto an existing canon.json, leaving its
    content untouched.

    This exists because #291 deliberately removed the only way this used to
    happen. Issue #193 documents `--merge-batches <empty-batch.json>` as the
    single (explicitly "not documented, sanctioned, or tested") escape from
    `blocked_needs_regeneration` for a MATURE, zero-candidate project: such a
    project has no candidates left, so the glossary pass never runs, so no
    merge ever restamps -- and after a plugin upgrade that touches
    bootstrap_names.py or segpack.py, segment selection stays blocked
    forever. Closing #291 without this would have turned that latent brick
    into an unconditional one.

    Making it an explicit, single-purpose, named mode is the whole point: the
    #291 defect was never that this operation exists, it was that it happened
    SILENTLY as a side effect of a command whose stated job was merging
    fragments. An operator who runs this has said what they mean, and the
    result payload names exactly which fields moved.

    Pass 1 runs over the existing entries first -- provenance should never be
    advanced on a canon.json that is not itself valid.
    """
    if not canon_path.is_file():
        raise CanonValidationError(
            f"canon.json not found at {canon_path} (nothing to restamp -- "
            f"bootstrap a new project with --init instead)"
        )

    canon = _load_canon(canon_path)
    _validate_existing_entries(canon, registry)

    before = dict(canon.get("generation_hashes") or {})
    on_disk, restamped = _stamp_write_verify(canon_path, canon, registry, force_restamp=True)
    after = on_disk["generation_hashes"]

    return {
        "success": True,
        "mode": "restamp_derivation",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        # Same key, same meaning, as every other writing mode -- always true
        # here, since force_restamp bypasses the #291 conservation path.
        "generation_hashes_restamped": restamped,
        # This mode's EXTRA detail: which fields actually moved. A restamp on
        # an already-current canon legitimately moves nothing and reports [].
        "generation_hashes_changed": sorted(
            field for field in GENERATION_HASH_FIELDS if before.get(field) != after.get(field)
        ),
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
    }


def run_merge(
    canon_path: Path,
    batch_path: str,
    research_mode: str,
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
) -> dict:
    """Legacy single-fragment merge path (--batch PATH). Equivalent to
    `run_merge_batches(canon_path, [batch_path], ...)`, kept as its own
    code path because existing tests/callers already invoke it this way.
    """
    batch = _load_batch(batch_path)
    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)

    _validate_batch_items(batch, registry)
    _enforce_offline_backstop(batch, research_mode)
    merged = _merge_batch(canon, batch, senses)

    on_disk, restamped = _stamp_write_verify(canon_path, merged, registry)

    n_accepted = sum(1 for item in batch if item.get("disposition") == "accepted")
    n_queued = sum(1 for item in batch if item.get("disposition") == "review_queue")
    return {
        "success": True,
        "mode": "merge",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "batch_items": len(batch),
        "merged_accepted": n_accepted,
        "merged_queued": n_queued,
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
        # #291: false here means this merge changed nothing, so canon.json's
        # provenance claim was deliberately left where it was.
        "generation_hashes_restamped": restamped,
    }


def run_check_batch(
    canon_path: Path,
    batch_path: str,
    research_mode: str,
    manifest_path: "str | None",
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
) -> dict:
    """--check-batch PATH [--expect-source-forms-file M.json]: Pass 1 +
    offline backstop on ONE fragment, NO write. When a manifest is given,
    additionally asserts exact source_form coverage. ALSO loads canon.json
    (read-only -- an absent file is the same fresh skeleton _load_canon
    always returns; nothing is ever written here) and canon_senses.json,
    then dry-runs `_merge_batch` (its return value discarded) so the
    refuse-recollapse guard -- and any ordinary entries{} collision --
    rejects a doomed fragment at precheck/readiness time (RFC #215 1d),
    not only at the final --merge-batches call.
    """
    batch = _load_batch(batch_path)
    _validate_batch_items(batch, registry)
    _enforce_offline_backstop(batch, research_mode)
    if manifest_path is not None:
        expected_forms = _load_source_forms_manifest(manifest_path)
        _assert_exact_source_form_coverage(batch, expected_forms)

    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)
    _merge_batch(canon, batch, senses)

    return {
        "success": True,
        "mode": "check_batch",
        "source_forms": len({item.get("source_form") for item in batch if isinstance(item, dict)}),
    }


def run_merge_batches(
    canon_path: Path,
    batch_paths: list,
    research_mode: str,
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
) -> dict:
    """--merge-batches P1 P2 ...: single process, single canon.json load.
    Validates ALL given fragments (Pass 1 + offline backstop) FIRST, before
    merging any of them, then threads `acc = _merge_batch(acc, frag,
    senses)` across every fragment IN THE GIVEN ORDER -- ONE senses load,
    shared across every fragment in this call."""
    batches = [_load_batch(p) for p in batch_paths]
    for batch in batches:
        _validate_batch_items(batch, registry)
        _enforce_offline_backstop(batch, research_mode)

    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)
    acc = canon
    for batch in batches:
        acc = _merge_batch(acc, batch, senses)

    on_disk, restamped = _stamp_write_verify(canon_path, acc, registry)

    n_accepted = sum(1 for batch in batches for item in batch if item.get("disposition") == "accepted")
    n_queued = sum(1 for batch in batches for item in batch if item.get("disposition") == "review_queue")
    return {
        "success": True,
        "mode": "merge_batches",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "fragments_merged": len(batch_paths),
        "merged_accepted": n_accepted,
        "merged_queued": n_queued,
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
        # #291: false here means every item was an identical re-submission
        # (or the fragment set was empty), so nothing changed and canon.json's
        # provenance claim was deliberately left where it was. Note
        # merged_accepted above counts SUBMITTED items, not changed ones.
        "generation_hashes_restamped": restamped,
    }


def _verify_merged_item(canon: dict, item: dict) -> "str | None":
    """Verifies ONE already-processed batch item is correctly reflected in
    the CURRENT canon.json, by disposition. Returns the item's own
    source_form (to be reported in `missing`) if verification fails, or
    None if it passes."""
    source_form = item.get("source_form") if isinstance(item, dict) else None
    label = source_form if isinstance(source_form, str) and source_form else "<item without a valid source_form>"
    disposition = item.get("disposition") if isinstance(item, dict) else None

    if disposition == "accepted":
        expected_entry = _entry_from_accepted_item(item)
        actual_entry = canon.get("entries", {}).get(source_form)
        return None if actual_entry == expected_entry else label

    if disposition == "review_queue":
        in_queue = item in canon.get("review_queue", [])
        # Accept-supersedes: a LATER batch's accepted resolution for the
        # same source_form is not a failure -- never reported missing.
        superseded = isinstance(source_form, str) and source_form in canon.get("entries", {})
        return None if (in_queue or superseded) else label

    # An unrecognized disposition here means the fragment was never Pass-1
    # validated before --verify-merged ran (this mode is disk-independent
    # and does not itself re-run Pass 1) -- unverifiable, report it.
    return label


def run_verify_merged(
    canon_path: Path, batch_paths: list, manifest_path: "str | None", registry: "Registry"
) -> dict:
    """--verify-merged --batch F1 [--batch F2 ...] [--expect-source-forms-file
    M.json]: disk-INDEPENDENT verification, fresh reads only, no write."""
    if not canon_path.is_file():
        raise CanonValidationError(f"canon.json not found at {canon_path} (nothing to verify)")
    canon = _load_canon(canon_path)

    missing = []
    try:
        _validate_whole_file(canon, registry)
    except CanonValidationError as e:
        missing.append(str(e))

    covered_forms = set()
    for batch_path in batch_paths:
        batch = _load_batch(batch_path)
        for item in batch:
            source_form = item.get("source_form") if isinstance(item, dict) else None
            if isinstance(source_form, str) and source_form:
                covered_forms.add(source_form)
            failure_label = _verify_merged_item(canon, item)
            if failure_label is not None:
                missing.append(failure_label)

    if manifest_path is not None:
        expected_forms = _load_source_forms_manifest(manifest_path)
        missing.extend(sorted(set(expected_forms) - covered_forms))

    missing = sorted(set(missing))
    return {"verified": not missing, "missing": missing}


def run_validate_only(canon_path: Path, research_mode: str, registry: "Registry") -> dict:
    if not canon_path.is_file():
        raise CanonValidationError(f"canon.json not found at {canon_path} (nothing to validate)")
    canon = _load_canon(canon_path)

    _validate_existing_entries(canon, registry)
    _validate_whole_file(canon, registry)

    return {
        "success": True,
        "mode": "validate",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "entries_count": len(canon["entries"]),
        "review_queue_count": len(canon["review_queue"]),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Two-pass jsonschema validation (and, with --batch/"
            "--merge-batches, merge) backstop for canon.json -- see this "
            "file's own module docstring and references/canon-and-"
            "glossary.md for the full spec."
        )
    )
    parser.add_argument(
        "--research-mode",
        required=True,
        choices=RESEARCH_MODES,
        help=(
            "REQUIRED, never defaulted, for EVERY mode below -- profile.yml's "
            "own glossary.research_mode, resolved once by the orchestrating "
            "Claude session. In any mode that validates a batch fragment "
            "(--check-batch, --merge-batches, legacy --batch), 'offline' "
            "fatally forbids basis:\"established\" for every new entry "
            "(basis:\"transliterated\" and basis:\"sense_translated\" both "
            "remain legal under offline -- neither needs an external "
            "citation). Has no effect in --verify-merged or VALIDATE-ONLY "
            "mode -- kept required anyway so no call site can accidentally "
            "omit declaring the precondition."
        ),
    )
    parser.add_argument(
        "--batch",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "Legacy single-fragment MERGE mode when given ALONE (a JSON "
            "array, canon-batch.schema.json shape): Pass 1 + offline "
            "backstop + dedup/collision merge + generation_hashes stamping "
            "+ atomic write + Pass 2. Repeatable ONLY under --verify-merged, "
            "where it names the set of already-processed fragments to "
            "verify against the current canon.json. Omitted entirely: runs "
            "VALIDATE-ONLY mode against the existing canon.json (no write)."
        ),
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help=(
            "Bootstrap an EMPTY but fully stamped canon.json (entries={}, "
            "review_queue=[], both generation_hashes freshly computed via "
            "cache_key.py) when none exists yet -- W3's no_new_candidates "
            "SKIP branch, which never reaches a merge (#290). Create-only: "
            "an existing canon.json is left untouched and reported "
            "\"created\": false, never re-stamped."
        ),
    )
    parser.add_argument(
        "--restamp-derivation",
        action="store_true",
        help=(
            "Re-record the CURRENT particle_config/derivation-bundle "
            "provenance onto an existing canon.json, content untouched. The "
            "sanctioned escape for a mature, zero-candidate project whose "
            "derivation bundle moved and which therefore has no glossary "
            "merge left to run (#193) -- since #291, an ordinary merge that "
            "changes nothing deliberately does NOT restamp."
        ),
    )
    parser.add_argument(
        "--check-batch",
        metavar="PATH",
        default=None,
        help=(
            "Pass 1 + offline backstop on the ONE fragment at PATH, NO "
            "write. Combine with --expect-source-forms-file for exact "
            "coverage checking."
        ),
    )
    parser.add_argument(
        "--merge-batches",
        metavar="PATH",
        nargs="+",
        default=None,
        help=(
            "Merge MULTIPLE fragments, in the given order, in ONE atomic "
            "operation (validate all first, thread the merge, stamp "
            "generation_hashes, in-memory Pass 2, one atomic write, "
            "disk-re-read Pass 2)."
        ),
    )
    parser.add_argument(
        "--verify-merged",
        action="store_true",
        help=(
            "Disk-independent verification that the fragment(s) named by "
            "--batch are correctly reflected in the CURRENT canon.json -- "
            "no write. Requires one or more --batch PATH."
        ),
    )
    parser.add_argument(
        "--expect-source-forms-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a JSON array of expected source_form strings (a FILE, "
            "never inline argv). --check-batch: asserts the one fragment's "
            "own coverage is EXACT. --verify-merged: asserts the union of "
            "every named fragment's coverage is EXACT against this list "
            "(pass the aggregate manifest_all.json here for the final "
            "verify call)."
        ),
    )
    parser.add_argument(
        "--canon-path",
        metavar="PATH",
        default=None,
        help=(
            f"Override the canon.json path (default: "
            f"{DEFAULT_CANON_PATH})."
        ),
    )
    parser.add_argument(
        "--senses-path",
        metavar="PATH",
        default=None,
        help=(
            f"Override the canon_senses.json path (default: "
            f"{DEFAULT_SENSES_PATH}). Consulted by --check-batch, "
            f"--merge-batches, and legacy --batch to refuse merging any "
            f"ACCEPTED item whose source_form is an adjudicated homonym "
            f"split (RFC #215 1d, 'recollapse'). When omitted, an absent "
            f"default sidecar is treated as empty (no splits yet); an "
            f"EXPLICIT --senses-path that does not exist is a hard error "
            f"instead (a typo'd path must never silently bypass the "
            f"recollapse guard) -- see canon_senses.py::load_senses."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    canon_path = Path(args.canon_path) if args.canon_path else DEFAULT_CANON_PATH
    # allow_absent=True ONLY for the genuinely-implicit default -- an
    # EXPLICIT --senses-path that turns out missing must BLOCK, never
    # silently read as "no splits yet" (mirrors glossary_batch_plan.py's
    # own `canon_explicit = args.canon is not None` discipline).
    senses_path = Path(args.senses_path) if args.senses_path else DEFAULT_SENSES_PATH
    allow_absent_senses = args.senses_path is None

    selected_modes = _selected_modes(args)
    if len(selected_modes) > 1:
        parser.error(", ".join(spec.flag for spec in selected_modes) + " are mutually exclusive")

    # #292: `--batch` is meaningful in exactly two shapes -- ALONE (legacy
    # single-fragment merge) or under --verify-merged (naming the already-
    # processed fragments to verify). Alongside any other mode it used to be
    # accepted and then SILENTLY IGNORED, because main()'s dispatch chain
    # below tests `args.batch` only in its final elif and can never reach it.
    # A call site could therefore read `{"success": true}` for a fragment
    # that was never merged. Fail loud instead; no shipped caller passes the
    # combination, so nothing legitimate regresses.
    batch_conflicts = [spec.flag for spec in selected_modes if not spec.batch_ok]
    if args.batch is not None and batch_conflicts:
        parser.error(
            "--batch is not accepted with "
            + ", ".join(batch_conflicts)
            + " -- it would be silently ignored. Pass --batch alone (legacy "
            "single-fragment merge), or under --verify-merged."
        )

    # Every mode that refuses --expect-source-forms-file, with its own reason.
    # Silently dropping the flag would give a false sense that coverage was
    # verified when nothing checked it, so each refusal is loud and says why.
    # Modes are mutually exclusive (checked above), so this names at most one.
    source_forms_refusers = [
        spec for spec in selected_modes if spec.source_forms_refusal is not None
    ]
    if source_forms_refusers and args.expect_source_forms_file is not None:
        parser.error(
            "; ".join(
                f"{spec.flag} does not accept --expect-source-forms-file "
                f"({spec.source_forms_refusal})"
                for spec in source_forms_refusers
            )
        )
    if args.verify_merged and not args.batch:
        parser.error("--verify-merged requires one or more --batch PATH")
    if not args.verify_merged and args.batch is not None and len(args.batch) > 1:
        parser.error(
            "--batch may be given more than once only under --verify-merged"
        )

    try:
        registry = _build_schema_registry()
        if args.init:
            result = run_init(canon_path, args.research_mode, registry)
        elif args.restamp_derivation:
            result = run_restamp_derivation(canon_path, args.research_mode, registry)
        elif args.check_batch is not None:
            result = run_check_batch(
                canon_path,
                args.check_batch,
                args.research_mode,
                args.expect_source_forms_file,
                registry,
                senses_path,
                allow_absent_senses,
            )
        elif args.merge_batches is not None:
            result = run_merge_batches(
                canon_path,
                args.merge_batches,
                args.research_mode,
                registry,
                senses_path,
                allow_absent_senses,
            )
        elif args.verify_merged:
            result = run_verify_merged(
                canon_path, args.batch, args.expect_source_forms_file, registry
            )
        elif args.batch is not None:
            result = run_merge(
                canon_path,
                args.batch[0],
                args.research_mode,
                registry,
                senses_path,
                allow_absent_senses,
            )
        else:
            result = run_validate_only(canon_path, args.research_mode, registry)
    except CanonValidationError as e:
        payload = {"success": False, "error": str(e)}
        if e.offending is not None:
            payload["offending"] = e.offending
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {e}"}, ensure_ascii=False)
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    if args.verify_merged:
        return 0 if result.get("verified") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
