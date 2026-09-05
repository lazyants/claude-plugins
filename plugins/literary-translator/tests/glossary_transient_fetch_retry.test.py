#!/usr/bin/env python3
"""`fetch_until_stable()`: a transient network fault must not be charged to the
citation (#853).

Before this, every established row that failed for ANY reason but the shared
budget was sent straight to `repairable` -- so a fetch pass that landed inside
a network outage burned a rung re-picking sources that were never at fault,
and the merge being all-or-nothing cost the whole batch. `fetch_until_stable`
re-runs the SAME fetch over the SAME pinned snapshot, up to
`len(_FETCH_RETRY_DELAYS_SEC)` extra times, and only classifies once no
established row is still failing at the transport layer or the ladder is
spent.

Two properties fail SILENTLY if they regress, which is why they are pinned
here rather than left to the driver's own logging:

  1. THE VOCABULARY IS EXACTLY WHAT #853 MEASURED. A cut, truncated or
     unresolvable exchange retries; an answer we received and refused on its
     merits does not. `_TRANSIENT_FETCH_OUTCOMES` /
     `_SHARED_BUDGET_OUTCOMES` must stay disjoint -- the shared-budget branch
     is never retried AND never repaired, for a different reason (a hostile
     server can spend that budget on another row's behalf).

  2. THE LOOP ACTUALLY RUNS. A retry ladder that never iterates would report
     the exact shape a working one reports on a clean pass -- these tests
     assert the exact `run_fetch` call count so a no-op loop cannot pass.

No network, no subprocess, no real sleep: `run_fetch`, `read_pairs` and
`sleep` are injected fakes.
"""

import importlib.util
import shutil
import socket
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DRIVER = (PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
          / "glossary_dispatch_driver.py")
JSON_STDOUT = DRIVER.parent / "json_stdout.py"


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    # json_stdout.py is the driver's one hard sibling dependency: it is loaded
    # by exact path at import time and the driver exits without it, exactly as
    # a deployed copy does. Staging it keeps this fixture a real scripts/ dir.
    shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location("gdd_transient_retry", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scripted(passes_outcomes):
    """Builds a `(run_fetch, read_pairs, calls)` triple driven by a script.

    `passes_outcomes[i]` is the pairs list `read_pairs()` returns for the i-th
    pass; the fetch command always exits zero here. The one test that needs a
    NON-ZERO exit hand-rolls its own closures, because it also needs
    `read_pairs` to fail loudly if the short circuit does not hold."""
    calls = {"run_fetch": 0, "read_pairs": 0}

    def run_fetch():
        calls["run_fetch"] += 1
        return True

    def read_pairs():
        i = calls["read_pairs"]
        calls["read_pairs"] += 1
        return passes_outcomes[i]

    return run_fetch, read_pairs, calls


def _fake_sleep(record):
    def sleep(delay):
        record.append(delay)
    return sleep


# ---------------------------------------------------------------------------
# 1. The clean and the transient-then-clean case
# ---------------------------------------------------------------------------

def test_clean_first_pass_calls_run_fetch_exactly_once(mod):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["ok"] is True
    assert result["passes"] == 1
    assert result["classified"] == {"budget_failed": [], "repairable": []}


def test_transient_failure_retries_and_stops_as_soon_as_a_pass_is_clean(mod):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "refused:connect-timeout"}],
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    delays = []
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep(delays))
    assert calls["run_fetch"] == 2, "a loop that ran zero times must not pass"
    assert result["passes"] == 2
    assert result["classified"] == {"budget_failed": [], "repairable": []}
    assert delays == [mod._FETCH_RETRY_DELAYS_SEC[0]]


def test_persistent_transient_failure_uses_exactly_three_passes(mod):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 3
    assert result["passes"] == 3
    assert result["classified"] == {"budget_failed": [], "repairable": [0]}


# ---------------------------------------------------------------------------
# 2. Outcomes that must NOT retry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outcome", ["http_error:403", "refused:content-type-not-allowed"])
def test_non_transient_refusals_do_not_retry(mod, outcome):
    """An answer we received and refused on its merits is a fact about the
    citation, not the link -- it must reach `repairable` on the first pass."""
    run_fetch, read_pairs, calls = _scripted([[{"item_index": 0, "outcome": outcome}]])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["classified"] == {"budget_failed": [], "repairable": [0]}


def test_shared_budget_outcome_alone_does_not_retry_and_stays_budget_failed(mod):
    run_fetch, read_pairs, calls = _scripted([[{"item_index": 0, "outcome": "refused:batch-deadline"}]])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["classified"] == {"budget_failed": [0], "repairable": []}


