---
name: ai-cli-optout
description: Opt out of telemetry, tracking, error reporting, analytics and feedback uploads for installed AI CLIs and IDEs — Anthropic Claude Code, OpenAI Codex CLI, Google Gemini CLI, GitHub gh and Copilot CLI, Cursor and cursor-agent, Google Antigravity, VS Code (vscode), JetBrains PhpStorm, Windsurf/Codeium, Zed and Ollama — plus Vercel CLI, the Vercel Claude Code plugin, and macOS, Windows and Linux privacy controls (Apple Intelligence, Mac Analytics, Advertising ID, Recall, diagnostic data, Flatpak, GNOME and KDE). Applies documented opt-outs, checks vendor docs for new flags, and reports persistent local state without deleting it. Use for "opt out", "disable telemetry", "disable tracking", "privacy mode", "kill switch", "disable analytics", "stop sending data", "ai cli opt out" or "disable apple intelligence".
---

# AI CLI Optout

## Overview

One skill for every vendor. Vendor configs live in `vendors/*.json` — each declares doc URLs, settings files and edits to apply, env vars for settings.json, shell env vars for `~/.zshrc`, shell commands (`defaults write`, `reg add`) for OS-level opt-outs, persistent local files to report, platform gating, and caveats for flags that should never auto-apply.

Covered vendors (see each vendor's sources and limitations):

| Vendor | Platform | Settings / command | Caveats |
|---|---|---|---|
| Anthropic Claude Code | any | `~/.claude/settings.json` (or active `CLAUDE_CONFIG_DIR`) | Self-Modification hook may block settings edits |
| OpenAI Codex CLI | any | `~/.codex/config.toml` (`analytics.enabled`, `feedback.enabled`) | — |
| Google Gemini CLI | any | `~/.gemini/settings.json` + `GEMINI_TELEMETRY_ENABLED=false` | — |
| GitHub Copilot CLI + gh | any | `~/.config/gh/config.yml` via `gh config set` + `GH_TELEMETRY=false` | `COPILOT_OFFLINE=true` disables features too — never auto-apply |
| Cursor | darwin | manual_only — Cmd+Shift+J → Privacy Mode | Electron rewrite on graceful quit — quit before JSON edits |
| Cursor CLI (cursor-agent) | any | manual_only — account-level Privacy Mode covers it | Distinct binary from editor; bundled native modules unsigned (Gatekeeper friction on Mac) |
| Google Antigravity | darwin, linux, win32 | Platform-specific `settings.json` (`telemetry.telemetryLevel`) | See vendor instructions for the separate AI-training control |
| VS Code | darwin, linux, win32 | Platform-specific `settings.json` (`telemetry.telemetryLevel`) | Copilot extension does NOT inherit — manual per-extension |
| PhpStorm | darwin | manual_only — Settings → Tools → Usage Statistics | AI Assistant is non-bundled; only surface if plugin exists |
| Vercel CLI | any | `vercel telemetry disable` (persistent) + `VERCEL_TELEMETRY_DISABLED=1` (per-run override) | Sibling tools (Next.js, Turborepo) have separate streams and are not covered. |
| Vercel Claude Code plugin | any | `VERCEL_PLUGIN_TELEMETRY=off` | Separate from Vercel CLI; see the installed plugin's telemetry policy |
| Windsurf / Codeium (Devin Desktop) | any | Manual account privacy / Data Controls | Account plan/admin permissions affect the available control |
| Zed | darwin, linux, win32 | `settings.json` telemetry controls | See vendor sources for platform paths and AI data controls |
| Ollama | any | Manual local-inference and server cloud controls | Resolve the server owner/home; cloud opt-out also loses web search |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter plist) | Apple Intelligence keys are MDM-only on unmanaged Macs |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, AllowTelemetry, AdvertisingInfo) | Home/Pro: diagnostic data floor is Required |
| Linux system privacy | linux | Flatpak HTTP telemetry + manual desktop controls | GNOME/KDE controls vary by installed desktop; no universal OS switch |

