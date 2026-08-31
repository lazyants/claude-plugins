#!/usr/bin/env python3
"""fetch_citation.py -- the ONLY sanctioned network retrieval in the citation
audit path (#347). Standard library only.

WHY THIS EXISTS, and why it is a script rather than a prompt rule.

Under `glossary.research_mode: live` the W3 glossary pass resolves some names
as `basis: "established"` with a `source` URI, and the pre-merge citation review
has to look at the cited page before those bytes are frozen into `canon.json`.
Until 1.16.1 the reviewing agent fetched that URL itself, with no validation of
scheme or address. A `source` is attacker-authorable in the only sense that
matters -- it is produced by an LLM reading source text that a hostile document
can seed -- so `http://169.254.169.254/latest/meta-data/`, `file:///etc/passwd`,
or `http://127.0.0.1:6379/` were all reachable from a "citation".

The first design for this fix told the reviewing agent to fetch only through
this script. That is not a boundary and was rejected in review: the reviewer is
an unrestricted agent that already holds Bash and already ingests page content,
so a hostile page can simply instruct it to curl something else. A rule the
attacker can talk the enforcer out of is not an enforcement point.

So retrieval was moved OUT of the judging agent entirely:

  prepare agent -- runs this script, ingests NO page content. It only reads the
                   one metadata line printed below, which is generated locally
                   and never contains retrieved bytes.
  judge agent   -- reads local files only. Every byte it judges arrived through
                   the checks in this file.

That is the exact claim this file is allowed to support, and no wider one. It
does NOT make the pipeline SSRF-free: the resolver/generation agent still does
open web research by design under `research_mode: live`. That exposure is
accepted by design and documented rather than quietly covered (#353). What #353
DID close is narrower than "the judge is safe": the judge is dispatched as the
plugin agent `literary-translator:citation-judge`, whose frontmatter grants
`tools: Read` and nothing else, so it no longer holds a tool that could fetch
anything. Its SCOPE rules -- read only the evidence files the index names --
are still prompt-level, and nothing here should be read as claiming otherwise.
Overclaiming here would be worse than the original bug, because the next reader
would stop looking.

THE CHECKS, and the failure each one closes.

1. Scheme allowlist (http/https). Closes `file:`, `ftp:`, `gopher:`, `data:`,
   `javascript:`. An allowlist, never a denylist -- the set of schemes a URL
   library will accept is open-ended and grows with the runtime.
2. No embedded credentials, no control characters. `user:pw@host` shifts which
   host is really contacted depending on who parses it; control characters
   enable request splitting.
3. Resolve, then check EVERY returned address. A name that resolves to both a
   public and a private address must be refused -- checking only the first
   address returned by getaddrinfo leaves a trivially winnable race, since the
   order is not stable.
4. Connect to the RESOLVED IP, not the name. Between "we resolved it and liked
   the answer" and "we connected" the name can resolve differently -- classic
   DNS rebinding. TLS still verifies against the ORIGINAL hostname via SNI, so
   pinning the address costs no certificate validation.
5. Redirects are followed MANUALLY, and every hop re-runs checks 1-4. A public
   URL that 302s to 169.254.169.254 is the standard bypass, and it defeats any
   design that validates only the URL it was handed.
6. Caps on time, bytes per item AND bytes per batch, and content type. A
   boundary that can be held open forever is a denial-of-service surface of
   its own.

OUTPUT CONTRACT (consumed by the prepare/judge split -- do not change casually).

  --batch <snapshot.json> --out-dir <dir>
      <snapshot.json> is a canon-batch snapshot: a TOP-LEVEL JSON ARRAY, the
      shape `canon-batch.schema.json` declares and the exact bytes
      `canon_validate.py --approve-to` publishes. (A `{"items": [...]}` wrapper
      is also accepted, for hand-built fixtures.)
      Iterates every item whose `source` is a NON-EMPTY STRING -- NOT only the
      ones with `basis: "established"`, and not literally every item that has the
      key: an empty or non-string `source` is skipped deliberately, because it is
      not a fetch target and its shape is Pass 1's business. canon_validate.py's
      offline half skips exactly the same items, and the two must agree on WHICH
      items they cover, not merely on the checks they run.
      The queued branch of canon-batch.schema.json types
      `source` as a bare unconstrained string, so a `disposition: "review_queue"`
      item can carry `basis: "established"` plus an arbitrary `source` and pass
      Pass 1 today. Covering the whole field is no harder than covering half.
      Writes one evidence file per ADMITTED url plus an index.json recording
      every item's outcome, and prints EXACTLY ONE line of JSON metadata.
      Never prints retrieved bytes: the agent that runs this must not be
      injectable by what it fetched.

  <URL>
      Single-URL mode, for tests and manual checking. Prints the metadata line,
      then a delimiter line, then the decoded body. Writes no files.

Outcomes recorded per item: "fetched", "refused:<reason>", "http_error:<code>".
"""
from __future__ import annotations

import argparse
import codecs
import contextlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin, quote

ALLOWED_SCHEMES = ("http", "https")

# The default port per scheme, in ONE place. Read by validate_url (to fill in an
# absent port) and by _port_suffix (to decide whether to write it out).
DEFAULT_PORTS = {"http": 80, "https": 443}

# Schemes worth NAMING in a refusal, so `outcome` still says which kind of unsafe
# URL was attempted. Everything outside this set collapses to "other" -- see
# scheme_token(). This is a diagnostic vocabulary, NOT a denylist: refusal is
# decided by ALLOWED_SCHEMES above and nothing here widens it.
KNOWN_SCHEMES = ("file", "ftp", "ftps", "data", "javascript", "gopher", "ws", "wss",
                 "mailto", "tel", "about", "blob", "chrome", "jar", "ldap", "dict",
                 "sftp", "smb", "nfs", "redis", "gemini")
MAX_REDIRECTS = 5
MAX_BYTES = 2_000_000
# One recv's worth of body per deadline check. Small enough that the check is
# frequent, large enough that a 2 MB page is not read in thousands of slices.
READ_CHUNK_BYTES = 65_536
# PER-ITEM budget, checked at the top of each redirect hop AND between body
# chunks (see _read_bounded). ONE gap is known and accepted rather than papered
# over: getaddrinfo() takes no timeout argument, so a pathological resolver can
# block past this. The body read used to be a second such gap -- bounded by
# MAX_BYTES and the socket's per-recv idle timeout, neither of which bounds
# ELAPSED time against a server that trickles. Round 5 closed it, because the
# consequence was not slowness: the prepare step is one bash call under a
# measured 600 s clamp, so a single held socket ran the whole batch out of time.
#
# An earlier version of this comment added "nothing downstream treats it as a
# latency guarantee". That was itself an overclaim, caught in review: downstream
# IS a single bash call under a measured hard 600 s clamp (see BATCH_TIMEOUT_SEC
# and the 1.16.1 CHANGELOG). Wall-clock is exactly what downstream cares about,
# which is why the batch-wide budget below exists.
TOTAL_TIMEOUT_SEC = 30.0

# BATCH-WIDE budget for --batch mode. Sized against the agent Bash tool's
# measured 600 s per-call clamp, the same constant #348 is about, with headroom
# for process start, JSON I/O and evidence writes. run_batch() must return a
# usable index.json and exit cleanly rather than being killed mid-write: a killed
# call is an EVIDENCE_FAILED that spends a retry, and exhausting the retry ladder
# merges zero batches.
BATCH_TIMEOUT_SEC = 420.0

# BATCH-WIDE ceiling on the total bytes WRITTEN as evidence, across every item
# in the batch -- and it is an ACTUAL ceiling: the total written in one batch
# is always STRICTLY LESS than this number. (An earlier version of this
# constant named a ceiling the guard did not enforce: the guard admitted an
# item whenever spent_bytes had not yet REACHED this same constant, so the
# admitted item's own write could land past it by up to 3 * MAX_BYTES. A
# reviewer read that gap as the name promising something the code did not
# keep, and was right -- see "HOW THE CEILING HOLDS EXACTLY" below for the fix.)
#
# It is a FIXED CATASTROPHE LIMIT and carries NO admissibility guarantee --
# that sentence is the whole derivation and it is deliberate. `--batch-size`
# has no ceiling, and a partner closure (see glossary_batch_plan.py's
# build_partner_adjacency/closure) is appended to a batch WHOLE once its
# length check has already passed, so the number of sources in one batch is
# unbounded by construction. No fixed constant can therefore promise "never
# refuses a legitimate batch" -- only two designs could, and both are worse:
# a budget that SCALES with the snapshot's own item count stops being a bound
# at all (a hostile 400-item snapshot buys itself 400 MB, and the snapshot's
# length is downstream of LLM output over uncontrolled source text); no
# budget is the defect this constant exists to close.
#
# HOW THE CEILING HOLDS EXACTLY: run_batch does not admit an item once
# spent_bytes reaches BATCH_MAX_TOTAL_BYTES - 3 * MAX_BYTES (the ADMIT
# THRESHOLD -- derived where it is used, not a second module constant, so a
# test that monkeypatches this constant alone still exercises the real
# relationship). The last item admitted therefore starts BELOW that
# threshold and can write at most 3 * MAX_BYTES (one item's worst case: a
# body that is entirely undecodable, which the decode/re-encode round trip
# triples -- see the write site's own comment. Since #801 the codec is the
# response's DECLARED charset rather than always UTF-8, and 3x is still the
# ceiling for every member of ALLOWED_CHARSETS -- proved by codec family in
# that constant's own comment). So the running total after
# it is strictly below (BATCH_MAX_TOTAL_BYTES - 3 * MAX_BYTES) +
# 3 * MAX_BYTES == BATCH_MAX_TOTAL_BYTES: the one-item overshoot now lands ON
# the ceiling instead of past it. The threshold is deliberately NOT
# "spent + MAX_BYTES > ceiling" checked per item: that phrasing refuses items
# that would in fact have fit whenever the CURRENT item is not the worst
# case, and a false refusal is the expensive error here (it costs a retry and
# can exhaust the ladder -- see below) while the overshoot it would be
# guarding against is already impossible once the threshold is set
# 3 * MAX_BYTES below the ceiling.
#
# Sized generously against the NOMINAL batch instead of against a maximum
# that does not exist: 1 MB of written evidence per source across
# glossary_batch_plan.DEFAULT_BATCH_SIZE = 40 (half MAX_BYTES, comfortably
# above a typical reference page) sets the ADMIT THRESHOLD at 40 MB, so the
# ceiling is threshold + 3 * MAX_BYTES = 46 MB. A batch larger than the
# threshold WILL have its tail refused, and that is accepted rather than
# designed away.
#
# And what the soft-fail buys there is FAIL-SAFE REPORTING, not recovery.
# run_batch still writes index.json and exits 0, the refused item is recorded,
# and the judge is told by name that the refusal is a fact about this run --
# but nothing SHRINKS the batch afterwards. Every retry calls
# batchDispatchPrompt(batch, attempt, rejectionReason) with the SAME `batch`
# object and serializes the same batch.candidates, so a refusal can reproduce
# through all MAX_CITATION_RETRIES + 1 attempts and return
# citation-review-exhausted, and because the merge is all-or-nothing, one
# exhausted batch stops the whole pass. That is the exact cost
# refused:batch-deadline has carried since 1.16.1 against the same unbounded
# batch size -- this constant adds a second way to reach it, and this comment
# says so rather than calling the soft-fail a save.
BATCH_MAX_TOTAL_BYTES = 46_000_000

