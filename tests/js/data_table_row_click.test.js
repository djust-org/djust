/**
 * Tests for #1111 row-level navigation client-side handler
 * (`python/djust/components/static/djust_components/data-table-row-click.js`).
 *
 * Covers:
 *   - Click on `<tr.data-table-row-clickable>` with `data-href` →
 *     `window.location.assign(href)` is called.
 *   - Click on a nested `<a>` inside the row → navigation does NOT fire.
 *   - Keyboard Enter / Space on a focused row → navigation fires.
 *   - Click on the selectable checkbox cell → navigation does NOT fire
 *     (composes with the dj-click case via the same nested-control
 *     guard since `<input>` is in the protected-descendants list).
 *
 * The handler is loaded as a component module (not bundled into
 * `client.js`), so we eval it directly into a JSDOM and exercise the
 * exposed `window.djustDataTableRowClick.bindRow()` entry point.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { JSDOM } from "jsdom";
import { readFileSync } from "fs";

const ROW_CLICK_JS_PATH =
  "./python/djust/components/static/djust_components/data-table-row-click.js";
const rowClickCode = readFileSync(ROW_CLICK_JS_PATH, "utf-8");

function buildDom(bodyHtml) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
    { runScripts: "dangerously", url: "http://localhost/" }
  );
  // Eval the component module into the JSDOM window. Module installs
  // window.djustDataTableRowClick.navigate as the production hook into
  // window.location.assign; we replace it with a vitest spy so tests
  // can capture target URLs without JSDOM 26+'s non-configurable
  // Location.prototype.assign getting in the way (#1171 R4).
  dom.window.eval(rowClickCode);
  const assignSpy = vi.fn();
  dom.window.djustDataTableRowClick.navigate = assignSpy;
  dom.window.__locationAssignSpy = assignSpy;
  // The module hooks DOMContentLoaded when readyState === "loading"
  // (which is true under JSDOM); fire it explicitly so initAll() runs.
  dom.window.document.dispatchEvent(
    new dom.window.Event("DOMContentLoaded")
  );
  return dom;
}

function clickableRowMarkup({
  href = "/c/1/",
  withCheckbox = false,
  withAnchor = false,
} = {}) {
  return `
    <div class="data-table-wrapper">
      <table class="data-table">
        <tbody>
          <tr class="data-table-row-clickable"
              data-href="${href}"
              role="button" tabindex="0" style="cursor:pointer">
            ${
              withCheckbox
                ? `<td><input type="checkbox" class="data-table-checkbox" id="cb1"></td>`
                : ""
            }
            <td>
              ${
                withAnchor
                  ? `<a href="/inner/" id="inner-link">inner</a>`
                  : "Plain cell"
              }
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
}

describe("data-table-row-click — Option A (data-href)", () => {
  it("navigates via window.location.assign on row click", () => {
    const dom = buildDom(clickableRowMarkup());
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    expect(tr).not.toBeNull();

    // Click on the row's plain cell (target === td → bubbles to tr).
    const td = tr.querySelector("td");
    td.click();

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledTimes(1);
    expect(dom.window.__locationAssignSpy).toHaveBeenCalledWith("/c/1/");
  });

  it("does NOT navigate when click target is a nested <a>", () => {
    const dom = buildDom(clickableRowMarkup({ withAnchor: true }));
    const link = dom.window.document.getElementById("inner-link");
    expect(link).not.toBeNull();

    link.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("does NOT navigate when click target is a nested <input> (selectable composition)", () => {
    const dom = buildDom(clickableRowMarkup({ withCheckbox: true }));
    const cb = dom.window.document.getElementById("cb1");
    expect(cb).not.toBeNull();

    cb.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("rejects javascript: URI in data-href (defense-in-depth)", () => {
    // Even if a developer accidentally let a hostile value through into
    // data-href, the handler must not call assign() with a
    // javascript: URI.
    const dom = buildDom(
      clickableRowMarkup({ href: "javascript:alert(1)" })
    );
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    tr.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });
});

describe("data-table-row-click — keyboard activation", () => {
  it("Enter on a focused row triggers navigation", () => {
    const dom = buildDom(clickableRowMarkup());
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    tr.focus();

    const enterEvent = new dom.window.KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    tr.dispatchEvent(enterEvent);

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledTimes(1);
    expect(dom.window.__locationAssignSpy).toHaveBeenCalledWith("/c/1/");
  });

  it("Space on a focused row triggers navigation", () => {
    const dom = buildDom(clickableRowMarkup());
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    tr.focus();

    const spaceEvent = new dom.window.KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    });
    tr.dispatchEvent(spaceEvent);

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledTimes(1);
  });

  it("Space inside a nested input does NOT hijack the keystroke", () => {
    // The user is typing in a nested checkbox/text input — Space must
    // NOT navigate. Implementation guard: only fires when
    // document.activeElement === tr.
    const dom = buildDom(clickableRowMarkup({ withCheckbox: true }));
    const cb = dom.window.document.getElementById("cb1");
    cb.focus();

    const spaceEvent = new dom.window.KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    });
    // Space dispatched FROM the checkbox bubbles up; we must not
    // navigate because document.activeElement is the checkbox, not
    // the row.
    cb.dispatchEvent(spaceEvent);

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("non-Enter/Space keys do nothing", () => {
    const dom = buildDom(clickableRowMarkup());
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    tr.focus();

    ["Tab", "Escape", "ArrowDown", "a"].forEach((key) => {
      tr.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", {
          key,
          bubbles: true,
          cancelable: true,
        })
      );
    });

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });
});

describe("data-table-row-click — Option B (dj-click composition)", () => {
  /**
   * Verifies that for the dj-click row shape, the handler:
   *   - Does NOT call window.location.assign (no data-href).
   *   - DOES call stopImmediatePropagation when target is a nested
   *     control (so the bubble-phase dj-click handler never fires).
   *   - Does NOT cancel the click for plain row clicks (so dj-click
   *     fires normally on bubble).
   */
  it("plain row click does not preventDefault or stopPropagation", () => {
    const dom = buildDom(`
      <table class="data-table"><tbody>
        <tr class="data-table-row-clickable"
            dj-click="open_user" data-value="42"
            role="button" tabindex="0">
          <td id="cell">Alice</td>
        </tr>
      </tbody></table>
    `);
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    let bubbledTarget = null;
    // Mimic djust's bubble-phase dj-click binding via a document-level
    // capture-false listener. If our capture-phase handler stops
    // propagation, this listener won't fire.
    dom.window.document.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("[dj-click]")) {
        bubbledTarget = e.target.closest("[dj-click]");
      }
    });

    dom.window.document.getElementById("cell").click();

    expect(bubbledTarget).toBe(tr); // bubble reached document
    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("nested <a> click stops propagation so dj-click does not fire", () => {
    const dom = buildDom(`
      <table class="data-table"><tbody>
        <tr class="data-table-row-clickable"
            dj-click="open_user" data-value="42"
            role="button" tabindex="0">
          <td><a href="/inner/" id="inner-link">link</a></td>
        </tr>
      </tbody></table>
    `);
    let djClickFired = false;
    dom.window.document.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("[dj-click]")) {
        djClickFired = true;
      }
    });

    dom.window.document.getElementById("inner-link").click();

    expect(djClickFired).toBe(false);
  });
});

