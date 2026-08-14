"""Theme-owned tooling for the unified napari.org search (single source).

The theme is the single source for everything that can be shared across the
napari.org sub-sites:

* the runtime assets — ``static/search/*`` (installer JS, CSS, canonical site
  list ``napari-sites.json``);
* the canonical Pagefind version (pinned in the ``[search]`` extra and read
  back from the installed package);
* the build/inject logic.

Sphinx/pydata sites get all of this automatically from the theme's
``build-finished`` hook (``run_pagefind`` in ``__init__.py``). Non-Sphinx sites
(mystmd workshops, Nikola island-dispatch, hub-lite, ...) that can't run Sphinx
hooks use this module's CLI from their own build system instead::

    python -m napari_sphinx_theme.search prepare --site docs/_build/html \
        --base-url /workshops/

The Pagefind INDEX itself is always per-site — it is built from that site's own
HTML, so it can never be "shipped" from the theme. This module only centralises
the machinery around it, so individual repos don't have to.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import cast

# The runtime assets that make up the unified search, copied verbatim from the
# theme into each site's output (single list — add a new asset in ONE place).
SEARCH_ASSETS = (
    "napari-search-installer.js",
    "napari-search.css",
    "napari-sites.json",
)

ASSET_DIR = pathlib.Path(__file__).parent / "static" / "search"
MOUNT_DEFAULT = ".napari-search"


def pagefind_version() -> str:
    """The canonical Pagefind version (read from the installed package)."""
    try:
        return importlib.metadata.version("pagefind")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_index(
    site_dir: pathlib.Path | str,
    exclude_selectors: tuple[str, ...] = (".headerlink",),
) -> subprocess.CompletedProcess[str] | None:
    """Run the Pagefind index build over ``site_dir``.

    Returns ``None`` if the ``pagefind`` package isn't installed (callers warn
    and degrade gracefully). Uses ``python -m pagefind`` so the binary comes
    from the theme-pinned ``pagefind[bin]`` package — no per-site npx pinning.
    """
    if importlib.util.find_spec("pagefind") is None:
        return None
    cmd = [sys.executable, "-m", "pagefind", "--site", str(site_dir)]
    for selector in exclude_selectors:
        cmd += ["--exclude-selectors", selector]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def copy_assets(dest_dir: pathlib.Path | str) -> list[pathlib.Path]:
    """Copy the unified-search runtime assets into ``dest_dir``."""
    dest = pathlib.Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[pathlib.Path] = []
    for name in SEARCH_ASSETS:
        src = ASSET_DIR / name
        if not src.is_file():
            missing = f"napari-sphinx-theme search asset missing: {src}"
            raise FileNotFoundError(missing)
        target = dest / name
        shutil.copy(src, target)
        copied.append(target)
    return copied


def _installer_snippet(
    base_url: str,
    mount_selector: str,
    mount_class: str,
) -> str:
    classes = mount_selector.lstrip(".")
    if mount_class:
        classes += f" {mount_class.lstrip('.')}"
    # Normalise base_url so it never yields a double slash ("" -> "/x", "/s/" -> "/s/x").
    base = base_url.rstrip("/")
    return (
        f'<div class="{classes}"></div>\n'
        "<script\n"
        f'  src="{base}/_static/search/napari-search-installer.js"\n'
        f'  data-bundle-path="{base}/pagefind/"\n'
        f'  data-mount="{mount_selector}"\n'
        f'  data-placeholder="Search"\n'
        "  defer\n"
        "></script>\n"
        "</body>"
    )


def inject(
    site_dir: pathlib.Path | str,
    base_url: str = "",
    mount_selector: str = MOUNT_DEFAULT,
    mount_class: str = "",
) -> int:
    """Copy assets into the build and mount the search widget on every page.

    For non-Sphinx sites: copies the runtime assets into ``<site>/_static/
    search/`` and injects the mount div + installer script before ``</body>``
    on each HTML page. Host sites hide their own search bar via their theme
    config (e.g. mystmd ``site.options.hide_search``) — never by DOM removal,
    which breaks React-hydrated pages.
    """
    build = pathlib.Path(site_dir)
    copy_assets(build / "_static" / "search")
    snippet = _installer_snippet(base_url, mount_selector, mount_class)
    count = 0
    for html_file in build.rglob("*.html"):
        text = html_file.read_text(encoding="utf-8")
        if mount_selector.lstrip(".") in text and "napari-search-installer.js" in text:
            continue
        if "</body>" not in text:
            continue
        html_file.write_text(text.replace("</body>", snippet, 1), encoding="utf-8")
        count += 1
    return count


def prepare(
    site_dir: pathlib.Path | str,
    base_url: str = "",
    exclude_selectors: tuple[str, ...] = (".headerlink",),
    mount_selector: str = MOUNT_DEFAULT,
    mount_class: str = "",
) -> int:
    """Index + inject: the whole non-Sphinx site flow in one call."""
    result = build_index(site_dir, exclude_selectors)
    if result is None:
        print(
            "napari-sphinx-theme: 'pagefind' package not installed; install the "
            "'search' extra (napari-sphinx-theme[search]).",
            file=sys.stderr,
        )
        return 1
    if result.returncode != 0:
        print(
            f"napari-sphinx-theme: pagefind failed:\n{result.stdout}\n{result.stderr}"
        )
        return result.returncode
    count = inject(site_dir, base_url, mount_selector, mount_class)
    print(
        f"napari-sphinx-theme: built pagefind index and injected into {count} page(s)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m napari_sphinx_theme.search",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser(
        "prepare", help="build the pagefind index and inject the search widget"
    )
    p_prepare.add_argument("--site", required=True, type=pathlib.Path)
    p_prepare.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL", ""),
        help="URL prefix the site is deployed under (default: $BASE_URL)",
    )
    p_prepare.add_argument(
        "--exclude-selectors",
        action="append",
        default=[],
        metavar="SEL",
        help="selectors pagefind should ignore (repeatable; default .headerlink)",
    )
    p_prepare.add_argument(
        "--mount-selector", default=MOUNT_DEFAULT, help="element that hosts the trigger"
    )
    p_prepare.add_argument(
        "--mount-class", default="", help="extra class(es) for the mount div"
    )
    p_prepare.set_defaults(func=prepare)

    args = parser.parse_args(argv)
    exclude = tuple(args.exclude_selectors) or (".headerlink",)
    func = cast(Callable[..., int], args.func)
    return func(
        site_dir=args.site,
        base_url=args.base_url,
        exclude_selectors=exclude,
        mount_selector=args.mount_selector,
        mount_class=args.mount_class,
    )


if __name__ == "__main__":
    raise SystemExit(main())
