# Known opt-out flags — reference

**Authoritative source:** `vendors/*.json`. This file is a readable summary.

Last verified: 2026-04-24 (extension for Antigravity / VS Code / PhpStorm / macOS / Windows).

## Anthropic Claude Code
- Env vars (in `~/.claude/settings.json` and `~/.claude-bm/settings.json` env block):
  - `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` — kill switch (Statsig + Sentry + surveys + /feedback)
  - `DISABLE_TELEMETRY=1` — Statsig operational metrics
  - `DISABLE_ERROR_REPORTING=1` — Sentry crash reports
  - `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1` — in-session rating prompt
  - `DISABLE_FEEDBACK_COMMAND=1` — `/feedback`
- Top-level: `"skipWebFetchPreflight": true` — no hostname preflight to api.anthropic.com
- Provider switches (route to Bedrock/Vertex/Foundry; telemetry off by default there):
  - `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`, `CLAUDE_CODE_USE_FOUNDRY=1`
- Persistent: `~/.claude/projects/*` — plaintext session transcripts, 30-day default retention (tune with `"cleanupPeriodDays": N`)
- Docs: https://code.claude.com/docs/en/env-vars.md · https://code.claude.com/docs/en/data-usage.md · https://code.claude.com/docs/en/monitoring-usage.md · https://code.claude.com/docs/en/settings.md · https://www.anthropic.com/legal/privacy

## OpenAI Codex CLI
- `~/.codex/config.toml`:
  - `analytics.enabled = false` — usage analytics
  - `feedback.enabled = false` — `/feedback` uploads
- No env var documented
- Profile-scoped: `profiles.<name>.analytics.enabled = false`
- Persistent: `~/.codex/logs_2.sqlite` (can be huge), `~/.codex/history.jsonl` (plaintext conversations), `~/.codex/installation_id`, `~/.codex/sessions/`, `~/.codex/.codex-global-state.json`
- Docs: https://developers.openai.com/codex/config-reference

## Google Gemini CLI
- `~/.gemini/settings.json`:
  - `telemetry.enabled = false` — telemetry
  - `telemetry.logPrompts = false` — prompt content in telemetry
- Env vars (override settings.json): `GEMINI_TELEMETRY_ENABLED=false`, `GEMINI_TELEMETRY_LOG_PROMPTS=false`
- CLI flag: `--no-telemetry` (per-invocation)
- Persistent: `~/.gemini/settings.json` itself (retains OTLP endpoint if previously enabled)
- Docs: https://google-gemini.github.io/gemini-cli/docs/cli/telemetry.html

## GitHub CLI + Copilot CLI
- Base gh: `GH_TELEMETRY=false` (env) **or** `gh config set telemetry disabled` (persists to `~/.config/gh/config.yml`). Both real.
- **Copilot CLI has no documented standalone telemetry toggle**. Its only global off-switch is offline mode:
  - `COPILOT_OFFLINE=true` — offline mode for BYOK / local providers. Also disables `web_fetch`, `/delegate`, Code Search. NOT a regular telemetry toggle; never auto-apply.
- Persistent:
  - `~/.config/github-copilot/apps.json` — contains plaintext OAuth tokens (`ghu_*`). Ensure mode 0600.
  - `~/.config/github-copilot/versions.json` — extension versions
- Docs: https://docs.github.com/en/copilot/responsible-use/copilot-cli · https://cli.github.com/manual/gh_config

## Cursor
- Manual-only. **Vendor-blessed AI-telemetry control: Cmd+Shift+J (Cursor Settings panel) → Privacy → Privacy Mode dropdown.** NOT Cmd+, — that opens VS Code settings where Privacy Mode is absent.
  - Dropdown tiers: `Default` (training allowed) / `Privacy Mode` (no training; code may be stored for Background Agent) / `Privacy Mode with Storage` (no training, no storage).
  - Browser equivalent: https://cursor.com/settings → Privacy tab.
  - Business-plan accounts: forced on; no action needed.
