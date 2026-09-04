#!/usr/bin/env python3
"""name_discovery.py -- LLM-based proper-name discovery for UNCASED sources (#286).

Produces the ``name_inventory`` a caseless-script project needs, from N
independent cheap-tier codex passes over the book's own segments, filtered
deterministically, and frozen into the project's own
``languages/<code>.local.json``. It replaces the book-local script every
Hebrew volume shipped so far hand-rolled outside ``durable_root``.

WHY THIS EXISTS. ``bootstrap_names.py``'s candidate path is gated on
``is_upper_initial()``, and Hebrew/Yiddish/Arabic letters are Unicode
category ``Lo``: a caseless source yields a STRUCTURAL zero, forever, however
the heuristic is tuned. The only existing bypass is ``name_inventory``, an
exact-form allowlist the operator supplies by hand. This script derives that
allowlist with an LLM and verifies it deterministically -- discovery and
identity are the model's job, structural verification is a script's. The
``Lu``-gate is untouched: the cased path is unchanged, and
``bootstrap_names.py`` and ``segpack.py`` are not edited by this feature.


CJK IS OUT OF SCOPE, and measured rather than assumed. The census matches
WHOLE TOKENS, and `TOKEN_RE` treats a contiguous letter run as one token, so
in unspaced CJK prose the source token is the whole run: against
``我去北京大学。`` the inventory forms ``北京`` and ``北京大学`` both score
ZERO. That is a property of the shipped inventory machinery, identical for a
hand-built ``name_inventory``, not something this pass introduces -- but it
means an inventory is not a working route for CJK at all, so this feature
claims Hebrew, Yiddish and Arabic, the whitespace-delimited uncased scripts
its four real consumers use. Supporting CJK needs segmentation in BOTH the
census and the production extractor and is a separate piece of work.

THE SHAPE IS MEASURED, NOT GUESSED (issue #286's own A/B, on a real he->en
volume's name-richest chapter):
  * N INDEPENDENT passes at a CHEAP tier, unioned -- six concurrent cheap
    passes reached 121 entities in 63s of wall clock, against 111 for three
    high-tier passes at ~450s. Precision is 92-96% at EVERY tier; the tier
    buys recall only, and sampling variance dominates what surfaces.
  * NOT an iterative refinement chain. Measured over six steps:
    83 -> 83 -> 83 -> 87 -> 98 -> 89, with step 6 DELETING 11 correct forms
    it had been handed. Each pass here therefore receives no accumulated
    list, no canon, and no prior state.
  * The DETERMINISTIC OCCURRENCE FILTER is what makes the cheap tier safe:
    in the production run it dropped 1,141 of 1,776 harvested forms.

NO FALLBACK, FAIL LOUD. A partial or failed discovery must never degrade to
the ``Lu``-gate: on a caseless source that yields an empty canon which every
downstream gate happily accepts -- a silent quality collapse dressed as
success. Hence ``--fold``'s completeness gate (exit 2 on any missing or
unbound harvest), its exit 1 on zero survivors, and ``--verify-inventory``,
which W3 runs BEFORE ``bootstrap_names.py`` (that script has no discovery
prerequisite and exits 0 over whatever it finds).

THE PROPERTY ``--verify-inventory`` ENFORCES is deliberately NOT "discovery
ran". A hand-built ``name_inventory`` is the documented pre-#286 route and a
project satisfying the pipeline that way is CORRECT, not a bypass. The
property is "the resolved particle_config carries a non-empty
``name_inventory`` before the glossary pass" -- observable from ONE input, so
the thing checked and the thing used are the same bytes, resolved by the same
rule. An earlier design attested "discovery ran" through a receipt verified by
a downstream consumer; every review round found another artifact it had failed
to bind (the config, the manifest, ``name_candidates.json``, the curation, the
enabled/disabled branch), which is what a check placed far from the fact it
certifies costs.

CLI
    python3 name_discovery.py --dispatch --run-id ID --particle-config F
        [--passes N] [--effort low] [--max-parallel N] [--node node]
        [--deadline-sec N] [--model ID]
    python3 name_discovery.py --fold --run-id ID --particle-config F
        [--honorific-prefix P]... [--dry-run]
    python3 name_discovery.py --verify-inventory --particle-config F

Exit codes: 0 clean / 1 gate-fail (recoverable; re-run) / 2 fatal (usage,
environment, or a refusal). A fatal prints a named line to stderr ONLY and NO
stdout JSON, so nothing can be mistaken for a schema-conforming result -- the
``review_artifact_check.py`` discipline. Every non-fatal path prints exactly
one JSON line to stdout; all human detail goes to stderr.

CONTRACT with language_smoke_report.py (sibling script, same scripts/
directory -- imported, never re-implemented, because THAT file owns the
authoritative census):

    load_particle_config(path: Path) -> dict
        The five-key config loader. Returns a dict carrying `raw_bytes`,
        `particles`, `particles_lower`, `stopwords`, `has_elision`,
        `elision_re` and `name_inventory` -- the last as a FROZENSET, because
        the lru_cached trie below needs a hashable argument. Fatals (exit 2,
        under ITS name) on a config that is not five-key-valid.

    _inventory_scan_pieces(manifest: dict) -> list[str]
        Takes the PARSED manifest object, not a path. Every block whose
        `plain_text` is non-empty after .strip(), sentinel-masked. This is the
        census population, and therefore the population `--dispatch` must
        cover exactly once.

    inventory_forms_seen(pieces: list[str], lang: dict) -> set[str]
        THE occurrence census. Walks the same compiled trie as
        `extract_candidate_spans()`'s pass 2, under the same TERMINATORS and
        whole-token match_units() rules, but records EVERY terminal at every
        position via a parallel match-unit-tuple -> [forms] map.

    segment_plain_text(seg_record, blocks) -> str
    segment_clean_text(seg_record, blocks) -> str
        A segment's text in `block_ids` ORDER (not order_index order), the
        second with sentinels stripped. `segment_plain_text` SILENTLY SKIPS a
        `block_ids` entry naming a block that does not exist, which is why
        this script resolves every reference itself first (see build_units).

    sha1_bytes(data: bytes) -> str
    SENTINEL_RE: re.Pattern

WHY NOT ``extract_candidate_spans()`` FOR THE CENSUS. Pass 2 emits AT MOST
ONE candidate per token position, longest-first, and the trie's terminals do
not carry the surface form. Measured on the live he/yi->en book, THREE
inventory forms occur in the text and are never emitted, because a longer form
covers them at every position. "Survives iff it produced a recorded span"
would silently drop those three -- the exact thin canon this script exists to
prevent. ``inventory_forms_seen``'s own docstring records this; read it before
proposing the cheaper-looking route.

THIS SCRIPT'S OWN CODE IS STDLIB-ONLY, but the import above is not:
``language_smoke_report.py`` imports ``jsonschema`` and can ``sys.exit(2)``
during module import. ``jsonschema`` is already a ``requirements.txt``
dependency and is already required at W3, where the smoke report runs. The
import guard below converts a missing dependency into THIS script's own named
fatal, so the failure is attributed rather than surfacing under another
script's name.

Hash impact, priced rather than asserted (see
references/hash-migration-impact.md):
  * ``particle_config_hash`` MOVES, for an adopting project only -- ``--fold``
    writes the resolved config's ``name_inventory``, so the derivation
    genuinely changed. On a converged book that re-stales every segment: run
    discovery at W3 on a fresh book, or price it with ``--dry-run`` first.
  * ``derivation_bundle_hash`` does NOT move: neither ``bootstrap_names.py``
    nor ``segpack.py`` is edited.
  * ``plugin_bundle_hash`` does NOT move: this file is deliberately not a
    ``PLUGIN_BUNDLE_MEMBERS`` entry. Registration would re-stale every cased
    and discovery-disabled project while not actually forcing rediscovery -- a
    bundle mismatch stales segment cache keys, it does not require the
    inventory to be regenerated.

Reproducibility is SAME-RUNTIME. The census builds its token class from the
running interpreter's Unicode database, so the run manifest records
``python_version`` and ``unidata_version`` as facts rather than claiming
cross-runtime replay.

Self-anchored: this file lives at ``${durable_root}/scripts/name_discovery.py``
and derives durable_root from its own path. It never takes a --durable-root
flag and never assumes cwd.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything, so the whole set opts out
# uniformly rather than case by case.
sys.dont_write_bytecode = True

SCRIPT_VERSION = "1.80.0"

# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them.
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    print(
        f"FATAL name_discovery.py: cannot load json_stdout.py from "
        f"{_JSON_STDOUT_PATH} ({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside name_discovery.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there.",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

dumps_line = _json_stdout.dumps_line

# Self-anchored: ${durable_root}/scripts/name_discovery.py
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
LANGUAGES_DIR = DURABLE_ROOT / "languages"
MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
RUNS_DIR = DURABLE_ROOT / "runs" / "name-discovery"
RESOLVE_COMPANION_SCRIPT = SCRIPTS_DIR / "resolve_codex_companion.py"

# The sibling census. A guarded, named failure -- never a bare ImportError
# surfacing under the other script's name, and never a silent fallback to a
# re-implementation, which is the whole reason this import exists.
#
# SENTINEL_RE and segment_plain_text are imported but never referenced, and that
# is DELIBERATE: build_units below encodes what segment_plain_text does with
# block_ids order and with sentinel-bearing blocks, so the import is an
# import-time assertion that both still exist under those names. If either is
# renamed away, this script fails loudly here instead of silently disagreeing
# with the census it is supposed to mirror.
try:
    from language_smoke_report import (  # noqa: E402
        SENTINEL_RE,
        _inventory_scan_pieces,
        inventory_forms_seen,
        load_particle_config,
        segment_clean_text,
        segment_plain_text,
        sha1_bytes,
    )
except BaseException as _census_exc:  # noqa: BLE001 - SystemExit is the point
    # BaseException, not Exception, and that is the whole guard: a missing
    # jsonschema makes language_smoke_report.py raise SystemExit(2) during its
    # OWN module import, which `except Exception` does not catch -- the error
    # would surface under that script's name, unattributed. Printed to stderr
    # and exited 2 by hand rather than through fatal(), which is not defined
    # this early in the module.
    print(
        f"FATAL name_discovery.py: cannot import the occurrence census from "
        f"language_smoke_report.py in {SCRIPTS_DIR} ({_census_exc!r}).\n"
        "That module must be installed alongside this one under "
        "${durable_root}/scripts/ (Step 0a's copy pass places it there) and it "
        "imports jsonschema, which requirements.txt pins and W3 already needs. "
        "Install the dependency or re-run Step 0a; this script deliberately has "
        "no fallback census -- see its CONTRACT docstring for why a second "
        "implementation of the fold would be a false-PASS risk.",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# ---------------------------------------------------------------------------
# Bounds on model-authored form lists.
#
# `load_particle_config` requires only "non-empty strings", and
# `_capped_candidate_name`'s 200-character protection applies to EMITTED names
# after matching -- its comment rests explicitly on `name_inventory` being
# "operator-supplied, not source-document-controlled". This script breaks that
# provenance, so the bound moves to where the untrusted value ENTERS.
#
# There is deliberately NO separate UTF-8 byte cap: a Unicode scalar is at most
# four UTF-8 bytes, so MAX_FORM_CHARS already implies <= 800 bytes, and a second
# check would be machinery with no reachable case of its own.
# ---------------------------------------------------------------------------
MAX_REPLY_BYTES = 1 << 20
MAX_FORMS_PER_REPLY = 2000
MAX_FORM_CHARS = 200  # matches bootstrap_names.py's _MAX_CANDIDATE_NAME_CHARS
SENTINEL_DELIMITERS = ("⟦", "⟧")

HARVEST_KEYS = frozenset(
    {"run_id", "unit", "pass", "source_sha1", "prompt_sha1", "model", "effort", "forms"}
)

# The reserved unit that holds every non-empty block no segment claims. On the
# validate_seg allowlist by construction (letters and underscores only), so it
# reaches a filename safely; a real seg equal to it is a fatal collision.
UNSEGMENTED_UNIT = "__unsegmented__"

PROBE_TIMEOUT_SEC = 30
BROKER_TEARDOWN_TIMEOUT_SEC = 5
BROKER_EXIT_POLL_SEC = 5.0
LAUNCH_TIMEOUT_SEC = 300
STATUS_TIMEOUT_SEC = 60
DEFAULT_DEADLINE_SEC = 900
DEFAULT_PASSES = 6
DEFAULT_MAX_PARALLEL = 6
DEFAULT_EFFORT = "low"

# Bare-filename contract for particle_config -- must match
# profile_validate.py's own schema pattern exactly. Enforced here in
# defense-in-depth, because --verify-inventory is also invoked by hand.
PARTICLE_CONFIG_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")

# The four keys the census depends on. `name_inventory` is DELIBERATELY absent:
# --fold writes it, so binding it would make a successful fold invalidate its
# own run manifest and no retry after a crash could ever succeed.
CONFIG_SEMANTIC_KEYS = ("ELISION_RE", "PARTICLES", "STOPWORDS", "has_elision")


class NameDiscoveryError(Exception):
    """Carries an `offending` payload folded into the failure JSON."""

    def __init__(self, message, **payload):
        super().__init__(message)
        self.payload = payload


# ---------------------------------------------------------------------------
# COPIED HELPERS. This project keeps every script self-contained -- there is no
# shared util module -- so a cross-cutting decision is duplicated BYTE-IDENTICALLY
# and pinned by a drift test, never imported. Each copy names its owner.
# ---------------------------------------------------------------------------

# Owner: cache_key.py's _SEG_ID_RE / validate_seg -- the CANONICAL copy, which
# tests/seg_validate_drift.test.py names as such. review_artifact_check.py
# carries a deliberately DIVERGENT variant, kept for backward-compatible
# error wording and exempted from that drift check; copying it here would
# have enrolled this script in the exception instead of the contract.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")

# Owner: resume_setup.py's RUN_ID_RE / validate_run_id. Copies live in
# select_segments.py, backfill_resume_gate_ack.py, skeptic_setup.py,
# segment_dispatch_driver.py and claim_record.py, all pinned by
# tests/run_id_pattern_drift.test.py -- which is why THIS copy is registered in
# that test's EXPECTED_COPIES roster as
# ("name_discovery.py", "_RUN_ID_DIR_RE") in the change that introduces it.
_RUN_ID_DIR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Owner: glossary_dispatch_driver.py's _ERE_META / _ere_escape. Deliberately
# NOT re.escape(): that also escapes `-`, `&`, `~`, `#` and the space, and a
# backslash before an ordinary character is UNDEFINED in POSIX ERE.
_ERE_META = frozenset(r"\.[]{}()*+?^$|")


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


def validate_run_id(run_id):
    """Return an error string if `run_id` is not a safe RUN_ID, else None.

    The regex alone is NOT the whole contract: `[A-Za-z0-9._-]` admits dots
    freely, so `.`, `..` and any value CONTAINING `..` pass it and are refused
    separately below. A copy carrying only the pattern therefore ACCEPTS
    `z..poison` while the owner REFUSES it -- agreement on the pattern is not
    agreement on the answer, which is why all three branches are reproduced
    here rather than just the fullmatch.

    Called before ANY path is constructed from the run id, so no later reader
    can forget it.
    """
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not _RUN_ID_DIR_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", ".."):
        return f"run id must not be '.' or '..'; got {run_id!r}."
    if ".." in run_id:
        return f"run id must not contain '..'; got {run_id!r}."
    return None


class _DuplicateKey(ValueError):
    """Internal: raised by `_reject_duplicate_keys` and re-labelled by
    `read_json_strict`. A ValueError so a stray escape still lands in the same
    `except ValueError` net as a malformed document."""


def _reject_duplicate_keys(pairs):
    """`json.loads` object_pairs_hook that REFUSES a repeated member name
    instead of silently keeping the last one.

    Plain `json.loads` resolves `{"forms": ["X"], "forms": []}` to
    `{"forms": []}` -- BEFORE any schema or key-set check can see the
    duplicate. Here that would accept a silently emptied harvest slot as a
    successful one, thinning the union with every gate green. Owner:
    canon_link_groups.py.

    So it is refused, before validation, for every object in the document. A
    duplicate key is never legitimate JSON authoring; nothing well-formed is
    newly rejected."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


