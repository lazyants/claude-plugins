---
name: verification-and-runtime-traps
description: "Use when writing or trusting tests/verification and a GREEN result could be masking a real defect — a suite that passes in isolation but fails in the full run, a test that hand-builds a fixture instead of driving the actual SHIPPED artifact (template/seed/scaffold/default config), a fix whose correctness hinges on a DEFAULT flag/mode, a durable-root plugin's resume/idempotency and preflight gating, or running `node --test`. Reach for it before banking any 'tests pass' signal on these shapes."
---

The unifying failure: a green, well-covered suite proves nothing about the real defect because the **tested path diverges from the real path** — the fixture isn't the shipped file, the isolated run isn't the full run, the flagged invocation isn't the default one. Verify the property on the REAL artifact / REAL run / REAL default before trusting green.

## 1. Test through the SHIPPED artifact, not a hand-built fixture

When a consumer hard-requires a marker / header / schema-shape / sentinel in an artifact that is ALSO shipped in the repo (a template, seed file, scaffold, default config), a suite can be fully green while every fresh project is broken — because every test hand-builds a fixture that already satisfies the requirement and no test ever feeds the shipped file to the consumer.

Canonical case: `compute_style_contract_hash` (in `cache_key.py`) hard-required `<!-- STYLE_CONTRACT_BEGIN -->` / `<!-- STYLE_CONTRACT_END -->` markers in `style_bible.md`, with thorough tests for missing/duplicate/out-of-order markers — but every test hand-built a `style_bible.md` fixture that already CONTAINED the markers, while the shipped seed template `style_bible.template.md` shipped WITHOUT them. Deterministic HIGH blocker on every fresh project, invisible to coverage because the consumer's logic and the fixtures were both correct; only the shipped artifact deviated.

**Apply:**
- Assert the shipped artifact itself satisfies the requirement (e.g. `style_bible.template.md` contains exactly one correctly-scoped marker pair).
- Better, an END-TO-END repro that would have been RED on the bug: copy the shipped template to a tmp dir, run it THROUGH the real consumer, assert it returns a value (a hash), not `SystemExit`.
- Pair with a W1/preflight gate that surfaces the requirement early (before expensive work) using the **BYTE-IDENTICAL** condition the deep consumer uses, so "passes the gate" provably implies "won't fatal later."

**Variants of the same masking family — watch for all of them:**

