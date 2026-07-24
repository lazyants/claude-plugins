# Adversarial review lenses for a gate

Lenses for reviewing a gate's soundness and choosing its shape. See the spine in `SKILL.md` — allowlist-not-denylist, strictness bias, red-before-green-both-directions, convergence-is-simplification — which these instantiate.

## Contents
1. Over-catch — an over-broad free-text gate (pattern AND scope)
2. Under-catch — the three false-GREEN vectors + the re-run-your-own-output meta rule
3. Structural-equivalence / staleness → dumb canonical-JSON equality
4. LLM-output vs an exact-string registry — typographic false-REDs
5. Untrusted identifier → path / shell — allowlist, `fullmatch`, calibrate
6. Nested-discovery fixed-point, mode-voided exemption, reason-code relabel
7. What does this assertion actually PROVE? — the strength ladder, satisfiability, witness completeness
8. Cross-gate exception drift — a decision recorded only in prose re-surfaces as a false regression
9. Destructive filesystem gate — preserved-dotfile symlink-survival vectors in a clean-then-rebuild step

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

Two further under-catch shapes, both structural rather than a coding slip — no amount of testing the gate reveals them, because the gate is doing exactly what it was written to do:

4. **The self-referential gate — `expected` derived through the code under test (spine §8).** A coverage gate computed its expected occurrence set by calling `occurrence_targets.build(...)`, the very function whose matching it was meant to certify (`validate_backlinks.py:623-625`). Any name the matcher fails to find is absent from BOTH sides, so `expected == actual`, `warnings: 0`, exit 0 — on every input, forever. The gate cannot fail on the defect class it exists to catch. This survived a full plan→codex→review→ship cycle because every reviewer checked whether the gate was *correct*, never where its expectation *came from*. **Ask the provenance question explicitly, and never cite such a gate as evidence its own subject works** — the check has to be an independent oracle (a committed expected fixture, a second implementation, or human eyes).
5. **An empty-result sentinel read as an affirmative (spine §9).** `glossary_batch_plan.py` prints `{"no_new_candidates": true}` for *both* "every candidate is already in canon" and "my extractor matched nothing at all"; the consumer (`SKILL.md`'s SKIP branch) assumes the benign reading and skips the step. On an uncased script where the candidate matcher was structurally dead, that skipped the sole writer of `canon.json` and the run FATALed two steps later with an error naming neither cause. **Zero is not a result — it is the absence of one.** Either emit distinguishable states at the source (`nothing_to_do` vs `detector_found_nothing`), or make the consumer prove non-vacuity (assert the detector actually ran and saw candidate-shaped input) before treating empty as success.

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

---

## 7. What does this assertion actually PROVE?

For a gate asserting over **prose/doc contracts** (fixed-string, line-based helpers: `grep -F`,
section-scoped scans, occurrence counts, line-order comparisons). Verified over an 11-round
adversarial plan review, 2026-07-22 — every round found the *next* rung, always in the assertion
added to close the previous round.

### The strength ladder

Each rung is a distinct question. **Passing rung N says nothing about rung N+1**, and the natural
reading of a green assertion silently claims all of them:

| rung | question | what defeats it |
|---|---|---|
| 0 | does the needle exist *anywhere*? | a whole-file `has` is satisfied by an unrelated copy elsewhere |
| 1 | is it in the right **section**? | scoped scans can't tell "needle absent" from "**heading** absent"; can't see inside fences; can't prove a heading's **parent** (a same-named decoy under another parent binds) |
| 2 | is it **new** — a witness of the change, not a pre-existing attractor? | a needle already present passes before the edit. Fix: assert `count == 0` before, `== 1` after |
| 3 | is it in the right **place** *within* the section? | novelty proves nothing about position. Fix: line-order comparison against anchors |
| 4 | is its **anchor** unrenameable? | a bare label (`**Flat entries**`) can be renamed and a decoy reference planted. Fix: anchor on text carrying the section's *defining condition*, which can't be repurposed without destroying its meaning |

Practical rules: an occurrence-count comparison needs `count == 1` on **every anchor** first, or
`line_of` takes its first match from a duplicate. A count is line-based (`grep -cF`), not
occurrence-based — two hits on one line read as one.

### Satisfiability — check assertion PAIRS, not assertions

**A set of individually-reasonable assertions can be jointly impossible.** A spec requiring a
sentence *inside* a section while a negative pin forbids that sentence's identifier *in* that
section cannot be satisfied — and reviewing each assertion alone never finds it, because each is
fine in isolation. Before shipping an assertion set, ask of every positive/negative pair: *can both
pass on the same artifact?* Resolve by relocating the positive to a section where the term
legitimately lives; a **bare-identifier** negative then also becomes strictly stronger than a
specific bad-phrasing needle, because it forbids every future wording rather than one.

### Witness completeness — witness the OUTCOME, not the setup

If a requirement has **N** normative outcomes, **N** witnesses are needed. One collapsed assertion
("pins the membership branch") lets the implementer write the setup plus one outcome and omit the
rest with everything green. The failure mode is choosing needles from *which assertions already
exist* rather than from *what the requirement actually is* — a proxy standing in for the goal. Same
trap for a structural requirement ("this must MOVE"): needles taken from the moved block's
introductory paragraph pass while the normative bullets stay behind.

### The termination rule

This ladder does not terminate in a fixed-string world — rung 5 is always available to an author who
edits the anchors. **Bound the threat model in the artifact itself**: these assertions defend against
*incomplete/careless implementation and drift*, not a hostile author; note the other layers (human
review, a review loop); and file the real structural guarantee (a **structure-aware parser** that
resolves each element to its owning branch) as a follow-up. Then fence later rounds from re-opening
it. → the contrivance-gradient stop signal in skill:review-loop-discipline.

---

## 8. Cross-gate exception drift — a decision recorded only in prose re-surfaces as a false regression

When a project accumulates MULTIPLE validation gates over time — especially a later one, added in a separate session/phase, that re-derives or regression-checks something an earlier gate already triaged — an "accepted / deferred" exception is honored by a consuming gate ONLY if it lives in the machine-readable channel THAT gate reads. A decision recorded only in a human-readable prose report is invisible to any new automated pass, however conceptually adjacent, that was never taught to read that doc.

**Concrete:** an earlier pass ran to convergence and documented 4 items as permanently-accepted `needs_human` limitations in a prose report, but deliberately never added them to the sidecar triage JSON the pass itself reads (`checkd_pass1_triage.json`) — because "dismiss" was the only `decision` value the file had ever carried, and these were accepted-as-open, not dismissed. Months later a separate newer gate reading that SAME sidecar for its own regression check reported all 4 as blocking, indistinguishable at first glance from the genuinely-new false positives an unrelated text edit had also introduced. Only matching each item word-for-word against the old prose report revealed they were the already-litigated items, never wired into the file the new gate consults.

**This is a PROCESS trap, not a checker bug.** The sidecar's schema technically supported the exception (any entry present marks a finding "triaged," regardless of its `decision` value) — the gap was that an "accepted, deferred" decision got recorded in prose instead of in the file every consuming check reads. It is the non-adversarial twin of the root-of-trust lens in `references/pipeline-trust.md`: the reference value lives in a channel the consumer never reads — but here nobody rewrote anything, the acceptance was simply never propagated.

**Two rules.** (1) **Wire an exception into EVERY gate that could re-surface it, and make "is it propagated to all of them" its own explicit check** — never assume a decision documented once is discoverable everywhere; a later or parallel gate reading a different sidecar re-derives from scratch. The propagated record must carry an HONEST decision value (`needs_human` / accepted-open, NOT a silent "resolved"). (2) **Before treating a "new" blocking finding as a regression, check whether it is word-for-word identical to something already accepted in an adjacent report/doc** — if so, the fix is wiring it into the CURRENT gate's own mechanism with that honest value, NOT re-litigating a question already answered.

---

## 9. Destructive filesystem gate — preserved-dotfile symlink-survival vectors in a clean-then-rebuild step

A "clean the managed output dir before a deterministic rebuild" step (so stale files can't survive and defeat a render/diff acceptance gate) is a **data-loss / out-of-root-write attack surface**, and the recurring vector is **symlinks**: `Path.is_file()` / `is_dir()` / `mkdir(exist_ok=True)` / `Path.resolve()` all FOLLOW symlinks, so a planted symlink turns the clean into an `rm`-into-someone-else's-tree and a write into an out-of-root clobber. Making one such feature safe took FOUR+ adversarial rounds — each closed one vector and exposed the next — and **the green test suite hid every one**; disk/behaviour verification, not the pass count, is the signal. This is the allowlist-not-denylist + strictness-bias + root-of-trust-boundary spine applied to the filesystem.

**The core insight:** the files you deliberately PRESERVE across the clean (dotfiles — an ownership marker, its write-temp, snapshot dirs) are exactly the ones an attacker/user can PLANT as symlinks that SURVIVE the clean. Enumerate every preserved dotfile as a symlink vector. The vectors, in the order they surface:
1. **out_dir itself a symlink** → the clean follows it into the target. Refuse if `out_dir.is_symlink()`, and check it BEFORE `mkdir(exist_ok=True)` (mkdir happily accepts a symlinked dir as "exists").
2. **A symlink ENTRY inside out_dir** → `entry.is_dir()` follows it → `rmtree` into the target. Check `entry.is_symlink()` BEFORE `is_dir()`; `unlink()` a symlink entry, never `rmtree` it.
3. **A preserved ownership MARKER** → a planted symlink named like the marker survives the clean; `is_file()` FOLLOWS it → ownership-gate bypass (deletes user data) AND `write_text` on it clobbers the external target. Treat it as managed ONLY if it is a REAL regular file (`not is_symlink() and is_file()`) AND its content parses as the expected JSON; refuse otherwise.
4. **The marker's WRITE-TEMP is ALSO a preserved dotfile** → same survival; `write_text` to a predictable temp follows a planted symlink. `os.replace` of the dest is safe, but the SOURCE temp is the hole. Create it EXCLUSIVE + no-follow: `tempfile.mkstemp(dir=out_dir, prefix="<nondot>-")` (`O_CREAT|O_EXCL|0600` → never follows a symlink, never writes a pre-existing path; a **NON-dot prefix** so a crash-stale temp is swept by the next clean), write via the returned fd, then `os.replace(tmp, marker)`. **Never `Path.write_text` to a predictable/preserved path.**
5. **Reading the marker to validate it** must catch `(OSError, ValueError)` — `ValueError` is the parent of BOTH `json.JSONDecodeError` and `UnicodeDecodeError`, so a garbage/non-UTF-8 marker returns "unmanaged" instead of escaping as a bare traceback.

**A `.resolve()` / `os.path.realpath` ANYWHERE upstream of the guards NEUTERS them** — the guards trust the path they are handed. A shared resolver that computed the out-dir called `dest.resolve()`, which follows ALL symlink components, collapsing a symlinked destination to its real external target BEFORE any `is_symlink()` guard ran; every downstream guard then saw a plain dir and passed (a review bot reproduced it — the adapter wrote into the external target). **Fix:** normalize NO-FOLLOW with `os.path.abspath` (lexical only), **REJECT any `..` segment** in the untrusted destination first (else abspath's lexical `..`-collapse can jump past a symlink), then a component-wise no-follow `is_symlink()` walk of every component STRICTLY BELOW the trusted root. Centralize the check IN the shared resolver so every consumer (assemble / render / diff) inherits it.

**Root-of-trust boundary + audit the whole class in ONE sweep.** Stop the walk at the trusted root (the tool's own install dir) — don't guard at or above it; guard the trusted root's immediate child with `.parent.is_symlink()`, **NOT a realpath-containment check** (realpath over-rejects a LEGITIMATELY symlinked install — root a symlink, real subdir underneath — while `.is_symlink()` on the child rejects only a planted redirect). The moment you find ONE such hole, grep EVERY writer of the class across ALL scripts (`mkdir`/`mkstemp`/`open`-for-write/`write_text`/`os.replace` under the managed root) and walk each target's FULL ancestor chain — the identical bug recurs one tree-level up (leaf marker → its write-temp → sibling snapshot dirs → the shared parent) and across every sibling writer, and an adversarial reviewer surfaces only ONE per round.

**False-RED tail — don't over-reject legitimate OS symlinks.** For a genuinely OUT-OF-root absolute destination, checking EVERY component false-rejects real OS symlinks (`/var`, `/tmp` are symlinks on macOS — including pytest's own tmpdirs), and there's no reliable way to distinguish a benign OS symlink from a planted redirect OUTSIDE the managed root. Keep leaf+immediate-parent for the out-of-root case and DOCUMENT the boundary in code + the PR thread; the realistic threat (a symlink planted in a cloned project tree) is necessarily IN-root and fully closed. General adjudication lesson: when two reviewers disagree on a security finding, weigh the proposed fix's OWN regressions — the "stricter" fix (check every component) is not automatically right when it breaks legitimate usage. Twin of the root-of-trust *value*-location lens in `references/pipeline-trust.md`; cousin of the dangling-symlink read guard (`os.path.lexists`+`is_file`) in `references/json-schema-and-json.md` §3(a).
