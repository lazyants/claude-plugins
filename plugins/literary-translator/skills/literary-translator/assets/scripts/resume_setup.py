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
   into one `input_digest`. That digest is only ever compared against the
   candidates the caller offered in `resume_from_run_ids` (see its own
   paragraph below) -- an identical digest alone never resumes. MATCH
   against a candidate's own recorded digest -> resume (`effectiveRunId`
   = that candidate's RUN_ID, `resume: true`, every cached artifact is
   trustworthy). MISMATCH on every candidate, an absent candidate digest,
   or no candidate at all -> a FRESH run: a brand-new RUN_ID,
   `resume: false`, reuse NOTHING. `input.digest`, once written for a
   RUN_ID, is NEVER overwritten with a different value -- a mismatch
   always produces a fresh RUN_ID instead.

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
      "args": <any JSON value>,                  # required. For kind="mass" this
                                                  # MUST be the literal empty object
                                                  # {} -- see the dedicated `args`
                                                  # paragraph below. For
                                                  # kind="glossary" it is the full
                                                  # ordered args this invocation was
                                                  # given, hashed verbatim (unchanged).
      "subst": {                                 # required; every key required
        "research_mode": "...", "verse_policy": "...",
        "source_lang": "...", "target_lang": "...",
        "max_fix_rounds": N, "batch_agent_cap": N,
        "max_codex_jobs_per_batch": N,            # engine.max_codex_jobs_per_batch,
                                                  # or 400 when the profile omits it
        "effort": "low|medium|high|xhigh",       # #197; NOT "model" (see SUBST_FIELDS)
        "citation_content_types": "text/,application/pdf"   # 1.16.1 (#347);
                                                 # "" when the profile key is
                                                 # absent, but REQUIRED even then
      },
      "plugin_root": "<absolute path>" | "",     # #412; optional, defaults to "" --
                                                 # see the dedicated paragraph below
      "resume_from_run_ids": ["<RUN_ID>", ...],  # NEW, optional -- see the dedicated
                                                 # paragraph below. Most-recent-first.
      "resume_from_run_id": "<candidate RUN_ID>" | null,   # DEPRECATED, optional --
                                                 # kept for one release; see below.
                                                 # Mutually exclusive with the plural
                                                 # field above (both present -> error).
      "segs": ["seg01", "seg02", ...],           # DEPRECATED for kind="mass", IGNORED
                                                 # entirely -- see the dedicated
                                                 # paragraph below. Accepted-but-unread
                                                 # for one release only.
      "glossary_rule": <any JSON value>,         # required for kind="glossary"
      "batches": [                               # required for kind="glossary"
        {"index": 0, "names": ["Alice", "Bob"]},
        {"index": 1, "names": ["Carol"]}
      ]
    }

`args` for kind="mass" -- PINNED, not left to prose (LT-409 post-review
fix). Before this fix the field's meaning for the mass path was undefined
in every doc that described it, and the natural reading (this invocation's
own args) was the eligible-SEGS list the Workflow was launched with --
which SHRINKS by one entry every time a segment converges, one level up
from the identical `segs` defect below. Three readings (the shrinking
SEGS list, `{}`, or the field omitted -- `payload.get("args")` yields
`None` when omitted, and `null` canonical-JSON-hashes differently from
`{}`) each hashed differently, so two sessions following the same prose
could silently compute two different digests for the same batch. This
script now REJECTS (raises `ResumeSetupError`) any kind="mass" payload
whose `args` is not the literal empty object `{}` -- closing the class by
making every other value a hard failure, rather than merely documenting a
preferred one. `args` governs Step 1's OWN gating
(`select_segments.py --only-segs`/`--allow-retranslate-converged`/
`--allow-empty`, already run and already enforced before this script is
ever invoked) -- those flags do not change what any already-promoted
per-segment artifact MEANS, so they have no business gating whether this
run's digest matches a prior one, and `{}` is the value that says so
structurally. For kind="glossary", `args` keeps its pre-existing meaning
(the full ordered args this invocation was given, e.g. the candidate
list) and is hashed verbatim, unchanged by this fix.

