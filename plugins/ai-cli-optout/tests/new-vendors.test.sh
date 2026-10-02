#!/usr/bin/env bash
# Exercise shipped detection/command strings against isolated command fixtures.
# No real vendor CLI, account, desktop setting or system service is modified.
set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$TEST_DIR/lib.sh"
VENDORS_DIR="$(cd "$TEST_DIR/../skills/ai-cli-optout/vendors" && pwd)"
BASH_BIN="$(command -v bash)"
JQ_BIN="$(command -v jq)"
GREP_BIN="$(command -v grep)"
FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT
FIXTURE_BIN="$FIXTURE_ROOT/bin"
mkdir "$FIXTURE_BIN"
ln -s "$JQ_BIN" "$FIXTURE_BIN/jq"
ln -s "$GREP_BIN" "$FIXTURE_BIN/grep"

rejects() {
  if "$@"; then return 1; else return 0; fi
}

cat > "$FIXTURE_BIN/claude" <<'STUB'
#!/bin/sh
[ "$1 $2 $3" = 'plugin list --json' ] || exit 2
printf '%s\n' "$CLAUDE_LIST_JSON"
exit "${CLAUDE_LIST_EXIT:-0}"
STUB
chmod +x "$FIXTURE_BIN/claude"
VERCEL_CHECK="$(jq -r '.detect_check' "$VENDORS_DIR/vercel-plugin.json")"
vercel_detect() {
  CLAUDE_LIST_JSON="$1" CLAUDE_LIST_EXIT="${2:-0}" \
    PATH="$FIXTURE_BIN" "$BASH_BIN" -c "$VERCEL_CHECK"
}

assert "Vercel manifest-name registration is detected" vercel_detect \
  '[{"id":"vercel@claude-plugins-official","enabled":true}]'
assert "Vercel marketplace-name registration is detected" vercel_detect \
  '[{"id":"vercel-plugin@vercel","enabled":true}]'
assert "disabled Vercel registration is skipped" rejects vercel_detect \
  '[{"id":"vercel@claude-plugins-official","enabled":false}]'
assert "unrelated Claude plugins do not imply Vercel presence" rejects vercel_detect \
  '[{"id":"context7@claude-plugins-official","enabled":true}]'
assert "adjacent Vercel names do not collide" rejects vercel_detect \
  '[{"id":"vercel-cli@other","enabled":true},{"id":"my-vercel@other","enabled":true}]'
assert "empty Claude plugin inventory is skipped" rejects vercel_detect '[]'
assert "malformed Claude inventory fails detection" rejects vercel_detect '{broken'
assert "failed Claude inventory with matching stdout fails closed" rejects vercel_detect \
  '[{"id":"vercel@vercel","enabled":true}]' 2
mv "$FIXTURE_BIN/claude" "$FIXTURE_ROOT/claude.saved"
assert "missing Claude CLI cannot detect the plugin" rejects vercel_detect \
  '[{"id":"vercel@vercel","enabled":true}]'
mv "$FIXTURE_ROOT/claude.saved" "$FIXTURE_BIN/claude"
assert "Vercel plugin never falls back to CLI/config state" jq -e \
  '.detect_cmd == "" and .detect_paths == []' "$VENDORS_DIR/vercel-plugin.json"
assert "Vercel plugin and CLI use separate opt-out variables" jq -e -s \
  '([.[0].shell_env_vars[].name] - [.[1].shell_env_vars[].name]) | length > 0' \
  "$VENDORS_DIR/vercel-plugin.json" "$VENDORS_DIR/vercel.json"

cat > "$FIXTURE_BIN/code" <<'STUB'
#!/bin/sh
[ "$1" = '--list-extensions' ] || exit 2
printf '%s\n' "$EDITOR_LIST"
exit "${EDITOR_LIST_EXIT:-0}"
STUB
chmod +x "$FIXTURE_BIN/code"
WINDSURF_CHECK="$(jq -r '.detect_check' "$VENDORS_DIR/windsurf.json")"
windsurf_detect() {
  EDITOR_LIST="$1" EDITOR_LIST_EXIT="${2:-0}" \
    PATH="$FIXTURE_BIN" "$BASH_BIN" -c "$WINDSURF_CHECK"
}
assert "Codeium extension alone is detected without Windsurf" windsurf_detect \
  $'ms-python.python\nCodeium.codeium'
assert "other extensions do not imply Codeium installation" rejects windsurf_detect \
  $'ms-python.python\nGitHub.copilot'
assert "adjacent Codeium extension names do not collide" rejects windsurf_detect \
  'codeium.codeium-extra'
assert "failed editor inventory with matching stdout fails closed" rejects windsurf_detect \
  'codeium.codeium' 2
