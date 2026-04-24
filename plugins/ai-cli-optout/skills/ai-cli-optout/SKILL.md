---
name: ai-cli-optout
description: Opt out of telemetry / error reporting / analytics / feedback uploads across every locally installed AI CLI and AI-enabled IDE — Anthropic Claude Code, OpenAI Codex CLI, Google Gemini CLI, GitHub CLI + Copilot CLI, Cursor, Cursor CLI (cursor-agent), Google Antigravity, VS Code, PhpStorm — plus macOS + Windows OS-level privacy surfaces (Apple Intelligence, Mac Analytics, Advertising ID, Recall, Copilot, Diagnostic Data). Applies documented opt-out flags per vendor, diffs against live vendor docs to surface NEW vars not in baseline, and reports persistent local state without deleting anything. Triggers — "opt out", "opt out of telemetry", "disable telemetry", "privacy mode", "kill switch", "disable analytics", "stop sending data", "disable claude code tracking", "disable claude code telemetry", "stop sending data to anthropic", "opt out of codex", "disable codex analytics", "disable openai telemetry", "disable gemini telemetry", "disable gemini analytics", "disable copilot telemetry", "disable gh telemetry", "cursor privacy mode", "disable cursor telemetry", "disable cursor cli telemetry", "cursor-agent privacy", "opt out of cursor cli", "ai cli privacy", "ai cli opt out", "opt out of antigravity", "disable google antigravity telemetry", "vscode telemetry off", "disable vscode telemetry", "phpstorm privacy", "disable jetbrains telemetry", "disable apple intelligence", "macos privacy optout", "disable mac analytics", "disable advertising id", "windows privacy optout", "disable windows telemetry", "disable recall", "disable windows copilot".
---

# AI CLI Optout

## Overview

One skill for every vendor. Vendor configs live in `vendors/*.json` — each declares doc URLs, settings files and edits to apply, env vars for settings.json, shell env vars for `~/.zshrc`, shell commands (`defaults write`, `reg add`) for OS-level opt-outs, persistent local files to report, platform gating, and caveats for flags that should never auto-apply.

Covered vendors (2026-04-24 baseline):

| Vendor | Platform | Settings / command | Caveats |
|---|---|---|---|
| Anthropic Claude Code | any | `~/.claude/settings.json`, `~/.claude-bm/settings.json` | Self-Modification hook may block `~/.claude/settings.json` |
| OpenAI Codex CLI | any | `~/.codex/config.toml` (`analytics.enabled`, `feedback.enabled`) | — |
| Google Gemini CLI | any | `~/.gemini/settings.json` + `GEMINI_TELEMETRY_ENABLED=false` | — |
| GitHub Copilot CLI + gh | any | `~/.config/gh/config.yml` via `gh config set` + `GH_TELEMETRY=false` | `COPILOT_OFFLINE=true` disables features too — never auto-apply |
| Cursor | darwin | manual_only — Cmd+Shift+J → Privacy Mode | Electron rewrite on graceful quit — quit before JSON edits |
| Cursor CLI (cursor-agent) | any | manual_only — account-level Privacy Mode covers it | Distinct binary from editor; bundled native modules unsigned (Gatekeeper friction on Mac) |
| Google Antigravity | darwin | `~/Library/.../Antigravity/User/settings.json` (`telemetry.telemetryLevel`) | AI-training opt-out is EMAIL-ONLY (antigravity-support@google.com) |
| VS Code | darwin | `~/Library/.../Code/User/settings.json` (`telemetry.telemetryLevel`) | Copilot extension does NOT inherit — manual per-extension |
| PhpStorm | darwin | manual_only — Settings → Tools → Usage Statistics | AI Assistant is non-bundled; only surface if plugin exists |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter plist) | Apple Intelligence keys are MDM-only on unmanaged Macs |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, AllowTelemetry, AdvertisingInfo) | Home edition: AllowTelemetry=0 unsupported (floor is Required) |

## Workflow

### Step 0 — Detect current platform

Resolve `uname -s` → `darwin` / `linux` / `win32` (MINGW*/MSYS*/CYGWIN*). Store as `$CURRENT_PLATFORM`.

For each vendor config, read the optional `platforms` array:
- **absent** → runs on any platform.
- **includes `$CURRENT_PLATFORM`** → normal flow.
- **does NOT include `$CURRENT_PLATFORM`** → vendor is **dormant**: skill lists the config in the final report as a copy-paste checklist, but does NOT auto-execute anything (no Edit calls, no shell commands, no settings writes). This is how Windows configs get surfaced on a Mac.

On Linux: every platform-gated vendor (macOS, Windows, Antigravity, VS Code, PhpStorm, Cursor — all tagged `darwin` or `win32`) is dormant. Non-platform-gated vendors (Claude Code, Codex, Gemini, Copilot CLI) work normally. Flag this up front: "Running on Linux — platform-specific vendors will be listed as copy-paste. Run the skill on the target OS for interactive execution."

### Step 1 — Detect installed vendors

For each `vendors/*.json`, run `command -v` against the vendor's `detect_cmd` (e.g. `codex`, `gemini`, `gh`). If the binary isn't on PATH, fall back to checking `detect_paths[]` for any existing state directory — always run both checks, because fresh installs often lack the CLI shim (e.g. VS Code until the user runs "Install 'code' command in PATH" from the palette). Skip uninstalled vendors silently.

