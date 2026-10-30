"""Build and accept model-turn packets: translate, review, audit, canon,
research, research_verify.
Every packet is a frozen intended input; native Claude turns also require a
receipt from the actual Agent request/result before acceptance. A turn is
validated against that snapshot, never against a possibly-changed
`messages.json` or `canon.json` read again later.

The coverage turn is built and accepted by `adapter_check.py`, not here: its
packet is not id-keyed the way the four kinds here are, and the
cross-module API gives this module no way to read its request.

Design decisions worth calling out:

- `build --kind canon` takes no `--locale` (it proposes canon for every
  target locale at once, matching `canon_TASK.md`'s reply shape) and scans
  every message in `messages.json`; its runs live under `R/runs/_canon/`,
  mirroring `R/runs/_coverage/`.
- The restricted canon audit `canon.py change` prepares
  (`R/runs/<locale>/canon-audit-<id>.json`) is an **audit** packet, not a
  canon one: `audit_TASK.md` names the field (`canon_entry` set means "judge
  only how that one entry is rendered"), and `canon.py change`'s request
  carries exactly one entry's `occurrences`. `build --kind audit --locale L
  --entry ID` reads that request file and scopes the audit packet to its
  occurrences, with `canon_entry` set to the entry/before/after/reason.
- `accept --kind translate` additionally excludes an id whose current
  candidate already has `checks: "pass"` and a `verdict: "pass"` bound to
  that candidate's hash from a later `build --kind translate` (the
  translate/review loop ends when `build` finds nothing left; the ledger
  state alone (`pending`/`stale`) does not change until export, so without
  this exclusion `build` would offer an already-approved candidate for
  retranslation forever).
- The two usage-research kinds (`research`, `research_verify`) serve the
  canon step, not the ledger: `build` reads `canon.json` and the locale's
  `R/canon.research/<locale>.json` (never the ledger or the lock), and
  `accept` writes only that locale's research file -- it never loads or
  saves the ledger, so a research accept cannot touch a candidate, and
  nothing here feeds any hash a candidate is checked against. Their batches
  hold `RESEARCH_BATCH_SIZE` terms, not the config's `batch_size`: web
  research per term is far heavier than translating a message. `--entry` is
  repeatable for these kinds (re-run or narrow the scope); for `audit` it
  still names exactly one entry.
- A `research_verify` verdict binds to the `research_sha256` of the
  research it was cast on. `accept` re-reads the locale's file and treats a
  verdict whose research was replaced since the build as missing, so a
  verdict never attaches to research it did not check.
- Every accepted audit `fail` is stored on the ledger entry as
  `audit_proposal` (an extra field beyond `ledger.py`'s documented entry
  schema; JSON round-trips it fine) — there is no setter for it in
  `ledger.py`, so it is read and written as a plain dict field, matching
  `accept-audit`'s own job of turning a checked replacement into a candidate.
  A null value keeps an unresolved finding, with no checks or candidate.
  A `review` `fail`
  with a `proposed` value routes here too when the candidate it is
  reviewing has `origin: "audit"` (i.e. it was itself installed by
  `accept-audit` and is now being reviewed per that command's "still needs a
  review verdict" note): the checked replacement goes back into
  `audit_proposal`, not straight into a candidate, so it still needs
  `accept-audit` again rather than sitting as an unexportable candidate with
  `accepted_by: None`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapter_client  # noqa: E402
import canon as canon_mod  # noqa: E402
import checks as checks_mod  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import lz_common  # noqa: E402
import packet_handoff  # noqa: E402

KINDS = ("translate", "review", "audit", "canon")
RESEARCH_KINDS = ("research", "research_verify")
RESEARCH_BATCH_SIZE = 8
RESEARCH_EXAMPLES = 5
RESEARCH_TERM_KINDS = ("term", "ui_label")
TRANSLATE_STATES = ("pending", "stale")
AUDIT_STATES = ("existing", "translated", "human_locked")
CANON_PSEUDO_LOCALE = "_canon"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def default_templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def render_prompt(templates_dir, kind: str, packet: dict) -> str:
    prefix, suffix = prompt_instructions(templates_dir, kind)
    return prefix + json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=True) + suffix


def prompt_instructions(templates_dir, kind: str) -> tuple[str, str]:
    """Render `<kind>_TASK.md`, replacing the literal line `{{PACKET_JSON}}`
    with the packet's JSON. Raises `LookupError` naming the problem when the
    template file is missing or does not carry the placeholder line — the
    caller turns that into an `EXIT_CANNOT` refusal."""
    template_path = Path(templates_dir) / f"{kind}_TASK.md"
    if not template_path.is_file():
        raise LookupError(f"template is missing: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    positions = [n for n, line in enumerate(lines) if line.strip() == "{{PACKET_JSON}}"]
    if len(positions) != 1:
        raise LookupError(f"template needs exactly one {{{{PACKET_JSON}}}} line: {template_path}")
    n = positions[0]
    return ("\n".join(lines[:n]) + "\n" if n else "",
            "\n" + "\n".join(lines[n + 1:]) if n + 1 < len(lines) else "")


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def batch_prefix(msg_id: str) -> str:
    if "." in msg_id:
        return msg_id.rsplit(".", 1)[0]
    return msg_id


def _sanitize_batch_name(text: str) -> str:
    out = [ch if (ch.isalnum() or ch in "-_.") else "_" for ch in text]
    result = "".join(out).strip("_.")
    return result or "batch"


def make_batches(ids: list, batch_size: int) -> list:
    """Pack consecutive prefix groups up to `batch_size`, keeping each
    fitting group whole. Oversized groups split into same-prefix batches
    on their own, without mixing their remainder with neighboring groups.
    Sorted prefixes keep related messages adjacent even in nested catalogs."""
    groups: dict = {}
    for i in sorted(ids):
        groups.setdefault(batch_prefix(i), []).append(i)
    batches = []
    pending = []
    limit = max(1, batch_size)
    for prefix in sorted(groups):
        group_ids = groups[prefix]
        if pending and len(pending) + len(group_ids) > limit:
            batches.append(pending)
            pending = []
        if len(group_ids) > limit:
            for start in range(0, len(group_ids), limit):
                batches.append(group_ids[start:start + limit])
        else:
            pending.extend(group_ids)
    if pending:
        batches.append(pending)
    return batches


def name_batches(batches: list) -> list:
    """`[(name, ids), ...]`, names derived from each batch's id prefix and
    de-duplicated within this call, case-insensitively -- two prefixes that
    differ only in case (`Auth.x`, `auth.y`) name the same directory on a
    case-insensitive filesystem (macOS), and the second batch would
    otherwise silently overwrite the first (security-review observation)."""
    used: set = set()
    named = []
    for ids in batches:
        base = _sanitize_batch_name(batch_prefix(ids[0]))
        name = base
        n = 2
        while name.lower() in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name.lower())
        named.append((name, ids))
    return named


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_translate(ledger_data: dict, locale: str) -> list:
    entries = ledger_mod.locale_entries(ledger_data, locale)
    return [i for i, e in entries.items() if e.get("state") in TRANSLATE_STATES and not ledger_mod.candidate_ready(e)]


def select_review(ledger_data: dict, locale: str) -> list:
    entries = ledger_mod.locale_entries(ledger_data, locale)
    return [i for i, e in entries.items() if ledger_mod.candidate_needs_review(e)]


def select_audit(ledger_data: dict, locale: str, messages=None, cfg=None, canon_lock=None, fresh=False,
                 check_inputs=None) -> list:
    entries = ledger_mod.locale_entries(ledger_data, locale)
    reviewed = set()
    if not fresh and messages is not None:
        coverage = ledger_mod.audit_coverage(ledger_data, messages, cfg, canon_lock, locale)
        reviewed.update(coverage["ids"]["pass"])
        reviewed.update(coverage["ids"]["fail"])
        if check_inputs is not None:
            reviewed = {msg_id for msg_id in reviewed
                        if entries[msg_id].get("audit", {}).get("check_inputs") == check_inputs
                        and entries[msg_id].get("audit", {}).get("current_checks", {}).get("status")
                        in ("pass", "observations")}
    return [i for i, e in entries.items() if e.get("state") in AUDIT_STATES and i not in reviewed]


# ---------------------------------------------------------------------------
# Canon lock filtering (translate / review / audit packets)
# ---------------------------------------------------------------------------


def filter_canon_lock(canon_lock: dict, messages: list, locale: str | None = None) -> dict:
    """The canon snapshot a batch packet embeds: the union of
    `ledger.relevant_canon` for every message in the batch (a single
    enforcement relevance rule, owned by `ledger.py`). Corrected-note
    context travels separately for explicit occurrences and approved locale groups and
    participates in `ledger.canon_sha256`. Preserve lock
    order so each message's canon hash also matches sync and export."""
    seen_ids: set = set()
    note_context = {}
    for message in messages:
        for entry in ledger_mod.relevant_canon(message, canon_lock):
            seen_ids.add(entry.get("id"))
        notes = ledger_mod.canon_note_context(message, canon_lock, locale)
        if notes:
            context = ledger_mod.informational_canon_context(message, canon_lock, locale) if locale else {}
            note_context[message["id"]] = {"entries": notes, "groups": context.get("groups", [])}
    result = {"entries": [entry for entry in canon_lock.get("entries", []) if entry.get("id") in seen_ids]}
    if note_context:
        result["note_context"] = note_context
    return result


