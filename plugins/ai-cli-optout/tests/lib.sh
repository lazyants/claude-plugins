# Minimal assertion helpers for ai-cli-optout tests.
# Sourced — not executed directly. Callers own $TESTS_RUN / $TESTS_FAILED.

TESTS_RUN=0
TESTS_FAILED=0

_ok()   { printf "  ok    %s\n" "$1"; TESTS_RUN=$((TESTS_RUN + 1)); }
_fail() { printf "  FAIL  %s\n" "$1" >&2; TESTS_RUN=$((TESTS_RUN + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); }

assert() {
  local msg="$1"; shift
  if "$@" >/dev/null 2>&1; then _ok "$msg"; else _fail "$msg"; fi
}

assert_eq() {
  local msg="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then _ok "$msg"; else _fail "$msg: expected '$expected', got '$actual'"; fi
}

assert_contains() {
  local msg="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then _ok "$msg"; else _fail "$msg: '$needle' not in output"; fi
}

assert_not_contains() {
  local msg="$1" needle="$2" haystack="$3"
  if ! printf '%s' "$haystack" | grep -qF -- "$needle"; then _ok "$msg"; else _fail "$msg: '$needle' unexpectedly in output"; fi
}
