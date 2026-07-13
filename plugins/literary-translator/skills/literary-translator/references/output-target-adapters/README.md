# Output-target adapters

`profile.yml`'s `output.target` is always explicit, never sniffed, and is
only ever consulted when `output.v1_scope: assembled_book` (Step 0d, see
`SKILL.md` and `references/assembly-and-output.md`) — under the default
`segment_drafts_and_audit` scope, nothing in this directory is read at all.
v1 ships exactly **three** targets — no more, no generic renderer-plugin
framework sitting above them. That is a deliberate design decision (see "Why
only three" below), not an oversight.

| `output.target` | Adapter doc | Renders to | Status |
|---|---|---|---|
| `obsidian` | [`obsidian.md`](./obsidian.md) | An Obsidian vault (many markdown files) | **Shipped, PRIMARY.** The only working target this increment. |
| `epub` | *(not yet written)* | A single EPUB file | **Not yet shipped.** Step 0d resolves the name; there is no renderer behind it yet — see `references/assembly-and-output.md`'s "Why `build_epub.py` hasn't been generalized." |
| `custom` | *(co-designed per project)* | Whatever the co-designed renderer produces | **Always experimental/co-designed** — parsing/rendering logic is per-project by design, the output contract below is fixed. |

## The shared output contract

Regardless of which target renders, `scripts/assemble.py` builds one
NodeStream (the whole-book reading order, sentinels still unresolved in the
text — see `references/assembly-and-output.md` for its exact shape) and
hands it to whichever adapter `output.target` resolves to. Every built-in
adapter module exposes the same entry point:

```python
def render(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict:
    """Writes the artifact(s) under out_dir. Returns a small manifest
    { "written": [relative_path, ...], "kind": "vault"|"file" } for the diff tool."""
```

The adapter, not the assembler, does the final substitution: a verse
placeholder becomes rendered verse, `⟦FNREF_N⟧` becomes this target's own
footnote-ref syntax, footnote definitions are appended wherever this target
conventionally puts them. This is exactly why the NodeStream stays
sentinel-in-text rather than pre-rendered — it lets `obsidian` and `epub`
diverge only at render time, never in how the book itself is reconstructed.
`kind: "vault"` (many files, e.g. `obsidian`) vs. `kind: "file"` (one file,
e.g. `epub`) tells `scripts/diff_rendered_output.py` how to reduce the
render for comparison — see `references/assembly-and-output.md`'s
render+diff section.

### Resolving `output.target` (`output_resolve.py`)

Mirrors `cache_key.py`'s `resolve_extractor_path` on the source side,
retargeted to the output side:

- `target: obsidian` → the flat sibling module `render_obsidian`.
- `target: epub` → the flat sibling module name `render_epub` (resolves
  today; the module itself is a later phase — see the table above).
- `target: custom` → a path-safe resolved `Path`, via the trio below.
- Anything else → FATAL, naming the unrecognized value. There is no default
  fallthrough — the dispatch is exhaustive over exactly these three values.

**`custom` path-safety**, from `output.adapter_config.custom.renderer_path`:

1. `null`/falsy → **HALT** for co-design (a sentinel, not an error to
   swallow — mirrors the source side's `extractor_path: null` HALT at Step
   0c).
2. A **positive allow-list**, `^[A-Za-z0-9._/-]+$`, plus rejection of any
   `..` path segment, a leading `/`, or a non-string value — checked
   *before* any path join. A denylist alone is not sufficient here (it would
   still pass shell metacharacters or encoded traversal sequences a
   blocklist didn't anticipate).
3. Join under the fixed subtree
   `${durable_root}/scripts/custom_renderers/<value>`, confirm the resolved
   path stays contained under that subtree, then let a plain
   read/existence check fail loudly if the file isn't there.

## Why only three, no generic framework

Same rationale as the source side's
[`../source-format-adapters/README.md`](../source-format-adapters/README.md#why-only-three-no-generic-framework):
`custom`'s rendering logic stays deliberately undocumented and co-designed
per project — a truly custom output target can't be pre-documented — but
its **output contract is fixed, mandatory**: the same `render(...)` entry
point signature, writing under the given `out_dir`, returning the same
small manifest, exactly like `obsidian`/`epub`. There is no generic
renderer-plugin framework behind these three, and none is planned: building
one now would be premature abstraction over a sample size of one working
adapter (`obsidian` is, for now, the only real, specified rendering strategy
this plugin has ever had to generalize from).

One asymmetry worth naming explicitly, since it's easy to assume the output
side mirrors the source side exactly and it doesn't: on the *source* side,
`gutenberg_epub` **uses** one `extract.py.template`, with adapter-specific
logic living only in marked `# ADAPT-POINT:` sections — `plain_text` is
specified to share that same template once implemented (#62), but is not
wired in yet. On the *output* side, `obsidian` and `epub` are **distinct,
standalone renderer
modules** with no shared template — a vault and a single EPUB file have
different-enough structure (many files with frontmatter/backlinks vs. one
packaged archive) that forcing them through one parameterized template
would be the premature abstraction this section is arguing against, not a
simplification.

## See also

- [`obsidian.md`](./obsidian.md) — the shipped adapter: vault layout,
  entity-note frontmatter, the wikilink rule, backlinks-as-index, the
  category→folder catalog.
- [`../assembly-and-output.md`](../assembly-and-output.md) — the two
  `output.v1_scope` paths, Step 0d, W9 Assemble, the reconstruction
  algorithm, the NodeStream/anchor-map artifacts, and the render+diff
  acceptance gate in full.
- [`../source-format-adapters/README.md`](../source-format-adapters/README.md)
  — the mirror-image decision on the input side, including the "two senses
  of proven" framing this directory does not need (there is only one
  working output target so far, so there is no second-adapter proven/unproven
  distinction to draw yet).
- `SKILL.md`, Step 0d — the orchestrating-session procedure that resolves
  `output.target` to this directory and runs the two `custom` procedural
  checks described above.
