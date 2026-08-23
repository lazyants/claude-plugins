#!/usr/bin/env python3
"""diff_rendered_output.py -- render+diff acceptance gate (W9 Assemble).

Generalizes `historiettes-t3/diff_rendered_output.py` to this plugin's own,
much simpler shared output contract: every built-in adapter's `render(...)`
returns `{"written": [...], "kind": "vault"|"file"}`
(references/output-target-adapters/README.md), and every adapter substitutes
its own sentinels/verse content/footnote defs at render time -- so unlike the
reference project's HTML-structural diff (v1 fake per-segment footnote
numbering vs v2 global numbering, verse restructuring, name-annotation
insertions, all needing bespoke normalization), there is exactly ONE
normalized form to compare here: a stdlib-only, markdown-aware LINE
reduction. No `bs4`, no HTML parsing at all -- see
`references/assembly-and-output.md`'s "Render + diff" section, the
authoritative spec this script implements.

This script NEVER re-renders anything live -- it only reads whatever
`assemble.py` already wrote to the candidate directory (self-resolved via
`output_resolve.resolve_out_dir(profile, durable_root)` -- the SAME
target-agnostic out_dir rule every adapter, including `render_obsidian.py`,
resolves against, so this script never imports an adapter module just to
find a path; or overridden via --candidate-dir for standalone testing
against a hand-built fixture vault) and diffs it against the last accepted
baseline, frozen under `${durable_root}/out/.baseline/` by a prior
`--accept-baseline` run.

## Reduction

    1. Normalize line endings (\\r\\n / \\r -> \\n).
    2. `rstrip()` trailing whitespace per line -- PRESERVING leading
       indentation, since markdown is whitespace-significant (a blockquote's
       "> " prefix, list-item nesting, etc.) -- never the HTML reference's
       `norm_ws` collapse-all-internal-whitespace treatment.
    3. Strip a trailing run of blank lines (the tail only -- an interior
       blank line, e.g. a paragraph separator, is real content and is kept).

For a `kind: "vault"` render (many files, e.g. `obsidian`), every file is
first concatenated in sorted-relative-path order, each preceded by its own
`--- <relpath> ---` header line, and the WHOLE resulting blob is what gets
line-reduced -- so a file being added/removed/renamed shows up as an
ordinary line-level mismatch (the header line itself), never a separate
structural check. (A `kind: "file"` render, e.g. a future `epub` target,
would reduce that one file directly -- not exercised yet, since `epub` has
no renderer behind it this increment.)

## Comparison

Positional, via `itertools.zip_longest` -- EVERY failure accumulates rather
than short-circuiting on the first mismatch. `difflib` renders a readable
report to stderr; the pass/fail verdict itself is exact-equality of the two
reduced line sequences, nothing fuzzier.

No anchor-map-keyed resync is implemented in this phase (an OPTIONAL
enhancement per the shared contract, so one legitimate insertion doesn't
cascade-shift every later position into a false mismatch) -- deferred, not
needed for the acceptance gate to do its job: a plain positional diff still
fails loudly on any real structural change, which is the actual
requirement (references/assembly-and-output.md: "There is no separate
item-count acceptance check anywhere in this pipeline -- the render+diff
comparison IS the gate").

## Two-tree mode (`--baseline-dir`, #589)

`--baseline-dir DIR` compares `--candidate-dir` against THAT already-rendered
tree instead of the frozen `out/.baseline/` reduction -- the same
`reduce_vault()` on both sides, the same positional `compare()`, no new
comparison logic. It exists because a project that POST-PROCESSES the rendered
vault (renaming/merging notes downstream of `render()`) has no way to ask "is
what I produced still equivalent to what the renderer wrote" WITHOUT spending
the project's single frozen baseline slot: `--accept-baseline` does accept any
`--candidate-dir` you point it at, but there is exactly one
`${durable_root}/out/.baseline/`, and overwriting it with a hand-supplied tree
destroys the reduction the pipeline's own acceptance gate compares against.

The mode is READ-ONLY: no baseline is read, written, or needed -- a project
with no `out/.baseline/` at all can use it, and one WITH a baseline still has
it byte-identical afterwards. It is mutually exclusive with
`--accept-baseline`, for the reason just given. `stale_baseline` never appears,
because there is no stored render-version to compare against; the `ok`/
`mismatch` payloads instead carry `"mode": "two_tree"`, so a consumer can never
mistake a two-tree verdict for a frozen-baseline one.

What it does NOT distinguish, because it reuses the reduction verbatim rather
than a stricter one: `reduce_vault()` flattens every file behind an ordinary
`--- <relpath> ---` line in the SAME line space as content (that is the
documented design -- an added/removed/renamed file is meant to surface as a
plain line mismatch, not a separate structural check). So two trees whose
concatenations coincide compare equal even though their file topology differs
-- e.g. one file containing the literal line `--- b.md ---` followed by `Body`,
against two files `a.md` (empty) and `b.md` (`Body`). This is a property of the
shared reducer, identical on the frozen-baseline path, not something this mode
introduces; it is called out here because this mode is aimed at trees the
renderer did not write.

## Exit codes + one-line JSON stdout `reason`

    0 = match                                   {"reason": "ok", "match": true, ...}
    1 = mismatch OR guard refusal                {"reason": "mismatch", "match": false, ...}
                                                  {"reason": "candidate_not_built", ...}
                                                  {"reason": "baseline_dir_not_found", ...}
                                                  {"reason": "baseline_exists", ...}
                                                  {"reason": "baseline_dir_is_symlink", ...}
                                                  {"reason": "out_dir_is_symlink", ...}
                                                  {"reason": "out_dir_symlink", ...}
    2 = no baseline exists yet (bootstrap state) {"reason": "no_baseline"}
                                                  {"reason": "profile_precondition", ...}

The full `reason` set is exactly {ok, mismatch, candidate_not_built,
baseline_dir_not_found, no_baseline, baseline_exists, baseline_dir_is_symlink,
out_dir_is_symlink, out_dir_symlink, profile_precondition}. Two of those are
reached only on the DEFAULT (no `--candidate-dir`) path, where this script
resolves the out_dir itself: `profile_precondition` (exit 2) when
`cache_key.load_profile` halts, and `out_dir_symlink` (exit 1) when
`output_resolve` refuses `output.destination`. Its NAME is narrower than its
meaning: the same reason is emitted for BOTH refusals that resolver makes --
a `..` traversal segment (checked first) and a symlinked path component --
because the exit carries the resolver's own message rather than re-classifying
it. Distinct from `write_baseline()`'s own `out_dir_is_symlink`, which refuses
the symlink condition from the WRITE side. `main()`'s defensive catch-all is the one JSON
line outside this set: it emits `{"success": false, "error": ...}` with no
`reason` field at all.

`--accept-baseline` freezes the current candidate's reduction as the new
baseline under `${durable_root}/out/.baseline/`; overwrite-guarded --
refuses (exit 1, `reason: "baseline_exists"`, its own distinct reason string
rather than overloading `"mismatch"`) if a baseline already exists,
unless `--force-accept-baseline` is also passed. The baseline is stamped
with a render-version hash (sha1 of this script's own bytes plus
render_obsidian.py's) so a stale-renderer baseline is detectable -- surfaced
as an informational `stale_baseline` field on a normal diff run, never
exit-code-gating on its own.
"""

