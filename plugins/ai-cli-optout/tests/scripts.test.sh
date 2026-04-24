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

if [ "$TESTS_FAILED" -eq 0 ]; then
  echo "scripts: $TESTS_RUN ok"
else
  echo "scripts: $TESTS_FAILED / $TESTS_RUN failed" >&2
fi
exit "$TESTS_FAILED"
