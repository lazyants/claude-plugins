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
import math
import os
import select
import stat
import unicodedata
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

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

# Terminal states. Every candidate and every record ends in exactly one of these three, and
# nothing else is ever assigned to Record.state -- an earlier "info" constant was, which made
# this comment false about the line directly under it.
REPORTED = "reported"
NO_CURRENT = "known-no-current-value"
GAP = "gap"
TERMINAL_STATES = frozenset({REPORTED, NO_CURRENT, GAP})

# A percentage the vendor reports past 100 is the backend's number, not schema drift: the
# official schema types `usedPercent` as an unbounded int32. Refusing it would gap a pool for
# being over its limit, which is the moment this report is most worth reading.
PERCENT_MAX = float("inf")

# Diagnostics that mean "there is no current value here", as opposed to "this was not examined".
KNOWN_ABSENT = frozenset({"no-usage-cache", "no-subscription"})

# --- the three JSON-RPC messages, frozen whole ------------------------------------------------
# Serialized at import and never rebuilt. The dict literals below are never bound to a name, so
# after import there is no message left to mutate and no expression anywhere that could compose a
# method from data -- every method this module can name is a literal in these three frames.
_FRAMES: tuple[bytes, ...] = tuple(
    (json.dumps(message) + "\n").encode("utf-8") for message in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "code-limits", "title": "code-limits",
                                   "version": "1"}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
    )
)
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

KEYCHAIN_SERVICE_DEFAULT = "Claude Code-credentials"
KEYCHAIN_SERVICE_PREFIX = KEYCHAIN_SERVICE_DEFAULT + "-"
WINDOW_LABELS = {300: "5h", 10080: "weekly"}
MAX_LINE_BYTES = 4 * 1024 * 1024


