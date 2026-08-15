"""End-to-end verification of the unified napari.org search.

Opens a built napari.org sub-site (docs, workshops, ...) in a real browser,
drives the Pagefind Component UI modal exactly like a user would, and reports
how many results each query returns.

Why a browser? A plain "does the index contain words" check cannot catch the
failure modes that actually bite in production:

  * A dead sibling bundle in the merge list used to hang the whole search —
    pagefind's worker throws "Failed to load Pagefind metadata" when a merged
    bundle 404s, and the modal shows "Searching for ..." forever.
  * The installer failing to mount the trigger or modal.
  * A bundle that is missing or unparseable.

Requires Playwright (``pip install playwright``). A Playwright browser is used
if installed; otherwise the system Chrome/Edge is launched automatically, so no
``playwright install`` download is needed on most machines. In containers or CI
without a system browser, run ``python -m playwright install --with-deps
chromium`` once first (the bundled Chromium needs its system libraries).

Usage::

    python scripts/verify_search.py --url http://localhost:3001
    python scripts/verify_search.py --url http://localhost:3001 --queries seg plugin
    python scripts/verify_search.py --dir docs/_build/html --port 8342
    python scripts/verify_search.py --dir docs/_build/html --sanity   # no browser

Cross-site, browser-free (proves builds are CONNECTED for the merge; needs only
stdlib, so it runs from any python on any OS):

    # Serve the builds at their canonical paths and check that every one is
    # well-formed, listed in the merge list, and reachable by the installer.
    # (Single line so it runs unchanged in both PowerShell and bash.)
    python scripts/verify_search.py --sites docs=../napari-docs/docs/_build/html workshops=../napari-workshops/docs/_build/html --from-site docs --sanity

Cross-site, with a real browser (reports actual merged result COUNTS)::

    # Serve several built sites at their canonical napari.org paths and search
    # from one of them; reports how many results come from each sibling site.
    python scripts/verify_search.py --sites docs=../napari-docs/docs/_build/html workshops=../napari-workshops/docs/_build/html --from-site docs --queries segmentation plugin

Exit code is 0 when the checks pass (browser modes: every query returned at
least one result; sanity modes: bundles well-formed and connected), 1 otherwise.
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

DEFAULT_QUERIES = ["workshops", "segmentation", "plugin", "keybindings", "layer"]
# Terms that exist in multiple sibling sites, for proving the merge works.
CROSS_SITE_QUERIES = ["segmentation", "plugin", "workshops"]
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
SYSTEM_BROWSERS = [
    # Windows
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]


def _find_system_browser() -> str | None:
    for candidate in SYSTEM_BROWSERS:
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def _launch_browser(p: Any) -> Any:
    """Launch Playwright's Chromium, preferring an installed system browser.

    Falls back to Playwright's bundled Chromium. If neither can start (common
    in containers, where the bundled Chromium is missing system libraries such
    as libnspr4), exit with an actionable message instead of a raw Playwright
    traceback.
    """
    system_browser = _find_system_browser()
    if system_browser:
        try:
            return p.chromium.launch(headless=True, executable_path=system_browser)
        except Exception as exc:  # noqa: BLE001 - fall back to bundled Chromium
            print(
                f"note: system browser {system_browser} failed ({exc}); "
                "trying bundled Chromium"
            )
    try:
        return p.chromium.launch(headless=True)
    except Exception as exc:
        message = (
            "Could not launch a browser for the search checks.\n"
            f"  reason: {exc}\n"
            "  fix (Linux/container): install the Playwright browser and its deps:\n"
            "    uv run --with playwright python -m playwright install "
            "--with-deps chromium\n"
            "  ...or install a system browser (e.g. "
            "`sudo apt-get install -y chromium`)\n"
            "      and it will be auto-detected. `--sanity` needs no browser."
        )
        raise SystemExit(message) from exc


def _serve(build_dir: pathlib.Path, port: int) -> ThreadingHTTPServer:
    handler = lambda *args, **kw: SimpleHTTPRequestHandler(
        *args, directory=str(build_dir), **kw
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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


def _site_of_href(href: str) -> str:
    """Which site a search-result URL belongs to ('' if not under a known mount)."""
    for name, prefixes in SITE_MOUNTS.items():
        if any(href.startswith(p) for p in prefixes):
            return name
    return ""


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


def run_browser_checks(
    url: str,
    queries: list[str],
    per_query_timeout_s: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    from playwright.sync_api import sync_playwright  # deferred import

    results: list[dict[str, Any]] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.on(
            "pageerror",
            lambda err: page_errors.append(str(err).splitlines()[0][:160]),
        )

        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1500)

        if page.locator("pagefind-modal-trigger").count() == 0:
            browser.close()
            return (
                [],
                [f"installer did not mount: no pagefind-modal-trigger on {url}"],
            )

        for query in queries:
            page.locator("pagefind-modal-trigger").first.click(timeout=10_000)
            page.wait_for_timeout(400)
            input_el = page.locator("pagefind-modal input").first
            input_el.click(click_count=3)
            page.keyboard.press("Backspace")
            input_el.type(query, delay=40)

            # Wait for a terminal state for THIS query: "N results for <query>"
            # or "No results", else the search hung ("Searching for ..." forever).
            # Matching on the query text prevents a stale result set from the
            # previous search from satisfying the wait.
            hit = page.wait_for_function(
                """(q) => {
                    const el = document.querySelector('pagefind-modal');
                    const root = el && el.shadowRoot ? el.shadowRoot : el;
                    if (!root) return false;
                    const t = root.textContent || '';
                    const esc = q.replace(/[.*+?^${}()|[\\]\\/]/g, '\\\\$&');
                    const re = new RegExp('\\\\d+\\\\s+results?\\\\s+for\\\\s+' + esc, 'i');
                    return re.test(t) || t.includes('No results');
                }""",
                arg=query,
                timeout=per_query_timeout_s * 1000,
            )
            if not hit:
                results.append(
                    {"query": query, "count": None, "titles": [], "hung": True}
                )
                page.keyboard.press("Escape")
                continue

            # The summary line ("N results for ...") renders a beat before the
            # result list, so give the links a moment to paint before reading them.
            page.wait_for_timeout(400)
            row = page.evaluate(
                """(q) => {
                    const el = document.querySelector('pagefind-modal');
                    const root = el && el.shadowRoot ? el.shadowRoot : el;
                    const t = (root && root.textContent) || '';
                    const esc = q.replace(/[.*+?^${}()|[\\]\\/]/g, '\\\\$&');
                    const m = t.match(new RegExp('(\\\\d+)\\\\s+results?\\\\s+for\\\\s+' + esc, 'i'));
                    const count = m ? parseInt(m[1], 10) : 0;
                    const titles = root
                        ? Array.from(root.querySelectorAll('a[href]')).slice(0, 3)
                            .map(a => (a.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 55))
                        : [];
                    return { query: q, count, titles, hung: false };
                }""",
                query,
            )
            results.append(row)
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)

        browser.close()

    return results, page_errors


def run_cross_site_checks(
    url: str,
    queries: list[str],
    per_query_timeout_s: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Search from one site and report which sibling sites results come from.

    Works only when the sibling bundles are reachable (i.e. served at their
    canonical napari.org paths via --sites, or on the live deployment) — this is
    the check that proves the cross-site merge actually returns sibling results.
    """
    from playwright.sync_api import sync_playwright  # deferred import

    results: list[dict[str, Any]] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.on(
            "pageerror",
            lambda err: page_errors.append(str(err).splitlines()[0][:160]),
        )
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)

        # Sphinx/pydata sites use the hooked search button; non-Sphinx sites use
        # an injected pagefind-modal-trigger. Click whichever is present.
        trigger = page.locator("pagefind-modal-trigger")
        button = page.locator(".search-button-field")
        click_target = trigger if trigger.count() else button

        for query in queries:
            click_target.first.click(timeout=10_000)
            page.wait_for_timeout(400)
            input_el = page.locator("pagefind-modal input").first
            input_el.click(click_count=3)
            page.keyboard.press("Backspace")
            input_el.type(query, delay=40)
            page.wait_for_timeout(3000)
            row = page.evaluate(
                """(q) => {
                    const el = document.querySelector('pagefind-modal');
                    const root = el && el.shadowRoot ? el.shadowRoot : el;
                    const t = (root && root.textContent) || '';
                    const m = t.match(new RegExp('(\\\\d+)\\\\s+results?\\\\s+for\\\\s+' + q, 'i'));
                    const hrefs = root
                        ? Array.from(root.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '')
                        : [];
                    return { query: q, count: m ? parseInt(m[1], 10) : 0, hrefs };
                }""",
                query,
            )
            results.append(row)
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        browser.close()
    return results, page_errors


