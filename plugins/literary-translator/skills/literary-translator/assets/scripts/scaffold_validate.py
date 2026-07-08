#!/usr/bin/env python3
"""scaffold_validate.py -- W1 Scaffold gate: hand-adapted files actually filled in.

Runs as a hard, blocking gate BEFORE W2 (Extract) begins (see SKILL.md's
W1 Scaffold section). Step 0/0a already did all the *copying* of
placeholder scaffold files into the durable root; W1 is the human-facing
label for "fill in every placeholder across profile.yml and every other
just-scaffolded file" -- profile.yml's own placeholder scan lives entirely
in profile_validate.py (a whole-parsed-object scan), which never touches
these free-form hand-adapted markdown files. This script closes THAT gap.

Two independent, unrelated checks:

1. LT_REQUIRED_FILL marker scan. `style_bible.md`/`PLAN.md`/
   `consistency_issues.md` (the shipped `*.template.md` copies, once
   hand-adapted after their one-time Step 0a copy) wrap their own
   must-fill sections in

       <!-- LT_REQUIRED_FILL_BEGIN: <id> -->
       ...
       <!-- LT_REQUIRED_FILL_END -->

   marker pairs, shipped containing the fixed sentinel string
   `LT_PLACEHOLDER_UNFILLED`. This scan FATALLY rejects, naming the file
   and the specific marker `id`, if that sentinel still appears anywhere
   inside a marker span. Text living OUTSIDE any marker span is never
   scanned -- an implicit allowlist for illustrative examples / free-form
   notes, by construction, never a separately maintained exception list.
   Not every scanned file is guaranteed to contain marker pairs at all
   (e.g. `consistency_issues.md` may ship with none); a file with zero
   marker spans simply passes this check with nothing to find.

2. Era/domain trap-string check -- separate, marker-free, NOT part of
   check 1. `translate_TASK.md`/`review_TASK.md` carry an illustrative
   ERA/DOMAIN TRAP EXAMPLE callout, deliberately never LT_REQUIRED_FILL-
   marked (traps are discovered DURING a run, not knowable at W1 scaffold
   time -- nothing could be "required" there). The shipped example itself
   is the literal string `guéridon=refrain-song` (a small round
   pedestal table in a 17th-century French memoir vs. a type of song --
   the genuine, narrower risk the callout guards against is THIS
   old-book-specific example surviving an unedited copy-paste into an
   unrelated project's own task templates). This check FATALLY rejects,
   naming the file, if that exact literal string still appears anywhere
   in either file -- no marker span involved.

Dependency-free by design: stdlib `re`/`os` only, no jsonschema, no
requirements.txt entry, no preflight needed.

Exit 0 = clean (every scanned file present, no unfilled marker span, no
surviving trap string). Exit 1 = one or more fatal findings (all are
printed, not just the first). Exit 2 = usage error.

Usage: python3 scaffold_validate.py
"""
import re
import sys
from pathlib import Path
from typing import Callable

# Self-anchored: this script lives at ${durable_root}/scripts/scaffold_validate.py,
# so parents[1] is the durable root. Never assumes cwd, never takes a
# --durable-root flag.
DURABLE_ROOT = Path(__file__).resolve().parents[1]

# Files hand-adapted from *.template.md at Step 0a, checked for surviving
# LT_REQUIRED_FILL marker spans. Not every one of these is guaranteed to
# actually ship marker pairs (consistency_issues.md may have none) -- a
# file with zero spans is not itself an error.
MARKER_SCAN_FILES = ["PLAN.md", "style_bible.md", "consistency_issues.md"]

# Files checked for the surviving era/domain trap-string literal. Deliberately
# a SEPARATE list from MARKER_SCAN_FILES -- these two task templates carry
# the ERA/DOMAIN TRAP EXAMPLE callout, never an LT_REQUIRED_FILL marker.
TRAP_STRING_SCAN_FILES = ["translate_TASK.md", "review_TASK.md"]

SENTINEL = "LT_PLACEHOLDER_UNFILLED"

# The shipped illustrative example this check exists to guard against --
# see the module docstring for what it means and why it's dangerous left
# unedited in a different project's task templates.
TRAP_STRING = "guéridon=refrain-song"

MARKER_BEGIN_RE = re.compile(r"<!--\s*LT_REQUIRED_FILL_BEGIN:\s*(?P<id>[^>\n]+?)\s*-->")
MARKER_END_RE = re.compile(r"<!--\s*LT_REQUIRED_FILL_END\s*-->")


def scan_markers(path: Path, text: str) -> list[str]:
    """Return a list of fatal-finding strings for one MARKER_SCAN_FILES file.

    Walks BEGIN/END markers in document order, pairing each BEGIN with the
    next END found after it (markers are not expected to nest). A BEGIN
    with no following END is itself a fatal, named finding -- a malformed
    scaffold file the sentinel scan could otherwise silently skip past.
    """
    findings: list[str] = []
    ends = list(MARKER_END_RE.finditer(text))

    for b in MARKER_BEGIN_RE.finditer(text):
        marker_id = b.group("id")
        span_start = b.end()
        # first END that starts at or after this BEGIN's end
        end_match = next((e for e in ends if e.start() >= span_start), None)
        if end_match is None:
            findings.append(
                f"{path}: LT_REQUIRED_FILL_BEGIN marker '{marker_id}' has no "
                f"matching LT_REQUIRED_FILL_END -- malformed scaffold file"
            )
            continue
        span_text = text[span_start:end_match.start()]
        if SENTINEL in span_text:
            findings.append(
                f"{path}: marker '{marker_id}' still contains the unfilled "
                f"placeholder sentinel '{SENTINEL}' -- fill in this section "
                f"before continuing past W1"
            )
    return findings


def scan_trap_string(path: Path, text: str) -> list[str]:
    if TRAP_STRING not in text:
        return []
    return [
        f"{path}: still contains the shipped illustrative example "
        f"'{TRAP_STRING}' -- replace it with this project's own "
        f"era/domain trap example (or remove the callout) before "
        f"continuing past W1"
    ]


def collect_findings(
    names: list[str], scanner: Callable[[Path, str], list[str]]
) -> list[str]:
    """Read each named file under DURABLE_ROOT and apply `scanner` to it.

    Missing or unreadable files are themselves fatal findings; readable
    files are handed off to the per-check scanner. Both file lists in
    main() share this identical prelude, so it lives here once.
    """
    findings: list[str] = []
    for name in names:
        path = DURABLE_ROOT / name
        if not path.exists():
            findings.append(f"{path}: file missing (expected from Step 0a scaffold copy)")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            findings.append(f"{path}: could not read file ({e})")
            continue
        findings.extend(scanner(path, text))
    return findings


def main() -> None:
    if len(sys.argv) != 1:
        print("usage: python3 scaffold_validate.py", file=sys.stderr)
        sys.exit(2)

    findings = [
        *collect_findings(MARKER_SCAN_FILES, scan_markers),
        *collect_findings(TRAP_STRING_SCAN_FILES, scan_trap_string),
    ]

    if findings:
        print("scaffold_validate: FATAL -- W1 scaffold gate failed:")
        for f in findings:
            print(f"  - {f}")
        sys.exit(1)

    print("scaffold_validate: OK -- no unfilled LT_REQUIRED_FILL markers, no surviving trap string")
    sys.exit(0)


if __name__ == "__main__":
    main()
