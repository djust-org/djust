# `cross-test-state-leak`

**Rule** (the line the rule sheet carries): A test leaves nothing behind that a test on the same
worker can read, and a test that reads a shared table reads its own row, never the table's first.
→ this page

**Status**: `wiki-only` · **gate**: yes, for the shapes named —
`python/tests/test_user_rows_do_not_leak_between_tests.py` (no test that holds a rolled-back
database writes a `User` through `sync_to_async`) and `tests/db_row_isolation.py` (a row another
connection committed under a rolled-back `django_db` is swept after the test: `User`, `Group`,
`Permission`, `Session`; a sweep, not a failure, so it cannot be a gate on the polluter) ·
**candidate for mechanical gate**: no further; a read-by-position check is not mechanical
**Origin**: main run 41774238410 (`python-tests (py3.14, shard 1/4)`), the second of two flakes
fixed together with the `tests/timing_report.py` attribution flake.

## Instances

| PR | what drifted / what was missed | cure | rule in force? |
|---|---|---|---|
| main run 41774238410 | `test_typed_args_floor_3756.py::...[obj.users.first.password]` failed its control ("Django did not print the secret"): `.first` on `User.objects.all()` read a leftover `User.objects.create(username=...)` (empty password, lowest pk), not the fixture's user. The leftover came from `test_object_permission_event.py::test_per_event_sync_orm_get_object_does_not_raise_sync_only`, which created the user through `sync_to_async` under a plain `django_db`: asgiref runs that on an executor thread with its own connection, so the write autocommits outside the test's transaction and the rollback never removes it. A second test file (`test_handler_perm_async_wrap_1648.py`, two tests) did the same with `create_user`. Both shapes failed one CI cell only because xdist put polluter and victim on one worker there | the victim reads the fixture's own row (`filter(pk=user.pk)`), the polluters are `django_db(transaction=True)` (flushed at teardown), and the AST pin above fails at the polluter, naming the test | no (origin) |
| #3782 | The AST pin saw 5 tests that write a non-`User` row through `sync_to_async`; a runtime probe (row counts of `User` / `Group` / `Permission` / `Session` after each test's teardown, over `python/djust/tests`, `python/tests`, `tests/unit`, `tests/integration`, 61,053 tests) found 293 tests that leave a `Session` row, in 57 files (and no `User` or `Group` row; one more test added 4 `Permission` rows, which is `create_permissions` rather than a thread write), almost all through a helper (`_make_db_session`) the AST cannot follow. Marking them `transaction=True` flushes the database after each | `tests/db_row_isolation.py` snapshots the four tables before the test's own setup (outside its transaction) and deletes whatever is new after the rollback; its terminal summary names the tests. Overhead on 284 database tests: not measurable above run-to-run noise (about 10 s either way) | yes (the sweep) |

## Detection

- **A writer.** An `async` test that calls `sync_to_async(<Model>.objects.create...)` under a plain
  `@pytest.mark.django_db` (or the `db` fixture). The AST pin covers the user model written in the
  test's body; a write reached through a helper is invisible to any AST scan, so
  `tests/db_row_isolation.py` measures the tables instead and sweeps what it finds. The sweep's
  summary line lists the files; run `pytest <file>` and read the "djust db row isolation" section
  to see whether a file still has tests that commit on another connection. Under SQLite's shared
  cache a SELECT in an open transaction holds a table lock that fails another connection's INSERT
  ("database table is locked"), which is why the snapshot is taken before the test's transaction
  opens, not inside it.
- **A reader.** `X.objects.first()`, `X.objects.all()[0]`, or `.first` reached through a template
  over a global queryset, in a test that created its own row. Prefer
  `X.objects.filter(pk=row.pk)`: it keeps the same code path and cannot be shadowed.
- **To reproduce**, run the polluter and the victim in that order in one process:
  `pytest <polluter> <victim>`. It fails every time; the CI failure was a scheduling accident.

## Rejected shapes

- **Deleting the row at the end of the polluter.** A `try/finally` delete works until the test
  fails before it, and each such test needs its own. A transactional mark is the cleanup the
  test framework already owns.
- **Only narrowing the victim.** It makes the one reader immune and leaves the row for every
  other reader that has not been written yet; the pin is what keeps the next polluter out.
