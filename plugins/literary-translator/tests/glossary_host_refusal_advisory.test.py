#!/usr/bin/env python3
"""#919: an intermittent host refusal (HTTP 403/429/5xx) burns the repair ladder
because the repair agent is never told what happened to any OTHER row in the
run. The fix is a run-scoped, advisory tally that reaches the repair prompt as
one extra fact -- never a rule, never a ban, never a retry-policy change.

Five properties fail SILENTLY if they regress, which is why they are pinned
here rather than left to the driver's own logging:

  1. THE TALLY SURVIVES ACROSS FETCH PASSES. A 403 seen on pass 1 whose row
     then succeeds on pass 2 is the measured pattern (13 refusals against ~47
     successes on one host in the issue), so a tally taken from the LAST pass
     alone would silently report nothing for exactly the case this exists to
     catch.

  2. THE CURRENT REPAIR'S OWN FAILING HOSTS ARE NEVER CAPPED AWAY. A repair
     can legitimately carry more failing hosts than the historical cap, and
     the host refusing right now is the one the agent is about to re-pick --
     truncating it away by an unlucky sort order would silently hide the one
     fact that matters most.

  3. A HOST STRING IS MODEL-AUTHORED TEXT ABOUT TO BE INTERPOLATED INTO
     ANOTHER MODEL'S PROMPT. `_safe_host` bounds its syntax and size; it
     establishes no trust, so the prompt itself must still frame the list as
     evidence, never as instruction.

  4. THE STORED TALLY IS NEVER RAISED ON. `sanitize_host_refusals` is a
     fail-safe read of a state document another process might have written;
     a malformed value must be dropped, never crash the driver.

  5. AN EMPTY OR ABSENT TALLY MUST NOT CHANGE THE PROMPT'S BYTES. The
     paragraph is new, additive machinery -- if it silently leaked in on a run
     with no history to report, every existing prompt-parity pin would be
     lying about what ships.

No network, no subprocess, no real sleep for the Python-only groups. The
template groups run the REAL shipped template under node (skipped if node is
not on PATH), because a hand-built fixture for the other side of this wire is
exactly how a broken contract passes on both ends at once.

Four more properties were added after a code-review round found each one
admitted. Each test below that pins one says so in its own docstring and
states which pre-fix behaviour it fails against:

  FINDING 1 (MAJOR) -- `_safe_host` dropped a host the fetcher itself accepts
  (a bare IP literal, a non-ASCII host it would IDNA-encode), so a refusal on
  that host never reached the tally at all. Its own fix introduced a second,
  quieter defect (round-2 MAJOR, admitted): `_safe_host` returns an IPv6
  literal bracket-less, and `sanitize_host_refusals`'s bare-form-only
  round-trip check would then silently drop every IPv6 host from the tally
  on the next load.

  FINDING 2 (MAJOR) -- `sanitize_host_refusals` called `int(status)` with
  nothing catching the `ValueError` a superscript digit or an over-length
  digit string raises, so a malformed state document could crash the whole
  driver invocation rather than being dropped.

  FINDING 3 (MINOR) -- an earlier pass's tally was discarded whenever a LATER
  fetch command failed outright, because the `ok: False` return path never
  carried `host_refusals` at all.

  FINDING 4 (NIT) -- `repair_advisory_hosts` returned a live reference into
  its input tally's per-host dict instead of a copy.

Review round 2 found two more, both in `_safe_host` and both caused by round
1's own fixes:

  ROUND 2 FINDING 1 (MAJOR) -- a SCOPED IPv6 literal escaped every bound: the
  IP branch returned before the ASCII/length/label check, so a scope id
  carrying U+2028 (invisible to the pre-`urlsplit` control-character check,
  which only catches characters below U+0020) or a scope id of unbounded
  length could reach the built prompt -- and U+2028 in particular could make
  the harness's `splitlines()`-based reply parsing fatal.

  ROUND 2 FINDING 2 (MINOR) -- IDNA maps several Unicode "full stop"
  look-alikes (U+3002 among them) onto an ASCII `.`, re-introducing a
  trailing dot AFTER the only strip that used to run, which made the final
  label empty and dropped a host the fetcher genuinely used.

A closing security pass found two more, both P3:

  ADMITTED 1 -- a scoped IPv6 literal split ONE endpoint into SEVERAL tally
  keys: `%1`, `%01` and `%001` each produced a different key although
  `getaddrinfo` resolves all three to the identical endpoint, and a scope id
  can carry punctuation no DNS label may. The fix rejects any IPv6 literal
  with a non-empty scope id outright.

  ADMITTED 2 -- `read_outcome_pairs` assumed the index's JSON root is a
  `dict` (a list/null/number/string root raised a bare `AttributeError`
  instead of the `DriverError` every other refusal in it raises) and
  accepted a Boolean item_index (`isinstance(True, int)` is `True` in
  Python), which could alias row 1's host onto a forged entry.
"""

import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
JSON_STDOUT = SKILL_ROOT / "assets" / "scripts" / "json_stdout.py"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js"

NODE = shutil.which("node")