def _ere_escape(text):
    return "".join("\\" + ch if ch in _ERE_META else ch for ch in text)


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------

def log(message):
    print(f"name_discovery.py: {message}", file=sys.stderr)


def fatal(message, exit_code=2, **payload) -> NoReturn:
    """A fatal prints a named line to stderr ONLY and NO stdout JSON -- nothing
    this script writes to stdout may ever be mistaken for a schema-conforming
    result. The `review_artifact_check.py` discipline."""
    detail = "".join(f"\n  {k}: {v}" for k, v in sorted(payload.items()))
    print(f"FATAL name_discovery.py: {message}{detail}", file=sys.stderr)
    sys.exit(exit_code)


def emit(payload):
    sys.stdout.write(dumps_line(payload))
    sys.stdout.write("\n")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json_strict(path, describe, keys=None):
    """Parse `path` with duplicate keys refused and, when `keys` is given, an
    exact top-level key set enforced. Every artifact this script re-reads goes
    through here -- including the particle_config, whose own loader
    (`load_particle_config`) uses plain `json.loads` and would collapse a
    duplicate silently."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NameDiscoveryError(f"could not read {describe} at {path}: {exc!r}",
                                 path=str(path))
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKey as exc:
        raise NameDiscoveryError(
            f"{describe} at {path} repeats the member name {exc.args[0]!r}; a "
            f"duplicate key is never legitimate JSON authoring and collapsing it "
            f"silently is how a thinned artifact passes every later check",
            path=str(path), duplicate_key=exc.args[0])
    except (UnicodeDecodeError, ValueError) as exc:
        raise NameDiscoveryError(f"{describe} at {path} is not valid UTF-8 JSON: {exc}",
                                 path=str(path))
    if not isinstance(obj, dict):
        raise NameDiscoveryError(f"{describe} at {path} must be a JSON object",
                                 path=str(path))
    if keys is not None:
        unknown = sorted(set(obj) - set(keys))
        missing = sorted(set(keys) - set(obj))
        if unknown or missing:
            raise NameDiscoveryError(
                f"{describe} at {path} has the wrong key set "
                f"(unknown={unknown}, missing={missing})",
                path=str(path), unknown_keys=unknown, missing_keys=missing)
    return obj, raw


def write_json_atomic(path, payload, *, exclusive=False):
    """tmp -> os.replace, atomic on one filesystem: a concurrent reader sees the
    old file or the new one, never a torn one. `exclusive` refuses to overwrite
    an existing destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise NameDiscoveryError(f"refusing to overwrite {path}", path=str(path))
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name("%s.%d.%s.tmp" % (path.name, os.getpid(), os.urandom(4).hex()))
    # try/finally, not "unlink on the error path": write_text can raise AFTER
    # creating the file (a form carrying a lone surrogate is the reachable case),
    # and os.replace consumes the temp only on success. Either way the directory
    # must not accrete an orphan per failed attempt.
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        # Unconditional, not guarded by exists(): a stat can itself raise on a
        # filesystem fault and would then mask the write error this finally is
        # unwinding from.
        try:
            tmp.unlink()
        except OSError:
            pass


