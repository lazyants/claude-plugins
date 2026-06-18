# Migrate vpp/plattform onto the enduser-handbook plugin

**Status:** open · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2)

## Problem

`vpp/plattform` still carries the bespoke `.claude/skills/vpp-handbook/` skill. Now that the project-agnostic `enduser-handbook` plugin ships in the lazy-ants marketplace, vpp should consume the plugin and delete its fork so methodology updates land once.

## Work

1. Install `enduser-handbook@lazyants` in `vpp/plattform`.
2. Create `.claude/handbook/profile.yml` from the plugin's `assets/handbook.profile.example.yml` with vpp's values: `language.code: de`, `language.register: formal_Sie`, `audience.persona: Marktpartner`, Laravel/Vue stack globs, `stack.backend.api_url_prefix: /api/v1`, `capture.engine: playwright` + the docker command, `capture.role_flags.admin: [AuthAdminUser]`, `capture.live_action_examples` / `capture.pii_categories` ported from the old SKILL.md R6/R7, `publish.target: obsidian_vault` with the vault paths and `Voraussetzungen` / `Verwandte Themen` labels.
3. Port the vpp-specific tone/domain content from `references/tone-of-voice.md` into `.claude/handbook/style-guide.md` (referenced via `style_guide.source`), keeping only the German examples / Marktpartner persona / energy-market terminology — the generic disciplines now live in the base skill.
4. **Gate:** run `regression-checks.sh <golden-vpp-chapter> <newly-generated-chapter>` (set `EXPECTED_H1_WORD`) and require exit 0 before deleting the fork.
5. Delete `.claude/skills/vpp-handbook/`.

## Notes

- This is a **GitLab** repo (`git.lazy-ants.de`); the change ships as an MR there, not on GitHub. Only this backlog entry lives in `claude-plugins`.
- Cross-ref: plan `rosy-inventing-stroustrup.md` §E (consumer migration) and Verification §1.