`segs` -- DEPRECATED for kind="mass" as of LT-409, and now IGNORED
entirely: never read, validated, or otherwise inspected, even when
present. Before this fix the mass-kind digest domain was built directly
from this caller-supplied list, and two callers disagreed about what to
pass it: the SHRINKING post-`select_segments.py` eligible list (shrinks by
one entry every time a segment converges) versus the FULL, stable
manifest candidate set -- so the two paths computed different digests for
the same batch, and the shrinking-list path independently re-minted a
fresh, non-resuming RUN_ID on every single convergence, discarding
in-flight fix work each time. The domain is now derived HERE instead,
directly from `manifest.json`'s own `segments[]` array (mirroring
`select_segments.py`'s own `load_candidate_segments()` shape/validation --
duplicated, not imported, per this project's "no shared lib between
self-contained scripts" convention) -- see `_load_manifest_seg_ids()`.
That set does not shrink as segments converge (a segment's own cache_key
does not change just because its ledger status did), so the digest stays
stable across exactly the case the whole resumability story exists to
survive, while still changing, correctly, when the manifest itself
changes (a real W2/W3 re-run) or any segment's cache_key does (a real
profile/source/derivation change). `segs`, when present, is accepted
purely so an already-deployed caller built against the pre-LT-409 contract
does not fail outright for one release -- the NEXT release should stop
sending it, and this script should stop documenting it as accepted. The
"non-empty array of strings" structural validation this field used to
carry has NOT vanished -- it now lives in `_load_manifest_seg_ids()`,
gating the manifest's own `segments[]` instead (and is in fact stricter:
it also validates each id against the same seg-id-safety allowlist
`select_segments.py`/`codex_job.py` already enforce).

`resume_from_run_ids` (plural, a JSON array of candidate RUN_IDs,
most-recent-first) is the NEW preferred field, replacing singular
`resume_from_run_id` (kept for one release, mutually exclusive with the
plural field -- supplying both is a hard `ResumeSetupError`, not a
silently-resolved ambiguity). This script computes `input_digest` EXACTLY
ONCE per invocation regardless of how many candidates are offered --
kind="mass"'s per-segment `cache_key.py` shell-outs are the expensive part
of that computation -- and compares that ONE digest against every
candidate's own recorded `runs/<candidate>/input.digest`, returning the
FIRST one that MATCHES. A caller that previously had to invoke this
script once PER candidate (paying the full per-segment `cache_key.py` cost
EVERY time -- for a project with N segments and K offered candidates, that
is N*K subprocess spawns) now pays the N-spawn cost exactly once no matter
how many candidates it offers. `effectiveRunId` in the result (below)
already names which candidate matched when `resume` is true -- no separate
"which one matched" field is needed. Omitting both fields is a
genuinely-first-ever-run signal, exactly as before.

MIGRATION COST (measured, not assumed) -- both the `args` pin and the
`segs`->manifest.json domain change above alter what
`compute_input_digest()` hashes for kind="mass", so every pre-existing
`runs/<RUN_ID>/input.digest` written before this fix is invalidated for
resume purposes (the identical inputs, hashed under the OLD formula, will
never again equal what this script now computes). This was NOT assumed
zero-cost: `tome1` genuinely has none (`find <durable_root> -name
input.digest` returns zero across six completed W5 batches -- the resume
gate has never actually fired there), but `ssk-he-en`'s `vol2/run` carries
SIX real run directories with recorded digests, five of them kind="mass"
(`20260714T210207Z`, `20260801T081142Z`, `20260801T090001Z`,
`20260801T124257Z`, `20260801T132418Z` -- the sixth, `20260801T001211Z`,
has a `glossary/runs/<id>/` sibling and is kind="glossary", unaffected).
Each of those five will fail to resume the next time that project's mass
path is invoked: a fresh RUN_ID mints instead, and only whatever segment
was genuinely IN-FLIGHT (not yet `converged`) at that moment gets
re-dispatched under it -- an already-`converged` segment is governed by
`select_segments.py`'s own cache-key/draft-sha1 classification, which
never depended on RUN_ID at all, so it is unaffected either way. No
compatibility shim was built for this: the pre-existing digest is an
opaque hash with no recorded breakdown of which `args`/`segs` a historical
caller actually sent, so there is no way to reconstruct a byte-identical
old-formula comparison to fall back to -- "also try matching the old
formula" is not implementable without fabricating inputs this script never
recorded in the first place. The bounded, one-time cost measured above was
judged cheaper than carrying a compatibility path in this function
permanently; the CHANGELOG is where this migration is recorded for an
operator, not a shim here.

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

