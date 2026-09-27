import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import { TableKit } from "@tiptap/extension-table";
import Image from "@tiptap/extension-image";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import BubbleMenu from "@tiptap/extension-bubble-menu";
import FloatingMenu from "@tiptap/extension-floating-menu";

// The editor or its menu holds focus. A menu that holds keyboard focus
// stays open, so Tab/arrow navigation into it does not dismiss it.
const focused = (view, element) =>
  view.hasFocus() || element.contains(document.activeElement);
// Shown over a selection with text in it, or a table cell selection. A caret
// (empty selection) or a whitespace-only selection has nothing to format.
const bubbleShouldShow =
  (element) =>
  ({ editor, view, state, from, to }) => {
    const { selection } = state;
    if (!focused(view, element) || !editor.isEditable) return false;
    return (
      "$anchorCell" in selection ||
      state.doc.textBetween(from, to, " ").trim() !== ""
    );
  };
// Shown on an empty top-level paragraph, as upstream, plus while focused.
const floatingShouldShow =
  (element) =>
  ({ editor, view, state }) => {
    const { $anchor, empty } = state.selection;
    return (
      focused(view, element) &&
      editor.isEditable &&
      empty &&
      $anchor.depth === 1 &&
      $anchor.parent.isTextblock &&
      !$anchor.parent.type.spec.code &&
      $anchor.parent.childCount === 0
    );
  };
