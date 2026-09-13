# Markdown editor

The MarkdownEditor hook adds selection-aware formatting to a native textarea.
The optional Visual mode uses Tiptap to edit formatted content while storing
Markdown. There is no frontend application, API endpoint or application event
handler to implement. FormMixin still binds, validates and submits the field.
Server preview and published content must use a server-side Markdown sanitizer.

## Native forms

Use ordinary Django fields and native `as_live()` / `as_live_field()` rendering:

```python
from django import forms

class PostForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={
        "dj-input": "validate_field",
        "dj-debounce": "250",
        "data-markdown-editor": "visual",
    }))
```

Beside the native rendered fields, place the stable controls host:

```django
{% include "djust_components/markdown_controls.html" with field="body" mode="visual" %}
```

The host must be inside the same semantic form as the field. Keep the usual
CSRF token and `dj-submit="submit_form"`. Put body last when rendering an entire
form so its label and controls are adjacent. To choose a different placement,
use native `as_live_field()` rendering in the template layout.

Load these optional static assets before djust mounts hooks (the usual deferred
client or framework-injected client works):

```django
{% load static %}
<link rel="stylesheet" href="{% static 'djust_components/markdown-editor.css' %}">
<script src="{% static 'djust_components/markdown-visual.js' %}"></script>
<script src="{% static 'djust_components/markdown-editor.js' %}"></script>
```

Omit `markdown-visual.js` for a lightweight Markdown-only editor. If the visual
asset is unavailable, the original textarea remains usable. The assets are
shipped prebuilt: application users do not need Node, npm or a bundler.

## Existing component

`MarkdownEditor(name="body", value=source, mode="visual", event="update_body")`
and `{% markdown_editor name="body" value=source mode="visual" event="update_body" %}`
use the same hook. Update `value` in the bound native event to refresh the
sanitized server preview. `preview=False` omits that preview. `toolbar=False`
keeps the plain textarea. All three renderers (Python, Django tag and Rust tag
handler) use `djust.markdown.render_markdown(..., provisional=False)`.

## Editing contract

- Switching modes alone leaves the original Markdown bytes untouched. An edit
  may normalize Markdown spelling or whitespace while retaining supported content.
- Visual mode supports emphasis, headings, links, nested lists, quotes, task
  lists, tables, images and fenced code. The toolbar exposes the common actions;
  tables and images already in Markdown can be edited in place.
- Unsupported HTML, footnotes, directives and unsafe/unrecognized URLs stay in
  Markdown mode with an explanation. Raw source is never silently replaced just
  to enter Visual mode. This is a conservative guard, not a claim of universal
  Markdown compatibility; Tiptap's Markdown extension is still beta.
- Visual undo/redo uses the editor transaction history. Source mode uses native
  textarea editing; toolbar insertions preserve native undo where `insertText`
  is supported, with a `setRangeText` fallback. Editing the source then reopening
  Visual begins a new document load; cross-mode undo is not promised.
- Only the native textarea has a form name. Each visual edit updates it and emits
  its normal input event (or change for change-only bindings). No shadow hidden
  form, autosave endpoint, reconnect buffer or duplicate submit handler is added.
- While the visual surface has focus its current draft wins over stale field
  patches, matching native focused-input behavior. Unfocused server resets are
  reflected in the editor. This is not collaborative document editing.
- Native required-field validation reveals and focuses the textarea if needed.
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

`markdown-editor.css` exposes `--dj-md-editor-bg`, `--dj-md-editor-text`,
`--dj-md-editor-border`, `--dj-md-editor-muted`, `--dj-md-editor-accent`,
`--dj-md-editor-code-bg`, `--dj-md-editor-radius`, `--dj-md-editor-height`
and `--dj-font-mono`. Theme these variables rather than
copying rendering code.

The optional visual bundle is approximately 155 KiB gzip and does not enter the
core djust client. Its pinned MIT dependencies and license notices live with the
standalone build in `js/markdown-editor/` and the generated static assets.

```shell
make markdown-editor-build
make test-markdown-editor
npx vitest run tests/js/markdown_editor.test.js
pytest python/tests/test_markdown_editor_preview.py
```

The dedicated Markdown editor CI job tests the real engine and hook, rebuilds
assets and rejects a source/bundle mismatch. The main JavaScript suite exercises
source controls and the stable DOM contract. djust.org is the first application
integration; browser checks there supplement unit and transport tests.
