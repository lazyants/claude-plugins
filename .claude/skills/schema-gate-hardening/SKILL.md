---
name: schema-gate-hardening
description: "Author, harden, or adversarially review a validation/ship gate, JSON-Schema extension, stdlib-JSON or dep-free-YAML reader, structural-equivalence/staleness check, untrusted-identifier-to-path/shell allowlist, LLM-output-vs-registry match, or an agentic-pipeline tamper-detection/sentinel-isolation primitive — covering false-GREEN (under-catch) and false-RED (over-catch) vectors, the allowlist-not-denylist and strictness-bias spine, and where a root-of-trust must live."
---

# Hardening validation gates, schemas, and pipeline-trust primitives

A gate/schema/integrity check certifies only **what it can see**. Every recurring failure below is a way it silently passes a defect (false-GREEN / under-catch) or permanently blocks legitimate input (false-RED / over-catch). Front-load the relevant checklist; do not discover these one adversarial round at a time.

## Cross-cutting spine (applies to every gate here)

1. **Positive allowlist, fail closed — never a denylist.** Enumerating what to reject gets broken by a new shape every review round; a denylist that blocks `/ \ ..` still passes shell metacharacters. State the finite set of *accepted* shapes; everything else halts.
2. **Strictness bias — the only tolerable failure is a false-HALT, never a false-PASS.** For any ambiguous design choice pick the branch whose worst outcome is "safely refused" (remedy is idempotent) over "silently accepted a bad value." *When unsure, HALT.*
3. **Red-before-green is necessary but NOT sufficient — prove BOTH directions.** Watch the gate FAIL on the real defect, AND separately construct realistic *legitimate* content and prove it does NOT trip. A gate that over-catches hand-authored/LLM content is often worse than the under-catch it replaced.
4. **Front-load the whole checklist.** A single adversarial reviewer surfaces these one class per round, in the same order, even on a same-plugin precedent file. Paste the checklist into the plan up front instead of looping N times.
5. **Differential-test against a REAL oracle, then audit the harness.** Compare your reader against `ruby -ryaml` / `jsonschema` / `Psych` over hand-fixtures AND an adversarial (not ASCII-only) fuzz corpus. A green run over a blind corpus proves only the classes you fuzzed — run ≥1 adversarial reviewer as the backstop for the class you didn't think to fuzz. Also audit that the harness implements the spec (using `.trim()`/`\s` where the spec says ASCII-only *proves the wrong program*).
6. **Convergence is a SIMPLIFICATION, not another layer.** When a reviewer keeps returning same-CLASS collisions/false-passes, DELETE the clever machinery generating them (the sorter, the sentinel, the field projector) rather than adding context-awareness to it. A fix round that deletes code is converging; one that adds a guard is usually whack-a-mole.
7. **Schema is advisory unless `jsonschema.validate` actually runs at runtime — grep for it first.** If nothing validates the doc at runtime, the schema `pattern`/`enum` is documentation; the per-script runtime check is the load-bearing thing.
8. **A deferred/found instance signals a CLASS.** State the invariant that bounds the whole class and open ONE tracking issue; do not patch instance-by-instance. Resolving error A by moving the same input's failure to error B is zero progress — enumerate the full failure set for the topology the fix targets.

## References — read the one that matches the task

- **`references/json-schema-and-json.md`** — read when AUTHORING/EXTENDING a schema or writing a stdlib gate that reads JSON or YAML: draft-2020-12 `if/then`/`allOf`/`oneOf`/`additionalProperties` traps, the `format:"uri"`→`rfc3987` stdlib override, the ~7 ways `json.loads` out-permits strict JSON + Python type traps + schema-vs-runtime resolution, the `const`-falsifies-a-hardcode-bug check, and dep-free single-field YAML extraction.
- **`references/gate-review-lenses.md`** — read when REVIEWING a gate for soundness or choosing its shape: over-catch (over-broad free-text regex/scope), under-catch (false-GREEN vectors), structural-equivalence → dumb canonical-JSON equality, LLM-output-vs-registry typographic false-REDs, untrusted-identifier→path/shell allowlists, and the nested-discovery fixed-point / mode-voided-exemption / reason-code-relabel lenses.
- **`references/pipeline-trust.md`** — read when adding a tamper-detection / integrity check or a marker/sentinel to isolate text in an agentic pipeline: where the root-of-trust must live (and detect-then-HALT vs detect-then-proceed), and why a content-matching sentinel is never a sound isolation primitive (track the real position instead).
