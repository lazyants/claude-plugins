# Container isolation

Capture runs in a sandboxed environment — a container, a VM, or at minimum a
fresh browser profile dedicated to the harness. It does not run on the
developer's host shell, host browser, or host user account. The only thing the
host contributes is the command line that launches the sandbox.

## Why

You enforce isolation so that captures are reproducible across machines and CI,
not portraits of one developer's desk. Concretely, you keep these out of the
sandbox:

- **Host locale drift.** The developer's `LANG` / `LC_ALL` / OS language pack
  decides date formats, number separators, sort order, and which translation
  file the app picks. If host locale leaks in, two developers produce
  visibly different chapters from the same code. The sandbox pins locale to
  `capture.locale` so every run looks the same regardless of who launched it.
- **Cached cookies and storage.** The developer's logged-in browser session,
  saved form autofill, prior consent banners dismissed, and feature-flag
  overrides set during debugging all silently change what the app renders. A
  fresh sandbox starts from zero state every time.
- **Host fonts.** A screenshot taken on a machine with a custom font installed
  will render UI labels in that font; another machine produces different
  glyph shapes, different line wrapping, different button widths. The
  handbook screenshots become non-comparable. The sandbox image carries a
  fixed font set.
- **Host filesystem writes.** A capture run must not be able to overwrite
  arbitrary host paths because of a buggy spec or a path-traversal mistake.
  The sandbox mounts only `capture.output_dir` writable; everything else is
  either read-only or unmounted.
- **Host secrets.** Developer SSH keys, cloud credentials in `~/.aws`,
  browser-stored passwords, and shell history are not available to the
  harness. A capture run that needs a credential gets it from a
  sandbox-scoped mechanism the project chose (env var passed in via
  `capture.command`, mounted secret file, etc.), never from ambient host
  state.

## The host's only job: invoke the command

You treat `capture.command` from the profile as a literal, copy-pasteable
string. You do not edit it, second-guess its flags, or "improve" it by adding
host paths. You run exactly what the profile says. The profile owns:

- **What runtime.** `docker compose run …`, `podman run …`, `vagrant ssh -c …`,
  `nix-shell --run …`, `flatpak run …`, a remote `ssh ci-runner …`, or
  anything else the project standardized on.
- **What image / VM.** The profile's command names the image tag or VM
  snapshot. You do not pull a different one because it is "more recent".
- **What environment.** Locale, timezone, font set, network reachability to
  the app under test, and any in-sandbox tool versions are baked into the
  image or set by flags in `capture.command`.

If the command needs project-specific glue (a hosts-file entry so the sandbox
can reach the dev domain, a compose override file, a `--add-host` flag, a
shared docker network, a VPN reachability check), that glue lives in
`capture.command` or in a project-side `.claude/handbook/capture-recipe.md`
the command references. It does not live in this skill.

## What the project's command must guarantee

When you read `capture.command` from the profile, you check that the project
has wired it so the sandbox satisfies these guarantees. If a guarantee is not
met, you halt and tell the user to fix the command before running captures.

1. **Locale equals `capture.locale`.** `capture.locale` is a full POSIX
   locale (e.g. `de_DE.UTF-8`), not a bare ISO language code — a bare code
   cannot pin date/number/sort formatting, which is the whole point of this
   guarantee. The sandbox sets `LANG` and `LC_ALL` (or the runtime's
   equivalent) to `capture.locale` *verbatim*, and the app under test renders
   in that locale's language. The content language alone lives in
   `language.code`; `capture.locale` is the process locale. You do not accept
   "it usually picks the right one" — pin it.
2. **All capture output lands under `capture.output_dir`.** The command
   mounts `capture.output_dir` writable into the sandbox and the spec writes
   only there. No writes to other host paths. The path in `capture.output_dir`
   is the single source-of-truth location for screenshots and any captured
   artifacts.
3. **No incidental host filesystem writes.** The sandbox does not bind-mount
   the developer's home directory, SSH agent socket, cloud credential dirs,
   or arbitrary project paths writable. Read-only mounts for source code are
   fine; writable mounts are scoped to `capture.output_dir`.
4. **No host browser, no host Node, no host Python.** Whatever runtime the
   capture engine needs comes from inside the sandbox. The developer never
   has to install the engine on the host for captures to work.
5. **Deterministic across machines.** Two developers on different OSes,
   running the same `capture.command` against the same commit, produce
   byte-for-byte equivalent screenshots (modulo timestamps in the data
   itself). If you see drift, the sandbox is leaky — fix the command before
   shipping more captures.

