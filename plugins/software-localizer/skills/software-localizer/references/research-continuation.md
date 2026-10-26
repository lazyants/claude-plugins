# Canon research across ordinary user turns

Research serves a complete, traceable canon before the translation audit. A stored assessment
is not proof that its planned searches ran. Preserve three distinct outcomes: a completed
bounded search with evidence, a completed bounded search with no outside usage, and work whose
tool allowance prevented completion. Only the last calls for resource continuation.

## Choose a bounded turn

Before starting, agree the locale and original entry scope with the operator. Read existing
assessments and decisions, identify exact unanswered queries or checks, and state how the new
evidence could affect the rendering or its acceptance. Verify the actual packets and cited
inputs exist. Choose a small packet set and a stop condition in the working plan; do not repeat
a broad search or reopen a rejected route merely because another turn is available. A later
turn continues unperformed work without changing the evidence criteria.

The observed search allowance is at coordinator-turn level, shared by its nested native
agents. Its configured value can vary. More subagents or parallel locales do not multiply it;
a single print invocation is not promised to finish a large locale. Keep one accepting owner
per locale file. Research and independent verification for that locale must not accept
concurrently. Use normal settings and enabled tools. Do not change limits, switch profiles or
launch repeated print invocations to evade an exhausted tool's stop instruction.

## Finish and hand off

1. Stop using an exhausted tool in that turn. Save every exact model answer at a distinct path
   beside its original `packet.json` and `prompt.md`; do not edit quotes or upgrade confidence.
2. Accept valid answers sequentially per locale. Both output types include per-entry progress:

   ```json
   {"progress": {"status": "resource_limited", "remaining": ["Search the reference help centre for this product sense"]}}
   ```

   For verification, name exact unchecked source indexes and any unfinished counter-evidence
   checks. Return only checked indexes. A source actually fetched but inaccessible can be
   `confirmed: false` in a complete check; an unperformed fetch is still pending. A completed
   product-specific term uses `complete`, `remaining: []`, empty findings and low confidence.
3. Run `python3 S/report.py --root R --locale L`. Report completed research, completed
   verification, resource-limited entries, and any missing/invalid/stale replies from accept.
   Never present resource-limited canon work as complete or use it to claim the audit is ready.
4. Finish the coordinator's reply with a handoff: locale, original packet/answer paths and
   entry IDs, latest checkpoint packet paths, completed siblings, exact remaining work and the
   next bounded packet set. The typed record lives in `R/canon.research/<L>.json`.
5. Wait for the operator's ordinary follow-up user turn authorizing that continuation. Do not
   arrange an automatic new research run inside the exhausted turn. A STOP or rejection is
   not permission for a successor study.

## Continue only unfinished work

After that next-turn authorization, read the handoff and stored state. Retain the original
packets and answers. Build continuation packets for only the unfinished IDs from the agreed
scope (repeat `--entry` for each):

```bash
python3 S/packets.py build --root R --kind research --locale L --entry ID
python3 S/packets.py build --root R --kind research_verify --locale L --entry ID
```

Use the appropriate phase for each ID; do not run verification while its research is limited.
Build creates unused directories and returns their exact paths. A limited entry's item carries
`resume.previous` with retained findings/checks and `progress.remaining`, plus the preceding
packet path. Give the new prompt to the researcher, or to an independent verifier who did not
research that entry. Continue only that remaining work. Save a new answer and accept against
the **new** packet, never substitute an old packet or replay a full original batch.

Acceptance preserves earlier research findings, raw answers and completed verification indexes.
Completed siblings are untouched. A stale checkpoint is refused (`stale` for research,
`missing` for verification); inspect current state and build again instead of forcing it.
Research continuation clears prior verification because the evidence hash changed. Verification
continuation keeps that hash and can return only newly checked indexes; all source indexes must
be accounted for before `complete` is accepted. Remaining counter-evidence work still requires
`resource_limited` even when every source index has been checked.

Default selection also includes typed limited records, but can include never-started work outside
a narrowed scope. Explicit IDs keep the handoff bounded. Naming an already completed entry with
`--entry` is an intentional fresh assessment, so do not feed all low-confidence IDs to it.

## Saved answers from older versions

Absent `progress` means the old completed interpretation, not proof of search coverage. The
plugin cannot infer tool exhaustion reliably from prose. If an older run recorded the limit
only in notes, review its exact saved answers and handoff, identify the affected IDs and
unperformed work, and obtain authorization to finish that scope. Build fresh explicit packets
for just those IDs. They are fresh assessments rather than typed continuations; provide the
original evidence to the researcher and retain it in the new answer. Acceptance archives the
previous record and answers, but cannot reconstruct searches that were never recorded.

At closeout distinguish progress toward the chosen canon, knowledge gained from completed
checks, and remaining unknowns. A checkpoint or a green fixture test alone proves neither real
research completion nor improved translation quality.
