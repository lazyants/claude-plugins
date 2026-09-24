"""Shared library for software-localizer scripts.

Every other script in this plugin imports exactly the names defined here.
This module must stay importable with no side effects beyond reading the
process environment: no file writes, no network, no work at import time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NoReturn

EXIT_OK, EXIT_FAIL, EXIT_CANNOT = 0, 1, 2

CHOOSE_PREFIX = "CHOOSE_"

# The directory the driving session writes a project-built adapter into
# (plan section 2); `adapter_digest` hashes this whole tree when it holds
# anything, and only falls back to hashing an outside-R script file named
# by `adapter.argv` when it does not (plan section 6).
ADAPTER_DIR_NAME = "adapter"


def emit(obj: dict) -> None:
    """Write exactly one JSON line to stdout and flush it."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def fail(message: str, code: int = EXIT_FAIL, **fields) -> NoReturn:
    """Report a failure on both channels and exit.

    stderr gets the human message; stdout gets exactly one JSON line
    (``{"ok": false, "error": message, **fields}``); the process exits with
    `code`. Never returns.
    """
    print(message, file=sys.stderr)
    payload = {"ok": False, "error": message}
    payload.update(fields)
    emit(payload)
    sys.exit(code)


def run_main(main: Callable[[], int]) -> NoReturn:
    """Top-level wrapper every script's ``if __name__ == "__main__"`` calls.

    Runs `main`, which must return an exit code. Any exception other than
    `SystemExit`/`KeyboardInterrupt` is caught here and converted to
    ``fail(str(exc), EXIT_CANNOT)`` so a script never lets a raw traceback
    reach the user.
    """
    try:
        code = main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # top-level safety net, by design
        fail(str(exc), EXIT_CANNOT)
    else:
        sys.exit(code)


class _FailingArgumentParser(argparse.ArgumentParser):
    """An `ArgumentParser` whose `error()` follows the plugin's failure
    contract (one stdout JSON line, human detail on stderr, exit
    `EXIT_CANNOT`) instead of argparse's default (usage text on stderr,
    a bare `sys.exit(2)` with no JSON line at all)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        fail(message, EXIT_CANNOT)


def make_parser(prog: str, description: str = None) -> argparse.ArgumentParser:
    """Build the top-level parser for a script, so a bad CLI argument still
    emits exactly one JSON line before exiting.

    `add_subparsers()` defaults its `parser_class` to `type(self)`, so every
    subcommand parser created from the result of this function inherits the
    same failure behaviour with no further wiring needed."""
    return _FailingArgumentParser(prog=prog, description=description)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj) -> str:
    """The canonical hash of any JSON-serializable value: sorted keys, tight
    separators, and non-ASCII characters kept literal (not `\\uXXXX`-escaped)
    so the same value hashes the same way regardless of what serialized it."""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_bytes(text.encode("utf-8"))


def value_sha256(value) -> str:
    """The hash of a message VALUE: a plain string, or `{"forms": [...]}`.
    Ledger candidates and review verdicts are bound to a value by this hash
    (plan sections 7 and 10), so a candidate and the verdict cast on it can
    be matched by content, not by object identity."""
    is_string = isinstance(value, str)
    is_forms = isinstance(value, dict) and isinstance(value.get("forms"), list)
    if not (is_string or is_forms):
        raise TypeError(f"value_sha256 expects a string or {{'forms': [...]}}, got {value!r}")
    return sha256_json(value)


def atomic_write_text(path, text: str) -> None:
    """Write `text` to `path` atomically: tmp file in the same directory,
    flush, fsync, then `os.replace`. Leaves no temp file behind, on success
    or on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".lz-tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_text(path, text)


