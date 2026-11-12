- **Password fields are kept out of drafts, the `dj-auto-recover` envelope and
  `_collectFormData`.** A field with `type="password"`, or marked
  `autocomplete="new-password"` / `"current-password"`, is no longer saved to browser storage
  by the draft manager (even when the page marks it `data-draft="true"`), sent back to the
  server by `dj-auto-recover`, or written to from a stored draft. Tests in
  `tests/js/secret_fields_not_kept_3978.test.js`.
