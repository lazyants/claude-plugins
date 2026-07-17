# Authoring JSON-Schema, stdlib-JSON, and dep-free-YAML gates

The mechanical traps in reading/extending a schema or hand-writing a stdlib gate over JSON or YAML. See the spine in `SKILL.md` for the invariants these apply.

## Contents
1. Extending an agent-facing JSON Schema (draft 2020-12 / `jsonschema` 4.26)
2. `format:"uri"` silently needs `rfc3987` (GPLv3) — override with stdlib
3. Hardening a stdlib-only JSON-reading gate — the ~7 out-permits + type traps + schema-vs-runtime
4. A `const`-pinned field falsifies a "hardcode overrides the config knob" bug
5. Dep-free single-field extraction from a YAML/config file

---

## 1. Extending an agent-facing JSON Schema (draft 2020-12 / `jsonschema` 4.26)

All verified empirically (add a `sense_translated` value to a `basis` enum with its own conditional).

- **A bare top-level `if`/`then` CANNOT take a sibling — restructure into `allOf`.** Many hand-written schemas carry a bare top-level `if`/`then` pair. Pasting a *second* `if`/`then` beside it is a **duplicate JSON key**: `json.loads` accepts the file and **silently keeps only the LAST pair — the original constraint vanishes with no error anywhere.** The single easiest way to silently delete a rule you meant to keep. Fix: `"allOf": [ {if,then}, {if,then} ]`, then add a regression asserting the ORIGINAL conditional still rejects its negative cases — that test is the only thing standing between you and the silent drop.
- **`additionalProperties: false` × `allOf` footgun does NOT bite — know why.** The classic footgun (outer `additionalProperties:false` can't see properties introduced inside `allOf`/`if`/`then`) is harmless **iff every property is declared in the schema's own top-level `properties`** and the `then` blocks only *constrain* already-declared ones (never introduce new). Check that before assuming you're safe.
- **`minLength: 1` admits `"   "` — use `"pattern": "\\S"`.** A whitespace-only string passes `minLength:1`. Not academic when consumers disagree (one tested raw truthiness → delivered `"   "`; another stripped-and-skipped it — a silent split). `pattern: "\\S"` (unanchored `re.search` ⇒ "contains a visible character") is the right idiom. Test whitespace-only, not just empty.
- **Constrain the CO-FIELD, or the new enum value escapes a downstream filter.** A downstream gate filtered with `entry["is_proper_name"] is True`; the new enum value was legal with `is_proper_name: false`, so an entry could validate, freeze, and be delivered while **silently escaping the audit** meant to review it. If a downstream consumer keys on a co-field, pin that co-field structurally in the new value's conditional (`"is_proper_name": { "const": true }`).
- **THE BIG ONE — `oneOf` makes the error message USELESS to an agent.** For a discriminated union (`oneOf` on a `disposition` field), a violation surfaces as `ValidationError.message` → **a DUMP of the whole instance** (not even "is not valid under any of the given schemas"), while the ACTUAL reason lives only in `ValidationError.context` (which *also* carries the non-matching branch's noise, e.g. telling an `accepted` item it "should have been `review_queue`"). A formatter that prints only `e.message` tells the agent nothing — and an agent under orders to *"repeat until the validator prints `success: true`"* **cannot converge → unbounded retry → timeout.** Any validator whose errors are read by an **agent** must recurse into `e.context` and present the sub-errors **grouped/labelled by branch** (or select with `jsonschema.exceptions.best_match`).
- **Verify empirically, don't reason.** A ~12-case standalone harness — build the schema, run `Draft202012Validator(...).iter_errors()` over positive AND negative instances, print each verdict + message — settles all of the above in minutes, including proving the original conditional survived the `allOf` restructuring and the new `const: true` caused no collateral damage.

## 2. `format:"uri"` silently needs `rfc3987` (GPLv3) — override with stdlib

Even WITH `format_checker=jsonschema.FormatChecker()` passed explicitly, `jsonschema` (4.26) only ENFORCES `"format":"uri"` when the optional package **`rfc3987`** is importable. Without it, `fc.conforms("not-a-uri", "uri")` returns **True** — a silent no-op that false-passes malformed URIs, so schema/tests requiring rejection fail ONLY in a clean declared-deps env. Note `rfc3986_validator` (MIT) backs `"uri-reference"`, **NOT** `"uri"`/`"iri"` — those use `rfc3987`.

**License trap:** `rfc3987` is **GPLv3+** — declaring it in an MIT project's `requirements.txt` adds a copyleft runtime dep.

**Fix (dep-free + MIT): register a stdlib checker that OVERRIDES jsonschema's built-in `"uri"`** (a registered custom checker wins regardless of whether `rfc3987` is installed):
```python
fc = jsonschema.FormatChecker()
@fc.checks("uri", raises=(ValueError,))
def _uri(v):
    from urllib.parse import urlparse
    p = urlparse(v)
    if not (p.scheme and p.netloc):
        raise ValueError("not an absolute URI")
    return True
```
scheme+netloc ⇒ a real absolute URL; stricter than rfc3987 (rejects scheme-only `mailto:`/`urn:`), which is fine/better for citation URLs and deterministic under pure stdlib. Then delete any `_rfc3987_backend_effective()` probe / "pip install rfc3987" warning.

## 3. Hardening a stdlib-only JSON-reading gate

For a stdlib-only (no `jsonschema` at runtime) gate that reads a JSON artifact and must **fatal cleanly (exit 2, named stderr, NO traceback) on any malformed input** and **never false-green**. This checklist RECURS verbatim on every new JSON-reading script — a same-plugin precedent does not immunize a new file. Front-load it.

**(a) `json.loads` out-permits strict JSON in ~7 ways — catch ALL at ONE decode chokepoint.** Route every read through one `_read_json_file`; each failure mode → clean fatal:
- **OSError** — unreadable/not-a-regular-file path. Also guard with `os.path.lexists` + `is_file`, NOT `Path.exists`, so a **dangling symlink** counts as present-but-bad, not silently absent.
- **UnicodeDecodeError** — invalid UTF-8 bytes. It's a `ValueError`, **not** an `OSError`, so `read_text(encoding="utf-8")`'s failure needs its own catch.
- **JSONDecodeError** — bad syntax (a `ValueError` subclass).
- **Named non-finite** `NaN`/`Infinity`/`-Infinity` — accepted by default. Reject via `parse_constant=<hook that raises ValueError>`; the hook's ValueError propagates UNWRAPPED, so catch `ValueError`.
- **Numeric-overflow non-finite** — `1e999` is valid JSON syntax that parses to `float('inf')` (`parse_constant` does NOT fire — it's a literal). Reject by round-tripping `json.dumps(parsed, ensure_ascii=False, allow_nan=False)` → raises ValueError on any inf/nan.
- **Lone surrogate** `"\ud800"` — valid JSON, accepted into a Python str, but NOT UTF-8 encodable. Surface via `json.dumps(parsed, ensure_ascii=False).encode("utf-8")` → `UnicodeEncodeError` (`ensure_ascii=False` is load-bearing: default True re-escapes the surrogate and the encode silently succeeds).
- **Deep nesting** → `RecursionError` (a `RuntimeError`, **NOT** a ValueError). Handler MUST be minimal — a short static message, **no `{e}`, no helper-function frame** — near stack exhaustion any extra formatting/frame can re-trigger RecursionError.

Keep the surrogate-encode, non-finite-dumps, and recursion catches as **SEPARATE try-blocks** with distinct messages: `UnicodeEncodeError ⊂ ValueError`, so a broad `except ValueError` would swallow the surrogate case with the wrong message.

**(b) Python type traps that false-green / false-reject numeric identity.**
- **`bool` subclasses `int`** (`True==1`): a JSON boolean false-matches an integer field. Compare with `type(v) is int`, NOT `isinstance` (which admits bool).
- **jsonschema treats `1.0` as valid `"type":"integer"`.** A `type(v) is int`-only runtime check false-REJECTS a schema-valid `1.0`. Also accept: `type(v) is float and v.is_integer() and v == expected` (`is_integer()` is False for fractional AND NaN/Inf → they stay rejected).

**(c) Schema-vs-runtime drift — the three-way resolution.** An adversarial reviewer mechanically flags every `jsonschema.validate` vs runtime divergence; you cannot close them all by validating in the runtime (violates stdlib-only + duplicates the authoring layer). Resolve per field class:
- **Load-bearing field of a CURRENT item** (verdict/version/identity that decides the gate) → ENFORCE in the runtime to match the schema.
- **Advisory / documentation-only field** the runtime never reads → **LOOSEN the schema** (drop its type/enum, keep a description) so schema == lenient reader. Enforcing it would over-block on cosmetic typos.
- **Content of ORPHAN / non-current records** → **RATIFY a documented two-layer boundary**: the gate validates current-item + structural readability; full-file conformance is the authoring layer's job (safe because required items are recomputed fresh from the source-of-truth, never from the artifact). A genuine design fork — **ask the owner**.

**(d) Testing traps.**
- **Deep-nesting depth is interpreter-specific.** On CPython 3.14 a `"["*N+"0"+"]"*N` PARSES at N=100000 and is caught by the "not a JSON object" structural check — the WRONG path. RecursionError only fires at **N≥~200000** (use 500000). A too-shallow N makes the test assert the recursion message but hit the structural one → green report, wrong path tested.
- Build the malformed file as **literal text** via `write_text`, never `json.dumps` (which re-normalizes surrogates/NaN/depth into a different representation and misses the target path).

**(e) Improvements worth using over the originals above.**
- **Deterministic depth rejection beats "pick a bigger N".** Add an ITERATIVE (explicit stack/queue, non-recursive) depth-measurer; reject any doc over a small generous `MAX_NESTING_DEPTH` (e.g. 100 — real payloads nest ~6 deep) BEFORE `json.loads`'s result reaches `jsonschema`/`repr()`. This makes the RecursionError class UNREACHABLE regardless of interpreter. Test the boundary at `MAX_NESTING_DEPTH + 1`, and keep ONE separate test at extreme depth (e.g. 100000) asserting ONLY "some clean error was raised" — which layer catches an extreme case legitimately differs by interpreter (CPython 3.11.15/3.12.13 raise RecursionError inside `json.loads`; 3.14.6 parses it fine), so a message-content assertion there is a real cross-interpreter flake.
- **Direct string-walk beats the round-trip for lone surrogates.** An iterative walk over every dict key AND string value, `.encode("utf-8")` on each, raising with the exact JSON path (dot/slash-joined ancestor segments built from already-validated segments only, joined lazily at raise time) gives an actionable message for free.
- **The crash may be DOWNSTREAM of the loader — still the loader's bug.** A lone surrogate crashed a CONSUMER doing `identity_json.encode("utf-8")` two files away. Reject-at-ingestion in the loader is the right fix precisely because every consumer assumes a loaded string is safe to encode. When a reviewer reports "X crashes at file B line N," check whether the defect is that file A's loader let the hostile value through.

## 4. A `const`-pinned schema field falsifies a "hardcode overrides the config knob" bug

Before filing "hardcoded literal X silently ignores/overrides the profile knob," **read the config field's SCHEMA.** If the field is `{"const": "high"}` (or a single-value enum), the value you expected is not even LEGAL — the hardcode is CONSISTENT with the schema and **correct-by-design**; any "downgrade" the run observed came from the run hand-editing its own config off-schema, not a plugin defect. A "hardcode ignores field X" hypothesis requires first proving the schema/type of X actually PERMITS the value you expected. If it's a `const`, the legitimate finding is an ENHANCEMENT ("make X a real tunable"), never a bug. (Real instance: `engine.effort` = `{"const":"high"}` made a hardcoded `"Effort: high."` correct, not a downgrade of `xhigh`.)

## 5. Dep-free single-field extraction from a YAML/config file

Node stdlib has no YAML parser but a skill must read one key (e.g. `profile_version:`) from a user's `profile.yml` to reject an unsupported version. Hand-rolling a full parser is out (a silent mis-parse is the exact false-green forbidden). YAML is HARDER than JSON: whitespace/line-terminator rules differ from a real parser's.

**The trap: a denylist line-scan gets broken forever.** A scan specified as "reject these bad shapes" was defeated by a NEW shape every round. The forbidden outcome is silently returning `ok` with a version a real parser reads DIFFERENTLY. Counterexample CLASSES (each reproduced vs `ruby -ryaml`):
1. **Multi-document** — `---\n{ profile_version: 2 }\n---\nprofile_version: 1` → first doc declares **2**; scan sees one col-0 hit → `ok, 1`.
2. **Quoted duplicate key** — `profile_version: 1\n"profile_version": 2` → a quoted key is the SAME key, later wins → **2**; scan sees one unquoted hit → `ok, 1`.
3. **Escaped quoted key** — `"profile_version": 2` → the YAML escape decodes to `_`; no regex enumerating quote styles catches it without decoding YAML escapes.
4. **Trailing invalid YAML** — `profile_version: 1\n:\n` → scan `ok`; Ruby raises `Psych::SyntaxError`.
5. **Indented plain-scalar continuation** — `profile_version: 1\n  trailing` → real value is the STRING `"1 trailing"`.
6. **Blank-line continuation** — `profile_version: 1\n\n  2` → real value is the STRING `"1\n2"`.
7. **Orphan indent before the first key** — `  code: de\nprofile_version: 1` → not a top-level block mapping; Ruby rejects.
8. **Tab in block indentation** (found by adversarial review, not fuzz) — a tab in leading whitespace of any nested/blank line → YAML forbids tabs in block indentation so Ruby raises for the WHOLE document, but a col-0 scan skips indented lines and returns `ok, 1`.

**The fix: allowlist the WHOLE top level, fail closed on everything else.** The only path to `ok` is a file where **every column-0 line is blank / a `#` comment / a snake_case block key**, with: no indented content before the first key; the first top-level key is the target; exactly one such key; its value an unquoted integer on the same line with no continuation. Two sub-rules each cost a round:
- **JS `\s` and `String#trim()` ≠ YAML whitespace.** They include U+00A0 (NBSP), U+FEFF (BOM), U+3000; YAML whitespace is **space and tab ONLY**. Use `[ \t]` explicitly everywhere: blank `/^[ \t]*$/`, comment `/^[ \t]*#/`, comment-separator `/[ \t]+#.*$/`, ASCII-only trim. Any Unicode space surviving into the value then fails `^\d+$` → halt. (`\d` in JS is already ASCII-only — good.)
- **`\r` / U+0085 / U+2028 / U+2029 are line terminators to a real parser but NOT to a `\n`-only splitter.** After normalizing `\r\n`→`\n`, FAIL CLOSED on any remaining `[\r … ]` (CR, U+0085, U+2028, U+2029) plus C0 controls, **before splitting**.
- **Tabs in leading whitespace are illegal YAML indentation — reject DOCUMENT-WIDE, not just col-0** (the allowlist only inspects col-0 lines, so a tab indenting a nested line slips through). Add `if (lines.some(l => /^[ \t]*\t/.test(l))) → halt` right after the line-terminator check. `/^[ \t]*\t/` = a tab anywhere in the contiguous leading-whitespace run; it does NOT match `profile_version:\t1` (a tab as the colon→value separator is legal), so that stays `ok`. **Caveat (#126) — this blanket reject is sound ONLY for the pure col-0 allowlist reader** (a valid allowlisted profile has no leading tabs at all, so document-wide rejection loses nothing). The instant you build the opacity-aware cross-line guard (below), a leading-ws tab becomes legal CONTENT inside an open quote/flow region (any column) or a block/plain region with ≥1 leading SPACE — so there you must gate the opacity skip on `line[0] === ' '` and run the tab check AFTER `structuralScan` so it can read that opacity; only a col-0 `\t` is always structural → halt.

**The honest invariant (never claim "impossible for all YAML").** State a threat model, not an absolute: "this is a pre-flight version READER, not a YAML validator; it returns `ok, N` only for the allowlisted shape, and never returns `ok` for a file whose target-key a real parser reads as anything but the integer N; it does not claim to detect every invalid YAML." Note the model out loud: the profile is a **trusted first-party artifact** — this guards AUTHOR ERROR, not an attacker (whoever can write the profile can already write anything the skill acts on). Keep two invariants distinct: *"detects all invalid YAML"* is OUT of scope (never claimed); *"never returns a WRONG version"* STILL HOLDS and is load-bearing.

**The method that settles it: differential test + audit the harness.** Implement the spec's steps verbatim, run BOTH the scan and `ruby -ryaml` (ships on macOS) over hand-fixtures (one per counterexample) AND an adversarial fuzz corpus, and diff. Invariant under test (encode the WEAKER negative form): whenever the scan says `ok, N`, the parser does NOT read a Hash whose `profile_version` is an Integer ≠ N. Do NOT encode the over-strong `ok ⇒ Psych.load succeeds with N` — it is UNSATISFIABLE, because the reader deliberately returns `ok` on parse-invalid input (e.g. `a: [1,2}`) and a parser SyntaxError is never a violation (the scan is deliberately stricter, not parser-equivalent). Then **audit the harness against the spec** (the first "0 violations" proof used `.trim()`/`\s` where the spec said ASCII-only → it *proved the wrong program*; and an ASCII-only fuzz alphabet could never generate the breaking inputs). Fuzz with the adversarial CLASSES (BOM, NBSP, CR, LS/PS), not just ASCII. **Adversarial review is the backstop the fuzz can't be:** a green differential test (thousands of unicode-aware inputs) still missed the tab-in-indentation class the corpus never generated — a reviewer asking "what YAML rule is a col-0 line-scan structurally BLIND to?" finds it. Run BOTH.

**A deferred instance signals a CLASS — state the invariant, don't enumerate/patch.** Root cause of the whole tail (unterminated flow/quote, invalid dedent, undefined/forward `*alias`, …) is one thing: the scan does **no cross-line structural validation**. Broaden the boundary note to the invariant ("no cross-line structural validation beyond the documented column-0 top-level shape") so any further same-class instance is already inside the boundary, and fold every instance into ONE tracking issue. Escalate fix-vs-defer of a shipped-example-reachable gap to the owner.

**If you BUILD the deferred cross-line guard, the hard problem INVERTS to "NEVER false-reject a VALID profile"** (blocking a valid profile is strictly worse than the visible-one-step-later bug). Plan review CANNOT prove false-reject-freeness for a hand-rolled cross-line YAML lexer — only a large differential fuzz vs `ruby -ryaml` can; once the architecture is stable, STOP looping plan-review and shift to build-with-fuzz-gate. The provably-safe design is **OPACITY-FIRST + INLINE-ONLY**: never classify anything inside an open plain/block/quote/flow region (skip it as opaque); classify structural tokens ONLY inline, on the same line as their real introducer; own-line value nodes become accepted false-NEGATIVES. Flow depth is a **FLOORED UNTYPED counter** (a stray closer only decrements — dropping the typed-mismatch flag removes a whole false-reject class). Alias detection is gated on a **zero-`&` document** (any anchor present ⇒ bail). Block-scalar invalid-dedent detection had to be **CUT** — block scalars become pure opacity; a real dedent guard reintroduces the mini-parser mis-parse risk (net-negative). Two corners a fuzz corpus still missed, both found by adversarial reviewers: libyaml starts a comment at a `#` **immediately after** a flow closer `]`/`}` with NO leading space, valid **only when flow depth returns to 0** (fix = `if (flowDepth === 0) return …` the instant depth hits 0 — monotonic-safe, only ever FEWER flags); and an anchor gate `includes('&')` disables the alias mechanism for any prose `&` (`R&D`) — narrow it to `/(?:^|[^\w])&/` (a real anchor introducer is ALWAYS token-start, so excluding a word-preceded `&` never misses one). General rule: it's safe to shrink a fail-safe over-approximation as long as every removed case is PROVABLY not a real hit. Note `Psych.load` NEVER RAISES on a multi-document stream — it silently returns the FIRST doc, so a multi-doc oracle must count meaningful docs via `Psych.parse_stream(s).children`, not "load succeeded."