def load_driver(scripts_dir: Path):
    """Imports the real shipped script from an isolated fixture scripts/ dir."""
    target = scripts_dir / "glossary_dispatch_driver.py"
    if not target.exists():
        shutil.copy2(DRIVER, target)
        # json_stdout.py is the driver's one hard sibling dependency: it is
        # loaded by exact path at import time and the driver exits without it,
        # exactly as a deployed copy does. Staging it keeps this fixture a real
        # scripts/ dir.
        shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location(
        f"gdd_hra_{abs(hash(str(target)))}", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    return load_driver(scripts)


def subst(**over):
    base = dict(durable_root="/durable", source_lang="he", target_lang="en",
                research_mode="live", run_id="runX", effort="high",
                citation_content_types="text/html", batch_agent_cap=10 ** 9,
                plugin_root="/plugin", resumed_batch_indices=[])
    base.update(over)
    return base


BATCH = {"index": 0, "candidates": [{"name": "Alpha", "freq": 2}]}


# ---------------------------------------------------------------------------
# 1. `_safe_host` -- bounds syntax and size only; establishes no trust.
# ---------------------------------------------------------------------------

def test_safe_host_accepts_and_lowercases_an_ordinary_host(mod):
    assert mod._safe_host("https://WWW.Example.COM/path") == "www.example.com"


def test_safe_host_normalizes_a_trailing_dot_like_the_fetcher(mod):
    """fetch_citation.py:833-847 strips a trailing dot before comparing hosts;
    counting the un-stripped form would silently split one host's tally in two."""
    assert mod._safe_host("https://EXAMPLE.com./x") == "example.com"


@pytest.mark.parametrize("raw", [
    "https://exa\nmple.com/x",   # a raw LF -- urlsplit silently strips it
    "https://exa\tmple.com/x",   # a raw TAB -- same silent-strip risk
    "https://exa\rmple.com/x",   # a raw CR
], ids=["lf", "tab", "cr"])
def test_safe_host_rejects_a_raw_control_character_before_urlsplit(mod, raw):
    """Checked BEFORE urlsplit is called at all: urlsplit strips LF/CR/TAB from
    a URL, so checking after the call would never see the character that must
    be refused."""
    assert mod._safe_host(raw) is None


def test_safe_host_rejects_a_malformed_ipv6_literal(mod):
    """urlsplit itself raises ValueError on an unterminated IPv6 bracket."""
    assert mod._safe_host("http://[::1/x") is None


def test_safe_host_accepts_a_global_ipv4_literal(mod):
    """FINDING 1 (round-3 MAJOR, admitted). The fetcher itself resolves and
    fetches a bare IP-literal source -- rejecting it here silently dropped a
    real refusal from ever reaching the repair prompt. FAILS against the
    pre-fix driver, which returns None for every IP literal; passes once
    lane 1 canonicalizes it instead of dropping it."""
    assert mod._safe_host("http://8.8.8.8/x") == "8.8.8.8"


def test_safe_host_accepts_a_global_ipv6_literal(mod):
    """Same finding, the IPv6 side. FAILS against the pre-fix driver."""
    assert mod._safe_host("http://[2606:4700:4700::1111]/x") == "2606:4700:4700::1111"


def test_safe_host_canonicalizes_an_expanded_ipv6_literal(mod):
    """Proves real CANONICALIZATION rather than a pass-through of whatever
    string urlsplit handed back: an uncompressed IPv6 literal must come back
    in its compressed, canonical form -- the same form the previous test's
    literal already happened to be written in. FAILS against the pre-fix
    driver, which returns None for any IP literal at all."""
    assert mod._safe_host("http://[2606:4700:4700:0:0:0:0:1111]/x") == \
        "2606:4700:4700::1111"


def test_safe_host_rejects_a_scoped_ipv6_literal_carrying_line_separator(mod):
    """ROUND 2 FINDING 1 (MAJOR, admitted). `ipaddress` accepts an arbitrary
    IPv6 scope id after `%`, and the round-1 fix's IP branch returned that
    canonical string BEFORE the ASCII/length/label bound in step 6 ever ran --
    so a scope id is not restricted to printable ASCII at all. U+2028 (LINE
    SEPARATOR) sits above U+007F, so the pre-`urlsplit` control-character
    check (which only rejects `ch < " "` and `\\x7f`) never sees it, and it
    would reach `JSON.stringify` in the template and then the harness's own
    `splitlines()`-based read of a job's last stdout line -- splitting THAT
    line and making the whole invocation fatal. FAILS against the driver from
    the previous round, which returned the scope id verbatim."""
    url = "http://[fe80::1% scope]/x"
    assert mod._safe_host(url) is None


def test_safe_host_rejects_a_scoped_ipv6_literal_with_an_oversized_scope(mod):
    """Same finding, the size side: a scope id has no length bound of its own
    in `ipaddress`, so an oversized one could approach `ARG_MAX` once it
    reaches a shelled command. A few hundred characters is enough to prove
    the bound applies; the reproduction that found this used 300 000+.
    FAILS against the driver from the previous round."""
    oversized_scope = "a" * 300  # well past the 253-char total-host bound
    url = f"http://[fe80::1%{oversized_scope}]/x"
    assert mod._safe_host(url) is None


def test_safe_host_still_accepts_a_plain_unscoped_ipv6_literal(mod):
    """The fix for the scoped case must not break what round 1 added: an
    ordinary, unscoped IPv6 literal still returns its canonical bracket-less
    form."""
    assert mod._safe_host("http://[2606:4700:4700::1111]/x") == "2606:4700:4700::1111"


@pytest.mark.parametrize("url", [
    "http://[fe80::1%251]/x",
    "http://[fe80::1%2501]/x",
    "http://[fe80::1%25001]/x",
], ids=["scope-251", "scope-2501", "scope-25001"])
def test_safe_host_rejects_every_scoped_ipv6_numeric_alias(mod, url):
    """ADMITTED 1 (round-4 P3). Before the fix, `_safe_host` accepted a scoped
    IPv6 literal's scope id verbatim (only bounding its size and ASCII-ness),
    so these three URLs -- which `getaddrinfo` resolves to the SAME endpoint
    -- each produced a DIFFERENT tally key ("fe80::1%251",
    "fe80::1%2501", "fe80::1%25001"). That distinct-keys-for-one-endpoint
    split is the defect, not merely that a scope id was accepted at all. The
    fix rejects any IPv6 literal with a non-empty scope id outright, so all
    three must now return None."""
    assert mod._safe_host(url) is None


def test_safe_host_rejects_three_scope_aliases_as_three_distinct_pre_fix_keys(mod):
    """States the defect's shape directly, rather than only its fixed
    outcome: if a regression ever re-admitted scope ids, the bug would show
    up exactly as these three URLs resolving to three DIFFERENT non-None
    values instead of the single None every one of them must return now.
    This asserts the post-fix invariant (all three collapse to the SAME
    outcome -- rejection) that a partial fix could violate by, say, bounding
    the scope id's syntax without banning it outright."""
    results = {mod._safe_host(u) for u in (
        "http://[fe80::1%251]/x", "http://[fe80::1%2501]/x",
        "http://[fe80::1%25001]/x")}
    assert results == {None}


def test_safe_host_rejects_a_scope_id_carrying_a_quote(mod):
    """A scope id can carry punctuation no DNS label may -- a quote character
    is exactly the kind of thing that would need escaping wherever this
    string is later interpolated, which the prompt's own untrusted-evidence
    framing does not by itself prevent for a stored dict KEY."""
    assert mod._safe_host('http://[fe80::1%qu"ote]/x') is None


def test_safe_host_rejects_a_scope_id_carrying_a_backslash(mod):
    assert mod._safe_host("http://[fe80::1%back\\slash]/x") is None


def test_safe_host_rejects_an_ordinary_benign_named_scope_too(mod):
    """The fix REJECTS THE CLASS -- any non-empty scope id -- rather than
    merely bounding or escaping a malicious-looking one. An entirely benign,
    real-world interface name must be rejected just the same, which is what
    distinguishes "rejected outright" from "sanitized"."""
    assert mod._safe_host("http://[fe80::1%eth0]/x") is None


def test_sanitize_host_refusals_drops_a_scoped_ipv6_key(mod):
    """The stored-state side of the same finding: a scoped-IPv6 key of that
    shape must not survive the sanitizer either. FAILS against the driver
    from the previous round, which kept this key."""
    key = "fe80::1% scope"
    assert mod.sanitize_host_refusals({key: {"403": 1}}) == {}


@pytest.mark.skipif(NODE is None, reason="node required")
def test_no_line_or_paragraph_separator_reaches_the_built_repair_prompt(mod):
    """Drives the REAL pipeline -- `host_refusals_from` then
    `repair_advisory_hosts` then the real node template's `batchRepairPrompt`
    -- and asserts on the BUILT STRING, not on the input: even if a future
    regression let a U+2028/U+2029-carrying host back out of `_safe_host`,
    this is the check that would catch it reaching the actual prompt text a
    codex job would read. Against the CURRENT (already fixed) driver,
    `_safe_host` itself refuses the malicious host before this pipeline ever
    runs, which is exactly why this must be an end-to-end check rather than a
    unit test of `_safe_host` alone."""
    established_row = [{"source_form": "A", "basis": "established",
                        "source": "http://[fe80::1% scope]/x"}]
    pairs = [{"item_index": 0, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, established_row, {0})
    advisory = mod.repair_advisory_hosts(tally, established_row)
    prompt = _repair_prompt(mod, host_advisory=advisory)
    assert " " not in prompt
    assert " " not in prompt


def test_safe_host_idna_encodes_a_non_ascii_host(mod):
    """FINDING 1. A non-ASCII host the fetcher itself would IDNA-encode before
    connecting must come back canonicalized the same way, not dropped --
    dropping it silently removed a real refusal from the tally. FAILS against
    the pre-fix driver, which returns None for any non-ASCII host."""
    assert mod._safe_host("https://bücher.de/x") == "xn--bcher-kva.de"


def test_safe_host_idna_reintroduced_trailing_dot_is_stripped_again(mod):
    """ROUND 2 FINDING 2 (MINOR, admitted). U+3002 IDEOGRAPHIC FULL STOP is
    one of several Unicode "full stop" look-alikes IDNA maps onto an ASCII
    `.` -- `fetch_citation.py`'s own validator accepts a source URL spelled
    this way and reaches the wire authority `xn--bcher-kva.de.` -- but the
    ONLY trailing-dot strip in the previous round ran BEFORE the IDNA
    encoding step, so the dot IDNA reintroduces survived into the label
    split, made the final label empty, and `_safe_host` returned None for a
    host the fetcher genuinely used. Both spellings must tally under the
    SAME host, or one host's refusals silently split into two entries. FAILS
    against the driver from the previous round, which returned None here."""
    assert mod._safe_host("https://bücher.de。/entry") == "xn--bcher-kva.de"
    assert mod._safe_host("https://bücher.de。/entry") == \
        mod._safe_host("https://bücher.de/entry")


def test_safe_host_still_rejects_a_host_whose_idna_encoding_raises(mod):
    """A single label of 60 non-ASCII characters is exactly the shape that
    makes Python's own `idna` codec raise UnicodeError ('label too long') --
    the fix must catch that and drop the host, not let the exception escape
    into a prompt-building call site that does not expect one."""
    label = "ü" * 60  # each char multi-byte in its punycode encoding
    assert mod._safe_host(f"https://{label}.de/x") is None


def test_safe_host_rejects_a_64_char_label(mod):
    label = "a" * 64  # one over the 63-char DNS label bound
    assert mod._safe_host(f"https://{label}.example.com/x") is None


def test_safe_host_accepts_a_63_char_label(mod):
    """The boundary's other side: 63 is the largest VALID label, so a bound that
    silently rejected it would be off by one in the wrong direction."""
    label = "a" * 63
    assert mod._safe_host(f"https://{label}.example.com/x") == f"{label}.example.com"


def test_safe_host_rejects_a_300_char_host(mod):
    host = ".".join(["a" * 60] * 5)  # 304 chars, over the 253 total bound
    assert len(host) > 253
    assert mod._safe_host(f"https://{host}/x") is None


def test_safe_host_rejects_an_empty_label(mod):
    assert mod._safe_host("https://a..b.com/x") is None


def test_safe_host_rejects_a_non_url_string(mod):
    assert mod._safe_host("not a url at all, just prose") is None


def test_safe_host_rejects_a_non_string_value(mod):
    assert mod._safe_host(12345) is None


def test_safe_host_rejects_none(mod):
    assert mod._safe_host(None) is None


def test_safe_host_rejects_the_empty_string(mod):
    assert mod._safe_host("") is None


def test_an_idn_host_403_reaches_the_repair_advisory_list(mod):
    """FINDING 1, end to end. Before the fix a non-ASCII host was dropped by
    `_safe_host` at the FIRST step of the pipeline, so a real refusal on that
    host never reached `host_refusals_from`'s tally at all -- this fails
    against the pre-fix driver for that reason, not because of anything
    downstream."""
    rows = [{"source_form": "Buch", "basis": "established",
            "source": "https://bücher.de/x"}]
    pairs = [{"item_index": 0, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, rows, {0})
    assert tally == {"xn--bcher-kva.de": {"403": 1}}
    result = mod.repair_advisory_hosts(tally, rows)
    assert result == [{"host": "xn--bcher-kva.de", "statuses": {"403": 1}}]


@pytest.mark.skipif(NODE is None, reason="node required")
def test_an_idn_host_403_reaches_the_built_repair_prompt(mod):
    """The same chain, one step further: the advisory list this driver builds
    must actually show up in the REAL template's rendered prompt text."""
    rows = [{"source_form": "Buch", "basis": "established",
            "source": "https://bücher.de/x"}]
    pairs = [{"item_index": 0, "outcome": "http_error:403"}]
    advisory = mod.repair_advisory_hosts(mod.host_refusals_from(pairs, rows, {0}), rows)
    prompt = _repair_prompt(mod, host_advisory=advisory)
    assert "xn--bcher-kva.de" in prompt
    assert "403 x 1" in prompt


# ---------------------------------------------------------------------------
# 2. Status reporting -- which HTTP statuses are worth telling the repair
#    agent about, and the outcome-string shapes that must never reach it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (403, True), (429, True), (500, True), (503, True), (599, True),
    (400, False), (401, False), (404, False), (410, False), (451, False),
    (402, False), (499, False), (600, False), (200, False), (301, False),
])
def test_is_reported_refusal_status(mod, status, expected):
    assert mod._is_reported_refusal_status(status) is expected


_ROW_A = {"source_form": "Alpha", "basis": "established",
         "source": "https://hosta.example/x"}


@pytest.mark.parametrize("outcome,reported", [
    ("http_error:403", True),
    ("http_error:429", True),
    ("http_error:500", True),
    ("http_error:503", True),
    ("http_error:400", False),
    ("http_error:401", False),
    ("http_error:404", False),
    ("http_error:410", False),
    ("http_error:451", False),
    ("fetched", False),
    ("refused:read-timeout", False),
    ("refused:connect-timeout", False),
    ("http_error:abc", False),   # non-numeric tail
    ("http_error:", False),      # empty tail
])
def test_host_refusals_from_reports_only_the_stated_statuses(mod, outcome, reported):
    pairs = [{"item_index": 0, "outcome": outcome}]
    tally = mod.host_refusals_from(pairs, [_ROW_A], {0})
    if reported:
        status = outcome.split(":", 1)[1]
        assert tally == {"hosta.example": {status: 1}}, \
            "a reported outcome must produce a non-zero, non-empty tally"
    else:
        assert tally == {}


# ---------------------------------------------------------------------------
# 3. `host_refusals_from` -- counting, dedup, and the exclusions.
# ---------------------------------------------------------------------------

def test_host_refusals_from_sums_two_rows_on_the_same_host_and_status(mod):
    rows = [
        {"source_form": "A", "basis": "established", "source": "https://hosta.example/1"},
        {"source_form": "B", "basis": "established", "source": "https://hosta.example/2"},
    ]
    pairs = [{"item_index": 0, "outcome": "http_error:403"},
             {"item_index": 1, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, rows, {0, 1})
    assert tally == {"hosta.example": {"403": 2}}


def test_host_refusals_from_keeps_two_statuses_on_one_host_apart(mod):
    rows = [
        {"source_form": "A", "basis": "established", "source": "https://hosta.example/1"},
        {"source_form": "B", "basis": "established", "source": "https://hosta.example/2"},
    ]
    pairs = [{"item_index": 0, "outcome": "http_error:403"},
             {"item_index": 1, "outcome": "http_error:503"}]
    tally = mod.host_refusals_from(pairs, rows, {0, 1})
    assert tally == {"hosta.example": {"403": 1, "503": 1}}


def test_host_refusals_from_drops_an_unsafe_host_without_dropping_its_siblings(mod):
    """A global IP literal is now a SAFE host (finding 1), so an oversized
    label is used here instead as the still-genuinely-unsafe example."""
    rows = [
        {"source_form": "A", "basis": "established",
         "source": f"http://{'a' * 64}.test/1"},
        {"source_form": "B", "basis": "established", "source": "https://hostb.example/2"},
    ]
    pairs = [{"item_index": 0, "outcome": "http_error:403"},
             {"item_index": 1, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, rows, {0, 1})
    assert tally == {"hostb.example": {"403": 1}}, \
        "row 1's tally must survive row 0's unsafe host being dropped"


def test_host_refusals_from_excludes_a_non_established_row(mod):
    pairs = [{"item_index": 0, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, [_ROW_A], set())
    assert tally == {}


def test_host_refusals_from_excludes_an_out_of_range_item_index(mod):
    pairs = [{"item_index": 5, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, [_ROW_A], {5})
    assert tally == {}


def test_host_refusals_from_excludes_a_non_dict_row(mod):
    pairs = [{"item_index": 0, "outcome": "http_error:403"}]
    tally = mod.host_refusals_from(pairs, ["not a row"], {0})
    assert tally == {}


def test_host_refusals_from_empty_input_gives_empty_dict(mod):
    assert mod.host_refusals_from([], [], set()) == {}


# ---------------------------------------------------------------------------
# ADMITTED 2 (round-4 P3). `read_outcome_pairs` assumed the index's JSON root
# is a dict and accepted a Boolean item_index. Two distinct bugs, tested
# separately, plus one end-to-end proof that fixing the read actually
# protects `host_refusals_from` downstream.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("root", [[], None, 42, "not an object"],
                        ids=["list", "null", "number", "string"])
def test_read_outcome_pairs_refuses_a_non_dict_root_as_a_controlled_error(mod, tmp_path, root):
    """Each of these is SYNTACTICALLY VALID JSON, so the existing
    `(OSError, ValueError)` catch around `json.load` never fires -- the root
    parses fine and only THEN reaches `.get()`. FAILS against the pre-fix
    driver with a bare `AttributeError` escaping this function entirely
    (list/None/int/str have no `.get`), not with the `DriverError` this
    function's own docstring promises every other refusal in it raises."""
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(root), encoding="utf-8")
    with pytest.raises(mod.DriverError):
        mod.read_outcome_pairs(index_path)


def test_read_outcome_pairs_skips_a_boolean_item_index(mod, tmp_path):
    """`isinstance(True, int)` is `True` in Python, so a forged
    `"item_index": true` used to pass the existing `isinstance(idx, int)`
    check and come back as pair `{"item_index": True, "outcome": ...}` --
    which then aliases row 1 wherever it is used as a list index (`True ==
    1`). FAILS against the pre-fix driver, which includes this entry."""
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"entries": [
        {"item_index": True, "outcome": "http_error:403"},
        {"item_index": 0, "outcome": "http_error:500"},
    ]}), encoding="utf-8")
    pairs = mod.read_outcome_pairs(index_path)
    assert pairs == [{"item_index": 0, "outcome": "http_error:500"}], (
        "the forged boolean entry must be skipped, not aliased to row 1")


