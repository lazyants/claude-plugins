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
KNOWN_ABSENT = frozenset({"no-usage-cache"})

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
    difference. Combining marks are the same error pointed the other way. East-Asian AMBIGUOUS
    counts as ONE here, which the overwhelmingly common configuration renders narrow -- what
    reaches this rule now is a vendor pool name or a profile directory name, and a terminal
    configured otherwise draws every one of them wider, which shifts a whole column rather than
    one row inside it.
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


def _figure(percent: float) -> str:
    """A percentage with no trailing zero to pad the column out: `0`, `7.5`, `100`.

    Three window columns each spending a fixed `%5.1f` pushed the table past the width a
    terminal shows without wrapping, and the `.0` it bought that room for was never a
    measurement: every vendor here reports whole numbers today, and the one whose schema allows
    a fraction still prints it.
    """
    return f"{percent:.1f}".rstrip("0").rstrip(".") or "0"


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
    """`in 8h`, `in 5d 21h`, `3d 02h ago`. Absolute stamps are for the header and the vouchers.

    A window's usefulness is how long is LEFT, and a reader should not have to subtract dates to
    get it. The absolute time survives where it is the fact itself -- a voucher's expiry date.

    The past direction says only `ago`. It used to open with the word `reset`, which cost six
    columns in the widest cell on the page -- enough to wrap the table on a terminal that would
    otherwise hold it -- to repeat what the column heading and the legend below already say.
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
    return f"in {span}" if delta.total_seconds() > 0 else f"{span} ago"


class Record:
    """One pool observation, one reason there is no current value for it, or one info line."""

    def __init__(self, name: str, state: str, *, percent=None, resets=None,
                 freshness: str = "", diagnostic: str = "", note: str = "",
                 info: bool = False, expires=None, title: str = "",
                 family: str = "", window: str = "") -> None:
        if state not in TERMINAL_STATES:
            raise Malformed("internal-error")
        self.name = name
        self.state = state
        # WHERE this observation sits in the table: `family` names the allowance -- one line --
        # and `window` names the column across it. Both default to the record's own name, so a
        # record built without them opens its own row and its own column instead of silently
        # sharing a cell with a pool it has nothing to do with.
        self.family = family or name
        self.window = window or name
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

    `ident` is the candidate's own absolute path and is what rows are GROUPED by; `where` is
    only ever printed. They are different fields because the printable name is lossy: two
    candidates can share a basename (`--claude-profile` is repeatable, and takes paths under
    different parents), and a directory name carrying a non-printable character is escaped to
    print, which maps two distinct directories onto one string. Grouped by the printed name, two
    accounts merged into ONE row -- one supplying the 5h figure, the other the weekly, under one
    account's freshness, exiting 0. The ambiguity of two rows both reading `.claude` is older
    than this and stays; silently attributing one account's usage to another does not.

    The vendor group travels WITH the row because `_source` needs it to keep a live Claude row
    distinguishable from a live Codex one, and the bucketing loop is the last place that still
    knows it. The obvious alternative -- looking the group back up out of a side table keyed on
    `id(record)` -- needs a default for the miss, and any default there prints a wrong vendor
    with a right one's confidence, unfalsifiable from the page.
    """

    ident: str
    where: str
    group: str
    record: Record


def _window_row(*, name: str, percent: float, resets: datetime.datetime, freshness: str,
                now: datetime.datetime, family: str = "", window: str = "",
                stale_note: str = "current window unknown without --live") -> Record:
    """A window with a reset time. What decides whether it is CURRENT is that time and nothing
    else -- past means the figure describes the previous window, future means it describes this
    one.

    `is_active` deliberately does not enter here. MEASURED across four accounts: exactly one pool
    per account carries it, and it is whichever one is currently BINDING -- `.claude` had it on
    `weekly_all` while its five-hour window, 9% used and resetting in 17 minutes, carried
    `is_active: false`. Treated as "no current window" that greyed out a perfectly current
    number as not comparable to the cell beside it, and greyed out the reset time with it. Which
    pool binds first is worth knowing; it is not a reason to withdraw a figure from the page.
    """
    place = {"family": family, "window": window}
    if resets <= now:
        return Record(name, NO_CURRENT, percent=percent, resets=resets, freshness=freshness,
                      diagnostic="stale-after-reset", note=stale_note, **place)
    return Record(name, REPORTED, percent=percent, resets=resets, freshness=freshness, **place)


def _row_or_gap(name: str, produce, *, family: str = "", window: str = "") -> Record:
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
        return Record(name, GAP, diagnostic=exc.code, family=family, window=window)
    except Exception:
        return Record(name, GAP, diagnostic="internal-error", family=family, window=window)


