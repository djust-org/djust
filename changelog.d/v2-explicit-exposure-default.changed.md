- **`LiveView.exposure_policy` defaults to `"explicit"` (ADR-038 Step R).** It was `"legacy"`.
  A view that states no policy now gets only what it declares: its template context is what
  `get_context_data()` returns (plus the framework's own providers and Django's context
  processors), server-side persistence is its `state(..., persist="server")` fields, and the
  browser receives as data only its `state(..., client=True)` fields. Under `"legacy"` every
  public attribute set on `self` was template context, and much of it also reached session
  state and the client state mirror.

  What breaks. A view written for 1.x whose template reads attributes that nothing supplies
  renders those names empty, with no error. Its state is no longer restored on the HTTP POST
  fallback or (with `enable_state_snapshot`) on a reconnect, a component assigned in `mount()`
  is an error, an interactive component's events need a WebSocket or SSE connection, and
  time-travel restore and replay are refused. In production an explicit view's failures are
  value-free in logs and error frames; under `DEBUG` they read as before.

  To keep the old behaviour on a view, state it:

  ```python
  class OldView(LiveView):
      exposure_policy = "legacy"
  ```

  A base class your views inherit from carries it for all of them. There is no setting that
  changes the default. To migrate a view, see [Migrating to djust 2.0](docs/website/guides/migrating-to-2.0.md) and
  [explicit exposure](docs/website/state/explicit-exposure.md#migrating-a-view);
  `manage.py djust_exposure_inventory --view myapp.views.MyView` lists what the view exported.

  The same default applies wherever djust reads a policy from something that has none: an
  object that is not a view no longer counts as legacy when djust decides whether it may use
  a legacy export or restore path, and `djust_exposure_inventory` reports such a class as
  explicit. Error diagnostics are unchanged for those owners: a failure handled while no view
  exists (a view whose constructor or mount failed), or on behalf of a component or a formset
  class, is logged as it was, and a view that states `"legacy"` keeps its detailed production
  logs in those cases.

  The README and the guides' executed examples (interactive components, navigation
  `params_form`, the shared room clock, time-travel debugging) supply their template context
  in `get_context_data()`. The component catalogue's usage snippets do too: a template-tag
  example supplies each name its tag reads, and a Python-component example holds its
  component in `self._component` and hands it over as `component` (a component in a public
  attribute is an error on an explicit view). The MCP `scaffold_view` tool supplies every
  name its view sets. `djust new` and `djust_gen_live` already generated explicit views.
