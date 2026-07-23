#!/usr/bin/env bash
# Structural gate for the v1.0.5 reference assets + the wording contracts they coordinate with.
#
# These are HARD gates on the SHAPED invariants that prose review keeps missing:
#   - the surface-enumeration reference impl is unit-testable (no $$eval) and not text-gated;
#   - the capture guard is installed at context level, fails closed, has ONE read escape ('read')
#     + one benign-telemetry escape ('benign'), and orders its branches
#     deny < classify-benign < eventsource < beacon < classify-read < get-head < fail-closed;
#   - the example spec installs the guard before the first page and blocks service workers;
#   - the revalidation carve-out is the PRECISE one (not the broad "skips the HALT");
#   - the normative-vs-reference one-liner appears in every decision-5 touch point + asset header;
#   - glossaryTerms was renamed to glossary_terms everywhere.
#
# It also runs the executable unit test (node:test) when node is on PATH, and an OPTIONAL local
# TypeScript syntax check when a LOCAL transpiler exists (never a network-fetching `npx -y`).
#
# Greps are evadeable, so the *extraction* regression is hard-gated by control-inventory.test.mjs
# (node:test), not by grep. The selector-coverage grep here is a labeled heuristic.

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../skills/enduser-handbook" && pwd)"
PLUGIN_DIR="$(cd "$TEST_DIR/.." && pwd)"

ASSETS="$SKILL_DIR/assets"
REFS="$SKILL_DIR/references"

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf "  ok    %s\n" "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf "  FAIL  %s\n" "$1" >&2; }

# Assert a fixed string IS present in a file.
has() {
  local msg="$1" needle="$2" file="$3"
  if grep -qF -- "$needle" "$file" 2>/dev/null; then ok "$msg"; else bad "$msg ('$needle' not in $(basename "$file"))"; fi
}

# Assert a fixed string is NOT present in a file.
hasnt() {
  local msg="$1" needle="$2" file="$3"
  if ! grep -qF -- "$needle" "$file" 2>/dev/null; then ok "$msg"; else bad "$msg ('$needle' unexpectedly in $(basename "$file"))"; fi
}

# Assert a fixed string is present case-insensitively.
has_ci() {
  local msg="$1" needle="$2" file="$3"
  if grep -qiF -- "$needle" "$file" 2>/dev/null; then ok "$msg"; else bad "$msg ('$needle' not in $(basename "$file"))"; fi
}

# Assert a fixed string IS present within one Markdown section (the exact heading line up to,
# but not including, the next heading of the same or shallower level). `has`/`hasnt` above are
# whole-file greps and cannot prove a claim is section-bound; this can. One-pass awk, fixed-string
# index() matching (not a regex), so a needle containing regex metacharacters still matches
# literally. An absent heading is a hard failure, never a silent pass. Do NOT pipe awk into
# `grep -q` — the script runs under `set -o pipefail` (:20) and an early `grep -q` exit would
# surface as a false SIGPIPE failure here.
#
# Inert Markdown does NOT count as proof: a fenced code block (``` or ~~~) can contain the needle
# text without it being live prose — that is the documentation equivalent of the EMBED_FORMULA
# false-green this test file exists to kill, so fence content is skipped. A fence opener is <=3
# leading spaces (4+ is an indented code block, not a fence — CommonMark) then a run of 3+ of the
# SAME character (` or ~); the matching closer must use that same character and be at least as
# long, so a short `~~~` never closes a `~~~~` fence and vice versa. Char + length are tracked, not
# a bare on/off flag. A backtick fence's info string may not itself contain a backtick (CommonMark)
# — a line like ```` ```lang`x ```` is ordinary text, not an opener; tildes carry no such rule, so a
# clean info string like ```` ```js ```` still opens normally. CRLF line endings are normalized
# before both the heading compare and the needle scan. An empty needle is a hard failure, never a
# silent pass.
#
# Residual, deliberately not handled: HTML comments are NOT tracked, so a needle that appears only
# inside a `<!-- ... -->` (in or out of a fence) still counts as found, and a needle sitting inside
# a 4-space-indented code block is treated as live text. Every current caller's needle sits in plain
# prose, so neither gap bites today. A prior revision tracked comments too; two rounds of codex
# review found real regressions in that state machine (a fence opener inside an open comment, a
# second comment span on one line) faster than they could be fixed soundly. If a caller ever needs
# comment-awareness, reach for a real Markdown parser instead of extending this awk state machine.
#
# Internal: exit 0 iff `needle` is found within `heading`'s section of `file`, exit 1 otherwise (no
# ok/bad side effects). Shared by has_in_section and hasnt_in_section below, so the fence/CRLF/
# empty-needle engine lives in exactly one place rather than as two copies that could drift.
# Round 4 built this exact split (`_section_contains` + both callers) and reverted it — not because
# the split was wrong, but because there was no real caller for hasnt_in_section yet and it landed
# mid-round at a convergence point that didn't want the extra surface; the write-up explicitly
# named "the next time a real assertion needs the narrower claim" as the adoption condition. This
# is that caller — the section-boundary self-tests below are inherently negative claims ("must NOT
# be found"), and the engine is now large enough (fence char + length + backtick-info-string +
# CRLF) that a hand-duplicated second copy is a real drift risk, not just a style preference.
_section_contains() {
  local file="$1" heading="$2" needle="$3"
  awk -v heading="$heading" -v needle="$needle" '
       function leading_spaces(s,    i, n) {
         n = length(s); i = 1
         while (i <= n && substr(s, i, 1) == " ") i++
         return i - 1
       }
       function count_run(s, ch,   i, n) {
         n = length(s); i = 1
         while (i <= n && substr(s, i, 1) == ch) i++
         return i - 1
       }
       function blank_from(s, from,   i, n, c) {
         n = length(s)
         for (i = from; i <= n; i++) {
           c = substr(s, i, 1)
           if (c != " " && c != "\t") return 0
         }
         return 1
       }
       {
         raw = $0
         sub(/\r$/, "", raw)
         indent = leading_spaces(raw)
         rest = substr(raw, indent + 1)
         fc = substr(rest, 1, 1)

         # fence state, evaluated before anything else — a fence is opaque, full stop.
         if (in_fence) {
           is_close = 0
           if (indent <= 3 && fc == fence_char) {
             run = count_run(rest, fence_char)
             if (run >= fence_len && blank_from(rest, run + 1)) is_close = 1
           }
           if (is_close) { in_fence = 0 }
           next
         }
         if (indent <= 3 && (fc == "`" || fc == "~")) {
           run = count_run(rest, fc)
           if (run >= 3) {
             info = substr(rest, run + 1)
             # CommonMark: a backtick fence info string may not itself contain a backtick
             # (tildes have no such rule) — otherwise this is ordinary text, not an opener.
             if (fc != "`" || index(info, "`") == 0) {
               in_fence = 1; fence_char = fc; fence_len = run; next
             }
           }
         }

         line = raw
         n2 = match(line, /^#+/)
         hlevel = (n2 == 1) ? RLENGTH : 0

         if (hlevel > 0 && line == heading && found_heading == 0) {
           in_section = 1; found_heading = 1; level = hlevel; next
         }
         if (in_section && hlevel > 0 && hlevel <= level) { in_section = 0 }
         if (in_section && index(line, needle) > 0) { found_needle = 1 }
       }
       END { exit (needle != "" && found_heading && found_needle) ? 0 : 1 }
     ' "$file"
}

has_in_section() {
  local msg="$1" file="$2" heading="$3" needle="$4"
  if [ ! -f "$file" ]; then bad "$msg (file not found: $(basename "$file"))"; return; fi
  if _section_contains "$file" "$heading" "$needle"; then
    ok "$msg"
  else
    bad "$msg ('$needle' not found under heading '$heading' in $(basename "$file"))"
  fi
}

# Assert a fixed string is NOT present within one Markdown section — the boundary-proving
# counterpart to has_in_section (mirrors has/hasnt above), same shared engine. Exists specifically
# to prove the section-boundary mechanism itself (round 21): has_in_section's four prior self-tests
# all place their needle INSIDE the section under test, so none of them can catch a broken or
# deleted boundary — a boundary that never closes only WIDENS what has_in_section finds, so every
# existing positive assertion stays green regardless. Proving "this must not leak past a real
# boundary" needs a genuine negative claim; see the self-tests below for the one caller this exists
# for.
hasnt_in_section() {
  local msg="$1" file="$2" heading="$3" needle="$4"
  if [ ! -f "$file" ]; then bad "$msg (file not found: $(basename "$file"))"; return; fi
  if _section_contains "$file" "$heading" "$needle"; then
    bad "$msg ('$needle' unexpectedly found under heading '$heading' in $(basename "$file"))"
  else
    ok "$msg"
  fi
}

# Count exact occurrences of a fixed string in a file (line-based). grep exits 1 (not just non-zero
# from a real error) when the needle is simply ABSENT — the common, expected case for a not-yet-fixed
# sentinel — so the `|| true` here is load-bearing: without it, a plain assignment's exit status
# propagates the grep failure and this function silently ABORTS the whole script under `bash -e`
# before the caller ever sees "0" and reports the missing sentinel.
count_fixed() {
  local c
  c="$(grep -cF -- "$1" "$2" 2>/dev/null || true)"
  printf '%s\n' "${c:-0}"
}

# Line number of the first match of a fixed string, or empty if absent.
line_of() {
  grep -nF -- "$1" "$2" 2>/dev/null | head -n1 | cut -d: -f1
}


# Assert that a needle's own line (already resolved via line_of) sits STRICTLY BEFORE a boundary
# line (also already resolved). This is the PLACEMENT half of a §3.1/§3.2 group proof: an H2's
# scope legitimately includes its nested H3s, so has_in_section alone cannot tell "before the H3"
# from "inside the H3" — only a position comparison against the boundary heading's own line can.
# Centralized here (round-3 codex BLOCKER 1) rather than hand-duplicated per call site, which is
# exactly the copy-paste-drift risk this file's whole helper discipline exists to avoid.
assert_line_before() {
  local msg="$1" needle_line="$2" boundary_line="$3"
  if [ -n "$needle_line" ] && [ -n "$boundary_line" ] && [ "$needle_line" -lt "$boundary_line" ]; then
    ok "$msg (line=$needle_line < boundary=$boundary_line)"
  else
    bad "$msg (line=$needle_line boundary=$boundary_line)"
  fi
}

# The ONLY way to iterate a glob-derived category in this file. Expands, fails CLOSED on an empty
# category, and returns paths in the global CATEGORY_FILES. nullglob/globstar are deliberately OFF
# (:20), so a bare `for f in "$DIR"/*.x` would iterate the LITERAL pattern once and turn "covered
# nothing" into a silent pass — that shape must not appear anywhere below.
# MUST be called directly in the main shell: `bad` mutates FAIL, which is lost in ANY subshell,
# including the producer side of `< <(...)`.
CATEGORY_FILES=()
category_files() {   # $1 = human label, $2 = root dir, $3… = find predicates
  local label="$1" root="$2"; shift 2
  local f raw find_rc saw_sentinel raw_n
  CATEGORY_FILES=()
  # EVERY stage runs in the main shell and has its exit status checked. NO process substitution
  # and NO pipe anywhere in this function: both discard the producer's status, and a stage that
  # emits SOME records then fails (unreadable subtree, I/O error, disk full) would truncate the
  # category silently. `pipefail` does NOT cover process substitutions — it cannot rescue this.
  raw="$(mktemp "${TMPDIR:-/tmp}/eh-cat.XXXXXX")" \
    || { bad "category '$label' — mktemp failed; coverage is UNKNOWN"; return; }

  find "$root" "$@" -print0 > "$raw"; find_rc=$?
  [ "$find_rc" -eq 0 ] \
    || bad "category '$label' — find exited $find_rc (partial traversal); coverage is UNKNOWN"

  # Round-7 codex P1 (ped-ant): the pipeline used to be find -> sort -z -> consume, with `sort -z`
  # existing ONLY for cosmetic deterministic ordering — every gate here is COUNT/PRESENCE-based,
  # never order-sensitive, so nothing downstream cares what order CATEGORY_FILES holds paths in.
  # `sort -z` is a GNU extension some older BSD `sort` vintages lack (this plugin ships to whatever
  # macOS a user has, and this file already commits to bash-3.2-era portability), so depending on it
  # bought a real distribution risk for zero functional benefit. Removed: find -> consume is the
  # whole pipeline now, and the record-count taken below is straight off find's own output — no
  # transformer stage sits between producer and consumer to lose records, so the old
  # raw_n-vs-sorted_n conservation check (which existed solely to catch A TRANSFORMER dropping
  # records) has nothing left to guard and is gone with it. `-mindepth`/`-maxdepth` are ORDINARY
  # BSD/macOS-supported find options (not POSIX, but verified on stock macOS BSD find), not a
  # portability risk, and stay.
  raw_n="$(_nul_records "$raw")"

  # COMPLETION SENTINEL: an empty record, which `find -print0` can never emit for a real path.
  # Without it the CONSUMER is the last unchecked stage — a $raw truncated AFTER find's own check
  # makes `read` stop early, leaving a nonempty-but-partial array that the count guard below
  # happily accepts. The loop must SEE the sentinel to prove it consumed the whole stream. Appended
  # to $raw directly (there is no separate sorted file anymore); `raw_n` above was already taken
  # before this append, so it counts real records only, never the sentinel itself.
  printf '\0' >> "$raw" \
    || bad "category '$label' — could not append completion sentinel; coverage is UNKNOWN"

  # Reading from a PLAIN FILE keeps the loop in the caller's shell, so `bad` still mutates FAIL.
  saw_sentinel=0
  while IFS= read -r -d '' f; do
    if [ -z "$f" ]; then saw_sentinel=1; break; fi
    CATEGORY_FILES+=("$f")
  done < "$raw"
  [ "$saw_sentinel" -eq 1 ] \
    || bad "category '$label' — consumer never reached the completion sentinel (truncated read); coverage is UNKNOWN"
  [ "${#CATEGORY_FILES[@]}" -eq "$raw_n" ] \
    || bad "category '$label' — consumed ${#CATEGORY_FILES[@]} of $raw_n records; coverage is UNKNOWN"
  rm -f "$raw"
  [ "${#CATEGORY_FILES[@]}" -gt 0 ] \
    || bad "category '$label' matched no files — this gate covered nothing"
}

# Counts NUL-delimited records. Runs in a command substitution (a subshell) DELIBERATELY: it
# returns its answer on stdout and mutates no counter, so the subshell-loses-`bad` trap (§0.6)
# does not apply.
_nul_records() {
  local n=0 x
  while IFS= read -r -d '' x; do n=$((n+1)); done < "$1"
  printf '%s' "$n"
}

# §1.2 membership boundary — a POSITIVE allowlist, fail CLOSED: every direct child of assets/lib/
# must be a regular file named *.mjs or *.d.mts. A directory, a symlink, a dotfile, or any other
# extension `bad`s BY NAME — that is what makes the category TOTAL, not merely wide (rev-2, codex
# round-1 m3 / round-2 C3). This is deliberately SEPARATE from category_files: the two content
# loops over assets/lib (banner, fork-wording) only need to SELECT the well-formed members; this
# is the one place that also polices what does NOT belong, which no other category in this file
# needs (test files, references, assets/*.ts have no closed-membership rule).
# `[ -f "$f" ]` FOLLOWS a symlink and would wrongly pass one (codex round-3) — `[ -L "$f" ]` sees
# the link itself, never its target. Runs directly in the main shell so `bad` mutates FAIL, same
# discipline as category_files.
assert_lib_no_stragglers() {
  local dir="$1" f base raw find_rc straggler_seen=0
  raw="$(mktemp "${TMPDIR:-/tmp}/eh-cat.XXXXXX")" \
    || { bad "assets/lib membership — mktemp failed; coverage is UNKNOWN"; return; }
  find "$dir" -mindepth 1 -maxdepth 1 -print0 > "$raw"; find_rc=$?
  [ "$find_rc" -eq 0 ] \
    || bad "assets/lib membership — find exited $find_rc (partial traversal); coverage is UNKNOWN"
  while IFS= read -r -d '' f; do
    # Round-3 codex MAJOR 3a: `basename "$f"` runs through a command substitution, which STRIPS
    # trailing newlines from its captured output — so an entry literally named "x.mjs<NEWLINE>"
    # would print "x.mjs\n" and be captured as the indistinguishable "x.mjs", silently misclassified
    # as an allowed member. Check the RAW path (never stripped, since `read -d ''` only splits on
    # NUL) for an embedded newline FIRST, before basename ever runs on it.
    case "$f" in
      *$'\n'*)
        bad "assets/lib membership — an entry's path contains an embedded newline, not an allowed member (allowlist)"
        straggler_seen=1
        continue
        ;;
    esac
    base="$(basename "$f")"
    case "$base" in
      .*)
        bad "assets/lib membership — '$base' is a hidden entry, not an allowed member (allowlist)"
        straggler_seen=1
        continue
        ;;
    esac
    if [ -L "$f" ]; then
      bad "assets/lib membership — '$base' is a symlink, not an allowed member (allowlist)"
      straggler_seen=1
      continue
    fi
    if [ -d "$f" ]; then
      bad "assets/lib membership — '$base' is a directory, not an allowed member (allowlist)"
      straggler_seen=1
      continue
    fi
    # Round-3 codex MAJOR 3b: everything above rules OUT bad shapes (hidden/symlink/directory) but
    # never required a GOOD one — a FIFO, socket, or device node named e.g. "x.mjs" fell through to
    # the extension check below and was accepted BY NAME. `[ -L ]` already excluded symlinks above,
    # so `[ -f ]` here is safe (nothing left to follow) and requires an actual regular file.
    if [ ! -f "$f" ]; then
      bad "assets/lib membership — '$base' is not a regular file (fifo/socket/device?), not an allowed member (allowlist)"
      straggler_seen=1
      continue
    fi
    case "$base" in
      *.mjs|*.d.mts) : ;;   # allowed
      *)
        bad "assets/lib membership — '$base' has an extension other than .mjs/.d.mts, not an allowed member (allowlist)"
        straggler_seen=1
        ;;
    esac
  done < "$raw"
  rm -f "$raw"
  [ "$straggler_seen" -eq 0 ] \
    && ok "assets/lib membership — every direct child is an allowed *.mjs/*.d.mts regular file (no stragglers)"
}