# --- Claude Code -------------------------------------------------------------------------------

# The allowance a `session`/`weekly_all` pair belongs to. They are two windows on ONE quota,
# which is exactly why they belong on one line rather than on two that repeat the profile name.
CLAUDE_ALL = "all"
CLAUDE_KINDS = {"session": (CLAUDE_ALL, "5h"), "weekly_all": (CLAUDE_ALL, "weekly"),
                "five_hour": (CLAUDE_ALL, "5h"), "seven_day": (CLAUDE_ALL, "weekly")}


def _claude_place(kind: str) -> tuple[str, str]:
    """(allowance, window) for one vendor kind. Total -- an unknown kind is never folded away.

    A kind this table has no entry for keeps its own name in BOTH places, so it opens its own row
    and its own column: wide, but it states what it is. Inferring a place from the kind's PREFIX
    read like harmless tidiness and was a guess about an allowance nobody had measured -- folded
    into another pool's row, an unrecognised window either overwrites a real number or hides
    behind one.
    """
    return CLAUDE_KINDS.get(kind, (kind, kind))


def _claude_rows(utilization: dict, freshness: str, now: datetime.datetime) -> list[Record]:
    """Rows from a `utilization` object, in either of the two shapes Claude Code emits."""
    entries = utilization.get("limits")
    records: list[Record] = []
    if entries is not None:
        if not isinstance(entries, list):
            raise Malformed("payload-malformed")
        # The model-scoped weekly pool is not shown. It read `0%` on every account measured, and
        # a column that is empty on every row but one costs the table more width than the pool
        # is worth. No rescue when it is the only entry: putting back the pool this report is
        # told to hide is a substitution like any other, and an account carrying nothing else is
        # a shape the code says the vendor does not produce -- `session` and `weekly_all` ship
        # beside it. Should it ever occur, the container yields no record and the profile gaps
        # visibly, which is the answer to a payload nobody can read as asked.
        # Skipped inside the loop rather than filtered ahead of it, so `index` stays the entry's
        # position in the PAYLOAD: over a filtered list, one scoped entry ahead of a malformed one
        # shifted every label under it, and a warning reading `limits[1]` named payload entry 2 --
        # against what _claude_raw_place's own docstring promises the number means.
        for index, item in enumerate(entries):
            if _is_scoped(item):
                continue
            # Per RECORD, not per candidate. One malformed entry must not suppress its siblings:
            # the valid pools are exactly what the operator opened the report to see, and a
            # profile that prints nothing looks identical to one that has nothing. The entry's
            # own SHAPE is validated inside the producer for the same reason -- a container pass
            # that rejected a non-object entry ahead of the loop lost every valid sibling.
            records.append(_row_or_gap(
                f"limits[{index}]", lambda: _claude_row(_obj(item), freshness, now),
                **_claude_raw_place(item)))
        return records

    for key in ("five_hour", "seven_day"):
        slot = utilization.get(key)
        if slot is None:
            continue
        # The place needs no vendor data here -- it follows from `key`, which is one of the two
        # names this branch loops over. The list shape needed `_claude_raw_place` for the same
        # guarantee; leaving it off this one put a gapped flat window in a row and a column of its
        # own while its healthy twin sat under `all (flat)`.
        family, window = _claude_place(key)
        records.append(_row_or_gap(
            f"{key} (flat)", lambda: _claude_flat_row(key, slot, freshness, now),
            family=f"{family} (flat)", window=window))
    return records


def _claude_flat_row(key: str, slot, freshness: str, now: datetime.datetime) -> Record:
    """One row of the older flat shape, whose windows are named keys rather than list items."""
    body = _obj(slot)
    # `(flat)` rides the ALLOWANCE, which is the only cell on the row that names it now that
    # the window is a column heading. Which container shape answered is worth disclosing: the
    # flat one is a fallback, and a report that used it silently reads like the current shape.
    family, window = _claude_place(key)
    family = f"{family} (flat)"
    name = f"{key} (flat)"
    percent = _num(body.get("utilization"), 0.0, 100.0)
    if body.get("resets_at") is None:
        # The same unopened window the list shape reports, in the shape that has no `is_active`
        # to consult: the profile measured here carries `{"utilization": 0, "resets_at": null}`
        # under BOTH `five_hour` and `seven_day` while its `limits` array says the identical
        # thing. Reading the null as drift gapped a window whose only fault was never having
        # been used. Above zero it is still malformed -- consumption means a window opened.
        # An ABSENT `resets_at` and an explicit null are the same answer here, as they already
        # are in the list shape: neither states a reset time, and the reader has never had a
        # reason to tell "the vendor said null" from "the vendor said nothing" apart.
        if percent > 0:
            raise Malformed("field-malformed")
        return Record(name, NO_CURRENT, percent=percent, freshness=freshness,
                      note="no window opened yet", family=family, window=window)
    return _window_row(
        name=name,
        percent=percent,
        resets=_from_iso(body.get("resets_at")),
        freshness=freshness, now=now, family=family, window=window,
    )