## Trigger phrases

**This list is documentation, not discovery.** The body loads only after the skill has already been
selected, so nothing here can cause a match — selection happens entirely on the frontmatter
`description`, which the Agent Skills spec caps at 1024 characters. The description therefore names
every vendor and every action term (telemetry, tracking, analytics, error reporting, feedback, opt
out, privacy mode, kill switch) rather than every phrasing of them, which is what keeps requests like
"disable windows telemetry" or "opt out of vercel" matchable without listing them verbatim. The list
below records the phrasings this skill is meant to answer, for authors and for anyone auditing that
coverage:

"opt out", "opt out of telemetry", "disable telemetry", "privacy mode", "kill switch",
"disable analytics", "stop sending data", "disable claude code tracking",
"disable claude code telemetry", "stop sending data to anthropic", "opt out of codex",
"disable codex analytics", "disable openai telemetry", "disable gemini telemetry",
"disable gemini analytics", "disable copilot telemetry", "disable gh telemetry",
"cursor privacy mode", "disable cursor telemetry", "disable cursor cli telemetry",
"cursor-agent privacy", "opt out of cursor cli", "ai cli privacy", "ai cli opt out",
"opt out of antigravity", "disable google antigravity telemetry", "vscode telemetry off",
"disable vscode telemetry", "phpstorm privacy", "disable jetbrains telemetry",
"disable apple intelligence", "macos privacy optout", "disable mac analytics",
"disable advertising id", "windows privacy optout", "disable windows telemetry",
"disable recall", "disable windows copilot", "disable vercel telemetry", "opt out of vercel",
"vercel privacy", "vercel plugin telemetry", "windsurf privacy", "codeium opt out",
"zed telemetry off", "ollama privacy", "linux privacy optout", "flatpak telemetry",
"disable gnome reporting", "disable kde feedback".

## Workflow

### Step 0 — Detect current platform

Resolve `uname -s` → `darwin` / `linux` / `win32` (MINGW*/MSYS*/CYGWIN*). Store as `$CURRENT_PLATFORM`.

For each vendor config, read the optional `platforms` array:
- **absent** → runs on any platform.
- **includes `$CURRENT_PLATFORM`** → normal flow.
- **does NOT include `$CURRENT_PLATFORM`** → vendor is **dormant**: skill lists the config in the final report as a copy-paste checklist, but does NOT auto-execute anything (no Edit calls, no shell commands, no settings writes). This is how Windows configs get surfaced on a Mac.

Apply the vendor's actual platform list; VS Code, Antigravity and Linux privacy controls run on Linux. Only vendors that exclude the current platform are dormant. State which vendors are dormant and the target OS required for execution.

### Step 1 — Detect installed vendors

For each `vendors/*.json`, run `command -v` against the vendor's `detect_cmd` (e.g. `codex`, `gemini`, `gh`). If the binary isn't on PATH, check `detect_paths[]` for a vendor-exclusive executable or application bundle. Expand a leading `~` and declared environment placeholders without `eval`; a missing variable is an unresolved path, never a literal directory to create. Ordinary config/state directories are not installation evidence: they survive uninstall and can belong to sibling products. Skip uninstalled vendors silently. For fresh GUI installs without a CLI shim, use the shipped application-bundle fallback.

When a vendor supplies `detect_check`, run that read-only check instead of inferring installation from the host CLI. Select `detect_check[$CURRENT_PLATFORM]` if it is a platform map; otherwise use its string command. Plugin checks verify an enabled, exact identity in the CLI's installed-plugin list, honoring its active config directory; editor checks verify the relevant binary or application. A blank `detect_cmd` is not a command to probe. Report failed/unavailable detection as uncertain and skip application; do not substitute shared config-directory detection.

