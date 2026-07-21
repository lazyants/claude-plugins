# Claude Code plugin CLI: validation + GC mechanism

Reverse-engineered from the CLI binary + live files. This is a universal mechanism — it applies to any
Claude Code install, not just a specific machine's setup. It explains WHY sharing a `plugins/` directory
across profiles causes churn, and why the durable fix is full per-profile content independence rather than
just de-symlinking the registry file.

## 1. `known_marketplaces.json` is a per-config-dir CACHE, derived from per-profile settings

- Marketplaces are declared **per-profile** in `settings.json` → `extraKnownMarketplaces`. Internally the
  CLI spreads the built-in/seed marketplace list first and a profile's `extraKnownMarketplaces` last, so a
  profile's own declared `source` for a given marketplace name overrides the seed.
- `known_marketplaces.json` is a **cache** the CLI writes under `<CONFIG_DIR>/plugins/`, seeded from that
  profile's settings plus the official/seed marketplaces.
- It's a legitimate pattern for a profile to declare a marketplace as a local `directory` source while
  another profile declares the same marketplace name as `github` — a deliberate per-profile source split,
  not a bug.

## 2. The prefix validation (the root cause of "corrupted installLocation")

On marketplace refresh the CLI validates each cache-managed `installLocation` **string**:

- `path.resolve(installLocation)` must `startsWith` `<CONFIG_DIR>/plugins/marketplaces` — a pure string
  prefix check, **with no symlink resolution**.
- The check runs only for cache-managed sources: `source: "file"` / `"directory"` / seed entries **skip**
  it; `github` / `git` / `url` / `npm` are checked.
- When several profiles' `plugins/` directories (and thus their `known_marketplaces.json`) are actually ONE
  shared inode via whole-directory symlinks, only ONE profile's `<CONFIG_DIR>` prefix can be stored at a
  time. Any `claude plugin` op (or auto-refresh) from a non-owning profile re-stamps the prefix to its own
  `<CONFIG_DIR>`, and every other profile then rejects the entry on next use:
  `Marketplace 'X' has a corrupted installLocation … expected a path inside <CONFIG_DIR>/plugins/marketplaces`.

## 3. Why de-sharing just the registry file is not enough — catalog-scoped GC

Two destructive behaviors are scoped to the CURRENT profile's install catalog (`installed_plugins.json`):

- **Startup cache-GC** marks any `cache/<marketplace>/<plugin>/<version>` **not referenced by the current
  profile's catalog** as orphaned (writes a `.orphaned_at` marker) and deletes it after a grace period.
- **`claude plugin uninstall`** recursively deletes `data/<plugin>`.

With multiple independent catalogs sharing one `cache/` and `data/` directory, each profile's GC or
uninstall can prune or delete another profile's plugins — because from the acting profile's own catalog,
another profile's plugin looks unreferenced. That's why de-symlinking only `known_marketplaces.json` doesn't
fix the underlying problem: the fix needs each profile to have a **fully independent** `plugins/` directory
(its own `marketplaces/`, `cache/`, `data/`, install manifests, catalog, and registry), not just a
per-profile copy of the registry file inside an otherwise-shared directory.

## 4. Other internals worth knowing

- The registry writer is **atomic-rename**. Writing over a top-level **file symlink** FORKS the symlink (it
  writes a new real file at that path rather than following it), so a real per-profile registry file cannot
  coexist inside an otherwise-shared, symlinked `plugins/` directory — another reason the whole directory
  has to become real per-profile, not just the registry file within it.
- The CLI honors an environment variable (observed as `CLAUDE_CODE_PLUGIN_CACHE_DIR`) that points it at an
  alternate shared plugin-cache location — but a shared cache still can't hold per-profile marketplace
  *sources* independently, so redirecting the cache alone doesn't resolve the catalog-scoping problem in §3.
- A "read-only"-looking invocation can MUTATE live state: printing the CLI version or refreshing a
  marketplace has been observed to trigger the startup cache-GC (writing `.orphaned_at` markers, deleting
  after a grace period).
  Avoid running the live CLI against a shared or actively-churning store while diagnosing it; prefer
  inspecting on-disk state statically over invoking the binary mid-diagnosis.

## 5. Hand-editing the registry is a stopgap, not a fix

Editing the `installLocation` string in `known_marketplaces.json` back to the "owning" profile's prefix
(never `marketplace remove`, which orphans already-installed plugins) can clear one instance of the
corrupted-installLocation error. But as long as the underlying `plugins/` directory stays shared across
profiles, the same op from another profile re-triggers it — the edit doesn't change the shared-inode
root cause described in §2. Treat it as a temporary unblock at most; the durable fix is the structural one
described in the main skill file (independent per-profile `plugins/` directories).
