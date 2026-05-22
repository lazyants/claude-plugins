#!/usr/bin/env bash
#
# db-guardrails — PreToolUse:Bash hook
# ------------------------------------
# Blocks destructive database commands before Claude Code executes them.
#
# Input  (stdin) : JSON, e.g. {"tool_name":"Bash","tool_input":{"command":"..."}}
# Output (exit)  : 2 + stderr  => DENY  (Claude sees the message and stops)
#                  0           => ALLOW
#
# This is the fast, framework-agnostic UX layer. Its threat model is the
# *accidental* destructive command, not a determined adversary — a command can
# be obfuscated past any regex (SQL block comments, base64, etc.). The hard
# guarantee is database-level privilege separation, which the bundled
# `db-guardrails` skill installs. This hook just makes the accidental wipe
# impossible without a deliberate, out-of-band opt-in.
#
# Deliberate bypass: start Claude Code with
#     ALLOW_DESTRUCTIVE_DB_HOOK=true
# in the shell environment. There is intentionally no inline sentinel, comment
# or flag that re-enables a single command — an LLM could append it to any
# command to bypass its own guard. The opt-in must come from the human.
#
# Written for bash 3.2+ (macOS system bash) — no associative arrays, no ${x,,}.
#
set -uo pipefail

# --- deliberate, human-set, out-of-band bypass ----------------------------
if [[ "${ALLOW_DESTRUCTIVE_DB_HOOK:-}" == "true" ]]; then
  exit 0
fi

payload="$(cat)"
[[ -z "$payload" ]] && exit 0

# --- extract the command string from the tool payload --------------------
# Prefer jq; fall back to python3. If neither exists the hook cannot parse its
# input — it warns loudly (Claude sees the warning) and allows, rather than
# bricking every Bash call in the session.
cmd=""
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
elif command -v python3 >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' \
    2>/dev/null || true)"
else
  echo "db-guardrails: neither 'jq' nor 'python3' found — destructive-DB hook is INACTIVE. Install jq to restore protection." >&2
  exit 0
fi

[[ -z "$cmd" ]] && exit 0

cmd_lower="$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')"

# --- chained / compound command detection --------------------------------
# Command substitution executes even inside double quotes, so $(...) and
# backticks are dangerous wherever they appear — checked on the raw command.
# Operators (; && || | >) are only operators OUTSIDE quotes, so they are
# checked against a copy with quoted spans removed — this stops a pipe inside
# e.g. `grep -E "a|b"` from being mistaken for a real pipeline.
chained=0
case "$cmd_lower" in
  *'$('* | *'`'*) chained=1 ;;
esac
[[ "$cmd_lower" == *$'\n'* ]] && chained=1

if [[ "$chained" -eq 0 ]]; then
  cmd_unquoted="$(printf '%s' "$cmd_lower" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")"
  case "$cmd_unquoted" in
    *';'* | *'&&'* | *'||'* | *'|'* | *'>'*) chained=1 ;;
  esac
fi

# Read-only inspection tools as a single, un-chained command — a destructive
# keyword there is an argument, not an executed statement.
if [[ "$chained" -eq 0 ]]; then
  if [[ "$cmd_lower" =~ ^[[:space:]]*(grep|egrep|fgrep|rg|ag|cat|less|more|head|tail|bat)[[:space:]] ]] \
     || [[ "$cmd_lower" =~ ^[[:space:]]*git[[:space:]]+grep[[:space:]] ]]; then
    exit 0
  fi
fi

# --- denial ----------------------------------------------------------------
deny() {
  label="$1"
  logdir="${HOME:-/tmp}/.claude/logs"
  mkdir -p "$logdir" 2>/dev/null || true
  printf '%s BLOCKED [%s] :: %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$label" "$cmd" \
    >> "$logdir/destructive-db-blocked.log" 2>/dev/null || true
  {
    echo "BLOCKED by db-guardrails — destructive database command detected."
    echo "  matched rule: ${label}"
    echo ""
    echo "Claude must not run this and cannot bypass the guard itself."
    echo "If the command is genuinely intended, the human operator should either:"
    echo "  - run it in their own terminal, outside Claude Code; or"
    echo "  - restart Claude Code with ALLOW_DESTRUCTIVE_DB_HOOK=true set in the shell."
  } >&2
  exit 2
}

