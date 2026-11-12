# docs/checks/ — one file per system-check ID

This directory is the registry of every system-check ID djust has assigned,
live, retired and reserved, and the source of the per-check text in two docs:

- `docs/system-checks.md`: the quick-reference table, the check count, and
  each family's sections (the `reference` part of each file);
- `docs/website/guides/error-codes.md`: each family's sections (the `guide`
  part).

Those regions sit between `<!-- BEGIN GENERATED ... -->` / `<!-- END GENERATED
... -->` markers and are rewritten by `make generated-docs`. Everything outside
the markers is hand-written and stays where it is. Before this directory existed
every new check appended to the same table and sections, so nearly every merge
conflicted, and two branches could take the same ID without noticing (#2070,
#4080).

## Adding a check

1. Take an ID: `make new-check-id FAMILY=V` prints the next free one (`C3`,
   `audio.W` and `djust_theming.W` work too). Retired and reserved IDs are
   never handed out again.
2. Emit it from the check (`id="djust.V028"`).
3. Add `docs/checks/V028.md`:

   ```markdown
   ---
   id: V028
   severity: Warning
   summary: One line for the quick-reference table
   ---

   <!-- doc: reference -->
   ### V028 — short title
   - **Severity**: Warning
   - **What it detects**: ...

   <!-- doc: guide -->
   ### V028: short title

   **What causes it**: ...

   **Fix**: ...
   ```

   `severity` and `summary` are required for the families in the table (C, V,
   S, T, Q, Y, B). Either part may be left out; a check with no `reference`
   part has only its table row in `docs/system-checks.md`.
4. `make generated-docs`, and commit the new file with the regenerated docs.

Two branches that take the same ID both add `docs/checks/V028.md`, and git
reports an add/add conflict on it. Two branches that add different checks add
different files; the regenerated docs are `merge=union` in `.gitattributes`, so
they merge without a conflict and `make generated-docs` then re-sorts them.

## Retiring a check

Keep the file and set `status: retired`, with a summary that says when and why
(`Retired in 2.0 with ... Never emitted; the ID is not reused`). `status:
reserved` holds an ID for a check that has not shipped (`S010`); it has no table
row.

## What enforces this

- `python scripts/system-check-docs.py check` (pre-commit and CI): the docs match
  these files, and no one edited a generated region by hand.
- `python/djust/tests/test_check_id_registry_4080.py`: every ID the code emits
  has a file here, no retired or reserved ID is emitted, and every live file is
  still emitted.
- `python/djust/tests/test_check_id_uniqueness_2070.py`: no ID is emitted by two
  different check functions.
