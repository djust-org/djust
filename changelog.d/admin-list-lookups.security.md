- **The djust admin change list filters and sorts only on declared fields.**
  `ModelListView.apply_filter` accepts the field names in the ModelAdmin's
  `list_filter` (`get_list_filter()`), and `sort_by` accepts the sortable
  `list_display` columns and the fields named in `ordering` (`get_ordering()`),
  as Django's admin does. The queryset applies the same check to the stored
  filters and ordering, so any other name (another lookup, a path through a
  relation, an expression) is ignored and logged at debug level. The filter
  list on the page now comes from `get_list_filter()` as well. A custom
  change-list template that filters or sorts on other fields must declare them
  in `list_filter`, `list_display` or `ordering`. The auth admin's social
  accounts list sorts only on its own columns.
