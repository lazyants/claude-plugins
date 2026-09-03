#!/usr/bin/env python3
"""backfill_glossary_merge_ack.py -- #820: acknowledge, per glossary RUN_ID,
the runs whose W3 glossary pass merged BEFORE `canon_validate.py
--merge-batches` started writing a durable `glossary/runs/<RUN_ID>/
merged.json` marker on successful merge.

## Why this script exists

#820 adds a W5 admission gate that refuses to dispatch translation while a
project's W3 glossary pass has a run directory (`glossary/runs/<RUN_ID>/`) on
disk with no `merged.json` beside it -- see `select_segments.py`'s own gate
for the refusal itself. That gate is correct going forward and unsatisfiable
backwards: every project whose glossary already merged under an OLDER build
has run directories with no marker at all, and would refuse forever. This
script closes that gap for an EXISTING project, in the same shape this
plugin has closed it twice before -- see `backfill_resume_gate_ack.py` and
`backfill_ever_converged.py`, both of which this file is deliberately
modelled on: dry-run by default, one JSON line on stdout, never fabricating
the evidence the gate actually wants.

## What this script does NOT do, and why it matters most

**It never verifies that a merge actually happened, and it never fabricates
one.** `canon_validate.py --merge-batches` writes `merged.json` only after
successfully merging every batch's fragment into canon.json -- an artifact
this script cannot reconstruct after the fact, because the merge itself is
long since done (or was never done at all) and there is no independent
record of which outcome occurred. Writing a marker that CLAIMS a verified
merge would be a forged proof, worse than no marker at all: a later reader
would treat it as evidence the merge machinery ran and checked out, when
all that is actually known is that the run's own batch artifacts are
present and internally consistent.

So this script checks something narrower and honestly weaker:
**structural completeness** -- every `manifest_<index>.json` the run
produced has a matching `out_<index>_attempt_0.json` fragment beside it,
i.e. every batch this run planned was actually dispatched and answered at
least once. That is NOT proof of a successful merge; it is proof the run
was not abandoned mid-flight. The marker's own `"source": "backfill-ack"`
and `"note"` say exactly that, so nothing downstream can mistake this for
`canon_validate.py`'s own `"source": "merge"` record.

A run that is NOT structurally complete -- some `manifest_<index>.json` with
no matching `out_<index>_attempt_0.json` -- is a batch that was never
adjudicated. Acknowledging it would wave through precisely the defect #820
exists to prevent, so this script REFUSES such a run outright and leaves it
untouched, rather than acknowledging it partially or silently.

Structural completeness is checked by FILE EXISTENCE alone, never by
shelling out to `canon_validate.py --check-batch`. `resume_setup.py`'s own
`probe_resumed_batches()` resolves that sibling script from the writable
durable root specifically because it is not a gate; this script is not one
either, and re-validating batch content here would be exactly the kind of
accuracy/identity call the house convention reserves for a human operator
running `canon_validate.py` by hand, not for a backfill utility.

## Per-RUN_ID markers, never a project-level flag

Exactly the reasoning `backfill_resume_gate_ack.py`'s own docstring gives at
length: a project-level marker or a bypass flag on `select_segments.py`
would be one edit away from a blanket off-switch, and acknowledging a run
requires NAMING it, so a newly-unmerged run is still refused. Per-run
acknowledgement makes the wildcard structurally inexpressible.

## CLI flags

    --durable-root PATH / --plugin-root PATH
        The same two independent overrides every script in this family
        documents: `--durable-root` is the DATA root (`glossary/runs/`
        lives under it). `--plugin-root` is accepted for flag uniformity
        across this plugin's entrypoints and reported back in the output,
        but resolves nothing here -- this script shells out to no sibling.

    --apply
        Without it (the default), this script is DRY RUN: it makes ZERO
        filesystem writes and only reports what it would acknowledge. With
        it, the missing `merged.json` markers for structurally-complete
        runs are actually created.

    --allow-empty
        Without this flag, finding ZERO runs needing acknowledgement AND
        ZERO refused runs is a FATAL error. A project that is genuinely
        already compliant (every run already merged, or already
        acknowledged) and a scan that matched nothing at all (wrong
        `--durable-root`, no `glossary/runs/` directory) look almost
        identical; pass this flag to confirm the zero is expected. A
        non-empty `refused` list is never gated behind this flag -- it is
        already loud and non-silent on its own.

## Output

Exactly ONE JSON object on stdout (the house convention), with a
human-readable summary on stderr:
``{"success": bool, "durable_root": ..., "plugin_root": ...,
"applied": bool, "runs_scanned": [...], "already_marked": [...],
"needs_ack": [...], "created": [...], "refused": [{"run_id", "reason"}],
"batches_by_run_id": {"<run_id>": [ints]}, "counts": {...}}``.
Fatal failure: ``{"success": false, "error": ...}``, with extra context
keys, never relied on for anything but `error`.

``success`` is false, and the exit code 1, whenever ``refused`` is
non-empty -- an operator must not be able to miss that some runs were
deliberately left unmarked. It is also false on any fatal condition (an
unsafe RUN_ID-shaped directory name, or zero runs needing acknowledgement
without ``--allow-empty``).
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

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
        f"backfill_glossary_merge_ack.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside backfill_glossary_merge_ack.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

# ---------------------------------------------------------------------------
# Self-anchoring -- identical convention to select_segments.py/
# backfill_resume_gate_ack.py.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """`durable_root_str` governs DATA (`glossary/runs/`), rebuilt from that
    root when given, self-anchored otherwise. `plugin_root_str` is accepted
    for flag uniformity across this plugin's entrypoints and reported in the
    output, but resolves nothing: this script shells out to no sibling."""
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
    else:
        durable_root = Path(durable_root_str).resolve()

    return {
        "durable_root": durable_root,
        "glossary_runs_dir": durable_root / "glossary" / "runs",
        "plugin_root": Path(plugin_root_str).resolve() if plugin_root_str else None,
    }


# ---------------------------------------------------------------------------
# RUN_ID validation -- duplicated from glossary_dispatch_driver.py's
# validate_run_id() (itself resume_setup.py's own contract, which OWNS this
# allowlist), per the "no shared lib between self-contained scripts"
# convention. A directory name under glossary/runs/ is not attacker-
# controlled in the ordinary case, but this script builds filesystem paths
# directly from it, so it is checked rather than trusted transitively.
# tests/backfill_glossary_merge_ack.test.py pins this copy against
# resume_setup.py's by name, a drift check, not a second source of truth.
# ---------------------------------------------------------------------------

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_run_id(name) -> "str | None":
    """Returns a refusal string, or None when `name` is safe to splice into
    a filesystem path."""
    if not isinstance(name, str) or not name:
        return "run id must be a non-empty string"
    if not _RUN_ID_RE.fullmatch(name):
        return f"unsafe run id: {name!r}"
    if name in (".", ".."):
        return f"run id must not be '.' or '..'; got {name!r}"
    if ".." in name:
        return f"run id must not contain '..'; got {name!r}"
    return None


class FatalError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE JSON
    payload on stdout (exit 1), never a bare traceback."""


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(dumps_line({"success": False, "error": message, **extra}))


