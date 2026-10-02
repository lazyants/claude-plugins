#!/usr/bin/env bash
# Smoke tests for the two bash scripts shipped with the skill:
# - report_persistent_files.sh reads real paths under $HOME and must tolerate
#   both empty (paths missing) and populated (paths present) cases.
# - check_new_optouts.sh fetches docs via curl and must flag tokens that appear
#   in docs but not in the vendor's baseline. Tested via file:// so no network.

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$TEST_DIR/lib.sh"
SKILL_DIR="$(cd "$TEST_DIR/../skills/ai-cli-optout" && pwd)"

command -v jq >/dev/null 2>&1 || { echo "jq required"; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl required"; exit 2; }

tmp="$(mktemp -d -t ai-cli-optout-test.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

echo "== report_persistent_files.sh =="

# Empty fake HOME → path reported as not present
HOME="$tmp/home1" bash "$SKILL_DIR/scripts/report_persistent_files.sh" anthropic >"$tmp/out1.txt" 2>&1
rc1=$?
out1="$(<"$tmp/out1.txt")"
assert_eq      "empty HOME: exit 0"                 "0" "$rc1"
assert_contains "empty HOME: lists ~/.claude/projects" "~/.claude/projects" "$out1"
assert_contains "empty HOME: flags (not present)"      "(not present)"      "$out1"

# Populated fake HOME → size reported instead of (not present)
mkdir -p "$tmp/home2/.claude/projects"
dd if=/dev/zero of="$tmp/home2/.claude/projects/filler" bs=1024 count=4 >/dev/null 2>&1
HOME="$tmp/home2" bash "$SKILL_DIR/scripts/report_persistent_files.sh" anthropic >"$tmp/out2.txt" 2>&1
rc2=$?
out2="$(<"$tmp/out2.txt")"
assert_eq          "populated HOME: exit 0"                      "0" "$rc2"
assert_contains    "populated HOME: lists ~/.claude/projects"    "~/.claude/projects" "$out2"
assert_not_contains "populated HOME: path NOT flagged (not present)" "(not present)" "$out2"

# Unknown vendor → exit 2
HOME="$tmp/home1" bash "$SKILL_DIR/scripts/report_persistent_files.sh" no-such-vendor >/dev/null 2>&1
rc3=$?
assert_eq "unknown vendor: exit 2" "2" "$rc3"

# Real shipped PhpStorm patterns must report every matching installed version,
# including a HOME containing spaces, while unmatched patterns remain absent.
glob_home="$tmp/home with spaces"
for version in PhpStorm2026.1 PhpStorm2026.2; do
  mkdir -p "$glob_home/Library/Caches/JetBrains/$version/event-log-data/logs/FUS"
  printf 'queued event\n' >"$glob_home/Library/Caches/JetBrains/$version/event-log-data/logs/FUS/queue.log"
done
glob_out="$(HOME="$glob_home" bash "$SKILL_DIR/scripts/report_persistent_files.sh" phpstorm 2>&1)"
assert_eq "PhpStorm glob: exit 0" "0" "$?"
for version in PhpStorm2026.1 PhpStorm2026.2; do
  assert_contains "PhpStorm glob: reports $version queue" "$glob_home/Library/Caches/JetBrains/$version/event-log-data/logs/FUS  [" "$glob_out"
done
assert_not_contains "PhpStorm glob: present pattern not called absent" "PhpStorm*/event-log-data/logs/FUS  [(not present)]" "$glob_out"
assert_contains "PhpStorm glob: unmatched log pattern remains absent" "PhpStorm*/log  [(not present)]" "$glob_out"

# Isolate uname and path conversion, rather than depending on the test runner's
# platform. The Vercel vendor is copied unchanged to test its shipped paths.
mkdir -p "$tmp/report/scripts" "$tmp/report/vendors" "$tmp/bin"
cp "$SKILL_DIR/scripts/report_persistent_files.sh" "$tmp/report/scripts/"
cp "$SKILL_DIR/vendors/vercel.json" "$tmp/report/vendors/"
cat >"$tmp/bin/uname" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${TEST_KERNEL:-Linux}"
EOF
cat >"$tmp/bin/cygpath" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "-u" ] || exit 1
[ "${TEST_TRANSLATE_FAIL:-0}" = "0" ] || exit 1
[ "$2" = "C:/Users/Test User/AppData/Roaming/com.vercel.cli/Data/config.json" ] ||
[ "$2" = "C:/Users/Test User/AppData/Roaming/com.vercel.cli/Data/auth.json" ] || exit 1
printf '%s/%s\n' "$TEST_APPDATA/com.vercel.cli/Data" "${2##*/}"
EOF
cp "$tmp/bin/cygpath" "$tmp/bin/wslpath"
chmod +x "$tmp/bin/uname" "$tmp/bin/cygpath" "$tmp/bin/wslpath"
windows_appdata="$tmp/Windows User/AppData/Roaming"
mkdir -p "$windows_appdata/com.vercel.cli/Data"
printf '{}\n' >"$windows_appdata/com.vercel.cli/Data/config.json"
printf '{}\n' >"$windows_appdata/com.vercel.cli/Data/auth.json"
windows_paths="$(jq -r '.persistent_files[].path | select(startswith("%"))' "$SKILL_DIR/vendors/vercel.json")"
assert_contains "Vercel Windows path: config uses CLI Data default" '%APPDATA%\com.vercel.cli\Data\config.json' "$windows_paths"
assert_not_contains "Vercel Windows path: no duplicated Roaming" '%APPDATA%\Roaming\' "$windows_paths"