def test_a_forged_boolean_item_index_cannot_attribute_a_refusal_to_row_1(mod, tmp_path):
    """End to end through the REAL `read_outcome_pairs` -> `host_refusals_from`
    pipeline (not a hand-built pairs list, which would prove nothing about
    the read itself). Row 1 is `hostb.example`; the only entry in the index
    claims `item_index: true`. FAILS against the pre-fix driver: `True in
    {0, 1}` is `True` (Python treats `True == 1` for set membership) and
    `rows[True]` is `rows[1]`, so the forged entry would attribute its 403 to
    `hostb.example` -- a row it has nothing to do with."""
    rows = [
        {"source_form": "A", "basis": "established", "source": "https://hosta.example/0"},
        {"source_form": "B", "basis": "established", "source": "https://hostb.example/1"},
    ]
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"entries": [
        {"item_index": True, "outcome": "http_error:403"},
    ]}), encoding="utf-8")
    pairs = mod.read_outcome_pairs(index_path)
    tally = mod.host_refusals_from(pairs, rows, {0, 1})
    assert tally == {}, "a forged boolean item_index must not reach either row's host"


# ---------------------------------------------------------------------------
# 4. Accumulation across fetch passes (round-1 MAJOR 1).
#
# This is the load-bearing regression test: a tally computed from the LAST
# pass alone returns {} here, because pass 2 is entirely clean. Only a tally
# that ACCUMULATES over every pass sees the 403 that pass 1 actually recorded.
# ---------------------------------------------------------------------------

