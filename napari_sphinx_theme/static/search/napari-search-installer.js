// napari-search-installer.js
//
// Mounts the unified napari.org search (Pagefind Component UI) for the current
// site. Ships with napari-sphinx-theme and is copied by non-Sphinx sites at
// build time (e.g. napari-workshops' copy_theme_css.py).
//
// Usage (append to a page that already contains at least one element matching
// data-mount, once the site's own pagefind bundle exists):
//
//   <div class="napari-search"></div>
//   <script
//     src="/_static/search/napari-search-installer.js"
//     data-bundle-path="/stable/pagefind/"
//     data-mount=".napari-search"
//     defer
//   ></script>
//
// - data-bundle-path: path to THIS site's own pagefind bundle directory (the
//   folder containing pagefind-component-ui.js). Relative paths resolve against
//   the page. Required.
// - data-hook-button: CSS selector matching an EXISTING search button (e.g.
//   pydata-sphinx-theme's `.search-button__button`) that should open the modal.
//   Use this on Sphinx/pydata sites so the navbar keeps the theme's own search
//   button visual — the installer simply hooks its click. Exactly one of
//   data-hook-button and data-mount should be used.
// - data-mount: CSS selector matching every element that should host a search
//   trigger. Defaults to ".napari-search". Some themes render their navbar more
//   than once per page (desktop + mobile variants), so this script tag may be
//   included more than once — each match gets its own trigger, but only one
//   modal is created, and the installer only ever runs once per page.
// - data-merge-paths: optional comma-separated list of sibling pagefind bundle
//   paths, overriding the canonical merge list (napari-sites.json).
// - data-placeholder: optional text shown on the search trigger button.
//
// The canonical merge list lives in napari-sites.json beside this script and is
// maintained in ONE place. Sites listed there that haven't built pagefind yet
// are skipped gracefully (per-index resilience — each bundle is probed and dead
// ones are dropped rather than allowed to hang the search), and light up
// automatically the moment they opt in — no per-site curation is needed.
//
// Pagefind itself does NOT handle a missing merged bundle gracefully: its
// worker fetches each merged bundle's pagefind-entry.json and throws "Failed to
// load Pagefind metadata" (and the search never resolves) if that returns a
// non-JSON response, e.g. a 404 HTML page. So we probe every candidate bundle
// up front and only configure the instance with the ones that respond.

