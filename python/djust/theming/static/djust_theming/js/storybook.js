/* Component Storybook — presentation-only helpers.
 *
 * Nothing here owns state the server renders: search, filters and the
 * preview are djust events (StorybookSidebarMixin / Preview). This file only
 * does what the browser is for — focus, scrolling, clipboard, a scroll-spy
 * and the narrow-screen sidebar toggle — and re-runs the parts that touch
 * rendered nodes after each server patch (`djust:dom-update`).
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

  /* Copy buttons on code blocks. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.sb-copy');
    if (!btn) return;
    var block = btn.closest('.sb-code');
    var body = block && $('.sb-code-body', block);
    if (!body || !navigator.clipboard) return;
    navigator.clipboard.writeText(body.textContent).then(function () {
      btn.setAttribute('data-copied', '');
      btn.textContent = 'Copied';
      setTimeout(function () { btn.removeAttribute('data-copied'); btn.textContent = 'Copy'; }, 1500);
    });
  });

  function addCopyButtons() {
    $$('.sb-code[data-copy]').forEach(function (block) {
      if ($('.sb-copy', block)) return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sb-copy';
      btn.textContent = 'Copy';
      block.appendChild(btn);
    });
  }

  /* Keep the active sidebar link in view on load. */
  function revealActive() {
    var active = $('.sb-sidebar-link.active');
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: 'center' });
    }
  }

  /* "On this page": hide links to sections the page does not have, and mark
     the section nearest the top of the viewport as you scroll. */
  var spyTargets = [];
  function wireRail() {
    var rail = $('.sb-rail');
    if (!rail) return;
    spyTargets = [];
    $$('a[href^="#"]', rail).forEach(function (link) {
      var target = document.getElementById(link.getAttribute('href').slice(1));
      link.hidden = !target;
      if (target) spyTargets.push({ link: link, target: target });
    });
    onScroll();
  }
  function onScroll() {
    if (!spyTargets.length) return;
    var top = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sb-topbar-h')) || 3.25) * 16 + 24;
    var current = spyTargets[0];
    spyTargets.forEach(function (t) {
      if (t.target.getBoundingClientRect().top - top <= 0) current = t;
    });
    spyTargets.forEach(function (t) { t.link.classList.toggle('active', t === current); });
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  function init() {
    addCopyButtons();
    wireRail();
  }
  document.addEventListener('DOMContentLoaded', function () { init(); revealActive(); });
  document.addEventListener('djust:dom-update', init);
})();