def audit_canon_context(canon_lock: dict, messages: list, locale: str) -> dict:
    """Per-message informational links, separate from mechanical enforcement.

    Corrected-note dependencies are bound separately through `canon.note_context`.

    Seed with literal relevance and explicit approved occurrences; expand
    only through directly linked, approved groups for this locale. Never
    infer aliases, follow transitive groups, or read the unfrozen canon.
    """
    result = {}
    for message in messages:
        context = ledger_mod.informational_canon_context(message, canon_lock, locale)
        if context:
            result[message["id"]] = context
    return result


# ---------------------------------------------------------------------------
# messages.json helpers
# ---------------------------------------------------------------------------


def message_by_id(messages: dict) -> dict:
    return {m["id"]: m for m in messages["messages"]}


def plural_item_fields(message: dict, locale: str):
    plural = message.get("plural")
    if plural is None:
        return None, None, None, None
    target_labels = plural["target_labels"].get(locale)
    source_labels = plural.get("source_labels")
    return target_labels, source_labels, plural.get("count_arguments", []), plural.get("general_index")


def item_to_message(item: dict, locale: str) -> dict:
    """Reconstruct the minimal `message` shape `checks.check_candidate` and
    `ledger.*_sha256` need, from a packet item (accept time never re-reads
    `messages.json`; the packet is the frozen snapshot). Every field
    `ledger.context_sha256` reads for a plural message -- `source_labels`
    included -- must round-trip through the packet, or the hash this
    reconstruction feeds into differs from the one `sync()` computes from
    the live message, and a candidate for a plural message is refused as
    stale forever."""
    target_labels = item.get("target_labels")
    if target_labels is not None:
        plural = {
            "target_labels": {locale: target_labels},
            "source_labels": item.get("source_labels"),
            "count_arguments": item.get("count_arguments", []),
            "general_index": item.get("general_index", 0),
        }
    else:
        plural = None
    return {
        "id": item["id"],
        "source": item["source"],
        "plural": plural,
        "context": item.get("context", {}),
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _previous_fix_round(entry: dict):
    """`(previous_value, problems)` for a translate fix round, or
    `(None, None)` when this id has nothing to fix yet."""
    cand = entry.get("candidate")
    if not cand:
        return None, None
    problems = list(cand.get("problems") or [])
    verdict = cand.get("verdict")
    if verdict and verdict.get("verdict") == "fail":
        for issue in verdict.get("issues") or []:
            problems.append({"check": f"review:{issue.get('kind', '')}", "detail": issue.get("text", "")})
    if not problems:
        return None, None
    return cand.get("value"), problems


def build_translate_item(message: dict, locale: str, entry: dict) -> dict:
    item = {
        "id": message["id"],
        "source": message["source"],
        "context": message.get("context", {}),
    }
    target_labels, source_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
        item["source_labels"] = source_labels
        item["count_arguments"] = count_arguments
        item["general_index"] = general_index
    previous, problems = _previous_fix_round(entry)
    if previous is not None:
        item["previous"] = previous
        item["problems"] = problems
    return item


def build_review_item(message: dict, locale: str, entry: dict) -> dict:
    """The candidate's `value_sha256`/`source_sha256`/`context_sha256`/
    `style_sha256`/`canon_sha256` snapshots ride along as-built: `accept`'s
    review path re-checks them against the *current* candidate before
    binding a verdict, so a verdict cast on this snapshot never attaches to
    a candidate quietly rebuilt under a changed context or style, even when
    the rebuilt value happens to hash the same."""
    cand = entry["candidate"]
    item = {
        "id": message["id"],
        "source": message["source"],
        "value": cand["value"],
        "value_sha256": cand["value_sha256"],
        "source_sha256": cand["source_sha256"],
        "context_sha256": cand["context_sha256"],
        "style_sha256": cand["style_sha256"],
        "canon_sha256": cand["canon_sha256"],
        "context": message.get("context", {}),
    }
    target_labels, source_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
        item["source_labels"] = source_labels
        item["count_arguments"] = count_arguments
        item["general_index"] = general_index
    return item


def build_audit_item(message: dict, locale: str):
    """`None` when the message has no current value for this locale despite
    its ledger state (a data inconsistency the build reports and skips
    rather than guessing at)."""
    value = message.get("targets", {}).get(locale)
    if value is None:
        return None
    item = {
        "id": message["id"],
        "source": message["source"],
        "value": value,
        "value_sha256": lz_common.value_sha256(value),
        "context": message.get("context", {}),
    }
    target_labels, source_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
        item["source_labels"] = source_labels
        item["count_arguments"] = count_arguments
        item["general_index"] = general_index
    return item


def _audit_item_snapshot(packet: dict, item: dict) -> dict:
    locale = packet["locale"]
    message = {**item_to_message(item, locale), "targets": {locale: item["value"]}}
    cfg = {"style": {locale: packet.get("style")}}
    return ledger_mod.audit_snapshot(
        message, locale, cfg, packet.get("canon", {"entries": []}),
        canon_context=packet.get("canon_context", {}).get(item["id"], {}),
    )


def build_canon_item(message: dict, target_locales: list) -> dict:
    targets = {}
    for locale in target_locales:
        value = message.get("targets", {}).get(locale)
        if value is not None:
            targets[locale] = value
    item = {"id": message["id"], "source": message["source"], "context": message.get("context", {})}
    if targets:
        item["targets"] = targets
    return item


def do_build(root: Path, kind: str, locale, templates_dir: Path, entry_ids=None, fresh=False) -> dict:
    if kind not in KINDS + RESEARCH_KINDS:
        lz_common.fail(f"unknown packet kind: {kind}", lz_common.EXIT_CANNOT)
    if fresh and (kind != "audit" or entry_ids):
        lz_common.fail("--fresh is only valid with an unrestricted --kind audit", lz_common.EXIT_CANNOT)
    if kind == "audit" and entry_ids and len(entry_ids) > 1:
        lz_common.fail("--kind audit takes exactly one --entry", lz_common.EXIT_CANNOT)
    cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, cfg)

    if kind != "canon" and not locale:
        lz_common.fail(f"kind {kind!r} requires --locale", lz_common.EXIT_CANNOT)
    if locale and locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
    if entry_ids and kind != "audit" and kind not in RESEARCH_KINDS:
        lz_common.fail("--entry is only valid with --kind audit, research or research_verify", lz_common.EXIT_CANNOT)

    if kind in RESEARCH_KINDS:
        return _build_research(root, cfg, kind, locale, entry_ids, templates_dir)

    messages = lz_common.load_messages(root / "messages.json")
    by_id = message_by_id(messages)
    ledger_data = ledger_mod.load(root)
    canon_lock = canon_mod.load_lock(root)
    batch_size = cfg["batch_size"]

    if entry_ids:
        return _build_restricted_canon_audit(root, cfg, locale, entry_ids[0], by_id, canon_lock, templates_dir)

    skipped = []

    if kind == "canon":
        run_locale_dir = root / "runs" / CANON_PSEUDO_LOCALE
        selected_ids = [m["id"] for m in messages["messages"]]
        named = name_batches(make_batches(selected_ids, batch_size))
        batches_out = []
        for name, ids in named:
            items = [build_canon_item(by_id[i], cfg["target_locales"]) for i in ids]
            packet = {
                "kind": "canon",
                "locales": cfg["target_locales"],
                "batch": name,
                "items": items,
            }
            batches_out.append(_write_batch(run_locale_dir, name, kind, packet, templates_dir))
        return {"ok": True, "kind": kind, "locale": None, "selected": len(selected_ids),
                "batches": batches_out, "skipped": skipped}

    run_locale_dir = root / "runs" / locale
    if kind == "translate":
        selected_ids = select_translate(ledger_data, locale)
    elif kind == "review":
        selected_ids = select_review(ledger_data, locale)
    else:
        selected_ids = select_audit(ledger_data, locale, messages, cfg, canon_lock, fresh=fresh,
                                   check_inputs=ledger_mod.audit_check_inputs(root, cfg))

    entries = ledger_mod.locale_entries(ledger_data, locale)
    style = cfg["style"][locale]
    named = name_batches(make_batches(selected_ids, batch_size))
    batches_out = []
    for name, ids in named:
        items = []
        item_messages = []
        for i in ids:
            message = by_id.get(i)
            if message is None:
                skipped.append({"id": i, "reason": "id not present in messages.json"})
                continue
            entry = entries.get(i, {})
            if kind == "translate":
                item = build_translate_item(message, locale, entry)
            elif kind == "review":
                item = build_review_item(message, locale, entry)
            else:
                item = build_audit_item(message, locale)
                if item is None:
                    skipped.append({"id": i, "reason": "no current target value for this locale"})
                    continue
            items.append(item)
            item_messages.append(message)
        if not items:
            continue
        packet = {
            "kind": kind,
            "locale": locale,
            "batch": name,
            "style": style,
            "canon": filter_canon_lock(canon_lock, item_messages, locale),
            "items": items,
        }
        if kind == "audit":
            packet["canon_entry"] = None
            packet["canon_context"] = audit_canon_context(canon_lock, item_messages, locale)
            _check_audit_packet(root, cfg, packet)
        batches_out.append(_write_batch(run_locale_dir, name, kind, packet, templates_dir))

    return {"ok": True, "kind": kind, "locale": locale, "selected": len(selected_ids),
            "batches": batches_out, "skipped": skipped}


# ---------------------------------------------------------------------------
# Build: usage research
# ---------------------------------------------------------------------------