unix_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=Darwin WSL_INTEROP= WSL_DISTRO_NAME= HOME="$tmp/home1" APPDATA="$windows_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" vercel 2>&1)"
unix_windows_rows="$(printf '%s\n' "$unix_out" | awk '/^- %/{print}')"
assert_eq "Windows entries on macOS: both rows retained" "2" "$(printf '%s\n' "$unix_windows_rows" | wc -l | tr -d ' ')"
assert_contains "Windows entries on macOS: explicitly skipped" '[(skipped: Windows path on non-Windows host)]' "$unix_windows_rows"
assert_not_contains "Windows entries on macOS: no false absent claim" '(not present)' "$unix_windows_rows"

windows_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=MINGW64_NT-10.0 WSL_INTEROP= WSL_DISTRO_NAME= HOME="$tmp/home1" APPDATA="$windows_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" vercel 2>&1)"
windows_rows="$(printf '%s\n' "$windows_out" | awk '/^- %/{print}')"
assert_eq "Windows env expansion: reports both shipped files" "2" "$(printf '%s\n' "$windows_rows" | wc -l | tr -d ' ')"
assert_not_contains "Windows env expansion: both files present" '(not present)' "$windows_rows"
assert_not_contains "Windows env expansion: no skipped files" '(skipped:' "$windows_rows"

for mode in native wsl; do
  kernel=MINGW64_NT-10.0
  wsl=""
  [ "$mode" != wsl ] || { kernel=Linux; wsl=fixture; }
  drive_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL="$kernel" WSL_INTEROP="$wsl" WSL_DISTRO_NAME= TEST_APPDATA="$windows_appdata" HOME="$tmp/home1" APPDATA='C:\Users\Test User\AppData\Roaming' bash "$tmp/report/scripts/report_persistent_files.sh" vercel 2>&1)"
  drive_rows="$(printf '%s\n' "$drive_out" | awk '/^- %/{print}')"
  assert_eq "Windows drive conversion ($mode): reports both files" "2" "$(printf '%s\n' "$drive_rows" | wc -l | tr -d ' ')"
  assert_not_contains "Windows drive conversion ($mode): files present" '(not present)' "$drive_rows"
  assert_not_contains "Windows drive conversion ($mode): conversion succeeds" '(skipped:' "$drive_rows"
