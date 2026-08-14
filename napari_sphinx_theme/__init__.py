from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from docutils.nodes import Node
from sphinx.util import logging

from .napari_code_theme import NapariCodeTheme

if TYPE_CHECKING:
    from sphinx.application import Sphinx

logger = logging.getLogger(__name__)

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "not-installed"

__all__ = ["NapariCodeTheme", "__version__", "get_html_theme_path", "setup"]

TEMPLATE_SECTIONS: list[str] = [
    "theme_navbar_start",
    "theme_navbar_center",
    "theme_navbar_end",
    "theme_footer_items",
    "theme_page_sidebar_items",
    "theme_left_sidebar_end",
    "sidebars",
]


def setup_html_template_context(
    app: Sphinx,
    pagename: str,
    templatename: str,
    context: dict[str, Any],
    doctree: Node,
) -> None:
    """Update template names for page build and setup html context."""

    for section in TEMPLATE_SECTIONS:
        section_value = context.get(section)
        if section_value:
            # Break apart `,` separated strings so we can use , in the defaults
            if isinstance(section_value, str):
                context[section] = [ii.strip() for ii in section_value.split(",")]

            # Add `.html` to templates with no suffix
            section_value = context.get(section)
            if isinstance(section_value, list):
                for ii, template in enumerate(section_value):
                    if not Path(template).suffix:
                        context[section][ii] = template + ".html"


def _theme_options(app: Sphinx) -> dict[str, Any] | None:
    """Return the builder's theme options, or ``None`` if not available.

    ``Builder`` doesn't declare ``theme_options`` in its type, so Sphinx's
    HTMLBuilder subclasses are the ones that expose it — guard at runtime.
    """
    try:
        return app.builder.theme_options  # type: ignore[attr-defined, no-any-return]
    except AttributeError:
        return None


def set_config_defaults(app: Sphinx) -> None:
    """Set default config values for theme."""
    theme: dict[str, Any] | None = _theme_options(app)
    if not theme:
        theme = {}

    # Note: If there are custom options for the theme that you would like to set,
    #       do so here. For example:
    #       theme['logo'] = logo.svg

    # Update the HTML theme config
    app.builder.theme_options = theme  # type: ignore[attr-defined]


def _search_enabled(app: Sphinx) -> bool:
    """Whether the site opted into the unified Pagefind search."""
    options = _theme_options(app)
    return bool(options and options.get("search") == "pagefind")


def run_pagefind(app: Sphinx, exception: BaseException | None) -> None:
    """Build the pagefind search index for opted-in sites.

    The theme owns the pagefind build step (see docs/adr/0001-theme-owns-
    unified-search.md). It runs after every HTML build when
    ``search = "pagefind"``, so adopting sites must NOT run pagefind in CI
    themselves (double indexing). If pagefind is missing or fails, warn loudly
    and continue — the search UI degrades gracefully in the browser.
    """
    if exception is not None or app.builder.format != "html":
        return
    if not _search_enabled(app):
        return
    # Imported lazily so `python -m napari_sphinx_theme.search` (and plain
    # package imports) don't double-import it and warn.
    from . import search

    result = search.build_index(app.outdir, exclude_selectors=(".headerlink",))
    if result is None:
        logger.warning(
            "napari-sphinx-theme: search='pagefind' but the 'pagefind' package "
            "is not installed; install the 'search' extra or run pagefind "
            "yourself, or the site's search will not work."
        )
        return
    logger.info("napari-sphinx-theme: building pagefind search index...")
    if result.returncode != 0:
        logger.warning(
            "napari-sphinx-theme: pagefind failed:\n%s%s",
            result.stdout,
            result.stderr,
        )


def get_html_theme_path() -> list[str]:
    """Return list of HTML theme paths."""
    return [str(Path(__file__).parent.parent.resolve())]


# For more details, see:
# https://www.sphinx-doc.org/en/master/development/theming.html#distribute-your-theme-as-a-python-package
def setup(app: Sphinx) -> dict[str, Any]:
    """Setup the Sphinx theme extension."""
    here = Path(__file__).parent.resolve()

    # register theme with Sphinx
    app.add_html_theme("napari_sphinx_theme", str(here))

    # connect event handlers for configuration and template processing
    app.connect("builder-inited", set_config_defaults)
    app.connect("html-page-context", setup_html_template_context)
    app.connect("build-finished", run_pagefind)

    # add sidebar templates to the search path for templates
    app.config.templates_path.append(str(here / "_templates"))

    return {"version": __version__, "parallel_read_safe": True}