def _scripted(passes_outcomes):
    calls = {"run_fetch": 0, "read_pairs": 0}

    def run_fetch():
        calls["run_fetch"] += 1
        return True

    def read_pairs():
        i = calls["read_pairs"]
        calls["read_pairs"] += 1
        return passes_outcomes[i]

    return run_fetch, read_pairs, calls


def test_host_refusals_accumulate_across_passes_not_just_the_last(mod):
    """Pass 1: row0 refuses with a reported host status (403), row1 times out
    (transient -- forces a second pass). Pass 2: both rows come back clean.
    `run_fetch` must run EXACTLY twice, and `host_refusals` must still hold
    pass 1's 403 even though the LAST pass alone reported nothing at all."""
    rows = [
        {"source_form": "A", "basis": "established", "source": "https://hosta.example/1"},
        {"source_form": "B", "basis": "established", "source": "https://hostb.example/2"},
    ]
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "http_error:403"},
         {"item_index": 1, "outcome": "refused:read-timeout"}],
        [{"item_index": 0, "outcome": "fetched"},
         {"item_index": 1, "outcome": "fetched"}],
    ])
    result = mod.fetch_until_stable(
        run_fetch, read_pairs, lambda: {0, 1},
        sleep=lambda delay: None, load_rows=lambda: rows)
    assert calls["run_fetch"] == 2, "a loop that ran zero times must not pass"
    assert result["ok"] is True
    assert result["host_refusals"] == {"hosta.example": {"403": 1}}, (
        "a tally taken from the last pass alone would be {} here -- pass 2 is "
        "entirely clean -- so this assertion fails against the pre-fix shape")


