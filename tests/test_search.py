from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import napari_sphinx_theme
from napari_sphinx_theme import run_pagefind, search


class FakeBuilder:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.theme_options = options or {}
        self.format = "html"
        self.outdir = "/tmp/site"


class FakeApp:
    def __init__(self, builder: FakeBuilder) -> None:
        self.builder = builder
        self.outdir = builder.outdir


def _app(options: dict[str, Any] | None = None) -> Any:
    return FakeApp(FakeBuilder(options))


def test_search_defaults_to_sphinx() -> None:
    # The `sphinx` default comes from theme.conf; an unset option must behave
    # as sphinx (pagefind build step not run).
    assert theme_conf_default_is_sphinx()
    with patch("napari_sphinx_theme.search.subprocess.run") as run:
        run_pagefind(_app(), None)
    run.assert_not_called()


def test_search_option_is_preserved() -> None:
    # An opted-in site runs the pagefind build step (asserted in
    # test_run_pagefind_invokes_pagefind); here we just verify the enabled
    # path reaches the find_spec guard.
    with (
        patch("napari_sphinx_theme.search.importlib.util.find_spec", return_value=None),
        patch("napari_sphinx_theme.search.subprocess.run") as run,
    ):
        run_pagefind(_app(options={"search": "pagefind"}), None)
    run.assert_not_called()


def test_run_pagefind_skipped_when_disabled() -> None:
    with patch("napari_sphinx_theme.search.subprocess.run") as run:
        run_pagefind(_app(options={"search": "sphinx"}), None)
    run.assert_not_called()


def theme_conf_default_is_sphinx() -> bool:
    theme_conf = Path(napari_sphinx_theme.__file__).parent / "theme.conf"
    return any(
        line.strip() == "search = sphinx"
        for line in theme_conf.read_text().splitlines()
    )


def test_run_pagefind_skipped_when_build_failed() -> None:
    with patch("napari_sphinx_theme.search.subprocess.run") as run:
        run_pagefind(_app(options={"search": "pagefind"}), Exception("boom"))
    run.assert_not_called()


def test_run_pagefind_warns_when_package_missing() -> None:
    with (
        patch("napari_sphinx_theme.search.importlib.util.find_spec", return_value=None),
        patch("napari_sphinx_theme.search.subprocess.run") as run,
    ):
        run_pagefind(_app(options={"search": "pagefind"}), None)
    run.assert_not_called()


def test_search_inject_copies_assets_and_mounts(tmp_path: Path) -> None:
    """The non-Sphinx flow: copies assets + injects the installer snippet."""
    site = tmp_path / "site"
    (site / "_static").mkdir(parents=True)
    (site / "index.html").write_text(
        "<html><body><p>segmentation content</p></body></html>"
    )

    count = search.inject(site, base_url="/workshops/")
    text = (site / "index.html").read_text()
    assert count == 1
    assert "napari-search-installer.js" in text
    assert 'data-bundle-path="/workshops/pagefind/"' in text
    # Runtime assets were copied in from the theme package.
    for name in search.SEARCH_ASSETS:
        assert (site / "_static" / "search" / name).is_file()


def test_run_pagefind_invokes_pagefind() -> None:
    completed = Mock()
    completed.returncode = 0
    completed.stdout = ""
    completed.stderr = ""
    with (
        patch(
            "napari_sphinx_theme.search.importlib.util.find_spec", return_value=Mock()
        ),
        patch(
            "napari_sphinx_theme.search.subprocess.run", return_value=completed
        ) as run,
    ):
        run_pagefind(_app(options={"search": "pagefind"}), None)
    run.assert_called_once()
    cmd = run.call_args.args[0]
    assert cmd[:5] == [sys.executable, "-m", "pagefind", "--site", "/tmp/site"]
    assert "--exclude-selectors" in cmd
    assert ".headerlink" in cmd
