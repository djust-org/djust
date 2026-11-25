# Bug-report triage

When investigating an issue with a code-location citation:

1. **Trust the symptom, not the cited path.** The reporter's diagnostic data
   (error messages, patch counts, observable behavior) is the load-bearing
   evidence. The code location they cite is their hypothesis, which may be
   wrong — even when the cited code looks like a perfect match for the
   symptom (e.g., a dead-code fallback that produces the exact bytes the
   reporter saw).

2. **Trace from observable symptom to actual code path.** Write a reproducer
   test FIRST (Stage 4 of the bugfix pipeline already requires this).
   Confirm the reproducer fails. Then trace the data flow from where the
   symptom appears (output, error, missing patch) BACKWARDS through the
   framework until you find the offending code. This is symptom-up.

3. **Don't trust path-down hypotheses.** If you start at the reporter-cited
   location and try to verify the bug from there, you'll burn time when
   the location is wrong.

4. **Canonical case study**: PR #1206 (#1205 list[Model] VDOM fix). Reporter
   cited `python/djust/mixins/jit.py:_lazy_serialize_context` — a method
   with a `str(model)` fallback that exactly matched the reported symptom
   (`__str__` strings in serialized context). The method had **zero call
   sites** — dead code. The actual bug was upstream in
   `python/djust/mixins/rust_bridge.py:_sync_state_to_rust` change-detection,
   which at the time compared `list[Model]` via `Model.__eq__` (pk-only).
   Reproducer-first TDD surfaced the real path; trying to fix the
   reporter-cited code would have been a no-op.

   *Mechanism note (corrected 2026-09, #2738 PR).* The `Model.__eq__`
   sentence described the code as it stood at #1206 and had gone stale:
   since #2664 that comparison is `deep_fingerprint`
   (`python/djust/change_detection.py:90`), under which a `Model` is a leaf
   compared by `id()` (`:129`, `return (_TAG_ID, id(value))`). The eager
   `_normalize_db_values` pass (`python/djust/mixins/rust_bridge.py:83`,
   applied at `:693`) is still load-bearing for the same reason — it turns a
   Model into a dict so a container compare detects a field mutation behind
   a stable `id()`. The
   case study is unchanged and still canonical; only the named mechanism
   moved.

5. **`_framework_attrs` snapshot-order invariant (#1393).** Any new attr
   assigned in `LiveView.__init__` must be placed BEFORE or AFTER the
   `self._framework_attrs = frozenset(self.__dict__.keys())` line based on
   whether it is framework state (reset on reconnect) or user state
   (persisted, change-tracked). See the comment block at
   `python/djust/live_view.py:518` for the rule + examples.

6. **Multi-reopen issues require bit-exact runnable repro before "root
   cause confirmed" (#1389, PR #1086).** Theory-testing against synthetic
   test cases is INSUFFICIENT — it confirms only that THE FRAMEWORK
   behaves a certain way, not that THIS USER'S BUG matches the theory.
   PR #1086 had 3 "root cause" comments based on framework-side theory
   testing; all three were wrong. The actual fix landed only after
   gaining direct project access to reproduce against the user's exact
   environment.
