/**
 * Code Snippet — copy-to-clipboard behavior.
 *
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  function initSnippet(el) {
    var btn = el.querySelector(".dj-code-snippet__copy");
    if (!btn || btn._djCodeSnippetInit) return;
    btn._djCodeSnippetInit = true;

    btn.addEventListener("click", function () {
      var code = el.querySelector(".dj-code-snippet__code");
      if (!code) return;

      var text = code.textContent || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          btn.textContent = "Copied!";
          setTimeout(function () {
            btn.textContent = "Copy";
          }, 2000);
        });
      } else {
        // Fallback for older browsers
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        btn.textContent = "Copied!";
        setTimeout(function () {
          btn.textContent = "Copy";
        }, 2000);
      }
    });
  }

  function initAll() {
    document.querySelectorAll(".dj-code-snippet").forEach(initSnippet);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  new MutationObserver(initAll).observe(document.body, {
    childList: true,
    subtree: true,
  });
})();
