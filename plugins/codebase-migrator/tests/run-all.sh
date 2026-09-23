#!/usr/bin/env bash
# Runs the codebase-migrator pytest suite, refusing to report a false green
# on a suite that collected zero tests (see plan section 7, "Acceptance").
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="${TEST_DIR:-$SCRIPT_DIR}"

collected_lines="$(python3 -m pytest "$TEST_DIR" --collect-only -q 2>/dev/null | grep -c '::' || true)"

if [ "$collected_lines" -lt 1 ]; then
    echo "codebase-migrator: pytest collected zero tests under $TEST_DIR" >&2
    exit 1
fi

echo "codebase-migrator: collected $collected_lines tests" >&2

exec python3 -m pytest "$TEST_DIR" -q
