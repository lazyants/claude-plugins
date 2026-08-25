#!/usr/bin/env python3
"""Report every usage-limit pool this machine draws on: Claude Code profiles and Codex homes.

Two sources that differ in kind, and every row says which one it came from.

Claude Code keeps a CACHE on disk, `<profile>/.claude.json` -> `cachedUsageUtilization`, refreshed
whenever Claude Code happens to fetch it. So it goes stale, and a window whose `resets_at` has
already passed describes the PREVIOUS window -- not the current one, and not zero. Such a row
renders as `stale-after-reset` and is never presented as current usage. `--live` fetches instead,
which needs the profile's OAuth token.

Codex answers live: `codex app-server` exposes the read-only JSON-RPC method
`account/rateLimits/read`. It is the only source carrying `rateLimitResetCredits` -- the "usage
limit reset" the Codex TUI offers -- and the only one enumerating the per-model pools.

This script writes nothing itself. Spawning the vendor's app-server does make it open and migrate
its own state databases under the selected CODEX_HOME, exactly as any `codex` invocation does;
that is disclosed in the report's own header rather than claimed away.

Nothing read here is echoed back. Every failure renders as one token from DIAGNOSTICS -- never an
exception string, a response body, or the child's stderr -- because `http.client` puts an invalid
header's bytes inside the ValueError it raises, bearer included.

There is deliberately no way to name the RPC that SPENDS a reset credit. The three messages below
are whole frozen objects rather than templates, and no function in this module takes a method as
an argument, so no input can select one and none can be assembled.

Python 3.11+, standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

# --- the complete diagnostic vocabulary -------------------------------------------------------
# Closed on purpose, with `internal-error` as the total fallback: an unforeseen failure must never
# have to invent a message, because a message is exactly the channel a credential escapes through.
DIAGNOSTICS = (
    "token-absent",
    "token-expired",
    "keychain-denied",
    "http-error",
    "response-malformed",
    "appserver-failed",
    "appserver-protocol-error",
    "payload-malformed",
    "field-malformed",
    "candidate-unreadable",
    "no-usage-cache",
    "no-subscription",
    "stale-after-reset",
    "internal-error",
)
DIAGNOSTIC_SET = frozenset(DIAGNOSTICS)

# Terminal states. Every candidate and every record ends in exactly one of these.
REPORTED = "reported"
NO_CURRENT = "known-no-current-value"
GAP = "gap"
INFO = "info"  # a rendered fact that is not a window: coupon count, credit balance

# Diagnostics that mean "there is no current value here", as opposed to "this was not examined".
KNOWN_ABSENT = frozenset({"no-usage-cache", "no-subscription"})

# --- the three JSON-RPC messages, frozen whole ------------------------------------------------
_REQ_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"clientInfo": {"name": "code-limits", "title": "code-limits", "version": "1"}},
}
_NOTIF_INITIALIZED = {"jsonrpc": "2.0", "method": "initialized", "params": {}}
_REQ_RATE_LIMITS = {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}}
_RATE_LIMITS_ID = 2

# --- pinned network destination ---------------------------------------------------------------
# http.client, not urllib. Measured on 3.11 and 3.14: `urllib.build_opener()` carries a
# ProxyHandler that honours HTTPS_PROXY, and its redirect handler re-sends `Authorization` to the
# new origin. An HTTPSConnection consults no proxy variable and follows no redirect, so both ways
# of moving a bearer off this host stop existing instead of being guarded against.
API_HOST = "api.anthropic.com"
API_PATH = "/api/oauth/usage"
HTTP_TIMEOUT = 15.0
HTTPSConnection = http.client.HTTPSConnection  # module-level so a test can substitute it

def _appserver_timeout() -> float:
    """Seconds to wait on one app-server. Overridable because a slow machine may need longer.

    Deliberately the ONLY environment knob in this module: it can lengthen or shorten a wait and
    can move nothing anywhere. The request destination has no such knob, which is the point.
    """
    raw = os.environ.get("CODE_LIMITS_APPSERVER_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return 45.0
    return value if 0.5 <= value <= 600.0 else 45.0
KEYCHAIN_SERVICE_PREFIX = "Claude Code-credentials-"
WINDOW_LABELS = {300: "5h", 10080: "weekly"}


class Malformed(Exception):
    """Carries a DIAGNOSTICS token and nothing else: no message, no cause, no payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- scalar contracts -------------------------------------------------------------------------
# `bool` is rejected explicitly wherever a number is required. isinstance(True, int) is true in
# Python, so without this a `True` percentage would render as 1.0% instead of failing.


