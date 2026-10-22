- **The Rust state sync walks each structured context value once instead of
  three times.** The change-detection fingerprint, the `SafeString` path scan
  and the conversion for the Rust renderer now come from one traversal
  (`python/djust/state_pass.py`); a value with any shape the walk does not handle (a
  model, a form, a component, any subclass, a cycle) still takes the original
  passes, so the output is unchanged. On a 300-row table page the sync's
  Python time per mount + event drops from ~7.3 ms to ~4.7 ms. Equivalence
  cases in `python/djust/tests/test_state_single_pass.py`.
