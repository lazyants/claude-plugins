# History, exact commands, and recurrence

Four independent hits across two plugins and three sessions, each one hardening the prescription further. Read the dated sections below in order — later ones supersede/extend earlier ones (e.g. the 2026-07-14 hardening supersedes the "string-literal escape is fine" reading of the first hit).

## 2026-07-10 (literary-translator 1.3.1) — first hit, twice in one session

Writing prose or code that describes a specific Unicode line-boundary/control character (U+2028 LINE SEPARATOR, U+2029, U+0085 NEL, etc.) — in a plan document, an agent prompt, or test source — it is very easy to have the editor/LLM-output pipeline render the actual character inline instead of the intended escape-sequence text. The result LOOKS identical to the escaped form when skimmed (both are invisible or near-invisible), so it passes a normal read-through, but it's byte-for-byte the wrong thing.

It happened TWICE, independently, in the same session — once by the lead while writing a plan file's example test code, and once by a teammate composing the actual test file from that plan. Both times the mistake was the exact anti-pattern the surrounding text was explicitly warning against ("embed the special char via a Python escape, never a literal character in source") — writing the warning didn't prevent making the mistake one paragraph later. Two independent occurrences of the identical slip in one session is a strong signal this is a systemic authoring hazard around Unicode-boundary bugs specifically, not a one-off typo.

**How it was caught (both times):** not by reading the text, but by an explicit byte-level scan — `repr()` of each line, or `any(ord(c) > 126 for c in line)` across the whole file — which makes an invisible literal character visible as `\u2028` in the repr output.

Rules established at this point:
- When a plan, prompt, or test needs to reference a specific non-ASCII/control character, write it as an explicit escape or `chr(0x2028)` call — never rely on typing/pasting the literal glyph, even inside a code block in a markdown plan file (markdown doesn't protect you; the raw bytes still land in the file).
- After writing any text that discusses Unicode boundary/control characters, run a byte-level self-check before trusting it:
  ```
  python3 -c "print([(i,repr(l)) for i,l in enumerate(open(path,encoding='utf-8').read().split(chr(10))) if any(ord(c)>126 for c in l)])"
  ```
  Expect only KNOWN legitimate non-ASCII (e.g. accented prose, `§` section references) — flag anything else.
- `chr(0x2028)` (a runtime function call) is more robust than a `"\u2028"` string-literal escape when there's any risk of the escape itself being mis-typed or auto-converted by a tool in the pipeline — it can't silently become the literal character no matter what re-serializes the surrounding text.
- If reviewing a plan/prompt that discusses such characters (codex-rescue or a human), this is a cheap, high-value thing to explicitly check for — a normal reading pass will not catch it.

## 2026-07-14 update (littrans 1.4.6, #188) — the ESCAPE also degrades; source-scan alone is insufficient

Authoring red-before-green tests for a U+2028 line-count bug, the separator was mangled THREE times in one session despite all the warnings above: the plan leaked a literal U+2028 twice, then a byte-scrub of the plan replaced it with a plain SPACE — which PASSES a "zero exotic bytes" scan yet makes the test VACUOUS (`"alpha beta".splitlines()` is already length 1 → green-before-green; only codex caught it).

Hardened prescription:
- Use `chr(0x2028)` (pure-ASCII source) as the DEFAULT, not a fallback — a `"\u2028"`-style string-literal escape is NOT safe here; it silently became a literal char once and a space once in this one session.
- A SOURCE byte-scan (zero `e2 80 a8`) is NECESSARY BUT NOT SUFFICIENT — with `chr(0x2028)` the char lives only in the evaluated runtime string, so ALSO assert it in the test:
  ```
  SEP.encode("utf-8") == bytes((0xE2,0x80,0xA8))
  fixture.count(SEP) == 1
  ```
  The runtime assert is what catches the "silently became a space" degradation a source-only scan passes.
- Scan RAW BYTES for `b"\xe2\x80\xa8"` — NOT via `str.splitlines()`, which itself SPLITS on U+2028 and is blind to the very char you're hunting (an earlier scan in this same session used `splitlines()` and reported 0 falsely).

## 2026-07-16 update (littrans #215 plan) — a normalization-variant char breaks `Edit` `old_string` matching too

Not just tests — a plan bullet describing an NFC-bypass test carried an intentional example pair: a composed e-acute (U+00E9) and a decomposed e-acute (U+0065 U+0301). A later `Edit` whose `old_string` copied that line FAILED to match; the tool's own message noted it also tried swapping `\uXXXX` escapes and neither form matched. Same root (a Unicode bug's subject matter leaking exotic bytes into the doc), new blast radius: it silently breaks string-replace on that doc.

