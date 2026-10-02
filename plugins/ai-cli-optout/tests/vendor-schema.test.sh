#!/usr/bin/env bash
# Static invariants on every vendor JSON. Guards:
# - B1 regression: no shared / ancestor detect_paths (e.g. JetBrains root).
# - Dormant-platform: vendors issuing shell_commands[] must declare platforms.
# - Manual-only: manual_only vendors must carry manual_instructions +
#   process_check and MUST NOT define settings_files[].edits (the auto-edit
#   pathway must be unreachable by construction, not by Claude's judgment).
# - Dotted-path edits: keys like "env.DISABLE_TELEMETRY" — never literal nested objects.
# - Detection never uses persistent config/state as proof of installation.
# - Platform settings/process maps cover every declared platform.
# - Diff patterns match normalized candidates, which contain no backticks.

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
  "~/.claude"
  "~/.claude-bm"
  "~/.codex"
  "~/.gemini"
  "~/.cursor"
  "~/.vscode"
  "~/.antigravity"
  "~/.config/github-copilot"
  "~/.config/gh"
  "~/Library/Application Support/Cursor"
  "~/Library/Application Support/Code"
  "~/Library/Application Support/Antigravity"
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

# A fallback must name the executable itself or an actual macOS app bundle.
# Checking existence/executability is the skill's responsibility; this static
# contract prevents new vendor-specific state directories bypassing the list.
DETECT_PATHS_VALID='all(.detect_paths[]?;
  . as $path | ($path | gsub("\\\\"; "/")) as $normalized
  | ($normalized | split("/")[-1] | ascii_downcase) as $leaf
  | ($vendor.detect_cmd | ascii_downcase) as $cmd
  | ($leaf | endswith(".app")) or
    ($cmd != "" and
      (($leaf == $cmd and ($normalized | contains("/bin/"))) or
       $leaf == ($cmd + ".exe") or
       ($leaf == ($cmd + ".cmd") and ($normalized | contains("/bin/"))))))'

valid_detect_paths() {
  jq -e --argjson vendor "$1" "$DETECT_PATHS_VALID" <<< "$1" >/dev/null
}

invalid_detect_paths() {
  ! valid_detect_paths "$1"
}

assert "detection guard rejects vendor state directory" \
  invalid_detect_paths '{"detect_cmd":"claude","detect_paths":["~/.claude"]}'
assert "detection guard rejects config dir named like binary" \
  invalid_detect_paths '{"detect_cmd":"gh","detect_paths":["~/.config/gh"]}'
assert "detection guard rejects editor support directory" \
  invalid_detect_paths '{"detect_cmd":"cursor","detect_paths":["~/Library/Application Support/Cursor"]}'
assert "detection guard rejects vendor config file" \
  invalid_detect_paths '{"detect_cmd":"claude","detect_paths":["~/.claude/settings.json"]}'
assert "detection guard permits alternate executable and app bundle" \
  valid_detect_paths '{"detect_cmd":"cursor","detect_paths":["~/.local/bin/cursor","/Applications/Cursor.app"]}'

