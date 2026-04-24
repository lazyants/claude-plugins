#!/usr/bin/env bash
# Read-only report of local files each vendor keeps on disk beyond telemetry opt-outs.
# No deletion. Prints path + size + note per vendor.
#
# Usage:
#   report_persistent_files.sh                 — all vendors
#   report_persistent_files.sh <vendor>        — one vendor by name

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDORS_DIR="$(cd "$SCRIPT_DIR/../vendors" && pwd)"

command -v jq >/dev/null 2>&1 || { echo "jq required (brew install jq)"; exit 1; }

report_vendor() {
  local config="$1"
  local display count
  IFS=$'\t' read -r display count < <(jq -r '[.display, ((.persistent_files // []) | length)] | @tsv' "$config")

  echo "## $display"
  if [ "$count" = "0" ]; then
    echo "(no persistent files listed)"
    echo
    return
  fi

  # One TSV row per entry — spaces in paths survive because tabs delimit.
  local path note expanded size
  while IFS=$'\t' read -r path note; do
    [ -z "$path" ] && continue
    expanded="${path/#\~/$HOME}"

    if [ -e "$expanded" ]; then
      size="$(du -sh "$expanded" 2>/dev/null | awk '{print $1}')"
      [ -z "$size" ] && size="?"
    else
      size="(not present)"
    fi

    printf -- "- %s  [%s]\n" "$path" "$size"
    [ -n "$note" ] && printf "    %s\n" "$note"
  done < <(jq -r '.persistent_files[]? | [.path, (.note // "")] | @tsv' "$config")
  echo
}

echo "# Persistent local files across AI CLIs"
echo

if [ "$#" -ge 1 ] && [ "$1" != "--all" ]; then
  config="$VENDORS_DIR/$1.json"
  if [ ! -f "$config" ]; then
    echo "no such vendor: $1 (expected $config)" >&2
    exit 2
  fi
  report_vendor "$config"
else
  for config in "$VENDORS_DIR"/*.json; do
    report_vendor "$config"
  done
fi
