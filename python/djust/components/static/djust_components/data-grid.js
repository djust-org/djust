/**
 * djust-components Data Grid — Client-side enhancements.
 *
 * Handles column resize, inline cell editing, and keyboard navigation
 * for the editable data grid component.
 *
 * Auto-initializes on DOMContentLoaded and observes for dynamically
 * inserted grids (djust LiveView patches).
 */
(function () {
  "use strict";

  // ── Column Resize ──────────────────────────────────────────────

  function initResize(wrapper) {
    if (wrapper._dgResize) return;
    wrapper._dgResize = true;

    var table = wrapper.querySelector("table.data-grid");
    if (!table) return;

    var headers = table.querySelectorAll("th[data-resizable]");
    headers.forEach(function (th) {
      var handle = document.createElement("div");
      handle.className = "data-grid-resize-handle";
      th.appendChild(handle);

      var startX, startWidth;

      handle.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        startX = e.clientX;
        startWidth = th.offsetWidth;
        handle.classList.add("resizing");

        function onMove(ev) {
          var dx = ev.clientX - startX;
          var newWidth = Math.max(40, startWidth + dx);
          th.style.width = newWidth + "px";
          th.style.minWidth = newWidth + "px";
        }

        function onUp() {
          handle.classList.remove("resizing");
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
        }

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });
  }

  // ── Inline Cell Editing ────────────────────────────────────────

  function initInlineEdit(wrapper) {
    if (wrapper._dgEdit) return;
    wrapper._dgEdit = true;

    var table = wrapper.querySelector("table.data-grid");
    if (!table) return;

    table.addEventListener("dblclick", function (e) {
      var td = e.target.closest("td[data-editable='true']");
      if (!td || td.querySelector("input, select")) return;

      var currentValue = td.textContent.trim();
      var colKey = td.getAttribute("data-col-key");
      var rowKey = td.closest("tr").getAttribute("data-row-key");
      var editEvent = wrapper.getAttribute("data-edit-event");
      var colType = td.getAttribute("data-type") || "text";

      td.classList.add("data-grid-cell-editing");

      var input;
      if (colType === "select") {
        // For select columns we fall back to text input (options come from server)
        input = document.createElement("input");
        input.type = "text";
      } else if (colType === "number") {
        input = document.createElement("input");
        input.type = "number";
      } else {
        input = document.createElement("input");
        input.type = "text";
      }

      input.value = currentValue;
      td.textContent = "";
      td.appendChild(input);
      input.focus();
      input.select();

      function finish(save) {
        var newValue = input.value;
        td.classList.remove("data-grid-cell-editing");
        td.textContent = save ? newValue : currentValue;

        if (save && newValue !== currentValue && editEvent) {
          var trigger = wrapper.querySelector(".data-grid-edit-trigger");
          if (trigger) {
            trigger.setAttribute("data-value", JSON.stringify({
              row_key: rowKey,
              column: colKey,
              value: newValue
            }));
            trigger.click();
          }
        }
      }

      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
        if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
        if (ev.key === "Tab") {
          ev.preventDefault();
          finish(true);
          // Move to next editable cell
          var next = td.nextElementSibling;
          while (next && !next.hasAttribute("data-editable")) {
            next = next.nextElementSibling;
          }
          if (next) {
            next.focus();
            next.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
          }
        }
      });

      input.addEventListener("blur", function () {
        setTimeout(function () {
          if (td.querySelector("input, select")) finish(true);
        }, 100);
      });
    });
  }

  // ── Keyboard Navigation ────────────────────────────────────────

  function initKeyboardNav(wrapper) {
    if (wrapper._dgKeyboard) return;
    wrapper._dgKeyboard = true;

    var table = wrapper.querySelector("table.data-grid");
    if (!table) return;

    wrapper.addEventListener("keydown", function (e) {
      var active = document.activeElement;
      if (!active || !table.contains(active)) return;
      // Don't navigate when editing
      if (active.tagName === "INPUT" || active.tagName === "SELECT") return;

      var td = active.closest("td");
      if (!td) return;

      var tr = td.parentElement;
      var cellIdx = td.cellIndex;
      var rows = table.querySelectorAll("tbody tr.data-grid-row");
      var rowIdx = -1;
      rows.forEach(function (r, i) { if (r === tr) rowIdx = i; });

      var targetCell = null;

      if (e.key === "ArrowRight" && td.nextElementSibling) {
        targetCell = td.nextElementSibling;
      } else if (e.key === "ArrowLeft" && td.previousElementSibling) {
        targetCell = td.previousElementSibling;
      } else if (e.key === "ArrowDown" && rowIdx >= 0 && rowIdx < rows.length - 1) {
        var nextRow = rows[rowIdx + 1];
        if (nextRow && nextRow.children[cellIdx]) targetCell = nextRow.children[cellIdx];
      } else if (e.key === "ArrowUp" && rowIdx > 0) {
        var prevRow = rows[rowIdx - 1];
        if (prevRow && prevRow.children[cellIdx]) targetCell = prevRow.children[cellIdx];
      } else if (e.key === "Enter" && td.hasAttribute("data-editable")) {
        td.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
        e.preventDefault();
        return;
      } else if (e.key === "F2" && td.hasAttribute("data-editable")) {
        td.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
        e.preventDefault();
        return;
      } else if (e.key === "Escape") {
        wrapper.focus();
        return;
      }

      if (targetCell) {
        e.preventDefault();
        targetCell.focus();
      }
    });
  }

  // ── Init ───────────────────────────────────────────────────────

  function initDataGrid(wrapper) {
    if (wrapper.hasAttribute("data-resizable")) initResize(wrapper);
    if (wrapper.hasAttribute("data-edit-event")) initInlineEdit(wrapper);
    if (wrapper.hasAttribute("data-keyboard-nav")) initKeyboardNav(wrapper);
  }

  function initAll() {
    document.querySelectorAll(".data-grid-wrapper").forEach(initDataGrid);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-init on djust LiveView patches
  var observer = new MutationObserver(function (mutations) {
    var shouldInit = false;
    mutations.forEach(function (m) {
      if (m.addedNodes.length) shouldInit = true;
    });
    if (shouldInit) initAll();
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