def read_json(path, what: str):
    """Read and parse one JSON file, or `fail(EXIT_CANNOT)` naming `what`."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"{what} is missing: {path}", EXIT_CANNOT)
    except OSError as exc:
        fail(f"{what} is unreadable: {path} ({exc})", EXIT_CANNOT)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"{what} is invalid JSON: {path} ({exc})", EXIT_CANNOT)


def resolve_root(root_arg) -> Path:
    """Resolve `root_arg` to its realpath. `fail(EXIT_CANNOT)` if it is not
    an existing directory. `scaffold.py` does its own resolution instead of
    this, since a fresh root need not exist yet."""
    p = Path(root_arg).resolve()
    if not p.is_dir():
        fail(f"root is not a directory: {root_arg}", EXIT_CANNOT)
    return p


def now_iso() -> str:
    """UTC, seconds precision, trailing `Z` (no microseconds, no `+00:00`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# localize.json (plan section 3)
# ---------------------------------------------------------------------------


def _is_choose(value) -> bool:
    return isinstance(value, str) and value.startswith(CHOOSE_PREFIX)


def _find_choose_sentinels(value, path: str, problems: list) -> None:
    """Recursively flag every string leaf that still carries a `CHOOSE_`
    placeholder, wherever it sits in the config tree."""
    if isinstance(value, str):
        if value.startswith(CHOOSE_PREFIX):
            problems.append({"field": path or "<root>", "message": f"still has a placeholder value: {value}"})
    elif isinstance(value, dict):
        for key in sorted(value):
            _find_choose_sentinels(value[key], f"{path}.{key}" if path else key, problems)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _find_choose_sentinels(item, f"{path}[{i}]", problems)


def _resolve_maybe_relative(root: Path, value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = root / p
    return p


def validate_config(cfg, root) -> list:
    """Every problem with `cfg` (`localize.json`'s already-parsed content),
    per plan section 3. Never raises or exits: reports every problem found,
    at once, as `[{"field", "message"}]`; an empty list means `cfg` is
    ready to use."""
    root = Path(root)
    problems: list = []

    if not isinstance(cfg, dict):
        return [{"field": "<root>", "message": "localize.json is not a JSON object"}]

    _find_choose_sentinels(cfg, "", problems)

    if cfg.get("schema") != 1:
        problems.append({"field": "schema", "message": "schema must be 1"})

    source_locale = cfg.get("source_locale")
    source_ok = isinstance(source_locale, str) and bool(source_locale)
    if not source_ok:
        problems.append({"field": "source_locale", "message": "source_locale must be a non-empty string"})

    target_locales = cfg.get("target_locales")
    target_set: set = set()
    if not _is_choose(target_locales):
        if not isinstance(target_locales, list) or not target_locales:
            problems.append({"field": "target_locales", "message": "target_locales must be a non-empty list of strings"})
        else:
            seen: set = set()
            for i, loc in enumerate(target_locales):
                if not isinstance(loc, str) or not loc:
                    problems.append({"field": f"target_locales[{i}]", "message": "must be a non-empty string"})
                    continue
                if loc in seen:
                    problems.append({"field": f"target_locales[{i}]", "message": f"duplicate target locale: {loc}"})
                    continue
                seen.add(loc)
                target_set.add(loc)
            if source_ok and source_locale in target_set:
                problems.append({"field": "target_locales", "message": "source_locale must not also be a target locale"})

    # project_root (plan section 2): a relative value resolves against R,
    # the same convention `adapter.argv` uses, and must not equal, sit
    # inside, or contain R.
    project_root = cfg.get("project_root")
    if not _is_choose(project_root):
        if not isinstance(project_root, str) or not project_root:
            problems.append({"field": "project_root", "message": "project_root must be a non-empty string"})
        else:
            p = _resolve_maybe_relative(root, project_root)
            try:
                p_resolved = p.resolve()
            except OSError:
                p_resolved = p
            if not p_resolved.is_dir():
                problems.append({"field": "project_root", "message": f"project_root does not exist: {project_root}"})
            else:
                root_resolved = root.resolve()
                overlap = p_resolved == root_resolved
                if not overlap:
                    try:
                        p_resolved.relative_to(root_resolved)
                        overlap = True
                    except ValueError:
                        pass
                if not overlap:
                    try:
                        root_resolved.relative_to(p_resolved)
                        overlap = True
                    except ValueError:
                        pass
                if overlap:
                    problems.append({
                        "field": "project_root",
                        "message": "project_root must not equal, sit inside, or contain the workspace root",
                    })

    # style: exactly one entry per target locale, each with a formality.
    style = cfg.get("style")
    if not _is_choose(style):
        if not isinstance(style, dict):
            problems.append({"field": "style", "message": "style must be an object"})
        elif target_set:
            style_keys = {k for k in style if isinstance(k, str)}
            for loc in sorted(target_set - style_keys):
                problems.append({"field": f"style.{loc}", "message": "missing a style entry for this target locale"})
            for loc in sorted(style_keys - target_set):
                problems.append({"field": f"style.{loc}", "message": "style entry for a locale that is not a target"})
            for loc in sorted(style_keys & target_set):
                entry = style[loc]
                if not isinstance(entry, dict):
                    problems.append({"field": f"style.{loc}", "message": "style entry must be an object"})
                    continue
                formality = entry.get("formality")
                if not isinstance(formality, str) or not formality:
                    problems.append({"field": f"style.{loc}.formality", "message": "formality must be a non-empty string"})

    # adapter.argv: a non-empty list of strings whose first element resolves
    # (on PATH or as a file, relative paths resolved against R).
    adapter = cfg.get("adapter")
    if not _is_choose(adapter):
        if not isinstance(adapter, dict):
            problems.append({"field": "adapter", "message": "adapter must be an object"})
        else:
            argv = adapter.get("argv")
            if not _is_choose(argv):
                if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                    problems.append({"field": "adapter.argv", "message": "must be a non-empty list of non-empty strings"})
                else:
                    exe = argv[0]
                    resolved = shutil.which(exe) is not None
                    if not resolved:
                        resolved = _resolve_maybe_relative(root, exe).is_file()
                    if not resolved:
                        problems.append({
                            "field": "adapter.argv[0]",
                            "message": f"does not resolve on PATH or as a file: {exe}",
                        })
            options = adapter.get("options", {})
            if not isinstance(options, dict):
                problems.append({"field": "adapter.options", "message": "adapter.options must be an object"})

    # batch_size / max_rounds / adapter_timeout_s: optional (consumers fall
    # back to a default when absent); when present, positive integers.
    for field in ("batch_size", "max_rounds", "adapter_timeout_s"):
        value = cfg.get(field)
        if value is None or _is_choose(value):
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append({"field": field, "message": f"{field} must be a positive integer"})

    # allow_identical: optional; when present, a list of strings.
    allow_identical = cfg.get("allow_identical")
    if allow_identical is not None and not _is_choose(allow_identical):
        if not isinstance(allow_identical, list) or not all(isinstance(a, str) for a in allow_identical):
            problems.append({"field": "allow_identical", "message": "allow_identical must be a list of strings"})

    return problems


def load_config(root) -> dict:
    """Read `root/localize.json` and validate it. A missing file, invalid
    JSON, or any validation problem is `fail(EXIT_CANNOT)`: from a
    downstream script's point of view an unready config is a missing
    dependency, not its own judgment to make. `config_validate.py` is the
    one script that reports these problems as its own verdict rather than
    a hard failure.

    `project_root` comes back resolved to an absolute path (a relative value
    resolves against `root`, the same convention `validate_config` already
    checked it against) -- every other script reads `cfg["project_root"]`
    straight, with no resolution of its own, so it targets the same
    directory regardless of the process's current working directory."""
    root = Path(root)
    cfg = read_json(root / "localize.json", "localize.json")
    problems = validate_config(cfg, root)
    if problems:
        detail = "; ".join(f"{p['field']}: {p['message']}" for p in problems)
        fail(f"localize.json has problems: {detail}", EXIT_CANNOT, problems=problems)
    cfg["project_root"] = str(_resolve_maybe_relative(root, cfg["project_root"]).resolve())
    return cfg


# ---------------------------------------------------------------------------
# messages.json (plan section 4)
# ---------------------------------------------------------------------------


def _label_shape_ok(label) -> bool:
    return (
        isinstance(label, dict)
        and isinstance(label.get("label"), str)
        and bool(label["label"])
        and isinstance(label.get("exact"), bool)
    )


def messages_shape_problem(data):
    """The first structural problem with an already-parsed `messages.json`,
    or `None` when its shape is valid (plan section 4): types, required
    keys, unique ids, non-empty label lists, and every target's form count
    matching its label count. Content judgments (script checks, review) are
    a different layer and are not this function's job."""
    if not isinstance(data, dict):
        return "top level is not an object"
    if data.get("schema") != 1:
        return "schema must be 1"

    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return "files must be a list of strings"

    messages = data.get("messages")
    if not isinstance(messages, list):
        return "messages must be a list"

    seen_ids: set = set()
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return f"messages[{i}] is not an object"
        mid = msg.get("id")
        if not isinstance(mid, str) or not mid:
            return f"messages[{i}].id must be a non-empty string"
        if mid in seen_ids:
            return f"messages[{i}].id is a duplicate: {mid}"
        seen_ids.add(mid)

        tag = f"message {mid!r}"
        plural = msg.get("plural")
        source = msg.get("source")
        targets = msg.get("targets")

        if plural is None:
            if not isinstance(source, str):
                return f"{tag}.source must be a string for a non-plural message"
            if not isinstance(targets, dict):
                return f"{tag}.targets must be an object"
            for locale, value in targets.items():
                if value is not None and not isinstance(value, str):
                    return f"{tag}.targets[{locale!r}] must be a string or null"
        else:
            problem = _plural_shape_problem(tag, plural, source, targets)
            if problem:
                return problem

        context = msg.get("context")
        if not isinstance(context, dict):
            return f"{tag}.context must be an object"
        if not isinstance(context.get("file"), str):
            return f"{tag}.context.file must be a string"
        if not isinstance(context.get("key"), str):
            return f"{tag}.context.key must be a string"
        max_length = context.get("max_length")
        if max_length is not None and (
            not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 0
        ):
            return f"{tag}.context.max_length must be a non-negative integer or null"
        comment = context.get("comment")
        if comment is not None and not isinstance(comment, str):
            return f"{tag}.context.comment must be a string or null"

    return None


def _plural_shape_problem(tag: str, plural, source, targets):
    if not isinstance(plural, dict):
        return f"{tag}.plural must be an object"

    source_forms_ok = (
        isinstance(source, dict)
        and isinstance(source.get("forms"), list)
        and bool(source["forms"])
        and all(isinstance(f, str) for f in source["forms"])
    )
    if not source_forms_ok:
        return f"{tag}.source must be {{'forms': [...]}} with at least one string, for a plural message"
    n_source_forms = len(source["forms"])

    source_labels = plural.get("source_labels")
    if not isinstance(source_labels, list) or not source_labels or not all(_label_shape_ok(l) for l in source_labels):
        return f"{tag}.plural.source_labels must be a non-empty list of {{'label', 'exact'}}"
    if len(source_labels) != n_source_forms:
        return f"{tag}.plural.source_labels length must equal source.forms length"

    target_labels = plural.get("target_labels")
    if not isinstance(target_labels, dict):
        return f"{tag}.plural.target_labels must be an object"
    for locale, labels in target_labels.items():
        if not isinstance(labels, list) or not labels or not all(_label_shape_ok(l) for l in labels):
            return f"{tag}.plural.target_labels[{locale!r}] must be a non-empty list of {{'label', 'exact'}}"

    count_arguments = plural.get("count_arguments")
    if not isinstance(count_arguments, list) or not count_arguments or not all(
        isinstance(a, str) and a for a in count_arguments
    ):
        return f"{tag}.plural.count_arguments must be a non-empty list of non-empty strings"

    general_index = plural.get("general_index")
    if (
        not isinstance(general_index, int)
        or isinstance(general_index, bool)
        or not (0 <= general_index < n_source_forms)
    ):
        return f"{tag}.plural.general_index must index a source form"

    if not isinstance(targets, dict):
        return f"{tag}.targets must be an object"
    for locale, value in targets.items():
        if value is None:
            continue
        forms_ok = isinstance(value, dict) and isinstance(value.get("forms"), list) and all(
            isinstance(f, str) for f in value["forms"]
        )
        if not forms_ok:
            return f"{tag}.targets[{locale!r}] must be {{'forms': [...]}} or null"
        expected_labels = target_labels.get(locale)
        if expected_labels is not None and len(value["forms"]) != len(expected_labels):
            return f"{tag}.targets[{locale!r}].forms length must equal plural.target_labels[{locale!r}] length"

    return None


def load_messages(path) -> dict:
    """Read and shape-validate `messages.json` (plan section 4). The only
    reader: every script that needs message data goes through this."""
    data = read_json(path, "messages.json")
    problem = messages_shape_problem(data)
    if problem:
        fail(f"messages.json is invalid: {problem}", EXIT_CANNOT)
    return data


# ---------------------------------------------------------------------------
# Adapter acceptance (plan section 6)
# ---------------------------------------------------------------------------


def _tree_digests(base: Path, exclude_dirs=(".git", "__pycache__")) -> dict:
    """`relpath (posix) -> sha256` for every file under `base`, sorted walk.
    A symlink (file or directory) is recorded as `"symlink:<target>"` and,
    for a directory, is not descended into."""
    base = Path(base)
    result: dict = {}
    if not base.exists():
        return result
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        keep_dirs = []
        for d in sorted(dirnames):
            if d in exclude_dirs:
                continue
            full_d = Path(dirpath) / d
            if full_d.is_symlink():
                rel = full_d.relative_to(base).as_posix()
                result[rel] = f"symlink:{os.readlink(full_d)}"
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(base).as_posix()
            if full.is_symlink():
                result[rel] = f"symlink:{os.readlink(full)}"
            else:
                result[rel] = sha256_file(full)
    return result


def _adapter_argv_files(root: Path, cfg: dict) -> list:
    """Every element of `adapter.argv` that resolves to an existing file, as
    opposed to a bare command found on `PATH` (`"node"`, `"python3"`).
    Typically this is exactly the one script element of an
    `[interpreter, script]` pair."""
    argv = cfg["adapter"]["argv"]
    files = []
    for item in argv:
        p = _resolve_maybe_relative(root, item)
        if p.is_file():
            files.append(p)
    return files


def adapter_digest(root, cfg: dict) -> dict:
    """`{"files": {path: sha256}, "argv": [...], "options_sha256": ...}`
    identifying what the adapter actually executes (plan section 6):
    `adapter.argv` itself, so a changed argument or a swapped script is
    caught even when its byte content happens to match; the sha256 of every
    argv element that resolves to an existing file, relative to `root` or
    absolute -- typically the interpreter's script argument, wherever it
    lives, inside `root` or out; and, when `root/adapter/` holds anything,
    every file under it too (the normal case: the driving session places
    the adapter there). The two file sources are additive, not either/or:
    a self-contained `[interpreter, script]` adapter with nothing under
    `root/adapter/` is covered by the argv-file hash alone; one with both a
    populated `root/adapter/` and an argv script outside it is covered by
    both. An argv element that resolves inside `root/adapter/` is not hashed
    twice: it is already part of that tree's digest."""
    root = Path(root)
    adapter_dir = root / ADAPTER_DIR_NAME
    argv = list(cfg["adapter"]["argv"])
    files: dict = {}

    has_dir_contents = adapter_dir.is_dir() and any(adapter_dir.iterdir())
    if has_dir_contents:
        for rel, digest in sorted(_tree_digests(adapter_dir).items()):
            files[f"{ADAPTER_DIR_NAME}/{rel}"] = digest

    argv_files = _adapter_argv_files(root, cfg)
    if not has_dir_contents and not argv_files:
        fail(
            f"no adapter file found: {ADAPTER_DIR_NAME}/ is empty and adapter.argv names no existing file",
            EXIT_CANNOT,
        )
    adapter_dir_resolved = adapter_dir.resolve() if has_dir_contents else None
    for f in argv_files:
        f_resolved = f.resolve()
        if adapter_dir_resolved is not None:
            try:
                f_resolved.relative_to(adapter_dir_resolved)
                continue  # already hashed above, as part of the adapter/ tree
            except ValueError:
                pass
        files[str(f_resolved)] = sha256_file(f)

    options = cfg.get("adapter", {}).get("options", {})
    return {"files": files, "argv": argv, "options_sha256": sha256_json(options)}


def require_accepted_adapter(root, cfg: dict) -> None:
    """`fail(EXIT_FAIL)` unless `R/adapter.lock.json` matches the adapter's
    current files, argv and options (`adapter_digest`). Called by
    `collect.py`, `export_values.py` and `packets.py` before any of them run
    the adapter or rely on its accepted state."""
    root = Path(root)
    lock_path = root / "adapter.lock.json"
    lock = read_json(lock_path, "adapter.lock.json") if lock_path.is_file() else {}
    current = adapter_digest(root, cfg)
    if (
        lock.get("files") != current["files"]
        or lock.get("argv") != current["argv"]
        or lock.get("options_sha256") != current["options_sha256"]
    ):
        fail(
            "the adapter is not accepted for its current files and options: run adapter_check.py accept",
            EXIT_FAIL,
        )


def stage_files(project_dir, files, dest) -> None:
    """Copy exactly the adapter's listed `files` (relative to `project_dir`) into `dest`,
    keeping their relative paths. Real projects carry dependency and data directories far
    larger than their catalogs, so the core never copies a whole project: an adapter's
    `collect` and `export` must work in a directory holding only the files it listed.
    Refuses (EXIT_CANNOT) an absolute path, a path escaping the project, a symlink, or a
    listed file that is missing or not a regular file."""
    project_dir = Path(project_dir).resolve()
    dest = Path(dest)
    for rel in files:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            fail(f"adapter file path must be relative and inside the project: {rel}", EXIT_CANNOT)
        source = project_dir / rel_path
        if source.is_symlink() or not source.is_file():
            fail(f"adapter file is missing or not a regular file: {rel}", EXIT_CANNOT)
        if project_dir not in source.resolve().parents:
            fail(f"adapter file resolves outside the project: {rel}", EXIT_CANNOT)
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
