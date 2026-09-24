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
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NoReturn

EXIT_OK, EXIT_FAIL, EXIT_CANNOT = 0, 1, 2

CHOOSE_PREFIX = "CHOOSE_"

# `adapter.code_dir`'s default. See references/adapter-contract.md for the
# full contract: ALL adapter code lives in ONE directory, hashed as a whole
# by `adapter_digest`, so editing any file there -- a helper `adapter.argv`
# never names directly -- always invalidates acceptance. A relative
# `code_dir` resolves against the workspace root `R`; an absolute one may
# sit anywhere, including inside the project.
ADAPTER_DIR_NAME = "adapter"

# `adapter_check.py run`'s result, relative to `R`. Read by `adapter_check.py
# accept` and reported on (never written) by `status.py`.
ADAPTER_CHECK_RESULT = "runs/_adapter_check.json"

# `load_config` fills each of these in when `localize.json` leaves it absent
# or `null`; `scaffold.py` seeds a fresh `localize.json` with the same
# values. Lists are copied at each use site, never shared.
CONFIG_DEFAULTS = {"batch_size": 40, "max_rounds": 3, "adapter_timeout_s": 300, "allow_identical": []}


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


def make_parser(prog: str, description: str | None = None) -> argparse.ArgumentParser:
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
    (see references/state.md), so a candidate and the verdict cast on it can
    be matched by content, not by object identity."""
    is_string = isinstance(value, str)
    is_forms = isinstance(value, dict) and isinstance(value.get("forms"), list)
    if not (is_string or is_forms):
        raise TypeError(f"value_sha256 expects a string or {{'forms': [...]}}, got {value!r}")
    return sha256_json(value)


def is_forms_value(v) -> bool:
    """`True` for the plural message-value shape, `{"forms": [...]}` where
    every form is a string; `False` otherwise."""
    return isinstance(v, dict) and isinstance(v.get("forms"), list) and all(isinstance(f, str) for f in v["forms"])


def atomic_write_bytes(path, data: bytes) -> None:
    """Write `data` to `path` atomically: tmp file in the same directory,
    flush, fsync, then `os.replace`. Leaves no temp file behind, on success
    or on failure. Preserves an existing destination's permission bits
    (`os.stat(dest).st_mode & 0o7777`, applied to the temp file before the
    replace); a new file gets the umask default (`0o666 & ~umask`), the same
    as a plain `open()` for writing -- not `mkstemp`'s own, more
    restrictive, `0o600`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".lz-tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            mode = os.stat(path).st_mode & 0o7777
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path, text: str) -> None:
    """`atomic_write_bytes`, encoding `text` as UTF-8."""
    atomic_write_bytes(path, text.encode("utf-8"))


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
# localize.json
# ---------------------------------------------------------------------------


def _is_choose(value) -> bool:
    return isinstance(value, str) and value.startswith(CHOOSE_PREFIX)


def _find_choose_sentinels(value, path: str, problems: list) -> None:
    """Recursively flag every string leaf that still carries a `CHOOSE_`
    placeholder, wherever it sits in the config tree."""
    if _is_choose(value):
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


# A locale identifier is used to build output paths (ledger.py, export
# targets): it must never contain a path separator or a ".." segment, so it
# cannot escape the tree it is joined into.
_LOCALE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _locale_ok(value) -> bool:
    return isinstance(value, str) and _LOCALE_RE.match(value) is not None