# ---------------------------------------------------------------------------
# 3. DNS -- every fixture derived from `socket` at test time, never a literal.
#
# `dns-failure:<errno>` carries `socket.gaierror.errno`, the PLATFORM's own
# EAI value: measured, EAI_AGAIN is 2 on Darwin and -3 on glibc. A hard-coded
# number in this test would read as correct on the machine it was written on
# and silently assert nothing -- retry everything or nothing -- on the other.
# ---------------------------------------------------------------------------

def test_dns_again_retries(mod):
    outcome = f"refused:dns-failure:{socket.EAI_AGAIN}"
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": outcome}],
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 2
    assert result["classified"] == {"budget_failed": [], "repairable": []}


@pytest.mark.parametrize("outcome", [
    f"refused:dns-failure:{socket.EAI_NONAME}",  # the name does not exist -- a fact about the URL
    "refused:dns-empty",                          # getaddrinfo answered, with nothing
])
def test_dns_nonname_and_empty_do_not_retry(mod, outcome):
    run_fetch, read_pairs, calls = _scripted([[{"item_index": 0, "outcome": outcome}]])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["classified"] == {"budget_failed": [], "repairable": [0]}


# ---------------------------------------------------------------------------
# 4. TLS and HTTP-protocol families -- transient except one certificate fault.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outcome", [
    "refused:tls-error:SSLEOFError",             # closed mid-handshake -- a cut link
    "refused:http-protocol-error:IncompleteRead",  # a chunked body cut short
])
def test_tls_eof_and_http_protocol_error_retry(mod, outcome):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": outcome}],
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 2, outcome


def test_tls_cert_verification_failure_does_not_retry(mod):
    """A failed certificate verification is an answer about the host, the one
    exclusion in the tls-error family, and stays repairable."""
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "refused:tls-error:SSLCertVerificationError"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["classified"] == {"budget_failed": [], "repairable": [0]}


# ---------------------------------------------------------------------------
# 5. Scope, sleep ladder, disjointness, and the failed-command short-circuit
# ---------------------------------------------------------------------------

def test_transient_outcome_on_a_non_established_row_does_not_retry(mod):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 1, "outcome": "refused:connect-timeout"}],
    ])
    result = mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 1
    assert result["classified"] == {"budget_failed": [], "repairable": []}


def test_sleep_delays_match_the_retry_ladder_in_order(mod):
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
        [{"item_index": 0, "outcome": "refused:read-timeout"}],
    ])
    delays = []
    mod.fetch_until_stable(run_fetch, read_pairs, lambda: {0}, sleep=_fake_sleep(delays))
    assert delays == list(mod._FETCH_RETRY_DELAYS_SEC)


def test_transient_and_shared_budget_outcomes_are_disjoint(mod):
    assert mod._TRANSIENT_FETCH_OUTCOMES.isdisjoint(mod._SHARED_BUDGET_OUTCOMES)


def test_a_failed_fetch_command_short_circuits(mod):
    """`run_fetch` returning False means the command exited non-zero -- the
    existing `evidence_failed / fetch-failed` behaviour, unchanged on any
    pass. No further pass may run, and nothing may rely on a `classified` key
    that is never produced for this outcome."""
    calls = {"run_fetch": 0, "read_pairs": 0}

    def run_fetch():
        calls["run_fetch"] += 1
        return False

    def read_pairs():
        calls["read_pairs"] += 1
        raise AssertionError("read_pairs must not be called after a failed fetch")

    def load_established():
        raise AssertionError(
            "the approved snapshot must not be read when the fetch command "
            "itself failed: that call reports `fetch-failed`, and reading a "
            "snapshot the caller never got as far as needing turns it into a "
            "DriverError about the wrong thing")

    result = mod.fetch_until_stable(run_fetch, read_pairs, load_established,
                                    sleep=_fake_sleep([]))
    assert result == {"ok": False, "passes": 1}
    assert calls["run_fetch"] == 1
    assert calls["read_pairs"] == 0


def test_the_snapshot_is_read_once_however_many_passes_run(mod):
    """`load_established` is called AFTER the first fetch returns and never
    again. Once, because the approved snapshot is create-once and a later pass
    cannot see different rows; after the fetch, because a fetch command that
    exits non-zero must still be reported as `fetch-failed`."""
    run_fetch, read_pairs, calls = _scripted([
        [{"item_index": 0, "outcome": "refused:connect-timeout"}],
        [{"item_index": 0, "outcome": "refused:connect-timeout"}],
        [{"item_index": 0, "outcome": "fetched"}],
    ])
    loads = {"n": 0, "before_first_fetch": False}

    def load_established():
        loads["n"] += 1
        loads["before_first_fetch"] = calls["run_fetch"] == 0
        return {0}

    mod.fetch_until_stable(run_fetch, read_pairs, load_established,
                           sleep=_fake_sleep([]))
    assert calls["run_fetch"] == 3
    assert loads["n"] == 1, "the snapshot must be read exactly once"
    assert not loads["before_first_fetch"], "never before the first fetch returns"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