def sha1_text(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Config / manifest resolution
# ---------------------------------------------------------------------------

def resolve_particle_config(filename):
    if not isinstance(filename, str) or not PARTICLE_CONFIG_FILENAME_RE.fullmatch(filename):
        fatal(
            "--particle-config must be a BARE filename under "
            "${durable_root}/languages/ matching [A-Za-z0-9._-]+\\.json -- the "
            "profile's own source.language.particle_config LITERAL value, never "
            "a path and never rebuilt from source.language.code; got "
            f"{filename!r}",
            offending="particle_config")
    path = LANGUAGES_DIR / filename
    if not path.is_file():
        fatal(f"particle_config not found: {path}", offending="particle_config",
              path=str(path))
    return path


def config_semantics_sha1(config_path):
    """sha1 over the FOUR non-inventory keys, canonicalised. Not the whole file:
    --fold writes `name_inventory`, and binding that would make its own
    successful write invalidate the run manifest it must still be able to
    re-verify on a retry."""
    doc, _raw = read_json_strict(config_path, "the resolved particle_config")
    semantics = {k: doc.get(k) for k in CONFIG_SEMANTIC_KEYS}
    return sha1_text(json.dumps(semantics, ensure_ascii=False, sort_keys=True))


def load_manifest():
    if not MANIFEST_PATH.is_file():
        fatal(f"manifest.json not found: {MANIFEST_PATH} -- W2 must run first",
              offending="manifest")
    manifest, raw = read_json_strict(MANIFEST_PATH, "manifest.json")
    return manifest, sha1_bytes(raw)


# ---------------------------------------------------------------------------
# Discovery units -- a CHECKED partition of the manifest's non-empty blocks.
#
# `iter_manifest_texts` (bootstrap_names.py) yields one item per BLOCK keyed by
# a NULLABLE `block.get("seg")`, so it is not a unit source. Segment membership
# is expressed by `segments[].block_ids`, INDEPENDENTLY of a block's own `seg`
# field: a clean manifest has a heading with no `seg` yet listed in its
# segment's block_ids, and nothing gates the reverse -- a block may carry
# seg: "seg01" and be ABSENT from seg01.block_ids (validate_extraction.py
# checks only segment -> block references).
#
# So units are built from block_ids, and the partition is CHECKED against the
# census population rather than assumed. A block reachable by neither route is
# a silent recall loss, which is the exact defect class this feature removes.
# ---------------------------------------------------------------------------

def build_units(manifest):
    blocks = manifest.get("blocks")
    if not isinstance(blocks, dict):
        raise NameDiscoveryError("manifest.json: 'blocks' must be an object")
    # segments is an ARRAY of records, each carrying its own `seg` id and an
    # ORDERED block_ids list (manifest.schema.json's `segments` items; the
    # shipped extract.py.template writes exactly that). It is NOT a mapping --
    # a dict-shaped reading fatals on every real manifest, and a test fixture
    # that invents the mapping shape hides it.
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        raise NameDiscoveryError(
            "manifest.json: 'segments' must be an ARRAY of segment records "
            "(each with its own `seg` and an ordered `block_ids`), per "
            "manifest.schema.json")
    seen_ids = {}
    normalised = []
    for position, seg_record in enumerate(segments):
        if not isinstance(seg_record, dict):
            raise NameDiscoveryError(
                f"manifest.json: segments[{position}] must be an object")
        seg_id = seg_record.get("seg")
        if not isinstance(seg_id, str) or not seg_id:
            raise NameDiscoveryError(
                f"manifest.json: segments[{position}] has no usable `seg` id")
        if seg_id in seen_ids:
            raise NameDiscoveryError(
                f"manifest.json: segment id {seg_id!r} appears twice, at "
                f"positions {seen_ids[seg_id]} and {position}",
                offending="duplicate_segment_id", seg=seg_id)
        seen_ids[seg_id] = position
        normalised.append((seg_id, seg_record))

    def non_empty(bid):
        block = blocks.get(bid)
        if not isinstance(block, dict):
            return False
        text = block.get("plain_text", "")
        return isinstance(text, str) and bool(text.strip())

    # 1. Every block_ids reference must resolve. segment_plain_text SILENTLY
    #    skips a dangling one, so a malformed manifest would otherwise be
    #    dispatched with a hole in it.
    dangling = []
    claimed_by = {}
    for seg_id, seg_record in normalised:
        bids = seg_record.get("block_ids")
        if not isinstance(bids, list):
            raise NameDiscoveryError(
                f"manifest.json: segments[{seg_id!r}].block_ids must be an array")
        for bid in bids:
            if bid not in blocks:
                dangling.append((seg_id, bid))
                continue
            # 2. A block claimed by TWO segments is a fatal collision.
            if bid in claimed_by:
                raise NameDiscoveryError(
                    f"manifest.json: block {bid!r} is claimed by both segment "
                    f"{claimed_by[bid]!r} and {seg_id!r}",
                    offending="duplicate_block_claim", block_id=bid)
            claimed_by[bid] = seg_id
    if dangling:
        raise NameDiscoveryError(
            f"manifest.json: {len(dangling)} segments[].block_ids reference(s) name "
            f"a block that does not exist: {sorted(dangling)[:10]}",
            offending="dangling_block_ids", dangling=sorted(dangling)[:10])

    if UNSEGMENTED_UNIT in seen_ids:
        raise NameDiscoveryError(
            f"manifest.json: a real segment is named {UNSEGMENTED_UNIT!r}, which this "
            f"script reserves for the blocks no segment claims",
            offending="reserved_unit_collision")

    units = []
    consumed = []
    for seg_id, seg_record in sorted(normalised,
                                     key=lambda kv: _segment_sort_key(kv, blocks)):
        bids = list(seg_record.get("block_ids") or [])
        # 4. block_ids order is used AS AUTHORED (segment_plain_text follows it),
        #    so an order that disagrees with order_index would dispatch scrambled
        #    text. Refuse rather than silently reorder or silently accept.
        order_indices = [
            blocks[bid].get("order_index") for bid in bids
            if isinstance(blocks.get(bid), dict) and "order_index" in blocks[bid]
        ]
        if len(order_indices) == len(bids) and order_indices != sorted(order_indices):
            raise NameDiscoveryError(
                f"manifest.json: segment {seg_id!r} lists block_ids in an order that "
                f"disagrees with those blocks' order_index; segment_plain_text follows "
                f"block_ids, so the dispatched text would be scrambled",
                offending="block_ids_order", seg=seg_id)
        members = [bid for bid in bids if non_empty(bid)]
        if not members:
            continue
        err = validate_seg(seg_id)
        if err is not None:
            raise NameDiscoveryError(f"manifest.json: segment id is unsafe -- {err}",
                                     offending="unsafe_seg", seg=seg_id)
        units.append({
            "unit_id": seg_id,
            "block_ids": members,
            "text": segment_clean_text({"block_ids": members}, blocks),
        })
        consumed.extend(members)

    # 3. Exactly one reserved bucket for every non-empty block NO segment
    #    claims, regardless of its own `seg` value. Never silently skipped --
    #    front/back matter carries names.
    orphans = [
        bid for bid in sorted(blocks, key=lambda b: _block_sort_key(blocks[b], b))
        if bid not in claimed_by and non_empty(bid)
    ]
    if orphans:
        units.append({
            "unit_id": UNSEGMENTED_UNIT,
            "block_ids": orphans,
            "text": segment_clean_text({"block_ids": orphans}, blocks),
        })
        consumed.extend(orphans)

    # 5. COVERAGE IS CHECKED. The consumed multiset must equal the non-empty
    #    block set exactly once -- the same population _inventory_scan_pieces
    #    scans, which is what makes the dispatch's denominator and the census's
    #    denominator the same number.
    expected = {bid for bid in blocks if non_empty(bid)}
    seen = {}
    for bid in consumed:
        seen[bid] = seen.get(bid, 0) + 1
    duplicated = sorted(b for b, n in seen.items() if n > 1)
    missing = sorted(expected - set(seen))
    extra = sorted(set(seen) - expected)
    if duplicated or missing or extra:
        raise NameDiscoveryError(
            f"the discovery units do not partition the manifest's non-empty blocks "
            f"(duplicated={duplicated[:10]}, missing={missing[:10]}, extra={extra[:10]}); "
            f"a block covered by neither route would never be shown to the model",
            offending="coverage", duplicated=duplicated[:10],
            missing=missing[:10], extra=extra[:10])
    census_population = len(_inventory_scan_pieces(manifest))
    if len(expected) != census_population:
        raise NameDiscoveryError(
            f"the non-empty-block count this script derived ({len(expected)}) differs "
            f"from the census population _inventory_scan_pieces reports "
            f"({census_population}); the two must agree or the "
            f"occurrence filter is scoring against a different corpus",
            offending="census_population")
    return units


def _segment_sort_key(kv, blocks):
    """`kv` is one (seg_id, seg_record) pair off the normalised segments ARRAY."""
    seg_id, seg_record = kv
    idxs = [
        blocks[bid]["order_index"]
        for bid in (seg_record.get("block_ids") or [])
        if isinstance(blocks.get(bid), dict) and "order_index" in blocks[bid]
    ]
    return (min(idxs) if idxs else 0, seg_id)


def _block_sort_key(block, bid):
    idx = block.get("order_index") if isinstance(block, dict) else None
    return (idx if isinstance(idx, int) else 0, bid)


# ---------------------------------------------------------------------------
# The lock. ONE non-blocking kernel lease, held for the whole process lifetime
# of BOTH modes -- the segment_dispatch_driver.py discipline. Atomic per-file
# replacement gives no atomic SET snapshot, and a set is what --fold consumes,
# so two concurrent dispatches (or a fold racing a dispatch) must be excluded
# rather than detected afterwards.
#
# IT IS SCOPED TO THE PROJECT, NOT TO ONE RUN_ID, and that is not a detail. A
# run-scoped lease would let two DIFFERENT run ids fold concurrently, and every
# fold writes the SAME particle_config through a read-modify-write. Serialising
# only within a run id leaves exactly the interleaving the lock exists to
# remove: one fold publishing while another has the file mid-rewrite. Two
# concurrent discovery runs against one project are not a use case anyway --
# they would fight over the one artifact the feature produces.
#
# The guarantee is ONE MACHINE, on a filesystem that enforces flock. Acquiring
# it requires creating runs/name-discovery/ and the sentinel first, so a LOSING
# invocation may leave those behind; the sentinel is never unlinked and is not a
# stale lock -- a hard kill releases the kernel lease and an interrupted
# dispatch resumes normally.
# ---------------------------------------------------------------------------

class RunLock:
    def __init__(self):
        self.path = RUNS_DIR / ".name_discovery.lock"
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            fatal(f"could not open the run lock at {self.path}: {exc!r}",
                  offending="run_lock")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._fd)
            self._fd = None
            fatal(
                f"another name_discovery.py invocation holds the lease on "
                f"{self.path}. Dispatch and fold are mutually exclusive for this "
                f"PROJECT, not merely for one run id: two dispatches would "
                f"atomically replace the same harvest slots with different "
                f"stochastic replies, and two folds -- even under different run "
                f"ids -- read-modify-write the SAME particle_config, so one could "
                f"publish while the other has it mid-rewrite. Nothing has been "
                f"written; wait for the other invocation and re-run.",
                offending="run_lock_contended")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        return False


# ---------------------------------------------------------------------------
# The untrusted-writer boundary, ported from the two existing local drivers.
# Codex is an untrusted writer here exactly as it is there.
# ---------------------------------------------------------------------------

_PROBE_ENCLOSED = "enclosed"
_PROBE_STANDALONE = "standalone"
_PROBE_GIT_ABSENT = "git-absent"
_PROBE_NO_VERDICT = "no-verdict"

# A sandbox is confined iff it resolves to ITSELF under codex-companion's own
# workspace-root algorithm. _PROBE_NO_VERDICT is ABSENT, and its absence is the
# fail-closed rule: a bounded git call that timed out tells us nothing, while
# the companion's own probe is UNBOUNDED and would still find an enclosing
# repository. _PROBE_GIT_ABSENT is the one no-result case that is still safe,
# because the companion's resolver degrades the SAME way.
_CONFINED_PROBE_OUTCOMES = (_PROBE_STANDALONE, _PROBE_GIT_ABSENT)


def implicit_write_roots():
    roots = [Path("/tmp")]
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        roots.append(Path(env_tmp))
    return roots


def refuse_if_under_a_temp_root(durable_root):
    """The one confinement case no --cwd can reach -- REFUSED, not disclosed.

    Everything else here confines a job to its own throwaway directory. That
    argument holds for every location EXCEPT one already inside a root codex
    makes writable implicitly: a durable_root there is still fully
    model-writable -- scripts/, the harvest, the particle_config this run is
    about to publish -- whatever this driver passes as --cwd.

    Compared CANONICALLY on both sides: macOS resolves /tmp to /private/tmp and
    $TMPDIR under /var/folders to /private/var/folders, so a lexical test would
    miss the very case it is for."""
    temp_roots = []
    for raw in implicit_write_roots():
        try:
            temp_roots.append(Path(os.path.realpath(str(raw))))
        except OSError:
            continue
    resolved = Path(os.path.realpath(str(durable_root)))
    for temp_root in temp_roots:
        if resolved == temp_root or temp_root in resolved.parents:
            fatal(
                f"refusing to run: the durable root {resolved} lies under "
                f"{temp_root}, which codex makes writable under workspace-write "
                f"whatever this driver passes as --cwd. A dispatched job could "
                f"still write there, so the per-launch sandbox would confine it "
                f"away from everything EXCEPT the paths this pass most needs "
                f"protected -- scripts/, the harvest, and the particle_config it "
                f"is about to publish. Move it outside the temp roots (durable "
                f"state is meant to outlive a reboot in any case), or set TMPDIR "
                f"elsewhere.",
                offending="durable_root_under_temp_root", path=str(resolved),
                implicit_write_root=str(temp_root))


def probe_enclosing_repo(path):
    """Runs the companion's OWN workspace-root probe and reports WHICH outcome
    occurred, never a bare boolean -- the polarity here is inverted from the
    usual (absence of a repository is the SUCCESS condition), so a collapsed
    None would score a no-verdict probe as confined and fail OPEN."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=PROBE_TIMEOUT_SEC, cwd=str(path),
        )
    except FileNotFoundError:
        return _PROBE_GIT_ABSENT
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return _PROBE_NO_VERDICT
    return _PROBE_ENCLOSED if proc.returncode == 0 else _PROBE_STANDALONE


def _safe_close(descriptor):
    try:
        os.close(descriptor)
    except OSError:
        pass


def open_regular_no_follow_walk(path):
    """Opens `path` component-by-component from the filesystem root, with
    O_NOFOLLOW at EVERY step -- not just the leaf.

    A confined job can still WRITE A SYMLINK inside its own sandbox: write
    confinement restricts where writes LAND, never what a symlink's target
    string names. And a check followed by a SEPARATE later read is two
    lookups, with a window between them for a leaf swap; the fd returned here
    is the same fd the caller reads from.

    The leaf open carries O_NONBLOCK so a FIFO planted at the leaf returns
    immediately instead of blocking INSIDE os.open() -- before type checking
    can refuse it, and before any caller timeout can start. It is CLEARED once
    S_ISREG confirms a genuine regular file, because S_ISREG does not
    universally guarantee a nonblocking read cannot short-read.

    Returns (fd, "file") -- the CALLER owns the fd -- or (None, "absent") /
    (None, "suspicious"). Owner of this shape: codex_job.py's _is_regular and
    glossary_dispatch_driver.py's own walk."""
    candidate = Path(path)
    parts = candidate.parts if candidate.is_absolute() else candidate.resolve().parts
    if len(parts) < 2:
        return None, "suspicious"
    fd = None
    leaf_fd = None
    try:
        fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
        for name in parts[1:-1]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            closing, fd = fd, None
            _safe_close(closing)
            fd = next_fd
        leaf_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        closing, fd = fd, None
        _safe_close(closing)
        st = os.fstat(leaf_fd)
        if not stat.S_ISREG(st.st_mode):
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
            return None, "suspicious"
        try:
            current_flags = fcntl.fcntl(leaf_fd, fcntl.F_GETFL)
            fcntl.fcntl(leaf_fd, fcntl.F_SETFL, current_flags & ~os.O_NONBLOCK)
        except OSError as exc:
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
            log(f"warning: could not clear O_NONBLOCK on {path} after classifying it "
                f"regular: {exc}; treating as suspicious")
            return None, "suspicious"
    except FileNotFoundError:
        for descriptor in (fd, leaf_fd):
            if descriptor is not None:
                _safe_close(descriptor)
        return None, "absent"
    except OSError as exc:
        for descriptor in (fd, leaf_fd):
            if descriptor is not None:
                _safe_close(descriptor)
        log(f"warning: no-follow walk to {path} refused: {exc}; treating as suspicious")
        return None, "suspicious"
    return leaf_fd, "file"


