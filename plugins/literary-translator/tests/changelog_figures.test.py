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
figures are frozen here on purpose: #446 moved the plugin tuple to 18 and #369
moved it to 19 (17 scripts + two workflow templates) while taking the
orchestration tuple from 5 to 6, and the entries that state 17 and 5 are exactly
the historical records the newest-entry boundary below exists to leave alone --
a figure in a shipped entry is a fact about that release, not a live assertion.
A sweep would have added a six-to-thirty-four-row declaration set
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
import importlib.util
import re
import sys
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


def _int_constant(filename, name):
    """Value of a module-level `NAME = <int literal>` in a SHIPPED script, read
    by AST rather than imported, for the same reason `_tuple_len` is: importing
    would execute the module and bind this test to whatever else it does at
    import time. The node must BE an int constant -- a name bound to something
    merely int-like would let this row report a value it did not read."""
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, int
            ), (
                f"{name} in {filename} is no longer an int literal, so it is "
                f"not the number a changelog figure cites"
            )
            return node.value.value
    raise AssertionError(
        f"{name} is no longer a module-level assignment in {filename} -- the "
        f"derivation behind a changelog figure has lost its subject"
    )


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


def _frozenset_len(filename, name):
    """Member count of a module-level `frozenset({...})` in a SHIPPED script,
    read by AST for the same reason `_tuple_len` is.

    The node must be `frozenset` called on a SET DISPLAY, not merely something
    `len()` accepts: `frozenset("abc")` is a legal three-member frozenset built
    from a string, and a row reading that would report it had counted declared
    members. A duplicated member would also make the literal's length disagree
    with the frozenset's, so the count is taken from the EVALUATED set."""
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            value = node.value
            assert (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "frozenset"
                    and len(value.args) == 1
                    and isinstance(value.args[0], ast.Set)), (
                f"{name} in {filename} is no longer `frozenset({{...}})` over a "
                f"set display, so its length is not the member count a changelog "
                f"figure cites"
            )
            return len(ast.literal_eval(value.args[0]))
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


# Rewritten for 1.69.0, per the maintenance contract above. 1.69.0 is a BATCH
# FOLD -- the merges it labels each landed on `main` on their own PR without a
# version bump -- so the rotation it replaces is 1.68.0's (#607), whose single
# row, "38 new test functions across two files", is RETIRED here with the entry
# it described. (The record below that row was headed 1.67.0 while the row it
# introduced was 1.68.0's; that mislabel is left in place as the historical text
# it is, and is named here so this rotation is not read as inheriting from it.)
#
# TWO rows, both membership counts of a SHIPPED tuple, because a batch fold's
# one release-level cost is exactly that: it moves `plugin_bundle_hash`, and the
# two tuples the entry cites are the only figures in it a derivation can reach.
# `_tuple_len` reads each by AST from the script that owns it.
#
# What the entry states and this file deliberately does NOT declare, so an empty
# space below is read as a decision rather than an omission:
#
# - "fifteen of the nineteen plugin-bundle members carry a diff in this range",
#   and the six named schemas. Both are measurements over a GIT RANGE
#   (1.68.0..this cut), not over the tree, and this file can only read the tree.
#   A derivation would have to shell out to `git diff` against a base commit the
#   fold's own merge can move, which is a second implementation of what the diff
#   already says -- and pinning the base here would hardcode the answer, the
#   `lambda: 17` failure the docstring refuses. Both are spelled as WORDS or as
#   an enumeration for that reason: the schemas are NAMED rather than counted, so
#   that half cannot drift into a wrong total at all.
# - The remaining numerals are identifiers, never measurements: the version
#   numbers, the release date, every issue and PR number in the enumeration, the
#   pipeline stage labels (W1, W2, W3, W5, W7, R8, Step 0a), the two exit codes
#   in the #277 line, the interpreter versions in the #679 line, and `U+2028`.
#
# The rotations before it, each kept as its own record:
#
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
def _gate_durable_path_args():
    """How many of the arguments the W5 gate hands `glossary_batch_plan.py` are
    DATA PATHS taken from the target durable root -- the claim the 1.77.0 entry
    makes when it says the planner is invoked root-bound rather than left to
    self-anchor. Counted off the argv construction itself via `ast`, pairing each
    `--flag` literal with the expression that supplies its value, because the
    property under test is which values come from `durable_root` -- not how many
    flags there are. `--min-candidate-freq` is a scalar and must NOT count; if a
    later release passes it as a path, or drops one of the three, this goes red
    instead of quietly agreeing with stale prose."""
    src = (SCRIPTS / "select_segments.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_glossary_current")
    pairs = []
    for node in ast.walk(fn):
        items = None
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            items = node.value.elts
        elif isinstance(node, ast.AugAssign) and isinstance(node.value, ast.List):
            items = node.value.elts
        if not items:
            continue
        for flag, value in zip(items, items[1:]):
            if isinstance(flag, ast.Constant) and isinstance(flag.value, str) \
                    and flag.value.startswith("--"):
                pairs.append((flag.value, ast.unparse(value)))
    from_durable = {f for f, v in pairs
                    if "durable_root" in v or "senses_path_arg" in v}
    assert pairs, "argv construction not found -- the derive, not the prose, is broken"
    return len(from_durable)


