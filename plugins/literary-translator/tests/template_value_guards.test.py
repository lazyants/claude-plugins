"""tests/template_value_guards.test.py -- #370: the startup grammar guards on
the two profile-sourced scalars the glossary and skeptic workflow templates
splice into shell command strings.

WHY the guards exist, WHICH sites they cover and WHAT the leading-dash
narrowing buys are all argued once, in the template that owns each guard
(glossary-pass-wf.template.js and skeptic-pass-wf.template.js). They are not
restated here; a third copy of an argument is the thing that goes stale.

WHAT IS ASSERTED, in two layers:

  1. SOURCE-ORDER (no Node needed): each guard exists, carries its documented
     message, and its DECLARATION precedes its FIRST CONSUMER. For the glossary
     template that consumer is `CITATION_REVIEW_ENABLED`, NOT the first shell
     splice several hundred lines later -- a guard between the two would still
     precede every command, so an assertion written against the splice would
     stay green while "before any consumer" was violated, and so would every
     executed test in this file.
  2. EXECUTED (Node): the REAL shipped template is instantiated and run. An
     illegal value must abort before `agent()`/`pipeline()` is called; a legal
     value must get PAST the guard and reach the batch-agent-cap gate, which is
     downstream of both insertion points. The positive case is therefore not
     "it did not crash" -- it is "it reached a specific point after the guard".
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
SKEPTIC_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"
PROFILE_SCHEMA = ASSETS_DIR / "schemas" / "profile.schema.json"

assert GLOSSARY_TEMPLATE.is_file(), f"glossary template not found at {GLOSSARY_TEMPLATE}"
assert SKEPTIC_TEMPLATE.is_file(), f"skeptic template not found at {SKEPTIC_TEMPLATE}"
assert PROFILE_SCHEMA.is_file(), f"profile.schema.json not found at {PROFILE_SCHEMA}"

NODE = shutil.which("node")

pytestmark_node = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; no hard Node.js dependency for this plugin otherwise "
    "(matches tests/seg_safety_source_and_workflow.test.py's own stance)",
)


# ---------------------------------------------------------------------------
# Fixture vocabulary.
#
# Every value below is written so it is safe as the CONTENT of a JS double-
# quoted string literal (no bare `"` and no stray backslash), because that is
# exactly where the substitution puts it. The one deliberate exception is the
# trailing-newline case, which is spelled as the two-character JS ESCAPE `\n`
# in the substituted source: a raw newline there would terminate the string
# literal and Node would reject the file as a PARSE error, which is not the
# same thing as the guard refusing the value.
# ---------------------------------------------------------------------------

LEGAL_RESEARCH_MODES = ["live", "offline"]
ILLEGAL_RESEARCH_MODES = [
    "",                     # empty substitution
    "Live",                 # case is not part of the enum
    "live ",                # trailing space
    "live; rm -rf /",       # shell metacharacters
    "live offline",         # two values, one token
    "online",               # plausible-but-wrong value
]
# Spelled separately: these carry a JS escape rather than a literal character.
ILLEGAL_RESEARCH_MODES_JS_ESCAPED = [
    "live\\n",              # trailing newline -- JS `$` must not match before it
]

LEGAL_PARTICLE_CONFIGS = [
    "fr.json",              # a shipped preset
    "he.local.json",        # the documented per-project override form
    "x-y.json",             # INTERIOR dashes stay legal
    "_a.json",
    ".a.json",
]
ILLEGAL_PARTICLE_CONFIGS = [
    "",                     # empty substitution
    "fr",                   # no .json suffix
    "fr.yaml",              # wrong suffix
    ".json",                # suffix only, no stem (also below the schema minLength)
    "-x.json",              # LEADING DASH -- argparse eats it as an option name
    "-.json",               # ditto, degenerate
    "../fr.json",           # path traversal
    "/etc/fr.json",         # absolute path
    "languages/fr.json",    # a path, not the bare filename the flag documents
    "fr.json extra.json",   # two tokens
    "fr.json; rm -rf /",    # shell metacharacters
]
ILLEGAL_PARTICLE_CONFIGS_JS_ESCAPED = [
    "fr.json\\n",           # trailing newline -- see the module docstring
]


# ---------------------------------------------------------------------------
# Layer 1 -- source-order assertions. No Node required, so a node-less
# environment still fails loudly if a refactor deletes or relocates a guard.
# ---------------------------------------------------------------------------

def test_glossary_template_declares_the_research_mode_guard():
    raw = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    assert "RESEARCH_MODE_RE" in raw
    assert "Unsafe glossary.research_mode" in raw
    assert "/^(live|offline)$/" in raw, (
        "the guard's allowlist must be spelled as the schema enum itself -- if this "
        "moved, check profile.schema.json's glossary.research_mode enum first"
    )


def test_glossary_guard_precedes_its_first_consumer_not_merely_its_first_splice():
    """The acceptance criterion is "before ANY consumer", and the glossary
    template's first consumer is NOT a shell splice.

    `CITATION_REVIEW_ENABLED` reads RESEARCH_MODE hundreds of lines before
    `checkBatchCmd()` splices it, and it decides whether the pre-merge citation
    review runs at all. A guard placed between those two would still precede
    every shell command, so an assertion written against the splice would stay
    green while the criterion was violated -- and every executed test in this
    file would stay green too, because they all abort at the guard either way.
    This assertion is the only thing standing between the two positions.
    """
    raw = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    decl_idx = raw.index('const RESEARCH_MODE = "')
    guard_idx = raw.index("const RESEARCH_MODE_RE")
    first_consumer_idx = raw.index("const CITATION_REVIEW_ENABLED = RESEARCH_MODE")
    first_splice_idx = raw.index('" --research-mode " + RESEARCH_MODE')
    assert decl_idx < guard_idx < first_consumer_idx, (
        "RESEARCH_MODE_RE must be declared after RESEARCH_MODE and before "
        "CITATION_REVIEW_ENABLED, the first value it decides"
    )
    # Stated rather than assumed: the branch really does come first.
    assert first_consumer_idx < first_splice_idx


def test_skeptic_template_declares_the_particle_config_guard():
    raw = SKEPTIC_TEMPLATE.read_text(encoding="utf-8")
    assert "PARTICLE_CONFIG_RE" in raw
    assert "Unsafe source.language.particle_config" in raw
    assert r"/^[A-Za-z0-9_.][A-Za-z0-9_.-]*\.json$/" in raw, (
        "the guard must be the STRICTER-in-first-character form; see "
        "test_particle_guard_is_deliberately_stricter_than_the_schema"
    )
    # The message has to say WHY a schema-legal value is being refused, or the
    # operator reading it has no way to tell the narrowing from a bug.
    assert "no leading dash" in raw


def test_skeptic_guard_precedes_both_splice_sites():
    raw = SKEPTIC_TEMPLATE.read_text(encoding="utf-8")
    decl_idx = raw.index('const PARTICLE_CONFIG = "')
    guard_idx = raw.index("const PARTICLE_CONFIG_RE")
    splices = [m.start() for m in re.finditer(r"--particle-config \" \+ PARTICLE_CONFIG", raw)]
    assert len(splices) == 2, (
        f"expected exactly the two documented --particle-config splice sites, found {len(splices)} -- "
        "a new one needs no new guard, but this count is what proves the inventory is still complete"
    )
    assert decl_idx < guard_idx < min(splices)


def test_research_mode_guard_mirrors_the_schema_enum_exactly():
    """RESEARCH_MODE_RE claims to BE the schema enum. Pinned so a schema change
    cannot silently leave the runtime guard behind."""
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    enum = schema["properties"]["glossary"]["properties"]["research_mode"]["enum"]
    assert enum == ["live", "offline"], (
        f"profile.schema.json's research_mode enum is now {enum!r}; "
        "glossary-pass-wf.template.js's RESEARCH_MODE_RE must be updated with it"
    )


def test_particle_guard_is_deliberately_stricter_than_the_schema():
    """The one intentional asymmetry in this change, pinned so it stays
    intentional.

    The schema's pattern admits a LEADING DASH; the runtime guard does not,
    because both splice sites emit the value space-separated after
    `--particle-config`, where argparse reads a leading-dash token as an option
    name and exits with "expected one argument". Every value the runtime guard
    accepts is still schema-legal -- the guard narrows, it never widens.
    """
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    lang = schema["properties"]["source"]["properties"]["language"]["properties"]
    schema_pattern = lang["particle_config"]["pattern"]
    assert schema_pattern == r"^[A-Za-z0-9_.-]+\.json$", (
        f"profile.schema.json's particle_config pattern is now {schema_pattern!r}; "
        "re-derive the runtime guard's relationship to it before editing this test"
    )
    schema_re = re.compile(schema_pattern)
    # Only the SCHEMA side is spelled out here. A second Python copy of the
    # guard's own regex would be a hand-maintained duplicate that could drift
    # from the shipped one silently -- what the guard actually accepts and
    # refuses is settled by the executed tests below, against the real template.
    #
    # Direction 1: everything the executed tests accept is schema-legal too, so
    # the guard narrows and never widens.
    for value in LEGAL_PARTICLE_CONFIGS:
        assert schema_re.fullmatch(value), f"guard-legal {value!r} is not schema-legal"
    # Direction 2: the narrowing is real, and it is exactly the leading dash --
    # these two are SCHEMA-legal, and the executed parametrization above proves
    # the shipped guard refuses them anyway.
    for value in ("-x.json", "-.json"):
        assert schema_re.fullmatch(value), f"{value!r} is expected to be SCHEMA-legal"
        assert value in ILLEGAL_PARTICLE_CONFIGS


# ---------------------------------------------------------------------------
# Layer 2 -- executed assertions. The REAL shipped template is substituted and
# run under Node, wrapped the way the Workflow tool that executes it must
# (the raw template both `export`s meta and `return`s at its own top level, so
# it is not valid standalone JS). Duplicated rather than imported, per this
# directory's self-contained-test-file convention.
# ---------------------------------------------------------------------------

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260823T000000Z"
FIXTURE_PLUGIN_ROOT = str(PLUGIN_ROOT / "skills" / "literary-translator")


def instantiate_glossary(*, research_mode: str, batch_agent_cap: int = 0,
                         allow_unresolved: bool = False) -> str:
    text = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{SOURCE_LANG}}", "French")
    text = text.replace("{{TARGET_LANG}}", "Russian")
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{EFFORT}}", "high")
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    # A real, quote-free install root: the template's own PLUGIN_ROOT guard runs
    # BEFORE the one under test here and would otherwise be what aborts the run.
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(FIXTURE_PLUGIN_ROOT))
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    if not allow_unresolved:
        assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def instantiate_skeptic(*, particle_config: str, batch_agent_cap: int = 0,
                        allow_unresolved: bool = False) -> str:
    text = SKEPTIC_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{SOURCE_LANG}}", "French")
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{PARTICLE_CONFIG}}", particle_config)
    if not allow_unresolved:
        assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap_for_execution(js_source: str) -> str:
    assert js_source.count("export const meta") == 1, (
        "expected exactly one 'export const meta' declaration to strip -- "
        "the template's export contract may have changed"
    )
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const ARGS = __ARGS_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

async function agent(promptText, opts) {
  callsLog.push((opts && opts.label) || "");
  throw new Error("agent() should not have been called by this test");
}

async function pipeline(items, stage1, stage2) {
  pipelineCalled = true;
  throw new Error("pipeline() should not have been called by this test");
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, ARGS);
    process.stdout.write(JSON.stringify({
      result: result,
      calls: callsLog,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run_template(tmp_path: Path, js_source: str, args, timeout: int = 30):
    assert NODE is not None, "node executable not found on PATH -- required to run this test"
    text = HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", _wrap_for_execution(js_source))
    text = text.replace("__ARGS_JSON__", json.dumps(args))
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(text, encoding="utf-8")
    return subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=timeout)


# One well-formed batch, so the run has something to reach the cap gate with.
GLOSSARY_BATCH = [{
    "index": 0,
    "candidates": [{
        "name": "Jean", "freq": 3, "mid_sentence": False, "multiword": False,
        "abbrev": False, "n_segments": 2, "likely_name": True,
    }],
}]
SKEPTIC_BATCH = [{"index": 0, "assignments": [{"source_form": "Jean"}]}]


@pytestmark_node
@pytest.mark.parametrize("research_mode", ILLEGAL_RESEARCH_MODES + ILLEGAL_RESEARCH_MODES_JS_ESCAPED)
def test_glossary_throws_on_illegal_research_mode(tmp_path, research_mode):
    proc = run_template(tmp_path, instantiate_glossary(research_mode=research_mode), GLOSSARY_BATCH)
    assert proc.returncode != 0, (
        f"research_mode {research_mode!r} was accepted; stdout={proc.stdout[:400]}"
    )
    assert "Unsafe glossary.research_mode" in proc.stderr, proc.stderr[:400]
    # The guard must abort BEFORE anything is emitted. Asserting the absence of
    # the harness's own "should not have been called" text would be vacuous --
    # the harness writes exactly ONE error line, and the assertion above already
    # requires that line to be the guard's.
    assert proc.stdout.strip() == "", proc.stdout[:400]


@pytestmark_node
def test_glossary_throws_on_unsubstituted_research_mode_token(tmp_path):
    """The producer this guard exists for is the substituting session, so the
    substitution simply not happening is the first case, not an exotic one."""
    src = instantiate_glossary(research_mode="{{RESEARCH_MODE}}", allow_unresolved=True)
    proc = run_template(tmp_path, src, GLOSSARY_BATCH)
    assert proc.returncode != 0
    assert "Unsafe glossary.research_mode" in proc.stderr, proc.stderr[:400]


@pytestmark_node
@pytest.mark.parametrize("research_mode", LEGAL_RESEARCH_MODES)
def test_glossary_legal_research_mode_reaches_the_cap_gate(tmp_path, research_mode):
    """The positive case asserts progress PAST the guard, not merely absence of
    a crash: with batch_agent_cap=0 the run is expected to reach the
    batch-agent-cap gate, which lives several hundred lines downstream of the
    guard, and return its documented refusal object."""
    proc = run_template(tmp_path, instantiate_glossary(research_mode=research_mode), GLOSSARY_BATCH)
    assert proc.returncode == 0, proc.stderr[:600]
    payload = json.loads(proc.stdout)
    assert payload["result"]["reason"] == "batch-too-large", payload
    assert payload["calls"] == [], payload["calls"]
    assert payload["pipelineCalled"] is False


@pytestmark_node
@pytest.mark.parametrize("particle_config", ILLEGAL_PARTICLE_CONFIGS + ILLEGAL_PARTICLE_CONFIGS_JS_ESCAPED)
def test_skeptic_throws_on_illegal_particle_config(tmp_path, particle_config):
    proc = run_template(tmp_path, instantiate_skeptic(particle_config=particle_config), SKEPTIC_BATCH)
    assert proc.returncode != 0, (
        f"particle_config {particle_config!r} was accepted; stdout={proc.stdout[:400]}"
    )
    assert "Unsafe source.language.particle_config" in proc.stderr, proc.stderr[:400]
    assert proc.stdout.strip() == "", proc.stdout[:400]


@pytestmark_node
def test_skeptic_throws_on_unsubstituted_particle_config_token(tmp_path):
    src = instantiate_skeptic(particle_config="{{PARTICLE_CONFIG}}", allow_unresolved=True)
    proc = run_template(tmp_path, src, SKEPTIC_BATCH)
    assert proc.returncode != 0
    assert "Unsafe source.language.particle_config" in proc.stderr, proc.stderr[:400]


@pytestmark_node
@pytest.mark.parametrize("particle_config", LEGAL_PARTICLE_CONFIGS)
def test_skeptic_legal_particle_config_reaches_the_cap_gate(tmp_path, particle_config):
    proc = run_template(tmp_path, instantiate_skeptic(particle_config=particle_config), SKEPTIC_BATCH)
    assert proc.returncode == 0, proc.stderr[:600]
    payload = json.loads(proc.stdout)
    assert payload["result"]["reason"] == "batch-too-large", payload
    assert payload["calls"] == [], payload["calls"]
    assert payload["pipelineCalled"] is False
