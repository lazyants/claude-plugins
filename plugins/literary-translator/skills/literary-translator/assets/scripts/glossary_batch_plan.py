#!/usr/bin/env python3
"""glossary_batch_plan.py -- deterministic curation + batching of
bootstrap_names.py candidates into the glossary-pass Workflow's input.

NEW in 1.3.5 (#101 W3 resumability filter + #95 batch-cost curation).
Owns BOTH issues because they interact with #91's elision-ambiguity flag:
the same candidate->batch transition both EXCLUDES what is already resolved
in canon.json and CURATES the survivors down to what is worth a codex call
-- while never dropping an ambiguous elision pair that #91 needs an
adjudicator to look at.

This script does mechanical filtering/curation ONLY. It NEVER makes an
accuracy or identity call: exclusion is by exact name / source_form match
against canon.json; inclusion is by a fixed mechanical predicate. It is the
same IRON RULE every other script in this plugin follows ("scripts SURFACE
candidates ... NEVER make an accuracy/identity call") -- the codex glossary
pass, not this script, decides any canonical form.

Runs BEFORE the glossary-pass Workflow (and before resume_setup.py): the
orchestrating Claude session calls this once, reads its one JSON line, and
either (a) sees `no_new_candidates: true` and skips the whole glossary pass
this run, or (b) feeds `args` into the Workflow tool and `batches` into
resume_setup.py's payload.

Selection, per candidate, in this exact precedence order:

  (1) entries{}/review_queue EXCLUSION always wins first.
      - Excluded if `name` matches an `entries{}` key in canon.json.
      - Excluded if `name` matches a `review_queue[].source_form` in
        canon.json -- UNLESS that exact name is listed in --retry (the
        documented "queued items are only re-researched on explicit human
        request" retry path). A --retry name present in NEITHER
        name_candidates.json NOR review_queue fails loudly (a stale retry
        name from an earlier book must not be silently swallowed).

  (2) Among the survivors of (1): included only if `likely_name` AND
      `freq >= --min-candidate-freq`, EXCEPT the #91 elision bypass:
      - any surviving row with `elision_ambiguous: true` is force-included,
      - and any surviving row that is an included ambiguous row's
        `elision_stripped_form` target is also force-included.
      Force-inclusion BYPASSES THE ENTIRE STEP-2 PREDICATE -- both the
      `likely_name` requirement AND the frequency floor. A capitalized
      elision is sentence-initial by construction, so its ambiguous row has
      `likely_name=False`; requiring `likely_name` here would silently kill
      #91's dominant case. If a stripped-form target was itself EXCLUDED at
      step (1), it stays excluded -- the ambiguous row alone is still
      force-included, carrying its `elision_stripped_form` as the
      note-facing context the adjudicator needs.

Matching for the elision bypass is GLOBAL -- a stripped-form target is
matched against any other row's `name` across the whole file, never scoped
per-source (bootstrap_names.py's collect_candidates aggregates freq by name
only, and its by_source map is empty for the supported source_id=None
input, so a same-source rule would silently never fire).

Co-location: eligible rows are freq-sorted, then chunked into batches of
--batch-size. BEFORE chunking, any force-included pair (an ambiguous row
and its stripped-form target) is pulled into the SAME batch, so an
adjudicator sees both halves together even though freq-sort would otherwise
separate them. A co-location pull MAY push a batch one or two candidates
over --batch-size -- that is intended and harmless (the Workflow's preflight
cost cap counts batches, not candidates-per-batch); a co-located pair is
never re-split.

Output (one JSON line to stdout):
  * eligible candidates present ->
      {"no_new_candidates": false,
       "args":    [{"index": 0, "candidates": [<row>, ...]}, ...],
       "batches": [{"index": 0, "names":      ["Name", ...]},  ...]}
    `args` is the EXACT shape glossary-pass-wf.template.js expects (each
    candidate row passed through VERBATIM from name_candidates.json).
    `batches` is the names-only projection resume_setup.py's payload needs.
    The two projections always carry identical name sets, batch for batch.
  * eligible list legitimately empty ->
      {"no_new_candidates": true, "batches": []}
    The orchestrating session, on this marker, skips resume_setup.py and the
    Workflow dispatch entirely -- nothing to do this run. (resume_setup.py
    rejects an empty `batches` list, which is why the marker exists rather
    than an empty batch payload.)

Exit 0 on success (including the no_new_candidates marker); exit non-zero,
with an `error: ...` line on stderr, on any fatal condition (missing/
malformed input, a stale --retry name). stdout carries only the JSON
result -- errors never pollute it.

Self-anchored: this script always lives at
${durable_root}/scripts/glossary_batch_plan.py, so parents[1] is the
durable root. name_candidates.json and canon.json default to their
durable-root locations; both can be overridden for a smoke/dry run. Never
assumes cwd, never takes a --durable-root flag.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import NoReturn

# Self-anchored: ${durable_root}/scripts/glossary_batch_plan.py.
DURABLE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME_CANDIDATES = DURABLE_ROOT / "name_candidates.json"
DEFAULT_CANON = DURABLE_ROOT / "canon.json"

DEFAULT_MIN_CANDIDATE_FREQ = 2
# CLI-only default -- deliberately NOT a profile-schema key (keeps the
# profile-wiring surface small; see the 1.3.5 plan's knob-proliferation note).
DEFAULT_BATCH_SIZE = 40


def fail(message: str) -> NoReturn:
    """Fail loudly, naming the problem, exit non-zero. stdout is reserved
    for the JSON result, so every error goes to stderr."""
    sys.stderr.write("error: " + message + "\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def read_json_object(path: Path, label: str):
    """Read `path`, parse it as JSON, and confirm it is a JSON object. Callers
    own the file-existence policy (it differs per input), so `path` is assumed
    to exist here; any read/parse/type failure is fatal, tagged with `label`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"could not read {label} at {path}: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{label} at {path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        fail(f"{label} at {path} must be a JSON object.")
    return data


