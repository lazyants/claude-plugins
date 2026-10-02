# Quote-aware, non-evaluating scanner for block-destructive-db.sh.
# Run in LC_ALL=C: shell syntax is ASCII and byte indexing avoids quadratic
# multibyte substring work on large source-generating commands.
# Output uses NUL-delimited typed records: C chain status, D unbounded DELETE,
# H four literal-option bits followed by an invocation. The caller emits K only
# after the awk process exits successfully.

BEGIN {
    input_text = ""
    chained = 0
    quote = ""
    depth = 0
    shell_current = ""
    sql_segments = ""
    heredoc_delimiter = ""
    heredoc_line = ""
    in_heredoc = 0
    extra_unbounded = 0
}

function emit_record(kind, value) {
    printf "%s%s%c", kind, value, 0
}

# Preserve SQL line wraps and shell/semicolon boundaries. Only SQL text after
# DELETE supplies its bound; shell --options before it are not SQL comments.
function unbounded_delete(    count, statements, j, segment, tail, pos) {
    # A one-character awk separator also splits newlines on macOS awk. Force
    # the regexp path so SQL line wraps stay in the same statement.
    count = split(sql_segments, statements, /[;]/)
    for (j = 1; j <= count; j++) {
        segment = statements[j]
        if (segment ~ /delete[[:space:]]+([^|&]*[[:space:]])?from[[:space:]]/) {
            pos = index(segment, "delete")
            tail = substr(segment, pos + 6)
            gsub(/--[^\n]*/, "", tail)
            if (tail !~ /(^|[^[:alnum:]_])where([^[:alnum:]_]|$)/ &&
                tail !~ /(^|[^[:alnum:]_])limit([^[:alnum:]_]|$)/) return 1
        }
    }
    return 0
}

function flag_word(word) {
    if (stop_flags || word == "") return
    if (word == "--") stop_flags = 1
    else if (word == "--append") append_flag = 1
    else if (word == "--force") force_flag = 1
    else if (word == "--recursive" || word ~ /^-[a-z]*r[a-z]*$/) recursive_flag = 1
    else if (word == "--volumes" || word == "--volumes=true" || word == "--volumes=1" || word == "--volumes=t") volumes_flag = 1
}

# Read one ordinary heredoc delimiter, including quote removal/backslash quoting.
function read_delimiter(text, at,    j, size, char, following, delimiter_quote) {
    pending_delimiter = ""
    pending_quoted = pending_heredoc = 0
    delimiter_quote = ""
    size = length(text)
    j = at + 2
    if (substr(text, j, 1) == "-") j++
    while (substr(text, j, 1) ~ /[ \t]/) j++
    for (; j <= size; j++) {
        char = substr(text, j, 1)
        following = substr(text, j + 1, 1)
        if (delimiter_quote != "") {
            if (char == delimiter_quote) delimiter_quote = ""
            else pending_delimiter = pending_delimiter char
        } else if (char == "\\") {
            pending_quoted = pending_heredoc = 1
            pending_delimiter = pending_delimiter following
            j++
        } else if (char == "'" || char == "\"") {
            pending_quoted = pending_heredoc = 1
            delimiter_quote = char
        } else if (char ~ /[[:space:];&|()< >]/) {
            break
        } else {
            pending_heredoc = 1
            pending_delimiter = pending_delimiter char
        }
    }
}

function finish_substitution(marker) {
    emit_shell_segment()
    depth--
    quote = sub_quotes[depth]
    shell_current = sub_shells[depth] marker
    if (sub_from_heredoc[depth]) {
        if (unbounded_delete()) extra_unbounded = 1
        sql_segments = sub_sqlbase[depth]
    } else {
        sql_segments = sql_segments ";"
    }
}

# Strip shell quoting in literal argv, without evaluating variables/substitutions.
# --group="--append" is one value-bearing word, not an --append option.
function emit_shell_segment(    text, size, j, char, following, word, word_quote, word_start) {
    text = shell_current
    size = length(text)
    word = word_quote = ""
    word_start = 1
    append_flag = force_flag = recursive_flag = volumes_flag = stop_flags = 0
    for (j = 1; j <= size; j++) {
        char = substr(text, j, 1)
        following = substr(text, j + 1, 1)
        if (word_quote != "'" && char == "\\") {
            word = word substr(text, word_start, j - word_start) following
            j++
            word_start = j + 1
        } else if (word_quote != "") {
            if (char == word_quote) {
                word = word substr(text, word_start, j - word_start)
                word_quote = ""
                word_start = j + 1
            }
        } else if (char == "'" || char == "\"") {
            word = word substr(text, word_start, j - word_start)
            word_quote = char
            word_start = j + 1
        } else if (char ~ /[[:space:]]/) {
            flag_word(word substr(text, word_start, j - word_start))
            word = ""
            word_start = j + 1
        }
    }
    flag_word(word substr(text, word_start))
    emit_record("H", sprintf("%d%d%d%d", append_flag, force_flag, recursive_flag, volumes_flag) text)
    shell_current = ""
}

# Default newline records preserve empty lines. A NUL RS is paragraph mode in
# macOS awk, so it must not be used to gather an entire command. The caller's
# command substitution has already stripped trailing newlines.
{ input_text = input_text (NR > 1 ? "\n" : "") $0 }

