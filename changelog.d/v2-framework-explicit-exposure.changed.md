- **The views and mixins djust ships work under `exposure_policy = "explicit"` (ADR-038
  Step R).** The default is unchanged (`"legacy"`); this is the groundwork for changing it.

  Mixins. Under explicit exposure a template gets only what it is given, so a mixin's public
  attributes rendered empty. Each of these now supplies its own names, render-only (nothing is
  stored or sent to the browser as data), and a legacy view is untouched:
  `PresenceMixin` (`online_count`), `TutorialMixin` (`tutorial_running`,
  `tutorial_current_step`, `tutorial_total_steps`, `default_highlight_class`),
  `DataTableMixin` (its `table_*` configuration and state; never `table_model` or
  `table_queryset`), `FormSetHelpersMixin` (the formsets named in `formset_classes`) and
  `ThemeMixin` (`theme_preset`, `theme_mode`, `theme_resolved_mode`, with or without the
  `theme_context` processor). The first four register their names as context providers, so
  on an explicit view an application write of one of them in `get_context_data()` is a
  collision error, as it already is for `FormMixin`'s keys.

  Views. These are now explicit: the admin index, delete, logout and bulk-action progress
  views, the two auth admin pages, the component gallery (index and nine category pages), the
  component catalogue (index, category and detail pages) and `TtydTerminalView`. A subclass
  inherits the policy: a `TtydTerminalView` subclass that renders its own attributes
  (`session_log`) now lists them in `get_context_data()`, or sets
  `exposure_policy = "legacy"`. An explicit view binds a socket mount to the page's session
  and principal, so these pages need session and authentication middleware on the WebSocket
  (`AuthMiddlewareStack`), which `djust new` already generates.

  Still legacy, each for something explicit exposure does not offer yet: the admin change
  list and change form (they embed the ModelAdmin's widget views, and an explicit page
  cannot hold a legacy child), the interactive dropdown example and its catalogue page (an
  interactive component is reachable over the HTTP POST fallback only on a legacy view),
  `PersistentLiveView` and the function-based `@live_view` decorator.

  Scaffolding. `djust new` (every flag combination and `--from-schema`) and
  `djust_gen_live` generate views with `exposure_policy = "explicit"` whose
  `get_context_data()` extends `super()`'s context.

  The set of shipped LiveView classes and each one's policy is pinned in
  `python/djust/tests/test_exposure_framework_views.py`; the mixins are covered in
  `python/djust/tests/test_exposure_framework_mixins.py`.
