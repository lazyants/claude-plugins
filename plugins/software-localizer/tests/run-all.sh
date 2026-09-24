#!/usr/bin/env bash
# Runs the software-localizer pytest suite, refusing to report a false green
# on a run that executed fewer tests than were collected (plan section 13).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="${TEST_DIR:-$SCRIPT_DIR}"

collected="$(python3 -m pytest "$TEST_DIR" --collect-only -q 2>/dev/null | grep -c '::' || true)"

if [ "$collected" -lt 1 ]; then
    echo "software-localizer: pytest collected zero tests under $TEST_DIR" >&2
    exit 1
fi

echo "software-localizer: collected $collected tests" >&2

output_file="$(mktemp)"
trap 'rm -f "$output_file"' EXIT

set +e
python3 -m pytest "$TEST_DIR" -q --durations=25 2>&1 | tee "$output_file"
status="${PIPESTATUS[0]}"
set -e

if [ "$status" -ne 0 ]; then
    echo "software-localizer: pytest exited $status" >&2
    exit "$status"
fi

# pytest's final summary line looks like:
#   "12 passed, 3 skipped, 1 xfailed in 2.34s"
# possibly padded with "=" characters. Any category not mentioned is 0.
summary_line="$(grep -E ' in [0-9.]+s' "$output_file" | tail -1)"

count_of() {
    echo "$summary_line" | grep -oE "[0-9]+ $1" | head -1 | grep -oE '^[0-9]+' || true
}

passed="$(count_of passed)"; passed="${passed:-0}"
skipped="$(count_of skipped)"; skipped="${skipped:-0}"
xfailed="$(count_of xfailed)"; xfailed="${xfailed:-0}"
executed=$(( passed + skipped + xfailed ))

if [ "$executed" -ne "$collected" ]; then
    echo "software-localizer: executed $executed (passed+skipped+xfailed) but collected $collected -- some tests never ran" >&2
    exit 1
fi

echo "software-localizer: executed $executed of $collected collected tests" >&2
