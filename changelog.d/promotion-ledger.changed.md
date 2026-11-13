- **Tests that fail late now run early (the promotion ledger).** When the nightly
  `main-health` suite fails, `scripts/promotion_ledger.py promote` adds each
  failing test that PR CI does not run to `tests/promotion-ledger.json`, and PR
  and merge-queue CI then run it even though it is slow; it leaves after 30
  consecutive green nights unless pinned, within a 180 s budget. The nightly
  only posts the ledger patch on an issue; it never pushes. Path rules run the
  tree-scanning pins (`MERGE_GATES`) before merge whenever a change adds,
  deletes or renames a test module, the check-ID pins when a system check
  changes, and the client-size pins when client JS changes. `make test-early`
  runs the whole early tier locally. See `docs/testing-strategy.md`, "The early
  tier".