- **Inert-fix (code gains a capability the shipped artifact never learns).** Widening a schema enum is inert unless every **prompt** surface that TEACHES an agent the enum is widened too — and a seed `*_TASK.md` prompt is copied once and never auto-overwritten. A green suite proves the schema ACCEPTS the new value; it never proves any agent EMITS it. Drive the shipped prompt/artifact through the check.
- **Self-derived-expected.** A regression fixture masks its own bug when its EXPECTED value is produced by the same code path under test. An escaped-path parser fixture that passed the still-escaped spelling (`docs\(v2\)/admin/orders.md` — exactly what the buggy parser emitted) as `expectedTarget` asserted `buggy-output === buggy-output` → green, while the real caller compares against a filesystem-DERIVED target (`docs(v2)/...`) → mismatch forever. **The expected side of any transform test must come from an INDEPENDENT source (the real caller's actual input shape), never be re-derived through the transform under test.** Two defenses: (a) a mutation that reverts the fix MUST turn the fixture RED — a fixture a mutation can't break asserts nothing (a same-line `<!-- ## Admin -->` never puts `##` at column 0, so it can't distinguish sanitized-vs-raw scanning; use a multi-line comment that does); (b) an adversarial reviewer re-deriving the expected value from the real caller's data. In an adversarial loop over pure-function code, the FIXTURES are as much a review target as the code.
- **Stubbed-boundary / missing-DEFAULT.** A driver `codex_job.py` launched codex with `--write` only if `--write` was passed to the driver — and that flag defaulted OFF. The Workflow calls it WITHOUT `--write`, so the DEFAULT invocation ran codex read-only → no attempt written → the exact bug being fixed, unfixed. Yet 72 driver tests AND the full 1992-test suite passed green, because the driver tests used STUB gate scripts and NO test asserted the DEFAULT constructed codex argv carries `--write`. **When a fix's correctness hinges on a DEFAULT (a flag that must be ON by default, a mode that must be the fallback), a suite where every test passes the flag explicitly OR stubs the boundary is BLIND to the default being wrong — add a regression test that drives the BARE default (flag-less) path and asserts the produced command/behavior, and at integration read the real constructed argv/command (e.g. the actual `launch()` argv) rather than trusting the suite.**

**The anti-masking gate can itself be self-satisfying.** A drift gate written as *"assert every enum value APPEARS in the artifact"* (a substring check) is vacuously true: the new prose you added to explain a value contains that value's name, so the authoritative allowed-values line can stay stale and the test still passes. A drift test must PARSE the artifact's actual declared list — the parenthesised alternation in the prompt, the `"basis": "a|b|c"` pipe-list in the JSON skeleton — and compare it as a **SET** against the enum parsed from the shipped schema. Substring presence is not agreement.

Corollary throughout: a new test is worthless until watched RED against the real defect first.

## 2. Durable-root plugins: idempotency needs a FRESH pre-dispatch gate

A plugin that scaffolds a durable per-project root (`${durable_root}/`) splits its artifacts into two refresh classes:

| class | examples | refreshed |
|---|---|---|
| **(a) regenerated EVERY run** | Workflow templates instantiated at dispatch (`glossary-pass-wf.template.js`, `mass-translate-wf.template.js`) | every run, straight from the plugin |
| **(b) copied ONCE at scaffold** | `schemas/`, `scripts/`, seed prompts (`*_TASK.md`) | only when the scaffold step (Step 0a) actually runs |

A project resumed mid-pipeline (straight at W3/W5) never re-runs the scaffold step, so it gets the NEW class-(a) prompt against the OLD class-(b) schema — one perfectly atomic git commit, one self-inconsistent runtime. "One commit" atomicity is a fiction across a durable-root boundary; the atomicity exists only in git.

**Why it's a HANG, not a warning:** the dispatch prompt orders the agent *"Repeat until it prints `"success": true`"* against a validator reading the stale durable schema. Teaching the prompt a value the schema rejects ⇒ the agent can never satisfy it ⇒ unbounded fix loop ⇒ the wait-step timeout — a silent multi-minute stall per batch, not a clean failure. The regression test must deliberately CONSTRUCT a stale durable root: the suite always runs against the fresh plugin tree and never reproduces this on its own.

**Apply:**
1. Any change that widens a contract taught by a class-(a) prompt AND enforced by a class-(b) schema needs a **RUNTIME preflight gate**, not just commit-level atomicity. A prompt-contract-version bump does NOT cover it — that only fires when the scaffold/validate step runs, which is exactly the step a mid-pipeline resume skips.
2. The preflight must **NOT call any class-(b) script** — a stale durable root has a stale validator too, so asking it "do you support X?" is circular. Read the durable **data files directly** (`json.load` the durable schema; check the seed prompt's contract marker) from a class-(a) surface with a stdlib-only one-liner, and abort before any dispatch with an actionable "re-run Step 0 + 0a" message.
3. Direction asymmetry: `schemas/`+`scripts/` ARE overwritten unconditionally *when* the scaffold runs; the seed `*_TASK.md` prompts are NEVER auto-overwritten (guarded on destination absence). Different files go stale for different reasons.

**Gate placement — the self-blocking-gate deadlock (three placements, only the third works):**
- **In Step 0 (a pre-scaffold validator like `profile_validate.py`) → deadlock.** Step 0 runs strictly before Step 0a (the step that unconditionally overwrites the durable schemas), so a Step-0 fatal blocks the very "re-run Step 0a" remedy it prints.
- **As a counted `agent()` preflight inside the resumable Workflow → stale replay.** An `agent()` step inside a resumable Workflow is resume-cached by the run's `input_digest`, and that digest does not bind the durable-prompt bytes or the live plugin schema — so a cached `stale`/`ok` verdict REPLAYS across the operator's remedy (they fix the durable root, resume the same run, get the old cached answer). It also perturbs a fixed workflow call-count cost formula, dragging in estimator tests.
- **As a standalone plugin-path script invoked by the orchestration prose right before dispatch → correct.** Run it via `{{PLUGIN_ROOT}}/…`, deterministic, NOT an `agent()`. It is fresh on every resume (never cached), runs AFTER Step 0a so "re-run Step 0a" is reachable, and only fires on the dispatch path that can hang. Summary: fire-before-its-own-fix = deadlock; fire-as-cached-workflow-agent = stale replay; fire-as-a-fresh-pre-dispatch-script-after-the-fix-step = correct.

**Two more rules for the gate itself:**
- A new such plugin-path gate is a **third never-copied script** — add it to Step 0a's copy-exclusion list AND land that exclusion in the SAME commit as the script (else a between-commits Step 0a copies a vacuous-pass durable landmine).
- Guard every durable read PER FILE (`os.path.isfile`) and wrap `json.load` in `try/except` (`OSError` + `JSONDecodeError`) → an actionable halt, never a bare traceback. "The durable root exists" is NOT the resumed-project discriminator — a durable dir can exist with `schemas/` absent.
- Currency check: enum-SET equality is too weak (a partial migration adds the value at every enum site but keeps the old conditional shape), and a hand-picked "correctness-bearing projection" is also too weak (it false-PASSes on any un-enumerated construct). Drop projection entirely and compare the **WHOLE artifact as order-exact canonical JSON** (`json.dumps(sort_keys=True)`), strictness-biased — a healthy durable is a byte-copy, so equality is the right check.

## 3. isolation-green ≠ suite-green (pytest collection-time sys.modules pollution)

A `*.test.py` file can pass **15/15 alone** (`pytest tests/validate_assembled.test.py`) AND pass paired with its suspected polluter (`pytest a.test.py b.test.py` → 51 passed) yet FAIL only in the full `pytest tests/` run. Deterministic (alphabetical), not flaky.

**Cause.** A *different* test file runs a module-scope (collection-time) side effect:
```python
FA = _load_final_audit_module()   # top-level → runs at COLLECTION
# _load_final_audit_module() does spec.loader.exec_module() on the REAL shipped final_audit.py,
# whose own `sys.path.insert(0, SCRIPTS_DIR); import validate_draft as vd`
# caches sys.modules['validate_draft'] -> the REAL shipped file (a real, non-fixture DURABLE_ROOT).
```
With `--import-mode=importlib` (common for `*.test.py`-named suites), pytest imports every collected file's top-level code BEFORE any test runs, so the pollution is present regardless of execution order. A later test doing its OWN in-process `importlib.util.module_from_spec` + `exec_module()` of a script that also `import validate_draft as vd` (a plain import — checks `sys.modules` first) silently binds to the **stale cached** sibling (pointing at a different tmp_path/fixture) → the loaded module reads the wrong root → wrong result.

**Why the two-file pairing doesn't reproduce it:** pytest's collection scope is only the files passed as arguments. `pytest a.test.py b.test.py` never collects the polluter unless it's one of the two, giving a false all-green. Only the full `tests/` run (or one that happens to include the polluting file) reproduces it.

**Fix — hermetic in-process loader.** Snapshot & pop the sibling `sys.modules` entries around `exec_module`, restore after, forcing a fresh bind every time:
```python
_SIBLING_MODULE_NAMES = ("validate_draft", ...)  # every bare-name import the loaded script does
@contextlib.contextmanager
def _hermetic_sibling_imports():
    saved = {n: sys.modules.pop(n, None) for n in _SIBLING_MODULE_NAMES}
    try:
        yield
    finally:
        for n, m in saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m
```
Keep the load IN-PROCESS if the test needs to monkeypatch the loaded module — a subprocess can't be patched from the parent. Do NOT convert to a subprocess to "fix" it if you lose the patch surface.

**Meta rule:** a `*.test.py` file passing alone (even paired with the suspect) proves nothing about the full run. Only `pytest tests/` at 0 failures is the trustworthy gate before shipping — re-run the FULL suite yourself; never bank an isolation pass.

## 4. `node --test <dir>` fails with a misleading MODULE_NOT_FOUND

`node --test plugins/enduser-handbook/tests/` (seen on Node v26.3.0) does NOT discover-and-run the suite — it treats the bare directory positional as a script entry point and throws `Error: Cannot find module '.../tests' … code: 'MODULE_NOT_FOUND'`, then reports a bogus `tests 1 / pass 0 / fail 1`. The error points at a missing import, which is misleading — nothing is wrong with the tests (each runs green when named explicitly).

**Fix:** pass explicit test FILE paths, e.g. `node --test plugins/enduser-handbook/tests/control-inventory.test.mjs plugins/enduser-handbook/tests/capture-guard-policy.test.mjs`.

This is `.mjs`-ONLY (the zero-dependency `node:test` suites). It does NOT apply to `literary-translator`, whose tests are Python named `tests/*.test.py` and run with pytest — `cd plugins/literary-translator && python3 -m pytest` (full) or `... python3 -m pytest tests/<name>.test.py` (focused); its `pytest.ini` sets `python_files = *.test.py` and `--import-mode=importlib`. `node --test` SyntaxErrors on those (the Python triple-quoted docstrings). Don't reach for the node rule when editing pytest `.test.py` files.