def _research_examples(entry: dict, by_id: dict, locale: str) -> list:
    """The first `RESEARCH_EXAMPLES` occurrences of a canon entry that are
    still in `messages.json`, in the entry's `occurrences` order, each with
    the locale's current target when the message has one. Both research
    turns get the same list, so the verifier judges the product's sense of
    the term from the product's own messages, not from the researcher's
    account of them."""
    examples = []
    for occurrence in entry.get("occurrences") or []:
        message = by_id.get(occurrence)
        if message is None:
            continue
        example = {"id": occurrence, "source": message["source"]}
        value = message.get("targets", {}).get(locale)
        if value is not None:
            example["value"] = value
        examples.append(example)
        if len(examples) == RESEARCH_EXAMPLES:
            break
    return examples


def _select_research(by_entry: dict, research: dict, locale: str, entry_ids) -> list:
    """Canon entry ids for a `research` build. Default: every `term` or
    `ui_label` whose translation for the locale is not yet approved and
    that has no research record, plus resource-limited records (even if
    approved). Explicit `--entry` ids are taken as given (re-research of an
    approved or already-researched entry included), but each must be a
    researchable canon entry."""
    if entry_ids:
        for entry_id in entry_ids:
            entry = by_entry.get(entry_id)
            if entry is None or entry["kind"] not in RESEARCH_TERM_KINDS:
                lz_common.fail(f"not a term or ui_label canon entry: {entry_id}",
                                lz_common.EXIT_CANNOT, entry=entry_id)
        return list(dict.fromkeys(entry_ids))
    selected = []
    for entry in by_entry.values():
        if entry["kind"] not in RESEARCH_TERM_KINDS:
            continue
        record = research["entries"].get(entry["id"])
        if record and record.get("stale_note"):
            continue  # A corrected meaning needs separately scoped fresh research.
        if record and canon_mod.resource_limited(record.get("research")):
            selected.append(entry["id"])
            continue
        translation = entry["translations"].get(locale)
        if translation is not None and translation.get("status") == "approved":
            continue
        if entry["id"] in research["entries"]:
            continue
        selected.append(entry["id"])
    return selected


def _select_research_verify(research: dict, entry_ids) -> list:
    records = research["entries"]
    if entry_ids:
        for entry_id in entry_ids:
            if entry_id not in records:
                lz_common.fail(f"no research record for entry: {entry_id}", lz_common.EXIT_CANNOT, entry=entry_id)
            if records[entry_id].get("stale_note"):
                lz_common.fail(f"research is stale after a note correction; build research --entry {entry_id}",
                                lz_common.EXIT_FAIL, entry=entry_id)
            if canon_mod.resource_limited(records[entry_id].get("research")):
                lz_common.fail(f"finish resource-limited research before verification: {entry_id}",
                                lz_common.EXIT_CANNOT, entry=entry_id)
        return list(dict.fromkeys(entry_ids))
    return [i for i, record in records.items()
            if not record.get("stale_note") and not canon_mod.resource_limited(record.get("research"))
            and (record.get("verification") is None or canon_mod.resource_limited(record["verification"]))]


def build_research_item(entry: dict, locale: str, by_id: dict) -> dict:
    translation = entry["translations"].get(locale) or {}
    item = {
        "id": entry["id"], "kind": entry["kind"], "source": entry["source"],
        "note": entry.get("note") or "",
        "proposed": translation.get("value"), "status": translation.get("status"),
        "current": list(translation.get("current") or []),
        "examples": _research_examples(entry, by_id, locale),
    }
    if entry.get("note_revision"):
        item["note_revision"] = entry["note_revision"]
    return item


def build_research_verify_item(entry_id: str, record: dict, entry: dict, locale: str, by_id: dict) -> dict:
    """A record whose canon entry has since gone (`entry` is `{}`) keeps its
    own kind/source; it just has no note or examples left to show."""
    item = {
        "id": entry_id, "kind": record["kind"], "source": record["source"],
        "note": entry.get("note") or "",
        "examples": _research_examples(entry, by_id, locale),
        "research": record["research"], "research_sha256": record["research_sha256"],
    }
    if entry.get("note_revision"):
        item["note_revision"] = entry["note_revision"]
    return item


def _build_research(root: Path, cfg: dict, kind: str, locale: str, entry_ids, templates_dir: Path) -> dict:
    """`build --kind research|research_verify`: packets over canon entries,
    not messages -- see the module docstring. The ledger and the lock are
    never read."""
    messages = lz_common.load_messages(root / "messages.json")
    by_id = message_by_id(messages)
    by_entry = {e["id"]: e for e in canon_mod.load(root)["entries"]}
    research = canon_mod.load_research(root, locale)
    settings = cfg.get("research") or {}
    field = settings.get("field")
    sites = list((settings.get("sites") or {}).get(locale) or [])
    style = cfg["style"][locale]

    if kind == "research":
        selected_ids = _select_research(by_entry, research, locale, entry_ids)
    else:
        selected_ids = _select_research_verify(research, entry_ids)

    # Check every declared reference, independently of the bounded example
    # sample. These warnings describe stored source context, not audit coverage.
    diagnostics = []
    for entry_id in selected_ids:
        occurrences = (by_entry.get(entry_id) or {}).get("occurrences") or []
        unresolved = [i for i in occurrences if i not in by_id]
        if unresolved:
            diagnostics.append({
                "code": "unresolved_canon_occurrences", "entry_id": entry_id,
                "unresolved_ids": unresolved, "declared_count": len(occurrences),
                "resolvable_count": len(occurrences) - len(unresolved),
            })

    batches_out = []
    for name, ids in name_batches(make_batches(selected_ids, RESEARCH_BATCH_SIZE)):
        if kind == "research":
            items = [build_research_item(by_entry[i], locale, by_id) for i in ids]
        else:
            items = [build_research_verify_item(i, research["entries"][i], by_entry.get(i) or {}, locale, by_id)
                     for i in ids]
        for item in items:
            record = research["entries"].get(item["id"])
            item["checkpoint_sha256"] = lz_common.sha256_json(record)
            phase = "research" if kind == "research" else "verification"
            if record and not record.get("stale_note") and canon_mod.resource_limited(record.get(phase)):
                item["resume"] = {"previous": record[phase], "packet": record[
                    "researched" if kind == "research" else "verification"].get("packet")}
        packet = {"kind": kind, "locale": locale, "batch": name, "field": field, "style": style, "items": items}
        if kind == "research":
            packet["sites"] = sites
        batches_out.append(_write_batch(root / "runs" / locale, name, kind, packet, templates_dir))

    return {"ok": True, "kind": kind, "locale": locale, "selected": len(selected_ids),
            "batches": batches_out, "skipped": [], "diagnostics": diagnostics}


def _build_restricted_canon_audit(root: Path, cfg: dict, locale: str, entry_id: str, by_id: dict,
                                   canon_lock: dict, templates_dir: Path) -> dict:
    """`build --kind audit --entry ID`: the restricted canon audit
    `canon.py change` requests — an audit packet scoped to one canon entry's
    occurrences, with `canon_entry` set so the reviewer judges only that
    entry's rendering."""
    request_path = root / "runs" / locale / f"canon-audit-{entry_id}.json"
    if not request_path.is_file():
        lz_common.fail(f"no restricted canon-audit request for entry: {entry_id}",
                        lz_common.EXIT_CANNOT, entry=entry_id)
    request = lz_common.read_json(request_path, "restricted canon-audit request")
    occurrences = request.get("occurrences", [])
    style = cfg["style"][locale]
    run_locale_dir = root / "runs" / locale
    batch_size = cfg["batch_size"]

    chunks = make_batches(occurrences, batch_size)
    canon_entry_meta = {
        "entry": entry_id, "source": request.get("source"),
        "before": request.get("before"), "after": request.get("after"),
        "reason": request.get("reason"),
    }
    if "scope_change" in request:
        canon_entry_meta["scope_change"] = request["scope_change"]
    if "scope_decision" in request:
        canon_entry_meta["scope_decision"] = request["scope_decision"]
    if request.get("field") == "note":
        canon_entry_meta.update(field="note", impact="semantic", note_revision=request["note_revision"])
        if request.get("translation_change"):
            canon_entry_meta["translation_change"] = request["translation_change"]
    else:
        target = next((e for e in canon_lock.get("entries", []) if e["id"] == entry_id), {})
        if target.get("note_revision"):
            canon_entry_meta["note_revision"] = target["note_revision"]
    skipped, batches_out = [], []
    for n, ids in enumerate(chunks):
        name = f"canon-audit-{entry_id}" if n == 0 else f"canon-audit-{entry_id}-{n + 1}"
        items, item_messages = [], []
        for i in ids:
            message = by_id.get(i)
            if message is None:
                skipped.append({"id": i, "reason": "id not present in messages.json"})
                continue
            item = build_audit_item(message, locale)
            if item is None:
                skipped.append({"id": i, "reason": "no current target value for this locale"})
                continue
            items.append(item)
            item_messages.append(message)
        if not items:
            continue
        packet = {
            "kind": "audit", "locale": locale, "batch": name, "style": style,
            "canon": filter_canon_lock(canon_lock, item_messages, locale),
            "canon_entry": canon_entry_meta,
            "canon_context": audit_canon_context(canon_lock, item_messages, locale),
            "items": items,
        }
        _check_audit_packet(root, cfg, packet)
        batches_out.append(_write_batch(run_locale_dir, name, "audit", packet, templates_dir))

    return {"ok": True, "kind": "audit", "locale": locale, "selected": len(occurrences),
            "batches": batches_out, "skipped": skipped, "canon_entry": entry_id}


