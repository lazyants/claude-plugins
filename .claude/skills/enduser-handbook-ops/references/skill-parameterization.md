# Profile keys: every key needs a consumer

`enduser-handbook` is built on a per-project profile file (`.claude/handbook/profile.yml`) that
carries every binding. Read this before adding, renaming or removing a profile key.

(The general mechanism — why Claude Code has no frontmatter/`args`/env parameterization and how the
read-a-known-per-project-file pattern replaces it — lives in
`.claude/skills/plugin-repo-mechanics/references/skill-parameterization.md`.)

## Audit discipline: every profile key needs a consumer

Every key in the example profile must have a consumer in SKILL.md or a reference doc. Codex flagged
several dead keys on the original pass — `stack.*.type` and a phantom `capture.api_url_prefix` key
path — that needed wiring or removal. Run a key→consumer audit before shipping a profile change.

(Both examples were resolved and are live: `stack.backend.type` / `stack.frontend.type` are read in
the enduser-handbook SKILL.md page-identification step, and the real key is
`stack.backend.api_url_prefix`, not `capture.api_url_prefix` — grep the example profile and the skill
for the key you are adding, in both directions.)