echo "== has_in_section self-test: backtick-fence info-string boundary (round-4 regression) =="
# Permanent boundary cases for the fence-opener fix above — synthetic fixtures, not project docs.
# These two are phrased as plain has_in_section (positive, "must be found") calls via a
# heading-boundary reformulation, so no hasnt_in_section call is needed for THIS pair specifically:
# the bug under test is really about whether a REAL heading gets recognized as a section boundary,
# and that is directly, positively provable. The section-boundary self-tests further below (round
# 21) test a genuinely different claim and DO need hasnt_in_section — see there for why.
SELFTEST_DIR="$(mktemp -d)"
trap 'rm -rf "$SELFTEST_DIR"' EXIT

# codex's counterexample: a backtick run whose info string itself contains a backtick must NOT be
# treated as a fence opener — it is ordinary text. If it were wrongly treated as an opener, the
# fence would never close (no matching info-string-free closer follows), so it would swallow
# `## Other` as fence content instead of recognizing it as a real heading, and NEEDLE below would
# never be found under it either.
cat > "$SELFTEST_DIR/backtick-info-string.md" <<'EOF'
## Target
```lang`x
## Other
NEEDLE
EOF
has_in_section "self-test: backtick-in-info-string line is text, '## Other' is a real boundary" \
  "$SELFTEST_DIR/backtick-info-string.md" '## Other' 'NEEDLE'

# Positive companion — the fix must not over-correct: a clean info string (no embedded backtick)
# must still open a real fence, so a heading-lookalike INSIDE it does not steal the section from
# the needle that legitimately follows once the fence closes.
cat > "$SELFTEST_DIR/clean-info-string.md" <<'EOF'
## Assets
```js
## Not A Real Section
```
NEEDLE
## Next
EOF
has_in_section "self-test: a clean-info-string fence still opens, hides a fenced pseudo-heading" \
  "$SELFTEST_DIR/clean-info-string.md" '## Assets' 'NEEDLE'

# round-20: the closer rule (run >= fence_len, same fence_char) was proven correct in round 3's
# manual probes ("3-tilde does not close 4-tilde fence") — but that probe lived in a scratch
# harness and evaporated once the round ended. Every later round inherited a rule whose proof was
# gone, and both permanent self-tests above are backtick-only, three-backtick throughout, so
# neither exercises a length or char mismatch at the closer. Two fixtures, same positive
# heading-boundary form as the two above (a needle placed AFTER the real closer, so it is only
# live if the fence closed at the RIGHT line — no negative helper needed): one per axis, because
# each is independently droppable and neither fixture below exercises the other's axis (verified:
# the length fixture stays green under a char-check-dropped mutant and vice versa).
#
# Length axis: a same-char run shorter than the opener (``` closing ````) must NOT close it.
cat > "$SELFTEST_DIR/fence-length-mismatch.md" <<'EOF'
## Target
````
```
````
NEEDLE
## Next
EOF
has_in_section "self-test: a shorter same-char run does not close a longer fence (length axis)" \
  "$SELFTEST_DIR/fence-length-mismatch.md" '## Target' 'NEEDLE'

# Char axis: a run of the WRONG fence character, even at matching or greater length, must NOT
# close it (~~~~ closing ````).
cat > "$SELFTEST_DIR/fence-char-mismatch.md" <<'EOF'
## Target
````
~~~~
````
NEEDLE
## Next
EOF
has_in_section "self-test: a wrong-character run does not close a fence, regardless of length (char axis)" \
  "$SELFTEST_DIR/fence-char-mismatch.md" '## Target' 'NEEDLE'

# round-21 [IMPORTANT, zero prior coverage]: the section-boundary rule itself — `hlevel <= level`
# closes a section — had NO permanent casualty. All four self-tests above place their needle
# INSIDE the section under test, and every real call site is a positive assertion, so deleting the
# boundary check only WIDENS what gets scanned: every existing green assertion stays green. This
# is exactly what makes it dangerous — the W1/W6 validateGroups pins (round 9) use the IDENTICAL
# needle at both headings and rely ENTIRELY on section termination for their independence; if the
# boundary regresses, W6's copy silently satisfies the W1 pin too and that independence evaporates
# without any other gate noticing. Verified real-engine RED / boundary-deleted mutant GREEN before
# wiring, same as every other round this session.
#
# `<=` has two components (`==` and `<`), and — same lesson as round 20's fence axes, checked
# rather than assumed — one fixture cannot prove both: a same-level-only fixture doesn't
# discriminate a mutant that keeps `<` but drops `==`, and a shallower-only fixture doesn't
# discriminate the mirror mutant that keeps `==` but drops `<`. Confirmed against a reference
# engine before wiring; two fixtures, not one.
#
# Both are genuinely negative claims ("must NOT be found") — has_in_section's positive
# heading-boundary trick does not reformulate here, because the whole point is that removing the
# boundary check ONLY adds false positives, never removes a true one, so there is no positive fact
# whose presence depends on the boundary holding. This is hasnt_in_section's one caller (see its
# definition above for why it was reintroduced).
#
# Same-level case: a needle that exists ONLY under a FOLLOWING heading of the SAME level must not
# be found under the current one.
cat > "$SELFTEST_DIR/boundary-same-level.md" <<'EOF'
## Target
## Next
NEEDLE
EOF
hasnt_in_section "self-test: a same-level heading closes the section, later content does not leak in" \
  "$SELFTEST_DIR/boundary-same-level.md" '## Target' 'NEEDLE'

# Shallower-level case: a needle that exists ONLY after a SHALLOWER heading closes a deeper
# section must not be found under that deeper heading.
cat > "$SELFTEST_DIR/boundary-shallower-level.md" <<'EOF'
## Target
### Sub
## Next
NEEDLE
EOF
hasnt_in_section "self-test: a shallower heading closes a deeper section, later content does not leak in" \
  "$SELFTEST_DIR/boundary-shallower-level.md" '### Sub' 'NEEDLE'

# round-22: rounds 20-21 each found a member of one class — a rule this engine implements whose
# LOSS no permanent fixture would catch. The candidate list (four rules named by the reviewer, four
# more found walking the function line by line) turned out to close the class rather than sample
# it: every remaining branch/condition in _section_contains is now either fixture-guarded or
# provably unfalsifiable (documented inline at the two such cases below, and in the enumeration
# table in this round's report — not restated here). Each fixture below was verified against a
# reference engine — correct behaviour vs. its OWN targeted single-rule mutant, AND cross-checked
# against every OTHER new fixture's mutant — before being wired, so isolation is measured, not
# assumed (the fence-axis and boundary-`<=`-half lessons both came from assuming wrong).

# CRLF normalization (:114): a CRLF-terminated file must match identically to its LF counterpart —
# without stripping \r, `line == heading` never matches (trailing \r makes every line compare
# unequal to the LF-only heading param), so the heading is never found at all.
printf '## Target\r\nNEEDLE\r\n## Next\r\n' > "$SELFTEST_DIR/crlf.md"
has_in_section "self-test: a CRLF-terminated file matches the same as LF (CRLF normalization)" \
  "$SELFTEST_DIR/crlf.md" '## Target' 'NEEDLE'

# Fence-close indent gate (<=3 spaces, independent site from the opener's own gate below): a
# same-char, same-length run indented 4+ spaces is CommonMark indented-code, not a fence marker,
# and must not close an open fence.
printf '## Target\n```\n    ```\n```\nNEEDLE\n## Next\n' > "$SELFTEST_DIR/close-indent.md"
has_in_section "self-test: a 4-space-indented closer does not close a column-0 fence (close-indent gate)" \
  "$SELFTEST_DIR/close-indent.md" '## Target' 'NEEDLE'

# Fence-close no-trailing-content rule (blank_from): CommonMark requires a closing fence line to
# contain ONLY the fence marker, optionally followed by whitespace — trailing non-whitespace
# content after the run means the line is not a valid closer.
printf '## Target\n```\n``` extra\n```\nNEEDLE\n## Next\n' > "$SELFTEST_DIR/close-blank.md"
has_in_section "self-test: a closer run followed by non-whitespace text does not close the fence" \
  "$SELFTEST_DIR/close-blank.md" '## Target' 'NEEDLE'

# Fence-open indent gate (<=3 spaces, independent from the closer's own gate above — verified,
# neither fixture's mutant trips the other): a 4-space-indented backtick/tilde run is indented
# code, not a fence opener, so it must not swallow the content that follows it.
printf '## Target\n    ```\nNEEDLE\n## Next\n' > "$SELFTEST_DIR/open-indent.md"
has_in_section "self-test: a 4-space-indented run does not open a fence (open-indent gate)" \
  "$SELFTEST_DIR/open-indent.md" '## Target' 'NEEDLE'

# Fence-open minimum-length-3 rule: CommonMark requires at least 3 of the same character to open a
# fence — a run of 2 is ordinary text and must not swallow subsequent content.
printf '## Target\n``\nNEEDLE\n## Next\n' > "$SELFTEST_DIR/open-minlen.md"
has_in_section "self-test: a 2-character run does not open a fence (open-minimum-length rule)" \
  "$SELFTEST_DIR/open-minlen.md" '## Target' 'NEEDLE'

# First-occurrence-only heading binding (found_heading == 0): if the exact heading text appears a
# SECOND time later in the file, that second occurrence must not re-open (or extend) the section —
# it is a duplicate heading line, and by itself already closes the FIRST occurrence's section as an
# ordinary same-level boundary. Content between the second occurrence and the next heading must NOT
# be attributed to the first occurrence's section.
printf '## Target\nNEEDLE_FIRST\n## Target\nNEEDLE_SECOND\n## Next\n' > "$SELFTEST_DIR/rebind.md"
hasnt_in_section "self-test: a repeated identical heading does not re-open or extend the first section" \
  "$SELFTEST_DIR/rebind.md" '## Target' 'NEEDLE_SECOND'

# Empty-needle rejection (:151, needle != "") is orthogonal to file content, so it reuses the
# rebind fixture above rather than a dedicated file — the claim under test is about the NEEDLE
# argument, not anything in the markdown.
hasnt_in_section "self-test: an empty needle is always rejected, never a silent pass" \
  "$SELFTEST_DIR/rebind.md" '## Target' ''

# Needle matched as a literal fixed string (index()), never as a regex: 'a.b' as a regex matches
# ANY-character-between-a-and-b (so it would wrongly match "axb"); as a literal substring it must
# not, since "axb" contains no literal dot.
printf '## Target\naxb\n## Next\n' > "$SELFTEST_DIR/literal-needle.md"
hasnt_in_section "self-test: a needle containing regex metacharacters is matched literally, not as a pattern" \
  "$SELFTEST_DIR/literal-needle.md" '## Target' 'a.b'

# round-23 [IMPORTANT — the closure claim itself failed]: two rows of the round-22 enumeration were
# marked "guarded" on REASONING rather than a mutant actually run — round 23 wrote both mutants and
# both survived. "Transitively covered" and "every fixture depends on it" are themselves claims,
# and they need the same evidence as the rule they're about; asserting coverage is not
# demonstrating it. Closure criterion from here on: a row counts as guarded only when a specific
# mutant was run against a specific fixture and watched go red. The two rows below, plus two rules
# missed entirely by the round-22 walk, get that treatment now.

# Tilde as a valid fence-OPENER character (not just closer — the existing tilde fixture only ever
# exercises tildes as a CLOSER for a backtick fence). Backtick-info-string content ("lang`x") is
# included per the reviewer's suggestion: the info-string rejection rule is backtick-only by
# design, so this also proves that rule doesn't accidentally reject a valid tilde opener.
cat > "$SELFTEST_DIR/tilde-open.md" <<'EOF'
## Target
~~~lang`x
NEEDLE
~~~
## Next
EOF
hasnt_in_section "self-test: a tilde run opens a fence just like a backtick run (tilde-opener rule)" \
  "$SELFTEST_DIR/tilde-open.md" '## Target' 'NEEDLE'

# Heading level = length of the leading `#` run: a DEEPER heading (H3 under H2) must NOT close its
# shallower parent's section. Round 22 marked this "transitively covered by the boundary fixtures"
# — wrong: both boundary fixtures (round 21) only ever exercise the CLOSING direction (a same-level
# or shallower heading correctly closing); neither proves a heading that's genuinely DEEPER stays
# inside. A mutant that hardcodes the level to a constant survives both of round 21's fixtures
# (the relative ordering it depends on happens to be preserved for THOSE two cases) and only fails
# here, where H3's real level (3) must be compared against H2's real level (2) and found greater.
cat > "$SELFTEST_DIR/h2-h3-nested.md" <<'EOF'
## Target
### Sub
NEEDLE
## Next
EOF
has_in_section "self-test: a deeper (H3) heading does not close its shallower (H2) parent section" \
  "$SELFTEST_DIR/h2-h3-nested.md" '## Target' 'NEEDLE'

# Heading match must be EXACT text equality, not a prefix match. Round 22 marked this "guarded —
# every fixture depends on it" — true and irrelevant: every existing fixture's heading and file
# both happen to match exactly, so a prefix-match mutant satisfies every one of them too. A decoy
# heading that only SHARES A PREFIX with the query proves the distinction: under exact match it
# never opens (the query heading is genuinely absent from this file); under prefix match it opens
# on the decoy and leaks needles that were never meant to be in this section.
cat > "$SELFTEST_DIR/decoy-heading.md" <<'EOF'
## Target extra
NEEDLE
## Next
EOF
hasnt_in_section "self-test: a heading that only shares a prefix with the query does not bind (exact-match rule)" \
  "$SELFTEST_DIR/decoy-heading.md" '## Target' 'NEEDLE'

# The closer's tail rule (blank_from) has two directions, and the earlier fixture (above) only
# proved the rejecting one — trailing non-whitespace text does not close. This proves the
# accepting direction the same code path also has to get right: trailing WHITESPACE (spaces after
# the fence-character run, nothing else) still closes the fence, per CommonMark. A mutant requiring
# the run to end the line with nothing else at all — not even legal trailing whitespace — survives
# the earlier rejecting-direction fixture (a run followed by real text is still correctly rejected)
# and only fails here.
# printf, not a heredoc — a heredoc's trailing whitespace on the closer line is exactly the kind
# of thing an editor or a future hand-edit silently strips, which would quietly turn this back into
# a duplicate of the plain-closer case above and stop testing the whitespace-suffix direction at
# all. printf keeps the trailing spaces explicit in the source.
printf '## Target\n```\n```   \nNEEDLE\n## Next\n' > "$SELFTEST_DIR/close-whitespace.md"
has_in_section "self-test: a closer run followed only by trailing whitespace still closes the fence" \
  "$SELFTEST_DIR/close-whitespace.md" '## Target' 'NEEDLE'

# Two rules remain unfixtured — but this time backed by an ACTUAL mutant run against an adversarial
# fixture, not merely reasoned about (the standard round 23 raised the bar to). Fixture: a heading
# opens a section, then a line of ordinary prose contains a `#` NOT at its start, then NEEDLE.
cat > "$SELFTEST_DIR/midline-hash.md" <<'EOF'
## Target
prose with a # mid-line marker
NEEDLE
## Next
EOF
# Removing the `/^#+/` anchor ALONE (regex becomes `/#+/`, still gated by `n2 == 1`): the match
# position for a mid-line `#` is never 1, so `n2 == 1` still correctly rejects it — hlevel stays 0,
# NEEDLE is unaffected. Confirmed by running this exact single-point mutant: undetected, matching
# the prediction.
#
# Removing the `n2 == 1` check ALONE (condition becomes `n2 > 0`, anchor `^` still in the regex):
# the anchored regex simply never matches a mid-line `#` at all, so `n2` is 0 either way — `n2 > 0`
# is exactly as false as `n2 == 1` was. Also confirmed undetected.
#
# Removing BOTH together (regex `/#+/`, condition `n2 > 0`): the mid-line `#` now matches, hlevel
# becomes non-zero, and the prose line is wrongly read as a same-level heading that closes the
# section early — NEEDLE is lost. This is the one combination that IS observable, confirming the
# other two are genuinely redundant rather than merely believed to be:
has_in_section "self-test: a REAL heading, not a mid-line '#', is what closes a section (anchor+match-position rule)" \
  "$SELFTEST_DIR/midline-hash.md" '## Target' 'NEEDLE'

# The `found_heading` term in the END exit expression (`needle != "" && found_heading &&
# found_needle`) has no dedicated fixture, for the same reason as before — but this time the claim
# was checked, not just argued: `found_needle` can only be set to 1 inside the `in_section`-gated
# branch, and `in_section` can only become 1 in the SAME statement that sets `found_heading = 1`,
# so `found_needle == 1` structurally implies `found_heading == 1` already. Ran a mutant dropping
# `found_heading` from the END check against every fixture above (thirteen files, including one
# querying a heading absent from its file entirely) and every single one produced the identical
# exit code with and without the term. No fixture is added because none can discriminate it — that
# absence is now evidence, not assumption.

# round-24: two more mutants survived — both STATEMENT deletions (control flow / state reset), not
# condition weakenings. Every row above catalogues a RULE this engine implements; `next` and
# `is_close = 0` are load-bearing but aren't "rules" in that frame, so a complete enumeration of
# rules was still an incomplete enumeration of mutants. That's the reason this round is where the
# hardening stops — see the boundary note after the second fixture below.