def _write_batch(run_locale_dir: Path, name: str, kind: str, packet: dict, templates_dir: Path) -> dict:
    # Legacy batch names strip leading underscores, so this namespace cannot be an old batch.
    batch_dir = run_locale_dir / "_packets" / kind / name
    if kind in RESEARCH_KINDS:
        base, n = name, 2
        while batch_dir.exists():
            name = f"{base}-{n}"
            batch_dir = run_locale_dir / "_packets" / kind / name
            n += 1
        packet = {**packet, "batch": name}
    lock = packet.get("canon", {})
    entries = (packet.get("items", []) if kind in RESEARCH_KINDS else
               lock.get("entries", []) + [entry for context in lock.get("note_context", {}).values()
                                          for entry in context["entries"]])
    revisions = {e["id"]: e["note_revision"] for e in entries if e.get("note_revision")}
    restricted = packet.get("canon_entry") or {}
    if restricted.get("note_revision"):
        revisions[restricted["entry"]] = restricted["note_revision"]
    if revisions:
        packet["note_revisions"] = revisions
    try:
        prefix, suffix = prompt_instructions(templates_dir, kind)
    except LookupError as exc:
        lz_common.fail(str(exc), lz_common.EXIT_CANNOT)
    if revisions:
        suffix += ("\nCopy the packet's `note_revisions` object unchanged into a top-level "
                   "`note_revisions` field in your JSON answer. It binds this answer to the "
                   "corrected shared meanings, even if an older answer has identical wording.\n")
    if lock.get("note_context"):
        suffix += ("\n`canon.note_context` supplies corrected meanings per message through explicit "
                   "occurrences or approved groups. Read the supplied group notes and scopes. "
                   "It is informational: do not expand literal term enforcement or require "
                   "copying a full term into an item that does not use it.\n")
    prompt = prefix + json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=True) + suffix
    lz_common.atomic_write_json(batch_dir / "packet.json", packet)
    lz_common.atomic_write_text(batch_dir / "prompt.md", prompt)
    lz_common.atomic_write_json(batch_dir / "instructions.json", {"prefix": prefix, "suffix": suffix})
    if kind in packet_handoff.GUARDED_KINDS:
        request = packet_handoff.agent_request(kind, name, prompt)
        # The parent can submit these complete Agent arguments as ASCII JSON;
        # the child never needs Read/Bash to recover the packet.
        lz_common.atomic_write_text(batch_dir / "request.json", json.dumps(request, indent=2, ensure_ascii=True) + "\n")
    return {"batch": name, "dir": str(batch_dir), "items": len(packet["items"])}


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


def _parse_batch(root: Path, cfg: dict, items: list, extra_parse_items: list) -> dict:
    """One batched `adapter_client.parse` call: every source form of every
    item, plus whatever pre-built parse items (`{"key", "id", "text"}`) the
    caller needs parsed (a candidate value's forms, or a proposed replacement's
    forms)."""
    to_parse = []
    for item in items:
        to_parse.extend(_parse_items(item["id"], "source", checks_mod.forms_of(item["source"])))
    to_parse.extend(extra_parse_items)
    if not to_parse:
        return {}
    project_dir = str(Path(cfg["project_root"]))
    return adapter_client.parse(str(root), cfg, project_dir, to_parse)


def _parse_list(results: dict, item_id: str, prefix: str, forms: list) -> list:
    return [results[f"{item_id}::{prefix}::{idx}"] for idx in range(len(forms))]


def _parse_items(item_id: str, prefix: str, forms: list) -> list:
    """The parse items for one message's forms, keyed the way `_parse_list` reads them back."""
    return [{"key": f"{item_id}::{prefix}::{idx}", "id": item_id, "text": form} for idx, form in enumerate(forms)]


def _single_or_list(parses: list, is_plural: bool):
    """A non-plural message always has exactly one form -- unwrap it to a
    scalar. A plural message keeps its parse results list-shaped for EVERY
    form count, including exactly one target label: `checks.check_candidate`
    always iterates a plural's `source_parse`/`value_parse` as a list
    (`list(value_parse)`), and collapsing a one-element list here used to
    hand it a single parse-result dict instead -- `list()` of a dict yields
    its keys, not its forms, and the first `.get()` call on one of those
    keys (a string) raised `AttributeError`."""
    return parses if is_plural else parses[0]


def _audit_value_checks(item, locale, value, prefix, results, canon_lock, cfg, adapter_error=None):
    """Use real parser results; unavailable syntax coverage is never a pass.

    Source parse failures cannot establish argument/structure parity. Keep
    the independent checks, but omit comparisons against an unparsed source.
    """
    source_forms = checks_mod.forms_of(item["source"])
    value_forms = checks_mod.forms_of(value)
    unavailable = []
    if adapter_error is not None:
        problems = checks_mod.check_candidate(
            item_to_message(item, locale), locale, value, None, None, canon_lock, cfg,
        )
        return {"status": "unavailable", "problems": problems, "unavailable": [adapter_error]}
    source_parses = _parse_list(results, item["id"], "source", source_forms)
    value_parses = _parse_list(results, item["id"], prefix, value_forms)
    for role, parses in (("source", source_parses), (prefix, value_parses)):
        unavailable.extend(f"{role} form {i}: {parsed['error']}"
                           for i, parsed in enumerate(parses) if not parsed["ok"])
    is_plural = item.get("target_labels") is not None
    problems = checks_mod.check_candidate(
        item_to_message(item, locale), locale, value,
        _single_or_list(source_parses, is_plural), _single_or_list(value_parses, is_plural),
        canon_lock, cfg,
    )
    if any(not parsed["ok"] for parsed in source_parses):
        problems = [problem for problem in problems if problem["check"] not in ("arguments", "structure")]
    return {
        "status": "unavailable" if unavailable else ("observations" if problems else "pass"),
        "problems": problems,
        "unavailable": unavailable,
    }


def _check_audit_packet(root, cfg, packet):
    """Expose current-value observations before the model makes any verdict."""
    packet["check_inputs"] = ledger_mod.audit_check_inputs(root, cfg)
    extra = [parse_item for item in packet["items"]
             for parse_item in _parse_items(item["id"], "current", checks_mod.forms_of(item["value"]))]
    error = None
    try:
        results = _parse_batch(root, cfg, packet["items"], extra)
    except adapter_client.AdapterError as exc:
        results, error = {}, str(exc)
    for item in packet["items"]:
        item["current_checks"] = _audit_value_checks(
            item, packet["locale"], item["value"], "current", results, packet["canon"], cfg, error,
        )


_CANDIDATE_SNAPSHOT_FIELDS = ("value_sha256", "source_sha256", "context_sha256", "style_sha256", "canon_sha256")


def _review_candidate_still_matches(ledger_data: dict, locale: str, msg_id: str, item: dict) -> bool:
    """A review verdict binds to the exact candidate the packet was built
    from: the id's *current* candidate must still carry the same
    value_sha256, source_sha256, context_sha256, style_sha256 and
    canon_sha256 the packet item recorded at build time. If the candidate
    was rebuilt since (even to the same value, under a changed context or
    style), this is False and the verdict must not attach."""
    entry = ledger_mod.locale_entries(ledger_data, locale).get(msg_id)
    candidate = entry.get("candidate") if entry else None
    if candidate is None:
        return False
    return all(candidate.get(field) == item.get(field) for field in _CANDIDATE_SNAPSHOT_FIELDS)


def _valid_review_canon_candidate(cand) -> bool:
    """One raw entry from a review/audit verdict's `new_canon_candidates`:
    an object with `kind` in `term|ui_label|dnt`, a non-empty string
    `source`, and an optional string `proposed`/`note` (the shape
    `review_TASK.md`/`audit_TASK.md` document -- not yet the canon import
    shape, which needs the message id and locale this module supplies)."""
    if not isinstance(cand, dict):
        return False
    if cand.get("kind") not in CANDIDATE_KINDS:
        return False
    if not isinstance(cand.get("source"), str) or not cand["source"]:
        return False
    for field in ("proposed", "note"):
        value = cand.get(field)
        if value is not None and not isinstance(value, str):
            return False
    return True


def _canon_import_shape(cand: dict, msg_id: str, locale: str) -> dict:
    """A validated raw candidate, wrapped in the shape `canon.py import
    --file` reads (documented in `canon.py`'s own module docstring):
    `occurrences` is this message id, `translations` carries the proposed
    value under this locale."""
    return {
        "kind": cand["kind"],
        "source": cand["source"],
        "note": cand.get("note") or "",
        "occurrences": [msg_id],
        "translations": {locale: {"proposed": cand.get("proposed"), "current": []}},
    }


def _maybe_escalate(ledger_data: dict, cfg: dict, locale: str, msg_id: str, problems: list) -> bool:
    entry = ledger_mod.locale_entries(ledger_data, locale).get(msg_id, {})
    if entry.get("rounds", 0) >= cfg["max_rounds"]:
        ledger_mod.mark_escalated(ledger_data, locale, msg_id, problems)
        return True
    return False


