# Skeptic pass — the opt-in adversarial re-read of a frozen canon

**Read this only when `glossary.skeptic_pass.enabled` is true.** The pass is opt-in and
defaults to false: `glossary.skeptic_pass.enabled` is one of only two booleans in
`profile.schema.json` that default to `false`, and it is off in every live book profile. When
it is off, nothing here runs and nothing here binds — which is why this document is on demand
rather than part of the Pre-read mandate.

It moved out of `SKILL.md` unchanged. Every rule, gate, exit-code contract and tamper-detection
clause below is the text that lived there, so a project that turns the pass on loses nothing.

**Skeptic pass (RFC #215 Phase 2, opt-in + advisory)** — if
`glossary.enabled` is not false AND `glossary.skeptic_pass.enabled` is true
in `profile.yml`, run the structural-risk triage + adverse-only skeptic
pass immediately after the mandatory homonym-split gate (`SKILL.md`, W3) and before
W3a. The parent switch is not a formality: Step 0 fatally refuses the
contradictory combination — `glossary.enabled: false` with
`glossary.skeptic_pass.enabled: true` — outright, naming both fields, so
this pass never reaches a project that declared it wants no glossary at
all (see the `glossary.enabled: false` branch in `SKILL.md`'s W3). Every enabled pass
re-derives its own worklist fresh (never trusts a stale one):
`suspicion_scan.py --canon ${durable_root}/canon.json --manifest
${durable_root}/manifest.json --particle-config <literal value>
--research-mode <profile's glossary.research_mode> --source-format
<profile's source.format>` plus the profile's `glossary.skeptic_pass`
overrides (`--dispersion-threshold` / `--sample-cap` /
`--windows-per-entity` / `--near-threshold` / `--near-cap` /
`--near-pair-budget` / `--citation-block-types`, else
`skeptic_constants.py` defaults), writing
`${durable_root}/suspicion_worklist.json`. Then `skeptic_setup.py`
(`kind="skeptic"`, a resume domain fully separate from `resume_setup.py`
— never edits it, never adds a `kind` to it), invoked with **`--source-lang
<the SAME source-language label you interpolate into the template's
`{{SOURCE_LANG}}` placeholder at Step 0a>`** (REQUIRED — folded into the
skeptic input_digest, so changing the prompt's source-language label forces
a fresh RUN_ID; NOT reconstructed from `source.language.code`, since the
glossary/skeptic templates render `{{SOURCE_LANG}}` as a human-readable name,
not the locale code) plus its resolution flags (`--particle-config`,
`--research-mode`, `--source-format`, `--batch-agent-cap`, and any
`glossary.skeptic_pass` tuning overrides — mirror the `suspicion_scan.py`
values above; run `skeptic_setup.py --help` for the full set), validates
that worklist's freshness (schema + `producer_input_digest`), resolves the
skeptic RUN_ID, and atomically writes
`${durable_root}/skeptic/runs/{RUN_ID}/assignments.json` (aggregate) plus
one `assignments_{index}.json` per batch — BEFORE any dispatch. Only then
instantiate `skeptic-pass-wf.template.js` fresh from the plugin's current
copy (see Step 0a) and run it, passing `args` = the batches grouped from
`assignments.json`, each entity's `windows[]` enriched with the resolved
whole-block `text` (`manifest.blocks[window.block].plain_text`) alongside
the assignment's own fields. The Workflow's own dispatch → bounded-wait →
`skeptic_ready.py --validate-fragment` per batch, then one serialized
`skeptic_ready.py --merge-fragments` plus a disk-independent
`skeptic_ready.py --verify-merged`, produce
`${durable_root}/skeptic_triage.json`. Finally run `skeptic_report.py` to
render the findings for a human.

**Agent-trust & tamper-detection (H1):** like the glossary pass, this opt-in
pass feeds source-text windows to a file-capable `codex:codex-rescue` agent —
it carries the same pipeline-wide agent-trust, adding NO new filesystem
privilege and NO new accepted-state write path (the triage schema is
adverse-only and no freeze/merge reader opens `skeptic_triage.json`). As a
best-effort integrity tripwire, `skeptic_setup.py` stamps a THREE-way hash
triplet — `canon_sha256`/`manifest_sha256`/`senses_sha256` (#243 made
`canon_senses.json` a third authoritative frozen input this release, so it
is stamped and checked alongside the other two) — into the aggregate
manifest. Both the stamper and every verifier ultimately reduce to the
same `compute_frozen_input_hash_from_state` (`suspicion_scan.py`) — no
second, independently-drifting copy of the hash formula exists to fall out
of sync — but WHEN and HOW each side reads the bytes it hashes differs, and
the difference is load-bearing. The stamper always hashes a `(state,
content)` pair it already captured ONCE at derivation-read time, before the
freshness/worklist check that same snapshot fed — a fresh re-read at
stamp-write time would instead record whatever is on disk at THAT later
moment, silently adopting any mutation that landed in the window between
derivation and stamping as if it had been there from the start. Verifiers
are not uniform either: `canon.json` and `canon_senses.json` are hashed
from a captured snapshot too — the SAME one a downstream parse of the
competitors universe (#243) goes on to reuse — so the tamper comparison and
that parse can never independently disagree about which on-disk version
each one describes.

All three frozen inputs — `canon.json`, `manifest.json`, `canon_senses.json`
— now go through that one gated capture step alike, with no exception:
`frozen_input_check()` drives all three off a single table, and the loop
over that table is the only place `frozen_input_check()` itself reads a
frozen input's bytes for the H1 tamper comparison. It is not the only place
in `skeptic_ready.py` that reads canon/senses bytes at all —
`_resolve_competitors()` deliberately falls back to a plain fresh read of
`canon.json`/`canon_senses.json` when the caller has no H1-approved
snapshot to reuse for that particular input (`run_validate_fragment`, which
never calls `frozen_input_check()` at all; and `run_verify_merged` for
whichever of the two inputs happened to have no stamp to compare against).
That fallback is intentional, not a gap this round closed — see
`_resolve_competitors()`'s own docstring. `manifest.json` used to be wired
in separately, as a
hand-written call that captured its own snapshot outside that gate — which
is exactly how a stamped `manifest.json` read failure could escape the
standalone check raw, despite that mode's own documented "never crashes"
contract. Folding it into the same table doesn't just fix that one gap, it
removes the capacity for a future fourth frozen input to reopen it the same
way *inside `skeptic_ready.py` itself*: the only way to wire a read into
this module's own H1 tamper-comparison path is to add a table entry, and
there is no longer a code shape in `skeptic_ready.py`'s
`frozen_input_check()` that reaches a frozen input's bytes for that
comparison any other route. That is narrower than "any read of a frozen
input's bytes at all" — `_resolve_competitors()`'s fallback reads
(described two paragraphs above) stay exactly as un-gated as before; they
were never the gap this table closes, since they answer a different
question (what is the competitor universe) than the one the table's H1
comparison answers (did this frozen input change). `manifest.json`'s
snapshot is captured through that same gate and, since #268, returned like
the other two: `run_verify_merged` parses it to re-authenticate every cited
evidence record, so it needs the bytes the tamper comparison hashed rather
than a second, independently read version of the file.

That table (round 7) closed the read-side gap inside the verifier, but it
was still, by itself, only ONE of three independent enumerations of the
frozen-input set: `skeptic_setup.py` (the stamper) separately hand-wrote
the three `"..._sha256": ...` fields into `assignments.json`, and this
schema separately declared them — a fourth frozen input could be added to
the stamper and the schema and simply never typed into
`frozen_input_check()`'s table, and nothing would fail; it just wouldn't be
checked. Round 8 (#243 codex follow-up) closes that: `FROZEN_INPUT_SPECS`,
a single `(key, filename label, stamp field name)` tuple in
`skeptic_constants.py`, is now the shared source both sides iterate — the
stamper builds every stamp field in `assignments.json` from it (no
hand-written `"..._sha256"` line remains in `skeptic_setup.py`), and the
verifier builds its own check table from the exact same tuple. A frozen
input can no longer be wired into the stamper without also being wired into
the verifier, because there is no longer a place in EITHER script's own
code to add one without touching that shared tuple first. The schema's
`canon_sha256`/`manifest_sha256`/`senses_sha256` properties are still
separately-declared static data (JSON Schema cannot derive from a Python
tuple) — a parity test asserts the schema's declared **top-level property
names ending in `_sha256`** equal the stamp-field set `FROZEN_INPUT_SPECS`
derives, so a `_sha256`-suffixed schema property added without a matching
tuple entry (or the reverse) fails that test rather than going unnoticed.
That suffix filter applies to the SCHEMA side of the comparison only —
`FROZEN_INPUT_SPECS`'s own stamp-field set is never filtered by it — so a
schema property that stamps a frozen input WITHOUT a `_sha256` name (or
one nested below the top level, like `assignments[].evidence.sha256`)
stays invisible to this parity check only when it exists on the schema
side ALONE, with no matching `FROZEN_INPUT_SPECS` entry. If the same
non-suffix field is ALSO present in `FROZEN_INPUT_SPECS` (a tuple entry
whose `stamp_field` happens not to end in `_sha256`), the equality DOES
fail: that field appears in the tuple's unfiltered stamp-field set with
nothing on the filtered schema side to match it.

`FROZEN_INPUT_SPECS` binds the stamper and the verifier's tamper check —
that is its whole guarantee, and even that much is conditional on every
tuple entry having its OWN key (round 11, #243: a new entry that instead
REUSES an existing key aliases that key's existing snapshot at the
stamper/verifier's own hand-maintained lookup dicts instead of raising.
All four such maps now carry the same fail-closed
`sorted(<keys>) != sorted(<spec keys>)` guard — `suspicion_scan.py`'s and
`skeptic_setup.py`'s two digest functions and `skeptic_setup.py`'s own
`run()` block (round 11), plus `skeptic_ready.py`'s `paths` map in
`frozen_input_check()` (round 12) — and an AST-driven ALLOWLIST test parses
every top-level `*.py` file directly under `SCRIPTS_DIR` and requires each
of the four sites to carry a guard structurally identical to a canonical
shape. This replaced a six-round DENYLIST (#243, rounds 11-16, preserved
in git history, not reproduced here): each round found one more way to
weaken a guard while still passing, and the check grew one more rejection
rule to name that specific weakening. Six rounds in, the reviewer's own
prescribed fix was to build a general reaching-definition/dataflow
analyzer inside a test, for four guards that are, in practice, textually
near-identical — a denylist has no bounded endpoint, since it can only
enumerate weakenings someone has already thought to check for.

The allowlist inverts the approach: a candidate `if` qualifies only if it
is structurally identical to the canonical shape, and anything that does
not match is rejected outright, with no attempt to resolve or characterize
why it differs. Concretely: the test must be exactly `sorted(X) !=
sorted(Y)` for two bare-Name operands, with the builtin `sorted` itself
unshadowed anywhere reachable from the guard's owning or module scope;
exactly one of X/Y must be bound, as its owning scope's SOLE binding, to
one of the two canonical `FROZEN_INPUT_SPECS` key-projection
comprehensions the real sites actually ship — the subscript form
`[spec[0] for spec in FROZEN_INPUT_SPECS]` at three sites, or the
destructure form `[key for key, _label, _stamp_key in FROZEN_INPUT_SPECS]`
at `skeptic_ready.py`'s `frozen_input_check()`; and the body must directly
raise `AssertionError` carrying the anchor phrase. A hoisted `if _flag:`
(the comparison assigned to a name first) simply isn't this shape — a
bare-Name test — so it needs no special-case rejection rule of its own,
unlike the denylist version it replaced. This is strictly stronger
than that: the denylist enumerated specific weakenings to reject, and
each round found one it had missed, while the allowlist rejects any
candidate that does not match the canonical shape — no dataflow
analysis needed, only structural AST comparison plus a few
scope-bounded binding facts. The remaining trust boundary is not an
unanticipated class of weakening but a gap in the structural template
itself — some node facet the match doesn't pin. Structural completeness
is what has to be gotten right, and unlike "did we think of every
weakening," it is a property that can be checked directly.

`EXPECTED_GUARD_SITES` is still a hand-maintained set of the four (file,
function) obligations a guard must resolve to — there is no way to derive
"which four functions should own a guard" from the source itself — but it
no longer also pins each site's own two operand names the way an earlier
version of this check did: the structural requirements above already make
a "right shape, wrong operand" copy-paste pointless to separately guard
against, since the spec operand must independently prove itself against
the canonical projection shape and the other operand's own spelling is
never validated at all (it is simply the value under test). A guard's
owner is the nearest lexically-enclosing function, tracked by a scope walk
that resets attribution to `None` on crossing a `ClassDef` boundary — a
class body's own top-level statements run in the class's namespace, not
the enclosing function's runtime scope, so a guard relocated into a class
nested inside its owning function is reported as unowned rather than
misattributed. Separately, an owner only counts as matching an
`EXPECTED_GUARD_SITES` entry when it resolves to the UNIQUE module-level
def of that name; two module-level defs sharing a name — a dead `if
False: def run(): <guard>` sitting beside the real, unguarded `def run()`
— are reported AMBIGUOUS rather than silently resolved toward whichever
copy happens to carry the guard. Either way — wrong owner, no owner, or
an ambiguous one — a report happens only because a human already
enumerated the sites being compared against, not because the test derives
on its own which functions should own one. A guard is still located only
by this exact structural shape and file-set scan — a copy that lands in a
nested subdirectory the scan doesn't recurse into (no shipped script does
today) is still invisible to it.

Also invisible by construction: an unknown FIFTH key-indexed consumer
added later with no guard of its own at all — there is nothing to compare
it against. The durable fix, tracked as a follow-up, is to centralize all
four guards behind one shared helper each consumer calls, closing this by
construction instead of by enumeration.

What none of this proves, and does not claim to: that a guard's `if`
DOMINATES the protected access at runtime. A guard of the exact required
shape, in the exact expected function, sitting after an unconditional
`return` (or any other control-flow path that skips it) passes every
static check here while never executing. This is not just argued but
verified experimentally: relocating a guard to dead code placed after its
own function's `return` (function otherwise intact) leaves the static AST
check blind at every one of the four sites, as expected — but the
BEHAVIORAL suite's coverage of that same relocation is uneven, not
absent, and not universal either. At
`suspicion_scan.py::compute_producer_input_digest`, the relocation was
still caught: its behavioral test file failed (4 failing tests) even
though the static check stayed blind. At `skeptic_setup.py::run`, nothing
caught it — the static check stayed blind AND the plugin's entire test
suite stayed green (2799 passed, 1 skipped, 2 xfailed). That measurement
established that `run()`'s own copy of this guard was, at the time, the
one site among the four with no mismatch-driven behavioral test of its
own. Round 14 (#243) closed it: `skeptic_setup.test.py`'s
`test_run_fails_closed_on_frozen_input_specs_key_mismatch_after_upstream_digests`
wraps the real `resolve_skeptic_run()` so a duplicate-key
`FROZEN_INPUT_SPECS` mutation is injected only after both upstream digest
guards have already run against unmutated state, then asserts `run()`'s
own guard raises and names both sorted key lists — verified red the same
way, against the guard relocated to the same dead code. That test's own
docstring records a residual gap: weakening the guard to a bare LENGTH
comparison still passes green against this duplicate-key mutation (only a
same-count divergent-key swap would slip past it), a gap
`compute_skeptic_input_digest()`'s own round-11 parametrized test already
covers directly. The other three sites' behavioral tests
(`suspicion_scan.test.py`'s and `skeptic_setup.test.py`'s own
`duplicate_key_entry`/`same_count_key_swap` cases for the two digest
functions, `skeptic_ready.test.py`'s
`--verify-merged`/`--check-frozen-inputs` case for `frozen_input_check()`)
close this reachability gap for their own sites, not because they were
built to prove reachability, but because driving the guard through a real
key mismatch necessarily also proves it executes.
`FROZEN_INPUT_SPECS` does NOT bind the earlier
`read_frozen_input_snapshot()` capture in `skeptic_setup.py`, and its
SIGNATURE does not bind `compute_producer_input_digest()`/
`compute_skeptic_input_digest()` either — both still take a fixed
positional/keyword canon+manifest+senses signature, unrelated to this
tuple; a fourth frozen input still needs its own hand-added parameter (and
a matching update at every call site) before either digest can hash it at
all. Round 9 shipped with that gap silent, and it remains true only
CONDITIONALLY, historically stated: a fourth frozen input added to
`FROZEN_INPUT_SPECS` (plus the schema) is captured, stamped, and
H1-tamper-checked ONLY once it ALSO gets its own hand-added entry at every
one of those other manual sites — never from the tuple entry alone, and
never by reusing an existing key at any one of them (rounds 11 and 12
closed that silent-alias failure mode at all four key-indexed maps, and
`test_frozen_input_specs_keys_are_unique` closes it a layer beneath, on
the tuple itself; the schema's own `_sha256`-suffix parity test stays
deliberately blind to it, comparing STAMP FIELDS rather than keys, which
is why the uniqueness invariant is a separate test with its own
unambiguous failure message). Assuming those manual sites ARE correctly
updated, the gap that remained through round 9 was narrower but still
real: the fourth input would be captured, stamped, and H1-tamper-checked
correctly, yet invisible to both freshness digests — a mutation to it
BEFORE `skeptic_setup.py` ran would leave a stale worklist's
`producer_input_digest` unchanged, so the stale worklist would still read
as fresh and get (re)certified against the new state, the same
stale-certified-as-fresh failure mode this release closes for
`canon_senses.json`, just re-opened at a boundary this tuple didn't reach.

Round 10 (#243) closed that silent half WITHOUT touching either
signature or any call site: each function body now builds its own
`{key: (state, bytes)}` map from the parameters it already receives and
asserts that map's key set equals `FROZEN_INPUT_SPECS`'s key set BEFORE
hashing anything. A parameter added to the signature with no matching
`FROZEN_INPUT_SPECS` entry (or a `FROZEN_INPUT_SPECS` entry with no
matching parameter/map entry) now raises `AssertionError` the first time
the function runs, instead of the digest silently omitting the new input
forever. Both digest functions were re-derived this way against
`FROZEN_INPUT_SPECS`'s current 3-entry order and verified byte-identical
to the pre-round-10 formula on a fixed fixture — this is a hardening of
what already-shipped projects hash, not a digest-compatibility break.

Round 11 (#243): that key-SET comparison itself had a gap -- a `set()` on
both sides collapses duplicates, so a `FROZEN_INPUT_SPECS` entry that
REUSES an existing key (rather than getting its own) reduced to the same
key set as the hand-maintained map and passed the guard silently, then
aliased the reused key's snapshot into the loop instead of hashing the
new input at all. Both digest functions now compare the full,
non-deduplicated, sorted KEY LIST instead of a set -- strictly stronger,
since a duplicate changes the list even when it doesn't change the set —
and were re-verified byte-identical to the pre-round-11 formula on the
same fixed fixture.
`skeptic_constants.py`'s own comment next to `FROZEN_INPUT_SPECS` lists
every site a new frozen input still needs by hand, and which of them now
fail loud versus which (the raw capture calls only) still don't.
Generalizing the two digest functions' SIGNATURE so this tuple could drive
parameter names too was evaluated (#243 round 9) and deferred again in
round 10: both are direct-called by fixed parameter name/position from
dozens of sites in `tests/skeptic_setup.test.py` and
`tests/suspicion_scan.test.py`, several of which pin the exact NUL-byte
framing between two specific adjacent parameters — a cross-file
test-authoring change, not a same-file mechanical one. Round 10 fixed the
silent-omission risk by hardening the function BODIES instead.

Detection now fires at **two** decision points, not one. The first is
`skeptic_ready.py --verify-merged`'s own internal check, which runs after a
successful merge, as before. The second is new this release and is the
substantive part of the fix, not a footnote: `skeptic_ready.py
--check-frozen-inputs` — a standalone CLI mode built from the exact same
shared `frozen_input_check()` function `--verify-merged` calls internally —
is now called UNCONDITIONALLY from the Workflow's `notReadyBatches` branch,
before it concludes that a batch never becoming ready is merely an ordinary
advisory outcome. Previously, that branch gave up with a bare
`fragment-check-failed` and never called `--verify-merged` at all, so a
frozen input tampered sometime after `skeptic_setup.py` stamped this run
but before any batch's fragment ever validated would go completely
unreported as the FATAL tamper it is — the not-ready path is exactly where
a run ENDS when something has already gone wrong, so it is also exactly
where a tampered input was most likely to go unnoticed: the old behavior
reported the most alarming possible state (a frozen input changed
mid-pass) as the blandest possible outcome (an ordinary "some batches
didn't finish" advisory).

Sharing that one function does NOT mean the two modes always agree, and the
divergence is deliberate, not a bug: they answer differently on a READ
failure for a frozen input, as opposed to a hash MISMATCH, which both
always treat as fatal. `--verify-merged` fails CLOSED on a read error — it
raises raw — because degrading a frozen input it still needs to parse (the
#243 competitors universe projects from `canon.json`/`canon_senses.json`,
and since #268 evidence re-authentication parses `manifest.json` off the
same captured bytes) would silently empty that universe and let every
ambiguous form sail through unflagged, exactly the fail-OPEN failure mode
this release closes elsewhere. One qualification, added by #268: a read
error still ends that run, but if a tamper on a *different* frozen input has
already been detected by then, the run reports that mismatch instead of
dying with an exception the result shape cannot carry — the mode's answer to
an unreadable input is unchanged, only its answer to "unreadable AND already
tampered" is. `--check-frozen-inputs` tolerates the same read error and
degrades instead, because it never parses anything downstream — it only
ever answers "did a frozen input change," and raising there would trade its
own documented "never crashes" contract for a check that buys nothing in
return. Read the two modes as applying different, equally deliberate rules
for an unreadable input, not as two implementations of one rule that happen
to disagree.

This catches ACCIDENTAL / non-adversarial mutation of the frozen inputs (a
crash, a stray process, a buggy well-behaved agent) — it is NOT a hard
guarantee: a prompt-injected agent with pipeline-wide FS-write can rewrite
or delete the co-located stamp to match its tampered canon. A sound version
(anchoring the setup-time hash in a trusted CLI channel) is deferred to
Phase 3 alongside the warn→block flip; full agent containment is the
out-of-scope pipeline-wide FS-sandbox concern.

**Exit-code contract:** this block is advisory FOR SKEPTIC FINDINGS — a
non-zero exit from `suspicion_scan.py` / `skeptic_setup.py`, or a Workflow
result of `merged:false` for an ordinary skeptic reason (batch-too-large /
fragment-check-failed / coverage-gap / `verify-failed`), HALTS only the
skeptic pass for this run; log it and proceed straight to W3a regardless.
**EXCEPTION — a frozen-input mutation is FATAL to the WHOLE pipeline, NOT
advisory:** if the Workflow result carries `frozenInputMismatch: true`
(reason `"frozen-input-mismatch"` — either `skeptic_ready.py
--verify-merged` after a successful merge, OR `skeptic_ready.py
--check-frozen-inputs` from the `notReadyBatches` branch when a batch never
became ready, re-hashed `canon.json`/`manifest.json`/`canon_senses.json`
and found one changed on disk since `skeptic_setup.py` stamped this run —
see the H1 paragraph above for why both decision points exist), do NOT
proceed to W3a. The frozen inputs W3a consumes (segpack canon injection,
translation) were mutated mid-pass, so continuing would bake that mutation
into accepted state. HALT here (FATAL), surface the mismatch, and require
restoring + re-freezing/re-validating the trusted `canon.json`/
`manifest.json`/`canon_senses.json` before any re-run. (This is the one
non-advisory outcome of the opt-in pass; every skeptic *finding* stays
advisory.)
**The cat-5 audit command (`canon_adjudication_audit.py --check`,
immediately above) is UNCHANGED by any of this** — it never reads
`skeptic_triage.json` / `suspicion_worklist.json`, and its own summary +
exit code are byte-identical whether or not this opt-in pass ever ran;
`skeptic_report.py` is a wholly separate, advisory command a human runs to
see the skeptic pass's own findings, never itself a gate. When
`glossary.skeptic_pass.enabled` is false/absent (the default), skip this
entire block.

