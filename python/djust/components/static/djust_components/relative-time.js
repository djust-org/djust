/**
 * Relative Time — client-side "3 hours ago" display with auto-update.
 *
 * Parses the ISO datetime in the `datetime` attribute and replaces
 * textContent with a human-readable relative string.
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  var SECOND = 1000;
  var MINUTE = 60 * SECOND;
  var HOUR = 60 * MINUTE;
  var DAY = 24 * HOUR;
  var WEEK = 7 * DAY;
  var MONTH = 30 * DAY;
  var YEAR = 365 * DAY;

  function relativeString(date) {
    var now = Date.now();
    var diff = now - date.getTime();
    var future = diff < 0;
    var abs = Math.abs(diff);

    var value, unit;
    if (abs < MINUTE) {
      return "just now";
    } else if (abs < HOUR) {
      value = Math.floor(abs / MINUTE);
      unit = value === 1 ? "minute" : "minutes";
    } else if (abs < DAY) {
      value = Math.floor(abs / HOUR);
      unit = value === 1 ? "hour" : "hours";
    } else if (abs < WEEK) {
      value = Math.floor(abs / DAY);
      unit = value === 1 ? "day" : "days";
    } else if (abs < MONTH) {
      value = Math.floor(abs / WEEK);
      unit = value === 1 ? "week" : "weeks";
    } else if (abs < YEAR) {
      value = Math.floor(abs / MONTH);
      unit = value === 1 ? "month" : "months";
    } else {
      value = Math.floor(abs / YEAR);
      unit = value === 1 ? "year" : "years";
    }

    return future ? "in " + value + " " + unit : value + " " + unit + " ago";
  }

  function updateEl(el) {
    var iso = el.getAttribute("datetime");
    if (!iso) return;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return;
    el.textContent = relativeString(d);
  }

  function initEl(el) {
    if (el._djRelativeTimeInit) return;
    el._djRelativeTimeInit = true;

    updateEl(el);

    var auto = el.getAttribute("data-auto-update");
    if (auto === "true") {
      var interval =
        parseInt(el.getAttribute("data-interval"), 10) || 60;
      el._djRelativeTimeTimer = setInterval(function () {
        updateEl(el);
      }, interval * 1000);
    }
  }

  function initAll() {
    document.querySelectorAll(".dj-relative-time").forEach(initEl);
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
