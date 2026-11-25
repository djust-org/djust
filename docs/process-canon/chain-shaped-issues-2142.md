# Chain-shaped issues (#2142)

Some issues are one bug. Others are a **class**, and you only learn that from
the review of the fix: each PR's adversarial review surfaces the next instance,
in a variant the previous fix did not reach. Three of these ran back to back:

- scoped-attr: #2107 → #2109 → #2111 (sweep and scan still disagreed)
- streams: #2113/#2115 → #2112/#2117 → #2116/#2118 → #2119/#2122
- #2129 took five rounds; #2139 took three, and **two of its three defects were
  introduced by the previous round's fixes**

Each link shipped separately because each was a real, separately-reachable bug,
and batching them would have merged four incomplete diagnoses. That is the right
call. The problem is only that the drain plan presents such an issue as one
item, so a chain reads as a slipped estimate rather than as the shape of the
work.

**The convention:**

1. **Mark an issue *chain-suspected* at Stage 4** when its root cause is a class
   rather than an instance. The reliable signals are the recurring ones:
   parallel-path drift (#1646), a wire-contract mismatch, a shared-key collapse,
   or a fix whose shape is "the same edit at N sites". Say up front that
   follow-ups are expected rather than exceptional.
2. **File link N+1 immediately** when a review surfaces it, with a back-reference
   to the issue it came from, so the chain is visible in the issue graph rather
   than only in the PR trail.
3. **Count a chain as one finding and N PRs** in the milestone retro. Five PRs
   against one root cause is one diagnosis refined five times, not five
   failures, and the stats should not read as the latter.

**Do not estimate a chain up front.** By construction you cannot: link N+1 is
discovered by reviewing link N. An estimate that pretends otherwise makes
honest work look late, which is the pressure that produces the batched
half-diagnosis this convention exists to avoid.

A corollary worth stating, because it recurred verbatim in #2129, #2137 and
#2139: **when your own fix is the thing the next review breaks, that is the
chain, not a mistake to hide.** Each round of #2139 fixed the cited instance
and re-created the class one step over — a `sed` that fixed ids containing
` - ` broke every id whose *message* contained a hyphen. Write the round down
in the commit and the CHANGELOG, because the pattern is the finding.
