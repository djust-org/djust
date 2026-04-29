// 50-lazy-fill.js — v0.9.0 PR-B (#1043, ADR-015): client-side reception
// for `{% live_render lazy=True %}` chunks.
//
// Wire format from the server:
//
//   <dj-lazy-slot data-id="X" data-trigger="flush"></dj-lazy-slot>     // shell chunk
//   ...rest of body, </body></html>...                                  // body close
//   <template id="djl-fill-X" data-target="X" data-status="ok">
//       <div dj-view data-djust-embedded="X">...rendered child...</div>
//   </template>
//   <script>window.djust&&window.djust.lazyFill&&window.djust.lazyFill("X")</script>
//
// The browser parses each chunk as it streams in. Templates are
// inert-by-spec; the inline <script> after each fills runs at parse
// time and calls into this module to swap the slot.
//
// `data-trigger="visible"` defers the actual replaceWith until the
// slot enters the viewport — useful for below-fold lazy content where
// the user may never scroll to it.
//
// Status="error" / "timeout" wraps the fill in `<dj-error
// aria-live="polite">` for screen-reader announcement.

(function () {
  'use strict';

  if (!window.djust) window.djust = {};

  // Re-bind dj-* events on a freshly-inserted subtree. djust's
  // standard post-mutation reinit hook lives at window.djustReinit
  // when registered; if absent, slot-fills still work but events on
  // the new subtree won't bind until the next full reinit.
  function _reinitAfterFill() {
    try {
      if (typeof window.djustReinit === 'function') window.djustReinit();
    } catch (e) {
      if (window.djustDebug) console.warn('[lazy-fill] djustReinit threw', e);
    }
  }

  function _replaceSlot(slot, tpl, status) {
    if (status === 'ok') {
      // tpl.content is a DocumentFragment; cloneNode keeps the template
      // intact for double-fire idempotency.
      slot.replaceWith(tpl.content.cloneNode(true));
    } else {
      // error / timeout — wrap in <dj-error aria-live="polite"> so
      // screen readers announce. Custom element name is treated as
      // HTMLUnknownElement which is fine for layout-only purposes.
      var err = document.createElement('dj-error');
      err.setAttribute('aria-live', 'polite');
      err.appendChild(tpl.content.cloneNode(true));
      slot.replaceWith(err);
    }
    tpl.remove();
    _reinitAfterFill();
  }

  /**
   * Replace the `<dj-lazy-slot data-id="X">` placeholder with the
   * contents of `<template id="djl-fill-X">`. Idempotent — second call
   * for the same slot id is a no-op.
   *
   * Called by the inline activator script the server emits after each
   * fill chunk, AND by the auto-scan on DOMContentLoaded for templates
   * that arrived before this module loaded.
   */
  window.djust.lazyFill = function lazyFill(slotId) {
    var tpl = document.getElementById('djl-fill-' + slotId);
    if (!tpl || tpl.tagName !== 'TEMPLATE') {
      // Already filled (template removed) OR the template arrived
      // before this module loaded — auto-scan will retry.
      return;
    }

    var slot = document.querySelector(
      'dj-lazy-slot[data-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(slotId) : slotId) + '"]'
    );
    if (!slot) {
      // The fill chunk arrived but the slot was never in the DOM
      // (template-author error) or was removed by user JS. Drop the
      // template so a future call doesn't see stale state.
      tpl.remove();
      if (window.djustDebug) {
        console.warn('[lazy-fill] no slot found for id %s', slotId);
      }
      return;
    }

    var status = (tpl.dataset && tpl.dataset.status) || 'ok';
    var trigger = (slot.dataset && slot.dataset.trigger) || 'flush';

    if (trigger === 'visible' && 'IntersectionObserver' in window) {
      var io = new IntersectionObserver(
        function (entries) {
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].isIntersecting) {
              _replaceSlot(slot, tpl, status);
              io.disconnect();
              return;
            }
          }
        },
        { rootMargin: '50px' }
      );
      io.observe(slot);
      return;
    }

    // Default flush trigger OR visible-trigger fallback when
    // IntersectionObserver is unavailable.
    _replaceSlot(slot, tpl, status);
  };

  // Auto-scan: catch templates that landed before this module
  // initialized. Race scenarios: (1) the module is at slot 50, late
  // in the bundle; (2) the inline activator <script> runs before
  // window.djust.lazyFill is defined when the bundle loads
  // asynchronously.
  function _autoFillOnDOMReady() {
    var tpls = document.querySelectorAll('template[id^="djl-fill-"]');
    for (var i = 0; i < tpls.length; i++) {
      var slotId = tpls[i].id.slice('djl-fill-'.length);
      window.djust.lazyFill(slotId);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _autoFillOnDOMReady);
  } else {
    _autoFillOnDOMReady();
  }
})();
