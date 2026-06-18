#!/usr/bin/env bash
# Runs the pytest suite in this directory. Exits non-zero if any test fails.
set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -m pytest --version >/dev/null 2>&1; then
  echo "pytest not available for $PYTHON — install it with: $PYTHON -m pip install pytest" >&2
  exit 1
fi

if "$PYTHON" -m pytest "$TEST_DIR"; then
  echo "All tests passed."
  exit 0
else
  echo "Tests failed." >&2
  exit 1
fi