def accept_translate(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict, batch_name: str) -> dict:
    locale = packet["locale"]
    items = {it["id"]: it for it in packet["items"]}
    translations = output.get("translations")
    if not isinstance(translations, dict):
        lz_common.fail("translate output is missing 'translations'", lz_common.EXIT_FAIL)

    missing = [i for i in items if i not in translations]
    extra = sorted(set(translations) - set(items))
    canon_lock = packet.get("canon", {"entries": []})

    # A malformed value (not a string, not `{"forms": [...]}` of strings --
    # an int, None, a list, or a forms dict with non-string forms) must
    # never reach the adapter's parse: only a shape-valid value's forms go
    # into `extra_parse_items` below (security-review observation). One batched
    # `_parse_batch` call covers the whole batch's source forms plus every
    # shape-valid value's forms, matching `_accept_review_or_audit`.
    value_forms_by_id: dict = {}
    extra_parse_items: list = []
    for msg_id, item in items.items():
        if msg_id not in translations:
            continue
        value = translations[msg_id]
        if isinstance(value, str) or lz_common.is_forms_value(value):
            value_forms = checks_mod.forms_of(value)
            value_forms_by_id[msg_id] = value_forms
            extra_parse_items.extend(_parse_items(msg_id, "value", value_forms))
    parse_results = _parse_batch(root, cfg, list(items.values()), extra_parse_items)

    accepted, failed, escalated = [], [], []
    for msg_id, item in items.items():
        if msg_id not in translations:
            continue
        value = translations[msg_id]
        message = item_to_message(item, locale)
        is_plural = item.get("target_labels") is not None

        value_forms = value_forms_by_id.get(msg_id)
        if value_forms is None:
            problems = [{
                "check": "shape",
                "detail": f"value is not a string or {{'forms': [...]}} of strings: {value!r}",
            }]
        else:
            source_forms = checks_mod.forms_of(item["source"])
            source_parse = _single_or_list(_parse_list(parse_results, msg_id, "source", source_forms), is_plural)
            value_parse = _single_or_list(_parse_list(parse_results, msg_id, "value", value_forms), is_plural)
            problems = checks_mod.check_candidate(message, locale, value, source_parse, value_parse, canon_lock, cfg)

        # `lz_common.value_sha256` raises on a shape-invalid value (an int,
        # None, a list); `sha256_json` computes the identical hash for a
        # well-shaped value (it is what `value_sha256` itself delegates to)
        # while never raising, so a malformed translation still gets a
        # recorded (if unexportable) candidate instead of aborting accept.
        candidate = {
            "value": value,
            "value_sha256": lz_common.sha256_json(value),
            "origin": "translate",
            "source_sha256": ledger_mod.source_sha256(message),
            "context_sha256": ledger_mod.context_sha256(message, locale),
            "style_sha256": ledger_mod.style_sha256(cfg, locale),
            "canon_sha256": ledger_mod.canon_sha256(message, canon_lock, locale),
            "audited_target_sha256": None,
            "checks": "fail" if problems else "pass",
            "problems": problems,
            "verdict": None,
            "accepted_by": None,
        }
        ledger_mod.set_candidate(ledger_data, locale, msg_id, candidate)
        entry = ledger_mod.locale_entries(ledger_data, locale)[msg_id]
        entry["rounds"] = entry.get("rounds", 0) + 1

        if problems:
            if _maybe_escalate(ledger_data, cfg, locale, msg_id, problems):
                escalated.append(msg_id)
            failed.append({"id": msg_id, "reason": "checks failed", "problems": problems})
        else:
            accepted.append(msg_id)

    return {
        "ok": True, "kind": "translate", "locale": locale, "run": batch_name,
        "accepted": accepted, "failed": failed + [{"id": i, "reason": "missing from output"} for i in missing],
        "extra": extra, "escalated": escalated,
    }


def _valid_issue(issue) -> bool:
    return isinstance(issue, dict) and isinstance(issue.get("kind"), str) and isinstance(issue.get("text"), str)


def _valid_proposed_shape(proposed, is_plural: bool) -> bool:
    """`proposed` is `null`, or a value of the right shape for this
    message: a plain string for a non-plural message, `{"forms": [...]}`
    (every form a string) for a plural one -- the same shape
    `checks.forms_of` expects, checked up front so a malformed `proposed`
    never reaches `checks_mod.forms_of` unguarded."""
    if proposed is None:
        return True
    if is_plural:
        return lz_common.is_forms_value(proposed)
    return isinstance(proposed, str)


def _valid_verdict_shape(verdict: dict, is_plural: bool) -> bool:
    """A review/audit turn's verdict for one item, validated before it can
    cause any ledger state change: `verdict` must be exactly "pass" or
    "fail" (anything else is treated the same as a missing verdict -- no
    rounds increment, no escalation), `issues` a list of
    `{"kind": str, "text": str}`, `proposed` null or shaped for this
    message's plurality."""
    if verdict.get("verdict") not in ("pass", "fail"):
        return False
    issues = verdict.get("issues", [])
    if not isinstance(issues, list) or not all(_valid_issue(i) for i in issues):
        return False
    return _valid_proposed_shape(verdict.get("proposed"), is_plural)


def accept_review(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict,
                  batch_name: str, run_dir: Path) -> dict:
    return _accept_review_or_audit(root, cfg, packet, output, ledger_data, batch_name, run_dir, kind="review")


def accept_audit(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict,
                 batch_name: str, run_dir: Path) -> dict:
    return _accept_review_or_audit(root, cfg, packet, output, ledger_data, batch_name, run_dir, kind="audit")


