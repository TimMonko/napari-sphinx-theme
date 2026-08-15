"""Browser-free verification of the unified napari.org search.

Checks that built napari.org sub-sites are wired up for the Pagefind merge,
with no browser and no third-party dependencies (stdlib only), so it runs from
any python on any OS (PowerShell, WSL, CI):

  * ``--dir <build>`` — one build's pagefind bundle is present and well-formed.
  * ``--sites docs=... workshops=... --from-site docs`` — proves the builds are
    CONNECTED for the merge: each bundle is well-formed, listed in the
    from-site's merge list (napari-sites.json), and reachable at its canonical
    path (the installer's exact probe). This is the "are these two builds
    connected" check.

It cannot report actual merged result COUNTS (those need pagefind's WASM inside
a browser) — it verifies everything that has to be true for the merge to work.

Usage (single line, runs unchanged in both PowerShell and bash)::

    uv run --no-project python scripts/verify_search.py --dir docs/_build/html
    uv run --no-project python scripts/verify_search.py --sites docs=../napari-docs/docs/_build/html workshops=../napari-workshops/docs/_build/html --from-site docs

Exit code is 0 when the checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
import threading
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Canonical napari.org paths each site is deployed under — mirrors
# napari_sphinx_theme/static/search/napari-sites.json.
SITE_MOUNTS: dict[str, list[str]] = {
    "docs": ["/stable/", "/dev/"],
    "workshops": ["/workshops/"],
    "island-dispatch": ["/island-dispatch/"],
    "napari-animation": ["/napari-animation/"],
    "napari-metadata": ["/napari-metadata/"],
    "napari-plugin-manager": ["/napari-plugin-manager/"],
}


def _serve_sites(sites: dict[str, pathlib.Path], port: int) -> ThreadingHTTPServer:
    """Serve several build dirs under their canonical napari.org paths.

    This makes the installer's merge probes succeed for the sibling bundles, so
    we can verify cross-site results actually come back (and with correct URLs).
    ``/`` redirects to the first mount so the root isn't a 404.
    """
    mounts: list[tuple[str, pathlib.Path]] = []
    for name, build_dir in sites.items():
        for prefix in SITE_MOUNTS.get(name, [f"/{name}/"]):
            mounts.append((prefix, build_dir))
    mounts.sort(key=lambda m: len(m[0]), reverse=True)
    first_dir = mounts[0][1]

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(first_dir), **kwargs)

        def translate_path(self, path: str) -> str:
            # Abandon query params / fragments like the stdlib implementation.
            path = path.split("?", 1)[0].split("#", 1)[0]
            for prefix, build_dir in mounts:
                if path == prefix.rstrip("/") or path.startswith(prefix):
                    rest = path[len(prefix.rstrip("/")) :]
                    return str((build_dir / rest.lstrip("/")).resolve())
            return super().translate_path(path)

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", mounts[0][0])
                self.end_headers()
                return
            with contextlib.suppress(ConnectionResetError, BrokenPipeError):
                # Browser cancelled a transfer mid-stream; nothing to serve.
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _bundle_sanity(build_dir: pathlib.Path) -> list[str]:
    """Browser-free checks: the pagefind bundle must be present and well-formed."""
    problems: list[str] = []
    pagefind_dir = build_dir / "pagefind"
    if not pagefind_dir.is_dir():
        return [f"no pagefind bundle at {pagefind_dir.relative_to(build_dir)}/"]
    entry = pagefind_dir / "pagefind-entry.json"
    if not entry.is_file():
        problems.append(f"missing {entry.relative_to(build_dir)}")
    else:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{entry.relative_to(build_dir)} is not valid JSON")
        else:
            if not isinstance(data, dict) or "version" not in data:
                problems.append(
                    f"{entry.relative_to(build_dir)} missing expected 'version' key"
                )
    for required in ("pagefind.js", "pagefind-component-ui.js", "pagefind-worker.js"):
        if not (pagefind_dir / required).is_file():
            problems.append(f"missing pagefind/{required}")
    if not list(pagefind_dir.glob("index/**/*")) and not list(
        pagefind_dir.glob("*.pf_meta")
    ):
        problems.append("pagefind index chunks are empty")
    return problems


def _site_entry_url(name: str, port: int) -> str:
    """Canonical URL of a site's pagefind-entry.json when served via --sites."""
    base = SITE_MOUNTS.get(name, [f"/{name}/"])[0]
    return f"http://127.0.0.1:{port}/{base.lstrip('/')}pagefind/pagefind-entry.json"


