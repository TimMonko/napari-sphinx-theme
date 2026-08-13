# Theme owns unified napari.org search

Since the napari docs, workshops, and other napari.org sub-sites should all be
searchable from one search box, the napari-sphinx-theme ships a unified search
built on Pagefind's Component UI. The theme is the single source for the runtime
(the search installer and the canonical merge list of sibling sites) and, when
enabled, runs the Pagefind index build itself (on Sphinx `build-finished`). It
is opt-in: the default search provider remains Sphinx, so no existing theme
consumer changes behavior until it opts in.

## Status

accepted (implemented)

## Considered Options

- **Separate repo / "host once" at a canonical URL** — rejected. A new repo or a
  runtime dependency on the docs deployment for a ~1-file asset is more
  infrastructure than the theme-as-single-source; every Sphinx site already
  consumes the theme, and non-Sphinx sites already copy theme assets at build
  time (e.g. workshops' `copy_theme_css.py`).
- **Runtime-fetched site registry** (a well-known JSON listing all sites) —
  rejected. Adds a runtime dependency and fragile self-identification;
  per-index resilience means a baked-in, generous canonical list already handles
  not-yet-live sites safely.
- **Pagefind as the default provider** — rejected. It would force a build-time
  dependency and a search-behavior change on every consumer. Opt-in plus an
  all-sites canonical merge list achieves org-wide unification without the blast
  radius: sites "light up" the moment they opt in.
- **Build on Pagefind's legacy Default UI** (`PagefindUI`) — rejected. Deprecated
  upstream in favor of the Component UI (modal, `<pagefind-config>`), which also
  solves the old "two search UIs" problem with built-in keyboard handling.

## Consequences

The theme now owns search build behavior, and this carries real limitations that
must not be "fixed" away:

- **Sphinx-only coverage.** The `build-finished` step only helps Sphinx sites.
  Non-Sphinx sites (workshops/mystmd, island-dispatch/Nikola, hub-lite) still run
  their own Pagefind step and copy the installer at build time.
- **Indexes only what Sphinx emitted.** Content generated *after* Sphinx finishes
  (post-processing scripts) is missed unless the step runs last; sites with
  post-Sphinx generation must verify ordering.
- **Build-time dependency + subprocess.** Opted-in sites require the `pagefind`
  pip package (binary download) and run a subprocess on every build. If the
  binary is missing or a platform lacks it, the theme should warn and fall back
  to Sphinx search rather than fail the build.
- **Escape hatch required.** Power users with custom Pagefind needs (filters,
  sort, `pagefind_extended`, custom excludes) must be able to override or disable
  the auto-step (`search = "sphinx"`).
- **Version bumps ride theme releases.** The canonical Pagefind version is pinned
  by the theme (single source), so upgrades are org-coordinated events; cross-site
  merge skew during staggered upgrades is still possible.
- **Old Sphinx search must be neutralized** on opted-in sites, or users hit two
  different searches. We keep pydata-sphinx-theme's own search button markup
  (so the navbar visual stays on-brand) and *hook* it to open the Pagefind modal
  (`data-hook-button=".search-button__button"`), drop `#pst-search-dialog`, and
  add a capture-phase Ctrl/Cmd+K handler. pydata's dialog opener is null-guarded
  (`t && ...`), so once the dialog is removed its leftover handlers become
  silent no-ops — verified: no console errors. Non-Sphinx sites (no pydata
  button) instead inject a `pagefind-modal-trigger` into a mount point
  (`data-mount`, e.g. workshops' floating button).
- **Merge-list network effect.** Pagefind's worker does *not* survive a missing
  merged bundle: it fetches each merged `pagefind-entry.json` and throws
  "Failed to load Pagefind metadata" (the search hangs on "Searching for...")
  if one returns a 404 HTML page. The installer therefore probes every
  candidate bundle up front (AbortController timeout, valid-JSON check) and
  drops unreachable ones *per index*, so non-opted-in or broken sibling sites
  are skipped rather than fatal — and local/dev servers, where no sibling
  bundles exist, fall back to searching the current site only.
- **Reconcile existing CI steps** — sites that already run Pagefind in CI must
  remove their step to avoid double indexing.

## Implementation notes

- The installer (`static/search/napari-search-installer.js`) supports exactly
  one trigger mode per page: `data-hook-button` (hook an existing themed search
  button, e.g. pydata's `.search-button__button`) or `data-mount` (inject a
  `pagefind-modal-trigger` into each matching element). Sphinx/pydata sites use
  the former; non-Sphinx sites use the latter.
- The canonical merge list (`static/search/napari-sites.json`) lists every
  napari.org sub-site. `siteRoot()` (derived from `data-bundle-path`) excludes
  the current site from its own merge; the probe then drops anything not yet
  live.
- Run `scripts/verify_search.py --dir <build>` for a browser-driven end-to-end
  check that a set of queries actually return results through the real modal
  (and that a dead merge bundle no longer hangs it); `--sanity` checks the
  bundle without a browser.