// Menus use fixed positioning so the editor frame's overflow (components.css
// .dj-md-editor) cannot clip them.
const extensions = (menus = {}) => [
  StarterKit.configure({ underline: false, link: { openOnClick: false } }),
  Markdown,
  TableKit,
  Image.configure({ allowBase64: false }),
  TaskList,
  TaskItem.configure({ nested: true }),
  ...(menus.bubble
    ? [
        BubbleMenu.configure({
          element: menus.bubble,
          shouldShow: bubbleShouldShow(menus.bubble),
          options: {
            strategy: "fixed",
            placement: "top",
            flip: {},
            shift: { padding: 8 },
          },
        }),
      ]
    : []),
  ...(menus.floating
    ? [
        FloatingMenu.configure({
          element: menus.floating,
          shouldShow: floatingShouldShow(menus.floating),
          options: {
            strategy: "fixed",
            placement: "left",
            flip: {},
            shift: { padding: 8 },
          },
        }),
      ]
    : []),
];
const supported = new Set([
  "space",
  "code",
  "heading",
  "hr",
  "blockquote",
  "list",
  "list_item",
  "paragraph",
  "text",
  "escape",
  "strong",
  "em",
  "codespan",
  "br",
  "del",
  "link",
  "image",
  "table",
  "taskList",
  "taskItem",
]);
export function unsupported(editor, source) {
  let reason = "";
  const visit = (tokens, inCell = false) => {
    for (const token of tokens || []) {
      // A hard break inside a GFM table cell is written as <br> (the only
      // line-break spelling a table row has) and parses back to a hard break.
      const cellBreak =
        inCell && token.type === "html" && /^<br\s*\/?>$/i.test(token.raw);
      if (!supported.has(token.type) && !cellBreak)
        reason =
          "This document contains HTML or Markdown extensions that need Markdown mode.";
      if (
        (token.type === "link" || token.type === "image") &&
        !/^(https?:|mailto:|\/|#|\.\.?\/)/i.test(token.href || "")
      )
        reason =
          "This document contains a URL that Visual mode cannot preserve safely.";
      if (
        (token.type === "paragraph" || token.type === "text") &&
        /^\s*(:{3,}|\$\$)/m.test(token.raw || "")
      )
        reason =
          "Directives and math stay in Markdown mode to preserve their source.";
      if (token.tokens) visit(token.tokens, inCell);
      if (token.items) visit(token.items, inCell);
      if (token.header)
        token.header.forEach((cell) => visit(cell.tokens, true));
      if (token.rows)
        token.rows.flat().forEach((cell) => visit(cell.tokens, true));
    }
  };
  const tokens = editor.markdown.instance.lexer(source);
  visit(tokens);
  if (Object.keys(tokens.links || {}).some((key) => key.startsWith("^")))
    reason = "Footnotes stay in Markdown mode to preserve their source.";
  return reason;
}

// A GFM table cell holds inline content only. A block inside a cell (heading,
// list, quote, code block, nested table) has no Markdown table spelling, so
// these actions are unavailable while the selection is inside a table.
const BLOCK_ACTIONS = new Set([
  "heading",
  "quote",
  "list",
  "ordered",
  "task",
  "block",
  "table",
]);
// A GFM table always has a header row, so "header-row" can only add one (the
// case after the old header row was deleted); removing it would write an
// empty header row that reappears on the next load.
function hasHeaderRow(editor) {
  const { $from } = editor.state.selection;
  for (let depth = $from.depth; depth > 0; depth--) {
    const node = $from.node(depth);
    if (node.type.name === "table")
      return node.firstChild?.firstChild?.type.name === "tableHeader";
  }
  return false;
}
function command(editor, chain, action, href) {
  if (BLOCK_ACTIONS.has(action) && editor.isActive("table")) return null;
  switch (action) {
    case "bold":
      return chain.toggleBold();
    case "italic":
      return chain.toggleItalic();
    case "heading":
      return chain.toggleHeading({ level: 2 });
    case "quote":
      return chain.toggleBlockquote();
    case "list":
      return chain.toggleBulletList();
    case "ordered":
      return chain.toggleOrderedList();
    case "task":
      return chain.toggleTaskList();
    case "code":
      return chain.toggleCode();
    case "block":
      return chain.toggleCodeBlock();
    case "link":
      return href
        ? chain.extendMarkRange("link").setLink({ href })
        : chain.unsetLink();
    case "undo":
      return chain.undo();
    case "redo":
      return chain.redo();
    case "table":
      return chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true });
    case "row-before":
      return chain.addRowBefore();
    case "row-after":
      return chain.addRowAfter();
    case "row-delete":
      return chain.deleteRow();
    case "column-before":
      return chain.addColumnBefore();
    case "column-after":
      return chain.addColumnAfter();
    case "column-delete":
      return chain.deleteColumn();
    case "header-row":
      return hasHeaderRow(editor) ? null : chain.toggleHeaderRow();
    case "table-delete":
      return chain.deleteTable();
  }
  return null;
}
export function createVisual(
  element,
  source,
  onChange,
  label,
  onSelection = () => {},
  menus = {},
) {
  const editor = new Editor({
    element,
    injectCSS: false,
    extensions: extensions(menus),
    content: "",
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": label,
      },
    },
    onSelectionUpdate: onSelection,
    onTransaction: onSelection,
    onUpdate: ({ editor }) => onChange(editor.getMarkdown()),
  });
  return {
    editor,
    load(value) {
      const reason = unsupported(editor, value);
      if (reason) return reason;
      editor.commands.setContent(value, {
        contentType: "markdown",
        emitUpdate: false,
      });
      return "";
    },
    value: () => editor.getMarkdown(),
    focus: () => editor.commands.focus(undefined, { scrollIntoView: false }),
    editable: (value) => editor.setEditable(value, false),
    destroy: () => editor.destroy(),
    format(action, href) {
      const chain = command(editor, editor.chain().focus(), action, href);
      return chain ? chain.run() : false;
    },
    // Whether `action` applies at the current selection (editor.can()).
    can(action) {
      const chain = command(editor, editor.can().chain(), action, "#");
      return chain ? chain.run() : false;
    },
    inTable: () => editor.isActive("table"),
    // Close the selection (true) or empty-line (false) menu, e.g. when
    // keyboard focus leaves it for the rest of the page.
    hideMenu(bubble) {
      if (!editor.isDestroyed)
        editor.view.dispatch(
          editor.state.tr.setMeta(
            bubble ? "bubbleMenu" : "floatingMenu",
            "hide",
          ),
        );
    },
    hasHeaderRow: () => hasHeaderRow(editor),
  };
}
window.djust = window.djust || {};
window.djust.createMarkdownVisual = createVisual;