**Skip detection for dormant-platform vendors.** A vendor whose `platforms` doesn't include `$CURRENT_PLATFORM` (e.g. `windows-privacy` on a Mac, whose `detect_cmd` is the common `reg` name) will go directly to the copy-paste rendering branch without any `command -v` probe — a coincidental PATH hit on `reg` must not trigger false detection.

### Step 2 — Ask the user which to process

Default = all detected vendors. User can opt out of individual ones, or explicitly select a vendor after verifying its installed app/extension when automatic detection cannot inspect that installation.

### Step 3 — Per vendor

**Before applying anything:** read and print every `notes[]` entry for the selected vendor. These notes can change which file, scope or command is valid. Include them in the final report, including for dormant vendors. When a note calls for an extra action, follow the documented workflow for that action or report it as pending; printing a note alone is not proof that the action was applied.

**(a) Research:** run `bash scripts/check_new_optouts.sh <vendor>`. If it reports **NEW flags** not in baseline, show them to the user and ask whether to include. Do not add flags the script didn't surface — no guessing. If docs fail to fetch, note that research was skipped and proceed with baseline.

**(b) Apply settings edits:** for each entry in `settings_files[]`:
- **Check the app before reading or writing settings:** if `process_check` exists, choose `process_check.commands[$CURRENT_PLATFORM]` when present, otherwise `process_check.cmd`, and run it. Exit 0 means running: print `if_running`, defer all settings edits and ask the user to quit the app. After they quit, repeat the check; continue only on exit 1 (not running). A missing command/tool or exit 2+ is a failed check: report the failure and defer edits instead of assuming the app is stopped. Do not kill the app. Record deferred edits as pending, never applied.
- If a settings entry declares `install_check`, run `install_check[$CURRENT_PLATFORM]` (or its string command) first: exit 0 selects that installed product's file, exit 1 skips the absent product, and any other result defers the file as uncertain. Antigravity's current IDE and legacy editor use different binaries and settings roots; never create the other product's directory just because one is installed.
- Resolve `settings_files[].paths[$CURRENT_PLATFORM]` when `paths` exists; otherwise use `path`. Never edit another platform's path. Expand `~`, `${XDG_CONFIG_HOME}` (default `~/.config`) and `%APPDATA%` / `%LOCALAPPDATA%` from the actual environment without `eval`; unresolved required variables mean defer the file and report its intended path. Honor `home_env` when declared (e.g. `CODEX_HOME`), or active `CLAUDE_CONFIG_DIR` for Anthropic, rather than writing an inactive default profile directory. Do not create author-specific profile directories.
- Read the file (create with `{}` if missing for JSON, empty for TOML).
- **Confirmation-gated edits:** for any `edit` with `requires_confirmation: true`, surface its `tradeoff_note` to the user verbatim and ask for explicit confirmation BEFORE applying. Skip the edit on decline and record it in the final report as "declined by user — trade-off not accepted". Applies regardless of whether the user pre-approved the vendor in Step 2.
- Apply each `edit` according to `key_mode`: `literal` uses the complete key as one JSON/JSONC property (e.g. `telemetry.telemetryLevel`); `dotted` (the default) uses a nested path (e.g. Anthropic's `env.DISABLE_TELEMETRY`, Zed's `telemetry.metrics`, or TOML `analytics.enabled`). Preserve comments and unrelated settings. Follow the declared file format and key mode rather than treating every dotted key as nested JSON.
- **Codex profile coverage:** apply `profile_files.edits` to every existing valid `NAME.config.toml` under the resolved Codex home when `profile_files` exists (profile names use letters, numbers, `_` or `-`). Never create profiles. These overlays can override top-level analytics. For Codex older than 0.134.0, also apply `profile_edits` to each existing `[profiles.NAME]` section, preserving quoted profile names and unrelated keys. Codex 0.134.0+ ignores those legacy sections: report them as needing migration rather than claiming they are active profiles. If the version cannot be read, handle both existing layouts and state that version-specific coverage could not be confirmed. Profile analytics and top-level feedback are distinct controls; do not invent a profile feedback setting.
- On a Self-Modification / hook denial from the Edit tool, **do not retry**. Print the exact diff and instruct the user to run `! $EDITOR <path>` to paste manually. This applies to any settings file, not just `~/.claude/settings.json`.