def load_name_candidates(path: Path):
    """Load name_candidates.json and return its `candidates` list, validated
    just enough to trust `name`/`freq` below. Always required -- a missing
    file means bootstrap_names.py has not run yet."""
    if not path.is_file():
        fail(
            f"name_candidates.json not found at {path} -- run bootstrap_names.py "
            "for this project first."
        )
    data = read_json_object(path, "name_candidates.json")
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        fail(f"name_candidates.json at {path} is missing a 'candidates' array.")
    return normalize_rows(candidates, path)


def normalize_rows(candidates, path: Path):
    """Validate the essential shape of each candidate row (unique non-empty
    `name`; integer `freq` when present; a non-empty `elision_stripped_form`
    whenever `elision_ambiguous` is true). Rows are returned unmodified --
    they flow into `args` VERBATIM."""
    seen = set()
    for i, row in enumerate(candidates):
        if not isinstance(row, dict):
            fail(f"candidate at index {i} in {path} is not an object: {row!r}")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            fail(f"candidate at index {i} in {path} has a missing/empty 'name': {row!r}")
        if name in seen:
            fail(
                f"duplicate candidate name {name!r} in {path} -- "
                "name_candidates.json is expected to hold each name once."
            )
        seen.add(name)
        freq = row.get("freq")
        if freq is not None and (isinstance(freq, bool) or not isinstance(freq, int)):
            fail(f"candidate {name!r} in {path} has a non-integer 'freq': {freq!r}")
        if row.get("elision_ambiguous") is True:
            stripped = row.get("elision_stripped_form")
            if not isinstance(stripped, str) or not stripped:
                fail(
                    f"candidate {name!r} in {path} is elision_ambiguous:true but "
                    "carries no non-empty 'elision_stripped_form' -- the two "
                    "fields are written together by bootstrap_names.py."
                )
    return candidates


def load_canon(path: Path, explicit: bool):
    """Return (entry_keys, queued_source_forms) as two sets. A missing
    canon.json at the SELF-ANCHORED default is a legitimate first-glossary-run
    state (no prior entries/queue) -> two empty sets. A missing canon.json at
    an EXPLICIT --canon path is a caller error -> fail loudly (silently
    skipping exclusion is exactly the #101 bug this script exists to fix)."""
    if not path.is_file():
        if explicit:
            fail(f"--canon path not found: {path}")
        sys.stderr.write(
            f"note: no canon.json at {path} -- treating as empty "
            "(no prior entries{}/review_queue to exclude).\n"
        )
        return set(), set()
    canon = read_json_object(path, "canon.json")

    entries = canon.get("entries", {})
    if not isinstance(entries, dict):
        fail(f"canon.json at {path} has a non-object 'entries'.")
    review_queue = canon.get("review_queue", [])
    if not isinstance(review_queue, list):
        fail(f"canon.json at {path} has a non-array 'review_queue'.")

    entry_keys = set(entries.keys())
    queued = set()
    for item in review_queue:
        if isinstance(item, dict):
            source_form = item.get("source_form")
            if isinstance(source_form, str) and source_form:
                queued.add(source_form)
    return entry_keys, queued


