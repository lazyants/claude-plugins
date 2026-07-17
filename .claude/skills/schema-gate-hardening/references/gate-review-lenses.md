# Adversarial review lenses for a gate

Lenses for reviewing a gate's soundness and choosing its shape. See the spine in `SKILL.md` — allowlist-not-denylist, strictness bias, red-before-green-both-directions, convergence-is-simplification — which these instantiate.

## Contents
1. Over-catch — an over-broad free-text gate (pattern AND scope)
2. Under-catch — the three false-GREEN vectors + the re-run-your-own-output meta rule
3. Structural-equivalence / staleness → dumb canonical-JSON equality
4. LLM-output vs an exact-string registry — typographic false-REDs
5. Untrusted identifier → path / shell — allowlist, `fullmatch`, calibrate
6. Nested-discovery fixed-point, mode-voided exemption, reason-code relabel

---

## 1. Over-catch — an over-broad free-text gate (pattern AND scope)

When hardening a **hard-blocking** gate over free-form/hand-authored text (a "fill in this placeholder" or "don't leave this trap example" check), red-before-green is necessary but NOT sufficient — separately prove it does NOT catch realistic LEGITIMATE content, or the gate itself becomes the worse failure mode (an under-inclusive gate just misses some real cases; an over-broad hard gate is impossible to ever pass).

- **Pattern axis.** A generic shape regex `\[[A-Z][^\]]{2,}\]` (any bracketed uppercase text) passed two `codex` plan rounds because both only tested the PRISTINE template — but a translator legitimately writing `[NOTE]`, `[CHAPTER I]`, `[TRANSLATOR'S NOTE]`, `[SIC]` trips it FOREVER. **Fix:** replace the generic shape regex with a **closed, finite list of the exact known-shipped strings** (`"[SOURCE LANGUAGE]"`, `"[TARGET LANGUAGE]"`, `"[PROJECT TITLE / AUTHOR / PERIOD -- fill in]"`), matched via **whitespace-normalized substring containment**. A closed list can't over-match content that merely resembles the shape.
- **Scope axis (a THIRD axis, independent of pattern specificity).** A co-occurrence check ("does the label AND its content word both appear") written FILE-WIDE wrongly, permanently rejects a correct file when the content word (e.g. an ordinary French word like "guéridon") legitimately appears far from the callout. **Fix:** locate the labeled span first (`<!--\s*ERA/DOMAIN TRAP EXAMPLE.*?-->`, case-insensitive, DOTALL), then check the content word only inside that matched span. Ask: "if this content legitimately appears elsewhere in a real file, unrelated to the thing I'm checking, does my scope wrongly pull it in?"
- **When reviewing (or asking codex to review) such a gate, prompt for BOTH directions explicitly** — "does it catch every known-bad case" AND "construct realistic legitimate content a real user might write and confirm it does NOT trip." A codex loop returning clean twice is NOT proof if neither round's prompt asked the specificity question. Corpus-validation only proves no FP/FN against files that EXIST today, not against every plausible future rephrasing.
- **Related proximity-guard gap:** a word-distance `{0,4}` "read…directly" window let a long inline-code path between verb and adverb consume the slots and slip past — fix by collapsing backtick-code-spans to a single token before matching.

## 2. Under-catch — false-GREEN vectors

A GREEN certifies only what the gate can see; a false-GREEN silently passes your OWN output's defect. Three concrete under-catch mechanisms (from a format+completeness gate):

1. **A defect-count regex using `re.match()` where it means "anywhere".** `re.match()` tests only index 0 — it IGNORES a `^` and everything the pattern would match mid-line. `YDUP.match(line)` counted only col-0 `[Yiddish]`; blockquote-/bold-prefixed `> [Yiddish]` / `**[Yiddish]**` (the majority) were invisible. **Fix: `.search()`.** A comment claiming "anywhere" over a `.match()` call is the tell.
2. **Source-marker extraction too strict for a garbled OCR/text-layer.** A completeness gate (`∪ source markers ⊆ ∪ rendered headings`) is only as good as its extractor: an anchored strict regex silently MISSED split/nikud-carrying markers, and **a marker it can't extract can't be reported as dropped ⇒ false-GREEN.** Fix: whitespace-tolerant extraction + canonical reconstruction, and deliberately **OVER-extract** — over-capture only ever yields a loud false-RED (safe, you investigate); under-capture yields a silent false-GREEN.
3. **Per-unit review is structurally blind to cross-unit drift.** N independently-generated units each pass their own per-unit review "clean" yet drift in cross-unit PRESENTATION (one segment prefixes source lines with a `[Yiddish]` label; the other 39 use a bare `>`). The per-unit review CAN'T catch this — it never sees the others. Only a corpus-wide normalization gate over the assembled whole does.

**The meta rule (the actual save):** when an adversarial reviewer finds a false-GREEN VECTOR in YOUR OWN gate, **do not just fix the gate and move on — re-run the fixed gate on your OWN already-"GREEN" output, because that earlier GREEN was a lie.** (Fixing `.match`→`.search` immediately turned converged output from GREEN to RED and surfaced 11 real labels that would otherwise have shipped.) Cousin trap: `is_file()` follows symlinks — use `is_symlink()` in a promote guard.

