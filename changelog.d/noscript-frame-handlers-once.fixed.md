- **`html_update` / `html_recovery` frames with a `<noscript>` no longer run new
  element handlers twice.** Such a frame is parsed a second time with the
  page's scripting flag, so the `<noscript>` holds text as in the full render.
  That parse created its elements in the page's document, so an
  `<img onerror>`, `<details open ontoggle>`, `<video>` / `<audio onerror>` or
  SVG `<image onerror>` in the frame ran its handler for the parse and again
  for the element the morph inserted. The parsed tree is now copied into an
  inert document and the parse's own elements lose their handler attributes
  before any event reaches them; the `<noscript>` content stays text.
  Regression cases in `tests/js/full_html_frame_namespace.test.js`.
