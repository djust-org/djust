- **The AI schema and the MCP server no longer teach the 1.x attribute walk.**
  `djust.schema` (`CONVENTIONS["public_private"]`, the `get_context_data`, `state()` and
  `@computed` descriptions, the lifecycle and JIT examples and pitfall 7) and the
  `public_queryset` fix text of the MCP `detect_common_issues` tool recommended
  `self.items = self._items` followed by `super().get_context_data()`, which renders
  `{{ items }}` empty under explicit exposure. They now supply the name from
  `get_context_data()` (#4048).
