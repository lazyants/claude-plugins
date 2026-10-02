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

# Every synthetic denial must stay out of the operator's real audit trail.
TEST_TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TEST_TMPDIR"' EXIT
export HOME="$TEST_TMPDIR/home"
mkdir -p "$HOME"
unset ALLOW_DESTRUCTIVE_DB_HOOK

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
expect 2 "DELETE wrapped FROM"      $'psql -c "DELETE\nFROM users"'
expect 2 "DELETE multiline comment" $'psql -c "DELETE\nFROM users -- where id=5\n"'
expect 2 "DELETE compact SQL comment" $'psql -c "DELETE\nFROM users --where id=5"'
expect 2 "DELETE shell long options retained" 'mysql --user=root -e "DELETE FROM users"'
expect 2 "DELETE escaped argument retained" 'mysql --user=root -e DELETE\ FROM\ users'
expect 2 "DELETE separate shell calls" $'mysql -e "DELETE FROM a"\nmysql -e "DELETE FROM b WHERE id=1"'
expect 2 "DELETE chained bounded sibling" 'mysql -e "DELETE FROM a" && mysql -e "DELETE FROM b WHERE id=1"'
expect 2 "DELETE bounded commented sibling" 'mysql -e "DELETE FROM a WHERE id=1 -- comment"; mysql -e "DELETE FROM b"'
expect 2 "DELETE wrapped unsafe first stmt" $'psql -c "DELETE\nFROM a; DELETE FROM b\nWHERE id=1"'
expect 2 "DELETE wrapped unsafe last stmt" $'psql -c "DELETE FROM a\nWHERE id=1; DELETE\nFROM b"'
expect 2 "DELETE table name is no WHERE" 'psql -c "DELETE FROM somewhere"'
expect 2 "DELETE heredoc wrapped FROM" $'psql <<\'SQL\'\nDELETE\nFROM users;\nSQL'
expect 2 "DELETE heredoc distinct statements" $'psql <<SQL\nDELETE\nFROM a;\nDELETE FROM b WHERE id=1;\nSQL'
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
expect 2 "Symfony fixtures purge"    'php bin/console doctrine:fixtures:load --no-interaction'
expect 2 "Symfony later append no exemption" 'php bin/console doctrine:fixtures:load; echo --append'
expect 2 "Symfony newline append no exemption" $'php bin/console doctrine:fixtures:load\necho --append'
expect 2 "Symfony append=false no exemption" 'php bin/console doctrine:fixtures:load --append=false'
expect 2 "Symfony outer append no exemption" 'echo "$(php bin/console doctrine:fixtures:load)" --append'
expect 2 "Symfony append in option value no exemption" 'php bin/console doctrine:fixtures:load --group="--append"'
expect 2 "Symfony append in semicolon value no exemption" 'php bin/console doctrine:fixtures:load --group="example;--append"'
expect 2 "Symfony forced schema update" 'php bin/console doctrine:schema:update --force'
expect 2 "Symfony force before command" 'php bin/console --force doctrine:schema:update'
expect 2 "Flyway maven clean"       'mvn flyway:clean'
expect 2 "Mongo dropDatabase"       'mongosh --eval "db.dropDatabase()"'
expect 2 "Mongo collection drop"    'mongosh --eval "db.sessions.drop()"'
expect 2 "Mongo getCollection drop" 'mongosh --eval "db.getCollection(\"sessions\").drop()"'
expect 2 "Mongo deleteMany empty filter" 'mongosh --eval "db.users.deleteMany({})"'
expect 2 "Mongo remove empty filter" 'mongo --eval "db.users.remove({})"'
expect 2 "Mongo spaced empty filter" $'mongosh --eval "db.users.deleteMany( {\n} )"'
expect 2 "Mongo empty filter with options" 'mongosh --eval "db.users.deleteMany({}, {writeConcern: {w: 1}})"'
expect 2 "redis FLUSHALL"           'redis-cli FLUSHALL'

echo "# blocked: infrastructure (expect exit 2)"
expect 2 "docker compose down -v"   'docker compose down -v'
expect 2 "docker volume rm"         'docker volume rm app_pgdata'
expect 2 "docker system prune volumes" 'docker system prune --volumes -f'
expect 2 "docker system prune explicit true" 'docker system prune --volumes=true'
expect 2 "docker system prune after echo" 'echo ready && docker system prune --volumes'
expect 2 "docker outer invocation spans substitution" 'docker system prune "$(echo ready)" --volumes'
expect 2 "rm -rf data/mysql"        'rm -rf ./data/mysql'
expect 2 "rm --recursive pg_data"   'rm --recursive --force ./pg_data'
expect 2 "rm recursive flag 2nd"    'rm --force --recursive ./pg_data'
expect 2 "rm -rf after path"        'rm /var/lib/mysql -rf'
expect 2 "rm -r after path"         'rm ./data/mysql -r'
expect 2 "rm --recursive after path" 'rm ./data/postgres --recursive'
expect 2 "rm flag among paths"      'rm ./data/mysql -r ./tmp'
expect 2 "rm quoted path and flag"  'rm "/var/lib/postgresql" "-rf"'
expect 2 "rm uppercase -R"          'rm ./data/mariadb -R'
expect 2 "rm outer invocation spans substitution" 'rm /var/lib/mysql "$(echo extra)" -r'

