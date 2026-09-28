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
window.ClipboardEvent ??= class extends window.Event {};
window.Range.prototype.getClientRects ??= () => [];
window.Range.prototype.getBoundingClientRect ??= () => new window.DOMRect();
const hookSource = readFileSync(
  "../../python/djust/components/static/djust_components/markdown-editor.js",
  "utf8",
);
window.eval(hookSource);
function setup(value = "", { preserveComments = false } = {}) {
  document.querySelector("main").innerHTML =
    `<form><label for="body">Body</label><textarea id="body" name="body" dj-input="validate_field"></textarea><div dj-hook="MarkdownEditor" dj-update="ignore" data-field="body" data-mode="visual" data-preserve-comments="${preserveComments}"></div></form>`;
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
    "Before <!-- default stays in Markdown --> after.",
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
test("block HTML comments survive visual edits in their original order", () => {
  const source =
    "# Draft\n\nBefore.\n\n<!-- internal:start -->\n\nPrivate.\n\n<!-- internal:end -->\n\nAfter.";
  const { field, hook } = setup(source, { preserveComments: true });
  assert.equal(hook.mode, "visual", hook.status.textContent);
  assert.equal(field.value, source);
  assert.equal(hook.surface.querySelectorAll(".dj-md-comment").length, 2);
  hook.visual.editor.commands.setTextSelection(3);
  hook.visual.editor.commands.insertContent("Revised ");
  const saved = field.value;
  assert.equal((saved.match(/<!-- internal:start -->/g) || []).length, 1);
  assert.equal((saved.match(/<!-- internal:end -->/g) || []).length, 1);
  assert.ok(
    saved.indexOf("<!-- internal:start -->") < saved.indexOf("Private."),
  );
  assert.ok(saved.indexOf("Private.") < saved.indexOf("<!-- internal:end -->"));
  assert.equal(hook.visual.load(saved), "");
  hook.destroyed();
});
test("inline HTML comments retain exact bytes between edited words", () => {
  const source = "Before <!-- reviewer: keep  two spaces --> after.";
  const { field, hook } = setup(source, { preserveComments: true });
  assert.equal(hook.mode, "visual", hook.status.textContent);
  assert.equal(field.value, source);
  hook.visual.editor.commands.setTextSelection(2);
  hook.visual.editor.commands.insertContent("X");
  assert.equal(
    field.value,
    "BXefore <!-- reviewer: keep  two spaces --> after.",
  );
  assert.equal(hook.visual.load(field.value), "");
  hook.destroyed();
});
test("inline then block comments remain separate visual tokens", () => {
  const source = "Before <!-- inline --> after.\n\n<!-- block -->\n\nEnd.";
  const { field, hook } = setup(source, { preserveComments: true });
  assert.equal(hook.mode, "visual", hook.status.textContent);
  assert.equal(hook.surface.querySelectorAll("span.dj-md-comment").length, 1);
  assert.equal(hook.surface.querySelectorAll("div.dj-md-comment").length, 1);
  hook.visual.editor.commands.setTextSelection(2);
  hook.visual.editor.commands.insertContent("X");
  assert.ok(field.value.includes("<!-- inline -->"));
  assert.ok(field.value.includes("<!-- block -->"));
  hook.destroyed();
});
test("opt-in comments do not allow other HTML or incomplete comments", () => {
  for (const source of [
    "<!-- unfinished",
    "<!-- safe --><script>alert(1)</script>",
    "<details>still unsupported</details>",
  ]) {
    const { field, hook } = setup(source, { preserveComments: true });
    assert.equal(hook.mode, "markdown", source);
    assert.equal(field.value, source);
    hook.destroyed();
  }
});
test("HTML comments in fenced code stay literal code", () => {
  const source = "```html\n<!-- literal -->\n```";
  const { hook } = setup(source, { preserveComments: true });
  assert.equal(hook.mode, "visual");
  assert.equal(hook.surface.querySelectorAll(".dj-md-comment").length, 0);
  hook.destroyed();
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
const cellPos = (editor, text) => {
  let found = null;
  editor.state.doc.descendants((node, pos) => {
    if (found === null && node.isText && node.text === text) found = pos;
  });
  return found;
};
test("table commands keep a GFM table that reloads to the same document (#3107)", () => {
  const source = "| A | B |\n| --- | --- |\n| x | y |";
  for (const action of [
    "row-before",
    "row-after",
    "row-delete",
    "column-before",
    "column-after",
    "column-delete",
    "table-delete",
  ]) {
    const { hook } = setup(source);
    const visual = hook.visual;
    visual.editor.commands.setTextSelection(cellPos(visual.editor, "x"));
    assert.equal(visual.inTable(), true);
    assert.equal(visual.can(action), true, action);
    assert.equal(visual.format(action), true, action);
    const json = visual.editor.getJSON();
    assert.equal(visual.load(visual.value()), "", action);
    assert.deepEqual(visual.editor.getJSON(), json, action);
    hook.destroyed();
  }
});
test("block formats and header removal are refused inside a table cell", () => {
  const { hook } = setup("| A | B |\n| --- | --- |\n| x | y |");
  const visual = hook.visual;
  visual.editor.commands.setTextSelection(cellPos(visual.editor, "x"));
  for (const action of [
    "heading",
    "quote",
    "list",
    "ordered",
    "task",
    "block",
    "table",
    "header-row",
  ]) {
    assert.equal(visual.can(action), false, action);
    assert.equal(visual.format(action), false, action);
  }
  assert.equal(visual.hasHeaderRow(), true);
  assert.equal(visual.can("bold"), true);
  hook.destroyed();
});
test("a hard break in a cell reloads visually; raw HTML elsewhere still does not", () => {
  const { hook } = setup("");
  assert.equal(
    hook.visual.load("| A | B |\n| --- | --- |\n| x<br>z | y |"),
    "",
  );
  assert.equal(
    hook.visual.editor.getJSON().content[0].content[1].content[0].content[0]
      .content[1].type,
    "hardBreak",
  );
  assert.notEqual(hook.visual.load("x<br>z"), "");
  assert.notEqual(hook.visual.load("| A |\n| --- |\n| <br onclick=x> |"), "");
  assert.notEqual(hook.visual.load("| A |\n| --- |\n| <b>x</b> |"), "");
  hook.destroyed();
});
test("createVisual wires the selection and empty-line menu elements", () => {
  const bubble = document.createElement("div");
  const floating = document.createElement("div");
  const host = document.createElement("div");
  document.querySelector("main").appendChild(host);
  const visual = window.djust.createMarkdownVisual(
    host,
    "",
    () => {},
    "Body",
    () => {},
    {
      bubble,
      floating,
    },
  );
  const keys = visual.editor.state.plugins.map((plugin) => plugin.key);
  assert.ok(
    keys.some((key) => key.startsWith("bubbleMenu")),
    keys.join(" "),
  );
  assert.ok(
    keys.some((key) => key.startsWith("floatingMenu")),
    keys.join(" "),
  );
  visual.destroy();
  const plain = window.djust.createMarkdownVisual(host, "", () => {}, "Body");
  assert.ok(
    !plain.editor.state.plugins.some((plugin) => /Menu/.test(plugin.key)),
  );
  plain.destroy();
});
test("a | in cell text is escaped and reloads into the same cell", () => {
  const { hook } = setup("| A | B |\n| --- | --- |\n| x | y |");
  const visual = hook.visual;
  visual.editor.commands.setTextSelection(cellPos(visual.editor, "x") + 1);
  visual.editor.commands.insertContent("a|b");
  const md = visual.value();
  assert.match(md, /\| xa\\\|b \| y +\|/);
  const json = visual.editor.getJSON();
  assert.equal(visual.load(md), "");
  assert.deepEqual(visual.editor.getJSON(), json);
  hook.destroyed();
});
test("cells hold one paragraph: block commands cannot split a table", () => {
  const source = "| A | B |\n| --- | --- |\n| x | y |";
  for (const run of [
    (c) => c.setHeading({ level: 2 }),
    (c) => c.toggleBulletList(),
    (c) => c.setCodeBlock(),
    (c) => c.setHorizontalRule(),
    (c) => c.insertTable({ rows: 2, cols: 2 }),
  ]) {
    const { hook } = setup(source);
    const { editor } = hook.visual;
    editor.commands.setTextSelection(cellPos(editor, "x") + 1);
    run(editor.chain());
    const tables = editor.getJSON().content.filter((n) => n.type === "table");
    assert.equal(tables.length, 1, String(run));
    assert.equal(hook.visual.load(hook.visual.value()), "", String(run));
    hook.destroyed();
  }
});

const imagePos = (editor) => {
  let at = null;
  editor.state.doc.descendants((node, pos) => {
    if (at === null && /^(image|cellImage)$/.test(node.type.name)) at = pos;
  });
  assert.notEqual(at, null, "the image must be present before typing");
  return at;
};
const assertImageRoundTrip = (hook, expected) => {
  assert.equal(hook.visual.value(), expected);
  const before = hook.visual.editor.getJSON();
  assert.doesNotThrow(() => hook.visual.editor.state.doc.check());
  assert.equal(hook.visual.load(expected), "");
  assert.deepEqual(hook.visual.editor.getJSON(), before);
  assert.equal(hook.visual.value(), expected);
};

test("#3261 typing on either side preserves paragraph and nested inline images", () => {
  for (const wrap of [
    (s) => s,
    (s) => `## ${s}`,
    (s) => `> ${s}`,
    (s) => `- ${s}`,
  ]) {
    const image = '![a](/x.png "image title")';
    const { hook, field } = setup(wrap(`text ${image} text`) + "\n\nEnd");
    const editor = hook.visual.editor;
    const at = imagePos(editor);
    editor.view.dispatch(editor.state.tr.insertText("L", at));
    editor.view.dispatch(editor.state.tr.insertText("R", at + 2));
    const expected = wrap(`text L${image}R text`) + "\n\nEnd";
    assert.equal(field.value, expected);
    assertImageRoundTrip(hook, expected);
    hook.destroyed();
  }
});

test("#3261 image links and emphasis survive edits and serialization", () => {
  for (const image of [
    '[![a](/x.png "image title")](/y "link title")',
    "**![a](/x.png)**",
    "*![a](/x.png)*",
    "~~![a](/x.png)~~",
    "[**![a](/x.png)**](/y)",
  ]) {
    const { hook } = setup(`before ${image} after`);
    const editor = hook.visual.editor;
    editor.view.dispatch(
      editor.state.tr.insertText("!", editor.state.doc.content.size - 1),
    );
    assertImageRoundTrip(hook, `before ${image} after!`);
    hook.destroyed();
  }
});

test("#3261 marks spanning text and images survive save and reload", () => {
  for (const source of [
    "**left ![a](/x.png) right**",
    "[left ![a](/x.png) right](/y)",
    "**left [![a](/x.png)](/y) right**",
  ]) {
    const { hook } = setup(source);
    const before = hook.visual.editor.getJSON();
    assert.equal(hook.visual.load(hook.visual.value()), "");
    assert.deepEqual(hook.visual.editor.getJSON(), before);
    const image = [];
    hook.visual.editor.state.doc.descendants((n) => {
      if (n.type.name === "image") image.push(n);
    });
    assert.equal(image.length, 1);
    assert.ok(image[0].marks.length, "image retains the surrounding mark");
    hook.destroyed();
  }
});

test("#3261 copying marked text and an image out of a table retains content after typing", () => {
  const content = 'left [**![a](/x.png "title")**](/y) right';
  const { hook } = setup(`| H |\n| --- |\n| ${content} |\n\nTarget `);
  const editor = hook.visual.editor;
  const at = imagePos(editor);
  const { dom } = editor.view.serializeForClipboard(
    editor.state.doc.slice(at - 5, at + 7),
  );
  editor.commands.setTextSelection(editor.state.doc.content.size - 1);
  editor.view.pasteHTML(dom.innerHTML);
  editor.view.dispatch(editor.state.tr.insertText("!"));
  const expected = hook.visual.value();
  assert.ok(expected.endsWith(`Target ${content}!`), expected);
  assert.equal((expected.match(/!\[a\]/g) || []).length, 2);
  assertImageRoundTrip(hook, expected);
  hook.destroyed();
});

test("#3261 HTML image paste and toolbar marks retain attributes and reject data URLs", () => {
  const { hook } = setup("before ");
  const editor = hook.visual.editor;
  editor.commands.setTextSelection(8);
  editor.view.pasteHTML(
    '<img src="/x.png" alt="a" title="title"><img src="data:image/png;base64,AAAA">',
  );
  const at = imagePos(editor);
  editor.commands.setTextSelection({ from: at, to: at + 1 });
  editor.commands.toggleBold();
  editor.commands.setLink({ href: "/y" });
  editor.commands.setTextSelection(at + 1);
  editor.view.dispatch(editor.state.tr.insertText("!"));
  const expected = hook.visual.value();
  assert.equal(expected, 'before [**![a](/x.png "title")!**](/y)');
  assert.equal(hook.surface.querySelectorAll("img").length, 1);
  assertImageRoundTrip(hook, expected);
  hook.destroyed();
});

test("#3261 dropping an HTML image into paragraph or table keeps it on the next keystroke", () => {
  for (const source of ["before after", "| H |\n| --- |\n| before after |"]) {
    const { hook } = setup(source);
    const editor = hook.visual.editor;
    let at;
    editor.state.doc.descendants((node, pos) => {
      if (node.isText && node.text === "before after") at = pos + 7;
    });
    editor.view.posAtCoords = () => ({ pos: at, inside: -1 });
    const event = new window.MouseEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 10,
      clientY: 10,
    });
    Object.defineProperty(event, "dataTransfer", {
      value: {
        getData: (type) =>
          type === "text/html" ? '<img src="/x.png" alt="a">' : "",
        files: [],
      },
    });
    editor.view.dom.dispatchEvent(event);
    assert.ok(event.defaultPrevented);
    editor.view.dispatch(editor.state.tr.insertText("!"));
    const expected = hook.visual.value();
    assert.ok(expected.includes("before ![a](/x.png)!after"), expected);
    assertImageRoundTrip(hook, expected);
    hook.destroyed();
  }
});

test("#3261 serialization placeholders cannot replace user text or image titles", () => {
  const source = '\uE0000\uE000 **![a](/x.png "$& title")** \uE000';
  const { hook } = setup(source);
  assertImageRoundTrip(hook, source);
  hook.destroyed();
});

test("#3261 image commands and Markdown input rules insert valid inline images", () => {
  for (const source of ["text", "| H |\n| --- |\n| text |"]) {
    const { hook } = setup(source);
    const editor = hook.visual.editor;
    let at;
    editor.state.doc.descendants((node, pos) => {
      if (node.isText && node.text === "text") at = pos + 4;
    });
    editor.commands.setTextSelection(at);
    assert.equal(editor.commands.setImage({ src: "/x.png", alt: "a" }), true);
    editor.view.dispatch(editor.state.tr.insertText("!"));
    const expected = hook.visual.value();
    assert.ok(expected.includes("text![a](/x.png)!"), expected);
    assertImageRoundTrip(hook, expected);
    hook.destroyed();
  }
  const { hook } = setup("![a](/x.png");
  const editor = hook.visual.editor;
  const end = editor.state.doc.content.size - 1;
  editor.commands.setTextSelection(end);
  assert.ok(
    editor.view.someProp("handleTextInput", (handler) =>
      handler(editor.view, end, end, ")", () =>
        editor.state.tr.insertText(")"),
      ),
    ),
  );
  assertImageRoundTrip(hook, "![a](/x.png)");
  hook.destroyed();
});

test("#3261 inline code formatting cannot turn an image into literal Markdown", () => {
  const { hook } = setup("text ![a](/x.png) text");
  const editor = hook.visual.editor;
  const at = imagePos(editor);
  editor.commands.setTextSelection({ from: at, to: at + 1 });
  editor.commands.toggleCode();
  assertImageRoundTrip(hook, "text ![a](/x.png) text");
  editor.commands.setTextSelection(editor.state.doc.content.size - 1);
  editor.view.pasteHTML('<code><img src="/pasted.png" alt="pasted"></code>');
  assertImageRoundTrip(hook, "text ![a](/x.png) text![pasted](/pasted.png)");
  hook.destroyed();
});
