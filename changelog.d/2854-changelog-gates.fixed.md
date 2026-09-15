- **Release gates: a tagged release can no longer ship without its CHANGELOG
  section, and fragment path/class claims are now checked (#2854, #2849).**
  v1.1.3 shipped to PyPI with no `## [1.1.3]` section while the
  shipped-section pin reported OK against v1.1.2 — the pin's anchor silently
  fell back to the newest *sectioned* tag. `scripts/check-changelog-tagged-sections.py`
  now fails for every release tag above that anchor reachable from HEAD whose
  version has no working-tree section, and `make release` refuses to tag when
  `CHANGELOG.md` has no section for the target version (the pre-commit hook
  cannot see this: at the version-bump commit the tag does not exist yet).
  Separately, a new fragment reference check resolves backtick-quoted file
  paths and test-class names in pending fragments against the tree, closing
  the same #2652-shaped gap for fragments; count claims were already covered
  by `scripts/check-changelog-test-counts.py`.