def _claude_raw_place(item) -> dict:
    """(family, window) read off the RAW entry, before anything validates it.

    A record that GAPS has no validated kind to place it by, and the fallback names it after its
    INDEX in the payload -- so a malformed `session` opened a row AND a column both called
    `limits[0]`, while its healthy weekly sibling sat under `all`. The placement is derivable
    without trusting the entry: read the kind defensively, and answer nothing at all if it is not
    the plain printable string the vendor's schema says it is.
    """
    if not isinstance(item, dict):
        return {}
    kind = item.get("kind")
    if not isinstance(kind, str) or not kind or not kind.isprintable():
        return {}
    family, window = _claude_place(kind)
    return {"family": family, "window": window}


def _is_scoped(item) -> bool:
    """Whether an entry is the model-scoped weekly pool, which the report does not show.

    Read off the RAW entry, before anything validates it, so it may only answer yes for
    something that unambiguously IS one -- anything else falls through to the normal path and
    gaps there on its own terms rather than being quietly dropped by a guess made here.
    """
    return isinstance(item, dict) and item.get("kind") == "weekly_scoped"


def _claude_row(entry: dict, freshness: str, now: datetime.datetime) -> Record:
    kind = _text(entry.get("kind"), 64)
    name = kind
    scope = entry.get("scope")
    if scope is not None:
        model = _obj(scope).get("model")
        if model is not None:
            raw = _obj(model).get("display_name")
            if raw is not None:
                # Kept in the record NAME, which is what a warning prints, so a scoped pool that
                # could not be read still identifies itself. It names no row and no column: the
                # only kinds this report lays out are the ones CLAUDE_KINDS knows.
                name = f"{kind} ({_text(raw, 64)})"
    family, window = _claude_place(kind)
    active = _flag(entry.get("is_active")) if "is_active" in entry else True
    percent = _num(entry.get("percent"), 0.0, 100.0)
    if entry.get("resets_at") is None:
        # Measured on this machine, not hypothesised: a `weekly_scoped` entry ships
        # `resets_at: null` with `is_active: false`. That is the vendor reporting a pool with no
        # current window, so gapping the whole profile over it would be a check its owner could
        # never clear.
        #
        # `is_active` is NOT what decides this, and reading it as though it were gapped a
        # perfectly readable account: a profile that has consumed nothing ships
        # `{"kind": "session", "percent": 0, "resets_at": null, "is_active": true}` -- the
        # binding pool of an account whose window has never OPENED, so there is nothing for the
        # vendor to say a reset time about. Its flat twins carry the same `utilization: 0`
        # with `resets_at: null`. CONSUMPTION is what makes the pair contradictory: an ACTIVE
        # pool above zero has a window open by definition, and a window that is open and reports
        # no reset time is malformed. Nothing an inactive entry says is newly refused here --
        # tightening that arm too would gap a pool whose window merely closed, which no
        # measurement here calls malformed.
        if active and percent > 0:
            raise Malformed("field-malformed")
        note = "inactive, no reset time reported" if not active else "no window opened yet"
        return Record(name, NO_CURRENT, percent=percent, freshness=freshness,
                      note=note, family=family, window=window)
    return _window_row(
        name=name,
        percent=percent,
        resets=_from_iso(entry.get("resets_at")),
        freshness=freshness,
        now=now,
        family=family,
        window=window,
    )


def _claude_config(profile: Path) -> Path:
    """The file a Claude profile keeps its cached usage in.

    MEASURED, not assumed. The vendor's own diagnostics name "CLAUDE_CONFIG_DIR, the HOME it
    defaults from" as where settings are found and call the file `~/.claude.json`, so the path
    is `<CLAUDE_CONFIG_DIR or $HOME>/.claude.json` -- and the DEFAULT profile's config sits
    BESIDE `~/.claude`, not inside it. `~/.claude` is that profile's data directory only.

    Reproduced here: `~/.claude/.claude.json` also exists, as a leftover from an older layout --
    weeks stale, carrying an account but no `cachedUsageUtilization` at all. So "a config file
    is there" was mistaken for "the right config file is there", and the default account
    reported `no-usage-cache` while its weekly pool sat at 87%. A profile named explicitly by
    its own path is treated the same way, because it is the same directory either way.
    """
    home = Path(os.path.expanduser("~"))
    if profile == home / ".claude":
        return home / ".claude.json"
    return profile / ".claude.json"


