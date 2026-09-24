#!/usr/bin/env python3
"""Build and accept model-turn packets (plan section 10): translate, review,
audit, canon. Every packet is a frozen snapshot of what the model saw — a
turn is validated against that snapshot, never against a possibly-changed
`messages.json` or `canon.json` read again later.

The coverage turn is built and accepted by `adapter_check.py`, not here (plan
section 6): its packet is not id-keyed the way the four kinds here are, and
the pinned cross-module API gives this module no way to read its request.

Design choices this module makes where the plan leaves a gap (see the build
report for the reasoning kept short here):

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
  that candidate's hash from a later `build --kind translate` (plan section
  12 says the translate/review loop ends when `build` finds nothing left;
  the ledger state alone (`pending`/`stale`) does not change until export,
  so without this exclusion `build` would offer an already-approved
  candidate for retranslation forever).
- An audit `fail` with a `proposed` value is stored on the ledger entry as
  `audit_proposal` (an extra field beyond plan section 7's documented
  schema; JSON round-trips it fine) — there is no pinned setter for it, so
  it is read and written as a plain dict field, matching `accept-audit`'s
  own job of turning it into a candidate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lz_common  # noqa: E402
import adapter_client  # noqa: E402
import checks as checks_mod  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import canon as canon_mod  # noqa: E402

KINDS = ("translate", "review", "audit", "canon")
TRANSLATE_STATES = ("pending", "stale")
AUDIT_STATES = ("existing", "translated", "human_locked")
CANON_PSEUDO_LOCALE = "_canon"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def default_templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def render_prompt(templates_dir, kind: str, packet: dict) -> str:
    """Render `<kind>_TASK.md`, replacing the literal line `{{PACKET_JSON}}`
    with the packet's JSON. Raises `LookupError` naming the problem when the
    template file is missing or does not carry the placeholder line — the
    caller turns that into an `EXIT_CANNOT` refusal."""
    template_path = Path(templates_dir) / f"{kind}_TASK.md"
    if not template_path.is_file():
        raise LookupError(f"template is missing: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    packet_json = json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=False)

    lines = text.split("\n")
    out_lines = []
    replaced = False
    for line in lines:
        if line.strip() == "{{PACKET_JSON}}":
            out_lines.append(packet_json)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        raise LookupError(f"template has no {{{{PACKET_JSON}}}} line: {template_path}")
    return "\n".join(out_lines)


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
    """Ids grouped by prefix, each group chunked to at most `batch_size` --
    a batch never mixes two different id prefixes, so the model always sees
    a set of genuinely related messages (the point of grouping by prefix in
    the first place); a prefix with more ids than `batch_size` becomes
    several same-prefix batches instead of spilling into its neighbor."""
    groups: dict = {}
    for i in sorted(ids):
        groups.setdefault(batch_prefix(i), []).append(i)
    batches = []
    for prefix in sorted(groups):
        group_ids = groups[prefix]
        for start in range(0, len(group_ids), max(1, batch_size)):
            batches.append(group_ids[start:start + max(1, batch_size)])
    return batches


def name_batches(batches: list) -> list:
    """`[(name, ids), ...]`, names derived from each batch's id prefix and
    de-duplicated within this call."""
    used: set = set()
    named = []
    for ids in batches:
        base = _sanitize_batch_name(batch_prefix(ids[0]))
        name = base
        n = 2
        while name in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name)
        named.append((name, ids))
    return named


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _locale_entries(ledger_data: dict, locale: str) -> dict:
    return ledger_data.get("locales", {}).get(locale, {})


def _candidate_ready(entry: dict) -> bool:
    """A candidate whose checks passed and whose verdict is a pass bound to
    that exact candidate hash — nothing left for a translate round to do."""
    cand = entry.get("candidate")
    if not cand or cand.get("checks") != "pass":
        return False
    verdict = cand.get("verdict")
    return bool(verdict) and verdict.get("verdict") == "pass" and verdict.get("value_sha256") == cand.get("value_sha256")


def select_translate(ledger_data: dict, locale: str) -> list:
    entries = _locale_entries(ledger_data, locale)
    return [i for i, e in entries.items() if e.get("state") in TRANSLATE_STATES and not _candidate_ready(e)]


def select_review(ledger_data: dict, locale: str) -> list:
    entries = _locale_entries(ledger_data, locale)
    result = []
    for i, e in entries.items():
        cand = e.get("candidate")
        if not cand or cand.get("checks") != "pass":
            continue
        verdict = cand.get("verdict")
        if verdict is None or verdict.get("value_sha256") != cand.get("value_sha256"):
            result.append(i)
    return result


def select_audit(ledger_data: dict, locale: str) -> list:
    entries = _locale_entries(ledger_data, locale)
    return [i for i, e in entries.items() if e.get("state") in AUDIT_STATES]


# ---------------------------------------------------------------------------
# Canon lock filtering (translate / review / audit packets)
# ---------------------------------------------------------------------------


def _source_occurs(entry_source: str, item_sources: list) -> bool:
    for src in item_sources:
        for form in checks_mod.forms_of(src):
            if entry_source in form:
                return True
    return False


def filter_canon_lock(canon_lock: dict, item_sources: list) -> dict:
    entries = canon_lock.get("entries", []) if isinstance(canon_lock, dict) else []
    kept = [e for e in entries if _source_occurs(e.get("source", ""), item_sources)]
    return {"entries": kept}


# ---------------------------------------------------------------------------
# messages.json helpers
# ---------------------------------------------------------------------------


def message_by_id(messages: dict) -> dict:
    return {m["id"]: m for m in messages["messages"]}


def plural_item_fields(message: dict, locale: str):
    plural = message.get("plural")
    if plural is None:
        return None, None, None
    target_labels = plural["target_labels"].get(locale)
    return target_labels, plural.get("count_arguments", []), plural.get("general_index")


def item_to_message(item: dict, locale: str) -> dict:
    """Reconstruct the minimal `message` shape `checks.check_candidate` and
    `ledger.*_sha256` need, from a packet item (accept time never re-reads
    `messages.json`; the packet is the frozen snapshot)."""
    target_labels = item.get("target_labels")
    if target_labels is not None:
        plural = {
            "target_labels": {locale: target_labels},
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
    target_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
        item["count_arguments"] = count_arguments
        item["general_index"] = general_index
    previous, problems = _previous_fix_round(entry)
    if previous is not None:
        item["previous"] = previous
        item["problems"] = problems
    return item


def build_review_item(message: dict, locale: str, entry: dict) -> dict:
    """The candidate's `source_sha256`/`context_sha256`/`style_sha256`
    snapshots ride along as-built: `accept`'s review path re-checks them
    against the *current* candidate before binding a verdict, so a verdict
    cast on this snapshot never attaches to a candidate quietly rebuilt
    under a changed context or style, even when the rebuilt value happens to
    hash the same."""
    cand = entry["candidate"]
    item = {
        "id": message["id"],
        "source": message["source"],
        "value": cand["value"],
        "value_sha256": cand["value_sha256"],
        "source_sha256": cand["source_sha256"],
        "context_sha256": cand["context_sha256"],
        "style_sha256": cand["style_sha256"],
        "context": message.get("context", {}),
    }
    target_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
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
    target_labels, count_arguments, general_index = plural_item_fields(message, locale)
    if target_labels is not None:
        item["target_labels"] = target_labels
        item["count_arguments"] = count_arguments
        item["general_index"] = general_index
    return item


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


def do_build(root: Path, kind: str, locale, templates_dir: Path, entry_id=None) -> dict:
    if kind not in KINDS:
        lz_common.fail(f"unknown packet kind: {kind}", lz_common.EXIT_CANNOT)
    cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, cfg)

    if kind != "canon" and not locale:
        lz_common.fail(f"kind {kind!r} requires --locale", lz_common.EXIT_CANNOT)
    if locale and locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
    if entry_id is not None and kind != "audit":
        lz_common.fail("--entry is only valid with --kind audit", lz_common.EXIT_CANNOT)

    messages = lz_common.load_messages(root / "messages.json")
    by_id = message_by_id(messages)
    ledger_data = ledger_mod.load(root)
    canon_lock = canon_mod.load_lock(root)
    batch_size = cfg.get("batch_size", 40)

    if entry_id is not None:
        return _build_restricted_canon_audit(root, cfg, locale, entry_id, by_id, canon_lock, templates_dir)

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
        selected_ids = select_audit(ledger_data, locale)

    entries = _locale_entries(ledger_data, locale)
    style = cfg["style"][locale]
    named = name_batches(make_batches(selected_ids, batch_size))
    batches_out = []
    for name, ids in named:
        items = []
        item_sources = []
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
            item_sources.append(message["source"])
        if not items:
            continue
        packet = {
            "kind": kind,
            "locale": locale,
            "batch": name,
            "style": style,
            "canon": filter_canon_lock(canon_lock, item_sources),
            "items": items,
        }
        if kind == "audit":
            packet["canon_entry"] = None
        batches_out.append(_write_batch(run_locale_dir, name, kind, packet, templates_dir))

    return {"ok": True, "kind": kind, "locale": locale, "selected": len(selected_ids),
            "batches": batches_out, "skipped": skipped}


def _build_restricted_canon_audit(root: Path, cfg: dict, locale: str, entry_id: str, by_id: dict,
                                   canon_lock: dict, templates_dir: Path) -> dict:
    """`build --kind audit --entry ID`: the restricted canon audit
    `canon.py change` requests (plan section 8) — an audit packet scoped to
    one canon entry's occurrences, with `canon_entry` set so the reviewer
    judges only that entry's rendering."""
    request_path = root / "runs" / locale / f"canon-audit-{entry_id}.json"
    if not request_path.is_file():
        lz_common.fail(f"no restricted canon-audit request for entry: {entry_id}",
                        lz_common.EXIT_CANNOT, entry=entry_id)
    request = lz_common.read_json(request_path, "restricted canon-audit request")
    occurrences = request.get("occurrences", [])
    style = cfg["style"][locale]
    run_locale_dir = root / "runs" / locale
    batch_size = cfg.get("batch_size", 40)

    chunks = make_batches(occurrences, batch_size)
    canon_entry_meta = {
        "entry": entry_id, "source": request.get("source"),
        "before": request.get("before"), "after": request.get("after"),
        "reason": request.get("reason"),
    }
    skipped, batches_out = [], []
    for n, ids in enumerate(chunks):
        name = f"canon-audit-{entry_id}" if n == 0 else f"canon-audit-{entry_id}-{n + 1}"
        items, item_sources = [], []
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
            item_sources.append(message["source"])
        if not items:
            continue
        packet = {
            "kind": "audit", "locale": locale, "batch": name, "style": style,
            "canon": filter_canon_lock(canon_lock, item_sources),
            "canon_entry": canon_entry_meta,
            "items": items,
        }
        batches_out.append(_write_batch(run_locale_dir, name, "audit", packet, templates_dir))

    return {"ok": True, "kind": "audit", "locale": locale, "selected": len(occurrences),
            "batches": batches_out, "skipped": skipped, "canon_entry": entry_id}


