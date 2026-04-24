# Disclaimer

`ai-cli-optout` applies documented, vendor-published opt-out flags for telemetry, error reporting, analytics, feedback surveys, and related data collection across locally installed AI CLIs and AI-enabled IDEs.

**No warranty.** This plugin is provided "as is" per the MIT License. Running it does not guarantee that any vendor stops collecting data. It applies the flags the vendors themselves document at the time the baseline was recorded; vendors change those flags silently, and some flags have known silent-no-op cases (e.g. Apple Intelligence `applicationaccess` keys require an MDM profile on unmanaged Macs).

**Verify independently.** For regulated environments — GDPR Art. 28 processor agreements, HIPAA BAAs, SOC 2 controls, corporate DLP, zero-data-retention contracts — do not rely on this plugin alone. Confirm with vendor support, check network egress, and audit persistent local state (the plugin reports these files but does not delete them).

**Not affiliated** with Anthropic, OpenAI, Google, Microsoft, GitHub, JetBrains, Cursor, Vercel, or any other vendor whose product this plugin surfaces opt-outs for. Vendor names, product names, and trademarks remain the property of their respective owners.

**Opt-out is not the same as data deletion.** Existing local state (conversation logs, OAuth tokens, model caches, telemetry queues) is reported by the plugin but never removed. You decide whether to delete it.

**Prompts and outputs** still go to each vendor's API unless you switch providers (e.g. `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` / `CLAUDE_CODE_USE_FOUNDRY` for Anthropic). That is the essential data path; opt-out flags only cover auxiliary signals.

**Community-maintained.** Vendor flags churn. Re-verify quarterly — or every time a vendor ships a major CLI/IDE update — before trusting the output.
