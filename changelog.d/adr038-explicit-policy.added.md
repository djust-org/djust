- **`exposure_policy = "explicit"` is available (ADR-038).** A view that opts
  in exports only what it declares:
  - template context comes from `get_context_data()` and registered framework
    providers;
  - server persistence comes from `state(..., persist="server")`;
  - raw browser data comes from `state(..., client=True)`;
  - the back-navigation snapshot comes from `state(..., persist="client",
    client=True)`;
  - debug tooling gets a redacted projection.

  Ordinary attributes stay in server memory. Every turn, including background
  results, ticks, pushes, NOTIFY and `url_change`, is re-authorized against the
  current session, and a failed state save is reported instead of hidden.
  `legacy` remains the default, and nothing changes for views that don't opt
  in. Actors (`use_actors = True`) and `lazy=True` children are not supported
  under the explicit policy. See the "Explicit exposure" guide.