CONNECT_TIMEOUT_SEC = 10.0
EVIDENCE_PREFIX = "citation-"

# Text-ish only. A citation is a document a human could have read; anything
# else is either useless to the judge or an attempt to make it ingest something
# it cannot evaluate.
#
# This is the DEFAULT, overridable per project with --allow-content-type (from
# profile.yml's glossary.citation_content_types). A corpus whose sources are
# archive scans is the motivating case: `application/pdf` is a document a human
# could have read, but admitting it for everyone would widen the boundary for
# projects that never cite one. Widening is therefore an explicit, per-project
# act, and the closed-set property below holds over whatever list is in force --
# see content_type_token().
ALLOWED_CONTENT_PREFIXES = ("text/", "application/xhtml", "application/xml", "application/json")

# A configured prefix is copied verbatim into index.json as a token, so it is
# constrained to the RFC 9110 type/subtype charset rather than trusted because
# it arrived on a command line. No parameters (`; charset=...`), no wildcards,
# no whitespace: this is a prefix for str.startswith, not a media-range.
#
# \A and \Z, never ^ and $: Python's `$` also matches immediately BEFORE a
# trailing newline, so `^...$` would admit "text/html\n" -- which then lands in
# index.json and breaks the one-token-per-field shape the judge reads. Measured,
# not assumed: re.match(r"^[a-z/]*$", "text/html\n") returns a match.
CONTENT_TYPE_PREFIX_RE = re.compile(r"\A[a-z0-9][a-z0-9.+-]*/[a-z0-9.+-]*\Z")
MAX_CONTENT_TYPE_PREFIXES = 16

# The codec a retrieved body is decoded with, taken from the response's own
# `Content-Type: ...; charset=...` (#801). Before this, the decode was
# unconditionally UTF-8, so EVERY byte of a page served in a legacy encoding
# became U+FFFD -- measured on a live French->Russian volume, 14 of 306
# retrieved bodies, the worst 76% destroyed. Nothing downstream noticed: the
# outcome is "fetched", a body is on disk, and the judge then fails the item for
# a reason that is not the citation's fault.
#
# CLOSED SET, for the same reason content_type_token() reports a closed token:
# the header is attacker-authorable, and `codecs.lookup` would otherwise reach
# Python TEXT codecs that are not charsets at all. `unicode_escape` is the sharp
# one -- it decodes b"\\ud800" to a LONE SURROGATE, which then reaches
# result["body"].encode("utf-8") in run_batch, OUTSIDE the per-item except
# guard, and destroys the whole batch's index.json. That is the exact failure
# _encodable() exists to prevent, and its docstring's standing claim (retrieved
# bodies "pass through decode(errors=replace), which can never emit a
# surrogate") is what an open lookup would silently falsify. `undefined` raises
# on every input; `idna`/`punycode` raise on ordinary bytes.
#
# Membership rule: the legacy charsets reference and library sites actually
# serve for the scripts this plugin translates. `us-ascii` is deliberately NOT a
# member -- honouring it would MANGLE a page that under-declares, while falling
# back to UTF-8 (a strict superset) is never worse.
#
# THE 3x BOUND run_batch's byte budget rests on survives, by codec family
# rather than by enumeration: every member is either a charmap codec (one source
# byte -> at most one BMP character -> at most 3 UTF-8 bytes) or a multi-byte
# codec (several source bytes collapse into ONE character, which expands
# strictly less), and errors="replace" emits U+FFFD, itself 3 bytes, per
# undecodable unit. So one item still writes at most 3 * MAX_BYTES -- see
# BATCH_MAX_TOTAL_BYTES's own comment for what that buys.
#
# Every member is spelled as the CANONICAL codecs.lookup().name, because that is
# what the membership test below compares against; a test asserts the identity
# rather than leaving it to be noticed when a member silently never matches.
ALLOWED_CHARSETS = frozenset({
    "utf-8", "utf-8-sig",
    "iso8859-1", "iso8859-2", "iso8859-5", "iso8859-7", "iso8859-8",
    "iso8859-9", "iso8859-15",
    "cp1250", "cp1251", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256",
    "cp1257", "cp1258",
    "koi8-r", "koi8-u", "cp866",
    "shift_jis", "euc_jp", "iso2022_jp", "euc_kr",
    "gb2312", "gbk", "gb18030", "big5", "big5hkscs", "tis-620",
    "utf-16", "utf-16-be", "utf-16-le",
})
DEFAULT_CHARSET = "utf-8"

# The label is shape-checked BEFORE it reaches codecs.lookup, whose `encodings`
# search function imports a module named after the normalized label. The lookup
# is used only for its ALIAS table (windows-1251 -> cp1251, csKOI8R -> koi8-r);
# the security decision is the ALLOWED_CHARSETS membership test on the canonical
# name it returns, so the lookup's breadth buys convenience and grants nothing.
# Lowercase only, because the sole caller lowercases before matching -- an
# `A-Z` half here would be dead, and a dead half of a security gate reads as
# coverage it does not have. A caller that ever forgot to lowercase would fail
# CLOSED, which is the right direction for this gate.
CHARSET_LABEL_RE = re.compile(r"\A[a-z0-9._:+-]{1,64}\Z")

# Control characters anywhere in the URL. Also catches the raw CR/LF that make
# header injection possible.
CONTROL_CHAR_RE = re.compile(r"[\x00-\x20\x7f]")

DELIMITER = "----- LT_CITATION_BODY -----"


class Refused(Exception):
    """A URL the boundary declines to retrieve. The message is the machine
    reason recorded in index.json, so keep it short, stable and free of
    attacker-supplied text."""


def _refuse(reason: str) -> "Refused":
    return Refused(reason)


def name_for_comparison(host: str) -> str:
    """Fold a host to the ASCII form a RESOLVER will actually use, for NAME
    comparisons only. Kept BODY-identical to canon_validate.py's copy (the
    definitions differ by a leading underscore, and nothing tests source
    identity -- the enforced parity is behavioural, via the shared URL table).

    Comparing the raw string is not enough, and the trailing-dot fix alone was
    not enough either. `encodings.idna` splits labels on the literal set
    `[.。．｡]`, so U+3002, U+FF0E and U+FF61 are all label separators, and UTS-46
    folding maps decorated letters home: measured on CPython 3.14.6,
    "localhost。", "localhost．", "localhost｡" and "ⓛocalhost" all encode to
    b"localhost." or b"localhost" and all resolve to 127.0.0.1/::1. That is
    stdlib behaviour, not platform-specific.

    In THIS file that would only be a static false-negative, since resolve_and_pin
    re-checks every returned address. In canon_validate.py, which runs the same
    decision with no resolver behind it, it is the whole check -- the same
    argument that justified stripping one ASCII dot, applied to the spellings
    that argument missed. A non-ASCII URL is not far-fetched in a corpus whose
    source language is not English.

    Used for COMPARISON ONLY: the returned value is never what gets resolved,
    sent as Host: or used for SNI. On any host the codec rejects (an over-long
    label, for instance) the original is returned, so this can only ever add
    refusals, never admit something the raw comparison would have caught.
    """
    try:
        folded = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError):
        folded = host.lower()
    # rstrip, not a single [:-1]: several codepoints fold to MORE than one
    # dot (U+2025 "..", U+2026 "...", U+FE30 "...."), so stripping exactly one
    # left "localhost." / "localhost..", which matched neither the equality
    # test nor the ".localhost" suffix test -- the same one-dot reasoning
    # this function exists to generalise, stopping one dot short.
    return folded.rstrip(".")


