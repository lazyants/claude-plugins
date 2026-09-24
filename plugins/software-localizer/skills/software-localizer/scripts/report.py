#!/usr/bin/env python3
"""Write `R/reports/<locale>.md`, a native-speaker-readable report (plan
section 11): exported and pending candidates with source and context, audit
findings with proposals, escalated ids with their problems, canon candidates
raised by reviews, and notes on `existing`/`human_locked` messages.

Reads only durable state (the ledger, `messages.json`, and each review or
audit run's `new_canon_candidates.json` side file that `packets.py accept`
writes) — never a model turn's raw output, which is either already folded
into the ledger or was refused before it got here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lz_common  # noqa: E402
import ledger as ledger_mod  # noqa: E402


def _ready(entry: dict) -> bool:
    cand = entry.get("candidate")
    if not cand or cand.get("checks") != "pass":
        return False
    verdict = cand.get("verdict")
    return bool(verdict) and verdict.get("verdict") == "pass" and verdict.get("value_sha256") == cand.get("value_sha256")


def _source_text(message: dict):
    source = message.get("source")
    if isinstance(source, dict):
        return " / ".join(source.get("forms", []))
    return source


def _value_text(value):
    if isinstance(value, dict):
        return " / ".join(value.get("forms", []))
    return value


def _context_line(message: dict) -> str:
    context = message.get("context", {})
    parts = [context.get("file", "?")]
    if context.get("key"):
        parts.append(context["key"])
    return ":".join(parts)


def _section(lines: list, title: str, rows: list, row_fn) -> None:
    lines.append(f"## {title}")
    lines.append("")
    if not rows:
        lines.append("_none_")
        lines.append("")
        return
    for row in rows:
        lines.extend(row_fn(*row))
    lines.append("")


def _missing_line(msg_id: str) -> str:
    return f"- `{msg_id}` — (no longer in the project)"


def _row_exported(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    value = (entry.get("candidate") or {}).get("value")
    return [
        f"- `{msg_id}` — {_context_line(message)}",
        f"  - source: {_source_text(message)}",
        f"  - value: {_value_text(value)}",
    ]


def _row_candidate(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    value = (entry.get("candidate") or {}).get("value")
    return [
        f"- `{msg_id}` — {_context_line(message)}",
        f"  - source: {_source_text(message)}",
        f"  - candidate: {_value_text(value)}",
    ]


def _row_pending(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    out = [f"- `{msg_id}` — {_context_line(message)}", f"  - source: {_source_text(message)}"]
    candidate = entry.get("candidate")
    if candidate and candidate.get("problems"):
        for p in candidate["problems"]:
            out.append(f"  - problem: {p.get('check', p.get('kind', '?'))}: {p.get('detail', p.get('text', ''))}")
    return out


def _row_escalated(msg_id, entry, message):
    out = [f"- `{msg_id}` — {_context_line(message) if message else '(no longer in the project)'}"]
    candidate = entry.get("candidate") or {}
    for p in candidate.get("problems", []):
        out.append(f"  - problem: {p.get('check', p.get('kind', '?'))}: {p.get('detail', p.get('text', ''))}")
    proposal = entry.get("review_proposal")
    if proposal and proposal.get("value") is not None:
        out.append(f"  - kept proposal (never installed): {_value_text(proposal.get('value'))}")
    return out


def _row_audit(msg_id, entry, message, current_value):
    out = [f"- `{msg_id}` — {_context_line(message) if message else '(no longer in the project)'}"]
    if message is not None:
        out.append(f"  - source: {_source_text(message)}")
    out.append(f"  - current: {_value_text(current_value)}")
    proposal = entry["audit_proposal"]
    out.append(f"  - proposed: {_value_text(proposal.get('value'))} (checks {proposal.get('checks')})")
    for issue in proposal.get("issues", []):
        out.append(f"  - issue: {issue.get('kind', '?')}: {issue.get('text', '')}")
    return out


def _row_notes(msg_id, entry, message):
    out = [f"- `{msg_id}` — {_context_line(message) if message else '(no longer in the project)'}"]
    for note in entry.get("notes", []):
        out.append(f"  - note: {note}")
    return out


def _collect_canon_candidates(root: Path, locale: str) -> list:
    run_dir = root / "runs" / locale
    candidates = []
    if not run_dir.is_dir():
        return candidates
    for batch_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        f = batch_dir / "new_canon_candidates.json"
        if f.is_file():
            data = lz_common.read_json(f, f"new canon candidates ({f})")
            candidates.extend(data.get("candidates", []))
    return candidates


def build_report(root: Path, locale: str) -> str:
    cfg = lz_common.load_config(root)
    if locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)

    messages = lz_common.load_messages(root / "messages.json")
    by_id = {m["id"]: m for m in messages["messages"]}
    ledger_data = ledger_mod.load(root)
    entries = ledger_data.get("locales", {}).get(locale, {})

    exported, ready, needs_translation = [], [], []
    escalated, audit_findings, existing_notes = [], [], []
    counts: dict = {}

    for msg_id, entry in sorted(entries.items()):
        state = entry.get("state", "?")
        counts[state] = counts.get(state, 0) + 1
        message = by_id.get(msg_id)

        if entry.get("audit_proposal"):
            current_value = message.get("targets", {}).get(locale) if message else None
            audit_findings.append((msg_id, entry, message, current_value))

        if state == "translated":
            exported.append((msg_id, entry, message))
        elif state in ("pending", "stale"):
            (ready if _ready(entry) else needs_translation).append((msg_id, entry, message))
        elif state == "escalated":
            escalated.append((msg_id, entry, message))
        elif state in ("existing", "human_locked"):
            candidate = entry.get("candidate")
            # `accept-audit` leaves the entry existing/human_locked and
            # clears `audit_proposal` (plan section 9) -- without this, a
            # ready, accepted replacement fell through to a plain note and
            # never showed up as actionable at all.
            if candidate and candidate.get("accepted_by") and _ready(entry):
                ready.append((msg_id, entry, message))
            elif not entry.get("audit_proposal"):
                existing_notes.append((msg_id, entry, message))

    canon_candidates = _collect_canon_candidates(root, locale)

    lines = [f"# Localization report — {locale}", "", f"Generated {lz_common.now_iso()}.", "",
             "## Summary", ""]
    for state in sorted(counts):
        lines.append(f"- {state}: {counts[state]}")
    lines.append("")

    _section(lines, "Exported", exported, _row_exported)
    _section(lines, "Ready to export", ready, _row_candidate)
    _section(lines, "Still needs translation", needs_translation, _row_pending)
    _section(lines, "Escalated", escalated, _row_escalated)
    _section(lines, "Audit findings", audit_findings, _row_audit)
    _section(lines, "Existing / human-locked translations", existing_notes, _row_notes)

    lines.append("## Canon candidates raised by reviews")
    lines.append("")
    if canon_candidates:
        for cand in canon_candidates:
            lines.append(f"- **{cand.get('source', '?')}** ({cand.get('kind', '?')}): {cand.get('note', '')}")
    else:
        lines.append("_none_")
    lines.append("")

    return "\n".join(lines) + "\n"


def do_report(root: Path, locale: str) -> dict:
    text = build_report(root, locale)
    out_path = root / "reports" / f"{locale}.md"
    lz_common.atomic_write_text(out_path, text)
    return {"ok": True, "locale": locale, "report": str(out_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = lz_common.make_parser("report.py", "Write a native-speaker report for one locale.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--locale", required=True)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    root = lz_common.resolve_root(args.root)
    result = do_report(root, args.locale)
    lz_common.emit(result)
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