def now_iso8601() -> str:
    """Matches ledger_update.py's own `now_iso8601()` -- the house format for
    a timestamp written into a durable-root artifact."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write_json(path: Path, doc: dict) -> None:
    """tmp -> os.replace, in the SAME directory -- the house pattern
    duplicated byte-identically from canon_validate.py/ledger_merge.py/
    glossary_dispatch_driver.py, so a partially written file is never
    visible at the target path. The caller decides whether the target may
    already exist (`write_merge_ack()` below only reaches this after
    confirming it does not, or is not a marker worth keeping)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# The structural-completeness scan.
# ---------------------------------------------------------------------------

_MANIFEST_INDEX_RE = re.compile(r"manifest_(\d+)\.json")


def merged_marker_path(run_id: str, glossary_runs_dir: Path) -> Path:
    return glossary_runs_dir / run_id / "merged.json"


def find_manifest_indices(run_dir: Path) -> "list[int]":
    """Every batch index this run planned, from its own `manifest_<index>.json`
    files -- deliberately excludes `manifest_all.json` (no digit suffix, so it
    never matches `_MANIFEST_INDEX_RE`). Ascending, and de-duplicated by
    construction (a directory listing has no duplicate names)."""
    indices = []
    for entry in run_dir.iterdir():
        if not entry.is_file():
            continue
        m = _MANIFEST_INDEX_RE.fullmatch(entry.name)
        if m:
            indices.append(int(m.group(1)))
    indices.sort()
    return indices


def missing_attempt0_fragments(run_dir: Path, indices: "list[int]") -> "list[int]":
    """Indices from `indices` whose `out_<index>_attempt_0.json` fragment is
    not a regular file. Checking attempt 0 specifically, never "any attempt",
    matches the template's own RESUME-SKIP probe
    (glossary-pass-wf.template.js) -- attempt 0 is always written first for
    every batch this run drove at all, even one later rejected and re-driven
    through attempt 1+, so its presence is what actually proves the batch was
    dispatched and answered rather than abandoned before ever running."""
    missing = []
    for idx in indices:
        fragment = run_dir / f"out_{idx}_attempt_0.json"
        if not fragment.is_file():
            missing.append(idx)
    return missing


