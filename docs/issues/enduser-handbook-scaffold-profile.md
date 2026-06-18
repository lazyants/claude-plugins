# `/scaffold-profile` slash command for enduser-handbook

**Status:** deferred · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2, plan open question #2)

## Problem

Adopting the plugin in a new project means hand-writing `.claude/handbook/profile.yml` from the example. A guided scaffold would lower the barrier and reduce mis-keyed profiles.

## Work

Add a `commands/scaffold-profile` (or a skill entry point) that:
1. Reads `composer.json` / `package.json` / framework markers to guess `stack.backend.type` / `stack.frontend.type` and likely route/page globs.
2. Asks the user ~5 questions (language, register, audience, publish target, capture command).
3. Writes a populated `.claude/handbook/profile.yml` (and a `style-guide.md` stub) from the answers, then runs the base skill's Step-0 validation against it.

## Notes

- Clear v1.x quality-of-life feature; v1 intentionally ships only the example file + instructions.
- Should reuse the canonical key set and comments from `assets/handbook.profile.example.yml` so the two never drift.
