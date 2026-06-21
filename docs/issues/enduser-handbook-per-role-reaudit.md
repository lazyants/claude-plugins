# Per-role re-audit of the interactive surface

**Status:** deferred (out of scope for 1.0.6) · **Section:** enduser-handbook · **Surfaced:** 2026-06-20 (PR #11)

## Problem

The `surface-audit` mechanical pass (`assets/surface-audit.playwright.ts` + `assets/lib/control-inventory.mjs`) enumerates the interactive surface of whatever session it runs in — i.e. for the single role whose seeded `storageState` the capture uses. Many apps render a materially different control set per role: an admin sees destructive/bulk/settings controls an operator never does; a read-only viewer sees fewer still; a feature-flagged or plan-gated role sees yet another subset. A handbook audited against one role can therefore conclude "no delete exists" / "no export exists" when the control is simply gated to a role this run did not assume — the same wrong-conclusion class the audit exists to prevent, just along the role axis instead of the selector axis.

Today this is handled only by prose: `completeness-gate.md`'s disclose TRIGGER LIST item (5) says to disclose-don't-capture a control "gated to a role this chapter does not use", and the profile can point at one `storageState`. There is no mechanism to run the audit across N roles and diff the surfaces, so role-gated coverage gaps are found by human memory, not by the harness.

## Work

Add an **opt-in** per-role re-audit: let the profile declare multiple roles (each a label + `storageState` path), run the `surface-audit` enumeration once per role, and emit a per-role matrix plus a **diff** (controls present for role A but absent for role B). The diff is the high-value artifact — it turns "did we miss a role-gated control?" from recall into a mechanical check. Keep the single-role path the default (no profile change → today's behavior). Respect the v1.0.5 PII boundary unchanged (seeded data per role; human scrub). Watch-items: seeded fixtures must exist for each role (hermeticity per role); the diff must key on stable structural identity (`tag`/`role`/`name`/`data-testid`), not the now-broadened class/label fields, so cosmetic per-role label differences don't masquerade as surface differences.

## Notes

- Net-new candidate, deliberately deferred from the 1.0.6 tight 4-residual delta (user decision: ship the residual hardening, file the net-new ideas). This item keeps it tracked.
- Pairs with [the state-variant capture item](enduser-handbook-state-variant-capture.md): roles and states are the two axes along which a single capture run under-covers the real surface.
