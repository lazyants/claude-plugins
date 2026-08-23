"""tests/json_stdout_contract.test.py -- #369: the three properties the shared
one-line serialiser is FOR, driven end to end rather than asserted about source.

`tests/stdout_json_line_escape_gate.test.py` is the static half: it proves no
script still spells the raw idiom. This file is the behavioural half, and the
two prove different things -- a tree can pass the gate while the helper itself
is wrong, or while a durable root that lacks the helper runs green against a
cached one.

Three properties:

1. **A real CLI emits ONE physical line** even when its payload carries U+2028.
   Driven through a shipped script's actual `main()` in a subprocess, not through
   `dumps_line` in-process: the escape is only worth anything if it is on the
   path the CLI really takes.

2. **A staged root WITHOUT `json_stdout.py` fails loudly**, and does so even
   under hostile conditions -- with the live helper already in `sys.modules` and
   the plugin's own scripts directory on `sys.path`. That is the exact property
   the exact-path loader was chosen for over a bare `import json_stdout`, and
   without this test the ~70 fixture staging edits #369 made are unfalsifiable:
   a missed one would simply bind the live copy and pass.

3. **`dumps_line` owns `ensure_ascii`** and preserves every other `json.dumps`
   option, because four call sites pass one.

The escape's COMPLETENESS is not re-proved here. `tests/skeptic_ready.test.py`
already brute-force scans 0x0-0x10FFFF against the same
`LINE_SEPARATOR_ESCAPES` this module now owns, and a second, weaker restatement
of that scan would be a place for the two to disagree.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
JSON_STDOUT_SRC = SCRIPTS_DIR / "json_stdout.py"
assert JSON_STDOUT_SRC.is_file(), f"json_stdout.py not found at {JSON_STDOUT_SRC}"

# Authored with chr(), never a pasted glyph and never a "\uXXXX" string literal:
# both have silently degraded into something else in this repo before (see the
# unicode-boundary-text-authoring project skill).
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
NEL = chr(0x85)


def _load_helper():
    spec = importlib.util.spec_from_file_location("json_stdout_under_test", JSON_STDOUT_SRC)
    assert spec is not None and spec.loader is not None, f"could not load {JSON_STDOUT_SRC}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. End to end through a shipped CLI
# ---------------------------------------------------------------------------

def test_a_real_cli_emits_one_physical_line_when_its_payload_carries_u2028(tmp_path):
    """`diff_rendered_output.py` names the candidate directory it was given in
    its refusal payload, so a directory whose NAME carries U+2028 puts the
    character on the real stdout path with no fixture scaffolding at all.

    Measured against the pre-#369 script (base 0ce3686), this exact invocation
    printed `len(stdout.splitlines()) == 2` with a RAW U+2028 in it, against ONE
    non-empty newline-delimited record -- `len(stdout.rstrip(chr(10)).split(
    chr(10))) == 1`. Stated that precisely on purpose: the looser
    `stdout.split(chr(10))` is 2 as well, because `print()` adds a trailing
    newline, so an earlier wording of this sentence was simply wrong about the
    number it quoted. The disagreement that matters is between the ONE record
    the writer emitted and the TWO physical lines a `splitlines()`-shaped
    reader sees.

    A path is a legitimate operational carrier, not a contrived one: nothing in
    this plugin constrains the characters in an operator's durable-root or
    output path, and the same payload shape carries model-authored `source_form`
    and rationale text elsewhere.
    """
    weird_dir = tmp_path / ("candidate" + LINE_SEPARATOR + "dir")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "diff_rendered_output.py"),
         "--candidate-dir", str(weird_dir)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    assert len(proc.stdout.splitlines()) == 1, (
        "the CLI emitted more than one PHYSICAL line -- a boundary character "
        f"reached stdout raw. splitlines() saw {len(proc.stdout.splitlines())} "
        f"lines, split(chr(10)) saw {len(proc.stdout.split(chr(10)))}. stdout:\n"
        f"{proc.stdout!r}"
    )
    assert LINE_SEPARATOR not in proc.stdout, (
        "a raw U+2028 is still on stdout; it must be emitted as the \\u2028 escape"
    )
    # ...and the escape is a WIRE change only: the decoded value is unchanged.
    payload = json.loads(proc.stdout)
    assert payload["candidate_dir"] == str(weird_dir), (
        "the escape changed the decoded value -- it must round-trip the path "
        "character for character"
    )


# ---------------------------------------------------------------------------
# 2. A staged root without the helper fails LOUDLY -- under hostile conditions
# ---------------------------------------------------------------------------

_STAGED_CLI = "cache_key.py"


def _stage(dest: Path, with_helper: bool) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    script = dest / _STAGED_CLI
    script.write_bytes((SCRIPTS_DIR / _STAGED_CLI).read_bytes())
    if with_helper:
        (dest / "json_stdout.py").write_bytes(JSON_STDOUT_SRC.read_bytes())
    return script


def _run_staged(script: Path, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, timeout=120, env=env,
    )


def test_a_staged_script_without_the_helper_refuses_even_with_the_live_one_reachable(tmp_path):
    """THE property the exact-path loader exists for.

    The axis here is the one a SUBPROCESS can actually be hostile on: the
    plugin's own scripts directory is handed to the staged run on `PYTHONPATH`,
    so a bare `import json_stdout` would be satisfied from the wrong tree by a
    fresh interpreter. The staged script must STILL refuse, because it loads
    `json_stdout.py` by the exact path beside itself and nothing else. If this
    test ever goes green in the `without` case, every one of #369's ~70 fixture
    staging edits silently stopped meaning anything.

    The OTHER axis -- a poisoned in-process `sys.modules` -- deliberately is not
    here: a parent's module cache never reaches its subprocess, so poisoning it
    around this test would assert nothing. `test_two_staged_roots_in_one_process
    _each_get_their_own_helper` covers that axis in-process, where it is real.
    """
    hostile_env = {"PYTHONPATH": str(SCRIPTS_DIR)}

    without = _stage(tmp_path / "no_helper", with_helper=False)
    refused = _run_staged(without, hostile_env)
    assert refused.returncode != 0, (
        "a staged scripts/ directory MISSING json_stdout.py ran successfully. "
        "The loader resolved the helper from somewhere else -- so a fixture "
        "that forgets to stage it passes green, and the staging discipline "
        f"is not being enforced at all.\nstdout:\n{refused.stdout}"
    )
    combined = refused.stdout + refused.stderr
    assert "json_stdout.py" in combined and "alongside" in combined, (
        "the refusal must name the missing file and where it belongs, so an "
        f"operator can act on it. Got:\n{combined}"
    )

    # ...and the SAME staging with the sibling present must succeed, so the
    # assertion above is about the missing helper and not about the fixture
    # being broken in some other way.
    with_helper = _stage(tmp_path / "with_helper", with_helper=True)
    ok = _run_staged(with_helper, hostile_env)
    assert ok.returncode == 0, (
        "the staged script failed even WITH json_stdout.py beside it -- the "
        f"negative case above proves nothing.\nstdout:\n{ok.stdout}\n"
        f"stderr:\n{ok.stderr}"
    )


def test_two_staged_roots_in_one_process_each_get_their_own_helper(tmp_path):
    """The other half of the loader's job, and the half the subprocess test
    above CANNOT reach: `_run_staged()` launches a fresh interpreter, which
    never sees this process's `sys.modules`, so poisoning the parent proves
    nothing about caching.

    This one stays IN-PROCESS and drives a REAL routed script -- cache_key.py,
    whose only module-level work is stdlib imports plus the loader itself --
    staged into two roots whose helpers differ in an observable way. Loading the
    SCRIPT, not the helper, is what makes this a test of the production loader:
    a rewrite of cache_key.py's loader to a bare `import json_stdout` binds
    whatever `sys.modules` already holds and both roots then read one helper,
    which is exactly the assertion below.

    The obvious hardening against that (assert the imported helper's `__file__`
    sits beside the script) would instead REJECT the second root although its
    own sibling is present. That false rejection is real in this suite:
    `tests/validate_assembled.test.py` path-loads copied modules against three
    separate `tmp_path` roots in one process.

    The exact-path loader has no `sys.modules` key to collide on, so each root
    executes its own file.
    """
    import importlib.util as ilu

    roots = []
    for index in (1, 2):
        root = tmp_path / f"root{index}"
        root.mkdir()
        # Same behaviour, distinguishable identity: a per-root marker constant.
        (root / "json_stdout.py").write_text(
            JSON_STDOUT_SRC.read_text(encoding="utf-8")
            + f'\n\nSTAGED_ROOT_MARKER = "root{index}"\n',
            encoding="utf-8",
        )
        (root / "cache_key.py").write_bytes(
            (SCRIPTS_DIR / "cache_key.py").read_bytes()
        )
        roots.append(root)

    # Both halves of what a bare import would find: the cache already populated
    # with the LIVE helper (which carries no marker at all), and root1 on
    # sys.path so a bare import would resolve there on a cold cache too.
    # Executing a real script also runs ITS module-level statements, and
    # cache_key.py sets `sys.dont_write_bytecode = True`. That is process-global
    # and would otherwise outlive this test, silently disabling bytecode writes
    # for every later in-process import in this pytest worker.
    _prior_dont_write = sys.dont_write_bytecode
    _prior_helper = sys.modules.get("json_stdout")
    sys.modules["json_stdout"] = _load_helper()
    sys.path.insert(0, str(roots[0]))
    try:
        loaded = []
        for index, root in enumerate(roots, start=1):
            spec = ilu.spec_from_file_location(
                f"cache_key_root{index}", root / "cache_key.py"
            )
            assert spec is not None and spec.loader is not None
            module = ilu.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(module)

        markers = [
            getattr(m._json_stdout, "STAGED_ROOT_MARKER", None) for m in loaded
        ]
        assert markers == ["root1", "root2"], (
            f"each staged cache_key.py must load the helper beside ITSELF; got "
            f"{markers!r}. A None means the script resolved the process-wide "
            f"module instead of its sibling; a repeated 'root1' means the "
            f"second root got the first root's helper through a shared cache -- "
            f"either way a fixture staging several roots in one process would "
            f"silently share one helper."
        )
        for module, root in zip(loaded, roots):
            assert Path(module._json_stdout.__file__).parent == root, (
                f"{module._json_stdout.__file__} is not the copy beside {root}"
            )
        # ...and the poisoned cache entry was never consulted or overwritten.
        assert sys.modules["json_stdout"].__file__ == str(JSON_STDOUT_SRC), (
            "the exact-path load mutated the global module cache; it must not "
            "register anything, or two staged roots start fighting over the key"
        )
    finally:
        sys.path.remove(str(roots[0]))
        # Restore, never just delete: popping would destroy a binding some
        # other test in this worker had legitimately put there.
        if _prior_helper is None:
            sys.modules.pop("json_stdout", None)
        else:
            sys.modules["json_stdout"] = _prior_helper
        # No `cache_key_root*` cleanup: module_from_spec/exec_module never
        # registers, so a pop here could only delete somebody else's binding.
        sys.dont_write_bytecode = _prior_dont_write


def test_step_0a_copy_pass_would_carry_the_helper():
    """The durable root gets the helper because SKILL.md's Step 0a copy pass is
    a GLOB over `assets/scripts/*.py` with three named exclusions, not a
    hand-list -- so unlike this suite's fixtures it needed no edit. Pinned
    because that is the only reason a real run is safe, and a future narrowing
    of the copy pass to an enumeration would break every durable root silently
    at the next scaffold rather than here."""
    skill_md = (PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "every file in `assets/scripts/*.py` (except" in skill_md, (
        "Step 0a's copy pass is no longer described as a glob over "
        "assets/scripts/*.py -- json_stdout.py may no longer reach a durable root"
    )
    assert "json_stdout.py" not in skill_md.split("(except", 1)[1][:600], (
        "json_stdout.py has been added to Step 0a's copy-pass EXCLUSIONS -- every "
        "durable script that loads it would exit instead of running"
    )


# ---------------------------------------------------------------------------
# 3. The helper's own contract
# ---------------------------------------------------------------------------

def test_dumps_line_escapes_all_three_boundary_characters_json_leaves_raw():
    helper = _load_helper()
    for name, char in (("U+0085 NEL", NEL),
                       ("U+2028 LINE SEPARATOR", LINE_SEPARATOR),
                       ("U+2029 PARAGRAPH SEPARATOR", PARAGRAPH_SEPARATOR)):
        payload = {"source_form": "before" + char + "after"}
        out = helper.dumps_line(payload)
        assert len(out.splitlines()) == 1, f"{name} still forges a second line: {out!r}"
        assert char not in out, f"{name} is still raw in the output: {out!r}"
        assert json.loads(out) == payload, (
            f"{name}'s escape changed the decoded value -- the escape is a wire "
            "change only"
        )


def test_dumps_line_refuses_ensure_ascii():
    """Owned by the function, not the caller: `True` would escape the Hebrew,
    Yiddish and Russian payloads a reading agent has to understand, and `False`
    passed explicitly implies a caller may choose."""
    helper = _load_helper()
    import pytest
    for value in (True, False):
        with pytest.raises(TypeError, match="ensure_ascii"):
            helper.dumps_line({"a": 1}, ensure_ascii=value)


def test_dumps_line_preserves_every_other_json_dumps_option():
    """`**kwargs` is load-bearing, not generality: four shipped call sites pass a
    non-default option and would change behaviour without it."""
    helper = _load_helper()
    payload = {"b": 1, "a": 2}
    assert helper.dumps_line(payload, indent=1) == json.dumps(payload, ensure_ascii=False, indent=1)
    assert helper.dumps_line(payload, indent=2, sort_keys=False) == json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=False
    )
    assert helper.dumps_line(payload, separators=(",", ":")) == json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    assert helper.dumps_line(payload, allow_nan=False) == json.dumps(
        payload, ensure_ascii=False, allow_nan=False
    )
    # non-Latin content survives verbatim -- the whole reason ensure_ascii=False
    # is the base behaviour rather than an option
    hebrew = {"source_form": "רבי"}
    assert json.loads(helper.dumps_line(hebrew)) == hebrew
    assert "ר" in helper.dumps_line(hebrew)


def test_the_escape_set_is_derived_and_not_hand_listed():
    """1.16.2's round-5 finding, kept: a hand-typed two-member set (U+2028 and
    U+2029 only) silently missed NEL, which `str.splitlines()` also breaks on.
    The set must be computed from the real predicate -- 'does json.dumps leave
    this raw' -- so it stays correct if a future Python changes what it escapes."""
    helper = _load_helper()
    expected = {
        ch for ch in helper._SPLITLINES_BOUNDARY_CANDIDATES
        if ch in json.dumps(ch, ensure_ascii=False)
    }
    assert set(helper.LINE_SEPARATOR_ESCAPES) == expected
    assert expected == {NEL, LINE_SEPARATOR, PARAGRAPH_SEPARATOR}, (
        "the set of characters json.dumps(ensure_ascii=False) leaves raw has "
        f"changed under this Python: {sorted(hex(ord(c)) for c in expected)}"
    )
    # every candidate below 0x20 is already escaped by json.dumps itself -- that
    # is WHY the derived set is three members and not ten.
    for ch in helper._SPLITLINES_BOUNDARY_CANDIDATES:
        if ord(ch) < 0x20:
            assert ch not in expected, f"{hex(ord(ch))} does not need an escape from us"
