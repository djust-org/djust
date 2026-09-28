import { Editor, Extension, Node } from "@tiptap/core";
import { Plugin, TextSelection } from "@tiptap/pm/state";
import { DOMParser as PMDOMParser, Fragment, Slice } from "@tiptap/pm/model";
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

// Comments carry authoring metadata (including application-private markers).
// Keep their exact source in an atom rather than parsing them as HTML, where
// ProseMirror would discard them. Only a complete comment is recognized; raw
// tags and an unfinished <!-- still take the Markdown-only path below.
const commentTokenizer = (name, level) => ({
  name,
  level,
  start: (source) => {
    const at = source.indexOf("<!--");
    if (at < 0 || level === "inline") return at;
    const lineStart = source.lastIndexOf("\n", at - 1) + 1;
    return /^[ \t]*$/.test(source.slice(lineStart, at)) ? at : -1;
  },
  tokenize(source) {
    const match = /^<!--[\s\S]*?-->/.exec(source);
    if (!match) return undefined;
    return { type: name, raw: match[0], text: match[0] };
  },
});
const commentNode = (name, level) =>
  Node.create({
    name,
    group: level,
    inline: level === "inline",
    atom: true,
    selectable: false,
    addAttributes: () => ({ source: { default: "" } }),
    parseHTML: () => [
      {
        tag: `[data-dj-md-comment="${level}"]`,
        getAttrs: (element) => {
          const source = element.getAttribute("data-source") || "";
          return /^<!--[\s\S]*?-->$/.test(source) ? { source } : false;
        },
      },
    ],
    renderHTML: ({ node }) => [
      level === "inline" ? "span" : "div",
      {
        class: "dj-md-comment",
        "data-dj-md-comment": level,
        "data-source": node.attrs.source,
        contenteditable: "false",
        title: "HTML comment (edit in Markdown mode)",
      },
      "HTML comment",
    ],
    markdownTokenizer: commentTokenizer(name, level),
    parseMarkdown: (token) => ({ type: name, attrs: { source: token.raw } }),
    renderMarkdown: (node) => node.attrs.source,
  });
const BlockComment = commentNode("htmlCommentBlock", "block");
const InlineComment = commentNode("htmlCommentInline", "inline");

