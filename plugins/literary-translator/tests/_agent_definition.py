"""tests/_agent_definition.py -- the ONE reader for this plugin's shipped
`agents/*.md` definitions (#353).

Why this is a helper rather than two copies of a five-line parser: the judge's
tool boundary is expressed across TWO files -- the agent definition's
frontmatter and the `agentType` the glossary template passes -- and the whole
point of the pin is that the two must name the same agent. Two suites need to
read the definition (a Node-free contract suite and the Node-driven dispatch
harness), and if each parsed it its own way the pin could pass in one and fail
in the other for reasons that have nothing to do with the shipped bytes.

There is no YAML dependency here on purpose: this plugin's test suite is
stdlib-only, and the frontmatter this reads is a flat block of `key: value`
lines that a real YAML parser and this one agree on. Anything nested is
refused rather than silently half-read.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = PLUGIN_ROOT / "agents"
CITATION_JUDGE_AGENT = AGENTS_DIR / "citation-judge.md"

# The name the harness resolves an agentType against is "<plugin>:<agent>", and
# the plugin half is the manifest's own `name`. Read from the manifest rather
# than typed, so renaming the plugin cannot leave this pin quietly pointing at
# an agent nobody ships.
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"


def _plugin_name() -> str:
    import json

    return json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["name"]


def read_frontmatter(path: Path) -> dict[str, str]:
    """Parse an agent definition's leading `---` block into a flat dict.

    Refuses anything this reader cannot represent faithfully -- a missing or
    unterminated block, a nested value, a duplicate key -- rather than
    returning a partial dict a caller would then assert against. A silently
    half-read frontmatter is exactly how a tool allowlist would appear to be
    checked while not being checked.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError(f"{path} does not open with a --- frontmatter block")
    try:
        end = lines.index("---", 1)
    except ValueError:  # pragma: no cover -- guarded by the contract suite
        raise AssertionError(f"{path}'s frontmatter block is never closed") from None

    fields: dict[str, str] = {}
    for lineno, raw in enumerate(lines[1:end], start=2):
        if not raw.strip():
            continue
        if raw[:1] in (" ", "\t"):
            raise AssertionError(
                f"{path}:{lineno} is an indented (nested) frontmatter line; this "
                f"reader only represents flat key: value pairs: {raw!r}"
            )
        if ":" not in raw:
            raise AssertionError(f"{path}:{lineno} is not a key: value line: {raw!r}")
        key, _, value = raw.partition(":")
        key = key.strip()
        if key in fields:
            raise AssertionError(f"{path}:{lineno} repeats the frontmatter key {key!r}")
        fields[key] = value.strip()
    return fields


def tool_allowlist(fields: dict[str, str]) -> list[str]:
    """The declared tools, normalized to a list.

    The runtime accepts `tools: Read` and `tools: Read, Grep` alike, so a
    caller comparing against a list must not have to know which spelling the
    file happens to use -- otherwise re-spelling a single-tool allowlist as a
    one-item list would go RED while granting nothing new, and the suite would
    be pinning punctuation instead of capability.
    """
    raw = fields["tools"]
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [item.strip().strip("'\"") for item in raw.split(",") if item.strip()]


def citation_judge_agent_type() -> str:
    """The exact string the glossary template must pass as its `agentType`."""
    name = read_frontmatter(CITATION_JUDGE_AGENT)["name"]
    return f"{_plugin_name()}:{name}"