def test_host_refusals_is_empty_when_load_rows_is_not_given(mod):
    """`load_rows=None` (the existing default) must keep every currently passing
    call in tests/glossary_transient_fetch_retry.test.py untouched -- this
    asserts the new key's value on that path rather than its absence."""
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0},
                                    sleep=lambda delay: None)
    assert result["host_refusals"] == {}


# ---------------------------------------------------------------------------
# FINDING 3 (round-3 MINOR, admitted). A tally from an EARLIER pass must not
# be discarded when a LATER fetch command fails outright: the `ok: False`
# return happens because the COMMAND failed, not because pass 1's evidence
# stopped being true.
# ---------------------------------------------------------------------------

def test_host_refusals_survive_a_later_fetch_command_failure(mod):
    """Pass 1 reports a 403 on an established row plus a transient timeout on
    another (forcing pass 2). Pass 2's `run_fetch` itself returns False --
    the citation-fetch COMMAND failed, unrelated to what pass 1 already
    observed. FAILS against the pre-fix driver: its `{"ok": False, "passes":
    passes}` return carries no `host_refusals` key at all, so
    `result.get("host_refusals")` is `None`, not the pass-1 tally."""
    rows = [
        {"source_form": "A", "basis": "established", "source": "https://hosta.example/1"},
        {"source_form": "B", "basis": "established", "source": "https://hostb.example/2"},
    ]
    calls = {"run_fetch": 0, "read_pairs": 0}

    def run_fetch():
        calls["run_fetch"] += 1
        return calls["run_fetch"] == 1  # pass 1 succeeds, pass 2's command fails

    def read_pairs():
        calls["read_pairs"] += 1
        return [{"item_index": 0, "outcome": "http_error:403"},
                {"item_index": 1, "outcome": "refused:read-timeout"}]

    result = mod.fetch_until_stable(
        run_fetch, read_pairs, lambda: {0, 1},
        sleep=lambda delay: None, load_rows=lambda: rows)
    assert calls["run_fetch"] == 2, "a loop that ran zero times must not pass"
    assert calls["read_pairs"] == 1, "read_pairs must not be called after a failed fetch"
    assert result["ok"] is False
    assert result.get("host_refusals") == {"hosta.example": {"403": 1}}, (
        "pass 1's tally must survive even though the run overall did not")


def test_prepare_and_hand_back_merges_host_refusals_even_on_fetch_failed(mod, tmp_path):
    """The other half of finding 3: `prepare_and_hand_back` merges the fetch's
    tally into `state["hostRefusals"]` only on its OWN success path today --
    the merge sits AFTER the `if not fetch_state["ok"]: return ...`
    early-return, so even a driver that stopped discarding the tally inside
    `fetch_until_stable` (the test above) would still lose it one call frame
    up. FAILS against the pre-fix driver: `state` is never touched at all on
    the `fetch-failed` return.

    Fakes only the process-boundary collaborators a unit test cannot reach
    (the template harness and the shelled approve/fetch commands) -- the real
    `prepare_and_hand_back` and the real `fetch_until_stable` run underneath."""
    monkeypatched_sleep = {"calls": []}

    def _install_fake_sleep():
        # `fetch_until_stable`'s `sleep=time.sleep` default is bound at
        # driver-module LOAD time, so the real `time.sleep` must be replaced
        # BEFORE the module is executed for this test's own `mod` -- patching
        # it afterwards would not reach an already-captured default.
        time.sleep = lambda seconds: monkeypatched_sleep["calls"].append(seconds)

    original_sleep = time.sleep
    _install_fake_sleep()
    try:
        scripts = tmp_path / "durable2" / "scripts"
        scripts.mkdir(parents=True)
        m = load_driver(scripts)
    finally:
        time.sleep = original_sleep

    approved_path = tmp_path / "approved_0_attempt_0.json"
    approved_path.write_text(json.dumps([
        {"source_form": "A", "basis": "established", "disposition": "accepted",
         "source": "https://hosta.example/1"},
        {"source_form": "B", "basis": "established", "disposition": "accepted",
         "source": "https://hostb.example/2"},
    ]), encoding="utf-8")
    index_path = tmp_path / "index_0_attempt_0.json"

    def fake_call_template_functions(template_path, subst_, batches, calls, node_bin):
        out = {}
        for call in calls:
            fn = call["fn"]
            if fn == "approveBatchCmd":
                out[call["key"]] = "approve-cmd"
            elif fn == "approvedPath":
                out[call["key"]] = str(approved_path)
            elif fn == "fetchCitationsCmd":
                out[call["key"]] = "fetch-cmd"
            elif fn == "evidenceIndexPath":
                out[call["key"]] = str(index_path)
            elif fn == "citationJudgePrompt":
                out[call["key"]] = "unused"
            else:
                raise AssertionError(f"unexpected template call in this fake: {fn}")
        return out

    fetch_calls = {"n": 0}

    def fake_run_template_cmd(cmd, *, timeout):
        if cmd == "approve-cmd":
            return (0, "{}", "")
        if cmd == "fetch-cmd":
            fetch_calls["n"] += 1
            if fetch_calls["n"] == 1:
                index_path.write_text(json.dumps({"entries": [
                    {"item_index": 0, "outcome": "http_error:403"},
                    {"item_index": 1, "outcome": "refused:read-timeout"},
                ]}), encoding="utf-8")
                return (0, "", "")
            return (1, "", "simulated fetch command failure")
        raise AssertionError(f"unexpected command in this fake: {cmd!r}")

    m.call_template_functions = fake_call_template_functions
    m.run_template_cmd = fake_run_template_cmd

    ctx = m.Ctx(template=Path("/unused"), subst=subst(run_id="runX"),
               batches=[BATCH], node_bin="node", companion="unused.mjs",
               durable_root=tmp_path / "durable2", verdict_dir=tmp_path / "verdict2",
               research_mode="live", effort="high", poll_sec=0.01, deadline_sec=5.0,
               max_citation_retries=2, tmpdir=tmp_path)

    state = {}
    result = m.prepare_and_hand_back(ctx, BATCH, 0, tmp_path / "fragment_0_attempt_0.json",
                                     state)

    assert fetch_calls["n"] == 2, "a loop that ran zero times must not pass"
    assert result["state"] == "evidence_failed"
    assert result["reason"] == "fetch-failed"
    assert state.get("hostRefusals") == {"hosta.example": {"403": 1}}, (
        "the pass-1 tally must reach state['hostRefusals'] even though this "
        "attempt's fetch ultimately failed")


# ---------------------------------------------------------------------------
# 5. `merge_host_refusals` and `sanitize_host_refusals`.
# ---------------------------------------------------------------------------

