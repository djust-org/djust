- **Child background parameter contracts.** Scoped background updates now include
  the same owner-addressed parameter metadata as foreground renders. Invalid
  metadata withholds the HTML without exposing exception details or losing
  background-batch completion; legacy-only responses remain unchanged.
