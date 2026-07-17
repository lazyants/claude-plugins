#!/usr/bin/env python3
"""skeptic_report.py -- SEPARATE, advisory-only report over skeptic_triage.json
(RFC #215 Phase 2).

This command is NOT the persisted rollout gate -- that remains
`canon_adjudication_audit.py`, run and read exactly as it was before this
plugin version, untouched by this file (see
`tests/audit_unchanged_regression.test.py`, which proves the audit's
summary + exit code are byte-identical whether or not a
`skeptic_triage.json` sits in the durable root). No freeze/merge reader
opens `skeptic_triage.json`; this script is the ONLY consumer, and it is
read-only: it never writes an adjudication, a verdict, or any accepted-
state file, and it always exits 0 on a structurally valid triage artifact
regardless of what it contains -- it reports, it never blocks. The IRON
RULE (scripts SURFACE, never decide) applies here too: every verdict this
script prints was already authored by the skeptic codex pass and already
re-verified by `skeptic_ready.py --verify-merged`; this script recomputes
nothing evidentiary, it only renders.

Three inputs, one required + two best-effort:
  - `skeptic_triage.json` (REQUIRED; default `{durable_root}/
    skeptic_triage.json`, i.e. `skeptic_constants.SKEPTIC_TRIAGE_FILENAME`)
    -- schema-validated against `skeptic-triage.schema.json` before
    anything is rendered, so a foreign/corrupt artifact fails LOUD rather
    than rendering garbage.
  - `manifest.json` (REQUIRED; default `{durable_root}/manifest.json`) --
    needed to derive each cited quote: this script stores no quotes of its
    own anywhere, it slices `manifest.blocks[block].plain_text` at the
    STORED offsets fresh, every time it runs (see `derive_quote`).
  - `suspicion_worklist.json` (BEST-EFFORT; default `{durable_root}/
    suspicion_worklist.json`, i.e. `skeptic_constants.
    SUSPICION_WORKLIST_FILENAME`) -- optional enrichment only, mapping
    `source_form -> risk_classes` so the report can show WHY an entity was
    ever examined. Absent, unreadable, or malformed -> silently degrades to
    "risk classes: unavailable" per entity; this is advisory context, never
    a gate input, so it is never fatal.

`--triage`/`--manifest-path`/`--worklist-path`/`--schemas-dir` override the
individual file paths; `--durable-root` overrides the base directory those
defaults are computed from (default: this script's own self-anchored
`{durable_root}` = its parent's parent, i.e. `${durable_root}/scripts/
skeptic_report.py`) -- unlike most of this plugin's scripts, a
`--durable-root` override is deliberately offered here since this is a
human-run reporting command, not a pipeline W-step bound to one fixed
install layout.

Exit codes: 0 on a successful render (with or without adverse findings --
advisory, never blocking); 2 on a fatal input problem (missing/unreadable/
schema-invalid `skeptic_triage.json`, or a malformed `manifest.json`).
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT_DEFAULT = SCRIPT_DIR.parent

try:
    from skeptic_constants import (
        SKEPTIC_TRIAGE_FILENAME,
        SUSPICION_WORKLIST_FILENAME,
        SKEPTIC_TRIAGE_SCHEMA,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_report.py: cannot import skeptic_constants.py from {SCRIPT_DIR} ({exc}).\n"
        "skeptic_constants.py must be installed alongside skeptic_report.py under "
        "${durable_root}/scripts/ -- it supplies every filename/default this script uses. "
        "Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    import jsonschema
except ImportError as exc:
    sys.stderr.write(
        "skeptic_report.py requires the 'jsonschema' package (>=4.26.0) to validate "
        "skeptic_triage.json against skeptic-triage.schema.json. Install with:\n\n"
        "    pip install 'jsonschema>=4.26.0'\n\n"
        f"(import error: {exc})\n"
    )
    sys.exit(1)


class SkepticReportError(Exception):
    """Any fatal input problem -- reported to stderr, exit 2 (mirrors this
    plugin's other CLI scripts' FATAL convention)."""


def _read_json(path: Path, label: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SkepticReportError(f"{label} not found: {path}")
    except OSError as exc:
        raise SkepticReportError(f"{label} could not be read: {path} ({exc})")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkepticReportError(f"{label} is not valid JSON: {path} ({exc})")


def load_triage(triage_path: Path, schema_path: Path) -> dict:
    """Reads + schema-validates `skeptic_triage.json` (REQUIRED, fail-closed:
    a foreign/corrupt artifact must never render as if it were empty)."""
    doc = _read_json(triage_path, "skeptic_triage.json")
    schema = _read_json(schema_path, "skeptic-triage.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise SkepticReportError(
            f"{triage_path} failed schema validation at {where}: {first.message}"
        )
    return doc


def load_manifest(manifest_path: Path) -> dict:
    doc = _read_json(manifest_path, "manifest.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("blocks"), dict):
        raise SkepticReportError(f"manifest.json malformed (no blocks{{}} mapping): {manifest_path}")
    return doc


def load_worklist_risk_classes(worklist_path: Path) -> dict:
    """BEST-EFFORT enrichment only (see module docstring): maps
    `source_form -> risk_classes` from `suspicion_worklist.json`'s
    `entries[]`. Absent/unreadable/malformed -> `{}`, degrading every
    entity's risk-class display to "unavailable" rather than making the
    whole report fatal -- the worklist is never the binding input here,
    `skeptic_triage.json` is."""
    if not worklist_path.is_file():
        return {}
    try:
        doc = json.loads(worklist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return {}
    by_form = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("source_form"), str):
            by_form[e["source_form"]] = e.get("risk_classes") or []
    return by_form


def derive_quote(manifest: dict, evidence: dict) -> dict:
    """Slices `manifest.blocks[evidence['block']].plain_text` at the
    STORED offsets to derive the cited quote + its wider context, fresh,
    every time this runs -- a derived quote is NEVER itself stored
    anywhere (not in `skeptic_triage.json`, not by this function's caller).

    `quote` comes from `char_start`/`char_end` (the narrow cited span);
    `context` comes from `context_start`/`context_end` (the wider window
    the sha256 in `evidence` was computed over). These are deliberately
    two DIFFERENT offset pairs -- swapping them (using context_start/
    context_end where char_start/char_end belongs, or vice versa) would
    silently derive the wrong text for `quote` (it would show the whole
    context instead of the narrow citation) without raising any error,
    since both pairs are always in-bounds together. See
    `skeptic_report.test.py::test_adverse_derives_quote_from_char_offsets_
    not_context_offsets` for the regression this guards.

    This command never re-verifies evidence (that already happened in
    `skeptic_ready.py --verify-merged`); a report-time slicing failure
    (unknown block, out-of-range offsets) degrades to
    `unavailable_reason`, never an exception -- an advisory report must
    never crash on a single bad citation.
    """
    block_id = evidence.get("block")
    block = manifest.get("blocks", {}).get(block_id)
    if not isinstance(block, dict) or not isinstance(block.get("plain_text"), str):
        return {"quote": None, "context": None, "unavailable_reason": f"block {block_id!r} not found in manifest"}
    text = block["plain_text"]

    def _slice(start, end):
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
            return text[start:end]
        return None

    quote = _slice(evidence.get("char_start"), evidence.get("char_end"))
    context = _slice(evidence.get("context_start"), evidence.get("context_end"))
    if quote is None:
        return {"quote": None, "context": context, "unavailable_reason": "char_start/char_end out of range"}
    return {"quote": quote, "context": context, "unavailable_reason": None}


def coverage_label(coverage) -> str:
    """Renders `evidence_coverage` (`{cited, verified}`) as a short human
    label -- partial coverage (`verified < cited`) is ALWAYS explicitly
    flagged `(partial)`, never silently shown as if it were complete."""
    if not coverage:
        return "not recorded"
    cited = coverage.get("cited", 0)
    verified = coverage.get("verified", 0)
    if cited == 0:
        return "no citations"
    if verified >= cited:
        return f"{verified}/{cited} verified"
    return f"{verified}/{cited} verified (partial)"


def build_report(triage: dict, manifest: dict, worklist_risk_classes: "dict | None" = None) -> dict:
    """The advisory summary as a plain data structure -- one entry per
    triage record, carrying its derived quote(s) (computed fresh here, see
    `derive_quote`), best-effort `risk_classes` (`None` when the source_form
    has no worklist entry / no worklist was given), and a human-readable
    `evidence_coverage_label`. Kept separate from `format_report`'s text
    rendering so tests assert the DATA, not a text layout."""
    worklist_risk_classes = worklist_risk_classes or {}
    entries = []
    for rec in triage.get("records", []):
        source_form = rec.get("source_form")
        out = {
            "assignment_id": rec.get("assignment_id"),
            "source_form": source_form,
            "verdict": rec.get("verdict"),
            "rationale": rec.get("rationale"),
            "risk_classes": worklist_risk_classes.get(source_form),
            "evidence_coverage_label": coverage_label(rec.get("evidence_coverage")),
            "notes": rec.get("notes") or [],
        }
        if "evidence" in rec:
            out["evidence"] = derive_quote(manifest, rec["evidence"])
        if "referents" in rec:
            out["referents"] = [
                {
                    "disambiguator": referent.get("disambiguator"),
                    "evidence": derive_quote(manifest, referent["evidence"]),
                }
                for referent in rec["referents"]
            ]
        entries.append(out)
    return {"run_id": triage.get("run_id"), "record_count": len(entries), "entries": entries}


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _sanitize(s):
    """Neutralizes an agent-authored string before `format_report` prints
    it (fix L12): every triage record field rendered below (`run_id`,
    `source_form`, `verdict`, `risk_classes`, `rationale`, `notes`/
    disambiguators, the derived evidence `quote`) was authored by the
    skeptic codex pass, and this report is its SOLE human-facing consumer
    -- a human reads it to make identity decisions. Without sanitizing,
    an embedded newline could forge a fake "[n] SomeName (verdict: ...)"
    line, and an embedded ANSI/control escape (e.g. "\x1b[2J") could
    clear or spoof the terminal. Newlines/carriage returns are collapsed
    to a visible "\\n" marker (never silently dropped) and every
    remaining C0/C1 control character (0x00-0x1f, 0x7f-0x9f, including
    ESC) is stripped. A string with no control characters round-trips
    unchanged."""
    if not isinstance(s, str):
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return _CONTROL_CHARS_RE.sub("", s)


def format_report(report: dict) -> str:
    """Human-facing text rendering of `build_report`'s output. Deliberately
    unstructured prose (this is an advisory command for a human reviewer,
    not a machine-consumed artifact -- nothing downstream parses this
    string; see `canon_adjudication_audit.py`'s own JSON-line contract for
    the actual machine-checkable gate). Every agent-authored field is run
    through `_sanitize` first (see its docstring for why)."""
    lines = [
        f"Skeptic Triage Report -- run {_sanitize(report['run_id'])} -- {report['record_count']} record(s)",
        "=" * 60,
    ]
    if not report["entries"]:
        lines.append("(no adverse findings)")
    for i, e in enumerate(report["entries"], 1):
        lines.append(f"[{i}] {_sanitize(e['source_form'])}  (verdict: {_sanitize(e['verdict'])})")
        if e["risk_classes"] is not None:
            risk_classes = ", ".join(_sanitize(c) for c in e["risk_classes"])
            lines.append(f"    risk classes: {risk_classes or '(none)'}")
        else:
            lines.append("    risk classes: unavailable (no worklist entry)")
        lines.append(f"    rationale: {_sanitize(e['rationale'])}")
        lines.append(f"    evidence_coverage: {e['evidence_coverage_label']}")
        if "evidence" in e:
            ev = e["evidence"]
            if ev["unavailable_reason"]:
                lines.append(f"    evidence: unavailable ({ev['unavailable_reason']})")
            else:
                lines.append(f"    evidence quote: {_sanitize(ev['quote'])!r}")
        if "referents" in e:
            for r in e["referents"]:
                ev = r["evidence"]
                if ev["unavailable_reason"]:
                    shown = f"unavailable ({ev['unavailable_reason']})"
                else:
                    shown = repr(_sanitize(ev["quote"]))
                lines.append(f"    referent [{_sanitize(r['disambiguator'])}]: {shown}")
        if e["notes"]:
            lines.append(f"    notes: {', '.join(_sanitize(n) for n in e['notes'])}")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "SEPARATE, advisory-only report over skeptic_triage.json (RFC #215 Phase 2). "
            "Never a gate: always exits 0 on a structurally valid triage artifact, "
            "regardless of what it contains. See this file's own module docstring."
        ),
    )
    parser.add_argument(
        "--durable-root", metavar="PATH", default=None,
        help=f"Base directory every other default path is computed from "
             f"(default: this script's own self-anchored durable root, "
             f"{DURABLE_ROOT_DEFAULT}).",
    )
    parser.add_argument(
        "--triage", metavar="PATH", default=None,
        help=f"Override the skeptic_triage.json path (default: "
             f"{{durable_root}}/{SKEPTIC_TRIAGE_FILENAME}).",
    )
    parser.add_argument(
        "--manifest-path", metavar="PATH", default=None,
        help="Override manifest.json (default: {durable_root}/manifest.json). "
             "Needed to derive every cited quote from the stored offsets.",
    )
    parser.add_argument(
        "--worklist-path", metavar="PATH", default=None,
        help=f"Override suspicion_worklist.json (default: "
             f"{{durable_root}}/{SUSPICION_WORKLIST_FILENAME}). Best-effort "
             f"risk_classes enrichment only -- never fatal when absent.",
    )
    parser.add_argument(
        "--schemas-dir", metavar="PATH", default=None,
        help="Override the schemas directory (default: {durable_root}/schemas), "
             f"used to locate {SKEPTIC_TRIAGE_SCHEMA}.",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    durable_root = Path(args.durable_root) if args.durable_root else DURABLE_ROOT_DEFAULT
    triage_path = Path(args.triage) if args.triage else durable_root / SKEPTIC_TRIAGE_FILENAME
    manifest_path = Path(args.manifest_path) if args.manifest_path else durable_root / "manifest.json"
    worklist_path = Path(args.worklist_path) if args.worklist_path else durable_root / SUSPICION_WORKLIST_FILENAME
    schemas_dir = Path(args.schemas_dir) if args.schemas_dir else durable_root / "schemas"
    schema_path = schemas_dir / SKEPTIC_TRIAGE_SCHEMA

    try:
        triage = load_triage(triage_path, schema_path)
        manifest = load_manifest(manifest_path)
    except SkepticReportError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    worklist_risk_classes = load_worklist_risk_classes(worklist_path)
    report = build_report(triage, manifest, worklist_risk_classes)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
