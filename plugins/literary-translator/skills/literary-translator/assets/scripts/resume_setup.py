#!/usr/bin/env python3
"""resume_setup.py -- the deterministic PRE-WORKFLOW resume-integrity gate
and run-dir/manifest setup step.

NEW in 1.2.0. Invoked by the orchestrating Claude session (a plain bash
call, BEFORE the mass-translate or glossary-pass Workflow is ever
launched) -- never invoked from inside the Workflow itself. See
references/ledger-and-resumability.md's "Resume-integrity gate" section
and references/orchestration-and-batching.md's glossary pre-workflow setup
description for the full spec this script implements.

Two independent problems this script closes, both BEFORE any agent
dispatch happens:

1. RESUME-INTEGRITY: whether a run resumes at all is gated by an
   input+version digest, never by merely reusing a RUN_ID. Every input
   that can change what a cached agent result MEANS -- the raw args, the
   resolved profile-derived substitution values burned into the
   instantiated Workflow template (a `live`->`offline` research_mode flip
   changes agent policy without changing any single hashed byte
   otherwise), each segment's own composite cache_key (mass) or the
   pinned glossary rule + canon.json state (glossary), and every durable
   byte that can invalidate a cached result (plugin_bundle_hash,
   orchestration_bundle_hash, and a hash of schemas/ itself) -- is folded
   into one `input_digest`. MATCH against the prior run's own recorded
   digest -> resume (`effectiveRunId` = the prior RUN_ID, `resume: true`,
   every cached artifact is trustworthy). MISMATCH, or no prior digest at
   all -> a FRESH run: a brand-new RUN_ID, `resume: false`, reuse NOTHING.
   `input.digest`, once written for a RUN_ID, is NEVER overwritten with a
   different value -- a mismatch always produces a fresh RUN_ID instead.

2. GLOSSARY MANIFEST TRUST: for a glossary-pass run, this script is the
   SINGLE TRUSTED WRITER of `manifest_{index}.json` (one per batch, this
   batch's own candidate names) and the aggregate `manifest_all.json`
   (union of every batch) -- written atomically, straight from the
   orchestrating session's own `args.candidates[].name` lists, entirely
   independent of the codex fragments that get self-checked against them
   later. This is what lets `--check-batch --expect-source-forms-file`/
   `--verify-merged --expect-source-forms-file` (canon_validate.py) catch
   a codex batch that silently DROPPED a candidate name, rather than
   trusting the batch's own claimed coverage.

Any failure here ABORTS (nonzero exit) before any Workflow dispatch
happens -- never a partial/best-effort setup.

CLI:

    python3 resume_setup.py --payload-file PATH

The caller first writes a JSON payload object to a scratch file (no shell
interpolation of field values), then invokes this script with just that
path. Payload shape:

    {
      "kind": "mass" | "glossary",              # required
      "args": <any JSON value>,                  # the full ordered args, hashed verbatim
      "subst": {                                 # required; every key required
        "research_mode": "...", "verse_policy": "...",
        "source_lang": "...", "target_lang": "...",
        "max_fix_rounds": N, "batch_agent_cap": N,
        "effort": "low|medium|high|xhigh"        # #197; NOT "model" (see SUBST_FIELDS)
      },
      "resume_from_run_id": "<candidate RUN_ID>" | null,   # optional
      "segs": ["seg01", "seg02", ...],           # required for kind="mass"
      "glossary_rule": <any JSON value>,         # required for kind="glossary"
      "batches": [                               # required for kind="glossary"
        {"index": 0, "names": ["Alice", "Bob"]},
        {"index": 1, "names": ["Carol"]}
      ]
    }

`subst` carries the RESOLVED profile-derived substitution values the
orchestrating session already computed to render the Workflow template --
this script trusts them as given rather than re-deriving them from
profile.yml itself, since the whole point is to hash exactly what got
burned into THIS instantiation. For kind="mass", each segment's 15-field
composite cache_key is instead computed HERE, fresh, by shelling out to
cache_key.py --seg <id> (the one shared hashing implementation) -- never
trusted from the caller, closing a staleness/TOCTOU gap a pre-computed
caller-supplied value would leave open. For kind="glossary", canon_hash is
likewise computed here (sha1 of the current canon.json's raw bytes, or the
literal string "no-canon" if canon.json does not exist yet).

On success, prints one JSON line:

    {"success": true, "effectiveRunId": "...", "resume": true|false,
     "run_dir": "...", "input_digest": "..."}

On failure: {"success": false, "error": "..."}. Exit code 0/1 either way
-- callers should read stdout, not rely on the exit code alone.

RUN_ID allowlist (references/ledger-and-resumability.md's `{{RUN_ID}}`
derivation contract): `^[A-Za-z0-9][A-Za-z0-9._-]*$`, the whole value is
never `.`/`..`, and it never contains a `..` substring (reject dir-escape/
collapse) -- a fresh RUN_ID generated here always takes the colon-free
timestamp form `YYYYMMDDTHHMMSSZ`, which trivially satisfies the
allowlist. A caller-supplied `resume_from_run_id` is validated against the
SAME allowlist before it is ever used to build a path.

Self-anchored: this script always lives at
${durable_root}/scripts/resume_setup.py, so parents[1] is the durable
root. Never assumes cwd, never takes a --durable-root flag.

Part of `plugin_bundle_hash` (see cache_key.py's own PLUGIN_BUNDLE_MEMBERS
comment) -- this script's own logic directly determines whether a run
resumes (and therefore whether ANY cached result is reused at all), which
is squarely correctness-gating territory, not diagnostic-only.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-anchoring
# ---------------------------------------------------------------------------
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
RUNS_DIR = DURABLE_ROOT / "runs"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"

SUBST_FIELDS = frozenset({
    "research_mode", "verse_policy", "source_lang", "target_lang",
    "max_fix_rounds", "batch_agent_cap", "effort",
})
# NOT "model": the mass digest already carries engine.model via each
# segment's own cache_key/agent_config_hash; the glossary pass has no model
# knob at all, so folding model into this SHARED digest would be a false
# dependency (a model pin would spuriously stale the glossary run too).

# ${durable_root}/runs/<RUN_ID>/ -- the same hardened allowlist the
# {{RUN_ID}} substitution token itself is validated against (references/
# ledger-and-resumability.md's "{{RUN_ID}} derivation" section): letters/
# digits/dot/underscore/hyphen only, no ':' (a raw ISO-8601 timestamp is
# intentionally rejected -- this script always generates the colon-free
# form itself).
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

RUN_ID_RETRY_LIMIT = 5


class ResumeSetupError(Exception):
    """Raised for any failure that should surface as a FAILURE result."""


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


def fresh_run_id():
    """Colon-free sortable timestamp id, e.g. '20260710T143022Z'."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Small I/O helpers