def test_merge_host_refusals_adds_counts_for_the_same_host_and_status(mod):
    base = {"hosta.example": {"403": 2}}
    extra = {"hosta.example": {"403": 3}, "hostb.example": {"429": 1}}
    merged = mod.merge_host_refusals(base, extra)
    assert merged == {"hosta.example": {"403": 5}, "hostb.example": {"429": 1}}


def test_merge_host_refusals_never_mutates_either_input(mod):
    base = {"hosta.example": {"403": 2}}
    extra = {"hosta.example": {"403": 3}}
    base_before, extra_before = json.loads(json.dumps(base)), json.loads(json.dumps(extra))
    merged = mod.merge_host_refusals(base, extra)
    assert base == base_before
    assert extra == extra_before
    assert merged is not base
    assert merged is not extra


def test_sanitize_host_refusals_keeps_a_valid_document_unchanged(mod):
    value = {"hosta.example": {"403": 2, "503": 1}}
    assert mod.sanitize_host_refusals(value) == value


@pytest.mark.parametrize("value", [None, [], "a string", 42, True])
def test_sanitize_host_refusals_never_raises_on_the_wrong_shape(mod, value):
    assert mod.sanitize_host_refusals(value) == {}


def test_sanitize_host_refusals_drops_a_host_key_that_is_not_already_normalized(mod):
    """The key must already be the SAME string `_safe_host` would produce from
    it -- an un-normalized key (here, upper-case) is not a bare host this
    driver itself could have written, so it is dropped rather than silently
    re-normalized."""
    value = {"EXAMPLE.com": {"403": 1}}
    assert mod.sanitize_host_refusals(value) == {}


def test_sanitize_host_refusals_drops_an_unsafe_host_key(mod):
    """A global IP literal is now a SAFE host (finding 1) and would itself be a
    valid key here, so an empty-label host is used as the still-genuinely-
    unsafe example instead."""
    value = {"a..b": {"403": 1}}
    assert mod.sanitize_host_refusals(value) == {}


def test_safe_host_returns_an_ipv6_literal_bracket_less(mod):
    """Pins the exact spelling the round-trip test below depends on: an IPv6
    literal comes back WITHOUT its brackets."""
    assert mod._safe_host("http://[2606:4700:4700::1111]/x") == "2606:4700:4700::1111"


def test_sanitize_host_refusals_keeps_an_ipv6_key(mod):
    """The defect FINDING 1's OWN FIX introduces, so it is the one most likely
    to be missed. `_safe_host` returns an IPv6 literal bracket-LESS (see the
    test above), and that bracket-less spelling is exactly what
    `host_refusals_from` would have stored as the key. But
    `sanitize_host_refusals` validates a key by round-tripping it through
    `_safe_host("https://" + key)` -- and `https://2606:4700:4700::1111`
    parses `2606` as the HOST with the rest read as a bogus port, not as that
    address. A bare-form-only round-trip check would therefore silently drop
    every IPv6 host from the tally on the very next load: not a crash, a
    quiet data loss. The fix must also try the BRACKETED spelling
    (`https://[` + key + `]`) as an alternative round-trip."""
    value = {"2606:4700:4700::1111": {"403": 2}}
    assert mod.sanitize_host_refusals(value) == value


def test_sanitize_host_refusals_drops_a_non_dict_value(mod):
    value = {"hosta.example": "not a dict"}
    assert mod.sanitize_host_refusals(value) == {}


def test_sanitize_host_refusals_drops_a_status_key_that_is_not_digits(mod):
    value = {"hosta.example": {"abc": 1}}
    assert mod.sanitize_host_refusals(value) == {}


def test_sanitize_host_refusals_drops_a_digit_status_key_that_is_not_reported(mod):
    """"404" is digits, but 404 is not in the reported set -- the same
    restriction _is_reported_refusal_status enforces everywhere else."""
    value = {"hosta.example": {"404": 1}}
    assert mod.sanitize_host_refusals(value) == {}


@pytest.mark.parametrize("count", [0, -1, "2", 1.5, True, False])
def test_sanitize_host_refusals_drops_a_non_positive_int_count(mod, count):
    """`True`/`False` are `int` in Python but must NOT count as one here."""
    value = {"hosta.example": {"403": count}}
    assert mod.sanitize_host_refusals(value) == {}


def test_sanitize_host_refusals_keeps_a_good_status_beside_a_bad_one(mod):
    value = {"hosta.example": {"403": 2, "abc": 1, "404": 5, "503": 0}}
    assert mod.sanitize_host_refusals(value) == {"hosta.example": {"403": 2}}


def test_sanitize_host_refusals_drops_a_host_whose_every_status_is_bad(mod):
    value = {"hosta.example": {"abc": 1, "404": 5},
             "hostb.example": {"403": 1}}
    assert mod.sanitize_host_refusals(value) == {"hostb.example": {"403": 1}}


# ---------------------------------------------------------------------------
# FINDING 2 (round-3 MAJOR, admitted). `str.isdigit()` is true for strings
# `int()` refuses (a superscript, a non-ASCII digit spelling) or that `int()`
# refuses to convert past a length guard -- and the pre-fix code calls
# `int(status)` with nothing catching the ValueError, so the exception
# escapes `sanitize_host_refusals` entirely. `drive_all` only catches
# `DriverError`, so an operator's hand-edited (or merely differently
# generated) state document kills the whole invocation. EVERY case below
# must come back with the ENTRY DROPPED and NO EXCEPTION RAISED -- for the
# first three, "no exception" is the whole bug, since the pre-fix code
# raises before it can even return the wrong thing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", [
    "²",        # superscript two -- .isdigit() is True, int() raises ValueError
    "5" * 5000,      # exceeds Python's int() digit-conversion limit -- int() raises
], ids=["superscript-two", "5000-ascii-digits"])
def test_sanitize_host_refusals_never_raises_on_a_status_int_cannot_parse(mod, bad_status):
    """FAILS against the pre-fix driver with an uncaught ValueError escaping
    the call -- not with a wrong return value. `drive_all` catches only
    `DriverError`, so this crash is not a symptom the driver's own error
    handling would ever contain."""
    assert mod.sanitize_host_refusals({"hosta.example": {bad_status: 1}}) == {}


def test_sanitize_host_refusals_drops_a_non_ascii_digit_spelling(mod):
    """"٤٢٩" is the Arabic-Indic spelling of 429: `.isdigit()` is True AND
    `int()` happily parses it to 429, so the pre-fix code ACCEPTS it -- and
    keeps the ORIGINAL non-ASCII string as the dict key, which is exactly the
    key the template's own `/^[0-9]+$/` guard would then silently drop on the
    other side of the wire. This does not raise pre-fix; it returns the wrong
    (accepting) answer, which is why this is a separate test from the raising
    cases above."""
    assert mod.sanitize_host_refusals({"hosta.example": {"٤٢٩": 1}}) == {}


