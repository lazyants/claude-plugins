# Sentinel backfill — reading `backfill_ever_converged.py`'s report

**Read this when, and only when:** you are about to trust a clean
`backfill_ever_converged.py` report on a live, synced or networked project; or a
dispatch has refused a segment as `lost_sentinels`; or a segment's ledger row and
its marker disagree and you need to know whether anything ever observed that
convergence.

This is a **one-time legacy migration**, not a per-run rule. It concerns projects
that converged segments before `segment_dispatch_driver.py` entered
`PLUGIN_BUNDLE_MEMBERS` (`SKILL.md`'s #409 upgrade note). A project that has never
been touched by an older plugin has nothing to backfill and is told so by the
report itself. `SKILL.md` keeps the six-item report checklist and the two
operational instructions; the evidence and the residual live here.

See `backfill_ever_converged.py`'s own module docstring for the full mechanism and
CLI contract.

## The known limitation (#442/#443/#621)

## The known limitation (#442/#443/#621)

  **Known limitation, narrower than it was but not closed. Read this before
  trusting a clean report on a live or networked project.** Every sentinel
  lookup now goes through the directory descriptor the run holds — the census
  and the writer's `EEXIST` re-read alike — so no read can land in a
  different directory than the one the run opened. **That settles WHICH
  DIRECTORY and nothing about the entries inside it**, and two mechanisms
  reach a wrong answer without ever touching the pathname:

  - a sync client or restore tool rewriting sentinel entries **in place**,
    which leaves the directory inode unchanged, so the identity check sees
    nothing wrong;
  - a sentinel simply deleted after the census classified it PRESENT.

  A third is only partly closed: network-filesystem failover, remount or
  snapshot switching now surfaces as AMBIGUOUS and fails the run **if it
  invalidates the descriptor**, but a silent switch that keeps it valid does
  not.

  **What the dispatch gate now does about it, and what it still cannot do
  (#442).** The consequence used to be silent retranslation outright:
  `select_segments.py` gated only the segments it found PRESENT, so a marker
  that had since gone absent left that segment eligible and the refusal that
  would have protected converged work never fired. It now fires for the case
  the marker's own writer makes impossible: `ledger_update.py` cannot publish
  a `converged` ledger record without first writing the marker, so a selected
  segment whose materialized record says converged/stale while its marker
  reads ABSENT is refused, reported as `lost_sentinels`, and pointed at
  `backfill_ever_converged.py --apply` as the non-destructive remedy. The
  ledger status is a second witness in a different directory, written by a
  different writer, and one deleted marker no longer removes both.

  **The residual, stated as a state rather than a promise.** That second
  witness is mutable. A unit whose status has ALSO moved off converged/stale
  has neither witness left, classifies as `recoverable`, and is still
  dispatched silently. Two routes reach that state, and one of them needs no
  earlier re-dispatch at all: convergence raises the marker *before* it
  commits the ledger fragment, so a run killed between those two steps leaves
  a finished, reviewed unit at `in_progress` with its marker up — delete the
  marker after that and nothing remembers. (The other route is an
  authorized re-dispatch interrupted after the driver's own `in_progress`
  write.) **#443 shipped a content-bearing marker and did NOT close this** —
  provenance describes a marker that exists, and a deleted one has nothing
  left to describe. Closing it needs a second durable witness this gate can
  read when the marker is gone: the convergence record committed together
  with the marker rather than in two directories with two durability
  stories, or an append-only convergence journal. That remains open on #442
  itself, with the dispatch-time race tracked separately as #621.
  Until then: treat a clean run as evidence about the moment it ran, and
  re-run it immediately before dispatching rather than relying on an earlier
  result.

  **Two earlier drafts of this note were wrong in opposite directions, which
  is why it is worth reading rather than skimming.** The first said the
  failure needs something renaming `segments/` — an understatement, since a
  rename is one mechanism and not a precondition. The second said closing it
  "needs a locking protocol honoured by everything that can touch
  `segments/`" — an overstatement that survived several review rounds because
  a limitation that sounds cautious never gets attacked. The descriptor was
  already open; the census simply was not using it, and PR review reproduced
  a clean report about a directory the project was not using.

## `sentinel_attribution`

**`sentinel_attribution` is not on SKILL.md's six-item checklist, and
report also names, for every marker it found ALREADY present, which writer
the marker SAYS published it — `ledger_update` (earned at a real convergence,
carrying that convergence's reviewed draft sha1, plus its run token, and the
round label when the recording call supplied a run token and a non-empty label
could be read off the review artifact — evidence is all-or-nothing, so a
marker whose evidence would not fit records the identity fields alone), `backfill_ever_converged`
(retrofitted from a ledger row by a run of this script), `unattributed`, or
`unreadable`. It is a DIAGNOSTIC: it moves no bucket, no count and not
`success`, and no gate anywhere reads a marker's body. It is also
**self-reported, not authenticated** — nothing signs the marker, so the value
is that the plugin's own writers now record their evidence for you to check,
not that a claim of authorship proves anything on its own. **`unattributed` does not mean unprotected.** Every marker written
before this field existed is unattributed and protects exactly as it always
did — that is what makes the change safe to adopt on a project mid-flight,
and reading it as a defect would invert it. What the field is FOR is the
question that previously had no answer on disk at all: when a segment's
ledger row and its marker disagree, whether anything ever observed that
convergence, or whether the marker was merely asserted.