# ---------------------------------------------------------------------------


def _canonical_json_bytes(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_json(path: Path, doc) -> None:
    _atomic_write_text(path, json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _read_marker(path: Path, what: str) -> str:
    if not path.is_file():
        raise ResumeSetupError(
            f"{what} marker not found at {path} -- has Step 0a run for this project?"
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ResumeSetupError(f"{what} marker at {path} is empty")
    return value


def _schemas_dir_hash() -> str:
    if not SCHEMAS_DIR.is_dir():
        raise ResumeSetupError(f"schemas directory not found: {SCHEMAS_DIR}")
    files = sorted(SCHEMAS_DIR.glob("*.schema.json"), key=lambda p: p.name)
    if not files:
        raise ResumeSetupError(f"no *.schema.json files found under {SCHEMAS_DIR}")
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()


def _cache_key_for_seg(seg: str) -> dict:
    """Shells out to cache_key.py --seg <id> -- the one shared hashing
    implementation, and the freshest possible source of truth (never a
    caller-supplied, potentially-stale value)."""
    if not CACHE_KEY_SCRIPT.is_file():
        raise ResumeSetupError(f"{CACHE_KEY_SCRIPT} not found")
    try:
        proc = subprocess.run(
            [sys.executable, str(CACHE_KEY_SCRIPT), "--seg", seg],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(DURABLE_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResumeSetupError(f"could not run cache_key.py --seg {seg}: {exc}")
    if proc.returncode != 0:
        raise ResumeSetupError(
            f"cache_key.py --seg {seg} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ResumeSetupError(f"cache_key.py --seg {seg} did not print valid JSON: {exc}")
    if not isinstance(result, dict):
        raise ResumeSetupError(f"cache_key.py --seg {seg} printed a non-object JSON value")
    return result


def _canon_hash() -> str:
    canon_path = DURABLE_ROOT / "canon.json"
    if not canon_path.is_file():
        return "no-canon"
    return hashlib.sha256(canon_path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# input_digest computation
# ---------------------------------------------------------------------------


def compute_input_digest(payload: dict) -> str:
    kind = payload.get("kind")
    if kind not in ("mass", "glossary"):
        raise ResumeSetupError(f"payload 'kind' must be 'mass' or 'glossary', got {kind!r}")

    subst = payload.get("subst")
    if not isinstance(subst, dict):
        raise ResumeSetupError("payload 'subst' must be an object")
    missing_subst = SUBST_FIELDS - set(subst)
    if missing_subst:
        raise ResumeSetupError(
            f"payload 'subst' is missing required field(s): {sorted(missing_subst)}"
        )

    if kind == "mass":
        segs = payload.get("segs")
        if not isinstance(segs, list) or not segs or not all(isinstance(s, str) for s in segs):
            raise ResumeSetupError("payload 'segs' must be a non-empty array of strings for kind='mass'")
        domain = {seg: _cache_key_for_seg(seg) for seg in segs}
    else:
        if "glossary_rule" not in payload:
            raise ResumeSetupError("payload 'glossary_rule' is required for kind='glossary'")
        domain = {"glossary_rule": payload.get("glossary_rule"), "canon_hash": _canon_hash()}

    version = {
        "plugin_bundle_hash": _read_marker(RUNS_DIR / ".plugin_bundle_hash", "plugin_bundle_hash"),
        "orchestration_bundle_hash": _read_marker(
            RUNS_DIR / ".orchestration_bundle_hash", "orchestration_bundle_hash"
        ),
        "schemas": _schemas_dir_hash(),
    }

    digest_input = {
        "kind": kind,
        "args": payload.get("args"),
        "subst": {k: subst[k] for k in SUBST_FIELDS},
        "domain": domain,
        "version": version,
    }
    return _sha256_hex(_canonical_json_bytes(digest_input))


# ---------------------------------------------------------------------------
# Resume decision + run-dir/manifest setup
# ---------------------------------------------------------------------------


def resolve_run(payload: dict) -> "tuple[str, bool, str]":
    """Returns (run_id, resume, input_digest). MATCH against a caller-
    supplied resume_from_run_id's own recorded digest -> resume with that
    same id. MISMATCH, absent candidate digest, or no candidate at all ->
    a fresh RUN_ID, never resumed -- and the candidate's own input.digest
    (if any) is NEVER overwritten."""
    input_digest = compute_input_digest(payload)
    resume_from = payload.get("resume_from_run_id")

    if resume_from is not None:
        err = validate_run_id(resume_from)
        if err:
            raise ResumeSetupError(f"payload 'resume_from_run_id' is invalid: {err}")
        candidate_digest_path = RUNS_DIR / resume_from / "input.digest"
        if candidate_digest_path.is_file():
            prior_digest = candidate_digest_path.read_text(encoding="utf-8").strip()
            if prior_digest == input_digest:
                return resume_from, True, input_digest
            # MISMATCH -- never overwrite the old run's digest file; fall
            # through to a fresh run below.

    for _ in range(RUN_ID_RETRY_LIMIT):
        candidate = fresh_run_id()
        if not (RUNS_DIR / candidate).exists():
            return candidate, False, input_digest
        time.sleep(1)  # extremely unlikely same-second collision; retry once
    raise ResumeSetupError(
        "could not generate a unique fresh RUN_ID after repeated attempts "
        "(clock resolution collision)"
    )


def validate_glossary_batches_shape(batches) -> None:
    """Pure validation of payload['batches'] -- no I/O, no writes. Called
    BEFORE resolve_run()/any directory creation, so a malformed batch list
    (duplicate/negative/non-integer index, empty/malformed names) aborts
    with NOTHING created on disk at all -- not even a fresh RUN_ID's
    input.digest -- rather than leaving a half-written run dir behind a
    validation failure discovered mid-write."""
    if not isinstance(batches, list) or not batches:
        raise ResumeSetupError("payload 'batches' must be a non-empty array for kind='glossary'")

    seen_indexes = set()
    for batch in batches:
        if not isinstance(batch, dict):
            raise ResumeSetupError(f"payload 'batches' item must be an object, got {batch!r}")
        index = batch.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ResumeSetupError(f"batch 'index' must be a non-negative integer, got {index!r}")
        if index in seen_indexes:
            raise ResumeSetupError(f"duplicate batch index: {index}")
        seen_indexes.add(index)

        names = batch.get("names")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
            raise ResumeSetupError(
                f"batch {index}'s 'names' must be a non-empty array of non-empty strings"
            )


def write_glossary_manifests(glossary_run_dir: Path, batches) -> None:
    """Atomically writes manifest_{index}.json (per batch, deduped) and the
    aggregate manifest_all.json (union of every batch, deduped). Assumes
    `batches` already passed validate_glossary_batches_shape()."""
    all_names = []
    for batch in batches:
        index = batch["index"]
        names = batch["names"]
        _atomic_write_json(glossary_run_dir / f"manifest_{index}.json", sorted(set(names)))
        all_names.extend(names)

    _atomic_write_json(glossary_run_dir / "manifest_all.json", sorted(set(all_names)))


def write_run_dir(run_id: str, resume: bool, input_digest: str, kind: str, payload: dict) -> Path:
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"

    if resume:
        # MATCH path: input.digest already holds this exact value on disk
        # (that's how resolve_run() decided to resume) -- never rewritten.
        pass
    else:
        if digest_path.exists():
            # Unreachable in practice (resolve_run() only returns a fresh
            # id for a run_dir whose input.digest didn't already exist) --
            # refuse to clobber a foreign run's digest rather than silently
            # overwrite it.
            raise ResumeSetupError(
                f"refusing to overwrite existing input.digest at {digest_path}"
            )
        _atomic_write_text(digest_path, input_digest + "\n")

    if kind == "glossary":
        # write_glossary_manifests() rewrites manifest_{index}.json /
        # manifest_all.json on BOTH fresh and resumed runs. This is safe ONLY
        # because payload["batches"] (the per-batch name lists) is a pure
        # deterministic derivation of the digest-hashed `args` candidates: on a
        # MATCH-resume the rebuilt manifests are byte-identical to what any
        # in-flight --check-batch poll is validating against, so the rewrite is
        # a content no-op. If that derivation ever stops being deterministic,
        # gate this on a fresh run. (This subsystem is pilot-gated / not yet
        # source-proven end to end.)
        glossary_run_dir = DURABLE_ROOT / "glossary" / "runs" / run_id
        glossary_run_dir.mkdir(parents=True, exist_ok=True)
        write_glossary_manifests(glossary_run_dir, payload.get("batches"))

    return run_dir


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic pre-workflow resume-integrity gate + run-dir/"
            "manifest setup -- see this file's own module docstring."
        ),
    )
    parser.add_argument(
        "--payload-file",
        required=True,
        metavar="PATH",
        help="Path to a JSON payload file -- see module docstring for the exact shape.",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        payload_path = Path(args.payload_file)
        if not payload_path.is_file():
            raise ResumeSetupError(f"payload file not found: {payload_path}")
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ResumeSetupError(f"payload file at {payload_path} is not valid JSON: {exc}")
        if not isinstance(payload, dict):
            raise ResumeSetupError(f"payload file at {payload_path} must contain a JSON object")

        kind = payload.get("kind")
        if kind not in ("mass", "glossary"):
            raise ResumeSetupError(f"payload 'kind' must be 'mass' or 'glossary', got {kind!r}")

        if kind == "glossary":
            # Validated BEFORE any directory is created / RUN_ID resolved --
            # a malformed batch list aborts with nothing on disk at all.
            validate_glossary_batches_shape(payload.get("batches"))

        RUNS_DIR.mkdir(parents=True, exist_ok=True)

        run_id, resume, input_digest = resolve_run(payload)
        run_dir = write_run_dir(run_id, resume, input_digest, kind, payload)

        result = {
            "success": True,
            "effectiveRunId": run_id,
            "resume": resume,
            "run_dir": str(run_dir),
            "input_digest": input_digest,
        }
    except ResumeSetupError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(json.dumps({"success": False, "error": f"unexpected error: {e}"}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
