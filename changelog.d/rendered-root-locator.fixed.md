- **The LiveView root of a rendered page is found by one locator, the Rust
  scanner, read as html5ever reads the page.** The WebSocket frame, the
  initial-GET shell splice, `_strip_liveview_root_in_html`, the streaming split
  (`_split_for_streaming`, `arender_chunks`), the `dj-view` stamp and the test
  client's dj-id helper used a Python twin that disagreed with the Rust scanner
  and with the VDOM (built by html5ever) in three ways, now fixed:
  `{% if %}` / `{% else %}` text left in rendered output (a `{% verbatim %}`
  block, say) was balanced as template branches, so the root closed early and
  the initial GET spliced its tail in twice, or never closed and the whole page
  went out as the frame; a `<body>` tag anywhere (a child document's, inside an
  embedded child's wrapper) now adds its attributes to the one body element as
  html5ever does, a `<body>` that is another view's container is never a root,
  and a `<body>` root runs to the last `</body>` instead of a child's stray
  one; and a `<` or NUL inside a tag name is part of the name, so
  `<div<x dj-root>` is a root (`djust._rust.root_element_span`,
  `dj_root_stamp_offsets`). The Python fallback in `_extract_liveview_content`
  is gone. Template SOURCE keeps the Python locator, which balances
  `{% if %}` branches. Pinned by new cases in the shared corpus
  `python/tests/fixtures/root_selection_3031.json` (with each expected root
  content checked against html5ever's tree) and by
  `python/djust/tests/test_rendered_root_locator.py`.
