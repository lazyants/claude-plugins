"""tests/_js_source_projection.py -- the ONE offset-preserving JS code
projection for this plugin's test suite (#306), shared by
ledger_confirmation_schema.test.py and ledger_update.test.py.

Why a helper module rather than a second copy, and why a THIRD file rather
than one importing the other: every shared JS extractor in this suite scans
BALANCED and comment-aware, but used to LOCATE its starting point with a bare
index over raw source, so a commented-out or prose copy of the same
declaration won --

    // historical shape: const FAILURE_EVIDENCE_KEYS = ["error"];

1.15.0 (#289) fixed that for ledger_confirmation_schema.test.py by projecting
the source first. ledger_update.test.py could not reuse the fix in place,
because ledger_confirmation_schema.test.py already exec_module()s IT to borrow
its two verbatim extractors -- importing back would re-enter that load. Moving
the projection here breaks the cycle without a second implementation that
could disagree with the first while both stay green.

`js_code_only()` blanks every comment, string literal, template literal and
regex literal to spaces, preserving newlines AND every offset, so a match
found in the projection indexes straight back into the real file. That is the
whole contract the callers depend on: locate in the projection, slice the RAW
source at the same offset, and the extracted bytes stay verbatim.

Stdlib-only and import-free by design, matching this suite's house style.
"""


def line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def line_text_at(source: str, offset: int) -> str:
    # Sliced between real "\n" boundaries rather than via str.splitlines(),
    # which also splits on U+2028/U+0085 and would then disagree with
    # line_of()'s "\n"-only count on any template that ever carries one.
    start = source.rfind("\n", 0, offset) + 1
    end = source.find("\n", offset)
    return source[start:end if end != -1 else len(source)].strip()


# Keywords after which a `/` opens a regex literal rather than dividing --
# they all end in a position where an EXPRESSION may begin.
_REGEX_MAY_FOLLOW_KEYWORDS = frozenset({
    "await", "case", "delete", "do", "else", "in", "instanceof", "new", "of",
    "return", "typeof", "void", "yield",
})