describe("data-table-row-click — idempotence", () => {
  it("bindRow() is safe to call multiple times on the same <tr>", () => {
    const dom = buildDom(clickableRowMarkup());
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    // Manually re-bind a few times.
    dom.window.djustDataTableRowClick.bindRow(tr);
    dom.window.djustDataTableRowClick.bindRow(tr);
    dom.window.djustDataTableRowClick.bindRow(tr);

    tr.click();

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledTimes(1);
  });
});

describe("data-table-row-click — extended nested-control selector (#1171 R3)", () => {
  // PR #1170 selector covered a/button/input/label/select/textarea (6
  // tags). #1171 adds details/summary/option so disclosure widgets and
  // <option>s inside a <select> don't trigger row navigation when the
  // user toggles them. The <option> case is largely transitive (clicks
  // bubble via the parent <select>) — we cover it explicitly to lock
  // the contract in.
  it("does NOT navigate when click target is a nested <summary> (details disclosure)", () => {
    const dom = buildDom(`
      <table class="data-table"><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="/c/1/">
          <td>
            <details id="row-details">
              <summary id="row-summary">More info</summary>
              <p>Body</p>
            </details>
          </td>
        </tr>
      </tbody></table>
    `);
    const summary = dom.window.document.getElementById("row-summary");
    expect(summary).not.toBeNull();

    summary.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("does NOT navigate when click target is a nested <option> (transitive via <select>)", () => {
    const dom = buildDom(`
      <table class="data-table"><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="/c/1/">
          <td>
            <select id="row-select">
              <option id="row-option-a" value="a">A</option>
              <option value="b">B</option>
            </select>
          </td>
        </tr>
      </tbody></table>
    `);
    const opt = dom.window.document.getElementById("row-option-a");
    expect(opt).not.toBeNull();

    opt.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("DOES navigate when click is on a row containing <details> but outside the disclosure (happy path regression)", () => {
    // Regression check: extending the selector did not break the
    // happy path. Clicking a plain <td> sibling of a <details> still
    // navigates.
    const dom = buildDom(`
      <table class="data-table"><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="/c/1/">
          <td id="plain-cell">Plain cell</td>
          <td>
            <details><summary>More</summary><p>Body</p></details>
          </td>
        </tr>
      </tbody></table>
    `);
    const cell = dom.window.document.getElementById("plain-cell");
    expect(cell).not.toBeNull();

    cell.click();

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledTimes(1);
    expect(dom.window.__locationAssignSpy).toHaveBeenCalledWith("/c/1/");
  });
});

describe("data-table-row-click — URL allowlist (open-redirect defense)", () => {
  // Stage 11 review of PR #1170 found `/^(https?:|\/|\.)/` allowed
  // protocol-relative URLs (`//evil.com/path`) — same-origin assumption
  // fails. Tightened to `/^(https?:\/\/|\/(?!\/)|\.)/` which rejects `//`.
  it("rejects protocol-relative URL `//evil.com/path` (cross-origin)", () => {
    const dom = buildDom(`
      <table><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="//evil.com/path">
          <td>cell</td>
        </tr>
      </tbody></table>
    `);
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    dom.window.djustDataTableRowClick.bindRow(tr);

    tr.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });

  it("accepts single-leading-slash absolute path `/claims/42`", () => {
    const dom = buildDom(`
      <table><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="/claims/42">
          <td>cell</td>
        </tr>
      </tbody></table>
    `);
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    dom.window.djustDataTableRowClick.bindRow(tr);

    tr.click();

    expect(dom.window.__locationAssignSpy).toHaveBeenCalledWith("/claims/42");
  });

  it("rejects `javascript:alert(1)` (existing guard, regression)", () => {
    const dom = buildDom(`
      <table><tbody>
        <tr class="data-table-row-clickable" role="button" tabindex="0"
            data-href="javascript:alert(1)">
          <td>cell</td>
        </tr>
      </tbody></table>
    `);
    const tr = dom.window.document.querySelector("tr.data-table-row-clickable");
    dom.window.djustDataTableRowClick.bindRow(tr);

    tr.click();

    expect(dom.window.__locationAssignSpy).not.toHaveBeenCalled();
  });
});
