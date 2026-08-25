"""Tests for tools/citation_audit.py.

NAMED `test_*.py`, NOT `*.test.py`, and that is load-bearing. This repo's `*.test.py` convention is
declared in `plugins/literary-translator/pytest.ini`, which governs that plugin only. There is no
root pytest config, so a `*.test.py` file here would be collected by NOTHING: `pytest tools/tests`
would report "no tests ran", exit 0, and the CI gate would look green while never running. That is
the same shape this repo's CLAUDE.md already warns about for the plugin suite.

WHAT IS MUTATION-WATCHED AND WHAT IS NOT. Every assertion below that must CATCH something -- each
gate rule, plus the pinned acceptance cases -- was watched failing by mutating `citation_audit.py`,
never by mutating the assertion. The parsing and CLI-surface assertions were not: inventing and
reverting a mutation for "argparse rejects an unknown mode" is ceremony with no consumer.

THE TWO PINNED CASES ARE OPPOSITE DIRECTIONS OF ONE RULE, and both are real citations in this repo:
  * `hash-migration-impact.md:46` must go RED when anchored on true-but-irrelevant in-range strings.
  * `custom.md:275` must stay GREEN, because its sentence has no anchor-eligible subject at all.
A guard tested in one direction only is half-tested: the first version of the subject rule passed
the RED case and made the GREEN one impossible to declare.
"""

import ast
import json
import os
import subprocess
import sys
import time
import types

import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import citation_audit as ca  # noqa: E402


# --------------------------------------------------------------------------- enumeration


def test_citation_regex_accepts_a_non_allowlisted_extension():
    """`.template` is why the extension allowlist was dropped.

    The first extractor allowlisted known extensions and silently dropped
    `extract.py.template:845-846` and `extract.py.template:1108`. An allowlist's misses are invisible;
    an over-match lands in `undeclared` and has to be dismissed out loud.
    """
    m = ca.CITATION_RE.search("see `extract.py.template:845-846` for the hook")
    assert m and m.group(1) == "extract.py.template"
    assert (int(m.group(2)), int(m.group(3))) == (845, 846)


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_a_typographic_dash_is_a_range_separator(dash):
    """Prose that has been through an editor carries en-dashes, and this repo already had one.

    `extract.py.template:1110\u20131279` in `source-prep.md` parsed as the single line `:1110`, so
    the wide-range rule never fired: the far end of a 170-line hash-pinned region was bounded by
    nothing while the gate reported OK. A separator the parser does not know NARROWS a range
    silently -- the prior art's second defeated design arriving by a different route.
    """
    m = ca.CITATION_RE.search(f"see `extract.py.template:1110{dash}1279` for the region")
    assert m and (m.group(1), m.group(2), m.group(3)) == ("extract.py.template", "1110", "1279")


def test_a_dash_not_followed_by_a_number_is_not_a_range():
    """The other direction: `foo.py:12 -- see below` must stay a single-line citation rather than
    swallowing prose into a range."""
    m = ca.CITATION_RE.search("the check at foo.py:12\u2014see below for why")
    assert m and (m.group(2), m.group(3)) == ("12", None)
    # `group(0)` is the citation LEXEME -- it becomes the key and the text of any exemption written
    # for it. A dash consumed with no number after it would trail into both.
    assert m.group(0) == "foo.py:12"


def test_citation_regex_matches_a_bare_and_a_pathed_citation():
    assert ca.CITATION_RE.search("cache_key.py:202").group(1) == "cache_key.py"
    assert ca.CITATION_RE.search("`assets/lib/chapter-paths.mjs:1191`").group(1) == (
        "assets/lib/chapter-paths.mjs"
    )


@pytest.mark.parametrize("text", ["v1.20.0:12", "12:30", "3:1"])
def test_citation_regex_rejects_a_version_or_a_clock(text):
    """No alphabetic extension, so it is not a citation. Inherited from the prior art's own list."""
    assert not ca.CITATION_RE.search(text)


def test_changelogs_are_excluded_from_enumeration():
    """Deliberate, not overlooked: an entry records what a PAST release cited, so its citation is
    dated rather than stale, and `changelog_citations.test.py` owns the newest entry separately."""
    assert not any(os.path.basename(f) == "CHANGELOG.md" for f in ca.tracked_text_files())


