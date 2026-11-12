# docs/removed/ — one file per removal

The "Replace removed names and settings" tables in
`docs/website/guides/migrating-to-2.0.md` and the "Removed in 2.0.0" sections of
`docs/API_STABILITY.md` are generated from the files here by
`make generated-docs`, so two changes that each remove something add two files
instead of both appending to the same table (#4080).

- `2.0/migration/<NNN>-<slug>.md`: one table row. Front matter only:
  `table` (`python`, `settings` or `templates`), `removed` and `use` (the two
  cells, one line each), and `covers`.
- `2.0/stability/<NNN>-<slug>.md`: one `###` section of "Removed in 2.0.0",
  the whole file.

Rows and sections render in file-name order. `NNN` only orders them: pick a
number after the last one, and if another branch picks the same number the
slug breaks the tie.

`covers` lists the keys of the `REMOVED_*` tables in
`python/djust/_removed_settings.py` that the row documents, comma-separated,
with `REMOVED_NAMES` entries written `module.name`
(`djust.security.sign_snapshot`). Those tables are what refuses a removed name
at runtime, so `make check-generated-docs` fails when one of their keys has no
row here.