def _glossary_driver_deadline_default():
    """The glossary driver's own `--deadline-sec` default, read off its argument
    parser rather than regexed out of the source: the parser is what an
    operator's bare invocation actually gets, so it is the authoritative answer
    to "how long does the driver wait"."""
    path = SCRIPTS / "glossary_dispatch_driver.py"
    spec = importlib.util.spec_from_file_location(
        f"gdd_figures_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_arg_parser().get_default("deadline_sec")


def _name_discovery_passes_default():
    """name_discovery.py's own default `--passes`, read off its argument parser
    for the same reason the sibling above reads the glossary driver's: the
    parser is what a bare invocation gets, so it is the authoritative answer to
    "how many passes does discovery actually run", and no schema `default`
    annotation fills it in (nothing in this plugin does)."""
    path = SCRIPTS / "name_discovery.py"
    spec = importlib.util.spec_from_file_location(
        f"nd_figures_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.build_arg_parser().get_default("passes")


def _fetch_retry_delay(position):
    """The glossary driver's own `_FETCH_RETRY_DELAYS_SEC[position]`, read off
    the module rather than regexed out of the source: the tuple IS the retry
    ladder -- its length is the retry count and its members are the waits -- so
    a release that retunes either moves this figure, which is exactly what the
    1.86.0 entry quotes."""
    path = SCRIPTS / "glossary_dispatch_driver.py"
    spec = importlib.util.spec_from_file_location(
        f"gdd_retry_figures_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._FETCH_RETRY_DELAYS_SEC[position]


FIGURES = [
    # ROTATED TO 1.93.0 (#859 -- reject_review.py reported success for a segment
    # the driver had stopped reading), per the maintenance contract above.
    #
    # ZERO rows, and the reason is specific rather than an empty rotation left to
    # be read as an omission. The entry quotes exactly one measurement, "one live
    # volume of 39 segments", and it is a fact about A BOOK IN ANOTHER REPOSITORY:
    # how many segments that volume has, and how many rejections its operator
    # wrote into the void before looking at the classifier rather than at the
    # script's own output. This file re-derives a figure by calling an
    # authoritative implementation in THIS tree, and no implementation here can
    # see that volume -- the same reason the rotations below declared zero rows
    # for their own field measurements.
    #
    # The entry is deliberately worded without a tree-owned count. An earlier
    # draft said "Eleven regression cases cover it", which IS derivable -- the
    # parametrized never-gates cases plus the UnicodeDecodeError case -- and was
    # cut rather than declared, because `_TOKEN` matches digits and cannot see a
    # spelled-out figure: a number this file is structurally unable to check does
    # not belong in an entry whose whole point is that its figures are checked.
    # The migration paragraph likewise NAMES `PLUGIN_BUNDLE_MEMBERS` and
    # `SAFE_STALE_CARVEOUT_FIELDS` rather than quoting their sizes. The two byte
    # bounds the release adds are tree-owned constants, and they are pinned where
    # a pin can hold -- `review_rejection_consumer_warning.test.py` reads both by
    # AST and asserts the shipped value is the one applied -- rather than quoted
    # in prose, so the entry names neither.
    #
    # The remaining numerals are identifiers: the version numbers, the release
    # date, the issue number (#859), and the `8` of `UTF-8`, which names an
    # encoding.
    #
    # The 1.92.0 rotation this replaces is preserved in this file's history; the
    # rows below are the older records the maintenance contract keeps.
    #
    # The 1.91.0 rotation this replaces, kept as its own record (#858 -- a
    # project's first glossary run dispatched every batch against a canon.json
    # that did not exist):
    #
    # ZERO rows. The entry quotes exactly two measurements -- "of 12 dispatches
    # made while the file was absent, 2 answered with a question" -- and both are
    # facts about ONE LIVE RUN OF A BOOK IN ANOTHER REPOSITORY: how many codex
    # dispatches that pass made before its canon existed, and how many of them
    # came back with a question instead of a fragment. This file re-derives a
    # figure by calling an authoritative implementation in THIS tree, and no
    # implementation here can see that run. The migration paragraph deliberately
    # NAMES `PLUGIN_BUNDLE_MEMBERS` rather than quoting its size, which is
    # exactly the figure a row would otherwise have to be bought for. The
    # remaining numerals are identifiers: the version numbers, the release date,
    # the issue numbers (#858, #290), and `W3` / `Step 0a`, which name workflow
    # steps. None of them counts anything.
    #
    # The 1.90.0 rotation this replaces, kept as its own record
    # (#853 -- a transient network fault is retried, not charged to the citation):
    #
    # TWO rows, both members of `_FETCH_RETRY_DELAYS_SEC`. That tuple is the
    # retry ladder itself -- its length is how many extra passes a transport
    # failure buys and its members are the waits between them -- so it is a
    # figure this tree owns and one a later retune moves silently, which is the
    # shape this file exists for.
    #
    # NOT declared, and each for its own reason. The entry's live-run
    # measurements -- 19 entries failing from fetch position 0, 5 of 12 batches
    # exhausted, 20 repair rungs spent, 0 honest downgrades in ~30 repair
    # opportunities, hosts answering in ~20 ms -- are facts about runs of a book
    # in ANOTHER REPOSITORY, which nothing here can reach, exactly as the 1.84.2
    # rotation below records for its own. The EAI numbers (2 on Darwin, -3 on
    # glibc) are the running platform's libc constants, not this tree's: one of
    # the two is unreachable from whichever machine reads it, and a row that
    # could only ever check the local half would assert less than it looks like.
    # "up to three passes" is spelled as a word and so is invisible to the
    # tokenizer these rows are read with -- and it is the same tuple's LENGTH,
    # which the two rows above already pin the contents of.
    #
    # The 1.89.0 rotation this replaces, kept as its own record (#860 -- the
    # generic third-language clause defers to the project's own convention):
    #
    # ZERO rows. Every measurement this entry quotes was taken on a Hebrew->English
    # SERIES IN ANOTHER REPOSITORY: one volume's first-round finding counts and
    # their class share (97 over 30 segments, 62 of them), the round-over-round
    # comparison on the 20 segments both rounds cover (57 to 20, medium 44 to 5,
    # ~1.1 per segment for every other class), and the non-Latin run counts in two
    # delivered books (234 across 60 of 79 chapters, 755 across all 42). No
    # implementation in THIS tree can see a delivered book, so none of them is
    # re-derivable here by the only method this file accepts -- calling the
    # authoritative implementation.
    #
    # The one quantity the tree DOES own -- how many documents carry the rule --
    # is deliberately not written as a numeral to be declared. The entry
    # ENUMERATES the four carriers by name in its own list, which is the shape
    # the 1.83.1 rotation below settled on for exactly this case: naming the
    # places beats counting them, because the name survives a later edit that
    # would leave a count stale. `tests/third_language_defers_to_style_bible.test.py`
    # is where that set is asserted, against its own `EXPECTED_CARRIER_COUNT`.
    #
    # The remaining numerals are identifiers, not measurements: the version
    # numbers (including `1.11.0`, naming the release that added the fill block),
    # the release date, the issue numbers (#860, #203), `notes[]` and `W1`, which
    # name a draft field and a workflow step, and `R8`, which names an
    # engine-loop rule.
    #
    #
    #
    # The 1.88.1 rotation this replaces, kept as its own record (#862), with
    # its own banner rewritten as this demotion header:
    #
    # ZERO rows. The entry's only quantities are a count of volumes in a live
    # series translated in ANOTHER REPOSITORY -- two corrected by hand, a third
    # still carrying the inherited value -- which no implementation in this tree
    # can reach. Every claim about THIS tree is worded by naming the thing rather
    # than counting it: `profile.example.yml`, `validate_draft.py`'s substring
    # scan, `cache_key.py` hashing the project's own profile, and the fact that
    # the field is not a `CHOOSE_` sentinel. The count that was available and
    # deliberately not quoted is how many `CHOOSE_` sentinels the example ships;
    # the entry says the field is not one of them, which is the property it is
    # actually standing on and which does not rot when a later release adds
    # another. The remaining numerals are identifiers: the version numbers, the
    # release date and the issue number (#862).
    #
    # The 1.87.0 rotation this replaces, kept as its own record (#861 --
    # assemble.py had no argument parsing, so --help wrote the whole output
    # vault instead of printing usage):
    #
    # ZERO rows. The entry's only measurements are facts about ONE LIVE RUN OF A
    # BOOK IN ANOTHER REPOSITORY -- the 31 chapters and 665 entity notes that
    # `--help` wrote on the reporting operator's project -- and no
    # implementation in this tree can see that run. Everything the tree DOES own
    # that a later release could MOVE is stated by NAMING rather than counting:
    # the sibling scripts without `argparse` are listed (`draft_sha1.py`,
    # `output_resolve.py`, `scaffold_validate.py`, `validate_assembled.py`)
    # instead of counted, the untouched loaders are listed instead of counted,
    # and the migration paragraph names
    # `PLUGIN_BUNDLE_MEMBERS`, `DERIVATION_BUNDLE_MEMBERS`,
    # `ORCHESTRATION_BUNDLE_MEMBERS` and `_RENDER_VERSION_FILES` rather than
    # quoting their sizes or their number -- the 1.83.2 rotation below is what
    # quoting one costs. The remaining numerals are identifiers and exit codes:
    # the version numbers, the release date, the issue number (#861), and the
    # `0`/`2` of the CLI contract, which are literals in the source rather than
    # measurements. The entry does carry a few small counts that are NOT rows
    # here and the distinction is worth stating: "one WRITING deterministic
    # step", "the only script", "Both changed scripts" and "more than one"
    # mutually exclusive mode describe THIS RELEASE and its subject, and are
    # fixed by what the entry is about rather than by a tuple a later release
    # can grow -- a release that adds a second writing step does not make this
    # entry's sentence about #861 false. "zero-argument", "one line" and
    # "one-JSON-line" name contracts, not quantities measured off the tree.
    #

    # The 1.85.0 rotation this replaces, kept as its own record (#856 --
    # canon_adjudication_audit.py's stderr detail lists had no way past their
    # first 20 items):
    #
    # ONE row. The entry's `20` is the new `--limit` default, and it is a
    # TREE-OWNED constant -- `DEFAULT_ITEM_PRINT_LIMIT` in the shipped script --
    # so it is exactly the kind of figure that rots silently if a later release
    # retunes the default and leaves this prose behind. Read by AST rather than
    # off the argument parser: `_int_constant` does not execute the module, and
    # the parser default is that same constant by construction.
    #
    # The entry's other numerals are NOT rows. `161` and `22` are the required-item
    # counts measured on one book in ANOTHER repository -- no implementation here
    # can see that canon, the same reason the 1.84.2 and 1.84.0 rotations below
    # declared zero rows for their own field measurements. The `10` of
    # `_orphan_warning`'s "first-10 elision" is a source literal in a slice
    # expression, an identifier of behaviour rather than a measurement, like the
    # `400` of `err[-400:]` the 1.84.1 rotation names below. The remainder are
    # identifiers: the version numbers, the release date, the issue number (#856),
    # the `0` of `--limit 0` and of `` `0` prints every item `` (a flag ARGUMENT,
    # a literal the prose is defining rather than counting), and the exit code
    # `2`, a literal in the contract.
    #

    # The 1.84.2 rotation this replaces, kept as its own record (#852 -- a
    # reconciliation reset left behind the very snapshots that refuse its
    # re-drive):
    #
    # ZERO rows. The entry quotes exactly one measurement, "three batches of
    # eleven", and it is a fact about ONE LIVE RUN OF A BOOK IN ANOTHER
    # REPOSITORY: how many batches of that pass ended `approve-failed`. This file
    # re-derives a figure by calling an authoritative implementation in THIS
    # tree, and no implementation here can see that run -- it is not even a
    # property of a delivered artifact, only of a run that has since been
    # recovered by hand. The rest of the entry is deliberately worded without a
    # tree-owned count: the migration paragraph names `PLUGIN_BUNDLE_MEMBERS` and
    # `DERIVATION_BUNDLE_MEMBERS` rather than quoting their sizes, which is
    # precisely the figure the 1.83.2 rotation below had to declare three rows
    # for. The remaining numerals are identifiers: the version numbers, the
    # release date, the issue number (#852), `attempt 0` / `out_{i}_attempt_0.json`,
    # which name a rung and a filename, and `W3` / `W3a` in the migration
    # paragraph, which name workflow steps. None of them counts anything.
    #
    # The 1.84.1 rotation this replaces, kept as its own record (#851 -- the
    # glossary driver logged `err` for a command whose reason only ever reaches
    # stdout):
    #
    # ZERO rows, and for the reason this file's own economics section prefers:
    # the entry was WORDED to keep its claims re-derivable-free rather than
    # quoting a count and then buying a derivation for it. The one measurement
    # the work produced -- the byte size of a multi-row `canon_validate.py`
    # refusal payload, which is what makes tail-slicing stdout lose the `error`
    # field -- would have been re-derivable only by shelling out to the shipped
    # script with a pinned fragment, machinery heavier than every row this file
    # has ever carried. The entry says "runs to thousands of bytes" instead, and
    # the fact it is standing on is pinned where it belongs: as an executable
    # assertion in `glossary_dispatch_driver.test.py`, whose truncation test
    # builds an over-2 000-byte payload and fails if the slice is taken from the
    # wrong end. The remaining numerals are identifiers or code literals: the
    # version numbers, the release date, the issue number (#851), the batch and
    # attempt indices in the quoted log block, and the `400` of `err[-400:]`,
    # which is a literal in the source rather than a measurement.
    #
    # The 1.84.0 rotation before that one, kept as its own record (#844 --
    # `validation.terms` silently double-counted when one declared `source_form`
    # nested inside another): also ZERO rows, and for the same reason. Every
    # measurement that entry quotes was taken on DELIVERED BOOKS IN ANOTHER
    # REPOSITORY -- the two volumes' nesting pin counts (65 containing 63, 49
    # containing 42) and the substring-vs-token count of a pinned title (133
    # against 61). Its new behaviour is described by NAMING `term_pin_overlaps()`,
    # `warn_details` and `DERIVATION_BUNDLE_MEMBERS` rather than by quoting a
    # count of anything the tree owns -- deliberately, since the rotation IT
    # replaced shows what quoting one costs. Its remaining numerals are
    # identifiers: the version numbers, the release date, the issue number
    # (#844), and the exit code `0`, a literal in the contract rather than a
    # measurement.
    #
    # The 1.83.2 rotation this replaces, kept as its own record (#843 --
    # name_discovery.py --dispatch passes the resolver its required
    # --durable-root): THREE rows, each the size of a cache-key bundle tuple
    # that does not list `name_discovery.py` (21 for PLUGIN_BUNDLE_MEMBERS, 6
    # for ORCHESTRATION_BUNDLE_MEMBERS, 2 for DERIVATION_BUNDLE_MEMBERS) -- all
    # module-level tuples this tree owns, and exactly the figures that rot when
    # a later release adds a member.
    #
    # The 1.83.1 rotation, kept as its own record: also ZERO rows,
    # because that entry asserted a DOCUMENTATION state -- that no shipped
    # document said what `glossary_rule` must hold, and that nothing executable
    # changed. Its closest candidate figure, "how many places mentioned the field
    # before this release", is a fact about the PREVIOUS tree, which this one
    # cannot reach, so the entry enumerated those places rather than counting
    # them.
]

# The version FIGURES was last rotated to. An empty FIGURES makes the loop in
# the second test iterate zero times, which prints exactly what a passing one
# prints -- so the rotation itself is what gets asserted, and a release that
# forgets to rotate goes RED instead of silently checking nothing.
FIGURES_VERSION = "1.93.0"


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


def test_the_figure_rows_were_rotated_to_the_newest_entry():
    """FIGURES is written against ONE entry -- the release being edited. When
    a new entry lands and the rows are not rotated, every row's phrase stops
    occurring and the check below goes red loudly. But rotating to an EMPTY
    list is legitimate (an entry claiming no re-derivable number), and then
    nothing below iterates, so a stale empty list would sail through every
    later release unnoticed. This is the pin that makes the rotation itself
    the thing under test."""
    version, _entry = _newest_entry()
    assert version == FIGURES_VERSION, (
        f"CHANGELOG's newest entry is {version} but FIGURES was rotated to "
        f"{FIGURES_VERSION}. Rewrite FIGURES against the {version} entry -- "
        f"one row per number in it the tree can re-derive -- and update "
        f"FIGURES_VERSION. An empty list is a valid answer when the entry "
        f"claims no such number; say so in a comment, as the 1.72.0 and "
        f"1.74.0 rotations do."
    )


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
