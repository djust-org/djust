/**
 * Countdown — client-side timer behavior.
 *
 * Reads the ISO 8601 target datetime from data-target, counts down
 * days/hours/minutes/seconds, and fires a djust event (data-event)
 * when the countdown reaches zero.  Uses MutationObserver for
 * LiveView compatibility.
 */
(function () {
  "use strict";

  function initCountdown(el) {
    if (el._djCountdownInit) return;
    el._djCountdownInit = true;

    var target = el.getAttribute("data-target");
    if (!target) return;

    var eventName = el.getAttribute("data-event") || "";
    var targetDate = new Date(target);
    var fired = false;

    function pad(n) {
      return n < 10 ? "0" + n : String(n);
    }

    function update() {
      var now = Date.now();
      var diff = targetDate.getTime() - now;

      if (diff <= 0) {
        setValues(0, 0, 0, 0);
        if (!fired) {
          fired = true;
          if (eventName) {
            el.dispatchEvent(
              new CustomEvent("djust:" + eventName, {
                bubbles: true,
                detail: { finished: true },
              })
            );
          }
        }
        return;
      }

      var secs = Math.floor(diff / 1000);
      var days = Math.floor(secs / 86400);
      secs -= days * 86400;
      var hours = Math.floor(secs / 3600);
      secs -= hours * 3600;
      var minutes = Math.floor(secs / 60);
      secs -= minutes * 60;

      setValues(days, hours, minutes, secs);
    }

    function setValues(d, h, m, s) {
      var units = el.querySelectorAll("[data-unit]");
      for (var i = 0; i < units.length; i++) {
        var unit = units[i].getAttribute("data-unit");
        if (unit === "days") units[i].textContent = pad(d);
        else if (unit === "hours") units[i].textContent = pad(h);
        else if (unit === "minutes") units[i].textContent = pad(m);
        else if (unit === "seconds") units[i].textContent = pad(s);
      }
    }

    update();
    var interval = setInterval(function () {
      var diff = targetDate.getTime() - Date.now();
      if (diff <= 0) {
        update();
        clearInterval(interval);
      } else {
        update();
      }
    }, 1000);
  }

  function initAll() {
    document.querySelectorAll('[dj-hook="Countdown"]').forEach(initCountdown);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-initialize after LiveView patches
  new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType === 1) {
          if (node.getAttribute && node.getAttribute("dj-hook") === "Countdown") {
            initCountdown(node);
          }
          if (node.querySelectorAll) {
            node.querySelectorAll('[dj-hook="Countdown"]').forEach(initCountdown);
          }
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
