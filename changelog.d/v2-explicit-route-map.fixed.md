- **A public view with explicit exposure was missing from an anonymous visitor's route map.**
  `as_view()` on an explicit view returns djust's error-handling wrapper around Django's
  callable. The route map that `{% djust_client_config %}` emits treats a wrapped callback as
  login-gated (it cannot tell what a decorator does), so every public explicit route was left
  out of the map `dj-navigate` reads. The wrapper is now recognised by identity and looked
  through; a decorator applied around it still gates the route.