import argparse
import difflib
import hashlib
import json
import os
import sys
import tempfile
from itertools import zip_longest
from pathlib import Path

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"diff_rendered_output.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside diff_rendered_output.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
BASELINE_DIR = DURABLE_ROOT / "out" / ".baseline"
BASELINE_LINES_PATH = BASELINE_DIR / "reduced.txt"
BASELINE_META_PATH = BASELINE_DIR / "meta.json"

_RENDER_VERSION_FILES = ("render_obsidian.py", "diff_rendered_output.py")


# ---------------------------------------------------------------------------
# Reduction
# ---------------------------------------------------------------------------

def reduce_markdown_lines(text):
    """The three-rule stdlib-only markdown line reduction -- see module
    docstring. Leading indentation is deliberately preserved (only trailing
    whitespace is stripped); only the TRAILING run of blank lines is
    dropped, never an interior one."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def list_vault_relpaths(root):
    """Every regular file under root, sorted-relative-path order. Hidden
    files/directories (any path component starting with ".") are skipped --
    this is what keeps a self-anchored `.baseline/` living as a sibling
    inside the SAME out_dir the vault itself is written to from ever being
    walked back into as if it were candidate content."""
    relpaths = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        relpaths.append(rel.as_posix())
    return sorted(relpaths)


def reduce_vault(root):
    """kind: "vault" reduction -- concatenate every file in sorted-relpath
    order, each preceded by its own `--- <relpath> ---` header line, then
    line-reduce the whole (references/assembly-and-output.md)."""
    blob_lines = []
    for relpath in list_vault_relpaths(root):
        blob_lines.append(f"--- {relpath} ---")
        file_text = (root / relpath).read_text(encoding="utf-8")
        blob_lines.extend(reduce_markdown_lines(file_text))
    return blob_lines


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(baseline_lines, candidate_lines):
    """Positional zip_longest compare -- accumulates every failure, never
    short-circuits on the first mismatch."""
    failures = []
    for i, (b, c) in enumerate(zip_longest(baseline_lines, candidate_lines)):
        if b is None:
            failures.append(f"line {i}: unexpected extra line in candidate: {c!r}")
        elif c is None:
            failures.append(f"line {i}: missing line (present in baseline): {b!r}")
        elif b != c:
            failures.append(f"line {i}: mismatch:\n    baseline:  {b!r}\n    candidate: {c!r}")
    return failures


def readable_diff(baseline_lines, candidate_lines):
    return "\n".join(
        difflib.unified_diff(
            baseline_lines, candidate_lines,
            fromfile="baseline", tofile="candidate", lineterm="",
        )
    )


# ---------------------------------------------------------------------------
# Baseline storage
# ---------------------------------------------------------------------------

class BaselineWriteError(Exception):
    """Raised by write_baseline() for a fail-closed out/.baseline/
    precondition (currently: the baseline directory itself is a symlink).
    Caught in `_run()` and converted to this module's own
    `emit(exit_code, reason, extra)` one-JSON-line contract -- mirrors
    render_obsidian.py's `RenderError`."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _baseline_lines_content(lines):
    # A plain join-with-"\n" would round-trip an EMPTY `lines` list back as
    # [""] (one blank line) rather than [] on read via .splitlines() -- the
    # explicit empty-content special case below is what keeps write/read
    # symmetric for that edge case.
    content = "\n".join(lines)
    if content:
        content += "\n"
    return content


