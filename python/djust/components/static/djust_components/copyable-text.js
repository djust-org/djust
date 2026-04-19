/**
 * Copyable Text — click-to-copy with "Copied!" tooltip.
 *
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  function initEl(el) {
    if (el._djCopyableTextInit) return;
    el._djCopyableTextInit = true;

    function handleCopy() {
      var text = el.getAttribute("data-copy-text") || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(showTooltip);
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showTooltip();
      }
    }

    function showTooltip() {
      el.classList.add("dj-copied");
      setTimeout(function () {
        el.classList.remove("dj-copied");
      }, 2000);
    }

    el.addEventListener("click", handleCopy);
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleCopy();
      }
    });
  }

  function initAll() {
    document.querySelectorAll(".dj-copyable-text").forEach(initEl);
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
