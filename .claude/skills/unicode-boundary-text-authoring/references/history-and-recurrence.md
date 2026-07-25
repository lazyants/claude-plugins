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

## Related

The 2026-07-14 hit arose inside a plan section that was itself a red-before-green regression-test example for a different Unicode-splitting bug (#98) — a case of the bug-under-test's subject matter contaminating the prose describing it. When authoring any red-before-green test whose SUBJECT is a Unicode-boundary bug, expect the same contamination risk in the surrounding prose, not just the test body.
