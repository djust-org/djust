- **Every storybook page says where the component lives and what styles it.**
  A component's preview answered "what does it look like" and nothing else — a
  developer who wanted to change how it looked had to grep the package. Each
  detail page now carries a `SOURCE & STYLES` table: the template path, with
  the path to copy it to for an override and the per-theme override path; the
  module path for a Python component; and, for every stylesheet that matches
  the component's own classes, its path and the lines that define them.

  The stylesheet rows are computed from the rendered markup rather than kept
  in a table, so a component that gains a class gains its entry, and the two
  `components.css` files (the theming package's and the components app's) are
  told apart by path rather than conflated.