def _num(value, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Malformed("field-malformed")
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")) or not low <= out <= high:
        raise Malformed("field-malformed")
    return out


def _pos_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Malformed("field-malformed")
    return value


def _nonneg_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Malformed("field-malformed")
    return value


def _flag(value) -> bool:
    if not isinstance(value, bool):
        raise Malformed("field-malformed")
    return value


def _text(value, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise Malformed("field-malformed")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise Malformed("field-malformed")
    return value


def _decimal(value) -> float:
    """A credit balance arrives either as a number or as a decimal string ("347.8911250000")."""
    if isinstance(value, bool):
        raise Malformed("field-malformed")
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise Malformed("field-malformed") from None
    return _num(value, 0.0, float(2**53))


def _from_epoch_seconds(value) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(_pos_int(value), datetime.timezone.utc)


def _from_epoch_millis(value) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(_pos_int(value) / 1000, datetime.timezone.utc)


def _from_iso(value) -> datetime.datetime:
    if not isinstance(value, str):
        raise Malformed("field-malformed")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise Malformed("field-malformed") from None
    if parsed.tzinfo is None:  # a naive stamp cannot be compared against "now"
        raise Malformed("field-malformed")
    return parsed


def _obj(value):
    if not isinstance(value, dict):
        raise Malformed("payload-malformed")
    return value


# --- rendering ---------------------------------------------------------------------------------


def _local(when: datetime.datetime) -> str:
    return when.astimezone().strftime("%d %b %H:%M %Z")


def _age(since: datetime.datetime, now: datetime.datetime) -> str:
    seconds = int((now - since).total_seconds())
    if seconds < 0:
        return "stamped in the future"
    if seconds < 3600:
        return f"{seconds // 60}m old"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m old"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}h old"


def _window_label(minutes: int) -> str:
    return WINDOW_LABELS.get(minutes, f"{minutes}min")


class Record:
    """One pool observation, one reason there is no current value for it, or one info line."""

    def __init__(self, name: str, state: str, *, percent=None, resets=None,
                 freshness: str = "", diagnostic: str = "", note: str = "") -> None:
        self.name = name
        self.state = state
        self.percent = percent
        self.resets = resets
        self.freshness = freshness
        self.diagnostic = diagnostic
        self.note = note

    def render(self) -> str:
        head = f"    {self.name:<30}"
        if self.state == INFO:
            body = self.freshness
        elif self.state == REPORTED and self.resets is not None and self.percent is not None:
            pct = f"{self.percent:.1f}".rjust(5)
            body = f"{pct}%  resets {_local(self.resets):<21}  {self.freshness}"
        elif self.diagnostic and self.percent is not None:
            reset = f", reset {_local(self.resets)}" if self.resets is not None else ""
            body = (f"[{self.diagnostic}] previous window {self.percent:.1f}%"
                    f"{reset}  {self.freshness}")
        elif self.diagnostic:
            body = f"[{self.diagnostic}]"
        else:
            # An inactive pool: a real percentage, no current window, and deliberately no
            # diagnostic token -- "inactive" is a state the vendor reports, not a failure.
            reset = f"  resets {_local(self.resets)}" if self.resets is not None else ""
            pct = f"{self.percent:.1f}".rjust(5)
            body = f"{pct}%{reset}  {self.freshness}"
        return (head + body).rstrip() + (f"  -- {self.note}" if self.note else "")


def _window_row(*, name: str, percent: float, resets: datetime.datetime, freshness: str,
                now: datetime.datetime, active: bool) -> Record:
    if resets <= now:
        return Record(name, NO_CURRENT, percent=percent, resets=resets, freshness=freshness,
                      diagnostic="stale-after-reset",
                      note="current window unknown without --live")
    if not active:
        return Record(name, NO_CURRENT, percent=percent, resets=resets, freshness=freshness,
                      note="inactive, no current window")
    return Record(name, REPORTED, percent=percent, resets=resets, freshness=freshness)


# --- Claude Code -------------------------------------------------------------------------------


def _claude_rows(utilization: dict, freshness: str, now: datetime.datetime) -> list[Record]:
    """Rows from a `utilization` object, in either of the two shapes Claude Code emits."""
    entries = utilization.get("limits")
    records: list[Record] = []
    if entries is not None:
        if not isinstance(entries, list):
            raise Malformed("payload-malformed")
        for item in entries:              # container pass: shape first, for every entry
            _obj(item)
        for index, item in enumerate(entries):
            # Per RECORD, not per candidate. One malformed entry must not suppress its siblings:
            # the valid pools are exactly what the operator opened the report to see, and a
            # profile that prints nothing looks identical to one that has nothing.
            try:
                records.append(_claude_row(item, freshness, now))
            except Malformed as exc:
                records.append(Record(f"limits[{index}]", GAP, diagnostic=exc.code))
        return records

    for key in ("five_hour", "seven_day"):
        slot = utilization.get(key)
        if slot is None:
            continue
        slot = _obj(slot)
        try:
            records.append(_window_row(
                name=f"{key} (flat)",
                percent=_num(slot.get("utilization"), 0.0, 100.0),
                resets=_from_iso(slot.get("resets_at")),
                freshness=freshness, now=now, active=True,
            ))
        except Malformed as exc:
            records.append(Record(f"{key} (flat)", GAP, diagnostic=exc.code))
    return records


def _claude_row(entry: dict, freshness: str, now: datetime.datetime) -> Record:
    kind = _text(entry.get("kind"), 64)
    name = kind
    scope = entry.get("scope")
    if scope is not None:
        model = _obj(scope).get("model")
        if model is not None:
            display = _obj(model).get("display_name")
            if display is not None:
                name = f"{kind} ({_text(display, 64)})"
    active = _flag(entry.get("is_active")) if "is_active" in entry else True
    percent = _num(entry.get("percent"), 0.0, 100.0)
    if entry.get("resets_at") is None:
        # Measured on this machine, not hypothesised: a `weekly_scoped` entry ships
        # `resets_at: null` with `is_active: false`. That is the vendor reporting a pool with no
        # current window, so gapping the whole profile over it would be a check its owner could
        # never clear. An ACTIVE entry with no reset time is still malformed.
        if active:
            raise Malformed("field-malformed")
        return Record(name, NO_CURRENT, percent=percent, freshness=freshness,
                      note="inactive, no reset time reported")
    return _window_row(
        name=name,
        percent=percent,
        resets=_from_iso(entry.get("resets_at")),
        freshness=freshness,
        now=now,
        active=active,
    )


def _claude_cached(profile: Path, now: datetime.datetime) -> list[Record]:
    """Records for one Claude profile from its cache. The container is validated FIRST.

    A container that parses but yields zero recognised records is a gap, not an empty success: a
    loop that runs zero times otherwise prints exactly what a passing one prints.
    """
    try:
        with open(profile / ".claude.json", encoding="utf-8") as handle:
            blob = json.load(handle)
    except FileNotFoundError:
        raise Malformed("no-usage-cache") from None
    except (OSError, ValueError):
        raise Malformed("payload-malformed") from None

    if not isinstance(blob, dict):
        raise Malformed("payload-malformed")

    # Decided BEFORE the cache is inspected, so a profile that is both unsubscribed and cacheless
    # lands in exactly one class instead of matching two rules.
    if blob.get("hasAvailableSubscription") is False:
        raise Malformed("no-subscription")

    cached = blob.get("cachedUsageUtilization")
    if cached is None:
        raise Malformed("no-usage-cache")
    cached = _obj(cached)

    fetched = _from_epoch_millis(cached.get("fetchedAtMs"))
    freshness = f"cache {_age(fetched, now)}"
    records = _claude_rows(_obj(cached.get("utilization")), freshness, now)
    if not records:
        raise Malformed("payload-malformed")
    return records


def _keychain_service(profile: Path) -> str:
    """Measured on this machine: the service name is sha256 of the absolute config dir, 8 hex."""
    digest = hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:8]
    return KEYCHAIN_SERVICE_PREFIX + digest


def _claude_token(profile: Path, now: datetime.datetime) -> str:
    """Return the profile's bearer. Never rendered, never logged, never placed in an argv."""
    creds = profile / ".credentials.json"
    if creds.exists():
        try:
            with open(creds, encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, ValueError):
            raise Malformed("response-malformed") from None
        oauth = blob.get("claudeAiOauth") if isinstance(blob, dict) else None
        if not isinstance(oauth, dict):
            raise Malformed("token-absent")
        token = oauth.get("accessToken")
        if not isinstance(token, str) or not token:
            raise Malformed("token-absent")
        expires = oauth.get("expiresAt")
        if expires is not None and _from_epoch_millis(expires) <= now:
            raise Malformed("token-expired")
        return token

    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-s", _keychain_service(profile), "-w"],
            capture_output=True, text=True, timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError):
        raise Malformed("keychain-denied") from None
    if done.returncode != 0:  # stderr is deliberately captured and never rendered
        raise Malformed("keychain-denied")
    token = done.stdout.strip()
    if not token:
        raise Malformed("token-absent")
    return token


