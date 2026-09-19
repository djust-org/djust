- **The storybook's interactive previews stopped responding after ADR-031
  merged.** `tabs`, `dropdown`, `modal` and the other five `_INTERACTIVE`
  descriptors read their live state from `getattr(view, name)`, which ADR-031
  changed: a class-level descriptor now resolves to a `BoundComponent` whose
  per-view state is `.state`, where it used to resolve to the `State` itself —
  a `dict`. The merge was gated on `isinstance(descriptor, dict)`, so it
  silently contributed nothing and every preview lost the state it renders
  from. The components gallery got the unwrap when ADR-031 landed; the
  theming storybook's own copy did not — one pattern, two galleries, one of
  them fixed, which is the parallel-path shape the rebase exposed.
