- **`{% tabs %}` renders a tab list and only the active pane on the Rust
  engine.** Its Rust handler received the already-rendered body, so it rendered
  every `{% tab %}` pane (duplicate ids) and no tab list. The tag now renders
  through Django's own `do_tabs`, as `{% split_pane %}` does, and its output
  matches Django's. `LibraryRawBlockTagHandler` takes a `libraries=` argument
  for tags whose body holds their own child tags.
