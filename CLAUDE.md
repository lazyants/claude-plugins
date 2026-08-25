# claude-plugins — working rules

## Run the suites in CI, not on this laptop

Every plugin suite in this repo runs in **GitHub Actions** (`.github/workflows/<plugin>.yml`).
The repo is public, so Actions on standard runners costs nothing and has no minute budget to
ration. **Do not run a full suite locally.** `literary-translator` alone is ~6 200 pytest cases
and `enduser-handbook`'s `reference-assets.test.sh` is a 5 500-line bash suite; a full local run
drains the battery and wedges a machine that other sessions and agents are sharing.

Split of duty:

- **Local** — only the one test file that covers the change, plus cheap host-side static checks
  (`sh -n`, `node --check`, `jq -e . <file>`, `actionlint`).
- **CI** — everything broad: the whole plugin suite, every plugin, both runtimes.

Push the branch (or open the PR) and read the run:

```sh
gh run list --branch "$(git branch --show-current)" --limit 5
gh run view <run-id> --log-failed        # or: gh run watch <run-id>
gh workflow run literary-translator.yml --ref main   # manual, once the workflow is on main
```

A green run on the branch head is sufficient proof, and waiting for it is not a stall. Scope the
work, never the coverage: skipping a suite locally is right, skipping it remotely is not.

## What CI runs

| Workflow | Suite it drives | Runtimes installed |
| --- | --- | --- |
| `ai-cli-optout.yml` | `tests/run-all.sh` | bash, jq, curl (preinstalled) |
| `cc-usage-coach.yml` | `tests/run-all.sh` (pytest) | Python 3.14 |
| `db-guardrails.yml` | `tests/block-destructive-db.test.sh` | bash, python3, jq (preinstalled) |
| `enduser-handbook.yml` | `node --test tests/*.test.mjs`, then `tests/reference-assets.test.sh` | Node 22, ruby (preinstalled), esbuild (best-effort) |
| `literary-translator.yml` | `python3 -m pytest -q`, run **from the plugin directory** | Python 3.14 + `requirements.txt`, Node 22 |
| `skill-frontmatter.yml` | `tests/skill-frontmatter-limits.test.rb` | ruby (preinstalled) |
| `citation-audit.yml` | `tools/tests` (pytest), then `tools/citation_audit.py check` over the tree | Python 3.14 (stdlib only) |
| `version-surfaces.yml` | `.claude/skills/plugin-repo-mechanics/scripts/check_version_surfaces.test.py`, then the checker itself over the tree | Python 3.14 (stdlib only) |

`multi-profile-plugins` and `obsidian-project-vault` ship no tests, so they have no suite of their
own — `version-surfaces.yml` still covers their release surfaces, as it does every plugin's.

The five plugin workflows are each path-filtered to their own plugin plus their own file, so a PR
touching one plugin runs one suite; `workflow_dispatch` runs any of them by hand. Superseded runs on
the same ref are cancelled, so only the newest commit's run gates anything.