done

failed_conversion_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=MINGW64_NT-10.0 TEST_TRANSLATE_FAIL=1 WSL_INTEROP= WSL_DISTRO_NAME= HOME="$tmp/home1" APPDATA='C:\Users\Test User\AppData\Roaming' bash "$tmp/report/scripts/report_persistent_files.sh" vercel 2>&1)"
assert_contains "Failed Windows drive conversion: skipped instead of absent" '[(skipped: cannot translate Windows path (cygpath required))]' "$failed_conversion_out"

missing_env_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=MINGW64_NT-10.0 WSL_INTEROP= WSL_DISTRO_NAME= HOME="$tmp/home1" APPDATA= bash "$tmp/report/scripts/report_persistent_files.sh" vercel 2>&1)"
assert_contains "Unset APPDATA: explicitly skipped" '[(skipped: APPDATA not set)]' "$missing_env_out"

# A LOCALAPPDATA fixture covers another variable and proves shell-looking values
# are paths, never eval input. Create the literal directory containing $(...).
literal_appdata="$tmp/"'$(touch sentinel)'
mkdir -p "$literal_appdata"
printf 'local data\n' >"$literal_appdata/state.json"
jq '.persistent_files = [{path: "%LOCALAPPDATA%\\state.json", note: "fixture"}]' "$SKILL_DIR/vendors/vercel.json" >"$tmp/report/vendors/local.json"
literal_out="$(cd "$tmp" && PATH="$tmp/bin:$PATH" TEST_KERNEL=MINGW64_NT-10.0 WSL_INTEROP= WSL_DISTRO_NAME= HOME="$tmp/home1" LOCALAPPDATA="$literal_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" local 2>&1)"
assert_contains "LOCALAPPDATA with shell syntax: reports the literal path" '- %LOCALAPPDATA%\state.json  [' "$literal_out"
assert_not_contains "LOCALAPPDATA with shell syntax: literal file present" '(not present)' "$literal_out"
assert "Windows environment paths: shell syntax never executes" test ! -e "$tmp/sentinel"

# Exercise shipped platform maps rather than recreating the vendor paths in a
# clean schema fixture. Linux uses separate overridden config and data roots.
cp "$SKILL_DIR/vendors/zed.json" "$SKILL_DIR/vendors/antigravity.json" "$SKILL_DIR/vendors/code.json" "$tmp/report/vendors/"
platform_home="$tmp/platform home"
xdg_config="$tmp/active config"
xdg_data="$tmp/active data"
local_appdata="$tmp/Windows User/AppData/Local"
mkdir -p "$xdg_config/zed" "$xdg_data/zed/logs" "$platform_home/.config/zed" \
  "$platform_home/Library/Logs/Zed" "$windows_appdata/Zed" "$local_appdata/Zed/logs"
printf '{}\n' >"$xdg_config/zed/settings.json"
printf '{}\n' >"$platform_home/.config/zed/settings.json"
printf '{}\n' >"$windows_appdata/Zed/settings.json"
for code_root in "$xdg_config/Code" "$platform_home/Library/Application Support/Code" "$windows_appdata/Code"; do
  mkdir -p "$code_root/User/globalStorage" "$code_root/logs"
done
for product in Antigravity 'Antigravity IDE'; do
  for product_root in "$xdg_config/$product" "$platform_home/Library/Application Support/$product" "$windows_appdata/$product"; do
    mkdir -p "$product_root/logs" "$product_root/CachedData"
    printf 'installation identifier\n' >"$product_root/machineid"
  done
