import { Editor, Extension } from "@tiptap/core";
import { Plugin } from "@tiptap/pm/state";
import { Fragment, Slice } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import {
  Table,
  TableCell,
  TableHeader,
  TableKit,
  renderTableToMarkdown,
} from "@tiptap/extension-table";
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
// A GFM table cell holds one line of inline content. Restricting the cell
// schema to a single paragraph makes every path that could put a block in a
// cell (toolbar, shortcuts, input rules, paste, a selection spanning a table)
// fail at the schema instead of writing Markdown that reloads differently.
// The Markdown parser already builds exactly one paragraph per cell.
const CELL = { content: "paragraph" };
// The schema alone cannot stop ProseMirror from *fitting* a block into a
// cell by splitting the table in two around it (an `---` input rule, an
// inserted heading). Such a step starts or ends inside a cell and leaves more
// tables than it found; refuse it. Paste is flattened first (below), so a
// pasted block lands in the cell as text instead of being refused.
const inCell = ($pos) => {
  for (let depth = $pos.depth; depth > 0; depth--)
    if (/cell/.test($pos.node(depth).type.spec.tableRole || "")) return true;
  return false;
};
const countTables = (doc) => {
  let count = 0;
  doc.descendants((node) => {
    if (node.type.name === "table") count++;
  });
  return count;
};
const splitsTable = (tr) =>
  tr.steps.some((step, i) => {
    const before = tr.docs[i];
    if (typeof step.from !== "number") return false;
    if (!inCell(before.resolve(step.from)) && !inCell(before.resolve(step.to)))
      return false;
    const after = i + 1 < tr.docs.length ? tr.docs[i + 1] : tr.doc;
    return countTables(after) > countTables(before);
  });
const CellsStayInline = Extension.create({
  name: "djustCellsStayInline",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        filterTransaction: (tr) => !tr.docChanged || !splitsTable(tr),
        props: {
          // Paste into a cell: keep the text and inline marks, join the
          // pasted blocks with line breaks (written as <br> in the cell).
          transformPasted(slice, view) {
            if (!inCell(view.state.selection.$from)) return slice;
            const { schema } = view.state;
            const inline = [];
            slice.content.descendants((node) => {
              if (!node.isTextblock) return true;
              if (inline.length) inline.push(schema.nodes.hardBreak.create());
              node.forEach((child) => inline.push(child));
              return false;
            });
            if (!inline.length) return slice;
            const paragraph = schema.nodes.paragraph.create(null, inline);
            return new Slice(Fragment.from(paragraph), 1, 1);
          },
        },
      }),
    ];
  },
});
// A `|` in cell text must be written `\|`, or the next load splits the cell.
// Only a pipe after an even number of backslashes is a delimiter, so escape
// exactly those.
export const escapeCellPipes = (text) =>
  text.replace(/(^|[^\\])((?:\\\\)*)\|/g, "$1$2\\|");
const GfmTable = Table.extend({
  renderMarkdown(node, h) {
    const cellHelpers = {
      ...h,
      renderChildren: (...args) => escapeCellPipes(h.renderChildren(...args)),
    };
    // Upstream pads a table with extra blank lines; the block joiner already
    // separates blocks with one.
    return renderTableToMarkdown(node, cellHelpers).replace(/^\n+|\n+$/g, "");
  },
});
// Menus use fixed positioning so the editor frame's overflow (components.css
// .dj-md-editor) cannot clip them. `boundary` is the visual surface: the
// selection menu flips below a selection when above it would leave the
// surface (and cover the toolbar), and a menu hides when its anchor scrolls
// out of the surface.
const SHIFT = { padding: 8 };
const extensions = (menus = {}) => [
  StarterKit.configure({ underline: false, link: { openOnClick: false } }),
  Markdown,
  TableKit.configure({ table: false, tableCell: false, tableHeader: false }),
  GfmTable,
  TableCell.extend(CELL),
  TableHeader.extend(CELL),
  CellsStayInline,
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
            flip: { boundary: menus.boundary },
            shift: SHIFT,
            hide: { boundary: menus.boundary },
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
            shift: SHIFT,
            hide: { boundary: menus.boundary },
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

// The table around the selection, or null.
function currentTable(editor) {
  const { $from } = editor.state.selection;
  for (let depth = $from.depth; depth > 0; depth--) {
    const node = $from.node(depth);
    if (node.type.name === "table") return node;
  }
  return null;
}
// A GFM table always has a header row, so "header-row" can only add one (the
// case after the old header row was deleted); removing it would write an
// empty header row that reappears on the next load.
function hasHeaderRow(editor) {
  const table = currentTable(editor);
  return table?.firstChild?.firstChild?.type.name === "tableHeader";
}
function command(editor, chain, action, href) {
  const table = currentTable(editor);
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
      // No nested tables. can() evaluates commands without dispatching, so
      // it cannot see the CellsStayInline filter; say so here.
      return table
        ? null
        : chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true });
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
      // prosemirror-tables reports the last column as deletable but then
      // leaves the table unchanged.
      return table && table.firstChild.childCount > 1
        ? chain.deleteColumn()
        : null;
    case "header-row":
      return table && !hasHeaderRow(editor) ? chain.toggleHeaderRow() : null;
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
    extensions: extensions({ boundary: element, ...menus }),
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
  // The editor scrolls inside its own fixed-height box, and a scroll event
  // does not bubble to the window the menus listen on. Reposition (or, via
  // the `hide` middleware, hide) the open menus on that inner scroll too.
  let frame = 0;
  const onScroll = () => {
    if (frame || editor.isDestroyed) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (editor.isDestroyed) return;
      editor.view.dispatch(
        editor.state.tr
          .setMeta("bubbleMenu", "updatePosition")
          .setMeta("floatingMenu", "updatePosition"),
      );
    });
  };
  if (menus.bubble || menus.floating)
    editor.view.dom.addEventListener("scroll", onScroll, { passive: true });
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
    destroy() {
      editor.view.dom.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
      editor.destroy();
    },
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
