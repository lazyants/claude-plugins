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
in any template. Eight CLI modes now exist, selected by which flag is given
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

--correct PATH
    The ONE sanctioned, RECORDED route to change an already-frozen
    entries{} record, OR to dismiss a review_queue[] candidate (#495;
    disposition:"dismiss" added by #653). Every other path refuses:
    --merge-batches collides (which is correct -- a re-adjudication must
    never silently overwrite a frozen decision), --init is create-only,
    validate-only writes nothing. That left a canon entry that is simply
    WRONG repairable only by a hand-edit of the exact artifact the whole
    gate chain treats as frozen, performed outside every validation this
    script owns and recorded nowhere -- and a canon that contradicts the
    text keeps generating false review findings, so the cheapest way to
    silence them was to revert correct prose to match a wrong canon. A
    review_queue[] row had the same problem one step earlier: nothing
    recorded that a human looked at a candidate and judged it not
    canon-worthy, so it stayed queued forever with no route but a hand
    edit either.

    PATH is a canon-correction.schema.json document (a FILE, never inline
    argv -- a corrected canonical_target_form is exactly the kind of
    multiword/apostrophe/RTL string --expect-source-forms-file is read
    from a file to avoid). It names the source_form, carries a required
    free-text reason, and dispositions one of three ways: `correct`
    (replace the entries{} record, old_entry + new_entry required) or
    `remove` (delete it, new_entry forbidden) -- both state the OLD
    entries{} value via old_entry, refused naming both when it does not
    match disk, so the mode cannot be used blind -- or `dismiss` (drop one
    review_queue[] row, old_item required instead of old_entry, entries{}
    untouched) -- the same blind-use interlock, scoped to a queue row via
    old_item. The document is appended verbatim to canon.json's
    corrections[].

    A `correct` runs new_entry through the SAME content controls the merge
    path enforces -- the #347 static citation boundary and the offline
    basis:"established" backstop -- because being a second write path into
    entries{} is precisely why they must apply here too. `remove` and
    `dismiss` are both exempt: they constrain what may be FROZEN, and
    neither freezes anything.

    OUT-OF-BAND, deliberately: _merge_batch is not touched, no --force
    exists, and an ordinary batch carrying a differing resolution still
    raises. Also the one WRITING mode that does not STAMP: the existing
    generation_hashes are carried forward verbatim, because restamping
    would advance the derivation-bundle provenance claim and clear
    select_segments.py's derivation-state gate with nothing regenerated
    (the #291 hole). Nothing is lost -- the re-stale signal for a
    corrected entry is cache_key.py's per-segment used_terms_hash, which
    hashes only the entries a segment actually references, so a correction
    costs bounded re-review of exactly those segments and never reaches
    translate. A dismissal re-stales no translation SEGMENT either -- no
    used_terms_hash moves, since compute_used_terms_hash projects entries{}
    only, and a dismissal never touches entries{}. It is NOT free of cost
    whole-canon: review_queue[] changes and corrections[] grows, so
    canon.json's bytes change, and those bytes are a frozen input of
    whole-canon consumers (skeptic_ready.py's canon_sha256 stamp,
    suspicion_scan.py's worklist freshness gate). Run a dismissal BETWEEN
    skeptic passes, not into a live one.

--check-batch PATH [--expect-source-forms-file M.json]
    Pass 1 (per-item) + the offline backstop on the ONE fragment at PATH.
    Writes NOTHING in its flagless form, which is the form every gate in
    the pass issues. Two opt-in flags do write, and only on a PASS:
    --approve-to publishes the validated bytes as a snapshot, and
    --record-approval-to (#723) writes a verdict record naming their
    sha256. "--check-batch never writes" was stated categorically here
    while --approve-to already existed; it is the FLAGLESS form that
    never writes. When --expect-source-forms-file is given (a JSON array of
    expected source_form strings, read from the FILE, never inline argv),
    additionally asserts the fragment's item source_forms are an EXACT
    match (no missing, no extra) -- the coverage half of the manifest-
    trust design (references/canon-and-glossary.md's "manifest disk-
    verify"), closing the gap where a codex batch could pass shape
    validation while silently omitting a candidate name.

--merge-batches P1 P2 ... [--expect-source-forms-file M.json is NOT
accepted here -- see --verify-merged] [--citations-reviewed
--approval-records R1 R2 ... (required together, one record per fragment)]
[--glossary-merge-marker PATH]
    ONE process, single canon.json load: validates ALL given fragments
    (Pass 1 + offline backstop + #505's live citation attestation) FIRST,
    before merging any of them, so a later fragment's failure never leaves
    an earlier one half-applied.
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
    #820: when --glossary-merge-marker PATH is given, ONLY once the above
    disk-re-read has confirmed the merge landed, atomically writes a
    durable `{"schema": "glossary-run-merged/1", "run_id", "merged_at",
    "batches", "source": "merge"}` marker to PATH -- see
    _write_glossary_merge_marker()'s own docstring. This is what lets
    select_segments.py's W5 admission gate tell a genuinely merged
    glossary run apart from one that only produced fragments. A marker
    write failure is FATAL for the whole merge.

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

--batch PATH [--citations-reviewed --approval-records R (required
together)] (legacy, single-fragment merge -- KEPT for existing callers)
    The pre-1.2.0 merge path: Pass 1 + offline backstop + #505's live
    citation attestation on the one fragment, merge, stamp
    generation_hashes, in-memory Pass 2, one atomic write, disk-re-read
    Pass 2 (same no-masking discipline as --merge-batches above).
    Equivalent to `--merge-batches PATH` with exactly one fragment, kept as
    its own code path only because existing tests/callers already invoke it
    this way.

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
canon-file.schema.json / canon-correction.schema.json
from ${durable_root}/schemas/ -- never the plugin's
own assets/schemas/ (this script always runs from the durable, per-project
copy).

Usage. #505: under --research-mode live, both MERGE modes (--merge-batches,
legacy --batch) refuse a fragment carrying any basis:"established" item
without --citations-reviewed -- the operator's attestation that an independent
citation review approved those exact bytes. The pre-merge citation review runs
inside the glossary-pass Workflow, never here, so a hand-driven merge would
otherwise freeze an unaudited citation into a canon row nothing downstream may
question, with no signal. Every live merge example below therefore carries it; the offline one
does not, because offline forbids basis:"established" outright.
#412: every mode that STAMPS generation_hashes (--init,
--restamp-derivation, --merge-batches, legacy --batch) refuses to run without
either --plugin-root PATH or an explicit --allow-durable-sibling -- see
--plugin-root's own help and main()'s trusted-sibling precondition. The
non-stamping modes resolve no sibling and take neither flag's meaning, so they
are spelled here without it. ${plugin_root} below is the plugin's own install
root, i.e. SKILL.md's {{PLUGIN_ROOT}}:
    python3 canon_validate.py --research-mode offline --init --plugin-root ${plugin_root}
    python3 canon_validate.py --research-mode offline --init --allow-durable-sibling
    python3 canon_validate.py --research-mode offline --restamp-derivation --plugin-root ${plugin_root}
    python3 canon_validate.py --research-mode offline --correct correction.json
    python3 canon_validate.py --research-mode live --check-batch out_0.json
    python3 canon_validate.py --research-mode live --check-batch out_0.json --expect-source-forms-file manifest_0.json
    python3 canon_validate.py --research-mode live --merge-batches out_0.json out_1.json --plugin-root ${plugin_root} --citations-reviewed --approval-records approval_0.json approval_1.json
    python3 canon_validate.py --research-mode live --verify-merged --batch out_0.json --batch out_1.json --expect-source-forms-file manifest_all.json
    python3 canon_validate.py --research-mode live --batch glossary_out.json --plugin-root ${plugin_root} --citations-reviewed --approval-records approval_0.json
    python3 canon_validate.py --research-mode offline --batch glossary_out.json --plugin-root ${plugin_root}
    python3 canon_validate.py --research-mode live
    python3 canon_validate.py --research-mode live --canon-path /path/to/canon.json
    python3 canon_validate.py --research-mode live --merge-batches out_0.json --senses-path /path/to/canon_senses.json --plugin-root ${plugin_root} --citations-reviewed --approval-records approval_0.json

Exit code 0 on success, 1 on failure (for --verify-merged, "success" means
`verified: true`). Exactly one JSON line is printed to stdout either way --
callers (the glossary-pass Workflow, tests) should read stdout, not rely
on the exit code alone.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse, urlsplit

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

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"canon_validate.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside canon_validate.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

# Self-anchored: this script always lives at
# ${durable_root}/scripts/canon_validate.py, so parents[1] is the durable
# root. Never assumes cwd, never takes a --durable-root flag of its own --
# see references/ledger-and-resumability.md's "Script self-anchoring"
# invariant (the same rule applies to every copied script, not just the
# ledger ones). #412: an explicit --plugin-root PATH overrides where the
# SIBLING cache_key.py script is resolved from -- see
# resolve_cache_key_script() below and references/gotchas.md §4 for the
# full two-flag convention this override follows (this script itself needs
# only the --plugin-root half of it, since it has no data root of its own
# to override).
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


def resolve_cache_key_script(plugin_root_str) -> Path:
    """#412: `plugin_root_str` (this script's own --plugin-root CLI value,
    or None) governs where the SIBLING cache_key.py script this script
    shells out to (to STAMP generation_hashes into canon.json) is resolved
    from -- deliberately NEVER derived from this script's own self-anchored
    DURABLE_ROOT/SCRIPTS_DIR: ${durable_root}/scripts/ is a Step-0a copy the
    codex process this stamp gates can write to, so resolving the checker
    from inside the tree it checks would let a tampered copy validate
    itself -- the whole defect #412 exists to close. When given, resolves
    as `{plugin_root}/assets/scripts/cache_key.py`, the SAME layout
    SKILL.md documents for the plugin-anchored scripts, NOT
    ${durable_root}/scripts/'s own flattened copy layout.
    `plugin_root_str=None` reproduces the self-anchored sibling lookup
    unchanged -- but in a STAMPING mode reachable only behind an
    explicit --allow-durable-sibling (main()'s trusted-sibling precondition),
    never as a silent default. This script has no --durable-root of its own (its OWN data
    is always self-anchored), so there is only ever this one thing to
    resolve, unlike select_segments.py/ledger_merge.py/resume_setup.py/
    review_ready.py, each of which resolves BOTH a data root and a sibling."""
    if plugin_root_str is None:
        return CACHE_KEY_SCRIPT
    return Path(plugin_root_str).resolve() / "assets" / "scripts" / "cache_key.py"

RESEARCH_MODES = ("live", "offline")

# #820. Shared default reason for ModeSpec.glossary_merge_marker_refusal --
# declared ahead of the class it defaults a field on (a NamedTuple field
# default is evaluated at class-body time, so this cannot sit alongside its
# siblings _READS_NO_FRAGMENT/_WRITES_NO_CANON_ROW/etc. below, which are only
# needed once MODE_SPECS itself is built).
_NOT_A_MERGE_THAT_ESTABLISHES_ANYTHING = (
    "only --merge-batches performs the merge the W5 admission gate needs "
    "recorded"
)


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
    citations_reviewed_refusal -- None when --citations-reviewed is accepted
                with this mode; otherwise the REASON it is refused, shown to
                the operator verbatim. #505: only a mode that WRITES a canon
                row can freeze an unreviewed citation, so only the two merge
                modes take the attestation. Declared as a column for the same
                reason as its two siblings -- a mode added later inherits the
                refusal instead of having to be remembered by it.
    stamps_generation_hashes -- does this mode shell out to the sibling
                cache_key.py to WRITE canon.json's generation_hashes? #412:
                only these modes resolve a sibling out of the codex-writable
                ${durable_root}/scripts/, so only these require an explicit
                answer to "which cache_key.py may stamp this canon" -- see
                main()'s trusted-sibling precondition. Declared as a column
                rather than as a hand-typed set in main() for the same reason
                every other cross-flag fact is: a mode added later inherits
                the guard instead of having to be remembered by it.
    glossary_merge_marker_refusal -- None when --glossary-merge-marker is
                accepted with this mode; otherwise the REASON it is refused.
                #820: only --merge-batches ever establishes a genuine merge
                the W5 gate needs recorded, so every other row defaults to
                a shared refusal reason (declared with a default so adding a
                future mode need not restate it) and only --merge-batches
                overrides it to None.

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
    fragment_bytes_flag_refusal: "str | None"
    citations_reviewed_refusal: "str | None"
    stamps_generation_hashes: bool
    glossary_merge_marker_refusal: "str | None" = _NOT_A_MERGE_THAT_ESTABLISHES_ANYTHING


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
# every table-driven cross-flag guard below (mutual exclusion, --batch
# compatibility, --expect-source-forms-file acceptance, --approve-to
# acceptance, --citations-reviewed acceptance) is a comprehension over this
# table, so none of them can be taught about a new FLAG-SELECTED mode while
# another silently is not. Deliberately NOT stated as a COUNT any more: the
# count read "three" through both --approve-to and #505's attestation, which
# is how a number in a comment always ends up -- wrong and unnoticed. Also
# tests/canon_stamp_conservation.test.py fails if a parser flag is missing
# from the table or vice versa -- that row is unforgettable.
#
# The scope limit is real but narrower than "outside every guard": VALIDATE-
# ONLY, the default mode reached when no mode flag is passed at all, has no
# flag, no dest and no row here, so it sits outside the TABLE. It is not
# outside every guard, though -- main() refuses --expect-source-forms-file
# for it with an explicit `elif not selected_modes` check (it reads no
# fragment, exactly like --init), so the silent-ignore that once returned
# {"success": true} without checking coverage is closed; --approve-to and
# --citations-reviewed each carry their own `elif not selected_modes` check
# there for the same reason. The residual is only that it stays invisible to
# the TABLE-DRIVEN guards -- mutual exclusion and --batch compatibility -- and
# to the drift test, which compares parser dests against table dests and so
# cannot see a mode that has neither. Any future flagless mode inherits that
# residual and must be guarded by hand the same way.
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
# The shared refusal for every flag that OPERATES ON ONE FRAGMENT'S EXACT
# BYTES: --approve-to snapshots them, --record-approval-to records their
# digest. Both belong to --check-batch alone, which is the only mode that
# reviews a single pre-merge fragment, so honoring either elsewhere would
# snapshot -- or vouch for -- bytes nothing reviewed, the same false-success
# shape the source-forms refusal guards against. ONE column governs both
# (fragment_bytes_flag_refusal): a second column repeating these same values
# row for row is exactly the two-hand-maintained-lists defect this table
# exists to remove, and the reason text below is per-MODE, never per-flag.
_NOT_A_SINGLE_FRAGMENT_REVIEW = (
    "only --check-batch operates on the exact bytes of the single fragment "
    "it reviews pre-merge"
)
# #505. The attestation exists to gate the one irreversible act: freezing an
# `established` citation nobody audited into a frozen canon row. A mode
# that writes no row cannot do that, so accepting the flag there would state a
# precondition it does not have -- the same false-success shape the two
# refusals above guard against.
_WRITES_NO_CANON_ROW = "it writes no canon row, so it can freeze no citation"
# #505 x #495. --correct DOES write into entries{}, and its new_entry may carry
# basis:"established" with a source, so the "writes no row" reason above would
# be simply false for it. It is still outside this gate, for the reason the
# gate exists: #505's defect is SILENCE -- a bulk pass freezing citations
# nobody looked at, reported as success. A correction is the opposite act by
# construction. It refuses to run blind (it must state the OLD value and is
# refused when that does not match disk), carries a required free-text reason,
# and is appended verbatim to canon.json's corrections[] -- an on-the-record
# decision by somebody who has already concluded the entry is wrong. Demanding
# a second attestation there would add ceremony to an act that is already
# deliberate and already recorded, and would say nothing the corrections[]
# entry does not. Its citation SAFETY is unaffected: #495 already routes
# new_entry through the #347 static boundary and the offline backstop, both of
# which still apply.
_ALREADY_AN_ON_THE_RECORD_DECISION = (
    "a correction states the old value, carries a reason, and is recorded in "
    "corrections[] -- it is already the deliberate act this flag exists to force"
)

MODE_SPECS = (
    ModeSpec(
        "--init",
        "init",
        batch_ok=False,
        source_forms_refusal=_READS_NO_FRAGMENT,
        fragment_bytes_flag_refusal=_READS_NO_FRAGMENT,
        citations_reviewed_refusal=_READS_NO_FRAGMENT,
        stamps_generation_hashes=True,
    ),
    ModeSpec(
        "--restamp-derivation",
        "restamp_derivation",
        batch_ok=False,
        source_forms_refusal=_READS_NO_FRAGMENT,
        fragment_bytes_flag_refusal=_READS_NO_FRAGMENT,
        citations_reviewed_refusal=_READS_NO_FRAGMENT,
        stamps_generation_hashes=True,
    ),
    # #495. The one WRITING mode with stamps_generation_hashes=False: it
    # carries canon.json's existing stamp forward verbatim and computes no
    # hash, so it resolves no sibling cache_key.py and the #412
    # trusted-sibling precondition below has nothing to guard. That breaks
    # the identity --plugin-root's help used to state ("'stamping' and
    # 'writing' name the SAME four modes"), which is corrected there.
    # source_forms_refusal/_READS_NO_FRAGMENT is exact: --correct reads a
    # correction DOCUMENT, never a batch fragment, so there is no item set
    # for --expect-source-forms-file to cover.
    ModeSpec(
        "--correct",
        "correct",
        batch_ok=False,
        source_forms_refusal=_READS_NO_FRAGMENT,
        fragment_bytes_flag_refusal=_NOT_A_SINGLE_FRAGMENT_REVIEW,
        citations_reviewed_refusal=_ALREADY_AN_ON_THE_RECORD_DECISION,
        stamps_generation_hashes=False,
    ),
    ModeSpec(
        "--check-batch",
        "check_batch",
        batch_ok=False,
        source_forms_refusal=None,
        fragment_bytes_flag_refusal=None,
        citations_reviewed_refusal=_WRITES_NO_CANON_ROW,
        stamps_generation_hashes=False,
    ),
    ModeSpec(
        "--merge-batches",
        "merge_batches",
        batch_ok=False,
        source_forms_refusal=_COVERAGE_ENFORCED_ELSEWHERE,
        fragment_bytes_flag_refusal=_NOT_A_SINGLE_FRAGMENT_REVIEW,
        citations_reviewed_refusal=None,
        stamps_generation_hashes=True,
        glossary_merge_marker_refusal=None,
    ),
    ModeSpec(
        "--verify-merged",
        "verify_merged",
        batch_ok=True,
        source_forms_refusal=None,
        fragment_bytes_flag_refusal=_NOT_A_SINGLE_FRAGMENT_REVIEW,
        citations_reviewed_refusal=_WRITES_NO_CANON_ROW,
        stamps_generation_hashes=False,
    ),
    # The legacy bare-`--batch` merge. batch_ok=True is load-bearing, not
    # cosmetic: `--batch` IS this mode's own selector, so a False here would
    # make the --batch-compatibility guard fire on the mode itself.
    ModeSpec(
        "--batch (legacy single-fragment merge)",
        None,
        batch_ok=True,
        source_forms_refusal=_COVERAGE_ENFORCED_ELSEWHERE,
        fragment_bytes_flag_refusal=_NOT_A_SINGLE_FRAGMENT_REVIEW,
        citations_reviewed_refusal=None,
        stamps_generation_hashes=True,
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
    {
        "help",
        "research_mode",
        "batch",
        "expect_source_forms_file",
        "approve_to",
        "record_approval_to",
        "approval_records",
        "canon_path",
        "senses_path",
        "plugin_root",
        "allow_durable_sibling",
        "citations_reviewed",
        "glossary_merge_marker",
    }
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

# #820. The RUN_ID allowlist -- byte-identical to resume_setup.py's own
# RUN_ID_RE/validate_run_id(), which OWNS this contract, per this project's
# no-shared-util convention (duplicated, not imported; see
# glossary_dispatch_driver.py's, segment_dispatch_driver.py's, select_
# segments.py's and skeptic_setup.py's own copies). Used only by
# --glossary-merge-marker below, to refuse splicing an unsafe id into the
# marker's `run_id` field and into any path built from it.
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_run_id(run_id):
    """Return an error string if `run_id` is not a safe RUN_ID, else None."""
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not RUN_ID_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", ".."):
        return f"run id must not be '.' or '..'; got {run_id!r}."
    if ".." in run_id:
        return f"run id must not contain '..'; got {run_id!r}."
    return None


# #820. The glossary-run merge marker canon_validate.py --merge-batches
# writes on a SUCCESSFUL merge -- select_segments.py's W5 admission gate
# (check_glossary_runs_merged()) reads it back by this exact schema string,
# so it is pinned here, not restated ad hoc at the one write site below.
GLOSSARY_RUN_MERGED_SCHEMA = "glossary-run-merged/1"


def _merge_marker_now_iso8601() -> str:
    """Byte-for-byte the same format as ledger_update.py's/select_segments.
    py's own now_iso8601() copies: seconds precision, 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_glossary_merge_marker(marker_path: Path, batch_paths: list) -> None:
    """Atomically write the durable glossary-merge marker the W5 admission
    gate reads to tell a genuinely MERGED glossary run apart from one that
    only produced fragments (#820). Called ONLY from run_merge_batches,
    ONLY after `_stamp_write_verify()` has already written canon.json AND
    re-read it fresh from disk to confirm the merge landed -- so by the
    time this runs the merge is genuinely established, not merely
    attempted.

    `run_id` is derived from `marker_path`'s PARENT DIRECTORY NAME, the
    convention both entry points already use (glossary-pass-wf.template.js's
    RUN_DIR + '/merged.json'), and validated against this file's own
    validate_run_id() copy above -- an unsafe id refuses rather than
    writing a marker under a path the reader would not trust anyway (its
    own `run_id` field must match the directory name it is found in).

    `batches` records the ASCENDING 0-based POSITION of each fragment in
    `batch_paths`, i.e. the order --merge-batches was actually given on
    this command line -- not a real glossary batch index re-derived from a
    fragment's filename, which this script deliberately does not parse
    (mergeBatchesCmd()'s own comment: teaching this script the template's
    private filename convention is exactly what #734 avoided for the
    approval-record pairing, for the same reason). The reader
    (check_glossary_runs_merged()) does not consult this field at all --
    it is provenance for a human reading the marker, not part of the
    admission predicate.

    A marker that fails to write is FATAL for the whole merge (raises
    CanonValidationError, exactly like any other fatal failure in this
    module) rather than a silently degraded success: a merge that
    happened without its marker is precisely the state that makes the W5
    gate wrong. Re-running --merge-batches costs nothing once canon.json
    already reflects the fragments -- #291's _stamp_write_verify() treats
    an all-already-merged re-submission as a content-free no-op (nothing
    to re-merge, no restamp) -- so refusing here is a safe, retryable
    failure, not a stuck one."""
    run_id = marker_path.parent.name
    problem = validate_run_id(run_id)
    if problem is not None:
        raise CanonValidationError(
            f"cannot write glossary merge marker at {marker_path}: refused "
            f"to derive run_id from its parent directory name -- {problem}"
        )
    marker = {
        "schema": GLOSSARY_RUN_MERGED_SCHEMA,
        "run_id": run_id,
        "merged_at": _merge_marker_now_iso8601(),
        "batches": list(range(len(batch_paths))),
        "source": "merge",
    }
    try:
        _atomic_write_json(marker_path, marker)
    except OSError as e:
        raise CanonValidationError(
            f"merge succeeded but could not write its glossary merge marker "
            f"at {marker_path} ({e}) -- refusing rather than leaving an "
            "unrecorded merge the W5 gate cannot tell apart from one that "
            "never ran. Re-run --merge-batches with the same fragments once "
            "the path is writable; merging already-merged content is a "
            "no-op."
        )


# Most problems worth listing in one failure. The per-message cap bounds each
# ENTRY; nothing bounded the COUNT, and canon-batch.schema.json has no maxItems,
# so output stayed linear in the batch size: measured, 40 items (the shipped
# DEFAULT_BATCH_SIZE) produced 14 KB carrying an injected sentence 80 times, each
# copy individually within its 200-char cap. Round 6's claim that the cap made
# output "no longer a function of the attacker's input" was measured on a
# ONE-item batch and was true only there. An operator does not need 40 near
# identical diagnostics to act, and the prepare agent should not be handed them.
_MAX_LISTED_PROBLEMS = 8


def _joined_problems(problems) -> str:
    """Join problems one per line, bounded in COUNT as well as in each entry."""
    # Each element too, not only the count: a single item's schema errors are
    # joined with no count bound of their own upstream, so one problem could be
    # arbitrarily long while the list stayed short.
    shown = [_bounded_message(str(x)) for x in problems[:_MAX_LISTED_PROBLEMS]]
    if len(problems) > _MAX_LISTED_PROBLEMS:
        shown = shown + [f"... and {len(problems) - _MAX_LISTED_PROBLEMS} more "
                         f"(showing the first {_MAX_LISTED_PROBLEMS} of {len(problems)})"]
    return "\n  ".join(shown)


def _bounded_list(values) -> list:
    """Bound a reported list in COUNT and in each element's LENGTH.

    The same treatment CanonValidationError.__init__ applies, factored out so a
    reporting path that never raises can share it rather than reinvent it. That
    split is what let --verify-merged stay unbounded through two rounds of
    "everything is bounded now".
    """
    extra = len(values) - _MAX_LISTED_PROBLEMS
    bounded = [_bounded_message(str(v)) for v in list(values)[:_MAX_LISTED_PROBLEMS]]
    if extra > 0:
        bounded.append(f"... and {extra} more")
    return bounded


class CanonValidationError(Exception):
    """Raised for any failure that should surface as a FAILURE result.

    `offending`, when not None, is folded into the failure payload verbatim
    -- naming which batch items / entries triggered the failure, so a
    caller never has to re-derive that from a bare error string.
    """

    # Total ceiling for one failure payload. Generous: every legitimate
    # diagnostic in this file is far under it.
    MAX_MESSAGE_CHARS = 4000

    def __init__(self, message, offending=None):
        # BOUNDED HERE, at the one place every failure passes through, rather
        # than at each site that happens to build a list. Round 7 capped the two
        # sites it had measured and left three siblings in this same file
        # emitting 563 KB at the shipped DEFAULT_BATCH_SIZE and 6 MB at 500
        # items -- the exact magnitude that commit claimed to have closed,
        # reproduced one function over. Fixing the three would have been the
        # same mistake a third time; the guard belongs where it cannot be
        # missed, so a future raise site inherits it without knowing.
        message = str(message)
        if len(message) > self.MAX_MESSAGE_CHARS:
            message = (message[:self.MAX_MESSAGE_CHARS]
                       + f"\n  ... [truncated, {len(message)} chars total]")
        super().__init__(message)
        # Bounded in COUNT here, at the one place every failure passes through,
        # rather than at each of the call sites that build a list. Each element
        # is already length-capped by _bounded_message; without a count bound the
        # payload stayed linear in the batch size, and canon-batch.schema.json
        # has no maxItems. Measured at the shipped DEFAULT_BATCH_SIZE of 40: the
        # message obeyed its own cap while `offending` carried all 40 entries.
        if offending is not None:
            # A BARE STRING is not a list of offenders. list("shortkey") shreds
            # it into characters -- a regression the previous version of this
            # block introduced, reachable from canon_senses.py, which raises
            # with offending=<a single key>. Wrap before bounding.
            if isinstance(offending, (str, bytes)):
                offending = [offending]
            offending = _bounded_list(offending)
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


# Longest jsonschema message this script will re-emit. jsonschema builds its
# message by embedding the OFFENDING INSTANCE VALUE verbatim, so an item whose
# `basis`/`confidence`/extra key carries a paragraph of fragment prose produces a
# message containing that paragraph -- measured at 12.4 KB of output from a
# 6 KB payload, with the injected sentence repeated 120 times across `error` and
# `offending`. That output is read by the prepare agent, whose whole design
# premise is that it ingests nothing attacker-authored, and its reply is relayed
# into the next attempt's dispatch prompt. Round 5 capped `_indexed_item_label`
# and left this sibling channel in the same output string uncapped.
# The SLICE length. The emitted string can be 15 chars longer, because
# ' [...truncated]' is appended after slicing -- 215 is the real ceiling.
_SCHEMA_MESSAGE_MAX_CHARS = 200


def _bounded_message(message: str) -> str:
    """Cap a jsonschema message and strip the line breaks it can contain.

    Newlines matter as much as length: the caller joins problems one per line,
    so an embedded newline lets a value forge what looks like a separate
    diagnostic line of its own.
    """
    flat = " ".join(str(message).split())
    if len(flat) > _SCHEMA_MESSAGE_MAX_CHARS:
        flat = flat[:_SCHEMA_MESSAGE_MAX_CHARS] + " [...truncated]"
    return flat


def _format_single_error(e, prefix: str = "") -> str:
    # loc too, not only the message. For an entries{} error the path IS the
    # entries key -- a fragment-authored source_form -- so bounding one half of
    # this f-string and not the other left the site unbounded. Measured:
    # 32,826 chars generated, 4,037 delivered head-truncated, injected sentence
    # x59, saturating at n=8 rather than needing a large batch.
    loc = _bounded_message("/".join(str(p) for p in e.path)) or "<root>"
    return f"{prefix}at '{loc}': {_bounded_message(e.message)}"


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
    # COUNT-bounded as well: one item can carry arbitrarily many schema errors,
    # so a short list of problems could still be an enormous string.
    return "; ".join(_bounded_list(parts))


# Longest source_form EXCERPT (source characters kept, before repr()) a
# failure label may carry -- NOT the final rendered label's length. A real
# name is far shorter; the cap exists so the label cannot become a delivery
# vehicle for prose aimed at whoever reads the failure.
#
# Round 6 sweep finding, fixed here: this constant's own name and the
# function's old docstring both read as if 60 bounded the RENDERED label,
# and the code truncated repr()'s OUTPUT to enforce that -- the same
# source-versus-rendered confusion skeptic_report.py's `_bounded` needed
# fixing for earlier this round, in a different file. Stated plainly rather
# than fixed by narrowing further: repr()'s own escaping (a control
# character, a backslash, a non-ASCII codepoint Python considers
# unprintable) can still make the RENDERED label longer than 60 -- source
# TRUNCATION is what this bounds, not the escaped result.
_ITEM_LABEL_MAX_CHARS = 60


def _indexed_item_label(kind: str, index: int, item) -> str:
    """Builds a "kind[i]" or "kind[i] ('source_form')" label for a batch or
    review_queue item, so a Pass-1 failure names exactly which item broke.

    The INDEX is the identifier; the source_form is a convenience for whoever
    reads the failure. It is also free text the pipeline does not control -- an
    LLM wrote it from source text a hostile document can seed -- and this label
    lands in `error`/`offending`, which the prepare agent is told to read and
    describe. So it is bounded rather than passed through whole: the SOURCE is
    truncated first, THEN repr()'d -- never the other way around.

    Measured bug in the old order (repr() first, slice its output, close with
    a hardcoded "'"): repr() picks DOUBLE quotes whenever the string contains
    `'` but not `"`, which an apostrophe-bearing source_form does -- and this
    plugin's own domain, Hebrew-to-English transliteration, produces exactly
    that ("Re'uven", "Ya'akov", "Sh'muel") as ordinary correct data, not just
    adversarial input. The old code's hardcoded `"'"` closer then produced a
    label that opened with `"` and closed with `'` -- not valid Python literal
    syntax, breaking the "cannot break out of its own line" self-containment
    this whole function exists for. A minimal fix (close with the label's own
    first character instead of a hardcoded one) does not fully close it either:
    slicing repr()'s OUTPUT at a fixed position can land inside a multi-
    character escape sequence repr() produced (e.g. cutting between the `\\`
    and the `n` of a `\\n`), leaving a lone trailing backslash whose validity
    depends on Python's current, EXPLICITLY DEPRECATED lenient handling of
    unrecognized escape sequences in string literals -- accepted with a
    warning today, a planned SyntaxError in a future Python. Measured with 500
    randomized adversarial constructions (backslashes/quotes/control chars at
    every position near the cut): the repr-then-slice order breaks on the
    exact boundary case; slicing the SOURCE first and repr()'ing the
    (already-truncated) result never does, for any input, because repr() of
    an arbitrary string is unconditionally well-formed Python literal syntax
    by construction -- there is no escape sequence left dangling to cut
    through. The reader still has the index and the fragment when the excerpt
    is not enough.
    """
    source_form = item.get("source_form") if isinstance(item, dict) else None
    if not source_form:
        return f"{kind}[{index}]"
    if len(source_form) > _ITEM_LABEL_MAX_CHARS:
        source_form = source_form[:_ITEM_LABEL_MAX_CHARS] + "..."
    return f"{kind}[{index}] ({source_form!r})"


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
# Citation `source` safety -- the STATIC half of the #347 fetch boundary
# ---------------------------------------------------------------------------
#
# NOTE ON _is_uri, DIRECTLY ABOVE: it was deliberately NOT widened to do this
# job. It is the generic `format: "uri"` checker, wired through
# _check_uri_format -> _uri_format_checker into EVERY validator this script
# builds, so teaching it about loopback addresses would silently redefine what
# `format: "uri"` means on every field of every canon schema -- including
# fields that have nothing to do with citations. `http://127.0.0.1/x` IS a
# well-formed URI; it is an unacceptable CITATION. Those are two different
# questions and they stay in two different functions.
#
# DELIBERATE DUPLICATION with fetch_citation.py's `validate_url`, which
# implements this same static decision (its module docstring points back
# here). Not consolidated into a shared import, for two load-bearing reasons:
#
#   1. --check-batch must stay offline-safe and importable WITHOUT the
#      fetcher. fetch_citation.py owns sockets, TLS and DNS; this file is the
#      gate that also runs on the offline path, and must not grow an import
#      edge to a networking module to perform a check that touches no network.
#   2. A static rejection that fired only at fetch time would not close the
#      hole at all on that offline path -- nothing ever fetches there, so an
#      unsafe `source` would reach canon.json's frozen, hash-versioned bytes
#      completely unexamined.
#
# So: same reasons, same strings, two call sites. Change one, change the
# other, and keep tests/canon_citation_refusal.test.py's table in step.

# --- #383: a machine-truncated source_form may never be `accepted` ---------
#
# `bootstrap_names._capped_candidate_name()` bounds a candidate `name` and
# marks the cut with a trailing " [...truncated:<16 lowercase hex>]", while
# `bootstrap_names.span_match_keys()` keys every occurrence on the span's own
# UNCAPPED text. So a canon entry whose `source_form` is that truncated
# spelling can never match an occurrence of itself: it is INERT -- zero
# occurrences, zero evidence, and a green run rather than a halt.
#
# `glossary_TASK.md` tells the adjudicator to queue such a candidate, and
# `glossary_preflight.py` step 6c refuses to dispatch a durable prompt that
# lacks that instruction. This is the fail-closed half: an instruction the
# model overlooks is still caught here, on the fragment, before any of it can
# reach entries{}.
#
# DELIBERATE DUPLICATION of bootstrap_names' marker shape, exactly like the
# `validate_url` duplication documented above and for the first of its two
# reasons: this gate runs on the offline path and must not grow an import edge
# for a check that reads three constants. tests/canon_truncated_source_form.
# test.py pins this regex against bootstrap_names' own, so the two copies
# cannot silently drift -- by test, not by eye.
CAPPED_NAME_MARKER_RE = re.compile(r" \[\.\.\.truncated:[0-9a-f]{16}\]$")


def _enforce_no_truncated_accepted(batch: list) -> None:
    """Refuse any item whose `source_form` carries the truncation marker while
    `disposition` is "accepted", naming the offending items by index.

    Only `accepted` is refused. A marker-bearing `review_queue` item is the
    CORRECT outcome and must keep passing -- queueing it is what the prompt
    rule asks for, and `glossary_batch_plan.py` then excludes that
    `source_form` from every later batch. Refusing both would turn the
    remedy into a dead end with nowhere for the candidate to go.
    """
    problems = []
    for i, item in enumerate(batch):
        if not isinstance(item, dict):
            continue  # shape is Pass 1's job; never mask its error with ours
        if item.get("disposition") != "accepted":
            continue
        source_form = item.get("source_form")
        if not isinstance(source_form, str):
            continue  # likewise Pass 1's
        if CAPPED_NAME_MARKER_RE.search(source_form):
            # The REMEDY leads, because `_indexed_item_label` bounds this
            # string and a 200-character source_form pushes anything at the
            # tail out of the message an operator actually reads.
            problems.append(
                f'{_indexed_item_label("batch", i, item)}: must be '
                f'disposition:"review_queue", never "accepted" -- its '
                f"source_form carries bootstrap_names.py's machine-truncation "
                f"marker, so it can never match an occurrence of itself (see "
                f"glossary_TASK.md)"
            )
    if problems:
        raise CanonValidationError(
            "batch would freeze an inert canon entry:\n  " + _joined_problems(problems),
            offending=problems,
        )


CITATION_ALLOWED_SCHEMES = ("http", "https")

# Schemes worth NAMING in a refusal, so the reason still says which kind of
# unsafe URL was attempted. Everything outside this set collapses to "other".
# A diagnostic vocabulary, NOT a denylist: refusal is decided by
# CITATION_ALLOWED_SCHEMES above and nothing here widens it.
#
# Byte-identical to fetch_citation.py's KNOWN_SCHEMES / scheme_token(), and
# pinned that way by canon_citation_refusal.test.py's shared table. The reason
# this exists at all: urlsplit accepts a scheme of [A-Za-z0-9+.-] with no
# length bound, so interpolating the raw scheme let a `source` write its own
# refusal reason -- the exact thing this function's docstring promises never
# happens. Four review rounds missed it because every scheme in that shared
# table was a KNOWN member, and for those the token equals the scheme, so the
# two engines agreed and the divergence was invisible.
CITATION_KNOWN_SCHEMES = ("file", "ftp", "ftps", "data", "javascript", "gopher", "ws", "wss",
                          "mailto", "tel", "about", "blob", "chrome", "jar", "ldap", "dict",
                          "sftp", "smb", "nfs", "redis", "gemini")


def _citation_scheme_token(scheme: str) -> str:
    """Collapse a scheme to a closed vocabulary: a KNOWN member, `none` for an
    absent scheme, or `other`. Mirrors fetch_citation.scheme_token()."""
    if not scheme:
        return "none"
    return scheme if scheme in CITATION_KNOWN_SCHEMES else "other"

# Control characters anywhere in the URL. Also catches the raw CR/LF that make
# request/header splitting possible; \x20 (space) is inside the range on
# purpose, since a space is enough to carry a second request line.
_CITATION_CONTROL_CHAR_RE = re.compile(r"[\x00-\x20\x7f]")


# A host whose every label is a bare integer or an 0x-hex integer, and which is
# NOT already a canonical IP literal. getaddrinfo accepts these as addresses --
# measured: 2130706433, 0x7f.0x0.0x0.0x1, 017700000001 and 127.1 all resolve to
# 127.0.0.1 -- while ipaddress.ip_address() rejects every one of them, so the
# literal check upstream simply does not see them. In fetch_citation.py that was
# only a static miss (resolve_and_pin still refuses the loopback address it comes
# back with); in canon_validate.py, which has no resolver, it was the WHOLE
# check, and a `source` naming loopback in one of these spellings was frozen into
# canon.json.
#
# Refused outright rather than normalised, because normalising means picking a
# platform: 0177.0.0.1 resolves to 177.0.0.1 under getaddrinfo on BSD (measured
# here; inet_aton is the one API that does NOT diverge, returning 127.0.0.1 on
# both) and to 127.0.0.1 under glibc, so the SAME fragment gets different
# verdicts on macOS and Linux. A citation never legitimately cites a decimal,
# octal or hex-spelled address, and a real DNS name cannot have an all-numeric
# final label, so refusing costs nothing real. Verified against example.com,
# 1.example.com, 0x.com and archive.org, all still admitted.
_NUMERIC_LABEL_RE = re.compile(r"\A(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")


def _is_ambiguous_numeric_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return False              # a canonical literal; the address checks own it
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    return bool(labels) and all(_NUMERIC_LABEL_RE.match(l) for l in labels)


def _non_global_address_reason(ip) -> "str | None":
    """The reason this IP literal is not a legitimate citation host, or None.

    Every disqualifying property is named explicitly rather than leaning on
    `is_global` alone, and every reason ends in "-address" so a caller can
    recognise the family without matching the exact member.

    `is_global` is the right primary test, but it is neither sufficient nor
    stable. Not sufficient: 224.0.0.1 reports `is_global` TRUE while being
    multicast. Not stable: its answer has moved across Python versions
    (notably for 0.0.0.0/8 and several IPv6 ranges), and this gate must behave
    identically on whatever interpreter an operator happens to have. The
    checks therefore overlap deliberately -- WHICH one fires first for a given
    address is not a contract, only the refusal itself is.
    """
    if ip.is_loopback:
        return "loopback-address"
    if ip.is_link_local:
        return "link-local-address"      # includes 169.254.169.254
    # fec0::/10, IPv6 site-local. getattr because IPv4Address has no such
    # property. Deprecated by RFC 3879, still routed on legacy networks, and
    # covered by nothing around it: CPython leaves fec0::/10 out of
    # ipaddress._private_networks, so is_private is False and is_global is
    # consequently True. Kept byte-identical to fetch_citation._assert_global.
    if getattr(ip, "is_site_local", False):
        return "site-local-address"
    if ip.is_private:
        return "private-address"
    if ip.is_multicast:
        return "multicast-address"
    if ip.is_reserved:
        return "reserved-address"
    if ip.is_unspecified:
        return "unspecified-address"
    if not ip.is_global:
        return "non-global-address"      # e.g. CGNAT 100.64/10, which trips no named property
    # An IPv4-mapped or 6to4 IPv6 address can smuggle a private v4 address
    # past every check above, because all of them evaluate the WRAPPER rather
    # than the payload. Recurse into the payload.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _non_global_address_reason(mapped)
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        return _non_global_address_reason(sixtofour)
    return None



def _name_for_comparison(host: str) -> str:
    """Fold a host to the ASCII form a RESOLVER would actually use, for NAME
    comparisons only. Kept behaviourally identical to
    fetch_citation.name_for_comparison(); see that copy for the measurements.

    `encodings.idna` splits labels on the literal set `[.\u3002\uff0e\uff61]`
    and UTS-46 folds decorated letters home, so "localhost\u3002",
    "localhost\uff0e", "localhost\uff61" and "\u24dbocalhost" all fold to
    "localhost" and all resolve to loopback. In fetch_citation.py missing them
    would only be a static false-negative, because it re-checks every resolved
    address. THIS file has no resolver behind it by design -- it runs on the
    offline path where nothing ever fetches -- so here the miss is the entire
    check, exactly as it was for the trailing ASCII dot.

    Comparison only: the folded value is never returned to a caller or used as a
    connection target. Anything the codec rejects falls back to the original, so
    this can only add refusals.
    """
    try:
        folded = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError):
        folded = host.lower()
    # rstrip, not a single [:-1]: several codepoints fold to MORE than one
    # dot (U+2025 "..", U+2026 "...", U+FE30 "...."), so stripping exactly one
    # left "localhost." / "localhost..", which matched neither the equality
    # test nor the ".localhost" suffix test -- the same one-dot reasoning
    # this function exists to generalise, stopping one dot short.
    return folded.rstrip(".")


def _citation_source_refusal(value) -> "str | None":
    """A short, stable machine reason to refuse this citation `source`, or
    None when it is acceptable.

    STATIC ONLY -- no DNS, no connection, no I/O of any kind. `--check-batch`
    runs on the offline path and has to stay usable there, so this closes
    exactly the checks decidable from the URL text alone. The resolve-time
    half (require EVERY resolved address to be global, connect to the pinned
    IP, revalidate every redirect hop) lives in fetch_citation.py and cannot
    be done here. A NAME that resolves to 127.0.0.1 is therefore admitted by
    this function and refused later by the fetcher: that split is the design,
    not a gap in it.

    The reason never embeds the offending URL. It goes into an operator-facing
    message that a retry agent also reads, and a `source` is attacker-
    authorable in the one sense that matters -- an LLM produced it from source
    text a hostile document can seed.
    """
    if not isinstance(value, str) or not value:
        return "empty-url"
    if _CITATION_CONTROL_CHAR_RE.search(value):
        return "control-character-in-url"

    try:
        parts = urlsplit(value)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        username = parts.username
        password = parts.password
    except ValueError:
        # urlsplit itself raises on e.g. an unbalanced IPv6 bracket
        # ("http://[::1"). Guarded because this script's contract is ONE line
        # of JSON on stdout -- an escaping ValueError would replace that with
        # a traceback in the middle of a merge gate, which reads to an
        # operator as a broken tool rather than a rejected fragment.
        return "unparseable-url"

    if scheme not in CITATION_ALLOWED_SCHEMES:
        # An allowlist, never a denylist -- the set of schemes a URL library
        # will accept is open-ended and grows with the runtime. The scheme is
        # collapsed to a closed token before it reaches the reason string: it
        # is part of the offending URL, so echoing it raw would break this
        # function's own no-URL-in-the-reason contract.
        return f"scheme-not-allowed:{_citation_scheme_token(scheme)}"
    if username is not None or password is not None:
        # `user:pw@host` shifts which host is really contacted depending on
        # who parses it.
        return "embedded-credentials"
    if not host:
        return "no-host"
    # Against the FOLDED host as well as the raw one. The resolver does not see
    # the bytes written in the URL: getaddrinfo applies the IDNA codec, whose
    # nameprep pass NFKC-folds fullwidth digits to ASCII and whose label split
    # accepts [.\u3002\uff0e\uff61] as separators. So "\uff12\uff18\uff15\uff12\uff10\uff13\uff19\uff11\uff16\uff16" -- which this
    # check reads as a non-numeric name and ipaddress refuses to parse -- is
    # b"2852039166" to the resolver, i.e. 169.254.169.254. Measured: seven such
    # spellings passed BOTH static halves, one of them straight to cloud IMDS.
    #
    # This file already folds for the localhost NAME test a few lines below, and
    # for exactly this reason; round 7 is that same reasoning finally applied to
    # the numeric and literal checks, which sit ABOVE the fold. Folding alone is
    # not enough either: the four dot-separator spellings fold into a CANONICAL
    # literal, which _is_ambiguous_numeric_host deliberately passes, so the
    # literal check has to see the folded form too.
    folded_host = _name_for_comparison(host)
    if _is_ambiguous_numeric_host(host) or _is_ambiguous_numeric_host(folded_host):
        return "ambiguous-numeric-host"

    host = host.lower()
    # `localhost` and anything under it are refused BY NAME, before any
    # resolution: a resolver can be configured to point them anywhere, and
    # admitting the name would make the refusal depend on local DNS config.
    #
    # ONE trailing dot is stripped first, matching fetch_citation.py exactly.
    # "localhost." is the fully-qualified spelling of the same name and resolves
    # identically, but matches neither test below. This file has NO resolver
    # behind it -- it runs on the offline path where nothing ever fetches -- so
    # unlike the fetcher there is no second net here and the miss would be the
    # whole check. rstrip, not one dot: U+2025/U+2026/U+FE30 fold to two, three
    # and four dots, so a single strip left a name the tests below could not match (see name_for_comparison).
    host = host.rstrip(".")
    name = _name_for_comparison(host)
    if name == "localhost" or name.endswith(".localhost"):
        return "localhost-name"

    # A host that is ALREADY an IP literal never goes through name resolution
    # at all, so the fetcher's resolution-time check would simply not run for
    # it -- this is the half that has to be caught statically.
    try:
        # No .strip("[]"): urlsplit().hostname has ALREADY removed the brackets
        # (measured: urlsplit("http://[::1]/x").hostname == "::1"), and a URL
        # whose brackets are unbalanced raises out of urlsplit above rather than
        # reaching here. The old strip was a no-op on the only live path AND a
        # character-set strip rather than a pair strip
        # ("]::1[".strip("[]") == "::1"), so it encoded the opposite assumption
        # to its twin. Removed in fetch_citation.py in round 4; this is the same
        # removal in the sibling that the round-4 fix did not reach.
        ip = ipaddress.ip_address(host)
    except ValueError:
        # The FOLDED form too: the four Unicode dot separators fold a spelling
        # like 127。0。0。1 into the canonical literal a resolver
        # sees, and ipaddress rejects the raw form outright. Without this the
        # address checks never run on exactly the inputs that reach the network.
        try:
            ip = ipaddress.ip_address(folded_host)
        except ValueError:
            ip = None   # a name, not a literal; the fetcher owns that half
    if ip is not None:
        reason = _non_global_address_reason(ip)
        if reason is not None:
            return reason

    try:
        port = parts.port
    except ValueError:
        return "invalid-port"
    if port is not None and not (0 < port < 65536):
        return "invalid-port"
    return None


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
    except UnicodeDecodeError as e:
        # UnicodeDecodeError is a ValueError, NOT an OSError, so without this it
        # escapes to main()'s catch-all and the operator loses the one thing
        # that locates the problem -- which file, and which flag named it.
        # _read_json_bytes() already translates it; this is that house rule
        # applied to the reader every flag-supplied JSON path goes through.
        raise CanonValidationError(f"{what} at {path} is not valid UTF-8: {e}")
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
    """The PARSED fragment, for every mode that only reads one (--merge-batches,
    --verify-merged, legacy --batch, and --check-batch without --approve-to).

    Reads BYTES, via _load_batch_bytes with the raw copy discarded. NOT for
    byte-identity -- read_text() and read_bytes() parse a fragment to the same
    document either way, since a raw CR is invalid JSON inside a string and mere
    inter-token whitespace outside one. The reason is error shape: read_text()
    raises UnicodeDecodeError on a non-UTF-8 fragment and _read_json_file does
    not catch it (FileNotFoundError, OSError and JSONDecodeError only --
    UnicodeDecodeError is a ValueError), so a REACHABLE failure escaped into
    main()'s defensive catch-all as "unexpected error: 'utf-8' codec can't decode
    byte ..." instead of this module's own failure naming the offending file.

    Routing every fragment read through one path has a welcome side effect: the
    bytes the citation reviewer audits and the document the merge parses now come
    off the same read, so the snapshot invariant needs no caveat.
    """
    _, doc = _load_batch_bytes(batch_path_str)
    return doc


def _read_json_bytes(path: Path, what: str):
    """Opt-in byte-exact variant of _read_json_file. Returns (raw_bytes, doc)
    from a SINGLE read_bytes(), so a caller that both validates the JSON and
    copies the file writes the exact bytes it validated -- no second read, no
    TOCTOU. read_text() must NOT be used for the copy: it applies universal-
    newline translation, so a CRLF fragment would be snapshotted with different
    bytes than it had on disk.

    Every FRAGMENT read now comes through here, via _load_batch_bytes: the
    --approve-to snapshot because it COPIES bytes, and the read-only fragment
    consumers because read_text() lets a non-UTF-8 fragment escape as an
    unhandled UnicodeDecodeError instead of a named failure (see _load_batch).
    _load_canon and _load_source_forms_manifest deliberately stay on the text
    path -- neither copies bytes. The same UnicodeDecodeError leak is reachable
    there for a hand-corrupted canon.json or manifest; closing that is separate
    debt, deliberately not smuggled into this change."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CanonValidationError(f"{what} not found at {path}")
    except OSError as e:
        raise CanonValidationError(f"could not read {what} at {path}: {e}")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise CanonValidationError(f"{what} at {path} is not valid UTF-8: {e}")
    except json.JSONDecodeError as e:
        raise CanonValidationError(f"{what} at {path} is not valid JSON: {e}")
    return raw, doc


def _load_batch_bytes(batch_path_str: str):
    """(raw_bytes, batch_list) for the fragment, mirroring _load_batch's
    array-shape check but reading bytes so the snapshot is byte-exact."""
    batch_path = Path(batch_path_str)
    raw, doc = _read_json_bytes(batch_path, "batch file")
    if not isinstance(doc, list):
        raise CanonValidationError(f"batch file at {batch_path} does not contain a JSON array")
    return raw, doc


def _same_json_value(a, b) -> bool:
    """Type-exact equality for two decoded JSON values -- the comparison the
    `--correct` interlock needs, which `==` is not.

    Python's `==` collapses the boolean/number boundary: `True == 1` and
    `False == 0`. Once `old_entry` accepts ANY JSON value (it must -- see the
    schema), that leaks straight into the interlock: an on-disk `true` could be
    matched by a stated `1`, and the correction would be applied and RECORDED
    with the wrong value. The record is this mode's whole deliverable, so a
    record that misstates what was on disk defeats it even when the edit itself
    was the one the operator wanted.

    Compared as canonical JSON text rather than by walking the structures:
    `sort_keys=True` makes object key ORDER irrelevant (it is not part of the
    value) while list order stays significant (it is), and the encoder writes
    `true` and `1` differently, which is the whole point. Both sides came out
    of `json.loads`, so both are always encodable.
    """
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def _attributable_to(value, source_form: str) -> bool:
    """True iff `value` -- either the correction document's stated old_item,
    or one raw review_queue[] row read off disk -- can be said to be ABOUT
    `source_form`. Two shapes qualify: a mapping whose own source_form
    field equals `source_form` (the ordinary queued shape, and the same
    identity `_merge_batch`'s accept-branch filter uses), or a bare string
    equal to `source_form` (the legacy shape review_queue[] can carry --
    `_load_canon` type-checks the array itself, never its items).

    Called on BOTH sides of run_correct's dismiss branch: once on old_item
    itself, and once per row in review_queue[]. NEITHER side is
    schema-constrained to these shapes -- old_item is UNCONSTRAINED in
    canon-correction.schema.json, deliberately (#653 code review: a
    schema-level `oneOf` there was strictly weaker than this function and
    produced a FALSE error message on rejection, so it was removed; this
    function is the actual, sole authority on the rule), and review_queue[]
    rows are not schema-constrained here either and can be any JSON value a
    hand edit left behind. Anything else -- a list, a number, a boolean,
    null, or a mapping naming some OTHER source_form -- cannot be about
    `source_form` and returns False.
    """
    if isinstance(value, dict):
        return value.get("source_form") == source_form
    if isinstance(value, str):
        return value == source_form
    return False


def _load_correction(correction_path_str: str) -> dict:
    """The parsed correction document for --correct (#495), read through
    `_read_json_bytes` for the same reason every fragment read is: a
    non-UTF-8 file raises UnicodeDecodeError out of read_text(), which
    `_read_json_file` does not catch, so it would escape into main()'s
    defensive catch-all as "unexpected error: 'utf-8' codec can't decode
    byte ..." instead of a failure naming the offending file. The raw bytes
    are discarded -- nothing snapshots them; the document itself is what
    gets appended to corrections[], and it is appended as the PARSED value
    so the record is canonicalized by the same json.dumps every other
    canon.json write goes through.
    """
    correction_path = Path(correction_path_str)
    _, doc = _read_json_bytes(correction_path, "correction file")
    if not isinstance(doc, dict):
        raise CanonValidationError(
            f"correction file at {correction_path} does not contain a JSON "
            f"object (one correction per call, never an array -- a batch of "
            f"corrections would reintroduce the partial-application question "
            f"--merge-batches solves by validating every fragment first)"
        )
    return doc


def _write_approved_snapshot(path: Path, raw: bytes) -> None:
    """Publish `raw` at `path` CREATE-ONCE: write it to a unique tmp name, then
    os.link() that into place. What this function does, and refuses:

      * nothing at `path` yet -> the link publishes it;
      * already there, SAME bytes -> idempotent no-op;
      * already there, DIFFERENT bytes -> CanonValidationError naming the path,
        with the bytes already published left untouched.

    os.link() rather than `if path.exists(): refuse; else: os.replace(...)`: a
    check-then-act guard closes only the SEQUENTIAL duplicate, because two
    concurrent first writers both observe an absent path, both write, the later
    os.replace() wins, and neither is told. os.link() puts the decision in the
    publication itself, raising FileExistsError for the loser whatever it observed
    beforehand. tests/canon_approve_to.test.py exercises this concurrently as a
    regression check.

    tmp-then-link rather than a plain O_CREAT|O_EXCL write to `path`, so the
    published file is never a half-written fragment. The tmp name carries pid +
    random bytes so concurrent writers cannot collide on it, and it is always
    unlinked -- after a successful link the content lives on under `path`.

    SCOPE: create-once lasts only as long as the directory entry does, and
    resume_setup.py's run-start wipe removes every approved_* file -- deliberately,
    to stop a run adopting an orphaned dir's stale attempt -- which reopens the
    slot. So this is bounded to one live run per run DIRECTORY -- directory, not
    RUN_ID string; the pointer below says why. This function takes no lock and
    binds no run identity into the snapshot.

    What the snapshot guarantees, and every precondition it rests on, is stated
    once and not repeated here -- see references/canon-and-glossary.md, "What the
    approved snapshot guarantees, and the preconditions it rests on".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
    tmp_path.write_bytes(raw)
    try:
        # Atomic create-once. Everything below this line is the LOSER's path.
        os.link(tmp_path, path)
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError as e:
            raise CanonValidationError(
                f"--approve-to target {path} already exists but could not be "
                f"read to compare against the fragment just validated: {e}",
                offending=[str(path)],
            )
        if existing == raw:
            return
        raise CanonValidationError(
            f"--approve-to refuses to overwrite the approved snapshot already at "
            f"{path}: its bytes differ from the fragment just validated, so a "
            f"second, DIFFERENT fragment is being approved into a slot a citation "
            f"reviewer may already have audited. The original bytes are left "
            f"untouched, so the merge still consumes exactly what was reviewed. "
            f"Fix the duplicate --check-batch --approve-to call (or the "
            f"overlapping reviewer dispatch) -- never re-point this path.",
            offending=[str(path)],
        )
    except OSError as e:  # pragma: no cover -- needs a hardlink-less filesystem
        # os.link() is the create-once guarantee, so a filesystem that cannot
        # provide it (some SMB/FAT mounts) must FAIL, loudly and by name. Quietly
        # falling back to an overwriting write would restore the exact race this
        # function exists to close, on precisely the setups nobody tests on.
        raise CanonValidationError(
            f"--approve-to could not publish the approved snapshot at {path}: {e}. "
            f"The snapshot is published with os.link() so that only one writer can "
            f"create it while it exists; the filesystem holding the durable_root "
            f"must support hard links. Refusing to fall back to an overwriting "
            f"write, which would silently reintroduce the duplicate-approval race.",
            offending=[str(path)],
        )
    finally:
        tmp_path.unlink(missing_ok=True)


GLOSSARY_APPROVAL_SCHEMA = "glossary-approval/1"


def _write_approval_record(path: Path, raw: bytes, recorded_from: str) -> None:
    """Publish the #723 verdict record for `raw` at `path`.

    WHAT THIS IS FOR, and the one thing it is not. The glossary pass runs this
    only after an independent citation review returned CITATIONS_OK for these
    exact bytes, so the record is the on-disk answer to "batch i, these bytes,
    passed the review" -- the fact an operator needs when they stop a pass and
    merge by hand, and the fact whose absence let a batch whose only recorded
    verdicts were REJECTIONS be merged under --citations-reviewed. It is read by
    PEOPLE, and since #734 by exactly one gate: --approval-records, which the
    merge modes require alongside --citations-reviewed and which refuses unless
    a record names the sha256 of the fragment being merged.

    THAT GATE CAN ONLY REFUSE, and the distinction is the whole design. A record
    never permits anything -- above all it never authorizes skipping the citation
    review, which stays unconditional for every batch on both entry points -- so
    a forged copy still buys its forger nothing beyond the merge an honest one
    would have allowed. Do not give it a consumer that PERMITS; see
    _enforce_approval_record() for what the one reader does and does not claim.

    REPLACING, not create-once -- the opposite choice from
    _write_approved_snapshot() above, and for the opposite reason. That function
    guards a slot two concurrent reviewers could both claim within one run. This
    one records the LATEST verdict for a batch, and a resumed run that
    re-reviews a batch and approves different bytes must supersede the stale
    record rather than be refused by it; a refusal there would leave the
    operator reading a record for bytes this run rejected.

    The digest is over the bytes the caller VALIDATED, taken from the same
    single read (_load_batch_bytes), never a re-read or a re-serialisation --
    the same TOCTOU discipline --approve-to already follows.
    """
    _atomic_write_json(
        path,
        {
            "schema": GLOSSARY_APPROVAL_SCHEMA,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "recorded_from": recorded_from,
        },
    )


def _enforce_approval_record(raw: bytes, record_path_str: str, fragment_path_str: str) -> None:
    """#734 -- refuse the merge unless `record_path_str` is the verdict record
    for exactly these bytes.

    THE IN-BAND CONSUMER _write_approval_record() ONCE FORBADE, ADDED ON PURPOSE
    and narrowed to what it can honestly do. #723 shipped the record as evidence
    with no reader, on the reasoning that the trusted point is the operator and a
    record nothing consults cannot be forged into an authorization. The reviewer
    that came back on it (#734) pointed at the other half: the pass ALREADY gated
    its merge on whether the record was written, and it gated on an AGENT'S
    SENTENCE saying so. That is not "nothing reads it" -- it is a consumer
    reading an unverified claim, which is the weakest possible version of one.

    WHAT THIS DOES AND DOES NOT BUY, stated narrowly because the difference
    decides whether the check is worth its lines. It cannot make a record
    trustworthy: the same agent turn that runs the write command reports on it,
    and a turn willing to fabricate the sentinel can equally run a command that
    writes a well-formed record over bytes it already holds. What it does close
    is the case that actually happens without malice -- the command was never
    run, or failed, and the reply said otherwise -- by moving the decision off
    the reply and onto the filesystem. So the guarantee is "the merge refuses
    unless a record for these exact bytes exists", never "the record was written
    honestly".

    IT GRANTS NOTHING. This function can only REFUSE. There is no path where a
    record's presence permits something that would otherwise be forbidden -- in
    particular it does not authorize skipping the citation review, which remains
    unconditional for every batch on both entry points. A forged record buys its
    forger exactly the merge an unforged one would have.

    The digest is compared against the bytes the CALLER read for merging, passed
    in rather than re-read here: a re-read would open the TOCTOU window that
    --approve-to and --record-approval-to are both written to avoid.
    """
    record_path = Path(record_path_str)
    doc = _read_json_file(record_path, "--approval-records")
    if not isinstance(doc, dict):
        raise CanonValidationError(
            f"approval record at {record_path} must be a JSON object, "
            f"got {type(doc).__name__}"
        )
    schema = doc.get("schema")
    if schema != GLOSSARY_APPROVAL_SCHEMA:
        raise CanonValidationError(
            f"approval record at {record_path} declares schema {schema!r}, "
            f"expected {GLOSSARY_APPROVAL_SCHEMA!r} -- this is not a glossary "
            f"citation-review verdict record"
        )
    recorded = doc.get("sha256")
    actual = hashlib.sha256(raw).hexdigest()
    if recorded != actual:
        raise CanonValidationError(
            f"approval record at {record_path} attests to sha256 {recorded!r}, "
            f"but the fragment being merged ({fragment_path_str}) hashes to "
            f"{actual}. The reviewed bytes and the bytes about to be merged are "
            f"not the same object, so --citations-reviewed cannot be honoured "
            f"for this fragment"
        )


def _paired_approval_records(batch_paths: list, approval_record_paths, citations_reviewed: bool):
    """The record path to enforce for each fragment, positionally, or None when
    no enforcement is owed.

    THE PAIRING IS POSITIONAL AND THE COUNTS MUST MATCH EXACTLY. Anything
    cleverer -- deriving `approval_{i}_attempt_{n}.json` from
    `approved_{i}_attempt_{n}.json`, say -- would teach this script the glossary
    template's private filename convention, and then a rename on either side
    would silently pair a fragment with a record that is not its own."""
    # DEFENCE IN DEPTH, AND IT HAS TO POINT THE OTHER WAY. main() refuses both
    # halves of the pairing on its own, so neither branch below is reachable
    # from the CLI; what decides which one is worth writing is what a future
    # in-process caller would LOSE by getting it wrong.
    #
    # ATTESTING WITH NO RECORDS is the direction that matters, and it is the one
    # this function used to answer with `[None] * len(batch_paths)` -- silently
    # enforcing nothing, on the exact merge that claims an independent review
    # approved these bytes. A caller in that state believes it is running the
    # #734 check and is not, and nothing in its output says so.
    #
    # RECORDS WITH NO ATTESTATION only ever ADDS refusals, so getting it wrong
    # is loud and safe. It is still refused, because a caller in that state has
    # misread what the pairing means -- but it is not the failure this guard
    # exists for, and writing only that one was the earlier mistake.
    if citations_reviewed and approval_record_paths is None:
        raise CanonValidationError(
            "internal: --citations-reviewed merged without --approval-records"
        )
    if approval_record_paths is None:
        return [None] * len(batch_paths)
    if not citations_reviewed:
        raise CanonValidationError(
            "internal: --approval-records enforced without --citations-reviewed"
        )
    if len(approval_record_paths) != len(batch_paths):
        raise CanonValidationError(
            f"--approval-records takes exactly one record per merged fragment, "
            f"in the same order: got {len(approval_record_paths)} record(s) for "
            f"{len(batch_paths)} fragment(s)"
        )
    return list(approval_record_paths)


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


def _labelled_sides(missing, extra) -> list:
    """Bound two offender lists into one reported list, keeping BOTH sides
    identifiable and spending the whole budget when only one side is populated.

    The first version split the budget 4-and-4 unconditionally. That defended
    the two-sided case by halving the common one -- a batch agent that dropped
    or added items produces a ONE-sided discrepancy, and it reported 4 names
    where the unbounded version reported 8. It also dropped the "... and N more"
    overflow marker (appended at index 8, discarded by the [:4] slice) and left
    the entries unlabelled, so a reader could not tell which side a name was on.
    """
    def _side(label, values):
        # The overflow marker _bounded_list appends is not an offender, so it
        # must not inherit a side prefix.
        bounded = _bounded_list(values)
        return [x if x.startswith("... and ") else f"{label}: {x}" for x in bounded]

    if not missing:
        return _side("extra", extra)
    if not extra:
        return _side("missing", missing)
    half = _MAX_LISTED_PROBLEMS // 2
    out = [f"missing: {x}" for x in _bounded_list(missing)[:half]]
    out += [f"extra: {x}" for x in _bounded_list(extra)[:half]]
    dropped = max(0, len(missing) - half) + max(0, len(extra) - half)
    if dropped:
        out.append(f"... and {dropped} more")
    return out


def _assert_exact_source_form_coverage(items: list, expected_forms: list) -> None:
    """Asserts the set of source_form values across `items` EXACTLY equals
    `expected_forms` -- no missing, no extra. Raises CanonValidationError
    naming both sides of any discrepancy, each side bounded to the
    first few entries with a count of the rest (mirrors the naming discipline of
    every other CanonValidationError raised in this module)."""
    got = {item.get("source_form") for item in items if isinstance(item, dict)}
    want = set(expected_forms)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing or extra:
        parts = []
        # _bounded_list on BOTH sides. Round 9 bounded the sites spelled
        # `", ".join(repr(o) for o in ...)` and missed this one because it
        # spells the same thing as an f-string list interpolation -- searching
        # for a SPELLING rather than for the property. Measured unbounded: a
        # 17,134-char message delivered as 4,037 chars with the injected
        # sentence 61 times, and the `extra` half -- the entirely
        # fragment-authored half -- evicted by the head-keeping cap.
        if missing:
            parts.append("missing from batch: " + ", ".join(_bounded_list(missing)))
        if extra:
            parts.append("unexpected extra in batch: " + ", ".join(_bounded_list(extra)))
        raise CanonValidationError(
            "batch does not exactly cover the expected source_form "
            "manifest (" + "; ".join(parts) + ")",
            # Both sides, interleaved, so the count cap cannot spend all 8
            # slots on `missing` and report none of the attacker-authored
            # `extra` -- which is what it did, measured n=9 maxlen=16.
            offending=_labelled_sides(missing, extra),
        )


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _stamp_generation_hash(field: str, plugin_root_str=None) -> str:
    """Shells out to `cache_key.py --field <field>` -- the one shared
    hashing implementation -- and returns its bare stdout value, stripped.
    Never independently recomputed here. A missing/failing cache_key.py, or
    an empty value, is FATAL: canon-file.schema.json only requires the
    generation_hashes KEYS be present strings, it cannot itself catch an
    empty-but-present value, so this script must refuse to write one.

    #412: `plugin_root_str` is this script's own --plugin-root CLI value
    (or None) -- resolve_cache_key_script() uses it to find the sibling
    cache_key.py. cache_key.py is a LEAF: it accepts --durable-root but
    REJECTS --plugin-root entirely (no siblings of its own to resolve), so
    --plugin-root is never forwarded to it. When it IS given, the sibling no
    longer physically sits under this script's own DURABLE_ROOT, so
    cache_key.py's own self-anchoring would otherwise resolve the WRONG
    tree's data -- an explicit `--durable-root str(DURABLE_ROOT)` is
    forwarded instead, synthesized from this script's own (always
    self-anchored) root.
    """
    cache_key_script = resolve_cache_key_script(plugin_root_str)
    if not cache_key_script.is_file():
        raise CanonValidationError(
            f"cannot stamp generation_hashes.{field}: {cache_key_script} not found"
        )
    cmd = [sys.executable, str(cache_key_script), "--field", field]
    if plugin_root_str is not None:
        cmd += ["--durable-root", str(DURABLE_ROOT)]
    try:
        proc = subprocess.run(
            cmd,
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
    'disposition'. Raises CanonValidationError naming the offending items (bounded to the first 8)
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
            "batch failed per-item schema validation:\n  " + _joined_problems(problems),
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
            problems.append(f"entries[{_bounded_message(repr(source_form))}]: {formatted}")

    for i, item in enumerate(canon.get("review_queue", [])):
        errors = _sorted_errors(queued_validator, item)
        if errors:
            label = _indexed_item_label("review_queue", i, item)
            problems.append(f"{label}: {_format_errors(errors, instance=item)}")

    if problems:
        raise CanonValidationError(
            "canon.json failed per-item schema validation:\n  " + _joined_problems(problems),
            offending=problems,
        )


# ---------------------------------------------------------------------------
# The basis:"established" claim -- ONE predicate, both gates that key on it
# ---------------------------------------------------------------------------


def _established_offenders(batch: list) -> list:
    """Every item in `batch` claiming basis:"established", named by its
    `source_form` (a positional `<item N>` placeholder when it carries none;
    non-dict junk is skipped rather than raising, so a malformed fragment
    still reaches the schema errors that describe it properly).

    ONE definition, deliberately, for BOTH gates that key on that claim: the
    offline backstop below (which forbids it outright) and #505's citation
    attestation after it (which admits it only against an operator-attested
    review). They ask the same question of the same field for different
    reasons, and the scope both their docstrings state -- keyed on `basis`,
    NEVER on `disposition` -- is a property of THIS predicate, not of either
    caller. Two copies could drift into disagreeing about what an
    `established` claim is, and the gate that stopped recognizing one would
    simply stop firing.
    """
    return [
        item.get("source_form", f"<item {i}>")
        for i, item in enumerate(batch)
        if isinstance(item, dict) and item.get("basis") == "established"
    ]


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
    offenders = _established_offenders(batch)
    if offenders:
        raise CanonValidationError(
            "research_mode=offline forbids basis:\"established\" for every new "
            "entry, but the batch claims it for: " + ", ".join(_bounded_list(offenders))
            + ". Reassign basis:\"transliterated\" (if mechanical transliteration "
            "suffices), basis:\"sense_translated\" (if a project-specific editorial "
            "sense-rendering fits -- style_bible.md §C -- no external citation "
            "needed), or disposition:\"review_queue\" with a note carrying the "
            "literal prefix \"SOURCE_UNAVAILABLE:\" instead -- the whole batch "
            "merge is rejected, canon.json is unchanged.",
            offending=offenders,
        )


# ---------------------------------------------------------------------------
# Citation-review attestation (#505)
# ---------------------------------------------------------------------------


def _enforce_citation_review_attestation(
    batch: list, research_mode: str, citations_reviewed: bool
) -> None:
    """Under research_mode == "live", FATALLY rejects the whole batch merge
    when ANY item claims basis:"established" and the caller did not pass
    --citations-reviewed. Nothing is written to canon.json when this fires.

    WHY THIS LIVES HERE AND NOT IN THE WORKFLOW. The pre-merge citation
    review (1.16.0/1.16.1) is the only thing anywhere that opens an
    `established` item's `source` and asks whether the page exists, documents
    the right entity, and attests the claimed canonical_target_form -- and it
    lives entirely inside glossary-pass-wf.template.js's control flow. This
    script was the writer for BOTH callers: the Workflow, which does run that
    review, and a hand-driven merge, which does not. A canon row is immutable
    (`--verify-merged` writes nothing, re-merging a different resolution for
    the same source_form is a fatal collision) and the downstream reviewer is
    forbidden to question a frozen canon form, so a fabricated citation that
    reached this function was frozen for the life of the project with no
    signal at all -- a green run, never a halt.

    WHAT THE FLAG IS, EXACTLY: an OPERATOR ATTESTATION, and nothing more. It
    proves nothing about whether a review ran, and cannot: no durable artifact
    records a CITATIONS_OK verdict, and the approved snapshot the reviewer
    audits is written by the PREPARE step BEFORE any evidence is fetched, so
    even "this path is an approved_*.json" would say nothing about the
    verdict. What the refusal converts is a SILENT freeze into a deliberate
    act -- the same ceiling, and the same shape, as #412's
    --plugin-root/--allow-durable-sibling and reject_review.py's attested
    --reason: prove what the kernel can, halt on the rest, let the operator
    assert the remainder knowingly.

    SCOPE -- keyed on `basis`, NEVER on `disposition`. canon-batch.schema
    .json's QUEUED branch requires only `note`, leaves `source` an
    unconstrained optional string, and still admits basis:"established"
    (see _enforce_citation_source_safety's own scope note, which is forced by
    the same schema fact). `_merge_batch` freezes a queued item into
    canon.json's review_queue[] verbatim, so an accepted-only scan would leave
    exactly that door open. The Workflow's own reviewer scopes by basis too.

    OFFLINE IS UNTOUCHED: `established` is forbidden outright there, and
    _enforce_offline_backstop -- which both merge runners call immediately
    BEFORE this one, though after schema validation and the citation-source
    safety check, either of which can reject an offline batch first on its own
    grounds -- rejects it with its own message. Telling an offline operator to
    attest a review that offline has no way to run would be worse than
    silence.
    """
    if research_mode != "live" or citations_reviewed:
        return
    offenders = _established_offenders(batch)
    if offenders:
        raise CanonValidationError(
            "research_mode=live admits basis:\"established\", but nothing here "
            "has been told a citation review approved these bytes, and an "
            "ACCEPTED row this merge freezes is revisable afterwards only by a "
            "deliberate --correct somebody has to know to run. No "
            "attestation was supplied for: " + ", ".join(_bounded_list(offenders))
            + ". The pre-merge citation review -- which retrieves each cited "
            "page through scripts/fetch_citation.py and rejects the batch "
            "unless the page exists, documents the right entity and attests "
            "the claimed canonical_target_form -- runs inside the glossary-pass "
            "Workflow, never in this script. Re-run with --citations-reviewed "
            "once such a review has approved these exact bytes. Auditing a "
            "citation by hand is done through scripts/fetch_citation.py too, "
            "never curl: see references/canon-and-glossary.md, \"Pre-merge "
            "citation review\". The whole batch merge is rejected, canon.json "
            "is unchanged.",
            offending=offenders,
        )


# ---------------------------------------------------------------------------
# Citation-source backstop (#347)
# ---------------------------------------------------------------------------


def _enforce_citation_source_safety(batch: list) -> None:
    """FATALLY rejects the whole batch if ANY item carries a `source` the
    static citation boundary refuses. Nothing is written when this fires.

    SCOPE -- every item carrying a `source`, NOT only basis:"established".
    This is not defensive over-reach, it is forced by the schema:
    canon-batch.schema.json's QUEUED branch types `source` as a bare
    unconstrained string (no `format`, no `minLength`, no conditional) and its
    `basis` enum still admits "established", so a
    `disposition: "review_queue"` item can carry `basis: "established"` plus
    an entirely arbitrary `source` and pass Pass 1 untouched. Narrowing this
    to the ACCEPTED branch would leave that door open. The ACCEPTED branch's
    own `format: "uri"` conditional is NOT a substitute either: it asks
    whether the string is a well-formed URI, and `http://169.254.169.254/` is
    a perfectly well-formed URI.

    Item selection mirrors fetch_citation.py's `iter_sources` exactly -- a
    missing, empty or non-string `source` is skipped here, because it is not a
    fetch target and its shape is Pass 1's business, not the boundary's. The
    two files must agree on WHICH items they cover, not merely on the checks
    they run; a divergence there would be a hole neither file's tests would
    show.
    """
    problems = []
    offenders = []
    for i, item in enumerate(batch):
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if not isinstance(source, str) or not source:
            continue
        reason = _citation_source_refusal(source)
        if reason is not None:
            problems.append(f"{_indexed_item_label('batch', i, item)}: {reason}")
            # Bounded for the same reason _indexed_item_label is: this list is
            # serialized into `offending`, which the prepare agent reads, and
            # round 5 capped the label while leaving this sibling raw.
            offenders.append(_bounded_message(item.get("source_form", f"<item {i}>")))

    if problems:
        raise CanonValidationError(
            "batch carries an unsafe citation source:\n  " + _joined_problems(problems)
            + "\nA citation `source` must be an ordinary public http(s) URL. This is "
            "refused STATICALLY here, before anything fetches it, because "
            "--check-batch also runs on the offline path where nothing ever "
            "fetches -- so this is the only place such a `source` can be stopped "
            "before it is frozen into canon.json (#347; see fetch_citation.py's "
            "module docstring for the full boundary). Fix the fragment upstream: "
            "cite a real public page, or drop the `source` and route the item to "
            "disposition:\"review_queue\" with a note explaining why no citation "
            "is available -- never by loosening this check. The whole batch is "
            "rejected; canon.json is unchanged.",
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
    same entry.

    #488 added `canon_senses.senses_for`, which computes exactly this, and
    this function was DELIBERATELY not re-pointed at it. The lookup here is
    normalized through THIS module's own `normalize_form` binding, and
    `tests/canon_validate_recollapse.test.py:289-301` pins the indexed
    fast path by monkeypatching that binding and asserting exactly one
    call; delegating would move the call inside canon_senses, silently
    reduce that count to zero, and force a passing performance test to be
    rewritten for a refactor #488 does not need. The shared comparator is
    still the single source of truth -- what is duplicated is four lines of
    dict lookup, not the normalizer or the split predicate."""
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
            + "\n  ".join(_bounded_list(collisions)),
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
            + ", ".join(_bounded_list(overlap)),
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
    and review_queue[] is only ever filtered/appended by `_merge_batch`, or
    filtered (never reordered) by run_correct's dismiss branch (#653), so a
    pure reorder is not reachable today by any writing mode; treating one as
    a change is the safe direction if that ever stops being true.

    A dismissal is nonetheless exempt from THIS function's re-stamp
    consequence, deliberately: run_correct passes `preserve_stamp=True` for
    every disposition including dismiss, so `_stamp_write_verify` never
    calls this comparison for it at all (see run_correct's own docstring,
    D5b). The stamp is a claim about DERIVATION PROVENANCE, not about
    every consumer's behaviour, so "unchanged" here is scoped to exactly
    what the stamp is about: carrying the exclusion a dismissed row's
    review_queue entry represented over into corrections[] is what keeps
    glossary_batch_plan.py's automated re-research exclusion unchanged.
    Other consumers DO see a dismissal -- canon_adjudication_audit.py stops
    enumerating the row (acceptance criterion 4 REQUIRES exactly this) and
    person_registry.py's refusals[] stops listing it -- and that is fine:
    neither reads generation_hashes, and select_segments.py's
    derivation-state gate is the one thing this stamp actually governs.
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
    canon_path: Path, merged: dict, registry: "Registry", force_restamp: bool = False,
    plugin_root_str=None, preserve_stamp: bool = False,
) -> "tuple[dict, bool]":
    """Shared by every mode that writes canon.json (`run_merge`,
    `run_merge_batches`, `run_init`, `run_restamp_derivation`, and -- with
    `preserve_stamp=True` -- `run_correct`): resolves
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

    `plugin_root_str` (#412) is this script's own --plugin-root CLI value
    (or None), threaded straight through to `_stamp_generation_hash()` --
    see that function's own docstring for the forwarding rule.

    `preserve_stamp=True` (#495, `--correct`) is the exact OPPOSITE
    override: the document DID change and the #291 rule would therefore
    restamp, but this caller must not, so the existing stamp is carried
    forward verbatim anyway. `_content_view` excludes only
    generation_hashes, so a corrected entry reads as a changed document --
    and restamping it would advance canon.json's particle_config /
    derivation-bundle provenance claim, which is precisely what
    select_segments.py's derivation-state gate reads to decide whether a
    particle_config edit or a bootstrap_names.py/segpack.py fix has been
    regenerated through. A correction regenerates nothing, so it must not
    clear that gate. Nothing is lost: the re-stale signal for a corrected
    entry is cache_key.py's per-segment `used_terms_hash`, never these
    hashes.

    Preserving requires something trustworthy to preserve, so
    `preserve_stamp` with no `_preservable_prior` is a REFUSAL, not a
    silent fresh stamp -- a correction that healed a corrupt stamp as a
    side effect would be exactly the silent provenance advance the whole
    parameter exists to prevent. `--restamp-derivation` is the one mode
    that may do that, deliberately and by name.

    Returns `(freshly re-read on-disk document, restamped)`.
    """
    if force_restamp and preserve_stamp:
        # Its own error rather than a fall-through to the preservation refusal
        # below, which would blame an unpreservable stamp -- true of the wrong
        # thing, and misleading to whoever wired the new caller.
        raise CanonValidationError(
            "internal: force_restamp and preserve_stamp are contradictory -- "
            "a writing mode must pass at most one"
        )
    # This re-reads canon.json from disk even though the caller already holds
    # a pre-merge copy. That is DELIBERATE, not redundant I/O -- see
    # _preservable_prior's docstring: reading here keeps the guard correct
    # even if a future refactor starts mutating the merge accumulator in
    # place, rather than resting on _merge_batch's docstring promise.
    prior = None if force_restamp else _preservable_prior(canon_path)
    if preserve_stamp and prior is None:
        raise CanonValidationError(
            f"canon.json at {canon_path} carries no trustworthy "
            f"generation_hashes stamp to preserve (absent, malformed, "
            f"incomplete or empty), and a correction never stamps one -- "
            f"it would advance the provenance claim "
            f"select_segments.py's derivation-state gate reads without "
            f"anything having been regenerated. Repair the stamp first "
            f"with --restamp-derivation, then re-run the correction."
        )
    # `not preserve_stamp` leads so a preserving caller short-circuits before
    # two _content_view builds, and so the line reads as what it means: a
    # preserving caller never restamps.
    restamped = not preserve_stamp and (
        prior is None or _content_view(merged) != _content_view(prior)
    )

    if restamped:
        merged.setdefault("generation_hashes", {})
        for field in GENERATION_HASH_FIELDS:
            merged["generation_hashes"][field] = _stamp_generation_hash(field, plugin_root_str)
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


def run_init(
    canon_path: Path, research_mode: str, registry: "Registry", plugin_root_str=None
) -> dict:
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

    `plugin_root_str` (#412) is this script's own --plugin-root CLI value,
    threaded through to `_stamp_write_verify`.
    """
    created = not canon_path.is_file()
    restamped = False
    if created:
        # No prior file, so _stamp_write_verify always stamps fresh here --
        # the #291 conservation path cannot apply to a bootstrap.
        _, restamped = _stamp_write_verify(
            canon_path, {"entries": {}, "review_queue": []}, registry,
            plugin_root_str=plugin_root_str,
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


def run_restamp_derivation(
    canon_path: Path, research_mode: str, registry: "Registry", plugin_root_str=None
) -> dict:
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

    `plugin_root_str` (#412) is this script's own --plugin-root CLI value,
    threaded through to `_stamp_write_verify`.
    """
    if not canon_path.is_file():
        raise CanonValidationError(
            f"canon.json not found at {canon_path} (nothing to restamp -- "
            f"bootstrap a new project with --init instead)"
        )

    canon = _load_canon(canon_path)
    _validate_existing_entries(canon, registry)

    before = dict(canon.get("generation_hashes") or {})
    on_disk, restamped = _stamp_write_verify(
        canon_path, canon, registry, force_restamp=True, plugin_root_str=plugin_root_str
    )
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


def _finish_correction(
    canon_path: Path,
    canon: dict,
    merged: dict,
    doc: dict,
    registry: "Registry",
    research_mode: str,
    source_form: str,
    disposition: str,
    **extra,
) -> dict:
    """The write-and-report tail every `run_correct` disposition shares --
    factored out because it was the SAME ~20 lines twice (#653 code
    review): append `doc` to corrections[], write through
    `_stamp_write_verify(..., preserve_stamp=True)` (a correction never
    stamps, whatever disposition), and build the result payload. `merged`
    must already carry whichever of entries{}/review_queue[] this call's
    own disposition owns -- this function only adds corrections{} and
    writes; it never decides entries{} vs review_queue[] content. `**extra`
    folds in a disposition-specific payload field (dismiss's
    `rows_dropped`) without a mode-shaped branch here -- there is only one
    payload dict, so a key added to it later cannot silently miss the
    other disposition the way two separately hand-written dicts could.
    """
    merged["corrections"] = list(canon.get("corrections", [])) + [doc]

    on_disk, restamped = _stamp_write_verify(
        canon_path, merged, registry, preserve_stamp=True
    )

    payload = {
        "success": True,
        "mode": "correct",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "source_form": source_form,
        "disposition": disposition,
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
        "corrections_count": len(on_disk["corrections"]),
        # Every writing mode answers this the same way. Always false here --
        # a correction (any disposition) carries the stamp forward
        # verbatim, by design.
        "generation_hashes_restamped": restamped,
    }
    payload.update(extra)
    return payload


def run_correct(
    canon_path: Path,
    correction_path: str,
    research_mode: str,
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
) -> dict:
    """--correct: apply ONE out-of-band, adjudicated correction, and record
    it in canon.json's corrections[] (#495; disposition:"dismiss" added by
    #653). Two disjoint targets: `correct`/`remove` repair an already-frozen
    entries{} record; `dismiss` drops one review_queue[] row instead,
    recording that a human looked at a queued candidate and judged it not
    canon-worthy. entries{} and review_queue[] are never both touched by one
    call.

    Why this is a separate mode rather than a relaxed merge: `_merge_batch`
    refuses an accepted item whose source_form already carries a DIFFERENT
    resolution, and that refusal is correct -- it is the only thing standing
    between a frozen decision and a re-adjudication pass silently
    overwriting it. But it made canon.json write-once in practice, so an
    entry that is simply WRONG (a factual person-merge, an interpolated name
    with zero source occurrences, one entity frozen under two spellings)
    could only be repaired by hand-editing the exact artifact the whole gate
    chain treats as frozen -- outside every validation this script owns, and
    recorded nowhere. A canon that contradicts the text is not inert: it
    keeps generating false review findings, and the cheapest way to silence
    those is to revert correct prose to match a wrong canon. The guard
    therefore pushed toward corrupting the deliverable.

    So: `_merge_batch` is NOT touched, there is no --force, and an ordinary
    batch carrying a differing resolution still raises. Correction is
    out-of-band, one entry per call, and states what it is changing FROM --
    `old_entry` must equal what is on disk or the call is refused naming
    both values, so the mode cannot be used blind against a canon.json that
    moved since it was read.

    Three dispositions. `correct`/`remove` share the interlock above
    (`old_entry`); the asymmetry between them is deliberate:

      correct -- replace the record under the same key. Refused when
        `source_form` is an adjudicated homonym split, through the SAME
        `is_split` predicate `_merge_batch`'s recollapse guard uses: any
        single bare entry for a form with 2+ senses is a recollapse, and
        substituting a different bare entry is still one bare entry.

      remove -- delete the record. Deliberately NOT split-refused.
        `canon_adjudication_audit.py`'s BLOCKING `collapsed_split` finding
        says "the underlying canon.json entry must actually be corrected"
        for exactly this state (a split added to the sidecar after the bare
        entry already existed), and no substituted value can satisfy it --
        removal is the only repair, so refusing it here would leave that
        finding with no route at all. Removal is also what an interpolated
        name with zero source occurrences needs, and a key RENAME is a
        remove followed by an ordinary --merge-batches under the new key.

      dismiss -- drop one review_queue[] row (#653). States `old_item`
        instead of `old_entry`: the same blind-use interlock, scoped to a
        queue row rather than an entries{} record. Checked FIRST against an
        attribution allowlist (`_attributable_to`) -- a mapping whose own
        source_form equals this document's, or a bare string equal to it,
        and nothing else -- before any search, so a document naming the
        wrong source_form can never be matched against some other name's
        row. Reads/writes review_queue[] only: no entries{}-key refusal (a
        form that is ALSO an entries{} key -- the overlap
        `_assert_no_entries_review_queue_overlap` forbids -- is exactly the
        state dismissing its queue row repairs; removing the ENTRY is a
        different decision, spelled disposition:"remove"), no split refusal,
        and none of the content controls below (a dismissal freezes
        nothing, same exemption `remove` already takes). Every row equal to
        `old_item` is dropped -- two rows for one form are ORDINARY
        (`_merge_batch` appends whenever the whole object differs, so one
        form queued by two batches for two different reasons is two rows;
        matching on the whole value dismisses one reason without silently
        dismissing the other).

    Writes through the SAME `_stamp_write_verify` path every other writing
    mode uses -- Pass 2 before disk, one atomic write, post-write re-read
    and re-validate -- but with `preserve_stamp=True`: see that function's
    docstring for why a correction must never advance the provenance claim.
    """
    if not canon_path.is_file():
        raise CanonValidationError(
            f"canon.json not found at {canon_path} (nothing to correct -- "
            f"bootstrap a new project with --init)"
        )

    doc = _load_correction(correction_path)
    validator = _validator_for_schema_file("canon-correction.schema.json", registry)
    errors = _sorted_errors(validator, doc)
    if errors:
        raise CanonValidationError(
            "correction document failed schema validation: "
            + _format_errors(errors, instance=doc, root_schema=validator.schema)
        )

    canon = _load_canon(canon_path)
    # Loaded before the disposition branch, exactly as run_merge/
    # run_merge_batches load it: only the `correct` branch READS it, but an
    # EXPLICIT --senses-path that turns out missing must block in every
    # mode alike, never silently read as "no splits yet" in the one mode
    # that happens not to need it.
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)

    source_form = doc["source_form"]
    disposition = doc["disposition"]

    if disposition == "dismiss":
        # A wholly separate path -- reads/writes review_queue[] only, and
        # NEVER touches entries{} (see the docstring's dismiss paragraph for
        # why: no entries{}-key refusal, no split refusal, no content
        # controls). Kept as its own branch rather than folded into the
        # entries{}-membership checks below, which do not apply here at all.
        old_item = doc["old_item"]
        if not _attributable_to(old_item, source_form):
            # Checked BEFORE any search: a document whose stated row cannot
            # belong to its own source_form is refused on that ground alone,
            # naming both, so it can never be matched against some other
            # name's row.
            raise CanonValidationError(
                f"{source_form!r}: stated old_item "
                f"{_bounded_message(repr(old_item))} is not attributable to "
                f"this document's own source_form -- a dismissal must name a "
                f"row that IS the form it claims to dismiss (a mapping whose "
                f"own source_form field equals {source_form!r}, or the bare "
                f"string {source_form!r} itself).",
                offending=[_bounded_message(repr(source_form))],
            )

        # No `list()` copy: unlike `_merge_batch` (which APPENDS to its own
        # local in place), every rebind below is a fresh comprehension, so
        # there is nothing in-place to protect the original from.
        review_queue = canon.get("review_queue", [])
        attributable_rows = [
            row for row in review_queue if _attributable_to(row, source_form)
        ]
        # old_item is dumped to canonical JSON text ONCE here, not once per
        # row per traversal: `_same_json_value` dumps BOTH sides on every
        # call, and an operator-authored old_item of unbounded size was
        # otherwise re-serialized 2xN times across the match check and the
        # drop pass below (code review, #653).
        old_item_dump = json.dumps(old_item, sort_keys=True, ensure_ascii=False)

        def _matches_old_item(row) -> bool:
            return json.dumps(row, sort_keys=True, ensure_ascii=False) == old_item_dump

        if not any(_matches_old_item(row) for row in attributable_rows):
            # The same blind-use interlock --correct already enforces for
            # old_entry, scoped to a queue row: both the stated value and
            # what is actually queued under this source_form are named.
            # old_item is BOUNDED here, matching the treatment
            # `_bounded_list` already gives attributable_rows beside it --
            # an unbounded old_item would otherwise push "Currently queued
            # under ...: <the actual rows>" past CanonValidationError's
            # 4000-char TAIL truncation, deleting exactly the recovery
            # information this interlock exists to deliver.
            raise CanonValidationError(
                f"{source_form!r}: stated old_item does not match any row "
                f"currently queued under this source_form -- refusing to "
                f"dismiss blind. Stated: {_bounded_message(repr(old_item))}. "
                f"Currently queued under {source_form!r}: "
                + ", ".join(_bounded_list(attributable_rows))
                + ". Re-read canon.json and re-author the correction "
                f"against its CURRENT value.",
                offending=[_bounded_message(repr(source_form))],
            )
        # Drop EVERY row equal to old_item, in ONE traversal. Two rows for
        # one form are ORDINARY (see the docstring), so this is a
        # whole-value match, not "the first row" or "every row for this
        # source_form" -- `rows_dropped` is the count that DIDN'T survive,
        # not a second pass re-counting matches.
        kept = [row for row in review_queue if not _matches_old_item(row)]
        rows_dropped = len(review_queue) - len(kept)

        merged = dict(canon)
        merged["review_queue"] = kept
        # entries{} is passed through untouched -- not even copied, since
        # `merged = dict(canon)` already carries it forward verbatim.
        return _finish_correction(
            canon_path, canon, merged, doc, registry, research_mode,
            source_form, disposition, rows_dropped=rows_dropped,
        )

    entries = dict(canon.get("entries", {}))
    if source_form not in entries:
        raise CanonValidationError(
            f"{source_form!r}: no such entry in canon.json's entries{{}} -- a "
            f"correction never inserts blind. Add a new name through the "
            f"ordinary glossary-pass merge (--merge-batches) instead.",
            offending=[_bounded_message(repr(source_form))],
        )

    existing = entries[source_form]
    old_entry = doc["old_entry"]
    if not _same_json_value(existing, old_entry):
        # The blind-use interlock, and the whole reason old_entry is
        # required. Both values are named: a correction authored against a
        # stale read must show the operator WHAT it read and what is
        # actually there, not merely that they differ.
        raise CanonValidationError(
            f"{source_form!r}: stated old value does not match canon.json -- "
            f"refusing to correct blind. On disk: {existing!r}. Stated: "
            f"{old_entry!r}. Re-read canon.json and re-author the correction "
            f"against its CURRENT value.",
            offending=[_bounded_message(repr(source_form))],
        )

    if disposition == "correct":
        new_entry = doc["new_entry"]
        if _same_json_value(new_entry, old_entry):
            raise CanonValidationError(
                f"{source_form!r}: new_entry is identical to old_entry -- "
                f"nothing to correct. The merge path treats an identical "
                f"resubmission as a silent no-op; a correction must not, or a "
                f"mis-authored one reports success having changed nothing."
            )
        if new_entry.get("source_form") != source_form:
            raise CanonValidationError(
                f"{source_form!r}: new_entry's own source_form field is "
                f"{new_entry.get('source_form')!r} -- the entries{{}} map key "
                f"and the record's own authoritative source_form must agree. "
                f"To change the KEY itself, remove this entry and add the "
                f"corrected form through --merge-batches."
            )
        # The two content controls every OTHER route into entries{} enforces.
        # Passing the ENTRY to helpers written for batch ITEMS is exact, not a
        # coercion: `_enforce_citation_source_safety` selects on carrying a
        # `source` string (mirroring fetch_citation.iter_sources) and
        # `_enforce_offline_backstop` on `basis` -- neither reads
        # `disposition`. Omitting them was a measured hole, not a theoretical
        # one: --correct froze a `source` of "http://127.0.0.1:8080/x" that
        # --merge-batches refuses as `loopback-address`, and froze
        # basis:"established" under --research-mode offline, which the merge
        # path refuses outright. #347's docstring calls itself "the only place
        # such a `source` can be stopped before it is frozen into canon.json",
        # and a second write path that skipped it would have made that false.
        #
        # The `remove` branch is deliberately exempt from both: they constrain
        # what may be FROZEN, and a removal freezes nothing. Refusing a removal
        # because the entry being deleted carries a bad `source` would trap the
        # exact record most worth deleting.
        _enforce_citation_source_safety([new_entry])
        # `old_entry` is an UNCONSTRAINED JSON value on purpose (see the
        # schema): the row most worth correcting is one a hand edit left
        # malformed, and that includes a string, an array or a null under an
        # entries{} key -- `_load_canon` type-checks `entries` itself, never
        # its values. So read `basis` off it defensively; a non-mapping simply
        # has no basis, which is the right answer here (any new established
        # claim over it IS new).
        old_is_dict = isinstance(old_entry, dict)
        # WHAT THE CLAIM IS, and why keying this on `basis` alone was wrong.
        # An established entry asserts "this canonical_target_form is the
        # conventional one, and here is the citation". So the claim is the
        # (canonical_target_form, source) PAIR, not the basis label. Scoping
        # only on `basis` let a correction replace BOTH halves offline and
        # keep the exemption purely because the old row also said
        # "established" -- i.e. rewrite the claim without ever declaring that
        # research was possible. Caught on PR review; reproduced before fixing.
        claim_unchanged = (
            old_is_dict
            and old_entry.get("basis") == "established"
            and _same_json_value(
                new_entry.get("canonical_target_form"),
                old_entry.get("canonical_target_form"),
            )
            and _same_json_value(new_entry.get("source"), old_entry.get("source"))
        )
        if new_entry.get("basis") == "established" and not claim_unchanged:
            # SCOPED TO THE CLAIM, and the citation check above deliberately
            # is not scoped at all -- the asymmetry is measured, not aesthetic.
            #
            # The backstop's own rule is "offline forbids basis:established for
            # every NEW entry", and the claim_unchanged test above is what
            # decides "new" here. It compares the (canonical_target_form,
            # source) pair rather than the `basis` label, because that pair IS
            # the assertion an established row makes: "this rendering is the
            # conventional one, and here is the citation". Restating either half
            # offline is a new claim about the world whether or not the label
            # moved -- an earlier revision keyed on the label alone and let a
            # correction replace BOTH halves offline while keeping the exemption.
            #
            # What the scoping still admits, and why it exists: correcting
            # note/confidence/category on an established row restates no claim
            # and stays legal offline. That is not a corner -- 488 of the 999
            # frozen entries across the four live books are basis:"established",
            # so an unscoped call would put half the corpus out of reach of the
            # mode built to repair it. Do NOT widen this back to the label to
            # make an offline canonical_target_form edit legal again; that edit
            # is a restated claim, and its answer is --research-mode live.
            #
            # The citation check stays absolute because the same measurement
            # runs the other way: 0 of 497 sourced entries in that corpus carry
            # a `source` it refuses, so scoping it to changed-source-only would
            # buy nothing real while adding a second condition to get right --
            # and it would reopen the promotion case where a
            # transliterated->established entry keeps an unsafe source that only
            # now becomes a citation. Absolute is simpler AND stronger here.
            try:
                _enforce_offline_backstop([new_entry], research_mode)
            except CanonValidationError as e:
                # The helper's remediation advice is batch-shaped and names
                # disposition:"review_queue", which this mode's enum does not
                # admit -- following it is impossible. Re-raise naming the two
                # moves that DO exist here.
                raise CanonValidationError(
                    f"{e} (this correction STATES an established claim -- a "
                    f"canonical_target_form and its source -- that the frozen "
                    f"row did not already carry, which is a new claim about "
                    f"the world, so it needs --research-mode live; or correct "
                    f"it to an offline-legal basis instead. "
                    f"disposition:\"review_queue\" is a BATCH disposition and "
                    f"is not available to --correct.)",
                    offending=e.offending,
                )
        if is_split(senses, source_form):
            split_entry = _matching_senses_entry(senses, source_form)
            n = len(split_entry.get("senses", [])) if split_entry else 0
            raise CanonValidationError(
                f"{source_form!r}: is an adjudicated homonym split "
                f"({n} senses in canon_senses.json) -- refusing to correct it "
                f"into another single bare entry (recollapse). A form with two "
                f"referents has no one canonical target form; use "
                f"disposition:\"remove\" to clear the collapsed entry.",
                offending=[_bounded_message(repr(source_form))],
            )
        entries[source_form] = new_entry
    else:  # "remove" -- "dismiss" already returned above, and the schema's
        # enum admits nothing else
        del entries[source_form]

    merged = dict(canon)
    merged["entries"] = entries
    # review_queue[] is passed through untouched -- not even copied, since
    # `merged = dict(canon)` already carries it forward verbatim. Append-only
    # corrections[]/write/report tail lives in `_finish_correction`, shared
    # with the dismiss branch above.
    return _finish_correction(
        canon_path, canon, merged, doc, registry, research_mode,
        source_form, disposition,
    )


def _validate_and_enforce_batch(
    batch: list,
    registry: "Registry",
    research_mode: str,
    citations_reviewed: "bool | None",
) -> None:
    """Every per-fragment gate, in the ONE order every batch path must run
    them. THE single entry point -- `run_check_batch`, `run_merge_batches` and
    the legacy `run_merge` all call this and nothing else.

    It exists because they did NOT, and the divergence was invisible: the four
    calls were open-coded three times, so #383's refusal was added to two of
    them and the legacy `--batch PATH` path silently kept writing the forbidden
    item into entries{}. Review caught it; nothing in the suite could, because
    each path had its own tests and each passed. A fourth path added later
    inherits every gate by construction rather than by whoever writes it
    remembering all four.

    Order is load-bearing and is documented at each step's own definition:
    Pass 1 first, so a structurally broken item is reported as broken rather
    than as something else; the citation-safety check before the offline
    backstop, so an unsafe `source` is reported in BOTH research modes rather
    than only in the one that would reject the item for another reason.

    `citations_reviewed` (#505) has NO default on purpose: it is the caller's
    --citations-reviewed attestation, and `None` is the explicit "this path
    writes no canon row, so it can freeze no citation" answer that
    `run_check_batch` gives. A fourth path must therefore state which of the
    two it is rather than inherit a fail-open default it never considered.
    """
    _validate_batch_items(batch, registry)
    _enforce_no_truncated_accepted(batch)
    _enforce_citation_source_safety(batch)
    _enforce_offline_backstop(batch, research_mode)
    if citations_reviewed is not None:
        _enforce_citation_review_attestation(batch, research_mode, citations_reviewed)


def run_merge(
    canon_path: Path,
    batch_path: str,
    research_mode: str,
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
    plugin_root_str=None,
    citations_reviewed: bool = False,
    approval_record_paths=None,
) -> dict:
    """Legacy single-fragment merge path (--batch PATH). Equivalent to
    `run_merge_batches(canon_path, [batch_path], ...)`, kept as its own
    code path because existing tests/callers already invoke it this way.

    `plugin_root_str` (#412) is this script's own --plugin-root CLI value,
    threaded through to `_stamp_write_verify`. `citations_reviewed` (#505) is
    --citations-reviewed, the operator's attestation that an independent
    citation review approved these exact bytes; `approval_record_paths` (#734)
    is the verdict record that attestation rests on. Both are enforced here as
    well as in run_merge_batches: this path is a merge under the same flag, and
    a mode that accepted the attestation without the record would be the hole
    #734 closes, reachable by one different CLI spelling.
    """
    raw, batch = _load_batch_bytes(batch_path)
    records = _paired_approval_records([batch_path], approval_record_paths, citations_reviewed)
    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)

    _validate_and_enforce_batch(batch, registry, research_mode, citations_reviewed)
    if records[0] is not None:
        _enforce_approval_record(raw, records[0], batch_path)
    merged = _merge_batch(canon, batch, senses)

    on_disk, restamped = _stamp_write_verify(
        canon_path, merged, registry, plugin_root_str=plugin_root_str
    )

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
    approve_to: "str | None" = None,
    record_approval_to: "str | None" = None,
) -> dict:
    """--check-batch PATH [--expect-source-forms-file M.json]: Pass 1 +
    offline backstop on ONE fragment. No write unless --approve-to or
    --record-approval-to asks for one, and then only after every check below
    has passed. When a manifest is given,
    additionally asserts exact source_form coverage. ALSO loads canon.json
    (read-only -- an absent file is the same fresh skeleton _load_canon
    always returns; nothing is ever written here) and canon_senses.json,
    then dry-runs `_merge_batch` (its return value discarded) so the
    refuse-recollapse guard -- and any ordinary entries{} collision --
    rejects a doomed fragment at precheck/readiness time (RFC #215 1d),
    not only at the final --merge-batches call.
    """
    # When --approve-to or --record-approval-to is set we need the fragment's
    # exact bytes -- to snapshot them, to digest them, or both -- and they must
    # come from the SAME read that is validated, otherwise a second read between
    # validation and use is a TOCTOU. So take the bytes up front and derive the
    # parsed batch from them; the snapshot below writes these same bytes, never
    # a re-serialisation, and the record digests these same bytes.
    raw_bytes = None
    if approve_to is not None or record_approval_to is not None:
        raw_bytes, batch = _load_batch_bytes(batch_path)
    else:
        batch = _load_batch(batch_path)
    # None: check_batch writes no canon row, so it can freeze no citation.
    _validate_and_enforce_batch(batch, registry, research_mode, None)
    if manifest_path is not None:
        expected_forms = _load_source_forms_manifest(manifest_path)
        _assert_exact_source_form_coverage(batch, expected_forms)

    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)
    _merge_batch(canon, batch, senses)

    result = {
        "success": True,
        "mode": "check_batch",
        "source_forms": len({item.get("source_form") for item in batch if isinstance(item, dict)}),
    }
    # Snapshot ONLY after every check above has passed, so a rejected fragment
    # never leaves an approved copy. raw_bytes is exactly what was validated, and
    # _write_approved_snapshot refuses to put DIFFERENT bytes over a snapshot a
    # reviewer may already have audited (write-once per content).
    if approve_to is not None:
        if raw_bytes is None:  # unreachable: set together with approve_to above
            raise CanonValidationError("internal: --approve-to set but fragment bytes unread")
        _write_approved_snapshot(Path(approve_to), raw_bytes)
        result["approved_path"] = approve_to
    # #723, LAST and only after every check above: the verdict record vouches
    # for bytes, so it must never be written for bytes that failed a check, and
    # never before the snapshot those bytes are published as. Ordering is the
    # whole of its correctness -- see _write_approval_record().
    if record_approval_to is not None:
        if raw_bytes is None:  # unreachable: set together with record_approval_to
            raise CanonValidationError(
                "internal: --record-approval-to set but fragment bytes unread"
            )
        _write_approval_record(Path(record_approval_to), raw_bytes, batch_path)
        result["approval_record_path"] = record_approval_to
    return result


def run_merge_batches(
    canon_path: Path,
    batch_paths: list,
    research_mode: str,
    registry: "Registry",
    senses_path: Path,
    allow_absent_senses: bool,
    plugin_root_str=None,
    citations_reviewed: bool = False,
    approval_record_paths=None,
    glossary_merge_marker_path=None,
) -> dict:
    """--merge-batches P1 P2 ...: single process, single canon.json load.
    Validates ALL given fragments (Pass 1 + offline backstop) FIRST, before
    merging any of them, then threads `acc = _merge_batch(acc, frag,
    senses)` across every fragment IN THE GIVEN ORDER -- ONE senses load,
    shared across every fragment in this call.

    `plugin_root_str` (#412) is this script's own --plugin-root CLI value,
    threaded through to `_stamp_write_verify`. `citations_reviewed` (#505) is
    --citations-reviewed, the operator's attestation that an independent
    citation review approved these exact bytes -- checked per fragment inside
    the SAME pre-merge loop, so a later fragment's unattested citation refuses
    before an earlier one has been merged.

    `approval_record_paths` (#734) is --approval-records, one record per fragment
    in the same order, and it is what makes that attestation rest on something:
    each record must name the sha256 of the fragment it is paired with. Enforced
    in that same pre-merge loop, and over the bytes read HERE -- the raw copy
    _load_batch_bytes returns, never a re-read -- so the digest is taken of the
    object that is about to be merged.

    `glossary_merge_marker_path` (#820) is --glossary-merge-marker: written
    ONLY after `_stamp_write_verify()` below has already written canon.json
    and re-read it fresh from disk, i.e. only once the merge is genuinely
    established -- see `_write_glossary_merge_marker()`'s own docstring."""
    loaded = [_load_batch_bytes(p) for p in batch_paths]
    batches = [doc for _raw, doc in loaded]
    records = _paired_approval_records(batch_paths, approval_record_paths, citations_reviewed)
    for (raw, batch), batch_path, record_path in zip(loaded, batch_paths, records):
        _validate_and_enforce_batch(batch, registry, research_mode, citations_reviewed)
        if record_path is not None:
            _enforce_approval_record(raw, record_path, batch_path)

    canon = _load_canon(canon_path)
    senses = _load_senses_or_raise(senses_path, allow_absent_senses)
    acc = canon
    for batch in batches:
        acc = _merge_batch(acc, batch, senses)

    on_disk, restamped = _stamp_write_verify(
        canon_path, acc, registry, plugin_root_str=plugin_root_str
    )

    if glossary_merge_marker_path is not None:
        # Only NOW: canon.json has been written AND re-read fresh from disk
        # by _stamp_write_verify() above, so the merge this marker records
        # is genuinely established, not merely attempted.
        _write_glossary_merge_marker(Path(glossary_merge_marker_path), batch_paths)

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
    # BOUNDED HERE, because this mode reports failure through a SUCCESS-shaped
    # payload and therefore never constructs CanonValidationError -- so round 8's
    # "the one place every failure passes through" was false for exactly this
    # path. `missing` carries raw fragment-authored source_forms, and
    # glossaryVerifyPrompt tells an agent to read this line and return `missing`
    # COPIED VERBATIM, against MANIFEST_ALL (the whole run, not one batch).
    # Measured before this bound: 196 KB at 40 items and 2.4 MB at 500, linear
    # in both count and length -- the same magnitude the central bound was added
    # to close, on the last gate before merged:true.
    return {"verified": not missing, "missing": _bounded_list(missing)}


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
        "--approval-records",
        metavar="PATH",
        nargs="+",
        default=None,
        help=(
            "#734. One glossary citation-review verdict record per fragment "
            "given to --merge-batches (or the single legacy --batch fragment), "
            "IN THE SAME ORDER. Each must declare schema "
            "\"glossary-approval/1\" and the sha256 of the fragment it is "
            "paired with, or the merge refuses before any fragment is merged. "
            "REQUIRED WITH --citations-reviewed and meaningless without it: the "
            "attestation says an independent citation review approved these "
            "exact bytes, and this is the on-disk evidence that it did. Written "
            "by --record-approval-to at the end of the reviewing "
            "--check-batch run."
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
        "--correct",
        metavar="PATH",
        default=None,
        help=(
            "#495: apply ONE out-of-band, adjudicated correction to an "
            "already-frozen canon.json entries{} record, or dismiss one "
            "review_queue[] row (#653). PATH is a canon-correction.schema.json "
            "document; it must state the OLD value (refused, naming both, "
            "when it does not match disk) and carry a reason, and it "
            "dispositions 'correct'/'remove' (entries{}, old_entry) or "
            "'dismiss' (review_queue[], old_item). Deliberately NOT a relaxed "
            "merge, and the one WRITING mode that does not STAMP -- see this "
            "file's module docstring."
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
        "--approve-to",
        metavar="PATH",
        default=None,
        help=(
            "Only with --check-batch: on a PASS, atomically snapshot the "
            "EXACT validated bytes of the fragment to PATH so the citation "
            "reviewer audits the approved snapshot and the merge consumes that "
            "same copy. Writes nothing on failure. Refused in every other "
            "mode."
        ),
    )
    parser.add_argument(
        "--record-approval-to",
        metavar="PATH",
        default=None,
        help=(
            "#723. Only with --check-batch: on a PASS, atomically write a "
            "verdict record to PATH naming the sha256 of the EXACT validated "
            "bytes and the path they were read from. Written by the glossary "
            "pass ONLY after an independent citation review approved those "
            "bytes, so that the operator's later --citations-reviewed "
            "attestation has something on disk to rest on -- selecting the "
            "attested snapshot by digest instead of guessing which snapshot "
            "was the approved one. Since #734 exactly one gate reads it back: "
            "--approval-records, which the merge modes REQUIRE alongside "
            "--citations-reviewed. That gate can only REFUSE -- a record never "
            "permits anything, and above all never authorizes skipping the "
            "citation review -- so a forged copy still grants its forger only "
            "the merge an honest one would have. Writes nothing on failure. "
            "Refused in every other mode."
        ),
    )
    parser.add_argument(
        "--citations-reviewed",
        action="store_true",
        help=(
            "#505. Only with a merge mode (--merge-batches or the legacy bare "
            "--batch): attests that an independent citation review approved "
            "the exact bytes of every fragment named on this command line. "
            "Under --research-mode live a merge carrying any "
            "basis:\"established\" item is REFUSED without it -- the "
            "pre-merge citation review runs inside the glossary-pass Workflow, "
            "never in this script, so a hand-driven merge would otherwise "
            "freeze an unaudited citation into a canon row nothing downstream "
            "may question, with no signal. It is an attestation, not a proof -- but "
            "since #723 it is no longer unsupported: a glossary pass writes a "
            "verdict record (--record-approval-to) naming the sha256 of every "
            "approved fragment, so the operator attesting here can select those "
            "exact bytes by digest rather than by guesswork. Since #734 this "
            "flag REQUIRES those records: pass --approval-records, one per "
            "merged fragment in the same order, or the merge refuses. The "
            "attestation stays the operator's -- the records are what it rests "
            "on, checked here rather than taken on trust. Refused in every "
            "non-merge mode."
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
        "--glossary-merge-marker",
        metavar="PATH",
        default=None,
        help=(
            "#820. Only with --merge-batches: on a SUCCESSFUL merge (after "
            "canon.json has been written AND re-read fresh from disk to "
            "confirm it landed), atomically write a durable "
            "'glossary-run-merged/1' marker to PATH -- {run_id, merged_at, "
            "batches, source:\"merge\"}. run_id is taken from PATH's own "
            "parent directory name and refused if unsafe. This is what "
            "lets select_segments.py's W5 admission gate tell a genuinely "
            "MERGED glossary run apart from one that only produced "
            "fragments, without re-deriving that fact from any mutable "
            "project input. Refused in every other mode."
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
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "#412: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling cache_key.py script "
            "this script shells out to (to STAMP generation_hashes), as "
            "{PATH}/assets/scripts/cache_key.py -- deliberately NEVER "
            "derived from this script's own self-anchored durable root, "
            "because ${durable_root}/scripts/ is writable by the codex "
            "process this stamp gates (codex_job.py grants --write over "
            "the whole durable root), so resolving the checker from inside "
            "the tree it checks would let a tampered copy validate itself. "
            "cache_key.py is a LEAF and does not accept --plugin-root at "
            "all, so it is never forwarded to it; only a synthesized "
            "--durable-root is. REQUIRED by the STAMPING modes -- the "
            "four that WRITE generation_hashes (--init, "
            "--restamp-derivation, --merge-batches, legacy --batch) -- "
            "unless --allow-durable-sibling is given instead; ignored by "
            "every other mode, which resolves no sibling. STAMPING is now "
            "the NARROWER set: #495's --correct also writes canon.json, "
            "but carries its existing stamp forward verbatim and computes "
            "no hash, so it resolves no sibling and takes neither flag. "
            "Read any 'writing modes' phrasing elsewhere as these four "
            "STAMPING ones."
        ),
    )
    parser.add_argument(
        "--allow-durable-sibling",
        action="store_true",
        help=(
            "#412: run a STAMPING mode with the self-anchored "
            "${durable_root}/scripts/cache_key.py, accepting that the "
            "sibling comes out of a directory the codex process this stamp "
            "gates can write to. The explicit escape hatch for the case "
            "with no orchestrating session to supply --plugin-root (a "
            "hand-run recovery, a fully self-anchored drive). Mutually "
            "exclusive with --plugin-root: naming a trusted root AND "
            "waiving the requirement at once states two different "
            "intentions. Ignored by the non-stamping modes, which resolve "
            "no sibling at all."
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
    if args.expect_source_forms_file is not None:
        if source_forms_refusers:
            parser.error(
                "; ".join(
                    f"{spec.flag} does not accept --expect-source-forms-file "
                    f"({spec.source_forms_refusal})"
                    for spec in source_forms_refusers
                )
            )
        elif not selected_modes:
            # VALIDATE-ONLY (no mode flag) has no MODE_SPECS row, so the
            # comprehension above never reaches it -- guard it by hand. It
            # reads no fragment and runs no coverage check (exactly like
            # --init), so there is nothing --expect-source-forms-file could
            # verify; honoring it silently was the false-success bug. Refuse
            # loudly and point at the modes that DO enforce coverage.
            parser.error(
                "validate-only (no mode flag) does not accept "
                "--expect-source-forms-file -- it reads no fragment and runs "
                "no coverage check. Pass --check-batch or --verify-merged to "
                "enforce source-forms coverage."
            )
    # Every mode that refuses a FRAGMENT-BYTES flag, with its own reason -- the
    # same loud-refusal design as --expect-source-forms-file above, so neither a
    # snapshot nor a verdict record is ever silently produced for a mode that
    # reviewed no single fragment. Driven by ONE table column over BOTH flags:
    # the refusal reason is a property of the MODE, not of which flag was
    # passed, so a per-flag column would be the same list maintained twice.
    fragment_bytes_refusers = [
        spec for spec in selected_modes if spec.fragment_bytes_flag_refusal is not None
    ]
    for flag_name, flag_value in (
        ("--approve-to", args.approve_to),
        ("--record-approval-to", args.record_approval_to),
    ):
        if flag_value is None:
            continue
        if fragment_bytes_refusers:
            parser.error(
                "; ".join(
                    f"{spec.flag} does not accept {flag_name} "
                    f"({spec.fragment_bytes_flag_refusal})"
                    for spec in fragment_bytes_refusers
                )
            )
        elif not selected_modes:
            # VALIDATE-ONLY (no mode flag) has no MODE_SPECS row, so the
            # comprehension above never reaches it -- guard it by hand, exactly
            # as --expect-source-forms-file is guarded. It reviews no fragment,
            # so honoring either flag would vouch for bytes nothing reviewed.
            parser.error(
                f"validate-only (no mode flag) does not accept {flag_name} -- "
                "it reviews no single fragment. Pass --check-batch."
            )
    # #505 -- every mode that refuses --citations-reviewed, with its own
    # reason. Same table-driven shape as --approve-to above: the attestation
    # gates the one irreversible act (freezing an unreviewed `established`
    # citation into a frozen canon row), so a mode that writes no row must
    # refuse it loudly rather than accept a precondition it cannot have.
    citations_reviewed_refusers = [
        spec for spec in selected_modes if spec.citations_reviewed_refusal is not None
    ]
    if args.citations_reviewed:
        if citations_reviewed_refusers:
            parser.error(
                "; ".join(
                    f"{spec.flag} does not accept --citations-reviewed "
                    f"({spec.citations_reviewed_refusal})"
                    for spec in citations_reviewed_refusers
                )
            )
        elif not selected_modes:
            # VALIDATE-ONLY has no MODE_SPECS row, so the comprehension above
            # never reaches it -- guarded by hand exactly like its two siblings.
            parser.error(
                "validate-only (no mode flag) does not accept "
                "--citations-reviewed -- it writes no canon row, so it can "
                "freeze no citation. Pass --merge-batches or --batch."
            )
    # #734 -- the attestation and its evidence travel together, in BOTH
    # directions, and each direction refuses a different mistake.
    #
    # RECORDS WITHOUT THE ATTESTATION is a caller that verified something it
    # then did not claim: --approval-records decides nothing on its own, so
    # accepting it alone would run a check whose result changes no outcome --
    # the shape a reader mistakes for a guarantee.
    #
    # THE ATTESTATION WITHOUT RECORDS is the hole this issue closes, and it is
    # the direction that matters. Until #734 the glossary pass decided whether
    # the record had been written by reading an AGENT'S SENTENCE saying so, and
    # merged under --citations-reviewed on the strength of it. Making the flag
    # refuse without records moves that decision onto the filesystem. It is a
    # deliberate BREAKING change to this CLI: a caller passing
    # --citations-reviewed and nothing else now halts, loudly, naming the flag
    # it must add -- which is the correct direction for an operator who was
    # about to freeze citations into canon on the strength of a claim.
    #
    # Refused for the same modes and by the same table as --citations-reviewed
    # itself: a mode that cannot make the attestation cannot want its evidence,
    # so a second column would be the same list maintained twice.
    if args.approval_records is not None:
        if citations_reviewed_refusers:
            parser.error(
                "; ".join(
                    f"{spec.flag} does not accept --approval-records "
                    f"({spec.citations_reviewed_refusal})"
                    for spec in citations_reviewed_refusers
                )
            )
        elif not selected_modes:
            parser.error(
                "validate-only (no mode flag) does not accept "
                "--approval-records -- it merges nothing, so there is no "
                "attestation for a record to support. Pass --merge-batches "
                "or --batch."
            )
        elif not args.citations_reviewed:
            parser.error(
                "--approval-records requires --citations-reviewed -- a verdict "
                "record is the evidence an attestation rests on, and on its own "
                "it decides nothing"
            )
    elif args.citations_reviewed and not citations_reviewed_refusers and selected_modes:
        parser.error(
            "--citations-reviewed requires --approval-records: one "
            "glossary-approval/1 record per merged fragment, in the same "
            "order, each naming that fragment's sha256. The attestation says "
            "an independent citation review approved these exact bytes, and "
            "since #734 this script refuses to take that on trust -- the "
            "glossary pass writes the records with --record-approval-to"
        )
    # #820 -- every mode that refuses --glossary-merge-marker, with its own
    # reason. Same table-driven shape as --citations-reviewed above: a mode
    # other than --merge-batches never performs the merge the W5 gate needs
    # recorded, so honoring the flag there would silently accept a marker
    # request nothing on this call path can satisfy.
    glossary_merge_marker_refusers = [
        spec for spec in selected_modes if spec.glossary_merge_marker_refusal is not None
    ]
    if args.glossary_merge_marker is not None:
        if glossary_merge_marker_refusers:
            parser.error(
                "; ".join(
                    f"{spec.flag} does not accept --glossary-merge-marker "
                    f"({spec.glossary_merge_marker_refusal})"
                    for spec in glossary_merge_marker_refusers
                )
            )
        elif not selected_modes:
            # VALIDATE-ONLY has no MODE_SPECS row, so the comprehension above
            # never reaches it -- guarded by hand exactly like its siblings.
            parser.error(
                "validate-only (no mode flag) does not accept "
                "--glossary-merge-marker -- it merges nothing, so there is "
                "no merge for a marker to record. Pass --merge-batches."
            )
    # #412 -- the trusted-sibling precondition. A mode that STAMPS
    # generation_hashes shells out to a sibling cache_key.py; left to
    # self-anchor, that sibling comes out of ${durable_root}/scripts/, which
    # the codex process this very stamp gates holds --write over. The flag
    # used to be optional, with the self-anchored path as the silent
    # default, so a call site that simply never learned about --plugin-root
    # stamped through whatever cache_key.py happened to be on disk. That is
    # the failure this refusal removes: it is deliberately NOT a check that
    # the flag was spelled correctly everywhere -- #582 recorded that
    # enumerating call sites does not converge -- but a refusal to proceed
    # without an ANSWER, so a missed call site halts naming both flags
    # instead of forging hashes that later gate canon reuse.
    #
    # A comprehension over MODE_SPECS, never a hand-typed set: a writing mode
    # added later inherits this guard from its own row.
    stamping_modes = [spec for spec in selected_modes if spec.stamps_generation_hashes]
    if args.plugin_root is not None and args.allow_durable_sibling:
        parser.error(
            "--plugin-root and --allow-durable-sibling are mutually exclusive "
            "-- naming a trusted plugin root and waiving the requirement to "
            "name one state two different intentions. Pass exactly one."
        )
    if stamping_modes and args.plugin_root is None and not args.allow_durable_sibling:
        parser.error(
            "; ".join(
                f"{spec.flag} stamps canon.json's generation_hashes and so "
                f"requires either --plugin-root PATH (the trusted plugin "
                f"install root to resolve the sibling cache_key.py from) or "
                f"an explicit --allow-durable-sibling. #412: "
                f"${{durable_root}}/scripts/ is writable by the codex process "
                f"this stamp gates, so a silently self-anchored sibling could "
                f"be a tampered cache_key.py forging the hashes that gate "
                f"canon reuse"
                for spec in stamping_modes
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
            result = run_init(canon_path, args.research_mode, registry, args.plugin_root)
        elif args.restamp_derivation:
            result = run_restamp_derivation(canon_path, args.research_mode, registry, args.plugin_root)
        elif args.correct is not None:
            result = run_correct(
                canon_path,
                args.correct,
                args.research_mode,
                registry,
                senses_path,
                allow_absent_senses,
            )
        elif args.check_batch is not None:
            result = run_check_batch(
                canon_path,
                args.check_batch,
                args.research_mode,
                args.expect_source_forms_file,
                registry,
                senses_path,
                allow_absent_senses,
                args.approve_to,
                args.record_approval_to,
            )
        elif args.merge_batches is not None:
            result = run_merge_batches(
                canon_path,
                args.merge_batches,
                args.research_mode,
                registry,
                senses_path,
                allow_absent_senses,
                args.plugin_root,
                args.citations_reviewed,
                args.approval_records,
                args.glossary_merge_marker,
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
                args.plugin_root,
                args.citations_reviewed,
                args.approval_records,
            )
        else:
            result = run_validate_only(canon_path, args.research_mode, registry)
    except CanonValidationError as e:
        payload = {"success": False, "error": str(e)}
        if e.offending is not None:
            payload["offending"] = e.offending
        print(dumps_line(payload))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(
            dumps_line({"success": False, "error": f"unexpected error: {e}"})
        )
        return 1

    print(dumps_line(result))
    if args.verify_merged:
        return 0 if result.get("verified") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