def _accept_review_or_audit(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict,
                             batch_name: str, run_dir: Path, kind: str) -> dict:
    locale = packet["locale"]
    items = {it["id"]: it for it in packet["items"]}
    verdicts = output.get("verdicts")
    if not isinstance(verdicts, dict):
        lz_common.fail(f"{kind} output is missing 'verdicts'", lz_common.EXIT_FAIL)

    canon_lock = packet.get("canon", {"entries": []})
    audit_scope = (packet.get("canon_entry") or {}).get("entry", "full")
    # The admission gate below rejects changed inputs. Keep the packet's
    # frozen style for checking a proposal after its snapshot matches.
    audit_cfg = {**cfg, "style": {locale: packet.get("style")}}
    audit_at = lz_common.now_iso()
    packet_sha = lz_common.sha256_json(packet)
    missing, extra_ids = [], sorted(set(verdicts) - set(items))
    if kind == "audit":
        current_by_id = message_by_id(lz_common.load_messages(root / "messages.json"))
        current_canon_lock = canon_mod.load_lock(root)
        check_inputs = ledger_mod.audit_check_inputs(root, cfg)

    # Gather every proposed value's forms up front for one batched parse call.
    valid_verdicts = {}
    for msg_id, item in items.items():
        verdict = verdicts.get(msg_id)
        if not isinstance(verdict, dict) or verdict.get("value_sha256") != item["value_sha256"]:
            missing.append(msg_id)
            continue
        is_plural = item.get("target_labels") is not None
        if not _valid_verdict_shape(verdict, is_plural):
            # A malformed verdict (an unrecognized `verdict` string, a
            # non-{kind,text} issue, or a `proposed` of the wrong shape)
            # must cause no ledger state change at all -- treated the same
            # as a hash mismatch: no rounds increment, no escalation.
            missing.append(msg_id)
            continue
        if kind == "audit":
            current_message = current_by_id.get(msg_id)
            if (current_message is None or _audit_item_snapshot(packet, item)
                    != ledger_mod.audit_snapshot(current_message, locale, cfg, current_canon_lock)
                    or ("check_inputs" in packet and packet["check_inputs"] != check_inputs)):
                # A retained run can still have a valid handoff receipt after
                # a different batch audited newer inputs. Do not let replay
                # replace that newer record or retire its correction.
                missing.append(msg_id)
                continue
        if kind == "review" and not _review_candidate_still_matches(ledger_data, locale, msg_id, item):
            # The candidate this packet item snapshot was built from has
            # since been rebuilt (context or style changed and it was
            # retranslated) -- even a value that happens to hash the same
            # must not have a verdict cast on the old snapshot attached to
            # it, so this verdict is treated the same as a hash mismatch.
            missing.append(msg_id)
            continue
        valid_verdicts[msg_id] = verdict

    extra_parse_items = []
    for msg_id, verdict in valid_verdicts.items():
        if kind == "audit":
            extra_parse_items.extend(_parse_items(msg_id, "current", checks_mod.forms_of(items[msg_id]["value"])))
        proposed = verdict.get("proposed")
        if (kind == "audit" or verdict.get("verdict") == "fail") and proposed is not None:
            extra_parse_items.extend(_parse_items(msg_id, "proposed", checks_mod.forms_of(proposed)))
    parse_items = [items[msg_id] for msg_id in valid_verdicts] if kind == "audit" else list(items.values())
    parse_error = None
    try:
        parse_results = _parse_batch(root, cfg, parse_items, extra_parse_items)
    except adapter_client.AdapterError as exc:
        if kind != "audit":
            raise
        parse_results, parse_error = {}, str(exc)

    passed, failed_ids, escalated, proposals_stored = [], [], [], []
    audit_proposal_conflicts = []
    new_canon_candidates = []
    invalid_canon_candidates = []

    for msg_id, verdict in valid_verdicts.items():
        item = items[msg_id]
        message = item_to_message(item, locale)
        run_verdict = {
            "value_sha256": verdict["value_sha256"],
            "verdict": verdict.get("verdict"),
            "issues": verdict.get("issues", []),
            "run": batch_name,
        }

        if kind == "audit":
            snapshot = _audit_item_snapshot(packet, item)
            record = {
                "snapshot": snapshot,
                "verdict": verdict["verdict"],
                "issues": verdict.get("issues", []),
                "proposed": verdict.get("proposed"),
                "accepted_at": audit_at,
                "packet_sha256": packet_sha,
                "run": batch_name,
                "run_dir": str(run_dir.resolve()),
                "scope": audit_scope,
                "check_inputs": check_inputs,
                "audited_value": item["value"],
                "current_checks": _audit_value_checks(
                    item, locale, item["value"], "current", parse_results, canon_lock, audit_cfg, parse_error,
                ),
            }
            if verdict.get("proposed") is not None:
                record["proposal_checks"] = _audit_value_checks(
                    item, locale, verdict["proposed"], "proposed", parse_results, canon_lock, audit_cfg, parse_error,
                )
            entry = ledger_mod.locale_entries(ledger_data, locale).setdefault(msg_id, {})
            if packet.get("canon_entry") is None:
                entry["audit"] = record
            else:
                entry.setdefault("canon_audits", {})[audit_scope] = record
            previous = entry.get("audit_proposal")
            if previous:
                if previous.get("value") is None or previous.get("checks") != "pass":
                    # Retain the exact evidence without occupying the slot a
                    # person can promote only after a replacement passes checks.
                    entry.setdefault("legacy_audit_findings", []).append(previous)
                    entry["audit_proposal"] = None
                elif previous.get("audit_scope") == audit_scope:
                    entry["audit_proposal"] = None

        raw_candidates = verdict.get("new_canon_candidates")
        if raw_candidates is None:
            raw_candidates = []
        elif not isinstance(raw_candidates, list):
            # The whole field is the wrong shape (e.g. a string) -- record
            # it as one invalid entry rather than iterating its characters:
            # iterating a string here would land single-character
            # "candidates" in the side file and break report.py's
            # `cand.get(...)` reads.
            invalid_canon_candidates.append({"id": msg_id, "candidate": raw_candidates})
            raw_candidates = []
        for cand in raw_candidates:
            if _valid_review_canon_candidate(cand):
                new_canon_candidates.append(_canon_import_shape(cand, msg_id, locale))
            else:
                invalid_canon_candidates.append({"id": msg_id, "candidate": cand})

        if verdict.get("verdict") == "pass":
            if kind == "review":
                ledger_mod.record_verdict(ledger_data, locale, msg_id, run_verdict)
            passed.append(msg_id)
            continue

        # fail
        if kind == "review":
            ledger_mod.record_verdict(ledger_data, locale, msg_id, run_verdict)
            entry = ledger_mod.locale_entries(ledger_data, locale)[msg_id]
            failed_ids.append(msg_id)
            # Escalation timing: test whether `rounds` has ALREADY reached
            # `max_rounds` before this failure, not after bumping it for
            # this round -- otherwise a failure at
            # `rounds == max_rounds - 1` escalates one round early and
            # discards a proposal that should still have been installed and
            # reviewed. Without a proposal, recheck the incremented count:
            # there is no replacement left to review at the cap.
            if _maybe_escalate(ledger_data, cfg, locale, msg_id, verdict.get("issues", [])):
                escalated.append(msg_id)
                entry["review_proposal"] = {"value": verdict.get("proposed"), "issues": verdict.get("issues", []),
                                             "run": batch_name} if verdict.get("proposed") is not None else None
                continue
            entry["rounds"] = entry.get("rounds", 0) + 1
            proposed = verdict.get("proposed")
            if proposed is None and _maybe_escalate(ledger_data, cfg, locale, msg_id, verdict.get("issues", [])):
                escalated.append(msg_id)
            if proposed is not None:
                is_plural = item.get("target_labels") is not None
                source_forms = checks_mod.forms_of(item["source"])
                proposed_forms = checks_mod.forms_of(proposed)
                source_parse = _single_or_list(_parse_list(parse_results, msg_id, "source", source_forms), is_plural)
                proposed_parse = _single_or_list(_parse_list(parse_results, msg_id, "proposed", proposed_forms), is_plural)
                problems = checks_mod.check_candidate(
                    message, locale, proposed, source_parse, proposed_parse, canon_lock, cfg,
                )
                old_candidate = entry.get("candidate") or {}
                if old_candidate.get("origin") == "audit":
                    # This review was judging an already-*accepted* audit
                    # candidate (`accept-audit`'s "still needs a review
                    # verdict on its hash before export"). A checked
                    # replacement belongs back in the audit-proposal
                    # approval path, not installed straight as a candidate:
                    # `ledger.exportable` only waives its state gate when
                    # `accepted_by` is set, and a fresh candidate here would
                    # carry `accepted_by: None` with no route back to
                    # `accept-audit` (which reads `audit_proposal`, already
                    # cleared when this candidate was accepted) -- it could
                    # never be exported.
                    entry["audit_proposal"] = {
                        "audit_scope": old_candidate.get("audit_scope"),
                        "value": proposed,
                        "value_sha256": lz_common.value_sha256(proposed),
                        "audited_target_sha256": old_candidate.get("audited_target_sha256"),
                        "source_sha256": ledger_mod.source_sha256(message),
                        "context_sha256": ledger_mod.context_sha256(message, locale),
                        "style_sha256": ledger_mod.style_sha256(cfg, locale),
                        "canon_sha256": ledger_mod.canon_sha256(message, canon_lock, locale),
                        "checks": "fail" if problems else "pass",
                        "problems": problems,
                        "issues": verdict.get("issues", []),
                        "run": batch_name,
                    }
                    proposals_stored.append(msg_id)
                else:
                    new_candidate = {
                        "value": proposed,
                        "value_sha256": lz_common.value_sha256(proposed),
                        "origin": old_candidate.get("origin", "translate"),
                        "source_sha256": ledger_mod.source_sha256(message),
                        "context_sha256": ledger_mod.context_sha256(message, locale),
                        "style_sha256": ledger_mod.style_sha256(cfg, locale),
                        "canon_sha256": ledger_mod.canon_sha256(message, canon_lock, locale),
                        "audited_target_sha256": old_candidate.get("audited_target_sha256"),
                        "checks": "fail" if problems else "pass",
                        "problems": problems,
                        "verdict": None,
                        "accepted_by": None,
                    }
                    ledger_mod.set_candidate(ledger_data, locale, msg_id, new_candidate)
        else:  # audit
            failed_ids.append(msg_id)
            proposed = verdict.get("proposed")
            problems = []
            if proposed is not None:
                problems = record["proposal_checks"]["problems"]
            entry = ledger_mod.locale_entries(ledger_data, locale).setdefault(msg_id, {})
            pending = entry.get("audit_proposal")
            pending_scope = pending.get("audit_scope") if pending else None
            # Only an approvable replacement can still occupy this slot after
            # retirement above; a different or unknown scope was not judged here.
            if pending and pending_scope != audit_scope:
                if proposed is not None:
                    audit_proposal_conflicts.append({
                        "id": msg_id, "scope": audit_scope, "pending_scope": pending_scope,
                    })
            else:
                entry["audit_proposal"] = {
                    "audit_scope": audit_scope,
                    "value": proposed,
                    "value_sha256": lz_common.value_sha256(proposed) if proposed is not None else None,
                    "audited_target_sha256": item["value_sha256"],
                    "source_sha256": snapshot["source_sha256"],
                    "context_sha256": snapshot["context_sha256"],
                    "style_sha256": snapshot["style_sha256"],
                    "canon_sha256": snapshot["canon_sha256"],
                    "checks": ("unavailable" if record["proposal_checks"]["status"] == "unavailable"
                               else ("fail" if problems else "pass")) if proposed is not None else None,
                    "problems": problems,
                    "issues": verdict.get("issues", []),
                    "run": batch_name,
                }
                if proposed is not None:
                    proposals_stored.append(msg_id)

    if new_canon_candidates:
        lz_common.atomic_write_json(
            run_dir / "new_canon_candidates.json",
            {"candidates": new_canon_candidates},
        )

    result = {
        "ok": True, "kind": kind, "locale": locale, "run": batch_name,
        "passed": passed, "failed": failed_ids, "missing": missing, "extra": extra_ids,
        "escalated": escalated, "audit_proposals": proposals_stored,
        "invalid_canon_candidates": invalid_canon_candidates,
    }
    if kind == "audit":
        result["audit_proposal_conflicts"] = audit_proposal_conflicts
    return result


