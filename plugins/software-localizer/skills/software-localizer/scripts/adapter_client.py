"""The only module that invokes a project adapter (plan section 5).

An adapter is `cfg["adapter"]["argv"]` plus one of `collect` / `export` /
`parse`, run with the project root as the working directory, stdin closed,
and a timeout. It must print exactly one JSON line on stdout and exit `0`.
Any other outcome — non-zero exit, a timeout, output that is not exactly one
JSON line, or a reply missing a field this contract requires — becomes an
`AdapterError` naming the command that failed.

`collect`/`export`/`parse` write their options (and, for `export`/`parse`,
their other input) to a temp file under the workspace root and pass its path
as `--options`/`--values`/`--in`; nothing is ever passed as a raw argv
string the adapter would have to re-parse.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

import lz_common


class AdapterError(Exception):
    """An adapter invocation failed: non-zero exit, a timeout, output that
    was not exactly one JSON line, or a reply missing a required field."""


def _resolve_argv(root: str, argv: list[str]) -> list[str]:
    """Resolve a relative element of `argv` against the workspace root when
    a file exists there; anything else (an absolute path, or a bare command
    name meant to be found on `PATH`, such as `"node"`) passes through
    unchanged."""
    resolved = []
    for token in argv:
        if os.path.isabs(token):
            resolved.append(token)
            continue
        candidate = os.path.join(root, token)
        resolved.append(candidate if os.path.isfile(candidate) else token)
    return resolved


def run(
    root: str,
    cfg: dict,
    project_dir: str,
    command: str,
    extra_args: list[str],
    timeout: float | None = None,
) -> dict:
    """Run `adapter.argv + [command] + extra_args` and return the one parsed
    JSON object it printed on stdout. Raises `AdapterError` naming `command`
    on any failure."""
    argv = _resolve_argv(root, cfg["adapter"]["argv"]) + [command] + list(extra_args)
    effective_timeout = timeout if timeout is not None else cfg["adapter_timeout_s"]

    try:
        proc = subprocess.run(
            argv,
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=effective_timeout,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise AdapterError(f"adapter {command!r} timed out after {effective_timeout}s")
    except OSError as exc:
        raise AdapterError(f"adapter {command!r} failed to start: {exc}")

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise AdapterError(f"adapter {command!r} exited {proc.returncode}: {detail}")

    lines = [line for line in proc.stdout.splitlines() if line.strip() != ""]
    if len(lines) != 1:
        raise AdapterError(
            f"adapter {command!r} did not print exactly one JSON line (got {len(lines)})"
        )

    try:
        reply = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise AdapterError(f"adapter {command!r} printed invalid JSON: {exc}")

    if not isinstance(reply, dict):
        raise AdapterError(f"adapter {command!r} reply is not a JSON object")

    return reply


def _write_options(tmp_dir: str, cfg: dict) -> str:
    options_path = os.path.join(tmp_dir, "options.json")
    with open(options_path, "w", encoding="utf-8") as fh:
        json.dump(cfg["adapter"].get("options", {}), fh, ensure_ascii=False)
    return options_path


def _require_ok(reply: dict, command: str) -> None:
    if reply.get("ok") is not True:
        error = reply.get("error", "unknown error")
        raise AdapterError(f"adapter {command!r} reported failure: {error}")


def collect(root: str, cfg: dict, project_dir: str, out_path: str) -> dict:
    """Run `collect`, validate the messages file it wrote, and only then
    atomically replace `out_path` with it. Returns the validated messages
    dict. Validation happens on the temp-dir copy, before `out_path` is
    touched at all: a malformed collect must never clobber the last good
    `messages.json`."""
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        options_path = _write_options(tmp, cfg)
        collected_path = os.path.join(tmp, "messages.json")
        extra_args = [
            "--options", options_path,
            "--source-locale", cfg["source_locale"],
            "--target-locales", ",".join(cfg["target_locales"]),
            "--out", collected_path,
        ]
        reply = run(root, cfg, project_dir, "collect", extra_args)
        _require_ok(reply, "collect")

        if not os.path.isfile(collected_path):
            raise AdapterError("adapter 'collect' did not write the messages file")

        with open(collected_path, "r", encoding="utf-8") as fh:
            try:
                messages = json.load(fh)
            except json.JSONDecodeError as exc:
                raise AdapterError(f"adapter 'collect' wrote invalid JSON: {exc}")

        # Shape-validated against the plan's message-format contract here, in
        # the temp dir, before `out_path` is touched: a violation means the
        # adapter is wrong and fails the process (lz_common.load_messages's
        # own contract, exit 2) without ever overwriting the last good file.
        problem = lz_common.messages_shape_problem(messages)
        if problem is not None:
            lz_common.fail(f"messages.json is invalid: {problem}", lz_common.EXIT_CANNOT)

        lz_common.atomic_write_json(out_path, messages)

    return messages


def export(root: str, cfg: dict, project_dir: str, locale: str, values: dict) -> dict:
    """Run `export` for `locale` with `values` (id -> string | {"forms":
    [...]}). Returns the adapter's reply."""
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        options_path = _write_options(tmp, cfg)
        values_path = os.path.join(tmp, "values.json")
        with open(values_path, "w", encoding="utf-8") as fh:
            json.dump({"values": values}, fh, ensure_ascii=False)

        extra_args = [
            "--options", options_path,
            "--locale", locale,
            "--values", values_path,
        ]
        reply = run(root, cfg, project_dir, "export", extra_args)
        _require_ok(reply, "export")
        return reply


