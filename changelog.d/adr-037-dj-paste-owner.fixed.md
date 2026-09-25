- **`dj-paste` reaches the component or embedded child it is in.** The paste
  binder never attached owner context, so a paste inside a LiveComponent or a
  `{% live_render %}` child was sent to the page's root view. It now attaches
  context like every other binding, under both parameter policies. Found by
  ADR-037's embedded-child browser test
  (`tests/playwright/test_embedded_directives.py`); a case in
  `tests/js/dj-paste.test.js` pins it.