# `is_close` has file-lifetime scope in awk (no local declaration), so `is_close = 0` at the top of
# the in_fence branch is a per-line RESET, not a one-time initialization. Delete it, and a `1` a
# real closer wrote for one fenced block survives — stale — into the very next fenced block's first
# content line, where it is misread as if that line were already a valid closer, closing the second
# block one line early. That over-early-close line is itself still swallowed (the in_fence branch's
# `next` is unconditional), so a needle sitting on the second fence's OWN first line stays hidden
# either way — the fixture needs the needle on the second fence's second-or-later line, where the
# premature close has already flipped scanning back to live-prose mode. Two consecutive fenced
# blocks are required to observe this at all: a single fence never diverges, since is_close's
# implicit awk default (0) already matches what the reset would produce the first time through.
cat > "$SELFTEST_DIR/is-close-reset.md" <<'EOF'
## Target
```
alpha
```
```
first
NEEDLE
```
## Next
EOF
hasnt_in_section "self-test: closing one fence does not leave stale state that closes the very next fence early (is_close reset)" \
  "$SELFTEST_DIR/is-close-reset.md" '## Target' 'NEEDLE'

# The opener's `next` is what makes the rest of ITS OWN line — including the fence's info string —
# opaque, the same as everything between the markers. Delete it, and the opener line falls through
# to the heading/needle scan on the same pass, so a needle written INSIDE the info string (not
# separately fenced content — the very line that opens the fence) is counted as live prose instead
# of being swallowed as part of the marker line that starts the fence.
cat > "$SELFTEST_DIR/opener-next.md" <<'EOF'
## Target
```NEEDLE
opaque
```
## Next
EOF
hasnt_in_section "self-test: a needle inside the fence opener's own info string is swallowed, not scanned as prose (opener next)" \
  "$SELFTEST_DIR/opener-next.md" '## Target' 'NEEDLE'

# --- Boundary: this hardening stops here, deliberately — not because the surface is exhausted ----
#
# _section_contains has now been hardened along two axes: the 18 rules enumerated in round 22 and
# closed under round 23's "measured, not reasoned" criterion (every row names the mutant run and the
# fixture that caught it), plus these two round-24 statement-level cases — a state reset and a
# control-flow `next` — that aren't "rules" in the round-22 frame, which is exactly why a complete
# enumeration of rules still missed them.
#
# This is NOT a claim of mutation-completeness. The mutant space for a single-point edit to a
# sixty-line awk program — every statement, condition, operator, initialization, and evaluation
# order — is strictly larger than any list of guarantees a human enumeration can produce. Five
# adversarial review rounds (20-24) each found a real, previously-undetected survivor by attacking
# this one function specifically; there is no reason to expect a sixth round would come back empty.
# The residual is accepted deliberately here, not overlooked: continuing to hunt the next surviving
# mutant on the theory that eventually none will be found mistakes "no mutant found yet" for "no
# mutant exists" — those are different claims, and only the first one is true of this function.
#
# What a future round should do instead: if a SPECIFIC mutant is demonstrated to survive — someone
# writes it and watches it pass when it should fail — add its fixture, the same way this round and
# the four before it did. Do not re-open a general audit of this function on the theory that the
# closure claim might still be incomplete; it is incomplete, structurally, and chasing that
# completeness is the same receding target rounds 20-24 already walked into once.

echo "== surface-audit.playwright.ts =="
SA="$ASSETS/surface-audit.playwright.ts"
hasnt "surface-audit: does NOT use \$\$eval (extraction stays unit-testable)" '$$eval' "$SA"
has   "surface-audit: uses page.\$\$( for element handles"                    'page.$$(' "$SA"
has   "surface-audit: imports/calls extractRecord"                            'extractRecord' "$SA"
# INTERACTIVE_SELECTOR lives in control-inventory.mjs (the audit's pure logic lib) and surface-audit
# imports it for enumeration. Pin coverage on that module. Heuristic, NOT a hard gate (greps are
# evadeable; the extraction regression is gated by node:test).
CI="$ASSETS/lib/control-inventory.mjs"
has "control-inventory: surface-audit imports the shared INTERACTIVE_SELECTOR" 'INTERACTIVE_SELECTOR' "$SA"
if grep -qF '[aria-label]' "$CI" && grep -qF "[role=button]" "$CI" && grep -qF "a[download]" "$CI"; then
  ok "control-inventory: selector covers icon-only/role-button/download (heuristic)"
else
  bad "control-inventory: selector missing icon-only/role-button/download coverage (heuristic)"
fi
# ALL non-hidden native inputs in ONE matcher (submit/button/reset/image, checkbox/radio,
# text/email/…, AND date/time/range/color/file) + bare <select>/<summary>: these carry no
# aria-label/testid and must be matched by tag/type, or a destructive/mutating control gets no
# coverage row. A fixed input-type allowlist would silently omit whichever type the author forgot.
if grep -qF "input:not([type=hidden])" "$CI" && grep -qF "'select'" "$CI" && grep -qF "'summary'" "$CI"; then
  ok "control-inventory: selector covers all non-hidden inputs + select + summary"
else
  bad "control-inventory: selector missing non-hidden-input / select / summary coverage"
fi
# Auto-save / inline-edit fields (textarea, contenteditable) are a mutating side-effect class with no
# Save button — they must get a coverage row too.
if grep -qF "'textarea'" "$CI" && grep -qF "'[contenteditable]'" "$CI"; then
  ok "control-inventory: selector covers auto-save/inline-edit fields (textarea / contenteditable)"
else
  bad "control-inventory: selector missing auto-save/inline-edit fields (textarea / contenteditable)"
fi
# v1.0.6: framework glyph/icon controls are ENUMERATED (not genuine) so a <span class="btn
# glyphicon-trash"> add/delete control is not missed. All THREE matchers required.
if grep -qF "'.btn'" "$CI" && grep -qF "'[data-bs-toggle]'" "$CI" && grep -qF "'[data-toggle]'" "$CI"; then
  ok "control-inventory: selector covers framework button/toggle controls (.btn / data-bs-toggle / data-toggle)"
else
  bad "control-inventory: selector missing .btn / [data-bs-toggle] / [data-toggle]"
fi
# v1.0.6: class captured verbatim (className) + fed to the destructive heuristic. The ONLY new verbatim
# field; covered by the documented PII boundary (asserted in the disclose-docs block below).
has "control-inventory: captures class verbatim (ATTR_CLASS)"  'ATTR_CLASS' "$CI"
has "control-inventory: classifyByShape scans record.className" 'record.className' "$CI"
# value extraction must be TYPE-AWARE: keep value only for button-like inputs; a text-entry input's
# prefilled value is PII and must NOT enter the console-logged inventory.
has "control-inventory: type-aware value extraction (button-like only)" 'BUTTON_LIKE_INPUT_TYPES' "$ASSETS/lib/control-inventory.mjs"
# textContent extraction must ALSO be value-bearing-aware: textarea/select/non-button-input/
# contenteditable carry USER DATA in textContent, so it is suppressed (the real regression is gated by
# the node:test PII cases — this just stops the suppression being silently deleted).
has "control-inventory: text suppressed for value-bearing controls (isValueBearing)" 'isValueBearing' "$ASSETS/lib/control-inventory.mjs"
# Editability must be EFFECTIVE (el.isContentEditable), not the element's OWN contenteditable attribute
# — an inherited-editable child would otherwise leak its textContent (also gated by a node:test).
has "control-inventory: effective editability (isContentEditable, not getAttribute)" 'isContentEditable' "$ASSETS/lib/control-inventory.mjs"
# A <select>'s destructive option signal is preserved as a NON-PII boolean (raw options never stored),
# so blanking the option list does not silently drop a destructive control.
has "control-inventory: select destructive-option hint (hasDestructiveText)" 'hasDestructiveText' "$ASSETS/lib/control-inventory.mjs"
# Raw text is logged ONLY for a GENUINE LEAF control — genuine (button/link/input/role=control) AND a
# leaf (no genuine-control descendant). An identity-only element ([aria-label]/[data-testid]/.badge/
# [role=status] that is not a control) OR a genuine CONTAINER (a role=button/a[href] row wrapping a child
# control) has its aggregate text SUPPRESSED (row PII). Real regressions are gated by node:test.
has "control-inventory: text kept only for genuine controls (isGenuineControl)" 'isGenuineControl' "$ASSETS/lib/control-inventory.mjs"
# ...and only for LEAVES — a genuine control wrapping another control is a container (suppress). The
# descendant probe uses a NARROW genuine-control selector that EXCLUDES identity-only attrs, so an
# instrumented label span does not wrongly suppress a leaf button's label.
has "control-inventory: genuine-leaf requires no genuine descendant (hasGenuineControlDescendant)" 'hasGenuineControlDescendant' "$ASSETS/lib/control-inventory.mjs"
has "control-inventory: narrow genuine-control descendant selector (GENUINE_CONTROL_SELECTOR)" 'GENUINE_CONTROL_SELECTOR' "$ASSETS/lib/control-inventory.mjs"
# Identity labels (aria-label/title/name/href) can themselves carry PII; the chosen disposition is to
# DOCUMENT the boundary (run on seeded data, scrub in the classify pass), not add a lossy redactor.
has "surface-audit: documents the PII boundary (seeded data / scrub before commit)" 'PII BOUNDARY' "$SA"
has "completeness-gate: documents the mechanical pass PII boundary" 'PII boundary' "$REFS/completeness-gate.md"
# Scoped enumeration is composed by the pure, unit-tested buildScopedSelector (greps are evadeable —
# the comma-list PII regression + the self-match branch are gated by tests/control-inventory.test.mjs).
has "surface-audit: composes scope via buildScopedSelector (not raw interpolation)" 'buildScopedSelector' "$SA"
# The scope must be wrapped as its OWN :is() group so a comma-separated rowSelector can't leave a bare,
# non-interactive container matching (row-PII leak); the self-match branch (scope itself interactive)
# is kept (${scope}:is(...)).
has 'control-inventory: wraps the scope as its own :is() group (:is(${rowSelector}))' ':is(${rowSelector})' "$ASSETS/lib/control-inventory.mjs"
has 'control-inventory: scoped selector includes the scope element itself (${scope}:is()' '${scope}:is(' "$ASSETS/lib/control-inventory.mjs"
# The API wait must be armed BEFORE page.goto — a fast client-rendered response can resolve before a
# wait registered after goto() attaches and be missed. (armApiWait( matches only the call site, not
# the import, which lists it as `armApiWait }`.)
L_ARM="$(line_of 'armApiWait(' "$SA")"
L_GOTO="$(line_of 'page.goto(' "$SA")"
if [ -n "$L_ARM" ] && [ -n "$L_GOTO" ] && [ "$L_ARM" -lt "$L_GOTO" ]; then
  ok "surface-audit: API wait armed before goto (arm=$L_ARM < goto=$L_GOTO)"
else
  bad "surface-audit: API wait must be armed before goto (arm=$L_ARM goto=$L_GOTO)"
fi
# armApiWait must only accept a SUCCESSFUL response (a fast 4xx/5xx must not certify identity).
has "capture-helpers: armApiWait requires a successful response (res.ok())" 'res.ok()' "$ASSETS/capture-helpers.playwright.ts"

echo "== capture-helpers.playwright.ts =="
CH="$ASSETS/capture-helpers.playwright.ts"
POLICY="$ASSETS/lib/capture-guard-policy.mjs"
has   "capture-helpers: installs at context.route("            'context.route(' "$CH"
hasnt "capture-helpers: does NOT use page.route("              'page.route(' "$CH"
has   "capture-helpers: uses routeWebSocket"                   'routeWebSocket' "$CH"
has   "capture-helpers: delegates ordering to decideRoute"     'decideRoute' "$CH"
# A throw guarded by routeWebSocket availability (the upgrade-or-disable instruction).
if grep -q "routeWebSocket !== 'function'" "$CH" && grep -q "throw new Error" "$CH"; then
  ok "capture-helpers: throws when routeWebSocket is unavailable"
else
  bad "capture-helpers: missing the routeWebSocket-unavailable throw"
fi
# assertNoDangerousHits must drain a quiet period (async) so a delayed beacon is still caught.
has   "capture-helpers: assertNoDangerousHits drains a quiet period (setTimeout)" 'setTimeout' "$CH"
# ONLY classifyRequest as the read escape — no broad allowlists (checked in both guard files).
has   "capture-helpers: exposes classifyRequest read escape"   'classifyRequest' "$CH"
for f in "$CH" "$POLICY"; do
  base="$(basename "$f")"
  hasnt "guard: no writeAllowlist ($base)"  'writeAllowlist'  "$f"
  hasnt "guard: no streamAllowlist ($base)" 'streamAllowlist' "$f"
  hasnt "guard: no appOrigin ($base)"       'appOrigin'       "$f"
done

# The SEVEN guard sentinels now live in the PURE policy module (decideRoute), so reordering the actual
# decisions — not just the comments — is what fails. Each EXACTLY once, line numbers STRICTLY
# ASCENDING in the fixed order. Matching is per-sentinel exact (e.g. '// [guard:deny]'), so the header
# prose "seven // [guard:*] sentinels" is not miscounted.
echo "== capture-guard-policy.mjs guard sentinel order =="
sentinel_ok=1
for s in '// [guard:deny]' '// [guard:classify-benign]' '// [guard:eventsource]' '// [guard:beacon]' '// [guard:classify-read]' '// [guard:get-head]' '// [guard:fail-closed]'; do
  c="$(count_fixed "$s" "$POLICY")"
  if [ "$c" -ne 1 ]; then bad "guard sentinel appears exactly once: $s (found $c)"; sentinel_ok=0; else ok "guard sentinel present exactly once: $s"; fi
done
if [ "$sentinel_ok" -eq 1 ]; then
  L_DENY="$(line_of '// [guard:deny]' "$POLICY")"
  L_BENIGN="$(line_of '// [guard:classify-benign]' "$POLICY")"
  L_ES="$(line_of '// [guard:eventsource]' "$POLICY")"
  L_BEACON="$(line_of '// [guard:beacon]' "$POLICY")"
  L_READ="$(line_of '// [guard:classify-read]' "$POLICY")"
  L_GET="$(line_of '// [guard:get-head]' "$POLICY")"
  L_FC="$(line_of '// [guard:fail-closed]' "$POLICY")"
  if [ "$L_DENY" -lt "$L_BENIGN" ] && [ "$L_BENIGN" -lt "$L_ES" ] && [ "$L_ES" -lt "$L_BEACON" ] && [ "$L_BEACON" -lt "$L_READ" ] && [ "$L_READ" -lt "$L_GET" ] && [ "$L_GET" -lt "$L_FC" ]; then
    ok "guard sentinels strictly ascending: deny<classify-benign<eventsource<beacon<classify-read<get-head<fail-closed"
  else
    bad "guard sentinel order wrong: deny=$L_DENY benign=$L_BENIGN es=$L_ES beacon=$L_BEACON read=$L_READ get=$L_GET fail=$L_FC"
  fi
fi
# v1.0.6: the benign-telemetry verdict. classifyRequest gains a 'benign' return that BLOCKS the request
# (it never fires) but routes it to a SEPARATE non-dangerous ledger, so assertNoDangerousHits does not
# false-trip on dev telemetry (laravel-boost /_boost/, Sentry). No new allowlist; the order test above
# already proves classify-benign sits AFTER deny (deny still wins).
for f in "$CH" "$POLICY" "$ASSETS/lib/capture-guard-policy.d.mts"; do
  has "guard: classifyRequest admits a 'benign' verdict ($(basename "$f"))" "'benign'" "$f"
done
has "capture-helpers: separate blockedBenign ledger"                      'blockedBenign' "$CH"
has "capture-helpers: assertNoDangerousHits still gates on dangerousHits" 'dangerousHits.length' "$CH"
# The built-in dangerous-verb block must live in the deny step (finding 4).
has "capture-guard-policy: built-in dangerous-verb block (deny-dangerous-verb)" 'deny-dangerous-verb' "$POLICY"
# Percent-decode the path/query before scanning so encoded dangerous verbs cannot slip through.
has "capture-guard-policy: decodes URL before scanning (decodeURIComponent)" 'decodeURIComponent' "$POLICY"
# matchesDeny must scan the BODY too (postData), so an author can deny a body-shaped write.
has "capture-guard-policy: matchesDeny scans the request body (postData)" 'req.postData' "$POLICY"

