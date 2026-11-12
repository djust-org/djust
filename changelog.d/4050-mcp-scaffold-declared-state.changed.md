- **The MCP `scaffold_view` tool declares template state with `state()` (#4050).**
  A generated list view declares `search_query`, `page` and `editing` on the
  class, where they render by default, instead of setting them in `mount()`
  and handing each one over in `get_context_data()`, which now returns only
  derived and render-only values (`items`, `selected_item`, `total_pages`).
