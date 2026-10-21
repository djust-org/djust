- **`trigger_submit()` on a form without `dj-trigger-action` now warns outside
  debug mode.** The refusal was logged only with `djustDebug`, so in production
  the submit silently did nothing. It is now a `console.warn` naming the
  selector. 1 new case in `tests/js/form_polish.test.js`.