Three workflows deliberately reach outside one plugin directory, and the rule behind all three:
`enduser-handbook.yml` also lists the root `README.md` and `CHANGELOG.md`, because
`reference-assets.test.sh` reads both directly and pins release copy in them; `version-surfaces.yml`
is repo-wide by nature — its checker compares every plugin's manifest against
`.claude-plugin/marketplace.json`, the README's row and section, and the changelog, so its filter
lists all of those plus the scripts directory the checker lives in; and `skill-frontmatter.yml` is
repo-wide for the same reason — it walks `.claude/skills/**` and `plugins/*/skills/**`, so its
filter lists both roots, and it lists them as exact paths too, because a file or symlink created
AT `plugins/<name>/skills` is a path the walker stats and refuses by name. **A path filter must
cover every file the suite READS, not only the directory it lives in** — otherwise a PR editing
just that file merges with the suite never scheduled, which is exactly the hole that "CI replaces
the local run" is supposed to close. No other suite reaches outside its own plugin directory
(literary-translator's changelog tests read `PLUGIN_ROOT/CHANGELOG.md`, its own).

`citation-audit.yml` carries NO `paths:` filter at all, and that is the same rule taken to its
conclusion: its suite reads every tracked text file in the repo, so any path filter would be a lie.
It also asserts a non-zero collected test count before running the gate — `tools/tests` holds
`test_*.py`, not this repo's `*.test.py`, because no root pytest config declares that pattern and a
`*.test.py` file there would be collected by nothing, reported "no tests ran", and exited 0.

`version-surfaces.yml` is the one workflow whose `pull_request` trigger is scoped to `branches:
[main]`: its checker's baseline is `origin/main`, which is the right comparison only for a PR that
publishes. A stacked PR based on another branch may legitimately sit behind `main`, and its parent's
PR to `main` is where the check has to pass.

Runtime versions are pinned to mirror this machine (Python 3.14, Node 22) — a CI result is then
directly comparable to what a local run would have produced, which is the point of not running one.

### Things the workflows deliberately do

- `literary-translator` runs pytest with `working-directory: plugins/literary-translator`. From the
  repo root, pytest never reads that plugin's `pytest.ini`, so `python_files = *.test.py` does not
  apply, a fraction of the suite is collected, and the run exits 0 — a green that means nothing.
- `enduser-handbook` passes **explicit file paths** to `node --test`. A bare directory positional is
  treated as a script entry point and reports a bogus `1/0/1` with a misleading `MODULE_NOT_FOUND`.
- `enduser-handbook`'s toolchain step fails loudly if `node` or `ruby -ryaml -rjson` is missing.
  Several gates in that suite print `SKIPPED` and still pass, and a skipped gate reads exactly like
  a clean one. `esbuild` is the one genuinely optional tool (the suite never network-fetches it).
- `literary-translator` installs Node even though its suite is Python: a dozen test files are
  `skipif(NODE is None)` and would silently not execute.
- `literary-translator` checks out with `fetch-depth: 0`. Its `retired_wording_pins` and
  `wait_chunking_batch_passes` tests resolve frozen baseline commits with `git show`/`git diff`;
  on the default depth-1 shallow clone all 67 of them fail with `bad object`, saying nothing
  about the code. History is ~14 MiB packed, so the full clone is cheap.
- The pytest step runs `-n auto --dist loadfile` (pytest-xdist, CI-only — it is not in
  `requirements.txt`). Measured serially at **660s for 6 310 tests**: the slowest 25 account for
  ~100s and the other ~6 285 average **89ms each**. That is process-spawn overhead spread evenly,
  not a hot spot — so parallelism is the fix and rewriting `subprocess` call sites is not.
  `--dist loadfile` keeps a file on one worker: the slowest tests are lease/flock/kill-the-driver
  concurrency cases and must not be raced against their own siblings.
- The pytest step also passes `--durations=25`, so every run reports where its time went instead
  of leaving the question to be re-investigated by hand.
- `skill-frontmatter` measures the YAML **value** with Ruby's Psych, never the source bytes. The
  cap the Agent Skills spec sets is on the parsed string, and this repo's skills use block scalars
  whose raw text runs longer than the value a parser produces. A dependency-free hand-rolled reader
  was drafted for this gate and rejected twice: each review round found a fresh class of valid YAML
  it silently UNDER-measured, which is a false PASS on a genuinely violating file. Its toolchain
  step therefore fails loudly rather than skipping when ruby or psych is absent.
- `skill-frontmatter` runs its own fixture suite before it looks at the real corpus, and refuses to
  report on the corpus if any fixture fails. It also prints a per-root file count: a walk that
  iterates zero times otherwise prints exactly what a passing one prints.

**Acceptance for any change to how the suite is invoked:** `passed + skipped + xfailed` must
equal what `pytest --collect-only -q` reports on the same tree. A parallel or sharded run that
collects fewer is not faster, it is quieter.

Do **not** pin an absolute total here. A `pull_request` run builds a fresh merge ref against
whatever `main` is at dispatch, and sibling releases land constantly — three LT releases landed
during the hour this CI was written, moving the total 6310 → 6361 with no test change of ours.
An absolute number turns that into a false alarm; the collected-vs-executed identity does not.

## Repo conventions

- `plugins/**` changes ship via **pull request** — that is what gets the `lazy-ants-reviewer` bot's
  review. Internal `.claude/skills/**` docs are committed directly to `main`.
- Merging to `main` **is** publishing: `claude plugin install <name>@lazyants` resolves the version
  straight from the manifests on `main`. There is no release pipeline and tags are not in the
  resolution path.
- Follow-ups, bugs, and permanent caveats are **GitHub issues**, never an in-repo markdown backlog.
