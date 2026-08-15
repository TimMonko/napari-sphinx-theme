# Unified napari.org search (Pagefind)

One search box on every napari.org sub-site, backed by [Pagefind](https://pagefind.app)'s
Component UI, searching across all sub-sites at once from a single modal.

## Architecture

The **theme owns everything that can be shared**; each site owns only its
content and its own index:

```
napari-sphinx-theme (this repo) — single source of truth
├── static/search/napari-search-installer.js   runtime: mounts the modal, wires
│                                               triggers, merges sibling bundles
├── static/search/napari-search.css            napari theming for the modal
├── static/search/napari-sites.json            canonical list of sibling sites to merge
├── search.py                                  theme-owned CLI for non-Sphinx sites
└── __init__.py                                Sphinx build-finished hook (index + inject)

Each site
├── its own pagefind/ index  (built from its own HTML — per-site, never shared)
└── its own build step that produces the index and injects the installer
```

Two halves to keep straight:

1. **The index is per-site.** Pagefind indexes a site's own HTML into
   `<site>/pagefind/`. It can never be "shipped" from the theme — every site
   builds its own.
2. **The merge is client-side.** The installer reads the canonical merge list and
   points one Pagefind instance at the current site *plus* every sibling site's
   bundle. Results from all sites appear in one modal.

## Opting in

### Sphinx / pydata sites (e.g. napari docs)

Set the theme option and the theme does everything — it builds the index on
`build-finished` and injects the installer:

```python
html_theme_options = { 'search': 'pagefind', ... }
```

Also install the theme's `[search]` extra so the Pagefind binary is present:

```
napari-sphinx-theme[search]
```

### Non-Sphinx sites (mystmd workshops, Nikola island-dispatch, hub-lite, …)

Run the theme's CLI after your own build:

```sh
python -m napari_sphinx_theme.search prepare \
    --site docs/_build/html \
    --hook-button .myst-search-bar \
    --base-url /workshops/        # default: $BASE_URL
```

This builds the index, copies the runtime assets into `<site>/_static/search/`,
and injects the installer script. `--base-url` matters for sites deployed under
a subpath (Pagefind infers bundle URLs from it at runtime).

## How it works at runtime

The installer script (injected before `</body>`) does, in order:

1. Loads `napari-search.css` (theming).
2. Resolves the **merge list** from `napari-sites.json` (or `data-merge-paths`),
   excluding the current site.
3. **Probes every candidate bundle**: fetches each one's `pagefind-entry.json`
   (4s timeout, must parse as JSON) and drops any that fail. Pagefind itself
   throws/hangs on a dead bundle, so dropping them up front is what lets sites
   that haven't opted in yet stay on the list without breaking anyone.
4. Loads the Component UI from the current site's own bundle and calls
   `configureInstance('default', { bundlePath, mergeIndex })` — the official
   Pagefind multisite pattern (see <https://pagefind.app/docs/multisite/>).
5. Wires the trigger in one of two modes:
   - **`data-hook-button=<selector>`** (recommended) — keeps the host's own
     search button (pydata's `.search-button__button`, myst's
     `.myst-search-bar`) and opens the modal on its click via a document-level
     capture handler. Never removes host DOM (that breaks React-hydrated sites).
   - **`data-mount=<selector>`** — injects a `pagefind-modal-trigger` into each
     matching element (for hosts with no search button).
6. Binds Ctrl+K (and stops the host's own Ctrl+K from opening a second search).

### Site attribution in results

Merged results are one combined list. Pagefind renders each result as title +
excerpt; the **URL (in the link) is what identifies the source site**
(e.g. `/stable/…`, `/workshops/…`). There is deliberately no per-site badge —
it would need a custom result template plus a URL→site map to maintain, and the
URL is enough.

## Theming & inheritance

The header comment in `static/search/napari-search.css` documents the full
mechanism. In short:

- Pagefind's Component UI exposes `--pf-*` custom properties as the *only*
  theming lever.
- `napari-search.css` sets those on `:root`, referencing the theme's `--napari-*`
  tokens (and pydata's `--pst-*` tokens where present).
- Custom properties inherit down the DOM **and through shadow DOM**, and `var()`
  resolves **lazily at the point of use** — so `--pf-text: var(--napari-color-text-base)`
  follows the host's light/dark theme automatically, because the theme defines
  that token on `<html>` per mode.
- Dark mode matches both host conventions: `html[data-theme='dark']` (pydata)
  and `html.dark` (mystmd).
- **Load order matters.** The installer loads pagefind's
  `pagefind-component-ui.css` FIRST and our `napari-search.css` LAST: both
  declare `--pf-*` on `:root`, and at equal specificity the later stylesheet
  wins. If ours loads first, pagefind's `:root` defaults (loaded second)
  silently beat our light theme — dark mode still worked (our
  `html[data-theme='dark'], html.dark` selector out-ranks `:root`), but light
  mode fell back to pagefind's near-identical-but-not-theme-driven palette.
  Keep the order as-is.

To restyle the modal, edit `--pf-*` in `napari-search.css`. To change the
palette, change the `--napari-*` tokens in `static/css/napari-sphinx-theme.css`
and the modal follows.

## Testing locally

### Automated (theme repo)

`scripts/verify_search.py` drives the real modal in a headless browser:

Commands are written as single lines so they run unchanged in both PowerShell
and bash (for multi-line, PowerShell uses a trailing backtick, bash a trailing
backslash):

```sh
# Browser-free: is the pagefind bundle sane?
uv run --no-project python scripts/verify_search.py --sanity --dir <build>

# Full check: serve a build, open the modal, assert queries return results
uv run --with playwright python scripts/verify_search.py --dir C:/path/to/_build/html --queries segmentation plugin viewer

# Cross-site, browser-free (stdlib only — runs from PowerShell and WSL with
# uv, no playwright/browser needed): proves the builds are CONNECTED for the
# merge — each bundle well-formed, listed in the merge list, and reachable at
# its canonical path.
uv run --no-project python scripts/verify_search.py --sites docs=../napari-docs/docs/_build/html workshops=../napari-workshops/docs/_build/html --from-site docs --sanity

# Cross-site with a real browser: reports actual merged result COUNTS by site
# (e.g. `thebe` is workshops-only, `watershed` appears in both docs and
# workshops). Needs playwright + a browser (see note below).
uv run --with playwright python scripts/verify_search.py --sites docs=../napari-docs/docs/_build/html workshops=../napari-workshops/docs/_build/html --from-site docs --queries thebe watershed

# Against an already-running server
uv run --with playwright python scripts/verify_search.py --url http://127.0.0.1:3001 --queries plugin
```

`--sanity` needs no browser and no Playwright — stdlib only, so it runs with a
bare `python` (or `uv run --no-project python`) from PowerShell or WSL. With
`--dir` it checks one build's bundle; with `--sites` it also proves cross-site
connectivity (merge-list membership + bundle reachability at canonical paths) —
but it cannot report actual result counts, which need pagefind's WASM in a
browser. The browser modes use Playwright: it auto-detects a system Chrome/Edge
(no `playwright install` download needed) and falls back to Playwright's
bundled Chromium. In a
container or CI without a system browser, install the bundled browser + its
system libraries once first, otherwise the launch fails with something like
`libnspr4.so: cannot open shared object file`:

```sh
uv run --with playwright python -m playwright install --with-deps chromium
```

(`--with-deps` runs apt to install the system libs; `--with playwright` keeps
the package out of your project env.) On a machine with Chrome/Edge installed,
nothing is needed — the script finds it automatically.

### Manual smoke test

1. Build a search-enabled site (e.g. `pixi run docs-build` in napari-workshops).
2. Serve it: `python -m pagefind --site docs/_build/html --serve`
   (re-indexes + serves) — or any static server pointed at the build dir.
3. Open it and check:
   - the host search button (or trigger) opens the modal, and the host's own
     search UI does **not** also open;
   - Ctrl+K opens the modal (exactly once);
   - typing returns results;
   - toggling dark mode re-themes the modal;
   - devtools console has no errors. A few 404s for sibling bundles you haven't
     built locally is expected — they're probed and dropped.

### Gotchas

- `pagefind[bin]==1.5.2` must be installed — the plain `pagefind` package has no
  binary. The version is pinned by the theme's `[search]` extra; keep it
  org-wide, since merged bundles must match.
- mystmd's dev server (`jupyter-book start`) deletes `_build/html` on every
  rebuild, so search only survives in the `docs-build` output — not in the dev
  server. Preview search from the built output.
- Playwright against a server with an SSE `/__reload` connection must use
  `waitUntil: 'domcontentloaded'` (networkidle never fires).

## Versioning & rollout

- The Pagefind version is a theme dependency — bumps are org-coordinated and
  ride theme releases.
- Release order: theme release → docs re-lock pixi → workshops full build.
  Older per-site search widgets are superseded by this.
