"""Adapter acceptance (plan section 6). `run`:

1. `collect`s the LIVE project (read-only by contract) -- this is where the
   adapter's `files` list comes from.
2. Stages exactly those files into a temporary directory
   (`lz_common.stage_files`) and does the unchanged and awkward-value round
   trips there (`export`/`collect`), never in the live project. Real
   projects carry dependency and data directories far larger than their
   catalogs, so the core never copies a whole project -- an adapter whose
   `collect`/`export` needs anything beyond its declared `files` fails here.
3. Re-runs `collect` in the staged copy and compares it with the live
   collect: any difference means the adapter reads something it did not
   declare, and acceptance fails.
4. Runs `parse` against the LIVE project (read-only; it may need the
   project's own tooling, which is not limited to the declared files).

It also prepares the coverage turn's packet and prompt; `accept` validates
the coverage turn's answer and, once every miss is out of scope, writes
`adapter.lock.json`.

An `adapter_client.AdapterError` (a broken adapter subprocess, a timeout, a
malformed reply) and a `lz_common.stage_files` refusal (a declared file is
missing, a symlink, absolute, or escapes the project) are not caught here:
they propagate to `lz_common.run_main`, which reports them and exits `2`
(cannot run) -- the adapter itself could not be exercised, as distinct from
a check this script ran and found failing (exit `1`).
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import subprocess
import tempfile
from pathlib import Path

import adapter_client
import lz_common

COVERAGE_DIR_NAME = "_coverage"
CHECK_RESULT_NAME = "_adapter_check.json"
PLACEHOLDER = "{{PACKET_JSON}}"

# Awkward text: quote, backslash, newline, tab, a leading and a trailing
# space, and non-Latin script, with a tag so a swapped or duplicated form is
# still detectable.
def _awkward_value(tag: str) -> str:
    return f" {tag} \"quoted\" \\backslash\nline\ttab \u00fc \u65e5\u672c "


def _first_of(messages: list[dict], plural: bool) -> dict | None:
    for message in messages:
        if ("plural" in message) == plural:
            return message
    return None


def _values_for_locale(messages: list[dict], locale: str) -> dict:
    """Every non-null current target value for `locale`, keyed by id --
    exactly what `export` needs to reproduce the project unchanged."""
    values = {}
    for message in messages:
        target = message["targets"].get(locale)
        if target is not None:
            values[message["id"]] = target
    return values


def _read_file_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _check_staging_consistency(live_messages: dict, staged_messages: dict) -> dict:
    """The staged copy (only the adapter's declared `files`) must collect to
    exactly what the live project collects to. A difference means the
    adapter reads something beyond what it declared."""
    if live_messages == staged_messages:
        return {"ok": True}

    live_by_id = {m["id"]: m for m in live_messages["messages"]}
    staged_by_id = {m["id"]: m for m in staged_messages["messages"]}
    return {
        "ok": False,
        "files_differ": live_messages.get("files") != staged_messages.get("files"),
        "only_in_live": sorted(set(live_by_id) - set(staged_by_id)),
        "only_in_staged": sorted(set(staged_by_id) - set(live_by_id)),
        "changed_ids": sorted(
            mid for mid in (set(live_by_id) & set(staged_by_id))
            if live_by_id[mid] != staged_by_id[mid]
        ),
    }


def _check_unchanged_round_trip(root: str, cfg: dict, project_dir: Path, messages: dict) -> dict:
    per_locale = {}
    for locale in cfg["target_locales"]:
        values = _values_for_locale(messages["messages"], locale)
        adapter_client.export(root, cfg, str(project_dir), locale, values)

    diffs = []
    for f in messages["files"]:
        before = _read_file_bytes(Path(cfg["project_root"]) / f)
        after = _read_file_bytes(project_dir / f)
        if before != after:
            diffs.append(f)
    ok = not diffs
    per_locale["diffs"] = diffs
    per_locale["ok"] = ok
    return per_locale


def _check_awkward_round_trip(root: str, cfg: dict, project_dir: Path, messages: dict, scratch_dir: str) -> dict:
    non_plural = _first_of(messages["messages"], plural=False)
    plural_msg = _first_of(messages["messages"], plural=True)
    results = {}
    overall_ok = True

    # The expected state accumulates ACROSS locale turns, starting from the
    # baseline: once a locale's awkward export legitimately lands, that
    # value is the expected baseline for every later turn's "everything
    # else stays put" comparison too -- comparing against the ORIGINAL,
    # never-updated baseline would flag an earlier turn's own intended
    # change as an unexpected one once a later turn's comparison runs.
    expected_by_id = {m["id"]: copy.deepcopy(m) for m in messages["messages"]}

    for locale in cfg["target_locales"]:
        entry: dict = {"ok": True, "problems": []}
        values = {}

        if non_plural is not None:
            values[non_plural["id"]] = _awkward_value(f"NP-{locale}")

        if plural_msg is not None:
            n = len(plural_msg["plural"]["target_labels"][locale])
            values[plural_msg["id"]] = {"forms": [_awkward_value(f"P{i}-{locale}") for i in range(n)]}

        if not values:
            results[locale] = entry
            continue

        adapter_client.export(root, cfg, str(project_dir), locale, values)
        after_out = os.path.join(scratch_dir, f"_awkward_{locale}.json")
        after = adapter_client.collect(root, cfg, str(project_dir), after_out)
        after_by_id = {m["id"]: m for m in after["messages"]}

        for msg_id, value in values.items():
            expected_by_id[msg_id]["targets"][locale] = value

        # The COMPLETE re-collected message, for every id, must equal the
        # accumulated expectation -- every locale, not only `locale` (plan
        # section 6, the "awkward round trip" requirement): comparing only
        # `targets[locale]` let an export that silently touches a DIFFERENT
        # locale's stored value for the same id go unnoticed, since that
        # other locale's own later turn would overwrite its own awkward
        # value and erase the evidence before anything checked it.
        for msg_id, expected_msg in expected_by_id.items():
            after_msg = after_by_id.get(msg_id)
            if after_msg is None:
                entry["ok"] = False
                entry["problems"].append({"id": msg_id, "missing_after_export": True})
                continue
            if after_msg != expected_msg:
                entry["ok"] = False
                entry["problems"].append({
                    "id": msg_id,
                    "expected": expected_msg,
                    "got": after_msg,
                    "unexpected_change": msg_id not in values,
                })

        extra_ids = sorted(set(after_by_id) - set(expected_by_id))
        if extra_ids:
            entry["ok"] = False
            entry["problems"].append({"extra_ids_after_export": extra_ids})

        results[locale] = entry
        overall_ok = overall_ok and entry["ok"]

    return {"ok": overall_ok, "locales": results}


def _parse_items_for_forms(
    counter, kind: str, msg_id: str, locale, value, items: list[dict], key_meta: dict
) -> None:
    """Append parse items for one message's source or target value (a plain
    string, or `{"forms": [...]}` for a plural) to `items`, each keyed by an
    opaque running index drawn from `counter` -- never a key built by string
    concatenation. A non-plural id like `"foo#0"` and the first form of a
    plural id `"foo"` used to both build the same `"src::foo#0"` key that
    way, letting one adapter reply silently overwrite the other's in the
    results dict. `key_meta` maps each new key back to `{"kind", "id",
    "locale"}` so results can be attributed without reconstructing a key."""
    forms = value["forms"] if isinstance(value, dict) else [value]
    for form in forms:
        key = str(next(counter))
        items.append({"key": key, "text": form})
        key_meta[key] = {"kind": kind, "id": msg_id, "locale": locale}


def _check_parse_sanity(root: str, cfg: dict, project_dir: Path, messages: dict) -> dict:
    counter = itertools.count()
    items: list[dict] = []
    key_meta: dict[str, dict] = {}

    for message in messages["messages"]:
        _parse_items_for_forms(counter, "source", message["id"], None, message["source"], items, key_meta)
        for locale, target in message["targets"].items():
            if target is not None:
                _parse_items_for_forms(counter, "target", message["id"], locale, target, items, key_meta)

    if not items:
        return {"ok": True, "source_failures": [], "target_failures": []}

    results = adapter_client.parse(root, cfg, str(project_dir), items)

    source_failures = sorted({
        meta["id"] for key, meta in key_meta.items()
        if meta["kind"] == "source" and not results[key]["ok"]
    })
    target_failures = sorted(
        (
            {"id": meta["id"], "locale": meta["locale"], "error": results[key].get("error")}
            for key, meta in key_meta.items()
            if meta["kind"] == "target" and not results[key]["ok"]
        ),
        key=lambda e: (e["id"], e["locale"]),
    )

    return {"ok": not source_failures, "source_failures": source_failures, "target_failures": target_failures}


def _project_inventory(project_dir: Path) -> list[str]:
    # A project can be a subdirectory of a larger git checkout without a
    # `.git` entry of its own (a monorepo layout); detect the ENCLOSING
    # work tree, not just a local `.git`, or the tracked-plus-unignored
    # contract silently degrades to an unfiltered walk that includes
    # whatever the enclosing repo's `.gitignore` was meant to keep out.
    in_work_tree = False
    try:
        check = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        in_work_tree = check.returncode == 0 and check.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        in_work_tree = False

    if in_work_tree:
        try:
            proc = subprocess.run(
                ["git", "-C", str(project_dir), "ls-files", "--cached", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                return sorted({line for line in proc.stdout.splitlines() if line})
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the directory walk

    paths = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), project_dir)
            paths.append(rel.replace(os.sep, "/"))
    return sorted(paths)


def _build_coverage_packet(project_dir: Path, messages: dict) -> dict:
    ids_by_file: dict[str, list[str]] = {}
    for message in messages["messages"]:
        f = message["context"]["file"]
        ids_by_file.setdefault(f, []).append(message["id"])
    files = [{"file": path, "ids": sorted(ids)} for path, ids in sorted(ids_by_file.items())]
    return {
        "schema": 1,
        "project_root": str(project_dir),
        "files": files,
        "inventory": _project_inventory(project_dir),
    }


def _write_coverage_prompt(plugin_root: Path, out_dir: Path, packet: dict) -> None:
    template_path = plugin_root / "assets" / "templates" / "coverage_TASK.md"
    if not template_path.is_file():
        lz_common.fail(f"packaged coverage prompt template is missing: {template_path}", lz_common.EXIT_CANNOT)
    template_text = template_path.read_text(encoding="utf-8")
    packet_json = json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True)
    rendered = template_text.replace(PLACEHOLDER, packet_json)
    lz_common.atomic_write_text(out_dir / "prompt.md", rendered)


def cmd_run(args) -> int:
    root = lz_common.resolve_root(args.root)
    cfg = lz_common.load_config(root)
    project_dir = Path(cfg["project_root"])

    with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
        live_out = os.path.join(tmp, "messages_live.json")
        live_messages = adapter_client.collect(str(root), cfg, str(project_dir), live_out)

        # Stage exactly the declared files (never the whole project: a real
        # project's dependency/data directories dwarf its catalogs). A
        # missing/symlinked/absolute/escaping declared file refuses here
        # (EXIT_CANNOT) -- lz_common.stage_files -> lz_common.fail -> run_main.
        temp_project = Path(tmp) / "project"
        lz_common.stage_files(project_dir, live_messages["files"], temp_project)

        staged_out = os.path.join(tmp, "messages_staged.json")
        staged_messages = adapter_client.collect(str(root), cfg, str(temp_project), staged_out)
        staging_result = _check_staging_consistency(live_messages, staged_messages)

        unchanged_result = _check_unchanged_round_trip(str(root), cfg, temp_project, live_messages)
        awkward_result = _check_awkward_round_trip(str(root), cfg, temp_project, live_messages, tmp)
        parse_result = _check_parse_sanity(str(root), cfg, project_dir, live_messages)

        overall_ok = (
            staging_result["ok"] and unchanged_result["ok"] and awkward_result["ok"] and parse_result["ok"]
        )

        # Bound to the run: `accept` refuses unless the adapter's digest at
        # acceptance time still equals this one, so a checked-and-passed
        # adapter cannot be edited before `accept` locks in a version that
        # was never actually exercised.
        check_result = {
            "schema": 1,
            "ok": overall_ok,
            "staging_consistent": staging_result,
            "unchanged_round_trip": unchanged_result,
            "awkward_round_trip": awkward_result,
            "parse_sanity": parse_result,
            "adapter_digest": lz_common.adapter_digest(root, cfg),
            "checked_at": lz_common.now_iso(),
        }

        runs_dir = root / "runs"
        lz_common.atomic_write_json(runs_dir / CHECK_RESULT_NAME, check_result)

        coverage_dir = runs_dir / COVERAGE_DIR_NAME
        packet = _build_coverage_packet(project_dir, live_messages)
        lz_common.atomic_write_json(coverage_dir / "packet.json", packet)
        plugin_root = Path(__file__).resolve().parent.parent
        _write_coverage_prompt(plugin_root, coverage_dir, packet)

    lz_common.emit({"ok": overall_ok, "result": check_result})
    return lz_common.EXIT_OK if overall_ok else lz_common.EXIT_FAIL


def _missing_key(entry: dict) -> str:
    key = entry.get("key")
    return entry["file"] if key is None else f"{entry['file']}#{key}"


def _validate_coverage_output(payload) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("missing"), list):
        lz_common.fail("coverage output must be an object with a 'missing' list", lz_common.EXIT_CANNOT)
    missing = payload["missing"]
    for entry in missing:
        if not isinstance(entry, dict):
            lz_common.fail("a coverage 'missing' entry is not an object", lz_common.EXIT_CANNOT)
        if not isinstance(entry.get("file"), str):
            lz_common.fail("a coverage 'missing' entry has no string 'file'", lz_common.EXIT_CANNOT)
        if entry.get("key") is not None and not isinstance(entry.get("key"), str):
            lz_common.fail("a coverage 'missing' entry's 'key' must be a string or null", lz_common.EXIT_CANNOT)
        if not isinstance(entry.get("why_user_visible"), str):
            lz_common.fail(
                "a coverage 'missing' entry has no string 'why_user_visible'", lz_common.EXIT_CANNOT
            )
    return missing


def cmd_accept(args) -> int:
    root = lz_common.resolve_root(args.root)
    cfg = lz_common.load_config(root)

    check_result = lz_common.read_json(root / "runs" / CHECK_RESULT_NAME, "adapter_check.py run result")
    if not check_result.get("ok"):
        lz_common.fail(
            "adapter_check.py run did not pass; fix the adapter and rerun before accepting",
            lz_common.EXIT_FAIL,
            check_result=check_result,
        )

    digest = lz_common.adapter_digest(root, cfg)
    if check_result.get("adapter_digest") != digest:
        lz_common.fail(
            "the adapter changed since the last run; re-run `adapter_check.py run`",
            lz_common.EXIT_FAIL,
        )

    coverage_payload = lz_common.read_json(Path(args.coverage), "coverage turn output")
    missing = _validate_coverage_output(coverage_payload)

    out_of_scope = set(args.out_of_scope or [])
    unaccepted = [entry for entry in missing if _missing_key(entry) not in out_of_scope]
    if unaccepted:
        lz_common.fail(
            "the coverage turn found strings the adapter misses that are not marked out of scope",
            lz_common.EXIT_FAIL,
            unaccepted=unaccepted,
        )

    lock = {
        "schema": 1,
        "files": digest["files"],
        "argv": digest["argv"],
        "options_sha256": digest["options_sha256"],
        "check_results": check_result,
        "missing": missing,
        "out_of_scope": sorted(out_of_scope),
        "by": args.by,
        "at": lz_common.now_iso(),
    }
    lz_common.atomic_write_json(root / "adapter.lock.json", lock)
    lz_common.emit({"ok": True, "lock": lock})
    return lz_common.EXIT_OK


def build_parser():
    parser = lz_common.make_parser("adapter_check.py", "Adapter acceptance: run the round-trip checks, then accept.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Exercise the adapter in a temporary copy of the project.")
    run_p.add_argument("--root", required=True)

    accept_p = sub.add_parser("accept", help="Validate the coverage turn's output and write adapter.lock.json.")
    accept_p.add_argument("--root", required=True)
    accept_p.add_argument("--coverage", required=True)
    accept_p.add_argument("--by", required=True)
    accept_p.add_argument("--out-of-scope", nargs="*", default=[])

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "run":
        return cmd_run(args)
    if args.command == "accept":
        return cmd_accept(args)
    lz_common.fail(f"unknown command: {args.command}", lz_common.EXIT_CANNOT)


if __name__ == "__main__":
    lz_common.run_main(main)