(function () {
  const thisScript = document.currentScript;
  if (!thisScript || window.__napariSearchInstallerMounted) {
    return;
  }
  window.__napariSearchInstallerMounted = true;

  const bundlePath = thisScript.dataset.bundlePath;
  const mergePaths = (thisScript.dataset.mergePaths || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const hookButton = thisScript.dataset.hookButton || '';
  const mountSelector = thisScript.dataset.mount || '.napari-search';
  // Directory this very script was loaded from — its sibling CSS and the
  // canonical site list live here too.
  const ownDir = new URL('.', thisScript.src).href;

  if (!bundlePath) {
    console.error('napari-search-installer: data-bundle-path is required');
    return;
  }

  const bundleUrl = new URL(bundlePath, window.location.href).href;

  function loadStylesheet(href) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.appendChild(link);
  }

  function loadModule(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.type = 'module';
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });
  }

  // The path prefix this site's pagefind bundle lives under, used to identify
  // "this site" so it is excluded from the merge list (a site searches its own
  // current version, then merges every sibling site's stable version).
  function siteRoot() {
    return new URL(bundleUrl, window.location.href).pathname.replace(
      /\/?pagefind\/?$/,
      '/',
    );
  }

  function mergeListFromSites(sites) {
    const current = siteRoot();
    return (sites.sites || [])
      .filter((site) => !(site.roots || []).some((r) => current.startsWith(r)))
      .map((site) => ({ bundlePath: site.bundlePath }));
  }

  // Pagefind cannot survive a merged bundle that 404s (it throws and the search
  // hangs on "Searching for..."), so drop any candidate whose pagefind-entry.json
  // is unreachable or not JSON before configuring the instance.
  async function probeMergeBundles(candidates) {
    const results = await Promise.all(
      candidates.map(async (site) => {
        const entryUrl = new URL(
          `${site.bundlePath}pagefind-entry.json`,
          window.location.href,
        ).href;
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 4000);
          const response = await fetch(entryUrl, {
            signal: controller.signal,
          });
          clearTimeout(timer);
          if (!response.ok) return null;
          const data = JSON.parse(await response.text());
          return data && typeof data === 'object' ? site : null;
        } catch {
          // 404 HTML page, timeout, network error, or non-JSON -> skip it.
          return null;
        }
      }),
    );
    return results.filter(Boolean);
  }

  async function mount() {
    // Resolve the merge list BEFORE any component connects, so the unified
    // instance is configured correctly from the start. Every candidate bundle is
    // probed and dead ones dropped — see probeMergeBundles.
    let mergeIndex = [];
    let candidates = [];
    if (mergePaths.length) {
      candidates = mergePaths.map((p) => ({ bundlePath: p }));
    } else {
      try {
        const response = await fetch(`${ownDir}napari-sites.json`);
        candidates = mergeListFromSites(await response.json());
      } catch (error) {
        console.warn(
          'napari-search-installer: could not load the canonical site list; ' +
            'searching this site only.',
          error,
        );
      }
    }
    mergeIndex = await probeMergeBundles(candidates);
    if (mergeIndex.length < candidates.length) {
      const dropped = candidates.length - mergeIndex.length;
      console.warn(
        `napari-search-installer: dropped ${dropped} unreachable sibling ` +
          'bundle(s); searching this site' +
          (mergeIndex.length
            ? ` + ${mergeIndex.length} merged site(s)`
            : ' only') +
          '.',
      );
    }

    // Load the Pagefind Component UI from this site's own bundle. Its CSS goes
    // FIRST and our napari-search.css LAST so our --pf-* overrides win the
    // cascade: the component also declares its defaults on :root, and at equal
    // specificity the later stylesheet wins — so loading ours first would mean
    // the component's defaults (loaded second) silently beat our light theme.
    loadStylesheet(`${bundleUrl}pagefind-component-ui.css`);
    loadStylesheet(`${ownDir}napari-search.css`);
    await loadModule(`${bundleUrl}pagefind-component-ui.js`);

    const { configureInstance } = window.PagefindComponents;

    configureInstance('default', {
      bundlePath: bundleUrl,
      mergeIndex,
      excerptLength: 15,
    });

    // One modal, appended to the body, before we wire up any trigger so that
    // both trigger paths below can reference it.
    let modal = document.querySelector('pagefind-modal');
    if (!modal) {
      modal = document.createElement('pagefind-modal');
      document.body.appendChild(modal);
    }

    if (hookButton) {
      // Hook an existing themed search button (pydata-sphinx-theme's
      // `.search-button__button`, mystmd's `.myst-search-bar`, ...) so the page
      // keeps its on-brand search visual while the click opens the Pagefind
      // modal. Document-level capture fires before the host's own handlers
      // (React/Radix included) and survives re-renders, so preventDefault +
      // stopPropagation keeps the host's own search UI from also opening.
      if (!document.querySelector(hookButton)) {
        console.warn(
          `napari-search-installer: no element matches ${hookButton}`,
        );
      }
      document.addEventListener(
        'click',
        (event) => {
          const target = event.target;
          if (target && target.closest && target.closest(hookButton)) {
            event.preventDefault();
            event.stopPropagation();
            modal.open();
          }
        },
        true,
      );
    } else {
      // Inject a trigger into every mount (e.g. desktop + mobile navbar
      // variants, or a custom floating button on non-Sphinx sites).
      const mounts = document.querySelectorAll(mountSelector);
      if (!mounts.length) {
        console.warn(
          `napari-search-installer: no element matches ${mountSelector}`,
        );
      }
      mounts.forEach((mount) => {
        if (mount.dataset.napariSearchMounted) return;
        mount.dataset.napariSearchMounted = 'true';
        const trigger = document.createElement('pagefind-modal-trigger');
        trigger.setAttribute('shortcut', 'mod+k');
        if (mount.dataset.placeholder) {
          trigger.setAttribute('placeholder', mount.dataset.placeholder);
        }
        mount.appendChild(trigger);
      });
    }

    // Neutralise pydata-sphinx-theme's own search UI so the page ends up with
    // exactly one search (its dialog). Non-Sphinx hosts should hide their own
    // search bar via their theme config (e.g. mystmd `site.options.hide_search`)
    // rather than DOM removal — React-hydrated sites break on that.
    const dialog = document.getElementById('pst-search-dialog');
    if (dialog) dialog.remove();
    window.addEventListener(
      'keydown',
      (event) => {
        const mod = event.metaKey || event.ctrlKey;
        if (mod && !event.shiftKey && !event.altKey) {
          if (event.key.toLowerCase() === 'k') {
            event.preventDefault();
            // Also stop the host's own search shortcut (e.g. mystmd binds
            // Ctrl+K on `document`) from opening a second search UI.
            event.stopPropagation();
            if (modal && modal.open) modal.open();
          }
        }
      },
      true,
    );
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