`plugin_root` (#412) is the SAME value the orchestrating session substitutes
into the Workflow template's own `{{PLUGIN_ROOT}}` token (see
mass-translate-wf.template.js's header comment) -- recorded here as a
TOP-LEVEL field, deliberately NOT inside `subst` and NOT a member of
SUBST_FIELDS, so it is accepted, type-checked, and documented as part of
this script's producer-side contract, but NEVER folded into `input_digest`.
Two independent reasons, not one:
  1. It is a filesystem PATH (an install LOCATION), not a profile-derived
     semantic/behavioral value the way every `subst` field is -- it does not
     itself describe what a cached agent result MEANS. The signal for "did
     the plugin's actual CONTENT change" already exists and is already
     hashed: `plugin_bundle_hash` (below), a marker Step 0a stamps once from
     the plugin's own bytes.
  2. Hashing a raw absolute path would make `input_digest` NON-PORTABLE for
     a reason that has nothing to do with translation semantics: two
     operators running the IDENTICAL plugin version, with every OTHER subst
     field identical, would get a spurious digest MISMATCH purely because
     their local checkouts sit at different paths -- exactly the kind of
     false invalidation this digest exists to avoid (contrast with
     `source_lang`/`verse_policy`/etc., which are meant to be portable,
     stable values). This is worse than the "NOT model" precedent
     immediately below -- that one was a false DEPENDENCY on an unrelated
     digest; this one is a false dependency on the OPERATOR'S OWN
     filesystem layout.
This script performs NO resolution/derivation on `plugin_root` (unlike
RUN_ID) -- it is a straight, optional pass-through the caller already knows
before ever invoking this script (mirroring how every `subst` field's VALUE
is also caller-resolved, never derived here). It is independent of this
script's OWN pre-existing `--plugin-root` CLI flag below (which governs
ONLY where the cache_key.py SIBLING SCRIPT is resolved from, kind="mass"
only) -- the two seams are never collapsed, even though an orchestrating
session will typically pass the SAME underlying value to both.

On success, prints one JSON line (LT-409: this shape is UNCHANGED by the
`resume_from_run_ids`/`args`/`segs` contract fixes above -- a caller that
already parses this result needs no changes on the read side, only on how
it BUILDS the request):

    {"success": true, "effectiveRunId": "...", "resume": true|false,
     "run_dir": "...", "input_digest": "..."}

