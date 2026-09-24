"""Script checks for a candidate translation value (plan section 9).

Pure functions only: everything they need — the message, the adapter's parse
results for the source and the candidate, the frozen canon, the config — is
passed in. Nothing here shells out to the adapter or touches disk; the
caller (`packets.py`) collects the parse results with one batched adapter
`parse` call per batch and passes them in.

A plural message carries its forms in the order `plural.target_labels[locale]`
lists them; `source_parse`/`value_parse` follow the same order as the source
and value forms respectively. What script checks judge, and what is left to
the review turn instead, is documented in `references/checks.md`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def forms_of(value: Any) -> list:
    """A string becomes a single-element list; `{"forms": [...]}` becomes
    that list."""
    if isinstance(value, dict):
        return list(value["forms"])
    return [value]


def _tokens(parse_result: dict, kind: str) -> list[dict]:
    """The tokens of one `kind` ("argument" or "structure") from one parse
    result, or `[]` when the form did not parse."""
    if not parse_result.get("ok"):
        return []
    return [t for t in parse_result.get("tokens", []) if t.get("kind") == kind]


def _label_at(target_labels: list[dict], index: int) -> str:
    if 0 <= index < len(target_labels):
        return target_labels[index].get("label", str(index))
    return str(index)


def _lead_trail(text: str) -> tuple[str, str]:
    lead = text[: len(text) - len(text.lstrip())]
    stripped = text.rstrip()
    trail = text[len(stripped):]
    return lead, trail


def check_candidate(
    message: dict,
    locale: str,
    value: Any,
    source_parse,
    value_parse,
    canon_lock: dict,
    cfg: dict,
) -> list[dict]:
    """Every script check of plan section 9 against one candidate value.

    Returns a list of `{"check", "detail"}` problems, empty on a full pass.
    `source_parse`/`value_parse` are a single parse result for a non-plural
    message, or a list of parse results (one per form, source/value order)
    for a plural one.
    """
    plural_spec = message.get("plural")
    is_plural = plural_spec is not None
    source = message["source"]
    source_forms = forms_of(source)

    if is_plural:
        if not isinstance(value, dict) or "forms" not in value:
            return [{
                "check": "forms",
                "detail": f"locale {locale!r}: expected a forms list, got {value!r}",
            }]
        target_labels = plural_spec["target_labels"][locale]
        general_index = plural_spec["general_index"]
        count_arguments = set(plural_spec.get("count_arguments", []))
        value_forms = list(value["forms"])
        source_parses = list(source_parse)
        value_parses = list(value_parse)
    else:
        if not isinstance(value, str):
            return [{
                "check": "forms",
                "detail": f"expected a single string value, got {value!r}",
            }]
        target_labels = []
        general_index = 0
        count_arguments = set()
        value_forms = [value]
        source_parses = [source_parse]
        value_parses = [value_parse]

    problems: list[dict] = []

    if is_plural and len(value_forms) != len(target_labels):
        problems.append({
            "check": "forms",
            "detail": (
                f"locale {locale!r}: expected {len(target_labels)} forms, "
                f"got {len(value_forms)}"
            ),
        })

    problems.extend(_check_parse(value_forms, value_parses, is_plural, target_labels))
    problems.extend(_check_arguments(
        is_plural, value_forms, value_parses, source_parses, target_labels,
        general_index, count_arguments,
    ))
    problems.extend(_check_structure(
        is_plural, value_forms, value_parses, source_parses, target_labels, general_index,
    ))
    problems.extend(_check_whitespace(
        is_plural, value_forms, source_forms, target_labels, general_index,
    ))
    problems.extend(_check_empty(value_forms, is_plural, target_labels))
    problems.extend(_check_identical(
        message, value_forms, source_forms, is_plural, general_index, canon_lock, cfg,
    ))
    problems.extend(_check_max_length(message, value_forms, is_plural, target_labels))
    problems.extend(_check_dnt(message, value_forms, is_plural, target_labels, canon_lock))

    return problems


def _check_parse(value_forms, value_parses, is_plural, target_labels) -> list[dict]:
    problems = []
    for i, vp in enumerate(value_parses):
        if i >= len(value_forms):
            break
        if not vp.get("ok"):
            label = _label_at(target_labels, i) if is_plural else "value"
            error = vp.get("error", "unknown error")
            problems.append({
                "check": "parse",
                "detail": f"form '{label}' failed to parse: {error}",
            })
    return problems


def _multiset_diff(expected: list, actual: list) -> tuple[list, list]:
    ec, ac = Counter(expected), Counter(actual)
    missing = list((ec - ac).elements())
    extra = list((ac - ec).elements())
    return missing, extra


def _check_arguments(
    is_plural, value_forms, value_parses, source_parses, target_labels,
    general_index, count_arguments,
) -> list[dict]:
    problems = []

    if not is_plural:
        if value_parses[0].get("ok"):
            source_sigs = [t["signature"] for t in _tokens(source_parses[0], "argument")]
            value_sigs = [t["signature"] for t in _tokens(value_parses[0], "argument")]
            missing, extra = _multiset_diff(source_sigs, value_sigs)
            if missing or extra:
                parts = []
                if missing:
                    parts.append(f"missing {missing}")
                if extra:
                    parts.append(f"extra {extra}")
                problems.append({"check": "arguments", "detail": "; ".join(parts)})
        return problems

    source_arg_signatures: dict[str, set] = {}
    for sp in source_parses:
        for t in _tokens(sp, "argument"):
            source_arg_signatures.setdefault(t["name"], set()).add(t["signature"])

    general_tokens = _tokens(source_parses[general_index], "argument")
    required_names = set()
    required_count_group = False
    for t in general_tokens:
        if t["name"] in count_arguments:
            required_count_group = True
        else:
            required_names.add(t["name"])

    for i, vp in enumerate(value_parses):
        if i >= len(value_forms) or not vp.get("ok"):
            continue
        label = _label_at(target_labels, i)
        is_exact = bool(target_labels[i].get("exact")) if i < len(target_labels) else False
        v_tokens = _tokens(vp, "argument")

        # A count-group name (e.g. vue-i18n's "count"/"n") is validated only
        # by the coverage rule below: its whole point is that a form may use
        # any member of the group, so its literal name and signature need
        # not match what a particular source form happened to use.
        bad = [
            t["signature"] for t in v_tokens
            if t["name"] not in count_arguments
            and (
                t["name"] not in source_arg_signatures
                or t["signature"] not in source_arg_signatures[t["name"]]
            )
        ]
        if bad:
            problems.append({
                "check": "arguments",
                "detail": f"form '{label}' has unexpected argument(s) {bad}",
            })

        v_names = {t["name"] for t in v_tokens}
        missing_names = sorted(n for n in required_names if n not in v_names)
        if missing_names:
            problems.append({
                "check": "arguments",
                "detail": f"form '{label}' is missing argument(s) {missing_names}",
            })

        if required_count_group and not is_exact and not (v_names & count_arguments):
            problems.append({
                "check": "arguments",
                "detail": f"form '{label}' is missing the count argument",
            })

    return problems


def _check_structure(
    is_plural, value_forms, value_parses, source_parses, target_labels, general_index,
) -> list[dict]:
    problems = []

    if not is_plural:
        if value_parses[0].get("ok"):
            source_struct = [t["text"] for t in _tokens(source_parses[0], "structure")]
            value_struct = [t["text"] for t in _tokens(value_parses[0], "structure")]
            if source_struct != value_struct:
                problems.append({
                    "check": "structure",
                    "detail": f"expected structure tokens {source_struct}, got {value_struct}",
                })
        return problems

    general_struct = Counter(t["text"] for t in _tokens(source_parses[general_index], "structure"))
    for i, vp in enumerate(value_parses):
        if i >= len(value_forms) or not vp.get("ok"):
            continue
        v_struct = Counter(t["text"] for t in _tokens(vp, "structure"))
        if v_struct != general_struct:
            label = _label_at(target_labels, i)
            problems.append({
                "check": "structure",
                "detail": f"form '{label}' structure tokens do not match the general form",
            })
    return problems


def _check_whitespace(is_plural, value_forms, source_forms, target_labels, general_index) -> list[dict]:
    problems = []
    if not is_plural:
        src_lead, src_trail = _lead_trail(source_forms[0])
        val_lead, val_trail = _lead_trail(value_forms[0])
        if (src_lead, src_trail) != (val_lead, val_trail):
            problems.append({
                "check": "whitespace",
                "detail": (
                    f"expected leading {src_lead!r} / trailing {src_trail!r}, "
                    f"got leading {val_lead!r} / trailing {val_trail!r}"
                ),
            })
        return problems

    src_lead, src_trail = _lead_trail(source_forms[general_index])
    for i, vf in enumerate(value_forms):
        val_lead, val_trail = _lead_trail(vf)
        if (src_lead, src_trail) != (val_lead, val_trail):
            label = _label_at(target_labels, i)
            problems.append({
                "check": "whitespace",
                "detail": (
                    f"form '{label}' expected leading {src_lead!r} / trailing {src_trail!r}, "
                    f"got leading {val_lead!r} / trailing {val_trail!r}"
                ),
            })
    return problems


def _check_empty(value_forms, is_plural, target_labels) -> list[dict]:
    problems = []
    for i, vf in enumerate(value_forms):
        if not isinstance(vf, str) or vf.strip() == "":
            label = _label_at(target_labels, i) if is_plural else "value"
            problems.append({"check": "empty", "detail": f"form '{label}' is empty or whitespace-only"})
    return problems


def _dnt_source_texts(canon_lock: dict) -> list[dict]:
    return [e for e in canon_lock.get("entries", []) if e.get("kind") == "dnt"]


def _check_identical(message, value_forms, source_forms, is_plural, general_index, canon_lock, cfg) -> list[dict]:
    if message["id"] in set(cfg.get("allow_identical", [])):
        return []
    if value_forms != source_forms:
        return []
    compare_source = source_forms[general_index] if is_plural else source_forms[0]
    for entry in _dnt_source_texts(canon_lock):
        if entry.get("source") == compare_source:
            return []
    return [{"check": "identical", "detail": "value is identical to the source"}]


def _check_max_length(message, value_forms, is_plural, target_labels) -> list[dict]:
    max_length = (message.get("context") or {}).get("max_length")
    if max_length is None:
        return []
    problems = []
    for i, vf in enumerate(value_forms):
        if isinstance(vf, str) and len(vf) > max_length:
            label = _label_at(target_labels, i) if is_plural else "value"
            problems.append({
                "check": "max_length",
                "detail": f"form '{label}' is {len(vf)} characters, exceeds the limit of {max_length}",
            })
    return problems


def _check_dnt(message, value_forms, is_plural, target_labels, canon_lock) -> list[dict]:
    problems = []
    msg_id = message["id"]
    for entry in _dnt_source_texts(canon_lock):
        if msg_id not in entry.get("occurrences", []):
            continue
        term = entry.get("source", "")
        for i, vf in enumerate(value_forms):
            if not isinstance(vf, str) or term not in vf:
                label = _label_at(target_labels, i) if is_plural else "value"
                problems.append({
                    "check": "dnt",
                    "detail": (
                        f"form '{label}' is missing the do-not-translate term "
                        f"{term!r} (canon entry {entry.get('id')})"
                    ),
                })
    return problems
