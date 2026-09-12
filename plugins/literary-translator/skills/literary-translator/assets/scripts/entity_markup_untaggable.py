#!/usr/bin/env python3
"""entity_markup_untaggable.py -- report, ahead of W7, which canon targets
`assemble.py` will refuse as a marked entity span's LABEL (#932).

## The gap this closes

`output.entity_markup.index_from: markup` lets the translator wrap a name in
a declared tag (e.g. `<person ref="...">...</person>`) so the obsidian
adapter can mint or link an entity note for it. Whether a given canon target
form is even ELIGIBLE to be that label is decided entirely by `canon.json` +
`canon_link_groups.json` -- two or more canon entries owning the same
`canonical_target_form`, with no `canon_link_groups.json` group re-linking
them to one primary, makes that string UNTAGGABLE: `render_obsidian.py`'s
own collision de-linking (#206/#207/#588) drops the target from its link
map, and if a marked span's label still names it,
`_entity_markup_canon_collision_preflight`
(`render_obsidian.py`'s `_canon_collision_conflicts`, #837) refuses the
WHOLE render with `RenderError("entity_markup_canon_collision")` at W7 --
after translation, after review, after every fix round. Nothing before that
point tells the translator or the reviewer the string was ever off-limits.
Measured on a real book (he -> en): three review rounds plus one full
re-review were spent narrowing spans down to the two collision targets this
script would have named at W3a/W5, before a single span was fixed.

This script is a REPORT, not a gate (like `final_audit.py`, it is in no
`cache_key.py` bundle tuple -- see that module's docstring for why a script
that never shapes derived content must stay out of every hash). It computes
the untaggable set by CALLING the renderer's own shipped predicate --
`render_obsidian._owners_by_target`, `render_obsidian._link_decision` and
`render_obsidian._category_compatible` -- never a re-implementation, so the
list this script writes and the refusal `assemble.py` raises can never
disagree about which targets are untaggable. `canon_link_groups.load_link_groups`
supplies the same `{member: primary}` map `_link_decision` itself consults,
loaded the identical way `assemble.py`'s own `_attach_link_groups` loads it
(lazy import, `os.path.lexists` rather than `Path.exists()` so a dangling
sidecar symlink is a load error rather than silently "absent").

## Why a sidecar file and not a segpack field or a `select_segments.py` flag

`segpack.py` is a member of `cache_key.DERIVATION_BUNDLE_MEMBERS`: touching it
re-stales every converged segment (W3/W3a regeneration,
`--restamp-derivation`), and `segpack.schema.json` is a `schema_hash` member
too. Neither this script's existence nor its output belongs on that path --
this is a book-wide fact about `canon.json`, not a per-segment derivation.
`select_segments.py --classify-only` was the issue's own alternative and is
deliberately NOT touched: it is a completeness/dispatch classifier, not an
entity-markup authority, and widening it would duplicate this script's one
job in a second place. Two report-only surfaces read this file's output:
`mass-translate-wf.template.js`'s three prompts (translator, reviewer,
fixer) tell the agent to consult it, and `final_audit.py` gains a
WARN-only check (`warn_untaggable_targets`) that recomputes the list LIVE
via `resolve_untaggable()` below and flags a stale or missing on-disk copy.
Neither gates anything; the render-time refusal this script front-runs is
untouched.

## What is written

`${durable_root}/entity_markup_untaggable.json`:

    {"generated_by": "entity_markup_untaggable.py",
     "tags": [...],            # output.entity_markup.tags, in declared order
     "untaggable": [
       {"target": "...",                    # NFC canonical_target_form
        "tags": [...],                      # declared tags it is untaggable under
        "owners": [...],                    # every canon source_form owning it
        "sense_translated_owners": [...]},  # the subset basis == sense_translated
       ...
     ]}

Sorted by `target`; `tags` in declared order within a row. NO timestamp: the
document is a pure function of `profile.yml` + `canon.json` +
`canon_link_groups.json`, so `final_audit.py`'s staleness check is a plain
equality against a freshly recomputed list, not a "when was this run"
heuristic. The list is frozen at the moment this script runs -- a LATER
`canon_link_groups.json` edit can make it stale in the fail-safe direction
only (a target that gets re-linked stays listed here, so it stays untagged
and loses one index link; it never gains a false "safe to tag" verdict).
`final_audit.py`'s WARN and `SKILL.md`'s W3a step both say to re-run it
after any `canon.json` / `canon_link_groups.json` change.

Mode `off` (no `output.entity_markup` block) or `strip`
(`index_from` absent/`canon`) writes NOTHING and reports
`applicable: false`: neither mode ever records a span, so nothing can be
untaggable. `strip`/`off` do not even read canon.json.

## Contract

    resolve_untaggable(profile, durable_root) -> Resolution
        Resolution = namedtuple("Resolution", "mode tags rows path payload_text")

Raises ONLY `UntaggableError` (never a bare `AssembleError`,
`CanonLinkGroupsLoadError`, or stdlib exception) -- `final_audit.py`'s
WARN-only caller must never crash the whole audit over a malformed
`canon.json` or a broken sibling script. See `resolve_untaggable`'s own
docstring for the exact boundary and every `reason` this can carry.

## House style

Self-anchored (`DURABLE_ROOT`/`SCRIPTS_DIR` from `__file__`, no
`--durable-root` flag, matching every other script in this directory).
`json_stdout.py` loaded by exact path (never a bare `import`, for the same
multi-durable-root staging reason `render_obsidian.py`'s own loader block
documents). Exactly one JSON line on stdout describing the OUTCOME; the
`untaggable` rows themselves are never echoed there -- a book with dozens of
collision targets, each with several long owner forms, would put a
truncatable duplicate of the durable artifact into an LLM-facing transcript
(the same bound `reject_review.py`'s own unowned-data reporting applies for
the same reason). The file is the record; the operator and the prompts that
cite it read it directly. All human-readable detail goes to stderr;
`fatal()` prints one line there and exits 2, never a bare traceback.
"""

