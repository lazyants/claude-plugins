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
six figures in shipped entries that were re-derivable when this was written --
PLUGIN_BUNDLE_MEMBERS (17), ORCHESTRATION_BUNDLE_MEMBERS (5),
PRODUCER_CODE_CLOSURE (5), CACHE_KEY_FIELD_ORDER (15),
select_segments.CACHE_KEY_FIELDS (15), and 1.34.0's "88 new tests" -- ALL SIX
WERE CORRECT. Nothing wrong has reached a reader through this surface. Those
figures are frozen here on purpose: #446 has since moved the tuple to 18, and
the entries that state 17 are exactly the historical records the newest-entry
boundary below exists to leave alone. A sweep would have added a six-to-thirty-four-row declaration set
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
TESTS = PLUGIN_ROOT / "tests"

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


def _test_function_count(filename):
    """How many `def test_*` functions a SHIPPED test file defines, read by AST
    rather than by counting a string. A grep for `def test_` also matches the
    phrase inside a docstring or a comment -- which is exactly how a suite-size
    figure drifts without anything noticing."""
    tree = ast.parse((TESTS / filename).read_text(encoding="utf-8"))
    return sum(
        1 for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )


def _files_defining(names):
    """How many SHIPPED scripts assign any of `names` at module level.

    A COUNT OF FILES, not of grep hits: a changelog sentence about "N copies of
    this constant" is a statement about how many scripts hold their own
    definition, and a script that mentions the name in a comment or reads a
    sibling's copy is not one of them. Read by AST for the same reason
    _tuple_len() is -- a regex over the source counts the docstring paragraphs
    that discuss the duplication, which is precisely the prose a release entry
    is most likely to contain."""
    hits = []
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names
                for target in node.targets
            ):
                hits.append(path.name)
                break
    assert hits, f"no shipped script assigns any of {sorted(names)} any more"
    return len(hits)


def _local_dict_len(filename, funcname, varname):
    """Number of keys in the dict literal bound to `varname` inside module-level
    function `funcname`. Asserts the node IS a dict literal rather than counting
    whatever `len()` accepts -- the same trap _tuple_len() names."""
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == funcname:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == varname
                    for target in sub.targets
                ):
                    assert isinstance(sub.value, ast.Dict), (
                        f"{varname} in {filename}:{funcname} is no longer a dict "
                        f"literal, so its key count is not the figure cited"
                    )
                    return len(sub.value.keys)
            raise AssertionError(
                f"{funcname} in {filename} no longer binds {varname} -- the "
                f"derivation behind a changelog figure has lost its subject"
            )
    raise AssertionError(f"{funcname} is no longer defined in {filename}")


