# Verification methodology: the adjudicated-convergence loop

When verifying an LLM-built name/realia canon (or an extraction, or a translation) and deciding
HOW to check it, run a LOOP shaped like the plugin's `translate → gate → review → fix` pipeline —
but with a **blind adjudication as the convergence gate**, not raw findings. Established by a full
2×2 bakeoff (model × grounding) on the SSK vol.2 slice, 231 pooled findings, blind-adjudicated to
134 real defects.

## The recipe (reusable, also for translations)

> per-chapter **source-grounded** review on the **SAME model, fresh session** (primary — high
> recall) **+ a cross-model pass for the tail** (recall booster) → **pool** → **blind typed
> adjudication against the source** (fabrication / unattested-but-true / genuine-drop / genuine-gap
> / not-a-defect — the precision filter) → apply only the REAL fixes → **re-review the fixed
> artifact** → repeat.

## Empirical findings that justify each step (don't re-derive — measured)

1. **Source-grounding is non-negotiable.** Reading the real source caught **97%** of real defects;
   diff-only (comparing two outputs without the source) caught **9%**. A reviewer that only compares
   versions is nearly blind.
2. **Same-model fresh review beats cross-model** as the primary: Sol-source recall 82% / precision
   59% vs Terra-source 49% / 49%. The plugin-native discipline (codex reviews codex in a fresh
   session) is the right default.
3. **Cross-model still earns its place as a tail pass:** the other model uniquely caught ~15% of
   real defects the primary missed. Union of the two source methods = 97% recall. Cross-model =
   recall booster, not the base.
4. **Agreement ≠ truth.** Findings both methods agreed on had LOWER precision (47%) than the union
   (58%) — two models share the same strict-attestation blind spot and agree on NON-defects. See the
   `feedback-convergence-needs-two-sound-methods` memory topic. Never treat "2 methods agree" as gold.
5. **A blind adjudication/filter is essential.** Raw reviewer precision is only ~50-60%; ~40% of
   findings are "unattested-but-true" (correct general knowledge flagged as unsupported) or scope
   choices. Without adjudication you would "fix" ~40% non-defects.

## The stop condition (the part that bites)