def test_sanitize_host_refusals_drops_a_leading_zero_spelling(mod):
    """"0403" also parses to 403 under `int()`, so the pre-fix code accepts it
    and keeps "0403" -- not "403" -- as the stored key. The accepted spellings
    after the fix are exactly "403", "429", and "5" + two ASCII digits, none
    of which is "0403"."""
    assert mod.sanitize_host_refusals({"hosta.example": {"0403": 1}}) == {}


def test_sanitize_host_refusals_keeps_a_good_neighbour_beside_a_raising_one(mod):
    """The neighbour-survival property FINDING 2 asks for. Pre-fix, processing
    "²" raises ValueError partway through this ONE host's statuses dict,
    which loses "403" too -- not because "403" was judged bad, but because the
    whole call never returns. This must both NOT RAISE and keep "403"."""
    value = {"hosta.example": {"403": 2, "²": 9}}
    assert mod.sanitize_host_refusals(value) == {"hosta.example": {"403": 2}}


# ---------------------------------------------------------------------------
# 6. `repair_advisory_hosts` -- ordering, and the cap that never touches the
#    current repair's own failing hosts (round-1 MAJOR 2, round-2 MAJOR).
# ---------------------------------------------------------------------------

def test_repair_advisory_hosts_never_caps_the_current_repairs_lowest_host(mod):
    """12 historical hosts, and the CURRENT failed row's host is the one with
    the LOWEST count -- an ordinary count-desc cap would drop it. It must
    still appear, because it is the host the agent is about to re-pick."""
    tally = {f"h{i}.test": {"403": 12 - i} for i in range(12)}  # h0=12 .. h11=1
    failed_rows = [{"source": "https://h11.test/x"}]
    result = mod.repair_advisory_hosts(tally, failed_rows, limit=10)
    hosts = [entry["host"] for entry in result]
    assert "h11.test" in hosts, (
        "a plain count-desc cap would drop this host -- it is the current "
        "repair's own failing host and must never be truncated away")
    assert len(hosts) == 11, "current host + top 10 historical hosts by count"


def test_repair_advisory_hosts_lists_every_current_host_uncapped(mod):
    """11 distinct hosts fail in THIS repair alone -- one more than the
    historical cap. This must fail if the ten-host cap is applied to the
    current repair's own hosts: capping at 10 would drop exactly one of them."""
    failed_rows = [{"source": f"https://c{i}.test/x"} for i in range(11)]
    tally = {f"c{i}.test": {"403": 1} for i in range(11)}
    result = mod.repair_advisory_hosts(tally, failed_rows, limit=10)
    hosts = {entry["host"] for entry in result}
    assert hosts == {f"c{i}.test" for i in range(11)}
    assert len(result) == 11


def test_repair_advisory_hosts_caps_the_historical_remainder_at_the_limit(mod):
    """One current host, 30 unrelated historical hosts: the list holds the one
    current host plus at most `limit` further historical ones -- 11 total."""
    failed_rows = [{"source": "https://c0.test/x"}]
    tally = {"c0.test": {"403": 1}}
    tally.update({f"h{i}.test": {"403": 30 - i} for i in range(30)})
    result = mod.repair_advisory_hosts(tally, failed_rows, limit=10)
    assert len(result) == 11
    hosts = {entry["host"] for entry in result}
    assert hosts == {"c0.test"} | {f"h{i}.test" for i in range(10)}


def test_repair_advisory_hosts_empty_tally_gives_empty_list(mod):
    assert mod.repair_advisory_hosts({}, [{"source": "https://c0.test/x"}]) == []


def test_repair_advisory_hosts_orders_by_count_desc_then_host_asc(mod):
    tally = {"zzz.test": {"403": 5}, "aaa.test": {"403": 5}, "mmm.test": {"403": 9}}
    result = mod.repair_advisory_hosts(tally, [])
    assert [e["host"] for e in result] == ["mmm.test", "aaa.test", "zzz.test"]


def test_repair_advisory_hosts_does_not_alias_the_input_tally(mod):
    """FINDING 4 (NIT, admitted). The pre-fix code returns `tally[host]`
    itself as `statuses` rather than a copy, so a caller mutating its own
    tally afterwards -- exactly what `merge_host_refusals`'s docstring
    promises callers may freely do -- silently rewrites an already-returned
    advisory entry. FAILS against the pre-fix driver: mutating `tally` after
    the call changes `result[0]["statuses"]` too."""
    tally = {"hosta.example": {"403": 2}}
    result = mod.repair_advisory_hosts(tally, [{"source": "https://hosta.example/x"}])
    tally["hosta.example"]["403"] = 999
    tally["hosta.example"]["999"] = 1
    assert result[0]["statuses"] == {"403": 2}, (
        "the returned entry must not be a live reference into the input tally")


# ---------------------------------------------------------------------------
# 7. `batchRepairPrompt`'s advisory paragraph, through the REAL template.
# ---------------------------------------------------------------------------

_REPAIR_ROW = [{"source_form": "Alpha", "basis": "established",
               "disposition": "accepted", "source": "https://dead.test/a"}]


def _repair_prompt(mod, *, host_advisory="__omit__"):
    args = [BATCH, 0, _REPAIR_ROW, "/private/tmp/ltgd.x/repair_0_attempt_0.json",
            "unretrievable"]
    if host_advisory != "__omit__":
        args.append(host_advisory)
    out = mod.call_template_functions(
        TEMPLATE, subst(), [BATCH],
        [{"key": "p", "fn": "batchRepairPrompt", "args": args}], NODE)
    return out["p"]


@pytest.mark.skipif(NODE is None, reason="node required")
def test_advisory_paragraph_names_host_status_and_count(mod):
    advisory = [{"host": "www.chabad.org", "statuses": {"403": 13, "503": 2}}]
    prompt = _repair_prompt(mod, host_advisory=advisory)
    assert "www.chabad.org" in prompt
    assert "403 x 13" in prompt
    assert "503 x 2" in prompt


@pytest.mark.skipif(NODE is None, reason="node required")
def test_advisory_paragraph_names_the_list_as_untrusted(mod):
    advisory = [{"host": "www.chabad.org", "statuses": {"403": 1}}]
    prompt = _repair_prompt(mod, host_advisory=advisory)
    assert "UNTRUSTED TEXT" in prompt


@pytest.mark.skipif(NODE is None, reason="node required")
def test_empty_advisory_argument_is_byte_identical_to_no_argument_at_all(mod):
    """THE VACUITY GUARD. Comparing an EMPTY advisory argument against the SAME
    call with the argument OMITTED ENTIRELY -- rather than comparing either one
    against a separately hand-typed baseline -- is what proves the paragraph is
    truly ABSENT rather than merely harmless (e.g. an empty line, or a line that
    happens not to contain a test's chosen substring)."""
    with_empty = _repair_prompt(mod, host_advisory=[])
    without_arg = _repair_prompt(mod)  # host_advisory omitted -- "__omit__" sentinel
    assert with_empty == without_arg


@pytest.mark.skipif(NODE is None, reason="node required")
def test_null_and_non_array_advisory_are_also_byte_identical_to_absent(mod):
    without_arg = _repair_prompt(mod)
    assert _repair_prompt(mod, host_advisory=None) == without_arg
    assert _repair_prompt(mod, host_advisory="not an array") == without_arg


