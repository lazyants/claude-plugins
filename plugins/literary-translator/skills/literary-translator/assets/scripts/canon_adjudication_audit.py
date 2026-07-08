#!/usr/bin/env python3
"""canon_adjudication_audit.py -- persisted, machine-checkable rollout gate
for EVERY human/codex-workflow name-adjudication decision over canon.json,
the literary-translator plugin's frozen, hash-versioned, cross-segment
name/realia glossary.

Ported from the real, proven `historiettes-t3/audit_human_adjudications.py`
(a rollout-blocking gate that recomputes its required-item worklist fresh
from live data on every run and cross-checks it against a persisted verdict
file -- ground truth for the methodology below; read it directly before
changing this one). Generalized to this plugin's actual data model
(`canon.json`'s `entries{}`/`review_queue[]`, verified against
`canon-entry.schema.json`/`canon-file.schema.json`/`canon-batch.schema.json`
-- no `entity_id`, no `aliases[]`, no edge/candidate/reconciliation/rejected
store, no two-pass agreement). See `references/canon-and-glossary.md` for
the authoritative narrative this script's behavior must match.

IRON RULE (plugin-wide, restated here because this script sits right on the
boundary): scripts SURFACE candidates and ENFORCE schemas; they NEVER make
an accuracy/identity call. "Are these two names the same entity?" is
codex/human-owned. This script ONLY (a) mechanically enumerates every item a
human or a schema-validated codex workflow must sign off on, and (b)
validates + cross-checks the recorded verdicts against canon.json's CURRENT,
freshly recomputed state. It never decides same/different, and it never
writes a verdict or a risk-acceptance itself -- `canon_adjudications.json`
(the persisted artifact this script reads) is authored by a human reviewer
or a schema-validated codex workflow, NEVER by this script and NEVER
silently by Claude.

TWO DISTINCT ACTIONS (may be combined; passing neither is a usage error)
--------------------------------------------------------------------------
  --init   Write the empty `canon_adjudications.json` template IF IT DOES
           NOT EXIST YET (no-op, reporting what's already on file, unless
           --force is also given -- DESTRUCTIVE: resets it to an empty
           template).
  --check  Independently RECOMPUTE the four required-item categories fresh
           from canon.json and cross-check them against
           canon_adjudications.json. `--init --check` together run init
           first, then check, and print ONLY the check summary (never two
           stdout lines).

THE FOUR REQUIRED-ITEM CATEGORIES (recomputed fresh every --check run)
--------------------------------------------------------------------------
Entities are DERIVED, mechanically, never an accuracy call: an entity is a
normalized `canonical_target_form`. Normalization N(s) used for every
grouping/matching key throughout this script: NFC-normalize, casefold, and
collapse-and-strip internal whitespace (see `normalize_form`); display
fields always keep the original string. The scope filter for categories
1-3 is proper-name entries only -- `is_proper_name is True` AND
`basis != "not_a_name"`. Category 4 covers EVERY `review_queue[]` item,
scope filter or not.

  1. `duplicate_source_form` -- group proper-name entry RECORDS (not
     distinct field values -- two records sharing the exact same
     `source_form` field under two different `entries{}` map keys still
     count as two) by N(source_form). Any group of 2+ records is a required
     item, emitted REGARDLESS of whether the records' target forms agree --
     *the same source name (modulo case/whitespace/NFC) exists as 2+
     separate canon entries; same entity, or two people sharing a spelling?*
  2. `existing_merge` -- group proper-name entry records by
     N(canonical_target_form). Any group spanning 2+ DISTINCT normalized
     source forms is a required item -- *genuinely different source
     spellings resolve to one target name; is this collapse correct?*
     Requiring 2+ DISTINCT normalized (not raw) source forms is what keeps
     this disjoint from category 1: a pure surface-duplicate group (e.g.
     "Renaud"/"renaud" resolving to one target) is category 1 only.
  3. `candidate_missed_merge_pair` -- nodes are the distinct entities (one
     per normalized `canonical_target_form`, over proper-name entries).
     This plugin has no proposed-edge store at all, so EVERY unordered pair
     of distinct entities is a zero-edge candidate missed merge -- no
     similarity/confusability filter, which would silently drop the hardest
     case (two same entities under unrelated-looking names). A pair whose
     two entities share a common normalized source form is EXCLUDED here
     (it is already category 1's territory -- a normalized-source group
     spanning two targets). Canon is project-global, so there is exactly
     one cap scope, `"__canon__"`. If the pair count exceeds
     `--pair-review-cap` (default 40), this emits ONE cap-note requiring a
     FRESH `degenerate_cap_overrides["__canon__"]` risk-acceptance INSTEAD
     OF per-pair items (never both, never neither) -- see "CAP-OVERRIDE
     FRESHNESS" below. For any non-trivial canon (10 entities -> 45 pairs)
     the cap is exceeded by default, so the normal operating path is a
     single explicit, written risk-acceptance that pairwise identity was
     NOT exhaustively hand-verified -- exactly the residual risk this
     mechanism exists to make visible and accountable. Raise
     `--pair-review-cap` to demand exhaustive per-pair review instead.
  4. `review_queue_unresolved` -- plugin-native drainage gate, standing in
     for the source project's category 4. The source's category 4
     (correlated `rejected_different_entities` edges from a two-pass
     reconciliation) is DOCUMENTED UNPORTABLE: this plugin runs a single
     glossary pass with no reconciliation/edge/rejection store, so there is
     literally no such record to audit here (the same "OUT OF SCOPE, owned
     elsewhere" pattern the source project itself uses for categories it
     does not own). In its place, this category gates the plugin's genuine
     persisted human-deferral surface: `canon.json`'s `review_queue[]`.
     Semantics are deliberately NOT "confirmed_ok stays queued" (which
     would bless unresolved research indefinitely): every queued item is
     BLOCKING until it either (a) DRAINS -- promoted into `entries{}` or
     removed, so a fresh --check no longer enumerates it -- or (b) is
     explicitly risk-accepted via `review_queue_risk_overrides[key]`.
     Queued items never use the confirmed_ok/adverse mechanism.

KEY CONSTRUCTION -- stable, content-derived, never a raw delimiter-join
--------------------------------------------------------------------------
Every required-item key is `"{kind}::" + sha256(canonical_json(identity))`
(the FULL 64-hex digest, never truncated), where `identity` is a JSON value
built from normalized + sorted stable components (see each category above
and `build_item`/the `compute_cat*_items` functions for the exact shape
per kind) and `canonical_json` is `json.dumps(x, sort_keys=True,
ensure_ascii=False, separators=(",", ":"))`. This makes staleness automatic:
ANY change to an item's identity yields a new key, so the old adjudication
record becomes orphaned (informational, non-blocking, safe to prune) and
the changed item reports as a fresh missing_verdict -- the same "recompute
fresh; a fixed item disappears or reappears needing a fresh confirmed_ok"
guarantee the source project achieves via a separate identity-mismatch walk,
achieved here by the key itself. While building items, a `key ->
canonical_identity_json` map is maintained: the SAME key producing the SAME
identity twice is a true duplicate (a malformed canon emitted the identical
item twice) -- kept as the first occurrence, warned. The SAME key producing
a DIFFERENT identity is a genuine hash collision -- practically impossible
with sha256, but FATAL (exit 2) rather than a silent drop, because this gate
refuses to let a collision silently under-enforce a required item.

An `entries{}` map key that differs from its own entry's `source_form`
FIELD is a data-quality WARNING, never a crash: the field is authoritative
and used consistently for every grouping/key computation in this script
(canon-file.schema.json does not enforce map-key == source_form).

VERDICT CLASSES (adjudications{} records, categories 1-3 only)
--------------------------------------------------------------------------
  confirmed_ok -- the reviewer examined the item and the current canon.json
      state is correct as-is. The only class that satisfies the gate.
  adverse -- the reviewer found the current state IS wrong. Does NOT
      satisfy the gate -- BLOCKING. The underlying canon.json entry/entries
      must be corrected (a human/codex glossary-pass fix, then
      canon_validate.py) before this recomputes clean.
  Any other value (or a record missing/blank `reviewed_by`/`reason`) is
  `invalid_verdict_class` -- BLOCKING, distinct from a plain missing record.
`degenerate_cap_overrides["__canon__"]` and `review_queue_risk_overrides[key]`
are their own terminal class, conceptually "risk_accepted" -- not
confirmed_ok (nothing was actually verified) and not adverse (no correction
demanded).

CAP-OVERRIDE FRESHNESS
--------------------------------------------------------------------------
The `__canon__` cap override is bound to a FRESH cap-identity, never
permanent. On every --check where the pair count exceeds the cap, this
script computes `{entity_count, pair_count, cap, entity_set_fingerprint}`
(the fingerprint is sha256 of the canonical-JSON sorted list of every
distinct normalized entity), prints it (stderr, and folded into the
summary's `warnings[]` so it is copy-pasteable), and requires
`degenerate_cap_overrides["__canon__"]` to carry MATCHING values (plus
non-empty `risk_accepted_by`/`reason`) to satisfy category 3. A present
override whose recorded identity does not match the current one is STALE --
reported blocking, not satisfying -- closing the "sign once, bypass
forever" hole: any change to the entity set (add/remove/rename an entity)
changes the fingerprint and forces a re-sign.

CANON READING (never re-validates the full canon.json schema -- that is
canon_validate.py's job; this script assumes canon.json already passed it)
--------------------------------------------------------------------------
  - ABSENT canon.json -- `canon_present: false`, 0 required items, a
    visible stderr NOTE, exit 0. Canon *presence* is canon_validate.py's
    job (this script's own OUT-OF-SCOPE declaration, mirroring the source
    project's own pattern) -- never a silent green, since `canon_present`
    is explicit in the summary.
  - PRESENT but structurally malformed (unreadable JSON, top level not an
    object, `entries` not an object, `review_queue` not an array) -- FATAL
    exit 2, named stderr error, NO stdout JSON. This never false-greens a
    broken canon.
  - PRESENT-canon rows missing ENUMERATION-CRITICAL fields -- an entry
    missing a non-empty string `source_form`/`canonical_target_form`, a
    boolean `is_proper_name`, or a string `basis`; a queued item missing a
    non-empty string `source_form` -- are also FATAL exit 2 (skipping such
    a row could otherwise yield 0 items + exit 0, a false green). A queued
    item missing only `note` is NOT fatal -- `note` is display-only here --
    and stays enumerated as a blocking category-4 item.
  - `generation_hashes` is never re-validated here (canon_validate.py Pass
    2 owns it).

ADJUDICATIONS FILE VALIDATION BOUNDARY -- structural = fatal, per-record
content = blocking
--------------------------------------------------------------------------
  - FATAL exit 2, no stdout: unreadable JSON, top level not an object, any
    of the three sections (`adjudications`/`degenerate_cap_overrides`/
    `review_queue_risk_overrides`) present but not an object, or an
    individual record value not an object.
  - BLOCKING exit 1, WITH the summary: a required item's adjudication
    record present but with an invalid `verdict_class` or an empty/missing
    `reviewed_by`/`reason`; a missing record; a cap/queue override present
    but with an empty/missing `risk_accepted_by`/`reason`, or (cap override
    only) a stale identity.
Absent sections default to empty -- a not-yet-created override section is
not itself malformed. This script NEVER writes a verdict or an override;
`--init` only ever writes the empty template. Advisory record fields (`kind`,
`timestamp`, `_contract`) are intentionally NOT read or validated by this
reader -- the record key + load-bearing fields (verdict_class/reviewed_by/
reason, schema_version, cap-identity) are authoritative; the schema leaves
the advisory fields unconstrained to match. ORPHAN records (adjudication/
override keys matching no currently-required item) are reported for pruning
but their CONTENT is intentionally NOT gate-validated -- an orphan cannot
hide a current risk (required items are recomputed fresh from canon.json),
and full-file schema conformance of non-current records is the authoring
layer's job, deliberately not duplicated in this stdlib-only reader.

CLI
---
  --init                    write the empty template if absent
  --check                   recompute + cross-check
  --force                   with --init: overwrite an existing file
                            (DESTRUCTIVE)
  --canon-path PATH         override canon.json (default:
                            {durable_root}/canon.json)
  --adjudications-path PATH override the persisted adjudications artifact
                            (default: {durable_root}/canon_adjudications.json)
  --pair-review-cap N       category-3 pair-count cap (default: 40)
  --advisory                report every finding but never exit 1 for
                            blocking findings (WARN-first escape hatch;
                            never masks a genuine fatal exit 2)

OUTPUT / EXIT CODE
--------------------------------------------------------------------------
Exactly ONE JSON line to stdout -- `canon-adjudication-audit-summary.
schema.json`-shaped (a check summary, or the distinct --init-only summary
when --init is given without --check) -- all human-readable detail to
stderr. Exit 0 = gate clean (or `--init`-only success), 1 = blocking
findings (unless `--advisory`, which forces 0 while still reporting fully),
2 = fatal (bad paths, structurally malformed canon/adjudications,
enumeration-critical row malformation, a genuine key collision, or a usage
error when neither --init nor --check is given). `--advisory` never masks a
fatal exit 2.

STATUS: this is an OPT-IN rollout gate, not yet wired as a mandatory
pipeline step -- see SKILL.md's registration for the exact command and
when to run it.
"""

