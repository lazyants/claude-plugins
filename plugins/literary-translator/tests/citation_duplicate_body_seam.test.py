"""The SEAM between the citation fetcher and the glossary driver (#918).

`fetch_citation.py` writes an `index.json`; `glossary_dispatch_driver.py` reads
one. Nothing else joins them -- no shared module, no schema, no import. The
duplicate-body feature was built on both sides of that join at once, and each
side's own suite passes against its OWN hand-built fixtures: the fetcher's tests
read the index they just produced and never show it to the driver, while the
driver's tests construct outcome pairs by hand and never run the fetcher. Both
suites would stay green with the wire contract broken -- a renamed token, an
`item_index` that stopped being the snapshot position, an outcome the driver
classifies into the wrong branch.

So this file holds the one test neither side can write: it runs the REAL
`run_batch()` over a batch whose sources are served by the fetcher suite's own
fake network, then hands the REAL `index.json` it wrote to the REAL
`read_outcome_pairs()`, `classify_outcomes()` and `duplicate_body_indices()`.
Nothing in between is faked, retyped or re-derived.

The fetcher's own test module is IMPORTED rather than its helpers copied. A copy
is a second fixture that drifts: this test must fail when the fetcher's fake
transport stops matching the fetcher, which is precisely what a private copy of
it would hide.
"""

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fc = _load("fetch_citation_seam", SCRIPTS / "fetch_citation.py")
gdd = _load("glossary_driver_seam", SCRIPTS / "glossary_dispatch_driver.py")
# The producer's own test module, for its fake transport and its snapshot
# helpers. Loaded, never copied -- see this file's docstring.
fct = _load("fetch_citation_test_helpers",
            PLUGIN_ROOT / "tests" / "fetch_citation.test.py")


SHELL = fct.http_response(200, {"Content-Type": "text/html; charset=utf-8"},
                          b"<html><body><div id=root></div></body></html>")