def _claude_live(profile: Path, now: datetime.datetime) -> list[Record]:
    token = _claude_token(profile, now)
    conn = HTTPSConnection(API_HOST, timeout=HTTP_TIMEOUT)
    try:
        try:
            conn.request("GET", API_PATH, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            })
            response = conn.getresponse()
            body = response.read()
            status = response.status
        except Malformed:
            raise
        except Exception:
            # Deliberately bare and deliberately silent: rendering any exception text here can
            # print the bearer, which http.client embeds in an invalid-header ValueError.
            raise Malformed("http-error") from None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if status != 200:  # a 3xx is a non-200 like any other; nothing follows it
        raise Malformed("http-error")
    try:
        blob = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Malformed("response-malformed") from None
    if not isinstance(blob, dict):
        raise Malformed("response-malformed")

    inner = blob.get("utilization", blob)
    if not isinstance(inner, dict):
        raise Malformed("response-malformed")
    records = _claude_rows(inner, f"live {_local(now)}", now)
    if not records:
        raise Malformed("response-malformed")
    return records


# --- Codex ---------------------------------------------------------------------------------


def _appserver_result(home: Path) -> dict:
    """Spawn the app-server, exchange the three frozen messages, return the reply's result.

    The deadline starts AFTER a successful spawn and covers the handshake, the reply, termination
    and the reap. It is not wrapped around the blocking spawn itself: that would need extra
    threading machinery for a case never observed, while the reachable failure is a child that
    starts and then stalls, holding up every remaining candidate in the run.
    """
    env = dict(os.environ, CODEX_HOME=str(home))
    try:
        child = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, ValueError):
        raise Malformed("appserver-failed") from None

    stdin, stdout = child.stdin, child.stdout
    if stdin is None or stdout is None:  # unreachable with PIPE, but the type says otherwise
        child.kill()
        child.wait(timeout=10.0)
        raise Malformed("appserver-failed")

    deadline = time.monotonic() + _appserver_timeout()
    try:
        try:
            for message in (_REQ_INITIALIZE, _NOTIF_INITIALIZED, _REQ_RATE_LIMITS):
                stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            stdin.flush()
        except OSError:
            raise Malformed("appserver-failed") from None

        buffer = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Malformed("appserver-failed")
            ready, _, _ = select.select([stdout], [], [], min(remaining, 1.0))
            if not ready:
                continue
            chunk = os.read(stdout.fileno(), 65536)
            if not chunk:
                raise Malformed("appserver-protocol-error")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue  # notifications and log lines we do not model
                if not isinstance(message, dict) or message.get("id") != _RATE_LIMITS_ID:
                    continue
                if "error" in message:
                    raise Malformed("appserver-protocol-error")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise Malformed("appserver-protocol-error")
                return result
    finally:
        for stream in (stdin, stdout):
            try:
                stream.close()
            except Exception:
                pass
        child.kill()
        try:
            child.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            pass


