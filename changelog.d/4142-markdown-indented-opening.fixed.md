- **`{% djust_markdown %}` keeps the indentation of a source that opens with an indented code
  block (#4142).** Both engines stripped the source before parsing, so Markdown whose first line
  was an indented code block was read as a paragraph. The strip was dropped on both engines by the
  2.0-q train; this pins it: a source opening with four spaces or a tab, with or without blank
  lines around it, renders a code block on both engines, byte for byte. New cases in
  `test_a_source_opening_with_an_indented_code_block_is_a_code_block_on_both`.