- VS Code-inherited JSON (in `~/Library/Application Support/Cursor/User/settings.json`): `"telemetry.telemetryLevel": "off"` disables editor/crash-reporting telemetry — does NOT affect Cursor's AI telemetry.
- Cursor rewrites settings.json on graceful shutdown — quit Cursor before any manual JSON edit (`pgrep -x Cursor` check).
- Persistent: `~/.cursor/ai-tracking/`, `~/.cursor/browser-logs/`, `~/Library/Application Support/Cursor/logs/`
- Docs: https://cursor.com/data-use

## Cursor CLI (cursor-agent)
- Distinct binary from editor launcher `cursor`. Installed via `brew install --cask cursor-cli` (ships `cursor-agent`) or `curl https://cursor.com/install -fsS | bash`.
- **Same account, same Privacy Mode.** Cursor's account-level Privacy Mode toggle (Cmd+Shift+J or cursor.com/settings) covers editor + `cursor-agent` CLI + Background Agents. No CLI-specific toggle documented.
- `~/.cursor/cli-config.json` holds preferences (vim mode, default model, shell-permissions allow/deny) — NOT telemetry. Don't invent a key.
- Even with user's own API key: requests still route through Cursor's backend for final prompt building.
- Bundled `merkle-tree-napi.darwin-arm64.node` is unsigned → macOS Gatekeeper blocks first run. Known Cursor release-hygiene issue, not malware. Fix: System Settings → Privacy & Security → "Allow Anyway", or scoped `xattr -rd com.apple.quarantine /opt/homebrew/Caskroom/cursor-cli/`.
- Persistent: `~/.cursor/cli-config.json`, `~/.cursor/ai-tracking/` (shared with editor), `~/.cursor/plans/`.
- Docs: https://cursor.com/data-use, https://docs.cursor.com/cli/overview (JS-SPA; doc-diff scanner only sees data-use page).

## Google Antigravity (macOS)
- Google-branded VS Code fork (v1.107.0). Settings: `~/Library/Application Support/Antigravity/User/settings.json`:
  - `"telemetry.telemetryLevel": "off"` — VS Code-layer telemetry only (Application Insights)
  - `"telemetry.enableCrashReporter": false` — crash dumps to Google
- **AI-training opt-out is EMAIL-ONLY** — no documented settings key or env var. Send to `antigravity-support@google.com` to delete Interactions. Google uses Interactions for ML training unless accessed via Workspace/GCP.
- Close the app (including `Antigravity Helper` children) before editing settings.json — rewrites on graceful quit.
- Persistent: `~/Library/Application Support/Antigravity/{Crashpad,logs,machineid,CachedData}`
- Docs: https://antigravity.google/terms · https://discuss.ai.google.dev/t/antigravity-privacy/138277 · https://discuss.ai.google.dev/t/antigravity-data-training-opt-out/125236

## Visual Studio Code (macOS)
- Settings: `~/Library/Application Support/Code/User/settings.json`:
  - `"telemetry.telemetryLevel": "off"` — covers crash, error, usage tiers. Supersedes deprecated `telemetry.enableTelemetry` / `telemetry.enableCrashReporter` (do NOT double-write).
- CLI flag: `--disable-telemetry` — per-invocation only, not persistent.
- **GitHub Copilot extension does NOT inherit `telemetry.telemetryLevel`** per Microsoft docs — check each extension's own telemetry docs.
- Detection fallback includes `/Applications/Visual Studio Code.app` (fresh installs lack the `code` shim until palette → "Install 'code' command in PATH").
- Persistent: `~/Library/Application Support/Code/{User/globalStorage,logs}`
- Docs: https://github.com/microsoft/vscode-docs/blob/main/docs/configure/telemetry.md · https://code.visualstudio.com/docs/configure/telemetry

