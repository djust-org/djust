- **A value marked safe for text is now always escaped when written into an attribute by the
  component template tags, the native component handlers, the theming tags and their templates
  (every theme pack), the component classes and `{% djust_pwa_head %}` (#4009, completing it).**
  These wrote a tag or constructor argument into `class`, `id`, `name`, `value`, `aria-*`,
  `data-*`, `dj-*`, `onclick` and similar attributes with `conditional_escape` (or `{{ x }}` in a
  template), which keeps a value marked safe (`mark_safe`, `format_html` output) as it is.
  Attribute slots now use `attr_text` / `escape`, and `|force_escape` in the theming templates.
  **Visible change:** a value you marked safe is shown literally, escaped, when it lands in an
  attribute (`class=mark_safe("a&amp;b")` now reaches the browser as the text `a&amp;b`); in a
  text slot it still renders as markup. Both engines render the same. Also fixed: an id and the
  attributes that name it (`aria-describedby` of `{% modal %}`, `{% combobox %}` and
  `{% rich_select %}`; the `id` / `for` of `ForeignKeySelect` and `ManyToManySelect`) are escaped
  exactly once, so they match for an id containing `&`, `"` or `<`; and a caller's `onclick=` on
  `{% theme_button %}` is escaped once, in the template. The per-argument sweep in
  `python/tests/test_marked_safe_attribute_sweep_4009.py` drives every tag of the swept libraries
  on both engines and under every theme pack, every component class, every `Rust*` component and
  every native handler (called directly), and fails on any attribute that receives such a value
  unescaped or an id and a reference to it that do not match. The safe-markup guard now flags
  `conditional_escape` output placed inside a quoted attribute value.
