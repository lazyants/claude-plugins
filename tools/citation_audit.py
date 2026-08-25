#!/usr/bin/env python3
"""Audit every `file.ext:NNN` source citation in this repo against a declared anchor map.

WHAT THIS IS FOR (#579). This repo cites source by line number in prose, docstrings, comments and
test docstrings. Line numbers move whenever anything above them is edited, and until now nothing
checked them outside the newest CHANGELOG entry. Measured on `origin/main` `adf9f5d8` before this
tool existed: of 21 randomly sampled live citations, 15 pointed at unrelated code, off by 14 to
1400 lines.

WHAT THIS CHECKS, AND WHAT IT CANNOT. It checks that a citation still POINTS AT ITS CLAIM -- that
the strings a human or model declared as the claim's load-bearing tokens are still inside the cited
range, in the declared order. It does NOT check that the claim is TRUE, and it cannot: a citation
that was wrong the day it was written can be given anchors drawn from the wrong range and this tool
will pass it forever. No deterministic gate can audit the adjudication that produced its own inputs.
#579 says the same thing -- the complete check is a model reading each citation together with its
own sentence -- and that pass is what corrected this corpus once. This tool's job is to stop the
result from rotting again, not to establish it.

WHY ANCHORS AND NOT SOMETHING SIMPLER. `plugins/literary-translator/tests/changelog_citations.test.py`
built and defeated the two weaker designs, and its docstring is the record:
  * "line NNN exists in a file of at least NNN lines" cannot fail on drift -- a citation that slides
    still names a line that exists -- so it shares its blind spot with the defect. It reported clean
    for three rounds while eight citations pointed at unrelated code.
  * "one anchor appears anywhere in the range" was defeated by inserting lines INSIDE a wide range,
    pushing the claim past the end while the anchor sat safely near the start.
So anchors are an ordered LIST, both bite-halves fire (a citation with no declaration fails, and a
declaration no citation uses fails), and a range declares enough anchors to span its claim.

WHY A SNAPSHOT HASH WAS REJECTED. A content hash of the cited lines is auto-derivable, and anything
auto-derivable can be stamped in bulk -- which is precisely how ~170 known-wrong citations would have
been certified as verified. Anchors cannot be derived; they come out of adjudication. That is the
property that makes them worth anything.

BARE CONTINUATIONS (#754). Prose here names a file once and then keeps citing it without repeating the
name -- "<file> line 194, hashed <colon>335, stale-check <colon>599". `CITATION_RE` requires the
filename, so until now none of those was enumerated at all: measured on `origin/main` `94618b4c`, 30 such
tokens across 21 files, FOUR of them already pointing at unrelated code. A bare token is enumerated only
when the smallest backward window that names any file names exactly one (see `_continuation_candidates`),
and its declaration key carries that attribution, so a re-attribution cannot inherit the old anchors.
What is still NOT enumerated, and is the honest residual: 86 citation-shaped bare tokens in 26 files whose
neighbourhood names no file with a line number at all -- `reference-assets.test.sh` citing a reference
asset it reaches through a shell variable, `chapter-paths.mjs` citing its own body. Attributing those
needs "no file named => the container itself", and `reference-assets.test.sh` is the counterexample that
makes that rule silently point at the wrong file.

NOTHING HERE WRITES TO A SOURCE FILE. An earlier design had a `--renumber` mode that would move a
number when its anchors turned up elsewhere. It was cut rather than guarded: `final_audit.py` holds
`_fold_source_marks` at its definition and at two call sites, so a moved definition would have been
"repaired" to point at a call, and the same weak anchor would then have kept the gate green. The
tool reports where anchors were found; a human moves the number.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHOR_DIR = os.path.join(REPO_ROOT, "tools", "citation-anchors")

# Any alphabetic extension, deliberately -- NOT an allowlist of known extensions. An allowlist's
# misses are silent and unbounded: the first draft of this tool used one and dropped
# both `extract.py.template` citations without a trace. An over-match lands
# in `undeclared` and has to be dismissed by writing an exemption with a reason, which is at worst
# review-visible. Shape borrowed from changelog_citations.test.py's own `_CITATION`, whose comment
# reaches the same conclusion for the same reason.
# The range separator accepts a typographic dash as well as a hyphen. Prose that has been through
# an editor carries en-dashes, and this repo already had one: `extract.py.template:1110–1279` in
# `source-prep.md` parsed as the single line `:1110`, so the wide-range rule never fired and the
# far end of a 170-line region was pinned by nothing while the gate reported OK. A separator the
# parser does not know narrows a range SILENTLY, which is the prior art's second defeated design
# arriving by a different route.
CITATION_RE = re.compile(
    r"\b((?:[A-Za-z0-9_.-]+/)*[A-Za-z_][\w.-]*\.[A-Za-z][\w]*):(\d+)(?:[-–—](\d+))?\b")

# A citation's own lexeme is never a subject token (see `_subject_required`).
CODE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\(\))?|[A-Z][A-Z0-9_]{3,}")

# No line in this repo is anywhere near this long -- the longest tracked line is 9395 characters, in
# `publish-targets/README.md`. The bound exists because `CITATION_RE`'s path prefix
# `(?:[A-Za-z0-9_.-]+/)*` re-scans from every start offset, so one line of path-shaped text costs
# O(n^2): measured through this function, 8 KB takes 0.23s, 16 KB 0.97s, 32 KB 3.79s -- a clean 4x
# per doubling, against 1.9s for the entire honest 574-file run. This gate runs on `pull_request` in
# a public repo, so a fork's tree is scanned, and a single multi-megabyte line would hold a runner
# until the 6-hour job cap. An over-long line is REPORTED, not silently skipped (see
# `LINE-TOO-LONG`): a scanner that quietly drops content is the failure this whole tool exists to
# remove.
MAX_SCANNED_LINE = 20000

# A bare `:NNN` continuation. The OPENER SET IS THE WHOLE RULE, and it is an allowlist: start of line,
# whitespace, a backtick, `(`, or `/`. Everything else is a shape that is not a citation and whose false
# RED a maintainer switches the gate off over. `[` is out because `x[:10]` slice syntax appears ~200 times
# here (admitting it moves the unattributable pool from 93 to 255, every one of the 162 a slice); `"` is
# out because of JSON (`{"const":1}`); a preceding `:` is out because of IPv6 (`2606:4700:4700::1111`).
# `/` is IN for the opposite reason -- a slash-separated run of line numbers after ONE filename, as one
# `.d.mts` declaration file writes it, is two real continuations, and a slash followed by a digit outside
# a URL scheme occurs on exactly three tracked lines in this whole repo: that one, a clock, and this
# tool's own registry, which is not scanned. So the slash gains two and costs nothing measurable.
BARE_CONTINUATION_RE = re.compile(r"(?:^|(?<=[\s`(/])):(\d+)(?:[-–—](\d+))?\b")

# How far back a continuation may look for the file it continues. Measured window depth actually used by
# this corpus: 0 for 15 occurrences, 1 for 10, 4 for two, 5 for one. At 3 the three deepest -- in
# a skill reference and a driver test -- are lost; at 6 they are found and
# nothing else changes. The bound exists so a citation at the top of a long comment block cannot adopt a
# `:NNN` sixty lines below it.
MAX_CONTINUATION_LOOKBACK = 6

MIN_ANCHOR_LEN = 8
MAX_ANCHOR_OCCURRENCES = 3

# A single anchor bounds a range only from its START, so a claim can slide out of the far end -- the
# defeat that killed the prior art's second design. That needs ROOM to happen: across five lines or
# more, an anchor near the top leaves somewhere for the claim to go. Below that a lone anchor already
# spans most of the range, and demanding a second one is a false RED on citations that are correct
# and have no second load-bearing line to name. Counted INCLUSIVELY: `3-7` is five lines and is
# wide. The comparison and this number have to agree, or the constant documents a boundary the code
# does not enforce.
WIDE_RANGE_LINES = 5

# Mechanically recognisable non-citations. These need no hand-written exemption reason: a URL
# authority is `//host:port`, and a JSON/dotted path names no file that exists.
URL_AUTHORITY_RE = re.compile(r"//[A-Za-z0-9_.-]+:\d+")


def _run(*args):
    return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout


def tracked_text_files():
    """Every git-tracked file that decodes as UTF-8 and holds no NUL byte, minus CHANGELOGs.

    CHANGELOG entries are excluded deliberately, not overlooked: an entry records what a PAST
    release cited, so a citation in it is dated rather than stale. #579 says so, and
    `changelog_citations.test.py` already owns the newest entry by a different contract.
    """
    out = []
    for rel in _run("git", "ls-files", "-z").split("\0"):
        if not rel or os.path.basename(rel) == "CHANGELOG.md":
            continue
        # The anchor maps are this tool's own REGISTRY, not prose that cites anything. Every
        # declaration quotes the citation it describes, so scanning them makes the gate demand a
        # declaration for each of its own declarations -- 658 of them, measured, the moment these
        # files were first committed. Until then they were untracked and therefore invisible to
        # `git ls-files`, so the gate had been reporting green over a corpus that silently excluded
        # them: the green meant "the registry is not a tracked file yet", not "the registry is
        # consistent". A self-referential gate reads exactly like a passing one.
        if rel.startswith("tools/citation-anchors/"):
            continue
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        try:
            with open(path, "rb") as fh:
                head = fh.read(8192)
            if b"\0" in head:
                continue
            with open(path, encoding="utf-8") as fh:
                fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        out.append(rel)
    return sorted(out)


def normalize_sentence(text):
    """Collapse whitespace and NFC-normalize, so a quoted claim reads the same however it was wrapped.

    Used for DISPLAY and for comparing a recorded claim against the current text (`_adjudicated`).
    Nothing decides on it: identity is `decl_key`, which reads no prose.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def enclosing_sentence(lines, index):
    """The citing neighbourhood, for a HUMAN to read. This is not identity and carries no contract.

    A text fingerprint used to be identity here, and `decl_key` records why it is not any more.

    This function has one job:
    print enough context beside a problem, and record beside a declaration what was adjudicated. A
    display heuristic can be coarse. It cannot be wrong, because nothing decides on it.
    """
    lo = max(0, index - 1)
    hi = min(len(lines), index + 2)
    return normalize_sentence(" ".join(lines[lo:hi]))