def origin_of(scheme: str, host: str, port: int) -> str:
    """scheme://host[:port] -- the ONLY shape of a URL that may be recorded in
    index.json.

    Both components are already constrained by the time this is called: `scheme`
    passed the ALLOWED_SCHEMES allowlist, and `host` came through urlsplit and
    the address/name checks. Path, query and fragment are dropped entirely
    rather than escaped, because escaping preserves readable prose (percent-
    encoded English is still English to a reader) and the judge is a reader.
    Diagnostic value kept: which host a hop went to, and how many hops. Lost: the
    exact path, which is recoverable from the run's own logs if ever needed.
    """
    return f"{scheme}://{authority(scheme, host, port)}"


def bracket_host(host: str) -> str:
    """Put back the brackets urlsplit().hostname strips off an IPv6 literal.

    A registered name can never contain a colon (validate_url takes `hostname`,
    which has already had any `:port` and `user:pass@` split away), so a colon
    here means an address literal and nothing else.
    """
    return f"[{host}]" if ":" in host else host


def _port_suffix(scheme: str, port: int) -> str:
    """":port", or "" when it is the scheme default.

    The ONE piece the wire authority and the recorded origin must agree on, and
    the piece they had actually drifted on before 1.16.1 round 3: the header sent
    a bare hostname for every URL, so an admitted `https://host:8443/` asked the
    server for the wrong virtual host, and an IPv6 literal produced a
    syntactically invalid header (RFC 9110 requires `Host: [::1]:8443`).
    """
    # DEFAULT_PORTS, not an inline conditional: this function and validate_url
    # spelled the same two-entry table with OPPOSITE polarity ("80 if http else
    # 443" here, "443 if https else 80" there). Both are correct for http/https
    # and they disagree for anything else, which is the shape a drift takes when
    # the allowlist is ever widened. A missing scheme yields None, which no port
    # equals, so the port is written out rather than silently dropped.
    return "" if port == DEFAULT_PORTS.get(scheme) else f":{port}"


def wire_authority(scheme: str, host: str, port: int) -> str:
    """The `Host:` header value: an ASCII A-LABEL authority.

    Round 3 merged this with origin_of()'s authority because they had drifted on
    the port/bracket rule. Round 4 splits the HOST FORM back out, because the two
    consumers genuinely need different things and merging them papered over a
    second bug: http.client encodes the request as ASCII, so a Unicode hostname
    is either mis-sent or fatal. Measured -- `éxample.com` went out as raw
    Latin-1 where its A-label is `xn--xample-9ua.com`, and `例え.テスト` raised
    UnicodeEncodeError. The shared part (_port_suffix) still cannot drift.

    An unencodable host is REFUSED rather than sent mangled: a host the IDNA
    codec rejects is not a host this boundary can honestly claim it vetted.
    """
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        raise _refuse("host-not-idna-encodable")
    return bracket_host(ascii_host) + _port_suffix(scheme, port)


def authority(scheme: str, host: str, port: int) -> str:
    """host[:port] in DISPLAY form, for the origin recorded in index.json.

    Deliberately NOT the A-label: this value is read by a human operator
    diagnosing a citation, and `例え.テスト` is more useful to them than
    `xn--r8jz45g.xn--zckzah`. It is never sent on the wire -- see wire_authority.
    """
    return bracket_host(host) + _port_suffix(scheme, port)


def parse_content_type_prefixes(values) -> tuple:
    """Validate an operator-supplied Content-Type allowlist, or fall back to the
    shipped default when none was given.

    Exits rather than returning a diagnostic: a project that asked for a wider
    boundary and got the default silently is exactly the failure this release
    spent an hour of gate time on elsewhere -- a hardcoded value diverging from
    the profile that was supposed to set it. Fail loudly at preflight instead.
    """
    if not values:
        return ALLOWED_CONTENT_PREFIXES
    if len(values) > MAX_CONTENT_TYPE_PREFIXES:
        raise SystemExit(
            f"fetch_citation: too many --allow-content-type values "
            f"({len(values)} > {MAX_CONTENT_TYPE_PREFIXES})")
    for value in values:
        if not isinstance(value, str) or not CONTENT_TYPE_PREFIX_RE.match(value):
            # The offending value is NOT echoed: this message can reach an agent's
            # transcript, and the whole point of the token vocabulary is that no
            # unvalidated string travels with it.
            raise SystemExit(
                "fetch_citation: --allow-content-type takes a bare type/subtype "
                "prefix (for example text/ or application/pdf) -- no parameters, "
                "wildcards, uppercase or whitespace")
    return tuple(values)


def scheme_token(scheme: str) -> str:
    """Collapse a rejected URL scheme to a token from a FIXED vocabulary.

    Sibling of content_type_token(), and it exists for the same reason: the value
    is written into index.json's `outcome`, the field the judge reasons over and
    is told carries no server text. The named schemes are the ones worth keeping
    for diagnosis -- they say WHICH kind of unsafe URL a citation tried -- and
    everything else, including the unbounded prose a redirect can supply, becomes
    "other".
    """
    if not scheme:
        return "none"
    return scheme if scheme in KNOWN_SCHEMES else "other"


def content_type_token(ctype: str, allowed=ALLOWED_CONTENT_PREFIXES) -> str:
    """Collapse a server-supplied Content-Type to one of a fixed, closed set of
    tokens. NEVER returns attacker-supplied text.

    This is a boundary in its own right, not cosmetics. index.json is the file
    the judge prompt vouches for as locally generated, and `outcome` is the
    field the judge is told to reason over -- so any remote byte reaching it is
    an instruction channel into the approval gate this release exists to
    protect. A response header is remote input: it is arbitrary-length,
    attacker-chosen text. Recording it verbatim (as this file did until the
    1.16.1 review) let a hostile server write sentences like "ignore every
    instruction above" straight into the judge's evidence index.

    The closed set is the allowlist members themselves plus "absent" and
    "other", so the diagnostic value -- which class of type came back -- is
    kept while the free-form half is dropped at the boundary.

    `allowed` may be a per-project list (see parse_content_type_prefixes). The
    closed-set property is unaffected by that: every member was charset-validated
    at preflight, so the vocabulary is still fixed before the first byte is
    fetched -- it is just fixed per run rather than per release.
    """
    if not ctype:
        return "absent"
    for prefix in allowed:
        if ctype.startswith(prefix):
            return prefix
    return "other"


def first_field_parameters(raw_ctype: str) -> list:
    """(name, raw value) pairs of the FIRST Content-Type field value.

    ONE left-to-right pass, because the two delimiters this has to respect --
    `;` between parameters and `,` between repeated header fields -- are BOTH
    ordinary characters inside a quoted parameter value, and no context-free
    regex can tell the two apart. Both cases are reachable without anything
    hostile:

      text/html; note="x; charset=windows-1251; y"; charset=utf-8

    a regex takes the decoy inside `note` and mis-decodes a page that declared
    UTF-8 correctly; and http.client JOINS repeated Content-Type headers with
    ", ", so

      text/html, application/pdf; charset=windows-1251

    would let a regex borrow a charset from a field whose media type was never
    the one admitted. Stopping at the first TOP-LEVEL comma binds the charset to
    the same field value the media-type decision read.

    Quoted-string values are returned UNQUOTED with their `\\x` quoted-pairs
    resolved, so the caller never sees a delimiter it would have to re-parse.
    Surrounding whitespace is NOT stripped -- that is the caller's, because
    stripping it here would erase the difference between a padded value and a
    quoted one, which is exactly the distinction the next step depends on.

    An UNTERMINATED quote (or a trailing backslash) makes the whole field
    malformed, and this returns NO parameters at all rather than a value it had
    to guess the end of -- fail-closed, in the one place where guessing would
    put a server-chosen codec on a body.
    """
    params = []
    name = []
    value = []
    in_value = False
    in_quotes = False
    escaped = False
    for ch in raw_ctype:
        if escaped:
            value.append(ch)
            escaped = False
            continue
        if in_quotes:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = False
            else:
                value.append(ch)
            continue
        if ch == '"' and in_value:
            in_quotes = True
            continue
        if ch == ",":
            break
        if ch == ";":
            params.append(("".join(name), "".join(value)))
            name, value, in_value = [], [], False
            continue
        if ch == "=" and not in_value:
            in_value = True
            continue
        (value if in_value else name).append(ch)
    # `in_quotes` alone, and it is not an oversight: `escaped` is set only
    # inside the quoted branch and nothing clears `in_quotes` while it is set,
    # so a trailing backslash reaches here as an OPEN QUOTE rather than as a
    # second condition. Brute-forced over every string up to length 6 in
    # `" ; \ , = a x` -- zero cases where `escaped` is true and `in_quotes` is
    # not. Testing both would read as belt-and-braces while one half never
    # fires, which is worse than the shorter honest condition.
    if in_quotes:
        return []
    params.append(("".join(name), "".join(value)))
    # The FIRST entry is the media type itself (it precedes the first `;` and so
    # has no `=`), never a parameter. Dropping it here rather than at the call
    # site keeps "what this function returns" one sentence long.
    return params[1:]


