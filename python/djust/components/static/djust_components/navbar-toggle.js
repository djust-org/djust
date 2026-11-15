/**
 * djust-components NavBar — the menu toggle (#4146).
 *
 * The Tailwind and plain NavBar renderers used to toggle their menu with an
 * inline handler:
 *
 *     onclick="document.getElementById('<id>-mobile').classList.toggle('hidden')"
 *
 * A Content-Security-Policy whose script-src lacks 'unsafe-inline' refuses
 * that. The button now carries two markers instead:
 *
 *     data-navbar-toggle="<id>-mobile"      the element whose class changes
 *     data-navbar-toggle-class="hidden"     the class it toggles
 *
 * and this module does the same `classList.toggle` from one click listener on
 * `document`. Delegation covers a button present at load and one a LiveView
 * patch or morph inserts later, with nothing to re-bind. The NavBar renders
 * the <script> that loads this file itself; it runs once however many navbars
 * a page has.
 */
(function () {
  "use strict";

  if (typeof document === "undefined" || document._djustNavbarToggle) return;
  document._djustNavbarToggle = true;

  function onClick(e) {
    var target = e.target;
    if (!target || typeof target.closest !== "function") return;
    var button = target.closest("[data-navbar-toggle]");
    if (!button) return;
    var menu = document.getElementById(button.getAttribute("data-navbar-toggle"));
    var cls = button.getAttribute("data-navbar-toggle-class");
    // As the inline handler: no menu, nothing happens. A class that is not
    // one token would throw in classList.toggle; it is not ours to fix.
    if (!menu || !cls || /\s/.test(cls)) return;
    menu.classList.toggle(cls);
  }

  document.addEventListener("click", onClick);
})();
