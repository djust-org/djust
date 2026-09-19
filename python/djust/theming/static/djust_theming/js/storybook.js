/* Component Storybook — presentation-only helpers.
 *
 * Nothing here owns state the server renders: search, filters and the
 * preview are djust events (StorybookSidebarMixin / Preview), and copying
 * code is the code snippet component's own `dj-copy`. This file only does
 * what the browser is for — focus, scrolling, a scroll-spy for the table of
 * contents and the narrow-screen sidebar toggle — and re-runs the parts that
 * touch rendered nodes after each server patch (`djust:dom-update`).
 */
(function () {
  'use strict';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* `/` focuses the sidebar search, Esc clears it (dispatching `input` so the
     `dj-input="search"` handler sees the empty query) and blurs. */
  document.addEventListener('keydown', function (e) {
    var input = $('#sb-search');
    if (!input) return;
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '') || (e.target && e.target.isContentEditable);
    if (e.key === '/' && !typing) {
      e.preventDefault();
      input.focus();
      input.select();
    } else if (e.key === 'Escape' && e.target === input) {
      if (input.value) {
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      input.blur();
    }
  });

  /* Narrow screens: the sidebar slides in behind a menu button. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-sb-menu]');
    if (btn) {
      document.body.classList.toggle('sb-sidebar-open');
      btn.setAttribute('aria-expanded', document.body.classList.contains('sb-sidebar-open') ? 'true' : 'false');
      return;
    }
    if (document.body.classList.contains('sb-sidebar-open') && !e.target.closest('.sb-sidebar')) {
      document.body.classList.remove('sb-sidebar-open');
    }
  });

  /* Keep the active sidebar link in view on load. */
  function revealActive() {
    var active = $('.sb-sidebar .sidebar-item.active');
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'center' });
  }

  /* Table of contents: mark the section nearest the top as you scroll. */
  var spyTargets = [];
  function wireToc() {
    spyTargets = [];
    $$('.sb-rail .toc-item').forEach(function (link) {
      var target = document.getElementById((link.getAttribute('href') || '').slice(1));
      if (target) spyTargets.push({ link: link, target: target });
    });
    onScroll();
  }
  function onScroll() {
    if (!spyTargets.length) return;
    var top = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sb-topbar-h')) || 3.25) * 16 + 24;
    var current = spyTargets[0];
    spyTargets.forEach(function (t) { if (t.target.getBoundingClientRect().top - top <= 0) current = t; });
    spyTargets.forEach(function (t) { t.link.classList.toggle('toc-item-active', t === current); });
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  document.addEventListener('DOMContentLoaded', function () { wireToc(); revealActive(); });
  document.addEventListener('djust:dom-update', wireToc);
})();
