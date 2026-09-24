// djust account page kit (ADR-039): progressive enhancement only.
// Every page works without this script.
(function () {
  function init() {
    document.querySelectorAll("[data-dj-auth-toggle]").forEach(function (btn) {
      var input = document.getElementById(btn.getAttribute("aria-controls"));
      if (!input) return;
      btn.hidden = false;
      input.setAttribute("data-dj-auth-has-toggle", "");
      btn.addEventListener("click", function () {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.textContent = show ? "Hide" : "Show";
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
    var firstError = document.querySelector('.dj-auth-form [aria-invalid="true"]');
    if (firstError) firstError.focus();
    document.querySelectorAll('input[autocomplete="one-time-code"]').forEach(function (el) {
      el.addEventListener("paste", function (e) {
        var text = (e.clipboardData || window.clipboardData).getData("text").replace(/\s/g, "");
        if (!text) return;
        e.preventDefault();
        el.value = el.maxLength > 0 ? text.slice(0, el.maxLength) : text;
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
