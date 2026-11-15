- **A value marked safe for text is now always escaped when written into an attribute by the
  component template tags, the theming tags and templates, the component classes and
  `{% djust_pwa_head %}` (#4009, completing it).** These wrote a tag or constructor argument into `class`, `id`,
  `name`, `value`, `aria-*`, `data-*`, `dj-*` and similar attributes with `conditional_escape`
  (or `{{ x }}` in a template), which keeps a value marked safe (`mark_safe`, `format_html`
  output) as it is. Attribute slots now use `attr_text` / `escape`, and `|force_escape` in the
  theming component templates; a marked-safe value in a text slot still renders as markup. Both
  engines render the same. The per-argument sweep in
  `python/tests/test_marked_safe_attribute_sweep_4009.py` drives every tag of the swept libraries
  on both engines, every component class and every `Rust*` component, one argument at a time, and
  fails on any attribute that receives such a value unescaped. The safe-markup guard now flags
  `conditional_escape` output placed inside a quoted attribute value.
