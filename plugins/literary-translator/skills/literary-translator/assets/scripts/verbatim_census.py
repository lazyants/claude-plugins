#!/usr/bin/env python3
"""verbatim_census.py -- REPORT-ONLY census of Hebrew source text reproduced
inside a translated draft, compared against that draft's own segpack (#502).

## The gap this fills, and the one it deliberately does not

`validate_draft.py` validates a draft against its canonical segpack, but the
comparison is over KEY SETS plus placeholder/anchor rules (its own six checks,
`validate_draft.py:30-45`). No text of any reproduced source span is compared
to `plain_text`. `validate_conservation.py` is a different, opt-in gate and is
word-multiset by design (`validate_conservation.py:32-36`). So a quoted Hebrew
phrase inside an English draft can lose a letter and every gate stays green.

Measured on the shipped plugin over a real book (ssk-he-en vol.2, 42 drafts):
4040 reproduced Hebrew runs, 3003 byte-identical, 831 differing only in
pointing, 206 differing in LETTERS, across 40 of 42 segments.

**This script REPORTS. It never corrects, and it never gates.** That is not
caution, it is the measured conclusion: an alignment built exactly as the
issue first proposed offered 140 corrections of which roughly half would have
damaged the text, and on the population that was then read word-by-word there
were MORE cases where the draft was right and the SOURCE was corrupt than
cases where the draft was wrong. A deterministic comparison cannot tell
"the draft corrupted the quotation" from "the draft repaired a corrupted
source" -- that needs a reader. So the output is a reading queue, never a
patch, and this script writes nothing at all: no output-file flag exists,
because an operator-supplied path plus the house atomic-write idiom would let
one typo replace a draft with census JSON.

## Nothing is filtered out; the class is a RANK

Every Hebrew run in the draft that is not byte-identical to a source run of
its own unit is listed. Classification only decides what gets READ FIRST:

    tier 1  letter_diff          no fold below explains it
    tier 1  no_source_run        this unit's source carries no Hebrew at all
    tier 2  prefix_attached      source run minus exactly one leading letter
    tier 3  fold_equal           differs only in marks and/or connectors
    tier 4  verbatim_other_unit  byte-identical elsewhere in this segment

Earlier designs SUPPRESSED tiers 2-4. Each suppression hides a real defect,
and two were constructed against this tree: `שֵׁם` ("name") and `שָׁם`
("there") fold to the same key, and source `שלום` vs draft `לום` is a genuine
dropped leading letter that satisfies the prefix rule. **The tier is a
LIKELIHOOD heuristic, not a consequence ordering** -- a tier-3 word swap can
be worse than a tier-1 orthographic slip. Read the whole queue; the tier only
orders it. The output says so in `tier_is_likelihood_only`.

## "Byte-identical occurrence" means occurrence AS A RUN

The issue's wording is "no byte-identical occurrence in that block's segpack
plain_text". Read as a bare SUBSTRING test, a draft run that dropped its
first letter (`לום` inside source `שלום`) would count as present and the
census would be blind to exactly the defect it exists to find. So a run
counts as `verbatim` when it equals a source RUN of the same unit, delimited
the same way -- and the prefix case gets its own tier instead of vanishing.

## Hebrew only, and it refuses rather than reporting a hollow zero

The measured population is Hebrew and every control in #502 is Hebrew. A
generalized version would need a curated letter table per script; a category
filter proves category purity, never Script membership, so four unverified
tables serving zero measured demand were cut. Adding a second script later is
one range tuple plus its expected-set test.

Two refusals exist so a green-looking zero can never be mistaken for a clean
census:

  - a scanned unit whose segpack block carries no `plain_text` (schema-valid,
    `segpack.py:281-283`) -- the frozen contract names that field, and
    `validate_draft._block_source_text()`'s fallback to raw `source_html`
    would compare Hebrew against markup;
  - a run of segments whose SOURCE contains no Hebrew at all -- the census
    cannot say anything about such a project.

Both exit 2, naming what was refused.

## Exit codes -- deliberately not the plugin's usual 0/1/2 defect convention

    0  the census ran. A non-empty queue is NOT a failure.
    2  usage, environment, or malformed artifact.

There is no data-dependent 1: both artifacts are structurally validated
before they are walked. A bare `Exception` is deliberately NOT caught -- that
would relabel a programmer bug as a handled environment error -- so an
unforeseen bug can still traceback. Nothing in this plugin dispatches this
script (`segment_dispatch_driver.py`'s sibling allowlist, the W5 template's
`draft_ready.py && validate_draft.py` gate, and `final_audit.py`'s direct
`vd.validate()` call are the three execution paths, and none names it), so
non-gating rests on non-wiring, not on the exit number.

Usage: python3 verbatim_census.py SEG [SEG ...] [--durable-root PATH]

Self-anchoring by default: this script lives at
${durable_root}/scripts/verbatim_census.py and derives durable_root from its
own path. `--durable-root PATH` replaces that root for DATA, exactly as
`validate_draft.py` does.
"""
import argparse
import json
import re
import sys
import unicodedata
from html import unescape
from pathlib import Path

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
        f"verbatim_census.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside verbatim_census.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