for config in "$VENDORS_DIR"/*.json; do
  name="$(basename "$config" .json)"
  echo "-- $name"

  assert "parses as JSON"                  jq -e . "$config"
  assert "has .name"                       test -n "$(jq -r '.name // empty' "$config")"
  assert "has .display"                    test -n "$(jq -r '.display // empty' "$config")"
  assert "has .detect_cmd (string)"        test "$(jq -r '.detect_cmd | type' "$config")" = "string"
  assert "has an executable or compound install check" jq -e \
    '(.detect_cmd | type == "string" and length > 0) or
     ((.detect_check | type) == "string" and (.detect_check | length) > 0) or
     ((.detect_check | type) == "object" and (.detect_check | length) > 0 and
       all(.detect_check[]; type == "string" and length > 0))' "$config"
  assert "has .detect_paths (array)"       test "$(jq -r '.detect_paths | type' "$config")" = "array"
  assert "has .doc_urls (array)"           test "$(jq -r '.doc_urls | type' "$config")" = "array"

  # Forbidden / shared detect_paths (B1 regression guard)
  for forbidden in "${FORBIDDEN_DETECT_PATHS[@]}"; do
    hit="$(jq -r --arg p "$forbidden" '.detect_paths[]? | select(. == $p)' "$config")"
    assert "detect_paths excludes shared path '$forbidden'" test -z "$hit"
  done
  assert "detect_paths name executable or app bundle, never state" \
    jq -e --argjson vendor "$(jq -c . "$config")" "$DETECT_PATHS_VALID" "$config"

  assert "settings_regex contains no literal backticks" jq -e \
    '(.diff_patterns.settings_regex // "") | contains("`") | not' "$config"

  # A settings target is either one common path or an explicit platform map.
  assert "settings files define path xor paths" jq -e \
    'all(.settings_files[]?; has("path") != has("paths"))' "$config"
  assert "settings path is nonempty" jq -e \
    'all(.settings_files[]? | select(has("path")); .path | type == "string" and length > 0)' "$config"
  assert "settings paths map contains valid platform strings" jq -e --argjson valid "$VALID_PLATFORMS" \
    'all(.settings_files[]? | select(has("paths"));
      (.paths | type == "object") and (.paths | length > 0) and
      ((.paths | keys) - $valid | length == 0) and
      all(.paths[]; type == "string" and length > 0))' "$config"
  assert "settings paths cover every declared platform" jq -e \
    '.platforms as $platforms | all(.settings_files[]? | select(has("paths"));
      .paths as $paths | all($platforms[]?; $paths[.] != null))' "$config"
  assert "settings key_mode is literal or dotted" jq -e \
    'all(.settings_files[]?; (.key_mode // "dotted") as $mode | $mode == "literal" or $mode == "dotted")' "$config"
  assert "existing_only is boolean when present" jq -e \
    'all(.settings_files[]? | select(has("existing_only")); .existing_only | type == "boolean")' "$config"

  if [ "$(jq -r '.detect_check | type' "$config")" = "object" ]; then
    assert "detect_check map has supported platform keys" jq -e --argjson valid "$VALID_PLATFORMS" \
      '((.detect_check | keys) - $valid | length == 0)' "$config"
    assert "detect_check map covers declared platforms" jq -e \
      '.detect_check as $checks | all(.platforms[]?; $checks[.] != null)' "$config"
  fi

  assert "install_check maps contain commands for declared platforms" jq -e --argjson valid "$VALID_PLATFORMS" \
    '.platforms as $platforms | all(.settings_files[]? | select(has("install_check"));
      (.install_check | type == "object") and (.install_check | length > 0) and
      ((.install_check | keys) - $valid | length == 0) and
      all(.install_check[]; type == "string" and length > 0) and
      (.install_check as $checks | all($platforms[]?; $checks[.] != null)))' "$config"

  assert "profile metadata only targets existing TOML profiles" jq -e \
    'all(.settings_files[]? | select(has("profile_files") or has("profile_edits"));
      .format == "toml" and
      ((has("profile_files") | not) or
       (.profile_files.pattern == "*.config.toml" and .profile_files.existing_only == true and
        (.profile_files.edits | type == "array" and length > 0))))' "$config"
  bad_profile_keys="$(jq -r --arg re "$DOTTED_KEY_RE" \
    '.settings_files[]? | (.profile_files.edits[]?, .profile_edits[]?) | .key | select(test($re) | not)' "$config")"
  assert "profile edits carry dotted keys" test -z "$bad_profile_keys"

  if [ "$(jq -r '.process_check | has("commands")' "$config" 2>/dev/null)" = "true" ]; then
    assert "process command map contains valid platform commands" jq -e --argjson valid "$VALID_PLATFORMS" \
      '(.process_check.commands | type == "object") and
       ((.process_check.commands | keys) - $valid | length == 0) and
       all(.process_check.commands[]; type == "string" and length > 0)' "$config"
    assert "process command map covers declared platforms" jq -e \
      '.process_check.commands as $commands | all(.platforms[]?; $commands[.] != null)' "$config"
  fi

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
    assert "manual_only → zero auto-edit entries" \
      test "$(jq -r '[.settings_files[]?.edits[]?] | length' "$config")" -eq 0
    if [ "$name" = "cursor" ]; then
      assert "Cursor file-edit guidance → has process check" \
        test -n "$(jq -r '.process_check.cmd // empty' "$config")"
    fi
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

# Regressions exercise the shipped editor commands against isolated process and
# installation fixtures; no probe reaches a user's real editor or settings.
FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
mkdir -p "$FIXTURE_DIR/bin"
for fixture_tool in jq readlink dirname grep; do
  ln -s "$(command -v "$fixture_tool")" "$FIXTURE_DIR/bin/$fixture_tool"
done
cat > "$FIXTURE_DIR/bin/pgrep" <<'EOF'
#!/usr/bin/env bash
[ -z "${FIXTURE_PROCESS_ERROR:-}" ] || exit "$FIXTURE_PROCESS_ERROR"
if [ "$1" = "-ix" ]; then
  printf '%s\n' "${FIXTURE_PROCESSES:-}" | grep -Eiq "^($2)$"
else
  printf '%s\n' "${FIXTURE_PROCESSES:-}" | grep -Eiq "$2"
fi
EOF
chmod +x "$FIXTURE_DIR/bin/pgrep"
# The pgrep fixture uses env bash so keep its interpreter available in PATH.
ln -s "$BASH" "$FIXTURE_DIR/bin/bash"

process_status() {
  local vendor="$1" platform="$2" rows="$3" error="${4:-}"
  local cmd
  cmd="$(jq -r --arg platform "$platform" '.process_check.commands[$platform]' "$VENDORS_DIR/$vendor.json")"
  PATH="$FIXTURE_DIR/bin" FIXTURE_PROCESSES="$rows" FIXTURE_PROCESS_ERROR="$error" "$BASH" -c "$cmd"
}

for editor in code antigravity; do
  assert "$editor covers macOS Linux and Windows" jq -e \
    '.platforms | sort == ["darwin","linux","win32"]' "$VENDORS_DIR/$editor.json"
  assert "$editor preserves literal telemetry setting IDs" jq -e \
    'all(.settings_files[]; .key_mode == "literal")' "$VENDORS_DIR/$editor.json"
  assert "$editor Windows process check uses native PowerShell" jq -e \
    '.process_check.commands.win32 | startswith("powershell.exe -NoProfile -Command ") and contains("exit 2")' "$VENDORS_DIR/$editor.json"
done
assert_eq "VS Code Linux process recognized" 0 "$(process_status code linux code; echo $?)"
assert_eq "VS Code Linux stopped recognized" 1 "$(process_status code linux unrelated; echo $?)"
assert_eq "VS Code Linux process error propagated" 2 "$(process_status code linux unrelated 2; echo $?)"
assert_eq "VS Code macOS executable path recognized" 0 \
  "$(process_status code darwin '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'; echo $?)"
assert_eq "VS Code process check cannot match its own command text" 1 \
  "$(process_status code darwin "pgrep -if '/Visual Studio Code[.]app/Contents/'"; echo $?)"
assert_eq "legacy Antigravity Linux process recognized" 0 "$(process_status antigravity linux antigravity; echo $?)"
assert_eq "current Antigravity IDE Linux process recognized" 0 "$(process_status antigravity linux antigravity-ide; echo $?)"
assert_eq "Antigravity stopped recognized" 1 "$(process_status antigravity linux unrelated; echo $?)"
assert_eq "Antigravity process error propagated" 2 "$(process_status antigravity linux unrelated 2; echo $?)"
assert_eq "current Antigravity macOS executable path recognized" 0 \
  "$(process_status antigravity darwin '/Applications/Antigravity IDE.app/Contents/MacOS/Electron'; echo $?)"
assert_eq "Antigravity process check cannot match its own command text" 1 \
  "$(process_status antigravity darwin "pgrep -if '/[A]ntigravity( IDE)?[.]app/Contents/'"; echo $?)"

install_status() {
  local entry="$1" probe_shell="${2:-$BASH}" cmd
  cmd="$(jq -r --argjson entry "$entry" '.settings_files[$entry].install_check.linux' "$VENDORS_DIR/antigravity.json")"
  PATH="$FIXTURE_DIR/bin" "$probe_shell" -c "$cmd"
}

for entry in 0 1; do
  if [ "$entry" -eq 0 ]; then
    product="Antigravity"; binary="antigravity"
  else
    product="Antigravity IDE"; binary="antigravity-ide"
  fi
  mkdir -p "$FIXTURE_DIR/$binary/bin" "$FIXTURE_DIR/$binary/resources/app"
  printf '#!/bin/sh\nexit 0\n' > "$FIXTURE_DIR/$binary/bin/$binary"
  chmod +x "$FIXTURE_DIR/$binary/bin/$binary"
  ln -s "$FIXTURE_DIR/$binary/bin/$binary" "$FIXTURE_DIR/bin/$binary"
  assert_eq "$product launcher alone is insufficient installation proof" 1 "$(install_status "$entry"; echo $?)"
  printf '{"nameShort":"Unrelated standalone app","applicationName":"%s"}\n' "$binary" \
    > "$FIXTURE_DIR/$binary/resources/app/product.json"
  assert_eq "$product rejects a sibling product marker" 1 "$(install_status "$entry"; echo $?)"
  printf '{"nameShort":"%s","applicationName":"%s"}\n' "$product" "$binary" \
    > "$FIXTURE_DIR/$binary/resources/app/product.json"
  assert_eq "$product executable plus exact product marker recognized" 0 "$(install_status "$entry"; echo $?)"
  if command -v zsh >/dev/null; then
    assert_eq "$product installation probe works in zsh" 0 "$(install_status "$entry" "$(command -v zsh)"; echo $?)"
  fi
  printf '{invalid-json}\n' > "$FIXTURE_DIR/$binary/resources/app/product.json"
  assert_eq "$product malformed marker is an error rather than absent" 2 "$(install_status "$entry" 2>/dev/null; echo $?)"
done

assert "Anthropic ships only the standard settings target" jq -e \
  '[.settings_files[].path] == ["~/.claude/settings.json"]' "$VENDORS_DIR/anthropic.json"
assert "Codex profile edits contain analytics only" jq -e \
  'all(.settings_files[]; [.profile_files.edits[].key] == ["analytics.enabled"] and
    [.profile_edits[].key] == ["analytics.enabled"] and .home_env == "CODEX_HOME")' "$VENDORS_DIR/codex.json"

if [ "$TESTS_FAILED" -eq 0 ]; then
  echo "vendor-schema: $TESTS_RUN ok"
else
  echo "vendor-schema: $TESTS_FAILED / $TESTS_RUN failed" >&2
fi
exit "$TESTS_FAILED"
