- **A click no longer overtakes a debounced edit made before it.** Typing into a
  `dj-input` field (debounced, 300 ms by default for text) and clicking a
  `dj-click` button within the window sent the click first; the input followed
  with text typed before the click, so a "typing clears the error" handler undid
  what the click's handler had shown. A click now sends the pending debounced
  edits first (element `dj-debounce` and handler `@debounce`; `@throttle`
  windows are kept). A `dj-submit` now also flushes debounced fields outside its
  own form (it flushed only its form's, #1278). 5 cases in
  `tests/js/click_flushes_debounced_input.test.js`.
