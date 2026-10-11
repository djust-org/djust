- **MCP tools keep filesystem paths inside the project.**
  `find_handlers_for_template` read any absolute path that existed, so it
  confirmed which files existed anywhere on disk and returned `dj-*`-shaped
  strings from them. It now reads an absolute path only when the file resolves
  (symlinks and `..` followed) inside a directory the template loaders serve or
  inside `BASE_DIR`, and answers any other absolute path like a missing
  template. `seed_fixtures` compared paths with a string prefix, so
  `/app-data` passed for a `BASE_DIR` of `/app`, and skipped the check when
  `BASE_DIR` was unset; it now compares whole path components and refuses to
  load anything without `BASE_DIR`. Both use one containment helper.
