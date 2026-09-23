- **Explicit views no longer write upload resume records.** Uploads in flight
  aren't resumed for explicit views (decision D-g), but a resumable writer
  still recorded each upload's client filename and progress in the resume
  store. For explicit views the writer now runs as a plain writer, and nothing
  is recorded. Legacy views resume as before.
