#!/usr/bin/env bash
# Runs every *.test.sh in this directory. Exits non-zero if any file fails.
set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILED=0
TOTAL=0

for t in "$TEST_DIR"/*.test.sh; do
  TOTAL=$((TOTAL + 1))
  echo "## $(basename "$t")"
  if ! bash "$t"; then
    FAILED=$((FAILED + 1))
  fi
  echo
done

if [ "$FAILED" -eq 0 ]; then
  echo "All $TOTAL test files passed."
  exit 0
else
  echo "$FAILED / $TOTAL test files failed." >&2
  exit 1
fi
