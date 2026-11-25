- **With `DJUST_STATE_SAVE_INTERVAL` set, an HTTP fallback request that changes
  nothing no longer takes the tab from its socket (#4164).** It used to record a
  holder token on restore, which `SessionMiddleware` stored, so the socket's
  later flush of its newer, unsaved changes was refused and the page told to
  reload. A fallback request now claims the tab only with a save that changes
  the stored state; one that does still fences the older socket out.