done
for platform_case in linux darwin win32; do
  case "$platform_case" in
    linux) kernel=Linux; zed_settings="$xdg_config/zed/settings.json"; zed_logs="$xdg_data/zed/logs"; antigravity_root="$xdg_config" ;;
    darwin) kernel=Darwin; zed_settings='~/.config/zed/settings.json'; zed_logs='~/Library/Logs/Zed'; antigravity_root='~/Library/Application Support' ;;
    win32) kernel=MINGW64_NT-10.0; zed_settings='%APPDATA%/Zed/settings.json'; zed_logs='%LOCALAPPDATA%/Zed/logs'; antigravity_root='%APPDATA%' ;;
  esac
  zed_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL="$kernel" WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME="$xdg_config" XDG_DATA_HOME="$xdg_data" APPDATA="$windows_appdata" LOCALAPPDATA="$local_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" zed 2>&1)"
  assert_eq "Zed platform report ($platform_case): exit 0" "0" "$?"
  assert_contains "Zed platform report ($platform_case): selected settings path" "- $zed_settings  [" "$zed_out"
  assert_contains "Zed platform report ($platform_case): selected logs path" "- $zed_logs  [" "$zed_out"
  assert_not_contains "Zed platform report ($platform_case): files present" '(not present)' "$zed_out"
  assert_not_contains "Zed platform report ($platform_case): paths resolved" '(skipped:' "$zed_out"

  code_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL="$kernel" WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME="$xdg_config" APPDATA="$windows_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" code 2>&1)"
  assert_eq "VS Code platform report ($platform_case): exit 0" "0" "$?"
  assert_eq "VS Code platform report ($platform_case): two persistent paths" "2" "$(printf '%s\n' "$code_out" | awk '/^- /{count++} END {print count+0}')"
  assert_contains "VS Code platform report ($platform_case): selected globalStorage path" "- $antigravity_root/Code/User/globalStorage  [" "$code_out"
  assert_contains "VS Code platform report ($platform_case): selected logs path" "- $antigravity_root/Code/logs  [" "$code_out"
  assert_not_contains "VS Code platform report ($platform_case): files present" '(not present)' "$code_out"
  assert_not_contains "VS Code platform report ($platform_case): paths resolved" '(skipped:' "$code_out"

  antigravity_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL="$kernel" WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME="$xdg_config" APPDATA="$windows_appdata" bash "$tmp/report/scripts/report_persistent_files.sh" antigravity 2>&1)"
  assert_eq "Antigravity platform report ($platform_case): exit 0" "0" "$?"
  assert_eq "Antigravity platform report ($platform_case): six product paths" "6" "$(printf '%s\n' "$antigravity_out" | awk '/^- /{count++} END {print count+0}')"
  for product in Antigravity 'Antigravity IDE'; do
    assert_contains "Antigravity platform report ($platform_case): $product root" "- $antigravity_root/$product/logs  [" "$antigravity_out"
  done
  assert_not_contains "Antigravity platform report ($platform_case): files present" '(not present)' "$antigravity_out"
  assert_not_contains "Antigravity platform report ($platform_case): paths resolved" '(skipped:' "$antigravity_out"
done

# A missing map entry is inapplicable, not evidence that a file is absent. A
# generic .path remains the fallback when the map does not cover this platform.
xdg_state="$tmp/active state"
mkdir -p "$xdg_state"
printf 'state\n' >"$xdg_state/state.json"
jq '.persistent_files = [
  {paths: {darwin: "~/mac-only.json"}, note: "macOS-only state"},
  {paths: {darwin: "~/other-mac.json"}, path: "${XDG_STATE_HOME}/state.json", note: "generic fallback state"}
]' "$SKILL_DIR/vendors/zed.json" >"$tmp/report/vendors/platform-fixture.json"
fallback_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=Linux WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_STATE_HOME="$xdg_state" bash "$tmp/report/scripts/report_persistent_files.sh" platform-fixture 2>&1)"
assert_contains "Missing platform map: explicitly skipped" '[(skipped: no persistent path configured for linux)]' "$fallback_out"
assert_contains "Platform map: generic .path fallback resolves state override" "- $xdg_state/state.json  [" "$fallback_out"
assert_not_contains "Missing platform map: no false absent claim" '(not present)' "$fallback_out"
assert_not_contains "Platform map: another OS path is not reported" '~/mac-only.json' "$fallback_out"