def _regex_may_start_at(source: str, offset: int) -> bool:
    """The standard JS `/`-is-a-regex-not-a-division disambiguation: a regex
    literal may begin only where an expression may begin, so anything that
    can close one is read as division, unless it is a keyword after which an
    expression may follow.

    What closes an expression, and why each entry is here: `)`, `]`, `}`; an
    identifier or number; a closing string quote or template backtick (in
    code context an opening one is impossible -- the scanner consumes a
    literal whole, so a quote reached here is always the closing one); and a
    postfix `++`/`--`, distinguished from binary `+`/`-` by the doubled
    character. Every one of those was a demonstrated false-GREEN before it
    was listed: `let z = "x" /KEYS.some(k => k in raw)/ 1;` read as a regex
    literal and blanked a live presence test out of the projection.

    Where the two readings are genuinely ambiguous the tie goes to division,
    which consumes one character, over a regex, which could consume a line."""
    j = offset - 1
    while j >= 0 and source[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = source[j]
    if c in ")]}\"'`":
        return False
    if c in "+-" and j > 0 and source[j - 1] == c:
        return False  # postfix ++/--, not binary +/-
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (source[k].isalnum() or source[k] in "_$"):
            k -= 1
        # A reserved word is a legal PROPERTY NAME, and `obj.new` is an
        # ordinary member expression that ends an expression -- so a `/`
        # after it divides. Verified against real node: with `foo = {new: 8}`,
        # `foo.new / 2 / 2` evaluates to 2. Without this check the keyword
        # list below fired on the property name and read the division as a
        # regex start, blanking a live presence test out of the projection
        # for 9 of the 10 reserved words in it.
        dot = k
        while dot >= 0 and source[dot] in " \t\r\n":
            dot -= 1
        if dot >= 0 and source[dot] == ".":
            return False
        return source[k + 1:j + 1] in _REGEX_MAY_FOLLOW_KEYWORDS
    return True


def _quoted_literal_end(source: str, start: int) -> int:
    """Offset just past the closing quote of the `'`/`"` string at `start`. A
    backslash escapes the next character, including a line continuation's
    newline; a bare newline means the literal never closed."""
    quote = source[start]
    j = start + 1
    n = len(source)
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            break
        j += 1
    raise AssertionError(
        f"unterminated {quote} string literal at line {line_of(source, start)}: "
        f"{source[start:start + 60]!r}"
    )


def _template_chunk_end(source: str, start: int) -> tuple:
    """Scan a run of template-literal TEXT from `start` to whichever comes
    first, and report which: `(offset just past the closing backtick, False)`,
    or `(offset of the `$` opening a `${...}` substitution, True)`. The
    substitution's body is CODE again, so the caller resumes normal scanning
    there and comes back here once the matching `}` closes it -- the template
    really does interpolate (`Unsafe segment id ${JSON.stringify(s)}`), so
    this cannot be simplified into "blank backtick to backtick"."""
    j = start
    n = len(source)
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "$" and j + 1 < n and source[j + 1] == "{":
            return j, True
        if c == "`":
            return j + 1, False
        j += 1
    raise AssertionError(
        f"unterminated template literal at line {line_of(source, start)}: "
        f"{source[start:start + 60]!r}"
    )


def _regex_literal_end(source: str, start: int) -> int:
    """Offset just past the closing `/` (plus flags) of the regex literal at
    `start`. `[...]` character classes may contain an unescaped `/`."""
    j = start + 1
    n = len(source)
    in_class = False
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            break
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and source[j].isalpha():  # trailing flags
                j += 1
            return j
        j += 1
    raise AssertionError(
        f"unterminated regex literal at line {line_of(source, start)}: "
        f"{source[start:start + 60]!r}. A `/` that opens no regex is usually a "
        "DIVISION that _regex_may_start_at() mistook for a regex start -- add "
        "whatever closes the expression to its division list. This fails loudly "
        "rather than blanking the rest of the line, which is the safe direction."
    )


def js_code_only(source: str) -> str:
    """`source` with every comment, string literal, template literal and
    regex literal blanked to spaces -- newlines and every offset preserved,
    so a match found in the result indexes straight back into the real file.

    Deliberately NOT a second copy of review_prompt_schema_drift.test.py's
    literal parser imported at the top of this file: that one is scoped to
    the schema literals' restricted grammar (its token regex accepts only
    `{}[]:,` punctuation and raises on anything else), so it cannot walk a
    whole template. This is the complementary, cruder job -- decide per
    character whether it is code, and blank everything that is not.

    Pinned by ledger_confirmation_schema.test.py's test_js_code_only_shapes,
    the direct test table this had no equivalent of when it first shipped."""
    out = list(source)
    n = len(source)

    def blank(start: int, stop: int) -> None:
        for j in range(start, stop):
            if out[j] != "\n":
                out[j] = " "

    def enter_template_text(start: int) -> int:
        """Blank template-literal text from `start`, returning where code
        resumes -- either past the closing backtick or past the `${` whose
        body is code again (in which case the substitution is pushed)."""
        nonlocal depth
        end, is_substitution = _template_chunk_end(source, start)
        if not is_substitution:
            blank(start, end)
            return end
        blank(start, end + 2)
        substitutions.append(depth)
        depth += 1
        return end + 2

    depth = 0            # brace nesting, counted over CODE only
    substitutions = []   # brace depth at which each open ${...} started
    i = 0
    while i < n:
        c = source[i]
        if c == "/" and source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
        elif c == "/" and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end == -1:
                raise AssertionError(
                    f"unterminated block comment at line {line_of(source, i)}"
                )
            end += 2
        elif c == "/" and _regex_may_start_at(source, i):
            end = _regex_literal_end(source, i)
        elif c in "\"'":
            end = _quoted_literal_end(source, i)
        elif c == "`":
            blank(i, i + 1)
            i = enter_template_text(i + 1)
            continue
        elif c == "{":
            depth += 1
            i += 1
            continue
        elif c == "}":
            depth -= 1
            if substitutions and depth == substitutions[-1]:
                substitutions.pop()
                # Blank the substitution-closing `}` too: its opening `${` was
                # already blanked, so leaving this brace would put an
                # unbalanced close into the projection that every depth-counting
                # consumer (_call_argument_texts, _function_spans) would cross
                # at depth 0 early. The substitution's INNER code is untouched
                # and stays correctly positioned; only this one brace is blanked
                # (ordinary code-block `}` must survive for depth counting).
                blank(i, i + 1)
                i = enter_template_text(i + 1)  # back into the literal's text
            else:
                i += 1
            continue
        else:
            i += 1
            continue
        blank(i, end)
        i = end
    return "".join(out)


def from_declaration(mass_translate_source: str, needle: str) -> str:
    """`mass_translate_source` sliced to begin at `needle`'s first occurrence
    in the CODE PROJECTION.

    An extractor that scans BALANCED and comment-aware but LOCATES its
    starting point with a bare index/search over raw source loses to a
    commented-out or prose copy of the same declaration:

        // historical shape: const FAILURE_EVIDENCE_KEYS = ["error"];

    Handing such an extractor a source that already starts at the real
    declaration fixes WHERE it starts without touching HOW it scans.

    Still needed by review_prompt_schema_drift.test.py's object-literal parser
    and by extract_const_array_literal()'s bracket-span scan, neither of which
    is projection-anchored itself. It is NOT needed by ledger_update.test.py's
    _extract_js_function / _extract_js_const: #306 anchored those two on the
    projection internally, so wrapping them here would only project twice."""
    code = js_code_only(mass_translate_source)
    idx = code.find(needle)
    if idx == -1:
        # Explicit raise, not a bare `assert`, matching every other failure
        # path in this module: under `python -O` a stripped assert would let
        # `find()`'s -1 through and return the source's LAST CHARACTER as if
        # it were the declaration -- a silently wrong extraction, which is
        # the exact class this module exists to make impossible.
        raise AssertionError(
            f"the template's CODE declares no {needle!r} -- it may exist only "
            "inside a comment or a prompt string, which is exactly what this "
            "projection-anchored lookup exists to refuse"
        )
    return mass_translate_source[idx:]
