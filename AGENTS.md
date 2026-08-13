# napari-sphinx-theme — AI Coding Agent Instructions

A Sphinx theme with the look and feel of napari, based on
[pydata-sphinx-theme](https://github.com/pydata/pydata-sphinx-theme).

## Quick reference

### Environment

```sh
uv venv -p 3.13
uv pip install -e ".[dev]"
```

Or with uv sync:

```sh
uv sync
```

### Build demo docs and preview

```sh
uv run make docs                    # build demo site
uv run make docs-live               # live preview (auto-rebuild on changes)
```

### Build distribution wheel

```sh
uv run make build
```

### Code quality

```sh
ruff check . --fix
ruff format .
```

## Project structure

```
napari_sphinx_theme/
    __init__.py             # Theme registration
    _version.py             # Version (setuptools_scm)
    theme.conf              # Theme configuration (extends pydata-sphinx-theme)
    napari_code_theme.py    # Pygments theme for code highlighting
    layout.html             # Top-level page layout template
    footer.html             # Footer template
    partials/               # Reusable HTML partials
    sections/               # Section templates
    static/
        css/
            napari-sphinx-theme.css  # Custom CSS
```

## Key conventions

- **Extends pydata-sphinx-theme**: All pydata-sphinx-theme configuration options work here. See [pydata-sphinx-theme docs](https://pydata-sphinx-theme.readthedocs.io/).
- **CSS customization**: Edit `static/css/napari-sphinx-theme.css` for styling changes.
- **Code highlighting**: Configured in `napari_code_theme.py` (custom Pygments style).
- **HTML templates**: Page layout templates in `napari_sphinx_theme/` and `napari_sphinx_theme/partials/`.
- **Theme config**: `theme.conf` controls theme options passed to pydata-sphinx-theme.
- **Live reload**: `make docs-live` watches both `docs/` and `napari_sphinx_theme/` — CSS, templates, and config changes display without restart.
- **Used by**: The [napari docs site](https://napari.org) uses this theme.

## Search (unified napari.org search)

The theme ships a unified, cross-sub-site search built on Pagefind's Component
UI. Full design record and limitations: `docs/adr/0001-theme-owns-unified-search.md`.
Domain vocabulary: `CONTEXT.md`.

- **Opt-in only**: default search provider is Sphinx; a site enables Pagefind with
  `html_theme_options['search'] = 'pagefind'`. Never change the default.
- **Theme owns the build step**: when enabled, a `build-finished` hook runs
  `python -m pagefind` over the Sphinx output. Sites must NOT run pagefind in CI
  too (double indexing).
- **Canonical merge list**: `static/napari-sites.json` lists sibling napari.org
  sites' pagefind bundles; the installer merges it with per-index resilience
  (dead bundles are dropped, not fatal). Add new sites there, not per-site.
- **Two trigger modes in the installer**: Sphinx/pydata sites keep pydata's own
  search button and hook it to open the modal (`data-hook-button`); non-Sphinx
  sites inject a `pagefind-modal-trigger` into a mount point (`data-mount`).
  Both modes resolve the merge list *before* configuring the instance, and the
  per-index resilience is implemented by probing each candidate bundle's
  `pagefind-entry.json` (pagefind itself throws "Failed to load Pagefind
  metadata" and hangs if a merged bundle 404s).
- **Canonical pagefind version**: pinned as a dependency of the theme — bumps are
  org-coordinated and ride theme releases.
- **Verification**: `scripts/verify_search.py --dir <build>` drives the real
  modal in a headless browser (system Chrome/Edge) and asserts a set of queries
  return results — it catches the merge-hang failure class; `--sanity` checks
  the bundle without a browser.

## Links

- [napari docs repo](https://github.com/napari/docs) — the main docs site using this theme
- [pydata-sphinx-theme docs](https://pydata-sphinx-theme.readthedocs.io/)