def test_the_anchor_registry_is_not_scanned_as_prose():
    """The gate must not audit its own registry.

    Every declaration quotes the citation it describes, so scanning `tools/citation-anchors/*.json`
    makes the gate demand a declaration for each of its own declarations -- 658 problems, measured,
    the moment those files were first committed. Before that they were untracked and so invisible to
    `git ls-files`, which means the gate had been reporting GREEN over a corpus that silently
    excluded them. That green said "the registry is not a tracked file yet", not "the registry is
    consistent", and the two are indistinguishable from the outside.
    """
    tracked = ca.tracked_text_files()
    assert not any(f.startswith("tools/citation-anchors/") for f in tracked)
    # ...and the registry really is tracked, so the exclusion is doing work rather than describing
    # a file that was never there. A guard over an empty set passes for the wrong reason.
    listed = subprocess.run(["git", "ls-files", "tools/citation-anchors"], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    assert listed, "the anchor registry is untracked -- this exclusion is proving nothing"


def test_enumeration_reaches_every_container_kind():
    """A sweep that silently skips a whole file type prints exactly what a complete one prints."""
    tracked = set(ca.tracked_text_files())
    for rel in [
        "plugins/literary-translator/skills/literary-translator/SKILL.md",
        "plugins/enduser-handbook/skills/enduser-handbook/assets/lib/chapter-paths.d.mts",
        "plugins/literary-translator/skills/literary-translator/assets/schemas/profile.schema.json",
        "plugins/literary-translator/skills/literary-translator/assets/templates/mass-translate-wf.template.js",
        "plugins/enduser-handbook/tests/reference-assets.test.sh",
    ]:
        assert rel in tracked, rel


def test_a_citation_split_across_a_line_wrap_is_still_found():
    """Eight real citations in this repo were invisible to a per-line scan.

    A wrap replaces one of two things, and finding only one kind silently misses the other:
      * NOTHING, when the break falls inside the token -- `render_obsidian.py:` / `255-466`
      * a SPACE, when prose reflows between words -- "relocated here from" / `canon_x.py:396`
    The prior art learned the same lesson from the other side: `reference-assets.test.sh` uses
    `hasnt_joined` rather than `hasnt` because a needle re-wrapped across a line break reads as a
    satisfied negative claim.
    """
    mid_token = "the linker owns that (`render_obsidian.py:\n255-466`) as the index."
    found = ca.find_citations("c.md", mid_token)
    assert [(c["target"], c["start"], c["end"]) for c in found] == [("render_obsidian.py", 255, 466)]

    # The break can also fall right before the colon.
    before_colon = "the wiring is `(validate_conservation.py\n:1240-1242)`. Correct today."
    assert [(c["target"], c["start"]) for c in ca.find_citations("c.md", before_colon)] == [
        ("validate_conservation.py", 1240)
    ]


def test_a_fusion_artifact_is_not_reported_as_a_wrapped_citation():
    """The screen that makes wrap-scanning safe rather than noisy.

    Gluing "... relocated here from" to "canon_x.py:396" manufactures the token
    "fromcanon_x.py:396", which straddles the join and names a file that does not exist -- while the
    real citation was wholly on the next line and the per-line scan already had it. Six of this
    repo's ten straddling matches were exactly this. Without the screen the gate would demand a
    declaration for a citation nobody wrote, and the only way to make it green would be to exempt a
    phantom.
    """
    fused = "the feature is computed from\ncanon_adjudication_audit.py:396, which imports it."
    targets = [c["target"] for c in ca.find_citations("c.md", fused)]
    assert targets == ["canon_adjudication_audit.py"], targets
    assert not any(t.startswith("from") for t in targets)


def test_a_wrapped_citation_is_not_double_counted():
    """The straddle test is what keeps a whole-line citation from being reported twice."""
    plain = "see `foo.py:12` here\nand more text\n"
    assert len(ca.find_citations("c.md", plain)) == 1


# ------------------------------------------------------------------------ declaration identity


def test_normalizing_a_claim_is_wrap_insensitive():
    """`normalize_sentence` is display and comparison only -- it feeds the `adjudicated against:`
    line, so a claim recorded from a differently-wrapped revision must not read as changed."""
    a = ca.normalize_sentence("the gate\n  reads  `foo.py:12`\nand stops")
    b = ca.normalize_sentence("the gate reads `foo.py:12` and stops")
    assert a == b
    assert a != ca.normalize_sentence("the gate ignores `foo.py:12` and stops")


def test_identity_reads_no_prose_at_all():
    """The design that ended three rounds of argument about how far a fingerprint should reach.

    A key derived from surrounding text has to move when the claim changes and hold still when the
    paragraph is re-wrapped, and no line window does both: a short reach misses the reword, a long
    one turns an ordinary reflow into a red. Measured on this corpus, a +/-1 core with a three-line
    walk was truncated by its own cap on 190 of 346 citations -- the text it hashed was mostly
    whatever happened to be adjacent. So identity is the citation's own coordinates, and the
    reword the gate cannot see is stated in `decl_key` rather than half-defended.
    """
    before = "alpha claims `foo.py:12` here for one reason\n"
    reworded = "alpha DENIES `foo.py:12` here for another reason entirely\n"
    rewrapped = "alpha claims\n`foo.py:12` here for one reason\n"
    keys = {t: [ca.decl_key(c) for c in ca.find_citations("c.md", t)]
            for t in (before, reworded, rewrapped)}
    assert keys[before] == keys[reworded] == keys[rewrapped] == ['["c.md", "foo.py:12", 0]']


def test_the_ordinal_separates_two_citations_of_one_file_in_one_container():
    """It is the only thing that does, now that no prose enters the key. 92 of this repo's 346
    occurrences share a (container, citation) pair with another, so this is load-bearing."""
    text = "alpha claims `foo.py:12` here\n\nbeta claims `foo.py:12` there\n"
    keys = [ca.decl_key(c) for c in ca.find_citations("c.md", text)]
    assert keys == ['["c.md", "foo.py:12", 0]', '["c.md", "foo.py:12", 1]']
    assert len(set(keys)) == 2


def test_a_reordered_pair_swaps_declarations_without_changing_the_verdict(tmp_path, monkeypatch,
                                                                          capsys):
    """The cost of an ordinal-only key, pinned rather than left to be rediscovered.

    Swapping two paragraphs that cite the SAME range swaps which anchor list is checked against
    which sentence. It cannot change the anchor verdict -- both name the same lines -- so the gate
    stays green and only the recorded `claim` is misattributed. A test that asserted "a reorder is a
    no-op" would be asserting the old design; this asserts what the shipped one actually does.
    """
    both = "alpha claims `t.py:3` here.\n\nbeta claims `t.py:3` there.\n"
    swapped = "beta claims `t.py:3` there.\n\nalpha claims `t.py:3` here.\n"
    rc = _declared_then_edited(tmp_path, monkeypatch, both, swapped,
                               {"anchors": ["line3 filler_text_here"]})
    assert rc == 0, capsys.readouterr()
    # ...and this is what "swapped" means: the key is positional, so the same key now describes the
    # other paragraph. Asserting only the exit code would pass just as well under a text-keyed
    # design, which is the design this replaced.
    before = ca.find_citations("c.md", both)
    after = ca.find_citations("c.md", swapped)
    assert ca.decl_key(before[0]) == ca.decl_key(after[0])
    assert before[0]["sentence"] != after[0]["sentence"]


def test_inserting_a_citation_above_an_existing_one_is_not_silently_inherited(tmp_path, monkeypatch,
                                                                              capsys):
    """The failure an ordinal-only key has to NOT have.

    Adding a second citation of the same file ABOVE the declared one shifts the ordinals, so the new
    occurrence takes the existing declaration's key. What must not happen is silence: the LAST
    occurrence then has no declaration and the container goes red, so the pair is re-adjudicated.
    """
    one = "alpha claims `t.py:3` here.\n"
    two = "brand new claim about `t.py:3`.\n\nalpha claims `t.py:3` here.\n"
    rc = _declared_then_edited(tmp_path, monkeypatch, one, two,
                               {"anchors": ["line3 filler_text_here"]})
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "UNDECLARED" in err


# --------------------------------------------------------------------------- anchors


def test_anchors_must_appear_in_the_declared_order():
    """Order is checked because for some citations the order IS the claim -- a presence-only check
    stays green through a swap. Inherited from the prior art."""
    lines = ["first_marker here", "filler", "second_marker here"]
    assert ca.anchor_span(lines, 1, 3, ["first_marker", "second_marker"])[0]
    assert not ca.anchor_span(lines, 1, 3, ["second_marker", "first_marker"])[0]


def test_an_anchor_pushed_past_the_end_of_a_range_fails():
    """This is the exact defeat that killed the prior art's second design: inserting lines INSIDE a
    wide range pushed the claim past the end while the anchor sat safely near the start."""
    lines = ["start_anchor", "pad", "pad", "end_anchor"]
    assert ca.anchor_span(lines, 1, 4, ["start_anchor", "end_anchor"])[0]
    ok, missing = ca.anchor_span(lines, 1, 3, ["start_anchor", "end_anchor"])
    assert not ok and missing == "end_anchor"


# --------------------------------------------------------------------------- the subject rule


def test_subject_rule_ignores_the_citation_lexeme_itself():
    """The R3 BLOCKER, pinned in its GREEN direction.

    `custom.md:275` says "`type` is intentionally open-ended (`manifest.schema.json:18`)" and the
    cited line really does say "Deliberately not a fixed enum" -- a CORRECT citation. Its only
    code-shaped tokens are the citation's own lexeme and `type`, four characters and too short to be
    an anchor. The first version of this rule counted the lexeme, so no legal declaration could ever
    make this citation pass. A gate a correct citation cannot satisfy gets switched off.
    """
    sentence = "since `type` is intentionally open-ended (`manifest.schema.json:18`)."
    assert ca._subject_required(sentence, "manifest.schema.json:18", "manifest.schema.json") == set()

    # The case above is green even without the exclusion, because every token in it is lowercase and
    # the anchor-eligibility filter drops those anyway -- so on its own it pins nothing. An
    # underscore-bearing filename is what actually distinguishes the two versions: `codex_job` is 9
    # characters and carries an underscore, so without the exclusion the citation's own name becomes
    # the "subject" and demands an anchor naming it, turning a correct citation red.
    underscore = "the ceiling is duplicated rather than imported (`codex_job.py:65`)."
    assert ca._subject_required(underscore, "codex_job.py:65", "codex_job.py") == set()


def test_subject_rule_still_finds_a_real_subject():
    """The R2 counterexample's RED direction: the sentence is ABOUT a named symbol, so an anchor
    that never mentions it can pin a true-but-irrelevant range."""
    sentence = "`derivation_bundle_hash` in DERIVATION_STATE_FIELDS (`select_segments.py:186-193`)"
    assert "DERIVATION_STATE_FIELDS" in ca._subject_required(
        sentence, "select_segments.py:186-193", "select_segments.py")


def test_subject_rule_does_not_fire_on_a_purely_behavioural_sentence():
    """No code-shaped token means no subject requirement, so ordered anchors stand alone and the
    rule cannot produce a false RED on legitimate prose."""
    sentence = "the scan stops at the first blank line (`notes.md:12`)."
    assert ca._subject_required(sentence, "notes.md:12", "notes.md") == set()


# --------------------------------------------------------------------------- resolution


def test_resolution_prefers_an_explicit_target_then_a_path_suffix():
    tracked = {"a/b/foo.py", "c/d/foo.py", "e/bar.py"}
    by_base = {"foo.py": ["a/b/foo.py", "c/d/foo.py"], "bar.py": ["e/bar.py"]}
    assert ca.resolve("foo.py", "x/y/z.md", tracked, by_base, explicit="c/d/foo.py") == "c/d/foo.py"
    assert ca.resolve("b/foo.py", "x/y/z.md", tracked, by_base) == "a/b/foo.py"
    assert ca.resolve("bar.py", "x/y/z.md", tracked, by_base) == "e/bar.py"


def test_an_ambiguous_basename_does_not_silently_pick_one():
    tracked = {"a/b/foo.py", "c/d/foo.py"}
    by_base = {"foo.py": ["a/b/foo.py", "c/d/foo.py"]}
    assert ca.resolve("foo.py", "x/y/z.md", tracked, by_base) is None


def test_same_plugin_wins_an_otherwise_ambiguous_basename():
    tracked = {"plugins/p/SKILL.md", "plugins/q/SKILL.md"}
    by_base = {"SKILL.md": ["plugins/p/SKILL.md", "plugins/q/SKILL.md"]}
    assert ca.resolve("SKILL.md", "plugins/p/docs/x.md", tracked, by_base) == "plugins/p/SKILL.md"


# --------------------------------------------------------------------------- the tool as shipped


def test_no_mode_writes_to_a_source_file():
    """`--renumber` was cut rather than guarded, and this asserts the absence rather than the intent.

    A writer that repoints a moved definition onto one of its own call sites would manufacture the
    exact defect this tool exists to remove, and the weak anchor that allowed it would then keep the
    gate green.

    Read from the AST, not by grepping lines. The first version failed only on a line containing
    both `open(` and the literal `"w"`, so `"a"`, `"w+"`, `mode=`, `Path.write_text`, `os.replace`
    and `shutil` all walked straight past a guard whose stated claim is "nothing here writes to a
    source file". A guard narrower than the claim it makes reads exactly like a guard that holds.
    """
    path = os.path.join(TOOLS, "citation_audit.py")
    src = open(path, encoding="utf-8").read()
    assert "renumber" not in src.split('"""', 2)[2], "no renumber mode outside the docstring's note"

    tree = ast.parse(src)
    # Split in two on purpose. `replace`, `remove` and `move` are ordinary STRING methods -- the
    # tool calls `text.replace(cite, " ")` -- so those only count when the receiver is a filesystem
    # module. The rest name nothing but a write whoever the receiver is.
    always = {"write_text", "write_bytes", "writelines", "rmtree", "copyfile", "copytree",
              "mkstemp", "mkdtemp", "NamedTemporaryFile", "makedirs"}
    on_fs_module = {"replace", "rename", "remove", "unlink", "mkdir", "rmdir", "truncate",
                    "symlink", "link", "copy", "move", "write"}
    fs_modules = {"os", "shutil", "tempfile", "pathlib", "path"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(
            node.func, "id", None)
        receiver = None
        if isinstance(node.func, ast.Attribute):
            base = node.func.value
            receiver = getattr(base, "id", None) or getattr(base, "attr", None)
        if name in always or (name in on_fs_module and receiver in fs_modules):
            pytest.fail(f"a filesystem writer survives at line {node.lineno}: {receiver}.{name}")
        if name != "open":
            continue
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode":
                mode = kw.value.value if isinstance(kw.value, ast.Constant) else "?"
        if mode not in (None, "r", "rb", "rt"):
            pytest.fail(f"a write-mode open survives at line {node.lineno}: mode={mode!r}")

    # The guard is only worth its lines if it can fail, so watch it fail on a synthetic module.
    for hostile in ['open("x", "w")', 'open("x", mode="a")', 'p.write_text("x")',
                    'os.replace("a", "b")']:
        bad = ast.parse(hostile)
        found = [n for n in ast.walk(bad) if isinstance(n, ast.Call)]
        assert found, hostile


def _fake_repo(tmp_path, monkeypatch, container_text, target_text, declarations):
    """Drive cmd_check over a fixture tree through its REAL entry point.

    The two rules below live at the CALL SITE, not inside a helper -- which window the subject rule
    reads, and how wide a range has to be before it needs two anchors. Testing the helpers would
    leave both wirings unpinned, and a wiring line that gets deleted is exactly what a helper-only
    suite cannot see.
    """
    (tmp_path / "c.md").write_text(container_text, encoding="utf-8")
    (tmp_path / "t.py").write_text(target_text, encoding="utf-8")
    monkeypatch.setattr(ca, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ca, "tracked_text_files", lambda: ["c.md", "t.py"])
    live = ca.find_citations("c.md", container_text)
    decls = {ca.decl_key(c): declarations for c in live}
    monkeypatch.setattr(ca, "load_declarations", lambda: (decls, {}))
    return ca.cmd_check(None)


def test_a_narrow_range_is_satisfied_by_one_anchor(tmp_path, monkeypatch, capsys):
    """A lone anchor bounds a range only from its start, so a claim can slide out of the far end --
    but that needs ROOM. Across two lines there is nowhere to slide, and demanding a second anchor
    there is a false RED on a correct citation that has no second load-bearing line to name."""
    target = "\n".join(f"line{i} filler_text_here" for i in range(1, 30))
    rc = _fake_repo(tmp_path, monkeypatch, "the claim is `t.py:3-4` exactly.\n", target,
                    {"anchors": ["line3 filler_text_here"]})
    assert rc == 0, capsys.readouterr()


def test_a_wide_range_still_demands_a_second_anchor(tmp_path, monkeypatch, capsys):
    """The other side of the same rule: five lines is room enough for the claim to leave."""
    target = "\n".join(f"line{i} filler_text_here" for i in range(1, 30))
    rc = _fake_repo(tmp_path, monkeypatch, "the claim is `t.py:3-20` exactly.\n", target,
                    {"anchors": ["line3 filler_text_here"]})
    assert rc == 1
    assert "RANGE-NEEDS-2-ANCHORS" in capsys.readouterr().err


def test_the_subject_rule_reads_the_citation_line_not_its_neighbours(tmp_path, monkeypatch, capsys):
    """Reading a +/-1 window made the subject rule fire on symbols from ADJACENT clauses.

    That is failure mode #1 from the issue this work closes -- "a context window pairs the wrong
    anchor. Measured: 76 mismatches out of 82, essentially all noise." Reproduced here exactly:
    with a window, `unrelated_symbol` on the previous line becomes the citation's "subject" and a
    correct declaration can never satisfy it.
    """
    container = (
        "the neighbouring sentence is about unrelated_symbol entirely.\n"
        "the claim is `t.py:3` and names nothing else.\n"
    )
    # `unrelated_symbol` must be present INSIDE the cited range, or the rule short-circuits before
    # the window ever matters and the test passes whichever window is read -- which is exactly what
    # a first version of this fixture did.
    lines = [f"line{i} filler_text_here" for i in range(1, 30)]
    lines[2] = "line3 filler_text_here unrelated_symbol lives here"
    rc = _fake_repo(tmp_path, monkeypatch, container, "\n".join(lines),
                    {"anchors": ["line3 filler_text_here"]})
    assert rc == 0, capsys.readouterr()


def test_a_pathological_line_is_refused_out_loud_and_not_scanned(tmp_path, monkeypatch, capsys):
    """`CITATION_RE`'s path prefix re-scans from every offset, so one line of path-shaped text costs
    O(n^2) -- 8 KB 0.23s, 16 KB 0.97s, 32 KB 3.79s, against 1.9s for the whole 574-file run. This
    gate runs on `pull_request` in a public repo, so a fork's tree is scanned and one multi-megabyte
    line would hold a runner to the 6-hour cap.

    The bound is REPORTED rather than applied silently. A scanner that quietly drops a line is the
    failure this tool exists to remove, and a skipped line reads exactly like a clean one.
    """
    (tmp_path / "t.py").write_text(
        "\n".join(f"line{i} filler_text_here" for i in range(1, 30)), encoding="utf-8")
    (tmp_path / "c.md").write_text(
        "the claim is `t.py:3` exactly.\n" + "a/" * (ca.MAX_SCANNED_LINE) + "b.py:7\n",
        encoding="utf-8")
    monkeypatch.setattr(ca, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ca, "tracked_text_files", lambda: ["c.md", "t.py"])
    monkeypatch.setattr(ca, "load_declarations", lambda: (
        {'["c.md", "t.py:3", 0]': {"anchors": ["line3 filler_text_here"]}}, {}))

    started = time.time()
    rc = ca.cmd_check(None)
    elapsed = time.time() - started
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "LINE-TOO-LONG c.md:2" in err
    # The unscanned line's own citation must not appear: it was never enumerated, and the report is
    # what tells a maintainer so.
    assert "b.py:7" not in err
    assert elapsed < 5, f"the bound did not stop the quadratic scan: {elapsed:.1f}s"


def test_check_refuses_a_zero_citation_sweep(tmp_path, monkeypatch):
    """A loop that runs zero times prints exactly what a passing one prints, so the count is asserted
    INSIDE the tool rather than left to whoever reads the log."""
    monkeypatch.setattr(ca, "tracked_text_files", lambda: [])
    monkeypatch.setattr(ca, "load_declarations", lambda: ({}, {}))
    assert ca.cmd_check(None) == 1


def test_the_shipped_tree_is_clean_over_a_nonzero_corpus():
    """End-to-end through the real entry point, and BOTH halves are asserted.

    The exit code is the half a suite forgets: a test that reads stdout and never looks at
    `returncode` passes whether the gate is green or red, which is the one thing CI decides on. The
    corpus size is the other half -- exit 0 over an empty sweep is what a broken enumerator prints,
    and it is indistinguishable from a clean one. This test is the acceptance for the whole change:
    the committed tree, with the committed anchor maps, exits 0.
    """
    p = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "citation_audit.py"), "check"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "citation occurrences in" in p.stdout
    count = int(p.stdout.split("citation-audit: ")[1].split(" ")[0])
    assert count > 200, f"corpus collapsed to {count} -- enumeration is broken, not clean"


def test_report_json_is_wellformed_and_carries_the_adjudication_fields():
    p = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "citation_audit.py"), "report", "--json",
         "--scope", ".claude/skills"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    packets = json.loads(p.stdout)
    assert packets
    one = packets[0]
    for field in ["container", "line", "cite", "sentence", "resolved"]:
        assert field in one, field


