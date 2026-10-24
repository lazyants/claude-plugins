# Check values in the project's context

The project's tests can encode facts absent from a message packet: a value is a sentence
fragment, a caller supplies an already-adjusted number, or abbreviations must match across
locales. Run those tests before operator acceptance and again before live export. The core's
syntax checks, model review and export comparison do not run them for you.

## Prepare an unchanged baseline

Find the project's documented i18n checks in its instructions, package scripts and CI, including
tests of callers that compose messages. Record the exact commands, working directory and
required tools. Make a disposable scratch copy of the current project, including uncommitted
catalog and code changes; a checkout of HEAD alone does not reproduce a dirty project.
Include the code, tests and configuration those commands need, not just the adapter's declared
catalogs. Install dependencies by the project's normal method. Catalogs and any other writable
inputs must be independent copies, never symlinks or hard links to the live project. Keep test
outputs and other writes in scratch too. When the workspace `R` sits inside the project, leave it
out of every scratch copy: it holds export backups and journals with live absolute paths. Any new
Git worktree must follow the host's worktree location rules.

Run the commands before applying values. Save the baseline output and exit codes under
`R/project-checks/`. If required checks are absent or cannot run, report what is missing and
stop the affected approval/export; do not call this a pass. If the unchanged baseline fails,
show the operator the failure and resolve it or agree an explicit, recorded check scope before
continuing. An unexplained failure cannot certify a candidate set.

## Before audit approval

Start from an unchanged scratch project for each attempt. Build `candidate-values.json` from
the current audit proposals, using exactly their ids and values:

```json
{"values": {"app:settings.title": "Einstellungen"}}
```

Use the accepted adapter through `adapter_client.export(root, cfg, scratch_project, locale,
values)` with `values` set to that object's `values` map. This is the existing adapter client;
pass the real workspace as `root`, its loaded config as `cfg`, and the **scratch project path**
as `scratch_project`. It runs the adapter's export command in that directory. Check adapter
options for write destinations outside scratch before running it. The model returns values;
the adapter writes them. Never overlay values into the live project.

`export_values.py` is not a preview command for unaccepted audit proposals: it selects only
exportable ledger candidates. Do not manufacture operator approvals or review verdicts, or
change the real ledger, to make a proposal appear in a preview. Before approval, use the
adapter overlay above; before live export, use the normal exporter as described below.

Run the recorded project checks with all current proposals for the locale overlaid. Save the
exact id-to-value map, project snapshot/diff, commands, output and exit codes under
`R/project-checks/`. Beside each id named by a failing test, show "the project's tests reject
this", its proposed value and the relevant failure. Keep these findings alongside
`report.py`'s report; that script does not import project-test output. If the failure names no
message, show it as a set-level problem and stop approval of that set until attributed or
resolved. Do not guess which id caused it or approve the remaining set without checking it.

Fix or remove rejected proposals, rebuild scratch from the unchanged baseline and rerun the
checks on the resulting set. Give the operator the report and these results together, before
`packets.py accept-audit`. Supply relevant test failures as context to subsequent model turns.
Every replacement, including a review counter-proposal, invalidates the earlier result for
that value. Script checks, operator acceptance and the exact-value model review still apply.

## Before live export

After the review loop, build a fresh scratch project from the current live project. Create a
separate scratch workspace with copies of `localize.json`, `messages.json`, `canon.lock.json`,
`ledger/` and the adapter files/acceptance needed to run the exporter. **Do not copy `exports/`**:
old journals contain absolute destination paths, and recovery runs before any export, even a
dry run. Start with no export history in the scratch workspace.

Set only the scratch config's `project_root` to the scratch project, checking any relative
paths and adapter options. Validate that config. If relocating the adapter changes its digest,
run its normal acceptance procedure in scratch; never edit `adapter.lock.json` to bypass it.
Leave the real workspace, its config, ledger and journals unchanged.

Run `export_values.py --root <scratch workspace> --locale L` without `--dry-run`. Check its
export count and the scratch project diff against the intended final id-to-value set; zero
exports when candidates were expected is not a successful rehearsal. Run the recorded i18n
checks on that exported scratch project and retain the same evidence as above. With several
locales, also check the final combined locale changes if project tests enforce cross-locale
invariants. No failing or unrun required check may be reported as passing.

Fix any new failures through the normal candidate/review loop, then rebuild and recheck.
Export live only the exact set just tested, against the same project state; changes to values,
code, catalogs or test setup require another scratch check. Run the project's usual checks and
review its full diff after live export too.
