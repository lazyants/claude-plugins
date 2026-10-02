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
# Existing global, role, proxy, grant-option or unexpected grants are rejected
# by the automated post-check; revoke them deliberately and re-run. Mandatory
# MySQL roles also require manual removal/review. Only the named account/host
# is checked; stored routines with elevated definers need separate review.
# App and migrator must use distinct, dedicated usernames; neither may be root.
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

# These credentials are single-line values. Reject option-file line injection
# and client meta-command ambiguity instead of silently changing a password.
validate_password() {
  case "$2" in
    *'
'* | *"$(printf '\r')"*)
      echo "[db-guardrails] $1 must not contain CR or LF characters." >&2
      exit 1
      ;;
  esac
}
validate_password MYSQL_ROOT_PASSWORD "$MYSQL_ROOT_PASSWORD"
validate_password MIGRATOR_PASSWORD "$MIGRATOR_PASSWORD"

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

# Account changes and grants are not transactional. Reject identities that
# would elevate the app again or overwrite an administrative password before
# sending any SQL, rather than relying on the post-check after those changes.
if [ "$MYSQL_USER" = "$migrator_user" ]; then
  echo "[db-guardrails] MYSQL_USER and MIGRATOR_USER must be distinct usernames." >&2
  exit 1
fi
for account in "$MYSQL_USER" "$migrator_user"; do
  case "$account" in
    [Rr][Oo][Oo][Tt])
      echo "[db-guardrails] MYSQL_USER and MIGRATOR_USER must be dedicated non-root accounts." >&2
      exit 1
      ;;
  esac
done

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
escaped_root_pw=$(printf '%s' "$MYSQL_ROOT_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '[client]\npassword="%s"\n' "$escaped_root_pw" > "$defaults_file"

# SQL-escape single quotes in the password — the one value below that is a
# string literal, not an identifier. The SQL session below explicitly disables
# backslash escaping before it uses this value; doubling quotes is then valid
# regardless of the server's initial sql_mode.
escaped_migrator_pw=$(printf '%s' "$MIGRATOR_PASSWORD" | sed "s/'/''/g")

"$db_client" --defaults-extra-file="$defaults_file" -uroot <<SQL
SET SESSION sql_mode = CONCAT_WS(',', NULLIF(@@SESSION.sql_mode, ''), 'NO_BACKSLASH_ESCAPES');

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

# SHOW GRANTS FOR excludes MySQL mandatory roles. MariaDB returns no row for
# this MySQL-only variable, making SHOW ... LIKE portable between engines.
mandatory_roles=$("$db_client" --defaults-extra-file="$defaults_file" -uroot --batch --skip-column-names --raw <<'SQL'
SHOW GLOBAL VARIABLES LIKE 'mandatory_roles';
SQL
)
if printf '%s\n' "$mandatory_roles" | awk -F '\t' 'NF > 1 && $2 != "" { found = 1 } END { exit !found }'; then
  echo "[db-guardrails] verification failed: mandatory roles may restore destructive privileges; review/revoke them before re-running." >&2
  exit 1
fi

app_grants=$("$db_client" --defaults-extra-file="$defaults_file" -uroot --batch --skip-column-names --raw <<SQL
SHOW GRANTS FOR '${MYSQL_USER}'@'${app_host}';
SQL
)
# Fail closed on output we cannot establish as safe, including role/proxy
# assignments, partial revokes, dynamic privileges and WITH GRANT OPTION.
# Never print raw grant rows: MariaDB may include authentication hashes.
if ! printf '%s\n' "$app_grants" | awk '
  BEGIN {
    split("SELECT|INSERT|UPDATE|DELETE|EXECUTE|CREATE|ALTER|INDEX|REFERENCES|LOCK TABLES|CREATE TEMPORARY TABLES", names, "|")
    for (i in names) allowed[names[i]] = 1
  }
  {
    row = toupper($0)
    if (row !~ /^GRANT / || row !~ / ON .* TO / || row ~ /WITH GRANT OPTION/) { bad = 1; next }
    privileges = row
    sub(/^GRANT /, "", privileges)
    sub(/ ON .*/, "", privileges)
    scope = row
    sub(/^.* ON /, "", scope)
    sub(/ TO .*/, "", scope)
    if (scope == "*.*") {
      if (privileges != "USAGE") bad = 1
      next
    }
    count = split(privileges, parts, /, */)
    for (i = 1; i <= count; i++) if (!(parts[i] in allowed)) bad = 1
    seen = 1
  }
  END { exit bad || !seen }
'; then
  echo "[db-guardrails] verification failed: app account has global, inherited, proxy, grant-option or unrecognized privileges." >&2
  echo "[db-guardrails] inspect SHOW GRANTS locally, revoke the extra grants, and re-run; no safe-separation success is claimed." >&2
  exit 1
fi

echo "[db-guardrails] applied: '${MYSQL_USER}'@'${app_host}' without DROP, '${migrator_user}'@'%' with full rights."
