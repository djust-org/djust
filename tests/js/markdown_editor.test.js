import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import fs from "node:fs";
const source = fs.readFileSync(
  "./python/djust/components/static/djust_components/markdown-editor.js",
  "utf8",
);
function setup(value = "") {
  const dom = new JSDOM(
    '<form><label for="body">Body</label><textarea id="body" name="body" dj-input="validate_field"></textarea><div dj-hook="MarkdownEditor" dj-update="ignore" data-field="body"></div></form>',
    { runScripts: "outside-only" },
  );
  const doc = dom.window.document,
    field = doc.querySelector("textarea");
  field.value = value;
  dom.window.eval(source);
  const hook = {
    ...dom.window.djust.hooks.MarkdownEditor,
    el: doc.querySelector("[dj-hook]"),
  };
  hook.mounted();
  return { dom, doc, field, hook };
}
describe("MarkdownEditor native field controls", () => {
  it("formats a selection and dispatches exactly one native input", () => {
    const { field, hook } = setup("hello world");
    let count = 0;
    field.addEventListener("input", () => count++);
    field.setSelectionRange(6, 11);
    hook.format("bold");
    expect(field.value).toBe("hello **world**");
    expect(count).toBe(1);
    expect(field.selectionStart).toBe(8);
  });
  it("places the caret inside empty formatting marks", () => {
    const { field, hook } = setup("hello");
    field.setSelectionRange(5, 5);
    hook.format("italic");
    expect(field.value).toBe("hello__");
    expect(field.selectionStart).toBe(6);
  });
  it("formats full lines without including a following unselected line", () => {
    const { field, hook } = setup("alpha\nbeta\ngamma");
    field.setSelectionRange(1, 11);
    hook.format("ordered");
    expect(field.value).toBe("1. alpha\n2. beta\ngamma");
  });
  it("handles a blank first line", () => {
    const { field, hook } = setup("\nnext");
    field.setSelectionRange(0, 0);
    hook.format("heading");
    expect(field.value).toBe("## \nnext");
  });
  it("places fences on their own lines", () => {
    const { field, hook } = setup("before code after");
    field.setSelectionRange(7, 11);
    hook.format("block");
    expect(field.value).toBe("before \n```\ncode\n```\n after");
  });
  it("keeps controls inside server-declared markup across updates", () => {
    const { doc, field, hook } = setup("draft");
    const before = Array.from(field.parentNode.childNodes);
    field.value = "validated draft";
    hook.updated();
    hook.updated();
    expect(Array.from(field.parentNode.childNodes)).toEqual(before);
    expect(doc.querySelectorAll(".dj-markdown-toolbar")).toHaveLength(1);
    expect(field.value).toBe("validated draft");
    expect(doc.querySelector('[data-markdown-action="bold"]').textContent).toBe(
      "B",
    );
  });
  it("respects readonly and disabled fields", () => {
    const { field, hook } = setup("draft");
    field.readOnly = true;
    hook.updated();
    hook.format("bold");
    expect(field.value).toBe("draft");
    expect(hook.actionButtons.every((b) => b.disabled)).toBe(true);
    field.readOnly = false;
    field.disabled = true;
    hook.updated();
    hook.format("bold");
    expect(field.value).toBe("draft");
  });
  it("handles keyboard shortcuts without interrupting IME input", () => {
    const { dom, field } = setup("hello");
    field.setSelectionRange(0, 5);
    field.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key: "b",
        ctrlKey: true,
        bubbles: true,
      }),
    );
    expect(field.value).toBe("**hello**");
    field.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key: "i",
        ctrlKey: true,
        isComposing: true,
        bubbles: true,
      }),
    );
    expect(field.value).toBe("**hello**");
  });
  it("rebinds replacement fields and removes listeners when destroyed", () => {
    const { doc, field, hook, dom } = setup("old");
    const fresh = field.cloneNode();
    fresh.value = "fresh";
    field.replaceWith(fresh);
    hook.updated();
    fresh.setSelectionRange(0, 5);
    fresh.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: "b", ctrlKey: true }),
    );
    expect(fresh.value).toBe("**fresh**");
    hook.destroyed();
    expect(doc.querySelector(".dj-markdown-toolbar")).toBeNull();
    fresh.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: "b", ctrlKey: true }),
    );
    expect(fresh.value).toBe("**fresh**");
  });
});
