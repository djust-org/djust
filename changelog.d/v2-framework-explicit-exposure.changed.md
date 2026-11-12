- **The views and mixins djust ships work under `exposure_policy = "explicit"` (ADR-038
  Step R).** The default is unchanged (`"legacy"`); this is the groundwork for changing it.

  Mixins. Under explicit exposure a template gets only what it is given, so a mixin's public
  attributes rendered empty. Each of these now supplies its own names, render-only (nothing is
  stored or sent to the browser as data), and a legacy view is untouched:
  `PresenceMixin` (`online_count`), `TutorialMixin` (`tutorial_running`,
  `tutorial_current_step`, `tutorial_total_steps`, `default_highlight_class`),
  `DataTableMixin` (its `table_*` configuration and state; never `table_model`,
  `table_queryset`, a callable, or a class-level value that is not plain data),
  `FormSetHelpersMixin` (the formsets named in `formset_classes`) and `ThemeMixin`
  (`theme_preset`, `theme_mode`, `theme_resolved_mode`, with or without the `theme_context`
  processor). The first four register their names as context providers, so on an explicit
  view an application write of one of them in `get_context_data()` is a collision error, as
  it already is for `FormMixin`'s keys; declaring the name (`online_count = state(0)`) is
  fine. An explicit view that lists `DataTableMixin` or `FormSetHelpersMixin` after
  `LiveView` is refused when the class is defined, because the mixin would never be asked
  for its names.

  Views. These are now explicit: the admin index, delete, logout and bulk-action progress
  views, the two auth admin pages, the component gallery (index and nine category pages) and
  the component catalogue (index, category and detail pages). The social accounts page's
  search, sort, filter and page are declared `state(..., persist="server")`, so the page
  keeps them from one request to the next when it runs without a WebSocket, as the legacy
  session save did. The public gallery and catalogue pages declare no persisted field (it
  would make them need a server-side session and authentication middleware), so without a
  WebSocket their search and collapsed groups are not carried to the next request.

  Pinned to `exposure_policy = "legacy"`, each for something explicit exposure does not offer
  yet: the admin change list and change form (they embed the ModelAdmin's widget views, and
  an explicit page cannot hold a legacy child), the interactive dropdown example and its
  catalogue page (an interactive component is reachable over the HTTP POST fallback only on
  a legacy view), `TtydTerminalView` (a base class: a subclass's own attributes render
  through the attribute walk), `PersistentLiveView` and the function-based `@live_view`
  decorator.

  Scaffolding. `djust new` (every flag combination and `--from-schema`) and
  `djust_gen_live` generate views with `exposure_policy = "explicit"` whose
  `get_context_data()` returns a literal dict that spreads `super()`'s context.

  Behind a session-only WebSocket stack (`DjustMiddlewareStack`), a view with explicit
  exposure now mounts. Such a request has no `user`, and an explicit mount binds its state to
  one, so it used to be refused. The mount now reads the user from the session, as each
  explicit event already does, after the usual access check has run on the request as the
  stack built it: a `login_required` or `permission_required` view is still refused behind
  that stack, and a legacy view still gets no `request.user` there.

  The set of shipped LiveView classes and each one's policy is pinned in
  `python/djust/tests/test_exposure_framework_views.py`; the mixins are covered in
  `python/djust/tests/test_exposure_framework_mixins.py`.
