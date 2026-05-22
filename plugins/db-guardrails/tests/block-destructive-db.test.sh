#!/usr/bin/env bash
#
# Tests for hooks/block-destructive-db.sh
# Run: bash tests/block-destructive-db.test.sh
#
# Requires python3 (to JSON-encode test payloads). The hook itself works with
# jq OR python3 — this harness only uses python3 for encoding.
#
set -uo pipefail

HOOK="$(cd "$(dirname "$0")/.." && pwd)/hooks/block-destructive-db.sh"
[[ -f "$HOOK" ]] || { echo "hook not found: $HOOK"; exit 1; }

pass=0
fail=0

json_encode() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

run_hook() {  # $1 = command string ; returns the hook's exit code
  local payload
  payload="$(printf '{"tool_name":"Bash","tool_input":{"command":%s}}' "$(json_encode "$1")")"
  printf '%s' "$payload" | bash "$HOOK" >/dev/null 2>&1
}

expect() {  # $1 = expected exit ; $2 = description ; $3 = command
  run_hook "$3"
  local rc=$?
  if [[ "$rc" == "$1" ]]; then
    pass=$((pass + 1))
    printf 'ok   - %s\n' "$2"
  else
    fail=$((fail + 1))
    printf 'FAIL - %s (expected exit %s, got %s)\n' "$2" "$1" "$rc"
  fi
}

echo "# blocked: raw SQL (expect exit 2)"
expect 2 "DROP TABLE"               'mysql -e "DROP TABLE users"'
expect 2 "DROP TEMPORARY TABLE"     'mysql -e "DROP TEMPORARY TABLE tmp"'
expect 2 "DROP DATABASE"            'mysql -e "DROP DATABASE app"'
expect 2 "TRUNCATE TABLE"           'psql -c "TRUNCATE TABLE sessions"'
expect 2 "TRUNCATE without TABLE"   'psql -c "TRUNCATE sessions"'
expect 2 "DELETE without WHERE"     'mysql -e "DELETE FROM users"'
expect 2 "DELETE QUICK modifier"    'mysql -e "DELETE QUICK FROM users"'
expect 2 "DELETE first stmt unsafe" 'mysql -e "DELETE FROM a; DELETE FROM b WHERE id=1"'
expect 2 "DELETE -- where comment"  'mysql -e "DELETE FROM users -- where"'
expect 2 "dropdb CLI"               'dropdb app_development'
expect 2 "mysqladmin drop (flags)"  'mysqladmin -uroot drop appdb'
expect 2 "mysqladmin drop (bare)"   'mysqladmin drop appdb'

echo "# blocked: framework commands (expect exit 2)"
expect 2 "Laravel migrate:fresh"    'php artisan migrate:fresh --seed'
expect 2 "Laravel db:wipe"          'php artisan db:wipe'
expect 2 "Rails db:drop"            'bin/rails db:drop'
expect 2 "Rails db:reset"           'rake db:reset'
expect 2 "Rails db:purge"           'bin/rails db:purge'
expect 2 "Rails db:truncate_all"    'rails db:truncate_all'
expect 2 "Rails db:structure:load"  'rake db:structure:load'
expect 2 "Prisma migrate reset"     'npx prisma migrate reset'
expect 2 "Prisma force-reset"       'npx prisma db push --force-reset'
expect 2 "TypeORM schema:drop"      'npx typeorm schema:drop'
expect 2 "Django flush (manage.py)" 'python manage.py flush --noinput'
expect 2 "Django flush (admin)"     'django-admin flush --noinput'
expect 2 "Django migrate zero"      'python manage.py migrate myapp zero'
expect 2 "Symfony doctrine drop"    'php bin/console doctrine:database:drop --force'
expect 2 "Flyway maven clean"       'mvn flyway:clean'
expect 2 "Mongo dropDatabase"       'mongosh --eval "db.dropDatabase()"'
expect 2 "redis FLUSHALL"           'redis-cli FLUSHALL'

echo "# blocked: infrastructure (expect exit 2)"
expect 2 "docker compose down -v"   'docker compose down -v'
expect 2 "docker volume rm"         'docker volume rm app_pgdata'
expect 2 "rm -rf data/mysql"        'rm -rf ./data/mysql'
expect 2 "rm --recursive pg_data"   'rm --recursive --force ./pg_data'
expect 2 "rm recursive flag 2nd"    'rm --force --recursive ./pg_data'

echo "# blocked: dry-run flags are NOT exempted (expect exit 2)"
expect 2 "migrate:rollback pretend" 'php artisan migrate:rollback --pretend'
expect 2 "doctrine drop dump-sql"   'php bin/console doctrine:schema:drop --dump-sql'

echo "# allowed (expect exit 0)"
expect 0 "plain ls"                 'ls -la'
expect 0 "forward migrate"          'php artisan migrate --force'
expect 0 "DELETE with WHERE"        'mysql -e "DELETE FROM users WHERE id = 5"'
expect 0 "DELETE with LIMIT"        'mysql -e "DELETE FROM jobs LIMIT 100"'
expect 0 "truncate -s coreutil"     'truncate -s 0 /var/log/docker.log'
expect 0 "docker compose up"        'docker compose up -d'
expect 0 "rm -rf node_modules"      'rm -rf node_modules'
expect 0 "git log"                  'git log --oneline -5'
expect 0 "npm build"                'npm run build'

echo "# allowed: read-only inspection exemptions (expect exit 0)"
expect 0 "grep for DROP TABLE"      'grep -r "DROP TABLE" database/migrations'
expect 0 "grep -E with alternation" 'grep -E "DROP TABLE|TRUNCATE" database/migrations'
expect 0 "rg for migrate:fresh"     'rg "migrate:fresh" .'
expect 0 "git grep db:wipe"         'git grep "db:wipe"'

echo "# bypass (ALLOW_DESTRUCTIVE_DB_HOOK=true => exit 0)"
bypass_payload="$(printf '{"tool_name":"Bash","tool_input":{"command":%s}}' "$(json_encode 'mysql -e "DROP TABLE users"')")"
printf '%s' "$bypass_payload" | ALLOW_DESTRUCTIVE_DB_HOOK=true bash "$HOOK" >/dev/null 2>&1
bypass_rc=$?
if [[ "$bypass_rc" == "0" ]]; then
  pass=$((pass + 1)); echo "ok   - bypass env var allows DROP TABLE"
else
  fail=$((fail + 1)); echo "FAIL - bypass (expected exit 0, got $bypass_rc)"
fi

echo "# chained command is NOT exempted by a benign prefix (expect exit 2)"
expect 2 "grep then drop chained"   'grep -r foo . ; mysql -e "DROP TABLE users"'

echo ""
echo "passed: $pass   failed: $fail"
[[ "$fail" == "0" ]]
