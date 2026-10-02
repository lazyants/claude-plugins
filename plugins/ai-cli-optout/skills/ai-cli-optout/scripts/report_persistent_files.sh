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

# Windows paths can be read from Git Bash/Cygwin or a WSL shell with Windows
# environment variables supplied. Other hosts must not claim they are absent.
WINDOWS_HOST=""
CURRENT_PLATFORM="unknown"
case "$(uname -s)" in
  Darwin) CURRENT_PLATFORM="darwin" ;;
  Linux) CURRENT_PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) CURRENT_PLATFORM="win32"; WINDOWS_HOST="native" ;;
esac
if [ -z "$WINDOWS_HOST" ]; then
  if [ -n "${WSL_INTEROP:-}${WSL_DISTRO_NAME:-}" ] ||
     { [ -r /proc/sys/kernel/osrelease ] && grep -qi microsoft /proc/sys/kernel/osrelease; }; then
    WINDOWS_HOST="wsl"
  fi
fi

report_path() {
  local expanded="$1" label="$2" note="$3" match size
  local -a matches=()
  if [ -e "$expanded" ]; then
    matches=("$expanded")
  elif [[ "$expanded" == *'*'* || "$expanded" == *'?'* || "$expanded" == *'['* ]]; then
    # Disable splitting while retaining pathname expansion. Spaces and newlines
    # inside matching filenames survive; nullglob removes an unmatched pattern.
    local IFS=''
    local nullglob_set=0
    shopt -q nullglob && nullglob_set=1
    shopt -s nullglob
    matches=( $expanded )
    [ "$nullglob_set" -eq 1 ] || shopt -u nullglob
  fi

  if [ "${#matches[@]}" -eq 0 ]; then
    printf -- "- %s  [(not present)]\n" "$label"
    [ -n "$note" ] && printf "    %s\n" "$note"
    return
  fi
  for match in "${matches[@]}"; do
    size="$(du -sh "$match" 2>/dev/null | awk '{print $1}')"
    [ -z "$size" ] && size="?"
    # Preserve the familiar vendor label for literals; show each concrete match
    # when a pattern expands so versioned files are individually identifiable.
    if [ "$match" = "$expanded" ]; then
      printf -- "- %s  [%s]\n" "$label" "$size"
    else
      printf -- "- %s  [%s]\n" "$match" "$size"
    fi
    [ -n "$note" ] && printf "    %s\n" "$note"
  done
}

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

  # Tabs delimit rows without @tsv's backslash escaping of Windows paths.
  local path note expanded label var value suffix skip converter token default_root
  while IFS=$'\t' read -r path note; do
    expanded="${path/#\~/$HOME}"
    label="$path"
    skip=""
    var=""
    case "$path" in
      '(no configured platform path)') skip="no persistent path configured for $CURRENT_PLATFORM" ;;
      '${XDG_CONFIG_HOME}'*) var="XDG_CONFIG_HOME"; default_root="$HOME/.config" ;;
      '${XDG_DATA_HOME}'*) var="XDG_DATA_HOME"; default_root="$HOME/.local/share" ;;
      '${XDG_STATE_HOME}'*) var="XDG_STATE_HOME"; default_root="$HOME/.local/state" ;;
      *'${'*) skip="unsupported variable placeholder" ;;
    esac
    if [ -n "$var" ]; then
      # Only these three leading placeholders are supported. The substituted
      # environment value is literal path text, never shell input or a template.
      token='${'"$var"'}'
      suffix="${path#"$token"}"
      value="${!var:-}"
      [ -n "$value" ] || value="$default_root"
      if [[ "$value" != /* ]]; then
        skip="$var must be an absolute path"
      elif [[ "$suffix" == *'${'* ]]; then
        skip="unsupported variable placeholder"
      else
        expanded="$value$suffix"
        label="$expanded"
      fi
    elif [[ "$path" == %* ]]; then
      if [ -z "$WINDOWS_HOST" ]; then
        skip="Windows path on non-Windows host"
      elif [[ "$path" =~ ^%([A-Za-z_][A-Za-z0-9_]*)% ]]; then
        var="${BASH_REMATCH[1]}"
        value="${!var:-}"
        suffix="${path#*%}"
        suffix="${suffix#*%}"
        if [ -z "$value" ]; then
          skip="$var not set"
        elif [[ "$suffix" == *%* ]]; then
          skip="unsupported Windows variable path"
        else
          # Indirect variable lookup above and quoted substitution never execute
          # shell syntax from a path or environment value.
          expanded="$value$suffix"
          expanded="${expanded//\\//}"
          if [[ "$expanded" =~ ^[A-Za-z]:/ ]] || [[ "$expanded" == //* ]]; then
            converter="cygpath"
            [ "$WINDOWS_HOST" != "wsl" ] || converter="wslpath"
            if ! command -v "$converter" >/dev/null 2>&1 ||
               ! expanded="$("$converter" -u "$expanded" 2>/dev/null)"; then
              skip="cannot translate Windows path ($converter required)"
            fi
          fi
        fi
      else
        skip="unsupported Windows variable path"
      fi
    fi

    if [ -n "$skip" ]; then
      printf -- "- %s  [(skipped: %s)]\n" "$label" "$skip"
      [ -n "$note" ] && printf "    %s\n" "$note"
    else
      report_path "$expanded" "$label" "$note"
    fi
  done < <(jq -r --arg platform "$CURRENT_PLATFORM" '
    .persistent_files[]? | (.paths[$platform] // .path // "") as $path |
    [(if $path == "" then "(no configured platform path)" else $path end), (.note // "")] | join("\t")
  ' "$config")
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
