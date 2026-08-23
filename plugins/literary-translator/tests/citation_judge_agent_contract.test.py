"""tests/citation_judge_agent_contract.test.py -- the citation judge's tool
boundary, and the two-file contract that makes it reachable (#353).

WHAT IS BEING GUARDED. Under ``glossary.research_mode: live`` the W3 citation
judge reads retrieved page bodies verbatim. Those bytes are authored by
whoever controls a cited site, so every instruction in
``citationJudgePrompt()`` -- including "do not run any command that opens a
network connection" -- is a rule addressed to an agent that is simultaneously
reading the attacker's own text. 1.16.1 (#347) already refused to treat that
as an enforcement point when it moved RETRIEVAL out of the judging agent; what
it did not do, and said so explicitly, was remove the judge's Bash TOOL.

#353 removes it, by dispatching the judge as a plugin agent whose frontmatter
grants ``tools: Read`` and nothing else. That mechanism is expressed across
two files, and either one alone is inert:

  * ``agents/citation-judge.md`` -- the allowlist the harness enforces.
  * ``glossary-pass-wf.template.js`` -- the ``agentType`` that selects it.

Rename or relax either half and the judge silently reverts to a default-toolset
agent, with no other symptom: the pass still runs, still approves batches, and
still reports a clean glossary run. So the pin is the PAIR, not either file.

SCOPE, vs tests/glossary_citation_review.test.py. That file owns the OBSERVED
dispatch -- it executes the real template under Node and reads back the
``agentType`` the shipped code actually passed, which is the stronger evidence
and the only one that proves the option reaches the call. It cannot own the
allowlist, because it carries a module-wide skip when Node is absent: a
suite run on a host without Node would then report green while a widened
``tools:`` line went unread. This file needs no Node, so the capability half is
checked wherever the suite runs at all.

WHAT THIS FILE CANNOT CLAIM, stated because the next reader would otherwise
assume it: nothing here proves the RUNTIME honours ``tools:``. That is the
harness's contract, not this plugin's, and no test in this repository can
observe it. What is checked is that the plugin ships the narrowest declaration
it can and that the dispatch names it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
REPO_ROOT = PLUGIN_ROOT.parents[1]

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _agent_definition import (  # noqa: E402
    CITATION_JUDGE_AGENT,
    citation_judge_agent_type,
    read_frontmatter,
    tool_allowlist,
)

# The judge reads three things and writes nothing: the approved snapshot, the
# evidence index, and the evidence files that index names. Read covers all
# three. This is a whole-set equality on purpose -- a negative enumeration
# ("not Bash, not WebFetch, ...") passes for every tool nobody thought to name,
# including one the harness gains next month.
EXPECTED_JUDGE_TOOLS = ["Read"]


def test_citation_judge_agent_definition_is_shipped():
    assert CITATION_JUDGE_AGENT.is_file(), (
        f"the citation judge's agent definition is missing at {CITATION_JUDGE_AGENT}; "
        "without it the template's agentType resolves to nothing"
    )


def test_citation_judge_tool_allowlist_is_exactly_read():
    fields = read_frontmatter(CITATION_JUDGE_AGENT)
    assert "tools" in fields, (
        f"{CITATION_JUDGE_AGENT} declares no `tools:` line. Omitting it does not "
        "restrict anything -- it grants the default toolset, which is the state "
        "#353 exists to end."
    )
    assert tool_allowlist(fields) == EXPECTED_JUDGE_TOOLS, (
        f"the citation judge's tool allowlist must be exactly {EXPECTED_JUDGE_TOOLS}, "
        f"got {tool_allowlist(fields)!r}. Every byte this agent reads is "
        "attacker-authorable; the allowlist is the boundary."
    )


def test_citation_judge_declares_the_name_the_dispatch_uses():
    fields = read_frontmatter(CITATION_JUDGE_AGENT)
    assert fields.get("name") == CITATION_JUDGE_AGENT.stem, (
        f"{CITATION_JUDGE_AGENT} declares name={fields.get('name')!r}, which does not "
        f"match its own filename stem {CITATION_JUDGE_AGENT.stem!r}"
    )


def test_glossary_template_dispatches_the_judge_by_that_agent_type():
    """The wiring half. A correct agent definition nothing selects is inert."""
    source = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    expected = citation_judge_agent_type()
    judge_call = re.search(
        r"await agent\(citationJudgePrompt\(batch, attempt\), \{(?P<opts>.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert judge_call is not None, (
        "could not find the citation-judge agent() call in "
        f"{GLOSSARY_TEMPLATE}; this pin cannot be evaluated"
    )
    opts = judge_call.group("opts")
    assert f'agentType: "{expected}"' in opts, (
        f"the citation judge dispatch must pass agentType: {expected!r}, got: {opts!r}"
    )


def test_prepare_call_is_not_tool_restricted():
    """The judge's restriction must not be copied onto its sibling.

    The prepare step's whole job is running ``fetch_citation.py`` through Bash.
    Giving it the judge's allowlist would break retrieval outright -- and would
    do so as a citation review that rejects every batch, which reads like a
    corpus problem rather than a wiring one.
    """
    source = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    prepare_call = re.search(
        r"await agent\(citationPreparePrompt\(batch, attempt\), \{(?P<opts>.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert prepare_call is not None, (
        f"could not find the citation-prepare agent() call in {GLOSSARY_TEMPLATE}"
    )
    assert "agentType" not in prepare_call.group("opts"), (
        "the citation prepare call must stay a default-toolset Claude call -- it "
        f"needs Bash to run the fetcher: {prepare_call.group('opts')!r}"
    )


# ---------------------------------------------------------------------------
# The prose half. #353's residual was named in shipped documentation as well as
# in code, so closing it in code alone leaves the repository asserting, in
# operator-facing text, that the judge still holds a tool it does not.
#
# Historical release notes are exempt BY FILE, not by phrasing: CHANGELOG.md
# records what each release did and was true when written. README.md's release
# bullets are the same kind of record, so the claim there is time-qualified in
# place rather than deleted -- which is why the check below reads current-state
# prose only.
# ---------------------------------------------------------------------------

CURRENT_STATE_PROSE = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md",
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "canon-and-glossary.md",
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "orchestration-and-batching.md",
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "profile.example.yml",
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts" / "fetch_citation.py",
    GLOSSARY_TEMPLATE,
)

# Each pattern is a claim that was TRUE before #353 and is false after it. They
# are matched case-insensitively over the files above, which are the ones that
# described the judge's capability in the first place.
STALE_JUDGE_CLAIMS = (
    r"judge still holds",
    r"still holds a Bash tool",
    r"ordinary agent holding Bash",
    r"ordinary agent and still holds Bash",
)


def test_no_current_state_prose_still_claims_the_judge_holds_bash():
    offenders = []
    for path in CURRENT_STATE_PROSE:
        assert path.is_file(), f"prose sweep names a file that does not exist: {path}"
        text = path.read_text(encoding="utf-8")
        for pattern in STALE_JUDGE_CLAIMS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {pattern}")
    assert not offenders, (
        "these files still tell an operator the citation judge holds Bash, which "
        "stopped being true in #353:\n  " + "\n  ".join(offenders)
    )
