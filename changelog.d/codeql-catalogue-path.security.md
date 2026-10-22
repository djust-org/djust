- **The component catalogue no longer builds a file path from the requested
  component name** (code-scanning alerts #3085/#3086, py/path-injection).
  `get_component_template_source` looked the name up in a set and then read
  the file named after it; it now looks the name up in a name-to-path map
  built from the directory listing at import, so a caller's string never
  becomes part of a path, and a listed template that is a symlink resolving
  outside the components directory is refused with a warning. This was not
  exploitable before: the detail page already answered 404 for any name that
  is not a known component, and the set check rejected traversal. New tests
  pin `../`, absolute paths, percent-encoded and backslash separators, a
  symlink escape and the 404 on the detail page.
