"""Every measured figure DECLARED for the newest CHANGELOG entry is re-derived
from the tree, so a later edit in the same release cannot leave it stale.

WHY THIS EXISTS. A release note here states measured costs -- how many members a
bundle tuple has, how many files moved, how many entries this changelog holds.
Each is correct when written. An edit made later *in the same release* then moves
the thing it measured, and nothing recomputes it: the entry ships a figure that
was true at the moment of writing and false at the moment of merge, with no red
anywhere. 1.29.0 hit this five times in one release, and every instance was
caught by a reviewer reading the prose rather than by any check.

WHY IT IS THIS SMALL, stated because the obvious bigger version was deliberately
cut. The first design also swept every digit in the entry and demanded that each
one be declared or exempted, backed by a per-release model call to catch the
spelled-out figures a regex cannot see. The measurement that killed it: of the
six figures in shipped entries that can be re-derived today -- PLUGIN_BUNDLE_MEMBERS
(17), ORCHESTRATION_BUNDLE_MEMBERS (5), PRODUCER_CODE_CLOSURE (5),
CACHE_KEY_FIELD_ORDER (15), select_segments.CACHE_KEY_FIELDS (15), and 1.34.0's
"88 new tests" -- ALL SIX ARE CORRECT. Nothing wrong has reached a reader through
this surface. A sweep would have added a six-to-thirty-four-row declaration set
plus a model call to every release, permanently, to guard a defect with a measured
ship rate of zero. So what is here is the half that closes the failure the issue
actually names, and nothing else.

WHAT THIS DOES NOT CATCH, stated plainly because a check that oversells itself is
the same defect class it exists to remove.

- An UNDECLARED figure is not checked at all, and the author who mis-measures is
  the one least likely to declare it. There is no completeness half. This is the
  accepted residual, not an oversight.
- A derivation that hardcodes its own answer (`lambda: 17`) passes every assertion
  below. Nothing mechanical can see that. The guard is the maintenance rule at the
  bottom of this docstring: every row is watched failing, by mutating the TREE, not
  by mutating the row.
- Historical entries are not covered, by the same contract the citation test keeps
  (`tests/changelog_citations.test.py`) and for the same reason: an entry records
  what a past release measured.
- The entry slicer is fence-unaware. A `## <semver>` line inside a fenced block
  would end the slice early. Measured across every release heading in this file:
  ZERO sit inside a fence. When it does happen the result is usually RED (a declared
  phrase falls below the fake heading and goes missing) -- but not always: if every
  declaration sits ABOVE the fake heading, or a duplicate of a phrase sits below
  it, the truncated slice can still satisfy the exactly-once rule and pass GREEN.
  Accepted rather than guarded: the input is this repo's own changelog, written by
  the maintainer, and the frequency is zero.

HOW A ROW IS WRITTEN.

`phrase` is the SMALLEST UNIQUE SLICE of the entry that contains the figure --
never the whole sentence. Two reasons, and both bite in practice. It must contain
exactly ONE numeric token, because a phrase spanning several numbers would satisfy
every check here while only one of its numbers was ever verified. And it must
occur exactly once, because bare numerals repeat freely inside a single entry --
which is why the key is a phrase and never the numeral itself.

`value` is the number written out where a reviewer reads it, in the diff, next
to the phrase and the derivation. Be clear about what its check is worth: with
the prose and the tree both compared directly, a wrong `value` cannot by itself
ship a wrong figure -- if the prose is right the entry is right. It is a
readability device with a consistency guard attached, not a third independent
check, and calling it one would be this file's own defect class.

`derive` MUST CALL THE AUTHORITATIVE IMPLEMENTATION, never a lookalike. Measured
while this test was being written: counting `def test_` by AST gives 78 for the two
`person_registry` modules where pytest collects 88, because parametrized cases
count as they run. A re-implementation is not a verification -- it is a second
chance to make the same mistake, and it fails in the direction that looks right.

`_newest_entry` here is a near-copy of the one in `changelog_citations.test.py`,
and deliberately so: both modules are named `*.test.py`, so neither can import
the other by module name without `importlib` machinery worth more than the dozen
shared lines. One asymmetry to know about, since nothing flags it -- the
citations copy blanks fenced blocks BEFORE slicing and this one does not (see
above) -- so a future correction to either body does not reach the other.

MAINTENANCE CONTRACT. This tracks the NEWEST entry only, since that is the one
under active edit, and it is rewritten every release -- exactly like
`CITATION_ANCHORS`. When a new version entry lands, the previous entry's rows go
with it. Emptying `FIGURES` retires this check silently; that is a review
responsibility, not something asserted here.
"""

