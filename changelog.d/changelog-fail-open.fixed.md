- **A full `CHANGELOG.md` wipe passed the shipped-section gate silently
  (#2865).** `scripts/check-changelog-tagged-sections.py` anchored on the
  newest tagged section and failed OPEN when it could not select one:
  deleting one shipped section was caught (#2854 absence, #2862
  deletion-below-anchor), but deleting EVERY tagged section — the
  realistic tail of a cross-branch `CHANGELOG.md` merge resolved toward a
  branch without the shipped history (the v1.1.0rc5 consolidation class) —
  left the check with no anchor and exited 0. The no-anchor state is now
  disambiguated by the release tags reachable from `HEAD`, enumerated by
  the #2861 walk (kept `--merged HEAD`, extracted into one shared helper —
  no third tag call): with no release tag reachable, the fresh /
  pre-first-release pass stands; with some, every one of them lacks its
  section and the check fails naming the newest tag and its restore
  source (`git show v<newest>:CHANGELOG.md`). New cases in
  `TestWipedTaggedSections` in `tests/test_changelog_tagged_sections.py`.