mv "$FIXTURE_BIN/code" "$FIXTURE_ROOT/code.saved"
printf '#!/bin/sh\nexit 0\n' > "$FIXTURE_BIN/devin"
chmod +x "$FIXTURE_BIN/devin"
assert "separate Devin CLI alone does not detect desktop" rejects windsurf_detect ''
printf '#!/bin/sh\nexit 0\n' > "$FIXTURE_BIN/devin-desktop"
chmod +x "$FIXTURE_BIN/devin-desktop"
assert "documented renamed desktop launcher is detected" windsurf_detect ''
rm "$FIXTURE_BIN/devin-desktop"
printf '#!/bin/sh\nexit 0\n' > "$FIXTURE_BIN/windsurf"
chmod +x "$FIXTURE_BIN/windsurf"
assert "legacy Windsurf launcher is detected" windsurf_detect ''
rm "$FIXTURE_BIN/windsurf"

cat > "$FIXTURE_BIN/flatpak" <<'STUB'
#!/bin/sh
printf '%s\n' "$*" >> "$FLATPAK_TEST_LOG"
[ "$1" = config ] && [ "$2" = --user ] || exit 2
[ "$4" = report-os-info ] || exit 2
if [ "$3" = --get ]; then
  [ "${FLATPAK_SUPPORTS_KEY:-1}" = 1 ] || exit 2
  printf 'true\n'
elif [ "$3" = --set ]; then
  [ "$5" = false ] || exit 2
else
  exit 2
fi
STUB
chmod +x "$FIXTURE_BIN/flatpak"
FLATPAK_COMMAND="$(jq -r '.cli_commands[0].cmd' "$VENDORS_DIR/linux-privacy.json")"
FLATPAK_LOG_PATH="$FIXTURE_ROOT/flatpak.log"
flatpak_optout() {
  FLATPAK_TEST_LOG="$FLATPAK_LOG_PATH" FLATPAK_SUPPORTS_KEY="$1" \
    PATH="$FIXTURE_BIN" "$BASH_BIN" -c "$FLATPAK_COMMAND"
}
assert "Flatpak per-user opt-out executes on supported installation" flatpak_optout 1
FLATPAK_LOG="$(cat "$FLATPAK_LOG_PATH")"
assert_contains "Flatpak command checks key support before write" \
  'config --user --get report-os-info' "$FLATPAK_LOG"
assert_contains "Flatpak command disables documented header for user scope" \
  'config --user --set report-os-info false' "$FLATPAK_LOG"
assert_not_contains "Flatpak command does not mutate system scope" '--system' "$FLATPAK_LOG"
: > "$FLATPAK_LOG_PATH"
assert "unsupported Flatpak key fails without a write" rejects flatpak_optout 0
FLATPAK_LOG="$(cat "$FLATPAK_LOG_PATH")"
assert_not_contains "unsupported Flatpak release never runs --set" '--set' "$FLATPAK_LOG"
mv "$FIXTURE_BIN/flatpak" "$FIXTURE_ROOT/flatpak.saved"
: > "$FLATPAK_LOG_PATH"
assert "missing Flatpak command fails without mutation" rejects flatpak_optout 1
assert "missing Flatpak command writes no fixture log" test ! -s "$FLATPAK_LOG_PATH"

# Verify Windows shell quoting without executing PowerShell on the local host.
cat > "$FIXTURE_BIN/powershell.exe" <<'STUB'
#!/bin/sh
printf '%s\n' "$@" > "$POWERSHELL_TEST_LOG"
exit "${POWERSHELL_TEST_EXIT:-0}"
STUB
chmod +x "$FIXTURE_BIN/powershell.exe"
for vendor in zed ollama windsurf; do
  PROCESS_COMMAND="$(jq -r '.process_check.commands.win32' "$VENDORS_DIR/$vendor.json")"
  POWERSHELL_LOG_PATH="$FIXTURE_ROOT/$vendor.powershell.log"
  POWERSHELL_TEST_LOG="$POWERSHELL_LOG_PATH" PATH="$FIXTURE_BIN" \
    "$BASH_BIN" -c "$PROCESS_COMMAND"
  PROCESS_PAYLOAD="$(cat "$POWERSHELL_LOG_PATH")"
  assert_contains "$vendor Windows command preserves PowerShell variables" '$_.ProcessName' "$PROCESS_PAYLOAD"
  assert_contains "$vendor Windows command preserves assignment variables" '$running' "$PROCESS_PAYLOAD"
  assert_contains "$vendor Windows query failures remain distinguishable" 'catch { exit 2 }' "$PROCESS_PAYLOAD"
  POWERSHELL_TEST_LOG="$POWERSHELL_LOG_PATH" POWERSHELL_TEST_EXIT=2 PATH="$FIXTURE_BIN" \
    "$BASH_BIN" -c "$PROCESS_COMMAND"
  PROCESS_STATUS=$?
  assert_eq "$vendor Windows query error status propagates" 2 "$PROCESS_STATUS"
done

assert "Ollama has no automatic server edits or unconditional cloud export" jq -e \
  '.manual_only == true and .settings_files == [] and .shell_env_vars == []
   and any(.caveats[]; .var == "OLLAMA_NO_CLOUD" and .value == "1" and .auto_apply == false)' \
  "$VENDORS_DIR/ollama.json"

if [ "$TESTS_FAILED" -eq 0 ]; then
  echo "new-vendors: $TESTS_RUN ok"
else
  echo "new-vendors: $TESTS_FAILED / $TESTS_RUN failed" >&2
fi
exit "$TESTS_FAILED"
