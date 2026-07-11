# Profile validation — the normative contract

This is the normative procedure for Step 0 of `SKILL.md`: reading `.claude/handbook/profile.yml`,
checking its `profile_version`, and validating its structure. `assets/profile.schema.json` is
**normative data** (the machine-readable contract itself). `assets/lib/profile-version.mjs` and its
declaration file `assets/lib/profile-version.d.mts` are a **non-normative reference implementation**
of the `profile_version` scan described below — the normative gate is Claude *reading* the profile
against this document, not requiring that file to run.

## Supported profile_version

| `profile_version` | Status | Notes |
| --- | --- | --- |
| `1` | current | the shape documented by `assets/handbook.profile.example.yml` |

### Migration table

| From | To | Instructions |
| --- | --- | --- |
| *(none)* | | |

There is **no cross-version migration** yet — only `profile_version: 1` has ever shipped. This table
exists as the extension point for the day a `2` ships; do not fabricate an entry for a version that
does not exist. `assets/lib/profile-version.mjs` mirrors this table in its exported, empty
`MIGRATIONS` object.

## The `profile_version` pre-flight scan

`profile_version` is read by a column-0, top-level-key line-scan — not a general YAML parser. Node
has no stdlib YAML parser and this plugin ships zero runtime dependencies, so the scan is deliberately
narrow: it recognizes exactly **one** YAML shape (a top-level block mapping whose first key is
`profile_version`, an unquoted integer, on one line) and halts on everything else, including YAML that
a real parser would accept but read differently than a naive scan would guess.

Whitespace is **ASCII space and tab only**, everywhere in the scan — never JavaScript's `\s` or
`String#trim()`. Both of those treat U+00A0 (NBSP), U+FEFF (BOM) and U+3000 (ideographic space) as
whitespace; YAML's whitespace is space and tab only. Using the JS-native classes would make the scan
disagree with a real YAML parser about where a value ends.

**A tab anywhere in a line's block-indentation run halts the whole document — checked over every
line, not just column-0 ones.** YAML forbids a tab in block indentation; a document containing one
cannot be parsed at all (a real parser raises a syntax error), no matter how far from
`profile_version` the tab sits. The column-0 shape allowlist above only inspects the *first*
character of each line, so a forbidden tab several levels deep — e.g. under a key that comes
*after* `profile_version` — would otherwise go unnoticed and the scan would wrongly report `ok` for
a document a real parser cannot load. This rule is intentionally narrow: it matches a tab only
inside the *leading* whitespace run of a line, so `profile_version:\t1` (a tab as the colon→value
separator, legal YAML) is unaffected — only a tab that is itself indentation halts.

> `readProfileVersion` is a **pre-flight version reader, not a YAML validator**. It returns `ok, N`
> only for a file in which every column-0 line is blank, a comment, or a snake_case top-level key;
> whose first top-level key is `profile_version`; which contains exactly one such key; whose value is
> an unquoted integer on the same line with no continuation; and `N ∈ SUPPORTED_PROFILE_VERSIONS`.
> Every other input returns a halting status. It does not claim to detect every invalid YAML document,
> only to never return `ok` for one whose `profile_version` a real parser would read differently.

**Cross-line structural validation (v1.3.0) — mechanisms A and C.** Beyond the column-0 top-level
*shape* documented above, the scan additionally performs two narrow cross-line checks, both
**provably false-reject-free** by construction: a single forward pass classifies only inline
node-starts (a `[`/`{`/`"`/`'`/`*` that opens immediately after a real key or sequence introducer);
anything ambiguous — a folded plain-scalar continuation, block-scalar content, a mid-token quote —
resolves to *opaque* and is never flagged. That asymmetry is deliberate: a missed error is acceptable,
a false halt on a document a real parser would load is not.

- **Mechanism A — unterminated flow collection or quoted scalar.** An inline `[`/`{` that never
  closes, or an inline `"`/`'` whose closing quote never arrives before EOF, halts as malformed.