def read_sandbox_reply(path, label):
    """Reads the reply a dispatched codex job wrote, no-follow and byte-capped."""
    fd, state = open_regular_no_follow_walk(path)
    if state != "file" or fd is None:
        raise NameDiscoveryError(
            f"{label}: the sandbox reply at {path} is not a regular file reachable "
            f"without following a symlink (state={state})",
            label=label, reply_state=state)
    try:
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(MAX_REPLY_BYTES + 1)
    except OSError as exc:
        raise NameDiscoveryError(f"{label}: could not read the sandbox reply: {exc!r}",
                                 label=label)
    if len(data) > MAX_REPLY_BYTES:
        raise NameDiscoveryError(
            f"{label}: the sandbox reply exceeds {MAX_REPLY_BYTES} bytes and is "
            f"refused rather than truncated -- truncation would silently thin the union",
            label=label)
    return data


class DispatchSandbox:
    """One launch's write-confined directory, as a context manager.

    REFUSES rather than degrades: an unconfined sandbox is worse than no launch,
    because it hands back exactly the access this class exists to remove while
    reporting that it had been removed.

    TEARDOWN STOPS THE BROKER BEFORE REMOVING THE DIRECTORY, and that order is
    load-bearing. codex-companion keys a PERSISTENT broker to whatever --cwd it
    is handed (app-server-broker.mjs serve --cwd <dir>, spawned detached), so a
    per-launch cwd leaves one broker per launch behind -- the leak measured at
    2794 state directories in a single day. At 211 segments x 6 passes this pass
    would start 1266 of them. SIGTERM, never SIGKILL: the broker's own handler
    closes its app-server client, taking codex app-server and
    codex-code-mode-host down with it; SIGKILL leaves exactly those children
    behind, which is the leak itself.

    ONE DELIBERATE ADDITION over the glossary driver's teardown, which ends
    immediately after os.kill(): a bounded poll for the broker's exit before the
    directory is removed. At this launch count, "signalled" and "gone" being
    different states is the whole point.

    Best-effort teardown, never raising: cleanup must not turn a finished unit
    into a failed one.

    WHAT THIS DOES NOT ISOLATE, stated rather than left to be discovered. Every
    sandbox is a mkdtemp under the SAME implicit write root codex already has
    ($TMPDIR, see refuse_if_under_a_temp_root), so concurrently dispatched jobs
    are not isolated from EACH OTHER: one could overwrite a sibling's reply
    before this driver reads it, and the driver would attach that slot's trusted
    metadata to the planted content. A planted FORM is largely inert -- the
    census still scores it against the real source -- but an EMPTIED reply
    yields a thin harvest that looks complete. This is the shape both shipped
    drivers already have (codex_job.py and glossary_dispatch_driver.py mkdtemp
    the same way), so it is inherited, not introduced here, and closing it
    properly means per-job isolation outside every implicit write root, across
    all three drivers at once. Tracked separately; do not "fix" it here in a way
    that diverges from the other two."""

    def __init__(self, label):
        self.label = label
        self.path = None

    def __enter__(self):
        try:
            raw = tempfile.mkdtemp(prefix="ltnd.%s." % re.sub(r"[^A-Za-z0-9_.-]", "_", self.label))
        except OSError as exc:
            fatal(f"could not create a dispatch sandbox for {self.label}: {exc!r}",
                  label=self.label)
        # Pin ONE canonical spelling now -- macOS's /tmp -> /private/tmp symlink
        # otherwise yields two spellings of the same directory across this run,
        # including the companion's own state-dir keying, which would silently
        # miss each other.
        self.path = Path(os.path.realpath(raw))
        outcome = probe_enclosing_repo(self.path)
        if outcome not in _CONFINED_PROBE_OUTCOMES:
            self._teardown()
            fatal(
                "refusing to dispatch: the codex sandbox is not write-confined "
                f"(probe={outcome}). codex-companion resolves its workspace-write "
                "root by walking up from --cwd to the enclosing git top level, so "
                "a sandbox inside a working tree would hand the job write access "
                "to that whole repository. Set TMPDIR to a directory outside every "
                "git working tree and re-run -- nothing about this run has been "
                "recorded, so the re-run resumes exactly where this one stopped.",
                label=self.label, sandbox_probe=outcome)
        return self

    def __exit__(self, exc_type, exc, tb):
        self._teardown()
        return False

    def reply_path(self):
        assert self.path is not None, "reply_path() before __enter__"
        return self.path / "names.json"

    def _teardown(self):
        if self.path is None:
            return
        self._shutdown_broker()
        shutil.rmtree(str(self.path), ignore_errors=True)
        if self.path.exists():
            log(f"{self.label}: sandbox {self.path} could not be removed and is left "
                f"on disk; remove it by hand")
        self.path = None

    def _broker_pids(self):
        """Matched on ARGV rather than by reading the companion's own broker
        record: reading that record would mean duplicating its private state
        directory scheme here, where a silent upstream change would present as
        this cleanup simply never firing -- and the record is not written for
        every broker that exists. The match cannot hit anything else: the
        sandbox path is a single-use mkdtemp path reaching the broker's argv
        verbatim, the pattern additionally requires app-server-broker.mjs, and
        the path is anchored so a longer sibling path cannot match."""
        pattern = "app-server-broker\\.mjs .*--cwd %s( |$)" % _ere_escape(str(self.path))
        try:
            proc = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                                  text=True, timeout=BROKER_TEARDOWN_TIMEOUT_SEC)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        # pgrep: 0 = matched, 1 = nothing matched, >=2 = pgrep itself failed.
        # Only 0 carries pids.
        if proc.returncode != 0:
            return []
        own = os.getpid()
        pids = []
        for field in (proc.stdout or "").split():
            try:
                pid = int(field)
            except ValueError:
                continue
            if pid <= 1 or pid == own:
                continue
            pids.append(pid)
        return pids

    def _shutdown_broker(self):
        pids = self._broker_pids()
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + BROKER_EXIT_POLL_SEC
        while time.monotonic() < deadline:
            remaining = self._broker_pids()
            if not remaining:
                return
            time.sleep(0.2)
        remaining = self._broker_pids()
        if remaining:
            log(f"{self.label}: broker pid(s) {remaining} did not exit within "
                f"{BROKER_EXIT_POLL_SEC}s of SIGTERM; the sandbox is being removed "
                f"anyway, so they can only write into a directory nobody consumes from")


def resolve_companion(node_bin):
    """The installed codex-companion.mjs path, via the shipped resolver. Codex is
    the required engine (R1) and there is no non-LLM discovery path by design, so
    an unresolvable companion is fatal rather than a degraded run.

    BOTH arguments are part of the resolver's shipped CLI contract, not optional
    politeness: `--durable-root` is `required=True` there, so omitting it makes the
    resolver exit on its own argparse error and every dispatch fail before it starts
    (#843). `--node` decides which node binary the resolver probes the candidate
    companion with, so passing this driver's own `--node` keeps the probe and the
    later launch talking about the same runtime."""
    if not RESOLVE_COMPANION_SCRIPT.is_file():
        fatal(f"resolve_codex_companion.py not found at {RESOLVE_COMPANION_SCRIPT} -- "
              f"Step 0a's copy pass places it there", offending="companion_resolver")
    try:
        proc = subprocess.run(
            [sys.executable, str(RESOLVE_COMPANION_SCRIPT),
             "--durable-root", str(DURABLE_ROOT), "--node", node_bin],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run the codex-companion resolver: {exc!r}",
              offending="companion_resolver")
    if proc.returncode != 0:
        fatal("could not resolve codex-companion.mjs -- codex is the required engine "
              "for discovery and there is no deterministic fallback by design",
              offending="companion_resolver",
              resolver_stderr=(proc.stderr or "")[-800:])
    try:
        path = json.loads((proc.stdout or "").strip().splitlines()[-1])["companion_path"]
    except (ValueError, KeyError, IndexError):
        fatal("the codex-companion resolver printed no readable companion_path",
              offending="companion_resolver",
              resolver_stdout=(proc.stdout or "")[-800:])
    return path


# ---------------------------------------------------------------------------
# The prompt. Independent and stateless BY CONSTRUCTION -- no canon, no prior
# list, no accumulated state. The measured chain shape (83 -> ... -> 89, step 6
# deleting 11 correct forms) is why nothing is ever handed back to a later pass.
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are reading one chapter of a book in its ORIGINAL language.

List every proper name that appears in the text below: people, places, works,
organisations, and titles used as names.

Rules:
- Spell each name BYTE-FOR-BYTE as the text spells it at that occurrence.
  Do not normalise vowel points, do not expand abbreviations, do not
  transliterate, do not translate, and do not add or remove a leading title.
- Include a name even if you are unsure it is a name. A separate deterministic
  filter verifies every form against the source text afterwards, so a wrong
  guess costs nothing and a missing name cannot be recovered.
- Do not invent a spelling that is not in the text.

Reply with exactly one line of JSON and nothing else:
{{"forms": ["...", "..."]}}

Write that JSON to the file {reply_path} and nothing else to disk.

