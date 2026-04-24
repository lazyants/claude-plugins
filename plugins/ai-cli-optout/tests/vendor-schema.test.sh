#!/usr/bin/env bash
# Static invariants on every vendor JSON. Guards:
# - B1 regression: no shared / ancestor detect_paths (e.g. JetBrains root).
# - Dormant-platform: vendors issuing shell_commands[] must declare platforms.
# - Manual-only: manual_only vendors must carry manual_instructions +
#   process_check and MUST NOT define settings_files[].edits (the auto-edit
#   pathway must be unreachable by construction, not by Claude's judgment).
# - Dotted-path edits: keys like "env.DISABLE_TELEMETRY" — never literal nested objects.

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$TEST_DIR/lib.sh"
VENDORS_DIR="$(cd "$TEST_DIR/../skills/ai-cli-optout/vendors" && pwd)"

command -v jq >/dev/null 2>&1 || { echo "jq required (brew install jq)"; exit 2; }

# Paths that MUST NOT appear in any detect_paths — all are shared with
# unrelated apps and cause false detection. Add new traps here as they're found.
FORBIDDEN_DETECT_PATHS=(
  "~/Library/Application Support/JetBrains"
  "~/.config/JetBrains"
  "~/Library/Application Support"
  "~/Library/Caches"
  "~/Library/Preferences"
  "~/Library"
  "~/.config"
  "~/.cache"
  "~/.local"
  "~/.local/share"
  "~"
  "/Applications"
  "/Library"
  "/opt"
  "/usr/local"
  "/usr/local/bin"
  "/etc"
  "/var"
  "/tmp"
)

VALID_PLATFORMS='["darwin","linux","win32"]'
DOTTED_KEY_RE='^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$'

for config in "$VENDORS_DIR"/*.json; do
  name="$(basename "$config" .json)"
  echo "-- $name"

  assert "parses as JSON"                  jq -e . "$config"
  assert "has .name"                       test -n "$(jq -r '.name // empty' "$config")"
  assert "has .display"                    test -n "$(jq -r '.display // empty' "$config")"
  assert "has .detect_cmd (string)"        test "$(jq -r '.detect_cmd | type' "$config")" = "string"
  assert "has .detect_paths (array)"       test "$(jq -r '.detect_paths | type' "$config")" = "array"
  assert "has .doc_urls (array)"           test "$(jq -r '.doc_urls | type' "$config")" = "array"

  # Forbidden / shared detect_paths (B1 regression guard)
  for forbidden in "${FORBIDDEN_DETECT_PATHS[@]}"; do
    hit="$(jq -r --arg p "$forbidden" '.detect_paths[]? | select(. == $p)' "$config")"
    assert "detect_paths excludes shared path '$forbidden'" test -z "$hit"
  done

  # Platforms — if present, MUST be an array (scalar "darwin" would crash the
  # set-subtraction below silently, so guard first), with values restricted.
  has_platforms="$(jq -r 'has("platforms")' "$config")"
  if [ "$has_platforms" = "true" ]; then
    platforms_type="$(jq -r '.platforms | type' "$config")"
    assert "platforms is an array (not scalar)" test "$platforms_type" = "array"
    if [ "$platforms_type" = "array" ]; then
      bad="$(jq -r --argjson valid "$VALID_PLATFORMS" '.platforms - $valid | .[]' "$config" 2>/dev/null)"
      assert "platforms values are darwin/linux/win32" test -z "$bad"
    fi
  fi

  # Dotted-path edit keys
  bad_keys="$(jq -r --arg re "$DOTTED_KEY_RE" \
    '.settings_files[]?.edits[]?.key | select(test($re) | not)' "$config")"
  assert "all edits[].key are dotted paths" test -z "$bad_keys"

  # Confirmation gate: requires_confirmation=true MUST carry a non-empty
  # tradeoff_note so the user sees what they are trading off. Otherwise the
  # gate is silent-consent theater.
  bad_gates="$(jq -r '.settings_files[]?.edits[]?
    | select(.requires_confirmation == true)
    | select((.tradeoff_note // "") | length == 0)
    | .key' "$config")"
  assert "requires_confirmation edits carry non-empty tradeoff_note" test -z "$bad_gates"

  # manual_only invariants
  if [ "$(jq -r '.manual_only // false' "$config")" = "true" ]; then
    assert "manual_only → non-empty manual_instructions" \
      test "$(jq -r '(.manual_instructions // []) | length' "$config")" -gt 0
    assert "manual_only → has process_check.cmd" \
      test -n "$(jq -r '.process_check.cmd // empty' "$config")"
    assert "manual_only → zero auto-edit entries" \
      test "$(jq -r '[.settings_files[]?.edits[]?] | length' "$config")" -eq 0
  fi

  # shell_commands require platforms gating — otherwise defaults write / reg add
  # can fire on the wrong OS.
  if [ "$(jq -r '(.shell_commands // []) | length' "$config")" -gt 0 ]; then
    assert "shell_commands → platforms non-empty" \
      test "$(jq -r '(.platforms // []) | length' "$config")" -gt 0
  fi

  # cli_commands shape — each entry must carry .cmd and .disables so the skill
  # can render "what/why" before asking the user to run it.
  bad_cli="$(jq -r '.cli_commands[]?
    | select((.cmd // "") == "" or (.disables // "") == "")' "$config")"
  assert "cli_commands entries carry .cmd and .disables" test -z "$bad_cli"
done

if [ "$TESTS_FAILED" -eq 0 ]; then
  echo "vendor-schema: $TESTS_RUN ok"
else
  echo "vendor-schema: $TESTS_FAILED / $TESTS_RUN failed" >&2
fi
exit "$TESTS_FAILED"
