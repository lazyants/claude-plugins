#!/usr/bin/env python3
"""final_audit.py -- W7 Final audit: the last deterministic gate before W8
Deliver, run once over every currently-converged segment plus the whole
project.

Generalized directly from the real, proven `historiettes-t3/final_audit.py`
(5 checks over 75 converged segments, zero hard defects -- the origin this
script generalizes). See SKILL.md's "W7 Final audit" section for the
authoritative spec and `assets/schemas/final-audit-summary.schema.json` for
this script's exact output shape -- those are the ground truth to read
before changing this one, not the non-shipped origin project above.

## What it runs

Two HARD checks, each counted separately, both rolling into `hard_failures`
(gates this script's own exit code):

  1. **coverage_failures** -- re-invokes `validate_draft.py` (reused, never
     reimplemented) against every converged segment's CURRENT on-disk draft.
     Catches a structurally-broken hand-edit.
  2. **stale_review_failures** -- compares every converged segment's CURRENT
     draft content sha1 (canonical JSON, `dispatch_token` excluded, matching
     `ledger_update.py`'s own `draft_content_sha1()`) against its ledger
     FRAGMENT's own `reviewed_draft_sha1`. Catches a hand-edit that stays
     structurally valid but silently substitutes prose the reviewer never
     saw.

Six WARN-only, advisory, non-gating checks -- four generalized from the
real reference's A1/A3/A4/A5 (the real `main()` only ever gated on coverage),
plus two whose content the PROJECT supplies:

  (1) glossary-diff    -- cross-segment source-name -> target-form drift
                           using each converged draft's own `names[]`, plus
                           `canon.json` self-consistency.
  (2) link-graph        -- full FNREF/VERSE sentinel bijection on the
                           translated draft (orphan footnotes, dangling
                           refs, unreferenced verses), cross-checked against
                           the segpack's own placeholder map.
  (3) foreign-remainder  -- source-language stopword-density + longest
                           same-language-token run, using the resolved
                           language preset's own STOPWORDS (via
                           `bootstrap_names.load_language_config`).
  (4) verse-structure    -- paste/duplicate-field detection across a verse's
                           own translated fields (mode-agnostic: this script
                           does not know which fields a given verse_policy
                           mode requires -- that is validate_draft.py's sole
                           authority -- so it flags any two distinct,
                           non-empty string-valued fields that are identical
                           up to whitespace), plus a segpack-completeness
                           check (parent block carries no source text at
                           all -- a citation would be empty).
  (5) forbidden-pattern  -- the PROJECT's own deterministic style bans,
                           declared as profile.yml's
                           `validation.forbidden_patterns` (#520). The plugin
                           ships no patterns and hardcodes none; a project's
                           codepoint-decidable style_bible rules are the only
                           thing this check knows. Scans every string leaf of
                           blocks/footnotes/verses exactly as written.
  (6) term-consistency   -- the PROJECT's own pinned common-noun TERMS OF ART
                           (an office title, a recurring institutional realia),
                           declared as profile.yml's `validation.terms` (#199).
                           `canon.json` is a proper-name glossary by
                           construction and cannot hold such a term, and WARN 1
                           above keys on canon entries and per-draft `names[]`
                           -- both proper-name channels -- so a recurring common
                           noun renders two ways with nothing noticing. This
                           check compares each SOURCE-BEARING CARRIER against
                           its own translated counterpart and reports a carrier
                           whose source carries the term while its draft carries
                           no occurrence of the pinned target form. The plugin
                           ships no terms and hardcodes none.

A third, distinct gate -- the **whole-project completeness gate** -- shells
out to `select_segments.py` one final time, over the FULL `manifest.json`
with no `--only-segs` restriction, and folds its classification report into
`completeness_counts`/`project_complete`. This is NOT the same population as
the two hard checks above: the hard checks only ever look at segments
ALREADY converged; the completeness gate looks at the whole book, converged
or not. Unlike the six WARN-only checks below, this gate DOES affect the
exit code -- a project that is not yet complete exits `3` (below `1`
priority) rather than `0`, so `select_segments.py`'s W5 delivery-refusal
rule holds on this default path too.

**#409 Step 2 carve-out.** A segment classified `stale` that ALSO carries
the durable `.ever_converged.<seg>` sentinel (written once by
`ledger_update.py:mark_ever_converged`, the same sentinel #409 Step 1's
re-translate gate reads) is NOT automatically carved out just because it
is stale-plus-sentineled -- the carve-out is FIELD-AWARE. A converged
segment classifies `stale` whenever ANY of cache_key.py's 15
`CACHE_KEY_FIELD_ORDER` fields drifted; only 3 of them
(`plugin_bundle_hash`, `schema_hash`, `derivation_bundle_hash` -- see
`SAFE_STALE_CARVEOUT_FIELDS`'s own comment for the full field-by-field
reasoning) are pure tooling/schema/derivation-CODE fingerprints that can
never, by themselves, change what the segment's own translated prose
should say. The carve-out applies only when EVERY one of that segment's
`mismatched_fields` is in that 3-field allowlist AND its `stale_reason` is
exactly `cache_key_mismatch` (never `draft_sha1_mismatch` -- a hand-edit
since review, already independently caught as a HARD failure). Any other
field -- e.g. `style_contract_hash` moving because the operator edited
`style_bible.md`, or any unrecognized/future field name -- keeps blocking,
fail-safe. `stale_previously_converged` counts exactly the carved-out
segments; `completeness_counts['stale']` itself is left UNCHANGED (still
the raw select_segments.py count, so an operator can always see the true
total) and `project_complete` is computed net of the carve-out -- see
`compute_project_complete()`. See "Reporting" below for the exact 0/1/3
contract.

Finally, a **frontback coverage report** (advisory, never exit-code-gating)
reads `manifest.json`'s `frontback[]` inventory directly and reports one
line per entry: a `translate`-decision entry reports its own segment's
current classification (cross-referenced from the SAME `select_segments.py`
classification computed for the completeness gate above -- never
independently re-derived); a `regenerate`/`omit`-decision entry is reported
by decision alone (no matching segment exists for those by construction).

## select_segments.py JSON contract (this script's caller-side expectation)

`select_segments.py` is specified elsewhere in this plugin (see SKILL.md's
"W5 Mass-translate") but is not itself this script's concern to implement.
This script invokes it as a subprocess with `--allow-empty --classify-only`
(full manifest, no `--only-segs` restriction -- "Omitting `--only-segs`
entirely reproduces default behavior byte-for-byte") and expects EXACTLY
ONE line of JSON on its stdout, one of:

    {"success": true,
     "segs": [...],                 # the emitted SEGS dispatch list
     "classification": {SEG: {"category": CATEGORY, ...}, ...},  # every
                                     # manifest segment; the value is an
                                     # object, CATEGORY is not a bare string
     "counts": {"reusable": N, "stale": N, "blocked_needs_regeneration": N,
                "recoverable": N, "not_started": N, "human_escalation": N}}
    {"success": false, "error": "..."}

CATEGORY is one of the six values named in SKILL.md's W5 classification
(`reusable`, `stale`, `blocked_needs_regeneration`, `recoverable`,
`not_started`, `human_escalation`). This is the exact contract
`select_segments.py` MUST satisfy for this whole-project gate to function --
treat this docstring as authoritative for that one integration point.

`--allow-empty` is required here (and NOT optional): a fully-converged
project -- every manifest segment already classifies "reusable" -- makes
`select_segments.py`'s own default emitted SEGS list empty, and
`select_segments.py` FATALs on an empty SEGS list unless `--allow-empty` is
passed (a guard meant for a silently-no-op W5 DISPATCH batch, not for this
whole-project completeness gate). Without the flag this gate would crash at
exit 2 on exactly the "project_complete: true" case it exists to report.

`--classify-only` is required for a second, independent reason (#409): it
suppresses select_segments.py's own Step 1 re-translate refusal -- and,
with it, the `authorizes_dispatch`/`previously_converged` payload fields,
which are `false`/`[]` under `--classify-only` and so never populated for
this script to read. This audit only ever CLASSIFIES, it never dispatches
a translate, so it must never be refused for the ordinary state of a
finished, previously-converged book. Because `previously_converged` is
never populated here, this script's own `stale_previously_converged`
carve-out (above) is derived independently, straight from `classification`
itself -- each segment's own reported category plus a direct
`.ever_converged.<seg>` sentinel-file check -- never from
select_segments.py's `previously_converged` field.

## Canonical paths (load-bearing, no target-language suffix)

    draft_path(seg)   = {durable_root}/segments/{seg}.draft.json
    segpack_path(seg) = {durable_root}/segments/segpack_{seg}.json

Reads only `draft_path(seg)` for draft content -- never a language-suffixed
variant.

## Reporting

Exactly ONE line of JSON -- this script's own `final-audit-summary.schema.json`
-shaped summary -- is printed to stdout (the "Structured stdout" the schema
describes), matching the same house convention `ledger_merge.py`/
`cache_key.py` already use: callers should read stdout, not the exit code
alone. All human-readable diagnostic detail (per-check failures, WARN lines,
the frontback report) is printed to stderr, for a human running this by hand.

Exit code is fail-closed on both hard defects and project incompleteness:
`0` if `hard_failures == 0` AND `project_complete` is true; `1` if
`hard_failures > 0` (hard defects in converged drafts -- takes priority);
`3` if `hard_failures == 0` but `project_complete` is false (converged
drafts are clean, but the whole project has not fully converged --
`not_started`/`recoverable`/`blocked_needs_regeneration`/`human_escalation`
segments remain, OR a `stale` segment remains that is NOT carved out by the
#409 ever-converged sentinel -- see `stale_previously_converged` above and
`compute_project_complete()`). `3` is distinct from `1` so callers can tell
"incomplete" from "defective converged drafts"; either way any nonzero
exit still gates W8 Deliver. `warnings` and the frontback report never
affect the exit code -- they remain informational only.

Usage: python3 final_audit.py [--plugin-root PATH]

Self-anchored: this script always lives at {durable_root}/scripts/<name>.py.
It never assumes cwd == durable_root, and never takes a --durable-root flag
of its own -- its own data is always self-anchored. #412: an explicit
--plugin-root PATH overrides where the SIBLING select_segments.py script
the whole-project completeness gate shells out to is resolved from --
deliberately NEVER derived from this script's own self-anchored
DURABLE_ROOT (${durable_root}/scripts/ is a Step-0a copy the codex process
this gate protects can write to, so resolving the checker from inside the
tree it checks would let a tampered copy pass itself). select_segments.py
itself DOES accept --plugin-root (it resolves a further sibling of its
own, ledger_merge.py), so it is forwarded verbatim, together with a
synthesized --durable-root (this script's own DURABLE_ROOT, since
select_segments.py no longer physically sits under that root once
relocated). See run_completeness_gate() below and references/gotchas.md
§4 for the full two-flag convention this follows. Omitting the flag
reproduces today's self-anchored sibling lookup byte-for-byte.
"""
import argparse
import errno
import hashlib
import json
# `os` is used by exactly one thing here: the shared sentinel predicate's
# `dir_fd` branch. That branch is unreachable from this script (nothing here
# holds a directory descriptor, so it always passes None), but the predicate
# is pinned BYTE-IDENTICAL across four scripts by
# tests/select_segments.test.py::test_sentinel_predicate_is_identical_in_all_
# four_scripts -- so the import has to exist wherever the copy does, or the
# branch would raise NameError instead of the OSError its contract promises.
import os
import re
import stat
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:
    print(
        "ERROR: final_audit.py requires the 'PyYAML' package to read "
        "profile.yml (via validate_draft.py's own profile loader). Install "
        "with: pip install PyYAML (or: pip install -r requirements.txt from "
        "the literary-translator plugin's own directory).",
        file=sys.stderr,
    )
    sys.exit(2)

# ---------------------------------------------------------------------------
# Self-anchoring -- see the module docstring above for the #412 --plugin-root
# override.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SEGMENTS_DIR = DURABLE_ROOT / "segments"
RUNS_DIR = DURABLE_ROOT / "runs"
LEDGER_D = RUNS_DIR / "ledger.d"
MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
CANON_PATH = DURABLE_ROOT / "canon.json"
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"