echo "== capture-helpers safe-dismiss + leak-scan hardening =="
# dismissModal uses a safe-negative ALLOWLIST (not a destructive denylist) and refuses multi-dialog.
has "dismiss-policy: safe-negative allowlist present" 'DEFAULT_SAFE_LABELS' "$ASSETS/lib/dismiss-safe-label-policy.mjs"
has "capture-helpers: refuses when more than one dialog is open"     "getByRole('dialog').count" "$CH"
# maskAndAssert tags masked nodes and EXCLUDES them from the scan (no placeholder string-strip).
has   "capture-helpers: tags masked nodes (data-handbook-masked)"     'data-handbook-masked' "$CH"
hasnt "capture-helpers: no placeholder string-strip in the leak scan" '.split(placeholder).join' "$CH"
# maskAndAssert must pierce OPEN shadow roots for both masking and scanning.
has   "capture-helpers: pierces open shadow roots (shadowRoot)"        'shadowRoot' "$CH"
# The shadow walk must include the START node's OWN open root (a dialog that is itself a shadow host),
# not just descendants.
has   "capture-helpers: shadow walk includes start's own root (start.shadowRoot)" 'start.shadowRoot' "$CH"
# The leak scan must read ALL selected options of an unmasked <select>, not only the first.
hasnt "capture-helpers: leak-scan does not read only selectedOptions[0]" 'selectedOptions[0]' "$CH"
# A <select multiple>/[size>1] renders UNSELECTED options too — mask + scan must cover EVERY option
# (el.options), not just the selected ones, or an unselected PII label leaks.
has   "capture-helpers: masks/scans ALL select options (el.options, not just selected)" 'el.options' "$CH"
hasnt "capture-helpers: no longer masks/scans only selectedOptions" 'el.selectedOptions' "$CH"
# Route + API identity use the pathname-boundary matcher, not bare substring (no /api/users-old or
# ?next= redirect fail-open).
has   "capture-helpers: identity uses pathname-boundary matcher (urlMatchesTarget)" 'urlMatchesTarget' "$CH"
# An unmasked input's PII placeholder renders in the shot — it must be scanned AND masked.
has   "capture-helpers: scans/masks input placeholder" "getAttribute('placeholder')" "$CH"
# The spinner/loading check must count ALL visible indicators, not just .first() (a hidden decoy
# earlier in DOM order would otherwise let a later visible spinner certify a still-loading page).
has   "capture-helpers: spinner check counts visible indicators (not .first())" 'filter({ visible: true })' "$CH"
# The pure identity matcher must FAIL CLOSED on a blank route/API target (an empty target otherwise
# matches every URL via the '/' prefix).
has   "identity-match: fails closed on a blank target" 'blank target' "$ASSETS/lib/identity-match.mjs"
# v1.0.6: captureRegion gains an opt-in { maxHeight } that clamps a runaway-height region via a
# temporary CSS max-height/overflow + scrollTop reset, shot at scale:'css', restored after (NOT a
# viewport clip — the clip path is viewport-relative and breaks when maxHeight > viewport height).
has "capture-helpers: captureRegion has a maxHeight cap option"          'maxHeight' "$CH"
has "capture-helpers: captureRegion resets scrollTop for the top slice"  'scrollTop' "$CH"
has "capture-helpers: captureRegion shoots at scale css (DPR-neutral)"   "scale: 'css'" "$CH"
# v1.4.0 #18: bleed-free oversize-overlay helper — single viewport clip, animations:'disabled', and an
# atomic fail-closed publish (buffer → temp → rename; the rename guards against a rejected PNG at path).
has "capture-helpers: bleed-free oversize-overlay helper" 'export async function captureRegionClipped(' "$CH"
has "capture-helpers: verified-buffer atomic publish"     'await rename(tmp, path);'     "$CH"
# v1.4.0 #154: dismiss safe-negative gate extracted to a unit-tested lib; the .ts delegates and carries
# no inline label/verb tables, and both imports are VALUE imports (a type-only import would false-green).
has   "capture-helpers: dismiss delegates to isSafeNegativeLabel" 'if (!isSafeNegativeLabel(cancelLabel, safeLabels))' "$CH"
hasnt "capture-helpers: no inline safe-labels array"             'DEFAULT_SAFE_LABELS = ['              "$CH"
hasnt "capture-helpers: no inline unsafe-verbs set"              'UNSAFE_LEADING_VERBS = new Set'       "$CH"
has   "capture-helpers: value-imports isSafeNegativeLabel" "import { isSafeNegativeLabel } from './lib/dismiss-safe-label-policy.mjs'" "$CH"
has   "capture-helpers: value-imports clampClipToViewport" "import { clampClipToViewport } from './lib/viewport-clip.mjs'" "$CH"

echo "== capture.example.spec.ts =="
SPEC="$ASSETS/capture.example.spec.ts"
has "example spec: blocks service workers" "serviceWorkers: 'block'" "$SPEC"
L_GUARD="$(line_of 'installCaptureGuard(' "$SPEC")"
L_NEWPAGE="$(line_of 'newPage(' "$SPEC")"
if [ -n "$L_GUARD" ] && [ -n "$L_NEWPAGE" ] && [ "$L_GUARD" -lt "$L_NEWPAGE" ]; then
  ok "example spec: installCaptureGuard before newPage (guard=$L_GUARD < newPage=$L_NEWPAGE)"
else
  bad "example spec: guard must install before newPage (guard=$L_GUARD newPage=$L_NEWPAGE)"
fi
# assertNoDangerousHits must be awaited inside a finally (finding 7) and the GraphQL classifier must
# parse the body / fail closed rather than substring-match (finding 6).
has "example spec: asserts no dangerous hits in a finally" 'finally' "$SPEC"
# The GraphQL read-classifier (the guard's single allow escape-hatch) is now a pure, UNIT-TESTED
# module (graphql-read-classifier.mjs, exercised by tests/graphql-read-classifier.test.mjs), not an
# inline grep-only blob. The spec wires it via classifyGraphqlRead; parse + reject logic is gated on
# the MODULE (greps are evadeable — the real protection is the node:test).
has "example spec: wires the extracted GraphQL classifier (classifyGraphqlRead)" 'classifyGraphqlRead' "$SPEC"
GQL="$ASSETS/lib/graphql-read-classifier.mjs"
has "graphql-read-classifier: parses the body (JSON.parse)" 'JSON.parse' "$GQL"
has "graphql-read-classifier: rejects mutation/subscription" 'mutation|subscription' "$GQL"
# The endpoint must be matched by PATHNAME (new URL().pathname), NOT a full-URL substring — a substring
# admits a query-string decoy like '/collect?next=/graphql'. Negative tests gate this in node:test.
has   "graphql-read-classifier: endpoint matched by pathname (new URL)"        'new URL(' "$GQL"
hasnt "graphql-read-classifier: no full-URL substring endpoint test"           ".includes('/graphql')" "$GQL"

echo "== wording contracts (some files owned by other groups; pass once the full set lands) =="
REVAL="$REFS/revalidation.md"
SKILL="$SKILL_DIR/SKILL.md"
for f in "$REVAL" "$SKILL"; do
  base="$(basename "$f")"
  if [ ! -f "$f" ]; then bad "wording: $base does not exist yet"; continue; fi
  has    "wording: $base — 'skips only the initial accepted-manifest review'" 'skips only the initial accepted-manifest review' "$f"
  has    "wording: $base — 'newly discovered'"                                'newly discovered' "$f"
  has    "wording: $base — 'delta manifest'"                                  'delta manifest' "$f"
  has_ci "wording: $base — 'halt' (case-insensitive)"                         'halt' "$f"
done

echo "== v1.0.6 disclose docs + className PII boundary =="
has "completeness-gate: disclosure prose templates"              'Disclosure prose templates' "$REFS/completeness-gate.md"
has "completeness-gate: disclose trigger list"                   'TRIGGER LIST' "$REFS/completeness-gate.md"
has "completeness-gate: className in the PII-boundary field list" 'className' "$REFS/completeness-gate.md"
has "capture-spec-helpers: lists className in verbatim fields"    'className' "$REFS/capture-spec-helpers.md"
has "surface-audit: matrixLabel used by the surface audit"        'matrixLabel' "$SA"
# NOTE: 'record.className' alone is NOT a valid sentinel here — that bare substring already existed on
# origin/main (in classifyByShape's field list), so it is green even without matrixLabel. Pin the
# needle to the '||' suffix, which is unique to matrixLabel's fallback chain.
has "control-inventory: matrix-label fallback chain lives in the lib" 'record.className ||' "$CI"
has "control-inventory.d.mts: declares className"                 'className' "$ASSETS/lib/control-inventory.d.mts"

echo "== normative-vs-reference one-liner in every decision-5 touch point + asset header =="
NORMATIVE='non-normative reference implementation'
# Decision-5 touch points are a deliberate HAND-PICKED subset of references/ (+ SKILL.md), not a
# directory category — keep them explicit (§1.2). The two genuine categories — every assets/lib
# module (both extensions) and every top-level assets/*.ts file — are derived below via
# category_files so a future addition (like chapter-paths.mjs/.d.mts) cannot be silently omitted.
for f in \
  "$REFS/completeness-gate.md" \
  "$REFS/running-ui-source.md" \
  "$REFS/capture-safety.md" \
  "$REFS/manifest-discipline.md" \
  "$REFS/container-isolation.md" \
  "$REFS/capture-spec-helpers.md" \
  "$SKILL" \
  "$REFS/profile-validation.md" \
  "$REFS/capture-engines.md" \
  "$REFS/surface-diff.md"; do
  base="$(basename "$f")"
  if [ ! -f "$f" ]; then bad "normative banner: $base does not exist yet"; continue; fi
  has "normative banner present: $base" "$NORMATIVE" "$f"
done
assert_lib_no_stragglers "$ASSETS/lib"
category_files 'assets/lib modules (normative banner)' "$ASSETS/lib" -mindepth 1 -maxdepth 1 -type f \
  \( -name '*.mjs' -o -name '*.d.mts' \)   # UNION — both extensions, never just *.mjs (rev-13)
if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
  for f in "${CATEGORY_FILES[@]}"; do
    has "normative banner present: $(basename "$f")" "$NORMATIVE" "$f"
  done
fi
category_files 'assets/*.ts (normative banner)' "$ASSETS" -maxdepth 1 -name '*.ts'
if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
  for f in "${CATEGORY_FILES[@]}"; do
    has "normative banner present: $(basename "$f")" "$NORMATIVE" "$f"
  done
fi

echo "== glossaryTerms fully renamed to glossary_terms =="
if grep -rn 'glossaryTerms' "$SKILL_DIR" "$PLUGIN_DIR/.claude-plugin" 2>/dev/null; then
  bad "glossaryTerms still present (must be glossary_terms)"
else
  ok "no glossaryTerms residue under plugins/enduser-handbook"
fi

echo "== executable unit tests (node --test) =="
if command -v node >/dev/null 2>&1; then
  category_files 'unit test files' "$TEST_DIR" -maxdepth 1 -name '*.test.mjs'
  if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
    for t in "${CATEGORY_FILES[@]}"; do
      tbase="$(basename "$t")"
      if node --test "$t" >/dev/null 2>&1; then
        ok "$tbase passes under node --test"
      else
        bad "$tbase FAILED under node --test"
      fi
    done
  fi
else
  echo "  note  node not on PATH — skipping the executable unit tests"
fi

echo "== optional local TypeScript syntax check =="
category_files 'assets/*.ts (TypeScript check)' "$ASSETS" -maxdepth 1 -name '*.ts'
# Round-3 codex MAJOR 4: an unguarded copy of an EMPTY array is fatal under bash 3.2 `set -u` (the
# same hazard §0.6/§1.7 document for every other CATEGORY_FILES consumer) — the empty-category
# probe for THIS call (rows 10-16) would abort with "unbound variable" instead of failing through
# the parent FAIL counter, which is exactly the false-negative the zero-match guard exists to
# prevent. Length-guarded the same way every other expansion in this file is.
TS_FILES=()
[ "${#CATEGORY_FILES[@]}" -gt 0 ] && TS_FILES=("${CATEGORY_FILES[@]}")   # copied immediately — a later category_files call clobbers the global
if command -v esbuild >/dev/null 2>&1; then
  ts_ok=1
  if [ "${#TS_FILES[@]}" -gt 0 ]; then
    for f in "${TS_FILES[@]}"; do
      esbuild "$f" --bundle=false --log-level=silent --outfile=/dev/null >/dev/null 2>&1 || ts_ok=0
    done
  fi
  [ "$ts_ok" -eq 1 ] && ok "TypeScript parses under local esbuild" || bad "TypeScript syntax error under local esbuild"
elif npx --no-install esbuild --version >/dev/null 2>&1; then
  ts_ok=1
  if [ "${#TS_FILES[@]}" -gt 0 ]; then
    for f in "${TS_FILES[@]}"; do
      npx --no-install esbuild "$f" --bundle=false --log-level=silent --outfile=/dev/null >/dev/null 2>&1 || ts_ok=0
    done
  fi
  [ "$ts_ok" -eq 1 ] && ok "TypeScript parses under local npx esbuild" || bad "TypeScript syntax error under local npx esbuild"
else
  echo "  note  no local esbuild (and never network-fetching npx -y) — skipping the .ts syntax check"
fi

echo "== publish-target adapters =="
SMD="$REFS/publish-targets/static-md.md"
OMD="$REFS/publish-targets/obsidian-vault.md"
PROF="$ASSETS/handbook.profile.example.yml"

if [ -f "$SMD" ]; then ok "static-md adapter exists"; else bad "static-md adapter missing"; fi

# Exact-key bindings — each profile key the adapter must resolve, named verbatim.
has "static-md: binds publish.chapters_dir"               'publish.chapters_dir'               "$SMD"
has "static-md: binds publish.index_file"                 'publish.index_file'                 "$SMD"
has "static-md: binds publish.glossary_dir"               'publish.glossary_dir'               "$SMD"
has "static-md: binds publish.frontmatter_required"       'publish.frontmatter_required'       "$SMD"
has "static-md: binds publish.section_labels.prerequisites" 'publish.section_labels.prerequisites' "$SMD"
has "static-md: binds publish.section_labels.related"     'publish.section_labels.related'     "$SMD"
has "static-md: binds publish.wikilinks"                  'publish.wikilinks'                  "$SMD"
has "static-md: binds capture.output_dir"                 'capture.output_dir'                 "$SMD"

has_ci "static-md: adapter documents halt conditions" 'halt' "$SMD"

# Relative-link mandate: pins the general formula AND both worked path examples.
has "static-md: cross-subtree relative example" '](../'    "$SMD"
has "static-md: teaches the relative rule"       'relative' "$SMD"
# round-10 exhaustive sweep / round-11: this formula has TWO independent sites — the general-rule
# definition (:193) and the link-integrity gate's item 2 restatement (:408). Only ONE of the two is
# provable BY SECTION: :193's formula sits inside a FENCED code block (``` ... ```), and
# has_in_section deliberately treats fence content as opaque — that is the exact mechanism rounds
# 2-4 built to stop a fenced EXAMPLE from false-greening a needle, and it applies here even though
# this particular fence is load-bearing formatting, not an illustrative snippet. Reopening
# fence-visibility to fix this one site would reintroduce the false-green class those rounds closed,
# so :408 gets its own section-bounded per-site pin below and :193 does not.
#
# :193 is NOT left uncovered, though — count_fixed (:154) is a whole-file, fence-BLIND `grep -cF`,
# so it sees both lines regardless of the fence. The formula appears on exactly TWO physical lines
# in the real file (:193, :408); asserting the count is 2 catches a :193-only mutation (count drops
# to 1) with no change to fence handling. Narrower guarantee, stated honestly: this proves two
# lines carry the formula, not that the RIGHT two lines do — a mutation that corrupted :193 while
# introducing a third, unrelated copy elsewhere would hold the count at 2 and slip through. Smaller
# hole than "nothing catches :193 at all," but not zero.
has "static-md: pins the relative-path formula"  'relative(dirname(chapter_file), target_file)' "$SMD"
REL_CHAPTER_TARGET_COUNT="$(count_fixed 'relative(dirname(chapter_file), target_file)' "$SMD")"
if [ "$REL_CHAPTER_TARGET_COUNT" -eq 2 ]; then
  ok "static-md: relative-path formula appears on exactly 2 lines (:193 fenced def + :408 gate check)"
else
  bad "static-md: relative-path formula line-count drifted from 2 (found $REL_CHAPTER_TARGET_COUNT) — a fenced-site (:193) or gate-site (:408) mutation, or a legitimate new/removed occurrence needing this count updated"
fi
has_in_section "static-md: link-integrity gate item 2 uses the same relative-path formula" \
  "$SMD" '## Link-integrity gate before you publish' \
  'relative(dirname(chapter_file), target_file)'
has "static-md: documents vault-root index example" 'vault-root' "$SMD"
has "static-md: documents repo-root index example"  'repo-root'  "$SMD"
has "static-md: vault-root index path (one ../)"   '](../SUMMARY.md)'    "$SMD"
has "static-md: repo-root index path (two ../)"    '](../../SUMMARY.md)' "$SMD"

# No Obsidian leakage: never the literal wikilink symbol, never a Dataview fence.
hasnt "static-md: no wikilink symbol" '[[' "$SMD"
NEEDLE='```dataview'
hasnt "static-md: no dataview block" "$NEEDLE" "$SMD"

# Requires wikilinks: false for the static target.
has "static-md: requires wikilinks false" 'wikilinks: false' "$SMD"

# Each halt condition carries its exact quoted message (adapter contract, publish-targets/README.md:31).
has "static-md: index_file halt message"   'writable table of contents'   "$SMD"
has "static-md: chapters_dir halt message" 'cannot write chapters'        "$SMD"
has "static-md: wikilinks halt message"    'do not render on a static site' "$SMD"
has "static-md: network halt message"      'writes local files only'      "$SMD"
# Asset contract: screenshots remain at capture.output_dir (no fictional copy into chapters tree),
# and capture.output_dir must resolve under chapters_dir so the static site can serve the images.
has "static-md: assets remain at capture.output_dir"  'remain there'                    "$SMD"
has "static-md: assets must resolve under the docs tree" 'MUST resolve under chapters_dir' "$SMD"
has "static-md: capture.output_dir-under-tree halt"   'resolve under `publish.chapters_dir` so the rendered' "$SMD"

# Glossary relative-link must NOT double-prefix `../` onto <glossary-rel> — <glossary-rel> already
# equals relative(dirname(chapter), glossary_dir), so `../<glossary-rel>` over-climbs by one segment.
hasnt "static-md: no double-prefixed glossary link" '](../<glossary-rel>'        "$SMD"
has   "static-md: corrected glossary link template" '](<glossary-rel>/index.md'  "$SMD"
# round-10 exhaustive sweep, finding 3: static-md.md's own glossary-link relative() formula was
# completely unpinned — not even bare-name — the counterpart to the obsidian-vault.md glossary
# formula pinned this round under Finding 2. Pinned the same way: full call+args, section-bound.
has_in_section "static-md: glossary-link formula defines <glossary-rel> via relative(dirname(chapter_file), ...)" \
  "$SMD" '## Glossary backlink discipline' \
  '`relative(dirname(chapter_file), publish.glossary_dir)`'
