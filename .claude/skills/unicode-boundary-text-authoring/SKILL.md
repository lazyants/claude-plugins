---
name: unicode-boundary-text-authoring
description: Use when writing about, testing for, or embedding a Unicode boundary/invisible character — U+2028 LINE SEPARATOR, U+2029, U+0085 NEL, zero-width joiners, combining marks, NFC/NFD normalization variants — in plan text, prompts, test fixtures, or patch/Edit old_string/new_string arguments. Covers how the literal invisible character silently gets pasted in place of its escape spelling, how to author it safely with chr(), how to byte-scan for it, and how a scrub script or an Edit anchor can be corrupted by it.
---

# Unicode boundary/invisible-char authoring discipline

Describing or embedding a Unicode boundary/control character in a plan, prompt, test, or patch script is a **recurring, self-reinforcing trap**: the literal invisible/near-invisible character looks identical to its escaped spelling on skim, so it slips past a normal read-through — even inside the same paragraph that is explicitly warning against it. Confirmed four independent times (see `references/history-and-recurrence.md` for the exact sessions/dates): twice in one session by two different authors, once as an escape sequence that itself degraded into a literal char and then a plain space, once as a scrub script that silently corrupted 11,053 characters in one file, and once as a normalization-variant pair that broke an `Edit` `old_string` match.

## The rule, front-loaded

1. **Author with `chr(0x2028)` (a runtime function call) as the DEFAULT, not a fallback.** A `"\u2028"`-style string-literal escape is NOT safe — in this history it has silently become a literal character once and a plain space once, in the same session. `chr()` is pure ASCII in the source and can't be mis-typed or auto-converted into the character itself.
2. **Never type or paste the literal glyph anywhere it will be saved** — not in prose, not inside a markdown code block (markdown does not protect the raw bytes), not in an `Edit` `old_string`/`new_string`, not in an agent/teammate prompt. Spell the code point in words in prompts ("backslash-u-2-0-2-8") instead of pasting the escape spelling.
3. **A source byte-scan is necessary but NOT sufficient.** Scan raw bytes (not `str.splitlines()`, which itself splits on U+2028/U+2029 and is blind to the very character you're hunting), and also assert the character's presence/count at the RUNTIME-string level, since with `chr()` the character only exists after evaluation.
4. **A scrub/detection script is the biggest blast radius** — if its replace *argument* is a mis-pasted literal, it silently rewrites every matching character file-wide (observed: every space in an 83KB file). Build scrub scripts with `chr()` in the argument too, and verify with an independent signal (grep a known phrase, `wc -c` delta) after running it.
5. **A normalization-variant pair (NFC vs NFD) in a doc line breaks `Edit` old_string matching**, not just tests. Anchor edits on an ASCII-only neighbor line, or describe the pair symbolically (name the code points) instead of pasting the literal glyphs.

Read **`references/history-and-recurrence.md`** for the exact commands (byte-scan one-liners, the runtime assert pattern), the scrub-script corruption/recovery story, the Edit-anchoring incident, and the dated history behind each rule above — read it before writing a byte-scan command from scratch or before touching a scrub script over content that may contain these characters.