import ast
import re
from collections import namedtuple
from decimal import Decimal
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"
SCRIPTS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

# `## 1.34.1 - 2026-08-22`: the version, then anything (this repo appends a
# release date). Requiring end-of-line after the version would match no real
# heading in the file.
_VERSION_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b.*$", re.M)

# A numeric token as this changelog writes one. Digit grouping here is an ASCII
# space (`20 137`), verified by byte scan -- not NBSP, not a thin space -- so a
# grouped number must be ONE token rather than two. The decimal tail is not
# decoration: the newest entry at the time of writing carried `3.4x`, and a
# grammar that cannot express a real measured figure pushes the maintainer toward
# not declaring it, which is the only failure mode this file has no answer for.
_TOKEN = re.compile(r"\d+(?: \d{3})*(?:\.\d+)?")

# phrase -> the value the prose asserts -> how to re-derive it from the tree.
Figure = namedtuple("Figure", "phrase value derive")


def _release_entry_count():
    """Release entries in this changelog -- the count its own headings make."""
    return len(_VERSION_HEADING.findall(CHANGELOG.read_text(encoding="utf-8")))


def _test_module_count():
    """Test modules under `tests/`, by this project's own `*.test.py` pattern --
    not a hand-kept list. RECURSIVE, because `python_files` in `pytest.ini` is a
    BASENAME pattern: pytest would collect `tests/unit/x.test.py` while a
    top-level glob silently would not, and a derivation that quietly disagrees
    with the authority it cites is the failure this file warns about."""
    return len(list((PLUGIN_ROOT / "tests").rglob("*.test.py")))


def _tuple_len(filename, name):
    """Length of a module-level tuple in a SHIPPED script, read by AST rather
    than imported. Importing would execute the module and bind this test to
    whatever else it does at import time; a literal read cannot."""
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            # The node must BE a tuple, not merely something `len()` accepts:
            # `NAME = "abcdefghijklmnopq"` also has length 17, and this row
            # would then report that it had counted tuple members.
            assert isinstance(node.value, ast.Tuple), (
                f"{name} in {filename} is no longer a tuple literal, so its "
                f"length is not the member count a changelog figure cites"
            )
            return len(ast.literal_eval(node.value))
    raise AssertionError(
        f"{name} is no longer a module-level assignment in {filename} -- the "
        f"derivation behind a changelog figure has lost its subject"
    )


# Rewritten for 1.34.3 (#582), per the maintenance contract above: the previous
# entry's rows go with the previous entry. 1.34.2's three rows ("62 release
# entries", "162 test modules", "17 `PLUGIN_BUNDLE_MEMBERS`") were retired here.
#
# EMPTY ON PURPOSE, DISCLOSED RATHER THAN SILENT -- which is the one thing the
# contract above asks of a release that empties it. The 1.34.3 entry is prose
# only: it corrects the `{{PLUGIN_ROOT}}` definition and records the #582
# decision, and states no quantity derived from the tree. There is nothing here
# to re-derive, and inventing a figure so this list stays non-empty would be
# writing prose for a test to read rather than for a reader.
#
# Worth knowing for the next release, since 1.34.2 shipped one day before this
# one and could not have seen it: these rows track the NEWEST entry, so the act
# of adding any newer entry turns every row red at once. That is this list being
# rewritten on schedule, not a regression in the tree -- but it does mean the
# check goes red in the middle of an unrelated release, and the fix is always to
# rewrite the rows for the new entry, never to edit the old entry's prose.
FIGURES = []


def _newest_entry():
    """(version, text) of the first `## <semver>` section -- the release being
    edited. The heading must be a version: matching any `## <token>` would let a
    prose heading masquerade as the newest entry."""
    text = CHANGELOG.read_text(encoding="utf-8")
    heads = list(_VERSION_HEADING.finditer(text))
    assert heads, "CHANGELOG has no `## <major.minor.patch>` heading"
    first = heads[0]
    end = heads[1].start() if len(heads) > 1 else len(text)
    return first.group(1), text[first.start() : end]


