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
open web research by design under `research_mode: live`, and the judge still
holds a Bash tool. Both are named as residual exposures in the release notes
and tracked separately. Overclaiming here would be worse than the original bug,
because the next reader would stop looking.

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
6. Caps on time, bytes and content type. A boundary that can be held open
   forever is a denial-of-service surface of its own.

OUTPUT CONTRACT (consumed by the prepare/judge split -- do not change casually).

  --batch <snapshot.json> --out-dir <dir>
      <snapshot.json> is a canon-batch snapshot: a TOP-LEVEL JSON ARRAY, the
      shape `canon-batch.schema.json` declares and the exact bytes
      `canon_validate.py --approve-to` publishes. (A `{"items": [...]}` wrapper
      is also accepted, for hand-built fixtures.)
      Iterates every item carrying a `source` field -- NOT only the ones with
      `basis: "established"`. The queued branch of canon-batch.schema.json types
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
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
MAX_BYTES = 2_000_000
TOTAL_TIMEOUT_SEC = 30.0
CONNECT_TIMEOUT_SEC = 10.0
EVIDENCE_PREFIX = "citation-"

# Text-ish only. A citation is a document a human could have read; anything
# else is either useless to the judge or an attempt to make it ingest something
# it cannot evaluate.
ALLOWED_CONTENT_PREFIXES = ("text/", "application/xhtml", "application/xml", "application/json")

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


def check_address_literal(host: str) -> None:
    """Reject a host that is ALREADY an IP literal in a non-global range.

    Separate from the getaddrinfo pass below because a literal never goes
    through name resolution at all, so a resolution-time check would simply not
    run for `http://127.0.0.1/`.
    """
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
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

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _refuse(f"scheme-not-allowed:{scheme or 'none'}")
    if parts.username is not None or parts.password is not None:
        raise _refuse("embedded-credentials")

    host = parts.hostname
    if not host:
        raise _refuse("no-host")
    host = host.lower()

    # `localhost` and anything under it are refused by NAME, before resolution:
    # a resolver can be configured to point them anywhere, and admitting the
    # name would make the refusal depend on local DNS configuration.
    if host == "localhost" or host.endswith(".localhost"):
        raise _refuse("localhost-name")

    check_address_literal(host)

    try:
        port = parts.port
    except ValueError:
        raise _refuse("invalid-port")
    if port is None:
        port = 443 if scheme == "https" else 80
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


def fetch_one(url: str, *, deadline: float) -> dict:
    """Fetch a single URL through the full boundary, following redirects
    manually and revalidating EVERY hop.

    Returns a metadata dict with the decoded body under "body". Raises Refused
    for anything the boundary declines.
    """
    chain = []
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            raise _refuse("total-timeout")

        scheme, host, port, path = validate_url(current)
        pinned = resolve_and_pin(host, port)
        chain.append({"url": current, "host": host, "resolved": pinned})

        remaining = max(1.0, min(CONNECT_TIMEOUT_SEC, deadline - time.monotonic()))
        if scheme == "https":
            conn = _PinnedHTTPSConnection(host, pinned, port, remaining, ssl.create_default_context())
        else:
            conn = _PinnedHTTPConnection(host, pinned, port, remaining)

        try:
            # No Referer, no cookies, and an honest UA: this is a citation
            # check, not a browser session, and sending ambient credentials
            # would recreate the confused-deputy problem from the other side.
            conn.request("GET", path, headers={
                "Host": host,
                "User-Agent": "literary-translator/1.16.1 (+citation-audit)",
                "Accept": "text/html, text/plain, application/xhtml+xml;q=0.9, */*;q=0.1",
            })
            resp = conn.getresponse()
            status = resp.status
            location = resp.getheader("Location")
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()

            if status in (301, 302, 303, 307, 308):
                if not location:
                    raise _refuse(f"redirect-without-location:{status}")
                if hop >= MAX_REDIRECTS:
                    raise _refuse("too-many-redirects")
                # Resolved against the CURRENT url, then re-validated from
                # scratch on the next iteration -- a relative Location must not
                # inherit any trust from the hop it came from.
                current = urljoin(current, location)
                continue

            if status != 200:
                return {"ok": False, "status": status, "url": url, "final_url": chain[-1]["url"],
                        "chain": chain, "outcome": f"http_error:{status}"}

            if ctype and not any(ctype.startswith(p) for p in ALLOWED_CONTENT_PREFIXES):
                raise _refuse(f"content-type-not-allowed:{ctype}")

            raw = resp.read(MAX_BYTES + 1)
            truncated = len(raw) > MAX_BYTES
            raw = raw[:MAX_BYTES]
            body = raw.decode("utf-8", errors="replace")
            return {
                "ok": True, "status": status, "url": url, "final_url": chain[-1]["url"],
                "chain": chain, "content_type": ctype, "bytes": len(raw),
                "truncated": truncated, "outcome": "fetched", "body": body,
            }
        except (socket.timeout, TimeoutError):
            raise _refuse("connect-timeout")
        except ssl.SSLError as exc:
            raise _refuse(f"tls-error:{type(exc).__name__}")
        except OSError as exc:
            raise _refuse(f"network-error:{type(exc).__name__}")
        finally:
            conn.close()

    raise _refuse("too-many-redirects")


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


def run_batch(batch_path: Path, out_dir: Path) -> int:
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

    for i, item, src in sources:
        deadline = time.monotonic() + TOTAL_TIMEOUT_SEC
        entry = {
            "item_index": i,
            "source_form": item.get("source_form"),
            "basis": item.get("basis"),
            "source": src,
        }
        try:
            result = fetch_one(src, deadline=deadline)
        except Refused as exc:
            entry["outcome"] = f"refused:{exc}"
            counts["refused"] += 1
            index.append(entry)
            continue

        entry["outcome"] = result["outcome"]
        entry["final_url"] = result.get("final_url")
        entry["chain"] = result.get("chain")
        if result["ok"]:
            name = f"{EVIDENCE_PREFIX}{i:03d}.txt"
            (out_dir / name).write_text(result["body"], encoding="utf-8")
            entry["evidence_file"] = name
            entry["bytes"] = result["bytes"]
            entry["truncated"] = result["truncated"]
            entry["content_type"] = result.get("content_type")
            counts["fetched"] += 1
        else:
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


def run_single(url: str) -> int:
    deadline = time.monotonic() + TOTAL_TIMEOUT_SEC
    try:
        result = fetch_one(url, deadline=deadline)
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
    args = ap.parse_args(argv)

    if args.batch:
        if not args.out_dir:
            ap.error("--batch requires --out-dir")
        return run_batch(Path(args.batch), Path(args.out_dir))
    if args.url:
        return run_single(args.url)
    ap.error("give either a URL or --batch <snapshot> --out-dir <dir>")


if __name__ == "__main__":
    sys.exit(main())