function scan_text(    text, size, i, char, following, delimiter_line, suffix, pos, chunk) {
    text = input_text
    size = length(text)
    for (i = 1; i <= size; i++) {
        char = substr(text, i, 1)
        following = substr(text, i + 1, 1)
        if (in_heredoc) {
            if (depth == heredoc_depth) {
                suffix = substr(text, i)
                if (heredoc_quoted) pos = index(suffix, "\n")
                else pos = match(suffix, /[\n\\`]|\$\(/)
                if (pos != 1) {
                    chunk = (pos ? substr(suffix, 1, pos - 1) : suffix)
                    heredoc_line = heredoc_line chunk
                    i += length(chunk) - 1
                    continue
                }
            }
            if (char == "\n") {
                delimiter_line = heredoc_line
                gsub(/\t/, "", delimiter_line)
                if (delimiter_line == heredoc_delimiter) {
                    in_heredoc = 0
                    heredoc_delimiter = ""
                    sql_segments = sql_segments heredoc_sql ";"
                    emit_shell_segment()
                    heredoc_line = ""
                    continue
                } else {
                    heredoc_sql = heredoc_sql heredoc_line "\n"
                }
                heredoc_line = ""
            } else {
                heredoc_line = heredoc_line char
            }
            if (depth == heredoc_depth) {
                # Heredoc body quotes are SQL/data quotes, not shell quotes.
                # Only delimiter quoting or a backslash suppresses expansion.
                if (heredoc_quoted) continue
                if (char == "\\" && following ~ /[$`\\]/) {
                    heredoc_line = heredoc_line following
                    i++
                    continue
                }
                if (!(char == "$" && following == "(") && char != "`") continue
                sub_from_heredoc[depth] = 1
                sub_sqlbase[depth] = sql_segments
                sql_segments = ""
                quote = ""
            }
        }
        # Append ordinary quoted spans once, avoiding a growing string copy per
        # byte. Keep heredoc substitutions on the normal path for raw-line data.
        if (!in_heredoc && quote != "") {
            suffix = substr(text, i)
            if (quote == "'") {
                if (ansi_quote) pos = match(suffix, /['\\]/)
                else pos = index(suffix, "'")
            } else pos = match(suffix, /["\\`]|\$\(/)
            if (pos != 1) {
                chunk = (pos ? substr(suffix, 1, pos - 1) : suffix)
                shell_current = shell_current chunk
                sql_segments = sql_segments chunk
                i += length(chunk) - 1
                continue
            }
        }
        if (quote == "'" && ansi_quote && char == "\\") {
            shell_current = shell_current char following
            if (following == "n") sql_segments = sql_segments "\n"
            else if (following == "t") sql_segments = sql_segments "\t"
            else if (following == "r") sql_segments = sql_segments "\r"
            else if (following == "'" || following == "\\") sql_segments = sql_segments following
            else sql_segments = sql_segments char following
            i++
            continue
        }
        if (quote != "'" && char == "\\") {
            if (following != "\n") {
                shell_current = shell_current char following
                sql_segments = sql_segments following
            }
            i++
            continue
        }
        if (quote != "'" && char == "$" && following == "(") {
            chained = 1
            sub_quotes[depth] = quote
            sub_parens[depth] = 1
            sub_kinds[depth] = "paren"
            sub_shells[depth] = shell_current
            if (!(in_heredoc && depth == heredoc_depth)) sub_from_heredoc[depth] = 0
            depth++
            quote = shell_current = ""
            sql_segments = sql_segments "$("
            i++
            if (in_heredoc) heredoc_line = heredoc_line "("
            continue
        }
        if (quote != "'" && char == "`") {
            chained = 1
            if (depth > 0 && sub_kinds[depth - 1] == "backtick" && quote == "") {
                finish_substitution("`...`")
            } else {
                sub_quotes[depth] = quote
                sub_kinds[depth] = "backtick"
                sub_shells[depth] = shell_current
                if (!(in_heredoc && depth == heredoc_depth)) sub_from_heredoc[depth] = 0
                depth++
                quote = shell_current = ""
            }
            continue
        }
        if (quote == "" && depth > 0 && sub_kinds[depth - 1] == "paren") {
            if (char == "(") sub_parens[depth - 1]++
            if (char == ")") {
                sub_parens[depth - 1]--
                if (sub_parens[depth - 1] == 0) {
                    finish_substitution("$(...)")
                    continue
                }
            }
        }
        if (quote != "") {
            shell_current = shell_current char
            if (char == quote) {
                quote = ""
                ansi_quote = 0
                sql_segments = sql_segments "\n;"
            } else {
                sql_segments = sql_segments char
            }
        } else if (char == "'" || char == "\"") {
            quote = char
            ansi_quote = (char == "'" && substr(text, i - 1, 1) == "$")
            shell_current = shell_current char
            sql_segments = sql_segments char
        } else if (char == "\n") {
            chained = 1
            emit_shell_segment()
            sql_segments = sql_segments ";"
            if (pending_heredoc) {
                in_heredoc = 1
                heredoc_depth = depth
                heredoc_delimiter = pending_delimiter
                heredoc_quoted = pending_quoted
                heredoc_sql = ""
                pending_heredoc = 0
            }
        } else {
            if (char == "<" && following == "<") {
                read_delimiter(text, i)
            }
            if (char ~ /[;&|><()]/) chained = 1
            if (char ~ /[;&|]/) {
                emit_shell_segment()
                sql_segments = sql_segments ";"
            } else {
                shell_current = shell_current char
                sql_segments = sql_segments char
            }
        }
    }
    if (in_heredoc && heredoc_line != heredoc_delimiter) {
        sql_segments = sql_segments heredoc_sql heredoc_line
    } else if (in_heredoc) {
        sql_segments = sql_segments heredoc_sql
    }
}

END {
    scan_text()
    emit_shell_segment()
    emit_record("C", chained)
    emit_record("D", extra_unbounded || unbounded_delete())
}
