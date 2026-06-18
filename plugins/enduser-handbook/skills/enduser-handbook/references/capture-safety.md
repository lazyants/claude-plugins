# Capture safety — never fire the live action

Capture runs against the real running app. The app is wired to live integrations and real
data — your screenshots are evidence of what the UI looks like, not a license to make the
app *do* anything. This file is the discipline. It applies whether or not the profile lists
any examples.

## The principle

When you drive the app to capture a feature, you capture **view-state only**. You open the
page, you open the overlay, you read the labels — and you stop there. You never click a
control whose effect is irreversible, leaves the system (external API), mutates production
data, or sends something to a real customer. The function is still covered: you document
the open-state and disclose what the button would do. The ban is on *triggering* it, not on
documenting it.

A function the role can reach but cannot safely fire is **not** dropped from the chapter.
It is captured at its read-only/open state, and the prose tells the reader what the
control does and that the screenshot stops short of pressing it. Silent omission is a
defect; intentional disclosure is correct.

## Reading `capture.live_action_examples`

The profile's `capture.live_action_examples` is a list of concrete, project-specific
actions the running app exposes that must never be fired during capture. You read this
list before you start capturing and you keep it in mind as you navigate. Treat the list as
illustrative, not exhaustive — its purpose is to teach you the *shape* of dangerous
actions in this project so you can recognize the next one even when it is not listed.

If the list is empty you still apply the principle. An empty list means the project team
has not enumerated examples yet; it does not mean the app has no live paths. Default to
caution: any button labeled with a verb of dispatch, replay, send, confirm, commit,
delete, sign, submit-to-external, irrevocable-anything is suspect until proven read-only.

Before clicking any control in a capture run, you ask yourself: is this control in
`capture.live_action_examples`, or shaped like one of those entries? If yes, you stop at
modal-open and you do not click further.

## The PII rule — `capture.pii_categories`

The profile's `capture.pii_categories` lists categories of personally identifying or
sensitive content that must not appear in screenshots without explicit user sign-off. You
read this list and you treat it as a screenshot filter, not just a navigation hint.

Three options when a PII category would appear in your shot, in order of preference:

1. **Skip the shot.** Document the function in prose with a one-sentence disclosure that
   explains why no screenshot is included.
2. **Mask the PII.** Replace identifying values with placeholders before saving the PNG —
   names, addresses, account numbers, document contents, scanned identity material. The
   mask must be visually obvious so a reader does not mistake masked content for real
   data.
3. **Ask the user to sign off.** Only when masking is impractical and the screenshot is
   essential. The sign-off is per-shot, in writing, and recorded in the chapter's
   commit/PR notes. Do not assume a previous sign-off carries forward to a new shot.

If `capture.pii_categories` is empty you still apply the principle for any obviously
sensitive content (real names, real financial data, real medical or legal documents).

## Disclosure in prose

When the chapter covers a live or irreversible function, the prose names the action and
its effect, then names the limit of the screenshot. Two patterns, both required as the
situation calls:

- **Read-only function:** describe what the reader sees and what they can do. No
  disclosure needed beyond the normal step text.
- **Live/irreversible function:** describe what the control does, then state that the
  screenshot stops at the open-state. Example shape: "The {{action_label}} button sends
  the record to {{external_system}}. The screenshot below shows the dialog at open; the
  send is not performed in capture." Replace placeholders with the project's real label
  and the prose register from the profile's `language.register`.

The reader learns the function exists and what it does. The capture run never causes the
side-effect. Rule 6 (completeness) is satisfied; the safety rule is satisfied.

## Screenshot the warning dialog, not the after-state

For destructive or irreversible actions the app usually shows a confirmation dialog —
"Are you sure?", "This cannot be undone", a typed-confirm field. **Capture the warning
dialog**, not the post-confirm screen. The warning is the meaningful UI for the reader:
it tells them what they are about to do and what the system thinks the consequences are.
The post-confirm state requires you to actually fire the action, which is the exact
thing you must not do.

Apply the same pattern to multi-step send flows: capture the final-review screen with
the "Send" button visible and labeled; do not capture the "Sent" confirmation.

For success-toast screenshots (a different case — a reversible mutating action whose
outcome the reader needs to see), only fire the action when the side-effect is safely
reversible *and* the data state is staging/seed, never production. If in doubt,
screenshot the dialog at open and disclose the outcome in prose.

## Quick self-check before each click

Before you click any button during capture, run the checklist:

1. Is this control listed in or shaped like `capture.live_action_examples`?
2. Does its label contain a dispatch/send/commit/delete/sign verb?
3. Would the click hit an external system, a real customer, or mutate non-reversible
   state?
4. Would the resulting screenshot expose anything from `capture.pii_categories`?

If any answer is yes, stop at the current state, capture it, and disclose the action in
prose. Resume navigation by closing the dialog or backing out — not by clicking through.