def _claude_cached(profile: Path, now: datetime.datetime) -> list[Record]:
    """Records for one Claude profile from its cache. The container is validated FIRST.

    A container that parses but yields zero recognised records is a gap, not an empty success: a
    loop that runs zero times otherwise prints exactly what a passing one prints.
    """
    try:
        with open(_claude_config(profile), encoding="utf-8") as handle:
            blob = json.load(handle)
    except FileNotFoundError:
        raise Malformed("no-usage-cache") from None
    except (OSError, ValueError):
        raise Malformed("payload-malformed") from None

    if not isinstance(blob, dict):
        raise Malformed("payload-malformed")

    cached = blob.get("cachedUsageUtilization")
    if cached is None:
        # An absent cache is reported as an absent cache, and `hasAvailableSubscription` is not
        # consulted -- not to suppress a profile, and not to WORD its absence either.
        #
        # MEASURED across every profile on this machine: the flag reads false on accounts that
        # are demonstrably subscribed. Two ship it beside a full, freshly fetched `limits`
        # array, one of them at 100% of its weekly pool; reading it as "not subscribed"
        # suppressed exactly the account whose number mattered most, under a diagnostic that
        # exits 0, so nothing anywhere said a pool had gone unread. The third ships it beside
        # no cache at all, on the DEFAULT profile of a Claude Max 20x subscription whose pools
        # the --live path reads without trouble -- and the report answered "where is this
        # account" with `no-subscription`, a claim about billing this module never verified and
        # has no way to verify. The flag's only surviving reading is "this says nothing".
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


def _codex_spent_from(default, pools: dict[str, dict]) -> dict[str, dict]:
    """Only the pool the CLI actually spends from, out of everything the backend lists.

    `rateLimitsByLimitId` enumerates pools that are not what a person at a terminal is asking
    about -- a model-specific one (`codex_bengalfox`, which the backend calls
    `GPT-5.3-Codex-Spark`) and a reserve (`base_model_inference`, `gpt-reserve`). Only one of
    them answers "how much can I still use here", and the backend already says which: the
    top-level `rateLimits` object carries that pool's `limitId`.

    Read from the vendor's own pointer rather than from a hardcoded `"codex"`. When the map does
    not hold the pool that pointer names, the TOP-LEVEL object is that pool -- it carries the same
    windows under the same id, which is why the no-map path already reads it.

    A selector that resolves to NEITHER gaps the home. It has no third answer: every fallback
    tried here substituted a different pool, and printing a reserve or a model-specific pool in
    the place the operator reads their terminal quota is worse than saying the quota could not be
    determined -- twice over, because it also exits 0. Semantic uncertainty is never promoted to
    success.
    """
    if not isinstance(default, dict):
        raise Malformed("payload-malformed")
    raw_id = default.get("limitId")
    # `limitId` is `string | null`; a .get default answers only the ABSENT case, and null is the
    # backend using the same name the single-pool path already falls back to. A limitId this
    # module refuses raises out of _text and gaps the home, which is the point.
    chosen = "codex" if raw_id is None else _text(raw_id, 64)
    if chosen in pools:
        return {chosen: pools[chosen]}
    if any(isinstance(default.get(slot), dict) for slot in ("primary", "secondary")):
        return {chosen: default}
    raise Malformed("payload-malformed")


def _codex_labels(pools: dict[str, dict]) -> dict[str, str]:
    """The name to PRINT for each limit id -- the vendor's own `limitName` where it sent one.

    `limitName` is `string | null` and optional in the schema, and it is the only readable name
    the backend offers: `codex_bengalfox` and `base_model_inference` are internal ids for pools
    it calls `GPT-5.3-Codex-Spark` and `gpt-reserve`. Printing the id put a name nobody outside
    the vendor can read in the column the report is scanned by, with the readable one sitting
    unused in the same object.

    Detail may never gap a candidate -- the same rule the voucher title lives under: a name this
    module would refuse is dropped and the id stands in, because a home whose optional extras are
    malformed still has numbers worth printing.
    """
    labels: dict[str, str] = {}
    for limit_id, pool in pools.items():
        raw = pool.get("limitName")
        try:
            # `.strip()` decides only whether a name EXISTS -- the name itself is printed as the
            # vendor spelled it. A `limitName` of nothing but spaces passes `_text`, which allows
            # Zs deliberately, and would render a pool with no visible name at all.
            name = "" if raw is None else _text(raw, 64)
            labels[limit_id] = name if name.strip() else limit_id
        except Malformed:
            labels[limit_id] = limit_id
    return labels


