-- db-guardrails — layer 1 for PostgreSQL
-- --------------------------------------
-- The application connects as a role that owns nothing and has no CREATE on
-- the schema. DROP requires ownership; TRUNCATE is a separate privilege that
-- is withheld below. A separate migrator role owns the schema and is used
-- for migrations.
--
-- Run with psql 15+ as a superuser (e.g. `postgres`), against the app database:
--
--   export MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
--   psql -X -U postgres -d <app_db> \
--        -v app_user=myapp \
--        -v migrator_user=myapp_migrator \
--        -f privilege-separation-postgres.sql
--
-- The app's runtime connection keeps using <app_user>. Migrations must run as
-- <migrator_user>. Store the migrator password in the environment, never in a
-- tracked file. Re-running this script resets the migrator password to the
-- value of MIGRATOR_PASSWORD. Do not pass the secret with psql -v: process
-- arguments are visible to other users. Use -X to ignore startup files and
-- leave shell tracing off; the SQL also disables query echo before reading
-- the secret. Superuser/inherited roles and other ownership must be reviewed
-- separately; revoking direct grants does not remove those capabilities.

\set ON_ERROR_STOP on
\set ECHO none
\set ECHO_HIDDEN off
-- PostgreSQL identifiers resolve to the server's name type, which truncates
-- to its compiled identifier limit. Compare that canonical form before any
-- CREATE/ALTER or ownership change: two different long inputs may name one
-- role. App and migrator must remain distinct, nonempty identities.
SELECT :'app_user' <> '' AND :'migrator_user' <> ''
       AND :'app_user'::name <> :'migrator_user'::name AS distinct_role_names
\gset
\if :distinct_role_names
\else
DO $$ BEGIN
  RAISE EXCEPTION 'app_user and migrator_user must resolve to distinct, nonempty PostgreSQL role names';
END $$;
\endif

-- Initialize explicitly: \getenv leaves a variable unchanged if the env
-- key is missing. Never accept an old -v migrator_pw as a secret fallback.
\set migrator_pw ''
\getenv migrator_pw MIGRATOR_PASSWORD
SELECT length(:'migrator_pw') > 0 AS migrator_password_present
\gset
\if :migrator_password_present
\else
DO $$ BEGIN
  RAISE EXCEPTION 'MIGRATOR_PASSWORD must be set to a nonempty value in the environment';
END $$;
\endif

-- 1. Create the migrator role if it does not exist.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'migrator_user', :'migrator_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'migrator_user')
\gexec

-- 2. Ensure the migrator can log in and its password matches what was passed
--    (corrects a pre-existing role created NOLOGIN or with a stale password).
ALTER ROLE :"migrator_user" WITH LOGIN PASSWORD :'migrator_pw';

-- 3. The migrator owns the public schema (so it owns objects created there).
ALTER SCHEMA public OWNER TO :"migrator_user";

-- 4. Hand existing objects owned by the app user over to the migrator.
REASSIGN OWNED BY :"app_user" TO :"migrator_user";

-- 5. Strip every pre-existing privilege from the app role, and CREATE from
--    PUBLIC, so an earlier over-grant cannot survive this run. Then grant
--    back DML only — no CREATE on the schema means the app role can neither
--    create nor own tables. With no inherited/other grants, ownership or
--    superuser rights, it can neither DROP nor TRUNCATE those tables.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM :"app_user";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM :"app_user";
REVOKE ALL ON SCHEMA public FROM :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Also strip database-level CREATE: with it, the app role could CREATE SCHEMA
-- and own tables in a schema of its own, sidestepping the public-schema limit.
SELECT format('REVOKE CREATE ON DATABASE %I FROM PUBLIC, %I', current_database(), :'app_user')
\gexec

GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

-- 6. Tables the migrator creates later get the same app-role grants
--    automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";

-- Verify: \dn+ public  -> Owner must be the migrator, not the app user.
\unset migrator_pw