# The three siblings below would otherwise leave ${durable_root}/scripts/
# __pycache__/*.pyc behind -- files this script's own contract says it never
# writes. `sys.dont_write_bytecode` is already set at the top of this module for
# the json_stdout.py load (#369), which is earlier than these imports and so
# covers them too; a second assignment here would be a no-op.

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent

# Sibling imports, never reimplementations (occurrence_targets.py:158's own
# pattern). `segpack.py` can `sys.exit(<str>)` at import time when ITS sibling
# import fails, which would surface here as an uncaught SystemExit with status
# 1 -- a data/environment failure wearing the one status this script promises
# never to use. SystemExit is caught alongside ImportError for that reason.
try:
    from validate_draft import (
        draft_path,
        segpack_path,
        check_draft_structure,
        validate_seg,
        _ANY_SENTINEL_RE,
        _FNREF_ANCHOR_RE,
    )
    from segpack import validate_segpack
    from bootstrap_names import fold_match_key
except (ImportError, SystemExit) as exc:
    print(
        f"ERROR: verbatim_census.py cannot import its siblings from {SCRIPT_DIR} "
        f"({exc}). validate_draft.py, segpack.py and bootstrap_names.py must be "
        "installed alongside it under ${durable_root}/scripts/ -- they supply the "
        "canonical draft/segpack paths and structural validators, and "
        "fold_match_key(), the plugin's ONE #238/#241 mark+connector match key, "
        "which this script reuses rather than defining a fold of its own. "
        "Re-run Step 0a.",
        file=sys.stderr,
    )
    sys.exit(2)


class CensusError(Exception):
    """A usage, environment, or malformed-artifact failure -> exit 2."""


# ---------------------------------------------------------------------------
# Hebrew character classes. Ranges are curated; membership is decided by
# `unicodedata` at import, so an unassigned code point inside a named range
# (U+05EB-U+05EE, and the U+FB37/FB3D/FB3F/FB42/FB45 holes) drops out on every
# interpreter, and a non-letter inside one (U+FB29 HEBREW LETTER ALTERNATIVE
# PLUS SIGN, a symbol) is never admitted. The single U+05D0-U+05F2 span is
# what carries U+05EF HEBREW YOD TRIANGLE, which a U+05D0-U+05EA span misses.
# ---------------------------------------------------------------------------
_LETTER_RANGES = ((0x05D0, 0x05F2), (0xFB1D, 0xFB4F))
HEBREW_LETTERS = frozenset(
    chr(cp)
    for lo, hi in _LETTER_RANGES
    for cp in range(lo, hi + 1)
    if unicodedata.category(chr(cp)).startswith("L")
)

# Exactly final_audit._fold_source_marks()'s own range (final_audit.py:614).
# A mark OUTSIDE it -- an Arabic fatha, a Devanagari matra -- deliberately
# ENDS a run instead of joining it, so a mixed-script corruption stays visible
# as two short runs rather than being folded away as a mark difference.
HEBREW_MARKS = frozenset(
    chr(cp) for cp in range(0x0591, 0x05C8) if unicodedata.category(chr(cp)) == "Mn"
)

# Connectors are admitted only BETWEEN two Hebrew base letters -- which is
# what keeps ASCII '"' from fusing ordinary quoted Latin prose, the same
# construction bootstrap_names.py:222-242 uses for TOKEN_RE. Their FAMILY is
# tracked because fold_match_key() splits on all of them and joins with a
# space, so it erases which family was used: `אב־גד` and `אב׳גד` both fold to
# `אב גד`. A punctuation-family change is a real difference, so the family
# signature is compared alongside the folded key.
CONNECTOR_FAMILY = {
    "־": "hyphen", "-": "hyphen", "‑": "hyphen",
    "׳": "apostrophe", "'": "apostrophe", "’": "apostrophe",
    "״": "quote", '"': "quote",
}