## JetBrains PhpStorm (macOS) — manual-only
- No documented file-based toggle. UI path: Settings → Tools → Usage Statistics → uncheck "Send usage statistics" → restart.
- AI Assistant is a **non-bundled plugin, disabled by default**. Only surface its opt-out if `~/Library/Application Support/JetBrains/PhpStorm*/plugins/ai-assistant*` exists (Settings → Tools → AI Assistant → Data Sharing).
- Version paths use yearly suffix — glob `~/Library/Application Support/JetBrains/PhpStorm*`.
- Persistent: `~/Library/Caches/JetBrains/PhpStorm*/{event-log-data/logs/FUS,log}`; undocumented `~/Library/Application Support/JetBrains/consentOptions/accepted` (resets on version upgrade).
- Docs: https://www.jetbrains.com/help/phpstorm/settings-usage-statistics.html · https://www.jetbrains.com/help/ai-assistant/installation-guide-ai-assistant.html

## macOS system privacy (darwin-only)
- **Auto-apply (user-writable)**:
  - `defaults write com.apple.AdLib allowApplePersonalizedAdvertising -bool false` — personalized ads (per-user)
  - `sudo defaults write /Library/Application\ Support/CrashReporter/DiagnosticMessagesHistory AutoSubmit -bool false` — Share Mac Analytics (sudo)
  - `sudo defaults write /Library/Application\ Support/CrashReporter/DiagnosticMessagesHistory ThirdPartyDataSubmit -bool false` — Share with App Developers (sudo)
- **Manual-only (UI)**:
  - Apple Intelligence features — System Settings → Apple Intelligence & Siri. **`com.apple.applicationaccess allow*` keys are MDM-only** on unmanaged Macs; plain `defaults write` silently no-ops. Automation requires installing a signed `.mobileconfig` restrictions profile.
  - Improve Siri & Dictation — System Settings → Privacy & Security → Analytics & Improvements
  - Help Apple Improve Search — System Settings → Spotlight → Search Privacy
  - Share iCloud Analytics — System Settings → Privacy & Security → Analytics & Improvements
- Persistent: `/Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist`, `~/Library/Preferences/com.apple.appleintelligencereporting.plist` (macOS 26+)
- Docs: https://support.apple.com/guide/mac-help/apple-intelligence-and-privacy-mchlfc0d4779/mac · https://support.apple.com/guide/deployment/depba790e53/web (MDM restrictions table)

## Windows system privacy (win32-only; dormant on Mac)
- `reg add` commands — dormant on Mac (rendered as copy-paste checklist):
  - Recall (HKCU, user): `DisableAIDataAnalysis=1` under `HKCU\Software\Policies\Microsoft\Windows\WindowsAI`
  - Recall (HKLM, admin): `AllowRecallEnablement=0` under `HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsAI` (deletes snapshots on reboot)
  - Legacy Copilot (HKCU, user): `TurnOffWindowsCopilot=1` under `HKCU\Software\Policies\Microsoft\Windows\WindowsCopilot` (path is `WindowsCopilot`, not `WindowsAI`)
  - Diagnostic data (HKLM, admin): `AllowTelemetry=0` under `HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection` — **Home: unsupported, floor is Required [1]**
  - Activity history (HKLM, admin): `PublishUserActivities=0`, `UploadUserActivities=0` under `HKLM\SOFTWARE\Policies\Microsoft\Windows\System`
  - Advertising ID (HKCU, user): `Enabled=0` under `HKCU\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo`
  - Cortana (HKLM, admin): `AllowCortana=0` under `HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search`
  - Tailored experiences (HKCU, user): `DisableTailoredExperiences=1` under `HKCU\Software\Policies\Microsoft\Windows\CloudContent`
  - Inking & typing (HKCU, user): `RestrictImplicitTextCollection=1` under `HKCU\Software\Microsoft\InputPersonalization`
- **New Copilot chat app** (post-2024 rebuild): uninstall-only per Microsoft. Not covered by `TurnOffWindowsCopilot`.
- Edge telemetry is NOT part of Windows privacy — configure via Edge's own policy keys (out of scope).
- Docs: https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization · https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-windowsai