# The markers that can open a CONTINUATION line. Markdown blockquote arrows repeat (`> >`, and `> #`
# for a quoted comment); the comment leaders and bullets do NOT. That asymmetry is measured, not
# stylistic: `# #697: ...` is a comment marker followed by an issue reference, and stripping `#`
# repeatedly turns `if self.rename_failed:` + `# #697: ...` into the citation `self.rename_failed:697`
# -- a file that does not exist. Five such fusion artifacts appeared across this repo the moment the
# leaders were made repeatable, and the tail screen in `_wrapped_citations` cannot see them, because
# scanning the tail alone yields no citation with those numbers either.
# No blockquote citation exists in this repo today. This is the direction that fails SILENTLY, so
# covering it costs one alternative in a regex, and not covering it costs a citation that is
# invisible to the gate for as long as it lives.
#
# The leader may sit on EITHER side of the arrows -- `# > quoted comment` and `> # quoted code` are
# both ordinary -- but only the branch that actually contains an arrow may take a leader twice, or
# `# #697` matches "leader, no arrows, leader" and the fusion returns by another door.
LEADER = r"(?:#|//|\*|--)"
CONT_PREFIX_RE = re.compile(
    rf"^\s*(?:{LEADER}\s*)?(?:>\s*)+(?:{LEADER}\s*)?|^\s*{LEADER}?\s*"
)

