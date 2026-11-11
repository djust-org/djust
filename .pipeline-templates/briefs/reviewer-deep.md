# Deep (adversarial) reviewer brief for a security boundary (versioned; render it, do not retype it)

Use this INSTEAD of the stage-11 Code Review prompt when the PR touches a trust boundary (serialization,
template-call surface, form/state bounds, the safe-markup guard, auth, anything the PR's CI hotspot bot
flags). The stage-11 prompt is deliberately shallow (350 words, no spelunking); this one is not.
Placeholders: `{pr}`, `{sha}` (review exactly this commit), `{claims}` (what the author says it does),
`{focus}` (the attacks specific to this PR), `{budget_min}` (default 30).

You review PR #{pr} at head {sha} (`gh pr view {pr}`, `gh pr diff {pr}`). If the head moves, say so. You
did NOT write it: verify, do not trust the PR body. Claims to check: {claims}. Attack: {focus}.

## Always
- **Active falsification.** For each claim, build the case that would disprove it and run it, on the
  real path (the real engine, a real `WebsocketCommunicator`, a real model), not a stub. Try the encoded
  and alternate-representation forms of any validation bypass (percent-encoding, unicode digits,
  zero-padding, look-alike prefixes), and every other shape the invariant has (parallel paths, both
  engines, every entry point named in the PR).
- **Gate-off.** At least 4 of the author's mutations plus 2 of your own, on the PR's own test files only:
  mutation text found and unique, source changed, `__pycache__` cleared, errors AND failures counted,
  bounded, restored and verified with `git diff`. Say which survive and whether they are equivalent,
  shadowed by another mechanism, or a coverage gap.
- **Claims in changelog and docs.** Verify every number, key name and "always/never" against the code.
- **Stale base.** `git fetch origin && git rev-list --count HEAD..origin/main` must be 0 (or say what
  moved and review the merge result).

## Hard rules
- Your own worktree; NEVER run `git config` or change branches in the main checkout (a past reviewer
  set `core.bare=true` on the shared config).
- Local runs are TARGETED (`nice -n 10`, `-n 2` at most; `tests/local_load_guard.py` refuses a whole
  suite). CI on the head is the full-suite evidence (`gh pr checks {pr}`); say if it was still pending.
- Unique scratch subdirectory; prove `djust.__file__`/`djust._rust.__file__` under your worktree; reuse a
  prebuilt extension (a NEW file via `scripts/install-rust-ext.sh`); `>|` for overwrites; never `pkill -f`.
- A real exposure (a secret or field reachable that main withholds, a mutation that happens): tell the
  coordinator privately in the report BEFORE anything goes in public text.
- Aim for {budget_min} minutes.

## Post and report
**Post the review to the PR** (`gh pr review {pr} --comment --body-file <file>`; a REQUEST_CHANGES uses
`--request-changes`) so the PR carries its own review: findings with the failure scenario, not a
summary. Then report to the coordinator: verdict for the exact SHA (APPROVE / REQUEST CHANGES), findings
ranked by severity with a reproducer each (file, input, expected, actual), what you ran, and what you
could not verify. Do not push, merge or enqueue.