echo "# blocked: dry-run flags are NOT exempted (expect exit 2)"
expect 2 "migrate:rollback pretend" 'php artisan migrate:rollback --pretend'
expect 2 "doctrine drop dump-sql"   'php bin/console doctrine:schema:drop --dump-sql'

echo "# allowed (expect exit 0)"
expect 0 "plain ls"                 'ls -la'
expect 0 "forward migrate"          'php artisan migrate --force'
expect 0 "DELETE with WHERE"        'mysql -e "DELETE FROM users WHERE id = 5"'
expect 0 "DELETE with LIMIT"        'mysql -e "DELETE FROM jobs LIMIT 100"'
expect 0 "DELETE wrapped WHERE"     $'psql -c "DELETE\nFROM users\nWHERE id = 5"'
expect 0 "DELETE wrapped LIMIT"     $'psql -c "DELETE\nFROM jobs\nLIMIT 100"'
expect 0 "DELETE comment before WHERE" $'psql -c "DELETE FROM users -- cleanup\nWHERE id=5"'
expect 0 "DELETE heredoc with WHERE" $'psql <<SQL\nDELETE\nFROM users\nWHERE id=5;\nSQL'
expect 0 "Symfony append fixtures"  'php bin/console doctrine:fixtures:load --append'
expect 0 "Symfony quoted append flag" 'php bin/console doctrine:fixtures:load "--append"'
expect 0 "Symfony schema preview"   'php bin/console doctrine:schema:update --dump-sql'
expect 0 "Symfony force belongs to sibling" 'php bin/console doctrine:schema:update; echo --force'
expect 0 "Symfony outer force no effect" 'echo "$(php bin/console doctrine:schema:update)" --force'
expect 0 "Symfony force in option value no effect" 'php bin/console doctrine:schema:update --em="--force"'
expect 0 "Mongo filtered deleteMany" 'mongosh --eval "db.users.deleteMany({active: false})"'
expect 0 "Mongo filtered remove"    'mongo --eval "db.users.remove({id: 5})"'
expect 0 "truncate -s coreutil"     'truncate -s 0 /var/log/docker.log'
expect 0 "docker compose up"        'docker compose up -d'
expect 0 "docker system prune no volumes" 'docker system prune -af'
expect 0 "docker -v is not prune volume alias" 'docker system prune -v'
expect 0 "docker prune explicit false" 'docker system prune --volumes=false'
expect 0 "docker volume flag belongs to sibling" 'docker system prune -f; echo --volumes'
expect 0 "docker outer volume flag no effect" 'echo "$(docker system prune -f)" --volumes'
expect 0 "docker volume flag in filter no effect" 'docker system prune --filter="--volumes"'
expect 0 "rm -rf node_modules"      'rm -rf node_modules'
expect 0 "rm nonrecursive DB path"  'rm ./data/mysql'
expect 0 "rm flag belongs to sibling" 'rm ./data/mysql; rm -rf node_modules'
expect 0 "rm newline flag belongs to sibling" $'rm ./data/mysql\nrm -rf node_modules'
expect 0 "rm outer recursive flag no effect" 'echo "$(rm ./data/mysql)" -r'
expect 0 "rm end-of-options literal flag" 'rm -- -rf ./data/mysql'
expect 0 "rm recursive text in file path" 'rm ./data/mysql /tmp/-r'
expect 0 "git log"                  'git log --oneline -5'
expect 0 "npm build"                'npm run build'

