/**
 * djust-components NavBar -- the menu toggle under a strict CSP (#4146).
 *
 * By default the Tailwind and plain NavBar renderers toggle their menu with an
 * inline handler:
 *
 *     onclick="document.getElementById('<id>-mobile').classList.toggle('hidden')"
 *
 * A Content-Security-Policy whose script-src lacks 'unsafe-inline' refuses
 * that, so under LIVEVIEW_CONFIG["csp_strict"] the navbar root carries
 * `data-djust-navbar` and the button two markers instead:
 *
 *     data-navbar-toggle="<id>-mobile"      the element whose class changes
 *     data-navbar-toggle-class="hidden"     the class it toggles
 *
 * and this module does the same `classList.toggle` from one click listener on
 * `document`. Delegation covers a button present at load and one a LiveView
 * patch inserts later, with nothing to re-bind.
 *
 * Bounded to the navbar's own markup: the class must be one of the two the
 * renderers use (`hidden`, `show`), and the menu must be inside the button's
 * own `nav[data-djust-navbar]`. Markup that carries the attributes cannot
 * toggle a class it names on an element elsewhere on the page.
 *
 * The NavBar renders a <script> for this file and a strict LiveView page loads
 * it with the client; it registers once however often it runs.
 */
(function () {
  "use strict";

  if (typeof document === "undefined" || document._djustNavbarToggle) return;
  document._djustNavbarToggle = true;

  var CLASSES = { hidden: true, show: true };

  function onClick(e) {
    var target = e.target;
    if (!target || typeof target.closest !== "function") return;
    var button = target.closest("[data-navbar-toggle]");
    if (!button) return;
    var nav = button.closest("nav[data-djust-navbar]");
    if (!nav) return;
    var cls = button.getAttribute("data-navbar-toggle-class");
    if (!Object.prototype.hasOwnProperty.call(CLASSES, cls)) return;
    var menu = document.getElementById(button.getAttribute("data-navbar-toggle"));
    // As the inline handler: no menu, nothing happens. And only the nav's own menu.
    if (!menu || menu === nav || !nav.contains(menu)) return;
    menu.classList.toggle(cls);
  }

  document.addEventListener("click", onClick);
})();