import argparse
import json
import os
import secrets
import stat
import sys
from collections import namedtuple
from pathlib import Path
from typing import NoReturn

# Several sibling entrypoints in this directory promise not to write
# anything unexpected to disk; this one writes only its own durable output
# file, and opting out of __pycache__ uniformly matches every neighbour.
sys.dont_write_bytecode = True

# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout` -- see render_obsidian.py's
# own loader block (copied here verbatim in spirit) for why a bare sibling
# import is unsafe across multiple staged durable roots in one process.
import importlib.util as _importlib_util

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"entity_markup_untaggable.py: cannot load json_stdout.py from "
        f"{_JSON_STDOUT_PATH} ({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside entity_markup_untaggable.py "
        "under ${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line


def fatal(msg: str) -> NoReturn:
    """Fail loudly, naming the problem, and exit 2. Never a bare traceback
    for an expected/actionable condition. Defined early (before the
    render_obsidian import right below) because that import is a hard,
    unwrapped dependency failure this same function reports."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _system_exit_detail(exc: "SystemExit") -> str:
    """`sys.exit(some_string)` sets `SystemExit.code` to that string, but
    Python only auto-prints it to stderr when the exception propagates all
    the way to the interpreter uncaught. Surface it when present; copied
    from `assemble.py`'s own helper of the same name (each duplicates the
    other rather than sharing a module -- this plugin's own no-shared-lib
    convention, see `plugin-facts.md`)."""
    if isinstance(exc.code, str) and exc.code:
        return f"it exited with: {exc.code!r}"
    return "see this run's stderr for the specific reason it halted"


# render_obsidian.py is a genuine, unconditional dependency of this script --
# untaggable_rows() calls its predicate directly and cannot function without
# it, the same posture final_audit.py takes on validate_draft.py/
# bootstrap_names.py. A failure here (missing PyYAML, a missing json_stdout.py
# sibling) is therefore a hard, un-wrapped import failure -- never an
# UntaggableError -- so that `final_audit.py`'s own lazy
# `import entity_markup_untaggable as emu` sees it as the ImportError/
# SystemExit its WARN wrapper is written to catch, exactly as it would for
# any other broken sibling script. `assemble.py` and `canon_link_groups.py`,
# by contrast, are imported LAZILY below and their failures ARE wrapped as
# UntaggableError -- see resolve_untaggable()'s own docstring for why the
# two are treated differently. `OSError` (an EXISTING but unreadable
# render_obsidian.py -- permission bits, a directory left in its place)
# and `SystemExit` (render_obsidian.py's own PyYAML-missing guard, or its
# json_stdout.py loader) are both routed through `fatal()` here with the
# same `dependency_precondition` reason the lazy imports below use, rather
# than a bare traceback.
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import render_obsidian
except (ImportError, OSError) as _render_obsidian_exc:  # pragma: no cover - staging error path
    fatal(
        f"could not import render_obsidian.py from {SCRIPTS_DIR}: "
        f"{_render_obsidian_exc} (reason=dependency_precondition)"
    )
except SystemExit as _render_obsidian_exit:  # pragma: no cover - staging error path
    fatal(
        "render_obsidian.py halted during its own module-level dependency "
        f"preflight while importing it -- "
        f"{_system_exit_detail(_render_obsidian_exit)} "
        "(reason=dependency_precondition)"
    )

OUTPUT_NAME = "entity_markup_untaggable.json"

# dir-fd / open() flag constants for the atomic-publish discipline below --
# same names and fallbacks as scaffold_setup.py's own copy (a platform
# lacking one of these degrades the flag to a no-op rather than raising at
# import time).
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)

# `mode`: "off" | "strip" | "index" (mirrors assemble._entity_markup_mode's
# own vocabulary). `tags`/`rows`/`path` are always populated; `payload_text`
# is the exact document string to publish, or None when mode != "index"
# (nothing is ever written for off/strip).
Resolution = namedtuple("Resolution", "mode tags rows path payload_text")


class UntaggableError(Exception):
    """The one exception `resolve_untaggable()` (and `declared_tags()`) ever
    raise. `reason` is a short machine-matchable code -- one of
    `entity_markup_config_invalid`, `entity_markup_index_unsupported_target`
    (both carried through verbatim from the `assemble.AssembleError` that
    caused them), `dependency_precondition` (a required sibling script could
    not be imported, or halted during its own module-level preflight),
    `canon_invalid` (canon.json is missing/unreadable/malformed, or its
    entries cannot be projected the way the renderer's own helpers project
    them), or `canon_link_groups_invalid` (the sidecar failed to load)."""

    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


def _import_assemble():
    """`import assemble`, lazily and guarded -- never at module import time.
    `assemble.py`'s own module-level sibling imports
    (`validate_draft`/`output_resolve`/`cache_key`) can themselves
    `sys.exit(2)` during THIS very import statement
    (`assemble.py`'s own `_dependency_precondition_fatal`, mirrored here
    exactly at its own sibling-import block), so `ImportError` and
    `SystemExit` are both caught and re-raised as one `UntaggableError` with
    reason `dependency_precondition` -- this script's caller
    (`final_audit.py`'s WARN-only check) must see a controlled report, not
    a crash, when a sibling script is missing or broken. `OSError` is
    caught too: `assemble.py` EXISTING but unreadable (permission bits, a
    directory left in its place) raises `PermissionError`/`IsADirectoryError`
    -- both `OSError` subclasses -- from the import machinery itself, a
    different failure than "not found" but the same precondition to the
    caller."""
    try:
        import assemble
    except (ImportError, OSError) as exc:
        raise UntaggableError(
            f"could not import assemble.py from {SCRIPTS_DIR}: {exc}",
            "dependency_precondition",
        ) from exc
    except SystemExit as exc:
        raise UntaggableError(
            "assemble.py halted during its own module-level dependency "
            f"preflight while importing it -- {_system_exit_detail(exc)}",
            "dependency_precondition",
        ) from exc
    return assemble


def declared_tags(profile):
    """The resolved `output.entity_markup.tags` list, or `None` when the
    block is absent (mode `off`) -- via `assemble._entity_markup_config`,
    NEVER a restated grammar. `assemble.AssembleError` (an invalid block:
    non-mapping, unknown key, missing/non-list/empty/non-string/duplicate/
    malformed `tags`, a bad `ref_attribute` or `index_from`) becomes
    `UntaggableError(str(exc), exc.reason)` -- so this script refuses
    EXACTLY the configurations assembly refuses, never a subset."""
    assemble = _import_assemble()
    try:
        cfg = assemble._entity_markup_config(profile)
    except assemble.AssembleError as exc:
        raise UntaggableError(str(exc), exc.reason) from exc
    if cfg is None:
        return None
    return list(cfg["tags"])


def untaggable_rows(entries, tags, primary_by_source_form):
    """Rows for every canon target `tags` can never mark up, computed
    ENTIRELY through the renderer's own shipped predicate --
    `render_obsidian._owners_by_target` / `_link_decision` /
    `_category_compatible` -- so this list and
    `_entity_markup_canon_collision_preflight`'s own refusal can never
    disagree about which targets are untaggable (see
    `render_obsidian._canon_collision_conflicts`, the render-time twin of
    this function: that one walks MARKED SPANS and reports a conflict per
    labelled identity; this one walks EVERY canon target up front and
    reports a conflict per declared tag, so the operator sees the hazard
    before a single span is marked).

    `entries`: `canon.json`'s own `entries{}` mapping. `tags`: the
    resolved `output.entity_markup.tags` list, in declared order.
    `primary_by_source_form`: `{}` or the `canon_link_groups.json` sidecar's
    `{member: primary}` map.

    A target is untaggable under a given tag when: it has >=2 owners
    (`_owners_by_target`); `_link_decision` (collision delinking ON) finds
    no winner -- i.e. no single established entity or fully-grouped set of
    owners the tiebreak can resolve to; and >=2 of its owners are
    `_category_compatible` with that tag (fewer than two compatible owners
    means nothing can actually collide under THIS tag, even though the
    target collides in general -- mirrors `_canon_collision_conflicts`'s
    own `len(compatible) < 2: continue` exactly)."""
    owners_by_target = render_obsidian._owners_by_target(entries)
    rows = []
    for target in sorted(owners_by_target):
        owners = owners_by_target[target]
        if len(owners) < 2:
            continue
        winner, _cost = render_obsidian._link_decision(
            owners, True, primary_by_source_form
        )
        if winner is not None:
            continue  # a link group (or a single established entity) resolves it
        hit = []
        for tag in tags:
            compatible = [
                source_form
                for source_form, _basis in owners
                if render_obsidian._category_compatible(
                    (entries.get(source_form) or {}).get("category"), tag
                )
            ]
            if len(compatible) >= 2:
                hit.append(tag)
        if not hit:
            continue
        rows.append(
            {
                "target": target,
                "tags": hit,
                "owners": sorted(source_form for source_form, _basis in owners),
                "sense_translated_owners": sorted(
                    source_form for source_form, basis in owners
                    if basis == "sense_translated"
                ),
            }
        )
    return rows


def resolve_untaggable(profile, durable_root):
    """`Resolution` for the current `profile`/`canon.json`/
    `canon_link_groups.json`. Raises ONLY `UntaggableError` -- see the
    module docstring's "Contract" section.

    Mode resolution is delegated to `assemble._entity_markup_mode`
    (assembly's OWN copy, which re-validates the block through
    `_entity_markup_config` and raises on an invalid block or on
    `index_from: markup` under a non-obsidian target;
    `render_obsidian.py`'s own copy is the inert renderer variant and is
    deliberately NOT used here -- see that function's docstring for why the
    two copies must diverge exactly where they do).
    `assemble.AssembleError` becomes `UntaggableError(str(exc), exc.reason)`.

    When `mode != "index"`: returns `Resolution(mode, [], [], path, None)`
    WITHOUT reading canon.json at all -- `strip`/`off` never record a span,
    so nothing can be untaggable and there is nothing to report.

    When `mode == "index"`: the canon read, the `canon_link_groups.json`
    sidecar load (lazy, exactly `assemble.py`'s own `_attach_link_groups`
    discipline -- `os.path.lexists`, so a dangling sidecar symlink is a
    load error rather than silently "absent") and the row computation all
    run inside ONE boundary that catches `OSError` (an unreadable/vanished
    canon.json -- `final_audit.py`'s own loader catches the same class),
    `ValueError` (malformed JSON, or `canon.json`'s `entries` not being a
    mapping), `TypeError`/`AttributeError`/`KeyError` (a hand-edited canon
    entry with a wrong-typed field -- e.g. `_owners_by_target`'s own
    `.strip()` call on a non-string `canonical_target_form` raises
    `AttributeError`), `RecursionError` (an absurdly nested document) and
    `UnicodeError` (the escaped-lone-surrogate hole `claim_record.py`
    documents at its own `:668` -- a target form like `"\\ud800"` survives
    JSON parsing and every renderer helper untouched, but `.encode("utf-8")`
    on it raises `UnicodeEncodeError`, a `UnicodeError`/`ValueError`
    subclass). Every one of those becomes
    `UntaggableError(f"canon.json could not be read as the renderer's own "
    f"helpers read it: {exc!r}", "canon_invalid")` -- this script's caller
    (`final_audit.py`'s WARN-only lane) must never crash over a malformed
    canon.json.

    `payload_text` -- the exact document string a caller would publish --
    is built and `.encode("utf-8")`'d INSIDE this same boundary purely as a
    validation step (the resulting bytes are discarded here; the actual
    writer encodes it again itself), which is exactly what turns the
    lone-surrogate case above into `canon_invalid` here rather than a
    traceback at publish time.

    A present but broken `canon_link_groups.py` sibling (missing, or
    halting during its own module-level `jsonschema` preflight) is
    `UntaggableError(..., "dependency_precondition")`; a sidecar that
    fails validation is `UntaggableError(..., "canon_link_groups_invalid")`
    carrying `canon_link_groups.CanonLinkGroupsLoadError`'s own message."""
    path = durable_root / OUTPUT_NAME
    assemble = _import_assemble()
    try:
        mode = assemble._entity_markup_mode(profile)
    except assemble.AssembleError as exc:
        raise UntaggableError(str(exc), exc.reason) from exc
    if mode != "index":
        return Resolution(mode, [], [], path, None)

    tags = declared_tags(profile)

    try:
        canon_path = durable_root / "canon.json"
        canon_doc = json.loads(canon_path.read_text(encoding="utf-8"))
        entries = canon_doc.get("entries") if isinstance(canon_doc, dict) else None
        if not isinstance(entries, dict):
            raise ValueError(
                f"canon.json's 'entries' must be a JSON object, got "
                f"{type(entries).__name__}"
            )

        link_groups_path = durable_root / "canon_link_groups.json"
        primary_by_source_form = {}
        if os.path.lexists(link_groups_path):
            try:
                import canon_link_groups
            except (ImportError, OSError) as exc:
                raise UntaggableError(
                    f"{link_groups_path.name} is present but "
                    f"canon_link_groups.py could not be imported from "
                    f"{SCRIPTS_DIR}: {exc}",
                    "dependency_precondition",
                ) from exc
            except SystemExit as exc:
                raise UntaggableError(
                    f"{link_groups_path.name} is present but "
                    "canon_link_groups.py halted during its own "
                    f"module-level dependency preflight -- "
                    f"{_system_exit_detail(exc)}",
                    "dependency_precondition",
                ) from exc
            try:
                primary_by_source_form = canon_link_groups.load_link_groups(
                    link_groups_path, entries
                )
            except canon_link_groups.CanonLinkGroupsLoadError as exc:
                raise UntaggableError(
                    f"canon_link_groups.json failed to load: {exc}",
                    "canon_link_groups_invalid",
                ) from exc

        rows = untaggable_rows(entries, tags, primary_by_source_form)
        payload_text = (
            json.dumps(
                {"generated_by": "entity_markup_untaggable.py", "tags": tags, "untaggable": rows},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        payload_text.encode("utf-8")  # validation only -- the bytes are discarded
    except UntaggableError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError, UnicodeError) as exc:
        raise UntaggableError(
            f"canon.json could not be read as the renderer's own helpers "
            f"read it: {exc!r}",
            "canon_invalid",
        ) from exc

    return Resolution(mode, tags, rows, path, payload_text)


def _atomic_publish(dir_fd: int, name: str, text: str) -> None:
    """Publish `text` to `name`, a leaf entry inside the directory pinned
    by `dir_fd`, atomically. This follows the SAME DISCIPLINE as
    `scaffold_setup.py`'s own `atomic_write_text()` -- see that function's
    own docstring for the full threat-model rationale (the unguessable temp
    leaf, the fsync + inode/size identity check, the accepted
    sub-instruction stat->replace residual) -- with two deliberate
    differences: the temp leaf here is named `.{name}.{token}.tmp` rather
    than `.{name}.tmp.{pid}.{token}` (still unguessable, just a different
    shape), and refusals go through this script's own `fatal()` (stderr +
    exit 2) rather than `atomic_write_text()`'s `fail()` -- the two have the
    same posture, just a different name in a different module. It is
    reimplemented here rather than imported because `scaffold_setup.py` is
    PLUGIN-PATH-ONLY by its own module docstring's declaration (`SKILL.md`'s
    Step 0a copy list excludes it) -- it never exists beside this script in
    any real durable_root -- and extracting a shared leaf module for one
    function would be new machinery against this plugin's own established
    no-shared-lib convention (seven independent `draft_content_sha1()`
    copies already; see `plugin-facts.md`).

    Everything below happens BEFORE `os.replace()` durably renames the temp
    file into place -- every refusal above that point (`fatal()`, hence
    `sys.exit(2)`) leaves `name` untouched and the temp file unlinked. Once
    `os.replace()` itself returns, `name` HAS been published; a failure in
    a caller AFTER this function returns (e.g. `main()`'s own final
    `print()` hitting a broken stdout pipe) does not undo that -- this
    function's guarantee is about what happens up to and including its own
    return, not about anything the caller does next."""
    try:
        existing = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        fatal(f"refusing to write {name}: could not stat existing entry: {exc}")
    else:
        if not stat.S_ISREG(existing.st_mode):
            fatal(
                f"refusing to write {name}: it already exists and is not a "
                f"regular file (mode={oct(existing.st_mode)}) -- a symlink "
                "or other special entry must not be replaced"
            )

    tmp_name = f".{name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        fatal(f"refusing to write {name}: could not create temp file {tmp_name}: {exc}")

    def _cleanup_tmp() -> None:
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass

    def _refuse_open(detail: str) -> NoReturn:
        # Every refusal while `fd` is still open: release it, drop the temp
        # leaf, then the same stderr + exit-2 posture as every other refusal.
        os.close(fd)
        _cleanup_tmp()
        fatal(f"refusing to write {name}: {detail}")

    try:
        data = text.encode("utf-8")
        view = memoryview(data)
        written = 0
        while written < len(data):
            written += os.write(fd, view[written:])
        os.fsync(fd)
        fd_st = os.fstat(fd)
        try:
            name_st = os.stat(tmp_name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            _refuse_open(f"temp file vanished before publish: {exc}")
        if (fd_st.st_dev, fd_st.st_ino) != (name_st.st_dev, name_st.st_ino):
            _refuse_open("temp file was substituted before publish")
        if name_st.st_size != len(data):
            _refuse_open("temp file changed size before publish")
    except OSError as exc:
        _refuse_open(f"write failed: {exc}")
    else:
        os.close(fd)
    try:
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError as exc:
        _cleanup_tmp()
        fatal(f"refusing to write {name}: could not publish {tmp_name} as {name}: {exc}")


def main():
    argparse.ArgumentParser(
        description=(
            "Report the canon.json targets that assemble.py would refuse "
            "as a marked entity span's label (#932). Self-anchored: takes "
            "no arguments beyond --help."
        )
    ).parse_args()

    try:
        import validate_draft as vd
    except (ImportError, OSError) as exc:
        fatal(
            f"could not import validate_draft.py from {SCRIPTS_DIR}: {exc} "
            "(reason=dependency_precondition)"
        )
    except SystemExit as exc:  # pragma: no cover - defensive, should be unreachable
        fatal(
            "validate_draft.py halted during its own module-level "
            f"dependency preflight while importing it -- {_system_exit_detail(exc)} "
            "(reason=dependency_precondition)"
        )

    profile = vd.load_profile()

    try:
        res = resolve_untaggable(profile, DURABLE_ROOT)
    except UntaggableError as exc:
        fatal(f"{exc} (reason={exc.reason})")

    if res.mode != "index":
        file_present = res.path.exists()
        if file_present:
            print(
                f"NOTE: {OUTPUT_NAME} exists at {res.path} but "
                f"entity_markup_mode is {res.mode!r} -- left in place; "
                "delete it if the mode change is deliberate.",
                file=sys.stderr,
            )
        print(
            dumps_line(
                {
                    "success": True,
                    "applicable": False,
                    "entity_markup_mode": res.mode,
                    "path": str(res.path),
                    "file_present": file_present,
                    "untaggable_count": 0,
                }
            )
        )
        return

    dir_flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        dir_fd = os.open(str(DURABLE_ROOT), dir_flags)
    except OSError as exc:
        fatal(f"could not open {DURABLE_ROOT} to publish {OUTPUT_NAME}: {exc}")
    try:
        _atomic_publish(dir_fd, OUTPUT_NAME, res.payload_text)
    finally:
        os.close(dir_fd)

    print(
        dumps_line(
            {
                "success": True,
                "applicable": True,
                "entity_markup_mode": "index",
                "path": str(res.path),
                "tags": res.tags,
                "untaggable_count": len(res.rows),
            }
        )
    )


if __name__ == "__main__":
    main()
