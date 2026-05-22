# Framework boot guards (layer 2) — reference

Layer 1 (database privilege separation) is framework-agnostic and carries the
real guarantee — apply it for every stack from `privilege-separation-mysql.sh`
or `privilege-separation-postgres.sql`. Layer 2 is a smaller, earlier, more
legible check, and it *is* framework-specific.

In every case the pattern is the same: **the migration tool runs as the
migrator role; the app's runtime connection runs as the restricted role**, and
a boot-time guard refuses the destructive subcommands unless
`ALLOW_DESTRUCTIVE=true` is set.

Each framework below has a ready-made drop-in asset (except Node — see why
there). The skill copies the file in; this page explains placement and the
rationale.

## Laravel — `assets/laravel-*`

- `laravel-DestructiveCommandGuard.php` → `app/Support/DestructiveCommandGuard.php`,
  registered as a `CommandStarting` listener in `AppServiceProvider::boot()`.
  It aborts `migrate:fresh/refresh/reset/rollback` and `db:wipe` when the
  target connection driver is `mysql`/`mariadb`, and fails closed when the
  config is cached.
- `laravel-env.testing.example` → `.env.testing` — `--env=testing` then routes
  to in-memory SQLite.
- `laravel-run-as-migrator.sh` → `bin/artisan-as-migrator.sh` — runs artisan as
  the migrator user for legitimate destructive migrations.

## Django — `assets/django-db_guardrails.py`

Drop the file at the project root (next to `manage.py`, so `import
db_guardrails` resolves) and call `db_guardrails.guard()` at the top of
`settings.py`. `settings.py` is imported for every management command, so the
guard sees every invocation. It aborts `flush`, `sqlflush`, `reset_db` and
`migrate <app> zero`, and exempts `manage.py test` (which uses a throwaway test
database). Test isolation is otherwise built in — just confirm the real
`DATABASES['default']` points at the restricted role from layer 1.

## Rails — `assets/rails-db_guardrails.rb`

Drop the file at `config/initializers/db_guardrails.rb` — no registration step.
It enhances the destructive `db:*` rake tasks (`db:drop`, `db:reset`,
`db:schema:load`, `db:test:prepare`, `db:migrate:reset`) with a guard
prerequisite that aborts outside the `test` environment. It works regardless of
rake-task load order because, by the time initializers run inside the
`environment` task, the `db:*` tasks are already defined. Test isolation is
built in via the `test` environment in `config/database.yml`.

## Symfony — `assets/symfony-DestructiveCommandGuard.php`

Drop the file at `src/Console/DestructiveCommandGuard.php`. It is an
`EventSubscriberInterface` on `ConsoleEvents::COMMAND`; with Symfony's default
autoconfiguration it self-registers, otherwise tag it
`kernel.event_subscriber`. It throws on `doctrine:database:drop` and
`doctrine:schema:drop` unless `APP_ENV=test` or `ALLOW_DESTRUCTIVE=true`. Test
isolation — a separate `DATABASE_URL` in `.env.test`.

## Node ORMs (Prisma, TypeORM, Sequelize, Knex, Drizzle)

There is **no drop-in guard file** for Node — unlike Laravel's `CommandStarting`
event, Rails' rake tasks or Symfony's console events, Node migration tools have
no universal command hook to attach a guard to. For Node, layers 1 and 4 carry
the protection, backed by a config discipline:

1. **Layer 1** — the privilege-separated DB role. This is the guarantee.
2. **Split connection strings** — `DATABASE_URL` (restricted, app runtime) vs a
   `MIGRATOR_DATABASE_URL` used only by the migration npm script. A
   `package.json` `"migrate"` script that sets the migrator URL keeps the two
   apart; the app runtime never sees the migrator credentials.
3. **Test isolation** — a dedicated test database or a disposable container;
   never let the test runner's `DATABASE_URL` point at the real database.
4. **Layer 4** — the plugin's hook already blocks `prisma migrate reset`,
   `typeorm schema:drop`, `sequelize db:drop`, `knex migrate:rollback` and
   `drizzle-kit drop` at the Claude Code level.

## MongoDB

No SQL privilege model. Create the application user with a scoped role —
`readWrite` on the app database but **not** `dbAdmin` or `dbOwner`:

```javascript
db.createUser({
  user: "app_user",
  pwd: "...",
  roles: [{ role: "readWrite", db: "app_db" }],   // no dbAdmin => no dropDatabase
});
```

A `readWrite`-only user cannot run `db.dropDatabase()` or `db.collection.drop()`.