def _entry_reachable(url: str) -> bool:
    """True if a pagefind-entry.json is reachable and valid JSON.

    This is the installer's merge probe: 404s, timeouts, or non-JSON responses
    mean the bundle is dead and would be dropped from the merge.
    """
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status != 200:
                return False
            json.loads(resp.read().decode("utf-8"))
            return True
    except (OSError, ValueError):
        return False


def _site_merge_names(build_dir: pathlib.Path) -> set[str]:
    """Sites listed in a build's canonical merge list (napari-sites.json)."""
    sites_file = build_dir / "_static" / "search" / "napari-sites.json"
    if not sites_file.is_file():
        return set()
    try:
        data = json.loads(sites_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {site["name"] for site in data.get("sites", []) if "name" in site}


def _sanity_sites(sites: dict[str, pathlib.Path], port: int, from_site: str) -> int:
    """Browser-free cross-site verification (stdlib only; runs on any OS).

    Serves each build at its canonical napari.org path and checks everything
    that has to be true for the merge to work:

    * every passed build has a well-formed pagefind bundle;
    * every passed sibling is listed in the from-site's canonical merge list
      (the installer only merges sites on napari-sites.json);
    * every passed site's pagefind-entry.json is reachable + valid at its
      canonical path (the installer's exact probe).

    It cannot report merged result COUNTS -- those need pagefind's WASM inside a
    browser (drop --sanity for that). Returns an exit code.
    """
    problems: list[str] = []
    server = _serve_sites(sites, port)
    try:
        print("\n### Cross-site sanity (browser-free)\n")
        for name, build_dir in sites.items():
            local = _bundle_sanity(build_dir)
            if local:
                for problem in local:
                    print(f"- {name}: {problem}")
                    problems.append(f"{name}: {problem}")
            else:
                print(f"- {name}: bundle OK")

        merged = _site_merge_names(sites[from_site])
        print(
            f"\nmerge list from {from_site} (napari-sites.json): "
            f"{', '.join(sorted(merged)) or '(none)'}"
        )
        for name in sites:
            if name != from_site and name not in merged:
                print(f"  ! {name} is NOT in {from_site}'s merge list")
                problems.append(f"{name} not in {from_site} merge list")

        print("\nreachability (pagefind-entry.json at canonical path):")
        for name in sites:
            url = _site_entry_url(name, port)
            ok = _entry_reachable(url)
            print(f"- {name}: {'ok' if ok else 'DEAD'}")
            if not ok:
                problems.append(f"{name} bundle unreachable at {url}")
    finally:
        server.shutdown()
        server.server_close()

    if problems:
        print("\nFAILED")
        return 1
    print("\nOK: all builds well-formed and connected")
    return 0


def _single_sanity(build_dir: pathlib.Path) -> int:
    print(f"\n### Bundle sanity for {build_dir}")
    problems = _bundle_sanity(build_dir)
    for problem in problems:
        print(f"- {problem}")
    if problems:
        return 1
    print("- pagefind bundle present and well-formed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dir",
        type=pathlib.Path,
        help="check one build's pagefind bundle (browser-free)",
    )
    group.add_argument(
        "--sites",
        nargs="+",
        metavar="NAME=PATH",
        help="prove builds are connected for the merge (browser-free), e.g. "
        "docs=_build/html workshops=../workshops/docs/_build/html",
    )
    parser.add_argument(
        "--from-site", default="docs", help="merge-list origin (--sites mode)"
    )
    parser.add_argument("--port", type=int, default=8342)
    args = parser.parse_args(argv)

    if args.dir:
        if not args.dir.is_dir():
            print(f"ERROR: {args.dir} is not a directory", file=sys.stderr)
            return 1
        return _single_sanity(args.dir)

    sites: dict[str, pathlib.Path] = {}
    for item in args.sites:
        name, _, raw = item.partition("=")
        path = pathlib.Path(raw)
        if not path.is_dir():
            print(f"ERROR: {name} build dir not found: {path}", file=sys.stderr)
            return 1
        sites[name] = path
    if args.from_site not in sites:
        print(
            f"ERROR: --from-site {args.from_site!r} not among --sites {list(sites)}",
            file=sys.stderr,
        )
        return 1
    return _sanity_sites(sites, args.port, args.from_site)


if __name__ == "__main__":
    raise SystemExit(main())
