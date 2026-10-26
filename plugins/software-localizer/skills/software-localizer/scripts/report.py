"""Write `R/reports/<locale>.md`, a native-speaker-readable report:
exported and pending candidates with source and context,
adopted entries (translated with no candidate — `ledger.adopt` took the
project's own value) shown with that current value, audit coverage and saved
findings with or without proposed replacements, escalated ids with their problems,
canon candidates raised by
reviews, usage-research records a native speaker should check, and notes on
`existing`/`human_locked` messages.

Reads only durable state (the ledger, `messages.json`, `canon.json`,
`canon.research/<locale>.json`, and each review or audit run's
`new_canon_candidates.json` side file that `packets.py accept` writes) —
never a model turn's raw output, which is either already folded into the
ledger or was refused before it got here.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import canon as canon_mod  # noqa: E402
import lz_common  # noqa: E402
import ledger as ledger_mod  # noqa: E402


def _value_text(value):
    """A message value or source can be a plain string or `{"forms": [...]}`
    (plural forms); render either as one human-readable line."""
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


_MISSING = "(no longer in the project)"


def _missing_line(msg_id: str) -> str:
    return f"- `{msg_id}` — {_MISSING}"


def _context_or_missing(message) -> str:
    return _context_line(message) if message else _MISSING


def _problem_text(p: dict) -> str:
    return f"{p.get('check', p.get('kind', '?'))}: {p.get('detail', p.get('text', ''))}"


def _row_exported(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    value = (entry.get("candidate") or {}).get("value")
    return [
        f"- `{msg_id}` — {_context_line(message)}",
        f"  - source: {_value_text(message.get('source'))}",
        f"  - value: {_value_text(value)}",
    ]


def _row_candidate(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    value = (entry.get("candidate") or {}).get("value")
    return [
        f"- `{msg_id}` — {_context_line(message)}",
        f"  - source: {_value_text(message.get('source'))}",
        f"  - candidate: {_value_text(value)}",
    ]


def _row_adopted(msg_id, entry, message, current_value):
    if message is None:
        return [_missing_line(msg_id)]
    return [
        f"- `{msg_id}` — {_context_line(message)}",
        f"  - source: {_value_text(message.get('source'))}",
        f"  - value: {_value_text(current_value)}",
    ]


def _row_pending(msg_id, entry, message):
    if message is None:
        return [_missing_line(msg_id)]
    out = [f"- `{msg_id}` — {_context_line(message)}", f"  - source: {_value_text(message.get('source'))}"]
    candidate = entry.get("candidate")
    if candidate and candidate.get("problems"):
        for p in candidate["problems"]:
            out.append(f"  - problem: {_problem_text(p)}")
    return out


def _row_escalated(msg_id, entry, message):
    out = [f"- `{msg_id}` — {_context_or_missing(message)}"]
    candidate = entry.get("candidate") or {}
    for p in candidate.get("problems", []):
        out.append(f"  - problem: {_problem_text(p)}")
    proposal = entry.get("review_proposal")
    if proposal and proposal.get("value") is not None:
        out.append(f"  - kept proposal (never installed): {_value_text(proposal.get('value'))}")
    return out


def _row_audit(msg_id, entry, message, current_value, record=None, stale=False, proposal=None, checks_stale=False):
    out = [f"- `{msg_id}` — {_context_or_missing(message)}"]
    if message is not None:
        source_label = "current source" if stale else "source"
        out.append(f"  - {source_label}: {_value_text(message.get('source'))}")
    out.append(f"  - current: {_value_text(current_value)}")
    if record:
        scope = record.get("scope", "full")
        scope_label = "full audit" if scope == "full" else f"restricted canon audit {scope}"
        out.append(f"  - verdict: {record.get('verdict')} ({'stale' if stale else 'current'} {scope_label})")
        out.append(f"  - accepted: {record.get('accepted_at', '?')}; run: {record.get('run', '?')}")
        out.append(f"  - run directory: {record.get('run_dir', '?')}")
        out.append(f"  - packet sha256: {record.get('packet_sha256', '?')}")
        for field, digest in sorted(record.get("snapshot", {}).items()):
            out.append(f"  - audited {field}: {digest}")
        current_checks = record.get("current_checks")
        if current_checks:
            out.append(f"  - audited value: {_value_text(record.get('audited_value'))}")
            out.append(f"  - current-value checks: {current_checks['status']} ({'stale' if checks_stale else 'current'})")
            for problem in current_checks.get("problems", []):
                out.append(f"  - current-value observation: {_problem_text(problem)}")
            for gap in current_checks.get("unavailable", []):
                out.append(f"  - current-value coverage unavailable: {gap}")
            if current_checks["problems"]:
                out.append("  - owner hold: adjudicate these observations in context; they authorize no rewrite, "
                           "brand insertion, whitespace waiver or occurrence-scope change.")
        else:
            out.append("  - current-value checks: unreviewed (legacy audit)")
        if record.get("proposal_checks"):
            replacement = record["proposal_checks"]
            out.append(f"  - replacement checks: {replacement['status']} ({'stale' if checks_stale else 'current'})")
            for problem in replacement.get("problems", []):
                out.append(f"  - replacement observation: {_problem_text(problem)}")
            for gap in replacement.get("unavailable", []):
                out.append(f"  - replacement coverage unavailable: {gap}")
    if proposal:
        if proposal.get("audit_scope") is None:
            out.append(f"  - proposal scope: unknown (legacy record); run: {proposal.get('run', '?')}")
        if proposal.get("value") is None:
            out.append("  - unresolved: no proposed replacement; needs follow-up")
        else:
            out.append(f"  - proposed: {_value_text(proposal.get('value'))} (checks {proposal.get('checks')})")
        if stale or checks_stale:
            out.append("  - proposal snapshot: stale; audit again before approval")
    elif record and record.get("verdict") == "fail" and record.get("proposed") is None:
        out.append("  - unresolved: no proposed replacement; needs follow-up")
    if (record and record.get("proposed") is not None
            and (not proposal or proposal.get("value") != record["proposed"])):
        out.append(f"  - saved replacement (not active): {_value_text(record['proposed'])}")
        if record.get("scope", "full") == "full":
            out.append("  - next: resolve any outstanding proposal, then rerun this full audit with --fresh.")
        else:
            out.append("  - next: resolve any outstanding proposal, then rerun the restricted canon audit "
                       f"with --entry {record['scope']}.")
    issues = record.get("issues", []) if record else (proposal or {}).get("issues", [])
    for issue in issues:
        label = "saved issue" if stale else "issue"
        out.append(f"  - {label}: {issue.get('kind', '?')}: {issue.get('text', '')}")
    for issue in (proposal or {}).get("issues", []):
        if issue not in issues:
            out.append(f"  - proposal issue: {issue.get('kind', '?')}: {issue.get('text', '')}")
    return out


def _audit_rows(msg_id, entry, message, cfg, canon_lock, locale, check_inputs):
    """Keep full and restricted verdicts visible without duplicating their proposal.

    Compare to current inputs rather than sync's cached hashes. The saved
    hashes and run provenance identify what an obsolete finding judged.
    """
    current_value = message.get("targets", {}).get(locale) if message else None
    if not (entry.get("audit") or entry.get("canon_audits") or entry.get("audit_proposal")):
        return []
    snapshot = ledger_mod.audit_snapshot(message, locale, cfg, canon_lock) if message else None
    proposal = entry.get("audit_proposal")
    proposal_scope = proposal.get("audit_scope") if proposal else None
    records = [("full", entry.get("audit")), *sorted((entry.get("canon_audits") or {}).items())]
    rows = []
    used_proposal = False
    for scope, record in records:
        if not record:
            continue
        checks_status = ledger_mod.audit_checks_status(record, snapshot, check_inputs)
        stale = current_value is None or record.get("snapshot") != snapshot
        scoped_proposal = proposal if proposal_scope == scope else None
        if (record.get("verdict") == "fail" or record.get("issues") or stale or scoped_proposal
                or record.get("proposed") is not None or checks_status != "pass"):
            rows.append((msg_id, entry, message, current_value, record, stale, scoped_proposal,
                         checks_status == "stale"))
            used_proposal = used_proposal or scoped_proposal is not None
    if proposal and not used_proposal:
        # Older ledgers have a proposal but no complete audit verdict.
        # Preserve it while checking every snapshot field it actually kept.
        proposal_hashes = {
            "source_sha256": proposal.get("source_sha256"),
            "value_sha256": proposal.get("audited_target_sha256"),
            "context_sha256": proposal.get("context_sha256"),
            "style_sha256": proposal.get("style_sha256"),
            "canon_sha256": proposal.get("canon_sha256"),
        }
        stale = current_value is None or snapshot is None or any(
            digest is not None and snapshot.get(field) != digest
            for field, digest in proposal_hashes.items()
        )
        rows.append((msg_id, entry, message, current_value, None, stale, proposal))
    return rows


def _row_retained_audit(msg_id, finding):
    scope = finding.get("audit_scope")
    if scope is None:
        scope_label = "unknown (legacy record)"
    else:
        scope_label = "full audit" if scope == "full" else f"restricted canon audit {scope}"
    value = finding.get("value")
    out = [
        f"- `{msg_id}` — historical evidence; not an active proposal",
        f"  - saved scope: {scope_label}; run: {finding.get('run', '?')}",
        f"  - saved replacement: {_value_text(value) if value is not None else 'none'}",
        f"  - saved checks: {finding.get('checks') or 'not recorded'}",
    ]
    if finding.get("run_dir"):
        out.append(f"  - saved run directory: {finding['run_dir']}")
    for field, digest in sorted(finding.items()):
        if field.endswith("_sha256"):
            out.append(f"  - saved {field}: {digest}")
    for issue in finding.get("issues", []):
        out.append(f"  - saved issue: {issue.get('kind', '?')}: {issue.get('text', '')}")
    for problem in finding.get("problems", []):
        out.append(f"  - saved problem: {_problem_text(problem)}")
    return out


def _audit_summary(lines, coverage):
    lines.extend(["## Audit coverage", "", f"- reviewed: {coverage['reviewed']} / {coverage['total']}"])
    for key in ("pass", "fail", "unresolved", "stale", "unreviewed"):
        lines.append(f"- {key}: {coverage[key]}")
    lines.extend([
        "",
        "Total includes every currently collected message, including messages without a target value. "
        "Reviewed means a full verdict for the current snapshot; unresolved includes failed, stale and unreviewed messages.",
        "",
    ])
    for key in ("stale", "unreviewed"):
        ids = ", ".join(f"`{msg_id}`" for msg_id in coverage["ids"][key]) or "_none_"
        lines.extend([f"- {key} IDs: {ids}"])
    lines.append("")


def _row_notes(msg_id, entry, message):
    """A note is a ledger record (`{"at": ..., "note": text, "problems"?:
    [...]}`), never a bare string — render its text, and any problems it
    carries, as readable lines instead of the dict itself."""
    out = [f"- `{msg_id}` — {_context_or_missing(message)}"]
    for note in entry.get("notes", []):
        out.append(f"  - note: {note.get('note', '')}")
        for p in note.get("problems", []):
            out.append(f"    - problem: {_problem_text(p)}")
    return out


def _collect_canon_candidates(root: Path, locale: str) -> list:
    run_dir = root / "runs" / locale
    candidates = []
    if not run_dir.is_dir():
        return candidates
    for f in sorted(run_dir.rglob("new_canon_candidates.json")):
        if f.is_file():
            data = lz_common.read_json(f, f"new canon candidates ({f})")
            candidates.extend(data.get("candidates", []))
    return candidates


def _row_research(entry_id, record, translation, locale):
    """One flagged research record, with the canon's current value beside the
    recommendation so a native speaker sees both without opening a file."""
    summary = canon_mod.research_summary(record)
    if translation:
        canon_line = f"{translation.get('value')} ({translation.get('status')})"
    else:
        canon_line = f"(no {locale} translation)"
    if summary["verdict"] is None:
        verification = "not run"
    else:
        verification = f"{summary['verdict']}, {summary['confirmed']} of {summary['findings']} sources confirmed"
    out = [
        f"- `{entry_id}` — {record.get('source', '?')}",
        f"  - canon: {canon_line}",
        f"  - recommendation: {summary['recommendation']} (confidence {summary['confidence']})",
        f"  - verification: {verification}",
    ]
    if summary["better"]:
        out.append(f"  - better: {summary['better']}")
    out.append(f"  - check: {'; '.join(canon_mod.research_flags(record))}")
    out.append(f"  - note: {(record.get('research') or {}).get('note', '')}")
    for phase in ("research", "verification"):
        result = record.get(phase) or {}
        progress = result.get("progress")
        if progress:
            out.append(f"  - {phase} progress: {progress['status']}")
            for task in progress["remaining"]:
                out.append(f"    - remaining: {task}")
    return out


def build_report(root: Path, locale: str) -> str:
    cfg = lz_common.load_config(root)
    if locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)

    messages = lz_common.load_messages(root / "messages.json")
    by_id = {m["id"]: m for m in messages["messages"]}
    ledger_data = ledger_mod.load(root)
    entries = ledger_mod.locale_entries(ledger_data, locale)
    canon_lock = canon_mod.load_lock(root)
    audit_coverage = ledger_mod.audit_coverage(ledger_data, messages, cfg, canon_lock, locale)
    check_inputs = ledger_mod.audit_check_inputs(root, cfg)
    mechanical_counts = dict.fromkeys(("pass", "observations", "unavailable", "stale", "unreviewed"), 0)
    for message in messages["messages"]:
        snapshot = ledger_mod.audit_snapshot(message, locale, cfg, canon_lock)
        record = entries.get(message["id"], {}).get("audit")
        mechanical_counts[ledger_mod.audit_checks_status(record, snapshot, check_inputs)] += 1

    exported, adopted, ready, needs_translation = [], [], [], []
    escalated, audit_findings, existing_notes = [], [], []
    retained_audit_findings = []
    counts: dict = {}

    for msg_id, entry in sorted(entries.items()):
        state = entry.get("state", "?")
        counts[state] = counts.get(state, 0) + 1
        message = by_id.get(msg_id)

        message_findings = _audit_rows(msg_id, entry, message, cfg, canon_lock, locale, check_inputs)
        audit_findings.extend(message_findings)
        retained_audit_findings.extend(
            (msg_id, finding) for finding in entry.get("legacy_audit_findings", [])
        )

        if state == "translated":
            if entry.get("candidate"):
                exported.append((msg_id, entry, message))
            else:
                # `ledger.adopt` moves existing/human-locked entries to
                # "translated" without a candidate -- it treats the
                # project's own value as the baseline. Reading
                # `candidate.value` for one of these would print "None"
                # under "Exported"; show the project's current value
                # instead, under its own label.
                current_value = message.get("targets", {}).get(locale) if message else None
                adopted.append((msg_id, entry, message, current_value))
        elif state in ("pending", "stale"):
            (ready if ledger_mod.candidate_ready(entry) else needs_translation).append((msg_id, entry, message))
        elif state == "escalated":
            escalated.append((msg_id, entry, message))
        elif state in ("existing", "human_locked"):
            candidate = entry.get("candidate")
            # `accept-audit` leaves the entry existing/human_locked and
            # clears `audit_proposal`; a ready, accepted replacement must
            # still show up as actionable, not as a plain note.
            if candidate and candidate.get("accepted_by") and ledger_mod.candidate_ready(entry):
                ready.append((msg_id, entry, message))
            elif not message_findings:
                existing_notes.append((msg_id, entry, message))

    canon_candidates = _collect_canon_candidates(root, locale)

    lines = [f"# Localization report — {locale}", "", f"Generated {lz_common.now_iso()}.", "",
             "## Summary", ""]
    for state in sorted(counts):
        lines.append(f"- {state}: {counts[state]}")
    lines.append("")
    _audit_summary(lines, audit_coverage)
    lines.extend(["## Current-value mechanical coverage", ""])
    lines.extend(f"- {key}: {count}" for key, count in mechanical_counts.items())
    lines.extend(["", "Independent of the model verdict and replacement checks. Observations need owner "
                  "adjudication; unavailable, stale and unreviewed values have no clean mechanical result. "
                  "Counts cover full audits; restricted canon audits retain separate observations below.", ""])

    _section(lines, "Exported", exported, _row_exported)
    _section(lines, "Adopted", adopted, _row_adopted)
    _section(lines, "Ready to export", ready, _row_candidate)
    _section(lines, "Still needs translation", needs_translation, _row_pending)
    _section(lines, "Escalated", escalated, _row_escalated)
    _section(lines, "Audit findings", audit_findings, _row_audit)
    _section(lines, "Retained audit findings", retained_audit_findings, _row_retained_audit)
    _section(lines, "Existing / human-locked translations", existing_notes, _row_notes)

    lines.append("## Canon candidates raised by reviews")
    lines.append("")
    if canon_candidates:
        for cand in canon_candidates:
            lines.append(f"- **{cand.get('source', '?')}** ({cand.get('kind', '?')}): {cand.get('note', '')}")
    else:
        lines.append("_none_")
    lines.append("")

    records = canon_mod.load_research(root, locale)["entries"]
    canon_translations = {e["id"]: e.get("translations") or {} for e in canon_mod.load(root)["entries"]}
    completed = sum(1 for r in records.values() if not canon_mod.resource_limited(r.get("research")))
    verified = sum(1 for r in records.values() if canon_mod.research_summary(r)["verdict"] is not None)
    flagged = [(eid, r) for eid, r in sorted(records.items()) if canon_mod.research_flags(r)]
    lines.append("## Canon usage research")
    lines.append("")
    lines.append(f"- researched: {completed}, verified: {verified}, flagged for a native speaker: {len(flagged)}")
    lines.append("")
    if flagged:
        for eid, record in flagged:
            translation = canon_translations.get(eid, {}).get(locale)
            lines.extend(_row_research(eid, record, translation, locale))
    else:
        lines.append("_none_")
    lines.append("")

    lines.extend(["## Canon work awaiting another turn", ""])
    pending = []
    for eid, record in sorted(records.items()):
        if record.get("stale_note"):
            pending.append(f"- `{eid}`: canon note changed; scope fresh reassessment before continuing")
            continue
        for phase in ("research", "verification"):
            result = record.get(phase)
            if canon_mod.resource_limited(result):
                packet = (record.get("researched") if phase == "research" else result) or {}
                pending.append(f"- `{eid}` / {phase}: resource_limited; packet: {packet.get('packet', 'legacy')}")
                pending.extend(f"  - remaining: {task}" for task in result["progress"]["remaining"])
    lines.extend(pending or ["_none_"])
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