MIN_RUN_CHARS = 2  # the frozen contract's "2+ chars", counted on the final run

# The Hebrew proclitics -- single letters that orthographically fuse onto the
# next word (ha-, ve-, be-, le-, ke-, mi-, she-). `prefix_attached` means "the
# draft quoted the word without its attached prefix", which is a NON-defect
# reading of the difference, so the head it tolerates must be one of these.
# Any OTHER dropped leading letter is a letter difference and belongs in tier
# 1 -- accepting every Hebrew letter here classified source `אבג` / draft `בג`
# as a prefix, and did so 7 times in the live 42-draft book.
PROCLITICS = frozenset("הובלכמש")


def _placeholder_strings(segpack):
    """This segpack's declared verse placeholder strings. A `⟦…⟧` span counts
    as a machine token only if it is a `⟦FNREF_N⟧` anchor or one of these --
    validate_draft.placeholders()'s own exact-map basis. Masking every
    bracketed span instead (bootstrap_names.mask_sentinels()'s behaviour)
    would erase a legitimate `⟦אבגד⟧` of literal source prose, and with it the
    very run this census exists to list."""
    return {
        v.get("placeholder")
        for v in (segpack.get("verses") or [])
        if isinstance(v, dict) and v.get("placeholder")
    }


def mask_placeholders(text, verse_placeholders):
    """Replace each KNOWN placeholder span with an equal-length run of spaces.
    Equal-length, never a collapsing single space, for bootstrap_names.py:96's
    reason -- and because deletion would join `אב⟦FNREF_1⟧גד` into the
    invented run `אבגד`."""
    def _sub(m):
        tok = m.group(0)
        if _FNREF_ANCHOR_RE.fullmatch(tok) or tok in verse_placeholders:
            return " " * len(tok)
        return tok

    return _ANY_SENTINEL_RE.sub(_sub, text or "")


# The bare emphasis spelling segpack.py emits into a footnote's source_text
# (#725), plus the escaping that comes with it.
_EMPH_FOLD_RE = re.compile(r"</?i>")


def _fold_emphasis(text):
    """A footnote source_text reduced back to its own plain text -- the
    representation this census has always compared against (see
    `_source_units`' own note on why blocks are read from `plain_text`)."""
    return unescape(_EMPH_FOLD_RE.sub("", text))


def hebrew_runs(text):
    """Every maximal Hebrew run in `text`, as a list of strings, in order.

    A run is base letters, in-range marks following a base letter, and
    connectors sitting between two base letters. It always contains at least
    one base letter, so a sequence of standalone combining marks can never
    invent one. The length test is applied to the FINAL run string -- masking,
    tokenization and reporting therefore all see the same characters, and no
    separate trimming step can disagree with it. A trailing geresh is simply
    never admitted (no base letter follows it), so `א׳` is a one-character run
    and is not scanned."""
    runs = []
    i, n = 0, len(text)
    while i < n:
        if text[i] not in HEBREW_LETTERS:
            i += 1
            continue
        start = i
        j = i + 1
        while j < n:
            ch = text[j]
            if ch in HEBREW_LETTERS or ch in HEBREW_MARKS:
                j += 1
                continue
            if (
                ch in CONNECTOR_FAMILY
                and j + 1 < n
                and text[j + 1] in HEBREW_LETTERS
                and (text[j - 1] in HEBREW_LETTERS or text[j - 1] in HEBREW_MARKS)
            ):
                j += 1
                continue
            break
        run = text[start:j]
        if len(run) >= MIN_RUN_CHARS:
            runs.append(run)
        i = j
    return runs


def _connector_signature(run):
    """The ordered tuple of connector FAMILIES in `run` -- the axis
    fold_match_key() erases."""
    return tuple(CONNECTOR_FAMILY[ch] for ch in run if ch in CONNECTOR_FAMILY)


