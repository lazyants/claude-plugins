#!/usr/bin/env bash
#
# db-guardrails — PreToolUse:Bash hook
# ------------------------------------
# Blocks destructive database commands before Claude Code executes them.
#
# Input  (stdin) : JSON, e.g. {"tool_name":"Bash","tool_input":{"command":"..."}}
# Output (exit)  : 2 + stderr  => DENY  (Claude sees the message and stops)
#                  0           => ALLOW
#                  1 + stderr  => ALLOW with a visible hook error
#
# This is the fast, framework-agnostic UX layer. Its threat model is the
# *accidental* destructive command, not a determined adversary — a command can
# be obfuscated past any regex (SQL block comments, base64, etc.). The hard
# guarantee is database-level privilege separation, which the bundled
# `db-guardrails` skill installs where the engine supports it. This hook catches
# the common accidental wipe commands, with a deliberate, out-of-band opt-in.
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
# input — exit 1 reports a non-blocking hook error in the user's transcript,
# rather than bricking every Bash call in the session.
cmd=""
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
elif command -v python3 >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' \
    2>/dev/null || true)"
else
  echo "db-guardrails: neither 'jq' nor 'python3' found — destructive-DB hook is INACTIVE. Install jq to restore protection." >&2
  exit 1
fi

[[ -z "$cmd" ]] && exit 0

cmd_lower="$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')"