def _codex_window_row(*, limit_id: str, label: str, slot: str, window, collide: bool,
                      freshness: str, now: datetime.datetime) -> Record:
    """One Codex window. Raises Malformed only for a shape the vendor's schema does not permit."""
    body = _obj(window)
    # `windowDurationMins` and `resetsAt` are both `integer | null` in the vendor's own schema,
    # so a null is the backend declining to provide the value, not drift. Gapping on it would
    # leave such an account unable to report cleanly whatever its owner did -- a check nobody can
    # satisfy is worse than the gap it closes.
    raw_minutes = body.get("windowDurationMins")
    minutes = None if raw_minutes is None else _pos_int(raw_minutes)
    window = slot if minutes is None else _window_label(minutes)
    name = f"{limit_id}/{window}"
    # Identity is (limitId, slot). When one pool carries two windows of EQUAL duration the label
    # alone no longer separates them, so the slot joins the allowance's name and the two land on
    # two rows of the same column -- never in one cell, where the second would overwrite the
    # first and the report would be short a number it was handed.
    family = f"{label}({slot})" if collide else label
    if collide:
        name = f"{name}({slot})"
    percent = _num(body.get("usedPercent"), 0.0, PERCENT_MAX)
    raw_resets = body.get("resetsAt")
    if raw_resets is None:
        return Record(name, NO_CURRENT, percent=percent, freshness=freshness,
                      note="no reset time reported by the backend", family=family, window=window)
    return _window_row(
        name=name, percent=percent, resets=_from_epoch(raw_resets, EPOCH_SECONDS),
        freshness=freshness, now=now, family=family, window=window,
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
        for index, (key, pool) in enumerate(by_id.items()):
            try:
                pools[_text(key, 64)] = _obj(pool)
            except Malformed as exc:
                # The index is the entry's position in the PAYLOAD. Numbering by how many gaps
                # had accumulated so far named a position nothing on the vendor's side has.
                records.append(Record(f"limitId[{index}]", GAP, diagnostic=exc.code))
    elif not isinstance(default, dict):
        raise Malformed("payload-malformed")
    # No branch body for the single-pool shape: _codex_spent_from already answers a pointer the
    # map does not hold from the top-level object, which IS that shape. Deriving the pointer here
    # too stated the rule this whole reader turns on -- which pool the CLI spends from -- in two
    # places, and left the corner where that object carries no window gapping the entire home
    # rather than the quota alone, against what the handler below says should happen.

    try:
        pools = _codex_spent_from(default, pools)
    except Malformed as exc:
        # One unreadable thing may not suppress its siblings -- the per-record boundary the rest
        # of this reader already holds, applied to the selector. WHICH pool the CLI spends from
        # is unresolvable, and that gaps; the voucher count and the credit balance below were
        # never part of that question and are still perfectly readable. Raising here took them
        # down with it. The record is named for the vendor's own field, so the column it opens
        # says what could not be read.
        records.append(Record("rateLimits", GAP, diagnostic=exc.code))
        pools = {}
    labels = _codex_labels(pools)
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
            # The place is passed IN, not left to the fallback: a window that gaps still
            # belongs to the pool it was read from, and a gap that names itself opens a row of
            # its own -- the operator then cannot see which allowance went unread.
            # The column comes from the DURATION where one is readable, exactly as the healthy
            # sibling's does -- `_label_duration` is already total and answers None for every
            # shape that has no usable one. Falling back to the RPC slot name put a malformed 5h
            # window under a column called PRIMARY, beside the very `5H` column it belongs in.
            minutes = _label_duration(window)
            records.append(_row_or_gap(f"{limit_id}/{slot}", lambda: _codex_window_row(
                limit_id=limit_id, label=labels[limit_id], slot=slot, window=window,
                collide=collide, freshness=freshness, now=now),
                family=labels[limit_id],
                window=slot if minutes is None else _window_label(minutes)))
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
                    expires = _from_epoch(credit.get("expiresAt"), EPOCH_SECONDS)
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


def _discover(root: Path, prefix: str, markers: tuple[str, ...], external=None) -> list[Candidate]:
    """Directories matching the prefix that carry a vendor marker.

    A marker probe that ERRORS yields a candidate carrying `candidate-unreadable`, rather than
    being filtered out: a profile that cannot be examined must not vanish before the ledger.

    `external` maps a candidate to one further file to probe, OUTSIDE the directory. The default
    Claude profile keeps its config beside `~/.claude` rather than inside it, so an installation
    that authenticates through the Keychain has neither in-directory marker -- and the account
    was dropped before the ledger, with no diagnostic, on a report that still exited 0 because
    the OTHER profiles made the candidate list non-empty. An absent-marker profile vanishing
    quietly is the one failure this whole discovery step exists to prevent.
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
        probes = [entry / marker for marker in markers]
        if external is not None:
            probes.append(external(entry))
        kinds = [_stat_kind(path) for path in probes]
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
    for _ident, where, _group, record in vouchers:
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
                # say "400d ago", which is the wrong vocabulary for the wrong noun, and a
                # lapsed voucher is exactly the case worth stating plainly.
                left = "expired" if record.expires <= now else _relative(record.expires, now)
                body += ("  " + paint(f"expires {_local(record.expires)}", DIM)
                         + "  " + paint(left, YELLOW))
        print("    " + _pad(_safe_name(where), home_width) + body)
    for _ident, where, _group, record in infos:
        value = (paint(f"[{record.diagnostic}]", RED) if record.state == GAP
                 else paint(record.freshness, BOLD))
        print("    " + _pad(_safe_name(record.name), home_width)
              + paint(_safe_name(where), DIM) + " " + value)
    print()


# The two fixed columns every row carries; the window columns are discovered from the data and
# appended after them, so a vendor that starts reporting a third window is not silently dropped.
COLUMNS = ("WHERE", "POOL")
# Reading order for the window columns. A label this report has no opinion about sorts after
# both, alphabetically, rather than displacing the two an operator actually scans for.
WINDOW_ORDER = {"5h": 0, "weekly": 1}

# Keyed on the producers' exact notes, never on a substring of them: a note that drifts falls
# through to itself, which is wide but correct, rather than being silently mislabelled.
RESET_LABELS = {
    "inactive, no reset time reported": "inactive",
    "no reset time reported by the backend": "not reported",
    "no window opened yet": "unopened",
}


class Pool:
    """One allowance on one candidate: every window of it, keyed by window label.

    The report's unit used to be the WINDOW, so a profile's 5h and weekly figures took two rows
    that repeated the same candidate and the same quota -- one account read twice, competing
    with itself for the reader's attention. The unit is the allowance; the windows are columns
    across it.
    """

    def __init__(self, ident: str, where: str, group: str, family: str) -> None:
        self.ident = ident
        self.where = where
        self.group = group
        self.family = family
        self.cells: dict[str, Record] = {}

    def current(self) -> list[Record]:
        return [record for record in self.cells.values() if record.kind() == "current"]

    def freshness(self) -> str:
        """The row's provenance. Every cell on a row comes from ONE read of ONE candidate, so
        the first non-empty answer is the row's; the loop is there so an empty one cannot win."""
        for record in self.cells.values():
            if record.freshness:
                return record.freshness
        return ""


def _pivot(rows: list[Row]) -> list[Pool]:
    """Rows to pools: one line per (candidate, allowance), one column per window label.

    A second record for a window a pool ALREADY holds opens a new line rather than overwriting
    it. Two windows of equal duration under one limit id is a shape the vendor's schema permits,
    and a report that silently drops one of them is worse than one that prints a family twice.
    """
    pools: list[Pool] = []
    latest: dict[tuple[str, str, str], Pool] = {}
    for row in rows:
        key = (row.ident, row.group, row.record.family)
        pool = latest.get(key)
        if pool is None or row.record.window in pool.cells:
            pool = Pool(row.ident, row.where, row.group, row.record.family)
            pools.append(pool)
            latest[key] = pool
        pool.cells[row.record.window] = row.record
    return pools


def _column_head(label: str) -> str:
    """The heading for one window column.

    The part before the slash is this report's own vocabulary and reads as a heading, so it is
    upper-cased. Anything after it is a VENDOR string -- a model's display name -- and keeps the
    spelling its owner gave it, escaped like every other printed name rather than case-folded
    into something the vendor never wrote.
    """
    head, slash, rest = label.partition("/")
    return _safe_name(head.upper() + slash + rest)


def _window_columns(pools: list[Pool]) -> list[str]:
    labels = {label for pool in pools for label in pool.cells}
    return sorted(labels, key=lambda label: (WINDOW_ORDER.get(label, len(WINDOW_ORDER)), label))


def _cell(record, now: datetime.datetime) -> str:
    """One window's cell as PLAIN text. Painting happens after the widths are known.

    A cell says what it is without a section heading above it to explain it. A window whose
    reset is already past renders its own `3d 02h ago`, which is exactly why the figure
    beside it is the PREVIOUS window -- and the reason those rows no longer need a block of
    their own, repeating a caveat the cell already carries.
    """
    if record is None:
        return ""
    if record.state == GAP:
        return f"[{record.diagnostic}]"
    # No reset time is not one condition. A pool the vendor calls inactive and a pool whose
    # `resetsAt` the vendor simply did not send both arrive here, and an empty cell would make
    # them the same. The note is what separates them, so it becomes the cell -- in its short
    # form, keyed on the exact strings the producers write, because the full sentence would set
    # this column's width for every row on the page.
    tail = RESET_LABELS.get(record.note, record.note) if record.resets is None \
        else _relative(record.resets, now)
    if record.percent is None:
        return tail or "-"
    used = f"{_figure(record.percent):>4}%"
    return f"{used}  {tail}" if tail else used


def _paint_cell(text: str, record, paint: Paint) -> str:
    """Hue by consumption; anything that is not the CURRENT window recedes.

    Dim is the whole of what used to be a separate section with a heading. A stale or inactive
    figure is a real measurement and is printed at full precision, but it is not comparable to
    the live cell beside it and must not read as though it were.

    The GAP test comes FIRST, and the order is the whole of it: every record `_row_or_gap`
    builds carries `percent is None`, so a missing-value test placed above this one answered for
    every gap on the page and dimmed the diagnostic that says a window went unread -- the one
    cell that must not recede.
    """
    if record is None:
        return text
    if record.state == GAP:
        return paint(text, RED)
    if record.percent is None:
        return paint(text, DIM) if text else text
    hue = _hue(record.percent)
    return paint(text, hue if record.kind() == "current" else DIM + ";" + hue)


def _sorted_pools(pools: list[Pool]) -> list[Pool]:
    """By candidate, then by allowance, alphabetically -- and TOTAL.

    A candidate's rows sit TOGETHER, in the same order on every run, so the report reads as a
    list of the accounts on this machine rather than as a ranking whose membership shifts with
    every window that rolls over. Ranking by consumption scattered one Codex home's pools down
    the page and put the same directory name in four places.

    Nothing about a row's POSITION means anything now, which is the point: consumption is read
    off the figure and its hue, and the cell that is not a current window says so in its own
    words. A rank cannot say that, and a rank over a mix of current and expired windows was
    ordering numbers that are not comparable in the first place.
    """
    return sorted(pools, key=lambda pool: (
        pool.where, pool.ident, pool.group, pool.family,
        "\u0000".join(sorted(pool.cells)), pool.freshness()))


def _pool_table(pools: list[Pool], now: datetime.datetime, paint: Paint) -> None:
    """The one table. Every row is an allowance; every window column is shared by all of them.

    Widths are measured across EVERY row at once and floored by the header labels. A column
    narrower than the word naming it printed `WHEREPOOL` with nothing between the labels, and
    per-block widths made the columns move down the page -- a table whose columns move is not
    a table.
    """
    if not pools:
        return
    labels = _window_columns(pools)
    heads = list(COLUMNS) + [_column_head(label) for label in labels]
    grid = [[_safe_name(pool.where), _safe_name(pool.family)]
            + [_cell(pool.cells.get(label), now) for label in labels] for pool in pools]
    # `_width`, not `len`, on the heading too: a window column is headed partly by a VENDOR
    # string, and a CJK display name counts one per code point but draws two -- the floor would
    # then be shorter than the label it exists to protect.
    widths = [max([_width(row[index]) for row in grid] + [_width(heads[index])]) + 2
              for index in range(len(heads))]

    print(paint("  " + "".join(_pad(head, width) for head, width in zip(heads, widths))
                + "SOURCE", DIM))
    print(paint("  " + "\u2500" * (sum(widths) + 6), DIM))
    previous = None
    for pool, cells in zip(pools, grid):
        # Compared on IDENTITY, never on the printed name: two candidates that happen to print
        # alike are still two accounts, and blanking the second would show them as one group.
        # A candidate's rows are adjacent (see _sorted_pools), so repeating its name down the
        # group is noise the eye has to filter -- the reason one home reading `.codex` three
        # times over looked like three unrelated accounts. Blanked, never abbreviated: the name
        # is either there or it is the row above's, which is what the indentation already says.
        head = "" if pool.ident == previous else cells[0]
        previous = pool.ident
        line = _pad(head, widths[0]) + _pad(cells[1], widths[1])
        for index, label in enumerate(labels, start=len(COLUMNS)):
            text = cells[index]
            # `_paint_cell` may return escapes; the padding is computed from the PLAIN text, or
            # a coloured table goes crooked exactly when colour is on.
            line += (_paint_cell(text, pool.cells.get(label), paint)
                     + " " * max(0, widths[index] - _width(text)))
        # A row whose every cell gapped has no provenance to print, and the padding that was
        # holding a column open for it becomes trailing whitespace on the page.
        source = _source(pool.freshness(), pool.group)
        line = line + paint(source, DIM) if source else line.rstrip()
        print("  " + line)
    print()


def _render(groups: list[tuple[str, str, str, list[Record]]], notes: list[str],
            now: datetime.datetime, paint: Paint) -> None:
    """The whole report: a voucher band, then one table -- one line per allowance."""
    buckets: dict[str, list[Row]] = {"pool": [], "voucher": [], "info": []}
    for group, ident, where, records in groups:
        for record in records:
            kind = record.kind()
            buckets[kind if kind in ("voucher", "info") else "pool"].append(
                Row(ident, where, group, record))

    print(f"\n{paint('code-limits', BOLD)}  {paint(_local(now), DIM)}\n")
    _voucher_band(buckets["voucher"], buckets["info"], now, paint)
    pools = _sorted_pools(_pivot(buckets["pool"]))
    _pool_table(pools, now, paint)

    if any(record.diagnostic == "stale-after-reset"
           for pool in pools for record in pool.cells.values()):
        # Printed only when such a cell is on the page. The cell already says `... ago`;
        # what it cannot say is why a report would show a window that is over, or how to get the
        # current one -- and a legend for a row nobody is looking at is just noise.
        notes = notes + ["a cell reading `... ago` is the PREVIOUS window -- the cache"
                         " predates its reset  [stale-after-reset]"]
    for note in notes:
        print(paint(f"  {note}", DIM))
    print(paint("  --live fetches current Claude numbers instead of the on-disk cache", DIM))
    print(paint("  reading Codex starts its app-server, which migrates that home's own state"
                " databases", DIM))
    print(paint("  exactly as any codex invocation does. Nothing here is ever redeemed.", DIM))


def _refreshed(live: tuple[str, list[Record], str], cached: tuple[str, list[Record], str]):
    """Which of the two reads answers this candidate, as one decision in one place.

    A row comes from ONE read, whole -- gaps included. Merging the two per window looked strictly
    better and was worse: with a live gap suppressed by a cached cell, nothing was left to gap the
    run, and the row rendered a CACHED figure under the live provenance its first cell carried. A
    stale number labelled `api`, no note, exit 0. So whatever the live read produced is the answer
    whenever it produced anything at all, and the cache answers only when it produced nothing.

    A function rather than three lines inline because the live half cannot be reached from a test
    without a socket, and a rule that no test can state is a rule that quietly changes. Both
    arguments are whole `(state, records, code)` triples, exactly as `_examine` returns them --
    six interleaved positionals could not be read at a call site without the signature open
    beside it.
    """
    return live if live[1] else cached


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
              or _discover(home, ".claude", (".claude.json", ".credentials.json"),
                           external=_claude_config))
    codex = ([_explicit(Path(p)) for p in args.codex_home]
             or _discover(home, ".codex", ("auth.json", "config.toml")))

    warnings: list[str] = []
    notes: list[str] = []
    groups: list[tuple[str, str, str, list[Record]]] = []

    # Only the cached reader takes the run's clock: it dates rows from a file that was
    # written before the run. The live readers time-stamp their own observation, because
    # a keychain prompt or a stalled app-server can put minutes between run start and the
    # answer they are describing.
    claude_producer = _claude_live if args.live else (lambda profile: _claude_cached(profile, now))
    for group, candidates, producer, refresh in (
        (CLAUDE_GROUP, claude, claude_producer, not args.live),
        (CODEX_GROUP, codex, _codex_records, False),
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
            if refresh and any(record.diagnostic == "stale-after-reset" for record in records):
                # The one case where reading the file again cannot help: its window is over, so
                # no percentage in it is about the present, and the report goes and asks instead.
                #
                # Logging in does NOT fix this, which is what makes it worth doing automatically.
                # The CLI rewrites `.claude.json` at login but refreshes `cachedUsageUtilization`
                # only after a request that carries usage back -- so a profile can be freshly
                # authenticated and still be describing a window three days gone, with nothing on
                # the page to suggest that signing in again was not the answer.
                #
                # A retry that fails keeps the cached rows exactly as they were. They are stale,
                # which their cells already say, and losing them to a failed network call would
                # be the worse trade. Its reason is a NOTE, deliberately not a warning: the
                # default mode promised to read a cache and it read one.
                live_state, live_records, live_code = _examine(candidate, _claude_live)
                if not live_records:
                    detail = live_code or "the backend returned nothing to read"
                    notes.append(f"{where}: the cache describes a window that is over, and the"
                                 f" live retry did not answer -- {detail}")
                state, records, code = _refreshed((live_state, live_records, live_code),
                                                  (state, records, code))
            if code:
                # A candidate-level outcome has no pool to hang a row on, so it becomes a note.
                # It still decides the exit status below, exactly as before.
                notes.append(f"{where} [{code}]")
            groups.append((group, str(candidate.path), where, records))
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
