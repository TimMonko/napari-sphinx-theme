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
``playwright install`` download is needed on most machines.

Usage::

    python scripts/verify_search.py --url http://localhost:3001
    python scripts/verify_search.py --url http://localhost:3001 --queries seg plugin
    python scripts/verify_search.py --dir docs/_build/html --port 8342
    python scripts/verify_search.py --dir docs/_build/html --sanity   # no browser

Exit code is 0 if every query returned at least one result, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_QUERIES = ["workshops", "segmentation", "plugin", "keybindings", "layer"]
RESULT_RE = re.compile(r"(\d+)\s+results?\s+for\b")
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
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]


def _find_system_browser() -> str | None:
    for candidate in SYSTEM_BROWSERS:
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def _serve(build_dir: pathlib.Path, port: int) -> ThreadingHTTPServer:
    handler = lambda *args, **kw: SimpleHTTPRequestHandler(
        *args, directory=str(build_dir), **kw
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
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


def run_browser_checks(
    url: str,
    queries: list[str],
    per_query_timeout_s: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    from playwright.sync_api import sync_playwright  # deferred import

    results: list[dict[str, Any]] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        system_browser = _find_system_browser()
        if system_browser:
            browser = p.chromium.launch(headless=True, executable_path=system_browser)
        else:
            browser = p.chromium.launch(headless=True)
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
    parser.add_argument("--port", type=int, default=8342)
    parser.add_argument(
        "--queries", nargs="+", default=DEFAULT_QUERIES, help="search terms to check"
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
        results, page_errors = run_browser_checks(url, args.queries, args.timeout)
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
