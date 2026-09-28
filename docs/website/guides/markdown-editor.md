---
title: "Markdown Editor"
slug: markdown-editor
section: forms
order: 3
level: intermediate
description: "Add optional Visual and Markdown editing to native Django form fields with djust."
---

# Markdown editor

The `MarkdownEditor` hook adds formatting controls to a native textarea.
Enable Visual mode to edit formatted content with Tiptap while storing Markdown.

`FormMixin` still binds, validates and submits the field. The native form
integration needs no additional frontend application, API endpoint or application
event handler.

> **Render safely.** Server previews and published content must use a
> server-side Markdown sanitizer.

## Native forms

Use ordinary Django fields and native `as_live()` or `as_live_field()` rendering.

**1. Configure the field.** Enable the editor through the textarea's attributes:

```python
from django import forms

class PostForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={
        "dj-input": "validate_field",
        "dj-debounce": "250",
        "data-markdown-editor": "visual",
    }))
```

**2. Add the controls.** Place the stable controls host beside the native
rendered fields:

```django
{% include "djust_components/markdown_controls.html" with field="body" mode="visual" %}
```

The include also takes `bubble_menu=False` and `floating_menu=True`; see
[Selection and empty-line menus](#selection-and-empty-line-menus).

The host must be inside the same semantic form as the field. Keep the usual
CSRF token and `dj-submit="submit_form"`. Put body last when rendering an entire
form so its label and controls are adjacent. To choose a different placement,
use native `as_live_field()` rendering in the template layout.

**3. Load the assets.** Include these optional files before djust mounts hooks.
The usual deferred client or framework-injected client works:

```django
{% load static djust_assets %}
<link rel="stylesheet" href="{% static 'djust_components/markdown-editor.css' %}">
{% djust_asset "markdown-visual" %}
<script src="{% static 'djust_components/markdown-editor.js' %}"></script>
```

Omit the `markdown-visual` asset for a lightweight Markdown-only editor;
`{% djust_asset %}` adds its integrity hash, and its bundled package versions
are listed in djust's SBOM (see [Scanning a djust app](scanning.md)). If the
visual asset is unavailable, the original textarea remains usable. The assets
are shipped prebuilt: application users do not need Node, npm or a bundler.

## Existing component

The Python component and the template tag use the same editor hook.
Choose the version that fits your view.

**Python component**

```python
MarkdownEditor(
    name="body",
    value=source,
    mode="visual",
    event="update_body",
)
```

**Template tag**

```django
{% markdown_editor name="body" value=source mode="visual" event="update_body" %}
```

**Preview and toolbar options**

| Option | Behavior |
| --- | --- |
| `value` | Update this in the bound native event to refresh the sanitized server preview. |
| `preview=False` | Omit the server preview. |
| `toolbar=False` | Keep the plain textarea. |
| `bubble_menu=False` | Turn off the formatting menu over a selection (Visual mode; on by default). |
| `floating_menu=True` | Add the block menu on an empty line (Visual mode; off by default). |

The template tag takes the same names, e.g.
`{% markdown_editor name="body" mode="visual" floating_menu=True %}`.

All three renderers — the Python component, Django tag and Rust tag handler —
use the same Markdown sanitizer:

```python
djust.markdown.render_markdown(..., provisional=False)
```

## Editing contract

- **Source preservation.** Switching modes alone leaves the original Markdown
  bytes untouched. An edit may normalize Markdown spelling or whitespace while
  retaining supported content.
- **Supported content.** Visual mode supports emphasis, headings, links, nested
  lists, quotes, task lists, tables, images and fenced code. The toolbar exposes the common actions,
  including inserting and editing tables; images already in Markdown can be edited in place.
- **Unsupported content.** Unsupported HTML, footnotes, directives and
  unsafe/unrecognized URLs stay in Markdown mode with an explanation.
  Raw source is never silently replaced just
  to enter Visual mode. This is a conservative guard, not a claim of universal
  Markdown compatibility; Tiptap's Markdown extension is still beta.
- **Undo and redo.** Visual mode uses the editor transaction history. Source mode
  uses native textarea editing; toolbar insertions preserve native undo where `insertText`
  is supported, with a `setRangeText` fallback. Editing the source then reopening
  Visual begins a new document load; cross-mode undo is not promised.
- **Form events.** Only the native textarea has a form name. Each visual edit
  updates it and emits its normal input event (or change for change-only bindings). No shadow hidden
  form, autosave endpoint, reconnect buffer or duplicate submit handler is added.
- **Server updates.** While the visual surface has focus its current draft wins
  over stale field patches, matching native focused-input behavior. Unfocused server resets are
  reflected in the editor. This is not collaborative document editing.
- **Validation and read-only fields.** Native required-field validation reveals
  and focuses the textarea if needed.
  Read-only/disabled fields keep inspection and mode switching available while
  editing controls are disabled.

The controls live **inside server-declared `dj-update="ignore"` markup**.
Never insert toolbar siblings around a textarea after mount: that changes VDOM
index paths. The editor does not depend on `beforeUpdate` being called. It
cleans up editor resources and field listeners through `destroyed()`.

An optional space-separated `data-actions` attribute on the host limits toolbar
and menu actions to those the application publishes. For example, applications whose
Markdown policy does not render tasks can omit `task`. The action names are
`bold italic heading quote list ordered task code block link table undo redo`
and the table actions below. Server authorization,
image policy and sanitization remain application/framework responsibilities.

## Tables

**Insert table** (`table`) is an ordinary toolbar action. In Visual mode it
inserts a 3 × 3 table with a header row. In Markdown mode it inserts a GFM
skeleton as its own block after the line holding the caret (it never splits a
line) and selects the first header cell.

While the selection is inside a table, Visual mode shows a **Table** group:

| Action | Button |
| --- | --- |
| `row-before` / `row-after` | Add a row above / below |
| `row-delete` | Delete the row |
| `column-before` / `column-after` | Add a column left / right |
| `column-delete` | Delete the column |
| `header-row` | Make the first row the header |
| `table-delete` | Delete the table |

Each button is enabled only when the action applies at the selection
(`editor.can()`); for example, **Delete column** is disabled in a single-column
table. Markdown source mode hides the group.

Every table must still be valid GFM when it is saved, and the editor enforces
that itself rather than relying on which buttons it shows:

- **A cell holds one line of inline text.** The editor's schema allows exactly
  one paragraph per cell, so no path can put a heading, list, quote, code
  block, rule or nested table in one: not the toolbar, not a keyboard shortcut
  or a Markdown input rule such as `# ` or `---` (these stay literal text),
  not `getEditor()` commands, and not a selection that spans a table (only the
  blocks around the table change). Formatting inside a cell (bold, italic,
  code, link) works as usual.
- **Paste and drop.** Copied cells, or a table copied on its own from a
  spreadsheet or web page, paste cell by cell into the cells at the caret.
  Anything else pasted or dropped into a cell (several paragraphs, a list, or
  a Docs/Word selection that mixes text with a table) lands in that one cell
  as text joined by line breaks; a drop goes to the cell under the pointer,
  not the caret. An HTML table whose cells hold several paragraphs keeps one
  cell per `<td>`.
- **Images in cells** are inline, like the text around them. `![alt](src)` in
  a Markdown cell, and an `<img>` in a pasted, dropped or loaded HTML cell,
  load as an inline image that you can type before and after, and that
  `getEditor()` can insert text next to. It is saved as `![alt](src)`, and an
  image dropped into a cell leaves the caret after it. An `<img>` follows the
  same source rule as any other image (no `data:` URIs).
- **Line breaks.** Enter or Shift+Enter in a cell inserts a line break, saved
  as `<br>`, the only line break a table row can hold; it reopens in Visual
  mode. Raw HTML anywhere else still keeps a document in Markdown mode.
- **`|` in a cell** is saved as `\|`, so the cell reloads whole.
- **The header row.** A GFM table always has one. `header-row` is an action,
  not a toggle: it is available only when the first row is not a header, which
  happens after **Delete row** removes the header row; it then promotes the
  new first row. Deleting the header row keeps the table valid, with the next
  row's cells as body cells until you promote one.
- **Merged cells.** GFM has no spelling for them.

## Selection and empty-line menus

In Visual mode, selecting text opens a small formatting menu next to the
selection: bold, italic, inline code and link, plus the table actions when the
selection is inside a table. **It is on by default**, including for editors
that existed before it was added; turn it off with `bubble_menu=False` (the
component and template tag), `bubble_menu=False` on the
`markdown_controls.html` include, or `data-bubble-menu="false"` on a
hand-written host. The menu sits above the selection, or below it when above
would cover the toolbar; it follows the editor's own scrolling and hides while
the selection is scrolled out of view.
`floating_menu=True` adds a second menu on an empty line, with the block
actions: heading, quote, the three lists, code block and insert table.

- **The selection survives.** Pressing a menu button does not move focus, so
  the format applies to the selected text.
- **The browser's context menu is untouched.** The editor never handles
  right-click, with or without a selection, so spelling suggestions stay
  where the browser puts them.
- **Keyboard.** Alt+F10 moves focus from the editor to the open menu (or to
  the toolbar when none is open). Arrow keys, Home and End move between
  buttons, Escape returns to the editor with the selection intact, and the
  menu closes when focus leaves it.
- Both menus are `role="toolbar"` with an accessible name, and their buttons
  use the same `data-markdown-action` names and states as the toolbar.
  `data-actions` filters them too; a menu it leaves without buttons is not
  shown at all.
- **Flag spellings.** `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off`, in
  any case, as Python values or template strings; anything else keeps the
  default. The component, both template tags and the include read them the
  same way.

## Reaching the editor from app code

The hook exposes the live Tiptap editor through `getEditor()`, and
`window.djust.getHook()` returns the hook for an element
(see [Reaching a hook from page code](hooks.md#reaching-a-hook-from-page-code)):

```javascript
const hook = window.djust.getHook(document.querySelector('[dj-hook="MarkdownEditor"]'));
const editor = hook?.getEditor();  // null until Visual mode has been entered
if (editor?.can().addRowAfter()) editor.chain().focus().addRowAfter().run();
```

Edits made this way update the native field and fire its input event like any
other Visual edit. Prefer the built-in actions where they exist, and keep to
commands whose result is valid Markdown.

**Inside a table cell, block content is refused.** With the caret in a cell,
a command that would insert a block — `insertContent("<p>a</p><p>b</p>")`, a
heading, a list, a rule, a table — changes nothing, even though the chain can
still report `true`. Insert inline content instead, and use `setHardBreak()`
(saved as `<br>`) for a new line:

```javascript
editor.chain().focus().insertContent("first").setHardBreak().insertContent("second").run();
```

## Theme and contribution development

Theme the variables exposed by `markdown-editor.css` rather than copying
rendering code.

| Variable | Controls |
| --- | --- |
| `--dj-md-editor-bg` | Editor background |
| `--dj-md-editor-text` | Editor text |
| `--dj-md-editor-border` | Borders |
| `--dj-md-editor-muted` | Secondary text |
| `--dj-md-editor-accent` | Accent color |
| `--dj-md-editor-code-bg` | Code background |
| `--dj-md-editor-radius` | Corner radius |
| `--dj-md-editor-height` | Size of the editing surface (the textarea and the visual surface). It sets `height`, `min-height` and `max-height` at once, so it is a fixed size, not a minimum; longer content scrolls inside it (default `20rem`) |
| `--dj-md-editor-min-height` | Minimum height of the source/preview panes container, `.dj-md-editor__panes`, in `components.css` (default `16rem`) |
| `--dj-md-editor-toolbar-bg` | Toolbar background (`components.css`) |
| `--dj-font-mono` | Monospace font |

The optional visual bundle is approximately 165 KiB gzip and does not enter the
core djust client. Its pinned MIT dependencies and license notices live with the
vendored-asset build in `js/vendor/` and the generated static assets.

```shell
make vendor
make test-vendor
npx vitest run tests/js/markdown_editor.test.js
pytest python/tests/test_markdown_editor_preview.py
```

The dedicated Markdown editor CI job tests the real engine and hook, rebuilds
assets and rejects a source/bundle mismatch. The main JavaScript suite exercises
source controls and the stable DOM contract. djust.org is the first application
integration; browser checks there supplement unit and transport tests.