# The quote depth of a line, for deciding whether the next line CONTINUES it.
QUOTE_DEPTH_RE = re.compile(rf"^\s*(?:{LEADER}\s*)?((?:>\s*)*)")


def _quote_depth(line):
    return QUOTE_DEPTH_RE.match(line).group(1).count(">")


def _wrapped_citations(lines, i):
    """Citations broken across a line wrap, which a per-line scan cannot see at all.

    Measured on this repo when the gate was first wired: EIGHT real citations were invisible for
    exactly this reason -- `render_obsidian.py:` + `255-466`, `ledger_update.py:` + `789-792`,
    `validate_conservation.py` + `:1240-1242` and others. A silent miss is the failure direction
    this whole tool exists to remove, and the prior art already learned it in the other direction:
    `reference-assets.test.sh` uses `hasnt_joined` rather than `hasnt` because a needle re-wrapped
    across a line break reads as a satisfied claim.

    The join is made with NO glue, and that is the only join worth making: a citation contains no
    whitespace, so if the wrap replaced a SPACE then the whole citation sits on one side of the break
    and the per-line scan already has it. Only a break INSIDE the token hides one.

    That also means every straddling match has to be screened. Gluing "... relocated here from" to
    "canon_x.py:396" yields the token "fromcanon_x.py:396", which straddles the join and looks like a
    citation to a file that does not exist -- while the real citation was wholly on the next line and
    was already found. The screen is the tail: if scanning the tail ALONE already yields a citation
    with these same line numbers, the straddling match is a fusion artifact, not a wrapped citation.
    Measured on this repo: 10 straddling matches, of which 6 were exactly this artifact.
    """
    head = lines[i].rstrip()
    # A tail quoted MORE deeply than its head opens a new blockquote; it does not continue the line
    # above. Stripping its arrows anyway manufactures citations out of ordinary Markdown: a line
    # ending `The metric label is config.json:` followed by `> 697 failures were counted.` becomes
    # `config.json:697`, a false RED that can only be dismissed by writing a bogus exemption.
    if _quote_depth(lines[i + 1]) > _quote_depth(head):
        return []
    tail = CONT_PREFIX_RE.sub("", lines[i + 1])
    if not head or not tail:
        return []
    tail_alone = {(m.group(2), m.group(3)) for m in CITATION_RE.finditer(tail)}
    found = {}
    for m in CITATION_RE.finditer(head + tail):
        if m.start() < len(head) < m.end() and (m.group(2), m.group(3)) not in tail_alone:
            found.setdefault((m.group(1), m.group(2), m.group(3)), m)
    return list(found.values())


