- **A `<script>` or `<style>` inside a `<pre>`, `<code>` or `<textarea>` no
  longer disappears from the page.** The egress normalizer hides those blocks
  behind `__PRESERVED_BLOCK_<n>__` placeholders and puts them back after
  folding white space. A raw-text block is hidden first, so a `<pre>` that
  contained one held its placeholder, and the old restore never reached it:
  the page showed a literal `__PRESERVED_BLOCK_0__` where the script had been.
  Blocks are now put back recursively. The same restore also replaced page
  text that happened to spell a placeholder: a value such as a display name of
  `__PRESERVED_BLOCK_0__` was swapped for the page's first preserved block.
  That text is now escaped for the duration of the pass, including when
  removing an HTML comment joins two pieces of text into it. The plain path
  (Rust and its Python reference) is fixed; the provenance path already
  restored by position. New cases in
  `tests/unit/test_egress_normalizer_rust_parity.py`: no placeholder ever
  reaches the output across the fuzzed corpus, look-alike text behaves like
  any other text, and 240 nested-block pages come back verbatim.