def _write_batch(run_locale_dir: Path, name: str, kind: str, packet: dict, templates_dir: Path) -> dict:
    batch_dir = run_locale_dir / name
    try:
        prompt = render_prompt(templates_dir, kind, packet)
    except LookupError as exc:
        lz_common.fail(str(exc), lz_common.EXIT_CANNOT)
    lz_common.atomic_write_json(batch_dir / "packet.json", packet)
    lz_common.atomic_write_text(batch_dir / "prompt.md", prompt)
    return {"batch": name, "dir": str(batch_dir), "items": len(packet["items"])}


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


def _parse_batch(root: Path, cfg: dict, items: list, extra_texts: dict) -> dict:
    """One batched `adapter_client.parse` call: every source form of every
    item, plus whatever extra (key -> text) forms the caller needs parsed
    (a candidate value's forms, or a proposed replacement's forms)."""
    to_parse = []
    for item in items:
        for idx, form in enumerate(checks_mod.forms_of(item["source"])):
            to_parse.append({"key": f"{item['id']}::source::{idx}", "text": form})
    for key, text in extra_texts.items():
        to_parse.append({"key": key, "text": text})
    if not to_parse:
        return {}
    project_dir = str(Path(cfg["project_root"]))
    return adapter_client.parse(str(root), cfg, project_dir, to_parse)