**"Loop until no findings" is UNSATISFIABLE here and must not be used.** The reviewer perpetually
re-flags correct general knowledge ("Rebbe Nachman of Breslov" isn't in *this* chapter), so raw
findings never reach zero. This is the non-convergent case described in
`skill:review-loop-discipline` → `references/converge-and-exit.md`.

- **Stop = no NEW real defects after adjudication** (convergence on the adjudicated set, not raw
  findings), with a **round cap** (the `max_fix_rounds` analog — ~2-3; each round is ~40 agents).
- **Re-review after every fix batch is mandatory** — fixes regress (see `feedback-red-before-green` /
  "review is a LOOP, not a pass").
- **Scope questions are OUT of the loop** — "should ritual realia (maror, chuppah) or generic
  concepts (the Torah, Hasidism) be canon entries?" never resolve by looping; decide the scope rule
  ONCE (a human call), then they stop being flagged. Escalate residual + any oscillation.

## Adjudicator-bias guard (validated)

The blind adjudicator being a different model than a finder does NOT auto-favor that finder: the
Terra adjudicator rejected MORE of Terra's own findings (51%) than Sol's (41%) — a cross-model judge
still ranking the other model higher is a strong signal (same logic as the Terra-judge in
`skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`). Still finish with a human eyeball on
categories/scope — the adjudicated set has scope-vs-defect noise.

## Refinements from actually RUNNING the loop (SSK, round 0 → round 1)

Round 0 fixed 99 adjudicated real defects (removed 11 spurious, edited 18, split 1, added 26 real
names/works; 292 entities). Round 1 re-review then surfaced three things that sharpen the recipe:

1. **Scope must feed BACK into the convergence GATE, not just be skipped in fixing.** The reviewer
   re-flags the scope-excluded items (God/Hashem/Messiah/generic realia) EVERY round, and the
   blind adjudicator — which does not know your scope policy — keeps labeling them `genuine_drop`
   (10 of round-1's 45 "real defects"). So the loop is non-convergent even on the ADJUDICATED count
   unless a persistent scope-exclusion list is subtracted from "new real defects." Carry the scope
   decision forward as a filter, or you loop forever re-discovering God/Messiah. **The same
   axis-confusion bites in the REMOVE direction too, and there it is SILENT:** when the reviewer
   proposes deleting a scope-violating entry, the adjudicator rejects it `not_a_defect` — reasoning,
   correctly, that the entry IS a distinct, explicitly identified entity. Entity-hood and scope are
   different questions: the adjudicator answers "is this a real distinct entity?", the scope policy
   answers "does this CATEGORY belong in the canon at all?", and only the user owns the second. So an
   adjudicator `not_a_defect` must never veto a scope ruling — the applier needs an explicit,
   per-round **force-apply** override file alongside the force-skip one, or every category the user
   excluded quietly survives into the final canon while the loop reports itself clean.
2. **The adjudicator itself has attestation NOISE** — it flagged clearly-present terms ("the Land of
   Israel", "Rosh Hashanah", "Simchat Torah", verified present & attested in the canon) as
   `fabrication`. So a fully-automated stop criterion is UNSAFE; the human eyeball on the adjudicated
   set is load-bearing, not garnish. `+ my eyeball` is a required stage.
3. **A fix step regresses — re-review caught it.** Round 0's `split` op created a DUPLICATE entry
   (one tangled entity flagged 9× in round 1). Live confirmation of "review is a LOOP"
   (`feedback-red-before-green`). Apply structural ops (dedup, split) deterministically and
   re-review them.

Net: raw "45 new defects" collapsed under eyeball to ~10 scope (escalate), 1 self-inflicted split
bug (fixed), and a handful of genuine description over-assertions amid adjudicator noise — i.e. the
loop CONVERGED fast (99 → ~a dozen); the tail is judgment (scope) + noise, which is the EXIT signal,
not a reason to grind more automated rounds.

4. **THE BIG ONE — per-chapter review has a cross-chapter FALSE-POSITIVE blind spot.** Round 2's fix
   pass (Sol, given the WHOLE canon + a reject option) rejected **24 of 25** round-1 "defects" as
   NOT real — because a per-chapter reviewer sees only ONE chapter, but a canon entry's identity is
   usually supported ACROSS chapters. So it flags a multi-chapter entity's description as
   "fabrication/unsupported HERE" when chapter N elsewhere supports it (e.g. "Tcherin was never
   visited" ← ch10 says it was; "Krasinshteyn not wealthy" ← ch5 says he was; "Shlomo of Haysin
   missing" ← already in the canon). The per-chapter ADJUDICATOR shares the blind spot and confirms
   the false positives. **Consequence for the recipe:** the source-grounded reviewer/adjudicator
   MUST be given a whole-book / whole-canon view (or a final whole-corpus reject pass) before a
   finding is trusted — otherwise precision on multi-chapter entities is illusory. This is why
   round-1's "87% adjudicated precision" was really ~4% once cross-chapter context applied.
   - **Retro-caveat this forces:** the round-0 FIX prompt did NOT include the "verify each / reject
     false claims" guard (only round 2's did), so round-0's 18 description EDITs + 11 REMOVEs were
     applied credulously and may contain the same cross-chapter false positives (over-stripping
     correct epithets). A pristine canon needs ONE final whole-corpus re-verification of the APPLIED
     changes, restoring anything wrongly stripped. Lesson: give the fix pass the reject-guard AND the
     whole corpus from round 0, not just at the end.
   - **CONFIRMED quantitatively:** the whole-corpus re-verification of round-0's 29 applied changes
     found **16 were over-corrections** (~55%): 2 real entities wrongly removed + 14 descriptions
     over-stripped, all restored. Only 13 (9 removes + 4 edits) were genuinely correct. A per-chapter
     fix applied without the whole-corpus reject-guard damaged more than half of what it touched.

## Enforcing a scope policy over an already-built canon (whole-canon purge pass)

Sweeping the scope tail one review round at a time is slow: each round only sees the instances inside
its own chunk, so a newly-added exclusion category takes several more rounds to work through the
canon. Faster and uniform — once the loop has converged on CONTENT, run a single **whole-canon
scope-purge pass**: the full entry list (no corpus needed — this is a category judgment, not an
attestation one) + the complete exclusion policy → `{remove:[{id, en, category}]}`. It is cheap
(~100 KB prompt, no source text) and applies one rule everywhere at once.

**Never apply a purge pass without this check: compare what it wants to REMOVE against what it KEPT
of the SAME SHAPE.** A same-shape split is a misclassification, not a judgment call. Concretely: a
purge proposed deleting five `the rabbi of <town>` entries as `generic_unnamed` while keeping 67
entries of exactly that shape (including a nameless "the Rabbi of Tcherin") — in this genre the
toponym IS the personal identifier, so those removals would have silently dropped five recurring real
people. The removal list read perfectly reasonable on its own; only the comparison against the kept
set exposed it. Keep a per-round **veto** file so the rejection is auditable, and fold the
resolved rule back into the policy text (as an explicit KEEP clause) so the next round cannot
re-propose it.

## Convergence in practice (SSK, whole-corpus audit loop)

Once the loop switched to the WHOLE-CORPUS lens it converged in ~2 rounds: R2 = 10 dedup removes + 3
adds (2 real places + 1 scope leak "Mashiach" — caught by eyeball, scope guard then strengthened);
R3 = 2 dedup removes, **0 fabrications / 0 real drops / 0 scope leaks** → converged. Stop fired on
"no new REAL defects" (R3's 2 changes were duplicate removals, not defects), NOT on "empty patch" —
the audit will always find one more cosmetic dedup, so raw-empty is the wrong stop. Trajectory:
276 (high) → R0 fix → 292 → dedup split-bug → 289 → R1 recheck (proved per-chapter false positives)
→ R2 fix 289 → F1 whole-corpus restores (+16) → 291 → WC-audit R2 (−10 +3) → 284 → strip Mashiach
scope-leak → 283 → WC-audit R3 (−2 dedup) → 281 → strip mikveh scope-leak → **final 280 entities,
72 ambiguous**. Scope (divine/ritual-realia/abstract) stayed excluded per the user's standing decision.

## Pipeline placement, scaling, effort, driver (decided 2026-07-24)

- **Canon is built BEFORE translation, never concurrently.** Translation is per-segment/blind, so
  extracting names DURING it would (a) lose cross-book render CONSISTENCY (each segment invents its
  own English form for רבנו etc.), (b) inherit the per-chapter blind spot, (c) make global
  homonym-splitting impossible. Discovery is cheap; the cost is translation+review, which must run
  AFTER the canon anyway. Use translation only as a GAP-DETECTOR: while translating with the canon,
  flag names not in it + rendering-doubts → feed a canon/translation refinement round. (Check
  whether the plugin's W5 already emits unknown-name flags before building this.)
- **DECISION AXIS IS QUALITY, NOT COST** (user, 2026-07-24: "дело не в расходах, а в качестве").
  Never pick a lower-fidelity approach to save tokens/calls. Batch only if it does not hurt quality
  (else batch=1); pick the effort tier that is most ACCURATE (not cheapest); run MORE verification
  rounds / cross-model passes, not fewer.
- **Verification lens ranking: whole-corpus > per-chapter; grep-retrieval (RAG) is a big-book
  COMPROMISE, not an upgrade** — and since the axis is QUALITY not cost, grep-RAG is NOT chosen to
  save context: use whole-corpus where it fits, and **map-reduce (read every chapter in full)** at
  scale; grep-RAG only if even map-reduce is infeasible. Where the book fits in context, whole-corpus is strictly best.
  Per-entity grep-retrieval has real holes whole-corpus doesn't: (1) blind to OBLIQUE references
  (pronoun / role / epithet-not-in-surface-list / spelling variant) — the vindicating cross-chapter
  evidence is often phrased without the name, so grep misses it and the false positive survives;
  (2) homonym-contaminated bundles (one surface → many entities); (3) fragmented relational claims;
  (4) can't discover unknown names. For a large book prefer **map-reduce** (read each chapter ONCE →
  compact per-chapter entity-mention records → reconcile records cross-chapter → source-verify only
  contested) over naive grep-RAG — every chapter is actually read, so no oblique-reference blindness.
  Measure the chosen scaling architecture, don't assume. **MEASURED on the SSK slice:** grep-by-
  surface recovered **97%** of known (entity,chapter) presences — oblique blind-spot only ~3% (a
  LOWER bound; Hebrew Hasidic names are usually written explicitly). So for this genre hole (1) is
  smaller than feared and grep-RAG is a more viable big-book compromise than the critique implied —
  BUT hole (2) homonym-contamination is unmeasured and is likely the bigger risk (many entities
  share a surface, e.g. רבי נחמן → 3 people).
  - **"Where the book fits in context" is a MEASUREMENT, not an estimate.** `chars/4` is a
    Latin-prose ratio and badly UNDER-counts vocalized Hebrew — a 611K-char whole-corpus prompt
    estimated at "~153K tokens, fits" overflowed a 272K window — and `wc -c` counts BYTES, not
    characters, inflating the same prompt by ~40%. Size with
    `python3 -c 'import sys; print(len(open(sys.argv[1], encoding="utf-8").read()))' PATH` and
    calibrate against a prompt that actually RAN on
    that model. Full trap → `skill:codex-runtime-driving` → `references/prompt-sizing.md`.
- **Effort — do not restate a tier verdict here.** What this loop actually established is that the
  lever that mattered was whole-corpus context, NOT effort. No effort tier is validated as a winner:
  the bake-off that once reported one was measuring noise, and a controlled replication found the
  tiers indistinguishable. Single home for that evidence and for any tier decision →
  `skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`. Codex facts (verified in models_cache + codex-companion): `high/xhigh/max/ultra` are
  `supported_reasoning_levels` = thinking-DEPTH tiers of ONE model, NOT an agent swarm (that's Claude
  Code's "ultracode"/Workflow — a different axis). The `--effort` FLAG whitelist caps at `xhigh`;
  `max`/`ultra` are config-only (`model_reasoning_effort`) — don't change the user's global config
  without asking.
- **Loop driver = `/goal`.** `/goal` (CC ≥2.1.139) keeps working across turns until a separate fast
  model judges the condition met — the anti-lazy-stop primitive. Ship it as a plugin command (custom
  name, `disable-model-invocation: true`) that expands to a single `/goal` with ordered sub-goals
  (like `~/.claude/commands/goal-la.md`: GOAL 1…GOAL 2…GOAL 3 in ONE `/goal` — "multiple goals" =
  one goal, compound condition). The model CANNOT auto-invoke `/goal`; the skill's intake step ends
  by telling the USER to run it. Prefer a script-gated condition (Stop-hook `exit 2` = keep going, or
  `/goal "script exits 0"`) so the stop is code-decided, not model-judged. Stop-hooks can't be gated
  by the `if`/matcher field (tool-events only) → gate inside the command via a marker file.

## A MERGED canon row is immutable — every correction step must be PRE-merge

Before planning any canon review, correction, demotion or re-adjudication step, know that once
`--merge-batches` has run there is **no route back**. Three mechanisms look like they could fix a bad row and
none of them can:

- `canon_validate.py --verify-merged` is disk-independent, fresh-reads-only and **writes nothing**;
- re-merging a **different** resolution for the same `source_form` is a fatal cross-run collision, not an
  update (an *identical* resubmission is a silent no-op);
- `canon_adjudication_audit.py` never authors a verdict — its own IRON RULE is that it enumerates required
  sign-offs and cross-checks recorded ones. It can **block delivery**; it cannot repair a row.

And the shipped glossary Workflow runs `pipeline(BATCHES, batchStep)` then `--merge-batches` then
`--verify-merged` **in one call**, so there is no pause between a fragment passing `--check-batch` and the
canon being frozen. Correction therefore has exactly one shape: regenerate the batch's fragment and re-run
`--check-batch` **before** the merge.

**Why this earns a rule:** not knowing it put a citation-review step in three successive wrong places across
one plan's revisions — after the merge "demoting via `--verify-merged`" (writes nothing), then between
fragment-generation and merge (no such pause exists) — each costing a review round. It also decides a design
question outright: under `research_mode: live` the glossary pass invents `source` URIs that nothing reviews,
and **W5's reviewer cannot be the backstop** — its prompt explicitly puts "correctness of the frozen canon
decision itself" out of scope and routes suspicion back to the glossary/adjudication route. So a `live` run
needs a real pre-merge citation review, or it ships uninspected citations permanently.

## Scratchpad output is not durable — copy round artifacts out immediately

Every session gets a scratchpad under
`/private/tmp/claude-501/<project-slug>/<session-id>/scratchpad/`. It is session-scoped and does
**not** survive — anything written there and not copied out is gone when the session ends. This
loop produces exactly the kind of file that goes missing: a round's `canon.rN.json` or
`canon.final.json` is a **result**, not an intermediate, the moment it exists.

**Cost, 2026-07-27:** 472 files lost, including `canon.final.json` and `canon.r6.json` for the SSK
vol.2 name canon — outputs of long, expensive adjudication rounds that had to be reasoned around
afterwards rather than reproduced.

**Reflex: the moment a round's output becomes a result — a canon, a report, an evidence bundle, a
measurement table — copy it into the run directory (the durable_root's own tree) in the same turn
that produced it.** Do not defer the copy to an end-of-loop cleanup step; the session can end
without one. The system prompt's "prefer the scratchpad over /tmp" guidance is about keeping the
user's project clean, not about persistence — both are equally temporary.

**The second, quieter failure: a durable document that CITES a scratchpad artifact.** A plan or
report in the run folder saying "the verdict is in `codex-438-r2.out` beside this file" is broken the
moment that file is still only in the scratchpad — the sentence reads true, resolves in the
author's head, and only breaks when someone else (a reviewer, a re-review round) follows it. The
moment any durable file references a scratchpad path, the referenced file must move before the
reference ships. Cheap check: `ls` the run folder against the paths its own documents cite.

## Mechanics

Same detached-codex fan-out as the model bakeoff: `codex-companion.mjs task --background --model
<m> --effort high --cwd <per-agent-dir> --prompt-file <p>`, collect by parsed job-id across
`~/.claude*/plugins/data/codex-openai-codex/state/*/jobs/`, wait via Monitor (long background bash
polls get killed; detached codex jobs survive).

Related (narrative/evidentiary detail, not required to apply this method):
`skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`, and the `project-ssk-vol2-en-run`
memory topic.
