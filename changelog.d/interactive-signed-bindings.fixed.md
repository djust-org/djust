- **Interactive signed restoration** — staged fixed dropdowns now include a
  versioned binding record in signed navigation snapshots. Restore validates
  declarations, record shapes and identity collisions before mutation, uses
  current server configuration/callbacks, and rolls back partial registration on
  failure. Interactive resumes include fresh HTML to reconcile current controls.
