- **The HTML clean-up after each render runs in Rust, without the GIL.**
  `TemplateMixin._strip_comments_and_whitespace` (the egress normalizer every
  HTTP, socket, SSE, streaming and recovery render passes through) now calls
  `djust_core::html_whitespace::strip_comments_and_whitespace` for a plain
  render, detached from the interpreter. It applies the same rules in the
  same order — `<script>`/`<style>` preserved before comments are stripped
  (#1927), dj-if markers kept (#1678), `<pre>`/`<code>`/`<textarea>`
  preserved, text white space kept (#3570), the inter-tag rule (#2999) — and
  gives the same bytes. A render carrying provenance, and the rare input the
  port declines (a character Python's case-insensitive match folds to ASCII,
  such as `ſ`, or a non-ASCII character right after `dj-if` in a comment),
  run the previous Python code, now `_strip_comments_and_whitespace_py`.
  Single-thread the pass is 1.5–2x faster; eight threads normalizing a
  300-row table page 400 times take 20 ms instead of 255–320 ms. The parity
  test `tests/unit/test_egress_normalizer_rust_parity.py` compares the two
  over the VDOM fixtures, every repository template (raw and rendered), a
  12,000-input fuzzed corpus and every code point below U+3100 in the places
  a character's class decides a rule.
