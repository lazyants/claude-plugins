#!/usr/bin/env python3
"""Merge per-segment ledger fragments into the single, materialized ledger.json.

See references/ledger-and-resumability.md, section
"`mergeLedgerPrompt` / `ledger_merge.py` -- completeness verification" for
the authoritative spec this script implements. Mandatory and blocking:
`mass-translate-wf.template.js` runs this as its own final step, and a batch
is not considered complete until it succeeds.

What it does, in order:
  1. Reads every fragment under `runs/ledger.d/*.json` (one file per segment,
     written exclusively by `ledger_update.py`'s atomic writer -- this script
     never itself writes a fragment).
  2. If `--expected-from-manifest` or `--expected-segs` is given, checks that
     every named segment has a matching fragment -- a SUBSET/completeness
     check, never exact key-set equality, since `ledger.json` legitimately
     accumulates fragments across every batch ever run. Any name with no
     fragment at all is reported in `missing_segments` and the merge FAILS.
     Without either flag, `ledger.json` is still materialized, but this
     check is skipped entirely (`missing_segments` is trivially empty).
  3. For every fragment whose on-disk `status` is `converged`, recomputes
     the current 15-field cache key by shelling out to `cache_key.py --seg
     <id>` (the one shared hashing implementation) and compares it
     field-by-field against the fragment's own stored `cache_key`. A
     mismatch flips that segment's status to `stale` *in the materialized
     ledger.json only* -- the on-disk fragment itself is never rewritten.
     `stale` is a status this script computes; `ledger_update.py` never
     writes it to a fragment (see `ledger-fragment.schema.json`'s narrower
     enum vs. `ledger.schema.json`'s wider one). 1.25.0 addition (#491) --
     the sorted, non-empty list of exactly which fields moved is ALSO
     written, as `stale_mismatched_fields`, onto that same materialized
     entry only, so a downstream reader (assemble.py's own machinery-only
     carve-out) can tell WHY a segment went stale without recomputing
     anything itself. Never written to the fragment, and never written at
     all for the OTHER path that flips a segment stale below (a stored
     `cache_key` that isn't even a dict) -- there is no diff to report
     there, and assemble.py's carve-out treats a missing value as
     "not machinery-only" by design (see its own fail-safe docstring).
  4. Validates the materialized `{"segments": {...}}` document against
     `ledger.schema.json` (which composes the SAME status-free
     `ledger-record-base.schema.json` fragments do, just with a wider
     `status` enum -- never against `ledger-fragment.schema.json` itself).
  5. Atomically writes `runs/ledger.json` (tmp-write-then-`os.replace()`,
     the same durable pattern `ledger_update.py` uses for fragments).
  5.5. 1.2.0 addition -- if `--run-token` (a bare RUN_ID) is given together
     with `--expected-from-manifest`/`--expected-segs`, re-asserts, for EACH
     expected segment whose materialized status is still `converged`, that
     its on-disk draft's own `dispatch_token` equals the reconstructed
     `expected_draft_token(run_token, seg)` = `<run_token>:<seg>` EXACTLY,
     that `review.json`'s own `dispatch_token` equals that same value plus a
     `:r<roundLabel>` SUFFIX (a prefix match), and that the draft's current
     content sha1 (via `draft_content_sha1()`, dispatch_token-excluded)
     still matches the fragment's own recorded `reviewed_draft_sha1`. Any
     mismatch fails the WHOLE merge (nothing is written) -- closing a race
     where a stale/straggler draft+review pair is restored on disk
     *between* the per-segment convergence write (`ledger_update.py`, which
     already checked this once at write time) and this batch-final merge, so no
     false-green `batchComplete` can materialize from it.
  6. Prints one JSON line to stdout matching
     `ledger-merge-confirmation.schema.json`'s `oneOf` (SUCCESS/FAILURE are
     genuinely different shapes -- a failure never claims a `ledger_path`/
     `n_segments`/`stale_segments` that was never computed), and validates
     that very payload against its own schema before printing it, so a bug
     in this script can never emit a confirmation that lies about its own
     shape.

Usage:
    python3 ledger_merge.py
    python3 ledger_merge.py --expected-from-manifest /path/to/manifest.json
    python3 ledger_merge.py --expected-segs seg05,seg06,seg07
    python3 ledger_merge.py --expected-segs seg05,seg06 --skip-stale-check
    python3 ledger_merge.py --durable-root /path/to/durable_root --plugin-root /path/to/plugin/install

LT-409 (post-review correction): --durable-root PATH and --plugin-root PATH
are TWO INDEPENDENT overrides. --durable-root governs DATA (schemas/,
segments/, runs/). --plugin-root governs where the sibling cache_key.py
subprocess is found, as {PATH}/assets/scripts/cache_key.py -- deliberately
NEVER derived from --durable-root, because ${durable_root}/scripts/ is
writable by the codex process this stale-check gates, so resolving the
checker from inside the thing it checks would let a tampered copy pass
itself. Only --durable-root is forwarded to the cache_key.py subprocess as
its own same-named flag: cache_key.py is a LEAF with no siblings of its own
to resolve, and does not accept --plugin-root at all, so passing it would
simply make the invocation fail. Whenever either flag is given, the value
forwarded is always the ALREADY-RESOLVED absolute durable root (post-review
correction -- forwarding the raw --durable-root string used to double-
resolve it against the subprocess's own cwd whenever it was relative; see
_compute_stale_segments()'s own docstring for the exact scenario). Omitting
BOTH reproduces today's self-anchored behavior byte-for-byte.

#608: --plugin-root has TWO outcomes. NOT GIVEN is the self-anchored sibling
lookup above, unchanged. GIVEN but not resolving to a directory containing
assets/scripts/ is REFUSED by validate_plugin_root() before any fragment is
read -- see its docstring for what that used to do instead. The per-segment
stale-check skip is unchanged and stays non-fatal.

Exit code 0 on success, 1 on failure. Either way, exactly one JSON line is
printed to stdout -- callers (the `mergeLedgerPrompt` agent prompt, tests)
should read stdout, not rely on the exit code alone. #608's refusal keeps that
shape rather than adopting codex_job.py's stderr-plus-exit-2, because both
in-repo callers json.loads() this stdout and branch on "success": a
bare-stderr refusal would reach them as unparseable output and surface a
JSON-decode complaint instead of the operator's actual mistake.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import jsonschema
    from jsonschema import validators as jsonschema_validators
    from referencing import Registry, Resource
except ImportError as e:
    sys.stderr.write(
        "ledger_merge.py requires the 'jsonschema' package (>=4.26.0), which "
        "pulls in 'referencing' for $ref resolution across the schema "
        "files. Install with:\n\n"
        "    pip install 'jsonschema>=4.26.0'\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

# Self-anchored by default: this script always lives at
# ${durable_root}/scripts/ledger_merge.py, so parents[1] is the durable
# root. Never assumes cwd. LT-409 (post-review correction): --durable-root
# PATH and --plugin-root PATH are TWO INDEPENDENT overrides (see
# resolve_dirs() below) -- --durable-root governs DATA (schemas/segments/
# runs), --plugin-root governs where the cache_key.py SIBLING SCRIPT is
# found. They are deliberately never derived from each other:
# ${durable_root}/scripts/ is a Step-0a copy the codex process can write to
# (codex_job.py runs it with --write over the whole durable root), so
# resolving the checker cache_key.py FROM there would let a tampered copy
# validate itself. Omitting BOTH flags reproduces today's self-anchored
# behavior byte-for-byte -- see references/ledger-and-resumability.md's
# "Script self-anchoring" invariant.
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
SEGMENTS_DIR = DURABLE_ROOT / "segments"
LEDGER_D = DURABLE_ROOT / "runs" / "ledger.d"
LEDGER_JSON_PATH = DURABLE_ROOT / "runs" / "ledger.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"


def validate_plugin_root(plugin_root_str):
    """#608: a GIVEN --plugin-root that cannot resolve is a whole-run
    precondition failure, not a per-segment one.

    resolve_dirs() below performs no I/O and validates neither branch, so an
    unresolvable --plugin-root used to survive all the way to
    _compute_stale_segments()'s `if not cache_key_script.is_file():` branch --
    which is deliberately NON-FATAL, warns on stderr and `continue`s. That
    policy is right for what it was written for (a segment whose segpack was
    deleted must not sink an entire merge) and is left exactly as it is. What
    it cannot distinguish is the case where the operator mistyped the flag and
    EVERY segment is therefore unchecked: the merge then prints its ordinary
    success JSON and materializes runs/ledger.json with every status left as
    written, which is a false green and a silent one. The flag exists
    precisely so a durable-root copy of the checker -- writable by the codex
    process the stale-check gates -- is not the one that runs; degrading to
    "no checker ran at all" while reporting success is that same failure one
    step over.

    Raises LedgerMergeError (main() renders it as this script's ordinary
    one-JSON-line failure, exit 1). `plugin_root_str is None` -- the
    documented, deliberate self-anchored path -- returns without checking
    anything, so omitting the flag is untouched.

    The empty/whitespace-only leg is NOT a separate feature: Path("").resolve()
    is the CURRENT WORKING DIRECTORY, so `--plugin-root ""` (typically an
    unsubstituted {{PLUGIN_ROOT}}) run from a cwd that happens to contain
    assets/scripts/ passes a bare is_dir() check and silently runs THAT tree's
    checker. codex_job.py rejects it explicitly at its own equivalent site for
    the same reason; the message body here is deliberately the same one, so
    two scripts sharing this flag say the same thing about the same mistake.

    Path.is_dir() rather than the os.stat()/FileNotFoundError triad used by
    the data-loss guards elsewhere in this codebase: those sites care WHICH
    error occurred because a swallowed OSError would read as "converged". Here
    every non-directory answer -- ENOENT, ENOTDIR, ELOOP, EACCES -- reaches
    the identical refusal, so is_dir()'s swallowing cannot change the verdict.
    """
    if plugin_root_str is None:
        return
    if not plugin_root_str.strip():
        raise LedgerMergeError(
            "--plugin-root was given but is empty/whitespace-only -- this "
            "usually means a {{PLUGIN_ROOT}} template substitution did not "
            "happen. Omit the flag entirely for today's self-anchored "
            "behavior, or pass a real path."
        )
    plugin_scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
    if not plugin_scripts_dir.is_dir():
        raise LedgerMergeError(
            f"--plugin-root {plugin_root_str} does not resolve to a directory "
            f"containing assets/scripts/ (resolved: {plugin_scripts_dir})"
        )


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409: `durable_root_str` governs DATA (schemas/segments/runs) --
    rebuilt from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    cache_key.py SIBLING SCRIPT this script shells out to is resolved from
    -- deliberately NEVER derived from `durable_root_str` (see module
    docstring / the comment above these constants for why). When given, it
    resolves as `{plugin_root}/assets/scripts/cache_key.py` -- the SAME
    layout SKILL.md documents for the plugin-anchored scripts, NOT
    durable_root's own flattened `scripts/cache_key.py` copy layout.
    `plugin_root_str=None` reproduces today's self-anchored sibling lookup
    unchanged.

    Both None -> today's exact self-anchored values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        schemas_dir = SCHEMAS_DIR
        segments_dir = SEGMENTS_DIR
        ledger_d = LEDGER_D
        ledger_json_path = LEDGER_JSON_PATH
    else:
        durable_root = Path(durable_root_str).resolve()
        runs_dir = durable_root / "runs"
        schemas_dir = durable_root / "schemas"
        segments_dir = durable_root / "segments"
        ledger_d = runs_dir / "ledger.d"
        ledger_json_path = runs_dir / "ledger.json"

    if plugin_root_str is None:
        cache_key_script = CACHE_KEY_SCRIPT
    else:
        cache_key_script = Path(plugin_root_str).resolve() / "assets" / "scripts" / "cache_key.py"

    return {
        "durable_root": durable_root,
        "schemas_dir": schemas_dir,
        "segments_dir": segments_dir,
        "ledger_d": ledger_d,
        "ledger_json_path": ledger_json_path,
        "cache_key_script": cache_key_script,
    }


def draft_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.draft.json"


def review_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.review.json"

# The authoritative 15-field cache-key list (references/ledger-and-
# resumability.md, "Composite cache key -- exact 15-field structure"). Kept
# as a literal here (mirroring ledger-record-base.schema.json's own
# `cache_key.required` list) so a stale-check comparison never silently
# ignores a field neither side happens to have.
CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]


class LedgerMergeError(Exception):
    """Raised for any failure that should surface as a FAILURE confirmation.

    `missing_segments`, when not None, is folded into the FAILURE payload
    verbatim -- naming which expected segments have no fragment at all.
    """

    def __init__(self, message, missing_segments=None):
        super().__init__(message)
        self.missing_segments = missing_segments


def _load_schema_document(schema_path: Path) -> dict:
    if not schema_path.is_file():
        raise LedgerMergeError(f"schema file not found: {schema_path}")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LedgerMergeError(
            f"invalid JSON in schema {schema_path.name}: {e}"
        ) from e


def _build_schema_registry(schemas_dir: Path = SCHEMAS_DIR) -> "Registry":
    """Registers every *.schema.json file under `schemas_dir` by its own
    `$id` (a bare filename, per this project's convention -- e.g.
    "ledger-record-base.schema.json"), so `ledger.schema.json`'s `$ref` to
    that filename resolves regardless of load order.
    """
    if not schemas_dir.is_dir():
        raise LedgerMergeError(f"schemas directory not found: {schemas_dir}")
    resources = []
    for schema_file in sorted(schemas_dir.glob("*.schema.json")):
        contents = _load_schema_document(schema_file)
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    if not resources:
        raise LedgerMergeError(f"no *.schema.json files found under {schemas_dir}")
    return Registry().with_resources(resources)


def _validator_for(schema_filename: str, registry: "Registry", schemas_dir: Path = SCHEMAS_DIR):
    schema = _load_schema_document(schemas_dir / schema_filename)
    validator_cls = jsonschema_validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema, registry=registry)


def _read_fragments(ledger_d: Path = LEDGER_D) -> dict:
    """Reads every runs/ledger.d/*.json fragment. The filename stem (minus
    the .json suffix) IS the segment id, by construction of
    ledger_update.py's own write path (runs/ledger.d/{seg}.json). Returns
    {seg: record_dict}.

    An ABSENT ledger.d -- ENOENT, or ENOTDIR because the name is a plain
    file -- means "no fragments written yet". That is not an error and it
    merges to an empty ledger. #463: that benign reading is correct for
    those two errnos and WRONG for every other one, and the two constructs
    this function used to ask with could not tell them apart. `is_dir()`
    answers False on any suppressed OSError, and `glob()` returns an empty
    iterator for a directory it cannot read; neither raises. Measured on
    the interpreter this ships against, a ledger.d at mode 0o000 gives
    `is_dir() -> True` and `glob("*.json") -> []`, so the swallow that
    actually reaches production here is the glob one -- a populated
    fragment directory reporting itself empty, which merge() then
    publishes over a populated ledger.json.

    One `iterdir()` inside one try answers all of it: ENOENT/ENOTDIR are
    the definitive not-there, and every other OSError is a could-not-look
    that REFUSES rather than reporting emptiness -- the same split, and
    deliberately the same shape, as select_segments.py:2062-2074. The
    .json filter replaces the glob pattern exactly, name for name (see the
    endswith() comment in the loop): ledger_update.py stages
    `{seg}.json.tmp.{pid}` and publishes `{seg}.json`, so a staged temp file
    is excluded by both.
    """
    try:
        entries = sorted(ledger_d.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        # DEFINITIVELY nothing written yet: the directory is not there, or
        # the name is not a directory at all. Both are the documented
        # first-run state; neither is a suppressed error.
        return {}
    except OSError as exc:
        raise LedgerMergeError(
            f"the ledger fragment directory {ledger_d} exists but could not "
            f"be listed ({exc}) -- refusing to report it as empty, because "
            f"could-not-look is not nothing-is-there and an empty read here "
            f"is what merge() would publish over runs/ledger.json"
        ) from exc
    fragments = {}
    for frag_path in entries:
        # endswith(), NOT Path.suffix: the two disagree on a name that is
        # ALL suffix (".json", "..json"), which glob("*.json") did select and
        # `suffix` does not. No writer in this plugin can produce such a name
        # -- validate_seg() allows only (FRONTBACK:)?[A-Za-z0-9_]+ -- so this
        # is not a reachability fix. It is here so that swapping the
        # enumeration construct changes ONLY the errno behaviour this issue
        # is about, and leaves which names count as a fragment exactly as it
        # found them.
        if not frag_path.name.endswith(".json"):
            continue
        seg = frag_path.stem
        try:
            record = json.loads(frag_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise LedgerMergeError(
                f"invalid JSON in fragment {frag_path.name}: {e}"
            ) from e
        if not isinstance(record, dict):
            raise LedgerMergeError(
                f"fragment {frag_path.name} does not contain a JSON object"
            )
        fragments[seg] = record
    return fragments


def _expected_segments_from_manifest(manifest_path_str: str) -> list:
    manifest_path = Path(manifest_path_str)
    if not manifest_path.is_file():
        raise LedgerMergeError(f"manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LedgerMergeError(
            f"invalid JSON in manifest {manifest_path}: {e}"
        ) from e
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        raise LedgerMergeError(f"manifest {manifest_path} has no 'segments' array")
    expected = []
    for item in segments:
        # manifest.schema.json's segments[] entries are objects with their
        # own `seg` field (the segment id) -- never bare strings in the real
        # schema, but a bare string is accepted too for robustness/testing.
        if isinstance(item, dict) and isinstance(item.get("seg"), str):
            expected.append(item["seg"])
        elif isinstance(item, str):
            expected.append(item)
        else:
            raise LedgerMergeError(
                f"manifest {manifest_path}: malformed segments[] entry: {item!r}"
            )
    return expected


def _expected_segments(args) -> "list | None":
    """Returns the expected-segment list, or None if neither flag was given
    (meaning: materialize but skip the completeness check entirely).
    """
    if args.expected_from_manifest:
        return _expected_segments_from_manifest(args.expected_from_manifest)
    if args.expected_segs is not None:
        return [s.strip() for s in args.expected_segs.split(",") if s.strip()]
    return None


def _compute_stale_segments(
    fragments: dict,
    skip_stale_check: bool,
    cache_key_script: Path = CACHE_KEY_SCRIPT,
    durable_root: Path = DURABLE_ROOT,
    durable_root_str=None,
    plugin_root_str=None,
) -> "tuple[set, dict]":
    """For every fragment whose on-disk status is 'converged', recomputes
    the current cache key via `cache_key.py --seg <id>` and compares it
    field-by-field against the fragment's own stored `cache_key`. Returns
    `(stale, mismatched_fields)`: `stale` is the set of segment ids to mark
    'stale' in the MATERIALIZED output only; `mismatched_fields` is
    {seg: sorted [field, ...]} for exactly the segments where that
    field-by-field diff was actually computed (i.e. NOT the "stored
    cache_key isn't even a dict" branch below, which marks a segment stale
    for a different reason and has no diff to report) -- #491's
    `stale_mismatched_fields`, written onto the materialized entry only by
    merge() below so assemble.py's machinery-only carve-out can read WHY a
    segment went stale without recomputing anything itself.

    A per-segment failure to recompute (cache_key.py missing, non-zero
    exit, unparseable stdout) is treated as non-fatal for the overall
    merge -- logged to stderr, that segment's status is left as-is. A merge
    is still useful diagnostically even when one segment's cache key can't
    currently be recomputed (e.g. its segpack was deleted); refusing to
    materialize the whole ledger over one segment would defeat the point of
    per-segment fragmenting in the first place.

    LT-409: `cache_key_script` is the resolved sibling path to shell out
    against -- self-anchored by default, or resolve_dirs()'s own
    --plugin-root-aware `{plugin_root}/assets/scripts/cache_key.py` (never
    derived from durable_root; see resolve_dirs()'s own docstring for why).
    `durable_root` is cache_key.py's DATA root (cwd for the subprocess) --
    ALREADY resolve()'d by resolve_dirs(), so it is always an absolute path.
    `durable_root_str`/`plugin_root_str` are THIS script's own CLI values
    (cache_key.py has no --plugin-root, being a leaf with no siblings of its
    own), used only to DECIDE whether a --durable-root should be forwarded
    at all -- never their own string VALUE.

    Post-review correction (third instance of this exact shape --
    resume_setup.py and select_segments.py each had the identical bug in
    their own forward to cache_key.py): whenever either flag is set, the
    subprocess's own --durable-root is now the ALREADY-RESOLVED
    `durable_root`, never the raw `durable_root_str`. Forwarding the raw
    string used to double-resolve it whenever it was RELATIVE: the
    subprocess below runs with cwd=str(durable_root) (an absolute path), so
    a relative --durable-root VALUE would be resolved a SECOND time inside
    cache_key.py, against that already-resolved cwd -- e.g. --durable-root
    projects/book run from /repo resolves HERE to /repo/projects/book, then
    cache_key.py resolves "projects/book" again against that cwd, landing
    on /repo/projects/book/projects/book. Silent either way: this function's
    own per-segment failure handling treats a non-zero cache_key.py exit as
    a skip (segment left as-is, warning to stderr), so a wrong-tree read
    that happens to produce SOME JSON object looks identical to a genuine
    one, and one that crashes just quietly skips the stale-check for that
    segment rather than failing the whole merge. Forwarding the resolved
    path is a no-op for a caller that already passes an absolute path
    (every existing caller does), and does not change cache_key.py's own
    contract -- it already accepts an absolute --durable-root.
    """
    stale = set()
    mismatched_fields: dict = {}
    if skip_stale_check:
        return stale, mismatched_fields

    for seg, record in sorted(fragments.items()):
        if record.get("status") != "converged":
            continue

        stored_key = record.get("cache_key")
        if not isinstance(stored_key, dict):
            # A schema-valid converged fragment always has this; if it's
            # missing anyway, surface it as stale rather than silently
            # trusting an anomalous record. LOAD-BEARING for #491's own
            # `stale_mismatched_fields` too (see tests/stale_carveout.
            # test.py's absent/non-dict-cache_key tests): this `continue`
            # is what keeps this branch from ever reaching the
            # `mismatched_fields[seg] = moved` write below -- a naive
            # stored-vs-current diff against an ABSENT stored key would
            # read as "all 15 CACHE_KEY_FIELDS differ", fabricating a
            # content-affecting-looking list for a record that was never
            # actually compared at all.
            stale.add(seg)
            continue

        if not cache_key_script.is_file():
            sys.stderr.write(
                f"ledger_merge.py: warning: {cache_key_script} not found -- "
                f"skipping stale-check for segment '{seg}'\n"
            )
            continue

        cmd = [sys.executable, str(cache_key_script), "--seg", seg]
        if durable_root_str is not None or plugin_root_str is not None:
            cmd += ["--durable-root", str(durable_root)]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(durable_root),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            sys.stderr.write(
                f"ledger_merge.py: warning: could not run cache_key.py for "
                f"segment '{seg}': {e}\n"
            )
            continue

        if proc.returncode != 0:
            sys.stderr.write(
                f"ledger_merge.py: warning: cache_key.py --seg {seg} exited "
                f"{proc.returncode}: {proc.stderr.strip()}\n"
            )
            continue

        try:
            current_key = json.loads(proc.stdout)
        except json.JSONDecodeError:
            sys.stderr.write(
                f"ledger_merge.py: warning: cache_key.py --seg {seg} did not "
                f"print valid JSON -- skipping stale-check for this segment\n"
            )
            continue

        if not isinstance(current_key, dict):
            sys.stderr.write(
                f"ledger_merge.py: warning: cache_key.py --seg {seg} printed "
                f"a non-object JSON value -- skipping stale-check for this "
                f"segment\n"
            )
            continue

        moved = sorted(
            field
            for field in CACHE_KEY_FIELDS
            if stored_key.get(field) != current_key.get(field)
        )
        if moved:
            stale.add(seg)
            mismatched_fields[seg] = moved

    return stale, mismatched_fields


def expected_draft_token(run_token: str, seg: str) -> str:
    """Constructs the exact draft-form dispatch_token expected for THIS
    segment under the given bare run_token: '<run_token>:<seg>' -- draft
    dispatch_token's own documented format. Reconstructing the FULL
    expected token (not just extracting/comparing a RUN_ID prefix) also
    catches a same-run-but-wrong-segment token. Must match, byte for byte,
    ledger_update.py's own copy of this function.
    """
    return f"{run_token}:{seg}"


def review_token_matches(review_token, draft_token: str) -> bool:
    """review.json's own dispatch_token = '<draft_token>:r<roundLabel>' --
    a ':r<roundLabel>' SUFFIX the draft's own token does not carry.
    Matched by PREFIX here, not exact string equality, since the round
    label varies per review round. Must match, byte for byte,
    ledger_update.py's own copy of this function.
    """
    return isinstance(review_token, str) and review_token.startswith(f"{draft_token}:r")


def draft_content_sha1(path: Path) -> str:
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- must match, byte for byte, draft_sha1.py's own
    (and ledger_update.py's own byte-identical duplicate of)
    draft_content_sha1(), per this project's "no shared lib between
    self-contained scripts" convention. See draft_sha1.py's own module
    docstring for the full rationale.
    """
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError(f"draft at {path} must be a JSON object, got {type(doc).__name__}")
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def _read_json_file(path: Path, what: str):
    if not path.is_file():
        return None, f"{what} not found at {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{what} at {path} is not valid JSON: {exc}"


def _reassert_token_and_sha(
    seg: str, record: dict, run_token: str, segments_dir: Path = SEGMENTS_DIR
) -> "str | None":
    """1.2.0 addition: re-asserts, for one EXPECTED CONVERGED segment, that
    the on-disk draft's own dispatch_token equals
    expected_draft_token(run_token, seg) = '<run_token>:<seg>' EXACTLY, that
    review.json's own dispatch_token equals that same value plus a
    ':r<roundLabel>' SUFFIX (review_token_matches(), a prefix match), and
    that the draft's current content-sha1 (dispatch_token-excluded,
    matching draft_sha1.py's own algorithm) still equals the ledger
    fragment's own recorded reviewed_draft_sha1.

    Closes the race where a stale/straggler draft+review pair (consistent
    with each other, but from an OLD run) is restored on disk sometime
    *between* the per-segment convergence write (ledger_update.py, which
    already re-checked this at write time) and this batch-final merge --
    the whole point of re-checking it again here, right before reporting
    batchComplete.

    Returns a human-readable error string naming the specific mismatch, or
    None if all checks pass.
    """
    dpath = draft_path(seg, segments_dir)
    rpath = review_path(seg, segments_dir)

    draft_obj, err = _read_json_file(dpath, f"draft for segment '{seg}'")
    if err is not None:
        return err
    review_obj, err = _read_json_file(rpath, f"review artifact for segment '{seg}'")
    if err is not None:
        return err

    expected_token = expected_draft_token(run_token, seg)

    draft_token = draft_obj.get("dispatch_token") if isinstance(draft_obj, dict) else None
    if draft_token != expected_token:
        return (
            f"segment '{seg}': draft dispatch_token {draft_token!r} != "
            f"expected {expected_token!r} (run_token={run_token!r})"
        )

    review_token = review_obj.get("dispatch_token") if isinstance(review_obj, dict) else None
    if not review_token_matches(review_token, expected_token):
        return (
            f"segment '{seg}': review dispatch_token {review_token!r} does "
            f"not match expected prefix {expected_token + ':r'!r} "
            f"(run_token={run_token!r})"
        )

    recorded_sha1 = record.get("reviewed_draft_sha1")
    try:
        current_sha1 = draft_content_sha1(dpath)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return f"segment '{seg}': could not compute current draft content sha1: {exc}"
    if current_sha1 != recorded_sha1:
        return (
            f"segment '{seg}': draft content sha1 {current_sha1!r} != "
            f"ledger-recorded reviewed_draft_sha1 {recorded_sha1!r} -- draft "
            f"changed since convergence was recorded"
        )
    return None


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _refuse_to_empty_a_populated_ledger(ledger_json_path: Path, materialized_segments: dict) -> None:
    """#463. A merge that would take a populated runs/ledger.json to ZERO
    segments refuses instead of publishing.

    This constrains the OUTCOME rather than the failure mode, which is why
    it is here in addition to _read_fragments()' errno split and not
    instead of it. The errno split is enumerative -- it closes EACCES,
    ELOOP, EIO and whatever else the listing can raise. This one closes
    every cause, including causes nobody has enumerated, because no
    legitimate merge produces this transition at all: nothing in this
    plugin ever deletes a ledger fragment (fragments are created or
    overwritten by ledger_update.py and only ever read here), so a
    many-fragment ledger going to zero can only come from outside the
    plugin -- a hand-cleared directory, a partial restore, or a swallowed
    read. The first of those is a deliberate operator act and gets the
    deliberate operator escape hatch: delete runs/ledger.json. There is no
    flag, because a flag would let the accident pass too.

    The check is CONDITIONAL ON `materialized_segments` BEING EMPTY, and
    that ordering is the whole reason it cannot over-catch: a merge that
    has real fragments to publish never reads the outgoing ledger at all,
    so a corrupt or unreadable ledger.json can never block one.

    Only ENOENT passes: a ledger.json that is not there is a first run and
    has nothing to lose. Every other outcome of the read -- the file exists
    but raises, or does not parse, or does not carry a `segments` object --
    is a REFUSAL, because could-not-look is not nobody-is-there. Exempting
    this read while splitting the fragment read would leave the identical
    hole one file over: a populated-but-unreadable ledger.json overwritten
    with {} by a green run.
    """
    if materialized_segments:
        return

    def _unknown_state(reason: str) -> LedgerMergeError:
        """BUILDS (does not raise) the refusal for the three could-not-
        establish branches below. They differ only in WHY the outgoing ledger
        could not be shown to be empty, and three copies of a sentence this
        long is three places to keep in sync. Returning the exception rather
        than raising it inside the helper keeps every `raise` visible at the
        branch it belongs to, so the control flow still reads straight down.
        """
        return LedgerMergeError(
            f"this merge produced ZERO segments and the existing "
            f"{ledger_json_path} {reason}, so whether it holds segments that "
            f"are about to be erased cannot be established -- refusing rather "
            f"than assume it is empty. If the ledger really is to be reset, "
            f"delete {ledger_json_path} first"
        )

    try:
        raw = ledger_json_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # DEFINITIVELY a first materialization: there is no prior ledger,
        # so publishing an empty one loses nothing.
        return
    except OSError as exc:
        raise _unknown_state(f"could not be read ({exc})") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _unknown_state(f"does not parse as JSON ({exc})") from exc
    segments = doc.get("segments") if isinstance(doc, dict) else None
    if not isinstance(segments, dict):
        raise _unknown_state("carries no 'segments' object")
    if segments:
        raise LedgerMergeError(
            f"this merge produced ZERO segments while {ledger_json_path} "
            f"currently holds {len(segments)} -- refusing to publish, because "
            f"no legitimate merge empties a populated ledger: nothing in this "
            f"plugin deletes a ledger fragment, so a many-to-zero transition "
            f"means the fragment directory was cleared outside the plugin or "
            f"could not be read. Investigate the fragment directory first; if "
            f"the ledger really is to be reset, delete {ledger_json_path}"
        )


def merge(args, registry: "Registry", dirs: dict) -> dict:
    """Runs the full merge and returns the SUCCESS confirmation dict, or
    raises LedgerMergeError (caller turns that into the FAILURE dict).

    `dirs` (LT-409) is resolve_dirs()'s returned dict -- self-anchored
    defaults, or --durable-root's own paths.
    """
    fragments = _read_fragments(dirs["ledger_d"])

    expected = _expected_segments(args)
    if expected is not None:
        missing_segments = sorted(set(expected) - fragments.keys())
        if missing_segments:
            raise LedgerMergeError(
                f"{len(missing_segments)} expected segment(s) have no ledger "
                f"fragment: {', '.join(missing_segments)}",
                missing_segments=missing_segments,
            )

    stale_segments, stale_mismatched_fields = _compute_stale_segments(
        fragments,
        args.skip_stale_check,
        dirs["cache_key_script"],
        dirs["durable_root"],
        args.durable_root,
        args.plugin_root,
    )

    materialized_segments = {}
    for seg, record in fragments.items():
        entry = dict(record)
        # #491 rescue (post-review correction): the materialized
        # `stale_mismatched_fields` must ALWAYS be this merge's own freshly
        # computed diff, never anything a fragment supplied. `_read_fragments()`
        # applies NO fragment-schema validation at all, so a hand-written (or
        # attacker/operator-planted) fragment can carry this key with an
        # arbitrary value -- and because ledger.schema.json now DECLARES the
        # property (ledger-fragment.schema.json and ledger-record-base.
        # schema.json deliberately do not), `unevaluatedProperties: false` no
        # longer rejects it on the materialized entry the way it would reject
        # it on the fragment itself. Inheriting it here would let a planted
        # `stale_mismatched_fields` fabricate carve-out eligibility for a
        # segment assemble.py's machinery-only carve-out never actually
        # compared (see its own _stale_carveout_refusal_reason()). Drop any
        # inherited value unconditionally, for EVERY entry -- not just the
        # ones this merge itself flips stale below, and not just entries
        # whose fragment status was already "stale" (ledger_update.py never
        # writes that status, but nothing stops a hand-edited fragment from
        # claiming it) -- before the stale branch below gets a chance to set
        # the real one.
        entry.pop("stale_mismatched_fields", None)
        if seg in stale_segments:
            entry["status"] = "stale"
            # #491: record WHY, on the materialized entry only -- the
            # fragment in runs/ledger.d/ is never touched (see _read_fragments
            # / _atomic_write_json below: only ledger.json is (re)written).
            # Only set when the diff was actually computed (see
            # _compute_stale_segments' own docstring) and therefore
            # non-empty; assemble.py's carve-out treats a missing/empty
            # value as "not machinery-only" by design (fail-safe direction),
            # so omitting it here for the other stale branches is correct,
            # not an oversight.
            fields = stale_mismatched_fields.get(seg)
            if fields:
                entry["stale_mismatched_fields"] = fields
        materialized_segments[seg] = entry

    # 1.2.0 addition: for EACH expected segment whose materialized status is
    # still 'converged' (i.e. not just flipped 'stale' above), re-assert its
    # on-disk draft+review dispatch_token against the reconstructed
    # expected_draft_token(run_token, seg) AND that the draft's content
    # hasn't drifted since convergence was recorded --
    # closing a race where a stale/straggler pair is restored between the
    # per-segment convergence write and this batch-final merge. Only runs
    # when BOTH an expected-segment list AND --run-token were given;
    # "batch completeness" has no meaning without the former, and this check
    # is an independent addition on top of it, backward-compatible when the
    # latter is omitted.
    if expected is not None and args.run_token is not None:
        reassert_errors = []
        for seg in expected:
            entry = materialized_segments.get(seg)
            if entry is None or entry.get("status") != "converged":
                continue
            err = _reassert_token_and_sha(seg, entry, args.run_token, dirs["segments_dir"])
            if err is not None:
                reassert_errors.append(err)
        if reassert_errors:
            raise LedgerMergeError(
                f"batch-final re-verification failed for "
                f"{len(reassert_errors)} segment(s) -- refusing to report "
                f"batchComplete:\n  " + "\n  ".join(reassert_errors)
            )

    ledger_doc = {"segments": materialized_segments}

    ledger_validator = _validator_for("ledger.schema.json", registry, dirs["schemas_dir"])
    errors = sorted(
        ledger_validator.iter_errors(ledger_doc),
        key=lambda e: [str(p) for p in e.path],
    )
    if errors:
        detail = "; ".join(
            f"at '{'/'.join(str(p) for p in e.path) or '<root>'}': {e.message}"
            for e in errors
        )
        raise LedgerMergeError(
            f"materialized ledger.json failed schema validation: {detail}"
        )

    # #463: last thing before the replace, and after schema validation, so a
    # refusal here means nothing was written at all.
    _refuse_to_empty_a_populated_ledger(dirs["ledger_json_path"], materialized_segments)

    _atomic_write_json(dirs["ledger_json_path"], ledger_doc)

    return {
        "success": True,
        "ledger_path": str(dirs["ledger_json_path"]),
        "n_segments": len(materialized_segments),
        "missing_segments": [],
        "stale_segments": sorted(stale_segments),
    }


def _validate_confirmation(payload: dict, registry: "Registry", schemas_dir: Path = SCHEMAS_DIR) -> None:
    """Self-check: the confirmation payload this script is about to print
    must itself validate against ledger-merge-confirmation.schema.json's
    `oneOf`. If it doesn't, that's a bug in this script -- report it as a
    FAILURE rather than printing a confirmation that lies about its own
    shape (the same "don't trust an unverified success claim" principle
    `recordLedgerPrompt` applies to `ledger_update.py`'s own stdout).
    """
    validator = _validator_for("ledger-merge-confirmation.schema.json", registry, schemas_dir)
    errors = list(validator.iter_errors(payload))
    if errors:
        detail = "; ".join(e.message for e in errors)
        raise LedgerMergeError(
            f"internal error: ledger_merge.py's own confirmation payload "
            f"failed schema validation: {detail}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge per-segment runs/ledger.d/*.json fragments into the "
            "single materialized runs/ledger.json, validated against "
            "ledger.schema.json."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--expected-from-manifest",
        metavar="PATH",
        help=(
            "Path to manifest.json; reads the expected segment id list from "
            "its segments[] array (each item's 'seg' field). Enables the "
            "missing-fragment completeness check."
        ),
    )
    group.add_argument(
        "--expected-segs",
        metavar="SEG1,SEG2,...",
        help=(
            "Comma-separated explicit list of expected segment ids (for a "
            "partial-batch completeness check) -- the same list "
            "select_segments.py emitted as SEGS, never separately "
            "hand-typed. Enables the missing-fragment completeness check."
        ),
    )
    parser.add_argument(
        "--skip-stale-check",
        action="store_true",
        help=(
            "Skip the cache_key.py-based staleness recomputation entirely "
            "(diagnostic/testing use only -- production runs should always "
            "leave this on)."
        ),
    )
    parser.add_argument(
        "--run-token",
        metavar="RUN_ID",
        default=None,
        help=(
            "The current run's bare RUN_ID (mergeLedgerPrompt's own "
            "invocation: '--run-token <RUN_ID>', no payload file -- unlike "
            "ledger_update.py, which reads run_token from its --payload-file "
            "instead). When given together with --expected-from-manifest/"
            "--expected-segs, re-asserts for each expected CONVERGED "
            "segment that its on-disk draft's own dispatch_token equals the "
            "reconstructed '<run_token>:<seg>' exactly, that review's own "
            "dispatch_token equals that value plus a ':r<roundLabel>' "
            "suffix, and that the draft's current content sha1 still "
            "matches the ledger-recorded reviewed_draft_sha1, before "
            "reporting success -- closing a race where a stale/straggler "
            "pair is restored between the per-segment convergence write and "
            "this batch merge. Omit for the pre-1.2.0 behavior (no "
            "re-verification)."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's "
            "own self-anchored location -- replaces where schemas/, "
            "segments/, and runs/ are found (including the cache_key.py "
            "subprocess's own data), forwarded to it as its own "
            "--durable-root. Optional; omit for today's self-anchored "
            "behavior. Independent of --plugin-root below -- never affects "
            "where the SIBLING SCRIPT itself is found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling cache_key.py script "
            "this script shells out to, as {PATH}/assets/scripts/"
            "cache_key.py -- deliberately NEVER derived from "
            "--durable-root, because ${durable_root}/scripts/ is writable "
            "by the codex process this stale-check gates (codex_job.py "
            "grants --write over the whole durable root), so resolving the "
            "checker from inside the thing it checks would let a tampered "
            "copy pass itself. Optional; omit for today's self-anchored "
            "sibling lookup -- but #608: if it IS given and does not resolve "
            "to a directory containing assets/scripts/ (or is empty), the "
            "whole merge is REFUSED before any segment is processed, rather "
            "than silently skipping the stale-check for every segment and "
            "reporting success."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        # #608: FIRST, before resolve_dirs() and therefore before any fragment
        # is read, any schema registry is built, any cache_key.py subprocess is
        # spawned, or anything is written. The position is the requirement, not
        # an optimization; test_plugin_root_refusal_precedes_any_fragment_read
        # pins it and its docstring says why an absent ledger cannot.
        validate_plugin_root(args.plugin_root)
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        registry = _build_schema_registry(dirs["schemas_dir"])
        result = merge(args, registry, dirs)
        _validate_confirmation(result, registry, dirs["schemas_dir"])
    except LedgerMergeError as e:
        payload = {"success": False, "error": str(e)}
        if e.missing_segments is not None:
            payload["missing_segments"] = e.missing_segments
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps(
                {"success": False, "error": f"unexpected error: {e}"},
                ensure_ascii=False,
            )
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