## 3. Structural-equivalence / staleness → dumb canonical-JSON equality

For a gate deciding "is durable artifact A still structurally equivalent / current to shipped canonical B?" (e.g. a stdlib preflight that must catch a stale pre-migration schema copy before it HANGS the pipeline), the convergent design is **DUMB whole-artifact canonical-JSON EQUALITY + a strictness bias.** Every "clever" leniency is a false-PASS vector (the dangerous direction). The false-PASS tail (each a real collision):
1. **Hand-enumerated projection** (capture just the "relevant" fields) → leaks ANY un-enumerated construct (a sibling keyword, a top-level `not`, a duplicated `oneOf` branch). You CANNOT enumerate "the relevant parts" of an acceptance artifact — any keyword anywhere can change whether an instance validates.
2. **Reconstructing a sub-object** (`{"if": …, "then": …}`) → silently drops sibling keys. Capturing "the whole thing" means the WHOLE node, not a rebuilt copy of the parts you thought of.
3. **Sorting arrays for order-insensitivity** → a context-blind sort corrupts arrays that are INSTANCE DATA (a `const`/`default`/`enum` member can contain `{"required":["a","b"]}` whose order IS significant); two different instances collapse to equal. A context-aware sorter is a rabbit hole.
4. **A magic comparison SENTINEL** — `dict.get(k, "<absent>")`, or truncating an over-deep subtree to a marker — eventually COLLIDES with real data equal to the sentinel. (Twin of the content-matching-sentinel trap in `references/pipeline-trust.md`.)

**The convergent design:**
- Compare the WHOLE artifact as canonical JSON: `json.dumps(x, sort_keys=True)` — object-key-order-insensitive (the one reorder a healthy copy introduces) but **ARRAY-order-EXACT and scalar-TYPE-exact** (`true != 1`, `["a","b"] != ["b","a"]`). Compare two of these strings.
- **Normalize NOTHING else.** Don't sort arrays, don't strip annotations (`title`/`description`), don't reduce to fields — each is a false-PASS vector, and **none is needed: a HEALTHY artifact is a byte-copy of the canonical.** The only cost of order-exactness is a false-HALT on a hand-reordered array, which never happens from copying and halts SAFELY.
- Missing key → explicit presence check (`k in a` / `k in b`), never a sentinel default.
- Resource limit (depth) → **RAISE and halt**, never truncate-to-a-marker (raising is collision-proof AND caps recursion before the downstream `json.dumps` can `RecursionError`; guard depth on BOTH sides).
- **STRICTNESS BIAS is the tie-breaker for every ambiguous choice:** pick the design whose only failure is a false-HALT. This single rule chooses order-exactness over sorting, presence-checks over sentinels, raise-on-limit over truncation. It took six rounds because each round's fix-SPEC ("reconstruct {if,then}", "sort the set-semantic arrays") introduced the next false-PASS — a fix spec needs the same adversarial scrutiny as code. **The convergence signal was a SIMPLIFICATION** (the winning pivot DELETED the sorter/sentinels/projectors). When a reviewer keeps returning same-CLASS false-PASSes, REMOVE the mechanism, don't add context-awareness.

## 4. LLM-output vs an exact-string registry — typographic false-REDs

A fail-closed gate that pins an LLM-generated identifier against a curated canonical list with an EXACT membership test (`entity in set(canonicals)`) fail-closes on legitimate output, because **LLMs typographically "autocorrect."** The canonical stores a straight apostrophe `'` (U+0027); the model emits the curly `’` (U+2019) — often the curly form is even listed as an alias, which is why the model picks it. Same class: `‘ ’` quotes, `ʼ` (U+02BC), `´`/`` ` ``, and the dash family `‐ ‑ – —` vs hyphen-minus. Because it's fail-closed and aggregates all rows, one such row blocks the whole build.

**Size it before fixing:** count exact vs fold-only vs genuine-unknown (a full scan proved 4 of 1467 rows were pure typography, 0 genuine registry gaps — so it's not a recall/hallucination problem).

**Fix:** normalize a small, closed set of typographic variants to their canonical codepoint (`_norm_apostrophe`: `’‘ʼ´\`` → `'`; add dashes if the domain needs it) on BOTH sides, then:
- **exact match wins first** (preserve the verbatim string when it already matches);
- else a **fold-match rewrites the stored value to the EXACT canonical**, so everything downstream (index entries, wikilink targets) matches the registry — canonicalize, don't just accept the variant;
- else **still raise** (stay fail-closed on a genuinely unknown entity — normalization must not become a fuzzy matcher).

Guard the fold: build the normalized→canonical map once and **RAISE if two DISTINCT canonicals fold to the same key** (registry ambiguity), never silently pick one. This is a false-RED (over-catch) fix — opposite axis from §2, same gate-soundness discipline.