def _appserver_timeout() -> float:
    """Seconds to wait on one app-server. Overridable because a slow machine may need longer.

    Deliberately the only environment knob in this module that can change what is READ or where a
    request goes -- `NO_COLOR` is read too, but it can only restyle what has already been decided.
    This one can lengthen or shorten a wait and
    can move nothing anywhere. The request destination has no such knob, which is the point.
    """
    raw = os.environ.get("CODE_LIMITS_APPSERVER_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return 45.0
    return value if 0.5 <= value <= 600.0 else 45.0


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
    # isfinite, not the range alone: PERCENT_MAX is +inf, so `low <= inf <= high` is true there.
    if not math.isfinite(out) or not low <= out <= high:
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


_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _text(value, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise Malformed("field-malformed")
    # Every string this guards is PRINTED into the report, so the rule is about what a vendor
    # may put on the page. C0 and DEL were never enough: U+2028 LINE SEPARATOR is what
    # `str.splitlines()` breaks on, so a title reading "Full\u2028warnings\u2028  trusted" adds a
    # line to the report that looks like the report's own -- forged structure out of vendor JSON,
    # on a run that stays clean. U+202E can reverse a pool name in place. Rejected by CATEGORY:
    # controls, format characters, surrogates, private use, unassigned, and the line/paragraph
    # separators. Zs is deliberately NOT in the set -- a label may legitimately hold a no-break
    # space, and refusing that would be a check its owner could never clear.
    if any(unicodedata.category(ch) in _FORBIDDEN_CATEGORIES for ch in value):
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


# Ticks per second, named because a bare literal at the call site would not say which vendor's
# unit it is: Codex sends `resetsAt` in seconds, Claude Code sends `fetchedAtMs` in milliseconds.
EPOCH_SECONDS = 1
EPOCH_MILLIS = 1000


def _from_epoch(value, per_second: int) -> datetime.datetime:
    """An epoch integer counted in `per_second` ticks: EPOCH_SECONDS or EPOCH_MILLIS.

    The arithmetic stays INSIDE the guard. A 400-digit integer raises OverflowError in the
    division; hoisting that line out was measured turning `field-malformed` into `internal-error`
    at the `fetchedAtMs` call site, where the nearest handler is the whole candidate's.
    """
    try:
        parsed = datetime.datetime.fromtimestamp(_pos_int(value) / per_second,
                                                 datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise Malformed("field-malformed") from None
    return _renderable(parsed)


def _from_iso(value) -> datetime.datetime:
    if not isinstance(value, str):
        raise Malformed("field-malformed")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise Malformed("field-malformed") from None
    if parsed.tzinfo is None:  # a naive stamp cannot be compared against "now"
        raise Malformed("field-malformed")
    return _renderable(parsed)


def _renderable(when: datetime.datetime) -> datetime.datetime:
    """Reject a timestamp that parses but cannot be rendered in the local zone.

    `9999-12-31T23:59:59+00:00` is valid ISO-8601 and raises OverflowError inside astimezone() --
    but only east of Greenwich, where the offset pushes it past year 9999. West of Greenwich the
    same happens to a minimum stamp instead, and on a UTC machine neither overflows at all, so
    which timestamps this rejects is a property of the reader's zone, not of the value.
    That happens in the renderer, which runs outside the per-candidate handler, so it would abort
    the entire report rather than gapping one record.
    """
    try:
        when.astimezone()
    except (OverflowError, OSError, ValueError):
        raise Malformed("field-malformed") from None
    return when


def _obj(value):
    if not isinstance(value, dict):
        raise Malformed("payload-malformed")
    return value


# --- rendering ---------------------------------------------------------------------------------


def _safe_name(text: str) -> str:
    """A filesystem-derived name, made safe to PRINT -- escaped, never refused.

    A directory name belongs to the machine, not to a vendor, so gapping a profile over its
    spelling would be a check its owner could not clear; `_text`'s refusal is for vendor fields
    and is deliberately not applied here. But a name may not forge report STRUCTURE either.
    Reproduced: a directory called `.claude-evil\nwarnings\n  Claude Code trusted: checked`
    printed its own `warnings` heading in the middle of the report, above rows that then read as
    belonging to it, while the run still exited 0. Only non-printable characters are escaped, so
    an ordinary accented profile name still renders as itself.
    """
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)


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


CLAUDE_GROUP = "Claude Code"
CODEX_GROUP = "Codex"
VOUCHER_NAME = "reset vouchers"
CREDITS_NAME = "credits"
BAR_CELLS = 10

# ANSI, applied to finished cells only. Width arithmetic runs on the PLAIN strings -- an escape
# sequence inside a `:<12` would pad the visible text by however many bytes the escape happens to
# be, which is the classic way a coloured table goes crooked exactly when colour is on.
DIM, BOLD, RED, YELLOW, GREEN, CYAN = "2", "1", "31", "33", "32", "36"


class Paint:
    """Colour, or the identity function. One object so no call site has to test a flag."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text


def _use_colour(choice: str, stream) -> bool:
    """`never` off, `always` on, `auto` = a terminal with NO_COLOR unset.

    `auto` is the default and the only branch with any logic in it: piping the report -- into a
    file, a pager, or this plugin's own test harness -- must yield plain text, or every consumer
    that compares output would be comparing escape sequences.
    """
    if choice == "never":
        return False
    if choice == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def _width(text: str) -> int:
    """Terminal columns, not code points.

    A profile directory or a vendor pool name may hold wide characters, and `len()` counts a CJK
    ideograph as one while a terminal draws it as two -- every column after it then shifts by the
    difference. Combining marks are the same error pointed the other way. The gauge's own blocks
    are East-Asian AMBIGUOUS, which the overwhelmingly common configuration renders narrow, so
    they count as one here; a terminal configured otherwise draws a wider gauge, not a crooked
    table, because every row's gauge is the same length.
    """
    total = 0
    join_next = False
    for character in unicodedata.normalize("NFC", text):
        point = ord(character)
        if unicodedata.combining(character) or 0xFE00 <= point <= 0xFE0F:
            continue                                    # a mark or a variation selector
        if 0x1F3FB <= point <= 0x1F3FF:
            continue                                    # a skin-tone modifier
        if point == 0x200D:
            join_next = True                            # ZWJ: the next glyph joins this cluster
            continue
        if join_next:
            join_next = False
            continue
        total += 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
    return total


def _pad(text: str, width: int) -> str:
    """`f"{text:<width}"` but measured in columns."""
    return text + " " * max(0, width - _width(text))


def _bar(percent: float) -> str:
    """A ten-cell gauge. Derived from the same float the number is, never a second measurement."""
    filled = min(BAR_CELLS, max(0, math.floor(percent / (100 / BAR_CELLS) + 0.5)))
    return "\u2588" * filled + "\u2591" * (BAR_CELLS - filled)


def _hue(percent: float) -> str:
    return RED if percent >= 80 else YELLOW if percent >= 50 else GREEN


def _source(freshness: str, group: str) -> str:
    """Shorten a row's provenance to the part that varies.

    The producers spell freshness out per record -- `live 26 Aug 19:43 CEST`, `cache 2d04h old`.
    In a table every live row repeats the same stamp, which is already in the header, so the
    column becomes a wall of identical text and the reader stops reading it. What still varies
    is WHICH source and, for a cache, how old. Nothing is dropped that the header does not say.
    """
    if freshness.startswith("live"):
        # `api` vs `live` also keeps the VENDOR readable off the row. Under --live both sides are
        # live, and two candidates may share a directory basename, so without this the flat table
        # can show two rows that are genuinely indistinguishable.
        return "api" if group == CLAUDE_GROUP else "live"
    if freshness.startswith("cache "):
        return freshness[len("cache "):].replace(" old", "")
    return freshness


def _relative(when: datetime.datetime, now: datetime.datetime) -> str:
    """`in 8h`, `in 5d 21h`, `reset 3d ago`. Absolute stamps are for the header and the vouchers.

    A window's usefulness is how long is LEFT, and a reader should not have to subtract dates to
    get it. The absolute time survives where it is the fact itself -- a voucher's expiry date.
    """
    delta = when - now
    seconds = int(abs(delta.total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        span = f"{days}d {hours:02d}h"
    elif hours:
        span = f"{hours}h"
    else:
        span = f"{max(1, rest // 60)}m"
    return f"in {span}" if delta.total_seconds() > 0 else f"reset {span} ago"


class Record:
    """One pool observation, one reason there is no current value for it, or one info line."""

    def __init__(self, name: str, state: str, *, percent=None, resets=None,
                 freshness: str = "", diagnostic: str = "", note: str = "",
                 info: bool = False, expires=None, title: str = "") -> None:
        if state not in TERMINAL_STATES:
            raise Malformed("internal-error")
        self.name = name
        self.state = state
        # A RENDER kind, deliberately not a state: a coupon count or a credit balance was read
        # successfully, so it is `reported` for the exit contract and only the renderer needs to
        # know it carries no percentage.
        self.info = info
        self.percent = percent
        self.resets = resets
        self.freshness = freshness
        self.diagnostic = diagnostic
        self.note = note
        # Voucher detail. Both are optional in the vendor's payload and neither may ever gap a
        # run: a home that reports a count and nothing else still renders, just with less to say.
        self.expires = expires
        self.title = title

    # Row classes the table lays out. Deliberately derived HERE, beside the fields they read,
    # rather than re-derived by the renderer from a pile of `if`s -- there is one definition of
    # what "stale" means and the renderer cannot disagree with it.
    def kind(self) -> str:
        if self.info:
            return "voucher" if self.name == VOUCHER_NAME else "info"
        if self.state == GAP:
            return "gap"
        if self.diagnostic:
            return "stale"
        if self.state == NO_CURRENT:
            return "inactive"
        return "current"


class Row(NamedTuple):
    """One bucketed observation: the candidate it came from, whose vendor it is, and the record.

    The vendor group travels WITH the row because `_source` needs it to keep a live Claude row
    distinguishable from a live Codex one, and the bucketing loop is the last place that still
    knows it. The obvious alternative -- looking the group back up out of a side table keyed on
    `id(record)` -- needs a default for the miss, and any default there prints a wrong vendor
    with a right one's confidence, unfalsifiable from the page.
    """

    where: str
    group: str
    record: Record


def _window_row(*, name: str, percent: float, resets: datetime.datetime, freshness: str,
                now: datetime.datetime, active: bool,
                stale_note: str = "current window unknown without --live") -> Record:
    if resets <= now:
        return Record(name, NO_CURRENT, percent=percent, resets=resets, freshness=freshness,
                      diagnostic="stale-after-reset", note=stale_note)
    if not active:
        return Record(name, NO_CURRENT, percent=percent, resets=resets, freshness=freshness,
                      note="inactive, no current window")
    return Record(name, REPORTED, percent=percent, resets=resets, freshness=freshness)


def _row_or_gap(name: str, produce) -> Record:
    """Run one row producer so that EVERY failure becomes THIS row's gap, never the candidate's.

    The single place the per-record boundary is implemented. Three call sites had written it out
    by hand and the third had quietly drifted from the other two -- it validated the entry's
    shape outside its own handler and caught only Malformed, so one bad `five_hour` still took
    `seven_day` down with it. Both arms are load-bearing: Malformed carries the precise token,
    and anything else -- OverflowError out of float() on a 400-digit integer, for one -- would
    otherwise reach the candidate's handler and discard every valid sibling row.
    """
    try:
        return produce()
    except Malformed as exc:
        return Record(name, GAP, diagnostic=exc.code)
    except Exception:
        return Record(name, GAP, diagnostic="internal-error")


# --- Claude Code -------------------------------------------------------------------------------


def _claude_rows(utilization: dict, freshness: str, now: datetime.datetime) -> list[Record]:
    """Rows from a `utilization` object, in either of the two shapes Claude Code emits."""
    entries = utilization.get("limits")
    records: list[Record] = []
    if entries is not None:
        if not isinstance(entries, list):
            raise Malformed("payload-malformed")
        for index, item in enumerate(entries):
            # Per RECORD, not per candidate. One malformed entry must not suppress its siblings:
            # the valid pools are exactly what the operator opened the report to see, and a
            # profile that prints nothing looks identical to one that has nothing. The entry's
            # own SHAPE is validated inside the producer for the same reason -- a container pass
            # that rejected a non-object entry ahead of the loop lost every valid sibling.
            records.append(_row_or_gap(
                f"limits[{index}]", lambda: _claude_row(_obj(item), freshness, now)))
        return records

    for key in ("five_hour", "seven_day"):
        slot = utilization.get(key)
        if slot is None:
            continue
        records.append(_row_or_gap(
            f"{key} (flat)", lambda: _claude_flat_row(key, slot, freshness, now)))
    return records


def _claude_flat_row(key: str, slot, freshness: str, now: datetime.datetime) -> Record:
    """One row of the older flat shape, whose windows are named keys rather than list items."""
    body = _obj(slot)
    return _window_row(
        name=f"{key} (flat)",
        percent=_num(body.get("utilization"), 0.0, 100.0),
        resets=_from_iso(body.get("resets_at")),
        freshness=freshness, now=now, active=True,
    )


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
    if "hasAvailableSubscription" in blob and not _flag(blob["hasAvailableSubscription"]):
        # Key MEMBERSHIP, and _flag rather than `is False`: a vendor type change is schema
        # drift and must gap, where `is False` fell through to no-usage-cache -- a KNOWN_ABSENT
        # state that exits 0 -- and a `subscription is not None` guard did the same for null,
        # which is a present key holding a non-boolean like any other.
        raise Malformed("no-subscription")

    cached = blob.get("cachedUsageUtilization")
    if cached is None:
        raise Malformed("no-usage-cache")
    cached = _obj(cached)

    fetched = _from_epoch(cached.get("fetchedAtMs"), EPOCH_MILLIS)
    freshness = f"cache {_age(fetched, now)}"
    records = _claude_rows(_obj(cached.get("utilization")), freshness, now)
    if not records:
        raise Malformed("payload-malformed")
    return records


def _keychain_service(profile: Path) -> str:
    """The Keychain item a profile's credentials are stored under.

    MEASURED across this machine's profiles, and the default is a special case that an earlier
    reading of the same evidence missed. `~/.claude` keeps its live credential in the UNSUFFIXED
    item; the suffixed item for that path also exists but holds an EMPTY accessToken -- which is
    worse than a missing item, because it answers `token-absent` rather than failing visibly, and
    is exactly why "an item exists at the derived name" was mistaken for "the derivation is
    right". Every other config dir uses the suffix: sha256 of the absolute path, first 8 hex,
    confirmed against three of them carrying real 108-character tokens.

    Known limit, disclosed rather than guessed at: the suffix is derived from the RESOLVED path.
    A profile selected by a different spelling of the same directory, or through a separate
    secure-storage override, would hash differently -- that lands as `token-absent`, never as
    another account's number, because the item simply will not be found.
    """
    if profile == Path(os.path.expanduser("~")) / ".claude":
        return KEYCHAIN_SERVICE_DEFAULT
    digest = hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:8]
    return KEYCHAIN_SERVICE_PREFIX + digest


def _keychain_blob(profile: Path) -> dict:
    """The credential OBJECT for a profile that has no credential file on disk.

    `security ... -w` writes the whole stored JSON, not a bearer. Treating its stdout as the token
    put that entire object -- refresh token included -- into an Authorization header, and every
    Keychain-backed profile answered `http-error` because no such bearer exists.
    """
    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-s", _keychain_service(profile), "-w"],
            capture_output=True, text=True, timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError):
        raise Malformed("keychain-denied") from None
    if done.returncode != 0:  # stderr is deliberately captured and never rendered
        raise Malformed("keychain-denied")
    payload = done.stdout.strip()
    if not payload:
        raise Malformed("token-absent")
    try:
        return json.loads(payload)
    except ValueError:
        # Deliberately not rendered: the text that failed to parse IS the credential.
        raise Malformed("response-malformed") from None


def _bearer(blob) -> str:
    """The access token out of a Claude Code credential object, whatever it was read from.

    ONE extractor, because the file on disk and the Keychain item hold the same object. Two
    readers is how they drifted: the file's parsed it and validated the expiry, the Keychain's
    returned raw stdout and checked neither.
    """
    oauth = blob.get("claudeAiOauth") if isinstance(blob, dict) else None
    if not isinstance(oauth, dict):
        raise Malformed("token-absent")
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        raise Malformed("token-absent")
    expires = oauth.get("expiresAt")
    now = datetime.datetime.now(datetime.timezone.utc)  # read time, not run-start time
    if expires is not None and _from_epoch(expires, EPOCH_MILLIS) <= now:
        raise Malformed("token-expired")
    return token


def _claude_token(profile: Path) -> str:
    """Return the profile's bearer. Never rendered, never logged, never placed in an argv."""
    try:
        handle = open(profile / ".credentials.json", encoding="utf-8")
    except FileNotFoundError:
        # Absent, or a link to something absent -- open() cannot separate those, and a dangling
        # .credentials.json symlink therefore takes the keychain path too. Left as it is: the
        # reachable case is a profile that has no credential file, and adding a symlink probe
        # would be machinery for an edge nobody has produced.
        return _bearer(_keychain_blob(profile))
    except OSError:
        # Present but unreadable. Path.exists() answers False for a permission failure from
        # 3.14 and raises below it, so testing for the file first would read this as absence
        # and fall through to the keychain -- reporting a DIFFERENT credential's pool as this
        # profile's, cleanly. Opening it is the only probe whose failure modes are separable.
        raise Malformed("candidate-unreadable") from None
    with handle:
        try:
            blob = json.load(handle)
        except (OSError, ValueError):
            raise Malformed("response-malformed") from None
    return _bearer(blob)


def _claude_live(profile: Path) -> list[Record]:
    token = _claude_token(profile)
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

    now = datetime.datetime.now(datetime.timezone.utc)  # observation time, not run-start time
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

    The deadline starts AFTER a successful spawn and bounds the PROTOCOL phase: the handshake
    and the reply. Cleanup then adds its own bounded tail -- a kill followed by `wait(timeout=10)`
    -- so the knob is not a total per-home bound and an abnormally unkillable child can add up to
    ten seconds. That tail is disclosed rather than removed: making it part of one deadline needs
    extra machinery for a case never observed. The deadline is likewise not wrapped around the
    blocking spawn itself; the reachable failure is a child that starts and then stalls, holding
    up every remaining candidate in the run.
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
            for frame in _FRAMES:
                stdin.write(frame)
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
            if len(buffer) > MAX_LINE_BYTES:
                # A child that never emits a newline would otherwise be buffered until the
                # deadline, which can be ten minutes at the knob's ceiling.
                raise Malformed("appserver-protocol-error")
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


def _label_duration(window) -> int | None:
    """A window's duration in minutes, or None for every shape that has no usable one.

    Deliberately total where `_pos_int` raises: this answers only whether two slots would render
    under the SAME label, which is asked before either window is validated. A malformed duration
    still gaps its own record -- through the strict check inside the per-record handler.
    """
    if not isinstance(window, dict):
        return None
    raw = window.get("windowDurationMins")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


def _codex_window_row(*, limit_id: str, slot: str, window, collide: bool, freshness: str,
                      now: datetime.datetime) -> Record:
    """One Codex window. Raises Malformed only for a shape the vendor's schema does not permit."""
    body = _obj(window)
    # `windowDurationMins` and `resetsAt` are both `integer | null` in the vendor's own schema,
    # so a null is the backend declining to provide the value, not drift. Gapping on it would
    # leave such an account unable to report cleanly whatever its owner did -- a check nobody can
    # satisfy is worse than the gap it closes.
    raw_minutes = body.get("windowDurationMins")
    minutes = None if raw_minutes is None else _pos_int(raw_minutes)
    name = f"{limit_id}/{slot if minutes is None else _window_label(minutes)}"
    if collide:
        name = f"{name}({slot})"
    percent = _num(body.get("usedPercent"), 0.0, PERCENT_MAX)
    raw_resets = body.get("resetsAt")
    if raw_resets is None:
        return Record(name, NO_CURRENT, percent=percent, freshness=freshness,
                      note="no reset time reported by the backend")
    return _window_row(
        name=name, percent=percent, resets=_from_epoch(raw_resets, EPOCH_SECONDS),
        freshness=freshness, now=now, active=True,
        stale_note="the window shown had already reset when this was read",
    )


def _codex_records(home: Path) -> list[Record]:
    result = _appserver_result(home)
    now = datetime.datetime.now(datetime.timezone.utc)  # observation time, not run-start time
    freshness = f"live {_local(now)}"

    default = result.get("rateLimits")
    by_id = result.get("rateLimitsByLimitId")
    if by_id is not None and not isinstance(by_id, dict):
        raise Malformed("payload-malformed")

    pools: dict[str, dict] = {}
    records: list[Record] = []
    if by_id:  # a dict by the guard above, so truthiness is the emptiness test and nothing more
        for key, pool in by_id.items():
            try:
                pools[_text(key, 64)] = _obj(pool)
            except Malformed as exc:
                records.append(Record(f"limitId[{len(records)}]", GAP, diagnostic=exc.code))
    elif isinstance(default, dict):
        # `limitId` is `string | null`, and a .get default answers only the ABSENT case.
        raw_id = default.get("limitId")
        pools["codex" if raw_id is None else _text(raw_id, 64)] = default
    else:
        raise Malformed("payload-malformed")

    for limit_id in sorted(pools):
        pool = pools[limit_id]
        # Identity is (limitId, slot); only the LABEL comes from the duration. Two limit ids were
        # measured carrying a 10080-minute window at the same time, and one POOL can carry two
        # windows of equal duration, so the slot is appended whenever the label alone collides.
        primary_mins = _label_duration(pool.get("primary"))
        collide = (primary_mins is not None
                   and primary_mins == _label_duration(pool.get("secondary")))
        for slot in ("primary", "secondary"):
            window = pool.get(slot)
            if window is None:
                continue
            records.append(_row_or_gap(f"{limit_id}/{slot}", lambda: _codex_window_row(
                limit_id=limit_id, slot=slot, window=window, collide=collide,
                freshness=freshness, now=now)))
    if not records:
        raise Malformed("payload-malformed")

    # `rateLimitResetCredits` is `Summary | null` and is absent from the schema's `required`
    # list, so neither absence nor null is drift: it is the backend saying it has no
    # reset-credit data. Reported as a known absence, never as a gap -- an account whose backend
    # does not provide it could otherwise never produce a clean report. A present container of
    # the wrong shape still gaps, because that one the schema does forbid.
    coupons = result.get("rateLimitResetCredits")
    if coupons is None:
        records.append(Record(
            VOUCHER_NAME, NO_CURRENT, freshness="not reported", info=True,
            note="the backend provided no reset-credit data",
        ))
    else:
        try:
            count = _nonneg_int(_obj(coupons).get("availableCount"))
        except Malformed as exc:
            records.append(Record(VOUCHER_NAME, GAP, diagnostic=exc.code))
        else:
            # Expiry and title are read from the FIRST available credit. Both are optional in the
            # payload and neither may gap the run: a home reporting a count and nothing else
            # still renders, with less to say. A voucher that silently expires unnoticed is the
            # whole reason the date is worth surfacing -- the count alone never says when.
            expires, title = None, ""
            # Every read below is DETAIL, and detail may never gap a candidate: the count has
            # already validated, and a home whose optional extras are malformed still has a
            # number worth printing. So `credits` must be a list before it is iterated -- a bare
            # `7` there would raise TypeError straight past this handler and take every valid
            # usage row in the home with it -- and a title this module would refuse is simply
            # dropped rather than raised.
            listed = _obj(coupons).get("credits")
            for credit in (listed if isinstance(listed, list) else []):
                if not isinstance(credit, dict) or credit.get("status") != "available":
                    continue
                try:
                    expires = _from_epoch(credit.get("expiresAt"), 1)
                except Malformed:
                    expires = None
                try:
                    title = _text(credit.get("title"), 40)
                except Malformed:
                    title = ""
                break
            records.append(Record(
                VOUCHER_NAME, REPORTED, freshness=str(count), info=True,
                expires=expires, title=title,
                note="read only; redeem one in the Codex TUI with /usage, never from here",
            ))

    credits = default.get("credits") if isinstance(default, dict) else None
    if credits is not None and not isinstance(credits, dict):
        records.append(Record(CREDITS_NAME, GAP, diagnostic="payload-malformed"))
    elif isinstance(credits, dict):
        try:
            if _flag(credits.get("hasCredits")):
                records.append(Record(CREDITS_NAME, REPORTED, info=True,
                                      freshness=f"{_decimal(credits.get('balance')):.2f}"))
        except Malformed as exc:
            records.append(Record(CREDITS_NAME, GAP, diagnostic=exc.code))
    return records


# --- candidates ---------------------------------------------------------------------------------


class Candidate:
    def __init__(self, path: Path, gap: str = "") -> None:
        self.path = path
        self.gap = gap


def _explicit(path: Path) -> Candidate:
    """An explicitly named candidate still gets a probe, and is made absolute first.

    Discovery cannot hand over a directory that is not there, but an argument can, and without
    the probe a typo'd or moved profile reaches the producer, finds nothing, and reports a clean
    "no cache" -- a verdict about a profile that was never examined at all.

    Absolute because the Keychain service name is the hash of the profile's absolute path, and
    only discovery produces one of those for free. `--claude-profile .claude-work` from the
    parent directory otherwise hashes the two-word spelling the caller typed, finds no item, and
    gaps as `token-absent` -- the safe direction, but a documented invocation form that cannot
    work. abspath, not resolve(): normalising is the fix, and following a symlink would hash a
    path the vendor never stored.
    """
    path = Path(os.path.abspath(path))
    if _stat_kind(path) != "dir":
        return Candidate(path, "candidate-unreadable")
    return Candidate(path)


def _stat_kind(path: Path) -> str:
    """One of `dir`, `other`, `absent`, `unreadable`. Never a bare boolean.

    Deliberately `os.stat`, not `Path.is_dir()` / `Path.exists()`: those RAISE PermissionError up
    to 3.13 and SWALLOW it from 3.14, returning False. So neither a try/except nor a truth test
    can tell "not a directory" from "cannot look", and on exactly the interpreter CI pins an
    unreadable candidate would silently vanish before the ledger. `os.stat` reports the error on
    every version. The sibling health check answers this the same way.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    return "dir" if stat.S_ISDIR(info.st_mode) else "other"


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
        kind = _stat_kind(entry)
        if kind == "unreadable":
            out.append(Candidate(entry, "candidate-unreadable"))
            continue
        if kind != "dir":
            continue
        kinds = [_stat_kind(entry / marker) for marker in markers]
        if "unreadable" in kinds:
            out.append(Candidate(entry, "candidate-unreadable"))
            continue
        if any(k != "absent" for k in kinds):
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


def _pool_cells(where: str, record: Record, now: datetime.datetime) -> tuple[str, str, str, str]:
    """(where, pool, used, reset) as PLAIN text. Painting happens after the widths are known."""
    used = "" if record.percent is None else f"{_bar(record.percent)} {record.percent:>5.1f}%"
    if record.state == GAP:
        used = f"[{record.diagnostic}]"
        reset = ""
    elif record.resets is None:
        # No reset time is not one condition. A pool the vendor calls inactive and a pool whose
        # `resetsAt` the vendor simply did not send both arrive here, and an empty cell would
        # make them the same row. The note is what separates them, so it becomes the cell -- in
        # its short form, keyed on the exact strings the producers write. The full sentence would
        # set the RESET column's width for EVERY block, since the blocks share one layout, so one
        # inactive pool's prose would stretch the column the current rows are read in.
        reset = RESET_LABELS.get(record.note, record.note)
    else:
        reset = _relative(record.resets, now)
    return where, _safe_name(record.name), used, reset


def _sorted_rows(rows: list[Row]) -> list[Row]:
    """Most consumed first, and TOTAL -- no row's position may depend on discovery order.

    The key stops at "most consumed"; it is deliberately not a projected-exhaustion score, which
    would need a burn rate nobody here measures and would put an invented number in the column
    read first. Ordering across lifecycle classes is handled by SECTIONING, not by this key:
    a stale 99% is not more urgent than a live 40%, it is not comparable to it.
    """
    return sorted(rows, key=lambda row: (
        -(row.record.percent if row.record.percent is not None else -1.0),
        row.record.resets.timestamp() if row.record.resets is not None else float("inf"),
        row.where, row.record.name, row.record.freshness,
    ))


def _voucher_band(vouchers: list[Row], infos: list[Row], now: datetime.datetime,
                  paint: Paint) -> None:
    """The block above the table: a voucher count per home, then any credit balance.

    Keyed by HOME rather than by a pool name, because that is the only thing that varies -- every
    row here would otherwise read `reset vouchers` down the page.
    """
    if not vouchers and not infos:
        return
    print(f"  {paint('RESET VOUCHERS', CYAN + ';' + BOLD)}   "
          + paint("a one-shot rate-limit reset -- redeem in the Codex TUI with /usage", DIM))
    home_width = max([_width(_safe_name(row.where)) for row in vouchers]
                     + [_width(_safe_name(row.record.name)) for row in infos] + [8]) + 2
    for where, _group, record in vouchers:
        if record.state == GAP:
            body = paint(f"[{record.diagnostic}]", RED)
        elif record.state == NO_CURRENT or record.freshness == "0":
            # Dimmed, never reworded. `0` is a REPORTED count and must read as the integer it
            # is -- printing "none" for it makes a measured zero indistinguishable from the
            # `not reported` a backend sends when it has no voucher data at all.
            body = paint(record.freshness, DIM)
        else:
            body = paint(record.freshness, BOLD + ";" + GREEN)
            if record.title:
                # QUOTED, and through _safe_name like every other printed vendor string. This is
                # the slot the report's own `expires <date>  in <N>d` pair occupies, so a title
                # reading "expires 31 Dec 2099 CEST  in 9999d" sits exactly where the real thing
                # would when the payload omits `expiresAt`. It is also the one printed field that
                # would otherwise render a no-break space as an invisible space rather than as an
                # escape, because it is the only one that never passed through _safe_name.
                body += f'  "{_safe_name(record.title)}"'
            if record.expires is not None:
                # A voucher that has already lapsed is not a window that reset: `_relative` would
                # say "reset 400d ago", which is the wrong vocabulary for the wrong noun, and a
                # lapsed voucher is exactly the case worth stating plainly.
                left = "expired" if record.expires <= now else _relative(record.expires, now)
                body += ("  " + paint(f"expires {_local(record.expires)}", DIM)
                         + "  " + paint(left, YELLOW))
        print("    " + _pad(_safe_name(where), home_width) + body)
    for where, _group, record in infos:
        value = (paint(f"[{record.diagnostic}]", RED) if record.state == GAP
                 else paint(record.freshness, BOLD))
        print("    " + _pad(_safe_name(record.name), home_width)
              + paint(_safe_name(where), DIM) + " " + value)
    print()


COLUMNS = ("WHERE", "POOL", "USED", "RESET")

# Keyed on the producers' exact notes, never on a substring of them: a note that drifts falls
# through to itself, which is wide but correct, rather than being silently mislabelled.
RESET_LABELS = {
    "inactive, no current window": "inactive",
    "inactive, no reset time reported": "inactive",
    "no reset time reported by the backend": "not reported",
}


def _column_widths(blocks: list[list[Row]], now: datetime.datetime) -> list[int]:
    """One width per column, measured across EVERY pool block and floored by the header labels.

    Two failures come from measuring a block on its own. Sections would each pick their own column
    starts, so the stale block below the table would not line up with it -- a table whose columns
    move between sections is not a table. And a run of short values (a profile named `.a`, a
    one-character pool id) would make a column narrower than the word naming it, printing
    `WHEREPOOLUSED` with nothing between the labels. The header labels are therefore a FLOOR, not
    an afterthought.
    """
    cells = [_pool_cells(row.where, row.record, now) for block in blocks for row in block]
    return [max([_width(cell[index]) for cell in cells] + [len(COLUMNS[index])]) + 2
            for index in range(4)]


def _pool_table(rows: list[Row], widths: list[int], now: datetime.datetime, paint: Paint,
                heading: str = "") -> None:
    """One block of pool rows, laid out on widths shared with every other block."""
    if not rows:
        return
    cells = [_pool_cells(row.where, row.record, now) for row in rows]
    if heading:
        print(paint(f"  {heading}", DIM))
    # A section's dim is applied PER CELL, not by wrapping the finished line. Every paint() emits
    # its own reset, so wrapping a line that already contains one cancels the dim for everything
    # after that cell -- the row then renders half dim and half not, which reads as an artefact
    # rather than as a section.
    tone = DIM + ";" if heading else ""
    for (where, pool, used, reset), row in zip(cells, rows):
        record = row.record
        if record.state == GAP:
            # `used` is the diagnostic token here, not a gauge -- see _pool_cells.
            body = paint(used, tone + RED)
        elif record.percent is not None:
            # One paint over the finished cell. The gauge and the number always take the same
            # hue, so painting them apart bought nothing and cost a `used.split(" ", 1)` -- a
            # ValueError the day _pool_cells formats this cell without a space in it.
            body = paint(used, tone + _hue(record.percent))
        else:
            body = paint(used, DIM) if heading else used
        # `body` may carry escapes; the padding is computed from the PLAIN `used`.
        pad = " " * max(0, widths[2] - _width(used))
        head_cells = _pad(where, widths[0]) + _pad(pool, widths[1])
        source = _source(record.freshness, row.group)
        if heading:
            # A whole row of a non-current section is dim, one paint per run of cells.
            tail = paint(_pad(reset, widths[3]) + source, DIM)
            head = paint(head_cells, DIM)
        else:
            # In the current section only the provenance recedes; the reset is a fact worth
            # reading at full weight.
            tail = _pad(reset, widths[3]) + paint(source, DIM)
            head = head_cells
        print("  " + head + body + pad + tail)
    print()


def _render(groups: list[tuple[str, str, list[Record]]], notes: list[str],
            now: datetime.datetime, paint: Paint) -> None:
    """The whole report: a voucher band, one table of pools, then the classes that are not
    current usage, each under a heading that says why they are apart."""
    buckets: dict[str, list[Row]] = {
        "current": [], "stale": [], "inactive": [], "gap": [], "voucher": [], "info": []}
    for group, where, records in groups:
        for record in records:
            buckets[record.kind()].append(Row(where, group, record))

    print(f"\n{paint('code-limits', BOLD)}  {paint(_local(now), DIM)}\n")
    _voucher_band(buckets["voucher"], buckets["info"], now, paint)
    blocks = [_sorted_rows(buckets[name]) for name in ("current", "stale", "inactive", "gap")]
    if any(blocks):
        # The header goes above the FIRST non-empty block, whichever it is. Printing it only with
        # the current block meant a report whose pools were all stale or all gapped had a table
        # with no column names at all.
        widths = _column_widths(blocks, now)
        head = ("  " + _pad("WHERE", widths[0]) + _pad("POOL", widths[1])
                + _pad("USED", widths[2]) + _pad("RESET", widths[3]) + "SOURCE")
        print(paint(head, DIM))
        print(paint("  " + "\u2500" * (sum(widths) + 6), DIM))
    else:
        widths = [0, 0, 0, 0]
    _pool_table(blocks[0], widths, now, paint)
    _pool_table(blocks[1], widths, now, paint,
                "stale -- the % is the PREVIOUS window, not current  [stale-after-reset]")
    _pool_table(blocks[2], widths, now, paint, "inactive -- no current window")
    _pool_table(blocks[3], widths, now, paint, "NOT CHECKED")

    for note in notes:
        print(paint(f"  {note}", DIM))
    print(paint("  --live fetches current Claude numbers instead of the on-disk cache", DIM))
    print(paint("  reading Codex starts its app-server, which migrates that home's own state"
                " databases", DIM))
    print(paint("  exactly as any codex invocation does. Nothing here is ever redeemed.", DIM))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Claude Code and Codex usage limits.")
    parser.add_argument("--live", action="store_true",
                        help="fetch Claude usage instead of reading the on-disk cache")
    parser.add_argument("--claude-profile", action="append", default=[], metavar="PATH",
                        help="examine this Claude profile (repeatable; replaces discovery)")
    parser.add_argument("--codex-home", action="append", default=[], metavar="PATH",
                        help="examine this Codex home (repeatable; replaces discovery)")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="colourise the report (default: auto -- a terminal, NO_COLOR unset)")
    args = parser.parse_args(argv)
    paint = Paint(_use_colour(args.color, sys.stdout))

    # Neither vendor labels nor profile directory names are under this script's control, and an
    # unencodable character in either raises out of print() -- past every per-candidate handler,
    # ending the run with a traceback and no warnings, which is the one failure mode the exit
    # contract cannot describe. Escaping is the right answer for a NAME the machine gave us;
    # a vendor FIELD carrying one is refused in _text instead, so it gaps its own record.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    home = Path(os.path.expanduser("~"))
    now = datetime.datetime.now(datetime.timezone.utc)

    claude = ([_explicit(Path(p)) for p in args.claude_profile]
              or _discover(home, ".claude", (".claude.json", ".credentials.json")))
    codex = ([_explicit(Path(p)) for p in args.codex_home]
             or _discover(home, ".codex", ("auth.json", "config.toml")))

    warnings: list[str] = []
    notes: list[str] = []
    groups: list[tuple[str, str, list[Record]]] = []

    # Only the cached reader takes the run's clock: it dates rows from a file that was
    # written before the run. The live readers time-stamp their own observation, because
    # a keychain prompt or a stalled app-server can put minutes between run start and the
    # answer they are describing.
    claude_producer = _claude_live if args.live else (lambda profile: _claude_cached(profile, now))
    for group, candidates, producer in (
        (CLAUDE_GROUP, claude, claude_producer),
        (CODEX_GROUP, codex, _codex_records),
    ):
        if not candidates:
            # The same sentence in both places on purpose: the report body says what was not
            # looked at, and the warning block is what sets the exit status on it.
            missing = f"{group}: no candidates found -- NOT checked"
            warnings.append(missing)
            notes.append(missing)
            continue
        for candidate in candidates:
            state, records, code = _examine(candidate, producer)
            where = _safe_name(candidate.path.name)
            if code:
                # A candidate-level outcome has no pool to hang a row on, so it becomes a note.
                # It still decides the exit status below, exactly as before.
                notes.append(f"{where} [{code}]")
            groups.append((group, where, records))
            if state == GAP:
                detail = code or ", ".join(f"{r.name} [{r.diagnostic}]"
                                           for r in records if r.state == GAP)
                warnings.append(f"{group} {where}: NOT checked -- {detail}")

    _render(groups, notes, now, paint)

    if warnings:
        print(paint("\nwarnings", RED))
        for warning in warnings:
            print(f"  {warning}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