def parse_retry(retry_args):
    """Flatten the (possibly repeated, comma-separated) --retry flag into a
    set of source forms. Empty pieces are dropped."""
    retry = set()
    for chunk in retry_args or []:
        for piece in chunk.split(","):
            piece = piece.strip()
            if piece:
                retry.add(piece)
    return retry


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _int_field(row, key: str) -> int:
    """Return row[key] when it is a real integer (bool excluded -- bool is an
    int subclass), else 0. Backs both the frequency floor and the sort keys."""
    value = row.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def select_included(rows, entry_keys, queued, retry, min_freq):
    """Apply the two-step precedence and return the ordered list of included
    candidate rows (input order preserved). Pure -- no I/O."""
    # Step (1): exclusion always wins first.
    survivors = []
    for row in rows:
        name = row["name"]
        if name in entry_keys:
            continue
        if name in queued and name not in retry:
            continue
        survivors.append(row)

    survivor_names = {row["name"] for row in survivors}

    # Step (2): the #91 force-inclusion set, computed over survivors only.
    ambiguous_survivors = [r for r in survivors if r.get("elision_ambiguous") is True]
    force = set()
    for row in ambiguous_survivors:
        force.add(row["name"])
        target = row.get("elision_stripped_form")
        # Target force-inclusion only reaches a row that itself survived (1).
        if isinstance(target, str) and target in survivor_names:
            force.add(target)

    included = []
    for row in survivors:
        name = row["name"]
        if name in force:
            included.append(row)
        elif row.get("likely_name") is True and _int_field(row, "freq") >= min_freq:
            included.append(row)
    return included


# ---------------------------------------------------------------------------
# Co-location + chunking
# ---------------------------------------------------------------------------


def build_partner_adjacency(included):
    """Undirected adjacency among INCLUDED rows only: an ambiguous row is
    linked to its stripped-form target whenever BOTH are in the included
    set. Connected components of this graph must never be split across
    batches. (Every elision_ambiguous row that reaches `included` was
    force-included, so filtering `included` here recovers the exact
    survivor-ambiguous set.)"""
    included_names = {row["name"] for row in included}
    adjacency = defaultdict(set)
    for row in included:
        if row.get("elision_ambiguous") is not True:
            continue
        name = row["name"]
        target = row.get("elision_stripped_form")
        if isinstance(target, str) and target in included_names and target != name:
            adjacency[name].add(target)
            adjacency[target].add(name)
    return adjacency


def chunk_batches(included, batch_size):
    """Freq-sort the included rows, then greedily chunk them into batches of
    `batch_size`, pulling every co-located partner-closure into whichever
    batch its first member lands in (may exceed batch_size by a co-located
    pair -- intended)."""
    adjacency = build_partner_adjacency(included)
    by_name = {row["name"]: row for row in included}

    sorted_rows = sorted(
        included,
        key=lambda r: (-_int_field(r, "freq"), -_int_field(r, "mid_sentence"), r["name"]),
    )
    order = {row["name"]: i for i, row in enumerate(sorted_rows)}

    def closure(start):
        component = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            for neighbour in adjacency.get(node, ()):
                if neighbour not in component:
                    stack.append(neighbour)
        return component

    placed = set()
    batches = []
    current = []
    for row in sorted_rows:
        name = row["name"]
        if name in placed:
            continue
        if current and len(current) >= batch_size:
            batches.append(current)
            current = []
        # Place this row and its whole partner-closure together, in the
        # global freq-sort order, so a pulled low-freq partner sits right
        # after its high-freq anchor deterministically.
        for member in sorted(closure(name), key=lambda n: order[n]):
            if member not in placed:
                current.append(by_name[member])
                placed.add(member)
    if current:
        batches.append(current)
    return batches


def build_result(batches):
    args = []
    batch_projection = []
    for index, rows in enumerate(batches):
        args.append({"index": index, "candidates": rows})
        batch_projection.append({"index": index, "names": [row["name"] for row in rows]})
    return {"no_new_candidates": False, "args": args, "batches": batch_projection}