# glossary_terms is an authoring/manifest field, never emitted into the minimal published frontmatter.
has "static-md: glossary_terms authoring-only" 'authoring-time only' "$SMD"
# Index wiring is two required writes PLUS a conditional glossary_seed reconciliation (not "exactly two").
has "static-md: two-writes-plus-conditional framing" 'required writes**, plus one conditional' "$SMD"
# index_file halt covers an existing-but-read-only file, not just an unwritable parent dir.
has "static-md: index_file writable-if-exists halt" 'the file itself if it already exists' "$SMD"
# A halt covers an unwritable glossary target (the adapter writes glossary_dir/index.md in index wiring).
has "static-md: glossary-writable halt" 'cannot write the glossary' "$SMD"
# wikilinks halt fires on unset too, not only explicit true (unset would default to Obsidian wikilinks-on).
has "static-md: wikilinks halt covers unset" 'or leaves it unset' "$SMD"

# SKILL filename-normalization rule + dynamic halt list (written by a sibling teammate; assert anyway).
has  "skill: filename normalization rule" 'underscores with hyphens'                 "$SKILL"
has  "skill: dynamic halt list"           'files in this directory minus README.md'  "$SKILL"
hasnt "skill: stale hardcoded halt gone"  'Available: obsidian-vault.'               "$SKILL"
# Step 0b AND W5 (SKILL) plus the publish-targets README must use the resolved (hyphenated) adapter
# name — none may reintroduce the raw un-normalized '<publish.target>.md' form, which would send
# static_md to a non-existent static_md.md.
PTREADME="$REFS/publish-targets/README.md"
hasnt "skill: no raw un-normalized adapter path"     '<publish.target>.md' "$SKILL"
hasnt "publish-targets README: no raw adapter path"  '<publish.target>.md' "$PTREADME"

# Profile honesty: the example must not over-promise a single-adapter ship.
hasnt "profile: no over-promise" 'only obsidian_vault ships' "$PROF"

echo "== obsidian-vault adapter (#153) =="
# v1.4.0 #153: chapter image embeds are DERIVED from capture.output_dir (full-target relative form —
# relative(dirname(chapter_file), join(capture.output_dir, <slug>, <file>))), replacing the hardcoded
# assets/<chapter-slug>/ prefix, and the link-integrity gate keeps the resolved target inside the vault.
# v1.5.0 #19: the join() innards became chapterAssetDir(entry) (group-aware, D3) — re-picked below.
has "obsidian-vault: embed derived from capture.output_dir" 'chapterAssetDir(entry) = join(capture.output_dir' "$OMD"
# #247 (round-2, A2): the containment gate's own wording moved from "the active Obsidian vault"
# to the derived `<vault-root>` symbol (see "## Vault root" / §2.1) — re-pointed to the new prose,
# same containment claim, verified count_fixed == 1 file-wide.
has "obsidian-vault: vault-boundary gate"                   'stay inside `<vault-root>`'                          "$OMD"

echo "== #247: <vault-root> derivation (§2.1/§2.6) =="
# The vault root is derived once per run from a SINGLE anchor (publish.chapters_dir) — there is no
# publish.vault_root profile key. Five positives pin the derivation's normative shape (selection,
# zero-marker halt, multiple-marker halt, unreadable-ancestor halt); each is novel (absent from the
# Round-0 baseline — verified against $BASELINE, not re-checked at runtime since the pre-edit tree
# is not available here) and unique file-wide.
# Round-3 codex MAJOR 5: the needle stopped BEFORE the identifier, so it proved only that SOME
# anchor is named, not WHICH one — a mutation swapping publish.chapters_dir for publish.index_file
# left this green. Extended to bind the identifier itself (A2-returned string).
has_in_section "obsidian-vault: vault-root selection names publish.chapters_dir as its one discovery anchor" \
  "$OMD" '## Vault root' \
  'The only discovery anchor is `publish.chapters_dir`'
has_in_section "obsidian-vault: vault-root selection requires a readable .obsidian/ directory" \
  "$OMD" '## Vault root' \
  'holds a readable `.obsidian/` directory'
has_in_section "obsidian-vault: vault-root zero-marker halt" \
  "$OMD" '## Vault root' \
  'No Obsidian vault found above'
has_in_section "obsidian-vault: vault-root multiple-marker halt names it an unsupported topology" \
  "$OMD" '## Vault root' \
  'this vault topology is unsupported'
has_in_section "obsidian-vault: vault-root unreadable-ancestor halt names the exact path" \
  "$OMD" '## Vault root' \
  'while walking for the vault root'
# The heading itself must be unique — a duplicated "## Vault root" would let has_in_section bind to
# the wrong (first) occurrence, silently validating nothing about the real section.
VAULT_ROOT_HEADING_COUNT="$(count_fixed '## Vault root' "$OMD")"
if [ "$VAULT_ROOT_HEADING_COUNT" -eq 1 ]; then
  ok "obsidian-vault: '## Vault root' heading appears exactly once"
else
  bad "obsidian-vault: '## Vault root' heading count drifted from 1 (found $VAULT_ROOT_HEADING_COUNT)"
fi
# capture.output_dir must NEVER participate in vault-root SELECTION (§2.1's corrected derivation —
# the rev-2 BLOCKER this whole section exists to prevent). Scoped to the H2 itself: a whole-file
# hasnt would be defeated by the term's legitimate use elsewhere (the containment check below).
hasnt_in_section "obsidian-vault: capture.output_dir plays no part in vault-root selection" \
  "$OMD" '## Vault root' \
  'capture.output_dir'
# Round-3 codex MAJOR 5: same gap on the gate side — "plays no part in selecting" alone doesn't
# say WHAT plays no part. Extended to bind capture.output_dir itself (A2-returned string).
has_in_section "obsidian-vault: link-integrity gate states capture.output_dir plays no part in vault-root selection" \
  "$OMD" '## Link integrity gate before you publish' \
  '`capture.output_dir` deliberately plays no part in selecting'
# The corrected glossary wikilink form: vault-root-relative, replacing the two wrong 1.6.0 spellings.
has_in_section "obsidian-vault: glossary wikilink target is vault-root-relative" \
  "$OMD" '## Glossary backlink discipline' \
  'relative(<vault-root>, {{publish.glossary_dir}})'
# Round-3 codex MINOR 8: mislabeled — this needle sits on the `publish.wikilinks: true` (Obsidian
# default) bullet ("see 'Glossary backlink discipline' below FOR THE EXACT TARGET", :371), not the
# wikilinks-OFF one (which just says "...below.", :387, no "for the exact target" suffix). The
# wikilinks-ON pointer is deliberately MORE DETAILED than the off one — that asymmetry (lead-
# ratified) is what makes "below for the exact target" discriminate a real cross-reference from a
# copy-pasted one, since only the wikilinks-ON copy carries the longer phrase.
has_in_section "obsidian-vault: wikilinks-ON glossary bullet points at the exact-target section" \
  "$OMD" '## Wikilinks vs Markdown links' \
  'below for the exact target'
# Retired-form check (codex round-1 M7): neither wrong 1.6.0 path-prefix spelling survives, anywhere.
hasnt "obsidian-vault: no retired basename-form glossary path prefix" 'basename}}/index#' "$OMD"
hasnt "obsidian-vault: no retired raw glossary_dir path prefix"       '{{publish.glossary_dir}}/index#' "$OMD"

echo "== #247: glossary_seed becomes conditional (§2.7) =="
# glossary_seed is set-and-readable-conditional at exactly its two operative sites (INDEX wiring
# item 2, link-integrity gate item 5) — never mandatory. count_fixed proves the third (unconditional
# 1.6.0 layout-diagram) site is gone: exactly 2 occurrences file-wide, not 3.
GLOSSARY_SEED_COUNT="$(count_fixed '{{publish.glossary_seed}}' "$OMD")"
if [ "$GLOSSARY_SEED_COUNT" -eq 2 ]; then
  ok "obsidian-vault: {{publish.glossary_seed}} occurs at exactly its 2 conditional sites"
else
  bad "obsidian-vault: {{publish.glossary_seed}} count drifted from 2 (found $GLOSSARY_SEED_COUNT) — a site was added, removed, or the unconditional layout-diagram line regressed"
fi
has_in_section "obsidian-vault: INDEX wiring item 2 names glossary_seed" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  '{{publish.glossary_seed}}'
has_in_section "obsidian-vault: INDEX wiring item 2 is skipped when the key is unset" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'Skip this item entirely when the key is unset'
# Round-3 codex MAJOR 6: the "skipped when unset" pins above prove the negative half of the
# conditional but not the POSITIVE trigger — a mutation dropping "and readable" (leaving only "is
# set") would silently widen the condition and stayed green. A2-returned pins on the exact trigger
# at both operative sites; removing "and readable" from either must now go red.
has_in_section "obsidian-vault: INDEX wiring item 2 triggers on set AND readable, not merely set" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  '`publish.glossary_seed` is set and readable'
has_in_section "obsidian-vault: link-integrity gate item 5 names glossary_seed" \
  "$OMD" '## Link integrity gate before you publish' \
  '{{publish.glossary_seed}}'
has_in_section "obsidian-vault: link-integrity gate item 5's glossary_seed half is skipped when the key is unset" \
  "$OMD" '## Link integrity gate before you publish' \
  'this half of item 5 is skipped when the key is unset'
has_in_section "obsidian-vault: link-integrity gate item 5 triggers on set AND readable, not merely set" \
  "$OMD" '## Link integrity gate before you publish' \
  '{{publish.glossary_seed}}` is set and readable'

echo "== #248: obsidian-vault flat-branch witnesses (O6) =="
# Every flat-branch witness (O6) needs BOTH novelty (absent before A2's #248 edit — verified against
# the Round-0 $BASELINE, not re-checked at runtime for the same reason as the #247 block above) and
# placement between the full-semantic branch openers, L_FLAT < L < L_GRP. The three anchors are
# FROZEN — A2 must not reword them — so each is asserted unique first, same discipline as the guard-
# sentinel-order check above: an ordering comparison over a non-unique anchor proves nothing.
L_FLAT_NEEDLE='**Flat entries** (no `group`, the 1.4.1 shipped case)'
L_GRP_NEEDLE='**Grouped entries** (`anyGroup` manifests) additionally resolve a container'
L_S0_NEEDLE='**Step 0 — idempotency check.**'
o6_anchor_ok=1
c="$(count_fixed "$L_FLAT_NEEDLE" "$OMD")"
if [ "$c" -eq 1 ]; then ok "obsidian-vault: O6 flat-opener anchor is unique"; else bad "obsidian-vault: O6 flat-opener anchor count drifted from 1 (found $c)"; o6_anchor_ok=0; fi
c="$(count_fixed "$L_GRP_NEEDLE" "$OMD")"
if [ "$c" -eq 1 ]; then ok "obsidian-vault: O6 grouped-opener anchor is unique"; else bad "obsidian-vault: O6 grouped-opener anchor count drifted from 1 (found $c)"; o6_anchor_ok=0; fi
c="$(count_fixed "$L_S0_NEEDLE" "$OMD")"
if [ "$c" -eq 1 ]; then ok "obsidian-vault: O6 step-0-opener anchor is unique"; else bad "obsidian-vault: O6 step-0-opener anchor count drifted from 1 (found $c)"; o6_anchor_ok=0; fi

L_FLAT="$(line_of "$L_FLAT_NEEDLE" "$OMD")"
L_GRP="$(line_of "$L_GRP_NEEDLE" "$OMD")"
L_S0="$(line_of "$L_S0_NEEDLE" "$OMD")"
if [ "$o6_anchor_ok" -eq 1 ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ -n "$L_S0" ] \
   && [ "$L_FLAT" -lt "$L_GRP" ] && [ "$L_GRP" -lt "$L_S0" ]; then
  ok "obsidian-vault: O6 anchors ordered L_FLAT < L_GRP < L_S0 ($L_FLAT < $L_GRP < $L_S0)"
else
  bad "obsidian-vault: O6 anchor order wrong (flat=$L_FLAT grp=$L_GRP s0=$L_S0)"
fi

# Witness 1 (round-5 codex MAJOR, 3rd and terminating fix — A2/lead-verified). The bare formula
# `relative(dirname(index_file), chapter_file)` is a SHARED string (both branches legitimately use
# it), so no bare-formula check can bind a copy to its branch: round-3's first-match-placement only
# protected the flat copy (round-4 found the grouped copy unprotected); round-4's "second match >
# L_GRP" fix had no UPPER bound (a grouped-formula relocation past its own section stayed green)
# and used a LINE-based count (grep -oF, not count_fixed, is required to catch a 3rd occurrence
# appended onto an existing line). The terminating design: stop pinning the shared string and bind
# EACH copy to its own branch-specific sentence, which the two formula lines already carry —
#   FLAT (:262):    ...chapter_file)`; for wikilinks (the Obsidian default), ...
#   GROUPED (:287): ...Markdown links (`publish.wikilinks: false`), `relative(...chapter_file)`; ...
# Neither branch context appears in the other's line (verified), so each needle is inherently
# branch-bound — placement then only needs to prove it sits in ITS branch's line range.
FLAT_FORMULA_NEEDLE='chapter_file)`; for wikilinks'
c="$(count_fixed "$FLAT_FORMULA_NEEDLE" "$OMD")"; L="$(line_of "$FLAT_FORMULA_NEEDLE" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat entry's expected-target formula, branch-bound (placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat entry's expected-target formula, branch-bound (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# Round-6 codex MAJOR (4th, precision refinement): the previous upper bound — the NEXT H2, "##
# Wikilinks vs Markdown links" — was too loose. Step 0's own target-computation region ends far
# earlier, at "- **Container resolution**" (:313); everything between :313 and the H2 is container
# resolution / INDEX item 2 / manual-migration prose, none of which is Step 0. A careless
# cut-and-paste moving the grouped formula sentence down into that later prose (still legitimately
# "somewhere under INDEX wiring, after L_GRP") stayed inside [L_GRP, L_UPPER] and went undetected.
# Tightened to the actual Step-0 interval: (L_GRP, L_CR), a ~30-line window. "Container resolution"
# verified unique file-wide before use — an ordering comparison over a non-unique anchor proves
# nothing (same discipline as every other anchor in this file).
CR_ANCHOR='- **Container resolution**'
CR_ANCHOR_COUNT="$(count_fixed "$CR_ANCHOR" "$OMD")"
if [ "$CR_ANCHOR_COUNT" -eq 1 ]; then
  ok "obsidian-vault: O6 witness — Container-resolution anchor is unique"
else
  bad "obsidian-vault: O6 witness FAILED — Container-resolution anchor count drifted from 1 (found $CR_ANCHOR_COUNT)"
fi
L_CR="$(line_of "$CR_ANCHOR" "$OMD")"
GROUPED_FORMULA_NEEDLE='Markdown links (`publish.wikilinks: false`), `relative(dirname(index_file), chapter_file)`'
c="$(count_fixed "$GROUPED_FORMULA_NEEDLE" "$OMD")"; L="$(line_of "$GROUPED_FORMULA_NEEDLE" "$OMD")"
if [ "$CR_ANCHOR_COUNT" -eq 1 ] && [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_GRP" ] && [ -n "$L_CR" ] && [ "$L_GRP" -lt "$L" ] && [ "$L" -lt "$L_CR" ]; then
  ok "obsidian-vault: O6 witness — grouped Step-0's expected-target formula, branch-bound (placed at $L, before L_CR=$L_CR)"
else
  bad "obsidian-vault: O6 witness FAILED — grouped Step-0's expected-target formula, branch-bound (count=$c line=$L, need count==1 and $L_GRP<L<L_CR=$L_CR)"
fi
# Occurrence-level count, deliberately NOT count_fixed (which is LINE-based, per its own :188
# comment — a 3rd bare-formula copy appended onto an EXISTING line would still read as 2 lines).
# `grep -oF` emits one line per MATCH, so `wc -l` counts occurrences, catching a 3rd copy anywhere,
# same-line or not.
FORMULA_OCCURRENCES="$(grep -oF 'relative(dirname(index_file), chapter_file)' "$OMD" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$FORMULA_OCCURRENCES" -eq 2 ]; then
  ok "obsidian-vault: O6 witness — expected-target formula occurs exactly twice at occurrence level (flat + grouped)"
else
  bad "obsidian-vault: O6 witness FAILED — expected-target formula occurrence count drifted from 2 (found $FORMULA_OCCURRENCES)"
fi
# Witness 2 (round-3 codex BLOCKER 2): the >=2-match outcome. The old needle ("a flat entry gets no
# special case here") was the INTRO fragment only — it ends before the actual action ("duplicate
# halt fires"), which sits on the FOLLOWING physical line, so a mutation to the action itself left
# this green. Re-pointed to the TRIGGER+ACTION needle A2 returned, which is what the halt actually
# fires ON, not just the sentence that introduces it.
W2='Two or more matches** — halt'
c="$(count_fixed "$W2" "$OMD")"; L="$(line_of "$W2" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat >=2-match outcome (novel, placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat >=2-match outcome (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# Own adversarial check while wiring W2: the sentence wraps onto a SECOND physical line ("duplicate
# halt fires exactly as it does for the grouped branch below."), which W2 above does not span
# (has_in_section/count_fixed match per-line) — mutating just that continuation (e.g. "something
# entirely different happens here.") left W2 green in a probe. Same two-half-needle discipline as
# the locateChapterLine/expectedTarget pair: a second needle for the CONTINUATION line, so the
# claim that this is the IDENTICAL duplicate halt (not merely "some halt") is itself proven.
W2B='duplicate halt fires exactly as it does for the grouped branch below.'
c="$(count_fixed "$W2B" "$OMD")"; L="$(line_of "$W2B" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat >=2-match outcome names the IDENTICAL duplicate halt (novel, placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat >=2-match outcome's duplicate-halt continuation (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# Witness 3: the exactly-1-match outcome — already wired, no container to verify.
W3='a flat entry has no container to verify'
c="$(count_fixed "$W3" "$OMD")"; L="$(line_of "$W3" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat exactly-1-match outcome (novel, placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat exactly-1-match outcome (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# Witness 4: the 0-match (append) outcome — under the existing flat chapter-list heading, never a
# new container.
W4='under whichever heading the file already uses for its flat chapter list'
c="$(count_fixed "$W4" "$OMD")"; L="$(line_of "$W4" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat 0-match append outcome (novel, placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat 0-match append outcome (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# Witness 5 (two-mode target + display-text binding, O6). Round-3 codex BLOCKER 2: the old needle
# stopped BEFORE the bound value ("...manifest entry's", no `title`), so a title->slug mutation
# left it green. Extended to include the value itself (A2-returned string).
W5='display text is always the manifest entry'"'"'s `title`'
c="$(count_fixed "$W5" "$OMD")"; L="$(line_of "$W5" "$OMD")"
if [ "$c" -eq 1 ] && [ -n "$L" ] && [ -n "$L_FLAT" ] && [ -n "$L_GRP" ] && [ "$L_FLAT" -lt "$L" ] && [ "$L" -lt "$L_GRP" ]; then
  ok "obsidian-vault: O6 witness — flat display-text binding (novel, placed at $L)"
