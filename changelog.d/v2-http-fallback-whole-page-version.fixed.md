- **The page-POST fallback no longer reloads the page on its second event.**
  Each fallback request renders on a fresh server renderer, so every
  whole-page answer is numbered v1; the client checked it against the previous
  answer's version, saw a mismatch and, with no socket, reloaded the page. An
  explicit view had this on every second event, and since the legacy render
  cache was removed a legacy view does too. A whole-page fallback answer now
  re-bases the client's version cursor, as a socket mount or `html_recovery`
  frame does; patch answers are still checked. New cases in
  `tests/js/http-fallback-whole-page-version.test.js`.