**Skip detection for dormant-platform vendors.** A vendor whose `platforms` doesn't include `$CURRENT_PLATFORM` (e.g. `windows-privacy` on a Mac, whose `detect_cmd` is the common `reg` name) will go directly to the copy-paste rendering branch without any `command -v` probe — a coincidental PATH hit on `reg` must not trigger false detection.

### Step 2 — Ask the user which to process

Default = all detected vendors. User can opt out of individual ones.

### Step 3 — Per vendor

**(a) Research:** run `bash scripts/check_new_optouts.sh <vendor>`. If it reports **NEW flags** not in baseline, show them to the user and ask whether to include. Do not add flags the script didn't surface — no guessing. If docs fail to fetch, note that research was skipped and proceed with baseline.

**(b) Apply settings edits:** for each entry in `settings_files[]`:
- Read the file (create with `{}` if missing for JSON, empty for TOML).
- Apply each `edit` (nested keys like `env.DISABLE_TELEMETRY` or `analytics.enabled` — dotted path, not literal key).
- On a Self-Modification / hook denial from the Edit tool, **do not retry**. Print the exact diff and instruct the user to run `! $EDITOR <path>` to paste manually. This applies to any settings file, not just `~/.claude/settings.json`.

**(c) Shell env vars:** collect `shell_env_vars[]` into a summary block the user can append to `~/.zshrc`. The skill never writes shell rc files directly.

**(c2) `gh_config_commands[]` (Copilot only):** for each entry, surface the command and ask the user before running it. `gh config set telemetry disabled` is safe to run immediately once confirmed.

**(d) Caveat-gated flags:** for each `caveats[]` entry, present the `framing` text and explicitly ask the user before even suggesting the export. Never auto-apply.

**(e) Manual-only vendors (`manual_only: true` — Cursor, PhpStorm):**
- Print `manual_instructions[]` verbatim.
- Run the `process_check.cmd` (e.g. `pgrep -x Cursor`). If matched, print the `if_running` warning and stop — do not offer edits.
- **Only for Cursor** (`name == "cursor"`), if not running, additionally surface the VS Code-inherited JSON block `"telemetry.telemetryLevel": "off"` with the caveat that it covers editor telemetry only, NOT Cursor's AI telemetry. **Do NOT** apply this to PhpStorm or other manual_only vendors — they have no equivalent editor JSON.

**(f) Shell commands (`shell_commands[]` — macOS/Windows privacy vendors):**
- If the vendor's `platforms` array doesn't include `$CURRENT_PLATFORM` → render as copy-paste only, do NOT execute. Wrap Windows `reg add` commands in a ` ```powershell ` fenced block with a header: `# Run these in PowerShell on your Windows machine. Do NOT paste into zsh/bash.` Wrap macOS `defaults write` commands in a ` ```bash ` block.
- If the platform matches and all commands have `requires_sudo: false` and `requires_admin: false` → execute directly per command after showing the plan.
- If **any** command needs elevation (`requires_sudo: true` on macOS / `requires_admin: true` on Windows) → issue a **single consolidated prompt** that renders the full text of every elevated command (never just a count), then a single y/n. Never silently escalate. If the user declines, skip ALL elevated commands but still run the non-elevated ones if any. **After consent, immediately run `sudo -v` to refresh the sudo timestamp** — this prevents reprompts or partial execution if sudo expiry kicks in between commands. Run `sudo -v` once before the batch; do not wrap the whole batch inside a single `sudo -s <<EOF` (that loses per-command success signaling).
- `manual_only_items[]` (UI-only entries like Apple Intelligence's System Settings path) — print each entry's `ui_path` and `reason` verbatim; never attempt CLI application.

### Step 4 — Report

Consolidated summary:
- Flags applied per vendor + file (with diff).
- NEW flags discovered by research + user's accept/reject.
- Blocked files needing manual paste (with exact JSON/TOML block).
- Caveat-gated exports the user chose to apply (and the ones they declined).
- Shell-rc exports to add to `~/.zshrc`.
- Persistent local files found (paths + sizes, via `scripts/report_persistent_files.sh`). Never deleted — user decides.
- Reminder: restart each CLI after env vars change.

## Scripts

```bash
scripts/check_new_optouts.sh <vendor>     # one vendor
scripts/check_new_optouts.sh --all        # every vendor
scripts/report_persistent_files.sh        # all vendors
scripts/report_persistent_files.sh <vendor>
```

Both require `jq` and fail fast with a clear install hint if missing. `curl -fsSL --max-time 10` with per-URL failure tolerance.

## Adding a new vendor

1. Write `vendors/<name>.json` using the schema. Start from `codex.json` (minimal) or `anthropic.json` (multiple settings files + provider switches) — no single file uses every field.
2. Validate with `jq . vendors/<name>.json`.
3. Run `bash scripts/check_new_optouts.sh <name>` to smoke-test baseline extraction.
4. Update the vendors table above.

No code changes required — the skill is data-driven.

## Provider switches (Anthropic only, not auto-applied)

`~/.claude/settings.json` can also route prompts to AWS Bedrock, Google Vertex AI, or Azure AI Foundry via `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY`. These change the billing/compute backend, not just telemetry — mention in the report if the user asked for the nuclear option, but never auto-apply.

## What this does NOT opt out of

- Prompts and outputs still go to each vendor's API — that's the essential path; to avoid it the user must switch providers.
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is reported but never deleted.
- Cursor's AI telemetry when Privacy Mode is off — the vendor-blessed control is the UI toggle.
