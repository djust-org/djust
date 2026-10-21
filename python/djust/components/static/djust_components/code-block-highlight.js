/**
 * {% code_block %} syntax highlighting with djust's vendored highlight.js.
 *
 * Each code block is followed by
 *   <script src=".../code-block-highlight.js" data-hljs='{"src":..}' defer>
 * so the code needs no inline script, and a strict Content-Security-Policy
 * (script-src 'self', no 'unsafe-inline') allows it. Every run highlights the
 * block just before its own <script> (document.currentScript's previous
 * sibling). The library is injected ONCE per page (window.__djcHljsLoading);
 * blocks that find the load in progress wait for it with a bounded poll. Once
 * the library is loaded, a single MutationObserver (window.__djcHljsObserverInstalled)
 * highlights code blocks that arrive later via a djust socket patch, which does
 * not execute the <script> it inserts (#1625).
 */
(function () {
  var me = document.currentScript;
  if (!me) return;
  var pre = me.previousElementSibling;
  var el = pre && pre.querySelector ? pre.querySelector("code") : null;
  if (!el || el.dataset.highlighted) return;

  function doHL() {
    if (window.hljs && !el.dataset.highlighted) {
      hljs.highlightElement(el);
      el.dataset.highlighted = "true";
    }
  }

  // Warns only from the injected script's onerror, once per page.
  function warn() {
    if (!window.__djcHljsWarned) {
      window.__djcHljsWarned = true;
      console.warn(
        "[djust] highlight.js did not load; code blocks stay unhighlighted. " +
          "Check the browser console for an integrity (SRI) or CSP error."
      );
    }
  }

  // Highlights every unmarked block on the page. The injecting block's onload
  // runs it, so blocks whose poll gave up before a slow load finished are
  // still highlighted.
  function hlAll() {
    if (!window.hljs) return;
    Array.prototype.forEach.call(
      document.querySelectorAll("pre code[class^=language-]"),
      function (n) {
        if (!n.dataset.highlighted) {
          hljs.highlightElement(n);
          n.dataset.highlighted = "true";
        }
      }
    );
  }

  function installObserver() {
    if (window.__djcHljsObserverInstalled) return;
    if (typeof MutationObserver === "undefined") return;
    window.__djcHljsObserverInstalled = true;
    var hl = function (root) {
      if (!window.hljs) return;
      var sel = "pre code[class^=language-]";
      var nodes =
        root.matches && root.matches(sel)
          ? [root]
          : root.querySelectorAll
            ? root.querySelectorAll(sel)
            : [];
      Array.prototype.forEach.call(nodes, function (n) {
        if (!n.dataset.highlighted) {
          hljs.highlightElement(n);
          n.dataset.highlighted = "true";
        }
      });
    };
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        if (r.addedNodes) {
          Array.prototype.forEach.call(r.addedNodes, function (n) {
            if (n.nodeType === 1) hl(n);
          });
        }
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (window.hljs) {
    doHL();
    installObserver();
    return;
  }

  // First block on the page: inject the vendored library once.
  if (!window.__djcHljsLoading) {
    var cfg;
    try {
      cfg = JSON.parse(me.dataset.hljs || "{}");
    } catch (_err) {
      return;
    }
    if (!cfg.src) return;
    window.__djcHljsLoading = true;
    var s = document.createElement("script");
    s.src = cfg.src;
    s.integrity = cfg.integrity;
    if (cfg.crossOrigin) s.crossOrigin = cfg.crossOrigin;
    s.onload = function () {
      doHL();
      hlAll();
      installObserver();
    };
    s.onerror = warn;
    document.head.appendChild(s);
    return;
  }

  // Other blocks find the load in progress and wait for it. Bounded to 200
  // ticks (~10s) so an SRI mismatch, CSP block, or 404 cannot poll forever.
  // Giving up is silent: a load slower than the cap is still handled by the
  // onload sweep above, and a real failure is reported by onerror.
  var tries = 0;
  var iv = setInterval(function () {
    if (window.hljs) {
      clearInterval(iv);
      doHL();
      installObserver();
      return;
    }
    if (++tries >= 200) clearInterval(iv);
  }, 50);
})();