echo "# allowed: read-only inspection exemptions (expect exit 0)"
expect 0 "grep for DROP TABLE"      'grep -r "DROP TABLE" database/migrations'
expect 0 "grep -E with alternation" 'grep -E "DROP TABLE|TRUNCATE" database/migrations'
expect 0 "rg for migrate:fresh"     'rg "migrate:fresh" .'
expect 0 "git grep db:wipe"         'git grep "db:wipe"'
expect 0 "git commit mentions DELETE" 'git commit -m "delete unused import from module"'
expect 0 "git commit mentions db:wipe" 'git commit -m "add db:wipe guard for Rails"'
expect 0 "git commit multiline message" $'git commit -m "guard db:wipe\nblock DROP TABLE"'
expect 0 "git log mentions migrate:fresh" 'git log --grep "migrate:fresh"'
expect 0 "echo mentions db:wipe"    'echo "remember to run db:wipe later"'
expect 0 "printf mentions DROP TABLE" 'printf "%s\\n" "DROP TABLE users"'
expect 0 "sed in-place replacement" 'sed -i "" "s/force-reset/safe/" README.md'
expect 0 "sed -e replacement"       'sed -e "s/TRUNCATE TABLE/noop/" README.md'
expect 0 "sed -e joined replacement" 'sed -e'\''s/TRUNCATE TABLE/noop/'\'' README.md'
expect 0 "sed ordinary email replacement" 'sed '\''s/DROP TABLE/email/'\'' README.md'
expect 0 "echo literal substitution" 'echo '\''$(mysql -e "DROP TABLE users")'\'''
expect 0 "echo escaped substitution" 'echo "\$(mysql -e '\''DROP TABLE users'\'')"'

echo "# unavailable parsers are visible but non-blocking (expect exit 1)"
mkdir -p "$TEST_TMPDIR/no-parsers"
ln -s "$(command -v cat)" "$TEST_TMPDIR/no-parsers/cat"
parser_stderr="$(printf '%s' '{"tool_input":{"command":"ls"}}' | PATH="$TEST_TMPDIR/no-parsers" "$BASH" "$HOOK" 2>&1)"
parser_rc=$?
if [[ "$parser_rc" == 1 && "$parser_stderr" == *"hook is INACTIVE"* ]]; then
  pass=$((pass + 1)); echo "ok   - missing parsers report visible non-blocking hook error"
else
  fail=$((fail + 1)); echo "FAIL - missing parsers (expected exit 1 and warning, got $parser_rc: $parser_stderr)"
fi

echo "# each supported parser operates independently"
for parser in python3 jq; do
  if ! command -v "$parser" >/dev/null 2>&1; then
    echo "# $parser unavailable; its optional branch is not tested on this host"
    continue
  fi
  parser_path="$TEST_TMPDIR/parser-$parser"
  mkdir -p "$parser_path"
  for utility in cat tr sed mkdir date "$parser"; do
    ln -s "$(command -v "$utility")" "$parser_path/$utility"
  done
  parser_payload="$(printf '{"tool_input":{"command":%s}}' "$(json_encode $'psql -c "DELETE\nFROM users"')")"
  printf '%s' "$parser_payload" | PATH="$parser_path" "$BASH" "$HOOK" >/dev/null 2>&1
  parser_rc=$?
  if [[ "$parser_rc" == 2 ]]; then
    pass=$((pass + 1)); echo "ok   - $parser alone parses and blocks wrapped DELETE"
  else
    fail=$((fail + 1)); echo "FAIL - $parser alone (expected exit 2, got $parser_rc)"
  fi
done

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
expect 2 "echo then drop chained"   'echo ready && mysql -e "DROP TABLE users"'
expect 2 "git log then reset"       'git log --oneline; rake db:reset'
expect 2 "echo active substitution" 'echo "$(mysql -e '\''DROP TABLE users'\'')"'
expect 2 "echo active backticks"    'echo "`mysql -e '\''DROP TABLE users'\''`"'
expect 2 "echo nested wrapped DELETE" $'echo "$(psql -c "DELETE\nFROM users")"'
expect 2 "sed executes SQL"         'sed -n '\''1e mysql -e "DROP TABLE users"'\'' README.md'
expect 2 "sed substitution execution" 'sed '\''s/.*/mysql -e "DROP TABLE users"/e'\'' README.md'
expect 2 "sed attached execution command" 'sed '\''emysql -e "DROP TABLE users"'\'' README.md'
expect 2 "sed addressed attached execution" 'sed '\''/users/emysql -e "DROP TABLE users"'\'' README.md'
expect 2 "sed repeated execution flags" 'sed '\''s/.*/mysql -e "DROP TABLE users"/ee'\'' README.md'
expect 2 "sed external script"      'sed -f db:wipe.sed README.md'
expect 2 "sed attached external script" 'sed -fdb:wipe.sed README.md'

if [[ -s "$HOME/.claude/logs/destructive-db-blocked.log" ]]; then
  pass=$((pass + 1)); echo "ok   - denial fixtures log only inside isolated HOME"
else
  fail=$((fail + 1)); echo "FAIL - isolated audit log not written"
fi

echo ""
echo "passed: $pass   failed: $fail"
[[ "$fail" == "0" ]]