def _codex_records(home: Path, now: datetime.datetime) -> list[Record]:
    result = _appserver_result(home)
    freshness = f"live {_local(now)}"

    default = result.get("rateLimits")
    by_id = result.get("rateLimitsByLimitId")
    pools: dict[str, dict] = {}
    if isinstance(by_id, dict) and by_id:
        for key, pool in by_id.items():
            pools[_text(key, 64)] = _obj(pool)
    elif isinstance(default, dict):
        pools[_text(default.get("limitId", "codex"), 64)] = default
    else:
        raise Malformed("payload-malformed")

    records: list[Record] = []
    for limit_id in sorted(pools):
        pool = pools[limit_id]
        # Identity is (limitId, slot); only the LABEL comes from the duration. Two limit ids were
        # measured carrying a 10080-minute window at the same time, so the label alone collides.
        for slot in ("primary", "secondary"):
            window = pool.get(slot)
            if window is None:
                continue
            window = _obj(window)
            try:
                minutes = _pos_int(window.get("windowDurationMins"))
                records.append(_window_row(
                    name=f"{limit_id}/{_window_label(minutes)}",
                    percent=_num(window.get("usedPercent"), 0.0, 100.0),
                    resets=_from_epoch_seconds(window.get("resetsAt")),
                    freshness=freshness, now=now, active=True,
                ))
            except Malformed as exc:
                records.append(Record(f"{limit_id}/{slot}", GAP, diagnostic=exc.code))
    if not records:
        raise Malformed("payload-malformed")

    coupons = result.get("rateLimitResetCredits")
    if coupons is not None:
        try:
            count = _nonneg_int(_obj(coupons).get("availableCount"))
        except Malformed as exc:
            records.append(Record("reset coupons", GAP, diagnostic=exc.code))
            count = None
    if coupons is not None and count is not None:
        records.append(Record(
            "reset coupons", INFO, freshness=str(count),
            note="read only; redeem one in the Codex TUI with /usage, never from here",
        ))

    credits = default.get("credits") if isinstance(default, dict) else None
    if isinstance(credits, dict) and _flag(credits.get("hasCredits")):
        records.append(Record("credits", INFO, freshness=f"{_decimal(credits.get('balance')):.2f}"))
    return records


