# Capture guard: recursively percent-decode for the dangerous-verb hint

**Status:** deferred (low priority) · **Section:** enduser-handbook · **Surfaced:** 2026-06-20 (PR #9)

## Problem

`assets/lib/capture-guard-policy.mjs` percent-decodes a request URL **once** (`safeDecode`) before scanning the path/query for dangerous verbs (`/delete`, `/send`, …) in the `[guard:deny]` step. A **doubly**-encoded verb on a GET path (`%2564elete` → `%64elete` after one decode → `delete` only after a second decode) would slip past the dangerous-verb hint if the target server itself double-decodes.

This is a defense-in-depth **hint** only, not the guard's primary contract:
- The author's `denyPatterns` match the **raw** URL/body (`matchesDeny` uses `includes`/`RegExp.test`, no decoding), so they catch the exact shapes the author wrote — but a plain `/delete` pattern will NOT catch an encoded `/%2564elete` (nor even `/%64elete`) unless the author adds an encoding-aware pattern. Encoded variants stay the author's responsibility until the recursive-decode work below lands.
- The real always-on protection for writes is that every non-GET request fails closed (`[guard:fail-closed]`), so a doubly-encoded **write** is still blocked regardless.
- The residual gap is narrow: a destructive **GET** whose verb is encoded past one decode, against a server that double-decodes, with no author pattern covering the encoded shape.

## Work

In `hasDangerousVerb` / `safeDecode`, decode iteratively (decode until the string no longer changes, with a small fixed cap to bound work) before the verb scan, so multiply-encoded verbs are normalized. Add a `capture-guard-policy.test.mjs` case for `%2564elete` and a doubly-encoded body shape.

## Notes

- Flagged by the v1.0.5 security review as a below-threshold non-finding (defense-in-depth; primary contract is fail-closed on non-GET + author `denyPatterns`). Tracked here so the hardening isn't lost.
