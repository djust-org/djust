- **The documentation's view examples work under explicit exposure, the default.** Examples
  that set `self.x` in `mount()` and read `{{ x }}` in a template now supply the name in
  `get_context_data()`; the 1.x idiom `self.items = self._items` inside `get_context_data()`
  is replaced by `context["items"] = self._items`. The prose that said public attributes reach
  the template by themselves is corrected in the core-concepts pages, the API reference, the
  best-practices and security guides, `docs/ai/`, `docs/llms-full.txt`, the JIT serialization
  guide and `CLAUDE.md`. The HTTP-only guide's counter declares `state(0, persist="server")`,
  since on that transport each POST runs `mount()` again. `scripts/check-doc-exposure.py`
  finds a documented view that still relies on the attribute walk, and
  `tests/unit/test_doc_exposure_check.py` and the executed-snippet test fail on one (#4048).
