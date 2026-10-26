- **The JIT serializer no longer prints to stderr on every render.**
  `JITMixin._jit_serialize_queryset` wrote its "no paths", "Cache HIT",
  "Cache MISS" and "Query optimization" lines with `print(..., file=sys.stderr)`;
  the cache-hit line fired on every render of a view that assigns a QuerySet,
  bypassing logging's levels and handlers. They are `logger.debug` records of
  `djust.mixins.jit` now (`docs/JIT_SERIALIZATION_PATTERN.md` shows how to turn
  them on). 4 regression cases in `python/tests/test_jit_logs_not_stderr.py`.
