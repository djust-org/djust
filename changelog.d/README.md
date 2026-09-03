# changelog.d/ — one changelog fragment per PR

`CHANGELOG.md`'s `## [Unreleased]` section is **not edited directly any more.**
Every `feat:` / `fix:` PR (and any other PR that would have added a line there)
adds ONE new file here instead. The release cut folds them all into
`[Unreleased]` — in canonical section order, sorted by filename — and deletes
them, so the shipped `CHANGELOG.md` reads exactly as if it had been hand-edited.

Why: parallel PRs all appending to the same `[Unreleased]` lines conflict on
every merge, and a PR whose merge ref cannot be built gets **no** `pull_request`
CI run. A new file per PR never conflicts.

## Shape

```
changelog.d/<issue-or-slug>.<section>.md
```

- `<issue-or-slug>` — the issue number (`2510`), or a short slug when there is
  no issue (`changelog-fragments`). Compile order is by filename.
- `<section>` — one of `added`, `changed`, `fixed`, `security`,
  `documentation`, `removed`, `deprecated` (lower-case; it becomes the
  `### Section` heading).
- Body — exactly the bullet you would have written under that heading. Start
  with `- **…**`; multi-paragraph bullets are fine (indent continuation
  paragraphs by two spaces, as in `CHANGELOG.md`). One bullet per file; a PR
  that touches two sections writes two files.

Example — `changelog.d/2510.fixed.md`:

```markdown
- **A Rust panic on every LiveView mount when a `dict` is mutated during
  iteration (#2510).** `extract_value` now snapshots the keys first. 3 regression
  cases in `python/tests/test_dict_mutation_during_iteration_panic_2510.py`.
```

Test-count claims (`N regression cases in <path>`) are checked against the
named file exactly as they are in `CHANGELOG.md`
(`scripts/check-changelog-test-counts.py`).

## Commands

```bash
python scripts/changelog-fragments.py check      # validate fragments (pre-commit runs this)
make changelog-preview                            # show [Unreleased] with fragments folded in
make changelog-compile                            # fold + delete — the release cut does this
```

`make release` runs the compile first and stops if it changed anything, so the
folded `CHANGELOG.md` is reviewed and committed before the tag is created.

The pre-commit hook refuses a commit that changes the `[Unreleased]` body of
`CHANGELOG.md` directly unless that same commit deletes fragments (the compile).
`git merge` commits are exempt. Entries that were already in `[Unreleased]`
when this directory was introduced stay where they are.
