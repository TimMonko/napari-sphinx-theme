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
// are skipped gracefully (per-index resilience), and light up automatically the
// moment they opt in — no per-site curation is needed.

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

  async function mount() {
    loadStylesheet(`${ownDir}napari-search.css`);

    // Resolve the merge list BEFORE any component connects, so the unified
    // instance is configured correctly from the start.
    let mergeIndex = [];
    if (mergePaths.length) {
      mergeIndex = mergePaths.map((p) => ({ bundlePath: p }));
    } else {
      try {
        const response = await fetch(`${ownDir}napari-sites.json`);
        mergeIndex = mergeListFromSites(await response.json());
      } catch (error) {
        console.warn(
          'napari-search-installer: could not load the canonical site list; ' +
            'searching this site only.',
          error,
        );
      }
    }

    // Load the Pagefind Component UI from this site's own bundle.
    loadStylesheet(`${bundleUrl}pagefind-component-ui.css`);
    await loadModule(`${bundleUrl}pagefind-component-ui.js`);

    const { configureInstance } = window.PagefindComponents;

    configureInstance('default', {
      bundlePath: bundleUrl,
      mergeIndex,
      excerptLength: 15,
    });

    const mounts = document.querySelectorAll(mountSelector);
    if (!mounts.length) {
      console.warn(
        `napari-search-installer: no element matches ${mountSelector}`,
      );
      return;
    }

    // One trigger per mount (e.g. desktop + mobile navbar variants)…
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

    // …but only one modal, appended to the body.
    let modal = document.querySelector('pagefind-modal');
    if (!modal) {
      modal = document.createElement('pagefind-modal');
      document.body.appendChild(modal);
    }

    // pydata-sphinx-theme also binds Ctrl/Cmd+K to its (now empty) search
    // dialog. Remove the dialog and make sure the pagefind modal wins the
    // shortcut instead. Known minor rough edge: pydata's leftover handler may
    // log a console error on the first mod+k until its dialog reference is
    // gone; the modal still opens.
    const dialog = document.getElementById('pst-search-dialog');
    if (dialog) dialog.remove();
    window.addEventListener(
      'keydown',
      (event) => {
        const mod = event.metaKey || event.ctrlKey;
        if (mod && !event.shiftKey && !event.altKey) {
          if (event.key.toLowerCase() === 'k') {
            event.preventDefault();
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
