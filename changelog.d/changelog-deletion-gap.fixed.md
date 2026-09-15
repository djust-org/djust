- **Deleting an already-shipped `CHANGELOG.md` section no longer passes the
  shipped-section pin silently (#2862).** The #2028 pin iterated only the
  sections present in the working tree, so a shipped section *deleted* from
  the tree was never compared — removing the `## [1.2.0rc6]` section exited 0
  with `OK: 133 shipped CHANGELOG section(s) match the newest release tag`.
  The pin now iterates the union of the working tree's sections at or below
  the anchor and the anchor tag's frozen snapshot, so *deletion* and
  *rewrite* are two symptoms of one comparison; a deleted section fails by
  name with a restore instruction, a distinct message from the rewrite diff
  because the operator's next action differs. The multi-branch scoping from
  #2861 is preserved — the new demand reads the anchor's snapshot, never the
  tag list, so a maintenance branch is not failed for main's sections.
  Covered by synthetic tests in `TestDeletedShippedSection` and a real-tree
  canary in `tests/test_changelog_tagged_sections.py`.
