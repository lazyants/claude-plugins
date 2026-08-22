# pytest collection scope and environment-dependent totals (literary-translator)

## `pytest plugins/` silently collects 67 of 5321 tests and exits 0

Run the suites per plugin, never from the repo root: `pytest plugins/literary-translator/`
(5254 tests) and `pytest plugins/cc-usage-coach/` (67). There is no root `pytest.ini`/`conftest.py`.

`pytest plugins/` collects **67 tests in 0.2s and exits 0** — the 142 `*.test.py` files are all
invisible. This repo names its tests `*.test.py`, which matches neither of pytest's default
`python_files` patterns (`test_*.py`, `*_test.py`); the pattern comes from
`plugins/literary-translator/pytest.ini`, which pytest loads **only when the invocation's rootdir
resolves into that directory**. From the repo top the ini is never read, so only `cc-usage-coach`'s
6 `test_*.py` files match. Passing a `.test.py` file explicitly always works, which is why
single-file runs never expose it.

Why it bites: a near-empty collection is indistinguishable from a passing full run — same dots,
same exit 0. **The collected COUNT is the only tell**, so read it before banking any green.

## `python3 -O -m pytest` turns a suite into a green no-op

Measured on `plugins/literary-translator` 2026-08-16, during the triage of #315.

`pytest` implements its rich assertion output by **rewriting** `assert` statements at import time.
That rewriting does not survive `-O`: CPython strips `assert` under optimization before pytest's
machinery matters, and pytest emits only a warning — *"assertions not in test modules or plugins will
be ignored"* — which reads like it is about helper modules and is not.

**Control, built with the project's own `pytest.ini`:** a `*.test.py` containing
`assert False, "this must fail"` FAILS under `python3 -m pytest` and PASSES under
`python3 -O -m pytest`.

**On the real suite, current main:** `python3 -O -m pytest` → **91 failed, 5562 passed**. The 5562
passes assert nothing — 11 567 `Assert` statements across 159 of 160 test modules are inert. The 91
REDs are only the residue where a stripped assert carried a side effect, or a narrowing the next
line depended on.

**How to apply:**

- **Never read a `-O` run as a weaker version of a normal run.** It is not "58 tests fail under
  optimization"; it is "the suite stopped testing and 91 tests noticed".
- A ticket framed as *"N tests fail under `-O`"* has its premise backwards, and fixing the N is the
  wrong work — the number to care about is the passes.
- The inverse question is separate and worth asking on its own: **does any shipped script carry a
  bare fail-closed `assert`?** In this plugin, no — an AST sweep of all 47 shipped `.py`/`.py.template`
  files finds 7 `Assert` nodes, every one narrowing after a dependency check, with the anti-bare-assert
  convention written out at `fetch_citation.py:806` and `skeptic_report.py:517`.
- Same family as: a check that runs zero times prints exactly what a passing one prints.

## An absolute suite total is a fact about the MACHINE that ran it — a `--collect-only` delta is not

Release copy that states an absolute suite total (`N passed, M skipped`) is asserting a property of
the LAPTOP, not of the release. Any `skipif` gated on an external binary or a privilege —
`shutil.which("node")`, `git`, `fcntl.flock`, `os.geteuid() == 0`, symlink support in the sandbox —
shifts BOTH the passed and skipped counts on a leaner host, so a reviewer running elsewhere reads a
different true number and flags yours as wrong. Measured in this repo:
`plugins/literary-translator/tests/` has ~15 such gates.

**The rule is about WHICH PHASE the gate suppresses, not about `--collect-only`.** A count is
portable when the gate suppresses EXECUTION and not portable when it suppresses COLLECTION. `skipif`
is the well-behaved one: collected everywhere, executed conditionally, so a `--collect-only` delta
("29 vs 20 in this file, against the previous release") is the same number on every host while
"5840 passed" is not. Three constructs break it by raising or filtering during collection, and a
`--collect-only` count is then just as host-dependent as a run total:

- `pytest.importorskip(...)` at module scope
- `pytest.skip(..., allow_module_level=True)`
- `collect_ignore` / `collect_ignore_glob` in a `conftest.py`

Verified 2026-08-18 that `plugins/literary-translator/` contains none of the three (and has no
`conftest.py` at plugin or tests level), which is what makes its collection deltas portable — check
that before leaning on this, since it is a property of the suite, not of pytest.

Do NOT retro-edit a shipped entry to fix this: the number was true when measured and is labeled as a
measurement, and the whole changelog uses that convention. Fix forward — the next entry names the
environment beside the total, or states the delta instead.