# --- candidates ---------------------------------------------------------------------------------


class Candidate:
    def __init__(self, path: Path, gap: str = "") -> None:
        self.path = path
        self.gap = gap


def _explicit(path: Path) -> Candidate:
    """An explicitly named candidate still gets a probe.

    Discovery cannot hand over a directory that is not there, but an argument can, and without
    this a typo'd or moved profile reaches the producer, finds nothing, and reports a clean
    "no cache" -- a verdict about a profile that was never examined at all.
    """
    try:
        if not path.is_dir():
            return Candidate(path, "candidate-unreadable")
    except OSError:
        return Candidate(path, "candidate-unreadable")
    return Candidate(path)


def _discover(root: Path, prefix: str, markers: tuple[str, ...]) -> list[Candidate]:
    """Directories matching the prefix that carry a vendor marker.

    A marker probe that ERRORS yields a candidate carrying `candidate-unreadable`, rather than
    being filtered out: a profile that cannot be examined must not vanish before the ledger.
    """
    out: list[Candidate] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            out.append(Candidate(entry, "candidate-unreadable"))
            continue
        try:
            present = any((entry / marker).exists() for marker in markers)
        except OSError:
            out.append(Candidate(entry, "candidate-unreadable"))
            continue
        if present:
            out.append(Candidate(entry))
    return out


def _examine(candidate: Candidate, producer) -> tuple[str, list[Record], str]:
    """Run one candidate's producer, mapping every failure onto a single diagnostic token."""
    if candidate.gap:
        return GAP, [], candidate.gap
    try:
        records = producer(candidate.path)
    except Malformed as exc:
        code = exc.code if exc.code in DIAGNOSTIC_SET else "internal-error"
        return (NO_CURRENT if code in KNOWN_ABSENT else GAP), [], code
    except Exception:
        return GAP, [], "internal-error"

    if any(record.state == GAP for record in records):
        return GAP, records, ""
    if any(record.state == REPORTED for record in records):
        return REPORTED, records, ""
    return NO_CURRENT, records, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Claude Code and Codex usage limits.")
    parser.add_argument("--live", action="store_true",
                        help="fetch Claude usage instead of reading the on-disk cache")
    parser.add_argument("--claude-profile", action="append", default=[], metavar="PATH",
                        help="examine this Claude profile (repeatable; replaces discovery)")
    parser.add_argument("--codex-home", action="append", default=[], metavar="PATH",
                        help="examine this Codex home (repeatable; replaces discovery)")
    args = parser.parse_args(argv)

    home = Path(os.path.expanduser("~"))
    now = datetime.datetime.now(datetime.timezone.utc)

    claude = ([_explicit(Path(p)) for p in args.claude_profile]
              or _discover(home, ".claude", (".claude.json",)))
    codex = ([_explicit(Path(p)) for p in args.codex_home]
             or _discover(home, ".codex", ("auth.json", "config.toml")))

    warnings: list[str] = []
    print(f"code-limits  {_local(now)}")
    print("  Claude rows come from an on-disk cache unless --live; Codex rows are always live.")
    print("  Reading Codex limits starts its app-server, which touches that home's own state")
    print("  databases exactly as any codex invocation does. Nothing here is ever redeemed.")

    claude_producer = ((lambda p: _claude_live(p, now)) if args.live
                       else (lambda p: _claude_cached(p, now)))
    for group, candidates, producer in (
        ("Claude Code", claude, claude_producer),
        ("Codex", codex, lambda p: _codex_records(p, now)),
    ):
        print(f"\n{group}")
        if not candidates:
            warnings.append(f"{group}: no candidates found -- NOT checked")
            print("    [gap] no candidates found")
            continue
        for candidate in candidates:
            state, records, code = _examine(candidate, producer)
            print(f"  {candidate.path.name}")
            for record in records:
                print(record.render())
            if code:
                print(f"    [{code}]")
            if state == GAP:
                warnings.append(
                    f"{group} {candidate.path.name}: NOT checked -- {code or 'field-malformed'}")

    if warnings:
        print("\nwarnings")
        for warning in warnings:
            print(f"  {warning}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
