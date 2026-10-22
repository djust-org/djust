- **The combobox's Escape no longer closes the dialog around it, and a
  committed typed label no longer swallows the first Tab.** With the list
  open inside a `{% modal %}` or `{% sheet %}`, Escape closed the whole
  dialog: the combobox only called `preventDefault()`, so app-level bindings
  such as `dj-window-keydown.escape` closed it too. Typing an exact label and
  pressing Tab committed the option but pulled focus back to the input, so a
  second Tab was needed, and the list stayed filtered to the typed text.
  - With the list open, Escape is handled in the capture phase and stops
    there: the modal's keyboard handling and window bindings don't see it,
    and a native `<dialog>`'s `cancel` for that key press is refused. With the
    list closed, Escape closes the dialog as before.
  - A commit on blur no longer refocuses the input; it closes the list and
    clears its filter, so one Tab moves on.