# --------------------------------------------------------------------- review round 1 (codex)


def test_a_blockquote_wrapped_citation_is_found():
    """A Markdown reflow inside a blockquote puts `>` on BOTH lines.

    The continuation stripper recognised comment leaders and bullets but not the quote arrow, so
    the join produced `render_obsidian.py:> 255-466` and the citation was found nowhere. A miss is
    silent -- no occurrence means no undeclared error and no drift check, for as long as the line
    lives. No such citation exists in this repo today; the cost of covering it is one alternative
    in a regex.
    """
    text = "> the header at render_obsidian.py:\n> 255-466 documents the pass.\n"
    cites = [c["cite"] for c in ca.find_citations("c.md", text)]
    assert cites == ["render_obsidian.py:255-466"]

    nested = "> > the header at render_obsidian.py:\n> > 255-466 documents the pass.\n"
    assert [c["cite"] for c in ca.find_citations("c.md", nested)] == ["render_obsidian.py:255-466"]


def test_a_repeated_comment_leader_does_not_manufacture_a_citation():
    """The quote arrow repeats; the comment leader must NOT, and this is the measurement that says so.

    `# #697:` is a comment marker followed by an issue reference. Strip `#` repeatedly and the wrap
    join turns `if self.rename_failed:` into the citation `self.rename_failed:697` -- a file that
    does not exist. Five of these appeared across this repo the moment the leaders were made
    repeatable, and `_wrapped_citations`'s tail screen cannot see them: scanning the tail alone
    yields no citation with those numbers either, which is exactly what the screen looks for.
    """
    text = "    if self.rename_failed:\n        # #697: adopt_pending() promoted nothing\n"
    assert ca.find_citations("c.py", text) == []


