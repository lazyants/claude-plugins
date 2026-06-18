# Anti-fabrication

Every documented step traces to a real route and a real screenshot. Every UI label is quoted verbatim from the running app. If you cannot capture a step, you say so in the prose — you do not narrate around the gap.

This file is the discipline. The base SKILL.md and the chapter template enforce it; you apply it on every step of every chapter.

## Why fabricated steps are worse than missing steps

A missing step is **visible**: the reader sees a gap, asks, and the gap gets filled. A fabricated step is **silently wrong**: the reader follows it, the button isn't where you said, the label isn't what you wrote, and the reader concludes the handbook is unreliable. One fabricated step poisons the rest of the chapter, because the reader can no longer tell which steps were verified and which were invented.

The cost asymmetry:

- Missing step → one open question, fixed in the next pass.
- Fabricated step → loss of trust across the whole handbook, plus a real user wasting real time hunting for a control that doesn't exist (or exists with a different label, in a different place, behind a different role).

You always choose the visible gap over the silent lie.

## The trace-to-evidence rule

For every numbered step you write, you must be able to name three things:

1. **The route the step lives on** — the URL or in-app navigation path the screenshot was captured at. This comes from the capture manifest entry (see `manifest-discipline.md`); it is one of the routes discovered in the surface-discovery phase using `stack.backend.route_globs` / `stack.frontend.page_globs`.
2. **The screenshot file that proves it** — the PNG written by the capture run, under the path the profile's `capture.output_dir` resolves to. The chapter embeds this file by relative path. No embed → no proof → no step.
3. **The verbatim label** — the exact text the user sees on the control they are supposed to click, copied character-for-character from the screenshot (or, when ambiguous, cross-checked against the running app's source-of-truth string in the running UI; see `running-ui-source.md`).

If any of those three is missing, the step does not get written. You do not promise to "fill it in later" — later means it ships fabricated.

Concretely, before you write a step, you check:

- Does the manifest entry for this chapter list this overlay/control? If not, the surface-discovery pass missed it and you stop to extend the manifest first.
- Did the capture run produce a PNG for this step? If not, the capture failed or was gated by a role/seed-data/permission flag (see `capture.role_flags` and `capture-safety.md`) — you disclose the gap in prose; you do not narrate the click.
- Is the label you are about to type the exact bytes shown in the PNG? If you are paraphrasing or "cleaning up" the label, stop.

## The label-fidelity rule

Copy UI labels exactly. That means:

- **Exact casing** — if the button is `SAVE`, you write `SAVE`, not `Save`. If it is `speichern`, you write `speichern`, not `Speichern`.
- **Exact punctuation** — trailing colons, ellipses (`…` vs `...`), brackets, parentheses, asterisks, all preserved.
- **Exact articles and connector words** — if the menu reads `Edit the item`, you do not shorten to `Edit item`.
- **Whatever language they appear in** — labels are quoted in the language of the running UI, regardless of the language the surrounding prose is in. A handbook written in `language.code: de` quotes English labels in English when that is what the app actually displays; a handbook in `en` quotes German labels in German. The surrounding sentence is in the chapter language; the label inside the quote marks is in the UI's language.
- **Quote marks per project style** — the *outer* quote style (curly vs straight, single vs double, low-9 vs guillemets, locale-specific pairs) follows the chapter's locale conventions from the style guide; the *inner* string is the literal UI text.

When the label changes in the app, the chapter is wrong until the screenshot AND the quoted string are both refreshed. There is no "the label is basically the same" — it either matches or the step is stale.

## "If you cannot capture it, say so"

Some controls are real but uncapturable in the current capture run. Common reasons:

- The role used for capture lacks a permission flag listed in `capture.role_flags`, so the control does not render.
- The seed/staging data does not produce a row that makes the overlay reachable (an empty list has no row to click).
- The action is irreversible or hits a live external system — capture stops at the read-only open state per `capture-safety.md` and the profile's `capture.live_action_examples`.
- PII rules in `capture.pii_categories` forbid the screenshot.

In every one of these cases you do **not** invent a step. You write one sentence of disclosure in the chapter prose: that the function exists, why it is not shown in full, and (where useful) what the reader needs to invoke it. The function is then **covered** — documented-or-disclosed — without being fabricated.

What disclosure looks like in practice:

- Read-only open state captured, action not fired → embed the open-state screenshot, then a sentence in the project's voice explaining that the final confirm/send is intentionally not shown because it would hit a live system.
- Control not reachable for the capture role → a sentence naming the role or permission that exposes the control, with no screenshot.
- PII-blocked → a sentence saying the overlay contains customer data and is not screenshotted; the function is described without the embed.

You never write a numbered step (`4. Click on …`) for a control you did not capture. Numbered steps imply "I watched this work." Disclosure prose is the honest shape for everything else.

## Self-audit before publish

Before the chapter goes out, you walk every numbered step and ask: which PNG proves this, which route was it captured on, where did this label come from. If any step fails the trace, you fix it — either by re-capturing, by demoting the step to disclosure prose, or by deleting it. The completeness gate (`completeness-gate.md`) is the audit's outer loop; this file is the per-step contract that feeds it.
