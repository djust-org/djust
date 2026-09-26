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
  lists, quotes, task lists, tables, images and fenced code. The toolbar exposes the common actions;
  tables and images already in Markdown can be edited in place.
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
actions to those the application publishes. For example, applications whose
Markdown policy does not render tasks can omit `task`. Server authorization,
image policy and sanitization remain application/framework responsibilities.

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

The optional visual bundle is approximately 155 KiB gzip and does not enter the
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
