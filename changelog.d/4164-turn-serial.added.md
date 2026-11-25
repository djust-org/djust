- **A reconnect that restores an older state than its page showed now says so
  (#4164).** Reply and mount frames of a view whose state is saved carry
  `state_serial`, the turn serial of the state they show; a reconnecting page
  sends it back. When storage is behind it, the reconnect first asks the tab's
  other connection, if it is still open, to save (through the channel layer,
  waiting up to one second), and if storage is still behind the page gets a
  transient `state_not_restored` notice ("Your most recent changes could not
  be restored."), shown in the save-status corner and dispatched as
  `djust:error`. A page whose saves fail three times in a row is told once
  that its recent changes are not saved yet.
