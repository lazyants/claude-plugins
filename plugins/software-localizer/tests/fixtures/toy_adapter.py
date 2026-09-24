"""Test-only adapter for the "toy" locale format (plan section 5.4).

Not shipped with the plugin. Used only by the test suite as a project adapter
for a deliberately tiny format, so `adapter_check.py` and `collect.py` can be
exercised end to end against a real subprocess instead of a hand-written
`messages.json`.

Format: one flat JSON object per locale, `locales/<locale>.json`,
`{"key": "value"}`. `{name}` marks a runtime argument; `@:dotted.key` marks a
structure token (a reference to another message) that must survive
translation verbatim; a plural value is its forms joined by `" | "`. Every
value is stored and returned exactly as written -- this adapter never trims
or normalizes.

Self-contained: does not import the plugin's `lz_common`, since a real
project adapter is the project's own tooling, not part of the plugin core.
Follows the same wire contract as every other adapter: one JSON line on
stdout, exit 0 (ok), 1 (refused) or 2 (cannot run).

`--options` points at a JSON file with:

    {"plural_labels": {"<locale>": {"<source form count>": [[label, exact], ...]}},
     "general": {"<source form count>": general_index},
     "lossy": null | "trim" | "drop_key",
     "lossy_drop_key": "<key>",
     "unlisted_extra_file": "<path relative to cwd, never returned in `files`>"}

`plural_labels` and `general` are required for a project that has plural
messages. `lossy`/`lossy_drop_key` exist only so tests can drive a
deliberately broken export without a second fixture file: `"trim"` strips
every exported value; `"drop_key"` silently skips exporting one named id.
`unlisted_extra_file` exists so tests can drive a `collect` that reads a
file beyond the ones it declares: when set and that file exists, one extra
message is added whose text is the file's content, but the file's path is
never added to `files` -- so a `collect` staged with only the declared
`files` cannot see it and the extra message is silently absent there.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

EXIT_OK, EXIT_FAIL, EXIT_CANNOT = 0, 1, 2

PLURAL_SEP = " | "
COUNT_ARGUMENTS = ["count"]

_STRUCT_RE = re.compile(r"@:[A-Za-z0-9_.]+")


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def fail(message: str, code: int, **fields) -> NoReturn:
    payload = {"ok": False, "error": message}
    payload.update(fields)
    emit(payload)
    sys.exit(code)


class _FailingArgumentParser(argparse.ArgumentParser):
    """Like the plugin's own parser: a bad invocation still prints one JSON
    line before exiting, instead of argparse's bare usage text on stderr."""

    def error(self, message: str) -> NoReturn:
        fail(message, EXIT_CANNOT)


def make_parser() -> argparse.ArgumentParser:
    parser = _FailingArgumentParser(prog="toy_adapter.py")
    sub = parser.add_subparsers(dest="command", required=True)

    collect_p = sub.add_parser("collect")
    collect_p.add_argument("--options", required=True)
    collect_p.add_argument("--source-locale", required=True)
    collect_p.add_argument("--target-locales", required=True)
    collect_p.add_argument("--out", required=True)

    export_p = sub.add_parser("export")
    export_p.add_argument("--options", required=True)
    export_p.add_argument("--locale", required=True)
    export_p.add_argument("--values", required=True)

    parse_p = sub.add_parser("parse")
    parse_p.add_argument("--options", required=True)
    parse_p.add_argument("--in", dest="in_path", required=True)

    return parser


def read_json_file(path: Path, what: str):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"{what} is unreadable: {path} ({exc})", EXIT_CANNOT)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"{what} is invalid JSON: {path} ({exc})", EXIT_CANNOT)