def body_charset(raw_ctype: str) -> str:
    """The codec to decode a retrieved body with, from the response's own
    declared charset. Always returns a member of ALLOWED_CHARSETS.

    No `allowed=` seam, deliberately, though `content_type_token()` next door
    has one: that sibling's parameter exists because
    `parse_content_type_prefixes` really does hand it a per-project tuple. This
    set is closed by release, and a parameter nothing passes would advertise a
    configurability the boundary refuses.

    Every step fails CLOSED to UTF-8, which is what this function did
    unconditionally before #801 -- so an unparseable, unknown or non-charset
    declaration is exactly as good (and exactly as bad) as it was, and only a
    declaration that survives the whole ladder changes any behaviour.
    """
    for name, raw_value in first_field_parameters(raw_ctype):
        if name.strip().lower() != "charset":
            continue
        # The value arrives already unquoted; what is left is the OWS RFC 9110's
        # grammar permits around it. `charset="windows-1251" ; boundary=x` is a
        # VALID header, and an implementation that unquoted before trimming
        # would not see the closing quote there, would leave the quotes in the
        # label, and would fall back to UTF-8 -- silently re-creating the very
        # defect this function exists to fix. Caught in plan review rather than
        # in production, which is the only reason it is a comment and not an
        # incident.
        label = raw_value.strip().lower()
        if not CHARSET_LABEL_RE.match(label):
            return DEFAULT_CHARSET
        try:
            canonical = codecs.lookup(label).name
        except LookupError:
            return DEFAULT_CHARSET
        return canonical if canonical in ALLOWED_CHARSETS else DEFAULT_CHARSET
    return DEFAULT_CHARSET


# A host whose every label is a bare integer or an 0x-hex integer, and which is
# NOT already a canonical IP literal. getaddrinfo accepts these as addresses --
# measured: 2130706433, 0x7f.0x0.0x0.0x1, 017700000001 and 127.1 all resolve to
# 127.0.0.1 -- while ipaddress.ip_address() rejects every one of them, so the
# literal check upstream simply does not see them. In fetch_citation.py that was
# only a static miss (resolve_and_pin still refuses the loopback address it comes
# back with); in canon_validate.py, which has no resolver, it was the WHOLE
# check, and a `source` naming loopback in one of these spellings was frozen into
# canon.json.
#
# Refused outright rather than normalised, because normalising means picking a
# platform: 0177.0.0.1 resolves to 177.0.0.1 under getaddrinfo on BSD (measured
# here; inet_aton is the one API that does NOT diverge, returning 127.0.0.1 on
# both) and to 127.0.0.1 under glibc, so the SAME fragment gets different
# verdicts on macOS and Linux. A citation never legitimately cites a decimal,
# octal or hex-spelled address, and a real DNS name cannot have an all-numeric
# final label, so refusing costs nothing real. Verified against example.com,
# 1.example.com, 0x.com and archive.org, all still admitted.
_NUMERIC_LABEL_RE = re.compile(r"\A(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")


def _is_ambiguous_numeric_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return False              # a canonical literal; the address checks own it
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    return bool(labels) and all(_NUMERIC_LABEL_RE.match(l) for l in labels)


def check_address_literal(host: str) -> None:
    """Reject a host that is ALREADY an IP literal in a non-global range.

    Separate from the getaddrinfo pass below because a literal never goes
    through name resolution at all, so a resolution-time check would simply not
    run for `http://127.0.0.1/`.
    """
    try:
        # No .strip("[]"): urlsplit().hostname has ALREADY removed the brackets
        # (measured: urlsplit("http://[::1]/x").hostname == "::1"), which is the
        # invariant bracket_host() exists to undo. The old strip was a no-op on
        # the only live path AND a character-set strip rather than a pair strip
        # ("]::1[".strip("[]") == "::1"), so it encoded the opposite assumption
        # to its sibling one function away.
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # not a literal; the resolution pass covers it
    _assert_global(ip)


