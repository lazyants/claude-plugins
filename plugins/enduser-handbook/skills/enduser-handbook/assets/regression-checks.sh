#!/bin/sh
# regression-checks.sh — measurable regression diff between a golden and a candidate chapter.
#
# GOLDEN SEMANTICS: <golden.md> must be the SAME chapter's prior version, not a different
# (sibling/exemplar) chapter. Signals 1 (Sie-count ±10%) and 3 (H2-count exact) are bound to a
# chapter's own length and structure, so comparing two different chapters fails them spuriously.
# Use this chapter-over-chapter (before/after an edit), never cross-chapter.
#
# Usage: ./regression-checks.sh <golden.md> <candidate.md>
#   EXPECTED_H1_WORD   word the candidate's H1 must contain (e.g. a project verb like
#                      "verwalten"). Unset by default — signal 7 is skipped unless you set it.
#
# Exit 0 if all 8 signals from plan Verification §1 pass; non-zero otherwise.
#
# The base skill ships this for the obsidian_vault + German formal-Sie reference case.
# For other publish targets / languages, fork and adjust the patterns. The eight signals
# encode the regression contract that opens the migration gate in Section E step 5.

set -u

# Locale safety so the literal „ U+201E byte sequence in the grep patterns is matched as
# bytes regardless of the user's LC_ALL. Fall back to C.UTF-8 then en_US.UTF-8 then C.
if locale -a 2>/dev/null | grep -qi '^C\.UTF-\?8$'; then
  LC_ALL=C.UTF-8
elif locale -a 2>/dev/null | grep -qi '^en_US\.UTF-\?8$'; then
  LC_ALL=en_US.UTF-8
else
  LC_ALL=C
fi
LANG=$LC_ALL
export LC_ALL LANG

if [ $# -ne 2 ]; then
  echo "Usage: $0 <golden.md> <candidate.md>" >&2
  exit 2
fi

GOLDEN=$1
CANDIDATE=$2

if [ ! -r "$GOLDEN" ]; then
  echo "FAIL: golden file not readable: $GOLDEN" >&2
  exit 2
fi
if [ ! -r "$CANDIDATE" ]; then
  echo "FAIL: candidate file not readable: $CANDIDATE" >&2
  exit 2
fi

EXPECTED_H1_WORD=${EXPECTED_H1_WORD:-}

PASS=0
FAIL=0

pass() {
  PASS=$((PASS + 1))
  echo "PASS [$1] $2"
}

fail() {
  FAIL=$((FAIL + 1))
  echo "FAIL [$1] $2"
}

# Count matching lines. grep -c prints "0" and exits 1 on no match, which under a
# future `set -e` would abort the script; `grep | wc -l` always exits 0 with a clean
# single value. The SC2126 "use grep -c" hint is deliberately declined for that reason.
# shellcheck disable=SC2126
count() {
  grep -- "$1" "$2" 2>/dev/null | wc -l | tr -d ' '
}

# shellcheck disable=SC2126
count_ere() {
  grep -E -- "$1" "$2" 2>/dev/null | wc -l | tr -d ' '
}

# 1) Sie-count within ±10% of golden (symmetric window: too few drops the register,
#    too many is over-saturation drift). Integer math; both bounds are 0 when golden is 0.
GOLDEN_SIE=$(count " Sie " "$GOLDEN")
CAND_SIE=$(count " Sie " "$CANDIDATE")
LOWER=$(( (GOLDEN_SIE * 9) / 10 ))
UPPER=$(( (GOLDEN_SIE * 11) / 10 ))
if [ "$CAND_SIE" -ge "$LOWER" ] && [ "$CAND_SIE" -le "$UPPER" ]; then
  pass 1 "' Sie ' count: candidate=$CAND_SIE golden=$GOLDEN_SIE (in [$LOWER, $UPPER])"
else
  fail 1 "' Sie ' count: candidate=$CAND_SIE outside [$LOWER, $UPPER] (golden=$GOLDEN_SIE)"
fi

# 2) At least one German opening quote „.
CAND_QUOTE=$(count "„" "$CANDIDATE")
if [ "$CAND_QUOTE" -ge 1 ]; then
  pass 2 "German „ present ($CAND_QUOTE occurrence(s))"
else
  fail 2 "German „ quote missing from candidate"
fi

# 3) H2 count matches golden exactly.
GOLDEN_H2=$(count "^## " "$GOLDEN")
CAND_H2=$(count "^## " "$CANDIDATE")
if [ "$CAND_H2" -eq "$GOLDEN_H2" ]; then
  pass 3 "H2 section count matches ($CAND_H2)"
else
  fail 3 "H2 section count: candidate=$CAND_H2 golden=$GOLDEN_H2"
fi

# 4) Both expected German section labels present as H2. Count each independently —
#    an OR-regex would pass on two copies of one label and zero of the other.
VORAUS=$(count_ere "^## Voraussetzungen" "$CANDIDATE")
VERWANDTE=$(count_ere "^## Verwandte Themen" "$CANDIDATE")
if [ "$VORAUS" -ge 1 ] && [ "$VERWANDTE" -ge 1 ]; then
  pass 4 "Voraussetzungen and Verwandte Themen H2 labels both present"
else
  fail 4 "expected both H2 labels; found Voraussetzungen=$VORAUS Verwandte Themen=$VERWANDTE"
fi

# 5) At least one screenshot embed ![…
CAND_IMG=$(count "!\[" "$CANDIDATE")
if [ "$CAND_IMG" -ge 1 ]; then
  pass 5 "screenshot embed present ($CAND_IMG)"
else
  fail 5 "no markdown image embed '![' found"
fi

# 6) At least one Obsidian wikilink [[…]].
CAND_WIKI=$(count "\[\[" "$CANDIDATE")
if [ "$CAND_WIKI" -ge 1 ]; then
  pass 6 "wikilink present ($CAND_WIKI)"
else
  fail 6 "no Obsidian wikilink '[[' found"
fi

# 7) H1 contains the expected word (project-specific verb). Skipped unless the caller
#    sets EXPECTED_H1_WORD — there is no sane generic default across projects/languages.
if [ -z "$EXPECTED_H1_WORD" ]; then
  pass 7 "H1 word check skipped (EXPECTED_H1_WORD not set)"
else
  H1_LINE=$(grep -m1 "^# " "$CANDIDATE" 2>/dev/null || true)
  if [ -z "$H1_LINE" ]; then
    fail 7 "no H1 line ('# ') found in candidate"
  elif echo "$H1_LINE" | grep -q "$EXPECTED_H1_WORD"; then
    pass 7 "H1 contains '$EXPECTED_H1_WORD'"
  else
    fail 7 "H1 missing '$EXPECTED_H1_WORD' (got: $H1_LINE)"
  fi
fi

# 8) Frontmatter declares language: de.
if grep -Eq "^language: de" "$CANDIDATE"; then
  pass 8 "frontmatter language: de"
else
  fail 8 "frontmatter line 'language: de' not found"
fi

TOTAL=$((PASS + FAIL))
echo "----"
echo "TOTAL: $PASS/$TOTAL passed, $FAIL failed"

if [ "$FAIL" -eq 0 ]; then
  exit 0
fi
exit 1
