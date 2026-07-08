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
LEDGER_D = DURABLE_ROOT / "runs" / "ledger.d"
LEDGER_JSON_PATH = DURABLE_ROOT / "runs" / "ledger.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"

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