- **Mechanism C — alias to an undefined anchor.** An inline `*alias` halts as malformed, but only when
  the document contains **no anchor definition**. "No anchor definition" is approximated
  conservatively: the check backs off if a `&` appears anywhere it could legally *start* an anchor —
  start-of-token, i.e. preceded by whitespace, a flow indicator, or line-start. This over-approximation
  is deliberate and keeps the check false-reject-free: it never treats a real `&anchor` as absent, so
  it never flags an alias a real parser would resolve. The trade-off is an accepted false-negative — a
  `&` surrounded by spaces in prose (`you & me`) still backs the check off, but a mid-word `&` (`R&D`,
  `AT&T`) does not.

**Deferred — mechanism B (invalid dedent).** A block that dedents to a structurally invalid level —
reachable, for instance, through a hand-edit of the multi-line `capture.command: |` block scalar — is
not detected. Block scalars are treated as pure opacity: their content is never classified and never
flagged, by deliberate design. Modeling block-scalar-content lines (including one that happens to
start with `#`) together with general indentation tracking reintroduces the exact mini-YAML-parser
mis-parse risk this document warns against elsewhere — an indentation guard, for one, would
false-reject the *valid* shipped `capture.command: |` block scalar. A properly false-reject-free
design for B is tracked as a follow-up rather than rushed into this release.

**Honest residual.** These still scan `ok` for a document that is actually invalid YAML: an own-line
(non-inline) value node reached through a shape the classifier does not enumerate; general and block
dedent (mechanism B, above); and alias resolution once a document contains any `&` at a legal
anchor-start position (mechanism C's anchor-gate approximation only protects a document with no such
`&` at all — see mechanism C above). Separately, and in the other direction, a pre-existing
false-reject halts a *valid* explicit-indent block scalar whose content happens to be
tab-indented, tripping the document-wide tab check meant for structural indentation — not new in this
release, and tracked as its own follow-up. Likewise, a document that uses a YAML **document marker**
(`---` document-start or `...` document-end) at column 0 — e.g. a profile that simply opens with a
leading `---`, or ends with a trailing `---`/`...` — **or another unusual column-0 construct, such as
the explicit-key `?`/`:` block-mapping indicator** — is rejected by the Step-4 top-level-shape
allowlist ("not a top-level key"), even though a real parser loads such a document and reads
`profile_version` from it fine. These predate the #110 guard (they live in the Step-4 allowlist, not
in `scanStructure`) and are deliberately left alone rather than patched here. For the document marker
specifically, rejecting a `---` *separator* is protective, not just a gap — a genuine multi-document
stream must still halt rather than risk reading the wrong document's version (a real parser's
single-document load returns the *first* document — `2`, not `1`, for a stream of `---`,
`profile_version: 2`, `---`, `profile_version: 1` in that order); a proper fix has to distinguish a
lone leading/trailing marker from a real multi-document stream first. The explicit-key case carries no
such tension — it is simply an unimplemented shape, tracked the same way. Each of these is a real,
acknowledged gap, not silently assumed fixed by the A/C work above.

This is still the deliberate edge of the invariant above — a *reader*, not a validator. It never
returns the wrong *version* (a real parser does not read a *different* `profile_version` from these
files — it fails to parse at all), and unlike a wrong version the error fails **visibly**: a real YAML
read at profile-load time raises with a line number.

**Threat model**, stated once so nobody mistakes this for a security boundary: the profile is a
trusted, first-party artifact living in the project's own `.claude/handbook/`, authored from the
shipped example or by `/scaffold-profile`. The failure this guards against is **author error**, not an
attacker. Anyone able to write `profile.yml` can already write anything the skill will act on.

## Structural validation against `assets/profile.schema.json`

`assets/profile.schema.json` is a JSON-Schema (draft 2020-12) pinning the profile's shape: all nine
top-level keys, their types, and their closed enums (`stack.backend.type`, `stack.frontend.type`,
`stack.surface`, `capture.engine`, `publish.target`, `diataxis.quadrants_in_use`). There is no
dep-free YAML parser to *run* this schema against the profile, so it is applied by Claude reading
the profile alongside the schema at Step 0 — the schema is the contract both a human and Claude read,
not code that executes.

The root object is deliberately **open** (`additionalProperties: true`): an unrecognized top-level key
degrades to a warning, never a hard reject. This keeps a future package (or a project's own local
extension) from being blocked by a schema that has not caught up yet. The **only** closed object is
`style_guide.inline` (`additionalProperties: false`) — see "`inline` stays minimal", below.

Disposition, in order of severity:

- **An unknown top-level key, or an unknown key inside `style_guide.inline`** — a one-line warning
  naming the key; continue. This preserves the pre-1.2.0 `SKILL.md` contract.
- **A required field absent from a known object** (e.g. `glossary` missing entirely, or
  `stack.backend` present without `type`) — a genuine structural defect. Halt, naming the field.
- **A wrong enum or type at a known key** (e.g. `stack.backend.type: symfony`, `publish.wikilinks: "yes"`)
  — halt, naming the field, its expected shape, and its actual value.

## `style_guide` resolution

`style_guide.source`, when set, is verified to exist by `SKILL.md` Step 0a (unchanged by this
package) — a missing file halts there, before this procedure runs.

`style_guide.inline` is a **minimal fallback only**, closed to exactly four fields:
`sentence_style`, `address_form_examples`, `ui_label_rule`, `do_donts`. A real project's tone-of-voice
covers roughly ten dimensions (address form, sentence style, UI labels, headings, screenshot/alt-text
conventions, Diátaxis framing, terminology, numbers, do/don't examples); `inline` **stays minimal** by
design rather than growing to duplicate `style_guide.source` — richer tone-of-voice belongs in a real
style guide file, not in the profile. When `source` is `null`, the procedure emits a **warning** (not
a halt): an inline-only project is flagged, not rejected.

## Optional `node` helper

`assets/lib/profile-version.mjs` is an **optional** determinism aid for node-present / authoring
contexts (e.g. `/scaffold-profile`), invoked as:

```
node ${plugin_path}/skills/enduser-handbook/assets/lib/profile-version.mjs .claude/handbook/profile.yml
```

— the same `${plugin_path}` anchor `SKILL.md` Step 0 already uses for the missing-file halt. It is
**not** required: the normative Step 0 gate is Claude reading the profile per this document, so a
node-less backend (`django | rails | spring`) is never blocked on `node` being absent. When used, it
exits `0` for an `ok` verdict, `1` for a halting verdict (`unsupported` / `missing` / `duplicate` /
`malformed`), and `2` for a usage or IO error (missing argument, unreadable path) — never a stack
trace.

## Step 0 — ordered checks

These run after `SKILL.md`'s existing file-existence halt (Step 0, first bullet — unchanged: *"Missing
`.claude/handbook/profile.yml`. Copy the example at
`${plugin_path}/skills/enduser-handbook/assets/handbook.profile.example.yml`, edit values for this
project, and re-run."*).

1. **`profile_version` missing** — halt:
   `profile_version is missing from .claude/handbook/profile.yml. Add profile_version: 1 as the first top-level key. Supported: 1.`
2. **`profile_version` duplicated** — halt:
   `.claude/handbook/profile.yml declares profile_version more than once (lines <N>, <M>). Keep exactly one, as the first top-level key.`
3. **`profile_version` malformed** (not a top-level key, quoted, non-integer, multi-line, disallowed
   line shape, a tab used for block indentation anywhere in the document, an unterminated flow
   collection or quoted scalar anywhere in the document, or — in a document with no `&` at a legal
   anchor-start position anywhere — an alias to an undefined anchor; see "Cross-line structural
   validation" above for what these two structural checks do and do not catch. They fire after the
   missing/duplicate checks above, so a document that is both missing/duplicated and structurally
   invalid halts on the earlier reason) — halt:
   `.claude/handbook/profile.yml's profile_version could not be read (<reason>). Write it as the first top-level key, an unquoted integer, with no continuation line, e.g. profile_version: 1.`
4. **`profile_version` unsupported** — halt:
   `Unsupported profile_version: <N>. Supported: 1. No cross-version migration exists yet — see the table above.`
5. **Unknown top-level key, or unknown `style_guide.inline` key** — warn, continue:
   `Unknown profile key '<key>' (ignored). See assets/profile.schema.json for the recognized shape.`
6. **Required field missing at a known object** — halt:
   `Missing required profile field '<path>'. See assets/profile.schema.json and assets/handbook.profile.example.yml.`
7. **Wrong enum or type at a known key** — halt:
   `Invalid value at '<path>': expected <expected>, got <actual>. See assets/profile.schema.json.`

Every halt in this list is **actionable non-interactively** — /loop and scheduled runs get a specific,
copy-pasteable reason rather than a silent skip or a generic failure.
