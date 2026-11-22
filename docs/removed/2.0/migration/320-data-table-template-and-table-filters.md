---
table: templates
removed: The `djust_components/table.html` override point and the filters `table_cell`, `table_href`, `table_has_href`, `table_width`, `table_width_attrs`
use: Read the row in your own template. `{% data_table %}` is drawn by the native handler on both engines; the row-click script is `/static/djust_components/data-table-row-click.js` (serve it from `/static/` or load it yourself).
---
