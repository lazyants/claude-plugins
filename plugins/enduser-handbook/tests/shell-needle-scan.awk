# Find a backtick the SHELL would expand: unescaped, inside a double-quoted region, outside any
# single-quoted stretch. Run over reference-assets.test.sh by that suite itself.
#
# Why this exists (round 16, found by the repository's cross-file review bot). One assertion passed
# its needle in DOUBLE quotes:
#
#     has_in_section "..." "$SKILL" '### W2 ...' \
#       "`identityCommandOutcome` as the call's **last** argument"
#
# The shell ran `identityCommandOutcome` as a command, substituted its empty output, and handed the
# assertion the remainder — " as the call's **last** argument". The check still passed, while no
# longer verifying the identifier or its Markdown delimiters, which were the whole point. It would
# also have executed any same-named binary on PATH. Nothing about the run looked wrong: the failure
# text went to stderr in whatever language the operator's locale speaks, and the suite's own total
# was unchanged.
#
# Single-quote state is carried ACROSS lines on purpose: the long awk and node programs in that
# suite are each one multi-line '...' argument, and a per-line scanner reads every backtick inside
# them as live. Command substitutions re-open a context where single quotes protect again, so they
# are tracked rather than skipped — `"$(line_of '### ... (`x`)' "$F")"` is safe and must not be
# flagged. Validated both ways against the commit that carried the defect: exactly one hit there,
# none after the fix.
BEGIN { inS = 0 }
{
  if (!inS && $0 ~ /^[[:space:]]*#/) next
  inD = 0; subst = 0; n = length($0)
  for (i = 1; i <= n; i++) {
    c = substr($0, i, 1)
    if (c == "\\" && !inS) { i++; continue }
    if (c == "'" && (!inD || subst > 0)) { inS = !inS; continue }
    if (inS) continue
    if (c == "\"") { inD = !inD; continue }
    if (c == "$" && substr($0, i + 1, 1) == "(") { subst++; i++; continue }
    if (c == ")" && subst > 0) { subst--; continue }
    if (c == "`" && inD && subst == 0) { print FNR ": " $0; next }
  }
}