# --- DELETE FROM with no WHERE / LIMIT (heuristic) ------------------------
# Checked per statement: the command is split on `;` and newline so a safe
# WHERE in one statement cannot vouch for an unbounded DELETE in another. A
# ` -- ` SQL line comment is stripped first so a commented-out `where` cannot
# vouch either. `LIMIT` is treated as a safe bound, like `WHERE`.
# Known limitation: a `;` inside a quoted SQL string literal is also treated
# as a statement boundary — this can over-block (a false positive), never
# under-block.
old_ifs="$IFS"
IFS=$';\n'
for segment in $cmd_lower; do
  seg_check="${segment%% -- *}"
  if [[ "$seg_check" =~ delete[[:space:]]+([^|&]*[[:space:]])?from[[:space:]] ]] \
     && ! [[ "$seg_check" =~ where ]] \
     && ! [[ "$seg_check" =~ limit ]]; then
    IFS="$old_ifs"
    deny "DELETE without WHERE or LIMIT"
  fi
done
IFS="$old_ifs"

# --- pattern rules: 'EXTENDED_REGEX::human label' -------------------------
# Matched against the lower-cased command. Regexes must not contain '::'.
rules=(
  'drop[[:space:]]+(database|schema)::DROP DATABASE / DROP SCHEMA'
  'drop[[:space:]]+(temporary[[:space:]]+|foreign[[:space:]]+)?table::DROP TABLE'
  'truncate[[:space:]]+(table[[:space:]]+)?[^-[:space:]]::TRUNCATE TABLE'
  '(^|[^[:alnum:]_-])dropdb([[:space:]]|$)::dropdb (PostgreSQL)'
  '(mysqladmin|mariadb-admin)[[:space:]]([^|;&]*[[:space:]])?drop([[:space:]]|$)::mysqladmin / mariadb-admin drop'
  'migrate:fresh::artisan migrate:fresh (Laravel)'
  'migrate:refresh::artisan migrate:refresh (Laravel)'
  'migrate:reset::artisan migrate:reset (Laravel)'
  'migrate:rollback::migrate:rollback (Laravel / Knex)'
  'db:wipe::artisan db:wipe (Laravel)'
  'db:drop::db:drop (Rails / Sequelize)'
  'db:reset::db:reset (Rails)'
  'db:purge::db:purge (Rails)'
  'db:truncate_all::db:truncate_all (Rails)'
  'db:schema:load::db:schema:load (Rails)'
  'db:structure:load::db:structure:load (Rails)'
  'db:test:prepare::db:test:prepare (Rails)'
  'schema:drop::schema:drop (TypeORM)'
  'migrate[[:space:]]+reset::prisma migrate reset'
  'force-reset::prisma db push --force-reset'
  'accept-data-loss::prisma db push --accept-data-loss'
  'drizzle-kit[[:space:]]+drop::drizzle-kit drop'
  'doctrine:(database|schema):drop::doctrine database/schema drop (Symfony)'
  'ef[[:space:]]+database[[:space:]]+drop::dotnet ef database drop'
  '(manage\.py|django-admin|-m[[:space:]]+django)[[:space:]][^|;&]*(flush|sqlflush|reset_db)([[:space:]]|$)::Django flush / sqlflush / reset_db'
  '(manage\.py|django-admin|-m[[:space:]]+django)[[:space:]][^|;&]*migrate[[:space:]][^|;&]*[[:space:]]zero([[:space:]]|$)::Django migrate <app> zero'
  'alembic[[:space:]]+downgrade[[:space:]]+base::alembic downgrade base'
  'flyway[^|;&]*clean::flyway clean (also mvn flyway:clean / gradle flywayClean)'
  'liquibase[^|;&]*dropall::liquibase dropAll'
  'dropdatabase::dropDatabase() (MongoDB)'
  'flushall::redis FLUSHALL'
  'flushdb::redis FLUSHDB'
  'docker[ -]compose[[:space:]][^|;&]*down[^|;&]*(--volume|[[:space:]]-v([[:space:]]|$))::docker compose down -v (deletes DB volumes)'
  'docker[[:space:]]+volume[[:space:]]+(rm|prune)::docker volume rm / prune'
  '(^|[^[:alnum:]_])rm[[:space:]]+[^|;&]*(-[a-z]*r[a-z]*|--recursive)[[:space:]][^|;&]*(data/(mysql|mariadb|postgres|postgresql)|/var/lib/(mysql|postgresql)|(mysql|mariadb|postgres|pg)[-_]?data([^a-z]|$))::rm -rf of a database data directory'
)

for rule in "${rules[@]}"; do
  regex="${rule%%::*}"
  label="${rule##*::}"
  if [[ "$cmd_lower" =~ $regex ]]; then
    deny "$label"
  fi
done

exit 0
