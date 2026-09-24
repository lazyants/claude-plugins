# software-localizer

Translate a software project's user-interface strings into another language, or audit the
translation it already has, with `literary-translator`'s discipline: a canon and glossary
decided once, translation in small checked units, a review of every value, and a report for a
native speaker.

- **Core, not formats.** The plugin ships the canon, the checks, the model-turn contracts, the
  ledger, the export pipeline and the reports. How strings are collected from a project and
  written back — and how message syntax is parsed — is a per-project **adapter** written when
  the skill is used (see `skills/software-localizer/references/adapter-contract.md`), accepted
  only after round-trip, parse and coverage checks.
- **Two modes.** *Translate* new and changed strings incrementally; *audit* the existing
  translation and propose fixes a person accepts.
- **Models.** Codex translates (read-only, returns JSON); Claude subagents review, audit, propose
  the canon and check adapter coverage.
- **Safety.** A person's translation is never overwritten; nothing is exported without script
  checks and a review verdict bound to the exact value; export re-collects, compares, and writes
  under a journal that rolls back on failure.

The procedure is in `skills/software-localizer/SKILL.md`. Scripts are stdlib-only Python ≥ 3.11.

Tests: `bash tests/run-all.sh` from this directory (CI runs it on Python 3.11 and 3.14).
