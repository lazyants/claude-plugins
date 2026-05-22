---
name: db-guardrails
description: Install defense-in-depth database safety layers into a project — database-level privilege separation, a framework boot guard, and test-environment isolation. Complements the always-on db-guardrails blocking hook. Use when the user says "harden the database", "protect the database", "db guardrails", "stop dropping the database", "set up destructive command protection", "database privilege separation", "migrator user", or asks to make accidental database wipes impossible.
---

# db-guardrails — install the deep protection layers

The `db-guardrails` plugin already ships an **always-on hook** (`block-destructive-db.sh`)
that blocks destructive database commands at the Claude-Code level. That hook is
layer 4 — fast feedback, but a heuristic.

This skill installs the **deeper layers** that turn "blocked by a regex" into
"physically cannot happen". Run it once per project.

## The four layers

| Layer | What it is | Guarantee |
|-------|-----------|-----------|
| 1 | Database privilege separation | **Hard** — the app DB user has no `DROP` |
| 2 | Framework boot guard | Medium — app refuses destructive subcommands |
| 3 | Test-environment isolation | Medium — test runs target a throwaway DB |
| 4 | Claude Code hook (already installed by the plugin) | Heuristic — fast feedback |

Layer 1 is the one that actually matters. Layers 2–4 catch the mistake earlier
and more legibly, but layer 1 is what makes a wipe *impossible* rather than
*discouraged*.

## Procedure

### Step 1 — detect the stack

Inspect the project to determine:

- **Database engine** — look in `docker-compose*.yml`, `.env`, `config/database.php`,
  `config/database.yml`, `settings.py`, `prisma/schema.prisma`, `ormconfig`, etc.
  Identify MySQL/MariaDB, PostgreSQL, SQLite, or MongoDB.
- **Framework** — `artisan` + `composer.json` (Laravel), `bin/rails` + `Gemfile`
  (Rails), `manage.py` (Django), `bin/console` (Symfony), `package.json`
  dependencies (`prisma`, `typeorm`, `sequelize`, `knex`, `drizzle-kit`).

Tell the user what you found before changing anything.

> SQLite-only projects need no layer 1 — the protection there is a file backup
> and the layer-4 hook. MongoDB has no SQL privilege model; use a scoped role
> (`db.createUser` with `readWrite` but not `dbAdmin`).

### Step 2 — layer 1: database privilege separation

The principle is identical for every SQL database: the **application** connects
as a role that **cannot drop or truncate**; a **separate migrator role** holds
the destructive rights and is used only for migrations.

- **MySQL / MariaDB** — copy `assets/privilege-separation-mysql.sh` into the
  project (for the Docker image, `docker/mariadb/init/` so it runs on a fresh
  volume; otherwise run it once by hand). It revokes `DROP` from the app user
  and creates a migrator user. In MySQL, an account without `DROP` can run
  neither `DROP TABLE` nor `TRUNCATE TABLE` — both layers in one grant.
- **PostgreSQL** — copy `assets/privilege-separation-postgres.sql` and run it
  as a superuser. The migrator role owns the schema; the app role gets DML only
  and no `CREATE` on the schema, so it cannot own — therefore cannot drop —
  tables.

Generate the migrator password with `openssl rand -hex 24`. Store it in the
shell environment or a gitignored `.env`, **never** in a tracked file. Confirm
the result: MySQL `SHOW GRANTS FOR '<app_user>'@'%'` must show no `DROP`;
Postgres — the app role must not own `public` (`\dn+ public`).

### Step 3 — layers 2 & 3: framework boot guard + test isolation

Every supported framework has a **drop-in guard asset**. Copy the matching
file, place it where noted, and register it. `references/framework-guards.md`
carries the detail and the rationale for each.

**Laravel**

- `assets/laravel-DestructiveCommandGuard.php` → `app/Support/DestructiveCommandGuard.php`.
  Register in `AppServiceProvider::boot()`:

  ```php
  use App\Support\DestructiveCommandGuard;
  use Illuminate\Console\Events\CommandStarting;
  use Illuminate\Support\Facades\Event;

  Event::listen(CommandStarting::class, fn ($e) => (new DestructiveCommandGuard())->check($e));
  ```

- `assets/laravel-env.testing.example` → `.env.testing` (routes `--env=testing`
  to in-memory SQLite — it can never touch the real database).
- `assets/laravel-run-as-migrator.sh` → `bin/artisan-as-migrator.sh` — wrapper
  that runs migrations as the migrator user. Adjust the container service name
  if the project's is not `php-fpm`.

**Django**

- `assets/django-db_guardrails.py` → project root, next to `manage.py`. Add to
  the top of `settings.py`: `import db_guardrails; db_guardrails.guard()`.
- Test isolation is built in — Django uses a throwaway test database. Confirm
  the real `DATABASES['default']` uses the restricted role from step 2.

**Rails**

- `assets/rails-db_guardrails.rb` → `config/initializers/db_guardrails.rb`.
  No registration step — initializers load automatically.
- Test isolation is built in (the `test` environment in `config/database.yml`).

**Symfony**

- `assets/symfony-DestructiveCommandGuard.php` → `src/Console/DestructiveCommandGuard.php`.
  With autoconfiguration (the default) it self-registers; otherwise tag it
  `kernel.event_subscriber` in `config/services.yaml`.
- Test isolation — a separate `DATABASE_URL` in `.env.test`.

**Node ORMs (Prisma, TypeORM, Sequelize, Knex, Drizzle)**

Node has no universal command hook, so there is no drop-in guard file. Layer 1
plus the layer-4 hook carry it; see `references/framework-guards.md` for the
connection-string split + npm-script pattern.

### Step 4 — summarise

Report what was installed per layer, and state the two escape hatches plainly:

- Forward migrations that legitimately drop a table run via the migrator
  user/role (the wrapper script, or `ALLOW_DESTRUCTIVE=true` for Laravel).
- The layer-4 hook is bypassed only by restarting Claude Code with
  `ALLOW_DESTRUCTIVE_DB_HOOK=true` in the shell — a deliberate human action.

## Notes

- Never write the migrator password into a tracked file. If one is ever
  committed, rotate it at the database.
- Layer 1 changes are real infrastructure changes. On shared/production
  databases, get explicit user confirmation before applying, and apply through
  the proper control plane rather than ad-hoc SQL where one exists.
- This skill scaffolds files; it does not run destructive SQL itself. The user
  runs the privilege script against their database.