else
  bad "obsidian-vault: O6 witness FAILED — flat display-text binding (count=$c line=$L, need count==1 and $L_FLAT<L<$L_GRP)"
fi
# The gate-target witness sits OUTSIDE the flat/grouped branch structure — plain has_in_section, NOT
# O6 (no placement mutation exists for it; rev-19 corrected rev-18's wrongful demand for one).
has_in_section "obsidian-vault: link-integrity gate item 5's flat target (no container heading)" \
  "$OMD" '## Link integrity gate before you publish' \
  'under its flat chapter-list heading for a flat one'

echo "== group axis (#19) — chapter path + asset dir + embed formula, both adapters + SKILL.md =="
# D2: grouped chapter path form, shared across both adapters and SKILL.md W5.
has "obsidian-vault: grouped chapter-path form"        '/<group>/<slug>.md' "$OMD"
has "static-md: grouped chapter-path form"             '/<group>/<slug>.md' "$SMD"
has "skill: grouped chapter-path form (W5)"            '/<group>/<slug>.md' "$SKILL"
# D3: the ONE canonical embed formula, byte-identical in both adapters — chapterAssetDir(entry) is
# the group-aware core (R6-F3: static-md switched off its ungrouped partial concatenation).
EMBED_FORMULA='relative(dirname(chapter_file), join(chapterAssetDir(entry), <file>))'
has "obsidian-vault: full-target embed formula (D3)" "$EMBED_FORMULA" "$OMD"
has "static-md: full-target embed formula (D3)"      "$EMBED_FORMULA" "$SMD"

echo "== group axis (#19) — index wiring (D6), both adapters =="
# R6-F1: step-0 already-wired short-circuit runs BEFORE container classification, so re-runs converge.
has "obsidian-vault: step-0 already-wired short-circuit" 'wiring is already complete' "$OMD"
has "static-md: step-0 form-agnostic short-circuit"      'form-agnostic, and it runs BEFORE any container' "$SMD"
# round-9 [mutation testing]: the step-0 already-wired short-circuit's container-title comparison
# had ZERO coverage — not even a bare function-name grep. Pinned with its real args (containerTitle,
# entry) so a mutation that compares against a stale entry (e.g. oldEntry) instead of the current
# one, silently breaking the "already wired, skip re-wiring" short-circuit, goes red. This
# subsection IS hard-wrapped (~85-95 cols, unlike the sections pinned earlier in this cluster) —
# the call sits entirely on one physical line, verified before wiring; a longer needle reaching for
# the trailing "(titles compare TRIMMED...)" parenthetical would have wrapped and silently never
# matched, so it is deliberately left out.
has_in_section "obsidian-vault: step-0 idempotency check calls containerTitleMatches with its real args" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'containerTitleMatches(containerTitle, entry)'
# round-10 [mutation testing]: static-md.md's own step-0 idempotency check has the SAME two calls
# (locateChapterLine, containerTitleMatches), still pinned only by surrounding prose before this
# round. Codex's named mutations: locateChapterLine called with entry.slug instead of
# expectedTarget (permits a duplicate TOC insertion); containerTitleMatches comparing
# entry.group_title against itself (accepts a wrong container). Both left every existing pin green.
#
# #248 (round-2, A3): Step 0 is now FORM-AGNOSTIC and runs before container classification, so
# `locateChapterLine`/`expectedTarget` moved UP into the shared "## Index wiring" H2 (a group-free
# reader now sees them) — only `containerTitleMatches`, which verifies a GROUPED entry's container,
# still belongs to "### Grouped index wiring" and stays pinned there. Re-pointing the first pair's
# heading is what actually proves the relocation: an H2's scope includes its nested H3s, so pinning
# them to the (now too-narrow) H3 would silently pass on the pre-move text too.
#
# BOTH calls wrap mid-argument in this file (hard-wrapped, unlike the sections pinned earlier this
# round) — `locateChapterLine(indexLines,` ends one line and `expectedTarget)` starts the next;
# `containerTitleMatches(containerTitle,` ends one line and `entry)` starts the next. Neither full
# signature fits a single fixed-string needle. What's pinned instead: two needles per call, one per
# physical line, each covering one argument independently — so a mutation to EITHER argument is
# still caught, just not by one needle proving the whole signature at once. What this does NOT
# prove: that the two half-needles are adjacent lines of the SAME call (an adversarial rewrite that
# separated them further apart, keeping both halves individually true, would not be caught) — a
# narrower gap than round-9's SKILL.md pins, which each cover a complete signature in one shot.
has_in_section "static-md: step-0 idempotency check calls locateChapterLine, first arg indexLines" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'locateChapterLine(indexLines,'
has_in_section "static-md: step-0 idempotency check's locateChapterLine, second arg expectedTarget" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'expectedTarget)`'
has_in_section "static-md: step-0 idempotency check calls containerTitleMatches, first arg containerTitle" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'containerTitleMatches(containerTitle,'
has_in_section "static-md: step-0 idempotency check's containerTitleMatches, second arg entry (trimmed compare)" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'entry)`** (from `assets/lib/chapter-paths.mjs`; titles compare TRIMMED, not raw `===`)'
# #248 (round-2, A3): the three normative Step-0 OUTCOMES a group-free reader must actually see —
# the ambiguous-match halt, and the two flat-entry branches (line present / line absent) — now live
# in the same shared "## Index wiring" H2 rather than being reachable only through the grouped-only
# subsection. These are witnesses of OUTCOME, not setup: proving the calls above exist is not the
# same as proving each branch's result is stated. Each needle is unique file-wide (verified).
has_in_section "static-md: step-0's ambiguous-match halt (>=2 lines) is stated under the shared H2" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'appears multiple times in <index_file>'
has_in_section "static-md: step-0's flat-entry, line-present outcome is stated under the shared H2" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  '**Flat entry, line present**'
has_in_section "static-md: step-0's flat-entry, line-absent outcome is stated under the shared H2" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  '**Flat entry, line absent**'
# PLACEMENT, not just presence (round-3 codex BLOCKER 1 — the original 2-of-6 coverage let all of
# the following move independently below the H3 while every has_in_section above stayed green: the
# SECOND locateChapterLine half (`expectedTarget)`), and all three OUTCOME witnesses). An H2's
# scope legitimately includes its nested H3, so presence alone proves nothing about which side of
# the H3 boundary a line sits on — only a position comparison against the H3's own line does.
# ALL SIX setup+outcome assertions above get one, empirically confirmed to catch a moved-under-H3
# clone for each (verified individually before wiring, same discipline as the original two).
L_H3_GROUPED="$(line_of '### Grouped index wiring (`anyGroup` manifests only)' "$SMD")"
assert_line_before "static-md: step-0's locateChapterLine (1st half) sits before the grouped-only H3" \
  "$(line_of 'locateChapterLine(indexLines,' "$SMD")" "$L_H3_GROUPED"
assert_line_before "static-md: step-0's locateChapterLine (2nd half, expectedTarget) sits before the grouped-only H3" \
  "$(line_of 'expectedTarget)`' "$SMD")" "$L_H3_GROUPED"
assert_line_before "static-md: step-0's ambiguous-match halt sits before the grouped-only H3" \
  "$(line_of 'appears multiple times in <index_file>' "$SMD")" "$L_H3_GROUPED"
assert_line_before "static-md: step-0's flat-entry, line-present outcome sits before the grouped-only H3" \
  "$(line_of '**Flat entry, line present**' "$SMD")" "$L_H3_GROUPED"
assert_line_before "static-md: step-0's flat-entry, line-absent outcome sits before the grouped-only H3" \
  "$(line_of '**Flat entry, line absent**' "$SMD")" "$L_H3_GROUPED"
# R7-F1: wrong-container halt — never silently relocate a user-curated index line.
WRONG_CONTAINER_HALT="Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>'"
has "obsidian-vault: wrong-container halt" "$WRONG_CONTAINER_HALT" "$OMD"
has "static-md: wrong-container halt"      "$WRONG_CONTAINER_HALT" "$SMD"
# R7-F2: step-0's expected link target uses the same index-relative coordinate system as the TOC write.
# Round-6 codex fix: this comment previously claimed obsidian-vault.md has "exactly ONE site" for
# this formula — FALSE since #248's O6 work (round-2/5/6): it has TWO, the flat branch (:262) and
# the grouped Step-0 branch (:287), each independently proven by the branch-bound witnesses above
# (novel + placed in its own branch interval) plus the occurrence-count==2 check (also above), which
# is the REAL proof now. The whole-file `has` below only proves "at least one site exists" — strictly
# weaker, and fully subsumed — kept anyway as a cheap early signal (same call as static-md.md's own
# subsumed whole-file check just below: not duplicate coverage to be "simplified" away).
has "obsidian-vault: index-relative expected-target formula" 'relative(dirname(index_file), chapter_file)' "$OMD"
# round-10 exhaustive sweep / round-11: static-md.md has THREE independent sites for this SAME
# formula, not the two originally flagged — the flat TOC-write (item 1, "## Index wiring"), the
# step-0 idempotency check's expected target (also "## Index wiring" since #248's round-2 move —
# it explicitly reuses "the same coordinate system item 1 above uses" rather than an unrelated
# computation, but is still its own independent call site that must not drift from item 1's write
# path or re-runs stop converging), and the link-integrity gate's item 5. This whole-file `has` is
# now SUBSUMED by the three per-site pins below — kept deliberately as a cheap early signal, not
# duplicate coverage to be "simplified" away; a mutation corrupting only one of the three sites
# would leave this needle green since the other two copies still match.
has "static-md: index-relative expected-target formula"      'relative(dirname(index_file), chapter_file)' "$SMD"
# The bare formula occurs FIVE times file-wide, FOUR of them inside this same "## Index wiring" H2
# alone (item 1's write, its two degenerate-case bullets, and step 0's own target) — an H2-scoped
# needle using the bare formula cannot tell item 1's copy from step 0's copy, since has_in_section
# stops only at the same-or-shallower heading and both sit in the identical scanned range. Fixed
# the same way both times: each needle carries its OWN unique trailing context that the other
# occurrences do not share — item 1's ends "`. The link" (round-2, A3 added a new sentence binding
# the link's display text to the manifest title right after the formula, which is why this needle
# is "The link", not the older "Order"), step 0's ends "— step 0's own target." (verified
# count_fixed == 1 for each, file-wide). #248 (round-2, A3) moved step 0 itself from the
# grouped-only H3 into this shared H2, so the two pins are now siblings in the SAME section rather
# than nested across an H2/H3 boundary — the bare-formula ambiguity got WORSE by the move, which is
# exactly why the trailing-context discipline, not the heading alone, is what keeps them
# independent.
has_in_section "static-md: flat TOC-write uses the index-relative expected-target formula" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'relative(dirname(index_file), chapter_file)`. The link'
has_in_section "static-md: grouped step-0 idempotency check uses the same expected-target formula" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'relative(dirname(index_file), chapter_file)` — step 0'"'"'s own target.'
# Same PLACEMENT gap as the setup/outcome sextet above (O4 §3.3 group proof, reusing the already-
# resolved $L_H3_GROUPED): H2's scope includes the nested H3, so this needle stays green even moved
# back under "### Grouped index wiring" — empirically confirmed before wiring this check.
assert_line_before "static-md: step-0's own-target formula sits before the grouped-only H3" \
  "$(line_of 'step 0'"'"'s own target.' "$SMD")" "$L_H3_GROUPED"
has_in_section "static-md: link-integrity gate item 5 uses the same expected-target formula" \
  "$SMD" '## Link-integrity gate before you publish' \
  'relative(dirname(index_file), chapter_file)'
# rev 9: manual-migration halt — establishment wiring never renames/moves/deletes a container or line.
MANUAL_MIGRATION_HALT='This manifest change requires manual group migration (not automated in 1.5.0):'
has "obsidian-vault: manual-migration halt" "$MANUAL_MIGRATION_HALT" "$OMD"
has "static-md: manual-migration halt"      "$MANUAL_MIGRATION_HALT" "$SMD"

echo "== group axis (#19) — obsidian-only: Quartz limitation (D5) + markdown-link gate extension =="
has "obsidian-vault: Quartz shortest-mode limitation note" "does **not** resolve under Quartz's \`shortest\`" "$OMD"
# #220 Task J widens this gate off the `anyGroup` scope (was: 'and the manifest is `anyGroup`, this
# item'); re-pointed at the post-edit wording (also asserted section-scoped below in the Task H block).
has "obsidian-vault: markdown-link gate extension covers group-free" 'group-free manifests included' "$OMD"

echo "== group axis (#19) — static-md-only: gate #1 group-aware + headings-only automation =="
has "static-md: gate #1 is a resolution check, not a spelling check" 'resolution** check, not a spelling check' "$SMD"
has "static-md: automated grouped wiring is headings-only" '**Automated grouped wiring works only on a Markdown-headings-form index.**' "$SMD"
has "static-md: non-heading manual-wiring halt" "Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run." "$SMD"

echo "== group axis (#19) — manifest-discipline.md =="
MDISC="$REFS/manifest-discipline.md"
has "manifest-discipline: activation rule" 'a manifest becomes *grouped* the moment any single entry carries `group`' "$MDISC"
has "manifest-discipline: duplicate-slug halt (globally unique across all)" 'globally unique across all' "$MDISC"
has "manifest-discipline: reserved-slug halt" "slug 'assets' is reserved in a grouped manifest" "$MDISC"
has "manifest-discipline: every-grouped-entry title rule" 'Every grouped entry requires `group_title`' "$MDISC"
has "manifest-discipline: recapture carve-out for a group-only move" 'the screenshot set is NOT recaptured for a group-only move' "$MDISC"

echo "== group axis (#19) — capture-manifest example + capture spec consumer import =="
has "capture-manifest example: carries group_title"                    'group_title: "Group title"'      "$ASSETS/capture-manifest.example.yml"
# NOT the consumer-binding enforcement — that structural pin lives in chapter-paths.test.mjs (A5).
# has()/hasnt() are fixed-string greps a decoy comment can satisfy; this is only a cheap early signal.
has "capture.example.spec.ts: imports chapter-paths.mjs (early signal only)" "from './lib/chapter-paths.mjs'" "$SPEC"

echo "== group axis (#19) — revalidation.md manual-migration recipe + convergence checklist =="
# round-15 [adversarial reading]: "## Write-time canon" originally claimed the full-target embed
# AND link formulas are "the same formula in both adapters" — true for embeds, false for links:
# obsidian-vault.md under publish.wikilinks: true uses bare [[<chapter-slug>|Display title]] with
# no path math, so a W6 rewrite in that mode got two contradictory normative instructions (this
# canon's link formula vs the wikilinks-on bare-link rule). Now split: the embed paragraph stays
# unconditional (both adapters, either manifest shape); the link paragraph is NOT uniform — pinned
# on its explicit "only under wikilinks: false" scoping, the negation of the embed paragraph's
# unconditional claim.
has_in_section "revalidation: write-time-canon embed formula is unconditional, both adapters" \
  "$REVAL" '## Write-time canon' \
  'the full-target **embed** formula — the same formula in both'
has_in_section "revalidation: write-time-canon link formula is scoped, NOT uniform like the embed" \
  "$REVAL" '## Write-time canon' \
  'uses it only under `publish.wikilinks: false`'
# round-16 [what our own change newly exposed, not ambiguity or a stale citation]: round 15 fixed
# the "## Write-time canon" paragraph above, but the manual-migration recipe's own step 4 — which
# routes readers past that very paragraph — still said rewrite "embeds and glossary/Related links
# using the full-target formulas — ALWAYS", scoped only to flat-vs-grouped and silent on wikilinks
# mode. Under `publish.wikilinks: true` that directly contradicts the canon's bare-wikilink case. A4
# split embed (still unconditional) from link (adapter/mode-scoped, with the wikilinks-on bare-form
# spelled out) so the recipe can no longer be followed literally into the same contradiction.
#
# round-17 correction [the pin itself cemented a defect — a pin is only as good as the claim it
# points at, pinning is not review]: round 16's link fix, exactly as briefed and pinned above, still
# collapsed CHAPTER-target links and GLOSSARY-target links into one "wikilinks-on bare-form" rule.
# The Related block legitimately holds both target types, and the glossary form
# (`[[{{glossary_dir basename}}/index#TermHeading|TermHeading]]`) is not the chapter form
# (`[[<slug>|Display title]]`) — following the round-16 wording literally would have rewritten valid
# glossary links into links to nonexistent notes. A4 re-split step 4 by TARGET TYPE instead of by
# formula: chapter-target links use each adapter's chapter-link canon (unchanged from round 16);
# glossary-target links use each adapter's SEPARATE glossary canon, explicitly never the chapter
# formula even in the same mode. The stale needle above ('as a bare `[[<slug>|Display title]]`
# wikilink under `publish.wikilinks: true`') no longer exists in the file and must not be re-added —
# it was pinning the exact collapsed wording this round retracted. Replaced with ONE needle carrying
# the load-bearing claim itself (that the two target types take different formulas), not either
# formula individually — a mutation re-collapsing them back into one rule is what this needs to
# catch, and that mutation is precisely what round 16's own wording was.
has_in_section "revalidation: recipe step 4 embed rewrite is unconditional (all adapters, all modes)" \
  "$REVAL" '### The manual group-migration recipe' \
  'regardless of adapter or `publish.wikilinks` mode'