def _parse_list(results: dict, item_id: str, prefix: str, forms: list) -> list:
    return [results[f"{item_id}::{prefix}::{idx}"] for idx in range(len(forms))]


def _single_or_list(parses: list):
    return parses[0] if len(parses) == 1 else parses


_CANDIDATE_SNAPSHOT_FIELDS = ("value_sha256", "source_sha256", "context_sha256", "style_sha256")


def _review_candidate_still_matches(ledger_data: dict, locale: str, msg_id: str, item: dict) -> bool:
    """A review verdict binds to the exact candidate the packet was built
    from: the id's *current* candidate must still carry the same
    value_sha256, source_sha256, context_sha256 and style_sha256 the packet
    item recorded at build time. If the candidate was rebuilt since (even to
    the same value, under a changed context or style), this is False and
    the verdict must not attach."""
    entry = _locale_entries(ledger_data, locale).get(msg_id)
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
    --file` reads (plan section 10, documented in `canon.py`'s own
    docstring): `occurrences` is this message id, `translations` carries the
    proposed value under this locale."""
    return {
        "kind": cand["kind"],
        "source": cand["source"],
        "note": cand.get("note") or "",
        "occurrences": [msg_id],
        "translations": {locale: {"proposed": cand.get("proposed"), "current": []}},
    }


def _maybe_escalate(ledger_data: dict, cfg: dict, locale: str, msg_id: str, problems: list) -> bool:
    entry = _locale_entries(ledger_data, locale).get(msg_id, {})
    if entry.get("rounds", 0) >= cfg.get("max_rounds", 3):
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

    accepted, failed, escalated = [], [], []
    for msg_id, item in items.items():
        if msg_id not in translations:
            continue
        value = translations[msg_id]
        message = item_to_message(item, locale)
        source_forms = checks_mod.forms_of(item["source"])
        try:
            value_forms = checks_mod.forms_of(value)
        except (TypeError, KeyError):
            failed.append({"id": msg_id, "reason": "value is not a string or {'forms': [...]}"})
            continue

        extra_texts = {f"{msg_id}::value::{idx}": form for idx, form in enumerate(value_forms)}
        parse_results = _parse_batch(root, cfg, [item], extra_texts)
        source_parse = _single_or_list(_parse_list(parse_results, msg_id, "source", source_forms))
        value_parse = _single_or_list(_parse_list(parse_results, msg_id, "value", value_forms))

        problems = checks_mod.check_candidate(message, locale, value, source_parse, value_parse, canon_lock, cfg)
        candidate = {
            "value": value,
            "value_sha256": lz_common.value_sha256(value),
            "origin": "translate",
            "source_sha256": ledger_mod.source_sha256(message),
            "context_sha256": ledger_mod.context_sha256(message, locale),
            "style_sha256": ledger_mod.style_sha256(cfg, locale),
            "audited_target_sha256": None,
            "checks": "fail" if problems else "pass",
            "problems": problems,
            "verdict": None,
            "accepted_by": None,
        }
        ledger_mod.set_candidate(ledger_data, locale, msg_id, candidate)
        entry = _locale_entries(ledger_data, locale)[msg_id]
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


def accept_review(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict, batch_name: str) -> dict:
    return _accept_review_or_audit(root, cfg, packet, output, ledger_data, batch_name, kind="review")


def accept_audit(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict, batch_name: str) -> dict:
    return _accept_review_or_audit(root, cfg, packet, output, ledger_data, batch_name, kind="audit")


def _accept_review_or_audit(root: Path, cfg: dict, packet: dict, output: dict, ledger_data: dict,
                             batch_name: str, kind: str) -> dict:
    locale = packet["locale"]
    items = {it["id"]: it for it in packet["items"]}
    verdicts = output.get("verdicts")
    if not isinstance(verdicts, dict):
        lz_common.fail(f"{kind} output is missing 'verdicts'", lz_common.EXIT_FAIL)

    canon_lock = packet.get("canon", {"entries": []})
    missing, extra_ids = [], sorted(set(verdicts) - set(items))

    # Gather every proposed value's forms up front for one batched parse call.
    valid_verdicts = {}
    for msg_id, item in items.items():
        verdict = verdicts.get(msg_id)
        if not isinstance(verdict, dict) or verdict.get("value_sha256") != item["value_sha256"]:
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

    extra_texts = {}
    for msg_id, verdict in valid_verdicts.items():
        proposed = verdict.get("proposed")
        if verdict.get("verdict") == "fail" and proposed is not None:
            for idx, form in enumerate(checks_mod.forms_of(proposed)):
                extra_texts[f"{msg_id}::proposed::{idx}"] = form
    parse_results = _parse_batch(root, cfg, list(items.values()), extra_texts)

    passed, failed_ids, escalated, proposals_stored = [], [], [], []
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

        raw_candidates = verdict.get("new_canon_candidates")
        if raw_candidates is None:
            raw_candidates = []
        elif not isinstance(raw_candidates, list):
            # The whole field is the wrong shape (e.g. a string) -- record
            # it as one invalid entry rather than iterating its characters,
            # which used to land single-character "candidates" in the side
            # file and break report.py's `cand.get(...)` reads.
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
            entry = _locale_entries(ledger_data, locale)[msg_id]
            entry["rounds"] = entry.get("rounds", 0) + 1
            failed_ids.append(msg_id)
            if _maybe_escalate(ledger_data, cfg, locale, msg_id, verdict.get("issues", [])):
                escalated.append(msg_id)
                entry["review_proposal"] = {"value": verdict.get("proposed"), "issues": verdict.get("issues", []),
                                             "run": batch_name} if verdict.get("proposed") is not None else None
                continue
            proposed = verdict.get("proposed")
            if proposed is not None:
                source_forms = checks_mod.forms_of(item["source"])
                proposed_forms = checks_mod.forms_of(proposed)
                source_parse = _single_or_list(_parse_list(parse_results, msg_id, "source", source_forms))
                proposed_parse = _single_or_list(_parse_list(parse_results, msg_id, "proposed", proposed_forms))
                problems = checks_mod.check_candidate(
                    message, locale, proposed, source_parse, proposed_parse, canon_lock, cfg,
                )
                old_candidate = entry.get("candidate") or {}
                new_candidate = {
                    "value": proposed,
                    "value_sha256": lz_common.value_sha256(proposed),
                    "origin": old_candidate.get("origin", "translate"),
                    "source_sha256": ledger_mod.source_sha256(message),
                    "context_sha256": ledger_mod.context_sha256(message, locale),
                    "style_sha256": ledger_mod.style_sha256(cfg, locale),
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
            if proposed is not None:
                source_forms = checks_mod.forms_of(item["source"])
                proposed_forms = checks_mod.forms_of(proposed)
                source_parse = _single_or_list(_parse_list(parse_results, msg_id, "source", source_forms))
                proposed_parse = _single_or_list(_parse_list(parse_results, msg_id, "proposed", proposed_forms))
                problems = checks_mod.check_candidate(
                    message, locale, proposed, source_parse, proposed_parse, canon_lock, cfg,
                )
                entry = _locale_entries(ledger_data, locale).setdefault(msg_id, {})
                entry["audit_proposal"] = {
                    "value": proposed,
                    "value_sha256": lz_common.value_sha256(proposed),
                    "audited_target_sha256": item["value_sha256"],
                    "source_sha256": ledger_mod.source_sha256(message),
                    "context_sha256": ledger_mod.context_sha256(message, locale),
                    "style_sha256": ledger_mod.style_sha256(cfg, locale),
                    "checks": "fail" if problems else "pass",
                    "problems": problems,
                    "issues": verdict.get("issues", []),
                    "run": batch_name,
                }
                proposals_stored.append(msg_id)

    if new_canon_candidates:
        lz_common.atomic_write_json(
            Path(root) / "runs" / locale / batch_name / "new_canon_candidates.json",
            {"candidates": new_canon_candidates},
        )

    return {
        "ok": True, "kind": kind, "locale": locale, "run": batch_name,
        "passed": passed, "failed": failed_ids, "missing": missing, "extra": extra_ids,
        "escalated": escalated, "audit_proposals": proposals_stored,
        "invalid_canon_candidates": invalid_canon_candidates,
    }


def do_accept(root: Path, run_dir: Path, output_path: Path) -> dict:
    packet = lz_common.read_json(run_dir / "packet.json", "packet.json")
    output = lz_common.read_json(output_path, "accept output")
    kind = packet.get("kind")
    batch_name = packet.get("batch", run_dir.name)
    cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, cfg)

    if kind == "canon":
        return accept_canon(run_dir, output)

    if kind not in ("translate", "review", "audit"):
        lz_common.fail(f"packet.json has an unknown kind: {kind}", lz_common.EXIT_CANNOT)

    ledger_data = ledger_mod.load(root)
    if kind == "translate":
        result = accept_translate(root, cfg, packet, output, ledger_data, batch_name)
    elif kind == "review":
        result = accept_review(root, cfg, packet, output, ledger_data, batch_name)
    else:
        result = accept_audit(root, cfg, packet, output, ledger_data, batch_name)
    # Scoped to this packet's own locale: ledger.py stores one file per
    # locale precisely so a sibling locale's concurrently-running accept
    # never gets its in-memory copy clobbered by this save.
    ledger_mod.save(root, ledger_data, locales=[packet["locale"]])
    return result


CANDIDATE_KINDS = ("term", "ui_label", "dnt")


def _validate_canon_candidates(output) -> list:
    if not isinstance(output, dict) or not isinstance(output.get("candidates"), list):
        return None
    problems = []
    for i, cand in enumerate(output["candidates"]):
        if not isinstance(cand, dict):
            problems.append(f"candidates[{i}] is not an object")
            continue
        if cand.get("kind") not in CANDIDATE_KINDS:
            problems.append(f"candidates[{i}].kind must be one of {CANDIDATE_KINDS}")
        if not isinstance(cand.get("source"), str) or not cand["source"]:
            problems.append(f"candidates[{i}].source must be a non-empty string")
        if not isinstance(cand.get("occurrences", []), list):
            problems.append(f"candidates[{i}].occurrences must be a list")
    return problems


def accept_canon(run_dir: Path, output: dict) -> dict:
    problems = _validate_canon_candidates(output)
    if problems is None:
        lz_common.fail("canon output is missing a 'candidates' list", lz_common.EXIT_FAIL)
    if problems:
        lz_common.fail("canon output has invalid candidates", lz_common.EXIT_FAIL, problems=problems)
    out_path = run_dir / "candidates.json"
    lz_common.atomic_write_json(out_path, {"candidates": output["candidates"]})
    return {"ok": True, "kind": "canon", "run": run_dir.name, "candidates": len(output["candidates"]),
            "output": str(out_path)}


# ---------------------------------------------------------------------------
# accept-audit
# ---------------------------------------------------------------------------


def do_accept_audit(root: Path, locale: str, ids: list, by: str) -> dict:
    cfg = lz_common.load_config(root)
    if locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
    ledger_data = ledger_mod.load(root)
    entries = _locale_entries(ledger_data, locale)

    accepted, failed = [], []
    for msg_id in ids:
        entry = entries.get(msg_id)
        proposal = entry.get("audit_proposal") if entry else None
        if not proposal:
            failed.append({"id": msg_id, "reason": "no stored audit proposal"})
            continue
        if proposal.get("checks") != "pass":
            failed.append({"id": msg_id, "reason": "audit proposal failed checks"})
            continue
        candidate = {
            "value": proposal["value"],
            "value_sha256": proposal["value_sha256"],
            "origin": "audit",
            "source_sha256": proposal["source_sha256"],
            "context_sha256": proposal["context_sha256"],
            "style_sha256": proposal["style_sha256"],
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
    p_build.add_argument("--kind", required=True, choices=KINDS)
    p_build.add_argument("--locale", default=None)
    p_build.add_argument("--entry", default=None,
                         help="with --kind audit: a restricted audit of one canon entry (after canon.py change)")
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
        result = do_build(root, args.kind, args.locale, templates_dir, entry_id=args.entry)
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

    lz_common.fail(f"unknown command: {args.command}", lz_common.EXIT_CANNOT)


if __name__ == "__main__":
    lz_common.run_main(main)
