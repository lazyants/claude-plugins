# db-guardrails

**Stop AI coding agents from accidentally emptying your database.**

A Claude Code plugin: an always-on hook that blocks destructive database
commands before they run, plus a stack-aware skill that installs
defense-in-depth safety layers into a project.

It exists because it happened. On one project an agent ran `artisan migrate`
with a test flag that did *not* isolate to the test database — and wiped the
whole development database. Twice. db-guardrails is the hardened result.

## What you get

| Layer | Mechanism | Installed by |
|-------|-----------|--------------|
| 4 | Claude Code hook — blocks destructive Bash commands | the plugin (auto-on) |
| 1 | Database privilege separation — app user has no `DROP` | the `db-guardrails` skill |
| 2 | Framework boot guard — app refuses destructive subcommands | the `db-guardrails` skill |
| 3 | Test-environment isolation — tests target a throwaway DB | the `db-guardrails` skill |

Layer 4 is on the moment you install the plugin and protects **any** project.
Layers 1–3 are installed per-project by running the skill — layer 1 is the
hard guarantee, and it works for MySQL/MariaDB and PostgreSQL.

## Install

```
/plugin marketplace add lazyants/claude-plugins
/plugin install db-guardrails@lazyants
```

The blocking hook is active immediately — no `settings.json` editing.

**Dependency:** the hook parses its input with `jq` (preferred) or `python3`.
At least one must be on `PATH`. If neither is found the hook warns and allows,
rather than breaking every Bash command — so install `jq`.

## Layer 4 — the hook

`block-destructive-db.sh` runs as a `PreToolUse:Bash` hook. It inspects every
Bash command Claude is about to run and blocks it (exit 2, with a message
Claude sees) when it matches a destructive-database pattern. Recognised across
15+ stacks:

- **Raw SQL** — `DROP DATABASE/SCHEMA/TABLE`, `TRUNCATE TABLE`, `DELETE FROM`
  with no `WHERE`
- **Laravel** — `migrate:fresh/refresh/reset/rollback`, `db:wipe`
- **Rails** — `db:drop`, `db:reset`, `db:schema:load`, `db:test:prepare`
- **Django** — `manage.py flush / sqlflush / reset_db`
- **Prisma** — `migrate reset`, `db push --force-reset / --accept-data-loss`
- **TypeORM** `schema:drop` · **Sequelize** `db:drop` · **Knex**
  `migrate:rollback` · **Drizzle** `drizzle-kit drop`
- **Symfony/Doctrine** `doctrine:database:drop`, `doctrine:schema:drop`
- **EF Core** `dotnet ef database drop` · **Alembic** `downgrade base` ·
  **Flyway** `clean` · **Liquibase** `dropAll`
- **MongoDB** `dropDatabase()` · **Redis** `FLUSHALL` / `FLUSHDB`
- **Infrastructure** — `docker compose down -v`, `docker volume rm/prune`,
  `rm -rf` of a database data directory

Blocked attempts are logged to `~/.claude/logs/destructive-db-blocked.log`.

### Bypassing the hook

Deliberate and out-of-band only — start Claude Code with:

```sh
ALLOW_DESTRUCTIVE_DB_HOOK=true claude
```

There is **no** inline comment, flag, or sentinel that re-enables a single
command. That is intentional: an LLM could append a sentinel to any command to
bypass its own guard. The opt-in has to come from the human, before the
session starts.

### What the hook is and is not

It is a **fast heuristic** — instant, legible feedback that catches the
*accidental* destructive command. It is **not** a hard guarantee: a command can
be phrased to slip past a regex, and it cannot stop a non-Claude actor. That is
exactly why layer 1 exists. Run the skill.

## Layers 1–3 — the skill

In any project, run:

```
/db-guardrails
```

The skill detects the database engine and framework, then scaffolds:

- **Layer 1** — privilege-separation SQL/script for MySQL/MariaDB or PostgreSQL.
  The app role loses `DROP`; a separate migrator role keeps it. After this, an
  accidental `DROP TABLE` from the app connection *fails at the database* — it
  is no longer merely discouraged.
- **Layer 2** — a framework boot guard. Drop-in guard files for Laravel,
  Django, Rails and Symfony; Node.js as a documented config pattern (Node
  migration tools have no universal command hook to attach a guard to).
- **Layer 3** — test-environment isolation so test runs cannot reach the real
  database.

The skill scaffolds files and prints instructions — it does not run privilege
SQL against your database itself. You apply that step.

## Layout

```
db-guardrails/
├── hooks/
│   ├── hooks.json                  # wires the PreToolUse:Bash hook
│   └── block-destructive-db.sh     # layer 4 — the blocker
├── skills/db-guardrails/
│   ├── SKILL.md                    # the /db-guardrails installer skill
│   ├── assets/                     # scaffolding for layers 1–3
│   └── references/framework-guards.md
└── tests/
    └── block-destructive-db.test.sh
```

## Tests

```sh
bash tests/block-destructive-db.test.sh
```

Covers blocked commands, legitimate look-alikes that must pass (`truncate -s 0`
the coreutil, `php artisan migrate`, `DELETE ... WHERE`, `rm -rf node_modules`),
and the bypass env var.

## License

MIT — see the marketplace `LICENSE`.