--- TEXT BEGINS ---
{text}
--- TEXT ENDS ---
"""


def render_prompt(text, reply_path):
    return PROMPT_TEMPLATE.format(text=text, reply_path=reply_path)


def prompt_identity_sha1():
    """sha1 over the prompt TEMPLATE. The unit text and the sandbox path are
    excluded by construction -- they are the template's only two substitutions,
    the unit's own identity is `source_sha1`, and the sandbox path is a
    per-launch mkdtemp value that must not make an unchanged prompt look changed.
    Moves whenever the wording or the schema instruction changes what the model
    is asked."""
    return sha1_text(PROMPT_TEMPLATE)


# ---------------------------------------------------------------------------
# The run manifest. Written BEFORE any dispatch, atomically, and never
# rewritten -- resume_setup.py's discipline: compute the identity once, reuse a
# run only on EXACT equality, never overwrite a mismatching run's stamp.
#
# `backup_sha1` is computed HERE, from the particle_config as it stands before
# any dispatch. A value "filled by the first fold" would force a rewrite of a
# file this design calls write-once, and would leave a crash between the config
# write and the sidecar with nothing to verify the create-once backup against.
# ---------------------------------------------------------------------------

RUN_MANIFEST_KEYS = (
    "run_id", "script_version", "created_utc", "python_version", "unidata_version",
    "census_contract_sha1",
    "particle_config", "config_semantics_sha1", "manifest_sha1", "backup_sha1",
    "units", "passes", "model", "effort", "prompt_sha1",
)


def census_contract_sha1():
    """sha1 of language_smoke_report.py's bytes -- the SURVIVAL RULE's identity.

    It is bound and verified because that module decides which harvested forms
    reach the inventory. A run dispatched before an upgrade and folded after it
    would otherwise score the same harvest under a different census and publish
    a different survivor set, while the sidecar reported the old provenance."""
    return sha1_bytes((SCRIPTS_DIR / "language_smoke_report.py").read_bytes())


def run_dir_for(run_id):
    return RUNS_DIR / run_id


def unit_descriptors(units):
    """The persisted shape of build_units' output -- everything the fold needs
    about a unit and nothing derived at dispatch time.

    ONE definition, because --fold rebuilds this from the current manifest and
    compares it to what is on disk: two spellings of the structure would let the
    comparison pass while the two sides meant different things."""
    return [
        {
            "unit_id": u["unit_id"],
            "block_ids": list(u["block_ids"]),
            "source_sha1": sha1_text(u["text"]),
            "chars": len(u["text"]),
        }
        for u in units
    ]


def build_run_manifest(*, run_id, config_path, config_sha1_now, manifest_sha1,
                       units, passes, model, effort):
    return {
        "run_id": run_id,
        "script_version": SCRIPT_VERSION,
        "created_utc": utc_now(),
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "unidata_version": unicodedata.unidata_version,
        "census_contract_sha1": census_contract_sha1(),
        "particle_config": config_path.name,
        "config_semantics_sha1": config_semantics_sha1(config_path),
        "manifest_sha1": manifest_sha1,
        "backup_sha1": config_sha1_now,
        "units": unit_descriptors(units),
        "passes": passes,
        "model": model,
        "effort": effort,
        "prompt_sha1": prompt_identity_sha1(),
    }


def _identity_fields(doc):
    """Everything a run's identity is bound to. `created_utc`, `script_version`
    and `python_version` are excluded deliberately: they are provenance, not
    identity, and a patch release or an interpreter bump must not force a whole
    book to be re-discovered. `unidata_version` and `census_contract_sha1` ARE
    identity, because both change what the census COUNTS -- the token class is
    built from the running Unicode database, and the census itself is the
    survival rule."""
    return {
        k: doc.get(k) for k in
        ("run_id", "particle_config", "config_semantics_sha1", "manifest_sha1",
         "backup_sha1", "units", "passes", "model", "effort", "prompt_sha1",
         "unidata_version", "census_contract_sha1")
    }


def load_run_manifest(run_id):
    path = run_dir_for(run_id) / "run-manifest.json"
    if not path.is_file():
        raise NameDiscoveryError(
            f"no run manifest at {path}. --fold derives its expected harvest set from "
            f"that file and never from a directory listing, so it cannot run without "
            f"it; re-run --dispatch with this run id.",
            offending="run_manifest_absent", path=str(path))
    doc, _raw = read_json_strict(path, "the run manifest", keys=RUN_MANIFEST_KEYS)
    return doc, path


def sidecar_path(run_id):
    return run_dir_for(run_id) / "name-discovery.json"


def backup_path(run_id):
    return run_dir_for(run_id) / "particle_config.before.json"


def harvest_path(run_id, unit_id, pass_index):
    return run_dir_for(run_id) / "harvest" / f"{unit_id}.{pass_index}.json"


# ---------------------------------------------------------------------------
# Reply validation. Applied when the model's reply is READ, before anything is
# written or tokenized.
# ---------------------------------------------------------------------------

def validate_form_list(forms, label):
    """THE form-list contract, in ONE place, applied wherever a form list enters
    this script -- a model reply AND a harvest read back off disk.

    Two entry points, not one, and that is the whole reason this is a function:
    a run's harvest is a persisted artifact the operator is explicitly invited
    to hand-edit before folding, so `--fold` re-reads form lists that never went
    through parse_reply in this process (and, after a resume, possibly never in
    any process running this version). A bound applied only at the reply
    boundary is therefore not applied to everything that reaches the inventory.

    Returns the cleaned, stripped, deduplicated, sorted list."""
    if not isinstance(forms, list):
        raise NameDiscoveryError(f"{label}: 'forms' must be an array", label=label)
    if len(forms) > MAX_FORMS_PER_REPLY:
        raise NameDiscoveryError(
            f"{label}: {len(forms)} forms exceeds the {MAX_FORMS_PER_REPLY} cap and is "
            f"refused rather than truncated", label=label, n_forms=len(forms))
    cleaned = []
    for raw in forms:
        if not isinstance(raw, str) or not raw.strip():
            raise NameDiscoveryError(
                f"{label}: every form must be a non-empty string; got {raw!r}",
                label=label)
        form = raw.strip()
        if len(form) > MAX_FORM_CHARS:
            raise NameDiscoveryError(
                f"{label}: a form is {len(form)} characters, over the "
                f"{MAX_FORM_CHARS} cap that matches bootstrap_names.py's own "
                f"_MAX_CANDIDATE_NAME_CHARS", label=label, form_chars=len(form))
        if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in form):
            raise NameDiscoveryError(
                f"{label}: a form carries a Unicode control character or lone "
                f"surrogate (Cc/Cf/Cs)", label=label)
        if any(d in form for d in SENTINEL_DELIMITERS):
            raise NameDiscoveryError(
                f"{label}: a form carries a sentinel delimiter, which would collide "
                f"with this pipeline's own inline markers", label=label)
        cleaned.append(form)
    return sorted(set(cleaned))


def parse_reply(data, label):
    if len(data) > MAX_REPLY_BYTES:
        raise NameDiscoveryError(f"{label}: reply exceeds {MAX_REPLY_BYTES} bytes",
                                 label=label)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NameDiscoveryError(f"{label}: reply is not valid UTF-8: {exc}", label=label)
    try:
        obj = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKey as exc:
        raise NameDiscoveryError(
            f"{label}: reply repeats the member name {exc.args[0]!r}; collapsing it "
            f"silently would accept an emptied slot as a successful one",
            label=label, duplicate_key=exc.args[0])
    except ValueError as exc:
        raise NameDiscoveryError(f"{label}: reply is not one JSON object: {exc}",
                                 label=label)
    if not isinstance(obj, dict) or set(obj) != {"forms"}:
        raise NameDiscoveryError(
            f"{label}: reply must be a JSON object whose ONLY key is 'forms'; got "
            f"{sorted(obj) if isinstance(obj, dict) else type(obj).__name__}",
            label=label)
    forms = obj["forms"]
    if not isinstance(forms, list):
        raise NameDiscoveryError(f"{label}: 'forms' must be an array", label=label)
    return validate_form_list(forms, label)


def validate_harvest(path, expected, describe):
    """A harvest counts only when it validates AND is bound to its slot. A
    schema-valid file copied into another expected filename is refused, not
    counted -- filename completeness is not input completeness."""
    doc, _raw = read_json_strict(path, describe, keys=HARVEST_KEYS)
    for field, want in expected.items():
        if doc.get(field) != want:
            raise NameDiscoveryError(
                f"{describe} at {path} is not bound to this slot: {field} is "
                f"{doc.get(field)!r}, the run manifest says {want!r}",
                path=str(path), field=field)
    # The SAME contract parse_reply applied, re-applied on the way back in. A
    # harvest is a file, and the workflow invites hand-editing it before the
    # fold; every bound the reply boundary enforces has to hold here too, or an
    # edited artifact reaches the particle config through a door the model's own
    # reply cannot use.
    doc["forms"] = validate_form_list(doc.get("forms"), describe)
    return doc


# ---------------------------------------------------------------------------
# --dispatch
# ---------------------------------------------------------------------------

def launch_one(*, companion, node_bin, unit_text, effort, model, deadline_sec, label):
    """One codex turn in its own confined sandbox. Returns the parsed form list,
    or raises NameDiscoveryError -- which the caller counts as a failed slot and
    writes nothing for."""
    with DispatchSandbox(label) as sandbox:
        reply = sandbox.reply_path()
        prompt = render_prompt(unit_text, reply)
        prompt_file = sandbox.path / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        argv = [node_bin, companion, "task", "--background", "--json", "--write", "--fresh"]
        if effort:
            argv += ["--effort", effort]
        if model:
            argv += ["--model", model]
        argv += ["--cwd", str(sandbox.path), "--prompt-file", str(prompt_file)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=LAUNCH_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NameDiscoveryError(f"{label}: codex launch failed: {exc!r}", label=label)
        if proc.returncode != 0:
            raise NameDiscoveryError(
                f"{label}: codex launch returned {proc.returncode}", label=label,
                launch_stderr=(proc.stderr or "")[-600:])
        try:
            obj = json.loads(proc.stdout)
        except ValueError:
            obj = None
        job_id = obj.get("jobId") if isinstance(obj, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise NameDiscoveryError(
                f"{label}: codex launch printed no jobId, so this turn cannot be "
                f"watched", label=label, launch_stdout=(proc.stdout or "")[-600:])
        # A job codex-companion has already recorded terminal is a turn that is
        # OVER -- nothing can write the reply after it -- so the status read ends
        # the wait rather than burning the rest of the deadline. The REPLY is
        # re-checked before every not-ready exit: the status probe itself takes
        # real time, and a job can write its reply and then go terminal.
        deadline = time.monotonic() + deadline_sec
        while True:
            if reply.exists():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            status = read_job_status(companion=companion, node_bin=node_bin,
                                     job_id=job_id, sandbox_root=sandbox.path,
                                     timeout=min(STATUS_TIMEOUT_SEC, remaining))
            if reply.exists():
                break
            if status in ("completed", "failed", "cancelled"):
                break
            time.sleep(min(5.0, max(0.5, deadline - time.monotonic())))
        if not reply.exists():
            raise NameDiscoveryError(
                f"{label}: codex job {job_id} wrote no reply within {deadline_sec}s",
                label=label, job_id=job_id)
        return parse_reply(read_sandbox_reply(reply, label), label)


def read_job_status(*, companion, node_bin, job_id, sandbox_root, timeout):
    """ANY failure here returns None: UNKNOWN, never a fact about the job. The
    reply poll stays in charge on an unknown read -- a status this driver cannot
    read must never turn into a failure verdict."""
    argv = [node_bin, companion, "status", job_id, "--json", "--cwd", str(sandbox_root)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        obj = json.loads(proc.stdout)
    except ValueError:
        return None
    job = obj.get("job") if isinstance(obj, dict) else None
    if not isinstance(job, dict):
        return None
    status = job.get("status")
    return status if isinstance(status, str) else None


def cmd_dispatch(args):
    config_path = resolve_particle_config(args.particle_config)
    refuse_if_under_a_temp_root(DURABLE_ROOT)

    # THE COMMITTED-RUN FREEZE IS CHECKED FIRST, before the identity comparison
    # below, and the order is not cosmetic: a committed fold has REWRITTEN the
    # particle_config, so this run's own `backup_sha1` no longer matches the
    # config's current bytes and the identity check would report a generic
    # mismatch for the one cause that has a precise answer. Re-dispatching a
    # slot after a commit is refused because it would let --fold's
    # harvest-keyed shortcut publish a sidecar describing a harvest set that no
    # longer exists.
    if sidecar_path(args.run_id).is_file():
        raise NameDiscoveryError(
            f"run {args.run_id} has already committed a fold (its sidecar exists), so it "
            f"is frozen. Continuing after a commit requires a NEW run id, which "
            f"re-derives every input identity.",
            offending="run_committed")
    manifest, manifest_sha1 = load_manifest()
    units = build_units(manifest)
    if not units:
        raise NameDiscoveryError(
            "manifest.json holds no non-empty block, so there is nothing to discover",
            offending="empty_manifest")

    config_sha1_now = sha1_bytes(config_path.read_bytes())
    fresh = build_run_manifest(
        run_id=args.run_id, config_path=config_path, config_sha1_now=config_sha1_now,
        manifest_sha1=manifest_sha1, units=units, passes=args.passes,
        model=args.model, effort=args.effort)

    rd = run_dir_for(args.run_id)
    manifest_file = rd / "run-manifest.json"
    if manifest_file.is_file():
        existing, _p = load_run_manifest(args.run_id)
        have = _identity_fields(existing)
        want = _identity_fields(fresh)
        if have != want:
            differing = sorted(k for k, v in want.items() if have.get(k) != v)
            raise NameDiscoveryError(
                f"run {args.run_id} already exists and its identity differs from what "
                f"the current inputs and flags produce (fields: {differing}). The run "
                f"manifest is write-once, so this is refused rather than overwritten: "
                f"harvests gathered for the old inputs must never be combined with new "
                f"ones. Use a new run id.",
                offending="run_identity_mismatch", differing=differing)
        run_manifest = existing
    else:
        write_json_atomic(manifest_file, fresh, exclusive=True)
        run_manifest = fresh

    # Resolve the companion ONLY once the run is admitted -- an unresolvable
    # companion must not leave a half-written run behind.
    node_bin = args.node
    companion = resolve_companion(node_bin)
    log(f"companion resolved: {companion}")

    text_by_unit = {u["unit_id"]: u["text"] for u in units}
    slots = []
    reused = 0
    for entry in run_manifest["units"]:
        unit_id = entry["unit_id"]
        for pass_index in range(1, run_manifest["passes"] + 1):
            expected = {
                "run_id": args.run_id, "unit": unit_id, "pass": pass_index,
                "source_sha1": entry["source_sha1"],
                "prompt_sha1": run_manifest["prompt_sha1"],
            }
            path = harvest_path(args.run_id, unit_id, pass_index)
            if path.is_file():
                try:
                    validate_harvest(path, expected, f"harvest {unit_id}.{pass_index}")
                except NameDiscoveryError as exc:
                    log(f"harvest {unit_id}.{pass_index} is present but unusable "
                        f"({exc}); it will be re-dispatched")
                else:
                    reused += 1
                    continue
            slots.append((unit_id, pass_index, expected, path))

    log(f"{len(run_manifest['units'])} unit(s) x {run_manifest['passes']} pass(es): "
        f"{reused} reused, {len(slots)} to dispatch, at most {args.max_parallel} in flight")

    failures = []
    dispatched = 0

    def one(slot):
        unit_id, pass_index, expected, path = slot
        label = f"{unit_id}.{pass_index}"
        forms = launch_one(
            companion=companion, node_bin=node_bin, unit_text=text_by_unit[unit_id],
            effort=run_manifest["effort"], model=run_manifest["model"],
            deadline_sec=args.deadline_sec, label=label)
        payload = dict(expected)
        payload["model"] = run_manifest["model"]
        payload["effort"] = run_manifest["effort"]
        payload["forms"] = forms
        write_json_atomic(path, payload)
        return len(forms)

    if slots:
        with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
            for slot, result in zip(slots, pool.map(_guard(one), slots)):
                label = f"{slot[0]}.{slot[1]}"
                if isinstance(result, NameDiscoveryError):
                    failures.append(label)
                    log(f"{label}: FAILED -- {result}")
                else:
                    dispatched += 1
                    log(f"{label}: {result} form(s)")

    summary = {
        "mode": "dispatch",
        "run_id": args.run_id,
        "units": len(run_manifest["units"]),
        "passes": run_manifest["passes"],
        "dispatched": dispatched,
        "reused": reused,
        "failed": len(failures),
        "failed_slots": sorted(failures),
        "harvest_dir": str(rd / "harvest"),
    }
    emit(summary)
    if failures:
        log(f"{len(failures)} slot(s) failed. Re-run --dispatch with the SAME run id to "
            f"complete only the gaps; --fold refuses an incomplete harvest.")
        return 1
    return 0


def _guard(fn):
    """Turn a raised NameDiscoveryError into a returned one, so one failing slot
    is counted rather than cancelling the pool."""
    def inner(item):
        try:
            return fn(item)
        except NameDiscoveryError as exc:
            return exc
        except Exception as exc:  # noqa: BLE001 - a slot failure is never fatal
            return NameDiscoveryError(f"unexpected error: {exc!r}")
    return inner


# ---------------------------------------------------------------------------
# --fold
# ---------------------------------------------------------------------------

def honorific_groups(union, prefixes):
    """Deterministic honorific normalisation, for METRICS and PROVENANCE ONLY.

    The measured problem it addresses: one pass in eight returned every line
    stripped of its leading title, and unnormalised that inflated a naive union
    count from 108 to 148 -- duplicates presenting as coverage.

    It NEVER removes a member. Deciding that a bare form and a title-bearing one
    denote the same entity is an identity call, which THE IRON RULE reserves for
    the glossary adjudicator and forbids to a script; and both forms
    legitimately occur in the source, so both belong in the inventory.
    Cross-entry harmonisation of such a pair is issue #823's subject, not this
    script's."""
    if not prefixes:
        return []
    ordered = sorted(prefixes, key=len, reverse=True)
    by_key = {}
    for form in union:
        key = form
        for prefix in ordered:
            # A BOUNDARY is required after the prefix, not a bare startswith:
            # with the prefix "רבי", the unrelated surname "רבינוביץ" starts
            # with it and would be grouped with a genuinely stripped form,
            # reporting an honorific collapse where no title was removed.
            # Membership is unaffected either way (nothing is ever dropped),
            # but a wrong provenance metric is still a wrong measurement.
            if form.startswith(prefix) and _has_boundary_after(form, len(prefix)):
                stripped = form[len(prefix):].strip()
                if stripped:
                    key = stripped
                break
        by_key.setdefault(key, []).append(form)
    return [
        {"stripped": key, "forms": sorted(forms)}
        for key, forms in sorted(by_key.items()) if len(forms) > 1
    ]


