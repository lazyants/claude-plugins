# Richer `style_guide.inline` schema / profile validator

**Status:** deferred · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2, codex review IMPORTANT-4 / plan non-goals)

## Problem

Two related v1 simplifications worth revisiting once the schema settles:

1. **`style_guide.inline` is a minimal fallback.** A real tone-of-voice covers ~10 dimensions (address form, sentence style, UI labels, headings, screenshots/alt-text, Diátaxis, terminology, numbers, do/don't). The inline block can't reproduce that, so projects are told to use `style_guide.source`. If inline-only projects become common, expand the inline schema to match the real structure.
2. **No profile validator.** v1 does explicit existence checks (`style_guide.source` must exist; publish-target adapter must exist) but leaves value-shape validation to Claude reading the example. A JSON-Schema/YAML validator (and a `profile_version` migration helper) would catch malformed profiles before the skill runs — especially valuable in non-interactive flows (`/loop`, scheduled runs) where "Claude asks the user to clarify" doesn't work.

## Work

- Decide whether inline should grow to the full 10-dimension shape or stay deliberately minimal.
- If validating: ship a small schema + a check the skill runs at Step 0, with a clear migration message on `profile_version` bumps.

## Notes

- Explicit v1 non-goal (plan §Non-goals). File for when schema churn settles.