def _require_valid_token(key: str, index: int, token) -> None:
    """A `parse` token must be `{"kind": "argument", "name": str, "signature":
    str}` or `{"kind": "structure", "text": str}`; any other shape, or an
    unknown `kind`, is a malformed reply the caller must not silently treat
    as a valid parse."""
    if not isinstance(token, dict):
        raise AdapterError(f"adapter 'parse' reply for key {key!r} has a non-object token at index {index}")
    kind = token.get("kind")
    if kind == "argument":
        if not isinstance(token.get("name"), str) or not isinstance(token.get("signature"), str):
            raise AdapterError(
                f"adapter 'parse' reply for key {key!r} has a malformed argument token at index {index}"
            )
    elif kind == "structure":
        if not isinstance(token.get("text"), str):
            raise AdapterError(
                f"adapter 'parse' reply for key {key!r} has a malformed structure token at index {index}"
            )
    else:
        raise AdapterError(f"adapter 'parse' reply for key {key!r} has an unknown token kind {kind!r} at index {index}")


def parse(root: str, cfg: dict, project_dir: str, items: list[dict]) -> dict[str, dict]:
    """Run `parse` over `items` (`[{"key", "text"}]`). Returns
    `{key: {"ok": True, "tokens": [...]} | {"ok": False, "error": "..."}}`,
    one entry per requested key."""
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        options_path = _write_options(tmp, cfg)
        in_path = os.path.join(tmp, "parse_in.json")
        items = list(items)
        with open(in_path, "w", encoding="utf-8") as fh:
            json.dump({"items": items}, fh, ensure_ascii=False)

        extra_args = ["--options", options_path, "--in", in_path]
        reply = run(root, cfg, project_dir, "parse", extra_args)

    results = reply.get("results")
    if not isinstance(results, dict):
        raise AdapterError("adapter 'parse' reply is missing the 'results' object")

    for key, result in results.items():
        if not isinstance(result, dict) or "ok" not in result:
            raise AdapterError(f"adapter 'parse' reply for key {key!r} is missing required fields")
        ok = result["ok"]
        if not isinstance(ok, bool):
            raise AdapterError(f"adapter 'parse' reply for key {key!r} has a non-boolean 'ok'")
        if ok:
            tokens = result.get("tokens")
            if not isinstance(tokens, list):
                raise AdapterError(f"adapter 'parse' reply for key {key!r} is missing 'tokens'")
            for i, token in enumerate(tokens):
                _require_valid_token(key, i, token)
        elif not isinstance(result.get("error"), str):
            raise AdapterError(f"adapter 'parse' reply for key {key!r} is missing 'error'")

    requested_keys = {item["key"] for item in items}
    missing_keys = requested_keys - results.keys()
    if missing_keys:
        raise AdapterError(f"adapter 'parse' reply is missing results for {sorted(missing_keys)}")

    return results
