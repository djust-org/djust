- **An explicit view's turn encodes its saved state once, not three times (#3997).**
  The automatic save JSON-encoded the whole state for its text, again per
  attribute for the digests, and again as the envelope only to measure its
  length; each value is now encoded once and the rest is built from those
  encodings (the same characters). The state encoder handles a container's
  scalar members in one loop instead of a call each, and the save's name rule
  is built once per save and asks only about the view's own attributes. On
  `make benchmark-turn` (new: complete event turns, explicit against legacy,
  release build) the explicit-minus-legacy cost of a turn that changes one cell
  of a 500-row table went from 7.1 ms to 5.6 ms (1.70x to 1.54x legacy); a
  counter and a 30-value dashboard moved by 0.1-0.2 ms. What remains is mostly
  the session read, decode and write each saving turn makes; reducing it changes
  where saved state is kept, which is left for a decision. `python/djust/tests/test_explicit_turn_cost_3997.py`.