Fix: when a plan/doc line must contain a normalization-variant or exotic char, ANCHOR the `Edit` on an ASCII-only neighbor line/substring, never on the exotic line itself — or describe the pair symbolically (name the code points, as here) instead of pasting the literal glyphs.

## 2026-07-17 update (enduser-handbook #19 plan loop) — the SCRUB SCRIPT itself is the biggest blast radius yet

Third independent session hit, with a new catastrophic twist: a byte-scrub whose replace TARGET was a pasted "literal U+2028" in the `python3 -c` command silently became a plain SPACE in transit — so `t.replace(' ', '\\u2028')` rewrote EVERY SPACE in an 83KB plan file (11,053 of them) into the 6-char escape sequence.

Tells that caught it: a grep for known text ("Rev 29") suddenly failing, and a ~58KB file-size jump.

Recovery was lossless ONLY because the mapping was bijective (every corrupted token had one source form): reverse the replace, then re-repair the handful of legitimate escape spellings from their known contexts, then fix the one site a word-order difference caused the context-replace to miss.

Hardened prescription on top of the above:
- The chr()-only rule applies to the SCRUB/DETECTION script too — `t.replace(chr(0x2028), …)`, never a pasted literal as the replace ARGUMENT. A scrub with a mangled argument is a file-wide corruption engine, not a no-op.
- After ANY scrub, verify with an independent signal: re-grep a known phrase + `wc -c` (size should change by ~N×5 bytes for N replacements, not by tens of KB) — the "remaining: 0" self-report of the scrub can lie about the wrong thing having been replaced.
- The trap also fires in EDIT-TOOL strings and AGENT PROMPTS (two more hits in this same session): writing the escape spelling inside an `Edit` old_string/new_string or a teammate brief injects literals again. For file edits that must contain these spellings, go through python-with-`chr()` heredocs exclusively; for prompts, spell the code points in words ("backslash-u-2-0-2-8").

## 2026-08-18 hit (literary-translator #587, PR #593) — `\b` fails on a punctuation-edged pattern, in BOTH directions

A different sense of "boundary" from the rest of this file: not an invisible/control character, but the regex assertion `\b`, appended to a pattern built from `re.escape(name + ".")` to detect a name mention. `\b` is asserted against the *pattern's own* edge character, not against "the text has a word break here":

```python
re.compile(re.escape("R.") + r"\b").search("R.Smith")             # MATCHES  -- wrong, mid-word
re.compile(re.escape("R.") + r"\b").search("Written by R. Noson")  # None     -- wrong, the real mention
```

After `R.` the next character is a space: `.` and ` ` are both non-word, so there is no boundary there and the pattern never fires — while `R.Smith` has `.` followed by `S`, a word char, which IS a boundary, so it wrongly matches mid-word. **No test built from clean-edged Latin names caught either error**, because the bug requires the pattern's own edge to be punctuation; ordinary alphabetic-edged fixtures pass regardless of which form (`\b` or the adjacent-character check) is used.

Fix: replace `\b` with an explicit adjacent-character check, independent of the pattern's own edges:

```python
not ((start > 0 and text[start - 1].isalnum()) or (end < len(text) and text[end].isalnum()))
```

`str.isalnum()` beats `\w` twice over: `\w` counts `_`, and `isalnum()` is script-agnostic with no branch needed (Hebrew, Cyrillic, Devanagari letters are all `isalnum()`). Neither covers combining marks (category `M*`) or format characters (category `Cf` — ZWJ/ZWNJ, soft hyphen, RLM/LRM); marks always attach backwards so refusing on them is safe, but bidi marks legitimately sit *beside* a name in RTL prose, so refusing there would be a false refusal — the two halves need separate decisions.

**Cost of the miss:** the false half of the original rationale ("both forms still admit `R. Noson`") shipped in three copies before it was caught — a code comment, a test docstring, and a release note — and was caught by a closing review pass, not by any test, because the test SUITE pinned the true (correct) behaviour all along; the false claim lived only in prose describing it. See `project-lt-587-pr593` in this repo's session memory for the shipped fix.