def do_accept(root: Path, run_dir: Path, output_path: Path) -> dict:
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    output = lz_common.read_json(output_path, "accept output")
    kind = packet.get("kind")
    if kind in packet_handoff.GUARDED_KINDS:
        packet, output = packet_handoff.verify(run_dir, output_path)
    batch_name = packet.get("batch", run_dir.name)
    cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, cfg)
    if packet.get("note_revisions") and output.get("note_revisions") != packet["note_revisions"]:
        lz_common.fail("answer must echo this packet's note_revisions; run a fresh turn", lz_common.EXIT_FAIL)

    if kind == "canon":
        return accept_canon(packet, run_dir, output)

    if kind in RESEARCH_KINDS:
        # The packet's locale names a file under the root: refuse one that
        # is not a configured target before any path is built from it.
        if packet.get("locale") not in cfg["target_locales"]:
            lz_common.fail(f"packet.json names a locale that is not a configured target: {packet.get('locale')}",
                            lz_common.EXIT_CANNOT)
        if kind == "research":
            return accept_research(root, packet, run_dir, output)
        return accept_research_verify(root, packet, run_dir, output)

    if kind not in ("translate", "review", "audit"):
        lz_common.fail(f"packet.json has an unknown kind: {kind}", lz_common.EXIT_CANNOT)

    # A note correction invalidates already-built turns too, including audit
    # passes which otherwise have no persistent candidate to clear.
    restricted = packet.get("canon_entry") or {}
    if restricted.get("entry"):
        target = next((e for e in canon_mod.load(root)["entries"] if e["id"] == restricted["entry"]), {})
        if restricted.get("note_revision") != target.get("note_revision"):
            lz_common.fail("canon note changed since restricted audit build; rebuild the packet", lz_common.EXIT_FAIL,
                            entry=restricted["entry"])
    current_lock = canon_mod.load_lock(root)
    old_lock = packet.get("canon", {})
    for item in packet.get("items", []):
        message = item_to_message(item, packet["locale"])
        packet_entries = {e["id"]: e for e in ledger_mod.canon_dependencies(message, old_lock, packet["locale"])}
        for entry in ledger_mod.canon_dependencies(message, current_lock, packet["locale"]):
            if (entry.get("note_revision") and
                    entry["note_revision"] != packet_entries.get(entry["id"], {}).get("note_revision")):
                lz_common.fail("canon note changed since packet build; rebuild the packet", lz_common.EXIT_FAIL,
                                entry=entry["id"], id=item["id"])

    ledger_data = ledger_mod.load(root)
    if kind == "translate":
        result = accept_translate(root, cfg, packet, output, ledger_data, batch_name)
    elif kind == "review":
        result = accept_review(root, cfg, packet, output, ledger_data, batch_name, run_dir)
    else:
        result = accept_audit(root, cfg, packet, output, ledger_data, batch_name, run_dir)
    # Scoped to this packet's own locale: ledger.py stores one file per
    # locale precisely so a sibling locale's concurrently-running accept
    # never gets its in-memory copy clobbered by this save.
    ledger_mod.save(root, ledger_data, locales=[packet["locale"]])
    return result


CANDIDATE_KINDS = ("term", "ui_label", "dnt")


