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
Layers 1–3 are installed per-project by running the skill. Layer 1 provides
database-enforced schema-deletion protection for MySQL/MariaDB, PostgreSQL
and SQL Server after effective grants and ownership are verified. An app
with `DELETE` rights can still delete rows.

## Install

```
/plugin marketplace add lazyants/claude-plugins
/plugin install db-guardrails@lazyants
```

The blocking hook is active immediately — no `settings.json` editing.

**Dependency:** the hook parses its input with `jq` (preferred) or `python3`.
At least one must be on `PATH`. If neither is found the hook exits 1 with a
non-blocking error: the command proceeds and the transcript shows that the
guard is inactive. Install `jq` to restore protection.

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
- **Symfony/Doctrine** `doctrine:database:drop`, `doctrine:schema:drop`,
  fixture loading without `--append`, schema updates with `--force`
- **EF Core** `dotnet ef database drop` · **Alembic** `downgrade base` ·
  **Flyway** `clean` · **Liquibase** `dropAll`
- **MongoDB** database/collection drops, empty-filter `deleteMany({})` and
  `remove({})` · **Redis** `FLUSHALL` / `FLUSHDB`
- **Infrastructure** — `docker compose down -v`, `docker volume rm/prune`,
  `docker system prune --volumes`, recursive removal of a database data directory

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

- **Layer 1** — privilege-separation SQL/script for MySQL/MariaDB, PostgreSQL
  or SQL Server.
  The app role loses `DROP`; a separate migrator role keeps it. After this, an
  accidental `DROP TABLE` from the app connection *fails at the database* — it
  is no longer merely discouraged.
- **Layer 2** — a framework boot guard. Drop-in guard files for Laravel,
  Django, Rails and Symfony; Node.js as a documented config pattern (Node
  migration tools have no universal command hook to attach a guard to).
  EF Core similarly uses separate runtime and migrator connections and a
  migration-bundle pattern, with no universal boot interceptor.
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
    ├── block-destructive-db.test.sh
    ├── rails-db-guardrails.test.rb
    ├── credential-assets.test.py
    ├── symfony-db-guardrails.test.php
    ├── sqlserver-installer.test.py
    └── sqlserver-integration.test.py
```

## Tests

```sh
python3 tests/credential-assets.test.py
php tests/symfony-db-guardrails.test.php
python3 tests/sqlserver-installer.test.py
```

Run only the test file covering a local change; the complete suites run in
GitHub Actions. CI covers blocked commands, legitimate look-alikes
(`truncate -s 0`, `php artisan migrate`, `DELETE ... WHERE`,
`rm -rf node_modules`), both parsers, and the bypass env var. Test HOME is
temporary, so fixture blocks cannot pollute the user's audit log.

Credential tests use fake clients for transport/diagnostics and disposable
MySQL, MariaDB and PostgreSQL services in CI for authentication and privilege
checks. SQL Server has installer tests plus a required real-engine CI job;
the integration file requires its disposable container and never skips
missing setup. Symfony tests invoke the guard with console input/event stubs.

The Rails suite invokes real Rake tasks with a stub Rails environment and
database marker actions. It covers the first invocation, either task-definition
order, destructive prerequisites (including concurrent Rake invocation),
test/override exemptions and safe commands.
The Rails asset installs at `lib/tasks/db_guardrails.rake`; replace the old
`config/initializers/db_guardrails.rb` placement when upgrading.

## License

MIT — see the marketplace `LICENSE`.
