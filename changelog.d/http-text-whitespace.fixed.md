- **A page loaded over HTTP keeps the white space of its text.** The HTTP
  normalizer folded every run of white space outside `pre`, `code`,
  `textarea`, `script` and `style` to one space, including the white space
  inside text. A `white-space: pre-wrap` message `"question\n\n[1 image]"` read
  "question [1 image]" on a fresh load and kept its lines when patched in over
  the WebSocket. Django renders text verbatim, and the VDOM parser keeps it.
  The fold now applies only inside markup and to whitespace-only text between
  tags, which the #2999 rule then keeps or drops. One Rust rule
  (`djust_core::html_whitespace::fold_whitespace_edits`) serves both the plain
  and the provenance-tracking render paths.