## Common command patterns (engine-agnostic)

Whatever runtime the project standardizes on, a `capture.command` that drives a
**containerized live dev stack** almost always needs these patterns to satisfy the
guarantees above. The literal values (image tag, network name, host alias) are
project-specific and stay in `capture.command` / `.claude/handbook/capture-recipe.md` — only
the *shape* is general:

- **Pin the locale to `capture.locale`**, e.g. `-e LANG=de_DE.UTF-8 -e LC_ALL=de_DE.UTF-8`.
  Guarantee 1 requires the sandbox locale to equal `capture.locale`, which is itself a full
  POSIX locale (e.g. `de_DE.UTF-8`) — so set both `LANG` and `LC_ALL` to that value verbatim.
  An unpinned container inherits the image default (often `C`/POSIX), which changes date and
  number formats, sort order, and which translation file the app serves — so two machines
  produce visibly different chapters.
- **Run as the host user**, e.g. `--user "$(id -u):$(id -g)"`. A container running as root
  writes root-owned PNGs into `capture.output_dir` that the developer then cannot edit or
  clean without sudo. Map the host UID/GID so captured artifacts stay owned by the user.
- **Point `HOME` at a throwaway dir**, e.g. `-e HOME=/tmp`. With the repo bind-mounted and
  `HOME` left at its default, the engine dumps `.cache` / `.npm` / `.config` **into the
  bind-mounted repo**, which then get committed as junk. `HOME=/tmp` keeps that churn out of
  the tree (belt-and-suspenders: gitignore those paths too).
- **Reach the running app, don't recreate it.** When the app is already up (the dev stack the
  developer is using), join its existing network and resolve its host with
  `--add-host` / `host-gateway` rather than `compose up`-ing the app service — bringing the
  service up again can recreate containers and disrupt the developer's running stack. Use
  `compose run --rm` (one-off) or `docker run` against the existing network, never `up`.
- **Pin the engine image in lockstep with the test dependency.** The capture image tag (the
  Playwright/Cypress browser image) must match the engine version pinned in the project's
  package manifest; bump them together, or the browser and the test runner drift and captures
  stop being reproducible (guarantee 5).

## Capturing from a git worktree

When you run captures from a **git worktree** (a checkout linked to a main
clone, common when a team isolates a feature branch), the worktree's
`node_modules` is usually a **symlink** back to the main checkout's real
`node_modules`. That symlink **dangles inside the docker bind-mount**: the
container mounts the worktree path, follows the symlink, and lands on a target
that does not exist inside the sandbox's filesystem. The capture engine then
fails to resolve its own dependencies even though `node_modules` "is there" on
the host.

The pattern (engine-agnostic — adapt to whatever runtime `capture.command`
uses):

- **Overlay the real modules with a second, read-only mount** using **resolved
  absolute paths**, so the container sees real files where the dangling symlink
  was:
  `-v <abs-main-checkout>/node_modules:<container-app-dir>/node_modules:ro`.
  Resolve both sides to absolute paths first (a worktree-relative or `~`-relative
  path will not mount correctly); mark it `:ro` because capture never writes to
  `node_modules`.
- **Stage with explicit paths, never `git add -A`.** A `node_modules/` gitignore
  rule matches the **directory pattern**, not a **symlink** that happens to be
  named `node_modules` — so the symlink is *not* ignored and a blanket
  `git add -A` will commit it. Stage only the artifacts you mean to ship with
  `git add <paths>`, listing the chapter, manifest, and screenshot paths
  explicitly.
- **Serialize capture across parallel worktrees.** Worktrees share the
  developer's single running dev stack and its port; two captures running at once
  contend for the same app instance and produce non-deterministic shots (guarantee
  5). Run one worktree's capture at a time.

The engine-agnostic rules here are normative; any `*.playwright.*` asset shipped
under `../assets/` is a **non-normative reference implementation** for the
Playwright reference case — reimplement the driver glue for another engine; the
engine-neutral `../assets/lib/*.mjs` helpers are reused as-is.

## When the project has no sandbox yet

If `capture.command` is empty or the project clearly runs captures on the
host, you do not just run it anyway. You halt and tell the user the
guarantees above are not met, and point them at the example
`capture.command` in `assets/handbook.profile.example.yml` as a starting
shape. A leaky capture pipeline produces a handbook that looks correct today
and silently rots the next time someone rebuilds it on a different machine.