def _continuation_candidates(lines, i, upto):
    """Every distinct citation TARGET in the smallest backward window that names one.

    Smallest, not "the paragraph": in the one real case that separates them, the surrounding comment
    block names two files and the line itself names one, and the attribution is not in doubt. Reading the
    nearer, more
    specific evidence first keeps that case out of the ambiguity branch. The walk stops at a blank line,
    at the top of the file, or at `MAX_CONTINUATION_LOOKBACK`.

    Returns a set: empty means "not enumerated" (the docstring's residual), one means an attribution,
    more than one means the caller must refuse to guess.
    """
    targets = set()
    for back in range(0, MAX_CONTINUATION_LOOKBACK + 1):
        j = i - back
        if j < 0:
            break
        seg = lines[i][:upto] if back == 0 else lines[j]
        if back and not seg.strip():
            break
        if len(seg) > MAX_SCANNED_LINE:
            break
        targets |= {m.group(1) for m in CITATION_RE.finditer(seg)}
        if targets:
            break
    return targets


def _consumed_spans(lines, i):
    """Column spans on RAW line `i` that a full citation already accounts for.

    Two sources. The per-line matches are trivial. The other is the TAIL half of a citation wrapped across
    the break from line i-1: `_wrapped_citations` matches against `head + stripped_tail`, so a straddling
    match's tail part always begins at index 0 of the STRIPPED tail -- and the raw column is therefore the
    width of the prefix `CONT_PREFIX_RE` removed, not 0. Where a comment leader opens the tail line, that
    is 2, and dropping it would leave the bare token unconsumed, enumerated a second time and attributed
    to whatever the head line named. The one real wrapped case in this repo has a zero-width prefix and
    cannot show that up, which is why the fixture uses a comment leader.
    """
    spans = [(m.start(), m.end()) for m in CITATION_RE.finditer(lines[i])]
    if i > 0 and len(lines[i - 1]) <= MAX_SCANNED_LINE:
        raw = lines[i]
        prefix = len(raw) - len(CONT_PREFIX_RE.sub("", raw))
        head_len = len(lines[i - 1].rstrip())
        for m in _wrapped_citations(lines, i - 1):
            spans.append((prefix, prefix + m.end() - head_len))
    return spans


def find_citations(rel, text):
    """Every citation occurrence in one file, with its enclosing sentence and per-file ordinal."""
    lines = text.splitlines()
    seen = {}
    out = []
    for i, line in enumerate(lines):
        # A per-line match knows its own column; a wrapped one has none, because the citation is not
        # intact on either raw line. `col` is deliberately absent rather than 0 for those -- an
        # occurrence is classified at its own offset (see `auto_exempt_reason`), and a fabricated 0
        # would classify the wrong text.
        if len(line) > MAX_SCANNED_LINE:
            continue
        matches = [(m, m.start()) for m in CITATION_RE.finditer(line)]
        if i + 1 < len(lines) and len(lines[i + 1]) <= MAX_SCANNED_LINE:
            matches += [(m, None) for m in _wrapped_citations(lines, i)]
        rows = []
        for m, col in matches:
            target, start, end = m.group(1), int(m.group(2)), m.group(3)
            rows.append((m.group(0), target, [target], m.group(0), False, col, start, end))
        spans = _consumed_spans(lines, i)
        for m in BARE_CONTINUATION_RE.finditer(line):
            if any(lo <= m.start() < hi for lo, hi in spans):
                continue
            cands = sorted(_continuation_candidates(lines, i, m.start()))
            if not cands:
                continue
            # The KEY carries the whole candidate set, not just the winner. With only the winner in it,
            # an ambiguous occurrence cleared by an explicit "target" keeps a constant key while the
            # window's candidates are swapped underneath it -- `resolve` accepts any tracked explicit
            # target -- and the run stays green against a file the sentence no longer names.
            rows.append((m.group(0), cands[0] if len(cands) == 1 else None, cands,
                         "|".join(cands) + m.group(0), True, m.start(),
                         int(m.group(1)), m.group(2)))
        for cite, target, cands, key_cite, is_cont, col, start, end in rows:
            sentence = enclosing_sentence(lines, i)
            # The ordinal is what separates two occurrences of one citation in one file. It is
            # positional by design: identity reads no prose (see `decl_key`). For a continuation it is
            # counted per RAW TOKEN -- `:3` is the second `:3` in this file whatever it was attributed
            # to -- and deliberately NOT per identity string: with the counter keyed on the identity,
            # two paragraphs that exchange their files produce the identical key SET, every declaration
            # stays used, and the swap leaves no trace in the registry. Per raw token it does.
            key = (rel, cite, is_cont)
            ordinal = seen.get(key, 0)
            seen[key] = ordinal + 1
            out.append(
                {
                    "container": rel,
                    "line": i + 1,
                    "col": col,
                    "cite": cite,
                    "key_cite": key_cite,
                    "target": target,
                    "candidates": cands,
                    "is_continuation": is_cont,
                    "start": start,
                    "end": int(end) if end else start,
                    "sentence": sentence,
                    "ordinal": ordinal,
                    "raw_line": line,
                }
            )
    return out