def _has_boundary_after(form, index):
    """True when `form[index:]` starts at a token boundary -- i.e. the prefix is
    a standalone word rather than the head of a longer one. The space, the tab,
    NBSP, the Hebrew maqaf and the ordinary hyphen count -- and only those five,
    not whitespace in general (U+2003 and its siblings are absent) -- the
    joiners a stripped
    title can leave behind. This is deliberately NOT the #238/#241 connector set
    (language_smoke_report.py's NAME_CONNECTORS): geresh, gershayim and the ASCII
    apostrophe twins are absent because a title is not spelled onto its name with
    those, and whitespace is present although that fold treats it as a
    separator."""
    if index >= len(form):
        return False
    return form[index] in " \t\u00a0\u05be-"


def harvest_set_sha1(records):
    """The identity of the WHOLE harvest: sorted (unit, pass, sha1(sorted forms))
    triples. This, not the config's own hash, is what the committed-state
    shortcut keys off -- a config that happens to match says nothing about
    whether the harvest behind it is still the one on disk."""
    # json.dumps over the LIST, never " ".join: a space is legal inside a name,
    # so the joined string is not an injective encoding of the form set --
    # ["A", "B"] and ["A B"] collapse to the same bytes. Two genuinely different
    # harvests would then share a harvest_set_sha1, and the committed-state
    # shortcut would republish the FIRST one's inventory and provenance for the
    # second, reporting success. Same reason for inventory_sha1 below.
    triples = sorted(
        (r["unit"], r["pass"],
         sha1_text(json.dumps(sorted(set(r["forms"])), ensure_ascii=False)))
        for r in records
    )
    return sha1_text(json.dumps(triples, ensure_ascii=False))