@pytest.mark.skipif(NODE is None, reason="node required")
def test_a_malformed_entry_is_skipped_without_poisoning_a_good_one(mod):
    advisory = [
        {"host": "", "statuses": {"403": 1}},              # empty host
        {"host": "bad.test", "statuses": "not an object"},  # bad statuses
        {"host": "bad2.test", "statuses": {"403": 0}},       # non-positive count
        {"host": "good.test", "statuses": {"429": 4}},
    ]
    prompt = _repair_prompt(mod, host_advisory=advisory)
    assert "good.test" in prompt
    assert "429 x 4" in prompt
    assert "bad.test" not in prompt
    assert "bad2.test" not in prompt


@pytest.mark.skipif(NODE is None, reason="node required")
def test_every_entry_malformed_is_byte_identical_to_absent(mod):
    without_arg = _repair_prompt(mod)
    all_bad = [{"host": "", "statuses": {"403": 1}}, {"host": "x", "statuses": {}}]
    assert _repair_prompt(mod, host_advisory=all_bad) == without_arg


# ---------------------------------------------------------------------------
# 8. `run_repair` forwards the tally it was given, at the sequence level.
#
# Mirrors tests/glossary_driver_sequence.test.py's posture: drive the REAL
# `run_repair()`, and fake only the process-boundary collaborators (codex
# launch, the artifact wait, the template harness, and the check-batch
# command) that a unit test over a pure function cannot reach. What is NOT
# faked is the actual call shape `run_repair` builds for `batchRepairPrompt`.
# ---------------------------------------------------------------------------

_REPAIR_FRAGMENT_NAME = "repair_0_attempt_1.json"


def test_run_repair_forwards_host_advisory_as_the_sixth_prompt_argument(mod, tmp_path):
    captured = {}

    def fake_call_template_functions(template_path, subst_, batches, calls, node_bin):
        out = {}
        for call in calls:
            fn = call["fn"]
            if fn == "repairFragmentPath":
                out[call["key"]] = f"/unused/{_REPAIR_FRAGMENT_NAME}"
            elif fn == "fragmentPath":
                out[call["key"]] = str(tmp_path / "next_fragment.json")
            elif fn == "checkBatchCmd":
                out[call["key"]] = "true"
            elif fn == "batchRepairPrompt":
                captured["args"] = call["args"]
                out[call["key"]] = "--background\nrepair the thing"
            else:
                raise AssertionError(f"unexpected template call in this fake: {fn}")
        return out

    def fake_launch_codex(*, companion, node_bin, prompt, effort, sandbox_root,
                          tmpdir, label):
        return "job-fake"

    def fake_wait_for_artifact(ctx, *, ready, job_id, sandbox_root, label):
        (sandbox_root / _REPAIR_FRAGMENT_NAME).write_text(
            json.dumps([{"source_form": "Alpha", "basis": "established",
                        "disposition": "accepted",
                        "source": "https://replacement.example/a"}]),
            encoding="utf-8")
        return {"ready": True}

    def fake_cmd_ok(cmd, timeout):
        return True

    mod.call_template_functions = fake_call_template_functions
    mod.launch_codex = fake_launch_codex
    mod.wait_for_artifact = fake_wait_for_artifact
    mod._cmd_ok = fake_cmd_ok

    ctx = mod.Ctx(template=TEMPLATE, subst=subst(), batches=[BATCH], node_bin="node",
                 companion="unused.mjs", durable_root=tmp_path / "durable",
                 verdict_dir=tmp_path / "verdict", research_mode="live",
                 effort="high", poll_sec=0.01, deadline_sec=5.0,
                 max_citation_retries=2, tmpdir=tmp_path)

    snapshot_path = tmp_path / "approved_0_attempt_0.json"
    snapshot_path.write_text(json.dumps(
        [{"source_form": "Alpha", "basis": "established", "disposition": "accepted",
          "source": "https://dead.test/a"}]), encoding="utf-8")

    host_advisory = [{"host": "www.chabad.org", "statuses": {"403": 13}}]

    result = mod.run_repair(ctx, BATCH, 0, [0], snapshot_path,
                            cause="unretrievable", host_advisory=host_advisory)

    assert result["state"] == "repaired"
    assert "args" in captured, "batchRepairPrompt must actually have been built"
    args = captured["args"]
    assert len(args) == 6, (
        "batchRepairPrompt must be called with exactly six positional "
        f"arguments once host_advisory is wired through; got {len(args)}")
    assert args[4] == "unretrievable", "cause stays the fifth argument"
    assert args[5] == host_advisory, "host_advisory must be the sixth argument"


# ---------------------------------------------------------------------------
# 9. State round-trip: `hostRefusals` survives save/load; a pre-release
#    document (no such key) loads unchanged; a document for another run is
#    still reset in full.
# ---------------------------------------------------------------------------

@pytest.fixture
def state_project(tmp_path):
    durable = tmp_path / "durable"
    (durable / "scripts").mkdir(parents=True)
    (durable / "glossary" / "runs" / "run1").mkdir(parents=True)
    session = tmp_path / "session"
    return {"durable": durable, "session": session,
            "mod": load_driver(durable / "scripts")}


def test_host_refusals_survives_save_and_load(state_project):
    m = state_project["mod"]
    d = m.resolve_verdict_dir(str(state_project["session"]), state_project["durable"])
    state = m.fresh_state(state_project["durable"], "run1")
    state["hostRefusals"] = {"www.chabad.org": {"403": 13, "503": 2}}
    m.save_state(d, state)
    assert m.load_state(d, state_project["durable"], "run1") == state


def test_a_document_written_before_this_release_loads_unchanged(state_project):
    """No `hostRefusals` key at all -- STATE_VERSION was NOT bumped for this
    feature, so an in-flight run's document from before this release must load
    exactly as it always has, with no key manufactured for it."""
    m = state_project["mod"]
    d = m.resolve_verdict_dir(str(state_project["session"]), state_project["durable"])
    state = m.fresh_state(state_project["durable"], "run1")
    state["batches"]["0"] = {"attempt": 0, "status": "awaiting_judge",
                             "pending": {"nonce": "n0", "snapshot_sha256": "abc"}}
    assert "hostRefusals" not in state
    m.save_state(d, state)
    loaded = m.load_state(d, state_project["durable"], "run1")
    assert loaded == state
    assert "hostRefusals" not in loaded


def test_state_for_another_run_resets_host_refusals_too(state_project):
    m = state_project["mod"]
    d = m.resolve_verdict_dir(str(state_project["session"]), state_project["durable"])
    state = m.fresh_state(state_project["durable"], "runA")
    state["hostRefusals"] = {"www.chabad.org": {"403": 5}}
    m.save_state(d, state)
    reloaded = m.load_state(d, state_project["durable"], "runB")
    assert reloaded.get("hostRefusals") in (None, {}), (
        "a document from another run must not leak its host tally forward")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