## 2026-08-18 hit (literary-translator #586, PR #594 → PR #597) — a mark-counting cap measured the wrong layer, and a "writable at all" claim had a known hole

Two more lessons from the same release day, both from `sanitize_filename_component` (the LT filename sanitizer, #586/#592). Source: `project-lt-586-pr594.md` (1.31.0 shipped PR #594, then 1.31.1 shipped PR #597 fixing 1.31.0's own new guard).

**Part 1 — a guard written against a measured filesystem refusal must count what the filesystem counts.** `_MAX_MARKS_PER_BASE` counted combining marks AS WRITTEN in the source string; the kernel's EILSEQ refusal counts marks AFTER canonical (NFD) decomposition. The two diverge two ways:
- 59 code points in categories Mn/Mc/Me EXPAND into multiple marks under NFD (e.g. U+0344, U+0F73, U+0CCB…).
- A precomposed BASE letter can itself already carry marks: U+1EBF decomposes to a letter plus 2 combining marks.

So `"A" + 16×U+0344` is a run of 16 marks by the shipped as-written count, but 32 after NFD — past a cap of 30, into the exact EILSEQ the constant's own comment claimed to prevent. One-line form: **"I measured the threshold and then counted a different quantity."** This generalizes past marks-counting specifically: any cap measured against a filesystem, kernel, or protocol must count the units THAT LAYER counts, after whatever normalization it applies — not the units visible in source.

Measured platform facts behind the cap (macOS, this filesystem):
- **31 marks writes, 32 fails with EILSEQ — regardless of byte length** (a byte-length cap does not cover this failure mode at all).
- **macOS accepts `A` + 30 marks + CGJ (COMBINING GRAPHEME JOINER) + 30 marks** — so a raw-mark cap of 30 OVER-catches that shape: it refuses a string the filesystem actually accepts.
- UAX #15's Stream-Safe Text Format bound counts **NFKD non-starters**, not raw marks, and treats CGJ as a break point. The cap's rationale was first written by citing Stream-Safe directly — that citation was wrong (a third, still-different quantity) and had to be replaced by the MEASURED filesystem predicate (31 writes / 32 EILSEQs), not by a corrected Unicode-spec derivation.
- The pathological test fixtures for this cap shared the SAME blind spot as the bug: both used U+0301, which does NOT expand under NFD, so the tests passed for exactly the reason the guard was wrong. The fix moved the property assertion itself to count post-NFD, not just fixed the cap constant — an as-written assertion is green on all four new test cases while the underlying write still fails.

**Part 2 — "a property with a known hole is not a property."** This half is a release-claim discipline rule, not a Unicode fact — flagged here because the case that forced it is Unicode-specific. The same release had accepted 28 Win32 reserved device basenames (CON, PRN, AUX, NUL, COM1-9, LPT1-9) as a known, disclosed tradeoff (deferred, filed as #592) — until a reviewer pointed out the release had ALREADY claimed "writable at all" as one of the sanitizer's own stated properties. Once a property is claimed, a known-admitted class of counterexamples stops being an acceptable tradeoff and becomes a bug in the claim: check what your own release prose already asserts before accepting a gap as a tradeoff, because the tradeoff may already contradict a sentence you shipped.

Supporting facts, Unicode-specific:
- `str.isalnum()` admits six ISO-8859-1 SUPERSCRIPT alias basenames that are not plain ASCII: `COM¹`, `COM²`, `COM³`, `LPT¹`, `LPT²`, `LPT³` (superscript 1/2/3 = U+00B9/U+00B2/U+00B3) — Windows treats these as equivalent to the ASCII device names.
- The device-name reservation SURVIVES an extension (`COM1.txt` is still reserved).
- Microsoft's own documentation example is itself wrong: it lists `com³.md` as a reserved-name collision, but this codebase's `.md`-suffix rule dissolves that trailing superscript-3/dot pairing FIRST, so `com³_md` (post-sanitize) is not actually a device path — two test cases pin that the sanitizer must NOT re-fire on that shape.

## Related

The 2026-07-14 hit arose inside a plan section that was itself a red-before-green regression-test example for a different Unicode-splitting bug (#98) — a case of the bug-under-test's subject matter contaminating the prose describing it. When authoring any red-before-green test whose SUBJECT is a Unicode-boundary bug, expect the same contamination risk in the surrounding prose, not just the test body.