def rewrite_inventory(config_path, survivors):
    """Replace `name_inventory` outright -- NOT merged with a prior inventory, so
    a re-run's result never depends on run order -- carrying the other four keys
    through from the parsed document. The rendered bytes are parsed back through
    `load_particle_config` BEFORE the replace, so a document that would no longer
    load is never published."""
    doc, _raw = read_json_strict(config_path, "the resolved particle_config")
    doc["name_inventory"] = sorted(survivors)
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # PER-INVOCATION temp names. The project lock already serialises folds, but
    # a shared `<name>.tmp` is a second writer's target for free, and an
    # os.replace() of a file another process has rewritten publishes ITS bytes
    # under THIS run's reported hash -- a false sidecar with no failure anywhere.
    nonce = "%d.%s" % (os.getpid(), os.urandom(4).hex())
    probe = config_path.with_name(f"{config_path.name}.probe.{nonce}.tmp")
    try:
        probe.write_text(body, encoding="utf-8")
        load_particle_config(probe)
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    tmp = config_path.with_name(f"{config_path.name}.{nonce}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(str(tmp), str(config_path))
    finally:
        # Unconditional, not guarded by exists(): a stat can itself raise on a
        # filesystem fault and would then mask the write error this finally is
        # unwinding from.
        try:
            tmp.unlink()
        except OSError:
            pass
    return sha1_text(body)


def cmd_fold(args):
    config_path = resolve_particle_config(args.particle_config)
    if ".local." not in config_path.name:
        raise NameDiscoveryError(
            f"refusing to write discovery output into {config_path.name}: it is not a "
            f"project-local override. Discovery output is PROJECT DATA and a plugin "
            f"upgrade overwrites the shipped presets, so it must land in a "
            f"<code>.local.json the project owns -- the convention SKILL.md's "
            f"uncased-script walkthrough already documents. Point "
            f"source.language.particle_config at one and re-run.",
            offending="particle_config_not_local", particle_config=config_path.name)

    run_manifest, _mpath = load_run_manifest(args.run_id)

    # THE TARGET IS PART OF THE RUN'S IDENTITY, and comparing the tokenizer
    # semantics is not the same check: any other .local.json with the same
    # PARTICLES / STOPWORDS / has_elision / ELISION_RE passes that one, so a
    # mistyped --particle-config would publish this run's inventory into a
    # config it was never dispatched against, rewrite that file, and leave the
    # intended one untouched while reporting success. Refused here, before a
    # single harvest is read and before any backup or config byte is written.
    if config_path.name != run_manifest["particle_config"]:
        raise NameDiscoveryError(
            f"this run was dispatched against {run_manifest['particle_config']!r}, "
            f"but --particle-config names {config_path.name!r}. A fold publishes "
            f"into the file the run was dispatched for; folding into another one "
            f"would rewrite a config this run never measured. Pass the run's own "
            f"config, or dispatch a new run id against this one.",
            offending="particle_config_mismatch",
            expected=run_manifest["particle_config"], got=config_path.name)

    # Input re-verification BEFORE anything else: harvests gathered for older
    # inputs must never be combined with newer text or a newer tokenizer.
    manifest, manifest_sha1 = load_manifest()
    if manifest_sha1 != run_manifest["manifest_sha1"]:
        raise NameDiscoveryError(
            f"manifest.json has changed since this run was dispatched "
            f"(sha1 {manifest_sha1} vs the run manifest's "
            f"{run_manifest['manifest_sha1']}). Folding would combine harvests "
            f"gathered for the old text with a new corpus, silently dropping names "
            f"the new text introduced. Dispatch a new run id.",
            offending="manifest_changed")
    if unicodedata.unidata_version != run_manifest["unidata_version"]:
        raise NameDiscoveryError(
            f"this interpreter's Unicode database is "
            f"{unicodedata.unidata_version}, but the run was dispatched under "
            f"{run_manifest['unidata_version']}. The census builds its token class "
            f"from that database, so folding now could keep or drop a form purely "
            f"because a code point was reassigned. Reproducibility here is "
            f"SAME-RUNTIME by construction. Dispatch a new run id.",
            offending="unidata_version_changed")
    # PROMPT DRIFT IS A STALE RUN, on this side too. `prompt_sha1` is in
    # _identity_fields, so --dispatch already refuses to resume across a
    # PROMPT_TEMPLATE change -- but the fold re-verified every other input and
    # not this one, so a plugin upgrade that reworded the prompt left --fold
    # willing to publish a harvest gathered under the old question while
    # --dispatch refused to add to it. The harvest's own prompt_sha1 binding is
    # to the RUN MANIFEST, which says nothing about the current template.
    if prompt_identity_sha1() != run_manifest["prompt_sha1"]:
        raise NameDiscoveryError(
            "PROMPT_TEMPLATE has changed since this run was dispatched. The harvest "
            "answers a different question than this version of the script asks, and "
            "--dispatch already refuses to extend such a run -- folding it would "
            "publish an inventory the current prompt never produced. Dispatch a new "
            "run id.",
            offending="prompt_changed")
    if census_contract_sha1() != run_manifest["census_contract_sha1"]:
        raise NameDiscoveryError(
            "language_smoke_report.py has changed since this run was dispatched, "
            "and it owns the occurrence census -- the rule that decides which "
            "harvested forms survive. Folding now would score this harvest under a "
            "different survival rule than the one it was gathered for. Dispatch a "
            "new run id.",
            offending="census_contract_changed")
    current_semantics = config_semantics_sha1(config_path)
    if current_semantics != run_manifest["config_semantics_sha1"]:
        raise NameDiscoveryError(
            "the particle_config's tokenizer semantics have changed since this run "
            "was dispatched (PARTICLES / STOPWORDS / has_elision / ELISION_RE). The "
            "occurrence census depends on them, so a fold now would score the "
            "harvest against a different fold than the one it was gathered under. "
            "Dispatch a new run id.",
            offending="config_semantics_changed")

    # THE EXPECTED UNIT SET IS REBUILT, NEVER READ BACK. `manifest_sha1` above
    # proves the manifest's BYTES are unchanged; it says nothing about the
    # `units` array, which lives in this OTHER file and is exactly what the
    # completeness check consults. Validating each persisted unit_id was not
    # enough: DELETING an otherwise-valid entry leaves every survivor legal, so
    # its harvests are simply no longer expected, the loop iterates a shorter
    # set, and the fold exits 0 having published an inventory missing every name
    # unique to that unit -- the exact thin-inventory false green the
    # completeness gate exists to prevent, with nothing anywhere going red.
    #
    # So the fold derives the units from the manifest it just verified and
    # requires EXACT equality with what was persisted, ordering included. That
    # removes the trust rather than checking it: no allowlist over a value the
    # file supplies can catch an entry that is absent.
    expected_units = unit_descriptors(build_units(manifest))
    if run_manifest["units"] != expected_units:
        raise NameDiscoveryError(
            f"the run manifest's unit set does not match what the current "
            f"manifest.json produces ({len(run_manifest['units']) if isinstance(run_manifest['units'], list) else 'a non-list'} "
            f"persisted vs {len(expected_units)} derived). The run manifest is "
            f"write-once; a unit set that differs from the source it was built "
            f"from cannot be folded, because a missing entry silently narrows "
            f"the completeness check and publishes a thin inventory. Dispatch a "
            f"new run id.",
            offending="run_manifest_units_mismatch")
    # `type(...) is int`, not isinstance: bool subclasses int, so a tampered
    # `"passes": true` would pass an isinstance check and silently reduce the
    # fold to pass 1 -- a THIN inventory committed with every gate green.
    if type(run_manifest["passes"]) is not int or run_manifest["passes"] < 1:
        raise NameDiscoveryError(
            "the run manifest's `passes` is not a positive integer",
            offending="run_manifest_malformed")
    # Completeness: the expected set is the DERIVED one, never a directory
    # listing, so a stray extra file cannot stand in for a missing one.
    records = []
    for entry in expected_units:
        for pass_index in range(1, run_manifest["passes"] + 1):
            path = harvest_path(args.run_id, entry["unit_id"], pass_index)
            describe = f"harvest {entry['unit_id']}.{pass_index}"
            if not path.is_file():
                raise NameDiscoveryError(
                    f"{describe} is missing at {path}. A partial harvest never produces "
                    f"an inventory: on a caseless source a thin inventory yields a "
                    f"structurally impoverished canon that every downstream gate "
                    f"accepts. Re-run --dispatch with this run id to complete the gaps.",
                    offending="harvest_incomplete", path=str(path))
            # model and effort are in HARVEST_KEYS, so every harvest carries
            # them -- but carrying a value is not being bound to one. Comparing
            # them here is what stops the sidecar attesting the run manifest's
            # model/effort over forms some other run's job produced.
            records.append(validate_harvest(path, {
                "run_id": args.run_id, "unit": entry["unit_id"], "pass": pass_index,
                "source_sha1": entry["source_sha1"],
                "prompt_sha1": run_manifest["prompt_sha1"],
                "model": run_manifest["model"], "effort": run_manifest["effort"],
            }, describe))

    set_sha1 = harvest_set_sha1(records)
    prefixes = sorted(set(args.honorific_prefix))

    # The committed-state shortcut, keyed on EVERY input the sidecar's contents
    # depend on -- the harvest, the config, and the honorific prefixes. Keying
    # on the config alone would republish a stale sidecar after a slot had been
    # re-dispatched; keying on harvest and config alone silently ignored a
    # changed --honorific-prefix, which is a documented input to the dedup
    # metrics this file records, and left the sidecar describing the PREVIOUS
    # invocation while reporting success. A shortcut whose key is narrower than
    # its output is a false green by construction, so the rule is the key
    # covers the output, and anything else falls through to a real fold.
    side = sidecar_path(args.run_id)
    if side.is_file() and not args.dry_run:
        prior, _raw = read_json_strict(side, "the provenance sidecar")
        config_now = sha1_bytes(config_path.read_bytes())
        if (prior.get("harvest_set_sha1") == set_sha1
                and prior.get("particle_config_sha1_after") == config_now
                and prior.get("honorific_prefixes") == prefixes):
            prior["republished_utc"] = utc_now()
            write_json_atomic(side, prior)
            emit({
                "mode": "fold", "run_id": args.run_id, "committed": True,
                "rewrote_config": False,
                "surviving": prior.get("surviving"),
                "inventory_sha1": prior.get("inventory_sha1"),
                "particle_config": config_path.name,
            })
            log("this run is already committed for exactly this harvest; the sidecar "
                "was republished and the config left untouched")
            return 0

    union = sorted({f for r in records for f in r["forms"]})
    per_pass = {f"{r['unit']}.{r['pass']}": len(set(r["forms"])) for r in records}
    groups = honorific_groups(union, prefixes)

    # THE occurrence census -- the shipped, form-attributing one. name_inventory
    # must be a frozenset: the lru_cached trie needs a hashable argument.
    lang = load_particle_config(config_path)
    lang["name_inventory"] = frozenset(union)
    pieces = _inventory_scan_pieces(manifest)
    survivors = sorted(inventory_forms_seen(pieces, lang))

    log(f"{len(records)} harvest(s) -> union {len(union)} -> surviving {len(survivors)} "
        f"(dropped {len(union) - len(survivors)}) over {len(pieces)} block(s)")
    if groups:
        log(f"honorific grouping: {len(groups)} group(s) collapse under "
            f"{prefixes!r}; NO member is removed")

    if not survivors:
        emit({
            "mode": "fold", "run_id": args.run_id, "committed": False,
            "union_size": len(union), "surviving": 0,
            "particle_config": config_path.name,
        })
        log("ZERO forms survived the occurrence filter. Nothing has been written: a "
            "caseless book with an empty inventory is exactly the degradation this "
            "pass exists to end, so this is a gate failure, not a result. Check that "
            "the model was shown the right language's text and that the harvest is "
            "not empty.")
        return 1

    if args.dry_run:
        emit({
            "mode": "fold", "run_id": args.run_id, "dry_run": True, "committed": False,
            "union_size": len(union), "surviving": len(survivors),
            "dropped": len(union) - len(survivors),
            "honorific_groups": len(groups),
            "particle_config": config_path.name,
        })
        log("--dry-run: nothing written -- no backup, no config, no sidecar. Running "
            "for real rewrites the particle_config and moves particle_config_hash, "
            "which re-stales every converged segment of this book.")
        return 0

    # ONE immutable backup, created with O_EXCL and never re-copied. The naive
    # "copy the current config each time" LOSES the pre-discovery bytes: after a
    # crash between the config write and the sidecar, the "current" config is
    # already the new one. `backup_sha1` was computed at run-manifest creation,
    # before any dispatch, so it is exactly what the backup must equal.
    bpath = backup_path(args.run_id)
    if bpath.is_file():
        have = sha1_bytes(bpath.read_bytes())
        if have != run_manifest["backup_sha1"]:
            raise NameDiscoveryError(
                f"the pre-discovery backup at {bpath} does not match the run "
                f"manifest's backup_sha1 ({have} vs {run_manifest['backup_sha1']}); it "
                f"is the only copy of the bytes this fold replaces, so this is refused "
                f"rather than repaired",
                offending="backup_mismatch", path=str(bpath))
        log(f"pre-discovery backup already present at {bpath}; not re-copied")
    else:
        current = config_path.read_bytes()
        if sha1_bytes(current) != run_manifest["backup_sha1"]:
            raise NameDiscoveryError(
                "the particle_config's bytes differ from the run manifest's "
                "backup_sha1, so the config was edited between dispatch and this "
                "fold. Its four tokenizer keys are unchanged (that is checked above), "
                "but the name_inventory this fold is about to replace is not the one "
                "the run recorded. Dispatch a new run id.",
                offending="config_changed_since_dispatch")
        bpath.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(bpath), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(current)

    # config -> sidecar, in that order, so the sidecar never attests an
    # unfinished commit. A crash between them leaves the config committed, no
    # sidecar, and the immutable backup intact; the re-run completes publication.
    before_sha1 = run_manifest["backup_sha1"]
    after_sha1 = rewrite_inventory(config_path, survivors)
    sidecar = {
        "run_id": args.run_id,
        "script_version": SCRIPT_VERSION,
        "generated_utc": utc_now(),
        "python_version": run_manifest["python_version"],
        "unidata_version": run_manifest["unidata_version"],
        "particle_config": config_path.name,
        "particle_config_sha1_before": before_sha1,
        "particle_config_sha1_after": after_sha1,
        "manifest_sha1": run_manifest["manifest_sha1"],
        "config_semantics_sha1": run_manifest["config_semantics_sha1"],
        "census_contract_sha1": run_manifest["census_contract_sha1"],
        "harvest_set_sha1": set_sha1,
        "model": run_manifest["model"],
        "effort": run_manifest["effort"],
        "passes": run_manifest["passes"],
        "units": len(run_manifest["units"]),
        "harvested_total": sum(len(r["forms"]) for r in records),
        "union_size": len(union),
        "surviving": len(survivors),
        "dropped": len(union) - len(survivors),
        "per_pass_counts": per_pass,
        "honorific_prefixes": prefixes,
        "honorific_groups": groups,
        "inventory_sha1": sha1_text(json.dumps(survivors, ensure_ascii=False)),
    }
    write_json_atomic(side, sidecar)

    emit({
        "mode": "fold", "run_id": args.run_id, "committed": True,
        "rewrote_config": True,
        "union_size": len(union), "surviving": len(survivors),
        "dropped": len(union) - len(survivors),
        "honorific_groups": len(groups),
        "particle_config": config_path.name,
        "particle_config_sha1_before": before_sha1,
        "particle_config_sha1_after": after_sha1,
        "inventory_sha1": sidecar["inventory_sha1"],
        "sidecar": str(side),
    })
    log(f"wrote {len(survivors)} form(s) into {config_path}. particle_config_hash has "
        f"MOVED: every converged segment of this book is now stale. The pre-discovery "
        f"config is preserved verbatim at {bpath}.")
    return 0


# ---------------------------------------------------------------------------
# --verify-inventory
#
# The property enforced is deliberately NOT "discovery ran". A hand-built
# name_inventory is the documented pre-#286 route and a project satisfying the
# pipeline that way is CORRECT. The property is "the resolved particle_config
# carries a non-empty name_inventory" -- ONE input, so the thing checked and the
# thing bootstrap_names.py loads are the same bytes, resolved by the same rule.
#
# W3 runs this BEFORE bootstrap_names.py, which has no discovery prerequisite
# and exits 0 over whatever it finds.
# ---------------------------------------------------------------------------

def _run_currency(run_id, config_path, manifest, manifest_sha1, expected_units):
    """Why this run cannot be resumed against the CURRENT inputs, or None when
    it can. EXACTLY the bindings cmd_fold enforces, evaluated without writing
    anything -- deliberately the same list, because a resume plan that judged a
    run by a shorter list would recommend a run the fold then refuses."""
    try:
        rm, _p = load_run_manifest(run_id)
    except NameDiscoveryError as exc:
        return f"its run manifest is unreadable ({exc})"
    if rm.get("particle_config") != config_path.name:
        return (f"it was dispatched against {rm.get('particle_config')!r}, not "
                f"{config_path.name!r}")
    if rm.get("manifest_sha1") != manifest_sha1:
        return "manifest.json has changed since it was dispatched"
    if rm.get("unidata_version") != unicodedata.unidata_version:
        return "this interpreter's Unicode database differs from its own"
    if rm.get("census_contract_sha1") != census_contract_sha1():
        return "language_smoke_report.py has changed since it was dispatched"
    if rm.get("prompt_sha1") != prompt_identity_sha1():
        return "PROMPT_TEMPLATE has changed since it was dispatched"
    if rm.get("config_semantics_sha1") != config_semantics_sha1(config_path):
        return "the particle_config's tokenizer semantics have changed"
    if rm.get("units") != expected_units:
        return "its unit set does not match what the current manifest.json produces"
    if type(rm.get("passes")) is not int or rm["passes"] < 1:
        return "its `passes` is not a positive integer"
    return None


def cmd_resume_plan(args):
    """WHICH run, if any, W3 re-enters -- and with WHICH command.

    This mode exists because the alternative is a branch table written in prose
    for a session to execute by reading a directory listing, and that table was
    wrong three times in a row: it has to order committed-vs-incomplete,
    current-vs-stale and single-vs-several, and a normal interruption produces
    combinations of those (a committed run left stale by a manifest edit sitting
    beside the incomplete replacement dispatched to succeed it) that any short
    rule gets backwards. Every fact the decision needs already lives here, in
    the same checks --fold enforces, so the decision is computed rather than
    described.

    Emits one JSON line with `action`:
      fold               -- <run_id> is current and publication has BEGUN
                            (a sidecar, or the backup --fold writes before the
                            config): re-publish, or finish, with --fold ALONE.
      dispatch_then_fold -- <run_id> is current and nothing has been published
                            for it yet: complete its slots, then fold.
      fresh              -- nothing resumable; mint a new run id.
      ambiguous          -- more than one candidate in the chosen class; exit 1,
                            because picking one by timestamp is an identity call
                            this script does not get to make.
    A stale run is never chosen and never blocks a current one."""
    config_path = resolve_particle_config(args.particle_config)
    manifest, manifest_sha1 = load_manifest()
    expected_units = unit_descriptors(build_units(manifest))

    runs = []
    if RUNS_DIR.is_dir():
        for child in sorted(RUNS_DIR.iterdir()):
            if not child.is_dir() or not (child / "run-manifest.json").is_file():
                continue
            if validate_run_id(child.name) is not None:
                # A directory name that is not a legal run id was not written by
                # this script; report it rather than resuming into it.
                runs.append({"run_id": child.name, "committed": False,
                             "current": False,
                             "why": "its directory name is not a legal run id"})
                continue
            why = _run_currency(child.name, config_path, manifest, manifest_sha1,
                                expected_units)
            # PUBLICATION BEGUN, not "committed": the marker is the immutable
            # backup, which --fold writes with O_EXCL BEFORE it rewrites the
            # config and long before the sidecar. A crash in between leaves the
            # config rewritten and no sidecar, and --dispatch cannot be the
            # first step for that run -- it rebuilds the identity from the
            # REWRITTEN config and refuses on backup_sha1, so a
            # dispatch-then-fold chain would halt before the fold that finishes
            # publication. The sidecar is still consulted, so a committed run
            # whose backup was deleted by hand is not misread as unstarted.
            runs.append({
                "run_id": child.name,
                "committed": sidecar_path(child.name).is_file(),
                "publishing": backup_path(child.name).is_file(),
                "current": why is None,
                "why": why,
            })

    # AMBIGUITY IS TESTED OVER THE WHOLE CURRENT SET, before any class is
    # chosen. Preferring a committed run over a current unfinished one is
    # exactly the identity call this mode refuses to make: the unfinished run
    # may be the deliberate stochastic replacement whose result is meant to
    # supersede the committed one, and nothing on disk says which.
    current = [r for r in runs if r["current"]]
    if len(current) > 1:
        action, run_id = "ambiguous", None
    elif not current:
        action, run_id = "fresh", None
    elif current[0]["committed"] or current[0]["publishing"]:
        action, run_id = "fold", current[0]["run_id"]
    else:
        action, run_id = "dispatch_then_fold", current[0]["run_id"]

    emit({
        "mode": "resume-plan",
        "particle_config": config_path.name,
        "action": action,
        "run_id": run_id,
        "runs": runs,
    })
    if action == "ambiguous":
        log(f"{len(current)} runs are current against these inputs "
            f"({', '.join(r['run_id'] for r in current)}). Deciding which one is the "
            f"project's is an identity call, not a timestamp comparison -- ask the "
            f"operator which to keep and delete or rename the other, then re-run.")
        return 1
    if action == "fresh":
        log("no run is resumable against the current inputs" +
            (f" ({len(runs)} present, all stale or unreadable)" if runs else "")
            + "; dispatch a fresh run id.")
    else:
        log(f"resume {run_id} with --{action.replace('_', '-')}")
    return 0


def cmd_verify_inventory(args):
    config_path = resolve_particle_config(args.particle_config)
    lang = load_particle_config(config_path)
    inventory = lang["name_inventory"]
    emit({
        "mode": "verify-inventory",
        "particle_config": config_path.name,
        "path": str(config_path),
        "n_inventory": len(inventory),
        "ok": bool(inventory),
    })
    if not inventory:
        log(f"{config_path} carries an EMPTY name_inventory. On an uncased source that "
            f"is a structural zero: bootstrap_names.py's candidate path is gated on "
            f"is_upper_initial(), Hebrew/Yiddish/Arabic letters are category Lo, and "
            f"the inventory is the only bypass -- so W3 would reach the glossary planner "
            f"with no candidates, report no_new_candidates, and initialise an EMPTY "
            f"canon. HALT here. Either run the discovery pass (--dispatch then --fold) "
            f"or supply an inventory in this file by hand; do NOT proceed to "
            f"bootstrap_names.py.")
        return 1
    log(f"{config_path}: {len(inventory)} inventory form(s)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        description=(
            "LLM-based proper-name discovery for uncased sources (#286). Three "
            "modes: --dispatch fans out N independent cheap-tier codex passes per "
            "segment; --fold verifies every harvested form against the source with "
            "the shipped occurrence census and freezes the survivors into the "
            "project's own languages/<code>.local.json; --resume-plan reports which "
            "run W3 re-enters after an interruption and with which command; "
            "--verify-inventory is the one-input check W3 runs before "
            "bootstrap_names.py."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dispatch", action="store_true",
                      help="Fan out units x passes codex jobs and write the harvest.")
    mode.add_argument("--fold", action="store_true",
                      help="Verify, union, filter and freeze the harvest into the "
                           "resolved particle_config's name_inventory.")
    mode.add_argument("--resume-plan", action="store_true",
                      help="Read-only. Report WHICH run W3 re-enters and with which "
                           "command, computed from the same bindings --fold enforces.")
    mode.add_argument("--verify-inventory", action="store_true",
                      help="Exit 1 if the resolved particle_config's name_inventory is "
                           "empty. Reads nothing else.")
    p.add_argument("--particle-config", required=True, metavar="FILENAME",
                   help="Bare filename under ${durable_root}/languages/ -- the "
                        "profile's own source.language.particle_config LITERAL value, "
                        "never a path and never rebuilt from source.language.code.")
    p.add_argument("--run-id", metavar="ID", default=None,
                   help="Required by --dispatch and --fold. Names "
                        "runs/name-discovery/<ID>/.")
    p.add_argument("--passes", type=int, default=DEFAULT_PASSES, metavar="N",
                   help=f"Independent passes per unit (default {DEFAULT_PASSES}). The "
                        f"measured knob: repeats beat reasoning effort decisively.")
    p.add_argument("--effort", default=DEFAULT_EFFORT,
                   choices=("low", "medium", "high", "xhigh"),
                   help=f"codex reasoning effort (default {DEFAULT_EFFORT}). Measured: "
                        f"the tier buys recall only, precision is 92-96%% at every "
                        f"tier, and a higher tier costs 7-11x the wall clock.")
    p.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL, metavar="N",
                   help=f"Concurrent codex jobs in flight (default "
                        f"{DEFAULT_MAX_PARALLEL}).")
    p.add_argument("--model", default=None, metavar="ID",
                   help="Optional codex model id. Part of the run identity.")
    p.add_argument("--node", default="node", metavar="EXE",
                   help="node executable used for codex-companion (default 'node').")
    p.add_argument("--deadline-sec", type=int, default=DEFAULT_DEADLINE_SEC, metavar="N",
                   help=f"Per-job poll deadline (default {DEFAULT_DEADLINE_SEC}).")
    p.add_argument("--honorific-prefix", action="append", default=[], metavar="P",
                   help="Repeatable. Carries glossary.name_discovery.honorific_prefixes "
                        "for --fold's dedup metrics. NEVER drops a form.")
    p.add_argument("--dry-run", action="store_true",
                   help="--fold only: compute and report, write nothing at all.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    if args.resume_plan:
        for flag, name in ((args.run_id, "--run-id"), (args.dry_run, "--dry-run")):
            if flag:
                fatal(f"{name} is meaningless with --resume-plan, which decides the "
                      f"run id rather than taking one", offending="usage")
        try:
            return cmd_resume_plan(args)
        except NameDiscoveryError as exc:
            fatal(str(exc), **exc.payload)

    if args.verify_inventory:
        for flag, name in ((args.run_id, "--run-id"), (args.dry_run, "--dry-run")):
            if flag:
                fatal(f"{name} is meaningless with --verify-inventory, which reads only "
                      f"the resolved particle_config", offending="usage")
        try:
            return cmd_verify_inventory(args)
        except NameDiscoveryError as exc:
            fatal(str(exc), **exc.payload)

    if not args.run_id:
        fatal("--run-id is required by --dispatch and --fold", offending="usage")
    err = validate_run_id(args.run_id)
    if err is not None:
        fatal(f"unsafe --run-id -- {err} No path has been constructed from it.",
              offending="run_id")
    if args.passes < 1 or args.passes > 16:
        fatal(f"--passes must be between 1 and 16; got {args.passes}", offending="usage")
    if args.max_parallel < 1 or args.max_parallel > 16:
        fatal(f"--max-parallel must be between 1 and 16; got {args.max_parallel}",
              offending="usage")
    if args.dispatch and args.dry_run:
        fatal("--dry-run applies to --fold only", offending="usage")

    with RunLock():
        try:
            return cmd_dispatch(args) if args.dispatch else cmd_fold(args)
        except NameDiscoveryError as exc:
            fatal(str(exc), **exc.payload)


if __name__ == "__main__":
    sys.exit(main())