def auto_exempt_reason(c):
    """Classify the over-matches the broad regex introduces, so no human writes a reason for them.

    Only shapes that CANNOT be a source citation qualify. Anything a human might plausibly have
    meant as a citation must go through an explicit exemption instead -- the first draft of this
    plan called all 13 `template.js:NNN` tokens false positives, and 12 of them were real, stale
    citations. An automatic rule that guessed there would have shipped that mistake silently.

    Classification is per OCCURRENCE, never per line. `raw_line.find(cite)` answers about the FIRST
    textual occurrence, so on `URL https://schema.json:8443/x; and schema.json:8443 is the port` the
    second, real citation would inherit the first one's URL verdict and be exempted with nobody
    writing anything down. The occurrence carries its own column for that reason.
    """
    if c.get("is_continuation"):
        # A continuation NEVER inherits the URL heuristic, and this is not a suppression -- the branch
        # below tests `"://" in before[-12:]`, which is PROXIMITY, not containment. On
        # a line that cites a file, then mentions an `https://` URL, then continues with a bare token,
        # that perfectly good continuation would be auto-exempted -- and `cmd_check` skips an auto-exempt
        # occurrence unconditionally, so the gate would go green with a real citation neither declared nor
        # exempted nor ever checked again. The cost of the other direction is bounded and LOUD: a bare
        # token sitting inside a URL PATH is opener-legal because of the slash before it, and it now reds
        # until somebody writes an exemption with a reason. No such shape exists in this tree, and a false
        # RED costs one registry line where a false GREEN costs a citation nobody looks at again.
        return None
    col = c.get("col")
    if col is None:
        # A citation split across a line wrap does not appear intact on either raw line, so there
        # is no column to classify. Declining to auto-exempt is the safe direction: it lands in
        # `undeclared`, where a human sees it.
        return None
    before = c["raw_line"][:col]
    if before.endswith("//") and URL_AUTHORITY_RE.match(c["raw_line"], col - 2):
        return "url-authority"
    if "://" in before[-12:]:
        return "url-authority"
    return None


def resolve(target, container, tracked, by_basename, explicit=None):
    """explicit target > path-suffix match > same-plugin basename > repo-unique basename."""
    if explicit:
        return explicit if explicit in tracked else None
    if target is None:
        # An AMBIGUOUS continuation carries no inferred target. `cmd_check` reports it before reaching
        # here, but `cmd_report` -- the command an adjudicator runs precisely to resolve an ambiguity --
        # passes the occurrence's own target straight through, and `t.endswith("/" + None)` below raises
        # `TypeError`. Guarding here covers both callers with one line.
        return None
    if target in tracked:
        return target
    suffix = [t for t in tracked if t.endswith("/" + target)]
    if len(suffix) == 1:
        return suffix[0]
    cands = sorted(set(by_basename.get(os.path.basename(target), [])))
    if len(cands) > 1:
        prefix = container.split("/")[:2]
        same = [c for c in cands if c.split("/")[:2] == prefix]
        if len(same) == 1:
            return same[0]
    if len(cands) == 1:
        return cands[0]
    return None


def _subject_required(text, cite, target):
    """The anchor-eligible code-shaped tokens a subject anchor may be drawn from.

    `text` is deliberately not called a sentence: both callers pass the citation's OWN raw line,
    because a window made the rule fire on symbols from adjacent clauses.

    The citation's OWN lexeme is excluded, and so is its bare filename. Without that exclusion the
    rule fired on every citation and made correct ones impossible to declare: `custom.md:275` says
    "`type` is intentionally open-ended (`manifest.schema.json:18`)", and `manifest.schema.json:18`
    really does say "Deliberately not a fixed enum" -- but its only other code-shaped token is
    `type`, four characters, too short to be an anchor. A gate that a correct citation cannot
    satisfy gets switched off, or pushes the author to delete the line number.
    """
    # `target` rather than `cite.split(":")[0]`, which is the empty string for a continuation --
    # and `str.replace("", " ")` inserts a space between every character, silently shredding the token
    # set rather than raising. For a pathful citation the two are equal by construction, so nothing
    # about that path changes. Without it, a bare occurrence sharing its line with the pathful citation
    # it continues would demand an anchor naming that file's own stem -- a false RED on a correct citation.
    filename = target or ""
    stripped = text.replace(cite, " ")
    for name in (filename, os.path.basename(filename)):
        if name:
            stripped = stripped.replace(name, " ")
    out = set()
    for tok in CODE_TOKEN_RE.findall(stripped):
        tok = tok.rstrip("()")
        if len(tok) >= MIN_ANCHOR_LEN and ("_" in tok or tok.isupper() or not tok.islower()):
            out.add(tok)
    return out