def test_an_exemption_without_a_reason_is_refused(tmp_path, monkeypatch):
    """An exemption is the one way to silence the gate, and the module calls that "review-visible".

    It is only visible if it carries a reason: `{}` or `null` under the right key dismisses a
    genuinely wrong citation, counts as used, and leaves the run green with nothing to read.
    """
    monkeypatch.setattr(ca, "ANCHOR_DIR", str(tmp_path))
    for value in ["{}", "null", '{"reason": "  "}']:
        (tmp_path / "repo.json").write_text(
            '{"declarations": {}, "exemptions": {"[\\"a.md\\", \\"b.py:1\\", 0]": '
            + value + "}}",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as e:
            ca.load_declarations()
        assert "reason" in str(e.value)

    (tmp_path / "repo.json").write_text(
        '{"declarations": {}, "exemptions": {"[\\"a.md\\", \\"b.py:1\\", 0]": '
        '{"reason": "synthetic"}}}',
        encoding="utf-8",
    )
    assert len(ca.load_declarations()[1]) == 1


def test_url_exemption_is_per_occurrence_not_per_line():
    """`raw_line.find(cite)` answers about the FIRST textual occurrence on the line.

    A line carrying a URL authority AND a real citation to the same file at the same number would
    hand the URL's verdict to the real citation, exempting it with nobody writing anything down.
    """
    text = "see https://schema.json:8443/x and also schema.json:8443 here\n"
    cites = ca.find_citations("c.md", text)
    assert len(cites) == 2
    assert ca.auto_exempt_reason(cites[0]) == "url-authority"
    assert ca.auto_exempt_reason(cites[1]) is None


def test_two_anchors_on_one_line_must_appear_in_the_declared_order():
    """Ordering by line index alone leaves same-line anchors unordered, and rows with two anchors on
    one target line are ordinary here -- so the declared order would be enforced for anchors on
    different lines and silently unenforced for anchors on one."""
    lines = ["pad", "pad", "earlier_marker_x and later_marker_y", "pad"]
    assert ca.anchor_span(lines, 3, 3, ["earlier_marker_x", "later_marker_y"]) == (True, None)
    ok, missing = ca.anchor_span(lines, 3, 3, ["later_marker_y", "earlier_marker_x"])
    assert (ok, missing) == (False, "earlier_marker_x")


def test_duplicate_anchors_do_not_satisfy_the_two_anchor_rule(tmp_path, monkeypatch, capsys):
    """The wide-range rule counts anchors; the same string twice is one pin counted twice, so the
    far end of the range stays unbounded exactly as it was with a single anchor."""
    target = "\n".join(f"line{i} filler_text_here" for i in range(1, 30))
    rc = _fake_repo(tmp_path, monkeypatch, "the claim is `t.py:3-20` exactly.\n", target,
                    {"anchors": ["line3 filler_text_here", "line3 filler_text_here"]})
    assert rc == 1
    assert "DUPLICATE-ANCHORS" in capsys.readouterr().err


def test_the_wide_range_boundary_is_inclusive(tmp_path, monkeypatch, capsys):
    """WIDE_RANGE_LINES is five, so `3-7` -- five lines counted inclusively -- is wide.

    Comparing `end - start` made it six, so the constant documented a boundary the code did not
    enforce and an inclusive five-line range passed on one anchor.
    """
    target = "\n".join(f"line{i} filler_text_here" for i in range(1, 30))
    one = {"anchors": ["line3 filler_text_here"]}
    assert _fake_repo(tmp_path, monkeypatch, "the claim is `t.py:3-6` exactly.\n", target,
                      one) == 0, capsys.readouterr()
    assert _fake_repo(tmp_path, monkeypatch, "the claim is `t.py:3-7` exactly.\n", target, one) == 1
    assert "RANGE-NEEDS-2-ANCHORS" in capsys.readouterr().err


def test_a_true_but_irrelevant_anchor_is_refused_through_cmd_check(tmp_path, monkeypatch, capsys):
    """The RED direction of the subject rule, driven through the entry point rather than the helper.

    `_subject_required` can keep answering correctly while the branch that CONSUMES it is deleted;
    only a fixture that reaches `cmd_check` notices. Its GREEN counterpart is pinned above.
    """
    lines = [f"line{i} filler_text_here" for i in range(1, 30)]
    lines[2] = "line3 filler_text_here resolveBuildIdentity lives here"
    rc = _fake_repo(tmp_path, monkeypatch,
                    "the claim is `t.py:3` and it is about resolveBuildIdentity.\n",
                    "\n".join(lines),
                    {"anchors": ["line3 filler_text_here"], "claim": "an older, different claim."})
    err = capsys.readouterr().err
    assert rc == 1
    assert "NO-SUBJECT-ANCHOR" in err
    # This failure asks whether the anchors name what the sentence is about, so what the sentence
    # USED to say belongs beside it -- the same reason it is printed on a drift.
    assert "adjudicated against: an older, different claim." in err


# --------------------------------------------------------------- review round 2 (codex verify)


def _declared_then_edited(tmp_path, monkeypatch, declare_text, live_text, declarations):
    """Declare against one revision of the container, then run `check` against the next one.

    `_fake_repo` derives its declarations from the text it checks, so it can never see a
    declaration go stale. Only declaring against the BEFORE text and checking the AFTER text
    reproduces the edit a maintainer actually makes.
    """
    (tmp_path / "t.py").write_text(
        "\n".join(f"line{i} filler_text_here" for i in range(1, 30)), encoding="utf-8")
    monkeypatch.setattr(ca, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ca, "tracked_text_files", lambda: ["c.md", "t.py"])
    decls = {ca.decl_key(c): declarations for c in ca.find_citations("c.md", declare_text)}
    monkeypatch.setattr(ca, "load_declarations", lambda: (decls, {}))
    (tmp_path / "c.md").write_text(live_text, encoding="utf-8")
    return ca.cmd_check(None)


def test_a_failing_declaration_shows_what_it_was_adjudicated_against(tmp_path, monkeypatch, capsys):
    """The consumer that makes `claim` worth storing rather than a field nothing reads.

    Identity no longer reads prose, so a declaration outlives the sentence it was written for. When
    its anchors then fail, the first question is what the adjudicator was looking at -- and the
    answer is only useful if it is printed when it has CHANGED, not repeated on every drift report.
    """
    rc = _declared_then_edited(
        tmp_path, monkeypatch,
        "the claim is `t.py:3` exactly.\n",
        "a completely different claim about `t.py:3` now.\n",
        {"anchors": ["line9 filler_text_here"], "claim": "the claim is `t.py:3` exactly."},
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "DRIFTED" in err
    assert "adjudicated against: the claim is `t.py:3` exactly." in err


def test_an_unchanged_claim_is_not_echoed_back(tmp_path, monkeypatch, capsys):
    """The other half: a drift report on prose nobody touched must not repeat the line above it."""
    rc = _declared_then_edited(
        tmp_path, monkeypatch,
        "the claim is `t.py:3` exactly.\n",
        "the claim is `t.py:3` exactly.\n",
        # Recorded from a differently-wrapped revision of the SAME sentence. Comparing the raw
        # strings would call that a change and echo it back on every drift report; the comparison
        # is normalized for exactly this.
        {"anchors": ["line9 filler_text_here"], "claim": "the claim is\n  `t.py:3`   exactly."},
    )
    err = capsys.readouterr().err
    assert rc == 1 and "DRIFTED" in err
    assert "adjudicated against:" not in err


def test_a_deleted_citation_cannot_hand_its_declaration_to_a_url(tmp_path, monkeypatch, capsys):
    """The one silent path an ordinal-only key opened, and the reason auto-exempt occurrences are
    skipped unconditionally.

    A URL authority is enumerated like anything else, so it carries an ordinal. Delete the real
    citation above `https://t.py:3/x` and the URL slides from ordinal 1 to 0. While an explicit
    declaration was allowed to override the automatic classification, the URL then inherited that
    declaration, its anchors validated against the same range, the declaration counted as USED, and
    `check` returned 0 -- a real citation gone with nothing reported. Three (container, citation)
    groups in this repo already mix a real citation with a URL authority.
    """
    (tmp_path / "t.py").write_text(
        "\n".join(f"line{i} filler_text_here" for i in range(1, 30)), encoding="utf-8")
    monkeypatch.setattr(ca, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ca, "tracked_text_files", lambda: ["c.md", "t.py"])
    monkeypatch.setattr(ca, "load_declarations", lambda: ({
        '["c.md", "t.py:3", 0]': {"anchors": ["line3 filler_text_here"]},
        # A second, untouched citation keeps the sweep non-empty, so the run is judged on its
        # problems rather than on the zero-citation refusal.
        '["c.md", "t.py:5", 0]': {"anchors": ["line5 filler_text_here"]},
    }, {}))
    (tmp_path / "c.md").write_text(
        "the surviving claim is `t.py:5` exactly.\nsee https://t.py:3/x for the service.\n",
        encoding="utf-8")
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "STALE-DECLARATION" in err


@pytest.mark.parametrize("prefix", ["# >", "> #", "// >", "> //"])
def test_a_blockquote_and_a_comment_leader_compose_in_either_order(prefix):
    """`# >` is a quoted excerpt inside a source comment; `> #` is quoted code inside Markdown.

    Both are ordinary, and the grammar accepts a leader on either side of the arrows -- but only
    covering one order leaves the other free to regress silently, which is the failure mode this
    whole enumerator exists to remove. Deleting the post-arrow leader leaves `# >` working.
    """
    text = f"{prefix} the header at render_obsidian.py:\n{prefix} 255-466 documents the pass.\n"
    assert [c["cite"] for c in ca.find_citations("c.py", text)] == ["render_obsidian.py:255-466"]


def test_a_deeper_quoted_tail_does_not_continue_an_unquoted_head():
    """A tail quoted more deeply than its head OPENS a blockquote; it does not continue the line
    above. Stripping its arrows anyway turns ordinary Markdown into a citation nobody wrote, and a
    false RED can only be dismissed by writing a bogus exemption."""
    text = "The metric label is config.json:\n> 697 failures were counted.\n"
    assert ca.find_citations("c.md", text) == []
    nested = "> The metric label is config.json:\n> > 697 failures were counted.\n"
    assert ca.find_citations("c.md", nested) == []


@pytest.mark.parametrize("value", ["1", "true", "[]", '{"a": 1}'])
def test_an_exemption_reason_must_be_a_string(tmp_path, monkeypatch, value):
    """The diagnostic demands a non-blank string; stringifying whatever was there accepted `1`,
    `true` and `[]` as justification."""
    monkeypatch.setattr(ca, "ANCHOR_DIR", str(tmp_path))
    (tmp_path / "repo.json").write_text(
        '{"declarations": {}, "exemptions": {"[\\"a.md\\", \\"b.py:1\\", 0]": '
        '{"reason": ' + value + "}}}",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as e:
        ca.load_declarations()
    assert "reason" in str(e.value)


# ---------------------------------------------------------------------------
# #754 -- bare `:NNN` continuations.
# ---------------------------------------------------------------------------

FILLER = "\n".join(f"line{i} filler_text_here" for i in range(1, 30)) + "\n"


def _cont_setup(tmp_path, monkeypatch, container, declare_from=None, extra=None):
    """A fixture tree with four interchangeable targets, declared from ONE revision of the container.

    `declare_from` is what the anchors were adjudicated against; the container on disk is what
    `check` reads. Passing them separately is the only way a test can watch a declaration go stale,
    and every re-attribution test here needs exactly that. Returns the declaration map so a test can
    add an explicit `"target"` before calling `cmd_check` -- the map is what `load_declarations`
    closes over, so a mutation after this returns is still what the gate reads.
    """
    files = {"c.md": container, "a.py": FILLER, "b.py": FILLER, "d.py": FILLER, "t.py": FILLER}
    files.update(extra or {})
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(ca, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ca, "tracked_text_files", lambda: sorted(files))
    src = container if declare_from is None else declare_from
    decls = {
        ca.decl_key(c): {"anchors": [f"line{c['start']} filler_text_here"], "claim": c["sentence"]}
        for c in ca.find_citations("c.md", src)
    }
    monkeypatch.setattr(ca, "load_declarations", lambda: (decls, {}))
    return decls


def _undeclared(err):
    """Just the citation lexemes the run called UNDECLARED, so a test asserts a SET, not a substring.

    A substring assertion on one line cannot notice a second, unwanted occurrence -- and every
    opener-allowlist test here is about what must NOT be enumerated.
    """
    return sorted(line.split(" cites ")[1].split(" (")[0]
                  for line in err.splitlines() if line.strip().startswith("UNDECLARED "))


def test_a_bare_continuation_on_the_citation_s_own_line_is_enumerated(tmp_path, monkeypatch, capsys):
    """The shape the whole issue is about: name the file once, then keep citing it bare."""
    _cont_setup(tmp_path, monkeypatch, "the hash is `t.py:3`, and the check at `:5`.\n",
                declare_from="")
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert _undeclared(err) == [":5", "t.py:3"]
    assert "continuation of t.py" in err, err


def test_a_bare_continuation_is_attributed_across_a_line_wrap(tmp_path, monkeypatch, capsys):
    """Most of this corpus wraps its prose, so a same-line-only rule would miss a third of the class
    -- including both citations that were already stale when this was written."""
    _cont_setup(tmp_path, monkeypatch,
                "the hash is `t.py:3`, and the\nstale check sits at `:5`.\n", declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == [":5", "t.py:3"]


def test_the_continuation_lookback_bound_holds_on_BOTH_sides(tmp_path, monkeypatch, capsys):
    """A constant that is only tested from one side documents a boundary the code need not enforce.

    Six lines back is the deepest this corpus actually uses; seven is where a bare number stops
    being a continuation of anything and starts being a number in a different paragraph.
    """
    prose = "".join(f"filler prose line {i}\n" for i in range(1, 6))
    _cont_setup(tmp_path, monkeypatch, "the hash is `t.py:3`.\n" + prose + "and now `:5`.\n",
                declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == [":5", "t.py:3"], "6 lines back must still attach"

    prose = "".join(f"filler prose line {i}\n" for i in range(1, 7))
    _cont_setup(tmp_path, monkeypatch, "the hash is `t.py:3`.\n" + prose + "and now `:5`.\n",
                declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == ["t.py:3"], "7 lines back must NOT attach"


def test_a_blank_line_stops_the_continuation_walk(tmp_path, monkeypatch, capsys):
    """A paragraph break ends the sentence, whatever the line budget says."""
    _cont_setup(tmp_path, monkeypatch, "the hash is `t.py:3`.\n\nand now `:5`.\n", declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == ["t.py:3"]


def test_the_opener_allowlist_refuses_a_slice_a_json_value_and_an_ipv6_literal(
        tmp_path, monkeypatch, capsys):
    """The three shapes that are `:NNN` and are never a citation.

    A slice is the one that matters at scale -- admitting `[` moves the unattributable pool in this
    repo from 93 to 255, and every one of the 162 is `x[:10]`. The other two are one character each
    and would each be a false RED a maintainer has to dismiss by hand.
    """
    _cont_setup(tmp_path, monkeypatch,
                "the hash is `t.py:3`; code does data[:10], json {\"const\":9}, "
                "addr 2001:db8::1234.\n", declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == ["t.py:3"]


def test_a_slash_separated_run_of_line_numbers_is_two_continuations(tmp_path, monkeypatch, capsys):
    """The shape the first draft of this rule dropped: one filename, then `/:NNN/:NNN`."""
    _cont_setup(tmp_path, monkeypatch, "measured at t.py:3/:5/:7 exactly.\n", declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == [":5", ":7", "t.py:3"]


def test_a_url_earlier_on_the_line_cannot_auto_exempt_a_continuation(tmp_path, monkeypatch, capsys):
    """`auto_exempt_reason` tests `"://" in before[-12:]` -- PROXIMITY, not containment.

    Routed through it, a perfectly good continuation a dozen characters after a URL is auto-exempted
    and `cmd_check` skips it unconditionally, so the gate goes green with a real citation neither
    declared nor exempted nor ever checked again. That is the silent direction, and it is why a
    continuation returns `None` from that helper before any of its branches run.
    """
    _cont_setup(tmp_path, monkeypatch, "see `t.py:3`; https://x `:5` matters.\n", declare_from="")
    ca.cmd_check(None)
    assert _undeclared(capsys.readouterr().err) == [":5", "t.py:3"]


def test_a_wrapped_citation_s_tail_is_not_also_read_as_a_continuation(tmp_path, monkeypatch, capsys):
    """The tail half of a wrapped citation sits at the width of the stripped comment prefix, not 0.

    Drop that offset and the bare token is enumerated a SECOND time, attributed to whatever the head
    line named -- here `a.py`, which the sentence is not about at all. The one real wrapped citation
    in this repo opens its tail line with no prefix and cannot show that up.
    """
    _cont_setup(tmp_path, monkeypatch, "# a.py:1 and t.py\n# :3 is the continuation\n",
                declare_from="")
    ca.cmd_check(None)
    err = capsys.readouterr().err
    assert _undeclared(err) == ["a.py:1", "t.py:3"], err
    assert "continuation of a.py" not in err


def test_a_re_attribution_reds_on_the_continuation_s_OWN_key(tmp_path, monkeypatch, capsys):
    """The design's main risk: a bare token whose file changed under a key that did not.

    The assertion names the continuation KEY, not just "the run went red". Replacing `a.py` with
    `b.py` also moves the ordinary pathful key, so a test that only checks the exit code stays red
    with the candidate set deleted from the identity -- the wrong check eating the fixture.
    """
    _cont_setup(tmp_path, monkeypatch, "beta says `b.py:3` and also :3 here.\n",
                declare_from="alpha says `a.py:3` and also :3 here.\n")
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert '["c.md", "b.py:3", 0, "continuation"]' in err, err
    assert 'STALE-DECLARATION no live citation matches: ["c.md", "a.py:3", 0, "continuation"]' in err


def test_a_balanced_swap_of_two_paragraphs_still_reds(tmp_path, monkeypatch, capsys):
    """The fixture no pathful diagnostic can carry, and the one that pins the ORDINAL rule.

    Two paragraphs exchange their filenames. The pathful key multiset is identical before and after,
    so any red here is the continuations' alone. Count the ordinal per identity string instead of
    per raw token and both continuation keys are also unchanged: every declaration stays used, the
    run is green, and nothing records that both bare tokens now cite a different file.
    """
    before = "alpha says `a.py:3` and also :3 here.\n\nbeta says `b.py:3` and also :3 here.\n"
    after = "alpha says `b.py:3` and also :3 here.\n\nbeta says `a.py:3` and also :3 here.\n"
    _cont_setup(tmp_path, monkeypatch, after, declare_from=before)
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1, err
    assert '["c.md", "b.py:3", 0, "continuation"]' in err
    assert '["c.md", "a.py:3", 1, "continuation"]' in err
    assert 'STALE-DECLARATION no live citation matches: ["c.md", "a.py:3", 0, "continuation"]' in err
    assert 'STALE-DECLARATION no live citation matches: ["c.md", "b.py:3", 1, "continuation"]' in err


def test_two_named_files_make_the_attribution_the_adjudicator_s(tmp_path, monkeypatch, capsys):
    """Refusing to guess, and the explicit `"target"` that resolves it."""
    decls = _cont_setup(tmp_path, monkeypatch, "`a.py:3` and `b.py:3`, then also :3 here.\n")
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "AMBIGUOUS-CONTINUATION" in err, err
    assert "'a.py', 'b.py'" in err, err

    decls['["c.md", "a.py|b.py:3", 0, "continuation"]']["target"] = "a.py"
    assert ca.cmd_check(None) == 0, capsys.readouterr().err


def test_an_explicit_target_does_not_outlive_the_window_it_was_adjudicated_in(
        tmp_path, monkeypatch, capsys):
    """The second half of the same story, and the reason the key carries the whole candidate SET.

    Swap one competing candidate for a third file: the window is still ambiguous, the explicit
    target is still tracked, and `resolve` still accepts it. With only the winner in the key nothing
    would change and the run would stay green against a file the sentence no longer names.
    """
    before = "`a.py:3` and `b.py:3`, then also :3 here.\n"
    after = "`a.py:3` and `d.py:3`, then also :3 here.\n"
    decls = _cont_setup(tmp_path, monkeypatch, after, declare_from=before)
    decls['["c.md", "a.py|b.py:3", 0, "continuation"]']["target"] = "a.py"
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert '["c.md", "a.py|d.py:3", 0, "continuation"]' in err
    assert 'STALE-DECLARATION no live citation matches: ["c.md", "a.py|b.py:3", 0, "continuation"]' in err


def test_report_survives_an_ambiguous_continuation_instead_of_raising(tmp_path, monkeypatch, capsys):
    """`report` is the command an adjudicator runs precisely to settle an ambiguity, so it is the one
    command that must not die on one. Without the `target is None` guard `resolve` reaches
    `endswith("/" + None)` and raises."""
    _cont_setup(tmp_path, monkeypatch,
                "`a.py:3` and `b.py:3`, then also :3 in resolve_target_name().\n")
    ca.cmd_report(types.SimpleNamespace(scope=None, json=True))
    packets = json.loads(capsys.readouterr().out)
    bare = [p for p in packets if p["is_continuation"]]
    assert len(bare) == 1 and bare[0]["resolved"] is None and bare[0]["target"] is None
    # An ambiguous packet has NO filename to exclude, and `str.replace("", " ")` spaces out every
    # character rather than raising -- which shreds every multi-character token and hands the
    # adjudicator an empty list exactly where a human is being asked to choose. The sentence carries
    # one eligible subject on purpose: an assertion of `== []` would pass either way.
    assert bare[0]["subject_tokens"] == ["resolve_target_name"], bare[0]["subject_tokens"]


def test_a_continuation_that_drifts_names_the_file_it_was_attributed_to(tmp_path, monkeypatch, capsys):
    """The payoff: a bare number that no longer says what its sentence claims fails by name."""
    _cont_setup(tmp_path, monkeypatch, "the hash is `t.py:3`, and the check at `:5`.\n",
                extra={"t.py": "\n".join(f"line{i} filler_text_here" for i in [1, 2, 3, 4, 99]
                                         ) + "\n"})
    rc = ca.cmd_check(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "DRIFTED " + "c.md:1 -> " + "t.py:5-5" in err, err


def test_the_subject_rule_reads_a_continuation_s_ATTRIBUTED_filename(tmp_path, monkeypatch):
    """A bare cite has no filename of its own, and `"".split(":")[0]` is the empty string --
    `str.replace("", " ")` then spaces out every character rather than raising. The visible cost is
    a false RED: the target's own stem becomes a subject token the anchors are required to name."""
    # The synthetic filename's STEM has to be anchor-eligible (>= 8 chars, carrying an underscore)
    # or the two spellings of this rule are indistinguishable: a short stem is dropped by the length
    # floor whether it was stripped or not, and the mutation that reverts the fix stays green.
    line = "# validate_seg() (canon_x_validator.py:1316-1332, the regex check at :1251) --"
    got = ca._subject_required(line, ":1251", "canon_x_validator.py")
    assert "canon_x_validator" not in got, got
    assert got == {"validate_seg"}, got


def test_the_shipped_tree_enumerates_a_nonzero_number_of_continuations():
    """A rule that silently stops matching prints exactly what a working one prints, so the count is
    asserted inside the tool's own summary line.

    Deliberately NOT pinned to an absolute number: writing a new bare continuation already reds
    through UNDECLARED, so a count assertion would be a second, more brittle alarm for an event the
    first one catches. The corpus WITNESS below is the part worth pinning -- the slash-separated
    form is a real occurrence in this tree, not only a fixture.
    """
    p = subprocess.run([sys.executable, os.path.join(TOOLS, "citation_audit.py"), "check"],
                       cwd=REPO_ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    n = int(p.stdout.split("(")[1].split(" of them")[0])
    assert n > 0, "no bare continuation was enumerated at all -- the rule stopped matching"

    r = subprocess.run([sys.executable, os.path.join(TOOLS, "citation_audit.py"),
                        "report", "--json"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    slash = [packet for packet in json.loads(r.stdout)
             if packet["is_continuation"] and packet["container"].endswith("chapter-paths.d.mts")]
    assert sorted(packet["cite"] for packet in slash) == [":2140", ":2145"], slash
