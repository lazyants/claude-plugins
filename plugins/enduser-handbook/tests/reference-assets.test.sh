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

# Count exact occurrences of a fixed string in a file (line-based).
count_fixed() {
  grep -cF -- "$1" "$2" 2>/dev/null || echo 0
}

# Line number of the first match of a fixed string, or empty if absent.
line_of() {
  grep -nF -- "$1" "$2" 2>/dev/null | head -n1 | cut -d: -f1
}

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
has "capture-helpers: safe-negative allowlist (DEFAULT_SAFE_LABELS)" 'DEFAULT_SAFE_LABELS' "$CH"
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
has "surface-audit: className in the matrix-label fallback"       'className' "$SA"
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
  "$ASSETS/lib/graphql-read-classifier.d.mts"; do
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
  for t in control-inventory.test.mjs capture-guard-policy.test.mjs identity-match.test.mjs graphql-read-classifier.test.mjs; do
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
TS_FILES="$ASSETS/surface-audit.playwright.ts $ASSETS/capture-helpers.playwright.ts $ASSETS/capture.example.spec.ts"
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

TOTAL=$((PASS + FAIL))
echo "----"
echo "TOTAL: $PASS/$TOTAL passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