def load_declarations():
    """Merge the four per-scope anchor maps.

    Split four ways (literary-translator / enduser-handbook / internal-skills / repo) because this
    repo routinely has a dozen branches in flight and one shared JSON would collide on every one of
    them. Scope is a filing convention only -- nothing here reads it as authority.
    """
    decls, exempts = {}, {}
    if not os.path.isdir(ANCHOR_DIR):
        return decls, exempts
    for name in sorted(os.listdir(ANCHOR_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(ANCHOR_DIR, name), encoding="utf-8") as fh:
            blob = json.load(fh)
        for key, val in blob.get("declarations", {}).items():
            if key in decls:
                raise SystemExit(f"duplicate declaration key in {name}: {key}")
            decls[key] = val
        for key, val in blob.get("exemptions", {}).items():
            if key in exempts:
                raise SystemExit(f"duplicate exemption key in {name}: {key}")
            # An exemption is the one way to make the gate ignore a citation, and the docstring
            # above calls that "at worst review-visible". It is only review-visible if it carries a
            # reason: `{}` or `null` under the right key silently dismisses a genuinely wrong
            # citation and still counts as used, so the run stays green with nothing to read.
            reason = val.get("reason") if isinstance(val, dict) else None
            if not isinstance(reason, str) or not reason.strip():
                raise SystemExit(
                    f"exemption without a reason in {name}: {key}\n"
                    f"  an exemption must be an object with a non-blank \"reason\" string"
                )
            exempts[key] = val
    return decls, exempts


def decl_key(c):
    """Which citation a declaration describes: the file it is written in, the citation itself, and
    which occurrence of that citation in that file.

    No prose. A key derived from the surrounding text has to answer a question no line window can:
    move when the claim changes, hold still when the paragraph is re-wrapped. Every rule that tries
    lands somewhere on that trade -- a short reach misses the reword, a long one turns an ordinary
    reflow into a red -- and on this corpus a +/-1 core with a three-line walk was truncated by its
    own cap on 190 of 346 citations, so the "claim" it hashed was mostly adjacent text.

    What this costs, and it is the whole cost: the gate no longer notices a reword. It never
    reliably did. What it still guarantees is the thing #579 asked for -- every citation carries an
    adjudicated anchor list, and those anchors are still inside the range it names. The adjudicated
    text is kept beside the declaration as `claim`, printed when that declaration fails, so a
    maintainer can see what was read at the time.

    92 of 346 occurrences share a (container, citation) pair with another, so the ordinal is doing
    real work for a quarter of the corpus. Reordering two such paragraphs swaps their declarations,
    which cannot change the anchor verdict -- both name the same range -- and misattributes only
    which sentence was recorded. Inserting one ABOVE the other still reds the file rather than
    silently inheriting: the last occurrence's ordinal has no declaration.
    """
    parts = [c["container"], c.get("key_cite", c["cite"]), c["ordinal"]]
    if c.get("is_continuation"):
        # A fourth element ONLY for a continuation, so every pathful key stays byte-identical to the 269
        # already in the registry -- and so a bare token attributed to some file can never collide with
        # a pathful citation of that same file and line in the same container.
        parts.append("continuation")
    return json.dumps(parts, ensure_ascii=False)


def cite_display(c):
    """What a problem line calls this occurrence. A bare token says nothing on its own, so a continuation
    prints the attribution the gate inferred -- that inference is the thing an adjudicator has to agree
    with before writing anchors, and it must not have to be reconstructed by hand."""
    if not c.get("is_continuation"):
        return c["cite"]
    if c["target"]:
        return f"{c['cite']} (continuation of {c['target']})"
    return f"{c['cite']} (ambiguous continuation of {' | '.join(c['candidates'])})"


def anchor_span(target_lines, start, end, anchors):
    """Are all anchors inside [start, end], in the declared order? Returns (ok, detail).

    Position is a (line, column) pair, not a line. Advancing only the line index lets two anchors
    declared for the SAME line pass in either textual order, so the declared order would be checked
    for anchors on different lines and unchecked for anchors on one -- and rows with two anchors on
    one target line are ordinary here. Each match resumes the scan just past the previous one.
    """
    window = target_lines[start - 1 : end]
    line_i, col = 0, 0
    for a in anchors:
        for i in range(line_i, len(window)):
            at = window[i].find(a, col if i == line_i else 0)
            if at != -1:
                line_i, col = i, at + len(a)
                break
        else:
            return False, a
    return True, None


def collect(tracked):
    files = {}
    cites = []
    for rel in tracked:
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        files[rel] = text.splitlines()
        cites.extend(find_citations(rel, text))
    return files, cites


def _adjudicated(d, c):
    """What the declaration RECORDED, shown beside a failure when it differs from what is there now.

    This is the consumer that makes `claim` worth storing. Identity no longer reads prose, so a
    declaration can outlive the sentence it was written for; when its anchors then fail, the first
    question is what the adjudicator was actually looking at. Printed only when it has since
    changed, so a routine drift report does not repeat the line above it.
    """
    claim = (d or {}).get("claim")
    if not claim or normalize_sentence(claim) == c["sentence"]:
        return ""
    return f"\n            adjudicated against: {claim[:200]}"


def cmd_check(args):
    tracked = tracked_text_files()
    by_basename = {}
    for t in tracked:
        by_basename.setdefault(os.path.basename(t), []).append(t)
    files, cites = collect(tracked)
    decls, exempts = load_declarations()

    problems = []
    for rel in tracked:
        for n, line in enumerate(files[rel], 1):
            if len(line) > MAX_SCANNED_LINE:
                problems.append(f"LINE-TOO-LONG {rel}:{n} is {len(line)} characters "
                                f"(over {MAX_SCANNED_LINE}); it was NOT scanned for citations")
    used_decls, used_exempts = set(), set()
    examined = 0

    for c in cites:
        key = decl_key(c)
        # An automatically classified non-citation is skipped unconditionally. It used to be
        # skipped only when no declaration claimed its key, so that an explicit declaration could
        # override a misclassification -- an override nothing used (measured: zero declarations
        # attached to an auto-exempt occurrence) and one that opened a SILENT hole once identity
        # became positional. Delete the citation above a `https://schema.json:8443/x` and the URL
        # inherits its ordinal, its declaration, and its anchors; the declaration is marked used, so
        # no STALE-DECLARATION fires and the run returns 0 while a real citation has vanished. Three
        # (container, citation) groups in this repo already mix a real citation with a URL
        # authority, so this is reachable here and not in principle.
        if auto_exempt_reason(c):
            continue
        if key in exempts:
            used_exempts.add(key)
            continue
        examined += 1
        d = decls.get(key)
        if d is None:
            problems.append(f"UNDECLARED {c['container']}:{c['line']} cites {cite_display(c)}\n"
                            f"            key: {key}\n"
                            f"            sentence: {c['sentence'][:160]}")
            continue
        used_decls.add(key)
        if c.get("is_continuation") and c["target"] is None and not d.get("target"):
            problems.append(f"AMBIGUOUS-CONTINUATION {c['container']}:{c['line']} -> {c['cite']}: the "
                            f"window names {c['candidates']!r}, so which file this continues is not the "
                            f"gate's to guess; declare an explicit \"target\", or exempt it with a reason")
            continue
        resolved = resolve(c["target"], c["container"], set(tracked), by_basename, d.get("target"))
        if resolved is None:
            problems.append(f"UNRESOLVABLE {c['container']}:{c['line']} -> {c['target']} "
                            f"(declare an explicit \"target\", or exempt it with a reason)")
            continue
        # `resolve` returns a member of `tracked` or nothing, and `collect` read every one of them,
        # so this is a lookup. Subscripting keeps it one: a resolution rule that ever reached outside
        # the corpus would raise here, rather than quietly read a file the sweep never enumerated.
        tl = files[resolved]
        if c["end"] > len(tl):
            problems.append(f"PAST-EOF {c['container']}:{c['line']} -> {resolved}:{c['start']}-{c['end']} "
                            f"(file has {len(tl)} lines)")
            continue
        anchors = d.get("anchors", [])
        if not anchors:
            problems.append(f"NO-ANCHORS {c['container']}:{c['line']} -> {cite_display(c)}")
            continue
        if c["end"] - c["start"] + 1 >= WIDE_RANGE_LINES and len(anchors) < 2:
            problems.append(f"RANGE-NEEDS-2-ANCHORS {c['container']}:{c['line']} -> {cite_display(c)} "
                            f"(one anchor pins only where a range STARTS)")
            continue
        if len(set(anchors)) != len(anchors):
            problems.append(f"DUPLICATE-ANCHORS {c['container']}:{c['line']} -> {c['cite']} "
                            f"(the same string twice satisfies the two-anchor rule with one pin)")
            continue
        bad = [a for a in anchors if len(a) < MIN_ANCHOR_LEN]
        if bad:
            problems.append(f"ANCHOR-TOO-SHORT {c['container']}:{c['line']} -> {c['cite']}: {bad!r} "
                            f"(min {MIN_ANCHOR_LEN} chars; a common word is not an anchor)")
            continue
        joined = "\n".join(tl)
        loose = [a for a in anchors if joined.count(a) > MAX_ANCHOR_OCCURRENCES]
        if loose:
            problems.append(f"ANCHOR-NOT-DISTINCT {c['container']}:{c['line']} -> {c['cite']}: {loose!r} "
                            f"(occurs >{MAX_ANCHOR_OCCURRENCES}x in {resolved})")
            continue
        ok, missing = anchor_span(tl, c["start"], c["end"], anchors)
        if not ok:
            hint = ""
            for i, line in enumerate(tl, 1):
                if missing in line:
                    hint = f"; anchors last seen at line {i}"
                    break
            problems.append(f"DRIFTED {c['container']}:{c['line']} -> {resolved}:{c['start']}-{c['end']}: "
                            f"anchor {missing!r} is not in the cited range{hint}"
                            + _adjudicated(d, c))
            continue
        subjects = _subject_required(c["raw_line"], c["cite"], c["target"])
        if subjects and not any(any(s in a for a in anchors) for s in subjects):
            in_range = {s for s in subjects if any(s in line for line in tl[c["start"] - 1 : c["end"]])}
            if in_range:
                problems.append(
                    f"NO-SUBJECT-ANCHOR {c['container']}:{c['line']} -> {c['cite']}: the sentence is "
                    f"about {sorted(in_range)!r} but no anchor names it; true-but-irrelevant anchors "
                    f"can pin the wrong range" + _adjudicated(d, c))

    for key in set(decls) - used_decls:
        problems.append(f"STALE-DECLARATION no live citation matches: {key}")
    for key in set(exempts) - used_exempts:
        problems.append(f"STALE-EXEMPTION no live citation matches: {key}")

    continuations = sum(1 for c in cites if c.get("is_continuation"))
    print(f"citation-audit: {len(cites)} citation occurrences in {len(tracked)} tracked text files "
          f"({continuations} of them bare continuations); "
          f"{examined} required a declaration; {len(used_exempts)} exempted.")
    if examined == 0:
        # Appended rather than returned on: problems found before this point are real and a reader
        # needs both. Returning here printed the refusal INSTEAD of a stale declaration that had
        # just been detected, which reads as "nothing else was wrong".
        problems.append("REFUSING: zero citations examined -- a sweep that runs zero times prints "
                        "exactly what a passing one prints.")
    if problems:
        print(f"\n{len(problems)} problem(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_report(args):
    """Emit one adjudication packet per citation: the claim, the cited neighbourhood, and where the
    sentence's own subject tokens actually live in the target. This is what a model reads. It never
    runs in CI -- GitHub Actions holds no key, and the judgment is the part a script cannot do."""
    tracked = tracked_text_files()
    by_basename = {}
    for t in tracked:
        by_basename.setdefault(os.path.basename(t), []).append(t)
    files, cites = collect(tracked)
    decls, _ = load_declarations()
    packets = []
    for c in cites:
        if args.scope and not c["container"].startswith(args.scope):
            continue
        if auto_exempt_reason(c):
            continue
        d = decls.get(decl_key(c), {})
        resolved = resolve(c["target"], c["container"], set(tracked), by_basename, d.get("target"))
        p = dict(c)
        p["resolved"] = resolved
        p["declared"] = bool(d)
        # Computed even when the target is unresolved, and that is the case worth serving: an
        # AMBIGUOUS continuation carries no target at all, and this packet is exactly what an
        # adjudicator reads to decide which of the named files it continues. Emitting nothing there
        # would leave the one occurrence that needs a human with the least to go on -- and it is the
        # only path on which `target` is None, so it is also what keeps `_subject_required`'s
        # empty-name branch honest rather than defensive.
        p["subject_tokens"] = sorted(_subject_required(c["raw_line"], c["cite"], c["target"]))
        if resolved:
            tl = files[resolved]
            lo, hi = max(1, c["start"] - 2), min(len(tl), c["end"] + 2)
            p["target_lines"] = len(tl)
            p["cited"] = [{"n": n, "text": tl[n - 1]} for n in range(lo, hi + 1)]
            # The same input the GATE reads, not the wider neighbourhood. A packet that offered
            # subjects drawn from an adjacent clause would point an adjudicator at target lines the
            # gate will never ask an anchor to name -- measured at 76 of 82 when the gate itself
            # read a window.
            p["subject_locations"] = {
                s: [n for n, line in enumerate(tl, 1) if s in line][:6] for s in p["subject_tokens"]
            }
        packets.append(p)
    if args.json:
        json.dump(packets, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for p in packets:
            print(f"\n===== {p['container']}:{p['line']}  cites  {cite_display(p)}  -> {p['resolved']}")
            print(f"  CLAIM: {p['sentence'][:400]}")
            if p.get("cited"):
                for row in p["cited"]:
                    mark = ">" if p["start"] <= row["n"] <= p["end"] else " "
                    print(f"  {mark}{row['n']}: {row['text'][:200]}")
                if p["subject_locations"]:
                    print(f"  SUBJECTS: {p['subject_locations']}")
            else:
                print("  UNRESOLVED TARGET -- needs an explicit \"target\" or an exemption")
    print(f"\n{len(packets)} packet(s).", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("check", help="the CI gate: every citation declared, anchors in range and in order")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("report", help="emit adjudication packets for a model or a human to judge")
    r.add_argument("--scope", help="restrict to containers under this path prefix")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
