- **`reset_form()` resets the form the event came from in the browser.** The
  client reset the first `<form>` of the page whatever form the server reset,
  so opening a form from a button cleared an unrelated draft in another form.
  It now resets the submitted form or the form holding the triggering element;
  with neither, the page's form only when there is exactly one. Covered in
  `tests/js/reset_form_target.test.js`.