# Rewritten for 1.67.0 (#520), per the maintenance contract above. The rotation
# this replaces was 1.66.0's (#541) and it declared ONE row -- "30 new test
# functions across three files" -- which this rotation RETIRED, because 1.67.0
# became the newest entry when this branch rebased onto a main that had already
# shipped 1.66.0, and this guard checks the newest entry only. The list is empty
# as a result, never by inheritance. An empty list is indistinguishable from an
# unrotated one, which is what the sibling citation guard warns about, so this
# comment is written about THIS entry and states below why each quantity 1.67.0
# asserts is undeclarable here. `_files_defining`,
# `_local_dict_len`, `_tuple_len` and `_test_function_count` are kept unused,
# as earlier releases kept them, for the next entry that cites their class.
#
# ZERO rows, and for a reason this file can state precisely: every quantity the
# 1.67.0 entry asserts is spelled as a WORD -- "a fifth WARN", "the three scanned
# sections", "two of the three checkers", "two thirds of the he->en volume's
# blocks", "a second instance" -- and `_TOKEN` matches digits only, so none of
# them can be declared as a row here (a row whose phrase holds no numeral fails
# this test's own one-numeral-per-phrase check). The entry's actual numerals are
# of three kinds, none a measurement: version numbers (1.67.0, and 1.25.0/1.28.0
# cited as precedent for the same resume-identity movement), the issue number,
# the release date, and pipeline stage labels (W7, Step 0a).
#
# The one figure that COULD have been declared was rejected deliberately rather
# than missed: the entry says the scan covers three field families, which
# `_tuple_len("final_audit.py", "SCANNED_DRAFT_SECTIONS")` re-derives exactly.
# It is not declared because the entry spells that count as "three", not "3", so
# the phrase carries no numeral for the tokenizer to check -- rewording the prose
# to suit the guard would be the tail wagging the dog. This is the accepted
# residual the docstring above names, not an oversight.
#
# The rotations before it, each kept as its own record:
#
# Rewritten for 1.62.0 (#534), per the maintenance contract above. This rotation
# retired nothing and declared nothing. 1.62.0's figures are FIELD measurements
# over two live books (109 title occurrences for ~10 defects; 98 quotation sites,
# 86 already correct, 66 of them under a different rule; 93 roman against 2
# italic) plus one measurement of the rendered prompt (11 lines to 13, +1 072
# characters, re-derived by rendering the template at origin/main and at HEAD).
# The prompt measurement is the only one this tree CAN re-derive, and it is
# still not declared: its derivation would have to shell out to node, render
# both revisions of a template file and diff them, which is a second
# implementation of what tests/fix_prompt_class_concentration.test.py's harness
# already owns -- and a derivation that cannot read a live book, as the other
# three would need to, hardcodes its own answer, the `lambda: 17` failure this
# file refuses. Recorded here so the empty row list reads as a decision.
#
#
# The previous rotation, kept as its own record:
# Rewritten for 1.65.0 (#510 -- the glossary agent's trap discovery is
# rerouted, and the durable prompt is gated on content), per the maintenance
# contract above. This rotation inherited an already-empty row list from
# 1.63.0 (#526) and left it empty; the last row this file carried,
# `defines 72 test functions`, was declared by 1.58.0 (#433) and retired by
# 1.62.0 (#534).
#
# ZERO rows, because 1.65.0 states no figure this file's tokenizer can see. Its
# actual NUMERALS are of three kinds, none of them a measurement: the version
# and issue numbers, the heading's release date, and the contract-rule labels
# (R9, and the `3` of an unchanged PROMPT_CONTRACT_VERSION marker). Every real
# quantity it states is spelled out as a WORD, which `_TOKEN` cannot see: "all
# three TASK files" (the count profile_validate.py's resumed-project check
# walks), the "four assertions" a review round's inversion mutant turned red,
# and the pre-fix token counts recorded in tests/glossary_trap_routing.test.py's
# docstring. The last two are counts over a file as it stood BEFORE this diff,
# or over a mutant that exists in no tree at all, so neither is re-derivable
# here however it were phrased. Declaring any of them would hardcode an answer,
# which passes every assertion below while proving nothing (`lambda: 3`).
#
# Stated exactly because a rotation of THIS block on THIS branch got it wrong:
# it claimed to have inherited an empty list while the base it had just been
# rebased onto declared one row, and only a reviewer reading the note caught it.
#
# The previous rotation, kept as its own record:
# Rewritten for 1.63.0 (#526), per the maintenance contract above. The previous
# rotation (1.51.0, #498) retired the one inherited row, the size of
# `PLUGIN_BUNDLE_MEMBERS`.
#
# ZERO rows, because no figure 1.63.0 states is one this file's tokenizer can
# both SEE and RE-DERIVE. Every numeral it can see is an IDENTIFIER, never a
# measurement: version numbers, issue numbers, the heading's release date, the
# workflow-step names (`W5`, `W6`, `Step 0a`), and the round and segment ids of
# the field measurement (`round 1`, `round 2`, `seg26`, `seg32`, `seg33`,
# `seg38`, `seg20`) -- which name where something was observed in an
# operator-owned durable root, not a quantity this tree could recompute.
#
# Every real quantity the entry states is spelled as a WORD, which `_TOKEN`
# cannot see, so none can be a row here -- a row whose phrase holds no numeral
# fails this file's own one-numeral-per-phrase check. That set is the field
# measurement behind the issue ("five distinct false findings across two
# rounds", and the "six segments earlier" distance to the already-glossed first
# occurrence), all of it computed over an operator-owned durable root that is
# NOT in this repository, so no derivation here could re-check it however it
# were phrased.
#
# Every remaining spelled-out quantity in the entry was either removed or turned
# into an enumeration, so no undeclared tree-derived count is left in it. Two
# counts were REMOVED outright, both spelled as words and so invisible here. The first said the
# false #529 sentence occurs "in three places"; the entry now ENUMERATES the
# three sites by name, which says more and cannot drift into a wrong total. The
# second said 1.37.0's apply-side half had stood "for three releases", which the
# closing review measured as false against this file's own headings; the entry
# now names 1.37.0 rather than counting forward from it. A distance stated in
# prose rots on the next release, and nothing here or in the citations guard can
# see it. A third, smaller one went the same way: the entry said "the two LIVE
# copies" of the superseded sentence and now NAMES both, so the sentence cannot
# disagree with the enumeration above it.
#
# What remains and is NOT a count: "one segment". It is the domain fact the whole
# release is about -- review_TASK.template.md's own contract line is "You review
# exactly ONE segment per call" -- not a quantity derived from this tree, so
# there is nothing here to re-derive it against and nothing that could make it
# drift.
#
# Declaring any of the rest would mean hardcoding an answer, which passes every
# assertion below while proving nothing (`lambda: 4`). This is the accepted
# residual the docstring above names, not an oversight.
# Rewritten for 1.66.0 (#541), per the maintenance contract above. This rotation
# inherited an EMPTY map -- 1.65.0 (#510) and 1.63.0 (#526) each declared none,
# and 1.62.0 (#534) had already retired the last row before them -- and declared
# one row of its own: the count of `def test_*` FUNCTIONS this release adds,
# which `_test_function_count` re-derives from the tree. Past tense about what
# was inherited, never about what the base holds: this branch has been rebased
# six times and the sentence had to be rewritten against a different base each
# time.
#
# The entry also states the COLLECTED case count in parentheses; that one is
# deliberately NOT declared, because re-deriving it means expanding the
# `@pytest.mark.parametrize` argument lists, and a row hardcoding it would pass
# every assertion below while proving nothing (a `lambda` returning 39). The two
# The 1.67.0 (#607) entry's one re-derivable figure. Its other numerals are not
# measurements in this file's sense: version and issue numbers, the release date,
# and the estimator arithmetic (86 -> 94 calls, 106 and 37 segments at the two
# caps), which is re-derived in tests/batch_size_estimator.test.py against the
# template's own formula rather than restated here -- declaring it a second time
# would make this file the third copy of one number, which is the shape #580 was
# filed about.
FIGURES = [
    Figure(
        phrase="38 new test functions across two files",
        value=38,
        derive=lambda: sum(_test_function_count(f) for f in (
            "fix_scope_audit.test.py",
            "fix_scope_gate.test.py",
        )),
    ),
]


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
