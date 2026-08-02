# Making one base skill behave differently per project

Design-time knowledge for the next configurable skill shipped from this repo: one
marketplace-distributed skill carrying the methodology, plus a per-project profile file carrying
every binding. Read it before proposing a "configurable" skill, or before reaching for a
parameterization mechanism that does not exist.

## Claude Code offers no per-project skill parameterization

There is no config/param field in SKILL.md frontmatter, no env vars passed into a skill, and no
variable interpolation or includes. CLAUDE.md is human prose, not a config source.

**What DOES exist, and why it still does not solve this** (surveyed 2026-08-02 across 399 installed
`SKILL.md` files — supersedes an earlier, now-inaccurate "there are no CLI args" claim): the Skill
tool exposes an `args` passthrough parameter, and `argument-hint:` frontmatter documents it. The
frontmatter fields actually in use across those 399 files are `name`, `description`, `license`,
`version`, `author`, `tags`, `dependencies`, `user-invocable`, `allowed-tools`, `metadata`, `source`,
`tools`, `argument-hint`, `repo`, `trigger`, `disable-model-invocation`. **None of them is a
per-project config/param field.** `args` is PER-INVOCATION: the user would have to retype every
binding on every invocation, so it does not express a per-project binding at all.

## The working mechanism

**Instruct the skill, in plain prose, to `Read` a known per-project file as its first step**, then
drive all variable behavior off that file's keys.

Pattern (proven in the `enduser-handbook` plugin — see [[project]]):

- Base skill ships the methodology; per-project file (`.claude/<skill>/profile.yml`) carries the
  bindings (language, paths, stack globs, commands, publish target).
- First instruction: "Read `.claude/<skill>/profile.yml` (relative to project root = cwd at
  invocation). If missing, **halt** with a copy-the-example message. If `profile_version` unknown,
  halt. Unknown key in a known version → one-line warning, continue."
- Reference every variable as a profile key (`stack.backend.route_globs`), never a literal.
- Distribute as a marketplace plugin so methodology updates ship once instead of being hand-merged
  across N project copies.

Why not alternatives: user-level `~/.claude/skills/` fragments across machines; per-project skill
copies drift; `skill-creator` stamping a template gives no DRY. Validated by two independent
mechanism surveys — every non-"read-a-file" option was unsupported or strictly worse.

Once a profile exists, every key in it needs a consumer. That audit discipline, with the live
`enduser-handbook` key names it was derived from, lives in
`.claude/skills/enduser-handbook-ops/references/skill-parameterization.md`.
