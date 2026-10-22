- **`{% tabs %}` follows the WAI-ARIA tabs pattern.** Every tab was a Tab stop
  until an arrow key was pressed: the server rendered no `tabindex`. The
  selected tab now renders with `tabindex="0"` and the others with `-1` (the
  first tab when none is selected), and when focus leaves the tab list the
  client gives the Tab stop back to the selected tab. The tab list takes an
  accessible name, `label=` (`aria-label`) or `labelledby=`
  (`aria-labelledby`). With `param="<name>"` each tab is a `dj-patch` to
  `?<name>=<tab id>` instead of a `dj-click` event, so the view reads the tab
  in `handle_params` and Back/Forward and bookmarks select it.
