import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import { TableKit } from "@tiptap/extension-table";
import Image from "@tiptap/extension-image";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
const extensions = () => [
  StarterKit.configure({ underline: false, link: { openOnClick: false } }),
  Markdown,
  TableKit,
  Image.configure({ allowBase64: false }),
  TaskList,
  TaskItem.configure({ nested: true }),
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
  const visit = (tokens) => {
    for (const token of tokens || []) {
      if (!supported.has(token.type))
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
      if (token.tokens) visit(token.tokens);
      if (token.items) visit(token.items);
      if (token.header) token.header.forEach((cell) => visit(cell.tokens));
      if (token.rows) token.rows.flat().forEach((cell) => visit(cell.tokens));
    }
  };
  const tokens = editor.markdown.instance.lexer(source);
  visit(tokens);
  if (Object.keys(tokens.links || {}).some((key) => key.startsWith("^")))
    reason = "Footnotes stay in Markdown mode to preserve their source.";
  return reason;
}
export function createVisual(
  element,
  source,
  onChange,
  label,
  onSelection = () => {},
) {
  const editor = new Editor({
    element,
    injectCSS: false,
    extensions: extensions(),
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
      const chain = editor.chain().focus();
      switch (action) {
        case "bold":
          return chain.toggleBold().run();
        case "italic":
          return chain.toggleItalic().run();
        case "heading":
          return chain.toggleHeading({ level: 2 }).run();
        case "quote":
          return chain.toggleBlockquote().run();
        case "list":
          return chain.toggleBulletList().run();
        case "ordered":
          return chain.toggleOrderedList().run();
        case "task":
          return chain.toggleTaskList().run();
        case "code":
          return chain.toggleCode().run();
        case "block":
          return chain.toggleCodeBlock().run();
        case "link":
          return href
            ? chain.extendMarkRange("link").setLink({ href }).run()
            : chain.unsetLink().run();
        case "undo":
          return chain.undo().run();
        case "redo":
          return chain.redo().run();
      }
    },
  };
}
window.djust = window.djust || {};
window.djust.createMarkdownVisual = createVisual;