def emit_retry_diagnostics(retry, included_names, rows, candidate_names, entry_keys, min_freq):
    """Non-fatal stderr diagnostics (never touches stdout or the exit code): a
    --retry name that cleared the neither-input fatal guard but STILL resolved
    to no dispatched candidate is surfaced with the reason it was not
    dispatched -- never silently swallowed, which would undercut #101's
    explicit-human-retry intent. The fatal neither-input guard stays separate
    and unchanged (that case never reaches here)."""
    if not retry:
        return
    row_by_name = {row["name"]: row for row in rows}
    for name in sorted(retry):
        if name in included_names:
            continue
        if name not in candidate_names:
            # (a) queued (so it cleared the fatal guard) but no candidate row.
            sys.stderr.write(
                f"note: --retry name {name!r} is in canon.json's review_queue but "
                "not among the current name_candidates.json candidates -- nothing "
                "to dispatch for it (the source may have been re-extracted since "
                "it was queued).\n"
            )
        elif name in entry_keys:
            # Already-resolved: retry overrides only the review_queue exclusion.
            sys.stderr.write(
                f"note: --retry name {name!r} is already resolved in canon.json's "
                "entries{} -- retry overrides only the review_queue exclusion, "
                "never a resolved entry, so nothing is dispatched for it.\n"
            )
        else:
            # (b) a current candidate that survived step 1 but step-2 curation
            # dropped it (and it is not an elision force-include).
            row = row_by_name[name]
            reasons = []
            if row.get("likely_name") is not True:
                reasons.append("likely_name is not true")
            row_freq = _int_field(row, "freq")
            if row_freq < min_freq:
                reasons.append(f"freq {row_freq} < --min-candidate-freq {min_freq}")
            detail = " and ".join(reasons) if reasons else "step-2 curation dropped it"
            sys.stderr.write(
                f"note: --retry name {name!r} is a current candidate but was not "
                f"dispatched -- {detail}, and it is not an elision force-include.\n"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic curation + batching of bootstrap_names.py "
            "candidates for the glossary-pass Workflow. Excludes what is "
            "already in canon.json (#101), curates the survivors by "
            "likely_name/frequency (#95), and force-includes elision-"
            "ambiguous pairs for adjudication (#91). Mechanical only -- never "
            "an accuracy/identity call."
        ),
    )
    parser.add_argument(
        "--name-candidates", metavar="PATH", default=None,
        help=f"bootstrap_names.py output to curate (default: {DEFAULT_NAME_CANDIDATES}).",
    )
    parser.add_argument(
        "--canon", metavar="PATH", default=None,
        help=f"canon.json whose entries{{}}/review_queue are excluded "
             f"(default: {DEFAULT_CANON}; a missing default is treated as an "
             "empty canon, a missing explicit path is an error).",
    )
    parser.add_argument(
        "--min-candidate-freq", type=int, default=DEFAULT_MIN_CANDIDATE_FREQ,
        metavar="N",
        help=f"Minimum freq for a non-force-included candidate (default: "
             f"{DEFAULT_MIN_CANDIDATE_FREQ}). Overridable from the profile via "
             "glossary.min_candidate_freq (resolved by the orchestrating "
             "session, passed here as this flag).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, metavar="N",
        help=f"Target candidates per batch before co-location pulls (default: "
             f"{DEFAULT_BATCH_SIZE}). CLI-only, never a profile-schema key.",
    )
    parser.add_argument(
        "--retry", action="append", default=None, metavar="SRC[,SRC...]",
        help="Comma-separated source_form(s) to re-include even though they "
             "sit in canon.json's review_queue (the explicit human retry "
             "path). Repeatable. A name present in neither name_candidates.json "
             "nor review_queue fails loudly.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.min_candidate_freq < 1:
        parser.error("--min-candidate-freq must be >= 1")
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")

    name_candidates_path = (
        Path(args.name_candidates) if args.name_candidates else DEFAULT_NAME_CANDIDATES
    )
    canon_explicit = args.canon is not None
    canon_path = Path(args.canon) if args.canon else DEFAULT_CANON

    rows = load_name_candidates(name_candidates_path)
    entry_keys, queued = load_canon(canon_path, canon_explicit)
    retry = parse_retry(args.retry)

    # A stale --retry name (present in neither input) fails loudly.
    candidate_names = {row["name"] for row in rows}
    unknown_retry = sorted(
        name for name in retry if name not in candidate_names and name not in queued
    )
    if unknown_retry:
        fail(
            "--retry name(s) present in neither name_candidates.json nor "
            "canon.json's review_queue: " + ", ".join(repr(n) for n in unknown_retry)
        )

    included = select_included(
        rows, entry_keys, queued, retry, args.min_candidate_freq
    )
    included_names = {row["name"] for row in included}
    emit_retry_diagnostics(
        retry, included_names, rows, candidate_names, entry_keys, args.min_candidate_freq
    )

    if not included:
        # Distinct, schema-shaped marker: the orchestrating session skips
        # resume_setup.py and the Workflow entirely on this run.
        print(json.dumps({"no_new_candidates": True, "batches": []}, ensure_ascii=False))
        return 0

    batches = chunk_batches(included, args.batch_size)
    result = build_result(batches)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
