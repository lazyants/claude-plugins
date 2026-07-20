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
has_in_section() {
  local msg="$1" file="$2" heading="$3" needle="$4"
  if [ ! -f "$file" ]; then bad "$msg (file not found: $(basename "$file"))"; return; fi
  if awk -v heading="$heading" -v needle="$needle" '
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
     ' "$file"; then
    ok "$msg"
  else
    bad "$msg ('$needle' not found under heading '$heading' in $(basename "$file"))"
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

echo "== has_in_section self-test: backtick-fence info-string boundary (round-4 regression) =="
# Permanent boundary cases for the fence-opener fix above — synthetic fixtures, not project docs.
# Both are phrased as plain has_in_section (positive, "must be found") calls via a heading-boundary
# reformulation, so no extra assertion helper is needed: the bug under test is really about whether
# a REAL heading gets recognized as a section boundary, and that is directly, positively provable.
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
for f in \
  "$REFS/completeness-gate.md" \
  "$REFS/running-ui-source.md" \
  "$REFS/capture-safety.md" \
  "$REFS/manifest-discipline.md" \
  "$REFS/container-isolation.md" \
  "$REFS/capture-spec-helpers.md" \
  "$SKILL" \
  "$ASSETS/surface-audit.playwright.ts" \
  "$ASSETS/capture-helpers.playwright.ts" \
  "$ASSETS/capture.example.spec.ts" \
  "$ASSETS/lib/control-inventory.mjs" \
  "$ASSETS/lib/control-inventory.d.mts" \
  "$ASSETS/lib/capture-guard-policy.mjs" \
  "$ASSETS/lib/capture-guard-policy.d.mts" \
  "$ASSETS/lib/identity-match.mjs" \
  "$ASSETS/lib/identity-match.d.mts" \
  "$ASSETS/lib/graphql-read-classifier.mjs" \
  "$ASSETS/lib/graphql-read-classifier.d.mts" \
  "$ASSETS/lib/profile-version.mjs" \
  "$ASSETS/lib/profile-version.d.mts" \
  "$REFS/profile-validation.md" \
  "$REFS/capture-engines.md" \
  "$ASSETS/lib/surface-diff.mjs" \
  "$ASSETS/lib/surface-diff.d.mts" \
  "$ASSETS/lib/viewport-clip.mjs" \
  "$ASSETS/lib/viewport-clip.d.mts" \
  "$ASSETS/lib/dismiss-safe-label-policy.mjs" \
  "$ASSETS/lib/dismiss-safe-label-policy.d.mts" \
  "$ASSETS/reaudit.example.spec.ts" \
  "$REFS/surface-diff.md"; do
  base="$(basename "$f")"
  if [ ! -f "$f" ]; then bad "normative banner: $base does not exist yet"; continue; fi
  has "normative banner present: $base" "$NORMATIVE" "$f"
done

echo "== glossaryTerms fully renamed to glossary_terms =="
if grep -rn 'glossaryTerms' "$SKILL_DIR" "$PLUGIN_DIR/.claude-plugin" 2>/dev/null; then
  bad "glossaryTerms still present (must be glossary_terms)"
else
  ok "no glossaryTerms residue under plugins/enduser-handbook"
fi

echo "== executable unit tests (node --test) =="
if command -v node >/dev/null 2>&1; then
  for t in control-inventory.test.mjs capture-guard-policy.test.mjs identity-match.test.mjs graphql-read-classifier.test.mjs profile-version.test.mjs surface-diff.test.mjs viewport-clip.test.mjs dismiss-safe-label-policy.test.mjs chapter-paths.test.mjs; do
    if node --test "$TEST_DIR/$t" >/dev/null 2>&1; then
      ok "$t passes under node --test"
    else
      bad "$t FAILED under node --test"
    fi
  done
else
  echo "  note  node not on PATH — skipping the executable unit tests"
fi

echo "== optional local TypeScript syntax check =="
TS_FILES="$ASSETS/surface-audit.playwright.ts $ASSETS/capture-helpers.playwright.ts $ASSETS/capture.example.spec.ts $ASSETS/reaudit.example.spec.ts"
if command -v esbuild >/dev/null 2>&1; then
  ts_ok=1
  for f in $TS_FILES; do
    esbuild "$f" --bundle=false --log-level=silent --outfile=/dev/null >/dev/null 2>&1 || ts_ok=0
  done
  [ "$ts_ok" -eq 1 ] && ok "TypeScript parses under local esbuild" || bad "TypeScript syntax error under local esbuild"
elif npx --no-install esbuild --version >/dev/null 2>&1; then
  ts_ok=1
  for f in $TS_FILES; do
    npx --no-install esbuild "$f" --bundle=false --log-level=silent --outfile=/dev/null >/dev/null 2>&1 || ts_ok=0
  done
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
has "obsidian-vault: vault-boundary gate"                   'inside the active Obsidian vault'                          "$OMD"

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
# BOTH calls wrap mid-argument in this file (`### Grouped index wiring` is hard-wrapped, unlike the
# sections pinned earlier this round) — `locateChapterLine(indexLines,` ends one line and
# `expectedTarget)` starts the next; `containerTitleMatches(containerTitle,` ends one line and
# `entry)` starts the next. Neither full signature fits a single fixed-string needle. What's pinned
# instead: two needles per call, one per physical line, each covering one argument independently —
# so a mutation to EITHER argument is still caught, just not by one needle proving the whole
# signature at once. What this does NOT prove: that the two half-needles are adjacent lines of the
# SAME call (an adversarial rewrite that separated them further apart, keeping both halves
# individually true, would not be caught) — a narrower gap than round-9's SKILL.md pins, which each
# cover a complete signature in one shot.
has_in_section "static-md: step-0 idempotency check calls locateChapterLine, first arg indexLines" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'locateChapterLine(indexLines,'
has_in_section "static-md: step-0 idempotency check's locateChapterLine, second arg expectedTarget" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'expectedTarget)`'
has_in_section "static-md: step-0 idempotency check calls containerTitleMatches, first arg containerTitle" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'containerTitleMatches(containerTitle,'
has_in_section "static-md: step-0 idempotency check's containerTitleMatches, second arg entry (trimmed compare)" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'entry)`** (from `assets/lib/chapter-paths.mjs`; titles compare TRIMMED, not raw `===`)'
# R7-F1: wrong-container halt — never silently relocate a user-curated index line.
WRONG_CONTAINER_HALT="Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>'"
has "obsidian-vault: wrong-container halt" "$WRONG_CONTAINER_HALT" "$OMD"
has "static-md: wrong-container halt"      "$WRONG_CONTAINER_HALT" "$SMD"
# R7-F2: step-0's expected link target uses the same index-relative coordinate system as the TOC write.
# obsidian-vault.md has exactly ONE site for this formula (INDEX wiring), so the whole-file `has`
# below is a complete proof there — no per-site pin needed for that file.
has "obsidian-vault: index-relative expected-target formula" 'relative(dirname(index_file), chapter_file)' "$OMD"
# round-10 exhaustive sweep / round-11: static-md.md has THREE independent sites for this SAME
# formula, not the two originally flagged — the flat TOC-write (:231, "## Index wiring"), the
# grouped step-0 idempotency check's expected target (:255, "### Grouped index wiring", which
# explicitly reuses "the same coordinate system item 1 above uses" rather than an unrelated
# computation, but is still its own independent call site that must not drift from :231's write
# path or re-runs stop converging), and the link-integrity gate's item 5 (:417). This whole-file
# `has` is now SUBSUMED by the three per-site pins below — kept deliberately as a cheap early
# signal, not duplicate coverage to be "simplified" away; a mutation corrupting only one of the
# three sites would leave this needle green since the other two copies still match.
has "static-md: index-relative expected-target formula"      'relative(dirname(index_file), chapter_file)' "$SMD"
# Section nesting caught in mutation-testing: "## Index wiring" (:223) CONTAINS "### Grouped index
# wiring" (:243) as a subsection — has_in_section stops only at the same-or-shallower level, so a
# needle scoped to the H2 legitimately also scans everything inside its H3 subsection. The bare
# formula string alone can't tell :231's copy from :255's copy once both sit inside that scanned
# range, so a mutation touching ONLY :231 would have been invisible (255's untouched copy still
# satisfies the H2-scoped needle) — the exact cross-site-independence failure this round exists to
# prevent, just one level removed. Fixed by extending :231's needle with its own unique trailing
# context ("`. Order") that :255's differently-worded sentence does not share, so the two pins are
# now genuinely independent even though their sections nest.
has_in_section "static-md: flat TOC-write uses the index-relative expected-target formula" \
  "$SMD" '## Index wiring (do this on every chapter create/update)' \
  'relative(dirname(index_file), chapter_file)`. Order'
has_in_section "static-md: grouped step-0 idempotency check uses the same expected-target formula" \
  "$SMD" '### Grouped index wiring (`anyGroup` manifests only)' \
  'relative(dirname(index_file), chapter_file)'
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
has_in_section "revalidation: recipe step 4 embed rewrite is unconditional (all adapters, all modes)" \
  "$REVAL" '### The manual group-migration recipe' \
  'regardless of adapter or `publish.wikilinks` mode'
has_in_section "revalidation: recipe step 4 link rewrite states the wikilinks-on bare-link case" \
  "$REVAL" '### The manual group-migration recipe' \
  'as a bare `[[<slug>|Display title]]` wikilink under `publish.wikilinks: true`'
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
has_in_section "obsidian-vault: Related-block rule's cross-reference to Wikilinks vs Markdown links is intact" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'Markdown-link formula from "Wikilinks vs Markdown links" below when it is'
# Distinct claim from the one above — a future edit could make the rule format-neutral and still
# silently drop the >=2-link halt, so this is pinned separately rather than folded in.
has_in_section "obsidian-vault: the >=2-link Related-block halt survives the format-neutral rewording" \
  "$OMD" '## Chapter structure (Obsidian-flavoured)' \
  'Either way, you halt the publish step until at'
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
for f in \
  "$CH" \
  "$SA" \
  "$SPEC" \
  "$CI" \
  "$ASSETS/lib/control-inventory.d.mts" \
  "$POLICY" \
  "$ASSETS/lib/capture-guard-policy.d.mts" \
  "$ASSETS/lib/identity-match.mjs" \
  "$ASSETS/lib/identity-match.d.mts" \
  "$GQL" \
  "$ASSETS/lib/graphql-read-classifier.d.mts" \
  "$ASSETS/lib/viewport-clip.mjs" \
  "$ASSETS/lib/viewport-clip.d.mts" \
  "$ASSETS/lib/dismiss-safe-label-policy.mjs" \
  "$ASSETS/lib/dismiss-safe-label-policy.d.mts"; do
  hasnt "no residual fork-it wording (banner): $(basename "$f")" 'Fork for other' "$f"
done
for f in \
  "$SKILL" \
  "$REFS/completeness-gate.md" \
  "$REFS/running-ui-source.md" \
  "$REFS/container-isolation.md" \
  "$REFS/manifest-discipline.md" \
  "$REFS/capture-spec-helpers.md" \
  "$REFS/capture-safety.md"; do
  hasnt "no residual fork-it wording (prose): $(basename "$f")" 'fork the asset' "$f"
  hasnt "no residual fork-it wording (prose): $(basename "$f")" 'fork it for'    "$f"
done
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