import argparse
import hashlib
import itertools
import json
import os
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TypeGuard

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
CANON_PATH = DURABLE_ROOT / "canon.json"
DEFAULT_ADJUDICATIONS_PATH = DURABLE_ROOT / "canon_adjudications.json"
DEFAULT_PAIR_REVIEW_CAP = 40
EXCLUSION_MATERIALIZATION_BUDGET = 1_000_000  # cap on excluded-pair materialization in
# compute_cat3_items -- a well-formed canon (each normalized source_form belongs to
# exactly 1 entity) never nears it; only a corrupt canon (one source_form shared by a
# huge number of distinct entities, itself a massive category-1 anomaly that already
# hard-blocks) can.

VALID_VERDICT_CLASSES = {"confirmed_ok", "adverse"}
CAP_SCOPE_TOKEN = "__canon__"

KIND_DUP_SOURCE = "duplicate_source_form"
KIND_MERGE = "existing_merge"
KIND_PAIR = "candidate_missed_merge_pair"
KIND_QUEUE = "review_queue_unresolved"
ALL_KINDS = (KIND_DUP_SOURCE, KIND_MERGE, KIND_PAIR, KIND_QUEUE)


class CanonAdjudicationAuditError(Exception):
    """Raised for any failure that must surface as a FATAL result (exit 2,
    no stdout JSON -- nothing can be mistaken for a schema-conforming
    summary).

    `offending`, when not None, is folded into the stderr failure output
    verbatim -- naming which rows/keys triggered the failure, so a caller
    never has to re-derive that from a bare error string.
    """

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def normalize_form(s: str) -> str:
    """NFC-normalize, casefold, and collapse-and-strip internal whitespace --
    the one normalization N(...) every grouping/matching key in this script
    is computed from. Display fields always keep the original string; only
    grouping/hashing ever sees the normalized form."""
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