def _assert_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Every disqualifying property named explicitly rather than relying on
    `is_global` alone.

    `is_global` is the right primary test, but it has moved across Python
    versions (notably for 0.0.0.0/8 and some IPv6 ranges) and this file must
    behave identically on whatever interpreter an operator happens to have.
    Naming each property makes the intent auditable and version-stable; the
    checks overlap deliberately.
    """
    if ip.is_loopback:
        raise _refuse("loopback-address")
    if ip.is_link_local:
        raise _refuse("link-local-address")      # includes 169.254.169.254
    # fec0::/10, IPv6 site-local. getattr because IPv4Address has no such
    # property. Deprecated by RFC 3879, still routed on legacy networks -- and
    # it is NOT covered by any check around it: CPython leaves fec0::/10 out of
    # ipaddress._private_networks, so is_private is False and is_global is
    # consequently True. It was the one disqualifying property this function's
    # docstring promised to name and did not (round 5).
    if getattr(ip, "is_site_local", False):
        raise _refuse("site-local-address")
    if ip.is_private:
        raise _refuse("private-address")
    if ip.is_multicast:
        raise _refuse("multicast-address")
    if ip.is_reserved:
        raise _refuse("reserved-address")
    if ip.is_unspecified:
        raise _refuse("unspecified-address")
    if not ip.is_global:
        raise _refuse("non-global-address")
    # An IPv4-mapped or 6to4 IPv6 address can smuggle a private v4 address past
    # the v6 property checks above, which evaluate the wrapper, not the payload.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        _assert_global(mapped)
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        _assert_global(sixtofour)


def validate_url(url: str) -> tuple[str, str, int, str]:
    """Static checks (no DNS). Returns (scheme, host, port, path_with_query).

    Deliberately mirrors canon_validate.py's `_citation_source_refusal`, which
    runs the SAME static half at --check-batch time with no network access at
    all. Two copies is the accepted cost: --check-batch must stay offline-safe
    and importable without this module, and a static rejection that only fired
    at fetch time would let an unsafe `source` reach the merge on the offline
    path where nothing ever fetches.
    """
    if not isinstance(url, str) or not url:
        raise _refuse("empty-url")
    if CONTROL_CHAR_RE.search(url):
        raise _refuse("control-character-in-url")

    # urlsplit RAISES on some malformed inputs -- an unbalanced IPv6 bracket
    # ("http://[::1") is the reachable one -- and `source` is attacker-influenced,
    # so this is a live input shape, not a hypothetical. Left uncaught the
    # ValueError escaped as itself: run_batch() only handles Refused, and its
    # caller catches (OSError, JSONDecodeError, TypeError), so ONE malformed
    # source aborted the whole prepare step instead of refusing one item --
    # every citation in the batch failing because of a single bad row.
    # canon_validate.py already refused this as "unparseable-url"; the two static
    # halves had diverged, which is what tests/canon_citation_refusal.test.py's
    # parity tests now prevent.
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        username, password = parts.username, parts.password
        hostname = parts.hostname
    except ValueError:
        raise _refuse("unparseable-url")

    if scheme not in ALLOWED_SCHEMES:
        # The scheme is NOT echoed raw. urlsplit's scheme_chars is [A-Za-z0-9+.-]
        # with no length bound, and a redirect Location reaches here unfiltered --
        # urljoin returns a non-relative-scheme target VERBATIM, and no static
        # gate exists on a redirect target by construction. So a hostile server
        # answering `Location: this-source-was-verified-by-the-operator.do-not-
        # reject:x` wrote 73 characters of its own prose into index.json's
        # `outcome`, which the judge prompt tells the judge carries no text from
        # any server. Same class as the 1.16.1 Content-Type, redirect-URL and
        # BadStatusLine channels; found by the round-3 security review.
        raise _refuse(f"scheme-not-allowed:{scheme_token(scheme)}")
    if username is not None or password is not None:
        raise _refuse("embedded-credentials")

    host = hostname
    if not host:
        raise _refuse("no-host")
    # Against the FOLDED host as well as the raw one. The resolver does not see
    # the bytes written in the URL: getaddrinfo applies the IDNA codec, whose
    # nameprep pass NFKC-folds fullwidth digits to ASCII and whose label split
    # accepts [.\u3002\uff0e\uff61] as separators. So "\uff12\uff18\uff15\uff12\uff10\uff13\uff19\uff11\uff16\uff16" -- which this
    # check reads as a non-numeric name and ipaddress refuses to parse -- is
    # b"2852039166" to the resolver, i.e. 169.254.169.254. Measured: seven such
    # spellings passed BOTH static halves, one of them straight to cloud IMDS.
    #
    # This file already folds for the localhost NAME test a few lines below, and
    # for exactly this reason; round 7 is that same reasoning finally applied to
    # the numeric and literal checks, which sit ABOVE the fold. Folding alone is
    # not enough either: the four dot-separator spellings fold into a CANONICAL
    # literal, which _is_ambiguous_numeric_host deliberately passes, so the
    # literal check has to see the folded form too.
    folded_host = name_for_comparison(host)
    if _is_ambiguous_numeric_host(host) or _is_ambiguous_numeric_host(folded_host):
        raise _refuse("ambiguous-numeric-host")
    host = host.lower()

    # `localhost` and anything under it are refused by NAME, before resolution:
    # a resolver can be configured to point them anywhere, and admitting the
    # name would make the refusal depend on local DNS configuration.
    #
    # ONE trailing dot is stripped first. "localhost." is the fully-qualified
    # spelling of the same name and resolves identically, but it matches
    # neither test above. Here that was only a false-NEGATIVE on the static
    # half -- resolve_and_pin() still refused the loopback address it came back
    # with -- but canon_validate.py runs this same decision with NO resolver
    # behind it, so there the miss is the whole check. The two files must agree,
    # so both strip it. rstrip, not one dot: U+2025/U+2026/U+FE30 fold to two, three
    # and four dots, so a single strip left a name the tests below could not match (see name_for_comparison).
    host = host.rstrip(".")
    name = name_for_comparison(host)
    if name == "localhost" or name.endswith(".localhost"):
        raise _refuse("localhost-name")

    check_address_literal(host)
    if folded_host != host:
        check_address_literal(folded_host)

    try:
        port = parts.port
    except ValueError:
        raise _refuse("invalid-port")
    if port is None:
        # scheme is guaranteed to be in ALLOWED_SCHEMES by the check above.
        port = DEFAULT_PORTS[scheme]
    if not (0 < port < 65536):
        raise _refuse("invalid-port")

    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return scheme, host, port, path


def resolve_and_pin(host: str, port: int) -> str:
    """Resolve, require EVERY answer to be global, and return one address to
    pin the connection to.

    Returning an address the caller then connects to -- rather than letting the
    socket layer resolve the name a second time -- is the whole TOCTOU defence.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _refuse(f"dns-failure:{exc.errno}")
    if not infos:
        raise _refuse("dns-empty")

    addrs = []
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise _refuse("unparseable-resolved-address")
        _assert_global(ip)      # EVERY address, not just the one we will use
        addrs.append(addr)
    # Any address is safe to use: the loop above proved EVERY one is global, so
    # there is no "good" answer to prefer and no partial-private case left.
    return addrs[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a PINNED IP, with TLS still verified against the real hostname.

    Without the SNI/`server_hostname` split, pinning the address would silently
    downgrade certificate validation to "does this cert match an IP" -- trading
    an SSRF hole for a MITM hole. This keeps both closed.
    """

    def __init__(self, host: str, pinned_ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip
        # Our OWN reference to the context. HTTPSConnection stores one as the
        # private `self._context`, and reaching for that would make TLS
        # verification here depend on a stdlib implementation detail that has
        # no compatibility promise -- if it were ever renamed, this would fail
        # at connect() time in the one code path whose whole job is to keep
        # certificate checking intact.
        self._ssl_context = context

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        # server_hostname is the ORIGINAL host, never the pinned IP: the socket
        # goes to the vetted address while the certificate is still verified
        # against the name. Dropping this would trade an SSRF hole for a MITM
        # hole.
        self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP to a pinned IP. The Host header still carries the real name,
    so virtual hosting keeps working."""

    def __init__(self, host: str, pinned_ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)


def fetch_one(url: str, *, deadline: float, allowed_types=ALLOWED_CONTENT_PREFIXES) -> dict:
    """Fetch a single URL through the full boundary, following redirects
    manually and revalidating EVERY hop.

    Returns a metadata dict with the decoded body under "body". Raises Refused
    for anything the boundary declines.
    """
    chain = []
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        try:
            return _fetch_hop(url, current, chain, hop, deadline, allowed_types)
        except _Redirect as redirect:
            current = redirect.target
    # UNREACHABLE BY CONSTRUCTION, and kept deliberately. _fetch_hop_inner
    # refuses with this same reason at `hop >= MAX_REDIRECTS` before it can
    # raise _Redirect, so the final iteration never loops round and the
    # function always leaves from inside the loop. Deleting this line would
    # make the function fall off the end and return None the moment that
    # invariant is disturbed -- a silent success carrying no body -- so it
    # stays as the structural backstop rather than as live code.
    raise _refuse("too-many-redirects")


class _Redirect(Exception):
    """Internal control flow: one hop resolved to another URL."""

    def __init__(self, target: str):
        super().__init__(target)
        self.target = target


def _encodable(value):
    """Make a fragment-copied string safe to WRITE, not merely safe to read.

    The totality guard around each item stops at the last `except`. The
    index.json write happens after it, so a value that survives every refusal
    and then cannot be encoded destroys the whole batch's index -- the exact
    outcome the guard exists to prevent, arriving one step past its reach.

    json.loads accepts "\\ud800" and hands back a lone surrogate, which UTF-8
    cannot encode, so ONE such `source` in an otherwise valid approved fragment
    raised UnicodeEncodeError out of Path.write_text and left no index at all.
    Note this is a FRAGMENT channel, not a wire one: retrieved bodies already
    pass through decode(errors="replace"), which can never emit a surrogate --
    through ALLOWED_CHARSETS since #801, none of whose members can either.

    Non-strings pass through untouched -- the schema constrains those, and
    silently stringifying them here would hide a shape bug rather than fix one.
    """
    if not isinstance(value, str):
        return value
    return value.encode("utf-8", "replace").decode("utf-8")


# Refusals that a SLOW resolver and a genuinely unresolvable host produce
# identically, so past the deadline they are ours rather than the citation's.
# Deliberately closed and deliberately small: every other refusal -- above all
# the address and scheme ones -- keeps its own name no matter how late it fires.
# `unparseable-resolved-address` is deliberately NOT a member, though the first
# version of this set included it: the resolver ANSWERED, with a sockaddr that
# ipaddress refused to parse. Timing has nothing to do with it, so renaming it
# past the deadline would erase the only signal that the resolver returned a
# non-address -- a member that did not satisfy the set's own stated rule.
_RESOLUTION_TIMING_REASONS = frozenset({
    "dns-failure", "dns-empty",
})


def _past_deadline(deadline: float) -> bool:
    """True when OUR deadline has passed, so the refusal is ours to name.

    The watchdog interrupts a blocked call by shutting the socket down, and what
    the stdlib then raises depends on the scheme. Over plain HTTP the read
    returns EOF and _read_bounded's own clock check names it. Over HTTPS,
    ssl.SSLSocket.shutdown() clears _sslobj before the base shutdown, so
    OpenSSL's alert write hits EPIPE and the caller sees BrokenPipeError --
    measured: 3 of 4 trickle phases came back as network-error:BrokenPipeError,
    i.e. our own watchdog reported as the remote host misbehaving.

    That mattered beyond tidiness. The judge prompt names facts about THIS RUN
    rather than about the citation -- batch-deadline, read-timeout,
    total-timeout and batch-byte-budget -- so a mislabelled timeout is read as
    a citation defect, and citations are overwhelmingly HTTPS. The cause is
    decided by the clock, not by which exception the shutdown happened to
    produce: past the deadline, WE are the cause, whatever the stdlib called
    it.
    """
    return time.monotonic() > deadline


@contextlib.contextmanager
def _socket_deadline(sock, deadline: float):
    """Force `sock` shut when `deadline` passes, for the duration of the block.

    THE ONLY THING THAT ACTUALLY BOUNDS WALL-CLOCK HERE, and it took three tries
    to find that out, so the two rejected approaches are recorded rather than
    left to be re-attempted:

    1. Checking the clock BETWEEN read calls. Useless when one call can block
       forever: on a chunked body, read1() must first parse a chunk-size line,
       and that readline loops internally until it sees CRLF.
    2. Re-arming sock.settimeout(remaining) before each read. This looks right
       and is not: a socket timeout bounds each individual recv, never the total.
       A server trickling one byte every 2 s under a 3 s timeout satisfies every
       recv and still runs forever -- measured, 24.1 s against a 3 s deadline,
       with the socket's own timeout correctly reading 2.998 s the whole time.
       An attacker simply trickles faster than any threshold.

    A watchdog is out of band, so it does not care how deeply the stdlib is
    blocked or how many recvs a single call makes. shutdown() rather than
    close(): close() while another thread is inside recv can hand the fd number
    to something else. The block must still re-check the deadline afterwards,
    because a shutdown surfaces as ordinary EOF on most platforms -- otherwise a
    truncated body would read as a complete one.

    This also covers conn.getresponse(), which parses the status line and
    headers and has exactly the same unbounded-blocking shape as the body read
    (http.client will accept megabytes of header before giving up).
    """
    def _expire():
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass                       # already gone; nothing to interrupt

    timer = threading.Timer(max(0.0, deadline - time.monotonic()), _expire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


# Caps for the three fragment-copied fields recorded in index.json. Generous by
# design -- a real source_form is a name and a real URL is far under 2 KB, so
# nothing legitimate is touched -- but bounded, because these are the only
# open-ended strings in the file the judge prompt calls locally generated.
#
# The asymmetry is what forced this: canon_validate.py caps the same source_form to 60 chars in its
# DIAGNOSTIC LABEL (the value itself carries no maxLength in either schema) on the grounds that "a name long enough to hold a paragraph of
# instructions is not a name", while this file wrote it unbounded into the
# judge's own evidence index -- measured at ~12 KB per field, i.e. ~500 KB of
# attacker-authored text at the shipped DEFAULT_BATCH_SIZE. `source` is the worst
# of the three: for a REFUSED item it never passed validate_url, so CONTROL_CHAR_RE
# never applied and it could carry newlines and multi-line prose.
#
# The judge prompt already names all three untrusted, so this is defence in depth
# rather than the barrier -- but the barrier should not be prompt wording on one
# side of a boundary and a hard cap on the other. item_index, not source_form,
# is the field that authoritatively identifies an entry.
MAX_RECORDED_FIELD_CHARS = {"source_form": 200, "source": 2048, "basis": 64}


def _recorded(field: str, value):
    """Make a fragment-copied value safe to WRITE and bounded in LENGTH."""
    value = _encodable(value)
    if not isinstance(value, str):
        return value
    # Whitespace FLATTENED, not merely capped. The first version of this
    # function bounded length only, while its own comment said the hazard was
    # shape -- measured, a 4,000-char hostile `source` was recorded at 2,062
    # chars still carrying 82 newlines, i.e. multi-line injected prose in the
    # file the judge prompt calls locally generated. A refused item never
    # passed validate_url, so CONTROL_CHAR_RE never applied to it.
    value = " ".join(value.split())
    # NOT an assert. `python -O` strips asserts, which would leave `cap` None,
    # make `len(value) > cap` raise TypeError from run_batch -- outside every
    # handler -- and destroy index.json, the one outcome this file says must
    # never happen. A guard that disappears under an interpreter flag is not a
    # guard. Falling back to the STRICTEST declared cap keeps an undeclared
    # field bounded rather than unbounded or fatal; the suite enforces the
    # declaration itself, where being loud costs nothing at runtime.
    cap = MAX_RECORDED_FIELD_CHARS.get(field, min(MAX_RECORDED_FIELD_CHARS.values()))
    if len(value) > cap:
        return value[:cap] + "...[truncated]"
    return value


def _read_bounded(resp, deadline: float) -> bytes:
    """Read up to MAX_BYTES + 1 bytes, re-checking the deadline as it goes.

    A single resp.read(MAX_BYTES + 1) is bounded by VOLUME and by the socket's
    PER-RECV idle timeout, and by neither of the two things that matter here.
    A server that sends one byte every few seconds is never idle long enough to
    trip the socket timeout and never sends enough to hit the cap, so the call
    blocks for exactly as long as the server chooses. Measured before this fix:
    a 12 s trickle against a 3 s per-item deadline returned `fetched` after
    12.0 s -- elapsed time equal to the attacker's chosen duration.

    That is not merely slow. The whole prepare step is ONE bash call under a
    measured 600 s clamp (the same clamp #348 is about), so one held socket
    runs the call out of time, which reports EVIDENCE_FAILED, spends a
    citation-review retry, and on exhaustion merges ZERO batches -- defeating
    the batch deadline that round 2 added for this exact scenario, since that
    deadline is only tested BETWEEN items.

    read1() rather than read(): read(n) blocks until n bytes have arrived,
    which would put the deadline check out of reach entirely.

    But read1() is NOT "at most one recv", and believing it was is how the first
    version of this function stayed vulnerable to the attack it was written to
    stop. On a `Transfer-Encoding: chunked` body, http.client must first read a
    chunk-size LINE, and that readline loops until it sees CRLF. A server that
    trickles the chunk-size line a byte at a time is never idle long enough to
    trip the socket timeout and never completes the line, so read1() does not
    return at all. Measured against the between-calls-only version: a 24 s
    trickle of the chunk-size line against a 3 s deadline took 24.1 s and still
    returned `fetched` -- elapsed once again equal to the server's choice.

    Checking the clock between iterations cannot bound a single iteration that
    blocks forever -- and neither can re-arming the socket timeout, which is
    rejected approach #2 in _socket_deadline's docstring. An earlier version of
    THIS docstring taught that re-arm as the working mechanism while the sibling
    docstring recorded it as disproven, and the code kept executing it: measured,
    deleting it changed no phase and no elapsed time.

    The bound comes from _socket_deadline, which the caller wraps around this
    call. What this loop owes it is the EOF re-check below -- a shutdown arrives
    as ordinary EOF, so without that check a body cut short by the deadline is
    returned as a complete one (measured: the chunked phase returns `fetched`
    with the check removed). Both are load-bearing and neither subsumes the
    other; removing either fails a different phase.
    """
    chunks = []
    remaining = MAX_BYTES + 1
    while remaining > 0:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _refuse("read-timeout")
        try:
            chunk = resp.read1(min(READ_CHUNK_BYTES, remaining))
        except (socket.timeout, TimeoutError):
            raise _refuse("read-timeout")
        if not chunk:
            # EOF -- or the watchdog shutting the socket, which is
            # indistinguishable from EOF here. Re-check the clock so a body cut
            # short by the deadline is refused rather than served as complete.
            if time.monotonic() > deadline:
                raise _refuse("read-timeout")
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _fetch_hop(url, current, chain, hop, deadline, allowed_types) -> dict:
    """ONE redirect hop. Every exit is a dict, a Refused, or a _Redirect.

    THE TOTALITY RULE, and why this wrapper exists at all. Four instances of one
    class turned up across three of 1.16.1's review rounds (round 2 found none),
    each escaping this boundary as itself, and each fix closed that one instance:
        round 1  ValueError      from urlsplit on `http://[::1`
        round 3  HTTPException   from a malformed status line
        round 4  UnicodeError    from getaddrinfo on a bad IDNA label
        round 4  UnicodeEncodeError from conn.request on a non-ASCII path
    Every one of them aborted the whole batch, because run_batch only catches
    Refused -- and index.json was then never written at all, which is strictly
    worse than a per-item refusal. Chasing a fifth instance would be the same
    mistake a fifth time, so the boundary is now TOTAL: anything that is not
    already a Refused becomes one, carrying the exception's stdlib TYPE NAME and
    never its text. An unexpected exception type is a bug to fix, but it must
    cost one citation, never the run's entire evidence index.

    Round 5 is the coda, and it is the reason this docstring says WHAT the guard
    covers rather than that the class is closed. The sixth instance was real but
    out of this wrapper's reach entirely: canon_validate.py, the documented twin
    that runs the same static decision with no resolver behind it, was still
    interpolating a raw urlsplit scheme into its refusal reason. A totalising
    guard bounds the file it wraps; it says nothing about a sibling that
    reimplements the same rule. That parity is owned by
    canon_citation_refusal.test.py's shared table -- which missed it for four
    rounds because every scheme row was a KNOWN member, where the two engines
    agree by construction.
    """
    # THE STATIC CHECKS FIRST, then the clock. They are pure string work -- no
    # DNS, no socket -- so being late is no reason to skip them, and skipping
    # them cost the one thing this file exists to report. Measured before this
    # order: a hop entered past the deadline returned `total-timeout` without
    # ever running validate_url, so an attacker who burns the budget on hop 0
    # and then answers `302 Location: http://127.0.0.1:6379/` gets the loopback
    # attempt recorded as a run fact -- one of the run-fact reasons the judge
    # is explicitly told NOT to read as a defect in the source. The gate did not
    # flip (any refused:* fails the judge's first check either way), but the
    # evidence named our clock instead of their redirect.
    #
    # validate_url raises Refused, which the caller's handler keeps under its
    # own name: a security refusal computed late is still a security refusal.
    validate_url(current)
    if time.monotonic() > deadline:
        raise _refuse("total-timeout")
    try:
        return _fetch_hop_inner(url, current, chain, hop, deadline, allowed_types)
    except _Redirect:
        raise
    except Refused as refusal:
        # resolve_and_pin raises Refused, and this handler sits BEFORE the
        # catch-all -- so its tokens never reached the clock check added last
        # round, which that check's own comment predicted ("resolve_and_pin
        # takes no deadline at all"). Measured: a resolver overrunning a 0.5 s
        # budget by 0.7 s recorded refused:dns-failure:-3, which is not one of
        # the judge's run-fact reasons, so our own timeout reads as "this
        # citation's host does not resolve".
        #
        # ONLY the resolution-timing tokens are re-attributed, and the closed
        # set is the point: relabelling a SECURITY refusal would be far worse
        # than the mislabel it fixes. `loopback-address` past the deadline is
        # still `loopback-address` -- an SSRF attempt that reported itself as a
        # timeout would be hidden exactly when it matters.
        if _past_deadline(deadline) and str(refusal).split(":", 1)[0] in _RESOLUTION_TIMING_REASONS:
            raise _refuse("read-timeout")
        raise
    except Exception as exc:                      # noqa: BLE001 -- deliberate, see above
        # The clock FIRST, here too. validate_url and resolve_and_pin are called
        # before the inner try, so everything they raise lands on this catch-all
        # -- and resolve_and_pin takes no deadline at all, so a slow resolver
        # burns the budget and then the outcome blames something else. Same
        # argument as the four handlers inside: past the deadline WE are the
        # cause, and only the run-fact reasons are ones the judge is told not
        # to read as a defect in the citation.
        if _past_deadline(deadline):
            raise _refuse("read-timeout")
        raise _refuse(f"internal-error:{type(exc).__name__}")


def _fetch_hop_inner(url, current, chain, hop, deadline, allowed_types) -> dict:
    scheme, host, port, path = validate_url(current)
    # INSIDE the guarded region, unlike before: getaddrinfo raises a bare
    # UnicodeError (a ValueError, not a gaierror and not an OSError) for a
    # malformed IDNA label such as `a..example.com` -- an ordinary typo, no
    # attacker needed -- and this call used to sit outside every handler.
    pinned = resolve_and_pin(host, port)
    # ORIGIN ONLY -- never `current`. After hop 0 the URL is built from the
    # server's own Location header, so its path/query/fragment are
    # attacker-authored text, and this record lands in index.json, the file
    # the judge prompt vouches for as locally generated. validate_url's
    # CONTROL_CHAR_RE stops CR/LF/space and nothing else: ordinary printable
    # separators still spell prose, and U+00A0 survives too because
    # http.client decodes headers as ISO-8859-1 and a fragment never reaches
    # conn.request's ASCII encode. Scheme and host are the only two parts
    # already constrained (allowlist; urlsplit/IDNA), so they are the only
    # two kept. This is the sibling of the 1.16.1 content_type fix: that one
    # was scoped to a FIELD when the property is about the whole FILE.
    chain.append({"origin": origin_of(scheme, host, port), "host": host,
                  "hop": hop, "resolved": pinned})

    remaining = max(1.0, min(CONNECT_TIMEOUT_SEC, deadline - time.monotonic()))
    if scheme == "https":
        conn = _PinnedHTTPSConnection(host, pinned, port, remaining, ssl.create_default_context())
    else:
        conn = _PinnedHTTPConnection(host, pinned, port, remaining)

    try:
        # No Referer, no cookies, and an honest UA: this is a citation
        # check, not a browser session, and sending ambient credentials
        # would recreate the confused-deputy problem from the other side.
        # PERCENT-ENCODED, because http.client does `request.encode('ascii')` and
        # raises UnicodeEncodeError on any non-ASCII byte. Not hypothetical:
        # headers decode as ISO-8859-1, so one byte >= 0x80 in a Location makes
        # the joined path non-ASCII, and CONTROL_CHAR_RE only covers
        # [\x00-\x20\x7f]. It also fires on entirely BENIGN input -- a raw
        # non-ASCII citation URL, which for this plugin's Hebrew and Yiddish
        # corpora is the normal case, not the edge case. Encoding is the
        # correctness fix; _fetch_hop's totality rule is only the backstop.
        # `safe` keeps the sub-delimiters already legal in a request-target, and
        # keeps `%` so an already-encoded path is not double-encoded.
        conn.request("GET", quote(path, safe="/?=&%:@+$,;~!*'()[]"), headers={
            # authority(), not `host`: the connection goes to the pinned IP,
            # so this header is the ONLY thing telling the server which site
            # was asked for. A bare hostname misroutes every non-default-port
            # URL and is invalid for an IPv6 literal.
            "Host": wire_authority(scheme, host, port),
            "User-Agent": "literary-translator/1.16.1 (+citation-audit)",
            "Accept": "text/html, text/plain, application/xhtml+xml;q=0.9, */*;q=0.1",
        })
        # Captured BEFORE getresponse(): when a response is will_close,
        # getresponse() calls conn.close(), which sets conn.sock to None. The
        # socket OBJECT stays usable because the response's file wrapper still
        # holds a reference (CPython defers the real close while _io_refs > 0),
        # so the body-phase watchdog below needs this handle rather than a
        # conn.sock that has already been cleared.
        sock = conn.sock
        # The watchdog covers the status line and headers too: http.client will
        # accept megabytes of them, and a trickled header blocks exactly the way
        # a trickled chunk-size line does.
        with _socket_deadline(sock, deadline):
            resp = conn.getresponse()
        # The re-check _socket_deadline's docstring calls for, on the phase that
        # lacked it. http.client._read_headers treats EOF as a normal end of
        # headers, so a watchdog-cut response PARSES as complete -- measured, a
        # trickled `404 ... X-Pad:` came back as http_error:404 at exactly the
        # deadline, because the status != 200 branch returns without ever
        # consulting the clock. The 200 and redirect paths are covered by
        # _read_bounded's EOF check and the next hop's top-of-hop check; this
        # was the one exit that was not.
        if time.monotonic() > deadline:
            raise _refuse("read-timeout")
        status = resp.status
        location = resp.getheader("Location")
        # TWO readings of one header, deliberately independent. `ctype` keeps
        # its exact pre-#801 derivation because it feeds the ADMISSION decision
        # and its closed-token record; `ctype_header` is the raw field, read
        # only by body_charset, which decides nothing about admission. Deriving
        # one from the other would tie the boundary's admission rule to a
        # parameter parser it has no reason to depend on.
        ctype_header = resp.getheader("Content-Type") or ""
        ctype = ctype_header.split(";")[0].strip().lower()

        if status in (301, 302, 303, 307, 308):
            if not location:
                raise _refuse(f"redirect-without-location:{status}")
            if hop >= MAX_REDIRECTS:
                raise _refuse("too-many-redirects")
            # Resolved against the CURRENT url, then re-validated from
            # scratch on the next iteration -- a relative Location must not
            # inherit any trust from the hop it came from.
            #
            # urljoin PARSES both sides, so it raises ValueError itself on a
            # malformed Location such as `http://[::1` -- before the guarded
            # validate_url() call that the next iteration would have made.
            # The round-1 urlsplit hardening sits one step too late to see it.
            # (Round 1, not round 2: `unparseable-url` enters this file in
            # 7e714fe. Round 3's commit message says round 2 and is wrong.)
            try:
                target = urljoin(current, location)
            except ValueError:
                raise _refuse("unparseable-redirect-location")
            raise _Redirect(target)

        if status != 200:
            return {"ok": False, "status": status, "url": url,
                    "final_origin": chain[-1]["origin"],
                    "chain": chain, "outcome": f"http_error:{status}"}

        # The refused type is reported as a CLOSED token, never the raw
        # header: this reason string is written into index.json's `outcome`,
        # which the judge reads and the judge prompt calls locally
        # generated. See content_type_token(). An absent Content-Type is
        # admitted deliberately -- plenty of ordinary servers omit it, and
        # the body is still capped, decoded with errors="replace", and read
        # only through the delimiter -- so "absent" is recorded rather than
        # silently indistinguishable from an allowed type. (A response with no
        # Content-Type also declares no charset, so body_charset gives it the
        # UTF-8 this file used unconditionally before #801.)
        # ONE evaluation, bound to a local: the guard below and the value
        # recorded in index.json must be the SAME decision. Calling the
        # function twice let a future edit widen one and not the other with
        # nothing red -- on the one field whose closed-set property is the
        # entire point of the 1.16.1 round-1 fix.
        ctype_token = content_type_token(ctype, allowed_types)
        if ctype and ctype_token == "other":
            raise _refuse("content-type-not-allowed")

        with _socket_deadline(sock, deadline):
            raw = _read_bounded(resp, deadline)
        truncated = len(raw) > MAX_BYTES
        raw = raw[:MAX_BYTES]
        body = raw.decode(body_charset(ctype_header), errors="replace")
        return {
            "ok": True, "status": status, "url": url, "final_origin": chain[-1]["origin"],
            "chain": chain, "content_type": ctype_token,
            "bytes": len(raw),
            "truncated": truncated, "outcome": "fetched", "body": body,
        }
    except (socket.timeout, TimeoutError):
        if _past_deadline(deadline):
            raise _refuse("read-timeout")
        raise _refuse("connect-timeout")
    except ssl.SSLError as exc:
        if _past_deadline(deadline):
            raise _refuse("read-timeout")
        raise _refuse(f"tls-error:{type(exc).__name__}")
    except OSError as exc:
        if _past_deadline(deadline):
            raise _refuse("read-timeout")
        raise _refuse(f"network-error:{type(exc).__name__}")
    except http.client.HTTPException as exc:
        # The fourth exit, and the one round 7's fix missed: HTTPException is
        # not an OSError, so it is a genuinely separate handler. Measured, a
        # merely-SLOW server that committed no protocol violation came back as
        # http-protocol-error:BadStatusLine at exactly the deadline -- the
        # watchdog cut the socket, http.client saw a truncated status line, and
        # blamed the server. By this fix's own argument that is worse than what
        # it fixed: the judge reads it as "this citation's server is broken".
        if _past_deadline(deadline):
            raise _refuse("read-timeout")
        # http.client has its OWN hierarchy and HTTPException is NOT an
        # OSError -- measured: issubclass(BadStatusLine, OSError) is False.
        # So these escaped every handler above, and run_batch only catches
        # Refused: one malformed status line aborted the whole batch.
        #
        # The bigger half is what the traceback CONTAINS. BadStatusLine puts
        # the server's raw status line in its args, so an escaped exception
        # printed the server's own text to a stream the prepare agent reads
        # and is told to report -- the exact channel this release closes for
        # Content-Type and redirect URLs. Only the stdlib TYPE NAME crosses
        # the boundary; the instance's text never does.
        raise _refuse(f"http-protocol-error:{type(exc).__name__}")
    finally:
        conn.close()


def iter_sources(snapshot):
    """Every (index, item, source) in the batch snapshot that carries a
    `source`, whatever its basis or disposition. See the module docstring for
    why this is not narrowed to `basis == "established"`.

    THE SNAPSHOT IS A TOP-LEVEL ARRAY. `canon-batch.schema.json` declares
    `"type": "array"`, and `canon_validate.py --approve-to` publishes the
    fragment's RAW BYTES (`_write_approved_snapshot(path, raw)`), so the
    approved snapshot is that same array. An earlier version of this function
    assumed `{"items": [...]}` and raised `AttributeError: 'list' object has no
    attribute 'get'` on every real snapshot -- which, in the shipped split,
    would have failed every live prepare step on every attempt, burnt the whole
    retry ladder and returned `citation-review-exhausted` for every batch.

    The wrapped shape is still accepted because it costs one clause and makes
    this readable directly from a hand-built fixture; the array is the real
    production shape.
    """
    if isinstance(snapshot, list):
        items = snapshot
    elif isinstance(snapshot, dict):
        items = snapshot.get("items")
    else:
        raise TypeError(f"batch snapshot must be an array or an object, got {type(snapshot).__name__}")
    if not isinstance(items, list):
        raise TypeError("batch snapshot has no item array")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        src = item.get("source")
        if isinstance(src, str) and src:
            yield i, item, src


def run_batch(batch_path: Path, out_dir: Path, *,
              allowed_types=ALLOWED_CONTENT_PREFIXES) -> int:
    # TypeError is caught alongside the I/O and parse errors on purpose: a
    # snapshot of an unexpected SHAPE must surface as the contract's one-line
    # {"success": false, ...} just like an unreadable one. Letting it escape as
    # a traceback breaks the output contract precisely when the prepare agent
    # most needs a parseable answer -- it would read stderr noise, report
    # failure, and burn an attempt off the retry ladder for a reason nothing
    # downstream could classify.
    try:
        snapshot = json.loads(batch_path.read_text(encoding="utf-8"))
        sources = list(iter_sources(snapshot))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(json.dumps({"success": False, "error": f"unreadable-batch: {exc}"}))
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    counts = {"fetched": 0, "refused": 0, "http_error": 0}

    # BATCH-WIDE deadline, on top of each item's own. Without it the per-item
    # 30 s is not a bound on anything the caller cares about: a glossary batch is
    # DEFAULT_BATCH_SIZE = 40 sources, and this script runs as ONE bash call
    # inside ONE agent() call, under the same measured 600 s clamp that #348 is
    # about. 40 x 30 s = 1200 s, so ~20 slow or dead hosts is enough to have the
    # whole call killed -- no attacker required. A killed call reports
    # EVIDENCE_FAILED, which spends a citation-review retry, and exhausting the
    # ladder returns citation-review-exhausted and merges ZERO batches.
    # Fail SOFT instead: items past the budget are recorded as a normal refusal,
    # which the judge already knows how to treat, and the script still prints its
    # metadata line and exits cleanly.
    batch_deadline = time.monotonic() + BATCH_TIMEOUT_SEC
    # Companion budget, spent in BYTES rather than seconds. Incremented only
    # at the single write site below; nothing else touches it.
    spent_bytes = 0
    # ADMIT THRESHOLD, not the ceiling itself -- derived here rather than kept
    # as a second module constant, so the relationship holds even for a test
    # that monkeypatches BATCH_MAX_TOTAL_BYTES alone. See that constant's own
    # comment ("HOW THE CEILING HOLDS EXACTLY") for why the guard below checks
    # against THIS threshold and not against BATCH_MAX_TOTAL_BYTES directly.
    admit_threshold = BATCH_MAX_TOTAL_BYTES - 3 * MAX_BYTES

    for i, item, src in sources:
        # The entry dict is built INSIDE a guard, not before one. It used to sit
        # above the try below, so anything raised while CONSTRUCTING it -- a
        # _recorded() fault, an item that is not the shape item.get() expects --
        # escaped run_batch entirely and destroyed index.json. That is exactly
        # what the second guard's own comment forbids ("one bad citation may cost
        # one entry; it may never cost the run's evidence index"), and the guard
        # could not reach the lines that built its subject.
        #
        # item_index is set first and separately: it is the field that identifies
        # an entry, it cannot fail, and it is what lets a failed item still be
        # reported as an item rather than vanish from the index.
        entry = {"item_index": i}
        try:
            entry["source_form"] = _recorded("source_form", item.get("source_form"))
            entry["basis"] = _recorded("basis", item.get("basis"))
            entry["source"] = _recorded("source", src)
        except Exception as exc:                  # noqa: BLE001 -- deliberate
            entry.setdefault("source_form", None)
            entry.setdefault("basis", None)
            entry.setdefault("source", None)
            entry["outcome"] = f"refused:internal-error:{type(exc).__name__}"
            counts["refused"] += 1
            index.append(entry)
            continue
        if time.monotonic() > batch_deadline:
            entry["outcome"] = "refused:batch-deadline"
            counts["refused"] += 1
            index.append(entry)
            continue
        # Same soft-fail shape as the deadline check above, one budget later:
        # the item is refused, recorded, and the run continues rather than
        # dying mid-write. Checked against admit_threshold, NOT against
        # BATCH_MAX_TOTAL_BYTES directly -- THE BOUND THIS BUYS, stated once,
        # here, where it is enforced: the last item admitted starts BELOW
        # admit_threshold and can write at most 3 * MAX_BYTES (one item's
        # worst case; the clamp applies to RAW bytes before the replacement
        # decode, so that is the ceiling on a single write), so the running
        # total after it is strictly below
        # admit_threshold + 3 * MAX_BYTES == BATCH_MAX_TOTAL_BYTES. The
        # ceiling therefore holds EXACTLY, for any batch size N -- against a
        # pre-fix N * 3 * MAX_BYTES that grew without bound in N. Checking
        # "spent + MAX_BYTES > BATCH_MAX_TOTAL_BYTES" per item instead would
        # refuse items that would in fact have fit whenever the current item
        # is not the worst case -- see BATCH_MAX_TOTAL_BYTES's own comment for
        # why a false refusal is the expensive error here, and for why no
        # fixed number can also promise it never refuses a legitimate batch.
        # Item 40 can be starved by item 1 exactly as it can under
        # BATCH_TIMEOUT_SEC -- deliberate, and not a per-item fair share this
        # ticket buys.
        if spent_bytes >= admit_threshold:
            entry["outcome"] = "refused:batch-byte-budget"
            counts["refused"] += 1
            index.append(entry)
            continue
        deadline = min(time.monotonic() + TOTAL_TIMEOUT_SEC, batch_deadline)
        try:
            result = fetch_one(src, deadline=deadline, allowed_types=allowed_types)
        except Refused as exc:
            entry["outcome"] = f"refused:{exc}"
            counts["refused"] += 1
            index.append(entry)
            continue
        except Exception as exc:                  # noqa: BLE001 -- see below
            # SECOND, independent guard. _fetch_hop's totality rule should make
            # this unreachable, and the test suite asserts it is -- but the two
            # guards fail differently, which is the whole point: this one holds
            # even if a future edit introduces a raise OUTSIDE _fetch_hop's try
            # (which is exactly how the round-4 getaddrinfo defect arose -- the
            # call sat outside the guarded region, not inside a hole in it).
            # index.json MUST be written: the prepare agent reports its absence
            # as EVIDENCE_FAILED, which spends a citation-review retry, and
            # exhausting that ladder merges ZERO batches. One bad citation may
            # cost one entry; it may never cost the run's evidence index.
            entry["outcome"] = ("refused:read-timeout" if _past_deadline(deadline)
                                else f"refused:internal-error:{type(exc).__name__}")
            counts["refused"] += 1
            index.append(entry)
            continue

        entry["outcome"] = result["outcome"]
        entry["final_origin"] = result.get("final_origin")
        entry["chain"] = result.get("chain")
        if result["ok"]:
            name = f"{EVIDENCE_PREFIX}{i:03d}.txt"
            # Encode ONCE and spend exactly what gets written -- not
            # result["bytes"]. result["bytes"] is len(raw) BEFORE the
            # errors="replace" decode, and a run of invalid bytes
            # can expand on decode: measured, b"\xff" * n decodes to n
            # replacement characters and re-encodes to exactly 3n bytes (NOT a
            # universal "every invalid byte becomes its own U+FFFD" -- a
            # malformed MULTI-byte sequence can collapse several source bytes
            # into a single U+FFFD instead, which expands less). What the bound
            # rests on is the worst case, which b"\xff"*n reaches exactly: one
            # item's written size tops out at 3 * MAX_BYTES, so spending
            # result["bytes"] would let BATCH_MAX_TOTAL_BYTES of budget write
            # up to 3x itself on disk. write_bytes (rather than
            # write_text(..., encoding="utf-8"), which emits identical bytes on
            # this platform) additionally makes the evidence file byte-exact
            # rather than newline-translated -- correct for an evidence
            # artifact.
            written = result["body"].encode("utf-8")
            (out_dir / name).write_bytes(written)
            spent_bytes += len(written)
            entry["evidence_file"] = name
            # `truncated` keeps exactly the meaning the judge prompt gives it --
            # the body was cut at the size cap -- because that cap is applied
            # to RAW bytes before the decode, and this budget does not touch
            # it. `bytes` is given no meaning by the prompt at all; it is
            # never surfaced as a file size and nothing instructs the judge to
            # compare it against one, so a `bytes: 2000000` entry beside a
            # 6 MB file on disk is possible and misleads nobody.
            entry["bytes"] = result["bytes"]
            entry["truncated"] = result["truncated"]
            entry["content_type"] = result.get("content_type")
            counts["fetched"] += 1
        else:
            # No spend here: the write above is the only evidence-body write in
            # the function (run_single writes no files at all). A non-200
            # returns before _read_bounded runs, and a redirect raises
            # _Redirect before it, so no body is consumed or written on this
            # path -- a buffered header read can pull body bytes off the wire,
            # but none reach _read_bounded or disk.
            counts["http_error"] += 1
        index.append(entry)

    (out_dir / "index.json").write_text(
        json.dumps({"batch": str(batch_path), "entries": index, "counts": counts},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # ONE line, metadata only. No retrieved bytes reach stdout, because the
    # agent that runs this command reads its stdout and must not be injectable
    # by the pages it just fetched.
    print(json.dumps({
        "success": True,
        "index_path": str(out_dir / "index.json"),
        "evidence_dir": str(out_dir),
        "n_sources": len(index),
        "counts": counts,
    }))
    return 0


def run_single(url: str, *, allowed_types=ALLOWED_CONTENT_PREFIXES) -> int:
    deadline = time.monotonic() + TOTAL_TIMEOUT_SEC
    try:
        result = fetch_one(url, deadline=deadline, allowed_types=allowed_types)
    except Refused as exc:
        print(json.dumps({"success": False, "outcome": f"refused:{exc}", "url": url}))
        return 1
    body = result.pop("body", "")
    print(json.dumps({"success": result["ok"], **result}))
    if result["ok"]:
        print(DELIMITER)
        sys.stdout.write(body)
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Validated citation retrieval -- the only sanctioned fetch "
                    "in the citation audit path (#347).")
    ap.add_argument("url", nargs="?", help="single-URL mode (testing); prints metadata, delimiter, body")
    ap.add_argument("--batch", help="path to a batch snapshot JSON")
    ap.add_argument("--out-dir", help="directory to write evidence files and index.json into")
    ap.add_argument("--allow-content-type", action="append", metavar="PREFIX",
                    help="Content-Type prefix to admit, repeatable. Replaces the "
                         "default list entirely when given. Bare type/subtype only "
                         "(text/, application/pdf) -- no parameters or wildcards.")
    args = ap.parse_args(argv)
    allowed_types = parse_content_type_prefixes(args.allow_content_type)

    if args.batch:
        if not args.out_dir:
            ap.error("--batch requires --out-dir")
        return run_batch(Path(args.batch), Path(args.out_dir), allowed_types=allowed_types)
    if args.url:
        return run_single(args.url, allowed_types=allowed_types)
    ap.error("give either a URL or --batch <snapshot> --out-dir <dir>")


if __name__ == "__main__":
    sys.exit(main())
