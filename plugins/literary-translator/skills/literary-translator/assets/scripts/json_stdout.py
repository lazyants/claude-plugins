#!/usr/bin/env python3
"""json_stdout.py -- the single shared implementation of "serialise a JSON
payload with no unescaped line boundary inside its string data".

For the default (unindented) callers that is literally ONE physical stdout
line. Two callers pass `indent=`, and their output is multiline BY DESIGN --
what this module guarantees for them is narrower and still the point: no line
boundary the caller did not ask for, i.e. none coming out of the DATA.

Every CLI under this directory answers on stdout with a JSON payload, and
several of those stdouts are read by an LLM agent with a LINE-ORIENTED
grammar rather than by a JSON parser alone: the mass-translate and
glossary-pass workflow templates tell the dispatched agent to run a check
command "and read its one line of JSON output", then answer with a
line-shaped sentinel. `json.dumps(..., ensure_ascii=False)` does not
guarantee that one line. It backslash-escapes every control character below
0x20 unconditionally, but JSON permits a LITERAL U+0085 NEL, U+2028 LINE
SEPARATOR and U+2029 PARAGRAPH SEPARATOR inside a string, and leaves all
three raw. Measured: such a payload is ONE line to `str.split("\\n")` and
TWO (or more) to `str.splitlines()`. A `source_form`, a rationale, a block
id or an exception message carrying one therefore renders, to the agent
reading that stdout, as a second physical line -- of exactly the sentinel
shape the reply parsers guard against.

This module exists because 1.16.2 fixed that inside `skeptic_ready.py` only
and left the class open at every other stdout site (#369). `dumps_line()`
below is the one implementation; `skeptic_ready.py`'s `_json_dumps_line()`
now delegates to it rather than keeping a second copy of the derivation.

WHAT THIS IS NOT. It is not a canonicaliser and must never be used as one.
The hashing/canonical-form call sites in this plugin (`json.dumps(...,
sort_keys=True, separators=(",", ":"))` feeding a sha1/sha256, and every
serializer that writes a durable file) are deliberately NOT routed through
it: escaping there would change the bytes that get hashed and mass-
invalidate converged work. The escape is for the WIRE only -- a `\\uXXXX`
escape round-trips through every JSON parser identically to the raw
character, so only the emitted bytes differ, never the decoded value.

HOW IT IS LOADED. By EXACT PATH, never by bare name -- see the loader block
each importer carries. A bare `import json_stdout` resolves through the
global `sys.modules` cache "regardless of which fixture's copy the CALLER
intended" (tests/validate_assembled.test.py says exactly that about this
suite's other bare sibling imports), so a test that stages several durable
roots in one process would bind the FIRST root's copy for all of them.
Loading by path gives every importer the file beside itself, or a loud
failure, and needs no cache eviction to do it.
"""

import json

# CPython's str.splitlines() boundary set -- the SAME 10-member candidate
# list render_obsidian.py's _MENTIONS_LINE_BREAK_CHARS and skeptic_report.py's
# _LINE_BREAK_CHARS restate, stable across Python versions (nothing outside
# this list is ever a splitlines() boundary, so nothing outside it can ever
# need the escape below). Built entirely from chr()/\xXX escapes for
# codepoints <= 0xFF plus chr() calls for U+2028/U+2029 -- never a \uXXXX
# string-literal escape or a pasted glyph, both of which have silently
# degraded before (see the unicode-boundary-text-authoring project skill).
_SPLITLINES_BOUNDARY_CANDIDATES = "\n\r\v\f\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)

_BACKSLASH = chr(92)  # a literal backslash inside a string literal risks being
# parsed back into the very character this module must instead emit as
# PLAIN escaped text -- chr(92) sidesteps that ambiguity entirely.


def _compute_line_separator_escapes() -> dict:
    """DERIVES (does not hand-list) which of ``_SPLITLINES_BOUNDARY_
    CANDIDATES`` ``json.dumps(..., ensure_ascii=False)`` actually leaves
    RAW: every codepoint < 0x20 (``\\n \\r \\v \\f \\x1c \\x1d \\x1e``) is
    ALREADY backslash-escaped by ``json.dumps`` itself, unconditionally,
    regardless of ``ensure_ascii`` -- only the candidates >= 0x20 (U+0085
    NEL, U+2028, U+2029) survive it raw and need an escape from THIS
    function.

    A hand-typed two-member dict here (U+2028/U+2029 only) is exactly how
    1.16.2's round-5 F1 happened: it silently missed NEL, which
    ``str.splitlines()`` also treats as a boundary. This computation is the
    cheap equivalent of a full 0x0-0x10FFFF brute-force scan (which
    tests/skeptic_ready.test.py actually runs, to PROVE the reduced
    candidate set above is complete) -- filtering 10 known candidates by the
    real predicate, rather than trusting either a fixed count or a fixed
    list of which ones need it. If a future Python version ever changed
    which codepoints ``json.dumps`` escapes, this recomputes correctly; a
    hand-listed dict would not."""
    escapes = {}
    for ch in _SPLITLINES_BOUNDARY_CANDIDATES:
        if ch in json.dumps(ch, ensure_ascii=False):
            escapes[ch] = _BACKSLASH + "u" + format(ord(ch), "04x")
    return escapes


LINE_SEPARATOR_ESCAPES = _compute_line_separator_escapes()


def dumps_line(obj, **kwargs) -> str:
    """``json.dumps(obj, ensure_ascii=False, **kwargs)``, plus an escape for
    every ``str.splitlines()`` boundary character ``json.dumps`` leaves raw
    (see ``_compute_line_separator_escapes``' own docstring for the
    derivation and why a hand-typed set was found incomplete -- currently
    U+0085, U+2028 and U+2029, per ``LINE_SEPARATOR_ESCAPES``, never
    re-hand-listed here).

    ``**kwargs`` is load-bearing rather than generality: four call sites in
    this directory pass non-default options and would change behaviour
    without it -- ``bootstrap_names.py`` (``indent=1``), ``cache_key.py``
    (``indent=2, sort_keys=False``), ``glossary_preflight.py`` (compact
    ``separators``) and ``validate_conservation.py`` (``allow_nan=False``).

    ``ensure_ascii`` is owned by this function and passing it is a
    ``TypeError``: ``ensure_ascii=True`` would escape the Hebrew, Yiddish
    and Russian payloads a reading agent has to understand, and
    ``ensure_ascii=False`` passed explicitly would merely restate what this
    function already guarantees while implying a caller may choose."""
    if "ensure_ascii" in kwargs:
        raise TypeError(
            "dumps_line() owns ensure_ascii and does not accept it: this "
            "function always serialises with ensure_ascii=False and then "
            "escapes the boundary characters that survive it. Passing "
            "ensure_ascii=True would escape the non-Latin payload a reading "
            "agent needs; passing False restates what is already guaranteed."
        )
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    for raw, escaped in LINE_SEPARATOR_ESCAPES.items():
        text = text.replace(raw, escaped)
    return text