**(c) Shell env vars:** collect `shell_env_vars[]` into a summary block the user can append to `~/.zshrc`. The skill never writes shell rc files directly.

**(c2) `cli_commands[]`:** for each entry, surface the `cmd` + `disables` text and ask the user before running. Examples: `gh config set telemetry disabled` (Copilot), `vercel telemetry disable` (Vercel). Never run unconfirmed.

**(d) Caveat-gated flags:** for each `caveats[]` entry, present the `framing` text and explicitly ask the user before even suggesting the export. Never auto-apply.

**(e) Manual-only vendors (`manual_only: true`):**
- Print `manual_instructions[]` verbatim.
- When `process_check` exists, follow the same running/not-running/error rule as Step 3(b) before offering file edits. Vendors without this optional field still receive their manual instructions. Do not invent a process command.
- **Only for Cursor** (`name == "cursor"`), if not running, additionally surface the VS Code-inherited JSON block `"telemetry.telemetryLevel": "off"` with the caveat that it covers editor telemetry only, NOT Cursor's AI telemetry. **Do NOT** apply this to PhpStorm or other manual_only vendors — they have no equivalent editor JSON.

**(f) Shell commands (`shell_commands[]` — OS privacy vendors):**
- If the vendor's `platforms` array doesn't include `$CURRENT_PLATFORM` → render as copy-paste only, do NOT execute. Wrap Windows `reg add` commands in a ` ```powershell ` fenced block with a header: `# Run these in PowerShell on your Windows machine. Do NOT paste into zsh/bash.` Wrap macOS `defaults write` commands in a ` ```bash ` block.
- If the platform matches and all commands have `requires_sudo: false` and `requires_admin: false` → execute directly per command after showing the plan.
- If **any** command needs elevation (`requires_sudo: true` on macOS/Linux or `requires_admin: true` on Windows) → issue a **single consolidated prompt** that renders the full text of every elevated command (never just a count), then a single y/n. Never silently escalate. If the user declines, skip ALL elevated commands but still run the non-elevated ones if any. After consent for Unix `requires_sudo` commands, run `sudo -v` once before the batch; do not wrap the batch inside `sudo -s <<EOF` (that loses per-command success signaling). For Windows `requires_admin` commands, verify that the current Windows shell is elevated. If it is not, render the exact commands for an Administrator PowerShell window and keep them pending until the user runs them and their results are verified; do not run `sudo -v` on Windows. Record each command's success or failure separately.
- `manual_only_items[]` (UI-only entries like Apple Intelligence's System Settings path) — print each applicable entry's `ui_path` and `reason` verbatim, even when no shell command exists; never attempt CLI application. For Linux desktop controls, first identify the installed desktop/component and skip controls that are absent; do not imply a KDE setting applies to GNOME or every distribution ships a reporting agent.

### Step 4 — Report

Consolidated summary:
- Flags applied per vendor + file (with diff).
- Vendor notes and limitations, including dormant vendors; profile-scoped and deferred edits that remain pending.
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

**Read [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) before committing.** It documents the `detect_paths` sibling-config trap (the most common cause of false-positive vendor detection) and the `requires_confirmation` gate schema for risky edits.

No code changes required — the skill is data-driven.

## Provider switches (Anthropic only, not auto-applied)

`~/.claude/settings.json` can also route prompts to AWS Bedrock, Google Vertex AI, or Azure AI Foundry via `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY`. These change the billing/compute backend, not just telemetry — mention in the report if the user asked for the nuclear option, but never auto-apply.

## What this does NOT opt out of

- Prompts and outputs still go to each vendor's API — that's the essential path; to avoid it the user must switch providers.
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is reported but never deleted.
- Cursor's AI telemetry when Privacy Mode is off — the vendor-blessed control is the UI toggle.