def canonical_json(value: Any) -> str:
    """The one canonical JSON serialization every identity_struct is hashed
    through -- sorted keys, no ASCII-escaping, compact separators -- so the
    same identity always produces the same key regardless of dict insertion
    order or incidental serialization whitespace."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty_str(value: Any) -> bool:
    """True iff `value` is a non-blank string -- the one idiom every
    reviewed_by/reason/risk_accepted_by check in this script uses. Narrows
    on its own local parameter, so `.strip()` never has to be called on a
    second, un-narrowed `.get(...)` lookup of the same key."""
    return isinstance(value, str) and bool(value.strip())


def _int_eq(value: Any, expected: int) -> bool:
    """True iff `value` is an integer equal to `expected`. `type(value) is int` accepts a
    plain int while excluding bool (bool subclasses int, so True==1 / False==0 would else
    false-green a schema-invalid boolean cap-identity value). A finite integer-valued float
    is ALSO accepted: json.loads yields 1.0 for the JSON number 1.0, which the schema's
    "integer" type accepts (jsonschema treats 1.0 as a valid integer), so rejecting it would
    falsely report a schema-valid override stale. is_integer() is False for fractional floats
    AND for NaN/Infinity, so those stay rejected."""
    if type(value) is int:
        return value == expected
    if type(value) is float:
        return value.is_integer() and value == expected
    return False


def _reject_nonfinite_constant(constant: str) -> Any:
    """json.loads(parse_constant=...) hook. json.loads accepts the non-standard JSON
    constants NaN / Infinity / -Infinity by default; strict JSON forbids them and they
    would otherwise flow into numeric fields / the summary output. Raising here makes
    json.loads treat such a document as invalid so it fatals cleanly."""
    raise ValueError(f"non-standard JSON constant not allowed: {constant}")


def _read_json_file(path: Path, what: str, hint: Optional[str] = None) -> Any:
    suffix = f" -- {hint}" if hint else ""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # UnicodeDecodeError is a ValueError, not an OSError -- invalid UTF-8 bytes must
        # fatal here too, never escape as a raw traceback.
        raise CanonAdjudicationAuditError(f"could not read {what} at {path}: {e}{suffix}")
    try:
        parsed = json.loads(raw, parse_constant=_reject_nonfinite_constant)
    except ValueError as e:
        # Covers json.JSONDecodeError (malformed JSON, a ValueError subclass) AND the
        # ValueError raised by _reject_nonfinite_constant for NaN/Infinity/-Infinity.
        raise CanonAdjudicationAuditError(f"{what} at {path} is not valid JSON: {e}{suffix}")
    except RecursionError:
        # A deeply-nested-but-otherwise-valid document (e.g. thousands of nested arrays)
        # exhausts Python's call stack. RecursionError is a RuntimeError, NOT a ValueError,
        # so it needs its own handler here. Deliberately no {e} / repr(e) in this message --
        # near stack exhaustion, extra formatting work risks re-triggering RecursionError.
        raise CanonAdjudicationAuditError(
            f"{what} at {path} is too deeply nested to parse safely (exceeds the JSON "
            f"recursion limit){suffix}"
        )
    try:
        # allow_nan=False rejects non-finite floats that slip past parse_constant because
        # they are numeric literals, not named constants -- e.g. an over-large exponent like
        # 1e999 that json.loads parses to float('inf'). (parse_constant only catches the
        # NAMED NaN/Infinity/-Infinity tokens.)
        serialized = json.dumps(parsed, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise CanonAdjudicationAuditError(
            f"{what} at {path} contains a non-finite number (NaN/Infinity not allowed in "
            f"strict JSON): {e}{suffix}"
        )
    except RecursionError:
        # Defense-in-depth: json.loads above usually fails first at the same depth, but the
        # encoder's own recursion limit could theoretically differ. Same no-{e} discipline.
        raise CanonAdjudicationAuditError(
            f"{what} at {path} is too deeply nested to process safely (exceeds the JSON "
            f"recursion limit){suffix}"
        )
    try:
        # json.loads accepts \uXXXX escapes for lone surrogates (e.g. "\ud800") -- valid JSON
        # but NOT UTF-8 encodable. Left in place they crash downstream key construction /
        # stdout emission with an uncaught UnicodeEncodeError instead of this script's clean
        # fatal exit 2. Reject the whole payload here at the one decode boundary. ensure_ascii
        # =False forced the surrogate into `serialized` so .encode surfaces it.
        serialized.encode("utf-8")
    except UnicodeEncodeError as e:
        raise CanonAdjudicationAuditError(
            f"{what} at {path} contains a string that is not UTF-8 encodable (lone surrogate?): {e}{suffix}"
        )
    return parsed


# ---------------------------------------------------------------------------
# canon.json reading -- structural + enumeration-critical validation only.
# Full schema validation is canon_validate.py's job, never repeated here.
# ---------------------------------------------------------------------------


def _entry_enumeration_problems(entry: Any) -> list:
    if not isinstance(entry, dict):
        return ["entry is not a JSON object"]
    problems = []
    if not _nonempty_str(entry.get("source_form")):
        problems.append("missing/empty/whitespace-only string 'source_form'")
    if not _nonempty_str(entry.get("canonical_target_form")):
        problems.append("missing/empty/whitespace-only string 'canonical_target_form'")
    if not isinstance(entry.get("is_proper_name"), bool):
        problems.append("missing/non-boolean 'is_proper_name'")
    if not _nonempty_str(entry.get("basis")):
        problems.append("missing/empty/whitespace-only string 'basis'")
    return problems


def _queued_enumeration_problems(item: Any) -> list:
    if not isinstance(item, dict):
        return ["review_queue item is not a JSON object"]
    if not _nonempty_str(item.get("source_form")):
        return ["missing/empty/whitespace-only string 'source_form'"]
    return []


def read_canon(canon_path: Path, warnings: list) -> tuple:
    """Returns (canon_present, canon) where canon is None iff canon_present
    is False, or else {"entries": {...}, "review_queue": [...]}. Raises
    CanonAdjudicationAuditError (fatal) for a present-but-structurally- or
    enumeration-critically-malformed canon.json -- see this file's own
    module docstring, "CANON READING". Presence is detected via
    os.path.lexists (not Path.exists, which follows symlinks) so a dangling
    symlink at canon_path counts as present-but-not-a-regular-file (fatal),
    never as silently absent."""
    if not os.path.lexists(canon_path):
        return False, None
    if not canon_path.is_file():
        raise CanonAdjudicationAuditError(
            f"canon.json path exists but is not a regular file: {canon_path}"
        )

    doc = _read_json_file(canon_path, "canon.json", hint="run canon_validate.py first")
    if not isinstance(doc, dict):
        raise CanonAdjudicationAuditError(
            f"canon.json at {canon_path} is not a JSON object -- run canon_validate.py first"
        )
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise CanonAdjudicationAuditError(
            f"canon.json at {canon_path}: 'entries' is missing or not an object -- "
            f"run canon_validate.py first"
        )
    review_queue = doc.get("review_queue")
    if not isinstance(review_queue, list):
        raise CanonAdjudicationAuditError(
            f"canon.json at {canon_path}: 'review_queue' is missing or not an array -- "
            f"run canon_validate.py first"
        )

    fatal_rows = []
    for map_key, entry in entries.items():
        problems = _entry_enumeration_problems(entry)
        if problems:
            fatal_rows.append(f"entries[{map_key!r}]: {'; '.join(problems)}")
        elif entry.get("source_form") != map_key:
            warnings.append(
                f"entries[{map_key!r}]: map key does not match this entry's own "
                f"source_form field {entry['source_form']!r} -- the field is "
                f"authoritative and used consistently"
            )
    for i, item in enumerate(review_queue):
        problems = _queued_enumeration_problems(item)
        if problems:
            fatal_rows.append(f"review_queue[{i}]: {'; '.join(problems)}")

    if fatal_rows:
        raise CanonAdjudicationAuditError(
            f"canon.json at {canon_path} has enumeration-critical malformed row(s) -- "
            f"run canon_validate.py first:\n  " + "\n  ".join(fatal_rows),
            offending=fatal_rows,
        )

    return True, {"entries": entries, "review_queue": review_queue}


# ---------------------------------------------------------------------------
# Required-item construction (categories 1-4) -- see module docstring for
# the exact identity_struct shape per category.
# ---------------------------------------------------------------------------


def build_item(key_to_identity: dict, kind: str, identity: Any, warnings: list, **display) -> Optional[dict]:
    """Builds one required item keyed by `"{kind}::" + sha256(canonical_json
    (identity))`, registering it in `key_to_identity` for the cross-category
    defensive-dedup + collision discipline (see module docstring, "KEY
    CONSTRUCTION"). Returns None (and warns) for a true duplicate; raises
    CanonAdjudicationAuditError (fatal) for a genuine hash collision."""
    identity_json = canonical_json(identity)
    digest = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    key = f"{kind}::{digest}"

    existing = key_to_identity.get(key)
    if existing is not None:
        if existing == identity_json:
            warnings.append(
                f"duplicate required-item key {key!r} ({kind}) produced from distinct "
                f"canon rows -- keeping first occurrence"
            )
            return None
        raise CanonAdjudicationAuditError(
            f"hash collision: key {key!r} was produced for two different identities -- "
            f"this should be practically impossible with sha256, refusing to silently "
            f"drop either required item ({existing!r} vs {identity_json!r})",
            offending=[key, existing, identity_json],
        )
    key_to_identity[key] = identity_json
    return {"key": key, "kind": kind, **display}


def _proper_name_records(entries: dict) -> list:
    return [
        {
            "map_key": map_key,
            "source_form": entry["source_form"],
            "canonical_target_form": entry["canonical_target_form"],
        }
        for map_key, entry in entries.items()
        if entry.get("is_proper_name") is True and entry.get("basis") != "not_a_name"
    ]


def group_by_normalized(records: list, field: str) -> dict:
    """Bucket records by N(record[field]) -- the one grouping idiom every
    category derives from (source_form for category 1, canonical_target_form
    for categories 2-3)."""
    groups: dict = defaultdict(list)
    for r in records:
        groups[normalize_form(r[field])].append(r)
    return groups


def compute_cat1_items(records: list, key_to_identity: dict, warnings: list) -> list:
    """Category 1, duplicate_source_form: group proper-name entry records by
    N(source_form); any group of 2+ records is a required item, regardless
    of target agreement (counts RECORDS, not distinct field values)."""
    groups = group_by_normalized(records, "source_form")
    items = []
    for ns in sorted(groups):
        group = groups[ns]
        if len(group) < 2:
            continue
        record_list = sorted(
            [r["map_key"], r["source_form"], normalize_form(r["canonical_target_form"])]
            for r in group
        )
        item = build_item(
            key_to_identity, KIND_DUP_SOURCE,
            {"normalized_source": ns, "records": record_list}, warnings,
            normalized_source=ns, records=record_list,
        )
        if item is not None:
            items.append(item)
    return items


def compute_cat2_items(target_groups: dict, key_to_identity: dict, warnings: list) -> list:
    """Category 2, existing_merge: group proper-name entry records by
    N(canonical_target_form); any group spanning 2+ DISTINCT normalized
    source forms is a required item -- disjoint from category 1 by
    construction (a pure surface-duplicate group has only 1 distinct
    normalized source form)."""
    items = []
    for nt in sorted(target_groups):
        group = target_groups[nt]
        distinct_sources = sorted({normalize_form(r["source_form"]) for r in group})
        if len(distinct_sources) < 2:
            continue
        item = build_item(
            key_to_identity, KIND_MERGE,
            {"normalized_target": nt, "source_forms": distinct_sources}, warnings,
            normalized_target=nt, source_forms=distinct_sources,
        )
        if item is not None:
            items.append(item)
    return items


def _cap_note(entities: list, pair_count: int, pair_review_cap: int) -> dict:
    """Builds the FRESH cap-identity a degenerate_cap_overrides['__canon__']
    record must match -- shared by every compute_cat3_items exit path that
    reports an over-cap result (the exact-count path and the degenerate-
    scale upper-bound path both use the same 4-field shape)."""
    fingerprint = hashlib.sha256(canonical_json(entities).encode("utf-8")).hexdigest()
    return {
        "entity_count": len(entities),
        "pair_count": pair_count,
        "cap": pair_review_cap,
        "entity_set_fingerprint": fingerprint,
    }


def compute_cat3_items(target_groups: dict, pair_review_cap: int,
                        key_to_identity: dict, warnings: list) -> tuple:
    """Category 3, candidate_missed_merge_pair: every unordered pair of
    distinct entities (one per N(canonical_target_form) over proper-name
    entries), no similarity filter, EXCEPT a pair whose two entities share a
    common normalized source form (category 1's territory -- R2-1c).
    Returns (items, cap_note). cap_note is None when the pair count is <=
    pair_review_cap (per-pair items are emitted instead); otherwise cap_note
    carries the FRESH cap-identity a degenerate_cap_overrides["__canon__"]
    record must match, and NO per-pair items are emitted -- the two are
    mutually exclusive, never both, never neither.

    The pair count is computed ARITHMETICALLY (total pairs minus excluded
    pairs) before ever enumerating a single pair, so a large canon whose
    true pair count exceeds the cap never materializes an O(entity_count^2)
    list. The excluded-pairs SET itself is also never materialized past
    EXCLUSION_MATERIALIZATION_BUDGET -- see the budget-guard block below --
    since even that (normally tiny) set could otherwise blow up on a
    corrupt canon where one normalized source_form is shared by a huge
    number of distinct entities."""
    entities = sorted(target_groups.keys())
    entity_source_forms = {
        nt: sorted({normalize_form(r["source_form"]) for r in target_groups[nt]})
        for nt in entities
    }

    # Invert normalized-source-form -> the entities that carry it
    # (O(records) time, never O(entity_count^2)).
    entities_by_source: dict = defaultdict(set)
    for nt, sources in entity_source_forms.items():
        for ns in sources:
            entities_by_source[ns].add(nt)

    entity_count = len(entities)
    total_pairs = entity_count * (entity_count - 1) // 2

    # Upper bound on |excluded_pairs| -- and on the work materializing it
    # would take -- computed WITHOUT enumerating a single pair: the sum of
    # C(k,2) over every normalized source form shared by k>=2 entities. A
    # well-formed canon has this at 0; only a corrupt canon can approach the
    # budget, and category 1 already independently hard-blocks that same
    # corruption (a source form shared by many entities is many duplicate-
    # source-form records).
    sum_clique_edges = 0
    for shared_entities in entities_by_source.values():
        k = len(shared_entities)
        if k >= 2:
            sum_clique_edges += k * (k - 1) // 2

    if sum_clique_edges > EXCLUSION_MATERIALIZATION_BUDGET:
        warnings.append(
            f"category 3: canon has a degenerate-scale duplicate-source structure "
            f"(sum of per-source entity-pair counts {sum_clique_edges} exceeds the "
            f"{EXCLUSION_MATERIALIZATION_BUDGET} materialization budget); pair analysis "
            f"is reported over-cap using total unordered pair count {total_pairs} as an "
            f"upper bound. Category 1 independently flags the underlying duplicate "
            f"sources -- resolve those to restore exact category-3 analysis."
        )
        return [], _cap_note(entities, total_pairs, pair_review_cap)

    # Safe to materialize now -- bounded by the budget guard above. Pairs
    # sharing a common normalized source form are category 1's territory,
    # excluded here.
    excluded_pairs = set()
    for shared_entities in entities_by_source.values():
        if len(shared_entities) < 2:
            continue
        for pair in itertools.combinations(sorted(shared_entities), 2):
            excluded_pairs.add(frozenset(pair))

    pair_count = total_pairs - len(excluded_pairs)

    if pair_count > pair_review_cap:
        return [], _cap_note(entities, pair_count, pair_review_cap)

    items = []
    for nt_a, nt_b in itertools.combinations(entities, 2):
        if frozenset((nt_a, nt_b)) in excluded_pairs:
            continue
        identity = sorted([nt_a, nt_b])
        item = build_item(
            key_to_identity, KIND_PAIR, identity, warnings,
            entity_a=nt_a, entity_b=nt_b,
            entity_a_source_forms=entity_source_forms[nt_a],
            entity_b_source_forms=entity_source_forms[nt_b],
        )
        if item is not None:
            items.append(item)
    return items, None


def compute_cat4_items(review_queue: list, key_to_identity: dict, warnings: list) -> list:
    """Category 4, review_queue_unresolved: every review_queue[] item, keyed
    by the whole item as canonical JSON (so any content change re-blocks).
    Covers every queued item unconditionally -- no proper-name scope
    filter."""
    items = []
    for entry in review_queue:
        item = build_item(
            key_to_identity, KIND_QUEUE, entry, warnings,
            source_form=entry.get("source_form"), note=entry.get("note"),
        )
        if item is not None:
            items.append(item)
    return items


def compute_all_items(canon: dict, pair_review_cap: int, warnings: list) -> tuple:
    """Returns (cat1_items, cat2_items, cat3_items, cat4_items, cap_note),
    all recomputed fresh from `canon` -- never trusts anything cached from a
    previous run."""
    key_to_identity: dict = {}
    records = _proper_name_records(canon["entries"])
    target_groups = group_by_normalized(records, "canonical_target_form")

    cat1_items = compute_cat1_items(records, key_to_identity, warnings)
    cat2_items = compute_cat2_items(target_groups, key_to_identity, warnings)
    cat3_items, cap_note = compute_cat3_items(
        target_groups, pair_review_cap, key_to_identity, warnings
    )
    cat4_items = compute_cat4_items(canon["review_queue"], key_to_identity, warnings)

    return cat1_items, cat2_items, cat3_items, cat4_items, cap_note


# ---------------------------------------------------------------------------
# canon_adjudications.json reading -- structural malformation is fatal,
# per-record content problems are blocking (checked in the crosscheck
# functions below, not here).
# ---------------------------------------------------------------------------


def read_adjudications(path: Path, warnings: list) -> dict:
    """Returns {"adjudications": {...}, "degenerate_cap_overrides": {...},
    "review_queue_risk_overrides": {...}}, each section defaulted to {}
    when absent from an otherwise-valid file. A missing file is NOT fatal
    (treated as fully empty -- every required item will then report as
    missing_verdict); a present-but-structurally-malformed file (including a
    directory, or any other non-regular-file entry) raises
    CanonAdjudicationAuditError (fatal). Presence is detected via
    os.path.lexists (not Path.exists, which follows symlinks) so a dangling
    symlink at `path` counts as present-but-not-a-regular-file (fatal),
    never as silently absent."""
    empty = {"adjudications": {}, "degenerate_cap_overrides": {}, "review_queue_risk_overrides": {}}
    if not os.path.lexists(path):
        warnings.append(
            f"adjudications file not found at {path} -- treating as empty for this run "
            f"(every required item will report as missing_verdict; run --init first)"
        )
        return empty
    if not path.is_file():
        raise CanonAdjudicationAuditError(
            f"adjudications file path exists but is not a regular file: {path}"
        )

    doc = _read_json_file(path, "adjudications file")
    if not isinstance(doc, dict):
        raise CanonAdjudicationAuditError(f"adjudications file at {path} is not a JSON object")

    # Mirrors the schema's own {"schema_version": {"const": 1}} exactly -- OPTIONAL (no top-
    # level `required` in the schema), but IF present must be 1. The `in` guard (not
    # `.get() is not None`) also rejects an explicit `schema_version: null`, same as the
    # schema would.
    if "schema_version" in doc and not _int_eq(doc["schema_version"], 1):
        raise CanonAdjudicationAuditError(
            f"adjudications file at {path}: schema_version must be 1 (got {doc['schema_version']!r})"
        )

    result: dict = {}
    for section in ("adjudications", "degenerate_cap_overrides", "review_queue_risk_overrides"):
        value = doc.get(section, {})
        if not isinstance(value, dict):
            raise CanonAdjudicationAuditError(
                f"adjudications file at {path}: {section!r} is present but not an object"
            )
        for rec_key, rec_val in value.items():
            if not isinstance(rec_val, dict):
                raise CanonAdjudicationAuditError(
                    f"adjudications file at {path}: {section}[{rec_key!r}] is present but "
                    f"not an object"
                )
        result[section] = value
    return result


# ---------------------------------------------------------------------------
# Cross-checking required items against canon_adjudications.json
# ---------------------------------------------------------------------------


def _risk_accepted(override: Any) -> TypeGuard[dict]:
    """True iff `override` is a dict carrying a non-empty risk_accepted_by AND
    reason -- the shared terminal-risk-acceptance precondition for both a
    degenerate_cap_overrides record and a review_queue_risk_overrides record
    (a cap override additionally needs a matching fresh cap-identity). Typed
    as a TypeGuard so a True result narrows `override` to dict for the caller's
    follow-on field reads."""
    return (
        isinstance(override, dict)
        and _nonempty_str(override.get("risk_accepted_by"))
        and _nonempty_str(override.get("reason"))
    )


def crosscheck_regular_items(items: list, adjudications: dict) -> tuple:
    """Categories 1-3 only -- category 4 never uses the confirmed_ok/adverse
    mechanism (see crosscheck_queue). Returns (counts, by_kind, buckets)
    where buckets holds the actual items in each outcome, for the
    stderr report."""
    buckets: dict = {"confirmed_ok": [], "missing_verdict": [], "adverse": [], "invalid_verdict_class": []}
    by_kind = {k: 0 for k in ALL_KINDS}

    for it in items:
        by_kind[it["kind"]] += 1
        rec = adjudications.get(it["key"])
        if not isinstance(rec, dict):
            buckets["missing_verdict"].append(it)
            continue
        vc = rec.get("verdict_class")
        valid_fields = _nonempty_str(rec.get("reviewed_by")) and _nonempty_str(rec.get("reason"))
        if vc not in VALID_VERDICT_CLASSES or not valid_fields:
            buckets["invalid_verdict_class"].append(it)
        elif vc == "adverse":
            buckets["adverse"].append(it)
        else:  # vc == "confirmed_ok"
            buckets["confirmed_ok"].append(it)

    counts = {k: len(v) for k, v in buckets.items()}
    return counts, by_kind, buckets


def crosscheck_cap(cap_note: Optional[dict], cap_overrides: dict, warnings: list) -> tuple:
    """Returns (cap_notes, cap_overrides_ok, cap_overrides_missing). Also
    appends a copy-pasteable cap-note (when triggered) or an orphan note
    (when a stale override lingers with no current cap-note) to
    `warnings`."""
    override = cap_overrides.get(CAP_SCOPE_TOKEN)

    if cap_note is None:
        if isinstance(override, dict):
            warnings.append(
                f"degenerate_cap_overrides[{CAP_SCOPE_TOKEN!r}] no longer corresponds to "
                f"any current cap-note (category 3 is currently at or under the cap) -- "
                f"informational, non-blocking, safe to prune"
            )
        return 0, 0, 0

    warnings.append(
        f"CAP-NOTE ({KIND_PAIR}): {canonical_json(cap_note)} -- {cap_note['pair_count']} "
        f"pair(s) exceed --pair-review-cap {cap_note['cap']}; requires a FRESH "
        f"degenerate_cap_overrides[{CAP_SCOPE_TOKEN!r}] matching these exact values "
        f"(plus non-empty risk_accepted_by/reason) instead of per-pair review"
    )

    fresh = (
        _risk_accepted(override)
        and _int_eq(override.get("entity_count"), cap_note["entity_count"])
        and _int_eq(override.get("pair_count"), cap_note["pair_count"])
        and _int_eq(override.get("cap"), cap_note["cap"])
        and override.get("entity_set_fingerprint") == cap_note["entity_set_fingerprint"]
    )
    if fresh:
        return 1, 1, 0

    if isinstance(override, dict):
        warnings.append(
            f"degenerate_cap_overrides[{CAP_SCOPE_TOKEN!r}] is present but STALE or "
            f"incomplete -- does not match the fresh cap-identity above; category 3 stays "
            f"blocking until re-signed"
        )
    return 1, 0, 1


def crosscheck_queue(cat4_items: list, rq_overrides: dict) -> tuple:
    """Returns (review_queue_items, unaccepted_items) -- unaccepted_items is
    the actual list (for the stderr report), never just a count."""
    unaccepted = []
    for it in cat4_items:
        if not _risk_accepted(rq_overrides.get(it["key"])):
            unaccepted.append(it)
    return len(cat4_items), unaccepted


# ---------------------------------------------------------------------------
# --init: empty template + atomic write (tmp -> os.replace, matching
# canon_validate.py's own durable write pattern).
# ---------------------------------------------------------------------------


def build_template() -> dict:
    return {
        "schema_version": 1,
        "_contract": (
            "See canon_adjudication_audit.py's module docstring for the full contract. "
            "adjudications{}: keyed by '{kind}::' + sha256(canonical_json(identity)); each "
            "record needs verdict_class in {confirmed_ok, adverse} + non-empty reviewed_by "
            "+ non-empty reason + timestamp. degenerate_cap_overrides{}: keyed by "
            "'__canon__'; each record needs risk_accepted_by + reason + timestamp + "
            "entity_count/pair_count/cap/entity_set_fingerprint matching the FRESH values "
            "printed by --check's CAP-NOTE. review_queue_risk_overrides{}: keyed the same "
            "way as review_queue_unresolved items; each record needs risk_accepted_by + "
            "reason + timestamp. This file is authored by a human reviewer or a "
            "schema-validated codex workflow -- never by this script."
        ),
        "adjudications": {},
        "degenerate_cap_overrides": {},
        "review_queue_risk_overrides": {},
    }


def _atomic_write_json(path: Path, doc: dict) -> None:
    """Writes `doc` to `path` via tmp-write-then-os.replace(). Any OSError
    along the way (an unwritable parent, a path component that is not a
    directory, disk-full, ...) is folded into a CanonAdjudicationAuditError
    -- this must never escape as a raw traceback, since --init is expected
    to fail cleanly (fatal exit 2, no stdout JSON) rather than crash."""
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except OSError as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup only -- the write failure below is what matters
        raise CanonAdjudicationAuditError(f"could not write {path}: {e}")


def do_init(path: Path, force: bool) -> tuple:
    """Returns (created, (existing_adjudications, existing_cap_overrides,
    existing_review_queue_risk_overrides)). `created` is True iff a fresh
    empty template was actually written this run (absent, or --force reset
    an existing one). Never writes a verdict/override -- only ever the
    empty template."""
    existed = os.path.lexists(path)  # present-not-regular (dangling symlink / dir) counts
    # as existing -- never silently clobber without --force; the not-force branch's
    # read_adjudications() then fatals cleanly on it
    if existed and not force:
        doc = read_adjudications(path, warnings=[])
        counts = (
            len(doc["adjudications"]),
            len(doc["degenerate_cap_overrides"]),
            len(doc["review_queue_risk_overrides"]),
        )
        print(
            f"[init] {path} already exists -- leaving untouched (pass --force to reset "
            f"to an empty template). {counts[0]} adjudication(s), {counts[1]} cap "
            f"override(s), {counts[2]} review-queue risk override(s) on file.",
            file=sys.stderr,
        )
        return False, counts

    _atomic_write_json(path, build_template())
    verb = "reset (--force)" if existed else "wrote"
    print(f"[init] {verb} empty template to {path}", file=sys.stderr)
    return True, (0, 0, 0)


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def empty_totals() -> dict:
    return {
        "required_items": 0, "confirmed_ok": 0, "missing_verdict": 0, "adverse": 0,
        "invalid_verdict_class": 0, "cap_notes": 0, "cap_overrides_ok": 0,
        "cap_overrides_missing": 0, "review_queue_items": 0, "review_queue_unaccepted": 0,
        "orphaned_records": 0, "by_kind": {k: 0 for k in ALL_KINDS},
    }


def _print_item_list(label: str, items: list, file) -> None:
    print(f"\n-- {label} (first 20) --", file=file)
    for it in items[:20]:
        print(f"  {it['key']}  [{it['kind']}]", file=file)


def print_human_report(canon_path: Path, adjudications_path: Path, totals: dict,
                        buckets: dict, unaccepted_items: list, blocking_count: int,
                        gate_passed: bool, advisory: bool, warnings: list) -> None:
    print("=" * 70, file=sys.stderr)
    print("canon_adjudication_audit.py -- --check summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"canon_path: {canon_path}", file=sys.stderr)
    print(f"adjudications_path: {adjudications_path}", file=sys.stderr)
    print(f"required items (all 4 categories): {totals['required_items']}", file=sys.stderr)
    for kind in ALL_KINDS:
        print(f"  {kind}: {totals['by_kind'][kind]}", file=sys.stderr)
    print(f"  confirmed_ok:                 {totals['confirmed_ok']}", file=sys.stderr)
    print(f"  MISSING verdict (BLOCKING):   {totals['missing_verdict']}", file=sys.stderr)
    print(f"  adverse (BLOCKING):           {totals['adverse']}", file=sys.stderr)
    print(f"  invalid verdict (BLOCKING):   {totals['invalid_verdict_class']}", file=sys.stderr)
    print(
        f"cap notes: {totals['cap_notes']} (ok={totals['cap_overrides_ok']}, "
        f"MISSING={totals['cap_overrides_missing']})",
        file=sys.stderr,
    )
    print(
        f"review_queue items: {totals['review_queue_items']} "
        f"(UNACCEPTED/BLOCKING={totals['review_queue_unaccepted']})",
        file=sys.stderr,
    )
    print(f"orphaned records (informational, non-blocking): {totals['orphaned_records']}", file=sys.stderr)
    print(file=sys.stderr)
    tail = "  (--advisory: reporting only, never exits 1)" if advisory else ""
    print(f"BLOCKING findings: {blocking_count}  gate_passed={gate_passed}{tail}", file=sys.stderr)

    if buckets["missing_verdict"]:
        _print_item_list("missing verdict", buckets["missing_verdict"], sys.stderr)
    if buckets["adverse"]:
        _print_item_list("adverse (BLOCKING, canon.json must be corrected)", buckets["adverse"], sys.stderr)
    if buckets["invalid_verdict_class"]:
        _print_item_list("invalid verdict_class / empty reviewed_by or reason", buckets["invalid_verdict_class"], sys.stderr)
    if unaccepted_items:
        _print_item_list("review_queue items with no risk-acceptance", unaccepted_items, sys.stderr)

    if warnings:
        print(f"\n{len(warnings)} warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)


def _orphan_warning(records: dict, current_keys: set, record_label: str,
                    item_label: str, warnings: list) -> list:
    """Sorted list of persisted keys that no longer correspond to any
    currently-required/queued item (informational, non-blocking, safe to
    prune). Appends a first-10-truncated warning to `warnings` when the list
    is non-empty; returns the full list either way (its length feeds
    orphaned_records)."""
    orphaned = sorted(set(records) - current_keys)
    if orphaned:
        warnings.append(
            f"{len(orphaned)} {record_label} no longer correspond to any {item_label} "
            f"(informational, non-blocking, safe to prune): "
            f"{orphaned[:10]}{' ...' if len(orphaned) > 10 else ''}"
        )
    return orphaned


def run_check(canon_path: Path, adjudications_path: Path, pair_review_cap: int,
              advisory: bool, mode: str) -> tuple:
    """Returns (summary, exit_code). Prints the human-readable report to
    stderr as a side effect; the caller prints the returned summary as the
    one stdout JSON line."""
    warnings: list = []
    canon_present, canon = read_canon(canon_path, warnings)
    # Read/validate the adjudications file BEFORE the canon-absent early return: a bad
    # adjudications path (non-regular file, malformed JSON, wrong shape) is fatal exit 2 per
    # the OUTPUT contract regardless of canon presence; skipping it when canon is absent would
    # silently exit 0 for a bad invocation (codex round-13). An ABSENT adjudications file is
    # still treated as empty (non-fatal) as before.
    adjudications_doc = read_adjudications(adjudications_path, warnings)

    if not canon_present:
        print(
            f"[check] NOTE: canon.json not found at {canon_path} -- canon *presence* is "
            f"canon_validate.py's job, not this audit's; reporting 0 required items and "
            f"canon_present:false rather than a silent green.",
            file=sys.stderr,
        )
        summary = {
            "success": True, "mode": mode,
            "canon_path": str(canon_path), "adjudications_path": str(adjudications_path),
            "canon_present": False, "pair_review_cap": pair_review_cap, "advisory": advisory,
            "totals": empty_totals(), "blocking_count": 0, "gate_passed": True,
            "warnings": warnings, "generated_at": now_iso(),
        }
        return summary, 0

    cat1, cat2, cat3, cat4, cap_note = compute_all_items(canon, pair_review_cap, warnings)
    regular_items = cat1 + cat2 + cat3

    verdict_counts, by_kind, buckets = crosscheck_regular_items(regular_items, adjudications_doc["adjudications"])
    by_kind[KIND_QUEUE] = len(cat4)

    cap_notes_n, cap_overrides_ok, cap_overrides_missing = crosscheck_cap(
        cap_note, adjudications_doc["degenerate_cap_overrides"], warnings
    )
    review_queue_items, unaccepted_items = crosscheck_queue(
        cat4, adjudications_doc["review_queue_risk_overrides"]
    )

    current_regular_keys = {it["key"] for it in regular_items}
    orphaned_adjudications = _orphan_warning(
        adjudications_doc["adjudications"], current_regular_keys,
        "adjudication record(s)", "currently-required item", warnings,
    )

    current_cat4_keys = {it["key"] for it in cat4}
    orphaned_rq = _orphan_warning(
        adjudications_doc["review_queue_risk_overrides"], current_cat4_keys,
        "review_queue_risk_overrides record(s)", "currently-queued item", warnings,
    )

    cap_overrides = adjudications_doc["degenerate_cap_overrides"]
    # CAP_SCOPE_TOKEN is the only meaningful cap scope. It is an orphan when there is no
    # current cap-note; every other key is always an orphan (runtime ignores it).
    active_cap_keys = {CAP_SCOPE_TOKEN} if cap_note is not None else set()
    orphaned_cap_keys = sorted(set(cap_overrides) - active_cap_keys)
    # crosscheck_cap already warns for a stale CAP_SCOPE_TOKEN override; warn here only
    # for any OTHER orphan key so the __canon__ case is not double-warned.
    extra_orphan_cap_keys = [k for k in orphaned_cap_keys if k != CAP_SCOPE_TOKEN]
    if extra_orphan_cap_keys:
        warnings.append(
            f"degenerate_cap_overrides has {len(extra_orphan_cap_keys)} record(s) under "
            f"non-{CAP_SCOPE_TOKEN!r} key(s) {extra_orphan_cap_keys} -- canon has exactly one "
            f"cap scope ({CAP_SCOPE_TOKEN!r}); these are orphaned and ignored, safe to prune"
        )
    orphaned_records = len(orphaned_adjudications) + len(orphaned_rq) + len(orphaned_cap_keys)

    blocking_count = (
        verdict_counts["missing_verdict"] + verdict_counts["adverse"]
        + verdict_counts["invalid_verdict_class"] + cap_overrides_missing
        + len(unaccepted_items)
    )
    gate_passed = blocking_count == 0

    totals = {
        "required_items": len(regular_items) + len(cat4),
        "confirmed_ok": verdict_counts["confirmed_ok"],
        "missing_verdict": verdict_counts["missing_verdict"],
        "adverse": verdict_counts["adverse"],
        "invalid_verdict_class": verdict_counts["invalid_verdict_class"],
        "cap_notes": cap_notes_n,
        "cap_overrides_ok": cap_overrides_ok,
        "cap_overrides_missing": cap_overrides_missing,
        "review_queue_items": review_queue_items,
        "review_queue_unaccepted": len(unaccepted_items),
        "orphaned_records": orphaned_records,
        "by_kind": by_kind,
    }

    summary = {
        "success": True, "mode": mode,
        "canon_path": str(canon_path), "adjudications_path": str(adjudications_path),
        "canon_present": True, "pair_review_cap": pair_review_cap, "advisory": advisory,
        "totals": totals, "blocking_count": blocking_count, "gate_passed": gate_passed,
        "warnings": warnings, "generated_at": now_iso(),
    }

    print_human_report(
        canon_path, adjudications_path, totals, buckets, unaccepted_items,
        blocking_count, gate_passed, advisory, warnings,
    )

    exit_code = 0 if (gate_passed or advisory) else 1
    return summary, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _nonneg_int(value: str) -> int:
    """argparse `type=` validator for --pair-review-cap -- rejects a
    negative (or non-integer) value at parse time, before it can reach a
    schema-invalid summary (canon-adjudication-audit-summary.schema.json
    requires pair_review_cap >= 0). argparse's own error path already
    prints to stderr and exits 2 with no stdout JSON, satisfying the fatal
    contract."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value!r}")
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer, got {parsed}")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persisted, machine-checkable rollout gate for human/codex name-adjudication "
            "decisions over canon.json -- enumerates every duplicate-source-form group, "
            "existing merge, candidate missed-merge pair, and un-drained review_queue[] "
            "item a human/codex workflow must sign off, and cross-checks them against "
            "canon_adjudications.json. See this file's own module docstring and "
            "references/canon-and-glossary.md for the full contract."
        ),
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Write the empty adjudications template if absent (no-op otherwise unless --force).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Recompute the four required-item categories fresh from canon.json and "
             "cross-check them against the adjudications file.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With --init: overwrite an existing adjudications file with a fresh empty "
             "template. DESTRUCTIVE.",
    )
    parser.add_argument(
        "--canon-path", metavar="PATH", default=None,
        help=f"Override the canon.json path (default: {CANON_PATH}).",
    )
    parser.add_argument(
        "--adjudications-path", metavar="PATH", default=None,
        help=f"Override the persisted adjudications artifact path (default: "
             f"{DEFAULT_ADJUDICATIONS_PATH}).",
    )
    parser.add_argument(
        "--pair-review-cap", type=_nonneg_int, default=DEFAULT_PAIR_REVIEW_CAP,
        help=f"Category-3 pair count above which a single, explicit "
             f"degenerate_cap_overrides['{CAP_SCOPE_TOKEN}'] risk-acceptance is required "
             f"instead of per-pair review (default: {DEFAULT_PAIR_REVIEW_CAP}).",
    )
    parser.add_argument(
        "--advisory", action="store_true",
        help="Report every finding but never exit 1 for blocking findings (WARN-first "
             "escape hatch). Never masks a genuine fatal exit 2.",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.init and not args.check:
        print("Nothing to do -- pass --init, --check, or both. See --help.", file=sys.stderr)
        return 2

    canon_path = Path(args.canon_path) if args.canon_path else CANON_PATH
    adjudications_path = Path(args.adjudications_path) if args.adjudications_path else DEFAULT_ADJUDICATIONS_PATH

    try:
        if args.init:
            created, existing_counts = do_init(adjudications_path, args.force)
            if not args.check:
                summary = {
                    "success": True,
                    "mode": "init",
                    "created": created,
                    "adjudications_path": str(adjudications_path),
                    "existing_adjudications": existing_counts[0],
                    "existing_cap_overrides": existing_counts[1],
                    "existing_review_queue_risk_overrides": existing_counts[2],
                }
                print(json.dumps(summary, ensure_ascii=False))
                return 0

        mode = "init+check" if args.init else "check"
        summary, exit_code = run_check(
            canon_path, adjudications_path, args.pair_review_cap, args.advisory, mode
        )
        print(json.dumps(summary, ensure_ascii=False))
        return exit_code

    except CanonAdjudicationAuditError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        if e.offending is not None:
            print(f"offending: {e.offending!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
