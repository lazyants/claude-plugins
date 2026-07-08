# Assembly and output

## v1 scope: segment drafts and audit, not a book

`profile.yml`'s `output:` block has exactly two fields in v1:

```yaml
output:
  v1_scope: segment_drafts_and_audit
  destination: "/ABS/PATH/TO/YOUR_PROJECT/out/"
```

`destination` is where the audit/handoff package, not a book file, is
written. There is no `format` field and no book-destination field. This
plugin does not assemble an EPUB, a PDF, or any other single delivered book
file in v1. What it delivers, per project, is:

- every segment's converged draft (`segments/{seg}.draft.json`)
- the materialized ledger (`ledger.json`), a per-segment progress/status view
  built from `runs/ledger.d/*.json` fragments
- each draft's own `validate_draft.py` audit trail
- `final_audit.py`'s whole-project summary/WARN report (`final-audit-summary.schema.json`)

The default destination resolves inside `project.durable_root` as
`${durable_root}/out/`. Step 0 checks `output.destination`'s parent only when
the destination resolves outside `durable_root`; inside-root destinations are
created at Step 0a by `mkdir -p` of the specific resolved parent, including
non-default nested paths such as `${durable_root}/exports/final/report.md`.

The final audit summary is the machine-readable completion signal. It reports
`coverage_failures`, `stale_review_failures`, `hard_failures`, `warnings`,
`project_complete`, `completeness_counts`, `frontback_coverage`, and
`generated_at`, where `hard_failures == coverage_failures +
stale_review_failures`.

W7 runs `final_audit.py` over every converged segment. `coverage_failures`
are hard failures from re-running `validate_draft.py` against each current
converged draft. `stale_review_failures` are hard failures where the current
draft sha1 no longer matches that segment's ledger `reviewed_draft_sha1`.
`warnings` counts the four WARN-only advisory checks: glossary-diff,
link-graph, foreign-remainder scan, and verse-structure. WARN findings are for
human review; they are never auto-fixed by guessing.

`completeness_counts` uses exactly `not_started`, `recoverable`, `stale`,
`blocked_needs_regeneration`, and `human_escalation`. `human_escalation` is the
category for materialized `blocked` or `non_converged` statuses.
`project_complete == (every one of completeness_counts' five values == 0)`,
which means every `manifest.json` segment, including translate-decision
`FRONTBACK:{id}` units, classifies `reusable`.

There is one `frontback_coverage` entry per `manifest.json` `frontback[]` item.
Each entry has `id`, `decision: "translate"|"regenerate"|"omit"`, and
`status: string|null`. For `decision:"translate"`, `status` is the matching
segment's own classification. For `decision:"regenerate"` or `decision:"omit"`,
`status` is `null`. The field is always present, with an empty array when there
is no front/back matter.

That bundle — converged drafts plus the full audit trail — is the v1
deliverable. Turning it into an assembled, readable book is explicitly a
separate, out-of-scope step.

This scope boundary does not remove W6 or W7: the hand-maintained
`consistency_issues.md` consistency pass and the automated `final_audit.py`
final audit are both still in v1. W6 runs after every batch, before the next
batch starts; `consistency_issues.md` is never the output of an automated
script and is never read back in or acted on programmatically. Only book
assembly is out of scope.

At W8, the handoff report must list any `blocked`/`non_converged` segments and
surface W7's per-category counts alongside `project_complete`. It must keep
"this batch: N converged, zero hard defects" separate from "whole project: M of
TOTAL still incomplete"; a batch can succeed while the whole project is still
incomplete. v1 delivery must not mark the audit package complete while any item
remains `blocked` or `non_converged`.

## Why: `build_epub.py` exists but hasn't been generalized

The real reference project, `historiettes-t3`, has its own
`build_epub.py` (704 lines), confirmed to exist. It is **not**, however,
independently audited or generalized the way `final_audit.py` was before
being brought into this plugin as `scripts/final_audit.py`.

Concretely: `build_epub.py` exists in the source project, but the plugin
plan has not yet read it end to end, verified its actual behavior against
its own code (the same discipline already applied to `final_audit.py` —
trust the code, not the docstring or the plan's prior description of it),
or decided how much of it generalizes cleanly to arbitrary language pairs /
source formats versus how much is specific to Historiettes' own layout.

A v1.1 assembly effort should start by reading `build_epub.py` directly at
`/Users/moi/lazy-ants/development/historiettes-t3/build_epub.py` and
verifying its real behavior firsthand — not by guessing at its shape from
this reference or from the plan that preceded this plugin.

## Also out of scope for v1

- **No bilingual-output layout logic.** A bilingual EPUB or other bilingual
  layout is a plausible v2+ addition, but only once book assembly itself
  exists.

## Screencast-as-proof: a personal convention, not a plugin rule

One specific operator of this plugin treats
screencasting the final delivered book being opened in a reader as their
own personal proof-of-completion habit. That is a personal workflow
convention, not a rule this plugin imposes on other adopters. It is not
part of `SKILL.md`'s hard rules, and future users of this plugin are not
expected to follow it.
