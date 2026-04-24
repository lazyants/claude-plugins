#!/usr/bin/env bash
# Fetches current vendor docs and diffs against the baseline opt-out list.
# Exit 0 always; prints report. Claude parses the output and asks the user before applying NEW flags.
#
# Usage:
#   check_new_optouts.sh <vendor>     — scan one vendor (name from vendors/*.json)
#   check_new_optouts.sh --all        — scan every vendor config

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDORS_DIR="$(cd "$SCRIPT_DIR/../vendors" && pwd)"

command -v jq >/dev/null 2>&1 || { echo "jq required (brew install jq)"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl required"; exit 1; }

TMP=""
cleanup_tmp() { [ -n "$TMP" ] && rm -f "$TMP"; TMP=""; }
trap cleanup_tmp EXIT

scan_vendor() {
  local config="$1"
  local name display
  IFS=$'\t' read -r name display < <(jq -r '[.name, .display] | @tsv' "$config")

  echo "# $display opt-out doc scan"
  echo

  # Baseline keys for diffing against doc-extracted candidates. `env.` prefix
  # stripped so bare "DISABLE_TELEMETRY" in settings_files[].edits matches.
  # shell_commands regex handles both macOS `defaults write DOMAIN KEY -bool ...`
  # and Windows `reg add PATH /v KEY ...` in one pass.
  local baseline
  baseline="$(jq -r '
    [ (.shell_env_vars // [])[].name ] +
    [ (.settings_files // [])[]?.edits[]?.key ] +
    [ (.caveats // [])[].var? ] +
    [ (.cli_flags // [])[].flag? ] +
    [ (.shell_commands // [])[]?.command
      | (capture("(/v\\s+|\\s)(?<key>\\w+)\\s+(-(bool|int|string|array|dict|data|date|float)|/t\\s)") | .key)? ]
    | .[] | select(. != null and length > 0) | sub("^env\\."; "")
  ' "$config" | sort -u)"

  local -a urls=()
  while IFS= read -r u; do
    [ -n "$u" ] && urls+=("$u")
  done < <(jq -r '.doc_urls[]?' "$config")

  if [ "${#urls[@]}" -eq 0 ]; then
    echo "- no doc_urls configured for $name — skipping research."
    echo
    return
  fi

  cleanup_tmp
  TMP="$(mktemp -t ai-cli-optout-docs.XXXXXX)"

  local ok=0
  for url in "${urls[@]}"; do
    if curl -fsSL --max-time 10 "$url" >> "$TMP" 2>/dev/null; then
      echo "- fetched: $url"
      ok=$((ok + 1))
    else
      echo "- FAILED:  $url"
    fi
  done
  echo

  if [ "$ok" -eq 0 ]; then
    echo "## Result: research SKIPPED (no docs fetched for $name)"
    echo "Proceed with baseline only."
    echo
    return
  fi

  local env_re settings_re
  env_re="$(jq -r '.diff_patterns.env_regex // "(DISABLE|TELEMETRY|OPT_?OUT|TRACK|ANALYTICS|FEEDBACK)"' "$config")"
  settings_re="$(jq -r '.diff_patterns.settings_regex // ""' "$config")"

  local candidates
  candidates="$(
    {
      grep -oE '\b[A-Z][A-Z0-9_]{3,}\b' "$TMP" | grep -E "$env_re" || true
      if [ -n "$settings_re" ]; then
        grep -oE '`[A-Za-z][A-Za-z0-9_.]+`' "$TMP" | tr -d '`' | grep -E "$settings_re" || true
      fi
    } | sort -u
  )"

  echo "## Candidates found in docs:"
  if [ -z "$candidates" ]; then
    echo "(none)"
  else
    printf '%s\n' "$candidates" | sed 's/^/- /'
  fi
  echo

  echo "## Not in baseline (review before applying):"
  local new=""
  while IFS= read -r cand; do
    [ -z "$cand" ] && continue
    if ! grep -Fxq -- "$cand" <<< "$baseline"; then
      new+="$cand"$'\n'
    fi
  done <<< "$candidates"

  if [ -z "$new" ]; then
    echo "(none — baseline is up to date)"
  else
    printf '%s' "$new" | sed 's/^/- /'
    echo
    echo "For each flag above, verify in the docs that it is an opt-out flag"
    echo "(not just mentioned), confirm its location (env var vs settings file),"
    echo "then ask the user before adding it."
  fi
  echo
}

if [ "$#" -lt 1 ]; then
  echo "usage: $(basename "$0") <vendor>|--all" >&2
  exit 2
fi

if [ "$1" = "--all" ]; then
  for config in "$VENDORS_DIR"/*.json; do
    scan_vendor "$config"
  done
else
  config="$VENDORS_DIR/$1.json"
  if [ ! -f "$config" ]; then
    echo "no such vendor: $1 (expected $config)" >&2
    exit 2
  fi
  scan_vendor "$config"
fi
