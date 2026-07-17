# Trust & isolation primitives in an agentic pipeline

Two traps when a gate/check has to hold up against a semi-trusted (prompt-injectable) agent that shares the pipeline's filesystem, or when a marker must isolate one span of text from a later matching pass.

## 1. Where the root-of-trust lives (co-located = defeated)

A tamper-detection / integrity check is only as sound as **WHERE its reference value (the known-good hash) lives.** An integrity check compares the artifact against a KNOWN-GOOD reference; it is sound only if that reference lives in a channel the checked-against adversary **cannot write.** Co-locating the stamp with the adversary-writable artifact makes the check circular.

**Concrete defeat:** a setup step stamps `canon_sha256`/`manifest_sha256` into an aggregate `assignments.json`; a verifier re-hashes on-disk `canon.json`/`manifest.json` and fails on mismatch — sold as "DETECTED and HALTS." But `assignments.json` sits in the same run-dir the semi-trusted agent writes to, so the agent that mutates `canon.json` can in the same turn **(A)** delete the (schema-optional) stamp → the "absent stamp → skip" path silently passes, or **(B)** rewrite the stamp to `sha256(tampered canon)` → it matches. Net assurance against a determined injected agent: ~zero.

**Two sub-defeats, one fundamental:**
- **(A) schema-OPTIONAL stamp → omission silently skips** = a fail-open schema hole. Make the field **REQUIRED** so a missing stamp is a hard failure, not a skip.
- **(B) rewrite-to-match is FUNDAMENTAL:** under a "the agent has pipeline-wide FS-write" premise there is NO on-disk location the agent can't reach, so no on-disk stamp can ever be sound.

**The sound anchor:** in this threat model, CLI flags / env / values the orchestrator holds in memory are TRUSTED; any on-disk file within the agent's reach is NOT. Pass the setup-time hash to the verifier as a **trusted CLI arg** (`--expect-canon-sha256`), captured by the orchestrator BEFORE the untrusted agent step runs — never re-read from an agent-writable file.

**When you genuinely can't anchor it trusted yet, be HONEST — don't advertise soundness you lack.** Downgrade the docstring/schema/doc claim from a guarantee ("DETECTED and HALTS") to what it is — a **best-effort tripwire** that catches ACCIDENTAL / non-adversarial mutation (a crash mid-write, a stray process, a buggy well-behaved agent), explicitly NOT sound against a determined injected agent — and defer the sound fix. (Keep the runtime "…tamper detected, HALTING" message — it's a factual statement when a mismatch fires, not a soundness claim.)

**A tripwire that detects-then-PROCEEDS is worthless.** An advisory check ("log the failure, proceed regardless") lets even the ACCIDENTAL mutation it DID catch reach the downstream step and get baked into accepted state. A frozen-input mismatch must be a distinct, NON-advisory signal that HALTS the main pipeline before the mutated inputs are consumed — surface it as its own result field (`frozen_input_mismatch` / a distinct `reason`), don't bury it in a generic failure bucket, and gate the halt on it.

**How to apply — ask two questions up front for any tamper/integrity check:**
1. *Where does the KNOWN-GOOD value live, and can the untrusted step write there?* If yes, the check is not sound — anchor the reference in a trusted channel or downgrade the claim.
2. *What happens on a detected mismatch — does the pipeline actually STOP, or log-and-continue?* Log-and-continue = the check does nothing.

## 2. A content-matching sentinel is not a sound isolation primitive

A fixed textual sentinel embedded in shared text, later found-and-restored via string matching, can ALWAYS coincidentally collide with some real data channel you haven't enumerated. Point-patching each discovered collision leaves the NEXT channel unprotected, because the approach can only ever reason about channels someone already thought to check.

**Concrete:** a renderer embedded a fixed `⟦LIT_LABEL⟧` sentinel to keep a "(lit.: " label from being mis-matched by a later entity-linking regex, then restored it via string-content matching. Three independent review rounds each found a DIFFERENT real collision: a schema-unconstrained placeholder value could literally equal the sentinel; an author-controlled canon field could contain it as a substring; a block's own prose could coincidentally contain the same bracket sequence.

**The tell** that a sentinel-based fix is the wrong shape: the justification for "safe" is some flavor of *"this string is unusual enough"* or *"no such case exists in the codebase today."* That argument has been wrong repeatedly across different data channels in the same file.

**The fix is a redesign, not a 4th patch — track the real POSITION, never a marker.** Have the text-generating function return `(text, span)` — the exact `(start, end)` offsets of the sensitive substring WITHIN ITS OWN OUTPUT, computed directly from string lengths as it builds that output (zero string search). Track that span's ABSOLUTE position through later composition via a running cursor, then hand the position list to the **SAME protected-span/exclusion mechanism the matching pass already trusts.** There is nothing to "restore" — the substring was always in its final form; protecting it from being matched is all that's needed. This is airtight against every collision channel simultaneously, including ones nobody has found, because it never asks "does anything else happen to look like this string" (unanswerable in general — data-driven inputs are free-form almost everywhere).

**Two follow-on notes:**
- Merging position-tracked spans into an EXISTING span-exclusion list can break that list's reconstruction if it assumes spans are disjoint (a position nested inside a pre-existing protected span broke a sorted-and-disjoint loop). A generic **interval-merge (coalesce overlapping/touching spans into their union)** before any such reconstruction closes this for any future span source.
- A residual, genuinely-unreachable edge (e.g. a zero-length span) that's provably impossible from BOTH real callers — because one input is a **hardcoded source-code literal, not data-driven** — is fine to ratify as a documented comment-only limitation. The distinction that matters is DATA-DRIVEN (free-form, could be anything) vs SOURCE-CODE-CONSTANT (length fixed at the code level, no external input changes it).

**Variant — a control-flow sentinel WORD inside a schema-less agent reply.** A Workflow template gated its resume/wait step on a schema-LESS natural-language `agent()` reply via `precheck.indexOf("PRESENT") !== -1` / `ready.indexOf("READY") === -1`. A plausible FAILURE reply — `ABSENT 0 (fragment missing; not PRESENT)` or `TIMEOUT 0 (not READY)` — contains the sentinel word as a SUBSTRING, so the check falsely fires (a missing fragment marked ready, its repair skipped). No position to track here (it's a control DECISION, not spliced text), so the fix is the principle's other half: **EXACT-match the trimmed reply against a discriminator-carrying sentinel** (`String(precheck).trim() === "PRESENT " + batch.index`) — never substring-test a signal a free-text responder controls. When you fix one, grep the sibling templates — this exact substring pattern was cloned across three.
