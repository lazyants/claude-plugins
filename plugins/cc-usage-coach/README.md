# cc-usage-coach

**Find out where your Claude Code usage-limit tokens actually go — and how to spend fewer.**

A Claude Code plugin: a skill that reads your **local** session logs and builds
a compact, path-free signal pack, then the Claude runtime writes a personalized
report on where your Max / Pro limit tokens go plus a ranked list of low-effort
levers. Python measures; Claude concludes.

It exists because usage limits are opaque. You hit a cap, you don't know why,
and the generic advice ("use a smaller model") rarely matches how *you* actually
work. cc-usage-coach derives the answer from your own sessions instead of
guessing.

## What you get

- **A signal pack** — `signal_pack.json`, a compact aggregate of your session
  behavior (token shapes, cache patterns, tool mix, session lengths). It is
  **path-free and project-name-free** (project folders appear as opaque IDs),
  so it is safe to share.
- **A personalized report** — the Claude runtime reads the signal pack and
  writes a plain-language breakdown of where your limit tokens go, with ranked,
  low-effort changes that would use fewer.
- **A per-session arc view** — `arc.py` inspects a single session's prompt arc
  so you can see how one conversation consumed budget over time.

## Install

```
claude plugin marketplace add lazyants/claude-plugins
claude plugin install cc-usage-coach@lazyants
```

Restart Claude Code once after install so the skill triggers register.

## How it works

The skill runs three scripts in sequence; you normally just invoke the skill
and let it drive them.

1. **`scripts/extract.py`** — scans your local Claude Code session logs and
   builds a local dataset under `dataset/`.
2. **`scripts/signals.py`** — reads the dataset and emits three files:
   `signal_pack.json` (the path-free, project-name-free aggregate, safe to
   share) and two **local-only** maps: `source_index.json` (opaque session
   reference → real file) and `project_index.json` (opaque project ID → real
   project name).
3. **`scripts/arc.py <source_ref>`** — inspects a single session's prompt arc by
   its opaque `source_ref` (from `source_index.json`). Local-only.

The Claude runtime then reads `signal_pack.json` and writes your report. No raw
log bytes go into the model beyond the aggregated signals.

### Environment variables

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | Honored — points the scan at a non-default Claude Code config directory. |
| `CC_COACH_CONFIG_DIRS` | Comma-separated list of **extra** config dirs to scan. Default scans only the standard `.claude` directory. |
| `CC_COACH_OUT` | Where outputs are written (see precedence below). |

Output location precedence: `$CC_COACH_OUT` if set, else next to the scripts if
that directory is writable, else `${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/`.

## Privacy

cc-usage-coach is **local-first** by construction. It reads your local session
logs only, performs **no** network calls, and nothing leaves your machine.

- **`signal_pack.json` is path-free, project-name-free, and safe to share.** It
  contains aggregated signals only — no filesystem paths, no prompt text, no
  project/client/repo names. Sessions appear only as an opaque `source_ref` and
  projects only as an opaque project ID.
- **`source_index.json`, `project_index.json`, the `dataset/`, and the `arc.py`
  digest are local-only.** They contain real filesystem paths, project names,
  and your prompt text. They are written with `0600` permissions where
  applicable and **must never be uploaded or shared.** If you share output with
  anyone, share the signal pack — never these.

The opaque `source_ref` (session) and project ID are the only handles that cross
between the shareable pack and the local-only indexes, so you — and your own
Claude runtime, reading the local maps — can correlate a finding back to a real
session or project on your machine without the shared pack exposing either.

## Layout

```
cc-usage-coach/
├── skills/cc-usage-coach/
│   ├── SKILL.md       # the skill that drives the scripts and writes the report
│   └── scripts/
│       ├── extract.py # scan local session logs → local dataset/
│       ├── signals.py # dataset → signal_pack.json (+ local-only source/project indexes)
│       └── arc.py     # inspect one session's prompt arc (local-only)
└── tests/
    └── run-all.sh     # runs the pytest suite
```

## Tests

```sh
bash tests/run-all.sh
```

Runs the pytest suite over `tests/`, covering the extractor, the signal-pack
shape (including the path-free guarantee), the per-session arc, and fixture
safety.

## License

MIT — see the marketplace `LICENSE`.
