### `djust_components/table.html` and the `table_*` filters → the native `{% data_table %}`

Under Django's template engine `{% data_table %}` was an inclusion tag over
`djust_components/table.html`, a second implementation of the table that drew
none of the native-only features (`expandable`, `editable_columns`,
`exportable` ...) and differed from the Rust engine's table in markup. djust
`2.0.0` removed it (#3869): the tag is now a simple tag that hands the native
`DataTableHandler` the same operands the Rust engine does, so both engines draw
the same bytes. Removed with the template: the filters `table_cell`,
`table_href`, `table_has_href`, `table_width` and `table_width_attrs` of
`djust_components_tags`.

**Lost:** a project override of `djust_components/table.html` (the documented
way to change the row-click script's `/static/` prefix) has no effect, and a
template that used the filters outside `table.html` must read the row itself
(`{{ row.name }}` in a loop over your own rows). **Kept:** every argument of
`{% data_table %}`, and every feature the tag documented, which now works under
Django's engine too.

The markup is the native table's (no whitespace between elements, `data-row-key`
on rows, the handler's classes and wrappers), so CSS or tests written against the
old `table.html` markup may need new selectors. The row-click script is
`/static/djust_components/data-table-row-click.js` with a fixed `/static/` prefix;
serve it from there or load it yourself.