def _print_cross_site_report(
    results: list[dict[str, Any]], page_errors: list[str]
) -> int:
    failed = False
    print("\n### Cross-site merge verification\n")
    print("| Query | Total | by site (URL prefix) |")
    print("|---|---|---|")
    for row in results:
        hrefs: list[str] = row["hrefs"]
        by_site: dict[str, int] = {}
        samples: dict[str, list[str]] = {}
        for href in hrefs:
            site = _site_of_href(href) or "(other)"
            by_site[site] = by_site.get(site, 0) + 1
            if len(samples.setdefault(site, [])) < 2:
                samples[site].append(href)
        detail = ", ".join(f"{site}={n}" for site, n in sorted(by_site.items()))
        print(f"| {row['query']} | {row['count']} | {detail or 'none'} |")
        for site in sorted(samples):
            print(f"  - {site}: {samples[site]}")
        if not row["count"]:
            failed = True
        elif len(by_site) < 2:
            # Results came back but from one site only — legit if the term only
            # exists there, or a hint the merge isn't pulling siblings in.
            print(
                "  ! results from one site only (term may exist only there, "
                "or the merge may not be pulling siblings in)"
            )

    for error in page_errors:
        if "Pagefind metadata" in error or "pagefind" in error.lower():
            failed = True
            print(f"- PAGE ERROR: {error}")
    return 1 if failed else 0