// Markdown images are inline in every text block, including table cells.
// One node model keeps typing and moving content between containers valid.
// Tiptap 3.31's Markdown bridge only applies/serializes marks on text nodes:
// retain image marks while parsing, and serialize each text block through
// its ordinary mark-boundary renderer with temporary image placeholders.
// No placeholders are stored in the editor or exposed to the native field.
const imageMarkdown = (node, helpers, context, render) => {
  if (!node.content?.some((child) => child.type === "image"))
    return render(node, helpers, context);
  let sentinel = "\uE000";
  const source = JSON.stringify(node);
  while (source.includes(sentinel)) sentinel += "\uE000";
  const images = [];
  const content = node.content.map((child) => {
    if (child.type !== "image") return child;
    const token = `${sentinel}${images.length}${sentinel}`;
    images.push([token, Image.config.renderMarkdown(child)]);
    return { type: "text", text: token, marks: child.marks };
  });
  let markdown = render({ ...node, content }, helpers, context);
  for (const [token, image] of images)
    markdown = markdown.replaceAll(token, () => image);
  return markdown;
};
const ImageAwareStarterKit = StarterKit.extend({
  addExtensions() {
    return this.parent().map((extension) => {
      if (["bold", "italic", "strike", "link"].includes(extension.name))
        return extension.extend({
          parseMarkdown(token, helpers) {
            const result = this.parent(token, helpers);
            const mark = {
              type: result.mark,
              ...(result.attrs ? { attrs: result.attrs } : {}),
            };
            return {
              ...result,
              content: result.content.map((node) =>
                node.type === "image"
                  ? { ...node, marks: [...(node.marks || []), mark] }
                  : node,
              ),
            };
          },
        });
      if (["paragraph", "heading"].includes(extension.name))
        return extension.extend({
          parseMarkdown(token, helpers) {
            // Upstream lifts a lone image out of its paragraph as a block.
            if (
              this.name === "paragraph" &&
              token.tokens?.length === 1 &&
              token.tokens[0].type === "image"
            )
              return helpers.createNode(
                "paragraph",
                undefined,
                helpers.parseInline(token.tokens),
              );
            return this.parent(token, helpers);
          },
          renderMarkdown(node, helpers, context) {
            return imageMarkdown(node, helpers, context, this.parent);
          },
        });
      return extension;
    });
  },
});

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
// Inline content of `fragment`: text blocks joined by hard breaks (written as
// <br> in a cell), inline nodes kept with their marks.
function inlineContent(fragment, schema) {
  const out = [];
  let joinNext = false;
  fragment.descendants((node) => {
    if (node.isTextblock) {
      if (out.length) out.push(schema.nodes.hardBreak.create());
      node.forEach((child) => out.push(child));
      joinNext = true;
      return false;
    }
    if (node.isInline) {
      if (joinNext) out.push(schema.nodes.hardBreak.create());
      joinNext = false;
      out.push(node);
      return false;
    }
    return true;
  });
  return out;
}
// HTML (paste, drop, setContent) can put several blocks in a <td>; parse each
// cell as one paragraph instead of letting the parser push the extra blocks
// into new cells (which adds columns and shifts values).
const cellParse = (tag) => () => [
  {
    tag,
    getContent: (dom, schema) =>
      Fragment.from(
        schema.nodes.paragraph.create(
          null,
          inlineContent(
            PMDOMParser.fromSchema(schema).parseSlice(dom).content,
            schema,
          ),
        ),
      ),
  },
];
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
const hasImage = (fragment) => {
  let found = false;
  fragment.descendants((node) => {
    if (node.type.name === "image") found = true;
    return !found;
  });
  return found;
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
const hasTableNodes = (fragment) => {
  let found = false;
  fragment.descendants((node) => {
    if (node.type.spec.tableRole) found = true;
    return !found;
  });
  return found;
};
// The slice as one paragraph of inline content (or unchanged if it has none).
function flatSlice(slice, schema) {
  const inline = inlineContent(slice.content, schema);
  if (!inline.length) return slice;
  return new Slice(
    Fragment.from(schema.nodes.paragraph.create(null, inline)),
    1,
    1,
  );
}
// Mirrors what prosemirror-tables' pastedCells() accepts: after unwrapping
// single open wrappers (or a lone table), only rows or only cells.
function isCellSlice(slice) {
  let { content, openStart, openEnd } = slice;
  while (
    content.childCount === 1 &&
    ((openStart > 0 && openEnd > 0) ||
      content.child(0).type.spec.tableRole === "table")
  ) {
    openStart--;
    openEnd--;
    content = content.child(0).content;
  }
  if (!content.childCount) return false;
  const roles = new Set();
  content.forEach((node) => roles.add(node.type.spec.tableRole || ""));
  return (
    roles.size > 0 &&
    [...roles].every((r) => r === "row" || r === "cell" || r === "header_cell")
  );
}
// Pasted cells take the cell type of the rows they land in: GFM has header
// cells only in a table's first row, so a copied header row pasted into the
// body becomes body cells (and body cells pasted into the header row become
// header cells), which keeps the Markdown reloading to the same table.
function cellTypesFor(fragment, schema, firstRowIsHeader) {
  let row = 0;
  const walk = (frag) => {
    const nodes = [];
    frag.forEach((node) => {
      if (/cell/.test(node.type.spec.tableRole || "")) {
        const type =
          firstRowIsHeader && row === 0
            ? schema.nodes.tableHeader
            : schema.nodes.tableCell;
        nodes.push(type.create(node.attrs, node.content, node.marks));
      } else if (node.type.spec.tableRole === "row") {
        nodes.push(node.copy(walk(node.content)));
        row++;
      } else if (node.childCount && !node.isTextblock) {
        nodes.push(node.copy(walk(node.content)));
      } else nodes.push(node);
    });
    return Fragment.from(nodes);
  };
  return walk(fragment);
}
const inHeaderRow = ($pos) => {
  for (let depth = $pos.depth; depth > 0; depth--)
    if ($pos.node(depth).type.spec.tableRole === "row")
      return $pos.index(depth - 1) === 0;
  return false;
};
const CellsStayInline = Extension.create({
  name: "djustCellsStayInline",
  // Ahead of StarterKit's Enter handling.
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      // A cell holds one paragraph: Enter adds a line break (saved as <br>).
      Enter: () =>
        inCell(this.editor.state.selection.$from) &&
        this.editor.commands.setHardBreak(),
    };
  },
  addProseMirrorPlugins() {
    let view = null;
    let pastedTableHTML = false;
    let dropPos;
    return [
      new Plugin({
        view(editorView) {
          view = editorView;
          return {};
        },
        // A drop selects what it dropped, so the next keystroke would replace
        // it. Leave the caret after dropped images in any paragraph, and
        // after any content dropped in a cell, as a paste does.
        appendTransaction(trs, oldState, state) {
          let tr = null;
          // Code spans contain literal text, never images. Toolbar/keyboard
          // formatting and HTML paste may still apply that mark to an atom;
          // keep the image, removing only the unrepresentable code mark.
          if (trs.some((transaction) => transaction.docChanged))
            state.doc.descendants((node, pos) => {
              if (
                node.type.name === "image" &&
                node.marks.some((mark) => mark.type.name === "code")
              )
                tr = (tr || state.tr).removeMark(
                  pos,
                  pos + node.nodeSize,
                  state.schema.marks.code,
                );
            });
          const { selection } = state;
          if (
            selection.empty ||
            !trs.some((tr) => tr.getMeta("uiEvent") === "drop") ||
            (!inCell(selection.$to) && !hasImage(selection.content().content))
          )
            return tr;
          return (tr || state.tr).setSelection(
            TextSelection.create(state.doc, selection.to),
          );
        },
        filterTransaction(tr, state) {
          if (!tr.docChanged || !splitsTable(tr)) return true;
          // An input rule whose block cannot go in a cell (`---`, a heading)
          // consumed the typed character; type it as plain text instead.
          // (The editor can hold more than one input-rules plugin.)
          const typed = state.plugins
            .filter((p) => p.spec.isInputRules)
            .map((p) => tr.getMeta(p))
            .find(Boolean);
          if (
            view &&
            typed &&
            typeof typed.text === "string" &&
            typed.text !== "\n"
          )
            queueMicrotask(() => {
              if (view.state === state)
                view.dispatch(
                  state.tr.insertText(typed.text, typed.from, typed.to),
                );
            });
          return false;
        },
        props: {
          // Paste into a cell: keep the text and inline marks, join the
          // pasted blocks with line breaks (written as <br> in the cell).
          // Pasted cells or tables go to the table extension's cell paste.
          // Whether the clipboard HTML really held table markup. Pasting
          // several paragraphs into a one-paragraph cell makes the clipboard
          // parser invent cells for them; those are text, not copied cells.
          transformPastedHTML(html) {
            pastedTableHTML = /<(table|tr|td|th)[\s>]/i.test(html);
            return html;
          },
          // A drop lands where the pointer is, not at the selection.
          handleDOMEvents: {
            drop(view, event) {
              dropPos = view.posAtCoords({
                left: event.clientX,
                top: event.clientY,
              })?.pos;
              return false;
            },
          },
          transformPasted(slice, view) {
            const fromTable = pastedTableHTML;
            const at = dropPos;
            pastedTableHTML = false;
            dropPos = undefined;
            // Cells without table markup were invented by the parser for
            // text that did not fit a one-paragraph cell: always flatten.
            if (!fromTable && hasTableNodes(slice.content))
              return flatSlice(slice, view.state.schema);
            const $from =
              at == null
                ? view.state.selection.$from
                : view.state.doc.resolve(at);
            if (!inCell($from)) return slice;
            // Only a pure cells/table slice is a cell paste the table
            // extension accepts; Docs/Word mixes of text and a table are
            // flattened into the cell like any other blocks.
            if (isCellSlice(slice))
              return new Slice(
                cellTypesFor(
                  slice.content,
                  view.state.schema,
                  inHeaderRow($from),
                ),
                slice.openStart,
                slice.openEnd,
              );
            return flatSlice(slice, view.state.schema);
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
  renderMarkdown(node, h, context) {
    const cellHelpers = {
      ...h,
      renderChildren: (...args) => escapeCellPipes(h.renderChildren(...args)),
    };
    const markdown = renderTableToMarkdown(node, cellHelpers);
    // Upstream pads a table with extra blank lines; at the top level the
    // block joiner already separates blocks with one. Inside a list item
    // or quote keep the padding, so the item stays loose (<p>) in previews.
    return context?.parentType && context.parentType !== "doc"
      ? markdown
      : markdown.replace(/^\n+|\n+$/g, "");
  },
});
// Menus use fixed positioning so the editor frame's overflow (components.css
// .dj-md-editor) cannot clip them. `boundary` is the visual surface: the
// selection menu flips below a selection when above it would leave the
// surface (and cover the toolbar), and a menu hides when its anchor scrolls
// out of the surface.
const SHIFT = { padding: 8 };
const extensions = (menus = {}, preserveComments = false) => [
  ImageAwareStarterKit.configure({
    underline: false,
    link: { openOnClick: false },
  }),
  Markdown,
  ...(preserveComments ? [BlockComment, InlineComment] : []),
  TableKit.configure({ table: false, tableCell: false, tableHeader: false }),
  GfmTable,
  TableCell.extend({ ...CELL, parseHTML: cellParse("td") }),
  TableHeader.extend({ ...CELL, parseHTML: cellParse("th") }),
  CellsStayInline,
  Image.configure({ inline: true, allowBase64: false }),
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
export function unsupported(editor, source, preserveComments = false) {
  let reason = "";
  const visit = (tokens, inCell = false) => {
    for (const token of tokens || []) {
      // A hard break inside a GFM table cell is written as <br> (the only
      // line-break spelling a table row has) and parses back to a hard break.
      const cellBreak =
        inCell && token.type === "html" && /^<br\s*\/?>$/i.test(token.raw);
      if (
        !supported.has(token.type) &&
        !(
          preserveComments &&
          ["htmlCommentBlock", "htmlCommentInline"].includes(token.type)
        ) &&
        !cellBreak
      )
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
  options = {},
) {
  const preserveComments = options.preserveComments === true;
  const editor = new Editor({
    element,
    injectCSS: false,
    extensions: extensions({ boundary: element, ...menus }, preserveComments),
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
      const reason = unsupported(editor, value, preserveComments);
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
