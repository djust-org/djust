- **Live transports enforce or refuse access control that only the HTTP request runs.**
  The WebSocket and SSE mount, events, the HTTP POST event fallback, the API
  and `{% live_render %}` children never call a view's `setup()`,
  `dispatch()`, `get()` or `post()`, and never run the wrappers the URLconf
  puts around `as_view()`. djust replays its own declarations and Django's
  `LoginRequiredMixin`, `PermissionRequiredMixin` and `UserPassesTestMixin`
  there, but a custom `AccessMixin` subclass that checks the user in its own
  `dispatch()`, a `@method_decorator(...)` on `dispatch`, `get` or `post`, an
  overridden method that checks the user, or a URLconf wrapper such as
  `path("x/", login_required(MyView.as_view()))` did not run at all.
  `check_view_auth` now:
  - replays Django's `login_required`, `staff_member_required`,
    `user_passes_test(...)` and `permission_required(...)` applied with
    `@method_decorator`, wrapped around `as_view()` in the URLconf (read from
    the server's URLconf, not from the URL the client sends) or by an
    `as_view()` override, and other packages' decorators built on
    `user_passes_test`. The same users are admitted as on the HTTP request;
    an authenticated user who fails the test gets `PermissionDenied`, where
    Django's HTTP response would redirect to the login page. Async test
    functions are awaited (called directly on the event loop's thread, the
    check refuses instead of blocking). `login_not_required` is not access
    control. A decorator applied only to `post` is checked for every event on
    the page (the view's, its components', its embedded views' and uploads),
    not for the mount;
  - refuses a view whose custom `AccessMixin` subclass defines `dispatch()`,
    unless it declares `permission_required` or `check_permissions()`
    (`login_required = True` alone does not cover it);
  - refuses a view whose `setup()`/`dispatch()`/`get()`/`post()` override, a
    decorator on one (also under another decorator stacked above
    `@method_decorator`, or returned by a project decorator that delegates to
    it), or a URLconf wrapper checks the user (any attribute of it, such as
    `is_staff`, `groups` or a role) and denies by any means (raises, including
    `PermissionDenied` and `Http404`; calls `handle_no_permission()` or
    `redirect_to_login()`; returns a redirect, an error response or a page of
    its own such as `render(...)` or `HttpResponse(...)`; calls
    `get_object_or_404()`), unless a declared rule covers it (`login_required` covers a check that reads only
    `is_authenticated` / `is_anonymous`);
  - refuses a view with any other decorator or URLconf wrapper that is
    neither Django's nor djust's, unless it declares `permission_required` or
    `check_permissions()`.

  Permitted users of views using Django's `login_required`,
  `staff_member_required`, `user_passes_test` and `permission_required`
  decorators and wrappers, or its three auth mixins, are unaffected. Views
  whose access check lives in their own `setup()`, `dispatch()`, `get()`,
  `post()`, decorators or URLconf wrappers are refused for every user until
  the rule is declared. The HTTP GET page, which Django's dispatch already
  gated, is unchanged. System check `djust.S012` (an Error) names each
  refused view defined in the project; `djust.V013` (a warning) names
  overrides that only read the user, which are not refused, refused views
  that an installed package defines, and URLconf routes whose wrapper does not
  use `functools.wraps` (djust cannot read it). `djust.S012` no longer reports Django's
  own decorators on `dispatch()`, which are replayed. Only `djust` and
  `djust.*` modules are treated as framework code: apps and packages named
  `djust_*` are inspected, djust-auth's `LoginRequiredLiveViewMixin` is read
  as `login_required = True`, and djust-tenants' `TenantMixin` is not
  reported. `LoginRequiredLiveViewMixin` in `djust.auth` now also sets
  `login_required = True`.
  **Upgrade note:** a view refused this way must declare its rule. A public
  view that keeps a rate-limit, CSP or logging decorator or URLconf wrapper
  defines `check_permissions()` returning `True`. `login_required = False` is
  not a declaration.