def read_existing_marker(path: Path):
    """Returns `(state, detail)`:
      - `("absent", None)` -- no entry at `path`.
      - `("present", doc)` -- a regular, readable file that parses as a JSON
        object. `doc` is returned so the caller can report it, never so it
        can be overwritten.
      - `("ambiguous", reason)` -- something is at `path` that this script
        will not touch: not a regular file, unreadable, or not a JSON
        object. Deliberately fails CLOSED like `classify_ever_converged_
        sentinel()`'s AMBIGUOUS state: an entry this process cannot make
        sense of is never silently replaced, whatever it turns out to be."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return ("absent", None)
    except OSError as exc:
        return ("ambiguous", f"lstat failed: {exc}")
    if not path.is_file() or os.path.islink(str(path)):
        return ("ambiguous", "the entry is not a regular file")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ("ambiguous", f"could not read: {exc}")
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return ("ambiguous", f"not valid JSON: {exc}")
    if not isinstance(doc, dict):
        return ("ambiguous", "JSON content is not an object")
    return ("present", doc)


def build_ack_body(run_id: str, batches: "list[int]") -> dict:
    return {
        "schema": "glossary-run-merged/1",
        "run_id": run_id,
        "merged_at": now_iso8601(),
        "batches": batches,
        "source": "backfill-ack",
        "note": (
            "Acknowledged by backfill_glossary_merge_ack.py; predates the "
            "#820 merge marker; not a verified merge -- only structural "
            "completeness (every manifest_<index>.json has a matching "
            "out_<index>_attempt_0.json fragment) was checked."
        ),
    }


def scan(glossary_runs_dir: Path) -> "list[str]":
    """Every run-id-shaped directory name under `glossary_runs_dir`, sorted.
    A name that does not even match the base RUN_ID character class is not
    run-id-shaped at all (e.g. any dot-prefixed staging entry a driver may
    leave behind) and is silently skipped -- it was never a candidate run
    directory. A name that matches the character class but fails
    `validate_run_id()`'s stricter '.'/'..' refinement is refused loudly
    (FATAL) rather than skipped, exactly as glossary_dispatch_driver.py's own
    `validate_run_id()` docstring requires for any RUN_ID this script is
    about to splice into a filesystem path."""
    if not glossary_runs_dir.is_dir():
        return []
    run_ids = []
    for entry in sorted(glossary_runs_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        name = entry.name
        if not _RUN_ID_RE.fullmatch(name):
            continue
        problem = validate_run_id(name)
        if problem is not None:
            fatal(
                f"a directory under {glossary_runs_dir} looks like it could "
                f"be a glossary RUN_ID but is unsafe: {problem}",
                run_id=name,
            )
        run_ids.append(name)
    return run_ids


def run(args, dirs: dict) -> dict:
    glossary_runs_dir = dirs["glossary_runs_dir"]
    run_ids = scan(glossary_runs_dir)

    already_marked = []
    needs_ack = []
    refused = []
    batches_by_run_id = {}

    for run_id in run_ids:
        run_dir = glossary_runs_dir / run_id
        marker_path = merged_marker_path(run_id, glossary_runs_dir)
        state, detail = read_existing_marker(marker_path)
        if state == "present":
            already_marked.append(run_id)
            continue
        if state == "ambiguous":
            refused.append({
                "run_id": run_id,
                "reason": f"an existing merged.json could not be trusted, so "
                          f"it was left untouched: {detail}",
            })
            continue

        indices = find_manifest_indices(run_dir)
        if not indices:
            refused.append({
                "run_id": run_id,
                "reason": "no manifest_<index>.json files found under this "
                          "run directory -- nothing to acknowledge",
            })
            continue
        missing = missing_attempt0_fragments(run_dir, indices)
        if missing:
            refused.append({
                "run_id": run_id,
                "reason": f"structurally incomplete: {len(missing)} of "
                          f"{len(indices)} batch(es) never wrote "
                          f"out_<index>_attempt_0.json (missing indices: "
                          f"{missing})",
            })
            continue

        needs_ack.append(run_id)
        batches_by_run_id[run_id] = indices

    # The --allow-empty guard is scoped to "nothing of interest happened",
    # never to a genuine refusal: a non-empty `refused` is already loud and
    # non-silent (exit 1, the reasons listed), so it must not be swallowed
    # behind a flag whose whole point is confirming a BORING zero. Only when
    # there is also nothing to refuse does an empty needs_ack become
    # ambiguous between "already compliant" and "wrong --durable-root".
    if not needs_ack and not refused and not args.allow_empty:
        fatal(
            "found ZERO glossary runs needing acknowledgement -- refusing "
            "to report this silently. An already-compliant project and a "
            f"scan that matched nothing at all (wrong --durable-root, no "
            f"glossary/runs/ directory) look nearly identical here; this "
            f"run scanned {len(run_ids)} run-id-shaped directory(ies) and "
            f"found {len(already_marked)} already marked. Pass "
            "--allow-empty to confirm that zero is what you expected.",
            durable_root=str(dirs["durable_root"]),
            runs_scanned=run_ids,
            already_marked=already_marked,
        )

    created = []
    if args.apply:
        for run_id in needs_ack:
            marker_path = merged_marker_path(run_id, glossary_runs_dir)
            _atomic_write_json(marker_path, build_ack_body(run_id, batches_by_run_id[run_id]))
            created.append(run_id)
        created.sort()

    refused.sort(key=lambda r: r["run_id"])

    return {
        "success": not refused,
        "durable_root": str(dirs["durable_root"]),
        "plugin_root": str(dirs["plugin_root"]) if dirs["plugin_root"] else None,
        "applied": bool(args.apply),
        "runs_scanned": run_ids,
        "already_marked": already_marked,
        "needs_ack": needs_ack,
        "created": created,
        "refused": refused,
        "batches_by_run_id": {k: batches_by_run_id[k] for k in sorted(batches_by_run_id)},
        "counts": {
            "runs_scanned": len(run_ids),
            "already_marked": len(already_marked),
            "needs_ack": len(needs_ack),
            "created": len(created),
            "refused": len(refused),
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "#820: acknowledge, per glossary RUN_ID, the runs that merged "
            "before canon_validate.py --merge-batches wrote a durable "
            "merged.json marker. Never fabricates a verified merge. DRY RUN "
            "by default -- pass --apply to actually write."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually create the missing glossary/runs/<RUN_ID>/merged.json "
            "markers for structurally-complete runs. Without this flag the "
            "script makes ZERO filesystem writes and only reports what it "
            "would acknowledge."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Do not fatally error when zero runs need acknowledgement (an "
            "already-compliant project and a scan that matched nothing look "
            "nearly identical without this being explicit)."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "Use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where glossary/runs/ is "
            "found. Optional; omit for today's self-anchored behavior."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "Accepted for flag uniformity with this plugin's other "
            "entrypoints and reported back in the output, but resolves "
            "nothing here: this script shells out to no sibling script."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        result = run(args, dirs)
    except FatalError as exc:
        print(str(exc), file=sys.stdout)
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            dumps_line({"success": False, "error": f"unexpected error: {exc}"}),
            file=sys.stdout,
        )
        return 1

    mode = "APPLY" if result["applied"] else "DRY RUN (pass --apply to write)"
    print("=" * 70, file=sys.stderr)
    print("BACKFILL GLOSSARY MERGE ACKNOWLEDGEMENTS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"durable_root: {result['durable_root']}", file=sys.stderr)
    print(f"mode: {mode}", file=sys.stderr)
    print(f"\nrun-id-shaped directories scanned: {result['counts']['runs_scanned']}",
          file=sys.stderr)
    for run_id in result["runs_scanned"]:
        n = len(result["batches_by_run_id"].get(run_id, []))
        if run_id in result["already_marked"]:
            status = "already marked (merged.json present)"
        elif run_id in result["created"]:
            status = f"ACKNOWLEDGED ({n} batch(es))"
        elif run_id in result["needs_ack"]:
            status = f"needs acknowledgement, dry run ({n} batch(es))"
        else:
            reason = next(
                (r["reason"] for r in result["refused"] if r["run_id"] == run_id), ""
            )
            status = f"REFUSED -- {reason}"
        print(f"  - {run_id}: {status}", file=sys.stderr)
    print(
        f"\nalready marked: {result['counts']['already_marked']}\n"
        f"needs acknowledgement: {result['counts']['needs_ack']}\n"
        f"refused: {result['counts']['refused']}",
        file=sys.stderr,
    )
    if result["applied"]:
        print(f"acknowledged this run: {result['counts']['created']}", file=sys.stderr)
    if result["refused"]:
        print(
            "\nRefused runs were left untouched. Investigate before deciding "
            "whether they need a real canon_validate.py --merge-batches pass.",
            file=sys.stderr,
        )
    print("\n" + "=" * 70, file=sys.stderr)

    print(dumps_line(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