`effectiveRunId` is the MATCHED candidate's own RUN_ID when `resume` is
true (this doubles as the answer to "which one of `resume_from_run_ids`
matched"), or a freshly-minted RUN_ID when it is false.

On failure: {"success": false, "error": "..."}. Exit code 0/1 either way
-- callers should read stdout, not rely on the exit code alone.

RUN_ID allowlist (references/ledger-and-resumability.md's `{{RUN_ID}}`
derivation contract): `^[A-Za-z0-9][A-Za-z0-9._-]*$`, the whole value is
never `.`/`..`, and it never contains a `..` substring (reject dir-escape/
collapse) -- a fresh RUN_ID generated here always takes the colon-free
timestamp form `YYYYMMDDTHHMMSSZ`, which trivially satisfies the
allowlist. A caller-supplied `resume_from_run_id` is validated against the
SAME allowlist before it is ever used to build a path.

Self-anchored by default: this script always lives at
${durable_root}/scripts/resume_setup.py, so parents[1] is the durable
root. Never assumes cwd. LT-409 (post-review correction): --durable-root
PATH and --plugin-root PATH are TWO INDEPENDENT overrides. --durable-root
governs DATA (schemas/, runs/). --plugin-root governs where the sibling
cache_key.py script (kind="mass" only) is found, as
{PATH}/assets/scripts/cache_key.py -- deliberately NEVER derived from
--durable-root, because ${durable_root}/scripts/ is writable by the codex
process this resume-integrity gate protects (codex_job.py grants --write
over the whole durable root), so resolving the checker from inside the
thing it checks would let a tampered copy pass itself. Only --durable-root
is forwarded to the cache_key.py subprocess as its own same-named flag:
cache_key.py is a LEAF with no siblings of its own to resolve, and does not
accept --plugin-root at all, so passing it would simply make the invocation
fail. When --plugin-root is given WITHOUT --durable-root, a --durable-root
synthesized from the resolved durable root is passed instead, because
cache_key.py no longer physically sits under that root and would otherwise
self-anchor against the wrong tree. Omitting BOTH reproduces today's
self-anchored behavior byte-for-byte.

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
import shutil
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


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409: `durable_root_str` governs DATA (schemas/runs) -- rebuilt
    from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    cache_key.py SIBLING SCRIPT (kind="mass" only) is resolved from --
    deliberately NEVER derived from `durable_root_str`: ${durable_root}/
    scripts/ is a Step-0a copy the codex process can write to (codex_job.py
    runs it with --write over the whole durable root), so resolving the
    checker from inside the thing it checks would let a tampered copy
    validate itself. When given, it resolves as
    `{plugin_root}/assets/scripts/cache_key.py` -- the SAME layout SKILL.md
    documents for the plugin-anchored scripts, NOT durable_root's own
    flattened `scripts/cache_key.py` copy layout. `plugin_root_str=None`
    reproduces today's self-anchored sibling lookup unchanged.

    Both None -> today's exact self-anchored values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        schemas_dir = SCHEMAS_DIR
        runs_dir = RUNS_DIR
    else:
        durable_root = Path(durable_root_str).resolve()
        schemas_dir = durable_root / "schemas"
        runs_dir = durable_root / "runs"

    if plugin_root_str is None:
        cache_key_script = CACHE_KEY_SCRIPT
    else:
        cache_key_script = Path(plugin_root_str).resolve() / "assets" / "scripts" / "cache_key.py"

    return {
        "durable_root": durable_root,
        "schemas_dir": schemas_dir,
        "runs_dir": runs_dir,
        "cache_key_script": cache_key_script,
    }

SUBST_FIELDS = frozenset({
    "research_mode", "verse_policy", "source_lang", "target_lang",
    "max_fix_rounds", "batch_agent_cap", "max_codex_jobs_per_batch", "effort",
    # 1.16.1 (#347). It changes the prepare step's actual command line, so it
    # changes what a cached citation-review result MEANS: widening the list from
    # ["text/"] to ["text/", "application/pdf"] makes the boundary admit pages it
    # previously refused, and a resumed run would otherwise reuse verdicts taken
    # under the OLD policy while reporting them as current. Omitting it was
    # caught by codex in the 1.16.1 round-3 review, which measured two identical
    # digests across that exact change -- and it would have recreated this
    # release's own stated anti-goal: a profile setting that silently does not
    # take effect.
    "citation_content_types",
})
# NOT "model": the mass digest already carries engine.model via each
# segment's own cache_key/agent_config_hash; the glossary pass has no model
# knob at all, so folding model into this SHARED digest would be a false
# dependency (a model pin would spuriously stale the glossary run too).
#
# NOT "plugin_root" either (#412) -- accepted as its own TOP-LEVEL payload
# field (see the module docstring's payload-shape block for the full
# reasoning), never a member of this set: it is a filesystem PATH, not a
# profile-derived semantic value, and hashing it would make input_digest
# spuriously non-portable across two operators' otherwise-identical
# checkouts. plugin_bundle_hash (below) already owns "did the plugin's
# actual content change".

# ${durable_root}/runs/<RUN_ID>/ -- the same hardened allowlist the
# {{RUN_ID}} substitution token itself is validated against (references/
# ledger-and-resumability.md's "{{RUN_ID}} derivation" section): letters/
# digits/dot/underscore/hyphen only, no ':' (a raw ISO-8601 timestamp is
# intentionally rejected -- this script always generates the colon-free
# form itself).
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

RUN_ID_RETRY_LIMIT = 5

# LT-409: the canonical segment-id allowlist, kept identical to
# select_segments.py's/codex_job.py's/segment_dispatch_driver.py's own
# copies per this project's "no shared lib between self-contained scripts"
# convention (duplicated, not imported). A seg id is either an ordinary
# body id (e.g. "seg01") or a translate-decision FRONTBACK:{id} unit (e.g.
# "FRONTBACK:fm01"). re.fullmatch (NOT re.match + "$") -- in Python "$"
# also matches just before a trailing newline, so re.match(r"...$",
# "seg01\n") would WRONGLY pass. Used by _load_manifest_seg_ids() below --
# the manifest is untrusted input, exactly like select_segments.py's own
# manifest.json read.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal 'FRONTBACK:'
    prefix -- rejecting empties, path separators, '..', absolute paths, and
    every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


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


def _claim_fresh_digest(digest_path: Path, input_digest: str) -> None:
    """Atomically CLAIMS `digest_path` for a fresh (non-resume) run --
    post-review correction. resolve_run()'s own fresh-id loop only READS
    (`if not (dirs["runs_dir"] / candidate).exists(): return candidate, ...`)
    and creates nothing, so two concurrent resume_setup.py invocations
    against the SAME durable_root (e.g. a kind="mass" and a kind="glossary"
    call -- exactly the shape SKILL.md's own "Optional dispatch path"
    section already warns is unguarded) can both observe the same
    not-yet-taken candidate before EITHER creates anything, and both land
    here with the SAME `digest_path`. The pre-existing `if
    digest_path.exists(): raise` + unconditional _atomic_write_text() pair
    this replaces was ITSELF just as racy: a genuinely interleaved pair of
    callers could both pass that check as False and then both proceed to
    write, and _atomic_write_text()'s own os.replace() lets the LAST writer
    silently win with no exception on either side -- exactly the silent
    clobber test_concurrent_write_run_dir_calls_do_not_silently_clobber_
    input_digest() (resume_integrity.test.py) demonstrates.

    Closes it with a single O_CREAT|O_EXCL-equivalent claim: write the full
    content to a pid-suffixed tmp file first (so a mid-write crash can never
    leave a torn/partial digest at the final path), then os.link() the tmp
    file onto `digest_path` -- os.link() either creates the new name
    atomically or fails with FileExistsError, NEVER silently overwrites an
    existing target the way os.replace() does. At most ONE caller can ever
    win a given digest_path; the loser gets a loud ResumeSetupError instead
    of a silent clobber. Mirrors ledger_update.py's own
    mark_ever_converged() -- the SAME os.O_CREAT|os.O_EXCL exclusivity
    idiom, applied to a hardlink instead of a fresh fd, since the content
    here must be written before the claim succeeds rather than after.
    """
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = digest_path.parent / f".{digest_path.name}.claim.{os.getpid()}"
    tmp_path.write_text(input_digest + "\n", encoding="utf-8")
    try:
        try:
            os.link(str(tmp_path), str(digest_path))
        except FileExistsError:
            raise ResumeSetupError(
                f"refusing to overwrite existing input.digest at {digest_path} "
                f"-- claimed by another concurrent resume_setup.py invocation "
                f"before this one could (same fresh RUN_ID, different "
                f"payload). Retry: resolve_run() will pick a fresh RUN_ID."
            )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _read_marker(path: Path, what: str) -> str:
    if not path.is_file():
        raise ResumeSetupError(
            f"{what} marker not found at {path} -- has Step 0a run for this project?"
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ResumeSetupError(f"{what} marker at {path} is empty")
    return value


def _schemas_dir_hash(schemas_dir: Path = SCHEMAS_DIR) -> str:
    if not schemas_dir.is_dir():
        raise ResumeSetupError(f"schemas directory not found: {schemas_dir}")
    files = sorted(schemas_dir.glob("*.schema.json"), key=lambda p: p.name)
    if not files:
        raise ResumeSetupError(f"no *.schema.json files found under {schemas_dir}")
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()


def _cache_key_for_seg(
    seg: str,
    cache_key_script: Path = CACHE_KEY_SCRIPT,
    durable_root: Path = DURABLE_ROOT,
    durable_root_str=None,
    plugin_root_str=None,
) -> dict:
    """Shells out to cache_key.py --seg <id> -- the one shared hashing
    implementation, and the freshest possible source of truth (never a
    caller-supplied, potentially-stale value).

    LT-409: `cache_key_script` is the resolved sibling path to shell out
    against -- self-anchored by default, or resolve_dirs()'s own
    --plugin-root-aware `{plugin_root}/assets/scripts/cache_key.py` (never
    derived from durable_root; see resolve_dirs()'s own docstring for why).
    `durable_root` is cache_key.py's DATA root (cwd for the subprocess) --
    ALREADY resolve()'d by resolve_dirs(), so it is always an absolute path.
    `durable_root_str`/`plugin_root_str` are THIS script's own CLI values
    (cache_key.py has no --plugin-root, being a leaf), used only to DECIDE
    whether a --durable-root should be forwarded at all -- never their own
    string VALUE.

    Post-review correction: whenever either flag is set, the subprocess's
    own --durable-root is now the ALREADY-RESOLVED `durable_root`, never the
    raw `durable_root_str`. Forwarding the raw string used to double-resolve
    it whenever it was RELATIVE: the subprocess below runs with
    cwd=str(durable_root) (an absolute path), so a relative --durable-root
    VALUE would be resolved a SECOND time inside cache_key.py, against that
    already-resolved cwd -- e.g. --durable-root projects/book run from
    /repo resolves HERE to /repo/projects/book, then cache_key.py resolves
    "projects/book" again against that cwd, landing on
    /repo/projects/book/projects/book (a directory that generally doesn't
    exist, or -- worse -- silently reads whatever unrelated tree happens to
    sit there). Forwarding the resolved path is a no-op for a caller that
    already passes an absolute path (every existing caller does), and does
    not change cache_key.py's own contract -- it already accepts an
    absolute --durable-root.
    """
    if not cache_key_script.is_file():
        raise ResumeSetupError(f"{cache_key_script} not found")
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


def _canon_hash(durable_root: Path = DURABLE_ROOT) -> str:
    canon_path = durable_root / "canon.json"
    if not canon_path.is_file():
        return "no-canon"
    return hashlib.sha256(canon_path.read_bytes()).hexdigest()


def _load_manifest_seg_ids(durable_root: Path) -> list:
    """LT-409: the FULL candidate segment-id list from manifest.json's own
    `segments[]` array -- the mass-kind digest DOMAIN source of truth,
    replacing the caller-supplied (and caller-disputed) `segs` payload
    field. Mirrors select_segments.py's own load_candidate_segments()
    shape/validation (duplicated, not imported, per this project's "no
    shared lib between self-contained scripts" convention) -- manifest.json
    is untrusted input here exactly as it is there. Does NOT shrink as
    segments converge (unlike select_segments.py's own emitted SEGS, which
    excludes already-converged `reusable` segments) -- see the module
    docstring's `segs` paragraph for why that distinction is the whole
    point of this function existing."""
    manifest_path = durable_root / "manifest.json"
    if not manifest_path.is_file():
        raise ResumeSetupError(f"manifest.json not found at {manifest_path}")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResumeSetupError(f"could not read manifest.json at {manifest_path}: {exc}")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResumeSetupError(f"manifest.json at {manifest_path} is not valid JSON: {exc}")
    segments = manifest.get("segments") if isinstance(manifest, dict) else None
    if not isinstance(segments, list) or not segments:
        raise ResumeSetupError(f"manifest.json at {manifest_path} has no non-empty 'segments' array")
    ids = []
    for item in segments:
        # manifest.schema.json's segments[] entries are REQUIRED to be
        # objects with (at least) their own `seg` field -- a bare string is
        # not a valid entry under that schema and must be rejected fatally,
        # never silently coerced into a candidate id (same rule
        # select_segments.py's own load_candidate_segments() enforces).
        if not (isinstance(item, dict) and isinstance(item.get("seg"), str)):
            raise ResumeSetupError(f"manifest.json: malformed segments[] entry: {item!r}")
        seg = item["seg"]
        problem = validate_seg(seg)
        if problem is not None:
            raise ResumeSetupError(f"manifest.json: unsafe segment id: {problem}")
        ids.append(seg)
    return ids


# ---------------------------------------------------------------------------
# input_digest computation
# ---------------------------------------------------------------------------


def compute_input_digest(
    payload: dict, dirs: "dict | None" = None, durable_root_str=None, plugin_root_str=None
) -> str:
    """`dirs` (LT-409) is resolve_dirs()'s returned dict -- self-anchored
    defaults, or the --durable-root/--plugin-root-resolved paths.
    `durable_root_str`/`plugin_root_str` are forwarded to the cache_key.py
    subprocess (kind="mass" only) -- see _cache_key_for_seg()'s own
    docstring for the exact forwarding rule. `dirs` defaults to None,
    resolved fresh at call time (never a bound default expression) so a
    direct caller that monkeypatches this module's DURABLE_ROOT/RUNS_DIR/
    etc. globals still observes the patched values."""
    if dirs is None:
        dirs = resolve_dirs(None)
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
        # LT-409: `args` is PINNED to the literal empty object for kind="mass"
        # (see the module docstring's `args` paragraph for the full
        # reasoning) -- enforced HERE, before the expensive per-segment
        # cache_key.py shell-outs below, so a payload built against the old,
        # ambiguous contract fails fast and loud rather than silently
        # hashing whatever it happened to pass.
        mass_args = payload.get("args")
        if mass_args != {}:
            raise ResumeSetupError(
                "payload 'args' must be the literal empty object {} for kind='mass' "
                f"(it governs Step 1's own gating, not resume-integrity); got {mass_args!r}"
            )
        # `segs` (deprecated, LT-409) is deliberately NEVER read here -- see
        # the module docstring's own `segs` paragraph. The domain now comes
        # from manifest.json's full candidate set, which does not shrink as
        # segments converge.
        segs = _load_manifest_seg_ids(dirs["durable_root"])
        domain = {
            seg: _cache_key_for_seg(
                seg,
                dirs["cache_key_script"],
                dirs["durable_root"],
                durable_root_str,
                plugin_root_str,
            )
            for seg in segs
        }
    else:
        if "glossary_rule" not in payload:
            raise ResumeSetupError("payload 'glossary_rule' is required for kind='glossary'")
        domain = {
            "glossary_rule": payload.get("glossary_rule"),
            "canon_hash": _canon_hash(dirs["durable_root"]),
        }

    version = {
        "plugin_bundle_hash": _read_marker(
            dirs["runs_dir"] / ".plugin_bundle_hash", "plugin_bundle_hash"
        ),
        "orchestration_bundle_hash": _read_marker(
            dirs["runs_dir"] / ".orchestration_bundle_hash", "orchestration_bundle_hash"
        ),
        "schemas": _schemas_dir_hash(dirs["schemas_dir"]),
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


def _resume_from_candidates(payload: dict) -> list:
    """LT-409: merges payload['resume_from_run_ids'] (new, plural) and
    payload['resume_from_run_id'] (deprecated, singular, kept for one
    release) into ONE ordered candidate list -- see the module docstring's
    own `resume_from_run_ids` paragraph. Supplying BOTH fields is a hard
    ResumeSetupError, never a silently-resolved ambiguity: there is exactly
    one new consumer of the plural field, so there is no legacy payload
    that could ever legitimately carry both. Every candidate is validated
    against the same RUN_ID allowlist validate_run_id() already enforces,
    BEFORE it is ever used to build a path. Pure, no I/O -- called before
    the expensive compute_input_digest() so a malformed candidate list
    fails fast."""
    plural = payload.get("resume_from_run_ids")
    singular = payload.get("resume_from_run_id")
    if plural is not None and singular is not None:
        raise ResumeSetupError(
            "payload must not supply both 'resume_from_run_ids' and "
            "'resume_from_run_id' -- migrate to the plural field alone"
        )
    if plural is not None:
        if not isinstance(plural, list):
            raise ResumeSetupError("payload 'resume_from_run_ids' must be an array when present")
        candidates = plural
    elif singular is not None:
        candidates = [singular]
    else:
        candidates = []

    validated = []
    for candidate in candidates:
        err = validate_run_id(candidate)
        if err:
            raise ResumeSetupError(f"payload 'resume_from_run_ids' entry is invalid: {err}")
        validated.append(candidate)
    return validated


def resolve_run(
    payload: dict, dirs: "dict | None" = None, durable_root_str=None, plugin_root_str=None
) -> "tuple[str, bool, str]":
    """Returns (run_id, resume, input_digest). MATCH against any candidate
    in payload['resume_from_run_ids'] (or the deprecated singular
    'resume_from_run_id' -- see _resume_from_candidates()) -> resume with
    that SAME candidate's own id, trying candidates in the given order and
    returning the FIRST one whose own recorded runs/<id>/input.digest
    matches. MISMATCH on every candidate, an absent candidate digest, or no
    candidate at all -> a fresh RUN_ID, never resumed -- and no candidate's
    own input.digest (if any) is EVER overwritten. `input_digest` is
    computed EXACTLY ONCE regardless of how many candidates are offered
    (LT-409 -- see the module docstring's `resume_from_run_ids` paragraph
    for the cost this closes). `dirs` defaults to None, resolved fresh at
    call time (see write_run_dir()'s own docstring for why)."""
    if dirs is None:
        dirs = resolve_dirs(None)
    candidates = _resume_from_candidates(payload)
    input_digest = compute_input_digest(payload, dirs, durable_root_str, plugin_root_str)

    for resume_from in candidates:
        candidate_digest_path = dirs["runs_dir"] / resume_from / "input.digest"
        if candidate_digest_path.is_file():
            prior_digest = candidate_digest_path.read_text(encoding="utf-8").strip()
            if prior_digest == input_digest:
                return resume_from, True, input_digest
            # MISMATCH -- never overwrite the old run's digest file; try
            # the next candidate.

    for _ in range(RUN_ID_RETRY_LIMIT):
        candidate = fresh_run_id()
        if not (dirs["runs_dir"] / candidate).exists():
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


def validate_plugin_root_field(payload: dict) -> None:
    """#412: pure validation of payload['plugin_root'] -- no I/O, no writes,
    kind-independent (unlike validate_glossary_batches_shape). Called BEFORE
    resolve_run()/any directory creation, matching that function's own
    fail-before-any-write discipline. Optional (default ""): a payload built
    before #412 landed, or an orchestrating session not yet opting into the
    redirect, must keep working unchanged. When present, must be a string --
    never folded into input_digest, see SUBST_FIELDS's own comment and the
    module docstring's payload-shape block for why."""
    plugin_root = payload.get("plugin_root", "")
    if not isinstance(plugin_root, str):
        raise ResumeSetupError(
            f"payload 'plugin_root' must be a string when present, got {plugin_root!r}"
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


_GLOSSARY_FRAGMENT_RE = re.compile(r"^(out|approved)_(\d+)_attempt_(\d+)\.json$")

# #347 -- the citation audit's prepare step writes a DIRECTORY per batch attempt
# (fetched evidence bodies plus index.json), not a file, so the fragment regex
# above cannot see it and entry.unlink() could not remove it anyway.
# \A...\Z, not ^...$: Python's `$` also matches BEFORE a trailing newline, so
# `^...$` admits "evidence_0_attempt_1\n" -- a directory name POSIX allows.
# This regex gates a shutil.rmtree, so a name that matches by accident is
# deleted by accident. Same anchor defect this release already fixed once in
# the content-type gate (round 3); the sibling in a DESTRUCTIVE path had kept
# the loose form.
_GLOSSARY_EVIDENCE_DIR_RE = re.compile(r"\Aevidence_(\d+)_attempt_(\d+)\Z")


def _wipe_stale_glossary_fragments(glossary_run_dir: Path, resume: bool) -> None:
    """Remove fragments a later wait step could otherwise poll while assuming
    they are absent, closing the resume-freshness gap (LT 1.16.0).

    Nothing deletes fragments elsewhere, and a MATCH-resume reuses the SAME
    run_id (so the same glossary_run_dir), so without this a prior run's
    out_{i}_attempt_{n}.json sits at exactly the path the new run will poll and
    --check-batch passes on it immediately -- the reviewer then audits stale
    bytes. The rule is conditioned on `resume`:

    - Fresh run (resume is False): wipe ALL out_* and approved_* attempts,
      INCLUDING attempt 0. A fresh run must trust nothing on disk: fresh-id
      uniqueness only checks runs/<id>, not this separate glossary/runs/<id>
      tree, so an orphaned glossary dir can survive and collide on the
      one-second timestamp; keeping a stale attempt 0 there is the bug.
    - Resume (resume is True): wipe out_* for attempt >= 1 and ALL approved_*,
      but KEEP out_{i}_attempt_0.json. The resume-skip optimisation depends
      wholly on attempt 0 surviving, and a resume-skipped attempt-0 fragment is
      still citation-reviewed, so keeping it is safe here because the run_id
      genuinely matches by digest. Approved snapshots are never kept: they are
      re-produced by the fresh review of whatever fragment wins this run.

    Cost of the fresh-run wipe is at most one re-dispatch per batch on the rare
    orphan collision, never a wrong result.

    EVIDENCE DIRECTORIES (#347) are wiped UNCONDITIONALLY -- fresh run and
    resume alike, attempt 0 included. They follow the `approved_*` rule, not the
    `out_*` one, and for the same reason: evidence is an OUTPUT of the citation
    review, re-produced by the prepare step that runs before anything judges it,
    so a surviving copy is never useful and is potentially wrong. It is the
    stronger reading of the resume rule, and the asymmetry with `out_*` is
    deliberate: keeping attempt 0's FRAGMENT is what the resume-skip
    optimisation depends on, whereas keeping attempt 0's EVIDENCE buys nothing
    and would leave a previous run's fetched page bodies sitting at exactly the
    paths this run writes. The judge is separately instructed to read only the
    files this run's `index.json` names, so this is defence in depth rather than
    the sole protection -- but a stale body reachable at a live path is the kind
    of thing that outlives the prompt that currently makes it harmless.
    """
    for entry in glossary_run_dir.iterdir():
        if _GLOSSARY_EVIDENCE_DIR_RE.match(entry.name) and entry.is_dir():
            shutil.rmtree(entry)
            continue
        m = _GLOSSARY_FRAGMENT_RE.match(entry.name)
        if m is None:
            continue
        kind_, attempt = m.group(1), int(m.group(3))
        keep = resume and kind_ == "out" and attempt == 0
        if not keep:
            entry.unlink()


def write_run_dir(
    run_id: str, resume: bool, input_digest: str, kind: str, payload: dict, dirs: "dict | None" = None
) -> Path:
    """`dirs` defaults to None (self-anchored, resolved fresh at call time
    via resolve_dirs(None) -- NOT a bound default expression, so a caller
    that monkeypatches this module's DURABLE_ROOT/RUNS_DIR globals directly
    and then calls write_run_dir() with no explicit `dirs` still observes
    the patched values, preserving every pre-LT-409 direct-call test)."""
    if dirs is None:
        dirs = resolve_dirs(None)
    run_dir = dirs["runs_dir"] / run_id
    # exist_ok=True is LOAD-BEARING for resume: on a MATCH, run_dir already
    # legitimately exists from the prior run this call is resuming, and this
    # call must not fail on that. Never make this exclusive -- the resume
    # path depends on the directory already being there.
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"

    if resume:
        # MATCH path: input.digest already holds this exact value on disk
        # (that's how resolve_run() decided to resume) -- never rewritten.
        pass
    else:
        # Post-review correction: was a check-then-write pair
        # (`if digest_path.exists(): raise` then an unconditional
        # _atomic_write_text()) -- itself just as racy as the fresh-id check
        # it was guarding against. _claim_fresh_digest() closes it with a
        # single atomic claim; see its own docstring for the exact scenario
        # and the test that demonstrates it.
        _claim_fresh_digest(digest_path, input_digest)

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
        glossary_run_dir = dirs["durable_root"] / "glossary" / "runs" / run_id
        glossary_run_dir.mkdir(parents=True, exist_ok=True)
        _wipe_stale_glossary_fragments(glossary_run_dir, resume)
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
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where schemas/ and runs/ "
            "are found (including the cache_key.py subprocess's own data, "
            "for kind=\"mass\"), forwarded to it as its own --durable-root. "
            "Optional; omit for today's self-anchored behavior. Independent "
            "of --plugin-root below -- never affects where the SIBLING "
            "SCRIPT itself is found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling cache_key.py script "
            "this script shells out to for kind=\"mass\", as "
            "{PATH}/assets/scripts/cache_key.py -- deliberately NEVER "
            "derived from --durable-root, because ${durable_root}/scripts/ "
            "is writable by the codex process this resume-integrity gate "
            "protects (codex_job.py grants --write over the whole durable "
            "root), so resolving the checker from inside the thing it "
            "checks would let a tampered copy pass itself. Optional; omit "
            "for today's self-anchored sibling lookup."
        ),
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)

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

        # #412: kind-independent, same fail-before-any-write discipline.
        validate_plugin_root_field(payload)

        dirs["runs_dir"].mkdir(parents=True, exist_ok=True)

        run_id, resume, input_digest = resolve_run(
            payload, dirs, args.durable_root, args.plugin_root
        )
        run_dir = write_run_dir(run_id, resume, input_digest, kind, payload, dirs)

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
