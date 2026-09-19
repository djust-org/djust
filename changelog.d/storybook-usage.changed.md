- Storybook usage snippets now show the event side of a component: a descriptor (`Accordion`, `Tabs`, `Modal`, …) is shown in its class-level form, whose `Meta.event` the descriptor answers itself; any other event the markup emits (`dj-click="set_rating"`) gets an `@event_handler` stub that rebuilds the component with the kwarg the event drives. The template line is `{{ component }}` — `render()` marks the HTML safe, so `|safe` was never needed. `code_snippet` and `code_block` highlight their code on the server when Pygments is importable (an optional dependency; plain escaped text otherwise), with token colours drawn from the theme's properties (`--dj-code-*` to override).
- **Storybook usage follows ADR-033.** The generated view holds the component
  in `mount()` and its handler writes to it (`self.component.value = value`),
  with no `int(value)` and no `get_context_data` rebuild; the storybook's own
  demo handlers do the same and the wire-value coercion they carried is gone.
  The parameters table marks `event` as a rename of the verb — an instance's
  identity is `name`. `docs/website/core-concepts/components.md` and
  `guides/components.md` carry the state-written-through form, the `name`
  convention and the "which shape when" rule (D7).
