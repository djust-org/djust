# Implementer brief (versioned; render it, do not retype it)

Placeholders: `{issue}` issue number, `{branch}`, `{budget_min}` (default 30), `{extra}` issue-specific
instructions (the only free-text part). Record this file's path and git hash in the PR body.

Resolve GitHub issue #{issue} in djust-org/djust (`gh issue view {issue}` and its comments).
Work from the SYMPTOM up (CLAUDE.md "Bug-report triage"): write the failing reproducer FIRST, confirm it
fails on current main, then trace backwards. Decide each item: fix, or decline with a measured reason.
Enumerate the parallel paths that implement the same invariant (grep the SINK, not the callers you
expect) and decide each explicitly. {extra}

## Standing rules
1. **Local tests are targeted.** The tests you add or edit plus their importers (`grep -rl`),
   `nice -n 10`, xdist `-n 2` at most. NO whole-suite or whole-root run: `tests/local_load_guard.py`
   refuses one outside CI, and CI on your PR head is the full-suite evidence (`gh pr checks <N>`).
2. **Time budget: about {budget_min} minutes.** A part that has not converged after two attempts is left
   out, named in the PR body, and filed as an issue with a measured reason.
3. **Fold in vs file separately.** Fold in when it is a defect in code you add or a bypass of your own new
   rule (always), the same invariant on a parallel path, under ~15 minutes or ~30% added diff and covered
   by the same tests, or a correction to your own claims/docs. File separately when it is a different
   invariant or subsystem, needs a product/API decision, touches shared Rust or a crate you do not
   already change, or is pre-existing and outside the diff. List folded items under a **Folded in**
   heading in the PR body so the reviewer attacks them. Never leave a finding unmentioned.
4. **Gate-off every behavioural change.** Apply a mutation, assert the mutation text was found (and
   unique) and the source changed, clear `__pycache__`, count errors AND failures, bound the runtime,
   restore, verify with `git diff`. A mutation that does not apply, or breaks the build, reports a false
   zero. A survivor is a question: equivalent mutation, two mechanisms shadowing each other, or missing
   coverage. Delete the redundant mechanism rather than testing around it.
5. **Hygiene.** Unique scratch subdirectory; prove `djust.__file__` (and `djust._rust.__file__`) resolve
   under your worktree; reuse a prebuilt extension (install it as a NEW file via
   `scripts/install-rust-ext.sh <wheel>`, never `cp` over a loaded `.so`) and build only if you change
   Rust; zsh has noclobber, overwrite with `>|`; never `pkill -f`, never a bare `git stash`; never touch
   another session's processes.
6. **Commit and push.** `--no-verify` with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`,
   conventional message; a `fix:`/`feat:`/`security:` PR needs `changelog.d/{issue}.<section>.md` shaped
   `- **bold title (#{issue}).** body` (run `tests/test_changelog_fragments.py`); merge `origin/main` in
   (never rebase or force-push); push with `SKIP=pytest,npm-test,cargo-test` (SSH refused: HTTPS with
   `git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push https://github.com/djust-org/djust.git HEAD:refs/heads/<branch>`).
   Open the PR (on a GraphQL 502 retry, or `gh api -X POST repos/djust-org/djust/pulls --input <json>`).
7. **The PR body** ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`, uses
   `Closes #{issue}` only if every item is done (else `Refs`), has a **Folded in** section, and says what
   you did NOT verify. Do NOT merge, enable auto-merge or enqueue: the coordinator does that after review.

## Report
PR URL; head SHA from `git rev-parse HEAD` after the last push, verified with `git ls-remote`; each item
done/declined with its reason; gate-off results; what you did not verify.