# Unset/empty XDG roots use their standard defaults. The fixture covers all
# three allowlisted placeholders; unsupported placeholders remain unresolved.
for default_dir in .config .local/share .local/state; do
  mkdir -p "$platform_home/$default_dir"
  printf '{}\n' >"$platform_home/$default_dir/defaults.json"
done
jq '.persistent_files = [
  {path: "${XDG_CONFIG_HOME}/defaults.json"},
  {path: "${XDG_DATA_HOME}/defaults.json"},
  {path: "${XDG_STATE_HOME}/defaults.json"}
]' "$SKILL_DIR/vendors/zed.json" >"$tmp/report/vendors/xdg-fixture.json"
default_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=Linux WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME= XDG_DATA_HOME= XDG_STATE_HOME= bash "$tmp/report/scripts/report_persistent_files.sh" xdg-fixture 2>&1)"
for default_dir in .config .local/share .local/state; do
  assert_contains "Empty XDG root: standard $default_dir path" "- $platform_home/$default_dir/defaults.json  [" "$default_out"
done
assert_not_contains "Empty XDG roots: default files present" '(not present)' "$default_out"
relative_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=Linux WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME=relative XDG_DATA_HOME=relative XDG_STATE_HOME=relative bash "$tmp/report/scripts/report_persistent_files.sh" xdg-fixture 2>&1)"
for xdg_var in XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME; do
  assert_contains "Relative $xdg_var: explicitly skipped" "[(skipped: $xdg_var must be an absolute path)]" "$relative_out"
done
assert_not_contains "Relative XDG roots: no false absent claim" '(not present)' "$relative_out"

literal_xdg="$tmp/"'$(touch xdg-sentinel)'
mkdir -p "$literal_xdg"
printf '{}\n' >"$literal_xdg/defaults.json"
literal_xdg_out="$(cd "$tmp" && PATH="$tmp/bin:$PATH" TEST_KERNEL=Linux WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" XDG_CONFIG_HOME="$literal_xdg" XDG_DATA_HOME="$literal_xdg" XDG_STATE_HOME="$literal_xdg" bash "$tmp/report/scripts/report_persistent_files.sh" xdg-fixture 2>&1)"
assert_contains "XDG shell syntax: concrete literal path reported" "- $literal_xdg/defaults.json  [" "$literal_xdg_out"
assert_not_contains "XDG shell syntax: literal file present" '(not present)' "$literal_xdg_out"
assert "XDG environment paths: shell syntax never executes" test ! -e "$tmp/xdg-sentinel"
jq '.persistent_files = [{path: "${HOME}/defaults.json"}]' "$SKILL_DIR/vendors/zed.json" >"$tmp/report/vendors/unsupported-fixture.json"
unsupported_out="$(PATH="$tmp/bin:$PATH" TEST_KERNEL=Linux WSL_INTEROP= WSL_DISTRO_NAME= HOME="$platform_home" bash "$tmp/report/scripts/report_persistent_files.sh" unsupported-fixture 2>&1)"
assert_contains "Non-allowlisted variable: explicitly skipped" '[(skipped: unsupported variable placeholder)]' "$unsupported_out"
assert_not_contains "Non-allowlisted variable: no false absent claim" '(not present)' "$unsupported_out"

echo "== check_new_optouts.sh =="

# Build an isolated skill layout so VENDORS_DIR resolves to our fixture.
mkdir -p "$tmp/fixture/scripts" "$tmp/fixture/vendors"
cp "$SKILL_DIR/scripts/check_new_optouts.sh" "$tmp/fixture/scripts/"

cat >"$tmp/fixture/doc.txt" <<'EOF'
Example opt-out documentation.
  FAKE_NEW_VAR=1 — disables something newly documented.
  DISABLE_TELEMETRY=1 — known baseline flag.
