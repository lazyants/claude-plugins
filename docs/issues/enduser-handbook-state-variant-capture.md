# State-variant capture (empty / error / permission-denied)

**Status:** deferred (out of scope for 1.0.6) · **Section:** enduser-handbook · **Surfaced:** 2026-06-20 (PR #11)

## Problem

Capture and audit run against whatever single state the seeded environment happens to render — almost always the **happy/populated** state. The states a real user most needs documented are often the other ones: an **empty** list (first-run, "no records yet" with its call-to-action), a **validation/error** state (a 422 form, a 500 on a missing prerequisite), a **permission-denied** state (a gated control that renders disabled or a 403 surface), and **loading/partial** states. A handbook built only from the populated state silently omits exactly the screens where users get stuck.

v1.0.6 added the disclose TRIGGER LIST (`completeness-gate.md`), whose item (1) tells the author to disclose-don't-capture when a target errors because a prerequisite is absent — but that is a *disclosure* rule, not a *capture* mechanism. There is no harness support for deliberately driving the app into an empty/error/denied state and capturing it, so state coverage depends entirely on the author noticing the gap.

## Work

Add **opt-in** state-variant capture: let a capture spec declare a state precondition (e.g. seed an empty dataset / point at an empty-fixture `storageState`; intercept a backend route to force a 4xx/5xx with the capture guard's existing route layer; assume a role that lacks a permission) and capture the resulting screen as a labelled variant of the same page. Surface a per-page **state-coverage checklist** (populated / empty / error / denied) in the completeness gate so missing variants are visible. Reuse the existing fail-closed `installCaptureGuard` route machinery for the forced-error injection rather than adding a new interception path. Keep happy-state capture the default. Watch-items: forced-error fixtures must stay hermetic (no live external calls — the guard already blocks those); a forced 500 must be visibly disclosed as synthetic in the handbook, not presented as a real defect.

## Notes

- Net-new candidate, deliberately deferred from the 1.0.6 tight 4-residual delta (user decision: ship the residual hardening, file the net-new ideas). This item keeps it tracked.
- Pairs with [the per-role re-audit item](enduser-handbook-per-role-reaudit.md): states and roles are the two axes along which a single capture run under-covers the real surface.