def test_every_declared_figure_is_still_what_the_tree_says():
    version, entry = _newest_entry()
    # Where every whole numeral of the entry begins and ends, by the same
    # tokenizer a row's phrase is read with.
    entry_numerals = {(m.start(), m.end()) for m in _TOKEN.finditer(entry)}

    stale = []
    for figure in FIGURES:
        found = entry.count(figure.phrase)
        if found != 1:
            stale.append(
                f"{figure.phrase!r} occurs {found} times in {version} -- a figure "
                f"is declared by the smallest slice of prose that is UNIQUE, so "
                f"zero means the sentence was reworded or dropped and this row "
                f"rotted with it, and two means the row cannot say which "
                f"occurrence it covers"
            )
            continue

        numerals = list(_TOKEN.finditer(figure.phrase))
        if len(numerals) != 1:
            stale.append(
                f"{figure.phrase!r} contains {len(numerals)} numeric tokens "
                f"({[m.group(0) for m in numerals]}) -- a phrase spanning "
                f"several numbers would pass "
                f"every check here while only one of them was ever verified. "
                f"Narrow the phrase to the one figure this row declares"
            )
            continue

        # The phrase was located by plain substring search, so its numeral may
        # be a FRAGMENT of a longer number in the entry: `61 release entries`
        # occurs inside `161 release entries`, and every check below would then
        # compare 61 against 61 while the prose says 161. Same for a sign or a
        # decimal point immediately before it (`-17`, `.5`). So the numeral's
        # neighbours in the ENTRY must not extend it. Only the left side needs
        # a `.` guard: `_TOKEN` already swallows a decimal tail, so a `.` after
        # a matched token is sentence punctuation and never part of the number.
        # A following `,` is only extending when a digit follows it -- this file
        # groups with spaces, but a comma-grouped number must not read as its
        # first three digits.
        span = numerals[0]
        at = entry.index(figure.phrase)
        start, end = at + span.start(), at + span.end()
        # Two guards, because neither alone closes the substring false-green.
        #
        # The span must be a WHOLE numeral of the ENTRY, not merely of the
        # phrase. Checking neighbouring characters is not enough: `_TOKEN`
        # defines ASCII-space grouping as part of one numeral, so a phrase
        # ending at `61` survives the prose becoming `61 000`, and a phrase
        # opening at `137` sits happily inside `20 137` -- both with an
        # innocent space either side. Comparing spans makes the entry's own
        # tokenizer the authority on where a number ends.
        #
        # Then the neighbour check, for the two extensions `_TOKEN` cannot
        # express and so tokenizes identically at both spans: a leading sign or
        # decimal point (`-17`, `.5`), and comma grouping (`61,500`, where
        # `_TOKEN` stops at the comma). Slices rather than indexes so that
        # either end of the entry yields "" instead of an IndexError -- and ""
        # must not compare equal to a punctuation set, which is why this is a
        # set and not the substring test `before in ".,-"`.
        before, after = entry[start - 1 : start] if start else "", entry[end : end + 2]
        if (
            (start, end) not in entry_numerals
            or before in {".", ",", "-"}
            or (after[:1] == "," and after[1:2].isdigit())
        ):
            stale.append(
                f"{figure.phrase!r} matched inside a LONGER number in {version} "
                f"({entry[max(0, start - 4):end + 4]!r}) -- the row would compare "
                f"its own numeral against itself while the prose states a "
                f"different one. Widen the phrase to include the whole figure"
            )
            continue

        quoted = Decimal(span.group(0).replace(" ", ""))
        declared = Decimal(str(figure.value))
        if quoted != declared:
            stale.append(
                f"{figure.phrase!r} states {quoted} but this row declares "
                f"{declared} -- the prose and the declaration disagree"
            )
            continue

        derived = Decimal(str(figure.derive()))
        if derived != declared:
            stale.append(
                f"{figure.phrase!r} states {declared}, but the tree now says "
                f"{derived}"
            )

    assert not stale, (
        f"measured figures in the {version} entry no longer match the tree:\n  "
        + "\n  ".join(stale)
        + "\n\nRe-derive each from the tree and correct the PROSE -- never the "
        "other way round. A figure that moved after it was written is exactly "
        "what this checks: the entry is edited until merge, and the thing it "
        "measures moves with it."
    )
