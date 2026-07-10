# Revalidation — auditing an already-merged chapter

The author workflow (W1–W5) builds a chapter from scratch and halts for manifest
review before any capture. That path is **author-only**. It does not cover the
recurring task of checking whether a chapter that already shipped still matches
the running app after the feature moved underneath it. Revalidation is that
missing audit path.

You revalidate when a merged chapter may have gone stale — a dependency bump, a
copy revision, a layout tweak, a periodic pre-publish sweep — and you need to
know what, if anything, to refresh, without re-running the full author halt for
scope the user already accepted.

## The flow

1. **Re-derive the feature surface from the running UI.** Same as W1: drive the
   live surface and enumerate it today, per `running-ui-source.md`. The running
   UI is ground truth; the existing chapter and its screenshots are dated
   artifacts, not authority.
2. **Diff against the existing chapter and its manifest entry.** Compare the
   re-derived surface — routes, roles, steps, glossary terms, side-effect
   classes, every interactive trigger — against what the merged chapter and its
   capture-manifest entry currently say.
3. **Classify each delta into exactly one of three classes:**
   - **no-op** — the re-derived surface is identical to the accepted chapter +
     manifest. Nothing to do.
   - **accepted-diff** — an observed UI / prose / screenshot **refresh within
     the already-accepted manifest scope**: NO changed manifest field, NO
     added or removed step, NO route / role / glossary / side-effect change, and
     NO newly discovered interactive trigger. The control set and meaning are
     unchanged; only the rendered artifact moved (a restyled button, a reworded
     label that maps to the same step, a refreshed screenshot). Re-capture and
     re-author the refreshed artifacts; no halt.
   - **material** — anything else: a changed, added, or removed control or step,
     a route / role / glossary-term change, a side-effect reclassification, or a
     newly discovered interactive trigger.
4. **Halt on any material delta.**
   Revalidation skips only the initial accepted-manifest review for no-op or accepted-diff unchanged scope. Any material delta — to route, role, steps, glossary terms, side-effect class, or a changed/added/removed control or newly discovered interactive trigger — emits a delta manifest and halts for user acceptance per `manifest-discipline.md`.
   You do not re-capture a material delta before that acceptance closes.
5. **Re-capture and re-author only the deltas.** Refresh artifacts for the
   accepted-diff deltas and for the material deltas the user accepted in step 4.
   Untouched scope keeps its existing artifacts; you do not re-shoot a no-op.
6. **Run the completeness gate.** Build the coverage matrix and block on any
   unresolved row, exactly as on first authoring — see `completeness-gate.md`.
   Revalidation never publishes on a stale or incomplete matrix.

## How this differs from W1–W5

W1–W5 always halt for manifest review because, on first authoring, **all** scope
is unaccepted — there is no prior bless to lean on. Revalidation starts from a
manifest the user already accepted, so it can refresh artifacts inside that
accepted scope without re-asking. The carve-out is **bounded strictly to
unchanged scope**: the no-op and accepted-diff classes are defined so that
nothing which would alter what the user reviewed can slip through them. The
moment a delta touches a manifest-auditable field — route, role, step, glossary
term, side-effect class — or surfaces a control the manifest never enumerated,
it is **material**, and material always routes back through the
`manifest-discipline.md` "When the manifest changes" halt.

This is why the carve-out is not a loophole: it skips the review only where the
re-derived surface is provably identical to what was already blessed. Any
deviation that a reviewer would want to see is, by definition, a material delta
that emits a delta manifest and halts.

For a role-axis diff of the interactive surface — how it differs between roles rather than over time — see [surface-diff.md](surface-diff.md).
