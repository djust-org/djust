/**
 * djust-components Data Table — Row-level navigation handler (#1111).
 *
 * Adds keyboard accessibility (Enter / Space activate a focused row) and
 * a nested-control guard (clicks inside an interactive descendant
 * <a>/<button>/<input>/<label>/<select>/<textarea>/<details>/<summary>/<option>
 * do NOT trigger row navigation) to data_table rows that opt in via
 * the `data-table-row-clickable` marker class.
 *
 * The template tag emits one of two row shapes when row navigation is
 * enabled:
 *   1. `dj-click="<event>" data-value="<row_key>"` (Option B, preferred):
 *      djust's existing event binder fires the LiveView event on
 *      bubble-phase click. This module's job is purely defensive —
 *      cancel the click in the capture phase when the target is a
 *      nested control, so the LiveView event handler never fires for
 *      those clicks.
 *   2. `data-href="<url>"` (Option A, static URL): no `dj-click`, so
 *      this module is responsible for the navigation. On click (and on
 *      Enter / Space keydown when focused), navigate via
 *      `window.location.assign(tr.dataset.href)`. Same nested-control
 *      guard applies.
 *
 * Design notes:
 *   - CSP-strict friendly: no inline event handlers, no eval, no nonce
 *     plumbing required. The whole feature works under
 *     `script-src 'self'` once this file is served as a static asset.
 *   - Composition with selectable=True: the per-row checkbox is an
 *     <input>, so the nested-control guard automatically suppresses row
 *     navigation when clicking it. No special-case code required.
 *   - Composition with the cell-level link column (#1110): the cell
 *     <a> is also caught by the nested-control guard, so cell links
 *     "win" over row navigation — clicking the link follows the link
 *     and does NOT also fire the row event.
 *   - Auto-init on DOMContentLoaded; re-runs on djust LiveView VDOM
 *     patches via a MutationObserver, idempotent per-row via a
 *     `_dtRowClick` instance flag so repeated patches don't double-bind.
 */
(function () {
  "use strict";

  // Selectors for descendants whose clicks should NOT propagate up to
  // row-level navigation. Matches the Stage 4 brief enumeration plus
  // textarea (a non-button form control whose accidental row-nav would
  // be just as user-hostile as the others), plus details/summary/option
  // — disclosure widgets and select children that the user expects to
  // toggle/select without triggering row navigation (#1171 R3).
  var NESTED_CONTROL_SELECTOR =
    "a, button, input, label, select, textarea, details, summary, option";

  // Expose navigate via the public namespace so tests can spy on it
  // (vi.spyOn) without needing a magic underscored global. JSDOM 26+
  // marks Location.prototype.assign as non-configurable, so direct
  // interception isn't possible — going through this namespace lets
  // tests stub it cleanly. (#1171 R4)
  if (typeof window !== "undefined") {
    window.djustDataTableRowClick = window.djustDataTableRowClick || {};
    if (!window.djustDataTableRowClick.navigate) {
      window.djustDataTableRowClick.navigate = function (h) {
        window.location.assign(h);
      };
    }
  }

  function navigateForRow(tr) {
    // Static-URL path: navigate via dataset.href.
    var href = tr.dataset && tr.dataset.href;
    if (href) {
      // Only allow http(s) and SAME-ORIGIN relative-path URLs to defend
      // against a hostile data-href value sneaking in (e.g. javascript:
      // URIs or protocol-relative `//evil.com/...` cross-origin redirects).
      // Developer-controlled values from `reverse()` are always either
      // absolute or relative paths, never `javascript:` schemes.
      // The `(?!\/)` lookahead on the leading `/` rejects `//host` while
      // still allowing single-leading-slash absolute paths.
      if (/^(https?:\/\/|\/(?!\/)|\.)/.test(href)) {
        // Dispatch through the namespace so tests can vi.spyOn() the
        // navigate property. Production path is the default function
        // installed on the namespace above.
        window.djustDataTableRowClick.navigate(href);
      }
      return true;
    }
    return false;
  }

  function bindRow(tr) {
    if (tr._dtRowClick) return;
    tr._dtRowClick = true;

    // Capture-phase click on the row: if the click originated inside a
    // nested control, stopImmediatePropagation prevents the bubble-phase
    // dj-click handler from firing, AND prevents this module's own
    // click->navigate logic below.
    tr.addEventListener(
      "click",
      function (e) {
        if (e.target !== tr && e.target.closest) {
          var nested = e.target.closest(NESTED_CONTROL_SELECTOR);
          if (nested && tr.contains(nested)) {
            // The nested control gets to handle the click natively.
            // Suppress row-level navigation entirely.
            e.stopImmediatePropagation();
            return;
          }
        }

        // Static-URL navigation. (For dj-click rows, this is a no-op
        // because dataset.href isn't set.)
        navigateForRow(tr);
      },
      true /* capture */
    );

    // Keyboard activation: Enter and Space when the row is focused
    // synthesise a click. The click then re-enters the handler above
    // with `e.target === tr`, so the nested-control guard doesn't trip
    // and the navigation (or dj-click) fires normally.
    tr.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        // Only fire when the row itself is the active element — don't
        // hijack Space when the user is typing into a nested input.
        if (document.activeElement !== tr) return;
        e.preventDefault();
        tr.click();
      }
    });
  }

  function initWrapper(wrapper) {
    var rows = wrapper.querySelectorAll("tr.data-table-row-clickable");
    rows.forEach(bindRow);
  }

  function initAll() {
    document.querySelectorAll(".data-table-wrapper").forEach(initWrapper);
    // Also support tables that aren't wrapped (defensive).
    document
      .querySelectorAll("tr.data-table-row-clickable")
      .forEach(bindRow);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-init on djust LiveView patches that insert new rows.
  if (typeof MutationObserver !== "undefined") {
    var observer = new MutationObserver(function (mutations) {
      var shouldInit = false;
      for (var i = 0; i < mutations.length; i++) {
        if (mutations[i].addedNodes.length) {
          shouldInit = true;
          break;
        }
      }
      if (shouldInit) initAll();
    });
    if (document.body) {
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  // Expose for tests and for explicit re-init from app code. Merge
  // onto the namespace (the navigate property was already set up at
  // module top so the closure can call it via the namespace).
  if (typeof window !== "undefined") {
    window.djustDataTableRowClick = window.djustDataTableRowClick || {};
    window.djustDataTableRowClick.initAll = initAll;
    window.djustDataTableRowClick.initWrapper = initWrapper;
    window.djustDataTableRowClick.bindRow = bindRow;
  }
})();