def _atomic_write_in_baseline_dir(target_path, content):
    """No-follow atomic write for a file inside BASELINE_DIR (review round
    5, [BLOCKER] -- same class as render_obsidian.py's round-4
    `_stamp_vault_marker` fix): `tempfile.mkstemp` creates the temp file
    with O_CREAT|O_EXCL under a securely-randomized name -- it can never
    follow or reuse anything already sitting at a path, unlike a plain
    `Path.write_text()` to a predictable name. Writing through the SAME fd
    `mkstemp` returned means no second open/path-lookup ever happens
    either. `os.replace(tmp, target_path)` then always replaces whatever
    directory entry currently sits at the destination -- symlink or
    regular file -- rather than following it, so a planted
    `reduced.txt`/`meta.json` symlink can never get clobbered-through to
    an external file. A non-dot prefix ("lt-baseline-tmp-") keeps a rare
    crash-leftover visually distinct from real baseline content, though
    (unlike render_obsidian's managed out_dir) nothing automatically
    sweeps `.baseline/`'s own contents -- this directory is only ever
    touched by this script itself."""
    fd, tmp_name = tempfile.mkstemp(dir=str(BASELINE_DIR), prefix="lt-baseline-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, target_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_baseline_lines(path):
    content = path.read_text(encoding="utf-8")
    if content == "":
        return []
    if content.endswith("\n"):
        content = content[:-1]
    return content.split("\n")


def _render_version_hash():
    parts = []
    for name in _RENDER_VERSION_FILES:
        p = SCRIPTS_DIR / name
        if p.is_file():
            parts.append(p.read_bytes())
    return hashlib.sha1(b"\x00".join(parts)).hexdigest()


def baseline_exists():
    """True if ANY prior baseline content is already on disk -- review
    round 1: the overwrite guard previously keyed on meta.json alone, so a
    baseline with reduced.txt but no meta.json (e.g. an interrupted/partial
    prior --accept-baseline) got silently overwritten. Checked broadly:
    either stamped file present, or the baseline dir simply non-empty."""
    if BASELINE_META_PATH.is_file() or BASELINE_LINES_PATH.is_file():
        return True
    return BASELINE_DIR.is_dir() and any(BASELINE_DIR.iterdir())


def write_baseline(candidate_lines):
    """Freezes candidate_lines as the new accepted baseline. `.baseline/`
    is a PRESERVED dotfile (render_obsidian.py's `_clean_vault_content`
    keeps every dotfile entry), so a planted `out/.baseline -> /external`
    symlink would otherwise survive clean-render indefinitely and, before
    this fix, `BASELINE_DIR.mkdir(exist_ok=True)` would succeed against it
    (a symlink-to-dir "exists") and both writes below would follow it out
    of the vault (review round 5, [BLOCKER] -- the exact class of bug
    `render_obsidian.py`'s marker fixes closed for its own dotfile). The
    symlink checks below run BEFORE mkdir, for the same reason `render()`'s
    own out_dir symlink guard runs before its mkdir.

    Review round 6, [BLOCKER]: the round-5 fix only checked `.baseline/`
    itself -- but the SAME vector applies one level up, at `BASELINE_DIR.
    parent` (`${durable_root}/out`). A planted `${durable_root}/out ->
    /external` symlink is never touched by render_obsidian.py's own
    clean-render (that only ever runs INSIDE an already-resolved out_dir,
    never checks its own parent), so `BASELINE_DIR.mkdir(parents=True)`
    would happily create `.baseline/` inside the external target and both
    writes below would land there. Checked first, using the SAME reason
    `render_obsidian.py` already uses for its own out_dir-is-a-symlink
    guard (`out_dir_is_symlink`) -- it's the identical condition, just
    reached from this script's own entry point instead of render()'s."""
    if BASELINE_DIR.parent.is_symlink():
        raise BaselineWriteError(
            "out_dir_is_symlink",
            f"refusing to write the baseline: {BASELINE_DIR.parent} (out_dir) "
            "is a symlink -- writing through it could clobber files OUTSIDE "
            "the vault that the symlink points at; point output.destination "
            "at a real directory instead",
        )
    if BASELINE_DIR.is_symlink():
        raise BaselineWriteError(
            "baseline_dir_is_symlink",
            f"refusing to write the baseline: {BASELINE_DIR} is a symlink -- "
            "writing through it could clobber a file OUTSIDE the vault that "
            "the symlink points at; point output.destination at a real "
            "directory instead",
        )
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_in_baseline_dir(BASELINE_LINES_PATH, _baseline_lines_content(candidate_lines))
    meta = {"render_version": _render_version_hash(), "line_count": len(candidate_lines)}
    _atomic_write_in_baseline_dir(BASELINE_META_PATH, json.dumps(meta, indent=2) + "\n")


def read_stored_render_version():
    if not BASELINE_META_PATH.is_file():
        return None
    try:
        meta = json.loads(BASELINE_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return meta.get("render_version") if isinstance(meta, dict) else None


# ---------------------------------------------------------------------------
# Candidate-dir resolution -- via output_resolve.py, the target-agnostic
# out_dir default rule. This script deliberately never imports
# render_obsidian (or any other adapter module): the diff tool must stay
# target-agnostic, since a later `epub` render (kind: "file") is diffed by
# this exact same script, not a fork of it.
# ---------------------------------------------------------------------------

def resolve_default_candidate_dir():
    """Self-resolves the SAME out_dir assemble.py would have just rendered
    into, via profile.yml's output.destination -- reusing
    output_resolve.resolve_out_dir(profile, durable_root) rather than
    duplicating that rule a second time. Requires the durable-root
    ownership marker (Step 0a) to already exist, same as any other
    real-pipeline script; a standalone/test invocation should pass
    --candidate-dir explicitly instead of exercising this path against a
    hand-built fixture with no real profile.yml behind it."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import cache_key        # flat sibling import -- the existing profile.yml loader
    import output_resolve   # flat sibling import -- the shared, target-agnostic out_dir default rule

    profile = cache_key.load_profile(DURABLE_ROOT)  # fail()s/exits internally on any problem
    return output_resolve.resolve_out_dir(profile, DURABLE_ROOT)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def emit(exit_code, reason, extra=None):
    payload = {"reason": reason}
    if extra:
        payload.update(extra)
    print(dumps_line(payload))
    return exit_code


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="diff_rendered_output.py",
        description=(
            "Render+diff acceptance gate: reduces the candidate rendered "
            "output and compares it against the last --accept-baseline'd "
            "reduction. See references/assembly-and-output.md's 'Render + "
            "diff' section for the full spec."
        ),
    )
    parser.add_argument(
        "--candidate-dir", type=Path, default=None,
        help=(
            "Root of the already-rendered candidate output (default: "
            "resolved from profile.yml's output.destination, the same "
            "out_dir assemble.py just rendered into)."
        ),
    )
    # Two-tree mode and baseline-freezing are mutually exclusive by
    # construction, not by a hand-rolled check: freezing a hand-supplied
    # --baseline-dir as THE baseline would silently discard the project's own
    # accepted reduction. argparse's own usage error (exit 2, usage text on
    # stderr) is the established non-JSON exit for a CLI misuse here.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--baseline-dir", type=Path, default=None,
        help=(
            "Two-tree mode: compare --candidate-dir against THIS "
            "already-rendered tree instead of the frozen out/.baseline/ "
            "reduction. Read-only -- no baseline is read, written, or needed."
        ),
    )
    mode.add_argument(
        "--accept-baseline", action="store_true",
        help="Freeze the current candidate's reduction as the new baseline.",
    )
    parser.add_argument(
        "--force-accept-baseline", action="store_true",
        help="With --accept-baseline, overwrite an existing baseline instead of refusing.",
    )
    return parser


def main(argv=None):
    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit:
        # argparse's own usage-error/--help exit -- usage text is already on
        # stderr, and this is the one INTENTIONAL non-JSON exit (review
        # round 3): standard CLI usage behavior, never converted to a JSON
        # envelope. Re-raise unchanged, never double-printed.
        raise

    # Everything below is wrapped so an unexpected failure (a permissions
    # error reading a candidate file, an unwritable baseline dir, a
    # poisoned profile shape reached through resolve_default_candidate_dir,
    # etc.) still surfaces as one JSON line on stdout, exit 1 -- never a
    # bare traceback/stderr-only exit (review round 2; mirrors assemble.py's
    # own main()-level catch-all exactly). The already-anticipated outcomes
    # below (candidate_not_built/baseline_exists/no_baseline/mismatch/ok)
    # each return explicitly and never reach this catch-all.
    try:
        return _run(args)
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(dumps_line({"success": False, "error": f"unexpected error: {exc}"}))
        return 1


def _run(args):
    if args.candidate_dir is not None:
        candidate_dir = args.candidate_dir
    else:
        # resolve_default_candidate_dir() -> cache_key.load_profile() can
        # sys.exit() on a bad/missing profile.yml -- previously this
        # escaped as a bare stderr-only fatal (review round 3); never let
        # a profile precondition fall through this script's own one-
        # JSON-line contract just because the failure happened one
        # function down.
        sys.path.insert(0, str(SCRIPTS_DIR))
        import output_resolve  # flat sibling import -- for its OutputResolveError; resolve_out_dir now rejects a symlinked/'..' destination
        try:
            candidate_dir = resolve_default_candidate_dir()
        except SystemExit as exc:
            print(dumps_line({
                "success": False,
                "reason": "profile_precondition",
                "error": f"profile.yml failed to load/validate via cache_key.load_profile (exit {exc.code})",
            }))
            return 2
        except output_resolve.OutputResolveError as exc:
            # output.destination reached through a symlinked path component
            # (or containing a '..' segment) -- refuse rather than follow it
            # outside the declared destination (bot blocker #4).
            return emit(1, "out_dir_symlink", {"detail": str(exc)})

    if not candidate_dir.is_dir():
        return emit(1, "candidate_not_built", {"candidate_dir": str(candidate_dir)})

    candidate_lines = reduce_vault(candidate_dir)

    if args.baseline_dir is not None:
        # Two-tree mode (#589): both sides go through the SAME reduce_vault()
        # the frozen-baseline path uses, so a post-processed tree is judged by
        # exactly the rule that froze the baseline it is being compared to.
        # BASELINE_LINES_PATH is never read and write_baseline() is never
        # reached, so an absent (or disagreeing) out/.baseline/ cannot affect
        # this verdict, and no `stale_baseline` field is emitted -- there is no
        # stored render-version to compare against.
        if not args.baseline_dir.is_dir():
            return emit(1, "baseline_dir_not_found", {"baseline_dir": str(args.baseline_dir)})
        baseline_lines = reduce_vault(args.baseline_dir)
        failures = compare(baseline_lines, candidate_lines)
        two_tree = {"mode": "two_tree", "baseline_dir": str(args.baseline_dir)}
        if failures:
            report = readable_diff(baseline_lines, candidate_lines)
            if report:
                print(report, file=sys.stderr)
            for f in failures:
                print(f"FAIL: {f}", file=sys.stderr)
            return emit(1, "mismatch", {**two_tree, "match": False, "failures": len(failures)})
        return emit(0, "ok", {**two_tree, "match": True, "lines": len(candidate_lines)})

    if args.accept_baseline:
        if baseline_exists() and not args.force_accept_baseline:
            return emit(1, "baseline_exists", {
                "detail": (
                    f"a baseline already exists at {BASELINE_DIR} -- pass "
                    "--force-accept-baseline to overwrite it"
                ),
            })
        try:
            write_baseline(candidate_lines)
        except BaselineWriteError as exc:
            return emit(1, exc.reason, {"detail": str(exc)})
        return emit(0, "ok", {"accepted": True, "lines": len(candidate_lines)})

    if not BASELINE_LINES_PATH.is_file():
        return emit(2, "no_baseline")

    baseline_lines = _read_baseline_lines(BASELINE_LINES_PATH)
    failures = compare(baseline_lines, candidate_lines)

    if failures:
        report = readable_diff(baseline_lines, candidate_lines)
        if report:
            print(report, file=sys.stderr)
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return emit(1, "mismatch", {"match": False, "failures": len(failures)})

    extra = {"match": True, "lines": len(candidate_lines)}
    stored_version = read_stored_render_version()
    if stored_version is not None and stored_version != _render_version_hash():
        extra["stale_baseline"] = True
        print(
            "WARN: the accepted baseline was frozen under a different "
            "render_obsidian.py/diff_rendered_output.py version -- consider "
            "--accept-baseline --force-accept-baseline after reviewing the "
            "diff by hand.",
            file=sys.stderr,
        )
    return emit(0, "ok", extra)


if __name__ == "__main__":
    sys.exit(main())