# --- chained / compound command detection --------------------------------
# Walk shell quotes without evaluating anything. Substitution is active outside
# single quotes; escaped quotes/operators and quoted newlines are ordinary data.
# Keep two copies: shell segments for invocation-scoped flags, and SQL segments
# whose quoted newlines remain whitespace and whose closing shell quotes end an
# SQL argument. Semicolons remain conservative SQL boundaries even in literals.
chained=0
quote=""
sub_depth=0
sub_quotes=()
sub_parens=()
sub_kinds=()
sub_shells=()
shell_segments=()
shell_current=""
sql_segments=""
heredoc_delimiter=""
heredoc_line=""
in_heredoc=0
heredoc_re="^<<-?[[:space:]]*['\"]?([a-z_][a-z0-9_]*)['\"]?"
end_shell_segment() {
  shell_segments[${#shell_segments[@]}]="$shell_current"
  shell_current=""
}
for ((i = 0; i < ${#cmd_lower}; i++)); do
  char="${cmd_lower:i:1}"
  next="${cmd_lower:i+1:1}"
  if [[ "$in_heredoc" -eq 1 ]]; then
    if [[ "$char" == $'\n' ]]; then
      if [[ "${heredoc_line//$'\t'/}" == "$heredoc_delimiter" ]]; then
        in_heredoc=0
        heredoc_delimiter=""
        sql_segments+=';'
        end_shell_segment
      else
        sql_segments+="$heredoc_line"$'\n'
        shell_current+="$heredoc_line"$'\n'
      fi
      heredoc_line=""
    else
      heredoc_line+="$char"
    fi
    continue
  fi
  if [[ "$quote" != "'" && "$char" == '\' ]]; then
    # A backslash-newline continues the same command/SQL word.
    if [[ "$next" != $'\n' ]]; then
      shell_current+="$char$next"
      sql_segments+="$next"
    fi
    i=$((i + 1))
    continue
  fi
  if [[ "$quote" != "'" && "$char" == '$' && "$next" == '(' ]]; then
    chained=1
    sub_quotes[sub_depth]="$quote"
    sub_parens[sub_depth]=1
    sub_kinds[sub_depth]='paren'
    sub_shells[sub_depth]="$shell_current"
    sub_depth=$((sub_depth + 1))
    quote=""
    shell_current=""
    sql_segments+='$('
    i=$((i + 1))
    continue
  fi
  if [[ "$quote" != "'" && "$char" == '`' ]]; then
    chained=1
    if [[ "$sub_depth" -gt 0 && "${sub_kinds[sub_depth-1]}" == 'backtick' && -z "$quote" ]]; then
      end_shell_segment
      sub_depth=$((sub_depth - 1))
      quote="${sub_quotes[sub_depth]}"
      shell_current="${sub_shells[sub_depth]}"'`...`'
      sql_segments+=';'
    else
      sub_quotes[sub_depth]="$quote"
      sub_kinds[sub_depth]='backtick'
      sub_shells[sub_depth]="$shell_current"
      sub_depth=$((sub_depth + 1))
      quote=""
      shell_current=""
    fi
    continue
  fi
  if [[ -z "$quote" && "$sub_depth" -gt 0 && "${sub_kinds[sub_depth-1]}" == 'paren' ]]; then
    [[ "$char" == '(' ]] && sub_parens[sub_depth-1]=$((sub_parens[sub_depth-1] + 1))
    if [[ "$char" == ')' ]]; then
      sub_parens[sub_depth-1]=$((sub_parens[sub_depth-1] - 1))
      if [[ "${sub_parens[sub_depth-1]}" -eq 0 ]]; then
        end_shell_segment
        sub_depth=$((sub_depth - 1))
        quote="${sub_quotes[sub_depth]}"
        shell_current="${sub_shells[sub_depth]}"'$(...)'
        sql_segments+=';'
        continue
      fi
    fi
  fi
  if [[ -n "$quote" ]]; then
    shell_current+="$char"
    if [[ "$char" == "$quote" ]]; then
      quote=""
      sql_segments+=$'\n;'
    else
      sql_segments+="$char"
    fi
  elif [[ "$char" == "'" || "$char" == '"' ]]; then
    quote="$char"
    shell_current+="$char"
    sql_segments+="$char"
  elif [[ "$char" == $'\n' ]]; then
    chained=1
    end_shell_segment
    sql_segments+=';'
    [[ -n "$heredoc_delimiter" ]] && in_heredoc=1
  else
    if [[ "$char" == '<' && "$next" == '<' && "${cmd_lower:i}" =~ $heredoc_re ]]; then
      heredoc_delimiter="${BASH_REMATCH[1]}"
    fi
    case "$char" in
      ';'|'&'|'|'|'>'|'<'|'('|')') chained=1 ;;
    esac
    case "$char" in
      ';'|'&'|'|') end_shell_segment; sql_segments+=';' ;;
      *) shell_current+="$char"; sql_segments+="$char" ;;
    esac
  fi
done
if [[ "$in_heredoc" -eq 1 && "$heredoc_line" != "$heredoc_delimiter" ]]; then
  sql_segments+="$heredoc_line"
  shell_current+="$heredoc_line"
fi
end_shell_segment

# Inspection and text commands as a single, un-chained command — a destructive
# keyword there is an argument, not an executed statement. GNU sed's `e`
# commands/substitution flag and external script files do not get an exemption.
if [[ "$chained" -eq 0 ]]; then
  if [[ "$cmd_lower" =~ ^[[:space:]]*(grep|egrep|fgrep|rg|ag|cat|less|more|head|tail|bat|echo|printf)[[:space:]] ]] \
     || [[ "$cmd_lower" =~ ^[[:space:]]*git[[:space:]]+(grep|log|commit)[[:space:]] ]]; then
    exit 0
  fi
  sed_exec_re="(^|[[:space:];{}'\"])[\$0-9,]*e|(^|[[:space:];{}'\"])[\$0-9,]*/[^/]*/e|[^[:alnum:]_[:space:]][egimp0-9]*e[egimp0-9]*([^[:alnum:]_]|$)"
  sed_check="${cmd_lower// -e/ }"
  if [[ "$cmd_lower" =~ ^[[:space:]]*sed[[:space:]] ]] \
     && ! [[ "$sed_check" =~ $sed_exec_re ]] \
     && ! [[ "$cmd_lower" =~ (^|[[:space:]])(-[a-z]*f|--file([[:space:]=]|$)) ]]; then
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
# Checked per SQL statement and shell argument: quoted SQL and heredoc line
# wraps remain whitespace; shell command boundaries and SQL semicolons separate
# statements. Strip SQL line comments after DELETE, preserving newlines, so commented
# WHERE/LIMIT cannot vouch for a DELETE. LIMIT is a safe bound, like WHERE.
# Known limitation: a `;` inside a quoted SQL string literal is also treated
# as a statement boundary — this can over-block (a false positive), never
# under-block.
old_ifs="$IFS"
IFS=';'
for seg_check in $sql_segments; do
  if [[ "$seg_check" =~ delete[[:space:]]+([^|&]*[[:space:]])?from[[:space:]] ]]; then
    # Shell --options before DELETE are not SQL comments. Only inspect the SQL
    # tail for bounds, so WHERE before this DELETE cannot vouch for it either.
    delete_tail="${seg_check#*delete}"
    delete_tail="$(printf '%s' "$delete_tail" | sed 's/--.*$//')"
    if ! [[ "$delete_tail" =~ (^|[^[:alnum:]_])where([^[:alnum:]_]|$) ]] \
       && ! [[ "$delete_tail" =~ (^|[^[:alnum:]_])limit([^[:alnum:]_]|$) ]]; then
      IFS="$old_ifs"
      deny "DELETE without WHERE or LIMIT"
    fi
  fi
done
IFS="$old_ifs"

# Tokenize literal argv without evaluating substitutions or variables. Quoted
# flags remain flags; an option's value such as --group="--append" does not.
parse_shell_words() {
  local text="$1" word="" word_quote="" char next j
  words=()
  for ((j = 0; j < ${#text}; j++)); do
    char="${text:j:1}"
    next="${text:j+1:1}"
    if [[ "$word_quote" != "'" && "$char" == '\' ]]; then
      word+="$next"
      j=$((j + 1))
    elif [[ -n "$word_quote" ]]; then
      if [[ "$char" == "$word_quote" ]]; then
        word_quote=""
      else
        word+="$char"
      fi
    elif [[ "$char" == "'" || "$char" == '"' ]]; then
      word_quote="$char"
    elif [[ "$char" == [[:space:]] ]]; then
      [[ -n "$word" ]] && words[${#words[@]}]="$word"
      word=""
    else
      word+="$char"
    fi
  done
  [[ -n "$word" ]] && words[${#words[@]}]="$word"
}

# Options apply to the same invocation, regardless of their argv position.
# A later/nested command's --append / --force / -r cannot affect this command.
for segment in "${shell_segments[@]}"; do
  parse_shell_words "$segment"
  append=0 force=0 recursive=0 volumes=0
  for word in "${words[@]}"; do
    case "$word" in
      --) break ;;
      --append) append=1 ;;
      --force) force=1 ;;
      --recursive) recursive=1 ;;
      --volumes|--volumes=true|--volumes=1|--volumes=t) volumes=1 ;;
      -*) [[ "$word" =~ ^-[a-z]*r[a-z]*$ ]] && recursive=1 ;;
    esac
  done
  if [[ "$segment" =~ doctrine:fixtures:load([^[:alnum:]_:-]|$) ]] \
     && [[ "$append" -eq 0 ]]; then
    deny "doctrine:fixtures:load without --append (Symfony)"
  fi
  if [[ "$segment" =~ doctrine:schema:update([^[:alnum:]_:-]|$) ]] \
     && [[ "$force" -eq 1 ]]; then
    deny "doctrine:schema:update --force (Symfony)"
  fi
  if [[ "$segment" =~ (^|[^[:alnum:]_])rm[[:space:]] ]] \
     && [[ "$recursive" -eq 1 ]] \
     && [[ "$segment" =~ data/(mysql|mariadb|postgres|postgresql)|/var/lib/(mysql|postgresql)|(mysql|mariadb|postgres|pg)[-_]?data([^a-z]|$) ]]; then
    deny "rm -rf of a database data directory"
  fi
  if [[ "$segment" =~ docker[[:space:]]+system[[:space:]]+prune([[:space:]]|$) ]] \
     && [[ "$volumes" -eq 1 ]]; then
    deny "docker system prune --volumes (deletes anonymous DB volumes)"
  fi
done

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
  '\.[[:space:]]*drop[[:space:]]*\(::collection.drop() (MongoDB)'
  '\.[[:space:]]*(deletemany|remove)[[:space:]]*\([[:space:]]*\{[[:space:]]*\}[[:space:]]*(,|\))::unfiltered deleteMany / remove (MongoDB)'
  'flushall::redis FLUSHALL'
  'flushdb::redis FLUSHDB'
  'docker[ -]compose[[:space:]][^|;&]*down[^|;&]*(--volume|[[:space:]]-v([[:space:]]|$))::docker compose down -v (deletes DB volumes)'
  'docker[[:space:]]+volume[[:space:]]+(rm|prune)::docker volume rm / prune'
)

for rule in "${rules[@]}"; do
  regex="${rule%%::*}"
  label="${rule##*::}"
  if [[ "$cmd_lower" =~ $regex ]]; then
    deny "$label"
  fi
done

exit 0