def validate_config(cfg, root) -> list:
    """Every problem with `cfg` (`localize.json`'s already-parsed content).
    Never raises or exits: reports every problem found, at once, as
    `[{"field", "message"}]`; an empty list means `cfg` is ready to use."""
    root = Path(root)
    problems: list = []

    if not isinstance(cfg, dict):
        return [{"field": "<root>", "message": "localize.json is not a JSON object"}]

    _find_choose_sentinels(cfg, "", problems)

    if cfg.get("schema") != 1:
        problems.append({"field": "schema", "message": "schema must be 1"})

    source_locale = cfg.get("source_locale")
    source_ok = _locale_ok(source_locale)
    if not source_ok:
        problems.append({
            "field": "source_locale",
            "message": "source_locale must be a path-safe locale identifier: letters, digits, '_', '-', not starting with '_' or '-'",
        })

    target_locales = cfg.get("target_locales")
    target_set: set = set()
    if not _is_choose(target_locales):
        if not isinstance(target_locales, list) or not target_locales:
            problems.append({"field": "target_locales", "message": "target_locales must be a non-empty list of strings"})
        else:
            seen: set = set()
            for i, loc in enumerate(target_locales):
                if not _locale_ok(loc):
                    problems.append({
                        "field": f"target_locales[{i}]",
                        "message": "must be a path-safe locale identifier: letters, digits, '_', '-', not starting with '_' or '-'",
                    })
                    continue
                if loc in seen:
                    problems.append({"field": f"target_locales[{i}]", "message": f"duplicate target locale: {loc}"})
                    continue
                seen.add(loc)
                target_set.add(loc)
            if source_ok and source_locale in target_set:
                problems.append({"field": "target_locales", "message": "source_locale must not also be a target locale"})

    # project_root: a relative value resolves against R, the same convention
    # `adapter.argv` uses, and must not equal, sit inside, or contain R.
    project_root = cfg.get("project_root")
    if not _is_choose(project_root):
        if not isinstance(project_root, str) or not project_root:
            problems.append({"field": "project_root", "message": "project_root must be a non-empty string"})
        else:
            p = _resolve_maybe_relative(root, project_root)
            p_resolved = p.resolve()
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

    # adapter.code_dir / adapter.argv: ALL adapter code lives in one
    # directory, `code_dir` (default `ADAPTER_DIR_NAME`,
    # resolved the same way as `project_root` -- relative against `root`,
    # absolute used as is); it must already exist. `argv` is a non-empty
    # list of strings whose first element resolves (on PATH or as a file,
    # relative paths resolved against R). `argv[0]` -- the interpreter, or
    # the adapter executable itself -- is exempt from the "must sit inside
    # code_dir" rule below (an absolute interpreter, e.g. a test's
    # `sys.executable`, is not adapter code); `adapter_digest` still hashes
    # it when it resolves to a file, so a change to it (an interpreter
    # upgrade, say) invalidates acceptance anyway. Every OTHER element
    # (`argv[1:]`) that resolves to a file -- absolute or R-relative --
    # must sit inside `code_dir`, or editing it would never invalidate
    # acceptance.
    adapter = cfg.get("adapter")
    if not _is_choose(adapter):
        if not isinstance(adapter, dict):
            problems.append({"field": "adapter", "message": "adapter must be an object"})
        else:
            code_dir_value = adapter.get("code_dir", ADAPTER_DIR_NAME)
            code_dir_resolved = None
            if _is_choose(code_dir_value):
                pass  # already flagged by _find_choose_sentinels
            elif not isinstance(code_dir_value, str) or not code_dir_value:
                problems.append({"field": "adapter.code_dir", "message": "adapter.code_dir must be a non-empty string"})
            else:
                candidate = _resolve_maybe_relative(root, code_dir_value).resolve()
                if not candidate.is_dir():
                    problems.append({
                        "field": "adapter.code_dir",
                        "message": f"adapter.code_dir does not exist or is not a directory: {code_dir_value}",
                    })
                else:
                    code_dir_resolved = candidate

            argv = adapter.get("argv")
            if not _is_choose(argv):
                if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                    problems.append({"field": "adapter.argv", "message": "must be a non-empty list of non-empty strings"})
                else:
                    # Every element is checked the same way `resolve_argv` resolves it
                    # (an absolute path, or a relative path naming a file under `root`,
                    # is fine); a relative element that LOOKS like a path (it has a
                    # separator) but is not under `root` is refused here, before it can
                    # silently run against the project's own cwd instead -- unresolved
                    # and unhashed by `adapter_digest`. A relative element with no
                    # separator (a bare command name, a flag) is only checked at
                    # index 0, where it must resolve on PATH.
                    for i, item in enumerate(argv):
                        resolved_item = Path(item) if os.path.isabs(item) else root / item
                        is_file = resolved_item.is_file()

                        if not is_file:
                            if os.path.isabs(item):
                                continue  # an absolute non-file element (e.g. a flag): accepted as before
                            has_sep = "/" in item or (os.sep != "/" and os.sep in item)
                            if has_sep:
                                problems.append({
                                    "field": f"adapter.argv[{i}]",
                                    "message": (
                                        "relative adapter paths resolve against the workspace; "
                                        "use an absolute path for a script elsewhere"
                                    ),
                                })
                            elif i == 0 and shutil.which(item) is None:
                                problems.append({
                                    "field": "adapter.argv[0]",
                                    "message": f"does not resolve on PATH or as a file: {item}",
                                })
                            continue

                        if i == 0:
                            continue  # the interpreter / adapter executable itself: exempt

                        if code_dir_resolved is not None:
                            try:
                                resolved_item.resolve().relative_to(code_dir_resolved)
                            except ValueError:
                                problems.append({
                                    "field": f"adapter.argv[{i}]",
                                    "message": "keep every adapter file in adapter.code_dir",
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
    directory regardless of the process's current working directory.

    `batch_size`, `max_rounds`, `adapter_timeout_s` and `allow_identical`
    are optional in `localize.json` (`validate_config` accepts them absent
    or explicitly `null`); this is where `CONFIG_DEFAULTS` is filled in, so
    every downstream consumer can index them directly instead of each
    repeating its own fallback."""
    root = Path(root)
    cfg = read_json(root / "localize.json", "localize.json")
    problems = validate_config(cfg, root)
    if problems:
        detail = "; ".join(f"{p['field']}: {p['message']}" for p in problems)
        fail(f"localize.json has problems: {detail}", EXIT_CANNOT, problems=problems)
    cfg["project_root"] = str(_resolve_maybe_relative(root, cfg["project_root"]).resolve())
    for key, default in CONFIG_DEFAULTS.items():
        if cfg.get(key) is None:
            cfg[key] = list(default) if isinstance(default, list) else default
    return cfg


# ---------------------------------------------------------------------------
# messages.json
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
    or `None` when its shape is valid: types, required keys, unique ids,
    non-empty label lists, and every target's form count matching its label
    count. Content judgments (script checks, review) are a different layer
    and are not this function's job."""
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

    source_forms_ok = is_forms_value(source) and bool(source["forms"])
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
        if not is_forms_value(value):
            return f"{tag}.targets[{locale!r}] must be {{'forms': [...]}} or null"
        expected_labels = target_labels.get(locale)
        if expected_labels is not None and len(value["forms"]) != len(expected_labels):
            return f"{tag}.targets[{locale!r}].forms length must equal plural.target_labels[{locale!r}] length"

    return None


def load_messages(path) -> dict:
    """Read and shape-validate `messages.json`. The only reader: every
    script that needs message data goes through this."""
    data = read_json(path, "messages.json")
    problem = messages_shape_problem(data)
    if problem:
        fail(f"messages.json is invalid: {problem}", EXIT_CANNOT)
    return data


# ---------------------------------------------------------------------------
# Adapter acceptance -- see references/adapter-contract.md
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


def resolve_argv(root, cfg: dict) -> list[str]:
    """The one argv-resolution rule: an element that is already an absolute
    path stays as is; a relative element naming a file that exists under
    `root` becomes its absolute path; anything else -- a
    bare command name meant to be found on `PATH` (`"node"`, `"python3"`),
    or a flag -- passes through unchanged. `adapter_client.run` calls this
    to build the argv it actually executes; `adapter_digest` calls it to
    decide what to hash; `validate_config` resolves relative elements the
    same way to check they sit inside `adapter.code_dir`."""
    root = Path(root)
    resolved = []
    for item in cfg["adapter"]["argv"]:
        if os.path.isabs(item):
            resolved.append(item)
            continue
        candidate = root / item
        resolved.append(str(candidate) if candidate.is_file() else item)
    return resolved


def adapter_digest(root, cfg: dict) -> dict:
    """`{"files": {path: sha256}, "argv": [...], "options_sha256": ...}`
    identifying what the adapter actually executes: the sha256 of every file
    under `adapter.code_dir` (default `ADAPTER_DIR_NAME`, resolved the same
    way as `project_root` -- relative against `root`, absolute used as is),
    ALL of it, so a helper file no `argv` element names directly is still
    covered; the sha256 of every `resolve_argv` element that resolves to an
    existing file, wherever it lives -- this catches `argv[0]` (the
    interpreter, or the adapter executable itself), exempt from
    `validate_config`'s "must sit inside code_dir" rule but still hashed
    here, so an interpreter upgrade correctly invalidates acceptance;
    `argv[1:]` elements that resolve to a file are, per `validate_config`,
    already inside `code_dir`, so hashing them again here is redundant with
    the tree walk, not wrong; `adapter.argv` itself, so a changed argument
    or a swapped script is caught even when its byte content happens to
    match; and the options. `code_dir` must already exist as a directory --
    `validate_config` is what normally guarantees that before this ever
    runs; called directly (as tests do) it enforces the same thing itself,
    `fail(EXIT_CANNOT)`."""
    root = Path(root)
    adapter_cfg = cfg["adapter"]
    code_dir_value = adapter_cfg.get("code_dir", ADAPTER_DIR_NAME)
    code_dir = _resolve_maybe_relative(root, code_dir_value)
    if not code_dir.is_dir():
        fail(f"adapter.code_dir is not a directory: {code_dir_value}", EXIT_CANNOT)

    files = _tree_digests(code_dir)

    for item in resolve_argv(root, cfg):
        if os.path.isabs(item):
            candidate = Path(item)
            if candidate.is_file():
                files[str(candidate.resolve())] = sha256_file(candidate)

    argv = list(adapter_cfg["argv"])
    options = adapter_cfg.get("options", {})
    return {"files": files, "argv": argv, "options_sha256": sha256_json(options)}


def adapter_lock_matches(lock: dict, current: dict) -> bool:
    """`True` when `lock` (an `adapter.lock.json`-shaped dict, or `{}`)
    still matches `current` (an `adapter_digest` result) on files, argv and
    options_sha256 -- the comparison `require_accepted_adapter` enforces,
    and `status.py` reports on without failing."""
    return (
        lock.get("files") == current.get("files")
        and lock.get("argv") == current.get("argv")
        and lock.get("options_sha256") == current.get("options_sha256")
    )


def require_accepted_adapter(root, cfg: dict) -> None:
    """`fail(EXIT_FAIL)` unless `R/adapter.lock.json` matches the adapter's
    current files, argv and options (`adapter_digest`). Called by
    `collect.py`, `export_values.py` and `packets.py` before any of them run
    the adapter or rely on its accepted state."""
    root = Path(root)
    lock_path = root / "adapter.lock.json"
    lock = read_json(lock_path, "adapter.lock.json") if lock_path.is_file() else {}
    current = adapter_digest(root, cfg)
    if not adapter_lock_matches(lock, current):
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