has_in_section "revalidation: recipe step 4 chapter-target and glossary-target links use DIFFERENT formulas, never conflated" \
  "$REVAL" '### The manual group-migration recipe' \
  'never the chapter-link formula, even within the same mode — the two target types use different formulas'
# round-19 [fifth instance of the category-collapse shape, second self-inflicted]: step 4's
# chapter/glossary split (above) still omitted a THIRD target type static-md.md's Related block
# requires — the mandatory index-target link its own navigability check (Link-integrity gate item
# 3) enforces. Following the two-category recipe exactly on a moved chapter leaves that link at the
# OLD depth, pointing at a file that no longer exists there; the migration cannot converge. Two
# needles: the third category's EXISTENCE (a mandatory index-target link, on top of chapter and
# glossary), and its OWN anti-collapse clause — a distinct sentence from the chapter/glossary pin
# above, not a restatement of it. Either alone is the property that would fail if someone
# re-collapsed three cases back to two, which is exactly how both prior collapses (round 16, round
# 17) happened.
#
# The obsidian-vault.md NEGATIVE half of A4's clause ("no equivalent mandatory index-target link...
# so this case does not apply there") is deliberately left unpinned. It's a verified fact today,
# but A4 flagged it as tied to a filed, tracked gap (group-H's Related-block enumeration) — if that
# gap is ever legitimately closed by giving Obsidian's Related block an index member, this exact
# clause is the line that SHOULD change. Pinning it would fight that future correct edit rather than
# catch a regression; the asymmetry is stated in prose (static-md.md:15-16's "no backlinks panel"
# rationale) rather than gated in a test.
has_in_section "revalidation: recipe step 4 names a THIRD target type — static-md.md's mandatory index-target link" \
  "$REVAL" '### The manual group-migration recipe' \
  'also has a mandatory **index-target link** back to `{{publish.index_file}}`'
has_in_section "revalidation: recipe step 4's index-target case is never the chapter or glossary formula" \
  "$REVAL" '### The manual group-migration recipe' \
  'never the chapter-link or glossary-link formula'
has "revalidation: recipe fixes inbound links from other chapters"  'Fix inbound links from other chapters that referenced the old path' "$REVAL"
has "revalidation: recipe updates the capture spec output dir(s)"   "Update the project's capture spec output dir(s)" "$REVAL"
has "revalidation: terminal-state convergence checklist heading"    'Terminal-state convergence checklist' "$REVAL"
has "revalidation: post-migration handbook-wide link scan"          'Post-migration handbook-wide link scan' "$REVAL"
has "revalidation: anyGroup-flip write-canon note (not a halt)"     'informational write-canon note in the W6 report' "$REVAL"
has "revalidation: non-blocking stale-artifact advisory"            'non-blocking stale-artifact advisory' "$REVAL"
# R17-F3: the normative prose must call the production predicates, not paraphrase ad-hoc checks.
has "revalidation: invokes specReferencesDir("     'specReferencesDir(' "$REVAL"
has "revalidation: invokes chapterHasWikilinkTo("  'chapterHasWikilinkTo(' "$REVAL"
# round-9 [mutation testing, same class as the SKILL.md validateGroups gap]: the two bare-name pins
# above only prove the function NAME appears somewhere in the file — they don't touch the ARGUMENTS,
# so a mutation that swaps or drops an argument (e.g. checking the wrong dir, or the removed
# entry's NEW path instead of its old one) sails through green while silently weakening a
# manual-migration convergence fact. specReferencesDir has TWO independent call sites here (the
# retained-entry-group-changed fact, and the grouped-entry-removed fact) — each pinned by its own
# call+args needle, split from the "twice, with two different dir spellings" requirement into a
# second needle per site so a mutation that drops just the second required call is independently
# caught, without needing a fragile apostrophe-escaped single needle (this paragraph is one long
# unwrapped physical line per bullet, so no ~95-col wrap risk here — verified before wiring).
has_in_section "revalidation: retained-entry-group-changed fact calls specReferencesDir(specText, dir)" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'call `specReferencesDir(specText, dir)` once with the old asset dir'
has_in_section "revalidation: retained-entry-group-changed fact requires the SECOND dir-spelling call" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'and once with its `output_dir`-relative tail'
has_in_section "revalidation: grouped-entry-removed fact calls specReferencesDir(specText, dir)" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'call `specReferencesDir(specText, dir)` against the removed entry'
has_in_section "revalidation: grouped-entry-removed fact requires both dir spellings, not one" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'old dir, both spellings'
has_in_section "revalidation: wikilink-reference fact calls chapterHasWikilinkTo with its real args" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'chapterHasWikilinkTo(chapterText, slug, oldChapterRelPath)'

echo "== group axis (#19) — publish-targets README =="
has "publish-targets README: Group handling: support or halt bullet" 'Group handling: support or halt.' "$PTREADME"

echo "== #294/#295: vault-root-relative wikilinks + INDEX target (v1.8.0) =="
# #294 (Option A): wikilinks-mode chapter link and INDEX target are now vault-root-relative,
# mirroring the #247 glossary-link fix above (L1207-1218) — a bare basename only
# disambiguates vault-wide, which this skill never guarantees (it enforces uniqueness only
# across the handbook). Each pin below is red-before-green against the pre-#294 doc text.
has_in_section "obsidian-vault: chapter wikilink target is vault-root-relative (mirrors glossary)" \
  "$OMD" '## Wikilinks vs Markdown links' \
  'relative(<vault-root>, {{publish.chapters_dir}})'
hasnt "obsidian-vault: no longer claims wikilinks resolve by basename with a grouping-invariant form" \
  'Wikilinks resolve by basename, so grouping never changes this form' "$OMD"
has_in_section "obsidian-vault: chapter wikilink target changes with grouping, unlike the pre-1.8.0 bare slug" \
  "$OMD" '## Wikilinks vs Markdown links' \
  'grouping DOES change it'

# The legacy-bare union scan (§1b): an installed handbook may still carry the pre-1.8.0 bare
# `[[slug]]` spelling. W5 reconciles a qualified scan and a legacy-bare scan into exactly one
# of four outcomes via `classifyChapterWiring`: absent -> append, canonical -> leave (already
# wired), legacy -> retarget in place, duplicate -> halt. Pinned at both the flat-entry
# pointer (this algorithm applies with no container) and the grouped Step 0 (the full text).
has_in_section "obsidian-vault: flat entries' wikilinks-mode wiring points at the union scan through classifyChapterWiring" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'Wikilinks mode instead runs the qualified/legacy-bare **union scan**'
has_in_section "obsidian-vault: the four union-scan outcomes map onto append/leave/retarget/halt" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'the four outcomes map directly onto the three bullets above, plus one new one'
has_in_section "obsidian-vault: a legacy-form line is retargeted to the qualified spelling in place" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'retarget the matched bare-slug line to the qualified form in place'
has_in_section "obsidian-vault: grouped Step 0 runs the same union scan via classifyChapterWiring" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'Wikilinks mode instead runs a **union scan**'

# D-8: the 4-way classification decides target-string presence/form only — the pre-1.8.0
# wrong-container / uncontained placement halts are RETAINED, layered on top, never replaced.
# Two cases: (a) a grouped chapter's qualified wikilink is spelled exactly right but sits
# under the wrong heading — still a relocate-halt, not silently "already wired"; (b) a
# `legacy`-form bare line under the wrong container halts too, checked BEFORE any retarget.
has_in_section "obsidian-vault: D-8(a) — a correctly-spelled qualified wikilink under the wrong heading still halts for relocation" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'relocate-halt, not silently "already wired"'
has_in_section "obsidian-vault: D-8(b) — a legacy bare line under the wrong container halts BEFORE any retarget is attempted" \
  "$OMD" '## INDEX wiring (do all of these on every chapter create/update)' \
  'before any retarget is attempted — placement is checked before'

# revalidation.md: the write-time canon's wikilinks-on case is now vault-root-relative, not a
# bare basename — the old "basename resolution with no relative-path math" claim is gone.
hasnt "revalidation: no longer describes the wikilinks-on chapter link as bare basename resolution" \
  'basename resolution with no' "$REVAL"
has_in_section "revalidation: write-time-canon states the wikilinks-on link is vault-root-relative" \
  "$REVAL" '## Write-time canon' \
  'chapter links **vault-root-relative**'

# revalidation.md §1b BLOCKER-2a: the legacy-bare-gone check is scoped to the OLD container,
# never a vault-wide bare-slug scan — a root grouped-to-flat migration can make the new flat
# target equal the old bare slug, which a global rule would wrongly forbid. The pre-1.8.0
# "exactly ONE match under a shared container" special case (dead under vault-rel targets,
# which are never textually identical across a group rename) is gone.
has_in_section "revalidation: legacy-bare-gone check is scoped to the OLD container, not global" \
  "$REVAL" '### Terminal-state convergence checklist' \
  'the check is scoped to lines sitting under the container titled the OLD'
hasnt "revalidation: no longer requires exactly ONE match under a shared container (dead under vault-rel)" \
  'the fact instead requires exactly ONE' "$REVAL"

# manifest-discipline.md: the duplicate-slug gate stays global despite skill-emitted links no
# longer keying on basename — the file tree, a user-authored bare wikilink, and Quartz-shortest
# bare-name resolution still need it; relaxing to per-group is a deferred follow-up (D-3).
has_in_section "manifest-discipline: 1.8.0 states skill-emitted wikilinks are vault-root-relative, not bare basename" \
  "$MDISC" '### Manifest review — grouped and group-free halts' \
  'target are vault-root-relative, not a bare basename'
has_in_section "manifest-discipline: duplicate-slug gate rationale names user-authored bare wikilinks" \
  "$MDISC" '### Manifest review — grouped and group-free halts' \
  'for a user-authored bare `[[<slug>]]` wikilink anywhere'
has_in_section "manifest-discipline: duplicate-slug gate rationale names Quartz-shortest bare-name resolution" \
  "$MDISC" '### Manifest review — grouped and group-free halts' \
  'Quartz'"'"'s `shortest`-mode bare-name resolution'
has "manifest-discipline: activation rule lists the 1.8.0 currentIndexExpectedTarget group-free exception (third)" \
  'one from 1.8.0: `currentIndexExpectedTarget`' "$MDISC"

# publish-targets/README.md: the module-level group-free-exception inventory is now three, not two.
has "publish-targets README: three module-level group-free exceptions (adds currentIndexExpectedTarget)" \
  'Three exceptions are in' "$PTREADME"

echo "== #220/#221 write-canon + mandatory validateGroups wiring (Task H) =="
# Section-bound (has_in_section), not whole-file: a whole-file grep cannot prove a claim is made
# in the RIGHT step's own prose (round-5 blocker 6). These remain doc-consistency checks — they
# cannot prove any runtime step imperatively CALLS validateGroups; that is a human-reviewed
# contract (F1), not something greps enforce.
has_in_section "SKILL.md W1: halts before any capture asset on a returned message" \
  "$SKILL" '### W1 — Discover the feature surface' \
  'halt on every returned message before any capture asset'
has_in_section "SKILL.md W6: MUST run validation before re-capture/re-authoring" \
  "$SKILL" '### W6 — Revalidation / audit mode (existing chapters)' \
  'Before any re-capture or re-authoring, you MUST run'
# round-9 [mutation testing, IMPORTANT]: the two assertions above pin only the surrounding
# sequencing language ("halt on every returned message...", "Before any re-capture..."), never the
# CALL itself. A mutation that changes `validateGroups(entries)` to `validateGroups([])` at either
# site validates an empty array instead of the manifest — silently re-inerting #221's whole halt and
# restoring full production reachability for a duplicate slug — while every doc gate above, and the
# unit tests (the helper itself is unchanged), stay green. Pinned independently per workflow: the
# needle is identical at both W1 (:94) and W6 (:138), so only section-bounding tells them apart, and
# a single shared gate would pass a mutation that disables just one of the two call sites.
has_in_section "SKILL.md W1: pins the actual validateGroups(entries) call, not just its prose" \
  "$SKILL" '### W1 — Discover the feature surface' \
  'MUST run `validateGroups(entries)`'
has_in_section "SKILL.md W6: pins the actual validateGroups(entries) call, not just its prose" \
  "$SKILL" '### W6 — Revalidation / audit mode (existing chapters)' \
  'MUST run `validateGroups(entries)`'
# round-14 [IMPORTANT]: the pin above proves the CALL exists, but not what it's called WITH. The
# original wording — "against the current manifest (delta or unchanged)" — read naturally as "the
# delta manifest". validateGroups only counts duplicates within the array it receives, so
# validating an accepted delta alone against an unchanged retained entry of the same slug returns
# `[]`, silently permitting the exact overwrite #221 exists to prevent. A4 replaced it with an
# explicit merged-manifest requirement PLUS the mechanism, so a future re-compression has to
# actively delete a stated reason rather than merely miss an implication. Two needles, not one:
# the requirement (what) and the mechanism (why) are independently corruptible — losing the why
# alone reopens the exact editorial pressure that created this bug (an editor tightens the prose,
# drops the "unnecessary" explanation, and a later edit erodes the requirement itself since nobody
# left evidence of why it mattered).
has_in_section "SKILL.md W6: validateGroups runs against the MERGED manifest, never the delta alone" \
  "$SKILL" '### W6 — Revalidation / audit mode (existing chapters)' \
  'every retained entry plus the accepted delta, merged into one array, never the delta alone'
has_in_section "SKILL.md W6: states WHY the delta alone is insufficient (validateGroups' array-scoped duplicate check)" \
  "$SKILL" '### W6 — Revalidation / audit mode (existing chapters)' \
  'only sees duplicates within the array you hand it'
has_in_section "manifest-discipline: MUST run validateGroups(entries) (mandatory, not optional)" \
  "$MDISC" '## The discipline: no capture code before review' \
  'MUST run validateGroups(entries)'
hasnt "manifest-discipline: no longer frames validateGroups as an optional convenience" \
  'running it during drafting is an optional' "$MDISC"
# round-15 [adversarial reading, not mutation testing — the prior wording meant a requirement
# without requiring it]: step 5 said re-run validation "after every edit that touches a slug or a
# group", so a group_title-only edit (two groups converging on one title — containers are located
# BY TITLE, obsidian-vault.md:207) let a reader skip the only detector for that collision, silently
# merging two groups under one nav container. Now keyed to the GENERAL RULE (any field
# validateGroups inspects), with the slug/group/group_title list explicitly illustrative. Two
# needles: the general rule governing (not narrowed to slug/group), and the group_title-only
# scenario that caused the bug specifically.
has_in_section "manifest-discipline: step 5 re-run rule is NOT narrowed to slug/group only" \
  "$MDISC" '## The discipline: no capture code before review' \
  'never only an edit that touches `slug` or `group`'
has_in_section "manifest-discipline: step 5 names the group_title-only collision scenario" \
  "$MDISC" '## The discipline: no capture code before review' \
  'a `group_title`-only change (e.g. two groups converging on'
has_in_section "static-md: Assets section covers flat entries and group-free manifests alike" \
  "$SMD" '## Assets' \
  'flat entries and group-free manifests alike'
hasnt "static-md: no longer keeps the byte-identical 1.4.1 embed form for group-free" \
  'keep the shipped 1.4.1 embed form' "$SMD"
has_in_section "obsidian-vault: glossary backlink discipline covers any manifest, wikilinks off" \
  "$OMD" '## Glossary backlink discipline' \
  'Wikilinks off, any manifest'
# The `Wikilinks off, ` prefix disambiguates against a preserved bullet the bare phrase also
# matched; that bullet is gone after A4's merge, so the prefix isn't load-bearing today, and this
# needle is now a strict subset of the broader internal-link-bullet casualty added below — kept
# anyway because the two assert distinct claims (glossary vs internal-link bullet), and a future
# rewording could un-subsume it.
hasnt "obsidian-vault: no longer scopes the glossary backlink fix to group-free only" \
  'Wikilinks off, group-free manifest (shipped 1.4.1 form, unchanged)' "$OMD"
has_in_section "obsidian-vault: link integrity gate covers group-free manifests too" \
  "$OMD" '## Link integrity gate before you publish' \
  'group-free manifests included'
hasnt "obsidian-vault: gate no longer scoped to \`anyGroup\` only" \
  'and the manifest is `anyGroup`, this item' "$OMD"
has_in_section "obsidian-vault: link integrity gate states its chapter-scope limit (no handbook-wide sweep)" \
  "$OMD" '## Link integrity gate before you publish' \
  'does not sweep untouched chapters'
# round-16 [what our own change newly exposed]: widening item 2 to group-free manifests inherited
# an existing overbreadth we didn't create but did newly expose — it demanded EVERY standard
# Markdown link resolve like an internal .md/glossary target, so a compliant chapter with
# `[Support](mailto:...)`, an external `https://` link, or a bare `#fragment` would FALSE-HALT.
# Three needles, one per the three concrete false-halt cases named in the finding: the **relative**
# scoping is the actual fix (matches static-md.md:407's own wording); the bare-fragment rule and
# the non-relative exemption class are the two other named cases it was previously silent on.
has_in_section "obsidian-vault: link-integrity item 2 scoped to RELATIVE links only (not every link)" \
  "$OMD" '## Link integrity gate before you publish' \
  'this item also verifies every **relative** standard'
has_in_section "obsidian-vault: link-integrity item 2 checks a bare fragment against the chapter's own headings" \
  "$OMD" '## Link integrity gate before you publish' \
  "no path component) is checked against the **current chapter's own headings**, not"
