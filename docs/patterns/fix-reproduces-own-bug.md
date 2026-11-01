# Fix reproduces its own bug

**Rule** (formerly on the rule sheet): a fix to a shared cache, registry or dispatch must enumerate its callers' invariants before the first edit.
→ this page

**Status**: `wiki-only` (demoted at the v1.3.0-11 retro: no fired or missed instance in v1.3.0-10, v1.3.0-11 or v1.3.0-12) · **gate**: none · **candidate for mechanical gate**: no
**Origin**: v1.2.0-6 retro (#2838, four revisions)

## Instances

| PR | what drifted / what was missed | cure | rule in force? |
|---|---|---|---|
| #2838 | each review round's fix to the shared per-element rate-limit cache broke an adjacent keyboard/click path | enumerate every caller's invariant first | no (origin) |
| #2147, #2146, #2846 | see the v1.2.0-6 retro | | no (origin) |
| #3703 | the fix for review finding 2 (`initial` for an absent `BooleanField`) judged "supplied" by the field's own key, which broke multi-key widgets (review finding 3) | enumerate widget families first; test per family | no (demoted at v1.3.0-11/12; first instance since) |

## The rule as it stood in CLAUDE.md

- **A fix to a shared cache, registry, or dispatch must enumerate its callers' invariants before the first edit (#2838).** The keyboard path in `python/djust/static/djust/src/09-event-binding.js` shares a per-element rate-limit cache with the click/change/input paths. The four rounds went: round 1 overloaded `dj-key` as a required keyboard key, which silenced handlers on keyed list rows — a regression against `main`; round 2's first-match-wins dispatch dropped an ancestor handler that `main` did fire; round 3 dispatched every matching binding (correct) but rebuilt the rate-limit wrapper on every keystroke, defeating `dj-debounce` (3 keystrokes -> 3 server events) and leaking one `blur` listener per keystroke; round 4 keyed the cache by `(element, matched attribute)`, which satisfies the morph-stability and debounce invariants at once. Rounds 1-3 each fixed one observable symptom; only round 4 reasoned about the contract. **Rule:** when the change touches a cache, registry, or dispatch shared by more than one caller, write down the callers and the invariant each one needs, and check the fix against all of them before committing -- not one symptom at a time.