## 5. Untrusted identifier → path / shell — allowlist, `fullmatch`, calibrate

Hardening an attacker-influenced identifier that flows into filesystem paths AND/OR shell commands (e.g. a manifest `seg` id spliced into `segments/{seg}.draft.json` and workflow shell commands — path traversal / absolute-path escape / shell injection):

1. **Positive allowlist, NOT a denylist.** `^(?:PREFIX:)?[A-Za-z0-9_]+$` (anchored). A denylist rejecting `/ \ ..` + absolute paths STILL passes shell metacharacters (an existing denylist false-accepted `seg;rm -rf x`). One allowlist closes BOTH path-traversal AND shell-injection at once.
2. **`re.fullmatch`, NEVER `re.match(r"...$", s)`.** Python's `$` also matches just before a trailing newline, so `re.match(r"^[A-Za-z0-9_]+$", "seg01\n")` WRONGLY passes — a traversal/injection foothold via one trailing `\n`. `re.fullmatch` (or `\Z`) has no such hole. **JS differs:** `$` WITHOUT `/m` matches only end-of-input (NOT before a trailing `\n`), so `/^...$/.test(s)` is safe in JS — but do NOT add `/m`.
3. **Calibrate the allowlist against the FULL real vocabulary BEFORE choosing it.** Mine every fixture/example value first (plain `seg01`, suffixed `seg05_blocked_regen`, the special `FRONTBACK:fm01` prefix form) so the pattern accepts 100% of legitimate ids. When reviewers flag over-permissiveness, TIGHTEN, don't widen: no hyphen (zero real ids used one; excluding it also kills leading-`-` CLI-arg injection) — cheaper to rename 2 fixtures than loosen the pattern.
4. **When the schema is NOT enforced at runtime, the RUNTIME code check is load-bearing.** If nothing runs `jsonschema.validate` against the doc, the schema `pattern` is advisory; the per-script `validate_seg()` calls are the fix. Grep first for whether ANY code path actually validates.
5. **Guard the SOURCE and every sink**, BEFORE the path/command is built. A `--all`/bulk mode that sources ids from the doc itself needs the check inside its loop, local — not merely inherited from a downstream validator that happens to re-see the id. (Convergence of independent adversaries — codex + `/security-review` — on one indirect-guard spot is itself signal; harden it.)

Bypass probes that MUST fail: `../x`, `/abs`, `x/../y`, `x;rm`, `x|y`, `` x`id` ``, `x$y`, `x y`, `x\n`, `PREFIX:` alone, NUL, unicode digits (the ASCII class excludes them).

## 6. Nested-discovery fixed-point, mode-voided exemption, reason-code relabel

Patterns from generalizing a nested-discovery scan (footnotes/verses nested inside each other) to arbitrary depth — each caught by the adversarial review loop, not the first draft:

- **Fixed-point / worklist SEEDING trap.** When growing a frontier until nothing new is found, seed it with **the round-0 discoveries the INITIAL pass already made**, not only items later rounds surface: `frontier = seg_blocks ∪ {def_block(n) for every n discovered by the initial scan}`, then grow. A worklist that can't process the input that motivated it (the issue's own primary topology) fails its own first test case.
- **Mode-voided-content deadlock → exempt via MODE-INDEPENDENT ground truth.** A config mode (`verse_policy.mode: skip`) voided the runtime channel a footnote is normally discovered through, while a separate gate still unconditionally REQUIRED that footnote — an unsatisfiable deadlock. Drive the exemption off **mode-independent manifest/source-side ground truth** (`verse.store.fnrefs` or a `⟦FNREF_n⟧` scan of the store's `plain_text`), NOT the voided runtime content. When a mode empties the channel a check depends on, the check's escape hatch must read a channel the mode does NOT touch.
- **Whack-a-mole reason-code RELABEL is not a fix (review lens).** The first skip-exemption fixed `orphan_footnote_def` but the SAME topology then died with `orphan_verse`. Resolving error A by moving the same input's failure to error B is zero progress. Before declaring a fix done, **enumerate the full failure set for the topology the fix targets**, not just the one error code the ticket named.
- **Referenced-only-not-rendered + SPLIT return channel.** An item can be marked "referenced" (satisfying orphan/bijection checks) without appearing in output — correct when its sole reference site is itself stripped/invisible. Load-bearing: keep it OUT of any channel that would surface it (a nested-discovered footnote number reaches `seg_referenced_ns` but NEVER a node's `fnrefs`, which would re-emit a dangling `[^n]:`). Give the scan helper a SEPARATE return slot so referenced-only discoveries physically cannot ride the renderable path.
- **A flat loop CAN be the fixed point — no second worklist needed** when the exemption predicate reads only immutable state (order-independent, derived from immutable manifest ground truth): the existing full-set iteration is already the fixed point. Adding a worklist there is over-engineering. (The DISCOVERY side genuinely needs the worklist because it grows the frontier; the distinction is whether the set being iterated is fixed or growing.)
