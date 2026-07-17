# Porting / adjusting the `canon_adjudication_audit.py` gate

A machine-checkable human-adjudication **gate** ported onto canon.json's entity-less model from the
methodology spec `historiettes-t3/audit_human_adjudications.py` (persisted `--init` template + `--check`
recompute-fresh + cross-check; source verdicts `confirmed_ok` / `adverse`; `--pair-review-cap` +
`degenerate_cap_overrides`; keys from stable IDs; exit 0/1/2).

## The 4 categories on canon.json's entity-less model

canon.json has NO `entity_id` / edges / reconciliation, so derive **entity = normalized
`canonical_target_form`**: `N(s)` = NFC + casefold + whitespace-collapse. Recompute fresh on every
`--check`. Cats 1–3 are scoped to proper-name entries (`is_proper_name` true, `basis != not_a_name`):

- **Cat 1 `duplicate_source_form`** — group entry RECORDS by `N(source_form)`; **≥2 records** (count
  records, not distinct field values — a hand-edited canon can have 2 map keys with the same
  `source_form` FIELD) → item, **regardless of target agreement**. (A "≥2 distinct target forms"
  precondition would be WRONG.)
- **Cat 2 `existing_merge`** — group by `N(canonical_target_form)`; **≥2 distinct `N(source_form)`** →
  item. (Distinct-*normalized* source, not raw — makes it disjoint from Cat 1.)
- **Cat 3 `candidate_missed_merge_pair`** — **EVERY unordered distinct-entity pair** (NO
  confusability/token filter — that silently drops the hardest case, the same entity under unrelated
  names) **MINUS pairs whose entities share an `N(source_form)`** (those are Cat 1's). Cap (default 40)
  at scope `"__canon__"`: over-cap → ONE cap-note + ZERO per-pair items. **Cap-override freshness:** the
  `__canon__` override must carry a matching FRESH
  `{entity_count, pair_count, cap, entity_set_fingerprint=sha256(sorted normalized entity set)}` —
  printed by `--check` for the human to copy; a stale fingerprint → blocking. This closes
  sign-once-bypass-forever; for any real canon ≥10 entities the cap is exceeded, so the NORMAL path is
  one signed risk-acceptance.
- **Cat 4 `review_queue_unresolved`** — the source's two-pass "correlated rejection" has NO analog →
  **documented UNPORTABLE**. Re-cast to draining `review_queue[]`: **every queued item is BLOCKING until
  drained (leaves the queue) OR risk-accepted** via `review_queue_risk_overrides[key]`. **NO
  `confirmed_ok`/`adverse` for cat 4** (a "confirmed_ok stays queued" verdict would bless unresolved
  research).

## Key design decisions

- **Iron-rule compliant:** the script only ENUMERATES items a human/codex must rule on + checks a
  verdict/override exists; it NEVER decides same/different. The adjudications file is authored by a human
  or a schema-validated codex workflow, NEVER by the script/Claude. Non-empty `reviewed_by` /
  `risk_accepted_by` is required (blocks anonymous verdicts).
- **Keys = `"{kind}::" + full sha256(canonical_json(identity))`** (canonical_json = `sort_keys`, compact
  seps). Maintain a `key→identity` map: same key + same id → dedup + warn; same key + **different id →
  FATAL exit 2** (never silent-drop). `source_form` is canon's unique primary key; identity structs fold
  in the full sorted member set → no silent conflation, deterministic run-to-run. (A truncated
  `sha1[:16]` key would be wrong.)
- **Fatal vs blocking:** structural canon/adjudications malformation + entry rows missing
  ENUMERATION-CRITICAL fields (`source_form` / `canonical_target_form` / `is_proper_name` / `basis`;
  queued: `source_form`) → **fatal exit 2, no stdout JSON**. Per-record content problems (bad
  `verdict_class`, empty `reviewed_by`/`reason`) → **blocking exit 1** with the summary. Absent canon →
  `canon_present:false` + exit 0 (W3 / `canon_validate` owns presence). A queued item missing only
  `note` is still enumerated (not fatal).
- **Output:** exactly one JSON line to stdout; the schema `canon-adjudication-audit-summary.schema.json`
  is a **top-level `oneOf` by `mode`** (the init-only line and the check line are different shapes);
  detail to stderr; exit 0/1/2; `--advisory` forces exit 0 on blocking but NOT on fatal. stdlib-only,
  self-anchored, persisted artifact `{durable_root}/canon_adjudications.json`.
- **NOT added to `cache_key.py` bundle tuples** (it is a gate like `final_audit` — excluded; avoids the
  count-word drift trap). Registered as an **OPT-IN** gate inline in SKILL.md (W3-adjacent), not
  force-wired as a mandatory W-step.

The gate went through many adversarial codex code-review rounds that produced reusable stdlib-JSON
gate-hardening classes (`json.loads` over-permits strict JSON several ways, each a crash/false-green,
funneled to one clean-fatal `_read_json_file` chokepoint; `bool ⊂ int`; jsonschema accepting `1.0` as an
integer; an O(E²) Cat-3 budget guard; a canon-absent early-return that skipped adjudications-path
validation). Those classes live in the schema-gate-hardening skill.

## Reusable spec-port methodology (why this is a codex-loop before code)

When porting a spec-driven gate/audit onto a target whose data model **lacks the source's structures**:
1. Derive the analog **mechanically** where one exists (entity = target-form grouping).
2. For a category with **no data source, declare it UNPORTABLE and document why** — mirroring how the
   source script itself declares OUT-OF-SCOPE categories owned by other checks — rather than force-fit a
   lookalike (a confusability-based Cat 3 or a `confirmed_ok`-based Cat 4 are exactly such lookalikes to
   reject) or silently drop it.
3. **Run the DESIGN through codex adversarially BEFORE building** — the category-mapping judgment calls
   are where fidelity breaks, and each fix can introduce adjacent ones, so loop until codex is explicitly
   clean. For plan-file codex review use **`codex exec`**, not the Agent/rescue-forwarder (it stalls).

## Files touched by the gate (6)

`canon_adjudication_audit.py` + `canon-adjudication-audit-summary.schema.json` +
`canon-adjudications.schema.json` + `tests/canon_adjudication_audit.test.py` + inline SKILL.md
registration + a CHANGELOG bullet.