EOF

cat >"$tmp/fixture/vendors/testvendor.json" <<EOF
{
  "name": "testvendor",
  "display": "Test Vendor",
  "detect_cmd": "true",
  "detect_paths": [],
  "doc_urls": ["file://$tmp/fixture/doc.txt"],
  "settings_files": [{
    "path": "~/.fake/config.json",
    "format": "json",
    "edits": [
      { "key": "env.DISABLE_TELEMETRY", "value": "1", "disables": "baseline sentinel" }
    ]
  }],
  "shell_env_vars": [],
  "persistent_files": [],
  "diff_patterns": {
    "env_regex": "(FAKE|DISABLE|TELEMETRY)",
    "settings_regex": ""
  },
  "caveats": [],
  "notes": []
}
EOF

out3="$(bash "$tmp/fixture/scripts/check_new_optouts.sh" testvendor 2>&1)"
rc4=$?
assert_eq       "check_new_optouts: exit 0"                          "0" "$rc4"
assert_contains "check_new_optouts: fetched our fixture doc"         "fetched:" "$out3"
assert_contains "check_new_optouts: surfaces FAKE_NEW_VAR candidate" "FAKE_NEW_VAR" "$out3"

# Extract just the "Not in baseline" section — FAKE_NEW_VAR must appear there,
# DISABLE_TELEMETRY must not (it's in baseline).
not_in_baseline="$(printf '%s\n' "$out3" | awk '/^## Not in baseline/{flag=1; next} /^## /{flag=0} flag')"
assert_contains     "check_new_optouts: FAKE_NEW_VAR in 'Not in baseline'"           "FAKE_NEW_VAR"      "$not_in_baseline"
assert_not_contains "check_new_optouts: DISABLE_TELEMETRY NOT in 'Not in baseline'" "DISABLE_TELEMETRY" "$not_in_baseline"

# Keep the real shipped Anthropic regex and baseline; redirect only its URLs to
# a local doc. This catches regressions hidden by hand-built clean JSON fixtures.
cat >"$tmp/fixture/anthropic-doc.txt" <<'EOF'
Known setting: `skipWebFetchPreflight`.
New candidate setting: `skipNewShinyPreflight`.
Unrelated setting: `someOtherSetting`.
Known env opt-out: `DISABLE_TELEMETRY`.
EOF
jq --arg url "file://$tmp/fixture/anthropic-doc.txt" '.doc_urls = [$url]' "$SKILL_DIR/vendors/anthropic.json" >"$tmp/fixture/vendors/anthropic.json"
anthropic_out="$(bash "$tmp/fixture/scripts/check_new_optouts.sh" anthropic 2>&1)"
assert_eq "shipped Anthropic scan: exit 0" "0" "$?"
assert_contains "shipped Anthropic scan: recognizes settings token" '- skipWebFetchPreflight' "$anthropic_out"
assert_contains "shipped Anthropic scan: recognizes new settings token" '- skipNewShinyPreflight' "$anthropic_out"
anthropic_new="$(printf '%s\n' "$anthropic_out" | awk '/^## Not in baseline/{flag=1; next} /^## /{flag=0} flag')"
assert_contains "shipped Anthropic scan: new setting in diff" '- skipNewShinyPreflight' "$anthropic_new"
assert_not_contains "shipped Anthropic scan: baseline setting removed from diff" 'skipWebFetchPreflight' "$anthropic_new"
assert_not_contains "shipped Anthropic scan: unrelated setting ignored" 'someOtherSetting' "$anthropic_out"
assert_not_contains "shipped Anthropic scan: baseline env removed from diff" 'DISABLE_TELEMETRY' "$anthropic_new"

if [ "$TESTS_FAILED" -eq 0 ]; then
  echo "scripts: $TESTS_RUN ok"
else
  echo "scripts: $TESTS_FAILED / $TESTS_RUN failed" >&2
fi
exit "$TESTS_FAILED"
