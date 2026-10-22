- **The combobox works from the keyboard (WAI-ARIA combobox pattern).** The
  arrow keys did not move through the options (typing only filtered), Enter
  submitted the form with the old value, a typed exact label set the value in
  the browser without telling the server, and focus dropped to the page after
  a pick.
  - ArrowDown / ArrowUp now move `aria-activedescendant` through the visible
    options, wrapping. Each option renders a stable id, `<prefix>-option-<n>`.
  - Enter commits the active option, or a typed exact label, by pressing it,
    so the value event reaches the server exactly as a mouse pick does, and
    the form does not submit.
  - An exact typed label also commits on blur, sending its event once.
  - Escape closes the list and restores the chosen label.
  - After a pick, focus stays on the input. The list closes
    (`combobox--closed`) until the next keystroke, and typing or an arrow key
    reopens it.
