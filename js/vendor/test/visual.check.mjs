import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
const dom = new JSDOM("<!doctype html><main></main>", {
  url: "http://localhost",
  pretendToBeVisual: true,
  runScripts: "outside-only",
});
for (const name of [
  "window",
  "document",
  "navigator",
  "HTMLElement",
  "Element",
  "Node",
  "MutationObserver",
  "DOMParser",
  "getComputedStyle",
  "requestAnimationFrame",
  "cancelAnimationFrame",
])
  Object.defineProperty(globalThis, name, {
    value: dom.window[name],
    configurable: true,
  });
window.eval(
  readFileSync(
    "../../python/djust/components/static/djust_components/markdown-visual.js",
    "utf8",
  ),
);
const hookSource = readFileSync(
  "../../python/djust/components/static/djust_components/markdown-editor.js",
  "utf8",
);
window.eval(hookSource);
function setup(value = "") {
  document.querySelector("main").innerHTML =
    '<form><label for="body">Body</label><textarea id="body" name="body" dj-input="validate_field"></textarea><div dj-hook="MarkdownEditor" dj-update="ignore" data-field="body" data-mode="visual"></div></form>';
  const field = document.querySelector("textarea");
  field.value = value;
  const hook = {
    ...window.djust.hooks.MarkdownEditor,
    el: document.querySelector("[dj-hook]"),
  };
  hook.mounted();
  return { field, hook };
}
test("entering and leaving Visual preserves original Markdown bytes until an edit", () => {
  const original = "## Hello\n\nA __bold__ paragraph.\n\n* first\n* second\n";
  const { field, hook } = setup(original);
  assert.equal(hook.mode, "visual");
  assert.match(hook.surface.innerHTML, /<strong>bold<\/strong>/);
  hook.setMode("markdown");
  assert.equal(field.value, original);
  hook.setMode("visual");
  assert.equal(field.value, original);
  hook.destroyed();
});
test("visual edits update the native field, FormData and transport once", () => {
  const { field, hook } = setup("Hello");
  let events = 0;
  field.addEventListener("input", () => events++);
  hook.visual.editor.commands.setTextSelection({ from: 1, to: 6 });
  hook.visual.editor.commands.toggleBold();
  assert.equal(field.value, "**Hello**");
  assert.equal(new window.FormData(field.form).get("body"), "**Hello**");
  assert.equal(events, 1);
  hook.destroyed();
});
test("undo and redo restore source through normal input events", () => {
  const { field, hook } = setup("Hello");
  hook.visual.editor.commands.setTextSelection({ from: 1, to: 6 });
  hook.visual.editor.commands.toggleBold();
  hook.visual.editor.commands.undo();
  assert.equal(field.value, "Hello");
  hook.visual.editor.commands.redo();
  assert.equal(field.value, "**Hello**");
  hook.destroyed();
});
test("headings, nested lists, tables, tasks, images and fenced code survive serialization", () => {
  const sources = [
    "First line  \nSecond line",
    "## Heading\n\n- first\n  - nested",
    "| A | B |\n| --- | --- |\n| x | y |",
    "- [x] Done\n- [ ] Todo",
    "![alt](https://example.com/a.png)",
    '```python\nprint("hello")\n```',
  ];
  for (const source of sources) {
    const { hook } = setup(source);
    assert.equal(hook.mode, "visual", source + " " + hook.status.textContent);
    const first = hook.visual.editor.getJSON();
    const result = hook.visual.value();
    assert.equal(hook.visual.load(result), "");
    assert.deepEqual(hook.visual.editor.getJSON(), first);
    hook.destroyed();
  }
});
test("unsupported Markdown stays editable in source without silent loss", () => {
  for (const source of [
    "<details>keep me</details>",
    "Hello[^1]\n\n[^1]: footnote",
    ":::custom\nkeep\n:::",
    "[bad](javascript:alert(1))",
  ]) {
    const { field, hook } = setup(source);
    assert.equal(hook.mode, "markdown");
    assert.equal(field.value, source);
    assert.ok(hook.status.textContent);
    assert.equal(hook.surface.hidden, true);
    hook.destroyed();
  }
});
test("focused visual draft survives an older server value without replacing editor or history", () => {
  const { field, hook } = setup("Hello");
  hook.visual.editor.commands.insertContent("Local ");
  hook.visual.editor.view.focus();
  const draft = field.value,
    editor = hook.visual.editor;
  field.value = "Hello";
  hook.updated();
  assert.equal(field.value, draft);
  assert.equal(hook.visual.editor, editor);
  hook.destroyed();
});
test("server resets while unfocused sync to Visual and readonly prevents edits", () => {
  const { field, hook } = setup("Hello");
  field.value = "Server reset";
  field.readOnly = true;
  hook.updated();
  assert.equal(hook.visual.editor.getText(), "Server reset");
  assert.equal(hook.visual.editor.isEditable, false);
  hook.destroyed();
});
test("native required-field invalid events reveal the original textarea", () => {
  const { field, hook } = setup("");
  field.required = true;
  field.dispatchEvent(new window.Event("invalid"));
  assert.equal(hook.mode, "markdown");
  assert.equal(field.classList.contains("dj-markdown-source-hidden"), false);
  hook.destroyed();
});
test("schema strips executable HTML and unsafe pasted links", () => {
  const { hook } = setup("");
  hook.visual.editor.commands.setContent(
    '<p onclick="bad()">hello</p><script>bad()</script><a href="javascript:bad()">link</a><img src="https://example.com/a.png" onerror="bad()">',
    { contentType: "html" },
  );
  assert.equal(hook.surface.querySelector("script,[onclick],[onerror]"), null);
  assert.equal(hook.surface.querySelector('a[href^="javascript:"]'), null);
  hook.destroyed();
});
test("HTML and custom syntax inside fenced code remains editable visually", () => {
  const source =
    "```text\n<details>literal</details>\n:::literal\n$$literal\n```";
  const { field, hook } = setup(source);
  assert.equal(hook.mode, "visual");
  assert.equal(field.value, source);
  assert.equal(hook.surface.querySelector("details"), null);
  hook.destroyed();
});
