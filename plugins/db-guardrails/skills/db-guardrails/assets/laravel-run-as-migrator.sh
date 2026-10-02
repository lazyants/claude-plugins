#!/usr/bin/env bash
# db-guardrails — run artisan as the migrator user (Laravel)
# ----------------------------------------------------------
# Use for forward migrations whose up() drops tables, intentional
# migrate:rollback, and deliberate destructive runs. Place at
# `bin/artisan-as-migrator.sh`.
#
#   bin/artisan-as-migrator.sh migrate
#   bin/artisan-as-migrator.sh migrate:rollback
#   ALLOW_DESTRUCTIVE=true bin/artisan-as-migrator.sh migrate:fresh
#
# Environment:
#   MIGRATOR_USER          required migrator DB user — from shell env or .env
#   MIGRATOR_PASSWORD      migrator DB password — from the shell env or .env
#   DB_GUARD_PHP_SERVICE   docker compose service running PHP (default: php-fpm)
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

# Fall back to reading the migrator credentials from .env. Minimal dotenv read:
# strips `KEY=` and surrounding quotes. It deliberately does NOT strip inline
# `# comments` — doing so would truncate a value containing a literal '#'. The
# expected values (`openssl rand -hex 24`) never contain '#' or whitespace.
read_env() {  # $1 = key
  # A missing key must return an empty value successfully so the required
  # input checks below can explain what to set. Avoid a grep/head pipeline:
  # pipefail also turns an absent key (or a SIGPIPE) into a silent early exit.
  awk -v key="$1" 'index($0, key "=") == 1 {
    value = substr($0, length(key) + 2)
    sub(/\r$/, "", value)
    print value
    exit
  }' .env | sed -E "s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/"
}

if [ -f .env ]; then
  if [ -z "${MIGRATOR_PASSWORD:-}" ]; then
    MIGRATOR_PASSWORD="$(read_env MIGRATOR_PASSWORD)"
  fi
  if [ -z "${MIGRATOR_USER:-}" ]; then
    MIGRATOR_USER="$(read_env MIGRATOR_USER)"
  fi
fi

: "${MIGRATOR_USER:?set MIGRATOR_USER in your shell env or in .env}"
: "${MIGRATOR_PASSWORD:?set MIGRATOR_PASSWORD in your shell env or in .env}"

php_service="${DB_GUARD_PHP_SERVICE:-php-fpm}"

# Export so `docker compose exec -e NAME` passes values via the environment
# rather than the host argv (argv is visible via `ps -efww`).
export DB_USERNAME="${MIGRATOR_USER}"
export DB_PASSWORD="${MIGRATOR_PASSWORD}"

docker compose exec \
  -e DB_USERNAME \
  -e DB_PASSWORD \
  ${ALLOW_DESTRUCTIVE:+-e ALLOW_DESTRUCTIVE} \
  "$php_service" php artisan "$@"
