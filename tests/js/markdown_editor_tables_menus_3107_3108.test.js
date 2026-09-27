/**
 * MarkdownEditor table actions (#3107), selection/empty-line menus (#3108)
 * and the public window.djust.getHook accessor (#3107 part 2).
 *
 * These run the REAL vendored Tiptap bundle (markdown-visual.js) and the
 * real hook (markdown-editor.js) in one jsdom window; the getHook cases load
 * the real client.js so the accessor is exercised through mountHooks().
 */
import { describe, it, expect } from "vitest";
import { JSDOM } from "jsdom";
import fs from "node:fs";

const STATIC = "./python/djust/components/static/djust_components/";
const hookSource = fs.readFileSync(STATIC + "markdown-editor.js", "utf8");
const visualSource = fs.readFileSync(STATIC + "markdown-visual.js", "utf8");
const clientSource = fs.readFileSync(
  "./python/djust/static/djust/client.js",
  "utf8",
);
const TABLE = "Intro\n\n| A | B |\n| --- | --- |\n| x | y |\n\nOutro";
const TABLE_ACTIONS = [
  "row-before",
  "row-after",
  "row-delete",
  "column-before",
  "column-after",
  "column-delete",
  "header-row",
  "table-delete",
];

function setup(value = "", attrs = "", { client = false } = {}) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body><div dj-root><form><label for="body">Body</label><textarea id="body" name="body" dj-input="validate_field"></textarea><div dj-hook="MarkdownEditor" dj-update="ignore" data-field="body" data-mode="visual"${attrs}></div></form></div></body></html>`,
    {
      url: "http://localhost/",
      runScripts: client ? "dangerously" : "outside-only",
      pretendToBeVisual: true,
    },
  );
  const { window } = dom;
  const doc = window.document;
  const field = doc.querySelector("textarea");
  field.value = value;
  if (client) {
    window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    try {
      window.eval(clientSource);
    } catch (_) {
      /* client.js bootstraps against a live server; the hook API is enough */
    }
  }
  window.eval(visualSource);
  window.eval(hookSource);
  const el = doc.querySelector("[dj-hook]");
  let hook;
  if (client) {
    window.djust.mountHooks(doc);
    hook = window.djust.getHook(el);
  } else {
    hook = { ...window.djust.hooks.MarkdownEditor, el };
    hook.mounted();
  }
  return { window, doc, field, hook, el };
}
const wait = (window, ms) => new Promise((r) => window.setTimeout(r, ms));
// Text position of the first text node equal to `text`.
function posOf(editor, text) {
  let found = null;
  editor.state.doc.descendants((node, pos) => {
    if (found === null && node.isText && node.text === text) found = pos;
  });
  return found;
}
const button = (root, action) =>
  root.querySelector(`[data-markdown-action="${action}"]`);

describe("#3107 table actions", () => {
  it("renders every table action with an aria-label, inside a labelled group", () => {
    const { hook } = setup(TABLE);
    const group = hook.toolbar.querySelector(".dj-markdown-table-actions");
    expect(group.getAttribute("role")).toBe("group");
    expect(group.getAttribute("aria-label")).toBe("Table");
    for (const action of TABLE_ACTIONS) {
      const b = button(group, action);
      expect(b, action).not.toBeNull();
      expect(b.getAttribute("aria-label")).toBeTruthy();
      expect(b.type).toBe("button");
    }
    expect(button(hook.toolbar, "table").getAttribute("aria-label")).toBe(
      "Insert table",
    );
  });

  it("shows and enables table actions only inside a table (editor.can)", () => {
    const { hook } = setup(TABLE);
    const editor = hook.getEditor();
    const group = hook.toolbar.querySelector(".dj-markdown-table-actions");
    editor.commands.setTextSelection(2); // "Intro"
    expect(group.hidden).toBe(true);
    for (const action of TABLE_ACTIONS)
      expect(button(group, action).disabled, action).toBe(true);
    expect(button(hook.toolbar, "table").disabled).toBe(false);

    editor.commands.setTextSelection(posOf(editor, "x"));
    expect(group.hidden).toBe(false);
    for (const action of TABLE_ACTIONS.filter((a) => a !== "header-row"))
      expect(button(group, action).disabled, action).toBe(false);
    // A GFM table always has a header: it is shown pressed, and cannot be
    // removed (that would reappear as an empty header row on reload).
    expect(button(group, "header-row").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(button(group, "header-row").disabled).toBe(true);
    // Block formats have no spelling inside a GFM cell.
    for (const action of ["heading", "list", "block", "table"])
      expect(button(hook.toolbar, action).disabled, action).toBe(true);
    expect(button(hook.toolbar, "bold").disabled).toBe(false);
  });

  it("each table action edits the table, updates the field, and round-trips", () => {
    const expected = {
      "row-before":
        "| A   | B   |\n| --- | --- |\n|     |     |\n| x   | y   |",
      "row-after": "| A   | B   |\n| --- | --- |\n| x   | y   |\n|     |     |",
      "row-delete": "| A   | B   |\n| --- | --- |",
      "column-before":
        "|     | A   | B   |\n| --- | --- | --- |\n|     | x   | y   |",
      "column-after":
        "| A   |     | B   |\n| --- | --- | --- |\n| x   |     | y   |",
      "column-delete": "| B   |\n| --- |\n| y   |",
    };
    for (const [action, table] of Object.entries(expected)) {
      const { hook, field } = setup(TABLE);
      const editor = hook.getEditor();
      editor.commands.setTextSelection(posOf(editor, "x"));
      button(hook.toolbar, action).click();
      expect(field.value, action).toContain(table);
      const json = editor.getJSON();
      expect(hook.visual.load(field.value), action).toBe("");
      expect(editor.getJSON(), action).toEqual(json);
      hook.destroyed();
    }
  });

  it("deletes a table, and restores a header after the header row is deleted", () => {
    let { hook, field } = setup(TABLE);
    let editor = hook.getEditor();
    editor.commands.setTextSelection(posOf(editor, "x"));
    button(hook.toolbar, "table-delete").click();
    expect(field.value).not.toContain("|");
    expect(
      hook.toolbar.querySelector(".dj-markdown-table-actions").hidden,
    ).toBe(true);
    hook.destroyed();

    ({ hook, field } = setup(TABLE));
    editor = hook.getEditor();
    editor.commands.setTextSelection(posOf(editor, "A"));
    button(hook.toolbar, "row-delete").click();
    const header = button(hook.toolbar, "header-row");
    expect(header.getAttribute("aria-pressed")).toBe("false");
    expect(header.disabled).toBe(false);
    header.click();
    expect(field.value).toContain("| x   | y   |\n| --- | --- |");
    expect(header.getAttribute("aria-pressed")).toBe("true");
    expect(hook.visual.load(field.value)).toBe("");
  });

  it("inserts a table in Visual mode and a Markdown skeleton in source mode", () => {
    const { hook, field } = setup("Hello");
    hook.getEditor().commands.setTextSelection(6);
    button(hook.toolbar, "table").click();
    expect(field.value).toMatch(
      /^Hello\n\n\n\| +\| +\| +\|\n\| --- \| --- \| --- \|/,
    );
    expect(hook.visual.load(field.value)).toBe("");

    hook.setMode("markdown");
    field.value = "Before\nAfter";
    field.setSelectionRange(6, 6);
    button(hook.toolbar, "table").click();
    expect(field.value).toBe(
      "Before\n\n| Column | Column |\n| --- | --- |\n|  |  |\n\nAfter",
    );
    expect(field.value.slice(field.selectionStart, field.selectionEnd)).toBe(
      "Column",
    );
    // Source mode never shows the contextual table actions.
    expect(
      hook.toolbar.querySelector(".dj-markdown-table-actions").hidden,
    ).toBe(true);
    hook.setMode("visual");
    expect(hook.mode).toBe("visual");
  });

  it("keeps a hard break inside a table cell editable in Visual mode", () => {
    const { hook, field } = setup(TABLE);
    const editor = hook.getEditor();
    editor.commands.setTextSelection(posOf(editor, "x") + 1);
    editor.commands.setHardBreak();
    expect(field.value).toContain("| x<br> |");
    expect(hook.visual.load(field.value)).toBe("");
    // Outside a table, raw HTML still keeps the document in Markdown mode.
    expect(hook.visual.load("a<br>b")).not.toBe("");
  });
});

describe("#3108 selection (bubble) and empty-line (floating) menus", () => {
  it("builds an ARIA toolbar that Tiptap attaches only over a selection", async () => {
    const { hook, window } = setup("Hello world");
    const editor = hook.getEditor();
    const bubble = hook.bubble;
    expect(bubble.getAttribute("role")).toBe("toolbar");
    expect(bubble.getAttribute("aria-label")).toBe("Formatting");
    expect(
      [...bubble.querySelectorAll("[data-markdown-action]")].map(
        (b) => b.dataset.markdownAction,
      ),
    ).toEqual(["bold", "italic", "code", "link", ...TABLE_ACTIONS]);
    expect(bubble.isConnected).toBe(false);

    editor.commands.focus();
    editor.commands.setTextSelection({ from: 1, to: 6 });
    await wait(window, 300);
    expect(bubble.isConnected).toBe(true);
    expect(hook.surface.contains(bubble)).toBe(true);

    editor.commands.setTextSelection(3);
    await wait(window, 50);
    expect(bubble.isConnected).toBe(false);
  });

  it("shows table actions in the bubble only inside a table", async () => {
    const { hook, window } = setup(TABLE);
    const editor = hook.getEditor();
    const group = hook.bubble.querySelector(".dj-markdown-table-actions");
    editor.commands.focus();
    editor.commands.setTextSelection({ from: 1, to: 6 });
    await wait(window, 300);
    expect(group.hidden).toBe(true);
    const x = posOf(editor, "x");
    editor.commands.setTextSelection({ from: x, to: x + 1 });
    await wait(window, 300);
    expect(hook.bubble.isConnected).toBe(true);
    expect(group.hidden).toBe(false);
    expect(button(group, "row-after").disabled).toBe(false);
  });

  it("mousedown on a menu button is prevented, so the selection survives the click", async () => {
    const { hook, field, window } = setup("Hello world");
    const editor = hook.getEditor();
    editor.commands.focus();
    editor.commands.setTextSelection({ from: 7, to: 12 });
    await wait(window, 300);
    const bold = button(hook.bubble, "bold");
    const down = new window.MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
    });
    bold.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
    expect(editor.state.selection.from).toBe(7);
    expect(editor.state.selection.to).toBe(12);
    bold.click();
    expect(field.value).toBe("Hello **world**");
  });

  it("never suppresses the native context menu, with or without a selection", async () => {
    const { hook, window } = setup("Hello wrold");
    const editor = hook.getEditor();
    editor.commands.focus();
    const menu = () => {
      const event = new window.MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        button: 2,
      });
      editor.view.dom.dispatchEvent(event);
      return event.defaultPrevented;
    };
    editor.commands.setTextSelection(9); // caret inside a misspelt word
    expect(menu()).toBe(false);
    expect(hook.bubble.isConnected).toBe(false);
    editor.commands.setTextSelection({ from: 7, to: 12 });
    await wait(window, 300);
    expect(menu()).toBe(false);
    const onMenu = new window.MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    button(hook.bubble, "bold").dispatchEvent(onMenu);
    expect(onMenu.defaultPrevented).toBe(false);
  });

  it("is keyboard reachable: Alt+F10 in, arrows between buttons, Escape back", async () => {
    const { hook, window, doc } = setup("Hello world");
    const editor = hook.getEditor();
    editor.commands.focus();
    editor.commands.setTextSelection({ from: 1, to: 6 });
    await wait(window, 300);
    const key = (target, k, extra = {}) =>
      target.dispatchEvent(
        new window.KeyboardEvent("keydown", {
          key: k,
          bubbles: true,
          cancelable: true,
          ...extra,
        }),
      );
    key(editor.view.dom, "F10", { altKey: true });
    expect(doc.activeElement).toBe(button(hook.bubble, "bold"));
    key(doc.activeElement, "ArrowRight");
    expect(doc.activeElement).toBe(button(hook.bubble, "italic"));
    key(doc.activeElement, "End");
    expect(doc.activeElement).toBe(button(hook.bubble, "link"));
    key(doc.activeElement, "ArrowRight");
    expect(doc.activeElement).toBe(button(hook.bubble, "bold"));
    // Focus inside the menu keeps it open, even across an editor update
    // (e.g. a table action applied while a menu button holds focus).
    editor.commands.setTextSelection({ from: 1, to: 5 });
    await wait(window, 300);
    expect(doc.activeElement).toBe(button(hook.bubble, "bold"));
    expect(hook.bubble.isConnected).toBe(true);
    editor.commands.setTextSelection({ from: 1, to: 6 });
    key(doc.activeElement, "Escape");
    await wait(window, 50); // Tiptap's focus command applies on the next frame
    expect(doc.activeElement).toBe(editor.view.dom);
    expect(editor.state.selection.from).toBe(1);
    expect(editor.state.selection.to).toBe(6);
  });

  it("Alt+F10 falls back to the toolbar when no menu is open", () => {
    const { hook, window, doc } = setup("Hello world");
    const editor = hook.getEditor();
    editor.commands.focus();
    editor.commands.setTextSelection(3);
    expect(hook.bubble.isConnected).toBe(false);
    editor.view.dom.dispatchEvent(
      new window.KeyboardEvent("keydown", {
        key: "F10",
        altKey: true,
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(hook.toolbar.contains(doc.activeElement)).toBe(true);
    expect(doc.activeElement.disabled).toBe(false);
  });

  it("closes when keyboard focus leaves the menu for the page", async () => {
    const { hook, window, doc } = setup("Hello world");
    const editor = hook.getEditor();
    editor.commands.focus();
    editor.commands.setTextSelection({ from: 1, to: 6 });
    await wait(window, 300);
    button(hook.bubble, "bold").focus();
    doc.querySelector("label").setAttribute("tabindex", "0");
    doc.querySelector("label").focus();
    expect(hook.bubble.isConnected).toBe(false);
  });

  it('bubble_menu=false (data-bubble-menu="false") builds no bubble menu', () => {
    const { hook, doc } = setup("Hello", ' data-bubble-menu="false"');
    expect(hook.bubble).toBeUndefined();
    expect(doc.querySelector(".dj-markdown-bubble")).toBeNull();
  });

  it("the floating menu is opt-in and shows on an empty top-level line", async () => {
    let { hook } = setup("Hello");
    expect(hook.floating).toBeUndefined();
    hook.destroyed();

    let window;
    ({ hook, window } = setup("Hello\n\nWorld", ' data-floating-menu="true"'));
    const editor = hook.getEditor();
    const floating = hook.floating;
    expect(floating.getAttribute("role")).toBe("toolbar");
    expect(floating.getAttribute("aria-label")).toBe("Insert block");
    expect(
      [...floating.querySelectorAll("[data-markdown-action]")].map(
        (b) => b.dataset.markdownAction,
      ),
    ).toEqual([
      "heading",
      "quote",
      "list",
      "ordered",
      "task",
      "block",
      "table",
    ]);
    editor.commands.focus();
    editor.commands.setTextSelection(3);
    await wait(window, 300);
    expect(floating.isConnected).toBe(false);
    editor.commands.setTextSelection(6); // end of "Hello"
    editor.commands.splitBlock();
    await wait(window, 300);
    expect(floating.isConnected).toBe(true);
  });

  it("data-actions limits the menus as it limits the toolbar", () => {
    const { hook } = setup("Hello", ' data-actions="bold row-after"');
    expect(
      [...hook.bubble.querySelectorAll("[data-markdown-action]")].map(
        (b) => b.dataset.markdownAction,
      ),
    ).toEqual(["bold", "row-after"]);
  });

  it("destroy removes the menus with the editor", async () => {
    const { hook, window, doc } = setup("Hello world");
    const editor = hook.getEditor();
    editor.commands.focus();
    editor.commands.setTextSelection({ from: 1, to: 6 });
    await wait(window, 300);
    expect(doc.querySelector(".dj-markdown-bubble")).not.toBeNull();
    hook.destroyed();
    expect(doc.querySelector(".dj-markdown-bubble")).toBeNull();
    expect(editor.isDestroyed).toBe(true);
  });
});

describe("#3107 window.djust.getHook and MarkdownEditor.getEditor", () => {
  it("returns the mounted instance for an element or selector, else null", () => {
    const { window, doc, el, hook } = setup("Hello", "", { client: true });
    expect(hook).not.toBeNull();
    expect(hook.el).toBe(el);
    expect(window.djust.getHook('[dj-hook="MarkdownEditor"]')).toBe(hook);
    expect(window.djust.getHook(doc.querySelector("form"))).toBeNull();
    expect(window.djust.getHook(null)).toBeNull();
    expect(window.djust.getHook("#missing")).toBeNull();
  });

  it("reaches the live Tiptap editor through getEditor()", () => {
    const { window, el, field } = setup("Hello", "", { client: true });
    const editor = window.djust.getHook(el).getEditor();
    expect(typeof editor.chain).toBe("function");
    expect(editor.isActive("table")).toBe(false);
    editor
      .chain()
      .focus()
      .setTextSelection({ from: 1, to: 6 })
      .toggleBold()
      .run();
    expect(field.value).toBe("**Hello**");
  });

  it("getEditor() is null until Visual mode has been entered", () => {
    const dom = new JSDOM(
      '<form><textarea name="body" data-markdown-editor="markdown"></textarea><div dj-hook="MarkdownEditor" dj-update="ignore" data-field="body"></div></form>',
      { runScripts: "outside-only", pretendToBeVisual: true },
    );
    dom.window.eval(visualSource);
    dom.window.eval(hookSource);
    const plain = {
      ...dom.window.djust.hooks.MarkdownEditor,
      el: dom.window.document.querySelector("[dj-hook]"),
    };
    plain.mounted();
    expect(plain.mode).toBe("markdown");
    expect(plain.getEditor()).toBeNull();
    plain.setMode("visual");
    expect(plain.getEditor()).toBe(plain.visual.editor);
  });

  it("returns null once the hook is destroyed", () => {
    const { window, doc, el } = setup("Hello", "", { client: true });
    el.remove();
    window.djust.updateHooks(doc);
    expect(window.djust.getHook(el)).toBeNull();
  });
});
