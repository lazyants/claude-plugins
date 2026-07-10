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
     enum vs. `ledger.schema.json`'s wider one).
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

Exit code 0 on success, 1 on failure. Either way, exactly one JSON line is
printed to stdout -- callers (the `mergeLedgerPrompt` agent prompt, tests)
should read stdout, not rely on the exit code alone.
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

# Self-anchored: this script always lives at
# ${durable_root}/scripts/ledger_merge.py, so parents[1] is the durable
# root. Never assumes cwd, never takes a --durable-root flag -- see
# references/ledger-and-resumability.md's "Script self-anchoring" invariant.
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
SEGMENTS_DIR = DURABLE_ROOT / "segments"
LEDGER_D = DURABLE_ROOT / "runs" / "ledger.d"
LEDGER_JSON_PATH = DURABLE_ROOT / "runs" / "ledger.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"


def draft_path(seg):
    return SEGMENTS_DIR / f"{seg}.draft.json"


def review_path(seg):
    return SEGMENTS_DIR / f"{seg}.review.json"

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


def _build_schema_registry() -> "Registry":
    """Registers every *.schema.json file under SCHEMAS_DIR by its own `$id`
    (a bare filename, per this project's convention -- e.g.
    "ledger-record-base.schema.json"), so `ledger.schema.json`'s `$ref` to
    that filename resolves regardless of load order.
    """
    if not SCHEMAS_DIR.is_dir():
        raise LedgerMergeError(f"schemas directory not found: {SCHEMAS_DIR}")
    resources = []
    for schema_file in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        contents = _load_schema_document(schema_file)
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    if not resources:
        raise LedgerMergeError(f"no *.schema.json files found under {SCHEMAS_DIR}")
    return Registry().with_resources(resources)


def _validator_for(schema_filename: str, registry: "Registry"):
    schema = _load_schema_document(SCHEMAS_DIR / schema_filename)
    validator_cls = jsonschema_validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema, registry=registry)


def _read_fragments() -> dict:
    """Reads every runs/ledger.d/*.json fragment. The filename stem (minus
    the .json suffix) IS the segment id, by construction of
    ledger_update.py's own write path (runs/ledger.d/{seg}.json). Returns
    {seg: record_dict}. A missing ledger.d directory means "no fragments
    written yet" -- not an error; merges to an empty ledger.
    """
    if not LEDGER_D.is_dir():
        return {}
    fragments = {}
    for frag_path in sorted(LEDGER_D.glob("*.json")):
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


def _compute_stale_segments(fragments: dict, skip_stale_check: bool) -> set:
    """For every fragment whose on-disk status is 'converged', recomputes
    the current cache key via `cache_key.py --seg <id>` and compares it
    field-by-field against the fragment's own stored `cache_key`. Returns
    the set of segment ids to mark 'stale' in the MATERIALIZED output only.

    A per-segment failure to recompute (cache_key.py missing, non-zero
    exit, unparseable stdout) is treated as non-fatal for the overall
    merge -- logged to stderr, that segment's status is left as-is. A merge
    is still useful diagnostically even when one segment's cache key can't
    currently be recomputed (e.g. its segpack was deleted); refusing to
    materialize the whole ledger over one segment would defeat the point of
    per-segment fragmenting in the first place.
    """
    stale = set()
    if skip_stale_check:
        return stale

    for seg, record in sorted(fragments.items()):
        if record.get("status") != "converged":
            continue

        stored_key = record.get("cache_key")
        if not isinstance(stored_key, dict):
            # A schema-valid converged fragment always has this; if it's
            # missing anyway, surface it as stale rather than silently
            # trusting an anomalous record.
            stale.add(seg)
            continue

        if not CACHE_KEY_SCRIPT.is_file():
            sys.stderr.write(
                f"ledger_merge.py: warning: {CACHE_KEY_SCRIPT} not found -- "
                f"skipping stale-check for segment '{seg}'\n"
            )
            continue

        try:
            proc = subprocess.run(
                [sys.executable, str(CACHE_KEY_SCRIPT), "--seg", seg],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(DURABLE_ROOT),
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

        if any(
            stored_key.get(field) != current_key.get(field)
            for field in CACHE_KEY_FIELDS
        ):
            stale.add(seg)

    return stale


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


def _reassert_token_and_sha(seg: str, record: dict, run_token: str) -> "str | None":
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
    dpath = draft_path(seg)
    rpath = review_path(seg)

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


def merge(args, registry: "Registry") -> dict:
    """Runs the full merge and returns the SUCCESS confirmation dict, or
    raises LedgerMergeError (caller turns that into the FAILURE dict).
    """
    fragments = _read_fragments()

    expected = _expected_segments(args)
    if expected is not None:
        missing_segments = sorted(set(expected) - fragments.keys())
        if missing_segments:
            raise LedgerMergeError(
                f"{len(missing_segments)} expected segment(s) have no ledger "
                f"fragment: {', '.join(missing_segments)}",
                missing_segments=missing_segments,
            )

    stale_segments = _compute_stale_segments(fragments, args.skip_stale_check)

    materialized_segments = {}
    for seg, record in fragments.items():
        entry = dict(record)
        if seg in stale_segments:
            entry["status"] = "stale"
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
            err = _reassert_token_and_sha(seg, entry, args.run_token)
            if err is not None:
                reassert_errors.append(err)
        if reassert_errors:
            raise LedgerMergeError(
                f"batch-final re-verification failed for "
                f"{len(reassert_errors)} segment(s) -- refusing to report "
                f"batchComplete:\n  " + "\n  ".join(reassert_errors)
            )

    ledger_doc = {"segments": materialized_segments}

    ledger_validator = _validator_for("ledger.schema.json", registry)
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

    _atomic_write_json(LEDGER_JSON_PATH, ledger_doc)

    return {
        "success": True,
        "ledger_path": str(LEDGER_JSON_PATH),
        "n_segments": len(materialized_segments),
        "missing_segments": [],
        "stale_segments": sorted(stale_segments),
    }


def _validate_confirmation(payload: dict, registry: "Registry") -> None:
    """Self-check: the confirmation payload this script is about to print
    must itself validate against ledger-merge-confirmation.schema.json's
    `oneOf`. If it doesn't, that's a bug in this script -- report it as a
    FAILURE rather than printing a confirmation that lies about its own
    shape (the same "don't trust an unverified success claim" principle
    `recordLedgerPrompt` applies to `ledger_update.py`'s own stdout).
    """
    validator = _validator_for("ledger-merge-confirmation.schema.json", registry)
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
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        registry = _build_schema_registry()
        result = merge(args, registry)
        _validate_confirmation(result, registry)
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
