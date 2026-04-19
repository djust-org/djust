/**
 * Scroll Spy — client-side IntersectionObserver for active section tracking.
 *
 * Reads section IDs from data-sections (JSON array), observes each section
 * element, and fires a djust event (data-event) when the visible section
 * changes.  Updates the active CSS class on navigation links.
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  function initScrollSpy(el) {
    if (el._djScrollSpyInit) return;
    el._djScrollSpyInit = true;

    var sectionsRaw = el.getAttribute("data-sections");
    var eventName = el.getAttribute("data-event") || "section_changed";
    var offset = el.getAttribute("data-offset") || "0px";

    var sections;
    try {
      sections = JSON.parse(sectionsRaw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(sections) || sections.length === 0) return;

    var currentActive = null;

    function setActive(sectionId) {
      if (sectionId === currentActive) return;
      currentActive = sectionId;

      // Update nav link classes
      var links = el.querySelectorAll("[data-section]");
      for (var i = 0; i < links.length; i++) {
        var link = links[i];
        if (link.getAttribute("data-section") === sectionId) {
          link.classList.add("dj-scroll-spy__item--active");
        } else {
          link.classList.remove("dj-scroll-spy__item--active");
        }
      }

      // Fire djust event
      el.dispatchEvent(
        new CustomEvent("djust:" + eventName, {
          bubbles: true,
          detail: { section: sectionId },
        })
      );
    }

    var observer = new IntersectionObserver(
      function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) {
            setActive(entries[i].target.id);
          }
        }
      },
      { rootMargin: "-" + offset + " 0px 0px 0px", threshold: 0.1 }
    );

    // Observe each section element in the document
    for (var i = 0; i < sections.length; i++) {
      var sectionEl = document.getElementById(sections[i]);
      if (sectionEl) {
        observer.observe(sectionEl);
      }
    }

    el._djScrollSpyObserver = observer;
  }

  function initAll() {
    document.querySelectorAll('[dj-hook="ScrollSpy"]').forEach(initScrollSpy);
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
          if (
            node.getAttribute &&
            node.getAttribute("dj-hook") === "ScrollSpy"
          ) {
            initScrollSpy(node);
          }
          if (node.querySelectorAll) {
            node
              .querySelectorAll('[dj-hook="ScrollSpy"]')
              .forEach(initScrollSpy);
          }
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