def _valid_translation_entry(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    proposed = entry.get("proposed")
    if proposed is not None and not isinstance(proposed, str):
        return False
    current = entry.get("current")
    if current is not None and (not isinstance(current, list) or not all(isinstance(c, str) for c in current)):
        return False
    return True


def _valid_canon_translations(translations, target_locales: list) -> bool:
    if translations is None:
        return True
    if not isinstance(translations, dict):
        return False
    return all(locale in target_locales and _valid_translation_entry(entry) for locale, entry in translations.items())


def _valid_canon_output_candidate(cand, valid_ids: set, target_locales: list) -> bool:
    """One candidate from `build --kind canon`'s output, checked before it
    is written to `candidates.json`: `kind` in term|ui_label|dnt, a
    non-empty string `source`, `note` a string or absent, `occurrences` a
    list of strings each naming an id present in this packet (a non-string
    entry such as `[123]` must not reach `canon.json`: it would silently
    defeat `checks._check_dnt`, which matches occurrences by exact string
    id), and `translations` -- when given -- an object whose keys are this
    packet's target locales and whose values carry an optional string
    `proposed` and an optional list-of-strings `current`."""
    if not isinstance(cand, dict):
        return False
    if cand.get("kind") not in CANDIDATE_KINDS:
        return False
    if not isinstance(cand.get("source"), str) or not cand["source"]:
        return False
    note = cand.get("note")
    if note is not None and not isinstance(note, str):
        return False
    occurrences = cand.get("occurrences")
    if not isinstance(occurrences, list) or not all(isinstance(o, str) for o in occurrences):
        return False
    if not all(o in valid_ids for o in occurrences):
        return False
    return _valid_canon_translations(cand.get("translations"), target_locales)


def accept_canon(packet: dict, run_dir: Path, output: dict) -> dict:
    """Validate a canon turn's output before writing `candidates.json`: a
    malformed candidate is dropped and listed in the result rather than
    either silently written through or failing the whole batch -- the
    batch may carry a mix of good and bad candidates, and the good ones
    must still reach `canon.py import`."""
    if not isinstance(output, dict) or not isinstance(output.get("candidates"), list):
        lz_common.fail("canon output is missing a 'candidates' list", lz_common.EXIT_FAIL)

    valid_ids = {item["id"] for item in packet.get("items", [])}
    target_locales = packet.get("locales", [])

    valid_candidates, dropped = [], []
    for i, cand in enumerate(output["candidates"]):
        if _valid_canon_output_candidate(cand, valid_ids, target_locales):
            valid_candidates.append(cand)
        else:
            dropped.append({"index": i, "candidate": cand})

    out_path = run_dir / "candidates.json"
    lz_common.atomic_write_json(out_path, {"candidates": valid_candidates})
    return {
        "ok": True, "kind": "canon", "run": run_dir.name, "candidates": len(valid_candidates),
        "dropped": dropped, "output": str(out_path),
    }


# ---------------------------------------------------------------------------
# accept: usage research
# ---------------------------------------------------------------------------


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_http_url(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _progress_problem(result: dict):
    if "progress" not in result:
        return None  # Backward compatibility for saved pre-checkpoint answers.
    progress = result["progress"]
    if not isinstance(progress, dict) or progress.get("status") not in ("complete", "resource_limited"):
        return "progress.status must be complete or resource_limited"
    remaining = progress.get("remaining")
    if not isinstance(remaining, list) or not all(_non_empty_str(task) for task in remaining):
        return "progress.remaining must be a list of non-empty strings"
    if bool(remaining) != (progress["status"] == "resource_limited"):
        return "resource_limited needs remaining work; complete needs an empty remaining list"
    return None


def _checkpoint_matches(item: dict, record) -> bool:
    return ("checkpoint_sha256" not in item
            or item["checkpoint_sha256"] == lz_common.sha256_json(record))


def _attempt(record, kind: str, result: dict, run_dir: Path, at: str) -> list:
    history = list((record or {}).get("attempts") or [])
    if record and not history:
        history.append({"kind": "legacy_snapshot", "record": json.loads(json.dumps(record))})
    return history + [
        {"kind": kind, "packet": str(run_dir / "packet.json"), "at": at, "answer": result}]


def _research_problem(term):
    """`None` for a well-formed research reply for one term, else the reason
    it is refused. A finding carries what a person can follow up: a source
    name, an http(s) URL, the rendering and a verbatim quote. A reply with
    no findings is honest only at `low` confidence."""
    if not isinstance(term, dict):
        return "not an object"
    if problem := _progress_problem(term):
        return problem
    for field in ("recommendation", "note"):
        if not _non_empty_str(term.get(field)):
            return f"{field} must be a non-empty string"
    if term.get("confidence") not in canon_mod.RESEARCH_CONFIDENCE:
        return "confidence must be one of: " + ", ".join(canon_mod.RESEARCH_CONFIDENCE)
    findings = term.get("findings")
    if not isinstance(findings, list):
        return "findings must be a list"
    if not findings and term["confidence"] != "low":
        return "empty findings need confidence low"
    for n, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"finding {n} is not an object"
        for field in ("site", "rendering", "quote"):
            if not _non_empty_str(finding.get(field)):
                return f"finding {n}: {field} must be a non-empty string"
        if not _valid_http_url(finding.get("url")):
            return f"finding {n}: url must be an http(s) URL with a host"
    return None


def accept_research(root: Path, packet: dict, run_dir: Path, output: dict) -> dict:
    """Store each valid research reply as a new record in the packet
    locale's research file and report the rest. A new record replaces the
    entry's old one and resets its verification: the old verdict was cast
    on different research. A checkpoint continuation retains old findings;
    every accepted raw answer is archived. Only the locale's own file is
    written, and only once."""
    terms = output.get("terms") if isinstance(output, dict) else None
    if not isinstance(terms, dict):
        lz_common.fail("research output is missing a 'terms' object", lz_common.EXIT_FAIL)

    locale = packet["locale"]
    items = packet.get("items", [])
    extra = sorted(set(terms) - {item["id"] for item in items})
    data = canon_mod.load_research(root, locale)
    by_entry = {e["id"]: e for e in canon_mod.load(root)["entries"]}
    at = lz_common.now_iso()
    stored, invalid, missing, stale, limited = [], [], [], [], []
    for item in items:
        entry_id = item["id"]
        if item.get("note_revision") != by_entry.get(entry_id, {}).get("note_revision"):
            invalid.append({"id": entry_id, "reason": "canon note changed since packet build"})
            continue
        previous = data["entries"].get(entry_id)
        if not _checkpoint_matches(item, previous):
            stale.append(entry_id)
            continue
        if entry_id not in terms:
            missing.append(entry_id)
            continue
        term = terms[entry_id]
        reason = _research_problem(term)
        if reason is not None:
            invalid.append({"id": entry_id, "reason": reason})
            continue
        research = {
            "recommendation": term["recommendation"], "confidence": term["confidence"], "note": term["note"],
            "findings": [{k: f[k] for k in ("site", "url", "rendering", "quote")} for f in term["findings"]],
        }
        if "progress" in term:
            research["progress"] = term["progress"]
        if item.get("resume"):
            findings = list(previous["research"]["findings"])
            findings.extend(f for f in research["findings"] if f not in findings)
            research["findings"] = findings
        data["entries"][entry_id] = {
            "kind": item["kind"], "source": item["source"],
            "research": research, "research_sha256": canon_mod.research_sha256(research),
            "researched": {"run": run_dir.name, "at": at, "packet": str(run_dir / "packet.json")},
            "verification": None,
            "attempts": _attempt(previous, "research", term, run_dir, at),
            **({"note_revision": item["note_revision"]} if item.get("note_revision") else {}),
        }
        stored.append(entry_id)
        if canon_mod.resource_limited(research):
            limited.append(entry_id)
    if stored:
        canon_mod.save_research(root, locale, data)
    return {"ok": True, "kind": "research", "locale": locale, "run": run_dir.name, "stored": stored,
            "invalid": invalid, "missing": missing, "extra": extra, "stale": stale, "resource_limited": limited}


def _verdict_problem(verdict: dict, finding_count: int, allow_partial: bool = False):
    """`None` for a well-formed verdict, else the reason it is refused:
    one verdict object per stored finding index (a subset for a partial
    reply or continuation), an enum verdict and string `problems`/`better`."""
    if problem := _progress_problem(verdict):
        return problem
    findings = verdict.get("findings")
    if not isinstance(findings, list):
        return "findings must be a list"
    indexes = []
    for n, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return f"findings[{n}] is not an object"
        index = finding.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return f"findings[{n}]: index must be an integer"
        if not isinstance(finding.get("confirmed"), bool):
            return f"findings[{n}]: confirmed must be true or false"
        if not isinstance(finding.get("detail"), str):
            return f"findings[{n}]: detail must be a string"
        indexes.append(index)
    if allow_partial or canon_mod.resource_limited(verdict):
        if len(set(indexes)) != len(indexes) or any(i < 0 or i >= finding_count for i in indexes):
            return "partial findings must use unique in-range source indexes"
    elif sorted(indexes) != list(range(finding_count)):
        return f"findings must hold exactly one entry for each of the {finding_count} source indexes"
    if verdict.get("verdict") not in canon_mod.RESEARCH_VERDICTS:
        return "verdict must be one of: " + ", ".join(canon_mod.RESEARCH_VERDICTS)
    for field in ("problems", "better"):
        if not isinstance(verdict.get(field), str):
            return f"{field} must be a string"
    return None


def accept_research_verify(root: Path, packet: dict, run_dir: Path, output: dict) -> dict:
    """Attach each valid verdict to its entry's research record. A verdict
    is only ever bound to the research it was cast on: it must echo the
    item's `research_sha256`, and the locale's record must still carry that
    hash -- an entry re-researched since the build is `missing`, never given
    a verdict about different evidence."""
    verdicts = output.get("verdicts") if isinstance(output, dict) else None
    if not isinstance(verdicts, dict):
        lz_common.fail("research_verify output is missing a 'verdicts' object", lz_common.EXIT_FAIL)

    locale = packet["locale"]
    items = packet.get("items", [])
    extra = sorted(set(verdicts) - {item["id"] for item in items})
    data = canon_mod.load_research(root, locale)
    by_entry = {e["id"]: e for e in canon_mod.load(root)["entries"]}
    at = lz_common.now_iso()
    verified, invalid, missing, limited = [], [], [], []
    for item in items:
        entry_id = item["id"]
        verdict = verdicts.get(entry_id)
        if verdict is None:
            missing.append(entry_id)
            continue
        if not isinstance(verdict, dict):
            invalid.append({"id": entry_id, "reason": "not an object"})
            continue
        record = data["entries"].get(entry_id)
        if (not _checkpoint_matches(item, record)
                or verdict.get("research_sha256") != item["research_sha256"]
                or record is None or record.get("research_sha256") != item["research_sha256"]
                or record.get("stale_note")
                or item.get("note_revision") != by_entry.get(entry_id, {}).get("note_revision")
                or record.get("note_revision") != item.get("note_revision")):
            missing.append(entry_id)
            continue
        combined = dict(verdict)
        if item.get("resume"):
            findings = {f["index"]: f for f in record["verification"]["findings"]}
            # A continuation answers only the unperformed checks; prior checks win.
            for finding in verdict.get("findings", []) if isinstance(verdict.get("findings"), list) else []:
                if isinstance(finding, dict) and isinstance(finding.get("index"), int):
                    findings.setdefault(finding["index"], finding)
            combined["findings"] = list(findings.values())
        reason = _verdict_problem(verdict, len(item["research"]["findings"]), allow_partial=bool(item.get("resume")))
        reason = reason or _verdict_problem(combined, len(item["research"]["findings"]))
        if reason is not None:
            invalid.append({"id": entry_id, "reason": reason})
            continue
        attempts = _attempt(record, "research_verify", verdict, run_dir, at)
        record["verification"] = {
            "research_sha256": item["research_sha256"],
            "findings": [
                {"index": f["index"], "confirmed": f["confirmed"], "detail": f["detail"]}
                for f in sorted(combined["findings"], key=lambda f: f["index"])
            ],
            "verdict": verdict["verdict"], "problems": verdict["problems"], "better": verdict["better"],
            "run": run_dir.name, "at": at, "packet": str(run_dir / "packet.json"),
        }
        if "progress" in verdict:
            record["verification"]["progress"] = verdict["progress"]
        record["attempts"] = attempts
        (limited if canon_mod.resource_limited(verdict) else verified).append(entry_id)
    if verified or limited:
        canon_mod.save_research(root, locale, data)
    return {"ok": True, "kind": "research_verify", "locale": locale, "run": run_dir.name, "verified": verified,
            "invalid": invalid, "missing": missing, "extra": extra, "resource_limited": limited}


# ---------------------------------------------------------------------------
# accept-audit
# ---------------------------------------------------------------------------


def do_accept_audit(root: Path, locale: str, ids: list, by: str) -> dict:
    cfg = lz_common.load_config(root)
    if locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
    ledger_data = ledger_mod.load(root)
    entries = ledger_mod.locale_entries(ledger_data, locale)

    accepted, failed = [], []
    for msg_id in ids:
        entry = entries.get(msg_id)
        proposal = entry.get("audit_proposal") if entry else None
        if not proposal:
            failed.append({"id": msg_id, "reason": "no stored audit proposal"})
            continue
        if proposal.get("value") is None:
            failed.append({"id": msg_id, "reason": "audit finding has no proposed replacement"})
            continue
        if proposal.get("checks") != "pass":
            failed.append({"id": msg_id, "reason": "audit proposal failed checks"})
            continue
        candidate = {
            "value": proposal["value"],
            "value_sha256": proposal["value_sha256"],
            "origin": "audit",
            "audit_scope": proposal.get("audit_scope"),
            "source_sha256": proposal["source_sha256"],
            "context_sha256": proposal["context_sha256"],
            "style_sha256": proposal["style_sha256"],
            "canon_sha256": proposal["canon_sha256"],
            "audited_target_sha256": proposal["audited_target_sha256"],
            "checks": "pass",
            "problems": [],
            "verdict": None,
            "accepted_by": by,
        }
        ledger_mod.set_candidate(ledger_data, locale, msg_id, candidate)
        entries[msg_id]["audit_proposal"] = None
        accepted.append(msg_id)

    ledger_mod.save(root, ledger_data, locales=[locale])
    return {"ok": True, "locale": locale, "accepted": accepted, "failed": failed, "by": by}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_templates_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--templates-dir", default=None, help=argparse.SUPPRESS)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = lz_common.make_parser("packets.py", "Build and accept software-localizer model-turn packets.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Build packets for one turn kind.")
    p_build.add_argument("--root", required=True)
    p_build.add_argument("--kind", required=True, choices=KINDS + RESEARCH_KINDS)
    p_build.add_argument("--locale", default=None)
    p_build.add_argument("--fresh", action="store_true",
                         help="with unrestricted --kind audit: include already reviewed messages")
    p_build.add_argument("--entry", action="append", default=None,
                         help="with --kind audit: a restricted audit of one canon entry (after canon.py change); "
                              "with --kind research or research_verify: limit to this canon entry (repeatable)")
    _add_templates_dir_arg(p_build)

    p_accept = sub.add_parser("accept", help="Validate and apply a turn's output.")
    p_accept.add_argument("--root", required=True)
    p_accept.add_argument("--run", required=True)
    p_accept.add_argument("--output", required=True)

    p_aa = sub.add_parser("accept-audit", help="Promote stored audit proposals into candidates.")
    p_aa.add_argument("--root", required=True)
    p_aa.add_argument("--locale", required=True)
    p_aa.add_argument("--id", dest="ids", action="append", default=None)
    p_aa.add_argument("--file", default=None)
    p_aa.add_argument("--by", required=True)

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.command == "build":
        root = lz_common.resolve_root(args.root)
        templates_dir = Path(args.templates_dir) if args.templates_dir else default_templates_dir()
        result = do_build(root, args.kind, args.locale, templates_dir, entry_ids=args.entry, fresh=args.fresh)
        lz_common.emit(result)
        return lz_common.EXIT_OK

    if args.command == "accept":
        root = lz_common.resolve_root(args.root)
        run_dir = Path(args.run).resolve()
        output_path = Path(args.output).resolve()
        result = do_accept(root, run_dir, output_path)
        lz_common.emit(result)
        return lz_common.EXIT_OK

    if args.command == "accept-audit":
        root = lz_common.resolve_root(args.root)
        ids = list(args.ids or [])
        if args.file:
            file_data = lz_common.read_json(Path(args.file), "accept-audit --file")
            ids.extend(file_data.get("ids", []))
        if not ids:
            lz_common.fail("accept-audit needs at least one id (--id or --file)", lz_common.EXIT_CANNOT)
        result = do_accept_audit(root, args.locale, ids, args.by)
        lz_common.emit(result)
        return lz_common.EXIT_OK if not result["failed"] else lz_common.EXIT_FAIL

    # Unreachable: argparse's required subparsers group admits only the
    # three commands above.
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    lz_common.run_main(main)
