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

1. **Locale equals `capture.locale`.** The sandbox sets `LANG` and `LC_ALL`
   (or the equivalent for the runtime) to `capture.locale`. The app under
   test renders in that language. You do not accept "it usually picks the
   right one" — pin it.
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

## When the project has no sandbox yet

If `capture.command` is empty or the project clearly runs captures on the
host, you do not just run it anyway. You halt and tell the user the
guarantees above are not met, and point them at the example
`capture.command` in `assets/handbook.profile.example.yml` as a starting
shape. A leaky capture pipeline produces a handbook that looks correct today
and silently rots the next time someone rebuilds it on a different machine.
