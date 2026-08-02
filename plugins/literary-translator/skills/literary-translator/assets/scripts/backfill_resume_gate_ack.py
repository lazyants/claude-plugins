#!/usr/bin/env python3
"""backfill_resume_gate_ack.py -- #409 Step 3: acknowledge, per RUN_ID, the
runs that dispatched work BEFORE the resume-integrity gate was enforced.

## Why this script exists

``select_segments.py`` now refuses an authorizing invocation when a prior
RUN_ID dispatched work into a project without the resume-integrity gate --
that is, when a run id appears in some ``segments/<seg>.draft.json``'s own
``dispatch_token`` but has no ``runs/<RUN_ID>/input.digest`` (the artifact
``resume_setup.py`` writes before any dispatch). See that script's own Step 3
block comment for the full rationale and for the three states the underlying
set difference distinguishes.

That gate is correct going forward and unsatisfiable backwards. A real
project ran six consecutive W5 batches with the step skipped: hand-labelled
run ids, not one ``input.digest`` on disk, and nothing noticed for six
batches. Those runs cannot be retro-verified -- the digest is computed over
the inputs a run actually consumed, and for a run that has already finished
those inputs are not reconstructible. Without a way to record that, the new
gate would refuse such a project permanently.

## What this script does NOT do, and why it matters most

**It never fabricates an ``input.digest``.** Writing one now would compute it
from TODAY'S inputs and file it under a run that consumed something else --
a forged proof, and worse than no proof at all, because a later resume would
treat the match as authorization to reuse cached results that were never
validated against those inputs. This script writes a DIFFERENT artifact with
a different meaning: ``runs/<RUN_ID>/.resume_gate_ack`` says "this run
predates the gate; its inputs were never digested and cannot be
reconstructed". An honest record of a gap, never a claim that the gap was
filled. ``resume_setup.py`` and the driver's own candidate scan both require
``input.digest`` to be a FILE before they will consider a run directory at
all, so an ack-only directory can never be mistaken for a resumable run.

## Why per-RUN_ID markers, and never a project-level flag

A single project-level marker (or a CLI flag on ``select_segments.py``) is
one edit away from becoming a blanket off-switch -- a ``"*"`` entry, an
``"all": true`` key, a flag that becomes reflexive exactly the way
``--allow-retranslate-converged`` can. A gate with a wildcard escape is the
invisible warning the Step 3 check exists to replace. Per-run acknowledgement
makes the wildcard structurally inexpressible: acknowledging a run requires
naming it, and a NEWLY skipped run has a new id, so it is still refused. This
mirrors ``.ever_converged.{seg}`` being one marker per protected segment
rather than one flag per project.

## Why this is a standalone script rather than a flag on select_segments.py

``select_segments.py`` is the GATE. Giving the gate a flag that writes its
own bypass marker means one invocation can both refuse and authorize itself,
which collapses the gate into a switch. It is also the only script on the
dispatch path that must stay read-only: the driver and the manual W5 path
both call it before every batch, and a writer there would mutate a live
project on every classification. The dry-run/apply two-step below is the
operator-facing safety property, and ``select_segments.py`` has no such mode
to hang it on.

## Relationship to backfill_ever_converged.py

Deliberately modelled on it, because the SHAPE genuinely matches and not
merely because it is the neighbouring file: both retrofit a durable marker
for state that predates the marker's existence, both derive what to write
from artifacts already on disk, both are dry-run by default with an
``--apply`` that writes, both refuse a zero-result run without
``--allow-empty``, both emit exactly one JSON object on stdout with a
human summary on stderr, and both take the same independent
``--durable-root``/``--plugin-root`` pair. Three deliberate divergences:

  1. **No subprocess, so no ``--allow-merge``.** That script needs a
     materialized ledger, and ``ledger_merge.py`` writes one even when
     invoked bare -- hence its careful three-way ledger-source logic. This
     script reads drafts and stats files. Its dry run therefore makes ZERO
     filesystem writes unconditionally, with no flag and no caveat.
  2. **``--plugin-root`` is accepted but unused for resolution.** There is
     no sibling script to resolve. It is accepted so the flag pair stays
     uniform across the plugin's entrypoints (an operator pasting the same
     two flags everywhere must not hit an "unrecognized argument"), and it
     is reported back in the output so a run's own provenance is legible.
  3. **The marker carries a timestamped JSON body**, where
     ``.ever_converged.{seg}`` is deliberately fixed-content. That file's
     own comment gives the reason -- it sits in ``segments/``, where a
     varying body would make an otherwise identical project directory
     compare unequal. This marker sits in ``runs/<RUN_ID>/``, which already
     holds per-run varying content (``input.digest``, timestamped
     ``ledger.d`` fragments), so that reason does not apply -- and unlike a
     mechanical sentinel, this file records a HUMAN DECISION to proceed
     without proof. A decision with no date is not auditable.

## The two evidence halves, and why this script must union both

A run id comes into scope here from EITHER of two artifacts, exactly as in
``select_segments.py``'s gate:

  * a ``segments/<seg>.draft.json`` whose ``dispatch_token`` names it, and
  * a ``runs/workflows/<RUN_ID>/`` directory (where the orchestrating session
    writes the instantiated Workflow template).

Neither subsumes the other. The driver never creates a workflow directory --
it never calls ``pipeline()`` -- so a driver run is visible only through its
drafts. Conversely a draft holds only its most RECENT dispatch token, so a
skipped run whose drafts were all later re-dispatched under some other run
leaves no trace in the drafts and survives only as a workflow directory. On
the project that motivated this script the draft scan finds four run ids and
the workflow directories show six.

This union is a hard consistency requirement, not a nicety: the gate BLOCKS
on the union, so a retrofit that could only acknowledge the draft-derived
half would leave an operator facing a refusal with no way to clear it. A
dedicated test drives that exact round trip -- gate refuses, ``--apply``,
gate passes -- for a workflow-directory-only run id.

A workflow directory proves INSTANTIATION rather than dispatch, which is
sound for this purpose specifically: the documented order is
``resume_setup.py`` first, then instantiate with the resolved ``{{RUN_ID}}``,
so a workflow directory whose run id has no digest means the template was
instantiated without the gate having run. Verified against the one fully
compliant live project, where all six workflow directories have digests and
this half contributes no false positives.

## CLI flags

    --durable-root PATH / --plugin-root PATH
        The same two INDEPENDENT, orthogonal overrides ``select_segments.py``
        documents at length. ``--durable-root`` is the DATA root (segments/,
        runs/). ``--plugin-root`` is accepted for flag uniformity and
        reported, but resolves nothing here (see divergence 2 above).

    --apply
        Without it (the default), this script is DRY RUN: ZERO filesystem
        writes of any kind, reporting only what it would acknowledge. With
        it, the missing ``.resume_gate_ack`` markers are actually created.

    --only-runs RUN1,RUN2,...
        Acknowledge only these run ids, instead of every un-acknowledged one
        found. FATALS if any named id was not found dispatching in this
        project -- an operator naming a run that this scan cannot see is
        working from a different belief about the project than the evidence
        supports, and must find out rather than have the name silently
        dropped.

    --allow-empty
        Without this flag, finding ZERO run ids to acknowledge is a FATAL
        error. A project that is genuinely already compliant and a scan that
        silently matched nothing (wrong durable root, moved segments/) emit
        an almost identical report, differing only in the ``drafts_scanned``
        number nobody is watching. Pass this to confirm the zero is expected.

## Output

Exactly ONE JSON object on stdout (the house convention -- see
``select_segments.py``/``backfill_ever_converged.py``), with a human-readable
summary on stderr. Exit 0 on success, 1 on any fatal condition -- callers
should read stdout, never rely on the exit code alone.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring -- identical convention to select_segments.py/
# backfill_ever_converged.py.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """`durable_root_str` governs DATA (segments/, runs/), rebuilt from that
    root when given, self-anchored otherwise. `plugin_root_str` is accepted
    for flag uniformity across this plugin's entrypoints and reported in the
    output, but resolves nothing: this script shells out to no sibling (see
    the module docstring's divergence 2)."""
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
    else:
        durable_root = Path(durable_root_str).resolve()

    return {
        "durable_root": durable_root,
        "segments_dir": durable_root / "segments",
        "runs_dir": durable_root / "runs",
        "plugin_root": Path(plugin_root_str).resolve() if plugin_root_str else None,
    }


# ---------------------------------------------------------------------------
# The Step 3 evidence primitives. Duplicated from select_segments.py (the
# READER of these markers) rather than imported, per this project's "no
# shared lib between self-contained scripts" convention -- both are
# standalone entrypoints. Pinned against that copy by a dedicated drift test
# in tests/backfill_resume_gate_ack.test.py, which is a drift check, not a
# second source of truth.
# ---------------------------------------------------------------------------


def input_digest_path(run_id: str, runs_dir: Path) -> Path:
    return runs_dir / run_id / "input.digest"


def resume_gate_ack_path(run_id: str, runs_dir: Path) -> Path:
    return runs_dir / run_id / ".resume_gate_ack"


def draft_run_id(dispatch_token) -> "str | None":
    """The RUN_ID out of a draft's `dispatch_token`. Split on the FIRST colon
    only: a RUN_ID can never contain ':' (resume_setup.py's own
    `validate_run_id()` rejects it) but a SEG id can -- `FRONTBACK:fm04` is a
    real shipped shape, so `rsplit` would return the wrong half for exactly
    the frontback segments."""
    if not isinstance(dispatch_token, str):
        return None
    run_id, sep, rest = dispatch_token.partition(":")
    if not sep or not run_id or not rest:
        return None
    return run_id


def scan_dispatching_run_ids(segments_dir: Path) -> dict:
    """Every RUN_ID that has actually dispatched work, from the drafts' own
    `dispatch_token`. Returns `{"by_run_id": {...}, "drafts_scanned": N,
    "drafts_untokened": N}`. The counts are reported so a scan that silently
    matched nothing is distinguishable from a genuinely clean project."""
    by_run_id: dict = {}
    scanned = 0
    untokened = 0
    if not segments_dir.is_dir():
        return {"by_run_id": by_run_id, "drafts_scanned": 0, "drafts_untokened": 0}
    for path in sorted(segments_dir.glob("*.draft.json")):
        scanned += 1
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            untokened += 1
            continue
        run_id = draft_run_id(doc.get("dispatch_token") if isinstance(doc, dict) else None)
        if run_id is None:
            untokened += 1
            continue
        seg = doc.get("seg") if isinstance(doc.get("seg"), str) else path.name
        by_run_id.setdefault(run_id, []).append(seg)
    for segs in by_run_id.values():
        segs.sort()
    return {
        "by_run_id": by_run_id,
        "drafts_scanned": scanned,
        "drafts_untokened": untokened,
    }


# ---------------------------------------------------------------------------
# RUN_ID validation -- duplicated from resume_setup.py's own RUN_ID_RE, per
# the "no shared lib" convention. A run id read out of a draft's
# dispatch_token is not attacker-controlled in the ordinary case, but this
# script builds filesystem paths directly from it, so it is re-checked here
# rather than trusted transitively.
# ---------------------------------------------------------------------------

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_run_id(run_id):
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not _RUN_ID_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", "..") or ".." in run_id:
        return f"run id must not be '.' or '..' or contain '..'; got {run_id!r}."
    return None


class FatalError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE JSON
    payload on stdout (exit 1), never a bare traceback."""


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False))


def now_iso8601() -> str:
    """Matches ledger_update.py's own `now_iso8601()` -- the house format for
    a timestamp written into a durable-root artifact."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_ack(run_id: str, runs_dir: Path, segs: list, evidence=None) -> str:
    """Creates `runs/<RUN_ID>/.resume_gate_ack` with the same idempotent
    `O_CREAT | O_EXCL | O_WRONLY`, mode-0o644 create-only semantics
    `ledger_update.py:mark_ever_converged()` uses -- NEVER deletes or
    overwrites an existing marker.

    Returns "created", "already_present", or an "error: ..." string, so the
    caller can build an accurate report (the same three-way outcome
    backfill_ever_converged.py's own `mark_ever_converged()` returns).

    The body records WHAT is being acknowledged and WHEN. It is deliberately
    not an `input.digest` and deliberately not shaped like one: `gate_ran`
    is recorded as an explicit `false` so that a human reading this file
    later, or a future consumer, cannot mistake it for evidence the gate
    was satisfied."""
    run_dir = runs_dir / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"error: could not create {run_dir}: {exc}"
    path = resume_gate_ack_path(run_id, runs_dir)
    body = json.dumps(
        {
            "run_id": run_id,
            "gate_ran": False,
            "acknowledged_at": now_iso8601(),
            "acknowledged_by": "backfill_resume_gate_ack.py",
            # Which evidence half put this run id in scope: "drafts" (a
            # dispatch_token names it), "workflow_dir" (a template was
            # instantiated for it), or both. An empty dispatched_segs
            # alongside a workflow_dir-only evidence list is the normal,
            # expected shape, not a missing value.
            "evidence": sorted(evidence or []),
            "dispatched_segs": segs,
            "note": (
                "This run dispatched work before the resume-integrity gate was "
                "enforced. Its inputs were never digested and cannot be "
                "reconstructed, so no input.digest exists or can honestly be "
                "written for it. This file records that gap; it does not close "
                "it, and this run is not resumable."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return "already_present"
    except OSError as exc:
        return f"error: {exc}"
    try:
        os.write(fd, (body + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return "created"


def scan_workflow_run_ids(runs_dir: Path) -> list:
    """Every RUN_ID for which a Workflow template was instantiated, from
    `runs/workflows/<RUN_ID>/` -- the SECOND half of the Step 3 evidence.

    This must stay equivalent to select_segments.py's function of the same
    name, and the reason is a hard consistency requirement rather than
    tidiness: the gate blocks on the UNION of draft-derived and
    workflow-derived run ids, so a retrofit that could only acknowledge the
    draft-derived half would leave an operator facing a refusal with no way
    to clear it. tests/backfill_resume_gate_ack.test.py drives exactly that
    round trip (gate refuses -> --apply -> gate passes) for a workflow-only
    run id, and a drift pin ties the two scanners together."""
    workflows_dir = runs_dir / "workflows"
    if not workflows_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in workflows_dir.iterdir()
        if p.is_dir() and _RUN_ID_RE.fullmatch(p.name)
    )


def parse_only_runs(raw: str) -> list:
    seen = set()
    ordered = []
    for part in raw.split(","):
        part = part.strip()
        if not part or part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def run(args, dirs: dict) -> dict:
    runs_dir = dirs["runs_dir"]
    scan = scan_dispatching_run_ids(dirs["segments_dir"])
    by_run_id = scan["by_run_id"]
    workflow_run_ids = scan_workflow_run_ids(runs_dir)

    # The same UNION select_segments.py's gate blocks on -- see
    # scan_workflow_run_ids() for why acknowledging only the draft-derived
    # half would leave an unclearable refusal.
    evidence: dict = {}
    for run_id in by_run_id:
        evidence.setdefault(run_id, []).append("drafts")
    for run_id in workflow_run_ids:
        evidence.setdefault(run_id, []).append("workflow_dir")

    for run_id in evidence:
        problem = validate_run_id(run_id)
        if problem is not None:
            fatal(
                f"a draft's dispatch_token or a runs/workflows/ entry yielded "
                f"an unsafe RUN_ID: {problem}",
                run_id=run_id,
            )

    already_acknowledged = sorted(
        run_id for run_id in evidence if resume_gate_ack_path(run_id, runs_dir).exists()
    )
    gated = sorted(
        run_id for run_id in evidence if input_digest_path(run_id, runs_dir).is_file()
    )
    needs_ack = sorted(
        run_id
        for run_id in evidence
        if not input_digest_path(run_id, runs_dir).is_file()
        and not resume_gate_ack_path(run_id, runs_dir).exists()
    )

    if args.only_runs is not None:
        requested = parse_only_runs(args.only_runs)
        unknown = [r for r in requested if r not in evidence]
        if unknown:
            fatal(
                f"--only-runs names {len(unknown)} run id(s) that this project "
                f"shows no evidence of -- neither a draft dispatch_token nor a "
                f"runs/workflows/ entry: {', '.join(unknown)}. Refusing rather "
                f"than silently ignoring them -- a run id this scan cannot see "
                f"means you and the evidence disagree about what happened in "
                f"this project, which is worth resolving before writing an "
                f"acknowledgement.",
                dispatching_run_ids=sorted(by_run_id),
                workflow_run_ids=workflow_run_ids,
                drafts_scanned=scan["drafts_scanned"],
            )
        needs_ack = [r for r in needs_ack if r in requested]

    if not needs_ack and not args.allow_empty:
        fatal(
            "found ZERO run ids needing acknowledgement -- refusing to report "
            "this silently. An already-compliant project and a scan that "
            "matched nothing at all (wrong --durable-root, a moved segments/ "
            f"directory) look nearly identical here; this run scanned "
            f"{scan['drafts_scanned']} draft(s) and found "
            f"{len(evidence)} run id(s) with evidence "
            f"({len(by_run_id)} from drafts, {len(workflow_run_ids)} from "
            f"runs/workflows/). Pass --allow-empty to confirm that zero is "
            "what you expected.",
            durable_root=str(dirs["durable_root"]),
            dispatching_run_ids=sorted(by_run_id),
            workflow_run_ids=workflow_run_ids,
            gated_run_ids=gated,
            already_acknowledged=already_acknowledged,
            drafts_scanned=scan["drafts_scanned"],
            drafts_untokened=scan["drafts_untokened"],
        )

    created = []
    failed_to_create = []
    if args.apply:
        for run_id in needs_ack:
            # A workflow-dir-only run id has no drafts pointing at it (that
            # is exactly why the draft scan missed it), so the seg list is
            # legitimately empty here -- never a KeyError.
            outcome = write_ack(run_id, runs_dir, by_run_id.get(run_id, []), evidence[run_id])
            if outcome == "created":
                created.append(run_id)
            elif outcome == "already_present":
                # Raced with something else since the snapshot above -- not an
                # error, just not newly created by THIS invocation.
                pass
            else:
                failed_to_create.append({"run_id": run_id, "error": outcome})
                print(
                    f"backfill_resume_gate_ack.py: warning: could not create "
                    f"the acknowledgement for {run_id!r}: {outcome}. "
                    f"select_segments.py will keep refusing until it exists.",
                    file=sys.stderr,
                )
        created.sort()

    return {
        "success": True,
        "durable_root": str(dirs["durable_root"]),
        "plugin_root": str(dirs["plugin_root"]) if dirs["plugin_root"] else None,
        "applied": bool(args.apply),
        "dispatching_run_ids": sorted(by_run_id),
        "workflow_run_ids": workflow_run_ids,
        "run_id_evidence": {k: evidence[k] for k in sorted(evidence)},
        "segs_by_run_id": {k: by_run_id[k] for k in sorted(by_run_id)},
        "gated_run_ids": gated,
        "already_acknowledged": already_acknowledged,
        "needs_ack": needs_ack,
        "created": created,
        "failed_to_create": failed_to_create,
        "drafts_scanned": scan["drafts_scanned"],
        "drafts_untokened": scan["drafts_untokened"],
        "counts": {
            "run_ids_with_evidence": len(evidence),
            "dispatching_run_ids": len(by_run_id),
            "workflow_run_ids": len(workflow_run_ids),
            "gated": len(gated),
            "already_acknowledged": len(already_acknowledged),
            "needs_ack": len(needs_ack),
            "created": len(created),
            "failed_to_create": len(failed_to_create),
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "#409 Step 3: acknowledge, per RUN_ID, the runs that dispatched "
            "work before the resume-integrity gate was enforced. Never "
            "fabricates an input.digest. DRY RUN by default -- pass --apply "
            "to actually write."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually create the missing runs/<RUN_ID>/.resume_gate_ack "
            "markers. Without this flag the script makes ZERO filesystem "
            "writes and only reports what it would acknowledge."
        ),
    )
    parser.add_argument(
        "--only-runs",
        default=None,
        metavar="RUN1,RUN2,...",
        help=(
            "Acknowledge only these run ids instead of every un-acknowledged "
            "one found. FATALS if a named id was not found dispatching in "
            "this project."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Do not fatally error when zero run ids need acknowledgement (an "
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
            "self-anchored location -- replaces where segments/ and runs/ are "
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
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False),
            file=sys.stdout,
        )
        return 1

    mode = "APPLY" if result["applied"] else "DRY RUN (pass --apply to write)"
    print("=" * 70, file=sys.stderr)
    print("BACKFILL RESUME-GATE ACKNOWLEDGEMENTS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"durable_root: {result['durable_root']}", file=sys.stderr)
    print(f"mode: {mode}", file=sys.stderr)
    print(
        f"\ndrafts scanned: {result['drafts_scanned']} "
        f"({result['drafts_untokened']} carried no dispatch_token)",
        file=sys.stderr,
    )
    print(
        f"run ids with evidence: {result['counts']['run_ids_with_evidence']} "
        f"({result['counts']['dispatching_run_ids']} from drafts, "
        f"{result['counts']['workflow_run_ids']} from runs/workflows/)",
        file=sys.stderr,
    )
    for run_id in sorted(result["run_id_evidence"]):
        n = len(result["segs_by_run_id"].get(run_id, []))
        why = "+".join(result["run_id_evidence"][run_id])
        if run_id in result["gated_run_ids"]:
            status = "gate ran (input.digest present)"
        elif run_id in result["created"]:
            status = "ACKNOWLEDGED"
        elif run_id in result["already_acknowledged"]:
            status = "already acknowledged"
        elif any(f["run_id"] == run_id for f in result["failed_to_create"]):
            status = "FAILED to acknowledge"
        elif run_id in result["needs_ack"]:
            status = "needs acknowledgement (dry run)"
        else:
            status = "needs acknowledgement (not selected by --only-runs)"
        print(f"  - {run_id}: {status} [{n} draft(s), evidence: {why}]", file=sys.stderr)
    wf_only = [
        r for r, why in result["run_id_evidence"].items() if why == ["workflow_dir"]
    ]
    if wf_only:
        print(
            f"\n{len(wf_only)} run id(s) are visible ONLY as a runs/workflows/ "
            "directory, with no\nsurviving draft pointing at them:\n  "
            + "\n  ".join(sorted(wf_only))
            + "\nThat is the expected shape for a run whose drafts were later "
            "re-dispatched under\nanother run id -- a draft holds only its most "
            "RECENT dispatch token. They count\ntoward the gate exactly like "
            "draft-derived run ids.",
            file=sys.stderr,
        )
    print(
        f"\ngate ran for:         {result['counts']['gated']}\n"
        f"already acknowledged: {result['counts']['already_acknowledged']}\n"
        f"needs acknowledgement:{result['counts']['needs_ack']}",
        file=sys.stderr,
    )
    if result["applied"]:
        print(
            f"acknowledged this run:{result['counts']['created']}\n"
            f"failed:               {result['counts']['failed_to_create']}",
            file=sys.stderr,
        )
    print("\n" + "=" * 70, file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
