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

Four WARN-only, advisory, non-gating checks (generalized from the real
reference's A1/A3/A4/A5 -- the real `main()` only ever gated on coverage):

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

A third, distinct gate -- the **whole-project completeness gate** -- shells
out to `select_segments.py` one final time, over the FULL `manifest.json`
with no `--only-segs` restriction, and folds its classification report into
`completeness_counts`/`project_complete`. This is NOT the same population as
the two hard checks above: the hard checks only ever look at segments
ALREADY converged; the completeness gate looks at the whole book, converged
or not. Unlike the four WARN-only checks below, this gate DOES affect the
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

Usage: python3 final_audit.py
"""
import hashlib
import json
import re
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
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
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
# Whole-project completeness gate -- shells out to select_segments.py, over
# the full manifest.json, no --only-segs restriction.
# ---------------------------------------------------------------------------

def run_completeness_gate():
    """Returns (completeness_counts, classification_by_seg). FATALs (exit 2)
    if select_segments.py is missing, fails, or does not honor its own
    documented JSON contract -- this gate cannot be silently skipped."""
    if not SELECT_SEGMENTS_SCRIPT.is_file():
        _fatal(
            f"{SELECT_SEGMENTS_SCRIPT} not found -- final_audit.py's "
            f"whole-project completeness gate requires it. See this "
            f"script's own module docstring for select_segments.py's "
            f"required JSON contract."
        )

    try:
        proc = subprocess.run(
            # #409: --classify-only. This audit needs the CLASSIFICATION and
            # never translates anything, so it must not be refused when the
            # project contains previously-converged segments -- which is the
            # normal state of a finished book, and exactly the state this
            # audit runs in. Without the flag the new gate would take away
            # final_audit.py's documented "project incomplete" / exit-3 path.
            [sys.executable, str(SELECT_SEGMENTS_SCRIPT), "--allow-empty", "--classify-only"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(DURABLE_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fatal(f"could not run {SELECT_SEGMENTS_SCRIPT}: {exc}")

    stdout_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not stdout_lines:
        _fatal(
            f"{SELECT_SEGMENTS_SCRIPT} printed no JSON to stdout "
            f"(exit {proc.returncode}); stderr: {proc.stderr.strip()}"
        )
    try:
        payload = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        _fatal(
            f"{SELECT_SEGMENTS_SCRIPT}'s last stdout line was not valid "
            f"JSON: {exc}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        _fatal(
            f"{SELECT_SEGMENTS_SCRIPT} reported failure: "
            f"{error or '(no error message)'}"
        )

    counts = payload.get("counts")
    classification = payload.get("classification")
    if not isinstance(counts, dict) or not isinstance(classification, dict):
        _fatal(
            f"{SELECT_SEGMENTS_SCRIPT}'s JSON output is missing the "
            f"required 'counts'/'classification' objects -- see this "
            f"script's own module docstring for the required contract."
        )

    completeness_counts = {}
    for cat in COMPLETENESS_CATEGORIES:
        value = counts.get(cat)
        if not isinstance(value, int) or value < 0:
            _fatal(
                f"{SELECT_SEGMENTS_SCRIPT}'s 'counts' is missing a valid "
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
    convergence is recorded) and also READ by select_segments.py's own
    #409 Step 1 re-translate gate -- see that function's own docstring for
    why a separate durable file exists rather than reading the (mutable)
    ledger status. Filename convention restated here, not imported, per
    this plugin's "no shared lib between self-contained scripts"
    convention -- select_segments.py's own ever_converged_path makes the
    same choice for the same reason."""
    return SEGMENTS_DIR / f".ever_converged.{seg}"


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


def count_stale_previously_converged(classification):
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
        if ever_converged_path(seg).exists():
            n += 1
    return n


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

def compute_project_complete(completeness_counts, stale_previously_converged):
    """Pure, unit-testable without a durable root -- mirrors
    completeness_exit_code()'s own pattern. True iff every non-'stale'
    completeness category is 0 AND every 'stale' segment is accounted for
    by the #409 ever-converged sentinel (completeness_counts['stale'] -
    stale_previously_converged == 0). completeness_counts['stale'] itself
    is never mutated by this function -- see
    count_stale_previously_converged()'s own docstring and this module's
    docstring ("#409 Step 2 carve-out") for why a 'stale' segment without
    the sentinel must still block completeness exactly as before (the
    fail-safe direction)."""
    non_stale_clear = all(
        v == 0 for cat, v in completeness_counts.items() if cat != "stale"
    )
    stale_blocking = completeness_counts.get("stale", 0) - stale_previously_converged
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


def main():
    if len(sys.argv) != 1:
        print("usage: python3 final_audit.py", file=sys.stderr)
        sys.exit(2)

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
    # A4 (verse-structure) per converged segment.
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

    for seg in sorted(converged):
        warn_details.extend(warn_link_graph(seg))
        warn_details.extend(warn_foreign_remainder(seg, stopwords_lower))
        warn_details.extend(warn_verse_structure(seg))

    warnings_count = len(warn_details)

    completeness_counts, classification_by_seg = run_completeness_gate()
    # #409 Step 2: a 'stale' segment that also carries the durable
    # ever-converged sentinel is carved out of the completeness gate -- see
    # count_stale_previously_converged()'s and compute_project_complete()'s
    # own docstrings, and this module's docstring ("#409 Step 2 carve-out"),
    # for the full rationale. completeness_counts itself is left unchanged
    # (still the raw select_segments.py count); only project_complete is
    # computed net of the carve-out.
    stale_previously_converged = count_stale_previously_converged(classification_by_seg)
    # Rollup invariant, enforced procedurally: project_complete is true if
    # and only if every one of completeness_counts' non-'stale' four values
    # is 0 AND completeness_counts['stale'] - stale_previously_converged == 0
    # (i.e. every 'stale' segment is carved out by the sentinel).
    project_complete = compute_project_complete(completeness_counts, stale_previously_converged)

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
    print(
        f"\nWHOLE-PROJECT COMPLETENESS: "
        f"{'COMPLETE' if project_complete else 'INCOMPLETE'} -- "
        + ", ".join(f"{k}={v}" for k, v in completeness_counts.items())
        + f", stale_previously_converged={stale_previously_converged}",
        file=sys.stderr,
    )
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
