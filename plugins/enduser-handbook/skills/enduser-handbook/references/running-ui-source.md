# Running UI is the source

## The principle

For an end-user handbook, the **running UI is ground truth**. Not the source code, not the
design files, not the spec, not the previous chapter, not the screenshots you took last week.
You document what a real user sees when they drive the surface **today**, in the build you
just captured.

This applies to whatever surface the project ships, as set by `stack.surface` in the profile:

- `web_ui` — the browser-rendered application as the user lands on it.
- `mobile_app` — the app as it renders on a real device or emulator at the captured commit.
- `cli` — the actual stdout/stderr a user sees when they run the command, in the locale set
  by `capture.locale`.
- `api` — the actual response bodies, status codes, and headers the live service returns to
  a real request, not the OpenAPI document's promise.

Everything else in the project that *describes* the UI is secondary evidence. You consult it
to know what to look for; you do not trust it to tell you what's there.

To enumerate the control surface from the running UI mechanically — rather than reading it
off the source — use a tool that walks the **live DOM** and captures each control's
text/title/aria-label/href/role (see the surface-enumeration pass in `completeness-gate.md`).
That enumeration guidance is the normative, engine-agnostic rule;
`../assets/surface-audit.playwright.ts` is a **non-normative reference implementation** for
the Playwright reference case — reimplement the driver glue for another engine; the
engine-neutral `../assets/lib/*.mjs` helpers are reused as-is.

## Why secondary sources lie

You will be tempted to skip a capture because "the code clearly shows the button is there" or
"the spec says it works like this". Resist. Each secondary source has a specific failure mode:

- **Design docs lie.** Mockups are aspirational. PMs revise them after engineering ships
  something different. Figma still shows v1; production runs v3.
- **Source code lies (selectively).** Feature flags, A/B splits, role gates, environment
  guards, lazy-loaded components behind unmet conditions. A `<SendButton v-if="canSend">` in
  the code does not mean the captured role sees it. The truth is whatever rendered in the
  screenshot.
- **Specs are aspirational.** They describe what the team intends to ship, not what is on
  the cluster right now. Stories close before edge-case wording lands; copy decks drift from
  the production strings the i18n bundle actually serves.
- **Yesterday's screenshot documents yesterday's UI.** Buttons get relabelled, columns get
  reordered, a confirmation modal becomes a toast. A screenshot is a dated artifact, not a
  standing fact.
- **The previous chapter is a peer, not an authority.** If the prior author guessed wrong or
  the UI moved underneath them, repeating their wording perpetuates the error.

The only authoritative source for what a user sees today is the UI rendered today by the
capture run you just executed against the build under `capture.command`.

## Implication 1: re-capture when the UI changes, even if steps "look the same"

When you update an existing chapter and the underlying feature has changed at all — new
build, dependency bump, copy revision, layout tweak — re-run capture. Do not reuse old PNGs
on the assumption that "the flow is the same". A renamed label, a moved button, a swapped
icon, a new empty-state illustration: each invalidates the artifact even when the *steps* in
prose read identically. The screenshot's job is to show the reader what they will see; if it
doesn't match what they will see, it is wrong even if it's beautifully composed.

Rule of thumb: if you cannot point at a capture run that produced the current artifacts on
the current build, you do not have evidence. Re-capture.

## Implication 2: when running UI disagrees with the spec, document the UI

If the spec / design doc / previous chapter say the screen should show A and the captured UI
shows B:

- The chapter documents **B**. That is what the user will encounter.
- You flag the discrepancy to the team — file an issue, leave a note in the chapter's commit
  message, or surface it in the consistency review — so engineering or product can decide
  whether the UI or the spec is the bug.
- You do not write the spec's wording into the chapter as wishful documentation. A handbook
  describing a screen the user will not see is worse than no handbook.

This holds even when B looks "obviously wrong". The handbook's job is to tell the truth
about the running app, not to pre-correct it.

## Implication 3: permission leaks are bugs, not features

If the captured UI shows a control, a row of data, or an entire screen that the role
operating it **should not** be able to see — judged against `capture.role_flags[role]`, the
project's documented permission model, or plain common sense about what that role does — do
not document it as a feature.

The visible-but-forbidden surface is a permission leak. You:

- Stop documenting it. Do not embed the screenshot, do not write steps.
- Flag it as a security defect to the team via the project's normal channel.
- Keep the captured PNG out of the published artifact set (delete it from
  `capture.output_dir` or move it to an out-of-tree location until triaged).

A handbook is a published artifact. Treating a leak as if it were intended documents the
bug for future users and customers, which compounds the original problem. The rule against
fabrication (see `references/anti-fabrication.md`) cuts both ways: do not invent surface
that isn't there, and do not legitimize surface that shouldn't be there.

## Quick check before you write any step

For each step you are about to write, you can answer yes to all three:

1. Did the current capture run produce the artifact that backs this step?
2. Does the artifact show the surface as it appears on the build under `capture.command`,
   not on a previous build or in a design doc?
3. Should the role under capture actually be able to see this, per the project's permission
   model?

If any answer is no, you do not write the step — you re-capture, flag the discrepancy, or
report the leak.
