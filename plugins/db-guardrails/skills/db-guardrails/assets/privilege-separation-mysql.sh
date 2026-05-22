#!/bin/sh
# db-guardrails — layer 1 for MySQL / MariaDB
# -------------------------------------------
# Strips DROP from the application database user and creates a separate
# migrator user that holds the destructive rights. An account without DROP
# can run neither `DROP TABLE`/`DROP DATABASE` nor `TRUNCATE TABLE`.
#
# Two ways to use this:
#   (a) Docker — drop this file into the DB image's init directory
#       (`/docker-entrypoint-initdb.d/`, e.g. mount `docker/mariadb/init/`).
#       It runs automatically on a fresh data volume.
#   (b) Existing database — run it once by hand inside the running DB
#       container, with the variables below exported.
#
# Required environment variables:
#   MYSQL_DATABASE       name of the application database
#   MYSQL_USER           the application's database user (loses DROP)
#   MYSQL_ROOT_PASSWORD  root password (to apply the grants)
#   MIGRATOR_PASSWORD    password for the migrator user
# Optional:
#   MIGRATOR_USER        migrator user name (default: <MYSQL_USER>_migrator)
#   DB_APP_HOST          host part of the app account (default: %). If the app
#                        user exists for several hosts (e.g. '%' AND
#                        'localhost'), run this script once per host.
#   DB_GUARDRAILS_SKIP   set to "true" to intentionally skip (e.g. in CI)
#
# By default a missing MIGRATOR_PASSWORD is a hard error — privilege
# separation that silently did not run is worse than a loud failure. Set
# DB_GUARDRAILS_SKIP=true to opt out deliberately.
set -eu

# --- required inputs -------------------------------------------------------
if [ -z "${MIGRATOR_PASSWORD:-}" ]; then
  if [ "${DB_GUARDRAILS_SKIP:-}" = "true" ]; then
    echo "[db-guardrails] DB_GUARDRAILS_SKIP=true and MIGRATOR_PASSWORD unset — privilege separation skipped."
    exit 0
  fi
  echo "[db-guardrails] MIGRATOR_PASSWORD is not set. Set it to apply privilege separation," >&2
  echo "[db-guardrails] or set DB_GUARDRAILS_SKIP=true to skip intentionally (e.g. in CI)." >&2
  exit 1
fi

: "${MYSQL_DATABASE:?MYSQL_DATABASE must be set}"
: "${MYSQL_USER:?MYSQL_USER must be set}"
: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set}"

migrator_user="${MIGRATOR_USER:-${MYSQL_USER}_migrator}"
app_host="${DB_APP_HOST:-%}"

# --- identifier validation -------------------------------------------------
# User / database / migrator names are interpolated into SQL below — reject
# anything outside a safe identifier charset so they cannot inject SQL.
validate_identifier() {  # $1 = label, $2 = value
  case "$2" in
    '' | *[!A-Za-z0-9_-]*)
      echo "[db-guardrails] invalid $1: '$2' — allowed characters are A-Z a-z 0-9 _ -" >&2
      exit 1
      ;;
  esac
}
validate_identifier "MYSQL_DATABASE" "$MYSQL_DATABASE"
validate_identifier "MYSQL_USER" "$MYSQL_USER"
validate_identifier "MIGRATOR_USER" "$migrator_user"

case "$app_host" in
  '' | *[!A-Za-z0-9_.%-]*)
    echo "[db-guardrails] invalid DB_APP_HOST: '$app_host'" >&2
    exit 1
    ;;
esac

# --- pick the client binary ------------------------------------------------
# MariaDB ships `mariadb`, MySQL ships `mysql`. Prefer `mariadb`, fall back.
if command -v mariadb >/dev/null 2>&1; then
  db_client=mariadb
elif command -v mysql >/dev/null 2>&1; then
  db_client=mysql
else
  echo "[db-guardrails] no 'mariadb' or 'mysql' client on PATH; cannot apply privileges." >&2
  exit 1
fi

# --- root password via a temp defaults file, never on argv ----------------
# A command-line `-p<password>` is world-readable via `ps`; an option file
# (mode 0600, removed on exit) is not.
defaults_file="$(mktemp)"
cleanup() { rm -f "$defaults_file"; }
trap cleanup EXIT
# A signal otherwise terminates the script WITHOUT running the EXIT trap,
# leaving the root password on disk. Exit explicitly so EXIT (cleanup) fires.
trap 'exit 1' HUP INT TERM
chmod 600 "$defaults_file"
printf '[client]\npassword=%s\n' "$MYSQL_ROOT_PASSWORD" > "$defaults_file"

# SQL-escape single quotes in the password — the one value below that is a
# string literal, not an identifier (MySQL/MariaDB doubles quotes to escape).
escaped_migrator_pw=$(printf '%s' "$MIGRATOR_PASSWORD" | sed "s/'/''/g")

"$db_client" --defaults-extra-file="$defaults_file" -uroot <<SQL
-- Application user: everything a forward migration needs, but NO DROP.
-- (No DROP also means no TRUNCATE TABLE — MySQL requires DROP for TRUNCATE.)
REVOKE ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* FROM '${MYSQL_USER}'@'${app_host}';
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE,
      CREATE, ALTER, INDEX, REFERENCES, LOCK TABLES,
      CREATE TEMPORARY TABLES
  ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'${app_host}';

-- Migrator user: full rights, used only for migrations and intentional
-- destructive runs. The ALTER re-syncs the password if the user pre-existed.
CREATE USER IF NOT EXISTS '${migrator_user}'@'%' IDENTIFIED BY '${escaped_migrator_pw}';
ALTER USER '${migrator_user}'@'%' IDENTIFIED BY '${escaped_migrator_pw}';
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${migrator_user}'@'%';

FLUSH PRIVILEGES;
SQL

echo "[db-guardrails] applied: '${MYSQL_USER}'@'${app_host}' without DROP, '${migrator_user}'@'%' with full rights."
echo "[db-guardrails] verify with: SHOW GRANTS FOR '${MYSQL_USER}'@'${app_host}';"