def _levenshtein(a, b):
    """Unicode-codepoint Levenshtein. Computed over fold_match_key() values,
    so a distance measures LETTER difference rather than pointing noise -- the
    ordering inside tier 1 is meant to put the likeliest letter defects
    first."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_prefix_attached(run, source_run):
    """True when `run` is `source_run` minus exactly one leading base letter.

    Structural over FOLDED UNITS, not over the space-joined scalar key: on the
    scalar, source `אב־גד` (`אב גד`) and draft `ב׳גד` (`ב גד`) look like a
    one-letter head, although the raw text changed both a letter and the
    connector family. Requiring the same unit count, the same connector-family
    signature and identical later units rejects that."""
    if _connector_signature(run) != _connector_signature(source_run):
        return False
    units, src_units = fold_match_key(run).split(" "), fold_match_key(source_run).split(" ")
    if len(units) != len(src_units) or units[1:] != src_units[1:]:
        return False
    head, tail = src_units[0][:1], src_units[0][1:]
    return bool(head) and head in PROCLITICS and tail == units[0]


def _fold_equal(run, source_run):
    """Marks and/or connector VARIANTS only. fold_match_key() alone is not
    enough: it erases the connector family (`אב־גד` and `אב׳גד` share a key),
    so the family signature is compared too."""
    return (
        fold_match_key(run) == fold_match_key(source_run)
        and _connector_signature(run) == _connector_signature(source_run)
    )


# Decision ladder, in the order classify() applies it: own-unit `verbatim` ->
# `prefix_attached` -> `fold_equal` -> `verbatim_other_unit` -> `no_source_run`
# -> `letter_diff`. Every run takes the FIRST class that fits, so the classes
# cannot overlap and no dict or set ordering can change the counts. The
# precedence is deliberate where it overlaps: a run that its OWN unit already
# explains -- as a proclitic prefix or a mark/connector variant -- is reported
# that way even when the same string also occurs verbatim in another unit,
# because the own-unit reading is the one an operator can act on. Only a run
# its own unit cannot explain at all falls through to `verbatim_other_unit`,
# and only then to `letter_diff`.
TIER = {
    "letter_diff": 1,
    "no_source_run": 1,
    "prefix_attached": 2,
    "fold_equal": 3,
    "verbatim_other_unit": 4,
}
# Every per-class counter the payload carries, in payload order. One tuple,
# because `totals` and each segment's own counts must agree by construction.
COUNT_KEYS = ("runs", "verbatim", *TIER)


def classify(run, own_source_runs, segment_source_runs):
    """(class, nearest_source_run, distance).

    `distance` and `nearest_source_run` are None whenever this unit's own
    source carries NO Hebrew run to measure against -- which is the
    `no_source_run` class, and ALSO a `verbatim_other_unit` run in such a
    unit (an English heading or epigraph quoting Hebrew that belongs to a
    sibling block). Every other queued class carries the distance to the
    nearest run of its OWN unit, so tier 1's ordering is meaningful and the
    remaining tiers stay sortable."""
    if run in own_source_runs:
        return "verbatim", None, 0
    folded_run = fold_match_key(run)
    if own_source_runs:
        # Deterministic tie-break: lowest distance, then earliest occurrence
        # in the source text. `idx` is already unique per candidate, so the
        # order is total and nothing downstream can depend on set or dict
        # iteration.
        distance, idx = min(
            (_levenshtein(folded_run, fold_match_key(src)), idx)
            for idx, src in enumerate(own_source_runs)
        )
        nearest = own_source_runs[idx]
    else:
        nearest, distance = None, None
    for src in own_source_runs:
        if _is_prefix_attached(run, src):
            return "prefix_attached", nearest, distance
    for src in own_source_runs:
        if _fold_equal(run, src):
            return "fold_equal", nearest, distance
    # A SEGMENT-WIDE set is correct here only because the ladder's first
    # branch already returned every own-unit hit: by this line the run is
    # known absent from `own_source_runs`, so a match can only be another
    # unit's. Moving this test earlier would silently reclassify.
    if run in segment_source_runs:
        return "verbatim_other_unit", nearest, distance
    if not own_source_runs:
        return "no_source_run", None, None
    return "letter_diff", nearest, distance


def _load_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CensusError(f"{label} not found at {path}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CensusError(f"{label} at {path} is unreadable: {exc}")


def _validated_pair(seg, segments_dir):
    """Both artifacts, structurally validated BEFORE anything walks them.

    Without this a draft carrying `"blocks": []` or a segpack with a scalar
    block member reaches `.items()`/`.get()` and raises `AttributeError`,
    exiting 1 -- a data-dependent failure status this script promises not to
    have. `validate_segpack()` is additionally wrapped because it is not total
    over JSON values: a `canon_names` member that is itself a list raises
    `TypeError` from its own set construction (reproduced against
    `segpack.py:631` at bf85312; filed separately, not repaired here)."""
    err = validate_seg(seg)
    if err:
        raise CensusError(err)
    src = _load_json(segpack_path(seg, segments_dir), f"segpack {seg}")
    draft = _load_json(draft_path(seg, segments_dir), f"draft {seg}")
    if not isinstance(src, dict):
        raise CensusError(f"segpack {seg} must be a JSON object")
    try:
        seg_errs = validate_segpack(src, f"segpack {seg}")
    except Exception as exc:  # noqa: BLE001 -- see docstring: not total over JSON
        raise CensusError(f"segpack {seg} is malformed ({type(exc).__name__}: {exc})")
    if seg_errs:
        raise CensusError(f"segpack {seg} is invalid: {seg_errs[0]}")
    struct = check_draft_structure(draft)
    if struct:
        raise CensusError(f"draft {seg} is invalid: {struct[0]}")
    if draft.get("seg") != seg:
        raise CensusError(
            f"draft {seg} carries seg {draft.get('seg')!r} -- mislabeled/cross-wired"
        )
    if src.get("seg") != seg:
        raise CensusError(f"segpack {seg} carries seg {src.get('seg')!r} -- cross-wired")
    return src, draft


def _source_units(seg, src):
    """{unit_label: source_text} for every unit this census scans, plus the
    labels whose block carries no `plain_text`. Blocks are read from
    `plain_text` ONLY -- the frozen contract names that field, and
    `_block_source_text()`'s fallback to raw `source_html` would compare
    Hebrew runs against markup."""
    units, missing, seen = {}, [], set()
    for b in src.get("blocks") or []:
        if not isinstance(b, dict) or not b.get("id"):
            # Belt-and-braces: validate_segpack() already refuses this shape.
            raise CensusError(f"segpack {seg}: a block carries no usable id")
        bid = b["id"]
        if bid in seen:
            # This one is NOT redundant: neither validate_segpack() nor
            # check_draft_structure() checks for duplicate block ids, and a
            # duplicate would silently give one label two source texts.
            raise CensusError(f"segpack {seg}: duplicate block id {bid!r}")
        seen.add(bid)
        label = f"blocks:{bid}"
        if b.get("plain_text") is None:
            missing.append(label)
            continue
        units[label] = b["plain_text"]
    for f in src.get("footnotes") or []:
        if not isinstance(f, dict) or f.get("n") is None:
            continue
        label = f"footnotes:{f['n']}"
        if label in units:
            # Same refusal as duplicate block ids, and reachable for the same
            # reason: validate_segpack() type-checks `n` but never asserts it
            # is unique, so two footnotes numbered 1 are schema-valid. Letting
            # the later one win would compare the draft against a source text
            # chosen by list order.
            raise CensusError(
                f"segpack {seg}: duplicate footnote number {f['n']!r} -- "
                "the source text to compare against is ambiguous"
            )
        # #725: a footnote's source_text carries the source's own emphasis as
        # a bare `<i>`, with its entities left escaped. Fold both back out
        # before comparing: `hebrew_runs()` breaks a run at every non-letter,
        # so `<i>אב</i>גד` reads as the two runs ["אב", "גד"] while the
        # correct draft carries the single run `אבגד` -- which the census
        # would then queue as a tier-1 `letter_diff` against a translation
        # that is right. The comparison is about LETTERS, never markup, and
        # this is the same reason blocks are read from plain_text above.
        units[label] = _fold_emphasis(f.get("source_text") or "")
    return units, missing


def _draft_units(draft):
    units = {}
    for bid, text in (draft.get("blocks") or {}).items():
        units[f"blocks:{bid}"] = text or ""
    for n, text in (draft.get("footnotes") or {}).items():
        units[f"footnotes:{n}"] = text or ""
    return units


def census(segs, segments_dir):
    """The census payload. Raises CensusError for every refusal."""
    duplicates = sorted({s for s in segs if segs.count(s) > 1})
    if duplicates:
        # argparse's nargs="+" accepts a repeat. Aggregating one would need a
        # rule for what a segment's counts MEAN when scanned twice; refusing
        # keeps `totals` the sum of `per_segment` and `queued` the length of
        # the queue, which is what the payload's own invariant rests on.
        raise CensusError(
            f"these segment ids are repeated: {duplicates}. Each segment is "
            "scanned once; pass each id at most once."
        )
    per_segment, queue, missing_all, source_runs_total = {}, [], [], 0

    for seg in segs:
        src, draft = _validated_pair(seg, segments_dir)
        placeholders = _placeholder_strings(src)
        src_units, missing = _source_units(seg, src)
        missing_all.extend(f"{seg} {m}" for m in missing)
        drafted = _draft_units(draft)

        src_runs_by_unit = {
            label: hebrew_runs(mask_placeholders(text, placeholders))
            for label, text in src_units.items()
        }
        source_runs_total += sum(len(r) for r in src_runs_by_unit.values())
        segment_runs = {r for runs in src_runs_by_unit.values() for r in runs}
        seg_counts = {k: 0 for k in COUNT_KEYS}

        for label in sorted(drafted):
            if label not in src_units:
                # Key-set coverage is validate_draft.py's job, not this
                # script's; a draft unit with no source counterpart is simply
                # not comparable, and saying so beats inventing a comparison.
                continue
            own = src_runs_by_unit.get(label, [])
            for run in hebrew_runs(mask_placeholders(drafted[label], placeholders)):
                seg_counts["runs"] += 1
                klass, nearest, distance = classify(run, own, segment_runs)
                seg_counts[klass] += 1
                if klass == "verbatim":
                    continue
                queue.append({
                    "seg": seg,
                    "unit": label,
                    "run": run,
                    "class": klass,
                    "tier": TIER[klass],
                    "distance": distance,
                    "nearest_source_run": nearest,
                    "nearest_is_advisory": True,
                })
        per_segment[seg] = seg_counts

    if missing_all:
        raise CensusError(
            "these units carry no segpack 'plain_text', which is the field this "
            "census compares against; refusing rather than reporting an empty "
            f"census: {missing_all[:10]}{' …' if len(missing_all) > 10 else ''}"
        )
    if source_runs_total == 0:
        raise CensusError(
            "no Hebrew found in the source of any requested segment -- this "
            "census is Hebrew-only, so it can say nothing about this project"
        )

    # Derived, never accumulated in parallel: "totals is the sum of
    # per_segment" and "queued is the length of the queue" are then true by
    # construction rather than by three increments staying correct.
    totals = {k: sum(c[k] for c in per_segment.values()) for k in COUNT_KEYS}
    totals["queued"] = len(queue)

    queue.sort(key=lambda r: (
        r["tier"],
        r["distance"] is None,
        r["distance"] if r["distance"] is not None else 0,
        r["seg"], r["unit"], r["run"],
    ))
    return {
        "schema_version": 1,
        "source_script": "hebrew",
        "unidata_version": unicodedata.unidata_version,
        "scanned_fields": ["blocks", "footnotes"],
        "min_run_chars": MIN_RUN_CHARS,
        "tier_is_likelihood_only": True,
        "totals": totals,
        "per_segment": per_segment,
        "queue": queue,
    }


def build_arg_parser():
    p = argparse.ArgumentParser(
        description=(
            "Report-only census of Hebrew source text reproduced inside a "
            "translated draft (#502). A non-empty queue is NOT a failure: this "
            "script never gates, never corrects, and writes nothing."
        )
    )
    p.add_argument("segs", nargs="+", metavar="SEG", help="segment ids, e.g. seg05")
    p.add_argument(
        "--durable-root",
        default=None,
        help="override the self-anchored durable root for DATA (segments/)",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    root = Path(args.durable_root).resolve() if args.durable_root else DURABLE_ROOT
    try:
        payload = census(args.segs, root / "segments")
        # Serialization and the write itself are INSIDE the handled region:
        # the payload is Hebrew by construction, so a non-UTF-8 stdout
        # encoding (PYTHONIOENCODING=ascii, a redirected pipe under a C
        # locale) raises UnicodeEncodeError here. Outside, that surfaced as
        # exit 1 -- the one status this script promises never to use for an
        # environment failure.
        rendered = dumps_line(payload)
        print(rendered)
        sys.stdout.flush()
    except CensusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except UnicodeError as exc:
        print(
            f"ERROR: this census is Hebrew and stdout cannot encode it "
            f"({exc.__class__.__name__}: {exc}). Re-run with a UTF-8 stdout, "
            "e.g. PYTHONIOENCODING=utf-8.",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: could not write the census to stdout ({exc})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
