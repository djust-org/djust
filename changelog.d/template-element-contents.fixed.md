- **A `<template>` element's contents are rendered and patched.** The VDOM held
  every `<template>` empty (the HTML parser keeps a template's children in its
  contents fragment, not its child list): a template inserted by a patch
  arrived with no children, and content rendered into an existing template
  after the first render (a list loaded on demand) produced no patch at all.
  The parser now reads a template's contents as its children, and the client
  builds, addresses (`SetText`, `InsertChild`, `RemoveChild`, `MoveChild`) and
  morphs them in `template.content`. Cases in
  `crates/djust_vdom/tests/test_template_contents.rs`,
  `tests/js/template_contents_patch.test.js` and
  `python/djust/tests/test_template_element_patches.py`.