def write_json_file(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def parse_text(text: str):
    """Tokenize `text`. Returns `(tokens, None)` on success, or `(None,
    error)` when a raw '{', '}' or '@' is not part of a recognized token."""
    tokens: list[dict] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            end = text.find("}", i + 1)
            if end == -1:
                return None, f"unbalanced '{{' at position {i}"
            inner = text[i + 1:end]
            if not inner or "{" in inner:
                return None, f"empty or nested argument at position {i}"
            tokens.append({"kind": "argument", "name": inner, "signature": text[i:end + 1]})
            i = end + 1
        elif c == "}":
            return None, f"unexpected '}}' at position {i}"
        elif c == "@":
            m = _STRUCT_RE.match(text, i)
            if not m:
                return None, f"unexpected '@' at position {i}"
            tokens.append({"kind": "structure", "text": m.group(0)})
            i = m.end()
        else:
            i += 1
    return tokens, None


def split_plural(value: str) -> tuple[list[str], bool]:
    """A value containing the plural separator becomes its forms; anything
    else is a single-form (non-plural) value."""
    if PLURAL_SEP in value:
        return value.split(PLURAL_SEP), True
    return [value], False


def _locale_path(locale: str) -> Path:
    return Path("locales") / f"{locale}.json"


def cmd_collect(args) -> int:
    options = read_json_file(Path(args.options), "options")
    plural_labels = options.get("plural_labels", {})
    general = options.get("general", {})

    source_locale = args.source_locale
    target_locales = [t for t in args.target_locales.split(",") if t]

    source_path = _locale_path(source_locale)
    if not source_path.is_file():
        fail(f"source locale file not found: {source_path}", EXIT_CANNOT)
    source_obj = read_json_file(source_path, "source locale file")
    if not isinstance(source_obj, dict):
        fail(f"source locale file is not a flat object: {source_path}", EXIT_CANNOT)

    target_objs: dict[str, dict] = {}
    for locale in target_locales:
        p = _locale_path(locale)
        if p.is_file():
            obj = read_json_file(p, f"{locale} locale file")
            if not isinstance(obj, dict):
                fail(f"{locale} locale file is not a flat object: {p}", EXIT_CANNOT)
            target_objs[locale] = obj
        else:
            target_objs[locale] = {}

    files = [str(source_path)] + [str(_locale_path(locale)) for locale in target_locales]

    messages = []
    for key, source_value in source_obj.items():
        if not isinstance(source_value, str):
            fail(f"key {key!r} in {source_path} is not a string", EXIT_CANNOT)
        source_forms, is_plural = split_plural(source_value)

        message: dict = {
            "id": key,
            "context": {"file": str(source_path), "key": key, "max_length": None, "comment": None},
        }

        if is_plural:
            n = str(len(source_forms))
            source_label_pairs = plural_labels.get(source_locale, {}).get(n)
            if source_label_pairs is None:
                fail(
                    f"no plural labels for locale {source_locale!r} with {n} source forms",
                    EXIT_FAIL,
                    id=key,
                )
            general_index = general.get(n)
            if not isinstance(general_index, int) or not (0 <= general_index < len(source_forms)):
                fail(f"no valid general index for {n} source forms", EXIT_FAIL, id=key)

            target_labels = {}
            for locale in target_locales:
                pairs = plural_labels.get(locale, {}).get(n)
                if pairs is None:
                    fail(
                        f"no plural labels for locale {locale!r} with {n} source forms",
                        EXIT_FAIL,
                        id=key,
                    )
                target_labels[locale] = [{"label": lbl, "exact": bool(ex)} for lbl, ex in pairs]

            message["source"] = {"forms": source_forms}
            message["plural"] = {
                "source_labels": [{"label": lbl, "exact": bool(ex)} for lbl, ex in source_label_pairs],
                "target_labels": target_labels,
                "count_arguments": list(COUNT_ARGUMENTS),
                "general_index": general_index,
            }
        else:
            message["source"] = source_forms[0]

        targets = {}
        for locale in target_locales:
            raw = target_objs[locale].get(key)
            if raw is None:
                targets[locale] = None
                continue
            if not isinstance(raw, str):
                fail(f"key {key!r} in locale {locale!r} is not a string", EXIT_CANNOT)
            if is_plural:
                targets[locale] = {"forms": raw.split(PLURAL_SEP)}
            else:
                targets[locale] = raw
        message["targets"] = targets
        messages.append(message)

    extra_rel = options.get("unlisted_extra_file")
    if extra_rel:
        extra_path = Path(extra_rel)
        if extra_path.is_file():
            # Deliberately NOT added to `files`: a collect staged with only the
            # declared files cannot see this, so it silently loses this message --
            # the defect adapter_check.py's staging-consistency check exists to catch.
            messages.append({
                "id": "unlisted.extra",
                "source": extra_path.read_text(encoding="utf-8").strip(),
                "context": {"file": extra_rel, "key": "unlisted.extra", "max_length": None, "comment": None},
                "targets": {locale: None for locale in target_locales},
            })

    write_json_file(Path(args.out), {"schema": 1, "files": files, "messages": messages})
    emit({"ok": True, "count": len(messages)})
    return EXIT_OK


def cmd_export(args) -> int:
    options = read_json_file(Path(args.options), "options")
    lossy = options.get("lossy")
    lossy_drop_key = options.get("lossy_drop_key")

    payload = read_json_file(Path(args.values), "values file")
    values = payload.get("values")
    if not isinstance(values, dict):
        fail("values file is missing a 'values' object", EXIT_CANNOT)

    path = _locale_path(args.locale)
    if path.is_file():
        obj = read_json_file(path, f"{args.locale} locale file")
        if not isinstance(obj, dict):
            fail(f"{path} is not a flat object", EXIT_CANNOT)
    else:
        obj = {}

    changed = []
    for key, value in values.items():
        if isinstance(value, dict):
            forms = value.get("forms")
            if not isinstance(forms, list) or not all(isinstance(f, str) for f in forms):
                fail(f"value for key {key!r} has a malformed forms list", EXIT_CANNOT)
            text = PLURAL_SEP.join(forms)
        elif isinstance(value, str):
            text = value
        else:
            fail(f"value for key {key!r} is neither a string nor a forms object", EXIT_CANNOT)

        if lossy == "trim":
            text = text.strip()
        if lossy == "drop_key" and key == lossy_drop_key:
            continue

        obj[key] = text
        changed.append(key)

    write_json_file(path, obj)
    emit({"ok": True, "locale": args.locale, "changed": sorted(changed)})
    return EXIT_OK


def cmd_parse(args) -> int:
    read_json_file(Path(args.options), "options")  # validated for shape parity with other commands
    payload = read_json_file(Path(args.in_path), "parse input")
    items = payload.get("items")
    if not isinstance(items, list):
        fail("parse input is missing an 'items' list", EXIT_CANNOT)

    results = {}
    for item in items:
        if not isinstance(item, dict):
            fail("a parse item is not an object", EXIT_CANNOT)
        key = item.get("key")
        text = item.get("text")
        if not isinstance(key, str) or not isinstance(text, str):
            fail("a parse item is missing its 'key' or 'text'", EXIT_CANNOT)
        tokens, error = parse_text(text)
        results[key] = {"ok": True, "tokens": tokens} if tokens is not None else {"ok": False, "error": error}

    emit({"ok": True, "results": results})
    return EXIT_OK


def main() -> int:
    args = make_parser().parse_args()
    if args.command == "collect":
        return cmd_collect(args)
    if args.command == "export":
        return cmd_export(args)
    if args.command == "parse":
        return cmd_parse(args)
    fail(f"unknown command: {args.command}", EXIT_CANNOT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # top-level safety net: never a raw traceback
        fail(str(exc), EXIT_CANNOT)