has_in_section "obsidian-vault: link-integrity item 2 exempts non-relative targets (mailto/http/URI-scheme)" \
  "$OMD" '## Link integrity gate before you publish' \
  '**exempt** — this item verifies vault-internal resolution, not that an external'
# Boundary of this whole pin cluster (rounds 5-8, through the >=2-link halt below): every
# assertion here is a POSITIVE has_in_section — it proves the canon IS STATED in the right
# section. It cannot prove that no CONTRADICTING statement is stated elsewhere, because catching
# an addition would need a casualty per possible contradicting phrasing, and that space is
# unbounded (the same receding-target shape as the PII per-field suppression problem already
# documented for this plugin — the convergence move there was to stop enumerating and document
# the boundary instead). Confirmed empirically: appending "Glossary linking, however, is skipped
# entirely when wikilinks are off." right after the fallback bullet leaves every pin here green.
# The defense against a newly-added contradiction is adversarial review, not more greps — that is
# what actually caught both the round-7 and round-8 defects. When review finds the next one, the
# right response is a targeted casualty for THAT wording, not an attempt to enumerate
# contradictions in advance.
# round-5: the wikilinks-off Internal-chapter-link bullet had a surviving two-branch
# group-free/anyGroup conditional — a missed site of the #220 fix itself. A4 merged it to one
# all-manifest form. Section-scoped so a whole-file grep can't confuse this with the glossary
# bullet's own, separately-asserted "any manifest" wording a few lines below.
has_in_section "obsidian-vault: wikilinks-off internal chapter link is one all-manifest form" \
  "$OMD" '## Wikilinks vs Markdown links' \
  'Internal chapter link, any manifest'
hasnt "obsidian-vault: no longer special-cases the group-free internal-link spelling" \
  'group-free manifest (shipped 1.4.1 form, unchanged)' "$OMD"
# round-10 [mutation testing, same class as round-9]: the two pins above (and their static-md.md/
# SKILL.md siblings) assert only APPLICABILITY ("any manifest"/"Wikilinks off, any manifest") —
# never the relative() formula's own arguments. A mutation changing BOTH bases from
# dirname(chapter_file) to dirname(index_file) leaves every applicability pin green while writing
# links in the wrong coordinate system. Pinned independently — a single shared needle would pass a
# mutation that corrupts only one of the two formulas.
has_in_section "obsidian-vault: wikilinks-off internal-link formula uses dirname(chapter_file)" \
  "$OMD" '## Wikilinks vs Markdown links' \
  '`[Display title](relative(dirname(chapter_file), <target-chapter-file>))`'
has_in_section "obsidian-vault: wikilinks-off glossary-link formula uses dirname(chapter_file)" \
  "$OMD" '## Glossary backlink discipline' \
  '`[TermHeading](relative(dirname(chapter_file), {{publish.glossary_dir}}/index.md)#termheading)`'
# round-7: the wikilinks-off fallback told authors to skip the Related block and glossary linking
# entirely, while a separate rule required every Related block to hold >=2 wikilinks with a halt —
# unsatisfiable when wikilinks are off, and contradicting the very canon #220 exists to ship. A4
# rewrote both sites to be link-format-neutral. The earlier assertions above only covered the
# section that round's edit touched, so this same contradiction sat unpinned for six more rounds.
#
# round-8 [mutation testing]: this assertion's LABEL always claimed "Related block + glossary",
# but its needle only named the Related block — a mutation that dropped just the glossary clause
# ("...still applies, but glossary linking is skipped") sailed through green. Widened the needle
# to carry the glossary clause the label always claimed, closing the gap between what it says and
# what it proves, rather than adding a second assertion that would only restate the same claim.
has_in_section "obsidian-vault: wikilinks-off fallback still covers Related block + glossary" \
  "$OMD" '## What "Obsidian vault" implies' \
  'glossary links, and the Related block below all still apply'
# round-8 [mutation testing]: A4's cross-reference to "Wikilinks vs Markdown links" is unpinned
# text — nothing caught a mutation that broke it to a nonexistent heading name, silently stranding
# a reader. Pinned at both sites that cite it (this bullet, and the Related-block rule below).
has_in_section "obsidian-vault: fallback bullet's cross-reference to Wikilinks vs Markdown links is intact" \
  "$OMD" '## What "Obsidian vault" implies' \
  '("Wikilinks vs Markdown links" below)'
has_in_section "obsidian-vault: Related-block link form is profile-driven, not wikilink-only" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'in whichever form the profile dictates'
# round-18: the old asymmetric citation ('...the full-target Markdown-link formula from
# "Wikilinks vs Markdown links" below when it is `false`...') pinned a rule that INLINED the
# chapter-form wikilink example ('- [[<chapter-slug>|Display text]]') as representative of a
# category that also holds glossary targets — fourth instance of the category-collapse shape (see
# the round-17 step-4 fix). A reader pattern-matching that example for a glossary line would write
# `[[Term|Term]]`, a link to a nonexistent note. A4 dropped the inline example entirely and cites
# "Wikilinks vs Markdown links" symmetrically for BOTH modes — that section resolves the syntax BY
# TARGET TYPE, so no single example stands in for the category. The needle below pins that
# property (by-target-type resolution, not one inlined instance), same reasoning as round 17's
# "the two target types use different formulas" pin.
has_in_section "obsidian-vault: Related-block link syntax resolved by target type, not one inlined example" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'below for the exact syntax, by target type, in each `publish.wikilinks` mode'
# Distinct claim from the one above — a future edit could make the rule format-neutral and still
# silently drop the >=2-link halt, so this is pinned separately rather than folded in.
#
# round-18 [the wrap trap hitting a pin, not a grep]: this needle broke not because the claim
# changed but because A4's shorter paragraph reflowed the wrap — 'Either way, you halt the publish
# step' and 'until at least two...' used to share one physical line and no longer do. The guarantee
# itself never moved. A needle chosen to sit on one physical line is only stable while the
# surrounding prose keeps its line breaks; any edit ABOVE it in the same paragraph can reflow the
# tail. Re-pinned on the trailing clause alone, which stays intact regardless of where the
# preceding sentence wraps — smaller and more self-contained than the original, not a guarantee
# against every future reflow, just a narrower target for one to land on.
has_in_section "obsidian-vault: the >=2-link Related-block halt survives the format-neutral rewording" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'until at least two outbound Related-block links exist'
# round-19 [IMPORTANT, zero prior coverage]: round 17's template-override fix had NO pin at all —
# confirmed by the reviewer directly: delete this instruction, restoring the exact prior paragraph,
# and the doc suite stayed green. That reintroduces the defect the commit closed: a wikilinks-off
# Obsidian author starts from `assets/chapter-template.md`, whose Related section is `[[…]]`-shaped
# unconditionally, and substitutes placeholders literally — shipping wikilink-syntax links in a
# Markdown-link chapter. Nothing downstream catches it: link-integrity item 2 verifies wikilink
# RESOLUTION, never syntax-against-mode. The load-bearing claim is the CONJUNCTION — under
# `publish.wikilinks: false` the template's placeholders must be overridden — not either half
# alone (a needle with only the mode, or only "override the placeholders", is satisfiable by a
# weaker sentence). The conjunction spans a wrap ('override the' ends one line, "template's
# `[[…]]` Related-block placeholders" starts the next — the same class of trap round 18 hit), so
# two needles, one per physical line, jointly proving what one could not without crossing it.
has_in_section "obsidian-vault: under wikilinks:false, the template's placeholders MUST be overridden (mode+verb half)" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'Under `publish.wikilinks: false`, override the'
has_in_section "obsidian-vault: ...specifically the template's [[…]] Related-block placeholders (object+form half)" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  "template's \`[[…]]\` Related-block placeholders with the standard Markdown-link form from"
# A4 renamed this checklist item's parenthetical from "(graph-island check)" to
# "(outbound-link floor)" (obsidian-vault.md:325) — the old name reasserted a wikilinks-on
# rationale for a rule that is mode-neutral (the same ≥2-link floor also gates wikilinks-off).
# Named mutation this catches: the label drifting/reverting back to a wikilinks-on-flavored
# name — the exact defect CLASS rounds 7 and 8 fixed, recurring at a third site. Not a collision
# risk against the legitimate "a graph island" phrase a few lines above (different sentence, no
# parens, describes the Obsidian graph view specifically, which really is wikilinks-only).
has_in_section "obsidian-vault: link-integrity gate's >=2-link item uses the mode-neutral label" \
  "$OMD" '## Link integrity gate before you publish' \
  '(outbound-link floor)'
hasnt "obsidian-vault: no longer labels the >=2-link item with a wikilinks-on-only rationale" \
  '(graph-island check)' "$OMD"
# The full retired phrase ("skip the wikilink-specific steps below (Related block, glossary
# linking syntax)") wraps across two physical lines in the pre-edit source (~90-col hard wrap,
# right after "wikilink-specific") — a fixed-string whole-file grep can never match a needle that
# spans a line break, so that exact phrase would never have gone red even pre-edit. Pinned on the
# single-line-safe back half instead — self-documenting (names the actual retired instruction:
# skipping the Related block and glossary linking entirely) and verified to discriminate both
# directions against the real pre-edit and post-edit text.
hasnt "obsidian-vault: no longer tells authors to skip wikilink-specific steps when wikilinks are off" \
  'steps below (Related block, glossary linking syntax)' "$OMD"

echo "== Package A/B/D regression sentinels (#49, #50, #51, #52, #71) =="
hasnt "no non-waiting isVisible after Escape"    'isVisible'                    "$CH"
has   "bounded hidden-wait after Escape"         "state: 'hidden', timeout:"    "$CH"
has   "states the Playwright 1.51 module minimum" 'Playwright >= 1.51'          "$CH"
hasnt "no stale six-branch guard-order comment"  'deny < eventsource'           "$CH"

echo "== #69: no residual 'fork it for other engines' wording =="
category_files 'assets/lib modules (fork-banner)' "$ASSETS/lib" -mindepth 1 -maxdepth 1 -type f \
  \( -name '*.mjs' -o -name '*.d.mts' \)   # UNION — both extensions, never just *.mjs (rev-13)
if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
  for f in "${CATEGORY_FILES[@]}"; do
    hasnt "no residual fork-it wording (banner): $(basename "$f")" 'Fork for other' "$f"
  done
fi
category_files 'assets/*.ts (fork-banner)' "$ASSETS" -maxdepth 1 -name '*.ts'
if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
  for f in "${CATEGORY_FILES[@]}"; do
    hasnt "no residual fork-it wording (banner): $(basename "$f")" 'Fork for other' "$f"
  done
fi
# $SKILL stays explicit (decision-5 touch point, not a directory category); references/ is walked
# RECURSIVELY — globstar is OFF (:20), so "$REFS"/**/*.md would silently mean one level (§1.2).
hasnt "no residual fork-it wording (prose): $(basename "$SKILL")" 'fork the asset' "$SKILL"
hasnt "no residual fork-it wording (prose): $(basename "$SKILL")" 'fork it for'    "$SKILL"
category_files 'references (recursive, fork-prose)' "$REFS" -type f -name '*.md'
if [ "${#CATEGORY_FILES[@]}" -gt 0 ]; then
  for f in "${CATEGORY_FILES[@]}"; do
    hasnt "no residual fork-it wording (prose): $(basename "$f")" 'fork the asset' "$f"
    hasnt "no residual fork-it wording (prose): $(basename "$f")" 'fork it for'    "$f"
  done
fi
has "capture-spec-helpers: read classifier is documented GraphQL-only" 'is GraphQL-only' "$REFS/capture-spec-helpers.md"

echo "== profile validator (#64) =="
SCHEMA="$ASSETS/profile.schema.json"
PV="$ASSETS/lib/profile-version.mjs"
PVMTS="$ASSETS/lib/profile-version.d.mts"
PVALID="$REFS/profile-validation.md"
[ -f "$SCHEMA" ] && ok "profile.schema.json exists" || bad "profile.schema.json missing"
has "profile-schema: pins profile_version const 1"          '"const": 1'                    "$SCHEMA"
has "profile-schema: inline closed to its 4 fields"         '"additionalProperties": false'  "$SCHEMA"
has "profile-schema: root stays open for sibling packages"  '"additionalProperties": true'   "$SCHEMA"
has "profile-schema: normative provenance note"             'NORMATIVE profile contract'     "$SCHEMA"
for k in '"profile_version"' '"language"' '"audience"' '"stack"' '"capture"' \
         '"publish"' '"diataxis"' '"style_guide"' '"glossary"'; do
  has "profile-schema: defines top-level key $k" "$k" "$SCHEMA"
done
has   "profile-schema: backend.type enum"                 '"laravel"'   "$SCHEMA"
has   "profile-schema: capture.engine enum"               '"playwright"' "$SCHEMA"
has   "profile-schema: publish.target shipped set"        '"static_md"' "$SCHEMA"
hasnt "profile-schema: no fabricated future publish target" '"confluence"' "$SCHEMA"
has "profile-version: exports readProfileVersion"         'export function readProfileVersion' "$PV"
has "profile-version: exports SUPPORTED_PROFILE_VERSIONS" 'SUPPORTED_PROFILE_VERSIONS'         "$PV"
has "profile-version: exports MIGRATIONS extension point" 'export const MIGRATIONS'            "$PV"
has "profile-version: optional CLI tail present"          'import.meta.url'                    "$PV"
has "profile-version.d.mts: declares ProfileVersionVerdict" 'ProfileVersionVerdict'             "$PVMTS"
has "profile-validation: supported-version list"          'Supported profile_version'          "$PVALID"
has "profile-validation: no fabricated v2 migration"      'no cross-version migration'          "$PVALID"
has "profile-validation: inline-stays-minimal decision"   'stays minimal'                      "$PVALID"
has "profile-validation: node helper is optional"         'optional'                           "$PVALID"
has "profile-validation: honest invariant quoted"         'pre-flight version reader, not a YAML validator' "$PVALID"
# #155 — the role_flags ⊆ auth_role_enum membership check is documented as a WARN-level Step-0 item.
# ERE (grep -E, NOT BRE); the needle is a single metachar-free phrase that must sit on ONE line in
# profile-validation.md (a needle split across a wrapped line would not match).
if grep -Eq 'A role_flags key that is not a declared role silently disables its capability gate' "$PVALID"; then
  ok "profile-validation: #155 role_flags-membership warn item present"
else
  bad "profile-validation: #155 role_flags-membership warn wording missing"
fi
has "skill: Step 0 validates against the schema"          'profile.schema.json'                "$SKILL"
has "skill: Step 0 points at the validation procedure"    'profile-validation.md'              "$SKILL"

echo "== scaffold-profile command (#66) =="
CMD="$PLUGIN_DIR/commands/scaffold-profile.md"
STUB="$ASSETS/style-guide.example.md"
if [ -f "$CMD" ]; then ok "scaffold-profile command exists"; else bad "scaffold-profile command missing"; fi
hasnt "scaffold-profile: normative (no reference-impl banner)" 'non-normative reference implementation' "$CMD"
has "scaffold-profile: reads the canonical profile example" 'handbook.profile.example.yml' "$CMD"
has "scaffold-profile: reads the style-guide stub template" 'style-guide.example.md' "$CMD"
has "scaffold-profile: writes profile.yml"      '.claude/handbook/profile.yml'    "$CMD"
has "scaffold-profile: writes style-guide stub" '.claude/handbook/style-guide.md' "$CMD"
has "scaffold-profile: refuses to clobber existing files" 'already exists' "$CMD"
has "scaffold-profile: references the Step 0 existence gates" 'Step 0' "$CMD"
has "scaffold-profile: allowed-tools is the safe set" 'allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion' "$CMD"
has "scaffold-profile: static target forces wikilinks false" 'wikilinks: false' "$CMD"
has "scaffold-profile: syncs capture.command locale (LC_ALL)" 'LC_ALL' "$CMD"
has "scaffold-profile: disable-model-invocation set" 'disable-model-invocation: true' "$CMD"
has_ci "scaffold-profile: detect-then-confirm discipline" 'confirm' "$CMD"
if [ -f "$STUB" ]; then ok "style-guide.example stub exists"; else bad "style-guide.example stub missing"; fi
has_ci "style-guide stub: covers register/address form" 'register' "$STUB"

echo "== state-variant capture (#67 CORE) =="
has "capture-helpers: IdentityOptions gains a state-variant marker" 'state?: { present' "$CH"
has "capture-helpers: assertIdentity supports a state-variant marker" 'wrong-state marker' "$CH"
if [ -f "$REFS/state-variants.md" ]; then ok "state-variants.md exists"; else bad "state-variants.md missing"; fi
has "capture-manifest example: optional states field for variant intent" 'states:' "$ASSETS/capture-manifest.example.yml"

echo "== capture-engine docs (#70) =="
if [ -f "$REFS/capture-engines.md" ]; then ok "capture-engines.md exists"; else bad "capture-engines.md missing"; fi
has "capture-engines: illustrative-not-tested banner" 'illustrative recipes, not tested contracts' "$REFS/capture-engines.md"
has "capture-engines: Cypress resourceType deprecation" '14.0.0' "$REFS/capture-engines.md"

echo "== surface-diff (per-role re-audit, #73) =="
SD="$ASSETS/lib/surface-diff.mjs"
SDDOC="$REFS/surface-diff.md"
has "surface-diff: exports diffSurfaces"  'export function diffSurfaces'  "$SD"
has "surface-diff: exports structuralKey" 'export function structuralKey' "$SD"
has "surface-diff: structural key order documented" '[tag, role, name, testId]' "$SD"
has "surface-diff.md: single-role remains the default"   'single-role' "$SDDOC"
has "surface-diff.md: documents the structural diff key"  'tag / role / name / data-testid' "$SDDOC"
hasnt "surface-diff: no import type in the .mjs" 'import type' "$SD"
has "surface-diff: imports matrixLabel from control-inventory" "from './control-inventory.mjs'" "$SD"

TOTAL=$((PASS + FAIL))
echo "----"
echo "TOTAL: $PASS/$TOTAL passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
