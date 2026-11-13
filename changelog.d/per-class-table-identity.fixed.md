- **Two view classes that a metaclass calls equal no longer share handler metadata.**
  The per-class handler plan table looked a class up by equality, so under a
  metaclass defining `__eq__` / `__hash__` (by name, for instance) one class's
  event handlers were listed for another class of the same name, including a
  hot-replaced one. The table is keyed by the class object's identity and every
  hit is checked against it. The exposure contract's per-class table, new in
  this release, is built the same way. `python/djust/tests/test_per_turn_invalidation.py`.