def _print_report(
    results: list[dict[str, Any]], page_errors: list[str], sanity: list[str]
) -> int:
    failed = False
    print("\n### Search verification\n")
    print("| Query | Results | Top result |")
    print("|---|---|---|")
    for row in results:
        if row["hung"] or not row["count"]:
            failed = True
            status = "HUNG" if row["hung"] else "0 results"
            print(f"| {row['query']} | **{status}** | — |")
        else:
            top = row["titles"][0] if row["titles"] else "—"
            print(f"| {row['query']} | {row['count']} | {top} |")

    for problem in sanity:
        failed = True
        print(f"- SANITY FAIL: {problem}")

    for error in page_errors:
        if "Pagefind metadata" in error or "pagefind" in error.lower():
            failed = True
            print(f"- PAGE ERROR: {error}")

    if not results and not sanity:
        failed = True
        print("- No checks ran.")

    print(
        f"\n{'FAILED' if failed else 'PASSED'}: "
        f"{sum(1 for r in results if r['count'])}/{len(results)} queries returned results."
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL of a built site already being served")
    group.add_argument(
        "--dir", type=pathlib.Path, help="serve this build directory locally"
    )
    group.add_argument(
        "--sites",
        nargs="+",
        metavar="NAME=PATH",
        help="serve multiple built sites at their canonical napari.org paths "
        "(e.g. docs=_build/html workshops=../workshops/docs/_build/html) and "
        "verify the cross-site merge",
    )
    parser.add_argument(
        "--from-site", default="docs", help="search from this site (--sites mode)"
    )
    parser.add_argument("--port", type=int, default=8342)
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="search terms to check (default: site-local terms, or cross-site terms in --sites mode)",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="per-query seconds")
    parser.add_argument(
        "--sanity",
        action="store_true",
        help="browser-free bundle checks only (no Playwright needed)",
    )
    args = parser.parse_args(argv)

    url = args.url
    sanity_problems: list[str] = []

    if args.sites:
        # Cross-site mode: mount each build at its canonical napari.org path so
        # the merge probes succeed, then search from one site and report the
        # per-site breakdown of result URLs.
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
                f"ERROR: --from-site {args.from_site!r} not among --sites "
                f"{list(sites)}",
                file=sys.stderr,
            )
            return 1
        if args.sanity:
            return _sanity_sites(sites, args.port, args.from_site)
        _serve_sites(sites, args.port)
        base = SITE_MOUNTS.get(args.from_site, [f"/{args.from_site}/"])[0]
        url = f"http://127.0.0.1:{args.port}/{base.lstrip('/')}"
        print(
            f"Serving {len(sites)} site(s) at canonical paths; searching from "
            f"{args.from_site} at {url}"
        )
        queries = args.queries or CROSS_SITE_QUERIES
        try:
            results, page_errors = run_cross_site_checks(url, queries, args.timeout)
        except ImportError:
            print(
                "Playwright is not installed. Run `pip install playwright`.",
                file=sys.stderr,
            )
            return 2
        return _print_cross_site_report(results, page_errors)

    if args.dir:
        if not args.dir.is_dir():
            print(f"ERROR: {args.dir} is not a directory", file=sys.stderr)
            return 1
        sanity_problems = _bundle_sanity(args.dir)
        if not args.sanity:
            _serve(args.dir, args.port)
            url = f"http://127.0.0.1:{args.port}/"
            print(f"Serving {args.dir} at {url}")

    if args.sanity or url is None:
        print(f"\n### Bundle sanity for {args.dir or 'build'}")
        if sanity_problems:
            for p in sanity_problems:
                print(f"- {p}")
            return 1
        print("- pagefind bundle present and well-formed")
        return 0

    try:
        results, page_errors = run_browser_checks(
            url, args.queries or DEFAULT_QUERIES, args.timeout
        )
    except ImportError:
        print(
            "Playwright is not installed. Run `pip install playwright`, or use "
            "--sanity for browser-free bundle checks.",
            file=sys.stderr,
        )
        return 2

    return _print_report(results, page_errors, sanity_problems)


if __name__ == "__main__":
    raise SystemExit(main())
