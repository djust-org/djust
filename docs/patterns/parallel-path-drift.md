# `parallel-path-drift`

**Rule** (the line the rule sheet carries): A per-path invariant implemented in several
places must be fixed on every path, or expressed once.
→ this page

**Status**: `skill` · **gate**: partial — `tests/js/tick_buffer_version_desync_2829.test.js`
pins the three transport sources by grep; `scripts/check-bundle-init-order.mjs` is a
structural gate for one specific twin · **candidate for mechanical gate**: yes
**Origin**: #1646 — this repo's self-named most-recurring failure mode.

## Instances

| PR | what drifted / what was missed | cure | rule in force? |
|---|---|---|---|
| #2829 | the VDOM version had four sources of truth (buffer, WS, SSE, HTTP fallback) and a buffered tick desynced it | one decision, plus a test asserting exactly one call at each of the three transport sources and one definition in the response handler | yes, fired |
| #2831 | keyboard dispatch existed twice — the document path and the scoped `dj-window-keydown` registry — and the dotted-attribute fix was re-derived on one instead of lifted from the other | lifted the reference implementation (`_scopedListenerIsStale`-style prefix scan, `(element, attrName)` registry identity) | yes, fired |
| #2832 | the registry path evicted stale listeners on three conditions (#2108); the `dj-shortcut`/`dj-click-away` sweep checked only one | one shared predicate, `isScopedListenerStale`, with exactly two call sites | yes, fired |
| #2834 | a `_skip_render` / `_force_full_html` precedence decided in four render paths; three agreed and the fourth did not | one `_resolve_skip_render` helper — but see #2846 | yes, fired |
| #2846 | the fix covered the three paths the issue named and left a **fourth** (`_dispatch_single_event`) resolving the collision the old way | follow-up filed (#2847) rather than expanding the fix | yes, missed (the rule was in force; a fourth path was not enumerated) |
| #2825 | V008's escape hatch walked `tree.body` only, so class methods were never collected | collect methods; the check's documented remedy now works | yes, missed |
| #2833 | T018 skipped `{% extends %}` templates in the system-check path while `djust_typecheck` covered them — the skip was invisible | emit an Info message so a "passed" run is falsifiable | yes, fired |
| #2129, #2135, #1468, #1200 | the "tautology" family: tests that pass while the bug executes | gate-off discipline (revert the fix; confirm red) | yes, fired |

## Detection

- **Grep for the twins.** Any per-path invariant should appear in exactly one of: a single
  helper, or a test that asserts the count of implementations (the #2829 pin asserts one
  call per transport source — that is the mechanical shape).
- **Ask which path is correct before making them agree.** #2834's four paths disagreed about
  precedence; "make the odd one match" would have picked the wrong resolution. The tick path
  was right, and the other three were wrong.
- **Enumerate the paths from the code, not from the issue.** #2846 followed the issue's list
  of three and missed a fourth. An issue's enumeration is evidence, not the complete set.

## Rejected shapes

- **"Fix the path the issue names."** #2846 is the worked example: correct as scoped, and it
  left the same class latent in the same file. The scope rule (#1079) is right for the PR, so
  the remainder needs a follow-up issue rather than silence.
- **"Assert the paths agree."** #1859/#1860's anti-drift pins were decorative — they compared
  a converged chokepoint to itself and a set that no production path consulted. A parity test
  must be shown to go red when the paths genuinely differ (#1468).