# validate_draft.py and bootstrap_names.py live next to this script -- import
# them directly (never reimplemented) rather than shelling out, since both
# expose plain Python functions this script calls in-process.
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import validate_draft as vd
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    print(
        f"ERROR: final_audit.py could not import validate_draft.py from "
        f"{SCRIPTS_DIR}: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)
try:
    import bootstrap_names as bn
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    print(
        f"ERROR: final_audit.py could not import bootstrap_names.py from "
        f"{SCRIPTS_DIR}: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)

# Completeness-gate category enum, per SKILL.md's W5 classification --
# EXCLUDING "reusable" (the one category that means "already fine").
COMPLETENESS_CATEGORIES = [
    "not_started",
    "recoverable",
    "stale",
    "blocked_needs_regeneration",
    "human_escalation",
]

# Format-neutral placeholder sentinels -- same convention validate_draft.py
# uses: ⟦FNREF_N⟧ for footnote anchors, ⟦...⟧ generically for anything else
# (verse placeholders are free-form per segpack.schema.json's `placeholder`
# field, so they are matched by cross-referencing the segpack's own map,
# never by assuming a naming convention like the real reference's
# VERSE_V\d+_[0-9a-f]{8}).
FNREF_RE = re.compile(r"⟦FNREF_(\d+)⟧")
SENTINEL_RE = re.compile(r"⟦[^⟧]+⟧")


def _fatal(msg) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def load_json(path, label):
    if not path.exists():
        return None, f"{label} missing: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{label} at {path} is not valid JSON: {exc}"


def draft_path(seg):
    return SEGMENTS_DIR / f"{seg}.draft.json"


def segpack_path(seg):
    return SEGMENTS_DIR / f"segpack_{seg}.json"


def draft_content_sha1(path):
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- see draft_sha1.py's own module docstring for why.

    Must match, byte for byte, draft_sha1.py's and ledger_update.py's own
    draft_content_sha1() -- both parse the draft as JSON, drop
    'dispatch_token' if present, and re-serialize the remainder via
    identical sorted-key canonical JSON before hashing. This is compared
    directly against a fragment's `reviewed_draft_sha1`, which
    ledger_update.py writes via this exact algorithm -- NOT a raw-bytes
    hash of the on-disk file.

    Raises OSError (unreadable file), json.JSONDecodeError (not valid
    JSON), or ValueError (valid JSON but not an object) on failure --
    callers handle all three.
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


def _norm_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------------------
# Converged-segment discovery: reads runs/ledger.d/*.json fragments directly
# (never the materialized runs/ledger.json) -- the fragment's own on-disk
# `status` is exactly the "converged" this script's hard checks are scoped
# to. This is deliberately NOT the same as ledger_merge.py's materialized
# view, which additionally reclassifies a cache-key-mismatched fragment as
# `stale` -- that is a DIFFERENT staleness concept (config/derivation drift)
# from this script's own stale_review_failures check (draft-content drift
# since the review that approved it), and the two must not be conflated.
# ---------------------------------------------------------------------------

def load_converged_fragments():
    """Returns {seg: fragment_dict} for every runs/ledger.d/*.json fragment
    whose own on-disk `status` is "converged". A missing ledger.d directory
    means "nothing has converged yet" -- not an error; both hard checks
    trivially report zero failures over an empty population."""
    converged = {}
    if not LEDGER_D.is_dir():
        return converged
    for frag_path in sorted(LEDGER_D.glob("*.json")):
        seg = frag_path.stem
        record, err = load_json(frag_path, f"ledger fragment {frag_path.name}")
        if err:
            print(f"WARNING: {err} -- skipping for final_audit purposes", file=sys.stderr)
            continue
        if isinstance(record, dict) and record.get("status") == "converged":
            converged[seg] = record
    return converged


# ---------------------------------------------------------------------------
# Hard check 1: coverage, via validate_draft.py's own validate() -- reused,
# never reimplemented.
# ---------------------------------------------------------------------------

def hard_check_coverage(converged):
    """Returns (n_failing_segments, detail_lines)."""
    profile = vd.load_profile()
    cfg = vd.ProfileConfig(profile)
    n_failing = 0
    details = []
    for seg in sorted(converged):
        errs = vd.validate(seg, cfg)
        if errs:
            n_failing += 1
            for e in errs:
                details.append(f"[{seg}] COVERAGE {e}")
    return n_failing, details


# ---------------------------------------------------------------------------
# Hard check 2: stale-review -- current on-disk draft sha1 vs the ledger
# fragment's own reviewed_draft_sha1.
# ---------------------------------------------------------------------------

def hard_check_stale_review(converged):
    """Returns (n_failing_segments, detail_lines)."""
    n_failing = 0
    details = []
    for seg in sorted(converged):
        fragment = converged[seg]
        expected = fragment.get("reviewed_draft_sha1")
        dp = draft_path(seg)
        if not isinstance(expected, str) or not expected:
            n_failing += 1
            details.append(
                f"[{seg}] STALE-REVIEW converged fragment has no "
                f"reviewed_draft_sha1 -- cannot confirm the reviewer saw "
                f"the current draft"
            )
            continue
        if not dp.is_file():
            n_failing += 1
            details.append(f"[{seg}] STALE-REVIEW draft missing: {dp}")
            continue
        try:
            current = draft_content_sha1(dp)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            n_failing += 1
            details.append(
                f"[{seg}] STALE-REVIEW draft at {dp} is unreadable/corrupt "
                f"-- cannot confirm the reviewer saw the current draft ({exc})"
            )
            continue
        if current != expected:
            n_failing += 1
            details.append(
                f"[{seg}] STALE-REVIEW current draft sha1 {current} != "
                f"reviewed_draft_sha1 {expected} recorded at convergence "
                f"-- draft was hand-edited after the review that approved it"
            )
    return n_failing, details


# ---------------------------------------------------------------------------
# WARN 1: glossary-diff -- cross-segment source-name -> target-form drift,
# plus canon.json self-consistency. Generalized from the real reference's
# A1 (fr -> ru), which hardcoded field names; the plugin's own
# canon-entry.schema.json / draft.schema.json use the generalized
# `source_form`/`canonical_target_form` (canon) and `source_form`/
# `target_form` (per-draft names[] entries, schema-open per
# draft.schema.json -- also accepts `canonical_target_form` as an alias, in
# case a translate-prompt template reuses the canon field name verbatim).
# ---------------------------------------------------------------------------

def _name_entry_forms(entry):
    """Best-effort extraction of (source_form, target_form) from one
    draft `names[]` entry. draft.schema.json deliberately leaves this
    entry's own fields unconstrained (see its own description) -- this
    accepts either of the two plausible field-name conventions rather than
    hardcoding one, and returns (None, None) for an entry that matches
    neither (skipped, not fatal -- this is an advisory check)."""
    if not isinstance(entry, dict):
        return None, None
    source_form = entry.get("source_form")
    target_form = entry.get("target_form") or entry.get("canonical_target_form")
    if isinstance(source_form, str) and source_form and isinstance(target_form, str) and target_form:
        return source_form, target_form
    return None, None


def warn_glossary_diff(converged):
    """Cross-segment WARN check, run once over the WHOLE converged
    population (not per-segment)."""
    warns = []

    canon_entries = {}
    if CANON_PATH.is_file():
        canon, err = load_json(CANON_PATH, "canon.json")
        if err:
            warns.append(f"GLOSSARY-DIFF could not read canon.json: {err}")
        elif isinstance(canon, dict):
            canon_entries = canon.get("entries") or {}

    canon_by_source = {}
    for entry in canon_entries.values():
        if not isinstance(entry, dict):
            continue
        sf = entry.get("source_form")
        tf = entry.get("canonical_target_form")
        if sf and tf:
            canon_by_source.setdefault(sf, set()).add(tf)
    for sf, forms in canon_by_source.items():
        if len(forms) > 1:
            warns.append(
                f"GLOSSARY-DIFF canon.json self-inconsistent: source_form "
                f"{sf!r} -> {sorted(forms)} -- may reflect an intentional "
                f"split already adjudicated in canon_senses.json; "
                f"reconcile there before treating this as a defect"
            )

    # source_form -> {target_form -> [segs]}
    source_to_target = defaultdict(lambda: defaultdict(list))
    for seg in sorted(converged):
        draft, err = load_json(draft_path(seg), f"draft {seg}")
        if err or not isinstance(draft, dict):
            continue
        for entry in (draft.get("names") or []):
            sf, tf = _name_entry_forms(entry)
            if sf:
                source_to_target[sf][_norm_ws(tf)].append(seg)

    for sf, forms in sorted(source_to_target.items()):
        segcount = sum(len(v) for v in forms.values())
        if segcount < 2:
            continue
        distinct = set(forms)
        if len(distinct) > 1:
            detail = "; ".join(f"{k!r}={v}" for k, v in forms.items())
            warns.append(
                f"GLOSSARY-DIFF source_form {sf!r}: {len(distinct)} distinct "
                f"target forms across segments -> {detail}"
            )
        cset = canon_by_source.get(sf)
        if cset and len(cset) == 1:
            cform = _norm_ws(next(iter(cset)))
            if cform not in distinct:
                warns.append(
                    f"GLOSSARY-DIFF source_form {sf!r}: segments use "
                    f"{sorted(distinct)} but canon.json says {cform!r} -- MANUAL"
                )
    return warns


# ---------------------------------------------------------------------------
# WARN 2: link-graph -- full FNREF/VERSE sentinel bijection on the
# translated draft, cross-checked against the segpack's own placeholder map.
# validate_draft.py already checks PER-BLOCK placeholder multisets (its
# check 2/3); this is a document-wide bijection sweep (orphan footnotes,
# unreferenced verses) that validate_draft.py does not attempt.
# ---------------------------------------------------------------------------

def warn_link_graph(seg):
    warns = []
    draft, err = load_json(draft_path(seg), f"draft {seg}")
    if err or not isinstance(draft, dict):
        return warns  # already reported as a coverage hard failure
    segpack, err = load_json(segpack_path(seg), f"segpack {seg}")
    if err or not isinstance(segpack, dict):
        return warns  # already reported as a coverage hard failure

    blocks = draft.get("blocks", {}) or {}
    fns = {str(k): (v or "") for k, v in (draft.get("footnotes", {}) or {}).items()}
    verses = draft.get("verses", {}) or {}

    ph_to_vid = {}
    for v in (segpack.get("verses") or []):
        if isinstance(v, dict) and v.get("placeholder") and v.get("vid"):
            ph_to_vid[v["placeholder"]] = v["vid"]

    # Scan every text field this draft carries: blocks, footnotes, and every
    # string-valued field of every verse entry (a verse's own rendered/gloss
    # text can itself carry an inline ⟦FNREF_N⟧ the translator kept).
    text_fields = list(blocks.values()) + list(fns.values())
    for rv in verses.values():
        if isinstance(rv, dict):
            text_fields.extend(v for v in rv.values() if isinstance(v, str))

    ref_fn = set()
    ref_vid = set()
    unknown_sentinels = set()
    for text in text_fields:
        for token in SENTINEL_RE.findall(text or ""):
            m = FNREF_RE.fullmatch(token)
            if m:
                ref_fn.add(m.group(1))
            elif token in ph_to_vid:
                ref_vid.add(ph_to_vid[token])
            else:
                unknown_sentinels.add(token)

    for n in sorted(ref_fn):
        if n not in fns:
            warns.append(f"[{seg}] LINK-GRAPH dangling FNREF_{n}: no footnote {n} in draft")
    for n in sorted(fns):
        if n not in ref_fn:
            warns.append(
                f"[{seg}] LINK-GRAPH orphan footnote {n}: no ⟦FNREF_{n}⟧ "
                f"referenced anywhere in this draft -- MANUAL"
            )
    for vid in sorted(verses):
        if vid not in ref_vid:
            warns.append(
                f"[{seg}] LINK-GRAPH unreferenced verse {vid}: no matching "
                f"placeholder found in body/footnote/verse text -- MANUAL"
            )
    for token in sorted(unknown_sentinels):
        warns.append(
            f"[{seg}] LINK-GRAPH unrecognized sentinel {token!r}: neither a "
            f"footnote ref nor a known segpack verse placeholder -- MANUAL"
        )
    return warns


# ---------------------------------------------------------------------------
# WARN 3: foreign-remainder scan -- source-language stopword density +
# longest same-source-language-token run, using the resolved language
# preset's own STOPWORDS. Generalized from the real reference's hardcoded
# French-stopword-list + Latin-alphabet-run heuristic: a generalized plugin
# cannot assume source and target scripts differ (e.g. French -> German both
# use the Latin alphabet), so this scans for RUNS OF SOURCE-LANGUAGE
# STOPWORDS specifically, never "looks Latin" as a proxy for "is foreign".
# ---------------------------------------------------------------------------

def _strip_outer_punct(token):
    """Strip leading/trailing punctuation (Unicode category P) from a
    whitespace-split token, without touching combining marks (Mn/Mc) --
    those belong to their base letter, not the surrounding punctuation.
    '_' (the Markdown emphasis marker) is already covered here: its
    Unicode category is Pc (Connector Punctuation), so no separate
    special-case is needed."""
    def _is_adornment(ch):
        return unicodedata.category(ch).startswith("P")

    start, end = 0, len(token)
    while start < end and _is_adornment(token[start]):
        start += 1
    while end > start and _is_adornment(token[end - 1]):
        end -= 1
    return token[start:end]


def _fold_source_marks(s):
    """Fold Hebrew niqqud (vowel points / cantillation) for the
    foreign-remainder comparison ONLY: NFD-decompose, drop every combining
    mark (Unicode category Mn) in the Hebrew block range U+0591..U+05C7, then
    re-NFC. A pointed (vocalized) Hebrew draft token thus matches its
    unpointed consonantal stopword -- a shipped he.json carries bare
    consonantal function words, but a real Hebrew source draft may spell the
    same words fully pointed (#209).

    Scoped strictly to the Hebrew mark range, NOT a blanket Mn strip: a
    Latin/Cyrillic/etc. combining mark (e.g. the U+0301 COMBINING ACUTE in
    Spanish "Sí") is category Mn but out of range, so it is preserved --
    dropping it would collapse "Sí" into an unrelated-language "si". This is
    applied SYMMETRICALLY on both compare sides (the document-token side in
    warn_foreign_remainder() and the stopword side in main()); for NFC Latin
    text carrying no in-range marks it is exactly equivalent to the plain NFC
    normalization it replaces."""
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(
        c
        for c in decomposed
        if not (unicodedata.category(c) == "Mn" and 0x0591 <= ord(c) <= 0x05C7)
    )
    return unicodedata.normalize("NFC", stripped)


def warn_foreign_remainder(seg, stopwords_lower):
    warns = []
    if not stopwords_lower:
        return warns
    draft, err = load_json(draft_path(seg), f"draft {seg}")
    if err or not isinstance(draft, dict):
        return warns

    for bid, txt in (draft.get("blocks", {}) or {}).items():
        if not txt:
            continue
        clean = SENTINEL_RE.sub(" ", txt)
        tokens_ws = clean.split()
        low_tokens = [
            _fold_source_marks(_strip_outer_punct(t)).lower() for t in tokens_ws
        ]
        stop_hits = sum(1 for t in low_tokens if t in stopwords_lower)
        run = maxrun = 0
        for t in low_tokens:
            if t in stopwords_lower:
                run += 1
                maxrun = max(maxrun, run)
            else:
                run = 0
        if stop_hits >= 3 or maxrun >= 3:
            snippet = _norm_ws(clean)[:120]
            warns.append(
                f"[{seg}] FOREIGN-REMNANT possible untranslated source-language "
                f"text in {bid}: stopword_hits={stop_hits} longest_run={maxrun} "
                f":: {snippet!r} -- MANUAL"
            )
    return warns


# ---------------------------------------------------------------------------
# WARN 4: verse-structure -- generalized from the real reference's A5.
# validate_draft.py is the SOLE authority on which fields a verse entry
# must/must not carry for the active verse_policy.mode (see
# references/verse-policy.md); this WARN check is deliberately mode-agnostic
# and looks only for defects no mode-aware check would catch:
#   (a) paste/duplicate detection -- two distinct, non-empty string-valued
#       fields on the same verse entry that are identical up to whitespace
#       (whatever those fields are named for the active mode);
#   (b) segpack completeness -- the verse's OWN parent block carries no
#       source text at all, so a citation of the original would be empty.
# ---------------------------------------------------------------------------

def warn_verse_structure(seg):
    warns = []
    draft, err = load_json(draft_path(seg), f"draft {seg}")
    if err or not isinstance(draft, dict):
        return warns
    segpack, err = load_json(segpack_path(seg), f"segpack {seg}")
    if err or not isinstance(segpack, dict):
        return warns

    block_source = {}
    for b in (segpack.get("blocks") or []):
        if isinstance(b, dict) and b.get("id"):
            block_source[b["id"]] = (b.get("plain_text") or b.get("source_html") or "")
    parent_block_of = {
        v["vid"]: v.get("parent_block")
        for v in (segpack.get("verses") or [])
        if isinstance(v, dict) and v.get("vid")
    }

    for vid, rv in (draft.get("verses", {}) or {}).items():
        if not isinstance(rv, dict):
            continue
        seen = {}
        for field, value in rv.items():
            if not isinstance(value, str) or not value.strip():
                continue
            normed = _norm_ws(value)
            for other_field, other_normed in seen.items():
                if normed == other_normed:
                    warns.append(
                        f"[{seg}] VERSE-STRUCTURE verse {vid}: field "
                        f"{field!r} == field {other_field!r} up to whitespace "
                        f"(paste/duplicate -- need genuinely distinct content)"
                    )
            seen[field] = normed

        parent_block = parent_block_of.get(vid)
        source_text = block_source.get(parent_block, "") if parent_block else ""
        if not source_text.strip():
            warns.append(
                f"[{seg}] VERSE-STRUCTURE verse {vid}: segpack has NO "
                f"original source text for parent block {parent_block!r} "
                f"(a citation of the original would be empty)"
            )
    return warns


# ---------------------------------------------------------------------------
# WARN 5: forbidden-pattern scan -- the project's OWN deterministic style
# bans, declared in profile.yml as `validation.forbidden_patterns` (#520).
#
# The plugin ships NO patterns and hardcodes none. A style contract lives in
# the project's `style_bible.md`, and only the project knows which of its
# rules are codepoint-decidable: the one concrete rule that motivated this
# (a ban on runs of two or more asterisks, because the operator's own EPUB
# renderer prints them literally) cannot ship as a builtin, since `**` is
# ordinary Markdown bold on the shipped Obsidian output path. So the plugin
# ships the MECHANISM and the project supplies the rule.
#
# Three properties this check deliberately has, each of which a plausible
# alternative gets wrong:
#
#   - **The scanned text is the draft AS WRITTEN.** Sentinels are NOT
#     substituted out the way warn_foreign_remainder() does at its own call
#     site: `SENTINEL_RE.sub(" ", txt)` would both HIDE a violation that sits
#     inside a sentinel and MANUFACTURE one that only exists because a
#     placeholder became a space. A style contract is a statement about what
#     the translator wrote, so that is what is tested.
#   - **Every string leaf of blocks/footnotes/verses, not an allowlist of
#     fields.** `draft.schema.json` constrains a `verses` value no further
#     than "is an object", because which fields exist varies by
#     `verse_policy.mode` and validate_draft.py is that question's SOLE
#     authority. An allowlist here (`rendered`/`literal_gloss`) would
#     duplicate that authority and be wrong under some mode, so the scan is a
#     deliberate SUPERSET of what any one renderer reads. `names` and `notes`
#     are machinery/metadata and are not scanned.
#   - **A pattern that fails to compile is REPORTED, never skipped.** A
#     silently-unenforced operator rule is a false green: the run looks
#     exactly like one where the rule held.
#
# Advisory only. A hit never changes the exit code -- see this module's
# docstring on the WARN/hard split.
# ---------------------------------------------------------------------------

# Only these three families of a draft carry translator-authored prose.
SCANNED_DRAFT_SECTIONS = ("blocks", "footnotes", "verses")


def forbidden_patterns(profile):
    """profile.yml's `validation.forbidden_patterns` (#520) as a list of
    declaration mappings, or `[]` when the project declares none.

    Anything that is not a list of mappings reads as no declaration, and that
    is deliberate rather than lenient: **profile.schema.json is the gate for
    SHAPE, and it is the only one.** It refuses a null, a mapping where a list
    belongs, a scalar list item, an unknown property, a missing field and an
    id that is not a slug -- every one of them, at Step 0, before a run starts.
    Re-deciding any of that here would be a second, hand-written copy of a
    gate that already exists, and three review rounds spent finding a
    different shape each copy had missed. What this reader owes is not to
    crash on a shape Step 0 would have refused.

    That leaves exactly one gap, and it is shared, not special: an operator who
    edits profile.yml AFTER Step 0 is not re-validated, because W7 reaches the
    file through `vd.load_profile()`, which only `yaml.safe_load`s. No other
    field defends against that either -- not `untranslated_sentinel`, not
    `admit_contract_only_stale` -- so defending this one alone would be
    inconsistent machinery, not extra safety.

    What a schema genuinely CANNOT decide is whether a well-formed pattern
    string compiles. That check has no other home, and it is the one
    compile_forbidden_patterns() keeps."""
    validation = (profile or {}).get("validation")
    if not isinstance(validation, dict):
        return []
    decls = validation.get("forbidden_patterns")
    if not isinstance(decls, list):
        return []
    return [d for d in decls if isinstance(d, dict)]


def compile_forbidden_patterns(decls):
    """(compiled, warns) -- compiled is a list of (rule_id, regex, message).

    ONE rejection is reported: a pattern that does not compile. profile.yml is
    schema-valid by the time a run starts, so a well-formed declaration whose
    regex is nonetheless broken is the only failure no earlier gate can catch
    -- a JSON Schema can check that `pattern` is a string of the right length,
    never that `re` accepts it. Reporting it matters because the alternative
    is a run that reads exactly as it would if the rule had held: a rule the
    operator believes is enforced, silently enforcing nothing.

    A declaration is skipped without comment only when it carries no usable
    pattern to compile, which Step 0 already refuses."""
    compiled = []
    warns = []
    for index, decl in enumerate(decls):
        declared_id = decl.get("id")
        pattern = decl.get("pattern")
        message = decl.get("message")
        if not isinstance(pattern, str) or not pattern:
            continue
        # Named only once the declaration has survived the reject above, so
        # the loop reads in the order it decides things.
        rule_id = declared_id if isinstance(declared_id, str) and declared_id else f"#{index}"
        if not isinstance(message, str) or not message:
            message = "(declaration carries no message)"
        try:
            regex = re.compile(pattern)
        except Exception as exc:
            # Deliberately `Exception`, not `re.error`, and the try body is
            # exactly one call so nothing else can hide in here. `re.compile`
            # does NOT raise a single family: a malformed pattern raises
            # `re.error`, but an oversized repetition count raises
            # `OverflowError` -- measured on 3.14, from a 39-character pattern
            # that the schema's 200-codepoint cap admits without complaint --
            # and which types a given interpreter raises is not a contract any
            # version pins. Enumerating them means the NEXT unlisted type turns
            # an advisory lane into a traceback that blocks delivery, which is
            # exactly the failure this whole check exists to avoid. The
            # invariant is the one worth holding: an uncompilable pattern is
            # REPORTED, never fatal.
            warns.append(
                f"STYLE-PATTERN {rule_id}: pattern {pattern!r} does not "
                f"compile ({type(exc).__name__}: {exc}) -- rule NOT enforced "
                f"this run -- MANUAL"
            )
            continue
        compiled.append((rule_id, regex, message))
    return compiled, warns


def _string_leaves(node, label):
    """Yields (path_label, string) for every string leaf under `node`.

    An explicit stack, NOT recursion: `json.loads` decodes container nesting
    far deeper than Python's own recursion limit (measured: past 20 000 levels
    against a default limit of 1 000), and a `verses` value may legitimately
    carry nested objects. It is not the script's first depth-sensitive step,
    and no claim is made here about which step fails first or how -- four
    review rounds produced a different wrong answer to that each time, which
    is the sign that the attribution, not its wording, was the defect. The
    pre-existing behaviour is disclosed in the release notes rather than fixed;
    the iterative form costs the same as the recursive one and simply declines
    to add one more place that fails.

    Each path component is bracketed and repr'd -- `verses['v1']['rendered']`
    -- so that a key which itself contains a dot cannot render the same as the
    equivalent nesting. Two keys differing only in the RUN-LENGTH of internal
    whitespace still collapse to the same label once the emitted line is
    normalized; that is an ambiguous advisory string, and warnings gate
    nothing."""
    stack = [(node, label)]
    while stack:
        # LIFO, so a draft's leaves are reported in REVERSE insertion order --
        # unlike warn_verse_structure (dict order) and warn_link_graph
        # (sorted). Accepted deliberately rather than overlooked: every fix
        # costs code to buy ordering that nothing reads, since warnings gate
        # nothing and the one test comparing paths sorts them first.
        current, label = stack.pop()
        if isinstance(current, str):
            yield label, current
        elif isinstance(current, dict):
            for key, value in current.items():
                stack.append((value, f"{label}[{key!r}]"))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                stack.append((value, f"{label}[{index}]"))


def warn_forbidden_patterns(seg, compiled):
    warns = []
    if not compiled:
        return warns
    draft, err = load_json(draft_path(seg), f"draft {seg}")
    if err or not isinstance(draft, dict):
        return warns

    for section in SCANNED_DRAFT_SECTIONS:
        for label, text in _string_leaves(draft.get(section), section):
            for rule_id, regex, message in compiled:
                # ONE traversal, `finditer` rather than `findall`, and the
                # iterator is CONSUMED rather than materialized. Retaining a
                # Match per hit is not a micro-optimization here: a zero-width
                # rule is explicitly supported and yields len(text)+1 hits,
                # draft leaves carry no length cap, and measured on a
                # 1 000 000-character leaf the list peaks at 122.5 MiB against
                # roughly nothing for the streaming count. That is the
                # OverflowError failure in another costume -- an ADVISORY check
                # aborting W7 before it can emit its summary, taking the two
                # hard checks' verdict with it.
                found = regex.finditer(text)
                first = next(found, None)
                if first is None:
                    continue
                hit_count = 1 + sum(1 for _ in found)
                start = max(0, first.start() - 40)
                snippet = text[start:first.end() + 40]
                # One normalization, applied to the WHOLE formatted line as
                # its last step: main() prints each warning string raw, and a
                # line break reaching stderr from ANY of the three operator-
                # or draft-controlled fragments (the message, the snippet, or
                # a draft KEY inside the path label) would split one warning
                # across physical lines. `\s` here is Unicode-aware, so
                # U+0085, U+2028 and U+2029 collapse too, not just CR/LF.
                warns.append(_norm_ws(
                    f"[{seg}] STYLE-PATTERN {rule_id} in {label}: {message} "
                    f"(hits={hit_count}) :: {snippet!r} -- MANUAL"
                ))
    return warns


# ---------------------------------------------------------------------------
# WARN 6: term-consistency -- the project's OWN pinned common-noun terms of
# art, declared in profile.yml as `validation.terms` (#199).
#
# WHY THIS LANE EXISTS AT ALL. `canon.json` is a 1:1 PROPER-NAME dictionary by
# construction: `bootstrap_names.py` surfaces capitalized candidates, and the
# shipped adjudication contract refuses to `accept` an `is_proper_name:false`
# entry (assets/templates/glossary_TASK.template.md). WARN 1 above -- the only
# cross-segment consistency check this script has -- keys on `canon.entries`
# and each draft's own `names[]`, both proper-name channels. So a recurring
# COMMON NOUN that must render one way for the whole book (an Ancien-Regime
# court office, an institutional realia) can be neither frozen in canon nor
# seen by the drift check, and renders two ways in one delivered volume with
# nothing -- not even a WARN -- noticing. The pinning half is already served:
# `style_bible.md` section C carries a required-fill title/honorific mapping and
# is delivered in full to every translate and review job. The DETECTION half is
# what this buys, and it is what #199 asks for.
#
# THE RULE IS CARRIER-LOCAL ABSENCE, NOT A COUNT. For each carrier the segpack
# and the draft key by the same identifier, the check asks one question: does
# this carrier's SOURCE contain the pinned source form while its OWN translated
# counterpart contains no occurrence of the pinned target form? An earlier
# design compared per-segment occurrence TOTALS instead, and the totals are not
# comparable: under `full_rhymed_plus_literal` one source verse legitimately
# yields two target fields, inflating the target count and MASKING a genuinely
# missing prose or footnote occurrence. Segment-wide absence is the opposite
# failure -- a body that renders the office correctly hides a footnote that does
# not, which is exactly the instance #199 was filed for.
#
# WHICH CARRIERS ARE COMPARED, AND THE ONE PRINCIPLE BEHIND IT: a carrier is
# compared only where the ACTIVE POLICY says it is TRANSLATED. A carrier the
# project has declared passed through in the SOURCE language is not a
# source/target pair at all, and comparing it would warn on every occurrence of
# a term the operator handled exactly as instructed.
#
#   - blocks -- compared, EXCEPT a block claimed by a `mount:"block"` verse.
#     Extraction leaves the original poem in that segpack block while
#     validate_draft.py requires its `draft.blocks[id]` to be ONLY the verse
#     placeholder, so comparing it warns on every pinned term the poem contains
#     even when the verse itself renders it correctly.
#   - footnotes -- compared unless `footnotes.apparatus_policy` is
#     `preserve_source`, which carries definitions through UNTRANSLATED while
#     segpack.py still ships them. `body_refs_only`/`omit_apparatus` carry no
#     definitions at all, so they need no case of their own.
#   - verses -- compared unless `verse_policy.mode` is `skip`, which passes
#     verse content through as-is while still requiring every verse KEY to be
#     present. A verse's SOURCE text is not in the segpack (extraction
#     substitutes a placeholder); it lives in manifest.json's `verse.store[]`.
#
# THE VERSE TARGET IS `rendered` + `literal_gloss`, NOT EVERY STRING LEAF, and
# that is where this check deliberately parts from WARN 5 above. The reason is
# POLARITY, not disagreement. WARN 5 asks "did the translator write something
# BANNED?", where scanning a superset of fields can only over-report. This one
# asks "is the pin PRESENT?", where scanning a superset lets any extra field
# SUPPRESS the warning: a schema-valid draft carrying `rendered` with the wrong
# term plus an ignored `note` holding the pin would ship the wrong term
# unreported. Those two fields are not a second copy of validate_draft.py's
# per-mode authority -- they are the DELIVERED surface, and all three consumers
# agree on it mode-independently (validate_draft.py, assemble.py,
# render_obsidian.py).
#
# WHAT IT DOES NOT DECIDE. Whether a rendering is CORRECT is an editorial call
# and never a script's (see this plugin's iron rule). This check knows only what
# the operator pinned. It matches by SUBSTRING, which is what makes a suffixing
# target language work -- a pinned `<stem>` is found inside its inflected forms
# -- so the pinning contract is that an operator pins the INVARIANT part. A
# target language that inflects by prefix or stem change cannot be pinned this
# way. A carrier whose occurrence is legitimately omitted or rendered
# pronominally still warns; the line names the carrier so that costs one glance.
#
# Advisory only. A hit never changes the exit code -- see this module's
# docstring on the WARN/hard split.
# ---------------------------------------------------------------------------

# The verse fields that actually reach the reader. Kept as a constant because
# three shipped consumers agree on it and this check must not drift from them.
DELIVERED_VERSE_FIELDS = ("rendered", "literal_gloss")

# Tags are replaced by a SPACE, never by the empty string: gluing two adjacent
# elements together would manufacture an occurrence that spans a boundary no
# reader ever sees. The disclosed cost of that choice is the converse -- a term
# split by inline markup (`pre<em>sident</em>`) is a miss -- which only reaches
# a segpack block carrying `source_html` and no `plain_text`.
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _fold_term_text(text):
    """NFC, then casefold -- the one normalization both sides of this check go
    through, including the declared forms themselves.

    NFC is load-bearing, not decoration: extraction's own `normalize_text()`
    only collapses whitespace, so a decomposed `e` + COMBINING ACUTE and a
    precomposed one reach this script as different bytes and would silently
    fail to match while looking identical to the operator who wrote the pin."""
    return unicodedata.normalize("NFC", text).casefold()


def declared_terms(profile):
    """profile.yml's `validation.terms` (#199) as a list of
    (source_form, target_form, folded_source, folded_target) tuples, or `[]`
    when the project declares none.

    Shape is settled at Step 0 by profile.schema.json and is NOT re-decided
    here -- the same split WARN 5's own `forbidden_patterns()` documents at
    length. What this reader owes is not to crash on a shape Step 0 would have
    refused. Unlike WARN 5 there is no second, script-owned rejection: a
    declaration is two plain strings, and a string that is well-formed has
    nothing left that only a runtime can decide."""
    validation = (profile or {}).get("validation")
    if not isinstance(validation, dict):
        return []
    decls = validation.get("terms")
    if not isinstance(decls, list):
        return []
    terms = []
    for decl in decls:
        if not isinstance(decl, dict):
            continue
        source_form = decl.get("source_form")
        target_form = decl.get("target_form")
        if not (isinstance(source_form, str) and source_form):
            continue
        if not (isinstance(target_form, str) and target_form):
            continue
        terms.append(
            (source_form, target_form,
             _fold_term_text(source_form), _fold_term_text(target_form))
        )
    return terms


def verse_source_index(manifest):
    """`vid` -> that verse's SOURCE `plain_text`, from manifest.json's
    `verse.store[]` (where both fields are schema-REQUIRED).

    A `vid` appearing MORE THAN ONCE is dropped rather than resolved. The id is
    book-global -- the extractor assigns it from a single counter -- but neither
    manifest.schema.json nor the W2 derivable check rejects a duplicate, and
    assemble.py only refuses one much later. Keeping the last writer would let
    ONE segment's verse source be compared against ANOTHER segment's draft, and
    a silently mis-attributed comparison is worse than no comparison at all in a
    lane whose whole contract is to be quiet on inputs it cannot trust."""
    store = ((manifest or {}).get("verse") or {}).get("store")
    if not isinstance(store, list):
        return {}
    index = {}
    duplicated = set()
    for entry in store:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("vid")
        if not (isinstance(vid, str) and vid):
            continue
        if vid in index or vid in duplicated:
            duplicated.add(vid)
            index.pop(vid, None)
            continue
        plain_text = entry.get("plain_text")
        index[vid] = plain_text if isinstance(plain_text, str) else ""
    return index


def _carrier_source_text(block):
    """One segpack block's source text as a reader would see it.

    `plain_text` when the adapter supplied it -- every schema-valid manifest
    block carries it, so this is the normal path. `source_html` is a defensive
    fallback, de-tagged: counting raw markup would make an attribute value
    holding the term an occurrence that appears nowhere in the book."""
    plain_text = block.get("plain_text")
    if isinstance(plain_text, str) and plain_text:
        return plain_text
    source_html = block.get("source_html")
    if isinstance(source_html, str) and source_html:
        return _HTML_TAG_RE.sub(" ", source_html)
    return ""


def term_carriers(segpack, draft, apparatus_policy, verse_mode, verse_sources):
    """The (label, source_text, target_text) triples WARN 6 compares for one
    segment -- see this section's header for which carriers qualify and why.

    A carrier whose translated counterpart is absent, non-string or blank is
    omitted rather than reported: that is a coverage defect, and hard check 1
    already owns it. Reporting it here too would say the same thing twice, in
    the advisory lane, about a book that is already failing."""
    carriers = []
    draft_blocks = draft.get("blocks") or {}
    draft_footnotes = draft.get("footnotes") or {}
    draft_verses = draft.get("verses") or {}
    segpack_verses = [v for v in (segpack.get("verses") or []) if isinstance(v, dict)]

    # `mount` is tested for "embedded" and everything ELSE reads as "block" --
    # the exact normalization segpack.py itself applies when it writes this
    # field ("embedded" -> "embedded"; missing, "block", or an unknown adapter
    # value -> "block"). Testing `== "block"` instead would re-derive a
    # STRICTER rule than the producer's and silently compare a standalone
    # verse's placeholder-only draft block as though it were prose.
    placeholder_only_blocks = {
        v.get("parent_block") for v in segpack_verses if v.get("mount") != "embedded"
    }

    def add(label, source_text, target_text):
        if not isinstance(target_text, str) or not target_text.strip():
            return
        if not source_text:
            return
        carriers.append((label, source_text, target_text))

    for block in (segpack.get("blocks") or []):
        if not isinstance(block, dict):
            continue
        block_id = block.get("id")
        if not isinstance(block_id, str) or block_id in placeholder_only_blocks:
            continue
        add(f"blocks[{block_id!r}]", _carrier_source_text(block),
            draft_blocks.get(block_id))

    if apparatus_policy != "preserve_source":
        for footnote in (segpack.get("footnotes") or []):
            if not isinstance(footnote, dict):
                continue
            number = footnote.get("n")
            if number is None:
                continue
            source_text = footnote.get("source_text")
            add(f"footnotes[{str(number)!r}]",
                source_text if isinstance(source_text, str) else "",
                draft_footnotes.get(str(number)))

    if verse_mode != "skip":
        for verse in segpack_verses:
            vid = verse.get("vid")
            if not isinstance(vid, str):
                continue
            source_text = verse_sources.get(vid)
            if source_text is None:
                continue
            rendered_verse = draft_verses.get(vid)
            if not isinstance(rendered_verse, dict):
                continue
            add(f"verses[{vid!r}]", source_text, " ".join(
                rendered_verse[field]
                for field in DELIVERED_VERSE_FIELDS
                if isinstance(rendered_verse.get(field), str)
            ))

    return carriers


def warn_term_drift(seg, terms, apparatus_policy, verse_mode, verse_sources):
    warns = []
    if not terms:
        return warns
    draft, err = load_json(draft_path(seg), f"draft {seg}")
    if err or not isinstance(draft, dict):
        return warns  # already reported as a coverage hard failure
    segpack, err = load_json(segpack_path(seg), f"segpack {seg}")
    if err or not isinstance(segpack, dict):
        return warns  # already reported as a coverage hard failure

    for label, source_text, target_text in term_carriers(
        segpack, draft, apparatus_policy, verse_mode, verse_sources
    ):
        folded_source = _fold_term_text(source_text)
        folded_target = _fold_term_text(target_text)
        for source_form, target_form, folded_form, folded_pin in terms:
            if folded_form not in folded_source:
                continue
            if folded_pin in folded_target:
                continue
            # Normalized as ONE last step over the whole formatted line, for
            # the reason WARN 5 documents: main() prints each warning raw, and
            # a break reaching stderr from either operator-controlled fragment
            # would split one warning across physical lines.
            warns.append(_norm_ws(
                f"[{seg}] TERM-DRIFT {label}: source carries {source_form!r} "
                f"but this carrier's translation has no {target_form!r} -- MANUAL"
            ))
    return warns


# ---------------------------------------------------------------------------
# Whole-project completeness gate -- shells out to select_segments.py, over
# the full manifest.json, no --only-segs restriction.
# ---------------------------------------------------------------------------

def run_completeness_gate(plugin_root_str=None):
    """Returns (completeness_counts, classification_by_seg). FATALs (exit 2)
    if select_segments.py is missing, fails, or does not honor its own
    documented JSON contract -- this gate cannot be silently skipped.

    #412: `plugin_root_str` (this script's own --plugin-root CLI value, or
    None) governs where the SIBLING select_segments.py is resolved from --
    deliberately NEVER derived from this script's own self-anchored
    DURABLE_ROOT/SCRIPTS_DIR (see module docstring for the full tampered-
    copy rationale). When given, resolves as
    `{plugin_root}/assets/scripts/select_segments.py`. select_segments.py
    itself DOES accept --plugin-root (it resolves a further sibling of its
    own, ledger_merge.py) -- so it is forwarded as its RESOLVED value
    (doubled-path fix's sibling defect: forwarding it verbatim would let
    the CHILD -- launched with `cwd=str(DURABLE_ROOT)`, not this process's
    own cwd -- resolve a RELATIVE plugin_root_str against a DIFFERENT base
    than the one THIS script just resolved it against two lines above, so
    parent and child could silently select two different plugin roots;
    `--durable-root` itself is NOT subject to this, since it is always the
    resolved `str(DURABLE_ROOT)` constant, never a raw CLI string), together
    with a synthesized `--durable-root str(DURABLE_ROOT)` (this script has
    no --durable-root of its own; select_segments.py, once resolved via
    --plugin-root, no longer physically sits under DURABLE_ROOT and would
    otherwise self-anchor against the wrong tree). Omitting the flag
    reproduces today's self-anchored sibling lookup unchanged.
    """
    if plugin_root_str is None:
        select_segments_script = SELECT_SEGMENTS_SCRIPT
        resolved_plugin_root_str = None
    else:
        resolved_plugin_root = Path(plugin_root_str).resolve()
        resolved_plugin_root_str = str(resolved_plugin_root)
        select_segments_script = resolved_plugin_root / "assets" / "scripts" / "select_segments.py"

    if not select_segments_script.is_file():
        _fatal(
            f"{select_segments_script} not found -- final_audit.py's "
            f"whole-project completeness gate requires it. See this "
            f"script's own module docstring for select_segments.py's "
            f"required JSON contract."
        )

    # #409: --classify-only. This audit needs the CLASSIFICATION and never
    # translates anything, so it must not be refused when the project
    # contains previously-converged segments -- which is the normal state
    # of a finished book, and exactly the state this audit runs in. Without
    # the flag the new gate would take away final_audit.py's documented
    # "project incomplete" / exit-3 path.
    cmd = [sys.executable, str(select_segments_script), "--allow-empty", "--classify-only"]
    if resolved_plugin_root_str is not None:
        cmd += ["--durable-root", str(DURABLE_ROOT), "--plugin-root", resolved_plugin_root_str]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(DURABLE_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fatal(f"could not run {select_segments_script}: {exc}")

    stdout_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not stdout_lines:
        _fatal(
            f"{select_segments_script} printed no JSON to stdout "
            f"(exit {proc.returncode}); stderr: {proc.stderr.strip()}"
        )
    try:
        payload = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        _fatal(
            f"{select_segments_script}'s last stdout line was not valid "
            f"JSON: {exc}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        _fatal(
            f"{select_segments_script} reported failure: "
            f"{error or '(no error message)'}"
        )

    counts = payload.get("counts")
    classification = payload.get("classification")
    if not isinstance(counts, dict) or not isinstance(classification, dict):
        _fatal(
            f"{select_segments_script}'s JSON output is missing the "
            f"required 'counts'/'classification' objects -- see this "
            f"script's own module docstring for the required contract."
        )

    completeness_counts = {}
    for cat in COMPLETENESS_CATEGORIES:
        value = counts.get(cat)
        if not isinstance(value, int) or value < 0:
            _fatal(
                f"{select_segments_script}'s 'counts' is missing a valid "
                f"non-negative integer for category {cat!r}"
            )
        completeness_counts[cat] = value

    return completeness_counts, classification


# ---------------------------------------------------------------------------
# #409 Step 2: stale/ever-converged sentinel carve-out -- a 'stale'
# classification alone must not keep blocking the whole-project completeness
# gate when the segment's translation already converged and is protected
# from silent re-translation by #409 Step 1 (select_segments.py's own
# re-translate refusal). See this module's own docstring ("#409 Step 2
# carve-out") for the full rationale.
# ---------------------------------------------------------------------------

def ever_converged_path(seg):
    """The durable 'this segment has converged at least once' sentinel.
    WRITTEN by ledger_update.py:mark_ever_converged (the single place
    convergence is recorded) and READ by three scripts: select_segments.py's
    #409 Step 1 re-translate gate, backfill_ever_converged.py's
    already_sentineled scan, and this module's own carve-out below -- see
    that writer's docstring for why a separate durable file exists rather
    than reading the (mutable) ledger status. Filename convention restated
    here rather than imported for the bundle-hash reason spelled out in
    classify_ever_converged_sentinel() below -- NOT for the "no shared lib
    between self-contained scripts" convention, which is already false in
    this codebase.

    The PRESENCE TEST, however, is no longer restated: all four scripts now
    route it through one duplicated-verbatim predicate,
    classify_ever_converged_sentinel() below, because the four `.exists()`
    call sites were free to disagree with the writer and with each other,
    and two of them did."""
    return SEGMENTS_DIR / f".ever_converged.{seg}"


# ---------------------------------------------------------------------------
# The shared sentinel-presence predicate. This block is an EXACT duplicate of
# the copy in the other three sentinel scripts (search `SENTINEL_ABSENT` in
# ledger_update.py, select_segments.py and backfill_ever_converged.py) -- see
# classify_ever_converged_sentinel()'s docstring for why it is duplicated
# rather than imported, and which test pins the four copies together.
# ---------------------------------------------------------------------------

SENTINEL_ABSENT = "absent"
SENTINEL_PRESENT = "present"
SENTINEL_AMBIGUOUS = "ambiguous"


def _sentinel_entry_kind(mode: int) -> str:
    """A human name for the st_mode of whatever occupies a sentinel path --
    it goes straight into an operator-facing message, which has to say what
    is actually sitting there before it can ask anyone to fix it."""
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISBLK(mode):
        return "a block device"
    if stat.S_ISCHR(mode):
        return "a character device"
    return f"a non-regular entry (st_mode {stat.S_IFMT(mode):#o})"


def classify_ever_converged_sentinel(path, *, dir_fd=None) -> "tuple[str, str]":
    """Three-state classification of the `.ever_converged.<seg>` entry at
    `path`: `(SENTINEL_ABSENT|SENTINEL_PRESENT|SENTINEL_AMBIGUOUS, detail)`.

    THE SHARED PREDICATE. Every script that asks whether a segment has ever
    converged calls this, and all four must agree on it:
    ledger_update.py's `mark_ever_converged()` (the only writer),
    select_segments.py's #409 Step 1 dispatch gate,
    final_audit.py's `count_stale_previously_converged()` carve-out, and
    backfill_ever_converged.py's `already_sentineled` scan.

    DUPLICATED RATHER THAN IMPORTED because importing it would be a live
    hazard -- NOT because of the "no shared lib between self-contained
    scripts" convention, which is already false here (canon_validate.py and
    glossary_batch_plan.py import canon_senses.py; scaffold_setup.py imports
    cache_key.py). The real reason: ledger_update.py is a
    PLUGIN_BUNDLE_MEMBERS entry, and cache_key.py:100-107 records that that
    tuple is a literal byte-hash allowlist to which a TRANSITIVE IMPORT IS
    INVISIBLE -- which is why canon_senses.py had to be registered
    explicitly once two members imported it. A shared module would put this
    predicate's bytes outside the hash meant to cover them, so WEAKENING
    this guard would no longer move plugin_bundle_hash, and every durable
    root scaffolded beforehand would go on trusting it: the exact
    false-green cache_key.py:114-118 names. Consolidation stays possible --
    it just has to register the new module in PLUGIN_BUNDLE_MEMBERS in the
    same commit.

    What keeps the four copies honest is ENFORCEMENT, not discipline. A
    remembered convention rots -- this docstring's own first version cited
    the false one -- while a test that fails loudly does not.
    tests/select_segments.test.py's
    test_sentinel_predicate_is_identical_in_all_four_scripts pins the copies
    byte for byte and across the state matrix; its
    test_exactly_these_four_scripts_participate_in_the_sentinel_contract
    fails when a fifth copy appears or one of the four goes away.

    Why three states, and why not `Path.exists()`. `exists()` answers the
    wrong question three ways, and NOT all of them in the same direction --
    an earlier draft of this docstring said "twice over, and BOTH point at
    absent", which is the claim the CHANGELOG had to correct. Two of the
    three do point at "absent", and that is the direction that authorizes
    destroying converged work:

      1. It FOLLOWS symlinks, so a DANGLING symlink named as the sentinel
         reads as absent -- while the writer's `os.open(O_CREAT|O_EXCL)` gets
         EEXIST from that same symlink and reports the segment successfully
         marked. That split is the whole finding: a segment recorded as
         converged that the gate then sees as unprotected and retranslates.
         Verified on this project's Python (3.14.6): `exists()` -> False,
         `os.open` -> FileExistsError, for one and the same dangling link.
      2. Since Python 3.13 `exists()` swallows EVERY OSError and returns
         False, so an EACCES/ESTALE/EIO on the lookup is reported as "this
         segment never converged". Verified on 3.14.6: with an unreadable
         parent directory `exists()` returns False while `lstat()` raises
         EACCES. (On 3.8-3.12 the same call re-raised for EACCES but still
         swallowed ELOOP/ENOTDIR/EBADF -- so no supported version answers
         this correctly, and the version-dependence is itself a reason not
         to route a data-loss guard through `exists()`.)
      3. In the OTHER direction: a DIRECTORY at the marker's path is
         `exists() == True`, so `exists()` reports converged a segment the
         writer never marked. That one cannot destroy finished work, which is
         why it went unnoticed -- but it is the reason "exists() at least
         fails safe in one direction" is false, and the reason the fix is a
         third state rather than a flipped default.

    So: only ENOENT means absent, and it is determined by catching
    FileNotFoundError rather than by comparing `exc.errno`, so the verdict
    never depends on an errno that may be None. `lstat`, deliberately not
    `stat` -- a symlink is not something `mark_ever_converged()` can have
    (its O_CREAT|O_EXCL open refuses to write through one), so following a
    link would only ask the question about some unrelated file. Either way
    only the final `.ever_converged.<seg>` component is left unresolved:
    WITHOUT `dir_fd` the PARENT components still resolve normally, so a
    project whose whole `segments/` directory is a symlink is unaffected;
    WITH `dir_fd` there are no parent components left to resolve, because
    the caller already resolved them once, when it opened the descriptor.

    `dir_fd` -- OPTIONAL, and today exactly one caller passes it:
    backfill_ever_converged.py's census. Omitted (every other caller), the
    lookup resolves the whole pathname afresh, which is the right thing for
    a reader that holds nothing open. Passed, the BASENAME is looked up
    relative to that descriptor instead, and `segments/` is not resolved by
    pathname at all. The difference matters only for a caller that already
    HOLDS the directory open and acts on its census afterwards, which is
    exactly that one: it opens `segments/` once, does every write relative
    to the descriptor, and samples directory identity at the end. A census
    resolving the pathname afresh could therefore classify entries in a
    DIFFERENT directory than the one being written to -- re-point
    `segments/` at B for the length of the census and back to A before the
    run ends, and B's sentinel is reported as A's protection while the
    final identity sample compares A to A and agrees. Reproduced by review,
    not theorised. Binding the census to the descriptor removes that
    interleaving with no locking protocol at all, because the descriptor is
    already held; a caller that holds none gains nothing here and passes
    None.

    Anything that is neither ENOENT nor a regular file is AMBIGUOUS: it MAY
    be a converged segment whose sentinel this process cannot see. Each
    caller then maps AMBIGUOUS to ITS OWN work-preserving side, and that is
    deliberately NOT the same action in all four: the writer and the
    dispatch gate REFUSE (never destroy or mis-record converged work), while
    final_audit.py's carve-out COUNTS it (never declare a converged book
    incomplete and therefore undeliverable) and backfill's scan reports it
    unprotected (never claim protection it did not verify). One predicate,
    four deliberate mappings -- see each call site's own comment. The
    asymmetry is the reason a false "absent" is the unacceptable answer
    everywhere: it costs a finished translation, or a finished book.
    """
    try:
        # `path.name` is the basename and the descriptor is its parent, so
        # the `dir_fd` branch resolves no part of `segments/` by pathname.
        # `os.lstat` keeps `follow_symlinks` off exactly as `Path.lstat`
        # does, so the FINAL component stays unresolved either way and both
        # branches raise the same exceptions into the same handlers below.
        st = path.lstat() if dir_fd is None else os.lstat(path.name, dir_fd=dir_fd)
    except FileNotFoundError:
        return (SENTINEL_ABSENT, "")
    except OSError as exc:
        # `OSError.errno` is typed `int | None` and genuinely can be None. A
        # missing errno is the LEAST informative failure there is, so it
        # lands on the ambiguous side like every other non-ENOENT outcome --
        # never silently treated as "some other errno", and above all never
        # as absence. The ENOENT verdict above does not consult `errno` at
        # all (FileNotFoundError IS ENOENT by construction), so a None errno
        # can never reach it, which is why this branch can be a plain guard
        # rather than a three-way comparison.
        if exc.errno is None:
            return (SENTINEL_AMBIGUOUS, f"lstat failed with no errno: {exc}")
        code = errno.errorcode.get(exc.errno, f"errno {exc.errno}")
        return (SENTINEL_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (SENTINEL_PRESENT, "")
    return (
        SENTINEL_AMBIGUOUS,
        f"the entry is {_sentinel_entry_kind(st.st_mode)}, not a regular file",
    )


# The 3 of cache_key.py's 15 CACHE_KEY_FIELD_ORDER fields whose drift can
# NEVER, by itself, change what a converged segment's own translated prose
# should say -- read directly from cache_key.py (never taken on faith):
#
#   - plugin_bundle_hash -- sha1 of PLUGIN_BUNDLE_MEMBERS, the pipeline's
#     validate/orchestrate/ledger SCRIPTS (validate_draft.py,
#     canon_validate.py, cache_key.py, draft_sha1.py,
#     review_artifact_check.py, ledger_update.py, review_ready.py,
#     resume_setup.py, glossary_batch_plan.py, codex_job.py,
#     canon_senses.py, fetch_citation.py, + 2 workflow templates).
#     Deliberately excludes translate_TASK.md/review_TASK.md (prompt_hash's
#     own membership, below) and style_bible.md (style_contract_hash) --
#     this bundle is tooling/validation code, never a translation
#     instruction. This is the field #409's own motivating scenario moves:
#     a plugin upgrade flips it for every converged segment at once.
#   - schema_hash -- sha1 of draft/review/segpack JSON *schema* files:
#     structural-validity rules, not translated content or instructions.
#   - derivation_bundle_hash -- sha1 of bootstrap_names.py/segpack.py's own
#     CODE bytes (the segpack-BUILDING tool), never the segpack's data
#     itself (references/ledger-and-resumability.md's own "derivation_bundle_hash"
#     entry: "this one uses simple sorted-concatenation ... since it's just
#     script bytes, not swappable file identities"). It is one of
#     select_segments.py's DERIVATION_STATE_FIELDS (with particle_config_hash/
#     source_extraction_hash/source_input_hash), so a mismatch here alone
#     does NOT immediately classify 'stale': classify_converged_segment()
#     (select_segments.py:~730) first checks this segment's OWN segpack's
#     `generation_hashes.derivation_bundle_hash` against the CURRENT value.
#     Only reaching plain 'stale' (rather than 'blocked_needs_regeneration')
#     means the segpack has ALREADY been (re)stamped to match today's
#     bootstrap_names.py/segpack.py bytes -- ledger-and-resumability.md's own
#     "Derivation-state gate" section calls this state "safe to re-dispatch"
#     and documents `segpack_{seg}.json`'s generation_hashes as "copied
#     directly from whatever manifest.json/canon.json currently contain at
#     segpack-generation time (never independently recomputed --
#     transitively correct proof of the whole upstream chain)". If that
#     (re)stamping event had actually changed this segment's OWN segpack
#     content -- blocks, verses, footnotes, or referenced canon terms --
#     that would independently move input_sha1/verse_map_hash/
#     note_map_hash/used_terms_hash: cache_key.py's PER_SEGMENT_FIELDS is
#     the COMPLETE set of segpack-derived cache-key fields (no 5th
#     per-segment field exists to carry undetected drift), all 4 of which
#     are in the CONTENT-affecting set below and would block the carve-out
#     on their own (mismatched_fields must be a subset of this allowlist,
#     so any one of them appearing blocks the WHOLE segment). So
#     derivation_bundle_hash appearing ALONE means the segpack was
#     re-stamped (possibly re-run) but this segment's own derived content
#     came out byte-identical -- the draft, translated against the earlier
#     (now content-identical) segpack, is still valid. The one segpack
#     dimension input_sha1 does NOT hash is a block's own `id` (only its
#     order_index-sorted TEXT) -- a hypothetical re-ID without a text
#     change would slip this specific field, but a draft/segpack key
#     mismatch there is independently caught, unconditionally, by
#     hard_check_coverage (validate_draft.py's own structural key-match),
#     which forces hard_failures>0 -> exit 1 regardless of this carve-out
#     -- so even that gap fails safe at the system level, not just this
#     function's.
#
#     NOT extended to the other 3 DERIVATION_STATE_FIELDS
#     (particle_config_hash/source_extraction_hash/source_input_hash)
#     despite the same per-segment-completeness argument applying to them
#     structurally too: those three are DATA/CONFIG whose entire purpose is
#     to shape what gets extracted (a particle-list edit, an adapter_config
#     segmentation-threshold change, or the raw source book text itself) --
#     an operator touches them BECAUSE they want different extraction
#     output, unlike a code-bytes fingerprint that moves on innocuous
#     refactors having zero effect on any segment. Keeping them blocking is
#     the fail-safe call the lead's brief already settled for those three.
#
# Every other field bears directly on what the translated text should be,
# and must keep blocking (fail-safe: an unrecognized/future field name is
# simply absent from this allowlist, so it blocks by construction):
#
#   - input_sha1 / source_input_hash -- the segpack's own extracted source
#     text, and the raw source file's own bytes, respectively: literally
#     the text being translated.
#   - style_contract_hash / prompt_hash -- style_bible.md's STYLE_CONTRACT
#     section and translate_TASK.md/review_TASK.md: the operator's own
#     translation instructions, read in full on every translate/review
#     call (style_bible.template.md's own comment: "bump when
#     style_contract changes in a way that must invalidate every
#     already-converged segment").
#   - used_terms_hash -- the canon.json glossary entries this segment
#     actually references: a name's canonical form changing must be able
#     to flag this segment as needing a matching update.
#   - profile_semantics_hash -- source/target language, verse_policy mode,
#     apparatus_policy, untranslated_sentinel: the translation POLICY
#     itself.
#   - agent_config_hash -- effort/max_fix_rounds/model: an operator
#     deliberately raising effort/switching model to fix a quality problem
#     must not have already-converged segments silently exempted from
#     that quality bar.
#   - particle_config_hash / source_extraction_hash -- both
#     DERIVATION_STATE_FIELDS (cache_key.py): a mismatch here, even once
#     the segpack has "caught up" (so it's classified plain 'stale' rather
#     than 'blocked_needs_regeneration'), means the upstream
#     extraction/name-recognition config that produced the segpack the
#     draft was translated against has since moved -- unlike
#     derivation_bundle_hash (code bytes only), these directly gate
#     WHICH/HOW source content and candidate names were extracted.
#   - pipeline_version -- NOT in cache_key.py's own docstring examples the
#     lead cited, but IS one of the 15 CACHE_KEY_FIELD_ORDER fields (read
#     verbatim from profile.yml's project.pipeline_version, never hashed).
#     style_bible.template.md's own comment ties bumping it directly to
#     "invalidate every already-converged segment" -- it is the operator's
#     own deliberate lever for exactly that, so it must block.
#
# See also: a 'stale' classification whose stale_reason includes
# 'draft_sha1_mismatch' (the on-disk draft no longer matches its own
# ledger fragment's reviewed_draft_sha1 -- a hand-edit since review, NOT a
# cache-key field drift) must NEVER be carved out regardless of
# mismatched_fields -- see count_stale_previously_converged() below.
SAFE_STALE_CARVEOUT_FIELDS = frozenset(
    {"plugin_bundle_hash", "schema_hash", "derivation_bundle_hash"}
)


def count_stale_previously_converged(classification, sentinel_states=None):
    """How many of the whole-project completeness gate's own 'stale'
    segments carry the #409 ever-converged sentinel AND went stale for a
    reason that can never, by itself, change the segment's own translated
    prose -- i.e. are already finalized translations that only look stale
    because a MACHINERY-only cache-key field moved out from under them
    (most commonly plugin_bundle_hash, on every converged segment at once,
    after a plugin upgrade), not because a content-affecting input (the
    source text, the style bible, the prompt templates, canon terms,
    translation policy, engine config, or the source-extraction/particle
    config) actually changed and was never re-applied.

    Fail-safe direction, each independently sufficient to keep a segment
    OUT of this count (still blocks project_complete exactly as before):
      - no sentinel;
      - `stale_reason` is anything other than exactly
        `['cache_key_mismatch']` -- in particular 'draft_sha1_mismatch'
        (a hand-edit since review, wholly unrelated to cache-key drift,
        already independently caught as a HARD failure by
        hard_check_stale_review -- but never silently reported as a
        deliverable carve-out here);
      - `mismatched_fields` is empty, not a list, or contains ANY field
        not in SAFE_STALE_CARVEOUT_FIELDS (see that constant's own
        comment for the full field-by-field reasoning) -- an unrecognized
        or future cache_key field is absent from the allowlist by
        construction and so blocks, never silently becomes deliverable.

    `classification` is select_segments.py's own per-segment report
    (every manifest segment; the value is an object per this module's own
    "select_segments.py JSON contract" docstring section) -- the SAME
    dict `completeness_counts['stale']` is tallied from on the
    select_segments.py side, so this count can never exceed
    completeness_counts['stale']."""
    # Either the caller hands us the shared scan (main() does, so the count and
    # the diagnostic below cannot disagree) or we make our own. Never a
    # per-segment fallback: a missing key must raise, not silently re-stat, or
    # a drifted scan and a correct one would behave identically.
    if sentinel_states is None:
        sentinel_states = scan_sentinel_states(classification)
    n = 0
    for seg, entry in classification.items():
        category = entry.get("category") if isinstance(entry, dict) else entry
        if category != "stale":
            continue
        stale_reason = entry.get("stale_reason") if isinstance(entry, dict) else None
        if stale_reason != ["cache_key_mismatch"]:
            continue  # e.g. draft_sha1_mismatch -- never carved out
        mismatched_fields = entry.get("mismatched_fields") if isinstance(entry, dict) else None
        if not isinstance(mismatched_fields, list) or not mismatched_fields:
            continue
        if not all(field in SAFE_STALE_CARVEOUT_FIELDS for field in mismatched_fields):
            continue
        # AMBIGUOUS carves out, exactly like PRESENT, and only a clean ENOENT
        # does not. This is the OPPOSITE ACTION from the same predicate's other
        # callers, and it is the same principle: take the branch that cannot
        # destroy finished work.
        #
        # Here "refuse" would be the destructive branch. A segment only reaches
        # this line if the ledger ALREADY recorded it converged -- that is what
        # select_segments.py's `stale` category means -- and if the only reason
        # it went stale is a cache-key field that cannot change what the prose
        # should say. So the sentinel is corroboration, not the sole evidence.
        # Reading a dangling symlink or an EACCES as "absent" would drop the
        # segment out of the carve-out, leave stale_blocking > 0, and make
        # compute_project_complete() false -- a finished book declared
        # undeliverable over an unreadable dotfile. And it would be
        # unresolvable: the operator's only route to a fresh sentinel is a
        # retranslate, which select_segments.py's Step 1 gate now (correctly)
        # refuses for this very segment. Sentinel respected in one place and
        # not the other is how "tokens saved, book undeliverable" happens.
        #
        # The ambiguity is never silent -- and that guarantee is why this
        # function and collect_ambiguous_sentinels() below must read ONE scan
        # rather than each stat the path themselves. With two independent
        # reads, a sentinel that vanishes between them is counted as carved out
        # here and reported by nothing: `counted_as_converged=1`, no diagnostic,
        # exactly the silence this comment promises cannot happen. The reverse
        # order warns the operator about an entry that was never counted.
        # Neither is reachable once both consume `sentinel_states`.
        state, _detail = sentinel_states[seg]
        if state != SENTINEL_ABSENT:
            n += 1
    return n


# ---------------------------------------------------------------------------
# #533: the SECOND, opt-in subtraction. style_contract_hash is deliberately
# NOT added to SAFE_STALE_CARVEOUT_FIELDS above -- that set means "can never
# change what the prose should say", which is false for the style contract,
# and it is read for two other questions besides this one (assemble.py's
# assembly gate and select_segments.py's D6 report). Instead this is a
# separately named acceptance path, reached only when the project's own
# profile.yml declares it, and it names every segment it admits rather than
# counting them.
# ---------------------------------------------------------------------------

CONTRACT_ONLY_STALE_FIELD = "style_contract_hash"


def admit_contract_only_stale(profile):
    """Reads profile.yml's `validation.admit_contract_only_stale` (#533).

    True for a LITERAL `True` and nothing else -- an absent or non-dict
    `validation` block, an absent key, `false`, `null`, the string "true" and
    the integer 1 all read as False. `is True` rather than truthiness, so `1`
    (which compares equal to True) cannot become consent. Fail-closed:
    forgetting the declaration refuses, exactly as before this field existed.

    Restated in final_audit.py and validate_assembled.py rather than imported,
    and NOT hoisted into validate_draft.py -- which all three already import
    as `vd`, and which already owns load_profile(), so it is the obvious home.
    It is the wrong one: `validate_draft.py` is the first member of
    cache_key.py's PLUGIN_BUNDLE_MEMBERS and these four gate scripts are not
    members at all, so hosting the reader there would move
    plugin_bundle_hash for every project -- mass-invalidating every converged
    segment, which is the exact cost #533 exists to relieve. select_segments.py
    holds the fourth SAFE_STALE_CARVEOUT_FIELDS copy and does not import `vd`
    either, for the same reason. The three copies are behaviourally identical
    (the signature and this docstring differ) and are driven over one shared
    table by tests/contract_stale_admission.test.py, which pins behaviour, not
    source identity."""
    validation = (profile or {}).get("validation")
    if not isinstance(validation, dict):
        return False
    return validation.get("admit_contract_only_stale") is True


def collect_stale_contract_admitted(classification, sentinel_states):
    """The sorted list of 'stale' segments admitted by #533's opt-in path:
    already-converged units whose ONLY non-machinery cache-key movement is
    style_contract_hash.

    A LIST, not a count, because what is being recorded is an operator act --
    "these segments shipped without being judged against the current
    contract" -- and a number cannot be checked against the book later.

    Every condition is the fail-safe direction, each independently sufficient
    to keep a segment OUT (so it keeps blocking project_complete exactly as
    before):
      - `stale_reason` is anything other than exactly `['cache_key_mismatch']`
        -- in particular 'draft_sha1_mismatch', which is how this function
        enforces "the draft has not changed since the review that converged
        it" without recomputing a single sha1: select_segments.py puts that
        reason there precisely when the on-disk draft no longer matches its
        own reviewed_draft_sha1;
      - `mismatched_fields` is empty, not a list, or has a non-string member;
      - style_contract_hash is NOT among the moved fields (that record belongs
        to the #409 machinery-only count, or to neither);
      - some OTHER moved field is outside SAFE_STALE_CARVEOUT_FIELDS -- the
        source text, the prompts, canon terms, engine/extraction config, or
        an unrecognised future field, which is outside by construction;
      - the `.ever_converged` sentinel is ABSENT.

    Membership is tested as a SET, so a hand-edited runs/ledger.json carrying
    `["style_contract_hash", "style_contract_hash"]` -- which
    ledger.schema.json permits, having minItems but no uniqueItems -- reaches
    the same verdict here as in assemble.py's own gate. Two gates disagreeing
    about one record is the failure mode this whole feature has to avoid.

    AMBIGUOUS carves out exactly like PRESENT, and only a clean ENOENT does
    not -- identical to count_stale_previously_converged() above, for the
    identical reason (see its comment: refusing is the destructive branch
    here, and unrecoverably so)."""
    allowed = SAFE_STALE_CARVEOUT_FIELDS | {CONTRACT_ONLY_STALE_FIELD}
    admitted = []
    for seg, entry in classification.items():
        if not isinstance(entry, dict) or entry.get("category") != "stale":
            continue
        if entry.get("stale_reason") != ["cache_key_mismatch"]:
            continue
        mismatched = entry.get("mismatched_fields")
        if not isinstance(mismatched, list) or not mismatched:
            continue
        if not all(isinstance(f, str) for f in mismatched):
            continue
        moved = set(mismatched)
        if CONTRACT_ONLY_STALE_FIELD not in moved or not moved.issubset(allowed):
            continue
        state, _detail = sentinel_states[seg]
        if state != SENTINEL_ABSENT:
            admitted.append(seg)
    return sorted(admitted)


def scan_sentinel_states(classification):
    """One authoritative read of every classified segment's `.ever_converged`
    entry, as `{seg: (state, detail)}`.

    Exists so the carve-out count and the operator diagnostic can never
    disagree about the SAME path. They ask different questions of the same
    sentinel -- "is it absent?" and "is it ambiguous?" -- and answering each
    with its own `stat` makes the pair non-atomic for no benefit.

    What this does NOT promise: the scan is not atomic ACROSS segments. A
    sentinel written while it runs may be seen by a later segment's read and
    not an earlier one. That is inherent to auditing a live tree and it is
    harmless here, because every consumer treats segments independently; the
    defect this closes was two answers about ONE segment."""
    return {
        seg: classify_ever_converged_sentinel(ever_converged_path(seg))
        for seg in sorted(classification)
    }


def collect_ambiguous_sentinels(classification, sentinel_states=None):
    """Every segment in `classification` whose `.ever_converged.<seg>` entry is
    neither absent nor a regular file, as `[{"seg": ..., "detail": ...}]`.

    Purely diagnostic -- it never changes a count or an exit code. It exists
    because count_stale_previously_converged() above deliberately treats an
    ambiguous sentinel as carved out: that is the right call for deliverability
    (see its comment) but it would otherwise make a broken sentinel path
    completely invisible, and the operator is the only one who can repair it.
    Reported on stderr rather than in the JSON summary because
    final-audit-summary.schema.json is `additionalProperties: false`, so a new
    field means editing it, and its bytes are hashed by resume_setup.py's
    `_schemas_dir_hash()` (which globs EVERY schemas/*.schema.json) -- a
    diagnostic is not worth moving a project's resume identity to add a field.
    NOT schema_hash, which an earlier version of this paragraph claimed:
    `cache_key.compute_schema_hash()` hashes only the project-local
    draft/review/segpack schemas, so editing this one stales no converged
    segment at all. The cost is real but smaller than it was written to be --
    an interrupted run restarting at the project's next Step 0a re-scaffold,
    never a re-translation.

    Scans EVERY classified segment, not only the carve-out candidates: a
    broken sentinel on a reusable or converged segment is the same latent
    problem, and it will bite at the next cache-key move rather than now."""
    if sentinel_states is None:
        sentinel_states = scan_sentinel_states(classification)
    out = []
    for seg in sorted(classification):
        state, detail = sentinel_states[seg]
        if state == SENTINEL_AMBIGUOUS:
            out.append({"seg": seg, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# Frontback coverage report -- advisory, never exit-code-gating.
# ---------------------------------------------------------------------------

def build_frontback_coverage(classification_by_seg):
    manifest, err = load_json(MANIFEST_PATH, "manifest.json")
    if err:
        _fatal(err)
    if not isinstance(manifest, dict):
        _fatal(f"manifest.json at {MANIFEST_PATH} did not parse to an object")

    coverage = []
    for entry in (manifest.get("frontback") or []):
        if not isinstance(entry, dict):
            continue
        fb_id = entry.get("id")
        decision = entry.get("decision")
        if decision == "translate":
            resolved = classification_by_seg.get(fb_id)
            # select_segments.py's real classification value is an object,
            # {"category": CATEGORY, ...} -- unwrap to the bare CATEGORY
            # string per final-audit-summary.schema.json's own
            # "status": {"type": ["string", "null"]} contract. Fall back to
            # the raw value (or None if unresolved) for anything else so a
            # malformed/unexpected shape never crashes this advisory report.
            status = (
                resolved.get("category")
                if isinstance(resolved, dict)
                else resolved
            )
        else:
            status = None
        coverage.append({"id": fb_id, "decision": decision, "status": status})
    return coverage


# ---------------------------------------------------------------------------
# Project completeness -- #409 Step 2 carve-out applied.
# ---------------------------------------------------------------------------

def compute_project_complete(
    completeness_counts, stale_previously_converged, stale_contract_admitted=0
):
    """Pure, unit-testable without a durable root -- mirrors
    completeness_exit_code()'s own pattern. True iff every non-'stale'
    completeness category is 0 AND every 'stale' segment is accounted for
    by the #409 ever-converged sentinel (completeness_counts['stale'] -
    stale_previously_converged == 0). completeness_counts['stale'] itself
    is never mutated by this function -- see
    count_stale_previously_converged()'s own docstring and this module's
    docstring ("#409 Step 2 carve-out") for why a 'stale' segment without
    the sentinel must still block completeness exactly as before (the
    fail-safe direction).

    `stale_contract_admitted` (#533) is the SECOND subtraction: the number of
    'stale' segments the operator has explicitly declared shippable because
    only the style contract moved beneath them. It defaults to 0, so every
    existing caller -- and every project that has not declared it -- gets
    exactly the arithmetic above. The two subtracted populations are disjoint
    by construction: the #409 one requires every moved field to be inside
    SAFE_STALE_CARVEOUT_FIELDS, the #533 one requires style_contract_hash to
    be among the moved fields, and that field is not in that set."""
    non_stale_clear = all(
        v == 0 for cat, v in completeness_counts.items() if cat != "stale"
    )
    stale_blocking = (
        completeness_counts.get("stale", 0)
        - stale_previously_converged
        - stale_contract_admitted
    )
    return non_stale_clear and stale_blocking == 0


# ---------------------------------------------------------------------------
# Exit code -- fail-closed on both hard defects AND project incompleteness.
# ---------------------------------------------------------------------------

def completeness_exit_code(hard_failures, project_complete):
    """Pure, unit-testable without a durable root. hard_failures keeps
    priority over incompleteness: a converged draft with a real coverage/
    stale-review defect exits 1 even if the wider project is also
    incomplete -- see #208."""
    if hard_failures:
        return 1                     # hard defects in converged drafts
    if not project_complete:
        return 3                     # converged drafts clean, project isn't
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "W7 Final audit -- the last deterministic gate before W8 "
            "Deliver. See this file's own module docstring."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "#412: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling select_segments.py "
            "script the whole-project completeness gate shells out to, as "
            "{PATH}/assets/scripts/select_segments.py -- deliberately "
            "NEVER derived from this script's own self-anchored durable "
            "root, because ${durable_root}/scripts/ is writable by the "
            "codex process this gate protects (codex_job.py grants "
            "--write over the whole durable root), so resolving the "
            "checker from inside the tree it checks would let a tampered "
            "copy pass itself. select_segments.py itself DOES accept "
            "--plugin-root, so it is forwarded verbatim, together with a "
            "synthesized --durable-root. Optional; omit for today's "
            "self-anchored sibling lookup."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    converged = load_converged_fragments()

    hard_details = []
    warn_details = []

    coverage_failures, coverage_detail = hard_check_coverage(converged)
    hard_details.extend(coverage_detail)

    stale_review_failures, stale_detail = hard_check_stale_review(converged)
    hard_details.extend(stale_detail)

    # Rollup invariant, enforced procedurally (not merely schema-expressible):
    # hard_failures MUST equal coverage_failures + stale_review_failures.
    hard_failures = coverage_failures + stale_review_failures

    # WARN checks: A1 cross-segment once; A2 (link-graph)/A3 (foreign-scan)/
    # A4 (verse-structure)/#520 (forbidden-pattern)/#199 (term-consistency) per
    # converged segment.
    warn_details.extend(warn_glossary_diff(converged))

    stopwords_lower = frozenset()
    try:
        profile = vd.load_profile()
        particle_config = profile["source"]["language"]["particle_config"]
        lang = bn.load_language_config(particle_config)
        stopwords_lower = frozenset(
            _fold_source_marks(w).lower() for w in lang.stopwords
        )
    except (bn.BootstrapNamesError, KeyError, TypeError) as exc:
        print(
            f"WARNING: could not resolve source-language stopwords for the "
            f"foreign-remainder WARN check ({exc}) -- skipping that check "
            f"only, all other checks are unaffected",
            file=sys.stderr,
        )

    # #520. Compiled ONCE, before the loop: a declaration that does not
    # compile is an operator-level fact about this run, not a per-segment one,
    # so its WARN must appear exactly once however many segments converged.
    #
    # Its own vd.load_profile(), not the one the stopwords block above just
    # read -- for the same reason #533's reader takes its own further down:
    # that read sits inside a try which downgrades any failure to "skip the
    # foreign-remainder check", and borrowing it would let a stopwords problem
    # silently cancel the operator's forbidden-pattern declaration while the
    # audit still reported a clean WARN lane. A malformed profile cannot reach
    # here -- hard_check_coverage()'s own unguarded load raises first.
    #
    # ONE read, shared by BOTH operator-declaration lanes (#199). A second call
    # here would buy no authority and would let the two lanes observe different
    # bytes of a file an operator edits while the audit runs.
    operator_profile = vd.load_profile()
    compiled_patterns, pattern_decl_warns = compile_forbidden_patterns(
        forbidden_patterns(operator_profile)
    )
    warn_details.extend(pattern_decl_warns)

    # #199. Both policies are read from the SAME profile, because both decide
    # the same thing: whether a carrier is TRANSLATED at all. A missing or
    # malformed block simply resolves to None, which excludes nothing -- the
    # fail-open direction is right for an advisory lane, and Step 0 refuses a
    # profile that lacks either field anyway.
    terms = declared_terms(operator_profile)
    footnotes_config = operator_profile.get("footnotes")
    apparatus_policy = (
        footnotes_config.get("apparatus_policy")
        if isinstance(footnotes_config, dict) else None
    )
    verse_config = operator_profile.get("verse_policy")
    verse_mode = verse_config.get("mode") if isinstance(verse_config, dict) else None
    # The verse SOURCE text lives in manifest.json, not in any segpack. Read
    # once, and only when something is actually pinned; an unreadable manifest
    # leaves the verse lane silent rather than aborting the audit, exactly as an
    # unreadable draft does (build_frontback_coverage() below owns the fatal).
    verse_sources = {}
    if terms:
        manifest, manifest_err = load_json(MANIFEST_PATH, "manifest.json")
        if not manifest_err and isinstance(manifest, dict):
            verse_sources = verse_source_index(manifest)

    for seg in sorted(converged):
        warn_details.extend(warn_link_graph(seg))
        warn_details.extend(warn_foreign_remainder(seg, stopwords_lower))
        warn_details.extend(warn_verse_structure(seg))
        warn_details.extend(warn_forbidden_patterns(seg, compiled_patterns))
        warn_details.extend(warn_term_drift(
            seg, terms, apparatus_policy, verse_mode, verse_sources
        ))

    warnings_count = len(warn_details)

    completeness_counts, classification_by_seg = run_completeness_gate(args.plugin_root)
    # #409 Step 2: a 'stale' segment that also carries the durable
    # ever-converged sentinel is carved out of the completeness gate -- see
    # count_stale_previously_converged()'s and compute_project_complete()'s
    # own docstrings, and this module's docstring ("#409 Step 2 carve-out"),
    # for the full rationale. completeness_counts itself is left unchanged
    # (still the raw select_segments.py count); only project_complete is
    # computed net of the carve-out.
    # ONE read of the sentinel tree, shared by the count and the diagnostic
    # below. They previously stat'd each path independently, which made the
    # "ambiguity is never silent" guarantee false under concurrent mutation:
    # AMBIGUOUS then ABSENT counts a segment as carved out and reports nothing.
    sentinel_states = scan_sentinel_states(classification_by_seg)
    stale_previously_converged = count_stale_previously_converged(
        classification_by_seg, sentinel_states
    )

    # Diagnostic only -- deliberately computed BEFORE the summary so it prints
    # even when the audit goes on to pass. count_stale_previously_converged()
    # treats an ambiguous sentinel as carved out (see its own comment for why
    # that is the non-destructive branch here), which is right for the verdict
    # and would otherwise make a broken sentinel path completely invisible.
    # Never folded into hard_failures/warnings: this is a filesystem problem
    # for an operator to repair, not a translation defect, and inflating the
    # warning count would make it look like the book has an issue.
    ambiguous_sentinels = collect_ambiguous_sentinels(
        classification_by_seg, sentinel_states
    )

    # #533: read the declaration HERE, where it is used, rather than reusing
    # either of the two profile reads this run already performs. Deliberate:
    # hard_check_coverage() loads the profile for its own ProfileConfig and
    # the foreign-remainder WARN block loads it again inside a try that
    # downgrades a failure to a skipped check -- borrowing that one would make
    # a stopwords problem silently cancel the operator's declaration and block
    # a shippable book. A malformed profile/ownership marker cannot reach this
    # line: hard_check_coverage()'s own unguarded load, far above, raises on it
    # first, exactly as it does today.
    stale_contract_admitted = (
        collect_stale_contract_admitted(classification_by_seg, sentinel_states)
        if admit_contract_only_stale(vd.load_profile())
        else []
    )

    # Rollup invariant, enforced procedurally: project_complete is true if
    # and only if every one of completeness_counts' non-'stale' four values
    # is 0 AND completeness_counts['stale'] - stale_previously_converged == 0
    # (i.e. every 'stale' segment is carved out by the sentinel).
    project_complete = compute_project_complete(
        completeness_counts, stale_previously_converged, len(stale_contract_admitted)
    )

    frontback_coverage = build_frontback_coverage(classification_by_seg)

    summary = {
        "coverage_failures": coverage_failures,
        "stale_review_failures": stale_review_failures,
        "hard_failures": hard_failures,
        "warnings": warnings_count,
        "project_complete": project_complete,
        "completeness_counts": completeness_counts,
        "stale_previously_converged": stale_previously_converged,
        "frontback_coverage": frontback_coverage,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if stale_contract_admitted:
        # #533. Present only when the declaration is on AND something actually
        # qualified, so an undeclared project's summary keys are unchanged and
        # an empty list can never be read as "we checked, there were none" on
        # a run where nothing was ever checked.
        summary["stale_contract_admitted"] = stale_contract_admitted

    # --- human-readable report, to stderr -----------------------------------
    print("=" * 70, file=sys.stderr)
    print(f"FINAL AUDIT -- {len(converged)} converged segment(s)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(
        f"\nHARD (coverage={coverage_failures}, stale_review={stale_review_failures}): "
        f"{'CLEAN' if not hard_failures else str(hard_failures) + ' FAILURES'}",
        file=sys.stderr,
    )
    for d in hard_details:
        print("  ✗", d, file=sys.stderr)
    print(f"\nWARN / MANUAL-REVIEW ({warnings_count}):", file=sys.stderr)
    for w in warn_details:
        print("  •", w, file=sys.stderr)
    # #199, printed UNCONDITIONALLY and with the count first. A project that
    # declares no terms is the same run, on this lane, as one whose every term
    # held -- both produce zero warnings -- so without this line an absent or
    # empty declaration reads as a clean term-consistency pass. Naming the
    # number checked is what makes the two distinguishable at a glance.
    print(
        f"\nTERM CONSISTENCY: {len(terms)} declared term(s) checked over "
        f"{len(converged)} converged segment(s)",
        file=sys.stderr,
    )
    print(
        f"\nWHOLE-PROJECT COMPLETENESS: "
        f"{'COMPLETE' if project_complete else 'INCOMPLETE'} -- "
        + ", ".join(f"{k}={v}" for k, v in completeness_counts.items())
        + f", stale_previously_converged={stale_previously_converged}",
        file=sys.stderr,
    )
    if stale_contract_admitted:
        # Directly under the completeness verdict, and above the ambiguous
        # sentinels, for the same reason that block sits there: the verdict
        # printed one line up is true only because the operator declared these
        # units shippable, and whoever reads COMPLETE needs both facts in one
        # glance.
        print(
            f"\nCONTRACT-ONLY STALE ADMITTED ({len(stale_contract_admitted)}) -- "
            f"profile.yml declares validation.admit_contract_only_stale, so these "
            f"segments do not block the verdict above although the style contract "
            f"moved after they converged. Their drafts are unchanged since review "
            f"and their .ever_converged sentinels are not ABSENT -- an unreadable "
            f"or dangling one carves out like a present one, as it already does "
            f"for the machinery-only population. What they have NOT "
            f"had is a review against the CURRENT contract. If the contract edit "
            f"REVERSED a rule rather than adding one, re-review them instead.",
            file=sys.stderr,
        )
        for seg in stale_contract_admitted:
            print(f"  ~ {seg}", file=sys.stderr)
    if ambiguous_sentinels:
        # Printed right under the completeness verdict on purpose: this is the
        # one place where the number above rests on a sentinel nobody could
        # actually read, and an operator deciding to ship needs both facts in
        # the same glance.
        print(
            f"\nAMBIGUOUS EVER-CONVERGED SENTINELS "
            f"({len(ambiguous_sentinels)}) -- these were COUNTED as converged "
            f"so the completeness verdict above is not blocked by an "
            f"unreadable file, but nothing verified them. Repair the paths: "
            f"if the segment really did converge, replace the entry with a "
            f"regular file containing the single line 'converged'; only if it "
            f"did NOT converge is removing the entry correct.",
            file=sys.stderr,
        )
        for entry in ambiguous_sentinels:
            print(f"  ! {entry['seg']}: {entry['detail']}", file=sys.stderr)
    print(f"\nFRONTBACK COVERAGE ({len(frontback_coverage)} entries):", file=sys.stderr)
    for item in frontback_coverage:
        print(f"  - {item['id']} decision={item['decision']} status={item['status']}", file=sys.stderr)
    print("\n" + "=" * 70, file=sys.stderr)
    print(
        f"SUMMARY: hard_failures={hard_failures} warnings={warnings_count} "
        f"project_complete={project_complete}",
        file=sys.stderr,
    )

    # --- structured stdout: exactly one JSON line ---------------------------
    print(json.dumps(summary, ensure_ascii=False))

    sys.exit(completeness_exit_code(hard_failures, project_complete))


if __name__ == "__main__":
    main()
