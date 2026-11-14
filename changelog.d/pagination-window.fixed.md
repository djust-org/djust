- **`{% pagination %}` no longer walks every page to choose the buttons it shows.** The page list
  is the first and last page and the pages within two of the current one; it was found by
  iterating from 1 to `total_pages`, so a very large `total_pages` never finished rendering. It is
  now computed directly, on the Django tag and the native handler alike.