REAL_PAGE = fct.http_response(200, {"Content-Type": "text/html; charset=utf-8"},
                              b"<html><body><p>Odessa, a port city.</p></body></html>")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Refuse a real connection for every test here.

    The producer's suite installs this as an autouse fixture of its OWN module,
    which does not reach this file. Without it a fake that failed to patch would
    reach the network and the test would pass for the wrong reason.
    """
    import socket

    def _no(*args, **kwargs):
        raise AssertionError("a test in this file attempted a real connection")

    monkeypatch.setattr(socket, "create_connection", _no)
    monkeypatch.setattr(socket, "getaddrinfo", _no)


def _run_and_read(tmp_path, monkeypatch, snapshot, routes):
    """Drive the real fetcher over `snapshot`, return the index.json it wrote."""
    fct.FakeNet(monkeypatch, routes=routes)
    path = fct.write_snapshot(tmp_path, snapshot)
    out = tmp_path / "evidence"
    assert fc.run_batch(path, out) == 0
    return json.loads((out / "index.json").read_text(encoding="utf-8"))


def test_a_shell_the_fetcher_flagged_reaches_the_driver_as_a_repairable_duplicate(
        tmp_path, monkeypatch, capsys):
    """The whole seam, end to end, with nothing faked between the two sides."""
    snapshot = [
        fct.accepted("Odessa", "https://shell.example/article/25"),
        fct.accepted("Uman", "https://shell.example/article.aspx/uman"),
        fct.accepted("Berdychiv", "https://real.example/berdychiv"),
    ]
    index = _run_and_read(tmp_path, monkeypatch, snapshot, routes={
        ("shell.example", "/article/25"): SHELL,
        ("shell.example", "/article.aspx/uman"): SHELL,
        ("real.example", "/berdychiv"): REAL_PAGE,
    })
    capsys.readouterr()

    # The producer really did flag them -- asserted here so a later failure
    # cannot be blamed on a fetcher that quietly stopped flagging.
    outcomes = {e["item_index"]: e["outcome"] for e in index["entries"]}
    assert outcomes == {0: "unusable:duplicate-body",
                        1: "unusable:duplicate-body",
                        2: "fetched"}

    index_path = tmp_path / "evidence" / "index.json"
    pairs = gdd.read_outcome_pairs(index_path)
    # The driver's reader keeps the producer's positions and tokens intact.
    assert {p["item_index"] for p in pairs} == {0, 1, 2}

    established = {0, 1, 2}
    classified = gdd.classify_outcomes(pairs, established)
    assert classified == {"budget_failed": [], "repairable": [0, 1]}, (
        "the fetcher's token must land in the repair branch -- if it ever lands "
        "in budget_failed the batch reports an environment fault it never had, "
        "and if it lands nowhere a judge is spent on a body nobody can use")
    assert gdd.duplicate_body_indices(pairs, set(classified["repairable"])) == [0, 1], (
        "the driver must be able to say WHICH repairable rows are duplicates, "
        "or the repair prompt cannot tell the agent the truth about them")
    assert gdd.transient_indices(pairs, established) == [], (
        "a duplicate body is not a transport fault -- retrying the same fetch "
        "would re-retrieve the same shell and spend the retry ladder on it")


def test_a_batch_with_no_duplicate_reaches_the_driver_exactly_as_before(
        tmp_path, monkeypatch, capsys):
    """The control. Without it the test above proves only that SOMETHING is
    routed, not that the routing is caused by the duplicate."""
    snapshot = [
        fct.accepted("Odessa", "https://real.example/odessa"),
        fct.accepted("Uman", "https://real.example/uman"),
    ]
    index = _run_and_read(tmp_path, monkeypatch, snapshot, routes={
        ("real.example", "/odessa"): REAL_PAGE,
        ("real.example", "/uman"): fct.http_response(
            200, {"Content-Type": "text/html; charset=utf-8"},
            b"<html><body><p>Uman, a town.</p></body></html>"),
    })
    capsys.readouterr()

    assert [e["outcome"] for e in index["entries"]] == ["fetched", "fetched"]
    assert "unusable" not in index["counts"], (
        "a batch with no duplicate must keep the exact three-key counts dict it "
        "emitted before this feature existed")

    pairs = gdd.read_outcome_pairs(tmp_path / "evidence" / "index.json")
    assert gdd.classify_outcomes(pairs, {0, 1}) == {"budget_failed": [], "repairable": []}
    assert gdd.duplicate_body_indices(pairs, {0, 1}) == []


def test_the_driver_constant_is_the_token_and_is_in_no_other_branch():
    """The CONSUMER's half of the wire contract.

    The producer's half is already pinned by the first test in this file, which
    asserts the literal against an `index.json` the fetcher really wrote -- so a
    coordinated rename of both sides goes red there, and this test does not
    repeat that fetch. What it catches alone is narrower and real:
    `_DUPLICATE_BODY_OUTCOME` drifting away from the token while
    `duplicate_body_indices()` still matches, which would leave the constant
    decorative and the driver silently recognising nothing.

    The rest is where the token must NOT be. Each of the three is a different
    silent failure: equal to `_FETCH_OK` and every duplicate is treated as a
    clean retrieval; inside the shared-budget set and the row is never repaired
    at all, because a fresh URL cannot fix a run that ran out of budget; read as
    transient and the driver re-runs the same fetch, which returns the same
    shell and spends the retry ladder on it.

    (An earlier version of this test looked for the literal in the fetcher's
    SOURCE. That was vacuous: the literal also sits in the module docstring's
    "Outcomes recorded per item" line, so the writer could be renamed while the
    docstring alone kept the assertion green.)
    """
    assert gdd._DUPLICATE_BODY_OUTCOME == "unusable:duplicate-body"
    assert gdd._DUPLICATE_BODY_OUTCOME != gdd._FETCH_OK
    assert gdd._DUPLICATE_BODY_OUTCOME not in gdd._SHARED_BUDGET_OUTCOMES, (
        "a duplicate body is a fact about the citation, not about this run's "
        "shared budget -- in that set it would never be repaired at all")
    assert not gdd.is_transient_fetch_outcome(gdd._DUPLICATE_BODY_OUTCOME)
