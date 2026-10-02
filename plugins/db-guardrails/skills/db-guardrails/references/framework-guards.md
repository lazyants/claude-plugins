# Framework boot guards (layer 2) — reference

Layer 1 (database privilege separation) is framework-agnostic and carries the
schema-deletion protection — apply the engine-specific privilege asset for
MySQL/MariaDB, PostgreSQL or SQL Server. Layer 2 is a smaller, earlier, more
legible check, and it *is* framework-specific.

In every case the pattern is the same: **the migration tool runs as the
migrator role; the app's runtime connection runs as the restricted role**, and
a boot-time guard refuses the destructive subcommands unless
`ALLOW_DESTRUCTIVE=true` is set.

Laravel, Django, Rails and Symfony have ready-made drop-in assets. Node and
EF Core use connection configuration patterns. This page explains placement and the
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

Copy the file to `lib/tasks/db_guardrails.rake` — no registration step.
Remove `config/initializers/db_guardrails.rb` if upgrading an existing install.
Rails loads `.rake` files at task-definition time, before Rake copies a task's
prerequisites for invocation. An initializer runs inside the `environment`
prerequisite, after that copy, so adding a guard there misses the first run.

The asset guards `db:drop`, `db:reset`, `db:purge`,
`db:truncate_all`, `db:schema:load`, `db:structure:load`, `db:test:prepare` and
`db:migrate:reset`. Each task invokes the guard synchronously before dispatching
its other prerequisites, including in Rake's `--multitask` mode. It
defines placeholders for tasks loaded later, so either definition order works.
The guard boots `environment` and checks `Rails.env` at invocation time,
allowing only the `test` environment or the exact override
`ALLOW_DESTRUCTIVE=true`. Environment boot is cached by Rake; the safety check
itself runs on every destructive-task execution, including after re-enabling
just that task. Test isolation is built in via the `test` environment
in `config/database.yml`.

## Symfony — `assets/symfony-DestructiveCommandGuard.php`

Drop the file at `src/Console/DestructiveCommandGuard.php`. It is an
`EventSubscriberInterface` on `ConsoleEvents::COMMAND`; with Symfony's default
autoconfiguration it self-registers, otherwise tag it
`kernel.event_subscriber`. It throws on `doctrine:database:drop` and
`doctrine:schema:drop`, fixture loading without `--append`, and schema updates
with `--force`, unless `APP_ENV=test` or `ALLOW_DESTRUCTIVE=true`. Test
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

## .NET / EF Core — SQL Server

Apply the SQL Server privilege installer from step 2. Configure the app's
`ConnectionStrings` entry with the restricted app login; do not run
`Database.Migrate()` at startup under a privileged runtime connection.
Use a distinct migrator connection only in the deployment job. A migration
bundle can be built without embedding credentials:

```sh
ASPNETCORE_ENVIRONMENT=Production dotnet ef migrations bundle --output artifacts/efbundle
```

Run the bundle with its expected `ConnectionStrings` configuration injected
through the deployment secret store (for a key named `AppDb`, use
`ConnectionStrings__AppDb`). The bundle's configuration code must read that
key; keep required `appsettings.json` alongside it. Set the intended environment
when executing the bundle too (`ASPNETCORE_ENVIRONMENT=Production`). Avoid the `--connection`
argument for secret-bearing strings, since it exposes the secret on argv.
Protect those deployment files and environment; remove them after the job.
See the [EF Core migration bundle guidance](https://learn.microsoft.com/en-us/ef/core/managing-schemas/migrations/applying#bundles).

EF Core does not offer a universal command interceptor to install as a boot
guard. Layers 1 and 4 plus this connection split carry the protection.
The hook blocks `dotnet ef database drop`; legitimate destructive migrations
run only in the authorized migrator job. Tests use a separate SQL Server
database or disposable container, with credentials that cannot reach the
development or production database. Other EF Core database providers use
their corresponding privilege asset.

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

A `readWrite`-only user cannot run `db.dropDatabase()`, but **can** drop
collections and remove every document. It is not a collection-wipe guarantee.
The [MongoDB built-in role reference](https://www.mongodb.com/docs/manual/reference/built-in-roles/)
lists `dropCollection` and `remove` among its actions.

If the app must not drop collections, create a custom role with an explicit
privilege list copied from the installed server's `readWrite` role, excluding
`dropCollection`. Do not inherit `readWrite` as well: inherited grants would
restore the removed permission. Run this as a role administrator on `app_db`:

```javascript
const rw = db.getRole("readWrite", { showPrivileges: true });
const privileges = rw.inheritedPrivileges.map(p => ({
  resource: p.resource,
  actions: p.actions.filter(action => action !== "dropCollection"),
})).filter(p => p.actions.length > 0);
db.createRole({ role: "appNoCollectionDrop", privileges, roles: [] });
db.updateUser("app_user", { roles: [{ role: "appNoCollectionDrop", db: "app_db" }] });
```

Review that privilege list against the deployed MongoDB version and verify
the user's effective roles. Removing `dropCollection` still permits mass
deletes through `remove`; MongoDB privileges do not distinguish an empty
delete filter from a selective one. Removing `remove` blocks all deletes,
which may be incompatible with the app. Keep test data in a separate database.
The hook catches collection drops and literal empty-filter `deleteMany({})`
and `remove({})` commands, but remains a heuristic.
