/**
 * The URL guard of the data table's row navigation
 * (`python/djust/components/static/djust_components/data-table-row-click.js`).
 *
 * The rule: the URL is normalised the way a browser reads a URL, then tested. A value that is
 * protocol-relative after normalisation is refused, as is any scheme other than http(s). It is
 * not an origin guard: an absolute http(s) URL to any host is allowed on purpose, as it is in
 * an `<a href>`.
 *
 * The oracle here is the browser's own parser (`new URL`, the WHATWG algorithm), not a second
 * regex: for every generated value the guard accepts, the text it hands to `navigate()` must
 * either be an absolute http(s) URL or resolve to the page's own origin.
 */

import { describe, it, expect, vi } from "vitest";
import { JSDOM } from "jsdom";
import { readFileSync } from "fs";

const code = readFileSync(
  "./python/djust/components/static/djust_components/data-table-row-click.js",
  "utf-8"
);

function load() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, {
    runScripts: "dangerously",
    url: "https://site.example/app/page?q=1",
  });
  dom.window.eval(code);
  return dom;
}

const BASE = "https://site.example/app/page?q=1";
const BASE_ORIGIN = new URL(BASE).origin;

/** The previous test, kept to show the corpus exercises the old decision. */
const OLD = /^(https?:\/\/|\/(?!\/)|\.)/;

function resolvesTo(value) {
  try {
    return new URL(value, BASE);
  } catch (_e) {
    return null;
  }
}

describe("row navigation: the guard reads the URL the way the browser does", () => {
  const guard = load().window.djustDataTableRowClick.safeRowHref;

  it.each([
    ["backslash after the slash", "/\\other.example/x"],
    ["tab between the slashes", "/\t/other.example"],
    ["newline between the slashes", "/\n/other.example"],
    ["carriage return between the slashes", "/\r/other.example"],
    ["backslash and tab", "/\\\t/other.example"],
    ["leading control then //", "\u0001//other.example"],
    ["leading space then //", "  //other.example"],
    ["leading tab then //", "\t//other.example"],
    ["plain //", "//other.example/x"],
    ["slash then backslash twice", "/\\\\other.example"],
  ])("refuses %s", (_label, value) => {
    expect(guard(value)).toBe("");
  });

  it.each([
    ["/orders/42/", "/orders/42/"],
    ["/orders/42/?tab=a&b=1#x", "/orders/42/?tab=a&b=1#x"],
    ["./42/", "./42/"],
    ["../x/", "../x/"],
    [".", "."],
    ["https://shop.example/orders/42", "https://shop.example/orders/42"],
    ["http://shop.example/", "http://shop.example/"],
    ["HTTPS://x/y", "HTTPS://x/y"], // the scheme is case-insensitive
    ["Http://x/y", "Http://x/y"],
    ["/%2Fother.example", "/%2Fother.example"], // an escape is not a slash
  ])("still allows %s", (value, expected) => {
    expect(guard(value)).toBe(expected);
  });

  it("still refuses what it always refused", () => {
    for (const v of [
      "javascript:alert(1)",
      "data:text/html,x",
      "vbscript:x",
      "ftp://x/",
      "mailto:a@b.c",
      "other.example/x",
      "",
      "   ",
      "?q=1",
      "#frag",
    ]) {
      expect(guard(v), JSON.stringify(v)).toBe("");
    }
    expect(guard(undefined)).toBe("");
    expect(guard(null)).toBe("");
    expect(guard(5)).toBe("");
  });

  it("hands navigate() the normalised text, which is what was tested", () => {
    const dom = load();
    const navigate = vi.fn();
    dom.window.djustDataTableRowClick.navigate = navigate;
    dom.window.document.body.innerHTML =
      '<table><tbody><tr class="data-table-row-clickable" data-href="  /ok/\t1/ ">' +
      "<td>x</td></tr></tbody></table>";
    dom.window.djustDataTableRowClick.initAll();
    dom.window.document.querySelector("tr").click();
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith("/ok/1/");
  });

  it("a click on a row whose normalised href is protocol-relative navigates nowhere", () => {
    const dom = load();
    const navigate = vi.fn();
    dom.window.djustDataTableRowClick.navigate = navigate;
    for (const href of ["/\\other.example/x", "/\t/other.example"]) {
      dom.window.document.body.innerHTML =
        '<table><tbody><tr class="data-table-row-clickable"><td>x</td></tr></tbody></table>';
      const tr = dom.window.document.querySelector("tr");
      tr.setAttribute("data-href", href);
      dom.window.djustDataTableRowClick.initAll();
      tr.click();
    }
    expect(navigate).not.toHaveBeenCalled();
  });
});

/** Every combination of the pieces an href that normalises to protocol-relative is made of. */
function corpus() {
  const leads = ["", " ", "\t", "\n", "\u0001", "\u000b", "\u00a0", "\u2028"];
  const seps = ["/", "\\", "\t", "\n", "\r", "\u0000", " ", "%2f", "%5c", "%09", "\uff0f", "."];
  const heads = ["", "http:", "https:", "HtTp:", "ht\ttp:", "javascript:", "."];
  const tails = ["other.example/x", "x", ""];
  const out = [];
  for (const lead of leads)
    for (const head of heads)
      for (const a of seps)
        for (const b of seps)
          for (const c of ["", "/", "\\"])
            for (const tail of tails) out.push(lead + head + a + b + c + tail);
  return out;
}

describe("row navigation: differential against the browser's URL parser", () => {
  const guard = load().window.djustDataTableRowClick.safeRowHref;
  const values = corpus();

  it("the corpus is large", () => {
    expect(values.length).toBeGreaterThan(30000);
  });

  it("whatever the guard accepts is http(s) absolute or stays on the origin", () => {
    const bad = [];
    for (const v of values) {
      const target = guard(v);
      if (!target) continue;
      if (/^https?:\/\//i.test(target)) continue; // allowed by design
      const url = resolvesTo(target);
      if (!url || url.origin !== BASE_ORIGIN) bad.push(JSON.stringify(v));
    }
    expect(bad.slice(0, 5)).toEqual([]);
    expect(bad.length).toBe(0);
  });

  it("whatever the guard accepts is a web URL, or an absolute http(s) text the browser cannot parse", () => {
    for (const v of values) {
      const target = guard(v);
      if (!target) continue;
      const url = resolvesTo(target);
      if (url === null) {
        // `http://` with no host: location.assign() throws and nothing happens.
        expect(target, JSON.stringify(v)).toMatch(/^https?:\/\//i);
        continue;
      }
      expect(["http:", "https:"]).toContain(url.protocol);
    }
  });

  it("nothing the old test allowed AND the browser keeps on this origin is newly refused", () => {
    const refused = [];
    for (const v of values) {
      if (!OLD.test(v)) continue;
      const url = resolvesTo(v);
      if (url && url.origin === BASE_ORIGIN && !guard(v)) refused.push(JSON.stringify(v));
    }
    expect(refused.slice(0, 5)).toEqual([]);
  });

  it("the corpus exercises the old test: values it accepted are now refused", () => {
    const changed = values.filter((v) => {
      if (!OLD.test(v)) return false;
      if (/^https?:\/\//.test(v)) return false;
      const url = resolvesTo(v);
      return url && url.origin !== BASE_ORIGIN;
    });
    expect(changed.length).toBeGreaterThan(10);
    expect(changed.every((v) => guard(v) === "")).toBe(true);
  });
});
